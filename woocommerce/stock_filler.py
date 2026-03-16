#!/usr/bin/env python3
"""
============================================================
Stock Filler — Complete missing product data across suppliers
============================================================
Reads master_stock.csv, finds every incomplete product, then
fills in missing price / description / image / stock from:

  • Impulse   — stock_sheet.xlsx (price + qty) + Claude AI (desc)
  • Hirschs   — hirsch_supplier_feb_mar_2026.csv (price) +
                hirschs.co.za scraper (desc + image)
  • Makokoya  — makokoyahome.co.za scraper (price + desc + image)
                → falls back to Claude AI desc if site blocks

Outputs:
  stock_control/stock_filled.csv        — updated master stock
  stock_control/wc_import_filled.csv    — ready for WC Products > Import
  stock_control/fill_report.txt         — summary

USAGE:
  python3 stock_filler.py \
    --wc-export  /path/to/wc_export.csv \
    --hirsch-csv woocommerce/suppliers/hirschs/hirsch_supplier_feb_mar_2026.csv \
    --impulse-xl woocommerce/suppliers/impulse/stock_sheet.xlsx \
    --out        woocommerce/stock_control
============================================================
"""

import os, re, csv, sys, json, time, argparse
from pathlib import Path

# ── Optional deps ─────────────────────────────────────────────────────────────
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
    CLAUDE_AVAILABLE = True
except ImportError:
    CLAUDE_AVAILABLE = False
    print("WARN: anthropic not installed — Claude AI descriptions disabled")

try:
    import requests
    from requests.auth import HTTPBasicAuth
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
    print("WARN: requests not installed — web scraping disabled")

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False
    print("WARN: beautifulsoup4 not installed — HTML parsing disabled")

try:
    import openpyxl
    OPENPYXL_AVAILABLE = True
except ImportError:
    OPENPYXL_AVAILABLE = False
    print("WARN: openpyxl not installed — impulse xlsx loading disabled")

try:
    from duckduckgo_search import DDGS
    DDG_AVAILABLE = True
except ImportError:
    DDG_AVAILABLE = False

# ── Config ────────────────────────────────────────────────────────────────────
CLAUDE_MODEL   = "claude-haiku-4-5-20251001"
MAX_RETRIES    = 3
RETRY_DELAY    = 5
VAT_RATE       = 0.15
DEFAULT_MARKUP = 1.40   # Hirsch: 40% over supplier cost

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-ZA,en;q=0.9",
}

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
    "Images",
    "Upsells", "Cross-sells",
    "External URL", "Button text", "Position",
    "Attribute 1 name", "Attribute 1 value(s)", "Attribute 1 visible", "Attribute 1 global",
    "Attribute 2 name", "Attribute 2 value(s)", "Attribute 2 visible", "Attribute 2 global",
    "Meta: title", "Meta: description", "Meta: keywords",
    "_yoast_wpseo_title", "_yoast_wpseo_metadesc", "_yoast_wpseo_focuskw",
    "Brands",
]


# ══════════════════════════════════════════════════════════════════════════════
# LOADERS
# ══════════════════════════════════════════════════════════════════════════════

def load_master_stock(path):
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def load_wc_export(path):
    """Returns dict: SKU (upper) → full WC row."""
    if not path or not Path(path).exists():
        return {}
    out = {}
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            sku = row.get("SKU", "").strip().upper()
            if sku:
                out[sku] = row
    print(f"  Loaded {len(out)} products from WC export")
    return out


def load_hirsch_csv(path):
    """Returns dict: normalised_key → row with Brand/Model/SN-SKU/Sale Price."""
    if not path or not Path(path).exists():
        return {}
    out = {}
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            sku   = row.get("SN/SKU", "").strip().upper()
            model = row.get("Model",  "").strip().upper()
            for key in filter(None, [sku, model]):
                out[key] = row
    print(f"  Loaded {len(out)} Hirsch catalogue entries")
    return out


