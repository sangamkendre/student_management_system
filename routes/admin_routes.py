import csv
import io
from datetime import datetime, date
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, Response
from utils.db import get_db_connection
from utils.auth_helpers import admin_required, hash_password, generate_next_enrollment_number
from utils.fees_db import (
    ensure_fees_tables, get_fees_summary, get_all_fees, get_fee_details,
    create_student_fee, record_fee_payment, get_payment_receipt, delete_student_fee
)

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

@admin_bp.route("/dashboard")
@admin_required
def dashboard():
    conn = get_db_connection()
    cursor = conn.cursor()

    # Consolidated high-level KPI counts in a single round-trip
    cursor.execute("""
        SELECT 
            (SELECT COUNT(*) FROM students) AS total_students,
            (SELECT COUNT(*) FROM teachers) AS total_teachers,
            (SELECT COUNT(*) FROM batches) AS total_batches,
            (SELECT COUNT(*) FROM courses) AS total_courses
    """)
    kpi = cursor.fetchone() or {}
    total_students = kpi.get("total_students", 0)
    total_teachers = kpi.get("total_teachers", 0)
    total_batches = kpi.get("total_batches", 0)
    total_courses = kpi.get("total_courses", 0)

    # Today's attendance and sessions counts in a single query
    today = date.today()
    cursor.execute("""
        SELECT 
            COALESCE(COUNT(CASE WHEN a.status = 'present' THEN 1 END), 0) AS today_present,
            COALESCE(COUNT(DISTINCT s.session_id), 0) AS today_sessions
        FROM attendance_sessions s
        LEFT JOIN attendance a ON a.session_id = s.session_id
        WHERE s.session_date = %s
    """, (today,))
    today_stats = cursor.fetchone() or {}
    today_present = today_stats.get("today_present", 0)
    today_sessions = today_stats.get("today_sessions", 0)

    # Batches summary with attendance stats
    cursor.execute("""
        SELECT b.batch_id, b.batch_name, b.status, b.start_time, b.end_time,
               c.course_name, t.full_name AS teacher_name,
               (SELECT COUNT(*) FROM batch_students bs WHERE bs.batch_id = b.batch_id AND bs.status IN ('active', 'completed')) AS student_count,
               (SELECT COUNT(*) FROM attendance_sessions s WHERE s.batch_id = b.batch_id) AS session_count
        FROM batches b
        JOIN courses c ON b.course_id = c.course_id
        LEFT JOIN teachers t ON b.teacher_id = t.teacher_id
        ORDER BY b.created_at DESC
        LIMIT 6
    """,)
    recent_batches = cursor.fetchall()

    # Recent attendance sessions
    cursor.execute("""
        SELECT s.session_id, s.session_date, s.started_at, s.status,
               b.batch_name, c.course_name, t.full_name AS teacher_name,
               (SELECT COUNT(*) FROM attendance a WHERE a.session_id = s.session_id AND a.status = 'present') AS present_count
        FROM attendance_sessions s
        JOIN batches b ON s.batch_id = b.batch_id
        JOIN courses c ON b.course_id = c.course_id
        JOIN teachers t ON s.teacher_id = t.teacher_id
        ORDER BY s.started_at DESC
        LIMIT 5
    """,)
    recent_sessions = cursor.fetchall()

    cursor.close()
    conn.close()


    fees_summary = get_fees_summary()

    return render_template("admin/dashboard.html",
                           total_students=total_students,
                           total_teachers=total_teachers,
                           total_batches=total_batches,
                           total_courses=total_courses,
                           today_present=today_present,
                           today_sessions=today_sessions,
                           recent_batches=recent_batches,
                           recent_sessions=recent_sessions,
                           fees_summary=fees_summary)

# ==================== TEACHERS MANAGEMENT ====================

