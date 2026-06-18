#!/usr/bin/env python3
"""
Photo Enrichment Script
Anson Supermart Catalog Management

Automatically finds product photos via Google Image Search
and uploads them through the standard white-bg -> S3 pipeline.

Commands:
  stats                       Show photo queue breakdown
  tag-irl  [--dry-run]        Flag fashion/footwear/gift items as needing IRL photography
  mark-skip [--auto]          Permanently skip unresolvable products (tobacco, bulk/loose, fresh)
            [--merkeys A,B]   Also skip specific merkeys by comma-separated list
            [--dry-run]       Preview without writing
  run      [--limit N]        Search Google images and upload to S3
           [--dry-run]        Preview search results without downloading/uploading
           [--merkey X]       Process a single product
           [--no-remove-bg]   Skip background removal (faster, use for testing)

Usage:
  python photo_enrich.py stats
  python photo_enrich.py tag-irl --dry-run
  python photo_enrich.py tag-irl
  python photo_enrich.py mark-skip --auto --dry-run
  python photo_enrich.py mark-skip --auto
  python photo_enrich.py mark-skip --merkeys 9326,9270,1363227
  python photo_enrich.py run --dry-run --limit 10
  python photo_enrich.py run --limit 500
  python photo_enrich.py run --merkey 1019919
"""

import sqlite3
import re
import argparse
import sys
import time
import random
import shutil
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

SEARCH_DELAY_MIN  = 4.0  # seconds between searches (randomised to avoid fingerprinting)
SEARCH_DELAY_MAX  = 9.0
BATCH_PAUSE_EVERY = 60   # pause for BATCH_PAUSE_SECS after this many products
BATCH_PAUSE_SECS  = 1800 # 30-minute cooldown every 60 products
MAX_429_WAIT_SECS = 1800 # wait 30 min on rate-limit, retry once then exit cleanly
DOWNLOAD_TIMEOUT  = 15   # seconds for image downloads
MIN_IMAGE_BYTES   = 15_000 # skip anything under 15 KB (thumbnails)
MIN_IMAGE_DIM     = 400    # skip anything smaller than 400px on either side
BATCH_COMMIT      = 5

# Rotate user agents to reduce fingerprinting
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_2) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
]

PLACEHOLDER_URL = "https://s3-ap-southeast-1.amazonaws.com/ansonsupermart.com/images/ANSON-ONLINE-GROCERY-PLACEHOLDER.jpg"
BLANK_IMAGE_URL = "https://s3-ap-southeast-1.amazonaws.com/ansonsupermart.com/images/"

# Categories that need IRL photography — web search won't find the right variant
IRL_DEPARTMENTS = {"Fashion & Apparel"}
IRL_CATEGORIES  = {"Gift Items", "Foot Wear", "Footwear"}

# Products to permanently exclude from auto-search (mark-skip --auto)
SKIP_DEPARTMENTS = {"Fresh"}                        # Fresh produce / meat / fish dept
SKIP_CATEGORIES  = {"Tobacco", "Cigarettes"}        # Cigarette brands (no online images)
SKIP_DESC_PREFIXES = (                              # Bulk/loose items — no packaged image exists
    "RICE.", "FRUITS.", "SUGAR.", "VEGGIES.", "MEAT.",
    "PORK.", "CHICKEN.", "FISH.", "EGG.", "SEAFOOD.",
    "PRODUCE.", "FROZEN.",
)
SKIP_DESC_EXACT = {                                 # Generic commodity items
    "HOT WATER", "TUBE/CUBE ICE", "MIX VEGETABLE",
}

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
    """Build the best image search query for this product."""
    if barcode:
        # Quoted barcode = exact match, surfaces product pages and retailer listings
        return f'"{barcode}"'
    # Fall back to brand + cleaned description
    name = clean_desc_for_search(desc)
    if brand and brand.lower() not in name.lower():
        return f"{brand} {name}"
    return name


