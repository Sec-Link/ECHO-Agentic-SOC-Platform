from django.db.models import Q

from .engine import execute_workflow
from .models import TicketWorkflowBinding, Workflow, WorkflowExecution
from .parameter_binder import bind_workflow_parameters


def find_callable_workflows():
    return Workflow.objects.filter(
        is_active=True,
        is_draft=False,
        execution_engine='prefect',
        is_callable_from_ticket=True,
    ).order_by('name')


def invoke_workflow_from_ticket(workflow, ticket_data, user_inputs, executed_by, comment=''):
    inputs = bind_workflow_parameters(
        workflow.inputs_schema,
        user_inputs=user_inputs or {},
        ticket_data=ticket_data or {},
        context={
            'ticket_number': (ticket_data or {}).get('ticket_number'),
            'user_id': getattr(executed_by, 'id', None),
        },
    )
    ticket_number = (ticket_data or {}).get('ticket_number') or 'unknown'
    return execute_workflow(
        workflow=workflow,
        trigger_data={'inputs': inputs, 'ticket': ticket_data or {}, 'comment': comment},
        trigger_source=f'ticket_invoke:{ticket_number}',
        executed_by=executed_by,
    )


def dispatch_ticket_event(trigger_event, ticket_data, executed_by=None):
    bindings = TicketWorkflowBinding.objects.select_related('workflow').filter(
        workflow__trigger_type='event',
        workflow__is_active=True,
        workflow__is_draft=False,
        workflow__execution_engine='prefect',
        workflow__is_callable_from_ticket=True,
    )

    triggered = 0
    skipped = 0
    executions = []
    ticket_number = (ticket_data or {}).get('ticket_number') or 'unknown'

    for binding in bindings:
        if not _binding_matches_ticket(binding, ticket_data or {}):
            skipped += 1
            continue

        trigger_source = f'ticket_binding:{ticket_number}'
        if WorkflowExecution.objects.filter(workflow=binding.workflow, trigger_source=trigger_source).exists():
            skipped += 1
            continue

        try:
            inputs = bind_workflow_parameters(binding.workflow.inputs_schema, {}, ticket_data or {})
            execution = execute_workflow(
                workflow=binding.workflow,
                trigger_data={
                    'inputs': inputs,
                    'ticket': ticket_data or {},
                    'trigger_event': trigger_event,
                    'binding_id': str(binding.id),
                    'binding_name': binding.name,
                },
                trigger_source=trigger_source,
                executed_by=executed_by,
            )
            executions.append(execution)
            triggered += 1
        except Exception:
            skipped += 1

    return {
        'triggered': triggered,
        'skipped': skipped,
        'total': triggered + skipped,
        'executions': [
            {
                'id': str(execution.id),
                'workflow_id': str(execution.workflow_id),
                'workflow_name': execution.workflow.name,
                'status': execution.status,
            }
            for execution in executions
        ],
    }


def get_ticket_workplan(ticket_number):
    trigger_sources = [f'ticket_binding:{ticket_number}', f'ticket_invoke:{ticket_number}']
    executions = WorkflowExecution.objects.select_related('workflow', 'executed_by').filter(
        trigger_source__in=trigger_sources,
    ).order_by('-created_at')
    return [
        {
            'execution_id': str(execution.id),
            'workflow_id': str(execution.workflow_id),
            'workflow_name': execution.workflow.name,
            'status': execution.status,
            'trigger_source': execution.trigger_source,
            'trigger_data': execution.trigger_data,
            'progress_percent': execution.progress_percent,
            'started_at': execution.started_at,
            'completed_at': execution.completed_at,
            'created_at': execution.created_at,
            'error_message': execution.error_message,
            'executed_by': getattr(execution.executed_by, 'username', None),
        }
        for execution in executions
    ]


def _binding_matches_ticket(binding, ticket_data):
    return _matches_label_filters(ticket_data.get('labels') or [], binding.label_filters or [], binding.label_filter_logic)


def _matches_label_filters(ticket_labels, label_filters, logic):
    if not label_filters:
        return True
    matches = []
    for rule in label_filters:
        if not isinstance(rule, dict):
            continue
        expected_name = str(rule.get('label_name') or '').strip()
        expected_value = rule.get('label_value')
        if not expected_name:
            continue
        matched = False
        for label in ticket_labels:
            if not isinstance(label, dict):
                continue
            name = str(label.get('label_name') or '').strip()
            value = label.get('label_value')
            if name != expected_name:
                continue
            if expected_value in (None, '') or str(value or '') == str(expected_value):
                matched = True
                break
        matches.append(matched)
    if not matches:
        return False
    if logic == 'OR':
        return any(matches)
    return all(matches)


def _matches_condition_value(expected, actual):
    if expected in (None, '', []):
        return True
    if isinstance(expected, list):
        return actual in expected
    return expected == actual
