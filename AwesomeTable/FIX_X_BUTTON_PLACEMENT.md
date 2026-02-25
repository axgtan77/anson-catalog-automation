# Fix: X Button Showing on All Cards (Should Only Show in Popup)

## Problem

The close button "X" is showing on every product card in the grid view. It should ONLY appear when a product is clicked and the popup opens.

## Root Cause

In your Cards HTML template, the X button is placed **inside** the main card div, so it appears on all cards. It needs to be:
1. Positioned absolutely in the **top right** corner
2. Only visible when popup is open (`.popUpOpen` class)

---

## SOLUTION: Update Cards HTML Template

### Step 1: Open Google Sheets

Go to your Google Sheets → Template sheet → Cell B2

### Step 2: Replace with This Corrected HTML

Delete the current content and paste this:

```html
<div onclick="popDiv(this);">
  <div class="content" onclick="cancelClick(event);">
    
    <div class="img-preview">
      <img class="Big-Img" src="{{Photo}}">
    </div>

    <div class="container">
      <div class="info">
        <span class="name">{{Name}}</span>
        <span class="details">{{Size}}</span>
        <span class="details">{{Brand}}</span>

        <div class="price-block">
          <div class="price-main">
            ₱{{SRP_MODE3}} <span class="price-unit">(Per Piece)</span>
          </div>
          <div class="price-sub">
            <span class="price-line">Pack: ₱{{SRP_MODE2}}</span>
            <span class="price-sep">|</span>
            <span class="price-line">Case: ₱{{SRP_MODE1}}</span>
          </div>
        </div>

        <div class="description">{{Description}}</div>
      </div>
    </div>

    <div onclick="closePopUp(this);" class="closePopUp">×</div>

  </div>
</div>
```

**Key changes:**
1. ✅ Moved X button to the END (after all content)
2. ✅ Changed "X" to "×" (better close symbol)
3. ✅ CSS will position it top-right and hide it until popup opens

---

## Step 3: Update CSS (Column C, Row 2)

Also update the CSS in **Template sheet, Column C, Row 2**.

Find this section in your CSS:
```css
.closePopup{
        z-index: 1;
        right: 5px;
        padding: 7px;
        color: rgb(68, 68, 68);
        position: absolute;
        cursor:pointer;
        display:none;
}

.popUpOpen .closePopup {
        display:block;
}
```

**Replace it with this improved version:**

```css
.closePopUp {
        position: absolute;
        top: 10px;
        right: 10px;
        z-index: 1001;
        width: 32px;
        height: 32px;
        display: none;
        cursor: pointer;
        font-size: 28px;
        font-weight: 300;
        line-height: 32px;
        text-align: center;
        color: #666;
        background-color: rgba(255, 255, 255, 0.95);
        border-radius: 50%;
        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
        transition: all 0.2s ease;
}

.closePopUp:hover {
        color: #333;
        background-color: #fff;
        transform: scale(1.1);
        box-shadow: 0 3px 12px rgba(0, 0, 0, 0.3);
}

.popUpOpen .closePopUp {
        display: block;
}
```

**This gives you:**
- ✅ Top-right positioning (not top-left)
- ✅ Only shows when popup is open
- ✅ Nice circular button with hover effect
- ✅ Better visibility with white background

---

## Complete Updated CSS (Column C)

For your convenience, here's the COMPLETE CSS for Column C, Row 2:

