from flask import Blueprint, render_template, request, redirect, session, url_for, flash
from .models import db, User

auth_bp = Blueprint("auth", __name__, template_folder="templates")


@auth_bp.route("/")
def root():
    return redirect(url_for("auth.login"))


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        # Find user by username
        user = User.query.filter_by(username=username).first()

        if user and user.password == password:  # In production, use password hashing!
            session["user_id"] = user.id
            session["role"] = user.role
            flash("Login successful", "success")

            # Redirect based on role
            if user.role == "admin":
                return redirect("/admin/dashboard")
            else:
                return redirect("/agent/dashboard")

        flash("Invalid username or password", "danger")
        return redirect(url_for("auth.login"))

    return render_template("auth/login.html")


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))