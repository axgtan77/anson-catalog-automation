# Storefront Deprecation Work Plan — "Catalog → Shop" metamorphosis

Goal: strip catalog-era artifacts from the customer-facing storefront so it
reads like a real online supermarket (benchmarked against ever.ph and
builtamart.com, which both show: photo · name-with-size · ONE price ·
add-to-cart, and no supplier/barcode/SKU/class/second-price).

All edits land in `Storefront-dev/` first, verified on :5555, then rsync to
`Storefront/` + restart — same flow as prior deploys. **Nothing here is
destructive to data**: every item is display-layer unless explicitly marked
"data". The underlying fields stay in the DB (still used by search/checkout),
we just stop *showing* them.

Status legend: **Display-only** = template/render change, instantly
reversible. **Data** = changes product rows. **Publish** = changes what the
publish step emits.

---

## Phase 1 — Pure subtraction (low risk, reversible, do first)

### A. Remove the internal "Details" grid
- **What:** Delete the Supplier / Barcode / Class / Merkey key-value section.
- **Why:** Neither competitor shows any of these. Pure back-office data.
- **Where:** `Storefront/templates/product.html` — the
  `<section>` containing `<dl class="shop-product-dlgrid">` ("Details" heading:
  Supplier, Barcode, Class, Merkey blocks).
- **Type:** Display-only. Keep the columns in the DB — `barcode` powers
  search + checkout scanning; `supplier_name`/`class_l*` feed `search_text`.
- **Risk:** None. **Effort:** Trivial (delete one template section).

### B. Remove the "Needs Better Photo" badge
- **What:** Stop emitting the internal QC badge to customer cards.
- **Why:** Internal merchandising signal; no shop shows photo-QC status.
- **Where:** `Storefront/app.py` → `build_badges()` — remove the
  `if row['needs_irl_photo']: badges.append({'label': 'Needs Better Photo'...})`
  block. Keep `needs_irl_photo` in the DB (drives the photo worklist +
  browse-only gating).
- **Type:** Display-only. **Risk:** None. **Effort:** Trivial.

### C. Remove the secondary "piece price" subtext line
- **What:** Drop the duplicate price line (`price_subtext` "Pxx piece price").
- **Why:** Competitors show a single price. The second number adds noise.
- **Where:** `Storefront/app.py` → `build_product_dict()` `price_subtext`
  logic + `build_price_subtext()`; rendered at `_product_card.html` (the
  `product-price-subtext` div) and on `product.html`.
- **Decision:** Keep a subtext ONLY for fresh/per-kg items (their "≈ ₱X/kg"
  range is genuinely useful) — drop it everywhere else.
- **Type:** Display-only. **Risk:** Low. **Effort:** Small.

---

## Phase 2 — The "cold price" on drinks (needs one rule decision)

### D. Suppress the Mode-2 "cold" price as a selling option
- **What:** Stop surfacing the chilled/Mode-2 price as a second selling option
  (the Coke-in-Can "two prices" confusion).
- **Scope (measured):** 173 items currently show a 2nd "pack" price —
  **152 Beverages**, 18 Dairy & Eggs, 3 other. **143 are near-retail markups**
  (`price_pack < price_retail × 1.5`) = the cold-price pattern, not real packs.
  ~30 with a larger gap are likely genuine multi-packs/cases to KEEP.
- **Where (two surfaces):**
  - Card: `_product_card.html` — `product-pack-subtext` line + the pack option
    in the add-to-cart form.
  - Detail: `product.html` — the pack pill in `selling_options`.
  - Source of the option: `build_product_dict()` in `app.py` (builds
    `selling_options` / `pack_option` from `show_pack_on_storefront` +
    `price_pack`).
- **Recommended implementation:** display-layer rule in `build_product_dict()`
  — do NOT emit the pack option when it's a cold markup. Gate:
  `department == Beverages` **OR** `price_pack < price_retail × 1.5`.
  Genuine multi-packs (bigger gap) still show. Self-healing, reversible, no
  data rewrite.
- **DECISION NEEDED:** which rule —
  (a) Beverages only, (b) the ×1.5 markup heuristic (catches cold markups in
  any dept), or (c) both (recommended).
- **Type:** Display-only (recommended) — *or* Data, if you'd rather hard-set
  `show_pack_on_storefront=0` on those items via the encoder/one-time update.
- **Risk:** Low (must not hide real cases — the ×1.5 gate protects them).
  **Effort:** Small.

---

## Phase 3 — Card simplification (redesign, needs design sign-off)

### E. Slim the product card to shop-standard
- **What:** Move from today's dense card (brand · name · dept/category ·
  price · price-subtext · pack-subtext · size · availability) toward the
  competitor pattern (photo · name-with-size · one price · add-to-cart · stock).
- **Where:** `Storefront/templates/_product_card.html`.
- **Proposed keep:** photo, name (optionally prefix brand if not already in
  name), size, one price, availability chip, add-to-cart.
- **Proposed remove from card:** the `product-meta` department/category line
  (redundant with browse context); pack-subtext (folds into D); price-subtext
  (folds into C).
- **Type:** Display-only. **Risk:** Low-medium (visual). **Effort:** Medium —
  worth a quick mockup before/after.
- **DECISION NEEDED:** brand handling — keep a separate brand line, or prefix
  brand into the name like competitors do.

---

## Phase 4 — Publish sl-imming (optional, after display is settled)
Once a field is shown nowhere AND used by nothing (search/checkout), the
publish step can stop emitting it to shrink the serving DB. **Caveat:** most
candidates (barcode, supplier_name, class_l*) are still used by `search_text`
or checkout, so they STAY for now. Revisit only if we later drop them from
search too. Not urgent; no action this round.

---

## Suggested execution order
1. **Phase 1 (A, B, C)** — bundle as one change; pure deletes, ship together.
2. **Phase 2 (D)** — after you pick the rule (recommend "both").
3. **Phase 3 (E)** — after a quick before/after mockup you approve.

Each phase: edit in `Storefront-dev/`, verify on :5555, deploy, confirm live.
All reversible; no catalog/encoder data is destroyed.
