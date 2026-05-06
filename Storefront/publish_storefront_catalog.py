from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import struct
from datetime import date, datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
SOURCE_DB = BASE_DIR.parent / 'SQLite' / 'anson_products.db'
TARGET_DB = BASE_DIR / 'storefront_catalog.db'
SCHEMA_PATH = BASE_DIR / 'schema.sql'
OVERRIDES_PATH = BASE_DIR / 'storefront_category_overrides.csv'
FRESH_DISPLAY_OVERRIDES_PATH = BASE_DIR / 'storefront_fresh_display_overrides.csv'
DEFAULT_WI_ESC_CANDIDATES = [
    Path('/mnt/ssims/SSIMS/WI_ESC.FPB'),
    Path('/mnt/ssims/SSIMS/WI_ESC.FPB'),
]
DEFAULT_WI_SDR_CANDIDATES = [
    Path('/mnt/ssims/SSIMS/WI_SDR.FPB'),
    Path('/mnt/ssims/SSIMS/WI_SDR.FPB'),
]
DEFAULT_FE_T_DIR_CANDIDATES = [
    Path('/mnt/ssims') / str(datetime.now().year),
    Path('D:\Projects\new ssims'),
]
SALES_LOOKBACK_DAYS = 730
MIN_VEGETABLE_SALES_SAMPLES = 8
MIN_MEAT_SALES_SAMPLES = 8
MAX_MEAT_FE_T_FILES = 12
DEFAULT_FRESH_ACCEPTANCE_DAYS = 14
FRESH_ACCEPTANCE_WINDOWS = {
    'ES.FM.POULTRY': 7,
    'ES.MP.FISH': 7,
    'ES.AP.VEGETABLES': 10,
    'ES.AP.FRUITS': 10,
    'ES.FM.BEEF': 14,
    'ES.FM.PORK': 14,
}
PLACEHOLDER_IMAGE = (
    'https://s3-ap-southeast-1.amazonaws.com/ansonsupermart.com/images/'
    'ANSON-ONLINE-GROCERY-PLACEHOLDER.jpg'
)
# Quality gate thresholds
QUALITY_GATE_MIN_PRICE = 0.01
QUALITY_GATE_MAX_PRICE = 50000
SALES_RECENCY_DAYS = 365
INVALID_DEPARTMENTS = {'UNCATEGORIZED', 'UNKNOWN', 'OTHERS', 'NONE', ''}

GRAM_PATTERN = re.compile(r"\b(\d+(?:\.\d+)?)\s*(KG|G)\b", re.I)
PIECE_SIZE_PATTERN = re.compile(r"^\s*(\d+)\s*S\s*$", re.I)
PIECE_MEDESC_PATTERN = re.compile(r"\b(\d+)\s*'\s*S?\s*$", re.I)
EGG_WORD_PATTERN = re.compile(r"\bEGGS?\b", re.I)

MEAT_CLASSES = {'ES.FM.PORK', 'ES.FM.BEEF', 'ES.FM.POULTRY'}
FISH_CLASSES = {'ES.MP.FISH'}
PRODUCE_CLASSES = {'ES.AP.VEGETABLES', 'ES.AP.FRUITS'}
MASKED_NAME_PATTERN = re.compile(r"\*{5,}")
FRUITS_GARBAGE_PATTERN = re.compile(r"^FRUITS\.[/\.*\s-]+[A-Z0-9/\.*\s-]*$", re.I)
GARBAGE_MARK_PATTERN = re.compile(r"[/\*]{5,}")
FRESH_GARBAGE_CLASSES = MEAT_CLASSES | FISH_CLASSES | PRODUCE_CLASSES


def slugify(value: str | None) -> str:
    import re
    text = (value or '').strip().lower()
    text = re.sub(r'[^a-z0-9]+', '-', text)
    return text.strip('-') or 'item'


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(target_conn: sqlite3.Connection) -> None:
    target_conn.executescript(SCHEMA_PATH.read_text(encoding='utf-8'))
    existing_columns = {
        row['name']
        for row in target_conn.execute("PRAGMA table_info(products)").fetchall()
    }
    required_columns = {
        'needs_irl_photo': 'ALTER TABLE products ADD COLUMN needs_irl_photo INTEGER NOT NULL DEFAULT 0',
        'last_acceptance_date': 'ALTER TABLE products ADD COLUMN last_acceptance_date TEXT',
        'sellable_state': 'ALTER TABLE products ADD COLUMN sellable_state TEXT',
        'sellable_note': 'ALTER TABLE products ADD COLUMN sellable_note TEXT',
        'fulfillment_type': 'ALTER TABLE products ADD COLUMN fulfillment_type TEXT',
        'order_unit_label': 'ALTER TABLE products ADD COLUMN order_unit_label TEXT',
        'substitution_policy': 'ALTER TABLE products ADD COLUMN substitution_policy TEXT',
        'fulfillment_note': 'ALTER TABLE products ADD COLUMN fulfillment_note TEXT',
        'pricing_basis': 'ALTER TABLE products ADD COLUMN pricing_basis TEXT',
        'min_weight_g': 'ALTER TABLE products ADD COLUMN min_weight_g INTEGER',
        'max_weight_g': 'ALTER TABLE products ADD COLUMN max_weight_g INTEGER',
        'display_weight_g': 'ALTER TABLE products ADD COLUMN display_weight_g INTEGER',
        'display_price': 'ALTER TABLE products ADD COLUMN display_price REAL',
        'range_label': 'ALTER TABLE products ADD COLUMN range_label TEXT',
        'stock_status': "ALTER TABLE products ADD COLUMN stock_status TEXT NOT NULL DEFAULT 'in_stock'",
        'show_pack_on_storefront': 'ALTER TABLE products ADD COLUMN show_pack_on_storefront INTEGER NOT NULL DEFAULT 0',
        'pack_display_label': 'ALTER TABLE products ADD COLUMN pack_display_label TEXT',
        'pack_photo_url': 'ALTER TABLE products ADD COLUMN pack_photo_url TEXT',
        'pack_barcode': 'ALTER TABLE products ADD COLUMN pack_barcode TEXT',
    }
    for column_name, statement in required_columns.items():
        if column_name not in existing_columns:
            target_conn.execute(statement)
    target_conn.commit()


