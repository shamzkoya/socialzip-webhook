#!/usr/bin/env python3
"""
Fill Missing Images
===================
For every DRAFT product without an image:
  1. Try to match an existing image from the WordPress media gallery
     (matches by SKU or product name words against gallery filenames/slugs)
  2. If no gallery match → search DuckDuckGo for a product image URL
  3. Update the product via WooCommerce API with the found image

Run:
  python3 fill_missing_images.py

Options:
  --dry-run          Preview matches without updating WooCommerce
  --no-web-search    Only use gallery images, skip DuckDuckGo
  --workers N        Parallel workers for web search (default 6)
  --resume           Skip products already processed (uses progress file)
  --status publish   Also process published products (default: draft only)
"""

import os, re, sys, csv, json, time, random, argparse, threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

BASE = Path(__file__).parent
SC   = BASE / "stock_control"

load_dotenv(BASE / ".env")
WC_URL    = os.environ["WC_URL"].rstrip("/")
WC_KEY    = os.environ["WC_KEY"]
WC_SECRET = os.environ["WC_SECRET"]
AUTH      = HTTPBasicAuth(WC_KEY, WC_SECRET)
WC_API    = f"{WC_URL}/wp-json/wc/v3"
WP_API    = f"{WC_URL}/wp-json/wp/v2"

PROGRESS_FILE = SC / "image_fill_progress.json"
REPORT_FILE   = SC / "image_fill_report.csv"

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36"}
IMG_TIMEOUT = 6

TRUSTED_DOMAINS = [
    "takealot.com", "makro.co.za", "builders.co.za", "game.co.za",
    "incredible.co.za", "hirschs.co.za", "makokoya.co.za",
    "amazon.com", "amazon.co.uk", "walmart.com", "homedepot.com",
    "superbalist.com", "leroy.co.za", "bunnings.com.au",
    "trademe.co.nz", "overstock.com",
]

try:
    from duckduckgo_search import DDGS
    DDG_OK = True
except ImportError:
    DDG_OK = False
    print("WARN: duckduckgo_search not installed — web image search disabled")

_ddg_lock = threading.Lock()


# ── Helpers ────────────────────────────────────────────────────────────────────

def safe_json(r):
    """Parse response JSON, fixing invalid backslash escapes."""
    text = r.content.decode("utf-8", errors="replace")
    # Fix bare backslashes that aren't valid JSON escapes
    text = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', text)
    return json.loads(text)


def fetch_all_media():
    """Fetch all images from WP media library. Returns list of dicts."""
    print("Loading WordPress media gallery...")
    media, page = [], 1
    while True:
        r = requests.get(
            f"{WP_API}/media",
            auth=AUTH,
            params={"per_page": 100, "page": page, "media_type": "image"},
            timeout=60,
        )
        if not r.ok:
            print(f"  Media API error {r.status_code}: {r.text[:200]}")
            break
        try:
            data = safe_json(r)
        except Exception as e:
            print(f"  JSON error on page {page}: {e}")
            page += 1
            continue
        if not data:
            break
        media.extend(data)
        total_pages = int(r.headers.get("X-WP-TotalPages", 1))
        print(f"  Gallery: page {page}/{total_pages}  ({len(media)} images)\r", end="", flush=True)
        if page >= total_pages:
            break
        page += 1
        time.sleep(0.3)
    print(f"\n  Total gallery images: {len(media)}")
    return media


def build_gallery_index(media_items):
    """Build searchable index: normalised_key → (media_id, source_url)."""
    index = {}
    for m in media_items:
        media_id = m.get("id")
        url  = m.get("source_url", "") or (m.get("guid") or {}).get("rendered", "")
        if not url:
            continue
        # Ensure absolute URL (some WP installs return relative paths)
        if url.startswith("/"):
            url = WC_URL + url
        title = (m.get("title") or {}).get("rendered", "")
        slug  = m.get("slug", "")
        fname = url.rsplit("/", 1)[-1].rsplit(".", 1)[0]   # filename without ext/path

        for key in [title, slug, fname]:
            key = normalise(key)
            if key and len(key) > 3:
                index.setdefault(key, (media_id, url))   # first-wins per key

    return index


def normalise(s):
    """Lowercase, strip punctuation/numbers-only tokens, collapse spaces."""
    s = s.lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def score_match(product_words, gallery_key):
    """Return overlap score: how many product words appear in the gallery key."""
    g_words = set(gallery_key.split())
    overlap = sum(1 for w in product_words if w in g_words and len(w) > 2)
    return overlap


