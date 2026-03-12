#!/usr/bin/env python3
"""
============================================================
Master Stock Audit
============================================================
Reads the WooCommerce export and classifies every product
by supplier. Produces:

  stock_control/master_stock.csv   — every product + supplier + gaps
  stock_control/unmatched.csv      — products with no supplier
  stock_control/price_alerts.csv   — zero/missing prices
  stock_control/missing_images.csv — products with no image
  stock_control/summary.txt        — overview report

USAGE:
  python3 stock_audit.py --wc-export /path/to/wc_export.csv

  # With Impulse stock for code matching:
  python3 stock_audit.py \
    --wc-export /path/to/wc_export.csv \
    --impulse-stock /path/to/impulse_products.csv
============================================================
"""

import csv, re, json, sys, argparse
from pathlib import Path
from collections import defaultdict

# ── Supplier classification rules ─────────────────────────────────────────────
# Each rule: (supplier_id, test_function)
# test_function(sku, name, categories, description) -> bool

HIRSCH_BRANDS = {
    'lg','samsung','hisense','defy','bosch','smeg','miele','kic','siemens',
    'electrolux','beko','candy','haier','sharp','toshiba','panasonic','sony',
    'aeg','zanussi','delonghi','nespresso','cloud nine','tefal','kenwood',
    'kitchenaid','whirlpool','brandt','hoover','russell hobbs','morphy richards',
    'fisher & paykel','fisher and paykel',
}

HIRSCH_CATEGORIES = {
    'appliances','fridges','stoves','washing','dishwasher','microwave',
    'air conditioner','aircon','freezer','tumble','oven',
}

def is_hirsch(sku, name, cats, desc):
    name_l = name.lower()
    cats_l = cats.lower()
    if any(b in name_l for b in HIRSCH_BRANDS):
        if any(c in cats_l for c in HIRSCH_CATEGORIES) or any(c in cats_l for c in ['tv','audio','sound']):
            return True
    return False

def is_tupperware(sku, name, cats, desc):
    return 'tupperware' in name.lower() or 'tupperware' in cats.lower()

def is_jewellery(sku, name, cats, desc):
    kw = ['ring','necklace','bracelet','earring','pendant','chain','bangle',
          'anklet','jewel','brooch','charm','locket']
    return any(k in name.lower() for k in kw) or 'jewel' in cats.lower()

def is_makokoya(sku, name, cats, desc):
    kw = ['carpet','rug','mat','curtain','blind','runner']
    cats_l = cats.lower()
    return any(k in name.lower() for k in kw) or any(k in cats_l for k in kw)

def is_power_warehouse(sku, name, cats, desc):
    kw = ['ups','inverter','solar','battery','lithium','mecer','victron',
          'pylontech','revov','generator','load shed','loadshed']
    name_l, cats_l = name.lower(), cats.lower()
    return any(k in name_l for k in kw) or 'loadshedding' in cats_l

CLASSIFICATION_RULES = [
    # (supplier_id, fn) — order matters, first match wins
    ('tupperware',      is_tupperware),
    ('jewellery',       is_jewellery),
    ('power_warehouse', is_power_warehouse),
    ('hirschs',         is_hirsch),
    ('makokoya',        is_makokoya),
]

