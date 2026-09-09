# ADUFARMS – Maize Distribution Management System

A Flask + SQLite web application designed to manage maize purchasing, sales, payments, inventory, customers, suppliers, invoices, and business reporting.

## Core Modules

* Admin and staff login
* Role-based access control
* Purchase management
* Customer management
* Sales management
* Maize stock tracking
* Payment tracking
* Invoice management
* Transaction and customer search
* Business dashboard and reports
* Profit and outstanding balance tracking
* SQLite database
* Responsive web interface

## Technology

* Python
* Flask
* Flask-SQLAlchemy
* SQLite
* HTML/CSS
* JavaScript

## Run on Windows PowerShell

```powershell
cd ADUFARMS
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Then open:

```text
http://127.0.0.1:5000
```

## Database

The application uses SQLite for local development.

The database file is created automatically when the application is first started.

The local database file is excluded from Git using `.gitignore` so that business data is not uploaded to the repository.

## User Access

ADUFARMS supports different user roles.

### Administrator

Administrators have access to system management, configuration, users, transactions, reports, and other administrative functions.

### Staff

Staff users have access to normal business operations such as purchases, customers, sales, payments, invoices, and permitted searches/reports.

## Security

* Passwords are stored using password hashing.
* User access is controlled by roles.
* Sensitive configuration should be stored in environment variables.
* Local business data should not be committed to Git.

## Project Structure

```text
ADUFARMS/
│
├── app.py
├── database.py
├── requirements.txt
├── .gitignore
│
└── templates/
    ├── login.html
    └── dashboard.html
```

## Development Status

ADUFARMS is being developed as a modular maize trading and distribution management system.

Planned modules include:

* Purchase management
* Sales management
* Inventory management
* Payment tracking
* Invoice generation
* Search
* Reports and dashboard
* User and role management
* Audit logging

## Deployment

The current version is intended primarily for local development and testing.

For deployment across multiple computers or a business network, a production WSGI server and a shared database such as PostgreSQL should be considered.
