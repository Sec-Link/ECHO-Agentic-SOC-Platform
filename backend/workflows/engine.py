"""Django control-plane entry point for workflow executions."""
from __future__ import annotations

from typing import Any, Dict, Optional

from django.utils import timezone

from .models import Workflow, WorkflowExecution


class WorkflowExecutionUnavailable(ValueError):
    """Raised before an execution is created when a workflow cannot run."""


def ensure_workflow_is_runnable(workflow: Workflow) -> tuple[int, int]:
    if not workflow.is_active:
        raise WorkflowExecutionUnavailable('Workflow must be active before execution.')
    if workflow.execution_engine != 'prefect':
        raise WorkflowExecutionUnavailable(
            'Local execution is unavailable; publish the workflow to run it with Prefect.'
        )

    from .publisher import load_current_published_manifest

    try:
        pointer, manifest = load_current_published_manifest(workflow)
    except FileNotFoundError as exc:
        raise WorkflowExecutionUnavailable(
            'Workflow must be published before execution.'
        ) from exc
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise WorkflowExecutionUnavailable(
            'Published workflow manifest is unavailable or invalid; publish the workflow again.'
        ) from exc
    return int(pointer['current_version']), len(manifest['steps'])


def execute_workflow(
    workflow: Workflow,
    trigger_data: Optional[Dict[str, Any]] = None,
    trigger_source: str = 'manual',
    executed_by=None,
) -> WorkflowExecution:
    """Create an execution and submit its published snapshot to Prefect."""
    workflow_version, total_steps = ensure_workflow_is_runnable(workflow)
    execution = WorkflowExecution.objects.create(
        workflow=workflow,
        workflow_version=workflow_version,
        trigger_source=trigger_source,
        trigger_data=trigger_data or {},
        status='pending',
        total_steps=total_steps,
        executed_by=executed_by,
    )

    from . import prefect_client, prefect_dispatcher

    deployment_id = workflow.prefect_deployment_id or None
    if not prefect_client.is_configured(deployment_id):
        execution.status = 'failed'
        execution.error_message = 'Prefect not configured (PREFECT_API_URL / PREFECT_DEPLOYMENT_ID missing).'
        execution.completed_at = timezone.now()
        execution.save(update_fields=['status', 'error_message', 'completed_at'])
        return execution

    return prefect_dispatcher.submit(execution)
