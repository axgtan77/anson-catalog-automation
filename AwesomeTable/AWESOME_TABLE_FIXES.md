# Fixing Awesome Table - Images Not Showing & Missing Search Bar

## Problems Identified

### Problem 1: Images Not Displaying (X placeholders)
**Cause**: The Cards template references `{{Photo}}` but your photo URLs are in column H

### Problem 2: Name Search Bar Missing
**Cause**: Row 2, Column B (Name) is set to `None` instead of `StringFilter`

---

## SOLUTIONS

### FIX 1: Images Not Showing

**Issue Found in Template Sheet:**
Your Cards template (Template sheet, Row 2, Column B) contains:
```html
<img class="Big-Img" src="{{Photo}}">
```

This is looking for a field called `{{Photo}}`, which exists in your Products sheet Column H.

**However**, the photo URLs need to be properly formatted.

**Check your Products sheet:**
- Column H should be named "Photo" (Row 1) ✓ Correct
- Column H should contain full URLs ✓ Correct
- Example: `https://s3-ap-southeast-1.amazonaws.com/ansonsupermart.com/images/GARDENIA-CLASSIC-WHITE-BREAD-REGULAR-600G.jpg`

**Why images show as X:**

1. **Image URLs might be broken/404**
   - Test one URL in browser
   - Example: https://s3-ap-southeast-1.amazonaws.com/ansonsupermart.com/images/GARDENIA-CLASSIC-WHITE-BREAD-REGULAR-600G.jpg
   - If it doesn't load, images aren't uploaded to S3 or path is wrong

2. **S3 Bucket Not Public**
   - Your images must be publicly accessible
   - Check S3 bucket permissions

3. **CORS Issue**
   - S3 bucket needs CORS policy to allow Awesome Table to load images

**SOLUTION STEPS:**

#### Step 1: Test Image URLs

1. Open your spreadsheet
2. Copy a Photo URL from Column H (e.g., row 3)
3. Paste in browser
4. **If image loads** → Problem is with Awesome Table configuration
5. **If image doesn't load** → Problem is with S3 (see Step 2)

#### Step 2: Fix S3 Bucket (if images don't load)

**Go to AWS S3 Console:**

1. Find bucket: `ansonsupermart.com`
2. Click on bucket → Permissions tab
3. **Block Public Access**: Turn OFF (Allow public access)
4. **Bucket Policy**: Add this:

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "PublicReadGetObject",
            "Effect": "Allow",
            "Principal": "*",
            "Action": "s3:GetObject",
            "Resource": "arn:aws:s3:::ansonsupermart.com/images/*"
        }
    ]
}
```

5. **CORS Configuration**: Add this:

```json
[
    {
        "AllowedHeaders": ["*"],
        "AllowedMethods": ["GET", "HEAD"],
        "AllowedOrigins": ["*"],
        "ExposeHeaders": []
    }
]
```

6. Save changes

#### Step 3: Verify in Awesome Table

1. Go to Awesome Table editor
2. Refresh/Reload data
3. Images should now appear

---

### FIX 2: Missing Name Search Bar

**Issue Found:**
In Products sheet, Row 2, Column B (Name column):
- Current value: `None`
- Should be: `StringFilter`

**This removes the search box from the Name field!**

**SOLUTION:**

#### Option A: Fix in Google Sheets (Recommended)

1. Open your Google Sheets version (this is the source, not the Excel export)
2. Go to "Products" sheet
3. Find Row 2, Column B (under "Name" header)
4. Change the value from `None` to `StringFilter`
5. Save
6. Awesome Table will automatically update

#### Option B: Fix in Excel then Re-upload

1. Open `Anson_Supermart_Product_List__2_.xlsx` in Excel
2. Go to "Products" sheet
3. Cell B2: Change `None` to `StringFilter`
4. Save file
5. Upload to Google Drive
6. Update Awesome Table data source

---

## COMPLETE FIX CHECKLIST

### For Images:

- [ ] Test one image URL in browser
- [ ] If doesn't load: Fix S3 bucket permissions (see Step 2 above)
- [ ] If loads: Check Awesome Table refresh
- [ ] Verify CORS is configured
- [ ] Check that Column H "Photo" has full URLs
- [ ] Ensure no empty Photo cells (or provide default image)

### For Search Bar:

- [ ] Open Google Sheets (source file)
- [ ] Navigate to "Products" sheet
- [ ] Row 2, Column B: Change `None` → `StringFilter`
- [ ] Save
- [ ] Refresh Awesome Table

### Additional Checks:

- [ ] Verify "Search" column (J) is set to `StringFilter` in Row 2
  - Current: `StringFilter` ✓ Correct
- [ ] Verify "Cards" column (I) is set to `CardsContent` in Row 2
  - Current: `CardsContent` ✓ Correct
- [ ] Check Template sheet hasn't been modified accidentally

---

## VERIFICATION AFTER FIXES

### Test Images:

1. Open your catalog page
2. Products should show images instead of X
3. Click on a product → Popup should show large image
4. If still X, check browser console for errors (F12)

### Test Search:

1. Look for search boxes at top
2. Should see:
   - "Brand" dropdown (CategoryFilter) ✓ Already working
   - **"Search" text box** ← This should appear after fix
3. Type in search box
4. Products should filter

---

## WHY THIS HAPPENED

### Name Search Removal:
- Someone (or an automation) changed Column B Row 2 from `StringFilter` to `None`
- This is the Awesome Table configuration row
- `None` = don't show any filter for this column
- `StringFilter` = show text search box

### Images Not Showing:
**Most likely causes:**
1. S3 bucket permissions changed
2. Images were never uploaded to S3
3. Image filenames in Column H don't match actual files in S3
4. CORS not configured

---

## QUICK TEST COMMANDS

### Test if S3 images are accessible:

Open browser and try these URLs from your sheet:
```
https://s3-ap-southeast-1.amazonaws.com/ansonsupermart.com/images/GARDENIA-CLASSIC-WHITE-BREAD-REGULAR-600G.jpg

