import secrets
from datetime import datetime, timedelta, date
import requests
from utils.db import get_db_connection

def test_qr_flow():
    conn = get_db_connection()
    cur = conn.cursor()

    # Create a fresh active session for Python Batch A (batch_id = 1, teacher_id = 1)
    now = datetime.now()
    expires = now + timedelta(minutes=10)
    token = secrets.token_urlsafe(16)

    cur.execute("""
        INSERT INTO attendance_sessions (batch_id, teacher_id, session_date, started_at, expires_at, qr_token, status)
        VALUES (1, 1, %s, %s, %s, %s, 'active')
    """, (date.today(), now, expires, token))
    session_id = cur.lastrowid
    print(f"Created fresh session {session_id} with token: {token}")

    s = requests.Session()

    # 1. Test unauthenticated check-in
    r1 = s.get(f"http://127.0.0.1:5000/attendance/mark/{token}", allow_redirects=False)
    print("Unauthenticated status:", r1.status_code, "Redirect location:", r1.headers.get("Location"))
    assert r1.status_code in [302, 303], "Expected redirect to login"

    # 2. Log in as Bharat Kumar (student)
    login_resp = s.post("http://127.0.0.1:5000/login", data={"email": "bharat@gmail.com", "password": "student123", "role": "student"})
    print("Login status:", login_resp.status_code)

    # 3. Mark attendance
    mark_resp = s.get(f"http://127.0.0.1:5000/attendance/mark/{token}")
    print("Mark status:", mark_resp.status_code)
    assert "Attendance Recorded" in mark_resp.text, f"Expected success message but got: {mark_resp.text[:300]}"
    print("-> Attendance marked successfully!")

    # 4. Attempt duplicate attendance mark
    dup_resp = s.get(f"http://127.0.0.1:5000/attendance/mark/{token}")
    assert "Already Recorded" in dup_resp.text, f"Expected duplicate warning but got: {dup_resp.text[:300]}"
    print("-> Duplicate attendance successfully prevented!")

    # 5. Clean up the test session
    cur.execute("DELETE FROM attendance WHERE session_id = %s", (session_id,))
    cur.execute("DELETE FROM attendance_sessions WHERE session_id = %s", (session_id,))
    print("-> Cleaned up test session.")

    cur.close()
    conn.close()
    print("ALL TESTS PASSED WITH 100% SUCCESS!")

if __name__ == "__main__":
    test_qr_flow()