@admin_bp.route("/teachers")
@admin_required
def teachers():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT t.*,
               (SELECT COUNT(*) FROM batches b WHERE b.teacher_id = t.teacher_id) AS batch_count
        FROM teachers t
        ORDER BY t.created_at DESC
    """)
    teachers_list = cursor.fetchall()

    cursor.close()
    conn.close()
    return render_template("admin/teachers.html", teachers=teachers_list)

@admin_bp.route("/teachers/add", methods=["POST"])
@admin_required
def add_teacher():
    full_name = request.form.get("full_name", "").strip()
    email = request.form.get("email", "").strip()
    phone = request.form.get("phone", "").strip()
    password = request.form.get("password", "").strip()
    specialization = request.form.get("specialization", "").strip()

    if not full_name or not email or not password:
        flash("Name, email, and password are required.", "danger")
        return redirect(url_for("admin.teachers"))

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT teacher_id FROM teachers WHERE email = %s", (email,))
        if cursor.fetchone():
            flash("A teacher with this email already exists.", "warning")
        else:
            cursor.execute("""
                INSERT INTO teachers (full_name, email, phone, password, specialization, status)
                VALUES (%s, %s, %s, %s, %s, 'active')
            """, (full_name, email, phone, hash_password(password), specialization))
            flash(f"Teacher '{full_name}' added successfully!", "success")
    except Exception as e:
        flash(f"Database error: {str(e)}", "danger")
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for("admin.teachers"))

@admin_bp.route("/teachers/<int:teacher_id>/edit", methods=["POST"])
@admin_required
def edit_teacher(teacher_id):
    full_name = request.form.get("full_name", "").strip()
    email = request.form.get("email", "").strip()
    phone = request.form.get("phone", "").strip()
    specialization = request.form.get("specialization", "").strip()
    password = request.form.get("password", "").strip()

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        if password:
            cursor.execute("""
                UPDATE teachers 
                SET full_name = %s, email = %s, phone = %s, specialization = %s, password = %s
                WHERE teacher_id = %s
            """, (full_name, email, phone, specialization, hash_password(password), teacher_id))
        else:
            cursor.execute("""
                UPDATE teachers 
                SET full_name = %s, email = %s, phone = %s, specialization = %s
                WHERE teacher_id = %s
            """, (full_name, email, phone, specialization, teacher_id))
        flash("Teacher details updated.", "success")
    except Exception as e:
        flash(f"Error updating teacher: {str(e)}", "danger")
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for("admin.teachers"))

@admin_bp.route("/teachers/<int:teacher_id>/toggle-status", methods=["POST"])
@admin_required
def toggle_teacher_status(teacher_id):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT status, full_name FROM teachers WHERE teacher_id = %s", (teacher_id,))
    teacher = cursor.fetchone()
    if teacher:
        new_status = "inactive" if teacher["status"] == "active" else "active"
        cursor.execute("UPDATE teachers SET status = %s WHERE teacher_id = %s", (new_status, teacher_id))
        flash(f"Teacher '{teacher['full_name']}' status changed to {new_status.title()}.", "info")

    cursor.close()
    conn.close()
    return redirect(url_for("admin.teachers"))

@admin_bp.route("/teachers/<int:teacher_id>/delete", methods=["POST"])
@admin_required
def delete_teacher(teacher_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT full_name FROM teachers WHERE teacher_id = %s", (teacher_id,))
        teacher = cursor.fetchone()
        if not teacher:
            flash("Teacher not found.", "warning")
        else:
            cursor.execute("DELETE FROM teachers WHERE teacher_id = %s", (teacher_id,))
            flash(f"Teacher '{teacher['full_name']}' has been deleted successfully.", "success")
    except Exception as e:
        flash(f"Error deleting teacher: {str(e)}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("admin.teachers"))

# ==================== COURSES MANAGEMENT ====================

@admin_bp.route("/courses")
@admin_required
def courses():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT c.*,
               (SELECT COUNT(*) FROM batches b WHERE b.course_id = c.course_id) AS batch_count
        FROM courses c
        ORDER BY c.created_at DESC
    """)
    courses_list = cursor.fetchall()

    cursor.close()
    conn.close()
    return render_template("admin/courses.html", courses=courses_list)

@admin_bp.route("/courses/add", methods=["POST"])
@admin_required
def add_course():
    course_name = request.form.get("course_name", "").strip()
    description = request.form.get("description", "").strip()
    duration = request.form.get("duration", "").strip()

    if not course_name:
        flash("Course name is required.", "danger")
        return redirect(url_for("admin.courses"))

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO courses (course_name, description, duration)
            VALUES (%s, %s, %s)
        """, (course_name, description, duration))
        flash(f"Course '{course_name}' created successfully!", "success")
    except Exception as e:
        flash(f"Error creating course: {str(e)}", "danger")
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for("admin.courses"))

@admin_bp.route("/courses/<int:course_id>/edit", methods=["POST"])
@admin_required
def edit_course(course_id):
    course_name = request.form.get("course_name", "").strip()
    description = request.form.get("description", "").strip()
    duration = request.form.get("duration", "").strip()

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            UPDATE courses 
            SET course_name = %s, description = %s, duration = %s
            WHERE course_id = %s
        """, (course_name, description, duration, course_id))
        flash("Course updated successfully.", "success")
    except Exception as e:
        flash(f"Error updating course: {str(e)}", "danger")
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for("admin.courses"))

