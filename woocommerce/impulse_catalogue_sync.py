#!/usr/bin/env python3
"""
============================================================
Impulse Catalogue PDF → WooCommerce Sync  (async / fast)
============================================================
1. Renders all PDF pages to JPEG images in parallel (threads)
2. Sends CONCURRENCY pages at once to Claude Vision
3. Matches against impulse/stock_sheet.xlsx (qty, cost, selling price)
4. Skips products already in WooCommerce (WC ID in stock sheet)
5. Generates WC import CSV with descriptions + auto-categories

USAGE:
  python3 impulse_catalogue_sync.py \
    --pdf   /tmp/impulse_catalogue.pdf \
    --stock suppliers/impulse/stock_sheet.xlsx \
    --out   stock_control \
    [--resume]            # skip pages already in impulse_extract.json
    [--workers 10]        # parallel Vision API calls (default 10)
    [--no-desc]           # skip AI descriptions (faster, add manually)

OUTPUTS:
  stock_control/impulse_new_products.csv   ← import into WooCommerce
  stock_control/impulse_extract.json       ← raw extraction (resumable)
  stock_control/impulse_skipped.csv        ← already in WC
============================================================
"""

import os, re, csv, sys, json, base64, argparse, time, threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── .env loader ───────────────────────────────────────────────────────────────
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
    import fitz  # PyMuPDF
except ImportError:
    sys.exit("ERROR: pip3 install PyMuPDF")

try:
    import openpyxl
except ImportError:
    sys.exit("ERROR: pip3 install openpyxl")

# ── Config ────────────────────────────────────────────────────────────────────
VISION_MODEL  = "claude-opus-4-6"
DESC_MODEL    = "claude-haiku-4-5-20251001"
DEFAULT_WORKERS = 10      # parallel Vision calls
PAGE_ZOOM     = 2.0
VAT_RATE      = 0.15
MARKUP        = 1.40
JPEG_QUALITY  = 85

CATEGORY_MAP = [
    (["brush", "paint brush", "roller"],              "Hardware > Painting & Decorating"),
    (["scraper", "trowel", "putty"],                  "Hardware > Painting & Decorating"),
    (["plier", "wrench", "spanner", "clamp"],         "Hardware > Hand Tools"),
    (["hammer", "chisel", "mallet"],                  "Hardware > Hand Tools"),
    (["drill", "bit", "driver"],                      "Hardware > Power Tools & Accessories"),
    (["tape measure", "measuring", "level"],          "Hardware > Measuring & Layout"),
    (["ladder", "step"],                              "Hardware > Ladders & Access"),
    (["safety", "glove", "goggle", "helmet", "vest"], "Hardware > Safety & PPE"),
    (["blade", "cutting disc", "grinding"],           "Hardware > Cutting & Grinding"),
    (["knife", "utility knife", "cutter"],            "Hardware > Cutting & Grinding"),
    (["sprayer", "spray", "pump"],                    "Hardware > Garden & Outdoor"),
    (["garden", "hose", "watering"],                  "Hardware > Garden & Outdoor"),
    (["adaptor", "plug", "socket", "multi plug", "extension", "switch"],
                                                      "Hardware > Electrical"),
    (["cable", "wire", "conduit", "breaker"],         "Hardware > Electrical"),
    (["torch", "flashlight", "lantern", "light", "lamp"],
                                                      "Hardware > Lighting"),
    (["battery", "charger", "inverter"],              "Hardware > Electrical > Loadshedding"),
    (["lock", "padlock", "chain", "hinge"],           "Hardware > Locks & Security"),
    (["screw", "nail", "bolt", "nut", "washer", "anchor"],
                                                      "Hardware > Fasteners"),
    (["sealant", "adhesive", "glue", "silicone"],     "Hardware > Adhesives & Sealants"),
    (["sandpaper", "abrasive", "sanding"],            "Hardware > Abrasives"),
    (["edging strip", "corner", "tile", "grout"],     "Hardware > Building Materials"),
    (["broom", "mop", "bucket", "dustpan", "cleaning"],
                                                      "Homeware > Cleaning & Organization"),
    (["flask", "vacuum flask", "thermos"],            "Homeware > Kitchen"),
    (["peg", "clothespeg", "laundry"],                "Homeware > Cleaning & Organization"),
    (["mat", "welcome mat", "doormat"],               "Homeware > Decor"),
    (["hot water bottle"],                            "Homeware > Health & Wellness"),
    (["storage", "container", "box", "basket"],      "Homeware > Storage & Organisation"),
    (["hook", "hanger", "rack"],                     "Homeware > Storage & Organisation"),
    (["fan", "heater", "air"],                       "Homeware > Appliances > Small Appliances"),
    (["kettle", "iron", "toaster"],                  "Homeware > Appliances > Small Appliances"),
]

