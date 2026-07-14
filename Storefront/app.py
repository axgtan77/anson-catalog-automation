from __future__ import annotations

import os
import sqlite3
import hmac
import hashlib
import json
import secrets
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode, quote
from functools import wraps

from flask import Flask, abort, flash, g, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

import search_engine

BASE_DIR = Path(__file__).resolve().parent
STORE_DB_PATH = Path(os.environ.get('ANSON_STOREFRONT_DB', str(BASE_DIR / 'storefront_catalog.db')))
STOREFRONT_HOST = os.environ.get('STOREFRONT_HOST', '0.0.0.0')
STOREFRONT_PORT = int(os.environ.get('STOREFRONT_PORT', '8000'))
STOREFRONT_DEBUG = os.environ.get('STOREFRONT_DEBUG', '0').strip().lower() in {'1', 'true', 'yes', 'y'}
STOREFRONT_SECRET_KEY = os.environ.get('STOREFRONT_SECRET_KEY', 'anson-storefront-dev')
STOREFRONT_ADMIN_USERNAME = os.environ.get('STOREFRONT_ADMIN_USERNAME', 'admin')
STOREFRONT_ADMIN_PASSWORD = os.environ.get('STOREFRONT_ADMIN_PASSWORD', 'ChangeMeStorefront123!')
# Shared HMAC secret with the Royal Card PWA's shop-link Edge Function. When set,
# /auth/royalcard accepts signed deeplinks from the PWA and auto-links/logs in the
# customer by their Royal Card mobile. Empty => the route 404s (Phase A "off").
ROYALCARD_LINK_SECRET = os.environ.get('ROYALCARD_LINK_SECRET', '').strip()
ROYALCARD_PUBLIC_URL  = os.environ.get('ROYALCARD_PUBLIC_URL', 'https://royalcard.ansonsupermart.com')
# Phase B — direct Supabase lookup so storefront-native sign-ins (i.e. customers who
# log in at /account/login or register at /account/register without coming from the
# PWA deeplink) can still be auto-linked to their Royal Card. Server-side, so we use
# the service-role key (NOT the anon key) to bypass RLS for the members lookup.
# Both blank => Phase B silently disabled (login/register behaviour is unchanged).
SUPABASE_URL              = os.environ.get('SUPABASE_URL', '').strip().rstrip('/')
SUPABASE_SERVICE_ROLE_KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '').strip()
# Store-side contact handles, surfaced as "Message us" buttons on customer-facing
# pages (e.g. order status). Set these to whatever number/email staff actually monitor.
# All optional — anything blank just hides the matching link.
STORE_CONTACT_NAME  = os.environ.get('STORE_CONTACT_NAME',  'ANSON Supermart').strip()
STORE_CONTACT_PHONE = os.environ.get('STORE_CONTACT_PHONE', '').strip()
STORE_CONTACT_EMAIL = os.environ.get('STORE_CONTACT_EMAIL', '').strip()
STORE_MESSENGER_URL = os.environ.get('STORE_MESSENGER_URL', '').strip()   # e.g. m.me/ansonsupermart

app = Flask(__name__, template_folder=str(BASE_DIR / 'templates'), static_folder=str(BASE_DIR / 'static'))
app.secret_key = STOREFRONT_SECRET_KEY

SORT_OPTIONS = {
    # 'relevance' is search-only: the real ordering comes from the FTS match
    # rank (see search_products). The SQL clause here is just a safe fallback
    # so the key never breaks a plain browse query.
    'relevance': ("CASE WHEN COALESCE(priority, '') = 'TOP' THEN 0 ELSE 1 END, COALESCE(txn_count_24m, 0) DESC, name COLLATE NOCASE", 'Best Match'),
    'top': ("CASE WHEN COALESCE(priority, '') = 'TOP' THEN 0 ELSE 1 END, COALESCE(txn_count_24m, 0) DESC, name COLLATE NOCASE", 'Top Sellers'),
    'price_low': ('COALESCE(display_price, price_retail, 0) ASC, name COLLATE NOCASE', 'Price: Low to High'),
    'price_high': ('COALESCE(display_price, price_retail, 0) DESC, name COLLATE NOCASE', 'Price: High to Low'),
    'name_asc': ('name COLLATE NOCASE ASC', 'Name: A to Z'),
    'name_desc': ('name COLLATE NOCASE DESC', 'Name: Z to A'),
    'recent_sale': ('COALESCE(last_sale_date, "") DESC, COALESCE(txn_count_24m, 0) DESC, name COLLATE NOCASE', 'Recent Sale'),
}

PRICE_BANDS = {
    '': ('All Prices', None, None),
    'under-100': ('Under P100', None, 100),
    '100-250': ('P100 to P250', 100, 250),
    '250-500': ('P250 to P500', 250, 500),
    '500-up': ('P500 and Up', 500, None),
}

FRESH_CLASS_HINTS = {'ES.FM.PORK', 'ES.FM.BEEF', 'ES.FM.POULTRY', 'ES.MP.FISH', 'ES.AP.VEGETABLES', 'ES.AP.FRUITS'}
FRESH_DEPARTMENT_SLUGS = {'fresh'}
# Homepage tile eligibility: always exclude non-FMCG departments and bag/cigarette
# classes from "Best Sellers", "Fresh Picks" etc. so Sandobag and Marlboro never
# headline the page even though they sell well.
HOMEPAGE_TILE_DEPARTMENT_SLUGS = (
    'fresh', 'pantry-supplies', 'dairy-eggs', 'beverages', 'bread-bakery',
    'snacks', 'frozen-goods', 'personal-care', 'baby-kids', 'home-care',
    'ready-to-eat',
)
HOMEPAGE_TILE_EXCLUDED_L3 = (
    'NE.MS.PLASTIC PRODUCTS', 'NE.CW.CIGARETTES', 'NE.CW.LIGHTER & FLUIDS',
)
STOREFRONT_ALPHA_MODE = os.environ.get('STOREFRONT_ALPHA_MODE', '0').strip().lower() in {'1', 'true', 'yes', 'y'}
DISPLAY_PRICE_SQL = 'COALESCE(display_price, price_retail, 0)'
HOMEPAGE_SPOTLIGHT_ROW_SIZE = 5
RELATED_PRODUCTS_ROW_SIZE = 5
RELATED_PRODUCTS_MAX_ROWS = 2
PRODUCT_GRID_ROW_SIZE = 5
PRODUCT_GRID_ROWS_PER_PAGE = 7
PRODUCT_GRID_PAGE_SIZE = PRODUCT_GRID_ROW_SIZE * PRODUCT_GRID_ROWS_PER_PAGE
PLACEHOLDER_PHOTO = '/static/ANSON-ONLINE-GROCERY-PLACEHOLDER.jpg'
CART_SESSION_KEY = 'storefront_cart'
EDITING_REQUEST_KEY = 'storefront_editing_request'
NOTIFICATIONS_WEBHOOK_URL = os.environ.get('STOREFRONT_NOTIFICATIONS_WEBHOOK', '').strip()
NOTIFICATIONS_PUBLIC_BASE = os.environ.get('STOREFRONT_PUBLIC_BASE', '').strip().rstrip('/')
# Discord webhook messages don't push to phones unless they actively mention someone.
# Prefix every order notification with this so it pings even on default "Only @mentions"
# settings. Default '@here' pings online members; override with a user ('<@ID>') or
# role ('<@&ID>') mention via the systemd drop-in. Set empty to disable mentions.
NOTIFICATIONS_MENTION = os.environ.get('STOREFRONT_NOTIFICATIONS_MENTION', '@here').strip()
PAYMENT_PROOF_UPLOAD_DIR = Path(os.environ.get('STOREFRONT_PAYMENT_PROOF_DIR', str(BASE_DIR / 'instance' / 'uploads' / 'payment_proofs')))
PAYMENT_PROOF_ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'pdf'}
app.config['MAX_CONTENT_LENGTH'] = int(os.environ.get('STOREFRONT_MAX_UPLOAD_BYTES', str(6 * 1024 * 1024)))

# Preferred simplified status model. Legacy status values remain accepted/mapped so old rows and links do not break.
ORDER_STATUS_OPTIONS = ['NEW', 'REVIEWING', 'PICKING', 'AWAITING_CUSTOMER_CONFIRMATION', 'FOR_RELEASE', 'RELEASED', 'CANCELLED']
LEGACY_ORDER_STATUS_OPTIONS = ['CONFIRMED', 'READY_FOR_PICKUP', 'OUT_FOR_DELIVERY', 'COMPLETED', 'FULFILLED']
ALL_ORDER_STATUS_OPTIONS = ORDER_STATUS_OPTIONS + LEGACY_ORDER_STATUS_OPTIONS
ORDER_STATUS_LABELS = {
    'NEW': 'New',
    'REVIEWING': 'Reviewing',
    'PICKING': 'Being Prepared',          # in-store staff is shopping the order
    'AWAITING_CUSTOMER_CONFIRMATION': 'Awaiting Customer Confirmation',
    'CONFIRMED': 'Awaiting Payment',
    'FOR_RELEASE': 'For Release',
    'READY_FOR_PICKUP': 'For Release',
    'OUT_FOR_DELIVERY': 'For Release',
    'RELEASED': 'Released',
    'COMPLETED': 'Released',
    'FULFILLED': 'Released',
    'CANCELLED': 'Cancelled',
}
PAYMENT_STATUS_OPTIONS = ['UNPAID', 'PAYMENT_PENDING', 'PAYMENT_SUBMITTED', 'PAID', 'PAYMENT_REJECTED']
PAYMENT_STATUS_LABELS = {
    'UNPAID': 'Unpaid',
    'PAYMENT_PENDING': 'Payment Pending',
    'PAYMENT_SUBMITTED': 'Payment Submitted',
    'PAID': 'Paid / Verified',
    'PAYMENT_REJECTED': 'Payment Rejected',
}
# Treasury reference payment types include GCASH, CCARD, GSP, GRAB, ROYAL_R, GVOUCHER, CHECK, HOME, COMPANY.
# Storefront exposes only customer-facing first-pass options.
PAYMENT_METHOD_OPTIONS = [
    ('GCASH', 'GCash'),
    ('BANK_TRANSFER', 'Bank Transfer / QRPh'),
    ('CCARD', 'Credit Card'),
    ('CHECK', 'Check'),
]
PAYMENT_METHOD_LABELS = dict(PAYMENT_METHOD_OPTIONS)
CURATED_TOP_DEPARTMENT_ORDER = [
    'fresh',
    'pantry-supplies',
    'dairy-eggs',
    'beverages',
    'bread-bakery',
    'snacks',
    'frozen-goods',
    'personal-care',
    'pharmacies',
    'baby-kids',
]
BRAND_SPOTLIGHTS = [
    {
        'slug': 'bread-garden-bakeshop',
        'name': 'Bread Garden Bakeshop',
        'brand': 'Bread Garden',
        'eyebrow': 'In-house bakeshop',
        'tagline': 'Anson\'s own freshly-baked breads, cakes, and pastries.',
    },
]
BRAND_SPOTLIGHTS_BY_SLUG = {s['slug']: s for s in BRAND_SPOTLIGHTS}

# 16 friendly "shopper-language" department buckets shown in the homepage
# Browse Categories dropdown. 'department_slug' is the closest existing storefront
# department. When a bucket's label is narrower than its department (several
# buckets share one broad department, e.g. Pantry Supplies), 'category_slugs'
# lists the real aisles it covers — the tile then links to /browse/<slug>, a
# landing titled with the friendly name showing exactly those aisles, instead of
# dumping the shopper into the whole broad department. Buckets without
# 'category_slugs' link straight to their department page.
FRIENDLY_DEPARTMENT_BUCKETS = [
    {'emoji': '🍚', 'name': 'Staple Groceries',       'subtext': 'Coffee · Rice · Noodles',          'slug': 'staple-groceries',     'department_slug': 'pantry-supplies', 'category_slugs': ['rice-noodles-pasta', 'instant-noodles', 'cereals', 'sugar-sweeteners', 'baking', 'flour-mixes-baking-aids']},
    {'emoji': '🍪', 'name': 'Snacks & Powdered Drinks','subtext': 'Candies · Chocolate',              'department_slug': 'snacks'},
    {'emoji': '🥤', 'name': 'Beverages',              'subtext': 'Water · Softdrinks · Juice',       'department_slug': 'beverages'},
    {'emoji': '🥖', 'name': 'Bakery & Bread',         'subtext': 'Biscuits · Pasta',                 'department_slug': 'bread-bakery'},
    {'emoji': '🥩', 'name': 'Fresh Meat',             'subtext': 'Poultry · Pork · Beef',            'slug': 'fresh-meat',           'department_slug': 'fresh', 'category_slugs': ['chicken', 'pork', 'beef']},
    {'emoji': '🐟', 'name': 'Fresh Fish & Seafood',   'subtext': 'Fillet · Tinapa',                  'slug': 'fresh-fish-seafood',   'department_slug': 'fresh', 'category_slugs': ['fresh-seafood']},
    {'emoji': '🥫', 'name': 'Canned & Processed',     'subtext': 'Hotdog · Sardines · Tuna',         'slug': 'canned-processed',     'department_slug': 'pantry-supplies', 'category_slugs': ['canned-goods', 'canned-seafood', 'canned-meat', 'canned-fruits', 'canned-vegetables']},
    {'emoji': '🧂', 'name': 'Seasonings & Imported',  'subtext': 'Sauces · Milk · Specialty',        'slug': 'seasonings-imported',  'department_slug': 'pantry-supplies', 'category_slugs': ['sauce-spices-spreads-condiments', 'herbs-spices', 'broth-cubes-soup-bases', 'breakfast-spreads', 'culinary-milk-cream']},
    {'emoji': '🫒', 'name': 'Cooking Oil',            'subtext': 'Vegetable · Canola · Corn',        'slug': 'cooking-oil',          'department_slug': 'pantry-supplies', 'category_slugs': ['oil']},
    {'emoji': '🧴', 'name': 'Personal Care',          'subtext': 'Hair · Body · Oral',               'department_slug': 'personal-care'},
    {'emoji': '🧼', 'name': 'Laundry & Cleaning',     'subtext': 'Detergent · Fabric care',          'slug': 'laundry-cleaning',     'department_slug': 'home-care', 'category_slugs': ['laundry-items', 'fabric-conditioners', 'dishwashing', 'household-cleaners', 'disinfectants']},
    {'emoji': '🧺', 'name': 'Household & Paper',      'subtext': 'Tissue · Plastics',                'slug': 'household-paper',      'department_slug': 'home-care', 'category_slugs': ['cleaning-tools', 'other-home-items', 'trash-bags-disposable-needs', 'air-fresheners-deodorizers', 'pest-control']},
    {'emoji': '🍼', 'name': 'Baby Care',              'subtext': 'Diapers · Milk · Wipes',           'department_slug': 'baby-kids'},
    {'emoji': '💊', 'name': 'OTC Medicine',           'subtext': 'Vitamins · First aid',             'department_slug': 'pharmacies'},
    {'emoji': '🐾', 'name': 'Pet & General Merch',    'subtext': 'Pet food · Plastics · Toys',       'department_slug': 'pets'},
    {'emoji': '📚', 'name': 'Office & School',        'subtext': 'Paper · Pens · Notebooks',         'department_slug': 'office-school-supplies'},
]
FRIENDLY_BUCKETS_BY_SLUG = {b['slug']: b for b in FRIENDLY_DEPARTMENT_BUCKETS if b.get('slug')}

# Quick Baskets are fully CSV-managed — no code edit to add/edit a basket:
#   quick_baskets.csv       — basket definitions (slug, emoji, name), in order.
#   quick_basket_items.csv  — recipe lines (basket_slug, item_label, merkey,
#                             search_query, quantity). A line resolves to its
#                             pinned merkey, or falls back to search_query when
#                             the merkey is missing/unavailable.
QUICK_BASKETS_CSV = BASE_DIR / 'quick_baskets.csv'
QUICK_BASKET_ITEMS_CSV = BASE_DIR / 'quick_basket_items.csv'
ADMIN_SESSION_KEY = 'storefront_admin_authenticated'
ADMIN_USERNAME_SESSION_KEY = 'storefront_admin_username'
CUSTOMER_SESSION_KEY = 'storefront_customer_id'
CUSTOMER_NAME_SESSION_KEY = 'storefront_customer_name'


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(STORE_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_runtime_schema() -> None:
    conn = get_conn()
    try:
        conn.executescript((BASE_DIR / 'schema.sql').read_text(encoding='utf-8'))
        order_request_columns = {
            row['name']
            for row in conn.execute("PRAGMA table_info(order_requests)").fetchall()
        }
        if 'internal_note' not in order_request_columns:
            conn.execute("ALTER TABLE order_requests ADD COLUMN internal_note TEXT")
        if 'fulfillment_method' not in order_request_columns:
            conn.execute("ALTER TABLE order_requests ADD COLUMN fulfillment_method TEXT")
        if 'preferred_schedule' not in order_request_columns:
            conn.execute("ALTER TABLE order_requests ADD COLUMN preferred_schedule TEXT")
        if 'location_details' not in order_request_columns:
            conn.execute("ALTER TABLE order_requests ADD COLUMN location_details TEXT")
        if 'confirmed_at' not in order_request_columns:
            conn.execute("ALTER TABLE order_requests ADD COLUMN confirmed_at TEXT")
        if 'confirmed_total' not in order_request_columns:
            conn.execute("ALTER TABLE order_requests ADD COLUMN confirmed_total REAL")
        if 'customer_confirmation_note' not in order_request_columns:
            conn.execute("ALTER TABLE order_requests ADD COLUMN customer_confirmation_note TEXT")
        # Phase C — Royal Card redemption at checkout. Stores the points
        # debited from the member's balance for this online order. NULL or 0
        # means no points were redeemed. Cancellation flips the linked
        # bonus_ledger negative row to 'cancelled' so settle_bonuses skips it.
        if 'points_redeemed' not in order_request_columns:
            conn.execute("ALTER TABLE order_requests ADD COLUMN points_redeemed INTEGER NOT NULL DEFAULT 0")
        payment_columns = {
            'payment_status': "TEXT NOT NULL DEFAULT 'UNPAID'",
            'payment_method': 'TEXT',
            'payment_reference': 'TEXT',
            'payment_proof_path': 'TEXT',
            'payment_amount': 'REAL',
            'payment_submitted_at': 'TEXT',
            'payment_verified_at': 'TEXT',
            'payment_verified_by': 'TEXT',
            'payment_verification_note': 'TEXT',
        }
        for column_name, column_def in payment_columns.items():
            if column_name not in order_request_columns:
                conn.execute(f"ALTER TABLE order_requests ADD COLUMN {column_name} {column_def}")
        conn.execute("UPDATE order_requests SET payment_status = COALESCE(NULLIF(payment_status, ''), 'UNPAID')")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS order_payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_request_id INTEGER NOT NULL,
                payment_method TEXT NOT NULL,
                reference_number TEXT,
                amount REAL,
                proof_path TEXT,
                status TEXT NOT NULL DEFAULT 'SUBMITTED',
                customer_note TEXT,
                staff_note TEXT,
                submitted_at TEXT DEFAULT CURRENT_TIMESTAMP,
                verified_at TEXT,
                verified_by TEXT,
                FOREIGN KEY(order_request_id) REFERENCES order_requests(id)
            );
            CREATE INDEX IF NOT EXISTS idx_order_payments_order_request_id ON order_payments(order_request_id, submitted_at DESC);
        """)
        order_item_columns = {
            row['name']
            for row in conn.execute("PRAGMA table_info(order_request_items)").fetchall()
        }
        if 'original_requested_qty' not in order_item_columns:
            conn.execute("ALTER TABLE order_request_items ADD COLUMN original_requested_qty INTEGER")
        if 'original_quoted_price' not in order_item_columns:
            conn.execute("ALTER TABLE order_request_items ADD COLUMN original_quoted_price REAL")
        if 'removed' not in order_item_columns:
            conn.execute("ALTER TABLE order_request_items ADD COLUMN removed INTEGER NOT NULL DEFAULT 0")
        if 'removal_reason' not in order_item_columns:
            conn.execute("ALTER TABLE order_request_items ADD COLUMN removal_reason TEXT")
        if 'selling_option_key' not in order_item_columns:
            conn.execute("ALTER TABLE order_request_items ADD COLUMN selling_option_key TEXT NOT NULL DEFAULT 'retail'")
        if 'selling_option_label' not in order_item_columns:
            conn.execute("ALTER TABLE order_request_items ADD COLUMN selling_option_label TEXT")
        if 'selling_option_barcode' not in order_item_columns:
            conn.execute("ALTER TABLE order_request_items ADD COLUMN selling_option_barcode TEXT")
        # --- Picker MVP: per-item pick state ---
        if 'pick_status' not in order_item_columns:
            conn.execute("ALTER TABLE order_request_items ADD COLUMN pick_status TEXT NOT NULL DEFAULT 'PENDING'")
        if 'picked_at' not in order_item_columns:
            conn.execute("ALTER TABLE order_request_items ADD COLUMN picked_at TEXT")
        if 'picked_by' not in order_item_columns:
            conn.execute("ALTER TABLE order_request_items ADD COLUMN picked_by TEXT")
        conn.execute(
            """
            UPDATE order_request_items
            SET original_requested_qty = COALESCE(original_requested_qty, requested_qty),
                original_quoted_price = COALESCE(original_quoted_price, quoted_price),
                removed = COALESCE(removed, 0),
                selling_option_key = COALESCE(NULLIF(selling_option_key, ''), 'retail'),
                pick_status = COALESCE(NULLIF(pick_status, ''), 'PENDING')
            """
        )
        # --- Customer accounts migration ---
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone TEXT,
                email TEXT,
                password_hash TEXT NOT NULL,
                display_name TEXT NOT NULL,
                default_fulfillment_method TEXT,
                default_location_details TEXT,
                default_preferred_schedule TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_customers_phone ON customers(phone) WHERE phone IS NOT NULL")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_customers_email ON customers(email) WHERE email IS NOT NULL")
        # Royal Card linkage (Phase A) — populated when a customer arrives via the
        # signed /auth/royalcard deeplink or types their card number on profile.
        customer_columns = {row['name'] for row in conn.execute("PRAGMA table_info(customers)").fetchall()}
        if 'card_no' not in customer_columns:
            conn.execute("ALTER TABLE customers ADD COLUMN card_no TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_customers_card_no ON customers(card_no) WHERE card_no IS NOT NULL")
        if 'customer_id' not in order_request_columns:
            conn.execute("ALTER TABLE order_requests ADD COLUMN customer_id INTEGER REFERENCES customers(id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_order_requests_customer_id ON order_requests(customer_id)")
        product_columns = {row['name'] for row in conn.execute("PRAGMA table_info(products)").fetchall()}
        if 'stock_status' not in product_columns:
            conn.execute("ALTER TABLE products ADD COLUMN stock_status TEXT NOT NULL DEFAULT 'in_stock'")
        if 'alpha_visible' not in product_columns:
            conn.execute("ALTER TABLE products ADD COLUMN alpha_visible INTEGER NOT NULL DEFAULT 0")
        if 'default_selling_option' not in product_columns:
            conn.execute("ALTER TABLE products ADD COLUMN default_selling_option TEXT NOT NULL DEFAULT 'retail'")
        if 'exclusive_selling_option' not in product_columns:
            conn.execute("ALTER TABLE products ADD COLUMN exclusive_selling_option INTEGER NOT NULL DEFAULT 0")
        if 'pack_quantity' not in product_columns:
            conn.execute("ALTER TABLE products ADD COLUMN pack_quantity INTEGER")
        # --- Milestone 5: pickup time slots ---
        # Replaces the legacy free-text `preferred_schedule` with bookable
        # 2-hour windows so staff can plan capacity. The legacy column
        # stays around (we also write a human-readable string into it)
        # so existing admin views keep working unchanged.
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS pickup_slots (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                slot_date     TEXT NOT NULL,        -- ISO 'YYYY-MM-DD'
                start_time    TEXT NOT NULL,        -- 'HH:MM' 24h
                end_time      TEXT NOT NULL,        -- 'HH:MM' 24h
                capacity      INTEGER NOT NULL DEFAULT 8,
                is_active     INTEGER NOT NULL DEFAULT 1,
                notes         TEXT,
                created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(slot_date, start_time, end_time)
            );
            CREATE INDEX IF NOT EXISTS idx_pickup_slots_date_active ON pickup_slots(slot_date, is_active);
        """)
        if 'pickup_slot_id' not in order_request_columns:
            conn.execute(
                "ALTER TABLE order_requests ADD COLUMN pickup_slot_id INTEGER REFERENCES pickup_slots(id)"
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_order_requests_pickup_slot_id ON order_requests(pickup_slot_id) WHERE pickup_slot_id IS NOT NULL"
        )
        conn.commit()
    finally:
        conn.close()


ensure_runtime_schema()


# Synonym / local-term map for search query expansion. Seeded in
# search_engine; an optional search_synonyms.csv (term,alias1;alias2) lets the
# store grow it without code changes.
SEARCH_SYNONYMS = search_engine.load_synonyms(BASE_DIR / 'search_synonyms.csv')


# Limited-time manufacturer promo SKUs — the same base product as the regular
# listing but a temporary run (e.g. "1L + free 500ml / tuna / shirt" bundles),
# so they carry a "Limited Promo" chip. This is a CURATED list, not an auto-rule:
# the "+FREEBIE" MEDESC pattern is far too noisy to trust blindly (brand names
# like "A+", ages like "6+", combo packs like "5+1"/"180ML+90ML"). Edit
# storefront_limited_promo.csv and restart the app to toggle — no republish.
def load_limited_promo_merkeys(path) -> set[str]:
    import csv
    promos: set[str] = set()
    try:
        with open(path, newline='', encoding='utf-8-sig') as fh:
            for row in csv.DictReader(fh):
                merkey = (row.get('merkey') or '').strip()
                if merkey:
                    promos.add(merkey)
    except FileNotFoundError:
        pass
    return promos


LIMITED_PROMO_MERKEYS = load_limited_promo_merkeys(BASE_DIR / 'storefront_limited_promo.csv')


