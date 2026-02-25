#!/usr/bin/env python3
"""
Anson Supermart - Import MERCH_MASTER.csv into SQLite

This version matches your MERCH_MASTER header (the one you pasted), which includes:
- MERKEY, MEDESC, BARCD1..BARCD5 (legacy fields)
- AND the enrichment columns at the end: Active, Brand, Name, Description, Size, Filename, Photo,
  Sub-Department, Department, GP%, No. of item per pack, "Weight/⏎Volume", Unit of Measurement

Key fixes vs your old importer:
1) Imports ALL 66,656 rows (no longer "skips" missing Description).
2) Computes data_quality + needs_enrichment in the same way as web_encoder.py.
3) Handles the weird header: "Weight/\\nVolume" (the column name literally contains a newline).
4) Normalizes Department/Sub-Department keys to avoid mismatches.
"""

import csv
import os
import sqlite3

DB_DEFAULT = "anson_products.db"
CSV_DEFAULT = "../Data/MERCH_MASTER.csv"

def slugify(text: str) -> str:
    if not text:
        return ""
    text = text.strip().lower()
    text = text.replace("&", "and")
    for ch in ["/", "\\", ",", ".", "(", ")", "[", "]", "{", "}", ":", ";", "'", '"']:
        text = text.replace(ch, " ")
    return "-".join([p for p in text.split() if p])

def norm_key(s: str) -> str:
    return " ".join((s or "").strip().split()).upper()

def clean_text(s: str) -> str:
    return " ".join((s or "").strip().split())

def truthy_active(s: str) -> bool:
    v = (s or "").strip().upper()
    return v in ("X", "1", "TRUE", "T", "Y", "YES")

def get_weight_volume_field(row: dict) -> str:
    # Exact keys that may appear depending on OS/editor
    for k in ("Weight/Volume", "Weight/\nVolume", "Weight/\r\nVolume"):
        if k in row and row.get(k):
            return clean_text(row.get(k))
    # Fuzzy: any header containing both tokens
    for k in row.keys():
        kk = k.upper()
        if "WEIGHT" in kk and "VOLUME" in kk:
            val = row.get(k)
            if val:
                return clean_text(val)
    return ""

def compute_quality(description: str, name: str, brand_id: int, category_id: int, size: str):
    if not clean_text(description):
        return "NEEDS_DESCRIPTION", 1
    if not clean_text(name):
        return "NEEDS_NAME", 1
    if not (brand_id and brand_id != 0):
        return "NEEDS_BRAND", 1
    if not (category_id and category_id != 0):
        return "NEEDS_CATEGORY", 1
    if not clean_text(size):
        return "NEEDS_SIZE", 1
    return "COMPLETE", 0

