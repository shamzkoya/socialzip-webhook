#!/usr/bin/env python3
"""
============================================================
Impulse Imports → WooCommerce Sync Tool
============================================================
Combines the Impulse pamphlet (PDF) with the stock/price CSV to:

  1. Extract every product from the pamphlet using Claude Vision
  2. Match to stock CSV for price (ex VAT) and available stock
  3. Apply pricing: cost × 1.15 VAT × 1.40 markup
  4. Find product images online (Takealot, Amazon, DuckDuckGo)
  5. Generate SEO titles, descriptions, meta tags with Claude
  6. Output a WooCommerce-ready import CSV

USAGE:
  python3 impulse_sync.py \
    --pamphlet /path/to/impulse_catalogue.pdf \
    --stock    /path/to/impulse_pricelist.csv \
    --out      /path/to/output/ \
    --markup   40

  # Resume after interruption (skips already-processed pages):
  python3 impulse_sync.py --pamphlet catalogue.pdf --stock pricelist.csv --out ./out --resume

OUTPUTS:
  impulse_wc_import.csv     — WooCommerce import (update existing + new products)
  impulse_missing_images.csv — Products where no image was found (review manually)
  impulse_extracted.json    — Raw extraction data (for debugging/reprocessing)
============================================================
"""

import os, re, csv, sys, json, base64, argparse, time
from pathlib import Path
from typing import Optional

# ── Load .env ────────────────────────────────────────────────────────────────
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
    print("ERROR: anthropic not installed. Run: pip3 install anthropic")
    sys.exit(1)

try:
    from duckduckgo_search import DDGS
    DDG_AVAILABLE = True
except ImportError:
    DDG_AVAILABLE = False

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

# ── Config ───────────────────────────────────────────────────────────────────
CLAUDE_MODEL = "claude-haiku-4-5-20251001"
MAX_RETRIES  = 3
RETRY_DELAY  = 6
VAT_RATE     = 0.15
SUPPLIER     = "Impulse Imports"

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

# ── Prompts ──────────────────────────────────────────────────────────────────
EXTRACT_PROMPT = """You are extracting product data from an Impulse Imports catalogue page.

Examine the image carefully and extract EVERY product visible.

Impulse Imports sells general merchandise: homewares, kitchenware, garden, décor, tools, storage, stationery, toys, cleaning, hardware, and similar everyday items.

Return ONLY a valid JSON object — no markdown, no extra text:

{
  "products": [
    {
      "item_code": "The Impulse item code e.g. F00015, F01234 — look carefully for codes starting with F followed by digits",
      "name": "Full descriptive product name",
      "colour": "Colour if visible",
      "description_raw": "All descriptive text shown for this product",
      "features": ["feature 1", "feature 2"],
      "specifications": {
        "weight_kg": null,
        "length_cm": null,
        "width_cm": null,
        "height_cm": null,
        "capacity": null,
        "material": null,
        "pack_size": null,
        "other": {}
      },
      "category": "Best-fit category (e.g. Kitchenware, Garden, Storage, Cleaning, Tools, Décor, Toys)",
      "tags": ["tag1", "tag2", "tag3"]
    }
  ]
}

Rules:
- Extract EVERY visible product — do not skip any
- The item_code is critical — look for F-codes (e.g. F00015). If not visible, use null
- If a field is not shown, use null
- Return ONLY the JSON object
"""

SEO_PROMPT = """You are an SEO copywriter for a South African online homewares and general merchandise store.

Generate compelling WooCommerce content for this Impulse Imports product.

PRODUCT DATA:
{product_json}

Return ONLY a valid JSON object — no markdown, no extra text:

{{
  "seo_name": "Product listing name — descriptive, include key attributes, under 80 chars",
  "short_description": "One compelling sentence (120-160 chars) highlighting top benefits. No HTML.",
  "description": "Full HTML description: <h2>Overview</h2> 1-2 paragraphs, <h3>Key Features</h3> <ul><li> list, <h3>Specifications</h3> <ul> of specs, closing <p> call-to-action. Use keywords naturally.",
  "meta_title": "SEO meta title: product name + key benefit + brand (55-60 chars max)",
  "meta_description": "Compelling meta description with CTA (145-160 chars). Include product name and top benefit.",
  "meta_keywords": "keyword1, keyword2, keyword3, keyword4, keyword5, keyword6, keyword7, keyword8",
  "focus_keyword": "Primary 3-5 word keyword phrase (e.g. 'bamboo pegs 20 pack')",
  "yoast_title": "%%title%% - %%sitename%%",
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5"],
  "category_path": "Parent Category > Sub Category"
}}

Rules:
- Write in South African English ('colour' not 'color', 'organisation' not 'organization')
- Be conversion-focused and practical
- Include the item code and product name in key SEO positions
- Return ONLY the JSON object
"""

