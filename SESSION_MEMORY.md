# Session Memory — WooCommerce Cleanup & Sync

**Branch:** `claude/woocommerce-cleanup-sync-4knTf`
**Date:** 2026-03-25
**Session ID:** 61626457-acb5-4834-a26a-ac0b90c61b45

---

## What We Were Doing

Running `wc_push_import.py` to bulk-create new products from `impulse_new_products_enriched.csv`
into WooCommerce via the REST API.

Progress file: `woocommerce/stock_control/import_progress.json`

---

## State When Session Ended

```json
{"updates_done": 1, "creates_done": 16}
```

- **16 create batches done** (batch size = 10, so ~160 products created)
- **1 product updated** (already existed, matched by SKU)
- Total create batches: **113**
- Progress at time of last check: **~14%** (batch 16/113)
- A ReadTimeout hit batch 17 — retry logic kicked in (`retrying in 4s, attempt 1/3`) — this is expected and handled

---

## Recent Changes Made This Session

| Commit | Description |
|--------|-------------|
| `21b7d0d` | Update import progress checkpoint |
| `2a33ffe` | Reduce batch size to 10, add ReadTimeout retry in wc_push_import |
| `b069113` | Update import progress checkpoint |
| `15d6296` | Recalculate impulse prices: catalogue retail × VAT × markup |
| `6971dbc` | Fix impulse catalogue pricing: use retail_price + VAT + markup only |
| `504d4fe` | Add match_remaining_images.py for fuzzy gallery image matching |
| `49c56c4` | Fix gallery image assignment using media ID instead of URL sideload |
| `bac8725` | Add image fill progress tracking file |
| `e1549ce` | Fix fill_missing_images to handle relative source_url paths |
| `68d4877` | Add Kristal & gap price scripts; push 229 Kristal variant prices + gap fills |

---

## Key Files

| File | Purpose |
|------|---------|
| `woocommerce/stock_control/import_progress.json` | Tracks create/update batch progress for resumable import |
| `woocommerce/stock_control/impulse_new_products_enriched.csv` | Source data — enriched Impulse products to create in WC |
| `woocommerce/stock_control/impulse_skipped.csv` | Products skipped (already exist or errored) |
| `woocommerce/stock_control/master_stock.csv` | Master stock reference |
| `woocommerce/stock_control/missing_images.csv` | Products with no gallery images |

---

## Key Scripts

| Script | Purpose |
|--------|---------|
| `woocommerce/wc_push_import.py` | Main import script — creates/updates WC products in batches, resumable |
| `woocommerce/match_remaining_images.py` | Fuzzy-matches gallery images to products |
| `woocommerce/fill_missing_images.py` | Fills missing product gallery images |

---

## Architecture Notes

- **Batch size:** 10 products per API call (reduced from 50 to avoid timeouts)
- **Retry logic:** 3 attempts on ReadTimeout, exponential backoff (2s, 4s, 8s)
- **Resumable:** Script reads `import_progress.json` and skips already-done batches
- **Image strategy:** Images sideloaded to WC media library by ID (not URL)

---

## Next Steps (pick up here)

1. Wait for `wc_push_import.py` to finish (~113 batches total, was at 14%)
2. Check `impulse_skipped.csv` for any failed creates — investigate and retry if needed
3. Run `match_remaining_images.py` on newly created products
4. Verify prices look correct in WC (catalogue retail × VAT × markup formula)
5. Final QA pass on newly created products in WooCommerce admin

---

## To Resume Monitoring

```bash
# Check progress
cat woocommerce/stock_control/import_progress.json

# Watch live output if script still running
ps aux | grep wc_push_import

# Re-run if it stopped (it's resumable)
python3 woocommerce/wc_push_import.py
```