def ensure_source_schema(source_conn: sqlite3.Connection) -> None:
    existing_columns = {
        row['name']
        for row in source_conn.execute("PRAGMA table_info(products)").fetchall()
    }
    if 'availability_override' not in existing_columns:
        source_conn.execute("ALTER TABLE products ADD COLUMN availability_override TEXT DEFAULT 'AUTO'")
    if 'show_pack_on_storefront' not in existing_columns:
        source_conn.execute("ALTER TABLE products ADD COLUMN show_pack_on_storefront INTEGER DEFAULT 0")
    if 'pack_display_label' not in existing_columns:
        source_conn.execute("ALTER TABLE products ADD COLUMN pack_display_label TEXT")
    if 'pack_photo_url' not in existing_columns:
        source_conn.execute("ALTER TABLE products ADD COLUMN pack_photo_url TEXT")
    if 'pack_barcode' not in existing_columns:
        source_conn.execute("ALTER TABLE products ADD COLUMN pack_barcode TEXT")
    source_conn.commit()


def read_dbf_file(filepath: Path) -> list[dict[str, str]]:
    with filepath.open('rb') as handle:
        header = handle.read(32)
        num_records = struct.unpack('<I', header[4:8])[0]
        header_length = struct.unpack('<H', header[8:10])[0]
        record_length = struct.unpack('<H', header[10:12])[0]

        handle.seek(32)
        field_positions: dict[str, tuple[int, int, str]] = {}
        current_pos = 1
        while True:
            field_desc = handle.read(32)
            if field_desc[0] == 0x0D:
                break
            field_name = field_desc[0:11].split(b'\x00')[0].decode('ascii', errors='ignore').strip()
            field_type = chr(field_desc[11])
            field_length = field_desc[16]
            field_positions[field_name] = (current_pos, field_length, field_type)
            current_pos += field_length

        handle.seek(header_length)
        records: list[dict[str, str]] = []
        for _ in range(num_records):
            record_data = handle.read(record_length)
            if not record_data or len(record_data) < record_length:
                break
            if record_data[0] == 0x2A:
                continue
            record: dict[str, str] = {}
            for field_name, (pos, length, field_type) in field_positions.items():
                field_data = record_data[pos:pos + length]
                codec = 'ascii' if field_type in {'N', 'D'} else 'latin-1'
                record[field_name] = field_data.decode(codec, errors='ignore').strip()
            records.append(record)
        return records


def parse_dbf_date(raw: str | None) -> date | None:
    value = (raw or '').strip()
    if len(value) != 8 or not value.isdigit():
        return None
    try:
        return datetime.strptime(value, '%Y%m%d').date()
    except ValueError:
        return None


def resolve_wi_esc_path(candidate: str | None) -> Path | None:
    if candidate:
        path = Path(candidate)
        return path if path.exists() else None
    for path in DEFAULT_WI_ESC_CANDIDATES:
        if path.exists():
            return path
    return None


def resolve_wi_sdr_path(candidate: str | None) -> Path | None:
    if candidate:
        path = Path(candidate)
        return path if path.exists() else None
    for path in DEFAULT_WI_SDR_CANDIDATES:
        if path.exists():
            return path
    return None


def dbf_record_count(path: Path) -> int:
    with path.open('rb') as handle:
        header = handle.read(32)
    return struct.unpack('<I', header[4:8])[0]


def resolve_fe_t_paths(candidate: str | None) -> list[Path]:
    if candidate:
        path = Path(candidate)
        return [path] if path.exists() else []

    resolved: list[Path] = []
    seen: set[Path] = set()
    for directory in DEFAULT_FE_T_DIR_CANDIDATES:
        if not directory.exists():
            continue
        monthly_files = sorted(
            [
                path
                for path in directory.iterdir()
                if path.is_file() and re.fullmatch(r'FE_T\d{2}\.(?:FPB|fpb)', path.name)
            ],
            key=lambda path: path.name.upper(),
            reverse=True,
        )
        for path in monthly_files:
            if path in seen:
                continue
            if dbf_record_count(path) > 0:
                resolved.append(path)
                seen.add(path)
    return resolved


def load_last_acceptance_dates(wi_esc_path: Path | None) -> tuple[dict[str, str], date | None, set[tuple[str, str]]]:
    if wi_esc_path is None:
        return {}, None, set()

    last_acceptance_by_merkey: dict[str, date] = {}
    max_acceptance_date: date | None = None
    accepted_merkey_dates: set[tuple[str, str]] = set()
    for record in read_dbf_file(wi_esc_path):
        if (record.get('STATUS') or '').strip() == '*':
            continue
        if (record.get('TREFDC') or '').strip().upper() != 'AR-':
            continue
        merkey = (record.get('MERKEY') or '').strip()
        acceptance_date = parse_dbf_date(record.get('TRDATE'))
        if not merkey or acceptance_date is None:
            continue
        current = last_acceptance_by_merkey.get(merkey)
        if current is None or acceptance_date > current:
            last_acceptance_by_merkey[merkey] = acceptance_date
        if max_acceptance_date is None or acceptance_date > max_acceptance_date:
            max_acceptance_date = acceptance_date
        accepted_merkey_dates.add((merkey, acceptance_date.isoformat()))

    return (
        {merkey: accepted_at.isoformat() for merkey, accepted_at in last_acceptance_by_merkey.items()},
        max_acceptance_date,
        accepted_merkey_dates,
    )


def load_last_delivery_dates(
    wi_sdr_path: Path | None,
    accepted_merkey_dates: set[tuple[str, str]],
) -> tuple[dict[str, str], date | None]:
    if wi_sdr_path is None:
        return {}, None

    last_delivery_by_merkey: dict[str, date] = {}
    max_delivery_date: date | None = None
    for record in read_dbf_file(wi_sdr_path):
        if (record.get('STATUS') or '').strip() == '*':
            continue
        if (record.get('TRETYP') or '').strip().upper() != 'RE':
            continue
        # Posted SDR rows are already represented in WI_ESC stock-card receipts.
        if (record.get('STATUS') or '').strip().upper() == 'P':
            continue
        merkey = (record.get('MERKEY') or '').strip()
        delivery_date = parse_dbf_date(record.get('TRDATE'))
        if not merkey or delivery_date is None:
            continue
        merkey_date = (merkey, delivery_date.isoformat())
        if merkey_date in accepted_merkey_dates:
            continue
        current = last_delivery_by_merkey.get(merkey)
        if current is None or delivery_date > current:
            last_delivery_by_merkey[merkey] = delivery_date
        if max_delivery_date is None or delivery_date > max_delivery_date:
            max_delivery_date = delivery_date

    return (
        {merkey: delivered_at.isoformat() for merkey, delivered_at in last_delivery_by_merkey.items()},
        max_delivery_date,
    )


