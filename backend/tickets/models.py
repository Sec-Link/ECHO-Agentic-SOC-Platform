# models.py

from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone


class EventTicket(models.Model):
    """
    Main incident ticket model. Follows the provided specification while preserving
    existing database column names to avoid disruptive renames.
    """
    # Primary key: unique ticket identifier (e.g., SEC20240501001)
    ticket_number = models.CharField(
        max_length=100,  # keep 100 to match existing DB and avoid a PK length migration
        primary_key=True,
        help_text="Unique ticket number (e.g., SEC20240501001)"
    )

    # Original event ID coming from the SIEM/security device
    event_siem_id = models.CharField(
        blank=True,
        null=True,
        max_length=255,  # keep 255 to match existing DB
        help_text="Original SIEM event ID from security device"
    )

    # Ticket title and description (preserve original field names/columns)
    title = models.CharField(
        max_length=255,
        help_text="Short ticket title summarizing the incident"
    )
    description = models.TextField(
        blank=True,
        null=True,
        help_text="Detailed incident description"
    )

    # Incident impact and scope dimensions (e.g., High/Medium/Low)
    event_impact = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        help_text="Incident impact level (e.g., High/Medium/Low)"
    )
    event_scope = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        help_text="Incident scope level (e.g., High/Medium/Low)"
    )

    # Priority and status
    # NOTE: Priority levels are configuration-driven (see get_ticket_priority_choices()).
    # Status choices: new, acknowledged, triaged, contained, resolved
    STATUS_CHOICES = [
        ('new', 'New'),
        ('acknowledged', 'Acknowledged'),
        ('triaged', 'Triaged'),
        ('contained', 'Contained'),
        ('resolved', 'Resolved'),
        ('closed', 'Closed'),
    ]

    status = models.CharField(
        max_length=50,
        choices=STATUS_CHOICES,
        default='new',
        help_text="Ticket status: New, Acknowledged, Triaged, Contained, Resolved, or Closed"
    )

    PRIORITY_CHOICES = [
        ('critical', 'Critical'),
        ('high', 'High'),
        ('medium', 'Medium'),
        ('low', 'Low'),
    ]
    priority = models.CharField(
        max_length=50,
        choices=PRIORITY_CHOICES,
        default="medium",
        help_text="Overall incident priority (Critical/High/Medium/Low by default)"
    )

    # Assignment information (both group and owner text fields)
    current_assign_group = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="Current responsible team (e.g., SOC L1)"
    )
    current_assign_owner = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="Current responsible person's name or account"
    )

    # Timestamps for SLA calculations in the incident lifecycle
    event_response_time = models.DateTimeField(
        null=True,
        blank=True,
        help_text="T3: Time when the incident was first responded to"
    )
    event_analysis_time = models.DateTimeField(
        null=True,
        blank=True,
        help_text="T4: Time when the incident analysis was completed"
    )
    event_containment_time = models.DateTimeField(
        null=True,
        blank=True,
        help_text="T5: Time when the incident was contained"
    )
    event_containment_cause = models.TextField(
        blank=True,
        null=True,
        help_text="Explanation of containment actions and rationale"
    )
    ticket_closure_user_id = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="User ID of the person who closed the ticket"
    )
    ticket_resolved_time = models.DateTimeField(
        null=True,
        blank=True,
        help_text="T6: Time when the incident was marked as resolved"
    )
    ticket_closed_time = models.DateTimeField(
        null=True,
        blank=True,
        help_text="T7: Time when the ticket was officially closed"
    )

    # Additional incident classification and tracking fields
    event_level = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        help_text="Incident severity level (e.g., P1/P2/P3)"
    )

    EVENT_CATEGORY_CHOICES = [
        ('account_anomalies', 'Account Anomalies'),
        ('denial_of_service', 'Denial of Service'),
        ('malware', 'Malware'),
        ('system_anomalies', 'System Anomalies'),
        ('network_anomalies', 'Network Anomalies'),
        ('application_anomalies', 'Application Anomalies'),
        ('policy', 'Policy'),
        ('social_engineering', 'Social Engineering'),
        ('others', 'Others'),
    ]
    event_category = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        choices=EVENT_CATEGORY_CHOICES,
        help_text="Incident category (e.g., Account Anomalies, Malware)"
    )

    EVENT_RESULT_CHOICES = [
        ('true_positive', 'True Positive'),
        ('false_positive', 'False Positive'),
        ('true_positive_benign', 'True Positive - Benign'),
    ]
    event_result = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        choices=EVENT_RESULT_CHOICES,
        help_text="Determination result (e.g., True Positive, False Positive)"
    )
    ticket_records = models.TextField(
        blank=True,
        null=True,
        help_text="Detailed handling results and notes"
    )
    event_cause_category = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="Root cause classification"
    )
    ticket_stage = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        help_text="Ticket stage (e.g., To Confirm, Investigating, Fixing)"
    )
    event_break_time = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Interruption/downtime timestamp for tracking"
    )
    event_sources = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Event sources (e.g., SIEM, EDR, Firewall)"
    )
    event_platform = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="Involved platform (e.g., Windows, Linux, AWS)"
    )
    ticket_url = models.URLField(
        max_length=500,
        blank=True,
        null=True,
        help_text="Related link (e.g., alert detail page)"
    )
    event_risk_score = models.IntegerField(
        blank=True,
        null=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Risk score (0 to 100)"
    )
    ticket_category = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="Ticket category (e.g., Security Incident, Compliance Check)"
    )
    alert_message = models.TextField(
        blank=True,
        null=True,
        help_text="Raw alert message content"
    )
    labels = models.JSONField(
        default=list,
        blank=True,
        help_text="Ticket labels in [{label_name, label_value}] format"
    )
    create_uid = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="Creator user ID"
    )

    # Soft delete flag (0 = No, 1 = Yes)
    is_deleted = models.BooleanField(
        default=False,
        help_text="Soft delete flag (0 = No, 1 = Yes)"
    )

    # Creation and last update timestamps (preserve original field names)
    created_time = models.DateTimeField(
        auto_now_add=True,
        help_text="Ticket creation time"
    )
    updated_time = models.DateTimeField(
        auto_now=True,
        help_text="Last update time"
    )

    # Optional link to a Django auth user who is assigned to the ticket
    assigned_user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Assigned Django user reference (optional)"
    )

    # -----------------------------------------------------------------
    # Progress Status (editability toggle)
    # -----------------------------------------------------------------
    # This field is intentionally separate from the main lifecycle `status`.
    # Lifecycle `status` represents the incident workflow (New -> ... -> Closed).
    # `progress_status` represents whether the ticket is currently being worked on
    # (In Progress) or temporarily paused (Pending). Pending intervals are recorded
    # in TicketPendingInterval for SLA subtraction.
    PROGRESS_STATUS_CHOICES = [
        ('in_progress', 'In Progress'),
        ('pending', 'Pending'),
    ]
    progress_status = models.CharField(
        max_length=20,
        choices=PROGRESS_STATUS_CHOICES,
        default='in_progress',
        help_text="Progress status for editability: In Progress or Pending"
    )

    class Meta:
        db_table = 'event_ticket'
        verbose_name = 'Event Ticket'
        verbose_name_plural = 'Event Tickets'

    def __str__(self):
        return f"{self.ticket_number}: {self.title}"

    def save(self, *args, **kwargs):
        """Auto-assign ticket_number if not provided.

        Format: SECYYYYMMDDNNNNN
        - Date part comes from local date (timestamp-derived)
        - Sequence is a 5-digit incremental number scoped to the day
        """
        if not self.ticket_number:
            today = timezone.localdate()
            prefix = f"SEC{today.strftime('%Y%m%d')}"
            last = (
                EventTicket.objects.filter(ticket_number__startswith=prefix)
                .order_by('-ticket_number')
                .values_list('ticket_number', flat=True)
                .first()
            )
            if last and last.startswith(prefix):
                try:
                    seq = int(last[len(prefix):]) + 1
                except ValueError:
                    seq = 1
            else:
                seq = 1
            self.ticket_number = f"{prefix}{seq:05d}"

        super().save(*args, **kwargs)


