# WooCommerce Cleanup & Sync Tools

## Step-by-Step Terminal Commands

### Prerequisites
Open Ubuntu terminal and activate your Python environment:
```bash
cd ~/happy-harvesting
source venv/bin/activate
```

---

## STEP 1 — Audit your WooCommerce store

Find all problems: missing prices, zero stock, missing images, missing brands.

```bash
python3 ~/happy-harvesting/scripts/woocommerce/wc_audit.py \
  --csv "/mnt/c/Users/Dell/Downloads/wc-product-export-11-3-2026-1773263199929.csv" \
  --out "/mnt/c/Users/Dell/Downloads/wc_audit_output/"
```

**Output files:**
- `audit_published_no_price.csv` — urgent: live products with no price
- `audit_no_price.csv` — all products missing price
- `audit_no_image.csv` — products missing images
- `audit_zero_stock.csv` — products with zero stock
- `audit_no_brand.csv` — products missing brand

---

## STEP 2 — Extract Hirschs supplier data

Creates a CSV with all ~450 products from the Hirschs pamphlet.

```bash
cd "/mnt/c/Users/Dell/Downloads/" && \
python3 ~/happy-harvesting/scripts/woocommerce/hirsch_extract.py
```

**Output:** `Hirschs_Products_Extracted.csv`

---

## STEP 3 — Match Hirschs products to your WooCommerce store

```bash
python3 ~/happy-harvesting/scripts/woocommerce/supplier_match.py \
  --wc  "/mnt/c/Users/Dell/Downloads/wc-product-export-11-3-2026-1773263199929.csv" \
  --sup "/mnt/c/Users/Dell/Downloads/Hirschs_Products_Extracted.csv" \
  --out "/mnt/c/Users/Dell/Downloads/wc_match_output/" \
  --markup 1.4
```

`--markup 1.4` = 40% markup over supplier cost. Change to `1.35` for 35%, `1.5` for 50%, etc.

**Output files:**
- `matched_products.csv` — WC products successfully matched to Hirschs
- `unmatched_wc.csv` — your WC products with no Hirschs match
- `unmatched_supplier.csv` — Hirschs products not in your store (new products to add)
- `price_update_import.csv` — quick price-only update import for WooCommerce

---

## STEP 4 — Generate full WooCommerce import with all fixes

```bash
python3 ~/happy-harvesting/scripts/woocommerce/wc_import_gen.py \
  --wc      "/mnt/c/Users/Dell/Downloads/wc-product-export-11-3-2026-1773263199929.csv" \
  --matched "/mnt/c/Users/Dell/Downloads/wc_match_output/matched_products.csv" \
  --out     "/mnt/c/Users/Dell/Downloads/wc_import_clean.csv" \
  --markup  1.4 \
  --set-stock 10
```

**Output:** `wc_import_clean.csv`

---

## STEP 5 — Import back into WooCommerce

1. Go to **WooCommerce > Products > Import**
2. Upload `wc_import_clean.csv`
3. Tick **"Update existing products"**
4. Map columns → Run Import

---

## Copy scripts to WSL first

```bash
cp /mnt/c/Users/Dell/hh_hirsch_extract.py ~/happy-harvesting/scripts/woocommerce/hirsch_extract.py
mkdir -p ~/happy-harvesting/scripts/woocommerce/
cp /path/to/woocommerce/*.py ~/happy-harvesting/scripts/woocommerce/
```

Or clone directly from this repo:
```bash
cd ~/happy-harvesting/scripts
git clone <this-repo-url> .
```