def later_iso_date(first: str | None, second: str | None) -> str | None:
    if not first:
        return second
    if not second:
        return first
    try:
        first_dt = datetime.strptime(first, '%Y-%m-%d').date()
        second_dt = datetime.strptime(second, '%Y-%m-%d').date()
    except ValueError:
        return first or second
    return first if first_dt >= second_dt else second


def max_date_value(first: date | None, second: date | None) -> date | None:
    if first is None:
        return second
    if second is None:
        return first
    return first if first >= second else second


def get_fresh_acceptance_window_days(row: sqlite3.Row, default_days: int) -> int:
    class_l3 = (row['class_l3_name'] or '').strip().upper()
    return FRESH_ACCEPTANCE_WINDOWS.get(class_l3, default_days)


def has_recent_acceptance(
    last_acceptance_date: str | None,
    max_acceptance_date: date | None,
    window_days: int,
) -> bool:
    if not last_acceptance_date or max_acceptance_date is None:
        return False
    try:
        accepted_at = datetime.strptime(last_acceptance_date, '%Y-%m-%d').date()
    except ValueError:
        return False
    cutoff_date = max_acceptance_date - timedelta(days=max(window_days - 1, 0))
    return accepted_at >= cutoff_date


def determine_sellable_state(
    row: sqlite3.Row,
    fresh_display: dict[str, object] | None,
) -> tuple[str, str]:
    photo_url = (row['photo_url'] or '').strip()
    is_placeholder_photo = PLACEHOLDER_IMAGE in photo_url
    needs_irl_photo = bool(row['needs_irl_photo'])

    if fresh_display and fresh_display.get('pricing_basis') == 'per_kg':
        return 'review_required', 'Variable-weight fresh item. Final packed weight and total are confirmed at fulfillment.'
    if needs_irl_photo or is_placeholder_photo:
        return 'browse_only', 'Visible in the catalog, but held from direct ordering until merchandising is finalized.'
    return 'orderable', 'Published for ordering.'


def determine_fulfillment_profile(
    row: sqlite3.Row,
    fresh_display: dict[str, object] | None,
    sellable_state: str,
    sellable_note: str,
) -> dict[str, str | None]:
    pricing_basis = (fresh_display or {}).get('pricing_basis')
    range_label = ((fresh_display or {}).get('range_label') or '').strip()
    size = (row['size'] or '').strip()
    base_unit_label = range_label or size or None

    if sellable_state == 'browse_only':
        return {
            'fulfillment_type': 'catalog_only',
            'order_unit_label': base_unit_label,
            'substitution_policy': 'not_applicable',
            'fulfillment_note': sellable_note,
        }

    if pricing_basis == 'per_kg':
        return {
            'fulfillment_type': 'variable_weight',
            'order_unit_label': base_unit_label or 'Estimated packed weight',
            'substitution_policy': 'confirm_at_fulfillment',
            'fulfillment_note': 'Priced per kilo. Final packed weight and total are confirmed during fulfillment.',
        }

    if pricing_basis == 'fixed_pack':
        return {
            'fulfillment_type': 'fixed_pack',
            'order_unit_label': base_unit_label or 'Pack',
            'substitution_policy': 'fixed_pack',
            'fulfillment_note': 'Sold as a fixed pack using the published pack price.',
        }

    return {
        'fulfillment_type': 'standard',
        'order_unit_label': base_unit_label,
        'substitution_policy': 'standard',
        'fulfillment_note': 'Sold using the published shelf price.',
    }


def has_inventory_snapshot(source_conn: sqlite3.Connection) -> bool:
    try:
        row = source_conn.execute("SELECT COUNT(*) AS c FROM inventory").fetchone()
    except sqlite3.OperationalError:
        return False
    return bool(row and row["c"])


def load_recent_sales_quantity_averages(
    wi_esc_path: Path | None,
    lookback_days: int,
) -> dict[str, dict[str, float | int]]:
    if wi_esc_path is None:
        return {}

    max_sale_date: date | None = None
    sales_rows: list[tuple[str, date, float]] = []
    for record in read_dbf_file(wi_esc_path):
        if (record.get('STATUS') or '').strip() == '*':
            continue
        if (record.get('TRETYP') or '').strip().upper() != 'RT':
            continue
        if (record.get('TREFDC') or '').strip().upper() != 'SS-':
            continue

        merkey = (record.get('MERKEY') or '').strip()
        sale_date = parse_dbf_date(record.get('TRDATE'))
        raw_qty = (record.get('TRDRQT') or record.get('TRQUAN') or '').strip()
        try:
            qty = float(raw_qty or 0)
        except ValueError:
            qty = 0.0
        if not merkey or sale_date is None or qty <= 0:
            continue

        sales_rows.append((merkey, sale_date, qty))
        if max_sale_date is None or sale_date > max_sale_date:
            max_sale_date = sale_date

    if max_sale_date is None:
        return {}

    cutoff_date = max_sale_date - timedelta(days=max(lookback_days - 1, 0))
    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    for merkey, sale_date, qty in sales_rows:
        if sale_date < cutoff_date:
            continue
        totals[merkey] = totals.get(merkey, 0.0) + qty
        counts[merkey] = counts.get(merkey, 0) + 1

    return {
        merkey: {
            'avg_qty': totals[merkey] / counts[merkey],
            'sample_count': counts[merkey],
        }
        for merkey in totals
        if counts[merkey] > 0
    }


