#!/usr/bin/env python3
"""
Daily MP_MER.FPB Sync Script
Anson Supermart Catalog Management
Syncs prices and product data from FoxPro to SQLite database
"""

import sqlite3
import struct
import os
import shutil
from datetime import datetime
from pathlib import Path

# Configuration
DB_PATH = 'anson_products.db'
SOURCE_FPB = r'D:\Projects\CatalogAutomation\Data\MP_MER.FPB'  # Live file location
TEMP_DIR = Path('../Data/Temp')

# Price field mappings from MP_MER.FPB
# MEWHOP = Mode 1 (Case/Wholesale)
# MERET2 = Mode 2 (Pack)
# MERETP = Mode 3 (Retail/Piece)


class FoxProDBF:
    """
    Simple FoxPro/DBF reader.

    Critical DBF fact:
      - Each record begins with a 1-byte deletion flag.
      - Field bytes start at offset 1 (NOT offset 0).
    """

    def __init__(self, filename: str):
        self.filename = filename
        self.file = open(filename, 'rb')
        self._read_header()
        self._read_field_descriptors()

    def _read_header(self):
        header = self.file.read(32)
        if len(header) != 32:
            raise ValueError("Invalid DBF header (too short).")

        self.num_records = struct.unpack('<I', header[4:8])[0]
        self.header_length = struct.unpack('<H', header[8:10])[0]
        self.record_length = struct.unpack('<H', header[10:12])[0]

    def _read_field_descriptors(self):
        self.fields = []
        self.file.seek(32)

        while True:
            field_data = self.file.read(32)
            if not field_data or len(field_data) < 32:
                raise ValueError("Invalid DBF field descriptor block (unexpected EOF).")

            # 0x0D marks end of field descriptors
            if field_data[0] == 0x0D:
                break

            name = field_data[0:11].decode('latin1', errors='ignore').strip('\x00').strip()
            field_type = chr(field_data[11])
            length = field_data[16]
            decimals = field_data[17]

            self.fields.append({
                'name': name,
                'type': field_type,
                'length': length,
                'decimals': decimals
            })

        # Always trust header_length as the first record offset
        # (record region begins exactly at header_length)
        self.data_start = self.header_length

    def __iter__(self):
        self.file.seek(self.data_start)

        for _ in range(self.num_records):
            record_data = self.file.read(self.record_length)
            if not record_data:
                break
            if len(record_data) < self.record_length:
                # partial read -> stop
                break

            # Deletion flag is first byte
            deleted_flag = record_data[0:1]
            if deleted_flag == b'*':
                continue

            record = {}
            offset = 1  # <-- FIX: skip deletion flag

            for field in self.fields:
                raw = record_data[offset:offset + field['length']]
                offset += field['length']

                # Decode for character fields; numeric/date handled below
                text = raw.decode('latin1', errors='ignore').strip()

                if field['type'] == 'N':
                    if text:
                        try:
                            if field['decimals'] > 0:
                                record[field['name']] = float(text)
                            else:
                                record[field['name']] = int(text)
                        except ValueError:
                            record[field['name']] = None
                    else:
                        record[field['name']] = None

                elif field['type'] == 'D':
                    if text and len(text) == 8:
                        try:
                            record[field['name']] = datetime.strptime(text, '%Y%m%d').date()
                        except ValueError:
                            record[field['name']] = None
                    else:
                        record[field['name']] = None

                else:
                    # Character or other types: keep as string or None
                    record[field['name']] = text if text else None

            yield record

    def close(self):
        self.file.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def sync_prices_from_mp_mer(fpb_path, dry_run=False):
    print("=" * 80)
    print("SYNCING PRICES FROM MP_MER.FPB")
    print("=" * 80)
    print(f"Source file: {fpb_path}")
    print(f"Database: {DB_PATH}")
    print(f"Dry run: {dry_run}")
    print()

    if not os.path.exists(fpb_path):
        raise FileNotFoundError(f"MP_MER.FPB not found at {fpb_path}")

    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(f"Database not found at {DB_PATH}")

    stats = {
        'processed': 0,
        'prices_updated': 0,
        'prices_added': 0,
        'products_activated': 0,
        'products_deactivated': 0,
        'price_changes': 0,
        'skipped': 0,
        'errors': []
    }

    conn = get_db()
    cursor = conn.cursor()

    # Mark any previously stuck IN_PROGRESS entries as FAILED before starting
    cursor.execute("""
        UPDATE sync_log
        SET status = 'FAILED',
            error_message = 'Process was interrupted before completion',
            completed_at = CURRENT_TIMESTAMP
        WHERE sync_type = 'MP_MER_PRICE_SYNC' AND status = 'IN_PROGRESS'
    """)
    if cursor.rowcount:
        print(f"  (Cleaned up {cursor.rowcount} interrupted sync(s) from a previous run)")

    cursor.execute("""
        INSERT INTO sync_log (sync_type, source_file, status, started_at)
        VALUES ('MP_MER_PRICE_SYNC', ?, 'IN_PROGRESS', CURRENT_TIMESTAMP)
    """, (fpb_path,))
    sync_log_id = cursor.lastrowid
    conn.commit()

    try:
        print("Reading MP_MER.FPB...")

        debug_merkeys = []

        with FoxProDBF(fpb_path) as dbf:
            active_merkeys = set()

            for row in dbf:
                stats['processed'] += 1

                if len(debug_merkeys) < 20:
                    debug_merkeys.append(row.get('MERKEY'))

                if stats['processed'] % 5000 == 0:
                    print(f"  Processed {stats['processed']:,} records...")

                merkey = row.get('MERKEY')
                if not merkey:
                    stats['skipped'] += 1
                    continue

                merkey = str(merkey).strip()
                if not merkey:
                    stats['skipped'] += 1
                    continue

                active_merkeys.add(merkey)

                # Extract prices (float/int already parsed for N types)
                mode1_price = row.get('MEWHOP') or 0.0
                mode2_price = row.get('MERET2') or 0.0
                mode3_price = row.get('MERETP') or 0.0
                cost = row.get('MECOS0') or 0.0

                if mode1_price == 0 and mode2_price == 0 and mode3_price == 0:
                    stats['skipped'] += 1
                    continue

                cursor.execute("SELECT merkey FROM products WHERE merkey = ?", (merkey,))
                product_exists = cursor.fetchone() is not None
                if not product_exists:
                    stats['skipped'] += 1
                    continue

                cursor.execute("""
                    SELECT price_case, price_pack, price_retail, cost
                    FROM prices
                    WHERE merkey = ? AND is_current = 1
                """, (merkey,))
                current_price = cursor.fetchone()

                price_changed = False
                if current_price:
                    if (current_price['price_case'] != mode1_price or
                        current_price['price_pack'] != mode2_price or
                        current_price['price_retail'] != mode3_price or
                        current_price['cost'] != cost):
                        price_changed = True
                        stats['price_changes'] += 1

                if not current_price or price_changed:
                    cursor.execute("""
                        INSERT INTO prices (merkey, price_case, price_pack, price_retail, cost, effective_date, is_current)
                        VALUES (?, ?, ?, ?, ?, date('now'), 1)
                    """, (merkey, mode1_price, mode2_price, mode3_price, cost))

                    if current_price:
                        stats['prices_updated'] += 1
                    else:
                        stats['prices_added'] += 1

            print(f"✓ Processed {stats['processed']:,} records from MP_MER.FPB")
            print()
            print("DEBUG - First 20 MERKEYs read from file:")
            for i, mk in enumerate(debug_merkeys, 1):
                print(f"  {i:2}. '{mk}'")
            print()
            print(f"Total unique MERKEYs found: {len(active_merkeys):,}")
            print(f"Sample unique MERKEYs: {list(active_merkeys)[:10]}")
            print()

        print("Updating product active status...")

        cursor.execute("SELECT merkey FROM products WHERE active = 0")
        inactive_products = {row['merkey'] for row in cursor.fetchall()}

        newly_active = active_merkeys & inactive_products
        if newly_active:
            batch_size = 500
            newly_active_list = list(newly_active)
            for i in range(0, len(newly_active_list), batch_size):
                batch = newly_active_list[i:i + batch_size]
                placeholders = ','.join(['?'] * len(batch))
                cursor.execute(f"""
                    UPDATE products
                    SET active = 1, updated_at = CURRENT_TIMESTAMP
                    WHERE merkey IN ({placeholders})
                """, batch)
            stats['products_activated'] = len(newly_active)
            print(f"✓ Activated {stats['products_activated']} products")

        cursor.execute("SELECT merkey FROM products WHERE active = 1")
        all_active = {row['merkey'] for row in cursor.fetchall()}

        newly_inactive = all_active - active_merkeys
        if newly_inactive:
            batch_size = 500
            newly_inactive_list = list(newly_inactive)
            for i in range(0, len(newly_inactive_list), batch_size):
                batch = newly_inactive_list[i:i + batch_size]
                placeholders = ','.join(['?'] * len(batch))
                cursor.execute(f"""
                    UPDATE products
                    SET active = 0, updated_at = CURRENT_TIMESTAMP
                    WHERE merkey IN ({placeholders})
                """, batch)
            stats['products_deactivated'] = len(newly_inactive)
            print(f"✓ Deactivated {stats['products_deactivated']} products")

        print()

        if not dry_run:
            cursor.execute("""
                UPDATE sync_log
                SET status = 'SUCCESS',
                    records_processed = ?,
                    records_updated = ?,
                    records_added = ?,
                    records_skipped = ?,
                    completed_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (stats['processed'], stats['prices_updated'], stats['prices_added'],
                  stats['skipped'], sync_log_id))
            conn.commit()
            print("✓ Changes committed to database")
        else:
            conn.rollback()
            print("✗ Dry run - changes rolled back")

    except Exception as e:
        stats['errors'].append(str(e))
        cursor.execute("""
            UPDATE sync_log
            SET status = 'FAILED',
                error_message = ?,
                completed_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (str(e), sync_log_id))
        conn.commit()
        raise

    finally:
        conn.close()

    return stats


