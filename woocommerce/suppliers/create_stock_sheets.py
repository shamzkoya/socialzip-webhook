#!/usr/bin/env python3
"""
============================================================
Create Standardised Supplier Stock Sheets
============================================================
Generates a uniform .xlsx stock sheet for every supplier.
Sheets that already have data are pre-populated from:
  - Existing stock CSVs
  - WooCommerce product export (matched products)

All sheets follow the SAME column structure so they can be
updated consistently and fed into the sync pipeline.

USAGE:
  python3 create_stock_sheets.py \
    --wc-export /path/to/wc_export.csv \
    --impulse-stock /path/to/impulse_products.csv \
    --out /path/to/suppliers/

OUTPUT:
  suppliers/<name>/stock_sheet.xlsx  — one per supplier
============================================================
"""

import csv, json, re, sys, argparse
from pathlib import Path
from datetime import date

try:
    import openpyxl
    from openpyxl.styles import (PatternFill, Font, Alignment, Border, Side,
                                  GradientFill)
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation
except ImportError:
    print("pip3 install openpyxl"); sys.exit(1)

# ── Colour palette ─────────────────────────────────────────────────────────
COL_HEADER_BG  = "1F3864"   # dark navy
COL_HEADER_FG  = "FFFFFF"
COL_SECTION_BG = "D6E4F0"   # light blue section row
COL_ALT_BG     = "F5F9FF"   # alternating row tint
COL_FORMULA_BG = "FFF2CC"   # yellow — formula / auto-calc cells
COL_REQUIRED   = "FCE4D6"   # orange — must-fill cells
COL_BORDER     = "BDD7EE"

# ── Standard columns ───────────────────────────────────────────────────────
# (header, width, style_hint, notes_for_user)
COLUMNS = [
    # Identification
    ("Supplier SKU",        18, "required",  "Supplier's own product code"),
    ("WC SKU",              18, "normal",    "WooCommerce SKU (auto-matched)"),
    ("WC ID",               10, "normal",    "WooCommerce product ID"),
    ("Barcode / EAN",       18, "normal",    "EAN-13, UPC or internal barcode"),
    # Product info
    ("Product Name",        40, "required",  "Full product name as it appears on the store"),
    ("Brand",               15, "normal",    "Brand / manufacturer"),
    ("Category",            25, "normal",    "e.g. Homeware > Kitchenware"),
    ("Description",         50, "normal",    "Short product description"),
    # Pricing
    ("Cost Price (ex VAT)", 18, "required",  "What you pay the supplier, before VAT (R)"),
    ("VAT Rate %",          12, "formula",   "0 or 15 (auto-applied below)"),
    ("Cost Price (incl VAT)",18,"formula",   "=Cost*(1+VAT/100) — auto-calculated"),
    ("Selling Price (R)",   18, "required",  "Your retail price including VAT"),
    ("Margin %",            12, "formula",   "=(Selling-Cost_incl)/Selling*100 — auto"),
    # Stock
    ("Qty On Hand",         14, "required",  "Current physical stock count"),
    ("Reorder Level",       14, "normal",    "Alert when stock falls below this number"),
    ("Location / Bay",      18, "normal",    "Shelf, bay or bin reference in store"),
    # Media
    ("Image URL",           45, "normal",    "Direct URL or Drive link to product image"),
    # Admin
    ("Last Updated",        15, "normal",    "Date this row was last updated"),
    ("Notes",               35, "normal",    "Any supplier notes, variants, issues"),
]

COL_IDX = {c[0]: i+1 for i, c in enumerate(COLUMNS)}   # 1-based column index
N_COLS  = len(COLUMNS)

# Columns that have auto-calc formulas (yellow)
FORMULA_COLS = {"Cost Price (incl VAT)", "Margin %", "VAT Rate %"}
REQUIRED_COLS= {"Supplier SKU", "Product Name", "Cost Price (ex VAT)",
                "Selling Price (R)", "Qty On Hand"}


