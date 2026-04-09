from django.contrib import admin
from .models import (
    EventTicket,
    EventTicketAttachment,
    TicketWorkLog,
    TicketHandleLog,
    NotificationSendMailJob,
    TicketSLA,
)


class TicketSLAInline(admin.TabularInline):
    """
    Inline display of TicketSLA metrics in EventTicket admin.
    """
    model = TicketSLA
    extra = 0
    fields = (
        'mtta_seconds',
        'mtti_seconds',
        'mttc_seconds',
        'mttr_seconds',
        'created_at',
        'updated_at',
    )
    readonly_fields = (
        'mtta_seconds',
        'mtti_seconds',
        'mttc_seconds',
        'mttr_seconds',
        'created_at',
        'updated_at',
    )


class EventTicketAttachmentInline(admin.TabularInline):
    """
    Inline display of ticket attachments in EventTicket admin.
    """
    model = EventTicketAttachment
    extra = 1
    fields = ('file_name', 'file_path', 'uploaded_time', 'uploaded_user')
    readonly_fields = ('uploaded_time',)


class TicketWorkLogInline(admin.TabularInline):
    """
    Inline display of work logs in EventTicket admin.
    """
    model = TicketWorkLog
    extra = 1
    fields = ('log_entry', 'created_by', 'created_at')
    readonly_fields = ('created_at',)


@admin.register(EventTicket)
class EventTicketAdmin(admin.ModelAdmin):
    """
    Admin interface for EventTicket model with comprehensive configuration.

    Features:
    - Status-based filtering and display
    - SLA metrics visibility
    - Inline attachments and work logs
    - Timestamp tracking
    """

    list_display = (
        'ticket_number',
        'title',
        'status',
        'priority',
        'event_impact',
        'created_time',
        'assigned_user',
    )

    list_filter = (
        'status',
        'priority',
        'event_impact',
        'event_scope',
        'created_time',
        'is_deleted',
    )

    search_fields = (
        'ticket_number',
        'title',
        'event_siem_id',
        'description',
    )

    fieldsets = (
        ('Basic Information', {
            'fields': (
                'ticket_number',
                'event_siem_id',
                'title',
                'description',
            )
        }),
        ('Status & Priority', {
            'fields': (
                'status',
                'priority',
                'event_impact',
                'event_scope',
            )
        }),
        ('Assignment', {
            'fields': (
                'assigned_user',
                'current_assign_group',
                'current_assign_owner',
            )
        }),
        ('Timeline & SLA Timestamps', {
            'fields': (
                'created_time',
                'event_response_time',
                'event_analysis_time',
                'event_containment_time',
                'ticket_resolved_time',
                'ticket_closed_time',
            ),
            'description': 'SLA timeline: T2=created_time, T3=event_response_time, T4=event_analysis_time, T5=event_containment_time, T6=ticket_resolved_time'
        }),
        ('Incident Classification', {
            'fields': (
                'event_level',
                'event_category',
                'event_result',
                'event_cause_category',
                'event_sources',
                'event_platform',
            )
        }),
        ('Details & Records', {
            'fields': (
                'event_containment_cause',
                'ticket_records',
                'ticket_stage',
                'ticket_category',
                'event_risk_score',
                'ticket_url',
                'alert_message',
            ),
            'classes': ('collapse',)
        }),
        ('Administrative', {
            'fields': (
                'create_uid',
                'ticket_closure_user_id',
                'is_deleted',
                'updated_time',
            ),
            'classes': ('collapse',)
        }),
    )

    readonly_fields = (
        'created_time',
        'updated_time',
    )

    inlines = [
        TicketSLAInline,
        EventTicketAttachmentInline,
        TicketWorkLogInline,
    ]

    def get_readonly_fields(self, request, obj=None):
        """
        Make ticket_number read-only after creation.
        """
        readonly = list(self.readonly_fields)
        if obj:  # Editing an existing ticket
            readonly.append('ticket_number')
        return readonly


