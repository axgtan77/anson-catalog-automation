#!/usr/bin/env python3
"""
Anson Supermart - Operational_Master_Active_24M.csv Import
Imports sales metrics and performance data

Usage:
    python import_operational_master.py
"""

import sqlite3
import csv
import os
from datetime import datetime


def parse_date(date_str):
    """Parse date from various formats"""
    if not date_str:
        return None
    
    try:
        # Try YYYY-MM-DD format first
        return datetime.strptime(date_str, '%Y-%m-%d').date()
    except:
        pass
    
    try:
        # Try MM/DD/YYYY format
        return datetime.strptime(date_str, '%m/%d/%Y').date()
    except:
        pass
    
    return None


def import_operational_master(db_path='anson_products.db', 
                              csv_path='../Data/Operational_Master_Active_24M.csv'):
    """Import Operational Master CSV into database"""
    
    print("=" * 80)
    print("IMPORTING OPERATIONAL_MASTER_ACTIVE_24M.CSV")
    print("=" * 80)
    print()
    
    # Check files exist
    if not os.path.exists(db_path):
        print(f"✗ Error: Database '{db_path}' not found!")
        return False
    
    if not os.path.exists(csv_path):
        print(f"✗ Error: CSV file '{csv_path}' not found!")
        return False
    
    print(f"Database: {db_path}")
    print(f"CSV file: {csv_path}")
    print()
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Start sync log
        cursor.execute("""
            INSERT INTO sync_log (sync_type, source_file, status, started_at)
            VALUES ('OPERATIONAL_MASTER', ?, 'IN_PROGRESS', ?)
        """, (csv_path, datetime.now()))
        sync_id = cursor.lastrowid
        conn.commit()
        
        # Read CSV
        print("Reading CSV file...")
        with open(csv_path, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        
        print(f"Found {len(rows):,} rows")
        print()
        
        # Track stats
        stats = {
            'processed': 0,
            'added': 0,
            'updated': 0,
            'skipped': 0,
            'barcodes_added': 0
        }
        
        print("=" * 80)
        print("Importing Sales Metrics")
        print("=" * 80)
        print()
        
        for i, row in enumerate(rows, 1):
            merkey = row.get('MERKEY', '').strip()
            
            if not merkey:
                stats['skipped'] += 1
                continue
            
            # Parse data
            last_sale_date = parse_date(row.get('Last_Sale_Date', ''))
            
            try:
                txn_count_24m = int(row.get('Txn_Count_24M', 0))
            except:
                txn_count_24m = 0
            
            try:
                qty_sum_24m = float(row.get('Qty_Sum_24M', 0))
            except:
                qty_sum_24m = 0.0
            
            active_24m = row.get('Active_24M', '').strip().lower() == 'true'
            priority = row.get('Priority', '').strip()
            
            # Calculate velocity score (transactions per day over 24 months)
            if txn_count_24m > 0:
                velocity_score = txn_count_24m / 730  # 24 months ≈ 730 days
            else:
                velocity_score = 0.0
            
            # Check if sales_metrics exists
            cursor.execute("SELECT merkey FROM sales_metrics WHERE merkey = ?", (merkey,))
            exists = cursor.fetchone()
            
            if exists:
                # Update
                cursor.execute("""
                    UPDATE sales_metrics SET
                        last_sale_date = ?,
                        txn_count_24m = ?,
                        qty_sum_24m = ?,
                        active_24m = ?,
                        priority = ?,
                        velocity_score = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE merkey = ?
                """, (last_sale_date, txn_count_24m, qty_sum_24m, 
                      active_24m, priority, velocity_score, merkey))
                
                stats['updated'] += 1
            else:
                # Insert
                cursor.execute("""
                    INSERT INTO sales_metrics (
                        merkey, last_sale_date, txn_count_24m, qty_sum_24m,
                        active_24m, priority, velocity_score
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (merkey, last_sale_date, txn_count_24m, qty_sum_24m,
                      active_24m, priority, velocity_score))
                
                stats['added'] += 1
            
            # Import additional barcodes if not already in database
            for barcode_col in ['BARCD1', 'BARCD2', 'BARCD3', 'BARCD4', 'BARCD5', 
                               'DEFAULT_BARCODE']:
                barcode = row.get(barcode_col, '').strip()
                if barcode:
                    cursor.execute("""
                        INSERT OR IGNORE INTO barcodes (merkey, barcode, is_primary)
                        VALUES (?, ?, ?)
                    """, (merkey, barcode, barcode_col == 'DEFAULT_BARCODE'))
                    
                    if cursor.rowcount > 0:
                        stats['barcodes_added'] += 1
            
            stats['processed'] += 1
            
            if i % 1000 == 0:
                conn.commit()
                print(f"  Processed {i:,} / {len(rows):,} rows...", end='\r')
        
        conn.commit()
        print()
        print(f"  ✓ Processed {stats['processed']:,} sales metrics")
        print()
        
        # Calculate rankings
        print("=" * 80)
        print("Calculating Rankings")
        print("=" * 80)
        print()
        
        print("  Computing overall rankings...")
        cursor.execute("""
            WITH ranked AS (
                SELECT 
                    merkey,
                    ROW_NUMBER() OVER (ORDER BY velocity_score DESC) as rank
                FROM sales_metrics
                WHERE velocity_score > 0
            )
            UPDATE sales_metrics
            SET rank_overall = (
                SELECT rank FROM ranked WHERE ranked.merkey = sales_metrics.merkey
            )
        """)
        
        print("  Computing category rankings...")
        cursor.execute("""
            WITH ranked AS (
                SELECT 
                    s.merkey,
                    ROW_NUMBER() OVER (
                        PARTITION BY p.category_id 
                        ORDER BY s.velocity_score DESC
                    ) as rank
                FROM sales_metrics s
                JOIN products p ON s.merkey = p.merkey
                WHERE s.velocity_score > 0
            )
            UPDATE sales_metrics
            SET rank_category = (
                SELECT rank FROM ranked WHERE ranked.merkey = sales_metrics.merkey
            )
        """)
        
        conn.commit()
        print("  ✓ Rankings calculated")
        print()
        
        # Update sync log
        cursor.execute("""
            UPDATE sync_log SET
                status = 'SUCCESS',
                records_processed = ?,
                records_added = ?,
                records_updated = ?,
                records_skipped = ?
            WHERE id = ?
        """, (stats['processed'], stats['added'], 
              stats['updated'], stats['skipped'], sync_id))
        
        conn.commit()
        
        # Print summary
        print("=" * 80)
        print("IMPORT SUMMARY")
        print("=" * 80)
        print(f"  Sales metrics added:   {stats['added']:>6,}")
        print(f"  Sales metrics updated: {stats['updated']:>6,}")
        print(f"  Barcodes added:        {stats['barcodes_added']:>6,}")
        print(f"  Skipped:               {stats['skipped']:>6,}")
        print()
        print(f"  Total processed:       {stats['processed']:,}")
        print()
        
        # Get some statistics
        cursor.execute("""
            SELECT 
                COUNT(*) as total,
                COUNT(CASE WHEN active_24m = 1 THEN 1 END) as active,
                AVG(velocity_score) as avg_velocity,
                MAX(velocity_score) as max_velocity
            FROM sales_metrics
        """)
        
        total, active, avg_vel, max_vel = cursor.fetchone()
        
        print("Database Statistics:")
        print(f"  Total products with metrics: {total:,}")
        print(f"  Active in last 24 months:    {active:,}")
        print(f"  Average velocity score:      {avg_vel:.2f}")
        print(f"  Top velocity score:          {max_vel:.2f}")
        print()
        
        conn.close()
        
        print("=" * 80)
        print("✓ Operational Master import complete!")
        print("=" * 80)
        print()
        
        return True
        
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()
        
        # Update sync log with error
        try:
            cursor.execute("""
                UPDATE sync_log SET
                    status = 'FAILED',
                    error_message = ?
                WHERE id = ?
            """, (str(e), sync_id))
            conn.commit()
        except:
            pass
        
        return False


def main():
    """Main execution"""
    
    # Default paths
    db_path = 'anson_products.db'
    csv_path = '../Data/Operational_Master_Active_24M.csv'
    
    # Check if custom paths provided
    import sys
    if len(sys.argv) > 1:
        csv_path = sys.argv[1]
    if len(sys.argv) > 2:
        db_path = sys.argv[2]
    
    success = import_operational_master(db_path, csv_path)
    
    if success:
        print("Next step: python sync_mp_mer.py (to sync current prices)")
    else:
        print("Import failed. Check errors above.")


if __name__ == '__main__':
    main()
