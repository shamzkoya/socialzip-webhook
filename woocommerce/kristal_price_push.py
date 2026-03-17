#!/usr/bin/env python3
"""
kristal_price_push.py
=====================
Prices all unpriced Kristal carpet variant products by:
  1. Querying WC API for each parent product's variations + Size attribute
  2. Looking up the per-size price from Kristal website pricing grids
  3. Applying a 10% markup to be competitive
  4. Batch-updating all variant prices via WC API

Run:
  python3 kristal_price_push.py
  python3 kristal_price_push.py --dry-run
  python3 kristal_price_push.py --dry-run --limit 5
"""

import os, re, csv, sys, time, json, argparse
from pathlib import Path

def _load_env():
    env = Path(__file__).parent / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.strip() and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
_load_env()

import requests
from requests.auth import HTTPBasicAuth

WC_URL  = "https://happyharvesting.co.za/wp-json/wc/v3"
WC_AUTH = HTTPBasicAuth(
    "ck_d5322bdf8c16388bb4adcc573d7a0417b5ad0938",
    "cs_f75a2e8162382574310656a14cb368e823c3caf4",
)

BASE  = Path(__file__).parent
STOCK = BASE / "stock_control"

MARKUP = 0.10   # 10% over Kristal website prices

# ── Pricing grids (Kristal website ZAR, exact) ────────────────────────────────
# Key = "WxH" normalised (e.g. "80x150"), Value = ZAR price
PRICING = {
    "artist": {
        "80x150": 809, "80x200": 1069, "80x300": 1599,
        "120x170": 1369, "160x230": 2449, "200x290": 3869,
        "240x340": 5889, "300x400": 8659,
    },
    "alin": {
        "80x150": 399, "80x200": 529, "80x300": 789, "80x400": 1049,
        "120x170": 689, "160x230": 1199, "200x290": 1899,
        "240x340": 2889, "300x400": 4249,
    },
    "boutique": {
        "80x150": 559, "80x200": 749, "120x170": 949,
        "150x220": 1539, "160x230": 1699,   # 160x230 interpolated between 120x170 and 200x290
        "200x290": 2689, "240x330": 3999, "240x340": 3999,
    },
    "dejavu": {
        "120x180": 1159, "160x235": 2019, "200x290": 3119, "240x315": 4399,
        # map common variants
        "120x170": 1159, "160x230": 2019, "200x280": 3119, "240x340": 4399,
    },
    "amber": {
        "120x170": 1199, "160x230": 2249, "200x290": 3549, "240x340": 5299,
    },
    "eco": {
        "80x150": 349, "80x250": 579, "120x170": 589,
        "160x220": 999, "200x280": 1599,
        "160x230": 999, "200x290": 1599,   # common size aliases
    },
    "porto": {
        # Porto Eco same grid
        "80x150": 349, "80x250": 579, "120x170": 589,
        "160x220": 999, "200x280": 1599, "160x230": 999, "200x290": 1599,
    },
    # Estimated grids for collections not on website API
    "ikon": {
        "50x80": 220, "60x100": 300,
        "80x150": 399, "80x200": 529, "80x300": 789,
        "110x160": 689, "120x170": 689,
        "150x200": 1199, "160x230": 1199,
        "200x290": 1899, "240x340": 2889,
    },
    "luna": {
        "80x140": 559, "80x150": 559, "80x200": 749,
        "120x170": 949, "160x230": 1699,
        "200x290": 2689, "240x340": 3800,
    },
    "palma": {
        "80x150": 559, "80x200": 749, "120x170": 949,
        "160x230": 1699, "200x290": 2689, "240x340": 3800,
    },
    "pastel": {
        "80x150": 399, "80x200": 529, "80x300": 789,
        "120x170": 689, "160x230": 1199, "200x290": 1899, "240x340": 2889,
    },
}

def detect_collection(name):
    """Return collection key from product name."""
    name_l = name.lower()
    for col in ["artist", "alin", "boutique", "dejavu", "amber", "eco", "porto", "luna", "palma", "pastel", "ikon"]:
        if col in name_l:
            return col
    # Fall back to 'boutique' for generic Kristal Turkish rug entries
    return "boutique"

def normalise_size(size_str):
    """
    '80 x 150 cm' → '80x150'
    '80X150CM' → '80x150'
    """
    s = re.sub(r'[^0-9x]', '', size_str.lower().replace(" ", ""))
    # handle X vs x
    s = s.replace("x", "x")
    return s

def lookup_price(collection, size_norm):
    """Returns marked-up price or None."""
    grid = PRICING.get(collection, {})
    base = grid.get(size_norm)
    if base is None:
        # Fuzzy: find closest key by area
        def area(k):
            try:
                w, h = k.split("x")
                return int(w) * int(h)
            except:
                return 0
        try:
            q_w, q_h = size_norm.split("x")
            q_area = int(q_w) * int(q_h)
        except:
            return None
        best_key = min(grid.keys(), key=lambda k: abs(area(k) - q_area)) if grid else None
        if best_key:
            base = grid[best_key]
    if base is None:
        return None
    return round(base * (1 + MARKUP), 2)

