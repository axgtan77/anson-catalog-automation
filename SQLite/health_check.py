#!/usr/bin/env python3
"""
System Health Check - Anson Supermart
Checks database, imports, and system status

Usage:
    python health_check.py
"""

import sqlite3
import os
from pathlib import Path
from datetime import datetime

DB_PATH = 'anson_products.db'

def check_database():
    """Check database exists and structure"""
    print("=" * 80)
    print("DATABASE STATUS")
    print("=" * 80)
    
    if not os.path.exists(DB_PATH):
        print("❌ Database not found!")
        print(f"   Expected: {os.path.abspath(DB_PATH)}")
        print()
        print("   Run: python create_database.py")
        return False
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Check tables
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [row[0] for row in cursor.fetchall()]
    
    print(f"✓ Database exists: {os.path.abspath(DB_PATH)}")
    print(f"  Size: {os.path.getsize(DB_PATH) / 1024 / 1024:.1f} MB")
    print(f"  Tables: {len(tables)}")
    print()
    
    # Check record counts
    stats = {}
    
    queries = {
        'Products': 'SELECT COUNT(*) FROM products',
        'Active Products': 'SELECT COUNT(*) FROM products WHERE active=1',
        'Brands': 'SELECT COUNT(*) FROM brands',
        'Categories': 'SELECT COUNT(*) FROM categories',
        'Departments': 'SELECT COUNT(*) FROM departments',
        'Sales Metrics': 'SELECT COUNT(*) FROM sales_metrics',
        'Images': 'SELECT COUNT(*) FROM images',
        'Barcodes': 'SELECT COUNT(*) FROM barcodes',
    }
    
    print("Record Counts:")
    for label, query in queries.items():
        cursor.execute(query)
        count = cursor.fetchone()[0]
        stats[label] = count
        status = "✓" if count > 0 else "⚠️"
        print(f"  {status} {label:20s} {count:>8,}")
    
    print()
    
    # Check data quality
    cursor.execute("""
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN needs_enrichment=1 THEN 1 ELSE 0 END) as needs_work,
            SUM(CASE WHEN data_quality='COMPLETE' THEN 1 ELSE 0 END) as complete
        FROM products WHERE active=1
    """)
    
    total, needs_work, complete = cursor.fetchone()
    
    if total > 0:
        print("Data Quality:")
        print(f"  Total active:        {total:>8,}")
        print(f"  Complete:            {complete:>8,} ({complete/total*100:>5.1f}%)")
        print(f"  Needs enrichment:    {needs_work:>8,} ({needs_work/total*100:>5.1f}%)")
        print()
    
    # Check images
    cursor.execute("""
        SELECT 
            COUNT(DISTINCT p.merkey) as products_with_images,
            COUNT(*) as total_images
        FROM images img
        JOIN products p ON img.merkey = p.merkey
        WHERE p.active = 1
    """)
    
    products_with_images, total_images = cursor.fetchone()
    
    cursor.execute("SELECT COUNT(*) FROM products WHERE active=1")
    active_products = cursor.fetchone()[0]
    
    if active_products > 0:
        print("Image Coverage:")
        print(f"  Products with images: {products_with_images:>8,} / {active_products:,} ({products_with_images/active_products*100:.1f}%)")
        print(f"  Total image records:  {total_images:>8,}")
        print()
    
    # Check last sync
    cursor.execute("""
        SELECT sync_type, status, completed_at, records_processed
        FROM sync_log
        ORDER BY completed_at DESC
        LIMIT 5
    """)
    
    syncs = cursor.fetchall()
    
    if syncs:
        print("Recent Syncs:")
        for sync_type, status, completed_at, records in syncs:
            completed = completed_at or "In progress"
            status_str = status or "UNKNOWN"
            records_str = f"{records:,}" if records is not None else "?"
            print(f"  {status_str:12s} {sync_type:20s} {completed:20s} ({records_str} records)")
        print()
    
    conn.close()
    return True