def style_header(ws):
    """Write and style the column header row (row 1)."""
    hdr_fill = PatternFill("solid", fgColor=COL_HEADER_BG)
    hdr_font = Font(bold=True, color=COL_HEADER_FG, size=10)
    thin     = Side(style="thin", color=COL_BORDER)
    border   = Border(left=thin, right=thin, bottom=thin)

    for col_num, (header, width, hint, _) in enumerate(COLUMNS, 1):
        cell = ws.cell(row=1, column=col_num, value=header)
        cell.fill  = hdr_fill
        cell.font  = hdr_font
        cell.alignment = Alignment(horizontal="center", vertical="center",
                                   wrap_text=True)
        cell.border = border
        ws.column_dimensions[get_column_letter(col_num)].width = width

    ws.row_dimensions[1].height = 32
    ws.freeze_panes = "A2"


def style_row(ws, row_num, is_alt=False):
    """Apply alternating row style and formula cells."""
    thin = Side(style="thin", color=COL_BORDER)
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    for col_num, (header, _, hint, _) in enumerate(COLUMNS, 1):
        cell = ws.cell(row=row_num, column=col_num)
        if hint == "formula" or header in FORMULA_COLS:
            cell.fill = PatternFill("solid", fgColor=COL_FORMULA_BG)
        elif hint == "required" or header in REQUIRED_COLS:
            cell.fill = PatternFill("solid", fgColor=COL_REQUIRED if not cell.value else
                                    (COL_ALT_BG if is_alt else "FFFFFF"))
        elif is_alt:
            cell.fill = PatternFill("solid", fgColor=COL_ALT_BG)
        cell.border = border
        cell.alignment = Alignment(vertical="center", wrap_text=False)


def write_formulas(ws, row_num):
    """Insert auto-calc formulas for cost incl VAT and margin."""
    cost_col   = get_column_letter(COL_IDX["Cost Price (ex VAT)"])
    vat_col    = get_column_letter(COL_IDX["VAT Rate %"])
    sell_col   = get_column_letter(COL_IDX["Selling Price (R)"])
    incl_col   = get_column_letter(COL_IDX["Cost Price (incl VAT)"])
    margin_col = get_column_letter(COL_IDX["Margin %"])

    # Default VAT = 15 if not set
    ws[f"{vat_col}{row_num}"] = 15

    # Cost incl VAT
    ws[f"{incl_col}{row_num}"] = (
        f'=IF({cost_col}{row_num}="","",{cost_col}{row_num}*(1+{vat_col}{row_num}/100))'
    )
    ws[f"{incl_col}{row_num}"].number_format = 'R#,##0.00'

    # Margin %
    ws[f"{margin_col}{row_num}"] = (
        f'=IF(OR({sell_col}{row_num}="",{incl_col}{row_num}="",{sell_col}{row_num}=0),"",\n'
        f'  ({sell_col}{row_num}-{incl_col}{row_num})/{sell_col}{row_num}*100)'
    )
    ws[f"{margin_col}{row_num}"].number_format = '0.0"%"'


def add_notes_sheet(wb):
    """Add a Notes/Legend sheet explaining the colour coding."""
    ns = wb.create_sheet("LEGEND")
    ns.column_dimensions["A"].width = 25
    ns.column_dimensions["B"].width = 55

    entries = [
        ("COLOUR KEY", ""),
        ("Orange background", "Required field — must be filled in"),
        ("Yellow background", "Auto-calculated — do not edit manually"),
        ("Blue tint rows",    "Alternating row shading for readability"),
        ("",                  ""),
        ("COLUMN GUIDE", ""),
        *[(h, note) for h, _, _, note in COLUMNS],
    ]
    for r, (label, val) in enumerate(entries, 1):
        a = ns.cell(row=r, column=1, value=label)
        b = ns.cell(row=r, column=2, value=val)
        if label in ("COLOUR KEY", "COLUMN GUIDE"):
            a.font = Font(bold=True, size=11)
        a.alignment = Alignment(vertical="top")
        b.alignment = Alignment(vertical="top", wrap_text=True)
        ns.row_dimensions[r].height = 18


