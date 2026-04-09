# signals.py
"""
Signal handlers for automatic SLA calculation and ticket event processing.

This module handles post-save and pre-save signals to automatically calculate
SLA metrics whenever EventTicket timestamps are updated.
"""

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone
from django.core.exceptions import ValidationError
from .models import EventTicket, TicketSLA, TicketPendingInterval


def _overlap_seconds(a_start, a_end, b_start, b_end):
    """
    Calculate the number of seconds overlapped between two time intervals.

    Args:
        a_start, a_end: Start and end of the first interval
        b_start, b_end: Start and end of the second interval

    Returns:
        int: Number of seconds overlapped, or 0 if there is no overlap
    """
    if not a_start or not a_end or not b_start or not b_end:
        return 0
    start = max(a_start, b_start)
    end = min(a_end, b_end)
    if end <= start:
        return 0
    return int((end - start).total_seconds())


@receiver(pre_save, sender=EventTicket)
def update_ticket_timestamps_on_status_change(sender, instance, **kwargs):
    """
    Pre-save signal handler to update timestamps based on status changes.

    Lifecycle status rules are enforced via `status`.
    Pending edit-pause is handled via `progress_status`.
    """
    try:
        old_instance = EventTicket.objects.get(ticket_number=instance.ticket_number)
    except EventTicket.DoesNotExist:
        return

    now = timezone.now()

    # Enforce progress_status rules early.
    # - Pending is only allowed after the ticket is acknowledged (status != 'new').
    # - No changes are allowed once the ticket is closed.
    if getattr(instance, 'status', None) == 'closed' and getattr(instance, 'progress_status', 'in_progress') != getattr(old_instance, 'progress_status', 'in_progress'):
        raise ValidationError("Progress status cannot be changed after the ticket is closed.")

    if getattr(instance, 'progress_status', 'in_progress') == 'pending' and getattr(instance, 'status', None) == 'new':
        raise ValidationError("'pending' progress status is only allowed after the ticket is acknowledged.")

    # -----------------------------
    # Progress status (Pending/In Progress) interval tracking
    # -----------------------------
    if getattr(old_instance, 'progress_status', 'in_progress') != getattr(instance, 'progress_status', 'in_progress'):
        new_prog = getattr(instance, 'progress_status', 'in_progress')
        old_prog = getattr(old_instance, 'progress_status', 'in_progress')

        def infer_stage(ticket: EventTicket) -> str:
            if not ticket.event_response_time:
                return 'pre-acknowledge'
            if not ticket.event_analysis_time:
                return 'investigate'
            if not ticket.event_containment_time:
                return 'containment'
            if not ticket.ticket_resolved_time:
                return 'resolution'
            return 'post-resolution'

        # Enter pending -> open interval
        if new_prog == 'pending' and old_prog != 'pending':
            TicketPendingInterval.objects.create(
                ticket=instance,
                started_at=now,
                stage=infer_stage(old_instance),
                notes=f"Auto-opened due to progress_status change from '{old_prog}' to 'pending'"
            )
        # Exit pending -> close last open interval
        if old_prog == 'pending' and new_prog != 'pending':
            last_open = instance.pending_intervals.filter(ended_at__isnull=True).order_by('-started_at').first()
            if last_open:
                last_open.ended_at = now
                last_open.save(update_fields=['ended_at'])

    # -----------------------------
    # Lifecycle status rule enforcement + timestamps
    # -----------------------------
    if old_instance.status != instance.status:
        new_status = instance.status
        old_status = old_instance.status

        # Lifecycle workflow statuses use lowercase values.
        primary_chain = ['new', 'acknowledged', 'triaged', 'contained', 'resolved', 'closed']

        def baseline_index_for(ticket: EventTicket, current_status: str) -> int:
            """
            Return the baseline lifecycle index for irreversible transition checks.

            For non-primary/legacy statuses, infer the baseline from timestamps.
            """
            if current_status in primary_chain:
                return primary_chain.index(current_status)
            if ticket.ticket_resolved_time:
                return primary_chain.index('resolved')
            if ticket.event_containment_time:
                return primary_chain.index('contained')
            if ticket.event_analysis_time:
                return primary_chain.index('triaged')
            if ticket.event_response_time:
                return primary_chain.index('acknowledged')
            return primary_chain.index('new')

        # Disallow any further changes once closed
        if old_status == 'closed' and new_status != 'closed':
            raise ValidationError("Status cannot be changed after it is closed.")

        base_idx = baseline_index_for(old_instance, old_status)

        if new_status in primary_chain:
            new_idx = primary_chain.index(new_status)
            if new_idx < base_idx:
                raise ValidationError("Primary status transitions are irreversible (cannot move backward).")

        if new_status == 'acknowledged' and base_idx == primary_chain.index('new'):
            instance.event_response_time = now
        elif new_status == 'triaged' and base_idx <= primary_chain.index('acknowledged'):
            instance.event_analysis_time = now
        elif new_status == 'contained' and base_idx <= primary_chain.index('triaged'):
            instance.event_containment_time = now
        elif new_status == 'resolved' and base_idx <= primary_chain.index('contained'):
            instance.ticket_resolved_time = now
        elif new_status == 'closed' and base_idx <= primary_chain.index('resolved'):
            instance.ticket_closed_time = now


