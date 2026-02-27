#!/usr/bin/env python3
"""
Photo Enrichment Script
Anson Supermart Catalog Management

Automatically finds product photos via DuckDuckGo Image Search
and uploads them through the standard white-bg -> S3 pipeline.

Commands:
  stats                       Show photo queue breakdown
  tag-irl  [--dry-run]        Flag fashion/footwear/gift items as needing IRL photography
  run      [--limit N]        Search DDG images and upload to S3
           [--dry-run]        Preview search results without downloading/uploading
           [--merkey X]       Process a single product
           [--no-remove-bg]   Skip background removal (faster, use for testing)

Usage:
  python photo_enrich.py stats
  python photo_enrich.py tag-irl --dry-run
  python photo_enrich.py tag-irl
  python photo_enrich.py run --dry-run --limit 10
  python photo_enrich.py run --limit 500
  python photo_enrich.py run --merkey 1019919
"""

import sqlite3
import re
import argparse
import sys
import time
import tempfile
import requests
from pathlib import Path
from datetime import datetime

DB_PATH  = "anson_products.db"
ORIG_DIR = Path("uploads/original")
PROC_DIR = Path("uploads/processed")
ORIG_DIR.mkdir(parents=True, exist_ok=True)
PROC_DIR.mkdir(parents=True, exist_ok=True)

S3_BUCKET = "ansonsupermart.com"
S3_PREFIX = "images/"
S3_REGION = "ap-southeast-1"

SEARCH_DELAY    = 2.5    # seconds between DDG searches (be respectful)
DOWNLOAD_TIMEOUT = 15    # seconds
MIN_IMAGE_BYTES  = 8_000 # skip files too small to be a real product image
BATCH_COMMIT     = 25

PLACEHOLDER_URL = "https://s3-ap-southeast-1.amazonaws.com/ansonsupermart.com/images/ANSON-ONLINE-GROCERY-PLACEHOLDER.jpg"
BLANK_IMAGE_URL = "https://s3-ap-southeast-1.amazonaws.com/ansonsupermart.com/images/"

# Categories that need IRL photography — web search won't find the right variant
IRL_DEPARTMENTS = {"Fashion & Apparel"}
IRL_CATEGORIES  = {"Gift Items", "Foot Wear", "Footwear"}

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def ensure_irl_column(cur):
    try:
        cur.execute("ALTER TABLE products ADD COLUMN needs_irl_photo INTEGER NOT NULL DEFAULT 0")
    except Exception:
        pass  # already exists


# ---------------------------------------------------------------------------
# Search query builder
# ---------------------------------------------------------------------------

SIZE_RE  = re.compile(r'\b\d+(?:[.,]\d+)?\s*(?:KG|G|GM|MG|LT|ML|L\b|OZ|LBS?|PLY|YRDS?|FT\b|IN\b|CM|MM)\b', re.I)
PACK_RE  = re.compile(r'\s*/\d+(?:/\d+)*\s*$')
NOISE_RE = re.compile(r'\s+(?:[A-Z]{1,3}\d+|\d{5,}|P\d+\.\d{2}|EO|REF|NEW)\s*$')


def clean_desc_for_search(desc: str) -> str:
    """Strip MEDESC prefixes, pack counts, ref codes to get a searchable product name."""
    s = (desc or "").strip().lstrip("!").lstrip("*")
    # Remove dotted category prefix: FW.WEN -> WEN, NC.TOY.FIRETRUCK -> FIRETRUCK
    parts = s.split()
    if parts and "." in parts[0]:
        s = " ".join(parts[1:]) if len(parts) > 1 else s
    # Remove trailing size + pack count
    m = SIZE_RE.search(s)
    if m:
        s = s[:m.start()].strip()
    else:
        s = PACK_RE.sub("", s)
        s = NOISE_RE.sub("", s)
    return s.strip()


def build_search_query(barcode: str | None, brand: str | None, desc: str) -> str:
    """Build the best DDG image search query for this product."""
    if barcode:
        # Barcode search is very precise — usually brings up the exact product
        return f'"{barcode}"'
    # Fall back to brand + cleaned description
    name = clean_desc_for_search(desc)
    if brand and brand.lower() not in name.lower():
        return f"{brand} {name}"
    return name


