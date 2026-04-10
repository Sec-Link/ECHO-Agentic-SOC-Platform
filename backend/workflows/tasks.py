"""
Workflow Background Tasks

Defines Django 6.0 background task functions for workflow execution.

Django 6.0 ships with ``django.tasks`` which provides a ``@task`` decorator
and a pluggable backend system.  The default backend configured in settings
(``ImmediateBackend``) executes tasks synchronously inside the same request
cycle.  Swapping to an asynchronous backend (e.g. a Celery bridge) in the
future requires *only* a settings change — the task functions themselves stay
the same.

Usage
-----
Enqueue a workflow execution from anywhere in the codebase::

    from workflows.tasks import run_workflow_task
    task_result = run_workflow_task.enqueue(str(execution_id))

The returned ``TaskResult`` carries the task id, status, and (after
completion) the return value or error details.
"""
import logging
from typing import Optional

from django.tasks import task

logger = logging.getLogger(__name__)


@task(queue_name="default")
def run_workflow_task(execution_id: str) -> dict:
    """
    Execute a WorkflowExecution identified by *execution_id*.

    This function is the single entry-point used by both the manual ``execute``
    API action and the automatic signal-based triggers.  Keeping execution
    logic inside ``WorkflowEngine`` (``engine.py``) means this wrapper stays
    thin and easy to test.

    Args:
        execution_id: String UUID of the ``WorkflowExecution`` record to run.

    Returns:
        A dict with ``execution_id`` and final ``status``, e.g.::

            {"execution_id": "...", "status": "completed"}

    Raises:
        WorkflowExecution.DoesNotExist: If no record matches *execution_id*.
        Exception: Re-raised from ``WorkflowEngine.run()`` on failure (the
            engine already marks the execution as ``failed`` before raising).
    """
    from .models import WorkflowExecution
    from .engine import WorkflowEngine

    logger.info("Background task: starting workflow execution %s", execution_id)

    execution = WorkflowExecution.objects.select_related("workflow").get(
        id=execution_id
    )

    engine = WorkflowEngine(execution)
    try:
        engine.run()
    except Exception as exc:
        # WorkflowEngine.run() already persists the failed status.
        # Log here so the task backend captures the traceback as well.
        logger.exception(
            "Background task: workflow execution %s failed: %s", execution_id, exc
        )
        raise

    logger.info(
        "Background task: workflow execution %s finished with status '%s'",
        execution_id,
        execution.status,
    )
    return {"execution_id": execution_id, "status": execution.status}


@task(queue_name="default")
def trigger_workflows_for_event_task(
    trigger_type: str,
    trigger_source: str,
    trigger_data: dict,
    executed_by_id: Optional[int] = None,
) -> dict:
    """
    Find all active workflows matching *trigger_type* and enqueue them.

    This task is invoked by signal handlers so that automatic triggers do not
    block the HTTP request that caused the signal.

    Args:
        trigger_type: Workflow trigger type, e.g. ``"ticket_created"``.
        trigger_source: Human-readable source string, e.g. ``"ticket:SEC-001"``.
        trigger_data: Payload forwarded to every matching workflow.
        executed_by_id: Optional Django User pk to record as the executor.

    Returns:
        A dict summarising how many workflows were enqueued::

            {"triggered": 2, "skipped": 0}
    """
    from django.contrib.auth.models import User

    from .models import Workflow, WorkflowExecution
    from .signals import _matches_conditions

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
    )

    triggered = 0
    skipped = 0

    for workflow in workflows:
        if not _matches_conditions(trigger_data, workflow.trigger_conditions):
            skipped += 1
            continue

        try:
            execution = WorkflowExecution.objects.create(
                workflow=workflow,
                trigger_source=trigger_source,
                trigger_data=trigger_data,
                status="pending",
                executed_by=executed_by,
            )
            # Enqueue the actual execution as a nested task
            run_workflow_task.enqueue(str(execution.id))
            triggered += 1
            logger.info(
                "Auto-triggered workflow '%s' (execution %s)",
                workflow.name,
                execution.id,
            )
        except Exception as exc:
            logger.exception(
                "Failed to enqueue workflow '%s': %s", workflow.name, exc
            )

    return {"triggered": triggered, "skipped": skipped}

