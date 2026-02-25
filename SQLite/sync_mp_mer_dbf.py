#!/usr/bin/env python3
"""
Daily MP_MER.FPB Sync Script - Using DBF library
Anson Supermart Catalog Management
"""

import sqlite3
import os
from datetime import datetime

# Configuration
DB_PATH = 'anson_products.db'
SOURCE_FPB = r'D:\Projects\CatalogAutomation\Data\MP_MER.FPB'

def get_db():
    """Get database connection"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def sync_prices_from_mp_mer(fpb_path, dry_run=False):
    """Sync prices from MP_MER.FPB using dbf library"""
    print("=" * 80)
    print("SYNCING PRICES FROM MP_MER.FPB")
    print("=" * 80)
    print(f"Source file: {fpb_path}")
    print(f"Database: {DB_PATH}")
    print(f"Dry run: {dry_run}")
    print()
    
    # Install dbf library if needed
    try:
        from dbf import Table
    except ImportError:
        print("Installing dbf library...")
        import subprocess
        subprocess.check_call(['pip', 'install', 'dbf', '--break-system-packages'])
        from dbf import Table
    
    if not os.path.exists(fpb_path):
        raise FileNotFoundError(f"MP_MER.FPB not found at {fpb_path}")
    
    stats = {
        'processed': 0,
        'prices_updated': 0,
        'prices_added': 0,
        'price_changes': 0,
        'skipped': 0
    }
    
    conn = get_db()
    cursor = conn.cursor()
    
    # Start sync log
    cursor.execute("""
        INSERT INTO sync_log (sync_type, source_file, status, started_at)
        VALUES ('MP_MER_PRICE_SYNC', ?, 'IN_PROGRESS', CURRENT_TIMESTAMP)
    """, (fpb_path,))
    sync_log_id = cursor.lastrowid
    conn.commit()
    
    try:
        print("Opening MP_MER.FPB with dbf library...")
        
        # Open FoxPro table
        table = Table(fpb_path)
        table.open()
        
        print(f"Total records: {len(table):,}")
        print()
        
        # Debug: show first 10 MERKEYs
        print("DEBUG - First 10 MERKEYs from file:")
        for i, record in enumerate(table):
            if i >= 10:
                break
            merkey = record.merkey.strip() if record.merkey else None
            print(f"  {i+1}. '{merkey}'")
        
        print()
        print("Processing records...")
        
        active_merkeys = set()
        
        for record in table:
            stats['processed'] += 1
            
            if stats['processed'] % 5000 == 0:
                print(f"  Processed {stats['processed']:,} records...")
            
            # Get MERKEY
            merkey = record.merkey.strip() if record.merkey else None
            if not merkey:
                stats['skipped'] += 1
                continue
            
            active_merkeys.add(merkey)
            
            # Get prices
            mode1_price = float(record.mewhop) if record.mewhop else 0.0
            mode2_price = float(record.meret2) if record.meret2 else 0.0
            mode3_price = float(record.meretp) if record.meretp else 0.0
            cost = float(record.mecos0) if record.mecos0 else 0.0
            
            # Skip if no prices
            if mode1_price == 0 and mode2_price == 0 and mode3_price == 0:
                stats['skipped'] += 1
                continue
            
            # Check if product exists in database
            cursor.execute("SELECT merkey FROM products WHERE merkey = ?", (merkey,))
            if not cursor.fetchone():
                stats['skipped'] += 1
                continue
            
            # Get current price
            cursor.execute("""
                SELECT price_case, price_pack, price_retail, cost
                FROM prices
                WHERE merkey = ? AND is_current = 1
            """, (merkey,))
            
            current_price = cursor.fetchone()
            
            # Check if price changed
            price_changed = False
            if current_price:
                if (current_price['price_case'] != mode1_price or
                    current_price['price_pack'] != mode2_price or
                    current_price['price_retail'] != mode3_price or
                    current_price['cost'] != cost):
                    price_changed = True
                    stats['price_changes'] += 1
            
            # Insert new price record
            if not current_price or price_changed:
                cursor.execute("""
                    INSERT INTO prices (merkey, price_case, price_pack, price_retail, cost, effective_date, is_current)
                    VALUES (?, ?, ?, ?, ?, date('now'), 1)
                """, (merkey, mode1_price, mode2_price, mode3_price, cost))
                
                if current_price:
                    stats['prices_updated'] += 1
                else:
                    stats['prices_added'] += 1
        
        table.close()
        
        print(f"✓ Processed {stats['processed']:,} records")
        print(f"  Found {len(active_merkeys):,} unique MERKEYs")
        print()
        
        # Update sync log
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
        stats['errors'] = [str(e)]
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

def main():
    """Main sync process"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Sync MP_MER.FPB prices to database')
    parser.add_argument('--source', default=SOURCE_FPB, help='Path to MP_MER.FPB file')
    parser.add_argument('--dry-run', action='store_true', help='Test run without committing changes')
    
    args = parser.parse_args()
    
    print("=" * 80)
    print("MP_MER.FPB DAILY PRICE SYNC")
    print("=" * 80)
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    try:
        stats = sync_prices_from_mp_mer(args.source, dry_run=args.dry_run)
        
        # Print summary
        print()
        print("=" * 80)
        print("SYNC SUMMARY")
        print("=" * 80)
        print(f"Records processed:     {stats['processed']:,}")
        print(f"Prices updated:        {stats['prices_updated']:,}")
        print(f"Prices added:          {stats['prices_added']:,}")
        print(f"Price changes:         {stats['price_changes']:,}")
        print(f"Skipped:               {stats['skipped']:,}")
        print("=" * 80)
        
        if args.dry_run:
            print("✓ DRY RUN COMPLETE - No changes committed")
        else:
            print("✓ SYNC COMPLETE")
        
        print("=" * 80)
        
        return 0
        
    except Exception as e:
        print()
        print("=" * 80)
        print(f"✗ ERROR: {e}")
        print("=" * 80)
        import traceback
        traceback.print_exc()
        return 1

if __name__ == '__main__':
    exit(main())