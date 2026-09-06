"""Workflow Signals

Signal handlers for automatic workflow triggering.
"""
import logging

from django.db.models.signals import post_save  # noqa: F401
from django.dispatch import receiver  # noqa: F401

from .condition_evaluator import evaluate_condition_object

logger = logging.getLogger(__name__)


@receiver(post_save, sender='workflows.WorkflowExecution')
def on_execution_progress(sender, instance, **kwargs):
    from .progress import publish_progress

    publish_progress(instance)


def _get_nested_value(data: dict, field: str):
    current = data
    for part in str(field).split('.'):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def _ticket_labels_match(data: dict, label_rules) -> bool:
    if not isinstance(label_rules, list):
        return True

    labels = data.get('labels')
    if not isinstance(labels, list):
        return False if label_rules else True

    for rule in label_rules:
        if not isinstance(rule, dict):
            continue
        expected_name = str(rule.get('label_name') or '').strip()
        if not expected_name:
            continue
        expected_value = rule.get('label_value')

        matched = False
        for label in labels:
            if not isinstance(label, dict):
                continue
            name = str(label.get('label_name') or '').strip()
            value = label.get('label_value')
            if name != expected_name:
                continue
            if expected_value in (None, '') or str(value or '') == str(expected_value):
                matched = True
                break

        if not matched:
            return False

    return True


def trigger_workflows_for_event(trigger_type: str, instance, trigger_data: dict, executed_by_id: int | None = None):
    from .tasks import trigger_workflows_for_event_task

    trigger_source = f"{trigger_type}:{getattr(instance, 'pk', 'unknown')}"
    logger.info("Enqueueing workflow trigger '%s' for source '%s'", trigger_type, trigger_source)
    try:
        trigger_workflows_for_event_task.enqueue(
            trigger_type,
            trigger_source,
            trigger_data,
            executed_by_id,
        )
    except Exception as exc:
        logger.exception(
            "Failed to enqueue trigger_workflows_for_event_task for '%s': %s",
            trigger_type,
            exc,
        )


def _matches_conditions(data: dict, conditions: dict) -> bool:
    if not conditions:
        return True

    if conditions.get('alert_filters'):
        if not evaluate_condition_object(
            {'rules': conditions.get('alert_filters', []), 'logic': conditions.get('alert_filter_logic', 'AND')},
            lambda path: _get_nested_value(data, path),
            data,
        ):
            return False

    if conditions.get('ticket_filters'):
        if not evaluate_condition_object(
            {'rules': conditions.get('ticket_filters', []), 'logic': conditions.get('ticket_filter_logic', 'AND')},
            lambda path: _get_nested_value(data, path),
            data,
        ):
            return False

    if not _ticket_labels_match(data, conditions.get('ticket_label_filters')):
        return False

    special_keys = {
        'alert_filters',
        'alert_filter_logic',
        'ticket_filters',
        'ticket_filter_logic',
        'ticket_label_filters',
    }

    for field, expected in conditions.items():
        if field in special_keys:
            continue
        if isinstance(expected, list):
            if data.get(field) not in expected:
                return False
        elif isinstance(expected, dict):
            if not evaluate_condition_object(expected | {'field': field}, lambda path: _get_nested_value(data, path), data):
                return False
        else:
            if data.get(field) != expected:
                return False

    return True


@receiver(post_save, sender='tickets.EventTicket')
def on_ticket_save(sender, instance, created, **kwargs):
    trigger_data = {
        'ticket_number': instance.ticket_number,
        'title': instance.title,
        'status': instance.status,
        'priority': instance.priority,
        'description': instance.description,
        'create_uid': instance.create_uid,
        'event_category': instance.event_category,
        'event_result': instance.event_result,
        'labels': instance.labels or [],
        'current_assign_group': instance.current_assign_group,
        'current_assign_owner': instance.current_assign_owner,
    }

    if created:
        trigger_workflows_for_event('ticket_created', instance, trigger_data)
    else:
        trigger_workflows_for_event('ticket_status', instance, trigger_data)
