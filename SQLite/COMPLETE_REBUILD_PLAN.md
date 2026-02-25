# 🚀 Anson Supermart - Complete Modern Rebuild Plan
## SQLite-Based Product Management & Ecommerce System

---

## 📋 EXECUTIVE SUMMARY

You have **excellent data** with:
- **MERCH_MASTER.csv**: 66,658 products with rich metadata (Brand, Name, Description, Department, GP%, etc.)
- **Operational_Master_Active_24M.csv**: 72,643 products with sales metrics (Last Sale Date, Transaction Count, Priority)
- **MP_MER.FPB**: Daily updated FoxPro data (prices, inventory)

**RECOMMENDATION**: Build a modern SQLite-based system that:
1. ✅ Syncs daily from MP_MER.FPB (prices, inventory)
2. ✅ Enriches with MERCH_MASTER data (brands, categories, images)
3. ✅ Tracks sales performance from Operational Master
4. ✅ Auto-uploads images to S3
5. ✅ Publishes to multiple channels (Google Sheets, API, ecommerce site)

**This replaces your manual Awesome Table workflow with a professional product information management (PIM) system!**

---

## 🏗️ PROPOSED ARCHITECTURE

```
┌─────────────────────────────────────────────────────────────────┐
│                    DATA SOURCES (Daily Update)                   │
├─────────────────────────────────────────────────────────────────┤
│  MP_MER.FPB     │  MERCH_MASTER    │  TM_*.FPB Sales Files      │
│  (Prices/Stock) │  (Enrichment)    │  (Performance Metrics)     │
└────────┬───────────────┬─────────────────────┬──────────────────┘
         │               │                     │
         └───────────────┼─────────────────────┘
                         ▼
         ┌───────────────────────────────────┐
         │   SQLite Product Database (PIM)   │
         │  • products (master)              │
         │  • brands                         │
         │  • categories                     │
         │  • prices (history)               │
         │  • inventory                      │
         │  • sales_metrics                  │
         │  • images                         │
         └───────────────┬───────────────────┘
                         │
         ┌───────────────┼───────────────────┐
         │               │                   │
         ▼               ▼                   ▼
    ┌────────┐    ┌──────────┐      ┌──────────────┐
    │  AWS   │    │  Google  │      │  Ecommerce   │
    │   S3   │    │  Sheets  │      │     API      │
    │ Images │    │ Awesome  │      │   (Future)   │
    └────────┘    │  Table   │      └──────────────┘
                  └──────────┘
```

---

## 📊 DATABASE SCHEMA (SQLite)

### Core Tables:

