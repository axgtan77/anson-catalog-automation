from __future__ import annotations
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, session, g
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
import os, re
import subprocess
import sys
from urllib.parse import urlencode
from functools import wraps
from zoneinfo import ZoneInfo

from werkzeug.security import generate_password_hash, check_password_hash

from image_pipeline import process_to_white_bg
from s3_upload import upload_file_to_s3

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "anson-encoder-dev")

BASE_DIR = Path(__file__).resolve().parent
CATALOG_AUTOMATION_DIR = BASE_DIR.parent
DB_PATH = os.environ.get("ANSON_DB_PATH", str(BASE_DIR / "anson_products.db"))
STOREFRONT_DIR = CATALOG_AUTOMATION_DIR / "Storefront"
STOREFRONT_PUBLISH_SCRIPT = STOREFRONT_DIR / "publish_storefront_catalog.py"
STOREFRONT_TARGET_DB = STOREFRONT_DIR / "storefront_catalog.db"
STORE_FRONT_WI_ESC_CANDIDATES = [
    Path("/mnt/ssims/SSIMS/WI_ESC.FPB"),
    Path("/mnt/ssims/SSIMS/WI_ESC.FPB"),
    Path("/mnt/ssims/SSIMS/WI_ESC.FPB"),
]
STORE_FRONT_WI_SDR_CANDIDATES = [
    Path("/mnt/ssims/SSIMS/WI_SDR.FPB"),
    Path("/mnt/ssims/SSIMS/WI_SDR.FPB"),
]
UPLOAD_DIR = Path(os.environ.get("WEB_ENCODER_UPLOAD_DIR", str(BASE_DIR / "uploads")))
ORIG_DIR = UPLOAD_DIR / "original"
PROC_DIR = UPLOAD_DIR / "processed"
ORIG_DIR.mkdir(parents=True, exist_ok=True)
PROC_DIR.mkdir(parents=True, exist_ok=True)

S3_BUCKET = "ansonsupermart.com"
S3_PREFIX = "images/"
S3_REGION = "ap-southeast-1"
ADMIN_BOOTSTRAP_USERNAME = os.environ.get("ANSON_ADMIN_USERNAME", "admin")
ADMIN_BOOTSTRAP_PASSWORD = os.environ.get("ANSON_ADMIN_PASSWORD", "ChangeMe123!")
UNIT_OPTIONS = ["g", "kg", "mg", "ml", "L", "pcs", "pc", "pack", "packs", "set", "s"]
FORMAL_UNITS = {"g", "kg", "mg", "ml", "L", "s"}
PHT = ZoneInfo("Asia/Manila")