def load_monthly_sales_quantity_averages(fe_t_path: Path | None) -> dict[str, dict[str, float | int]]:
    if fe_t_path is None:
        return {}

    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    for record in read_dbf_file(fe_t_path):
        if (record.get('STATUS') or '').strip() == '*':
            continue

        merkey = (record.get('MERKEY') or '').strip()
        raw_qty = (record.get('TRQUAN') or '').strip()
        try:
            qty = float(raw_qty or 0)
        except ValueError:
            qty = 0.0
        if not merkey or qty <= 0:
            continue

        totals[merkey] = totals.get(merkey, 0.0) + qty
        counts[merkey] = counts.get(merkey, 0) + 1

    return {
        merkey: {
            'avg_qty': totals[merkey] / counts[merkey],
            'sample_count': counts[merkey],
        }
        for merkey in totals
        if counts[merkey] > 0
    }


def load_meat_sales_quantity_averages(fe_t_paths: list[Path]) -> dict[str, dict[str, float | int]]:
    if not fe_t_paths:
        return {}

    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    for fe_t_path in fe_t_paths[:MAX_MEAT_FE_T_FILES]:
        pending = {merkey for merkey, count in counts.items() if count < MIN_MEAT_SALES_SAMPLES}
        if counts and not pending:
            break

        for record in read_dbf_file(fe_t_path):
            if (record.get('STATUS') or '').strip() == '*':
                continue

            merkey = (record.get('MERKEY') or '').strip()
            if counts.get(merkey, 0) >= MIN_MEAT_SALES_SAMPLES:
                continue

            raw_qty = (record.get('TRQUAN') or '').strip()
            try:
                qty = float(raw_qty or 0)
            except ValueError:
                qty = 0.0
            if not merkey or qty <= 0:
                continue

            totals[merkey] = totals.get(merkey, 0.0) + qty
            counts[merkey] = counts.get(merkey, 0) + 1

    return {
        merkey: {
            'avg_qty': totals[merkey] / counts[merkey],
            'sample_count': counts[merkey],
        }
        for merkey in totals
        if counts[merkey] > 0
    }


def load_category_overrides() -> dict[str, dict[str, str]]:
    if not OVERRIDES_PATH.exists():
        return {}
    with OVERRIDES_PATH.open('r', encoding='utf-8-sig', newline='') as handle:
        rows = list(csv.DictReader(handle))
    return {
        (row.get('merkey') or '').strip(): {
            'department_name': (row.get('target_department') or '').strip(),
            'category_name': (row.get('target_category') or '').strip(),
        }
        for row in rows
        if (row.get('merkey') or '').strip()
    }


