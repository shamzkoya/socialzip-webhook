#!/usr/bin/env python3
"""
============================================================
Hirschs Supplier CSV → WooCommerce Sync
============================================================
Takes the Hirschs supplier CSV (already extracted from pamphlet)
and produces a full WooCommerce import with:

  1. Market-based pricing (Takealot, Makro, Game research)
     — ensures minimum margin over Hirsch cost price
  2. SEO titles, descriptions, meta tags (Claude AI)
  3. Product images (Takealot, brand sites, DuckDuckGo)
  4. Full WooCommerce import CSV ready to upload

USAGE:
  python3 hirsch_csv_sync.py \
    --supplier /path/to/hirsch_supplier.csv \
    --out      /path/to/output/ \
    --min-margin 20

  # Resume after interruption:
  python3 hirsch_csv_sync.py --supplier ... --out ... --resume

INPUT CSV columns expected:
  Supplier, Brand, Product Name, Model, SN/SKU, Sale Price, Source

OUTPUTS:
  hirsch_wc_import.csv       — WooCommerce import (use SKU to match existing)
  hirsch_pricing_report.csv  — Margin breakdown per product
  hirsch_missing_images.csv  — Products needing manual images
  hirsch_progress.json       — Progress checkpoint (for --resume)
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
    from ddgs import DDGS
    DDG_AVAILABLE = True
except ImportError:
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

# ── Config ────────────────────────────────────────────────────────────────────
CLAUDE_MODEL = "claude-haiku-4-5-20251001"
MAX_RETRIES  = 3
RETRY_DELAY  = 6
SUPPLIER     = "Hirschs"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
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
    "Categories", "Tags", "Shipping class", "Images",
    "Upsells", "Cross-sells", "External URL", "Button text", "Position",
    "Attribute 1 name", "Attribute 1 value(s)", "Attribute 1 visible", "Attribute 1 global",
    "Attribute 2 name", "Attribute 2 value(s)", "Attribute 2 visible", "Attribute 2 global",
    "Attribute 3 name", "Attribute 3 value(s)", "Attribute 3 visible", "Attribute 3 global",
    "Meta: title", "Meta: description", "Meta: keywords",
    "_yoast_wpseo_title", "_yoast_wpseo_metadesc", "_yoast_wpseo_focuskw",
    "Brands",
]

# ── Prompts ───────────────────────────────────────────────────────────────────
SEO_PROMPT = """You are an SEO copywriter for a South African online appliance and electronics store.

Generate compelling WooCommerce product content for this item.

PRODUCT DATA:
{product_json}

Return ONLY a valid JSON object — no markdown, no extra text:

{{
  "seo_name": "Product listing name — brand + model + key spec, under 80 chars",
  "short_description": "One compelling sentence (120-160 chars) highlighting top 2 benefits. No HTML.",
  "description": "Full HTML: <h2>Overview</h2> 2 paragraphs, <h3>Key Features</h3> <ul><li> list, <h3>Specifications</h3> <ul> list of specs, <p> call-to-action. Use keywords naturally.",
  "meta_title": "SEO title: brand + model + category + key benefit (55-60 chars max)",
  "meta_description": "Meta description with CTA (145-160 chars). Include brand, model, top benefit.",
  "meta_keywords": "8 comma-separated keywords including brand, model, category, use case",
  "focus_keyword": "Primary 3-5 word keyword phrase e.g. 'LG 65 inch OLED TV South Africa'",
  "yoast_title": "%%title%% - %%sitename%%",
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5"],
  "category_path": "Parent Category > Sub Category",
  "inferred_specs": {{
    "weight_kg": null,
    "length_cm": null,
    "width_cm": null,
    "height_cm": null,
    "energy_rating": null,
    "warranty": null
  }}
}}