def warm_search_index() -> None:
    """Build the FTS search index at boot if the catalog changed. Cheap when
    already fresh; failures must never block startup."""
    try:
        conn = get_conn()
        try:
            search_engine.ensure_search_index(conn)
        finally:
            conn.close()
    except Exception as exc:  # pragma: no cover - defensive
        app.logger.warning("search index warm-up skipped: %s", exc)


warm_search_index()


# ---------------------------------------------------------------------------
# Milestone 5 — pickup time slot helpers
# ---------------------------------------------------------------------------
# Default 2-hour windows seeded at first boot. Standard ANSON storefront
# pickup times — adjust here and re-seed (or insert directly) if hours shift.
_DEFAULT_PICKUP_WINDOWS = [
    ('10:00', '12:00'),
    ('14:00', '16:00'),
    ('16:00', '18:00'),
]
_DEFAULT_PICKUP_CAPACITY = 8


def _format_hour_12(hhmm: str) -> str:
    """Convert '14:00' -> '2 PM', '14:30' -> '2:30 PM'. Falls back to the
    raw input on bad data so the UI never blows up on a malformed slot."""
    try:
        h, m = hhmm.split(':')
        hour = int(h)
        minute = int(m)
    except (ValueError, AttributeError):
        return hhmm or ''
    suffix = 'AM' if hour < 12 else 'PM'
    h12 = hour % 12 or 12
    if minute:
        return f"{h12}:{minute:02d} {suffix}"
    return f"{h12} {suffix}"


def _format_slot_time_range(start: str, end: str) -> str:
    """'14:00','16:00' -> '2-4 PM'  ·  '10:00','12:00' -> '10-12 PM'.
    If the suffixes differ (e.g. 10 AM -> 12 PM) we keep both."""
    try:
        s_hour = int(start.split(':')[0])
        e_hour = int(end.split(':')[0])
    except (ValueError, AttributeError, IndexError):
        return f"{start}-{end}"
    s_suffix = 'AM' if s_hour < 12 else 'PM'
    e_suffix = 'AM' if e_hour < 12 else 'PM'
    s12 = s_hour % 12 or 12
    e12 = e_hour % 12 or 12
    s_min = start.split(':')[1] if ':' in start else '00'
    e_min = end.split(':')[1] if ':' in end else '00'
    s_label = f"{s12}:{s_min}" if s_min != '00' else f"{s12}"
    e_label = f"{e12}:{e_min}" if e_min != '00' else f"{e12}"
    if s_suffix == e_suffix:
        return f"{s_label}-{e_label} {e_suffix}"
    return f"{s_label} {s_suffix}-{e_label} {e_suffix}"


def _format_slot_date_label(slot_date: str, today: 'datetime | None' = None) -> str:
    """Today/Tom/short weekday label for the slot card. Falls back to the
    raw date string on bad input so the UI keeps rendering."""
    from datetime import date, timedelta
    try:
        d = datetime.strptime(slot_date, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return slot_date or ''
    today_d = (today.date() if today else datetime.now().date())
    delta = (d - today_d).days
    if delta == 0:
        return 'Today'
    if delta == 1:
        return 'Tom'
    # 2-6 days out: weekday short name. Beyond that: 'Jun 24' style.
    if 2 <= delta <= 6:
        return d.strftime('%a')
    return d.strftime('%b %d')


def seed_default_slots(days_ahead: int = 7) -> int:
    """If `pickup_slots` is empty for the next `days_ahead` days, populate
    them with the standard 2-hour windows. Idempotent — safe to call from
    every boot. Returns the number of rows inserted."""
    from datetime import date, timedelta
    today = datetime.now().date()
    conn = get_conn()
    try:
        # Cheap pre-check: do we already have *any* future-active rows? If so
        # leave seeding alone so manual edits aren't overwritten.
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM pickup_slots WHERE slot_date >= ? AND is_active = 1",
            (today.isoformat(),),
        ).fetchone()
        if row and (row['n'] or 0) > 0:
            return 0
        inserted = 0
        with conn:
            for offset in range(days_ahead):
                day = today + timedelta(days=offset)
                for start, end in _DEFAULT_PICKUP_WINDOWS:
                    cur = conn.execute(
                        """
                        INSERT OR IGNORE INTO pickup_slots
                            (slot_date, start_time, end_time, capacity, is_active)
                        VALUES (?, ?, ?, ?, 1)
                        """,
                        (day.isoformat(), start, end, _DEFAULT_PICKUP_CAPACITY),
                    )
                    inserted += cur.rowcount
        return inserted
    finally:
        conn.close()


seed_default_slots(days_ahead=7)


def _slot_to_dict(row: sqlite3.Row, booked_count: int = 0, today: 'datetime | None' = None) -> dict:
    capacity = int(row['capacity'] or 0)
    capacity_left = max(0, capacity - int(booked_count or 0))
    date_label = _format_slot_date_label(row['slot_date'], today=today)
    time_label = _format_slot_time_range(row['start_time'], row['end_time'])
    return {
        'id': row['id'],
        'slot_date': row['slot_date'],
        'start_time': row['start_time'],
        'end_time': row['end_time'],
        'capacity': capacity,
        'booked_count': int(booked_count or 0),
        'capacity_left': capacity_left,
        'is_active': bool(row['is_active']),
        'label_date': date_label,
        'label_time': time_label,
        'label_short': f"{date_label} {time_label}",
        # Human-readable string for the legacy `preferred_schedule` column.
        'label_legacy': f"{date_label} ({row['slot_date']}) {time_label}",
    }


def fetch_available_slots(days_ahead: int = 3) -> list[dict]:
    """Return active slots from today through `days_ahead` days that still
    have capacity. Used to render the slot picker in the checkout view.
    Booked count = non-CANCELLED order_requests with that pickup_slot_id."""
    from datetime import timedelta
    today = datetime.now().date()
    horizon = today + timedelta(days=days_ahead)
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT s.*,
                   COALESCE((
                       SELECT COUNT(*) FROM order_requests o
                       WHERE o.pickup_slot_id = s.id
                         AND COALESCE(o.status, 'NEW') != 'CANCELLED'
                   ), 0) AS booked_count
            FROM pickup_slots s
            WHERE s.is_active = 1
              AND s.slot_date >= ?
              AND s.slot_date <= ?
            ORDER BY s.slot_date ASC, s.start_time ASC
            """,
            (today.isoformat(), horizon.isoformat()),
        ).fetchall()
        results = []
        for row in rows:
            slot = _slot_to_dict(row, booked_count=row['booked_count'])
            if slot['capacity_left'] > 0:
                results.append(slot)
        return results
    finally:
        conn.close()


def fetch_pickup_slot(slot_id: int) -> dict | None:
    """Fetch a single slot by id, with booked_count, for label rendering
    after the order has been placed."""
    if not slot_id:
        return None
    conn = get_conn()
    try:
        row = conn.execute(
            """
            SELECT s.*,
                   COALESCE((
                       SELECT COUNT(*) FROM order_requests o
                       WHERE o.pickup_slot_id = s.id
                         AND COALESCE(o.status, 'NEW') != 'CANCELLED'
                   ), 0) AS booked_count
            FROM pickup_slots s
            WHERE s.id = ?
            """,
            (slot_id,),
        ).fetchone()
        return _slot_to_dict(row, booked_count=row['booked_count']) if row else None
    finally:
        conn.close()


def book_pickup_slot(order_request_id: int, slot_id: int) -> bool:
    """Atomically attach `slot_id` to `order_request_id`. Returns False if
    the slot is inactive or already full at booking time. Race-safe: we
    re-check capacity inside the same transaction before writing."""
    if not order_request_id or not slot_id:
        return False
    conn = get_conn()
    try:
        with conn:
            slot_row = conn.execute(
                "SELECT id, capacity, is_active FROM pickup_slots WHERE id = ?",
                (slot_id,),
            ).fetchone()
            if not slot_row or not slot_row['is_active']:
                return False
            # Booked = non-CANCELLED order_requests already on this slot.
            booked_row = conn.execute(
                """
                SELECT COUNT(*) AS n FROM order_requests
                WHERE pickup_slot_id = ?
                  AND COALESCE(status, 'NEW') != 'CANCELLED'
                  AND id != ?
                """,
                (slot_id, order_request_id),
            ).fetchone()
            booked = int(booked_row['n'] or 0) if booked_row else 0
            if booked >= int(slot_row['capacity'] or 0):
                return False
            conn.execute(
                "UPDATE order_requests SET pickup_slot_id = ? WHERE id = ?",
                (slot_id, order_request_id),
            )
        return True
    finally:
        conn.close()


def order_request_id_for_code(request_code: str) -> int | None:
    """Look up the numeric id for a request_code. Used by /checkout after
    create_order_request() returns the public code."""
    if not request_code:
        return None
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id FROM order_requests WHERE request_code = ?",
            (request_code,),
        ).fetchone()
        return row['id'] if row else None
    finally:
        conn.close()


def storefront_admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get(ADMIN_SESSION_KEY):
            return redirect(url_for('admin_login_page', next=request.full_path.rstrip('?')))
        return view(*args, **kwargs)
    return wrapped


def customer_login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get(CUSTOMER_SESSION_KEY):
            return redirect(url_for('customer_login_page', next=request.full_path.rstrip('?')))
        return view(*args, **kwargs)
    return wrapped


def resolve_admin_redirect_target(raw_target: str | None) -> str:
    candidate = (raw_target or '').strip()
    if candidate.startswith('/orders/admin'):
        return candidate
    return url_for('admin_orders_page')


def resolve_customer_redirect_target(raw_target: str | None) -> str:
    candidate = (raw_target or '').strip()
    if candidate and candidate.startswith('/') and not candidate.startswith('//'):
        return candidate
    return url_for('customer_profile_page')


def normalize_login_identifier(raw: str) -> tuple[str, str]:
    value = raw.strip()
    if '@' in value:
        return ('email', value.lower())
    digits = ''.join(ch for ch in value if ch.isdigit())
    return ('phone', digits)


def authenticate_customer(identifier: str, password: str) -> dict | None:
    id_type, normalized = normalize_login_identifier(identifier)
    if not normalized:
        return None
    conn = get_conn()
    try:
        if id_type == 'email':
            row = conn.execute("SELECT * FROM customers WHERE email = ?", (normalized,)).fetchone()
        else:
            row = conn.execute("SELECT * FROM customers WHERE phone = ?", (normalized,)).fetchone()
        if row and check_password_hash(row['password_hash'], password):
            return dict(row)
        return None
    finally:
        conn.close()


def get_current_customer() -> dict | None:
    customer_id = session.get(CUSTOMER_SESSION_KEY)
    if not customer_id:
        return None
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def claim_guest_orders(conn: sqlite3.Connection, customer_id: int, phone: str | None, email: str | None) -> int:
    clauses = []
    params: list = []
    if phone:
        clauses.append("REPLACE(REPLACE(REPLACE(contact_number, '-', ''), ' ', ''), '+', '') = ?")
        params.append(phone)
    if email:
        clauses.append("LOWER(TRIM(contact_email)) = ?")
        params.append(email)
    if not clauses:
        return 0
    where = "customer_id IS NULL AND (" + " OR ".join(clauses) + ")"
    cur = conn.execute(
        f"UPDATE order_requests SET customer_id = ? WHERE {where}",
        [customer_id] + params,
    )
    return cur.rowcount


def prefilled_request_form_data() -> dict[str, str]:
    base = empty_request_form_data()
    customer = get_current_customer()
    if customer:
        base['customer_name'] = customer['display_name'] or ''
        base['contact_number'] = customer['phone'] or ''
        base['contact_email'] = customer['email'] or ''
        base['fulfillment_method'] = customer['default_fulfillment_method'] or ''
        base['location_details'] = customer['default_location_details'] or ''
        base['preferred_schedule'] = customer['default_preferred_schedule'] or ''
    return base


def canonical_order_status(status: str | None) -> str:
    normalized = (status or '').strip().upper()
    if normalized in {'READY_FOR_PICKUP', 'OUT_FOR_DELIVERY'}:
        return 'FOR_RELEASE'
    if normalized in {'COMPLETED', 'FULFILLED'}:
        return 'RELEASED'
    if normalized == 'CONFIRMED':
        return 'AWAITING_CUSTOMER_CONFIRMATION'
    return normalized or 'NEW'


def order_status_label(status: str | None) -> str:
    normalized = (status or '').strip().upper()
    return ORDER_STATUS_LABELS.get(normalized, ORDER_STATUS_LABELS.get(canonical_order_status(normalized), normalized.replace('_', ' ').title() or 'Unknown'))


def payment_status_label(status: str | None) -> str:
    normalized = (status or '').strip().upper()
    return PAYMENT_STATUS_LABELS.get(normalized, normalized.replace('_', ' ').title() or 'Unpaid')


def payment_method_label(method: str | None) -> str:
    normalized = (method or '').strip().upper()
    return PAYMENT_METHOD_LABELS.get(normalized, normalized.replace('_', ' ').title() or 'Not selected')


def allowed_payment_method(method: str | None) -> bool:
    return (method or '').strip().upper() in PAYMENT_METHOD_LABELS


def is_allowed_payment_proof(filename: str | None) -> bool:
    if not filename or '.' not in filename:
        return False
    ext = filename.rsplit('.', 1)[1].lower()
    return ext in PAYMENT_PROOF_ALLOWED_EXTENSIONS


def save_payment_proof(file_storage) -> str | None:
    if not file_storage or not getattr(file_storage, 'filename', ''):
        return None
    if not is_allowed_payment_proof(file_storage.filename):
        raise ValueError('Payment proof must be an image or PDF file.')
    PAYMENT_PROOF_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    original = secure_filename(file_storage.filename)
    ext = original.rsplit('.', 1)[1].lower() if '.' in original else 'dat'
    filename = f"{datetime.now():%Y%m%d%H%M%S}-{secrets.token_urlsafe(12)}.{ext}"
    file_storage.save(PAYMENT_PROOF_UPLOAD_DIR / filename)
    return filename


def customer_can_submit_payment(order_request: dict) -> bool:
    status = canonical_order_status(order_request.get('status'))
    payment_status = (order_request.get('payment_status') or 'UNPAID').strip().upper()
    return status in {'AWAITING_CUSTOMER_CONFIRMATION', 'REVIEWING'} and payment_status in {'UNPAID', 'PAYMENT_PENDING', 'PAYMENT_REJECTED'}


def customer_can_cancel(order_request: dict) -> bool:
    status = (order_request.get('status') or '').strip().upper()
    return status in {'NEW', 'REVIEWING', 'CONFIRMED'}


def customer_can_edit(order_request: dict) -> bool:
    status = (order_request.get('status') or '').strip().upper()
    return status == 'NEW' and not order_request.get('confirmed_at')


def customer_can_delete(order_request: dict) -> bool:
    status = (order_request.get('status') or '').strip().upper()
    return status in {'NEW', 'CANCELLED'} and not order_request.get('confirmed_at')


def next_fulfillment_stage(order_request: dict) -> tuple[str, str] | None:
    status = canonical_order_status(order_request.get('status'))
    payment_status = (order_request.get('payment_status') or 'UNPAID').strip().upper()
    fulfillment_method = (order_request.get('fulfillment_method') or '').strip().lower()
    if status == 'AWAITING_CUSTOMER_CONFIRMATION' and payment_status == 'PAID':
        label = 'Mark For Pickup Release' if fulfillment_method == 'pickup' else 'Mark For Delivery Release'
        return ('FOR_RELEASE', label)
    if status == 'FOR_RELEASE' and payment_status == 'PAID':
        return ('RELEASED', 'Mark Released')
    return None


def customer_status_message(order_request: dict, estimated_total: float) -> tuple[str, str] | None:
    status = (order_request.get('status') or '').strip().upper()
    confirmed_total = order_request.get('confirmed_total')
    total = confirmed_total if confirmed_total is not None else estimated_total
    canonical_status = canonical_order_status(status)
    payment_status = (order_request.get('payment_status') or 'UNPAID').strip().upper()
    if canonical_status == 'AWAITING_CUSTOMER_CONFIRMATION' and payment_status in {'UNPAID', 'PAYMENT_PENDING', 'PAYMENT_REJECTED'}:
        return (
            'Payment needed',
            f"Please confirm the reviewed total of P{total:,.2f} and submit your payment details below.",
        )
    if payment_status == 'PAYMENT_SUBMITTED':
        return ('Payment submitted', 'Thanks — staff will verify your payment before release.')
    if canonical_status == 'FOR_RELEASE':
        return (
            'Order ready for release',
            f"Your payment is verified. Your order is ready for {order_request.get('fulfillment_method') or 'release'}.",
        )
    if canonical_status == 'RELEASED':
        return (
            'Order released',
            f"Your order has been released. Final total: P{total:,.2f}.",
        )
    return None


def is_valid_admin_login(username: str, password: str) -> bool:
    expected_username = STOREFRONT_ADMIN_USERNAME.strip()
    expected_password = STOREFRONT_ADMIN_PASSWORD.strip()
    return (
        bool(expected_username)
        and bool(expected_password)
        and hmac.compare_digest(username, expected_username)
        and hmac.compare_digest(password, expected_password)
    )


def slugify(text: str) -> str:
    value = (text or '').strip().lower()
    value = ''.join(ch if ch.isalnum() else '-' for ch in value)
    while '--' in value:
        value = value.replace('--', '-')
    return value.strip('-')


def normalize_quantity_label(label: str | None) -> str | None:
    if not label:
        return None
    value = label.strip()
    if not value:
        return None
    return value.replace('GMS', 'G').replace('gms', 'g')


def build_query_string(**kwargs: object) -> str:
    parts = []
    for key, value in kwargs.items():
        if value is None:
            continue
        value = str(value).strip()
        if value:
            parts.append((key, value))
    return urlencode(parts)


def make_pager(page: int, total_count: int, per_page: int) -> dict:
    total_pages = max((total_count + per_page - 1) // per_page, 1)
    page = min(max(page, 1), total_pages)
    return {
        'page': page,
        'per_page': per_page,
        'total_count': total_count,
        'total_pages': total_pages,
        'has_prev': page > 1,
        'has_next': page < total_pages,
        'prev_page': page - 1,
        'next_page': page + 1,
    }


def trim_orphaned_grid_items(items: list[dict], row_size: int) -> list[dict]:
    if row_size <= 1 or len(items) <= row_size:
        return items
    if len(items) % row_size == 1:
        return items[:-1]
    return items


def normalize_related_products(items: list[dict], row_size: int, max_rows: int) -> list[dict]:
    max_items = row_size * max_rows
    trimmed = items[:max_items]
    if len(trimmed) <= row_size:
        return trimmed
    if len(trimmed) < max_items:
        return trimmed[:row_size]
    return trimmed


def fetch_departments(order_by_count: bool = False) -> list[dict]:
    order_sql = 'product_count DESC, name COLLATE NOCASE' if order_by_count else 'name COLLATE NOCASE'
    conn = get_conn()
    try:
        rows = conn.execute(
            f"""
            SELECT id, name, slug, product_count
            FROM departments
            WHERE product_count > 0
            ORDER BY {order_sql}
            """
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def build_curated_top_departments(departments: list[dict]) -> list[dict]:
    departments_by_slug = {
        department['slug']: department
        for department in departments
        if department.get('product_count', 0) > 0
    }
    return [
        departments_by_slug[slug]
        for slug in CURATED_TOP_DEPARTMENT_ORDER
        if slug in departments_by_slug
    ]


def fetch_category_highlights(limit: int = 10) -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT c.id, c.name, c.slug, c.product_count, d.name AS department_name, d.slug AS department_slug
            FROM categories c
            JOIN departments d ON d.id = c.department_id
            WHERE c.product_count > 0
            ORDER BY c.product_count DESC, c.name COLLATE NOCASE
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def fetch_categories_for_department(department_slug: str) -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT c.id, c.name, c.slug, c.product_count, d.name AS department_name, d.slug AS department_slug
            FROM categories c
            JOIN departments d ON d.id = c.department_id
            WHERE d.slug = ? AND c.product_count > 0
            ORDER BY c.name COLLATE NOCASE
            """,
            (department_slug,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def fetch_brands_for_view(department_slug: str, category_slug: str | None = None,
                          limit: int = 60) -> list[dict]:
    """List the brands present in the current department/sub-department view,
    excluding out-of-stock items so the brand sheet only shows brands the
    shopper can actually buy. Sorted by product count desc so the heaviest
    brands surface first; capped to keep the bottom-sheet manageable."""
    clauses = [
        "active = 1",
        "COALESCE(stock_status, 'in_stock') != 'out_of_stock'",
        "brand IS NOT NULL",
        "TRIM(brand) <> ''",
        "department_slug = ?",
    ]
    params: list[object] = [department_slug]
    if category_slug:
        clauses.append("category_slug = ?")
        params.append(category_slug)
    conn = get_conn()
    try:
        rows = conn.execute(
            f"""
            SELECT brand, COUNT(*) AS product_count
            FROM products
            WHERE {' AND '.join(clauses)}
            GROUP BY brand
            ORDER BY product_count DESC, brand COLLATE NOCASE
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
        return [{'name': row['brand'], 'product_count': row['product_count']} for row in rows]
    finally:
        conn.close()


def fetch_department_header(department_slug: str) -> dict | None:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id, name, slug, product_count FROM departments WHERE slug = ?",
            (department_slug,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def fetch_category_header(department_slug: str, category_slug: str) -> dict | None:
    conn = get_conn()
    try:
        row = conn.execute(
            """
            SELECT c.id, c.name, c.slug, c.product_count, d.id AS department_id, d.name AS department_name, d.slug AS department_slug
            FROM categories c
            JOIN departments d ON d.id = c.department_id
            WHERE d.slug = ? AND c.slug = ?
            """,
            (department_slug, category_slug),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def fetch_last_publish_at() -> str | None:
    conn = get_conn()
    try:
        row = conn.execute("SELECT MAX(published_at) AS published_at FROM products").fetchone()
        return row['published_at'] if row else None
    finally:
        conn.close()


def cart_line_key(merkey: str, option_key: str) -> str:
    return f"{merkey}__{option_key or 'retail'}"


def get_cart() -> dict[str, dict]:
    raw_cart = session.get(CART_SESSION_KEY, {})
    if not isinstance(raw_cart, dict):
        return {}
    cart: dict[str, dict] = {}
    for key, value in raw_cart.items():
        if isinstance(value, int):
            try:
                qty = max(1, int(value))
            except (TypeError, ValueError):
                continue
            line_key = cart_line_key(str(key), 'retail')
            cart[line_key] = {
                'merkey': str(key),
                'qty': qty,
                'option_key': 'retail',
                'label': 'Piece',
                'price': None,
                'barcode': None,
                'photo_url': None,
            }
            continue
        if not isinstance(value, dict):
            continue
        merkey = str(value.get('merkey') or '').strip()
        if not merkey:
            continue
        try:
            qty = max(1, int(value.get('qty') or 0))
        except (TypeError, ValueError):
            continue
        option_key = (value.get('option_key') or 'retail').strip() or 'retail'
        line_key = cart_line_key(merkey, option_key)
        price_raw = value.get('price')
        try:
            price = float(price_raw) if price_raw is not None else None
        except (TypeError, ValueError):
            price = None
        cart[line_key] = {
            'merkey': merkey,
            'qty': qty,
            'option_key': option_key,
            'label': (value.get('label') or '').strip() or ('Piece' if option_key == 'retail' else 'Pack / Box'),
            'price': price,
            'barcode': (value.get('barcode') or None),
            'photo_url': (value.get('photo_url') or None),
        }
    return cart


def save_cart(cart: dict[str, dict]) -> None:
    session[CART_SESSION_KEY] = cart
    session.modified = True


def get_cart_count() -> int:
    return sum(line.get('qty', 0) for line in get_cart().values())


def get_editing_request_code() -> str | None:
    code = session.get(EDITING_REQUEST_KEY)
    return code if isinstance(code, str) and code else None


def clear_editing_state() -> None:
    if EDITING_REQUEST_KEY in session:
        session.pop(EDITING_REQUEST_KEY, None)
        session.modified = True


def linked_royal_card(customer_id: int | None) -> str:
    """Return the customer's linked Royal Card EAN-13 if any, else ''."""
    if not customer_id:
        return ''
    conn = get_conn()
    try:
        row = conn.execute("SELECT card_no FROM customers WHERE id = ?", (customer_id,)).fetchone()
        return (row['card_no'] or '').strip() if row else ''
    finally:
        conn.close()


def royalcard_member_lookup(phone_e164: str) -> dict | None:
    """Phase B: ask Supabase whether this +63 mobile is a Royal Card member.

    Returns {'card_no': '...', 'name': '...'} on a hit, else None. Silent no-op
    when SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY is unset (Phase B disabled).
    Fail-open: any HTTP error / timeout / parse failure returns None so the
    storefront sign-in/registration still succeeds — just without the link.
    """
    if not (SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY):
        return None
    phone = (phone_e164 or '').strip()
    if not phone:
        return None
    try:
        qs = urlencode({
            'phone_e164': f'eq.{phone}',
            'select':     'card_no,name',
            'limit':      '1',
        })
        url = f"{SUPABASE_URL}/rest/v1/members?{qs}"
        req = urllib.request.Request(url, headers={
            'apikey':        SUPABASE_SERVICE_ROLE_KEY,
            'Authorization': f'Bearer {SUPABASE_SERVICE_ROLE_KEY}',
            'Accept':        'application/json',
        })
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode('utf-8') or '[]')
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, OSError):
        return None
    if not isinstance(data, list) or not data:
        return None
    row = data[0] or {}
    card_no = (row.get('card_no') or '').strip()
    if not (card_no.isdigit() and len(card_no) == 13):
        return None
    return {'card_no': card_no, 'name': (row.get('name') or '').strip()}