class EventTicketAttachment(models.Model):
    """
    Stores file attachments linked to a specific ticket.
    """
    ticket = models.ForeignKey(EventTicket, on_delete=models.CASCADE, to_field='ticket_number', db_column='ticket_number')
    file_name = models.CharField(max_length=255)
    file_path = models.FileField(upload_to='ticket_attachments/')
    uploaded_time = models.DateTimeField(auto_now_add=True)
    uploaded_user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)

    class Meta:
        db_table = 'event_ticket_attachment'
        verbose_name = 'Ticket Attachment'
        verbose_name_plural = 'Ticket Attachments'

    def __str__(self):
        return f"Attachment for {self.ticket.ticket_number}: {self.file_name}"


class TicketWorkLog(models.Model):
    """
    Records operational actions or notes added during ticket handling.
    Comparable to FIR's 'Comment' or 'BusinessLine' action logs.
    """
    ticket = models.ForeignKey(EventTicket, on_delete=models.CASCADE, to_field='ticket_number', db_column='ticket_number')
    log_entry = models.TextField()
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'ticket_work_log'
        verbose_name = 'Work Log'
        verbose_name_plural = 'Work Logs'

    def __str__(self):
        return f"Log on {self.ticket.ticket_number} by {self.created_by}"


class TicketHandleLog(models.Model):
    """
    Tracks detailed handling steps or state changes (e.g., triage, escalation).
    """
    ticket = models.ForeignKey(EventTicket, on_delete=models.CASCADE, to_field='ticket_number', db_column='ticket_number')
    handler = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    action_taken = models.TextField(help_text="Description of the handling action")
    handled_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'ticket_handle_log'
        verbose_name = 'Handling Log'
        verbose_name_plural = 'Handling Logs'

    def __str__(self):
        return f"Handled {self.ticket.ticket_number} at {self.handled_at}"


