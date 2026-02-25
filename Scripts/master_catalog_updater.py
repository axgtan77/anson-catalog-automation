#!/usr/bin/env python3
"""
Master Catalog Updater - Processes both MP_MER.FPB and MP_MER2.FPB
Combines active products from both files and generates unified catalog
"""

import subprocess
import csv
from pathlib import Path
from datetime import datetime


def process_fpb_file(input_file: str, output_file: str, years: int = 5):
    """Process a single FPB file"""
    print(f"\nProcessing {Path(input_file).name}...")
    print(f"-" * 100)
    
    cmd = [
        'python3',
        'generate_active_catalog.py',
        input_file,
        '-o', output_file,
        '-y', str(years)
    ]
    
    result = subprocess.run(cmd, capture_output=False)
    
    if result.returncode != 0:
        print(f"✗ Error processing {input_file}")
        return False
    
    return True


def merge_catalogs(file1: str, file2: str, output_file: str):
    """Merge two catalog CSV files, removing duplicates based on MERKEY"""
    print(f"\nMerging catalogs...")
    print(f"-" * 100)
    
    products = {}  # Use dict to deduplicate by MERKEY
    
    # Read both files
    for input_file in [file1, file2]:
        if not Path(input_file).exists():
            print(f"Warning: {input_file} not found, skipping...")
            continue
        
        with open(input_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                merkey = row.get('MERKEY', '')
                if merkey:
                    # Keep the most recent entry (based on USRDAT)
                    if merkey not in products:
                        products[merkey] = row
                    else:
                        # Compare USRDAT and keep newer
                        existing_date = products[merkey].get('USRDAT', '')
                        new_date = row.get('USRDAT', '')
                        if new_date > existing_date:
                            products[merkey] = row
    
    # Write merged catalog
    if products:
        # Get fieldnames from first product
        fieldnames = list(next(iter(products.values())).keys())
        
        with open(output_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            
            # Sort by MERKEY for consistent output
            for merkey in sorted(products.keys()):
                writer.writerow(products[merkey])
        
        print(f"✓ Merged {len(products):,} unique products into {output_file}")
        return len(products)
    else:
        print("✗ No products to merge!")
        return 0


def generate_report(merged_file: str):
    """Generate a summary report of the catalog"""
    print(f"\nGenerating summary report...")
    print(f"-" * 100)
    
    with open(merged_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        products = list(reader)
    
    # Statistics
    total_products = len(products)
    products_with_barcode = sum(1 for p in products if p.get('Barcode'))
    
    # Price statistics
    prices = []
    for p in products:
        try:
            price = float(p.get('Price', 0))
            if price > 0:
                prices.append(price)
        except ValueError:
            pass
    
    avg_price = sum(prices) / len(prices) if prices else 0
    min_price = min(prices) if prices else 0
    max_price = max(prices) if prices else 0
    
    # Recent updates
    recent_updates = {}
    for p in products:
        usrdat = p.get('USRDAT', '')
        if len(usrdat) == 6:
            year = '20' + usrdat[4:6]
            recent_updates[year] = recent_updates.get(year, 0) + 1
    
    # Print report
    print(f"\n{'=' * 100}")
    print(f"CATALOG SUMMARY REPORT")
    print(f"{'=' * 100}")
    print(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"\nTotal active products: {total_products:,}")
    print(f"Products with barcodes: {products_with_barcode:,} ({products_with_barcode/total_products*100:.1f}%)")
    
    print(f"\nPrice Statistics:")
    print(f"  Average: ₱{avg_price:,.2f}")
    print(f"  Min: ₱{min_price:,.2f}")
    print(f"  Max: ₱{max_price:,.2f}")
    
    print(f"\nRecent Updates by Year:")
    for year in sorted(recent_updates.keys(), reverse=True)[:5]:
        print(f"  {year}: {recent_updates[year]:>6,} products")
    
    print(f"\n{'=' * 100}")


def main():
    """Main orchestration function"""
    print("=" * 100)
    print("ANSON SUPERMART MASTER CATALOG GENERATOR")
    print("=" * 100)
    print(f"Start time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Configuration
    YEARS_ACTIVE = 5
    MP_MER1 = '/mnt/user-data/uploads/MP_MER.FPB'
    MP_MER2 = '/mnt/user-data/uploads/MP_MER2.FPB'
    
    OUTPUT1 = 'catalog_from_mpmer1.csv'
    OUTPUT2 = 'catalog_from_mpmer2.csv'
    FINAL_OUTPUT = 'ANSON_ACTIVE_CATALOG.csv'
    
    # Step 1: Process first FPB file
    if Path(MP_MER1).exists():
        success1 = process_fpb_file(MP_MER1, OUTPUT1, YEARS_ACTIVE)
    else:
        print(f"Warning: {MP_MER1} not found!")
        success1 = False
    
    # Step 2: Process second FPB file
    if Path(MP_MER2).exists():
        success2 = process_fpb_file(MP_MER2, OUTPUT2, YEARS_ACTIVE)
    else:
        print(f"Warning: {MP_MER2} not found!")
        success2 = False
    
    # Step 3: Merge catalogs
    if success1 or success2:
        total_products = merge_catalogs(OUTPUT1, OUTPUT2, FINAL_OUTPUT)
        
        # Step 4: Generate report
        if total_products > 0:
            generate_report(FINAL_OUTPUT)
            
            # Final summary
            print(f"\n{'=' * 100}")
            print(f"✓ CATALOG GENERATION COMPLETE!")
            print(f"{'=' * 100}")
            print(f"Output file: {FINAL_OUTPUT}")
            print(f"Total active products: {total_products:,}")
            print(f"Ready for Google Sheets upload!")
            print(f"\nEnd time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"{'=' * 100}")
    else:
        print("\n✗ Failed to process any FPB files!")


if __name__ == '__main__':
    main()