# ---------------------------------------------------------------------------
# Image download
# ---------------------------------------------------------------------------

SKIP_EXTENSIONS = {".gif", ".svg", ".ico", ".bmp", ".tiff"}


def download_image(url: str, dest_path: Path, session: requests.Session) -> bool:
    """
    Download an image URL to dest_path.
    Returns True on success, False if the image should be skipped.
    """
    try:
        ext = Path(url.split("?")[0]).suffix.lower()
        if ext in SKIP_EXTENSIONS:
            return False

        resp = session.get(url, timeout=DOWNLOAD_TIMEOUT,
                           headers={"User-Agent": USER_AGENT}, stream=True)
        if resp.status_code != 200:
            return False

        content_type = resp.headers.get("content-type", "")
        if "svg" in content_type or "gif" in content_type:
            return False

        data = b""
        for chunk in resp.iter_content(8192):
            data += chunk
            if len(data) > 15_000_000:  # 15MB hard cap
                return False

        if len(data) < MIN_IMAGE_BYTES:
            return False

        dest_path.write_bytes(data)
        return True

    except Exception:
        return False


# ---------------------------------------------------------------------------
# Command: stats
# ---------------------------------------------------------------------------

def cmd_stats():
    conn = get_db()
    cur  = conn.cursor()
    ensure_irl_column(cur)

    cur.execute("SELECT COUNT(*) FROM products WHERE active=1")
    total = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM products WHERE active=1 AND needs_photo=0")
    has_photo = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM products WHERE active=1 AND needs_photo=1 AND needs_irl_photo=1")
    irl = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM products WHERE active=1 AND needs_photo=1 AND needs_irl_photo=0")
    auto_queue = cur.fetchone()[0]

    # Of the auto queue, how many have a searchable barcode?
    cur.execute("""
        SELECT COUNT(DISTINCT p.merkey)
        FROM products p
        JOIN barcodes b ON p.merkey=b.merkey
        WHERE p.active=1 AND p.needs_photo=1 AND p.needs_irl_photo=0
          AND b.barcode NOT LIKE '4000%'
          AND LENGTH(b.barcode) IN (12,13)
          AND b.barcode GLOB '[0-9]*'
    """)
    with_barcode = cur.fetchone()[0]

    conn.close()

    print("=" * 55)
    print("PHOTO QUEUE SUMMARY")
    print("=" * 55)
    print(f"Total active products      : {total:,}")
    print(f"  Have a real photo        : {has_photo:,}")
    print(f"  Needing a photo          : {total - has_photo:,}")
    print()
    print(f"Of those needing a photo:")
    print(f"  IRL photography only     : {irl:,}  (fashion/footwear/gifts)")
    print(f"  Auto-search queue        : {auto_queue:,}")
    print(f"    -> searchable by EAN   : {with_barcode:,}  (precise barcode search)")
    print(f"    -> name search only    : {auto_queue - with_barcode:,}")
    print("=" * 55)


# ---------------------------------------------------------------------------
# Command: tag-irl
# ---------------------------------------------------------------------------

def cmd_tag_irl(dry_run: bool):
    conn = get_db()
    cur  = conn.cursor()
    ensure_irl_column(cur)

    # Find active products in IRL categories that still need a photo
    cur.execute("""
        SELECT p.merkey, p.description, d.name as dept, c.name as cat
        FROM products p
        LEFT JOIN categories c ON p.category_id=c.id
        LEFT JOIN departments d ON c.department_id=d.id
        WHERE p.active=1
          AND p.needs_photo=1
          AND p.needs_irl_photo=0
          AND (
              d.name IN ({depts})
              OR c.name IN ({cats})
          )
    """.format(
        depts=",".join(f"'{d}'" for d in IRL_DEPARTMENTS),
        cats=",".join(f"'{c}'" for c in IRL_CATEGORIES),
    ))
    rows = cur.fetchall()

    print(f"Products to tag as needs_irl_photo: {len(rows):,}")

    if dry_run:
        from collections import Counter
        by_cat = Counter(f"{r['dept']} > {r['cat']}" for r in rows)
        print()
        for label, cnt in by_cat.most_common():
            print(f"  {cnt:>5}  {label}")
        print("\nDRY RUN — no changes written.")
        conn.close()
        return

    merkeys = [r["merkey"] for r in rows]
    cur.executemany(
        "UPDATE products SET needs_irl_photo=1, updated_at=CURRENT_TIMESTAMP WHERE merkey=?",
        [(mk,) for mk in merkeys]
    )
    conn.commit()
    conn.close()

    print("Tagged.")
    print("\nRun 'stats' to see updated queue breakdown.")


