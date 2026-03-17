#!/usr/bin/env python3
"""
WooCommerce Direct Import
=========================
Pushes products to happyharvesting.co.za via REST API.

What this script does:
  UPDATE  8 existing products  (wc_import_filled.csv     – enriched descriptions/prices)
  CREATE  28 new Hirsch products (hirsch_wc_import.csv)
  CREATE  1099 new Impulse products (impulse_new_products_enriched.csv)

Run:
  python3 wc_push_import.py

Resume after interruption:
  python3 wc_push_import.py --resume
"""

import csv, json, os, sys, time, argparse
from pathlib import Path
import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

# ── Paths & config ─────────────────────────────────────────────────────────────
BASE = Path(__file__).parent
SC   = BASE / "stock_control"

load_dotenv(BASE / ".env")
WC_URL    = os.environ["WC_URL"].rstrip("/")
WC_KEY    = os.environ["WC_KEY"]
WC_SECRET = os.environ["WC_SECRET"]
AUTH      = HTTPBasicAuth(WC_KEY, WC_SECRET)
API       = f"{WC_URL}/wp-json/wc/v3"

BATCH_SIZE = 25       # products per batch API call (conservative to avoid timeouts)
DELAY      = 1.5      # seconds between batches

PROGRESS_FILE = SC / "import_progress.json"
ERROR_FILE    = SC / "import_errors.json"

# ── Low-level API helpers ──────────────────────────────────────────────────────
def api_get(endpoint, params=None):
    r = requests.get(f"{API}/{endpoint}", auth=AUTH, params=params or {}, timeout=30)
    r.raise_for_status()
    return r.json(), r.headers

def api_post(endpoint, data):
    r = requests.post(f"{API}/{endpoint}", auth=AUTH, json=data, timeout=90)
    return r

def fetch_all_pages(endpoint, params=None):
    items, page = [], 1
    while True:
        p = {**(params or {}), "per_page": 100, "page": page}
        data, headers = api_get(endpoint, p)
        if not data:
            break
        items.extend(data)
        total_pages = int(headers.get("X-WP-TotalPages", 1))
        print(f"    page {page}/{total_pages} ({len(items)} loaded)\r", end="", flush=True)
        if page >= total_pages:
            break
        page += 1
    print()
    return items


# ── Taxonomy manager (categories, tags, brands) ────────────────────────────────
class TaxManager:
    def __init__(self, endpoint, label):
        self.endpoint = endpoint
        self.label    = label
        self._map     = {}   # lower(name) → id
        self._loaded  = False

    def load(self):
        if self._loaded:
            return
        print(f"  Loading {self.label}...")
        items = fetch_all_pages(self.endpoint)
        for item in items:
            self._map[item["name"].lower().strip()] = item["id"]
        print(f"  {len(items)} {self.label} loaded")
        self._loaded = True

    def get_or_create(self, name, parent_id=None):
        self.load()
        key = name.strip().lower()
        if key in self._map:
            return self._map[key]
        payload = {"name": name.strip()}
        if parent_id:
            payload["parent"] = parent_id
        r = requests.post(f"{API}/{self.endpoint}", auth=AUTH, json=payload, timeout=30)
        if r.ok:
            item = r.json()
            self._map[key] = item["id"]
            return item["id"]
        # term_exists → WooCommerce returns the existing term's ID in data.resource_id
        try:
            err = r.json()
            if err.get("code") == "term_exists":
                existing_id = err.get("data", {}).get("resource_id")
                if existing_id:
                    self._map[key] = existing_id
                    return existing_id
        except Exception:
            pass
        # Fallback: search by name (handles slight name differences / &amp; encoding)
        sr = requests.get(f"{API}/{self.endpoint}", auth=AUTH,
                          params={"search": name.strip(), "per_page": 10}, timeout=15)
        if sr.ok:
            for it in sr.json():
                if it["name"].lower().strip() == key:
                    self._map[key] = it["id"]
                    return it["id"]
                # Also match HTML-decoded names (e.g. &amp; → &)
                import html
                if html.unescape(it["name"]).lower().strip() == key:
                    self._map[key] = it["id"]
                    return it["id"]
        print(f"    WARN: could not resolve {self.label} '{name}': {r.text[:120]}")
        return None

    def resolve_csv(self, names_str):
        """Turn 'Parent > Child, Other' into [{id:N}, {id:M}]."""
        self.load()
        if not names_str or not names_str.strip():
            return []
        out = []
        for segment in names_str.split(","):
            segment = segment.strip()
            if not segment:
                continue
            parts     = [p.strip() for p in segment.split(">")]
            parent_id = None
            last_id   = None
            for part in parts:
                last_id   = self.get_or_create(part, parent_id)
                parent_id = last_id
            if last_id:
                out.append({"id": last_id})
        return out

    def resolve_simple(self, name_str):
        """Single name → [{id:N}] (for brands – no hierarchy)."""
        self.load()
        name = name_str.strip()
        if not name:
            return []
        brand_id = self.get_or_create(name)
        return [{"id": brand_id}] if brand_id else []