def import_merch_master(db_path: str = DB_DEFAULT, csv_path: str = CSV_DEFAULT) -> bool:
    print("=" * 80)
    print("IMPORTING MERCH_MASTER.CSV")
    print("=" * 80)
    print()
    print(f"Database: {db_path}")
    print(f"CSV file:  {csv_path}")
    print()

    if not os.path.exists(csv_path):
        print(f"ERROR: CSV file not found: {os.path.abspath(csv_path)}")
        return False

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO sync_log (sync_type, source_file, status, started_at)
        VALUES ('MERCH_MASTER', ?, 'IN_PROGRESS', CURRENT_TIMESTAMP)
    """, (os.path.basename(csv_path),))
    sync_id = cursor.lastrowid
    conn.commit()

    stats = {
        "departments_added": 0,
        "categories_added": 0,
        "brands_added": 0,
        "products_added": 0,
        "products_updated": 0,
        "images_added": 0,
        "skipped": 0,
        "processed": 0,
    }

    print("Reading CSV file...")
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    print(f"Found {len(rows):,} rows")
    print()

    # -----------------------------
    # Step 1: Departments
    # -----------------------------
    print("=" * 80)
    print("Step 1: Importing Departments")
    print("=" * 80)

    dept_keys = set()
    for row in rows:
        dept = clean_text(row.get("Department", ""))
        if dept:
            dept_keys.add(norm_key(dept))

    departments = {"UNKNOWN": 0}
    cursor.execute("SELECT id, name FROM departments")
    for did, name in cursor.fetchall():
        departments[norm_key(name)] = did

    for dkey in sorted(dept_keys):
        if dkey not in departments:
            display = dkey.title()
            cursor.execute("INSERT INTO departments (name, slug) VALUES (?, ?)", (display, slugify(display)))
            departments[dkey] = cursor.lastrowid
            stats["departments_added"] += 1

    conn.commit()
    print(f"  ✓ Imported {stats['departments_added']} departments")
    print()

    # -----------------------------
    # Step 2: Categories
    # -----------------------------
    print("=" * 80)
    print("Step 2: Importing Categories")
    print("=" * 80)

    categories = {("UNKNOWN", "UNKNOWN"): 0}
    cursor.execute("""
        SELECT c.id, c.name, d.name
        FROM categories c
        LEFT JOIN departments d ON c.department_id = d.id
    """)
    for cid, cname, dname in cursor.fetchall():
        categories[(norm_key(cname), norm_key(dname))] = cid

    cat_pairs = set()
    for row in rows:
        dept = clean_text(row.get("Department", ""))
        cat = clean_text(row.get("Sub-Department", ""))
        if dept and cat:
            cat_pairs.add((norm_key(cat), norm_key(dept)))

    for cat_key, dept_key in sorted(cat_pairs):
        dept_id = departments.get(dept_key, 0)
        if dept_id == 0:
            continue
        if (cat_key, dept_key) not in categories:
            display = cat_key.title()
            cursor.execute(
                "INSERT INTO categories (name, department_id, slug) VALUES (?, ?, ?)",
                (display, dept_id, slugify(f"{dept_key}-{cat_key}")),
            )
            categories[(cat_key, dept_key)] = cursor.lastrowid
            stats["categories_added"] += 1

    conn.commit()
    print(f"  ✓ Imported {stats['categories_added']} categories")
    print()

    # -----------------------------
    # Step 3: Brands
    # -----------------------------
    print("=" * 80)
    print("Step 3: Importing Brands")
    print("=" * 80)

    brands = {"UNKNOWN": 0}
    cursor.execute("SELECT id, name FROM brands")
    for bid, name in cursor.fetchall():
        brands[norm_key(name)] = bid

    brand_keys = set()
    for row in rows:
        b = clean_text(row.get("Brand", ""))
        if b:
            brand_keys.add(norm_key(b))

    for bkey in sorted(brand_keys):
        if bkey not in brands:
            display = bkey.title()
            cursor.execute("INSERT INTO brands (name, slug) VALUES (?, ?)", (display, slugify(display)))
            brands[bkey] = cursor.lastrowid
            stats["brands_added"] += 1

    conn.commit()
    print(f"  ✓ Imported {stats['brands_added']} brands")
    print()

    # -----------------------------
    # Step 4: Products
    # -----------------------------
    print("=" * 80)
    print("Step 4: Importing Products")
    print("=" * 80)
    print()

    for i, row in enumerate(rows, 1):
        merkey = clean_text(row.get("MERKEY", ""))
        if not merkey:
            stats["skipped"] += 1
            continue

        # Prefer enrichment Description; fallback to legacy MEDESC
        description = clean_text(row.get("Description", "")) or clean_text(row.get("MEDESC", ""))
        name = clean_text(row.get("Name", ""))
        size = clean_text(row.get("Size", ""))
        unit = clean_text(row.get("Unit of Measurement", ""))
        weight_volume = get_weight_volume_field(row)

        pack_qty_raw = clean_text(row.get("No. of item per pack", "")) or clean_text(row.get("MEQTY1", ""))
        gp_raw = clean_text(row.get("GP%", ""))

        brand_name = clean_text(row.get("Brand", ""))
        brand_id = brands.get(norm_key(brand_name), 0) if brand_name else 0

        dept_name = clean_text(row.get("Department", ""))
        cat_name = clean_text(row.get("Sub-Department", ""))

        dept_key = norm_key(dept_name)
        cat_key = norm_key(cat_name)

        department_id = departments.get(dept_key, 0) if dept_name else 0
        category_id = categories.get((cat_key, dept_key), 0) if (dept_name and cat_name) else 0

        active = truthy_active(row.get("Active", ""))

        try:
            pack_qty = int(pack_qty_raw) if pack_qty_raw else None
        except:
            pack_qty = None

        try:
            gp_percent = float(gp_raw.replace("%", "")) if gp_raw else None
        except:
            gp_percent = None

        data_quality, needs_enrichment = compute_quality(description, name, brand_id, category_id, size)

        cursor.execute("SELECT 1 FROM products WHERE merkey = ?", (merkey,))
        exists = cursor.fetchone()

        if exists:
            cursor.execute("""
                UPDATE products SET
                    description = ?,
                    name = ?,
                    brand_id = ?,
                    category_id = ?,
                    department_id = ?,
                    size = ?,
                    weight_volume = ?,
                    unit_of_measurement = ?,
                    pack_quantity = ?,
                    gp_percent = ?,
                    active = ?,
                    data_quality = ?,
                    needs_enrichment = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE merkey = ?
            """, (
                description, name, brand_id, category_id, department_id,
                size, weight_volume, unit, pack_qty, gp_percent, int(active),
                data_quality, needs_enrichment, merkey
            ))
            stats["products_updated"] += 1
        else:
            cursor.execute("""
                INSERT INTO products (
                    merkey, description, name, brand_id, category_id, department_id,
                    size, weight_volume, unit_of_measurement, pack_quantity,
                    gp_percent, active, data_quality, needs_enrichment
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                merkey, description, name, brand_id, category_id, department_id,
                size, weight_volume, unit, pack_qty, gp_percent, int(active),
                data_quality, needs_enrichment
            ))
            stats["products_added"] += 1

        # Barcodes from BARCD1..BARCD5
        for barcode_col in ["BARCD1", "BARCD2", "BARCD3", "BARCD4", "BARCD5"]:
            barcode = clean_text(row.get(barcode_col, ""))
            if barcode:
                is_primary = 1 if barcode_col == "BARCD1" else 0
                cursor.execute("""
                    INSERT OR IGNORE INTO barcodes (merkey, barcode, is_primary)
                    VALUES (?, ?, ?)
                """, (merkey, barcode, is_primary))

        # Images from enrichment columns Filename/Photo
        filename = clean_text(row.get("Filename", ""))
        photo_url = clean_text(row.get("Photo", ""))
        if filename or photo_url:
            cursor.execute("""
                INSERT OR IGNORE INTO images (merkey, filename, s3_url, is_primary)
                VALUES (?, ?, ?, 1)
            """, (merkey, filename, photo_url))
            if cursor.rowcount > 0:
                stats["images_added"] += 1

        stats["processed"] += 1

        if i % 1000 == 0:
            conn.commit()
            print(f"  Processed {i:,} / {len(rows):,} rows...", end="\r")

    conn.commit()
    print()
    print(f"  ✓ Processed {stats['processed']:,} products")
    print()

    cursor.execute("""
        UPDATE sync_log SET
            status = 'SUCCESS',
            records_processed = ?,
            records_added = ?,
            records_updated = ?,
            records_skipped = ?,
            completed_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (stats["processed"], stats["products_added"], stats["products_updated"], stats["skipped"], sync_id))
    conn.commit()
    conn.close()

    print("=" * 80)
    print("IMPORT SUMMARY")
    print("=" * 80)
    print(f"  Departments:     {stats['departments_added']:>6,} added")
    print(f"  Categories:      {stats['categories_added']:>6,} added")
    print(f"  Brands:          {stats['brands_added']:>6,} added")
    print(f"  Products added:  {stats['products_added']:>6,}")
    print(f"  Products updated:{stats['products_updated']:>6,}")
    print(f"  Images:          {stats['images_added']:>6,} added")
    print(f"  Skipped:         {stats['skipped']:>6,}")
    print()
    print(f"  Total processed: {stats['processed']:,}")
    print()
    print("=" * 80)
    print("✓ MERCH_MASTER import complete!")
    print("=" * 80)
    print()
    print("Next step: python import_operational_master.py")
    return True

def main():
    import sys
    csv_path = CSV_DEFAULT
    db_path = DB_DEFAULT
    if len(sys.argv) > 1:
        csv_path = sys.argv[1]
    if len(sys.argv) > 2:
        db_path = sys.argv[2]
    ok = import_merch_master(db_path=db_path, csv_path=csv_path)
    raise SystemExit(0 if ok else 1)

if __name__ == "__main__":
    main()
