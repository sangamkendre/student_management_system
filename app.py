import os
from datetime import datetime
from flask import Flask, redirect, url_for, session, render_template
from werkzeug.middleware.proxy_fix import ProxyFix
from config import Config
from routes.auth_routes import auth
from routes.admin_routes import admin_bp
from routes.teacher_routes import teacher_bp
from routes.student_routes import student_bp
from routes.attendance_routes import attendance_bp

app = Flask(__name__)
app.config.from_object(Config)

# Enable ProxyFix to correctly handle reverse proxies (Render, AWS, Heroku) for HTTPS and host detection
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)


# Register Blueprints
app.register_blueprint(auth)
app.register_blueprint(admin_bp)
app.register_blueprint(teacher_bp)
app.register_blueprint(student_bp)
app.register_blueprint(attendance_bp)

# Warm-up pool and verify database tables once on startup
with app.app_context():
    try:
        from utils.fees_db import ensure_fees_tables
        ensure_fees_tables()
    except Exception as e:
        print(f"[WARN] Database initialization notice: {e}")


@app.context_processor
def inject_globals():
    return {
        "current_year": datetime.now().year,
        "user_name": session.get("user_name"),
        "user_role": session.get("role"),
        "user_email": session.get("email")
    }

@app.route("/")
def home():
    if "user_id" in session and "role" in session:
        role = session["role"]
        if role == "admin":
            return redirect(url_for("admin.dashboard"))
        elif role == "teacher":
            return redirect(url_for("teacher.dashboard"))
        elif role == "student":
            return redirect(url_for("student.dashboard"))
    return redirect(url_for("auth.login"))

@app.errorhandler(404)
def page_not_found(e):
    return render_template("attendance/result.html",
                           status="error",
                           title="Page Not Found (404)",
                           message="The page you requested does not exist or has been moved."), 404

@app.errorhandler(500)
def internal_server_error(e):
    return render_template("attendance/result.html",
                           status="error",
                           title="Server Error (500)",
                           message="An internal server error occurred. Please try again or contact support."), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_ENV", "development").lower() != "production"
    app.run(host="0.0.0.0", port=port, debug=debug)