def find_gallery_image(product, gallery_index):
    """Try to find best matching gallery image for a product.
    Returns (media_id, url, method) or (None, None, None)."""
    name  = product.get("name", "")
    sku   = product.get("sku", "")

    # Try exact SKU match first
    sku_norm = normalise(sku)
    if sku_norm and sku_norm in gallery_index:
        mid, url = gallery_index[sku_norm]
        return mid, url, "sku-exact"

    # Try SKU contained in gallery key
    if sku_norm and len(sku_norm) > 3:
        for key, (mid, url) in gallery_index.items():
            if sku_norm in key:
                return mid, url, "sku-partial"

    # Word overlap matching on name
    name_norm  = normalise(name)
    name_words = [w for w in name_norm.split() if len(w) > 2]
    if not name_words:
        return None, None, None

    # Need at least 3 significant words to match (avoid false positives)
    min_words = min(3, len(name_words))
    best_mid, best_url, best_score = None, None, 0

    for key, (mid, url) in gallery_index.items():
        s = score_match(name_words, key)
        if s > best_score:
            best_score = s
            best_mid   = mid
            best_url   = url

    if best_score >= min_words:
        return best_mid, best_url, f"name-match({best_score})"

    return None, None, None


def search_image_online(name, brand="", sku=""):
    """Search DuckDuckGo for the best product image URL."""
    if not DDG_OK:
        return ""

    queries = []
    clean = re.sub(r'["\']', '', name)
    if brand and brand.lower() not in ("", "unknown"):
        queries.append(f"{brand} {clean} product")
    queries.append(f"{clean} product image")
    queries.append(f"{clean} buy online")

    for query in queries:
        for attempt in range(2):
            try:
                with _ddg_lock:
                    time.sleep(0.5 + random.random() * 0.5)
                    ddgs   = DDGS(proxy=None)
                    results = list(ddgs.images(query, max_results=10, safesearch="off"))

                # Prefer trusted domains, then any valid image URL
                trusted = [r for r in results if any(d in r.get("image","") for d in TRUSTED_DOMAINS)]
                candidates = trusted + [r for r in results if r not in trusted]

                for r in candidates:
                    url = r.get("image", "")
                    if not url or not url.startswith("http"):
                        continue
                    if not url.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                        continue
                    try:
                        resp = requests.head(url, timeout=IMG_TIMEOUT, headers=HEADERS, allow_redirects=True)
                        ct = resp.headers.get("Content-Type", "")
                        if resp.status_code == 200 and "image" in ct:
                            return url
                    except Exception:
                        continue
                break
            except Exception:
                if attempt == 0:
                    time.sleep(2)
    return ""


def update_product_image(product_id, image_url, media_id=None, dry_run=False):
    """Set image on a WooCommerce product.
    Uses media_id (no sideload) when available, falls back to src URL."""
    if dry_run:
        return True
    if media_id:
        payload = {"images": [{"id": media_id}]}
    else:
        payload = {"images": [{"src": image_url}]}
    r = requests.put(f"{WC_API}/products/{product_id}", auth=AUTH, json=payload, timeout=30)
    return r.status_code in (200, 201)


def fetch_products_missing_images(status="draft"):
    """Fetch products with no images."""
    print(f"Fetching {status} products missing images...")
    missing, page = [], 1
    while True:
        params = {"status": status, "per_page": 100, "page": page}
        r = requests.get(f"{WC_API}/products", auth=AUTH, params=params, timeout=60)
        r.raise_for_status()
        data = r.json()
        if not data:
            break
        for p in data:
            if not (p.get("images") or []):
                missing.append(p)
        total_pages = int(r.headers.get("X-WP-TotalPages", 1))
        print(f"  page {page}/{total_pages}\r", end="", flush=True)
        if page >= total_pages:
            break
        page += 1
        time.sleep(0.4)
    print(f"\n  Products missing images ({status}): {len(missing)}")
    return missing


def load_progress():
    if PROGRESS_FILE.exists():
        return json.loads(PROGRESS_FILE.read_text())
    return {"done": [], "failed": []}


def save_progress(prog):
    PROGRESS_FILE.write_text(json.dumps(prog, indent=2))


