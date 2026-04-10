"""
Management command to load sample workflows.

Usage:
    python manage.py load_sample_workflows
    python manage.py load_sample_workflows --email user@example.com
    python manage.py load_sample_workflows --email user1@example.com,user2@example.com
"""
from django.core.management.base import BaseCommand
from workflows.models import Workflow, WorkflowStep


class Command(BaseCommand):
    help = 'Load sample workflows into the database'

    def add_arguments(self, parser):
        parser.add_argument(
            '--email',
            type=str,
            default='security-team@example.com',
            help='Comma-separated list of recipient email addresses for alert notifications'
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force recreate the workflow if it already exists'
        )

    def handle(self, *args, **options):
        email_list = [e.strip() for e in options['email'].split(',') if e.strip()]
        force = options['force']

        self.stdout.write(self.style.NOTICE('Loading sample workflows...'))

        # Create High/Critical Alert Email Notification workflow
        self.create_high_alert_email_workflow(email_list, force)

        self.stdout.write(self.style.SUCCESS('Sample workflows loaded successfully!'))

    def create_high_alert_email_workflow(self, recipient_emails, force=False):
        """Create the High/Critical Alert Email Notification workflow."""
        workflow_name = 'High/Critical Alert Email Notification'

        # Check if workflow already exists
        existing = Workflow.objects.filter(name=workflow_name).first()
        if existing:
            if force:
                self.stdout.write(f'  Deleting existing workflow: {workflow_name}')
                existing.delete()
            else:
                self.stdout.write(self.style.WARNING(
                    f'  Workflow already exists: {workflow_name} (use --force to recreate)'
                ))
                return existing

        # Create the workflow
        workflow = Workflow.objects.create(
            name=workflow_name,
            description=(
                'Automatically sends email notification when a high or critical severity alert is received. '
                'Customize the recipient email addresses in the Send Email step.'
            ),
            trigger_type='alert',
            trigger_conditions={
                'severity': ['high', 'critical']
            },
            is_active=True,
            is_draft=False,
            version=1,
            tags=['alert', 'notification', 'email', 'high-severity', 'sample'],
            edges=[
                {
                    'id': 'edge_start_condition',
                    'source': 'start',
                    'target': 'step_check_severity'
                },
                {
                    'id': 'edge_condition_true',
                    'source': 'step_check_severity',
                    'target': 'step_send_email',
                    'sourceHandle': 'true',
                    'label': 'Yes'
                },
                {
                    'id': 'edge_condition_false',
                    'source': 'step_check_severity',
                    'target': 'step_log_skip',
                    'sourceHandle': 'false',
                    'label': 'No'
                },
                {
                    'id': 'edge_email_end',
                    'source': 'step_send_email',
                    'target': 'end'
                },
                {
                    'id': 'edge_skip_end',
                    'source': 'step_log_skip',
                    'target': 'end'
                }
            ]
        )

        # Step 1: Check severity (condition)
        WorkflowStep.objects.create(
            workflow=workflow,
            order=0,
            name='Check Severity',
            node_type='condition',
            position_x=250,
            position_y=100,
            action_type='condition',
            action_config={
                'left': '{{trigger.data.severity}}',
                'operator': 'contains',
                'right': 'high,critical'
            },
            condition={
                'logic': 'OR',
                'groups': [
                    {
                        'logic': 'OR',
                        'rules': [
                            {'field': '{{trigger.data.severity}}', 'operator': 'equals', 'value': 'high'},
                            {'field': '{{trigger.data.severity}}', 'operator': 'equals', 'value': 'critical'}
                        ]
                    }
                ]
            },
            timeout_seconds=30,
            on_failure='stop',
            is_active=True
        )

        # Step 2: Send Email (when severity is high/critical)
        email_body = '''
<html>
<body style="font-family: Arial, sans-serif; padding: 20px;">
    <h2 style="color: #d32f2f;">🚨 Security Alert Notification</h2>
    
    <table style="border-collapse: collapse; width: 100%; max-width: 600px;">
        <tr style="background-color: #f5f5f5;">
            <td style="padding: 10px; border: 1px solid #ddd;"><strong>Alert Name</strong></td>
            <td style="padding: 10px; border: 1px solid #ddd;">{{trigger.data.alert_name}}</td>
        </tr>
        <tr>
            <td style="padding: 10px; border: 1px solid #ddd;"><strong>Severity</strong></td>
            <td style="padding: 10px; border: 1px solid #ddd; color: #d32f2f; font-weight: bold;">
                {{trigger.data.severity}}
            </td>
        </tr>
        <tr style="background-color: #f5f5f5;">
            <td style="padding: 10px; border: 1px solid #ddd;"><strong>Source IP</strong></td>
            <td style="padding: 10px; border: 1px solid #ddd;">{{trigger.data.source_ip}}</td>
        </tr>
        <tr>
            <td style="padding: 10px; border: 1px solid #ddd;"><strong>Destination IP</strong></td>
            <td style="padding: 10px; border: 1px solid #ddd;">{{trigger.data.dest_ip}}</td>
        </tr>
        <tr style="background-color: #f5f5f5;">
            <td style="padding: 10px; border: 1px solid #ddd;"><strong>Event Time</strong></td>
            <td style="padding: 10px; border: 1px solid #ddd;">{{trigger.data.timestamp}}</td>
        </tr>
        <tr>
            <td style="padding: 10px; border: 1px solid #ddd;"><strong>Description</strong></td>
            <td style="padding: 10px; border: 1px solid #ddd;">{{trigger.data.description}}</td>
        </tr>
    </table>
    
    <p style="margin-top: 20px;">
        <a href="{{trigger.data.alert_url}}" 
           style="background-color: #1976d2; color: white; padding: 10px 20px; 
                  text-decoration: none; border-radius: 4px;">
            View Alert Details
        </a>
    </p>
    
    <hr style="margin-top: 30px; border: none; border-top: 1px solid #ddd;">
    <p style="color: #666; font-size: 12px;">
        This is an automated message from the SIEM platform.
    </p>
</body>
</html>
        '''

        WorkflowStep.objects.create(
            workflow=workflow,
            order=1,
            name='Send Alert Email',
            node_type='action',
            position_x=100,
            position_y=250,
            action_type='send_email',
            action_config={
                'to': recipient_emails,
                'subject': '[SIEM Alert] {{trigger.data.severity}} - {{trigger.data.alert_name}}',
                'body': email_body.strip(),
                'is_html': True
            },
            timeout_seconds=60,
            on_failure='continue',
            retry_count=2,
            is_active=True
        )

        # Step 3: Log skip (when severity is not high/critical)
        WorkflowStep.objects.create(
            workflow=workflow,
            order=2,
            name='Log Skipped Alert',
            node_type='action',
            position_x=400,
            position_y=250,
            action_type='log',
            action_config={
                'message': 'Alert skipped - severity "{{trigger.data.severity}}" does not require email notification',
                'level': 'info'
            },
            timeout_seconds=30,
            on_failure='continue',
            is_active=True
        )

        self.stdout.write(self.style.SUCCESS(f'  Created workflow: {workflow_name}'))
        self.stdout.write(f'    ID: {workflow.id}')
        self.stdout.write(f'    Recipients: {", ".join(recipient_emails)}')
        self.stdout.write(f'    Steps: {workflow.steps.count()}')

        return workflow

