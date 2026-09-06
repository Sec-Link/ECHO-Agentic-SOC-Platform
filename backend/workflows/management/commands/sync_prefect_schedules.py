from django.core.management.base import BaseCommand, CommandError

from workflows import prefect_client
from workflows.models import Workflow, WorkflowSchedule
from workflows.prefect_dispatcher import sync_schedule


class Command(BaseCommand):
    help = 'Replace the legacy shared Prefect schedule and sync active workflow schedules.'

    def handle(self, *args, **options):
        schedules = list(
            WorkflowSchedule.objects.filter(
                is_active=True,
                workflow__execution_engine='prefect',
            ).select_related('workflow')
        )
        try:
            deployment_ids = {
                prefect_client.resolve_deployment_id((workflow.prefect_deployment_id or '').strip() or None)
                for workflow in Workflow.objects.filter(execution_engine='prefect')
            }
            for deployment_id in deployment_ids:
                prefect_client.delete_deployment_schedule_by_slug(
                    deployment_id=deployment_id,
                    slug='argus-workflow-schedule',
                )
            for item in schedules:
                sync_schedule(item)
        except (OSError, ValueError, prefect_client.PrefectAPIError, prefect_client.PrefectConfigError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f'Synced {len(schedules)} Prefect schedule(s).'))
