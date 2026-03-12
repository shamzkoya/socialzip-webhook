#!/usr/bin/env python3
"""
impulse_extract.py — Parse Impulse Imports price list CSV into clean product data.

Usage:
    python3 impulse_extract.py --input /path/to/impulse_pricelist.csv --out /path/to/output/

The input is the Google Sheets export of the Impulse price list.
Outputs:
    impulse_products.csv  — clean product list with code, description, price, stock, barcodes
"""

import csv
import argparse
import os
import re
from pathlib import Path


def parse_impulse_csv(input_path: str) -> list[dict]:
    """Parse the Impulse price list CSV and return list of product dicts."""
    products = []
    skip_codes = {"DESC0", "CODE", ""}

    with open(input_path, encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))

    i = 0
    while i < len(rows):
        row = rows[i]

        # Product rows: col 2 = code, col 5 = description, col 20 = price
        code = row[2].strip() if len(row) > 2 else ""
        desc = row[5].strip() if len(row) > 5 else ""
        price_raw = row[20].strip() if len(row) > 20 else ""
        onhand_raw = row[28].strip() if len(row) > 28 else ""
        available_raw = row[31].strip() if len(row) > 31 else ""

        # Skip non-product rows
        if not code or code in skip_codes:
            i += 1
            continue

        # Skip header/footer/service rows
        if not re.match(r'^[A-Z]\d+', code):
            i += 1
            continue

        # Skip Z-codes (delivery/transport services)
        if code.startswith("Z"):
            i += 1
            continue

        # Parse numeric fields
        try:
            price = float(price_raw) if price_raw else 0.0
        except ValueError:
            price = 0.0

        try:
            onhand = float(onhand_raw) if onhand_raw else 0.0
        except ValueError:
            onhand = 0.0

        try:
            available = float(available_raw) if available_raw else 0.0
        except ValueError:
            available = 0.0

        # Check next row for barcodes (col 4 and col 8)
        barcodes = []
        if i + 1 < len(rows):
            next_row = rows[i + 1]
            next_code = next_row[2].strip() if len(next_row) > 2 else ""
            # Next row is a barcode row if col 2 is empty or numeric (barcode)
            if not next_code or re.match(r'^\d{8,14}$', next_code):
                b1 = next_row[4].strip() if len(next_row) > 4 else ""
                b2 = next_row[8].strip() if len(next_row) > 8 else ""
                if b1 and re.match(r'^\d{8,14}$', b1):
                    barcodes.append(b1)
                if b2 and re.match(r'^\d{8,14}$', b2):
                    barcodes.append(b2)

        products.append({
            "supplier_code": code,
            "description": desc,
            "price_ex_vat": round(price, 2),
            "stock_onhand": int(onhand) if onhand > 0 else 0,
            "stock_available": int(available) if available > 0 else 0,
            "barcodes": "|".join(barcodes),
            "supplier": "Impulse Imports",
        })

        i += 1

    return products


def main():
    parser = argparse.ArgumentParser(description="Extract Impulse Imports price list to clean CSV")
    parser.add_argument("--input", required=True, help="Path to Impulse price list CSV (Google Sheets export)")
    parser.add_argument("--out", default=".", help="Output directory (default: current dir)")
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    out_path = Path(args.out) / "impulse_products.csv"

    print(f"Parsing: {args.input}")
    products = parse_impulse_csv(args.input)

    # Filter out zero-price products
    valid = [p for p in products if p["price_ex_vat"] > 0]
    zero_price = [p for p in products if p["price_ex_vat"] == 0]
    in_stock = [p for p in valid if p["stock_available"] > 0]

    fieldnames = ["supplier_code", "description", "price_ex_vat", "stock_onhand",
                  "stock_available", "barcodes", "supplier"]

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(valid)

    print(f"\nResults:")
    print(f"  Total products parsed : {len(products)}")
    print(f"  With valid price      : {len(valid)}")
    print(f"  Zero price (skipped)  : {len(zero_price)}")
    print(f"  In stock (available>0): {len(in_stock)}")
    print(f"\nOutput: {out_path}")


if __name__ == "__main__":
    main()
