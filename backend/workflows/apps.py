"""
Workflows App Configuration

This app provides SOAR workflows and starts progress consumption at runtime.
"""
from django.apps import AppConfig
from django.core.signals import request_started


class WorkflowsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'workflows'
    verbose_name = 'SOAR Workflows'

    def ready(self):
        try:
            from . import signals  # noqa: F401
        except ImportError:
            pass

        from .runtime import ensure_started

        request_started.connect(
            ensure_started, dispatch_uid='workflows.start_event_consumer', weak=False,
        )

