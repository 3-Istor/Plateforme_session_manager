import smtplib
from email.message import EmailMessage

from ..config import Settings


def send_manager_email(settings: Settings, subject: str, body: str) -> bool:
    if not settings.smtp_host or not settings.smtp_from:
        return False
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.smtp_from
    message["To"] = str(settings.manager_email)
    message.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as client:
        if settings.smtp_use_tls:
            client.starttls()
        if settings.smtp_username:
            client.login(settings.smtp_username, settings.smtp_password)
        client.send_message(message)
    return True