def copy_from_network(source_path, dest_path):
    print(f"Copying from: {source_path}")
    print(f"         to: {dest_path}")

    if not os.path.exists(source_path):
        raise FileNotFoundError(f"Source file not found: {source_path}")

    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    shutil.copy2(source_path, dest_path)
    print(f"✓ File copied successfully ({os.path.getsize(dest_path):,} bytes)")
    return dest_path


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Sync MP_MER.FPB prices to database')
    parser.add_argument('--source', default=SOURCE_FPB, help='Path to MP_MER.FPB file')
    parser.add_argument('--dry-run', action='store_true', help='Test run without committing changes')
    parser.add_argument('--copy', action='store_true', help='Copy from network location first')

    args = parser.parse_args()

    print("=" * 80)
    print("MP_MER.FPB DAILY PRICE SYNC")
    print("=" * 80)
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    try:
        fpb_path = args.source

        if args.copy:
            TEMP_DIR.mkdir(parents=True, exist_ok=True)
            temp_fpb = TEMP_DIR / 'MP_MER.FPB'
            fpb_path = copy_from_network(args.source, str(temp_fpb))
            print()

        stats = sync_prices_from_mp_mer(fpb_path, dry_run=args.dry_run)

        print("=" * 80)
        print("SYNC SUMMARY")
        print("=" * 80)
        print(f"Records processed:     {stats['processed']:,}")
        print(f"Prices updated:        {stats['prices_updated']:,}")
        print(f"Prices added:          {stats['prices_added']:,}")
        print(f"Price changes:         {stats['price_changes']:,}")
        print(f"Products activated:    {stats['products_activated']:,}")
        print(f"Products deactivated:  {stats['products_deactivated']:,}")
        print(f"Skipped:               {stats['skipped']:,}")
        print("=" * 80)

        if args.dry_run:
            print("✓ DRY RUN COMPLETE - No changes committed")
        else:
            print("✓ SYNC COMPLETE")
        print("=" * 80)

        if args.copy and os.path.exists(fpb_path):
            os.remove(fpb_path)
            print(f"✓ Temp file removed: {fpb_path}")

        return 0

    except Exception as e:
        print()
        print("=" * 80)
        print(f"✗ ERROR: {e}")
        print("=" * 80)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())