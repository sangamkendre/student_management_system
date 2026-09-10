import secrets
from datetime import datetime, timedelta, date, time
from utils.db import get_db_connection
from utils.auth_helpers import hash_password
from utils.fees_db import ensure_fees_tables, create_student_fee, record_fee_payment

def seed_database():
    print("Connecting to database...")
    conn = get_db_connection()
    cursor = conn.cursor()

    # 1. Admin
    cursor.execute("SELECT admin_id FROM admins WHERE email = 'sangamkendre51@gmail.com'")
    if not cursor.fetchone():
        cursor.execute("""
            INSERT INTO admins (full_name, email, password)
            VALUES (%s, %s, %s)
        """, ('sangam kendre', 'sangamkendre51@gmail.com', 'tiger$333'))
        print("Admin user created.")
    else:
        print("Admin user exists.")

    # 2. Teachers
    teachers_data = [
        ('Rahul Sharma', 'rahul@institute.com', '+91 98765 43210', hash_password('password123'), 'Python Full Stack', 'active'),
        ('Sneha Patel', 'sneha@institute.com', '+91 98123 45678', hash_password('password123'), 'Data Science & AI', 'active')
    ]
    teacher_ids = {}
    for full_name, email, phone, pwd, spec, st in teachers_data:
        cursor.execute("SELECT teacher_id FROM teachers WHERE email = %s", (email,))
        row = cursor.fetchone()
        if not row:
            cursor.execute("""
                INSERT INTO teachers (full_name, email, phone, password, specialization, status)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (full_name, email, phone, pwd, spec, st))
            teacher_ids[full_name] = cursor.lastrowid
            print(f"Created teacher: {full_name}")
        else:
            teacher_ids[full_name] = row["teacher_id"]

    # 3. Courses
    courses_data = [
        ('Python Full Stack', 'Comprehensive full-stack development with Python, Flask, Django, MySQL, and modern JavaScript.', '3 Months'),
        ('Data Science & Machine Learning', 'Data analysis, statistics, machine learning algorithms, Pandas, and deep learning fundamentals.', '4 Months')
    ]
    course_ids = {}
    for name, desc, dur in courses_data:
        cursor.execute("SELECT course_id FROM courses WHERE course_name = %s", (name,))
        row = cursor.fetchone()
        if not row:
            cursor.execute("""
                INSERT INTO courses (course_name, description, duration)
                VALUES (%s, %s, %s)
            """, (name, desc, dur))
            course_ids[name] = cursor.lastrowid
            print(f"Created course: {name}")
        else:
            course_ids[name] = row["course_id"]

    # 4. Batches
    batches_data = [
        ('Python Batch A', course_ids['Python Full Stack'], teacher_ids['Rahul Sharma'], 
         date(2026, 9, 10), date(2026, 12, 10), time(10, 0), time(11, 30), 'active'),
        ('Python Batch B', course_ids['Python Full Stack'], teacher_ids['Rahul Sharma'], 
         date(2026, 10, 1), date(2027, 1, 15), time(14, 0), time(15, 30), 'upcoming'),
        ('Data Science Batch A', course_ids['Data Science & Machine Learning'], teacher_ids['Sneha Patel'], 
         date(2026, 8, 15), date(2026, 12, 15), time(16, 0), time(17, 30), 'active')
    ]
    batch_ids = {}
    for bname, cid, tid, sdate, edate, stime, etime, status in batches_data:
        cursor.execute("SELECT batch_id FROM batches WHERE batch_name = %s", (bname,))
        row = cursor.fetchone()
        if not row:
            cursor.execute("""
                INSERT INTO batches (batch_name, course_id, teacher_id, start_date, end_date, start_time, end_time, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (bname, cid, tid, sdate, edate, stime, etime, status))
            batch_ids[bname] = cursor.lastrowid
            print(f"Created batch: {bname}")
        else:
            batch_ids[bname] = row["batch_id"]

    # 5. Students
    students_data = [
        ('Bharat Kumar', 'bharat@gmail.com', '+91 91234 56789', hash_password('student123'), 'STU-2026-001', 'active'),
        ('Amit Verma', 'amit@gmail.com', '+91 92345 67890', hash_password('student123'), 'STU-2026-002', 'active'),
        ('Rahul Patel', 'rahulp@gmail.com', '+91 93456 78901', hash_password('student123'), 'STU-2026-003', 'active'),
        ('Priya Sharma', 'priya@gmail.com', '+91 94567 89012', hash_password('student123'), 'STU-2026-004', 'active')
    ]
    student_ids = {}
    for name, email, phone, pwd, eno, st in students_data:
        cursor.execute("SELECT student_id FROM students WHERE email = %s", (email,))
        row = cursor.fetchone()
        if not row:
            cursor.execute("""
                INSERT INTO students (full_name, email, phone, password, enrollment_number, status)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (name, email, phone, pwd, eno, st))
            student_ids[name] = cursor.lastrowid
            print(f"Created student: {name}")
        else:
            student_ids[name] = row["student_id"]

    # 6. Enroll students in Python Batch A
    python_batch_a_id = batch_ids['Python Batch A']
    for sname, sid in student_ids.items():
        cursor.execute("SELECT batch_student_id FROM batch_students WHERE batch_id = %s AND student_id = %s", 
                       (python_batch_a_id, sid))
        if not cursor.fetchone():
            cursor.execute("""
                INSERT INTO batch_students (batch_id, student_id, status)
                VALUES (%s, %s, 'active')
            """, (python_batch_a_id, sid))
            print(f"Enrolled {sname} into Python Batch A.")

    # 7. Pre-seed Historical Attendance Sessions for Python Batch A (Total 30 classes)
    cursor.execute("SELECT COUNT(*) as cnt FROM attendance_sessions WHERE batch_id = %s", (python_batch_a_id,))
    existing_sessions = cursor.fetchone()["cnt"]

    if existing_sessions < 30:
        print("Generating 30 attendance sessions to match prompt statistics...")
        # Bharat: 26 present, 4 absent
        # Amit: 28 present, 2 absent
        # Rahul: 22 present, 8 absent
        # Priya: 27 present, 3 absent
        bharat_absent_days = {3, 9, 17, 25}
        amit_absent_days = {7, 21}
        rahul_absent_days = {2, 5, 8, 12, 16, 20, 24, 28}
        priya_absent_days = {4, 15, 26}

        base_date = date.today() - timedelta(days=45)
        created_count = 0
        day_offset = 0

        while created_count < 30:
            current_day = base_date + timedelta(days=day_offset)
            day_offset += 1
            # Skip Sundays
            if current_day.weekday() == 6:
                continue

            created_count += 1
            started = datetime.combine(current_day, time(10, 0))
            expires = started + timedelta(minutes=10)
            token = secrets.token_urlsafe(16)

            cursor.execute("""
                INSERT INTO attendance_sessions (batch_id, teacher_id, session_date, started_at, expires_at, qr_token, status)
                VALUES (%s, %s, %s, %s, %s, %s, 'closed')
            """, (python_batch_a_id, teacher_ids['Rahul Sharma'], current_day, started, expires, token))
            ses_id = cursor.lastrowid

            # Bharat
            if created_count not in bharat_absent_days:
                cursor.execute("INSERT INTO attendance (session_id, student_id, status, marked_at) VALUES (%s, %s, 'present', %s)",
                               (ses_id, student_ids['Bharat Kumar'], started + timedelta(minutes=2)))

            # Amit
            if created_count not in amit_absent_days:
                cursor.execute("INSERT INTO attendance (session_id, student_id, status, marked_at) VALUES (%s, %s, 'present', %s)",
                               (ses_id, student_ids['Amit Verma'], started + timedelta(minutes=1)))

            # Rahul
            if created_count not in rahul_absent_days:
                cursor.execute("INSERT INTO attendance (session_id, student_id, status, marked_at) VALUES (%s, %s, 'present', %s)",
                               (ses_id, student_ids['Rahul Patel'], started + timedelta(minutes=4)))

            # Priya
            if created_count not in priya_absent_days:
                cursor.execute("INSERT INTO attendance (session_id, student_id, status, marked_at) VALUES (%s, %s, 'present', %s)",
                               (ses_id, student_ids['Priya Sharma'], started + timedelta(minutes=3)))

        print("Seeded 30 historical attendance sessions successfully!")

    # 8. Seed Student Fees and EMI Installments
    ensure_fees_tables()
    cursor.execute("SELECT COUNT(*) as cnt FROM student_fees")
    existing_fees = cursor.fetchone()["cnt"]

    if existing_fees == 0:
        print("Seeding initial student fees and EMI payment records...")
        py_course_id = course_ids.get('Python Full Stack')
        py_batch_id = batch_ids.get('Python Batch A')

        # Bharat Kumar: 3-month EMI (Total: 45,000, 2 EMIs Paid = 30,000, 1 EMI Remaining = 15,000)
        bharat_id = student_ids.get('Bharat Kumar')
        if bharat_id:
            fee1_id = create_student_fee(
                student_id=bharat_id,
                course_id=py_course_id,
                batch_id=py_batch_id,
                title="Python Full Stack Tuition Fee",
                total_amount=45000.00,
                discount_amount=0.00,
                payment_plan="emi",
                total_installments=3,
                first_due_date=date.today() - timedelta(days=60),
                notes="Standard 3-month installment plan."
            )
            record_fee_payment(fee1_id, 15000.00, payment_date=date.today() - timedelta(days=60),
                               payment_mode="upi", transaction_reference="UPI-98218201", remarks="EMI #1 Paid", recorded_by="Admin")
            record_fee_payment(fee1_id, 15000.00, payment_date=date.today() - timedelta(days=30),
                               payment_mode="net_banking", transaction_reference="NEFT-78219310", remarks="EMI #2 Paid", recorded_by="Admin")
            print("Seeded Bharat Kumar fee (3 EMIs: 2 Paid, 1 Remaining).")

        # Amit Verma: One-Time Full Payment (45,000 Paid)
        amit_id = student_ids.get('Amit Verma')
        if amit_id:
            fee2_id = create_student_fee(
                student_id=amit_id,
                course_id=py_course_id,
                batch_id=py_batch_id,
                title="Python Full Stack Tuition Fee",
                total_amount=45000.00,
                discount_amount=5000.00,
                payment_plan="one_time",
                total_installments=1,
                first_due_date=date.today() - timedelta(days=45),
                notes="Received early-bird discount of Rs 5,000."
            )
            record_fee_payment(fee2_id, 40000.00, payment_date=date.today() - timedelta(days=45),
                               payment_mode="card", transaction_reference="CARD-TXN-481920", remarks="Full One-Time Payment Cleared", recorded_by="Admin")
            print("Seeded Amit Verma fee (One-Time: Fully Paid).")

        # Priya Sharma: 4-month EMI (Total: 50,000, 1 EMI Paid = 12,500, Remaining = 37,500)
        priya_id = student_ids.get('Priya Sharma')
        if priya_id:
            fee3_id = create_student_fee(
                student_id=priya_id,
                course_id=py_course_id,
                batch_id=py_batch_id,
                title="Python Full Stack Tuition Fee",
                total_amount=50000.00,
                discount_amount=0.00,
                payment_plan="emi",
                total_installments=4,
                first_due_date=date.today() - timedelta(days=30),
                notes="4-month installment structure."
            )
            record_fee_payment(fee3_id, 12500.00, payment_date=date.today() - timedelta(days=30),
                               payment_mode="upi", transaction_reference="UPI-38291048", remarks="EMI #1 Paid", recorded_by="Admin")
            print("Seeded Priya Sharma fee (4 EMIs: 1 Paid, 3 Remaining).")

    cursor.close()
    conn.close()
    print("Database seeding completed successfully!")

if __name__ == "__main__":
    seed_database()
