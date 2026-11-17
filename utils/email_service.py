import os
import smtplib
from email.message import EmailMessage
import logging
from dotenv import load_dotenv
from config import Config

logger = logging.getLogger(__name__)

def send_password_reset_otp(to_email: str, user_name: str, role: str, otp: str, reset_url: str = None) -> dict:
    """
    Sends a 6-digit password reset OTP email to a user (admin, teacher, or student).
    Supports Brevo HTTPS REST API (ports 443) as well as SMTP (ports 2525 / 587).
    
    Returns a dictionary:
      {
        "sent": bool,           # True if delivered via SMTP or API
        "error": str or None    # Error message if delivery failed
      }
    """
    # Ensure latest environment variables are loaded using absolute path
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dotenv_path = os.path.join(base_dir, ".env")
    load_dotenv(dotenv_path, override=True)

    role_title = role.title() if role else "User"
    subject = f"EduManage - Password Reset OTP: {otp}"

    mail_server = os.getenv("MAIL_SERVER") or Config.MAIL_SERVER or "smtp-relay.brevo.com"
    mail_port = int(os.getenv("MAIL_PORT") or Config.MAIL_PORT or 2525)
    mail_use_tls = str(os.getenv("MAIL_USE_TLS", Config.MAIL_USE_TLS)).lower() in ("true", "1", "yes")
    mail_username = os.getenv("MAIL_USERNAME") or Config.MAIL_USERNAME or ""
    mail_password = os.getenv("MAIL_PASSWORD") or Config.MAIL_PASSWORD or ""
    mail_sender = os.getenv("MAIL_DEFAULT_SENDER") or Config.MAIL_DEFAULT_SENDER or mail_username or "noreply@edumanage.com"

    if "<" in mail_sender:
        sender = mail_sender
    else:
        sender = f"EduManage Portal <{mail_sender}>"

    # Plain text version
    text_body = f"""Hello {user_name},

We received a request to reset the password for your {role_title} account on the EduManage Portal ({to_email}).

Your 6-digit verification code (OTP) is:
----------------------------------------
{otp}
----------------------------------------

This OTP is valid for 10 minutes and can only be used once.

Please enter this verification code on the portal to set your new password.

If you did not request this password reset, please ignore this email or contact support if you have concerns.

Best regards,
EduManage Administration Team
"""

    # HTML version
    action_button_html = ""
    if reset_url:
        action_button_html = f"""
        <div style="text-align: center; margin: 24px 0 16px 0;">
          <a href="{reset_url}" style="display: inline-block; background: #4f46e5; color: #ffffff !important; text-decoration: none; padding: 12px 24px; border-radius: 8px; font-weight: 700; font-size: 14px;" target="_blank">
            Open Verification Page &rarr;
          </a>
        </div>
        """

    html_body = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f8fafc; margin: 0; padding: 0; }}
    .container {{ max-width: 540px; margin: 30px auto; background: #ffffff; border-radius: 14px; border: 1px solid #e2e8f0; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); }}
    .header {{ background: linear-gradient(135deg, #1e1b4b 0%, #312e81 100%); padding: 30px; text-align: center; color: #ffffff; }}
    .header h1 {{ margin: 0; font-size: 22px; font-weight: 700; letter-spacing: -0.02em; }}
    .header p {{ margin: 6px 0 0 0; font-size: 13px; color: #cbd5e1; }}
    .content {{ padding: 32px 30px; color: #334155; line-height: 1.6; font-size: 14.5px; }}
    .role-badge {{ display: inline-block; background: #e0e7ff; color: #4338ca; padding: 4px 12px; border-radius: 6px; font-size: 12px; font-weight: 700; margin-bottom: 12px; }}
    .otp-box {{ background: linear-gradient(180deg, #f8fafc 0%, #eef2ff 100%); border: 2px dashed #6366f1; border-radius: 12px; padding: 24px 16px; text-align: center; margin: 24px 0; }}
    .otp-label {{ font-size: 12px; text-transform: uppercase; letter-spacing: 0.1em; color: #64748b; font-weight: 700; margin-bottom: 8px; }}
    .otp-digits {{ font-size: 38px; font-weight: 800; letter-spacing: 8px; color: #4338ca; font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace; line-height: 1.2; }}
    .otp-expiry {{ font-size: 12px; color: #64748b; margin-top: 10px; font-weight: 500; }}
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
      <p>We received a request to reset the password for your account linked to <strong>{to_email}</strong>.</p>
      
      <div class="otp-box">
        <div class="otp-label">Your Verification Code (OTP)</div>
        <div class="otp-digits">{otp}</div>
        <div class="otp-expiry">⏳ Valid for 10 minutes &bull; Single-use only</div>
      </div>

      {action_button_html}

      <p style="font-size: 13px; color: #64748b; margin-top: 20px;">
        Enter this 6-digit code on the EduManage password reset page to choose a new password.
      </p>

      <p style="font-size: 12px; color: #94a3b8; margin-top: 24px; border-top: 1px solid #f1f5f9; padding-top: 14px;">
        🔒 <em>If you did not make this request, you can safely ignore this email. Your password will remain unchanged.</em>
      </p>
    </div>
    <div class="footer">
      &copy; EduManage Institute Management System &bull; Secure Authentication Service
    </div>
  </div>
</body>
</html>
"""

    # 1. Option A: Check if a Brevo API Key is configured (via BREVO_API_KEY or xkeysib- key)
    brevo_api_key = os.getenv("BREVO_API_KEY")
    if not brevo_api_key and mail_password and mail_password.startswith("xkeysib-"):
        brevo_api_key = mail_password

    if brevo_api_key:
        try:
            import json
            import urllib.request
            api_url = "https://api.brevo.com/v3/smtp/email"
            clean_email = mail_sender
            clean_name = "EduManage Portal"
            if "<" in mail_sender and ">" in mail_sender:
                clean_name = mail_sender.split("<")[0].strip() or "EduManage Portal"
                clean_email = mail_sender.split("<")[1].split(">")[0].strip()

            api_payload = {
                "sender": {"name": clean_name, "email": clean_email},
                "to": [{"email": to_email, "name": user_name}],
                "subject": subject,
                "htmlContent": html_body,
                "textContent": text_body
            }
            api_headers = {
                "accept": "application/json",
                "content-type": "application/json",
                "api-key": brevo_api_key
            }
            req = urllib.request.Request(api_url, data=json.dumps(api_payload).encode("utf-8"), headers=api_headers)
            with urllib.request.urlopen(req, timeout=12) as resp:
                if resp.status in (200, 201, 202):
                    logger.info(f"Password reset OTP sent to {to_email} via Brevo REST API.")
                    return {"sent": True, "error": None}
        except Exception as e:
            logger.warning(f"Brevo REST API sending failed: {e}. Falling back to SMTP.")

    # 2. Option B: SMTP delivery
    if not mail_username or not mail_password:
        logger.error("SMTP credentials (MAIL_USERNAME / MAIL_PASSWORD) are not set in .env")
        return {
            "sent": False,
            "error": "SMTP credentials (MAIL_USERNAME / MAIL_PASSWORD) are missing in .env."
        }

    # Determine ports to try (port 2525 bypasses cloud firewall blocks on 587)
    ports_to_try = [mail_port]
    if 2525 not in ports_to_try:
        ports_to_try.append(2525)

    last_error = None
    for port in ports_to_try:
        try:
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = sender
            msg["To"] = to_email
            msg.set_content(text_body)
            msg.add_alternative(html_body, subtype="html")

            server = smtplib.SMTP(mail_server, port, timeout=10)
            if mail_use_tls:
                server.starttls()
            server.login(mail_username, mail_password)
            server.send_message(msg)
            server.quit()

            logger.info(f"Password reset OTP sent to {to_email} via SMTP ({mail_server}:{port}).")
            return {
                "sent": True,
                "error": None
            }
        except Exception as e:
            last_error = e
            logger.warning(f"SMTP delivery failed on port {port}: {e}")

    return {
        "sent": False,
        "error": str(last_error) if last_error else "Failed to connect to SMTP server."
    }

def send_password_reset_email(to_email: str, user_name: str, role: str, reset_token: str, host_url: str) -> dict:
    """
    Backwards-compatible wrapper.
    """
    base_url = host_url.rstrip("/")
    reset_url = f"{base_url}/verify-otp?token={reset_token}"
    return send_password_reset_otp(to_email=to_email, user_name=user_name, role=role, otp="000000", reset_url=reset_url)
