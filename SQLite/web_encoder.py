from __future__ import annotations
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
import sqlite3
from pathlib import Path
from datetime import datetime
import os, re

from image_pipeline import process_to_white_bg
from s3_upload import upload_file_to_s3

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "anson-encoder-dev")

DB_PATH = "anson_products.db"
UPLOAD_DIR = Path("uploads")
ORIG_DIR = UPLOAD_DIR / "original"
PROC_DIR = UPLOAD_DIR / "processed"
ORIG_DIR.mkdir(parents=True, exist_ok=True)
PROC_DIR.mkdir(parents=True, exist_ok=True)

S3_BUCKET = "ansonsupermart.com"
S3_PREFIX = "images/"
S3_REGION = "ap-southeast-1"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

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

def get_primary_barcode(cur, merkey: str) -> str|None:
    cur.execute("SELECT barcode FROM barcodes WHERE merkey=? ORDER BY is_primary DESC, id ASC LIMIT 1", (merkey,))
    r = cur.fetchone()
    return r["barcode"] if r else None

@app.route("/")
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
             SUM(CASE WHEN img.id IS NULL THEN 1 ELSE 0 END) as missing_photos
      FROM products p
      LEFT JOIN images img ON p.merkey=img.merkey AND img.is_primary=1
      WHERE {where}
    """)
    s = dict(cur.fetchone())

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
    }, breakdown=breakdown)

@app.route("/products")
def products_list():
    scope = request.args.get("scope","active")
    page = request.args.get("page",1,type=int)
    per_page = max(10, min(200, request.args.get("per_page",50,type=int)))
    offset = (page-1)*per_page

    filter_type = request.args.get("filter","needs_work")
    search = (request.args.get("search","") or "").strip()
    missing_photos = request.args.get("missing_photos","0") == "1"

    where=[]; params=[]
    if scope=="active": where.append("p.active=1")
    if filter_type=="needs_work": where.append("p.needs_enrichment=1")
    elif filter_type=="all": pass
    else:
        where.append("p.data_quality=?"); params.append(filter_type)
    if missing_photos: where.append("img.id IS NULL")
    if search:
        where.append("(p.description LIKE ? OR p.merkey LIKE ? OR p.name LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])
    where_sql = " AND ".join(where) if where else "1=1"

    conn=get_db(); cur=conn.cursor()
    cur.execute(f"""
      SELECT COUNT(*) as cnt
      FROM products p
      LEFT JOIN images img ON p.merkey=img.merkey AND img.is_primary=1
      WHERE {where_sql}
    """, params)
    total = cur.fetchone()["cnt"]

    cur.execute(f"""
      SELECT p.merkey, p.description, p.name, p.size, p.data_quality, p.needs_enrichment,
             b.name as brand, c.name as category,
             COALESCE(s.txn_count_24m,0) as txn_count_24m,
             CASE WHEN img.id IS NULL THEN 1 ELSE 0 END as missing_photo
      FROM products p
      LEFT JOIN brands b ON p.brand_id=b.id
      LEFT JOIN categories c ON p.category_id=c.id
      LEFT JOIN sales_metrics s ON p.merkey=s.merkey
      LEFT JOIN images img ON p.merkey=img.merkey AND img.is_primary=1
      WHERE {where_sql}
      ORDER BY missing_photo DESC, txn_count_24m DESC, p.merkey ASC
      LIMIT ? OFFSET ?
    """, params + [per_page, offset])
    products=[dict(r) for r in cur.fetchall()]
    conn.close()

    total_pages = max(1, (total + per_page - 1)//per_page)
    return render_template("products_list.html", products=products, page=page, total_pages=total_pages, total=total,
                           filter_type=filter_type, search=search, scope=scope, missing_photos=missing_photos, per_page=per_page)

@app.route("/product/<merkey>")
def product_edit(merkey):
    conn=get_db(); cur=conn.cursor()
    cur.execute("""
      SELECT p.*, b.name as brand_name, c.name as category_name, d.name as department_name,
             s.txn_count_24m, s.last_sale_date, s.velocity_score,
             img.s3_url as image_url
      FROM products p
      LEFT JOIN brands b ON p.brand_id=b.id
      LEFT JOIN categories c ON p.category_id=c.id
      LEFT JOIN departments d ON p.department_id=d.id
      LEFT JOIN sales_metrics s ON p.merkey=s.merkey
      LEFT JOIN images img ON p.merkey=img.merkey AND img.is_primary=1
      WHERE p.merkey=?
    """,(merkey,))
    row=cur.fetchone()
    if not row:
        conn.close(); return "Product not found", 404
    product=dict(row)

    brands=[dict(r) for r in cur.execute("SELECT id,name FROM brands ORDER BY name")]
    categories=[dict(r) for r in cur.execute("SELECT id,name FROM categories ORDER BY name")]
    departments=[dict(r) for r in cur.execute("SELECT id,name FROM departments ORDER BY name")]
    barcodes=[dict(r) for r in cur.execute("SELECT barcode,is_primary FROM barcodes WHERE merkey=? ORDER BY is_primary DESC, barcode ASC",(merkey,))]
    conn.close()
    return render_template("product_edit.html", product=product, brands=brands, categories=categories, departments=departments, barcodes=barcodes)

@app.route("/product/<merkey>/update", methods=["POST"])
def product_update(merkey):
    description=(request.form.get("description") or "").strip()
    name=(request.form.get("name") or "").strip()
    brand_name=(request.form.get("brand") or "").strip()
    category_id=request.form.get("category_id", type=int)
    department_id=request.form.get("department_id", type=int)
    size=(request.form.get("size") or "").strip()
    weight_volume=(request.form.get("weight_volume") or "").strip()
    unit=(request.form.get("unit") or "").strip()
    notes=(request.form.get("notes") or "").strip()

    conn=get_db(); cur=conn.cursor()

    brand_id=None
    if brand_name:
        cur.execute("SELECT id FROM brands WHERE name=?",(brand_name,))
        r=cur.fetchone()
        if r: brand_id=r["id"]
        else:
            cur.execute("INSERT INTO brands(name,slug) VALUES(?,?)",(brand_name,slugify(brand_name)))
            brand_id=cur.lastrowid

    dq, ne = compute_quality(description,name,brand_id,category_id,size)

    cur.execute("""
      UPDATE products SET description=?, name=?, brand_id=?, category_id=?, department_id=?,
                          size=?, weight_volume=?, unit_of_measurement=?,
                          data_quality=?, needs_enrichment=?, enrichment_notes=?,
                          updated_at=CURRENT_TIMESTAMP
      WHERE merkey=?
    """,(description,name,brand_id,category_id,department_id,size,weight_volume,unit,dq,ne,notes or "Updated via web encoder", merkey))
    conn.commit(); conn.close()
    return redirect(url_for("product_edit", merkey=merkey))

@app.route("/product/<merkey>/photo", methods=["POST"])
def upload_photo(merkey):
    file = request.files.get("photo")
    if not file or file.filename=="":
        flash("No photo selected","error")
        return redirect(url_for("product_edit", merkey=merkey))

    ts=datetime.now().strftime("%Y%m%d_%H%M%S")
    safe=slugify(Path(file.filename).stem) or "photo"
    orig_path = ORIG_DIR / f"{merkey}_{ts}_{safe}.jpg"
    file.save(orig_path)

    conn=get_db(); cur=conn.cursor()
    barcode=get_primary_barcode(cur, merkey)
    identifier = barcode if barcode else merkey

    processed_path = PROC_DIR / f"{identifier}.jpg"
    result = process_to_white_bg(orig_path, processed_path, size=1200, padding_ratio=0.10, try_remove_bg=True)

    key=f"{S3_PREFIX}{identifier}.jpg"
    up=upload_file_to_s3(processed_path, key=key, bucket=S3_BUCKET, region=S3_REGION, content_type="image/jpeg", public_read=True)

    cur.execute("UPDATE images SET is_primary=0 WHERE merkey=? AND is_primary=1",(merkey,))
    cur.execute("""
      INSERT INTO images(merkey, filename, s3_url, local_path, is_primary, width, height, file_size, uploaded_at)
      VALUES(?,?,?,?,1,?,?,?,CURRENT_TIMESTAMP)
    """,(merkey, f"{identifier}.jpg", up.url, str(processed_path), result.width, result.height, result.file_size))
    conn.commit(); conn.close()

    flash("Photo uploaded + processed + uploaded to S3 ✅","success")
    return redirect(url_for("product_edit", merkey=merkey))

if __name__ == "__main__":
    print("Starting web server on http://0.0.0.0:5000")
    app.run(host="0.0.0.0", port=5000, debug=True)
