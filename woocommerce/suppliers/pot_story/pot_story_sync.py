#!/usr/bin/env python3
"""
Pot Story price, stock & dimension sync.
Applies 40% markup on supplier cost prices and fills L/W/H/kg for all Potstory products.
"""

import csv, os, re

MARKUP = 1.40
MASTER_CSV = os.path.join(os.path.dirname(__file__), '../../stock_control/master_wc_import.csv')

# ─────────────────────────────────────────────────────────────────────────────
# Product data: keyed by WC product ID (int)
# dims: (height_cm, width_cm, length_cm)   length = depth for rectangular items
# weight_kg: estimated (fibrecite / thick plastic pots)
# cost: supplier cost excl. VAT (Jan 2022 price list)
# note: for variable products, cost = cheapest variant (price shown as "from")
#       dims = largest variant dims
# ─────────────────────────────────────────────────────────────────────────────

PRODUCTS = {
    # ── SIMPLE products ───────────────────────────────────────────────────────

    # Rose Pot  H350 x W350mm
    7729: dict(cost=116.00,  h=35.0, w=35.0, l=35.0, kg=1.5),
    9433: dict(cost=116.00,  h=35.0, w=35.0, l=35.0, kg=1.5),   # Classic Round Rose Pot Brown
    9434: dict(cost=116.00,  h=35.0, w=35.0, l=35.0, kg=1.5),   # Classic Round Rose Pot White

    # Scroll Pot  H310 x W380mm
    7732: dict(cost=128.20,  h=31.0, w=38.0, l=38.0, kg=1.3),
    9783: dict(cost=128.20,  h=31.0, w=38.0, l=38.0, kg=1.3),   # Scroll Pot Brown

    # Shrub Pot  H400 x W460mm
    7747: dict(cost=152.50,  h=40.0, w=46.0, l=46.0, kg=2.5),

    # Swazi Pot  H370 x W640mm (wide, shallow-ish)
    7751: dict(cost=193.50,  h=37.0, w=64.0, l=64.0, kg=3.5),

    # Elfin  H150 x W240mm
    7768: dict(cost=78.00,   h=15.0, w=24.0, l=24.0, kg=0.5),
    9778: dict(cost=78.00,   h=15.0, w=24.0, l=24.0, kg=0.5),   # Elfin Pot Brown

    # Tortosa  H670 x W630mm
    7771: dict(cost=427.00,  h=67.0, w=63.0, l=63.0, kg=6.0),

    # Constantia  H670 x W430mm
    7772: dict(cost=215.30,  h=67.0, w=43.0, l=43.0, kg=4.5),

    # Chelsea Window Box Small  H250 x W210 x L520mm
    7773: dict(cost=116.00,  h=25.0, w=21.0, l=52.0, kg=1.0),

    # Bonsai Rectangle  H120 x W320 x L230mm
    7775: dict(cost=78.00,   h=12.0, w=23.0, l=32.0, kg=0.8),
    # Bonsai Pot (round) H120 x W460 x L360mm
    7777: dict(cost=109.00,  h=12.0, w=46.0, l=36.0, kg=1.0),
    9776: dict(cost=109.00,  h=12.0, w=46.0, l=36.0, kg=1.0),   # Bonsai Pot White

    # Serena  H380 x W445 x L445mm
    7776: dict(cost=208.00,  h=38.0, w=44.5, l=44.5, kg=3.0),

    # Rhino Pot (Square Pot w/ Rhino Motive)  H379 x W450 x L450mm
    7778: dict(cost=208.00,  h=37.9, w=45.0, l=45.0, kg=3.5),

    # Square Pot (plain)  H500 x W445 x L445mm
    7779: dict(cost=243.00,  h=50.0, w=44.5, l=44.5, kg=4.0),
    9432: dict(cost=243.00,  h=50.0, w=44.5, l=44.5, kg=4.0),   # Classic Square Pot Brown

    # African Jungle  H500 x W445 x L445mm (elephant motif square pot)
    7780: dict(cost=243.00,  h=50.0, w=44.5, l=44.5, kg=4.5),
    9430: dict(cost=243.00,  h=50.0, w=44.5, l=44.5, kg=4.5),   # Square Pot Elephant Brown
    9431: dict(cost=243.00,  h=50.0, w=44.5, l=44.5, kg=4.5),   # Square Pot Elephant White

    # Classic Pot Medium  H675 x W420 x L420mm
    7782: dict(cost=215.50,  h=67.5, w=42.0, l=42.0, kg=4.0),
    9429: dict(cost=215.50,  h=67.5, w=42.0, l=42.0, kg=4.0),   # Medium Classic Round Pot White

    # Classic Pot Large  H840 x W500 x L500mm
    9428: dict(cost=260.00,  h=84.0, w=50.0, l=50.0, kg=5.5),   # Large Classic Round Pot White

    # Window Box Large  H220 x W295 x L675mm
    7783: dict(cost=135.00,  h=22.0, w=29.5, l=67.5, kg=1.5),

    # Casablanca  H900 x W480mm
    14780: dict(cost=284.35, h=90.0, w=48.0, l=48.0, kg=7.0),

    # Desk Bowl  H150 x W335mm
    9779: dict(cost=71.50,   h=15.0, w=33.5, l=33.5, kg=0.7),

    # Bowl Pot Large  H240 x W500mm
    9780: dict(cost=135.00,  h=24.0, w=50.0, l=50.0, kg=2.0),

    # Wall Pot  H270 x W535mm (bracket mount – use l=15 for bracket depth)
    9781: dict(cost=116.00,  h=27.0, w=53.5, l=15.0, kg=1.5),

    # Cordoba  H320 x W430mm
    9784: dict(cost=133.00,  h=32.0, w=43.0, l=43.0, kg=2.0),

    # Centurion  H330 x W450 x L450mm
    9785: dict(cost=152.50,  h=33.0, w=45.0, l=45.0, kg=2.5),

    # ── VARIABLE products (parent row) ────────────────────────────────────────
    # Price = cheapest variant cost × 1.40  (WC shows "from R…")
    # Dims  = largest variant dims

    # Rib Pot: Small H330×W350, Large H500×W460 → cost from R116 (small)
    7739: dict(cost=116.00,  h=50.0, w=46.0, l=46.0, kg=2.5),

    # Acapulco: Small H140×W330, Large H260×W530 → cost from R71.3 (small)
    7744: dict(cost=71.30,   h=26.0, w=53.0, l=53.0, kg=1.5),

    # Oval Pot S/M/L: L=H300×W670×D480 → cost from R128.2 (small)
    7758: dict(cost=128.20,  h=30.0, w=67.0, l=48.0, kg=3.0),

    # Tivolee Med/Large: Med H510×W360, Lrg H670×W430 → cost from R133 (med)
    7762: dict(cost=133.00,  h=67.0, w=43.0, l=43.0, kg=4.5),

    # Angolan Med/Large: Med H270×W360, Lrg H400×W500 → cost from R94.6 (med)
    7767: dict(cost=94.60,   h=40.0, w=50.0, l=50.0, kg=3.0),

    # Haiti Med/Large: Med H240×W500, Lrg H300×W500 → cost from R90 (small)
    7769: dict(cost=90.00,   h=30.0, w=50.0, l=50.0, kg=3.0),

    # Octagon Med/Large: Med H700×W395, Lrg H500×W380 → cost from R172 (small)
    7770: dict(cost=172.00,  h=70.0, w=39.5, l=39.5, kg=5.0),

    # Window Basket Small/Large: Sm H250×W210×L520, Lg H250×W210×L725 → cost from R116 (sm)
    7774: dict(cost=116.00,  h=25.0, w=21.0, l=72.5, kg=1.5),

    # Twisted Pots S/M/L: Sm H360×W350, Med H495×W420, Lrg H650×W420 → cost from R139 (sm)
    14794: dict(cost=139.00, h=65.0, w=42.0, l=42.0, kg=5.0),

    # Ulundi Med/Large: Med H280×W400, Lrg H340×W460 → cost from R116 (med)
    14798: dict(cost=116.00, h=34.0, w=46.0, l=46.0, kg=2.5),

    # Savante Med/Large/XL: Med H380×W310, Lrg H530×W385, XL H770×W430 → cost from R116 (med)
    14801: dict(cost=116.00, h=77.0, w=43.0, l=43.0, kg=6.0),

    # Quattro Sm/Lrg: Sm H500×W400, Lrg H735×W390 → cost from R181.5 (sm)
    14805: dict(cost=181.50, h=73.5, w=39.0, l=39.0, kg=6.5),

    # Inca Pot Sm/Lrg: Sm H330×W350, Lrg H400×W470 → cost from R116 (sm)
    14808: dict(cost=116.00, h=40.0, w=47.0, l=47.0, kg=2.5),

    # Succulent Sm/Med/Lrg: Sm H160×W490, Med H190×W560, Lrg H290×W770 → cost from R106.4 (sm)
    14811: dict(cost=106.40, h=29.0, w=77.0, l=77.0, kg=4.0),

    # Classic Pot variable (Med+Large): Med H675×W420, Lrg H840×W500 → cost from R215.5 (med)
    14816: dict(cost=215.50, h=84.0, w=50.0, l=50.0, kg=5.5),
}

