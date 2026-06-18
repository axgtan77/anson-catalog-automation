# Anson Supermart Online Catalog Automation

Automated system to generate active product catalogs from FoxPro database files, similar to your Nielsen automation workflow.

> Source policy: Use `MP_MER.FPB` only. `MP_MER2.FPB` is deprecated. See `Documentation/SOURCE_FILE_POLICY.md`.

## Current Project Phase

The project is now in an operating and governance phase, not an initial build phase.

Current status:

1. Foundation pipeline: completed and running
   - `MP_MER.FPB` sync updates the SQLite catalog
   - encoder workflow is live
   - publish/export flow is established
2. Daily catalog operations: active
   - daily sync
   - encoder review of `NEEDS_REVIEW`
   - missing photo cleanup
   - curated product enrichment in Web Encoder
3. Taxonomy cleanup and governance: active in-progress phase
   - reduce fallback buckets such as `Unknown`, `Others`, and `<NONE>`
   - expand category-to-department override rules
   - strengthen encoder-side review and reporting tools
4. Storefront layer: architecture defined, phased implementation
   - storefront reads from a published catalog layer, not the live encoder tables

## Phase Summary

### Phase 1: Source-to-Catalog Foundation

Status: Completed

- source sync into `anson_products.db`
- active product filtering
- barcode-aware product catalog generation
- daily operational run path established

### Phase 2: Encoder Operations

Status: Live and ongoing

- Web Encoder used for product cleanup and enrichment
- photo workflow active
- audit logging and sync log visibility added
- encoder training and daily operating routine documented

### Phase 3: Taxonomy Normalization and Governance

Status: In progress

- live department naming normalized
- fallback buckets significantly reduced
- remaining work focused on:
  - `Others`
  - `Unknown`
  - `<NONE>`
  - ambiguous category-to-department mappings

Primary reference:
- [ANSON_MASTER_DEPARTMENT_PROPOSAL.md](D:\Projects\CatalogAutomation\Documentation\ANSON_MASTER_DEPARTMENT_PROPOSAL.md)

### Phase 4: Encoder Support and Review Tooling

Status: In progress

- audit log reader added
- sync log reader added
- daily audit summaries added
- per-user and per-action audit breakdowns added

Next likely improvements:
- stronger review queues by department and legacy class
- filters for weak or missing category placement
- tighter encoder QA workflow on source changes

### Phase 5: Storefront Publish Layer

Status: Defined, partial implementation path

- storefront boundary is documented
- public site should read only from `storefront_catalog.db`
- current work is still centered on catalog quality before broader storefront expansion

Primary reference:
- [STOREFRONT_ARCHITECTURE.md](D:\Projects\CatalogAutomation\Documentation\STOREFRONT_ARCHITECTURE.md)

## 🎯 What This Does

**Before:** 
- Manual download of MP_MER.FPB files
- Manual Excel template processing  
- Manual Google Sheets updates
- Manual image uploads to AWS
- Time consuming and error-prone

**After:**
- Single command processes everything
- Automatic filtering of inactive products (5-year activity window)
- Generates clean CSV ready for Google Sheets
- Can be scheduled to run automatically
- **From 72,282 total products → 51,518 active products**

## 📊 Processing Results

From your latest run:
```
Input Files:
  - MP_MER.FPB:  34,999 records → 18,009 active (51.5%)
  - MP_MER2.FPB: 37,283 records → 33,442 active (89.7%)

Output:
  - Total Active Products: 51,518 (after deduplication)
  - Products with Barcodes: 50,649 (98.3%)
  - Average Price: ₱147.03
```

Current online catalog shows only 14,734 products, so this would be a **3.5x increase** in catalog size!

## 🚀 Quick Start

### Method 1: Run Complete Automation

```bash
python3 master_catalog_updater.py
```

This single command:
1. Processes MP_MER.FPB (first database)
2. Processes MP_MER2.FPB (second database)
3. Merges results and removes duplicates
4. Generates `ANSON_ACTIVE_CATALOG.csv`
5. Creates summary report

**Runtime:** ~4 seconds for 72,000 records

### Method 2: Process Single File

```bash
# Process just one FPB file
python3 generate_active_catalog.py /path/to/MP_MER.FPB -o output.csv -y 5
```

Options:
- `-o, --output`: Output CSV filename (default: active_catalog.csv)
- `-y, --years`: Years to consider active (default: 5)

## 📁 File Structure

```
catalog-automation/
├── master_catalog_updater.py      # Main orchestration script
├── generate_active_catalog.py     # Core FPB processor
├── catalog_config.ini             # Configuration file
├── ANSON_ACTIVE_CATALOG.csv       # Final output (generated)
└── README.md                      # This file
```

## ⚙️ How It Works

### 1. Activity Detection (Your Brilliant Idea!)

Uses the `USRDAT` field (format: MMDDYY) to determine if a product is active:

```
Product last accessed on: 2025-08-21 (USRDAT = 082125)
Cutoff date: 2021-02-15 (5 years ago)
Result: ACTIVE ✓

Product last accessed on: 2018-03-10 (USRDAT = 031018)  
Cutoff date: 2021-02-15 (5 years ago)
Result: INACTIVE ✗ (filtered out)
```

