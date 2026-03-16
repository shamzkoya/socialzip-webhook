#!/usr/bin/env python3
"""
============================================================
Impulse Catalogue PDF → WooCommerce Sync
============================================================
1. Renders each page of the Impulse catalogue PDF as an image
2. Uses Claude Vision to extract every product (name, SKU, price)
3. Matches against impulse/stock_sheet.xlsx for qty and cost price
4. Skips products already in WooCommerce (have WC ID in stock sheet)
5. Generates full WC import CSV with descriptions + categories

USAGE:
  python3 impulse_catalogue_sync.py \
    --pdf     /tmp/impulse_catalogue.pdf \
    --stock   suppliers/impulse/stock_sheet.xlsx \
    --out     stock_control/ \
    [--resume]

OUTPUTS:
  stock_control/impulse_new_products.csv   — Import into WC
  stock_control/impulse_extract.json       — Raw extraction (for --resume)
  stock_control/impulse_skipped.csv        — Already in WC (skipped)
============================================================
"""

import os, re, csv, sys, json, base64, argparse, time
from pathlib import Path

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
    print("ERROR: pip3 install anthropic"); sys.exit(1)

try:
    import fitz  # PyMuPDF
except ImportError:
    print("ERROR: pip3 install PyMuPDF"); sys.exit(1)

try:
    import openpyxl
except ImportError:
    print("ERROR: pip3 install openpyxl"); sys.exit(1)

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

try:
    from duckduckgo_search import DDGS
    DDG_AVAILABLE = True
except ImportError:
    DDG_AVAILABLE = False

# ── Config ────────────────────────────────────────────────────────────────────
CLAUDE_MODEL  = "claude-haiku-4-5-20251001"
VISION_MODEL  = "claude-opus-4-6"       # Vision for page extraction
MAX_RETRIES   = 3
RETRY_DELAY   = 6
VAT_RATE      = 0.15
MARKUP        = 1.40      # 40% margin over cost incl VAT
SUPPLIER      = "Impulse Imports"
PAGE_ZOOM     = 2.0       # render quality

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}

# WooCommerce category mapping by keyword
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
    (["battery", "charger", "inverter", "ups"],       "Hardware > Electrical > Loadshedding"),
    (["lock", "padlock", "chain", "hinge"],           "Hardware > Locks & Security"),
    (["screw", "nail", "bolt", "nut", "washer", "anchor"],
                                                      "Hardware > Fasteners"),
    (["sealant", "adhesive", "glue", "silicone"],     "Hardware > Adhesives & Sealants"),
    (["sandpaper", "abrasive", "sanding"],            "Hardware > Abrasives"),
    (["edging strip", "corner", "tile", "grout"],     "Hardware > Building Materials"),
    (["broom", "mop", "bucket", "dustpan", "cleaning"],
                                                      "Homeware > Cleaning & Organization"),
    (["flask", "vacuum flask", "thermos"],            "Homeware > Kitchen"),
    (["peg", "clothespeg", "laundry"],                "Homeware > Cleaning & Organization > Laundry"),
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


# ── Helpers ───────────────────────────────────────────────────────────────────

def retry(fn, *args, retries=MAX_RETRIES, delay=RETRY_DELAY, **kwargs):
    for i in range(retries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            if i == retries - 1:
                raise
            print(f"    Retry {i+1}/{retries} after error: {e}")
            time.sleep(delay * (i + 1))


def page_to_b64(doc, page_idx, zoom=PAGE_ZOOM):
    page = doc[page_idx]
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat)
    return base64.b64encode(pix.tobytes("jpeg", jpg_quality=85)).decode()


def assign_categories(name: str) -> str:
    name_lower = name.lower()
    matched = []
    for keywords, category in CATEGORY_MAP:
        if any(kw in name_lower for kw in keywords):
            matched.append(category)
    if not matched:
        matched = ["Hardware"]
    # Add parent categories
    all_cats = set(matched)
    for c in list(matched):
        parts = c.split(" > ")
        for i in range(1, len(parts)):
            all_cats.add(" > ".join(parts[:i]))
    all_cats.add("All Products")
    # Price tier
    return ", ".join(sorted(all_cats))