# Products where we know they exist but can't match price (newer/unmatched)
# We'll mark in-stock and fill dims where possible, leave price blank
UNMATCHED_DIMS = {
    # Fern Vase (tall, narrow) – estimated
    9769: dict(h=40.0, w=15.0, l=15.0, kg=1.0),
    9770: dict(h=40.0, w=15.0, l=15.0, kg=1.0),
    9771: dict(h=40.0, w=15.0, l=15.0, kg=1.0),
    # Meadow Pot – medium round, estimated
    9772: dict(h=30.0, w=35.0, l=35.0, kg=1.5),
    9773: dict(h=30.0, w=35.0, l=35.0, kg=1.5),
    # Pinnacle Pot – tall narrow, estimated
    9774: dict(h=55.0, w=25.0, l=25.0, kg=2.5),
    9788: dict(h=55.0, w=25.0, l=25.0, kg=2.5),
    # Flora Pot – medium, estimated similar to Inca Small
    9777: dict(h=33.0, w=35.0, l=35.0, kg=1.5),
    # Amber Pot – medium, estimated
    9786: dict(h=35.0, w=38.0, l=38.0, kg=2.0),
    # Tweed Pot – medium textured, estimated
    9787: dict(h=35.0, w=40.0, l=40.0, kg=2.0),
    # Cuban Pot (similar to Cordoba)
    9782: dict(h=32.0, w=43.0, l=43.0, kg=2.0),
    # White Planter with Wooden Stand – estimated
    9859: dict(h=45.0, w=35.0, l=35.0, kg=3.0),
    # Small Red – small generic pot
    9862: dict(h=20.0, w=22.0, l=22.0, kg=0.8),
}


