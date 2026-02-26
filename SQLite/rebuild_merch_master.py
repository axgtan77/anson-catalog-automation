#!/usr/bin/env python3
"""
rebuild_merch_master.py
Anson Supermart - Reconcile MERCH_MASTER.csv with current MP_MER.FPB

What it does:
  1. Reads all rows from the existing MERCH_MASTER.csv (to preserve enrichment columns:
     Brand, Name, Description, Size, Photo, Sub-Department, Department, GP%, etc.)
  2. Reads all records from the current MP_MER.FPB (source of truth for raw fields)
  3. Writes a new MERCH_MASTER.csv that:
       - Keeps only MERKEYs present in MP_MER.FPB  (deleted products are dropped)
       - Updates all shared raw FoxPro fields from MP_MER.FPB
       - Preserves enrichment columns from the existing CSV for known MERKEYs
       - Adds new MERKEYs (in MP_MER but not yet in CSV) with empty enrichment columns
  4. Backs up the original MERCH_MASTER.csv before overwriting

Usage:
    python rebuild_merch_master.py
    python rebuild_merch_master.py --fpb "../Data/MP_MER.FPB" --csv "../Data/MERCH_MASTER.csv"
    python rebuild_merch_master.py --dry-run   (prints stats only, no files written)
"""

import argparse
import csv
import os
import shutil
import struct
from datetime import datetime
from pathlib import Path

# --- Defaults ---
DEFAULT_FPB = str(Path(__file__).parent.parent / "Data" / "MP_MER.FPB")
DEFAULT_CSV = str(Path(__file__).parent.parent / "Data" / "MERCH_MASTER.csv")

# Columns that come from MP_MER.FPB (raw FoxPro fields shared with MERCH_MASTER.csv).
# These will be overwritten from the FPB on every rebuild.
FPB_RAW_FIELDS = {
    "MEANCS", "MEANBX", "MEAN13", "MEDESC", "MECLAS", "MEADOC", "MEBRAC",
    "MEVATA", "MENVAT", "MEMSEQ", "MEPOSD", "MEPCK1", "MEQTY1", "MECOS0",
    "MEPACK", "MEMUCH", "MEPCK3", "MECOS1", "MECOS2", "MEDIS0", "MEDIS1",
    "MEDIS2", "MEDIS3", "MEEVAT", "MEOCHG", "MEXPEN", "MEMK12", "MEMK22",
    "MERET2", "ME2DRT", "MERDI2", "MEMK1R", "MEMK2R", "MERETP", "MERDRT",
    "MERDIS", "MERETU", "MERETD", "MEMK1W", "MEMK2W", "MEWHOP", "MEWDRT",
    "MEWDIS", "MEWHOU", "MEWHOD", "MEMINI", "MEMAKR", "MEMAKW", "CLRKEY",
    "SURKEY", "SURKY2", "USDLAR", "USPESO", "OTHERS", "MOPCK1", "MOQTY1",
    "MOCOS0", "MOXPEN", "MOPACK", "MOMUCH", "MOCOS1", "MOPCK3", "MEPRIM",
    "MOCOS2", "BARCD1", "BARCD2", "BARCD3", "BARCD4", "GNETPT", "BARCD5",
    "USERID", "USRDAT", "USRFUN",
}