def google_image_search(query: str, session: requests.Session, n: int = 5) -> tuple[list[str], bool]:
    """
    Return (urls, rate_limited).
    urls         : up to n full-res image URLs
    rate_limited : True if Google returned 429 (caller should back off)
    """
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    url = f"https://www.google.com/search?tbm=isch&q={requests.utils.quote(query)}&num={n}"
    try:
        resp = session.get(url, headers=headers, timeout=15)
    except Exception:
        return [], False
    if resp.status_code == 429:
        return [], True   # rate limited
    if resp.status_code != 200:
        return [], False
    raw_urls = re.findall(r'"(https?://[^"]{20,}\.(?:jpg|jpeg|png|webp)[^"]*)"', resp.text)
    real = []
    seen = set()
    for u in raw_urls:
        if "google" in u or "gstatic" in u:
            continue
        if u not in seen:
            seen.add(u)
            real.append(u)
        if len(real) >= n:
            break
    return real, False


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


def find_best_image(urls: list[str], session: requests.Session) -> tuple[Path | None, int, int]:
    """
    Download all candidate URLs to temp files, check dimensions with PIL,
    and return (best_path, width, height) for the largest image that meets
    minimum quality thresholds.  Returns (None, 0, 0) if all fail.

    Caller is responsible for moving/copying best_path to its final location
    and cleaning up the temp directory.
    """
    from PIL import Image as PILImage

    tmp_dir    = Path(tempfile.mkdtemp(prefix="photo_enrich_"))
    candidates = []

    for idx, url in enumerate(urls):
        tmp_path = tmp_dir / f"candidate_{idx}.jpg"
        if not download_image(url, tmp_path, session):
            continue
        try:
            with PILImage.open(tmp_path) as img:
                w, h = img.size
            if w < MIN_IMAGE_DIM or h < MIN_IMAGE_DIM:
                tmp_path.unlink(missing_ok=True)
                continue
            candidates.append((w * h, w, h, tmp_path))
        except Exception:
            tmp_path.unlink(missing_ok=True)

    if not candidates:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return None, 0, 0

    # Pick the largest by pixel area
    candidates.sort(key=lambda c: c[0], reverse=True)
    _, best_w, best_h, best_path = candidates[0]

    # Clean up the losers
    for _, _, _, p in candidates[1:]:
        p.unlink(missing_ok=True)

    return best_path, best_w, best_h


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
# Command: mark-skip
# ---------------------------------------------------------------------------

def cmd_mark_skip(auto: bool, merkeys_arg: list[str], dry_run: bool):
    """
    Permanently remove products from the auto-search queue by setting
    needs_irl_photo=1.  Use for products that will never have a web-findable
    packaged-product image (cigarettes, bulk loose items, fresh produce, etc.).
    """
    conn = get_db()
    cur  = conn.cursor()
    ensure_irl_column(cur)

    target_merkeys = set()

    if auto:
        # -- Pattern 1: Tobacco/cigarette category --------------------------
        cat_ph = ",".join(f"'{c}'" for c in SKIP_CATEGORIES)
        cur.execute(f"""
            SELECT p.merkey
            FROM products p
            LEFT JOIN categories c ON p.category_id=c.id
            WHERE p.active=1
              AND p.needs_photo=1
              AND p.needs_irl_photo=0
              AND c.name IN ({cat_ph})
        """)
        tobacco_rows = cur.fetchall()
        for r in tobacco_rows:
            target_merkeys.add(r["merkey"])
        print(f"  Auto (tobacco/cigarette category) : {len(tobacco_rows):,}")

        # -- Pattern 2: Bulk/loose description prefixes & exact matches -----
        prefix_sql = " OR ".join(f"p.description LIKE '{pfx}%'"
                                 for pfx in SKIP_DESC_PREFIXES)
        exact_ph   = ",".join(f"'{e}'" for e in SKIP_DESC_EXACT)
        cur.execute(f"""
            SELECT p.merkey
            FROM products p
            WHERE p.active=1
              AND p.needs_photo=1
              AND p.needs_irl_photo=0
              AND ({prefix_sql} OR p.description IN ({exact_ph}))
        """)
        bulk_rows = cur.fetchall()
        for r in bulk_rows:
            target_merkeys.add(r["merkey"])
        print(f"  Auto (bulk/loose descriptions)    : {len(bulk_rows):,}")

        # -- Pattern 3: Fresh department ------------------------------------
        dept_ph = ",".join(f"'{d}'" for d in SKIP_DEPARTMENTS)
        cur.execute(f"""
            SELECT p.merkey
            FROM products p
            LEFT JOIN categories c ON p.category_id=c.id
            LEFT JOIN departments d ON c.department_id=d.id
            WHERE p.active=1
              AND p.needs_photo=1
              AND p.needs_irl_photo=0
              AND d.name IN ({dept_ph})
        """)
        fresh_rows = cur.fetchall()
        for r in fresh_rows:
            target_merkeys.add(r["merkey"])
        print(f"  Auto (Fresh department)           : {len(fresh_rows):,}")

    # -- Manual merkeys from --merkeys A,B,C --------------------------------
    if merkeys_arg:
        for mk in merkeys_arg:
            target_merkeys.add(int(mk.strip()))
        print(f"  Manual (--merkeys)                : {len(merkeys_arg)}")

    if not target_merkeys:
        print("No products matched. Use --auto and/or --merkeys.")
        conn.close()
        return

    print(f"\nTotal to mark as skip (needs_irl_photo=1): {len(target_merkeys):,}")

    if dry_run:
        cur.execute(f"""
            SELECT p.merkey, p.description, d.name as dept, c.name as cat
            FROM products p
            LEFT JOIN categories c ON p.category_id=c.id
            LEFT JOIN departments d ON c.department_id=d.id
            WHERE p.merkey IN ({','.join('?' for _ in target_merkeys)})
            ORDER BY d.name, p.description
            LIMIT 25
        """, list(target_merkeys))
        sample = cur.fetchall()
        print("\nSample (first 25):")
        for r in sample:
            print(f"  {r['merkey']:>8}  {r['description'][:40]:<40}  "
                  f"[{r['dept'] or '?'} > {r['cat'] or '?'}]")
        if len(target_merkeys) > 25:
            print(f"  ... and {len(target_merkeys) - 25} more")
        print("\nDRY RUN — no changes written.")
        conn.close()
        return

    cur.executemany(
        "UPDATE products SET needs_irl_photo=1, updated_at=CURRENT_TIMESTAMP WHERE merkey=?",
        [(mk,) for mk in target_merkeys]
    )
    conn.commit()
    conn.close()

    print(f"Done. {len(target_merkeys):,} products moved out of the auto-search queue.")
    print("Run 'stats' to see the updated breakdown.")