def selling_price(cost):
    return round(cost * MARKUP, 2)


def weight_estimate(h_cm):
    """Fallback weight estimate from height if not specified."""
    if h_cm < 15:   return 0.5
    if h_cm < 25:   return 0.8
    if h_cm < 35:   return 1.5
    if h_cm < 45:   return 2.5
    if h_cm < 55:   return 3.5
    if h_cm < 70:   return 4.5
    return 6.0


def main():
    with open(MASTER_CSV, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    updated = 0
    matched_ids = set(PRODUCTS.keys()) | set(UNMATCHED_DIMS.keys())

    for row in rows:
        raw_id = row.get('ID', '').strip()
        if not raw_id:
            continue
        try:
            pid = int(float(raw_id))
        except ValueError:
            continue

        if pid not in matched_ids:
            continue

        # Always mark in stock
        row['In stock?'] = '1'

        data = PRODUCTS.get(pid)
        if data:
            price = selling_price(data['cost'])
            row['Regular price'] = str(price)
            row['Sale price']    = ''
            row['Weight (kg)']   = str(data['kg'])
            row['Height (cm)']   = str(data['h'])
            row['Width (cm)']    = str(data['w'])
            row['Length (cm)']   = str(data['l'])
            updated += 1
            print(f"  ✓ [{pid}] {row['Name'][:50]:<50}  R{data['cost']:>8.2f} → R{price:>8.2f}  "
                  f"H{data['h']}×W{data['w']}×L{data['l']}cm  {data['kg']}kg")
        else:
            dim = UNMATCHED_DIMS[pid]
            row['Weight (kg)'] = str(dim['kg'])
            row['Height (cm)'] = str(dim['h'])
            row['Width (cm)']  = str(dim['w'])
            row['Length (cm)'] = str(dim['l'])
            updated += 1
            print(f"  ~ [{pid}] {row['Name'][:50]:<50}  (no price – dims only)  "
                  f"H{dim['h']}×W{dim['w']}×L{dim['l']}cm  {dim['kg']}kg")

    with open(MASTER_CSV, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n{'─'*70}")
    print(f"  Done — {updated} Potstory products updated in master_wc_import.csv")
    print(f"  Markup applied: {int((MARKUP-1)*100)}%")


if __name__ == '__main__':
    main()
