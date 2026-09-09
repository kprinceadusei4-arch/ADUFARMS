from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file, abort
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime, date
from decimal import Decimal, InvalidOperation
from pathlib import Path
import sqlite3
import os
import io

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "adufarms.db"
EXPORT_DIR = BASE_DIR / "exports" / "invoices"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("ADUFARMS_SECRET_KEY", "change-this-secret-key")
app.config["DATABASE"] = str(DB_PATH)

def db():
    conn = sqlite3.connect(app.config["DATABASE"])
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    conn = db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        full_name TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'STAFF',
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS purchases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        purchase_id TEXT UNIQUE NOT NULL,
        purchase_date TEXT NOT NULL,
        local_agent TEXT NOT NULL,
        agent_phone TEXT,
        location TEXT,
        quantity_kg REAL NOT NULL CHECK(quantity_kg > 0),
        price_per_kg REAL NOT NULL CHECK(price_per_kg >= 0),
        total_purchase_cost REAL NOT NULL,
        transport_cost REAL NOT NULL DEFAULT 0,
        other_expenses REAL NOT NULL DEFAULT 0,
        total_cost REAL NOT NULL,
        quantity_received_kg REAL NOT NULL DEFAULT 0 CHECK(quantity_received_kg >= 0),
        staff_user TEXT NOT NULL,
        created_at TEXT NOT NULL,
        deleted INTEGER NOT NULL DEFAULT 0,
        deleted_at TEXT,
        deleted_by TEXT
    );

    CREATE TABLE IF NOT EXISTS customers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        phone TEXT,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS sales (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        transaction_id TEXT UNIQUE NOT NULL,
        sale_date TEXT NOT NULL,
        customer_id INTEGER NOT NULL,
        quantity_kg REAL NOT NULL CHECK(quantity_kg > 0),
        selling_price_kg REAL NOT NULL CHECK(selling_price_kg >= 0),
        total_sale REAL NOT NULL,
        staff_user TEXT NOT NULL,
        created_at TEXT NOT NULL,
        deleted INTEGER NOT NULL DEFAULT 0,
        deleted_at TEXT,
        deleted_by TEXT,
        FOREIGN KEY(customer_id) REFERENCES customers(id)
    );

    CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        transaction_id TEXT NOT NULL,
        payment_date TEXT NOT NULL,
        amount REAL NOT NULL CHECK(amount > 0),
        payment_method TEXT NOT NULL,
        payment_reference TEXT,
        staff_user TEXT NOT NULL,
        created_at TEXT NOT NULL,
        deleted INTEGER NOT NULL DEFAULT 0,
        deleted_at TEXT,
        deleted_by TEXT,
        FOREIGN KEY(transaction_id) REFERENCES sales(transaction_id)
    );

    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        action TEXT NOT NULL,
        reference TEXT,
        details TEXT,
        created_at TEXT NOT NULL
    );
    """)
    for table in ("purchases", "sales", "payments"):
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if "deleted" not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0")
        if "deleted_at" not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN deleted_at TEXT")
        if "deleted_by" not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN deleted_by TEXT")
    admin = conn.execute("SELECT id FROM users WHERE username='admin'").fetchone()
    if not admin:
        conn.execute(
            "INSERT INTO users(username,full_name,password_hash,role,created_at) VALUES(?,?,?,?,?)",
            ("admin", "System Administrator", generate_password_hash("admin123"), "ADMIN", now())
        )
    conn.commit()
    conn.close()

def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper

def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if session.get("role") != "ADMIN":
            flash("Administrator access is required.", "danger")
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return wrapper

def money(v):
    return f"GH₵{float(v or 0):,.2f}"

def get_float(name, default=0):
    raw = request.form.get(name, "").strip()
    if raw == "":
        return float(default)
    try:
        value = float(Decimal(raw))
        return value
    except (InvalidOperation, ValueError):
        raise ValueError(f"Invalid value for {name.replace('_',' ')}.")

def next_daily_id(prefix, table, column):
    today = date.today().strftime("%Y%m%d")
    conn = db()
    pattern = f"{prefix}-{today}-%"
    row = conn.execute(
        f"SELECT {column} FROM {table} WHERE {column} LIKE ? ORDER BY id DESC LIMIT 1",
        (pattern,)
    ).fetchone()
    conn.close()
    n = 1
    if row and row[0]:
        try:
            n = int(row[0].rsplit("-", 1)[1]) + 1
        except Exception:
            n = 1
    return f"{prefix}-{today}-{n:04d}"

def can_see_deleted():
    return session.get("role") == "ADMIN"

def visible_sql(alias):
    return "1=1" if can_see_deleted() else f"{alias}.deleted=0"

def delete_record(table, record_id, reference):
    conn = db()
    if session.get("role") == "ADMIN":
        if table == "sales":
            conn.execute("DELETE FROM payments WHERE transaction_id=(SELECT transaction_id FROM sales WHERE id=?)", (record_id,))
        conn.execute(f"DELETE FROM {table} WHERE id=?", (record_id,))
        action = "HARD DELETE"
    else:
        conn.execute("UPDATE " + table + " SET deleted=1,deleted_at=?,deleted_by=? WHERE id=?", (now(), session["username"], record_id))
        action = "SOFT DELETE"
    conn.commit()
    conn.close()
    log_action(action, reference, table)

def stock_summary():
    conn = db()
    purchased = conn.execute("SELECT COALESCE(SUM(quantity_received_kg),0) v FROM purchases WHERE deleted=0").fetchone()["v"]
    sold = conn.execute("SELECT COALESCE(SUM(quantity_kg),0) v FROM sales WHERE deleted=0").fetchone()["v"]
    conn.close()
    return float(purchased), float(sold), float(purchased - sold)

def sale_info(transaction_id):
    conn = db()
    row = conn.execute(f"""
        SELECT s.*, c.name customer_name, c.phone customer_phone,
               COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.transaction_id=s.transaction_id AND p.deleted=0),0) total_paid
        FROM sales s JOIN customers c ON c.id=s.customer_id
        WHERE s.transaction_id=? AND {visible_sql('s')}
    """, (transaction_id,)).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["balance"] = max(float(d["total_sale"]) - float(d["total_paid"]), 0)
    d["status"] = "PAID" if d["balance"] <= 0.005 else ("PART PAYMENT" if d["total_paid"] > 0 else "UNPAID")
    return d

def log_action(action, reference="", details=""):
    conn = db()
    conn.execute(
        "INSERT INTO audit_log(username,action,reference,details,created_at) VALUES(?,?,?,?,?)",
        (session.get("username"), action, reference, details, now())
    )
    conn.commit()
    conn.close()

@app.template_filter("money")
def money_filter(v):
    return money(v)

@app.template_filter("pretty_date")
def pretty_date(v):
    try:
        return datetime.strptime(v, "%Y-%m-%d").strftime("%-d %B %Y")
    except Exception:
        try:
            return datetime.strptime(v, "%Y-%m-%d %H:%M:%S").strftime("%-d %B %Y %I:%M %p")
        except Exception:
            return v

@app.route("/")
def index():
    return redirect(url_for("dashboard") if "user_id" in session else url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username","").strip()
        password = request.form.get("password","")
        conn = db()
        user = conn.execute("SELECT * FROM users WHERE username=? AND active=1", (username,)).fetchone()
        conn.close()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["full_name"] = user["full_name"]
            session["role"] = user["role"]
            return redirect(url_for("dashboard"))
        flash("Invalid username or password.", "danger")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/dashboard")
@login_required
def dashboard():
    purchased, sold, stock = stock_summary()
    conn = db()
    sales = conn.execute("SELECT COALESCE(SUM(total_sale),0) v FROM sales WHERE deleted=0").fetchone()["v"]
    payments = conn.execute("SELECT COALESCE(SUM(amount),0) v FROM payments WHERE deleted=0").fetchone()["v"]
    purchase_cost = conn.execute("SELECT COALESCE(SUM(total_purchase_cost),0) v FROM purchases WHERE deleted=0").fetchone()["v"]
    transport = conn.execute("SELECT COALESCE(SUM(transport_cost),0) v FROM purchases WHERE deleted=0").fetchone()["v"]
    other = conn.execute("SELECT COALESCE(SUM(other_expenses),0) v FROM purchases WHERE deleted=0").fetchone()["v"]
    customers = conn.execute("SELECT COUNT(*) v FROM customers").fetchone()["v"]
    transactions = conn.execute("SELECT COUNT(*) v FROM sales WHERE deleted=0").fetchone()["v"]
    paid = conn.execute("""SELECT COUNT(*) v FROM sales s WHERE s.deleted=0 AND
        COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.transaction_id=s.transaction_id AND p.deleted=0),0) >= s.total_sale""").fetchone()["v"]
    part = conn.execute("""SELECT COUNT(*) v FROM sales s WHERE s.deleted=0 AND
        COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.transaction_id=s.transaction_id AND p.deleted=0),0) > 0
        AND COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.transaction_id=s.transaction_id AND p.deleted=0),0) < s.total_sale""").fetchone()["v"]
    unpaid = transactions - paid - part
    outstanding = max(float(sales) - float(payments), 0)
    expenses = float(purchase_cost) + float(transport) + float(other)
    profit = float(sales) - expenses
    recent = conn.execute("""SELECT s.transaction_id,s.sale_date,c.name,s.quantity_kg,s.total_sale,
        COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.transaction_id=s.transaction_id AND p.deleted=0),0) paid
        FROM sales s JOIN customers c ON c.id=s.customer_id WHERE s.deleted=0 ORDER BY s.id DESC LIMIT 8""").fetchall()
    conn.close()
    stats = dict(purchased=purchased,sold=sold,stock=stock,sales=float(sales),payments=float(payments),
                 outstanding=outstanding,purchase_cost=float(purchase_cost),transport=float(transport),
                 other=float(other),expenses=expenses,profit=profit,customers=customers,transactions=transactions,
                 paid=paid,part=part,unpaid=unpaid)
    return render_template("dashboard.html", stats=stats, recent=recent)

@app.route("/purchases", methods=["GET","POST"])
@login_required
def purchases():
    if request.method == "POST":
        try:
            qty = get_float("quantity_kg")
            price = get_float("price_per_kg")
            transport = get_float("transport_cost")
            other = get_float("other_expenses")
            received = get_float("quantity_received_kg")
            if qty <= 0 or received < 0 or received > qty:
                raise ValueError("Quantity received must be between 0 and quantity purchased.")
            total_purchase = qty * price
            total_cost = total_purchase + transport + other
            pid = next_daily_id("ADU-PUR", "purchases", "purchase_id")
            conn = db()
            conn.execute("""INSERT INTO purchases
                (purchase_id,purchase_date,local_agent,agent_phone,location,quantity_kg,price_per_kg,
                 total_purchase_cost,transport_cost,other_expenses,total_cost,quantity_received_kg,staff_user,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (pid,request.form["purchase_date"],request.form["local_agent"].strip(),
                 request.form.get("agent_phone","").strip(),request.form.get("location","").strip(),
                 qty,price,total_purchase,transport,other,total_cost,received,session["username"],now()))
            conn.commit(); conn.close()
            log_action("PURCHASE CREATED", pid, f"{received:g} KG received")
            flash(f"Purchase {pid} recorded.", "success")
            return redirect(url_for("purchases"))
        except Exception as e:
            flash(str(e), "danger")
    conn = db()
    rows = conn.execute(f"SELECT p.* FROM purchases p WHERE {visible_sql('p')} ORDER BY p.id DESC LIMIT 100").fetchall()
    conn.close()
    return render_template("purchases.html", rows=rows, today=date.today().isoformat())

