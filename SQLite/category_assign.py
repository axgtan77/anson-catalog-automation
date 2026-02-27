#!/usr/bin/env python3
"""
Category Assignment Script
Anson Supermart Catalog Management

Uses MEDESC dotted prefix codes (e.g. FW., RTW., NC.TOY.) to bulk-assign
categories to uncategorized products.

Commands:
  stats                        Show uncategorized products by dotted prefix
  auto       [--dry-run]       Assign categories using patterns learned from
                               already-categorized products
  map CODE CAT_ID [--dry-run]  Map a dotted prefix code to a category
  add-cat NAME DEPT_ID         Create a new category (prints new ID)
  add-dept NAME                Create a new department (prints new ID)
  list                         List all departments and categories with IDs

Usage examples:
  python category_assign.py list
  python category_assign.py stats
  python category_assign.py auto --dry-run
  python category_assign.py auto
  python category_assign.py add-dept "Fashion & Apparel"
  python category_assign.py add-cat "Ready-to-Wear" 5
  python category_assign.py map RTW 71
  python category_assign.py map NC.TOY 52
"""

import sqlite3
import re
import argparse
import sys
from collections import Counter, defaultdict
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


def get_code(description):
    """
    Extract the dotted category prefix from a MEDESC description.
    e.g. 'FW.WEN #200-42' -> 'FW'
         'NC.TOY.FIRE TRUCK' -> 'NC.TOY'
         'FRUITS.APPLE 100' -> 'FRUITS'
         'APOLLO MURIATIC...' -> None (no dotted prefix)
    """
    desc = (description or "").strip().lstrip("!")
    words = desc.split()
    if not words:
        return None
    first = words[0]
    if "." not in first:
        return None
    parts = first.upper().split(".")
    if len(parts) < 2:
        return None
    return ".".join(parts[:-1])


def compute_quality(description, name, brand_id, category_id, size):
    issues = []
    if not (description or "").strip(): issues.append("NEEDS_DESCRIPTION")
    if not (name or "").strip():         issues.append("NEEDS_NAME")
    if not brand_id:                      issues.append("NEEDS_BRAND")
    if not category_id:                   issues.append("NEEDS_CATEGORY")
    if not (size or "").strip():          issues.append("NEEDS_SIZE")
    return ("COMPLETE", 0) if not issues else (issues[0], 1)


# ---------------------------------------------------------------------------
# Command: list
# ---------------------------------------------------------------------------