def _try_link_royal_card_to_customer(customer_id: int | None) -> dict | None:
    """Phase B: stamp customers.card_no when the customer's phone matches a
    Royal Card member in Supabase. Idempotent — if card_no is already set, or
    Phase B is disabled, or the phone doesn't match, returns None.

    Called from the /account/login and /account/register success paths after
    the customers row exists and the session is set. The caller flashes a
    success message when this returns the linked member dict.
    """
    if not customer_id:
        return None
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id, phone, display_name, card_no FROM customers WHERE id = ?",
            (customer_id,),
        ).fetchone()
        if not row:
            return None
        # Already linked (e.g. Phase A handled it earlier) — silent no-op.
        if (row['card_no'] or '').strip():
            return None
        phone_intl = normalize_ph_phone(row['phone'] or '')
        if not phone_intl:
            return None
        match = royalcard_member_lookup(phone_intl)
        if not match:
            return None
        # FoxBase stores names as 'LAST, FIRST MIDDLE' — flip for display, same
        # transform Phase A does for parity.
        member_name = match.get('name') or ''
        display = member_name
        if ',' in member_name:
            last, first = member_name.split(',', 1)
            display = f"{first.strip()} {last.strip()}".strip()
        display = display or (row['display_name'] or 'Royal Card Member')

        updates = ["card_no = ?"]
        params: list = [match['card_no']]
        # Only overwrite display_name if the storefront one is empty/placeholder.
        existing_name = (row['display_name'] or '').strip()
        if not existing_name or existing_name.lower().startswith('royal card member'):
            updates.append("display_name = ?")
            params.append(display)
        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(customer_id)
        with conn:
            conn.execute(f"UPDATE customers SET {', '.join(updates)} WHERE id = ?", params)
        return {'card_no': match['card_no'], 'name': display}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Phase C — Royal Card balance / pending bonuses / online redemption helpers
# ---------------------------------------------------------------------------
# All of these silently fall back to None / [] when Phase B Supabase env vars
# are unset or any HTTP/JSON error occurs. The storefront's guest experience
# is *never* affected — these are only invoked when a customer has a linked
# card_no. Per-request caching via flask.g keeps each page to at most one
# Supabase call per resource.

# Max points the customer may redeem on a single online order. Keep this
# small while the feature ships — easier to roll back, easier to support.
ROYALCARD_ONLINE_REDEMPTION_CAP = 500

# Friendly label shown for each `bonus_ledger.reason`. Anything not in this map
# is title-cased with underscores -> spaces.
ROYALCARD_BONUS_REASON_LABELS = {
    'welcome': 'Welcome bonus',
    'referral': 'Referral',
    'birthday': 'Birthday',
    'profile': 'Profile completion',
    'promo': 'Promo',
    'alpha_tester': 'Alpha tester',
    'online_redemption': 'Online redemption',
}


def _royalcard_supabase_ready() -> bool:
    return bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY)


def _royalcard_rest_request(method: str, path: str, *, body: dict | list | None = None,
                            extra_headers: dict | None = None, timeout: float = 3.0):
    """Internal: thin urllib.request wrapper for Supabase REST. Returns the
    parsed JSON body on success, None on any error. Caller decides what an
    empty result means. Service-role bypasses RLS (same as Phase B)."""
    if not _royalcard_supabase_ready():
        return None
    url = f"{SUPABASE_URL}/rest/v1/{path.lstrip('/')}"
    headers = {
        'apikey':        SUPABASE_SERVICE_ROLE_KEY,
        'Authorization': f'Bearer {SUPABASE_SERVICE_ROLE_KEY}',
        'Accept':        'application/json',
    }
    data: bytes | None = None
    if body is not None:
        headers['Content-Type'] = 'application/json'
        data = json.dumps(body).encode('utf-8')
    if extra_headers:
        headers.update(extra_headers)
    try:
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode('utf-8') or 'null'
        return json.loads(raw)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, OSError):
        return None


def fetch_member_balance(card_no: str | None) -> int | None:
    """Phase C1 — current point balance for `card_no` from Supabase `member_card`
    (security_invoker view; service_role bypasses RLS). Returns int balance, or
    None if Phase C is disabled, card_no is empty, or any Supabase error.

    Cached on flask.g per-request so a single page render makes at most one
    Supabase call even when both the chip and the checkout form ask for it.
    No long-lived cache — a redemption elsewhere would go stale immediately.
    """
    card = (card_no or '').strip()
    if not card or not card.isdigit() or len(card) != 13:
        return None
    if not _royalcard_supabase_ready():
        return None
    try:
        cache = g.setdefault('_royalcard_balance_cache', {})
    except RuntimeError:
        # Outside a request context (e.g. cron-style invocation). Skip cache.
        cache = None
    if cache is not None and card in cache:
        return cache[card]
    qs = urlencode({
        'card_no': f'eq.{card}',
        'select':  'points_balance',
        'limit':   '1',
    })
    data = _royalcard_rest_request('GET', f'member_card?{qs}')
    balance: int | None = None
    if isinstance(data, list) and data:
        raw = (data[0] or {}).get('points_balance')
        try:
            balance = int(raw) if raw is not None else None
        except (TypeError, ValueError):
            balance = None
    if cache is not None:
        cache[card] = balance
    return balance


def fetch_member_pending_bonuses(card_no: str | None) -> list[dict]:
    """Phase C2 — pending rows for `card_no` from Supabase `bonus_ledger`.
    Returns list of dicts with keys: points (int), reason (str),
    reason_label (friendly), detail (str), created_at (str), peso_value (int).
    Returns [] on any error or when Phase C is disabled. Negative-point rows
    (redemptions) are excluded — this list is intended to surface bonuses
    that are *waiting* to land in the member's balance.

    Cached on flask.g per-request, same as fetch_member_balance.
    """
    card = (card_no or '').strip()
    if not card or not card.isdigit() or len(card) != 13:
        return []
    if not _royalcard_supabase_ready():
        return []
    try:
        cache = g.setdefault('_royalcard_pending_cache', {})
    except RuntimeError:
        cache = None
    if cache is not None and card in cache:
        return cache[card]
    qs = urlencode({
        'card_no': f'eq.{card}',
        'status':  'eq.pending',
        'select':  'points,reason,detail,created_at',
        'order':   'created_at.desc',
    })
    data = _royalcard_rest_request('GET', f'bonus_ledger?{qs}')
    result: list[dict] = []
    if isinstance(data, list):
        for row in data:
            if not isinstance(row, dict):
                continue
            try:
                pts = int(row.get('points') or 0)
            except (TypeError, ValueError):
                pts = 0
            # Surface only positive (pending earn) rows on the profile page;
            # online-redemption negatives are an internal accounting concern.
            if pts <= 0:
                continue
            reason = (row.get('reason') or '').strip()
            friendly = ROYALCARD_BONUS_REASON_LABELS.get(
                reason, reason.replace('_', ' ').capitalize() or 'Bonus'
            )
            result.append({
                'points':       pts,
                'reason':       reason,
                'reason_label': friendly,
                'detail':       (row.get('detail') or '').strip(),
                'created_at':   (row.get('created_at') or '')[:10],
                'peso_value':   pts,   # 1 pt = ~P1 in-store; UI shows "value"
            })
    if cache is not None:
        cache[card] = result
    return result


def apply_redemption_to_ledger(card_no: str, points: int, order_request_id: int,
                                order_code: str) -> dict | None:
    """Phase C3/C4 — write a NEGATIVE bonus_ledger row representing the
    customer's redemption at online checkout. Returns the inserted row dict
    (with at least 'id') on success, or None on any error. Idempotency is
    delegated to the caller (the order should only insert one redemption
    row per submit; we tag detail='order:<code>' so duplicates are easy to
    spot if a retry sneaks through)."""
    card = (card_no or '').strip()
    if not card or not card.isdigit() or len(card) != 13:
        return None
    try:
        pts = int(points)
    except (TypeError, ValueError):
        return None
    if pts <= 0:
        return None
    if not _royalcard_supabase_ready():
        return None
    payload = [{
        'card_no':  card,
        'points':   -pts,
        'reason':   'online_redemption',
        'detail':   f'order:{order_code}',
        'status':   'pending',
    }]
    data = _royalcard_rest_request(
        'POST', 'bonus_ledger',
        body=payload,
        extra_headers={'Prefer': 'return=representation'},
    )
    if isinstance(data, list) and data:
        row = data[0] or {}
        if 'id' in row:
            return row
    return None


def cancel_redemption_for_order(order_request_id: int, order_code: str) -> bool:
    """Phase C3 edge case — when an order is cancelled, flip its pending
    online-redemption ledger row to 'cancelled' so settle_bonuses.py skips
    it and the points aren't debited from the member at EOD. Idempotent;
    returns True if a row was flipped (or there was nothing to flip)."""
    if not order_code:
        return False
    if not _royalcard_supabase_ready():
        # Phase C disabled — nothing to cancel. Treat as success so the
        # cancel flow isn't blocked.
        return True
    qs = urlencode({
        'reason':  'eq.online_redemption',
        'detail':  f'eq.order:{order_code}',
        'status':  'eq.pending',
    })
    data = _royalcard_rest_request(
        'PATCH', f'bonus_ledger?{qs}',
        body={'status': 'cancelled'},
        extra_headers={'Prefer': 'return=minimal'},
    )
    # PATCH with return=minimal returns null on success; None can also mean
    # error. We accept either — the cancel path shouldn't fail loudly.
    return True


def customer_first_name_from(display_name: str | None) -> str:
    """Pull the first word out of a display name for casual greeting use.
    Empty input → empty string (caller chooses the fallback)."""
    if not display_name:
        return ''
    first = display_name.strip().split()
    return first[0] if first else ''


# Order statuses that mean "the customer has actually received goods" — used
# to decide whether to surface the "Reorder last basket" hero. We accept both
# the modern RELEASED state and the legacy synonyms (COMPLETED/FULFILLED).
_FULFILLED_ORDER_STATUSES = ('RELEASED', 'COMPLETED', 'FULFILLED')


def fetch_customer_last_fulfilled_order(customer_id: int | None) -> dict | None:
    """Look up the most recent fulfilled order for this customer plus a tiny
    summary (item count, total, date) for the Reorder hero card. Returns None
    when the customer has no completed orders yet."""
    if not customer_id:
        return None
    placeholders = ','.join('?' for _ in _FULFILLED_ORDER_STATUSES)
    conn = get_conn()
    try:
        order_row = conn.execute(
            f"""
            SELECT id, request_code, confirmed_total, item_count, created_at
            FROM order_requests
            WHERE customer_id = ?
              AND status IN ({placeholders})
            ORDER BY COALESCE(confirmed_at, updated_at, created_at) DESC, id DESC
            LIMIT 1
            """,
            (customer_id, *_FULFILLED_ORDER_STATUSES),
        ).fetchone()
        if not order_row:
            return None
        # Live count of non-removed items + their quoted_price total. The
        # stored confirmed_total may be stale if the picker pulled items, so
        # we re-tally from order_request_items for honesty.
        agg = conn.execute(
            """
            SELECT COUNT(*) AS n,
                   COALESCE(SUM(requested_qty * COALESCE(quoted_price, 0)), 0) AS total
            FROM order_request_items
            WHERE order_request_id = ? AND removed = 0
            """,
            (order_row['id'],),
        ).fetchone()
        live_item_count = int(agg['n'] or 0)
        live_total = float(agg['total'] or 0)
        if live_item_count <= 0:
            return None
        return {
            'request_code': order_row['request_code'],
            'item_count': live_item_count,
            'total': live_total if live_total > 0 else float(order_row['confirmed_total'] or 0),
            'date': order_row['created_at'] or '',
        }
    finally:
        conn.close()


def fetch_homepage_top_sellers(limit: int = 10, roster_size: int = 60) -> list[dict]:
    """Top sellers for the homepage grid — rotated so it doesn't go stale.

    Sorting purely by txn_count_24m showed the same handful of items forever
    (a 24-month total barely moves). Instead we pull a ROSTER of the top
    `roster_size` sellers (in stock, homepage-eligible) and display a rotating
    window of `limit`, advancing one window per day — so the section cycles
    through the whole roster every ~roster_size/limit days and feels fresh.
    """
    tile_clause, tile_params = homepage_tile_filter()
    conn = get_conn()
    try:
        rows = conn.execute(
            f"""
            SELECT *
            FROM products
            WHERE active = 1
              AND COALESCE(stock_status, 'in_stock') != 'out_of_stock'
              AND COALESCE(sellable_state, '') != 'browse_only'
              AND {tile_clause}
            ORDER BY COALESCE(txn_count_24m, 0) DESC,
                     CASE WHEN COALESCE(priority, '') = 'TOP' THEN 0 ELSE 1 END,
                     name COLLATE NOCASE
            LIMIT ?
            """,
            [*tile_params, roster_size],
        ).fetchall()
    finally:
        conn.close()

    roster = [build_product_dict(row) for row in rows]
    if len(roster) <= limit:
        return roster

    # Rotate a window of `limit` through the roster, advancing one window per
    # day. Deterministic per day (stable within a day, fresh the next), and
    # wraps so it always returns exactly `limit` items.
    from datetime import date
    offset = (date.today().toordinal() * limit) % len(roster)
    return [roster[(offset + i) % len(roster)] for i in range(limit)]


def fetch_homepage_friendly_departments() -> list[dict]:
    """Map the 16 friendly shopper-language buckets to existing department
    slugs that actually have products. Buckets whose target department has
    zero products are dropped silently (rare — most have thousands)."""
    available = {dept['slug'] for dept in fetch_departments() if dept.get('product_count', 0) > 0}
    return [bucket for bucket in FRIENDLY_DEPARTMENT_BUCKETS if bucket['department_slug'] in available]


def normalize_ph_phone(raw: str) -> str:
    digits = ''.join(ch for ch in (raw or '') if ch.isdigit())
    if not digits:
        return ''
    if digits.startswith('63') and len(digits) >= 12:
        return '+' + digits
    if digits.startswith('0') and len(digits) == 11:
        return '+63' + digits[1:]
    if digits.startswith('9') and len(digits) == 10:
        return '+63' + digits
    return '+' + digits


def build_admin_contact_links(order_request: dict, customer_update_message: str) -> dict:
    contact_number = (order_request.get('contact_number') or '').strip()
    contact_email = (order_request.get('contact_email') or '').strip()
    request_code = order_request.get('request_code') or ''
    message = customer_update_message or ''
    subject = f"{request_code} update" if request_code else 'Order update'
    phone_intl = normalize_ph_phone(contact_number)
    phone_digits = phone_intl.lstrip('+') if phone_intl else ''

    links = {
        'phone_display': contact_number,
        'phone_intl': phone_intl,
        'email': contact_email,
        'mailto': (
            f"mailto:{contact_email}?subject={quote(subject)}&body={quote(message)}"
            if contact_email else ''
        ),
        'gmail_compose': (
            'https://mail.google.com/mail/?'
            + urlencode({'view': 'cm', 'fs': '1', 'to': contact_email, 'su': subject, 'body': message})
            if contact_email else ''
        ),
        'outlook_compose': (
            'https://outlook.office.com/mail/deeplink/compose?'
            + urlencode({'to': contact_email, 'subject': subject, 'body': message})
            if contact_email else ''
        ),
        'viber_chat': (
            f"viber://chat?number={quote(phone_intl)}" if phone_intl else ''
        ),
        'viber_call': (
            f"viber://contact?number={quote(phone_intl)}" if phone_intl else ''
        ),
        'whatsapp': (
            f"https://wa.me/{phone_digits}?text={quote(message)}" if phone_digits else ''
        ),
    }
    return links


def build_store_contact_links(order_request: dict | None = None) -> dict:
    """Customer-facing 'Message ANSON' links — same key shape as
    build_admin_contact_links, but pointing at the store's contact handles
    instead of the customer's own number. Mirrors the admin variant so the
    template doesn't have to branch on which one it received."""
    request_code = (order_request or {}).get('request_code') or ''
    subject = f"{request_code} — order question" if request_code else f"{STORE_CONTACT_NAME} — order question"
    body = f"Hi {STORE_CONTACT_NAME}, I have a question about my order {request_code}.".strip()
    phone_intl = normalize_ph_phone(STORE_CONTACT_PHONE)
    phone_digits = phone_intl.lstrip('+') if phone_intl else ''
    return {
        'phone_display':    STORE_CONTACT_PHONE,
        'phone_intl':       phone_intl,
        'email':            STORE_CONTACT_EMAIL,
        'mailto':           (f"mailto:{STORE_CONTACT_EMAIL}?subject={quote(subject)}&body={quote(body)}"
                             if STORE_CONTACT_EMAIL else ''),
        'gmail_compose':    ('https://mail.google.com/mail/?'
                             + urlencode({'view': 'cm', 'fs': '1', 'to': STORE_CONTACT_EMAIL, 'su': subject, 'body': body})
                             if STORE_CONTACT_EMAIL else ''),
        'viber_chat':       (f"viber://chat?number={quote(phone_intl)}" if phone_intl else ''),
        'viber_call':       (f"viber://contact?number={quote(phone_intl)}" if phone_intl else ''),
        'whatsapp':         (f"https://wa.me/{phone_digits}?text={quote(body)}" if phone_digits else ''),
        'messenger':        STORE_MESSENGER_URL,
        'store_name':       STORE_CONTACT_NAME,
    }


def get_editing_cart_url() -> str:
    code = get_editing_request_code()
    if code and session.get(ADMIN_SESSION_KEY):
        return url_for('admin_edit_cart_page', request_code=code)
    return url_for('cart_page')


@app.context_processor
def inject_editing_request_state():
    editing_code = get_editing_request_code()
    return {
        'editing_request_code': editing_code,
        'editing_cart_url': get_editing_cart_url() if editing_code else url_for('cart_page'),
    }


@app.context_processor
def inject_staff_view():
    # When a staff member is logged in (same admin session used for order
    # management), the storefront reveals the full catalog view — internal
    # Details (supplier/barcode/class/merkey), QC badges, the per-piece price
    # context, and the chilled/Mode-2 price. Customers see the clean shop.
    return {'staff_view': bool(session.get(ADMIN_SESSION_KEY))}


def viewer_is_staff() -> bool:
    """True when the current request is a logged-in staff/admin session.
    Customer-facing listings and search hide 'browse only' (no-photo, not
    orderable) items — they're admin-view-only until photographed. Staff see
    everything. Returns False safely outside a request context."""
    try:
        return bool(session.get(ADMIN_SESSION_KEY))
    except RuntimeError:
        return False


def wants_json_response() -> bool:
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return True
    best = request.accept_mimetypes.best_match(['application/json', 'text/html'])
    return best == 'application/json' and request.accept_mimetypes[best] > request.accept_mimetypes['text/html']


def empty_request_form_data() -> dict[str, str]:
    return {
        'customer_name': '',
        'contact_number': '',
        'contact_email': '',
        'fulfillment_method': '',
        'preferred_schedule': '',
        'location_details': '',
        'fulfillment_notes': '',
    }


def resolve_sort(sort_key: str | None) -> tuple[str, str]:
    key = sort_key if sort_key in SORT_OPTIONS else 'top'
    return key, SORT_OPTIONS[key][0]


def resolve_price_band(price_band: str | None) -> tuple[str, int | None, int | None]:
    key = price_band if price_band in PRICE_BANDS else ''
    _, minimum, maximum = PRICE_BANDS[key]
    return key, minimum, maximum


def build_badges(row: sqlite3.Row, is_fresh: bool) -> list[dict]:
    badges: list[dict] = []
    if str(row['merkey']) in LIMITED_PROMO_MERKEYS:
        badges.append({'label': 'Limited Promo', 'tone': 'promo'})
    if (row['priority'] or '').strip().upper() == 'TOP':
        badges.append({'label': 'Top Seller', 'tone': 'top'})
    if is_fresh:
        badges.append({'label': 'Fresh', 'tone': 'fresh'})
    if row['needs_irl_photo']:
        badges.append({'label': 'Needs Better Photo', 'tone': 'photo'})
    return badges


def build_availability(sellable_state: str, stock_status: str = 'in_stock') -> dict:
    if stock_status == 'out_of_stock':
        return {
            'label': 'Out of stock',
            'note': 'This item is currently out of stock.',
            'cta_label': 'Out of stock',
            'stock_status': 'out_of_stock',
        }
    mapping = {
        'orderable': {
            'label': 'Low stock' if stock_status == 'low_stock' else 'In stock',
            'note': 'This item is ready to order online.',
            'cta_label': 'Add to basket',
            'stock_status': stock_status,
        },
        'review_required': {
            'label': 'Fresh item',
            'note': 'Final packed weight and availability are confirmed at fulfillment.',
            'cta_label': 'Add to basket',
            'stock_status': stock_status,
        },
        'browse_only': {
            'label': 'Browse only',
            'note': 'This item is visible for browsing while catalog details are still being finalized.',
            'cta_label': 'Browse only',
            'stock_status': stock_status,
        },
    }
    return mapping.get(sellable_state, mapping['browse_only'])


def build_fulfillment(product_row: sqlite3.Row, availability: dict, display_size: str | None) -> dict:
    fulfillment_type = (product_row['fulfillment_type'] or '').strip().lower() or 'standard'
    substitution_policy = (product_row['substitution_policy'] or '').strip().lower() or 'standard'
    order_unit_label = (product_row['order_unit_label'] or display_size or '').strip()
    fulfillment_note = (product_row['fulfillment_note'] or '').strip()

    labels = {
        'catalog_only': 'Catalog only',
        'variable_weight': 'Variable weight',
        'fixed_pack': 'Fixed pack',
        'standard': 'Standard item',
    }
    substitution_labels = {
        'not_applicable': 'Ordering not enabled',
        'confirm_at_fulfillment': 'Final quantity confirmed by staff',
        'fixed_pack': 'Published pack only',
        'standard': 'Standard shelf item',
    }

    if not fulfillment_note:
        fulfillment_note = availability['note']

    return {
        'type': fulfillment_type,
        'label': labels.get(fulfillment_type, 'Standard item'),
        'order_unit_label': order_unit_label,
        'substitution_label': substitution_labels.get(substitution_policy, 'Standard shelf item'),
        'note': fulfillment_note,
    }


def is_order_request_allowed(product: dict) -> bool:
    return product['sellable_state'] in {'orderable', 'review_required'}


def build_price_subtext(row: sqlite3.Row, card_price: float | None) -> str | None:
    pricing_basis = (row['pricing_basis'] or '').strip().lower()
    price_retail = row['price_retail']
    if pricing_basis == 'per_kg' and price_retail is not None:
        return f"P{price_retail:,.2f}/kg"
    if card_price is not None and price_retail is not None and card_price != price_retail:
        return f"P{price_retail:,.2f} base price"
    return None