@app.route("/sales", methods=["GET","POST"])
@login_required
def sales():
    if request.method == "POST":
        try:
            qty = get_float("quantity_kg")
            price = get_float("selling_price_kg")
            if qty <= 0 or price < 0:
                raise ValueError("Enter valid quantity and selling price.")
            _, _, stock = stock_summary()
            if qty > stock + 1e-9:
                raise ValueError(f"Insufficient stock. Available: {stock:,.2f} KG.")
            name = request.form["customer_name"].strip()
            phone = request.form.get("customer_phone","").strip()
            if not name:
                raise ValueError("Customer name is required.")
            tid = next_daily_id("ADU", "sales", "transaction_id")
            total = qty * price
            conn = db()
            customer = conn.execute("SELECT id FROM customers WHERE lower(name)=lower(?) AND phone=?", (name,phone)).fetchone()
            if customer:
                cid = customer["id"]
            else:
                cur = conn.execute("INSERT INTO customers(name,phone,created_at) VALUES(?,?,?)", (name,phone,now()))
                cid = cur.lastrowid
            conn.execute("""INSERT INTO sales(transaction_id,sale_date,customer_id,quantity_kg,selling_price_kg,total_sale,staff_user,created_at)
                            VALUES(?,?,?,?,?,?,?,?)""",
                         (tid,request.form["sale_date"],cid,qty,price,total,session["username"],now()))
            conn.commit(); conn.close()
            log_action("SALE CREATED", tid, f"{qty:g} KG sold to {name}")
            flash(f"Transaction {tid} created.", "success")
            return redirect(url_for("sales"))
        except Exception as e:
            flash(str(e), "danger")
    conn = db()
    rows = conn.execute(f"""SELECT s.*,c.name customer_name,c.phone,
        COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.transaction_id=s.transaction_id),0) paid
        FROM sales s JOIN customers c ON c.id=s.customer_id WHERE {visible_sql('s')} ORDER BY s.id DESC LIMIT 100""").fetchall()
    conn.close()
    _, _, stock = stock_summary()
    return render_template("sales.html", rows=rows, stock=stock, today=date.today().isoformat())

