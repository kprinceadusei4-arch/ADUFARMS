from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default="staff")


class Supplier(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    phone = db.Column(db.String(50))
    location = db.Column(db.String(150))


class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    phone = db.Column(db.String(50))
    location = db.Column(db.String(150))


class Purchase(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    purchase_id = db.Column(db.String(50), unique=True, nullable=False)
    date = db.Column(db.Date, nullable=False)
    supplier = db.Column(db.String(150), nullable=False)
    location = db.Column(db.String(150))
    quantity = db.Column(db.Float, nullable=False)
    unit = db.Column(db.String(30), default="Bags")
    price_per_unit = db.Column(db.Float, default=0)
    transport_cost = db.Column(db.Float, default=0)
    loading_cost = db.Column(db.Float, default=0)
    other_expenses = db.Column(db.Float, default=0)
    total_landed_cost = db.Column(db.Float, default=0)
    amount_paid = db.Column(db.Float, default=0)
    balance = db.Column(db.Float, default=0)
    payment_status = db.Column(db.String(30), default="UNPAID")


class Sale(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.String(50), unique=True, nullable=False)
    date = db.Column(db.Date, nullable=False)
    customer = db.Column(db.String(150), nullable=False)
    quantity = db.Column(db.Float, nullable=False)
    unit = db.Column(db.String(30), default="Bags")
    selling_price = db.Column(db.Float, default=0)
    total_amount = db.Column(db.Float, default=0)
    amount_paid = db.Column(db.Float, default=0)
    balance = db.Column(db.Float, default=0)
    payment_status = db.Column(db.String(30), default="UNPAID")


class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    payment_id = db.Column(db.String(50), unique=True, nullable=False)
    date = db.Column(db.Date, nullable=False)
    payment_type = db.Column(db.String(30), nullable=False)
    reference_id = db.Column(db.String(50))
    customer_supplier = db.Column(db.String(150))
    amount = db.Column(db.Float, default=0)
    payment_method = db.Column(db.String(50))
    reference = db.Column(db.String(150))


class Invoice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    invoice_number = db.Column(db.String(50), unique=True, nullable=False)
    date = db.Column(db.Date, nullable=False)
    customer = db.Column(db.String(150), nullable=False)
    sale_id = db.Column(db.String(50))
    total_amount = db.Column(db.Float, default=0)
    amount_paid = db.Column(db.Float, default=0)
    balance = db.Column(db.Float, default=0)
    status = db.Column(db.String(30), default="UNPAID")