Rules:
- South African English ('colour' not 'color', 'litre' not 'liter')
- Include model number in title, h2, and focus keyword for SEO
- Be conversion-focused and practical
- Infer reasonable specs from the product name/category if not provided
- Return ONLY the JSON object
"""

# ── Market Price Research ─────────────────────────────────────────────────────
def research_market_price(brand: str, model: str, name: str) -> dict:
    if not REQUESTS_AVAILABLE:
        return {"best_price": 0, "source": "", "all_prices": []}

    found = []
    search_term = f"{brand} {model}".strip() if model else f"{brand} {name}"

    # Takealot
    try:
        url = f"https://www.takealot.com/all?qsearch={requests.utils.quote(search_term)}"
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code == 200:
            prices = re.findall(r'"buyBoxPrice"\s*:\s*(\d+(?:\.\d+)?)', r.text)
            if not prices:
                prices = re.findall(r'data-ref="listing-price[^"]*"[^>]*>\s*R\s*([\d\s,]+)', r.text)
            for p in prices[:3]:
                val = float(str(p).replace(",","").replace(" ",""))
                if 100 < val < 1000000:
                    found.append({"price": val, "source": "Takealot"})
    except Exception:
        pass

    # Makro
    try:
        url = f"https://www.makro.co.za/search?q={requests.utils.quote(search_term)}"
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code == 200:
            prices = re.findall(r'"price"\s*:\s*"?(\d{3,7}(?:\.\d+)?)"?', r.text)
            for p in prices[:3]:
                val = float(p)
                if 100 < val < 1000000:
                    found.append({"price": val, "source": "Makro"})
    except Exception:
        pass

    # Game
    try:
        url = f"https://www.game.co.za/en/search/?text={requests.utils.quote(search_term)}"
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code == 200:
            prices = re.findall(r'"price"\s*:\s*(\d{3,7}(?:\.\d+)?)', r.text)
            for p in prices[:3]:
                val = float(p)
                if 100 < val < 1000000:
                    found.append({"price": val, "source": "Game"})
    except Exception:
        pass

    # DuckDuckGo fallback
    if not found and DDG_AVAILABLE and model:
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(f"{brand} {model} price south africa", max_results=5))
            for res in results:
                text = res.get("body","") + res.get("title","")
                for p in re.findall(r'R\s*(\d{1,3}(?:[,\s]\d{3})*(?:\.\d{2})?)', text):
                    val = float(p.replace(",","").replace(" ",""))
                    if 100 < val < 1000000:
                        found.append({"price": val, "source": "Web"})
        except Exception:
            pass

    if not found:
        return {"best_price": 0, "source": "", "all_prices": []}

    sorted_found = sorted(found, key=lambda x: x["price"])
    mid = sorted_found[len(sorted_found)//2]
    return {"best_price": mid["price"], "source": mid["source"], "all_prices": found}


def set_retail_price(cost: float, market_price: float, min_margin: float) -> tuple[float, str]:
    if not cost or cost <= 0:
        return 0.0, "no_cost"
    floor = round(cost * (1 + min_margin / 100), 2)
    if market_price and market_price > 0:
        margin = (market_price - cost) / cost * 100
        if margin >= min_margin:
            return round(market_price, 2), f"market ({margin:.1f}% margin)"
        return floor, f"floor (market only {margin:.1f}%)"
    return round(cost * (1 + (min_margin + 5) / 100), 2), "estimated"


# ── Image Search ──────────────────────────────────────────────────────────────
def find_image(brand: str, model: str, name: str) -> str:
    if not DDG_AVAILABLE or not REQUESTS_AVAILABLE:
        return ""

    # Takealot first
    try:
        q = f"{brand} {model}".strip() if model else f"{brand} {name}"
        r = requests.get(f"https://www.takealot.com/all?qsearch={requests.utils.quote(q)}",
                         headers=HEADERS, timeout=8)
        if r.status_code == 200:
            imgs = re.findall(
                r'"(?:image|imageUrl)":\s*"(https://[^"]+(?:takealot|dream)[^"]+\.(?:jpg|jpeg|png|webp))"',
                r.text, re.IGNORECASE)
            if imgs:
                return imgs[0]
    except Exception:
        pass

    queries = []
    if model:
        queries.append(f'"{model}" {brand} official product image')
        queries.append(f'{brand} {model} buy south africa')
    queries.append(f'{brand} {name[:50]} product image white background')

    for query in queries:
        try:
            with DDGS() as ddgs:
                results = list(ddgs.images(query, max_results=8, type_image="photo"))
            if not results:
                continue
            scored = []
            for res in results:
                img = res.get("image","")
                src = res.get("url","")
                score = 0
                combined = (img + src).lower()
                if model and model.lower() in combined: score += 10
                if brand.lower() in combined: score += 3
                for d in ["takealot.com","makro.co.za","game.co.za"]:
                    if d in combined: score += 5; break
                for bad in ["pinterest","facebook","twitter","instagram","youtube"]:
                    if bad in combined: score -= 5
                scored.append((score, img))
            scored.sort(key=lambda x: -x[0])
            if scored and scored[0][1]:
                return scored[0][1]
        except Exception:
            pass
    return ""


# ── Claude SEO ────────────────────────────────────────────────────────────────
def parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r'^```(?:json)?\s*\n?', '', text)
        text = re.sub(r'\n?```\s*$', '', text)
    return json.loads(text.strip())


def generate_seo(product_data: dict, client) -> dict:
    prompt = SEO_PROMPT.format(product_json=json.dumps(product_data, indent=2))
    for attempt in range(MAX_RETRIES):
        try:
            msg = client.messages.create(
                model=CLAUDE_MODEL, max_tokens=2048,
                messages=[{"role": "user", "content": prompt}]
            )
            return parse_json(msg.content[0].text)
        except Exception as e:
            print(f"    [SEO error {attempt+1}: {e}]")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
    return {}


# ── WC Row ────────────────────────────────────────────────────────────────────
def build_row(product: dict, retail: float, seo: dict, img: str) -> dict:
    specs = seo.get("inferred_specs") or {}
    tags  = seo.get("tags") or []
    if isinstance(tags, list):
        tags = ", ".join(tags)
    sku = (product.get("Model") or product.get("SN/SKU") or "").strip()

    return {
        "ID":                       "",
        "Type":                     "simple",
        "SKU":                      sku,
        "Name":                     seo.get("seo_name") or f"{product['Brand']} {product['Product Name']}",
        "Published":                1,
        "Is featured?":             0,
        "Visibility in catalog":    "visible",
        "Short description":        seo.get("short_description") or "",
        "Description":              seo.get("description") or "",
        "Date sale price starts":   "",
        "Date sale price ends":     "",
        "Tax status":               "taxable",
        "Tax class":                "",
        "In stock?":                1,
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
        "Regular price":            retail if retail > 0 else "",
        "Categories":               seo.get("category_path") or "Appliances",
        "Tags":                     tags,
        "Shipping class":           "",
        "Images":                   img,
        "Upsells":                  "",
        "Cross-sells":              "",
        "External URL":             "",
        "Button text":              "",
        "Position":                 "",
        "Attribute 1 name":         "Brand",
        "Attribute 1 value(s)":     product.get("Brand") or "",
        "Attribute 1 visible":      1,
        "Attribute 1 global":       1,
        "Attribute 2 name":         "Model" if sku else "",
        "Attribute 2 value(s)":     sku,
        "Attribute 2 visible":      1 if sku else "",
        "Attribute 2 global":       "",
        "Attribute 3 name":         "",
        "Attribute 3 value(s)":     "",
        "Attribute 3 visible":      "",
        "Attribute 3 global":       "",
        "Meta: title":              seo.get("meta_title") or "",
        "Meta: description":        seo.get("meta_description") or "",
        "Meta: keywords":           seo.get("meta_keywords") or "",
        "_yoast_wpseo_title":       seo.get("yoast_title") or "",
        "_yoast_wpseo_metadesc":    seo.get("meta_description") or "",
        "_yoast_wpseo_focuskw":     seo.get("focus_keyword") or "",
        "Brands":                   product.get("Brand") or SUPPLIER,
    }


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--supplier",   required=True, help="Hirsch supplier CSV")
    parser.add_argument("--out",        default=".",   help="Output directory")
    parser.add_argument("--min-margin", type=float, default=20.0, help="Min margin %%")
    parser.add_argument("--resume",     action="store_true")
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    out_dir      = Path(args.out)
    progress_path= out_dir / "hirsch_progress.json"
    wc_path      = out_dir / "hirsch_wc_import.csv"
    pricing_path = out_dir / "hirsch_pricing_report.csv"
    missing_path = out_dir / "hirsch_missing_images.csv"

    api_key = os.environ.get("ANTHROPIC_API_KEY","")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set"); sys.exit(1)
    client = anthropic.Anthropic(api_key=api_key)

    # Load supplier CSV
    with open(args.supplier, encoding="utf-8-sig") as f:
        products = list(csv.DictReader(f))
    print(f"Loaded {len(products)} Hirsch products")

    # Load progress checkpoint
    done = {}
    if args.resume and progress_path.exists():
        with open(progress_path) as f:
            done = json.load(f)
        print(f"Resuming — {len(done)} already processed")

    wc_rows      = []
    pricing_rows = []
    missing_imgs = []

    for i, product in enumerate(products, 1):
        brand = product.get("Brand","").strip()
        model = product.get("Model","").strip()
        name  = product.get("Product Name","").strip()
        sku   = product.get("SN/SKU","").strip()
        key   = model or sku or name

        print(f"\n[{i}/{len(products)}] {brand} {model or sku} — {name[:45]}")

        # Use cached result if resuming
        if args.resume and key in done:
            cached = done[key]
            wc_rows.append(cached["wc_row"])
            pricing_rows.append(cached["pricing"])
            if not cached["wc_row"].get("Images"):
                missing_imgs.append({"brand": brand, "model": model, "name": name, "sku": sku})
            print(f"  (cached) R{cached['pricing']['retail_price']}")
            continue

        cost = 0.0
        try:
            cost = float(str(product.get("Sale Price","0")).replace(",","").replace("R","").strip())
        except Exception:
            pass

        # Market price research
        print(f"  Cost: R{cost:,.2f} | Researching market...")
        market = research_market_price(brand, model, name)
        retail, method = set_retail_price(cost, market["best_price"], args.min_margin)
        margin = ((retail - cost) / cost * 100) if cost > 0 else 0
        print(f"  Market: R{market['best_price']:,.2f} ({market['source']}) → Retail: R{retail:,.2f} ({margin:.1f}%)")

        # SEO
        seo_input = {
            "brand": brand, "model": model, "name": name, "sku": sku,
            "hirsch_cost_zar": cost, "retail_price_zar": retail,
            "market_price_zar": market["best_price"],
            "margin_pct": round(margin, 1),
            "source": product.get("Source",""),
        }
        print(f"  Generating SEO...")
        seo = generate_seo(seo_input, client)

        # Image
        print(f"  Finding image...")
        img = find_image(brand, model, name)
        if img:
            print(f"  Image: {img[:70]}...")
        else:
            print(f"  No image found")
            missing_imgs.append({"brand": brand, "model": model, "name": name, "sku": sku})

        wc_row  = build_row(product, retail, seo, img)
        pricing = {
            "brand": brand, "model": model, "name": name,
            "hirsch_cost": cost, "market_price": market["best_price"],
            "market_source": market.get("source",""),
            "retail_price": retail, "margin_pct": round(margin,1),
            "method": method,
        }
        wc_rows.append(wc_row)
        pricing_rows.append(pricing)

        # Save checkpoint
        done[key] = {"wc_row": wc_row, "pricing": pricing}
        with open(progress_path, "w") as f:
            json.dump(done, f, indent=2)

        time.sleep(0.5)

    # Write outputs
    with open(wc_path, "w", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=WC_COLUMNS).writeheader()
        csv.DictWriter(f, fieldnames=WC_COLUMNS).writerows(wc_rows)

    pricing_fields = ["brand","model","name","hirsch_cost","market_price",
                      "market_source","retail_price","margin_pct","method"]
    with open(pricing_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=pricing_fields)
        w.writeheader(); w.writerows(pricing_rows)

    with open(missing_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["brand","model","name","sku"])
        w.writeheader(); w.writerows(missing_imgs)

    avg_margin = sum(r["margin_pct"] for r in pricing_rows if r["hirsch_cost"] > 0) / max(1, sum(1 for r in pricing_rows if r["hirsch_cost"] > 0))

    print(f"""
============================================================
DONE
  Products processed   : {len(wc_rows)}
  With images          : {len(wc_rows) - len(missing_imgs)}
  Missing images       : {len(missing_imgs)}
  Average margin       : {avg_margin:.1f}%

  WooCommerce import   : {wc_path}
  Pricing report       : {pricing_path}
  Missing images       : {missing_path}

Import steps:
  WooCommerce > Products > Import > Upload hirsch_wc_import.csv
  ✓ Tick "Update existing products"
  ✓ Map SKU column — WC will update products with matching SKU
============================================================
""")


if __name__ == "__main__":
    main()
