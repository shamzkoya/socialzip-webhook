#!/usr/bin/env python3
"""
============================================================
WooCommerce Product Audit & Cleanup Tool
============================================================
Audits a WooCommerce product export CSV and identifies:
  - Products with missing prices
  - Products with zero/missing stock
  - Products missing images
  - Products missing brands/supplier info
  - Variation products without parent prices

USAGE:
  python3 wc_audit.py --csv /path/to/wc-product-export.csv

OUTPUT:
  - Audit report printed to console
  - audit_issues.csv - all products with issues
  - audit_no_price.csv - published products with no price
  - audit_no_image.csv - products missing images
  - audit_no_stock.csv - products with zero stock
============================================================
"""

import csv
import sys
import argparse
import os
from collections import defaultdict

def parse_args():
    parser = argparse.ArgumentParser(description='Audit WooCommerce product export CSV')
    parser.add_argument('--csv', required=True, help='Path to WooCommerce product export CSV')
    parser.add_argument('--out', default='.', help='Output directory for reports (default: current dir)')
    return parser.parse_args()

def load_products(csv_path):
    products = []
    with open(csv_path, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            products.append(row)
    return products

def audit_products(products):
    issues = {
        'no_price': [],
        'no_image': [],
        'zero_stock': [],
        'no_brand': [],
        'published_no_price': [],
        'missing_sku': [],
    }

    brand_stats = defaultdict(lambda: {'total': 0, 'with_price': 0, 'with_image': 0, 'with_stock': 0})
    type_counts = defaultdict(int)

    total = len(products)
    parent_products = []
    variation_products = []

    for p in products:
        ptype = p.get('Type', '').strip().lower()
        type_counts[ptype] += 1

        if ptype == 'variation':
            variation_products.append(p)
            continue

        parent_products.append(p)

        name = p.get('Name', '').strip()
        sku = p.get('SKU', '').strip()
        price = p.get('Regular price', '').strip() or p.get('Sale price', '').strip()
        image = p.get('Images', '').strip()
        stock = p.get('Stock', '').strip()
        in_stock = p.get('In stock?', '').strip().lower()
        brand = p.get('Brands', '').strip()
        published = p.get('Published', '').strip()

        # Track brand stats
        brand_key = brand if brand else '(no brand)'
        brand_stats[brand_key]['total'] += 1
        if price:
            brand_stats[brand_key]['with_price'] += 1
        if image:
            brand_stats[brand_key]['with_image'] += 1
        if stock and stock != '0' and in_stock == '1':
            brand_stats[brand_key]['with_stock'] += 1

        # Check issues
        if not price:
            issues['no_price'].append(p)
            if published == '1':
                issues['published_no_price'].append(p)

        if not image:
            issues['no_image'].append(p)

        if not stock or stock == '0' or in_stock != '1':
            issues['zero_stock'].append(p)

        if not brand:
            issues['no_brand'].append(p)

        if not sku:
            issues['missing_sku'].append(p)

    return issues, brand_stats, type_counts, parent_products, variation_products

def print_report(issues, brand_stats, type_counts, parent_products, variation_products, total):
    print('\n' + '='*60)
    print('  WOOCOMMERCE PRODUCT AUDIT REPORT')
    print('='*60)
    print(f'\nTotal rows in CSV: {total}')
    print(f'  Parent products (simple + variable): {len(parent_products)}')
    print(f'  Variation products: {len(variation_products)}')
    print(f'\nProduct types:')
    for t, count in sorted(type_counts.items(), key=lambda x: -x[1]):
        print(f'  {t or "(blank)":20s}: {count}')

    print(f'\n--- ISSUES FOUND ---')
    print(f'  Published with NO PRICE:  {len(issues["published_no_price"])}  ← URGENT')
    print(f'  Any product no price:     {len(issues["no_price"])}')
    print(f'  Missing images:           {len(issues["no_image"])}')
    print(f'  Zero/no stock:            {len(issues["zero_stock"])}')
    print(f'  Missing brand:            {len(issues["no_brand"])}')
    print(f'  Missing SKU:              {len(issues["missing_sku"])}')

    print(f'\n--- TOP BRANDS BY PRODUCT COUNT ---')
    sorted_brands = sorted(brand_stats.items(), key=lambda x: -x[1]['total'])
    print(f'  {"Brand":35s} {"Total":>6} {"W/Price":>8} {"W/Image":>8} {"W/Stock":>8}')
    print(f'  {"-"*35} {"-"*6} {"-"*8} {"-"*8} {"-"*8}')
    for brand, stats in sorted_brands[:30]:
        t = stats['total']
        wp = stats['with_price']
        wi = stats['with_image']
        ws = stats['with_stock']
        flag = ' ← NEEDS PRICES' if wp < t * 0.5 else ''
        print(f'  {brand[:35]:35s} {t:>6} {wp:>8} {wi:>8} {ws:>8}{flag}')

    print('\n' + '='*60 + '\n')

def write_report_csv(products, filepath, reason_col=None):
    if not products:
        return
    fieldnames = list(products[0].keys())
    if reason_col and reason_col not in fieldnames:
        fieldnames = [reason_col] + fieldnames
    with open(filepath, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(products)
    print(f'  Written: {filepath} ({len(products)} rows)')

def main():
    args = parse_args()

    if not os.path.exists(args.csv):
        print(f'ERROR: File not found: {args.csv}')
        sys.exit(1)

    print(f'Loading: {args.csv}')
    products = load_products(args.csv)
    total = len(products)
    print(f'Loaded {total} rows')

    issues, brand_stats, type_counts, parent_products, variation_products = audit_products(products)

    print_report(issues, brand_stats, type_counts, parent_products, variation_products, total)

    out = args.out
    os.makedirs(out, exist_ok=True)

    print('Writing report files...')
    write_report_csv(issues['published_no_price'], os.path.join(out, 'audit_published_no_price.csv'))
    write_report_csv(issues['no_price'],           os.path.join(out, 'audit_no_price.csv'))
    write_report_csv(issues['no_image'],           os.path.join(out, 'audit_no_image.csv'))
    write_report_csv(issues['zero_stock'],         os.path.join(out, 'audit_zero_stock.csv'))
    write_report_csv(issues['no_brand'],           os.path.join(out, 'audit_no_brand.csv'))

    print('\nDone. Review the CSV reports above.')

if __name__ == '__main__':
    main()