def save_report(results):
    rows = []
    for r in results:
        rows.append({
            "id":      r["id"],
            "sku":     r.get("sku", ""),
            "name":    r.get("name", ""),
            "source":  r.get("source", ""),
            "image":   r.get("image", ""),
            "status":  r.get("result", ""),
        })
    with open(REPORT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["id","sku","name","source","image","status"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"  Report → {REPORT_FILE}")


# ── Main ───────────────────────────────────────────────────────────────────────

def process_product(p, gallery_index, no_web_search, dry_run):
    pid   = p["id"]
    name  = p.get("name", "")
    sku   = p.get("sku", "")
    brand = p["brands"][0]["name"] if p.get("brands") else ""

    result = {"id": pid, "sku": sku, "name": name, "source": "", "image": "", "result": "no_image_found"}

    # Step 1: gallery match — use media ID to avoid sideload failures
    media_id, img_url, method = find_gallery_image(p, gallery_index)

    if img_url:
        result["source"] = f"gallery:{method}"
        result["image"]  = img_url
    elif not no_web_search:
        # Step 2: web search
        media_id = None
        img_url = search_image_online(name, brand, sku)
        if img_url:
            result["source"] = "web-search"
            result["image"]  = img_url

    if img_url:
        ok = update_product_image(pid, img_url, media_id=media_id, dry_run=dry_run)
        result["result"] = "updated" if ok else "api_error"

    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run",       action="store_true")
    parser.add_argument("--no-web-search", action="store_true")
    parser.add_argument("--workers",       type=int, default=6)
    parser.add_argument("--resume",        action="store_true")
    parser.add_argument("--status",        default="draft", help="draft|publish|any")
    args = parser.parse_args()

    if args.dry_run:
        print("\n*** DRY RUN — no changes will be made ***\n")

    prog = load_progress() if args.resume else {"done": [], "failed": []}
    done_ids = set(prog["done"])

    # Load gallery
    media_items   = fetch_all_media()
    gallery_index = build_gallery_index(media_items)
    print(f"  Gallery index: {len(gallery_index)} searchable keys")

    # Fetch products missing images
    statuses = ["draft", "publish"] if args.status == "any" else [args.status]
    products = []
    for st in statuses:
        products.extend(fetch_products_missing_images(st))

    # Deduplicate
    seen = set()
    products = [p for p in products if not (p["id"] in seen or seen.add(p["id"]))]

    if args.resume:
        products = [p for p in products if p["id"] not in done_ids]

    print(f"\nTotal to process: {len(products)}")
    if not products:
        print("Nothing to do.")
        return

    # Process
    results    = []
    gallery_ct = web_ct = no_img_ct = error_ct = 0

    print(f"Processing with {args.workers} workers...\n")

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(process_product, p, gallery_index, args.no_web_search, args.dry_run): p for p in products}
        for i, fut in enumerate(as_completed(futures), 1):
            try:
                r = fut.result()
            except Exception as e:
                p = futures[fut]
                r = {"id": p["id"], "sku": p.get("sku",""), "name": p.get("name",""), "source": "error", "image": "", "result": str(e)}

            results.append(r)

            if "gallery" in r["source"]:
                gallery_ct += 1
            elif r["source"] == "web-search":
                web_ct += 1
            elif r["result"] == "api_error":
                error_ct += 1
            else:
                no_img_ct += 1

            status_sym = "✓" if r["result"] == "updated" else ("~" if args.dry_run and r["image"] else "✗")
            print(f"  [{i:4d}/{len(products)}] {status_sym} {r['name'][:55]:<55}  [{r['source'] or 'no match'}]")

            if r["result"] in ("updated",) or args.dry_run:
                prog["done"].append(r["id"])
            else:
                prog["failed"].append(r["id"])

            if i % 25 == 0:
                save_progress(prog)

    save_progress(prog)

    print(f"\n{'='*60}")
    print(f"  Gallery matches:  {gallery_ct}")
    print(f"  Web search found: {web_ct}")
    print(f"  No image found:   {no_img_ct}")
    print(f"  API errors:       {error_ct}")
    print(f"  TOTAL updated:    {gallery_ct + web_ct}")
    print(f"{'='*60}")

    save_report(results)

    if no_img_ct > 0:
        print(f"\n{no_img_ct} products still have no image.")
        print("Consider running with --no-web-search=False and checking the report.")


if __name__ == "__main__":
    main()