def classify(sku, name, cats, desc, impulse_codes):
    if sku.upper() in impulse_codes:
        return 'impulse'
    for supplier_id, fn in CLASSIFICATION_RULES:
        if fn(sku, name, cats, desc):
            return supplier_id
    return 'unknown'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--wc-export',     required=True)
    parser.add_argument('--impulse-stock', default='')
    parser.add_argument('--out',           default='stock_control')
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(exist_ok=True)

    # Load Impulse codes
    impulse_codes = set()
    if args.impulse_stock and Path(args.impulse_stock).exists():
        with open(args.impulse_stock) as f:
            for row in csv.DictReader(f):
                code = row.get('supplier_code', '').strip().upper()
                if code:
                    impulse_codes.add(code)
        print(f'Loaded {len(impulse_codes)} Impulse codes')

    # Load WC export
    with open(args.wc_export, encoding='utf-8-sig') as f:
        wc = list(csv.DictReader(f))
    print(f'Loaded {len(wc)} WC products')

    # Classify every product
    master = []
    by_supplier = defaultdict(list)
    price_alerts = []
    missing_images = []
    missing_desc = []

    for r in wc:
        sku       = r.get('SKU', '').strip()
        name      = r.get('Name', '').strip()
        cats      = r.get('Categories', '')
        desc      = r.get('Description', '')
        price     = r.get('Regular price', '').strip()
        image     = r.get('Images', '').strip()
        published = r.get('Published', '')
        wc_id     = r.get('ID', '')
        prod_type = r.get('Type', '')

        # Skip variations (child products)
        if prod_type == 'variation':
            continue

        supplier = classify(sku, name, cats, desc, impulse_codes)

        # Data quality flags
        has_price = bool(price and price not in ('0', '0.0', ''))
        has_image = bool(image)
        has_desc  = bool(desc.strip())
        is_pub    = published == '1'

        gaps = []
        if not has_price: gaps.append('no_price')
        if not has_image: gaps.append('no_image')
        if not has_desc:  gaps.append('no_description')
        if not sku:       gaps.append('no_sku')

        row = {
            'wc_id':        wc_id,
            'sku':          sku,
            'name':         name,
            'type':         prod_type,
            'published':    is_pub,
            'supplier':     supplier,
            'regular_price': price,
            'has_price':    has_price,
            'has_image':    has_image,
            'has_desc':     has_desc,
            'categories':   cats,
            'gaps':         '|'.join(gaps),
        }
        master.append(row)
        by_supplier[supplier].append(row)

        if not has_price and is_pub:
            price_alerts.append({'wc_id': wc_id, 'sku': sku, 'name': name[:60],
                                  'supplier': supplier, 'issue': 'published with no price'})
        if not has_image:
            missing_images.append({'wc_id': wc_id, 'sku': sku, 'name': name[:60],
                                   'supplier': supplier})
        if not has_desc:
            missing_desc.append({'wc_id': wc_id, 'sku': sku, 'name': name[:60],
                                 'supplier': supplier})

    # Write master CSV
    fields = ['wc_id','sku','name','type','published','supplier',
              'regular_price','has_price','has_image','has_desc','categories','gaps']
    with open(out_dir / 'master_stock.csv', 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader(); w.writerows(master)

    # Unmatched
    unmatched = [r for r in master if r['supplier'] == 'unknown']
    with open(out_dir / 'unmatched.csv', 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader(); w.writerows(unmatched)

    # Price alerts
    with open(out_dir / 'price_alerts.csv', 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=['wc_id','sku','name','supplier','issue'])
        w.writeheader(); w.writerows(price_alerts)

    # Missing images
    with open(out_dir / 'missing_images.csv', 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=['wc_id','sku','name','supplier'])
        w.writeheader(); w.writerows(missing_images)

    # Summary report
    total = len(master)
    published = sum(1 for r in master if r['published'])
    lines = [
        '=' * 60,
        'MASTER STOCK AUDIT',
        '=' * 60,
        f'  Total products:    {total}',
        f'  Published:         {published}',
        f'  Unpublished:       {total - published}',
        '',
        'BY SUPPLIER:',
    ]
    for sup, items in sorted(by_supplier.items(), key=lambda x: -len(x[1])):
        pub   = sum(1 for r in items if r['published'])
        nprice= sum(1 for r in items if not r['has_price'])
        nimg  = sum(1 for r in items if not r['has_image'])
        ndesc = sum(1 for r in items if not r['has_desc'])
        lines.append(f'  {sup:<20} {len(items):4d} total  {pub:4d} pub  '
                     f'(no price:{nprice}  no img:{nimg}  no desc:{ndesc})')

    lines += [
        '',
        'DATA GAPS (published products):',
        f'  Missing price:     {len(price_alerts)}',
        f'  Missing image:     {len(missing_images)}',
        f'  Missing desc:      {len(missing_desc)}',
        f'  No supplier match: {len(unmatched)}',
        '',
        'OUTPUT FILES:',
        f'  {out_dir}/master_stock.csv',
        f'  {out_dir}/unmatched.csv',
        f'  {out_dir}/price_alerts.csv',
        f'  {out_dir}/missing_images.csv',
        '=' * 60,
    ]
    report = '\n'.join(lines)
    print(report)
    with open(out_dir / 'summary.txt', 'w') as f:
        f.write(report)


if __name__ == '__main__':
    main()
