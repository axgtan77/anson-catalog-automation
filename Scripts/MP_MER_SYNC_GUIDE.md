# MP_MER.FPB Sync - Complete Guide

## 🎯 What This Does

Comprehensive change detection for MP_MER.FPB:
- ✅ **New products** - Automatically adds to database
- ✅ **Price changes** - Tracks with full history
- ✅ **MEDESC changes** - Flags for review (size/property changes)
- ✅ **Barcode updates** - Syncs all barcodes
- ✅ **Change log** - Detailed report of all changes

---

## 📥 Installation

Download these files to `D:\Projects\CatalogAutomation\SQLite\`:
- `sync_mp_mer.py` ⭐
- `daily_sync.py` ⭐

---

## 🚀 Usage

### **Manual Sync (First Time)**

```bash
cd D:\Projects\CatalogAutomation\SQLite

# Sync from last night's MP_MER.FPB
python sync_mp_mer.py "\\server\share\MP_MER.FPB"

# Or with MP_MER2
python sync_mp_mer.py "\\server\share\MP_MER.FPB" "\\server\share\MP_MER2.FPB"
```

### **Output Example:**

```
================================================================================
MP_MER.FPB SYNC - CHANGE DETECTION
================================================================================
Started: 2026-02-24 11:45:00

Database: anson_products.db
MP_MER:   \\server\share\MP_MER.FPB

================================================================================
READING MP_MER.FPB
================================================================================
  Read 55,432 records

================================================================================
DETECTING CHANGES
================================================================================
  ✓ Processed 55,432 products

================================================================================
CHANGE DETECTION REPORT
================================================================================

Products processed: 55,432

🆕 NEW PRODUCTS: 127

  + 1234567    GARDENIA WHOLE WHEAT BREAD 600G                    ₱    45.00
  + 2345678    SAN MIGUEL BEER PALE PILSEN 1L                     ₱    85.00
  ... and 125 more

💰 PRICE CHANGES: 342

  ↑ 1403473    GARDENIA CLASSIC WHT 600G REG
     ₱   42.00 → ₱   45.00 ( +7.1%)
  ↓ 9876543    LUCKY ME PANCIT CANTON 60G
     ₱   14.50 → ₱   13.50 ( -6.9%)
  ... and 340 more

⚠️  MEDESC CHANGES: 23
    (Possible size/property changes - REVIEW REQUIRED)

  ! 5555555
     OLD: COCA COLA 1L/12
     NEW: COCA COLA 1.25L/12

  ! 6666666
     OLD: LUCKY ME BEEF 70G/30
     NEW: LUCKY ME BEEF 65G/30
  
  ... and 21 more

✓ No new barcodes

✓ Detailed log saved: sync_changes_20260224_114530.txt

================================================================================
SYNC COMPLETE
================================================================================
```

---

## ⚠️ MEDESC Changes - IMPORTANT!

When MEDESC changes, it often means:
- **Size change**: 100ml → 98ml
- **Pack size change**: 12-pack → 10-pack
- **Product reformulation**: New formula
- **Rebranding**: Name change

**Action Required:**
1. Review the change log
2. Update product details in web encoder:
   - Size field
   - Name (if changed)
   - Description (customer-friendly)
3. Product is flagged with `data_quality = 'NEEDS_REVIEW'`

---

## 📊 Price Change Detection

### **How It Works:**

1. Compares current price in database with MP_MER price
2. If different (>₱0.01), creates new price record
3. Marks old price as `is_current = 0`
4. New price marked as `is_current = 1`
5. **Full price history maintained!**

### **Query Price History:**

```sql
SELECT 
    effective_date,
    price_retail,
    is_current
FROM prices
WHERE merkey = '1403473'
ORDER BY effective_date DESC;
```

Result:
```
2026-02-24  45.00  1  ← Current
2026-01-15  42.00  0
2025-12-01  40.00  0
```

---

## 🆕 New Product Detection

### **When New Product Found:**

1. Creates product record with:
   - `description` = MEDESC
   - `data_quality` = 'NEEDS_DESCRIPTION'
   - `needs_enrichment` = 1
   - `active` = 1

2. Creates price record

3. Adds barcodes if present

4. **Encoder can then enrich:**
   - Add proper name
   - Choose brand/category
   - Write customer-friendly description
   - Add size info

---

## 🤖 Automated Daily Sync

### **Setup:**

1. **Edit `daily_sync.py`:**

```python
CONFIG = {
    'mp_mer_path': r'\\YOUR_SERVER\share\MP_MER.FPB',  # UPDATE THIS
    'mp_mer2_path': r'\\YOUR_SERVER\share\MP_MER2.FPB',  # Or None
    'db_path': 'anson_products.db',
    'last_sync_file': '.last_mp_mer_sync.json',
}
```

2. **Test it:**

```bash
python daily_sync.py
```

Output:
```
================================================================================
ANSON SUPERMART - DAILY AUTOMATED SYNC
================================================================================
Started: 2026-02-24 11:45:00