def load_stock_sheet(path):
    """Returns two dicts: by_sku (Supplier SKU → row), in_wc set of WC IDs."""
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    headers = rows[1]
    data = [dict(zip(headers, r)) for r in rows[2:] if any(r)]
    data = [r for r in data if r.get("Supplier SKU")]

    by_sku = {str(r["Supplier SKU"]).strip().upper(): r for r in data}
    in_wc_skus = {str(r["Supplier SKU"]).strip().upper()
                  for r in data if r.get("WC ID")}
    print(f"  Stock sheet: {len(data)} products, {len(in_wc_skus)} already in WooCommerce")
    return by_sku, in_wc_skus


def extract_products_from_page(client, page_b64, page_num):
    """Call Claude Vision to extract all products visible on a catalogue page."""
    prompt = """You are extracting product data from an Impulse Hardware & Homeware catalogue page.

For EVERY product shown on this page, extract:
- sku: the product code/SKU (often shown as small text like F00123, or a code on the product tag)
- name: full product name as shown
- wholesale_price: the smaller/lower price (labelled "wholesale", "trade", or shown as the lower price)
- retail_price: the recommended retail price (RRP, shown as the higher price)
- brand: brand name if shown (e.g. ALCO, CRAFTSMAN, DIY, etc.)
- category_hint: brief category description (e.g. "hand tool", "cleaning", "electrical")

Rules:
- If you can't find a SKU code, set sku to null
- Prices may show as "R12.50" or just "12.50" — extract as number only
- If only one price shown, put it in retail_price
- Skip section headers, banners, and decorative elements — only real products
- Be thorough: extract EVERY product on the page, even small ones

Return ONLY a JSON array, no explanation:
[{"sku": "F00123", "name": "Product Name", "wholesale_price": 12.50, "retail_price": 18.00, "brand": "ALCO", "category_hint": "hand tool"}, ...]

If no products on this page (cover, contents, etc.), return: []"""

    def call_api():
        resp = client.messages.create(
            model=VISION_MODEL,
            max_tokens=4096,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": page_b64}},
                    {"type": "text", "text": prompt}
                ]
            }]
        )
        raw = resp.content[0].text.strip()
        # Strip markdown code fences if present
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        return json.loads(raw)

    try:
        return retry(call_api)
    except Exception as e:
        print(f"    Page {page_num} extraction failed: {e}")
        return []


def generate_description(client, product):
    """Generate a short SEO description for a product."""
    name = product.get("name", "")
    brand = product.get("brand", "") or ""
    hint = product.get("category_hint", "") or ""
    price = product.get("selling_price", "")

    prompt = f"""Write a concise WooCommerce product description (2-3 sentences, max 80 words) for:
Product: {name}
Brand: {brand}
Type: {hint}
Price: R{price}

Focus on key features, practical use, and quality. No fluff, no marketing clichés. Plain text only."""

    def call():
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}]
        )
        return resp.content[0].text.strip()

    try:
        return retry(call)
    except Exception as e:
        return f"{name} by {brand}. Quality product available at SocialZip."


def find_image(name, brand=""):
    """Search for a product image URL."""
    if not REQUESTS_AVAILABLE or not DDG_AVAILABLE:
        return ""
    query = f"{brand} {name} product image site:takealot.com OR site:amazon.com".strip()
    try:
        ddgs = DDGS()
        results = list(ddgs.images(query, max_results=3))
        for r in results:
            url = r.get("image", "")
            if url and url.startswith("http") and any(ext in url.lower() for ext in [".jpg", ".jpeg", ".png", ".webp"]):
                # Quick check URL is reachable
                try:
                    resp = requests.head(url, timeout=5, headers=HEADERS, allow_redirects=True)
                    if resp.status_code == 200:
                        return url
                except Exception:
                    pass
    except Exception:
        pass
    return ""


