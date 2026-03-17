#!/usr/bin/env python3
"""
price_gap_filler.py
===================
Fills prices for the 189-product price gap:

1. Power 24 (6 products)      — DELETE via WC API
2. Hirsch (31 products)       — name/model fuzzy match from hirsch_supplier_feb_mar_2026.csv  (20% markup)
3. Tupperware (15 products)   — name fuzzy match from stock_current.csv                       (30% markup on cost)
4. Potstory (22 products)     — name fuzzy match from PRICE_LIST_JAN_2022_Nurseries.xls       (40% markup + VAT)
5. Makokoya/Kristal (46 rows) — by WC ID from stock_sheet.xlsx (selling price already set)

Run:
  python3 price_gap_filler.py
  python3 price_gap_filler.py --dry-run
"""

import os, sys, csv, re, time, json, argparse
from pathlib import Path
from difflib import SequenceMatcher

# ── Env ───────────────────────────────────────────────────────────────────────
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

# ── Config ────────────────────────────────────────────────────────────────────
WC_URL  = "https://happyharvesting.co.za/wp-json/wc/v3"
WC_AUTH = HTTPBasicAuth(
    "ck_d5322bdf8c16388bb4adcc573d7a0417b5ad0938",
    "cs_f75a2e8162382574310656a14cb368e823c3caf4",
)

BASE    = Path(__file__).parent
STOCK   = BASE / "stock_control"

# ── Markup constants ──────────────────────────────────────────────────────────
HIRSCH_MARKUP    = 0.20
TUPP_MARKUP      = 0.30
POTSTORY_MARKUP  = 0.40
VAT              = 0.15
FUZZY_THRESHOLD  = 0.55   # min similarity score to accept a match
HIRSCH_THRESHOLD = 0.60   # stricter for electronics (high-value)
TUPP_THRESHOLD   = 0.50   # Tupperware name matching

KNOWN_BRANDS = {
    "samsung", "defy", "kic", "hisense", "lg", "bosch", "kenwood",
    "smeg", "dyson", "siemens", "aeg", "whirlpool", "panasonic",
    "russell hobbs", "mellerware", "omega", "telefunken", "sinotec",
    "jvc", "condere", "sharp", "itel", "skyworth",
    "beko", "zanussi", "electrolux", "miele", "neff", "candy",
}

# ── Helpers ───────────────────────────────────────────────────────────────────
def similarity(a, b):
    """Word-overlap + sequence similarity, 0-1."""
    a, b = a.lower().strip(), b.lower().strip()
    seq = SequenceMatcher(None, a, b).ratio()
    words_a = set(re.split(r'\W+', a))
    words_b = set(re.split(r'\W+', b))
    words_a.discard("")
    words_b.discard("")
    if not words_a or not words_b:
        return seq
    overlap = len(words_a & words_b) / max(len(words_a), len(words_b))
    return max(seq, overlap)

def brand_in(text):
    t = text.lower()
    for brand in KNOWN_BRANDS:
        if brand in t:
            return brand
    return None

def best_match(query, candidates, threshold=FUZZY_THRESHOLD, brand_strict=False):
    """
    candidates: list of (key, price).
    brand_strict: if True, query brand must match candidate brand.
    Returns (key, price, score) or None.
    """
    query_brand = brand_in(query)
    best = None
    best_score = 0
    for key, price in candidates:
        if brand_strict and query_brand:
            cand_brand = brand_in(key)
            if cand_brand and cand_brand != query_brand:
                continue  # skip cross-brand matches
        s = similarity(query, key)
        if s > best_score:
            best_score = s
            best = (key, price, s)
    if best and best[2] >= threshold:
        return best
    return None

def wc_delete(product_id):
    r = requests.delete(
        f"{WC_URL}/products/{product_id}",
        auth=WC_AUTH,
        params={"force": True},
        timeout=30,
    )
    return r.status_code in (200, 201)

def wc_set_price(product_id, price, dry_run=False):
    if dry_run:
        return True
    r = requests.put(
        f"{WC_URL}/products/{product_id}",
        auth=WC_AUTH,
        json={"regular_price": str(round(price, 2))},
        timeout=30,
    )
    return r.status_code in (200, 201)

