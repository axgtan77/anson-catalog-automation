#!/usr/bin/env python3
"""
Fix scientific notation barcodes in the barcodes table.

When MERCH_MASTER.csv was originally imported, some barcode values were
stored in scientific notation (e.g. 4.80E+12) due to Excel float formatting.
The CSV was later rebuilt with correct values from MP_MER.FPB.
This script re-reads MERCH_MASTER.csv and replaces any sci-notation entries
in the barcodes table with the correct full barcode strings.
"""

import sqlite3
import csv
from pathlib import Path

DB_PATH   = "anson_products.db"
CSV_PATH  = Path("../Data/MERCH_MASTER.csv")
BARCODE_COLS = ["BARCD1", "BARCD2", "BARCD3", "BARCD4", "BARCD5"]


def is_sci_notation(s: str) -> bool:
    return "E+" in s.upper()


def is_valid_barcode(s: str) -> bool:
    return s.isdigit() and len(s) in (8, 12, 13)


def main():
    print("Reading MERCH_MASTER.csv...")
    csv_barcodes: dict[str, list[str]] = {}  # merkey -> [bc1, bc2, ...]
    with open(CSV_PATH, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            merkey = (row.get("MERKEY") or "").strip()
            if not merkey:
                continue
            bcs = []
            for col in BARCODE_COLS:
                v = (row.get(col) or "").strip()
                if v and is_valid_barcode(v):
                    bcs.append(v)
            if bcs:
                csv_barcodes[merkey] = bcs

    print(f"  {len(csv_barcodes):,} products with valid barcodes in CSV")

    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    cur = conn.cursor()

    # Find all sci-notation barcodes
    cur.execute("SELECT id, merkey, barcode FROM barcodes WHERE barcode LIKE '%.%+%'")
    bad_rows = cur.fetchall()
    print(f"\nSci-notation barcodes in DB : {len(bad_rows):,}")

    fixed = 0
    deleted = 0
    no_csv = 0

    for row in bad_rows:
        merkey = row["merkey"]
        good_bcs = csv_barcodes.get(merkey, [])

        if not good_bcs:
            no_csv += 1
            continue

        # Replace bad barcodes one-for-one with good ones from CSV where possible
        # Strategy: delete the bad row, then insert good barcodes if not already present
        cur.execute("DELETE FROM barcodes WHERE id = ?", (row["id"],))
        deleted += 1

        for bc in good_bcs:
            cur.execute("SELECT id FROM barcodes WHERE merkey=? AND barcode=?", (merkey, bc))
            if not cur.fetchone():
                is_primary = 1 if bc == good_bcs[0] else 0
                cur.execute(
                    "INSERT INTO barcodes(merkey, barcode, is_primary) VALUES(?,?,?)",
                    (merkey, bc, is_primary)
                )
                fixed += 1

    conn.commit()
    conn.close()

    print(f"  Deleted bad entries : {deleted:,}")
    print(f"  Inserted correct    : {fixed:,}")
    print(f"  No CSV match        : {no_csv:,}")
    print("\nDone.")


if __name__ == "__main__":
    main()