WC_COLUMNS = [
    "ID", "Type", "SKU", "Name", "Published", "Is featured?",
    "Visibility in catalog", "Short description", "Description",
    "Date sale price starts", "Date sale price ends",
    "Tax status", "Tax class",
    "In stock?", "Stock", "Low stock amount",
    "Backorders allowed?", "Sold individually?",
    "Weight (kg)", "Length (cm)", "Width (cm)", "Height (cm)",
    "Allow customer reviews?", "Purchase note",
    "Sale price", "Regular price",
    "Categories", "Tags", "Shipping class",
    "Images", "Download limit", "Download expiry days",
    "Parent", "Grouped products", "Upsells", "Cross-sells",
    "External URL", "Button text", "Position",
    "Brands", "Attribute 1 name", "Attribute 1 value(s)",
    "Attribute 1 visible", "Attribute 1 global",
    "Meta: _yoast_wpseo_metadesc",
]

VISION_PROMPT = """Extract ALL products shown on this Impulse Hardware & Homeware catalogue page.

For each product return:
- sku: product code shown (e.g. F00123, DIY-001, or small tag code). null if not visible.
- name: full product name exactly as shown
- wholesale_price: lower/trade/wholesale price (number only, no R)
- retail_price: recommended retail price (number only, no R)
- brand: brand name if shown (ALCO, CRAFTSMAN, etc.)
- category_hint: brief type (e.g. "scraper", "multi plug", "paint brush")

Rules:
- EVERY product on the page, even small ones in corners
- Skip banners, headers, decorative elements
- Prices like "R12.50" → 12.50
- If only one price shown → put in retail_price

Return ONLY a JSON array:
[{"sku":null,"name":"Hammer","wholesale_price":29.99,"retail_price":44.99,"brand":"ALCO","category_hint":"hand tool"}]

If no products (cover/TOC/blank page) return: []"""


# ── Helpers ───────────────────────────────────────────────────────────────────

_print_lock = threading.Lock()

def log(msg):
    with _print_lock:
        print(msg, flush=True)


def page_to_b64(doc, page_idx):
    page = doc[page_idx]
    mat = fitz.Matrix(PAGE_ZOOM, PAGE_ZOOM)
    pix = page.get_pixmap(matrix=mat)
    return base64.b64encode(pix.tobytes("jpeg", jpg_quality=JPEG_QUALITY)).decode()


def assign_categories(name: str, hint: str = "") -> str:
    text = f"{name} {hint}".lower()
    matched = []
    for keywords, category in CATEGORY_MAP:
        if any(kw in text for kw in keywords):
            matched.append(category)
    if not matched:
        matched = ["Hardware"]
    all_cats = set(matched)
    for c in list(matched):
        parts = c.split(" > ")
        for i in range(1, len(parts)):
            all_cats.add(" > ".join(parts[:i]))
    all_cats.add("All Products")
    return ", ".join(sorted(all_cats))


def load_stock_sheet(path):
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    headers = rows[1]
    data = [dict(zip(headers, r)) for r in rows[2:] if any(r)]
    data = [r for r in data if r.get("Supplier SKU")]
    by_sku = {str(r["Supplier SKU"]).strip().upper(): r for r in data}
    in_wc  = {str(r["Supplier SKU"]).strip().upper() for r in data if r.get("WC ID")}
    log(f"  Stock sheet: {len(data)} products, {len(in_wc)} already in WooCommerce")
    return by_sku, in_wc