```css
/*** CSS related to the content displayed***/

.card:not(.card-padding) {
        cursor:pointer;
}
.container {
        text-align:left;
}

.description {
        font-size: 14px;
        line-height: 18px;
        color: #333;
        margin:15px 0 5px 0 !important;
        text-align:justify;
        max-width:500px;
        display:none;
}

.name {
        font-size: 15px;
        margin: 0 0 !important;
        text-transform: uppercase;
        text-decoration: none;
        font-weight: bold;
        color: rgb(50, 122, 247);
        padding-right: 0;
        padding-top: 1px;
        font-style: normal;
        display:inline-block
}

.img-preview{
    display: flex;
    justify-items: center;
}
.img-preview:after {
    content: "";
    display: block;
    margin-bottom: 100%;
    margin-top: 15px;
    width: 0;
}
.img-preview>img{
        height: 100%;
}

.popUp{
        transition: padding 500ms  cubic-bezier(0, 0, 0, 1), background-color 500ms  cubic-bezier(0, 0, 0, 1);
        padding: 10%!important;
        z-index: 1000;
        display: flex;
        justify-content: center;
        align-items: center;
}

.popUp > div{
        transition: box-shadow 500ms cubic-bezier(0, 0, 0, 1);
        box-shadow: rgba(0, 0, 0, 0.4) 0 12px 32px 8px;
        background-color: white;
        cursor:default!important;
}

.cards-container{
        position:relative;
}

.button-container {
        text-align:center;
        float:right;
        display: none;
}
.popUpOpen .button-container {
        display: inline-block;
}

.openButton {
        display:none;

        width: 50px;
        height: 20px;

        padding:0.5em 0.55em 0.8em 0.35em;
        margin-top:6px;
        margin-left:10px;

        border-radius:2px;

        text-decoration:none;
        text-align:center;
        font-size:15px;
        background-color:#00C853;
}

.availability {
        display:none;

        max-width:500px;

        font-size: 14px;
        font-weight:bold;
        line-height: 18px;
        text-align:justify;
        color: #666;
}

[data-availability="Yes"][data-available="yes"] {
        display:inline-block;
}

[data-availability="No"][data-available="no"] {
        display:inline-block;
}


.popUpOpen .cross-relative {
        position:relative;
}

.closePopUp {
        position: absolute;
        top: 10px;
        right: 10px;
        z-index: 1001;
        width: 32px;
        height: 32px;
        display: none;
        cursor: pointer;
        font-size: 28px;
        font-weight: 300;
        line-height: 32px;
        text-align: center;
        color: #666;
        background-color: rgba(255, 255, 255, 0.95);
        border-radius: 50%;
        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
        transition: all 0.2s ease;
}

.closePopUp:hover {
        color: #333;
        background-color: #fff;
        transform: scale(1.1);
        box-shadow: 0 3px 12px rgba(0, 0, 0, 0.3);
}

.popUpOpen .closePopUp {
        display: block;
}

.popUpOpen .description {
        display:block;
}

.content-container{
        position:relative;
}

.Big-Img {
        padding-right:7px;
        width:100%;
        margin-bottom:15px;
}

.closePopUp img {
        width:20px;
}

.details {
        color: #666;
        line-height: 16px;
        font-size: 15px;
        text-decoration: none;
        letter-spacing: -0.5px;
        font-style: normal;
}

.Price {
        text-decoration: none;
        font-size: 20px;
        line-height: 20px;
        color: #444;
        font-weight: bold;
        clear: left;
        font-style: normal;
        display:block;
        margin:7px 0 !important;
}

.info {
        margin:0 7px;
}
.price-block {
  margin-top: 8px;
}

.price-main {
  font-size: 20px;
  font-weight: 700;
  color: #000;
  line-height: 1.1;
}

.price-unit {
  font-size: 12px;
  font-weight: 400;
  color: #555;
  margin-left: 6px;
}

.price-sub {
  margin-top: 6px;
  font-size: 12.5px;
  color: #444;
}

.price-line {
  font-weight: 600;
}

.price-sep {
  margin: 0 8px;
  color: #999;
}
```

---

## Summary of Changes

### What Changed:

1. **HTML (Column B):**
   - Moved `<div class="closePopUp">×</div>` to the end
   - This ensures it's positioned last (top layer)

2. **CSS (Column C):**
   - Added `position: absolute`
   - Changed to `top: 10px; right: 10px;` (was `right: 5px` only)
   - Set `display: none` by default
   - Only shows when `.popUpOpen .closePopUp` class is active
   - Added circular button styling with hover effect

### Result:

- ✅ No "X" on product cards in grid view
- ✅ "X" appears in **top-right corner** when popup opens
- ✅ Nice circular button with hover effect
- ✅ Click X → popup closes
- ✅ Click outside popup → also closes (existing functionality)

---

## Quick Implementation Steps

1. **Open Google Sheets** → Template sheet
2. **Cell B2**: Replace with new HTML (see "Step 2" above)
3. **Cell C2**: Replace with new CSS (complete version above)
4. **Save** (Ctrl+S)
5. **Refresh your Awesome Table page**

The X buttons should disappear from the cards and only appear in the popup!

---

## Alternative: Just Fix the CSS

If you don't want to update the HTML, you can just update the CSS in Column C to hide the X by default:

**Add this to your existing CSS:**
```css
.content > .closePopUp {
        display: none !important;
}

.popUpOpen .content > .closePopUp {
        display: block !important;
        position: absolute;
        top: 10px;
        right: 10px;
}
```

But I recommend the full update above for the best result!