def calc_selling_price(stock_row, catalogue_product):
    """Work out the selling price from available data."""
    # Priority: stock sheet selling price → wholesale × markup → retail price
    if stock_row:
        sp = stock_row.get("Selling Price (R)")
        if sp and str(sp).strip() not in ("", "None"):
            try:
                return round(float(str(sp).replace(",", ".")), 2)
            except ValueError:
                pass
        cost_ex = stock_row.get("Cost Price (ex VAT)")
        if cost_ex and str(cost_ex).strip() not in ("", "None"):
            try:
                cost_incl = float(str(cost_ex).replace(",", ".")) * (1 + VAT_RATE)
                return round(cost_incl * MARKUP, 2)
            except ValueError:
                pass

    # Fall back to catalogue wholesale price × markup
    wp = catalogue_product.get("wholesale_price")
    if wp:
        try:
            return round(float(wp) * MARKUP, 2)
        except (ValueError, TypeError):
            pass

    # Last resort: use retail price as-is
    rp = catalogue_product.get("retail_price")
    if rp:
        try:
            return round(float(rp), 2)
        except (ValueError, TypeError):
            pass
    return None


# ── Main ──────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--pdf",    default="/tmp/impulse_catalogue.pdf")
    p.add_argument("--stock",  default="suppliers/impulse/stock_sheet.xlsx")
    p.add_argument("--out",    default="stock_control")
    p.add_argument("--resume", action="store_true", help="Skip pages already in extract JSON")
    p.add_argument("--no-images", action="store_true", help="Skip image searching (faster)")
    p.add_argument("--no-desc",   action="store_true", help="Skip AI descriptions (faster)")
    return p.parse_args()