def build_product_dict(row: sqlite3.Row) -> dict:
    photo_url = row['photo_url'] or PLACEHOLDER_PHOTO
    if photo_url.startswith('http://'):
        photo_url = photo_url.replace('http://', 'https://', 1)
    display_size = normalize_quantity_label(row['range_label'] or row['size'])
    card_price = row['display_price'] if row['display_price'] is not None else row['price_retail']
    department_slug = row['department_slug'] or slugify(row['department_name'] or '')
    is_fresh = department_slug in FRESH_DEPARTMENT_SLUGS or (row['class_l3_name'] or '') in FRESH_CLASS_HINTS
    sellable_state = (row['sellable_state'] or '').strip().lower() or 'browse_only'
    stock_status = (row['stock_status'] or 'in_stock').strip().lower() if 'stock_status' in row.keys() else 'in_stock'
    availability = build_availability(sellable_state, stock_status)
    fulfillment = build_fulfillment(row, availability, display_size)
    show_pack_price = bool(row['show_pack_on_storefront']) if 'show_pack_on_storefront' in row.keys() else False
    default_selling_option = (row['default_selling_option'] or 'retail').strip().lower() if 'default_selling_option' in row.keys() else 'retail'
    if default_selling_option not in {'retail', 'pack', 'case'}:
        default_selling_option = 'retail'
    pack_price = row['price_pack'] if 'price_pack' in row.keys() else None
    pack_label = (row['pack_display_label'] or '').strip() if 'pack_display_label' in row.keys() else ''
    pack_quantity = int(row['pack_quantity'] or 0) if 'pack_quantity' in row.keys() else 0
    pack_photo_url = (row['pack_photo_url'] or '').strip() if 'pack_photo_url' in row.keys() else ''
    pack_barcode = (row['pack_barcode'] or '').strip() if 'pack_barcode' in row.keys() else ''
    show_case_price = bool(row['show_case_on_storefront']) if 'show_case_on_storefront' in row.keys() else False
    case_price = row['price_case'] if 'price_case' in row.keys() else None
    case_label = (row['case_display_label'] or '').strip() if 'case_display_label' in row.keys() else ''
    case_quantity = int(row['case_quantity'] or 0) if 'case_quantity' in row.keys() else 0
    case_photo_url = (row['case_photo_url'] or '').strip() if 'case_photo_url' in row.keys() else ''
    case_barcode = (row['case_barcode'] or '').strip() if 'case_barcode' in row.keys() else ''
    # "Cold price" detection: for beverages (and any item where the Mode-2/pack
    # price is only a small markup over retail), the second price is really the
    # chilled price, not a genuine larger pack. Flag it staff_only so customers
    # see one clean price while staff still get the full picture. Genuine packs
    # (a meaningfully bigger gap, e.g. a real case) are NOT flagged.
    _retail_val = float(row['price_retail']) if row['price_retail'] is not None else 0.0
    pack_is_cold = bool(
        pack_price is not None and float(pack_price) > 0 and (
            department_slug == 'beverages'
            or (_retail_val > 0 and float(pack_price) < _retail_val * 1.5)
        )
    )
    selling_options = []
    if row['price_retail'] is not None:
        selling_options.append({
            'key': 'retail',
            'label': 'Piece',
            'price': float(row['price_retail']),
            'barcode': (row['barcode'] or '').strip() or None,
            'photo_url': photo_url,
            'size_label': display_size or None,
            'is_default': default_selling_option != 'pack',
            'staff_only': False,
        })
    if show_pack_price and pack_price is not None and float(pack_price) > 0:
        selling_options.append({
            'key': 'pack',
            'label': pack_label or 'Pack / Box',
            'price': float(pack_price),
            'barcode': pack_barcode or None,
            'photo_url': pack_photo_url or None,
            'size_label': pack_label or display_size or None,
            'contains_label': f"Contains {pack_quantity} × {display_size}" if pack_quantity > 1 and display_size else None,
            'is_default': default_selling_option == 'pack',
            'staff_only': pack_is_cold,
        })
    if show_case_price and case_price is not None and float(case_price) > 0:
        selling_options.append({
            'key': 'case',
            'label': case_label or 'Case / Sack',
            'price': float(case_price),
            'barcode': case_barcode or None,
            'photo_url': case_photo_url or None,
            'size_label': case_label or display_size or None,
            'contains_label': f"Contains {case_quantity} × {display_size}" if case_quantity > 1 and display_size else None,
            'is_default': default_selling_option == 'case',
            'staff_only': False,
        })
    if selling_options and not any(opt.get('is_default') for opt in selling_options):
        selling_options[0]['is_default'] = True
    default_option = next((opt for opt in selling_options if opt.get('is_default')), selling_options[0] if selling_options else None)
    # "Sell only as this size" — when set, the storefront hides every option
    # except the default one, so the item is offered exclusively at that size
    # (e.g. an inner box that isn't sold per piece). Storefront-only: the POS /
    # other barcodes are unaffected. Falls back gracefully if the chosen option
    # isn't available (e.g. pack price missing) by keeping whatever default we
    # resolved above.
    exclusive_selling_option = bool(row['exclusive_selling_option']) if 'exclusive_selling_option' in row.keys() else False
    if exclusive_selling_option and default_option is not None:
        selling_options = [default_option]
        default_option['is_default'] = True
    pack_option = selling_options[1] if len(selling_options) > 1 else None
    return {
        'merkey': row['merkey'],
        'slug': row['slug'] or slugify(row['name'] or ''),
        'name': row['name'] or '',
        'brand': row['brand'] or '',
        'description': row['description'] or '',
        'department_name': row['department_name'] or '',
        'department_slug': department_slug,
        'category_name': row['category_name'] or '',
        'category_slug': row['category_slug'] or '',
        'class_l2_name': row['class_l2_name'] or '',
        'class_l3_name': row['class_l3_name'] or '',
        'card_price': (default_option.get('price') if default_option else card_price) or 0.0,
        'price_retail': row['price_retail'],
        'price_subtext': (
            build_price_subtext(row, card_price)
            if not default_option or default_option.get('key') == 'retail'
            # Exclusive items hide the per-piece price entirely; otherwise show
            # it as context beneath the default (pack/case) price.
            else ('' if exclusive_selling_option else f"P{float(row['price_retail'] or 0):,.2f} piece price")
        ),
        # The "piece price" context line under a pack/case default is a duplicate
        # second price for customers — keep it staff-only. The per-kg subtext
        # (fresh items) stays customer-facing.
        'price_subtext_staff_only': bool(
            default_option and default_option.get('key') != 'retail' and not exclusive_selling_option
        ),
        'show_pack_on_storefront': show_pack_price,
        'pack_option': pack_option,
        'exclusive_selling_option': exclusive_selling_option,
        'default_selling_option': default_option.get('key') if default_option else 'retail',
        'default_selling_option_label': default_option.get('label') if default_option else 'Piece',
        'selling_options': selling_options,
        'display_size': display_size,
        'size': row['size'] or '',
        'photo_url': photo_url,
        'display_photo_url': photo_url,
        'badges': build_badges(row, is_fresh),
        'availability': {
            'label': availability['label'],
            'note': row['sellable_note'] or availability['note'],
        },
        'fulfillment': fulfillment,
        'cta_label': availability['cta_label'],
        'can_request_order': sellable_state in {'orderable', 'review_required'} and stock_status != 'out_of_stock',
        'priority': row['priority'] or '',
        'txn_count_24m': row['txn_count_24m'] or 0,
        'last_sale_date': row['last_sale_date'] or '',
        'pricing_basis': row['pricing_basis'] or '',
        'supplier_name': row['supplier_name'] or '',
        'barcode': row['barcode'] or '',
        'sellable_state': sellable_state,
        'needs_irl_photo': bool(row['needs_irl_photo']),
    }


def homepage_tile_filter() -> tuple[str, list[object]]:
    """SQL fragment that limits homepage tiles to FMCG-headline departments and
    excludes bag/cigarette classes. Returns (clause_sql, params)."""
    dept_q = ','.join('?' for _ in HOMEPAGE_TILE_DEPARTMENT_SLUGS)
    excl_q = ','.join('?' for _ in HOMEPAGE_TILE_EXCLUDED_L3)
    clause = (
        f"department_slug IN ({dept_q}) "
        f"AND COALESCE(class_l3_name, '') NOT IN ({excl_q})"
    )
    params: list[object] = list(HOMEPAGE_TILE_DEPARTMENT_SLUGS) + list(HOMEPAGE_TILE_EXCLUDED_L3)
    return clause, params


def fetch_featured_products(limit: int = 8) -> list[dict]:
    tile_clause, tile_params = homepage_tile_filter()
    conn = get_conn()
    try:
        rows = conn.execute(
            f"""
            SELECT *
            FROM products
            WHERE active = 1 AND {tile_clause}
            ORDER BY CASE WHEN COALESCE(priority, '') = 'TOP' THEN 0 ELSE 1 END,
                     COALESCE(txn_count_24m, 0) DESC,
                     name COLLATE NOCASE
            LIMIT ?
            """,
            (*tile_params, limit),
        ).fetchall()
        return [build_product_dict(row) for row in rows]
    finally:
        conn.close()


def fetch_rotating_featured_collection(limit: int = 8) -> dict:
    tile_clause, tile_params = homepage_tile_filter()
    featured_modes = [
        {
            'key': 'top_sellers',
            'eyebrow': 'Top Sellers',
            'title': 'Featured top sellers',
            'lede': 'Reliable fast movers and familiar basket-builders from across the catalog.',
            'where_sql': f'active = 1 AND {tile_clause}',
            'params': list(tile_params),
            'order_sql': "COALESCE(txn_count_24m, 0) DESC, CASE WHEN COALESCE(priority, '') = 'TOP' THEN 0 ELSE 1 END, name COLLATE NOCASE",
        },
        {
            'key': 'fresh_picks',
            'eyebrow': 'Fresh Picks',
            'title': 'Featured fresh picks',
            'lede': 'Fresh-market items with current availability and strong selling activity.',
            'where_sql': f"active = 1 AND department_slug = 'fresh' AND {tile_clause}",
            'params': list(tile_params),
            'order_sql': "COALESCE(last_sale_date, '') DESC, COALESCE(txn_count_24m, 0) DESC, name COLLATE NOCASE",
        },
        {
            'key': 'value_picks',
            'eyebrow': 'Budget Picks',
            'title': 'Featured budget-friendly picks',
            'lede': 'Everyday staples at approachable price points for quick grocery baskets.',
            'where_sql': f"active = 1 AND COALESCE(display_price, price_retail, 0) BETWEEN 1 AND 150 AND {tile_clause}",
            'params': list(tile_params),
            'order_sql': "COALESCE(txn_count_24m, 0) DESC, COALESCE(display_price, price_retail, 0) ASC, name COLLATE NOCASE",
        },
        {
            'key': 'recently_bought',
            'eyebrow': 'Recently Bought',
            'title': 'Featured recently bought items',
            'lede': 'Items with very recent sales activity to keep the homepage feeling current.',
            'where_sql': f"active = 1 AND COALESCE(last_sale_date, '') >= date('now', '-7 days') AND {tile_clause}",
            'params': list(tile_params),
            'order_sql': "COALESCE(last_sale_date, '') DESC, COALESCE(txn_count_24m, 0) DESC, name COLLATE NOCASE",
        },
    ]
    mode = featured_modes[datetime.now().date().toordinal() % len(featured_modes)]

    conn = get_conn()
    try:
        rows = conn.execute(
            f"""
            SELECT *
            FROM products
            WHERE {mode['where_sql']}
            ORDER BY {mode['order_sql']}
            LIMIT ?
            """,
            [*mode['params'], limit],
        ).fetchall()
        products = [build_product_dict(row) for row in rows]
        if not products:
            products = fetch_featured_products(limit=limit)
            mode = {
                'key': 'fallback',
                'eyebrow': 'Featured',
                'title': 'Featured products',
                'lede': 'A rotating selection of dependable grocery picks from the catalog.',
            }
        return {
            **mode,
            'products': products,
        }
    finally:
        conn.close()


def fetch_department_spotlights(limit_per_department: int = 6) -> list[dict]:
    sections: list[dict] = []
    departments = fetch_departments(order_by_count=True)
    headline_slugs = set(HOMEPAGE_TILE_DEPARTMENT_SLUGS)
    excl_q = ','.join('?' for _ in HOMEPAGE_TILE_EXCLUDED_L3)
    conn = get_conn()
    try:
        for department in departments:
            if department['slug'] not in headline_slugs:
                continue
            rows = conn.execute(
                f"""
                SELECT *
                FROM products
                WHERE active = 1 AND department_id = ?
                  AND COALESCE(class_l3_name, '') NOT IN ({excl_q})
                ORDER BY CASE WHEN COALESCE(priority, '') = 'TOP' THEN 0 ELSE 1 END,
                         COALESCE(txn_count_24m, 0) DESC,
                         name COLLATE NOCASE
                LIMIT ?
                """,
                (department['id'], *HOMEPAGE_TILE_EXCLUDED_L3, limit_per_department),
            ).fetchall()
            products = [build_product_dict(row) for row in rows]
            products = trim_orphaned_grid_items(products, HOMEPAGE_SPOTLIGHT_ROW_SIZE)
            if products:
                sections.append({'department': department, 'products': products})
        return sections
    finally:
        conn.close()


def fetch_products(
    *,
    department_slug: str | None = None,
    category_slug: str | None = None,
    category_list: list[str] | None = None,
    brand_name: str | None = None,
    brand_list: list[str] | None = None,
    promo_only: bool = False,
    hide_out_of_stock: bool = False,
    search_query: str = '',
    page: int = 1,
    per_page: int = PRODUCT_GRID_PAGE_SIZE,
    sort_key: str = 'top',
    price_band: str = '',
) -> tuple[list[dict], dict, str, str]:
    resolved_sort_key, sort_clause = resolve_sort(sort_key)
    resolved_price_band, min_price, max_price = resolve_price_band(price_band)

    clauses = ['active = 1']
    params: list[object] = []

    if department_slug:
        clauses.append('department_slug = ?')
        params.append(department_slug)
    if category_slug:
        clauses.append('category_slug = ?')
        params.append(category_slug)
    if category_list:
        # Multi-aisle filter (friendly Browse-Categories buckets that span
        # several real categories). Sanitize and bail on empty to avoid IN ().
        cleaned_categories = [c.strip() for c in category_list if c and c.strip()]
        if cleaned_categories:
            placeholders = ','.join('?' for _ in cleaned_categories)
            clauses.append(f'category_slug IN ({placeholders})')
            params.extend(cleaned_categories)
    if brand_name:
        clauses.append('brand = ?')
        params.append(brand_name)
    if brand_list:
        # Multi-select brand filter from the category page filter sheet.
        # Sanitize: keep non-empty unique strings; bail if nothing's left so
        # we don't emit `IN ()` (which SQLite rejects).
        cleaned_brands = [b.strip() for b in brand_list if b and b.strip()]
        if cleaned_brands:
            placeholders = ','.join('?' for _ in cleaned_brands)
            clauses.append(f'brand IN ({placeholders})')
            params.extend(cleaned_brands)
    if promo_only:
        # "Promo only" filter — items priced below their retail (display_price
        # markdown) OR explicitly flagged TOP. Matches what would visibly look
        # like a promo to a shopper. Mirrors build_badges() logic.
        clauses.append(
            "(COALESCE(priority, '') = 'TOP' OR "
            "(display_price IS NOT NULL AND price_retail IS NOT NULL "
            "AND display_price < price_retail))"
        )
    if hide_out_of_stock:
        # Out-of-stock items hidden from the category grid by default (honest
        # stock signal — they're not orderable, so don't show them at all).
        clauses.append("COALESCE(stock_status, 'in_stock') != 'out_of_stock'")
    if not viewer_is_staff():
        # "Browse only" items (no photo, not orderable) are admin-view-only —
        # hidden from customer listings. Staff see them in the catalog view.
        clauses.append("COALESCE(sellable_state, '') != 'browse_only'")
    if search_query:
        # Tokenize so "chicken nuggets" matches "Chicken Breast Nuggets" (each
        # token must appear in search_text, but not necessarily adjacent).
        tokens = [t for t in search_query.lower().split() if t]
        for token in tokens:
            clauses.append('search_text LIKE ?')
            params.append(f'%{token}%')
    if min_price is not None:
        clauses.append(f'{DISPLAY_PRICE_SQL} >= ?')
        params.append(min_price)
    if max_price is not None:
        clauses.append(f'{DISPLAY_PRICE_SQL} < ?')
        params.append(max_price)

    where_sql = ' AND '.join(clauses)

    conn = get_conn()
    try:
        total_row = conn.execute(f'SELECT COUNT(*) AS c FROM products WHERE {where_sql}', params).fetchone()
        total_count = total_row['c'] if total_row else 0
        pager = make_pager(page, total_count, per_page)
        offset = (pager['page'] - 1) * per_page
        rows = conn.execute(
            f"""
            SELECT *
            FROM products
            WHERE {where_sql}
            ORDER BY {sort_clause}
            LIMIT ? OFFSET ?
            """,
            [*params, per_page, offset],
        ).fetchall()
        return [build_product_dict(row) for row in rows], pager, resolved_sort_key, resolved_price_band
    finally:
        conn.close()


def search_products(
    search_query: str,
    department_slug: str | None = None,
    price_band: str = '',
    sort_key: str = 'relevance',
    page: int = 1,
    per_page: int = PRODUCT_GRID_PAGE_SIZE,
) -> dict:
    """Intelligent product search.

    Runs the FTS cascade (search_engine.run_search) to get a relevance-ranked
    set of merkeys, then applies department/price filters and pagination in
    SQL. Default sort is relevance (FTS rank); an explicit sort_key overrides
    it. Returns a dict with products, pager, the resolved sort/price keys, and
    search metadata (mode, did_you_mean, suggestions).
    """
    resolved_sort_key, sort_clause = resolve_sort(sort_key)
    resolved_price_band, min_price, max_price = resolve_price_band(price_band)

    conn = get_conn()
    try:
        # Keep the index in sync with republished catalogs without a restart.
        search_engine.ensure_search_index(conn)
        outcome = search_engine.run_search(conn, search_query, synonyms=SEARCH_SYNONYMS)
        merkeys = outcome.merkeys

        empty_result = {
            'products': [],
            'pager': make_pager(1, 0, per_page),
            'sort_key': resolved_sort_key,
            'price_band': resolved_price_band,
            'mode': outcome.mode,
            'did_you_mean': outcome.did_you_mean,
            'suggestions': outcome.suggestions,
        }
        if not merkeys:
            return empty_result

        # Preserve FTS rank order via an ordinal map, then filter in SQL.
        rank = {mk: i for i, mk in enumerate(merkeys)}
        placeholders = ','.join('?' for _ in merkeys)
        clauses = [f'merkey IN ({placeholders})', 'active = 1']
        params: list[object] = list(merkeys)
        if not viewer_is_staff():
            # Browse-only (no-photo) items are admin-view-only — keep them out
            # of customer search results too.
            clauses.append("COALESCE(sellable_state, '') != 'browse_only'")
        if department_slug:
            clauses.append('department_slug = ?')
            params.append(department_slug)
        if min_price is not None:
            clauses.append(f'{DISPLAY_PRICE_SQL} >= ?')
            params.append(min_price)
        if max_price is not None:
            clauses.append(f'{DISPLAY_PRICE_SQL} < ?')
            params.append(max_price)
        where_sql = ' AND '.join(clauses)

        total_row = conn.execute(
            f'SELECT COUNT(*) AS c FROM products WHERE {where_sql}', params
        ).fetchone()
        total_count = total_row['c'] if total_row else 0
        pager = make_pager(page, total_count, per_page)

        # Items the shopper can't actually order — out of stock OR "browse only"
        # (sellable_state not orderable) — still appear in search (so a searched
        # item is findable) but are de-prioritized below everything orderable,
        # within either ordering mode. "Orderable" mirrors can_request_order in
        # build_product_dict: sellable_state in (orderable, review_required) AND
        # not out_of_stock.
        unorderable_rank_sql = (
            "CASE WHEN COALESCE(sellable_state, 'browse_only') IN ('orderable', 'review_required') "
            "AND COALESCE(stock_status, 'in_stock') <> 'out_of_stock' THEN 0 ELSE 1 END"
        )

        def _unorderable(r):
            orderable = (
                (r['sellable_state'] or 'browse_only') in ('orderable', 'review_required')
                and (r['stock_status'] or 'in_stock') != 'out_of_stock'
            )
            return 0 if orderable else 1

        # Relevance = FTS order (default). Any explicit sort uses the SQL clause.
        if resolved_sort_key == 'relevance':
            rows = conn.execute(
                f'SELECT * FROM products WHERE {where_sql}', params
            ).fetchall()
            rows = sorted(
                rows,
                key=lambda r: (_unorderable(r), rank.get(r['merkey'], 1_000_000)),
            )
            offset = (pager['page'] - 1) * per_page
            rows = rows[offset:offset + per_page]
        else:
            offset = (pager['page'] - 1) * per_page
            rows = conn.execute(
                f'SELECT * FROM products WHERE {where_sql} ORDER BY {unorderable_rank_sql}, {sort_clause} LIMIT ? OFFSET ?',
                [*params, per_page, offset],
            ).fetchall()

        return {
            'products': [build_product_dict(row) for row in rows],
            'pager': pager,
            'sort_key': resolved_sort_key,
            'price_band': resolved_price_band,
            'mode': outcome.mode,
            'did_you_mean': outcome.did_you_mean,
            'suggestions': outcome.suggestions,
        }
    finally:
        conn.close()


