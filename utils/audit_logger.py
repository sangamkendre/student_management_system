import logging
from datetime import datetime, date
from flask import session, request, has_request_context
from utils.db import get_db_connection

logger = logging.getLogger(__name__)

def ensure_activity_logs_table():
    """Ensure activity_logs table exists in the database."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS activity_logs (
                log_id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                role ENUM('admin', 'teacher', 'student', 'system') NOT NULL,
                user_name VARCHAR(150),
                action VARCHAR(60) NOT NULL,
                details TEXT NOT NULL,
                ip_address VARCHAR(50),
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_role_created (role, created_at),
                INDEX idx_user_role (user_id, role),
                INDEX idx_action (action),
                INDEX idx_created (created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)
        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"Error ensuring activity_logs table: {e}")

def get_client_ip():
    """Extract client IP address accounting for proxies and reverse proxies (e.g. Render)."""
    if not has_request_context():
        return None
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "127.0.0.1"

def log_activity(action: str, details: str, user_id=None, role=None, user_name=None, ip_address=None):
    """
    Log an administrative, teacher, or authentication action.
    Safely captures context from Flask session and request if not explicitly provided.
    Never throws an exception to avoid breaking primary workflows.
    """
    try:
        if has_request_context():
            if user_id is None:
                user_id = session.get("user_id", 0)
            if role is None:
                role = session.get("role", "system")
            if user_name is None:
                user_name = session.get("user_name", "System")
            if ip_address is None:
                ip_address = get_client_ip()
        else:
            user_id = user_id or 0
            role = role or "system"
            user_name = user_name or "System"
            ip_address = ip_address or "127.0.0.1"

        conn = get_db_connection()
        cursor = conn.cursor()
        ensure_activity_logs_table()

        cursor.execute("""
            INSERT INTO activity_logs (user_id, role, user_name, action, details, ip_address, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, NOW())
        """, (user_id, role, user_name, action, details, ip_address))

        cursor.close()
        conn.close()
        logger.info(f"[AUDIT] [{role.upper()}:{user_name}] {action} - {details}")
    except Exception as e:
        logger.warning(f"Failed to record activity log: {e}")

def get_activity_logs(role_filter=None, action_filter=None, search_query=None, date_filter=None, limit=50, offset=0):
    """
    Retrieve activity logs with optional filtering and pagination.
    """
    try:
        ensure_activity_logs_table()
        conn = get_db_connection()
        cursor = conn.cursor()

        where_clauses = []
        params = []

        if role_filter and role_filter != "all":
            where_clauses.append("role = %s")
            params.append(role_filter)

        if action_filter and action_filter != "all":
            where_clauses.append("action = %s")
            params.append(action_filter)

        if date_filter:
            where_clauses.append("DATE(created_at) = %s")
            params.append(date_filter)

        if search_query:
            pattern = f"%{search_query.strip()}%"
            where_clauses.append("(user_name LIKE %s OR details LIKE %s OR action LIKE %s OR ip_address LIKE %s)")
            params.extend([pattern, pattern, pattern, pattern])

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        # Total matching records
        count_query = f"SELECT COUNT(*) AS total FROM activity_logs {where_sql}"
        cursor.execute(count_query, tuple(params))
        total_count = (cursor.fetchone() or {}).get("total", 0)

        # Paginated logs
        query = f"""
            SELECT log_id, user_id, role, user_name, action, details, ip_address, created_at
            FROM activity_logs
            {where_sql}
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
        """
        cursor.execute(query, tuple(params + [limit, offset]))
        logs = cursor.fetchall() or []

        cursor.close()
        conn.close()
        return logs, total_count
    except Exception as e:
        logger.error(f"Error fetching activity logs: {e}")
        return [], 0

def get_activity_stats():
    """
    Return high-level summary KPIs for the activity logs dashboard.
    """
    try:
        ensure_activity_logs_table()
        conn = get_db_connection()
        cursor = conn.cursor()

        today = date.today()
        cursor.execute("""
            SELECT
                COUNT(*) AS total_logs,
                COALESCE(SUM(CASE WHEN DATE(created_at) = %s THEN 1 ELSE 0 END), 0) AS today_logs,
                COALESCE(SUM(CASE WHEN role = 'admin' THEN 1 ELSE 0 END), 0) AS admin_logs,
                COALESCE(SUM(CASE WHEN role = 'teacher' THEN 1 ELSE 0 END), 0) AS teacher_logs
            FROM activity_logs
        """, (today,))
        stats = cursor.fetchone() or {}

        # Top recent distinct action types for filter dropdown
        cursor.execute("""
            SELECT DISTINCT action 
            FROM activity_logs 
            ORDER BY action ASC
            LIMIT 40
        """)
        actions = [r["action"] for r in (cursor.fetchall() or [])]

        cursor.close()
        conn.close()
        return stats, actions
    except Exception as e:
        logger.error(f"Error fetching activity log stats: {e}")
        return {"total_logs": 0, "today_logs": 0, "admin_logs": 0, "teacher_logs": 0}, []
