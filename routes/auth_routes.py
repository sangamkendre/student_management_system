import secrets
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from utils.db import get_db_connection
from utils.auth_helpers import verify_password, hash_password
from utils.email_service import send_password_reset_email

auth = Blueprint("auth", __name__)

@auth.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        selected_role = request.form.get("role", "auto").strip()

        if not email or not password:
            flash("Please provide both email and password.", "danger")
            return render_template("auth/login.html", email=email, selected_role=selected_role)

        connection = get_db_connection()
        cursor = connection.cursor()

        matched_user = None
        user_role = None

        # Determine which role(s) to check
        roles_to_check = ["admin", "teacher", "student"] if selected_role == "auto" else [selected_role]

        for role in roles_to_check:
            if role == "admin":
                cursor.execute("SELECT admin_id AS id, full_name, email, password FROM admins WHERE email = %s", (email,))
                user = cursor.fetchone()
                if user and verify_password(password, user["password"]):
                    matched_user = user
                    user_role = "admin"
                    break
            elif role == "teacher":
                cursor.execute("SELECT teacher_id AS id, full_name, email, password, status FROM teachers WHERE email = %s", (email,))
                user = cursor.fetchone()
                if user and verify_password(password, user["password"]):
                    if user["status"] != "active":
                        cursor.close()
                        connection.close()
                        flash("Your teacher account is deactivated. Please contact the administrator.", "danger")
                        return render_template("auth/login.html", email=email, selected_role=selected_role)
                    matched_user = user
                    user_role = "teacher"
                    break
            elif role == "student":
                cursor.execute("SELECT student_id AS id, full_name, email, password, status FROM students WHERE email = %s", (email,))
                user = cursor.fetchone()
                if user and verify_password(password, user["password"]):
                    if user["status"] != "active":
                        cursor.close()
                        connection.close()
                        flash("Your student account is deactivated. Please contact the administrator.", "danger")
                        return render_template("auth/login.html", email=email, selected_role=selected_role)
                    matched_user = user
                    user_role = "student"
                    break

        cursor.close()
        connection.close()

        if matched_user and user_role:
            session.clear()
            session["user_id"] = matched_user["id"]
            session["role"] = user_role
            session["user_name"] = matched_user["full_name"]
            session["email"] = matched_user["email"]

            flash(f"Welcome back, {matched_user['full_name']}!", "success")

            next_url = request.args.get("next")
            if next_url and next_url.startswith("/"):
                return redirect(next_url)

            if user_role == "admin":
                return redirect(url_for("admin.dashboard"))
            elif user_role == "teacher":
                return redirect(url_for("teacher.dashboard"))
            elif user_role == "student":
                return redirect(url_for("student.dashboard"))

        flash("Invalid email or password. Please check your credentials.", "danger")
        return render_template("auth/login.html", email=email, selected_role=selected_role)

    # If already logged in, redirect to their role dashboard
    if "user_id" in session and "role" in session:
        role = session["role"]
        if role == "admin":
            return redirect(url_for("admin.dashboard"))
        elif role == "teacher":
            return redirect(url_for("teacher.dashboard"))
        elif role == "student":
            return redirect(url_for("student.dashboard"))

    return render_template("auth/login.html")

@auth.route("/logout")
def logout():
    session.clear()
    flash("You have been signed out successfully.", "info")
    return redirect(url_for("auth.login"))

# ==================== FORGOT & RESET PASSWORD ====================

