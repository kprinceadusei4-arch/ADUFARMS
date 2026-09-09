from flask import Flask, render_template, request, redirect, url_for, session
from database import db, User
from werkzeug.security import generate_password_hash, check_password_hash
import os

app = Flask(__name__)

app.secret_key = "adufarms-secret-key-change-this"

# Database configuration
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

app.config["SQLALCHEMY_DATABASE_URI"] = (
    "sqlite:///" + os.path.join(BASE_DIR, "adufarms.db")
)

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Connect database
db.init_app(app)


# Create database and default admin
with app.app_context():

    db.create_all()

    admin = User.query.filter_by(username="admin").first()

    if not admin:
        admin = User(
            username="admin",
            password=generate_password_hash("admin123"),
            role="admin"
        )

        db.session.add(admin)
        db.session.commit()


# LOGIN
@app.route("/", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        username = request.form.get("username")
        password = request.form.get("password")

        user = User.query.filter_by(username=username).first()

        if user and check_password_hash(user.password, password):

            session["logged_in"] = True
            session["username"] = user.username
            session["role"] = user.role

            return redirect(url_for("dashboard"))

        return render_template(
            "login.html",
            error="Invalid username or password"
        )

    return render_template("login.html")


# DASHBOARD
@app.route("/dashboard")
def dashboard():

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    return render_template("dashboard.html")


# LOGOUT
@app.route("/logout")
def logout():

    session.clear()

    return redirect(url_for("login"))


# START APPLICATION
if __name__ == "__main__":
    app.run(debug=True)