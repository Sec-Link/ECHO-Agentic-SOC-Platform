"""Django-side bridge to Prefect and the independent worker runtime."""
from __future__ import annotations

import logging
from typing import Any, Dict

from django.db import IntegrityError, transaction
from django.utils import timezone

from . import prefect_client
from .models import StepExecution, Workflow, WorkflowExecution, WorkflowSchedule

logger = logging.getLogger(__name__)


def build_run_envelope(
    workflow: Workflow,
    *,
    execution_id: str | None,
    trigger_source: str,
    trigger_data: Dict[str, Any],
    workflow_version: int | None = None,
) -> tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    from .publisher import load_current_published_manifest, load_published_manifest

    if workflow_version is None:
        pointer, definition = load_current_published_manifest(workflow)
        workflow_version = int(pointer['current_version'])
    else:
        definition = load_published_manifest(workflow, workflow_version)
        meta = definition['_meta']
        pointer = {
            'current_version': workflow_version,
            'published_at': meta.get('published_at'),
        }
    run = {
        'schema_version': 1,
        'execution_id': execution_id,
        'workflow': {
            'id': str(workflow.id),
            'version': workflow_version,
            'definition': definition,
        },
        'trigger': {
            'source': trigger_source or 'manual',
            'data': trigger_data or {},
        },
    }
    return run, pointer, definition


def submit(execution: WorkflowExecution) -> WorkflowExecution:
    try:
        run, pointer, definition = build_run_envelope(
            execution.workflow,
            execution_id=str(execution.id),
            trigger_source=execution.trigger_source,
            trigger_data=execution.trigger_data or {},
            workflow_version=execution.workflow_version,
        )
        result = prefect_client.create_flow_run(
            parameters={'run': run},
            name=f"{execution.workflow.name} :: {execution.id}",
            tags=['soar', f'workflow:{execution.workflow.id}'],
            deployment_id=(execution.workflow.prefect_deployment_id or '').strip() or None,
            idempotency_key=str(execution.id),
        )
    except (OSError, ValueError, TypeError, KeyError, prefect_client.PrefectAPIError) as exc:
        logger.exception('Failed to dispatch workflow execution %s', execution.id)
        execution.status = 'failed'
        execution.error_message = f'Prefect dispatch failed: {exc}'
        execution.completed_at = timezone.now()
        execution.save(update_fields=['status', 'error_message', 'completed_at'])
        return execution

    execution.task_result_id = str(result.get('id') or '')
    state = result.get('state') or {}
    execution.status = prefect_client.map_state_to_status(state.get('type') or result.get('state_type'))
    execution.total_steps = len(definition.get('steps') or [])
    execution.context = {
        **(execution.context or {}),
        'workflow_version': execution.workflow_version,
        'published_at': pointer.get('published_at'),
    }
    execution.save(update_fields=['task_result_id', 'status', 'total_steps', 'context'])
    return execution


def cancel(execution: WorkflowExecution) -> WorkflowExecution:
    if execution.task_result_id:
        prefect_client.cancel_flow_run(execution.task_result_id)
    return execution


def sync_status(execution: WorkflowExecution) -> WorkflowExecution:
    if not execution.task_result_id or (
        execution.status in prefect_client.TERMINAL_STATUSES
        and not (execution.status == 'failed' and not execution.error_message)
    ):
        return execution
    try:
        flow_run = prefect_client.get_flow_run(execution.task_result_id)
    except prefect_client.PrefectAPIError as exc:
        logger.warning('Prefect sync failed for execution %s: %s', execution.id, exc)
        return execution
    state = flow_run.get('state') or {}
    status = prefect_client.map_state_to_status(state.get('type') or flow_run.get('state_type'))
    execution.status = status
    if status == 'running' and not execution.started_at:
        execution.started_at = timezone.now()
    if status in prefect_client.TERMINAL_STATUSES and not execution.completed_at:
        execution.completed_at = timezone.now()
    if status == 'failed' and not execution.error_message:
        execution.error_message = str(
            state.get('message') or flow_run.get('state_name') or 'Prefect flow run failed.'
        )
    execution.save(update_fields=['status', 'started_at', 'completed_at', 'error_message'])
    return execution


@transaction.atomic
def register_runtime_execution(payload: Dict[str, Any]) -> WorkflowExecution:
    from .publisher import load_published_manifest

    flow_run_id = str(payload['prefect_flow_run_id'])
    workflow_version = int(payload['workflow_version'])
    workflow = Workflow.objects.get(id=payload['workflow_id'], execution_engine='prefect', is_active=True)
    try:
        definition = load_published_manifest(workflow, workflow_version)
    except (OSError, TypeError, ValueError, KeyError) as exc:
        raise ValueError(
            f'Published workflow version {workflow_version} is unavailable or invalid.'
        ) from exc

    existing = WorkflowExecution.objects.select_for_update().filter(task_result_id=flow_run_id).first()
    if existing:
        if str(existing.workflow_id) != str(payload['workflow_id']):
            raise ValueError('prefect_flow_run_id is already registered to another workflow')
        if existing.workflow_version != workflow_version:
            raise ValueError('prefect_flow_run_id is already registered to another workflow version')
        return existing
    try:
        with transaction.atomic():
            return WorkflowExecution.objects.create(
                workflow=workflow,
                workflow_version=workflow_version,
                trigger_source=str(payload.get('trigger_source') or 'schedule'),
                trigger_data=payload.get('trigger_data') or {},
                task_result_id=flow_run_id,
                status='pending',
                total_steps=len(definition['steps']),
                context={
                    'workflow_version': workflow_version,
                    'published_at': definition['_meta'].get('published_at'),
                },
            )
    except IntegrityError:
        existing = WorkflowExecution.objects.get(task_result_id=flow_run_id)
        if str(existing.workflow_id) != str(payload['workflow_id']):
            raise ValueError('prefect_flow_run_id is already registered to another workflow')
        if existing.workflow_version != workflow_version:
            raise ValueError('prefect_flow_run_id is already registered to another workflow version')
        return existing


