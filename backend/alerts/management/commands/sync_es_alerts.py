"""Sync Elasticsearch alerts into Postgres.

Usage examples:
- python manage.py sync_es_alerts
- python manage.py sync_es_alerts --size 500

This is the recommended entry point for manually validating ES->DB sync.
"""

from django.core.management.base import BaseCommand

from alerts.tasks import sync_es_alerts_to_db


class Command(BaseCommand):
    help = "Fetch alerts from Elasticsearch and upsert into alerts_alert"

    def add_arguments(self, parser):
        parser.add_argument('--size', dest='size', type=int, default=100)
        parser.add_argument(
            '--force-config',
            dest='force_config',
            action='store_true',
            help='Use configured ESIntegrationConfig even if disabled',
        )

    def handle(self, *args, **options):
        size = options.get('size')
        force_config = bool(options.get('force_config'))
        result = sync_es_alerts_to_db(size=size, force_config=force_config)
        self.stdout.write(self.style.SUCCESS(str(result)))

