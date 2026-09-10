from flask import Blueprint, render_template, request, session, redirect, url_for, flash
from utils.db import get_db_connection
from utils.auth_helpers import student_required, hash_password, verify_password
from utils.fees_db import get_student_fees, get_payment_receipt

student_bp = Blueprint("student", __name__, url_prefix="/student")

def get_student_id():
    return session.get("user_id")

@student_bp.route("/dashboard")
@student_required
def dashboard():
    student_id = get_student_id()
    conn = get_db_connection()
    cursor = conn.cursor()

    # Student Profile
    cursor.execute("SELECT * FROM students WHERE student_id = %s", (student_id,))
    student = cursor.fetchone()

    # Enrolled Batches with Course, Teacher, and attendance counts
    cursor.execute("""
        SELECT b.batch_id, b.batch_name, b.start_date, b.end_date, b.start_time, b.end_time, b.status AS batch_status,
               c.course_name, c.duration,
               t.full_name AS teacher_name, t.email AS teacher_email,
               bs.joined_at, bs.status AS enrollment_status
        FROM batch_students bs
        JOIN batches b ON bs.batch_id = b.batch_id
        JOIN courses c ON b.course_id = c.course_id
        LEFT JOIN teachers t ON b.teacher_id = t.teacher_id
        WHERE bs.student_id = %s AND bs.status IN ('active', 'completed')
        ORDER BY FIELD(b.status, 'active', 'upcoming', 'completed'), b.start_date DESC
    """, (student_id,))
    batches = cursor.fetchall()

    total_classes = 0
    total_present = 0

    batch_metrics = []
    if batches:
        b_ids = [b["batch_id"] for b in batches]
        placeholders = ','.join(['%s'] * len(b_ids))
        
        # Query total sessions and presents for all batches in a single round-trip
        cursor.execute(f"""
            SELECT 
                s.batch_id,
                COUNT(DISTINCT s.session_id) AS batch_classes,
                COUNT(DISTINCT CASE WHEN a.student_id = %s AND a.status = 'present' THEN a.attendance_id END) AS batch_present
            FROM attendance_sessions s
            LEFT JOIN attendance a ON s.session_id = a.session_id AND a.student_id = %s
            WHERE s.batch_id IN ({placeholders})
            GROUP BY s.batch_id
        """, tuple([student_id, student_id] + b_ids))
        
        stats_by_batch = {row["batch_id"]: row for row in cursor.fetchall()}
        
        for b in batches:
            stats = stats_by_batch.get(b["batch_id"], {})
            batch_classes = stats.get("batch_classes", 0)
            batch_present = stats.get("batch_present", 0)
            batch_absent = max(0, batch_classes - batch_present)
            pct = round((batch_present / batch_classes * 100), 2) if batch_classes > 0 else 0.0

            total_classes += batch_classes
            total_present += batch_present

            batch_metrics.append({
                **b,
                "classes": batch_classes,
                "present": batch_present,
                "absent": batch_absent,
                "pct": pct
            })


    total_absent = max(0, total_classes - total_present)
    overall_pct = round((total_present / total_classes * 100), 2) if total_classes > 0 else 0.0

    # Recent attendance records
    cursor.execute("""
        SELECT s.session_date, s.started_at,
               b.batch_name, c.course_name, t.full_name AS teacher_name,
               a.marked_at, a.status AS att_status
        FROM attendance_sessions s
        JOIN batches b ON s.batch_id = b.batch_id
        JOIN courses c ON b.course_id = c.course_id
        LEFT JOIN teachers t ON s.teacher_id = t.teacher_id
        JOIN batch_students bs ON bs.batch_id = b.batch_id AND bs.student_id = %s
        LEFT JOIN attendance a ON a.session_id = s.session_id AND a.student_id = %s
        ORDER BY s.session_date DESC, s.started_at DESC
        LIMIT 6
    """, (student_id, student_id))
    recent_records = cursor.fetchall()

    for r in recent_records:
        if not r["att_status"]:
            r["att_status"] = "absent"

    cursor.close()
    conn.close()

    return render_template("student/dashboard.html",
                           student=student,
                           batches=batch_metrics,
                           total_classes=total_classes,
                           total_present=total_present,
                           total_absent=total_absent,
                           overall_pct=overall_pct,
                           recent_records=recent_records)

