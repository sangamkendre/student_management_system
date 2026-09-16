import csv
import io
import secrets
from datetime import datetime, timedelta, date
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify, Response

from utils.db import get_db_connection
from utils.auth_helpers import teacher_required, hash_password, generate_next_enrollment_number, verify_password
from utils.qr_generator import generate_qr_base64

teacher_bp = Blueprint("teacher", __name__, url_prefix="/teacher")

def get_teacher_id():
    return session.get("user_id")

@teacher_bp.route("/dashboard")
@teacher_required
def dashboard():
    teacher_id = get_teacher_id()
    conn = get_db_connection()
    cursor = conn.cursor()

    # Teacher Profile Info
    cursor.execute("SELECT * FROM teachers WHERE teacher_id = %s", (teacher_id,))
    teacher = cursor.fetchone()

    # Assigned Batches with course name & student counts
    cursor.execute("""
        SELECT b.*, c.course_name, c.duration,
               (SELECT COUNT(*) FROM batch_students bs WHERE bs.batch_id = b.batch_id AND bs.status IN ('active', 'completed')) AS student_count
        FROM batches b
        JOIN courses c ON b.course_id = c.course_id
        WHERE b.teacher_id = %s
        ORDER BY FIELD(b.status, 'active', 'upcoming', 'completed'), b.start_date DESC
    """, (teacher_id,))
    batches = cursor.fetchall()

    # Total unique students across assigned batches
    cursor.execute("""
        SELECT COUNT(DISTINCT bs.student_id) AS total_students
        FROM batch_students bs
        JOIN batches b ON bs.batch_id = b.batch_id
        WHERE b.teacher_id = %s AND bs.status IN ('active', 'completed')
    """, (teacher_id,))
    total_students = cursor.fetchone()["total_students"] or 0

    # Total attendance sessions conducted
    cursor.execute("""
        SELECT COUNT(*) AS total_sessions
        FROM attendance_sessions
        WHERE teacher_id = %s
    """, (teacher_id,))
    total_sessions = cursor.fetchone()["total_sessions"] or 0

    # Recent attendance sessions
    cursor.execute("""
        SELECT s.*, b.batch_name, c.course_name,
               (SELECT COUNT(*) FROM attendance a WHERE a.session_id = s.session_id) AS attendee_count
        FROM attendance_sessions s
        JOIN batches b ON s.batch_id = b.batch_id
        JOIN courses c ON b.course_id = c.course_id
        WHERE s.teacher_id = %s
        ORDER BY s.started_at DESC
        LIMIT 5
    """, (teacher_id,))
    recent_sessions = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template("teacher/dashboard.html",
                           teacher=teacher,
                           batches=batches,
                           total_batches=len(batches),
                           total_students=total_students,
                           total_sessions=total_sessions,
                           recent_sessions=recent_sessions)

