from __future__ import annotations

import argparse
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
SOURCE_DB = BASE_DIR.parent / "SQLite" / "anson_products.db"
STOREFRONT_DB = BASE_DIR.parent / "Storefront" / "storefront_catalog.db"
PUBLISH_SCRIPT = BASE_DIR.parent / "Storefront" / "publish_storefront_catalog.py"

WI_ESC_CANDIDATES = [
    Path(r"D:\Projects\AnsonSupermart-PO-Workbench\apps\api\.staging\import-snapshots\WI_ESC.FPB"),
    Path(r"\\anson_server\ssims\SSIMS\WI_ESC.FPB"),
    Path(r"D:\Projects\SSIMS_DATA\live\WI_ESC.FPB"),
]
WI_SDR_CANDIDATES = [
    Path(r"\\anson_server\ssims\SSIMS\WI_SDR.FPB"),
    Path(r"D:\Projects\SSIMS_DATA\live\WI_SDR.FPB"),
]

RELEVANT_AUDIT_ACTIONS = {
    "product_update",
    "product_note",
    "photo_upload",
    "pack_photo_upload",      # pack/box images show on the storefront too
    "bulk_update",            # bulk edits change storefront-visible fields
    "product_restore",
    "purge_pending_deletion",
}


def first_existing_path(candidates: list[Path]) -> Path | None:
    for path in candidates:
        if path.exists():
            return path
    return None


def parse_timestamp(value: str | None) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def get_last_relevant_edit(source_db: Path) -> tuple[datetime | None, str | None]:
    conn = sqlite3.connect(source_db)
    try:
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" for _ in RELEVANT_AUDIT_ACTIONS)
        row = conn.execute(
            f"""
            SELECT MAX(created_at) AS last_edit
            FROM audit_log
            WHERE action_type IN ({placeholders})
            """,
            tuple(sorted(RELEVANT_AUDIT_ACTIONS)),
        ).fetchone()
        last_edit = row["last_edit"] if row else None
        return parse_timestamp(last_edit), last_edit
    finally:
        conn.close()


def get_last_storefront_publish(storefront_db: Path) -> tuple[datetime | None, str | None]:
    conn = sqlite3.connect(storefront_db)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT value FROM storefront_metadata WHERE key='last_publish_at'"
        ).fetchone()
        raw_value = row["value"] if row else None
        return parse_timestamp(raw_value), raw_value
    finally:
        conn.close()


def publish_storefront(source_db: Path, storefront_db: Path) -> subprocess.CompletedProcess[str]:
    wi_esc_path = first_existing_path(WI_ESC_CANDIDATES)
    wi_sdr_path = first_existing_path(WI_SDR_CANDIDATES)

    command = [
        sys.executable,
        str(PUBLISH_SCRIPT),
        "--source-db",
        str(source_db),
        "--target-db",
        str(storefront_db),
    ]
    if wi_esc_path:
        command.extend(["--wi-esc", str(wi_esc_path)])
    if wi_sdr_path:
        command.extend(["--wi-sdr", str(wi_sdr_path)])

    return subprocess.run(
        command,
        cwd=str(BASE_DIR.parent),
        capture_output=True,
        text=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Publish the storefront only when encoder edits are newer than the last publish."
    )
    parser.add_argument("--source-db", default=str(SOURCE_DB))
    parser.add_argument("--storefront-db", default=str(STOREFRONT_DB))
    args = parser.parse_args()

    source_db = Path(args.source_db)
    storefront_db = Path(args.storefront_db)

    last_edit_dt, last_edit_raw = get_last_relevant_edit(source_db)
    last_publish_dt, last_publish_raw = get_last_storefront_publish(storefront_db)

    print(f"Latest relevant encoder edit: {last_edit_raw or 'none'}")
    print(f"Last storefront publish: {last_publish_raw or 'none'}")

    if last_edit_dt is None:
        print("No relevant encoder edits found. Skipping storefront publish.")
        return 0

    if last_publish_dt is not None and last_edit_dt <= last_publish_dt:
        print("No new encoder edits since the last storefront publish. Skipping.")
        return 0

    print("New encoder edits detected. Publishing storefront catalog...")
    completed = publish_storefront(source_db, storefront_db)
    output = "\n".join(
        part.strip()
        for part in (completed.stdout, completed.stderr)
        if part and part.strip()
    ).strip()
    if output:
        print(output)

    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