def cmd_list():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT d.id as did, d.name as dept, c.id as cid, c.name as cat
        FROM departments d
        LEFT JOIN categories c ON c.department_id = d.id
        ORDER BY d.name, c.name
    """)
    cur_dept = None
    for r in cur.fetchall():
        if r["dept"] != cur_dept:
            print(f"\n  [{r['did']}] {r['dept']}")
            cur_dept = r["dept"]
        if r["cid"] is not None:
            print(f"        ({r['cid']:>3}) {r['cat']}")
    conn.close()


# ---------------------------------------------------------------------------
# Command: stats
# ---------------------------------------------------------------------------

def cmd_stats():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM products WHERE active=1 AND (category_id IS NULL OR category_id=0)")
    total_missing = cur.fetchone()[0]

    cur.execute("""
        SELECT description FROM products
        WHERE active=1 AND (category_id IS NULL OR category_id=0)
          AND description IS NOT NULL AND description != ''
    """)

    codes = Counter()
    no_code = 0
    for r in cur.fetchall():
        code = get_code(r["description"])
        if code:
            codes[code] += 1
        else:
            no_code += 1

    conn.close()

    print(f"Uncategorized (active) : {total_missing:,}")
    print(f"  Have dotted prefix   : {sum(codes.values()):,}")
    print(f"  No dotted prefix     : {no_code:,}")
    print()
    print(f"  {'CODE':<22} {'COUNT':>6}")
    print(f"  {'-'*22} {'-'*6}")
    for code, cnt in codes.most_common(60):
        print(f"  {code:<22} {cnt:>6}")


# ---------------------------------------------------------------------------
# Command: auto
# ---------------------------------------------------------------------------

def cmd_auto(dry_run):
    """
    Build a code -> category_id lookup from already-categorized products,
    then apply it to uncategorized ones. Uses longest-prefix match with
    fallback (e.g. RTW.ANNA -> RTW if RTW.ANNA has no mapping).
    """
    conn = get_db()
    cur = conn.cursor()

    # Build lookup from categorized products
    cur.execute("""
        SELECT description, category_id FROM products
        WHERE active=1
          AND category_id IS NOT NULL AND category_id != 0
          AND description IS NOT NULL AND description != ''
    """)
    code_votes = defaultdict(Counter)
    for r in cur.fetchall():
        code = get_code(r["description"])
        if code:
            code_votes[code][r["category_id"]] += 1

    # Keep only unambiguous mappings (>=80% of votes, >=3 samples)
    lookup = {}
    for code, votes in code_votes.items():
        total = sum(votes.values())
        best_cat, best_cnt = votes.most_common(1)[0]
        if best_cnt / total >= 0.80 and total >= 3:
            lookup[code] = best_cat

    print(f"Category prefix lookup built: {len(lookup):,} codes")

    # Load category names for display
    cur.execute("SELECT id, name FROM categories")
    cat_names = {r["id"]: r["name"] for r in cur.fetchall()}

    # Fetch uncategorized products
    cur.execute("""
        SELECT merkey, description, name, brand_id, size, category_id
        FROM products
        WHERE active=1
          AND (category_id IS NULL OR category_id=0)
          AND description IS NOT NULL AND description != ''
    """)
    products = cur.fetchall()
    total = len(products)

    print(f"Uncategorized products : {total:,}")
    print()

    updated = skipped = 0
    for p in products:
        code = get_code(p["description"])
        if not code:
            skipped += 1
            continue

        # Longest-prefix fallback: try NC.TOY, then NC
        cat_id = None
        parts = code.split(".")
        for length in range(len(parts), 0, -1):
            candidate = ".".join(parts[:length])
            if candidate in lookup:
                cat_id = lookup[candidate]
                break

        if not cat_id:
            skipped += 1
            continue

        dq, ne = compute_quality(
            p["description"], p["name"], p["brand_id"], cat_id, p["size"]
        )
        if dry_run:
            if updated < 20:
                print(f"  [DRY] {p['merkey']} | {p['description'][:40]:<40} -> {cat_names.get(cat_id, '?')}")
        else:
            cur.execute("""
                UPDATE products
                SET category_id=?, data_quality=?, needs_enrichment=?,
                    enrichment_notes='Category auto-assigned via prefix match',
                    updated_at=CURRENT_TIMESTAMP
                WHERE merkey=?
            """, (cat_id, dq, ne, p["merkey"]))
        updated += 1

    if not dry_run:
        conn.commit()
    conn.close()

    print(f"\nAuto-assigned : {updated:,}")
    print(f"No match      : {skipped:,}")
    if dry_run:
        print("DRY RUN — no changes written.")


# ---------------------------------------------------------------------------
# Command: map
# ---------------------------------------------------------------------------

def cmd_map(code, category_id, dry_run):
    code = code.upper()
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT id, name FROM categories WHERE id=?", (category_id,))
    cat = cur.fetchone()
    if not cat:
        print(f"ERROR: category id {category_id} not found. Run 'list' to see valid IDs.")
        conn.close()
        return

    cur.execute("""
        SELECT merkey, description, name, brand_id, size
        FROM products
        WHERE active=1 AND (category_id IS NULL OR category_id=0)
          AND description IS NOT NULL AND description != ''
    """)
    products = cur.fetchall()

    updated = 0
    for p in products:
        pcode = get_code(p["description"])
        if not pcode:
            continue
        # Match if the product code starts with the given code
        if pcode != code and not pcode.startswith(code + "."):
            continue
        dq, ne = compute_quality(
            p["description"], p["name"], p["brand_id"], category_id, p["size"]
        )
        if dry_run:
            if updated < 5:
                print(f"  [DRY] {p['merkey']} | {p['description'][:50]}")
        else:
            cur.execute("""
                UPDATE products
                SET category_id=?, data_quality=?, needs_enrichment=?,
                    enrichment_notes='Category assigned via prefix mapping',
                    updated_at=CURRENT_TIMESTAMP
                WHERE merkey=?
            """, (cat["id"], dq, ne, p["merkey"]))
        updated += 1

    if not dry_run:
        conn.commit()
    conn.close()

    print(f"\nCode '{code}' -> category '{cat['name']}' (id={category_id})")
    print(f"Products updated: {updated:,}")
    if dry_run:
        print("DRY RUN — no changes written.")


# ---------------------------------------------------------------------------
# Command: add-dept
# ---------------------------------------------------------------------------

def cmd_add_dept(name):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM departments WHERE UPPER(name)=UPPER(?)", (name,))
    existing = cur.fetchone()
    if existing:
        print(f"Department already exists: '{name}' (id={existing['id']})")
        conn.close()
        return
    cur.execute("INSERT INTO departments(name, slug) VALUES(?,?)",
                (name, slugify(name)))
    dept_id = cur.lastrowid
    conn.commit()
    conn.close()
    print(f"Created department: '{name}' (id={dept_id})")


# ---------------------------------------------------------------------------
# Command: add-cat
# ---------------------------------------------------------------------------

def cmd_add_cat(name, dept_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM departments WHERE id=?", (dept_id,))
    dept = cur.fetchone()
    if not dept:
        print(f"ERROR: department id {dept_id} not found. Run 'list' to see valid IDs.")
        conn.close()
        return
    cur.execute("SELECT id FROM categories WHERE UPPER(name)=UPPER(?) AND department_id=?",
                (name, dept_id))
    existing = cur.fetchone()
    if existing:
        print(f"Category already exists: '{name}' in '{dept['name']}' (id={existing['id']})")
        conn.close()
        return
    slug = f"{slugify(dept['name'])}-{slugify(name)}"
    cur.execute("INSERT INTO categories(name, department_id, slug) VALUES(?,?,?)",
                (name, dept_id, slug))
    cat_id = cur.lastrowid
    conn.commit()
    conn.close()
    print(f"Created category: '{name}' in '{dept['name']}' (id={cat_id})")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Category assignment tool")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("list")
    sub.add_parser("stats")

    p_auto = sub.add_parser("auto")
    p_auto.add_argument("--dry-run", action="store_true")

    p_map = sub.add_parser("map")
    p_map.add_argument("code",        help="Dotted prefix code, e.g. RTW or NC.TOY")
    p_map.add_argument("category_id", type=int, help="Target category ID")
    p_map.add_argument("--dry-run",   action="store_true")

    p_add_cat = sub.add_parser("add-cat")
    p_add_cat.add_argument("name")
    p_add_cat.add_argument("dept_id", type=int)

    p_add_dept = sub.add_parser("add-dept")
    p_add_dept.add_argument("name")

    args = parser.parse_args()
    print(f"\nStarted: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    if args.cmd == "list":
        cmd_list()
    elif args.cmd == "stats":
        cmd_stats()
    elif args.cmd == "auto":
        cmd_auto(args.dry_run)
    elif args.cmd == "map":
        cmd_map(args.code, args.category_id, args.dry_run)
    elif args.cmd == "add-cat":
        cmd_add_cat(args.name, args.dept_id)
    elif args.cmd == "add-dept":
        cmd_add_dept(args.name)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
