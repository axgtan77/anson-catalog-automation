#!/usr/bin/env python3
"""
Anson Supermart Online Catalog Generator
Filters active products from MP_MER.FPB based on USRDAT field
Similar to Nielsen automation - processes FoxPro database and generates clean catalog
"""

import struct
import csv
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional


class ProductCatalogGenerator:
    """Generate active product catalog from FoxPro database"""
    
    def __init__(self, input_file: str, output_csv: str, years_active: int = 5):
        self.input_file = input_file
        self.output_csv = output_csv
        self.years_active = years_active
        self.cutoff_date = datetime.now() - timedelta(days=years_active * 365)
        
        # Statistics
        self.stats = {
            'total_records': 0,
            'active_records': 0,
            'inactive_records': 0,
            'no_date_records': 0,
            'price_filtered': 0,
            'barcode_filtered': 0
        }
    
    def read_dbf_header(self, f):
        """Read DBF file header and return field definitions"""
        header = f.read(32)
        
        num_records = struct.unpack('<I', header[4:8])[0]
        header_length = struct.unpack('<H', header[8:10])[0]
        record_length = struct.unpack('<H', header[10:12])[0]
        
        # Read field descriptors
        f.seek(32)
        fields = []
        field_positions = {}
        current_pos = 1  # Skip deletion flag
        
        while True:
            field_desc = f.read(32)
            if field_desc[0] == 0x0D:  # End of fields
                break
            
            field_name = field_desc[0:11].decode('ascii').strip('\x00')
            field_type = chr(field_desc[11])
            field_length = field_desc[16]
            field_decimal = field_desc[17]
            
            field_positions[field_name] = (current_pos, field_length, field_type)
            current_pos += field_length
            
            fields.append({
                'name': field_name,
                'type': field_type,
                'length': field_length,
                'decimal': field_decimal
            })
        
        return num_records, header_length, record_length, fields, field_positions
    
    def parse_usrdat(self, usrdat: str) -> Optional[datetime]:
        """Parse USRDAT field (MMDDYY format) to datetime"""
        if not usrdat or len(usrdat) != 6:
            return None
        
        try:
            month = int(usrdat[0:2])
            day = int(usrdat[2:4])
            year = int(usrdat[4:6])
            
            # Convert 2-digit year to 4-digit
            year = 2000 + year
            
            return datetime(year, month, day)
        except (ValueError, IndexError):
            return None
    
    def parse_field(self, record_data: bytes, field_pos: tuple) -> str:
        """Parse a field from record data"""
        pos, length, field_type = field_pos
        field_data = record_data[pos:pos + length]
        
        if field_type == 'C':  # Character
            return field_data.decode('latin-1', errors='ignore').strip()
        elif field_type == 'N':  # Numeric
            value = field_data.decode('ascii', errors='ignore').strip()
            return value if value else '0'
        else:
            return field_data.decode('latin-1', errors='ignore').strip()
    
    def is_active_product(self, record: Dict[str, str]) -> bool:
        """
        Determine if a product is active based on multiple criteria
        Returns: (is_active, reason)
        """
        # Parse USRDAT
        usrdat_str = record.get('USRDAT', '').strip()
        
        if not usrdat_str:
            self.stats['no_date_records'] += 1
            # Products with no date - include them (could be legacy data)
            return True
        
        usrdat = self.parse_usrdat(usrdat_str)
        
        if not usrdat:
            self.stats['no_date_records'] += 1
            return True  # Invalid date - include to be safe
        
        # Primary filter: Check if updated within specified years
        if usrdat < self.cutoff_date:
            self.stats['inactive_records'] += 1
            return False
        
        # Quality checks
        try:
            price = float(record.get('MERETP', '0'))
            
            # Filter out extreme prices (likely data errors)
            if price > 50000:
                self.stats['price_filtered'] += 1
                return False
            
            # Filter out zero prices
            if price <= 0:
                self.stats['price_filtered'] += 1
                return False
                
        except ValueError:
            self.stats['price_filtered'] += 1
            return False
        
        # Check for description
        if not record.get('MEDESC', '').strip():
            return False
        
        # Passed all checks
        self.stats['active_records'] += 1
        return True
    
    def extract_product_info(self, record: Dict[str, str]) -> Dict[str, str]:
        """Extract relevant fields for catalog"""
        
        # Parse description to separate brand, name, size
        description = record.get('MEDESC', '').strip()
        
        # Get barcode - prefer MEAN13, fallback to BARCD1
        barcode = record.get('MEAN13', '').strip()
        if not barcode:
            barcode = record.get('BARCD1', '').strip()
        
        # Format barcode with asterisks if exists (like Nielsen automation)
        if barcode:
            barcode = f"*{barcode}*"
        
        # Build catalog record
        catalog_record = {
            'Brand': '',  # Could extract from MEBRAC or supplier
            'Name': description,
            'Description': description,
            'Size': '',  # Could extract from description parsing
            'Price': record.get('MERETP', '0'),
            'Photo': '',  # Will be populated by image upload script
            'Cards': '',  # Not sure what this field is for
            'Search': '',  # Could be auto-generated
            'MERKEY': record.get('MERKEY', ''),
            'MEDESC': description,
            'PRICEJP': record.get('MERETP', '0'),  # Same as Price?
            'Barcode': barcode,
            'SURKEY': record.get('SURKEY', ''),  # Supplier key
            'USRDAT': record.get('USRDAT', ''),  # Keep for reference
        }
        
        return catalog_record
    
    def process_database(self):
        """Main processing function"""
        print(f"=" * 100)
        print(f"ANSON SUPERMART CATALOG GENERATOR")
        print(f"=" * 100)
        print(f"Input file: {self.input_file}")
        print(f"Output file: {self.output_csv}")
        print(f"Activity filter: Last {self.years_active} years (since {self.cutoff_date.strftime('%Y-%m-%d')})")
        print()
        
        with open(self.input_file, 'rb') as f:
            # Read header
            num_records, header_length, record_length, fields, field_positions = self.read_dbf_header(f)
            
            print(f"Database contains {num_records:,} total records")
            print(f"Processing...")
            print()
            
            # Position to first record
            f.seek(header_length)
            
            # Open output CSV
            catalog_records = []
            
            # Process all records
            for i in range(num_records):
                record_data = f.read(record_length)
                
                if not record_data or len(record_data) < record_length:
                    break
                
                # Skip deleted records
                if record_data[0] == 0x2A:
                    continue
                
                self.stats['total_records'] += 1
                
                # Parse all fields
                record = {}
                for field_name, field_pos in field_positions.items():
                    record[field_name] = self.parse_field(record_data, field_pos)
                
                # Check if active
                if self.is_active_product(record):
                    catalog_record = self.extract_product_info(record)
                    catalog_records.append(catalog_record)
                
                # Progress indicator
                if (i + 1) % 1000 == 0:
                    print(f"Processed {i + 1:,} / {num_records:,} records...", end='\r')
            
            print()  # New line after progress
            
            # Write to CSV
            if catalog_records:
                fieldnames = list(catalog_records[0].keys())
                
                with open(self.output_csv, 'w', newline='', encoding='utf-8') as csvfile:
                    writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(catalog_records)
                
                print(f"\n✓ Successfully wrote {len(catalog_records):,} active products to {self.output_csv}")
            else:
                print("\n✗ No active products found!")
        
        # Print statistics
        self.print_statistics()
    
    def print_statistics(self):
        """Print processing statistics"""
        print()
        print("=" * 100)
        print("PROCESSING STATISTICS")
        print("=" * 100)
        print(f"Total records processed:     {self.stats['total_records']:>8,}")
        print(f"Active products (exported):  {self.stats['active_records']:>8,} ({self.stats['active_records']/self.stats['total_records']*100:5.1f}%)")
        print(f"Inactive products (old):     {self.stats['inactive_records']:>8,} ({self.stats['inactive_records']/self.stats['total_records']*100:5.1f}%)")
        print(f"No date / invalid date:      {self.stats['no_date_records']:>8,} ({self.stats['no_date_records']/self.stats['total_records']*100:5.1f}%)")
        print(f"Filtered by price:           {self.stats['price_filtered']:>8,}")
        print()
        print(f"Cutoff date: {self.cutoff_date.strftime('%Y-%m-%d')}")
        print(f"Today's date: {datetime.now().strftime('%Y-%m-%d')}")
        print("=" * 100)


def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Generate active product catalog from MP_MER.FPB')
    parser.add_argument('input_file', help='Path to MP_MER.FPB file')
    parser.add_argument('-o', '--output', default='active_catalog.csv', help='Output CSV file')
    parser.add_argument('-y', '--years', type=int, default=5, help='Number of years to consider active (default: 5)')
    
    args = parser.parse_args()
    
    # Check if input file exists
    if not Path(args.input_file).exists():
        print(f"Error: Input file '{args.input_file}' not found!")
        sys.exit(1)
    
    # Create generator and process
    generator = ProductCatalogGenerator(
        input_file=args.input_file,
        output_csv=args.output,
        years_active=args.years
    )
    
    generator.process_database()
    
    print(f"\n✓ Catalog generation complete!")
    print(f"✓ Next step: Upload {args.output} to Google Sheets")


if __name__ == '__main__':
    main()
