#!/usr/bin/env python3
"""
Barcode Enrichment Script
Anson Supermart Catalog Management

Queries Open Food Facts API using product barcodes to auto-fill:
  - Product name
  - Brand
  - Size / quantity

Only fills fields that are currently empty — never overwrites manual edits.
Skips products that are already fully enriched.

Usage:
  python barcode_enrich.py              # Run on all unenriched products
  python barcode_enrich.py --limit 500  # Process only 500 products
  python barcode_enrich.py --dry-run    # Preview without writing to DB
  python barcode_enrich.py --merkey 1002089  # Test a single product
"""

import sqlite3
import requests
import time
import re
import argparse
from datetime import datetime
from pathlib import Path

DB_PATH = "anson_products.db"
OFF_API = "https://world.openfoodfacts.org/api/v0/product/{barcode}.json"
REQUEST_DELAY = 0.2   # seconds between API calls (be a good citizen)
BATCH_COMMIT = 50     # commit to DB every N products
TIMEOUT = 10          # request timeout in seconds

USER_AGENT = "AnsonSupermart-CatalogEnricher/1.0 (catalog@ansonsupermart.com)"


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def slugify(text: str) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s/]+", "-", text)
    text = text.replace("&", "and")
    text = re.sub(r"-{2,}", "-", text)
    return text.strip("-")


def get_or_create_brand(cur, brand_name: str) -> int:
    brand_name = brand_name.strip().title()
    cur.execute("SELECT id FROM brands WHERE name=?", (brand_name,))
    r = cur.fetchone()
    if r:
        return r["id"]
    cur.execute("INSERT INTO brands(name, slug) VALUES(?, ?)",
                (brand_name, slugify(brand_name)))
    return cur.lastrowid


def compute_quality(description, name, brand_id, category_id, size) -> tuple[str, int]:
    issues = []
    if not (description or "").strip(): issues.append("NEEDS_DESCRIPTION")
    if not (name or "").strip():         issues.append("NEEDS_NAME")
    if not brand_id:                      issues.append("NEEDS_BRAND")
    if not category_id:                   issues.append("NEEDS_CATEGORY")
    if not (size or "").strip():          issues.append("NEEDS_SIZE")
    if not issues:
        return "COMPLETE", 0
    return issues[0], 1


# ---------------------------------------------------------------------------
# Open Food Facts API
# ---------------------------------------------------------------------------

def is_valid_barcode(barcode: str) -> bool:
    """Return True if barcode looks like a real EAN-13/UPC-A, not an internal code."""
    if not barcode:
        return False
    # Must be all digits
    if not barcode.isdigit():
        return False
    # Must be 8, 12, or 13 digits (EAN-8, UPC-A, EAN-13)
    if len(barcode) not in (8, 12, 13):
        return False
    # Skip internal Anson POS-generated barcodes (start with 4000001)
    if barcode.startswith("4000001"):
        return False
    return True


def fetch_off(barcode: str, session: requests.Session) -> dict | None:
    """Return OFF product dict or None on miss/error."""
    url = OFF_API.format(barcode=barcode)
    try:
        resp = session.get(url, timeout=TIMEOUT,
                           headers={"User-Agent": USER_AGENT})
        if resp.status_code != 200:
            return None
        data = resp.json()
        if data.get("status") != 1:
            return None
        return data.get("product", {})
    except Exception:
        return None


def extract_fields(product: dict) -> dict:
    """Pull the fields we care about from an OFF product dict."""
    name = (
        product.get("product_name_en") or
        product.get("product_name") or
        ""
    ).strip()

    brands_raw = (product.get("brands") or "").strip()
    # OFF sometimes returns comma-separated brand list; take the first one
    brand = brands_raw.split(",")[0].strip() if brands_raw else ""

    size = (product.get("quantity") or "").strip()

    return {"name": name, "brand": brand, "size": size}


# ---------------------------------------------------------------------------
# Core enrichment logic
# ---------------------------------------------------------------------------