def ensure_runtime_schema():
    """Small runtime migrations needed by the web UI."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(products)")
    cols = {r[1] for r in cur.fetchall()}
    changed = False
    if "supplier_code" not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN supplier_code TEXT")
        changed = True
    if "supplier_name" not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN supplier_name TEXT")
        changed = True
    class_cols = {
        "clrkey": "TEXT",
        "class_l1_code": "TEXT",
        "class_l1_name": "TEXT",
        "class_l2_code": "TEXT",
        "class_l2_name": "TEXT",
        "class_l3_code": "TEXT",
        "class_l3_name": "TEXT",
    }
    for col, coltype in class_cols.items():
        if col not in cols:
            cur.execute(f"ALTER TABLE products ADD COLUMN {col} {coltype}")
            changed = True
    if "availability_override" not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN availability_override TEXT DEFAULT 'AUTO'")
        changed = True
    if "alpha_override" not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN alpha_override TEXT DEFAULT 'AUTO'")
        changed = True
    if "show_pack_on_storefront" not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN show_pack_on_storefront INTEGER DEFAULT 0")
        changed = True
    if "pack_display_label" not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN pack_display_label TEXT")
        changed = True
    if "pack_photo_url" not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN pack_photo_url TEXT")
        changed = True
    if "pack_barcode" not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN pack_barcode TEXT")
        changed = True
    if "show_case_on_storefront" not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN show_case_on_storefront INTEGER DEFAULT 0")
        changed = True
    if "case_display_label" not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN case_display_label TEXT")
        changed = True
    if "case_barcode" not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN case_barcode TEXT")
        changed = True
    if "case_quantity" not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN case_quantity INTEGER")
        changed = True
    if "case_photo_url" not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN case_photo_url TEXT")
        changed = True
    cur.execute("PRAGMA table_info(images)")
    image_cols = {r[1] for r in cur.fetchall()}
    image_runtime_cols = {
        "public_status": "TEXT",
        "public_status_code": "INTEGER",
        "public_checked_at": "TEXT",
        "public_error": "TEXT",
    }
    for col, coltype in image_runtime_cols.items():
        if col not in image_cols:
            cur.execute(f"ALTER TABLE images ADD COLUMN {col} {coltype}")
            changed = True
    cur.execute(
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
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            full_name TEXT,
            role TEXT NOT NULL DEFAULT 'encoder',
            is_active INTEGER NOT NULL DEFAULT 1,
            must_change_password INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_login_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            merkey TEXT,
            action_type TEXT NOT NULL,
            field_name TEXT,
            old_value TEXT,
            new_value TEXT,
            details TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_merkey ON audit_log(merkey, created_at DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_user ON audit_log(username, created_at DESC)")
    cur.execute("SELECT id FROM users WHERE username=?", (ADMIN_BOOTSTRAP_USERNAME,))
    if not cur.fetchone():
        cur.execute(
            """
            INSERT INTO users(username, password_hash, full_name, role, is_active, must_change_password)
            VALUES(?,?,?,?,1,1)
            """,
            (
                ADMIN_BOOTSTRAP_USERNAME,
                generate_password_hash(ADMIN_BOOTSTRAP_PASSWORD),
                "System Administrator",
                "admin",
            ),
        )
        changed = True
    if changed:
        conn.commit()
    conn.close()

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def current_user():
    return getattr(g, "current_user", None)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            next_url = request.full_path if request.query_string else request.path
            return redirect(url_for("login", next=next_url))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user or user.get("role") != "admin":
            flash("Admin access required.", "error")
            return redirect(url_for("index"))
        return view(*args, **kwargs)
    return wrapped


def append_note(existing: str | None, note: str | None) -> str:
    existing = (existing or "").strip()
    note = (note or "").strip()
    if not note:
        return existing
    if not existing:
        return note
    if note in existing:
        return existing
    return f"{existing}; {note}"


def compose_description(brand_name: str | None, name: str | None, size: str | None) -> str:
    parts = []
    for value in (brand_name, name, size):
        text = (value or "").strip()
        if text:
            parts.append(text)
    return " ".join(parts).upper()


def clean_measure_value(value: str | None) -> str:
    text = (value or "").strip()
    if not text or text.upper() == "#VALUE!":
        return ""
    return text


def normalize_unit(unit: str | None) -> str:
    text = clean_measure_value(unit).lower()
    mapping = {
        "g": "g",
        "gm": "g",
        "grams": "g",
        "kg": "kg",
        "k": "kg",
        "mg": "mg",
        "ml": "ml",
        "l": "L",
        "lt": "L",
        "ltr": "L",
        "liter": "L",
        "liters": "L",
        "pcs": "pcs",
        "pc": "pc",
        "piece": "pc",
        "pieces": "pcs",
        "pack": "pack",
        "packs": "packs",
        "set": "set",
        "s": "s",
    }
    return mapping.get(text, clean_measure_value(unit))


def format_measure_value(value: str | None) -> str:
    text = clean_measure_value(value)
    if not text:
        return ""
    try:
        num = float(text)
    except ValueError:
        return text
    if num.is_integer():
        return str(int(num))
    return f"{num:.3f}".rstrip("0").rstrip(".")


def compose_size_from_parts(weight_volume: str | None, unit: str | None) -> str:
    value = format_measure_value(weight_volume)
    unit_norm = normalize_unit(unit)
    if not value or not unit_norm:
        return ""
    joiner = "" if unit_norm in FORMAL_UNITS else " "
    return f"{value}{joiner}{unit_norm}"


def normalize_size_text(size: str | None) -> str:
    size_text = clean_measure_value(size)
    if not size_text:
        return ""
    match = re.match(r"^\s*(\d+(?:\.\d+)?)\s*([A-Za-z]+)\s*$", size_text)
    if not match:
        return size_text
    value = format_measure_value(match.group(1))
    unit_norm = normalize_unit(match.group(2))
    if unit_norm not in UNIT_OPTIONS:
        return size_text
    return compose_size_from_parts(value, unit_norm)


def infer_size_parts(size: str | None, weight_volume: str | None, unit: str | None) -> tuple[str, str]:
    value = format_measure_value(weight_volume)
    unit_norm = normalize_unit(unit)
    if value and unit_norm:
        return value, unit_norm

    size_text = clean_measure_value(size)
    if not size_text:
        return "", ""

    match = re.match(r"^\s*(\d+(?:\.\d+)?)\s*([A-Za-z]+)\s*$", size_text)
    if not match:
        return "", ""

    inferred_value = format_measure_value(match.group(1))
    inferred_unit = normalize_unit(match.group(2))
    if inferred_unit not in UNIT_OPTIONS:
        return "", ""
    return inferred_value, inferred_unit


def first_existing_path(candidates: list[Path]) -> Path | None:
    for path in candidates:
        if path.exists():
            return path
    return None


def run_storefront_publish() -> tuple[bool, str]:
    wi_esc_path = first_existing_path(STORE_FRONT_WI_ESC_CANDIDATES)
    wi_sdr_path = first_existing_path(STORE_FRONT_WI_SDR_CANDIDATES)

    command = [
        sys.executable,
        str(STOREFRONT_PUBLISH_SCRIPT),
        "--source-db",
        str(DB_PATH),
        "--target-db",
        str(STOREFRONT_TARGET_DB),
    ]
    if wi_esc_path:
        command.extend(["--wi-esc", str(wi_esc_path)])
    if wi_sdr_path:
        command.extend(["--wi-sdr", str(wi_sdr_path)])

    completed = subprocess.run(
        command,
        cwd=str(CATALOG_AUTOMATION_DIR),
        capture_output=True,
        text=True,
    )
    output = "\n".join(
        part.strip()
        for part in (completed.stdout, completed.stderr)
        if part and part.strip()
    ).strip()
    if completed.returncode == 0:
        return True, output or "Storefront publish completed."
    return False, output or "Storefront publish failed."


def log_audit(cur: sqlite3.Cursor, action_type: str, merkey: str | None = None, field_name: str | None = None,
              old_value: object | None = None, new_value: object | None = None, details: str | None = None,
              user_id: int | None = None, username: str | None = None):
    user = current_user()
    resolved_user_id = user_id
    resolved_username = username
    if resolved_user_id is None and user:
        resolved_user_id = user["id"]
    if resolved_username is None and user:
        resolved_username = user["username"]
    cur.execute(
        """
        INSERT INTO audit_log(user_id, username, merkey, action_type, field_name, old_value, new_value, details)
        VALUES(?,?,?,?,?,?,?,?)
        """,
        (
            resolved_user_id,
            resolved_username,
            merkey,
            action_type,
            field_name,
            None if old_value is None else str(old_value),
            None if new_value is None else str(new_value),
            details,
        ),
    )

def slugify(text: str) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s/]+", "-", text)
    text = text.replace("&", "and")
    text = re.sub(r"-{2,}", "-", text)
    return text.strip("-")

def compute_quality(description: str, name: str, brand_id: int|None, category_id: int|None, size: str) -> tuple[str,int]:
    issues = []
    if not (description or "").strip(): issues.append("NEEDS_DESCRIPTION")
    if not (name or "").strip(): issues.append("NEEDS_NAME")
    if not brand_id: issues.append("NEEDS_BRAND")
    if not category_id: issues.append("NEEDS_CATEGORY")
    if not (size or "").strip(): issues.append("NEEDS_SIZE")
    if not issues: return "COMPLETE", 0
    return issues[0], 1

PLACEHOLDER_URL = "https://s3-ap-southeast-1.amazonaws.com/ansonsupermart.com/images/ANSON-ONLINE-GROCERY-PLACEHOLDER.jpg"
BLANK_IMAGE_URL = "https://s3-ap-southeast-1.amazonaws.com/ansonsupermart.com/images/"

def get_primary_barcode(cur, merkey: str) -> str|None:
    cur.execute("SELECT barcode FROM barcodes WHERE merkey=? ORDER BY is_primary DESC, id ASC LIMIT 1", (merkey,))
    r = cur.fetchone()
    return r["barcode"] if r else None

def is_real_image_url(url: str|None) -> bool:
    if not url:
        return False
    if url in (PLACEHOLDER_URL, BLANK_IMAGE_URL):
        return False
    return len(url) > len(BLANK_IMAGE_URL)

def add_cache_buster(url: str | None, version: str | int | None) -> str | None:
    if not url:
        return url
    if not version:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}v={version}"

LIST_STATE_KEYS = [
    "scope",
    "filter",
    "supplier",
    "class_l1",
    "sort_by",
    "sort_col",
    "sort_dir",
    "search",
    "missing_photos",
    "per_page",
    "page",
]

def extract_list_state(src) -> dict[str, str]:
    state = {}
    for key in LIST_STATE_KEYS:
        val = src.get(key)
        if val is None:
            continue
        sval = str(val).strip()
        if sval == "":
            continue
        state[key] = sval
    return state


SYNC_LOG_DIRS = [
    CATALOG_AUTOMATION_DIR,
    CATALOG_AUTOMATION_DIR / "Scripts",
    BASE_DIR,
]


def resolve_sync_change_file(filename: str) -> Path | None:
    if not re.fullmatch(r"sync_changes_[0-9_]+\.txt", filename or ""):
        return None

    candidates: list[Path] = []
    for directory in SYNC_LOG_DIRS:
        path = directory / filename
        if path.exists() and path.is_file():
            candidates.append(path)

    if not candidates:
        return None

    return max(candidates, key=lambda p: p.stat().st_mtime)


def list_sync_change_files() -> list[dict]:
    latest_by_name: dict[str, Path] = {}
    for directory in SYNC_LOG_DIRS:
        for path in directory.glob("sync_changes_*.txt"):
            current = latest_by_name.get(path.name)
            if current is None or path.stat().st_mtime > current.stat().st_mtime:
                latest_by_name[path.name] = path

    files = []
    for path in sorted(latest_by_name.values(), key=lambda p: p.stat().st_mtime, reverse=True):
        stat = path.stat()
        files.append(
            {
                "name": path.name,
                "path": str(path),
                "updated_at": datetime.fromtimestamp(stat.st_mtime),
                "size_bytes": stat.st_size,
            }
        )
    return files


def read_sync_change_file(filename: str) -> dict | None:
    path = resolve_sync_change_file(filename)
    if path is None:
        return None

    stat = path.stat()
    return {
        "name": path.name,
        "path": str(path),
        "updated_at": datetime.fromtimestamp(stat.st_mtime),
        "size_bytes": stat.st_size,
        "content": path.read_text(encoding="utf-8", errors="replace"),
    }


def utc_to_pht(value) -> str:
    if not value:
        return "-"

    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            return "-"
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            try:
                dt = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return text

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(PHT).strftime("%Y-%m-%d %I:%M:%S %p PHT")


app.jinja_env.filters["pht"] = utc_to_pht


@app.before_request
def load_current_user():
    g.current_user = None
    user_id = session.get("user_id")
    if not user_id:
        return
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, username, full_name, role, is_active, must_change_password FROM users WHERE id=?",
        (user_id,),
    )
    row = cur.fetchone()
    conn.close()
    if not row or not row["is_active"]:
        session.clear()
        return
    g.current_user = dict(row)
    if (
        g.current_user.get("must_change_password")
        and request.endpoint
        and request.endpoint not in {"change_password", "logout", "static"}
    ):
        return redirect(url_for("change_password"))


@app.context_processor
def inject_auth_context():
    return {"current_user": current_user()}


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        next_url = (request.form.get("next") or "").strip()
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, username, password_hash, full_name, role, is_active, must_change_password FROM users WHERE username=?",
            (username,),
        )
        row = cur.fetchone()
        if row and row["is_active"] and check_password_hash(row["password_hash"], password):
            session.clear()
            session["user_id"] = row["id"]
            cur.execute("UPDATE users SET last_login_at=CURRENT_TIMESTAMP WHERE id=?", (row["id"],))
            log_audit(cur, "login", details="User login", user_id=row["id"], username=row["username"])
            conn.commit()
            conn.close()
            target = next_url or url_for("index")
            return redirect(target)
        conn.close()
        flash("Invalid username or password.", "error")
        return render_template("login.html", next_url=next_url)

    if current_user():
        return redirect(url_for("index"))
    return render_template("login.html", next_url=(request.args.get("next") or "").strip())


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    conn = get_db()
    cur = conn.cursor()
    log_audit(cur, "logout", details="User logout")
    conn.commit()
    conn.close()
    session.clear()
    flash("Logged out.", "success")
    return redirect(url_for("login"))


@app.route("/account/password", methods=["GET", "POST"])
@login_required
def change_password():
    user = current_user()
    if request.method == "POST":
        current_password = request.form.get("current_password") or ""
        new_password = request.form.get("new_password") or ""
        confirm_password = request.form.get("confirm_password") or ""
        if len(new_password) < 8:
            flash("New password must be at least 8 characters.", "error")
            return render_template("change_password.html")
        if new_password != confirm_password:
            flash("New password and confirmation do not match.", "error")
            return render_template("change_password.html")
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT password_hash FROM users WHERE id=?", (user["id"],))
        row = cur.fetchone()
        if not row or not check_password_hash(row["password_hash"], current_password):
            conn.close()
            flash("Current password is incorrect.", "error")
            return render_template("change_password.html")
        cur.execute(
            """
            UPDATE users
            SET password_hash=?, must_change_password=0, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (generate_password_hash(new_password), user["id"]),
        )
        log_audit(cur, "change_password", details="User changed password")
        conn.commit()
        conn.close()
        flash("Password updated.", "success")
        return redirect(url_for("index"))
    return render_template("change_password.html")


