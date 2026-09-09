import asyncio
import os

from django.core.management.base import BaseCommand, CommandError

from workflows.event_consumer import run_forever


class Command(BaseCommand):
    help = 'Subscribe to Prefect progress/state events and persist them in Django.'

    def add_arguments(self, parser):
        parser.add_argument('--reset-checkpoint', action='store_true', help='Replay all retained Prefect events.')

    def handle(self, *args, **options):
        if not os.getenv('PREFECT_API_URL'):
            raise CommandError('PREFECT_API_URL is required; no ephemeral Prefect server will be started.')
        self.stdout.write('Waiting to consume Prefect workflow progress (Ctrl+C to stop).')
        try:
            asyncio.run(run_forever(reset_checkpoint=options['reset_checkpoint']))
        except KeyboardInterrupt:
            pass