def load_impulse_xlsx(path):
    """
    Returns dict: WC_SKU (upper) → {selling_price, cost_ex_vat, qty, description, image_url, ...}
    """
    if not path or not Path(path).exists() or not OPENPYXL_AVAILABLE:
        return {}
    wb   = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws   = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    # Row 0 = title banner, Row 1 = headers
    headers = rows[1]
    out = {}
    for r in rows[2:]:
        d = dict(zip(headers, r))
        wc_sku = str(d.get("WC SKU") or d.get("Supplier SKU") or "").strip().upper()
        if not wc_sku:
            continue

        # Selling price — skip formula strings
        sell = d.get("Selling Price (R)")
        if sell and not str(sell).startswith("="):
            try:
                sell = float(sell)
            except (ValueError, TypeError):
                sell = None
        else:
            sell = None

        cost_ex = d.get("Cost Price (ex VAT)")
        try:
            cost_ex = float(cost_ex) if cost_ex and not str(cost_ex).startswith("=") else None
        except (ValueError, TypeError):
            cost_ex = None

        qty = d.get("Qty On Hand")
        try:
            qty = int(float(qty)) if qty and not str(qty).startswith("=") else None
        except (ValueError, TypeError):
            qty = None

        out[wc_sku] = {
            "supplier_sku":  str(d.get("Supplier SKU") or "").strip(),
            "wc_id":         str(d.get("WC ID") or "").strip(),
            "barcode":       str(d.get("Barcode / EAN") or "").strip(),
            "name":          str(d.get("Product Name") or "").strip(),
            "brand":         str(d.get("Brand") or "").strip(),
            "category":      str(d.get("Category") or "").strip(),
            "description":   str(d.get("Description") or "").strip(),
            "cost_ex_vat":   cost_ex,
            "selling_price": sell,
            "qty":           qty,
            "image_url":     str(d.get("Image URL") or "").strip(),
            "notes":         str(d.get("Notes") or "").strip(),
        }
    print(f"  Loaded {len(out)} Impulse stock entries")
    return out


# ══════════════════════════════════════════════════════════════════════════════
# SCRAPERS
# ══════════════════════════════════════════════════════════════════════════════

def safe_get(url, timeout=12):
    """GET with retries and browser headers. Returns response or None."""
    if not REQUESTS_AVAILABLE:
        return None
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            if r.status_code == 200:
                return r
            if r.status_code in (403, 429):
                time.sleep(RETRY_DELAY * (attempt + 1))
        except Exception:
            time.sleep(RETRY_DELAY)
    return None


def parse_price_zar(text):
    """Extract first ZAR price from text string."""
    text = str(text).replace("\xa0", " ").replace(",", "")
    matches = re.findall(r'R\s*(\d+(?:\.\d+)?)', text)
    if not matches:
        matches = re.findall(r'(\d{3,7}(?:\.\d{1,2})?)', text)
    for m in matches:
        try:
            v = float(m)
            if 10 < v < 2_000_000:
                return v
        except ValueError:
            pass
    return None


# ── Hirsch scraper (Magento) ──────────────────────────────────────────────────

def scrape_hirsch_product(brand, model, name):
    """
    Search hirschs.co.za for a product by model number.
    Returns dict with keys: description, image_url, price, product_url
    """
    result = {"description": "", "image_url": "", "price": None, "product_url": ""}
    if not REQUESTS_AVAILABLE or not BS4_AVAILABLE:
        return result

    query  = model or f"{brand} {name}"
    search = f"https://www.hirschs.co.za/catalogsearch/result/?q={requests.utils.quote(query)}"
    r = safe_get(search)
    if not r:
        return result

    soup = BeautifulSoup(r.text, "html.parser")

    # Find first product link in search results
    product_url = ""
    for a in soup.select("a.product-item-link, .product-item-info a"):
        href = a.get("href", "")
        if href and "hirschs.co.za" in href and "/catalogsearch" not in href:
            product_url = href
            break
    if not product_url:
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            if "hirschs.co.za" in href and "/catalog/product/view" in href:
                product_url = href
                break

    result["product_url"] = product_url
    if not product_url:
        return result

    # Fetch product page
    rp = safe_get(product_url)
    if not rp:
        return result

    psoup = BeautifulSoup(rp.text, "html.parser")

    # Description
    for sel in [".product.attribute.description .value",
                "#description .value",
                "[data-role='content'] .value",
                ".product-info-main .overview"]:
        el = psoup.select_one(sel)
        if el:
            result["description"] = el.get_text(" ", strip=True)[:2000]
            break

    # Price
    for sel in [".price-wrapper .price", ".price-box .price"]:
        el = psoup.select_one(sel)
        if el:
            p = parse_price_zar(el.get_text())
            if p:
                result["price"] = p
                break

    # Image
    for sel in [".gallery-placeholder img", ".fotorama__img", ".product.media img"]:
        el = psoup.select_one(sel)
        if el:
            src = el.get("src") or el.get("data-src") or ""
            if src and "placeholder" not in src:
                result["image_url"] = src
                break

    # Try JSON-LD as fallback
    for script in psoup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, dict) and data.get("@type") in ("Product",):
                if not result["description"] and data.get("description"):
                    result["description"] = data["description"][:2000]
                if not result["image_url"] and data.get("image"):
                    img = data["image"]
                    result["image_url"] = img[0] if isinstance(img, list) else img
                if not result["price"]:
                    offers = data.get("offers", {})
                    if isinstance(offers, dict):
                        p = parse_price_zar(str(offers.get("price", "")))
                        if p:
                            result["price"] = p
        except Exception:
            pass

    return result


