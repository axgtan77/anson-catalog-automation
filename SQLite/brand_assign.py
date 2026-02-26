#!/usr/bin/env python3
"""
Brand Assignment Script
Anson Supermart Catalog Management

Two modes:
  1. auto    : Assigns brands to products whose MEDESC first word exactly
               matches an existing brand name in the brands table.
  2. map     : Bulk-assigns a brand to all unbranded products sharing a
               given MEDESC prefix (for known abbreviations).

Usage:
  python brand_assign.py auto --dry-run       # Preview auto-matches
  python brand_assign.py auto                 # Run auto-assignment
  python brand_assign.py map KELLY "Kelly"    # Map prefix -> brand name
  python brand_assign.py map HV "HV"          # (creates brand if needed)
  python brand_assign.py stats                # Show remaining unbranded prefixes
"""

import sqlite3
import re
import argparse
import sys
from datetime import datetime

DB_PATH = "anson_products.db"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def slugify(text):
    text = (text or "").strip().lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s/]+", "-", text)
    text = re.sub(r"-{2,}", "-", text)
    return text.strip("-")


def compute_quality(description, name, brand_id, category_id, size):
    issues = []
    if not (description or "").strip(): issues.append("NEEDS_DESCRIPTION")
    if not (name or "").strip():         issues.append("NEEDS_NAME")
    if not brand_id:                      issues.append("NEEDS_BRAND")
    if not category_id:                   issues.append("NEEDS_CATEGORY")
    if not (size or "").strip():          issues.append("NEEDS_SIZE")
    return ("COMPLETE", 0) if not issues else (issues[0], 1)


def get_prefix(description):
    words = (description or "").strip().lstrip("!").split()
    if not words:
        return None
    w = words[0].upper()
    if "." in w:
        w = w.split(".")[-1]
    return w


def get_or_create_brand(cur, brand_name):
    brand_name = brand_name.strip()
    cur.execute("SELECT id FROM brands WHERE UPPER(name)=UPPER(?)", (brand_name,))
    r = cur.fetchone()
    if r:
        return r["id"]
    cur.execute("INSERT INTO brands(name, slug) VALUES(?,?)",
                (brand_name, slugify(brand_name)))
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Mode: auto
# ---------------------------------------------------------------------------

def cmd_auto(dry_run):
    conn = get_db()
    cur  = conn.cursor()

    # Build exact prefix -> brand_id map from existing brands
    cur.execute("SELECT id, UPPER(name) as uname FROM brands")
    brands_map = {r["uname"]: r["id"] for r in cur.fetchall()}

    # Get all unbranded products with descriptions
    cur.execute("""
        SELECT merkey, description, name, brand_id, size, category_id
        FROM products
        WHERE active=1 AND brand_id IS NULL
          AND description IS NOT NULL AND description != ''
    """)
    products = cur.fetchall()

    updated = skipped = 0
    for p in products:
        prefix = get_prefix(p["description"])
        if not prefix:
            skipped += 1
            continue

        brand_id = brands_map.get(prefix)
        if not brand_id:
            skipped += 1
            continue

        dq, ne = compute_quality(p["description"], p["name"], brand_id,
                                 p["category_id"], p["size"])
        if not dry_run:
            cur.execute("""
                UPDATE products
                SET brand_id=?, data_quality=?, needs_enrichment=?,
                    enrichment_notes='Brand auto-assigned via prefix match',
                    updated_at=CURRENT_TIMESTAMP
                WHERE merkey=?
            """, (brand_id, dq, ne, p["merkey"]))
        else:
            if updated < 20:
                cur.execute("SELECT name FROM brands WHERE id=?", (brand_id,))
                bname = cur.fetchone()["name"]
                print(f"  [DRY] {p['merkey']} | {p['description'][:35]:<35} -> {bname}")
        updated += 1

    if not dry_run:
        conn.commit()
    conn.close()

    print(f"\nAuto-assigned : {updated:,}")
    print(f"No match      : {skipped:,}")
    if dry_run:
        print("DRY RUN — no changes written.")


# ---------------------------------------------------------------------------
# Mode: map
# ---------------------------------------------------------------------------

def cmd_map(prefix, brand_name, dry_run):
    prefix = prefix.upper()
    conn = get_db()
    cur  = conn.cursor()

    brand_id = get_or_create_brand(cur, brand_name)
    cur.execute("SELECT name FROM brands WHERE id=?", (brand_id,))
    actual_name = cur.fetchone()["name"]

    cur.execute("""
        SELECT merkey, description, name, size, category_id
        FROM products
        WHERE active=1 AND brand_id IS NULL
          AND description IS NOT NULL AND description != ''
    """)
    products = cur.fetchall()

    updated = 0
    for p in products:
        if get_prefix(p["description"]) != prefix:
            continue
        dq, ne = compute_quality(p["description"], p["name"], brand_id,
                                 p["category_id"], p["size"])
        if not dry_run:
            cur.execute("""
                UPDATE products
                SET brand_id=?, data_quality=?, needs_enrichment=?,
                    enrichment_notes='Brand assigned via prefix mapping',
                    updated_at=CURRENT_TIMESTAMP
                WHERE merkey=?
            """, (brand_id, dq, ne, p["merkey"]))
        else:
            if updated < 5:
                print(f"  [DRY] {p['merkey']} | {p['description'][:40]}")
        updated += 1

    if not dry_run:
        conn.commit()
    conn.close()

    print(f"\nPrefix '{prefix}' -> brand '{actual_name}' (id={brand_id})")
    print(f"Products updated: {updated:,}")
    if dry_run:
        print("DRY RUN — no changes written.")


# ---------------------------------------------------------------------------
# Mode: stats
# ---------------------------------------------------------------------------

def cmd_stats():
    conn = get_db()
    cur  = conn.cursor()

    cur.execute("""
        SELECT description FROM products
        WHERE active=1 AND brand_id IS NULL
          AND description IS NOT NULL AND description != ''
    """)

    from collections import Counter
    counts = Counter()
    for r in cur.fetchall():
        p = get_prefix(r["description"])
        if p:
            counts[p] += 1

    cur.execute("""
        SELECT COUNT(*) FROM products
        WHERE active=1 AND brand_id IS NULL
    """)
    total_unbranded = cur.fetchone()[0]
    conn.close()

    print(f"Total unbranded (active): {total_unbranded:,}")
    print(f"Unique prefixes          : {len(counts):,}")
    print()
    print(f"  {'PREFIX':<22} {'COUNT':>6}")
    print(f"  {'-'*22} {'-'*6}")
    for w, c in counts.most_common(50):
        print(f"  {w:<22} {c:>6}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")

    p_auto = sub.add_parser("auto")
    p_auto.add_argument("--dry-run", action="store_true")

    p_map = sub.add_parser("map")
    p_map.add_argument("prefix")
    p_map.add_argument("brand_name")
    p_map.add_argument("--dry-run", action="store_true")

    sub.add_parser("stats")

    args = parser.parse_args()
    print(f"\nStarted: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    if args.cmd == "auto":
        cmd_auto(args.dry_run)
    elif args.cmd == "map":
        cmd_map(args.prefix, args.brand_name, args.dry_run)
    elif args.cmd == "stats":
        cmd_stats()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