def wc_batch_update(updates, dry_run=False):
    """updates: list of {id, regular_price}"""
    if not updates or dry_run:
        return len(updates), 0
    ok = err = 0
    for i in range(0, len(updates), 50):
        chunk = updates[i:i+50]
        for attempt in range(4):
            try:
                r = requests.post(
                    f"{WC_URL}/products/batch",
                    auth=WC_AUTH,
                    json={"update": chunk},
                    timeout=60,
                )
                if r.status_code in (200, 201):
                    data = r.json()
                    ok += len(data.get("update", []))
                    break
                time.sleep(2 ** attempt)
            except Exception as e:
                print(f"  [warn] batch attempt {attempt+1}: {e}")
                time.sleep(2 ** attempt)
        else:
            err += len(chunk)
    return ok, err

# ── 1. Power 24 — DELETE ──────────────────────────────────────────────────────
POWER24_IDS = [20185, 20191, 20203, 20248, 20256, 20280]

def delete_power24(dry_run=False):
    print("\n=== 1. Power 24 — DELETE ===")
    for pid in POWER24_IDS:
        if dry_run:
            print(f"  [dry] would delete ID {pid}")
            continue
        ok = wc_delete(pid)
        print(f"  {'✓' if ok else '✗'} deleted ID {pid}")
        time.sleep(0.3)

# ── 2. Hirsch — price from supplier CSV ───────────────────────────────────────
def load_hirsch_price_list():
    """
    Returns list of (search_key, sale_price) for fuzzy matching.
    search_key = brand + ' ' + product_name + ' ' + model
    """
    path = BASE / "suppliers/hirschs/hirsch_supplier_feb_mar_2026.csv"
    items = []
    with open(path) as f:
        for row in csv.DictReader(f):
            brand   = row.get("Brand", "").strip()
            name    = row.get("Product Name", "").strip()
            model   = row.get("Model", "").strip()
            price_s = row.get("Sale Price", "").strip()
            if not price_s:
                continue
            try:
                price = float(price_s)
            except ValueError:
                continue
            # Build multiple search keys
            key = f"{brand} {name} {model}".strip()
            items.append((key, price))
            if model:
                items.append((model, price))   # model-only key for parenthetical matches
    return items

def extract_model(name):
    """Extract model number from parentheses in product name."""
    m = re.search(r'\(([A-Z0-9]{5,})\)', name.upper())
    return m.group(1) if m else None

def price_hirsch(dry_run=False):
    print("\n=== 2. Hirsch — fuzzy price match ===")
    candidates = load_hirsch_price_list()
    print(f"  Loaded {len(candidates)} Hirsch price list entries")

    # Load master_stock to get Hirsch no-price products
    with open(STOCK / "master_stock.csv") as f:
        rows = list(csv.DictReader(f))

    targets = [r for r in rows if r.get("supplier") == "hirschs" and not r.get("regular_price")]
    print(f"  Hirsch no-price products: {len(targets)}")

    updates = []
    skipped = []
    for row in targets:
        wc_id = row["wc_id"]
        name  = row["name"]
        sku   = row.get("sku", "")

        # Try model from parentheses first
        model = extract_model(name) or extract_model(sku)
        query = f"{model} {name}" if model else name

        match = best_match(query, candidates, threshold=HIRSCH_THRESHOLD, brand_strict=True)
        if match:
            matched_key, price, score = match
            # Apply markup: supplier price is sale price (already retail); 20% over for our store
            retail = round(price * (1 + HIRSCH_MARKUP), 2)
            print(f"  ✓ [{score:.2f}] {name[:50]} → R{retail} (from: {matched_key[:40]})")
            updates.append({"id": int(wc_id), "regular_price": str(retail)})
        else:
            print(f"  ✗ NO MATCH: {name[:60]}")
            skipped.append(row)

    if updates:
        ok, err = wc_batch_update(updates, dry_run)
        print(f"  Pushed {ok} prices, {err} errors")
    print(f"  Skipped (no match): {len(skipped)}")
    return skipped