# ── Makokoya scraper ──────────────────────────────────────────────────────────

def scrape_makokoya_product(name, sku=""):
    """
    Search makokoyahome.co.za for a product by name.
    Returns dict with keys: description, image_url, price, product_url
    """
    result = {"description": "", "image_url": "", "price": None, "product_url": ""}
    if not REQUESTS_AVAILABLE or not BS4_AVAILABLE:
        return result

    query  = name
    # Try WooCommerce-style search (common for SA homewares sites)
    for search_url in [
        f"https://makokoyahome.co.za/?s={requests.utils.quote(query)}&post_type=product",
        f"https://makokoyahome.co.za/shop/?s={requests.utils.quote(query)}",
        f"https://www.makokoyahome.co.za/?s={requests.utils.quote(query)}&post_type=product",
    ]:
        r = safe_get(search_url)
        if r:
            break
    else:
        return result

    soup = BeautifulSoup(r.text, "html.parser")

    # Find first product link
    product_url = ""
    for a in soup.select("a.woocommerce-loop-product__link, .product a.product, ul.products li a"):
        href = a.get("href", "")
        if href and ("makokoya" in href or href.startswith("/")):
            product_url = href if href.startswith("http") else f"https://makokoyahome.co.za{href}"
            break

    result["product_url"] = product_url
    if not product_url:
        return result

    # Fetch product page
    rp = safe_get(product_url)
    if not rp:
        return result

    psoup = BeautifulSoup(rp.text, "html.parser")

    # Price (WooCommerce standard)
    for sel in [".woocommerce-Price-amount", ".price .amount", "p.price"]:
        el = psoup.select_one(sel)
        if el:
            p = parse_price_zar(el.get_text())
            if p:
                result["price"] = p
                break

    # Description
    for sel in ["#tab-description .woocommerce-Tabs-panel",
                ".woocommerce-product-details__short-description",
                "[itemprop='description']",
                ".entry-content"]:
        el = psoup.select_one(sel)
        if el:
            result["description"] = el.get_text(" ", strip=True)[:2000]
            break

    # Image (WooCommerce standard)
    for sel in [".woocommerce-product-gallery__image img",
                ".wp-post-image",
                "figure.woocommerce-product-gallery__wrapper img"]:
        el = psoup.select_one(sel)
        if el:
            src = el.get("src") or el.get("data-src") or el.get("data-large_image") or ""
            if src and "placeholder" not in src:
                result["image_url"] = src
                break

    # JSON-LD fallback
    for script in psoup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, dict) and data.get("@type") == "Product":
                if not result["description"] and data.get("description"):
                    result["description"] = data["description"][:2000]
                if not result["image_url"] and data.get("image"):
                    img = data["image"]
                    result["image_url"] = img[0] if isinstance(img, list) else img
                if not result["price"]:
                    offers = data.get("offers", {})
                    if isinstance(offers, dict):
                        p = parse_price_zar(str(offers.get("price", "")))
                        if p:
                            result["price"] = p
        except Exception:
            pass

    return result


