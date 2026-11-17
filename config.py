import os
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
dotenv_path = os.path.join(BASE_DIR, ".env")
load_dotenv(dotenv_path, override=True)

class Config:

    SECRET_KEY = os.getenv("SECRET_KEY", "change-this-secret-key")


    MYSQL_HOST = os.getenv("MYSQL_HOST", "gateway01.ap-northeast-1.prod.aws.tidbcloud.com")
    MYSQL_PORT = int(os.getenv("MYSQL_PORT", 4000))
    MYSQL_USER = os.getenv("MYSQL_USER", "2cehH5YppVnhKiw.root")
    MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "gVzrz8rZricxKFoZ")
    MYSQL_DB = os.getenv(
        "MYSQL_DB",
        "student_management_system"
    )

    # Email / SMTP Configuration
    MAIL_SERVER = os.getenv("MAIL_SERVER", "smtp-relay.brevo.com")
    MAIL_PORT = int(os.getenv("MAIL_PORT", 587))
    MAIL_USE_TLS = str(os.getenv("MAIL_USE_TLS", "True")).lower() in ("true", "1", "yes")
    MAIL_USERNAME = os.getenv("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
    MAIL_DEFAULT_SENDER = os.getenv("MAIL_DEFAULT_SENDER", "")
