#!/usr/bin/env python3
"""
Match Remaining Images
======================
For the products that fill_missing_images.py couldn't match, this script
uses the WordPress media SEARCH API to find gallery images by keyword.

It reads failed entries from image_fill_report.csv and for each product:
  1. Searches WP media gallery using brand name / key words from product name
  2. Shows the top matches with their URLs (so you can verify)
  3. In --apply mode, assigns the best match

Run:
  python3 match_remaining_images.py            # preview mode (dry run)
  python3 match_remaining_images.py --apply    # actually update WooCommerce
  python3 match_remaining_images.py --top N    # show top N candidates (default 5)
"""

import os, re, csv, json, time, argparse
from pathlib import Path
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

REPORT_FILE = SC / "image_fill_report.csv"


# ── helpers ────────────────────────────────────────────────────────────────────

def normalise(s):
    s = s.lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def safe_json(r):
    text = r.content.decode("utf-8", errors="replace")
    text = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', text)
    return json.loads(text)


def search_media(query, max_results=20):
    """Search WP media library by text query. Returns list of (media_id, url, title)."""
    results = []
    try:
        r = requests.get(
            f"{WP_API}/media",
            auth=AUTH,
            params={"search": query, "per_page": max_results, "media_type": "image"},
            timeout=30,
        )
        if not r.ok:
            return results
        data = safe_json(r)
        for m in data:
            mid   = m.get("id")
            url   = m.get("source_url", "") or (m.get("guid") or {}).get("rendered", "")
            title = (m.get("title") or {}).get("rendered", "")
            if url:
                if url.startswith("/"):
                    url = WC_URL + url
                results.append((mid, url, title))
    except Exception as e:
        print(f"    WARN: media search failed for '{query}': {e}")
    return results


def extract_search_terms(name):
    """
    Generate search queries from product name.
    Returns a list of queries to try (most specific first).
    """
    # Strip sizes/dimensions/codes that are noise
    clean = re.sub(r'\b\d+\s*(cm|mm|kg|l|ml|w|"\"|\')\b', '', name, flags=re.IGNORECASE)
    clean = re.sub(r'\s+', ' ', clean).strip()

    queries = []

    # Try the first meaningful words (brand + product type)
    words = [w for w in clean.split() if len(w) > 2 and not re.match(r'^\d+$', w)]

    # Full clean name
    if len(clean) > 3:
        queries.append(clean)

    # First 3 significant words
    if len(words) >= 3:
        queries.append(" ".join(words[:3]))

    # First 2 significant words (brand + noun)
    if len(words) >= 2:
        queries.append(" ".join(words[:2]))

    # Just the brand/first word (if long enough)
    if words and len(words[0]) > 3:
        queries.append(words[0])

    # Deduplicate preserving order
    seen = set()
    unique = []
    for q in queries:
        if q not in seen:
            seen.add(q)
            unique.append(q)
    return unique


def score_result(product_words, media_title, media_url):
    """Score a search result by word overlap with product name."""
    combined = normalise(media_title + " " + media_url.rsplit("/", 1)[-1].rsplit(".", 1)[0])
    combined_words = set(combined.split())
    overlap = sum(1 for w in product_words if w in combined_words and len(w) > 2)
    return overlap


def find_gallery_candidates(name, sku, top_n=5):
    """Search gallery for matching images. Returns list of (score, media_id, url, title)."""
    queries    = extract_search_terms(name)
    sku_norm   = normalise(sku)
    name_norm  = normalise(name)
    prod_words = [w for w in name_norm.split() if len(w) > 2 and not re.match(r'^\d+$', w)]

    seen_ids = set()
    all_results = []

    # Also try SKU directly if it looks alphanumeric/descriptive
    if sku and re.search(r'[a-zA-Z]', sku):
        queries = [sku] + queries

    for query in queries:
        results = search_media(query, max_results=20)
        time.sleep(0.3)
        for mid, url, title in results:
            if mid in seen_ids:
                continue
            seen_ids.add(mid)
            s = score_result(prod_words, title, url)
            all_results.append((s, mid, url, title))

    # Sort: SKU exact matches first, then by word score, then recent (higher id)
    def sort_key(x):
        s, mid, url, title = x
        fname = url.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        # Boost if SKU appears in filename/title
        sku_boost = 100 if (sku_norm and (sku_norm in normalise(fname) or sku_norm in normalise(title))) else 0
        return -(sku_boost + s * 10 + mid / 1e9)

    all_results.sort(key=sort_key)
    return all_results[:top_n]


def load_no_image_products():
    """Read image_fill_report.csv and return rows with no image or api_error."""
    if not REPORT_FILE.exists():
        print(f"ERROR: {REPORT_FILE} not found. Run fill_missing_images.py first.")
        return []
    rows = []
    with open(REPORT_FILE, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["status"] in ("no_image_found", "api_error"):
                rows.append(row)
    return rows


def update_product_image(product_id, media_id):
    payload = {"images": [{"id": media_id}]}
    r = requests.put(f"{WC_API}/products/{product_id}", auth=AUTH, json=payload, timeout=30)
    return r.status_code in (200, 201)


# ── main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply",     action="store_true", help="Actually update products (default: dry-run)")
    parser.add_argument("--top",       type=int, default=5,  help="Show top N gallery candidates per product")
    parser.add_argument("--min-score", type=int, default=1,  help="Min word-overlap score to auto-assign (default 1)")
    args = parser.parse_args()

    if not args.apply:
        print("\n*** DRY RUN — pass --apply to update products ***\n")

    products = load_no_image_products()
    if not products:
        print("No products to process.")
        return
    print(f"Products without images: {len(products)}\n")

    updated      = 0
    still_missing = []

    for p in products:
        pid  = p["id"]
        sku  = p["sku"]
        name = p["name"]

        print(f"  [{pid}] {name}")
        print(f"    SKU: {sku or '(none)'}")

        candidates = find_gallery_candidates(name, sku, top_n=args.top)

        if not candidates:
            print(f"    → No gallery matches found\n")
            still_missing.append(name)
            continue

        print(f"    Gallery candidates (top {len(candidates)}):")
        for rank, (score, mid, url, title) in enumerate(candidates, 1):
            fname = url.rsplit("/", 1)[-1]
            label = title.strip() or fname
            print(f"      #{rank}  score={score}  id={mid}  {label}")
            print(f"           {url}")

        best_score, best_mid, best_url, best_title = candidates[0]
        if best_score >= args.min_score:
            if args.apply:
                ok = update_product_image(int(pid), best_mid)
                sym = "✓ updated" if ok else "✗ api_error"
                print(f"    → {sym}  (gallery id={best_mid})")
                if ok:
                    updated += 1
                else:
                    still_missing.append(name)
            else:
                print(f"    → Would assign id={best_mid}  (score={best_score})")
        else:
            print(f"    → Best score {best_score} is below --min-score {args.min_score} — skipping")
            still_missing.append(name)
        print()

    print("=" * 60)
    if args.apply:
        print(f"  Updated:       {updated}")
        print(f"  Still missing: {len(still_missing)}")
        if still_missing:
            print("\n  Products still without images:")
            for n in still_missing:
                print(f"    • {n}")
    else:
        print(f"  Total processed: {len(products)}")
        print("  Run with --apply to update products.")
    print("=" * 60)


if __name__ == "__main__":
    main()