@transaction.atomic
def apply_runtime_snapshot(execution: WorkflowExecution, payload: Dict[str, Any]) -> WorkflowExecution:
    from .publisher import load_published_manifest

    execution = WorkflowExecution.objects.select_for_update().select_related('workflow').get(pk=execution.pk)
    flow_run_id = str(payload['prefect_flow_run_id'])
    if execution.task_result_id and execution.task_result_id != flow_run_id:
        raise ValueError('prefect_flow_run_id does not match this execution')
    if not execution.task_result_id:
        execution.task_result_id = flow_run_id

    incoming_status = str(payload['status'])
    if execution.status == 'cancelled' and incoming_status != 'cancelled':
        return execution

    try:
        definition = load_published_manifest(execution.workflow, execution.workflow_version)
    except (OSError, TypeError, ValueError, KeyError) as exc:
        raise ValueError(
            f'Published workflow version {execution.workflow_version} is unavailable or invalid.'
        ) from exc
    manifest_steps = {
        str(step['id']): step
        for step in definition['steps']
    }
    terminal_step_statuses = {'completed', 'failed', 'skipped', 'cancelled'}
    step_results = [
        {**item, 'step_id': str(item['step_id'])}
        for item in payload.get('step_results') or []
    ]
    for item in step_results:
        step_id = str(item['step_id'])
        manifest_step = manifest_steps.get(step_id)
        if manifest_step is None:
            raise ValueError(
                f'step {step_id} does not belong to published workflow version {execution.workflow_version}'
            )
        status = str(item.get('status') or 'completed')
        now = timezone.now()
        step_execution, created = StepExecution.objects.get_or_create(
            workflow_execution=execution,
            source_step_id=step_id,
            defaults={
                'step_name': manifest_step['name'],
                'step_order': manifest_step['order'],
                'action_type': manifest_step['action_type'],
                'status': status,
                'attempt_number': max(int(item.get('attempt_number') or 1), 1),
                'started_at': now,
                'completed_at': now if status in terminal_step_statuses else None,
                'input_data': item.get('input_data') or {},
                'output_data': item.get('output_data') or {},
                'error_message': item.get('error_message') or '',
                'logs': item.get('logs') or '',
            },
        )
        if not created:
            step_execution.status = status
            step_execution.attempt_number = max(int(item.get('attempt_number') or 1), 1)
            step_execution.started_at = step_execution.started_at or now
            step_execution.completed_at = now if status in terminal_step_statuses else None
            step_execution.input_data = item.get('input_data') or {}
            step_execution.output_data = item.get('output_data') or {}
            step_execution.error_message = item.get('error_message') or ''
            step_execution.logs = item.get('logs') or ''
            step_execution.save(update_fields=[
                'status', 'attempt_number', 'started_at', 'completed_at',
                'input_data', 'output_data', 'error_message', 'logs', 'updated_at',
            ])

    execution.status = incoming_status
    execution.started_at = execution.started_at or timezone.now()
    execution.current_step = max(int(payload.get('current_step') or 0), 0)
    execution.total_steps = len(definition['steps'])
    execution.completed_steps = execution.step_executions.filter(status__in=terminal_step_statuses).count()
    execution.update_progress()
    execution.context = {
        **(payload.get('context') or {}),
        'workflow_version': execution.workflow_version,
    }
    execution.error_message = payload.get('error_message') or ''
    if incoming_status in prefect_client.TERMINAL_STATUSES:
        execution.completed_at = timezone.now()
        execution.result_data = {
            'execution_id': str(execution.id),
            'status': incoming_status,
            'step_results': step_results,
        }
        if incoming_status == 'completed':
            execution.completed_steps = execution.total_steps
            execution.update_progress()
    execution.save()
    return execution


def schedule_slug(schedule: WorkflowSchedule) -> str:
    return f'argus-workflow-{schedule.workflow_id}-{schedule.id}'


def _schedule_definition(schedule: WorkflowSchedule) -> Dict[str, Any]:
    if schedule.schedule_type == 'interval':
        return {'interval': schedule.interval_seconds or 0}
    return {'cron': schedule.cron or '', 'timezone': schedule.timezone or 'UTC'}


def sync_schedule(schedule: WorkflowSchedule) -> Dict[str, Any]:
    workflow = schedule.workflow
    if workflow.execution_engine != 'prefect':
        return {}
    deployment_override = (workflow.prefect_deployment_id or '').strip() or None
    if not prefect_client.is_configured(deployment_override):
        return {}
    deployment_id = prefect_client.resolve_deployment_id(deployment_override)
    run, _, _ = build_run_envelope(
        workflow,
        execution_id=None,
        trigger_source=schedule.trigger_source or 'schedule',
        trigger_data=schedule.trigger_data or {},
    )
    return prefect_client.upsert_deployment_schedule(
        deployment_id=deployment_id,
        slug=schedule_slug(schedule),
        schedule=_schedule_definition(schedule),
        is_active=bool(schedule.is_active),
        parameters={'run': run},
    )


def delete_schedule(schedule: WorkflowSchedule) -> None:
    workflow = schedule.workflow
    if workflow.execution_engine != 'prefect' or not prefect_client.is_configured((workflow.prefect_deployment_id or '').strip() or None):
        return
    prefect_client.delete_deployment_schedule_by_slug(
        deployment_id=prefect_client.resolve_deployment_id((workflow.prefect_deployment_id or '').strip() or None),
        slug=schedule_slug(schedule),
    )
