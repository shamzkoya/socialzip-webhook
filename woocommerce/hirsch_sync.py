#!/usr/bin/env python3
"""
============================================================
Hirschs Pamphlet → WooCommerce Sync Tool
============================================================
Processes Hirschs pamphlet images to:

  1. Extract every product (brand, model, specs, Hirsch price)
     using Claude Vision
  2. Research market prices on Takealot, Makro, Game
  3. Set competitive retail price with minimum 15-25% margin
  4. Find product images (brand websites, Takealot, etc.)
  5. Generate full SEO content with Claude
  6. Output WooCommerce-ready import CSV

USAGE:
  python3 hirsch_sync.py \
    --images /path/to/hirsch_pages/ \
    --out    /path/to/output/ \
    --min-margin 20

  # Resume after interruption:
  python3 hirsch_sync.py --images ./pages --out ./out --resume

PRICING LOGIC:
  - Hirsch pamphlet price = your cost price
  - Script researches Takealot, Makro, Game for market retail price
  - Sets your price = market price (competitive) IF margin >= min-margin %
  - If market price gives < min-margin, sets price = cost × (1 + min-margin/100)
  - Flags products where market price couldn't be found

OUTPUTS:
  hirsch_wc_import.csv       — WooCommerce import CSV
  hirsch_pricing_report.csv  — Full pricing breakdown per product
  hirsch_missing_images.csv  — Products needing manual images
  hirsch_extracted.json      — Raw extraction (for --resume)
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
CLAUDE_MODEL  = "claude-haiku-4-5-20251001"
MAX_RETRIES   = 3
RETRY_DELAY   = 6
DEFAULT_MIN_MARGIN = 20.0   # % minimum margin
VAT_RATE      = 0.15
SUPPLIER      = "Hirschs"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
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
    "Attribute 3 name", "Attribute 3 value(s)", "Attribute 3 visible", "Attribute 3 global",
    "Meta: title", "Meta: description", "Meta: keywords",
    "_yoast_wpseo_title", "_yoast_wpseo_metadesc", "_yoast_wpseo_focuskw",
    "Brands",
]

# ── Prompts ──────────────────────────────────────────────────────────────────
EXTRACT_PROMPT = """You are extracting product data from a Hirschs promotional pamphlet page.

Hirschs is a South African appliance and electronics retailer. Their pamphlets feature branded appliances: TVs, fridges, washing machines, dishwashers, microwaves, air conditioners, sound systems, small appliances, and similar.

Examine the image carefully and extract EVERY product visible.

Return ONLY a valid JSON object — no markdown, no extra text:

{
  "products": [
    {
      "brand": "Brand name e.g. LG, Samsung, Hisense, Defy, Bosch, Siemens, KIC",
      "name": "Full product name as shown",
      "model": "Exact model number e.g. OLED65C4PSA, WM-9000X — this is critical for price research",
      "sku": "Model number (same as model if no separate SKU shown)",
      "category": "Category e.g. Television, Refrigerator, Washing Machine, Dishwasher, Air Conditioner, Microwave, Sound System, Small Appliance",
      "hirsch_price": 0.00,
      "colour": "Colour if mentioned",
      "description_raw": "All descriptive text shown for this product",
      "features": ["key feature 1", "key feature 2", "key feature 3"],
      "specifications": {
        "weight_kg": null,
        "length_cm": null,
        "width_cm": null,
        "height_cm": null,
        "capacity_litres": null,
        "screen_size_inches": null,
        "resolution": null,
        "energy_rating": null,
        "power_watts": null,
        "load_kg": null,
        "warranty_years": null,
        "colour": null,
        "other": {}
      },
      "tags": ["tag1", "tag2", "tag3"]
    }
  ]
}

Rules:
- Extract EVERY visible product — do not skip any
- model number is critical — copy it EXACTLY as shown (e.g. OLED65C4PSA, QA65QN800DKXFA)
- hirsch_price must be a number, no currency symbols (e.g. 34999.00)
- If price not visible, use 0
- Return ONLY the JSON object
"""

SEO_PROMPT = """You are an SEO copywriter for a South African online appliance and electronics store.

Generate compelling WooCommerce content for this product.

