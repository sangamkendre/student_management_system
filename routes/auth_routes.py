import secrets
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from utils.db import get_db_connection
from utils.auth_helpers import verify_password, hash_password
from utils.email_service import send_password_reset_email, send_password_reset_otp

auth = Blueprint("auth", __name__)

@auth.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
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
                cursor.execute("SELECT admin_id AS id, full_name, email, password FROM admins WHERE LOWER(email) = LOWER(%s)", (email,))
                user = cursor.fetchone()
                if user and verify_password(password, user["password"]):
                    matched_user = user
                    user_role = "admin"
                    break
            elif role == "teacher":
                cursor.execute("SELECT teacher_id AS id, full_name, email, password, status FROM teachers WHERE LOWER(email) = LOWER(%s)", (email,))
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
                cursor.execute("SELECT student_id AS id, full_name, email, password, status FROM students WHERE LOWER(email) = LOWER(%s)", (email,))
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
    """Ensure password_resets table exists in the database with otp column."""
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS password_resets (
            reset_id INT AUTO_INCREMENT PRIMARY KEY,
            email VARCHAR(255) NOT NULL,
            role ENUM('admin', 'teacher', 'student') NOT NULL,
            token VARCHAR(255) NOT NULL UNIQUE,
            otp VARCHAR(10) NULL,
            expires_at DATETIME NOT NULL,
            used TINYINT(1) DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_token (token),
            INDEX idx_email_role (email, role)
        )
    """)
    # Ensure otp column exists if the table was created before
    try:
        cursor.execute("ALTER TABLE password_resets ADD COLUMN otp VARCHAR(10) NULL AFTER token")
    except Exception:
        pass

@auth.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
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
                cursor.execute("SELECT admin_id AS id, full_name, email FROM admins WHERE LOWER(email) = LOWER(%s)", (email,))
                u = cursor.fetchone()
                if u:
                    matched_user = u
                    user_role = "admin"
                    break
            elif r == "teacher":
                cursor.execute("SELECT teacher_id AS id, full_name, email, status FROM teachers WHERE LOWER(email) = LOWER(%s)", (email,))
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
                cursor.execute("SELECT student_id AS id, full_name, email, status FROM students WHERE LOWER(email) = LOWER(%s)", (email,))
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
            WHERE LOWER(email) = LOWER(%s) AND role = %s AND used = 0
        """, (email, user_role))

        # Generate fresh 6-digit numeric OTP and secure session token (10-minute validity)
        otp = f"{secrets.randbelow(900000) + 100000}"
        reset_token = secrets.token_urlsafe(32)
        expires_at = datetime.now() + timedelta(minutes=10)

        cursor.execute("""
            INSERT INTO password_resets (email, role, token, otp, expires_at, used)
            VALUES (%s, %s, %s, %s, %s, 0)
        """, (email, user_role, reset_token, otp, expires_at))

        cursor.close()
        conn.close()

        # Dispatch OTP email
        verify_url = url_for("auth.verify_otp", token=reset_token, _external=True)
        email_result = send_password_reset_otp(
            to_email=email,
            user_name=matched_user["full_name"],
            role=user_role,
            otp=otp,
            reset_url=verify_url
        )

        session["reset_token"] = reset_token
        session["reset_email"] = email
        session["reset_role"] = user_role

        if not email_result.get("sent"):
            err_msg = email_result.get("error") or "Please check your SMTP settings."
            flash(f"Failed to send verification email: {err_msg}", "danger")
        else:
            flash(f"A 6-digit OTP verification code has been sent to your email ({email}).", "success")

        return redirect(url_for("auth.verify_otp", token=reset_token))

    return render_template("auth/forgot_password.html")

