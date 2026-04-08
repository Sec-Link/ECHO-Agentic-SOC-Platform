# File: `tickets/utils.py`
from typing import Any, Dict, Iterable, Optional, List, Union
from django.utils import timezone
from django.contrib.auth import get_user_model

from .models import EventTicket, EventTicketAttachment, TicketHandleLog

User = get_user_model()

# -----------------------------
# Change logging helper
# -----------------------------

def _format_value(obj: Any, field_name: str = "") -> str:
    """
    Convert a field value to a human-friendly string.
    For user-like objects return username; for model instances fallback to str().
    """
    if obj is None:
        return "None"
    # User instance
    if hasattr(obj, "get_username"):
        return obj.get_username()
    # File/storage field with url/name
    if hasattr(obj, "url"):
        return getattr(obj, "name", getattr(obj, "url", str(obj)))
    # Generic model instance
    try:
        # If it's a model, try to show a concise identifier
        pk = getattr(obj, "pk", None)
        if pk is not None:
            return f"{obj.__class__.__name__}({pk})"
    except Exception:
        pass
    return str(obj)


def record_field_changes_handle_log(
    ticket: EventTicket,
    handler: User,
    fields: Iterable[str],
    original: Optional[Union[EventTicket, Dict[str, Any]]] = None,
    description: str = "",
    attachments: Optional[Iterable[EventTicketAttachment]] = None,
) -> Optional[TicketHandleLog]:
    """
    Compare the specified `fields` for `ticket` against `original` and record a TicketHandleLog
    if any differences exist.

    Parameters:
    - ticket: the updated EventTicket instance (post-save).
    - handler: user who performed the changes.
    - fields: iterable of field names to compare (e.g. ['status', 'priority', 'assigned_user']).
    - original: optional original state; may be:
        * an EventTicket instance (pre-change),
        * or a dict mapping field_name -> original_value.
      If not provided, the function will try to fetch the DB state using ticket.pk.
    - description: optional short description prefix for the action text.
    - attachments: optional iterable of EventTicketAttachment instances to include in the log.

    Returns:
    - Created TicketHandleLog instance, or None if no changes detected.
    """
    # Resolve original snapshot into a dict
    orig_snapshot: Dict[str, Any] = {}
    if original is None:
        # try to fetch from DB to get pre-change values
        try:
            db_obj = EventTicket.objects.get(pk=ticket.pk)
            for f in fields:
                orig_snapshot[f] = getattr(db_obj, f, None)
        except EventTicket.DoesNotExist:
            # No original available; treat all fields as changed from None
            for f in fields:
                orig_snapshot[f] = None
    elif isinstance(original, EventTicket):
        for f in fields:
            orig_snapshot[f] = getattr(original, f, None)
    else:
        # assume dict-like
        orig_snapshot = dict(original)

    changes: List[str] = []
    for f in fields:
        before = orig_snapshot.get(f, None)
        after = getattr(ticket, f, None)

        # Special handling for foreign keys to compare by pk but display friendly text
        if hasattr(before, "pk") or hasattr(after, "pk"):
            before_id = getattr(before, "pk", before)
            after_id = getattr(after, "pk", after)
            if before_id != after_id:
                before_label = _format_value(before, f)
                after_label = _format_value(after, f)
                changes.append(f"{f}: {before_label} -> {after_label}")
        else:
            # Normal comparison; treat empty string and None as equivalent only if both empty
            if before != after:
                before_label = _format_value(before, f)
                after_label = _format_value(after, f)
                changes.append(f"{f}: {before_label} -> {after_label}")

    if not changes and not attachments:
        return None

    parts: List[str] = []
    if description:
        parts.append(description)
    if changes:
        parts.append("Changes: " + " | ".join(changes))

    if attachments:
        att_parts: List[str] = []
        for a in attachments:
            # prefer explicit file_name or file_path.url when available
            name = getattr(a, "file_name", None) or getattr(a, "file_path", None)
            if hasattr(name, "name"):
                name = name.name
            if getattr(a, "file_path", None) and getattr(a.file_path, "url", None):
                att_parts.append(f"{name} (url: {a.file_path.url})")
            else:
                att_parts.append(str(name))
        if att_parts:
            parts.append("Attachments: " + ", ".join(att_parts))

    action_text = " | ".join(parts) if parts else "No details provided"
    # include a timestamp in the stored action text for traceability (human readable)
    ts = timezone.now().isoformat()
    action_text_with_time = f"[{ts}] {action_text}"

    log = TicketHandleLog.objects.create(
        ticket=ticket,
        handler=handler,
        action_taken=action_text_with_time,

    )
    print( f"Recorded TicketHandleLog for ticket {ticket.ticket_number}: {action_text_with_time}" )
    return log

