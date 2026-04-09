"""
Workflow Signals

Signal handlers for automatic workflow triggering.

Signals call ``trigger_workflows_for_event`` which delegates to the Django 6.0
background task ``trigger_workflows_for_event_task`` via ``.enqueue()``.  This
means the HTTP request that fired the signal is not blocked by workflow
execution when using an async backend.  With the default ``ImmediateBackend``
the task still runs synchronously, but the code path is identical — only a
settings change is needed to switch backends.
"""
import logging

# post_save and receiver are imported here for use by the commented-out
# auto-trigger signal receivers below.  Uncomment them together when enabling
# automatic workflow triggering.
from django.db.models.signals import post_save  # noqa: F401
from django.dispatch import receiver  # noqa: F401

logger = logging.getLogger(__name__)


def _get_nested_value(data: dict, field: str):
    current = data
    for part in str(field).split('.'):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def _rule_matches(actual, operator: str, expected) -> bool:
    if isinstance(actual, list):
        # Ticket labels are stored as [{label_name, label_value}] and can be
        # filtered via field='labels' with contains/equality semantics.
        if operator == 'contains':
            actual_values = []
            for item in actual:
                if isinstance(item, dict):
                    name = str(item.get('label_name') or '').strip()
                    value = str(item.get('label_value') or '').strip()
                    actual_values.append(f"{name}:{value}" if value else name)
                else:
                    actual_values.append(str(item))
            expected_str = str(expected or '')
            return any(expected_str in item for item in actual_values)
        if operator == '==':
            return any(str(expected) == str(item) for item in actual)
        if operator == '!=':
            return all(str(expected) != str(item) for item in actual)

    if operator == 'contains':
        return str(expected) in str(actual or '')
    if operator == '!=':
        return actual != expected
    if operator == '>':
        return actual is not None and actual > expected
    if operator == '<':
        return actual is not None and actual < expected
    if operator == '>=':
        return actual is not None and actual >= expected
    if operator == '<=':
        return actual is not None and actual <= expected
    return actual == expected


def _rules_match(data: dict, rules, logic: str = 'AND') -> bool:
    if not isinstance(rules, list):
        return True

    normalized_logic = str(logic or 'AND').upper()
    evaluated = []

    for rule in rules:
        if not isinstance(rule, dict):
            continue
        field = rule.get('field')
        if not field:
            continue
        operator = rule.get('operator') or '=='
        expected = rule.get('value')
        actual = _get_nested_value(data, field)
        evaluated.append(_rule_matches(actual, operator, expected))

    if not evaluated:
        return True
    if normalized_logic == 'OR':
        return any(evaluated)
    return all(evaluated)


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


def trigger_workflows_for_event(trigger_type: str, instance, trigger_data: dict):
    """
    Enqueue a background task that finds and executes all active workflows
    matching *trigger_type*.

    Using ``trigger_workflows_for_event_task.enqueue()`` here means execution
    is handled by Django 6.0's task backend rather than running inline, which
    prevents signal handlers from blocking web requests once an async backend
    is configured.

    Args:
        trigger_type: Workflow trigger type (e.g. ``"ticket_created"``).
        instance: The model instance that raised the signal.
        trigger_data: Payload forwarded to matching workflows.
    """
    from .tasks import trigger_workflows_for_event_task

    trigger_source = f"{trigger_type}:{getattr(instance, 'pk', 'unknown')}"
    logger.info(
        "Enqueueing workflow trigger '%s' for source '%s'",
        trigger_type,
        trigger_source,
    )
    try:
        trigger_workflows_for_event_task.enqueue(
            trigger_type,
            trigger_source,
            trigger_data,
        )
    except Exception as exc:
        logger.exception(
            "Failed to enqueue trigger_workflows_for_event_task for '%s': %s",
            trigger_type,
            exc,
        )


def _matches_conditions(data: dict, conditions: dict) -> bool:
    """
    Check if *data* satisfies all *conditions*.

    Conditions format::

        {
            "field_name": "expected_value",
            "field_name": ["value1", "value2"],   # any of these
            "field_name": {"operator": ">", "value": 10}
        }

    Returns ``True`` when *conditions* is empty (no filter = match all).
    """
    if not conditions:
        return True

    if not _rules_match(
        data,
        conditions.get('alert_filters'),
        conditions.get('alert_filter_logic', 'AND'),
    ):
        return False
    if not _rules_match(
        data,
        conditions.get('ticket_filters'),
        conditions.get('ticket_filter_logic', 'AND'),
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
        actual = data.get(field)

        if isinstance(expected, list):
            if actual not in expected:
                return False
        elif isinstance(expected, dict):
            operator = expected.get('operator', '==')
            value = expected.get('value')

            if operator == '==' and actual != value:
                return False
            elif operator == '!=' and actual == value:
                return False
            elif operator == '>' and not (actual and actual > value):
                return False
            elif operator == '<' and not (actual and actual < value):
                return False
            elif operator == 'contains' and value not in str(actual):
                return False
        else:
            if actual != expected:
                return False

    return True


# ---------------------------------------------------------------------------
# Auto-trigger signal receivers
# Uncomment the decorators below to enable automatic workflow triggering when
# tickets are created or updated.
# ---------------------------------------------------------------------------

# @receiver(post_save, sender='tickets.EventTicket')
# def on_ticket_save(sender, instance, created, **kwargs):
#     """Trigger workflows when a ticket is created or updated."""
#     if created:
#         trigger_data = {
#             'ticket_number': instance.ticket_number,
#             'title': instance.title,
#             'status': instance.status,
#             'priority': instance.priority,
#             'description': instance.description,
#         }
#         trigger_workflows_for_event('ticket_created', instance, trigger_data)
#     else:
#         trigger_data = {
#             'ticket_number': instance.ticket_number,
#             'status': instance.status,
#             'priority': instance.priority,
#         }
#         trigger_workflows_for_event('ticket_status', instance, trigger_data)

