from __future__ import annotations

from django.core.management.base import BaseCommand

from accounts.services import ReadonlyRoleService


class Command(BaseCommand):
    help = "Sync readonly_user group with view_* permissions for panel apps."

    def handle(self, *args, **options):
        group = ReadonlyRoleService.ensure_readonly_group()
        self.stdout.write(self.style.SUCCESS(f"Readonly group synced: {group.name}"))
