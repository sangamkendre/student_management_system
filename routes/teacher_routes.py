import secrets
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify
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
               (SELECT COUNT(*) FROM attendance_sessions s WHERE s.batch_id = b.batch_id) AS session_count
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

    # Generate token & expiry
    qr_token = secrets.token_urlsafe(20)
    now = datetime.now()
    expires_at = now + timedelta(minutes=duration_mins)
    today = now.date()

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

    flash(f"Attendance session started for {batch['batch_name']}! Display the QR code below.", "success")
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
