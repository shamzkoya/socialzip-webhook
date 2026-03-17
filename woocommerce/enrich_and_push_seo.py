#!/usr/bin/env python3
"""
Enrich all products in master_wc_import.csv with:
  - Prices from supplier data (where missing)
  - Full SEO: description, short description, meta title, meta desc, keywords
Then push updates to WooCommerce via REST API.
"""

import csv
import json
import os
import sys
import time
import re
from pathlib import Path

# Load .env before importing anthropic
def _load_dotenv():
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
_load_dotenv()

import anthropic
import requests
from requests.auth import HTTPBasicAuth

# ─── Config ─────────────────────────────────────────────────────────────────
WC_URL   = "https://happyharvesting.co.za/wp-json/wc/v3"
WC_KEY   = "ck_d5322bdf8c16388bb4adcc573d7a0417b5ad0938"
WC_SEC   = "cs_f75a2e8162382574310656a14cb368e823c3caf4"
WC_AUTH  = HTTPBasicAuth(WC_KEY, WC_SEC)

BASE     = Path("/home/user/socialzip-webhook/woocommerce")
STOCK    = BASE / "stock_control"

INPUT_CSV    = STOCK / "master_wc_import.csv"
OUTPUT_CSV   = STOCK / "master_wc_enriched.csv"
PROGRESS_F   = STOCK / "seo_enrich_progress.json"
ERRORS_F     = STOCK / "seo_enrich_errors.json"

CLAUDE_CLIENT = anthropic.Anthropic()
SEO_BATCH_SIZE = 10   # products per Claude call
WC_BATCH_SIZE  = 50  # products per WC API batch update

# ─── Price lookups ────────────────────────────────────────────────────────────

def build_price_lookups():
    """Build SKU → price maps from all supplier sources."""
    price_map = {}  # sku (lower) → price

    # 1. Hirschs wc_import (has prices by SKU)
    hirsch_f = STOCK / "hirsch_wc_import.csv"
    if hirsch_f.exists():
        with open(hirsch_f) as f:
            for row in csv.DictReader(f):
                sku = row.get("SKU", "").strip()
                price = row.get("Regular price", "").strip()
                if sku and price:
                    price_map[sku.lower()] = price

    # 2. Impulse stock_sheet.xlsx
    try:
        import openpyxl
        wb = openpyxl.load_workbook(
            BASE / "suppliers/impulse/stock_sheet.xlsx", read_only=True)
        ws = wb["Stock"]
        rows = list(ws.iter_rows(values_only=True))
        for r in rows[2:]:
            # cols: 0=SupplierSKU, 1=WCSKU, 11=SellingPrice
            wc_sku = str(r[1]).strip() if r[1] else ""
            sup_sku = str(r[0]).strip() if r[0] else ""
            price = r[11]
            if price and str(price).strip() and not str(price).startswith("="):
                try:
                    p = float(price)
                    if wc_sku:
                        price_map[wc_sku.lower()] = str(p)
                    if sup_sku:
                        price_map[sup_sku.lower()] = str(p)
                except (ValueError, TypeError):
                    pass
    except Exception as e:
        print(f"  [warn] Impulse sheet: {e}")

    print(f"  Price lookup: {len(price_map)} entries from supplier data")
    return price_map


# ─── SEO generation ──────────────────────────────────────────────────────────

def clean_name(name):
    """Titlecase and tidy product name."""
    return name.strip()


def generate_seo_batch(products):
    """Call Claude Haiku to generate SEO content for a batch of products.
    Returns list of dicts keyed by SKU."""

    lines = []
    for i, p in enumerate(products, 1):
        cat = p["Categories"].split(",")[0].strip().split(">")[-1].strip()
        lines.append(f"{i}. SKU:{p['SKU']} | {p['Name']} | Cat:{cat}")

    product_list = "\n".join(lines)

    prompt = f"""You are writing product content for Happy Harvesting (happyharvesting.co.za), \
a South African online store selling homeware, garden, electronics, appliances, fashion and lifestyle products.

Generate SEO-optimised product content for ALL {len(products)} products listed below.
Write for South African shoppers.

CRITICAL: Return ONLY a valid JSON array. No markdown fences. No explanation. No extra text.
All string values must be properly JSON-escaped (no unescaped quotes, no newlines in strings).
Use only plain text in description — no HTML tags (avoid <, > characters entirely).

Products:
{product_list}

For each product return an object with these exact keys:
- sku: exact SKU from input
- description: Product description 120-180 words, plain text only, no HTML, no angle brackets
- short_description: 1-2 compelling sentences, max 160 chars, plain text
- meta_title: SEO page title, max 58 chars, include main keyword
- meta_description: SEO meta description, max 150 chars, action-oriented
- keywords: 5-6 comma-separated search keywords for South African buyers
- focus_keyword: single best keyword phrase, 2-4 words"""

    for attempt in range(3):
        try:
            resp = CLAUDE_CLIENT.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=8000,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
            )
            text = resp.content[0].text.strip()
            # Strip markdown code fences if present
            if text.startswith("```"):
                text = re.sub(r"^```[a-z]*\n?", "", text)
                text = re.sub(r"\n?```$", "", text)
            data = json.loads(text)
            # Index by SKU
            return {item["sku"]: item for item in data}
        except json.JSONDecodeError as e:
            print(f"  [warn] JSON parse error attempt {attempt+1}: {e}")
            time.sleep(2)
        except anthropic.RateLimitError:
            print("  [rate limit] sleeping 30s...")
            time.sleep(30)
        except Exception as e:
            print(f"  [error] Claude attempt {attempt+1}: {e}")
            time.sleep(5)
    return {}