```sql
-- 1. PRODUCTS (Master product catalog)
CREATE TABLE products (
    merkey TEXT PRIMARY KEY,
    barcode_primary TEXT,
    description TEXT NOT NULL,
    brand_id INTEGER,
    category_id INTEGER,
    department_id INTEGER,
    name TEXT,
    size TEXT,
    weight_volume TEXT,
    unit_of_measurement TEXT,
    pack_quantity INTEGER,
    gp_percent REAL,
    active BOOLEAN DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (brand_id) REFERENCES brands(id),
    FOREIGN KEY (category_id) REFERENCES categories(id),
    FOREIGN KEY (department_id) REFERENCES departments(id)
);

-- 2. BRANDS
CREATE TABLE brands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    slug TEXT UNIQUE,
    logo_url TEXT
);

-- 3. CATEGORIES (Sub-Departments)
CREATE TABLE categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    department_id INTEGER,
    slug TEXT UNIQUE,
    FOREIGN KEY (department_id) REFERENCES departments(id)
);

-- 4. DEPARTMENTS
CREATE TABLE departments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    slug TEXT UNIQUE
);

-- 5. PRICES (Price history tracking)
CREATE TABLE prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    merkey TEXT NOT NULL,
    price_retail REAL NOT NULL,
    price_pack REAL,
    price_case REAL,
    effective_date DATE NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (merkey) REFERENCES products(merkey)
);

-- 6. INVENTORY (Stock levels from MP_MER)
CREATE TABLE inventory (
    merkey TEXT PRIMARY KEY,
    quantity_on_hand INTEGER DEFAULT 0,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (merkey) REFERENCES products(merkey)
);

-- 7. BARCODES (Multiple barcodes per product)
CREATE TABLE barcodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    merkey TEXT NOT NULL,
    barcode TEXT NOT NULL,
    barcode_type TEXT, -- EAN13, UPC, etc.
    is_primary BOOLEAN DEFAULT 0,
    UNIQUE(merkey, barcode),
    FOREIGN KEY (merkey) REFERENCES products(merkey)
);

-- 8. IMAGES
CREATE TABLE images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    merkey TEXT NOT NULL,
    filename TEXT NOT NULL,
    s3_url TEXT,
    cdn_url TEXT,
    is_primary BOOLEAN DEFAULT 0,
    width INTEGER,
    height INTEGER,
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (merkey) REFERENCES products(merkey)
);

-- 9. SALES_METRICS (From Operational Master & TM files)
CREATE TABLE sales_metrics (
    merkey TEXT PRIMARY KEY,
    last_sale_date DATE,
    txn_count_24m INTEGER DEFAULT 0,
    qty_sum_24m REAL DEFAULT 0,
    txn_count_30d INTEGER DEFAULT 0,
    active_24m BOOLEAN DEFAULT 0,
    priority TEXT, -- PRIORITY_1_DESC, etc.
    velocity_score REAL, -- Calculated metric
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (merkey) REFERENCES products(merkey)
);

-- 10. SYNC_LOG (Track data synchronization)
CREATE TABLE sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sync_type TEXT NOT NULL, -- 'MP_MER', 'SALES', 'IMAGES'
    status TEXT, -- 'SUCCESS', 'FAILED', 'PARTIAL'
    records_processed INTEGER,
    records_updated INTEGER,
    records_added INTEGER,
    error_message TEXT,
    started_at TIMESTAMP,
    completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## 🔄 DATA FLOW & SYNC PROCESS

### Daily Sync Workflow:

```python
# 1. Check if MP_MER.FPB has been updated
if mp_mer_file_changed():
    
    # 2. Import latest prices & inventory
    sync_prices_from_mp_mer()
    
    # 3. Import sales data (if TM files available)
    sync_sales_metrics()
    
    # 4. Calculate velocity scores
    calculate_product_velocity()
    
    # 5. Generate exports
    export_to_google_sheets()  # Awesome Table
    export_to_s3_json()        # API/Ecommerce
    
    # 6. Sync images (only new/missing)
    sync_missing_images_to_s3()
    
    # 7. Log sync
    log_sync_completion()
```

### File Change Detection:

```python
import os
from datetime import datetime

def mp_mer_file_changed(filepath, last_sync_file='.last_sync'):
    """Check if MP_MER.FPB has been modified since last sync"""
    
    current_mtime = os.path.getmtime(filepath)
    
    # Read last sync time
    if os.path.exists(last_sync_file):
        with open(last_sync_file, 'r') as f:
            last_sync = float(f.read().strip())
    else:
        last_sync = 0
    
    # File changed?
    if current_mtime > last_sync:
        # Update last sync time
        with open(last_sync_file, 'w') as f:
            f.write(str(current_mtime))
        return True
    
    return False
