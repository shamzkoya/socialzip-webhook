#!/usr/bin/env python3
"""
Draft Incomplete Products
=========================
Finds all PUBLISHED WooCommerce products that are missing a price
OR missing images, then sets them to DRAFT status.

Run:
  python3 draft_incomplete_products.py

Options:
  --dry-run   Show what WOULD be drafted without making changes
  --report    Save a CSV report of drafted products (default: draft_report.csv)
"""

import os, sys, time, csv, argparse
from pathlib import Path
import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

BASE = Path(__file__).parent
SC   = BASE / "stock_control"

load_dotenv(BASE / ".env")
WC_URL    = os.environ["WC_URL"].rstrip("/")
WC_KEY    = os.environ["WC_KEY"]
WC_SECRET = os.environ["WC_SECRET"]
AUTH      = HTTPBasicAuth(WC_KEY, WC_SECRET)
API       = f"{WC_URL}/wp-json/wc/v3"

BATCH_SIZE = 50   # WC batch update supports up to 100
DELAY      = 1.5  # seconds between API calls


# ── Helpers ────────────────────────────────────────────────────────────────────

def fetch_all_published(per_page=100):
    """Fetch every published product from WooCommerce."""
    products, page = [], 1
    while True:
        params = {"status": "publish", "per_page": per_page, "page": page}
        r = requests.get(f"{API}/products", auth=AUTH, params=params, timeout=60)
        r.raise_for_status()
        data = r.json()
        if not data:
            break
        products.extend(data)
        total_pages = int(r.headers.get("X-WP-TotalPages", 1))
        print(f"  Fetched page {page}/{total_pages}  ({len(products)} products)\r", end="", flush=True)
        if page >= total_pages:
            break
        page += 1
        time.sleep(0.5)
    print()
    return products


def is_missing_price(p):
    regular = (p.get("regular_price") or "").strip()
    sale    = (p.get("sale_price") or "").strip()
    price   = (p.get("price") or "").strip()
    return not (regular or sale or price)


def is_missing_image(p):
    images = p.get("images") or []
    return len(images) == 0


def batch_set_draft(product_ids, dry_run=False):
    """Set a list of product IDs to draft via WC batch update."""
    updates = [{"id": pid, "status": "draft"} for pid in product_ids]
    if dry_run:
        return len(updates)

    r = requests.post(
        f"{API}/products/batch",
        auth=AUTH,
        json={"update": updates},
        timeout=120,
    )
    if r.status_code not in (200, 201):
        print(f"  ERROR {r.status_code}: {r.text[:300]}")
        return 0
    result = r.json()
    updated = result.get("update") or []
    return len(updated)


def save_report(products, filepath):
    rows = []
    for p in products:
        rows.append({
            "id":          p["id"],
            "sku":         p.get("sku", ""),
            "name":        p.get("name", ""),
            "missing":     p["_missing_reason"],
            "categories":  ", ".join(c["name"] for c in (p.get("categories") or [])),
            "permalink":   p.get("permalink", ""),
        })
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "sku", "name", "missing", "categories", "permalink"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"\n  Report saved → {filepath}  ({len(rows)} rows)")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Draft WooCommerce products missing price or images")
    parser.add_argument("--dry-run", action="store_true", help="Preview only — no changes made")
    parser.add_argument("--report",  default=str(SC / "draft_report.csv"), help="Path for CSV report")
    args = parser.parse_args()

    if args.dry_run:
        print("\n*** DRY RUN — no changes will be made ***\n")

    # ── Step 1: Fetch all published products ──────────────────────────────────
    print("Fetching all published products from WooCommerce...")
    products = fetch_all_published()
    print(f"Total published products: {len(products)}")

    # ── Step 2: Filter incomplete ─────────────────────────────────────────────
    incomplete = []
    no_price_count = 0
    no_image_count = 0
    both_count     = 0

    for p in products:
        mp = is_missing_price(p)
        mi = is_missing_image(p)
        if mp or mi:
            if mp and mi:
                reason = "no price + no image"
                both_count += 1
            elif mp:
                reason = "no price"
                no_price_count += 1
            else:
                reason = "no image"
                no_image_count += 1
            p["_missing_reason"] = reason
            incomplete.append(p)

    print(f"\n--- INCOMPLETE PRODUCTS (published) ---")
    print(f"  Missing price only:      {no_price_count}")
    print(f"  Missing image only:      {no_image_count}")
    print(f"  Missing BOTH:            {both_count}")
    print(f"  TOTAL to be drafted:     {len(incomplete)}")

    if not incomplete:
        print("\nNothing to draft. All published products have prices and images.")
        return

    # ── Step 3: Save report ───────────────────────────────────────────────────
    save_report(incomplete, args.report)

    if args.dry_run:
        print("\nDry run complete. Re-run without --dry-run to apply changes.")
        return

    # ── Step 4: Set to draft in batches ───────────────────────────────────────
    print(f"\nSetting {len(incomplete)} products to DRAFT...")
    ids = [p["id"] for p in incomplete]
    total_done = 0

    for i in range(0, len(ids), BATCH_SIZE):
        chunk = ids[i:i + BATCH_SIZE]
        done  = batch_set_draft(chunk)
        total_done += done
        print(f"  Drafted {total_done}/{len(ids)}...", end="\r", flush=True)
        if i + BATCH_SIZE < len(ids):
            time.sleep(DELAY)

    print(f"\n\nDone. {total_done} products set to DRAFT.")
    print(f"Review the list: {args.report}")
    print("\nTo re-publish a product once it has a price & image, edit it in")
    print("WooCommerce admin and click 'Publish'.")


if __name__ == "__main__":
    main()