@app.route("/admin/users", methods=["GET", "POST"])
@admin_required
def admin_users():
    conn = get_db()
    cur = conn.cursor()
    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        if action == "create":
            username = (request.form.get("username") or "").strip()
            full_name = (request.form.get("full_name") or "").strip()
            password = request.form.get("password") or ""
            role = (request.form.get("role") or "encoder").strip()
            if not username or len(password) < 8:
                flash("Username and an 8+ character password are required.", "error")
            else:
                try:
                    cur.execute(
                        """
                        INSERT INTO users(username, password_hash, full_name, role, is_active, must_change_password)
                        VALUES(?,?,?,?,1,1)
                        """,
                        (username, generate_password_hash(password), full_name, role if role in {"admin", "encoder"} else "encoder"),
                    )
                    log_audit(cur, "create_user", field_name="username", new_value=username, details=f"role={role}")
                    conn.commit()
                    flash(f"User '{username}' created.", "success")
                except sqlite3.IntegrityError:
                    flash("Username already exists.", "error")
        elif action == "toggle_active":
            user_id = request.form.get("user_id", type=int)
            cur.execute("SELECT username, is_active FROM users WHERE id=?", (user_id,))
            row = cur.fetchone()
            if row:
                new_active = 0 if row["is_active"] else 1
                cur.execute("UPDATE users SET is_active=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (new_active, user_id))
                log_audit(cur, "toggle_user_active", field_name="is_active", old_value=row["is_active"], new_value=new_active, details=row["username"])
                conn.commit()
                flash(f"User '{row['username']}' updated.", "success")
        elif action == "reset_password":
            user_id = request.form.get("user_id", type=int)
            new_password = request.form.get("new_password") or ""
            if len(new_password) < 8:
                flash("Reset password must be at least 8 characters.", "error")
            else:
                cur.execute("SELECT username FROM users WHERE id=?", (user_id,))
                row = cur.fetchone()
                if row:
                    cur.execute(
                        """
                        UPDATE users
                        SET password_hash=?, must_change_password=1, updated_at=CURRENT_TIMESTAMP
                        WHERE id=?
                        """,
                        (generate_password_hash(new_password), user_id),
                    )
                    log_audit(cur, "reset_password", details=f"target={row['username']}")
                    conn.commit()
                    flash(f"Password reset for '{row['username']}'.", "success")

    cur.execute(
        """
        SELECT id, username, full_name, role, is_active, must_change_password, created_at, last_login_at
        FROM users
        ORDER BY role DESC, username
        """
    )
    users = [dict(r) for r in cur.fetchall()]
    conn.close()
    return render_template("admin_users.html", users=users)


@app.route("/admin/publish-storefront", methods=["POST"])
@admin_required
def publish_storefront():
    conn = get_db()
    cur = conn.cursor()
    try:
        ok, output = run_storefront_publish()
        log_audit(
            cur,
            "storefront_publish",
            details=f"success={1 if ok else 0}; output={output[:2000]}",
        )
        conn.commit()
    finally:
        conn.close()

    if ok:
        flash("Storefront catalog published successfully.", "success")
    else:
        flash(f"Storefront publish failed: {output}", "error")
    return redirect(url_for("index"))

@app.route("/")
@login_required
def index():
    scope = request.args.get("scope","active")
    conn = get_db(); cur = conn.cursor()
    where = "1=1" if scope=="all" else "p.active=1"

    cur.execute("SELECT COUNT(*) as c FROM products"); total_all = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) as c FROM products WHERE active=1"); total_active = cur.fetchone()["c"]

    cur.execute(f"""
      SELECT COUNT(*) as total_scope,
             SUM(CASE WHEN p.needs_enrichment=1 THEN 1 ELSE 0 END) as needs_work,
             SUM(CASE WHEN p.data_quality='COMPLETE' THEN 1 ELSE 0 END) as complete,
             SUM(CASE WHEN p.needs_photo=1 THEN 1 ELSE 0 END) as missing_photos
      FROM products p
      WHERE {where}
    """)
    s = dict(cur.fetchone())
    cur.execute("SELECT COUNT(*) as c FROM products WHERE pending_deletion=1")
    pending_deletion_count = cur.fetchone()["c"]
    cur.execute(
        f"""
        SELECT COUNT(*) as c
        FROM products p
        WHERE {where} AND COALESCE(p.availability_override, 'AUTO')='FORCE_UNAVAILABLE'
        """
    )
    forced_unavailable_count = cur.fetchone()["c"]
    cur.execute(
        f"""
        SELECT COUNT(*) as c
        FROM products p
        LEFT JOIN inventory inv ON inv.merkey = p.merkey
        WHERE {where} AND COALESCE(inv.quantity_on_hand, 0) <= 0
        """
    )
    zero_stock_count = cur.fetchone()["c"]
    cur.execute(
        f"""
        SELECT COUNT(*) as c
        FROM products p
        LEFT JOIN inventory inv ON inv.merkey = p.merkey
        WHERE {where}
          AND COALESCE(p.availability_override, 'AUTO')='FORCE_UNAVAILABLE'
          AND COALESCE(inv.quantity_on_hand, 0) > 0
        """
    )
    stock_exception_count = cur.fetchone()["c"]

    cur.execute(f"""
      SELECT p.data_quality, COUNT(*) as count
      FROM products p
      WHERE {where} AND p.needs_enrichment=1
      GROUP BY p.data_quality
      ORDER BY count DESC
    """)
    breakdown = [dict(r) for r in cur.fetchall()]
    conn.close()
    return render_template("dashboard.html", stats={
        "scope": scope,
        "total_all": total_all,
        "total_active": total_active,
        "total_scope": s["total_scope"] or 0,
        "needs_work": s["needs_work"] or 0,
        "complete": s["complete"] or 0,
        "missing_photos": s["missing_photos"] or 0,
        "pending_deletion": pending_deletion_count,
        "forced_unavailable": forced_unavailable_count,
        "zero_stock": zero_stock_count,
        "stock_exceptions": stock_exception_count,
    }, breakdown=breakdown)

