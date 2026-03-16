#!/usr/bin/env python3
"""
Build the full master WooCommerce stock sheet — all products, no duplicates.

Sources (applied in order; later sources override earlier on same SKU):
  1. master_stock.csv                    - All 2477 existing WC products
  2. wc_import_filled.csv               - Existing products with enriched data
  3. hirsch_wc_import.csv               - New Hirsch catalogue products
  4. impulse_new_products_enriched.csv  - New enriched Impulse catalogue products

Output:
  master_wc_import.csv  - Complete stock sheet ready for WooCommerce import
"""
import csv, os

BASE = os.path.dirname(os.path.abspath(__file__))
SC   = os.path.join(BASE, "stock_control")
OUT  = os.path.join(SC, "master_wc_import.csv")

MASTER_COLS = [
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
    "Download limit", "Download expiry days",
    "Parent", "Grouped products", "Upsells", "Cross-sells",
    "External URL", "Button text", "Position",
    "Attribute 1 name", "Attribute 1 value(s)", "Attribute 1 visible", "Attribute 1 global",
    "Attribute 2 name", "Attribute 2 value(s)", "Attribute 2 visible", "Attribute 2 global",
    "Attribute 3 name", "Attribute 3 value(s)", "Attribute 3 visible", "Attribute 3 global",
    "Meta: title", "Meta: description", "Meta: keywords",
    "_yoast_wpseo_title", "_yoast_wpseo_metadesc", "_yoast_wpseo_focuskw",
    "Meta: _yoast_wpseo_metadesc", "Brands",
]

# Maps audit supplier names to display names for the Brands column
SUPPLIER_MAP = {
    "impulse":         "Impulse Imports",
    "hirschs":         "Hirschs",
    "makokoya":        "Makokoya",
    "power_warehouse": "Power Warehouse",
    "tupperware":      "Tupperware",
    "jewellery":       "Jewellery",
    "unknown":         "",
}

# Keyed by SKU for deduplication; no-SKU rows get a unique placeholder key
rows_by_sku = {}
_no_sku_counter = [0]

def _key(sku, fallback_prefix=""):
    if sku:
        return sku
    _no_sku_counter[0] += 1
    return f"__no_sku_{fallback_prefix}_{_no_sku_counter[0]}__"


# ── Step 1: existing WooCommerce products (master_stock.csv audit format) ──────
print("Step 1: loading existing WC products from master_stock.csv ...")
existing_count = 0
with open(os.path.join(SC, "master_stock.csv"), newline="", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        sku = row.get("sku", "").strip()
        wc_row = {
            "ID":                   row.get("wc_id", ""),
            "Type":                 row.get("type", "simple") or "simple",
            "SKU":                  sku,
            "Name":                 row.get("name", ""),
            "Published":            "1" if row.get("published", "").lower() == "true" else "0",
            "Is featured?":         "0",
            "Visibility in catalog":"visible",
            "Tax status":           "taxable",
            "In stock?":            "1",
            "Regular price":        row.get("regular_price", ""),
            "Categories":           row.get("categories", ""),
            "Brands":               SUPPLIER_MAP.get(
                                        row.get("supplier", "").lower(),
                                        row.get("supplier", "")
                                    ),
        }
        rows_by_sku[_key(sku, "ms")] = wc_row
        existing_count += 1
print(f"  {existing_count} existing products loaded")


# ── Steps 2-4: enriched / new-product files override existing entries ──────────
ENRICHED_SOURCES = [
    ("wc_import_filled.csv",              "Updated existing products (enriched)"),
    ("hirsch_wc_import.csv",              "New Hirsch catalogue products"),
    ("impulse_new_products_enriched.csv", "New enriched Impulse catalogue products"),
]

for filename, label in ENRICHED_SOURCES:
    path = os.path.join(SC, filename)
    added = overridden = 0
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            sku = row.get("SKU", "").strip()
            k   = _key(sku, filename)
            if k in rows_by_sku:
                overridden += 1
            else:
                added += 1
            rows_by_sku[k] = dict(row)
    print(f"  {label}: {added} new  |  {overridden} updated existing")


# ── Write output ───────────────────────────────────────────────────────────────
all_rows = list(rows_by_sku.values())
with open(OUT, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=MASTER_COLS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(all_rows)

# ── Summary ───────────────────────────────────────────────────────────────────
print(f"\n✓  {len(all_rows)} total products written to master_wc_import.csv")
from collections import Counter
by_brand = Counter(r.get("Brands", "") or "Unknown/no supplier" for r in all_rows)
for brand, cnt in sorted(by_brand.items(), key=lambda x: -x[1]):
    print(f"   {brand or 'Unknown/no supplier':30s}  {cnt}")
