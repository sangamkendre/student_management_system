import smtplib
from email.message import EmailMessage
import logging
from config import Config

logger = logging.getLogger(__name__)

def send_password_reset_email(to_email: str, user_name: str, role: str, reset_token: str, host_url: str) -> dict:
    """
    Sends a password reset email to a user (admin, teacher, or student).
    If SMTP credentials (MAIL_USERNAME/MAIL_PASSWORD) are configured in Config,
    delivers via real SMTP. Otherwise, uses development simulation mode.
    
    Returns a dictionary:
      {
        "sent": bool,           # True if delivered via SMTP
        "simulated": bool,      # True if development fallback used
        "reset_url": str,       # Complete reset URL
        "error": str or None    # Error message if SMTP delivery failed
      }
    """
    base_url = host_url.rstrip("/")
    reset_url = f"{base_url}/reset-password/{reset_token}"
    role_title = role.title() if role else "User"

    subject = f"EduManage - Password Reset Request ({role_title})"
    sender = Config.MAIL_DEFAULT_SENDER or "noreply@edumanage.com"

    # Plain text version
    text_body = f"""Hello {user_name},

We received a request to reset the password for your {role_title} account on the EduManage Portal ({to_email}).

To reset your password, please open the link below in your browser:
{reset_url}

This password reset link is valid for 30 minutes and can only be used once.

If you did not request this password reset, please ignore this email or contact support if you have concerns.

Best regards,
EduManage Administration Team
"""

    # HTML version
    html_body = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f8fafc; margin: 0; padding: 0; }}
    .container {{ max-width: 540px; margin: 30px auto; background: #ffffff; border-radius: 12px; border: 1px solid #e2e8f0; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); }}
    .header {{ background: linear-gradient(135deg, #1e1b4b 0%, #312e81 100%); padding: 30px; text-align: center; color: #ffffff; }}
    .header h1 {{ margin: 0; font-size: 22px; font-weight: 700; }}
    .header p {{ margin: 6px 0 0 0; font-size: 13px; color: #cbd5e1; }}
    .content {{ padding: 32px 30px; color: #334155; line-height: 1.6; font-size: 14.5px; }}
    .role-badge {{ display: inline-block; background: #e0e7ff; color: #4338ca; padding: 3px 10px; border-radius: 6px; font-size: 12px; font-weight: 700; margin-bottom: 12px; }}
    .btn-container {{ text-align: center; margin: 28px 0; }}
    .btn-reset {{ display: inline-block; background: #4f46e5; color: #ffffff !important; text-decoration: none; padding: 13px 28px; border-radius: 8px; font-weight: 700; font-size: 15px; box-shadow: 0 4px 12px rgba(79, 70, 229, 0.35); }}
    .link-fallback {{ background: #f1f5f9; padding: 12px; border-radius: 6px; word-break: break-all; font-family: monospace; font-size: 12px; color: #475569; margin-top: 14px; }}
    .footer {{ background: #f8fafc; padding: 20px 30px; border-top: 1px solid #e2e8f0; font-size: 12px; color: #64748b; text-align: center; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <div style="font-size: 36px; margin-bottom: 8px;">🎓</div>
      <h1>EduManage Portal</h1>
      <p>Student Management System</p>
    </div>
    <div class="content">
      <span class="role-badge">{role_title} Account</span>
      <p style="font-size: 16px; margin-top: 4px;">Hello <strong>{user_name}</strong>,</p>
      <p>We received a request to reset the password for your account associated with <strong>{to_email}</strong>.</p>
      
      <div class="btn-container">
        <a href="{reset_url}" class="btn-reset" target="_blank">Reset My Password</a>
      </div>

      <p style="font-size: 13px; color: #64748b;">If the button above does not work, copy and paste this link into your browser:</p>
      <div class="link-fallback">{reset_url}</div>

      <p style="font-size: 12.5px; color: #94a3b8; margin-top: 24px;">
        ⏳ <em>This reset link will expire in 30 minutes and can only be used once. If you did not request a password reset, you can safely ignore this email.</em>
      </p>
    </div>
    <div class="footer">
      &copy; EduManage Institute Management System &bull; Secure Authentication Service
    </div>
  </div>
</body>
</html>
"""

    # Check if SMTP credentials are provided
    has_credentials = bool(Config.MAIL_USERNAME and Config.MAIL_PASSWORD)

    if has_credentials:
        try:
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = sender
            msg["To"] = to_email
            msg.set_content(text_body)
            msg.add_alternative(html_body, subtype="html")

            server = smtplib.SMTP(Config.MAIL_SERVER, Config.MAIL_PORT, timeout=10)
            if Config.MAIL_USE_TLS:
                server.starttls()
            server.login(Config.MAIL_USERNAME, Config.MAIL_PASSWORD)
            server.send_message(msg)
            server.quit()

            logger.info(f"Password reset email sent to {to_email} via SMTP.")
            return {
                "sent": True,
                "simulated": False,
                "reset_url": reset_url,
                "error": None
            }
        except Exception as e:
            logger.warning(f"SMTP delivery failed to {to_email}: {e}. Falling back to simulation mode.")
            print(f"[DEVELOPMENT FALLBACK] Reset Link for {to_email} ({role}): {reset_url}")
            return {
                "sent": False,
                "simulated": True,
                "reset_url": reset_url,
                "error": str(e)
            }
    else:
        # Development simulation mode
        logger.info(f"[SIMULATED EMAIL] Password reset requested for {to_email} ({role}). Reset Link: {reset_url}")
        print(f"\n========================================================")
        print(f"[SIMULATED PASSWORD RESET EMAIL]")
        print(f"To: {to_email} ({user_name} - {role_title})")
        print(f"Reset Link: {reset_url}")
        print(f"========================================================\n")
        return {
            "sent": False,
            "simulated": True,
            "reset_url": reset_url,
            "error": None
        }