PRODUCT DATA:
{product_json}

Return ONLY a valid JSON object — no markdown, no extra text:

{{
  "seo_name": "Product listing name — brand + model + key spec, under 80 chars",
  "short_description": "One compelling sentence (120-160 chars) highlighting the top 2 benefits. No HTML.",
  "description": "Full HTML: <h2>Overview</h2> 1-2 paragraphs, <h3>Key Features</h3> <ul><li> list, <h3>Specifications</h3> <ul> of specs, closing <p> call-to-action. Use keywords naturally.",
  "meta_title": "SEO meta title: brand + model + category + key benefit (55-60 chars max)",
  "meta_description": "Compelling meta description with CTA (145-160 chars). Include brand, model, top benefit.",
  "meta_keywords": "keyword1, keyword2, keyword3, keyword4, keyword5, keyword6, keyword7, keyword8",
  "focus_keyword": "Primary 3-5 word keyword phrase e.g. 'LG 65 inch OLED TV'",
  "yoast_title": "%%title%% - %%sitename%%",
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5"],
  "category_path": "Parent Category > Sub Category"
}}

Rules:
- Write in South African English ('colour' not 'color')
- Include model number in title and description for SEO
- Be conversion-focused
- Return ONLY the JSON object
"""

# ── Market Price Research ─────────────────────────────────────────────────────
def research_market_price(brand: str, model: str, name: str) -> dict:
    """
    Search Takealot, Makro, Game, Checkers for current retail price.
    Returns dict with best_price, source, all_prices found.
    """
    if not REQUESTS_AVAILABLE:
        return {"best_price": 0, "source": "", "all_prices": []}

    model_clean = (model or "").strip()
    brand_clean = (brand or "").strip()
    name_clean  = (name or "").strip()
    found_prices = []

    # ── Takealot ──────────────────────────────────────────────────────────────
    if model_clean:
        try:
            query = f"{brand_clean} {model_clean}".strip()
            url = f"https://www.takealot.com/all?qsearch={requests.utils.quote(query)}"
            resp = requests.get(url, headers=HEADERS, timeout=10)
            if resp.status_code == 200:
                # Extract prices from JSON data in page
                prices = re.findall(r'"buyBoxPrice"\s*:\s*(\d+(?:\.\d+)?)', resp.text)
                if not prices:
                    prices = re.findall(r'"price"\s*:\s*(\d{4,6}(?:\.\d+)?)', resp.text)
                for p in prices[:3]:
                    val = float(p)
                    if 500 < val < 500000:
                        found_prices.append({"price": val, "source": "Takealot"})
        except Exception:
            pass

    # ── Makro ─────────────────────────────────────────────────────────────────
    if model_clean:
        try:
            query = f"{brand_clean} {model_clean}".strip()
            url = f"https://www.makro.co.za/search?q={requests.utils.quote(query)}"
            resp = requests.get(url, headers=HEADERS, timeout=10)
            if resp.status_code == 200:
                prices = re.findall(r'"price"\s*:\s*"?(\d{4,6}(?:\.\d+)?)"?', resp.text)
                for p in prices[:3]:
                    val = float(p)
                    if 500 < val < 500000:
                        found_prices.append({"price": val, "source": "Makro"})
        except Exception:
            pass

    # ── Game ──────────────────────────────────────────────────────────────────
    if model_clean:
        try:
            query = f"{brand_clean} {model_clean}".strip()
            url = f"https://www.game.co.za/en/search/?text={requests.utils.quote(query)}"
            resp = requests.get(url, headers=HEADERS, timeout=10)
            if resp.status_code == 200:
                prices = re.findall(r'"price"\s*:\s*(\d{4,6}(?:\.\d+)?)', resp.text)
                for p in prices[:3]:
                    val = float(p)
                    if 500 < val < 500000:
                        found_prices.append({"price": val, "source": "Game"})
        except Exception:
            pass

    # ── DuckDuckGo price search fallback ──────────────────────────────────────
    if not found_prices and DDG_AVAILABLE and model_clean:
        try:
            query = f"{brand_clean} {model_clean} price south africa buy"
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=5))
            for r in results:
                text = r.get("body", "") + r.get("title", "")
                prices = re.findall(r'R\s*(\d{1,3}(?:[,\s]\d{3})*(?:\.\d{2})?)', text)
                for p in prices:
                    val = float(p.replace(",", "").replace(" ", ""))
                    if 500 < val < 500000:
                        found_prices.append({"price": val, "source": "Web"})
        except Exception:
            pass

    if not found_prices:
        return {"best_price": 0, "source": "", "all_prices": []}

    # Return median price to avoid outliers
    sorted_prices = sorted(found_prices, key=lambda x: x["price"])
    mid = len(sorted_prices) // 2
    best = sorted_prices[mid]
    return {
        "best_price": best["price"],
        "source": best["source"],
        "all_prices": found_prices
    }


def calculate_retail_price(cost: float, market_price: float, min_margin_pct: float) -> tuple[float, str]:
    """
    Returns (retail_price, pricing_method).
    Strategy:
    - If market price found and gives >= min_margin: use market price (be competitive)
    - If market price too low (< min_margin): use cost × (1 + min_margin/100)
    - If no market price: use cost × (1 + min_margin/100 + 0.05) as safe default
    """
    if not cost or cost <= 0:
        return 0.0, "no_cost"

    min_price = round(cost * (1 + min_margin_pct / 100), 2)

    if market_price and market_price > 0:
        margin = (market_price - cost) / cost * 100
        if margin >= min_margin_pct:
            return round(market_price, 2), f"market_price ({margin:.1f}% margin)"
        else:
            return min_price, f"min_margin_floor (market was {margin:.1f}%)"

    # No market price found — add buffer above minimum
    safe_price = round(cost * (1 + (min_margin_pct + 5) / 100), 2)
    return safe_price, "estimated_no_market_data"


# ── Image Search ──────────────────────────────────────────────────────────────
def find_product_image(brand: str, model: str, name: str) -> str:
    if not DDG_AVAILABLE or not REQUESTS_AVAILABLE:
        return ""

    # Priority 1: Takealot
    if model:
        try:
            url = f"https://www.takealot.com/all?qsearch={requests.utils.quote(f'{brand} {model}')}"
            resp = requests.get(url, headers=HEADERS, timeout=8)
            if resp.status_code == 200:
                imgs = re.findall(
                    r'"(?:image|imageUrl|img)":\s*"(https://[^"]+(?:takealot|dream)[^"]+\.(?:jpg|jpeg|png|webp))"',
                    resp.text, re.IGNORECASE
                )
                if imgs:
                    return imgs[0]
        except Exception:
            pass

    # Priority 2: Brand website
    brand_domain = brand.lower().replace(" ", "") + ".co.za"
    queries = []
    if model:
        queries.append(f'"{model}" {brand} product image site:{brand_domain} OR site:takealot.com OR site:makro.co.za')
        queries.append(f'{brand} {model} official product image white background')
    queries.append(f'{brand} {name} buy online south africa product image')

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
                if model and model.lower() in combined:
                    score += 10
                for domain in ["takealot.com", "makro.co.za", "game.co.za", brand_domain]:
                    if domain in combined:
                        score += 5
                        break
                if brand.lower() in combined:
                    score += 3
                for bad in ["pinterest", "facebook", "twitter", "instagram", "youtube", "review"]:
                    if bad in combined:
                        score -= 5
                scored.append((score, img_url))
            scored.sort(key=lambda x: -x[0])
            best = scored[0][1] if scored else ""
            if best:
                return best
        except Exception as e:
            print(f"    [Image search warning: {e}]")
    return ""


# ── Claude API ────────────────────────────────────────────────────────────────
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


def extract_products_from_image(image_path: str, client) -> list[dict]:
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
def build_wc_row(product: dict, retail_price: float, pricing_method: str,
                 market_data: dict, seo: dict, image_url: str) -> dict:
    specs = product.get("specifications") or {}
    tags  = seo.get("tags") or product.get("tags") or []
    if isinstance(tags, list):
        tags = ", ".join(tags)
    in_stock = 1  # Hirsch products — assume available unless told otherwise

    return {
        "ID":                       "",
        "Type":                     "simple",
        "SKU":                      product.get("sku") or product.get("model") or "",
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
        "Stock":                    10,
        "Low stock amount":         2,
        "Backorders allowed?":      0,
        "Sold individually?":       0,
        "Weight (kg)":              specs.get("weight_kg") or "",
        "Length (cm)":              specs.get("length_cm") or "",
        "Width (cm)":               specs.get("width_cm") or "",
        "Height (cm)":              specs.get("height_cm") or "",
        "Allow customer reviews?":  1,
        "Purchase note":            "",
        "Sale price":               "",
        "Regular price":            retail_price if retail_price > 0 else "",
        "Categories":               seo.get("category_path") or product.get("category") or "Appliances",
        "Tags":                     tags,
        "Shipping class":           "",
        "Images":                   image_url,
        "Upsells":                  "",
        "Cross-sells":              "",
        "External URL":             "",
        "Button text":              "",
        "Position":                 "",
        "Attribute 1 name":         "Brand",
        "Attribute 1 value(s)":     product.get("brand") or "",
        "Attribute 1 visible":      1,
        "Attribute 1 global":       1,
        "Attribute 2 name":         "Colour" if product.get("colour") else "",
        "Attribute 2 value(s)":     product.get("colour") or "",
        "Attribute 2 visible":      1 if product.get("colour") else "",
        "Attribute 2 global":       1 if product.get("colour") else "",
        "Attribute 3 name":         "Model" if product.get("model") else "",
        "Attribute 3 value(s)":     product.get("model") or "",
        "Attribute 3 visible":      1 if product.get("model") else "",
        "Attribute 3 global":       "",
        "Meta: title":              seo.get("meta_title") or "",
        "Meta: description":        seo.get("meta_description") or "",
        "Meta: keywords":           seo.get("meta_keywords") or "",
        "_yoast_wpseo_title":       seo.get("yoast_title") or "",
        "_yoast_wpseo_metadesc":    seo.get("meta_description") or "",
        "_yoast_wpseo_focuskw":     seo.get("focus_keyword") or "",
        "Brands":                   product.get("brand") or SUPPLIER,
    }


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Hirschs pamphlet → WooCommerce sync")
    parser.add_argument("--images",     required=True, help="Folder of Hirsch pamphlet JPEG images")
    parser.add_argument("--out",        default=".",   help="Output directory")
    parser.add_argument("--min-margin", type=float, default=DEFAULT_MIN_MARGIN,
                        help=f"Minimum margin %% (default {DEFAULT_MIN_MARGIN})")
    parser.add_argument("--resume",     action="store_true", help="Skip already-processed pages")
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    out_dir       = Path(args.out)
    json_path     = out_dir / "hirsch_extracted.json"
    wc_path       = out_dir / "hirsch_wc_import.csv"
    pricing_path  = out_dir / "hirsch_pricing_report.csv"
    missing_path  = out_dir / "hirsch_missing_images.csv"

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set. Add it to woocommerce/.env")
        sys.exit(1)
    client = anthropic.Anthropic(api_key=api_key)

    # Get sorted image list
    images_dir = Path(args.images)
    image_paths = sorted([
        str(p) for p in images_dir.iterdir()
        if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    ])
    if not image_paths:
        print(f"ERROR: No images found in {images_dir}")
        sys.exit(1)
    print(f"Found {len(image_paths)} pamphlet images")

    # Load existing extractions if resuming
    all_extracted = []
    processed_pages = set()
    if args.resume and json_path.exists():
        with open(json_path) as f:
            all_extracted = json.load(f)
        processed_pages = {p["image"] for p in all_extracted}
        print(f"Resuming — {len(processed_pages)} pages already processed")

    # Extract products from each page
    print(f"\nExtracting products with Claude Vision...")
    for i, img_path in enumerate(image_paths, 1):
        if img_path in processed_pages:
            print(f"  Page {i}/{len(image_paths)} — skipped (already done)")
            continue
        print(f"  Page {i}/{len(image_paths)}: {Path(img_path).name}")
        products = extract_products_from_image(img_path, client)
        all_extracted.append({"image": img_path, "page": i, "products": products})
        with open(json_path, "w") as f:
            json.dump(all_extracted, f, indent=2)
        time.sleep(1)

    # Flatten + deduplicate by model/SKU
    seen = {}
    all_products = []
    for page_data in all_extracted:
        for p in page_data.get("products", []):
            key = (p.get("model") or p.get("sku") or p.get("name") or "").strip().upper()
            if key and key in seen:
                continue
            if key:
                seen[key] = True
            all_products.append(p)

    print(f"\nTotal unique products: {len(all_products)}")
    print(f"Min margin: {args.min_margin}%")

    # Process each product: price research + SEO + image
    print(f"\nResearching prices, generating SEO, finding images...")
    wc_rows      = []
    pricing_rows = []
    missing_imgs = []

    for i, product in enumerate(all_products, 1):
        brand  = product.get("brand") or ""
        model  = product.get("model") or ""
        name   = product.get("name") or ""
        cost   = float(product.get("hirsch_price") or 0)

        print(f"\n  [{i}/{len(all_products)}] {brand} {model} — {name[:40]}")
        print(f"    Hirsch cost: R{cost:,.2f}")

        # Market price research
        print(f"    Researching market price...")
        market_data = research_market_price(brand, model, name)
        market_price = market_data["best_price"]
        if market_price:
            print(f"    Market price: R{market_price:,.2f} (from {market_data['source']})")
        else:
            print(f"    No market price found")

        # Calculate retail price
        retail, method = calculate_retail_price(cost, market_price, args.min_margin)
        margin_actual = ((retail - cost) / cost * 100) if cost > 0 else 0
        print(f"    Retail price: R{retail:,.2f} ({method}, {margin_actual:.1f}% margin)")

        # SEO generation
        seo_input = {
            **product,
            "retail_price_zar": retail,
            "hirsch_cost_zar":  cost,
            "market_price_zar": market_price,
            "margin_pct":       round(margin_actual, 1),
        }
        seo = generate_seo(seo_input, client)

        # Image search
        print(f"    Finding image...")
        img_url = find_product_image(brand, model, name)
        if img_url:
            print(f"    Found: {img_url[:70]}...")
        else:
            print(f"    No image found")
            missing_imgs.append({"brand": brand, "model": model, "name": name, "sku": product.get("sku", "")})

        wc_rows.append(build_wc_row(product, retail, method, market_data, seo, img_url))

        pricing_rows.append({
            "brand":          brand,
            "model":          model,
            "name":           name,
            "hirsch_cost":    cost,
            "market_price":   market_price,
            "market_source":  market_data.get("source", ""),
            "retail_price":   retail,
            "margin_pct":     round(margin_actual, 1),
            "pricing_method": method,
        })

        time.sleep(0.5)

    # Write WooCommerce CSV
    with open(wc_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=WC_COLUMNS)
        writer.writeheader()
        writer.writerows(wc_rows)

    # Write pricing report
    pricing_fields = ["brand","model","name","hirsch_cost","market_price",
                      "market_source","retail_price","margin_pct","pricing_method"]
    with open(pricing_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=pricing_fields)
        writer.writeheader()
        writer.writerows(pricing_rows)

    # Write missing images
    with open(missing_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["brand","model","name","sku"])
        writer.writeheader()
        writer.writerows(missing_imgs)

    # Summary
    priced     = sum(1 for r in wc_rows if r["Regular price"])
    has_image  = len(wc_rows) - len(missing_imgs)
    avg_margin = sum(r["margin_pct"] for r in pricing_rows if r["hirsch_cost"] > 0) / max(1, sum(1 for r in pricing_rows if r["hirsch_cost"] > 0))

    print(f"""
============================================================
DONE
============================================================
  Products extracted         : {len(all_products)}
  With price set             : {priced}
  With images found          : {has_image}
  Missing images             : {len(missing_imgs)}
  Average margin             : {avg_margin:.1f}%

Output files:
  WooCommerce import  : {wc_path}
  Pricing report      : {pricing_path}
  Missing images      : {missing_path}
  Raw extraction      : {json_path}

Next steps:
  1. Review {pricing_path} — check margins, adjust prices if needed
  2. Review {missing_path} — add images manually
  3. Import {wc_path} into WooCommerce > Products > Import
     → Tick "Update existing products"
============================================================
""")


if __name__ == "__main__":
    main()
