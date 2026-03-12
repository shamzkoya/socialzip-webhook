#!/usr/bin/env python3
"""
============================================================
Pamphlet → WooCommerce Import Tool
============================================================
Feed it a folder of catalogue/pamphlet images (JPEGs, PNGs)
or a PDF and it will:

  1. Extract every product using Claude Vision AI
  2. Generate SEO titles, descriptions, meta tags
  3. Find product images from online sources
  4. Apply your markup to the supplier price
  5. Output a WooCommerce-ready import CSV

USAGE:
  python3 pamphlet_to_woocommerce.py --input ./pamphlet_images --markup 40
  python3 pamphlet_to_woocommerce.py --input catalogue.pdf --markup 35 --vat 15
  python3 pamphlet_to_woocommerce.py --input ./images --markup 40 --output my_products.csv

FIRST-TIME SETUP:
  1. Run:  bash setup_pamphlet.sh
  2. Copy .env.example to .env and add your Anthropic API key
  3. Run:  bash run_pamphlet.sh --input ./your_images --markup 40
============================================================
"""

import os
import re
import csv
import sys
import json
import base64
import argparse
import time
import math
from pathlib import Path
from typing import Optional

# ── Load .env file if present ────────────────────────────────────────────────
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
    print("ERROR: anthropic package not installed. Run: bash setup_pamphlet.sh")
    sys.exit(1)

try:
    from duckduckgo_search import DDGS
    DDG_AVAILABLE = True
except ImportError:
    DDG_AVAILABLE = False
    print("Warning: duckduckgo_search not installed — image search disabled. Run setup_pamphlet.sh")

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False


# ── CONFIGURATION ─────────────────────────────────────────────────────────────

CLAUDE_MODEL   = "claude-opus-4-6"
MAX_RETRIES    = 3
RETRY_DELAY    = 6   # seconds between API retries

# WooCommerce import CSV columns (exact names WooCommerce expects)
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


# ── PROMPTS ───────────────────────────────────────────────────────────────────

EXTRACT_PROMPT = """You are a product data extraction specialist analysing a supplier catalogue page.

Carefully examine the image and extract EVERY product you can see.

Return ONLY a valid JSON object in this exact format — no extra text, no markdown:

{
  "products": [
    {
      "name": "Full descriptive product name including brand",
      "brand": "Brand or manufacturer name",
      "model": "Model number or model name (e.g. WM-9000X)",
      "sku": "Product code, part number, item code, or barcode if visible",
      "category": "Product category (e.g. Washing Machines, LED TVs, Power Tools, Safety Equipment)",
      "sub_category": "Sub-category if applicable (e.g. Front Load, 4K OLED)",
      "supplier_price_ex_vat": 0.00,
      "currency": "ZAR",
      "colour": "Colour if mentioned",
      "description_raw": "All descriptive text for this product as shown in the catalogue",
      "features": ["key feature 1", "key feature 2", "key feature 3"],
      "specifications": {
        "weight_kg": null,
        "length_cm": null,
        "width_cm": null,
        "height_cm": null,
        "capacity": null,
        "power_watts": null,
        "voltage": null,
        "energy_rating": null,
        "warranty": null,
        "colour": null,
        "other": {}
      },
      "in_box": "What is included in the box",
      "tags": ["tag1", "tag2", "tag3"]
    }
  ]
}

Rules:
- Extract EVERY visible product — do not skip any
- If a field is not shown, use null (not empty string)
- Prices must be numbers only, no currency symbols
- Extract all specification text even if partial
- Return ONLY the JSON object, nothing else
"""

