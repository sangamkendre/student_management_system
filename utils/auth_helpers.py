import re
from datetime import datetime
from functools import wraps
from flask import session, redirect, url_for, flash, request
from werkzeug.security import generate_password_hash, check_password_hash

def generate_next_enrollment_number(cursor) -> str:
    """
    Generate a unique, sequential enrollment number in format STU-{YEAR}-{SEQ:03d}
    e.g. STU-2026-001, STU-2026-002, etc.
    Guarantees uniqueness by checking existing database records.
    """
    current_year = datetime.now().year
    prefix = f"STU-{current_year}-"

    cursor.execute("SELECT enrollment_number FROM students WHERE enrollment_number LIKE %s", (f"{prefix}%",))
    rows = cursor.fetchall()

    max_seq = 0
    pattern = re.compile(rf"^STU-{current_year}-(\d+)$")
    for r in rows:
        val = r.get("enrollment_number") if isinstance(r, dict) else r[0]
        if val:
            match = pattern.match(val.strip())
            if match:
                try:
                    num = int(match.group(1))
                    if num > max_seq:
                        max_seq = num
                except ValueError:
                    pass

    candidate_num = max_seq + 1
    while True:
        candidate_str = f"{prefix}{candidate_num:03d}"
        cursor.execute("SELECT student_id FROM students WHERE enrollment_number = %s", (candidate_str,))
        if not cursor.fetchone():
            return candidate_str
        candidate_num += 1


def hash_password(password: str) -> str:
    """Hash a password for secure storage."""
    return generate_password_hash(password)

def verify_password(plain_password: str, stored_password: str) -> bool:
    """Verify password supporting both werkzeug hashes and plaintext fallback."""
    if not stored_password or not plain_password:
        return False
    if stored_password.startswith(("pbkdf2:", "scrypt:", "argon2:")):
        return check_password_hash(stored_password, plain_password)
    # Plaintext fallback for legacy/seeded records
    return stored_password == plain_password

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session or "role" not in session:
            flash("Please log in to access this page.", "warning")
            return redirect(url_for("auth.login", next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def role_required(*allowed_roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if "user_id" not in session or "role" not in session:
                flash("Please log in to continue.", "warning")
                return redirect(url_for("auth.login", next=request.url))
            if session.get("role") not in allowed_roles:
                flash("You do not have permission to access that page.", "danger")
                user_role = session.get("role")
                if user_role == "admin":
                    return redirect(url_for("admin.dashboard"))
                elif user_role == "teacher":
                    return redirect(url_for("teacher.dashboard"))
                elif user_role == "student":
                    return redirect(url_for("student.dashboard"))
                return redirect(url_for("auth.login"))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def admin_required(f):
    return role_required("admin")(f)

def teacher_required(f):
    return role_required("teacher")(f)

def student_required(f):
    return role_required("student")(f)
