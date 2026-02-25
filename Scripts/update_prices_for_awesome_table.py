#!/usr/bin/env python3
"""
Price Update Script - Anson Supermart
Updates prices from latest MP_MER.FPB files and exports to Awesome Table format

Usage:
    python update_prices_for_awesome_table.py
"""

import struct
import csv
from pathlib import Path
from datetime import datetime


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
                print(f"  Read {i + 1:,} / {num_records:,} records...", end='\r')
        
        print(f"  Read {len(records):,} records (total in file: {num_records:,})")
        
    return records


def merge_product_databases(mp_mer_path, mp_mer2_path=None):
    """Merge MP_MER.FPB and MP_MER2.FPB files"""
    
    print("=" * 80)
    print("READING PRODUCT MASTER FILES")
    print("=" * 80)
    print()
    
    print(f"Reading: {mp_mer_path}")
    records1 = read_dbf_file(mp_mer_path)
    
    # Create dictionary keyed by MERKEY
    product_master = {}
    for record in records1:
        merkey = record.get('MERKEY', '').strip()
        if merkey:
            product_master[merkey] = record
    
    print(f"✓ Loaded {len(product_master):,} products from MP_MER.FPB")
    
    # Merge with MP_MER2 if provided
    if mp_mer2_path:
        print()
        print(f"Reading: {mp_mer2_path}")
        records2 = read_dbf_file(mp_mer2_path)
        
        merged_count = 0
        new_count = 0
        
        for record in records2:
            merkey = record.get('MERKEY', '').strip()
            if merkey:
                if merkey in product_master:
                    # Update existing - use newer USRDAT if available
                    existing_date = product_master[merkey].get('USRDAT', '')
                    new_date = record.get('USRDAT', '')
                    
                    if new_date >= existing_date:
                        product_master[merkey] = record
                        merged_count += 1
                else:
                    product_master[merkey] = record
                    new_count += 1
        
        print(f"✓ Updated {merged_count:,} existing products")
        print(f"✓ Added {new_count:,} new products from MP_MER2.FPB")
    
    print()
    print(f"TOTAL PRODUCTS IN MASTER: {len(product_master):,}")
    print()
    
    return product_master


def export_to_awesome_table_format(product_master, output_csv, image_base_url):
    """Export products in Awesome Table format"""
    
    print("=" * 80)
    print("EXPORTING TO AWESOME TABLE FORMAT")
    print("=" * 80)
    print()
    
    exported_count = 0
    skipped_count = 0
    
    with open(output_csv, 'w', newline='', encoding='utf-8') as f:
        fieldnames = [
            'Brand', 'Name', 'Description', 'Size', 
            'SRP_MODE1', 'SRP_MODE2', 'SRP_MODE3',
            'Photo', 'Cards', 'Search',
            'MERKEY', 'MEDESC', 'PRICE2', 'Barcode'
        ]
        
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        
        for merkey, record in sorted(product_master.items()):
            # Get price
            price = record.get('MERETP', '0').strip()
            try:
                price_float = float(price) if price else 0.0
            except ValueError:
                price_float = 0.0
            
            # Skip if no valid price
            if price_float <= 0:
                skipped_count += 1
                continue
            
            # Get description
            description = record.get('MEDESC', '').strip()
            if not description:
                skipped_count += 1
                continue
            
            # Get barcode
            barcode = record.get('MEAN13', '').strip()
            if not barcode:
                barcode = record.get('BARCD1', '').strip()
            
            # Format barcode with asterisks
            if barcode:
                barcode_formatted = f"*{barcode}*"
            else:
                barcode_formatted = ""
            
            # Build photo URL
            # Create filename from description (simplified)
            photo_filename = description.replace(' ', '-') + '.jpg'
            photo_url = f"{image_base_url}{photo_filename}"
            
            # Export row
            row = {
                'Brand': record.get('MEBRAC', '').strip(),  # Brand code, might need mapping
                'Name': description.split()[0] if description else '',  # First word as name
                'Description': description,
                'Size': '',  # Extract from description if needed
                'SRP_MODE1': price_float,  # Case price (same for now)
                'SRP_MODE2': price_float,  # Pack price (same for now)
                'SRP_MODE3': price_float,  # Per piece price
                'Photo': photo_url,
                'Cards': photo_filename,
                'Search': '',
                'MERKEY': merkey,
                'MEDESC': description,
                'PRICE2': price_float,
                'Barcode': barcode_formatted
            }
            
            writer.writerow(row)
            exported_count += 1
            
            if exported_count % 1000 == 0:
                print(f"  Exported {exported_count:,} products...", end='\r')
    
    print()
    print(f"✓ Exported {exported_count:,} products to {output_csv}")
    print(f"⚠️  Skipped {skipped_count:,} products (no price or description)")
    print()
    
    return exported_count


def main():
    """Main execution"""
    
    print("=" * 80)
    print("ANSON SUPERMART - PRICE UPDATE & AWESOME TABLE EXPORT")
    print("=" * 80)
    print()
    
    # Configuration - UPDATE THESE PATHS
    MP_MER_PATH = "MP_MER.FPB"  # Path to your latest MP_MER.FPB
    MP_MER2_PATH = "MP_MER2.FPB"  # Path to MP_MER2.FPB (or None if not using)
    OUTPUT_CSV = "AWESOME_TABLE_PRODUCTS_UPDATED.csv"
    IMAGE_BASE_URL = "https://s3-ap-southeast-1.amazonaws.com/ansonsupermart.com/images/"
    
    print("Configuration:")
    print(f"  MP_MER file: {MP_MER_PATH}")
    print(f"  MP_MER2 file: {MP_MER2_PATH}")
    print(f"  Output: {OUTPUT_CSV}")
    print(f"  Image base URL: {IMAGE_BASE_URL}")
    print()
    
    # Check if files exist
    if not Path(MP_MER_PATH).exists():
        print(f"✗ Error: {MP_MER_PATH} not found!")
        print("  Please update MP_MER_PATH in the script to point to your file.")
        return
    
    # Step 1: Merge product databases
    product_master = merge_product_databases(MP_MER_PATH, MP_MER2_PATH)
    
    # Step 2: Export to Awesome Table format
    exported = export_to_awesome_table_format(product_master, OUTPUT_CSV, IMAGE_BASE_URL)
    
    # Summary
    print("=" * 80)
    print("COMPLETE!")
    print("=" * 80)
    print()
    print(f"✓ Product master updated with {len(product_master):,} products")
    print(f"✓ Exported {exported:,} products to Awesome Table format")
    print(f"✓ Output file: {OUTPUT_CSV}")
    print()
    print("NEXT STEPS:")
    print("1. Open the CSV file and review prices")
    print("2. Import to your Google Sheets")
    print("3. Awesome Table will automatically refresh")
    print()


if __name__ == '__main__':
    main()
