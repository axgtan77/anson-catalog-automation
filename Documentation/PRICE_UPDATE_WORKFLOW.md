# Update Prices for Awesome Table - Complete Workflow

## Quick Overview

You want to:
1. Read latest MP_MER.FPB (and MP_MER2.FPB)
2. Extract current prices (MERETP field)
3. Update your Google Sheets
4. Awesome Table automatically refreshes with new prices

---

## OPTION 1: Simple Price Update (Recommended)

If you already have the products in your Google Sheets and just need to update prices:

### Step 1: Extract Prices from MP_MER.FPB

Run this Python script:

```python
python update_prices_for_awesome_table.py
```

This will:
- Read MP_MER.FPB + MP_MER2.FPB
- Merge the data (keeping newer prices)
- Generate: `AWESOME_TABLE_PRODUCTS_UPDATED.csv`

### Step 2: Update Google Sheets

**Method A: VLOOKUP in Google Sheets**

1. Upload the CSV to Google Drive
2. In your Products sheet, add a helper column
3. Use VLOOKUP to match MERKEY and get new price:

```
=VLOOKUP(K3, IMPORTRANGE("URL_TO_NEW_CSV", "Sheet1!K:G"), 7, FALSE)
```

Where:
- K3 = MERKEY column
- Column 7 = SRP_MODE3 (price column in new CSV)

**Method B: Replace Entire Sheet**

1. Open your Google Sheets
2. Create new tab: "Products_Updated"
3. File → Import → Upload CSV
4. Replace old "Products" sheet with new one
5. Awesome Table automatically updates

---

## OPTION 2: Use Your Existing Catalog Automation

Since you already have the catalog automation from earlier:

### Combine with Sales-Based Filtering

```bash
# Step 1: Generate merged product database
python master_catalog_updater.py

# This creates: ANSON_ACTIVE_CATALOG.csv with latest prices

# Step 2: Filter by sales (optional)
python generate_sales_based_catalog.py \
    "TM_02*.FPB" \
    "ANSON_ACTIVE_CATALOG.csv" \
    -o "AWESOME_TABLE_READY.csv" \
    --min-transactions 2

# Step 3: Upload AWESOME_TABLE_READY.csv to Google Sheets
```

---

## OPTION 3: Manual Price Update (If Small Changes)

If only a few prices changed:

1. Open your Google Sheets
2. Find the products by MERKEY
3. Update columns:
   - Column E: SRP_MODE1 (Case price)
   - Column F: SRP_MODE2 (Pack price)
   - Column G: SRP_MODE3 (Per piece price)
4. Save - Awesome Table auto-refreshes

---

## RECOMMENDED WORKFLOW (Best Practice)

### Monthly Price Update Process

**Every 1st of Month:**

```bash
# 1. Download latest MP_MER.FPB from your system
# 2. Run automation to get active products with updated prices

python generate_sales_based_catalog.py \
    "TM_[LASTMONTH]*.FPB" \
    "MP_MER.FPB" \
    -o "Products_$(date +%Y%m).csv" \
    --min-transactions 2

# 3. Upload to Google Sheets
# 4. Awesome Table refreshes automatically
```

**This gives you:**
- ✅ Latest prices from MP_MER.FPB
- ✅ Only active products (with sales)
- ✅ Filtered by velocity (min 2 transactions)
- ✅ Ready for Awesome Table

---

## DETAILED STEPS: Complete Price Update

### Step 1: Get Latest FoxPro Files

Ensure you have the most recent:
- `MP_MER.FPB` (Product master)
- `MP_MER2.FPB` (Additional products)
- `TM_*.FPB` (Sales transactions - optional for filtering)

### Step 2: Run Price Update Script

```bash
cd /path/to/your/scripts

python update_prices_for_awesome_table.py
```

**Configuration (edit script first):**
```python
MP_MER_PATH = "C:/path/to/MP_MER.FPB"
MP_MER2_PATH = "C:/path/to/MP_MER2.FPB"
OUTPUT_CSV = "AWESOME_TABLE_PRODUCTS_UPDATED.csv"
IMAGE_BASE_URL = "https://s3-ap-southeast-1.amazonaws.com/ansonsupermart.com/images/"
```

### Step 3: Review Output

Open `AWESOME_TABLE_PRODUCTS_UPDATED.csv` in Excel:

Check:
- Column G (SRP_MODE3): Prices look correct?
- Column H (Photo): Image URLs correct?
- Column K (MERKEY): All products present?
- Column L (MEDESC): Descriptions accurate?