# ── DuckDuckGo image fallback ─────────────────────────────────────────────────

def find_image_ddg(query):
    if not DDG_AVAILABLE:
        return ""
    try:
        with DDGS() as ddgs:
            results = list(ddgs.images(query + " product", max_results=3))
        for r in results:
            url = r.get("image", "")
            if url and url.startswith("http"):
                return url
    except Exception:
        pass
    return ""


# ══════════════════════════════════════════════════════════════════════════════
# CLAUDE AI — description generation
# ══════════════════════════════════════════════════════════════════════════════

def claude_generate_description(client, product_name, brand="", category="", price=None, supplier=""):
    prompt = f"""Write a product description for a South African e-commerce store (Happy Harvesting).

Product: {product_name}
{f'Brand: {brand}' if brand else ''}
{f'Category: {category}' if category else ''}
{f'Price: R{price:,.2f}' if price else ''}
{f'Supplier: {supplier}' if supplier else ''}

Return ONLY a JSON object — no markdown:
{{
  "short_description": "2-3 sentence overview (max 120 words)",
  "description": "Full HTML description with <p> tags, <ul> features list (max 300 words)",
  "meta_title": "SEO title (max 60 chars)",
  "meta_description": "SEO meta description (max 155 chars)",
  "focus_keyword": "primary keyword phrase"
}}"""

    for attempt in range(MAX_RETRIES):
        try:
            msg = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=600,
                messages=[{"role": "user", "content": prompt}]
            )
            raw = msg.content[0].text.strip()
            # Strip markdown fences
            raw = re.sub(r'^```(?:json)?\s*', '', raw)
            raw = re.sub(r'\s*```$', '', raw)
            return json.loads(raw)
        except Exception:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
    return {}


# ══════════════════════════════════════════════════════════════════════════════
# WC ROW BUILDER
# ══════════════════════════════════════════════════════════════════════════════

