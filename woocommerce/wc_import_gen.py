#!/usr/bin/env python3
"""
============================================================
WooCommerce Master Import CSV Generator
============================================================
Takes the matched_products.csv (from supplier_match.py) and
the original WC export, then builds a clean WC import CSV
with all fixes applied:
  - Correct regular price (from supplier + markup)
  - Supplier brand info filled in
  - Stock status updated
  - Published status fixed for priced items

USAGE:
  python3 wc_import_gen.py \
    --wc      /path/to/wc-product-export.csv \
    --matched /path/to/matched_products.csv \
    --out     /path/to/wc_import_clean.csv \
    [--markup 1.4]

OUTPUT:
  wc_import_clean.csv  - Import this directly into WooCommerce
                         via Products > Import > Update existing products
============================================================
"""

import csv
import sys
import argparse
import os


# Minimum columns WooCommerce import needs for an update
WC_IMPORT_COLUMNS = [
    'ID', 'Type', 'SKU', 'Name', 'Published', 'In stock?',
    'Regular price', 'Sale price', 'Brands', 'Categories',
    'Images', 'Short description', 'Description', 'Stock',
]


def parse_args():
    parser = argparse.ArgumentParser(description='Generate WooCommerce import CSV from matched supplier data')
    parser.add_argument('--wc',      required=True, help='Original WC product export CSV')
    parser.add_argument('--matched', required=True, help='matched_products.csv from supplier_match.py')
    parser.add_argument('--out',     default='wc_import_clean.csv', help='Output import file path')
    parser.add_argument('--markup',  type=float, default=1.4,
                        help='Selling price markup over supplier price (default 1.4 = 40%%)')
    parser.add_argument('--set-stock', type=int, default=10,
                        help='Default stock quantity to set for matched in-stock products (default: 10)')
    return parser.parse_args()


def load_csv(path):
    with open(path, newline='', encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))


def build_matched_lookup(matched_rows):
    """Index matched rows by WC product ID."""
    lookup = {}
    for row in matched_rows:
        wc_id = row.get('WC_ID', '').strip()
        if wc_id:
            lookup[wc_id] = row
    return lookup


def apply_fixes(wc_row, match, markup, default_stock):
    """Apply supplier data fixes to a WC product row."""
    row = dict(wc_row)  # copy

    # Fix price
    sup_price = match.get('Sup_Price_ZAR', '')
    try:
        cost = float(str(sup_price).replace(',', '').strip())
        if cost > 0:
            selling = round(cost * markup, 2)
            if not row.get('Regular price', '').strip():
                row['Regular price'] = str(selling)
    except (ValueError, AttributeError):
        pass

    # Fill brand if missing
    if not row.get('Brands', '').strip():
        row['Brands'] = match.get('Sup_Brand', '')

    # Update stock if zero
    if not row.get('Stock', '').strip() or row.get('Stock', '0') == '0':
        row['Stock'] = str(default_stock)
        row['In stock?'] = '1'

    # Auto-publish if now has price and was unpublished
    if row.get('Regular price', '').strip() and row.get('Published', '').strip() == '0':
        row['Published'] = '1'  # set to publish

    return row


def main():
    args = parse_args()

    for path in [args.wc, args.matched]:
        if not os.path.exists(path):
            print(f'ERROR: File not found: {path}')
            sys.exit(1)

    print(f'Loading WC export:  {args.wc}')
    wc_products = load_csv(args.wc)
    print(f'  {len(wc_products)} rows')

    print(f'Loading matched:    {args.matched}')
    matched_rows = load_csv(args.matched)
    print(f'  {len(matched_rows)} matched products')

    match_lookup = build_matched_lookup(matched_rows)

    output_rows = []
    price_fixed = 0
    brand_fixed = 0
    stock_fixed = 0
    published_fixed = 0

    for wc_row in wc_products:
        wc_id = wc_row.get('ID', '').strip()

        if wc_id in match_lookup:
            match = match_lookup[wc_id]
            fixed = apply_fixes(wc_row, match, args.markup, args.set_stock)

            # Count fixes applied
            if fixed.get('Regular price', '') != wc_row.get('Regular price', ''):
                price_fixed += 1
            if fixed.get('Brands', '') != wc_row.get('Brands', ''):
                brand_fixed += 1
            if fixed.get('Stock', '') != wc_row.get('Stock', ''):
                stock_fixed += 1
            if fixed.get('Published', '') != wc_row.get('Published', ''):
                published_fixed += 1

            output_rows.append(fixed)
        else:
            output_rows.append(wc_row)

    # Write output
    out_path = args.out
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

    fieldnames = list(wc_products[0].keys()) if wc_products else WC_IMPORT_COLUMNS
    with open(out_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(output_rows)

    print(f'\n=== FIXES APPLIED ===')
    print(f'  Products processed:  {len(output_rows)}')
    print(f'  Prices updated:      {price_fixed}')
    print(f'  Brands filled:       {brand_fixed}')
    print(f'  Stock updated:       {stock_fixed}')
    print(f'  Published fixed:     {published_fixed}')
    print(f'=====================')
    print(f'\nOutput: {out_path}')
    print(f'\nNext step:')
    print(f'  1. Open WooCommerce > Products > Import')
    print(f'  2. Upload: {os.path.basename(out_path)}')
    print(f'  3. Check "Update existing products"')
    print(f'  4. Map columns and run import')


if __name__ == '__main__':
    main()