def extract_page(client, doc, page_idx, total):
    """Render one page and call Claude Vision. Returns (page_idx, list_of_products)."""
    b64 = page_to_b64(doc, page_idx)
    for attempt in range(3):
        try:
            resp = client.messages.create(
                model=VISION_MODEL,
                max_tokens=4096,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                        {"type": "text",  "text": VISION_PROMPT}
                    ]
                }]
            )
            raw = resp.content[0].text.strip()
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
            products = json.loads(raw)
            count = len(products)
            label = f"{products[0].get('name','')[:25]}..." if products else "(none)"
            log(f"  Page {page_idx+1:3d}/{total}: {count:3d} products — {label}")
            return page_idx, products
        except Exception as e:
            if attempt == 2:
                log(f"  Page {page_idx+1:3d}/{total}: ERROR after 3 tries — {e}")
                return page_idx, []
            time.sleep(4 * (attempt + 1))


def calc_price(product):
    """Price = catalogue retail price + VAT + markup.
    Falls back to wholesale price if no retail price shown.
    Stock sheet is NOT used for pricing."""
    rp = product.get("retail_price")
    if rp:
        try:
            return round(float(rp) * (1 + VAT_RATE) * MARKUP, 2)
        except (ValueError, TypeError):
            pass
    wp = product.get("wholesale_price")
    if wp:
        try:
            return round(float(wp) * (1 + VAT_RATE) * MARKUP, 2)
        except (ValueError, TypeError):
            pass
    return None


def generate_description(client, name, brand, hint, price):
    prompt = (f"Write a concise WooCommerce product description (2-3 sentences, max 80 words) for: "
              f"{name} by {brand}. Type: {hint}. Price: R{price}. "
              f"Focus on features and practical use. Plain text only.")
    for attempt in range(3):
        try:
            resp = client.messages.create(
                model=DESC_MODEL,
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}]
            )
            return resp.content[0].text.strip()
        except Exception as e:
            if attempt == 2:
                return f"{name} — quality product by {brand}. Available at SocialZip."
            time.sleep(3)


# ── Main ──────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--pdf",     default="/tmp/impulse_catalogue.pdf")
    p.add_argument("--stock",   default="suppliers/impulse/stock_sheet.xlsx")
    p.add_argument("--out",     default="stock_control")
    p.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    p.add_argument("--resume",  action="store_true")
    p.add_argument("--no-desc", action="store_true")
    return p.parse_args()