@teacher_bp.route("/batches")
@teacher_required
def batches():
    teacher_id = get_teacher_id()
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT b.*, c.course_name, c.duration,
               (SELECT COUNT(*) FROM batch_students bs WHERE bs.batch_id = b.batch_id AND bs.status IN ('active', 'completed')) AS student_count,
               (SELECT COUNT(*) FROM attendance_sessions s WHERE s.batch_id = b.batch_id) AS session_count,
               (SELECT s.session_id FROM attendance_sessions s WHERE s.batch_id = b.batch_id AND s.session_date = CURDATE() ORDER BY s.started_at DESC LIMIT 1) AS today_session_id,
               (SELECT s.status FROM attendance_sessions s WHERE s.batch_id = b.batch_id AND s.session_date = CURDATE() ORDER BY s.started_at DESC LIMIT 1) AS today_session_status,
               (SELECT COUNT(*) FROM attendance a WHERE a.session_id = (SELECT s.session_id FROM attendance_sessions s WHERE s.batch_id = b.batch_id AND s.session_date = CURDATE() ORDER BY s.started_at DESC LIMIT 1) AND a.status = 'present') AS today_attendee_count
        FROM batches b
        JOIN courses c ON b.course_id = c.course_id
        WHERE b.teacher_id = %s
        ORDER BY FIELD(b.status, 'active', 'upcoming', 'completed'), b.start_date DESC
    """, (teacher_id,))
    batches = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template("teacher/batches.html", batches=batches)

@teacher_bp.route("/students")
@teacher_required
def students():
    teacher_id = get_teacher_id()
    search_query = request.args.get("search", "").strip()
    conn = get_db_connection()
    cursor = conn.cursor()

    if search_query:
        like_term = f"%{search_query}%"
        cursor.execute("""
            SELECT s.*,
                   (SELECT GROUP_CONCAT(b.batch_name SEPARATOR ', ')
                    FROM batch_students bs
                    JOIN batches b ON bs.batch_id = b.batch_id
                    WHERE bs.student_id = s.student_id AND b.teacher_id = %s AND bs.status IN ('active', 'completed')) AS my_batches,
                   (SELECT b.batch_id
                    FROM batch_students bs
                    JOIN batches b ON bs.batch_id = b.batch_id
                    WHERE bs.student_id = s.student_id AND b.teacher_id = %s AND bs.status IN ('active', 'completed')
                    LIMIT 1) AS primary_batch_id,
                   (SELECT GROUP_CONCAT(b2.batch_name SEPARATOR ', ')
                    FROM batch_students bs2
                    JOIN batches b2 ON bs2.batch_id = b2.batch_id
                    WHERE bs2.student_id = s.student_id AND bs2.status IN ('active', 'completed')) AS all_enrolled_batches
            FROM students s
            WHERE s.full_name LIKE %s OR s.phone LIKE %s OR s.enrollment_number LIKE %s OR s.email LIKE %s
            ORDER BY (my_batches IS NOT NULL) DESC, s.full_name ASC
        """, (teacher_id, teacher_id, like_term, like_term, like_term, like_term))
    else:
        cursor.execute("""
            SELECT s.*,
                   (SELECT GROUP_CONCAT(b.batch_name SEPARATOR ', ')
                    FROM batch_students bs
                    JOIN batches b ON bs.batch_id = b.batch_id
                    WHERE bs.student_id = s.student_id AND b.teacher_id = %s AND bs.status IN ('active', 'completed')) AS my_batches,
                   (SELECT b.batch_id
                    FROM batch_students bs
                    JOIN batches b ON bs.batch_id = b.batch_id
                    WHERE bs.student_id = s.student_id AND b.teacher_id = %s AND bs.status IN ('active', 'completed')
                    LIMIT 1) AS primary_batch_id,
                   (SELECT GROUP_CONCAT(b2.batch_name SEPARATOR ', ')
                    FROM batch_students bs2
                    JOIN batches b2 ON bs2.batch_id = b2.batch_id
                    WHERE bs2.student_id = s.student_id AND bs2.status IN ('active', 'completed')) AS all_enrolled_batches
            FROM students s
            ORDER BY (my_batches IS NOT NULL) DESC, s.full_name ASC
        """, (teacher_id, teacher_id))
    students_list = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template("teacher/students.html", students=students_list, search_query=search_query)

@teacher_bp.route("/batches/<int:batch_id>/students")
@teacher_required
def batch_students(batch_id):
    teacher_id = get_teacher_id()
    search_query = request.args.get("search", "").strip()
    conn = get_db_connection()
    cursor = conn.cursor()

    # Verify ownership
    cursor.execute("""
        SELECT b.*, c.course_name
        FROM batches b
        JOIN courses c ON b.course_id = c.course_id
        WHERE b.batch_id = %s AND b.teacher_id = %s
    """, (batch_id, teacher_id))
    batch = cursor.fetchone()

    if not batch:
        cursor.close()
        conn.close()
        flash("Batch not found or you do not have permission to view it.", "danger")
        return redirect(url_for("teacher.batches"))

    # Total sessions for this batch
    cursor.execute("SELECT COUNT(*) AS total FROM attendance_sessions WHERE batch_id = %s", (batch_id,))
    total_batch_sessions = cursor.fetchone()["total"] or 0

    # Students in this batch with attendance metrics (supports search)
    if search_query:
        like_term = f"%{search_query}%"
        cursor.execute("""
            SELECT bs.batch_student_id, bs.joined_at, bs.status AS enrollment_status,
                   s.student_id, s.full_name, s.email, s.phone, s.enrollment_number,
                   (SELECT COUNT(*) FROM attendance a 
                    JOIN attendance_sessions ses ON a.session_id = ses.session_id 
                    WHERE ses.batch_id = %s AND a.student_id = s.student_id AND a.status = 'present') AS present_count
            FROM batch_students bs
            JOIN students s ON bs.student_id = s.student_id
            WHERE bs.batch_id = %s AND (s.full_name LIKE %s OR s.phone LIKE %s OR s.enrollment_number LIKE %s OR s.email LIKE %s)
            ORDER BY s.full_name ASC
        """, (batch_id, batch_id, like_term, like_term, like_term, like_term))
    else:
        cursor.execute("""
            SELECT bs.batch_student_id, bs.joined_at, bs.status AS enrollment_status,
                   s.student_id, s.full_name, s.email, s.phone, s.enrollment_number,
                   (SELECT COUNT(*) FROM attendance a 
                    JOIN attendance_sessions ses ON a.session_id = ses.session_id 
                    WHERE ses.batch_id = %s AND a.student_id = s.student_id AND a.status = 'present') AS present_count
            FROM batch_students bs
            JOIN students s ON bs.student_id = s.student_id
            WHERE bs.batch_id = %s
            ORDER BY s.full_name ASC
        """, (batch_id, batch_id))
    students = cursor.fetchall()

    for s in students:
        present = s["present_count"]
        absent = max(0, total_batch_sessions - present)
        s["absent_count"] = absent
        s["attendance_pct"] = round((present / total_batch_sessions * 100), 1) if total_batch_sessions > 0 else 0.0

    # Other students in database not in this batch for easy enrollment
    cursor.execute("""
        SELECT s.student_id, s.full_name, s.enrollment_number, s.email, s.phone
        FROM students s
        WHERE s.status = 'active'
          AND s.student_id NOT IN (
              SELECT student_id FROM batch_students WHERE batch_id = %s
          )
        ORDER BY s.full_name ASC
    """, (batch_id,))
    available_students = cursor.fetchall()

    # Generate next suggested enrollment number for the enrollment modal
    next_enrollment_no = generate_next_enrollment_number(cursor)

    cursor.close()
    conn.close()

    return render_template("teacher/batch_students.html",
                           batch=batch,
                           students=students,
                           available_students=available_students,
                           total_batch_sessions=total_batch_sessions,
                           next_enrollment_no=next_enrollment_no,
                           search_query=search_query)

@teacher_bp.route("/batches/<int:batch_id>/students/export-csv")
@teacher_required
def export_batch_students_csv(batch_id):
    """Download clean, formatted CSV roster of all students enrolled in this batch."""
    teacher_id = get_teacher_id()
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT b.*, c.course_name, t.full_name AS teacher_name
        FROM batches b
        JOIN courses c ON b.course_id = c.course_id
        LEFT JOIN teachers t ON b.teacher_id = t.teacher_id
        WHERE b.batch_id = %s AND b.teacher_id = %s
    """, (batch_id, teacher_id))
    batch = cursor.fetchone()

    if not batch:
        cursor.close()
        conn.close()
        flash("Batch not found or you do not have permission to view it.", "danger")
        return redirect(url_for("teacher.batches"))

    cursor.execute("SELECT COUNT(*) AS total FROM attendance_sessions WHERE batch_id = %s", (batch_id,))
    total_batch_sessions = cursor.fetchone()["total"] or 0

    cursor.execute("""
        SELECT bs.joined_at, bs.status AS enrollment_status,
               s.enrollment_number, s.full_name, s.email, s.phone,
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
    writer.writerow(["Teacher", batch["teacher_name"] or "Unassigned"])
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


@teacher_bp.route("/api/generate-enrollment-number", methods=["GET"])
@teacher_required
def api_generate_enrollment_number():
    """API endpoint to dynamically generate a fresh unique enrollment number."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        enrollment_no = generate_next_enrollment_number(cursor)
        return jsonify({"success": True, "enrollment_number": enrollment_no})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

@teacher_bp.route("/batches/<int:batch_id>/students/add", methods=["POST"])
@teacher_required
def add_student_to_batch(batch_id):
    teacher_id = get_teacher_id()
    conn = get_db_connection()
    cursor = conn.cursor()

    # Verify batch ownership
    cursor.execute("SELECT batch_id FROM batches WHERE batch_id = %s AND teacher_id = %s", (batch_id, teacher_id))
    if not cursor.fetchone():
        cursor.close()
        conn.close()
        flash("Batch not found or unauthorized.", "danger")
        return redirect(url_for("teacher.batches"))

    enroll_type = request.form.get("enroll_type", "existing")

    if enroll_type == "existing":
        student_id = request.form.get("student_id")
        if not student_id:
            flash("Please select a student to enroll.", "warning")
            cursor.close()
            conn.close()
            return redirect(url_for("teacher.batch_students", batch_id=batch_id))

        try:
            # Check student details and ensure they have an enrollment number
            cursor.execute("SELECT student_id, full_name, enrollment_number FROM students WHERE student_id = %s", (student_id,))
            student_info = cursor.fetchone()
            assigned_eno = None
            if student_info and not student_info.get("enrollment_number"):
                assigned_eno = generate_next_enrollment_number(cursor)
                cursor.execute("UPDATE students SET enrollment_number = %s WHERE student_id = %s", (assigned_eno, student_id))

            cursor.execute("""
                INSERT INTO batch_students (batch_id, student_id, status)
                VALUES (%s, %s, 'active')
            """, (batch_id, student_id))
            
            student_name = student_info["full_name"] if student_info else "Student"
            if assigned_eno:
                flash(f"Student '{student_name}' enrolled in batch and assigned Enrollment No: {assigned_eno}!", "success")
            else:
                flash(f"Student '{student_name}' added to batch successfully!", "success")
        except Exception as e:
            flash(f"Could not enroll student: {str(e)}", "danger")

    elif enroll_type == "new":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()
        enrollment_number = request.form.get("enrollment_number", "").strip()
        password = request.form.get("password", "student123")

        if not full_name or not email:
            flash("Student name and email are required.", "danger")
            cursor.close()
            conn.close()
            return redirect(url_for("teacher.batch_students", batch_id=batch_id))

        try:
            # Auto-generate enrollment number if left blank
            if not enrollment_number:
                enrollment_number = generate_next_enrollment_number(cursor)

            # Check if email exists
            cursor.execute("SELECT student_id, enrollment_number FROM students WHERE email = %s", (email,))
            existing = cursor.fetchone()
            if existing:
                student_id = existing["student_id"]
                # If existing record has no enrollment number, assign the auto-generated one
                if not existing.get("enrollment_number"):
                    cursor.execute("UPDATE students SET enrollment_number = %s WHERE student_id = %s", (enrollment_number, student_id))
            else:
                cursor.execute("""
                    INSERT INTO students (full_name, email, phone, password, enrollment_number, status)
                    VALUES (%s, %s, %s, %s, %s, 'active')
                """, (full_name, email, phone, hash_password(password), enrollment_number))
                student_id = cursor.lastrowid

            # Enroll in batch
            cursor.execute("""
                INSERT INTO batch_students (batch_id, student_id, status)
                VALUES (%s, %s, 'active')
                ON DUPLICATE KEY UPDATE status = 'active'
            """, (batch_id, student_id))
            flash(f"Student '{full_name}' registered with Enrollment No: {enrollment_number} and enrolled in batch!", "success")
        except Exception as e:
            flash(f"Error registering student: {str(e)}", "danger")

    cursor.close()
    conn.close()
    return redirect(url_for("teacher.batch_students", batch_id=batch_id))

@teacher_bp.route("/batches/<int:batch_id>/students/<int:student_id>/remove", methods=["POST"])
@teacher_required
def remove_student_from_batch(batch_id, student_id):
    teacher_id = get_teacher_id()
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT batch_id FROM batches WHERE batch_id = %s AND teacher_id = %s", (batch_id, teacher_id))
    if not cursor.fetchone():
        cursor.close()
        conn.close()
        flash("Unauthorized batch action.", "danger")
        return redirect(url_for("teacher.batches"))

    cursor.execute("DELETE FROM batch_students WHERE batch_id = %s AND student_id = %s", (batch_id, student_id))
    cursor.close()
    conn.close()

    flash("Student removed from batch.", "info")
    return redirect(url_for("teacher.batch_students", batch_id=batch_id))

@teacher_bp.route("/batches/<int:batch_id>/complete", methods=["POST"])
@teacher_required
def complete_batch(batch_id):
    teacher_id = get_teacher_id()
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT batch_id, batch_name, status FROM batches WHERE batch_id = %s AND teacher_id = %s", (batch_id, teacher_id))
    batch = cursor.fetchone()
    if not batch:
        cursor.close()
        conn.close()
        flash("Batch not found or unauthorized.", "danger")
        return redirect(url_for("teacher.batches"))

    # Update batch status to completed, set end_date if null, and mark batch_students as completed
    cursor.execute("""
        UPDATE batches 
        SET status = 'completed',
            end_date = COALESCE(end_date, CURDATE())
        WHERE batch_id = %s
    """, (batch_id,))

    cursor.execute("""
        UPDATE batch_students 
        SET status = 'completed' 
        WHERE batch_id = %s AND status = 'active'
    """, (batch_id,))

    # Close any open attendance sessions for this batch
    cursor.execute("""
        UPDATE attendance_sessions 
        SET status = 'closed' 
        WHERE batch_id = %s AND status = 'active'
    """, (batch_id,))

    cursor.close()
    conn.close()

    flash(f"Batch '{batch['batch_name']}' has been marked as COMPLETED! Students and faculty will see this reflected.", "success")
    next_page = request.form.get("next")
    if next_page == "batch_students":
        return redirect(url_for("teacher.batch_students", batch_id=batch_id))
    return redirect(url_for("teacher.batches"))

@teacher_bp.route("/batches/<int:batch_id>/reopen", methods=["POST"])
@teacher_required
def reopen_batch(batch_id):
    teacher_id = get_teacher_id()
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT batch_id, batch_name, status FROM batches WHERE batch_id = %s AND teacher_id = %s", (batch_id, teacher_id))
    batch = cursor.fetchone()
    if not batch:
        cursor.close()
        conn.close()
        flash("Batch not found or unauthorized.", "danger")
        return redirect(url_for("teacher.batches"))

    cursor.execute("UPDATE batches SET status = 'active' WHERE batch_id = %s", (batch_id,))
    cursor.execute("UPDATE batch_students SET status = 'active' WHERE batch_id = %s AND status = 'completed'", (batch_id,))

    cursor.close()
    conn.close()

    flash(f"Batch '{batch['batch_name']}' has been reopened and set to ACTIVE.", "info")
    next_page = request.form.get("next")
    if next_page == "batch_students":
        return redirect(url_for("teacher.batch_students", batch_id=batch_id))
    return redirect(url_for("teacher.batches"))

@teacher_bp.route("/batches/<int:batch_id>/attendance/start", methods=["POST"])
@teacher_required
def start_attendance(batch_id):
    teacher_id = get_teacher_id()
    duration_mins = int(request.form.get("duration", 5))
    mode = request.form.get("mode", "auto")  # 'auto', 'resume', 'new'

    conn = get_db_connection()
    cursor = conn.cursor()

    # Verify batch ownership
    cursor.execute("SELECT batch_id, batch_name, status FROM batches WHERE batch_id = %s AND teacher_id = %s", (batch_id, teacher_id))
    batch = cursor.fetchone()
    if not batch:
        cursor.close()
        conn.close()
        flash("Batch not found or unauthorized.", "danger")
        return redirect(url_for("teacher.batches"))

    if batch["status"] == "completed":
        cursor.close()
        conn.close()
        flash("Cannot launch an attendance session for a completed batch. Please reopen the batch first if needed.", "warning")
        return redirect(url_for("teacher.batches"))

    now = datetime.now()
    today = now.date()

    # Check for existing sessions today for this batch
    cursor.execute("""
        SELECT s.session_id, s.status, s.expires_at,
               (SELECT COUNT(*) FROM attendance a WHERE a.session_id = s.session_id) AS attendee_count
        FROM attendance_sessions s
        WHERE s.batch_id = %s AND s.session_date = %s
        ORDER BY s.started_at DESC
    """, (batch_id, today))
    today_sessions = cursor.fetchall()

    # If teacher chose 'resume' OR if 'auto' and an active session already exists today
    if mode != "new" and today_sessions:
        active_today = next((s for s in today_sessions if s["status"] == "active" and s["expires_at"] > now), None)
        if active_today:
            cursor.close()
            conn.close()
            flash(f"Resumed today's active attendance session for {batch['batch_name']}.", "info")
            return redirect(url_for("teacher.session_view", session_id=active_today["session_id"]))

        # If the most recent session today has 0 attendees, it was created by accident or unused!
        # Automatically reactivate/reopen this empty session rather than polluting the database with duplicate sessions.
        latest_today = today_sessions[0]
        if latest_today["attendee_count"] == 0 or mode == "resume":
            qr_token = secrets.token_urlsafe(20)
            expires_at = now + timedelta(minutes=duration_mins)
            cursor.execute("""
                UPDATE attendance_sessions
                SET status = 'active',
                    started_at = %s,
                    expires_at = %s,
                    qr_token = %s
                WHERE session_id = %s
            """, (now, expires_at, qr_token, latest_today["session_id"]))
            cursor.close()
            conn.close()
            if latest_today["attendee_count"] > 0:
                flash(f"Reopened today's attendance session ({latest_today['attendee_count']} attendees already marked). Timer extended by {duration_mins} mins.", "success")
            else:
                flash(f"Attendance session launched for {batch['batch_name']}! Display QR code below.", "success")
            return redirect(url_for("teacher.session_view", session_id=latest_today["session_id"]))

    # Generate token & expiry for new session
    qr_token = secrets.token_urlsafe(20)
    expires_at = now + timedelta(minutes=duration_mins)

    # Expire any previous active sessions for this batch
    cursor.execute("""
        UPDATE attendance_sessions 
        SET status = 'closed' 
        WHERE batch_id = %s AND status = 'active'
    """, (batch_id,))

    # Create new session
    cursor.execute("""
        INSERT INTO attendance_sessions (batch_id, teacher_id, session_date, started_at, expires_at, qr_token, status)
        VALUES (%s, %s, %s, %s, %s, %s, 'active')
    """, (batch_id, teacher_id, today, now, expires_at, qr_token))
    session_id = cursor.lastrowid

    cursor.close()
    conn.close()

    flash(f"New attendance session started for {batch['batch_name']}! Display the QR code below.", "success")
    return redirect(url_for("teacher.session_view", session_id=session_id))

@teacher_bp.route("/attendance/session/<int:session_id>")
@teacher_required
def session_view(session_id):
    teacher_id = get_teacher_id()
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT s.*, b.batch_name, c.course_name,
               TIMESTAMPDIFF(SECOND, NOW(), s.expires_at) AS seconds_left
        FROM attendance_sessions s
        JOIN batches b ON s.batch_id = b.batch_id
        JOIN courses c ON b.course_id = c.course_id
        WHERE s.session_id = %s AND s.teacher_id = %s
    """, (session_id, teacher_id))
    session_data = cursor.fetchone()

    if not session_data:
        cursor.close()
        conn.close()
        flash("Attendance session not found or unauthorized.", "danger")
        return redirect(url_for("teacher.batches"))

    # Construct QR scan URL
    qr_url = request.host_url.rstrip("/") + url_for("attendance.mark_attendance", token=session_data["qr_token"])
    qr_code_b64 = generate_qr_base64(qr_url)

    # Students list in batch with marked attendance status
    cursor.execute("""
        SELECT s.student_id, s.full_name, s.enrollment_number,
               a.marked_at, a.status AS att_status
        FROM batch_students bs
        JOIN students s ON bs.student_id = s.student_id
        LEFT JOIN attendance a ON a.session_id = %s AND a.student_id = s.student_id
        WHERE bs.batch_id = %s AND bs.status = 'active'
        ORDER BY (a.marked_at IS NOT NULL) DESC, s.full_name ASC
    """, (session_id, session_data["batch_id"]))
    roster = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template("teacher/attendance_session.html",
                           session=session_data,
                           qr_code_b64=qr_code_b64,
                           qr_url=qr_url,
                           roster=roster)

@teacher_bp.route("/attendance/session/<int:session_id>/close", methods=["POST"])
@teacher_required
def close_session(session_id):
    teacher_id = get_teacher_id()
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE attendance_sessions
        SET status = 'closed'
        WHERE session_id = %s AND teacher_id = %s
    """, (session_id, teacher_id))

    cursor.execute("SELECT batch_id FROM attendance_sessions WHERE session_id = %s", (session_id,))
    sess = cursor.fetchone()

    cursor.close()
    conn.close()

    flash("Attendance session closed.", "info")
    if sess:
        return redirect(url_for("teacher.batch_reports", batch_id=sess["batch_id"]))
    return redirect(url_for("teacher.batches"))