@app.route("/payments", methods=["GET","POST"])
@login_required
def payments():
    if request.method == "POST":
        try:
            tid = request.form["transaction_id"].strip()
            info = sale_info(tid)
            if not info:
                raise ValueError("Customer Transaction ID was not found.")
            amount = get_float("amount")
            if amount <= 0:
                raise ValueError("Payment amount must be greater than zero.")
            if amount > info["balance"] + 0.005:
                raise ValueError(f"Payment exceeds outstanding balance of {money(info['balance'])}.")
            conn = db()
            conn.execute("""INSERT INTO payments(transaction_id,payment_date,amount,payment_method,payment_reference,staff_user,created_at)
                            VALUES(?,?,?,?,?,?,?)""",
                         (tid,request.form["payment_date"],amount,request.form["payment_method"],
                          request.form.get("payment_reference","").strip(),session["username"],now()))
            conn.commit(); conn.close()
            log_action("PAYMENT RECORDED", tid, money(amount))
            flash(f"Payment recorded against {tid}.", "success")
            return redirect(url_for("payments"))
        except Exception as e:
            flash(str(e), "danger")
    conn = db()
    rows = conn.execute(f"""SELECT p.*,c.name customer_name,c.phone,s.total_sale invoice_amount
        FROM payments p JOIN sales s ON s.transaction_id=p.transaction_id
        JOIN customers c ON c.id=s.customer_id WHERE {visible_sql('p')} AND {visible_sql('s')} ORDER BY p.id DESC LIMIT 100""").fetchall()
    conn.close()
    return render_template("payments.html", rows=rows, today=date.today().isoformat())