def fetch_related_products(product: dict, limit: int = 10) -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM products
            WHERE active = 1
              AND merkey != ?
              AND department_slug = ?
            ORDER BY CASE WHEN category_slug = ? THEN 0 ELSE 1 END,
                     CASE WHEN COALESCE(priority, '') = 'TOP' THEN 0 ELSE 1 END,
                     COALESCE(txn_count_24m, 0) DESC,
                     name COLLATE NOCASE
            LIMIT ?
            """,
            (product['merkey'], product['department_slug'], product['category_slug'], limit),
        ).fetchall()
        return normalize_related_products([build_product_dict(row) for row in rows], RELATED_PRODUCTS_ROW_SIZE, RELATED_PRODUCTS_MAX_ROWS)
    finally:
        conn.close()


def fetch_product(merkey: str) -> dict | None:
    conn = get_conn()
    try:
        row = conn.execute('SELECT * FROM products WHERE merkey = ? AND active = 1', (merkey,)).fetchone()
        return build_product_dict(row) if row else None
    finally:
        conn.close()


def fetch_products_by_merkeys(merkeys: list[str]) -> list[dict]:
    if not merkeys:
        return []
    placeholders = ','.join('?' for _ in merkeys)
    conn = get_conn()
    try:
        rows = conn.execute(
            f"SELECT * FROM products WHERE active = 1 AND merkey IN ({placeholders})",
            merkeys,
        ).fetchall()
        products_by_merkey = {row['merkey']: build_product_dict(row) for row in rows}
        return [products_by_merkey[merkey] for merkey in merkeys if merkey in products_by_merkey]
    finally:
        conn.close()


def build_cart_items() -> list[dict]:
    cart = get_cart()
    if not cart:
        return []
    merkeys = list({line['merkey'] for line in cart.values()})
    products_by_merkey = {p['merkey']: p for p in fetch_products_by_merkeys(merkeys)}
    items: list[dict] = []
    for line_key, line in cart.items():
        product = products_by_merkey.get(line['merkey'])
        if not product:
            continue
        live_option = next(
            (opt for opt in (product.get('selling_options') or []) if opt.get('key') == line['option_key']),
            None,
        )
        unit_price = line.get('price')
        if unit_price is None:
            unit_price = (live_option.get('price') if live_option else None) or product.get('card_price') or 0.0
        unit_price = float(unit_price)
        photo_url = line.get('photo_url') or (live_option and live_option.get('photo_url')) or product.get('photo_url')
        label = line.get('label') or (live_option and live_option.get('label')) or 'Piece'
        barcode = line.get('barcode') or (live_option and live_option.get('barcode')) or product.get('barcode')
        items.append({
            'line_key': line_key,
            'product': product,
            'requested_qty': line['qty'],
            'option_key': line['option_key'],
            'option_label': label,
            'option_barcode': barcode,
            'option_photo_url': photo_url,
            'unit_price': unit_price,
            'line_price': unit_price * line['qty'],
        })
    return items


def create_order_request(
    customer_name: str,
    contact_number: str,
    contact_email: str,
    fulfillment_method: str,
    preferred_schedule: str,
    location_details: str,
    fulfillment_notes: str,
    cart_items: list[dict],
    customer_id: int | None = None,
    points_redeemed: int = 0,
) -> tuple[str, int]:
    """Insert a new order_request + items, returning (request_code, order_row_id).
    Phase C: when points_redeemed > 0, stores the debit amount on the row so
    cancellation can roll it back and the status template can display the
    discounted total."""
    request_code = f"OR-{datetime.now():%Y%m%d-%H%M%S-%f}"
    conn = get_conn()
    try:
        with conn:
            cur = conn.execute(
                """
                INSERT INTO order_requests (
                    request_code, customer_name, contact_number, contact_email, fulfillment_method,
                    preferred_schedule, location_details, fulfillment_notes, item_count, customer_id,
                    points_redeemed
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_code,
                    customer_name,
                    contact_number,
                    contact_email or None,
                    fulfillment_method or None,
                    preferred_schedule or None,
                    location_details or None,
                    fulfillment_notes or None,
                    sum(item['requested_qty'] for item in cart_items),
                    customer_id,
                    max(0, int(points_redeemed or 0)),
                ),
            )
            order_request_id = cur.lastrowid
            conn.executemany(
                """
                INSERT INTO order_request_items (
                    order_request_id, merkey, product_name, brand, requested_qty, original_requested_qty,
                    order_unit_label, pricing_basis, quoted_price, original_quoted_price, sellable_state,
                    fulfillment_type, fulfillment_note, removed,
                    selling_option_key, selling_option_label, selling_option_barcode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        order_request_id,
                        item['product']['merkey'],
                        item['product']['name'],
                        item['product']['brand'] or None,
                        item['requested_qty'],
                        item['requested_qty'],
                        item['product']['fulfillment']['order_unit_label'] or None,
                        item['product']['pricing_basis'] or None,
                        item['unit_price'],
                        item['unit_price'],
                        item['product']['sellable_state'],
                        item['product']['fulfillment']['type'],
                        item['product']['fulfillment']['note'],
                        0,
                        item.get('option_key') or 'retail',
                        item.get('option_label') or None,
                        item.get('option_barcode') or None,
                    )
                    for item in cart_items
                ],
            )
        return request_code, order_request_id
    finally:
        conn.close()


def notify_ops_webhook(*, request_code: str, customer_name: str, item_count: int,
                       estimated_total: float, action: str = 'new', note: str = '',
                       royal_card: str = '', pickup_slot_label: str = '',
                       points_redeemed: int = 0) -> None:
    """Fire-and-forget notification to Slack/Discord/etc. via STOREFRONT_NOTIFICATIONS_WEBHOOK.
    No-op when webhook isn't configured. Errors are swallowed so order flow never breaks.
    `note` carries the customer's fulfillment notes (substitutions, item changes) so staff
    can read them straight from the chat without opening the order."""
    if not NOTIFICATIONS_WEBHOOK_URL:
        return
    try:
        import urllib.request
        import json as _json
        verb = 'updated' if action == 'updated' else 'placed'
        admin_url = f"{NOTIFICATIONS_PUBLIC_BASE}/orders/admin/{request_code}" if NOTIFICATIONS_PUBLIC_BASE else f"/orders/admin/{request_code}"
        note_clean = ' '.join((note or '').split())
        if len(note_clean) > 600:
            note_clean = note_clean[:597] + '...'
        # SECURITY: the note is customer-controlled and the storefront is publicly
        # reachable (Tailscale Funnel). Defang every mention/link token so a note can
        # never inject a ping — '@' breaks @everyone/@here/@name, '<' breaks the raw
        # ID forms <@id>/<@&id>/<#id> (Discord) and <!here>/<@id> (Slack). The
        # zero-width space is invisible in the rendered message.
        note_clean = note_clean.replace('@', '@​').replace('<', '<​')
        # customer_name is also customer-controlled — defang it the same way so it can't
        # carry a ping when the operator mention forces parse:['everyone'] (e.g. @here).
        name_clean = (customer_name or '(unknown)').replace('@', '@​').replace('<', '<​')
        lines = [
            f":shopping_cart: Order request *{verb}* — `{request_code}`",
            f"Customer: {name_clean}",
            f"Items: {item_count}  ·  Est. total: P{estimated_total:,.2f}",
        ]
        # Royal Card linkage — printed before the note so staff sees it at a glance.
        if royal_card and royal_card.isdigit() and len(royal_card) == 13:
            lines.append(f":crown: Royal Card: `{royal_card}` — scan at till to award points")
        # Phase C — surface the online points redemption so staff knows the
        # discount has already been booked in bonus_ledger. The till total
        # they collect should match `estimated_total` minus this amount.
        if points_redeemed and int(points_redeemed) > 0:
            pr = int(points_redeemed)
            lines.append(
                f":star2: Royal Card discount: P{pr} ({pr} pts) — already booked in ledger"
            )
        # Milestone 5: surface the picked pickup-slot label so staff can plan capacity
        # straight from the chat. Defanged the same way as the customer note in case
        # we ever surface an operator-edited slot note here.
        if pickup_slot_label:
            slot_clean = ' '.join(pickup_slot_label.split()).replace('@', '@​').replace('<', '<​')
            if slot_clean:
                lines.append(f":clock3: Pickup window: {slot_clean}")
        if note_clean:
            lines.append(f":memo: Customer note: {note_clean}")
        lines.append(f"Review: {admin_url}")
        text = "\n".join(lines)
        # Discord rejects unknown keys (403); Slack accepts. Pick the right key
        # based on the URL host so a single env var works for either provider.
        if 'discord.com' in NOTIFICATIONS_WEBHOOK_URL or 'discordapp.com' in NOTIFICATIONS_WEBHOOK_URL:
            import re as _re
            content = f"{NOTIFICATIONS_MENTION} {text}" if NOTIFICATIONS_MENTION else text
            payload_obj = {'content': content}
            # SECURITY: scope pings to ONLY the operator-configured mention. Never use a
            # broad `parse` that would let any stray @here/<@id> in the message body (e.g.
            # a customer note) trigger a ping. Combined with the note defang above, this
            # makes customer text incapable of pinging anyone.
            allowed = {'parse': []}
            if NOTIFICATIONS_MENTION:
                if '@everyone' in NOTIFICATIONS_MENTION or '@here' in NOTIFICATIONS_MENTION:
                    allowed['parse'] = ['everyone']
                role_ids = _re.findall(r'<@&(\d+)>', NOTIFICATIONS_MENTION)
                user_ids = _re.findall(r'<@!?(\d+)>', NOTIFICATIONS_MENTION)
                if role_ids:
                    allowed['roles'] = role_ids
                if user_ids:
                    allowed['users'] = user_ids
            payload_obj['allowed_mentions'] = allowed
            payload = _json.dumps(payload_obj).encode('utf-8')
        else:
            payload = _json.dumps({'text': text}).encode('utf-8')
        req = urllib.request.Request(
            NOTIFICATIONS_WEBHOOK_URL,
            data=payload,
            headers={
                'Content-Type': 'application/json',
                # Discord rejects requests without a recognizable User-Agent.
                'User-Agent': 'AnsonStorefront/1.0 (+https://ansonsupermart.com)',
            },
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=4) as resp:
            resp.read()
    except Exception as exc:
        app.logger.warning(f"Webhook notify failed: {exc}")


STAFF_EDITABLE_STATUSES = {'NEW', 'REVIEWING', 'PICKING', 'AWAITING_CUSTOMER_CONFIRMATION', 'CONFIRMED', 'FOR_RELEASE', 'READY_FOR_PICKUP', 'OUT_FOR_DELIVERY'}


def replace_order_request_items(
    request_code: str,
    customer_name: str,
    contact_number: str,
    contact_email: str,
    fulfillment_method: str,
    preferred_schedule: str,
    location_details: str,
    fulfillment_notes: str,
    cart_items: list[dict],
    staff_override: bool = False,
) -> bool:
    """Overwrite items + fulfillment fields on an existing order_request.
    Customer-driven edits only allowed while status='NEW'. Staff (admin) can
    edit any pre-COMPLETED/CANCELLED status when staff_override=True.
    Returns True on success."""
    conn = get_conn()
    try:
        order = conn.execute(
            "SELECT id, status, points_redeemed FROM order_requests WHERE request_code = ?",
            (request_code,),
        ).fetchone()
        if not order:
            return False
        status = (order['status'] or '').upper()
        if staff_override:
            if status not in STAFF_EDITABLE_STATUSES:
                return False
        else:
            if status != 'NEW':
                return False
        # Phase C — if the order has a points_redeemed value, make sure the
        # new subtotal can still cover it. We auto-cap on edit so the order
        # never becomes "negative" — the customer keeps as much of the
        # redemption as the new subtotal allows. Settlement only debits the
        # capped amount (the negative ledger row stays as-is; this is a
        # storefront-only display correction, not a balance adjustment).
        new_subtotal = sum(
            (item.get('requested_qty') or 0) * (item.get('unit_price') or 0.0)
            for item in cart_items
        )
        capped_redemption = max(0, int(order['points_redeemed'] or 0))
        if capped_redemption > 0 and new_subtotal < capped_redemption:
            capped_redemption = int(new_subtotal)
        with conn:
            conn.execute("DELETE FROM order_request_items WHERE order_request_id = ?", (order['id'],))
            conn.execute(
                """
                UPDATE order_requests
                SET customer_name = ?, contact_number = ?, contact_email = ?,
                    fulfillment_method = ?, preferred_schedule = ?, location_details = ?,
                    fulfillment_notes = ?, item_count = ?, points_redeemed = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    customer_name,
                    contact_number,
                    contact_email or None,
                    fulfillment_method or None,
                    preferred_schedule or None,
                    location_details or None,
                    fulfillment_notes or None,
                    sum(item['requested_qty'] for item in cart_items),
                    capped_redemption,
                    order['id'],
                ),
            )
            conn.executemany(
                """
                INSERT INTO order_request_items (
                    order_request_id, merkey, product_name, brand, requested_qty, original_requested_qty,
                    order_unit_label, pricing_basis, quoted_price, original_quoted_price, sellable_state,
                    fulfillment_type, fulfillment_note, removed,
                    selling_option_key, selling_option_label, selling_option_barcode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        order['id'],
                        item['product']['merkey'],
                        item['product']['name'],
                        item['product']['brand'] or None,
                        item['requested_qty'],
                        item['requested_qty'],
                        item['product']['fulfillment']['order_unit_label'] or None,
                        item['product']['pricing_basis'] or None,
                        item['unit_price'],
                        item['unit_price'],
                        item['product']['sellable_state'],
                        item['product']['fulfillment']['type'],
                        item['product']['fulfillment']['note'],
                        0,
                        item.get('option_key') or 'retail',
                        item.get('option_label') or None,
                        item.get('option_barcode') or None,
                    )
                    for item in cart_items
                ],
            )
        return True
    finally:
        conn.close()


def calculate_order_estimated_total(items: list[dict]) -> float:
    return sum((item.get('requested_qty') or 0) * (item.get('quoted_price') or 0.0) for item in items)


def build_customer_product_label(product: dict, fallback_name: str = '') -> str:
    name = (product.get('name') or fallback_name or '').strip()
    brand = (product.get('brand') or '').strip()
    size = (product.get('display_size') or product.get('size') or '').strip()
    parts: list[str] = []
    if brand and not name.lower().startswith(brand.lower()):
        parts.append(brand)
    if name:
        parts.append(name)
    if size and size.lower() not in ' '.join(parts).lower():
        parts.append(size)
    return ' '.join(parts).strip()


def order_item_customer_label(item: dict) -> str:
    option_label = (item.get('selling_option_label') or '').strip()
    if option_label and option_label.lower() != 'piece':
        product_name = (item.get('product_name') or '').strip()
        brand = (item.get('brand') or '').strip()
        parts: list[str] = []
        if brand and not product_name.lower().startswith(brand.lower()):
            parts.append(brand)
        if product_name:
            parts.append(product_name)
        if option_label.lower() not in ' '.join(parts).lower():
            parts.append(option_label)
        return ' '.join(parts).strip()
    return (
        (item.get('customer_product_label') or '').strip()
        or (item.get('product_name') or '').strip()
        or (item.get('merkey') or '').strip()
        or 'Item'
    )


def build_customer_update_message(order_request: dict, active_items: list[dict], removed_items: list[dict]) -> str:
    lines = [
        f"Hello {order_request['customer_name']},",
        f"We updated your order request {order_request['request_code']}.",
        "",
    ]

    if active_items:
        lines.append("Updated items:")
        for item in active_items:
            change_bits: list[str] = []
            if item.get('original_requested_qty') != item.get('requested_qty'):
                change_bits.append(f"qty {item.get('original_requested_qty')} -> {item.get('requested_qty')}")
            if (item.get('original_quoted_price') or 0.0) != (item.get('quoted_price') or 0.0):
                change_bits.append(
                    f"price P{(item.get('original_quoted_price') or 0.0):,.2f} -> P{(item.get('quoted_price') or 0.0):,.2f}"
                )
            item_label = order_item_customer_label(item)
            summary = f"- {item_label}: Qty {item['requested_qty']}, P{(item.get('quoted_price') or 0.0):,.2f}"
            if change_bits:
                summary += f" ({'; '.join(change_bits)})"
            lines.append(summary)

    if removed_items:
        lines.append("")
        lines.append("Unavailable or removed items:")
        for item in removed_items:
            lines.append(f"- {order_item_customer_label(item)}")

    if active_items:
        lines.append("")
        lines.append(f"Updated estimated total: P{calculate_order_estimated_total(active_items):,.2f}")

    lines.append("")
    lines.append("Please reply to confirm any questions or substitutions.")
    return "\n".join(lines)


def fetch_order_request(request_code: str) -> dict | None:
    conn = get_conn()
    try:
        order_row = conn.execute(
            """
            SELECT id, request_code, customer_id, customer_name, contact_number, contact_email, fulfillment_method,
                   preferred_schedule, location_details, fulfillment_notes, internal_note, status,
                   item_count, confirmed_at, confirmed_total, customer_confirmation_note,
                   payment_status, payment_method, payment_reference, payment_proof_path, payment_amount,
                   payment_submitted_at, payment_verified_at, payment_verified_by, payment_verification_note,
                   COALESCE(points_redeemed, 0) AS points_redeemed,
                   created_at, updated_at
            FROM order_requests
            WHERE request_code = ?
            """,
            (request_code,),
        ).fetchone()
        if not order_row:
            return None
        item_rows = conn.execute(
            """
            SELECT id, merkey, product_name, brand, requested_qty, original_requested_qty, order_unit_label,
                   pricing_basis, quoted_price, original_quoted_price, sellable_state, fulfillment_type,
                   fulfillment_note, removed, removal_reason, created_at,
                   COALESCE(NULLIF(selling_option_key, ''), 'retail') AS selling_option_key,
                   selling_option_label, selling_option_barcode,
                   COALESCE(NULLIF(pick_status, ''), 'PENDING') AS pick_status,
                   picked_at, picked_by
            FROM order_request_items
            WHERE order_request_id = ?
            ORDER BY id
            """,
            (order_row['id'],),
        ).fetchall()
        items = [dict(row) for row in item_rows]
        merkeys = list({item['merkey'] for item in items if item.get('merkey')})
        product_lookup = {p['merkey']: p for p in fetch_products_by_merkeys(merkeys)} if merkeys else {}
        for item in items:
            product = product_lookup.get(item['merkey']) or {}
            options = product.get('selling_options') or []
            item['customer_product_label'] = build_customer_product_label(product, item.get('product_name') or '')
            item['available_options'] = options
            # Aisle hint for the picker — derived from the product's category.
            item['category_name'] = product.get('category_name') or ''
            chosen = next((opt for opt in options if opt.get('key') == item.get('selling_option_key')), None)
            item['display_photo_url'] = (
                (chosen or {}).get('photo_url')
                or product.get('photo_url')
                or PLACEHOLDER_PHOTO
            )
        active_items = [item for item in items if not item.get('removed')]
        removed_items = [item for item in items if item.get('removed')]
        payment_rows = conn.execute(
            """
            SELECT id, payment_method, reference_number, amount, proof_path, status,
                   customer_note, staff_note, submitted_at, verified_at, verified_by
            FROM order_payments
            WHERE order_request_id = ?
            ORDER BY submitted_at DESC, id DESC
            """,
            (order_row['id'],),
        ).fetchall()
        payments = [
            {
                **dict(row),
                'payment_method_label': payment_method_label(row['payment_method']),
                'status_label': payment_status_label(row['status']),
            }
            for row in payment_rows
        ]
        return {
            'request': {
                **dict(order_row),
                'status_label': order_status_label(order_row['status']),
                'canonical_status': canonical_order_status(order_row['status']),
                'payment_status_label': payment_status_label(order_row['payment_status']),
                'payment_method_label': payment_method_label(order_row['payment_method']),
            },
            'items': active_items,
            'payments': payments,
            'removed_items': removed_items,
            'estimated_total': calculate_order_estimated_total(active_items),
            'customer_update_message': build_customer_update_message(
                {
                    **dict(order_row),
                    'status_label': order_status_label(order_row['status']),
                },
                active_items,
                removed_items,
            ),
        }
    finally:
        conn.close()


def fetch_order_requests(status: str = '', fulfillment_method: str = '', query: str = '') -> list[dict]:
    conn = get_conn()
    try:
        clauses: list[str] = []
        params: list[object] = []

        if status and status in ORDER_STATUS_OPTIONS:
            if status == 'FOR_RELEASE':
                clauses.append("UPPER(COALESCE(status, 'NEW')) IN ('FOR_RELEASE', 'READY_FOR_PICKUP', 'OUT_FOR_DELIVERY')")
            elif status == 'RELEASED':
                clauses.append("UPPER(COALESCE(status, 'NEW')) IN ('RELEASED', 'COMPLETED', 'FULFILLED')")
            elif status == 'AWAITING_CUSTOMER_CONFIRMATION':
                clauses.append("UPPER(COALESCE(status, 'NEW')) IN ('AWAITING_CUSTOMER_CONFIRMATION', 'CONFIRMED')")
            else:
                clauses.append("status = ?")
                params.append(status)

        if fulfillment_method in {'pickup', 'delivery'}:
            clauses.append("fulfillment_method = ?")
            params.append(fulfillment_method)

        if query:
            like = f"%{query}%"
            clauses.append("(request_code LIKE ? OR customer_name LIKE ? OR contact_number LIKE ?)")
            params.extend([like, like, like])

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ''
        rows = conn.execute(
            f"""
            SELECT request_code, customer_name, contact_number, fulfillment_method, status, payment_status, payment_method,
                   item_count, created_at, updated_at
            FROM order_requests
            {where_sql}
            ORDER BY created_at DESC
            """,
            params,
        ).fetchall()
        return [
            {
                **dict(row),
                'status_label': order_status_label(row['status']),
                'canonical_status': canonical_order_status(row['status']),
                'payment_status_label': payment_status_label(row['payment_status']),
                'payment_method_label': payment_method_label(row['payment_method']),
                'fulfillment_method_label': (row['fulfillment_method'] or '').replace('_', ' ').title() if row['fulfillment_method'] else '',
            }
            for row in rows
        ]
    finally:
        conn.close()


def update_order_request(request_code: str, status: str, internal_note: str) -> bool:
    if status not in ALL_ORDER_STATUS_OPTIONS:
        return False
    conn = get_conn()
    try:
        # Phase C — if admin is flipping the order to CANCELLED, capture the
        # row id + redemption so we can cancel the linked bonus_ledger entry
        # in the same flow.
        existing = conn.execute(
            "SELECT id, points_redeemed FROM order_requests WHERE request_code = ?",
            (request_code,),
        ).fetchone()
        with conn:
            cur = conn.execute(
                """
                UPDATE order_requests
                SET status = ?, internal_note = ?, updated_at = CURRENT_TIMESTAMP
                WHERE request_code = ?
                """,
                (status, internal_note or None, request_code),
            )
        try:
            if (status or '').upper() == 'CANCELLED' and existing and (existing['points_redeemed'] or 0) > 0:
                cancel_redemption_for_order(existing['id'], request_code)
        except Exception:
            pass
        return cur.rowcount > 0
    finally:
        conn.close()


def cancel_order_request_by_customer(request_code: str) -> bool:
    conn = get_conn()
    try:
        order_row = conn.execute(
            """
            SELECT id, status, internal_note, confirmed_at, points_redeemed
            FROM order_requests
            WHERE request_code = ?
            """,
            (request_code,),
        ).fetchone()
        if not order_row:
            return False
        order_dict = dict(order_row)
        if not customer_can_cancel(order_dict):
            return False

        existing_note = (order_dict.get('internal_note') or '').strip()
        audit_note = 'Cancelled by customer via storefront.'
        next_note = audit_note if not existing_note else f"{existing_note}\n{audit_note}"

        with conn:
            cur = conn.execute(
                """
                UPDATE order_requests
                SET status = 'CANCELLED',
                    internal_note = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE request_code = ?
                """,
                (next_note, request_code),
            )
        # Phase C — flip the linked negative bonus_ledger row to 'cancelled'
        # so settle_bonuses.py skips it and the customer's balance isn't
        # actually debited at EOD. No-op when points_redeemed == 0.
        try:
            if (order_dict.get('points_redeemed') or 0) > 0:
                cancel_redemption_for_order(order_dict['id'], request_code)
        except Exception:
            # Cancellation should never fail loudly on a Supabase hiccup.
            pass
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_order_request_by_customer(request_code: str) -> bool:
    conn = get_conn()
    try:
        order_row = conn.execute(
            """
            SELECT id, status, confirmed_at
            FROM order_requests
            WHERE request_code = ?
            """,
            (request_code,),
        ).fetchone()
        if not order_row:
            return False
        order_dict = dict(order_row)
        if not customer_can_delete(order_dict):
            return False
        with conn:
            conn.execute("DELETE FROM order_request_items WHERE order_request_id = ?", (order_row['id'],))
            cur = conn.execute("DELETE FROM order_requests WHERE id = ?", (order_row['id'],))
        return cur.rowcount > 0
    finally:
        conn.close()


def update_order_request_by_customer(request_code: str, form_data) -> tuple[bool, str]:
    conn = get_conn()
    try:
        order_row = conn.execute(
            """
            SELECT id, status, confirmed_at
            FROM order_requests
            WHERE request_code = ?
            """,
            (request_code,),
        ).fetchone()
        if not order_row:
            return False, 'Order request not found.'

        order_dict = dict(order_row)
        if not customer_can_edit(order_dict):
            return False, 'This request can no longer be edited online.'

        customer_name = (form_data.get('customer_name') or '').strip()
        contact_number = (form_data.get('contact_number') or '').strip()
        contact_email = (form_data.get('contact_email') or '').strip()
        fulfillment_method = (form_data.get('fulfillment_method') or '').strip().lower()
        preferred_schedule = (form_data.get('preferred_schedule') or '').strip()
        location_details = (form_data.get('location_details') or '').strip()
        fulfillment_notes = (form_data.get('fulfillment_notes') or '').strip()

        if fulfillment_method not in {'pickup', 'delivery'}:
            return False, 'Please choose pickup or delivery.'
        if not customer_name or not contact_number or not location_details:
            return False, 'Please provide your name, contact number, and pickup or delivery details.'

        item_rows = conn.execute(
            """
            SELECT id, requested_qty, removed
            FROM order_request_items
            WHERE order_request_id = ?
            ORDER BY id
            """,
            (order_row['id'],),
        ).fetchall()

        updates: list[tuple[int, float, int, str | None, int]] = []
        active_item_count = 0
        for row in item_rows:
            item_id = row['id']
            qty_raw = form_data.get(f'item_qty_{item_id}', str(row['requested_qty'] or 1))
            remove_flag = form_data.get(f'item_remove_{item_id}') == 'on'
            try:
                qty = max(0, int(qty_raw))
            except (TypeError, ValueError):
                qty = max(1, int(row['requested_qty'] or 1))
            removed = 1 if remove_flag or qty == 0 else 0
            if not removed:
                active_item_count += qty
            removal_reason = 'Removed by customer before review' if removed else None
            updates.append((qty or 0, removed, removal_reason, item_id))

        if active_item_count <= 0:
            return False, 'Your request still needs at least one item. You can cancel it instead if needed.'

        with conn:
            for qty, removed, removal_reason, item_id in updates:
                conn.execute(
                    """
                    UPDATE order_request_items
                    SET requested_qty = CASE WHEN ? > 0 THEN ? ELSE requested_qty END,
                        removed = ?,
                        removal_reason = ?
                    WHERE id = ?
                    """,
                    (qty, qty, removed, removal_reason, item_id),
                )

            conn.execute(
                """
                UPDATE order_requests
                SET customer_name = ?,
                    contact_number = ?,
                    contact_email = ?,
                    fulfillment_method = ?,
                    preferred_schedule = ?,
                    location_details = ?,
                    fulfillment_notes = ?,
                    item_count = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    customer_name,
                    contact_number,
                    contact_email or None,
                    fulfillment_method,
                    preferred_schedule or None,
                    location_details,
                    fulfillment_notes or None,
                    active_item_count,
                    order_row['id'],
                ),
            )
        return True, 'Order request updated.'
    finally:
        conn.close()


def update_order_request_items(request_code: str, status: str, internal_note: str, form_data) -> bool:
    if status not in ALL_ORDER_STATUS_OPTIONS:
        return False

    conn = get_conn()
    try:
        order_row = conn.execute(
            "SELECT id, payment_status, points_redeemed FROM order_requests WHERE request_code = ?",
            (request_code,),
        ).fetchone()
        if not order_row:
            return False

        item_rows = conn.execute(
            """
            SELECT id, merkey, COALESCE(NULLIF(selling_option_key, ''), 'retail') AS selling_option_key,
                   quoted_price, original_quoted_price
            FROM order_request_items WHERE order_request_id = ? ORDER BY id
            """,
            (order_row['id'],),
        ).fetchall()
        merkeys = list({r['merkey'] for r in item_rows if r['merkey']})
        product_lookup = {p['merkey']: p for p in fetch_products_by_merkeys(merkeys)} if merkeys else {}

        if canonical_order_status(status) in {'FOR_RELEASE', 'RELEASED'} and (order_row['payment_status'] or 'UNPAID').upper() != 'PAID':
            return False

        with conn:
            for row in item_rows:
                item_id = row['id']
                qty_raw = form_data.get(f'item_qty_{item_id}', '1')
                price_raw = form_data.get(f'item_price_{item_id}')
                remove_flag = form_data.get(f'item_remove_{item_id}') == 'on'
                requested_option = (form_data.get(f'item_option_{item_id}') or row['selling_option_key'] or 'retail').strip()

                try:
                    qty = max(1, int(qty_raw))
                except (TypeError, ValueError):
                    qty = 1

                # A BLANK price field must NEVER silently zero the line (that would
                # quote the item as free). Treat blank/unparseable as "leave unchanged":
                # keep the existing quote, falling back to the original quote if needed.
                # Only an explicit number (including 0) sets a new price.
                existing_price = row['quoted_price'] if row['quoted_price'] else row['original_quoted_price']
                fallback_price = max(0.0, float(existing_price or 0.0))
                if price_raw is None or str(price_raw).strip() == '':
                    quoted_price = fallback_price
                else:
                    try:
                        quoted_price = max(0.0, float(price_raw))
                    except (TypeError, ValueError):
                        quoted_price = fallback_price

                option_label = None
                option_barcode = None
                option_changed = requested_option != (row['selling_option_key'] or 'retail')
                product = product_lookup.get(row['merkey']) if row['merkey'] else None
                if product:
                    chosen = next(
                        (opt for opt in (product.get('selling_options') or []) if opt.get('key') == requested_option),
                        None,
                    )
                    if chosen is None:
                        requested_option = 'retail'
                        chosen = next(
                            (opt for opt in (product.get('selling_options') or []) if opt.get('key') == 'retail'),
                            None,
                        )
                    if chosen:
                        option_label = chosen.get('label')
                        option_barcode = chosen.get('barcode')
                        if option_changed and chosen.get('price') is not None:
                            quoted_price = float(chosen['price'])

                removed = 1 if remove_flag else 0
                conn.execute(
                    """
                    UPDATE order_request_items
                    SET requested_qty = ?,
                        quoted_price = ?,
                        selling_option_key = ?,
                        selling_option_label = ?,
                        selling_option_barcode = ?,
                        removed = ?,
                        removal_reason = CASE WHEN ? = 1 THEN COALESCE(removal_reason, 'Removed during staff review') ELSE NULL END
                    WHERE id = ?
                    """,
                    (qty, quoted_price, requested_option, option_label, option_barcode, removed, removed, item_id),
                )

            active_count_row = conn.execute(
                """
                SELECT COALESCE(SUM(CASE WHEN removed = 0 THEN requested_qty ELSE 0 END), 0) AS item_count
                FROM order_request_items
                WHERE order_request_id = ?
                """,
                (order_row['id'],),
            ).fetchone()
            active_count = active_count_row['item_count'] if active_count_row else 0
            conn.execute(
                """
                UPDATE order_requests
                SET status = ?, internal_note = ?, item_count = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (status, internal_note or None, active_count, order_row['id']),
            )
        # Phase C — if this admin update CANCELLED an order that had an
        # online redemption, flip the linked bonus_ledger row so the points
        # debit doesn't settle at EOD. Outside the `with conn:` so a Supabase
        # hiccup never rolls back the SQLite write.
        try:
            if (status or '').upper() == 'CANCELLED' and (order_row['points_redeemed'] or 0) > 0:
                cancel_redemption_for_order(order_row['id'], request_code)
        except Exception:
            pass
        return True
    finally:
        conn.close()


def confirm_order_request(request_code: str, customer_confirmation_note: str) -> bool:
    conn = get_conn()
    try:
        order_row = conn.execute("SELECT id FROM order_requests WHERE request_code = ?", (request_code,)).fetchone()
        if not order_row:
            return False

        total_row = conn.execute(
            """
            SELECT COALESCE(SUM(CASE WHEN removed = 0 THEN requested_qty * quoted_price ELSE 0 END), 0.0) AS confirmed_total
            FROM order_request_items
            WHERE order_request_id = ?
            """,
            (order_row['id'],),
        ).fetchone()
        confirmed_total = total_row['confirmed_total'] if total_row else 0.0

        with conn:
            cur = conn.execute(
                """
                UPDATE order_requests
                SET status = 'AWAITING_CUSTOMER_CONFIRMATION',
                    payment_status = CASE WHEN COALESCE(payment_status, 'UNPAID') = 'UNPAID' THEN 'PAYMENT_PENDING' ELSE payment_status END,
                    confirmed_at = COALESCE(confirmed_at, CURRENT_TIMESTAMP),
                    confirmed_total = ?,
                    customer_confirmation_note = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (confirmed_total, customer_confirmation_note or None, order_row['id']),
            )
        return cur.rowcount > 0
    finally:
        conn.close()


