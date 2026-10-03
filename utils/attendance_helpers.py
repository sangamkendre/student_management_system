from utils.db import get_db_connection


def get_low_attendance_records(teacher_id=None, batch_id=None, course_id=None, search_query=None, shortage_level=None, threshold=75.0):
    """
    Fetch all student-batch enrollments where attendance percentage is below the given threshold (default 75%).
    Computes attended sessions, absent sessions, attendance %, severity, and consecutive classes needed to cross 75%.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    query = """
        SELECT 
            s.student_id, s.full_name, s.enrollment_number, s.email, s.phone, s.created_at,
            b.batch_id, b.batch_name, b.start_time, b.end_time, b.status AS batch_status,
            c.course_id, c.course_name, 
            t.teacher_id, t.full_name AS teacher_name, t.email AS teacher_email,
            (SELECT COUNT(*) FROM attendance_sessions ses WHERE ses.batch_id = b.batch_id) AS total_sessions,
            (SELECT COUNT(*) FROM attendance a 
             JOIN attendance_sessions ses ON a.session_id = ses.session_id 
             WHERE ses.batch_id = b.batch_id AND a.student_id = s.student_id AND a.status = 'present') AS present_count
        FROM batch_students bs
        JOIN students s ON bs.student_id = s.student_id
        JOIN batches b ON bs.batch_id = b.batch_id
        JOIN courses c ON b.course_id = c.course_id
        LEFT JOIN teachers t ON b.teacher_id = t.teacher_id
        WHERE bs.status IN ('active', 'completed')
    """
    params = []

    if teacher_id:
        query += " AND b.teacher_id = %s"
        params.append(teacher_id)

    if batch_id:
        query += " AND b.batch_id = %s"
        params.append(batch_id)

    if course_id:
        query += " AND b.course_id = %s"
        params.append(course_id)

    if search_query:
        like_term = f"%{search_query}%"
        query += " AND (s.full_name LIKE %s OR s.enrollment_number LIKE %s OR s.phone LIKE %s OR s.email LIKE %s)"
        params.extend([like_term, like_term, like_term, like_term])

    query += " ORDER BY b.batch_name ASC, s.full_name ASC"
    cursor.execute(query, tuple(params))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    defaulters = []
    critical_count = 0
    warning_count = 0
    batches_set = set()

    for r in rows:
        total = r["total_sessions"] or 0
        present = r["present_count"] or 0

        # If no classes have been conducted yet, student is not in attendance deficit
        if total == 0:
            continue

        pct = round((present / total * 100), 1)
        if pct < threshold:
            absent = max(0, total - present)
            # Consecutive future classes needed to reach >= 75%:
            # (present + C) / (total + C) >= 3/4  =>  4(present + C) >= 3(total + C)  =>  C >= 3*total - 4*present
            classes_needed = max(0, 3 * total - 4 * present)
            severity = "critical" if pct < 50.0 else "warning"

            if severity == "critical":
                critical_count += 1
            else:
                warning_count += 1

            batches_set.add(r["batch_id"])

            r["absent_count"] = absent
            r["attendance_pct"] = pct
            r["classes_needed"] = classes_needed
            r["severity"] = severity

            # Clean phone for WhatsApp / tel
            raw_phone = (r["phone"] or "").strip()
            clean_digits = "".join(ch for ch in raw_phone if ch.isdigit())
            r["whatsapp_phone"] = clean_digits if len(clean_digits) >= 10 else None

            if shortage_level == "critical" and severity != "critical":
                continue
            if shortage_level == "warning" and severity != "warning":
                continue

            defaulters.append(r)

    # Sort: lowest attendance % first, then student name
    defaulters.sort(key=lambda x: (x["attendance_pct"], x["full_name"]))

    return {
        "defaulters": defaulters,
        "total_count": len(defaulters),
        "critical_count": critical_count,
        "warning_count": warning_count,
        "batches_affected": len(batches_set)
    }


def get_low_attendance_count(teacher_id=None, batch_id=None, threshold=75.0):
    """
    Quickly compute count of students whose attendance is below threshold (default 75%).
    Useful for dashboard badges and KPI metric cards.
    """
    res = get_low_attendance_records(teacher_id=teacher_id, batch_id=batch_id, threshold=threshold)
    return res["total_count"]
