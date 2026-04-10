#!/usr/bin/env python3
"""
Anson Supermart - Automated Daily Sync
Checks MP_MER files for changes and runs sync -> export -> optional Sheets upload.

Usage:
    python daily_sync.py

Run this daily via Windows Task Scheduler.
"""

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent

# Configuration
CONFIG = {
    "mp_mer_path": "/mnt/ssims/SSIMS/MP_MER.FPB",
    "mp_sup_path": "/mnt/ssims/SSIMS/MP_SUP.FPB",
    "mp_cls_path": "/mnt/ssims/SSIMS/MP_CLS.FPB",
    "copy_source_files": True,
    "local_source_dir": ["Data", "SourceCache"],
    "db_path": ["SQLite", "anson_products.db"],
    "last_sync_file": ".last_mp_mer_sync.json",
    "output_csv": "AWESOME_TABLE_PRODUCTS_UPDATED.csv",
    "image_base_url": "https://s3-ap-southeast-1.amazonaws.com/ansonsupermart.com/images/",
    "sqlite_enrichment_db": ["SQLite", "anson_products.db"],
    "enable_brand_prefix_rules": True,
    "brand_prefix_rules_csv": ["Data", "Taxonomy", "brand_prefix_rules.csv"],
    "enable_price_export": True,
    "enable_sheets_upload": True,
    "google_sheets": {
        "spreadsheet_id": "1s8mLpfLyUF_hppt86GJmFyselqT-OFem5qllgFZagAQ",
        "worksheet_name": "Products",
        "credentials_json": ["Config", "google-service-account.json"],
        "start_row": 3,
        "replace_header": False,
    },
}


def resolve_project_path(value, base_dir=SCRIPT_DIR.parent):
    if isinstance(value, (list, tuple)):
        return str((base_dir.joinpath(*value)).resolve())
    return str((base_dir / value).resolve())


def sheets_upload_enabled() -> bool:
    override = os.environ.get("CATALOG_SHEETS_UPLOAD", "").strip().lower()
    if override in {"0", "false", "no", "off"}:
        return False
    if override in {"1", "true", "yes", "on"}:
        return True
    return CONFIG.get("enable_sheets_upload", False)


def get_file_info(filepath):
    """Get file modification time and size."""
    if not os.path.exists(filepath):
        return None

    stat = os.stat(filepath)
    return {
        "mtime": stat.st_mtime,
        "size": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
    }


def load_last_sync(last_sync_file):
    if not os.path.exists(last_sync_file):
        return {}
    try:
        with open(last_sync_file, "r", encoding="utf-8") as f:
            payload = json.load(f)
            files = payload.get("files", {})
            if files:
                normalized = {}
                for path, info in files.items():
                    normalized[os.path.normcase(os.path.normpath(path))] = info
                payload["files"] = normalized
            return payload
    except Exception:
        return {}


def has_any_file_changed(filepaths, last_sync_file):
    """Check if any tracked source file changed since last sync."""
    snapshot = {}
    changed_files = []

    last_info = load_last_sync(last_sync_file)
    old_files = last_info.get("files", {})

    # Backward compatibility with old format
    if not old_files and "mtime" in last_info and filepaths:
        old_files[filepaths[0]] = {
            "mtime": last_info.get("mtime", 0),
            "size": last_info.get("size", 0),
            "modified": last_info.get("modified", ""),
        }

    normalized_current = {}

    for path in filepaths:
        current = get_file_info(path)
        if not current:
            continue

        normalized_path = os.path.normcase(os.path.normpath(path))
        normalized_current[normalized_path] = path
        snapshot[path] = current
        previous = old_files.get(normalized_path, {})
        if not previous:
            basename = os.path.basename(path)
            for old_path, old_info in old_files.items():
                if os.path.basename(old_path) == basename:
                    previous = old_info
                    break
        prev_mtime = previous.get("mtime", 0)
        prev_size = previous.get("size", 0)

        if current["mtime"] > prev_mtime or current["size"] != prev_size:
            changed_files.append(path)

    return len(changed_files) > 0, changed_files, snapshot


