from django.core.management.base import BaseCommand

from workflows.worker_credentials import bootstrap_worker_credentials


class Command(BaseCommand):
    help = 'Initialize restricted worker access and publish its Secret reference to Prefect.'

    def handle(self, *args, **options):
        bootstrap_worker_credentials()
        self.stdout.write(self.style.SUCCESS('Workflow worker access is ready (no manual Token required).'))