def main():
    args = parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(exist_ok=True)

    extract_json = out_dir / "impulse_extract.json"
    out_csv      = out_dir / "impulse_new_products.csv"
    skipped_csv  = out_dir / "impulse_skipped.csv"

    client = anthropic.Anthropic()

    print("=== Loading stock sheet ===")
    stock_dir = Path(args.stock)
    if not stock_dir.is_absolute():
        stock_dir = Path(__file__).parent / args.stock
    by_sku, in_wc_skus = load_stock_sheet(stock_dir)

    print("\n=== Opening PDF ===")
    doc = fitz.open(args.pdf)
    total_pages = doc.page_count
    print(f"  {total_pages} pages")

    # Load existing extraction if resuming
    extracted = {}  # page_idx → list of products
    if args.resume and extract_json.exists():
        with open(extract_json) as f:
            raw = json.load(f)
        extracted = {int(k): v for k, v in raw.items()}
        print(f"  Resuming: {len(extracted)} pages already extracted")

    print(f"\n=== Extracting products from {total_pages} pages ===")
    for page_idx in range(total_pages):
        if page_idx in extracted:
            continue

        b64 = page_to_b64(doc, page_idx)
        products = extract_products_from_page(client, b64, page_idx + 1)
        extracted[page_idx] = products

        # Save progress after each page
        with open(extract_json, "w") as f:
            json.dump({str(k): v for k, v in extracted.items()}, f, indent=2)

        count = len(products)
        if count:
            names = [p.get("name", "?")[:30] for p in products[:3]]
            print(f"  Page {page_idx+1:3d}/{total_pages}: {count} products — {', '.join(names)}{'...' if count > 3 else ''}")
        else:
            print(f"  Page {page_idx+1:3d}/{total_pages}: (no products)")

        time.sleep(0.3)  # small rate limit buffer

    # Deduplicate products by SKU or name
    print("\n=== Deduplicating extracted products ===")
    all_products = []
    seen_skus  = set()
    seen_names = set()

    for page_products in extracted.values():
        for p in page_products:
            sku  = str(p.get("sku") or "").strip().upper()
            name = str(p.get("name") or "").strip().upper()
            if not name:
                continue
            key = sku if sku else name
            if key in seen_skus or (not sku and name in seen_names):
                continue
            seen_skus.add(key)
            seen_names.add(name)
            all_products.append(p)

    print(f"  Total unique products in catalogue: {len(all_products)}")

    # Split new vs already in WC
    new_products     = []
    skipped_products = []

    for p in all_products:
        sku = str(p.get("sku") or "").strip().upper()
        if sku and sku in in_wc_skus:
            skipped_products.append(p)
        else:
            # Also check stock sheet for name match if no SKU
            if not sku:
                name = p.get("name", "").upper()
                matched_sku = next(
                    (s for s in by_sku if by_sku[s].get("Product Name", "").upper() == name), None
                )
                if matched_sku and matched_sku in in_wc_skus:
                    skipped_products.append(p)
                    continue
            new_products.append(p)

    print(f"  Already in WooCommerce (skipped): {len(skipped_products)}")
    print(f"  New products to add:              {len(new_products)}")

    # Write skipped CSV
    with open(skipped_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["sku", "name", "wholesale_price", "retail_price", "brand"])
        w.writeheader()
        for p in skipped_products:
            w.writerow({k: p.get(k, "") for k in ["sku", "name", "wholesale_price", "retail_price", "brand"]})

    # Build WC import rows
    print(f"\n=== Building WooCommerce import for {len(new_products)} new products ===")
    wc_rows = []

    for i, product in enumerate(new_products, 1):
        raw_sku  = str(product.get("sku") or "").strip()
        name     = product.get("name", "").strip()
        brand    = product.get("brand") or "Impulse"
        hint     = product.get("category_hint") or ""

        # Look up in stock sheet
        stock_key = raw_sku.upper() if raw_sku else None
        stock_row = by_sku.get(stock_key) if stock_key else None

        # Derive final SKU
        if not raw_sku and stock_row:
            raw_sku = stock_row.get("Supplier SKU", "") or ""
        sku = raw_sku if raw_sku else f"IMP-{i:04d}"

        # Selling price
        product["selling_price"] = calc_selling_price(stock_row, product)
        price = product["selling_price"]

        # Stock quantity
        qty = ""
        if stock_row:
            q = stock_row.get("Qty On Hand")
            if q is not None and str(q).strip() not in ("", "None"):
                try:
                    qty = int(float(str(q)))
                except (ValueError, TypeError):
                    qty = ""

        # Categories
        categories = assign_categories(f"{name} {hint}")

        # Description
        desc = short_desc = ""
        if not args.no_desc:
            print(f"  [{i}/{len(new_products)}] {name[:50]}")
            desc = generate_description(client, product)
            short_desc = desc[:160] if desc else ""
        else:
            print(f"  [{i}/{len(new_products)}] {name[:50]} (no desc)")

        # Image
        image = ""
        if not args.no_images and brand and name:
            image = find_image(name, brand)

        row = {col: "" for col in WC_COLUMNS}
        row.update({
            "Type":                     "simple",
            "SKU":                      sku,
            "Name":                     name,
            "Published":                "1",
            "Is featured?":             "0",
            "Visibility in catalog":    "visible",
            "Short description":        short_desc,
            "Description":              desc,
            "Tax status":               "taxable",
            "Tax class":                "",
            "In stock?":                "1" if qty else "1",
            "Stock":                    str(qty) if qty != "" else "10",
            "Low stock amount":         "3",
            "Backorders allowed?":      "0",
            "Sold individually?":       "0",
            "Allow customer reviews?":  "1",
            "Regular price":            str(price) if price else "",
            "Categories":               categories,
            "Images":                   image,
            "Brands":                   brand,
            "Meta: _yoast_wpseo_metadesc": short_desc[:155] if short_desc else "",
        })
        wc_rows.append(row)

    # Write WC import CSV
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=WC_COLUMNS)
        w.writeheader()
        w.writerows(wc_rows)

    print(f"\n{'='*60}")
    print(f"DONE")
    print(f"  Catalogue products found:  {len(all_products)}")
    print(f"  Already in WooCommerce:    {len(skipped_products)}")
    print(f"  New products to import:    {len(new_products)}")
    print(f"\nOutput files:")
    print(f"  {out_csv}     ← import this into WooCommerce")
    print(f"  {skipped_csv}")
    print(f"  {extract_json}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
