# serializers.py
"""
Django REST Framework serializers for ticket models.

Provides serialization for EventTicket and TicketSLA models.
"""

from rest_framework import serializers
from .models import EventTicket, TicketSLA, EventTicketAttachment, TicketWorkLog, TicketHandleLog


class TicketSLASerializer(serializers.ModelSerializer):
    """
    Serializer for TicketSLA model.

    Exposes SLA metrics in both raw seconds and human-readable formats.
    """

    mtta_display = serializers.SerializerMethodField()
    mtti_display = serializers.SerializerMethodField()
    mttc_display = serializers.SerializerMethodField()
    mttr_display = serializers.SerializerMethodField()

    class Meta:
        model = TicketSLA
        fields = [
            'ticket',
            'mtta_seconds',
            'mtti_seconds',
            'mttc_seconds',
            'mttr_seconds',
            'mtta_display',
            'mtti_display',
            'mttc_display',
            'mttr_display',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'ticket',
            'mtta_seconds',
            'mtti_seconds',
            'mttc_seconds',
            'mttr_seconds',
            'mtta_display',
            'mtti_display',
            'mttc_display',
            'mttr_display',
            'created_at',
            'updated_at',
        ]

    def get_mtta_display(self, obj):
        """Return MTTA in human-readable format"""
        return obj.get_mtta_display()

    def get_mtti_display(self, obj):
        """Return MTTI in human-readable format"""
        return obj.get_mtti_display()

    def get_mttc_display(self, obj):
        """Return MTTC in human-readable format"""
        return obj.get_mttc_display()

    def get_mttr_display(self, obj):
        """Return MTTR in human-readable format"""
        return obj.get_mttr_display()


class EventTicketAttachmentSerializer(serializers.ModelSerializer):
    """
    Serializer for EventTicketAttachment model.
    """

    class Meta:
        model = EventTicketAttachment
        fields = [
            'id',
            'ticket',
            'file_name',
            'file_path',
            'uploaded_time',
            'uploaded_user',
        ]
        read_only_fields = ['id', 'uploaded_time']


class TicketWorkLogSerializer(serializers.ModelSerializer):
    """
    Serializer for TicketWorkLog model.
    """

    created_by_username = serializers.CharField(
        source='created_by.username',
        read_only=True
    )

    class Meta:
        model = TicketWorkLog
        fields = [
            'id',
            'ticket',
            'log_entry',
            'created_by',
            'created_by_username',
            'created_at',
        ]
        read_only_fields = ['id', 'created_at']


class TicketHandleLogSerializer(serializers.ModelSerializer):
    """
    Serializer for TicketHandleLog model.
    """

    handler_username = serializers.CharField(
        source='handler.username',
        read_only=True
    )

    class Meta:
        model = TicketHandleLog
        fields = [
            'id',
            'ticket',
            'handler',
            'handler_username',
            'action_taken',
            'handled_at',
        ]
        read_only_fields = ['id', 'handled_at']


class EventTicketSerializer(serializers.ModelSerializer):
    """
    Serializer for EventTicket model with nested SLA data.

    Includes full SLA metrics and related attachment/work log counts.
    """

    create_uid = serializers.CharField(required=True, allow_blank=False)

    sla = TicketSLASerializer(read_only=True)
    assigned_user_username = serializers.CharField(
        source='assigned_user.username',
        read_only=True,
        allow_null=True
    )
    attachments_count = serializers.SerializerMethodField()
    work_logs_count = serializers.SerializerMethodField()

    class Meta:
        model = EventTicket
        fields = [
            'ticket_number',
            'event_siem_id',
            'title',
            'description',
            'status',
            'priority',
            'event_impact',
            'event_scope',
            'current_assign_group',
            'current_assign_owner',
            'assigned_user',
            'assigned_user_username',
            'event_response_time',
            'event_analysis_time',
            'event_containment_time',
            'event_containment_cause',
            'ticket_resolved_time',
            'ticket_closed_time',
            'event_level',
            'event_category',
            'event_result',
            'ticket_records',
            'event_cause_category',
            'ticket_stage',
            'event_sources',
            'event_platform',
            'event_risk_score',
            'ticket_category',
            'alert_message',
            'labels',
            'create_uid',
            'is_deleted',
            'created_time',
            'updated_time',
            'sla',
            'attachments_count',
            'work_logs_count',
        ]
        read_only_fields = [
            'ticket_number',
            'created_time',
            'updated_time',
            'sla',
        ]

    def get_attachments_count(self, obj):
        """Return the count of attachments for this ticket"""
        return obj.eventticketattachment_set.count()

    def get_work_logs_count(self, obj):
        """Return the count of work logs for this ticket"""
        return obj.ticketworklog_set.count()

    def validate_labels(self, value):
        """Validate and normalize labels as unique (label_name, label_value) pairs."""
        if value is None:
            return []
        if not isinstance(value, list):
            raise serializers.ValidationError("labels must be a list")

        normalized = []
        seen_pairs = set()

        for idx, item in enumerate(value):
            if not isinstance(item, dict):
                raise serializers.ValidationError(f"labels[{idx}] must be an object")

            raw_name = item.get("label_name")
            if raw_name is None:
                raise serializers.ValidationError(f"labels[{idx}].label_name is required")

            label_name = str(raw_name).strip()
            if not label_name:
                raise serializers.ValidationError(f"labels[{idx}].label_name cannot be empty")

            raw_value = item.get("label_value", "")
            label_value = "" if raw_value is None else str(raw_value).strip()

            pair = (label_name, label_value)
            if pair in seen_pairs:
                raise serializers.ValidationError(
                    f"Duplicate label pair found: {label_name}:{label_value}"
                )
            seen_pairs.add(pair)
            normalized.append({"label_name": label_name, "label_value": label_value})

        return normalized


class EventTicketListSerializer(serializers.ModelSerializer):
    """
    Simplified serializer for listing EventTickets.

    Used for list views to reduce payload size.
    Includes SLA summary instead of full details.
    """

    sla_summary = serializers.SerializerMethodField()
    assigned_user_username = serializers.CharField(
        source='assigned_user.username',
        read_only=True,
        allow_null=True
    )

    class Meta:
        model = EventTicket
        fields = [
            'ticket_number',
            'title',
            'status',
            'priority',
            'labels',
            'event_impact',
            'assigned_user_username',
            'created_time',
            'updated_time',
            'sla_summary',
        ]
        read_only_fields = [
            'ticket_number',
            'created_time',
            'updated_time',
        ]

    def get_sla_summary(self, obj):
        """
        Return a summary of SLA metrics if available.
        Returns None if SLA record doesn't exist yet.
        """
        try:
            sla = obj.sla
            return {
                'mtta_seconds': sla.mtta_seconds,
                'mtti_seconds': sla.mtti_seconds,
                'mttc_seconds': sla.mttc_seconds,
                'mttr_seconds': sla.mttr_seconds,
            }
        except TicketSLA.DoesNotExist:
            return None