@receiver(post_save, sender=EventTicket)
def calculate_ticket_sla(sender, instance, created, **kwargs):
    """
    Post-save signal handler to calculate and persist SLA metrics.

    Called whenever an EventTicket is created or updated.
    Automatically calculates:
    - MTTA (Mean Time To Acknowledge): T3 - T2
    - MTTI (Mean Time To Investigate): T4 - T3
    - MTTC (Mean Time To Containment): T5 - T2
    - MTTR (Mean Time To Resolution): T6 - T2

    Args:
        sender: The model class (EventTicket)
        instance: The EventTicket instance being saved
        created: Boolean indicating if this is a new instance
        **kwargs: Additional keyword arguments from the signal
    """

    # Get or create the associated TicketSLA record
    sla, _ = TicketSLA.objects.get_or_create(ticket=instance)

    # Persist SLA metrics with pending-time subtraction by default.
    #
    # Pending intervals are recorded in TicketPendingInterval and (when enabled)
    # are subtracted from the SLA windows to reflect paused time.
    metrics = compute_sla_for_ticket(instance, subtract_pending=True)
    sla.mtta_seconds = metrics['mtta_seconds']
    sla.mtti_seconds = metrics['mtti_seconds']
    sla.mttc_seconds = metrics['mttc_seconds']
    sla.mttr_seconds = metrics['mttr_seconds']

    # Save the calculated SLA metrics to the database
    sla.save(update_fields=['mtta_seconds', 'mtti_seconds', 'mttc_seconds', 'mttr_seconds'])


# -----------------------------
# SLA computation helpers (exported)
# -----------------------------

def pending_overlap_seconds(ticket: EventTicket, window_start, window_end, stages=None) -> int:
    """
    Sum pending overlap seconds within [window_start, window_end] for the given ticket.
    If stages are provided, only include intervals whose 'stage' is in stages.
    """
    if not window_start or not window_end:
        return 0
    qs = ticket.pending_intervals.all()
    if stages:
        qs = qs.filter(stage__in=stages)
    total = 0
    for p in qs:
        pend_end = p.ended_at or timezone.now()
        total += _overlap_seconds(window_start, window_end, p.started_at, pend_end)
    return total


def format_seconds(seconds: int | None) -> str:
    """Format seconds into "Hh Mm Ss"; return "N/A" if None."""
    if seconds is None:
        return "N/A"
    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}h {minutes}m {secs}s"


def compute_sla_for_ticket(ticket: EventTicket, subtract_pending: bool) -> dict:
    """
    Compute SLA metrics for a ticket using T2..T6 (MTTA/MTTI/MTTC/MTTR).

    If subtract_pending is True, subtract pending overlaps by stage for each window:
      - MTTA: stages ['pre-acknowledge'] within [T2, T3]
      - MTTI: stages ['investigate'] within [T3, T4]
      - MTTC: stages ['pre-acknowledge','investigate','containment'] within [T2, T5]
      - MTTR: stages ['pre-acknowledge','investigate','containment','resolution'] within [T2, T6]

    Returns a dict with both *_seconds and *_display keys.
    """
    t2 = ticket.created_time
    t3 = ticket.event_response_time
    t4 = ticket.event_analysis_time
    t5 = ticket.event_containment_time
    t6 = ticket.ticket_resolved_time

    def wnd(start, end):
        return int((end - start).total_seconds()) if start and end else None

    mtta = wnd(t2, t3)
    mtti = wnd(t3, t4)
    mttc = wnd(t2, t5)
    mttr = wnd(t2, t6)

    if subtract_pending:
        if mtta is not None:
            mtta = max(0, mtta - pending_overlap_seconds(ticket, t2, t3, stages=['pre-acknowledge']))
        if mtti is not None:
            mtti = max(0, mtti - pending_overlap_seconds(ticket, t3, t4, stages=['investigate']))
        if mttc is not None:
            mttc = max(0, mttc - pending_overlap_seconds(ticket, t2, t5, stages=['pre-acknowledge', 'investigate', 'containment']))
        if mttr is not None:
            mttr = max(0, mttr - pending_overlap_seconds(ticket, t2, t6, stages=['pre-acknowledge', 'investigate', 'containment', 'resolution']))

    return {
        'mtta_seconds': mtta,
        'mtti_seconds': mtti,
        'mttc_seconds': mttc,
        'mttr_seconds': mttr,
        'mtta_display': format_seconds(mtta),
        'mtti_display': format_seconds(mtti),
        'mttc_display': format_seconds(mttc),
        'mttr_display': format_seconds(mttr),
    }