def load_fresh_display_overrides() -> dict[str, dict[str, object]]:
    if not FRESH_DISPLAY_OVERRIDES_PATH.exists():
        return {}

    overrides: dict[str, dict[str, object]] = {}

    def parse_int(value: str | None) -> int | None:
        text = (value or '').strip()
        if not text:
            return None
        try:
            return int(float(text))
        except ValueError:
            return None

    with FRESH_DISPLAY_OVERRIDES_PATH.open('r', encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            merkey = (row.get('merkey') or '').strip()
            pricing_basis = (row.get('pricing_basis') or '').strip()
            if not merkey or not pricing_basis:
                continue
            overrides[merkey] = {
                'pricing_basis': pricing_basis,
                'min_weight_g': parse_int(row.get('min_weight_g')),
                'max_weight_g': parse_int(row.get('max_weight_g')),
                'display_weight_g': parse_int(row.get('display_weight_g')),
                'range_label': (row.get('range_label') or '').strip() or None,
            }

    return overrides


def parse_size_grams(text: str | None) -> int | None:
    if not text:
        return None
    match = GRAM_PATTERN.search(text.upper())
    if not match:
        return None
    quantity = float(match.group(1))
    unit = match.group(2).upper()
    if unit == 'KG':
        quantity *= 1000
    return int(round(quantity))


def parse_piece_pack_count(size: str | None, source_medesc: str | None) -> int | None:
    size_text = (size or '').strip().upper()
    if size_text:
        match = PIECE_SIZE_PATTERN.match(size_text.replace(' ', ''))
        if match:
            return int(match.group(1))

    medesc_text = (source_medesc or '').strip().upper()
    if medesc_text:
        match = PIECE_MEDESC_PATTERN.search(medesc_text)
        if match:
            return int(match.group(1))
    return None


def is_masked_storefront_item(row: sqlite3.Row) -> bool:
    fields = [
        row['name'],
        row['description'],
        row['source_medesc'],
    ]
    return any(MASKED_NAME_PATTERN.search((value or '').strip()) for value in fields)


def is_unusual_storefront_item(row: sqlite3.Row) -> bool:
    name = (row['name'] or '').strip().upper()
    description = (row['description'] or '').strip().upper()
    source_medesc = (row['source_medesc'] or '').strip().upper()
    supplier_name = (row['supplier_name'] or '').strip().upper()
    class_l3 = (row['class_l3_name'] or '').strip().upper()

    if name in {'FRUITS..', 'FRUITS..********'} or description in {'FRUITS..', 'FRUITS..********/100'}:
        return True
    if (
        supplier_name in {'-**', '---', '---*'}
        and (
            FRUITS_GARBAGE_PATTERN.match(name)
            or FRUITS_GARBAGE_PATTERN.match(description)
            or FRUITS_GARBAGE_PATTERN.match(source_medesc)
        )
    ):
        return True
    if source_medesc.startswith('FRUITS..') and class_l3 == 'ES.OVER THE COUNTER MEDICINE':
        return True
    if supplier_name in {'-**', '---', '---*'} and class_l3 == 'ES.OVER THE COUNTER MEDICINE':
        return True
    if (
        class_l3 in FRESH_GARBAGE_CLASSES
        and (
            supplier_name in {'-**', '---', '---*'}
            or GARBAGE_MARK_PATTERN.search(name)
            or GARBAGE_MARK_PATTERN.search(description)
            or GARBAGE_MARK_PATTERN.search(source_medesc)
        )
        and (
            GARBAGE_MARK_PATTERN.search(name)
            or GARBAGE_MARK_PATTERN.search(description)
            or GARBAGE_MARK_PATTERN.search(source_medesc)
        )
    ):
        return True
    return False


def check_has_image(row: sqlite3.Row) -> str | None:
    photo_url = (row['photo_url'] or '').strip()
    if not photo_url or photo_url == PLACEHOLDER_IMAGE:
        return 'No product image'
    return None


def check_sales_recency(row: sqlite3.Row, is_fresh: bool) -> str | None:
    if is_fresh:
        return None
    last_sale = (row['last_sale_date'] or '').strip()
    if not last_sale:
        return 'No recorded sales'
    try:
        sale_date = date.fromisoformat(last_sale)
    except ValueError:
        return f'Invalid last_sale_date: {last_sale}'
    cutoff = date.today() - timedelta(days=SALES_RECENCY_DAYS)
    if sale_date < cutoff:
        return f'Last sale {last_sale} (>{SALES_RECENCY_DAYS} days ago)'
    return None


def check_valid_department(dept_name: str) -> str | None:
    normalized = dept_name.strip().upper()
    if normalized in INVALID_DEPARTMENTS:
        return f'Department: {dept_name!r}'
    return None


def check_price_sanity(row: sqlite3.Row) -> str | None:
    price = row['price_retail']
    if price is None:
        return 'No retail price'
    price = float(price)
    if price < QUALITY_GATE_MIN_PRICE:
        return f'Price too low: {price:.2f}'
    if price > QUALITY_GATE_MAX_PRICE:
        return f'Price too high: {price:.2f}'
    return None


GATE_LABELS = {
    'no_image': 'NO IMAGE',
    'no_recent_sales': 'NO RECENT SALES',
    'invalid_department': 'INVALID DEPARTMENT',
    'price_out_of_range': 'PRICE OUT OF RANGE',
}


def write_exclusion_report(
    exclusion_records: list[dict],
    exclusion_counts: dict[str, int],
    output_dir: Path,
) -> Path | None:
    total = len(exclusion_records)
    if total == 0:
        return None
    now = datetime.now()
    filename = f"publish_exclusions_{now.strftime('%Y%m%d_%H%M%S')}.txt"
    report_path = output_dir / filename

    lines: list[str] = []
    lines.append('=' * 80)
    lines.append('PUBLISH QUALITY GATE EXCLUSION REPORT')
    lines.append('=' * 80)
    lines.append(f"Date: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f'Total excluded: {total}')
    lines.append('')
    lines.append('SUMMARY')
    lines.append('-' * 80)
    for key, label in GATE_LABELS.items():
        count = exclusion_counts.get(key, 0)
        lines.append(f'  {label + ":":<25s} {count}')
    lines.append('(Products may appear in multiple sections if they fail multiple gates)')
    lines.append('')

    for gate_key, gate_label in GATE_LABELS.items():
        items = [r for r in exclusion_records if any(k == gate_key for k, _ in r['reasons'])]
        if not items:
            continue
        lines.append(f'EXCLUDED FOR: {gate_label} ({len(items)} items)')
        lines.append('-' * 80)
        for rec in items:
            reason_msg = next((msg for k, msg in rec['reasons'] if k == gate_key), '')
            lines.append(f"  {rec['merkey']:<10s} {rec['name']:<40s} {reason_msg}")
        lines.append('')

    report_path.write_text('\n'.join(lines), encoding='utf-8')
    return report_path


def classify_fresh_display(
    row: sqlite3.Row,
    sales_quantity_averages: dict[str, dict[str, float | int]] | None = None,
    meat_sales_quantity_averages: dict[str, dict[str, float | int]] | None = None,
    fresh_display_overrides: dict[str, dict[str, object]] | None = None,
) -> dict[str, object] | None:
    class_l2 = (row['class_l2_name'] or '').strip().upper()
    class_l3 = (row['class_l3_name'] or '').strip().upper()
    name = (row['name'] or '').strip().upper()
    description = (row['description'] or '').strip().upper()
    source_medesc = (row['source_medesc'] or '').strip().upper()
    category_name = (row['category_name'] or '').strip().upper()
    price_retail = row['price_retail']
    size_grams = parse_size_grams(row['size'])
    piece_pack_count = parse_piece_pack_count(row['size'], row['source_medesc'] or row['description'])
    is_kiwi = 'KIWI' in name or 'KIWI' in description or 'KIWI' in source_medesc
    is_explicit_pack = 'PACK' in description or 'PACK' in source_medesc
    is_egg_pack = (
        category_name == 'EGGS'
        or bool(EGG_WORD_PATTERN.search(name))
        or bool(EGG_WORD_PATTERN.search(description))
        or bool(EGG_WORD_PATTERN.search(source_medesc))
    )
    is_mushroom_pack = class_l3 == 'ES.AP.VEGETABLES' and 'MUSHROOM' in (name + ' ' + description + ' ' + source_medesc) and size_grams is not None
    is_miso_pack = class_l3 == 'ES.AP.VEGETABLES' and 'MISO' in (name + ' ' + description + ' ' + source_medesc)
    brand_name = (row['brand_name'] or '').strip().upper()
    unit_of_measurement = (row['unit_of_measurement'] or '').strip().upper()
    is_meat_sales_driven = (
        brand_name in {'MONTEREY', 'MAGNOLIA', 'MAGNOLIA CHICKEN STATION'}
        and unit_of_measurement == 'KG'
        and class_l3 in MEAT_CLASSES
    )

    if price_retail is None:
        return None

    merkey = (row['merkey'] or '').strip()
    override = (fresh_display_overrides or {}).get(merkey)
    if override:
        pricing_basis = str(override.get('pricing_basis') or '').strip()
        if pricing_basis == 'fixed_pack':
            return {
                'pricing_basis': 'fixed_pack',
                'min_weight_g': None,
                'max_weight_g': None,
                'display_weight_g': None,
                'display_price': float(price_retail),
                'range_label': override.get('range_label') or row['size'] or None,
            }
        if pricing_basis == 'per_kg':
            min_weight_g = override.get('min_weight_g')
            max_weight_g = override.get('max_weight_g')
            display_weight_g = override.get('display_weight_g') or max_weight_g or min_weight_g
            range_label = override.get('range_label')
            if not range_label:
                if min_weight_g and max_weight_g and min_weight_g != max_weight_g:
                    range_label = f"{min_weight_g}G-{max_weight_g}G"
                elif display_weight_g:
                    range_label = f"{display_weight_g}G"
            return {
                'pricing_basis': 'per_kg',
                'min_weight_g': min_weight_g,
                'max_weight_g': max_weight_g,
                'display_weight_g': display_weight_g,
                'display_price': round(float(price_retail) * (display_weight_g / 1000.0), 2) if display_weight_g else None,
                'range_label': range_label,
            }

    pricing_basis = None
    min_weight_g = None
    max_weight_g = None
    display_weight_g = None
    sales_stats = (sales_quantity_averages or {}).get((row['merkey'] or '').strip())
    meat_sales_stats = (meat_sales_quantity_averages or {}).get((row['merkey'] or '').strip())

    if is_egg_pack:
        pricing_basis = 'fixed_pack'
    elif class_l3 in PRODUCE_CLASSES and is_explicit_pack:
        pricing_basis = 'fixed_pack'
    elif is_mushroom_pack:
        pricing_basis = 'fixed_pack'
    elif is_miso_pack:
        pricing_basis = 'fixed_pack'
    elif class_l3 in PRODUCE_CLASSES and is_kiwi:
        pricing_basis = 'fixed_pack'
        piece_pack_count = piece_pack_count or 1
    elif class_l3 in PRODUCE_CLASSES and piece_pack_count and piece_pack_count > 1:
        pricing_basis = 'fixed_pack'
    elif class_l3 in MEAT_CLASSES or class_l2 == 'ES.FRESH MEAT SECTION':
        pricing_basis = 'per_kg'
        if is_meat_sales_driven and meat_sales_stats and int(meat_sales_stats.get('sample_count', 0)) >= MIN_MEAT_SALES_SAMPLES:
            average_weight_g = int(round(float(meat_sales_stats['avg_qty']) * 1000))
            display_weight_g = max(100, int(((average_weight_g + 99) // 100) * 100))
            min_weight_g = max(100, display_weight_g - 100)
            max_weight_g = display_weight_g
        else:
            min_weight_g = 500
            max_weight_g = 550
            display_weight_g = 500
    elif class_l3 in FISH_CLASSES or class_l2 == 'ES.MARINE & FRESH WTR PRODUCTS':
        pricing_basis = 'per_kg'
        min_weight_g = 600
        max_weight_g = 750
        display_weight_g = 750
    elif class_l3 in PRODUCE_CLASSES:
        pricing_basis = 'per_kg'
        if class_l3 == 'ES.AP.VEGETABLES' and sales_stats and int(sales_stats.get('sample_count', 0)) >= MIN_VEGETABLE_SALES_SAMPLES:
            average_weight_g = int(round(float(sales_stats['avg_qty']) * 1000))
            display_weight_g = max(100, int(((average_weight_g + 99) // 100) * 100))
            min_weight_g = max(100, display_weight_g - 100)
            max_weight_g = display_weight_g
        else:
            min_weight_g = 350
            max_weight_g = 400
            display_weight_g = 400

    if pricing_basis is None:
        return None

    if pricing_basis == 'fixed_pack':
        if piece_pack_count:
            unit_label = 'pc' if piece_pack_count == 1 else 'pcs'
            range_label = f"{piece_pack_count} {unit_label}"
        elif size_grams:
            range_label = f"{size_grams}G"
        elif is_miso_pack:
            range_label = '1 pack'
        else:
            range_label = row['size'] or None
        return {
            'pricing_basis': pricing_basis,
            'min_weight_g': None,
            'max_weight_g': None,
            'display_weight_g': None,
            'display_price': float(price_retail),
            'range_label': range_label,
        }

    if size_grams and size_grams <= 250:
        min_weight_g = size_grams
        max_weight_g = size_grams
        display_weight_g = size_grams

    range_label = (
        f"{min_weight_g}G-{max_weight_g}G"
        if min_weight_g and max_weight_g and min_weight_g != max_weight_g
        else (f"{display_weight_g}G" if display_weight_g else None)
    )
    display_price = round(float(price_retail) * (display_weight_g / 1000.0), 2) if display_weight_g else None
    return {
        'pricing_basis': pricing_basis,
        'min_weight_g': min_weight_g,
        'max_weight_g': max_weight_g,
        'display_weight_g': display_weight_g,
        'display_price': display_price,
        'range_label': range_label,
    }


def fetch_source_rows(source_conn: sqlite3.Connection) -> list[sqlite3.Row]:
    query = """
    WITH current_prices AS (
        SELECT merkey, price_retail, price_pack, price_case
        FROM prices
        WHERE is_current = 1
    ),
    primary_images AS (
        SELECT i.merkey,
               COALESCE(NULLIF(i.cdn_url, ''), NULLIF(i.s3_url, '')) AS photo_url
        FROM images i
        WHERE i.is_primary = 1
          AND COALESCE(i.public_status, 'ok') = 'ok'
    ),
    primary_barcodes AS (
        SELECT b.merkey, b.barcode
        FROM barcodes b
        WHERE b.is_primary = 1
    ),
    barcode_rollup AS (
        SELECT merkey, GROUP_CONCAT(barcode, ', ') AS all_barcodes
        FROM (
            SELECT DISTINCT merkey, barcode
            FROM barcodes
            WHERE barcode IS NOT NULL
              AND TRIM(barcode) <> ''
        )
        GROUP BY merkey
    )
    SELECT
        p.merkey,
        p.name,
        b.name AS brand_name,
        p.description,
        p.source_medesc,
        p.size,
        p.weight_volume,
        p.unit_of_measurement,
        p.pack_quantity,
        p.data_quality,
        p.supplier_name,
        p.class_l1_name,
        p.class_l2_name,
        p.class_l3_name,
        p.availability_override,
        COALESCE(p.show_pack_on_storefront, 0) AS show_pack_on_storefront,
        p.pack_display_label,
        p.pack_photo_url,
        p.pack_barcode,
        d.id AS department_id,
        d.name AS department_name,
        c.id AS category_id,
        c.name AS category_name,
        cp.price_retail,
        cp.price_pack,
        cp.price_case,
        COALESCE(pi.photo_url, ?) AS photo_url,
        pb.barcode,
        br.all_barcodes,
        inv.quantity_on_hand,
        inv.reorder_point,
        inv.last_updated AS inventory_last_updated,
        sm.txn_count_24m,
        sm.qty_sum_24m,
        sm.last_sale_date,
        sm.priority,
        p.needs_irl_photo,
        p.active
    FROM products p
    LEFT JOIN brands b ON b.id = p.brand_id
    LEFT JOIN categories c ON c.id = p.category_id
    LEFT JOIN departments d ON d.id = p.department_id
    LEFT JOIN current_prices cp ON cp.merkey = p.merkey
    LEFT JOIN primary_images pi ON pi.merkey = p.merkey
    LEFT JOIN primary_barcodes pb ON pb.merkey = p.merkey
    LEFT JOIN barcode_rollup br ON br.merkey = p.merkey
    LEFT JOIN inventory inv ON inv.merkey = p.merkey
    LEFT JOIN sales_metrics sm ON sm.merkey = p.merkey
    WHERE p.active = 1
      AND p.pending_deletion = 0
      AND COALESCE(TRIM(p.name), '') <> ''
      AND cp.price_retail IS NOT NULL
    ORDER BY
        CASE WHEN COALESCE(sm.priority, '') = 'TOP' THEN 0 ELSE 1 END,
        COALESCE(sm.txn_count_24m, 0) DESC,
        p.name COLLATE NOCASE
    """
    return source_conn.execute(query, (PLACEHOLDER_IMAGE,)).fetchall()


def rebuild_catalog(
    source_db: Path,
    target_db: Path,
    wi_esc_path: Path | None,
    wi_sdr_path: Path | None,
    fresh_acceptance_days: int,
    fe_t_path: Path | None,
    fe_t_paths: list[Path],
) -> tuple[int, int, int, dict[str, int], Path | None]:
    source_conn = connect(source_db)
    target_conn = connect(target_db)
    ensure_source_schema(source_conn)
    ensure_schema(target_conn)

    rows = fetch_source_rows(source_conn)
    inventory_enabled = has_inventory_snapshot(source_conn)
    acceptance_dates, max_acceptance_date, accepted_merkey_dates = load_last_acceptance_dates(wi_esc_path)
    delivery_dates, max_delivery_date = load_last_delivery_dates(wi_sdr_path, accepted_merkey_dates)
    max_fresh_signal_date = max_date_value(max_acceptance_date, max_delivery_date)
    sales_quantity_averages = load_monthly_sales_quantity_averages(fe_t_path)
    meat_sales_quantity_averages = load_meat_sales_quantity_averages(fe_t_paths)
    category_overrides = load_category_overrides()
    fresh_display_overrides = load_fresh_display_overrides()
    departments: dict[int, dict[str, object]] = {}
    categories: dict[int, dict[str, object]] = {}
    product_rows: list[tuple] = []
    department_lookup: dict[str, int] = {}
    category_lookup: dict[tuple[str, str], int] = {}
    next_category_id = 1
    exclusion_records: list[dict] = []
    exclusion_counts: dict[str, int] = {
        'no_image': 0,
        'no_recent_sales': 0,
        'invalid_department': 0,
        'price_out_of_range': 0,
    }

    for row in rows:
        dept_name = (row['department_name'] or 'Uncategorized').strip()
        category_name = (row['category_name'] or 'General').strip()
        if dept_name not in department_lookup:
            department_lookup[dept_name] = row['department_id'] or -(len(department_lookup) + 1)
        category_key = (dept_name, category_name)
        if category_key not in category_lookup:
            category_lookup[category_key] = next_category_id
            next_category_id += 1

    for row in rows:
        if is_masked_storefront_item(row) or is_unusual_storefront_item(row):
            continue
        avail_override = (row['availability_override'] or 'AUTO').strip().upper()
        stock_qty = float(row['quantity_on_hand'] or 0)
        reorder_point = float(row['reorder_point'] or 0)
        if avail_override == 'FORCE_UNAVAILABLE':
            continue

        if inventory_enabled and stock_qty <= 0:
            stock_status = 'out_of_stock'
        elif inventory_enabled and reorder_point > 0 and stock_qty <= reorder_point:
            stock_status = 'low_stock'
        else:
            stock_status = 'in_stock'

        fresh_display = classify_fresh_display(
            row,
            sales_quantity_averages,
            meat_sales_quantity_averages,
            fresh_display_overrides,
        ) or {}
        last_acceptance_date = later_iso_date(
            acceptance_dates.get(row['merkey']),
            delivery_dates.get(row['merkey']),
        )
        if fresh_display and not has_recent_acceptance(
            last_acceptance_date,
            max_fresh_signal_date,
            get_fresh_acceptance_window_days(row, fresh_acceptance_days),
        ):
            continue

        cat_override = category_overrides.get(row['merkey'])
        dept_name = (cat_override['department_name'] if cat_override else row['department_name'] or 'Uncategorized').strip()
        category_name = (cat_override['category_name'] if cat_override else row['category_name'] or 'General').strip()

        gate_failures: list[tuple[str, str]] = []
        image_warning = check_has_image(row)
        if image_warning:
            exclusion_counts['no_image'] += 1
        reason = check_price_sanity(row)
        if reason:
            gate_failures.append(('price_out_of_range', reason))
        reason = check_valid_department(dept_name)
        if reason:
            gate_failures.append(('invalid_department', reason))
        reason = check_sales_recency(row, is_fresh=bool(fresh_display))
        if reason:
            gate_failures.append(('no_recent_sales', reason))
        if gate_failures or image_warning:
            exclusion_records.append({
                'merkey': row['merkey'],
                'name': (row['name'] or '').strip(),
                'reasons': ([('no_image', image_warning)] if image_warning else []) + gate_failures,
            })
            for key, _ in gate_failures:
                exclusion_counts[key] += 1
            if gate_failures:
                continue

        sellable_state, sellable_note = determine_sellable_state(row, fresh_display)
        fulfillment_profile = determine_fulfillment_profile(row, fresh_display, sellable_state, sellable_note)

        dept_id = department_lookup.setdefault(dept_name, -(len(department_lookup) + 1))
        category_id = category_lookup.setdefault((dept_name, category_name), next_category_id)
        if category_id == next_category_id:
            next_category_id += 1
        dept_slug = slugify(dept_name)
        category_slug = slugify(category_name)

        departments.setdefault(dept_id, {'id': dept_id, 'name': dept_name, 'slug': dept_slug, 'product_count': 0})
        departments[dept_id]['product_count'] += 1
        categories.setdefault(category_id, {'id': category_id, 'department_id': dept_id, 'name': category_name, 'slug': category_slug, 'product_count': 0})
        categories[category_id]['product_count'] += 1

        display_name = (row['name'] or row['description'] or row['merkey']).strip()
        brand_name = (row['brand_name'] or '').strip()
        slug = slugify(f"{brand_name} {display_name} {row['merkey']}")
        search_text = ' '.join(
            part for part in [
                row['merkey'], brand_name, display_name, row['description'], row['size'],
                dept_name, category_name, row['barcode'], row['all_barcodes'], row['supplier_name'], last_acceptance_date
            ] if part
        ).lower()

        product_rows.append((
            row['merkey'], slug, display_name, brand_name, row['description'], row['size'],
            dept_id, dept_name, dept_slug,
            category_id, category_name, category_slug,
            row['price_retail'], row['price_pack'], row['price_case'], row['show_pack_on_storefront'], row['pack_display_label'], row['pack_photo_url'], row['pack_barcode'], row['photo_url'], row['data_quality'],
            row['supplier_name'], row['class_l1_name'], row['class_l2_name'], row['class_l3_name'],
            row['barcode'], row['all_barcodes'], row['txn_count_24m'], row['qty_sum_24m'],
            row['last_sale_date'], row['priority'], last_acceptance_date,
            sellable_state,
            sellable_note,
            fulfillment_profile.get('fulfillment_type'),
            fulfillment_profile.get('order_unit_label'),
            fulfillment_profile.get('substitution_policy'),
            fulfillment_profile.get('fulfillment_note'),
            fresh_display.get('pricing_basis'),
            fresh_display.get('min_weight_g'),
            fresh_display.get('max_weight_g'),
            fresh_display.get('display_weight_g'),
            fresh_display.get('display_price'),
            fresh_display.get('range_label'),
            row['active'], search_text,
            row['needs_irl_photo'],
            stock_status,
        ))

    with target_conn:
        target_conn.execute('DELETE FROM departments')
        target_conn.execute('DELETE FROM categories')
        target_conn.execute('DELETE FROM products')
        target_conn.executemany(
            'INSERT INTO departments (id, name, slug, product_count) VALUES (:id, :name, :slug, :product_count)',
            list(departments.values()),
        )
        target_conn.executemany(
            'INSERT INTO categories (id, department_id, name, slug, product_count) VALUES (:id, :department_id, :name, :slug, :product_count)',
            list(categories.values()),
        )
        target_conn.executemany(
            """
            INSERT INTO products (
                merkey, slug, name, brand, description, size,
                department_id, department_name, department_slug,
                category_id, category_name, category_slug,
                price_retail, price_pack, price_case, show_pack_on_storefront, pack_display_label, pack_photo_url, pack_barcode, photo_url, status,
                supplier_name, class_l1_name, class_l2_name, class_l3_name,
                barcode, all_barcodes, txn_count_24m, qty_sum_24m,
                last_sale_date, priority, last_acceptance_date, sellable_state, sellable_note, fulfillment_type, order_unit_label, substitution_policy, fulfillment_note, pricing_basis, min_weight_g, max_weight_g,
                display_weight_g, display_price, range_label, active, search_text, needs_irl_photo, stock_status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            product_rows,
        )
        target_conn.execute("INSERT INTO storefront_metadata (key, value) VALUES ('last_publish_at', CURRENT_TIMESTAMP) ON CONFLICT(key) DO UPDATE SET value=excluded.value")
        target_conn.execute(
            "INSERT INTO storefront_metadata (key, value) VALUES ('source_db', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(source_db),),
        )

    report_path = write_exclusion_report(exclusion_records, exclusion_counts, BASE_DIR)

    source_conn.close()
    target_conn.close()
    return len(product_rows), len(departments), len(categories), exclusion_counts, report_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Publish encoder data into storefront DB.')
    parser.add_argument('--source-db', default=str(SOURCE_DB))
    parser.add_argument('--target-db', default=str(TARGET_DB))
    parser.add_argument('--wi-esc', default=None, help='Optional WI_ESC.FPB path for fresh acceptance recency filtering')
    parser.add_argument('--wi-sdr', default=None, help='Optional WI_SDR.FPB path for open fresh delivery recency fallback')
    parser.add_argument('--fe-t', default=None, help='Optional FE_T monthly transaction file for weighed vegetable display ranges')
    parser.add_argument('--fresh-acceptance-days', type=int, default=DEFAULT_FRESH_ACCEPTANCE_DAYS, help='Default fresh receipt window in days for classes without an override')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_db = Path(args.source_db)
    target_db = Path(args.target_db)
    wi_esc_path = resolve_wi_esc_path(args.wi_esc)
    wi_sdr_path = resolve_wi_sdr_path(args.wi_sdr)
    fe_t_paths = resolve_fe_t_paths(args.fe_t)
    fe_t_path = fe_t_paths[0] if fe_t_paths else None
    if not source_db.exists():
        raise FileNotFoundError(f'Source DB not found: {source_db}')
    product_count, department_count, category_count, exclusion_counts, report_path = rebuild_catalog(
        source_db,
        target_db,
        wi_esc_path,
        wi_sdr_path,
        args.fresh_acceptance_days,
        fe_t_path,
        fe_t_paths,
    )
    print(f'Published {product_count:,} products')
    print(f'Departments: {department_count}')
    print(f'Categories: {category_count}')
    print(f'Output DB: {target_db}')
    if wi_esc_path:
        print(f'WI_ESC: {wi_esc_path}')
    if wi_sdr_path:
        print(f'WI_SDR: {wi_sdr_path}')
    if fe_t_path:
        print(f'FE_T: {fe_t_path}')
    if len(fe_t_paths) > 1:
        print(f'FE_T fallback files: {len(fe_t_paths)}')
    no_image_count = exclusion_counts.get('no_image', 0)
    excluded_counts = {k: v for k, v in exclusion_counts.items() if k != 'no_image'}
    total_excluded = sum(excluded_counts.values())
    if no_image_count or total_excluded:
        if no_image_count:
            print(f'\nWarning: {no_image_count:,} published products have no image (not excluded)')
        if total_excluded:
            print(f'\nQuality gate exclusions:')
            for gate_key, label in GATE_LABELS.items():
                if gate_key == 'no_image':
                    continue
                count = exclusion_counts.get(gate_key, 0)
                if count:
                    print(f'  {label + ":":<25s} {count}')
            print(f'  {"TOTAL:":<25s} {total_excluded}')
        if report_path:
            print(f'Report: {report_path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
