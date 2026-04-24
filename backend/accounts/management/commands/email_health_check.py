from __future__ import annotations

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = "Send a test email to verify SMTP/email configuration."

    def add_arguments(self, parser):
        parser.add_argument("--to", required=True, help="Recipient email for health check")
        parser.add_argument("--subject", default="SIEM Email Health Check", help="Optional email subject")

    def handle(self, *args, **options):
        to = (options.get("to") or "").strip()
        subject = options.get("subject") or "SIEM Email Health Check"
        if not to:
            raise CommandError("--to is required")

        body = (
            "This is a health check email from SIEM backend.\n"
            f"UTC time: {timezone.now().isoformat()}\n"
            f"EMAIL_HOST: {getattr(settings, 'EMAIL_HOST', '')}\n"
            f"EMAIL_PORT: {getattr(settings, 'EMAIL_PORT', '')}\n"
            f"DEFAULT_FROM_EMAIL: {getattr(settings, 'DEFAULT_FROM_EMAIL', '')}\n"
        )

        try:
            sent = send_mail(
                subject=subject,
                message=body,
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
                recipient_list=[to],
                fail_silently=False,
            )
        except Exception as exc:
            raise CommandError(f"Email health check failed: {exc}") from exc

        if sent != 1:
            raise CommandError(f"Email health check failed, send_mail returned {sent}")

        self.stdout.write(self.style.SUCCESS(f"Email health check passed, sent to {to}"))
