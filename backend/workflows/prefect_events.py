"""Consume Prefect events in Django; the worker never accesses the Argus database."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone as datetime_timezone
from uuid import NAMESPACE_URL, UUID, uuid5

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from . import prefect_client
from .models import Workflow, WorkflowEventCheckpoint, WorkflowExecution
from .prefect_dispatcher import apply_runtime_snapshot, register_runtime_execution, sync_status
from .serializers import RuntimeRegistrationSerializer, RuntimeSnapshotSerializer

logger = logging.getLogger(__name__)
EVENT_NAMES = [
    'argus.workflow.progress',
    'prefect.flow-run.Running', 'prefect.flow-run.Paused',
    'prefect.flow-run.Completed', 'prefect.flow-run.Failed',
    'prefect.flow-run.Crashed', 'prefect.flow-run.Cancelled',
]
CHECKPOINT = 'workflow-progress'


class ProgressEventSerializer(RuntimeSnapshotSerializer, RuntimeRegistrationSerializer):
    execution_id = serializers.UUIDField()
    sequence = serializers.IntegerField(min_value=1)

    def validate(self, attrs):
        for field in ('context', 'trigger_data'):
            if not isinstance(attrs[field], dict):
                raise serializers.ValidationError({field: 'Must be an object.'})
        return attrs


def _execution(payload):
    execution = WorkflowExecution.objects.select_for_update().filter(pk=payload['execution_id']).first()
    if execution is None:
        execution = register_runtime_execution(payload)
    if (
        str(execution.id) != str(payload['execution_id'])
        or str(execution.workflow_id) != str(payload['workflow_id'])
        or execution.workflow_version != int(payload['workflow_version'])
        or (execution.task_result_id and execution.task_result_id != str(payload['prefect_flow_run_id']))
    ):
        raise ValueError('Prefect event does not match this execution and published workflow version.')
    return execution


@transaction.atomic
def apply_event(event):
    if event.event == 'argus.workflow.progress':
        serializer = ProgressEventSerializer(data=event.payload)
        serializer.is_valid(raise_exception=True)
        payload = serializer.data
        if event.resource.id != f"argus.workflow.execution.{payload['execution_id']}":
            raise ValueError('Progress event resource does not match execution_id.')
        execution = _execution(payload)
        return apply_runtime_snapshot(execution, payload, occurred=event.occurred)

    if event.event not in EVENT_NAMES or not event.resource.id.startswith('prefect.flow-run.'):
        return None
    flow_id = str(UUID(event.resource.id.removeprefix('prefect.flow-run.')))
    execution = WorkflowExecution.objects.select_for_update().filter(task_result_id=flow_id).first()
    if execution is None:
        # Scheduled runs (including a process crash before the first custom event)
        # are registered by the server from Prefect's immutable run parameters.
        flow = prefect_client.get_flow_run(flow_id)
        parameters = flow.get('parameters') or {}
        run = parameters.get('run') if isinstance(parameters, dict) else None
        if not isinstance(run, dict) or run.get('schema_version') != 1:
            return None
        workflow = run.get('workflow') or {}
        trigger = run.get('trigger') or {}
        if not isinstance(workflow, dict) or not isinstance(trigger, dict):
            raise ValueError('Prefect run workflow and trigger must be objects.')
        definition = workflow.get('definition') or {}
        if not isinstance(definition, dict) or not isinstance(definition.get('steps') or [], list):
            raise ValueError('Prefect run definition must contain a list of steps.')
        serializer = RuntimeRegistrationSerializer(data={
            'workflow_id': workflow.get('id'),
            'workflow_version': workflow.get('version'),
            'prefect_flow_run_id': flow_id,
            'trigger_source': trigger.get('source') or 'schedule',
            'trigger_data': trigger.get('data') or {},
            'total_steps': len(definition.get('steps') or []),
        })
        serializer.is_valid(raise_exception=True)
        payload = dict(serializer.data)
        payload['execution_id'] = str(UUID(str(run['execution_id']))) if run.get('execution_id') else str(uuid5(NAMESPACE_URL, 'argus:prefect:' + flow_id))
        execution = _execution(payload)
        if not execution.task_result_id:
            execution.task_result_id = flow_id
            execution.save(update_fields=['task_result_id'])
    # Read the current native state, so replayed state transitions cannot undo
    # completion/cancellation. Custom progress supplies the detailed step data.
    return sync_status(execution, force=True, occurred=event.occurred)


def consume_event(event):
    with transaction.atomic():
        try:
            # A malformed event rolls back all its writes without poisoning the stream.
            with transaction.atomic():
                apply_event(event)
        except (serializers.ValidationError, ValueError, Workflow.DoesNotExist, prefect_client.PrefectFlowRunNotFound) as exc:
            logger.warning('Rejected Prefect event %s (%s): %s', event.id, event.event, type(exc).__name__)
        checkpoint, _ = WorkflowEventCheckpoint.objects.select_for_update().get_or_create(
            name=CHECKPOINT, defaults={'occurred': event.occurred},
        )
        if event.occurred > checkpoint.occurred:
            checkpoint.occurred = event.occurred
            checkpoint.save(update_fields=['occurred'])


def replay_events(handler=consume_event):
    from prefect.events.schemas.events import Event

    checkpoint = WorkflowEventCheckpoint.objects.filter(pk=CHECKPOINT).first()
    # ponytail: five-minute overlap covers delayed event persistence; for longer
    # delays replay with --reset-checkpoint while events are retained in Prefect.
    since = checkpoint.occurred - timedelta(minutes=5) if checkpoint else datetime(1970, 1, 1, tzinfo=datetime_timezone.utc)
    for raw in prefect_client.iter_events({
        'event': {'name': EVENT_NAMES},
        'occurred': {'since': since.isoformat(), 'until': timezone.now().isoformat()},
        'order': 'ASC',
    }):
        handler(Event.model_validate(raw))
