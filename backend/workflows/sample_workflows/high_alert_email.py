"""
Sample Workflow: High/Critical Alert Email Notification

This script creates a sample workflow that sends email notifications
when high or critical severity alerts are received.

Usage:
    python manage.py shell < workflows/sample_workflows/high_alert_email.py

Or run interactively:
    python manage.py shell
    >>> exec(open('workflows/sample_workflows/high_alert_email.py').read())
"""
import os
import sys
import django

# Setup Django if running as standalone script
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'siem_project.settings')
    django.setup()

from workflows.models import Workflow, WorkflowStep


def create_high_alert_email_workflow(recipient_emails=None):
    """
    Create a sample workflow for high/critical alert email notification.

    Args:
        recipient_emails: List of email addresses to receive notifications.
                         Defaults to ['security-team@example.com']

    Returns:
        The created Workflow instance
    """
    if recipient_emails is None:
        recipient_emails = ['security-team@example.com']

    # Check if workflow already exists
    existing = Workflow.objects.filter(name='High/Critical Alert Email Notification').first()
    if existing:
        print(f"Workflow already exists: {existing.name} (ID: {existing.id})")
        return existing

    # Create the workflow
    workflow = Workflow.objects.create(
        name='High/Critical Alert Email Notification',
        description='Automatically sends email notification when a high or critical severity alert is received. '
                   'Customize the recipient email addresses in the Send Email step.',
        trigger_type='alert',
        trigger_conditions={
            'severity': ['high', 'critical']
        },
        is_active=True,
        is_draft=False,
        version=1,
        tags=['alert', 'notification', 'email', 'high-severity'],
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
            'subject': '[SIEM Alert] {{trigger.data.severity|upper}} - {{trigger.data.alert_name}}',
            'body': '''
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
            ''',
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

    print(f"✅ Created workflow: {workflow.name}")
    print(f"   ID: {workflow.id}")
    print(f"   Trigger: {workflow.trigger_type}")
    print(f"   Recipients: {', '.join(recipient_emails)}")
    print(f"   Steps: {workflow.steps.count()}")

    return workflow


# Run when executed directly
if __name__ == "__main__" or '__file__' not in dir():
    # Example: Create workflow with custom email addresses
    # Modify this list to set your own recipient emails
    custom_emails = [
        'security-team@example.com',
        'soc-analyst@example.com'
    ]

    workflow = create_high_alert_email_workflow(recipient_emails=custom_emails)

    print("\n📧 To modify recipient emails:")
    print("   1. Go to Workflows in the SIEM UI")
    print("   2. Click on 'High/Critical Alert Email Notification'")
    print("   3. Edit the 'Send Alert Email' step")
    print("   4. Update the 'Recipients' field")

