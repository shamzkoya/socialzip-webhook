#!/usr/bin/env python3
"""
WooCommerce Duplicate SKU Fixer
================================
Finds all products sharing a SKU, keeps the first one unchanged,
and assigns clean unique SKUs to every duplicate.

New SKU format: original + '-' + product_id  (e.g. ABC123-31072)
"""

import sys
import time
import requests
from requests.auth import HTTPBasicAuth
from collections import defaultdict

URL    = 'https://happyharvesting.co.za'
KEY    = 'ck_136f115b9330f42169bae720a7ff1aa94ccba966'
SECRET = 'cs_cbfd5d52202530530492692c1bf1267cf1978cea'
AUTH   = HTTPBasicAuth(KEY, SECRET)


def api_get(endpoint, params=None):
    r = requests.get(
        f"{URL}/wp-json/wc/v3/{endpoint}",
        auth=AUTH, params=params or {}, timeout=30
    )
    r.raise_for_status()
    return r.json(), r.headers


def api_put(endpoint, data):
    r = requests.put(
        f"{URL}/wp-json/wc/v3/{endpoint}",
        auth=AUTH, json=data, timeout=30
    )
    r.raise_for_status()
    return r.json()


def fetch_all_products():
    items, page = [], 1
    while True:
        data, headers = api_get('products', {'per_page': 100, 'page': page})
        if not data:
            break
        items.extend(data)
        total_pages = int(headers.get('X-WP-TotalPages', 1))
        print(f"  Loading page {page}/{total_pages} ({len(items)} products)...", end='\r', flush=True)
        if page >= total_pages:
            break
        page += 1
    print()
    return items


def main():
    print("\n=== WooCommerce Duplicate SKU Fixer ===\n")

    print("Fetching all products...")
    products = fetch_all_products()
    print(f"Loaded {len(products)} products\n")

    sku_map = defaultdict(list)
    for p in products:
        sku = p.get('sku', '').strip()
        if sku:
            sku_map[sku].append({'id': p['id'], 'name': p['name'], 'sku': sku})

    dupes = {sku: items for sku, items in sku_map.items() if len(items) > 1}
    print(f"Found {len(dupes)} duplicate SKU groups\n")

    if not dupes:
        print("No duplicates found — all SKUs are unique!")
        return

    total_fixed = 0
    errors      = []

    for sku, group in dupes.items():
        group.sort(key=lambda x: x['id'])
        keeper     = group[0]
        duplicates = group[1:]

        print(f"SKU '{sku}' → keeping ID {keeper['id']}, fixing {len(duplicates)} duplicate(s)")

        for item in duplicates:
            new_sku = f"{sku}-{item['id']}"
            try:
                api_put(f"products/{item['id']}", {'sku': new_sku})
                print(f"  OK ID {item['id']} -> {new_sku}")
                total_fixed += 1
            except Exception as e:
                print(f"  FAIL ID {item['id']}: {e}")
                errors.append({'id': item['id'], 'error': str(e)})
            time.sleep(0.3)

    print(f"\n{'='*45}")
    print(f"  SKU groups fixed : {len(dupes)}")
    print(f"  Products updated : {total_fixed}")
    print(f"  Errors           : {len(errors)}")
    if errors:
        print("\n  Failed IDs:")
        for e in errors:
            print(f"    ID {e['id']}: {e['error']}")
    print(f"{'='*45}")
    print("\nDone.")


if __name__ == '__main__':
    main()