@admin_bp.route("/courses/<int:course_id>/delete", methods=["POST"])
@admin_required
def delete_course(course_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM courses WHERE course_id = %s", (course_id,))
        flash("Course deleted successfully.", "info")
    except Exception as e:
        flash(f"Could not delete course: {str(e)}", "danger")
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for("admin.courses"))

# ==================== BATCHES MANAGEMENT ====================

@admin_bp.route("/batches")
@admin_required
def batches():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT b.*, c.course_name, t.full_name AS teacher_name,
               (SELECT COUNT(*) FROM batch_students bs WHERE bs.batch_id = b.batch_id AND bs.status IN ('active', 'completed')) AS student_count
        FROM batches b
        JOIN courses c ON b.course_id = c.course_id
        LEFT JOIN teachers t ON b.teacher_id = t.teacher_id
        ORDER BY b.created_at DESC
    """)
    batches_list = cursor.fetchall()

    cursor.execute("SELECT course_id, course_name FROM courses ORDER BY course_name ASC")
    all_courses = cursor.fetchall()

    cursor.execute("SELECT teacher_id, full_name, specialization FROM teachers WHERE status = 'active' ORDER BY full_name ASC")
    active_teachers = cursor.fetchall()

    cursor.close()
    conn.close()
    return render_template("admin/batches.html",
                           batches=batches_list,
                           courses=all_courses,
                           teachers=active_teachers)

@admin_bp.route("/batches/add", methods=["POST"])
@admin_required
def add_batch():
    batch_name = request.form.get("batch_name", "").strip()
    course_id = request.form.get("course_id")
    teacher_id = request.form.get("teacher_id") or None
    start_date = request.form.get("start_date") or None
    end_date = request.form.get("end_date") or None
    start_time = request.form.get("start_time") or None
    end_time = request.form.get("end_time") or None
    status = request.form.get("status", "upcoming")

    if not batch_name or not course_id:
        flash("Batch name and Course selection are required.", "danger")
        return redirect(url_for("admin.batches"))

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO batches (batch_name, course_id, teacher_id, start_date, end_date, start_time, end_time, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (batch_name, course_id, teacher_id, start_date, end_date, start_time, end_time, status))
        flash(f"Batch '{batch_name}' created successfully!", "success")
    except Exception as e:
        flash(f"Error creating batch: {str(e)}", "danger")
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for("admin.batches"))

@admin_bp.route("/batches/<int:batch_id>/edit", methods=["POST"])
@admin_required
def edit_batch(batch_id):
    batch_name = request.form.get("batch_name", "").strip()
    course_id = request.form.get("course_id")
    teacher_id = request.form.get("teacher_id") or None
    start_date = request.form.get("start_date") or None
    end_date = request.form.get("end_date") or None
    start_time = request.form.get("start_time") or None
    end_time = request.form.get("end_time") or None
    status = request.form.get("status", "upcoming")

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            UPDATE batches 
            SET batch_name = %s, course_id = %s, teacher_id = %s,
                start_date = %s, end_date = %s, start_time = %s, end_time = %s, status = %s
            WHERE batch_id = %s
        """, (batch_name, course_id, teacher_id, start_date, end_date, start_time, end_time, status, batch_id))
        flash("Batch updated successfully.", "success")
    except Exception as e:
        flash(f"Error updating batch: {str(e)}", "danger")
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for("admin.batches"))

@admin_bp.route("/batches/<int:batch_id>/delete", methods=["POST"])
@admin_required
def delete_batch(batch_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM batches WHERE batch_id = %s", (batch_id,))
        flash("Batch deleted successfully.", "info")
    except Exception as e:
        flash(f"Could not delete batch: {str(e)}", "danger")
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for("admin.batches"))

# ==================== STUDENTS MANAGEMENT ====================