### 2. Quality Filters

Additional filters to ensure data quality:
- ✓ Price > ₱0.01 and < ₱50,000
- ✓ Has product description
- ✓ Valid MERKEY (product code)

### 3. Smart Deduplication

When merging MP_MER.FPB and MP_MER2.FPB:
- Uses MERKEY as unique identifier
- Keeps the most recent record (newer USRDAT)
- Ensures no duplicate products in catalog

## 📋 Output Format

The generated CSV has these columns (matching your Google Sheets structure):

```csv
Brand,Name,Description,Size,Price,Photo,Cards,Search,MERKEY,MEDESC,PRICEJP,Barcode,SURKEY,USRDAT
```

Key fields:
- **MERKEY**: Product code (unique identifier)
- **MEDESC**: Product description
- **Price/PRICEJP**: Retail price
- **Barcode**: EAN-13 with asterisks (e.g., *4000001019915*)
- **USRDAT**: Last update date
- **SURKEY**: Supplier code

## 🔧 Customization

### Adjust Activity Window

To change from 5 years to 3 years:

```bash
python3 master_catalog_updater.py  # Edit line: YEARS_ACTIVE = 3
```

Or for single file:
```bash
python3 generate_active_catalog.py MP_MER.FPB -y 3
```

### Filter by Different Criteria

Edit `generate_active_catalog.py` → `is_active_product()` function to add:
- Specific supplier codes (SURKEY)
- Price ranges
- Brand codes (MEBRAC)
- Category filters

### Configuration File

Modify `catalog_config.ini` for:
- File paths
- Filter settings
- Google Sheets integration
- AWS S3 settings

## 📊 Statistics & Reporting

The script automatically generates detailed statistics:

```
PROCESSING STATISTICS
═══════════════════════════════════════════════════════════════
Total records processed:       34,999
Active products (exported):    18,009 ( 51.5%)
Inactive products (old):       16,926 ( 48.4%)
No date / invalid date:            64 (  0.2%)
Filtered by price:                  0

Cutoff date: 2021-02-15
Today's date: 2026-02-14
```

## 🔄 Next Steps: Full Automation

### Step 1: Google Sheets Integration

Create `upload_to_google_sheets.py` to:
- Authenticate with Google Sheets API
- Clear existing data
- Upload new catalog
- Format columns
- Add filters

### Step 2: Image Management

Create `sync_product_images.py` to:
- Detect products without images
- Generate placeholder images
- Upload to AWS S3
- Update Photo URLs in catalog

### Step 3: Scheduled Automation

Set up Windows Task Scheduler or cron job:

```bash
# Run daily at 2 AM
0 2 * * * cd /path/to/scripts && python3 master_catalog_updater.py
```

### Step 4: Change Detection

Create `detect_catalog_changes.py` to:
- Compare current vs previous catalog
- Generate "New Products" report
- Generate "Discontinued Products" report
- Email notifications

## 🎯 Benefits

Compared to your current manual process:

| Task | Manual | Automated | Time Saved |
|------|--------|-----------|------------|
| Download FPB files | 2 min | 0 min | 2 min |
| Process through Excel | 10 min | 0 min | 10 min |
| Filter active products | 15 min | 4 sec | ~15 min |
| Update Google Sheets | 5 min | 10 sec* | ~5 min |
| Upload images | 20 min | 30 sec* | ~20 min |
| **Total** | **~52 min** | **~1 min** | **~51 min saved** |

*Estimated with API automation

**Annual time savings:** ~44 hours (if run weekly)

## 🐛 Troubleshooting

### "Input file not found"
- Check file paths in config or command line
- Ensure FPB files are accessible

### "No active products found"
- Check USRDAT field format
- Verify year filter setting
- Try running with `-y 10` for 10-year window

### Missing barcodes
- Normal - some products don't have MEAN13
- Can filter by: `require_barcode = True` in config

## 📝 Implementation Notes

### Why This Works

Just like your Nielsen automation success:
- **Direct FoxPro reading**: No Excel middleman
- **Proven approach**: Same structure as your Nielsen scripts
- **Clean data flow**: FPB → CSV → Google Sheets
- **Fast execution**: Processes 72K records in 4 seconds

### Database Fields Used

From the 79 fields in MP_MER.FPB, we primarily use:
- MERKEY (product code)
- MEDESC (description)
- MERETP (retail price)
- MEAN13 (barcode)
- USRDAT (last update - YOUR KEY INSIGHT!)
- SURKEY (supplier)

## 🎓 What You've Achieved

1. ✓ Identified perfect activity indicator (USRDAT)
2. ✓ Automated catalog generation from FoxPro
3. ✓ Removed 20,764 inactive products automatically
4. ✓ Ready for Google Sheets automation
5. ✓ Foundation for full end-to-end automation

This is a **much better solution** than trying to process sales transactions!

## 📞 Support

Questions or need help customizing?
- Check code comments in `generate_active_catalog.py`
- Review processing statistics after each run
- Start with single file processing before full automation

---

**Next recommendation:** Set up Google Sheets API integration so you can go from:
```
Double-click → master_catalog_updater.py → Catalog updated online
```

Just like your Nielsen automation turned 60 minutes into 30 seconds! 🚀
