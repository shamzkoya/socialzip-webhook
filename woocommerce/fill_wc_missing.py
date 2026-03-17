#!/usr/bin/env python3
"""
fill_wc_missing.py
==================
Directly fetches DRAFT products from WooCommerce and fills in:
  1. Prices  — from supplier stock sheets (40% markup + VAT for Impulse,
               20% for Hirsch, 30% for Tupperware, 35% for others)
  2. Descriptions + short descriptions via Claude Haiku
  3. SEO meta: title, meta description, keywords, focus keyword (Yoast)

Run:
  python3 fill_wc_missing.py
  python3 fill_wc_missing.py --skip-prices
  python3 fill_wc_missing.py --skip-seo
  python3 fill_wc_missing.py --batch-size 15
  python3 fill_wc_missing.py --status draft,publish
"""

import os, re, sys, json, time, argparse
from pathlib import Path

# ─── Env ──────────────────────────────────────────────────────────────────────
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
import anthropic

# ─── Config ───────────────────────────────────────────────────────────────────
WC_URL  = "https://happyharvesting.co.za/wp-json/wc/v3"
WC_AUTH = HTTPBasicAuth(
    "ck_d5322bdf8c16388bb4adcc573d7a0417b5ad0938",
    "cs_f75a2e8162382574310656a14cb368e823c3caf4",
)
CLAUDE  = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

BASE    = Path(__file__).parent
STOCK   = BASE / "stock_control"
PROG_F  = STOCK / "fill_wc_progress.json"

SEO_BATCH   = 15       # products per Claude call
WC_BATCH    = 50       # products per WC batch update

# ─── Supplier pricing ─────────────────────────────────────────────────────────
SUPPLIER_SHEETS = {
    "impulse":    (BASE / "suppliers/impulse/stock_sheet.xlsx",    0.40, 0.15),
    "tupperware": (BASE / "suppliers/tupperware/stock_sheet.xlsx", 0.30, 0.15),
    "hirsch":     (BASE / "suppliers/hirschs/stock_sheet.xlsx",    0.20, 0.15),
    "makokoya":   (BASE / "suppliers/makokoya/stock_sheet.xlsx",   0.35, 0.15),
    "mama_busi":  (BASE / "suppliers/mama_busi/stock_sheet.xlsx",  0.40, 0.15),
    "sagren":     (BASE / "suppliers/sagren/stock_sheet.xlsx",     0.35, 0.15),
    "mr_d":       (BASE / "suppliers/mr_d/stock_sheet.xlsx",       0.35, 0.15),
}

def build_price_index():
    """Returns {sku_lower: retail_price_float}."""
    idx = {}
    try:
        import openpyxl
    except ImportError:
        print("  [warn] openpyxl not installed — skipping supplier price sheets")
        return idx

    # Also load hirsch_wc_import.csv
    hirsch_csv = STOCK / "hirsch_wc_import.csv"
    if hirsch_csv.exists():
        import csv
        with open(hirsch_csv) as f:
            for row in csv.DictReader(f):
                sku = row.get("SKU", "").strip()
                p   = row.get("Regular price", "").strip()
                if sku and p:
                    try: idx[sku.lower()] = float(p)
                    except: pass

    for label, (path, markup, vat) in SUPPLIER_SHEETS.items():
        if not path.exists():
            continue
        try:
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            ws = wb.active
            rows = list(ws.iter_rows(values_only=True))
            count = 0
            for r in rows[2:]:
                sup_sku = str(r[0]).strip() if r[0] else ""
                wc_sku  = str(r[1]).strip() if r[1] else ""
                cost    = r[8]
                sell    = r[11]
                row_vat = float(r[9]) / 100 if r[9] else vat
                for sku in set(filter(None, [sup_sku, wc_sku])):
                    if sku and sku.lower() != "none":
                        try:
                            if sell:
                                idx[sku.lower()] = float(sell)
                            elif cost:
                                idx[sku.lower()] = round(float(cost) * (1 + markup) * (1 + row_vat), 2)
                            count += 1
                        except:
                            pass
            print(f"  {label:12s} {count:4d} SKUs loaded")
        except Exception as e:
            print(f"  [warn] {label}: {e}")

    print(f"  Price index total: {len(idx)} entries")
    return idx


# ─── WooCommerce helpers ──────────────────────────────────────────────────────
def wc_get_all(status="draft"):
    """Fetch all products with given status, returning list of dicts."""
    products = []
    page = 1
    while True:
        r = requests.get(f"{WC_URL}/products", auth=WC_AUTH, params={
            "status": status, "per_page": 100, "page": page,
            "_fields": "id,name,sku,description,short_description,regular_price,price,meta_data,categories,tags",
        }, timeout=30)
        items = r.json()
        if not items or not isinstance(items, list):
            break
        products.extend(items)
        if len(items) < 100:
            break
        page += 1
    return products


