# ADUFARMS Hardening Audit Report — 2026-09-10

## 1. Inspection (before modifications)
- Project: Flask + SQLite (`ADUFARMS/app.py`, ~1082 lines), templates (15), static, `exports/invoices/`.
- DB `adufarms.db` (106,496 bytes): users=3, purchases=1, sales=1, payments=1, customers=2, audit_log=58, stock_movements=2, reversals=0.
- Existing strengths: password hashing, active-user check, CSRF, admin_required on delete/restore/users/audit, stock non-negative checks on create, soft-delete (deleted flag), audit_log + reversals tables.
- Gaps found:
  1. No backup/restore feature; no `.env.example`; no dotenv loading.
  2. CSV export used manual string concatenation.
  3. Invoice showed only Transaction ID (no distinct Invoice No).
  4. `edit_purchase` / `edit_sale` did NOT adjust `stock_movements` (stock drift bug).
  5. Purchase/sale/payment creation used 2 separate DB connections (non-atomic: insert then movement).
  6. Audit actions used generic names (`TRANSACTION REVERSED`, `PAYMENT RECORDED`, `USER MODIFIED`) — spec requires distinct PURCHASE/SALE/PAYMENT REVERSED/RESTORED/DELETED, USER ENABLED/DISABLED, PROFILE UPDATED, DATABASE BACKUP/RESTORE, ADMIN ACTION.
  7. `log_action` stored username only (no user ID).
  8. No session lifetime; no global server-side guard for admin endpoints (relied on decorators only).
  9. Reports limited to sales+purchases+payment_total (missing payments detail, balances, stock, expenses, profit, counts).

## 2. Backup (data preservation)
- Created `backups/adufarms_pre_hardening_20260910_180531.db` (106,496 bytes) BEFORE any code change.
- `init_db()` now auto-creates a timestamped backup before migrations.
- Never deleted/reset DB; schema changes are additive only; historical users/transactions/audit preserved.

## 3. Changes (preserving functionality)
- New `stock_service.py`: single centralized stock module (available_stock, assert_stock_available, apply_purchase/sale_create/edit, apply_reversal/restoration). All callers pass one connection → atomic.
- New `backup_service.py`: timestamped `adufarms_YYYYMMDD_HHMMSS.db`, collision-safe, SQLite backup API, `safe_restore` with path-traversal guard + pre-restore safety backup. Stored in `backups/` (gitignored, not served).
- `app.py` hardened: dotenv, 30-min permanent session, `actor_label()` (username + id), spec-compliant audit names, atomic purchase/sale/payment/edit/reverse/restore transactions with rollback, `csv.writer` exports (sales/purchases/payments/balances), invoice_number (`INV-…`) in HTML+PDF (all 11 fields), admin `/admin/backup` + `/admin/restore` (admin-only, logged), USER ENABLED/DISABLED, PROFILE UPDATED, ADMIN ACTION, global `before_request` RBAC guard, no route serves `.db`.
- Templates: `backup.html` (new), `invoice.html` (Invoice No + Transaction ID + sale date/qty/price), `base.html` (Backup nav for ADMIN).
- Config: `.env.example` (placeholders), `requirements.txt` (+python-dotenv), `.gitignore` (backups/*.db, .env).

## 4. Tests (2026-09-10)
- Health 200, unauth → /login, login-fail 200, STAFF → /users,/admin/audit-logs,/admin/backup,/restore,/delete = 403, ADMIN → backup/audit 200, CSV sales 200 text/csv, PDF 200 application/pdf (2764 bytes), invoice HTML has both numbers, stock=40.0 KG, all 24 audit actions present, csv.writer + session lifetime + dotenv present, no password-hash leak, no audit DELETE/UPDATE, no direct .db route, stock math 100-60=40 non-negative.
- DB size unchanged (106,496 bytes + audit growth from tests only); no users/transactions removed.

## 5. How to run
```
cd ADUFARMS_Flask_System/ADUFARMS
pip install -r requirements.txt
copy .env.example .env
python app.py