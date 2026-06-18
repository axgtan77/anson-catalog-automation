PRAGMA foreign_keys = OFF;

CREATE TABLE IF NOT EXISTS storefront_metadata (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS departments (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    product_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY,
    department_id INTEGER,
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    product_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS products (
    merkey TEXT PRIMARY KEY,
    slug TEXT NOT NULL,
    name TEXT,
    brand TEXT,
    description TEXT,
    size TEXT,
    department_id INTEGER,
    department_name TEXT,
    department_slug TEXT,
    category_id INTEGER,
    category_name TEXT,
    category_slug TEXT,
    price_retail REAL,
    price_pack REAL,
    price_case REAL,
    pack_quantity INTEGER,
    show_pack_on_storefront INTEGER NOT NULL DEFAULT 0,
    default_selling_option TEXT NOT NULL DEFAULT 'retail',
    exclusive_selling_option INTEGER NOT NULL DEFAULT 0,
    pack_display_label TEXT,
    pack_photo_url TEXT,
    pack_barcode TEXT,
    photo_url TEXT,
    status TEXT,
    supplier_name TEXT,
    class_l1_name TEXT,
    class_l2_name TEXT,
    class_l3_name TEXT,
    barcode TEXT,
    all_barcodes TEXT,
    txn_count_24m REAL,
    qty_sum_24m REAL,
    last_sale_date TEXT,
    priority TEXT,
    last_acceptance_date TEXT,
    sellable_state TEXT,
    sellable_note TEXT,
    fulfillment_type TEXT,
    order_unit_label TEXT,
    substitution_policy TEXT,
    fulfillment_note TEXT,
    pricing_basis TEXT,
    min_weight_g INTEGER,
    max_weight_g INTEGER,
    display_weight_g INTEGER,
    display_price REAL,
    range_label TEXT,
    needs_irl_photo INTEGER NOT NULL DEFAULT 0,
    stock_status TEXT NOT NULL DEFAULT 'in_stock',
    alpha_visible INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    published_at TEXT DEFAULT CURRENT_TIMESTAMP,
    search_text TEXT
);

CREATE INDEX IF NOT EXISTS idx_departments_slug ON departments(slug);
CREATE INDEX IF NOT EXISTS idx_categories_slug ON categories(slug);
CREATE INDEX IF NOT EXISTS idx_categories_department_id ON categories(department_id);
CREATE INDEX IF NOT EXISTS idx_products_department_slug ON products(department_slug);
CREATE INDEX IF NOT EXISTS idx_products_category_slug ON products(category_slug);
CREATE INDEX IF NOT EXISTS idx_products_slug ON products(slug);
CREATE INDEX IF NOT EXISTS idx_products_brand ON products(brand);
CREATE INDEX IF NOT EXISTS idx_products_priority ON products(priority, txn_count_24m DESC);
CREATE INDEX IF NOT EXISTS idx_products_search_text ON products(search_text);

CREATE TABLE IF NOT EXISTS order_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_code TEXT NOT NULL UNIQUE,
    customer_name TEXT NOT NULL,
    contact_number TEXT NOT NULL,
    contact_email TEXT,
    fulfillment_method TEXT,
    preferred_schedule TEXT,
    location_details TEXT,
    fulfillment_notes TEXT,
    internal_note TEXT,
    status TEXT NOT NULL DEFAULT 'NEW',
    item_count INTEGER NOT NULL DEFAULT 0,
    confirmed_at TEXT,
    confirmed_total REAL,
    customer_confirmation_note TEXT,
    payment_status TEXT NOT NULL DEFAULT 'UNPAID',
    payment_method TEXT,
    payment_reference TEXT,
    payment_proof_path TEXT,
    payment_amount REAL,
    payment_submitted_at TEXT,
    payment_verified_at TEXT,
    payment_verified_by TEXT,
    payment_verification_note TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS order_request_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_request_id INTEGER NOT NULL,
    merkey TEXT NOT NULL,
    product_name TEXT NOT NULL,
    brand TEXT,
    requested_qty INTEGER NOT NULL DEFAULT 1,
    original_requested_qty INTEGER,
    order_unit_label TEXT,
    pricing_basis TEXT,
    quoted_price REAL,
    original_quoted_price REAL,
    sellable_state TEXT,
    fulfillment_type TEXT,
    fulfillment_note TEXT,
    removed INTEGER NOT NULL DEFAULT 0,
    removal_reason TEXT,
    selling_option_key TEXT NOT NULL DEFAULT 'retail',
    selling_option_label TEXT,
    selling_option_barcode TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(order_request_id) REFERENCES order_requests(id)
);

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

CREATE UNIQUE INDEX IF NOT EXISTS idx_customers_phone ON customers(phone) WHERE phone IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_customers_email ON customers(email) WHERE email IS NOT NULL;


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

CREATE INDEX IF NOT EXISTS idx_order_requests_status ON order_requests(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_order_request_items_order_request_id ON order_request_items(order_request_id);
