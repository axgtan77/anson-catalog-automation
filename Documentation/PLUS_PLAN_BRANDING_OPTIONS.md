# Adding Branded Banner to Awesome Table (PLUS Plan Workaround)

Since the PLUS plan doesn't support full header customization, here's how to add your Anson Supermart branding **inside the content area** without upgrading.

## Solution: Add Banner Row in Google Sheets

### Step 1: Create Banner Image

**Option A: Use Canva (Free & Easy)**

1. Go to canva.com
2. Create new design → Custom size: **1200px × 200px**
3. Set background color: **Red (#E31E24)**
4. Add your Anson logo (white version if you have it)
5. Add text: "ANSON SUPERMART & DEPARTMENT STORE"
   - Font: Bold, white color
   - Size: 48-60pt
6. Download as PNG

**Option B: Simple Text Banner**

If you don't want to create an image, just use text in your sheet:

```
Row 1 of your Google Sheet:
---------------------------------------------
| 🏪 ANSON SUPERMART & DEPARTMENT STORE    |
| Fresh Quality Products at Great Prices   |
---------------------------------------------
```

### Step 2: Add to Google Sheets

**Method A: Image Banner**

1. Open your Google Sheet that feeds Awesome Table
2. Insert a new row at the very top
3. Merge cells across all columns (A1:Z1 or however many columns you have)
4. Insert → Image → Image in cell
5. Upload your banner image
6. Set image to "Fill cell"

**Method B: Styled Text Banner**

1. Insert new row at top
2. Merge cells across all columns
3. Type: "🏪 ANSON SUPERMART & DEPARTMENT STORE"
4. Format:
   - Background color: Red (#E31E24)
   - Text color: White
   - Font size: 24pt
   - Bold
   - Center alignment
   - Increase row height to 60-80px

### Step 3: Make Row Stand Out in Awesome Table

In your Google Sheet, add these special formatting:

**Cell A1** (first column of banner row):
- Add a special identifier: `BANNER_ROW`
- Or leave it with just your company name

**Configure Awesome Table to display it:**

1. In Awesome Table editor
2. Look for "Card view" or "List view" settings
3. Enable "Show header row" or similar
4. The banner will appear at the top of your catalog

---

## Alternative: Add Banner as Featured Section

### Create a "Featured" Category

In your Google Sheet:

1. Add a column called "Category" (if you don't have one)
2. Create special products with Category = "HEADER"
3. Make these rows show your branding info:

Example rows:

```
MERKEY | Name                | Description              | Price | Category | Image
-------|---------------------|--------------------------|-------|----------|----------------
BANNER | Anson Supermart     | Quality Products Daily   | 0.00  | HEADER   | [logo-url]
```

4. In Awesome Table, configure to show HEADER category first
5. Style it to be full-width and prominent

---

## CSS Injection Method (If Allowed on PLUS)

Check if your PLUS plan allows custom CSS:

**Settings → Advanced → Custom CSS** (if available)

If yes, add this code:

```css
/* Add red top border to mimic header */
.at-app {
    border-top: 60px solid #E31E24;
    position: relative;
}

/* Add logo/text in the border area */
.at-app::before {
    content: "🏪 ANSON SUPERMART & DEPARTMENT STORE";
    position: absolute;
    top: -50px;
    left: 20px;
    color: white;
    font-size: 24px;
    font-weight: bold;
}

/* Or use background image for logo */
.at-app {
    background-image: url('https://your-logo-url.png');
    background-repeat: no-repeat;
    background-position: 20px 10px;
    background-size: 200px auto;
}
```

**Note:** Custom CSS may not be available on PLUS plan.

---

## Recommended Approach for PLUS Plan

### Simple 3-Step Solution:

**1. Create a Text Banner in Google Sheets**
   - Row 1: Merge all columns
   - Background: Red
   - Text: "ANSON SUPERMART & DEPARTMENT STORE"
   - Height: 60px

**2. Add Your Logo URL**
   - Upload logo to Google Drive or AWS S3
   - Use image URL in the merged cell
   - Or add as product image in special row

**3. Style in Awesome Table**
   - Use the row as visual separator
   - Configure to appear at top

**Result:** 
- Branded appearance without upgrading
- Cost: $0 (stays on PLUS plan)
- Time: 10-15 minutes to set up

---

## Cost-Benefit Analysis

### Option A: Upgrade to PRO
- **Cost:** +$60/month = $720/year
- **Benefit:** Full header customization, logo upload
- **Worth it if:** You need other PRO features too

### Option B: DIY Banner in Content
- **Cost:** $0
- **Benefit:** Visible branding, keeps PLUS plan
- **Worth it if:** Budget-conscious, willing to work around limits

### Option C: Upgrade Only for Launch
- **Cost:** $60 for 1 month
- **Process:** 
  1. Upgrade to PRO for one month
  2. Set up perfect header/logo
  3. Downgrade back to PLUS
  4. Header *might* stay (check if it persists)
- **Risk:** Header might reset when downgrading

---

## What I Recommend

**For You (PLUS Plan User):**

**Best Approach:** Add banner in Google Sheets
- Creates branded look without upgrade
- Free solution
- Takes 15 minutes
- Still looks professional

**Steps:**
1. Create 1200×200px red banner with your logo in Canva
2. Add as image in Row 1 of Google Sheet (merged cells)
3. In Awesome Table, make sure it displays at top
4. Done!

**If Budget Allows:** Consider PRO upgrade
- Only if you also want:
  - Private sheets (important for you?)
  - Remove "Awesome Table" branding
  - More advanced features

**For just logo/colors alone:** Not worth $720/year IMO. The in-sheet banner works well.

---

## Next Steps

1. **Try the free solution first** (banner in sheets)
2. **See if you like the look**
3. **If not satisfied, then consider PRO upgrade**
4. **Or wait until you need other PRO features anyway**

Let me know which approach you want to take and I can help you implement it!