# ── Helpers ──────────────────────────────────────────────────────────────────
def load_image_as_base64(path: str) -> tuple[str, str]:
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode("utf-8")
    ext = Path(path).suffix.lower()
    types = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
    return data, types.get(ext, "image/jpeg")


def parse_json_response(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r'^```(?:json)?\s*\n?', '', text)
        text = re.sub(r'\n?```\s*$', '', text)
    return json.loads(text.strip())


def retail_price(cost_ex_vat: float, markup_pct: float = 40.0) -> float:
    """cost × (1 + markup%) × (1 + VAT%) — rounded to 2dp."""
    if not cost_ex_vat or cost_ex_vat <= 0:
        return 0.0
    return round(cost_ex_vat * (1 + markup_pct / 100) * (1 + VAT_RATE), 2)


def load_stock_csv(stock_path: str) -> dict[str, dict]:
    """Load impulse_products.csv → dict keyed by item_code."""
    stock = {}
    with open(stock_path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            code = row.get("supplier_code", "").strip()
            if code:
                stock[code] = row
    return stock


def pdf_to_images(pdf_path: str, output_dir: str) -> list[str]:
    try:
        from pdf2image import convert_from_path
    except ImportError:
        print("ERROR: pdf2image not installed. Run: pip3 install pdf2image")
        print("       Also: sudo apt-get install -y poppler-utils")
        sys.exit(1)
    os.makedirs(output_dir, exist_ok=True)
    pages = convert_from_path(pdf_path, dpi=200, fmt="jpeg")
    paths = []
    for i, page in enumerate(pages):
        out = os.path.join(output_dir, f"page_{i+1:03d}.jpg")
        page.save(out, "JPEG", quality=90)
        paths.append(out)
        print(f"  Page {i+1}/{len(pages)} → {Path(out).name}")
    return paths


def find_product_image(name: str, item_code: str) -> str:
    """Search online for a product image. Returns best URL found or ''."""
    if not DDG_AVAILABLE or not REQUESTS_AVAILABLE:
        return ""

    HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    name_clean = (name or "").strip()
    code_clean = (item_code or "").strip()

    # Build search queries from specific to general
    queries = []
    if code_clean:
        queries.append(f'"{code_clean}" product image')
        queries.append(f'impulse imports {code_clean} product')
    queries.append(f'{name_clean} product image white background')
    queries.append(f'{name_clean} buy online south africa')

    # Try Takealot first
    if name_clean:
        try:
            url = f"https://www.takealot.com/all?qsearch={requests.utils.quote(name_clean)}"
            resp = requests.get(url, headers=HEADERS, timeout=8)
            if resp.status_code == 200:
                imgs = re.findall(
                    r'"(?:image|imageUrl)":\s*"(https://[^"]+(?:takealot|dream)[^"]+\.(?:jpg|jpeg|png|webp))"',
                    resp.text, re.IGNORECASE
                )
                if imgs:
                    return imgs[0]
        except Exception:
            pass

    # DuckDuckGo fallback
    for query in queries:
        try:
            with DDGS() as ddgs:
                results = list(ddgs.images(query, max_results=8, type_image="photo"))
            if not results:
                continue
            scored = []
            for r in results:
                img_url = r.get("image", "")
                src_url = r.get("url", "")
                score = 0
                combined = (img_url + " " + src_url).lower()
                if code_clean and code_clean.lower() in combined:
                    score += 10
                for domain in ["takealot.com", "amazon.com", "makro.co.za", "game.co.za"]:
                    if domain in combined:
                        score += 5
                        break
                for bad in ["pinterest", "facebook", "twitter", "instagram", "youtube"]:
                    if bad in combined:
                        score -= 5
                scored.append((score, img_url))
            scored.sort(key=lambda x: -x[0])
            best = scored[0][1] if scored else ""
            if best:
                return best
        except Exception as e:
            print(f"    [Image search warning: {e}]")
            continue

    return ""


# ── Claude API Calls ──────────────────────────────────────────────────────────
def extract_products_from_page(image_path: str, client) -> list[dict]:
    b64, media_type = load_image_as_base64(image_path)
    for attempt in range(MAX_RETRIES):
        try:
            msg = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=4096,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}},
                        {"type": "text", "text": EXTRACT_PROMPT}
                    ]
                }]
            )
            result = parse_json_response(msg.content[0].text)
            products = result.get("products", [])
            print(f"    → {len(products)} product(s) found")
            return products
        except json.JSONDecodeError as e:
            print(f"    [JSON parse error attempt {attempt+1}: {e}]")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
        except Exception as e:
            print(f"    [API error attempt {attempt+1}: {e}]")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY * (attempt + 1))
    return []