# ── 3. Tupperware — price from stock_current.csv ─────────────────────────────
def price_tupperware(dry_run=False):
    print("\n=== 3. Tupperware — name fuzzy match ===")
    # Use stock_sheet.xlsx (selling prices, more current than stock_current.csv)
    try:
        import openpyxl
        path = BASE / "suppliers/tupperware/stock_sheet.xlsx"
        wb   = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
        ws   = wb.active
        candidates = []
        for r in list(ws.iter_rows(values_only=True))[2:]:
            name    = str(r[4]).strip() if r[4] else ""   # col E = Product Name
            selling = r[11]                                # col L = Selling Price
            if name and selling:
                try:
                    candidates.append((name, float(selling)))
                except (ValueError, TypeError):
                    pass
    except Exception as e:
        print(f"  [warn] stock_sheet.xlsx failed ({e}), falling back to stock_current.csv")
        path = BASE / "suppliers/tupperware/stock_current.csv"
        candidates = []
        with open(path) as f:
            for row in csv.DictReader(f):
                name  = row.get("name", "").strip()
                price = row.get("price", "").strip()
                if name and price:
                    try:
                        candidates.append((name, float(price)))
                    except ValueError:
                        pass
    print(f"  Loaded {len(candidates)} Tupperware entries")

    with open(STOCK / "master_stock.csv") as f:
        rows = list(csv.DictReader(f))
    targets = [r for r in rows if r.get("supplier") == "tupperware" and not r.get("regular_price")]
    print(f"  Tupperware no-price products: {len(targets)}")

    updates = []
    skipped = []
    for row in targets:
        wc_id = row["wc_id"]
        name  = row["name"]
        match = best_match(name, candidates, threshold=TUPP_THRESHOLD)
        if match:
            matched_key, cost, score = match
            # stock_sheet selling prices are already retail — use as-is
            retail = round(cost, 2)
            print(f"  ✓ [{score:.2f}] {name[:50]} → R{retail} (from: {matched_key[:40]})")
            updates.append({"id": int(wc_id), "regular_price": str(retail)})
        else:
            print(f"  ✗ NO MATCH: {name[:60]}")
            skipped.append(row)

    if updates:
        ok, err = wc_batch_update(updates, dry_run)
        print(f"  Pushed {ok} prices, {err} errors")
    print(f"  Skipped: {len(skipped)}")
    return skipped

# ── 4. Potstory — price from .xls ────────────────────────────────────────────
def price_potstory(dry_run=False):
    print("\n=== 4. Potstory — .xls price match ===")
    try:
        import xlrd
    except ImportError:
        print("  [error] xlrd not installed — run: pip install xlrd")
        return []

    xls_path = BASE / "suppliers/pot_story/PRICE_LIST_JAN_2022_Nurseries.xls"
    wb = xlrd.open_workbook(str(xls_path))
    ws = wb.sheet_by_index(0)

    # Build candidates: (name, cost_ex_vat)
    candidates = []
    for i in range(ws.nrows):
        row = [ws.cell_value(i, j) for j in range(ws.ncols)]
        name  = str(row[0]).strip()
        price = row[2] if len(row) > 2 else ""
        if name and isinstance(price, (int, float)) and price > 0:
            candidates.append((name, price))
    print(f"  Loaded {len(candidates)} Potstory entries (ex-VAT)")

    with open(STOCK / "master_stock.csv") as f:
        rows = list(csv.DictReader(f))

    # Potstory products: name starts with 'Potstory'
    targets = [r for r in rows if r["name"].lower().startswith("potstory") and not r.get("regular_price")]
    print(f"  Potstory no-price products: {len(targets)}")

    updates = []
    skipped = []
    for row in targets:
        wc_id = row["wc_id"]
        name  = row["name"]
        # Strip "Potstory " prefix for matching
        query = re.sub(r'^potstory\s+', '', name, flags=re.I).strip()
        # Also try stripping colour suffix: "Rose Pot White" → "Rose Pot"
        query_short = re.sub(r'\s+(white|black|green|grey|red|blue|peach|terracotta|brown|beige|cream|tan|sand|charcoal)$', '', query, flags=re.I)

        match = best_match(query, candidates) or best_match(query_short, candidates)
        if match:
            matched_key, cost_ex_vat, score = match
            # cost_ex_vat * markup * (1+VAT)
            retail = round(cost_ex_vat * (1 + POTSTORY_MARKUP) * (1 + VAT), 2)
            print(f"  ✓ [{score:.2f}] {name[:50]} → R{retail} (from: {matched_key} @ R{cost_ex_vat} ex-VAT)")
            updates.append({"id": int(wc_id), "regular_price": str(retail)})
        else:
            print(f"  ✗ NO MATCH: {name[:60]} (query: {query})")
            skipped.append(row)

    if updates:
        ok, err = wc_batch_update(updates, dry_run)
        print(f"  Pushed {ok} prices, {err} errors")
    print(f"  Skipped: {len(skipped)}")
    return skipped

