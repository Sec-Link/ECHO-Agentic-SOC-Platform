"""Periodic scheduler for alerts ES sync."""

from __future__ import annotations

import json
import time

from django.core.management.base import BaseCommand

from alerts.tasks import get_or_create_alert_sync_schedule, run_alert_sync_by_schedule


class Command(BaseCommand):
    help = 'Run scheduled ES->alerts sync based on AlertSyncSchedule settings.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--run-once',
            action='store_true',
            help='Execute one cycle immediately and exit.',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Run even when schedule.enabled is false.',
        )
        parser.add_argument(
            '--poll-seconds',
            type=int,
            default=5,
            help='Fallback sleep seconds while waiting for next interval.',
        )

    def _run_once(self, *, force: bool):
        result = run_alert_sync_by_schedule(force=force)
        self.stdout.write(json.dumps(result, default=str))
        return result

    def handle(self, *args, **options):
        run_once = bool(options.get('run_once'))
        force = bool(options.get('force'))
        fallback_poll = max(1, int(options.get('poll_seconds') or 5))

        if run_once:
            self._run_once(force=force)
            return

        self.stdout.write(self.style.SUCCESS('alerts_scheduler started'))
        try:
            while True:
                schedule = get_or_create_alert_sync_schedule()
                if schedule.enabled or force:
                    self._run_once(force=force)
                    sleep_for = max(10, int(schedule.interval_seconds or 300))
                else:
                    self.stdout.write('schedule disabled; waiting...')
                    sleep_for = fallback_poll
                time.sleep(sleep_for)
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING('alerts_scheduler stopped by user'))