@teacher_bp.route("/attendance/session/<int:session_id>/extend", methods=["POST"])
@teacher_required
def extend_session(session_id):
    """Extend the timer of an attendance session or reactivate an expired session."""
    teacher_id = get_teacher_id()
    extra_mins = int(request.form.get("minutes", 5))

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT session_id, batch_id, expires_at, status FROM attendance_sessions WHERE session_id = %s AND teacher_id = %s", (session_id, teacher_id))
    session_data = cursor.fetchone()
    if not session_data:
        cursor.close()
        conn.close()
        flash("Session not found or unauthorized.", "danger")
        return redirect(url_for("teacher.batches"))

    now = datetime.now()
    base_time = max(now, session_data["expires_at"] or now)
    new_expires_at = base_time + timedelta(minutes=extra_mins)
    new_token = secrets.token_urlsafe(20)

    cursor.execute("""
        UPDATE attendance_sessions
        SET expires_at = %s,
            status = 'active',
            qr_token = %s
        WHERE session_id = %s
    """, (new_expires_at, new_token, session_id))

    cursor.close()
    conn.close()

    flash(f"Session extended by {extra_mins} minutes! New QR code generated.", "success")
    return redirect(url_for("teacher.session_view", session_id=session_id))

@teacher_bp.route("/attendance/session/<int:session_id>/delete", methods=["POST"])
@teacher_required
def delete_session(session_id):
    """Safely delete an accidental or unwanted attendance session and its records."""
    teacher_id = get_teacher_id()
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT s.session_id, s.batch_id, s.session_date, b.batch_name
        FROM attendance_sessions s
        JOIN batches b ON s.batch_id = b.batch_id
        WHERE s.session_id = %s AND s.teacher_id = %s
    """, (session_id, teacher_id))
    sess = cursor.fetchone()

    if not sess:
        cursor.close()
        conn.close()
        flash("Session not found or unauthorized.", "danger")
        return redirect(url_for("teacher.batches"))

    batch_id = sess["batch_id"]
    batch_name = sess["batch_name"]
    date_str = sess["session_date"].strftime("%d %b %Y") if sess["session_date"] else "Session"

    # Delete attendance records
    cursor.execute("DELETE FROM attendance WHERE session_id = %s", (session_id,))
    # Delete session
    cursor.execute("DELETE FROM attendance_sessions WHERE session_id = %s", (session_id,))

    cursor.close()
    conn.close()

    flash(f"Attendance session ({date_str}) for '{batch_name}' has been deleted successfully.", "info")

    next_url = request.form.get("next") or request.referrer
    if next_url and ("reports" in next_url or "attendance-sheet" in next_url or "batches" in next_url):
        return redirect(next_url)
    return redirect(url_for("teacher.batch_reports", batch_id=batch_id))

@teacher_bp.route("/attendance/session/<int:session_id>/manual-mark", methods=["POST"])
@teacher_required
def manual_mark(session_id):
    teacher_id = get_teacher_id()
    student_id = request.form.get("student_id")
    action = request.form.get("action", "present")

    conn = get_db_connection()
    cursor = conn.cursor()

    # Verify session
    cursor.execute("SELECT session_id, batch_id FROM attendance_sessions WHERE session_id = %s AND teacher_id = %s", (session_id, teacher_id))
    if not cursor.fetchone():
        cursor.close()
        conn.close()
        flash("Unauthorized.", "danger")
        return redirect(url_for("teacher.batches"))

    if action == "present":
        cursor.execute("""
            INSERT INTO attendance (session_id, student_id, status, marked_at)
            VALUES (%s, %s, 'present', NOW())
            ON DUPLICATE KEY UPDATE status = 'present', marked_at = NOW()
        """, (session_id, student_id))
        flash("Student marked Present manually.", "success")
    elif action == "remove":
        cursor.execute("DELETE FROM attendance WHERE session_id = %s AND student_id = %s", (session_id, student_id))
        flash("Attendance record removed for student.", "info")

    cursor.close()
    conn.close()
    return redirect(url_for("teacher.session_view", session_id=session_id))

@teacher_bp.route("/batches/<int:batch_id>/reports")
@teacher_required
def batch_reports(batch_id):
    teacher_id = get_teacher_id()
    conn = get_db_connection()
    cursor = conn.cursor()

    # Verify ownership
    cursor.execute("""
        SELECT b.*, c.course_name
        FROM batches b
        JOIN courses c ON b.course_id = c.course_id
        WHERE b.batch_id = %s AND b.teacher_id = %s
    """, (batch_id, teacher_id))
    batch = cursor.fetchone()

    if not batch:
        cursor.close()
        conn.close()
        flash("Batch not found.", "danger")
        return redirect(url_for("teacher.batches"))

    # Total sessions held for this batch
    cursor.execute("""
        SELECT session_id, session_date, started_at, status,
               (SELECT COUNT(*) FROM attendance a WHERE a.session_id = s.session_id) AS present_count
        FROM attendance_sessions s
        WHERE s.batch_id = %s
        ORDER BY s.started_at DESC
    """, (batch_id,))
    sessions = cursor.fetchall()
    total_sessions = len(sessions)

    # Student-wise attendance
    cursor.execute("""
        SELECT s.student_id, s.full_name, s.enrollment_number, s.email,
               (SELECT COUNT(*) FROM attendance a 
                JOIN attendance_sessions ses ON a.session_id = ses.session_id 
                WHERE ses.batch_id = %s AND a.student_id = s.student_id AND a.status = 'present') AS present_count
        FROM batch_students bs
        JOIN students s ON bs.student_id = s.student_id
        WHERE bs.batch_id = %s AND bs.status = 'active'
        ORDER BY s.full_name ASC
    """, (batch_id, batch_id))
    student_stats = cursor.fetchall()

    for st in student_stats:
        p = st["present_count"]
        a = max(0, total_sessions - p)
        st["absent_count"] = a
        st["attendance_pct"] = round((p / total_sessions * 100), 2) if total_sessions > 0 else 0.0

    cursor.close()
    conn.close()

    return render_template("teacher/reports.html",
                           batch=batch,
                           sessions=sessions,
                           student_stats=student_stats,
                           total_sessions=total_sessions)

@teacher_bp.route("/attendance-sheet")
@teacher_bp.route("/batches/<int:batch_id>/attendance-sheet")
@teacher_required
def attendance_sheet(batch_id=None):
    """Batch Student Attendance Matrix Sheet - spreadsheet view with date columns and student rows."""
    teacher_id = get_teacher_id()
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get all active/completed batches taught by this teacher for dropdown selector
    cursor.execute("""
        SELECT b.batch_id, b.batch_name, c.course_name, b.status,
               (SELECT COUNT(*) FROM batch_students bs WHERE bs.batch_id = b.batch_id AND bs.status IN ('active', 'completed')) AS student_count
        FROM batches b
        JOIN courses c ON b.course_id = c.course_id
        WHERE b.teacher_id = %s
        ORDER BY FIELD(b.status, 'active', 'upcoming', 'completed'), b.start_date DESC
    """, (teacher_id,))
    teacher_batches = cursor.fetchall()

    if not teacher_batches:
        cursor.close()
        conn.close()
        flash("You do not have any assigned batches yet.", "info")
        return redirect(url_for("teacher.dashboard"))

    # Determine which batch to display
    selected_batch_id = batch_id
    if not selected_batch_id or not any(b["batch_id"] == selected_batch_id for b in teacher_batches):
        selected_batch_id = teacher_batches[0]["batch_id"]

    # Fetch selected batch details
    cursor.execute("""
        SELECT b.*, c.course_name
        FROM batches b
        JOIN courses c ON b.course_id = c.course_id
        WHERE b.batch_id = %s AND b.teacher_id = %s
    """, (selected_batch_id, teacher_id))
    current_batch = cursor.fetchone()

    # Optional month filter (format: 'YYYY-MM' or 'all')
    month_filter = request.args.get("month", "all").strip()

    # Fetch all sessions for this batch
    if month_filter and month_filter != "all":
        cursor.execute("""
            SELECT s.session_id, s.session_date, s.started_at, s.status,
                   (SELECT COUNT(*) FROM attendance a WHERE a.session_id = s.session_id AND a.status = 'present') AS present_count
            FROM attendance_sessions s
            WHERE s.batch_id = %s AND DATE_FORMAT(s.session_date, '%%Y-%%m') = %s
            ORDER BY s.session_date ASC, s.started_at ASC
        """, (selected_batch_id, month_filter))
    else:
        cursor.execute("""
            SELECT s.session_id, s.session_date, s.started_at, s.status,
                   (SELECT COUNT(*) FROM attendance a WHERE a.session_id = s.session_id AND a.status = 'present') AS present_count
            FROM attendance_sessions s
            WHERE s.batch_id = %s
            ORDER BY s.session_date ASC, s.started_at ASC
        """, (selected_batch_id,))
    sessions = cursor.fetchall()

    # Get distinct months available for filtering
    cursor.execute("""
        SELECT DISTINCT DATE_FORMAT(session_date, '%%Y-%%m') AS ym,
                        DATE_FORMAT(session_date, '%%M %%Y') AS ym_label
        FROM attendance_sessions
        WHERE batch_id = %s
        ORDER BY ym DESC
    """, (selected_batch_id,))
    available_months = cursor.fetchall()

    # Fetch enrolled students
    cursor.execute("""
        SELECT s.student_id, s.full_name, s.enrollment_number, s.email, s.phone
        FROM batch_students bs
        JOIN students s ON bs.student_id = s.student_id
        WHERE bs.batch_id = %s AND bs.status IN ('active', 'completed')
        ORDER BY s.full_name ASC
    """, (selected_batch_id,))
    students = cursor.fetchall()

    # Fetch all attendance records for these sessions
    session_ids = [s["session_id"] for s in sessions]
    att_matrix = {}  # {student_id: {session_id: 'present'}}
    for st in students:
        att_matrix[st["student_id"]] = {}

    if session_ids:
        format_strings = ','.join(['%s'] * len(session_ids))
        cursor.execute(f"""
            SELECT session_id, student_id, status
            FROM attendance
            WHERE session_id IN ({format_strings}) AND status = 'present'
        """, tuple(session_ids))
        att_rows = cursor.fetchall()
        for r in att_rows:
            sid = r["student_id"]
            if sid in att_matrix:
                att_matrix[sid][r["session_id"]] = "present"

    # Compute summary stats for each student
    total_sessions_count = len(sessions)
    for st in students:
        sid = st["student_id"]
        present_count = len(att_matrix.get(sid, {}))
        absent_count = max(0, total_sessions_count - present_count)
        pct = round((present_count / total_sessions_count * 100), 1) if total_sessions_count > 0 else 0.0
        st["present_count"] = present_count
        st["absent_count"] = absent_count
        st["attendance_pct"] = pct

    # Overall batch statistics
    total_students_count = len(students)
    batch_avg_pct = round(sum(st["attendance_pct"] for st in students) / total_students_count, 1) if total_students_count > 0 else 0.0

    # Today's attendance info
    today = date.today()
    today_session = next((s for s in sessions if s["session_date"] == today), None)

    cursor.close()
    conn.close()

    return render_template("teacher/attendance_sheet.html",
                           batches=teacher_batches,
                           current_batch=current_batch,
                           sessions=sessions,
                           students=students,
                           att_matrix=att_matrix,
                           total_sessions=total_sessions_count,
                           total_students=total_students_count,
                           batch_avg_pct=batch_avg_pct,
                           today_session=today_session,
                           available_months=available_months,
                           selected_month=month_filter)

@teacher_bp.route("/api/attendance/toggle", methods=["POST"])
@teacher_required
def api_toggle_attendance():
    """Toggle a student's attendance for a specific session via AJAX."""
    teacher_id = get_teacher_id()
    data = request.get_json() or {}
    session_id = data.get("session_id")
    student_id = data.get("student_id")
    target_status = data.get("status")  # 'present' or 'absent'

    if not session_id or not student_id:
        return jsonify({"success": False, "error": "Missing parameters"}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT s.session_id, s.batch_id
        FROM attendance_sessions s
        JOIN batches b ON s.batch_id = b.batch_id
        WHERE s.session_id = %s AND s.teacher_id = %s
    """, (session_id, teacher_id))
    sess = cursor.fetchone()
    if not sess:
        cursor.close()
        conn.close()
        return jsonify({"success": False, "error": "Unauthorized session"}), 403

    batch_id = sess["batch_id"]

    if target_status == "present":
        cursor.execute("""
            INSERT INTO attendance (session_id, student_id, status, marked_at)
            VALUES (%s, %s, 'present', NOW())
            ON DUPLICATE KEY UPDATE status = 'present', marked_at = NOW()
        """, (session_id, student_id))
    else:
        cursor.execute("DELETE FROM attendance WHERE session_id = %s AND student_id = %s", (session_id, student_id))

    # Recalculate student statistics for this batch
    cursor.execute("SELECT COUNT(*) AS total FROM attendance_sessions WHERE batch_id = %s", (batch_id,))
    total_batch_sessions = cursor.fetchone()["total"] or 0

    cursor.execute("""
        SELECT COUNT(*) AS present_count
        FROM attendance a
        JOIN attendance_sessions s ON a.session_id = s.session_id
        WHERE s.batch_id = %s AND a.student_id = %s AND a.status = 'present'
    """, (batch_id, student_id))
    student_present = cursor.fetchone()["present_count"] or 0
    student_absent = max(0, total_batch_sessions - student_present)
    student_pct = round((student_present / total_batch_sessions * 100), 1) if total_batch_sessions > 0 else 0.0

    # Recalculate session attendees count
    cursor.execute("SELECT COUNT(*) AS cnt FROM attendance WHERE session_id = %s AND status = 'present'", (session_id,))
    session_present_count = cursor.fetchone()["cnt"] or 0

    cursor.close()
    conn.close()

    return jsonify({
        "success": True,
        "new_status": target_status,
        "student_id": student_id,
        "session_id": session_id,
        "student_present": student_present,
        "student_absent": student_absent,
        "student_pct": student_pct,
        "session_present_count": session_present_count
    })

@teacher_bp.route("/api/session/<int:session_id>/mark-all", methods=["POST"])
@teacher_required
def api_mark_all_session(session_id):
    """Mark all enrolled students present or absent for a session."""
    teacher_id = get_teacher_id()
    action = request.form.get("action", "present")  # 'present' or 'clear'

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT s.session_id, s.batch_id
        FROM attendance_sessions s
        WHERE s.session_id = %s AND s.teacher_id = %s
    """, (session_id, teacher_id))
    sess = cursor.fetchone()
    if not sess:
        cursor.close()
        conn.close()
        flash("Session not found or unauthorized.", "danger")
        return redirect(url_for("teacher.batches"))

    batch_id = sess["batch_id"]

    if action == "present":
        cursor.execute("SELECT student_id FROM batch_students WHERE batch_id = %s AND status IN ('active', 'completed')", (batch_id,))
        students = cursor.fetchall()
        for st in students:
            cursor.execute("""
                INSERT INTO attendance (session_id, student_id, status, marked_at)
                VALUES (%s, %s, 'present', NOW())
                ON DUPLICATE KEY UPDATE status = 'present', marked_at = NOW()
            """, (session_id, st["student_id"]))
        flash(f"All {len(students)} students marked Present for this session.", "success")
    elif action == "clear":
        cursor.execute("DELETE FROM attendance WHERE session_id = %s", (session_id,))
        flash("Attendance cleared for this session.", "info")

    cursor.close()
    conn.close()
    return redirect(request.referrer or url_for("teacher.attendance_sheet", batch_id=batch_id))

