#!/usr/bin/env python3
"""
Merge all WC import CSVs into one master import file.
Sources:
  - stock_control/wc_import_filled.csv   (existing products, updated)
  - stock_control/hirsch_wc_import.csv   (new Hirsch products)
  - stock_control/impulse_new_products.csv (new Impulse products)
Output:
  - stock_control/master_wc_import.csv
"""
import csv, os

BASE = os.path.dirname(os.path.abspath(__file__))
SC   = os.path.join(BASE, "stock_control")

SOURCES = [
    os.path.join(SC, "wc_import_filled.csv"),
    os.path.join(SC, "hirsch_wc_import.csv"),
    os.path.join(SC, "impulse_new_products.csv"),
]
OUT = os.path.join(SC, "master_wc_import.csv")

# Build union of all columns, preserving a sensible order
# WooCommerce-standard columns first, extras appended
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
    "Meta: _yoast_wpseo_metadesc",
    "Brands",
]

all_rows = []
seen_skus = set()
source_counts = {}

for path in SOURCES:
    name = os.path.basename(path)
    count = 0
    with open(path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            sku = row.get("SKU", "").strip()
            if sku and sku in seen_skus:
                print(f"  SKIP duplicate SKU: {sku} in {name}")
                continue
            if sku:
                seen_skus.add(sku)
            all_rows.append(row)
            count += 1
    source_counts[name] = count
    print(f"  Loaded {count} rows from {name}")

with open(OUT, "w", newline='', encoding='utf-8') as f:
    writer = csv.DictWriter(f, fieldnames=MASTER_COLS, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(all_rows)

print(f"\nDone! {len(all_rows)} total products written to master_wc_import.csv")
for src, cnt in source_counts.items():
    print(f"  {src}: {cnt}")
