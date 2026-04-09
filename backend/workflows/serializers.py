"""
Workflow Serializers

Serializers for the workflow API endpoints.
"""
from rest_framework import serializers
from .models import (
    ActionTemplate,
    Workflow,
    WorkflowStep,
    WorkflowExecution,
    StepExecution,
    SavedWorkflowNode,
)


class ActionTemplateSerializer(serializers.ModelSerializer):
    """Serializer for ActionTemplate model."""

    class Meta:
        model = ActionTemplate
        fields = [
            'id', 'name', 'category', 'description', 'action_type',
            'config_schema', 'default_config', 'is_active',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class WorkflowStepSerializer(serializers.ModelSerializer):
    """Serializer for WorkflowStep model."""
    action_template_name = serializers.CharField(
        source='action_template.name',
        read_only=True
    )

    class Meta:
        model = WorkflowStep
        fields = [
            'id', 'workflow', 'order', 'name',
            'node_type', 'node_category', 'position_x', 'position_y',
            'action_template', 'action_template_name', 'action_type',
            'action_config', 'timeout_seconds', 'on_failure',
            'retry_count', 'retry_delay_seconds', 'condition',
            'next_step_true', 'next_step_false', 'connections',
            'is_active', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class WorkflowStepCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating/updating WorkflowStep."""
    id = serializers.UUIDField(required=False, allow_null=True)
    next_step_true = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    next_step_false = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    connections = serializers.ListField(
        child=serializers.CharField(allow_blank=True),
        required=False,
    )

    class Meta:
        model = WorkflowStep
        fields = [
            'id', 'order', 'name', 'node_type', 'node_category', 'position_x', 'position_y',
            'action_template', 'action_type',
            'action_config', 'timeout_seconds', 'on_failure',
            'retry_count', 'retry_delay_seconds', 'condition',
            'next_step_true', 'next_step_false', 'connections', 'is_active'
        ]
        read_only_fields = []
        extra_kwargs = {
            'id': {'read_only': False, 'required': False},
        }


class WorkflowListSerializer(serializers.ModelSerializer):
    """Serializer for listing workflows (minimal data)."""
    created_by_username = serializers.CharField(
        source='created_by.username',
        read_only=True
    )
    step_count = serializers.SerializerMethodField()
    execution_count = serializers.SerializerMethodField()
    last_execution = serializers.SerializerMethodField()

    class Meta:
        model = Workflow
        fields = [
            'id', 'name', 'description', 'trigger_type', 'is_active',
            'is_draft', 'version', 'tags', 'created_by', 'created_by_username',
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


class WorkflowDetailSerializer(serializers.ModelSerializer):
    """Serializer for workflow detail view (includes steps)."""
    created_by_username = serializers.CharField(
        source='created_by.username',
        read_only=True
    )
    steps = WorkflowStepSerializer(many=True, read_only=True)

    class Meta:
        model = Workflow
        fields = [
            'id', 'name', 'description', 'trigger_type', 'trigger_conditions',
            'schedule_cron', 'is_active', 'is_draft', 'version', 'tags',
            'edges', 'created_by', 'created_by_username', 'steps',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class WorkflowCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating/updating workflows."""
    steps = WorkflowStepCreateSerializer(many=True, required=False)

    class Meta:
        model = Workflow
        fields = [
            'id', 'name', 'description', 'trigger_type', 'trigger_conditions',
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

    def _create_step(self, workflow, step_data, order_offset=0):
        step_id = self._to_uuid_or_none(step_data.pop('id', None))
        self._sanitize_step_references(step_data)

        if 'order' in step_data:
            step_data['order'] = step_data['order'] + order_offset

        if step_id:
            step = WorkflowStep(id=step_id, workflow=workflow, **step_data)
            step.save()
        else:
            step = WorkflowStep.objects.create(workflow=workflow, **step_data)

        return step

    def create(self, validated_data):
        steps_data = validated_data.pop('steps', [])
        workflow = Workflow.objects.create(**validated_data)

        for step_data in steps_data:
            self._create_step(workflow, step_data.copy())

        return workflow

    def update(self, instance, validated_data):
        steps_data = validated_data.pop('steps', None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if steps_data is not None:
            instance.steps.all().delete()
            for step_data in steps_data:
                self._create_step(instance, step_data.copy())

        return instance


class StepExecutionSerializer(serializers.ModelSerializer):
    """Serializer for StepExecution model."""
    step_name = serializers.CharField(source='step.name', read_only=True)
    step_order = serializers.IntegerField(source='step.order', read_only=True)
    action_type = serializers.CharField(source='step.action_type', read_only=True)
    duration_seconds = serializers.SerializerMethodField()

    class Meta:
        model = StepExecution
        fields = [
            'id', 'step', 'step_name', 'step_order', 'action_type',
            'status', 'attempt_number', 'started_at', 'completed_at',
            'input_data', 'output_data', 'error_message', 'logs',
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
            'current_step', 'total_steps', 'completed_steps', 'progress_percent',
            'started_at', 'completed_at', 'duration',
            'executed_by', 'executed_by_username',
            'task_result_id',
            'created_at',
        ]
        read_only_fields = ['id', 'created_at']

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
            'status', 'current_step', 'total_steps', 'completed_steps',
            'progress_percent', 'started_at', 'completed_at', 'duration',
            'duration_seconds', 'result_data', 'error_message', 'context',
            'executed_by', 'executed_by_username',
            'task_result_id',
            'step_executions',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_duration(self, obj):
        return obj.get_duration_display()

    def get_duration_seconds(self, obj):
        return obj.get_duration_seconds()


class WorkflowExecuteSerializer(serializers.Serializer):
    """Serializer for triggering a workflow execution."""
    trigger_data = serializers.JSONField(required=False, default=dict)
    trigger_source = serializers.CharField(required=False, default='manual')
    confirm_mass_update = serializers.BooleanField(required=False, default=False)


class SavedWorkflowNodeSerializer(serializers.ModelSerializer):
    """Serializer for reusable saved workflow nodes."""
    created_by_username = serializers.CharField(source='created_by.username', read_only=True)

    class Meta:
        model = SavedWorkflowNode
        fields = [
            'id', 'name', 'node_type', 'node_category',
            'action_type', 'action_config', 'timeout_seconds', 'on_failure',
            'retry_count', 'retry_delay_seconds', 'condition', 'is_active',
            'created_by', 'created_by_username', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_by', 'created_by_username', 'created_at', 'updated_at']