def main():
    args = parse_args()
    out_dir     = Path(args.out)
    base_dir    = Path(__file__).parent
    stock_path  = base_dir / args.stock
    extract_json = out_dir / "impulse_extract.json"
    out_csv      = out_dir / "impulse_new_products.csv"
    skipped_csv  = out_dir / "impulse_skipped.csv"
    out_dir.mkdir(exist_ok=True)

    client = anthropic.Anthropic()

    log("=== Loading stock sheet ===")
    by_sku, in_wc_skus = load_stock_sheet(stock_path)

    log("\n=== Opening PDF ===")
    doc = fitz.open(args.pdf)
    total = doc.page_count
    log(f"  {total} pages")

    # Load existing extractions if resuming
    extracted = {}
    if args.resume and extract_json.exists():
        with open(extract_json) as f:
            extracted = {int(k): v for k, v in json.load(f).items()}
        log(f"  Resuming: {len(extracted)}/{total} pages already extracted")

    todo = [i for i in range(total) if i not in extracted]
    log(f"\n=== Extracting {len(todo)} pages with {args.workers} parallel workers ===")

    save_lock = threading.Lock()

    def save_progress():
        with save_lock:
            with open(extract_json, "w") as f:
                json.dump({str(k): v for k, v in extracted.items()}, f)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(extract_page, client, doc, idx, total): idx for idx in todo}
        for future in as_completed(futures):
            page_idx, products = future.result()
            extracted[page_idx] = products
            save_progress()

    # ── Deduplicate ───────────────────────────────────────────────────────────
    log("\n=== Deduplicating ===")
    seen_keys = set()
    all_products = []
    for page_products in extracted.values():
        for p in page_products:
            sku  = str(p.get("sku") or "").strip().upper()
            name = str(p.get("name") or "").strip().upper()
            if not name:
                continue
            key = sku if sku else name
            if key in seen_keys:
                continue
            seen_keys.add(key)
            all_products.append(p)

    log(f"  Unique catalogue products: {len(all_products)}")

    # ── Split new vs already in WC ────────────────────────────────────────────
    new_products = []
    skipped      = []

    for p in all_products:
        sku = str(p.get("sku") or "").strip().upper()
        if sku and sku in in_wc_skus:
            skipped.append(p)
            continue
        if not sku:
            name = p.get("name", "").upper()
            match = next((s for s in by_sku
                          if by_sku[s].get("Product Name", "").upper() == name), None)
            if match and match in in_wc_skus:
                skipped.append(p)
                continue
        new_products.append(p)

    log(f"  Already in WooCommerce (skipped): {len(skipped)}")
    log(f"  New products to add:              {len(new_products)}")

    with open(skipped_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["sku","name","wholesale_price","retail_price","brand"])
        w.writeheader()
        for p in skipped:
            w.writerow({k: p.get(k,"") for k in ["sku","name","wholesale_price","retail_price","brand"]})

    # ── Build WC import CSV ───────────────────────────────────────────────────
    log(f"\n=== Building WooCommerce import ({'with' if not args.no_desc else 'without'} descriptions) ===")
    wc_rows = []

    for i, product in enumerate(new_products, 1):
        raw_sku   = str(product.get("sku") or "").strip()
        name      = product.get("name", "").strip()
        brand     = product.get("brand") or "Impulse"
        hint      = product.get("category_hint") or ""

        stock_row = by_sku.get(raw_sku.upper()) if raw_sku else None
        if not raw_sku and stock_row:
            raw_sku = stock_row.get("Supplier SKU", "") or ""
        sku = raw_sku or f"IMP-{i:04d}"

        price = calc_price(product)

        qty = ""
        if stock_row:
            q = stock_row.get("Qty On Hand")
            if q is not None and str(q).strip() not in ("", "None"):
                try:
                    qty = int(float(str(q)))
                except (ValueError, TypeError):
                    pass

        categories = assign_categories(name, hint)

        desc = short_desc = ""
        if not args.no_desc:
            desc = generate_description(client, name, brand, hint, price or "")
            short_desc = desc[:160]
            log(f"  [{i:4d}/{len(new_products)}] {name[:50]}")

        row = {col: "" for col in WC_COLUMNS}
        row.update({
            "Type":                     "simple",
            "SKU":                      sku,
            "Name":                     name,
            "Published":                "1",
            "Visibility in catalog":    "visible",
            "Short description":        short_desc,
            "Description":              desc,
            "Tax status":               "taxable",
            "In stock?":                "1",
            "Stock":                    str(qty) if qty != "" else "10",
            "Low stock amount":         "3",
            "Backorders allowed?":      "0",
            "Sold individually?":       "0",
            "Allow customer reviews?":  "1",
            "Regular price":            str(price) if price else "",
            "Categories":               categories,
            "Brands":                   brand,
            "Meta: _yoast_wpseo_metadesc": short_desc[:155],
        })
        wc_rows.append(row)

    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=WC_COLUMNS)
        w.writeheader()
        w.writerows(wc_rows)

    log(f"\n{'='*60}")
    log(f"DONE")
    log(f"  Catalogue products:        {len(all_products)}")
    log(f"  Already in WooCommerce:    {len(skipped)}")
    log(f"  New products in CSV:       {len(new_products)}")
    log(f"\n  {out_csv}")
    log(f"  {skipped_csv}")
    log(f"{'='*60}")


if __name__ == "__main__":
    main()
