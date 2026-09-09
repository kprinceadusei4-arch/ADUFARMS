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
- Dashboard: stock, sales, payments, expenses and estimated profit
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
- Username: admin
- Password: admin123

Change the default password after first login.

## Database
The SQLite database `adufarms.db` is created automatically on first run.

## Notes
This version is designed as a solid local business system. For multi-computer/network deployment, use a production WSGI server and a shared database such as PostgreSQL.