```

---

## 📦 IMPLEMENTATION PHASES

### 🎯 PHASE 1: Database Foundation (Week 1)
**Priority: HIGHEST**

**Deliverables:**
1. ✅ SQLite database creation script
2. ✅ Initial data import from MERCH_MASTER.csv
3. ✅ Import from Operational_Master_Active_24M.csv
4. ✅ Import current MP_MER.FPB data
5. ✅ Basic query interface (CLI)

**Scripts to create:**
- `create_database.py` - Creates schema
- `import_merch_master.py` - Imports enrichment data
- `import_operational_master.py` - Imports metrics
- `sync_mp_mer.py` - Syncs from FoxPro

**Output:**
- `anson_products.db` - SQLite database with ~66K products

---

### 🎯 PHASE 2: Daily Sync Automation (Week 2)
**Priority: HIGH**

**Deliverables:**
1. ✅ File change detection system
2. ✅ Automated MP_MER.FPB sync
3. ✅ Price history tracking
4. ✅ Sales metrics update
5. ✅ Windows Task Scheduler setup

**Scripts to create:**
- `daily_sync.py` - Main orchestrator
- `detect_changes.py` - File monitoring
- `update_prices.py` - Price sync
- `update_sales.py` - Sales metrics sync

**Output:**
- Automated daily updates (runs at 2 AM)

---

### 🎯 PHASE 3: AWS S3 Image Management (Week 3)
**Priority: MEDIUM**

**Deliverables:**
1. ✅ S3 bucket setup guide
2. ✅ Image upload automation
3. ✅ Missing image detection
4. ✅ Batch upload script
5. ✅ Image URL mapping

**What you'll need:**
- AWS account (I'll help you set up)
- S3 bucket configuration
- boto3 Python library

**Scripts to create:**
- `setup_s3_bucket.py` - Guided setup
- `upload_images.py` - Batch uploader
- `sync_images.py` - Daily image sync

---

### 🎯 PHASE 4: Export & Publishing (Week 4)
**Priority: MEDIUM**

**Deliverables:**
1. ✅ Google Sheets export (Awesome Table compatible)
2. ✅ JSON API export (for ecommerce)
3. ✅ Product feed generation (Google Merchant, Facebook)
4. ✅ Active product filtering

**Scripts to create:**
- `export_google_sheets.py` - Sheets export
- `export_json_api.py` - JSON export
- `generate_product_feed.py` - Feed generator

---

### 🎯 PHASE 5: Google Sheets API Integration (Optional)
**Priority: LOW**

**Deliverables:**
1. ✅ Google API setup guide
2. ✅ Automated upload to Sheets
3. ✅ Template formatting
4. ✅ Change notifications

**What you'll need:**
- Google Cloud Platform account
- OAuth credentials
- Google Sheets API enabled

---

## 🎨 RECOMMENDED BUILD ORDER

Based on your needs, here's what I recommend building **FIRST**:

### **START WITH: Phase 1 - Database Foundation**

**Why:**
1. Consolidates all your data in one place
2. Enables powerful queries and analysis
3. Foundation for everything else
4. Takes ~2-3 hours to build
5. Immediate value - better than multiple CSVs

**What you'll get immediately:**
```sql
-- Query all active products with sales in last 30 days
SELECT p.*, s.txn_count_30d, pr.price_retail
FROM products p
JOIN sales_metrics s ON p.merkey = s.merkey
JOIN prices pr ON p.merkey = pr.merkey
WHERE s.txn_count_30d > 0
  AND p.active = 1
ORDER BY s.txn_count_30d DESC;

-- Top brands by revenue
SELECT b.name, COUNT(*) as products, SUM(s.qty_sum_24m * pr.price_retail) as revenue
FROM products p
JOIN brands b ON p.brand_id = b.id
JOIN sales_metrics s ON p.merkey = s.merkey
JOIN prices pr ON p.merkey = pr.merkey
GROUP BY b.name
ORDER BY revenue DESC
LIMIT 20;
```

---

## 🔧 QUICK START (Phase 1)

### Step 1: Install Dependencies

```bash
pip install pandas sqlite3 python-dateutil --break-system-packages
```

### Step 2: Create Database

I'll create a script that:
1. Creates the SQLite schema
2. Imports MERCH_MASTER.csv → products, brands, categories
3. Imports Operational_Master → sales_metrics
4. Imports MP_MER.FPB → current prices

### Step 3: Query Your Data

```python
import sqlite3

conn = sqlite3.connect('anson_products.db')
cursor = conn.cursor()

# Get all active products
cursor.execute("""
    SELECT merkey, description, brand, price_retail, txn_count_24m
    FROM products p
    JOIN sales_metrics s ON p.merkey = s.merkey
    WHERE active = 1 AND txn_count_24m > 10
    ORDER BY txn_count_24m DESC
    LIMIT 100
""")

