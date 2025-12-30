from flask import Blueprint, render_template, request, redirect, session, url_for, flash
from .utils import get_db

auth_bp = Blueprint("auth", __name__, template_folder="templates")

@auth_bp.route("/")
def root():
    return redirect(url_for("auth.login"))

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        db = get_db(); cur = db.cursor()
        cur.execute("SELECT id, role, password FROM users WHERE username = ?", (username,))
        row = cur.fetchone()
        if row and row["password"] == password:
            session["user_id"] = row["id"]
            session["role"] = row["role"]
            flash("Login successful", "success")
            return redirect("/admin/dashboard") if row["role"]=="admin" else redirect("/agent/dashboard")
        flash("Invalid username or password", "danger")
        return redirect(url_for("auth.login"))
    return render_template("auth/login.html")

@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
