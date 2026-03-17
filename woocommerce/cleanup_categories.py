#!/usr/bin/env python3
"""
Category Cleanup
================
1. DELETES all products in the Clothing category (permanently removed)
2. DRAFTS all products in Christmas / Xmas categories

Run:
  python3 cleanup_categories.py

Options:
  --dry-run   Preview only, no changes made
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

DELAY = 1.2

CLOTHING_KEYWORDS  = ["clothing", "apparel", "fashion", "wear", "garment", "shirt", "dress", "pants", "jacket", "coat", "shoe", "boot"]
CHRISTMAS_KEYWORDS = ["christmas", "xmas", "festive", "holiday", "x-mas"]


# ── Helpers ────────────────────────────────────────────────────────────────────

def fetch_all_categories():
    cats, page = [], 1
    while True:
        r = requests.get(f"{API}/products/categories",
                         auth=AUTH,
                         params={"per_page": 100, "page": page},
                         timeout=30)
        r.raise_for_status()
        data = r.json()
        if not data:
            break
        cats.extend(data)
        if page >= int(r.headers.get("X-WP-TotalPages", 1)):
            break
        page += 1
    return cats


def match_categories(all_cats, keywords):
    matched = []
    for c in all_cats:
        name = c["name"].lower()
        slug = c.get("slug", "").lower()
        if any(kw in name or kw in slug for kw in keywords):
            matched.append(c)
    return matched


def fetch_products_in_categories(cat_ids, extra_params=None):
    if not cat_ids:
        return []
    products, page = [], 1
    params_base = {"category": ",".join(str(i) for i in cat_ids), "per_page": 100}
    if extra_params:
        params_base.update(extra_params)
    while True:
        params = {**params_base, "page": page}
        r = requests.get(f"{API}/products", auth=AUTH, params=params, timeout=60)
        r.raise_for_status()
        data = r.json()
        if not data:
            break
        products.extend(data)
        total_pages = int(r.headers.get("X-WP-TotalPages", 1))
        print(f"    page {page}/{total_pages}  ({len(products)} loaded)\r", end="", flush=True)
        if page >= total_pages:
            break
        page += 1
        time.sleep(0.5)
    print()
    return products


def batch_delete(product_ids, dry_run=False):
    """Permanently delete products (force=true)."""
    if dry_run:
        return len(product_ids)
    total = 0
    for i in range(0, len(product_ids), 50):
        chunk = product_ids[i:i+50]
        deletes = [{"id": pid} for pid in chunk]
        r = requests.post(
            f"{API}/products/batch",
            auth=AUTH,
            json={"delete": [pid for pid in chunk]},
            timeout=120,
        )
        if r.status_code not in (200, 201):
            print(f"  ERROR {r.status_code}: {r.text[:300]}")
        else:
            result = r.json()
            deleted = result.get("delete") or []
            total += len(deleted)
        time.sleep(DELAY)
    return total


def batch_set_status(product_ids, status, dry_run=False):
    if dry_run:
        return len(product_ids)
    total = 0
    for i in range(0, len(product_ids), 50):
        chunk = product_ids[i:i+50]
        updates = [{"id": pid, "status": status} for pid in chunk]
        r = requests.post(
            f"{API}/products/batch",
            auth=AUTH,
            json={"update": updates},
            timeout=120,
        )
        if r.status_code not in (200, 201):
            print(f"  ERROR {r.status_code}: {r.text[:300]}")
        else:
            result = r.json()
            total += len(result.get("update") or [])
        time.sleep(DELAY)
    return total


def save_csv(products, filepath, action):
    rows = [{
        "id":         p["id"],
        "sku":        p.get("sku", ""),
        "name":       p.get("name", ""),
        "action":     action,
        "categories": ", ".join(c["name"] for c in (p.get("categories") or [])),
        "status":     p.get("status", ""),
        "permalink":  p.get("permalink", ""),
    } for p in products]
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["id","sku","name","action","categories","status","permalink"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"  Report → {filepath}  ({len(rows)} rows)")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    args = parser.parse_args()

    if args.dry_run:
        print("\n*** DRY RUN — no changes will be made ***\n")

    # ── Load all categories ───────────────────────────────────────────────────
    print("Loading categories from WooCommerce...")
    all_cats = fetch_all_categories()
    print(f"  Total categories: {len(all_cats)}")

    clothing_cats  = match_categories(all_cats, CLOTHING_KEYWORDS)
    christmas_cats = match_categories(all_cats, CHRISTMAS_KEYWORDS)

    print(f"\nClothing categories matched ({len(clothing_cats)}):")
    for c in clothing_cats:
        print(f"  [{c['id']}] {c['name']}  (slug: {c['slug']})")

    print(f"\nChristmas categories matched ({len(christmas_cats)}):")
    for c in christmas_cats:
        print(f"  [{c['id']}] {c['name']}  (slug: {c['slug']})")

    if not clothing_cats and not christmas_cats:
        print("\nNo matching categories found. Check category names in WooCommerce.")
        print("Tip: Run with --dry-run and check the matched category list above.")
        return

    # ── Clothing: fetch ALL statuses then delete ──────────────────────────────
    clothing_ids = [c["id"] for c in clothing_cats]
    christmas_ids = [c["id"] for c in christmas_cats]

    clothing_products  = []
    christmas_products = []

    if clothing_ids:
        print(f"\nFetching clothing products...")
        for status in ["publish", "draft", "private"]:
            chunk = fetch_products_in_categories(clothing_ids, {"status": status})
            clothing_products.extend(chunk)
        # deduplicate by id
        seen = set()
        clothing_products = [p for p in clothing_products if not (p["id"] in seen or seen.add(p["id"]))]
        print(f"  Total clothing products: {len(clothing_products)}")

    if christmas_ids:
        print(f"\nFetching Christmas products...")
        for status in ["publish", "draft", "private"]:
            chunk = fetch_products_in_categories(christmas_ids, {"status": status})
            christmas_products.extend(chunk)
        seen = set()
        christmas_products = [p for p in christmas_products if not (p["id"] in seen or seen.add(p["id"]))]
        print(f"  Total Christmas products: {len(christmas_products)}")

    # ── Save reports ──────────────────────────────────────────────────────────
    if clothing_products:
        save_csv(clothing_products, SC / "deleted_clothing.csv", "DELETED")
    if christmas_products:
        save_csv(christmas_products, SC / "drafted_christmas.csv", "DRAFTED")

    if args.dry_run:
        print("\nDry run complete. Re-run without --dry-run to apply changes.")
        return

    # ── Apply: delete clothing ────────────────────────────────────────────────
    if clothing_products:
        cloth_ids = [p["id"] for p in clothing_products]
        print(f"\nDeleting {len(cloth_ids)} clothing products...")
        deleted = batch_delete(cloth_ids)
        print(f"  Deleted: {deleted}/{len(cloth_ids)}")
    else:
        print("\nNo clothing products found to delete.")

    # ── Apply: draft Christmas ────────────────────────────────────────────────
    if christmas_products:
        xmas_ids = [p["id"] for p in christmas_products if p.get("status") != "draft"]
        already_draft = len(christmas_products) - len(xmas_ids)
        print(f"\nDrafting {len(xmas_ids)} Christmas products ({already_draft} already draft)...")
        drafted = batch_set_status(xmas_ids, "draft")
        print(f"  Drafted: {drafted}/{len(xmas_ids)}")
    else:
        print("\nNo Christmas products found to draft.")

    print("\nCleanup complete.")


if __name__ == "__main__":
    main()
