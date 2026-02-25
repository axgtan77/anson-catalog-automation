#!/usr/bin/env python3
"""
Anson Supermart - Database Initialization
Creates SQLite database with complete schema

Usage:
    python create_database.py
"""

import sqlite3
import os
from pathlib import Path
from datetime import datetime


def create_database(db_path='anson_products.db', schema_path='schema.sql'):
    """Create SQLite database with schema"""
    
    print("=" * 80)
    print("ANSON SUPERMART - DATABASE INITIALIZATION")
    print("=" * 80)
    print()
    
    # Check if database already exists
    if os.path.exists(db_path):
        response = input(f"⚠️  Database '{db_path}' already exists. Overwrite? (yes/no): ")
        if response.lower() != 'yes':
            print("Cancelled. Database not modified.")
            return False
        
        # Backup existing database
        backup_path = f"{db_path}.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        print(f"Creating backup: {backup_path}")
        os.rename(db_path, backup_path)
    
    # Read schema
    if not os.path.exists(schema_path):
        print(f"✗ Error: Schema file '{schema_path}' not found!")
        return False
    
    print(f"Reading schema from: {schema_path}")
    with open(schema_path, 'r', encoding='utf-8') as f:
        schema_sql = f.read()
    
    # Create database
    print(f"Creating database: {db_path}")
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Execute schema
        print("Executing schema...")
        cursor.executescript(schema_sql)
        
        conn.commit()
        
        # Verify creation
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = cursor.fetchall()
        
        cursor.execute("SELECT name FROM sqlite_master WHERE type='view' ORDER BY name")
        views = cursor.fetchall()
        
        cursor.execute("SELECT name FROM sqlite_master WHERE type='trigger' ORDER BY name")
        triggers = cursor.fetchall()
        
        print()
        print("=" * 80)
        print("DATABASE CREATED SUCCESSFULLY!")
        print("=" * 80)
        print()
        print(f"Tables created ({len(tables)}):")
        for table in tables:
            print(f"  ✓ {table[0]}")
        
        print()
        print(f"Views created ({len(views)}):")
        for view in views:
            print(f"  ✓ {view[0]}")
        
        print()
        print(f"Triggers created ({len(triggers)}):")
        for trigger in triggers:
            print(f"  ✓ {trigger[0]}")
        
        # Get database info
        cursor.execute("SELECT value FROM db_metadata WHERE key='schema_version'")
        version = cursor.fetchone()
        
        print()
        print(f"Schema version: {version[0] if version else 'N/A'}")
        print(f"Database file: {os.path.abspath(db_path)}")
        print(f"File size: {os.path.getsize(db_path):,} bytes")
        
        conn.close()
        
        print()
        print("=" * 80)
        print("NEXT STEPS:")
        print("=" * 80)
        print()
        print("1. Import MERCH_MASTER.csv:")
        print("   python import_merch_master.py")
        print()
        print("2. Import Operational_Master_Active_24M.csv:")
        print("   python import_operational_master.py")
        print()
        print("3. Sync from MP_MER.FPB:")
        print("   python sync_mp_mer.py")
        print()
        
        return True
        
    except sqlite3.Error as e:
        print(f"✗ Database error: {e}")
        return False
    except Exception as e:
        print(f"✗ Unexpected error: {e}")
        return False


def test_database(db_path='anson_products.db'):
    """Run basic tests on the database"""
    
    print("=" * 80)
    print("TESTING DATABASE")
    print("=" * 80)
    print()
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Test queries
        tests = [
            ("Check departments table", "SELECT COUNT(*) FROM departments"),
            ("Check brands table", "SELECT COUNT(*) FROM brands"),
            ("Check products table", "SELECT COUNT(*) FROM products"),
            ("Check foreign keys enabled", "PRAGMA foreign_keys"),
        ]
        
        for test_name, sql in tests:
            cursor.execute(sql)
            result = cursor.fetchone()
            print(f"  ✓ {test_name}: {result[0]}")
        
        conn.close()
        
        print()
        print("All tests passed!")
        return True
        
    except Exception as e:
        print(f"✗ Test failed: {e}")
        return False


def main():
    """Main execution"""
    
    # Set paths
    db_path = 'anson_products.db'
    schema_path = 'schema.sql'
    
    # Create database
    success = create_database(db_path, schema_path)
    
    if success:
        # Test database
        test_database(db_path)
        
        print()
        print("=" * 80)
        print("✓ Database ready for data import!")
        print("=" * 80)
    else:
        print()
        print("=" * 80)
        print("✗ Database creation failed")
        print("=" * 80)


if __name__ == '__main__':
    main()
