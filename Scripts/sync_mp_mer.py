#!/usr/bin/env python3
"""
Anson Supermart - MP_MER.FPB Sync with Change Detection
Syncs prices, detects new products, tracks MEDESC changes

Usage:
    python sync_mp_mer.py [path/to/MP_MER.FPB]
    
Features:
- Detects new products
- Tracks price changes (with history)
- Flags MEDESC changes (possible size/property changes)
- Updates barcodes
- Logs all changes for review
"""

import struct
import sqlite3
import os
import sys
from datetime import datetime


def read_dbf_file(filepath):
    """Read FoxPro DBF file and return records"""
    
    with open(filepath, 'rb') as f:
        # Read header
        header = f.read(32)
        num_records = struct.unpack('<I', header[4:8])[0]
        header_length = struct.unpack('<H', header[8:10])[0]
        record_length = struct.unpack('<H', header[10:12])[0]
        
        # Read field descriptors
        f.seek(32)
        fields = []
        field_positions = {}
        current_pos = 1
        
        while True:
            field_desc = f.read(32)
            if field_desc[0] == 0x0D:
                break
            
            field_name = field_desc[0:11].split(b'\x00')[0].decode('ascii', errors='ignore').strip()
            field_type = chr(field_desc[11])
            field_length = field_desc[16]
            
            field_positions[field_name] = (current_pos, field_length, field_type)
            fields.append({'name': field_name, 'type': field_type, 'length': field_length})
            current_pos += field_length
        
        # Read all records
        f.seek(header_length)
        records = []
        
        for i in range(num_records):
            record_data = f.read(record_length)
            
            if not record_data or len(record_data) < record_length:
                break
            
            if record_data[0] == 0x2A:  # Deleted record
                continue
            
            record = {}
            for field_name, (pos, length, ftype) in field_positions.items():
                field_data = record_data[pos:pos + length]
                
                if ftype == 'C':
                    value = field_data.decode('latin-1', errors='ignore').strip()
                elif ftype == 'N':
                    value = field_data.decode('ascii', errors='ignore').strip()
                elif ftype == 'D':
                    value = field_data.decode('ascii', errors='ignore').strip()
                else:
                    value = field_data.decode('latin-1', errors='ignore').strip()
                
                record[field_name] = value
            
            records.append(record)
            
            if (i + 1) % 5000 == 0:
                print(f"  Reading {i + 1:,} / {num_records:,} records...", end='\r')
        
        print(f"  Read {len(records):,} records                    ")
        
    return records


def parse_float(value):
    """Parse float safely"""
    try:
        return float(value) if value else 0.0
    except:
        return 0.0


def parse_int(value):
    """Parse int safely"""
    try:
        return int(value) if value else 0
    except:
        return 0


def compute_markup_pct(retail_price, unit_cost):
    """Compute markup percentage over unit cost."""
    if not unit_cost or unit_cost <= 0:
        return None
    return ((retail_price - unit_cost) / unit_cost) * 100.0


# ---------------------------------------------------------------------------
# Price sanity thresholds
# ---------------------------------------------------------------------------
# Max percentage change before a price update is quarantined for review.
PRICE_CHANGE_PCT_THRESHOLD = 200.0
# Max markup over unit cost before flagging as suspicious.
MARKUP_CEILING_PCT = 500.0
# Minimum absolute price jump (₱) to bother checking — tiny changes are fine.
PRICE_CHANGE_ABS_MIN = 10.0


def check_price_sanity(
    merkey, medesc, old_price, new_price, unit_cost,
    case_price, pack_price,
):
    """
    Return two warning lists:
      - hard_warnings: should quarantine the price update
      - soft_warnings: informative review signals only
    """
    hard_warnings = []
    soft_warnings = []
    if old_price <= 0:
        return hard_warnings, soft_warnings

    diff = new_price - old_price
    abs_diff = abs(diff)
    pct_change = (diff / old_price) * 100.0

    # 1. Extreme percentage swing
    if abs(pct_change) > PRICE_CHANGE_PCT_THRESHOLD and abs_diff > PRICE_CHANGE_ABS_MIN:
        hard_warnings.append(
            f"Price change {pct_change:+.1f}% exceeds ±{PRICE_CHANGE_PCT_THRESHOLD:.0f}% threshold "
            f"(₱{old_price:.2f} → ₱{new_price:.2f})"
        )

    # 2. Mode-equality signals
    if case_price > 0 and abs(new_price - case_price) < 0.02 and abs(new_price - old_price) > 1.0:
        hard_warnings.append(
            f"New retail ₱{new_price:.2f} matches case price ₱{case_price:.2f} — possible mode mixup"
        )
    if pack_price > 0 and abs(new_price - pack_price) < 0.02 and abs(new_price - old_price) > 1.0:
        soft_warnings.append(
            f"New retail ₱{new_price:.2f} matches pack price ₱{pack_price:.2f} — review mode semantics"
        )

    # 3. Markup over unit cost exceeds ceiling
    if unit_cost and unit_cost > 0:
        markup = ((new_price - unit_cost) / unit_cost) * 100.0
        if markup > MARKUP_CEILING_PCT:
            hard_warnings.append(
                f"Markup {markup:+.1f}% over unit cost ₱{unit_cost:.2f} exceeds {MARKUP_CEILING_PCT:.0f}% ceiling"
            )

    # 4. Price dropped to near-zero (possible accidental clear)
    if new_price < 1.0 and old_price >= 5.0:
        hard_warnings.append(
            f"Price dropped to near-zero ₱{new_price:.2f} from ₱{old_price:.2f}"
        )

    return hard_warnings, soft_warnings