def build_wc_row(stock_row, wc_row, price, description, short_desc, image_url,
                 qty=None, meta=None, brands=""):
    meta = meta or {}
    return {
        "ID":                       wc_row.get("ID", stock_row.get("wc_id", "")),
        "Type":                     wc_row.get("Type", stock_row.get("type", "simple")),
        "SKU":                      stock_row.get("sku", ""),
        "Name":                     wc_row.get("Name", stock_row.get("name", "")),
        "Published":                wc_row.get("Published", "1" if stock_row.get("published") == "True" else "0"),
        "Is featured?":             wc_row.get("Is featured?", "0"),
        "Visibility in catalog":    wc_row.get("Visibility in catalog", "visible"),
        "Short description":        short_desc or wc_row.get("Short description", ""),
        "Description":              description or wc_row.get("Description", ""),
        "Date sale price starts":   "",
        "Date sale price ends":     "",
        "Tax status":               wc_row.get("Tax status", "taxable"),
        "Tax class":                wc_row.get("Tax class", ""),
        "In stock?":                "1" if (qty is None or qty > 0) else "0",
        "Stock":                    str(qty) if qty is not None else wc_row.get("Stock", ""),
        "Low stock amount":         wc_row.get("Low stock amount", ""),
        "Backorders allowed?":      wc_row.get("Backorders allowed?", "0"),
        "Sold individually?":       wc_row.get("Sold individually?", "0"),
        "Weight (kg)":              wc_row.get("Weight (kg)", ""),
        "Length (cm)":              wc_row.get("Length (cm)", ""),
        "Width (cm)":               wc_row.get("Width (cm)", ""),
        "Height (cm)":              wc_row.get("Height (cm)", ""),
        "Allow customer reviews?":  wc_row.get("Allow customer reviews?", "1"),
        "Purchase note":            "",
        "Sale price":               wc_row.get("Sale price", ""),
        "Regular price":            str(price) if price else wc_row.get("Regular price", ""),
        "Categories":               wc_row.get("Categories", stock_row.get("categories", "")),
        "Tags":                     wc_row.get("Tags", ""),
        "Shipping class":           wc_row.get("Shipping class", ""),
        "Images":                   image_url or wc_row.get("Images", ""),
        "Upsells":                  "",
        "Cross-sells":              "",
        "External URL":             "",
        "Button text":              "",
        "Position":                 "",
        "Attribute 1 name":         wc_row.get("Attribute 1 name", ""),
        "Attribute 1 value(s)":     wc_row.get("Attribute 1 value(s)", ""),
        "Attribute 1 visible":      wc_row.get("Attribute 1 visible", ""),
        "Attribute 1 global":       wc_row.get("Attribute 1 global", ""),
        "Attribute 2 name":         wc_row.get("Attribute 2 name", ""),
        "Attribute 2 value(s)":     wc_row.get("Attribute 2 value(s)", ""),
        "Attribute 2 visible":      wc_row.get("Attribute 2 visible", ""),
        "Attribute 2 global":       wc_row.get("Attribute 2 global", ""),
        "Meta: title":              meta.get("meta_title", wc_row.get("Meta: title", "")),
        "Meta: description":        meta.get("meta_description", wc_row.get("Meta: description", "")),
        "Meta: keywords":           meta.get("focus_keyword", wc_row.get("Meta: keywords", "")),
        "_yoast_wpseo_title":       meta.get("meta_title", wc_row.get("_yoast_wpseo_title", "")),
        "_yoast_wpseo_metadesc":    meta.get("meta_description", wc_row.get("_yoast_wpseo_metadesc", "")),
        "_yoast_wpseo_focuskw":     meta.get("focus_keyword", wc_row.get("_yoast_wpseo_focuskw", "")),
        "Brands":                   brands or wc_row.get("Brands", ""),
    }


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Fill missing stock data from supplier sources")
    parser.add_argument("--master",      default="woocommerce/stock_control/master_stock.csv")
    parser.add_argument("--wc-export",   default="", help="WooCommerce product export CSV")
    parser.add_argument("--hirsch-csv",  default="woocommerce/suppliers/hirschs/hirsch_supplier_feb_mar_2026.csv")
    parser.add_argument("--impulse-xl",  default="woocommerce/suppliers/impulse/stock_sheet.xlsx")
    parser.add_argument("--out",         default="woocommerce/stock_control")
    parser.add_argument("--markup",      type=float, default=DEFAULT_MARKUP,
                        help="Price markup multiplier over Hirsch cost (default 1.40 = 40%%)")
    parser.add_argument("--suppliers",   default="impulse,hirschs,makokoya",
                        help="Comma-separated list of suppliers to process")
    parser.add_argument("--limit",       type=int, default=0,
                        help="Max products to process per supplier (0=all, for testing)")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    suppliers_to_run = [s.strip().lower() for s in args.suppliers.split(",")]

    # ── Load data ──────────────────────────────────────────────────────────────
    print("\n=== Loading data ===")
    master      = load_master_stock(args.master)
    wc_existing = load_wc_export(args.wc_export)
    hirsch_cat  = load_hirsch_csv(args.hirsch_csv)
    impulse_xl  = load_impulse_xlsx(args.impulse_xl)

    # Claude client
    client = None
    if CLAUDE_AVAILABLE:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if api_key:
            client = anthropic.Anthropic(api_key=api_key)
            print(f"  Claude AI enabled ({CLAUDE_MODEL})")
        else:
            print("  WARN: ANTHROPIC_API_KEY not set — Claude descriptions disabled")

    # ── Process each incomplete product ───────────────────────────────────────
    wc_rows      = []
    fill_log     = []
    skipped      = 0
    already_done = 0

    total_incomplete = [r for r in master if r.get("gaps") and r.get("supplier") in suppliers_to_run]
    print(f"\n=== Processing {len(total_incomplete)} incomplete products ===\n")

    by_supplier = {}
    for r in total_incomplete:
        sup = r.get("supplier", "unknown")
        by_supplier.setdefault(sup, []).append(r)

    for supplier in suppliers_to_run:
        products = by_supplier.get(supplier, [])
        if args.limit:
            products = products[:args.limit]
        if not products:
            print(f"\n--- {supplier.upper()}: no incomplete products ---")
            continue

        print(f"\n{'='*60}")
        print(f"  {supplier.upper()} — {len(products)} incomplete products")
        print(f"{'='*60}")

        for i, stock_row in enumerate(products, 1):
            sku      = stock_row.get("sku", "").strip()
            name     = stock_row.get("name", "").strip()
            gaps     = stock_row.get("gaps", "")
            wc_row   = wc_existing.get(sku.upper(), {})

            print(f"\n  [{i}/{len(products)}] {sku} — {name[:50]}")
            print(f"    Gaps: {gaps}")

            need_price = "no_price" in gaps
            need_desc  = "no_description" in gaps
            need_image = "no_image" in gaps

            price       = float(stock_row.get("regular_price") or 0) or None
            description = wc_row.get("Description", "").strip()
            short_desc  = wc_row.get("Short description", "").strip()
            image_url   = wc_row.get("Images", "").strip()
            qty         = None
            meta        = {}
            brands      = ""
            fill_actions = []

            # ── IMPULSE ───────────────────────────────────────────────────────
            if supplier == "impulse":
                xl = impulse_xl.get(sku.upper(), {})

                if need_price and xl.get("selling_price"):
                    price = xl["selling_price"]
                    fill_actions.append(f"price=R{price:.2f}(xlsx)")

                if xl.get("qty") is not None:
                    qty = xl["qty"]
                    fill_actions.append(f"qty={qty}(xlsx)")

                if xl.get("description") and xl["description"] not in ("None", ""):
                    description = xl["description"]
                    fill_actions.append("desc(xlsx)")

                if xl.get("image_url") and xl["image_url"] not in ("None", ""):
                    image_url = xl["image_url"]
                    fill_actions.append("image(xlsx)")

                # Generate description with Claude if still missing
                if need_desc and not description and client:
                    print(f"    Generating description with Claude...")
                    cat = xl.get("category") or stock_row.get("categories", "")
                    meta = claude_generate_description(
                        client, name, brand=xl.get("brand", ""),
                        category=cat, price=price, supplier="Impulse Imports"
                    )
                    description = meta.get("description", "")
                    short_desc  = meta.get("short_description", "")
                    if description:
                        fill_actions.append("desc(claude)")

                # Image fallback
                if need_image and not image_url:
                    img = find_image_ddg(f"{name} product south africa")
                    if img:
                        image_url = img
                        fill_actions.append("image(ddg)")

                brands = xl.get("brand", "") or "Impulse Imports"

            # ── HIRSCHS ───────────────────────────────────────────────────────
            elif supplier == "hirschs":
                hrow  = hirsch_cat.get(sku.upper(), {})
                model = hrow.get("Model", "").strip()
                brand = hrow.get("Brand", "").strip()

                if need_price:
                    cost_str = hrow.get("Sale Price", "")
                    try:
                        cost = float(str(cost_str).replace("R","").replace(",","").strip())
                        if cost > 0:
                            price = round(cost * args.markup, 2)
                            fill_actions.append(f"price=R{price:.2f}(csv×{args.markup})")
                    except (ValueError, TypeError):
                        pass

                if need_desc or need_image:
                    print(f"    Scraping hirschs.co.za for {brand} {model}...")
                    scraped = scrape_hirsch_product(brand, model, name)
                    time.sleep(0.8)

                    if need_desc and scraped.get("description"):
                        description = scraped["description"]
                        fill_actions.append("desc(hirschs.co.za)")
                    elif need_desc and client:
                        print(f"    Generating description with Claude...")
                        meta = claude_generate_description(
                            client, name, brand=brand, price=price, supplier="Hirschs"
                        )
                        description = meta.get("description", "")
                        short_desc  = meta.get("short_description", "")
                        if description:
                            fill_actions.append("desc(claude)")

                    if need_image and scraped.get("image_url"):
                        image_url = scraped["image_url"]
                        fill_actions.append("image(hirschs.co.za)")
                    elif need_image:
                        img = find_image_ddg(f"{brand} {model} {name}")
                        if img:
                            image_url = img
                            fill_actions.append("image(ddg)")

                    # Use scraped price if we still don't have one
                    if need_price and not price and scraped.get("price"):
                        price = scraped["price"]
                        fill_actions.append(f"price=R{price:.2f}(hirschs.co.za)")

                brands = brand or "Hirschs"

            # ── MAKOKOYA ──────────────────────────────────────────────────────
            elif supplier == "makokoya":
                print(f"    Scraping makokoyahome.co.za for '{name}'...")
                scraped = scrape_makokoya_product(name, sku)
                time.sleep(0.8)

                if need_price and scraped.get("price"):
                    price = scraped["price"]
                    fill_actions.append(f"price=R{price:.2f}(makokoya)")

                if need_desc and scraped.get("description"):
                    description = scraped["description"]
                    fill_actions.append("desc(makokoya)")
                elif need_desc and client:
                    print(f"    Generating description with Claude...")
                    cat = stock_row.get("categories", "Homeware")
                    meta = claude_generate_description(
                        client, name, category=cat, price=price, supplier="Makokoya"
                    )
                    description = meta.get("description", "")
                    short_desc  = meta.get("short_description", "")
                    if description:
                        fill_actions.append("desc(claude)")

                if need_image and scraped.get("image_url"):
                    image_url = scraped["image_url"]
                    fill_actions.append("image(makokoya)")
                elif need_image:
                    img = find_image_ddg(f"{name} home decor south africa")
                    if img:
                        image_url = img
                        fill_actions.append("image(ddg)")

                brands = "Makokoya"

            # ── Build WC import row ───────────────────────────────────────────
            if fill_actions:
                row = build_wc_row(
                    stock_row, wc_row,
                    price=price,
                    description=description,
                    short_desc=short_desc,
                    image_url=image_url,
                    qty=qty,
                    meta=meta,
                    brands=brands,
                )
                wc_rows.append(row)
                fill_log.append({
                    "supplier": supplier, "sku": sku, "name": name,
                    "actions": ", ".join(fill_actions),
                    "price": price or "", "has_desc": bool(description),
                    "has_image": bool(image_url),
                })
                print(f"    ✓ {', '.join(fill_actions)}")
            else:
                skipped += 1
                print(f"    — nothing changed")

    # ── Write outputs ──────────────────────────────────────────────────────────
    print(f"\n\n=== Writing outputs ===")

    wc_path   = out_dir / "wc_import_filled.csv"
    log_path  = out_dir / "fill_report.csv"
    rpt_path  = out_dir / "fill_report.txt"

    with open(wc_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=WC_COLUMNS)
        writer.writeheader()
        writer.writerows(wc_rows)
    print(f"  WC import CSV  → {wc_path}  ({len(wc_rows)} rows)")

    with open(log_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["supplier","sku","name","actions","price","has_desc","has_image"])
        writer.writeheader()
        writer.writerows(fill_log)
    print(f"  Fill log CSV   → {log_path}")

    # Summary by supplier
    by_sup_done = {}
    for row in fill_log:
        s = row["supplier"]
        by_sup_done.setdefault(s, {"total":0,"price":0,"desc":0,"image":0})
        by_sup_done[s]["total"] += 1
        if row["price"]: by_sup_done[s]["price"] += 1
        if row["has_desc"]: by_sup_done[s]["desc"] += 1
        if row["has_image"]: by_sup_done[s]["image"] += 1

    lines = [
        "=" * 60,
        "STOCK FILL REPORT",
        "=" * 60,
        f"  Total products updated : {len(wc_rows)}",
        f"  Products unchanged     : {skipped}",
        "",
        "BY SUPPLIER:",
    ]
    for sup, counts in by_sup_done.items():
        lines.append(
            f"  {sup:<16} {counts['total']:4d} updated  "
            f"(price:{counts['price']}  desc:{counts['desc']}  img:{counts['image']})"
        )
    lines += [
        "",
        "NEXT STEPS:",
        "  1. Review wc_import_filled.csv",
        "  2. WooCommerce > Products > Import",
        "     → Upload wc_import_filled.csv",
        "     → Tick 'Update existing products'",
        "     → Map columns → Run Import",
        "=" * 60,
    ]
    report = "\n".join(lines)
    print("\n" + report)
    with open(rpt_path, "w") as f:
        f.write(report)


if __name__ == "__main__":
    main()
