#!/usr/bin/env python3
"""
WooCommerce Import Failure Analysis
====================================
Connects to WooCommerce REST API and diagnoses why products failed to import.

Usage:
  python3 wc_analysis.py \
    --url https://happyharvesting.co.za \
    --key ck_xxxxxxxxxxxx \
    --secret cs_xxxxxxxxxxxx
"""

import sys
import json
import argparse
import requests
from requests.auth import HTTPBasicAuth
from collections import defaultdict, Counter


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument('--url',    required=True, help='Site URL e.g. https://happyharvesting.co.za')
    p.add_argument('--key',    required=True, help='WooCommerce Consumer Key (ck_...)')
    p.add_argument('--secret', required=True, help='WooCommerce Consumer Secret (cs_...)')
    p.add_argument('--per-page', type=int, default=100)
    return p.parse_args()


def api_get(base_url, endpoint, key, secret, params=None):
    url  = f"{base_url.rstrip('/')}/wp-json/wc/v3/{endpoint}"
    auth = HTTPBasicAuth(key, secret)
    params = params or {}
    params['per_page'] = params.get('per_page', 100)
    r = requests.get(url, auth=auth, params=params, timeout=30)
    r.raise_for_status()
    return r.json(), r.headers


def fetch_all(base_url, endpoint, key, secret):
    """Paginate through all results."""
    items = []
    page  = 1
    while True:
        data, headers = api_get(base_url, endpoint, key, secret,
                                params={'per_page': 100, 'page': page})
        if not data:
            break
        items.extend(data)
        total_pages = int(headers.get('X-WP-TotalPages', 1))
        print(f"  Fetched page {page}/{total_pages} ({len(items)} so far)...", end='\r')
        if page >= total_pages:
            break
        page += 1
    print()
    return items


