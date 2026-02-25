#!/usr/bin/env python3
"""
Anson Supermart - Automated Daily Sync
Checks if MP_MER.FPB has changed and syncs if needed

Usage:
    python daily_sync.py
    
Run this daily via Windows Task Scheduler
"""

import os
import sys
from pathlib import Path
from datetime import datetime
import subprocess
import json


# Configuration
CONFIG = {
    'mp_mer_path': r'\\server\share\MP_MER.FPB',  # UPDATE THIS
    'mp_mer2_path': r'\\server\share\MP_MER2.FPB',  # UPDATE THIS (or None)
    'db_path': 'anson_products.db',
    'last_sync_file': '.last_mp_mer_sync.json',
}


def get_file_info(filepath):
    """Get file modification time and size"""
    if not os.path.exists(filepath):
        return None
    
    stat = os.stat(filepath)
    return {
        'mtime': stat.st_mtime,
        'size': stat.st_size,
        'modified': datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
    }


def has_file_changed(filepath, last_sync_file):
    """Check if file has changed since last sync"""
    
    current_info = get_file_info(filepath)
    if not current_info:
        print(f"⚠️  File not found: {filepath}")
        return False, None
    
    # Load last sync info
    if os.path.exists(last_sync_file):
        try:
            with open(last_sync_file, 'r') as f:
                last_info = json.load(f)
        except:
            last_info = {}
    else:
        last_info = {}
    
    last_mtime = last_info.get('mtime', 0)
    last_size = last_info.get('size', 0)
    
    # Check if changed
    changed = (current_info['mtime'] > last_mtime or 
              current_info['size'] != last_size)
    
    return changed, current_info


def save_sync_info(filepath, file_info, last_sync_file):
    """Save file info after successful sync"""
    with open(last_sync_file, 'w') as f:
        json.dump(file_info, f, indent=2)


def run_sync(mp_mer_path, mp_mer2_path, db_path):
    """Run the sync script"""
    
    cmd = ['python', 'sync_mp_mer.py', mp_mer_path]
    if mp_mer2_path and os.path.exists(mp_mer2_path):
        cmd.append(mp_mer2_path)
    
    print("Running sync...")
    print(f"Command: {' '.join(cmd)}")
    print()
    
    result = subprocess.run(cmd, capture_output=False)
    return result.returncode == 0


def main():
    """Main execution"""
    
    print("=" * 80)
    print("ANSON SUPERMART - DAILY AUTOMATED SYNC")
    print("=" * 80)
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    # Check configuration
    mp_mer_path = CONFIG['mp_mer_path']
    mp_mer2_path = CONFIG['mp_mer2_path']
    last_sync_file = CONFIG['last_sync_file']
    
    print(f"Checking: {mp_mer_path}")
    print()
    
    # Check if file has changed
    changed, current_info = has_file_changed(mp_mer_path, last_sync_file)
    
    if not current_info:
        print("❌ MP_MER.FPB not found or not accessible")
        print()
        print("Update CONFIG in this script with correct path:")
        print(f"  Current: {mp_mer_path}")
        print()
        sys.exit(1)
    
    print(f"File info:")
    print(f"  Last modified: {current_info['modified']}")
    print(f"  Size: {current_info['size']:,} bytes")
    print()
    
    if not changed:
        print("✓ No changes detected - sync not needed")
        print()
        
        # Show last sync info
        if os.path.exists(last_sync_file):
            with open(last_sync_file, 'r') as f:
                last_info = json.load(f)
                print(f"Last sync: {last_info.get('modified', 'Unknown')}")
        
        print()
        sys.exit(0)
    
    print("🔄 Changes detected - starting sync...")
    print()
    
    # Run sync
    success = run_sync(mp_mer_path, mp_mer2_path, CONFIG['db_path'])
    
    if success:
        print()
        print("=" * 80)
        print("✓ SYNC SUCCESSFUL")
        print("=" * 80)
        
        # Save sync info
        save_sync_info(mp_mer_path, current_info, last_sync_file)
        print(f"Sync info saved: {last_sync_file}")
        print()
        
        sys.exit(0)
    else:
        print()
        print("=" * 80)
        print("❌ SYNC FAILED")
        print("=" * 80)
        print("Check errors above")
        print()
        
        sys.exit(1)


if __name__ == '__main__':
    main()
