"""
Workflows App Configuration

This app provides SOAR (Security Orchestration, Automation and Response)
workflow capabilities using Django 6.0's built-in task system.
"""
from django.apps import AppConfig


class WorkflowsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'workflows'
    verbose_name = 'SOAR Workflows'

    def ready(self):
        # Import signals to register them
        try:
            import workflows.signals  # noqa
        except ImportError:
            pass