Checking: \\server\share\MP_MER.FPB

File info:
  Last modified: 2026-02-24 02:30:15
  Size: 45,234,567 bytes

🔄 Changes detected - starting sync...

[... sync runs ...]

================================================================================
✓ SYNC SUCCESSFUL
================================================================================
Sync info saved: .last_mp_mer_sync.json
```

3. **Schedule with Windows Task Scheduler:**

Create task:
- **Name**: Anson MP_MER Daily Sync
- **Trigger**: Daily at 3:00 AM
- **Action**: 
  - Program: `python`
  - Arguments: `daily_sync.py`
  - Start in: `D:\Projects\CatalogAutomation\SQLite`
- **Settings**: 
  - Run whether user is logged on or not
  - Run with highest privileges

---

## 📋 Change Log File

After each sync, a detailed log is saved:

**File**: `sync_changes_YYYYMMDD_HHMMSS.txt`

**Contains:**
- All new products
- All price changes with percentages
- All MEDESC changes for review
- Complete audit trail

**Use for:**
- Review MEDESC changes
- Track price trends
- Audit trail for accounting
- Product management decisions

---

## 🔍 Detecting Size Changes

### **Example: Shrinkflation Detection**

When manufacturer reduces size but keeps price same:

```
MEDESC CHANGE:
  MERKEY: 5555555
  OLD: COCA COLA 1L/12
  NEW: COCA COLA 1.25L/12
```

**What happened:**
- Size increased: 1L → 1.25L
- Pack size same: 12 units

**Action:**
1. Update Size field: "1.25 L"
2. Check if price changed
3. Update product description

---

## 📊 Reports You Can Generate

### **1. Biggest Price Increases:**

```sql
SELECT 
    p.merkey,
    p.description,
    pr_old.price_retail as old_price,
    pr_new.price_retail as new_price,
    (pr_new.price_retail - pr_old.price_retail) as increase,
    ((pr_new.price_retail - pr_old.price_retail) / pr_old.price_retail * 100) as pct_increase
FROM products p
JOIN prices pr_new ON p.merkey = pr_new.merkey AND pr_new.is_current = 1
JOIN prices pr_old ON p.merkey = pr_old.merkey 
    AND pr_old.effective_date < pr_new.effective_date
WHERE p.active = 1
ORDER BY pct_increase DESC
LIMIT 20;
```

### **2. Products Needing Review (MEDESC Changed):**

```sql
SELECT 
    merkey,
    description,
    enrichment_notes,
    updated_at
FROM products
WHERE data_quality = 'NEEDS_REVIEW'
ORDER BY updated_at DESC;
```

### **3. New Products This Week:**

```sql
SELECT 
    merkey,
    description,
    created_at
FROM products
WHERE created_at >= date('now', '-7 days')
ORDER BY created_at DESC;
```

---

## ⚙️ Configuration Options

### **In `daily_sync.py`:**

```python
CONFIG = {
    # Network path to MP_MER.FPB (updated nightly)
    'mp_mer_path': r'\\server\share\MP_MER.FPB',
    
    # Optional: MP_MER2.FPB for additional products
    'mp_mer2_path': r'\\server\share\MP_MER2.FPB',
    
    # Database location
    'db_path': 'anson_products.db',
    
    # File to track last sync (auto-created)
    'last_sync_file': '.last_mp_mer_sync.json',
}
```

---

## 🔧 Troubleshooting

### **"File not found"**
- Check network path is accessible
- Verify file exists: `dir \\server\share\MP_MER.FPB`
- Check permissions

### **"No changes detected" but you expect changes**
- Delete `.last_mp_mer_sync.json` to force sync
- Check file modification timestamp

### **Too many MEDESC changes**
- Normal on first sync
- Subsequent syncs should have fewer
- Review in batches

### **Sync runs but no price changes**
- Check MERETP field in MP_MER.FPB
- Verify prices are actually different
- Check price comparison threshold (₱0.01)

---

## 📅 Recommended Schedule

**Daily Sync:**
- **Time**: 3:00 AM (after MP_MER.FPB updated at 2:30 AM)
- **Duration**: 2-5 minutes for 50K products
- **Automatic**: Via Windows Task Scheduler

**Manual Review:**
- **Weekly**: Review MEDESC changes
- **Monthly**: Analyze price trends
- **Quarterly**: Clean up old price history

---

## ✅ Summary

**What You Get:**
- ✅ Automatic price updates
- ✅ New product detection
- ✅ Size/property change alerts
- ✅ Full price history
- ✅ Detailed change logs
- ✅ Audit trail

**Maintenance:**
- ~5 minutes/week to review MEDESC changes
- Everything else is automatic!

**Time Saved:**
- Manual price updates: 2-3 hours/day
- New product entry: 1 hour/day
- **Total: 15-20 hours/week saved!**

---

## 🚀 Next Steps

1. Run first sync manually
2. Review change log
3. Set up daily automation
4. Review MEDESC changes weekly
5. Use web encoder to enrich new products

**Your catalog stays current automatically!** 🎯
