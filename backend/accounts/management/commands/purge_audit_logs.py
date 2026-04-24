from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import AuditLog


class Command(BaseCommand):
    help = "Purge audit logs older than retention period."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=int(getattr(settings, "AUDIT_LOG_RETENTION_DAYS", 90)),
            help="Retention days to keep (default from AUDIT_LOG_RETENTION_DAYS).",
        )

    def handle(self, *args, **options):
        days = max(1, int(options["days"]))
        cutoff = timezone.now() - timedelta(days=days)
        deleted, _ = AuditLog.objects.filter(created_at__lt=cutoff).delete()
        self.stdout.write(self.style.SUCCESS(f"Purged {deleted} audit logs older than {days} days."))
