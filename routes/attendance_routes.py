from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify
from datetime import datetime
from utils.db import get_db_connection
from utils.auth_helpers import login_required

attendance_bp = Blueprint("attendance", __name__)

@attendance_bp.route("/attendance/mark/<token>", methods=["GET", "POST"])
def mark_attendance(token):
    # 1. Require user to be logged in
    if "user_id" not in session:
        flash("Please log in to your student account to record attendance.", "info")
        return redirect(url_for("auth.login", next=request.url))

    if session.get("role") != "student":
        return render_template("attendance/result.html",
                               status="error",
                               title="Access Restricted",
                               message="Only students can mark attendance for classes.")

    student_id = session["user_id"]
    student_name = session.get("user_name", "Student")

    connection = get_db_connection()
    cursor = connection.cursor()

    # 2. Retrieve session info
    cursor.execute("""
        SELECT s.session_id, s.batch_id, s.teacher_id, s.session_date, s.started_at, s.expires_at, s.status,
               b.batch_name, c.course_name, t.full_name AS teacher_name
        FROM attendance_sessions s
        JOIN batches b ON s.batch_id = b.batch_id
        JOIN courses c ON b.course_id = c.course_id
        JOIN teachers t ON s.teacher_id = t.teacher_id
        WHERE s.qr_token = %s
    """, (token,))
    att_session = cursor.fetchone()

    if not att_session:
        cursor.close()
        connection.close()
        return render_template("attendance/result.html",
                               status="error",
                               title="Invalid Session",
                               message="The QR code is invalid or the attendance session does not exist.")

    now = datetime.now()

    # 3. Check expiration & status
    if att_session["status"] != "active" or now > att_session["expires_at"]:
        if att_session["status"] == "active":
            cursor.execute("UPDATE attendance_sessions SET status = 'expired' WHERE session_id = %s", (att_session["session_id"],))
        cursor.close()
        connection.close()
        return render_template("attendance/result.html",
                               status="error",
                               title="Session Expired",
                               message=f"This QR code expired at {att_session['expires_at'].strftime('%I:%M %p')}. Please request a new QR code from your instructor.",
                               batch_name=att_session["batch_name"],
                               course_name=att_session["course_name"])

    # 4. Check enrollment in this batch
    cursor.execute("""
        SELECT batch_student_id, status FROM batch_students
        WHERE batch_id = %s AND student_id = %s
    """, (att_session["batch_id"], student_id))
    enrollment = cursor.fetchone()

    if not enrollment or enrollment["status"] != "active":
        cursor.close()
        connection.close()
        return render_template("attendance/result.html",
                               status="error",
                               title="Not Enrolled",
                               message=f"You are not actively enrolled in {att_session['batch_name']} ({att_session['course_name']}). Only enrolled students can mark attendance.",
                               batch_name=att_session["batch_name"],
                               course_name=att_session["course_name"])

    # 5. Check duplicate attendance
    cursor.execute("""
        SELECT attendance_id, marked_at FROM attendance
        WHERE session_id = %s AND student_id = %s
    """, (att_session["session_id"], student_id))
    existing_attendance = cursor.fetchone()

    if existing_attendance:
        marked_time = existing_attendance["marked_at"].strftime("%I:%M %p") if existing_attendance["marked_at"] else "earlier"
        cursor.close()
        connection.close()
        return render_template("attendance/result.html",
                               status="warning",
                               title="Already Recorded",
                               message=f"Your attendance for {att_session['batch_name']} was already marked Present at {marked_time}.",
                               batch_name=att_session["batch_name"],
                               course_name=att_session["course_name"],
                               student_name=student_name)

    # 6. Mark attendance
    cursor.execute("""
        INSERT INTO attendance (session_id, student_id, status, marked_at)
        VALUES (%s, %s, 'present', NOW())
    """, (att_session["session_id"], student_id))

    cursor.close()
    connection.close()

    return render_template("attendance/result.html",
                           status="success",
                           title="Attendance Recorded!",
                           message=f"Great job, {student_name}! You have been marked Present.",
                           batch_name=att_session["batch_name"],
                           course_name=att_session["course_name"],
                           teacher_name=att_session["teacher_name"],
                           marked_at=datetime.now().strftime("%d %b %Y at %I:%M %p"))

@attendance_bp.route("/api/session/<int:session_id>/live-status")
@login_required
def live_session_status(session_id):
    """API polling endpoint for the live QR view in Teacher panel."""
    connection = get_db_connection()
    cursor = connection.cursor()

    cursor.execute("""
        SELECT s.session_id, s.batch_id, s.teacher_id, s.expires_at, s.status,
               TIMESTAMPDIFF(SECOND, NOW(), s.expires_at) AS seconds_left
        FROM attendance_sessions s
        WHERE s.session_id = %s
    """, (session_id,))
    att_session = cursor.fetchone()

    if not att_session:
        cursor.close()
        connection.close()
        return jsonify({"status": "error", "message": "Session not found"}), 404

    seconds_left = max(0, att_session["seconds_left"] or 0)
    current_status = att_session["status"]
    if seconds_left <= 0 and current_status == "active":
        cursor.execute("UPDATE attendance_sessions SET status = 'expired' WHERE session_id = %s", (session_id,))
        current_status = "expired"

    # Count enrolled
    cursor.execute("SELECT COUNT(*) AS total FROM batch_students WHERE batch_id = %s AND status = 'active'", (att_session["batch_id"],))
    total_enrolled = cursor.fetchone()["total"]

    # Attendees who marked
    cursor.execute("""
        SELECT st.student_id, st.full_name, st.enrollment_number, a.marked_at
        FROM attendance a
        JOIN students st ON a.student_id = st.student_id
        WHERE a.session_id = %s
        ORDER BY a.marked_at DESC
    """, (session_id,))
    attendees = cursor.fetchall()

    cursor.close()
    connection.close()

    attendees_list = []
    for row in attendees:
        attendees_list.append({
            "student_id": row["student_id"],
            "full_name": row["full_name"],
            "enrollment_number": row["enrollment_number"] or "N/A",
            "marked_at": row["marked_at"].strftime("%I:%M:%S %p") if row["marked_at"] else ""
        })

    return jsonify({
        "status": current_status,
        "seconds_remaining": seconds_left,
        "total_enrolled": total_enrolled,
        "present_count": len(attendees_list),
        "attendees": attendees_list
    })
