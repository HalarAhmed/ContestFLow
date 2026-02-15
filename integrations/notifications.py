"""Email notifications via SMTP."""
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from config import settings
from utils.logging import get_logger

logger = get_logger(__name__)


def send_email(to: str, subject: str, body_text: str, body_html: Optional[str] = None) -> bool:
    """Send email via SMTP. Uses settings for server; to can override recipient."""
    if not settings.SMTP_HOST or not settings.SMTP_USER:
        logger.warning("SMTP not configured; skipping send to %s", to)
        return False
    recipient = to or settings.NOTIFICATION_EMAIL or settings.SMTP_USER
    if not recipient:
        logger.warning("No recipient for email")
        return False
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.SMTP_USER
    msg["To"] = recipient
    msg.attach(MIMEText(body_text, "plain"))
    if body_html:
        msg.attach(MIMEText(body_html, "html"))
    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            server.starttls()
            if settings.SMTP_PASSWORD:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.sendmail(settings.SMTP_USER, recipient, msg.as_string())
        logger.info("Email sent to %s: %s", recipient, subject[:50])
        return True
    except Exception as e:
        logger.exception("Failed to send email: %s", e)
        return False


def notify_contest_new(contest_platform: str, contest_name: str, start_time_str: str, duration_str: str, to: Optional[str] = None) -> bool:
    subject = f"[CP Assistant] New contest: {contest_name}"
    body = f"A new contest has been announced.\n\nPlatform: {contest_platform}\nContest: {contest_name}\nStart: {start_time_str}\nDuration: {duration_str}\n\nDo you want to register? Reply or use the dashboard to register."
    return send_email(to or "", subject, body)


def notify_contest_reminder(contest_name: str, when: str, start_time_str: str, to: Optional[str] = None) -> bool:
    subject = f"[CP Assistant] Contest in {when}: {contest_name}"
    body = f"Reminder: {contest_name} starts at {start_time_str} ({when})."
    return send_email(to or "", subject, body)


def notify_registration_result(success: bool, contest_name: str, message: str, to: Optional[str] = None) -> bool:
    subject = f"[CP Assistant] Registration {'success' if success else 'failed'}: {contest_name}"
    body = message
    return send_email(to or "", subject, body)


def notify_post_contest_report(contest_name: str, summary: str, to: Optional[str] = None) -> bool:
    subject = f"[CP Assistant] Post-contest report: {contest_name}"
    body = summary
    return send_email(to or "", subject, body)
