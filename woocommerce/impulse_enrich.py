#!/usr/bin/env python3
"""
============================================================
Impulse Product Enricher
============================================================
Takes impulse_new_products.csv and fills in:
  - Description (2-3 sentences, SEO-friendly)
  - Short description (1 sentence)
  - Tags / Keywords
  - Meta description (Yoast SEO)
  - Weight (kg), Length, Width, Height (cm)  — estimated by Claude
  - Images — searched via DuckDuckGo

Runs parallel workers for speed. Saves progress after every
batch so it can be resumed.

USAGE:
  python3 impulse_enrich.py \
    --input  stock_control/impulse_new_products.csv \
    --output stock_control/impulse_new_products_enriched.csv \
    [--workers 8] [--resume] [--no-images]
============================================================
"""

import os, re, csv, sys, json, time, threading, random, base64
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── .env ─────────────────────────────────────────────────────────────────────
def load_dotenv():
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
load_dotenv()

try:
    import anthropic
except ImportError:
    sys.exit("ERROR: pip3 install anthropic")

try:
    import requests
except ImportError:
    sys.exit("ERROR: pip3 install requests")

try:
    from ddgs import DDGS
    DDG_OK = True
except ImportError:
    try:
        from duckduckgo_search import DDGS
        DDG_OK = True
    except ImportError:
        DDG_OK = False
        print("WARNING: ddgs not installed — images will be skipped")

# ── Config ────────────────────────────────────────────────────────────────────
DESC_MODEL     = "claude-haiku-4-5-20251001"
DIM_MODEL      = "claude-haiku-4-5-20251001"
DEFAULT_WORKERS = 20
PROGRESS_FILE  = "stock_control/impulse_enrich_progress.json"
MAX_IMG_TRIES  = 4
IMG_TIMEOUT    = 6

_print_lock = threading.Lock()
_ddg_lock   = threading.Lock()   # DDG rate-limits: serialize searches

def log(msg):
    with _print_lock:
        print(msg, flush=True)

# ── Dimension estimator ───────────────────────────────────────────────────────
DIMENSION_PROMPT = """Given this product name, estimate realistic physical dimensions for e-commerce shipping.

Product: {name}
Category hint: {hint}
Brand: {brand}

Return ONLY a JSON object (no explanation):
{{"weight_kg": 0.5, "length_cm": 30, "width_cm": 10, "height_cm": 5}}

Rules:
- Be realistic for a physical retail product
- Weight should include packaging (add ~15%)
- All values as numbers (floats ok)
- If product has a size in the name (e.g. "2LT", "1.8M", "40x60CM"), use that
- Minimum weight 0.05 kg
"""

# ── Description prompt ────────────────────────────────────────────────────────
DESC_PROMPT = """Write WooCommerce product content AND estimate shipping dimensions for a South African hardware/homeware store.

Product: {name}
Brand: {brand}
Category: {hint}
Price: R{price}

Return ONLY a JSON object:
{{
  "description": "2-3 sentence product description. Highlight key features, materials, and practical use. Mention size/capacity where relevant. South African context (mention 'load shedding' for torches/batteries/lanterns). NO fluff.",
  "short_description": "One punchy sentence (max 20 words) summarising the product.",
  "tags": "comma-separated list of 8-12 relevant search tags (lowercase, no #)",
  "meta_description": "SEO meta description, max 155 chars, include key benefit and brand",
  "weight_kg": 0.5,
  "length_cm": 30,
  "width_cm": 10,
  "height_cm": 5
}}

Dimension rules: be realistic for a physical retail product, weight includes packaging (+15%), use any size in the name (e.g. "2LT", "1.8M"), minimum weight 0.05 kg."""

# ── Image search ──────────────────────────────────────────────────────────────
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36"}

TRUSTED_DOMAINS = [
    "takealot.com", "makro.co.za", "builders.co.za", "leroy.co.za",
    "trademe.co.nz", "amazon.com", "amazon.co.uk", "bunnings.com.au",
    "homedepot.com", "lowes.com", "walmart.com", "overstock.com",
    "superbalist.com", "game.co.za", "incredible.co.za"
]

def search_image(name: str, brand: str = "", sku: str = "") -> str:
    """Search DuckDuckGo for the best product image URL."""
    if not DDG_OK:
        return ""

    queries = []
    clean = re.sub(r'["\']', '', name)
    if brand and brand.lower() not in ("impulse", ""):
        queries.append(f"{brand} {clean} product")
    queries.append(f"{clean} hardware product image")
    if sku and sku.startswith("F0"):
        queries.append(f"impulse hardware {clean}")

    for query in queries:
        for attempt in range(3):
            try:
                with _ddg_lock:
                    time.sleep(0.4 + random.random() * 0.3)
                    ddgs = DDGS(proxy=None)
                    results = list(ddgs.images(query, max_results=8, safesearch="off"))

                for r in results:
                    url = r.get("image", "")
                    if not url or not url.startswith("http"):
                        continue
                    # Prefer trusted retail domains
                    domain_match = any(d in url for d in TRUSTED_DOMAINS)
                    if not url.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                        continue
                    try:
                        resp = requests.head(url, timeout=IMG_TIMEOUT, headers=HEADERS, allow_redirects=True)
                        if resp.status_code == 200:
                            ct = resp.headers.get("content-type", "")
                            if "image" in ct:
                                return url
                    except Exception:
                        continue
                break  # got results, move on
            except Exception as e:
                if attempt == 2:
                    break
                time.sleep(2 * (attempt + 1))

    return ""


# ── Text enrichment ───────────────────────────────────────────────────────────

