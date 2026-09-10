import threading
import pymysql
from dbutils.pooled_db import PooledDB
from config import Config

_pool = None
_pool_lock = threading.Lock()

def get_pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = PooledDB(
                    creator=pymysql,
                    maxconnections=20,
                    mincached=2,
                    maxcached=8,
                    maxshared=0,
                    blocking=True,
                    maxusage=500,
                    ping=0,
                    host=Config.MYSQL_HOST,
                    port=Config.MYSQL_PORT,
                    user=Config.MYSQL_USER,
                    password=Config.MYSQL_PASSWORD,
                    database=Config.MYSQL_DB,
                    charset="utf8mb4",
                    cursorclass=pymysql.cursors.DictCursor,
                    autocommit=True,
                    connect_timeout=10,
                    read_timeout=30,
                    write_timeout=30
                )
    return _pool

def reset_pool():
    global _pool
    with _pool_lock:
        if _pool is not None:
            try:
                _pool.close()
            except Exception:
                pass
            _pool = None

def get_db_connection():
    pool = get_pool()
    try:
        return pool.connection()
    except Exception:
        # Retry once if connection pool had stale connections
        reset_pool()
        return get_pool().connection()