def check_files():
    """Check required files exist"""
    print("=" * 80)
    print("FILE STATUS")
    print("=" * 80)
    
    required_files = {
        'schema.sql': 'Database schema',
        'create_database.py': 'Database creator',
        'import_merch_master.py': 'MERCH_MASTER importer',
        'import_operational_master.py': 'Operational Master importer',
        'web_encoder.py': 'Web encoder app',
        'create_templates.py': 'Template generator',
        'image_pipeline.py': 'Image processor',
        's3_upload.py': 'S3 uploader',
    }
    
    all_exist = True
    
    for filename, description in required_files.items():
        exists = os.path.exists(filename)
        status = "✓" if exists else "❌"
        print(f"  {status} {filename:30s} - {description}")
        if not exists:
            all_exist = False
    
    print()
    
    # Check templates directory
    templates_dir = Path('templates')
    if templates_dir.exists():
        templates = list(templates_dir.glob('*.html'))
        print(f"✓ Templates directory exists: {len(templates)} template(s)")
        for t in templates:
            print(f"    - {t.name}")
    else:
        print("⚠️  Templates directory not found")
        print("   Run: python create_templates.py")
    
    print()
    
    # Check uploads directory
    uploads_dir = Path('uploads')
    if uploads_dir.exists():
        orig = len(list((uploads_dir / 'original').glob('*'))) if (uploads_dir / 'original').exists() else 0
        proc = len(list((uploads_dir / 'processed').glob('*'))) if (uploads_dir / 'processed').exists() else 0
        print(f"✓ Uploads directory exists")
        print(f"    Original images:  {orig}")
        print(f"    Processed images: {proc}")
    else:
        print("ℹ️  Uploads directory will be created on first photo upload")
    
    print()
    return all_exist


def check_dependencies():
    """Check Python dependencies"""
    print("=" * 80)
    print("DEPENDENCIES")
    print("=" * 80)
    
    dependencies = {
        'flask': 'Web framework (required)',
        'boto3': 'AWS SDK (required for S3)',
        'PIL': 'Image processing (required)',
        'rembg': 'Background removal (optional)',
    }
    
    for module, description in dependencies.items():
        try:
            if module == 'PIL':
                import PIL
            else:
                __import__(module)
            print(f"  ✓ {module:15s} - {description}")
        except ImportError:
            print(f"  ❌ {module:15s} - {description} - NOT INSTALLED")
    
    print()


def check_aws():
    """Check AWS configuration"""
    print("=" * 80)
    print("AWS CONFIGURATION")
    print("=" * 80)
    
    # Check for AWS credentials
    aws_config_paths = [
        Path.home() / '.aws' / 'credentials',
        Path.home() / '.aws' / 'config',
    ]
    
    has_credentials = False
    for path in aws_config_paths:
        if path.exists():
            print(f"  ✓ {path}")
            has_credentials = True
    
    if not has_credentials:
        print("  ⚠️  No AWS credentials found")
        print("     Set up with: aws configure")
        print("     Or set environment variables:")
        print("       AWS_ACCESS_KEY_ID")
        print("       AWS_SECRET_ACCESS_KEY")
    
    # Check environment variables
    aws_env_vars = ['AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_DEFAULT_REGION']
    env_configured = False
    
    for var in aws_env_vars:
        if os.environ.get(var):
            print(f"  ✓ {var} set in environment")
            env_configured = True
    
    if has_credentials or env_configured:
        print()
        print("  AWS credentials configured ✓")
        print("  Bucket: ansonsupermart.com")
        print("  Region: ap-southeast-1")
    
    print()


def main():
    """Main health check"""
    
    print()
    print("=" * 80)
    print("ANSON SUPERMART - SYSTEM HEALTH CHECK")
    print("=" * 80)
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    # Run checks
    files_ok = check_files()
    db_ok = check_database()
    check_dependencies()
    check_aws()
    
    # Summary
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    
    if files_ok and db_ok:
        print("✓ System is ready!")
        print()
        print("Next steps:")
        print("  - Start web encoder: python web_encoder.py")
        print("  - Access at: http://localhost:5000")
    else:
        print("⚠️  System needs setup")
        print()
        if not files_ok:
            print("  Missing files - make sure all scripts are in place")
        if not db_ok:
            print("  Database issues - run imports:")
            print("    python create_database.py")
            print("    python import_merch_master.py")
            print("    python import_operational_master.py")
    
    print()


if __name__ == '__main__':
    main()