@admin_bp.route("/students")
@admin_required
def students():
    search_query = request.args.get("search", "").strip()
    batch_filter = request.args.get("batch_id", "").strip()
    conn = get_db_connection()
    cursor = conn.cursor()

    conditions = []
    params = []

    if search_query:
        like_term = f"%{search_query}%"
        conditions.append("(s.full_name LIKE %s OR s.phone LIKE %s OR s.enrollment_number LIKE %s OR s.email LIKE %s)")
        params.extend([like_term, like_term, like_term, like_term])

    if batch_filter and batch_filter.isdigit():
        conditions.append("s.student_id IN (SELECT bs.student_id FROM batch_students bs WHERE bs.batch_id = %s)")
        params.append(int(batch_filter))

    where_sql = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    cursor.execute(f"""
        SELECT s.*,
               (SELECT COUNT(*) FROM batch_students bs WHERE bs.student_id = s.student_id AND bs.status = 'active') AS active_batches_count,
               (SELECT GROUP_CONCAT(b.batch_name SEPARATOR ', ') 
                FROM batch_students bs 
                JOIN batches b ON bs.batch_id = b.batch_id 
                WHERE bs.student_id = s.student_id AND bs.status = 'active') AS enrolled_batches
        FROM students s
        {where_sql}
        ORDER BY s.created_at DESC
    """, tuple(params))
    students_list = cursor.fetchall()

    cursor.execute("SELECT batch_id, batch_name, status FROM batches ORDER BY batch_name ASC")
    all_batches = cursor.fetchall()

    cursor.close()
    conn.close()
    return render_template("admin/students.html",
                           students=students_list,
                           batches=all_batches,
                           search_query=search_query,
                           selected_batch_id=batch_filter)

