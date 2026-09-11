# ADUFARMS – Maize Distribution Management System

A Flask + SQLite web application for maize trading/distribution.

## Features
- Admin/staff login and role-based access
- Purchase entry with automatic purchase IDs
- Customer sales with automatic transaction IDs
- Stock control; sales cannot exceed available stock
- Multiple payments per customer transaction
- Automatic PAID / PART PAYMENT / UNPAID status
- Printable A4 invoice and PDF export
- Customer/transaction search
- Customer ledger with statements, opening balances and derived outstanding balances
- Dashboard: stock, sales, payments, expenses and estimated profit
- Verified database assistant for stock, activity, balances and top-customer questions
- Official branding path: `static/images/branding/adufarms-logo.jpg`
- SQLite database
- Basic audit log
- Responsive business interface

## Run on Windows PowerShell

```powershell
cd ADUFARMS
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Then open:
http://127.0.0.1:5000

## First login
Use an administrator account already provisioned for your environment. The application does not display or create a known default password.

Staff can create purchases, customers, sales and payments. Financial-record edits, reversals, permanent deletion, user management, backups and audit logs are restricted to administrators.

The AI Assistant is local and database-backed. It does not call an external provider or invent financial figures; unsupported questions receive a clear limitation message. External AI or notification integrations can be added later using environment variables.

## Database
The SQLite database `adufarms.db` is created automatically on first run.

## Notes
This version is designed as a solid local business system. For multi-computer/network deployment, use a production WSGI server and a shared database such as PostgreSQL.
