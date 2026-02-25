# Phase 1: SQLite Database - Quick Start Guide

## 🚀 What You're Building

A powerful SQLite product database that consolidates:
- ✅ 66,658 products from MERCH_MASTER (with brands, categories, descriptions)
- ✅ 72,643 sales metrics from Operational Master (24-month performance)
- ✅ Current prices from MP_MER.FPB (daily updates)

**All in ONE database - 10,000x faster than Google Sheets!**

---

## 📁 File Setup

**1. Download these files to `D:\Projects\CatalogAutomation\SQLite\`:**
- `schema.sql` ⭐
- `create_database.py` ⭐
- `import_merch_master.py` ⭐
- `import_operational_master.py` ⭐

**2. Copy your data files to `D:\Projects\CatalogAutomation\Data\`:**
- `MERCH_MASTER.csv` (you already uploaded this)
- `Operational_Master_Active_24M.csv` (you already uploaded this)

---

## ⚡ Installation (3 Steps - 10 Minutes Total)

### Step 1: Create the Database (1 minute)

```bash
cd D:\Projects\CatalogAutomation\SQLite

python create_database.py
```

**Expected output:**
```
Tables created (10):
  ✓ products
  ✓ brands
  ✓ categories
  ✓ departments
  ✓ prices
  ✓ inventory
  ✓ barcodes
  ✓ images
  ✓ sales_metrics
  ✓ sync_log

Views created (4):
  ✓ v_active_products
  ✓ v_products_missing_images
  ✓ v_top_sellers_by_category
  ✓ v_stats_by_department

Database file: D:\Projects\CatalogAutomation\SQLite\anson_products.db
✓ Database ready for data import!
```

**Result**: Empty database with complete schema created ✅

---

### Step 2: Import MERCH_MASTER (5 minutes)

```bash
python import_merch_master.py
```

**Expected output:**
```
Step 1: Importing Departments
  ✓ Imported 23 departments

Step 2: Importing Categories  
  ✓ Imported 156 categories

Step 3: Importing Brands
  ✓ Imported 1,247 brands

Step 4: Importing Products
  ✓ Processed 66,658 products

IMPORT SUMMARY
  Departments:     23 added
  Categories:      156 added
  Brands:          1,247 added
  Products added:  66,658
  Images:          45,234 added

✓ MERCH_MASTER import complete!
```

**Result**: 66K products with brands, categories, descriptions imported ✅

---

### Step 3: Import Operational Master (3 minutes)

```bash
python import_operational_master.py
```

**Expected output:**
```
Importing Sales Metrics
  ✓ Processed 72,643 sales metrics

Calculating Rankings
  ✓ Rankings calculated

IMPORT SUMMARY
  Sales metrics added:   72,643
  Barcodes added:        15,234

Database Statistics:
  Total products with metrics: 72,643
  Active in last 24 months:    45,123
  Average velocity score:      12.34

✓ Operational Master import complete!
```

**Result**: Sales metrics for 72K products imported ✅

---

## 🎉 DONE! You Now Have a Working Database!

**Database location**: `D:\Projects\CatalogAutomation\SQLite\anson_products.db`

**Contains**:
- 66,658 products (from MERCH_MASTER)
- 72,643 sales metrics (from Operational Master)
- 1,247 brands
- 156 categories
- 23 departments
- 45,234 product images mapped
- Complete 24-month sales history

---

## 💡 What You Can Do NOW

### Query Your Data (Examples)

```python
import sqlite3

conn = sqlite3.connect('anson_products.db')
cursor = conn.cursor()

# Top 10 best sellers
cursor.execute("""
    SELECT p.description, b.name as brand, s.txn_count_24m, pr.price_retail
    FROM products p
    JOIN brands b ON p.brand_id = b.id
    JOIN sales_metrics s ON p.merkey = s.merkey
    LEFT JOIN prices pr ON p.merkey = pr.merkey AND pr.is_current = 1
    WHERE s.txn_count_24m > 0
    ORDER BY s.txn_count_24m DESC
    LIMIT 10
""")

for row in cursor.fetchall():
    print(f"{row[0]}: {row[2]:,} transactions")
```

### Use Pre-Built Views

```sql
-- All active products with full details
SELECT * FROM v_active_products LIMIT 100;

-- Products missing images
SELECT * FROM v_products_missing_images LIMIT 100;

-- Top sellers by category
SELECT * FROM v_top_sellers_by_category;

-- Department statistics
SELECT * FROM v_stats_by_department;
```

---

## 🔧 Troubleshooting

### "ModuleNotFoundError: No module named 'sqlite3'"
Already built into Python - should not happen

### "File not found: ../Data/MERCH_MASTER.csv"
- Check file is in `D:\Projects\CatalogAutomation\Data\`
- Or update path in script

### Database already exists warning
- Backup is created automatically
- Type 'yes' to proceed with new database

### Import takes too long
- Normal for 66K products
- MERCH_MASTER: ~5 minutes
- Operational Master: ~3 minutes

---

## ⏭️ Next Steps

**After Phase 1 is complete:**

1. **Sync current prices** from MP_MER.FPB
2. **Set up daily automation** (Phase 2)
3. **Export to Awesome Table** (Phase 4)
4. **Upload images to S3** (Phase 3)

---

## 📊 Database Size

**After import:**
- File size: ~150-200 MB
- 66K products
- 72K sales records
- Lightning-fast queries (milliseconds)

**vs Google Sheets:**
- Sheets: 10M cell limit, slow with 66K products
- SQLite: No limits, instant queries, offline access

---

## ✅ Verification Checklist

After imports complete, verify:

- [ ] `anson_products.db` file exists (150-200 MB)
- [ ] Can open database in SQLite viewer
- [ ] Products table has ~66K rows
- [ ] Sales_metrics table has ~72K rows
- [ ] Brands table has ~1,200 rows
- [ ] Views work (v_active_products)

---

## 🎯 What This Achieves

**Before (Current System):**
- Multiple CSV files scattered
- Manual updates required
- Google Sheets limitations
- Slow queries
- No price history

**After (SQLite Database):**
- Single source of truth
- Ready for automation
- No size limits
- Lightning fast
- Complete history tracking
- Foundation for ecommerce

---

## 📞 Need Help?

**Common commands:**

```bash
# Start over (delete database)
del anson_products.db

# Re-create empty database
python create_database.py

# Check database size
dir anson_products.db
```

**Check sync logs:**

```python
cursor.execute("SELECT * FROM sync_log ORDER BY completed_at DESC LIMIT 5")
for row in cursor.fetchall():
    print(row)
```

---

## 🚀 Ready to Proceed?

**Time investment**: 10-15 minutes
**Result**: Professional product database with 66K+ products

**Let's build it!** 

Download the 4 files above, copy to SQLite folder, and run the 3 commands. You'll have a working database in minutes! 📊