def create_sheet(supplier_id: str, display_name: str, products: list,
                 out_dir: Path):
    """Create a styled .xlsx stock sheet for one supplier."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Stock"

    # ── Title row ──────────────────────────────────────────────────────────
    ws.insert_rows(1)
    ws.merge_cells(f"A1:{get_column_letter(N_COLS)}1")
    title_cell = ws["A1"]
    title_cell.value = f"{display_name} — Stock Sheet   (updated: {date.today()})"
    title_cell.font  = Font(bold=True, size=13, color="FFFFFF")
    title_cell.fill  = PatternFill("solid", fgColor="0D2137")
    title_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 28

    # ── Header row ─────────────────────────────────────────────────────────
    # Shift down and style
    ws.insert_rows(2)
    for col_num, (header, width, hint, _) in enumerate(COLUMNS, 1):
        cell = ws.cell(row=2, column=col_num, value=header)
        cell.fill  = PatternFill("solid", fgColor=COL_HEADER_BG)
        cell.font  = Font(bold=True, color=COL_HEADER_FG, size=10)
        cell.alignment = Alignment(horizontal="center", vertical="center",
                                   wrap_text=True)
        ws.column_dimensions[get_column_letter(col_num)].width = width
    ws.row_dimensions[2].height = 30
    ws.freeze_panes = "A3"

    # ── Data rows ──────────────────────────────────────────────────────────
    for i, product in enumerate(products):
        row_num = i + 3
        is_alt  = (i % 2 == 1)

        # Write values
        ws.cell(row=row_num, column=COL_IDX["Supplier SKU"],        value=product.get("sku") or "")
        ws.cell(row=row_num, column=COL_IDX["WC SKU"],              value=product.get("wc_sku") or "")
        ws.cell(row=row_num, column=COL_IDX["WC ID"],               value=product.get("wc_id") or "")
        ws.cell(row=row_num, column=COL_IDX["Barcode / EAN"],       value=product.get("barcode") or "")
        ws.cell(row=row_num, column=COL_IDX["Product Name"],        value=product.get("name") or "")
        ws.cell(row=row_num, column=COL_IDX["Brand"],               value=product.get("brand") or "")
        ws.cell(row=row_num, column=COL_IDX["Category"],            value=product.get("category") or "")
        ws.cell(row=row_num, column=COL_IDX["Description"],         value=product.get("description") or "")
        ws.cell(row=row_num, column=COL_IDX["Cost Price (ex VAT)"], value=product.get("cost") or "")
        ws.cell(row=row_num, column=COL_IDX["Selling Price (R)"],   value=product.get("selling_price") or "")
        ws.cell(row=row_num, column=COL_IDX["Qty On Hand"],         value=product.get("qty") or "")
        ws.cell(row=row_num, column=COL_IDX["Reorder Level"],       value=product.get("reorder") or "")
        ws.cell(row=row_num, column=COL_IDX["Location / Bay"],      value=product.get("location") or "")
        ws.cell(row=row_num, column=COL_IDX["Image URL"],           value=product.get("image") or "")
        ws.cell(row=row_num, column=COL_IDX["Last Updated"],        value=product.get("last_updated") or str(date.today()))
        ws.cell(row=row_num, column=COL_IDX["Notes"],               value=product.get("notes") or "")

        # Formulas
        write_formulas(ws, row_num)

        # Styling
        style_row(ws, row_num, is_alt)

        # Number formats
        for col_name in ("Cost Price (ex VAT)", "Selling Price (R)"):
            ws.cell(row=row_num, column=COL_IDX[col_name]).number_format = 'R#,##0.00'
        ws.cell(row=row_num, column=COL_IDX["Qty On Hand"]).number_format = '#,##0'

    # ── Add 50 empty rows for future stock ─────────────────────────────────
    start_empty = len(products) + 3
    for i in range(50):
        row_num = start_empty + i
        write_formulas(ws, row_num)
        style_row(ws, row_num, i % 2 == 1)

    # ── Print / filter settings ────────────────────────────────────────────
    ws.auto_filter.ref = f"A2:{get_column_letter(N_COLS)}2"
    ws.sheet_view.showGridLines = False

    add_notes_sheet(wb)

    out_path = out_dir / supplier_id / "stock_sheet.xlsx"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    print(f"  ✓ {supplier_id:25s} {len(products):4d} products → {out_path}")
    return out_path


# ── Data loaders ──────────────────────────────────────────────────────────
def load_wc(path):
    """Load WC export, return dict keyed by SKU (upper)."""
    by_sku = {}
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            sku = r.get("SKU","").strip().upper()
            if sku:
                by_sku[sku] = r
    return by_sku


def load_csv_simple(path, name_col, sku_col=None, price_col=None, qty_col=None):
    rows = []
    try:
        with open(path, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                rows.append({
                    "sku":   (r.get(sku_col) or "").strip() if sku_col else "",
                    "name":  (r.get(name_col) or "").strip(),
                    "cost":  _num(r.get(price_col)) if price_col else "",
                    "qty":   _num(r.get(qty_col))   if qty_col   else "",
                })
    except Exception as e:
        print(f"    [warn] Could not load {path}: {e}")
    return rows


def _num(v):
    if v is None: return ""
    try: return float(str(v).replace("R","").replace(",","").strip())
    except: return ""


def wc_matched(wc_by_sku, supplier_rows, sku_key="sku"):
    """Add WC IDs/SKUs to supplier rows where a match is found."""
    for r in supplier_rows:
        sku = str(r.get(sku_key) or "").upper()
        wc  = wc_by_sku.get(sku)
        if wc:
            r["wc_id"]  = wc.get("ID","")
            r["wc_sku"] = wc.get("SKU","")
            if not r.get("selling_price"):
                r["selling_price"] = _num(wc.get("Regular price"))


def wc_products_for_supplier(wc_by_sku, classifier_fn):
    """Pull WC products that match a supplier, as pre-populated rows."""
    rows = []
    for sku, r in wc_by_sku.items():
        name = r.get("Name","")
        cats = r.get("Categories","")
        desc = r.get("Description","")
        if classifier_fn(sku, name, cats, desc):
            rows.append({
                "sku":          "",
                "wc_sku":       sku,
                "wc_id":        r.get("ID",""),
                "name":         name,
                "category":     cats.split(",")[0].strip() if cats else "",
                "selling_price": _num(r.get("Regular price")),
                "qty":          _num(r.get("Stock")),
                "image":        r.get("Images",""),
                "notes":        "Pre-loaded from WC export — fill in cost price",
            })
    return rows


# ── Supplier classifiers (same as stock_audit.py) ────────────────────────
HIRSCH_BRANDS = {'lg','samsung','hisense','defy','bosch','smeg','miele','kic',
                 'siemens','electrolux','beko','delonghi','tefal','kenwood',
                 'russell hobbs','cloud nine','fisher & paykel','whirlpool'}
HIRSCH_CATS   = {'appliances','fridges','stoves','washing','dishwasher',
                 'microwave','air conditioner','aircon','freezer','tv','audio'}

def is_tupperware(sku, name, cats, desc):
    return 'tupperware' in name.lower() or 'tupperware' in cats.lower()

def is_jewellery(sku, name, cats, desc):
    kw = ['ring','necklace','bracelet','earring','pendant','chain','bangle','jewel']
    return any(k in name.lower() for k in kw) or 'jewel' in cats.lower()

def is_makokoya(sku, name, cats, desc):
    kw = ['carpet','rug','mat','curtain','blind','runner']
    return any(k in name.lower() for k in kw) or any(k in cats.lower() for k in kw)

def is_power_warehouse(sku, name, cats, desc):
    kw = ['ups','inverter','solar','battery','lithium','mecer','victron',
          'pylontech','generator','load shed','loadshed']
    return any(k in name.lower() for k in kw) or 'loadshedding' in cats.lower()

def is_hirsch(sku, name, cats, desc):
    name_l = name.lower(); cats_l = cats.lower()
    return (any(b in name_l for b in HIRSCH_BRANDS) and
            any(c in cats_l for c in HIRSCH_CATS))


# ── Main ──────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wc-export",     required=True)
    parser.add_argument("--impulse-stock", default="")
    parser.add_argument("--out",           default="woocommerce/suppliers")
    args = parser.parse_args()

    out_dir     = Path(args.out)
    wc_by_sku   = load_wc(args.wc_export)
    today       = str(date.today())

    # ── Impulse stock codes ──────────────────────────────────────────────
    impulse_codes = set()
    impulse_rows  = []
    if args.impulse_stock and Path(args.impulse_stock).exists():
        with open(args.impulse_stock, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                code = r.get("supplier_code","").strip().upper()
                if code:
                    impulse_codes.add(code)
                    wc = wc_by_sku.get(code, {})
                    impulse_rows.append({
                        "sku":          code,
                        "wc_sku":       wc.get("SKU","") if wc else code,
                        "wc_id":        wc.get("ID","")  if wc else "",
                        "barcode":      r.get("barcodes","").split("|")[0],
                        "name":         r.get("description",""),
                        "cost":         _num(r.get("price_ex_vat")),
                        "qty":          _num(r.get("stock_onhand")),
                        "selling_price":_num(wc.get("Regular price","")) if wc else "",
                        "last_updated": today,
                    })

    print("\nGenerating stock sheets...\n")

    # ── 1. IMPULSE ────────────────────────────────────────────────────────
    create_sheet("impulse", "Impulse Imports", impulse_rows, out_dir)

    # ── 2. HIRSCHS ────────────────────────────────────────────────────────
    hirsch_csv = out_dir / "hirschs" / "hirsch_supplier_feb_mar_2026.csv"
    hirsch_wc  = wc_products_for_supplier(wc_by_sku, is_hirsch)
    hirsch_rows= []
    if hirsch_csv.exists():
        with open(hirsch_csv) as f:
            for r in csv.DictReader(f):
                wc_r = None
                model = r.get("Model","").strip().upper()
                if model: wc_r = wc_by_sku.get(model)
                hirsch_rows.append({
                    "sku":      model or r.get("SN/SKU",""),
                    "wc_id":    wc_r.get("ID","") if wc_r else "",
                    "name":     f"{r.get('Brand','')} {r.get('Product Name','')}".strip(),
                    "brand":    r.get("Brand",""),
                    "cost":     _num(r.get("Sale Price")),
                    "selling_price": _num(wc_r.get("Regular price","")) if wc_r else "",
                    "notes":    r.get("Source",""),
                    "last_updated": today,
                })
    if not hirsch_rows:
        hirsch_rows = hirsch_wc
    create_sheet("hirschs", "Hirschs", hirsch_rows, out_dir)

    # ── 3. TUPPERWARE ─────────────────────────────────────────────────────
    tupp_csv  = out_dir / "tupperware" / "stock_current.csv"
    tupp_wc   = {r["name"].lower(): r for r in wc_products_for_supplier(wc_by_sku, is_tupperware)}
    tupp_rows = []
    if tupp_csv.exists():
        with open(tupp_csv) as f:
            for r in csv.DictReader(f):
                name  = r.get("name","").strip()
                wc_r  = tupp_wc.get(name.lower(), {})
                tupp_rows.append({
                    "name":          name,
                    "wc_id":         wc_r.get("wc_id",""),
                    "wc_sku":        wc_r.get("wc_sku",""),
                    "cost":          _num(r.get("price")),
                    "selling_price": wc_r.get("selling_price","") or _num(r.get("price","")) * 1.4 if _num(r.get("price")) else "",
                    "qty":           _num(r.get("qty")),
                    "category":      "Homeware > Kitchenware > Tupperware",
                    "brand":         "Tupperware",
                    "last_updated":  today,
                })
    if not tupp_rows:
        tupp_rows = wc_products_for_supplier(wc_by_sku, is_tupperware)
    create_sheet("tupperware", "Tupperware", tupp_rows, out_dir)

    # ── 4. MAMA BUSI (fashion) ────────────────────────────────────────────
    fashion_csv = out_dir / "mama_busi" / "fashion_stock.csv"
    fashion_rows= []
    if fashion_csv.exists():
        with open(fashion_csv) as f:
            for r in csv.DictReader(f):
                fashion_rows.append({
                    "name":         r.get("name",""),
                    "cost":         _num(r.get("price")),
                    "selling_price":_num(r.get("price","")) * 1.4 if _num(r.get("price")) else "",
                    "qty":          _num(r.get("qty")),
                    "category":     "Lifestyle & Everyday Essentials > Fashion",
                    "last_updated": today,
                    "notes":        "Model code in name — update SKU when received from supplier",
                })
    create_sheet("mama_busi", "Mama Busi (Fashion)", fashion_rows, out_dir)

    # ── 5. MAKOKOYA ───────────────────────────────────────────────────────
    makokoya_rows = wc_products_for_supplier(wc_by_sku, is_makokoya)
    # Sort by category then name
    makokoya_rows.sort(key=lambda r: (r.get("category",""), r.get("name","")))
    create_sheet("makokoya", "Makokoya", makokoya_rows, out_dir)

    # ── 6. POWER WAREHOUSE ────────────────────────────────────────────────
    pw_rows = wc_products_for_supplier(wc_by_sku, is_power_warehouse)
    # Also pull from their XLS if available
    pw_xlsx = out_dir / "power_warehouse" / "backup_list.xlsx"
    if pw_xlsx.exists():
        try:
            import openpyxl as xl
            wb = xl.load_workbook(str(pw_xlsx), read_only=True, data_only=True)
            ws2 = wb.active
            seen = {r["name"].lower() for r in pw_rows}
            for i, row in enumerate(ws2.iter_rows(values_only=True)):
                if i == 0: continue
                sku  = str(row[0]).strip() if row[0] else ""
                name = str(row[1]).strip() if len(row) > 1 and row[1] else ""
                cost = _num(row[2]) if len(row) > 2 else ""
                sell = _num(row[4]) if len(row) > 4 else ""
                if name and name.lower() not in seen:
                    seen.add(name.lower())
                    wc = wc_by_sku.get(sku.upper(), {})
                    pw_rows.append({
                        "sku":          sku,
                        "wc_id":        wc.get("ID",""),
                        "wc_sku":       wc.get("SKU",""),
                        "name":         name,
                        "cost":         cost,
                        "selling_price":sell or (_num(cost)*1.4 if cost else ""),
                        "category":     "Hardware > Electrical > Loadshedding",
                        "last_updated": today,
                    })
        except Exception as e:
            print(f"    [warn] Power Warehouse XLS: {e}")
    create_sheet("power_warehouse", "Power Warehouse", pw_rows, out_dir)

    # ── 7. SAGREN ─────────────────────────────────────────────────────────
    # No stock data — create empty sheet with WC products tagged to them
    sagren_kw = ['china','wholesale','decor','homeware','general']
    sagren_wc = []
    for sku, r in wc_by_sku.items():
        if sku in impulse_codes: continue
        name = r.get("Name",""); cats = r.get("Categories","")
        if (not is_hirsch(sku,name,cats,"") and not is_tupperware(sku,name,cats,"") and
            not is_jewellery(sku,name,cats,"") and not is_makokoya(sku,name,cats,"") and
            not is_power_warehouse(sku,name,cats,"")):
            if any(k in cats.lower() for k in ['novelty','lifestyle','stationery','fashion','under r99']):
                sagren_wc.append({
                    "wc_sku": sku, "wc_id": r.get("ID",""), "name": name,
                    "selling_price": _num(r.get("Regular price")),
                    "qty": _num(r.get("Stock")),
                    "category": cats.split(",")[0].strip(),
                    "notes": "Assign supplier SKU and cost price",
                    "last_updated": today,
                })
    create_sheet("sagren", "Sagren", sagren_wc[:200], out_dir)  # limit to avoid huge file

    # ── 8. JEWELLERY ──────────────────────────────────────────────────────
    jewel_rows = wc_products_for_supplier(wc_by_sku, is_jewellery)
    create_sheet("jewellery", "Jewellery Supplier", jewel_rows, out_dir)

    # ── 9. GUJJI CELLS ────────────────────────────────────────────────────
    gujji_csv = out_dir / "gujji_cells" / "stock_current.csv"
    gujji_rows= []
    if gujji_csv.exists():
        with open(gujji_csv) as f:
            for r in csv.DictReader(f):
                gujji_rows.append({
                    "sku":          r.get("Item Code",""),
                    "name":         r.get("Item Name",""),
                    "description":  r.get("Description",""),
                    "cost":         _num(r.get("Price")),
                    "selling_price":_num(r.get("Price",""))*1.4 if _num(r.get("Price")) else "",
                    "qty":          _num(r.get("QTY")),
                    "category":     "Mobile Tech & Gadgets",
                    "last_updated": today,
                })
    create_sheet("gujji_cells", "Gujji Cells", gujji_rows, out_dir)

    # ── 10. ARCHIES CELLS ─────────────────────────────────────────────────
    # Empty — phones sold via WhatsApp images only
    archies_rows = []
    for sku, r in wc_by_sku.items():
        if 'mobile' in r.get("Categories","").lower() or 'phone' in r.get("Name","").lower():
            if sku not in impulse_codes and not is_hirsch(sku,r.get("Name",""),r.get("Categories",""),""):
                archies_rows.append({
                    "wc_sku": sku, "wc_id": r.get("ID",""),
                    "name": r.get("Name",""),
                    "selling_price": _num(r.get("Regular price")),
                    "qty": _num(r.get("Stock")),
                    "category": "Mobile Tech & Gadgets > Mobile Phones",
                    "notes": "Check stock — sourced via WhatsApp from Archie",
                    "last_updated": today,
                })
    create_sheet("archies_cells", "Archie's Cell Phones", archies_rows, out_dir)

    # ── 11. CHINA CENTER ──────────────────────────────────────────────────
    create_sheet("china_center", "China Center", [], out_dir)

    # ── 12. FITNASTICS ────────────────────────────────────────────────────
    create_sheet("fitnastics", "Fitnastics", [], out_dir)

    # ── 13. MR D ──────────────────────────────────────────────────────────
    create_sheet("mr_d", "Mr D", [], out_dir)

    # ── 14. TEXPERTS ──────────────────────────────────────────────────────
    create_sheet("texperts", "Texperts", [], out_dir)

    print(f"""
============================================================
ALL STOCK SHEETS CREATED
============================================================
Location: {out_dir}/

Every sheet has the same columns:
  Supplier SKU · WC SKU · WC ID · Barcode
  Product Name · Brand · Category · Description
  Cost Price (ex VAT) · VAT % · Cost Price (incl) [auto]
  Selling Price · Margin % [auto]
  Qty On Hand · Reorder Level · Location/Bay
  Image URL · Last Updated · Notes

Orange = required fields
Yellow = auto-calculated (do not edit)

Sheets pre-populated from:
  - Existing stock CSVs where available
  - WooCommerce product export (matched products)
  - 50 blank rows added for new stock entries
============================================================
""")


if __name__ == "__main__":
    main()