https://s3-ap-southeast-1.amazonaws.com/ansonsupermart.com/images/MERVYNS-FRESH-CHICKEN-EGGS-LARGE-12S.jpg

https://s3-ap-southeast-1.amazonaws.com/ansonsupermart.com/images/NESCAFE-CLASSIC-INSTANT-COFFEE-25G.jpg
```

**If ALL return 404 or Access Denied:**
→ S3 bucket issue (follow Step 2 above)

**If SOME work, SOME don't:**
→ Missing image files (need to upload)

**If ALL work in browser but not in Awesome Table:**
→ CORS issue (add CORS policy above)

---

## CURRENT CONFIGURATION SUMMARY

### What's Working ✓
- Brand filter (CategoryFilter)
- Overall search (Column J - StringFilter)
- Card template (has all the HTML/CSS/JS)
- Data structure (18,200 products)

### What's Broken ✗
- Images showing as X (S3 or permissions issue)
- Name search bar missing (Row 2, Col B = None instead of StringFilter)

---

## RECOMMENDED IMMEDIATE ACTIONS

**Priority 1: Fix Name Search (Easy - 30 seconds)**
1. Open Google Sheets
2. Products sheet, Cell B2
3. Change `None` to `StringFilter`
4. Done

**Priority 2: Fix Images (Takes 5-10 minutes)**
1. Test one image URL in browser
2. If broken: Fix S3 permissions (see Step 2)
3. If working: Might just need Awesome Table refresh

---

## CONTACT INFO FOR HELP

If issues persist after these fixes:

**AWS S3 Issues:**
- Check AWS CloudWatch logs
- Verify bucket name: `ansonsupermart.com`
- Verify folder: `/images/`

**Awesome Table Issues:**
- Contact support@awesome-table.com
- Provide your app URL
- Mention: "Images showing as X, search bar missing"

**Google Sheets Issues:**
- Verify you're editing the SOURCE sheet (in Google Drive)
- Not the Excel export

---

## SUMMARY

**Two simple fixes:**

1. **Search Bar**: Change Cell B2 from `None` → `StringFilter`
2. **Images**: Check S3 permissions + CORS

Both are quick fixes that should resolve your issues immediately!

Let me know which fix worked and if you need help with S3 configuration.
