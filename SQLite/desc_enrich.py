#!/usr/bin/env python3
"""
Description Enrichment Script
Anson Supermart Catalog Management

Parses the raw MEDESC field to auto-fill:
  - size   : extracted via regex (e.g. "350G", "500ML", "1LT")
  - name   : cleaned-up description (title-cased, pack counts removed)
  - brand  : matched against known brand prefixes from existing data

Only fills fields that are currently empty — never overwrites manual edits.

Usage:
  python desc_enrich.py               # Run on all unenriched active products
  python desc_enrich.py --limit 200   # Process first 200
  python desc_enrich.py --dry-run     # Preview without writing
  python desc_enrich.py --merkey X    # Test single product
"""

import sqlite3
import re
import argparse
from datetime import datetime
from pathlib import Path

DB_PATH     = "anson_products.db"
BATCH_COMMIT = 200

# ---------------------------------------------------------------------------
# Size / pack extraction
# ---------------------------------------------------------------------------

# Matches things like: 350G  500ML  1LT  1.5KG  200G/24  750ML/12/6
# Also: 2PLY  40YRDS  25MM  8"  #7  P15.00
SIZE_RE = re.compile(
    r"""
    \b
    (
        \d+(?:[.,]\d+)?         # number (integer or decimal)
        \s*
        (?:
            KG|G|GM|MG          # weight
          | LT|ML|L\b           # volume
          | OZ|LBS?             # imperial weight
          | PLY                 # tissue ply
          | YRDS?|YDS?|M\b|CM|MM  # length
          | FT\b|IN\b
        )
    )
    """,
    re.VERBOSE | re.IGNORECASE
)

# Pack count suffix: /24  /12/8  /6/1  etc.
PACK_RE = re.compile(r'\s*/\d+(?:/\d+)*\s*$')

# Trailing reference codes / prices at end: "301009"  "P15.00"  "EO"
TRAILING_RE = re.compile(r'\s+(?:[A-Z]{1,3}\d+|\d{4,}|P\d+\.\d{2}|EO|REF|NEW)\s*$')


def extract_size(desc: str) -> str:
    """Return first size token found in desc, else empty string."""
    m = SIZE_RE.search(desc)
    if m:
        return m.group(0).strip().upper()
    return ""


def clean_name(desc: str) -> str:
    """
    Return a cleaned product name from MEDESC:
    - Remove trailing pack counts  (/24  /12/8)
    - Remove trailing size token and anything after it
    - Remove trailing reference codes
    - Remove leading '!' markers
    - Collapse whitespace, title-case
    """
    s = desc.strip().lstrip('!')
    # Remove size token and everything after it (pack count, ref codes)
    m = SIZE_RE.search(s)
    if m:
        s = s[:m.start()].strip()
    else:
        s = PACK_RE.sub('', s)
        s = TRAILING_RE.sub('', s)
    s = re.sub(r'\s{2,}', ' ', s).strip()
    return s.title()


# ---------------------------------------------------------------------------
# Brand prefix lookup
# ---------------------------------------------------------------------------

def build_brand_lookup(cur) -> dict[str, int]:
    """
    Build a dict mapping uppercase description prefixes → brand_id.

    For each brand that already has products with description data, we collect
    the first word(s) of those descriptions as prefixes for that brand.
    Returns longest-match-first lookup entries.
    """
    cur.execute("""
        SELECT UPPER(TRIM(p.description)) as desc, p.brand_id
        FROM products p
        WHERE p.brand_id IS NOT NULL
          AND p.description IS NOT NULL
          AND p.description != ''
        LIMIT 100000
    """)

    # prefix_len -> { prefix: brand_id }
    prefix_votes: dict[str, dict[int, int]] = {}  # prefix -> {brand_id: count}

    for row in cur.fetchall():
        desc = row["desc"]
        brand_id = row["brand_id"]
        # Collect 1-word and 2-word prefixes
        words = desc.split()
        for n in (1, 2):
            if len(words) >= n:
                prefix = " ".join(words[:n])
                if prefix not in prefix_votes:
                    prefix_votes[prefix] = {}
                prefix_votes[prefix][brand_id] = prefix_votes[prefix].get(brand_id, 0) + 1

    # For each prefix, pick the brand_id with the most votes
    # Only keep prefixes where >=80% of votes go to one brand (avoid ambiguous ones)
    lookup: dict[str, int] = {}
    for prefix, votes in prefix_votes.items():
        total = sum(votes.values())
        best_brand, best_count = max(votes.items(), key=lambda x: x[1])
        if best_count / total >= 0.80 and total >= 3:
            lookup[prefix] = best_brand

    return lookup


def match_brand(desc_upper: str, lookup: dict[str, int]) -> int | None:
    """Try 2-word prefix first, then 1-word. Return brand_id or None."""
    words = desc_upper.split()
    for n in (2, 1):
        if len(words) >= n:
            prefix = " ".join(words[:n])
            if prefix in lookup:
                return lookup[prefix]
    return None


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
    text = re.sub(r"-{2,}", "-", text)
    return text.strip("-")