def enrich_text(client, row: dict) -> dict:
    """Call Claude Haiku ONCE to get description, tags, meta, and dimensions."""
    name  = row.get("Name", "")
    brand = row.get("Brands", "") or "Impulse"
    price = row.get("Regular price", "")
    cats  = row.get("Categories", "")

    hint = cats.split(">")[-1].strip() if ">" in cats else cats.split(",")[0].strip()

    updates = {}
    prompt = DESC_PROMPT.format(name=name, brand=brand, hint=hint, price=price)

    for attempt in range(3):
        try:
            resp = client.messages.create(
                model=DESC_MODEL,
                max_tokens=600,
                messages=[{"role": "user", "content": prompt}]
            )
            raw = resp.content[0].text.strip()
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
            data = json.loads(raw)
            updates["Description"]       = data.get("description", "")
            updates["Short description"] = data.get("short_description", "")
            updates["Tags"]              = data.get("tags", "")
            updates["Meta: _yoast_wpseo_metadesc"] = data.get("meta_description", "")[:155]
            updates["Weight (kg)"]  = str(round(float(data.get("weight_kg",  0.5)), 3))
            updates["Length (cm)"]  = str(round(float(data.get("length_cm", 20.0)), 1))
            updates["Width (cm)"]   = str(round(float(data.get("width_cm",  10.0)), 1))
            updates["Height (cm)"]  = str(round(float(data.get("height_cm",  5.0)), 1))
            break
        except Exception as e:
            if attempt == 2:
                updates["Description"]       = f"{name} — quality product by {brand}."
                updates["Short description"] = f"{name} by {brand}."
                updates["Tags"]              = hint.lower()
                updates["Meta: _yoast_wpseo_metadesc"] = f"Buy {name} by {brand} at SocialZip."
                updates["Weight (kg)"]  = "0.5"
                updates["Length (cm)"]  = "20.0"
                updates["Width (cm)"]   = "10.0"
                updates["Height (cm)"]  = "5.0"
            else:
                time.sleep(2 * (attempt + 1))

    return updates


def enrich_row(client, row: dict, no_images: bool) -> dict:
    """Full enrichment: text + image for one product."""
    name  = row.get("Name", "")
    brand = row.get("Brands", "") or ""
    sku   = row.get("SKU", "")

    text_updates = enrich_text(client, row)
    row.update(text_updates)

    if not no_images and not row.get("Images", "").strip():
        img = search_image(name, brand, sku)
        if img:
            row["Images"] = img

    return row


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--input",   default="stock_control/impulse_new_products.csv")
    p.add_argument("--output",  default="stock_control/impulse_new_products_enriched.csv")
    p.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    p.add_argument("--resume",  action="store_true")
    p.add_argument("--no-images", action="store_true")
    return p.parse_args()


def main():
    args   = parse_args()
    base   = Path(__file__).parent
    in_csv  = base / args.input
    out_csv = base / args.output
    prog_f  = base / PROGRESS_FILE

    if not in_csv.exists():
        sys.exit(f"ERROR: {in_csv} not found")

    client = anthropic.Anthropic()

    # Load input CSV
    with open(in_csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    log(f"Loaded {len(rows)} products from {in_csv.name}")

    # Load progress
    done_skus = set()
    enriched_rows = {}   # sku → enriched row
    if args.resume and prog_f.exists():
        with open(prog_f) as f:
            enriched_rows = json.load(f)
        done_skus = set(enriched_rows.keys())
        log(f"Resuming: {len(done_skus)} already enriched")

    todo = [r for r in rows if r.get("SKU", "") not in done_skus]
    log(f"To enrich: {len(todo)} products ({args.workers} workers, images={'off' if args.no_images else 'on'})\n")

    save_lock = threading.Lock()
    counter = [0]

    def save_progress():
        with save_lock:
            with open(prog_f, "w") as f:
                json.dump(enriched_rows, f)

    def process_one(row):
        enriched = enrich_row(client, dict(row), args.no_images)
        sku = enriched.get("SKU", "?")
        with save_lock:
            enriched_rows[sku] = enriched
            counter[0] += 1
            n = counter[0]
        if n % 5 == 0 or n == len(todo):
            save_progress()
        img_flag = "📷" if enriched.get("Images") else "  "
        log(f"  [{n:4d}/{len(todo)}] {img_flag} {sku:12s} {enriched.get('Name','')[:45]}")
        return enriched

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(process_one, r) for r in todo]
        for f in as_completed(futures):
            try:
                f.result()
            except Exception as e:
                log(f"  ERROR: {e}")

    # Merge: start from original order, apply enrichment
    final_rows = []
    for row in rows:
        sku = row.get("SKU", "")
        if sku in enriched_rows:
            final_rows.append(enriched_rows[sku])
        else:
            final_rows.append(row)

    # Write output
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(final_rows)

    # Stats
    n_desc = sum(1 for r in final_rows if r.get("Description","").strip())
    n_img  = sum(1 for r in final_rows if r.get("Images","").strip())
    n_dim  = sum(1 for r in final_rows if r.get("Weight (kg)","").strip())
    n_tags = sum(1 for r in final_rows if r.get("Tags","").strip())

    log(f"\n{'='*60}")
    log(f"ENRICHMENT COMPLETE — {len(final_rows)} products")
    log(f"  Descriptions:  {n_desc}")
    log(f"  Images found:  {n_img}")
    log(f"  Dimensions:    {n_dim}")
    log(f"  Tags/keywords: {n_tags}")
    log(f"\n  Output: {out_csv}")
    log(f"{'='*60}")


if __name__ == "__main__":
    main()
