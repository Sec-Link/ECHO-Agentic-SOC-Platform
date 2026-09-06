"""Django background task used to submit event-triggered workflows."""
from __future__ import annotations

import logging
from typing import Optional

from django.tasks import task

logger = logging.getLogger(__name__)


@task(queue_name="default")
def trigger_workflows_for_event_task(
    trigger_type: str,
    trigger_source: str,
    trigger_data: dict,
    executed_by_id: Optional[int] = None,
) -> dict:
    """Find all active workflows matching *trigger_type* and execute them."""
    from django.contrib.auth.models import User

    from ..engine import execute_workflow
    from ..models import Workflow
    from ..signals import _matches_conditions

    executed_by: Optional[User] = None
    if executed_by_id is not None:
        try:
            executed_by = User.objects.get(pk=executed_by_id)
        except User.DoesNotExist:
            logger.warning(
                "trigger_workflows_for_event_task: user %s not found", executed_by_id
            )

    workflows = Workflow.objects.filter(
        trigger_type=trigger_type,
        is_active=True,
        is_draft=False,
        execution_engine='prefect',
    )

    triggered = 0
    skipped = 0

    for workflow in workflows:
        if not _matches_conditions(trigger_data, workflow.trigger_conditions):
            skipped += 1
            continue

        try:
            execution = execute_workflow(
                workflow=workflow,
                trigger_data=trigger_data,
                trigger_source=trigger_source,
                executed_by=executed_by,
            )
            triggered += 1
            logger.info(
                "Auto-triggered workflow '%s' (execution %s, status=%s)",
                workflow.name,
                execution.id,
                execution.status,
            )
        except Exception as exc:
            logger.exception(
                "Failed to trigger workflow '%s': %s", workflow.name, exc
            )

    return {"triggered": triggered, "skipped": skipped}


__all__ = [
    "trigger_workflows_for_event_task",
]

