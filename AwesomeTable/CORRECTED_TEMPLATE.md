# CORRECTED AWESOME TABLE TEMPLATE

## Problem Identified

Your Cards template has **double-double quotes** (`""`) which is breaking the HTML rendering.

**Incorrect (current):**
```html
<img class=""Big-Img"" src=""{{Photo}}"">
```

**Correct (should be):**
```html
<img class="Big-Img" src="{{Photo}}">
```

---

## SOLUTION: Replace Template Sheet Content

### Step 1: Open Google Sheets

Open your source Google Sheets file (NOT the Excel export)

### Step 2: Go to Template Sheet

Navigate to the "Template" sheet

### Step 3: Replace Column B, Row 2 (Cards Template)

**Delete the current content in cell B2** and replace with this corrected HTML:

```html
<div onclick="popDiv(this);">
  <div class="content" onclick="cancelClick(event);">
    <div onclick="closePopUp(this);" class="closePopUp">X</div>

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
  </div>
</div>
```

---

## COMPLETE TEMPLATE SHEET CONFIGURATION

For reference, here's what each column in your Template sheet should contain:

### Template Sheet - Row 1 (Headers):

| Column A | Column B | Column C | Column D | Column E | Column F |
|----------|----------|----------|----------|----------|----------|
| Search | Cards | <style> | <style> | <script> | <style> |

### Template Sheet - Row 2 (Content):

#### Column A - Search Config:
```
{{Name}} {{Brand}} {{Description}} {{Barcode}}
```

#### Column B - Cards HTML:
```html
<div onclick="popDiv(this);">
  <div class="content" onclick="cancelClick(event);">
    <div onclick="closePopUp(this);" class="closePopUp">X</div>

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
  </div>
</div>
```

#### Column C - Content CSS:
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

#### Column D - Awesome Table CSS:
```css
/*** CSS for Awesome Table Gadget***/
/* loader progress bar */
.loader {
        background-color:#437af8;
}

/*** Configuration of FILTERS ***/

/** Labels of filters **/
.google-visualization-controls-label {
        color:#333;
        font-weight: 500!important;
}

/** StringFilter **/
.google-visualization-controls-stringfilter INPUT {
        border:1px solid #d9d9d9!important;
        color:#222;
}
.google-visualization-controls-stringfilter INPUT:hover {
        border:1px solid #b9b9b9;
        border-top:1px solid #a0a0a0;
}
.google-visualization-controls-stringfilter INPUT:focus {
        border:1px solid #4d90fe;
}

/* CategoryFilter & csvFilter hovered item in the dropDown */
.charts-menuitem-highlight {
        background-color:rgb(50, 122, 247)!important;
        border-color:rgb(50, 122, 247)!important;
        color:#FFF!important;
}
```

#### Column E - JavaScript:
```javascript
function popDiv(e) {

        if (e.classList.contains('popUp') == false) {
                if (window.innerWidth < 480) {
                        var elem = e.querySelector('.openButton');

                        if (elem.getAttribute('href') == '')
                                return;

                        elem.click();
                        return;
                }

                var rect = e.getBoundingClientRect();

                var style = 'position: fixed;' +
                            'top: 0;bottom: 0;left: 0;right: 0;' +
                            'padding:' +
                            (rect.top > 0 ? rect.top : 0) + 'px ' +
                            (window.innerWidth > rect.right ? window.innerWidth - rect.right : 0) + 'px ' +
                            (window.innerHeight > rect.bottom ? window.innerHeight - rect.bottom : 0) + 'px ' +
                            (rect.left > 0 ? rect.left : 0) + 'px';

                e.setAttribute('style', style);

                e.parentElement.setAttribute('style', 'height:' + rect.height + 'px');
                e.querySelector('div').setAttribute('style', '');

                clearTimeout(e.timeout);
                e.timeout = setTimeout(function () {
                        e.classList.add('popUp');
                }, 20);
                
                e.classList.add('popUpOpen');
        } else {
                e.setAttribute('style', e.getAttribute('style') + '!important;background-Color:transparent;');
                e.querySelector('div').setAttribute('style', 'box-shadow: rgba(0, 0, 0, 0.4) 0 0!important');

                e.classList.remove('popUpOpen');
                
                clearTimeout(e.timeout);
                e.timeout = setTimeout(function () {
                        e.setAttribute('style', 'padding: 0');
                        e.parentElement.setAttribute('style', '');
                        e.classList.remove('popUp');
                        e.querySelector('div').setAttribute('style', '');
                }, 500);
        }
}

function cancelClick(ev, e) {
        if (e.parentElement.classList.contains('popUp') == true) {

                ev = ev || window.event;
                if (ev.stopPropagation)
                        ev.stopPropagation();
                if (ev.cancelBubble != null)
                        ev.cancelBubble = true;

        }
}

function closePopUp(e) {
        while (e.classList.contains('popUp') == false) {
                if (e.classList.contains('card-content') || e == document.body)
                        return;

                e = e.parentElement;
        }

        popDiv(e);
}
```

