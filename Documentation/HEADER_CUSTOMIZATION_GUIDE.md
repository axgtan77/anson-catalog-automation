# Customizing Awesome Table Header - Anson Supermart

## Current Header Analysis

**What you have now:**
- Green/teal banner (#009688 or similar)
- Small generic icon on left
- "Anson Supermart Product List" text
- Help, Language, and Sign In buttons on right
- Shows "1 - 18 / 18200" items count

**What you want:**
- Red banner matching logo (#E31E24 or your brand color)
- Your Anson Supermart logo
- Same clean layout

---

## OPTION 1: Customize in Awesome Table Dashboard (Easiest)

### Step 1: Access Your Awesome Table Settings

1. Go to your Awesome Table dashboard: https://app.awesome-table.com
2. Sign in with your account
3. Find your "Anson Supermart Product List" app
4. Click on it to edit

### Step 2: Customize Brand Colors

**Location**: App Settings → Appearance → Theme

**Changes to make:**
```
Primary Color: #E31E24 (your red from logo)
Header Background: #E31E24
Header Text Color: #FFFFFF (white)
Accent Color: #C41E3A (darker red for hover states)
```

### Step 3: Upload Your Logo

**Location**: App Settings → Appearance → Logo

1. Click "Upload Logo"
2. Select your Anson logo file (recommended: PNG with transparent background)
3. **Optimal size**: 200px wide × 60px tall (or maintain aspect ratio)
4. **Format**: PNG or SVG preferred

**Logo requirements:**
- Max file size: Usually 2MB
- Recommended: Transparent background
- Dimensions: 200-300px width works well

### Step 4: Customize Header Text

**Location**: App Settings → General → App Name

- Current: "Anson Supermart Product List"
- Suggestion: Keep it or change to just "Product Catalog" or "Anson Online Store"

### Step 5: Preview and Publish

1. Click "Preview" to see changes
2. If satisfied, click "Publish"
3. Changes appear immediately on your public URL

---

## OPTION 2: Custom CSS Styling (Advanced)

If Awesome Table allows custom CSS injection:

### Access Custom CSS

**Location**: Settings → Advanced → Custom CSS (if available)

### CSS Code to Apply

```css
/* Change header background to red */
.at-header,
.at-app-bar {
    background-color: #E31E24 !important;
}

/* Make header text white */
.at-header .at-title,
.at-header a,
.at-header button {
    color: #FFFFFF !important;
}

/* Style the logo area */
.at-header .at-logo {
    max-height: 50px;
    width: auto;
}

/* Adjust header height if needed */
.at-header {
    min-height: 70px;
    padding: 10px 20px;
}

/* Remove or customize the generic icon */
.at-header .at-icon {
    display: none; /* or customize */
}

/* Ensure buttons are visible on red background */
.at-header button {
    border: 1px solid white;
}

.at-header button:hover {
    background-color: rgba(255, 255, 255, 0.1);
}
```

---

## OPTION 3: Using Google Sheets + Awesome Table Settings

If you're managing content through Google Sheets:

### Step 1: Prepare Your Spreadsheet

Your Google Sheet should have a special config sheet or settings area where you can specify:

```
Setting Name        | Value
--------------------|------------------
AppName             | Anson Supermart
LogoURL             | https://yoursite.com/logo.png
PrimaryColor        | #E31E24
HeaderBackground    | #E31E24
HeaderTextColor     | #FFFFFF
```

### Step 2: Reference in Awesome Table

When connecting your sheet, make sure Awesome Table reads these settings.

---

## OPTION 4: Host Logo and Link It

### Where to Host Your Logo

**Option A: Google Drive** (Free, Easy)
1. Upload logo to Google Drive
2. Right-click → Get Link → Set to "Anyone with link can view"
3. Use that URL in Awesome Table logo settings

**Option B: AWS S3** (You already use this for product images)
1. Upload to your S3 bucket: `s3://your-bucket/branding/anson-logo.png`
2. Make it public
3. Use URL: `https://your-bucket.s3.amazonaws.com/branding/anson-logo.png`

**Option C: Direct in Google Sheets**
- Use a URL column in your config
- Awesome Table can pull header images from sheets

---

## RECOMMENDED SETUP

### Header Layout Design

```
┌─────────────────────────────────────────────────────────┐
│  [Logo]  Anson Supermart          HELP  EN  SIGN IN    │  ← Red background
└─────────────────────────────────────────────────────────┘
│                                                          │
│  [Search]  [Brand ▼]  [Barcode]                        │
│                                                          │
│  1 - 18 / 18200                                    ◄ ►  │
└─────────────────────────────────────────────────────────┘
```

### Exact Colors to Use

From your logo:
- **Primary Red**: `#E31E24`
- **White Text**: `#FFFFFF`
- **Hover State**: `#C41E3A` (darker red)

---

## ALTERNATE SUGGESTION: Move Branding to Page Body

You mentioned: "Or do you suggest that these be shown on the body of the page?"

### Option: Banner Above Products

Add a branded banner section at the top of the product grid:

```
┌─────────────────────────────────────────────────────────┐
│                  [Teal Awesome Table Header]            │
└─────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────┐
│  [Large Anson Logo]    SUPERMART & DEPARTMENT STORE     │  ← Your red banner
│                        Fresh Quality Products           │
└─────────────────────────────────────────────────────────┘
│  [Search]  [Filters]                                    │
│                                                          │
│  [Product Grid]                                          │
```

**Pros:**
- More prominent branding
- Larger logo display
- Can add tagline/messaging
- Awesome Table header remains functional

**Cons:**
- Takes up more vertical space
- Less clean/integrated

**How to implement:**
1. In Awesome Table, add a "Header HTML" section (if available)
2. Or add as first "card" in your grid
3. Style to be full-width and stand out

---

## QUICK START RECOMMENDATIONS

### Immediate Actions (Today):

1. **Log into Awesome Table dashboard**
2. **Navigate to Appearance settings**
3. **Upload your logo PNG file**
4. **Change primary color to #E31E24 (red)**
5. **Preview and publish**

### If Settings Not Available:

**Contact Awesome Table Support:**
- Email: support@awesome-table.com
- Ask about: "Custom branding options for header"
- Request: Logo upload, color customization
- Plan: May require paid plan for full customization

### Check Your Plan:

Awesome Table has different plans:
- **Free**: Limited customization
- **Pro**: More branding options
- **Enterprise**: Full white-labeling

You might need to upgrade to get full header control.

---

## LOGO FILE PREPARATION

### Create Proper Logo Files

**For Web Header** (Do this before uploading):

1. **Version 1: Horizontal Logo** (Recommended for header)
   - Dimensions: 200px W × 50px H
   - Background: Transparent PNG
   - Use: Header left side

2. **Version 2: Square Icon** (For mobile/small screens)
   - Dimensions: 64px × 64px
   - Background: Transparent PNG
   - Use: Mobile view, favicon

3. **Version 3: Full Banner** (If using body banner)
   - Dimensions: 1200px W × 150px H
   - Background: Red #E31E24
   - Text: White "SUPERMART & DEPARTMENT STORE"

### Where to Create These

- **Canva** (free online): canva.com
- **GIMP** (free software): gimp.org
- **Photoshop** (if you have it)
- **Fiverr** ($5-20): Have someone do it professionally

---

## NEXT STEPS AFTER HEADER UPDATE

Once header is customized:

1. **Update Favicon**
   - Small icon that appears in browser tab
   - Upload 32×32 or 64×64 PNG of your logo

2. **Social Sharing Images**
   - When people share your catalog link
   - 1200×630 image with your branding

3. **Mobile Optimization**
   - Test how header looks on phone
   - May need different logo size for mobile

4. **Brand Consistency**
   - Use same red throughout filters/buttons
   - Consistent fonts (if Awesome Table allows)

---

## SUMMARY

**Easiest Path:**
1. Log into Awesome Table
2. Go to Appearance settings
3. Upload logo + change colors
4. Done in 5 minutes

**If No Settings Available:**
1. Contact Awesome Table support
2. May need plan upgrade
3. Or use custom CSS if allowed

**Alternative:**
- Add branded banner in page body
- Keep Awesome Table header as-is

**Recommended:** Try Option 1 first (Awesome Table settings). It's the proper way to do it and will look most professional.

Let me know if you can access the Awesome Table dashboard settings and I can guide you through the exact steps!