def analyse(args):
    print(f"\n{'='*60}")
    print(f"  WooCommerce Analysis — {args.url}")
    print(f"{'='*60}\n")

    # ── 1. Test connection ──────────────────────────────────────
    print("Testing connection...")
    try:
        data, headers = api_get(args.url, 'products', args.key, args.secret,
                                params={'per_page': 1})
        total = int(headers.get('X-WP-Total', 0))
        print(f"✅ Connected. Total products in WooCommerce: {total}\n")
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        sys.exit(1)

    # ── 2. Fetch all products ───────────────────────────────────
    print(f"Fetching all {total} products (this may take a minute)...")
    products = fetch_all(args.url, 'products', args.key, args.secret)
    print(f"✅ Loaded {len(products)} products\n")

    # ── 3. Status breakdown ─────────────────────────────────────
    status_counts = Counter(p['status'] for p in products)
    print("─── Product Status Breakdown ───────────────────────────")
    for status, count in status_counts.most_common():
        print(f"  {status:<20} {count:>6}")
    print()

    # ── 4. Missing SKUs ─────────────────────────────────────────
    no_sku = [p for p in products if not p.get('sku', '').strip()]
    print(f"─── Missing SKU ────────────────────────────────────────")
    print(f"  Products with NO SKU: {len(no_sku)}")
    if no_sku[:5]:
        print("  Examples:")
        for p in no_sku[:5]:
            print(f"    ID {p['id']:>6} — {p['name'][:60]}")
    print()

    # ── 5. Duplicate SKUs ───────────────────────────────────────
    sku_map = defaultdict(list)
    for p in products:
        sku = p.get('sku', '').strip()
        if sku:
            sku_map[sku].append(p['id'])
    dupes = {sku: ids for sku, ids in sku_map.items() if len(ids) > 1}
    print(f"─── Duplicate SKUs ─────────────────────────────────────")
    print(f"  Duplicate SKUs found: {len(dupes)}")
    if dupes:
        print("  Top duplicates:")
        for sku, ids in list(dupes.items())[:10]:
            print(f"    SKU '{sku}' used by IDs: {ids}")
    print()

    # ── 6. Missing price ────────────────────────────────────────
    no_price = [p for p in products
                if not p.get('regular_price', '').strip()
                and not p.get('price', '').strip()]
    print(f"─── Missing Price ───────────────────────────────────────")
    print(f"  Products with NO price: {len(no_price)}")
    if no_price[:5]:
        print("  Examples:")
        for p in no_price[:5]:
            print(f"    ID {p['id']:>6} — {p['name'][:60]}")
    print()

    # ── 7. Missing images ───────────────────────────────────────
    no_image = [p for p in products if not p.get('images')]
    print(f"─── Missing Images ─────────────────────────────────────")
    print(f"  Products with NO image: {len(no_image)}")
    print()

    # ── 8. Missing categories ───────────────────────────────────
    no_cat = [p for p in products if not p.get('categories')]
    print(f"─── Missing Categories ──────────────────────────────────")
    print(f"  Products with NO category: {len(no_cat)}")
    print()

    # ── 9. Missing description ──────────────────────────────────
    no_desc = [p for p in products
               if not p.get('description', '').strip()
               and not p.get('short_description', '').strip()]
    print(f"─── Missing Description ─────────────────────────────────")
    print(f"  Products with NO description at all: {len(no_desc)}")
    print()

    # ── 10. Price health check ──────────────────────────────────
    zero_price = [p for p in products
                  if p.get('regular_price') == '0'
                  or p.get('price') == '0']
    print(f"─── Zero Price Products ─────────────────────────────────")
    print(f"  Products priced at R0: {len(zero_price)}")
    if zero_price[:5]:
        for p in zero_price[:5]:
            print(f"    ID {p['id']:>6} — {p['name'][:60]}")
    print()

    # ── 11. Category breakdown ──────────────────────────────────
    cat_counter = Counter()
    for p in products:
        for cat in p.get('categories', []):
            cat_counter[cat['name']] += 1
    print(f"─── Top Categories ──────────────────────────────────────")
    for cat, count in cat_counter.most_common(20):
        print(f"  {cat:<40} {count:>5}")
    print()

    # ── 12. Summary & recommended fixes ─────────────────────────
    print(f"{'='*60}")
    print(f"  DIAGNOSIS SUMMARY")
    print(f"{'='*60}")
    print(f"  Total products loaded:     {len(products):>6}")
    print(f"  Published:                 {status_counts.get('publish', 0):>6}")
    print(f"  Draft:                     {status_counts.get('draft', 0):>6}")
    print(f"  Missing SKU:               {len(no_sku):>6}")
    print(f"  Duplicate SKUs:            {len(dupes):>6}")
    print(f"  Missing price:             {len(no_price):>6}")
    print(f"  Zero price:                {len(zero_price):>6}")
    print(f"  Missing image:             {len(no_image):>6}")
    print(f"  Missing category:          {len(no_cat):>6}")
    print(f"  Missing description:       {len(no_desc):>6}")
    print()
    print("  RECOMMENDED FIXES:")
    if len(no_sku) > 100:
        print("  ⚠  Large number of missing SKUs — likely cause of import failures")
        print("     Fix: Add unique SKUs to all products before re-importing")
    if len(dupes) > 0:
        print("  ⚠  Duplicate SKUs — WooCommerce rejects rows with duplicate SKUs")
        print("     Fix: Deduplicate SKUs in your import CSV")
    if len(no_price) > 100:
        print("  ⚠  Many products have no price — check markup calculation")
    if len(no_image) > 500:
        print("  ⚠  Most products have no image — image URLs may have failed")
        print("     Fix: Use direct image URLs (not redirects) in the Images column")
    if status_counts.get('draft', 0) > 1000:
        print("  ⚠  Many products stuck as Draft — Published column may be 0 in CSV")
        print("     Fix: Set Published = 1 in import CSV")
    print()


if __name__ == '__main__':
    args = parse_args()
    analyse(args)
