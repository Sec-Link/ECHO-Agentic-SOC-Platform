from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from ...persistence import persist_workflow_definition
from ...sample_workflows.critical_ticket_email_workflow import build_critical_ticket_email_workflow_definition
from ...views import WorkflowViewSet
from ... import prefect_client

User = get_user_model()


class Command(BaseCommand):
    help = 'Import the critical ticket email playbook as a durable Django workflow.'

    def add_arguments(self, parser):
        parser.add_argument('--recipient', default='achen@seclink.info', help='Recipient email address')
        parser.add_argument('--username', default='', help='Existing username to own the workflow')
        parser.add_argument('--activate', action='store_true', help='Mark the workflow active after import')

    def handle(self, *args, **options):
        recipient = (options.get('recipient') or '').strip()
        if not recipient:
            raise CommandError('--recipient is required')

        username = (options.get('username') or '').strip()
        created_by = None
        if username:
            created_by = User.objects.filter(username=username).first()
            if created_by is None:
                raise CommandError(f'User not found: {username}')

        workflow_definition, _ = build_critical_ticket_email_workflow_definition(recipient=recipient)
        workflow = persist_workflow_definition(
            workflow_definition=workflow_definition,
            created_by=created_by,
            trigger_type='ticket_created',
            trigger_conditions={},
            is_active=bool(options.get('activate')),
            is_draft=not bool(options.get('activate')),
            tags=['playbook', 'email', 'critical-ticket'],
            update_existing=True,
        )

        if workflow.prefect_deployment_id:
            try:
                deployment = prefect_client.get_deployment(workflow.prefect_deployment_id)
                if deployment.get('entrypoint') != 'backend/workflows/prefect/flow.py:run_soar_workflow':
                    workflow.prefect_deployment_id = ''
                    workflow.save(update_fields=['prefect_deployment_id'])
            except prefect_client.PrefectAPIError:
                workflow.prefect_deployment_id = ''
                workflow.save(update_fields=['prefect_deployment_id'])

        workflow_viewset = WorkflowViewSet()
        workflow_viewset._ensure_prefect_deployment(workflow)
        workflow_viewset._sync_default_schedule(workflow)
        workflow_viewset._sync_prefect_deployment(workflow)

        self.stdout.write(self.style.SUCCESS(f'Imported workflow: {workflow.name}'))
        self.stdout.write(f'  id={workflow.id}')
        self.stdout.write(f'  execution_engine={workflow.execution_engine}')
        self.stdout.write(f'  is_active={workflow.is_active}')
        self.stdout.write(f'  is_draft={workflow.is_draft}')
        self.stdout.write(f'  steps={workflow.steps.count()}')