@app.route("/products")
@login_required
def products_list():
    scope = request.args.get("scope","active")
    page = request.args.get("page",1,type=int)
    per_page = max(10, min(200, request.args.get("per_page",50,type=int)))
    offset = (page-1)*per_page

    filter_type = request.args.get("filter","needs_work")
    department = (request.args.get("department","") or "").strip()
    supplier = (request.args.get("supplier","") or "").strip()
    class_l1 = (request.args.get("class_l1","") or "").strip()
    sort_by = (request.args.get("sort_by","workflow") or "workflow").strip()
    sort_col = (request.args.get("sort_col","") or "").strip()
    sort_dir = (request.args.get("sort_dir","asc") or "asc").strip().lower()
    if sort_dir not in {"asc", "desc"}:
        sort_dir = "asc"
    search = (request.args.get("search","") or "").strip()
    missing_photos = request.args.get("missing_photos","0") == "1"

    where=[]; params=[]
    if scope=="active": where.append("p.active=1")
    if filter_type=="needs_work": where.append("p.needs_enrichment=1")
    elif filter_type=="pending_deletion": where.append("p.pending_deletion=1")
    elif filter_type=="force_unavailable": where.append("COALESCE(p.availability_override, 'AUTO')='FORCE_UNAVAILABLE'")
    elif filter_type=="zero_stock": where.append("COALESCE(inv.quantity_on_hand, 0) <= 0")
    elif filter_type=="stock_exceptions":
        where.append("COALESCE(p.availability_override, 'AUTO')='FORCE_UNAVAILABLE'")
        where.append("COALESCE(inv.quantity_on_hand, 0) > 0")
    elif filter_type=="all": pass
    else:
        where.append("p.data_quality=?"); params.append(filter_type)
    if missing_photos: where.append("p.needs_photo=1")
    if department == "__NONE__":
        where.append("(d.name IS NULL OR TRIM(d.name)='')")
    elif department:
        where.append("COALESCE(d.name,'')=?")
        params.append(department)
    if supplier:
        where.append("p.supplier_code=?")
        params.append(supplier)
    if class_l1:
        where.append("p.class_l1_code=?")
        params.append(class_l1)
    if search:
        where.append(
            "("
            "p.description LIKE ? OR "
            "p.merkey LIKE ? OR "
            "p.name LIKE ? OR "
            "p.supplier_code LIKE ? OR "
            "p.supplier_name LIKE ? OR "
            "p.class_l1_name LIKE ? OR "
            "p.class_l2_name LIKE ? OR "
            "p.class_l3_name LIKE ? OR "
            "p.clrkey LIKE ? OR "
            "EXISTS (SELECT 1 FROM brands b2 WHERE b2.id = p.brand_id AND b2.name LIKE ?) OR "
            "EXISTS (SELECT 1 FROM barcodes bc WHERE bc.merkey = p.merkey AND bc.barcode LIKE ?)"
            ")"
        )
        params.extend([f"%{search}%"] * 11)
    where_sql = " AND ".join(where) if where else "1=1"

    conn=get_db(); cur=conn.cursor()
    # Supplier options for supplier-centric workflow (scope-aware).
    supplier_where = "p.active=1" if scope=="active" else "1=1"
    cur.execute(f"""
      SELECT p.supplier_code,
             MAX(TRIM(COALESCE(p.supplier_name,''))) as supplier_name,
             COUNT(*) as cnt
      FROM products p
      WHERE {supplier_where}
        AND p.supplier_code IS NOT NULL
        AND TRIM(p.supplier_code) <> ''
      GROUP BY p.supplier_code
      ORDER BY p.supplier_code
    """)
    supplier_options = []
    for r in cur.fetchall():
        row = dict(r)
        code = (row.get("supplier_code") or "").strip()
        name = (row.get("supplier_name") or "").strip()
        row["supplier_display"] = f"{code} - {name}" if name else code
        supplier_options.append(row)
    cur.execute(f"""
      SELECT p.class_l1_code, MAX(TRIM(COALESCE(p.class_l1_name,''))) as class_l1_name, COUNT(*) as cnt
      FROM products p
      WHERE {supplier_where}
        AND p.class_l1_code IS NOT NULL
        AND TRIM(p.class_l1_code) <> ''
      GROUP BY p.class_l1_code
      ORDER BY p.class_l1_code
    """)
    class_l1_options = [dict(r) for r in cur.fetchall()]
    cur.execute(f"""
      SELECT d.name as department_name, COUNT(*) as cnt
      FROM products p
      LEFT JOIN departments d ON d.id = p.department_id
      WHERE {supplier_where}
      GROUP BY d.name
      ORDER BY CASE WHEN d.name IS NULL OR TRIM(d.name)='' THEN 1 ELSE 0 END, d.name
    """)
    department_options = []
    for r in cur.fetchall():
        row = dict(r)
        raw_name = row.get("department_name")
        row["department_name"] = (raw_name or "").strip()
        row["department_display"] = row["department_name"] or "<NONE>"
        row["department_value"] = row["department_name"] or "__NONE__"
        department_options.append(row)

    cur.execute(f"""
      SELECT COUNT(*) as cnt
      FROM products p
      LEFT JOIN departments d ON d.id = p.department_id
      LEFT JOIN inventory inv ON inv.merkey = p.merkey
      WHERE {where_sql}
    """, params)
    total = cur.fetchone()["cnt"]

    sortable_columns = {
        "merkey": "p.merkey",
        "description": "p.description",
        "name": "p.name",
        "supplier": "COALESCE(p.supplier_name, p.supplier_code, '')",
        "department": "COALESCE(d.name, '')",
        "class": "COALESCE(p.class_l1_name, '')",
        "brand": "b.name",
        "category": "c.name",
        "size": "p.size",
        "status": "p.data_quality",
        "photo": "p.needs_photo",
        "sales_24m": "txn_count_24m",
        "stock": "COALESCE(inv.quantity_on_hand, 0)",
        "storefront": "COALESCE(p.availability_override, 'AUTO')",
    }

    order_sql = "missing_photo DESC, txn_count_24m DESC, p.merkey ASC"
    if sort_col in sortable_columns:
        order_sql = f"{sortable_columns[sort_col]} {sort_dir.upper()}, p.merkey ASC"
    elif sort_by == "supplier":
        order_sql = "COALESCE(p.supplier_name,''), COALESCE(p.supplier_code,''), missing_photo DESC, txn_count_24m DESC, p.merkey ASC"

    cur.execute(f"""
      SELECT p.merkey, p.description, p.name, p.size, p.data_quality, p.needs_enrichment,
             p.pending_deletion, p.active, COALESCE(p.availability_override, 'AUTO') as availability_override,
             COALESCE(p.show_pack_on_storefront, 0) as show_pack_on_storefront,
             COALESCE(p.supplier_code,'') as supplier_code,
             COALESCE(p.supplier_name,'') as supplier_name,
             COALESCE(p.class_l1_code,'') as class_l1_code,
             COALESCE(p.class_l1_name,'') as class_l1_name,
             COALESCE(p.class_l2_name,'') as class_l2_name,
             COALESCE(p.class_l3_name,'') as class_l3_name,
             COALESCE(p.clrkey,'') as clrkey,
             b.name as brand, c.name as category, d.name as department_name,
             COALESCE(s.txn_count_24m,0) as txn_count_24m,
             COALESCE(inv.quantity_on_hand, 0) as quantity_on_hand,
             inv.last_updated as inventory_last_updated,
             ip.quantity_on_hand as posted_quantity_on_hand,
             ip.last_updated as posted_inventory_last_updated,
             p.needs_photo as missing_photo,
             img.id as image_id,
             img.s3_url as image_url,
             img.uploaded_at as image_uploaded_at
      FROM products p
      LEFT JOIN brands b ON p.brand_id=b.id
      LEFT JOIN categories c ON p.category_id=c.id
      LEFT JOIN departments d ON p.department_id=d.id
      LEFT JOIN sales_metrics s ON p.merkey=s.merkey
      LEFT JOIN inventory inv ON p.merkey=inv.merkey
      LEFT JOIN inventory_posted ip ON p.merkey=ip.merkey
      LEFT JOIN images img ON p.merkey=img.merkey AND img.is_primary=1 AND COALESCE(img.public_status, 'ok')='ok'
      WHERE {where_sql}
      ORDER BY {order_sql}
      LIMIT ? OFFSET ?
    """, params + [per_page, offset])
    products=[dict(r) for r in cur.fetchall()]
    for p in products:
        code = (p.get("supplier_code") or "").strip()
        name = (p.get("supplier_name") or "").strip()
        p["supplier_display"] = f"{code} - {name}" if code and name else (code or "-")
        if is_real_image_url(p.get("image_url")):
            version = p.get("image_id") or p.get("image_uploaded_at")
            p["image_url"] = add_cache_buster(p["image_url"], version)
        else:
            p["image_url"] = None
    conn.close()

    total_pages = max(1, (total + per_page - 1)//per_page)
    list_state = {
        "scope": scope,
        "filter": filter_type,
        "department": department,
        "supplier": supplier,
        "class_l1": class_l1,
        "sort_by": sort_by,
        "sort_col": sort_col,
        "sort_dir": sort_dir,
        "search": search,
        "missing_photos": "1" if missing_photos else "0",
        "per_page": str(per_page),
        "page": str(page),
    }
    list_state_qs = urlencode(list_state)
    return render_template(
        "products_list.html",
        products=products,
        page=page,
        total_pages=total_pages,
        total=total,
        filter_type=filter_type,
        search=search,
        scope=scope,
        missing_photos=missing_photos,
        per_page=per_page,
        department=department,
        department_options=department_options,
        supplier=supplier,
        supplier_options=supplier_options,
        class_l1=class_l1,
        class_l1_options=class_l1_options,
        sort_by=sort_by,
        sort_col=sort_col,
        sort_dir=sort_dir,
        list_state_qs=list_state_qs,
    )


@app.route("/logs/sync")
@login_required
def sync_logs():
    files = list_sync_change_files()
    selected_name = (request.args.get("file") or "").strip()
    selected_log = read_sync_change_file(selected_name) if selected_name else None
    if selected_log is None and files:
        selected_log = read_sync_change_file(files[0]["name"])
        selected_name = files[0]["name"]

    conn = get_db()
    cur = conn.cursor()
    sync_runs = []
    try:
        cur.execute(
            """
            SELECT id, sync_type, source_file, status, records_processed, records_updated, records_added,
                   records_skipped, error_message, started_at, completed_at
            FROM sync_log
            ORDER BY COALESCE(completed_at, started_at) DESC
            LIMIT 25
            """
        )
        sync_runs = [dict(r) for r in cur.fetchall()]
    except sqlite3.Error:
        sync_runs = []
    finally:
        conn.close()

    return render_template(
        "sync_logs.html",
        files=files,
        selected_name=selected_name,
        selected_log=selected_log,
        sync_runs=sync_runs,
    )


@app.route("/logs/audit")
@login_required
def audit_logs():
    merkey = (request.args.get("merkey") or "").strip()
    username = (request.args.get("username") or "").strip()
    action_type = (request.args.get("action_type") or "").strip()
    day = (request.args.get("day") or "").strip()
    try:
        entry_limit = int((request.args.get("limit") or "100").strip())
    except ValueError:
        entry_limit = 100
    entry_limit = entry_limit if entry_limit in {25, 50, 100, 200, 500} else 100

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT merkey, COUNT(*) as entry_count, MAX(created_at) as latest_at
        FROM audit_log
        WHERE merkey IS NOT NULL AND TRIM(merkey) <> ''
        GROUP BY merkey
        ORDER BY latest_at DESC
        LIMIT 50
        """
    )
    product_links = [dict(r) for r in cur.fetchall()]

    cur.execute(
        """
        SELECT username, COUNT(*) as entry_count, MAX(created_at) as latest_at
        FROM audit_log
        WHERE username IS NOT NULL AND TRIM(username) <> ''
        GROUP BY username
        ORDER BY latest_at DESC
        LIMIT 25
        """
    )
    user_links = [dict(r) for r in cur.fetchall()]

    cur.execute(
        """
        SELECT action_type, COUNT(*) as entry_count, MAX(created_at) as latest_at
        FROM audit_log
        GROUP BY action_type
        ORDER BY latest_at DESC
        LIMIT 25
        """
    )
    action_links = [dict(r) for r in cur.fetchall()]

    summary_where = []
    summary_params: list[str] = []
    if merkey:
        summary_where.append("merkey = ?")
        summary_params.append(merkey)
    if username:
        summary_where.append("username = ?")
        summary_params.append(username)
    if action_type:
        summary_where.append("action_type = ?")
        summary_params.append(action_type)
    summary_where_sql = " AND ".join(summary_where) if summary_where else "1=1"

    cur.execute(
        f"""
        SELECT
            date(datetime(created_at, '+8 hours')) as audit_day,
            COUNT(*) as entry_count,
            COUNT(DISTINCT CASE WHEN merkey IS NOT NULL AND TRIM(merkey) <> '' THEN merkey END) as product_count,
            COUNT(DISTINCT CASE WHEN username IS NOT NULL AND TRIM(username) <> '' THEN username END) as user_count,
            SUM(CASE WHEN action_type = 'product_update' THEN 1 ELSE 0 END) as product_updates,
            SUM(CASE WHEN action_type = 'photo_upload' THEN 1 ELSE 0 END) as photo_uploads,
            SUM(CASE WHEN action_type = 'login' THEN 1 ELSE 0 END) as logins,
            MAX(created_at) as latest_at
        FROM audit_log
        WHERE {summary_where_sql}
        GROUP BY date(datetime(created_at, '+8 hours'))
        ORDER BY audit_day DESC
        LIMIT 31
        """,
        summary_params,
    )
    daily_summary = [dict(r) for r in cur.fetchall()]

    if not day and daily_summary:
        day = daily_summary[0]["audit_day"]

    user_day_where = list(summary_where)
    user_day_params = list(summary_params)
    if day:
        user_day_where.append("date(datetime(created_at, '+8 hours')) = ?")
        user_day_params.append(day)
    user_day_where_sql = " AND ".join(user_day_where) if user_day_where else "1=1"

    cur.execute(
        f"""
        SELECT
            COALESCE(NULLIF(TRIM(username), ''), '(unknown)') as username,
            COUNT(*) as entry_count,
            COUNT(DISTINCT CASE WHEN merkey IS NOT NULL AND TRIM(merkey) <> '' THEN merkey END) as product_count,
            SUM(CASE WHEN action_type = 'product_update' THEN 1 ELSE 0 END) as product_updates,
            SUM(CASE WHEN action_type = 'photo_upload' THEN 1 ELSE 0 END) as photo_uploads,
            SUM(CASE WHEN action_type = 'login' THEN 1 ELSE 0 END) as logins,
            MAX(created_at) as latest_at
        FROM audit_log
        WHERE {user_day_where_sql}
        GROUP BY COALESCE(NULLIF(TRIM(username), ''), '(unknown)')
        ORDER BY entry_count DESC, latest_at DESC
        LIMIT 25
        """,
        user_day_params,
    )
    daily_user_summary = [dict(r) for r in cur.fetchall()]

    action_day_where = list(user_day_where)
    action_day_params = list(user_day_params)
    if username:
        action_day_where.append("username = ?")
        action_day_params.append(username)
    action_day_where_sql = " AND ".join(action_day_where) if action_day_where else "1=1"

    cur.execute(
        f"""
        SELECT
            action_type,
            COUNT(*) as entry_count,
            COUNT(DISTINCT CASE WHEN merkey IS NOT NULL AND TRIM(merkey) <> '' THEN merkey END) as product_count,
            MAX(created_at) as latest_at
        FROM audit_log
        WHERE {action_day_where_sql}
        GROUP BY action_type
        ORDER BY entry_count DESC, latest_at DESC
        LIMIT 20
        """,
        action_day_params,
    )
    daily_action_summary = [dict(r) for r in cur.fetchall()]

    where = []
    params: list[str] = []
    if merkey:
        where.append("al.merkey = ?")
        params.append(merkey)
    if username:
        where.append("al.username = ?")
        params.append(username)
    if action_type:
        where.append("al.action_type = ?")
        params.append(action_type)
    if day:
        where.append("date(datetime(al.created_at, '+8 hours')) = ?")
        params.append(day)
    where_sql = " AND ".join(where) if where else "1=1"

    cur.execute(
        f"""
        SELECT al.id, al.user_id, al.username, al.merkey, al.action_type, al.field_name,
               al.old_value, al.new_value, al.details, al.created_at,
               p.description as product_description
        FROM audit_log al
        LEFT JOIN products p ON p.merkey = al.merkey
        WHERE {where_sql}
        ORDER BY al.created_at DESC, al.id DESC
        LIMIT ?
        """,
        [*params, entry_limit],
    )
    entries = [dict(r) for r in cur.fetchall()]
    conn.close()

    return render_template(
        "audit_logs.html",
        entries=entries,
        merkey=merkey,
        username=username,
        action_type=action_type,
        day=day,
        entry_limit=entry_limit,
        daily_summary=daily_summary,
        daily_user_summary=daily_user_summary,
        daily_action_summary=daily_action_summary,
        product_links=product_links,
        user_links=user_links,
        action_links=action_links,
    )

@app.route("/product/<merkey>")
@login_required
def product_edit(merkey):
    conn=get_db(); cur=conn.cursor()
    cur.execute("""
      SELECT p.*, b.name as brand_name, c.name as category_name, d.name as department_name,
             s.txn_count_24m, s.last_sale_date, s.velocity_score,
             inv.quantity_on_hand, inv.last_updated as inventory_last_updated,
             ip.quantity_on_hand as posted_quantity_on_hand,
             ip.last_updated as posted_inventory_last_updated,
             pr.price_retail, pr.price_pack, pr.price_case, pr.cost,
             img.id as image_id, img.s3_url as image_url, img.uploaded_at as image_uploaded_at
      FROM products p
      LEFT JOIN brands b ON p.brand_id=b.id
      LEFT JOIN categories c ON p.category_id=c.id
      LEFT JOIN departments d ON p.department_id=d.id
      LEFT JOIN sales_metrics s ON p.merkey=s.merkey
      LEFT JOIN inventory inv ON p.merkey=inv.merkey
      LEFT JOIN inventory_posted ip ON p.merkey=ip.merkey
      LEFT JOIN prices pr ON p.merkey=pr.merkey AND pr.is_current=1
      LEFT JOIN images img ON p.merkey=img.merkey AND img.is_primary=1 AND COALESCE(img.public_status, 'ok')='ok'
      WHERE p.merkey=?
    """,(merkey,))
    row=cur.fetchone()
    if not row:
        conn.close(); return "Product not found", 404
    product=dict(row)
    if not is_real_image_url(product.get("image_url")):
        product["image_url"] = None
    else:
        version = product.get("image_id") or product.get("image_uploaded_at") or product.get("updated_at")
        product["image_url"] = add_cache_buster(product["image_url"], version)

    brands=[dict(r) for r in cur.execute("SELECT id,name FROM brands ORDER BY name")]
    categories=[dict(r) for r in cur.execute("SELECT id,name,department_id FROM categories ORDER BY name")]
    departments=[dict(r) for r in cur.execute("SELECT id,name FROM departments ORDER BY name")]
    barcodes=[dict(r) for r in cur.execute("SELECT barcode,is_primary FROM barcodes WHERE merkey=? ORDER BY is_primary DESC, barcode ASC",(merkey,))]
    product["primary_barcode"] = barcodes[0]["barcode"] if barcodes else ""
    structured_value, structured_unit = infer_size_parts(
        product.get("size"),
        product.get("weight_volume"),
        product.get("unit_of_measurement"),
    )
    product["structured_weight_value"] = structured_value
    product["structured_unit"] = structured_unit
    product["auto_generate_size_default"] = bool(structured_value and structured_unit)
    list_state = extract_list_state(request.args)
    list_url = url_for("products_list", **list_state) if list_state else url_for("products_list")
    conn.close()
    return render_template(
        "product_edit.html",
        product=product,
        brands=brands,
        categories=categories,
        departments=departments,
        barcodes=barcodes,
        unit_options=UNIT_OPTIONS,
        list_state=list_state,
        list_url=list_url,
    )

@app.route("/product/<merkey>/update", methods=["POST"])
@login_required
def product_update(merkey):
    description=(request.form.get("description") or "").strip()
    name=(request.form.get("name") or "").strip()
    brand_name=(request.form.get("brand") or "").strip()
    category_id=request.form.get("category_id", type=int)
    requested_department_id=request.form.get("department_id", type=int)
    size=normalize_size_text((request.form.get("size") or "").strip())
    weight_volume=clean_measure_value(request.form.get("weight_volume"))
    unit=normalize_unit(request.form.get("unit"))
    notes=(request.form.get("notes") or "").strip()
    availability_override = (request.form.get("availability_override") or "AUTO").strip().upper()
    alpha_override = (request.form.get("alpha_override") or "AUTO").strip().upper()
    show_pack_on_storefront = 1 if (request.form.get("show_pack_on_storefront") or "").strip() == "1" else 0
    pack_display_label = (request.form.get("pack_display_label") or "").strip()
    pack_photo_url = (request.form.get("pack_photo_url") or "").strip()
    pack_barcode = (request.form.get("pack_barcode") or "").strip()
    show_case_on_storefront = 1 if (request.form.get("show_case_on_storefront") or "").strip() == "1" else 0
    case_display_label = (request.form.get("case_display_label") or "").strip()
    case_barcode = (request.form.get("case_barcode") or "").strip()
    case_photo_url = (request.form.get("case_photo_url") or "").strip()
    try:
        case_quantity = int((request.form.get("case_quantity") or "").strip() or 0)
    except (TypeError, ValueError):
        case_quantity = 0
    primary_barcode=(request.form.get("primary_barcode") or "").strip()
    auto_fill_description = (request.form.get("auto_fill_description") or "").strip() == "1"
    auto_generate_size = (request.form.get("auto_generate_size") or "").strip() == "1"
    list_state = extract_list_state(request.form)

    if auto_generate_size:
        generated_size = compose_size_from_parts(weight_volume, unit)
        if generated_size:
            size = generated_size

    if auto_fill_description:
        description = compose_description(brand_name, name, size)
    if availability_override not in {"AUTO", "FORCE_UNAVAILABLE", "FORCE_AVAILABLE"}:
        availability_override = "AUTO"
    if alpha_override not in {"AUTO", "FORCE_INCLUDE", "FORCE_EXCLUDE"}:
        alpha_override = "AUTO"

    conn=get_db(); cur=conn.cursor()
    cur.execute(
        """
        SELECT p.*, b.name as brand_name, c.name as category_name, d.name as department_name
        FROM products p
        LEFT JOIN brands b ON b.id=p.brand_id
        LEFT JOIN categories c ON c.id=p.category_id
        LEFT JOIN departments d ON d.id=p.department_id
        WHERE p.merkey=?
        """,
        (merkey,),
    )
    old_product = cur.fetchone()
    if not old_product:
        conn.close()
        return "Product not found", 404
    old_product = dict(old_product)
    old_primary_barcode = get_primary_barcode(cur, merkey) or ""

    brand_id=None
    if brand_name:
        cur.execute("SELECT id FROM brands WHERE name=?",(brand_name,))
        r=cur.fetchone()
        if r: brand_id=r["id"]
        else:
            cur.execute("INSERT INTO brands(name,slug) VALUES(?,?)",(brand_name,slugify(brand_name)))
            brand_id=cur.lastrowid

    category_row = None
    department_id = None
    if category_id:
        cur.execute(
            "SELECT c.name, c.department_id, d.name AS department_name FROM categories c LEFT JOIN departments d ON d.id = c.department_id WHERE c.id=?",
            (category_id,),
        )
        category_row = cur.fetchone()
        if category_row:
            department_id = category_row["department_id"]
        else:
            category_id = None

    if category_id is None:
        department_id = requested_department_id

    dq, ne = compute_quality(description,name,brand_id,category_id,size)

    cur.execute("""
      UPDATE products SET description=?, name=?, brand_id=?, category_id=?, department_id=?,
                          size=?, weight_volume=?, unit_of_measurement=?,
                          availability_override=?, alpha_override=?, show_pack_on_storefront=?, pack_display_label=?, pack_photo_url=?, pack_barcode=?,
                          show_case_on_storefront=?, case_display_label=?, case_barcode=?, case_quantity=?, case_photo_url=?,
                          data_quality=?, needs_enrichment=?, enrichment_notes=?,
                          updated_at=CURRENT_TIMESTAMP
      WHERE merkey=?
    """,(description,name,brand_id,category_id,department_id,size,weight_volume,unit,availability_override,alpha_override,show_pack_on_storefront,pack_display_label,pack_photo_url,pack_barcode,show_case_on_storefront,case_display_label,case_barcode,case_quantity,case_photo_url,dq,ne,notes or "Updated via web encoder", merkey))

    # Allow barcode correction from product edit page.
    if primary_barcode:
        cur.execute("UPDATE barcodes SET is_primary=0 WHERE merkey=?", (merkey,))
        cur.execute("SELECT id FROM barcodes WHERE merkey=? AND barcode=?", (merkey, primary_barcode))
        existing_barcode = cur.fetchone()
        if existing_barcode:
            cur.execute(
                "UPDATE barcodes SET is_primary=1 WHERE id=?",
                (existing_barcode["id"],),
            )
        else:
            cur.execute(
                "INSERT INTO barcodes(merkey, barcode, is_primary) VALUES(?,?,1)",
                (merkey, primary_barcode),
            )

    cur.execute("SELECT name FROM departments WHERE id=?", (department_id,))
    department_row = cur.fetchone()
    field_changes = [
        ("description", old_product.get("description") or "", description),
        ("name", old_product.get("name") or "", name),
        ("brand", old_product.get("brand_name") or "", brand_name),
        ("category", old_product.get("category_name") or "", category_row["name"] if category_row else ""),
        ("department", old_product.get("department_name") or "", department_row["name"] if department_row else ""),
        ("size", old_product.get("size") or "", size),
        ("weight_volume", old_product.get("weight_volume") or "", weight_volume),
        ("unit_of_measurement", old_product.get("unit_of_measurement") or "", unit),
        ("availability_override", old_product.get("availability_override") or "AUTO", availability_override),
        ("alpha_override", old_product.get("alpha_override") or "AUTO", alpha_override),
        ("show_pack_on_storefront", str(old_product.get("show_pack_on_storefront") or 0), str(show_pack_on_storefront)),
        ("pack_display_label", old_product.get("pack_display_label") or "", pack_display_label),
        ("pack_photo_url", old_product.get("pack_photo_url") or "", pack_photo_url),
        ("pack_barcode", old_product.get("pack_barcode") or "", pack_barcode),
        ("show_case_on_storefront", str(old_product.get("show_case_on_storefront") or 0), str(show_case_on_storefront)),
        ("case_display_label", old_product.get("case_display_label") or "", case_display_label),
        ("case_barcode", old_product.get("case_barcode") or "", case_barcode),
        ("case_quantity", str(old_product.get("case_quantity") or 0), str(case_quantity)),
        ("case_photo_url", old_product.get("case_photo_url") or "", case_photo_url),
        ("primary_barcode", old_primary_barcode, primary_barcode),
    ]
    for field_name, old_value, new_value in field_changes:
        if (old_value or "") != (new_value or ""):
            log_audit(cur, "product_update", merkey=merkey, field_name=field_name, old_value=old_value, new_value=new_value)
    if notes:
        log_audit(cur, "product_note", merkey=merkey, field_name="enrichment_notes", old_value=old_product.get("enrichment_notes") or "", new_value=notes)

    conn.commit(); conn.close()
    return redirect(url_for("product_edit", merkey=merkey, **list_state))

@app.route("/products/purge-pending", methods=["POST"])
@admin_required
def purge_pending_deletion():
    conn=get_db(); cur=conn.cursor()
    conn.execute("PRAGMA foreign_keys = ON")
    cur.execute("SELECT COUNT(*) as c FROM products WHERE pending_deletion=1")
    count = cur.fetchone()["c"]
    log_audit(cur, "purge_pending_deletion", details=f"products_deleted={count}")
    cur.execute("DELETE FROM products WHERE pending_deletion=1")
    conn.commit(); conn.close()
    flash(f"Deleted {count:,} products marked for deletion.", "success")
    return redirect(url_for("index"))

@app.route("/product/<merkey>/restore", methods=["POST"])
@login_required
def product_restore(merkey):
    list_state = extract_list_state(request.form)
    conn=get_db(); cur=conn.cursor()
    cur.execute("SELECT pending_deletion, active FROM products WHERE merkey=?", (merkey,))
    before = cur.fetchone()
    cur.execute("""
      UPDATE products SET pending_deletion=0, active=1, updated_at=CURRENT_TIMESTAMP
      WHERE merkey=?
    """, (merkey,))
    if before:
        log_audit(cur, "product_restore", merkey=merkey, field_name="pending_deletion", old_value=before["pending_deletion"], new_value=0)
        log_audit(cur, "product_restore", merkey=merkey, field_name="active", old_value=before["active"], new_value=1)
    conn.commit(); conn.close()
    flash("Product restored - pending deletion flag cleared.", "success")
    return redirect(url_for("product_edit", merkey=merkey, **list_state))

@app.route("/product/<merkey>/photo", methods=["POST"])
@login_required
def upload_photo(merkey):
    file = request.files.get("photo")
    if not file or file.filename == "":
        file = request.files.get("camera_photo")
    if not file or file.filename=="":
        flash("No photo selected from album/files or camera","error")
        return redirect(url_for("product_edit", merkey=merkey))
    apply_white_bg = request.form.get("apply_white_bg", "").strip() == "1"
    list_state = extract_list_state(request.form)

    ts=datetime.now().strftime("%Y%m%d_%H%M%S")
    safe=slugify(Path(file.filename).stem) or "photo"
    source_ext = Path(file.filename).suffix.lower()
    if source_ext not in {".jpg", ".jpeg", ".png", ".webp"}:
        source_ext = ".jpg"
    orig_path = ORIG_DIR / f"{merkey}_{ts}_{safe}{source_ext}"
    file.save(orig_path)

    conn=get_db(); cur=conn.cursor()
    cur.execute("SELECT s3_url, filename FROM images WHERE merkey=? AND is_primary=1 ORDER BY id DESC LIMIT 1", (merkey,))
    old_image = cur.fetchone()
    barcode=get_primary_barcode(cur, merkey)
    identifier = barcode if barcode else merkey

    if apply_white_bg:
        upload_path = PROC_DIR / f"{identifier}.jpg"
        result = process_to_white_bg(orig_path, upload_path, size=1200, padding_ratio=0.06, try_remove_bg=True)
        key = f"{S3_PREFIX}{identifier}.jpg"
        content_type = "image/jpeg"
        stored_filename = f"{identifier}.jpg"
        width = result.width
        height = result.height
        file_size = result.file_size
        success_msg = "Photo uploaded + White BG processed + uploaded to S3"
    else:
        upload_path = orig_path
        key = f"{S3_PREFIX}{identifier}{source_ext}"
        content_type = file.mimetype or "application/octet-stream"
        stored_filename = f"{identifier}{source_ext}"
        width = None
        height = None
        file_size = upload_path.stat().st_size
        success_msg = "Photo uploaded directly to S3"

    up=upload_file_to_s3(upload_path, key=key, bucket=S3_BUCKET, region=S3_REGION, content_type=content_type, public_read=True)

    cur.execute("UPDATE images SET is_primary=0 WHERE merkey=? AND is_primary=1",(merkey,))
    cur.execute("""
      INSERT INTO images(
        merkey, filename, s3_url, local_path, is_primary, width, height, file_size,
        uploaded_at, public_status, public_status_code, public_checked_at, public_error
      )
      VALUES(?,?,?,?,1,?,?,?,CURRENT_TIMESTAMP,'ok',200,CURRENT_TIMESTAMP,'Uploaded by web encoder')
    """,(merkey, stored_filename, up.url, str(upload_path), width, height, file_size))
    cur.execute("UPDATE products SET needs_photo=0, needs_irl_photo=0, updated_at=CURRENT_TIMESTAMP WHERE merkey=?",(merkey,))
    log_audit(
        cur,
        "photo_upload",
        merkey=merkey,
        field_name="image",
        old_value=(old_image["s3_url"] if old_image else ""),
        new_value=up.url,
        details=f"filename={stored_filename}; white_bg={'yes' if apply_white_bg else 'no'}",
    )
    conn.commit(); conn.close()

    flash(f"{success_msg} ✅","success")
    return redirect(url_for("product_edit", merkey=merkey, **list_state))

@app.route("/product/<merkey>/pack-photo", methods=["POST"])
@login_required
def upload_pack_photo(merkey):
    file = request.files.get("pack_photo")
    if not file or file.filename == "":
        file = request.files.get("pack_camera_photo")
    if not file or file.filename == "":
        flash("No pack photo selected from album/files or camera", "error")
        return redirect(url_for("product_edit", merkey=merkey))
    apply_white_bg = request.form.get("apply_white_bg", "").strip() == "1"
    list_state = extract_list_state(request.form)

    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT pack_photo_url, pack_barcode FROM products WHERE merkey=?", (merkey,))
    row = cur.fetchone()
    if not row:
        conn.close()
        flash("Product not found", "error")
        return redirect(url_for("products_list"))
    old_pack_url = row["pack_photo_url"] or ""
    pack_barcode = (row["pack_barcode"] or "").strip()
    primary_barcode = get_primary_barcode(cur, merkey) or ""
    if pack_barcode:
        identifier = pack_barcode
    elif primary_barcode:
        identifier = f"{primary_barcode}-pack"
    else:
        identifier = f"{merkey}-pack"

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = slugify(Path(file.filename).stem) or "pack"
    source_ext = Path(file.filename).suffix.lower()
    if source_ext not in {".jpg", ".jpeg", ".png", ".webp"}:
        source_ext = ".jpg"
    orig_path = ORIG_DIR / f"{merkey}_pack_{ts}_{safe}{source_ext}"
    file.save(orig_path)

    if apply_white_bg:
        upload_path = PROC_DIR / f"{identifier}.jpg"
        process_to_white_bg(orig_path, upload_path, size=1200, padding_ratio=0.06, try_remove_bg=True)
        key = f"{S3_PREFIX}{identifier}.jpg"
        content_type = "image/jpeg"
        success_msg = "Pack photo uploaded + White BG processed + uploaded to S3"
    else:
        upload_path = orig_path
        key = f"{S3_PREFIX}{identifier}{source_ext}"
        content_type = file.mimetype or "application/octet-stream"
        success_msg = "Pack photo uploaded directly to S3"

    up = upload_file_to_s3(upload_path, key=key, bucket=S3_BUCKET, region=S3_REGION, content_type=content_type, public_read=True)
    new_pack_url = up.url

    cur.execute(
        "UPDATE products SET pack_photo_url=?, updated_at=CURRENT_TIMESTAMP WHERE merkey=?",
        (new_pack_url, merkey),
    )
    log_audit(
        cur,
        "pack_photo_upload",
        merkey=merkey,
        field_name="pack_photo_url",
        old_value=old_pack_url,
        new_value=new_pack_url,
        details=f"identifier={identifier}; white_bg={'yes' if apply_white_bg else 'no'}",
    )
    conn.commit(); conn.close()

    flash(f"{success_msg} ✅", "success")
    return redirect(url_for("product_edit", merkey=merkey, **list_state))

if __name__ == "__main__":
    ensure_runtime_schema()
    host = os.environ.get("WEB_ENCODER_HOST", "0.0.0.0")
    port = int(os.environ.get("WEB_ENCODER_PORT", "5000"))
    debug_mode = os.environ.get("WEB_ENCODER_DEBUG", "0").strip().lower() in {"1", "true", "yes", "y"}
    print(f"Starting web server on http://{host}:{port}")
    print(f"DB path: {DB_PATH}")
    print(f"Debug mode: {debug_mode}")
    app.run(host=host, port=port, debug=debug_mode)