# Initialise taxonomy managers
CATS   = TaxManager("products/categories", "categories")
TAGS   = TaxManager("products/tags",       "tags")
BRANDS = TaxManager("products/brands",     "brands")


# ── CSV row → WooCommerce API payload ──────────────────────────────────────────
def row_to_payload(row, is_update=False):
    """
    Convert a CSV row to a WC REST API product dict.
    is_update=True  → skip empty fields so we don't wipe existing WC data.
    is_update=False → include all fields for new products.
    """
    p = {}

    def set_field(key, val):
        if val or not is_update:
            p[key] = val

    # Identity
    sku  = row.get("SKU", "").strip()
    name = row.get("Name", "").strip()
    set_field("sku",  sku)
    set_field("name", name)
    set_field("type", (row.get("Type", "") or "simple").strip().lower())

    # Status
    pub = str(row.get("Published", "1")).strip()
    p["status"] = "publish" if pub in ("1", "true") else "draft"

    # Descriptions (never blank-out on update)
    desc  = row.get("Description",       "").strip()
    short = row.get("Short description", "").strip()
    if desc:
        p["description"] = desc
    if short:
        p["short_description"] = short

    # Pricing
    reg  = row.get("Regular price", "").strip()
    sale = row.get("Sale price",    "").strip()
    if reg:
        p["regular_price"] = reg
    if sale:
        p["sale_price"] = sale

    # Tax
    tax = row.get("Tax status", "").strip()
    if tax:
        p["tax_status"] = tax

    # Stock
    in_stk = str(row.get("In stock?", "1")).strip()
    p["stock_status"] = "instock" if in_stk in ("1", "true", "yes", "") else "outofstock"
    qty = row.get("Stock", "").strip()
    if qty:
        p["manage_stock"]   = True
        p["stock_quantity"] = int(qty)

    # Dimensions
    if row.get("Weight (kg)", "").strip():
        p["weight"] = row["Weight (kg)"].strip()
    dims = {}
    for csv_col, api_key in [("Length (cm)", "length"), ("Width (cm)", "width"), ("Height (cm)", "height")]:
        v = row.get(csv_col, "").strip()
        if v:
            dims[api_key] = v
    if dims:
        p["dimensions"] = dims

    # Images — skip domains known to block hotlinking or that return errors
    BLOCKED_DOMAINS = ("hirschs.co.za", "hirsch.co.za")
    img_str = row.get("Images", "").strip()
    if img_str:
        valid_imgs = []
        for u in img_str.split(","):
            u = u.strip()
            if not u:
                continue
            if any(d in u for d in BLOCKED_DOMAINS):
                continue   # silently skip – CDN blocks external hotlinks
            valid_imgs.append({"src": u})
        if valid_imgs:
            p["images"] = valid_imgs

    # Taxonomy (only set if values present, to avoid wiping on updates)
    cat_str = row.get("Categories", "").strip()
    if cat_str:
        resolved = CATS.resolve_csv(cat_str)
        if resolved:
            p["categories"] = resolved

    tag_str = row.get("Tags", "").strip()
    if tag_str:
        resolved = TAGS.resolve_csv(tag_str)
        if resolved:
            p["tags"] = resolved

    brand_str = row.get("Brands", "").strip()
    if brand_str:
        resolved = BRANDS.resolve_simple(brand_str)
        if resolved:
            p["brands"] = resolved

    # SEO meta (Yoast)
    meta = []
    for csv_col, meta_key in [
        ("_yoast_wpseo_title",        "_yoast_wpseo_title"),
        ("_yoast_wpseo_metadesc",     "_yoast_wpseo_metadesc"),
        ("_yoast_wpseo_focuskw",      "_yoast_wpseo_focuskw"),
        ("Meta: _yoast_wpseo_metadesc", "_yoast_wpseo_metadesc"),
        ("Meta: title",               "_yoast_wpseo_title"),
    ]:
        v = row.get(csv_col, "").strip()
        if v:
            meta.append({"key": meta_key, "value": v})

    if meta:
        # Deduplicate meta keys (keep last value)
        seen = {}
        for m in meta:
            seen[m["key"]] = m["value"]
        p["meta_data"] = [{"key": k, "value": v} for k, v in seen.items()]

    return p