@student_bp.route("/batches")
@student_required
def batches():
    student_id = get_student_id()
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT b.*, c.course_name, c.description AS course_desc, c.duration,
               t.full_name AS teacher_name, t.email AS teacher_email, t.phone AS teacher_phone,
               bs.joined_at, bs.status AS enrollment_status
        FROM batch_students bs
        JOIN batches b ON bs.batch_id = b.batch_id
        JOIN courses c ON b.course_id = c.course_id
        LEFT JOIN teachers t ON b.teacher_id = t.teacher_id
        WHERE bs.student_id = %s
        ORDER BY FIELD(b.status, 'active', 'upcoming', 'completed'), b.start_date DESC
    """, (student_id,))
    batches = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template("student/batches.html", batches=batches)

@student_bp.route("/attendance")
@student_required
def attendance_history():
    student_id = get_student_id()
    selected_batch_id = request.args.get("batch_id", "")

    conn = get_db_connection()
    cursor = conn.cursor()

    # Enrolled batches for dropdown filter (active & completed)
    cursor.execute("""
        SELECT b.batch_id, b.batch_name, b.status AS batch_status, c.course_name
        FROM batch_students bs
        JOIN batches b ON bs.batch_id = b.batch_id
        JOIN courses c ON b.course_id = c.course_id
        WHERE bs.student_id = %s AND bs.status IN ('active', 'completed')
        ORDER BY FIELD(b.status, 'active', 'upcoming', 'completed'), b.start_date DESC
    """, (student_id,))
    enrolled_batches = cursor.fetchall()

    # Query attendance records
    query = """
        SELECT s.session_id, s.session_date, s.started_at,
               b.batch_id, b.batch_name, c.course_name, t.full_name AS teacher_name,
               a.marked_at,
               CASE WHEN a.status = 'present' THEN 'present' ELSE 'absent' END AS att_status
        FROM attendance_sessions s
        JOIN batches b ON s.batch_id = b.batch_id
        JOIN courses c ON b.course_id = c.course_id
        LEFT JOIN teachers t ON s.teacher_id = t.teacher_id
        JOIN batch_students bs ON bs.batch_id = b.batch_id AND bs.student_id = %s
        LEFT JOIN attendance a ON a.session_id = s.session_id AND a.student_id = %s
    """
    params = [student_id, student_id]

    if selected_batch_id:
        query += " WHERE b.batch_id = %s"
        params.append(selected_batch_id)

    query += " ORDER BY s.session_date DESC, s.started_at DESC"

    cursor.execute(query, tuple(params))
    history = cursor.fetchall()

    # Metrics for the filtered view
    total_classes = len(history)
    total_present = sum(1 for h in history if h["att_status"] == "present")
    total_absent = total_classes - total_present
    pct = round((total_present / total_classes * 100), 2) if total_classes > 0 else 0.0

    cursor.close()
    conn.close()

    return render_template("student/attendance.html",
                           history=history,
                           enrolled_batches=enrolled_batches,
                           selected_batch_id=selected_batch_id,
                           total_classes=total_classes,
                           total_present=total_present,
                           total_absent=total_absent,
                           pct=pct)

@student_bp.route("/scan")
@student_required
def scan():
    return render_template("student/scan.html")

@student_bp.route("/profile", methods=["GET", "POST"])
@student_required
def profile():
    student_id = get_student_id()
    conn = get_db_connection()
    cursor = conn.cursor()

    if request.method == "POST":
        action = request.form.get("action", "change_password")

        if action == "update_info":
            full_name = request.form.get("full_name", "").strip()
            phone = request.form.get("phone", "").strip()

            if not full_name:
                flash("Full name cannot be empty.", "danger")
            else:
                cursor.execute("""
                    UPDATE students
                    SET full_name = %s, phone = %s
                    WHERE student_id = %s
                """, (full_name, phone, student_id))
                session["user_name"] = full_name
                flash("Profile details updated successfully!", "success")

        elif action == "change_password":
            current_password = request.form.get("current_password", "").strip()
            new_password = request.form.get("new_password", "").strip()
            confirm_password = request.form.get("confirm_password", "").strip()

            cursor.execute("SELECT password FROM students WHERE student_id = %s", (student_id,))
            student_rec = cursor.fetchone()

            if not current_password or not new_password or not confirm_password:
                flash("All password fields are required.", "warning")
            elif not verify_password(current_password, student_rec["password"]):
                flash("Current password is incorrect.", "danger")
            elif len(new_password) < 6:
                flash("New password must be at least 6 characters long.", "warning")
            elif new_password != confirm_password:
                flash("New passwords do not match. Please re-enter.", "danger")
            else:
                hashed_pwd = hash_password(new_password)
                cursor.execute("UPDATE students SET password = %s WHERE student_id = %s", (hashed_pwd, student_id))
                flash("Password changed successfully! You can now use your new password.", "success")

    # Fetch fresh student details
    cursor.execute("""
        SELECT s.*,
               (SELECT COUNT(*) FROM batch_students bs WHERE bs.student_id = s.student_id AND bs.status = 'active') AS batch_count,
               (SELECT GROUP_CONCAT(b.batch_name SEPARATOR ', ') FROM batch_students bs 
                JOIN batches b ON bs.batch_id = b.batch_id 
                WHERE bs.student_id = s.student_id AND bs.status = 'active') AS enrolled_batches
        FROM students s
        WHERE s.student_id = %s
    """, (student_id,))
    student = cursor.fetchone()

    cursor.close()
    conn.close()

    return render_template("student/profile.html", student=student)


# ==================== STUDENT FEES & EMI PORTAL ====================

@student_bp.route("/fees")
@student_required
def fees():
    student_id = get_student_id()
    fee_records = get_student_fees(student_id)

    # Calculate student summary
    total_assigned = sum(float(item["fee"]["final_amount"]) for item in fee_records)
    total_paid = sum(float(item["fee"]["paid_amount"]) for item in fee_records)
    total_remaining = sum(float(item["fee"]["remaining_amount"]) for item in fee_records)

    return render_template("student/fees.html",
                           fee_records=fee_records,
                           total_assigned=total_assigned,
                           total_paid=total_paid,
                           total_remaining=total_remaining)


@student_bp.route("/fees/receipt/<string:receipt_number>")
@student_required
def view_receipt(receipt_number):
    receipt = get_payment_receipt(receipt_number)
    student_id = get_student_id()

    # Ensure student can only view their own receipts
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT student_id FROM student_fees WHERE fee_id = %s", (receipt["fee_id"] if receipt else 0,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()

    if not receipt or not row or row["student_id"] != student_id:
        flash("Receipt not found or unauthorized.", "danger")
        return redirect(url_for("student.fees"))

    return render_template("admin/receipt.html", receipt=receipt, is_student_view=True)