@app.route("/purchases/<int:purchase_id>/edit", methods=["GET", "POST"])
@login_required
def edit_purchase(purchase_id):
    conn = db()
    row = conn.execute(f"SELECT p.* FROM purchases p WHERE p.id=? AND {visible_sql('p')}", (purchase_id,)).fetchone()
    if not row:
        abort(404)
    if request.method == "POST":
        try:
            qty = get_float("quantity_kg")
            price = get_float("price_per_kg")
            transport = get_float("transport_cost")
            other = get_float("other_expenses")
            received = get_float("quantity_received_kg")
            if qty <= 0 or received < 0 or received > qty:
                raise ValueError("Quantity received must be between 0 and quantity purchased.")
            conn.execute("""UPDATE purchases SET purchase_date=?,local_agent=?,agent_phone=?,location=?,
                quantity_kg=?,price_per_kg=?,total_purchase_cost=?,transport_cost=?,other_expenses=?,
                total_cost=?,quantity_received_kg=? WHERE id=?""",
                (request.form["purchase_date"], request.form["local_agent"].strip(), request.form.get("agent_phone", "").strip(),
                 request.form.get("location", "").strip(), qty, price, qty * price, transport, other,
                 qty * price + transport + other, received, purchase_id))
            conn.commit()
            log_action("PURCHASE UPDATED", row["purchase_id"])
            flash("Purchase updated.", "success")
            return redirect(url_for("purchases"))
        except Exception as e:
            flash(str(e), "danger")
    conn.close()
    return render_template("edit_record.html", kind="purchase", record=row)