@admin.register(TicketSLA)
class TicketSLAAdmin(admin.ModelAdmin):
    """
    Admin interface for TicketSLA model.

    Displays SLA metrics and provides visibility into response time performance.
    """

    list_display = (
        'ticket',
        'mtta_seconds_display',
        'mtti_seconds_display',
        'mttc_seconds_display',
        'mttr_seconds_display',
        'updated_at',
    )

    list_filter = (
        'created_at',
        'updated_at',
    )

    search_fields = (
        'ticket__ticket_number',
        'ticket__title',
    )

    fieldsets = (
        ('Ticket Reference', {
            'fields': ('ticket',)
        }),
        ('SLA Metrics (in seconds)', {
            'fields': (
                'mtta_seconds',
                'mtti_seconds',
                'mttc_seconds',
                'mttr_seconds',
            ),
            'description': 'MTTA=T3-T2, MTTI=T4-T3, MTTC=T5-T2, MTTR=T6-T2'
        }),
        ('Timestamps', {
            'fields': (
                'created_at',
                'updated_at',
            ),
            'classes': ('collapse',)
        }),
    )

    readonly_fields = (
        'ticket',
        'mtta_seconds',
        'mtti_seconds',
        'mttc_seconds',
        'mttr_seconds',
        'created_at',
        'updated_at',
    )

    def mtta_seconds_display(self, obj):
        """Display MTTA in human-readable format"""
        return obj.get_mtta_display()
    mtta_seconds_display.short_description = 'MTTA (Time to Acknowledge)'

    def mtti_seconds_display(self, obj):
        """Display MTTI in human-readable format"""
        return obj.get_mtti_display()
    mtti_seconds_display.short_description = 'MTTI (Time to Investigate)'

    def mttc_seconds_display(self, obj):
        """Display MTTC in human-readable format"""
        return obj.get_mttc_display()
    mttc_seconds_display.short_description = 'MTTC (Time to Containment)'

    def mttr_seconds_display(self, obj):
        """Display MTTR in human-readable format"""
        return obj.get_mttr_display()
    mttr_seconds_display.short_description = 'MTTR (Time to Resolution)'


@admin.register(EventTicketAttachment)
class EventTicketAttachmentAdmin(admin.ModelAdmin):
    """
    Admin interface for EventTicketAttachment model.
    """

    list_display = (
        'ticket',
        'file_name',
        'uploaded_time',
        'uploaded_user',
    )

    list_filter = (
        'uploaded_time',
    )

    search_fields = (
        'ticket__ticket_number',
        'file_name',
    )

    readonly_fields = (
        'uploaded_time',
    )


@admin.register(TicketWorkLog)
class TicketWorkLogAdmin(admin.ModelAdmin):
    """
    Admin interface for TicketWorkLog model.
    """

    list_display = (
        'ticket',
        'created_by',
        'created_at',
    )

    list_filter = (
        'created_at',
    )

    search_fields = (
        'ticket__ticket_number',
        'log_entry',
    )

    readonly_fields = (
        'created_at',
    )


@admin.register(TicketHandleLog)
class TicketHandleLogAdmin(admin.ModelAdmin):
    """
    Admin interface for TicketHandleLog model.
    """

    list_display = (
        'ticket',
        'handler',
        'handled_at',
    )

    list_filter = (
        'handled_at',
    )

    search_fields = (
        'ticket__ticket_number',
        'action_taken',
    )

    readonly_fields = (
        'handled_at',
    )


@admin.register(NotificationSendMailJob)
class NotificationSendMailJobAdmin(admin.ModelAdmin):
    """
    Admin interface for NotificationSendMailJob model.

    Provides visibility into email notification queue status.
    """

    list_display = (
        'ticket',
        'recipient_email',
        'template_name',
        'sent',
        'created_at',
        'sent_at',
    )

    list_filter = (
        'sent',
        'template_name',
        'created_at',
    )

    search_fields = (
        'ticket__ticket_number',
        'recipient_email',
        'subject',
    )

    readonly_fields = (
        'created_at',
        'sent_at',
    )