def wc_batch_update(updates):
    """Push batch of product updates. Returns (ok, err)."""
    if not updates:
        return 0, 0
    ok = err = 0
    for i in range(0, len(updates), WC_BATCH):
        chunk = updates[i:i + WC_BATCH]
        for attempt in range(4):
            try:
                r = requests.post(f"{WC_URL}/products/batch", auth=WC_AUTH,
                    json={"update": chunk}, timeout=60)
                if r.status_code in (200, 201):
                    data = r.json()
                    ok  += len(data.get("update", []))
                    errs = data.get("update_errors", [])
                    err += len([e for e in errs if e])
                    break
                else:
                    time.sleep(2 ** attempt)
            except Exception as e:
                print(f"  [warn] batch update attempt {attempt+1}: {e}")
                time.sleep(2 ** attempt)
    return ok, err


# ─── SEO generation ───────────────────────────────────────────────────────────
def generate_seo_batch(products):
    """Call Claude Haiku for a batch, return {sku: seo_dict}."""
    lines = []
    for i, p in enumerate(products, 1):
        cats = p.get("categories", [])
        cat  = cats[0]["name"] if cats else "General"
        lines.append(f"{i}. SKU:{p['sku'] or p['id']} | {p['name']} | Cat:{cat}")

    prompt = f"""You are writing product content for Happy Harvesting (happyharvesting.co.za), a South African online store selling homeware, garden, electronics, appliances, fashion and lifestyle products.

Generate SEO-optimised product content for ALL {len(products)} products listed below.
Write for South African shoppers. Prices are in Rands (ZAR).

CRITICAL: Return ONLY a valid JSON array. No markdown fences. No explanation.
All string values must be properly JSON-escaped. No HTML tags. Plain text only.

Products:
{chr(10).join(lines)}

For each product return an object with EXACTLY these keys:
- sku: exact SKU or id from input
- description: 120-180 words, plain text, no HTML
- short_description: 1-2 compelling sentences, max 160 chars
- meta_title: SEO page title, max 58 chars, include main keyword
- meta_description: SEO meta desc, max 150 chars, action-oriented
- keywords: 5-6 comma-separated keywords for SA buyers
- focus_keyword: single best keyword phrase, 2-4 words"""

    for attempt in range(3):
        try:
            resp = CLAUDE.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=8000,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
            )
            text = resp.content[0].text.strip()
            if text.startswith("```"):
                text = re.sub(r"^```[a-z]*\n?", "", text)
                text = re.sub(r"\n?```$", "", text)
            data = json.loads(text)
            return {str(item["sku"]): item for item in data}
        except json.JSONDecodeError as e:
            print(f"  [warn] JSON parse error attempt {attempt+1}: {e}")
            time.sleep(2)
        except anthropic.RateLimitError:
            print("  [rate limit] sleeping 30s...")
            time.sleep(30)
        except Exception as e:
            print(f"  [error] SEO attempt {attempt+1}: {e}")
            time.sleep(5)
    return {}


# ─── Progress ─────────────────────────────────────────────────────────────────
def load_progress():
    if PROG_F.exists():
        with open(PROG_F) as f:
            return json.load(f)
    return {"seo_done": [], "price_done": []}