def check_new_product_sanity(merkey, medesc, price, unit_cost, case_price, pack_price):
    """
    Return a list of warning strings for a brand-new product's initial price.
    Empty list = price looks fine.
    """
    warnings = []

    # Piece price equals case price — likely entered in wrong mode
    if case_price > 0 and abs(price - case_price) < 0.02 and pack_price > 0 and price > pack_price * 1.5:
        warnings.append(
            f"Retail ₱{price:.2f} matches case price ₱{case_price:.2f} — possible mode mixup"
        )

    # Extreme markup on a new product
    if unit_cost and unit_cost > 0:
        markup = ((price - unit_cost) / unit_cost) * 100.0
        if markup > MARKUP_CEILING_PCT:
            warnings.append(
                f"Markup {markup:+.1f}% over unit cost ₱{unit_cost:.2f} exceeds {MARKUP_CEILING_PCT:.0f}% ceiling"
            )

    return warnings


def extract_unit_cost(record):
    """
    Best-effort retail unit cost for log/reporting.
    Prefer MECOS2 because it most closely matches the retail unit (MEPCK3).
    Fallback to MECOS1, then MECOS0 if needed.
    """
    for field in ("MECOS2", "MECOS1", "MECOS0"):
        cost = parse_float(record.get(field, '').strip() if isinstance(record.get(field, ''), str) else record.get(field, ''))
        if cost > 0:
            return cost, field
    return 0.0, ""


def clean_text(text):
    """Clean and normalize text"""
    return ' '.join((text or '').strip().split())


def normalize_barcode(value):
    """Normalize barcode text from DBF fields."""
    text = clean_text(value)
    # Ignore masked/non-printable placeholder values (e.g. ***********/*).
    if not text:
        return ""
    if not text.isdigit():
        return ""
    # Keep practical retail barcode lengths (EAN8/UPC/EAN13/etc).
    if len(text) < 6 or len(text) > 18:
        return ""
    return text


def unique_preserve_order(items):
    seen = set()
    out = []
    for item in items:
        if not item:
            continue
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def extract_product_barcodes(record):
    """
    Barcode policy:
    - Primary: SUSTOK (item barcode)
    - Fallback primary: MEAN13 (legacy)
    - Keep SUSTK1/SUSTK2 and other barcode fields as alternates.
    """
    sustok = normalize_barcode(record.get("SUSTOK", ""))
    mean13 = normalize_barcode(record.get("MEAN13", ""))
    sustk1 = normalize_barcode(record.get("SUSTK1", ""))
    sustk2 = normalize_barcode(record.get("SUSTK2", ""))
    barcd1 = normalize_barcode(record.get("BARCD1", ""))
    barcd2 = normalize_barcode(record.get("BARCD2", ""))
    barcd3 = normalize_barcode(record.get("BARCD3", ""))
    barcd4 = normalize_barcode(record.get("BARCD4", ""))
    barcd5 = normalize_barcode(record.get("BARCD5", ""))

    primary = sustok or mean13
    candidates = unique_preserve_order(
        [primary, sustok, mean13, sustk1, sustk2, barcd1, barcd2, barcd3, barcd4, barcd5]
    )
    if not primary and candidates:
        primary = candidates[0]
    return primary, candidates


def load_supplier_name_map(mp_sup_path):
    """Load supplier code -> supplier name map from MP_SUP.FPB."""
    if not mp_sup_path:
        return {}
    if not os.path.exists(mp_sup_path):
        print(f"Warning: MP_SUP not found, supplier names will not be updated: {mp_sup_path}")
        return {}

    try:
        sup_records = read_dbf_file(mp_sup_path)
    except Exception as exc:
        print(f"Warning: failed reading MP_SUP ({exc}); supplier names will not be updated.")
        return {}

    supplier_map = {}
    for record in sup_records:
        code = clean_text(record.get("SURKEY", ""))
        name = clean_text(record.get("SUDESC", ""))
        if code:
            supplier_map[code] = name
    return supplier_map


def load_class_map(mp_cls_path):
    """Load CLRKEY -> CLDESC map from MP_CLS.FPB."""
    if not mp_cls_path:
        return {}
    if not os.path.exists(mp_cls_path):
        print(f"Warning: MP_CLS not found, class hierarchy will not be updated: {mp_cls_path}")
        return {}

    try:
        cls_records = read_dbf_file(mp_cls_path)
    except Exception as exc:
        print(f"Warning: failed reading MP_CLS ({exc}); class hierarchy will not be updated.")
        return {}

    cls_map = {}
    for record in cls_records:
        clrkey = clean_text(record.get("CLRKEY", ""))
        cldesc = clean_text(record.get("CLDESC", ""))
        if clrkey:
            cls_map[clrkey] = cldesc
    return cls_map


