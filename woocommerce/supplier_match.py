#!/usr/bin/env python3
"""
============================================================
Supplier → WooCommerce Product Matcher
============================================================
Matches supplier product data (Hirschs, Impulse, etc.)
against WooCommerce product export CSV.

Matching strategy (in priority order):
  1. Exact SKU / model match
  2. Fuzzy model number match
  3. Brand + product name similarity

USAGE:
  python3 supplier_match.py \
    --wc  /path/to/wc-product-export.csv \
    --sup /path/to/Hirschs_Products_Extracted.csv \
    --out /path/to/output/

OUTPUT:
  matched_products.csv       - WC products matched to supplier
  unmatched_wc.csv           - WC products with no supplier match
  unmatched_supplier.csv     - Supplier products not in WooCommerce
  price_update_import.csv    - Ready-to-import WC price fixes
============================================================
"""

import csv
import re
import sys
import argparse
import os
from difflib import SequenceMatcher


def parse_args():
    parser = argparse.ArgumentParser(description='Match supplier data to WooCommerce products')
    parser.add_argument('--wc',  required=True, help='WooCommerce product export CSV')
    parser.add_argument('--sup', required=True, help='Supplier product CSV (Hirschs, Impulse, etc.)')
    parser.add_argument('--out', default='.',   help='Output directory')
    parser.add_argument('--markup', type=float, default=1.4,
                        help='Selling price markup multiplier over supplier price (default: 1.4 = 40%% markup)')
    return parser.parse_args()


def normalize_sku(s):
    """Strip spaces, dashes, underscores; uppercase for comparison."""
    if not s:
        return ''
    return re.sub(r'[\s\-_/]', '', s.strip().upper())


def normalize_name(s):
    """Lowercase, strip punctuation for fuzzy matching."""
    if not s:
        return ''
    return re.sub(r'[^a-z0-9 ]', '', s.strip().lower())


def similarity(a, b):
    return SequenceMatcher(None, a, b).ratio()


