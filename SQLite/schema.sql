PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS departments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    slug TEXT UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    department_id INTEGER,
    slug TEXT UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (department_id) REFERENCES departments(id),
    UNIQUE(name, department_id)
);

CREATE TABLE IF NOT EXISTS brands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    slug TEXT UNIQUE,
    logo_url TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS products (
    merkey TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    name TEXT,
    brand_id INTEGER,
    category_id INTEGER,
    department_id INTEGER,
    size TEXT,
    weight_volume TEXT,
    unit_of_measurement TEXT,
    pack_quantity INTEGER,
    gp_percent REAL,
    data_quality TEXT DEFAULT 'NEEDS_DESCRIPTION',
    needs_enrichment INTEGER DEFAULT 1,
    enrichment_notes TEXT,
    active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (brand_id) REFERENCES brands(id),
    FOREIGN KEY (category_id) REFERENCES categories(id),
    FOREIGN KEY (department_id) REFERENCES departments(id)
);

CREATE TABLE IF NOT EXISTS barcodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    merkey TEXT NOT NULL,
    barcode TEXT NOT NULL,
    barcode_type TEXT DEFAULT 'EAN13',
    is_primary INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(merkey, barcode),
    FOREIGN KEY (merkey) REFERENCES products(merkey) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    merkey TEXT NOT NULL,
    price_retail REAL NOT NULL,
    price_pack REAL,
    price_case REAL,
    cost REAL,
    effective_date DATE NOT NULL DEFAULT (date('now')),
    is_current INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (merkey) REFERENCES products(merkey) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS inventory (
    merkey TEXT PRIMARY KEY,
    quantity_on_hand INTEGER DEFAULT 0,
    reorder_point INTEGER,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (merkey) REFERENCES products(merkey) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    merkey TEXT NOT NULL,
    filename TEXT,
    s3_url TEXT,
    cdn_url TEXT,
    local_path TEXT,
    is_primary INTEGER DEFAULT 0,
    width INTEGER,
    height INTEGER,
    file_size INTEGER,
    uploaded_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (merkey) REFERENCES products(merkey) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS sales_metrics (
    merkey TEXT PRIMARY KEY,
    last_sale_date DATE,
    txn_count_24m INTEGER DEFAULT 0,
    qty_sum_24m REAL DEFAULT 0,
    txn_count_30d INTEGER DEFAULT 0,
    qty_sum_30d REAL DEFAULT 0,
    active_24m INTEGER DEFAULT 0,
    priority TEXT,
    velocity_score REAL,
    rank_overall INTEGER,
    rank_category INTEGER,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (merkey) REFERENCES products(merkey) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sync_type TEXT NOT NULL,
    source_file TEXT,
    status TEXT CHECK(status IN ('IN_PROGRESS', 'SUCCESS', 'FAILED', 'PARTIAL')),
    records_processed INTEGER,
    records_updated INTEGER,
    records_added INTEGER,
    records_skipped INTEGER,
    error_message TEXT,
    started_at TIMESTAMP,
    completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TRIGGER IF NOT EXISTS update_product_timestamp 
AFTER UPDATE ON products
FOR EACH ROW
BEGIN
    UPDATE products SET updated_at = CURRENT_TIMESTAMP WHERE merkey = NEW.merkey;
END;

CREATE TRIGGER IF NOT EXISTS ensure_single_primary_barcode
BEFORE INSERT ON barcodes
FOR EACH ROW
WHEN NEW.is_primary = 1
BEGIN
    UPDATE barcodes SET is_primary = 0 WHERE merkey = NEW.merkey AND is_primary = 1;
END;

CREATE TRIGGER IF NOT EXISTS ensure_single_primary_image
BEFORE INSERT ON images
FOR EACH ROW
WHEN NEW.is_primary = 1
BEGIN
    UPDATE images SET is_primary = 0 WHERE merkey = NEW.merkey AND is_primary = 1;
END;

CREATE TRIGGER IF NOT EXISTS mark_old_prices_not_current
BEFORE INSERT ON prices
FOR EACH ROW
WHEN NEW.is_current = 1
BEGIN
    UPDATE prices SET is_current = 0 WHERE merkey = NEW.merkey AND is_current = 1;
END;

INSERT OR IGNORE INTO departments (id, name, slug) VALUES (0, 'Unknown', 'unknown');
INSERT OR IGNORE INTO categories (id, name, department_id, slug) VALUES (0, 'Unknown', 0, 'unknown');
INSERT OR IGNORE INTO brands (id, name, slug) VALUES (0, 'Unknown', 'unknown');

CREATE VIEW IF NOT EXISTS v_products_needing_enrichment AS
SELECT p.merkey, p.description, p.name, p.data_quality, COALESCE(s.txn_count_24m,0) as txn_count_24m
FROM products p
LEFT JOIN sales_metrics s ON p.merkey = s.merkey
WHERE p.active = 1 AND p.needs_enrichment = 1
ORDER BY txn_count_24m DESC;