def submit_customer_payment(request_code: str, form_data, files) -> tuple[bool, str]:
    method = (form_data.get('payment_method') or '').strip().upper()
    reference = (form_data.get('payment_reference') or '').strip()
    customer_note = (form_data.get('payment_note') or '').strip()
    if not allowed_payment_method(method):
        return False, 'Please choose a supported payment method.'
    proof_filename = None
    try:
        proof_filename = save_payment_proof(files.get('payment_proof'))
    except ValueError as exc:
        return False, str(exc)
    if not reference and not proof_filename:
        return False, 'Please enter a payment reference number or upload proof of payment.'

    conn = get_conn()
    try:
        order_row = conn.execute(
            """
            SELECT id, status, payment_status, confirmed_total
            FROM order_requests
            WHERE request_code = ?
            """,
            (request_code,),
        ).fetchone()
        if not order_row:
            return False, 'Order request not found.'
        if not customer_can_submit_payment(dict(order_row)):
            return False, 'Payment cannot be submitted for this order right now.'
        amount = order_row['confirmed_total']
        with conn:
            conn.execute(
                """
                INSERT INTO order_payments (
                    order_request_id, payment_method, reference_number, amount, proof_path, status, customer_note
                ) VALUES (?, ?, ?, ?, ?, 'SUBMITTED', ?)
                """,
                (order_row['id'], method, reference or None, amount, proof_filename, customer_note or None),
            )
            conn.execute(
                """
                UPDATE order_requests
                SET payment_status = 'PAYMENT_SUBMITTED',
                    payment_method = ?,
                    payment_reference = ?,
                    payment_proof_path = COALESCE(?, payment_proof_path),
                    payment_amount = ?,
                    payment_submitted_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (method, reference or None, proof_filename, amount, order_row['id']),
            )
        return True, 'Payment details submitted. Staff will verify before release.'
    finally:
        conn.close()


def update_staff_payment(request_code: str, form_data, files, action: str, admin_username: str | None) -> tuple[bool, str]:
    method = (form_data.get('payment_method') or '').strip().upper()
    reference = (form_data.get('payment_reference') or '').strip()
    staff_note = (form_data.get('payment_verification_note') or '').strip()
    amount_raw = (form_data.get('payment_amount') or '').strip()
    amount = None
    if amount_raw:
        try:
            amount = max(0.0, float(amount_raw))
        except ValueError:
            return False, 'Verified payment amount must be numeric.'
    if method and not allowed_payment_method(method):
        return False, 'Unsupported payment method.'
    proof_filename = None
    try:
        proof_filename = save_payment_proof(files.get('payment_proof'))
    except ValueError as exc:
        return False, str(exc)

    conn = get_conn()
    try:
        order_row = conn.execute(
            "SELECT id, confirmed_total, payment_proof_path FROM order_requests WHERE request_code = ?",
            (request_code,),
        ).fetchone()
        if not order_row:
            return False, 'Order request not found.'
        existing_proof = order_row['payment_proof_path']
        final_proof = proof_filename or existing_proof
        final_amount = amount if amount is not None else order_row['confirmed_total']
        final_status = 'PAID' if action == 'payment_verify' else 'PAYMENT_REJECTED'
        payment_row_status = 'VERIFIED' if action == 'payment_verify' else 'REJECTED'
        next_order_status = "FOR_RELEASE" if action == 'payment_verify' else "AWAITING_CUSTOMER_CONFIRMATION"
        with conn:
            if method:
                conn.execute(
                    """
                    INSERT INTO order_payments (
                        order_request_id, payment_method, reference_number, amount, proof_path, status,
                        staff_note, verified_at, verified_by
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
                    """,
                    (order_row['id'], method, reference or None, final_amount, final_proof, payment_row_status, staff_note or None, admin_username or None),
                )
            conn.execute(
                """
                UPDATE order_requests
                SET status = ?,
                    payment_status = ?,
                    payment_method = COALESCE(NULLIF(?, ''), payment_method),
                    payment_reference = COALESCE(NULLIF(?, ''), payment_reference),
                    payment_proof_path = COALESCE(?, payment_proof_path),
                    payment_amount = COALESCE(?, payment_amount),
                    payment_verified_at = CASE WHEN ? = 'PAID' THEN CURRENT_TIMESTAMP ELSE payment_verified_at END,
                    payment_verified_by = CASE WHEN ? = 'PAID' THEN ? ELSE payment_verified_by END,
                    payment_verification_note = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    next_order_status,
                    final_status,
                    method,
                    reference,
                    proof_filename,
                    final_amount,
                    final_status,
                    final_status,
                    admin_username or None,
                    staff_note or None,
                    order_row['id'],
                ),
            )
        if action == 'payment_verify':
            return True, 'Payment verified. Order is now for release.'
        return True, 'Payment proof rejected. Customer can resubmit payment details.'
    finally:
        conn.close()


@app.context_processor
def inject_global_template_values() -> dict:
    departments_by_count = fetch_departments(order_by_count=True)
    # Phase C — resolve the signed-in customer's linked card once per request
    # so templates can render the chip / pending dot without each one calling
    # linked_royal_card(session.get(...)). Empty string for guests + unlinked.
    current_card_no = ''
    cid = session.get(CUSTOMER_SESSION_KEY)
    if cid:
        try:
            current_card_no = linked_royal_card(cid) or ''
        except Exception:
            current_card_no = ''
    return {
        'top_departments': build_curated_top_departments(departments_by_count),
        'top_spotlights': BRAND_SPOTLIGHTS,
        'last_publish_at': fetch_last_publish_at(),
        'build_query_string': build_query_string,
        'cart_count': get_cart_count(),
        'storefront_admin_authenticated': bool(session.get(ADMIN_SESSION_KEY)),
        'customer_logged_in': bool(session.get(CUSTOMER_SESSION_KEY)),
        'customer_display_name': session.get(CUSTOMER_NAME_SESSION_KEY, ''),
        'current_customer_card_no':   current_card_no,
        'payment_status_label': payment_status_label,
        'payment_method_label': payment_method_label,
        'linked_royal_card': linked_royal_card,
        'build_admin_contact_links': build_admin_contact_links,
        'build_store_contact_links': build_store_contact_links,
        # Phase C — surface balance / pending bonuses so templates can call
        # them directly without a per-route plumb-through. Both are no-ops
        # for guests (card_no = ''), and fail-open to None / [] otherwise.
        'royal_card_balance':         fetch_member_balance,
        'royal_card_pending_bonuses': fetch_member_pending_bonuses,
    }