def load_wc_products(csv_path):
    products = []
    with open(csv_path, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get('Type', '').strip().lower() == 'variation':
                continue
            products.append(row)
    return products


def load_supplier_products(csv_path):
    products = []
    with open(csv_path, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            products.append(row)
    return products


def build_wc_index(wc_products):
    """Build lookup indexes for fast matching."""
    sku_index = {}
    brand_index = {}

    for p in wc_products:
        sku = normalize_sku(p.get('SKU', ''))
        if sku:
            sku_index[sku] = p

        brand = p.get('Brands', '').strip().lower()
        if brand not in brand_index:
            brand_index[brand] = []
        brand_index[brand].append(p)

    return sku_index, brand_index


def match_products(wc_products, supplier_products, markup):
    sku_index, brand_index = build_wc_index(wc_products)

    matched = []
    unmatched_supplier = []
    matched_wc_ids = set()

    for sup in supplier_products:
        sup_model = normalize_sku(sup.get('Model', '') or sup.get('SN/SKU', ''))
        sup_sku   = normalize_sku(sup.get('SN/SKU', '') or sup.get('Model', ''))
        sup_brand = sup.get('Brand', '').strip().lower()
        sup_name  = normalize_name(sup.get('Product Name', ''))
        sup_price = sup.get('Sale Price', '')

        try:
            cost_price = float(str(sup_price).replace(',', '').replace('R', '').strip())
        except (ValueError, AttributeError):
            cost_price = 0.0

        selling_price = round(cost_price * markup, 2) if cost_price > 0 else 0.0

        wc_match = None
        match_method = ''
        match_score = 0.0

        # 1. Try exact model/SKU match
        for key in [sup_model, sup_sku]:
            if key and key in sku_index:
                wc_match = sku_index[key]
                match_method = 'exact_sku'
                match_score = 1.0
                break

        # 2. Try fuzzy model match (>= 80% similar)
        if not wc_match and sup_model:
            for wc_sku, wc_p in sku_index.items():
                score = similarity(sup_model, wc_sku)
                if score >= 0.80 and score > match_score:
                    wc_match = wc_p
                    match_method = f'fuzzy_sku({score:.0%})'
                    match_score = score

        # 3. Try brand + name similarity
        if not wc_match and sup_brand and sup_name:
            candidates = brand_index.get(sup_brand, [])
            for wc_p in candidates:
                wc_name = normalize_name(wc_p.get('Name', ''))
                score = similarity(sup_name, wc_name)
                if score >= 0.65 and score > match_score:
                    wc_match = wc_p
                    match_method = f'name_sim({score:.0%})'
                    match_score = score

        if wc_match:
            wc_id = wc_match.get('ID', '')
            matched_wc_ids.add(wc_id)

            current_price = wc_match.get('Regular price', '').strip()
            needs_price_update = not current_price or current_price == '0'

            matched.append({
                'WC_ID': wc_id,
                'WC_SKU': wc_match.get('SKU', ''),
                'WC_Name': wc_match.get('Name', ''),
                'WC_Type': wc_match.get('Type', ''),
                'WC_Published': wc_match.get('Published', ''),
                'WC_Current_Price': current_price,
                'Supplier': sup.get('Supplier', ''),
                'Sup_Brand': sup.get('Brand', ''),
                'Sup_Model': sup.get('Model', ''),
                'Sup_SKU': sup.get('SN/SKU', ''),
                'Sup_Price_ZAR': cost_price,
                'Suggested_Selling_Price': selling_price,
                'Match_Method': match_method,
                'Needs_Price_Update': 'YES' if needs_price_update else 'no',
            })
        else:
            unmatched_supplier.append(sup)

    # Find unmatched WC products
    unmatched_wc = [p for p in wc_products if p.get('ID', '') not in matched_wc_ids]

    return matched, unmatched_wc, unmatched_supplier


def build_price_import(matched_rows):
    """Build a WooCommerce-ready import CSV for price updates."""
    rows = []
    for m in matched_rows:
        if m['Needs_Price_Update'] == 'YES' and m['Suggested_Selling_Price'] > 0:
            rows.append({
                'ID': m['WC_ID'],
                'SKU': m['WC_SKU'],
                'Regular price': m['Suggested_Selling_Price'],
                'Sale price': '',
            })
    return rows


def write_csv(rows, filepath, fieldnames=None):
    if not rows:
        print(f'  (no data) skipped: {filepath}')
        return
    if not fieldnames:
        fieldnames = list(rows[0].keys())
    with open(filepath, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    print(f'  Written: {filepath} ({len(rows)} rows)')


def main():
    args = parse_args()

    for path in [args.wc, args.sup]:
        if not os.path.exists(path):
            print(f'ERROR: File not found: {path}')
            sys.exit(1)

    os.makedirs(args.out, exist_ok=True)

    print(f'Loading WooCommerce products: {args.wc}')
    wc_products = load_wc_products(args.wc)
    print(f'  Loaded {len(wc_products)} parent products')

    print(f'Loading supplier products: {args.sup}')
    supplier_products = load_supplier_products(args.sup)
    print(f'  Loaded {len(supplier_products)} supplier products')

    print(f'\nMatching (markup: {args.markup}x)...')
    matched, unmatched_wc, unmatched_supplier = match_products(
        wc_products, supplier_products, args.markup
    )

    total_sup = len(supplier_products)
    matched_count = len(matched)
    needs_price = sum(1 for m in matched if m['Needs_Price_Update'] == 'YES')

    print(f'\n=== MATCH RESULTS ===')
    print(f'  Supplier products:    {total_sup}')
    print(f'  Matched to WC:        {matched_count}  ({matched_count/total_sup*100:.0f}%)')
    print(f'  Unmatched supplier:   {len(unmatched_supplier)}')
    print(f'  Unmatched WC:         {len(unmatched_wc)}')
    print(f'  Matched needing price:{needs_price}')
    print(f'=====================\n')

    # Write outputs
    out = args.out
    write_csv(matched,            os.path.join(out, 'matched_products.csv'))
    write_csv(unmatched_wc,       os.path.join(out, 'unmatched_wc.csv'))
    write_csv(unmatched_supplier, os.path.join(out, 'unmatched_supplier.csv'))

    price_rows = build_price_import(matched)
    write_csv(price_rows, os.path.join(out, 'price_update_import.csv'),
              fieldnames=['ID', 'SKU', 'Regular price', 'Sale price'])

    print(f'\nDone! Check the output files in: {out}')
    print(f'To update prices in WooCommerce: import price_update_import.csv via WC > Products > Import')


if __name__ == '__main__':
    main()