def _ensure_password_resets_table(cursor):
    """Ensure password_resets table exists in the database."""
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS password_resets (
            reset_id INT AUTO_INCREMENT PRIMARY KEY,
            email VARCHAR(255) NOT NULL,
            role ENUM('admin', 'teacher', 'student') NOT NULL,
            token VARCHAR(255) NOT NULL UNIQUE,
            expires_at DATETIME NOT NULL,
            used TINYINT(1) DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_token (token),
            INDEX idx_email_role (email, role)
        )
    """)

@auth.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        selected_role = request.form.get("role", "auto").strip()

        if not email:
            flash("Please provide your registered email address.", "danger")
            return render_template("auth/forgot_password.html", email=email, selected_role=selected_role)

        conn = get_db_connection()
        cursor = conn.cursor()
        _ensure_password_resets_table(cursor)

        matched_user = None
        user_role = None

        roles_to_check = ["admin", "teacher", "student"] if selected_role == "auto" else [selected_role]

        for r in roles_to_check:
            if r == "admin":
                cursor.execute("SELECT admin_id AS id, full_name, email FROM admins WHERE email = %s", (email,))
                u = cursor.fetchone()
                if u:
                    matched_user = u
                    user_role = "admin"
                    break
            elif r == "teacher":
                cursor.execute("SELECT teacher_id AS id, full_name, email, status FROM teachers WHERE email = %s", (email,))
                u = cursor.fetchone()
                if u:
                    if u.get("status") != "active":
                        cursor.close()
                        conn.close()
                        flash("Your teacher account is deactivated. Please contact the administrator.", "danger")
                        return render_template("auth/forgot_password.html", email=email, selected_role=selected_role)
                    matched_user = u
                    user_role = "teacher"
                    break
            elif r == "student":
                cursor.execute("SELECT student_id AS id, full_name, email, status FROM students WHERE email = %s", (email,))
                u = cursor.fetchone()
                if u:
                    if u.get("status") != "active":
                        cursor.close()
                        conn.close()
                        flash("Your student account is deactivated. Please contact the administrator.", "danger")
                        return render_template("auth/forgot_password.html", email=email, selected_role=selected_role)
                    matched_user = u
                    user_role = "student"
                    break

        if not matched_user:
            cursor.close()
            conn.close()
            flash(f"No registered account found with email '{email}'. Please check your address or contact your institute admin.", "warning")
            return render_template("auth/forgot_password.html", email=email, selected_role=selected_role)

        # Invalidate any existing unused reset tokens for this email and role
        cursor.execute("""
            UPDATE password_resets 
            SET used = 1 
            WHERE email = %s AND role = %s AND used = 0
        """, (email, user_role))

        # Generate fresh secure token (30-minute validity)
        reset_token = secrets.token_urlsafe(32)
        expires_at = datetime.now() + timedelta(minutes=30)

        cursor.execute("""
            INSERT INTO password_resets (email, role, token, expires_at, used)
            VALUES (%s, %s, %s, %s, 0)
        """, (email, user_role, reset_token, expires_at))

        cursor.close()
        conn.close()

        # Dispatch email
        email_result = send_password_reset_email(
            to_email=email,
            user_name=matched_user["full_name"],
            role=user_role,
            reset_token=reset_token,
            host_url=request.host_url
        )

        return render_template("auth/reset_sent.html",
                               email=email,
                               user_name=matched_user["full_name"],
                               role=user_role,
                               email_result=email_result)

    return render_template("auth/forgot_password.html")

@auth.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    conn = get_db_connection()
    cursor = conn.cursor()
    _ensure_password_resets_table(cursor)

    # Check token validity
    cursor.execute("""
        SELECT * FROM password_resets 
        WHERE token = %s
    """, (token,))
    reset_entry = cursor.fetchone()

    if not reset_entry:
        cursor.close()
        conn.close()
        flash("Invalid password reset link. Please request a new link.", "danger")
        return redirect(url_for("auth.forgot_password"))

    if reset_entry["used"] == 1:
        cursor.close()
        conn.close()
        flash("This password reset link has already been used. Please request a new one.", "warning")
        return redirect(url_for("auth.forgot_password"))

    if reset_entry["expires_at"] < datetime.now():
        cursor.close()
        conn.close()
        flash("This password reset link has expired (30-minute limit). Please request a fresh link.", "warning")
        return redirect(url_for("auth.forgot_password"))

    role = reset_entry["role"]
    email = reset_entry["email"]

    # Retrieve user's name
    user_name = "User"
    if role == "admin":
        cursor.execute("SELECT full_name FROM admins WHERE email = %s", (email,))
        u = cursor.fetchone()
        if u: user_name = u["full_name"]
    elif role == "teacher":
        cursor.execute("SELECT full_name FROM teachers WHERE email = %s", (email,))
        u = cursor.fetchone()
        if u: user_name = u["full_name"]
    elif role == "student":
        cursor.execute("SELECT full_name FROM students WHERE email = %s", (email,))
        u = cursor.fetchone()
        if u: user_name = u["full_name"]

    if request.method == "POST":
        new_password = request.form.get("password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()

        if not new_password or not confirm_password:
            flash("Please fill in both password fields.", "danger")
            return render_template("auth/reset_password.html", token=token, email=email, role=role, user_name=user_name)

        if len(new_password) < 6:
            flash("Password must be at least 6 characters long.", "warning")
            return render_template("auth/reset_password.html", token=token, email=email, role=role, user_name=user_name)

        if new_password != confirm_password:
            flash("Passwords do not match. Please re-enter them.", "danger")
            return render_template("auth/reset_password.html", token=token, email=email, role=role, user_name=user_name)

        hashed = hash_password(new_password)

        # Update password in role table
        if role == "admin":
            cursor.execute("UPDATE admins SET password = %s WHERE email = %s", (hashed, email))
        elif role == "teacher":
            cursor.execute("UPDATE teachers SET password = %s WHERE email = %s", (hashed, email))
        elif role == "student":
            cursor.execute("UPDATE students SET password = %s WHERE email = %s", (hashed, email))

        # Mark token as used
        cursor.execute("UPDATE password_resets SET used = 1 WHERE reset_id = %s", (reset_entry["reset_id"],))

        cursor.close()
        conn.close()

        flash(f"Password for {email} ({role.title()}) has been reset successfully! Please log in with your new password.", "success")
        return redirect(url_for("auth.login"))

    cursor.close()
    conn.close()

    return render_template("auth/reset_password.html",
                           token=token,
                           email=email,
                           role=role,
                           user_name=user_name)