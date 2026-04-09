"""
Workflow Management Commands

Management command to initialize default action templates.
"""
from django.core.management.base import BaseCommand
from workflows.models import ActionTemplate
from workflows.actions import ActionRegistry


class Command(BaseCommand):
    help = 'Initialize default action templates from the action registry'

    def handle(self, *args, **options):
        actions = ActionRegistry.get_action_info()
        created_count = 0
        updated_count = 0

        for action_info in actions:
            template, created = ActionTemplate.objects.update_or_create(
                action_type=action_info['action_type'],
                defaults={
                    'name': action_info['name'],
                    'description': action_info['description'],
                    'category': action_info['category'],
                    'config_schema': action_info['config_schema'],
                    'is_active': True,
                }
            )

            if created:
                created_count += 1
                self.stdout.write(self.style.SUCCESS(f"Created: {template.name}"))
            else:
                updated_count += 1
                self.stdout.write(f"Updated: {template.name}")

        self.stdout.write(self.style.SUCCESS(
            f"\nDone! Created {created_count}, Updated {updated_count} action templates."
        ))