def save_sync_info(file_snapshot, last_sync_file):
    """Save tracked file info after successful sync."""
    normalized_snapshot = {
        os.path.normcase(os.path.normpath(path)): info
        for path, info in file_snapshot.items()
    }
    payload = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "files": normalized_snapshot,
    }
    with open(last_sync_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def run_command(name, cmd):
    print(f"Running {name}...")
    print(f"Command: {' '.join(cmd)}")
    print()
    result = subprocess.run(cmd, capture_output=False)
    return result.returncode == 0


def copy_source_file(source_path, local_dir):
    local_dir.mkdir(parents=True, exist_ok=True)
    destination = local_dir / Path(source_path).name
    shutil.copy2(source_path, destination)
    return str(destination.resolve())


def run_sync(mp_mer_path, db_path, mp_sup_path="", mp_cls_path=""):
    cmd = [
        sys.executable,
        str(SCRIPT_DIR / "sync_mp_mer.py"),
        mp_mer_path,
        "--db",
        db_path,
    ]
    if mp_sup_path:
        cmd.extend(["--mp-sup", mp_sup_path])
    if mp_cls_path:
        cmd.extend(["--mp-cls", mp_cls_path])
    return run_command("database sync", cmd)


def run_price_export(mp_mer_path, output_csv, image_base_url, sqlite_enrichment_db):
    cmd = [
        sys.executable,
        str(SCRIPT_DIR / "update_prices_for_awesome_table.py"),
        "--mp-mer",
        mp_mer_path,
        "--output",
        output_csv,
        "--image-base-url",
        image_base_url,
        "--sqlite-db",
        sqlite_enrichment_db,
    ]

    return run_command("price export", cmd)


def run_brand_prefix_rules(db_path, rules_csv):
    cmd = [
        sys.executable,
        str(SCRIPT_DIR / "apply_brand_prefix_rules.py"),
        "--db",
        db_path,
        "--rules",
        rules_csv,
        "--apply",
    ]
    return run_command("brand prefix rules", cmd)


def run_sheets_upload(csv_path, sheets_cfg):
    spreadsheet_id = sheets_cfg.get("spreadsheet_id", "")
    credentials_json = resolve_project_path(sheets_cfg.get("credentials_json", "")) if sheets_cfg.get("credentials_json") else ""

    if not spreadsheet_id or spreadsheet_id == "YOUR_SPREADSHEET_ID_HERE":
        print("Google Sheets upload is enabled but spreadsheet_id is not configured.")
        return False

    if not credentials_json or credentials_json == "YOUR_SERVICE_ACCOUNT_JSON_PATH":
        print("Google Sheets upload is enabled but credentials_json is not configured.")
        return False

    cmd = [
        sys.executable,
        str(SCRIPT_DIR / "upload_to_google_sheets.py"),
        "--csv",
        csv_path,
        "--spreadsheet-id",
        spreadsheet_id,
        "--worksheet-name",
        sheets_cfg.get("worksheet_name", "Products"),
        "--credentials-json",
        credentials_json,
        "--start-row",
        str(sheets_cfg.get("start_row", 3)),
    ]

    if sheets_cfg.get("replace_header", False):
        cmd.append("--replace-header")

    return run_command("Google Sheets upload", cmd)


def main():
    print("=" * 80)
    print("ANSON SUPERMART - DAILY AUTOMATED SYNC")
    print("=" * 80)
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    mp_mer_path = CONFIG["mp_mer_path"]
    mp_sup_path = CONFIG.get("mp_sup_path", "")
    mp_cls_path = CONFIG.get("mp_cls_path", "")
    copy_source_files = CONFIG.get("copy_source_files", False)
    local_source_dir = Path(resolve_project_path(CONFIG.get("local_source_dir", ["Data", "SourceCache"]), SCRIPT_DIR))
    db_path = resolve_project_path(CONFIG["db_path"])
    sqlite_enrichment_db = resolve_project_path(CONFIG["sqlite_enrichment_db"])
    last_sync_file = str((SCRIPT_DIR / CONFIG["last_sync_file"]).resolve())
    output_csv = str((SCRIPT_DIR / CONFIG["output_csv"]).resolve())

    tracked_files = [mp_mer_path]
    if mp_sup_path:
        tracked_files.append(mp_sup_path)
    if mp_cls_path:
        tracked_files.append(mp_cls_path)

    print("Tracking source files:")
    for path in tracked_files:
        info = get_file_info(path)
        if not info:
            print(f"  - Missing/unreachable: {path}")
        else:
            print(f"  - {path}")
            print(f"    modified: {info['modified']} | size: {info['size']:,} bytes")
    print()
    print(f"Database path: {db_path}")
    print(f"SQLite enrichment path: {sqlite_enrichment_db}")
    print()

    if not get_file_info(mp_mer_path):
        print("ERROR: MP_MER.FPB not found or not accessible")
        print(f"Current path: {mp_mer_path}")
        sys.exit(1)

    changed, changed_files, snapshot = has_any_file_changed(tracked_files, last_sync_file)

    if not changed:
        print("No changes detected - sync not needed")
        last_info = load_last_sync(last_sync_file)
        if last_info.get("updated_at"):
            print(f"Last sync: {last_info['updated_at']}")
        print()
        sys.exit(0)

    print("Changes detected in:")
    for path in changed_files:
        print(f"  - {path}")
    print()

    working_mp_mer_path = mp_mer_path
    working_mp_sup_path = mp_sup_path
    working_mp_cls_path = mp_cls_path

    if copy_source_files:
        print("Copying source files to local cache...")
        working_mp_mer_path = copy_source_file(mp_mer_path, local_source_dir)
        print(f"  MP_MER copied to: {working_mp_mer_path}")
        if mp_sup_path and get_file_info(mp_sup_path):
            working_mp_sup_path = copy_source_file(mp_sup_path, local_source_dir)
            print(f"  MP_SUP copied to: {working_mp_sup_path}")
        elif mp_sup_path:
            print(f"  MP_SUP not copied (missing/unreachable): {mp_sup_path}")
        if mp_cls_path and get_file_info(mp_cls_path):
            working_mp_cls_path = copy_source_file(mp_cls_path, local_source_dir)
            print(f"  MP_CLS copied to: {working_mp_cls_path}")
        elif mp_cls_path:
            print(f"  MP_CLS not copied (missing/unreachable): {mp_cls_path}")
        print()

    # Step 1: DB sync
    if not run_sync(working_mp_mer_path, db_path, working_mp_sup_path, working_mp_cls_path):
        print("ERROR: database sync failed")
        sys.exit(1)

    # Step 2: Post-sync brand normalization rules
    if CONFIG.get("enable_brand_prefix_rules", True):
        rules_csv = resolve_project_path(CONFIG["brand_prefix_rules_csv"])
        if not run_brand_prefix_rules(db_path, rules_csv):
            print("ERROR: brand prefix rule application failed")
            sys.exit(1)
    else:
        print("Brand prefix rules disabled by config")

    # Step 3: Awesome Table CSV export
    if CONFIG.get("enable_price_export", True):
        if not run_price_export(
            working_mp_mer_path,
            output_csv,
            CONFIG["image_base_url"],
            sqlite_enrichment_db,
        ):
            print("ERROR: price export failed")
            sys.exit(1)
    else:
        print("Price export disabled by config")

    # Step 4: Google Sheets upload
    if sheets_upload_enabled():
        if not run_sheets_upload(output_csv, CONFIG.get("google_sheets", {})):
            print("ERROR: Google Sheets upload failed")
            sys.exit(1)
    else:
        print("Google Sheets upload disabled by config")

    save_sync_info(snapshot, last_sync_file)

    print()
    print("=" * 80)
    print("SYNC SUCCESSFUL")
    print("=" * 80)
    print(f"Sync marker saved: {last_sync_file}")
    print(f"Latest CSV: {output_csv}")
    print()
    sys.exit(0)


if __name__ == "__main__":
    main()
