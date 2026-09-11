from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file, abort
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime, date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
import sqlite3
import os
import io
import csv
import secrets
import hmac
from markupsafe import Markup
from werkzeug.utils import secure_filename

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except Exception:
    pass

import stock_service as stock_svc
import backup_service as backup_svc
import assistant_service

BASE_DIR = Path(__file__).resolve().parent
_DB_ENV = os.environ.get("DATABASE_PATH") or os.environ.get("ADUFARMS_DB_PATH")
DB_PATH = Path(_DB_ENV) if _DB_ENV else (BASE_DIR / "adufarms.db")
EXPORT_DIR = BASE_DIR / "exports" / "invoices"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)
PROFILE_DIR = BASE_DIR / "static" / "images" / "users"
PROFILE_DIR.mkdir(parents=True, exist_ok=True)
BACKUP_DIR = BASE_DIR / "backups"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("ADUFARMS_SECRET_KEY") or os.urandom(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("ADUFARMS_COOKIE_SECURE", "0") == "1",
    MAX_CONTENT_LENGTH=2 * 1024 * 1024,
    PERMANENT_SESSION_LIFETIME=timedelta(minutes=30),
)
app.config["DATABASE"] = str(DB_PATH)

ALLOWED_PROFILE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def db():
    conn = sqlite3.connect(app.config["DATABASE"])
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    # Backup before migrations (never silently overwrite the only backup)
    if DB_PATH.exists() and DB_PATH.stat().st_size > 0:
        try:
            backup_svc.create_backup(str(DB_PATH))
        except Exception:
            app.logger.exception("Pre-migration backup failed")
    conn = db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        full_name TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'STAFF',
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        last_login TEXT,
        profile_image TEXT
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
        sales_id TEXT UNIQUE,
        invoice_number TEXT UNIQUE,
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
        payment_id TEXT UNIQUE,
        transaction_id TEXT NOT NULL,
        sales_id TEXT,
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

    CREATE TABLE IF NOT EXISTS invoices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_number TEXT UNIQUE NOT NULL,
        transaction_id TEXT UNIQUE NOT NULL,
        sales_id TEXT UNIQUE,
        invoice_date TEXT NOT NULL,
        generated_by TEXT NOT NULL,
        generated_at TEXT NOT NULL,
        last_regenerated_at TEXT,
        deleted INTEGER NOT NULL DEFAULT 0,
        deleted_at TEXT,
        deleted_by TEXT,
        deletion_reason TEXT,
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

    CREATE TABLE IF NOT EXISTS reversals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        table_name TEXT NOT NULL,
        record_id INTEGER NOT NULL,
        reference TEXT NOT NULL,
        reason TEXT NOT NULL,
        reversed_by TEXT NOT NULL,
        reversed_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS stock_movements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        movement_type TEXT NOT NULL CHECK(movement_type IN ('PURCHASE', 'SALE', 'REVERSAL', 'ADJUSTMENT')),
        reference TEXT NOT NULL,
        quantity_kg REAL NOT NULL,
        movement_date TEXT NOT NULL,
        created_by TEXT NOT NULL,
        notes TEXT,
        created_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_sales_customer ON sales(customer_id);
    CREATE INDEX IF NOT EXISTS idx_sales_date ON sales(sale_date);
    CREATE INDEX IF NOT EXISTS idx_purchases_date ON purchases(purchase_date);
    CREATE INDEX IF NOT EXISTS idx_customer_name ON customers(name);
    CREATE INDEX IF NOT EXISTS idx_customer_phone ON customers(phone);
    CREATE INDEX IF NOT EXISTS idx_user_role ON users(role);
    CREATE INDEX IF NOT EXISTS idx_payments_transaction ON payments(transaction_id);
    CREATE INDEX IF NOT EXISTS idx_payments_date ON payments(payment_date);
    CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at);
    CREATE INDEX IF NOT EXISTS idx_stock_reference ON stock_movements(reference);
    CREATE INDEX IF NOT EXISTS idx_invoices_sales ON invoices(sales_id);
    """)
    user_columns = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "last_login" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN last_login TEXT")
    if "profile_image" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN profile_image TEXT")
    customer_columns = {row[1] for row in conn.execute("PRAGMA table_info(customers)").fetchall()}
    for column, definition in {
        "address": "TEXT",
        "location": "TEXT",
        "customer_type": "TEXT NOT NULL DEFAULT 'RETAIL'",
        "opening_balance": "REAL NOT NULL DEFAULT 0",
        "notes": "TEXT",
        "active": "INTEGER NOT NULL DEFAULT 1",
        "deleted_at": "TEXT",
        "deleted_by": "TEXT",
        "deletion_reason": "TEXT",
    }.items():
        if column not in customer_columns:
            conn.execute(f"ALTER TABLE customers ADD COLUMN {column} {definition}")
    sales_columns = {row[1] for row in conn.execute("PRAGMA table_info(sales)").fetchall()}
    if "sales_id" not in sales_columns:
        conn.execute("ALTER TABLE sales ADD COLUMN sales_id TEXT")
    if "invoice_number" not in sales_columns:
        conn.execute("ALTER TABLE sales ADD COLUMN invoice_number TEXT")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_sales_sales_id ON sales(sales_id)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_sales_invoice_number ON sales(invoice_number)")
    existing_sales = conn.execute("SELECT id, sale_date FROM sales WHERE sales_id IS NULL OR sales_id = '' ORDER BY sale_date, id").fetchall()
    for row in existing_sales:
        sale_date = row["sale_date"] or date.today().isoformat()
        try:
            id_date = datetime.strptime(sale_date, "%Y-%m-%d").date()
        except ValueError:
            id_date = date.today()
        generated = next_daily_id("ADU-SAL", "sales", "sales_id", conn, id_date)
        conn.execute("UPDATE sales SET sales_id=? WHERE id=?", (generated, row["id"]))
    for table in ("purchases", "sales", "payments"):
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if "deleted" not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0")
        if "deleted_at" not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN deleted_at TEXT")
        if "deleted_by" not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN deleted_by TEXT")
        if "deletion_reason" not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN deletion_reason TEXT")
    payment_columns = {row[1] for row in conn.execute("PRAGMA table_info(payments)").fetchall()}
    if "payment_id" not in payment_columns:
        conn.execute("ALTER TABLE payments ADD COLUMN payment_id TEXT")
    if "sales_id" not in payment_columns:
        conn.execute("ALTER TABLE payments ADD COLUMN sales_id TEXT")
    if "notes" not in payment_columns:
        conn.execute("ALTER TABLE payments ADD COLUMN notes TEXT")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_payments_payment_id ON payments(payment_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_payments_sales_id ON payments(sales_id)")
    conn.execute("UPDATE payments SET sales_id=(SELECT s.sales_id FROM sales s WHERE s.transaction_id=payments.transaction_id) WHERE sales_id IS NULL OR sales_id='' ")
    invoice_columns = {row[1] for row in conn.execute("PRAGMA table_info(invoices)").fetchall()}
    for column, definition in {
        "deleted": "INTEGER NOT NULL DEFAULT 0",
        "deleted_at": "TEXT",
        "deleted_by": "TEXT",
        "deletion_reason": "TEXT",
    }.items():
        if column not in invoice_columns:
            conn.execute(f"ALTER TABLE invoices ADD COLUMN {column} {definition}")
    invoice_columns = {row[1] for row in conn.execute("PRAGMA table_info(invoices)").fetchall()}
    if "sales_id" not in invoice_columns:
        conn.execute("ALTER TABLE invoices ADD COLUMN sales_id TEXT")
    conn.execute("UPDATE invoices SET sales_id=(SELECT s.sales_id FROM sales s WHERE s.transaction_id=invoices.transaction_id) WHERE sales_id IS NULL OR sales_id='' ")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_invoices_sales_id ON invoices(sales_id)")
    conn.execute("UPDATE sales SET invoice_number=(SELECT i.invoice_number FROM invoices i WHERE i.transaction_id=sales.transaction_id) WHERE invoice_number IS NULL OR invoice_number='' ")
    payment_rows = conn.execute("SELECT id,payment_date,transaction_id FROM payments WHERE payment_id IS NULL OR payment_id='' ORDER BY payment_date,id").fetchall()
    for row in payment_rows:
        try:
            payment_date = datetime.strptime(row["payment_date"], "%Y-%m-%d").date()
        except (TypeError, ValueError):
            payment_date = date.today()
        payment_id = next_daily_id("ADU-PAY", "payments", "payment_id", conn, payment_date)
        conn.execute("UPDATE payments SET payment_id=? WHERE id=?", (payment_id, row["id"]))
    conn.execute("UPDATE payments SET sales_id=(SELECT s.sales_id FROM sales s WHERE s.transaction_id=payments.transaction_id) WHERE sales_id IS NULL OR sales_id='' ")
    invoice_rows = conn.execute("""SELECT s.transaction_id,s.sales_id,s.sale_date,s.staff_user,s.created_at
        FROM sales s LEFT JOIN invoices i ON i.transaction_id=s.transaction_id
        WHERE i.transaction_id IS NULL ORDER BY s.sale_date,s.id""").fetchall()
    for row in invoice_rows:
        try:
            invoice_date = datetime.strptime(row["sale_date"], "%Y-%m-%d").date()
        except (TypeError, ValueError):
            invoice_date = date.today()
        invoice_number = next_daily_id("ADU-INV", "invoices", "invoice_number", conn, invoice_date)
        conn.execute("""INSERT INTO invoices(invoice_number,transaction_id,sales_id,invoice_date,generated_by,generated_at)
                        VALUES(?,?,?,?,?,?)""",
                     (invoice_number, row["transaction_id"], row["sales_id"], row["sale_date"],
                      row["staff_user"], row["created_at"] or now()))
        conn.execute("UPDATE sales SET invoice_number=? WHERE transaction_id=?",
                 (invoice_number, row["transaction_id"]))
    conn.execute("""INSERT INTO stock_movements
        (movement_type,reference,quantity_kg,movement_date,created_by,notes,created_at)
        SELECT 'PURCHASE',p.purchase_id,p.quantity_received_kg,p.purchase_date,p.staff_user,
               'Historical movement backfill',p.created_at FROM purchases p
        WHERE p.deleted=0 AND NOT EXISTS
        (SELECT 1 FROM stock_movements m WHERE m.movement_type='PURCHASE' AND m.reference=p.purchase_id)""")
    conn.execute("""INSERT INTO stock_movements
        (movement_type,reference,quantity_kg,movement_date,created_by,notes,created_at)
        SELECT 'SALE',s.transaction_id,-s.quantity_kg,s.sale_date,s.staff_user,
               'Historical movement backfill',s.created_at FROM sales s
        WHERE s.deleted=0 AND NOT EXISTS
        (SELECT 1 FROM stock_movements m WHERE m.movement_type='SALE' AND m.reference=s.transaction_id)""")
    conn.commit()
    conn.close()


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def csrf_input():
    return Markup(f'<input type="hidden" name="csrf_token" value="{csrf_token()}">')


@app.context_processor
def security_context():
    notifications = []
    if session.get("user_id"):
        conn = db()
        stock = conn.execute("SELECT COALESCE(SUM(quantity_received_kg),0) v FROM purchases WHERE deleted=0").fetchone()["v"] - conn.execute("SELECT COALESCE(SUM(quantity_kg),0) v FROM sales WHERE deleted=0").fetchone()["v"]
        unpaid = conn.execute("""SELECT COUNT(*) v FROM sales s WHERE s.deleted=0 AND
            COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.transaction_id=s.transaction_id AND p.deleted=0),0) < s.total_sale""").fetchone()["v"]
        latest = conn.execute("SELECT action,reference FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()
        if stock <= 0:
            notifications.append(("Stock depleted", "No available maize stock remains.", "danger", "box-seam"))
        if unpaid:
            notifications.append((f"{unpaid} unpaid balance" if unpaid != 1 else "1 unpaid balance", "Review customer balances.", "warning", "exclamation-circle"))
        if latest:
            notifications.append(("Recent transaction", f"{latest['action'].title()} {latest['reference'] or ''}".strip(), "info", "activity"))
    return {"csrf_input": csrf_input, "csrf_token": csrf_token, "ui_notifications": notifications}


app.jinja_env.globals.update(csrf_input=csrf_input, csrf_token=csrf_token)


@app.before_request
def protect_post_requests():
    # Server-side RBAC: staff must not bypass admin URLs.
    admin_only_endpoints = {"delete_record_route", "restore_record", "users", "edit_user",
                            "toggle_user", "delete_user", "audit_logs", "admin_backup", "admin_restore"}
    if request.endpoint in admin_only_endpoints and str(session.get("role", "")).upper() != "ADMIN":
        # Allow unauthenticated to fall through to login_required (redirect) rather than 403
        if "user_id" in session:
            abort(403)
    if request.method == "POST" and request.endpoint != "login":
        submitted = request.form.get("csrf_token", "")
        if not submitted or not hmac.compare_digest(submitted, session.get("csrf_token", "")):
            abort(400, description="Your form session expired. Please reload the page and try again.")


def valid_date(value, field_name):
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return value
    except (TypeError, ValueError):
        raise ValueError(f"Enter a valid {field_name}.")


def nonnegative_float(name, default=0):
    value = get_float(name, default)
    if value < 0:
        raise ValueError(f"{name.replace('_', ' ').capitalize()} cannot be negative.")
    return value


def add_stock_movement(movement_type, reference, quantity, movement_date, notes=""):
    # Legacy helper retained for compatibility; delegates to centralized service
    # within its own atomic connection.
    conn = db()
    try:
        stock_svc.record_movement(conn, movement_type, reference, quantity,
                                  movement_date, session.get("username", "system"), notes, now())
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


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
        if str(session.get("role", "")).upper() != "ADMIN":
            abort(403)
        return f(*args, **kwargs)
    return wrapper


def money(v):
    return f"GH\u20b5{float(v or 0):,.2f}"


def get_float(name, default=0):
    raw = request.form.get(name, "").strip()
    if raw == "":
        return float(default)
    try:
        value = float(Decimal(raw))
        return value
    except (InvalidOperation, ValueError):
        raise ValueError(f"Invalid value for {name.replace('_',' ')}.")


def next_daily_id(prefix, table, column, conn=None, id_date=None):
    today = (id_date or date.today()).strftime("%Y%m%d")
    owns_connection = conn is None
    conn = conn or db()
    pattern = f"{prefix}-{today}-%"
    rows = conn.execute(
        f"SELECT {column} FROM {table} WHERE {column} LIKE ?",
        (pattern,)
    ).fetchall()
    if owns_connection:
        conn.close()
    n = 1
    numbers = []
    for row in rows:
        try:
            numbers.append(int(row[0].rsplit("-", 1)[1]))
        except Exception:
            continue
    if numbers:
        n = max(numbers) + 1
    return f"{prefix}-{today}-{n:04d}"


def invoice_number_for(sale_reference: str) -> str:
    if not sale_reference:
        return ""
    ref = sale_reference.strip().upper()
    if ref.startswith("ADU-INV-"):
        return ref
    if ref.startswith("INV-"):
        return ref
    if ref.startswith("ADU-SAL-"):
        return ref.replace("ADU-SAL-", "ADU-INV-", 1)
    if ref.startswith("ADU-"):
        return f"ADU-INV-{ref[4:]}"
    return f"ADU-INV-{ref}"


def ensure_invoice_record(info):
    """Create or retrieve the single persisted invoice for a saved sale."""
    conn = db()
    try:
        existing = conn.execute(
            "SELECT invoice_number FROM invoices WHERE transaction_id=?",
            (info["transaction_id"],)
        ).fetchone()
        if existing:
            conn.execute("UPDATE sales SET invoice_number=? WHERE transaction_id=?",
                         (existing["invoice_number"], info["transaction_id"]))
            conn.commit()
            return existing["invoice_number"]
        sale_date = datetime.strptime(info["sale_date"], "%Y-%m-%d").date()
        invoice_number = next_daily_id("ADU-INV", "invoices", "invoice_number", conn, sale_date)
        timestamp = now()
        conn.execute("""INSERT INTO invoices(invoice_number,transaction_id,sales_id,invoice_date,generated_by,generated_at)
                        VALUES(?,?,?,?,?,?)""",
                     (invoice_number, info["transaction_id"], info["sales_id"], info["sale_date"],
                      session.get("username", "system"), timestamp))
        conn.execute("UPDATE sales SET invoice_number=? WHERE transaction_id=?",
                 (invoice_number, info["transaction_id"]))
        conn.execute("INSERT INTO audit_log(username,action,reference,details,created_at) VALUES(?,?,?,?,?)",
                     (actor_label() if session.get("username") else "system", "INVOICE GENERATED",
                      invoice_number, f"sales_id={info['sales_id']}; transaction_id={info['transaction_id']}", timestamp))
        conn.commit()
        return invoice_number
    except sqlite3.IntegrityError:
        conn.rollback()
        existing = conn.execute(
            "SELECT invoice_number FROM invoices WHERE transaction_id=?",
            (info["transaction_id"],)
        ).fetchone()
        if existing:
            return existing["invoice_number"]
        raise
    finally:
        conn.close()


def can_see_deleted():
    return str(session.get("role", "")).upper() == "ADMIN"


def visible_sql(alias):
    return "1=1" if can_see_deleted() else f"{alias}.deleted=0"


def actor_label():
    return f"{session.get('username')} (id={session.get('user_id')})"


SPEC_DELETE = {"purchases": "PURCHASE", "sales": "SALE", "payments": "PAYMENT"}
SPEC_REVERSE = {"purchases": "PURCHASE REVERSED", "sales": "SALE REVERSED", "payments": "PAYMENT REVERSED"}
SPEC_RESTORE = {"purchases": "PURCHASE RESTORED", "sales": "SALE RESTORED", "payments": "PAYMENT RESTORED"}


class PurchaseStockInUseError(ValueError):
    """Raised when a purchase cannot be removed without invalidating stock."""

    def __init__(self, purchase_id):
        super().__init__("Purchase stock is already used by active sales.")
        self.purchase_id = purchase_id


class CustomerHasDependenciesError(ValueError):
    """Raised when financial history prevents permanent customer deletion."""

    def __init__(self, customer_id):
        super().__init__("Customer has existing financial records.")
        self.customer_id = customer_id


def hard_delete_record(table, record_id, reference):
    conn = db()
    try:
        reason = request.form.get("reason", "").strip()
        if not reason:
            raise ValueError("A reason for deletion is required.")
        row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (record_id,)).fetchone()
        if not row:
            raise ValueError("This transaction is unavailable.")
        if table == "purchases" and not row["deleted"]:
            available = stock_svc.available_stock(conn)
            if float(row["quantity_received_kg"]) > available + 1e-9:
                raise PurchaseStockInUseError(record_id)
        details = "; ".join(f"{key}={row[key]}" for key in row.keys()) + f"; reason={reason}"
        transaction_type = SPEC_DELETE.get(table, table.rstrip("s").upper())
        conn.execute(
            "INSERT INTO audit_log(username,action,reference,details,created_at) VALUES(?,?,?,?,?)",
            (actor_label(), "DELETE", reference, f"type={transaction_type}; {details}", now())
        )
        if table == "sales":
            conn.execute("DELETE FROM payments WHERE transaction_id=?", (row["transaction_id"],))
            conn.execute("DELETE FROM invoices WHERE transaction_id=?", (row["transaction_id"],))
            conn.execute("DELETE FROM stock_movements WHERE reference=?", (reference,))
        elif table == "purchases":
            conn.execute("DELETE FROM stock_movements WHERE reference=?", (reference,))
        conn.execute("DELETE FROM reversals WHERE table_name=? AND record_id=?", (table, record_id))
        conn.execute(f"DELETE FROM {table} WHERE id=?", (record_id,))
        conn.commit()
    except (sqlite3.Error, ValueError):
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_record(table, record_id, reference):
    conn = db()
    try:
        reason = request.form.get("reason", "").strip()
        if not reason:
            raise ValueError("A reversal reason is required.")
        row = conn.execute(f"SELECT * FROM {table} WHERE id=? AND deleted=0", (record_id,)).fetchone()
        if not row:
            raise ValueError("This transaction is already reversed or unavailable.")
        ts = now()
        by = session["username"]
        if table == "sales":
            qty = float(row["quantity_kg"])
            stock_svc.apply_reversal(conn, table, reference, qty, row["sale_date"], by, ts, reason)
            conn.execute("UPDATE sales SET deleted=1,deleted_at=?,deleted_by=?,deletion_reason=? WHERE id=?", (ts, by, reason, record_id))
            conn.execute("UPDATE payments SET deleted=1,deleted_at=?,deleted_by=? WHERE transaction_id=? AND deleted=0",
                         (ts, by, row["transaction_id"]))
            conn.execute("UPDATE payments SET deletion_reason=? WHERE transaction_id=? AND deleted=1",
                         (reason, row["transaction_id"]))
        elif table == "purchases":
            qty = float(row["quantity_received_kg"])
            stock_svc.apply_reversal(conn, table, reference, qty, row["purchase_date"], by, ts, reason)
            conn.execute("UPDATE purchases SET deleted=1,deleted_at=?,deleted_by=?,deletion_reason=? WHERE id=?", (ts, by, reason, record_id))
        else:
            conn.execute("UPDATE payments SET deleted=1,deleted_at=?,deleted_by=?,deletion_reason=? WHERE id=?", (ts, by, reason, record_id))
            stock_svc.apply_reversal(conn, table, reference, 0, row["payment_date"], by, ts, reason)
        conn.execute("INSERT INTO reversals(table_name,record_id,reference,reason,reversed_by,reversed_at) VALUES(?,?,?,?,?,?)",
                     (table, record_id, reference, reason, by, ts))
        spec_action = SPEC_REVERSE.get(table, "TRANSACTION REVERSED")
        qty_log = row['quantity_kg'] if table == 'sales' else row['quantity_received_kg'] if table == 'purchases' else 0
        amt_log = row['total_sale'] if table == 'sales' else row['total_cost'] if table == 'purchases' else row['amount'] if table == 'payments' else 0
        conn.execute("INSERT INTO audit_log(username,action,reference,details,created_at) VALUES(?,?,?,?,?)",
                     (f"{by} (id={session.get('user_id')})", spec_action, reference,
                      f"type={table}; previous_status=ACTIVE; new_status=REVERSED; reason={reason}; quantity_kg={qty_log}; amount={amt_log}", ts))
        if table == "sales":
            conn.execute("UPDATE invoices SET deleted=1,deleted_at=?,deleted_by=?,deletion_reason=? WHERE transaction_id=?",
                         (ts, by, reason, row["transaction_id"]))
        conn.commit()
    except (sqlite3.Error, ValueError):
        conn.rollback()
        raise
    finally:
        conn.close()


def stock_summary():
    conn = db()
    purchased = conn.execute("SELECT COALESCE(SUM(quantity_received_kg),0) v FROM purchases WHERE deleted=0").fetchone()["v"]
    sold = conn.execute("SELECT COALESCE(SUM(quantity_kg),0) v FROM sales WHERE deleted=0").fetchone()["v"]
    conn.close()
    return float(purchased), float(sold), float(purchased - sold)


def invoice_status_for(total_sale, total_paid):
    total_sale = float(total_sale or 0)
    total_paid = float(total_paid or 0)
    if total_paid > total_sale + 0.005:
        return "OVERPAID"
    if total_sale - total_paid <= 0.005:
        return "PAID"
    if total_paid > 0:
        return "PART PAYMENT"
    return "UNPAID"


def payment_info(payment_ref):
    ref = (payment_ref or "").strip()
    if not ref:
        return None
    conn = db()
    row = conn.execute("""
        SELECT p.*, s.sales_id, s.transaction_id, s.sale_date, s.total_sale, s.quantity_kg,
               s.selling_price_kg, c.name customer_name, c.phone customer_phone,
               c.address customer_address, c.location customer_location,
               (SELECT SUM(pp.amount) FROM payments pp WHERE pp.transaction_id=s.transaction_id AND pp.deleted=0) total_paid
        FROM payments p
        JOIN sales s ON s.transaction_id=p.transaction_id
        JOIN customers c ON c.id=s.customer_id
        WHERE p.deleted=0 AND s.deleted=0 AND (p.payment_id=? OR p.id=? OR p.transaction_id=? OR p.sales_id=? OR s.sales_id=?)
    """, (ref, ref if str(ref).isdigit() else -1, ref, ref, ref)).fetchone()
    conn.close()
    if not row:
        return None
    info = dict(row)
    info["balance"] = max(float(info["total_sale"]) - float(info["total_paid"] or 0), 0)
    info["status"] = invoice_status_for(info["total_sale"], info["total_paid"])
    info["invoice_number"] = info.get("invoice_number") or invoice_number_for(info["sales_id"])
    return info


def sale_info(transaction_id):
    ref = (transaction_id or "").strip()
    if not ref:
        return None
    conn = db()
    row = conn.execute(f"""
         SELECT s.*, c.name customer_name, c.phone customer_phone,
             c.address customer_address, c.location customer_location,
             COALESCE(s.invoice_number, (SELECT i.invoice_number FROM invoices i WHERE i.transaction_id=s.transaction_id)) invoice_number,
             COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.transaction_id=s.transaction_id AND p.deleted=0),0) total_paid
        FROM sales s JOIN customers c ON c.id=s.customer_id
        WHERE ({visible_sql('s')}) AND (s.transaction_id=? OR s.sales_id=?
            OR EXISTS (SELECT 1 FROM invoices i WHERE i.transaction_id=s.transaction_id AND i.invoice_number=?)
            OR EXISTS (SELECT 1 FROM payments p WHERE p.transaction_id=s.transaction_id AND p.payment_id=? OR p.sales_id=?))
    """, (ref, ref, ref, ref, ref)).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["sales_id"] = d.get("sales_id") or d.get("transaction_id")
    d["balance"] = max(float(d["total_sale"]) - float(d["total_paid"]), 0)
    d["status"] = invoice_status_for(d["total_sale"], d["total_paid"])
    d["invoice_number"] = d.get("invoice_number") or invoice_number_for(d["sales_id"])
    return d


def log_action(action, reference="", details=""):
    conn = db()
    conn.execute(
        "INSERT INTO audit_log(username,action,reference,details,created_at) VALUES(?,?,?,?,?)",
        (actor_label() if session.get("username") else session.get("username"), action, reference, details, now())
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
            session.permanent = True
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["full_name"] = user["full_name"]
            session["role"] = user["role"]
            conn = db()
            conn.execute("UPDATE users SET last_login=? WHERE id=?", (now(), user["id"]))
            conn.commit()
            conn.close()
            log_action("LOGIN", username, f"user_id={user['id']}; role={user['role']}")
            return redirect(url_for("dashboard"))
        flash("Invalid username or password.", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    if session.get("username"):
        log_action("LOGOUT", session.get("username"), f"user_id={session.get('user_id')}")
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    purchased, sold, stock = stock_summary()
    conn = db()
    today = date.today().isoformat()
    month_start = date.today().replace(day=1).isoformat()
    sales = conn.execute("SELECT COALESCE(SUM(total_sale),0) v FROM sales WHERE deleted=0").fetchone()["v"]
    payments = conn.execute("SELECT COALESCE(SUM(amount),0) v FROM payments WHERE deleted=0").fetchone()["v"]
    purchase_cost = conn.execute("SELECT COALESCE(SUM(total_purchase_cost),0) v FROM purchases WHERE deleted=0").fetchone()["v"]
    transport = conn.execute("SELECT COALESCE(SUM(transport_cost),0) v FROM purchases WHERE deleted=0").fetchone()["v"]
    other = conn.execute("SELECT COALESCE(SUM(other_expenses),0) v FROM purchases WHERE deleted=0").fetchone()["v"]
    customers = conn.execute("SELECT COUNT(*) v FROM customers WHERE active=1").fetchone()["v"]
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
    recent = conn.execute("""SELECT s.sales_id transaction_id,s.sales_id,s.sale_date,c.name,s.quantity_kg,s.total_sale,
        COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.transaction_id=s.transaction_id AND p.deleted=0),0) paid
        FROM sales s JOIN customers c ON c.id=s.customer_id WHERE s.deleted=0 ORDER BY s.id DESC LIMIT 8""").fetchall()
    recent_purchases = conn.execute("""SELECT purchase_id,purchase_date,local_agent,quantity_received_kg,total_cost
        FROM purchases WHERE deleted=0 ORDER BY id DESC LIMIT 6""").fetchall()
    recent_payments = conn.execute("""SELECT p.payment_id,p.transaction_id,s.sales_id,p.payment_date,p.amount,p.payment_method,c.name customer_name
        FROM payments p JOIN sales s ON s.transaction_id=p.transaction_id JOIN customers c ON c.id=s.customer_id
        WHERE p.deleted=0 AND s.deleted=0 ORDER BY p.id DESC LIMIT 6""").fetchall()
    recent_invoices = conn.execute("""SELECT i.invoice_number,s.sales_id,i.invoice_date,c.name customer_name
        FROM invoices i JOIN sales s ON s.transaction_id=i.transaction_id JOIN customers c ON c.id=s.customer_id
        WHERE s.deleted=0 ORDER BY i.id DESC LIMIT 6""").fetchall()
    outstanding_customers = conn.execute("""SELECT c.id,c.name,c.phone,
        COALESCE(SUM(s.total_sale),0) total_sales,
        COALESCE((SELECT SUM(p.amount) FROM payments p JOIN sales ps ON ps.transaction_id=p.transaction_id
                  WHERE ps.customer_id=c.id AND ps.deleted=0 AND p.deleted=0),0) total_paid
        FROM customers c JOIN sales s ON s.customer_id=c.id AND s.deleted=0
        GROUP BY c.id HAVING total_sales-total_paid > 0.005 ORDER BY total_sales-total_paid DESC LIMIT 8""").fetchall()
    monthly_sales = conn.execute("""SELECT substr(sale_date,1,7) month,COALESCE(SUM(total_sale),0) revenue,
        COALESCE(SUM(quantity_kg),0) quantity FROM sales WHERE deleted=0 GROUP BY month ORDER BY month DESC LIMIT 6""").fetchall()
    monthly_purchases = conn.execute("""SELECT substr(purchase_date,1,7) month,COALESCE(SUM(total_cost),0) cost,
        COALESCE(SUM(quantity_received_kg),0) quantity FROM purchases WHERE deleted=0 GROUP BY month ORDER BY month DESC LIMIT 6""").fetchall()
    supplier_count = conn.execute("SELECT COUNT(DISTINCT local_agent) v FROM purchases WHERE deleted=0").fetchone()["v"]
    today_sales = conn.execute("SELECT COALESCE(SUM(total_sale),0) v FROM sales WHERE deleted=0 AND sale_date=?", (today,)).fetchone()["v"]
    today_payments = conn.execute("SELECT COALESCE(SUM(amount),0) v FROM payments WHERE deleted=0 AND payment_date=?", (today,)).fetchone()["v"]
    month_sales = conn.execute("SELECT COALESCE(SUM(total_sale),0) v FROM sales WHERE deleted=0 AND sale_date>=?", (month_start,)).fetchone()["v"]
    month_payments = conn.execute("SELECT COALESCE(SUM(amount),0) v FROM payments WHERE deleted=0 AND payment_date>=?", (month_start,)).fetchone()["v"]
    conn.close()
    stats = dict(purchased=purchased,sold=sold,stock=stock,sales=float(sales),payments=float(payments),
                 outstanding=outstanding,purchase_cost=float(purchase_cost),transport=float(transport),
                 other=float(other),expenses=expenses,profit=profit,customers=customers,transactions=transactions,
                 paid=paid,part=part,unpaid=unpaid,suppliers=supplier_count,
                 today_sales=float(today_sales),today_payments=float(today_payments),
                 month_sales=float(month_sales),month_payments=float(month_payments))
    return render_template("dashboard.html", stats=stats, recent=recent,
                           recent_purchases=recent_purchases, recent_payments=recent_payments,
                           recent_invoices=recent_invoices, outstanding_customers=outstanding_customers,
                           monthly_sales=monthly_sales, monthly_purchases=monthly_purchases)


@app.route("/purchases", methods=["GET","POST"])
@login_required
def purchases():
    if request.method == "POST":
        try:
            purchase_date = valid_date(request.form.get("purchase_date"), "purchase date")
            local_agent = request.form.get("local_agent", "").strip()
            if not local_agent:
                raise ValueError("Supplier or local agent is required.")
            qty = get_float("quantity_kg")
            price = nonnegative_float("price_per_kg")
            transport = nonnegative_float("transport_cost")
            other = nonnegative_float("other_expenses")
            received = get_float("quantity_received_kg")
            if qty <= 0 or received < 0 or received > qty:
                raise ValueError("Quantity received must be between 0 and quantity purchased.")
            total_purchase = qty * price
            total_cost = total_purchase + transport + other
            pid = next_daily_id("ADU-PUR", "purchases", "purchase_id")
            ts = now()
            by = session["username"]
            conn = db()
            try:
                conn.execute("""INSERT INTO purchases
                    (purchase_id,purchase_date,local_agent,agent_phone,location,quantity_kg,price_per_kg,
                     total_purchase_cost,transport_cost,other_expenses,total_cost,quantity_received_kg,staff_user,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (pid,purchase_date,local_agent,
                     request.form.get("agent_phone","").strip(),request.form.get("location","").strip(),
                     qty,price,total_purchase,transport,other,total_cost,received,by,ts))
                stock_svc.apply_purchase_create(conn, pid, received, purchase_date, by, ts)
                conn.execute("INSERT INTO audit_log(username,action,reference,details,created_at) VALUES(?,?,?,?,?)",
                             (actor_label(), "PURCHASE CREATED", pid, f"{received:g} KG received; total_cost={total_cost:.2f}", ts))
                conn.commit()
            except (sqlite3.Error, ValueError):
                conn.rollback()
                raise
            finally:
                conn.close()
            flash(f"Purchase {pid} recorded.", "success")
            return redirect(url_for("purchases"))
        except (ValueError, sqlite3.IntegrityError) as error:
            flash(str(error) if isinstance(error, ValueError) else "The purchase could not be saved.", "danger")
    blocked_purchase = request.args.get("blocked_purchase", type=int)
    conn = db()
    rows = conn.execute(f"SELECT p.* FROM purchases p WHERE {visible_sql('p')} ORDER BY p.id DESC LIMIT 100").fetchall()
    blocked_purchase_row = None
    if blocked_purchase:
        blocked_purchase_row = conn.execute(
            "SELECT id,purchase_id,purchase_date,local_agent,quantity_received_kg,total_cost FROM purchases WHERE id=?",
            (blocked_purchase,)
        ).fetchone()
    conn.close()
    return render_template("purchases.html", rows=rows, today=date.today().isoformat(),
                           blocked_purchase=blocked_purchase_row)


