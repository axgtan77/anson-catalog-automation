# MP_MER.FPB Daily Sync - Quick Start Guide

## Overview

This system syncs prices from your FoxPro MP_MER.FPB file to the SQLite database daily. It:

✅ Reads current prices from MP_MER.FPB (Mode 1, 2, 3)
✅ Updates price history (tracks every price change)
✅ Marks products as active/inactive based on presence in MP_MER
✅ Logs all sync operations

---

## File Locations

**Source File (Live):**
```
F:\SSIMS\MP_MER.FPB  (or \\anson_server\ssims\ssims\MP_MER.FPB)
```

**Daily Backups:**
```
\\anson_server\ssims.bak\MMDDYY.LZH
Example: 022426.LZH (Feb 24, 2026)
```

**Recommended:** Use live file from F:\SSIMS for daily sync (simpler, no extraction needed)

---

## Quick Start

### Manual Sync (Do This Now)

**Step 1: Navigate to SQLite folder**
```bash
cd D:\Projects\CatalogAutomation\SQLite
```

**Step 2: Run sync (dry run first - test mode)**
```bash
python sync_mp_mer.py --dry-run
```

This will:
- Read MP_MER.FPB from F:\SSIMS
- Show what would be updated
- NOT commit changes (safe to test)

**Step 3: Review output**
You should see:
```
================================================================================
SYNC SUMMARY
================================================================================
Records processed:     XX,XXX
Prices updated:        X,XXX
Prices added:          X,XXX
Price changes:         XXX
Products activated:    XX
Products deactivated:  XX
Skipped:               X,XXX
================================================================================
✓ DRY RUN COMPLETE - No changes committed
================================================================================
```

**Step 4: Run actual sync**
```bash
python sync_mp_mer.py
```

This commits the changes to database.

---

## Command Options

### Basic Sync (Recommended)
```bash
python sync_mp_mer.py
```
- Reads from F:\SSIMS\MP_MER.FPB
- Updates database
- Commits changes

### Test Mode (Dry Run)
```bash
python sync_mp_mer.py --dry-run
```
- Shows what would be updated
- Does NOT commit changes
- Safe for testing

### Custom Source Location
```bash
python sync_mp_mer.py --source "C:\path\to\MP_MER.FPB"
```
- Use specific FPB file
- Good for testing with backups

### Copy from Network First
```bash
python sync_mp_mer.py --copy
```
- Copies F:\SSIMS\MP_MER.FPB to temp folder first
- Syncs from temp copy
- Cleans up temp file after
- Safer if worried about file locking

---

## What Gets Synced

### Prices
- **Mode 1 (MEWHOP)** → price_case (Wholesale/Case price)
- **Mode 2 (MERET2)** → price_pack (Pack price)
- **Mode 3 (MERETP)** → price_retail (Retail price)
- **Cost (MECOS0)** → cost

### Price History
- Every price change is recorded with date
- Old prices marked as `is_current = 0`
- New prices marked as `is_current = 1`
- Full price history preserved

### Product Status
- Products in MP_MER → marked `active = 1`
- Products NOT in MP_MER → marked `active = 0`
- Tracks which products are currently available

---

## Scheduling Daily Sync

### Windows Task Scheduler (Recommended)

**Create Batch File:** `D:\Projects\CatalogAutomation\Sync\daily_sync.bat`
```batch
@echo off
cd /d D:\Projects\CatalogAutomation\SQLite
python sync_mp_mer.py >> sync_log.txt 2>&1
```

**Schedule:**
1. Open Task Scheduler
2. Create Basic Task
3. Name: "Anson Catalog Price Sync"
4. Trigger: Daily at 10:00 PM (after 9:40 PM backup)
5. Action: Start a program
   - Program: `D:\Projects\CatalogAutomation\Sync\daily_sync.bat`
6. Finish

---

## Next Steps

After first successful sync:

1. ✅ Set up daily schedule (Task Scheduler at 10 PM)
2. ✅ Monitor sync_log table for issues
3. ✅ Check price history is working
4. ⏭️ Move to Phase 3: Image Management

---

Ready to sync? Run: `python sync_mp_mer.py --dry-run`