@app.route("/sales/<int:sale_id>/edit", methods=["GET", "POST"])
@login_required
def edit_sale(sale_id):
    conn = db()
    row = conn.execute(f"""SELECT s.*,c.name customer_name,c.phone customer_phone,
        COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.transaction_id=s.transaction_id AND p.deleted=0),0) paid
        FROM sales s JOIN customers c ON c.id=s.customer_id WHERE s.id=? AND {visible_sql('s')}""", (sale_id,)).fetchone()
    if not row:
        abort(404)
    if request.method == "POST":
        try:
            qty = get_float("quantity_kg")
            price = get_float("selling_price_kg")
            if qty <= 0 or price < 0:
                raise ValueError("Enter valid quantity and selling price.")
            _, _, stock = stock_summary()
            if qty > stock + float(row["quantity_kg"]) + 1e-9:
                raise ValueError(f"Insufficient stock. Available: {stock + float(row['quantity_kg']):,.2f} KG.")
            total = qty * price
            if total + 0.005 < float(row["paid"]):
                raise ValueError("Sale total cannot be lower than payments already received.")
            name = request.form["customer_name"].strip()
            phone = request.form.get("customer_phone", "").strip()
            if not name:
                raise ValueError("Customer name is required.")
            customer = conn.execute("SELECT id FROM customers WHERE lower(name)=lower(?) AND phone=?", (name, phone)).fetchone()
            cid = customer["id"] if customer else conn.execute(
                "INSERT INTO customers(name,phone,created_at) VALUES(?,?,?)", (name, phone, now())
            ).lastrowid
            conn.execute("UPDATE sales SET sale_date=?,customer_id=?,quantity_kg=?,selling_price_kg=?,total_sale=? WHERE id=?",
                         (request.form["sale_date"], cid, qty, price, total, sale_id))
            conn.commit()
            log_action("SALE UPDATED", row["transaction_id"])
            flash("Sale updated.", "success")
            return redirect(url_for("sales"))
        except Exception as e:
            flash(str(e), "danger")
    conn.close()
    return render_template("edit_record.html", kind="sale", record=row)

@app.route("/payments/<int:payment_id>/edit", methods=["GET", "POST"])
@login_required
def edit_payment(payment_id):
    conn = db()
    row = conn.execute(f"""SELECT p.*,s.total_sale,s.deleted sale_deleted
        FROM payments p JOIN sales s ON s.transaction_id=p.transaction_id
        WHERE p.id=? AND {visible_sql('p')} AND {visible_sql('s')}""", (payment_id,)).fetchone()
    if not row:
        abort(404)
    if request.method == "POST":
        try:
            amount = get_float("amount")
            if amount <= 0:
                raise ValueError("Payment amount must be greater than zero.")
            other_paid = conn.execute("SELECT COALESCE(SUM(amount),0) v FROM payments WHERE transaction_id=? AND id!=? AND deleted=0",
                                      (row["transaction_id"], payment_id)).fetchone()["v"]
            if amount + float(other_paid) > float(row["total_sale"]) + 0.005:
                raise ValueError("Payment exceeds the sale balance.")
            conn.execute("UPDATE payments SET payment_date=?,amount=?,payment_method=?,payment_reference=? WHERE id=?",
                         (request.form["payment_date"], amount, request.form["payment_method"],
                          request.form.get("payment_reference", "").strip(), payment_id))
            conn.commit()
            log_action("PAYMENT UPDATED", row["transaction_id"], money(amount))
            flash("Payment updated.", "success")
            return redirect(url_for("payments"))
        except Exception as e:
            flash(str(e), "danger")
    conn.close()
    return render_template("edit_record.html", kind="payment", record=row)