@app.route("/sales", methods=["GET","POST"])
@login_required
def sales():
    if request.method == "POST":
        try:
            sale_date = valid_date(request.form.get("sale_date"), "sale date")
            qty = get_float("quantity_kg")
            price = nonnegative_float("selling_price_kg")
            if qty <= 0 or price < 0:
                raise ValueError("Enter valid quantity and selling price.")
            name = request.form["customer_name"].strip()
            phone = request.form.get("customer_phone","").strip()
            if not name:
                raise ValueError("Customer name is required.")
            total = qty * price
            ts = now()
            by = session["username"]
            conn = db()
            try:
                conn.execute("BEGIN IMMEDIATE")
                tid = next_daily_id("ADU", "sales", "transaction_id", conn, datetime.strptime(sale_date, "%Y-%m-%d").date())
                sales_id = next_daily_id("ADU-SAL", "sales", "sales_id", conn, datetime.strptime(sale_date, "%Y-%m-%d").date())
                invoice_id = next_daily_id("ADU-INV", "invoices", "invoice_number", conn,
                                           datetime.strptime(sale_date, "%Y-%m-%d").date())
                stock_svc.assert_stock_available(conn, qty)
                customer = conn.execute("SELECT id FROM customers WHERE lower(name)=lower(?) AND phone=?", (name,phone)).fetchone()
                if customer:
                    cid = customer["id"]
                else:
                    cur = conn.execute("INSERT INTO customers(name,phone,created_at) VALUES(?,?,?)", (name,phone,ts))
                    cid = cur.lastrowid
                conn.execute("""INSERT INTO sales(transaction_id,sales_id,invoice_number,sale_date,customer_id,quantity_kg,selling_price_kg,total_sale,staff_user,created_at)
                                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                             (tid,sales_id,invoice_id,sale_date,cid,qty,price,total,by,ts))
                conn.execute("""INSERT INTO invoices(invoice_number,transaction_id,sales_id,invoice_date,generated_by,generated_at)
                                VALUES(?,?,?,?,?,?)""",
                             (invoice_id, tid, sales_id, sale_date, by, ts))
                stock_svc.apply_sale_create(conn, tid, qty, sale_date, by, ts)
                conn.execute("INSERT INTO audit_log(username,action,reference,details,created_at) VALUES(?,?,?,?,?)",
                             (actor_label(), "SALE CREATED", sales_id, f"{qty:g} KG sold to {name}; sales_id={sales_id}; total={total:.2f}", ts))
                conn.commit()
            except (sqlite3.Error, ValueError):
                conn.rollback()
                raise
            finally:
                conn.close()
            flash(f"SALE CREATED SUCCESSFULLY. Sales ID: {sales_id}. Invoice No.: {invoice_id}", "success")
            return redirect(url_for("sales"))
        except (ValueError, sqlite3.IntegrityError) as error:
            flash(str(error) if isinstance(error, ValueError) else "The sale could not be saved.", "danger")
    related_purchase = request.args.get("related_purchase", type=int)
    selected_customer = request.args.get("customer_id", type=int)
    conn = db()
    selected_customer_row = conn.execute("SELECT id,name,phone FROM customers WHERE id=? AND active=1", (selected_customer,)).fetchone() if selected_customer else None
    related_purchase_row = None
    sales_filter = ""
    sales_params = []
    if related_purchase:
        related_purchase_row = conn.execute(
            "SELECT purchase_id,purchase_date,local_agent FROM purchases WHERE id=?",
            (related_purchase,)
        ).fetchone()
        if related_purchase_row:
            sales_filter = " AND s.sale_date >= ?"
            sales_params.append(related_purchase_row["purchase_date"])
    rows = conn.execute(f"""SELECT s.*,c.name customer_name,c.phone,
        COALESCE(s.invoice_number, i.invoice_number) invoice_number,
        COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.transaction_id=s.transaction_id AND p.deleted=0),0) paid
        FROM sales s JOIN customers c ON c.id=s.customer_id LEFT JOIN invoices i ON i.transaction_id=s.transaction_id
        WHERE {visible_sql('s')} {sales_filter}
        ORDER BY s.sale_date ASC, s.id ASC LIMIT 100""", sales_params).fetchall()
    conn.close()
    _, _, stock = stock_summary()
    return render_template("sales.html", rows=rows, stock=stock, today=date.today().isoformat(),
                           related_purchase=related_purchase_row, selected_customer=selected_customer_row)


@app.route("/payments", methods=["GET","POST"])
@login_required
def payments():
    if request.method == "POST":
        try:
            sale_ref = (request.form.get("sales_id") or request.form.get("transaction_id") or "").strip()
            customer_id = request.form.get("customer_id", type=int)
            if not sale_ref:
                raise ValueError("Select an outstanding sale.")
            if not customer_id:
                raise ValueError("Select a customer first.")
            info = sale_info(sale_ref)
            if not info:
                raise ValueError("Sales ID was not found.")
            if int(info["customer_id"]) != customer_id:
                raise ValueError("The selected sale does not belong to that customer.")
            tid = info["transaction_id"]
            pay_date = valid_date(request.form.get("payment_date"), "payment date")
            amount = get_float("amount")
            if amount <= 0:
                raise ValueError("Payment amount must be greater than zero.")
            if amount > info["balance"] + 0.005:
                raise ValueError(f"Payment exceeds outstanding balance of {money(info['balance'])}.")
            ts = now()
            by = session["username"]
            conn = db()
            try:
                payment_reference = request.form.get("payment_reference", "").strip()
                if payment_reference and conn.execute(
                    "SELECT 1 FROM payments WHERE transaction_id=? AND payment_reference=? AND deleted=0",
                    (tid, payment_reference)
                ).fetchone():
                    raise ValueError("A payment with this reference already exists for the sale.")
                payment_id = next_daily_id("ADU-PAY", "payments", "payment_id", conn,
                                           datetime.strptime(pay_date, "%Y-%m-%d").date())
                conn.execute(
                    """INSERT INTO payments(payment_id,transaction_id,sales_id,payment_date,amount,
                       payment_method,payment_reference,notes,staff_user,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (payment_id, tid, info["sales_id"], pay_date, amount, request.form["payment_method"],
                     payment_reference, request.form.get("notes", "").strip(), by, ts))
                conn.execute("INSERT INTO audit_log(username,action,reference,details,created_at) VALUES(?,?,?,?,?)",
                             (actor_label(), "PAYMENT CREATED", payment_id, f"sales_id={info['sales_id']}; amount={amount:.2f}; method={request.form['payment_method']}", ts))
                conn.commit()
            except (sqlite3.Error, ValueError):
                conn.rollback()
                raise
            finally:
                conn.close()
            flash(f"Payment recorded against {info['sales_id']}.", "success")
            return redirect(url_for("payments"))
        except (ValueError, sqlite3.IntegrityError) as error:
            flash(str(error) if isinstance(error, ValueError) else "The payment could not be saved.", "danger")
    conn = db()
    rows = conn.execute(f"""SELECT p.*, COALESCE(p.sales_id, s.sales_id) AS sales_id, c.name customer_name, c.phone, s.total_sale invoice_amount
        FROM payments p JOIN sales s ON s.transaction_id=p.transaction_id
        JOIN customers c ON c.id=s.customer_id WHERE {visible_sql('p')} AND {visible_sql('s')} ORDER BY p.id DESC LIMIT 100""").fetchall()
    customers = conn.execute("SELECT id,name,phone FROM customers WHERE active=1 ORDER BY name").fetchall()
    outstanding_sales = conn.execute("""SELECT s.sales_id,s.customer_id,s.sale_date,s.total_sale,c.name customer_name,
        COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.transaction_id=s.transaction_id AND p.deleted=0),0) paid
        FROM sales s JOIN customers c ON c.id=s.customer_id
        WHERE s.deleted=0 AND s.total_sale - COALESCE((SELECT SUM(p.amount) FROM payments p
            WHERE p.transaction_id=s.transaction_id AND p.deleted=0),0) > 0.005
        ORDER BY c.name,s.sale_date,s.id""").fetchall()
    conn.close()
    return render_template("payments.html", rows=rows, customers=customers,
                           outstanding_sales=outstanding_sales, today=date.today().isoformat())