def get_variations(parent_id):
    """Fetch all variations for a product. Returns list of dicts with id, size, price."""
    results = []
    page = 1
    while True:
        for attempt in range(4):
            try:
                r = requests.get(
                    f"{WC_URL}/products/{parent_id}/variations",
                    auth=WC_AUTH,
                    params={"per_page": 100, "page": page, "_fields": "id,regular_price,attributes"},
                    timeout=30,
                )
                if r.status_code == 200:
                    break
                time.sleep(2 ** attempt)
            except Exception as e:
                print(f"    [retry {attempt+1}] {e}")
                time.sleep(2 ** attempt)
        else:
            return results  # give up after 4 retries
        items = r.json() if r.status_code == 200 else []
        if not items:
            break
        for v in items:
            size_str = ""
            for attr in v.get("attributes", []):
                if "size" in attr.get("name", "").lower():
                    size_str = attr.get("option", "")
                    break
            results.append({
                "id": v["id"],
                "size": size_str,
                "current_price": v.get("regular_price", ""),
            })
        if len(items) < 100:
            break
        page += 1
    return results

def push_variation_prices(parent_id, updates, dry_run=False):
    """
    updates: list of {id, regular_price}
    Returns (ok_count, err_count)
    """
    if dry_run or not updates:
        return len(updates), 0
    for attempt in range(4):
        try:
            r = requests.post(
                f"{WC_URL}/products/{parent_id}/variations/batch",
                auth=WC_AUTH,
                json={"update": updates},
                timeout=60,
            )
            if r.status_code in (200, 201):
                data = r.json()
                return len(data.get("update", [])), 0
            time.sleep(2 ** attempt)
        except Exception as e:
            print(f"    [warn] variation batch attempt {attempt+1}: {e}")
            time.sleep(2 ** attempt)
    return 0, len(updates)

def main():
    parser = argparse.ArgumentParser(description="Price Kristal carpet variants from website pricing grid")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="Process only first N products")
    parser.add_argument("--skip", type=int, default=0, help="Skip first N products (resume)")
    args = parser.parse_args()

    if args.dry_run:
        print("*** DRY RUN — no changes will be made ***\n")

    # Load Kristal parent product IDs from master_stock.csv
    with open(STOCK / "master_stock.csv") as f:
        stock = list(csv.DictReader(f))

    kristal_parents = [
        row for row in stock
        if row.get("supplier") == "makokoya"
        and "kristal" in row.get("name", "").lower()
        and not row.get("regular_price")
    ]
    print(f"Kristal parent products to price: {len(kristal_parents)}")
    if args.skip:
        kristal_parents = kristal_parents[args.skip:]
        print(f"  (skipping first {args.skip}, resuming from #{args.skip+1})")
    if args.limit:
        kristal_parents = kristal_parents[:args.limit]
        print(f"  (limited to {args.limit})")

    total_vars = 0
    priced_vars = 0
    no_size_vars = 0
    no_price_vars = 0
    product_errs = 0
    skipped_products = []

    for i, row in enumerate(kristal_parents, 1):
        parent_id = int(row["wc_id"])
        name = row["name"]
        collection = detect_collection(name)

        print(f"\n[{i}/{len(kristal_parents)}] {name[:60]} (ID:{parent_id}, col:{collection})")

        variations = get_variations(parent_id)
        if not variations:
            print(f"  [skip] no variations found")
            skipped_products.append(row)
            product_errs += 1
            continue

        updates = []
        for v in variations:
            total_vars += 1
            size_str = v["size"]
            if not size_str:
                print(f"    [warn] variation {v['id']}: no size attribute")
                no_size_vars += 1
                continue
            if v["current_price"]:
                # Already has a price — skip
                continue

            size_norm = normalise_size(size_str)
            price = lookup_price(collection, size_norm)

            if price is None:
                print(f"    [warn] no price for size '{size_str}' (norm: {size_norm}) in {collection}")
                no_price_vars += 1
                continue

            print(f"    {size_str} ({size_norm}) → R{price}")
            updates.append({"id": v["id"], "regular_price": str(price)})

        if updates:
            ok, err = push_variation_prices(parent_id, updates, args.dry_run)
            priced_vars += ok
            product_errs += err
            print(f"  → {'[dry] ' if args.dry_run else ''}pushed {len(updates)} variant prices")
        else:
            print(f"  → already fully priced, nothing to update")

        time.sleep(0.2)   # polite delay

    print(f"\n{'='*60}")
    print(f"  Products processed : {len(kristal_parents)}")
    print(f"  Variant prices set : {priced_vars}")
    print(f"  No-size warnings   : {no_size_vars}")
    print(f"  No-grid warnings   : {no_price_vars}")
    print(f"  Errors             : {product_errs}")
    if skipped_products:
        print(f"  Skipped products   : {len(skipped_products)}")

if __name__ == "__main__":
    main()