def compute_quality(description, name, brand_id, category_id, size) -> tuple[str, int]:
    issues = []
    if not (description or "").strip(): issues.append("NEEDS_DESCRIPTION")
    if not (name or "").strip():         issues.append("NEEDS_NAME")
    if not brand_id:                      issues.append("NEEDS_BRAND")
    if not category_id:                   issues.append("NEEDS_CATEGORY")
    if not (size or "").strip():          issues.append("NEEDS_SIZE")
    return ("COMPLETE", 0) if not issues else (issues[0], 1)


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def enrich_products(limit, dry_run, target_merkey):
    conn = get_db()
    cur  = conn.cursor()

    print("Building brand prefix lookup...", flush=True)
    lookup = build_brand_lookup(cur)
    print(f"  {len(lookup):,} brand prefixes indexed", flush=True)
    print()

    if target_merkey:
        cur.execute("""
            SELECT merkey, description, name, brand_id, size, category_id, data_quality
            FROM products WHERE merkey = ?
        """, (target_merkey,))
    else:
        cur.execute("""
            SELECT merkey, description, name, brand_id, size, category_id, data_quality
            FROM products
            WHERE needs_enrichment = 1 AND active = 1
              AND description IS NOT NULL AND description != ''
              AND (name IS NULL OR name = '' OR brand_id IS NULL OR size IS NULL OR size = '')
            ORDER BY merkey ASC
        """ + (f" LIMIT {limit}" if limit else ""))

    products = cur.fetchall()
    total = len(products)

    print("=" * 70)
    print("DESCRIPTION ENRICHMENT — MEDESC Parser")
    print("=" * 70)
    print(f"Products to process : {total:,}")
    print(f"Dry run             : {dry_run}")
    print()

    stats = dict(processed=0, updated=0, skipped=0,
                 got_name=0, got_brand=0, got_size=0)

    for i, p in enumerate(products, 1):
        merkey      = p["merkey"]
        raw_desc    = (p["description"] or "").strip()
        exist_name  = (p["name"]  or "").strip()
        exist_brand = p["brand_id"]
        exist_size  = (p["size"]  or "").strip()

        if not raw_desc:
            stats["skipped"] += 1
            stats["processed"] += 1
            continue

        # --- Extract fields ---
        new_size  = extract_size(raw_desc) if not exist_size  else exist_size
        new_name  = clean_name(raw_desc)   if not exist_name  else exist_name
        new_brand = exist_brand
        if not exist_brand:
            new_brand = match_brand(raw_desc.upper(), lookup)

        # Check if anything new
        nothing_new = (
            new_name  == exist_name  and
            new_brand == exist_brand and
            new_size  == exist_size
        )
        if nothing_new:
            stats["skipped"] += 1
            stats["processed"] += 1
            continue

        dq, ne = compute_quality(raw_desc, new_name, new_brand, p["category_id"], new_size)

        if dry_run:
            if i <= 30 or (new_brand and not exist_brand):
                print(f"[DRY] {merkey} | {raw_desc[:35]}")
                if new_name  != exist_name:  print(f"      name  : {exist_name!r} -> {new_name!r}")
                if new_brand != exist_brand: print(f"      brand : {exist_brand} -> {new_brand}")
                if new_size  != exist_size:  print(f"      size  : {exist_size!r} -> {new_size!r}")
                print(f"      quality: {dq}")
        else:
            cur.execute("""
                UPDATE products
                SET name = ?, brand_id = ?, size = ?,
                    data_quality = ?, needs_enrichment = ?,
                    enrichment_notes = 'Auto-enriched via MEDESC parser',
                    updated_at = CURRENT_TIMESTAMP
                WHERE merkey = ?
            """, (new_name, new_brand, new_size, dq, ne, merkey))

        stats["updated"]   += 1
        if new_name  != exist_name:  stats["got_name"]  += 1
        if new_brand != exist_brand: stats["got_brand"] += 1
        if new_size  != exist_size:  stats["got_size"]  += 1
        stats["processed"] += 1

        if not dry_run and stats["updated"] % BATCH_COMMIT == 0:
            conn.commit()

        if i % 2000 == 0 or i == total:
            pct = i / total * 100
            print(f"  [{i:>6}/{total}  {pct:4.1f}%]  "
                  f"updated={stats['updated']}  skipped={stats['skipped']}", flush=True)

    if not dry_run:
        conn.commit()
    conn.close()

    print()
    print("=" * 70)
    print("ENRICHMENT SUMMARY")
    print("=" * 70)
    print(f"Processed   : {stats['processed']:,}")
    print(f"Updated     : {stats['updated']:,}")
    print(f"  got name  : {stats['got_name']:,}")
    print(f"  got brand : {stats['got_brand']:,}")
    print(f"  got size  : {stats['got_size']:,}")
    print(f"Skipped     : {stats['skipped']:,}  (no description or already filled)")
    print("=" * 70)
    print("DRY RUN — no changes written." if dry_run else "DONE.")
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(description="Enrich products from MEDESC descriptions")
    parser.add_argument("--limit",   type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--merkey",  type=str, default=None)
    args = parser.parse_args()

    print(f"\nStarted: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    enrich_products(args.limit, args.dry_run, args.merkey)


if __name__ == "__main__":
    main()