def generate_seo(product: dict, client) -> dict:
    prompt = SEO_PROMPT.format(product_json=json.dumps(product, indent=2))
    for attempt in range(MAX_RETRIES):
        try:
            msg = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=2048,
                messages=[{"role": "user", "content": prompt}]
            )
            return parse_json_response(msg.content[0].text)
        except Exception as e:
            print(f"    [SEO error attempt {attempt+1}: {e}]")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
    return {}


# ── WooCommerce Row Builder ───────────────────────────────────────────────────
def build_wc_row(product: dict, stock_row: Optional[dict], seo: dict,
                 image_url: str, markup_pct: float) -> dict:
    item_code  = product.get("item_code") or ""
    cost       = float(stock_row["price_ex_vat"]) if stock_row else 0.0
    price      = retail_price(cost, markup_pct) if cost > 0 else 0.0
    available  = int(float(stock_row["stock_available"])) if stock_row else 0
    in_stock   = 1 if available > 0 else 0

    specs = product.get("specifications") or {}
    tags  = (seo.get("tags") or product.get("tags") or [])
    if isinstance(tags, list):
        tags = ", ".join(tags)

    return {
        "ID":                       "",
        "Type":                     "simple",
        "SKU":                      item_code,
        "Name":                     seo.get("seo_name") or product.get("name") or "",
        "Published":                1,
        "Is featured?":             0,
        "Visibility in catalog":    "visible",
        "Short description":        seo.get("short_description") or "",
        "Description":              seo.get("description") or "",
        "Date sale price starts":   "",
        "Date sale price ends":     "",
        "Tax status":               "taxable",
        "Tax class":                "",
        "In stock?":                in_stock,
        "Stock":                    available,
        "Low stock amount":         5,
        "Backorders allowed?":      0,
        "Sold individually?":       0,
        "Weight (kg)":              specs.get("weight_kg") or "",
        "Length (cm)":              specs.get("length_cm") or "",
        "Width (cm)":               specs.get("width_cm") or "",
        "Height (cm)":              specs.get("height_cm") or "",
        "Allow customer reviews?":  1,
        "Purchase note":            "",
        "Sale price":               "",
        "Regular price":            price if price > 0 else "",
        "Categories":               seo.get("category_path") or product.get("category") or "General Merchandise",
        "Tags":                     tags,
        "Shipping class":           "",
        "Images":                   image_url,
        "Upsells":                  "",
        "Cross-sells":              "",
        "External URL":             "",
        "Button text":              "",
        "Position":                 "",
        "Attribute 1 name":         "Colour" if product.get("colour") else "",
        "Attribute 1 value(s)":     product.get("colour") or "",
        "Attribute 1 visible":      1 if product.get("colour") else "",
        "Attribute 1 global":       1 if product.get("colour") else "",
        "Attribute 2 name":         "",
        "Attribute 2 value(s)":     "",
        "Attribute 2 visible":      "",
        "Attribute 2 global":       "",
        "Meta: title":              seo.get("meta_title") or "",
        "Meta: description":        seo.get("meta_description") or "",
        "Meta: keywords":           seo.get("meta_keywords") or "",
        "_yoast_wpseo_title":       seo.get("yoast_title") or "",
        "_yoast_wpseo_metadesc":    seo.get("meta_description") or "",
        "_yoast_wpseo_focuskw":     seo.get("focus_keyword") or "",
        "Brands":                   SUPPLIER,
    }


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Impulse Imports → WooCommerce sync")
    parser.add_argument("--pamphlet", required=True, help="Impulse catalogue PDF")
    parser.add_argument("--stock",    required=True, help="Impulse price list CSV (from impulse_extract.py)")
    parser.add_argument("--out",      default=".",   help="Output directory")
    parser.add_argument("--markup",   type=float, default=40.0, help="Markup %% over cost (default 40)")
    parser.add_argument("--resume",   action="store_true", help="Skip pages already in impulse_extracted.json")
    parser.add_argument("--wc-export", dest="wc_export", default="",
                        help="WooCommerce product export CSV — used to skip products "
                             "already in the store and to detect what fields are missing")
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    out_dir     = Path(args.out)
    json_path   = out_dir / "impulse_extracted.json"
    wc_path     = out_dir / "impulse_wc_import.csv"
    missing_path= out_dir / "impulse_missing_images.csv"

    # Load API key
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set. Add it to woocommerce/.env")
        sys.exit(1)
    client = anthropic.Anthropic(api_key=api_key)

    # Load stock data
    print(f"Loading stock data from: {args.stock}")
    stock = load_stock_csv(args.stock)
    print(f"  {len(stock)} products in stock CSV")

    # Convert PDF to images (skip if pages already exist and resuming)
    pages_dir = out_dir / "_pages"
    existing_pages = sorted(pages_dir.glob("page_*.jpg")) if pages_dir.exists() else []
    if args.resume and existing_pages:
        image_paths = [str(p) for p in existing_pages]
        print(f"\nUsing {len(image_paths)} existing page images (resume mode)")
    else:
        print(f"\nConverting pamphlet PDF to images...")
        image_paths = pdf_to_images(args.pamphlet, str(pages_dir))
        print(f"  {len(image_paths)} pages")

    # Load existing extractions if resuming
    all_extracted = []
    processed_pages = set()
    if args.resume and json_path.exists():
        with open(json_path) as f:
            all_extracted = json.load(f)
        processed_pages = {p["page"] for p in all_extracted}
        print(f"\nResuming — {len(processed_pages)} pages already processed")

    # Extract products from each page
    print(f"\nExtracting products from {len(image_paths)} pages with Claude Vision...")
    for i, img_path in enumerate(image_paths, 1):
        if i in processed_pages:
            print(f"  Page {i}/{len(image_paths)} — skipped (already done)")
            continue
        print(f"  Page {i}/{len(image_paths)}")
        products = extract_products_from_page(img_path, client)
        all_extracted.append({"page": i, "image": img_path, "products": products})
        # Save progress after each page
        with open(json_path, "w") as f:
            json.dump(all_extracted, f, indent=2)
        time.sleep(1)  # gentle rate limiting

    # Flatten all products, deduplicate by item_code
    seen_codes = {}
    all_products = []
    for page_data in all_extracted:
        for p in page_data.get("products", []):
            code = (p.get("item_code") or "").strip()
            if code and code in seen_codes:
                continue  # deduplicate
            if code:
                seen_codes[code] = True
            all_products.append(p)

    print(f"\nTotal unique products extracted: {len(all_products)}")

    # ── Load WC export to avoid duplicates ──────────────────────────────────
    wc_existing = {}  # sku -> wc row
    if args.wc_export and Path(args.wc_export).exists():
        print(f"\nLoading WC export to check for existing products...")
        with open(args.wc_export, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                sku = row.get("SKU", "").strip().upper()
                if sku:
                    wc_existing[sku] = row
        print(f"  {len(wc_existing)} products already in WC store")

    def needs_update(wc_row):
        """Return list of fields that are missing/empty in the existing WC product."""
        missing = []
        if not wc_row.get("Description", "").strip():
            missing.append("description")
        if not wc_row.get("Images", "").strip():
            missing.append("image")
        p = str(wc_row.get("Regular price", "")).strip()
        if not p or p in ("0", "0.0"):
            missing.append("price")
        return missing

    # Generate SEO + find images + build WooCommerce rows
    print(f"\nGenerating SEO content and finding images...")
    wc_rows = []
    missing_images = []
    skipped_complete = 0

    for i, product in enumerate(all_products, 1):
        item_code  = (product.get("item_code") or "").strip().upper()
        name       = product.get("name") or ""
        stock_row  = stock.get(item_code) if item_code else None

        # Check if already in WC store
        existing = wc_existing.get(item_code) if item_code else None
        if existing:
            missing = needs_update(existing)
            if not missing:
                skipped_complete += 1
                print(f"  [{i}/{len(all_products)}] {item_code} — SKIP (already complete in WC)")
                continue
            print(f"  [{i}/{len(all_products)}] {item_code} — UPDATE {missing}  '{name[:40]}'")
        else:
            print(f"  [{i}/{len(all_products)}] {item_code or 'NO-CODE'} — NEW  '{name[:50]}'")

        # SEO generation
        seo_input = {**product}
        if stock_row:
            seo_input["price_ex_vat"]    = stock_row["price_ex_vat"]
            seo_input["retail_price_zar"]= retail_price(float(stock_row["price_ex_vat"]), args.markup)
            seo_input["stock_available"] = stock_row["stock_available"]
        seo_input["supplier"] = SUPPLIER

        # For updates: only regenerate what's missing to save API costs
        seo = {}
        if existing:
            missing = needs_update(existing)
            if "description" in missing or "price" in missing:
                seo = generate_seo(seo_input, client)
        else:
            seo = generate_seo(seo_input, client)

        # Image search — only if missing
        img_url = ""
        if not existing or "image" in (needs_update(existing) if existing else []):
            print(f"    Searching for image...")
            img_url = find_product_image(name, item_code)
            if img_url:
                print(f"    Found: {img_url[:80]}...")
            else:
                print(f"    No image found")
                missing_images.append({"item_code": item_code, "name": name, "category": product.get("category", "")})
        else:
            img_url = existing.get("Images", "")

        # For updates: preserve existing WC ID so import updates rather than creates
        wc_row = build_wc_row(product, stock_row, seo, img_url, args.markup)
        if existing:
            wc_row["ID"] = existing.get("ID", "")
            # Preserve existing values where we're not updating
            if "description" not in (needs_update(existing) if existing else []):
                wc_row["Description"] = existing.get("Description", "")
                wc_row["Short description"] = existing.get("Short description", "")
        wc_rows.append(wc_row)
        time.sleep(0.5)

    print(f"\n  Skipped {skipped_complete} products already complete in WC")

    # Write WooCommerce import CSV
    with open(wc_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=WC_COLUMNS)
        writer.writeheader()
        writer.writerows(wc_rows)

    # Write missing images CSV
    with open(missing_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["item_code", "name", "category"])
        writer.writeheader()
        writer.writerows(missing_images)

    # Summary
    matched   = sum(1 for p in all_products if (p.get("item_code") or "").strip() in stock)
    unmatched = len(all_products) - matched
    priced    = sum(1 for r in wc_rows if r["Regular price"])

    new_count    = sum(1 for r in wc_rows if not r.get("ID"))
    update_count = sum(1 for r in wc_rows if r.get("ID"))
    print(f"""
============================================================
DONE
============================================================
  Products extracted from pamphlet : {len(all_products)}
  Already complete in WC (skipped) : {skipped_complete}
  Rows in import CSV               : {len(wc_rows)}
    → New products to add          : {new_count}
    → Existing products to update  : {update_count}
  Products with images found       : {len(wc_rows) - len(missing_images)}
  Products missing images          : {len(missing_images)}

Output files:
  WooCommerce import  : {wc_path}
  Missing images list : {missing_path}
  Raw extraction data : {json_path}

Next steps:
  1. Review {missing_path} — add images manually for these products
  2. Import {wc_path} into WooCommerce > Products > Import
     → Tick "Update existing products"
     → Products with an ID will UPDATE; those without will be CREATED
============================================================
""")


if __name__ == "__main__":
    main()