top_products = cursor.fetchall()
```

---

## 💡 KEY BENEFITS OF SQLITE APPROACH

### vs Google Sheets:
✅ **10,000x faster queries**
✅ **No row limits** (66K+ products easily)
✅ **Complex joins** (brands, categories, sales)
✅ **Price history** (track changes over time)
✅ **Atomic updates** (no data corruption)
✅ **Offline access** (no internet needed)
✅ **Free** (no subscription)

### vs FoxPro:
✅ **Modern Python integration**
✅ **Portable** (single .db file)
✅ **SQL queries** (powerful analytics)
✅ **Better data types**
✅ **Unicode support**

### vs Cloud Database:
✅ **No monthly costs**
✅ **No internet dependency**
✅ **Simple backup** (copy .db file)
✅ **Easy to migrate** later if needed

---

## 📊 DATA ENRICHMENT STRATEGY

### From MERCH_MASTER (66,658 products):
- ✅ Brand names
- ✅ Product names
- ✅ Descriptions
- ✅ Sizes
- ✅ Departments & Sub-departments
- ✅ GP%
- ✅ Image filenames
- ✅ Active status

### From Operational_Master (72,643 products):
- ✅ Sales metrics (24 months)
- ✅ Last sale date
- ✅ Transaction counts
- ✅ Quantity sold
- ✅ Priority classification
- ✅ Barcode availability

### From MP_MER.FPB (Daily):
- ✅ Current prices
- ✅ Barcodes
- ✅ Last update dates
- ✅ Supplier codes

### Merge Strategy:
```python
# Priority order for data conflicts:
# 1. MP_MER.FPB (most current - prices, barcodes)
# 2. Operational_Master (sales performance)
# 3. MERCH_MASTER (enrichment data)

# MERKEY is the common key across all sources
```

---

## 🚀 NEXT STEPS

### Decision Points:

**1. Start with Phase 1?** (Database Foundation)
- I'll create the complete database schema
- Import all your existing data
- Give you a powerful query interface
- **Time: 2-3 hours**

**2. AWS S3 Setup?**
- I'll walk you through creating S3 bucket
- Set up permissions
- Configure for image hosting
- **Time: 30 minutes**

**3. Google Sheets API?**
- We can set this up later
- Not critical for Phase 1
- **Time: Defer to Phase 5**

**4. Daily Sync Automation?**
- Build after Phase 1 complete
- Uses database as foundation
- **Time: Phase 2 (Week 2)**

---

## 📁 File Structure for New System

```
D:\Projects\CatalogAutomation\
├── database\
│   ├── anson_products.db          ← SQLite database
│   ├── create_database.py
│   ├── import_merch_master.py
│   └── import_operational_master.py
│
├── sync\
│   ├── daily_sync.py
│   ├── sync_mp_mer.py
│   ├── sync_sales.py
│   └── detect_changes.py
│
├── images\
│   ├── setup_s3.py
│   ├── upload_images.py
│   └── sync_images.py
│
├── export\
│   ├── export_google_sheets.py
│   ├── export_json.py
│   └── export_awesome_table.py
│
└── data\                          ← Source files
    ├── MERCH_MASTER.csv
    ├── Operational_Master_Active_24M.csv
    └── MP_MER.FPB (symlink to network)
```

---

## ✅ MY RECOMMENDATION

**Build in this order:**

1. **Phase 1 (THIS WEEK)**: SQLite database + data import
   - Immediate value
   - Foundation for everything
   - 2-3 hours of work

2. **Phase 2 (NEXT WEEK)**: Daily sync automation
   - Keeps data current
   - Eliminates manual updates
   - 3-4 hours of work

3. **Phase 3 (WEEK 3)**: AWS S3 image management
   - Professional image hosting
   - Fast CDN delivery
   - 2 hours of work

4. **Phase 4 (WEEK 4)**: Export to Awesome Table
   - Replaces current manual process
   - Auto-updates daily
   - 1-2 hours of work

**Total time to complete system: ~2-3 weeks of part-time work**

---

## 🎯 IMMEDIATE ACTION

**Let's start with Phase 1!**

I'll create:
1. Database schema SQL
2. Import scripts for your CSV files
3. MP_MER.FPB sync script
4. Basic query examples

**You'll have a working product database in ~2 hours!**

Ready to proceed? 🚀