SEO_PROMPT = """You are an expert SEO copywriter for a South African online appliance and electronics store.

Given the product data below, generate compelling SEO-optimised WooCommerce content.

PRODUCT DATA:
{product_json}

Return ONLY a valid JSON object in this exact format — no extra text, no markdown:

{{
  "seo_name": "Product listing name (include brand, model, key spec — keep under 80 chars)",
  "short_description": "One compelling sentence (120-160 chars) highlighting the top 2 benefits. No HTML.",
  "description": "Full HTML description. Structure: <h2>Overview</h2> then 2 paragraphs, <h3>Key Features</h3> with <ul><li> list, <h3>Specifications</h3> with a clean <table> or <ul> of specs, then a short <p> call-to-action. Use keywords naturally.",
  "meta_title": "SEO meta title: brand + model + category + key benefit (55-60 chars max)",
  "meta_description": "Compelling meta description with CTA (145-160 chars). Include brand, model, top benefit.",
  "meta_keywords": "keyword1, keyword2, keyword3, keyword4, keyword5, keyword6, keyword7, keyword8",
  "focus_keyword": "Primary 3-5 word keyword phrase for Yoast (e.g. 'LG 9kg front load washing machine')",
  "yoast_title": "%%title%% - %%sitename%%",
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5"],
  "category_path": "Parent Category > Sub Category"
}}

Rules:
- Write in South African English (e.g. 'colour' not 'color')
- Be conversion-focused — customers should want to buy
- Include model number and brand name in key SEO positions
- Return ONLY the JSON object, nothing else
"""


# ── HELPERS ───────────────────────────────────────────────────────────────────

def load_image_as_base64(image_path: str) -> tuple[str, str]:
    """Returns (base64_data, media_type)."""
    with open(image_path, "rb") as f:
        data = base64.b64encode(f.read()).decode("utf-8")
    ext = Path(image_path).suffix.lower()
    media_types = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp",
    }
    media_type = media_types.get(ext, "image/jpeg")
    return data, media_type