def class_level(clrkey):
    """Return hierarchy level from CLRKEY format."""
    if len(clrkey) != 6 or not clrkey.isdigit():
        return 0
    if clrkey.endswith("0000"):
        return 1
    if clrkey.endswith("00"):
        return 2
    return 3


def derive_class_triplet(clrkey, cls_map):
    """Return L1/L2/L3 code+name dictionary from a CLRKEY."""
    key = clean_text(clrkey)
    if len(key) != 6 or not key.isdigit():
        return {
            "clrkey": "",
            "class_l1_code": "",
            "class_l1_name": "",
            "class_l2_code": "",
            "class_l2_name": "",
            "class_l3_code": "",
            "class_l3_name": "",
        }

    l1 = f"{key[:2]}0000"
    l2 = f"{key[:4]}00"
    l3 = key
    return {
        "clrkey": key,
        "class_l1_code": l1,
        "class_l1_name": clean_text(cls_map.get(l1, "")),
        "class_l2_code": l2,
        "class_l2_name": clean_text(cls_map.get(l2, "")),
        "class_l3_code": l3,
        "class_l3_name": clean_text(cls_map.get(l3, "")),
    }


def ensure_first_seen_date_column(conn):
    """Ensure products.first_seen_date exists and backfill from created_at when missing."""
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(products)")
    columns = {row[1] for row in cursor.fetchall()}

    if "first_seen_date" not in columns:
        cursor.execute("ALTER TABLE products ADD COLUMN first_seen_date DATE")
        conn.commit()

    # Backfill only from created_at-derived date; keep NULL if created_at missing.
    cursor.execute(
        """
        UPDATE products
        SET first_seen_date = date(created_at)
        WHERE first_seen_date IS NULL
          AND created_at IS NOT NULL
          AND TRIM(created_at) <> ''
        """
    )
    conn.commit()


def ensure_source_medesc_column(conn):
    """Ensure products.source_medesc exists for non-destructive source tracking."""
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(products)")
    columns = {row[1] for row in cursor.fetchall()}

    if "source_medesc" not in columns:
        cursor.execute("ALTER TABLE products ADD COLUMN source_medesc TEXT")
        conn.commit()

    # Backfill from existing description for legacy rows.
    cursor.execute(
        """
        UPDATE products
        SET source_medesc = description
        WHERE source_medesc IS NULL
          AND description IS NOT NULL
          AND TRIM(description) <> ''
        """
    )
    conn.commit()


def ensure_supplier_code_column(conn):
    """Ensure products.supplier_code exists for supplier-oriented workflows."""
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(products)")
    columns = {row[1] for row in cursor.fetchall()}

    if "supplier_code" not in columns:
        cursor.execute("ALTER TABLE products ADD COLUMN supplier_code TEXT")
        conn.commit()


def ensure_supplier_name_column(conn):
    """Ensure products.supplier_name exists for supplier-oriented workflows."""
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(products)")
    columns = {row[1] for row in cursor.fetchall()}

    if "supplier_name" not in columns:
        cursor.execute("ALTER TABLE products ADD COLUMN supplier_name TEXT")
        conn.commit()


def ensure_class_columns(conn):
    """Ensure products has CLRKEY and class hierarchy columns."""
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(products)")
    columns = {row[1] for row in cursor.fetchall()}
    needed = {
        "clrkey": "TEXT",
        "class_l1_code": "TEXT",
        "class_l1_name": "TEXT",
        "class_l2_code": "TEXT",
        "class_l2_name": "TEXT",
        "class_l3_code": "TEXT",
        "class_l3_name": "TEXT",
    }
    changed = False
    for col, coltype in needed.items():
        if col not in columns:
            cursor.execute(f"ALTER TABLE products ADD COLUMN {col} {coltype}")
            changed = True
    if changed:
        conn.commit()


