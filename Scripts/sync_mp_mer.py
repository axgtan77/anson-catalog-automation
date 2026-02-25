#!/usr/bin/env python3
"""
Anson Supermart - MP_MER.FPB Sync with Change Detection
Syncs prices, detects new products, tracks MEDESC changes

Usage:
    python sync_mp_mer.py [path/to/MP_MER.FPB]
    
Features:
- Detects new products
- Tracks price changes (with history)
- Flags MEDESC changes (possible size/property changes)
- Updates barcodes
- Logs all changes for review
"""

import struct
import sqlite3
import os
import sys
from pathlib import Path
from datetime import datetime, date
from typing import Dict, List, Tuple


def read_dbf_file(filepath):
    """Read FoxPro DBF file and return records"""
    
    with open(filepath, 'rb') as f:
        # Read header
        header = f.read(32)
        num_records = struct.unpack('<I', header[4:8])[0]
        header_length = struct.unpack('<H', header[8:10])[0]
        record_length = struct.unpack('<H', header[10:12])[0]
        
        # Read field descriptors
        f.seek(32)
        fields = []
        field_positions = {}
        current_pos = 1
        
        while True:
            field_desc = f.read(32)
            if field_desc[0] == 0x0D:
                break
            
            field_name = field_desc[0:11].decode('ascii').strip('\x00')
            field_type = chr(field_desc[11])
            field_length = field_desc[16]
            
            field_positions[field_name] = (current_pos, field_length, field_type)
            fields.append({'name': field_name, 'type': field_type, 'length': field_length})
            current_pos += field_length
        
        # Read all records
        f.seek(header_length)
        records = []
        
        for i in range(num_records):
            record_data = f.read(record_length)
            
            if not record_data or len(record_data) < record_length:
                break
            
            if record_data[0] == 0x2A:  # Deleted record
                continue
            
            record = {}
            for field_name, (pos, length, ftype) in field_positions.items():
                field_data = record_data[pos:pos + length]
                
                if ftype == 'C':
                    value = field_data.decode('latin-1', errors='ignore').strip()
                elif ftype == 'N':
                    value = field_data.decode('ascii', errors='ignore').strip()
                elif ftype == 'D':
                    value = field_data.decode('ascii', errors='ignore').strip()
                else:
                    value = field_data.decode('latin-1', errors='ignore').strip()
                
                record[field_name] = value
            
            records.append(record)
            
            if (i + 1) % 5000 == 0:
                print(f"  Reading {i + 1:,} / {num_records:,} records...", end='\r')
        
        print(f"  Read {len(records):,} records                    ")
        
    return records


def parse_float(value):
    """Parse float safely"""
    try:
        return float(value) if value else 0.0
    except:
        return 0.0


def parse_int(value):
    """Parse int safely"""
    try:
        return int(value) if value else 0
    except:
        return 0


def clean_text(text):
    """Clean and normalize text"""
    return ' '.join((text or '').strip().split())