# ---------------------------------------------------------------------------
# Minimal FoxPro DBF reader (no external dependencies)
# ---------------------------------------------------------------------------
class FoxProDBF:
    def __init__(self, filename):
        self.filename = filename
        self.file = open(filename, "rb")
        self._read_header()
        self._read_field_descriptors()

    def _read_header(self):
        header = self.file.read(32)
        self.num_records = struct.unpack("<I", header[4:8])[0]
        self.header_length = struct.unpack("<H", header[8:10])[0]
        self.record_length = struct.unpack("<H", header[10:12])[0]

    def _read_field_descriptors(self):
        self.fields = []
        self.file.seek(32)
        while True:
            fd = self.file.read(32)
            if not fd or len(fd) < 32 or fd[0] == 0x0D:
                break
            self.fields.append({
                "name": fd[0:11].decode("latin1", errors="ignore").strip("\x00").strip(),
                "type": chr(fd[11]),
                "length": fd[16],
                "decimals": fd[17],
            })
        self.data_start = self.header_length

    def __iter__(self):
        self.file.seek(self.data_start)
        for _ in range(self.num_records):
            rec = self.file.read(self.record_length)
            if not rec or len(rec) < self.record_length:
                break
            if rec[0:1] == b"*":   # deleted
                continue
            row = {}
            offset = 1
            for f in self.fields:
                raw = rec[offset: offset + f["length"]]
                offset += f["length"]
                text = raw.decode("latin1", errors="ignore").strip()
                if f["type"] == "N":
                    if text:
                        try:
                            row[f["name"]] = float(text) if f["decimals"] > 0 else int(text)
                        except ValueError:
                            row[f["name"]] = None
                    else:
                        row[f["name"]] = None
                elif f["type"] == "D":
                    if text and len(text) == 8:
                        try:
                            row[f["name"]] = datetime.strptime(text, "%Y%m%d").strftime("%Y-%m-%d")
                        except ValueError:
                            row[f["name"]] = ""
                    else:
                        row[f["name"]] = ""
                else:
                    row[f["name"]] = text
            yield row

    def close(self):
        self.file.close()

    def __enter__(self): return self
    def __exit__(self, *a): self.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def rebuild(fpb_path: str, csv_path: str, dry_run: bool = False):
    print("=" * 70)
    print("MERCH_MASTER.csv REBUILD")
    print("=" * 70)
    print(f"Source FPB : {fpb_path}")
    print(f"Target CSV : {csv_path}")
    print(f"Dry run    : {dry_run}")
    print()

    if not os.path.exists(fpb_path):
        raise FileNotFoundError(f"MP_MER.FPB not found: {fpb_path}")
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"MERCH_MASTER.csv not found: {csv_path}")

    # --- 1. Read existing MERCH_MASTER.csv ---
    print("Reading existing MERCH_MASTER.csv...")
    existing: dict[str, dict] = {}
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        original_headers = reader.fieldnames or []
        for row in reader:
            mk = (row.get("MERKEY") or "").strip()
            if mk:
                existing[mk] = row
    print(f"  {len(existing):,} rows loaded  |  {len(original_headers)} columns")

    # Identify enrichment-only columns (those NOT in FPB_RAW_FIELDS and not MERKEY/RECORD#)
    enrichment_cols = [
        h for h in original_headers
        if h not in FPB_RAW_FIELDS and h not in ("MERKEY", "RECORD#")
    ]
    print(f"  Enrichment columns preserved: {enrichment_cols}")
    print()

    # --- 2. Read MP_MER.FPB ---
    print("Reading MP_MER.FPB...")
    fpb_records: dict[str, dict] = {}
    with FoxProDBF(fpb_path) as dbf:
        for row in dbf:
            mk = str(row.get("MERKEY") or "").strip()
            if mk:
                fpb_records[mk] = row
    print(f"  {len(fpb_records):,} records loaded")
    print()

    # --- 3. Compute diff stats ---
    existing_keys = set(existing.keys())
    fpb_keys = set(fpb_records.keys())

    removed = existing_keys - fpb_keys
    added   = fpb_keys - existing_keys
    kept    = existing_keys & fpb_keys

    print(f"  Kept (in both)    : {len(kept):,}")
    print(f"  New (FPB only)    : {len(added):,}")
    print(f"  Dropped (CSV only): {len(removed):,}  ← these are removed from output")
    print()

    if dry_run:
        print("Dry run — no files written.")
        return

    # --- 4. Build output rows ---
    # Preserve original column order; RECORD# will be renumbered
    out_headers = original_headers[:]  # same columns as original

    output_rows = []
    record_num = 1

    for mk, fpb_row in fpb_records.items():
        # Start from existing row (preserves enrichment) or blank dict
        base = dict(existing[mk]) if mk in existing else {h: "" for h in out_headers}

        # Update raw FoxPro fields from FPB
        for field in FPB_RAW_FIELDS:
            if field in out_headers and field in fpb_row:
                val = fpb_row[field]
                base[field] = "" if val is None else str(val)

        # Always refresh MERKEY from FPB
        base["MERKEY"] = mk

        # Renumber RECORD#
        if "RECORD#" in out_headers:
            base["RECORD#"] = str(record_num)

        output_rows.append(base)
        record_num += 1

    # --- 5. Backup original ---
    backup_path = csv_path.replace(".csv", f"_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
    shutil.copy2(csv_path, backup_path)
    print(f"Backup saved: {backup_path}")

    # --- 6. Write new CSV ---
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=out_headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(output_rows)

    print(f"Written: {csv_path}")
    print(f"  Rows written: {len(output_rows):,}")
    print()
    print("=" * 70)
    print("REBUILD COMPLETE")
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(description="Rebuild MERCH_MASTER.csv from current MP_MER.FPB")
    parser.add_argument("--fpb", default=DEFAULT_FPB, help="Path to MP_MER.FPB")
    parser.add_argument("--csv", default=DEFAULT_CSV, help="Path to MERCH_MASTER.csv")
    parser.add_argument("--dry-run", action="store_true", help="Show stats only, don't write files")
    args = parser.parse_args()

    rebuild(args.fpb, args.csv, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
