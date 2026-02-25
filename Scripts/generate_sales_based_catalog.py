#!/usr/bin/env python3
"""
Sales-Based Catalog Generator for Anson Supermart
Uses actual sales transactions to determine active products
Most accurate method for catalog filtering
"""

import struct
import csv
import glob
from collections import Counter
from datetime import datetime
from pathlib import Path


class SalesBasedCatalogGenerator:
    """Generate catalog based on actual sales transactions"""
    
    def __init__(self, sales_pattern: str, product_database: str, output_csv: str, 
                 min_transactions: int = 1):
        """
        Args:
            sales_pattern: Pattern for sales files (e.g., '/path/TM_*.FPB')
            product_database: Path to MP_MER.FPB or merged catalog
            output_csv: Output catalog file
            min_transactions: Minimum number of transactions to be considered active
        """
        self.sales_pattern = sales_pattern
        self.product_database = product_database
        self.output_csv = output_csv
        self.min_transactions = min_transactions
        
        self.stats = {
            'sales_files': 0,
            'total_transactions': 0,
            'retail_transactions': 0,
            'unique_products_sold': 0,
            'products_in_catalog': 0,
            'products_exported': 0
        }
    
    def extract_active_products_from_sales(self):
        """Extract MERKEYs from sales transactions where TRETYP='RE'"""
        print("=" * 100)
        print("STEP 1: ANALYZING SALES TRANSACTIONS")
        print("=" * 100)
        print()
        
        active_merkeys = set()
        transaction_counts = Counter()
        
        sales_files = sorted(glob.glob(self.sales_pattern))
        
        if not sales_files:
            print(f"✗ No sales files found matching pattern: {self.sales_pattern}")
            return None, None
        
        for filename in sales_files:
            print(f"Processing {Path(filename).name}...", end=' ')
            
            with open(filename, 'rb') as f:
                # Read header
                header = f.read(32)
                num_records = struct.unpack('<I', header[4:8])[0]
                header_length = struct.unpack('<H', header[8:10])[0]
                record_length = struct.unpack('<H', header[10:12])[0]
                
                # Find field positions
                f.seek(32)
                field_positions = {}
                current_pos = 1
                
                while True:
                    field_desc = f.read(32)
                    if field_desc[0] == 0x0D:
                        break
                    
                    field_name = field_desc[0:11].decode('ascii').strip('\x00')
                    field_length = field_desc[16]
                    
                    field_positions[field_name] = (current_pos, field_length)
                    current_pos += field_length
                
                merkey_pos, merkey_len = field_positions['MERKEY']
                tretyp_pos, tretyp_len = field_positions['TRETYP']
                
                # Read transactions
                f.seek(header_length)
                
                file_retail = 0
                file_total = 0
                
                for i in range(num_records):
                    record_data = f.read(record_length)
                    
                    if not record_data or len(record_data) < record_length:
                        break
                    
                    if record_data[0] == 0x2A:  # Deleted
                        continue
                    
                    file_total += 1
                    
                    # Check transaction type
                    tretyp = record_data[tretyp_pos:tretyp_pos + tretyp_len].decode('ascii', errors='ignore').strip()
                    
                    if tretyp == 'RE':  # Retail sales
                        merkey = record_data[merkey_pos:merkey_pos + merkey_len].decode('ascii', errors='ignore').strip()
                        
                        if merkey:
                            active_merkeys.add(merkey)
                            transaction_counts[merkey] += 1
                            file_retail += 1
                
                self.stats['total_transactions'] += file_total
                self.stats['retail_transactions'] += file_retail
                
                print(f"{file_retail:,} retail transactions")
            
            self.stats['sales_files'] += 1
        
        # Filter by minimum transaction count
        if self.min_transactions > 1:
            filtered_merkeys = {m for m in active_merkeys if transaction_counts[m] >= self.min_transactions}
            print(f"\n✓ Filtered to products with {self.min_transactions}+ transactions: {len(filtered_merkeys):,} products")
            active_merkeys = filtered_merkeys
        
        self.stats['unique_products_sold'] = len(active_merkeys)
        
        return active_merkeys, transaction_counts
    
    def read_product_database(self):
        """Read product master database (MP_MER.FPB or CSV)"""
        print()
        print("=" * 100)
        print("STEP 2: READING PRODUCT DATABASE")
        print("=" * 100)
        print()
        
        products = {}
        
        # Check if it's a CSV (from previous catalog generation) or FPB
        if self.product_database.endswith('.csv'):
            print(f"Reading CSV: {self.product_database}")
            with open(self.product_database, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    merkey = row.get('MERKEY', '').strip()
                    if merkey:
                        products[merkey] = row
        
        else:
            # Read from FPB file
            print(f"Reading FPB: {self.product_database}")
            
            with open(self.product_database, 'rb') as f:
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
                    field_length = field_desc[16]
                    field_type = chr(field_desc[11])
                    
                    field_positions[field_name] = (current_pos, field_length, field_type)
                    current_pos += field_length
                
                # Read all records
                f.seek(header_length)
                
                for i in range(num_records):
                    record_data = f.read(record_length)
                    
                    if not record_data or len(record_data) < record_length:
                        break
                    
                    if record_data[0] == 0x2A:  # Deleted
                        continue
                    
                    # Parse record
                    record = {}
                    for field_name, (pos, length, ftype) in field_positions.items():
                        field_data = record_data[pos:pos + length]
                        
                        if ftype == 'C':
                            value = field_data.decode('latin-1', errors='ignore').strip()
                        elif ftype == 'N':
                            value = field_data.decode('ascii', errors='ignore').strip()
                        else:
                            value = field_data.decode('latin-1', errors='ignore').strip()
                        
                        record[field_name] = value
                    
                    merkey = record.get('MERKEY', '').strip()
                    if merkey:
                        # Format for catalog
                        barcode = record.get('MEAN13', '').strip()
                        if not barcode:
                            barcode = record.get('BARCD1', '').strip()
                        if barcode:
                            barcode = f"*{barcode}*"
                        
                        products[merkey] = {
                            'Brand': '',
                            'Name': record.get('MEDESC', ''),
                            'Description': record.get('MEDESC', ''),
                            'Size': '',
                            'Price': record.get('MERETP', '0'),
                            'Photo': '',
                            'Cards': '',
                            'Search': '',
                            'MERKEY': merkey,
                            'MEDESC': record.get('MEDESC', ''),
                            'PRICEJP': record.get('MERETP', '0'),
                            'Barcode': barcode,
                            'SURKEY': record.get('SURKEY', ''),
                            'USRDAT': record.get('USRDAT', ''),
                        }
                    
                    if (i + 1) % 5000 == 0:
                        print(f"Processed {i + 1:,} / {num_records:,} records...", end='\r')
                
                print()
        
        self.stats['products_in_catalog'] = len(products)
        print(f"✓ Loaded {len(products):,} products from database")
        
        return products
    
    def generate_catalog(self):
        """Main generation function"""
        print("=" * 100)
        print("SALES-BASED CATALOG GENERATOR")
        print("=" * 100)
        print(f"Sales pattern: {self.sales_pattern}")
        print(f"Product database: {self.product_database}")
        print(f"Output: {self.output_csv}")
        print(f"Minimum transactions: {self.min_transactions}")
        print()
        
        # Step 1: Extract active products from sales
        active_merkeys, transaction_counts = self.extract_active_products_from_sales()
        
        if not active_merkeys:
            print("✗ No active products found in sales data!")
            return
        
        # Step 2: Read product database
        all_products = self.read_product_database()
        
        # Step 3: Generate catalog
        print()
        print("=" * 100)
        print("STEP 3: GENERATING ACTIVE CATALOG")
        print("=" * 100)
        print()
        
        catalog_products = []
        missing_products = []
        
        for merkey in active_merkeys:
            if merkey in all_products:
                product = all_products[merkey].copy()
                # Add transaction count for reference
                product['TransactionCount'] = transaction_counts.get(merkey, 0)
                catalog_products.append(product)
            else:
                missing_products.append(merkey)
        
        # Sort by transaction count (most popular first)
        catalog_products.sort(key=lambda x: x.get('TransactionCount', 0), reverse=True)
        
        # Write catalog
        if catalog_products:
            fieldnames = list(catalog_products[0].keys())
            
            with open(self.output_csv, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(catalog_products)
            
            self.stats['products_exported'] = len(catalog_products)
            
            print(f"✓ Generated catalog with {len(catalog_products):,} products")
            
            if missing_products:
                print(f"⚠️  Warning: {len(missing_products):,} products sold but not in database")
                print(f"   Sample missing MERKEYs: {', '.join(missing_products[:5])}")
        else:
            print("✗ No products to export!")
        
        # Print statistics
        self.print_statistics(transaction_counts)
    
    def print_statistics(self, transaction_counts):
        """Print generation statistics"""
        print()
        print("=" * 100)
        print("GENERATION STATISTICS")
        print("=" * 100)
        print(f"Sales files processed:        {self.stats['sales_files']:>8,}")
        print(f"Total transactions:           {self.stats['total_transactions']:>8,}")
        print(f"Retail transactions (RE):     {self.stats['retail_transactions']:>8,}")
        print(f"Unique products sold:         {self.stats['unique_products_sold']:>8,}")
        print(f"Products in database:         {self.stats['products_in_catalog']:>8,}")
        print(f"Products exported to catalog: {self.stats['products_exported']:>8,}")
        print()
        
        # Sales velocity distribution
        print("=" * 100)
        print("SALES VELOCITY DISTRIBUTION")
        print("=" * 100)
        
        velocity_buckets = {
            '100+ transactions': sum(1 for c in transaction_counts.values() if c >= 100),
            '50-99 transactions': sum(1 for c in transaction_counts.values() if 50 <= c < 100),
            '20-49 transactions': sum(1 for c in transaction_counts.values() if 20 <= c < 50),
            '10-19 transactions': sum(1 for c in transaction_counts.values() if 10 <= c < 20),
            '5-9 transactions': sum(1 for c in transaction_counts.values() if 5 <= c < 10),
            '2-4 transactions': sum(1 for c in transaction_counts.values() if 2 <= c < 5),
            '1 transaction': sum(1 for c in transaction_counts.values() if c == 1),
        }
        
        for bucket, count in velocity_buckets.items():
            pct = count / len(transaction_counts) * 100 if transaction_counts else 0
            print(f"  {bucket:25s} {count:>6,} products ({pct:5.1f}%)")
        
        print()
        print("=" * 100)


def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Generate catalog from sales transactions')
    parser.add_argument('sales_pattern', help='Pattern for sales files (e.g., TM_*.FPB)')
    parser.add_argument('product_db', help='Product database (MP_MER.FPB or ANSON_ACTIVE_CATALOG.csv)')
    parser.add_argument('-o', '--output', default='SALES_BASED_CATALOG.csv', help='Output CSV file')
    parser.add_argument('-m', '--min-transactions', type=int, default=1, 
                        help='Minimum transactions to be considered active (default: 1)')
    
    args = parser.parse_args()
    
    # Create generator
    generator = SalesBasedCatalogGenerator(
        sales_pattern=args.sales_pattern,
        product_database=args.product_db,
        output_csv=args.output,
        min_transactions=args.min_transactions
    )
    
    generator.generate_catalog()
    
    print(f"\n✓ Sales-based catalog generation complete!")
    print(f"✓ Output: {args.output}")
    print(f"\n💡 This catalog contains only products that actually sold in the specified period.")


if __name__ == '__main__':
    main()