def ensure_class_hierarchy_table(conn):
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS class_hierarchy (
            clrkey TEXT PRIMARY KEY,
            cldesc TEXT,
            level INTEGER,
            class_l1_code TEXT,
            class_l1_name TEXT,
            class_l2_code TEXT,
            class_l2_name TEXT,
            class_l3_code TEXT,
            class_l3_name TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()


def upsert_class_hierarchy(conn, cls_map):
    """Upsert class dictionary rows for reporting/filtering."""
    if not cls_map:
        return 0
    cursor = conn.cursor()
    upserts = 0
    for clrkey, cldesc in cls_map.items():
        class_bits = derive_class_triplet(clrkey, cls_map)
        cursor.execute(
            """
            INSERT INTO class_hierarchy (
                clrkey, cldesc, level,
                class_l1_code, class_l1_name,
                class_l2_code, class_l2_name,
                class_l3_code, class_l3_name,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(clrkey) DO UPDATE SET
                cldesc=excluded.cldesc,
                level=excluded.level,
                class_l1_code=excluded.class_l1_code,
                class_l1_name=excluded.class_l1_name,
                class_l2_code=excluded.class_l2_code,
                class_l2_name=excluded.class_l2_name,
                class_l3_code=excluded.class_l3_code,
                class_l3_name=excluded.class_l3_name,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                clrkey,
                cldesc,
                class_level(clrkey),
                class_bits["class_l1_code"],
                class_bits["class_l1_name"],
                class_bits["class_l2_code"],
                class_bits["class_l2_name"],
                class_bits["class_l3_code"],
                class_bits["class_l3_name"],
            ),
        )
        upserts += 1
    conn.commit()
    return upserts


def sync_mp_mer(db_path='anson_products.db', mp_mer_path=None, mp_sup_path=None, mp_cls_path=None):
    """Sync from MP_MER.FPB with comprehensive change detection"""
    
    print("=" * 80)
    print("MP_MER.FPB SYNC - CHANGE DETECTION")
    print("=" * 80)
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    if not mp_mer_path:
        print("❌ Error: MP_MER.FPB path required")
        print("Usage: python sync_mp_mer.py path/to/MP_MER.FPB")
        return False
    
    if not os.path.exists(mp_mer_path):
        print(f"❌ Error: File not found: {mp_mer_path}")
        return False
    
    print(f"Database: {db_path}")
    print(f"MP_MER:   {mp_mer_path}")
    if mp_sup_path:
        print(f"MP_SUP:   {mp_sup_path}")
    if mp_cls_path:
        print(f"MP_CLS:   {mp_cls_path}")
    print()
    
    # Connect to database
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    cursor = conn.cursor()

    # Lightweight schema migration for new-product tracking.
    ensure_first_seen_date_column(conn)
    ensure_source_medesc_column(conn)
    ensure_supplier_code_column(conn)
    ensure_supplier_name_column(conn)
    ensure_class_columns(conn)
    ensure_class_hierarchy_table(conn)

    supplier_name_map = load_supplier_name_map(mp_sup_path)
    if supplier_name_map:
        print(f"Loaded {len(supplier_name_map):,} suppliers from MP_SUP")
    cls_map = load_class_map(mp_cls_path)
    if cls_map:
        cls_count = upsert_class_hierarchy(conn, cls_map)
        print(f"Loaded {cls_count:,} class rows from MP_CLS")
    
    # Start sync log
    cursor.execute("""
        INSERT INTO sync_log (sync_type, source_file, status, started_at)
        VALUES ('MP_MER_SYNC', ?, 'IN_PROGRESS', CURRENT_TIMESTAMP)
    """, (os.path.basename(mp_mer_path),))
    sync_id = cursor.lastrowid
    conn.commit()
    
    # Track changes
    changes = {
        'new_products': [],
        'price_changes': [],
        'price_anomalies': [],   # suspicious changes quarantined for review
        'medesc_changes': [],
        'barcode_changes': [],
        'status_changes': [],
        'products_processed': 0,
        'prices_updated': 0,
        'prices_quarantined': 0,
        'barcodes_added': 0,
        'class_updates': 0,
    }
    
    try:
        # Read MP_MER
        print("=" * 80)
        print("READING MP_MER.FPB")
        print("=" * 80)
        records = read_dbf_file(mp_mer_path)
        
        print()
        print("=" * 80)
        print("DETECTING CHANGES")
        print("=" * 80)
        print()
        
        # Get existing products from database
        cursor.execute("""
            SELECT merkey, description, active, source_medesc, supplier_code, supplier_name,
                   clrkey, class_l1_code, class_l2_code, class_l3_code
            FROM products
        """)
        existing_products = {
            row[0]: {
                'description': row[1],
                'active': row[2],
                'source_medesc': row[3],
                'supplier_code': row[4],
                'supplier_name': row[5],
                'clrkey': row[6],
                'class_l1_code': row[7],
                'class_l2_code': row[8],
                'class_l3_code': row[9],
            }
            for row in cursor.fetchall()
        }
        
        # Get current prices
        cursor.execute("""
            SELECT merkey, price_retail
            FROM prices
            WHERE is_current = 1
        """)
        current_prices = {row[0]: row[1] for row in cursor.fetchall()}
        
        # Process each record from MP_MER
        for i, record in enumerate(records, 1):
            merkey = record.get('MERKEY', '').strip()
            if not merkey:
                continue
            
            medesc = clean_text(record.get('MEDESC', ''))
            if not medesc:
                continue
            
            # Get prices across all selling modes
            price_raw = record.get('MERETP', '').strip()       # Mode 3 (Retail/Piece)
            price = parse_float(price_raw)
            case_price = parse_float(record.get('MEWHOP', ''))  # Mode 1 (Case/Wholesale)
            pack_price = parse_float(record.get('MERET2', ''))  # Mode 2 (Pack)

            if price <= 0:
                continue  # Skip products with no price
            
            primary_barcode, barcodes = extract_product_barcodes(record)
            supplier_code = record.get('SURKEY', '').strip()
            supplier_name = supplier_name_map.get(supplier_code, "") if supplier_code else ""
            class_bits = derive_class_triplet(record.get('CLRKEY', ''), cls_map)
            
            # Check if product exists
            if merkey not in existing_products:
                # NEW PRODUCT
                unit_cost, unit_cost_field = extract_unit_cost(record)
                markup_pct = compute_markup_pct(price, unit_cost)

                new_warnings = check_new_product_sanity(
                    merkey, medesc, price, unit_cost, case_price, pack_price,
                )

                new_entry = {
                    'merkey': merkey,
                    'medesc': medesc,
                    'price': price,
                    'case_price': case_price,
                    'pack_price': pack_price,
                    'unit_cost': unit_cost,
                    'unit_cost_field': unit_cost_field,
                    'markup_pct': markup_pct,
                    'primary_barcode': primary_barcode,
                    'barcodes': list(barcodes),
                    'warnings': new_warnings,
                }
                changes['new_products'].append(new_entry)

                if new_warnings:
                    changes['price_anomalies'].append({
                        'merkey': merkey,
                        'medesc': medesc,
                        'old_price': 0.0,
                        'new_price': price,
                        'case_price': case_price,
                        'pack_price': pack_price,
                        'unit_cost': unit_cost,
                        'markup_pct': markup_pct,
                        'warnings': new_warnings,
                        'source': 'new_product',
                    })

                # Insert new product
                cursor.execute("""
                    INSERT INTO products (
                        merkey, description, source_medesc, supplier_code, supplier_name,
                        clrkey, class_l1_code, class_l1_name, class_l2_code, class_l2_name, class_l3_code, class_l3_name,
                        data_quality, needs_enrichment, active, first_seen_date
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'NEEDS_DESCRIPTION', 1, 1, date('now'))
                """, (
                    merkey, medesc, medesc, supplier_code, supplier_name,
                    class_bits["clrkey"],
                    class_bits["class_l1_code"], class_bits["class_l1_name"],
                    class_bits["class_l2_code"], class_bits["class_l2_name"],
                    class_bits["class_l3_code"], class_bits["class_l3_name"],
                ))

                # Insert price (store all three modes + cost)
                cursor.execute("""
                    INSERT INTO prices (
                        merkey, price_retail, price_pack, price_case, cost,
                        effective_date, is_current
                    ) VALUES (?, ?, ?, ?, ?, date('now'), 1)
                """, (merkey, price,
                      pack_price if pack_price > 0 else None,
                      case_price if case_price > 0 else None,
                      unit_cost if unit_cost > 0 else None))

                changes['prices_updated'] += 1
                
            else:
                # EXISTING PRODUCT - Check for changes
                
                # 1. Check MEDESC change
                old_source_medesc = existing_products[merkey].get('source_medesc') or existing_products[merkey]['description']
                if medesc != old_source_medesc:
                    changes['medesc_changes'].append({
                        'merkey': merkey,
                        'old_medesc': old_source_medesc,
                        'new_medesc': medesc,
                        'reason': 'Possible size/property change'
                    })
                    
                    # Do not overwrite curated description; track source MEDESC changes separately.
                    cursor.execute("""
                        UPDATE products SET
                            source_medesc = ?,
                            needs_enrichment = 1,
                            data_quality = 'NEEDS_REVIEW',
                            enrichment_notes = 'SOURCE MEDESC changed - review curated fields',
                            updated_at = CURRENT_TIMESTAMP
                        WHERE merkey = ?
                    """, (medesc, merkey))

                # 1b. Keep supplier code in sync from source file.
                old_supplier = (existing_products[merkey].get('supplier_code') or '').strip()
                old_supplier_name = (existing_products[merkey].get('supplier_name') or '').strip()
                should_update_supplier_name = bool(supplier_name_map) and supplier_name != old_supplier_name
                if supplier_code != old_supplier or should_update_supplier_name:
                    cursor.execute("""
                        UPDATE products SET
                            supplier_code = ?,
                            supplier_name = CASE
                                WHEN ? = 1 THEN ?
                                ELSE supplier_name
                            END,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE merkey = ?
                    """, (supplier_code, 1 if should_update_supplier_name else 0, supplier_name, merkey))

                # 1c. Keep class hierarchy in sync from source CLRKEY.
                old_clrkey = (existing_products[merkey].get('clrkey') or '').strip()
                old_l1 = (existing_products[merkey].get('class_l1_code') or '').strip()
                old_l2 = (existing_products[merkey].get('class_l2_code') or '').strip()
                old_l3 = (existing_products[merkey].get('class_l3_code') or '').strip()
                if (
                    class_bits["clrkey"] != old_clrkey
                    or class_bits["class_l1_code"] != old_l1
                    or class_bits["class_l2_code"] != old_l2
                    or class_bits["class_l3_code"] != old_l3
                ):
                    cursor.execute(
                        """
                        UPDATE products SET
                            clrkey = ?,
                            class_l1_code = ?, class_l1_name = ?,
                            class_l2_code = ?, class_l2_name = ?,
                            class_l3_code = ?, class_l3_name = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE merkey = ?
                        """,
                        (
                            class_bits["clrkey"],
                            class_bits["class_l1_code"], class_bits["class_l1_name"],
                            class_bits["class_l2_code"], class_bits["class_l2_name"],
                            class_bits["class_l3_code"], class_bits["class_l3_name"],
                            merkey,
                        ),
                    )
                    changes['class_updates'] += 1
                
                # 2. Check price change
                if merkey in current_prices:
                    old_price = current_prices[merkey]
                    if abs(price - old_price) > 0.01:  # Price changed
                        price_diff = price - old_price
                        price_pct = (price_diff / old_price * 100) if old_price > 0 else 0
                        unit_cost, unit_cost_field = extract_unit_cost(record)
                        markup_pct = compute_markup_pct(price, unit_cost)

                        change_entry = {
                            'merkey': merkey,
                            'medesc': medesc,
                            'old_price': old_price,
                            'new_price': price,
                            'diff': price_diff,
                            'pct_change': price_pct,
                            'case_price': case_price,
                            'pack_price': pack_price,
                            'unit_cost': unit_cost,
                            'unit_cost_field': unit_cost_field,
                            'markup_pct': markup_pct,
                        }

                        # --- Sanity check ---
                        hard_warnings, soft_warnings = check_price_sanity(
                            merkey, medesc, old_price, price, unit_cost,
                            case_price, pack_price,
                        )

                        if hard_warnings or soft_warnings:
                            change_entry['warnings'] = [*hard_warnings, *soft_warnings]
                        if soft_warnings:
                            changes['price_anomalies'].append({
                                **change_entry,
                                'warnings': soft_warnings,
                                'source': 'price_change_soft_review',
                                'quarantined': False,
                            })

                        if hard_warnings:
                            # Quarantine: do NOT apply this price change
                            changes['price_anomalies'].append({
                                **change_entry,
                                'warnings': hard_warnings,
                                'source': 'price_change',
                                'quarantined': True,
                            })
                            changes['prices_quarantined'] += 1
                        else:
                            # Safe — apply normally
                            changes['price_changes'].append(change_entry)

                            # Mark old price as not current
                            cursor.execute("""
                                UPDATE prices SET is_current = 0
                                WHERE merkey = ? AND is_current = 1
                            """, (merkey,))

                            # Insert new price (all modes + cost)
                            cursor.execute("""
                                INSERT INTO prices (
                                    merkey, price_retail, price_pack, price_case, cost,
                                    effective_date, is_current
                                ) VALUES (?, ?, ?, ?, ?, date('now'), 1)
                            """, (merkey, price,
                                  pack_price if pack_price > 0 else None,
                                  case_price if case_price > 0 else None,
                                  unit_cost if unit_cost > 0 else None))

                            changes['prices_updated'] += 1
                else:
                    # No price record yet - add one
                    unit_cost, _ = extract_unit_cost(record)
                    cursor.execute("""
                        INSERT INTO prices (
                            merkey, price_retail, price_pack, price_case, cost,
                            effective_date, is_current
                        ) VALUES (?, ?, ?, ?, ?, date('now'), 1)
                    """, (merkey, price,
                          pack_price if pack_price > 0 else None,
                          case_price if case_price > 0 else None,
                          unit_cost if unit_cost > 0 else None))
                    changes['prices_updated'] += 1
            
            # 3. Update/add barcodes
            if barcodes:
                # Get existing barcodes
                cursor.execute("""
                    SELECT barcode FROM barcodes WHERE merkey = ?
                """, (merkey,))
                existing_barcodes = {row[0] for row in cursor.fetchall()}
                
                for barcode in barcodes:
                    if barcode not in existing_barcodes:
                        cursor.execute("""
                            INSERT OR IGNORE INTO barcodes (
                                merkey, barcode, is_primary
                            ) VALUES (?, ?, 0)
                        """, (merkey, barcode))
                        
                        if cursor.rowcount > 0:
                            changes['barcodes_added'] += 1
                            changes['barcode_changes'].append({
                                'merkey': merkey,
                                'barcode': barcode,
                                'primary_barcode': primary_barcode,
                                'medesc': medesc,
                            })

                # Enforce primary barcode policy every sync:
                # SUSTOK first, fallback to MEAN13.
                if primary_barcode:
                    cursor.execute(
                        """
                        UPDATE barcodes
                        SET is_primary = CASE WHEN barcode = ? THEN 1 ELSE 0 END
                        WHERE merkey = ?
                        """,
                        (primary_barcode, merkey),
                    )
            
            changes['products_processed'] += 1
            
            if i % 1000 == 0:
                conn.commit()
                print(f"  Processed {i:,} / {len(records):,} products...", end='\r')
        
        conn.commit()
        print()
        print(f"  ✓ Processed {changes['products_processed']:,} products")
        print()
        
        # Update sync log
        cursor.execute("""
            UPDATE sync_log SET
                status = 'SUCCESS',
                records_processed = ?,
                records_updated = ?,
                records_added = ?,
                completed_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (changes['products_processed'], changes['prices_updated'], 
              len(changes['new_products']), sync_id))
        
        conn.commit()
        
        # Print detailed change report
        print("=" * 80)
        print("CHANGE DETECTION REPORT")
        print("=" * 80)
        print()
        
        print(f"Products processed: {changes['products_processed']:,}")
        print()
        
        # New products
        if changes['new_products']:
            flagged_new = sum(1 for p in changes['new_products'] if p.get('warnings'))
            flag_note = f" ({flagged_new} flagged)" if flagged_new else ""
            print(f"🆕 NEW PRODUCTS: {len(changes['new_products']):,}{flag_note}")
            print()
            for prod in changes['new_products'][:20]:  # Show first 20
                markup_label = (
                    f" markup {prod['markup_pct']:+.1f}%"
                    if prod.get('markup_pct') is not None
                    else " markup n/a"
                )
                warn_marker = " ⚠" if prod.get('warnings') else ""
                print(
                    f"  + {prod['merkey']:10s} {prod['medesc'][:60]:60s} "
                    f"₱{prod['price']:>8.2f} | cost ₱{prod.get('unit_cost', 0):>8.2f} |{markup_label}{warn_marker}"
                )
            if len(changes['new_products']) > 20:
                print(f"  ... and {len(changes['new_products']) - 20} more")
            print()
        else:
            print("✓ No new products")
            print()
        
        # Price changes
        if changes['price_changes']:
            print(f"💰 PRICE CHANGES: {len(changes['price_changes']):,}")
            print()
            for change in sorted(changes['price_changes'], 
                                key=lambda x: abs(x['pct_change']), 
                                reverse=True)[:20]:
                direction = "↑" if change['diff'] > 0 else "↓"
                print(f"  {direction} {change['merkey']:10s} {change['medesc'][:50]:50s}")
                print(f"     ₱{change['old_price']:>8.2f} → ₱{change['new_price']:>8.2f} "
                      f"({change['pct_change']:>+6.1f}%)")
                if change.get('markup_pct') is not None:
                    print(
                        f"     Unit cost ₱{change.get('unit_cost', 0):>8.2f} | "
                        f"Current markup {change['markup_pct']:+.1f}%"
                    )
                else:
                    print("     Unit cost n/a | Current markup n/a")
            if len(changes['price_changes']) > 20:
                print(f"  ... and {len(changes['price_changes']) - 20} more")
            print()
        else:
            print("✓ No price changes")
            print()

        # Price anomalies (quarantined)
        if changes['price_anomalies']:
            print(f"🚨 PRICE ANOMALIES (QUARANTINED - NOT APPLIED): {len(changes['price_anomalies']):,}")
            print()
            for anomaly in changes['price_anomalies'][:30]:
                src = anomaly.get('source', 'unknown')
                if src == 'new_product':
                    label = "NEW"
                elif src == 'price_change_soft_review':
                    label = "REV"
                else:
                    label = "CHG"
                old_p = anomaly.get('old_price', 0)
                new_p = anomaly.get('new_price', 0)
                if old_p > 0:
                    pct = ((new_p - old_p) / old_p) * 100.0
                    price_line = f"₱{old_p:.2f} → ₱{new_p:.2f} ({pct:+.1f}%)"
                else:
                    price_line = f"₱{new_p:.2f}"
                mode_info = ""
                if anomaly.get('case_price', 0) > 0:
                    mode_info += f" | case ₱{anomaly['case_price']:.2f}"
                if anomaly.get('pack_price', 0) > 0:
                    mode_info += f" | pack ₱{anomaly['pack_price']:.2f}"
                print(f"  [{label}] {anomaly['merkey']:10s} {anomaly['medesc'][:45]:45s}")
                print(f"       {price_line}{mode_info}")
                for w in anomaly.get('warnings', []):
                    print(f"       ⚠ {w}")
                print()
            if len(changes['price_anomalies']) > 30:
                print(f"  ... and {len(changes['price_anomalies']) - 30} more")
            print()
        else:
            print("✓ No price anomalies")
            print()

        # MEDESC changes (IMPORTANT!)
        if changes['medesc_changes']:
            print(f"⚠️  MEDESC CHANGES: {len(changes['medesc_changes']):,}")
            print("    (Possible size/property changes - REVIEW REQUIRED)")
            print()
            for change in changes['medesc_changes'][:20]:
                print(f"  ! {change['merkey']:10s}")
                print(f"     OLD: {change['old_medesc']}")
                print(f"     NEW: {change['new_medesc']}")
                print()
            if len(changes['medesc_changes']) > 20:
                print(f"  ... and {len(changes['medesc_changes']) - 20} more")
            print()
        else:
            print("✓ No MEDESC changes")
            print()
        
        # Barcode changes
        if changes['barcode_changes']:
            print(f"📊 NEW BARCODES: {len(changes['barcode_changes']):,}")
            print()
            for change in changes['barcode_changes'][:20]:
                primary_marker = " [PRIMARY]" if change['barcode'] == change.get('primary_barcode') else ""
                print(f"  + {change['merkey']:10s} {change['barcode']}{primary_marker}")
                if change.get('medesc'):
                    print(f"     {change['medesc'][:60]}")
            if len(changes['barcode_changes']) > 20:
                print(f"  ... and {len(changes['barcode_changes']) - 20} more")
        else:
            print("✓ No new barcodes")
        if changes['class_updates']:
            print(f"🧭 CLASS HIERARCHY UPDATES: {changes['class_updates']:,}")

        print()
        
        # Save detailed change log
        log_file = f"sync_changes_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(log_file, 'w', encoding='utf-8') as f:
            f.write("=" * 80 + "\n")
            f.write("MP_MER SYNC - DETAILED CHANGE LOG\n")
            f.write("=" * 80 + "\n")
            f.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Source: {mp_mer_path}\n")
            f.write("\n")
            
            f.write("NEW PRODUCTS\n")
            f.write("-" * 80 + "\n")
            for prod in changes['new_products']:
                barcode_summary = ", ".join(prod.get('barcodes') or [])
                primary_summary = prod.get('primary_barcode') or "-"
                if prod.get('markup_pct') is not None:
                    f.write(
                        f"{prod['merkey']}\t{prod['medesc']}\t₱{prod['price']:.2f}"
                        f"\tUnit Cost ₱{prod.get('unit_cost', 0):.2f}\tMarkup {prod['markup_pct']:+.1f}%\n"
                    )
                else:
                    f.write(
                        f"{prod['merkey']}\t{prod['medesc']}\t₱{prod['price']:.2f}"
                        f"\tUnit Cost n/a\tMarkup n/a\n"
                    )
                f.write(f"  PRIMARY BARCODE: {primary_summary}\n")
                if barcode_summary:
                    f.write(f"  ALL BARCODES: {barcode_summary}\n")
            f.write("\n")
            
            f.write("PRICE CHANGES (APPLIED)\n")
            f.write("-" * 80 + "\n")
            for change in changes['price_changes']:
                line = (
                    f"{change['merkey']}\t{change['medesc']}\t"
                    f"₱{change['old_price']:.2f} → ₱{change['new_price']:.2f}\t"
                    f"{change['pct_change']:+.1f}%"
                )
                if change.get('markup_pct') is not None:
                    line += (
                        f"\tUnit Cost ₱{change.get('unit_cost', 0):.2f}"
                        f"\tCurrent Markup {change['markup_pct']:+.1f}%"
                    )
                else:
                    line += "\tUnit Cost n/a\tCurrent Markup n/a"
                f.write(line + "\n")
            f.write("\n")

            f.write("PRICE ANOMALIES (QUARANTINED - NOT APPLIED)\n")
            f.write("-" * 80 + "\n")
            for anomaly in changes['price_anomalies']:
                old_p = anomaly.get('old_price', 0)
                new_p = anomaly.get('new_price', 0)
                src = anomaly.get('source', 'unknown')
                if old_p > 0:
                    pct = ((new_p - old_p) / old_p) * 100.0
                    price_str = f"₱{old_p:.2f} → ₱{new_p:.2f}\t{pct:+.1f}%"
                else:
                    price_str = f"₱{new_p:.2f}\t(new product)"
                line = f"{anomaly['merkey']}\t{anomaly['medesc']}\t{price_str}"
                if anomaly.get('case_price', 0) > 0:
                    line += f"\tCase ₱{anomaly['case_price']:.2f}"
                if anomaly.get('pack_price', 0) > 0:
                    line += f"\tPack ₱{anomaly['pack_price']:.2f}"
                if anomaly.get('unit_cost', 0) > 0:
                    line += f"\tUnit Cost ₱{anomaly['unit_cost']:.2f}"
                f.write(line + "\n")
                for w in anomaly.get('warnings', []):
                    f.write(f"  WARNING: {w}\n")
            f.write("\n")

            f.write("MEDESC CHANGES (REVIEW REQUIRED)\n")
            f.write("-" * 80 + "\n")
            for change in changes['medesc_changes']:
                f.write(f"{change['merkey']}\n")
                f.write(f"  OLD: {change['old_medesc']}\n")
                f.write(f"  NEW: {change['new_medesc']}\n")
                f.write("\n")

            f.write("BARCODE CHANGES\n")
            f.write("-" * 80 + "\n")
            for change in changes['barcode_changes']:
                primary_marker = " [PRIMARY]" if change['barcode'] == change.get('primary_barcode') else ""
                f.write(f"{change['merkey']}\t{change['barcode']}{primary_marker}\n")
                if change.get('medesc'):
                    f.write(f"  {change['medesc']}\n")
            f.write("\n")
        
        print(f"✓ Detailed log saved: {log_file}")
        print()
        
        conn.close()
        
        print("=" * 80)
        print("SYNC COMPLETE")
        print("=" * 80)
        print(f"Completed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        if changes['prices_quarantined']:
            print(
                f"⚠ {changes['prices_quarantined']:,} price change(s) quarantined — "
                f"review anomalies in {log_file}"
            )
        print()

        return True
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        
        cursor.execute("""
            UPDATE sync_log SET
                status = 'FAILED',
                error_message = ?,
                completed_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (str(e), sync_id))
        conn.commit()
        conn.close()
        
        return False


def main():
    """Main execution"""
    # Avoid Windows console crashes on Unicode symbols in logs.
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

    import argparse

    parser = argparse.ArgumentParser(
        description='Sync MP_MER.FPB into SQLite with change detection.'
    )
    parser.add_argument('mp_mer_path', help='Path to MP_MER.FPB')
    parser.add_argument(
        '--db',
        default='anson_products.db',
        help='Path to SQLite database (default: anson_products.db)',
    )
    parser.add_argument(
        '--mp-sup',
        default='',
        help='Optional path to MP_SUP.FPB for supplier names',
    )
    parser.add_argument(
        '--mp-cls',
        default='',
        help='Optional path to MP_CLS.FPB for class hierarchy',
    )
    args = parser.parse_args()

    success = sync_mp_mer(args.db, args.mp_mer_path, args.mp_sup, args.mp_cls)
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