# ── Batch sender ───────────────────────────────────────────────────────────────
def send_batch(action, items, errors_out):
    """
    action: "create" or "update"
    items: list of WC product dicts
    errors_out: list to append errors to
    Returns count of successes.
    """
    payload = {action: items}
    r = api_post("products/batch", payload)
    if not r.ok:
        print(f"\n  HTTP {r.status_code}: {r.text[:300]}")
        errors_out.append({"action": action, "http": r.status_code, "body": r.text[:500]})
        return 0

    data     = r.json()
    returned = data.get(action, [])
    ok             = 0
    img_fail_idxs  = []   # positions of image-failed items to retry without images
    for i, item in enumerate(returned):
        if isinstance(item, dict) and item.get("id"):
            ok += 1
        elif isinstance(item, dict) and item.get("error"):
            err  = item["error"]
            code = err.get("code", "")
            if "image" in code.lower():
                img_fail_idxs.append(i)   # retry without image
            elif code == "product_invalid_sku":
                # Product already exists (from a previous in-flight request).
                # Update it with our enriched payload to ensure correct data.
                existing_id = err.get("data", {}).get("resource_id")
                if existing_id:
                    dup_p = dict(items[i])
                    dup_p["id"] = existing_id
                    dup_p.pop("images", None)   # skip images on update to avoid CDN issues
                    ru = api_post("products/batch", {"update": [dup_p]})
                    if ru.ok and ru.json().get("update", [{}])[0].get("id"):
                        ok += 1   # count as success
                    else:
                        errors_out.append({"action": "sku_dup_update", "id": existing_id,
                                           "sku": item.get("sku", "?")})
                else:
                    ok += 1   # already exists, counts as done
            else:
                errors_out.append({"action": action, "error": err,
                                   "sku": item.get("sku", "?"), "name": item.get("name", "?")})

    # Retry image-failed items without images
    if img_fail_idxs:
        retry_items = []
        for i in img_fail_idxs:
            p = dict(items[i])
            p.pop("images", None)
            retry_items.append(p)
        print(f"    Retrying {len(retry_items)} image-failed items without images...")
        r2 = api_post("products/batch", {action: retry_items})
        if r2.ok:
            for item in r2.json().get(action, []):
                if isinstance(item, dict) and item.get("id"):
                    ok += 1
                elif isinstance(item, dict) and item.get("error"):
                    err = item["error"]
                    if err.get("code") == "product_invalid_sku":
                        ok += 1   # already exists, count as done
                    else:
                        errors_out.append({"action": f"{action}_img_retry", "error": err,
                                           "sku": item.get("sku", "?")})
        else:
            errors_out.append({"action": f"{action}_img_retry", "http": r2.status_code,
                               "body": r2.text[:300]})

    return ok


