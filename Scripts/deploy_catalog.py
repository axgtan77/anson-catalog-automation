#!/usr/bin/env python3
"""
PRODUCTION DEPLOYMENT SCRIPT - STRATEGY 2
Anson Supermart Catalog Generator
Full Month + Velocity Filter (min-transactions 2)

INSTRUCTIONS:
1. Update the file paths below (lines 20-24)
2. Run: python3 deploy_catalog.py
3. Review the output catalog
4. If satisfied, set up monthly automation
"""

import subprocess
import sys
from pathlib import Path
from datetime import datetime

# ============================================================================
# CONFIGURATION - UPDATE THESE PATHS FOR YOUR SYSTEM
# ============================================================================

# Path to your sales transaction files (use wildcards for month)
# Example: "C:\\Data\\TM_02*.FPB" for February
# Example: "\\\\server\\share\\TM_02*.FPB" for network path
SALES_FILES_PATTERN = "/mnt/user-data/uploads/TM_02*.FPB"  # UPDATE THIS

# Path to your product master database
# Use ANSON_ACTIVE_CATALOG.csv (merged) or MP_MER.FPB + MP_MER2.FPB
PRODUCT_DATABASE = "ANSON_ACTIVE_CATALOG.csv"  # UPDATE THIS

# Where to save the output catalog
OUTPUT_CATALOG = "/mnt/user-data/outputs/PRODUCTION_CATALOG.csv"  # UPDATE THIS

# Minimum transactions filter (2 = exclude one-time sales)
MIN_TRANSACTIONS = 2

# ============================================================================
# SCRIPT EXECUTION - DO NOT MODIFY BELOW THIS LINE
# ============================================================================

def main():
    print("=" * 100)
    print("ANSON SUPERMART CATALOG GENERATOR - PRODUCTION DEPLOYMENT")
    print("Strategy 2: Full Month + Velocity Filter")
    print("=" * 100)
    print()
    
    # Validate paths
    print("Validating configuration...")
    print(f"  Sales files pattern: {SALES_FILES_PATTERN}")
    print(f"  Product database: {PRODUCT_DATABASE}")
    print(f"  Output catalog: {OUTPUT_CATALOG}")
    print(f"  Minimum transactions: {MIN_TRANSACTIONS}")
    print()
    
    # Check if product database exists
    if not Path(PRODUCT_DATABASE).exists():
        print(f"❌ ERROR: Product database not found: {PRODUCT_DATABASE}")
        print("   Please update PRODUCT_DATABASE path in this script (line 23)")
        sys.exit(1)
    
    # Build command
    cmd = [
        'python3',
        'generate_sales_based_catalog.py',
        SALES_FILES_PATTERN,
        PRODUCT_DATABASE,
        '-o', OUTPUT_CATALOG,
        '--min-transactions', str(MIN_TRANSACTIONS)
    ]
    
    print("=" * 100)
    print("RUNNING CATALOG GENERATION")
    print("=" * 100)
    print()
    print(f"Command: {' '.join(cmd)}")
    print()
    
    # Run the generator
    result = subprocess.run(cmd, capture_output=False)
    
    if result.returncode == 0:
        print()
        print("=" * 100)
        print("✅ SUCCESS! CATALOG GENERATED")
        print("=" * 100)
        print()
        print(f"Output file: {OUTPUT_CATALOG}")
        print()
        
        # Check if file exists and get size
        output_path = Path(OUTPUT_CATALOG)
        if output_path.exists():
            file_size = output_path.stat().st_size / 1024 / 1024  # MB
            print(f"File size: {file_size:.2f} MB")
            
            # Count lines (products)
            with open(OUTPUT_CATALOG, 'r', encoding='utf-8') as f:
                product_count = sum(1 for line in f) - 1  # -1 for header
            print(f"Products in catalog: {product_count:,}")
            print()
            
            # Expected range
            print("Expected range: 15,000-17,000 products")
            if 15000 <= product_count <= 17000:
                print("✅ Product count is in expected range!")
            elif product_count < 15000:
                print("⚠️  Product count is lower than expected")
                print("   Consider lowering MIN_TRANSACTIONS or checking date range")
            else:
                print("⚠️  Product count is higher than expected")
                print("   Consider increasing MIN_TRANSACTIONS")
        
        print()
        print("=" * 100)
        print("NEXT STEPS:")
        print("=" * 100)
        print()
        print("1. Open the catalog in Excel and review:")
        print(f"   {OUTPUT_CATALOG}")
        print()
        print("2. Check that:")
        print("   • Product count matches expectations (~16,000)")
        print("   • Best sellers are at the top (highest TransactionCount)")
        print("   • Prices look current")
        print("   • No obviously discontinued items")
        print()
        print("3. Compare with your current online catalog")
        print()
        print("4. If satisfied, proceed to monthly automation setup")
        print("   (See AUTOMATION_SETUP.txt)")
        print()
        
    else:
        print()
        print("=" * 100)
        print("❌ ERROR: Catalog generation failed")
        print("=" * 100)
        print()
        print("Common issues:")
        print("1. Sales files not found - check SALES_FILES_PATTERN")
        print("2. Product database not found - check PRODUCT_DATABASE path")
        print("3. Permission denied - check OUTPUT_CATALOG folder permissions")
        print()
        sys.exit(1)

if __name__ == '__main__':
    main()