# ---------------------------------------------------------------------------
# Command: run
# ---------------------------------------------------------------------------

def cmd_run(limit: int | None, dry_run: bool, target_merkey: str | None, remove_bg: bool):
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
    print("PHOTO ENRICHMENT — Google Image Search")
    print("=" * 65)
    print(f"Products to process : {total:,}")
    print(f"Dry run             : {dry_run}")
    print(f"Background removal  : {remove_bg}")
    print(f"Search delay        : {SEARCH_DELAY_MIN}-{SEARCH_DELAY_MAX}s (randomised)")
    print()

    if total == 0:
        print("Nothing to process.")
        conn.close()
        return

    stats = dict(processed=0, uploaded=0, no_results=0, download_failed=0)
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
        bc_row  = cur.fetchone()
        barcode = bc_row["barcode"] if bc_row else None
        query   = build_search_query(barcode, brand, desc)

        if dry_run:
            print(f"[{i:>5}/{total}] {merkey} | {desc[:40]}")
            print(f"         query: {query}")
            stats["processed"] += 1
            continue

        # --- Search (with single 429 backoff, then clean exit) ---
        results, rate_limited = google_image_search(query, http_session, n=5)
        if rate_limited:
            print(f"\n  [RATE LIMITED] Waiting {MAX_429_WAIT_SECS//60} min before one retry...", flush=True)
            time.sleep(MAX_429_WAIT_SECS)
            http_session = requests.Session()
            results, rate_limited = google_image_search(query, http_session, n=5)
            if rate_limited:
                print(f"  [RATE LIMITED] Still blocked after {MAX_429_WAIT_SECS//60} min. "
                      f"Processed {stats['processed']}, uploaded {stats['uploaded']}. "
                      f"Exiting cleanly — rerun to continue.", flush=True)
                break

        if not results:
            stats["no_results"] += 1
            stats["processed"] += 1
            print(f"  [{i:>5}/{total}] {merkey} | {desc[:35]:<35} | {query[:28]} -> no results")
            time.sleep(random.uniform(SEARCH_DELAY_MIN, SEARCH_DELAY_MAX))
            continue

        # --- Download all candidates, pick the best (largest dims) ---
        identifier = barcode if barcode else merkey
        orig_path  = ORIG_DIR / f"{merkey}_auto.jpg"
        proc_path  = PROC_DIR / f"{identifier}.jpg"

        best_path, best_w, best_h = find_best_image(results, http_session)
        if best_path is None:
            stats["download_failed"] += 1
            stats["processed"] += 1
            print(f"  [{i:>5}/{total}] {merkey} | {desc[:35]:<35} | {query[:28]} -> download fail")
            time.sleep(random.uniform(SEARCH_DELAY_MIN, SEARCH_DELAY_MAX))
            continue
        shutil.move(str(best_path), orig_path)
        shutil.rmtree(best_path.parent, ignore_errors=True)

        # --- Process + Upload ---
        try:
            result = process_to_white_bg(orig_path, proc_path,
                                         size=1200, padding_ratio=0.06,
                                         try_remove_bg=remove_bg)
            s3_key = f"{S3_PREFIX}{identifier}.jpg"
            up = upload_file_to_s3(proc_path, key=s3_key, bucket=S3_BUCKET,
                                   region=S3_REGION, content_type="image/jpeg",
                                   public_read=True)
        except Exception as e:
            print(f"  [{i:>5}/{total}] {merkey} pipeline/upload error: {e}")
            stats["download_failed"] += 1
            stats["processed"] += 1
            time.sleep(random.uniform(SEARCH_DELAY_MIN, SEARCH_DELAY_MAX))
            continue

        # --- Update DB ---
        cur.execute("UPDATE images SET is_primary=0 WHERE merkey=? AND is_primary=1", (merkey,))
        cur.execute("""
            INSERT INTO images(
                merkey, filename, s3_url, local_path, is_primary,
                width, height, file_size, uploaded_at,
                public_status, public_status_code, public_checked_at, public_error
            )
            VALUES(?,?,?,?,1,?,?,?,CURRENT_TIMESTAMP,'ok',200,CURRENT_TIMESTAMP,'Uploaded by photo_enrich')
        """, (merkey, f"{identifier}.jpg", up.url, str(proc_path),
              result.width, result.height, result.file_size))
        cur.execute("""
            UPDATE products
            SET needs_photo=0,
                enrichment_notes='Photo auto-sourced via Google image search',
                updated_at=CURRENT_TIMESTAMP
            WHERE merkey=?
        """, (merkey,))

        stats["uploaded"] += 1
        stats["processed"] += 1
        conn.commit()  # commit every success — no work lost on interruption

        print(f"  [{i:>5}/{total}] {merkey} | {desc[:35]:<35} | {query[:28]} -> OK ({best_w}x{best_h})", flush=True)

        if i % 50 == 0:
            pct = i / total * 100
            print(f"\n  --- Progress: {i}/{total} ({pct:.1f}%)  "
                  f"uploaded={stats['uploaded']}  "
                  f"no_results={stats['no_results']}  "
                  f"dl_failed={stats['download_failed']} ---\n", flush=True)

        # Periodic cooldown every BATCH_PAUSE_EVERY products
        if i % BATCH_PAUSE_EVERY == 0 and i < total:
            print(f"\n  [COOLDOWN] {i} done — pausing {BATCH_PAUSE_SECS//60} min to avoid rate limits...", flush=True)
            time.sleep(BATCH_PAUSE_SECS)
            http_session = requests.Session()
            print("  [COOLDOWN] Resuming.\n", flush=True)

        time.sleep(random.uniform(SEARCH_DELAY_MIN, SEARCH_DELAY_MAX))

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

    p_skip = sub.add_parser("mark-skip")
    p_skip.add_argument("--auto",    action="store_true",
                        help="Apply pattern-based auto-detection (tobacco, bulk, fresh)")
    p_skip.add_argument("--merkeys", type=str, default=None,
                        help="Comma-separated merkeys to mark (e.g. 9326,9270)")
    p_skip.add_argument("--dry-run", action="store_true")

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
    elif args.cmd == "mark-skip":
        merkeys_list = [m for m in args.merkeys.split(",") if m.strip()] if args.merkeys else []
        cmd_mark_skip(args.auto, merkeys_list, args.dry_run)
    elif args.cmd == "run":
        cmd_run(args.limit, args.dry_run, args.merkey,
                remove_bg=not args.no_remove_bg)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