@app.route("/purchases/<int:purchase_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def edit_purchase(purchase_id):
    conn = db()
    row = conn.execute(f"SELECT p.* FROM purchases p WHERE p.id=? AND {visible_sql('p')}", (purchase_id,)).fetchone()
    conn.close()
    if not row:
        abort(404)
    if request.method == "POST":
        try:
            purchase_date = valid_date(request.form.get("purchase_date"), "purchase date")
            qty = get_float("quantity_kg")
            price = nonnegative_float("price_per_kg")
            transport = nonnegative_float("transport_cost")
            other = nonnegative_float("other_expenses")
            received = get_float("quantity_received_kg")
            if qty <= 0 or received < 0 or received > qty:
                raise ValueError("Quantity received must be between 0 and quantity purchased.")
            ts = now()
            by = session["username"]
            conn = db()
            try:
                current = conn.execute("SELECT * FROM purchases WHERE id=?", (purchase_id,)).fetchone()
                if not current:
                    raise ValueError("This transaction is unavailable.")
                old_received = float(current["quantity_received_kg"])
                stock_svc.apply_purchase_edit(conn, current["purchase_id"], old_received, received,
                                              purchase_date, by, ts)
                conn.execute("""UPDATE purchases SET purchase_date=?,local_agent=?,agent_phone=?,location=?,
                    quantity_kg=?,price_per_kg=?,total_purchase_cost=?,transport_cost=?,other_expenses=?,
                    total_cost=?,quantity_received_kg=? WHERE id=?""",
                    (purchase_date, request.form["local_agent"].strip(), request.form.get("agent_phone", "").strip(),
                     request.form.get("location", "").strip(), qty, price, qty * price, transport, other,
                     qty * price + transport + other, received, purchase_id))
                conn.execute("INSERT INTO audit_log(username,action,reference,details,created_at) VALUES(?,?,?,?,?)",
                             (actor_label(), "PURCHASE UPDATED", current["purchase_id"],
                              f"received {old_received:g}->{received:g} KG; total_cost={qty*price+transport+other:.2f}", ts))
                conn.commit()
            except (sqlite3.Error, ValueError):
                conn.rollback()
                raise
            finally:
                conn.close()
            flash("Purchase updated.", "success")
            return redirect(url_for("purchases"))
        except (ValueError, sqlite3.IntegrityError) as error:
            flash(str(error) if isinstance(error, ValueError) else "The purchase could not be updated.", "danger")
    return render_template("edit_record.html", kind="purchase", record=row)


@app.route("/sales/<int:sale_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def edit_sale(sale_id):
    conn = db()
    row = conn.execute(f"""SELECT s.*,c.name customer_name,c.phone customer_phone,
        COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.transaction_id=s.transaction_id AND p.deleted=0),0) paid
        FROM sales s JOIN customers c ON c.id=s.customer_id WHERE s.id=? AND {visible_sql('s')}""", (sale_id,)).fetchone()
    conn.close()
    if not row:
        abort(404)
    if request.method == "POST":
        try:
            sale_date = valid_date(request.form.get("sale_date"), "sale date")
            qty = get_float("quantity_kg")
            price = nonnegative_float("selling_price_kg")
            if qty <= 0 or price < 0:
                raise ValueError("Enter valid quantity and selling price.")
            total = qty * price
            if total + 0.005 < float(row["paid"]):
                raise ValueError("Sale total cannot be lower than payments already received.")
            name = request.form["customer_name"].strip()
            phone = request.form.get("customer_phone", "").strip()
            if not name:
                raise ValueError("Customer name is required.")
            ts = now()
            by = session["username"]
            conn = db()
            try:
                current = conn.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone()
                if not current:
                    raise ValueError("This transaction is unavailable.")
                old_qty = float(current["quantity_kg"])
                stock_svc.apply_sale_edit(conn, current["transaction_id"], old_qty, qty, sale_date, by, ts)
                customer = conn.execute("SELECT id FROM customers WHERE lower(name)=lower(?) AND phone=?", (name, phone)).fetchone()
                cid = customer["id"] if customer else conn.execute(
                    "INSERT INTO customers(name,phone,created_at) VALUES(?,?,?)", (name, phone, ts)
                ).lastrowid
                conn.execute("UPDATE sales SET sale_date=?,customer_id=?,quantity_kg=?,selling_price_kg=?,total_sale=? WHERE id=?",
                             (sale_date, cid, qty, price, total, sale_id))
                conn.execute("INSERT INTO audit_log(username,action,reference,details,created_at) VALUES(?,?,?,?,?)",
                             (actor_label(), "SALE UPDATED", current["transaction_id"],
                              f"qty {old_qty:g}->{qty:g} KG; total={total:.2f}", ts))
                conn.commit()
            except (sqlite3.Error, ValueError):
                conn.rollback()
                raise
            finally:
                conn.close()
            flash("Sale updated.", "success")
            return redirect(url_for("sales"))
        except (ValueError, sqlite3.IntegrityError) as error:
            flash(str(error) if isinstance(error, ValueError) else "The sale could not be updated.", "danger")
    return render_template("edit_record.html", kind="sale", record=row)


@app.route("/payments/<int:payment_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def edit_payment(payment_id):
    conn = db()
    row = conn.execute(f"""SELECT p.*,s.total_sale,s.deleted sale_deleted
        FROM payments p JOIN sales s ON s.transaction_id=p.transaction_id
        WHERE p.id=? AND {visible_sql('p')} AND {visible_sql('s')}""", (payment_id,)).fetchone()
    conn.close()
    if not row:
        abort(404)
    if request.method == "POST":
        try:
            payment_date = valid_date(request.form.get("payment_date"), "payment date")
            amount = get_float("amount")
            if amount <= 0:
                raise ValueError("Payment amount must be greater than zero.")
            conn = db()
            try:
                other_paid = conn.execute("SELECT COALESCE(SUM(amount),0) v FROM payments WHERE transaction_id=? AND id!=? AND deleted=0",
                                          (row["transaction_id"], payment_id)).fetchone()["v"]
                if amount + float(other_paid) > float(row["total_sale"]) + 0.005:
                    raise ValueError("Payment exceeds the sale balance.")
                conn.execute("UPDATE payments SET payment_date=?,amount=?,payment_method=?,payment_reference=?,notes=? WHERE id=?",
                             (payment_date, amount, request.form["payment_method"],
                              request.form.get("payment_reference", "").strip(),
                              request.form.get("notes", "").strip(), payment_id))
                conn.execute("INSERT INTO audit_log(username,action,reference,details,created_at) VALUES(?,?,?,?,?)",
                             (actor_label(), "PAYMENT UPDATED", row["transaction_id"], f"amount={amount:.2f}", now()))
                conn.commit()
            except (sqlite3.Error, ValueError):
                conn.rollback()
                raise
            finally:
                conn.close()
            flash("Payment updated.", "success")
            return redirect(url_for("payments"))
        except (ValueError, sqlite3.IntegrityError) as error:
            flash(str(error) if isinstance(error, ValueError) else "The payment could not be updated.", "danger")
    return render_template("edit_record.html", kind="payment", record=row)


@app.route("/customers", methods=["GET", "POST"])
@login_required
def customers():
    if request.method == "POST":
        try:
            name = request.form.get("name", "").strip()
            opening_balance = nonnegative_float("opening_balance")
            if not name:
                raise ValueError("Customer name is required.")
            conn = db()
            customer_cursor = conn.execute("""INSERT INTO customers
                (name,phone,address,location,customer_type,opening_balance,notes,created_at)
                VALUES(?,?,?,?,?,?,?,?)""", (
                    name, request.form.get("phone", "").strip(),
                    request.form.get("address", "").strip(), request.form.get("location", "").strip(),
                    request.form.get("customer_type", "RETAIL"), opening_balance,
                    request.form.get("notes", "").strip(), now()))
            customer_id = customer_cursor.lastrowid
            conn.commit()
            conn.close()
            flash("Customer created. Enter the sale details to generate the Sales ID and invoice number.", "success")
            return redirect(url_for("sales", customer_id=customer_id))
        except (ValueError, sqlite3.Error) as error:
            if "conn" in locals():
                conn.rollback()
                conn.close()
            flash(str(error) if isinstance(error, ValueError) else "The customer could not be saved.", "danger")
    blocked_customer = request.args.get("blocked_customer", type=int)
    conn = db()
    rows = conn.execute("""SELECT c.*, COALESCE(SUM(s.total_sale), 0) total_sales,
        COALESCE((SELECT SUM(p.amount) FROM payments p JOIN sales ps ON ps.transaction_id=p.transaction_id
              WHERE ps.customer_id=c.id AND ps.deleted=0 AND p.deleted=0), 0) total_paid
        FROM customers c LEFT JOIN sales s ON s.customer_id=c.id AND s.deleted=0
        WHERE c.active=1 GROUP BY c.id ORDER BY c.name""").fetchall()
    blocked_customer_row = None
    if blocked_customer:
        blocked_customer_row = conn.execute("SELECT id,name FROM customers WHERE id=?", (blocked_customer,)).fetchone()
    conn.close()
    return render_template("customers.html", rows=rows, blocked_customer=blocked_customer_row)


@app.route("/customers/<int:customer_id>")
@login_required
def customer_statement(customer_id):
    conn = db()
    customer = conn.execute("SELECT * FROM customers WHERE id=? AND active=1", (customer_id,)).fetchone()
    if not customer:
        conn.close()
        abort(404)
    sales_rows = conn.execute("""SELECT s.*, i.invoice_number, COALESCE((SELECT SUM(p.amount) FROM payments p
        WHERE p.transaction_id=s.transaction_id AND p.deleted=0), 0) paid
        FROM sales s LEFT JOIN invoices i ON i.transaction_id=s.transaction_id
        WHERE s.customer_id=? AND s.deleted=0 ORDER BY s.sale_date DESC, s.id DESC""", (customer_id,)).fetchall()
    conn.close()
    total_sales = sum(float(row["total_sale"]) for row in sales_rows) + float(customer["opening_balance"] or 0)
    total_paid = sum(float(row["paid"]) for row in sales_rows)
    return render_template("customer_statement.html", customer=customer, sales_rows=sales_rows,
                           total_sales=total_sales, total_paid=total_paid,
                           balance=max(total_sales - total_paid, 0))


@app.route("/customers/<int:customer_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def edit_customer(customer_id):
    conn = db()
    customer = conn.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
    if not customer:
        conn.close()
        abort(404)
    if request.method == "POST":
        try:
            name = request.form.get("name", "").strip()
            opening_balance = nonnegative_float("opening_balance")
            if not name:
                raise ValueError("Customer name is required.")
            conn.execute("""UPDATE customers SET name=?,phone=?,address=?,location=?,customer_type=?,
                opening_balance=?,notes=? WHERE id=?""", (
                name, request.form.get("phone", "").strip(), request.form.get("address", "").strip(),
                request.form.get("location", "").strip(), request.form.get("customer_type", "RETAIL"),
                opening_balance, request.form.get("notes", "").strip(), customer_id))
            conn.execute("INSERT INTO audit_log(username,action,reference,details,created_at) VALUES(?,?,?,?,?)",
                         (actor_label(), "CUSTOMER EDITED", name,
                          f"customer_id={customer_id}; old_name={customer['name']}; old_phone={customer['phone'] or ''}; new_phone={request.form.get('phone', '').strip()}", now()))
            conn.commit()
            conn.close()
            flash("Customer updated.", "success")
            return redirect(url_for("customers"))
        except (ValueError, sqlite3.Error) as error:
            conn.rollback()
            conn.close()
            flash(str(error) if isinstance(error, ValueError) else "The customer could not be updated.", "danger")
    conn.close()
    return render_template("customer_edit.html", customer=customer)


@app.route("/customers/<int:customer_id>/delete", methods=["POST"])
@login_required
@admin_required
def delete_customer(customer_id):
    conn = db()
    try:
        customer = conn.execute("SELECT * FROM customers WHERE id=? AND active=1", (customer_id,)).fetchone()
        if not customer:
            abort(404)
        reason = request.form.get("reason", "").strip()
        if not reason:
            raise ValueError("A reason for deletion is required.")
        ts = now()
        conn.execute("BEGIN IMMEDIATE")
        sales = conn.execute("SELECT transaction_id FROM sales WHERE customer_id=? AND deleted=0", (customer_id,)).fetchall()
        transaction_ids = [row["transaction_id"] for row in sales]
        conn.execute("UPDATE customers SET active=0,deleted_at=?,deleted_by=?,deletion_reason=? WHERE id=?",
                     (ts, session["username"], reason, customer_id))
        conn.execute("UPDATE sales SET deleted=1,deleted_at=?,deleted_by=?,deletion_reason=? WHERE customer_id=? AND deleted=0",
                     (ts, session["username"], reason, customer_id))
        conn.execute("""UPDATE payments SET deleted=1,deleted_at=?,deleted_by=?,deletion_reason=?
                        WHERE transaction_id IN (SELECT transaction_id FROM sales WHERE customer_id=?) AND deleted=0""",
                     (ts, session["username"], reason, customer_id))
        conn.execute("""UPDATE invoices SET deleted=1,deleted_at=?,deleted_by=?,deletion_reason=?
                        WHERE transaction_id IN (SELECT transaction_id FROM sales WHERE customer_id=?) AND deleted=0""",
                     (ts, session["username"], reason, customer_id))
        conn.execute("INSERT INTO audit_log(username,action,reference,details,created_at) VALUES(?,?,?,?,?)",
                     (actor_label(), "CUSTOMER DELETED", customer["name"],
                      f"type=CUSTOMER; customer_id={customer_id}; sales={len(transaction_ids)}; reason={reason}", ts))
        conn.commit()
    except ValueError as error:
        conn.rollback()
        flash(str(error), "danger")
        return redirect(request.referrer or url_for("customers"))
    except sqlite3.Error:
        conn.rollback()
        app.logger.exception("Customer deletion failed: customer_id=%s", customer_id)
        flash("Customer could not be deleted. No changes were made.", "danger")
        return redirect(url_for("customers"))
    finally:
        conn.close()
    flash("Customer deleted successfully.", "success")
    return redirect(url_for("customers"))


@app.route("/admin/deleted-records")
@login_required
@admin_required
def deleted_records():
    conn = db()
    customers = conn.execute("SELECT * FROM customers WHERE active=0 ORDER BY deleted_at DESC").fetchall()
    sales = conn.execute("""SELECT s.*,c.name customer_name,COALESCE(s.invoice_number,i.invoice_number) invoice_number
        FROM sales s JOIN customers c ON c.id=s.customer_id LEFT JOIN invoices i ON i.transaction_id=s.transaction_id
        WHERE s.deleted=1 ORDER BY s.deleted_at DESC LIMIT 200""").fetchall()
    payments = conn.execute("""SELECT p.*,s.sales_id,c.name customer_name
        FROM payments p JOIN sales s ON s.transaction_id=p.transaction_id JOIN customers c ON c.id=s.customer_id
        WHERE p.deleted=1 ORDER BY p.deleted_at DESC LIMIT 200""").fetchall()
    invoices = conn.execute("""SELECT i.*,s.sales_id,c.name customer_name
        FROM invoices i JOIN sales s ON s.transaction_id=i.transaction_id JOIN customers c ON c.id=s.customer_id
        WHERE i.deleted=1 ORDER BY i.deleted_at DESC LIMIT 200""").fetchall()
    conn.close()
    return render_template("deleted_records.html", customers=customers, sales=sales,
                           payments=payments, invoices=invoices)


@app.route("/admin/customers/<int:customer_id>/restore", methods=["POST"])
@login_required
@admin_required
def restore_customer(customer_id):
    reason = request.form.get("reason", "").strip()
    if not reason:
        flash("A restoration reason is required.", "danger")
        return redirect(request.referrer or url_for("deleted_records"))
    conn = db()
    try:
        customer = conn.execute("SELECT * FROM customers WHERE id=? AND active=0", (customer_id,)).fetchone()
        if not customer:
            abort(404)
        ts = now()
        deletion_timestamp = customer["deleted_at"]
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE customers SET active=1,deleted_at=NULL,deleted_by=NULL,deletion_reason=NULL WHERE id=?", (customer_id,))
        conn.execute("UPDATE sales SET deleted=0,deleted_at=NULL,deleted_by=NULL,deletion_reason=NULL WHERE customer_id=? AND deleted=1 AND deleted_at=?",
                     (customer_id, deletion_timestamp))
        conn.execute("""UPDATE payments SET deleted=0,deleted_at=NULL,deleted_by=NULL,deletion_reason=NULL
                       WHERE deleted=1 AND deleted_at=? AND transaction_id IN (SELECT transaction_id FROM sales WHERE customer_id=?)""",
                     (deletion_timestamp, customer_id))
        conn.execute("""UPDATE invoices SET deleted=0,deleted_at=NULL,deleted_by=NULL,deletion_reason=NULL
                       WHERE deleted=1 AND deleted_at=? AND transaction_id IN (SELECT transaction_id FROM sales WHERE customer_id=?)""",
                     (deletion_timestamp, customer_id))
        conn.execute("INSERT INTO audit_log(username,action,reference,details,created_at) VALUES(?,?,?,?,?)",
                     (actor_label(), "CUSTOMER RESTORED", customer["name"],
                      f"customer_id={customer_id}; reason={reason}", ts))
        conn.commit()
        flash("Customer and related records restored.", "success")
    except sqlite3.Error:
        conn.rollback()
        app.logger.exception("Customer restore failed: customer_id=%s", customer_id)
        flash("Customer could not be restored. No changes were made.", "danger")
    finally:
        conn.close()
    return redirect(request.referrer or url_for("deleted_records"))


@app.route("/admin/customers/<int:customer_id>/permanent-delete", methods=["POST"])
@login_required
@admin_required
def permanently_delete_customer(customer_id):
    reason = request.form.get("reason", "").strip()
    if not reason:
        flash("A permanent deletion reason is required.", "danger")
        return redirect(request.referrer or url_for("deleted_records"))
    conn = db()
    try:
        customer = conn.execute("SELECT * FROM customers WHERE id=? AND active=0", (customer_id,)).fetchone()
        if not customer:
            abort(404)
        transaction_ids = [r["transaction_id"] for r in conn.execute("SELECT transaction_id FROM sales WHERE customer_id=?", (customer_id,)).fetchall()]
        ts = now()
        conn.execute("BEGIN IMMEDIATE")
        if transaction_ids:
            marks = ",".join("?" for _ in transaction_ids)
            conn.execute(f"DELETE FROM payments WHERE transaction_id IN ({marks})", transaction_ids)
            conn.execute(f"DELETE FROM invoices WHERE transaction_id IN ({marks})", transaction_ids)
            conn.execute(f"DELETE FROM stock_movements WHERE reference IN ({marks})", transaction_ids)
            conn.execute(f"DELETE FROM sales WHERE transaction_id IN ({marks})", transaction_ids)
        conn.execute("INSERT INTO audit_log(username,action,reference,details,created_at) VALUES(?,?,?,?,?)",
                     (actor_label(), "CUSTOMER PERMANENTLY DELETED", customer["name"],
                      f"customer_id={customer_id}; transactions={len(transaction_ids)}; reason={reason}", ts))
        conn.execute("DELETE FROM customers WHERE id=?", (customer_id,))
        conn.commit()
        flash("Customer and related records permanently deleted.", "success")
    except sqlite3.Error:
        conn.rollback()
        app.logger.exception("Permanent customer deletion failed: customer_id=%s", customer_id)
        flash("Customer could not be permanently deleted. No changes were made.", "danger")
    finally:
        conn.close()
    return redirect(request.referrer or url_for("deleted_records"))


@app.route("/admin/invoices/<int:invoice_id>/delete", methods=["POST"])
@login_required
@admin_required
def delete_invoice(invoice_id):
    reason = request.form.get("reason", "").strip()
    if not reason:
        flash("A deletion reason is required.", "danger")
        return redirect(request.referrer or url_for("invoice"))
    conn = db()
    try:
        row = conn.execute("SELECT * FROM invoices WHERE id=? AND deleted=0", (invoice_id,)).fetchone()
        if not row:
            abort(404)
        ts = now()
        conn.execute("UPDATE invoices SET deleted=1,deleted_at=?,deleted_by=?,deletion_reason=? WHERE id=?",
                     (ts, session["username"], reason, invoice_id))
        conn.execute("INSERT INTO audit_log(username,action,reference,details,created_at) VALUES(?,?,?,?,?)",
                     (actor_label(), "INVOICE DELETED", row["invoice_number"], f"invoice_id={invoice_id}; reason={reason}", ts))
        conn.commit()
        flash("Invoice cancelled and retained in the recycle bin.", "success")
    except sqlite3.Error:
        conn.rollback()
        app.logger.exception("Invoice deletion failed: invoice_id=%s", invoice_id)
        flash("Invoice could not be deleted. No changes were made.", "danger")
    finally:
        conn.close()
    return redirect(request.referrer or url_for("invoice"))


@app.route("/records/<table>/<int:record_id>/reverse", methods=["POST"])
@login_required
@admin_required
def reverse_record_route(table, record_id):
    if table not in {"purchases", "sales", "payments"}:
        abort(404)
    conn = db()
    row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (record_id,)).fetchone()
    conn.close()
    if not row:
        abort(404)
    reference = row["purchase_id"] if table == "purchases" else row["transaction_id"]
    try:
        delete_record(table, record_id, reference)
    except ValueError as error:
        flash(str(error), "danger")
        return redirect(request.referrer or url_for("dashboard"))
    flash("Transaction reversed and retained in the audit history.", "success")
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/records/<table>/<int:record_id>/delete", methods=["POST"])
@login_required
@admin_required
def delete_record_route(table, record_id):
    if table not in {"purchases", "sales", "payments"}:
        abort(404)
    conn = db()
    row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (record_id,)).fetchone()
    conn.close()
    if not row:
        app.logger.warning("Transaction deletion requested for missing record: table=%s record_id=%s", table, record_id)
        abort(404)
    reference = row["purchase_id"] if table == "purchases" else row["transaction_id"]
    try:
        hard_delete_record(table, record_id, reference)
    except PurchaseStockInUseError as error:
        return redirect(url_for("purchases", blocked_purchase=error.purchase_id))
    except ValueError as error:
        flash(str(error), "danger")
        return redirect(request.referrer or url_for("dashboard"))
    except sqlite3.Error:
        app.logger.exception("Transaction deletion failed: table=%s record_id=%s reference=%s", table, record_id, reference)
        flash("Transaction could not be deleted. No changes were made.", "danger")
        return redirect(request.referrer or url_for("dashboard"))
    success_messages = {
        "purchases": "Purchase deleted successfully.",
        "sales": "Sale deleted successfully.",
        "payments": "Payment deleted successfully.",
    }
    flash(success_messages[table], "success")
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/records/<table>/<int:record_id>/restore", methods=["POST"])
@login_required
@admin_required
def restore_record(table, record_id):
    if table not in {"purchases", "sales", "payments"}:
        abort(404)
    conn = db()
    try:
        row = conn.execute(f"SELECT * FROM {table} WHERE id=? AND deleted=1", (record_id,)).fetchone()
        if not row:
            flash("This record is not available for restoration.", "danger")
            return redirect(request.referrer or url_for("dashboard"))
        ts = now()
        by = session["username"]
        reference = row["purchase_id"] if table == "purchases" else row["transaction_id"]
        qty = float(row["quantity_received_kg"]) if table == "purchases" else (-float(row["quantity_kg"]) if False else 0)
        if table == "purchases":
            qty = float(row["quantity_received_kg"])
            movement_date = row["purchase_date"]
        elif table == "sales":
            qty = float(row["quantity_kg"])
            movement_date = row["sale_date"]
        else:
            qty = 0
            movement_date = row["payment_date"]
        stock_svc.apply_restoration(conn, table, reference, qty, movement_date, by, ts)
        conn.execute(f"UPDATE {table} SET deleted=0,deleted_at=NULL,deleted_by=NULL,deletion_reason=NULL WHERE id=?", (record_id,))
        if table == "sales":
            conn.execute("UPDATE payments SET deleted=0,deleted_at=NULL,deleted_by=NULL,deletion_reason=NULL WHERE transaction_id=?", (row["transaction_id"],))
            conn.execute("UPDATE invoices SET deleted=0,deleted_at=NULL,deleted_by=NULL,deletion_reason=NULL WHERE transaction_id=?", (row["transaction_id"],))
        spec_action = SPEC_RESTORE.get(table, "TRANSACTION RESTORED")
        conn.execute("INSERT INTO audit_log(username,action,reference,details,created_at) VALUES(?,?,?,?,?)",
                     (actor_label(), spec_action, reference, f"type={table}; restored_by={by}", ts))
        conn.commit()
    except ValueError as error:
        try:
            conn.rollback()
        except Exception:
            pass
        flash(str(error), "danger")
        return redirect(request.referrer or url_for("dashboard"))
    except sqlite3.Error:
        try:
            conn.rollback()
        except Exception:
            pass
        flash("Record could not be restored.", "danger")
        return redirect(request.referrer or url_for("dashboard"))
    finally:
        conn.close()
    flash("Record restored.", "success")
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/invoice", methods=["GET","POST"])
@app.route("/invoices", methods=["GET","POST"])
@app.route("/invoice/<transaction_id>", methods=["GET"])
@app.route("/invoice/<transaction_id>/print", methods=["GET"])
@login_required
def invoice(transaction_id=None):
    if transaction_id is not None:
        tid = str(transaction_id).strip()
    else:
        tid = request.values.get("transaction_id", "").strip()
    info = sale_info(tid) if tid else None
    if info:
        info["invoice_number"] = ensure_invoice_record(info)
    payments_list = []
    invoice_matches = []
    invoice_history = []
    if info:
        conn = db()
        payments_list = conn.execute("SELECT * FROM payments WHERE transaction_id=? AND deleted=0 ORDER BY payment_date,id", (info["transaction_id"],)).fetchall()
        conn.close()
    customer_query = request.values.get("customer", "").strip()
    phone_query = request.values.get("phone", "").strip()
    invoice_no = request.values.get("invoice_no", "").strip()
    invoice_date = request.values.get("invoice_date", "").strip()
    status_query = request.values.get("status", "").strip().upper()
    if customer_query or phone_query or invoice_no or invoice_date or status_query:
        conn = db()
        conditions = ["s.deleted=0"]
        params = []
        if customer_query:
            conditions.append("(c.name LIKE ? OR c.phone LIKE ?)")
            params.extend([f"%{customer_query}%", f"%{customer_query}%"])
        if phone_query:
            conditions.append("c.phone LIKE ?")
            params.append(f"%{phone_query}%")
        if invoice_no:
            conditions.append("i.invoice_number LIKE ?")
            params.append(f"%{invoice_no.upper()}%")
        if invoice_date:
            conditions.append("s.sale_date=?")
            params.append(invoice_date)
        if status_query:
            statuses = []
            if status_query in {"PAID", "PART PAYMENT", "PARTIALLY PAID", "UNPAID", "OVERPAID"}:
                statuses = [status_query, status_query.replace("PART PAYMENT", "PARTIALLY PAID")]
            if not statuses:
                statuses = [status_query]
            clause = " OR ".join(["(CASE WHEN (s.total_sale - COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.transaction_id=s.transaction_id AND p.deleted=0),0)) <= 0.005 THEN 'PAID' WHEN COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.transaction_id=s.transaction_id AND p.deleted=0),0) > s.total_sale + 0.005 THEN 'OVERPAID' WHEN COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.transaction_id=s.transaction_id AND p.deleted=0),0) > 0 THEN 'PART PAYMENT' ELSE 'UNPAID' END) = ?" for _ in statuses])
            conditions.append(f"({clause})")
            params.extend(statuses)
        invoice_matches = conn.execute(f"""SELECT s.transaction_id,s.sales_id,i.invoice_number,s.sale_date,c.name customer_name,c.phone customer_phone,s.total_sale,
            COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.transaction_id=s.transaction_id AND p.deleted=0),0) total_paid
            FROM sales s JOIN customers c ON c.id=s.customer_id LEFT JOIN invoices i ON i.transaction_id=s.transaction_id
            WHERE {' AND '.join(conditions)} ORDER BY s.sale_date DESC,s.id DESC LIMIT 50""", params).fetchall()
        conn.close()
    conn = db()
    invoice_history = conn.execute("""SELECT s.transaction_id,s.sales_id,s.sale_date,c.name customer_name,c.phone customer_phone,s.total_sale,
        COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.transaction_id=s.transaction_id AND p.deleted=0),0) total_paid,
        CASE WHEN COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.transaction_id=s.transaction_id AND p.deleted=0),0) > s.total_sale + 0.005 THEN 'OVERPAID'
             WHEN s.total_sale - COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.transaction_id=s.transaction_id AND p.deleted=0),0) <= 0.005 THEN 'PAID'
             WHEN COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.transaction_id=s.transaction_id AND p.deleted=0),0) > 0 THEN 'PART PAYMENT'
             ELSE 'UNPAID' END status
        FROM sales s JOIN customers c ON c.id=s.customer_id
        WHERE s.deleted=0 ORDER BY s.sale_date DESC,s.id DESC LIMIT 20""").fetchall()
    conn.close()
    return render_template("invoice.html", info=info, payments=payments_list,
                           invoice_matches=invoice_matches, invoice_history=invoice_history,
                           customer_query=customer_query, phone_query=phone_query,
                           invoice_no=invoice_no, invoice_date=invoice_date,
                           status_query=status_query, print_view=request.path.endswith('/print'))


@app.route("/invoice/<transaction_id>/pdf")
@login_required
def invoice_pdf(transaction_id):
    info = sale_info(transaction_id)
    if not info:
        abort(404)
    info["invoice_number"] = ensure_invoice_record(info)
    conn = db()
    pdf_payments = conn.execute("""SELECT payment_id,payment_date,payment_method,amount
        FROM payments WHERE transaction_id=? AND deleted=0 ORDER BY payment_date,id""",
                               (info["transaction_id"],)).fetchall()
    conn.close()
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.enums import TA_RIGHT, TA_CENTER
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
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
    logo_path = BASE_DIR / "static" / "images" / "branding" / "adufarms-logo.jpg"
    if logo_path.exists():
        logo = Image(str(logo_path), width=42*mm, height=30*mm, kind="proportional")
        story.append(logo)
    story += [Paragraph("<b>ADUFARMS</b>", styles["Title"]),
              Paragraph("MAIZE SUPPLY &amp; DELIVERY SERVICES", styles["Heading3"]), Spacer(1,8)]
    meta = [
        ["Invoice No.", info["invoice_number"], "Date", pretty_date(info["sale_date"])],
        ["Sales ID", info["sales_id"], "Phone", info["customer_phone"] or "-"],
        ["Customer", info["customer_name"], "Status", info["status"]],
        ["Address", info["customer_address"] or info["customer_location"] or "-", "Due", pretty_date(info["sale_date"])],
    ]
    t = Table(meta, colWidths=[28*mm,70*mm,25*mm,55*mm])
    t.setStyle(TableStyle([("GRID",(0,0),(-1,-1),0.4,colors.grey),("BACKGROUND",(0,0),(0,-1),colors.lightgrey)]))
    story += [t, Spacer(1,15)]
    data = [["Description","Quantity (KG)","Unit Price (GHS)","Total (GHS)"],
            ["Maize Supply",f'{info["quantity_kg"]:,.2f}',money(info["selling_price_kg"]),money(info["total_sale"])]]
    t2=Table(data,colWidths=[70*mm,35*mm,35*mm,40*mm])
    t2.setStyle(TableStyle([("GRID",(0,0),(-1,-1),0.5,colors.grey),("BACKGROUND",(0,0),(-1,0),colors.lightgrey),
                            ("ALIGN",(1,1),(-1,-1),"RIGHT")]))
    payment_data = [["Payment ID", "Date", "Method", "Amount (GHS)"]]
    payment_data.extend([[payment["payment_id"], pretty_date(payment["payment_date"]),
                          payment["payment_method"], money(payment["amount"])] for payment in pdf_payments])
    if len(payment_data) == 1:
        payment_data.append(["No payments recorded", "-", "-", money(0)])
    payment_table = Table(payment_data, colWidths=[45*mm,35*mm,50*mm,40*mm])
    payment_table.setStyle(TableStyle([("GRID",(0,0),(-1,-1),0.4,colors.grey),
                                       ("BACKGROUND",(0,0),(-1,0),colors.lightgrey),
                                       ("ALIGN",(3,1),(3,-1),"RIGHT")]))
    story += [t2, Spacer(1,12), Paragraph("<b>PAYMENT HISTORY</b>", styles["Heading3"]), payment_table, Spacer(1,15)]
    totals=[["Subtotal (GHS)",money(info["total_sale"])],
            ["Delivery Fee (GHS)","N/A"],
            ["Grand Total (GHS)",money(info["total_sale"])],
            ["Amount Paid",money(info["total_paid"])],
            ["Outstanding Balance",money(info["balance"])],
            ["Payment Status",info["status"]]]
    t3=Table(totals,colWidths=[120*mm,60*mm],hAlign="RIGHT")
    t3.setStyle(TableStyle([("GRID",(0,0),(-1,-1),0.4,colors.grey),("ALIGN",(1,0),(1,-1),"RIGHT"),
                            ("BACKGROUND",(0,0),(0,-1),colors.lightgrey)]))
    payment_lines = [Paragraph("<b>PAYMENT METHOD</b>: Mobile Money / Bank Transfer", styles["Normal"]),
                     Paragraph("<b>MOBILE MONEY</b>: 054 734 6840 · Agnes Adomah", styles["Normal"]),
                     Paragraph("<b>BANK</b>: 0070910682301 · Kyeremeh Bismark · Republic Bank (Legon Branch)", styles["Normal"]),
                     Spacer(1, 12), Paragraph("Authorized Signature: ____________________________", styles["Normal"])]
    story += [t3, Spacer(1,12)] + payment_lines + [Spacer(1,18), Paragraph("Thank you for doing business with ADUFARMS.", styles["Center"])]
    doc.build(story); buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=f"{info['invoice_number']}.pdf", mimetype="application/pdf")


@app.route("/search")
@login_required
def search():
    q = request.args.get("q","").strip()
    results=[]
    if q:
        conn=db()
        results=conn.execute(f"""SELECT s.transaction_id,s.sales_id,i.invoice_number,s.sale_date,c.name customer_name,c.phone,
            s.quantity_kg,s.selling_price_kg,s.total_sale,
            COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.transaction_id=s.transaction_id AND p.deleted=0),0) paid
            FROM sales s JOIN customers c ON c.id=s.customer_id LEFT JOIN invoices i ON i.transaction_id=s.transaction_id
            WHERE ({visible_sql('s')}) AND (s.sales_id LIKE ? OR s.transaction_id LIKE ? OR i.invoice_number LIKE ?
                OR c.name LIKE ? OR c.phone LIKE ? OR s.sale_date LIKE ?
                OR EXISTS (SELECT 1 FROM payments sp WHERE sp.transaction_id=s.transaction_id AND sp.payment_id LIKE ?))
            ORDER BY s.id DESC""", tuple(f"%{q}%" for _ in range(7))).fetchall()
        purchases=conn.execute(f"""SELECT p.* FROM purchases p WHERE ({visible_sql('p')}) AND (purchase_id LIKE ? OR local_agent LIKE ? OR location LIKE ? OR agent_phone LIKE ? OR purchase_date LIKE ?)
            ORDER BY id DESC""",tuple(f"%{q}%" for _ in range(5))).fetchall()
        payments=conn.execute(f"""SELECT p.*,s.sales_id,c.name customer_name FROM payments p
            JOIN sales s ON s.transaction_id=p.transaction_id JOIN customers c ON c.id=s.customer_id
            WHERE ({visible_sql('p')}) AND ({visible_sql('s')})
            AND (p.payment_id LIKE ? OR CAST(p.id AS TEXT) LIKE ? OR p.transaction_id LIKE ? OR p.payment_reference LIKE ? OR s.sales_id LIKE ? OR p.payment_date LIKE ?)
            ORDER BY p.id DESC""", tuple(f"%{q}%" for _ in range(6))).fetchall()
        conn.close()
    else:
        purchases=[]
        payments=[]
    return render_template("search.html", q=q, results=results, purchases=purchases, payments=payments)


@app.route("/reports")
@login_required
def reports():
    start = request.args.get("start", "").strip()
    end = request.args.get("end", "").strip()
    params = []
    date_filter = ""
    if start:
        date_filter += " AND sale_date >= ?"
        params.append(start)
    if end:
        date_filter += " AND sale_date <= ?"
        params.append(end)
    if start and end and start > end:
        flash("The report start date must be before the end date.", "danger")
        return redirect(url_for("reports"))
    purchase_params = []
    purchase_filter = ""
    payment_params = []
    payment_filter = ""
    if start:
        purchase_filter += " AND purchase_date >= ?"; purchase_params.append(start)
        payment_filter += " AND payment_date >= ?"; payment_params.append(start)
    if end:
        purchase_filter += " AND purchase_date <= ?"; purchase_params.append(end)
        payment_filter += " AND payment_date <= ?"; payment_params.append(end)
    conn = db()
    sales_rows = conn.execute(f"""SELECT s.transaction_id, s.sales_id, s.sale_date, c.name customer_name,
        s.quantity_kg, s.total_sale, COALESCE(SUM(p.amount), 0) paid
        FROM sales s JOIN customers c ON c.id=s.customer_id LEFT JOIN payments p
        ON p.transaction_id=s.transaction_id AND p.deleted=0
        WHERE s.deleted=0 {date_filter.replace('sale_date', 's.sale_date')}
        GROUP BY s.id ORDER BY s.sale_date DESC, s.id DESC""", params).fetchall()
    purchase_rows = conn.execute(f"""SELECT purchase_id, purchase_date, local_agent,
        quantity_received_kg, total_cost FROM purchases WHERE deleted=0 {purchase_filter}
        ORDER BY purchase_date DESC, id DESC""", purchase_params).fetchall()
    payment_rows = conn.execute(f"""SELECT p.payment_id,p.transaction_id, s.sales_id, p.payment_date, p.amount,
        p.payment_method, c.name customer_name FROM payments p
        JOIN sales s ON s.transaction_id=p.transaction_id JOIN customers c ON c.id=s.customer_id
        WHERE p.deleted=0 AND s.deleted=0 {payment_filter.replace('payment_date','p.payment_date')}
        ORDER BY p.payment_date DESC, p.id DESC""", payment_params).fetchall()
    payment_total = conn.execute(f"SELECT COALESCE(SUM(amount),0) v FROM payments WHERE deleted=0 {payment_filter}", payment_params).fetchone()["v"]
    balance_rows = conn.execute(f"""SELECT s.transaction_id, s.sales_id, s.sale_date, c.name customer_name, c.phone,
        s.total_sale, COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.transaction_id=s.transaction_id AND p.deleted=0),0) paid,
        s.total_sale - COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.transaction_id=s.transaction_id AND p.deleted=0),0) balance
        FROM sales s JOIN customers c ON c.id=s.customer_id
        WHERE s.deleted=0 {date_filter.replace('sale_date','s.sale_date')} ORDER BY s.sale_date DESC""", params).fetchall()
    purchased = conn.execute(f"SELECT COALESCE(SUM(quantity_received_kg),0) v FROM purchases WHERE deleted=0 {purchase_filter}", purchase_params).fetchone()["v"]
    sold = conn.execute(f"SELECT COALESCE(SUM(quantity_kg),0) v FROM sales WHERE deleted=0 {date_filter.replace('sale_date','sale_date')}", params).fetchone()["v"]
    stock_kg = float(purchased) - float(sold)
    sales_total = sum(float(r["total_sale"]) for r in sales_rows)
    purchase_cost_total = sum(float(r["total_cost"]) for r in purchase_rows)
    expenses_total = conn.execute(f"SELECT COALESCE(SUM(total_purchase_cost),0)+COALESCE(SUM(transport_cost),0)+COALESCE(SUM(other_expenses),0) v FROM purchases WHERE deleted=0 {purchase_filter}", purchase_params).fetchone()["v"]
    profit_est = float(sales_total) - float(expenses_total)
    counts = dict(sales=len(sales_rows), purchases=len(purchase_rows),
                  payments=len(payment_rows), customers=len(balance_rows))
    conn.close()
    summary = dict(stock_kg=stock_kg, sales_total=sales_total, purchase_cost_total=purchase_cost_total,
                   payment_total=float(payment_total), expenses_total=float(expenses_total),
                   profit_est=profit_est, counts=counts)
    if request.args.get("format") == "csv":
        export_type = request.args.get("type", "sales")
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        if export_type == "purchases":
            writer.writerow(["Purchase ID", "Date", "Supplier", "Quantity KG", "Total Cost"])
            for row in purchase_rows:
                writer.writerow([row["purchase_id"], row["purchase_date"], row["local_agent"],
                                 row["quantity_received_kg"], row["total_cost"]])
            filename = "adufarms-purchases-report.csv"
        elif export_type == "payments":
            writer.writerow(["Payment ID", "Sales ID", "Date", "Customer", "Amount", "Method"])
            for row in payment_rows:
                writer.writerow([row["payment_id"], row["sales_id"], row["payment_date"], row["customer_name"],
                                 row["amount"], row["payment_method"]])
            filename = "adufarms-payments-report.csv"
        elif export_type == "balances":
            writer.writerow(["Sales ID", "Date", "Customer", "Total Sale", "Paid", "Balance"])
            for row in balance_rows:
                writer.writerow([row["sales_id"], row["sale_date"], row["customer_name"],
                                 row["total_sale"], row["paid"], row["balance"]])
            filename = "adufarms-balances-report.csv"
        else:
            writer.writerow(["Sales ID", "Date", "Customer", "Quantity KG", "Total Sale", "Paid", "Balance"])
            for row in sales_rows:
                writer.writerow([row["sales_id"], row["sale_date"], row["customer_name"],
                                 row["quantity_kg"], row["total_sale"], row["paid"],
                                 float(row["total_sale"]) - float(row["paid"])])
            filename = "adufarms-sales-report.csv"
        return send_file(io.BytesIO(buf.getvalue().encode("utf-8-sig")), as_attachment=True,
                         download_name=filename, mimetype="text/csv")
    return render_template("reports.html", sales_rows=sales_rows, purchase_rows=purchase_rows,
                           payment_rows=payment_rows, balance_rows=balance_rows,
                           payment_total=payment_total, summary=summary, start=start, end=end)


@app.route("/search/transaction/<transaction_id>")
@login_required
def transaction_history(transaction_id):
    info=sale_info(transaction_id)
    if not info: abort(404)
    conn=db()
    pay=conn.execute("SELECT * FROM payments WHERE transaction_id=? AND deleted=0 ORDER BY payment_date,id",(info["transaction_id"],)).fetchall()
    conn.close()
    return render_template("history.html", info=info, payments=pay)


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    conn = db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "password":
            current = request.form.get("current_password", "")
            new_password = request.form.get("new_password", "")
            if not check_password_hash(user["password_hash"], current) or len(new_password) < 8:
                flash("Current password is incorrect or the new password is too short.", "danger")
            else:
                conn.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(new_password), user["id"]))
                conn.commit()
                log_action("PASSWORD CHANGED", user["username"], f"user_id={user['id']}")
                flash("Password changed successfully.", "success")
        elif action == "image":
            image = request.files.get("profile_image")
            if image and image.filename:
                extension = Path(image.filename).suffix.lower()
                if extension not in ALLOWED_PROFILE_EXTS:
                    flash("Profile image must be JPG, PNG, or WEBP.", "danger")
                    conn.close()
                    return render_template("profile.html", user=user)
                filename = secure_filename(f"user-{user['id']}-{image.filename}")
                image.save(PROFILE_DIR / filename)
                conn.execute("UPDATE users SET profile_image=? WHERE id=?", (filename, user["id"]))
                conn.commit()
                session["profile_image"] = filename
                log_action("PROFILE UPDATED", user["username"], f"user_id={user['id']}; profile_image updated")
                flash("Profile image updated.", "success")
        user = conn.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    conn.close()
    return render_template("profile.html", user=user)