# ── 5. Makokoya/Kristal — from stock_sheet.xlsx ───────────────────────────────
def price_makokoya(dry_run=False):
    print("\n=== 5. Makokoya/Kristal — sheet selling price → WC ===")
    try:
        import openpyxl
    except ImportError:
        print("  [error] openpyxl not installed")
        return []

    path = BASE / "suppliers/makokoya/stock_sheet.xlsx"
    wb   = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    ws   = wb.active
    rows = list(ws.iter_rows(values_only=True))

    # Col indices (0-based): WC SKU=1, WC ID=2, Selling Price=11
    # Build {wc_id: selling_price} for rows that have a selling price
    id_to_price = {}
    for r in rows[2:]:   # skip header rows
        wc_id   = r[2]
        selling = r[11]
        if wc_id and selling:
            try:
                id_to_price[str(int(wc_id))] = float(selling)
            except (ValueError, TypeError):
                pass
    print(f"  {len(id_to_price)} Makokoya rows with selling price in sheet")

    # Get current no-price makokoya products from master_stock
    with open(STOCK / "master_stock.csv") as f:
        stock_rows = list(csv.DictReader(f))

    targets = [r for r in stock_rows if r.get("supplier") == "makokoya" and not r.get("regular_price")]
    print(f"  Makokoya no-price products in WC: {len(targets)}")

    updates = []
    skipped = []
    for row in targets:
        wc_id = row["wc_id"]
        price = id_to_price.get(wc_id)
        if price:
            print(f"  ✓ ID {wc_id} {row['name'][:50]} → R{price}")
            updates.append({"id": int(wc_id), "regular_price": str(price)})
        else:
            skipped.append(row)

    if updates:
        ok, err = wc_batch_update(updates, dry_run)
        print(f"  Pushed {ok} prices, {err} errors")
    print(f"  Skipped (no sheet entry): {len(skipped)}")

    # Summary of remaining Kristal collections
    kristal_skipped = [r for r in skipped if "kristal" in r["name"].lower()]
    if kristal_skipped:
        from collections import Counter
        def collection(name):
            m = re.search(r'-(.*?)-', name)
            return m.group(1).strip() if m else "Unknown"
        counts = Counter(collection(r["name"]) for r in kristal_skipped)
        print(f"\n  Kristal collections still needing prices:")
        for col, cnt in counts.most_common():
            print(f"    {col}: {cnt} products")

    return skipped

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Fill price gaps for unpriced WC products")
    parser.add_argument("--dry-run", action="store_true", help="Print matches but don't push to WC")
    parser.add_argument("--only", help="Comma-separated: power24,hirsch,tupperware,potstory,makokoya")
    args = parser.parse_args()

    only = set(args.only.split(",")) if args.only else {"power24", "hirsch", "tupperware", "potstory", "makokoya"}

    if args.dry_run:
        print("*** DRY RUN — no changes will be made ***\n")

    if "power24" in only:
        delete_power24(args.dry_run)

    if "hirsch" in only:
        price_hirsch(args.dry_run)

    if "tupperware" in only:
        price_tupperware(args.dry_run)

    if "potstory" in only:
        price_potstory(args.dry_run)

    if "makokoya" in only:
        price_makokoya(args.dry_run)

    print("\n=== Done ===")

if __name__ == "__main__":
    main()
