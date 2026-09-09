from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from typing import Any, Dict

from .base import ActionResult, BaseAction


class SendEmailAction(BaseAction):
    name = "Send Email"
    description = "Send an email notification"
    category = "notification"
    config_schema = {
        "type": "object",
        "properties": {
            "to": {"type": "array", "items": {"type": "string"}, "description": "List of recipient emails"},
            "subject": {"type": "string", "description": "Email subject"},
            "body": {"type": "string", "description": "Email body"},
            "is_html": {"type": "boolean", "default": False},
        },
        "required": ["to", "subject", "body"],
    }

    def execute(self, config: Dict[str, Any], context: Dict[str, Any]) -> ActionResult:
        recipients = config.get("to", [])
        if isinstance(recipients, str):
            recipients = [recipients]
        subject = self.resolve_variables(config.get("subject", ""), context)
        body = self.resolve_variables(config.get("body", ""), context)
        host = os.getenv("EMAIL_HOST", "")
        port = int(os.getenv("EMAIL_PORT", "587"))
        username = os.getenv("EMAIL_HOST_USER", "")
        password = os.getenv("EMAIL_HOST_PASSWORD", "")
        sender = os.getenv("DEFAULT_FROM_EMAIL", username or "noreply@localhost")
        use_tls = os.getenv("EMAIL_USE_TLS", "true").lower() in {"1", "true", "yes"}
        use_ssl = os.getenv("EMAIL_USE_SSL", "false").lower() in {"1", "true", "yes"}
        timeout = int(os.getenv("EMAIL_TIMEOUT", "15"))
        message = EmailMessage()
        message["Subject"], message["From"], message["To"] = subject, sender, ", ".join(recipients)
        if config.get("is_html"):
            message.set_content("This message contains HTML.")
            message.add_alternative(body, subtype="html")
        else:
            message.set_content(body)
        try:
            smtp_type = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
            with smtp_type(host, port, timeout=timeout) as smtp:
                if use_tls and not use_ssl:
                    smtp.starttls()
                if username:
                    smtp.login(username, password)
                smtp.send_message(message)
            return ActionResult(True, {"sent_to": recipients, "subject": subject, "from_email": sender}, logs=f"Email sent to {', '.join(recipients)}")
        except Exception as exc:
            return ActionResult(False, error=str(exc), logs=f"Failed to send email to {recipients}: {exc}")