@auth.route("/verify-otp", methods=["GET", "POST"])
def verify_otp():
    token = request.args.get("token") or request.form.get("token") or session.get("reset_token")
    if not token:
        flash("No active password reset request found. Please enter your email first.", "warning")
        return redirect(url_for("auth.forgot_password"))

    conn = get_db_connection()
    cursor = conn.cursor()
    _ensure_password_resets_table(cursor)

    cursor.execute("""
        SELECT * FROM password_resets 
        WHERE token = %s
    """, (token,))
    reset_entry = cursor.fetchone()

    if not reset_entry:
        cursor.close()
        conn.close()
        flash("Invalid or expired session. Please request a new OTP.", "danger")
        return redirect(url_for("auth.forgot_password"))

    email = reset_entry["email"]
    role = reset_entry["role"]
    correct_otp = str(reset_entry.get("otp") or "").strip()

    # Retrieve user's name
    user_name = "User"
    if role == "admin":
        cursor.execute("SELECT full_name FROM admins WHERE LOWER(email) = LOWER(%s)", (email,))
        u = cursor.fetchone()
        if u: user_name = u["full_name"]
    elif role == "teacher":
        cursor.execute("SELECT full_name FROM teachers WHERE LOWER(email) = LOWER(%s)", (email,))
        u = cursor.fetchone()
        if u: user_name = u["full_name"]
    elif role == "student":
        cursor.execute("SELECT full_name FROM students WHERE LOWER(email) = LOWER(%s)", (email,))
        u = cursor.fetchone()
        if u: user_name = u["full_name"]

    if request.method == "POST":
        submitted_otp = request.form.get("otp", "").strip()
        new_password = request.form.get("password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()

        if reset_entry["used"] == 1:
            cursor.close()
            conn.close()
            flash("This OTP has already been used. Please request a new code.", "warning")
            return redirect(url_for("auth.forgot_password"))

        if reset_entry["expires_at"] < datetime.now():
            cursor.close()
            conn.close()
            flash("This OTP has expired (10-minute limit). Please request a fresh OTP.", "warning")
            return redirect(url_for("auth.forgot_password"))

        if not submitted_otp or submitted_otp != correct_otp:
            cursor.close()
            conn.close()
            flash("Invalid OTP code. Please enter the correct 6-digit code sent to your email.", "danger")
            return render_template("auth/verify_otp.html",
                                   token=token,
                                   email=email,
                                   role=role,
                                   user_name=user_name,
                                   submitted_otp=submitted_otp)

        if not new_password or not confirm_password:
            cursor.close()
            conn.close()
            flash("Please fill in both password fields.", "danger")
            return render_template("auth/verify_otp.html",
                                   token=token,
                                   email=email,
                                   role=role,
                                   user_name=user_name,
                                   submitted_otp=submitted_otp)

        if len(new_password) < 6:
            cursor.close()
            conn.close()
            flash("Password must be at least 6 characters long.", "warning")
            return render_template("auth/verify_otp.html",
                                   token=token,
                                   email=email,
                                   role=role,
                                   user_name=user_name,
                                   submitted_otp=submitted_otp)

        if new_password != confirm_password:
            cursor.close()
            conn.close()
            flash("Passwords do not match. Please re-enter them.", "danger")
            return render_template("auth/verify_otp.html",
                                   token=token,
                                   email=email,
                                   role=role,
                                   user_name=user_name,
                                   submitted_otp=submitted_otp)

        hashed = hash_password(new_password)

        if role == "admin":
            cursor.execute("UPDATE admins SET password = %s WHERE LOWER(email) = LOWER(%s)", (hashed, email))
        elif role == "teacher":
            cursor.execute("UPDATE teachers SET password = %s WHERE LOWER(email) = LOWER(%s)", (hashed, email))
        elif role == "student":
            cursor.execute("UPDATE students SET password = %s WHERE LOWER(email) = LOWER(%s)", (hashed, email))

        cursor.execute("UPDATE password_resets SET used = 1 WHERE reset_id = %s", (reset_entry["reset_id"],))

        cursor.close()
        conn.close()

        session.pop("reset_token", None)
        session.pop("reset_email", None)
        session.pop("reset_role", None)

        flash(f"Password for {email} ({role.title()}) has been reset successfully! Please log in with your new password.", "success")
        return redirect(url_for("auth.login"))

    cursor.close()
    conn.close()

    return render_template("auth/verify_otp.html",
                           token=token,
                           email=email,
                           role=role,
                           user_name=user_name)

@auth.route("/resend-otp", methods=["POST"])
def resend_otp():
    token = request.form.get("token") or session.get("reset_token")
    if not token:
        flash("No active reset session. Please submit your email again.", "warning")
        return redirect(url_for("auth.forgot_password"))

    conn = get_db_connection()
    cursor = conn.cursor()
    _ensure_password_resets_table(cursor)

    cursor.execute("SELECT * FROM password_resets WHERE token = %s", (token,))
    entry = cursor.fetchone()

    if not entry:
        cursor.close()
        conn.close()
        flash("Session expired. Please request a new OTP.", "danger")
        return redirect(url_for("auth.forgot_password"))

    email = entry["email"]
    role = entry["role"]

    # Mark old token as used
    cursor.execute("UPDATE password_resets SET used = 1 WHERE reset_id = %s", (entry["reset_id"],))

    # Generate new OTP & token
    new_otp = f"{secrets.randbelow(900000) + 100000}"
    new_token = secrets.token_urlsafe(32)
    new_expiry = datetime.now() + timedelta(minutes=10)

    cursor.execute("""
        INSERT INTO password_resets (email, role, token, otp, expires_at, used)
        VALUES (%s, %s, %s, %s, %s, 0)
    """, (email, role, new_token, new_otp, new_expiry))

    user_name = "User"
    if role == "admin":
        cursor.execute("SELECT full_name FROM admins WHERE LOWER(email) = LOWER(%s)", (email,))
        u = cursor.fetchone()
        if u: user_name = u["full_name"]
    elif role == "teacher":
        cursor.execute("SELECT full_name FROM teachers WHERE LOWER(email) = LOWER(%s)", (email,))
        u = cursor.fetchone()
        if u: user_name = u["full_name"]
    elif role == "student":
        cursor.execute("SELECT full_name FROM students WHERE LOWER(email) = LOWER(%s)", (email,))
        u = cursor.fetchone()
        if u: user_name = u["full_name"]

    cursor.close()
    conn.close()

    verify_url = url_for("auth.verify_otp", token=new_token, _external=True)
    email_result = send_password_reset_otp(
        to_email=email,
        user_name=user_name,
        role=role,
        otp=new_otp,
        reset_url=verify_url
    )

    session["reset_token"] = new_token
    session["reset_email"] = email
    session["reset_role"] = role

    if not email_result.get("sent"):
        err_msg = email_result.get("error") or "Please check your SMTP settings."
        flash(f"Failed to resend OTP: {err_msg}", "danger")
    else:
        flash("A fresh 6-digit OTP has been sent to your email.", "info")

    return redirect(url_for("auth.verify_otp", token=new_token))

@auth.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    return redirect(url_for("auth.verify_otp", token=token))