@admin_bp.route("/batches/<int:batch_id>/students/export-csv")
@admin_required
def export_batch_students_csv(batch_id):
    """Download clean, formatted CSV roster of all students enrolled in a specific batch (Admin)."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT b.*, c.course_name, t.full_name AS teacher_name
        FROM batches b
        JOIN courses c ON b.course_id = c.course_id
        LEFT JOIN teachers t ON b.teacher_id = t.teacher_id
        WHERE b.batch_id = %s
    """, (batch_id,))
    batch = cursor.fetchone()

    if not batch:
        cursor.close()
        conn.close()
        flash("Batch not found.", "danger")
        return redirect(url_for("admin.batches"))

    cursor.execute("SELECT COUNT(*) AS total FROM attendance_sessions WHERE batch_id = %s", (batch_id,))
    total_batch_sessions = cursor.fetchone()["total"] or 0

    cursor.execute("""
        SELECT bs.joined_at, bs.status AS enrollment_status,
               s.enrollment_number, s.full_name, s.email, s.phone, s.status AS student_account_status,
               (SELECT COUNT(*) FROM attendance a 
                JOIN attendance_sessions ses ON a.session_id = ses.session_id 
                WHERE ses.batch_id = %s AND a.student_id = s.student_id AND a.status = 'present') AS present_count
        FROM batch_students bs
        JOIN students s ON bs.student_id = s.student_id
        WHERE bs.batch_id = %s
        ORDER BY s.full_name ASC
    """, (batch_id, batch_id))
    students = cursor.fetchall()
    cursor.close()
    conn.close()

    output = io.StringIO()
    output.write('\ufeff')  # UTF-8 BOM for Microsoft Excel
    writer = csv.writer(output)

    # Metadata Header
    writer.writerow(["STUDENT BATCH ROSTER"])
    writer.writerow(["Batch Name", batch["batch_name"]])
    writer.writerow(["Course", batch["course_name"]])
    writer.writerow(["Assigned Teacher", batch["teacher_name"] or "Unassigned"])
    writer.writerow(["Schedule Timing", f"{batch.get('start_time') or 'TBD'} - {batch.get('end_time') or 'TBD'}"])
    writer.writerow(["Total Classes Held", total_batch_sessions])
    writer.writerow(["Total Students Enrolled", len(students)])
    writer.writerow(["Exported On", datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
    writer.writerow([])

    # Table Header
    writer.writerow([
        "S.No",
        "Enrollment Number",
        "Student Name",
        "Email Address",
        "Contact Number",
        "Enrollment Status",
        "Classes Attended",
        "Total Classes Held",
        "Attendance %",
        "Joined Date"
    ])

    for idx, s in enumerate(students, start=1):
        present = s["present_count"] or 0
        pct = f"{round((present / total_batch_sessions * 100), 1)}%" if total_batch_sessions > 0 else "0.0%"
        joined_str = s["joined_at"].strftime("%Y-%m-%d") if s.get("joined_at") else "N/A"

        writer.writerow([
            idx,
            s["enrollment_number"] or "N/A",
            s["full_name"],
            s["email"],
            s["phone"] or "N/A",
            (s["enrollment_status"] or "active").capitalize(),
            present,
            total_batch_sessions,
            pct,
            joined_str
        ])

    safe_name = "".join(c for c in batch["batch_name"] if c.isalnum() or c in (" ", "-", "_")).strip().replace(" ", "_")
    filename = f"{safe_name}_students_{date.today()}.csv"

    response = Response(output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = f"attachment; filename=\"{filename}\""
    return response

@admin_bp.route("/students/export-csv")
@admin_required
def export_students_csv():
    """Download full or batch-filtered student directory as CSV."""
    batch_filter = request.args.get("batch_id", "").strip()
    if batch_filter and batch_filter.isdigit():
        return export_batch_students_csv(int(batch_filter))

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT s.enrollment_number, s.full_name, s.email, s.phone, s.status, s.created_at,
               (SELECT GROUP_CONCAT(b.batch_name SEPARATOR ', ') 
                FROM batch_students bs 
                JOIN batches b ON bs.batch_id = b.batch_id 
                WHERE bs.student_id = s.student_id AND bs.status = 'active') AS enrolled_batches
        FROM students s
        ORDER BY s.created_at DESC
    """)
    students = cursor.fetchall()
    cursor.close()
    conn.close()

    output = io.StringIO()
    output.write('\ufeff')
    writer = csv.writer(output)
    writer.writerow(["ALL REGISTERED STUDENTS DIRECTORY"])
    writer.writerow(["Total Students", len(students)])
    writer.writerow(["Exported On", datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
    writer.writerow([])
    writer.writerow(["S.No", "Enrollment Number", "Student Name", "Email Address", "Contact Number", "Active Batches", "Status", "Registered On"])

    for idx, s in enumerate(students, start=1):
        reg_date = s["created_at"].strftime("%Y-%m-%d") if s.get("created_at") else "N/A"
        writer.writerow([
            idx,
            s["enrollment_number"] or "N/A",
            s["full_name"],
            s["email"],
            s["phone"] or "N/A",
            s["enrolled_batches"] or "None",
            (s["status"] or "active").capitalize(),
            reg_date
        ])

    filename = f"all_students_{date.today()}.csv"
    response = Response(output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = f"attachment; filename=\"{filename}\""
    return response



@admin_bp.route("/students/add", methods=["POST"])
@admin_required
def add_student():
    full_name = request.form.get("full_name", "").strip()
    email = request.form.get("email", "").strip()
    phone = request.form.get("phone", "").strip()
    enrollment_number = request.form.get("enrollment_number", "").strip()
    password = request.form.get("password", "student123").strip()
    batch_id = request.form.get("batch_id")

    if not full_name or not email:
        flash("Student name and email are required.", "danger")
        return redirect(url_for("admin.students"))

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT student_id FROM students WHERE email = %s", (email,))
        if cursor.fetchone():
            flash("A student with this email already exists.", "warning")
        else:
            if not enrollment_number:
                enrollment_number = generate_next_enrollment_number(cursor)

            cursor.execute("""
                INSERT INTO students (full_name, email, phone, password, enrollment_number, status)
                VALUES (%s, %s, %s, %s, %s, 'active')
            """, (full_name, email, phone, hash_password(password), enrollment_number))
            new_student_id = cursor.lastrowid

            if batch_id:
                cursor.execute("""
                    INSERT INTO batch_students (batch_id, student_id, status)
                    VALUES (%s, %s, 'active')
                """, (batch_id, new_student_id))

            flash(f"Student '{full_name}' added successfully with Enrollment No: {enrollment_number}!", "success")
    except Exception as e:
        flash(f"Error adding student: {str(e)}", "danger")
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for("admin.students"))

@admin_bp.route("/students/<int:student_id>/edit", methods=["POST"])
@admin_required
def edit_student(student_id):
    full_name = request.form.get("full_name", "").strip()
    email = request.form.get("email", "").strip()
    phone = request.form.get("phone", "").strip()
    enrollment_number = request.form.get("enrollment_number", "").strip()
    status = request.form.get("status", "active")
    password = request.form.get("password", "").strip()

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        if password:
            cursor.execute("""
                UPDATE students
                SET full_name = %s, email = %s, phone = %s, enrollment_number = %s, status = %s, password = %s
                WHERE student_id = %s
            """, (full_name, email, phone, enrollment_number or None, status, hash_password(password), student_id))
        else:
            cursor.execute("""
                UPDATE students
                SET full_name = %s, email = %s, phone = %s, enrollment_number = %s, status = %s
                WHERE student_id = %s
            """, (full_name, email, phone, enrollment_number or None, status, student_id))
        flash("Student details updated successfully.", "success")
    except Exception as e:
        flash(f"Error updating student: {str(e)}", "danger")
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for("admin.students"))

@admin_bp.route("/students/<int:student_id>/delete", methods=["POST"])
@admin_required
def delete_student(student_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT full_name FROM students WHERE student_id = %s", (student_id,))
        student = cursor.fetchone()
        if not student:
            flash("Student not found.", "warning")
        else:
            cursor.execute("DELETE FROM students WHERE student_id = %s", (student_id,))
            flash(f"Student '{student['full_name']}' has been deleted successfully.", "success")
    except Exception as e:
        flash(f"Error deleting student: {str(e)}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("admin.students"))

# ==================== ATTENDANCE REPORTS ====================

@admin_bp.route("/reports")
@admin_required
def reports():
    filter_course = request.args.get("course_id", "")
    filter_batch = request.args.get("batch_id", "")
    filter_teacher = request.args.get("teacher_id", "")
    filter_date = request.args.get("date", "")

    conn = get_db_connection()
    cursor = conn.cursor()

    # Dropdown filters data
    cursor.execute("SELECT course_id, course_name FROM courses ORDER BY course_name ASC")
    courses = cursor.fetchall()

    cursor.execute("SELECT batch_id, batch_name FROM batches ORDER BY batch_name ASC")
    batches = cursor.fetchall()

    cursor.execute("SELECT teacher_id, full_name FROM teachers ORDER BY full_name ASC")
    teachers = cursor.fetchall()

    # Batch-wise attendance overview query
    batch_query = """
        SELECT b.batch_id, b.batch_name, c.course_name, t.full_name AS teacher_name,
               (SELECT COUNT(*) FROM attendance_sessions s WHERE s.batch_id = b.batch_id) AS total_sessions,
               (SELECT COUNT(*) FROM batch_students bs WHERE bs.batch_id = b.batch_id AND bs.status = 'active') AS enrolled_students,
               (SELECT COUNT(*) FROM attendance a 
                JOIN attendance_sessions s ON a.session_id = s.session_id 
                WHERE s.batch_id = b.batch_id AND a.status = 'present') AS total_presents
        FROM batches b
        JOIN courses c ON b.course_id = c.course_id
        LEFT JOIN teachers t ON b.teacher_id = t.teacher_id
        WHERE 1=1
    """
    batch_params = []
    if filter_course:
        batch_query += " AND b.course_id = %s"
        batch_params.append(filter_course)
    if filter_batch:
        batch_query += " AND b.batch_id = %s"
        batch_params.append(filter_batch)
    if filter_teacher:
        batch_query += " AND b.teacher_id = %s"
        batch_params.append(filter_teacher)

    cursor.execute(batch_query, tuple(batch_params))
    batch_summaries = cursor.fetchall()

    total_possible_attendances = 0
    total_marked_presents = 0

    for bs in batch_summaries:
        expected = bs["total_sessions"] * bs["enrolled_students"]
        presents = bs["total_presents"]
        absents = max(0, expected - presents)
        rate = round((presents / expected * 100), 2) if expected > 0 else 0.0

        bs["expected"] = expected
        bs["absents"] = absents
        bs["attendance_rate"] = rate

        total_possible_attendances += expected
        total_marked_presents += presents

    total_marked_absents = max(0, total_possible_attendances - total_marked_presents)
    overall_rate = round((total_marked_presents / total_possible_attendances * 100), 2) if total_possible_attendances > 0 else 0.0

    # Detailed session logs
    session_query = """
        SELECT s.session_id, s.session_date, s.started_at, s.status,
               b.batch_name, c.course_name, t.full_name AS teacher_name,
               (SELECT COUNT(*) FROM attendance a WHERE a.session_id = s.session_id AND a.status = 'present') AS present_count,
               (SELECT COUNT(*) FROM batch_students bs WHERE bs.batch_id = s.batch_id AND bs.status = 'active') AS total_students
        FROM attendance_sessions s
        JOIN batches b ON s.batch_id = b.batch_id
        JOIN courses c ON b.course_id = c.course_id
        JOIN teachers t ON s.teacher_id = t.teacher_id
        WHERE 1=1
    """
    session_params = []
    if filter_course:
        session_query += " AND b.course_id = %s"
        session_params.append(filter_course)
    if filter_batch:
        session_query += " AND b.batch_id = %s"
        session_params.append(filter_batch)
    if filter_teacher:
        session_query += " AND s.teacher_id = %s"
        session_params.append(filter_teacher)
    if filter_date:
        session_query += " AND s.session_date = %s"
        session_params.append(filter_date)

    session_query += " ORDER BY s.session_date DESC, s.started_at DESC LIMIT 30"
    cursor.execute(session_query, tuple(session_params))
    recent_sessions = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template("admin/reports.html",
                           courses=courses,
                           batches=batches,
                           teachers=teachers,
                           filter_course=filter_course,
                           filter_batch=filter_batch,
                           filter_teacher=filter_teacher,
                           filter_date=filter_date,
                           batch_summaries=batch_summaries,
                           recent_sessions=recent_sessions,
                           total_possible=total_possible_attendances,
                           total_presents=total_marked_presents,
                           total_absents=total_marked_absents,
                           overall_rate=overall_rate)

@admin_bp.route("/reports/export-csv")
@admin_required
def export_csv():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT s.session_date, b.batch_name, c.course_name, t.full_name AS teacher_name,
               st.full_name AS student_name, st.enrollment_number, a.status, a.marked_at
        FROM attendance a
        JOIN attendance_sessions s ON a.session_id = s.session_id
        JOIN batches b ON s.batch_id = b.batch_id
        JOIN courses c ON b.course_id = c.course_id
        JOIN teachers t ON s.teacher_id = t.teacher_id
        JOIN students st ON a.student_id = st.student_id
        ORDER BY s.session_date DESC, a.marked_at DESC
    """)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Date", "Batch", "Course", "Teacher", "Student Name", "Enrollment No", "Status", "Marked At"])

    for r in rows:
        writer.writerow([
            r["session_date"],
            r["batch_name"],
            r["course_name"],
            r["teacher_name"],
            r["student_name"],
            r["enrollment_number"] or "N/A",
            r["status"].upper(),
            r["marked_at"]
        ])

    response = Response(output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = f"attachment; filename=attendance_report_{date.today()}.csv"
    return response


# ==================== FEES & EMI MANAGEMENT ====================

@admin_bp.route("/fees")
@admin_required
def fees():
    ensure_fees_tables()
    search_query = request.args.get("search", "").strip()
    status_filter = request.args.get("status", "").strip()
    plan_filter = request.args.get("plan", "").strip()

    summary = get_fees_summary()
    fee_list = get_all_fees(search_query=search_query, status_filter=status_filter, plan_filter=plan_filter)

    conn = get_db_connection()
    cursor = conn.cursor()

    # Dropdowns for Assign Fee Modal
    cursor.execute("""
        SELECT student_id, full_name, enrollment_number, email 
        FROM students 
        WHERE status = 'active' 
        ORDER BY full_name ASC
    """)
    students = cursor.fetchall()

    cursor.execute("SELECT course_id, course_name FROM courses ORDER BY course_name ASC")
    courses = cursor.fetchall()

    cursor.execute("""
        SELECT b.batch_id, b.batch_name, c.course_name 
        FROM batches b 
        JOIN courses c ON b.course_id = c.course_id 
        WHERE b.status IN ('upcoming', 'active') 
        ORDER BY b.batch_name ASC
    """)
    batches = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template("admin/fees.html",
                           summary=summary,
                           fees=fee_list,
                           students=students,
                           courses=courses,
                           batches=batches,
                           search_query=search_query,
                           status_filter=status_filter,
                           plan_filter=plan_filter)


@admin_bp.route("/fees/assign", methods=["POST"])
@admin_required
def assign_fee():
    student_id = request.form.get("student_id", type=int)
    course_id = request.form.get("course_id", type=int)
    batch_id = request.form.get("batch_id", type=int)
    title = request.form.get("title", "").strip()
    total_amount = request.form.get("total_amount", type=float)
    discount_amount = request.form.get("discount_amount", 0.0, type=float)
    payment_plan = request.form.get("payment_plan", "one_time").strip()
    total_installments = request.form.get("total_installments", 1, type=int)
    first_due_date = request.form.get("first_due_date", "").strip()
    notes = request.form.get("notes", "").strip()

    # Optional initial upfront payment
    initial_payment = request.form.get("initial_payment", 0.0, type=float)
    initial_payment_mode = request.form.get("initial_payment_mode", "upi")
    initial_ref = request.form.get("initial_ref", "").strip()

    if not student_id or not title or not total_amount or total_amount <= 0:
        flash("Please select a student and provide a valid fee title and amount.", "danger")
        return redirect(url_for("admin.fees"))

    try:
        fee_id = create_student_fee(
            student_id=student_id,
            course_id=course_id,
            batch_id=batch_id,
            title=title,
            total_amount=total_amount,
            discount_amount=discount_amount,
            payment_plan=payment_plan,
            total_installments=total_installments,
            first_due_date=first_due_date or None,
            notes=notes
        )

        if initial_payment and initial_payment > 0:
            record_fee_payment(
                fee_id=fee_id,
                amount_paid=initial_payment,
                payment_date=date.today(),
                payment_mode=initial_payment_mode,
                transaction_reference=initial_ref,
                remarks="Initial down-payment recorded at fee assignment",
                recorded_by=session.get("user_name", "Admin")
            )
            flash("Fee assigned and initial payment recorded successfully!", "success")
        else:
            flash("Fee assigned successfully!", "success")

        return redirect(url_for("admin.fee_details", fee_id=fee_id))

    except Exception as e:
        flash(f"Error assigning fee: {str(e)}", "danger")
        return redirect(url_for("admin.fees"))


@admin_bp.route("/fees/<int:fee_id>")
@admin_required
def fee_details(fee_id):
    data = get_fee_details(fee_id)
    if not data:
        flash("Fee record not found.", "danger")
        return redirect(url_for("admin.fees"))

    return render_template("admin/fee_details.html",
                           fee=data["fee"],
                           installments=data["installments"],
                           payments=data["payments"])


@admin_bp.route("/fees/<int:fee_id>/payments/add", methods=["POST"])
@admin_required
def add_fee_payment(fee_id):
    amount_paid = request.form.get("amount_paid", type=float)
    payment_date = request.form.get("payment_date", "").strip()
    payment_mode = request.form.get("payment_mode", "upi").strip()
    transaction_reference = request.form.get("transaction_reference", "").strip()
    remarks = request.form.get("remarks", "").strip()
    target_installment_id = request.form.get("target_installment_id", type=int)

    if not amount_paid or amount_paid <= 0:
        flash("Please enter a valid payment amount greater than zero.", "danger")
        return redirect(url_for("admin.fee_details", fee_id=fee_id))

    try:
        res = record_fee_payment(
            fee_id=fee_id,
            amount_paid=amount_paid,
            payment_date=payment_date or None,
            payment_mode=payment_mode,
            transaction_reference=transaction_reference,
            remarks=remarks,
            recorded_by=session.get("user_name", "Admin"),
            target_installment_id=target_installment_id if target_installment_id else None
        )

        flash(f"Payment of ₹{float(res['amount_paid']):,.2f} recorded! Receipt: {res['receipt_number']}. Remaining: ₹{float(res['remaining_fee']):,.2f}", "success")
        return redirect(url_for("admin.fee_details", fee_id=fee_id))

    except Exception as e:
        flash(f"Payment failed: {str(e)}", "danger")
        return redirect(url_for("admin.fee_details", fee_id=fee_id))


@admin_bp.route("/fees/receipt/<string:receipt_number>")
@admin_required
def view_receipt(receipt_number):
    receipt = get_payment_receipt(receipt_number)
    if not receipt:
        flash("Receipt not found.", "danger")
        return redirect(url_for("admin.fees"))

    return render_template("admin/receipt.html", receipt=receipt)


@admin_bp.route("/fees/<int:fee_id>/delete", methods=["POST"])
@admin_required
def delete_fee(fee_id):
    try:
        delete_student_fee(fee_id)
        flash("Fee record and payment history deleted successfully.", "info")
    except Exception as e:
        flash(f"Failed to delete fee: {str(e)}", "danger")

    return redirect(url_for("admin.fees"))