def sync_mp_mer(db_path='anson_products.db', mp_mer_path=None, mp_mer2_path=None):
    """Sync from MP_MER.FPB with comprehensive change detection"""
    
    print("=" * 80)
    print("MP_MER.FPB SYNC - CHANGE DETECTION")
    print("=" * 80)
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    if not mp_mer_path:
        print("❌ Error: MP_MER.FPB path required")
        print("Usage: python sync_mp_mer.py path/to/MP_MER.FPB")
        return False
    
    if not os.path.exists(mp_mer_path):
        print(f"❌ Error: File not found: {mp_mer_path}")
        return False
    
    print(f"Database: {db_path}")
    print(f"MP_MER:   {mp_mer_path}")
    if mp_mer2_path:
        print(f"MP_MER2:  {mp_mer2_path}")
    print()
    
    # Connect to database
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    cursor = conn.cursor()
    
    # Start sync log
    cursor.execute("""
        INSERT INTO sync_log (sync_type, source_file, status, started_at)
        VALUES ('MP_MER_SYNC', ?, 'IN_PROGRESS', CURRENT_TIMESTAMP)
    """, (os.path.basename(mp_mer_path),))
    sync_id = cursor.lastrowid
    conn.commit()
    
    # Track changes
    changes = {
        'new_products': [],
        'price_changes': [],
        'medesc_changes': [],
        'barcode_changes': [],
        'status_changes': [],
        'products_processed': 0,
        'prices_updated': 0,
        'barcodes_added': 0,
    }
    
    try:
        # Read MP_MER
        print("=" * 80)
        print("READING MP_MER.FPB")
        print("=" * 80)
        records = read_dbf_file(mp_mer_path)
        
        # Read MP_MER2 if provided
        if mp_mer2_path and os.path.exists(mp_mer2_path):
            print()
            print("=" * 80)
            print("READING MP_MER2.FPB")
            print("=" * 80)
            records2 = read_dbf_file(mp_mer2_path)
            
            # Merge, preferring newer data
            merged = {}
            for rec in records:
                merkey = rec.get('MERKEY', '').strip()
                if merkey:
                    merged[merkey] = rec
            
            for rec in records2:
                merkey = rec.get('MERKEY', '').strip()
                if merkey:
                    # Prefer newer USRDAT
                    if merkey in merged:
                        existing_date = merged[merkey].get('USRDAT', '')
                        new_date = rec.get('USRDAT', '')
                        if new_date >= existing_date:
                            merged[merkey] = rec
                    else:
                        merged[merkey] = rec
            
            records = list(merged.values())
            print(f"  Merged total: {len(records):,} unique products")
        
        print()
        print("=" * 80)
        print("DETECTING CHANGES")
        print("=" * 80)
        print()
        
        # Get existing products from database
        cursor.execute("""
            SELECT merkey, description, active
            FROM products
        """)
        existing_products = {row[0]: {'description': row[1], 'active': row[2]} 
                           for row in cursor.fetchall()}
        
        # Get current prices
        cursor.execute("""
            SELECT merkey, price_retail
            FROM prices
            WHERE is_current = 1
        """)
        current_prices = {row[0]: row[1] for row in cursor.fetchall()}
        
        # Process each record from MP_MER
        for i, record in enumerate(records, 1):
            merkey = record.get('MERKEY', '').strip()
            if not merkey:
                continue
            
            medesc = clean_text(record.get('MEDESC', ''))
            if not medesc:
                continue
            
            # Get price
            price_raw = record.get('MERETP', '').strip()
            price = parse_float(price_raw)
            
            if price <= 0:
                continue  # Skip products with no price
            
            # Get barcodes
            barcodes = []
            for bc_field in ['MEAN13', 'BARCD1', 'BARCD2', 'BARCD3']:
                bc = record.get(bc_field, '').strip()
                if bc:
                    barcodes.append(bc)
            
            # Check if product exists
            if merkey not in existing_products:
                # NEW PRODUCT
                changes['new_products'].append({
                    'merkey': merkey,
                    'medesc': medesc,
                    'price': price
                })
                
                # Insert new product
                cursor.execute("""
                    INSERT INTO products (
                        merkey, description, data_quality, needs_enrichment, active
                    ) VALUES (?, ?, 'NEEDS_DESCRIPTION', 1, 1)
                """, (merkey, medesc))
                
                # Insert price
                cursor.execute("""
                    INSERT INTO prices (
                        merkey, price_retail, effective_date, is_current
                    ) VALUES (?, ?, date('now'), 1)
                """, (merkey, price))
                
                changes['prices_updated'] += 1
                
            else:
                # EXISTING PRODUCT - Check for changes
                
                # 1. Check MEDESC change
                old_desc = existing_products[merkey]['description']
                if medesc != old_desc:
                    changes['medesc_changes'].append({
                        'merkey': merkey,
                        'old_medesc': old_desc,
                        'new_medesc': medesc,
                        'reason': 'Possible size/property change'
                    })
                    
                    # Update description and flag for review
                    cursor.execute("""
                        UPDATE products SET
                            description = ?,
                            needs_enrichment = 1,
                            data_quality = 'NEEDS_REVIEW',
                            enrichment_notes = 'MEDESC changed - review for size/property changes',
                            updated_at = CURRENT_TIMESTAMP
                        WHERE merkey = ?
                    """, (medesc, merkey))
                
                # 2. Check price change
                if merkey in current_prices:
                    old_price = current_prices[merkey]
                    if abs(price - old_price) > 0.01:  # Price changed
                        price_diff = price - old_price
                        price_pct = (price_diff / old_price * 100) if old_price > 0 else 0
                        
                        changes['price_changes'].append({
                            'merkey': merkey,
                            'medesc': medesc,
                            'old_price': old_price,
                            'new_price': price,
                            'diff': price_diff,
                            'pct_change': price_pct
                        })
                        
                        # Mark old price as not current
                        cursor.execute("""
                            UPDATE prices SET is_current = 0
                            WHERE merkey = ? AND is_current = 1
                        """, (merkey,))
                        
                        # Insert new price
                        cursor.execute("""
                            INSERT INTO prices (
                                merkey, price_retail, effective_date, is_current
                            ) VALUES (?, ?, date('now'), 1)
                        """, (merkey, price))
                        
                        changes['prices_updated'] += 1
                else:
                    # No price record yet - add one
                    cursor.execute("""
                        INSERT INTO prices (
                            merkey, price_retail, effective_date, is_current
                        ) VALUES (?, ?, date('now'), 1)
                    """, (merkey, price))
                    changes['prices_updated'] += 1
            
            # 3. Update/add barcodes
            if barcodes:
                # Get existing barcodes
                cursor.execute("""
                    SELECT barcode FROM barcodes WHERE merkey = ?
                """, (merkey,))
                existing_barcodes = {row[0] for row in cursor.fetchall()}
                
                for idx, barcode in enumerate(barcodes):
                    if barcode not in existing_barcodes:
                        is_primary = 1 if idx == 0 else 0
                        cursor.execute("""
                            INSERT OR IGNORE INTO barcodes (
                                merkey, barcode, is_primary
                            ) VALUES (?, ?, ?)
                        """, (merkey, barcode, is_primary))
                        
                        if cursor.rowcount > 0:
                            changes['barcodes_added'] += 1
                            changes['barcode_changes'].append({
                                'merkey': merkey,
                                'barcode': barcode
                            })
            
            changes['products_processed'] += 1
            
            if i % 1000 == 0:
                conn.commit()
                print(f"  Processed {i:,} / {len(records):,} products...", end='\r')
        
        conn.commit()
        print()
        print(f"  ✓ Processed {changes['products_processed']:,} products")
        print()
        
        # Update sync log
        cursor.execute("""
            UPDATE sync_log SET
                status = 'SUCCESS',
                records_processed = ?,
                records_updated = ?,
                records_added = ?,
                completed_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (changes['products_processed'], changes['prices_updated'], 
              len(changes['new_products']), sync_id))
        
        conn.commit()
        
        # Print detailed change report
        print("=" * 80)
        print("CHANGE DETECTION REPORT")
        print("=" * 80)
        print()
        
        print(f"Products processed: {changes['products_processed']:,}")
        print()
        
        # New products
        if changes['new_products']:
            print(f"🆕 NEW PRODUCTS: {len(changes['new_products']):,}")
            print()
            for prod in changes['new_products'][:20]:  # Show first 20
                print(f"  + {prod['merkey']:10s} {prod['medesc'][:60]:60s} ₱{prod['price']:>8.2f}")
            if len(changes['new_products']) > 20:
                print(f"  ... and {len(changes['new_products']) - 20} more")
            print()
        else:
            print("✓ No new products")
            print()
        
        # Price changes
        if changes['price_changes']:
            print(f"💰 PRICE CHANGES: {len(changes['price_changes']):,}")
            print()
            for change in sorted(changes['price_changes'], 
                                key=lambda x: abs(x['pct_change']), 
                                reverse=True)[:20]:
                direction = "↑" if change['diff'] > 0 else "↓"
                print(f"  {direction} {change['merkey']:10s} {change['medesc'][:50]:50s}")
                print(f"     ₱{change['old_price']:>8.2f} → ₱{change['new_price']:>8.2f} "
                      f"({change['pct_change']:>+6.1f}%)")
            if len(changes['price_changes']) > 20:
                print(f"  ... and {len(changes['price_changes']) - 20} more")
            print()
        else:
            print("✓ No price changes")
            print()
        
        # MEDESC changes (IMPORTANT!)
        if changes['medesc_changes']:
            print(f"⚠️  MEDESC CHANGES: {len(changes['medesc_changes']):,}")
            print("    (Possible size/property changes - REVIEW REQUIRED)")
            print()
            for change in changes['medesc_changes'][:20]:
                print(f"  ! {change['merkey']:10s}")
                print(f"     OLD: {change['old_medesc']}")
                print(f"     NEW: {change['new_medesc']}")
                print()
            if len(changes['medesc_changes']) > 20:
                print(f"  ... and {len(changes['medesc_changes']) - 20} more")
            print()
        else:
            print("✓ No MEDESC changes")
            print()
        
        # Barcode changes
        if changes['barcode_changes']:
            print(f"📊 NEW BARCODES: {len(changes['barcode_changes']):,}")
        else:
            print("✓ No new barcodes")
        
        print()
        
        # Save detailed change log
        log_file = f"sync_changes_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(log_file, 'w', encoding='utf-8') as f:
            f.write("=" * 80 + "\n")
            f.write("MP_MER SYNC - DETAILED CHANGE LOG\n")
            f.write("=" * 80 + "\n")
            f.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Source: {mp_mer_path}\n")
            f.write("\n")
            
            f.write("NEW PRODUCTS\n")
            f.write("-" * 80 + "\n")
            for prod in changes['new_products']:
                f.write(f"{prod['merkey']}\t{prod['medesc']}\t₱{prod['price']:.2f}\n")
            f.write("\n")
            
            f.write("PRICE CHANGES\n")
            f.write("-" * 80 + "\n")
            for change in changes['price_changes']:
                f.write(f"{change['merkey']}\t{change['medesc']}\t"
                       f"₱{change['old_price']:.2f} → ₱{change['new_price']:.2f}\t"
                       f"{change['pct_change']:+.1f}%\n")
            f.write("\n")
            
            f.write("MEDESC CHANGES (REVIEW REQUIRED)\n")
            f.write("-" * 80 + "\n")
            for change in changes['medesc_changes']:
                f.write(f"{change['merkey']}\n")
                f.write(f"  OLD: {change['old_medesc']}\n")
                f.write(f"  NEW: {change['new_medesc']}\n")
                f.write("\n")
        
        print(f"✓ Detailed log saved: {log_file}")
        print()
        
        conn.close()
        
        print("=" * 80)
        print("SYNC COMPLETE")
        print("=" * 80)
        print(f"Completed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print()
        
        return True
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        
        cursor.execute("""
            UPDATE sync_log SET
                status = 'FAILED',
                error_message = ?,
                completed_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (str(e), sync_id))
        conn.commit()
        conn.close()
        
        return False


def main():
    """Main execution"""
    
    if len(sys.argv) < 2:
        print("Usage: python sync_mp_mer.py <path/to/MP_MER.FPB> [path/to/MP_MER2.FPB]")
        print()
        print("Example:")
        print("  python sync_mp_mer.py \\\\server\\share\\MP_MER.FPB")
        print("  python sync_mp_mer.py C:\\Data\\MP_MER.FPB C:\\Data\\MP_MER2.FPB")
        sys.exit(1)
    
    mp_mer_path = sys.argv[1]
    mp_mer2_path = sys.argv[2] if len(sys.argv) > 2 else None
    
    db_path = 'anson_products.db'
    
    success = sync_mp_mer(db_path, mp_mer_path, mp_mer2_path)
    
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