@app.route("/users", methods=["GET","POST"])
@app.route("/admin/users", methods=["GET","POST"])
@login_required
@admin_required
def users():
    if request.method=="POST":
        username=request.form.get("username", "").strip()
        full_name=request.form.get("full_name", "").strip()
        password=request.form.get("password", "")
        role=request.form.get("role","STAFF")
        conn = None
        try:
            if not username or not full_name or len(password) < 8 or role not in {"ADMIN", "STAFF"}:
                raise ValueError("Username, full name, valid role and an 8-character password are required.")
            conn=db()
            conn.execute("INSERT INTO users(username,full_name,password_hash,role,created_at) VALUES(?,?,?,?,?)",
                         (username,full_name,generate_password_hash(password),role,now()))
            conn.commit()
            log_action("USER CREATED", username, f"role={role}; created_by={actor_label()}")
            log_action("ADMIN ACTION", username, f"USER CREATED role={role}")
            flash("User created.", "success")
        except ValueError as error:
            flash(str(error), "danger")
        except sqlite3.IntegrityError:
            flash("Username already exists.", "danger")
        except sqlite3.OperationalError:
            flash("The user could not be saved because the database is busy. Please try again.", "danger")
        finally:
            if conn is not None:
                conn.close()
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
        conn.close()
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
                    if len(password) < 8:
                        raise ValueError("Password must be at least 8 characters.")
                    conn.execute("UPDATE users SET username=?,full_name=?,password_hash=?,role=? WHERE id=?",
                                 (username, full_name, generate_password_hash(password), role, user_id))
                    conn.commit()
                    log_action("PASSWORD CHANGED", username, f"user_id={user_id}; changed_by={actor_label()}")
                else:
                    conn.execute("UPDATE users SET username=?,full_name=?,role=? WHERE id=?",
                                 (username, full_name, role, user_id))
                    conn.commit()
                log_action("USER UPDATED", username, f"user_id={user_id}; role={role}")
                log_action("ADMIN ACTION", username, f"USER UPDATED user_id={user_id}")
                flash("User updated.", "success")
                conn.close()
                return redirect(url_for("users"))
            except ValueError as error:
                flash(str(error), "danger")
            except sqlite3.IntegrityError:
                flash("Username already exists.", "danger")
    conn.close()
    return render_template("edit_record.html", kind="user", record=row)