# ─── WooCommerce push ─────────────────────────────────────────────────────────

def wc_batch_update(updates):
    """POST a batch of product updates to WC. updates = list of dicts with 'id' key."""
    url = f"{WC_URL}/products/batch"
    for attempt in range(4):
        try:
            resp = requests.post(
                url,
                auth=WC_AUTH,
                json={"update": updates},
                timeout=60,
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                ok  = len(data.get("update", []))
                err = len(data.get("error", []))
                return ok, err, data.get("error", [])
            else:
                print(f"  [WC {resp.status_code}] {resp.text[:200]}")
                time.sleep(2 ** attempt)
        except Exception as e:
            print(f"  [WC error] {e}")
            time.sleep(2 ** attempt)
    return 0, len(updates), []


# ─── Load / save progress ─────────────────────────────────────────────────────

def load_progress():
    if PROGRESS_F.exists():
        with open(PROGRESS_F) as f:
            return json.load(f)
    return {"seo_done": [], "wc_pushed": [], "wc_errors": []}


def save_progress(prog):
    with open(PROGRESS_F, "w") as f:
        json.dump(prog, f, indent=2)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  Happy Harvesting — SEO Enrichment & WC Push")
    print("=" * 60)

    # Load all products
    with open(INPUT_CSV) as f:
        rows = list(csv.DictReader(f))
    fieldnames = list(rows[0].keys())
    print(f"\nLoaded {len(rows)} products from master_wc_import.csv")

    # Add any missing SEO columns
    for col in ["Meta: title", "Meta: description", "Meta: keywords",
                "_yoast_wpseo_title", "_yoast_wpseo_metadesc", "_yoast_wpseo_focuskw"]:
        if col not in fieldnames:
            fieldnames.append(col)
            for r in rows:
                r[col] = ""

    # ── Step 1: Price enrichment ──────────────────────────────────────────────
    print("\n[1/3] Enriching missing prices from supplier data...")
    price_map = build_price_lookups()

    price_filled = 0
    for row in rows:
        if not row.get("Regular price", "").strip():
            sku = row.get("SKU", "").strip().lower()
            if sku in price_map:
                row["Regular price"] = price_map[sku]
                price_filled += 1

    still_missing = sum(1 for r in rows if not r.get("Regular price", "").strip())
    print(f"  Prices filled from supplier data: {price_filled}")
    print(f"  Still missing price: {still_missing}")

    # ── Step 2: SEO generation ────────────────────────────────────────────────
    print("\n[2/3] Generating SEO content via Claude Haiku...")
    prog = load_progress()
    seo_done_set = set(prog["seo_done"])

    # Products needing SEO (missing description OR meta title)
    needs_seo = [
        r for r in rows
        if r.get("ID", "").strip()  # must have WC ID (existing product)
        and r.get("SKU", "").strip() not in seo_done_set
        and (
            not r.get("Description", "").strip()
            or not r.get("Meta: title", "").strip()
        )
    ]
    print(f"  Products needing SEO: {len(needs_seo)}")

    # Build SEO lookup dict for fast update
    seo_lookup = {}  # sku → seo dict

    total_batches = (len(needs_seo) + SEO_BATCH_SIZE - 1) // SEO_BATCH_SIZE
    for batch_num, i in enumerate(range(0, len(needs_seo), SEO_BATCH_SIZE), 1):
        batch = needs_seo[i : i + SEO_BATCH_SIZE]
        print(f"  SEO batch {batch_num}/{total_batches} ({len(batch)} products)...", end=" ", flush=True)

        seo_data = generate_seo_batch(batch)
        if not seo_data:
            print("FAILED (skipping)")
            continue

        for p in batch:
            sku = p["SKU"].strip()
            s = seo_data.get(sku) or seo_data.get(sku.upper()) or seo_data.get(sku.lower())
            if s:
                seo_lookup[sku] = s
                seo_done_set.add(sku)

        prog["seo_done"] = list(seo_done_set)
        save_progress(prog)
        print(f"OK ({len(seo_data)} enriched)")

        # Avoid hammering Claude
        time.sleep(1)

    # Apply SEO to rows
    applied = 0
    for row in rows:
        sku = row.get("SKU", "").strip()
        s = seo_lookup.get(sku)
        if not s:
            continue
        if not row.get("Description", "").strip():
            # Wrap plain-text paragraphs in <p> tags for WC
            desc = s.get("description", "").strip()
            if desc and not desc.startswith("<"):
                paras = [p.strip() for p in desc.split("\n\n") if p.strip()]
                desc = "".join(f"<p>{p}</p>" for p in paras) if paras else f"<p>{desc}</p>"
            row["Description"] = desc
        if not row.get("Short description", "").strip():
            row["Short description"] = s.get("short_description", "")
        if not row.get("Meta: title", "").strip():
            row["Meta: title"] = s.get("meta_title", "")[:60]
        if not row.get("Meta: description", "").strip():
            row["Meta: description"] = s.get("meta_description", "")[:155]
        if not row.get("Meta: keywords", "").strip():
            row["Meta: keywords"] = s.get("keywords", "")
        # Yoast fields
        if not row.get("_yoast_wpseo_title", "").strip():
            row["_yoast_wpseo_title"] = s.get("meta_title", "")[:60]
        if not row.get("_yoast_wpseo_metadesc", "").strip():
            row["_yoast_wpseo_metadesc"] = s.get("meta_description", "")[:155]
        if not row.get("_yoast_wpseo_focuskw", "").strip():
            row["_yoast_wpseo_focuskw"] = s.get("focus_keyword", "")
        applied += 1

    print(f"\n  SEO applied to {applied} products")

    # Save enriched CSV
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"  Saved enriched CSV → {OUTPUT_CSV}")

    # ── Step 3: Push to WooCommerce ───────────────────────────────────────────
    print("\n[3/3] Pushing updates to WooCommerce...")
    wc_pushed_set = set(str(x) for x in prog["wc_pushed"])

    # Only push products that have a WC ID
    pushable = [r for r in rows if r.get("ID", "").strip()
                and str(r["ID"]).strip() not in wc_pushed_set]
    print(f"  Products to push: {len(pushable)}")

    total_ok = 0
    total_err = 0
    all_errors = []
    wc_batches = (len(pushable) + WC_BATCH_SIZE - 1) // WC_BATCH_SIZE

    for batch_num, i in enumerate(range(0, len(pushable), WC_BATCH_SIZE), 1):
        batch = pushable[i : i + WC_BATCH_SIZE]
        updates = []
        for row in batch:
            upd = {"id": int(row["ID"])}
            if row.get("Regular price", "").strip():
                upd["regular_price"] = str(row["Regular price"])
            if row.get("Description", "").strip():
                upd["description"] = row["Description"]
            if row.get("Short description", "").strip():
                upd["short_description"] = row["Short description"]
            # SEO meta via meta_data
            meta = []
            if row.get("Meta: title", "").strip():
                meta.append({"key": "_yoast_wpseo_title",   "value": row["Meta: title"]})
            if row.get("Meta: description", "").strip():
                meta.append({"key": "_yoast_wpseo_metadesc", "value": row["Meta: description"]})
            if row.get("_yoast_wpseo_focuskw", "").strip():
                meta.append({"key": "_yoast_wpseo_focuskw",  "value": row["_yoast_wpseo_focuskw"]})
            if row.get("Meta: keywords", "").strip():
                meta.append({"key": "_yoast_wpseo_metakeywords", "value": row["Meta: keywords"]})
            if meta:
                upd["meta_data"] = meta

            if len(upd) > 1:  # has something beyond just id
                updates.append(upd)

        if not updates:
            continue

        print(f"  WC batch {batch_num}/{wc_batches} ({len(updates)} products)...", end=" ", flush=True)
        ok, err, errs = wc_batch_update(updates)
        total_ok += ok
        total_err += err
        all_errors.extend(errs)

        # Mark as pushed
        for row in batch:
            wc_pushed_set.add(str(row["ID"]))

        prog["wc_pushed"] = list(wc_pushed_set)
        if errs:
            prog["wc_errors"] = prog.get("wc_errors", []) + errs
        save_progress(prog)
        print(f"OK {ok} | ERR {err}")

        time.sleep(0.5)

    # Save error log
    if all_errors:
        with open(ERRORS_F, "w") as f:
            json.dump(all_errors, f, indent=2)

    print("\n" + "=" * 60)
    print(f"  DONE — WC updated: {total_ok} | errors: {total_err}")
    print(f"  Progress saved → {PROGRESS_F}")
    if total_err:
        print(f"  Errors logged → {ERRORS_F}")
    print("=" * 60)


if __name__ == "__main__":
    main()