def parse_json_response(text: str) -> dict:
    """Strip markdown fences and parse JSON."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r'^```(?:json)?\s*\n?', '', text)
        text = re.sub(r'\n?```\s*$', '', text)
    return json.loads(text.strip())


def pdf_to_images(pdf_path: str, output_dir: str) -> list[str]:
    """Convert PDF pages to JPEG images. Requires poppler-utils + pdf2image."""
    try:
        from pdf2image import convert_from_path
    except ImportError:
        print("ERROR: pdf2image not installed. Run: bash setup_pamphlet.sh")
        print("       Also needs poppler: sudo apt-get install -y poppler-utils")
        sys.exit(1)

    print(f"  Converting PDF to images...")
    os.makedirs(output_dir, exist_ok=True)
    pages = convert_from_path(pdf_path, dpi=200, fmt="jpeg")
    paths = []
    for i, page in enumerate(pages):
        out = os.path.join(output_dir, f"page_{i+1:03d}.jpg")
        page.save(out, "JPEG", quality=90)
        paths.append(out)
        print(f"    Page {i+1}/{len(pages)} → {out}")
    return paths


def get_image_paths(source: str) -> list[str]:
    """Return list of image file paths from a file or directory."""
    source = Path(source)
    supported = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

    if source.is_dir():
        paths = sorted([
            str(p) for p in source.iterdir()
            if p.suffix.lower() in supported
        ])
        if not paths:
            print(f"ERROR: No images found in {source}")
            sys.exit(1)
        return paths

    if source.suffix.lower() == ".pdf":
        tmp_dir = source.parent / f"_pdf_pages_{source.stem}"
        return pdf_to_images(str(source), str(tmp_dir))

    if source.suffix.lower() in supported:
        return [str(source)]

    print(f"ERROR: Unsupported source: {source}")
    sys.exit(1)


def calculate_retail_price(cost: float, markup_pct: float, vat_pct: float = 0) -> float:
    """Apply markup % then optional VAT % to a cost price."""
    if not cost or cost <= 0:
        return 0.0
    price = cost * (1 + markup_pct / 100)
    if vat_pct > 0:
        price *= (1 + vat_pct / 100)
    return round(price, 2)


def find_product_image(name: str, brand: str, model: str) -> str:
    """
    Find the best product image URL by searching:
    1. Takealot (exact model number)
    2. Amazon (exact model number)
    3. Brand/manufacturer website
    4. General DuckDuckGo image search fallback
    """
    if not DDG_AVAILABLE or not REQUESTS_AVAILABLE:
        return ""

    model_clean = (model or "").strip()
    brand_clean = (brand or "").strip()
    name_clean  = (name  or "").strip()

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }

    # ── Priority 1: Takealot (South African — search by exact model) ──────────
    if model_clean:
        try:
            search_term = f"{brand_clean} {model_clean}".strip()
            url = f"https://www.takealot.com/all?qsearch={requests.utils.quote(search_term)}"
            resp = requests.get(url, headers=HEADERS, timeout=8)
            if resp.status_code == 200:
                # Extract first product image from Takealot response
                img_matches = re.findall(
                    r'"image(?:Url|_url|)":\s*"(https://[^"]+takealot[^"]+\.(?:jpg|jpeg|png|webp))"',
                    resp.text, re.IGNORECASE
                )
                if not img_matches:
                    # Try og:image or product image tags
                    img_matches = re.findall(
                        r'<img[^>]+src="(https://[^"]+(?:takealot|cdn\.dream)[^"]+\.(?:jpg|jpeg|png|webp))"',
                        resp.text, re.IGNORECASE
                    )
                if img_matches:
                    return img_matches[0]
        except Exception:
            pass

    # ── Priority 2: Amazon (search by exact model number) ────────────────────
    if model_clean:
        try:
            search_term = f"{brand_clean} {model_clean}".strip()
            url = f"https://www.amazon.com/s?k={requests.utils.quote(search_term)}"
            resp = requests.get(url, headers=HEADERS, timeout=8)
            if resp.status_code == 200:
                img_matches = re.findall(
                    r'"hiRes"\s*:\s*"(https://m\.media-amazon\.com/images/[^"]+\.jpg)"',
                    resp.text
                )
                if not img_matches:
                    img_matches = re.findall(
                        r'<img[^>]+src="(https://m\.media-amazon\.com/images/[^"]+\.jpg)"[^>]+data-image-index',
                        resp.text
                    )
                if img_matches:
                    return img_matches[0]
        except Exception:
            pass

    # ── Priority 3: DuckDuckGo image search (model + site priority) ──────────
    if not DDG_AVAILABLE:
        return ""

    # Build queries from most specific to least specific
    queries = []
    if model_clean:
        queries.append(f'"{model_clean}" {brand_clean} product image site:takealot.com OR site:amazon.com OR site:{brand_clean.lower().replace(" ", "")}.com')
        queries.append(f'{brand_clean} {model_clean} official product image white background')
    queries.append(f'{brand_clean} {name_clean} product image')

    preferred_domains = [
        "takealot.com", "amazon.com", "amazon.co.za",
        brand_clean.lower().replace(" ", "") + ".com",
        brand_clean.lower().replace(" ", "") + ".co.za",
    ]

    for query in queries:
        try:
            with DDGS() as ddgs:
                results = list(ddgs.images(query, max_results=10, type_image="photo"))
            if not results:
                continue

            # Score results: prefer exact model match + preferred domains
            scored = []
            for r in results:
                img_url = r.get("image", "")
                src_url = r.get("url", "")
                score = 0
                combined = (img_url + " " + src_url).lower()

                # Exact model number match in URL = highest score
                if model_clean and model_clean.lower() in combined:
                    score += 10
                # Preferred domain match
                for domain in preferred_domains:
                    if domain.lower() in combined:
                        score += 5
                        break
                # Brand name in URL
                if brand_clean and brand_clean.lower().replace(" ", "") in combined:
                    score += 3
                # Avoid social media, news sites
                for bad in ["pinterest", "facebook", "twitter", "instagram", "youtube"]:
                    if bad in combined:
                        score -= 5

                scored.append((score, img_url))

            scored.sort(key=lambda x: -x[0])
            best_url = scored[0][1] if scored else ""
            if best_url:
                return best_url

        except Exception as e:
            print(f"    [Image search warning: {e}]")
            continue

    return ""


# ── CLAUDE API CALLS ──────────────────────────────────────────────────────────

def extract_products_from_image(image_path: str, client: "anthropic.Anthropic") -> list[dict]:
    """Use Claude Vision to extract all products from a catalogue image."""
    print(f"  Extracting products from: {Path(image_path).name}")
    b64_data, media_type = load_image_as_base64(image_path)

    for attempt in range(MAX_RETRIES):
        try:
            message = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=4096,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": b64_data,
                            },
                        },
                        {
                            "type": "text",
                            "text": EXTRACT_PROMPT,
                        }
                    ],
                }]
            )
            response_text = message.content[0].text
            data = parse_json_response(response_text)
            products = data.get("products", [])
            print(f"    Found {len(products)} product(s)")
            return products

        except json.JSONDecodeError as e:
            print(f"    [JSON parse error on attempt {attempt+1}: {e}]")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
        except anthropic.RateLimitError:
            wait = RETRY_DELAY * (2 ** attempt)
            print(f"    [Rate limited — waiting {wait}s]")
            time.sleep(wait)
        except anthropic.APIError as e:
            print(f"    [API error on attempt {attempt+1}: {e}]")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)

    print(f"    [Failed to extract products from {Path(image_path).name} after {MAX_RETRIES} attempts]")
    return []


def generate_seo_content(product: dict, client: "anthropic.Anthropic") -> dict:
    """Use Claude to generate SEO-optimised content for a product."""
    product_json = json.dumps(product, indent=2, ensure_ascii=False)
    prompt = SEO_PROMPT.format(product_json=product_json)

    for attempt in range(MAX_RETRIES):
        try:
            message = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=2048,
                messages=[{
                    "role": "user",
                    "content": prompt,
                }]
            )
            response_text = message.content[0].text
            return parse_json_response(response_text)

        except json.JSONDecodeError as e:
            print(f"    [SEO JSON parse error on attempt {attempt+1}: {e}]")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
        except anthropic.RateLimitError:
            wait = RETRY_DELAY * (2 ** attempt)
            print(f"    [Rate limited — waiting {wait}s]")
            time.sleep(wait)
        except anthropic.APIError as e:
            print(f"    [SEO API error on attempt {attempt+1}: {e}]")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)

    return {}


# ── ROW BUILDER ───────────────────────────────────────────────────────────────

def build_sku(brand: str, model: str, index: int) -> str:
    """Generate a clean SKU from brand + model."""
    brand_part = re.sub(r'[^A-Z0-9]', '', (brand or "PROD").upper())[:6]
    model_part = re.sub(r'[^A-Z0-9]', '', (model or str(index)).upper())[:12]
    if not model_part:
        model_part = f"{index:04d}"
    return f"{brand_part}-{model_part}"


def build_wc_row(
    product: dict,
    seo: dict,
    image_url: str,
    markup_pct: float,
    vat_pct: float,
    row_index: int,
) -> dict:
    """Map extracted product + SEO data into a WooCommerce import CSV row."""

    brand    = (product.get("brand") or "").strip()
    model    = (product.get("model") or "").strip()
    sku      = (product.get("sku") or "").strip() or build_sku(brand, model, row_index)
    specs    = product.get("specifications") or {}
    features = product.get("features") or []

    # Name: use SEO name if available, else raw product name
    name = (seo.get("seo_name") or product.get("name") or "Unnamed Product").strip()

    # Price
    cost = float(product.get("supplier_price_ex_vat") or 0)
    retail_price = calculate_retail_price(cost, markup_pct, vat_pct)

    # Descriptions
    short_desc = (seo.get("short_description") or product.get("description_raw") or "")[:300]
    description = seo.get("description") or ""
    if not description and product.get("description_raw"):
        description = f"<p>{product['description_raw']}</p>"

    # Attributes: colour as Attribute 1, model as Attribute 2
    colour = (
        product.get("colour") or
        specs.get("colour") or
        ""
    ).strip()

    # Tags
    tags = seo.get("tags") or product.get("tags") or []
    if isinstance(tags, list):
        tags_str = ", ".join(str(t) for t in tags)
    else:
        tags_str = str(tags)

    # Category
    category = (
        seo.get("category_path") or
        product.get("category") or
        "Uncategorised"
    )
    if product.get("sub_category") and ">" not in category:
        category = f"{category} > {product['sub_category']}"

    # Meta
    meta_title = (seo.get("meta_title") or name)[:60]
    meta_desc  = (seo.get("meta_description") or short_desc)[:160]
    meta_kw    = seo.get("meta_keywords") or ""
    focus_kw   = seo.get("focus_keyword") or ""
    yoast_title = seo.get("yoast_title") or "%%title%% - %%sitename%%"

    row = {col: "" for col in WC_COLUMNS}
    row.update({
        "ID":                        "",
        "Type":                      "simple",
        "SKU":                       sku,
        "Name":                      name,
        "Published":                 "1",
        "Is featured?":              "0",
        "Visibility in catalog":     "visible",
        "Short description":         short_desc,
        "Description":               description,
        "Tax status":                "taxable",
        "Tax class":                 "",
        "In stock?":                 "1",
        "Stock":                     "10",
        "Low stock amount":          "2",
        "Backorders allowed?":       "0",
        "Sold individually?":        "0",
        "Weight (kg)":               str(specs.get("weight_kg") or ""),
        "Length (cm)":               str(specs.get("length_cm") or ""),
        "Width (cm)":                str(specs.get("width_cm") or ""),
        "Height (cm)":               str(specs.get("height_cm") or ""),
        "Allow customer reviews?":   "1",
        "Regular price":             str(retail_price) if retail_price > 0 else "",
        "Sale price":                "",
        "Categories":                category,
        "Tags":                      tags_str,
        "Images":                    image_url,
        "Attribute 1 name":          "Brand" if brand else "",
        "Attribute 1 value(s)":      brand,
        "Attribute 1 visible":       "1" if brand else "",
        "Attribute 1 global":        "1" if brand else "",
        "Attribute 2 name":          "Colour" if colour else "",
        "Attribute 2 value(s)":      colour,
        "Attribute 2 visible":       "1" if colour else "",
        "Attribute 2 global":        "1" if colour else "",
        "Attribute 3 name":          "Model" if model else "",
        "Attribute 3 value(s)":      model,
        "Attribute 3 visible":       "1" if model else "",
        "Attribute 3 global":        "0" if model else "",
        "Meta: title":               meta_title,
        "Meta: description":         meta_desc,
        "Meta: keywords":            meta_kw,
        "_yoast_wpseo_title":        yoast_title,
        "_yoast_wpseo_metadesc":     meta_desc,
        "_yoast_wpseo_focuskw":      focus_kw,
        "Brands":                    brand,
    })
    return row


# ── MAIN PIPELINE ─────────────────────────────────────────────────────────────

def process_all(
    source: str,
    markup_pct: float,
    vat_pct: float,
    output_csv: str,
    api_key: str,
    skip_images: bool = False,
    skip_seo: bool = False,
):
    """Full pipeline: images → extract → SEO → image search → CSV."""

    print(f"\n{'='*60}")
    print(f"  Pamphlet → WooCommerce Import Tool")
    print(f"{'='*60}")
    print(f"  Source:   {source}")
    print(f"  Markup:   {markup_pct}%")
    print(f"  VAT:      {vat_pct}%")
    print(f"  Output:   {output_csv}")
    print(f"{'='*60}\n")

    # Initialise Anthropic client
    client = anthropic.Anthropic(api_key=api_key)

    # Get all image paths
    image_paths = get_image_paths(source)
    print(f"Processing {len(image_paths)} image(s)...\n")

    all_rows = []
    total_products = 0
    row_index = 1

    for img_num, image_path in enumerate(image_paths, 1):
        print(f"[{img_num}/{len(image_paths)}] {Path(image_path).name}")

        # Step 1: Extract products via Claude Vision
        products = extract_products_from_image(image_path, client)
        if not products:
            print(f"  No products found, skipping.\n")
            continue

        for product in products:
            product_name = product.get("name", f"Product {row_index}")
            brand  = product.get("brand", "")
            model  = product.get("model", "")
            cost   = product.get("supplier_price_ex_vat", 0)
            retail = calculate_retail_price(float(cost or 0), markup_pct, vat_pct)

            print(f"  → {product_name}")
            print(f"     Brand: {brand}  |  Model: {model}  |  Cost: R{cost}  |  Retail: R{retail}")

            # Step 2: Generate SEO content
            seo = {}
            if not skip_seo:
                print(f"     Generating SEO content...")
                seo = generate_seo_content(product, client)
                if seo.get("meta_title"):
                    print(f"     SEO title: {seo['meta_title'][:55]}...")

            # Step 3: Find product image online
            image_url = ""
            if not skip_images and DDG_AVAILABLE:
                print(f"     Searching for product image...")
                image_url = find_product_image(product_name, brand, model)
                if image_url:
                    print(f"     Image found: {image_url[:80]}...")
                else:
                    print(f"     No image found online")

            # Step 4: Build WooCommerce row
            row = build_wc_row(product, seo, image_url, markup_pct, vat_pct, row_index)
            all_rows.append(row)
            row_index += 1
            total_products += 1

            # Small delay to avoid rate limiting
            time.sleep(0.5)

        print()

    # Step 5: Write CSV
    if not all_rows:
        print("No products extracted. Check your images and try again.")
        sys.exit(1)

    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=WC_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"\n{'='*60}")
    print(f"  COMPLETE")
    print(f"{'='*60}")
    print(f"  Products extracted:  {total_products}")
    print(f"  Images processed:    {len(image_paths)}")
    print(f"  Output file:         {output_csv}")
    print(f"\n  NEXT STEPS:")
    print(f"  1. Open your WooCommerce admin")
    print(f"  2. Products > Import")
    print(f"  3. Upload: {output_csv}")
    print(f"  4. Map columns and click Run Import")
    print(f"{'='*60}\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="Pamphlet → WooCommerce Import CSV Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 pamphlet_to_woocommerce.py --input ./pamphlet_images --markup 40
  python3 pamphlet_to_woocommerce.py --input catalogue.pdf --markup 35 --vat 15
  python3 pamphlet_to_woocommerce.py --input ./images --markup 40 --output my_products.csv
  python3 pamphlet_to_woocommerce.py --input ./images --markup 40 --no-images --no-seo
        """
    )
    parser.add_argument(
        "--input", "-i", required=True,
        help="Folder of images, a single image, or a PDF file"
    )
    parser.add_argument(
        "--markup", "-m", type=float, default=40,
        help="Markup percentage to add to supplier price (default: 40 = 40%%)"
    )
    parser.add_argument(
        "--vat", type=float, default=0,
        help="VAT percentage to add on top of marked-up price (default: 0 = prices already incl. VAT)"
    )
    parser.add_argument(
        "--output", "-o", default="woocommerce_import.csv",
        help="Output CSV filename (default: woocommerce_import.csv)"
    )
    parser.add_argument(
        "--api-key", default=None,
        help="Anthropic API key (overrides ANTHROPIC_API_KEY env var / .env file)"
    )
    parser.add_argument(
        "--no-images", action="store_true",
        help="Skip searching for product images online (faster)"
    )
    parser.add_argument(
        "--no-seo", action="store_true",
        help="Skip SEO content generation (faster, but no descriptions or meta tags)"
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # Resolve API key: CLI arg > .env > environment variable
    api_key = (
        args.api_key or
        os.environ.get("ANTHROPIC_API_KEY", "")
    )
    if not api_key:
        print("\nERROR: No Anthropic API key found.")
        print("  Option 1: Add ANTHROPIC_API_KEY=sk-ant-... to your .env file")
        print("  Option 2: export ANTHROPIC_API_KEY=sk-ant-...")
        print("  Option 3: Pass --api-key sk-ant-... on the command line")
        print("\n  Get a key at: https://console.anthropic.com/settings/keys\n")
        sys.exit(1)

    process_all(
        source=args.input,
        markup_pct=args.markup,
        vat_pct=args.vat,
        output_csv=args.output,
        api_key=api_key,
        skip_images=args.no_images,
        skip_seo=args.no_seo,
    )


if __name__ == "__main__":
    main()