### Step 4: Import to Google Sheets

**Option A: Import to New Tab**
1. Google Sheets → File → Import
2. Upload → Select CSV
3. Import location: "Insert new sheet(s)"
4. Separator type: Comma
5. Click "Import data"

**Option B: Replace Existing Products Sheet**
1. Make backup of current Products sheet
2. File → Import
3. Import location: "Replace data at selected cell"
4. Select Cell A1 in Products sheet
5. Click "Import data"

### Step 5: Verify in Awesome Table

1. Open your Awesome Table page
2. Hard refresh (Ctrl+Shift+R)
3. Check that:
   - Prices updated correctly
   - Product count is reasonable
   - Images still loading

### Step 6: Update Products Sheet Configuration

Make sure Row 2 still has correct configuration:

| Column A | Column B | Column C | ... | Column G | Column H | Column I | Column J |
|----------|----------|----------|-----|----------|----------|----------|----------|
| CategoryFilter | StringFilter | None | ... | None | None | CardsContent | StringFilter |

---

## PRICE EXTRACTION FROM MP_MER.FPB

### Key Fields:

From your FoxPro database:

```
MERKEY  = Product code (unique identifier)
MEDESC  = Product description
MERETP  = Retail price (this is what you need!)
MEAN13  = EAN-13 barcode
MEBRAC  = Brand code
USRDAT  = Last update date
```

### Price Mapping for Awesome Table:

```
MERETP → SRP_MODE3 (Per Piece price)
MERETP → SRP_MODE2 (Pack price - same for now)
MERETP → SRP_MODE1 (Case price - same for now)
```

If you have different prices for pack/case, you'd need to add those fields to the script.

---

## AUTOMATION SCRIPT (For Future)

Create `update_monthly_prices.bat`:

```batch
@echo off
REM Monthly Price Update for Awesome Table
echo ================================================
echo Anson Supermart - Monthly Price Update
echo ================================================

REM Set paths
set PYTHON=C:\Python\python.exe
set SCRIPT_DIR=C:\CatalogAutomation
set MP_MER=\\server\share\MP_MER.FPB
set MP_MER2=\\server\share\MP_MER2.FPB
set OUTPUT_DIR=C:\Catalogs

REM Run update
cd /d %SCRIPT_DIR%
%PYTHON% update_prices_for_awesome_table.py

echo.
echo Update complete! 
echo Next: Import the CSV to Google Sheets
echo.

pause
```

---

## FREQUENTLY ASKED QUESTIONS

### Q: How often should I update prices?

**A**: Depends on your business:
- **Weekly**: If prices change frequently
- **Monthly**: Most common (1st of month)
- **On-demand**: When you update prices in FoxPro

### Q: Will this overwrite my existing products?

**A**: Only if you choose "Replace" during import. Use "Insert new sheet" to be safe.

### Q: What if some products are missing after update?

**A**: 
- Check if they have valid prices in MP_MER.FPB
- Check if descriptions are present
- Make sure both MP_MER and MP_MER2 are processed

### Q: Can I keep my custom fields (Brand, Name, etc)?

**A**: Yes! The script generates basic data. You can:
1. Import to new sheet
2. Use VLOOKUP to merge with your existing data
3. Preserve custom formatting

### Q: Images are broken after update

**A**: Check that:
- Image filenames match what's in S3
- IMAGE_BASE_URL is correct
- Column H (Photo) has full URLs

---

## TROUBLESHOOTING

### "File not found" error

- Update paths in script to point to your actual files
- Use absolute paths: `C:\full\path\to\MP_MER.FPB`

### Prices are all zero

- Check MERETP field in MP_MER.FPB
- Some products might not have prices set
- Script skips products with price = 0

### Too many/few products

- Script exports all products with valid price + description
- Use sales-based filtering if you want only active products
- Adjust filters as needed

### Google Sheets import fails

- Check CSV encoding (should be UTF-8)
- Verify file size < 50MB
- Try importing to new sheet first

---

## SUMMARY

**Simplest Path:**

1. Run: `python update_prices_for_awesome_table.py`
2. Import CSV to Google Sheets
3. Done! Awesome Table refreshes automatically

**Best Path (with filtering):**

1. Run: `python generate_sales_based_catalog.py` with latest MP_MER.FPB
2. Get only active products with current prices
3. Import to Google Sheets
4. Perfect catalog with current prices + only selling items

Choose what works best for your workflow!