@teacher_bp.route("/batches/<int:batch_id>/attendance/manual-date", methods=["POST"])
@teacher_required
def add_manual_session(batch_id):
    """Manually add an attendance session date (for manual roll-call or past date)."""
    teacher_id = get_teacher_id()
    session_date_str = request.form.get("session_date", "").strip()

    if not session_date_str:
        flash("Please select a valid date for the class.", "warning")
        return redirect(url_for("teacher.attendance_sheet", batch_id=batch_id))

    try:
        session_date = datetime.strptime(session_date_str, "%Y-%m-%d").date()
    except ValueError:
        flash("Invalid date format.", "danger")
        return redirect(url_for("teacher.attendance_sheet", batch_id=batch_id))

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT batch_id, batch_name FROM batches WHERE batch_id = %s AND teacher_id = %s", (batch_id, teacher_id))
    batch = cursor.fetchone()
    if not batch:
        cursor.close()
        conn.close()
        flash("Unauthorized.", "danger")
        return redirect(url_for("teacher.batches"))

    now = datetime.now()
    started_at = datetime.combine(session_date, now.time())
    qr_token = secrets.token_urlsafe(16)

    cursor.execute("""
        INSERT INTO attendance_sessions (batch_id, teacher_id, session_date, started_at, expires_at, qr_token, status)
        VALUES (%s, %s, %s, %s, %s, %s, 'closed')
    """, (batch_id, teacher_id, session_date, started_at, started_at, qr_token))

    cursor.close()
    conn.close()

    flash(f"Class date '{session_date.strftime('%d %b %Y')}' added to attendance sheet! You can now mark attendance.", "success")
    return redirect(url_for("teacher.attendance_sheet", batch_id=batch_id))