@app.route("/records/<table>/<int:record_id>/delete", methods=["POST"])
@login_required
def delete_record_route(table, record_id):
    if table not in {"purchases", "sales", "payments"}:
        abort(404)
    conn = db()
    row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (record_id,)).fetchone()
    conn.close()
    if not row:
        abort(404)
    reference = row["purchase_id"] if table == "purchases" else row["transaction_id"]
    delete_record(table, record_id, reference)
    flash("Record deleted." if session.get("role") == "ADMIN" else "Record moved to deleted records.", "success")
    return redirect(request.referrer or url_for("dashboard"))

@app.route("/records/<table>/<int:record_id>/restore", methods=["POST"])
@login_required
@admin_required
def restore_record(table, record_id):
    if table not in {"purchases", "sales", "payments"}:
        abort(404)
    conn = db()
    conn.execute(f"UPDATE {table} SET deleted=0,deleted_at=NULL,deleted_by=NULL WHERE id=?", (record_id,))
    conn.commit()
    conn.close()
    flash("Record restored.", "success")
    return redirect(request.referrer or url_for("dashboard"))

@app.route("/invoice", methods=["GET","POST"])
@login_required
def invoice():
    tid = request.values.get("transaction_id","").strip()
    info = sale_info(tid) if tid else None
    payments_list = []
    if info:
        conn = db()
        payments_list = conn.execute("SELECT * FROM payments WHERE transaction_id=? ORDER BY payment_date,id", (tid,)).fetchall()
        conn.close()
    return render_template("invoice.html", info=info, payments=payments_list)

@app.route("/invoice/<transaction_id>/pdf")
@login_required
def invoice_pdf(transaction_id):
    info = sale_info(transaction_id)
    if not info:
        abort(404)
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.enums import TA_RIGHT, TA_CENTER
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib.units import mm
    except ImportError:
        flash("Install reportlab with: pip install reportlab", "danger")
        return redirect(url_for("invoice", transaction_id=transaction_id))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=18*mm,leftMargin=18*mm,topMargin=18*mm,bottomMargin=18*mm)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="SmallRight", parent=styles["Normal"], alignment=TA_RIGHT, fontSize=9))
    styles.add(ParagraphStyle(name="Center", parent=styles["Normal"], alignment=TA_CENTER))
    story = []
    story += [Paragraph("<b>ADUFARMS</b>", styles["Title"]),
              Paragraph("MAIZE DISTRIBUTION", styles["Heading3"]), Spacer(1,8)]
    meta = [
        ["Invoice No.", info["transaction_id"], "Date", pretty_date(info["sale_date"])],
        ["Customer", info["customer_name"], "Phone", info["customer_phone"] or "-"]
    ]
    t = Table(meta, colWidths=[28*mm,70*mm,25*mm,55*mm])
    t.setStyle(TableStyle([("GRID",(0,0),(-1,-1),0.4,colors.grey),("BACKGROUND",(0,0),(0,-1),colors.lightgrey)]))
    story += [t, Spacer(1,15)]
    data = [["Description","Quantity (KG)","Price/KG","Amount"],
            ["Maize",f'{info["quantity_kg"]:,.2f}',money(info["selling_price_kg"]),money(info["total_sale"])]]
    t2=Table(data,colWidths=[70*mm,35*mm,35*mm,40*mm])
    t2.setStyle(TableStyle([("GRID",(0,0),(-1,-1),0.5,colors.grey),("BACKGROUND",(0,0),(-1,0),colors.lightgrey),
                            ("ALIGN",(1,1),(-1,-1),"RIGHT")]))
    story += [t2, Spacer(1,15)]
    totals=[["TOTAL",money(info["total_sale"])],
            ["TOTAL PAID",money(info["total_paid"])],
            ["BALANCE",money(info["balance"])],
            ["PAYMENT STATUS",info["status"]]]
    t3=Table(totals,colWidths=[120*mm,60*mm],hAlign="RIGHT")
    t3.setStyle(TableStyle([("GRID",(0,0),(-1,-1),0.4,colors.grey),("ALIGN",(1,0),(1,-1),"RIGHT"),
                            ("BACKGROUND",(0,0),(0,-1),colors.lightgrey)]))
    story += [t3, Spacer(1,25), Paragraph("Thank you for doing business with ADUFARMS.", styles["Center"])]
    doc.build(story); buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=f"{transaction_id}.pdf", mimetype="application/pdf")