def enrich_products(limit: int | None, dry_run: bool, target_merkey: str | None):
    conn = get_db()
    cur = conn.cursor()

    # Build query for products that still need work and have at least one barcode
    if target_merkey:
        cur.execute("""
            SELECT p.merkey, p.name, p.brand_id, p.size, p.description,
                   p.category_id, p.data_quality
            FROM products p
            WHERE p.merkey = ?
        """, (target_merkey,))
    else:
        cur.execute("""
            SELECT p.merkey, p.name, p.brand_id, p.size, p.description,
                   p.category_id, p.data_quality
            FROM products p
            WHERE p.needs_enrichment = 1
              AND p.active = 1
              AND EXISTS (
                  SELECT 1 FROM barcodes b
                  WHERE b.merkey = p.merkey
                    AND b.barcode NOT LIKE '4000001%'
                    AND length(b.barcode) IN (8, 12, 13)
                    AND b.barcode GLOB '[0-9]*'
              )
            ORDER BY p.merkey ASC
        """ + (f" LIMIT {limit}" if limit else ""))

    products = cur.fetchall()
    total = len(products)

    print("=" * 70)
    print("BARCODE ENRICHMENT — Open Food Facts")
    print("=" * 70)
    print(f"Products to process : {total:,}")
    print(f"Dry run             : {dry_run}")
    print(f"Delay between calls : {REQUEST_DELAY}s")
    print()

    if total == 0:
        print("Nothing to do — all products are already enriched.")
        conn.close()
        return

    stats = {
        "processed": 0,
        "hit": 0,        # barcode matched in OFF
        "miss": 0,       # barcode not found in OFF
        "no_barcode": 0, # product has no barcodes at all
        "skipped": 0,    # all fields already filled, nothing new to write
        "updated": 0,    # actually wrote something to DB
        "errors": 0,
    }

    session = requests.Session()

    for i, product in enumerate(products, 1):
        merkey = product["merkey"]
        existing_name   = (product["name"]  or "").strip()
        existing_brand  = product["brand_id"]
        existing_size   = (product["size"]  or "").strip()

        # Fetch barcodes for this product (primary first, then alternates)
        cur.execute("""
            SELECT barcode FROM barcodes
            WHERE merkey = ?
            ORDER BY is_primary DESC, id ASC
        """, (merkey,))
        barcodes = [r["barcode"] for r in cur.fetchall() if is_valid_barcode(r["barcode"])]

        if not barcodes:
            stats["no_barcode"] += 1
            stats["processed"] += 1
            continue

        # Try each barcode until we get a hit
        off_data = None
        matched_barcode = None
        for bc in barcodes:
            off_data = fetch_off(bc, session)
            time.sleep(REQUEST_DELAY)
            if off_data:
                matched_barcode = bc
                break

        stats["processed"] += 1

        if not off_data:
            stats["miss"] += 1
            if i % 100 == 0 or i == total:
                _print_progress(i, total, stats)
            continue

        stats["hit"] += 1
        fields = extract_fields(off_data)

        # Only fill fields that are currently empty
        new_name   = fields["name"]   if not existing_name  else existing_name
        new_brand  = fields["brand"]  if not existing_brand else None  # None = keep existing id
        new_size   = fields["size"]   if not existing_size  else existing_size

        # Resolve brand to id
        new_brand_id = existing_brand
        if new_brand and not existing_brand:
            new_brand_id = get_or_create_brand(cur, new_brand)

        # Check if there's actually anything new to write
        nothing_new = (
            new_name == existing_name and
            new_brand_id == existing_brand and
            new_size == existing_size
        )
        if nothing_new:
            stats["skipped"] += 1
            continue

        dq, ne = compute_quality(
            product["description"], new_name, new_brand_id, product["category_id"], new_size
        )

        if dry_run:
            print(f"  [DRY] {merkey} | barcode={matched_barcode}")
            print(f"        name  : {existing_name!r} -> {new_name!r}")
            print(f"        brand : {existing_brand} -> {new_brand_id} ({new_brand})")
            print(f"        size  : {existing_size!r} -> {new_size!r}")
            print(f"        quality: {dq}")
            stats["updated"] += 1
        else:
            cur.execute("""
                UPDATE products
                SET name = ?,
                    brand_id = ?,
                    size = ?,
                    data_quality = ?,
                    needs_enrichment = ?,
                    enrichment_notes = 'Auto-enriched via Open Food Facts barcode lookup',
                    updated_at = CURRENT_TIMESTAMP
                WHERE merkey = ?
            """, (new_name, new_brand_id, new_size, dq, ne, merkey))
            stats["updated"] += 1

            # Commit in batches
            if stats["updated"] % BATCH_COMMIT == 0:
                conn.commit()

        if i % 50 == 0 or i == total:
            _print_progress(i, total, stats)

    # Final commit
    if not dry_run:
        conn.commit()

    conn.close()

    print()
    print("=" * 70)
    print("ENRICHMENT SUMMARY")
    print("=" * 70)
    print(f"Processed   : {stats['processed']:,}")
    print(f"  OFF hits  : {stats['hit']:,}  ({stats['hit']/max(stats['processed'],1)*100:.1f}%)")
    print(f"  OFF misses: {stats['miss']:,}")
    print(f"  No barcode: {stats['no_barcode']:,}")
    print(f"  Skipped   : {stats['skipped']:,}  (already filled)")
    print(f"  Updated   : {stats['updated']:,}")
    print("=" * 70)
    if dry_run:
        print("DRY RUN — no changes written.")
    else:
        print("DONE.")
    print("=" * 70)


def _print_progress(i, total, stats):
    pct = i / total * 100
    print(f"  [{i:>6}/{total}  {pct:4.1f}%]  "
          f"hits={stats['hit']}  misses={stats['miss']}  "
          f"updated={stats['updated']}  skipped={stats['skipped']}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Enrich products from Open Food Facts using barcodes"
    )
    parser.add_argument("--limit", type=int, default=None,
                        help="Max number of products to process (default: all)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview changes without writing to DB")
    parser.add_argument("--merkey", type=str, default=None,
                        help="Test enrichment for a single MERKEY")
    args = parser.parse_args()

    print()
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    enrich_products(
        limit=args.limit,
        dry_run=args.dry_run,
        target_merkey=args.merkey,
    )


if __name__ == "__main__":
    main()