#### Column F - Card Layout CSS:
```css
.awesomeTable-visualization-cards .card-content {
  margin: 0 -1px -1px 0;
  padding: 0;
  box-shadow: rgba(0, 0, 0, 0.4) 0 0;
  border: solid 1px rgba(189, 189, 189, 0.4);
  display:flex;
}

.card-content>div>div {
  padding: 15px;
}

.awesomeTable-visualization-cards .cards-container {
  margin-right: 1px;
}

.awesomeTable-visualization-cards .card-content:hover {
  background-color: white;
  box-shadow: rgba(0, 0, 0, 0.4) 0 0;
}

.card-content>div:not(.popUp):hover{
  box-shadow: rgba(0, 0, 0, 0.4) 0 8px 16px 0;
}

.card-content>div {
    transition: box-shadow 500ms cubic-bezier(0.22, 0.84, 0.57, 1);
}

div.popUp {
    transition: box-shadow 500ms cubic-bezier(0.22, 0.84, 0.57, 1), padding 500ms  cubic-bezier(0, 0, 0, 1);
}
```

---

## QUICK FIX STEPS

1. **Open Google Sheets** (your source file, not Excel)
2. **Go to Template sheet**
3. **Click on cell B2** (Cards column, row 2)
4. **Delete all content**
5. **Copy and paste** the corrected HTML from "Column B - Cards HTML" above
6. **Save** (Ctrl+S or Cmd+S)
7. **Refresh your Awesome Table page**

---

## WHAT WAS WRONG

**The double-quote problem:**

When you export from Google Sheets to Excel and back, or when copying/pasting, sometimes quotes get escaped incorrectly.

**Your code had:**
```html
<img class=""Big-Img"" src=""{{Photo}}"">
```

**Should be:**
```html
<img class="Big-Img" src="{{Photo}}">
```

The `""` (double-double quotes) breaks the HTML attribute parsing, so:
- Images don't load
- Popups don't work
- Styling breaks

---

## AFTER YOU FIX

You should see:
1. ✓ Product images display in cards
2. ✓ Click on product → Popup opens
3. ✓ Large image shows in popup
4. ✓ Product details display
5. ✓ Click X or outside popup → Closes

---

## IF STILL NOT WORKING

### Debug Checklist:

1. **Check browser console** (F12 → Console tab)
   - Look for JavaScript errors
   - Look for image loading errors

2. **Verify Photo column data**
   - Column H has full URLs
   - URLs are correct format
   - No empty cells

3. **Test one product manually**
   - Look at HTML source (View Page Source)
   - Find the img tag
   - Check if src="{{Photo}}" got replaced with actual URL

4. **Clear cache**
   - Hard refresh: Ctrl+Shift+R (Windows) or Cmd+Shift+R (Mac)
   - Or clear browser cache completely

---

## ALTERNATIVE: DOWNLOAD FIXED TEMPLATE

If copy-paste doesn't work well in Google Sheets, I can create a clean Excel file with the corrected template that you can import.

Let me know if you need this!
