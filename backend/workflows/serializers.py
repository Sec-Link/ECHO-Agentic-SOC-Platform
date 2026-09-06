"""
Workflow Serializers

Serializers for the workflow API endpoints.
"""
from rest_framework import serializers
from django.db import transaction
from .models import (
    ActionTemplate,
    Workflow,
    WorkflowStep,
    WorkflowExecution,
    StepExecution,
    SavedWorkflowNode,
    WorkflowSchedule,
    TicketWorkflowBinding,
)
from .publisher import get_published_state
from .secret_config import (
    SecretConfigError,
    prepare_config_for_storage,
    redact_config,
)


def _secure_config(action_type, incoming, *, existing=None, require_sensitive=False):
    try:
        return prepare_config_for_storage(
            action_type or "",
            incoming,
            existing=existing,
            require_sensitive=require_sensitive,
        )
    except SecretConfigError as exc:
        raise serializers.ValidationError(exc.messages) from exc


def _step_existing_config(instance, attrs):
    existing = {}
    template = (
        attrs.get('action_template')
        if 'action_template' in attrs
        else getattr(instance, 'action_template', None)
    )
    if template and template.default_config:
        existing.update(template.default_config)
    if instance and instance.action_config:
        existing.update(instance.action_config)
    return existing


def _validate_step_timeout(action_type, config, timeout_seconds):
    if action_type != 'delay':
        return
    try:
        delay_seconds = min(max(int((config or {}).get('seconds', 5)), 1), 3600)
        timeout_value = int(timeout_seconds or 0)
    except (TypeError, ValueError) as exc:
        raise serializers.ValidationError({
            'timeout_seconds': 'Delay seconds and timeout_seconds must be integers.'
        }) from exc
    if timeout_value <= delay_seconds:
        raise serializers.ValidationError({
            'timeout_seconds': 'Delay step timeout_seconds must be greater than its seconds value.'
        })


class SensitiveConfigRepresentationMixin:
    sensitive_config_field = 'action_config'

    def _representation_action_type(self, obj):
        return getattr(obj, 'action_type', '') or ''

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        field_name = self.sensitive_config_field
        action_type = self._representation_action_type(instance)
        safe_config, configured = redact_config(action_type, representation.get(field_name) or {})
        representation[field_name] = safe_config
        representation['configured_secret_fields'] = configured
        return representation


class WorkflowPublishedStateMixin:
    def _get_published_state(self, obj):
        cache = getattr(self, '_published_state_cache', None)
        if cache is None:
            cache = {}
            self._published_state_cache = cache
        key = obj.pk
        if key not in cache:
            cache[key] = get_published_state(obj)
        return cache[key]