@app.route("/search")
@login_required
def search():
    q = request.args.get("q","").strip()
    results=[]
    if q:
        conn=db()
        results=conn.execute("""SELECT s.transaction_id,s.sale_date,c.name customer_name,c.phone,
            s.quantity_kg,s.selling_price_kg,s.total_sale,
            COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.transaction_id=s.transaction_id AND p.deleted=0),0) paid
            FROM sales s JOIN customers c ON c.id=s.customer_id
            WHERE ({visible_sql('s')}) AND (s.transaction_id LIKE ? OR c.name LIKE ? OR c.phone LIKE ?)
            ORDER BY s.id DESC""",(f"%{q}%",f"%{q}%",f"%{q}%")).fetchall()
        purchases=conn.execute(f"""SELECT p.* FROM purchases p WHERE ({visible_sql('p')}) AND (purchase_id LIKE ? OR local_agent LIKE ? OR location LIKE ?)
            ORDER BY id DESC""",(f"%{q}%",f"%{q}%",f"%{q}%")).fetchall()
        conn.close()
    else:
        purchases=[]
    return render_template("search.html", q=q, results=results, purchases=purchases)

@app.route("/search/transaction/<transaction_id>")
@login_required
def transaction_history(transaction_id):
    info=sale_info(transaction_id)
    if not info: abort(404)
    conn=db()
    pay=conn.execute("SELECT * FROM payments WHERE transaction_id=? ORDER BY payment_date,id",(transaction_id,)).fetchall()
    conn.close()
    return render_template("history.html", info=info, payments=pay)

@app.route("/users", methods=["GET","POST"])
@login_required
@admin_required
def users():
    if request.method=="POST":
        username=request.form["username"].strip()
        full_name=request.form["full_name"].strip()
        password=request.form["password"]
        role=request.form.get("role","STAFF")
        try:
            conn=db()
            conn.execute("INSERT INTO users(username,full_name,password_hash,role,created_at) VALUES(?,?,?,?,?)",
                         (username,full_name,generate_password_hash(password),role,now()))
            conn.commit(); conn.close()
            flash("User created.", "success")
        except sqlite3.IntegrityError:
            flash("Username already exists.", "danger")
    conn=db()
    rows=conn.execute("SELECT id,username,full_name,role,active,created_at FROM users ORDER BY id").fetchall()
    conn.close()
    return render_template("users.html", rows=rows)

@app.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def edit_user(user_id):
    conn = db()
    row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        abort(404)
    if request.method == "POST":
        username = request.form["username"].strip()
        full_name = request.form["full_name"].strip()
        password = request.form.get("password", "")
        role = request.form.get("role", "STAFF")
        if not username or not full_name or role not in {"ADMIN", "STAFF"}:
            flash("Enter valid user details.", "danger")
        else:
            try:
                if password:
                    conn.execute("UPDATE users SET username=?,full_name=?,password_hash=?,role=? WHERE id=?",
                                 (username, full_name, generate_password_hash(password), role, user_id))
                else:
                    conn.execute("UPDATE users SET username=?,full_name=?,role=? WHERE id=?",
                                 (username, full_name, role, user_id))
                conn.commit()
                flash("User updated.", "success")
                return redirect(url_for("users"))
            except sqlite3.IntegrityError:
                flash("Username already exists.", "danger")
    conn.close()
    return render_template("edit_record.html", kind="user", record=row)

@app.route("/users/<int:user_id>/toggle", methods=["POST"])
@login_required
@admin_required
def toggle_user(user_id):
    conn=db()
    conn.execute("UPDATE users SET active=CASE active WHEN 1 THEN 0 ELSE 1 END WHERE id=? AND username!='admin'",(user_id,))
    conn.commit(); conn.close()
    return redirect(url_for("users"))

@app.route("/users/<int:user_id>/delete", methods=["POST"])
@login_required
@admin_required
def delete_user(user_id):
    conn = db()
    conn.execute("DELETE FROM users WHERE id=? AND username!='admin'", (user_id,))
    conn.commit()
    conn.close()
    flash("User permanently deleted.", "success")
    return redirect(url_for("users"))

@app.route("/health")
def health():
    return {"status":"ok","application":"ADUFARMS","time":now()}

if __name__ == "__main__":
    init_db()
    app.run(debug=True, host="127.0.0.1", port=5000)