class NotificationSendMailJob(models.Model):
    """
    Asynchronous email notification queue for ticket events (creation, escalation, etc.).
    Inspired by FIR's notification system but implemented as a job queue.
    """
    ticket = models.ForeignKey(EventTicket, on_delete=models.CASCADE, to_field='ticket_number', db_column='ticket_number')
    recipient_email = models.EmailField()
    subject = models.CharField(max_length=255)
    template_name = models.CharField(max_length=100, help_text="Email template used (e.g., 'new_ticket', 'escalation')")
    context_data = models.JSONField(help_text="Serialized context for rendering email template", default=dict)
    sent = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'notification_send_mail_job'
        verbose_name = 'Email Notification Job'
        verbose_name_plural = 'Email Notification Jobs'

    def __str__(self):
        return f"{('[Sent] ' if self.sent else '')}Email to {self.recipient_email} re: {self.ticket.ticket_number}"


class TicketSLA(models.Model):
    """
    Service Level Agreement (SLA) tracking for tickets.

    Stores calculated SLA metrics for incident response performance monitoring.
    One-to-one relationship with EventTicket.

    SLA Metrics:
    - MTTA (Mean Time To Acknowledge): T3 - T2 (event_response_time - created_time)
    - MTTI (Mean Time To Investigate): T4 - T3 (event_analysis_time - event_response_time)
    - MTTC (Mean Time To Containment): T5 - T2 (event_containment_time - created_time)
    - MTTR (Mean Time To Resolution): T6 - T2 (ticket_resolved_time - created_time)
    """

    # One-to-one relationship with EventTicket
    ticket = models.OneToOneField(
        EventTicket,
        on_delete=models.CASCADE,
        primary_key=True,
        to_field='ticket_number',
        db_column='ticket_number',
        related_name='sla',
        help_text="Reference to the associated event ticket"
    )

    # SLA Time Metrics (stored in timedelta format, converted to seconds for database)
    # MTTA: Mean Time To Acknowledge (T3 - T2, in seconds)
    mtta_seconds = models.IntegerField(
        null=True,
        blank=True,
        help_text="Mean Time To Acknowledge in seconds (event_response_time - created_time)"
    )

    # MTTI: Mean Time To Investigate (T4 - T3, in seconds)
    mtti_seconds = models.IntegerField(
        null=True,
        blank=True,
        help_text="Mean Time To Investigate in seconds (event_analysis_time - event_response_time)"
    )

    # MTTC: Mean Time To Containment (T5 - T2, in seconds)
    mttc_seconds = models.IntegerField(
        null=True,
        blank=True,
        help_text="Mean Time To Containment in seconds (event_containment_time - created_time)"
    )

    # MTTR: Mean Time To Resolution (T6 - T2, in seconds)
    mttr_seconds = models.IntegerField(
        null=True,
        blank=True,
        help_text="Mean Time To Resolution in seconds (ticket_resolved_time - created_time)"
    )

    # Timestamp tracking
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="SLA record creation timestamp"
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="SLA record last update timestamp"
    )

    class Meta:
        db_table = 'ticket_sla'
        verbose_name = 'Ticket SLA'
        verbose_name_plural = 'Ticket SLAs'

    def __str__(self):
        return f"SLA for {self.ticket.ticket_number}"

    # Property methods for human-readable time formatting
    def get_mtta_display(self):
        """Return MTTA in human-readable format (hours:minutes:seconds)"""
        if self.mtta_seconds is None:
            return "N/A"
        hours, remainder = divmod(self.mtta_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{int(hours)}h {int(minutes)}m {int(seconds)}s"

    def get_mtti_display(self):
        """Return MTTI in human-readable format (hours:minutes:seconds)"""
        if self.mtti_seconds is None:
            return "N/A"
        hours, remainder = divmod(self.mtti_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{int(hours)}h {int(minutes)}m {int(seconds)}s"

    def get_mttc_display(self):
        """Return MTTC in human-readable format (hours:minutes:seconds)"""
        if self.mttc_seconds is None:
            return "N/A"
        hours, remainder = divmod(self.mttc_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{int(hours)}h {int(minutes)}m {int(seconds)}s"

    def get_mttr_display(self):
        """Return MTTR in human-readable format (hours:minutes:seconds)"""
        if self.mttr_seconds is None:
            return "N/A"
        hours, remainder = divmod(self.mttr_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{int(hours)}h {int(minutes)}m {int(seconds)}s"


class TicketPendingInterval(models.Model):
    """
    Records a pause interval when a ticket enters 'pending' status.

    Each interval stores:
    - started_at: when 'pending' began
    - ended_at: when 'pending' ended (set when status leaves 'pending')
    - stage: lifecycle stage this pending interval belongs to
      Examples: 'pre-acknowledge', 'investigate', 'containment', 'resolution', 'post-resolution'
    - notes: optional notes/reason

    Pending intervals are subtracted from SLA windows when calculating
    MTTA/MTTI/MTTC/MTTR to reflect paused time.
    """
    ticket = models.ForeignKey(
        EventTicket,
        on_delete=models.CASCADE,
        to_field='ticket_number',
        db_column='ticket_number',
        related_name='pending_intervals',
        help_text="Reference to the associated event ticket"
    )
    started_at = models.DateTimeField(help_text="Timestamp when pending began")
    ended_at = models.DateTimeField(null=True, blank=True, help_text="Timestamp when pending ended")
    stage = models.CharField(
        max_length=50,
        help_text="Lifecycle stage for this pending interval (e.g., pre-acknowledge/investigate/containment/resolution)"
    )
    notes = models.TextField(blank=True, null=True, help_text="Optional notes/reason for pending interval")

    class Meta:
        db_table = 'ticket_pending_interval'
        verbose_name = 'Ticket Pending Interval'
        verbose_name_plural = 'Ticket Pending Intervals'

    def __str__(self):
        return f"Pending[{self.stage}] {self.ticket.ticket_number} from {self.started_at} to {self.ended_at or '...'}"