@teacher_bp.route("/batches/<int:batch_id>/attendance-sheet/export-csv")
@teacher_required
def export_attendance_sheet_csv(batch_id):
    """Export the batch attendance matrix as a CSV spreadsheet."""
    teacher_id = get_teacher_id()
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT b.*, c.course_name, t.full_name AS teacher_name
        FROM batches b
        JOIN courses c ON b.course_id = c.course_id
        LEFT JOIN teachers t ON b.teacher_id = t.teacher_id
        WHERE b.batch_id = %s AND b.teacher_id = %s
    """, (batch_id, teacher_id))
    batch = cursor.fetchone()

    if not batch:
        cursor.close()
        conn.close()
        flash("Batch not found.", "danger")
        return redirect(url_for("teacher.batches"))

    cursor.execute("""
        SELECT session_id, session_date
        FROM attendance_sessions
        WHERE batch_id = %s
        ORDER BY session_date ASC, started_at ASC
    """, (batch_id,))
    sessions = cursor.fetchall()

    cursor.execute("""
        SELECT s.student_id, s.full_name, s.enrollment_number
        FROM batch_students bs
        JOIN students s ON bs.student_id = s.student_id
        WHERE bs.batch_id = %s AND bs.status IN ('active', 'completed')
        ORDER BY s.full_name ASC
    """, (batch_id,))
    students = cursor.fetchall()

    session_ids = [s["session_id"] for s in sessions]
    att_set = set()
    if session_ids:
        format_strings = ','.join(['%s'] * len(session_ids))
        cursor.execute(f"""
            SELECT session_id, student_id
            FROM attendance
            WHERE session_id IN ({format_strings}) AND status = 'present'
        """, tuple(session_ids))
        for r in cursor.fetchall():
            att_set.add((r["student_id"], r["session_id"]))

    cursor.close()
    conn.close()

    output = io.StringIO()
    output.write('\ufeff')  # Excel UTF-8 BOM
    writer = csv.writer(output)

    writer.writerow(["ATTENDANCE REGISTER SHEET"])
    writer.writerow(["Batch", batch["batch_name"]])
    writer.writerow(["Course", batch["course_name"]])
    writer.writerow(["Instructor", batch["teacher_name"] or "N/A"])
    writer.writerow(["Total Classes Held", len(sessions)])
    writer.writerow(["Exported On", datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
    writer.writerow([])

    # Header Row
    headers = ["Roll / Enrollment No", "Student Name"]
    for ses in sessions:
        headers.append(ses["session_date"].strftime("%d %b"))
    headers.extend(["Total Present", "Total Absent", "Attendance %"])
    writer.writerow(headers)

    total_classes = len(sessions)
    for st in students:
        sid = st["student_id"]
        row = [st["enrollment_number"] or "N/A", st["full_name"]]
        present_cnt = 0
        for ses in sessions:
            if (sid, ses["session_id"]) in att_set:
                row.append("P")
                present_cnt += 1
            else:
                row.append("A")
        absent_cnt = max(0, total_classes - present_cnt)
        pct = f"{round((present_cnt / total_classes * 100), 1)}%" if total_classes > 0 else "0.0%"
        row.extend([present_cnt, absent_cnt, pct])
        writer.writerow(row)

    safe_name = "".join(c for c in batch["batch_name"] if c.isalnum() or c in (" ", "-", "_")).strip().replace(" ", "_")
    filename = f"{safe_name}_attendance_sheet_{date.today()}.csv"

    response = Response(output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = f"attachment; filename=\"{filename}\""
    return response

@teacher_bp.route("/profile", methods=["GET", "POST"])
@teacher_required
def profile():
    teacher_id = get_teacher_id()
    conn = get_db_connection()
    cursor = conn.cursor()

    if request.method == "POST":
        action = request.form.get("action", "change_password")

        if action == "update_info":
            full_name = request.form.get("full_name", "").strip()
            phone = request.form.get("phone", "").strip()
            specialization = request.form.get("specialization", "").strip()

            if not full_name:
                flash("Full name cannot be empty.", "danger")
            else:
                cursor.execute("""
                    UPDATE teachers
                    SET full_name = %s, phone = %s, specialization = %s
                    WHERE teacher_id = %s
                """, (full_name, phone, specialization, teacher_id))
                session["user_name"] = full_name
                flash("Profile information updated successfully!", "success")

        elif action == "change_password":
            current_password = request.form.get("current_password", "").strip()
            new_password = request.form.get("new_password", "").strip()
            confirm_password = request.form.get("confirm_password", "").strip()

            cursor.execute("SELECT password FROM teachers WHERE teacher_id = %s", (teacher_id,))
            teacher_rec = cursor.fetchone()

            if not current_password or not new_password or not confirm_password:
                flash("All password fields are required.", "warning")
            elif not verify_password(current_password, teacher_rec["password"]):
                flash("Current password is incorrect.", "danger")
            elif len(new_password) < 6:
                flash("New password must be at least 6 characters long.", "warning")
            elif new_password != confirm_password:
                flash("New passwords do not match. Please try again.", "danger")
            else:
                hashed_pwd = hash_password(new_password)
                cursor.execute("UPDATE teachers SET password = %s WHERE teacher_id = %s", (hashed_pwd, teacher_id))
                flash("Password changed successfully! Keep your new credentials safe.", "success")

    # Fetch fresh teacher details
    cursor.execute("""
        SELECT t.*,
               (SELECT COUNT(*) FROM batches b WHERE b.teacher_id = t.teacher_id AND b.status = 'active') AS active_batches,
               (SELECT COUNT(DISTINCT bs.student_id) FROM batch_students bs 
                JOIN batches b ON bs.batch_id = b.batch_id 
                WHERE b.teacher_id = t.teacher_id AND bs.status = 'active') AS total_students
        FROM teachers t
        WHERE t.teacher_id = %s
    """, (teacher_id,))
    teacher = cursor.fetchone()

    cursor.close()
    conn.close()

    return render_template("teacher/profile.html", teacher=teacher)