# ── Load CSV sources ───────────────────────────────────────────────────────────
def load_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume", action="store_true",
                        help="Skip already-processed batches")
    args = parser.parse_args()

    # Load progress
    progress = {"updates_done": 0, "creates_done": 0}
    if args.resume and PROGRESS_FILE.exists():
        progress = json.loads(PROGRESS_FILE.read_text())
        print(f"Resuming: {progress['updates_done']} updates, {progress['creates_done']} creates already done")

    errors = []
    if ERROR_FILE.exists() and args.resume:
        errors = json.loads(ERROR_FILE.read_text())

    # ── Pre-load taxonomies ──────────────────────────────────────────────────
    print("\nPre-loading taxonomies...")
    CATS.load()
    TAGS.load()
    BRANDS.load()

    # ── Source 1: UPDATE existing products (wc_import_filled.csv) ────────────
    filled_path = SC / "wc_import_filled.csv"
    updates_done = progress["updates_done"]
    if filled_path.exists():
        filled_rows = load_csv(filled_path)
        update_batches = [filled_rows[i:i+BATCH_SIZE]
                          for i in range(0, len(filled_rows), BATCH_SIZE)]
        total_update_batches = len(update_batches)

        print(f"\nUpdating {len(filled_rows)} existing products "
              f"({total_update_batches} batch(es))...")

        for bi, batch in enumerate(update_batches):
            if bi < updates_done:
                print(f"  Skipping update batch {bi+1}/{total_update_batches} (already done)")
                continue

            payloads = []
            for row in batch:
                wc_id = row.get("ID", "").strip()
                if not wc_id:
                    print(f"  SKIP: no ID for '{row.get('Name','')}' in wc_import_filled.csv")
                    continue
                pl = row_to_payload(row, is_update=True)
                pl["id"] = int(wc_id)
                payloads.append(pl)

            if payloads:
                ok = send_batch("update", payloads, errors)
                print(f"  Update batch {bi+1}/{total_update_batches}: {ok}/{len(payloads)} OK")

            updates_done += 1
            progress["updates_done"] = updates_done
            PROGRESS_FILE.write_text(json.dumps(progress))
            time.sleep(DELAY)
    else:
        print("  wc_import_filled.csv not found — skipping updates")

    # ── Source 2 & 3: CREATE new products ────────────────────────────────────
    create_sources = [
        (SC / "hirsch_wc_import.csv",             "Hirsch"),
        (SC / "impulse_new_products_enriched.csv", "Impulse (enriched)"),
    ]

    all_new_rows = []
    for path, label in create_sources:
        if path.exists():
            rows = load_csv(path)
            # Filter out any that already have an ID (they'd be updates, not creates)
            new_rows = [r for r in rows if not r.get("ID", "").strip()]
            print(f"  {label}: {len(new_rows)} new products to create")
            all_new_rows.extend(new_rows)
        else:
            print(f"  WARN: {path.name} not found")

    create_batches = [all_new_rows[i:i+BATCH_SIZE]
                      for i in range(0, len(all_new_rows), BATCH_SIZE)]
    total_create_batches = len(create_batches)
    creates_done = progress["creates_done"]

    print(f"\nCreating {len(all_new_rows)} new products "
          f"({total_create_batches} batch(es))...")

    created_total = 0
    for bi, batch in enumerate(create_batches):
        if bi < creates_done:
            pct = int((bi+1)/total_create_batches*100)
            print(f"  Skipping create batch {bi+1}/{total_create_batches} (already done) [{pct}%]")
            created_total += len(batch)
            continue

        payloads = [row_to_payload(row, is_update=False) for row in batch]
        ok = send_batch("create", payloads, errors)
        created_total += ok
        pct = int((bi+1)/total_create_batches*100)
        print(f"  Create batch {bi+1}/{total_create_batches}: {ok}/{len(payloads)} OK "
              f"[{pct}%] ({created_total} total so far)")

        creates_done += 1
        progress["creates_done"] = creates_done
        PROGRESS_FILE.write_text(json.dumps(progress))

        # Save errors after every batch
        if errors:
            ERROR_FILE.write_text(json.dumps(errors, indent=2))

        time.sleep(DELAY)

    # ── Final summary ─────────────────────────────────────────────────────────
    print(f"\n{'='*55}")
    print("IMPORT COMPLETE")
    print(f"  Products created : {created_total}")
    print(f"  Error count      : {len(errors)}")
    if errors:
        print(f"  Error details    : {ERROR_FILE}")
        for e in errors[:5]:
            print(f"    {e}")
        if len(errors) > 5:
            print(f"    ... and {len(errors)-5} more")
    else:
        print("  No errors!")

    # Clean up progress file on success
    if not errors and PROGRESS_FILE.exists():
        PROGRESS_FILE.unlink()

    return len(errors) == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
