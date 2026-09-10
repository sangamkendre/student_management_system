import secrets
from datetime import datetime, date, timedelta
from decimal import Decimal
from utils.db import get_db_connection

_fees_tables_ensured = False

def ensure_fees_tables(force=False):
    """Ensures that student_fees, fee_installments, and fee_payments tables exist."""
    global _fees_tables_ensured
    if _fees_tables_ensured and not force:
        return
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS student_fees (
                fee_id INT AUTO_INCREMENT PRIMARY KEY,
                student_id INT NOT NULL,
                course_id INT NULL,
                batch_id INT NULL,
                title VARCHAR(150) NOT NULL,
                total_amount DECIMAL(10, 2) NOT NULL,
                discount_amount DECIMAL(10, 2) NOT NULL DEFAULT 0.00,
                final_amount DECIMAL(10, 2) NOT NULL,
                paid_amount DECIMAL(10, 2) NOT NULL DEFAULT 0.00,
                remaining_amount DECIMAL(10, 2) NOT NULL,
                payment_plan ENUM('one_time', 'emi') NOT NULL DEFAULT 'one_time',
                total_installments INT NOT NULL DEFAULT 1,
                status ENUM('pending', 'partial', 'paid') NOT NULL DEFAULT 'pending',
                notes TEXT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_student (student_id),
                INDEX idx_status (status)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS fee_installments (
                installment_id INT AUTO_INCREMENT PRIMARY KEY,
                fee_id INT NOT NULL,
                installment_number INT NOT NULL,
                title VARCHAR(100) NOT NULL,
                due_date DATE NOT NULL,
                amount DECIMAL(10, 2) NOT NULL,
                paid_amount DECIMAL(10, 2) NOT NULL DEFAULT 0.00,
                remaining_amount DECIMAL(10, 2) NOT NULL,
                status ENUM('pending', 'partial', 'paid', 'overdue') NOT NULL DEFAULT 'pending',
                paid_date DATE NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_fee (fee_id),
                INDEX idx_due_date (due_date),
                INDEX idx_status (status)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS fee_payments (
                payment_id INT AUTO_INCREMENT PRIMARY KEY,
                fee_id INT NOT NULL,
                installment_id INT NULL,
                receipt_number VARCHAR(50) NOT NULL UNIQUE,
                amount_paid DECIMAL(10, 2) NOT NULL,
                payment_date DATE NOT NULL,
                payment_mode ENUM('cash', 'upi', 'card', 'net_banking', 'cheque') NOT NULL DEFAULT 'upi',
                transaction_reference VARCHAR(100) NULL,
                remarks TEXT NULL,
                recorded_by VARCHAR(100) NOT NULL DEFAULT 'Admin',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_fee (fee_id),
                INDEX idx_receipt (receipt_number)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)
        _fees_tables_ensured = True
    finally:
        cursor.close()
        conn.close()




def generate_receipt_number():
    """Generates a unique receipt code like REC-20260907-A1B2."""
    date_str = datetime.now().strftime("%Y%m%d")
    random_str = secrets.token_hex(2).upper()
    return f"REC-{date_str}-{random_str}"

def add_months(orig_date, months):
    """Accurately increments date by given number of months."""
    month = orig_date.month - 1 + months
    year = orig_date.year + month // 12
    month = month % 12 + 1
    day = min(orig_date.day, [31,
        29 if year % 4 == 0 and not (year % 100 == 0 and year % 400 != 0) else 28,
        31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)

def create_student_fee(student_id, course_id, batch_id, title, total_amount, discount_amount=0.0,
                       payment_plan="one_time", total_installments=1, first_due_date=None, notes=None):
    """
    Creates a new fee assignment and calculates installments.
    """
    ensure_fees_tables()
    conn = get_db_connection()
    cursor = conn.cursor()

    total_amount = Decimal(str(total_amount))
    discount_amount = Decimal(str(discount_amount or 0))
    final_amount = max(Decimal("0.00"), total_amount - discount_amount)
    total_installments = max(1, int(total_installments or 1))

    if payment_plan != "emi":
        payment_plan = "one_time"
        total_installments = 1

    if not first_due_date:
        first_due_date = date.today()
    elif isinstance(first_due_date, str):
        first_due_date = datetime.strptime(first_due_date, "%Y-%m-%d").date()

    cursor.execute("""
        INSERT INTO student_fees 
        (student_id, course_id, batch_id, title, total_amount, discount_amount, final_amount, 
         paid_amount, remaining_amount, payment_plan, total_installments, status, notes)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 0.00, %s, %s, %s, 'pending', %s)
    """, (
        student_id,
        course_id if course_id else None,
        batch_id if batch_id else None,
        title.strip(),
        total_amount,
        discount_amount,
        final_amount,
        final_amount,
        payment_plan,
        total_installments,
        notes.strip() if notes else None
    ))

    fee_id = cursor.lastrowid

    # Create installments
    base_installment = (final_amount / Decimal(total_installments)).quantize(Decimal("0.01"))
    running_total = Decimal("0.00")

    today = date.today()
    for i in range(1, total_installments + 1):
        if i == total_installments:
            inst_amount = final_amount - running_total
        else:
            inst_amount = base_installment
            running_total += inst_amount

        due = add_months(first_due_date, i - 1)
        inst_title = f"EMI #{i}" if payment_plan == "emi" else "Full Payment"
        inst_status = "overdue" if due < today else "pending"

        cursor.execute("""
            INSERT INTO fee_installments
            (fee_id, installment_number, title, due_date, amount, paid_amount, remaining_amount, status)
            VALUES (%s, %s, %s, %s, %s, 0.00, %s, %s)
        """, (fee_id, i, inst_title, due, inst_amount, inst_amount, inst_status))

    cursor.close()
    conn.close()
    return fee_id

def record_fee_payment(fee_id, amount_paid, payment_date=None, payment_mode="upi", 
                       transaction_reference=None, remarks=None, recorded_by="Admin", 
                       target_installment_id=None):
    """
    Records a payment towards a fee assignment.
    Allocates payment to pending installments and updates remaining balances.
    """
    ensure_fees_tables()
    conn = get_db_connection()
    cursor = conn.cursor()

    amount_paid = Decimal(str(amount_paid))
    if amount_paid <= Decimal("0.00"):
        cursor.close()
        conn.close()
        raise ValueError("Payment amount must be greater than zero.")

    if not payment_date:
        payment_date = date.today()
    elif isinstance(payment_date, str):
        payment_date = datetime.strptime(payment_date, "%Y-%m-%d").date()

    # Verify fee exists
    cursor.execute("SELECT * FROM student_fees WHERE fee_id = %s", (fee_id,))
    fee = cursor.fetchone()
    if not fee:
        cursor.close()
        conn.close()
        raise ValueError("Fee record not found.")

    receipt_no = generate_receipt_number()

    # Record payment transaction
    cursor.execute("""
        INSERT INTO fee_payments 
        (fee_id, installment_id, receipt_number, amount_paid, payment_date, payment_mode, 
         transaction_reference, remarks, recorded_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        fee_id,
        target_installment_id if target_installment_id else None,
        receipt_no,
        amount_paid,
        payment_date,
        payment_mode,
        transaction_reference.strip() if transaction_reference else None,
        remarks.strip() if remarks else None,
        recorded_by
    ))
    payment_id = cursor.lastrowid

    # Fetch installments to allocate payment
    cursor.execute("""
        SELECT * FROM fee_installments 
        WHERE fee_id = %s 
        ORDER BY installment_number ASC
    """, (fee_id,))
    installments = cursor.fetchall()

    remaining_payment = amount_paid

    # If target installment specified, prioritize it
    if target_installment_id:
        target_inst = next((item for item in installments if item["installment_id"] == target_installment_id), None)
        if target_inst and target_inst["remaining_amount"] > Decimal("0.00"):
            can_pay = min(remaining_payment, target_inst["remaining_amount"])
            new_inst_paid = target_inst["paid_amount"] + can_pay
            new_inst_rem = target_inst["remaining_amount"] - can_pay
            new_inst_status = "paid" if new_inst_rem <= Decimal("0.00") else "partial"
            paid_dt = payment_date if new_inst_status == "paid" else None

            cursor.execute("""
                UPDATE fee_installments 
                SET paid_amount = %s, remaining_amount = %s, status = %s, paid_date = %s 
                WHERE installment_id = %s
            """, (new_inst_paid, new_inst_rem, new_inst_status, paid_dt, target_inst["installment_id"]))
            remaining_payment -= can_pay

    # Allocate remaining payment to unpaid installments in sequence
    if remaining_payment > Decimal("0.00"):
        for inst in installments:
            if target_installment_id and inst["installment_id"] == target_installment_id:
                continue

            inst_rem = inst["remaining_amount"]
            if inst_rem > Decimal("0.00"):
                can_pay = min(remaining_payment, inst_rem)
                new_inst_paid = inst["paid_amount"] + can_pay
                new_inst_rem = inst_rem - can_pay
                new_inst_status = "paid" if new_inst_rem <= Decimal("0.00") else "partial"
                paid_dt = payment_date if new_inst_status == "paid" else None

                cursor.execute("""
                    UPDATE fee_installments 
                    SET paid_amount = %s, remaining_amount = %s, status = %s, paid_date = %s 
                    WHERE installment_id = %s
                """, (new_inst_paid, new_inst_rem, new_inst_status, paid_dt, inst["installment_id"]))

                remaining_payment -= can_pay
                if remaining_payment <= Decimal("0.00"):
                    break

    # Re-calculate overall fee totals
    cursor.execute("""
        SELECT COALESCE(SUM(amount_paid), 0.00) AS total_paid 
        FROM fee_payments 
        WHERE fee_id = %s
    """, (fee_id,))
    total_paid = cursor.fetchone()["total_paid"]

    final_amt = fee["final_amount"]
    new_fee_rem = max(Decimal("0.00"), final_amt - total_paid)
    if new_fee_rem <= Decimal("0.00"):
        new_fee_status = "paid"
    elif total_paid > Decimal("0.00"):
        new_fee_status = "partial"
    else:
        new_fee_status = "pending"

    cursor.execute("""
        UPDATE student_fees 
        SET paid_amount = %s, remaining_amount = %s, status = %s 
        WHERE fee_id = %s
    """, (total_paid, new_fee_rem, new_fee_status, fee_id))

    cursor.close()
    conn.close()

    return {
        "payment_id": payment_id,
        "receipt_number": receipt_no,
        "amount_paid": amount_paid,
        "remaining_fee": new_fee_rem,
        "status": new_fee_status
    }

_last_overdue_check = None

def refresh_overdue_statuses(force=False):
    """Updates pending installments to overdue if due_date has passed."""
    global _last_overdue_check
    now = datetime.now()
    if not force and _last_overdue_check and (now - _last_overdue_check).total_seconds() < 600:
        return
    ensure_fees_tables()
    conn = get_db_connection()
    cursor = conn.cursor()
    today = date.today()
    try:
        cursor.execute("""
            UPDATE fee_installments 
            SET status = 'overdue' 
            WHERE due_date < %s AND status IN ('pending', 'partial') AND remaining_amount > 0
        """, (today,))
        _last_overdue_check = now
    finally:
        cursor.close()
        conn.close()

def get_fees_summary():
    """Returns top-level metrics for the Fees Dashboard in a single database round-trip."""
    ensure_fees_tables()
    refresh_overdue_statuses()
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT 
                COALESCE(SUM(final_amount), 0.00) AS total_assigned,
                COALESCE(SUM(paid_amount), 0.00) AS total_collected,
                COALESCE(SUM(remaining_amount), 0.00) AS total_remaining,
                COUNT(*) AS total_records,
                COALESCE(SUM(CASE WHEN status = 'paid' THEN 1 ELSE 0 END), 0) AS fully_paid_count,
                COALESCE(SUM(CASE WHEN status = 'partial' THEN 1 ELSE 0 END), 0) AS partial_count,
                COALESCE(SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END), 0) AS pending_count,
                (SELECT COUNT(*) FROM fee_installments WHERE status = 'overdue') AS overdue_count
            FROM student_fees
        """)
        summary = cursor.fetchone() or {}
        if "overdue_count" not in summary or summary["overdue_count"] is None:
            summary["overdue_count"] = 0
        return summary
    finally:
        cursor.close()
        conn.close()


def get_all_fees(search_query=None, status_filter=None, plan_filter=None):
    """Fetches all student fees with student, course, and batch details."""
    ensure_fees_tables()
    conn = get_db_connection()
    cursor = conn.cursor()

    sql = """
        SELECT f.*,
               s.full_name AS student_name, s.enrollment_number, s.email AS student_email, s.phone AS student_phone,
               c.course_name, b.batch_name,
               (SELECT MIN(due_date) FROM fee_installments WHERE fee_id = f.fee_id AND status IN ('pending', 'partial', 'overdue')) AS next_due_date,
               (SELECT COUNT(*) FROM fee_installments WHERE fee_id = f.fee_id AND status = 'paid') AS paid_installments_count,
               (SELECT COUNT(*) FROM fee_installments WHERE fee_id = f.fee_id) AS total_installments_count
        FROM student_fees f
        JOIN students s ON f.student_id = s.student_id
        LEFT JOIN courses c ON f.course_id = c.course_id
        LEFT JOIN batches b ON f.batch_id = b.batch_id
        WHERE 1=1
    """
    params = []

    if status_filter and status_filter in ('pending', 'partial', 'paid'):
        sql += " AND f.status = %s"
        params.append(status_filter)

    if plan_filter and plan_filter in ('one_time', 'emi'):
        sql += " AND f.payment_plan = %s"
        params.append(plan_filter)

    if search_query:
        sql += " AND (s.full_name LIKE %s OR s.enrollment_number LIKE %s OR s.phone LIKE %s OR f.title LIKE %s)"
        like_query = f"%{search_query.strip()}%"
        params.extend([like_query, like_query, like_query, like_query])

    sql += " ORDER BY f.created_at DESC"

    cursor.execute(sql, tuple(params))
    records = cursor.fetchall()

    cursor.close()
    conn.close()
    return records

def get_fee_details(fee_id):
    """Fetches comprehensive details for a specific fee assignment."""
    ensure_fees_tables()
    refresh_overdue_statuses()
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT f.*,
               s.full_name AS student_name, s.enrollment_number, s.email AS student_email, s.phone AS student_phone,
               c.course_name, b.batch_name
        FROM student_fees f
        JOIN students s ON f.student_id = s.student_id
        LEFT JOIN courses c ON f.course_id = c.course_id
        LEFT JOIN batches b ON f.batch_id = b.batch_id
        WHERE f.fee_id = %s
    """, (fee_id,))
    fee = cursor.fetchone()

    if not fee:
        cursor.close()
        conn.close()
        return None

    # Fetch installments
    cursor.execute("""
        SELECT * FROM fee_installments 
        WHERE fee_id = %s 
        ORDER BY installment_number ASC
    """, (fee_id,))
    installments = cursor.fetchall()

    # Fetch payment transactions
    cursor.execute("""
        SELECT p.*, i.title AS installment_title
        FROM fee_payments p
        LEFT JOIN fee_installments i ON p.installment_id = i.installment_id
        WHERE p.fee_id = %s
        ORDER BY p.payment_date DESC, p.created_at DESC
    """, (fee_id,))
    payments = cursor.fetchall()

    cursor.close()
    conn.close()

    return {
        "fee": fee,
        "installments": installments,
        "payments": payments
    }

def get_payment_receipt(receipt_number):
    """Fetches complete payment receipt details."""
    ensure_fees_tables()
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT p.*,
               f.title AS fee_title, f.total_amount, f.discount_amount, f.final_amount, f.paid_amount AS fee_total_paid, f.remaining_amount AS fee_remaining,
               f.payment_plan,
               s.full_name AS student_name, s.enrollment_number, s.email AS student_email, s.phone AS student_phone,
               c.course_name, b.batch_name,
               i.title AS installment_title
        FROM fee_payments p
        JOIN student_fees f ON p.fee_id = f.fee_id
        JOIN students s ON f.student_id = s.student_id
        LEFT JOIN courses c ON f.course_id = c.course_id
        LEFT JOIN batches b ON f.batch_id = b.batch_id
        LEFT JOIN fee_installments i ON p.installment_id = i.installment_id
        WHERE p.receipt_number = %s
    """, (receipt_number,))
    receipt = cursor.fetchone()

    cursor.close()
    conn.close()
    return receipt

def get_student_fees(student_id):
    """Fetches all fee assignments and payment history for a given student."""
    ensure_fees_tables()
    refresh_overdue_statuses()
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT f.*,
                   c.course_name, b.batch_name,
                   (SELECT MIN(due_date) FROM fee_installments WHERE fee_id = f.fee_id AND status IN ('pending', 'partial', 'overdue')) AS next_due_date
            FROM student_fees f
            LEFT JOIN courses c ON f.course_id = c.course_id
            LEFT JOIN batches b ON f.batch_id = b.batch_id
            WHERE f.student_id = %s
            ORDER BY f.created_at DESC
        """, (student_id,))
        fees = cursor.fetchall()

        if not fees:
            return []

        fee_ids = [f["fee_id"] for f in fees]
        placeholders = ','.join(['%s'] * len(fee_ids))

        cursor.execute(f"SELECT * FROM fee_installments WHERE fee_id IN ({placeholders}) ORDER BY installment_number ASC", tuple(fee_ids))
        all_installments = cursor.fetchall()

        cursor.execute(f"SELECT * FROM fee_payments WHERE fee_id IN ({placeholders}) ORDER BY payment_date DESC, created_at DESC", tuple(fee_ids))
        all_payments = cursor.fetchall()

        installments_by_fee = {}
        for inst in all_installments:
            installments_by_fee.setdefault(inst["fee_id"], []).append(inst)

        payments_by_fee = {}
        for p in all_payments:
            payments_by_fee.setdefault(p["fee_id"], []).append(p)

        result = []
        for fee in fees:
            fid = fee["fee_id"]
            result.append({
                "fee": fee,
                "installments": installments_by_fee.get(fid, []),
                "payments": payments_by_fee.get(fid, [])
            })
        return result
    finally:
        cursor.close()
        conn.close()

def delete_student_fee(fee_id):
    """Deletes a student fee assignment and all related installments and payments."""
    ensure_fees_tables()
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("DELETE FROM fee_payments WHERE fee_id = %s", (fee_id,))
    cursor.execute("DELETE FROM fee_installments WHERE fee_id = %s", (fee_id,))
    cursor.execute("DELETE FROM student_fees WHERE fee_id = %s", (fee_id,))

    cursor.close()
    conn.close()
    return True