@app.route('/orders/payment-proof/<path:filename>')
def payment_proof_file(filename: str):
    safe_name = secure_filename(filename)
    if safe_name != filename:
        abort(404)
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT customer_id FROM order_requests WHERE payment_proof_path = ?",
            (filename,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        abort(404)
    if not session.get(ADMIN_SESSION_KEY) and row['customer_id'] != session.get(CUSTOMER_SESSION_KEY):
        abort(404)
    return send_from_directory(PAYMENT_PROOF_UPLOAD_DIR, filename, as_attachment=False)


@app.route('/')
def home():
    breadcrumbs = [{'label': 'Home', 'url': None}]
    departments = fetch_departments()
    customer_id = session.get(CUSTOMER_SESSION_KEY)
    last_fulfilled_order = fetch_customer_last_fulfilled_order(customer_id) if customer_id else None
    first_name = customer_first_name_from(session.get(CUSTOMER_NAME_SESSION_KEY, ''))
    return render_template(
        'home.html',
        breadcrumbs=breadcrumbs,
        departments=departments,
        category_highlights=fetch_category_highlights(limit=10),
        featured_collection=fetch_rotating_featured_collection(limit=8),
        spotlight_sections=fetch_department_spotlights(limit_per_department=6),
        # Milestone 3 — Royal Card homepage redesign
        last_fulfilled_order=last_fulfilled_order,
        homepage_top_sellers=fetch_homepage_top_sellers(limit=10),
        friendly_departments=fetch_homepage_friendly_departments(),
        quick_baskets=quick_baskets_home_summary(),
        customer_first_name=first_name,
    )


def load_quick_baskets_meta() -> list[dict]:
    """Basket definitions from quick_baskets.csv (slug, emoji, name), in file
    order. This is the full list of baskets — add a row to add a basket, no
    code change. Missing/unreadable file -> empty."""
    import csv
    out: list[dict] = []
    try:
        with open(QUICK_BASKETS_CSV, newline='', encoding='utf-8-sig') as fh:
            for row in csv.DictReader(fh):
                slug = (row.get('slug') or '').strip()
                if not slug:
                    continue
                out.append({
                    'slug': slug,
                    'emoji': (row.get('emoji') or '🧺').strip() or '🧺',
                    'name': (row.get('name') or slug).strip() or slug,
                })
    except (FileNotFoundError, OSError):
        pass
    return out


def quick_basket_meta(slug: str) -> dict | None:
    return next((b for b in load_quick_baskets_meta() if b['slug'] == slug), None)


def load_quick_basket_lines() -> dict[str, list[dict]]:
    """Read the recipe → product mapping CSV: slug -> [ {label, merkey,
    search_query, qty} ]. Missing/unreadable file -> empty (baskets just
    show as unavailable)."""
    import csv
    baskets: dict[str, list[dict]] = {}
    try:
        with open(QUICK_BASKET_ITEMS_CSV, newline='', encoding='utf-8-sig') as fh:
            for row in csv.DictReader(fh):
                slug = (row.get('basket_slug') or '').strip()
                if not slug:
                    continue
                try:
                    qty = max(1, int(row.get('quantity') or 1))
                except (TypeError, ValueError):
                    qty = 1
                baskets.setdefault(slug, []).append({
                    'label': (row.get('item_label') or '').strip(),
                    'merkey': (row.get('merkey') or '').strip(),
                    'search_query': (row.get('search_query') or '').strip(),
                    'qty': qty,
                })
    except (FileNotFoundError, OSError):
        pass
    return baskets


def resolve_basket_line(line: dict) -> dict:
    """Resolve one recipe line to a currently-orderable product: prefer the
    pinned merkey, fall back to the search_query if it's missing/unavailable."""
    product = fetch_product(line['merkey']) if line['merkey'] else None
    if (not product or not is_order_request_allowed(product)) and line['search_query']:
        conn = get_conn()
        try:
            search_engine.ensure_search_index(conn)
            outcome = search_engine.run_search(conn, line['search_query'], synonyms=SEARCH_SYNONYMS)
        finally:
            conn.close()
        for mk in outcome.merkeys:
            cand = fetch_product(mk)
            if cand and is_order_request_allowed(cand):
                product = cand
                break
    available = bool(product and is_order_request_allowed(product))
    return {'label': line['label'], 'qty': line['qty'],
            'product': product if available else None, 'available': available}


def resolve_quick_basket(slug: str) -> dict | None:
    meta = quick_basket_meta(slug)
    if not meta:
        return None
    items = [resolve_basket_line(line) for line in load_quick_basket_lines().get(slug, [])]
    available = [it for it in items if it['available']]
    estimated_total = sum((it['product'].get('card_price') or 0) * it['qty'] for it in available)
    return {
        'meta': meta,
        'items': items,
        'available_count': len(available),
        'total_count': len(items),
        'estimated_total': estimated_total,
    }


def quick_baskets_home_summary() -> list[dict]:
    """Light per-tile summary for the homepage strip: real item count and a
    'from ₱X' estimate from the pinned products' prices (one query)."""
    lines_by_slug = load_quick_basket_lines()
    all_merkeys = [ln['merkey'] for lines in lines_by_slug.values() for ln in lines if ln['merkey']]
    prices: dict[str, float] = {}
    if all_merkeys:
        conn = get_conn()
        try:
            placeholders = ','.join('?' for _ in all_merkeys)
            for row in conn.execute(
                f"SELECT merkey, COALESCE(price_retail, 0) AS p FROM products WHERE merkey IN ({placeholders})",
                all_merkeys,
            ).fetchall():
                prices[row['merkey']] = float(row['p'] or 0)
        finally:
            conn.close()
    summary = []
    for bucket in load_quick_baskets_meta():
        lines = lines_by_slug.get(bucket['slug'], [])
        total = sum(prices.get(ln['merkey'], 0) * ln['qty'] for ln in lines)
        summary.append({
            'slug': bucket['slug'],
            'emoji': bucket['emoji'],
            'name': bucket['name'],
            'item_count': len(lines),
            'price_from': round(total),
        })
    return summary


@app.route('/quick-basket/<slug>')
def quick_basket_page(slug: str):
    basket = resolve_quick_basket(slug)
    if not basket:
        abort(404)
    breadcrumbs = [
        {'label': 'Home', 'url': url_for('home')},
        {'label': basket['meta']['name'], 'url': None},
    ]
    return render_template('quick_basket.html', breadcrumbs=breadcrumbs, basket=basket)


@app.post('/quick-basket/<slug>/add')
def quick_basket_add_all(slug: str):
    basket = resolve_quick_basket(slug)
    if not basket:
        abort(404)
    cart = get_cart()
    added = 0
    for item in basket['items']:
        if not item['available']:
            continue
        product = item['product']
        options = product.get('selling_options') or []
        chosen = next((o for o in options if o.get('is_default')), None) or (options[0] if options else None)
        if not chosen:
            continue
        line_key = cart_line_key(product['merkey'], chosen['key'])
        existing = cart.get(line_key)
        cart[line_key] = {
            'merkey': product['merkey'],
            'qty': (existing.get('qty', 0) if existing else 0) + item['qty'],
            'option_key': chosen['key'],
            'label': chosen.get('label') or 'Piece',
            'price': float(chosen.get('price') or 0.0),
            'barcode': chosen.get('barcode'),
            'photo_url': chosen.get('photo_url'),
        }
        added += 1
    save_cart(cart)
    if added:
        flash(f"Added {added} item{'s' if added != 1 else ''} from {basket['meta']['name']} to your basket.", 'success')
    else:
        flash('None of this basket\'s items are available right now.', 'info')
    return redirect(url_for('cart_page'))


@app.route('/department/<slug>')
def department_page(slug: str):
    page = max(request.args.get('page', 1, type=int), 1)
    active_category = (request.args.get('category') or '').strip()
    active_sort = request.args.get('sort', 'top')
    active_price_band = request.args.get('price_band', '')
    # Milestone 4: comma-separated brand multi-select + promo-only toggle.
    raw_brand_param = (request.args.get('brand') or '').strip()
    active_brands = [b.strip() for b in raw_brand_param.split(',') if b.strip()]
    promo_only = (request.args.get('promo') or '').strip().lower() in {'1', 'true', 'on', 'yes'}

    department = fetch_department_header(slug)
    if not department:
        abort(404)

    categories = fetch_categories_for_department(slug)
    valid_category_slugs = {category['slug'] for category in categories}
    if active_category and active_category not in valid_category_slugs:
        active_category = ''

    # Brand list driven by the current dept (+ optional sub-dept) so the
    # filter sheet only shows brands the shopper can actually pick from.
    available_brands = fetch_brands_for_view(slug, active_category or None)
    available_brand_names = {brand['name'] for brand in available_brands}
    active_brands = [b for b in active_brands if b in available_brand_names]

    products, pager, active_sort, active_price_band = fetch_products(
        department_slug=slug,
        category_slug=active_category or None,
        brand_list=active_brands or None,
        promo_only=promo_only,
        hide_out_of_stock=True,
        page=page,
        per_page=PRODUCT_GRID_PAGE_SIZE,
        sort_key=active_sort,
        price_band=active_price_band,
    )

    breadcrumbs = [
        {'label': 'Home', 'url': url_for('home')},
        {'label': department['name'], 'url': None},
    ]

    return render_template(
        'department.html',
        breadcrumbs=breadcrumbs,
        department=department,
        categories=categories,
        top_category_cards=sorted(categories, key=lambda item: (-item['product_count'], item['name']))[:6],
        active_category=active_category,
        active_sort=active_sort,
        active_price_band=active_price_band,
        active_brands=active_brands,
        available_brands=available_brands,
        promo_only=promo_only,
        products=products,
        pager=pager,
        sort_options=SORT_OPTIONS,
        price_bands=PRICE_BANDS,
    )


@app.route('/department/<department_slug>/category/<category_slug>')
def category_page(department_slug: str, category_slug: str):
    return redirect(url_for('department_page', slug=department_slug, category=category_slug))


@app.route('/browse/<slug>')
def browse_bucket(slug: str):
    """Friendly Browse-Categories tile landing — shows exactly the aisles a
    multi-category bucket covers (e.g. 'Canned & Processed' = all canned-*),
    titled with the friendly name instead of dumping the shopper into the whole
    broad department."""
    bucket = FRIENDLY_BUCKETS_BY_SLUG.get(slug)
    if not bucket:
        abort(404)

    page = max(request.args.get('page', 1, type=int), 1)
    active_sort = request.args.get('sort', 'top')
    active_price_band = request.args.get('price_band', '')

    products, pager, active_sort, active_price_band = fetch_products(
        department_slug=bucket['department_slug'],
        category_list=bucket.get('category_slugs') or None,
        hide_out_of_stock=True,
        page=page,
        per_page=PRODUCT_GRID_PAGE_SIZE,
        sort_key=active_sort,
        price_band=active_price_band,
    )

    breadcrumbs = [
        {'label': 'Home', 'url': url_for('home')},
        {'label': bucket['name'], 'url': None},
    ]

    return render_template(
        'aisle.html',
        breadcrumbs=breadcrumbs,
        bucket=bucket,
        products=products,
        pager=pager,
        active_sort=active_sort,
        active_price_band=active_price_band,
        sort_options=SORT_OPTIONS,
        price_bands=PRICE_BANDS,
    )


@app.route('/brand/<slug>')
def brand_spotlight(slug: str):
    spotlight = BRAND_SPOTLIGHTS_BY_SLUG.get(slug)
    if not spotlight:
        abort(404)

    page = max(request.args.get('page', 1, type=int), 1)
    active_sort = request.args.get('sort', 'top')
    active_price_band = request.args.get('price_band', '')

    products, pager, active_sort, active_price_band = fetch_products(
        brand_name=spotlight['brand'],
        page=page,
        per_page=PRODUCT_GRID_PAGE_SIZE,
        sort_key=active_sort,
        price_band=active_price_band,
    )

    breadcrumbs = [
        {'label': 'Home', 'url': url_for('home')},
        {'label': spotlight['name'], 'url': None},
    ]

    return render_template(
        'brand.html',
        breadcrumbs=breadcrumbs,
        spotlight=spotlight,
        products=products,
        pager=pager,
        active_sort=active_sort,
        active_price_band=active_price_band,
        sort_options=SORT_OPTIONS,
        price_bands=PRICE_BANDS,
    )


@app.route('/product/<merkey>')
def product_page(merkey: str):
    product = fetch_product(merkey)
    if not product:
        abort(404)
    # Browse-only (no-photo) items are admin-view-only — customers can't reach
    # the page even via a direct link. Staff see it.
    if product.get('sellable_state') == 'browse_only' and not viewer_is_staff():
        abort(404)

    breadcrumbs = [
        {'label': 'Home', 'url': url_for('home')},
        {'label': product['department_name'], 'url': url_for('department_page', slug=product['department_slug'])},
        {'label': product['name'], 'url': None},
    ]

    return render_template(
        'product.html',
        breadcrumbs=breadcrumbs,
        product=product,
        related=fetch_related_products(product, limit=10),
    )


@app.post('/cart/add/<merkey>')
def add_to_cart(merkey: str):
    product = fetch_product(merkey)
    if not product or not is_order_request_allowed(product):
        if wants_json_response():
            return jsonify({'ok': False, 'error': 'Product is not available for ordering.'}), 404
        abort(404)

    quantity = max(request.form.get('quantity', 1, type=int) or 1, 1)
    requested_option = (request.form.get('selected_option') or 'retail').strip() or 'retail'
    selling_options = product.get('selling_options') or []
    chosen = next((opt for opt in selling_options if opt.get('key') == requested_option), None)
    if chosen is None:
        chosen = next((opt for opt in selling_options if opt.get('is_default')), None)
    if chosen is None:
        chosen = {
            'key': 'retail',
            'label': 'Piece',
            'price': product.get('card_price') or product.get('price_retail') or 0.0,
            'barcode': product.get('barcode'),
            'photo_url': product.get('photo_url'),
        }

    line_key = cart_line_key(merkey, chosen['key'])
    cart = get_cart()
    existing = cart.get(line_key)
    new_qty = (existing.get('qty', 0) if existing else 0) + quantity
    cart[line_key] = {
        'merkey': merkey,
        'qty': new_qty,
        'option_key': chosen['key'],
        'label': chosen.get('label') or ('Piece' if chosen['key'] == 'retail' else 'Pack / Box'),
        'price': float(chosen.get('price') or 0.0),
        'barcode': chosen.get('barcode'),
        'photo_url': chosen.get('photo_url'),
    }
    save_cart(cart)
    cart_count = get_cart_count()
    if wants_json_response():
        return jsonify({
            'ok': True,
            'message': f"{product['name']} ({cart[line_key]['label']}) added to basket.",
            'cart_count': cart_count,
            'item_qty': new_qty,
            'merkey': merkey,
            'option_key': chosen['key'],
        })
    flash(f"{product['name']} ({cart[line_key]['label']}) added to order request cart.", 'success')
    return redirect(request.form.get('next') or get_editing_cart_url())


@app.post('/cart/update')
def update_cart():
    cart = get_cart()
    for line_key in list(cart.keys()):
        qty = request.form.get(f'qty_{line_key}', type=int)
        if qty is None:
            continue
        if qty <= 0:
            cart.pop(line_key, None)
        else:
            cart[line_key]['qty'] = qty
    save_cart(cart)
    # Milestone 5: the redesigned cart's qty stepper hits this endpoint
    # over AJAX. Return live totals so the page can update without a
    # full reload. HTML callers keep the old redirect+flash behavior.
    if wants_json_response():
        items = build_cart_items()
        subtotal = sum(item['line_price'] for item in items)
        customer_id = session.get(CUSTOMER_SESSION_KEY)
        earn_pts = _royal_card_earn_pts(subtotal) if linked_royal_card(customer_id) else 0
        return jsonify({
            'ok': True,
            'cart_count': get_cart_count(),
            'subtotal': subtotal,
            'subtotal_formatted': f"P{subtotal:,.2f}",
            'total_formatted': f"P{subtotal:,.2f}",
            'earn_pts': earn_pts,
            'line_totals': {
                item['line_key']: {
                    'qty': item['requested_qty'],
                    'line_price': item['line_price'],
                    'line_price_formatted': f"P{item['line_price']:,.2f}",
                }
                for item in items
            },
        })
    flash('Order request cart updated.', 'success')
    return redirect(request.form.get('next') or get_editing_cart_url())


@app.post('/cart/remove/<merkey>')
def remove_from_cart(merkey: str):
    option_key = (request.form.get('option') or request.args.get('option') or 'retail').strip() or 'retail'
    line_key = cart_line_key(merkey, option_key)
    cart = get_cart()
    cart.pop(line_key, None)
    save_cart(cart)
    if wants_json_response():
        items = build_cart_items()
        subtotal = sum(item['line_price'] for item in items)
        customer_id = session.get(CUSTOMER_SESSION_KEY)
        earn_pts = _royal_card_earn_pts(subtotal) if linked_royal_card(customer_id) else 0
        return jsonify({
            'ok': True,
            'cart_count': get_cart_count(),
            'subtotal': subtotal,
            'subtotal_formatted': f"P{subtotal:,.2f}",
            'total_formatted': f"P{subtotal:,.2f}",
            'earn_pts': earn_pts,
        })
    flash('Item removed from order request cart.', 'success')
    return redirect(request.form.get('next') or get_editing_cart_url())


def _royal_card_earn_pts(subtotal: float) -> int:
    """ANSON loyalty rule: 1 pt per P200 spent (floor). Surfaced on the
    cart/checkout pages so the customer knows what they'll bank — only
    rendered when they're logged in and Royal Card-linked."""
    try:
        s = float(subtotal or 0)
    except (TypeError, ValueError):
        return 0
    if s <= 0:
        return 0
    return int(s // 200)


@app.route('/cart', methods=['GET', 'POST'])
def cart_page():
    """Cart view (Milestone 5). GET renders the redesigned mobile-first
    cart. POST is legacy — any old form that still targets /cart gets
    handled by /checkout to keep the existing admin-edit-cart flow + any
    bookmarked POSTers working. The first-class submit path is /checkout."""
    if request.method == 'POST':
        # Legacy fallback: a form was posted directly to /cart. Delegate to
        # the new checkout handler so editing flows + any stale bookmarks
        # keep working without duplicating 80 lines of logic.
        return checkout_page()
    breadcrumbs = [
        {'label': 'Home', 'url': url_for('home')},
        {'label': 'Cart', 'url': None},
    ]
    cart_items = build_cart_items()
    estimated_total = sum(item['line_price'] for item in cart_items)
    customer_id = session.get(CUSTOMER_SESSION_KEY)
    royal_card = linked_royal_card(customer_id) if customer_id else ''
    earn_pts = _royal_card_earn_pts(estimated_total) if royal_card else 0
    return render_template(
        'order_cart.html',
        breadcrumbs=breadcrumbs,
        cart_items=cart_items,
        estimated_total=estimated_total,
        royal_card_linked=bool(royal_card),
        earn_pts=earn_pts,
    )


@app.route('/checkout', methods=['GET', 'POST'])
def checkout_page():
    """Focused checkout flow (Milestone 5). GET renders the fulfillment/slot/
    payment/note form. POST submits the order — picks up the legacy POST-/cart
    payload too, so the admin-edit-cart flow keeps working."""
    breadcrumbs = [
        {'label': 'Home', 'url': url_for('home')},
        {'label': 'Cart', 'url': url_for('cart_page')},
        {'label': 'Checkout', 'url': None},
    ]
    cart_items = build_cart_items()
    estimated_total = sum(item['line_price'] for item in cart_items)
    customer_id = session.get(CUSTOMER_SESSION_KEY)
    royal_card = linked_royal_card(customer_id) if customer_id else ''
    earn_pts = _royal_card_earn_pts(estimated_total) if royal_card else 0
    # Phase C — fetch the live balance once so the redemption UI can render
    # and the POST handler can re-fetch to detect concurrent redemptions.
    # Falls back to None silently when Phase C is disabled / Supabase errors.
    royal_card_balance = fetch_member_balance(royal_card) if royal_card else None

    def render_checkout(form_data: dict, *, selected_slot_id: int | None = None,
                        redeem_points_value: int = 0):
        # Per-order cap is the min of: customer's balance, order subtotal, and
        # the rollout cap. UI hides the section entirely when the cap is <= 0.
        max_redeemable = 0
        if royal_card and isinstance(royal_card_balance, int) and royal_card_balance > 0:
            max_redeemable = min(
                int(royal_card_balance),
                int(estimated_total),
                ROYALCARD_ONLINE_REDEMPTION_CAP,
            )
        return render_template(
            'checkout.html',
            breadcrumbs=breadcrumbs,
            cart_items=cart_items,
            estimated_total=estimated_total,
            form_data=form_data,
            royal_card_linked=bool(royal_card),
            royal_card_number=royal_card,
            royal_card_balance=royal_card_balance if royal_card else None,
            royal_card_max_redeemable=max_redeemable,
            royal_card_redeem_points=max(0, int(redeem_points_value or 0)),
            earn_pts=earn_pts,
            available_slots=fetch_available_slots(days_ahead=3),
            selected_slot_id=selected_slot_id,
        )

    if request.method == 'POST':
        if not cart_items:
            flash('Your cart is empty.', 'error')
            return redirect(url_for('cart_page'))

        customer_name = (request.form.get('customer_name') or '').strip()
        contact_number = (request.form.get('contact_number') or '').strip()
        contact_email = (request.form.get('contact_email') or '').strip()
        fulfillment_method = (request.form.get('fulfillment_method') or '').strip().lower()
        preferred_schedule = (request.form.get('preferred_schedule') or '').strip()
        location_details = (request.form.get('location_details') or '').strip()
        fulfillment_notes = (request.form.get('fulfillment_notes') or '').strip()
        # Phase C — points the customer wants to redeem. Only honored when
        # they're linked AND Phase C is enabled; ignored entirely for guests.
        redeem_points_raw = (request.form.get('redeem_points') or '').strip()
        try:
            redeem_points = max(0, int(float(redeem_points_raw))) if redeem_points_raw else 0
        except (TypeError, ValueError):
            redeem_points = 0
        # Milestone 5: pickup slot picker — optional for now (delivery + legacy
        # bookmarked forms don't have it). Parsed to int or None.
        pickup_slot_id_raw = (request.form.get('pickup_slot_id') or '').strip()
        try:
            pickup_slot_id = int(pickup_slot_id_raw) if pickup_slot_id_raw else None
        except ValueError:
            pickup_slot_id = None
        # When user picks a slot, write a human-readable label into the
        # legacy preferred_schedule column so existing admin views stay
        # readable without any template changes.
        picked_slot = fetch_pickup_slot(pickup_slot_id) if pickup_slot_id else None
        if picked_slot:
            preferred_schedule = picked_slot['label_legacy']

        form_data = empty_request_form_data()
        form_data.update({
            'customer_name': customer_name,
            'contact_number': contact_number,
            'contact_email': contact_email,
            'fulfillment_method': fulfillment_method,
            'preferred_schedule': preferred_schedule,
            'location_details': location_details,
            'fulfillment_notes': fulfillment_notes,
        })

        if fulfillment_method not in {'pickup', 'delivery'}:
            flash('Please choose pickup or delivery for this request.', 'error')
            return render_checkout(form_data, selected_slot_id=pickup_slot_id,
                                   redeem_points_value=redeem_points)

        if not customer_name or not contact_number or not location_details:
            flash('Please provide your name, contact number, and pickup or delivery details.', 'error')
            return render_checkout(form_data, selected_slot_id=pickup_slot_id,
                                   redeem_points_value=redeem_points)

        # Pickup requires a slot once a slot system is in place. Delivery is
        # exempt (no slot picker shown for delivery anyway).
        if fulfillment_method == 'pickup' and pickup_slot_id is None and fetch_available_slots(days_ahead=3):
            flash('Please pick a pickup window.', 'error')
            return render_checkout(form_data, selected_slot_id=None,
                                   redeem_points_value=redeem_points)

        # Phase C — re-validate the requested redemption at submit time. The
        # balance the customer saw on the GET may have moved (e.g. they redeemed
        # in-store between page load and submit). Re-fetch fresh, cap to the
        # smaller of order total / rollout cap, and refuse if it now exceeds
        # available — never silently over-redeem. Guests and non-Phase-C
        # storefronts skip this block entirely.
        effective_redemption = 0
        if redeem_points > 0:
            if not royal_card:
                # Guest who somehow injected the field — silently drop, don't
                # leak that the feature exists.
                redeem_points = 0
            else:
                fresh_balance = fetch_member_balance(royal_card)
                if fresh_balance is None:
                    flash(
                        "We couldn't verify your Royal Card balance right now — "
                        "please try again or place the order without using points.",
                        'error',
                    )
                    return render_checkout(form_data, selected_slot_id=pickup_slot_id,
                                           redeem_points_value=redeem_points)
                allowed = min(
                    int(fresh_balance),
                    int(estimated_total),
                    ROYALCARD_ONLINE_REDEMPTION_CAP,
                )
                if redeem_points > allowed:
                    flash(
                        f"You requested {redeem_points} pts but only {allowed} can be "
                        f"used on this order. Please adjust and try again.",
                        'error',
                    )
                    return render_checkout(form_data, selected_slot_id=pickup_slot_id,
                                           redeem_points_value=allowed)
                effective_redemption = redeem_points

        editing_code = get_editing_request_code()
        if editing_code:
            is_staff = bool(session.get(ADMIN_SESSION_KEY))
            ok = replace_order_request_items(
                editing_code,
                customer_name, contact_number, contact_email,
                fulfillment_method, preferred_schedule, location_details, fulfillment_notes,
                cart_items,
                staff_override=is_staff,
            )
            if not ok:
                clear_editing_state()
                flash(
                    f'We couldn\'t save your changes to {editing_code} because our staff already '
                    f'started preparing it. Your updated items are still in your cart — please '
                    f'message us so we can apply them, or submit them as a new request.',
                    'error',
                )
                return redirect(url_for('cart_page'))
            save_cart({})
            clear_editing_state()
            # Try to attach the picked slot to the edited order. Silent
            # failure (slot full) is acceptable on an edit — the order
            # itself was saved.
            if pickup_slot_id is not None:
                editing_row_id = order_request_id_for_code(editing_code)
                if editing_row_id:
                    book_pickup_slot(editing_row_id, pickup_slot_id)
            notify_ops_webhook(
                request_code=editing_code,
                customer_name=customer_name,
                item_count=sum(item['requested_qty'] for item in cart_items),
                estimated_total=estimated_total,
                action='updated',
                note=fulfillment_notes,
                royal_card=linked_royal_card(session.get(CUSTOMER_SESSION_KEY)),
                pickup_slot_label=(picked_slot or {}).get('label_short', ''),
            )
            flash(f'Order request {editing_code} updated with your changes.', 'success')
            return redirect(url_for(
                'admin_order_detail_page' if is_staff else 'order_request_page',
                request_code=editing_code,
            ))

        request_code, new_row_id = create_order_request(
            customer_name,
            contact_number,
            contact_email,
            fulfillment_method,
            preferred_schedule,
            location_details,
            fulfillment_notes,
            cart_items,
            customer_id=session.get(CUSTOMER_SESSION_KEY),
            points_redeemed=effective_redemption,
        )

        # Phase C — book the negative bonus_ledger row for the redemption.
        # We've already validated the amount against the live balance above;
        # if the Supabase POST itself fails, roll the order back to zero
        # points so we never charge the customer for a discount they didn't
        # get. The order itself still stands — they can pay full price.
        booked_redemption = 0
        if effective_redemption > 0 and royal_card:
            inserted = apply_redemption_to_ledger(
                royal_card, effective_redemption, new_row_id, request_code,
            )
            if inserted:
                booked_redemption = effective_redemption
            else:
                # Roll the column back so the discounted total isn't shown to
                # the customer or to ops. Flash a friendly error.
                conn_rb = get_conn()
                try:
                    with conn_rb:
                        conn_rb.execute(
                            "UPDATE order_requests SET points_redeemed = 0 WHERE id = ?",
                            (new_row_id,),
                        )
                finally:
                    conn_rb.close()
                flash(
                    "We couldn't apply your Royal Card discount — your order is "
                    "still placed at full price. Please try redeeming in-store.",
                    'error',
                )

        # Try to atomically attach the picked slot. If the slot just filled
        # between the GET and the POST, flash a friendly error and re-render
        # with a fresh slot list — but the order is *still* placed, so the
        # customer doesn't lose their basket. Staff will pick a slot manually.
        slot_booked = False
        if pickup_slot_id is not None and new_row_id:
            slot_booked = book_pickup_slot(new_row_id, pickup_slot_id)
            if not slot_booked:
                flash(
                    'That pickup window just filled — your order is saved, but '
                    'please message us to pick another slot.',
                    'error',
                )

        save_cart({})
        notify_ops_webhook(
            request_code=request_code,
            customer_name=customer_name,
            item_count=sum(item['requested_qty'] for item in cart_items),
            estimated_total=estimated_total,
            action='new',
            note=fulfillment_notes,
            royal_card=linked_royal_card(session.get(CUSTOMER_SESSION_KEY)),
            pickup_slot_label=((picked_slot or {}).get('label_short', '') if slot_booked else ''),
            points_redeemed=booked_redemption,
        )
        flash(f'Order request {request_code} submitted for internal review.', 'success')
        return redirect(url_for('order_request_page', request_code=request_code))

    return render_checkout(prefilled_request_form_data())


@app.route('/orders/lookup', methods=['GET', 'POST'])
def order_lookup_page():
    breadcrumbs = [
        {'label': 'Home', 'url': url_for('home')},
        {'label': 'Track Order Request', 'url': None},
    ]

    if request.method == 'POST':
        request_code = (request.form.get('request_code') or '').strip().upper()
        if not request_code:
            flash('Please enter your order request code.', 'error')
        elif fetch_order_request(request_code):
            return redirect(url_for('order_request_page', request_code=request_code))
        else:
            flash('We could not find that order request code. Please check it and try again.', 'error')

    return render_template('order_lookup.html', breadcrumbs=breadcrumbs)


# ---------------------------------------------------------------------------
# Customer account routes
# ---------------------------------------------------------------------------

@app.route('/account/register', methods=['GET', 'POST'])
def customer_register_page():
    next_target = (request.args.get('next') or request.form.get('next') or '').strip()
    if session.get(CUSTOMER_SESSION_KEY):
        return redirect(resolve_customer_redirect_target(next_target))

    breadcrumbs = [
        {'label': 'Home', 'url': url_for('home')},
        {'label': 'Create Account', 'url': None},
    ]

    if request.method == 'POST':
        display_name = (request.form.get('display_name') or '').strip()
        phone_raw = (request.form.get('phone') or '').strip()
        email_raw = (request.form.get('email') or '').strip()
        password = request.form.get('password') or ''
        password_confirm = request.form.get('password_confirm') or ''

        phone = ''.join(ch for ch in phone_raw if ch.isdigit()) or None
        email = email_raw.lower() if email_raw else None

        errors = []
        if not display_name:
            errors.append('Please enter your name.')
        if not phone and not email:
            errors.append('Please provide a phone number or email address.')
        if len(password) < 6:
            errors.append('Password must be at least 6 characters.')
        if password != password_confirm:
            errors.append('Passwords do not match.')

        if errors:
            for e in errors:
                flash(e, 'error')
            return render_template(
                'customer_register.html', breadcrumbs=breadcrumbs,
                form_display_name=display_name, form_phone=phone_raw, form_email=email_raw,
                next_target=next_target,
            )

        conn = get_conn()
        try:
            with conn:
                cur = conn.execute(
                    "INSERT INTO customers (phone, email, password_hash, display_name) VALUES (?, ?, ?, ?)",
                    (phone, email, generate_password_hash(password), display_name),
                )
                customer_id = cur.lastrowid
                claimed = claim_guest_orders(conn, customer_id, phone, email)
            session[CUSTOMER_SESSION_KEY] = customer_id
            session[CUSTOMER_NAME_SESSION_KEY] = display_name
            session.modified = True
            msg = 'Account created successfully.'
            if claimed:
                msg += f' {claimed} previous order(s) linked to your account.'
            flash(msg, 'success')
            # Phase B: if this phone is already a Royal Card member, stamp the
            # card on the freshly-created customer row so online orders count
            # toward their points balance from the very first purchase.
            linked = _try_link_royal_card_to_customer(customer_id)
            if linked:
                flash("Your Royal Card was linked to your online account — you'll earn points on online orders.", 'success')
            return redirect(resolve_customer_redirect_target(next_target))
        except sqlite3.IntegrityError:
            flash('An account with that phone number or email already exists.', 'error')
            return render_template(
                'customer_register.html', breadcrumbs=breadcrumbs,
                form_display_name=display_name, form_phone=phone_raw, form_email=email_raw,
                next_target=next_target,
            )
        finally:
            conn.close()

    return render_template('customer_register.html', breadcrumbs=breadcrumbs,
                           form_display_name='', form_phone='', form_email='',
                           next_target=next_target)


@app.route('/auth/royalcard')
def royalcard_link_page():
    """Phase A Royal Card deeplink:
        /auth/royalcard?card=<13>&phone=<+63...>&name=<...>&exp=<unix>&t=<hmac>
    Token is signed by the Supabase Edge Function `shop-link` with the shared
    ROYALCARD_LINK_SECRET. Find-or-create the customer by phone, stamp card_no,
    log them into the storefront session, redirect home with a flash."""
    if not ROYALCARD_LINK_SECRET:
        abort(404)
    card  = (request.args.get('card')  or '').strip()
    phone = (request.args.get('phone') or '').strip()
    name  = (request.args.get('name')  or '').strip()
    try:
        exp = int(request.args.get('exp') or '0')
    except ValueError:
        abort(400, 'bad token')
    got = (request.args.get('t') or '').strip()
    if not (card and phone and exp and got):
        abort(400, 'missing fields')
    # signature over canonical form (no URL-encoding in the signed string)
    canonical = f"{card}|{phone}|{name}|{exp}".encode()
    expected  = hmac.new(ROYALCARD_LINK_SECRET.encode(), canonical, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, got):
        abort(400, 'bad signature')
    if exp < int(datetime.utcnow().timestamp()):
        abort(400, 'token expired')
    # 13-digit EAN-13 starting with the ANSON prefix
    if not (card.isdigit() and len(card) == 13 and card.startswith('1010570')):
        abort(400, 'bad card')

    phone_intl = normalize_ph_phone(phone)
    if not phone_intl:
        abort(400, 'bad phone')
    # FoxBase stores names as 'LAST, FIRST MIDDLE' — flip for display.
    display = name
    if ',' in name:
        last, first = name.split(',', 1)
        display = f"{first.strip()} {last.strip()}".strip()
    display = display or 'Royal Card Member'

    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id, card_no, display_name FROM customers WHERE phone = ?",
            (phone_intl,),
        ).fetchone()
        if row:
            cust_id = row['id']
            updates, params = [], []
            if not row['card_no']:
                updates.append("card_no = ?"); params.append(card)
            if not (row['display_name'] or '').strip():
                updates.append("display_name = ?"); params.append(display)
            if updates:
                updates.append("updated_at = CURRENT_TIMESTAMP")
                params.append(cust_id)
                conn.execute(f"UPDATE customers SET {', '.join(updates)} WHERE id = ?", params)
        else:
            # Auto-provision a passwordless customer (login is via the deeplink).
            # The placeholder hash format makes the customer login route reject any
            # password attempt while remaining a well-formed non-null value.
            placeholder = '!royalcard-link-only!' + secrets.token_urlsafe(16)
            cur = conn.execute(
                "INSERT INTO customers (phone, display_name, password_hash, card_no) "
                "VALUES (?, ?, ?, ?)",
                (phone_intl, display, placeholder, card),
            )
            cust_id = cur.lastrowid
            claim_guest_orders(conn, cust_id, phone_intl, None)
        conn.commit()
    finally:
        conn.close()

    session[CUSTOMER_SESSION_KEY] = cust_id
    session[CUSTOMER_NAME_SESSION_KEY] = display
    session.modified = True
    flash(f"Welcome, {display.split()[0]}! Your Royal Card is linked.", 'success')
    return redirect(url_for('home'))


@app.route('/account/login', methods=['GET', 'POST'])
def customer_login_page():
    if session.get(CUSTOMER_SESSION_KEY):
        return redirect(url_for('customer_profile_page'))

    breadcrumbs = [
        {'label': 'Home', 'url': url_for('home')},
        {'label': 'Sign In', 'url': None},
    ]
    next_target = (request.args.get('next') or request.form.get('next') or '').strip()

    if request.method == 'POST':
        identifier = (request.form.get('identifier') or '').strip()
        password = request.form.get('password') or ''
        customer = authenticate_customer(identifier, password)
        if customer:
            session[CUSTOMER_SESSION_KEY] = customer['id']
            session[CUSTOMER_NAME_SESSION_KEY] = customer['display_name']
            session.modified = True
            flash(f"Welcome back, {customer['display_name']}.", 'success')
            # Phase B: opportunistically link the Royal Card if this phone is on
            # file in Supabase but the storefront row never got card_no stamped
            # (e.g. this customer registered directly here, not via PWA deeplink).
            if not (customer.get('card_no') or '').strip():
                linked = _try_link_royal_card_to_customer(customer['id'])
                if linked:
                    flash("Your Royal Card was linked to your online account — you'll earn points on online orders.", 'success')
            return redirect(resolve_customer_redirect_target(next_target))
        flash('Login failed. Please check your phone/email and password.', 'error')

    return render_template('customer_login.html', breadcrumbs=breadcrumbs, next_target=next_target)


@app.route('/account/forgot-password', methods=['GET', 'POST'])
def customer_forgot_password_page():
    if session.get(CUSTOMER_SESSION_KEY):
        return redirect(url_for('customer_profile_page'))

    breadcrumbs = [
        {'label': 'Home', 'url': url_for('home')},
        {'label': 'Sign In', 'url': url_for('customer_login_page')},
        {'label': 'Reset Password', 'url': None},
    ]
    form_phone = ''
    form_email = ''
    temp_password = None

    if request.method == 'POST':
        form_phone = (request.form.get('phone') or '').strip()
        form_email = (request.form.get('email') or '').strip()
        phone = ''.join(ch for ch in form_phone if ch.isdigit())
        email = form_email.lower() if form_email else ''
        if not phone and not email:
            flash('Please enter the phone number or email you registered with.', 'error')
        else:
            conn = get_conn()
            try:
                if phone and email:
                    row = conn.execute(
                        "SELECT id, display_name FROM customers WHERE phone = ? AND email = ?",
                        (phone, email),
                    ).fetchone()
                elif phone:
                    row = conn.execute(
                        "SELECT id, display_name FROM customers WHERE phone = ?", (phone,)
                    ).fetchone()
                else:
                    row = conn.execute(
                        "SELECT id, display_name FROM customers WHERE email = ?", (email,)
                    ).fetchone()
                if not row:
                    flash('No account matches the details you entered.', 'error')
                else:
                    import secrets
                    temp_password = secrets.token_urlsafe(8)[:10]
                    with conn:
                        conn.execute(
                            "UPDATE customers SET password_hash = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                            (generate_password_hash(temp_password), row['id']),
                        )
                    flash('Temporary password generated. Use it to sign in, then change it from your profile.', 'success')
            finally:
                conn.close()

    return render_template(
        'customer_forgot_password.html',
        breadcrumbs=breadcrumbs,
        form_phone=form_phone,
        form_email=form_email,
        temp_password=temp_password,
    )


@app.route('/account/logout', methods=['POST'])
def customer_logout():
    session.pop(CUSTOMER_SESSION_KEY, None)
    session.pop(CUSTOMER_NAME_SESSION_KEY, None)
    flash('You have been signed out.', 'success')
    return redirect(url_for('home'))


@app.route('/account/profile', methods=['GET', 'POST'])
@customer_login_required
def customer_profile_page():
    customer = get_current_customer()
    if not customer:
        session.pop(CUSTOMER_SESSION_KEY, None)
        return redirect(url_for('customer_login_page'))

    breadcrumbs = [
        {'label': 'Home', 'url': url_for('home')},
        {'label': 'My Account', 'url': None},
    ]

    if request.method == 'POST':
        action = (request.form.get('action') or '').strip()

        if action == 'update_profile':
            display_name = (request.form.get('display_name') or '').strip()
            phone_raw = (request.form.get('phone') or '').strip()
            email_raw = (request.form.get('email') or '').strip()
            default_fulfillment = (request.form.get('default_fulfillment_method') or '').strip()
            default_location = (request.form.get('default_location_details') or '').strip()
            default_schedule = (request.form.get('default_preferred_schedule') or '').strip()

            phone = ''.join(ch for ch in phone_raw if ch.isdigit()) or None
            email = email_raw.lower() if email_raw else None

            if not display_name:
                flash('Name is required.', 'error')
                return redirect(url_for('customer_profile_page'))
            if not phone and not email:
                flash('Please keep at least a phone number or email on your account.', 'error')
                return redirect(url_for('customer_profile_page'))

            conn = get_conn()
            try:
                with conn:
                    conn.execute(
                        """UPDATE customers SET display_name=?, phone=?, email=?,
                           default_fulfillment_method=?, default_location_details=?,
                           default_preferred_schedule=?, updated_at=CURRENT_TIMESTAMP
                           WHERE id=?""",
                        (display_name, phone, email, default_fulfillment or None,
                         default_location or None, default_schedule or None, customer['id']),
                    )
                session[CUSTOMER_NAME_SESSION_KEY] = display_name
                session.modified = True
                flash('Profile updated.', 'success')
            except sqlite3.IntegrityError:
                flash('That phone number or email is already used by another account.', 'error')
            finally:
                conn.close()
            return redirect(url_for('customer_profile_page'))

        if action == 'change_password':
            current_pw = request.form.get('current_password') or ''
            new_pw = request.form.get('new_password') or ''
            confirm_pw = request.form.get('confirm_password') or ''
            if not check_password_hash(customer['password_hash'], current_pw):
                flash('Current password is incorrect.', 'error')
            elif len(new_pw) < 6:
                flash('New password must be at least 6 characters.', 'error')
            elif new_pw != confirm_pw:
                flash('New passwords do not match.', 'error')
            else:
                conn = get_conn()
                try:
                    with conn:
                        conn.execute(
                            "UPDATE customers SET password_hash=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                            (generate_password_hash(new_pw), customer['id']),
                        )
                    flash('Password changed.', 'success')
                finally:
                    conn.close()
            return redirect(url_for('customer_profile_page'))

    customer = get_current_customer()
    return render_template('customer_profile.html', breadcrumbs=breadcrumbs, customer=customer)


@app.route('/account/orders')
@customer_login_required
def customer_orders_page():
    customer_id = session.get(CUSTOMER_SESSION_KEY)
    breadcrumbs = [
        {'label': 'Home', 'url': url_for('home')},
        {'label': 'My Orders', 'url': None},
    ]
    conn = get_conn()
    try:
        orders = conn.execute(
            """SELECT id, request_code, customer_name, status, payment_status, payment_method, item_count,
                      confirmed_total, created_at, fulfillment_method
               FROM order_requests WHERE customer_id = ? ORDER BY created_at DESC""",
            (customer_id,),
        ).fetchall()
        orders = [dict(row) | {'status_label': order_status_label(row['status']), 'payment_status_label': payment_status_label(row['payment_status']), 'payment_method_label': payment_method_label(row['payment_method'])} for row in orders]
    finally:
        conn.close()
    return render_template('customer_orders.html', breadcrumbs=breadcrumbs, orders=orders)


@app.route('/account/orders/<request_code>')
@customer_login_required
def customer_order_detail_page(request_code: str):
    customer_id = session.get(CUSTOMER_SESSION_KEY)
    # Ownership check before delegating to the enriched bundle.
    conn = get_conn()
    try:
        owner = conn.execute(
            "SELECT 1 FROM order_requests WHERE request_code = ? AND customer_id = ?",
            (request_code, customer_id),
        ).fetchone()
    finally:
        conn.close()
    if not owner:
        abort(404)

    order_bundle = fetch_order_request(request_code)
    if not order_bundle:
        abort(404)
    order = order_bundle['request']
    active_items = order_bundle['items']
    removed_items = order_bundle['removed_items']
    estimated_total = order_bundle['estimated_total']
    breadcrumbs = [
        {'label': 'Home', 'url': url_for('home')},
        {'label': 'My Orders', 'url': url_for('customer_orders_page')},
        {'label': request_code, 'url': None},
    ]
    return render_template(
        'order_request.html',
        breadcrumbs=breadcrumbs,
        order_request=order,
        order_items=active_items,
        removed_items=removed_items,
        estimated_total=estimated_total,
        customer_status_message=customer_status_message(order, estimated_total),
        customer_can_edit=customer_can_edit(order),
        customer_can_cancel=customer_can_cancel(order),
        customer_can_delete=customer_can_delete(order),
        customer_can_submit_payment=customer_can_submit_payment(order),
        payment_method_options=PAYMENT_METHOD_OPTIONS,
        payment_history=order_bundle.get('payments', []),
        reorder_url=url_for('customer_reorder', request_code=request_code),
    )


@app.route('/account/reorder/<request_code>', methods=['POST'])
@customer_login_required
def customer_reorder(request_code: str):
    customer_id = session.get(CUSTOMER_SESSION_KEY)
    conn = get_conn()
    try:
        order = conn.execute(
            "SELECT id FROM order_requests WHERE request_code = ? AND customer_id = ?",
            (request_code, customer_id),
        ).fetchone()
        if not order:
            abort(404)
        items = conn.execute(
            """
            SELECT merkey, requested_qty, quoted_price,
                   COALESCE(NULLIF(selling_option_key, ''), 'retail') AS selling_option_key,
                   selling_option_label, selling_option_barcode
            FROM order_request_items
            WHERE order_request_id = ? AND removed = 0
            """,
            (order['id'],),
        ).fetchall()
    finally:
        conn.close()

    cart = get_cart()
    for item in items:
        merkey = item['merkey']
        option_key = item['selling_option_key'] or 'retail'
        line_key = cart_line_key(merkey, option_key)
        existing = cart.get(line_key)
        new_qty = (existing.get('qty', 0) if existing else 0) + (item['requested_qty'] or 0)
        cart[line_key] = {
            'merkey': merkey,
            'qty': new_qty,
            'option_key': option_key,
            'label': (item['selling_option_label'] or ('Piece' if option_key == 'retail' else 'Pack / Box')),
            'price': float(item['quoted_price']) if item['quoted_price'] is not None else (existing.get('price') if existing else None),
            'barcode': item['selling_option_barcode'] or (existing.get('barcode') if existing else None),
            'photo_url': existing.get('photo_url') if existing else None,
        }
    save_cart(cart)
    flash(f'Items from {request_code} added to your cart.', 'success')
    return redirect(url_for('cart_page'))


@app.route('/orders/admin/login', methods=['GET', 'POST'])
def admin_login_page():
    if session.get(ADMIN_SESSION_KEY):
        return redirect(resolve_admin_redirect_target(request.args.get('next')))

    next_target = resolve_admin_redirect_target(request.args.get('next') or request.form.get('next'))

    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        if is_valid_admin_login(username, password):
            session[ADMIN_SESSION_KEY] = True
            session[ADMIN_USERNAME_SESSION_KEY] = username
            session.modified = True
            flash('Admin access granted.', 'success')
            return redirect(next_target)
        flash('Login failed. Please check the admin username and password.', 'error')

    breadcrumbs = [
        {'label': 'Home', 'url': url_for('home')},
        {'label': 'Order Admin Login', 'url': None},
    ]
    return render_template(
        'admin_login.html',
        breadcrumbs=breadcrumbs,
        next_target=next_target,
    )


@app.route('/orders/admin/logout', methods=['POST'])
def admin_logout():
    session.pop(ADMIN_SESSION_KEY, None)
    session.pop(ADMIN_USERNAME_SESSION_KEY, None)
    flash('Admin session signed out.', 'success')
    return redirect(url_for('admin_login_page'))


@app.route('/orders/admin/poll')
@storefront_admin_required
def admin_orders_poll():
    """Lightweight JSON poll for the admin page. Returns count of orders in NEW
    status and the most recent order id, so the page can detect new arrivals."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS new_count, COALESCE(MAX(id), 0) AS latest_id "
            "FROM order_requests WHERE UPPER(COALESCE(status,'NEW')) = 'NEW'"
        ).fetchone()
        return jsonify({
            'new_count': int(row['new_count'] or 0),
            'latest_id': int(row['latest_id'] or 0),
        })
    finally:
        conn.close()


@app.route('/orders/admin', methods=['GET'])
@storefront_admin_required
def admin_orders_page():
    active_status = (request.args.get('status') or '').strip().upper()
    active_fulfillment_method = (request.args.get('fulfillment_method') or '').strip().lower()
    search_query = (request.args.get('q') or '').strip()
    breadcrumbs = [
        {'label': 'Home', 'url': url_for('home')},
        {'label': 'Order Requests Admin', 'url': None},
    ]
    return render_template(
        'admin_orders.html',
        breadcrumbs=breadcrumbs,
        orders=fetch_order_requests(active_status, active_fulfillment_method, search_query),
        active_status=active_status,
        active_fulfillment_method=active_fulfillment_method,
        search_query=search_query,
        status_options=ORDER_STATUS_OPTIONS,
    )


@app.route('/orders/admin/<request_code>', methods=['GET', 'POST'])
@storefront_admin_required
def admin_order_detail_page(request_code: str):
    if request.method == 'POST':
        action = (request.form.get('admin_action') or 'save').strip().lower()
        status = (request.form.get('status') or '').strip().upper()
        internal_note = (request.form.get('internal_note') or '').strip()
        customer_confirmation_note = (request.form.get('customer_confirmation_note') or '').strip()
        if action in {'payment_verify', 'payment_reject'}:
            ok, message = update_staff_payment(
                request_code, request.form, request.files, action, session.get(ADMIN_USERNAME_SESSION_KEY)
            )
            flash(message, 'success' if ok else 'error')
            return redirect(url_for('admin_order_detail_page', request_code=request_code))
        if action == 'advance':
            existing_bundle = fetch_order_request(request_code)
            if existing_bundle:
                next_stage = next_fulfillment_stage(existing_bundle['request'])
                if next_stage:
                    status = next_stage[0]
        if not update_order_request_items(request_code, status, internal_note, request.form):
            flash('Unable to update order request.', 'error')
        else:
            if action == 'confirm':
                if confirm_order_request(request_code, customer_confirmation_note):
                    flash(f'Order request {request_code} confirmed with the reviewed total.', 'success')
                else:
                    flash('Order changes were saved, but confirmation did not complete.', 'error')
            elif action == 'advance':
                flash(f'Order request {request_code} moved to {order_status_label(status)}.', 'success')
            else:
                flash(f'Order request {request_code} updated. Customer update message refreshed below.', 'success')
        return redirect(url_for('admin_order_detail_page', request_code=request_code))

    order_bundle = fetch_order_request(request_code)
    if not order_bundle:
        abort(404)

    breadcrumbs = [
        {'label': 'Home', 'url': url_for('home')},
        {'label': 'Order Requests Admin', 'url': url_for('admin_orders_page')},
        {'label': request_code, 'url': None},
    ]
    return render_template(
        'admin_order_detail.html',
        breadcrumbs=breadcrumbs,
        order_request=order_bundle['request'],
        order_items=order_bundle['items'],
        removed_items=order_bundle['removed_items'],
        payment_history=order_bundle.get('payments', []),
        payment_method_options=PAYMENT_METHOD_OPTIONS,
        estimated_total=order_bundle['estimated_total'],
        customer_update_message=order_bundle['customer_update_message'],
        customer_status_message=customer_status_message(order_bundle['request'], order_bundle['estimated_total']),
        contact_links=build_admin_contact_links(order_bundle['request'], order_bundle['customer_update_message']),
        next_stage=next_fulfillment_stage(order_bundle['request']),
        status_options=ORDER_STATUS_OPTIONS,
    )


# ============================================================================
# PICKER MVP — operator phone view for walking the aisles.
# Mockup: RoyalCard/mockups/shop_picker.html
# ============================================================================

PICKER_BLOCKING_STATUSES = {'CANCELLED', 'RELEASED', 'COMPLETED'}


def _picker_load_order_or_redirect(request_code: str):
    """Fetch an order for the picker view. Returns (order_bundle, None) on
    success, or (None, redirect_response) when the order is missing/finished."""
    order_bundle = fetch_order_request(request_code)
    if not order_bundle:
        abort(404)
    canonical = (order_bundle['request'].get('canonical_status') or '').upper()
    raw_status = (order_bundle['request'].get('status') or '').upper()
    if canonical in PICKER_BLOCKING_STATUSES or raw_status in PICKER_BLOCKING_STATUSES:
        flash(f'Order {request_code} is {order_bundle["request"].get("status_label") or raw_status}; picker is closed.', 'error')
        return None, redirect(url_for('admin_order_detail_page', request_code=request_code))
    return order_bundle, None


@app.route('/orders/admin/<request_code>/picker', methods=['GET'])
@storefront_admin_required
def admin_picker_page(request_code: str):
    order_bundle, redirect_resp = _picker_load_order_or_redirect(request_code)
    if redirect_resp is not None:
        return redirect_resp
    order = order_bundle['request']
    items = order_bundle['items']
    total_count = len(items)
    resolved_count = sum(1 for it in items if (it.get('pick_status') or 'PENDING').upper() != 'PENDING')
    breadcrumbs = [
        {'label': 'Order Admin', 'url': url_for('admin_orders_page')},
        {'label': request_code, 'url': url_for('admin_order_detail_page', request_code=request_code)},
        {'label': 'Picker', 'url': None},
    ]
    return render_template(
        'picker.html',
        breadcrumbs=breadcrumbs,
        order_request=order,
        order_items=items,
        total_count=total_count,
        resolved_count=resolved_count,
        royal_card=linked_royal_card(order.get('customer_id')),
    )


@app.route('/orders/admin/<request_code>/picker/start', methods=['POST'])
@storefront_admin_required
def admin_picker_start(request_code: str):
    order_bundle, redirect_resp = _picker_load_order_or_redirect(request_code)
    if redirect_resp is not None:
        return redirect_resp
    canonical = (order_bundle['request'].get('canonical_status') or '').upper()
    if canonical not in {'NEW', 'REVIEWING'}:
        flash(f'Cannot start picking from status {canonical or "?"}.', 'error')
        return redirect(url_for('admin_order_detail_page', request_code=request_code))
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE order_requests SET status = 'PICKING', updated_at = CURRENT_TIMESTAMP WHERE request_code = ?",
            (request_code,),
        )
        conn.commit()
    finally:
        conn.close()
    flash(f'Started picking {request_code}.', 'success')
    return redirect(url_for('admin_picker_page', request_code=request_code))


@app.route('/orders/admin/<request_code>/picker/item/<int:item_id>', methods=['POST'])
@storefront_admin_required
def admin_picker_item_action(request_code: str, item_id: int):
    action = (request.form.get('pick_action') or '').strip().lower()
    if action not in {'pick', 'oos', 'skip', 'reset'}:
        return jsonify({'ok': False, 'error': 'bad_action'}), 400

    conn = get_conn()
    try:
        order_row = conn.execute(
            "SELECT id, status FROM order_requests WHERE request_code = ?",
            (request_code,),
        ).fetchone()
        if not order_row:
            return jsonify({'ok': False, 'error': 'order_not_found'}), 404
        raw_status = (order_row['status'] or '').upper()
        if raw_status in PICKER_BLOCKING_STATUSES:
            return jsonify({'ok': False, 'error': 'order_closed', 'status': raw_status}), 409
        item_row = conn.execute(
            "SELECT id FROM order_request_items WHERE id = ? AND order_request_id = ?",
            (item_id, order_row['id']),
        ).fetchone()
        if not item_row:
            return jsonify({'ok': False, 'error': 'item_not_found'}), 404

        admin_username = session.get(ADMIN_USERNAME_SESSION_KEY) or 'admin'
        if action == 'pick':
            conn.execute(
                """
                UPDATE order_request_items
                SET pick_status = 'PICKED',
                    picked_at = CURRENT_TIMESTAMP,
                    picked_by = ?,
                    removed = 0,
                    removal_reason = NULL
                WHERE id = ?
                """,
                (admin_username, item_id),
            )
            new_status = 'PICKED'
        elif action == 'oos':
            conn.execute(
                """
                UPDATE order_request_items
                SET pick_status = 'OUT_OF_STOCK',
                    picked_at = CURRENT_TIMESTAMP,
                    picked_by = ?,
                    removed = 1,
                    removal_reason = 'out_of_stock'
                WHERE id = ?
                """,
                (admin_username, item_id),
            )
            new_status = 'OUT_OF_STOCK'
        elif action == 'skip':
            conn.execute(
                """
                UPDATE order_request_items
                SET pick_status = 'SKIPPED',
                    picked_at = CURRENT_TIMESTAMP,
                    picked_by = ?,
                    removed = 1,
                    removal_reason = 'skipped_by_picker'
                WHERE id = ?
                """,
                (admin_username, item_id),
            )
            new_status = 'SKIPPED'
        else:  # reset
            conn.execute(
                """
                UPDATE order_request_items
                SET pick_status = 'PENDING',
                    picked_at = NULL,
                    picked_by = NULL,
                    removed = 0,
                    removal_reason = NULL
                WHERE id = ?
                """,
                (item_id,),
            )
            new_status = 'PENDING'
        conn.execute(
            "UPDATE order_requests SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (order_row['id'],),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({'ok': True, 'pick_status': new_status})


@app.route('/orders/admin/<request_code>/picker/finish', methods=['POST'])
@storefront_admin_required
def admin_picker_finish(request_code: str):
    order_bundle, redirect_resp = _picker_load_order_or_redirect(request_code)
    if redirect_resp is not None:
        return redirect_resp
    order = order_bundle['request']
    canonical = (order.get('canonical_status') or '').upper()
    if canonical != 'PICKING':
        flash(f'Cannot finish picking — order is {canonical or "?"}.', 'error')
        return redirect(url_for('admin_order_detail_page', request_code=request_code))

    # Read every (active + removed) item, then check pick_status across them.
    conn = get_conn()
    try:
        all_rows = conn.execute(
            """
            SELECT id, COALESCE(NULLIF(pick_status, ''), 'PENDING') AS pick_status,
                   requested_qty, quoted_price, removed
            FROM order_request_items
            WHERE order_request_id = ?
            """,
            (order['id'],),
        ).fetchall()
        all_rows = [dict(r) for r in all_rows]
        pending = [r for r in all_rows if (r['pick_status'] or 'PENDING').upper() == 'PENDING']
        if pending:
            flash(f'{len(pending)} item(s) still unresolved. Pick, mark out of stock, or skip every row before finishing.', 'error')
            return redirect(url_for('admin_picker_page', request_code=request_code))

        had_removals = any(
            (r['pick_status'] or '').upper() in {'OUT_OF_STOCK', 'SKIPPED'} for r in all_rows
        )
        payment_status = (order.get('payment_status') or 'UNPAID').strip().upper()

        if had_removals:
            new_state = 'AWAITING_CUSTOMER_CONFIRMATION'
        elif payment_status == 'PAID':
            new_state = 'FOR_RELEASE'
        else:
            new_state = 'AWAITING_CUSTOMER_CONFIRMATION'

        conn.execute(
            "UPDATE order_requests SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (new_state, order['id']),
        )
        conn.commit()

        # Re-fetch active items for an accurate count after removals.
        active_rows = [r for r in all_rows if not r.get('removed')]
        item_count = sum(int(r['requested_qty'] or 0) for r in active_rows)
        estimated_total = sum(
            float(r['quoted_price'] or 0) * int(r['requested_qty'] or 0) for r in active_rows
        )
    finally:
        conn.close()

    notify_ops_webhook(
        request_code=request_code,
        customer_name=order.get('customer_name') or '',
        item_count=item_count,
        estimated_total=estimated_total,
        action='updated',
        note=order.get('fulfillment_notes') or '',
        royal_card=linked_royal_card(order.get('customer_id')),
    )
    flash(f'Picking complete for {request_code}. Order moved to {order_status_label(new_state)}.', 'success')
    return redirect(url_for('admin_order_detail_page', request_code=request_code))


@app.route('/orders/<request_code>')
def order_request_page(request_code: str):
    order_bundle = fetch_order_request(request_code)
    if not order_bundle:
        abort(404)

    breadcrumbs = [
        {'label': 'Home', 'url': url_for('home')},
        {'label': 'Order Request Status', 'url': None},
    ]
    return render_template(
        'order_request.html',
        breadcrumbs=breadcrumbs,
        order_request=order_bundle['request'],
        order_items=order_bundle['items'],
        removed_items=order_bundle['removed_items'],
        estimated_total=order_bundle['estimated_total'],
        customer_status_message=customer_status_message(order_bundle['request'], order_bundle['estimated_total']),
        customer_can_edit=customer_can_edit(order_bundle['request']),
        customer_can_cancel=customer_can_cancel(order_bundle['request']),
        customer_can_delete=customer_can_delete(order_bundle['request']),
        customer_can_submit_payment=customer_can_submit_payment(order_bundle['request']),
        payment_method_options=PAYMENT_METHOD_OPTIONS,
        payment_history=order_bundle.get('payments', []),
    )


@app.route('/orders/<request_code>/payment', methods=['POST'])
def submit_order_payment(request_code: str):
    order_bundle = fetch_order_request(request_code)
    if not order_bundle:
        abort(404)
    customer_id = session.get(CUSTOMER_SESSION_KEY)
    order_customer_id = order_bundle['request'].get('customer_id')
    if order_customer_id and order_customer_id != customer_id:
        abort(404)
    ok, message = submit_customer_payment(request_code, request.form, request.files)
    flash(message, 'success' if ok else 'error')
    return redirect(url_for('customer_order_detail_page' if customer_id else 'order_request_page', request_code=request_code))


@app.route('/orders/admin/<request_code>/edit-cart', methods=['GET', 'POST'])
@storefront_admin_required
def admin_edit_cart_page(request_code: str):
    if get_editing_request_code() != request_code:
        flash('No active edit cart found for this order. Click Add or substitute items first.', 'error')
        return redirect(url_for('admin_order_detail_page', request_code=request_code))
    if request.method == 'POST':
        return apply_editing_order_request_cart(request_code)

    order_bundle = fetch_order_request(request_code)
    if not order_bundle:
        abort(404)
    order = order_bundle['request']
    cart_items = build_cart_items()
    return render_template(
        'order_cart.html',
        breadcrumbs=[
            {'label': 'Order Admin', 'url': url_for('admin_orders_page')},
            {'label': request_code, 'url': url_for('admin_order_detail_page', request_code=request_code)},
            {'label': 'Edit basket', 'url': None},
        ],
        cart_items=cart_items,
        estimated_total=sum(item['line_price'] for item in cart_items),
        form_data={
            'customer_name': order.get('customer_name') or '',
            'contact_number': order.get('contact_number') or '',
            'contact_email': order.get('contact_email') or '',
            'fulfillment_method': order.get('fulfillment_method') or '',
            'preferred_schedule': order.get('preferred_schedule') or '',
            'location_details': order.get('location_details') or '',
            'fulfillment_notes': order.get('fulfillment_notes') or '',
        },
        request_submitted=False,
        admin_edit_mode=True,
        admin_edit_request_code=request_code,
    )


@app.route('/orders/<request_code>/edit-cart', methods=['POST'])
def start_editing_order_request(request_code: str):
    order_bundle = fetch_order_request(request_code)
    if not order_bundle:
        abort(404)
    order = order_bundle['request']
    is_staff = bool(session.get(ADMIN_SESSION_KEY))
    status = (order.get('status') or '').upper()
    if is_staff:
        if status not in STAFF_EDITABLE_STATUSES:
            flash(f'Order is {status}; reopen it before editing items.', 'error')
            return redirect(url_for('admin_order_detail_page', request_code=request_code))
    else:
        if status != 'NEW':
            flash(
                'This order is already being prepared by our staff, so it can no longer be '
                'edited online. Please message us with any changes and we\'ll update it for you.',
                'error',
            )
            return redirect(url_for('order_request_page', request_code=request_code))

    new_cart: dict[str, dict] = {}
    for item in order_bundle['items']:
        merkey = (item.get('merkey') or '').strip()
        if not merkey:
            continue
        option_key = (item.get('selling_option_key') or 'retail').strip() or 'retail'
        line_key = cart_line_key(merkey, option_key)
        new_cart[line_key] = {
            'merkey': merkey,
            'qty': max(1, int(item.get('requested_qty') or 1)),
            'option_key': option_key,
            'label': item.get('selling_option_label'),
            'price': item.get('quoted_price'),
            'barcode': item.get('selling_option_barcode'),
            'photo_url': item.get('display_photo_url'),
        }
    save_cart(new_cart)
    session[EDITING_REQUEST_KEY] = request_code
    session.modified = True
    flash(
        f'Now editing {request_code}. Browse and add items, adjust quantities in your cart, '
        f'then click "Save changes" to update this request.',
        'success',
    )
    return redirect(url_for('home'))


@app.route('/orders/<request_code>/apply-edit-cart', methods=['POST'])
@storefront_admin_required
def apply_editing_order_request_cart(request_code: str):
    if get_editing_request_code() != request_code:
        flash('No active edit cart found for this order.', 'error')
        return redirect(url_for('admin_order_detail_page', request_code=request_code))

    cart_items = build_cart_items()
    if not cart_items:
        flash('Edit cart is empty. Nothing was saved.', 'error')
        return redirect(url_for('cart_page'))

    order_bundle = fetch_order_request(request_code)
    if not order_bundle:
        abort(404)
    order = order_bundle['request']
    ok = replace_order_request_items(
        request_code,
        order.get('customer_name') or '',
        order.get('contact_number') or '',
        order.get('contact_email') or '',
        order.get('fulfillment_method') or '',
        order.get('preferred_schedule') or '',
        order.get('location_details') or '',
        order.get('fulfillment_notes') or '',
        cart_items,
        staff_override=True,
    )
    if not ok:
        flash(f'Could not save changes to {request_code}. Check the order status and try again.', 'error')
        return redirect(url_for('admin_order_detail_page', request_code=request_code))

    estimated_total = sum(item['line_price'] for item in cart_items)
    save_cart({})
    clear_editing_state()
    notify_ops_webhook(
        request_code=request_code,
        customer_name=order.get('customer_name') or '',
        item_count=sum(item['requested_qty'] for item in cart_items),
        estimated_total=estimated_total,
        action='updated',
        note=order.get('fulfillment_notes') or '',
    )
    flash(f'Order request {request_code} updated with edit cart changes.', 'success')
    return redirect(url_for('admin_order_detail_page', request_code=request_code))


@app.route('/orders/<request_code>/cancel-edit', methods=['POST'])
def cancel_editing_order_request(request_code: str):
    is_staff = bool(session.get(ADMIN_SESSION_KEY))
    if get_editing_request_code() == request_code:
        save_cart({})
        clear_editing_state()
        flash('Edits discarded. Your original request is unchanged.', 'success')
    return redirect(url_for(
        'admin_order_detail_page' if is_staff else 'order_request_page',
        request_code=request_code,
    ))


@app.route('/orders/<request_code>/manage', methods=['POST'])
def manage_order_request_page(request_code: str):
    action = (request.form.get('customer_action') or '').strip().lower()
    order_bundle = fetch_order_request(request_code)
    if not order_bundle:
        abort(404)

    if action == 'cancel':
        if cancel_order_request_by_customer(request_code):
            flash(f'Order request {request_code} was cancelled.', 'success')
        else:
            flash('This request can no longer be cancelled online. Please contact the store for help.', 'error')
    elif action == 'edit':
        ok, message = update_order_request_by_customer(request_code, request.form)
        flash(message, 'success' if ok else 'error')
    elif action == 'delete':
        if delete_order_request_by_customer(request_code):
            flash(f'Order request {request_code} was deleted.', 'success')
            return redirect(url_for('order_lookup_page'))
        flash('This request can no longer be deleted online. Please cancel it instead or contact the store.', 'error')
    elif action == 'customer_confirm':
        # Customer approving the picker's removed/out-of-stock items.
        # Stamps customer_confirmation_note so the staff knows to proceed.
        canonical = (order_bundle['request'].get('canonical_status') or '').upper()
        if canonical != 'AWAITING_CUSTOMER_CONFIRMATION':
            flash('This order is not waiting on your approval right now.', 'error')
        else:
            note = (request.form.get('customer_confirmation_note') or 'Customer approved removed items.').strip()
            if confirm_order_request(request_code, note):
                flash('Thanks — we approved the changes and will continue your order.', 'success')
            else:
                flash('Could not record your approval. Please try again or message us.', 'error')
    else:
        flash('Unknown order action.', 'error')

    return redirect(url_for('order_request_page', request_code=request_code))


@app.route('/search')
def search():
    q = (request.args.get('q') or '').strip()
    page = max(request.args.get('page', 1, type=int), 1)
    active_department = (request.args.get('department') or '').strip()
    # Searches rank by relevance ("Best Match") by default; browsing-only views
    # (filters with no query) keep the Top Sellers default.
    default_sort = 'relevance' if q else 'top'
    active_sort = request.args.get('sort') or default_sort
    active_price_band = request.args.get('price_band', '')

    department_options = fetch_departments()
    valid_department_slugs = {department['slug'] for department in department_options}
    if active_department and active_department not in valid_department_slugs:
        active_department = ''

    has_filters = bool(active_department or active_price_band or active_sort not in ('top', 'relevance'))

    did_you_mean = ''
    suggestions: list[dict] = []
    search_mode = ''

    if q:
        result = search_products(
            search_query=q,
            department_slug=active_department or None,
            price_band=active_price_band,
            sort_key=active_sort,
            page=page,
            per_page=PRODUCT_GRID_PAGE_SIZE,
        )
        products = result['products']
        pager = result['pager']
        active_sort = result['sort_key']
        active_price_band = result['price_band']
        did_you_mean = result['did_you_mean']
        suggestions = result['suggestions']
        search_mode = result['mode']
    elif has_filters:
        # No query, just department/price browsing — relevance has nothing to
        # rank against, so use the standard filtered browse.
        browse_sort = active_sort if active_sort != 'relevance' else 'top'
        products, pager, active_sort, active_price_band = fetch_products(
            department_slug=active_department or None,
            page=page,
            per_page=PRODUCT_GRID_PAGE_SIZE,
            sort_key=browse_sort,
            price_band=active_price_band,
        )
    else:
        products = []
        pager = make_pager(1, 0, PRODUCT_GRID_PAGE_SIZE)
        active_sort, _ = resolve_sort(active_sort)
        active_price_band, _, _ = resolve_price_band(active_price_band)

    breadcrumbs = [
        {'label': 'Home', 'url': url_for('home')},
        {'label': 'Search', 'url': None},
    ]

    return render_template(
        'search.html',
        breadcrumbs=breadcrumbs,
        q=q,
        products=products,
        pager=pager,
        department_options=department_options,
        active_department=active_department,
        active_sort=active_sort,
        active_price_band=active_price_band,
        sort_options=SORT_OPTIONS,
        price_bands=PRICE_BANDS,
        did_you_mean=did_you_mean,
        suggestions=suggestions,
        search_mode=search_mode,
    )


@app.route('/health')
def health():
    conn = get_conn()
    try:
        row = conn.execute('SELECT COUNT(*) AS c FROM products').fetchone()
        return {'status': 'ok', 'products': row['c'] if row else 0}
    finally:
        conn.close()


if __name__ == '__main__':
    ensure_runtime_schema()
    print(f"Starting storefront on http://{STOREFRONT_HOST}:{STOREFRONT_PORT}")
    print(f"Store DB path: {STORE_DB_PATH}")
    print(f"Debug mode: {STOREFRONT_DEBUG}")
    app.run(host=STOREFRONT_HOST, port=STOREFRONT_PORT, debug=STOREFRONT_DEBUG)