@app.route("/users/<int:user_id>/toggle", methods=["POST"])
@login_required
@admin_required
def toggle_user(user_id):
    conn=db()
    target = conn.execute("SELECT username, active FROM users WHERE id=?", (user_id,)).fetchone()
    if target and target["username"] == "admin":
        conn.close()
        flash("The primary admin account cannot be disabled.", "danger")
        return redirect(url_for("users"))
    conn.execute("UPDATE users SET active=CASE active WHEN 1 THEN 0 ELSE 1 END WHERE id=? AND username!='admin'",(user_id,))
    conn.commit()
    updated = conn.execute("SELECT username, active FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    if updated:
        state = "USER ENABLED" if updated["active"] else "USER DISABLED"
        log_action(state, updated["username"], f"user_id={user_id}; by={actor_label()}")
        log_action("ADMIN ACTION", updated["username"], f"{state} user_id={user_id}")
    return redirect(url_for("users"))


@app.route("/admin/audit-logs")
@login_required
@admin_required
def audit_logs():
    conn = db()
    rows = conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 500").fetchall()
    reversals = conn.execute("SELECT * FROM reversals ORDER BY id DESC LIMIT 100").fetchall()
    conn.close()
    return render_template("audit_logs.html", rows=rows, reversals=reversals)


@app.route("/users/<int:user_id>/delete", methods=["POST"])
@login_required
@admin_required
def delete_user(user_id):
    conn = db()
    target = conn.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
    if target and target["username"] == "admin":
        conn.close()
        flash("The primary admin account cannot be deleted.", "danger")
        return redirect(url_for("users"))
    conn.execute("DELETE FROM users WHERE id=? AND username!='admin'", (user_id,))
    conn.commit()
    conn.close()
    if target:
        log_action("ADMIN ACTION", target["username"], f"USER DELETED user_id={user_id} by={actor_label()}")
    flash("User permanently deleted.", "success")
    return redirect(url_for("users"))


@app.route("/admin/backup", methods=["GET", "POST"])
@login_required
@admin_required
def admin_backup():
    if request.method == "POST":
        try:
            dst = backup_svc.create_backup(app.config["DATABASE"])
            log_action("DATABASE BACKUP", dst.name, f"created_by={actor_label()}; size={dst.stat().st_size}")
            log_action("ADMIN ACTION", dst.name, "DATABASE BACKUP")
            flash(f"Backup {dst.name} created.", "success")
        except Exception:
            app.logger.exception("Backup failed")
            flash("Backup could not be created.", "danger")
        return redirect(url_for("admin_backup"))
    backups = backup_svc.list_backups()
    items = [{"name": p.name, "size": p.stat().st_size,
              "mtime": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")} for p in backups]
    return render_template("backup.html", backups=items)


@app.route("/admin/restore", methods=["POST"])
@login_required
@admin_required
def admin_restore():
    name = request.form.get("backup_name", "").strip()
    if not name:
        flash("Select a backup to restore.", "danger")
        return redirect(url_for("admin_backup"))
    try:
        pre = backup_svc.safe_restore(app.config["DATABASE"], name)
        log_action("DATABASE RESTORE", name, f"restored_by={actor_label()}; pre_restore_backup={pre.name}")
        log_action("ADMIN ACTION", name, "DATABASE RESTORE")
        flash(f"Database restored from {name}. Safety backup {pre.name} retained.", "success")
    except ValueError as error:
        flash(str(error), "danger")
    except Exception:
        app.logger.exception("Restore failed")
        flash("Restore failed. No changes were applied beyond the safety backup.", "danger")
    return redirect(url_for("admin_backup"))


@app.route("/stock")
@login_required
def stock():
    purchased, sold, available = stock_summary()
    conn = db()
    movements = conn.execute(
        "SELECT * FROM stock_movements ORDER BY id DESC LIMIT 200").fetchall()
    low = available <= 100
    depleted = available <= 0
    conn.close()
    return render_template("stock.html", purchased=purchased, sold=sold,
                           available=available, movements=movements,
                           low=low, depleted=depleted)


@app.route("/notifications")
@login_required
def notifications():
    conn = db()
    purchased = conn.execute(
        "SELECT COALESCE(SUM(quantity_received_kg),0) v FROM purchases WHERE deleted=0").fetchone()["v"]
    sold = conn.execute(
        "SELECT COALESCE(SUM(quantity_kg),0) v FROM sales WHERE deleted=0").fetchone()["v"]
    stock = float(purchased) - float(sold)
    unpaid_rows = conn.execute("""SELECT s.transaction_id, c.name customer_name,
        s.total_sale - COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.transaction_id=s.transaction_id AND p.deleted=0),0) balance
        FROM sales s JOIN customers c ON c.id=s.customer_id WHERE s.deleted=0
        AND COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.transaction_id=s.transaction_id AND p.deleted=0),0) < s.total_sale
        ORDER BY s.id DESC LIMIT 20""").fetchall()
    recent_audit = conn.execute(
        "SELECT username, action, reference, created_at FROM audit_log ORDER BY id DESC LIMIT 15").fetchall()
    conn.close()
    items = []
    if stock <= 0:
        items.append({"kind": "danger", "icon": "box-seam", "title": "Stock depleted",
                      "body": "No available maize stock remains.", "time": now()})
    elif stock <= 100:
        items.append({"kind": "warning", "icon": "exclamation-triangle", "title": "Low stock",
                      "body": f"Only {stock:,.2f} KG remains.", "time": now()})
    for r in unpaid_rows:
        items.append({"kind": "warning", "icon": "hourglass-split",
                      "title": f"Outstanding balance — {r['customer_name']}",
                      "body": f"{r['transaction_id']} owes {money(r['balance'])}.", "time": now()})
    for r in recent_audit:
        items.append({"kind": "info", "icon": "activity",
                      "title": f"{r['action'].title()} {r['reference'] or ''}".strip(),
                      "body": f"by {r['username']} · {r['created_at']}", "time": r["created_at"]})
    if request.args.get("mark_read") == "1":
        session["notifications_read_at"] = now()
        return redirect(url_for("notifications"))
    return render_template("notifications.html", items=items, stock=stock)


@app.route("/assistant", methods=["GET", "POST"])
@login_required
def assistant():
    question = request.values.get("question", "").strip()
    result = None
    if question:
        conn = db()
        try:
            result = assistant_service.answer_question(conn, question)
        finally:
            conn.close()
    return render_template("assistant.html", question=question, result=result)


@app.route("/health")
def health():
    return {"status":"ok","application":"ADUFARMS","time":now()}


@app.errorhandler(403)
def forbidden(error):
    return render_template("error.html", code=403, message="You do not have permission to perform this action."), 403


@app.errorhandler(400)
def bad_request(error):
    return render_template("error.html", code=400, message=getattr(error, "description", "The request could not be processed.")), 400


@app.errorhandler(404)
def not_found(error):
    return render_template("error.html", code=404, message="The requested page could not be found."), 404


@app.errorhandler(405)
def method_not_allowed(error):
    return render_template("error.html", code=405, message="This action is not allowed with the current method."), 405


@app.errorhandler(500)
def server_error(error):
    return render_template("error.html", code=500, message="Something went wrong while processing your request."), 500


if __name__ == "__main__":
    init_db()
    app.run(debug=os.environ.get("ADUFARMS_DEBUG", "0") == "1", use_reloader=False, host="127.0.0.1", port=5000)