def save_progress(prog):
    with open(PROG_F, "w") as f:
        json.dump(prog, f, indent=2)


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Fill missing WC product data")
    parser.add_argument("--skip-prices", action="store_true", help="Skip price filling")
    parser.add_argument("--skip-seo",    action="store_true", help="Skip SEO/description filling")
    parser.add_argument("--batch-size",  type=int, default=SEO_BATCH)
    parser.add_argument("--status",      default="draft", help="Product status (draft/publish/all)")
    parser.add_argument("--dry-run",     action="store_true", help="Preview only, no WC updates")
    args = parser.parse_args()

    print("=" * 65)
    print("  Happy Harvesting — Fill All Missing Product Data")
    print("=" * 65)

    prog = load_progress()
    seo_done  = set(prog.get("seo_done", []))
    price_done = set(str(x) for x in prog.get("price_done", []))

    # ── Fetch products ────────────────────────────────────────────────────────
    statuses = args.status.split(",")
    products = []
    for s in statuses:
        s = s.strip()
        print(f"\nFetching {s} products...")
        batch = wc_get_all(s)
        products.extend(batch)
        print(f"  {len(batch)} {s} products fetched")

    print(f"\nTotal products: {len(products)}")

    # Helper: get meta value
    def get_meta(p, key):
        for m in p.get("meta_data", []):
            if m["key"] == key:
                return str(m.get("value", "")).strip()
        return ""

    # ── Step 1: Price filling ────────────────────────────────────────────────
    price_updates = []
    if not args.skip_prices:
        print("\n[1/2] Filling missing prices from supplier sheets...")
        price_idx = build_price_index()

        for p in products:
            pid = str(p["id"])
            if pid in price_done:
                continue
            if p.get("regular_price") or p.get("price"):
                continue  # already has price
            sku = p.get("sku", "").strip().lower()
            retail = price_idx.get(sku)
            if retail:
                price_updates.append({"id": p["id"], "regular_price": str(retail)})
                price_done.add(pid)

        print(f"  Products to price: {len(price_updates)}")
        if price_updates and not args.dry_run:
            ok, err = wc_batch_update(price_updates)
            print(f"  Prices pushed: {ok} OK, {err} errors")
            prog["price_done"] = list(price_done)
            save_progress(prog)
        elif args.dry_run:
            for u in price_updates:
                p = next(x for x in products if x["id"] == u["id"])
                print(f"  [DRY] R{u['regular_price']:>10}  {p['name'][:50]}")
    else:
        print("\n[1/2] Skipping prices (--skip-prices)")

    # ── Step 2: SEO / descriptions ───────────────────────────────────────────
    seo_updates = []
    if not args.skip_seo:
        print("\n[2/2] Filling missing descriptions & SEO via Claude Haiku...")

        needs_seo = [
            p for p in products
            if str(p["id"]) not in seo_done
            and (
                not p.get("description", "").strip()
                or not get_meta(p, "_yoast_wpseo_title")
            )
        ]
        print(f"  Products needing SEO: {len(needs_seo)}")

        batch_size = args.batch_size
        total_batches = (len(needs_seo) + batch_size - 1) // (batch_size or 1)

        for batch_num, i in enumerate(range(0, len(needs_seo), batch_size), 1):
            batch = needs_seo[i:i + batch_size]
            print(f"  Batch {batch_num}/{total_batches} ({len(batch)} products)...", end=" ", flush=True)

            seo_data = generate_seo_batch(batch)
            if not seo_data:
                print("FAILED — skipping")
                continue

            for p in batch:
                key = str(p.get("sku") or p["id"])
                s = seo_data.get(key) or seo_data.get(str(p["id"]))
                if not s:
                    continue

                upd = {"id": p["id"]}
                if not p.get("description", "").strip() and s.get("description"):
                    upd["description"] = s["description"]
                if not p.get("short_description", "").strip() and s.get("short_description"):
                    upd["short_description"] = s["short_description"]

                # Build Yoast meta
                meta = []
                if s.get("meta_title") and not get_meta(p, "_yoast_wpseo_title"):
                    meta.append({"key": "_yoast_wpseo_title",    "value": s["meta_title"]})
                if s.get("meta_description") and not get_meta(p, "_yoast_wpseo_metadesc"):
                    meta.append({"key": "_yoast_wpseo_metadesc", "value": s["meta_description"]})
                if s.get("focus_keyword") and not get_meta(p, "_yoast_wpseo_focuskw"):
                    meta.append({"key": "_yoast_wpseo_focuskw",  "value": s["focus_keyword"]})
                if s.get("keywords"):
                    meta.append({"key": "_yoast_wpseo_metakeywords", "value": s["keywords"]})

                if meta:
                    upd["meta_data"] = meta

                if len(upd) > 1:
                    seo_updates.append(upd)
                    seo_done.add(str(p["id"]))

            prog["seo_done"] = list(seo_done)
            save_progress(prog)
            print(f"OK ({len(seo_data)} enriched)")
            time.sleep(0.5)

        print(f"\n  SEO updates prepared: {len(seo_updates)}")
        if seo_updates and not args.dry_run:
            ok, err = wc_batch_update(seo_updates)
            print(f"  SEO pushed: {ok} OK, {err} errors")
        elif args.dry_run:
            print(f"  [DRY RUN] would push {len(seo_updates)} SEO updates")
    else:
        print("\n[2/2] Skipping SEO (--skip-seo)")

    # ── Summary ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 65)
    print(f"  Prices applied  : {len(price_updates)}")
    print(f"  SEO updates     : {len(seo_updates)}")
    print("  Done.")
    print("=" * 65)


if __name__ == "__main__":
    main()
