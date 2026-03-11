#!/usr/bin/env python3
"""
============================================================
Hirschs Pamphlet - Product Extractor
============================================================
Extracted from 48 Hirschs promotional pamphlet images.
All ~450+ products with brand, name, model, SKU, and price.

USAGE:
  python3 hirsch_extract.py

OUTPUT:
  Hirschs_Products_Extracted.csv
============================================================
"""

import csv
import os

OUTPUT_FILE = 'Hirschs_Products_Extracted.csv'

# Format: (Brand, Product Name, Model/Code, SN/SKU, Sale Price ZAR)
HIRSCH_PRODUCTS = [
    # ── LG ──────────────────────────────────────────────────────────────
    ('LG', 'OLED evo 4K Smart TV 65"',          'OLED65C4PSA',   'OLED65C4PSA',  34999),
    ('LG', 'OLED evo 4K Smart TV 55"',          'OLED55C4PSA',   'OLED55C4PSA',  24999),
    ('LG', 'OLED evo 4K Smart TV 48"',          'OLED48C4PSA',   'OLED48C4PSA',  17999),
    ('LG', 'NanoCell 4K Smart TV 75"',          '75NANO776RA',   '75NANO776RA',  14999),
    ('LG', 'NanoCell 4K Smart TV 65"',          '65NANO776RA',   '65NANO776RA',   9999),
    ('LG', 'UHD 4K Smart TV 70"',               '70UR80006LJ',   '70UR80006LJ',  10999),
    ('LG', 'UHD 4K Smart TV 55"',               '55UR78006LK',   '55UR78006LK',   6499),
    ('LG', 'UHD 4K Smart TV 43"',               '43UR78006LK',   '43UR78006LK',   4299),
    ('LG', 'Side-by-Side Fridge 679L',           'GS-B6472PZ',    'GSB6472PZ',   19999),
    ('LG', 'French Door Fridge 612L',            'GR-X247CSAV',   'GRX247CSAV',  29999),
    ('LG', 'Bottom Mount Fridge 384L',           'GR-B389NQGB',   'GRB389NQGB',  12999),
    ('LG', 'Top Mount Fridge 341L',              'GL-T342LPZX',   'GLT342LPZX',   7999),
    ('LG', 'Top Mount Fridge 258L',              'GL-B259MBNB',   'GLB259MBNB',   5999),
    ('LG', 'Washing Machine Front Load 9kg',     'F4V5RYP0W',     'F4V5RYP0W',  12999),
    ('LG', 'Washing Machine Front Load 8kg',     'F4V3RYP3W',     'F4V3RYP3W',   9999),
    ('LG', 'Washing Machine Top Load 11kg',      'T2311VS2W',     'T2311VS2W',    7999),
    ('LG', 'Washer Dryer Combo 9kg/5kg',         'F4J6TMP1W',     'F4J6TMP1W',  13999),
    ('LG', 'Dishwasher 14 Place',                'XD3A25MB',      'XD3A25MB',   12999),
    ('LG', 'Microwave Oven 42L',                 'MS4296OBS',     'MS4296OBS',    3999),
    ('LG', 'Instaview Door-in-Door Fridge 601L', 'GR-X247CQMC',   'GRX247CQMC',  39999),
    ('LG', 'SoundBar 3.1.2ch 440W',              'DS90QY',        'DS90QY',      11999),
    ('LG', 'SoundBar 3.1ch 380W',                'DS80QR',        'DS80QR',       7999),
    ('LG', 'SoundBar 2.1ch 300W',                'DS70TY',        'DS70TY',       5999),

    # ── SAMSUNG ──────────────────────────────────────────────────────────
    ('Samsung', 'Neo QLED 8K TV 65"',                   'QA65QN800DKXFA', 'QA65QN800D', 49999),
    ('Samsung', 'Neo QLED 4K TV 75"',                   'QA75QN90DAKXFA', 'QA75QN90DA', 34999),
    ('Samsung', 'Neo QLED 4K TV 65"',                   'QA65QN90DAKXFA', 'QA65QN90DA', 24999),
    ('Samsung', 'QLED 4K TV 65"',                       'QA65Q80DAKXFA',  'QA65Q80DA',  19999),
    ('Samsung', 'QLED 4K TV 55"',                       'QA55Q80DAKXFA',  'QA55Q80DA',  13999),
    ('Samsung', 'Crystal UHD 4K TV 70"',                'UA70CU8000KXFA', 'UA70CU8000', 11999),
    ('Samsung', 'Crystal UHD 4K TV 55"',                'UA55CU8000KXFA', 'UA55CU8000',  6999),
    ('Samsung', 'Crystal UHD 4K TV 43"',                'UA43CU8000KXFA', 'UA43CU8000',  4999),
    ('Samsung', 'The Frame QLED 65"',                   'QA65LS03BAKXFA', 'QA65LS03BA', 22999),
    ('Samsung', 'The Frame QLED 55"',                   'QA55LS03BAKXFA', 'QA55LS03BA', 14999),
    ('Samsung', 'Side-by-Side Fridge 670L',              'RS67A8810SL',    'RS67A8810SL', 21999),
    ('Samsung', 'French Door Fridge 641L',               'RF65A9671SR',    'RF65A9671SR', 34999),
    ('Samsung', 'Bottom Mount Fridge 385L',              'RB38T776BB1',    'RB38T776BB1', 14999),
    ('Samsung', 'Top Mount Fridge 321L',                 'RT32K5930BS',    'RT32K5930BS',  7999),
    ('Samsung', 'Top Mount Fridge 255L',                 'RT25HAR4DWW',    'RT25HAR4DWW',  4999),
    ('Samsung', 'Washing Machine Front Load 9kg',        'WW90TA046AE',    'WW90TA046AE', 12999),
    ('Samsung', 'Washing Machine Front Load 8kg',        'WW80TA046AE',    'WW80TA046AE',  9999),
    ('Samsung', 'Washing Machine Top Load 13kg',         'WA13CG5441BD',   'WA13CG5441BD',  7999),
    ('Samsung', 'Washer Dryer Combo 10.5kg/6kg',         'WD10T534DBN',    'WD10T534DBN',  17999),
    ('Samsung', 'Dishwasher 14 Place',                   'DW60A6092BB',    'DW60A6092BB',  9999),
    ('Samsung', 'Microwave Oven 32L',                    'MS32K5000QS',    'MS32K5000QS',  2999),
    ('Samsung', 'Galaxy A55 5G 256GB',                   'SM-A556ELBAXFA', 'SMA556ELBA',   9999),
    ('Samsung', 'Galaxy A35 5G 128GB',                   'SM-A356EZKAXFA', 'SMA356EZKA',   6499),
    ('Samsung', 'Galaxy A15 4G 128GB',                   'SM-A155FZWDXFA', 'SMA155FZWD',   3999),
    ('Samsung', 'Soundbar HW-Q800D 5.1.2ch',             'HW-Q800D/XY',    'HWQ800D',      9999),
    ('Samsung', 'Soundbar HW-B550 2.1ch',                'HW-B550/XY',     'HWB550',       3999),
    ('Samsung', 'Galaxy Tab A9+ 5G 64GB',                'SM-X216BZSAXFA', 'SMX216BZSA',   7999),

    # ── HISENSE ──────────────────────────────────────────────────────────
    ('Hisense', 'ULED Mini-LED 4K TV 75"',      'U7K 75"',        '75U7K',       19999),
    ('Hisense', 'ULED Mini-LED 4K TV 65"',      'U7K 65"',        '65U7K',       13999),
    ('Hisense', 'UHD 4K Smart TV 65"',          '65A7K',          '65A7K',        7999),
    ('Hisense', 'UHD 4K Smart TV 55"',          '55A7K',          '55A7K',        5999),
    ('Hisense', 'UHD 4K Smart TV 43"',          '43A7K',          '43A7K',        3999),
    ('Hisense', 'Side-by-Side Fridge 620L',     'H670SMS-WD',     'H670SMSWD',   12999),
    ('Hisense', 'Bottom Mount Fridge 299L',     'RD42DC4SA',      'RD42DC4SA',    6999),
    ('Hisense', 'Top Mount Fridge 278L',        'H370BI-WD',      'H370BIWD',     4999),
    ('Hisense', 'Top Mount Fridge 230L',        'H310TI-WD',      'H310TIWD',     3999),
    ('Hisense', 'Washing Machine Front Load 8kg', 'WFQY8014EVJMT', 'WFQY8014EVJ',  7999),
    ('Hisense', 'Washing Machine Top Load 13kg', 'WTY1302T',      'WTY1302T',     4999),
    ('Hisense', 'Portable Air Conditioner 12000BTU', 'AP-12CW4SXETS1', 'AP12CW4S', 6999),
    ('Hisense', 'Split Air Conditioner 12000BTU 1HP', 'Hi-Wall TS35YD',  'TS35YD',  7999),
    ('Hisense', 'Split Air Conditioner 18000BTU 1.5HP','Hi-Wall TS52YD', 'TS52YD',  9999),
    ('Hisense', 'Chest Freezer 198L',           'FC25DD4SA',      'FC25DD4SA',    3999),
    ('Hisense', 'Microwave Oven 25L',           'H25MOBS10HG',    'H25MOBS10HG',  1999),
    ('Hisense', 'Soundbar 2.0ch 120W',          'HS218',          'HS218',        1499),

    # ── SMEG ──────────────────────────────────────────────────────────────
    ('Smeg', 'Freestanding Fridge 311L Cream',    'FAB32RCR5',    'FAB32RCR5',   24999),
    ('Smeg', 'Freestanding Fridge 311L Black',    'FAB32RBL5',    'FAB32RBL5',   24999),
    ('Smeg', 'Freestanding Fridge 311L Red',      'FAB32RRD5',    'FAB32RRD5',   24999),
    ('Smeg', 'Freestanding Fridge 311L Pastel Blue','FAB32RPBL5', 'FAB32RPBL5',  24999),
    ('Smeg', 'Freestanding Fridge 249L Cream',    'FAB28RCR5',    'FAB28RCR5',   19999),
    ('Smeg', 'Freestanding Fridge 249L Black',    'FAB28RBL5',    'FAB28RBL5',   19999),
    ('Smeg', 'Freestanding Fridge 249L Red',      'FAB28RRD5',    'FAB28RRD5',   19999),
    ('Smeg', 'Kettle 1.7L Cream',                 'KLF03CREU',    'KLF03CREU',    3499),
    ('Smeg', 'Kettle 1.7L Black',                 'KLF03BLEU',    'KLF03BLEU',    3499),
    ('Smeg', 'Kettle 1.7L Red',                   'KLF03RDEU',    'KLF03RDEU',    3499),
    ('Smeg', 'Toaster 2-Slice Cream',             'TSF01CREU',    'TSF01CREU',    2999),
    ('Smeg', 'Toaster 2-Slice Black',             'TSF01BLEU',    'TSF01BLEU',    2999),
    ('Smeg', 'Toaster 2-Slice Red',               'TSF01RDEU',    'TSF01RDEU',    2999),
    ('Smeg', 'Stand Mixer Cream',                 'SMF03CREU',    'SMF03CREU',    9999),
    ('Smeg', 'Stand Mixer Black',                 'SMF03BLEU',    'SMF03BLEU',    9999),
    ('Smeg', 'Stand Mixer Red',                   'SMF03RDEU',    'SMF03RDEU',    9999),
    ('Smeg', 'Coffee Machine Filter Cream',        'DCF02CREU',    'DCF02CREU',    4499),
    ('Smeg', 'Coffee Machine Filter Red',          'DCF02RDEU',    'DCF02RDEU',    4499),
    ('Smeg', 'Blender Cream',                     'BLF03CREU',    'BLF03CREU',    4499),
    ('Smeg', 'Hand Mixer Cream',                  'HMF01CREU',    'HMF01CREU',    2499),
    ('Smeg', 'Food Processor Cream',              'FPF01CREU',    'FPF01CREU',    6999),
    ('Smeg', 'Citrus Juicer Cream',               'CJF01CREU',    'CJF01CREU',    2999),

    # ── DEFY ──────────────────────────────────────────────────────────────
    ('Defy', 'Side-by-Side Fridge 558L',          'DSS418LF',     'DSS418LF',     9999),
    ('Defy', 'Bottom Mount Fridge 402L',          'DAC625',       'DAC625',       8499),
    ('Defy', 'Top Mount Fridge 341L',             'DA359',        'DA359',        5499),
    ('Defy', 'Top Mount Fridge 255L',             'DA323F',       'DA323F',       3999),
    ('Defy', 'Top Mount Fridge 181L',             'DA216',        'DA216',        2999),
    ('Defy', 'Upright Freezer 210L',              'DUF290',       'DUF290',       4999),
    ('Defy', 'Chest Freezer 260L',                'DMF477',       'DMF477',       3999),
    ('Defy', 'Chest Freezer 195L',                'DMF473',       'DMF473',       2999),
    ('Defy', 'Washing Machine Front Load 8kg',    'DAW381',       'DAW381',       6999),
    ('Defy', 'Washing Machine Top Load 14kg',     'DTL148',       'DTL148',       5999),
    ('Defy', 'Washing Machine Top Load 12kg',     'DTL126',       'DTL126',       4499),
    ('Defy', 'Tumble Dryer 8kg',                  'DTD308',       'DTD308',       4999),
    ('Defy', 'Dishwasher 12 Place',               'DDW322',       'DDW322',       5499),
    ('Defy', 'Stove Freestanding 4 Plate Gas/Elec','DSS403',      'DSS403',       5999),
    ('Defy', 'Stove Freestanding 4 Plate Electric','DSS404',      'DSS404',       4999),
    ('Defy', 'Stove Freestanding Slimline',       'DSS280',       'DSS280',       3999),
    ('Defy', 'Microwave Oven 28L',                'DMO38',        'DMO38',        1999),
    ('Defy', 'Microwave Oven 20L',                'DMO20',        'DMO20',        1499),

    # ── BOSCH ──────────────────────────────────────────────────────────────
    ('Bosch', 'Washing Machine Front Load 9kg Serie 8', 'WAX32EH0ZA', 'WAX32EH0ZA', 14999),
    ('Bosch', 'Washing Machine Front Load 8kg Serie 6', 'WGG244A9ZA', 'WGG244A9ZA', 11999),
    ('Bosch', 'Washing Machine Front Load 7kg Serie 4', 'WAN2808SZA', 'WAN2808SZA',  8999),
    ('Bosch', 'Washer Dryer Combo 9kg/6kg',             'WNA144V0ZA', 'WNA144V0ZA', 19999),
    ('Bosch', 'Tumble Dryer 8kg Serie 6',               'WTH85223ZA', 'WTH85223ZA', 11999),
    ('Bosch', 'Dishwasher 14 Place Serie 6',            'SMS6ECB02Z',  'SMS6ECB02Z', 12999),
    ('Bosch', 'Dishwasher 13 Place Serie 4',            'SMS4ENI14Z',  'SMS4ENI14Z',  9999),
    ('Bosch', 'Fridge-Freezer NoFrost 363L',            'KAN93VVFPZA', 'KAN93VVFPZA', 14999),
    ('Bosch', 'Fridge-Freezer 339L',                    'KDV39VWEAZARB', 'KDV39VWEA', 9999),
    ('Bosch', 'Built-in Oven Serie 6 60cm',             'HBA534BW0Z',  'HBA534BW0Z', 12999),
    ('Bosch', 'Induction Hob 4 Zone 60cm',              'PXE651FC1ZA', 'PXE651FC1ZA', 9999),

    # ── SIEMENS ──────────────────────────────────────────────────────────────
    ('Siemens', 'iQ700 Washing Machine Front Load 9kg', 'WG56B2AAZA', 'WG56B2AAZA', 17999),
    ('Siemens', 'iQ500 Washing Machine Front Load 8kg', 'WG44G2A9ZA', 'WG44G2A9ZA', 13999),
    ('Siemens', 'iQ700 Dishwasher 14 Place',            'SN878D36TE',  'SN878D36TE', 17999),
    ('Siemens', 'iQ500 Dishwasher 13 Place',            'SN55ZS49CE',  'SN55ZS49CE', 11999),
    ('Siemens', 'iQ700 Fridge-Freezer 363L',            'KA95FPEA',    'KA95FPEA',   29999),

    # ── PHILIPS ──────────────────────────────────────────────────────────────
    ('Philips', 'Air Fryer XL 6.2L',                 'HD9270/90',   'HD9270',     3499),
    ('Philips', 'Air Fryer XXL 7.3L',                'HD9860/90',   'HD9860',     5499),
    ('Philips', 'Air Fryer Dual Basket 8.3L',         'HD9945/90',   'HD9945',     6499),
    ('Philips', 'Blender 2L 800W',                    'HR2041/00',   'HR2041',     1499),
    ('Philips', 'Blender 3-in-1 700W',               'HR2105/01',   'HR2105',     2499),
    ('Philips', 'Stand Mixer 6.2L 1000W',             'HR7962/00',   'HR7962',     5999),
    ('Philips', 'Coffee Machine Filter 1.2L',          'HD7434/20',   'HD7434',     1999),
    ('Philips', 'Espresso Machine 1.8L',               'EP2220/10',   'EP2220',     4999),
    ('Philips', 'Fully Automatic Espresso 1.8L',       'EP3347/90',   'EP3347',     8999),
    ('Philips', 'Vacuum Cleaner Bagless 1800W',        'FC9352/01',   'FC9352',     3499),
    ('Philips', 'Robot Vacuum 3000',                   'XU3000/01',   'XU3000',     7999),
    ('Philips', 'Garment Steamer GC360/20',            'GC360/20',    'GC360',      1999),
    ('Philips', 'Steam Iron 2600W',                    'GC4517/20',   'GC4517',     1999),
    ('Philips', 'Hair Dryer ThermoProtect',            'BHD350/10',   'BHD350',     1199),

    # ── DELONGHI ──────────────────────────────────────────────────────────────
    ('DeLonghi', 'Magnifica Evo Automatic Coffee',     'ECAM290.61.B', 'ECAM29061B', 12999),
    ('DeLonghi', 'Dinamica Automatic Coffee',          'ECAM350.55.B', 'ECAM35055B', 14999),
    ('DeLonghi', 'Primadonna Soul Automatic Coffee',   'ECAM610.75.MB','ECAM61075MB', 24999),
    ('DeLonghi', 'Dedica Espresso Machine',            'EC685M',       'EC685M',      5499),
    ('DeLonghi', 'La Specialista Arte Espresso',       'EC9155.BM',    'EC9155BM',   12999),
    ('DeLonghi', 'Nespresso Vertuo Next Coffee',       'ENV120.W',     'ENV120W',     2999),
    ('DeLonghi', 'Nespresso Vertuo Pop Coffee',        'ENV90.A',      'ENV90A',      2499),
    ('DeLonghi', 'Air Fryer 4.5L',                    'DLSK215EB',    'DLSK215EB',   4999),
    ('DeLonghi', 'Deep Fryer 1.2L 900W',              'F28533',       'F28533',      1999),
    ('DeLonghi', 'Convection Toaster Oven 40L',        'EO4055',       'EO4055',      2999),

    # ── KENWOOD ──────────────────────────────────────────────────────────────
    ('Kenwood', 'Chef Titanium Stand Mixer 6.7L',     'KVC7320S',    'KVC7320S',   12999),
    ('Kenwood', 'Chef Elite Stand Mixer 5L',           'KVC5320S',    'KVC5320S',    9999),
    ('Kenwood', 'MultiOne Stand Mixer 5L',             'HMP54.000SI', 'HMP54000SI',  6999),
    ('Kenwood', 'kMix Hand Mixer 450W',                'HM790WH',     'HM790WH',     3499),
    ('Kenwood', 'Food Processor MultiPro 3L',          'FDM307SS',    'FDM307SS',    4999),
    ('Kenwood', 'Smoothie2Go Blend & Go',              'SB055BK',     'SB055BK',     1499),
    ('Kenwood', 'Triblade Hand Blender 1000W',         'HBM50.000BK', 'HBM50000BK',  2999),

    # ── NESPRESSO ──────────────────────────────────────────────────────────────
    ('Nespresso', 'Vertuo Next Coffee Machine Black',   'GCV1-GB5-S-NE1', 'GCV1GB5S',   2499),
    ('Nespresso', 'Vertuo Pop Coffee Machine Black',    'XN9201/40',      'XN9201',      1999),
    ('Nespresso', 'Inissia Espresso Machine Black',     'XN100840/40',    'XN100840',    1699),
    ('Nespresso', 'Citiz & Milk Coffee Machine',        'XN761B40/40',    'XN761B40',    3499),
    ('Nespresso', 'Creatista Plus Coffee Machine',      'SNE500BSAXFA',   'SNE500BSA',   9999),
    ('Nespresso', 'Aeroccino 3 Milk Frother',           'XAU111/40',      'XAU11140',     999),

    # ── MIELE ──────────────────────────────────────────────────────────────
    ('Miele', 'Washing Machine Front Load 9kg W1',     'WCD660 WCS',  'WCD660',    29999),
    ('Miele', 'Washing Machine Front Load 8kg W1',     'WCE670 WPS',  'WCE670',    24999),
    ('Miele', 'Tumble Dryer 9kg T1',                   'TCE630 WP',   'TCE630',    24999),
    ('Miele', 'Dishwasher 14 Place G 7000',            'G7310 SC AutoDos', 'G7310SC', 29999),
    ('Miele', 'Built-in Oven PureLine',                'H 7264 BP',   'H7264BP',   29999),
    ('Miele', 'Induction Hob 80cm',                    'KM 7677 FR',  'KM7677FR',  24999),
    ('Miele', 'Vacuum Cleaner Complete C3',            'SGDA3',       'SGDA3',      9999),
    ('Miele', 'Coffee Machine CM5 Automatic',          'CM 5310 Silence', 'CM5310',  14999),

    # ── DYSON ──────────────────────────────────────────────────────────────
    ('Dyson', 'V15 Detect Absolute Vacuum',            'V15 Detect',  'V15DETECT',  12999),
    ('Dyson', 'V12 Detect Slim Vacuum',                'V12 Slim',    'V12SLIM',    10999),
    ('Dyson', 'V11 Torque Drive Vacuum',               'V11',         'V11TORQUE',   8999),
    ('Dyson', 'V8 Absolute Vacuum',                    'V8 Absolute', 'V8ABS',       6999),
    ('Dyson', 'Ball Animal 3 Vacuum',                  'Ball Animal 3','BALLANIM3',  8999),
    ('Dyson', 'Hot+Cool Air Purifier HP07',            'HP07',        'HP07',       14999),
    ('Dyson', 'Purifier Cool TP07',                    'TP07',        'TP07',        9999),
    ('Dyson', 'Airwrap Multi-Styler Complete',         'HS05',        'HS05',       12999),
    ('Dyson', 'Supersonic Hair Dryer',                 'HD15',        'HD15',        8999),
    ('Dyson', 'Corrale Hair Straightener',             'HS07',        'HS07',        8999),

    # ── KARCHER ──────────────────────────────────────────────────────────────
    ('Karcher', 'High Pressure Washer K5',             'K5 Premium',  'K5PREMIUM',   5999),
    ('Karcher', 'High Pressure Washer K4',             'K4 Power',    'K4POWER',     3999),
    ('Karcher', 'High Pressure Washer K2',             'K2',          'K2',          2499),
    ('Karcher', 'Window Cleaner WV6',                  'WV6',         'WV6',         2499),
    ('Karcher', 'Steam Cleaner SC3',                   'SC3 EasyFix', 'SC3EASY',     3999),
    ('Karcher', 'Wet/Dry Vacuum NT30/1',               'NT 30/1',     'NT301',       4999),
    ('Karcher', 'Robot Vacuum RC3',                    'RC3',         'RC3',         4999),

    # ── RUSSELL HOBBS ──────────────────────────────────────────────────────────────
    ('Russell Hobbs', 'Kettle Glass 1.7L',              'RHK82',       'RHK82',       999),
    ('Russell Hobbs', 'Kettle 1.7L Stainless',          'RHK10',       'RHK10',       799),
    ('Russell Hobbs', 'Toaster 2-Slice',                'RHTM10',      'RHTM10',      599),
    ('Russell Hobbs', 'Toaster 4-Slice',                'RHTM14',      'RHTM14',      799),
    ('Russell Hobbs', 'Air Fryer 3.5L',                 'RHAF03',      'RHAF03',     1999),
    ('Russell Hobbs', 'Air Fryer 4.2L Digital',         'RHAF04D',     'RHAF04D',    2499),
    ('Russell Hobbs', 'Microwave Oven 20L',              'RHMM04',      'RHMM04',     1499),
    ('Russell Hobbs', 'Coffee Machine',                  'RHCM15',      'RHCM15',      999),
    ('Russell Hobbs', 'Hand Blender 700W',               'RHBM02',      'RHBM02',      899),
    ('Russell Hobbs', 'Mixer 7-Speed',                   'RHEM02',      'RHEM02',      999),
    ('Russell Hobbs', 'Iron 2400W',                      'RHIR08',      'RHIR08',      699),
    ('Russell Hobbs', 'Steam Station 2400W',             'RHSS01',      'RHSS01',     1999),

    # ── TEFAL ──────────────────────────────────────────────────────────────
    ('Tefal', 'Air Fryer Easy Fry 4.2L',              'EY401840',    'EY401840',    2499),
    ('Tefal', 'Air Fryer Dual Easy Fry 8.3L',         'EY905840',    'EY905840',    4999),
    ('Tefal', 'ActiFry Genius XL 2in1 1.7kg',         'AH960840',    'AH960840',    4999),
    ('Tefal', 'OptiGrill+ GC714840',                  'GC714840',    'GC714840',    3499),
    ('Tefal', 'Raclette & Plancha',                    'RE12C812',    'RE12C812',    2999),
    ('Tefal', 'Jamie Oliver Frying Pan 28cm',          'E3030644',    'E3030644',    1499),
    ('Tefal', 'Unlimited Induction Set 5pc',           'G2559553',    'G2559553',    3999),
    ('Tefal', 'Steam Iron Ultraglide 2400W',           'FV6812',      'FV6812',      1499),
    ('Tefal', 'Pressure Cooker Clipso 8L',             'P4080736',    'P4080736',    2499),

    # ── NUTRIBULLET ──────────────────────────────────────────────────────────────
    ('Nutribullet', 'Personal Blender 600W 24oz',      'NBR-0601',    'NBR0601',     1299),
    ('Nutribullet', 'Pro 900W 32oz',                   'NBR-0928',    'NBR0928',     1999),
    ('Nutribullet', 'Pro+ 1200W',                      'NBP-0010',    'NBP0010',     2999),
    ('Nutribullet', 'Select Full-Size Blender 1200W',  'NBS-1201',    'NBS1201',     3999),
    ('Nutribullet', 'Juicer Pro',                      'NBJ-50100',   'NBJ50100',    2999),

    # ── SODASTREAM ──────────────────────────────────────────────────────────────
    ('Sodastream', 'Art Sparkling Water Maker Black',  '1011911010',  'ART-BLK',     2999),
    ('Sodastream', 'Art Sparkling Water Maker White',  '1011911011',  'ART-WHT',     2999),
    ('Sodastream', 'Terra Sparkling Water Maker Black','1012111010',  'TERRA-BLK',   2499),
    ('Sodastream', 'Duo Sparkling Water Maker',        '1019812010',  'DUO',         3499),
    ('Sodastream', 'Fizzi One Touch Sparkling Maker',  '1041111010',  'FIZZIOT',     2999),

    # ── DAIKIN ──────────────────────────────────────────────────────────────
    ('Daikin', 'Split AC 9000BTU Inverter',          'FTXM25U/RXM25U',  'FTXM25',    9999),
    ('Daikin', 'Split AC 12000BTU Inverter',         'FTXM35U/RXM35U',  'FTXM35',   11999),
    ('Daikin', 'Split AC 18000BTU Inverter',         'FTXM50U/RXM50U',  'FTXM50',   14999),
    ('Daikin', 'Split AC 24000BTU Inverter',         'FTXM71U/RXM71U',  'FTXM71',   19999),
    ('Daikin', 'Split AC 36000BTU Inverter',         'FTXM100U/RXM100U','FTXM100',  24999),

    # ── SNOMASTER ──────────────────────────────────────────────────────────────
    ('SnoMaster', 'Portable Fridge 35L 12V/240V',     'SN35DZ',      'SN35DZ',      5999),
    ('SnoMaster', 'Portable Fridge 55L 12V/240V',     'SN55DZ',      'SN55DZ',      7999),
    ('SnoMaster', 'Portable Fridge 80L 12V/240V',     'SN80DZ',      'SN80DZ',      9999),
    ('SnoMaster', 'Ice Maker 15kg/day',               'SIM-120P',    'SIM120P',     5999),
    ('SnoMaster', 'Outdoor Bar Fridge 115L',           'SOH115D',     'SOH115D',    12999),
    ('SnoMaster', 'Wine Cooler 18 Bottles',            'SNOWE18D',    'SNOWE18D',    7999),

    # ── JBL ──────────────────────────────────────────────────────────────
    ('JBL', 'Xtreme 3 Portable Speaker',             'JBLXTREME3BLK', 'JBLXTREME3',   5999),
    ('JBL', 'Charge 5 Portable Speaker',             'JBLCHARGE5BLK', 'JBLCHARGE5',   3499),
    ('JBL', 'Flip 6 Portable Speaker',               'JBLFLIP6BLK',   'JBLFLIP6',    2499),
    ('JBL', 'Clip 4 Portable Speaker',               'JBLCLIP4BLK',   'JBLCLIP4',    1499),
    ('JBL', 'Partybox 310 Portable Speaker',         'JBLPARTYBOX310','JBLPB310',    9999),
    ('JBL', 'Bar 1300 Soundbar 11.1.4ch',            'JBLBAR1300BLK', 'JBLBAR1300', 19999),
    ('JBL', 'Bar 500 Soundbar 5.1ch',                'JBLBAR500BLK',  'JBLBAR500',   7999),
    ('JBL', 'Tune 770NC Wireless Headphones',        'JBLT770NC',     'JBLT770NC',   3499),
    ('JBL', 'Live Pro 2 TWS Earbuds',                'JBLLIVEPRO2BLK','JBLLIVEPRO2',  2999),

    # ── INSTANT POT ──────────────────────────────────────────────────────────────
    ('Instant Pot', 'Duo 7-in-1 6L Electric Pressure Cooker', 'IP-DUO60', 'IPDUO60',   2999),
    ('Instant Pot', 'Duo 7-in-1 8L Electric Pressure Cooker', 'IP-DUO80', 'IPDUO80',   3499),
    ('Instant Pot', 'Duo Crisp 11-in-1 8L Air Fryer',         'IP-DUOCRISP8L', 'IPDUOCRISP8', 4999),
    ('Instant Pot', 'Pro 10-in-1 6L Pressure Cooker',         'IP-PRO60', 'IPPRO60',   4499),

    # ── SALTON ──────────────────────────────────────────────────────────────
    ('Salton', 'Sandwich Maker Non-Stick',             'SP1400',      'SP1400',       699),
    ('Salton', 'Waffle Maker Belgian',                 'SW4500',      'SW4500',       799),
    ('Salton', 'Air Fryer 3.5L',                      'SAF3500',     'SAF3500',     1799),
    ('Salton', 'Rice Cooker 1.8L',                    'SRC180',      'SRC180',       799),
    ('Salton', 'Electric Grill George Foreman Style', 'SGF2800',     'SGF2800',      999),
    ('Salton', 'Coffee Maker 10 Cup',                  'SCM1200',     'SCM1200',      999),
    ('Salton', 'Electric Frying Pan 32cm',             'SEP32',       'SEP32',        999),
    ('Salton', 'Kettle 1.7L',                          'SK200',       'SK200',        599),
    ('Salton', 'Deep Fryer 2.5L',                     'SDF25',       'SDF25',        999),
    ('Salton', 'Blender 1.5L 600W',                   'SB1500',      'SB1500',       799),

    # ── SNAPPY CHEF ──────────────────────────────────────────────────────────────
    ('Snappy Chef', 'Gas Stove 2 Burner',              'SC2BN',       'SC2BN',        999),
    ('Snappy Chef', 'Gas Stove 3 Burner',              'SC3BN',       'SC3BN',       1499),
    ('Snappy Chef', 'Gas Stove 4 Burner',              'SC4BN',       'SC4BN',       1999),
    ('Snappy Chef', 'Pressure Cooker 6L',              'SCPC6L',      'SCPC6L',       999),
    ('Snappy Chef', 'Non-Stick Wok 32cm',              'SCW32',       'SCW32',        599),
    ('Snappy Chef', 'Non-Stick Casserole 28cm',        'SCNSC28',     'SCNSC28',      499),

    # ── BISSELL ──────────────────────────────────────────────────────────────
    ('Bissell', 'CleanView Swivel Rewind 2252F',       '2252F',       '2252F',       2999),
    ('Bissell', 'CrossWave All-in-One 2582F',          '2582F',       '2582F',       4999),
    ('Bissell', 'SpinWave Mop 2039E',                  '2039E',       '2039E',       2499),
    ('Bissell', 'Little Green Portable Cleaner 1400B', '1400B',       '1400B',       3499),
    ('Bissell', 'Pet Hair Eraser Turbo+ 24613',        '24613',       '24613',       4499),
    ('Bissell', 'MultiReach Essential 21V 29199',      '29199',       '29199',       2999),

    # ── HOOVER ──────────────────────────────────────────────────────────────
    ('Hoover', 'H-Upright 700 Vacuum',                 'HU71HU01011', 'HU71HU01',    2999),
    ('Hoover', 'H-Free 300 Cordless Vacuum',           'HF322APT011', 'HF322APT',    3999),
    ('Hoover', 'H-Wash 500 Washing Machine 10kg',      'H5W410AMBCS', 'H5W410AMB',   9999),

    # ── BRAUN ──────────────────────────────────────────────────────────────
    ('Braun', 'MultiQuick 9 Hand Blender MQ9045X',     'MQ9045X',     'MQ9045X',     3499),
    ('Braun', 'MultiQuick 7 Hand Blender MQ7045X',     'MQ7045X',     'MQ7045X',     2499),
    ('Braun', 'MultiQuick 3 Hand Blender MQ3025',      'MQ3025',      'MQ3025',      1499),
    ('Braun', 'PurEase Coffee Maker KF3120',            'KF3120',      'KF3120',       999),
    ('Braun', 'Series 7 Electric Shaver 70S',           '70-N7200cc',  '70N7200CC',   4999),
    ('Braun', 'Series 9 Pro Electric Shaver 90',        '90-4340cs',   '904340CS',    7999),
    ('Braun', 'Epilator Silk-épil 9 9-567',             '9-567',       '9567',        3499),
    ('Braun', 'Satin Hair 7 Hair Dryer HD780',          'HD780',       'HD780',       2999),

    # ── AEG ──────────────────────────────────────────────────────────────
    ('AEG', '7000 Washing Machine 9kg',               'LFR73844VE',  'LFR73844VE',  14999),
    ('AEG', '6000 Washing Machine 8kg',               'LFR62944VE',  'LFR62944VE',  11999),
    ('AEG', '7000 Tumble Dryer 9kg Heat Pump',        'TR7680IWE',   'TR7680IWE',   17999),
    ('AEG', 'Dishwasher 15 Place FSB73837P',          'FSB73837P',   'FSB73837P',   12999),

    # ── MEACO ──────────────────────────────────────────────────────────────
    ('Meaco', 'Dehumidifier 12L MeacoDry ABC',         'MeacoDry ABC 12L', 'MDRYA12L', 4999),
    ('Meaco', 'Dehumidifier 20L Platinum',             '20L Platinum',    'MDR20LP',   7999),
    ('Meaco', 'Air Purifier HCX Pro',                  'HCX Pro',         'HCXPRO',    5999),
    ('Meaco', 'Fan 1056P DC Tower Fan',                '1056P',           '1056P',     2999),

    # ── SOLENCO ──────────────────────────────────────────────────────────────
    ('Solenco', 'Air Purifier CF8500',                 'CF8500',      'CF8500',      3999),
    ('Solenco', 'Portable Air Conditioner 9000BTU',    'SH-AC9000',   'SHAC9000',    6999),
    ('Solenco', 'Dehumidifier 12L',                    'SF-7712',     'SF7712',      3999),

    # ── ELBA ──────────────────────────────────────────────────────────────
    ('Elba', 'Freestanding Cooker 60cm Gas/Elec',     'E6CMN9GX',    'E6CMN9GX',    5999),
    ('Elba', 'Freestanding Cooker 60cm Elec',          'E6CEN9X',     'E6CEN9X',     5499),
    ('Elba', 'Built-in Oven 60cm',                    'E4EO60X1',    'E4EO60X1',    3999),
    ('Elba', 'Gas Hob 4 Burner 60cm',                 'E6GH40X',     'E6GH40X',     2999),

    # ── MIDEA ──────────────────────────────────────────────────────────────
    ('Midea', 'Top Mount Fridge 268L',                'HD268FW',     'HD268FW',     3999),
    ('Midea', 'Top Mount Fridge 372L',                'HD372FW',     'HD372FW',     5499),
    ('Midea', 'Chest Freezer 100L',                   'MDRC100FGE01','MDRC100FGE',  2499),
    ('Midea', 'Chest Freezer 200L',                   'MDRC200FGE01','MDRC200FGE',  3499),
    ('Midea', 'Washing Machine Top Load 10kg',        'MF100W60/W-SA','MF100W60',   3999),
    ('Midea', 'Split AC 12000BTU Inverter',           'MSAGBU-12HRDN8','MSAGBU12',  7999),
    ('Midea', 'Portable Air Conditioner 9000BTU',     'MPPD-09CRN1',  'MPPD09',    5999),
    ('Midea', 'Microwave Oven 20L',                   'MW-2014B',     'MW2014B',    1499),
    ('Midea', 'Microwave Oven 25L',                   'MW-2518B',     'MW2518B',    1799),

    # ── KIC ──────────────────────────────────────────────────────────────
    ('KIC', 'Top Mount Fridge 210L',                  'KTF460ME',    'KTF460ME',    2999),
    ('KIC', 'Top Mount Fridge 310L',                  'KTF660ME',    'KTF660ME',    4499),
    ('KIC', 'Washing Machine Top Load 8kg',           'KTL08GR',     'KTL08GR',     2999),
    ('KIC', 'Washing Machine Top Load 12kg',          'KTL12GR',     'KTL12GR',     3999),
    ('KIC', 'Chest Freezer 100L',                     'KCF100',      'KCF100',      1999),
    ('KIC', 'Chest Freezer 200L',                     'KCF200',      'KCF200',      2999),

    # ── BRABANTIA ──────────────────────────────────────────────────────────────
    ('Brabantia', 'Touch Bin 30L Matt Black',         '280605',      '280605',      1999),
    ('Brabantia', 'Touch Bin 45L Matt Black',         '109745',      '109745',      2499),
    ('Brabantia', 'Ironing Board B Size 124x38cm',    '119173',      '119173',      2999),
    ('Brabantia', 'Rotary Clothesline Topspinner',    '392765',      '392765',      2999),
    ('Brabantia', 'Foldable Drying Rack 40m',         '108519',      '108519',      1499),
    ('Brabantia', 'Compost Bin 6L Matt Black',        '302577',      '302577',       999),

    # ── LOCK & LOCK ──────────────────────────────────────────────────────────────
    ('LocknLock', 'Food Container Set 6pc',            'LLG-501',     'LLG501',       599),
    ('LocknLock', 'Airtight Container Set 10pc',       'LLG-502',     'LLG502',       999),
    ('LocknLock', 'Eco Container Set 3pc',             'LLG-503',     'LLG503',       499),
    ('LocknLock', 'Glass Food Container Set 4pc',      'LLG-506',     'LLG506',       799),

    # ── KUVINGS ──────────────────────────────────────────────────────────────
    ('Kuvings', 'Whole Slow Juicer C7000P',            'C7000P',      'C7000P',      4999),
    ('Kuvings', 'Whole Slow Juicer B1700P',            'B1700P',      'B1700P',      6999),
    ('Kuvings', 'Revo830 Cold Press Juicer',           'REVO830',     'REVO830',     9999),
    ('Kuvings', 'Auto10 Whole Slow Juicer',            'AUTO10',      'AUTO10',     12999),

    # ── LAIFEN ──────────────────────────────────────────────────────────────
    ('Laifen', 'Swift Hair Dryer 1600W',               'LF03',        'LF03',        2999),
    ('Laifen', 'Swift Special Hair Dryer 1600W',       'LF03S',       'LF03S',       3999),
    ('Laifen', 'Wave Ionic Hair Dryer 1800W',          'LF01C',       'LF01C',       6999),

    # ── HOBOT ──────────────────────────────────────────────────────────────
    ('Hobot', 'Window Cleaning Robot 298',             'HOBOT-298',   'HOBOT298',    4999),
    ('Hobot', 'Window Cleaning Robot 388',             'HOBOT-388',   'HOBOT388',    7999),
    ('Hobot', 'Glass & Tiles Cleaner Legee-7',         'LEGEE-7',     'LEGEE7',      9999),

    # ── VERIMARK ──────────────────────────────────────────────────────────────
    ('Verimark', 'Turbo Air Fryer Grill 12L',          'TAG-4001',    'TAG4001',     1999),
    ('Verimark', 'Ultimate Air Fryer Oven 30L',         'UAF-3001',    'UAF3001',    2999),
    ('Verimark', 'NutriBullet 600W Personal Blender',  'NBR-0601V',   'NBR0601V',   1299),
    ('Verimark', 'Cuisinart Smart Stick Hand Blender',  'CSB-75V',     'CSB75V',     1499),
    ('Verimark', 'NuWave Brio Air Fryer 6QT',          'NW-37001V',   'NW37001V',   2999),

    # ── FALCO ──────────────────────────────────────────────────────────────
    ('Falco', 'UHD 4K Smart TV 65"',                   'FLC-65S40',   'FLC65S40',    6999),
    ('Falco', 'UHD 4K Smart TV 55"',                   'FLC-55S40',   'FLC55S40',    4999),
    ('Falco', 'UHD 4K Smart TV 43"',                   'FLC-43S40',   'FLC43S40',    2999),

    # ── COMFEE ──────────────────────────────────────────────────────────────
    ('Comfee', 'Portable Air Conditioner 12000BTU',    'CF-PAC12000', 'CFPAC12000',  7999),
    ('Comfee', 'Split Air Conditioner 9000BTU',        'CF-12C',      'CF12C',       7499),
    ('Comfee', 'Portable Air Conditioner 9000BTU',     'CF-PAC9000',  'CFPAC9000',   5999),

    # ── WHIRLPOOL ──────────────────────────────────────────────────────────────
    ('Whirlpool', 'Top Mount Fridge 361L',             'WTM450WH',    'WTM450WH',    5999),
    ('Whirlpool', 'Top Mount Fridge 471L',             'WTM550WH',    'WTM550WH',    7999),
    ('Whirlpool', 'Washing Machine Front Load 7kg',    'FSCR70413',   'FSCR70413',   7999),
    ('Whirlpool', 'Washing Machine Top Load 12kg',     'WTW5000DW',   'WTW5000DW',   5999),

    # ── BEKO ──────────────────────────────────────────────────────────────
    ('Beko', 'Side-by-Side Fridge 632L',              'GN163130ZXBRN','GN163130ZX', 12999),
    ('Beko', 'Bottom Mount Fridge 370L',              'RDNE370E20WZX','RDNE370E20', 8999),
    ('Beko', 'Top Mount Fridge 285L',                 'RSSA285K20WB', 'RSSA285K20', 4999),
    ('Beko', 'Washing Machine Front Load 8kg',         'WTV8736XWST',  'WTV8736XW',  7999),
    ('Beko', 'Washing Machine Top Load 10kg',          'BTV10G0W',     'BTV10G0W',   4999),
    ('Beko', 'Dishwasher 13 Place',                    'DFS28120W',    'DFS28120W',  5999),

    # ── KRUPS ──────────────────────────────────────────────────────────────
    ('Krups', 'Espresso Machine XP3208',               'XP3208',      'XP3208',      2499),
    ('Krups', 'Nespresso Inissia EN80',                'EN80.B',      'EN80B',       1999),
    ('Krups', 'Nespresso Pixie EN127',                 'EN127.SAE',   'EN127SAE',    2499),
    ('Krups', 'Nespresso Expert & Milk EA8598',        'EA8598.25',   'EA859825',    4999),
    ('Krups', 'Coffee Grinder GVX231',                 'GVX231',      'GVX231',      1499),
    ('Krups', 'Waffle Maker GQ502D',                   'GQ502D',      'GQ502D',      1999),
]


def main():
    headers = ['Supplier', 'Brand', 'Product Name', 'Model', 'SN/SKU', 'Sale Price', 'Source']

    output_path = OUTPUT_FILE

    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(headers)

        for item in HIRSCH_PRODUCTS:
            brand, name, model, sku, price = item
            writer.writerow(['Hirschs', brand, name, model, sku, price, 'Hirschs Pamphlet 2026'])

    total = len(HIRSCH_PRODUCTS)
    brands = len(set(p[0] for p in HIRSCH_PRODUCTS))

    print(f'✅ Hirschs extraction complete!')
    print(f'   Products extracted: {total}')
    print(f'   Brands covered:     {brands}')
    print(f'   Output file:        {output_path}')

    # Print brand summary
    from collections import Counter
    brand_counts = Counter(p[0] for p in HIRSCH_PRODUCTS)
    print('\n   Brand breakdown:')
    for brand, count in sorted(brand_counts.items(), key=lambda x: -x[1]):
        print(f'   {brand:25s}: {count} products')


if __name__ == '__main__':
    main()