class ActionTemplateSerializer(SensitiveConfigRepresentationMixin, serializers.ModelSerializer):
    """Serializer for ActionTemplate model."""

    sensitive_config_field = 'default_config'
    configured_secret_fields = serializers.ListField(child=serializers.CharField(), read_only=True)

    class Meta:
        model = ActionTemplate
        fields = [
            'id', 'name', 'category', 'description', 'action_type',
            'config_schema', 'default_config', 'configured_secret_fields', 'is_active',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate(self, attrs):
        action_type = attrs.get('action_type') or getattr(self.instance, 'action_type', '')
        if 'default_config' in attrs:
            existing = getattr(self.instance, 'default_config', {}) if self.instance else {}
            attrs['default_config'] = _secure_config(
                action_type, attrs['default_config'], existing=existing
            )
        return attrs


class WorkflowStepSerializer(SensitiveConfigRepresentationMixin, serializers.ModelSerializer):
    """Serializer for WorkflowStep model."""
    action_template_name = serializers.CharField(
        source='action_template.name',
        read_only=True
    )
    configured_secret_fields = serializers.ListField(child=serializers.CharField(), read_only=True)

    class Meta:
        model = WorkflowStep
        fields = [
            'id', 'workflow', 'order', 'name',
            'node_type', 'node_category', 'position_x', 'position_y',
            'action_template', 'action_template_name', 'action_type',
            'action_config', 'configured_secret_fields', 'timeout_seconds', 'on_failure',
            'retry_count', 'retry_delay_seconds', 'condition',
            'next_step_true', 'next_step_false', 'connections',
            'is_active', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate(self, attrs):
        action_type = attrs.get('action_type') or getattr(self.instance, 'action_type', '')
        if 'action_config' in attrs:
            existing = _step_existing_config(self.instance, attrs)
            attrs['action_config'] = _secure_config(
                action_type,
                attrs['action_config'],
                existing=existing,
                require_sensitive=True,
            )
        _validate_step_timeout(
            action_type,
            attrs.get('action_config') or getattr(self.instance, 'action_config', {}),
            attrs.get('timeout_seconds', getattr(self.instance, 'timeout_seconds', 300)),
        )
        return attrs


class WorkflowStepCreateSerializer(SensitiveConfigRepresentationMixin, serializers.ModelSerializer):
    """Serializer for creating/updating WorkflowStep."""
    id = serializers.UUIDField(required=False, allow_null=True)
    next_step_true = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    next_step_false = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    connections = serializers.ListField(
        child=serializers.CharField(allow_blank=True),
        required=False,
    )
    configured_secret_fields = serializers.ListField(child=serializers.CharField(), read_only=True)

    class Meta:
        model = WorkflowStep
        fields = [
            'id', 'order', 'name', 'node_type', 'node_category', 'position_x', 'position_y',
            'action_template', 'action_type',
            'action_config', 'configured_secret_fields', 'timeout_seconds', 'on_failure',
            'retry_count', 'retry_delay_seconds', 'condition',
            'next_step_true', 'next_step_false', 'connections', 'is_active'
        ]
        read_only_fields = []
        extra_kwargs = {
            'id': {'read_only': False, 'required': False},
        }

    def validate(self, attrs):
        # Nested workflow writes need the parent serializer to merge secrets by
        # original Step ID before the existing rows are rebuilt.
        if self.parent is not None:
            return attrs
        action_type = attrs.get('action_type') or getattr(self.instance, 'action_type', '')
        if 'action_config' in attrs:
            existing = _step_existing_config(self.instance, attrs)
            attrs['action_config'] = _secure_config(
                action_type,
                attrs['action_config'],
                existing=existing,
                require_sensitive=True,
            )
        _validate_step_timeout(
            action_type,
            attrs.get('action_config') or getattr(self.instance, 'action_config', {}),
            attrs.get('timeout_seconds', getattr(self.instance, 'timeout_seconds', 300)),
        )
        return attrs


class WorkflowScheduleSerializer(serializers.ModelSerializer):
    workflow_name = serializers.CharField(source='workflow.name', read_only=True)

    class Meta:
        model = WorkflowSchedule
        fields = [
            'id', 'workflow', 'workflow_name', 'name', 'schedule_type',
            'cron', 'interval_seconds', 'timezone', 'is_active',
            'trigger_source', 'trigger_data', 'created_by',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_by', 'created_at', 'updated_at']

    def validate(self, attrs):
        schedule_type = attrs.get('schedule_type') or getattr(self.instance, 'schedule_type', None)
        cron_value = attrs.get('cron') if 'cron' in attrs else getattr(self.instance, 'cron', None)
        interval_value = attrs.get('interval_seconds') if 'interval_seconds' in attrs else getattr(self.instance, 'interval_seconds', None)

        if schedule_type == 'cron' and not cron_value:
            raise serializers.ValidationError({'cron': 'Cron expression is required for cron schedules.'})
        if schedule_type == 'interval' and not interval_value:
            raise serializers.ValidationError({'interval_seconds': 'Interval seconds is required for interval schedules.'})
        return attrs


class TicketWorkflowBindingSerializer(serializers.ModelSerializer):
    workflow_name = serializers.CharField(source='workflow.name', read_only=True)

    class Meta:
        model = TicketWorkflowBinding
        fields = [
            'id', 'name', 'workflow', 'workflow_name',
            'label_filters', 'label_filter_logic',
            'created_by', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_by', 'created_at', 'updated_at']

    def validate_label_filters(self, value):
        if value in (None, ''):
            return []
        if not isinstance(value, list):
            raise serializers.ValidationError('label_filters must be a list')
        for item in value:
            if not isinstance(item, dict) or not str(item.get('label_name') or '').strip():
                raise serializers.ValidationError('Each label filter must include label_name')
        return value


class WorkflowListSerializer(WorkflowPublishedStateMixin, serializers.ModelSerializer):
    """Serializer for listing workflows (minimal data)."""
    created_by_username = serializers.CharField(
        source='created_by.username',
        read_only=True
    )
    step_count = serializers.SerializerMethodField()
    execution_count = serializers.SerializerMethodField()
    last_execution = serializers.SerializerMethodField()
    published_version = serializers.SerializerMethodField()
    published_at = serializers.SerializerMethodField()
    has_unpublished_changes = serializers.SerializerMethodField()

    class Meta:
        model = Workflow
        fields = [
            'id', 'name', 'description', 'trigger_type', 'execution_engine',
            'prefect_deployment_id', 'inputs_schema', 'is_callable_from_ticket',
            'allowed_invoker_roles',
            'is_active', 'is_draft', 'version', 'published_version', 'published_at', 'has_unpublished_changes',
            'tags', 'created_by', 'created_by_username',
            'step_count', 'execution_count', 'last_execution',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_step_count(self, obj):
        return obj.steps.count()

    def get_execution_count(self, obj):
        return obj.executions.count()

    def get_last_execution(self, obj):
        last = obj.executions.first()
        if last:
            return {
                'id': str(last.id),
                'status': last.status,
                'started_at': last.started_at,
                'completed_at': last.completed_at,
            }
        return None

    def get_published_version(self, obj):
        return self._get_published_state(obj).get('published_version')

    def get_published_at(self, obj):
        return self._get_published_state(obj).get('published_at')

    def get_has_unpublished_changes(self, obj):
        return self._get_published_state(obj).get('has_unpublished_changes')


class WorkflowDetailSerializer(WorkflowPublishedStateMixin, serializers.ModelSerializer):
    """Serializer for workflow detail view (includes steps)."""
    created_by_username = serializers.CharField(
        source='created_by.username',
        read_only=True
    )
    steps = WorkflowStepSerializer(many=True, read_only=True)
    schedules = WorkflowScheduleSerializer(many=True, read_only=True)
    published_version = serializers.SerializerMethodField()
    published_at = serializers.SerializerMethodField()
    has_unpublished_changes = serializers.SerializerMethodField()

    class Meta:
        model = Workflow
        fields = [
            'id', 'name', 'description', 'trigger_type', 'execution_engine',
            'prefect_deployment_id', 'inputs_schema', 'is_callable_from_ticket',
            'allowed_invoker_roles',
            'trigger_conditions',
            'schedule_cron', 'is_active', 'is_draft', 'version', 'published_version', 'published_at', 'has_unpublished_changes', 'tags',
            'edges', 'created_by', 'created_by_username', 'steps',
            'schedules',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_published_version(self, obj):
        return self._get_published_state(obj).get('published_version')

    def get_published_at(self, obj):
        return self._get_published_state(obj).get('published_at')

    def get_has_unpublished_changes(self, obj):
        return self._get_published_state(obj).get('has_unpublished_changes')


class WorkflowCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating/updating workflows."""
    steps = WorkflowStepCreateSerializer(many=True, required=False)

    class Meta:
        model = Workflow
        fields = [
            'id', 'name', 'description', 'trigger_type', 'execution_engine',
            'prefect_deployment_id', 'inputs_schema', 'is_callable_from_ticket',
            'allowed_invoker_roles',
            'trigger_conditions',
            'schedule_cron', 'is_active', 'is_draft', 'version', 'tags', 'edges', 'steps'
        ]
        read_only_fields = ['id']

    @staticmethod
    def _to_uuid_or_none(value):
        import uuid as uuid_module

        if not value:
            return None
        if isinstance(value, uuid_module.UUID):
            return value
        try:
            return uuid_module.UUID(str(value))
        except (ValueError, TypeError, AttributeError):
            return None

    def _sanitize_step_references(self, step_data):
        step_data['next_step_true'] = self._to_uuid_or_none(step_data.get('next_step_true'))
        step_data['next_step_false'] = self._to_uuid_or_none(step_data.get('next_step_false'))

        connections = step_data.get('connections')
        if isinstance(connections, list):
            sanitized = []
            for item in connections:
                parsed = self._to_uuid_or_none(item)
                if parsed is not None:
                    sanitized.append(str(parsed))
            step_data['connections'] = sanitized

    def _prepare_step_config(self, step_data, existing_config=None):
        action_type = step_data.get('action_type') or ''
        base_config = {}
        action_template = step_data.get('action_template')
        if action_template and action_template.default_config:
            base_config.update(action_template.default_config)
        if self.context.get('preserve_existing_secrets', True):
            base_config.update(existing_config or {})
        step_data['action_config'] = _secure_config(
            action_type,
            step_data.get('action_config') or {},
            existing=base_config,
            require_sensitive=self.context.get('require_sensitive', True),
        )
        _validate_step_timeout(
            action_type,
            step_data['action_config'],
            step_data.get('timeout_seconds', 300),
        )
        return step_data

    def _create_step(self, workflow, step_data, order_offset=0, existing_config=None):
        step_id = self._to_uuid_or_none(step_data.pop('id', None))
        self._sanitize_step_references(step_data)
        self._prepare_step_config(step_data, existing_config)

        if 'order' in step_data:
            step_data['order'] = step_data['order'] + order_offset

        if step_id:
            step = WorkflowStep(id=step_id, workflow=workflow, **step_data)
            step.save()
        else:
            step = WorkflowStep.objects.create(workflow=workflow, **step_data)

        return step

    @transaction.atomic
    def create(self, validated_data):
        steps_data = validated_data.pop('steps', [])
        workflow = Workflow.objects.create(**validated_data)

        for step_data in steps_data:
            self._create_step(workflow, step_data.copy())

        return workflow

    @transaction.atomic
    def update(self, instance, validated_data):
        steps_data = validated_data.pop('steps', None)

        prepared_steps = None
        if steps_data is not None:
            existing_steps = {
                str(step.id): step.action_config or {}
                for step in instance.steps.all()
            }
            prepared_steps = []
            for raw_step_data in steps_data:
                step_data = raw_step_data.copy()
                original_id = self._to_uuid_or_none(step_data.get('id'))
                self._prepare_step_config(
                    step_data,
                    existing_steps.get(str(original_id), {}),
                )
                prepared_steps.append(step_data)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if prepared_steps is not None:
            instance.steps.all().delete()
            for step_data in prepared_steps:
                self._create_step(instance, step_data.copy())

        return instance


class StepExecutionSerializer(SensitiveConfigRepresentationMixin, serializers.ModelSerializer):
    """Serializer for StepExecution model."""
    step = serializers.UUIDField(source='source_step_id', read_only=True)
    step_name = serializers.CharField(read_only=True)
    step_order = serializers.IntegerField(read_only=True)
    action_type = serializers.CharField(read_only=True)
    duration_seconds = serializers.SerializerMethodField()
    configured_secret_fields = serializers.ListField(child=serializers.CharField(), read_only=True)
    sensitive_config_field = 'input_data'

    def _representation_action_type(self, obj):
        return obj.action_type or ''

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        input_data = representation.get('input_data') or {}
        nested = input_data.get('action_config') if isinstance(input_data, dict) else None
        if isinstance(nested, dict):
            safe_nested, nested_configured = redact_config(
                self._representation_action_type(instance), nested
            )
            input_data['action_config'] = safe_nested
            representation['configured_secret_fields'] = sorted(set(
                representation.get('configured_secret_fields', []) + nested_configured
            ))
        return representation

    class Meta:
        model = StepExecution
        fields = [
            'id', 'step', 'step_name', 'step_order', 'action_type',
            'status', 'attempt_number', 'started_at', 'completed_at',
            'input_data', 'configured_secret_fields', 'output_data', 'error_message', 'logs',
            'duration_seconds', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_duration_seconds(self, obj):
        return obj.get_duration_seconds()


class WorkflowExecutionListSerializer(serializers.ModelSerializer):
    """Serializer for listing workflow executions."""
    workflow_name = serializers.CharField(source='workflow.name', read_only=True)
    executed_by_username = serializers.CharField(
        source='executed_by.username',
        read_only=True
    )
    duration = serializers.SerializerMethodField()

    class Meta:
        model = WorkflowExecution
        fields = [
            'id', 'workflow', 'workflow_name', 'trigger_source', 'status',
            'workflow_version',
            'current_step', 'total_steps', 'completed_steps', 'progress_percent',
            'started_at', 'completed_at', 'duration',
            'executed_by', 'executed_by_username',
            'task_result_id',
            'created_at',
        ]
        read_only_fields = ['id', 'workflow_version', 'created_at']

    def get_duration(self, obj):
        return obj.get_duration_display()


class WorkflowExecutionDetailSerializer(serializers.ModelSerializer):
    """Serializer for workflow execution detail view."""
    workflow_name = serializers.CharField(source='workflow.name', read_only=True)
    executed_by_username = serializers.CharField(
        source='executed_by.username',
        read_only=True
    )
    step_executions = StepExecutionSerializer(many=True, read_only=True)
    duration = serializers.SerializerMethodField()
    duration_seconds = serializers.SerializerMethodField()

    class Meta:
        model = WorkflowExecution
        fields = [
            'id', 'workflow', 'workflow_name', 'trigger_source', 'trigger_data',
            'workflow_version',
            'status', 'current_step', 'total_steps', 'completed_steps',
            'progress_percent', 'started_at', 'completed_at', 'duration',
            'duration_seconds', 'result_data', 'error_message', 'context',
            'executed_by', 'executed_by_username',
            'task_result_id',
            'step_executions',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'workflow_version', 'created_at', 'updated_at']

    def get_duration(self, obj):
        return obj.get_duration_display()

    def get_duration_seconds(self, obj):
        return obj.get_duration_seconds()


class WorkflowExecuteSerializer(serializers.Serializer):
    """Serializer for triggering a workflow execution."""
    trigger_data = serializers.JSONField(required=False, default=dict)
    trigger_source = serializers.CharField(required=False, default='manual')
    confirm_mass_update = serializers.BooleanField(required=False, default=False)


class RuntimeRegistrationSerializer(serializers.Serializer):
    workflow_id = serializers.UUIDField()
    workflow_version = serializers.IntegerField(min_value=1)
    prefect_flow_run_id = serializers.UUIDField()
    trigger_source = serializers.CharField(required=False, default='schedule')
    trigger_data = serializers.JSONField(required=False, default=dict)
    total_steps = serializers.IntegerField(min_value=0)


class RuntimeStepResultSerializer(serializers.Serializer):
    step_id = serializers.UUIDField()
    status = serializers.ChoiceField(choices=[choice[0] for choice in StepExecution.STATUS_CHOICES])
    attempt_number = serializers.IntegerField(min_value=1, required=False, default=1)
    input_data = serializers.JSONField(required=False, default=dict)
    output_data = serializers.JSONField(required=False, default=dict)
    error_message = serializers.CharField(required=False, allow_blank=True, default='')
    logs = serializers.CharField(required=False, allow_blank=True, default='')
    started_at = serializers.DateTimeField(required=False, allow_null=True)
    completed_at = serializers.DateTimeField(required=False, allow_null=True)


class RuntimeSnapshotSerializer(serializers.Serializer):
    prefect_flow_run_id = serializers.UUIDField()
    status = serializers.ChoiceField(choices=[choice[0] for choice in WorkflowExecution.STATUS_CHOICES])
    current_step = serializers.IntegerField(min_value=0)
    total_steps = serializers.IntegerField(min_value=0)
    context = serializers.JSONField(required=False, default=dict)
    error_message = serializers.CharField(required=False, allow_blank=True, default='')
    step_results = RuntimeStepResultSerializer(many=True, required=False, default=list)


class SavedWorkflowNodeSerializer(SensitiveConfigRepresentationMixin, serializers.ModelSerializer):
    """Serializer for reusable saved workflow nodes."""
    created_by_username = serializers.CharField(source='created_by.username', read_only=True)
    configured_secret_fields = serializers.ListField(child=serializers.CharField(), read_only=True)

    class Meta:
        model = SavedWorkflowNode
        fields = [
            'id', 'name', 'node_type', 'node_category',
            'action_type', 'action_config', 'configured_secret_fields', 'timeout_seconds', 'on_failure',
            'retry_count', 'retry_delay_seconds', 'condition', 'is_active',
            'created_by', 'created_by_username', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_by', 'created_by_username', 'created_at', 'updated_at']

    def validate(self, attrs):
        action_type = attrs.get('action_type') or getattr(self.instance, 'action_type', '')
        if 'action_config' in attrs:
            existing = getattr(self.instance, 'action_config', {}) if self.instance else {}
            attrs['action_config'] = _secure_config(
                action_type,
                attrs['action_config'],
                existing=existing,
                require_sensitive=True,
            )
        _validate_step_timeout(
            action_type,
            attrs.get('action_config') or getattr(self.instance, 'action_config', {}),
            attrs.get('timeout_seconds', getattr(self.instance, 'timeout_seconds', 300)),
        )
        return attrs