# ---------------------------------------------------------------------------
# Command: run
# ---------------------------------------------------------------------------

def cmd_run(limit: int | None, dry_run: bool, target_merkey: str | None, remove_bg: bool):
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        print("ERROR: duckduckgo_search not installed. Run: pip install duckduckgo_search")
        sys.exit(1)

    process_to_white_bg = None
    upload_file_to_s3   = None
    if not dry_run:
        try:
            from image_pipeline import process_to_white_bg
            from s3_upload import upload_file_to_s3
        except ImportError as e:
            print(f"ERROR: {e}")
            sys.exit(1)

    conn = get_db()
    cur  = conn.cursor()
    ensure_irl_column(cur)

    if target_merkey:
        cur.execute("""
            SELECT p.merkey, p.description, p.name, b.name as brand,
                   s.txn_count_24m
            FROM products p
            LEFT JOIN brands b ON p.brand_id=b.id
            LEFT JOIN sales_metrics s ON p.merkey=s.merkey
            WHERE p.merkey=?
        """, (target_merkey,))
    else:
        cur.execute("""
            SELECT p.merkey, p.description, p.name, b.name as brand,
                   COALESCE(s.txn_count_24m, 0) as txn_count_24m
            FROM products p
            LEFT JOIN brands b ON p.brand_id=b.id
            LEFT JOIN sales_metrics s ON p.merkey=s.merkey
            WHERE p.active=1
              AND p.needs_photo=1
              AND p.needs_irl_photo=0
            ORDER BY txn_count_24m DESC, p.merkey ASC
        """ + (f" LIMIT {limit}" if limit else ""))

    products = cur.fetchall()
    total = len(products)

    print("=" * 65)
    print("PHOTO ENRICHMENT — DuckDuckGo Image Search")
    print("=" * 65)
    print(f"Products to process : {total:,}")
    print(f"Dry run             : {dry_run}")
    print(f"Background removal  : {remove_bg}")
    print(f"Search delay        : {SEARCH_DELAY}s")
    print()

    if total == 0:
        print("Nothing to process.")
        conn.close()
        return

    stats = dict(processed=0, uploaded=0, no_results=0, download_failed=0, skipped=0)
    http_session = requests.Session()

    for i, p in enumerate(products, 1):
        merkey = p["merkey"]
        desc   = (p["description"] or "").strip()
        brand  = (p["brand"] or "").strip()

        # Get best barcode for this product
        cur.execute("""
            SELECT barcode FROM barcodes
            WHERE merkey=?
              AND barcode NOT LIKE '4000%'
              AND LENGTH(barcode) IN (12,13)
              AND barcode GLOB '[0-9]*'
            ORDER BY is_primary DESC, id ASC
            LIMIT 1
        """, (merkey,))
        bc_row = cur.fetchone()
        barcode = bc_row["barcode"] if bc_row else None

        query = build_search_query(barcode, brand, desc)

        if dry_run:
            print(f"[{i:>5}/{total}] {merkey} | {desc[:40]}")
            print(f"         query: {query}")
            stats["processed"] += 1
            continue

        # --- Search ---
        image_url = None
        try:
            with DDGS() as ddgs:
                results = list(ddgs.images(keywords=query, max_results=5))
            for r in results:
                url = r.get("image", "")
                if url and not any(url.lower().endswith(ext) for ext in SKIP_EXTENSIONS):
                    image_url = url
                    break
        except Exception as e:
            print(f"  [{i:>5}] {merkey} DDG error: {e}")
            stats["no_results"] += 1
            stats["processed"] += 1
            time.sleep(SEARCH_DELAY)
            continue

        if not image_url:
            stats["no_results"] += 1
            stats["processed"] += 1
            time.sleep(SEARCH_DELAY)
            continue

        # --- Download ---
        identifier = barcode if barcode else merkey
        orig_path  = ORIG_DIR / f"{merkey}_auto.jpg"
        proc_path  = PROC_DIR / f"{identifier}.jpg"

        if not download_image(image_url, orig_path, http_session):
            stats["download_failed"] += 1
            stats["processed"] += 1
            time.sleep(SEARCH_DELAY)
            continue

        # --- Process + Upload ---
        try:
            result = process_to_white_bg(orig_path, proc_path,
                                         size=1200, padding_ratio=0.10,
                                         try_remove_bg=remove_bg)
            s3_key = f"{S3_PREFIX}{identifier}.jpg"
            up = upload_file_to_s3(proc_path, key=s3_key, bucket=S3_BUCKET,
                                   region=S3_REGION, content_type="image/jpeg",
                                   public_read=True)
        except Exception as e:
            print(f"  [{i:>5}] {merkey} pipeline/upload error: {e}")
            stats["download_failed"] += 1
            stats["processed"] += 1
            time.sleep(SEARCH_DELAY)
            continue

        # --- Update DB ---
        cur.execute("UPDATE images SET is_primary=0 WHERE merkey=? AND is_primary=1", (merkey,))
        cur.execute("""
            INSERT INTO images(merkey, filename, s3_url, local_path, is_primary,
                               width, height, file_size, uploaded_at)
            VALUES(?,?,?,?,1,?,?,?,CURRENT_TIMESTAMP)
        """, (merkey, f"{identifier}.jpg", up.url, str(proc_path),
              result.width, result.height, result.file_size))
        cur.execute("""
            UPDATE products
            SET needs_photo=0,
                enrichment_notes='Photo auto-sourced via DDG image search',
                updated_at=CURRENT_TIMESTAMP
            WHERE merkey=?
        """, (merkey,))

        stats["uploaded"] += 1
        stats["processed"] += 1

        print(f"  [{i:>5}/{total}] {merkey} | {desc[:35]:<35} | query={query[:30]} -> OK")

        if stats["uploaded"] % BATCH_COMMIT == 0:
            conn.commit()

        time.sleep(SEARCH_DELAY)

    if not dry_run:
        conn.commit()
    conn.close()

    print()
    print("=" * 65)
    print("SUMMARY")
    print("=" * 65)
    print(f"Processed       : {stats['processed']:,}")
    print(f"  Uploaded      : {stats['uploaded']:,}")
    print(f"  No results    : {stats['no_results']:,}")
    print(f"  Download fail : {stats['download_failed']:,}")
    if dry_run:
        print("\nDRY RUN — no images downloaded or uploaded.")
    print("=" * 65)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Photo enrichment via DuckDuckGo image search")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("stats")

    p_irl = sub.add_parser("tag-irl")
    p_irl.add_argument("--dry-run", action="store_true")

    p_run = sub.add_parser("run")
    p_run.add_argument("--limit",        type=int,  default=None)
    p_run.add_argument("--dry-run",      action="store_true")
    p_run.add_argument("--merkey",       type=str,  default=None)
    p_run.add_argument("--no-remove-bg", action="store_true",
                       help="Skip background removal (faster for testing)")

    args = parser.parse_args()
    print(f"\nStarted: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    if args.cmd == "stats":
        cmd_stats()
    elif args.cmd == "tag-irl":
        cmd_tag_irl(args.dry_run)
    elif args.cmd == "run":
        cmd_run(args.limit, args.dry_run, args.merkey,
                remove_bg=not args.no_remove_bg)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
