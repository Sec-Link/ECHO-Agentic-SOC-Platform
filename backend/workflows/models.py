"""
Workflows Models

Defines the database models for SOAR workflow management:
- Workflow: The main workflow/playbook definition
- WorkflowStep: Individual steps within a workflow
- WorkflowExecution: Records of workflow runs
- StepExecution: Records of individual step executions
- ActionTemplate: Reusable action templates
"""
import uuid
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


def _secure_action_config(instance, field_name, action_type):
    """Encrypt sensitive values and preserve omitted write-only values on save."""
    from .secret_config import prepare_config_for_storage

    existing = {}
    if instance.pk:
        existing = (
            instance.__class__.objects.filter(pk=instance.pk)
            .values_list(field_name, flat=True)
            .first()
            or {}
        )
    current = getattr(instance, field_name) or {}
    secured = prepare_config_for_storage(action_type or "", current, existing=existing)
    setattr(instance, field_name, secured)
    return secured != current


def _include_secured_field(kwargs, field_name):
    update_fields = kwargs.get("update_fields")
    if update_fields is not None and field_name not in update_fields:
        kwargs["update_fields"] = set(update_fields) | {field_name}


class ActionTemplate(models.Model):
    """
    Reusable action templates that can be used in workflow steps.
    These define what actions are available in the system.
    """
    ACTION_CATEGORIES = [
        ('enrichment', 'Enrichment'),
        ('containment', 'Containment'),
        ('release', 'Release'),
        ('notification', 'Notification'),
        ('integration', 'Integration'),
        ('utility', 'Utility'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200, unique=True, help_text="Action template name")
    category = models.CharField(
        max_length=50,
        choices=ACTION_CATEGORIES,
        default='utility',
        help_text="Action category"
    )
    description = models.TextField(blank=True, help_text="Action description")

    # The action type identifier used by the execution engine
    action_type = models.CharField(
        max_length=100,
        unique=True,
        help_text="Unique action type identifier (e.g., 'send_email', 'block_ip')"
    )

    # JSON schema for the action's configuration parameters
    config_schema = models.JSONField(
        default=dict,
        help_text="JSON schema defining required/optional parameters"
    )

    # Default configuration values
    default_config = models.JSONField(
        default=dict,
        help_text="Default configuration values"
    )

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['category', 'name']
        verbose_name = 'Action Template'
        verbose_name_plural = 'Action Templates'

    def __str__(self):
        return f"{self.name} ({self.action_type})"

    def save(self, *args, **kwargs):
        if _secure_action_config(self, "default_config", self.action_type):
            _include_secured_field(kwargs, "default_config")
        return super().save(*args, **kwargs)


class Workflow(models.Model):
    """
    Main workflow/playbook definition.
    Users can create custom workflows with multiple steps.
    """
    TRIGGER_TYPES = [
        ('manual', 'Manual Execution'),
        ('alert', 'On Alert Created'),
        ('ticket_created', 'On Ticket Created'),
        ('ticket_status', 'On Ticket Status Change'),
        ('scheduled', 'Scheduled'),
        ('webhook', 'External Webhook'),
    ]

    EXECUTION_ENGINES = [
        ('prefect', 'Prefect'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200, help_text="Workflow name")
    description = models.TextField(blank=True, help_text="Workflow description")

    # Historical rows may still contain ``local``. New workflows use Prefect.
    execution_engine = models.CharField(
        max_length=20,
        choices=EXECUTION_ENGINES,
        default='prefect',
        help_text="Engine that runs this workflow. New workflows use Prefect."
    )
    prefect_deployment_id = models.CharField(
        max_length=64,
        blank=True,
        default='',
        help_text="Optional Prefect deployment id override for this workflow."
    )

    # Trigger configuration
    trigger_type = models.CharField(
        max_length=50,
        choices=TRIGGER_TYPES,
        default='manual',
        help_text="What triggers this workflow"
    )
    trigger_conditions = models.JSONField(
        default=dict,
        blank=True,
        help_text="Filter conditions for automatic triggers (JSON)"
    )
    inputs_schema = models.JSONField(
        default=list,
        blank=True,
        help_text="Input definitions for ticket-context workflow invocation."
    )
    is_callable_from_ticket = models.BooleanField(
        default=False,
        help_text="Whether this workflow can be invoked from ticket context."
    )
    allowed_invoker_roles = models.JSONField(
        default=list,
        blank=True,
        help_text="Django group names allowed to invoke this workflow from tickets."
    )

    # Scheduling (for scheduled trigger type)
    schedule_cron = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="Cron expression for scheduled workflows (e.g., '0 */4 * * *')"
    )

    # Workflow status
    is_active = models.BooleanField(default=True, help_text="Is this workflow active?")
    is_draft = models.BooleanField(default=True, help_text="Is this a draft version?")
    version = models.PositiveIntegerField(default=1, help_text="Workflow version number")

    # Ownership
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_workflows',
        help_text="User who created this workflow"
    )

    # Metadata
    tags = models.JSONField(default=list, blank=True, help_text="Tags for categorization")

    # Visual workflow editor data - stores edges/connections between nodes
    edges = models.JSONField(
        default=list,
        blank=True,
        help_text="List of edge connections for visual workflow editor"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        verbose_name = 'Workflow'
        verbose_name_plural = 'Workflows'

    def __str__(self):
        return f"{self.name} v{self.version}"

    def get_steps_ordered(self):
        """Get all steps in execution order."""
        return self.steps.all().order_by('order')

    def clone(self, new_name=None, user=None):
        """Create a copy of this workflow."""
        new_workflow = Workflow.objects.create(
            name=new_name or f"{self.name} (Copy)",
            description=self.description,
            trigger_type=self.trigger_type,
            trigger_conditions=self.trigger_conditions,
            schedule_cron=self.schedule_cron,
            is_active=False,
            is_draft=True,
            version=1,
            created_by=user or self.created_by,
            tags=self.tags.copy() if self.tags else [],
        )

        # Clone all steps
        for step in self.steps.all():
            WorkflowStep.objects.create(
                workflow=new_workflow,
                order=step.order,
                name=step.name,
                node_category=step.node_category,
                action_type=step.action_type,
                action_config=step.action_config.copy() if step.action_config else {},
                timeout_seconds=step.timeout_seconds,
                on_failure=step.on_failure,
                retry_count=step.retry_count,
                condition=step.condition,
                is_active=step.is_active,
            )

        return new_workflow


class TicketWorkflowBinding(models.Model):
    LABEL_LOGIC = [
        ('AND', 'All labels must match'),
        ('OR', 'Any label may match'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    workflow = models.ForeignKey(
        Workflow,
        on_delete=models.CASCADE,
        related_name='ticket_workflow_bindings',
    )
    label_filters = models.JSONField(default=list, blank=True)
    label_filter_logic = models.CharField(max_length=3, choices=LABEL_LOGIC, default='AND')
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ticket_workflow_bindings',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        verbose_name = 'Ticket Workflow Binding'
        verbose_name_plural = 'Ticket Workflow Bindings'

    def __str__(self):
        return f"{self.name} -> {self.workflow.name}"


class WorkflowStep(models.Model):
    """
    Individual step within a workflow.
    Steps are executed in order and can have conditions.
    Supports different node types for visual workflow editing.
    """
    ON_FAILURE_CHOICES = [
        ('stop', 'Stop Workflow'),
        ('continue', 'Continue to Next Step'),
        ('retry', 'Retry Step'),
        ('skip', 'Skip to Next Step'),
    ]

    # Node types for visual workflow editor
    NODE_TYPE_CHOICES = [
        ('action', 'Action Node'),
        ('condition', 'Condition Node'),
        ('start', 'Start Node'),
        ('end', 'End Node'),
        # 'parallel' removed – use Condition nodes for branching instead
    ]

    NODE_CATEGORY_CHOICES = ActionTemplate.ACTION_CATEGORIES + [
        ('control', 'Control'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workflow = models.ForeignKey(
        Workflow,
        on_delete=models.CASCADE,
        related_name='steps',
        help_text="Parent workflow"
    )

    # Node type for visual editor
    node_type = models.CharField(
        max_length=20,
        choices=NODE_TYPE_CHOICES,
        default='action',
        help_text="Type of node in the visual workflow editor"
    )
    node_category = models.CharField(
        max_length=50,
        default='utility',
        help_text="Category used to group nodes in the visual workflow editor"
    )

    # Visual position in the editor (for drag-and-drop)
    position_x = models.FloatField(
        default=0,
        help_text="X coordinate in the visual editor"
    )
    position_y = models.FloatField(
        default=0,
        help_text="Y coordinate in the visual editor"
    )

    # Step ordering
    order = models.PositiveIntegerField(default=0, help_text="Execution order (0-based)")
    name = models.CharField(max_length=200, help_text="Step name/description")

    # Action configuration
    action_template = models.ForeignKey(
        ActionTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='workflow_steps',
        help_text="Action template to use"
    )
    action_type = models.CharField(
        max_length=100,
        help_text="Action type identifier (fallback if no template)"
    )
    action_config = models.JSONField(
        default=dict,
        help_text="Step-specific configuration parameters"
    )

    # Execution settings
    timeout_seconds = models.PositiveIntegerField(
        default=300,
        help_text="Maximum execution time in seconds"
    )
    on_failure = models.CharField(
        max_length=20,
        choices=ON_FAILURE_CHOICES,
        default='stop',
        help_text="Action to take if step fails"
    )
    retry_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of retry attempts"
    )
    retry_delay_seconds = models.PositiveIntegerField(
        default=30,
        help_text="Delay between retries in seconds"
    )

    # Conditional execution
    condition = models.JSONField(
        default=dict,
        blank=True,
        help_text="Condition for executing this step (JSON expression)"
    )

    # Condition branching - for condition nodes
    next_step_true = models.UUIDField(
        null=True,
        blank=True,
        help_text="Next step ID if condition evaluates to true"
    )
    next_step_false = models.UUIDField(
        null=True,
        blank=True,
        help_text="Next step ID if condition evaluates to false"
    )

    # Visual connections for workflow editor (stores edge connections)
    connections = models.JSONField(
        default=list,
        blank=True,
        help_text="List of connected step IDs for visual editor"
    )

    is_active = models.BooleanField(default=True, help_text="Is this step active?")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['workflow', 'order']
        # Removed unique_together to allow flexible node ordering in visual editor
        verbose_name = 'Workflow Step'
        verbose_name_plural = 'Workflow Steps'

    def __str__(self):
        return f"{self.workflow.name} - Step {self.order}: {self.name}"

    def save(self, *args, **kwargs):
        if _secure_action_config(self, "action_config", self.action_type):
            _include_secured_field(kwargs, "action_config")
        return super().save(*args, **kwargs)


class SavedWorkflowNode(models.Model):
    """Reusable node template saved from workflow editor (Save As)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    node_type = models.CharField(max_length=20, choices=WorkflowStep.NODE_TYPE_CHOICES, default='action')
    node_category = models.CharField(max_length=50, default='utility')
    action_type = models.CharField(max_length=100, blank=True)
    action_config = models.JSONField(default=dict, blank=True)
    timeout_seconds = models.PositiveIntegerField(default=300)
    on_failure = models.CharField(max_length=20, choices=WorkflowStep.ON_FAILURE_CHOICES, default='stop')
    retry_count = models.PositiveIntegerField(default=0)
    retry_delay_seconds = models.PositiveIntegerField(default=30)
    condition = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='saved_workflow_nodes')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['node_category', 'name']

    def __str__(self):
        return f"{self.name} ({self.node_category})"

    def save(self, *args, **kwargs):
        if _secure_action_config(self, "action_config", self.action_type):
            _include_secured_field(kwargs, "action_config")
        return super().save(*args, **kwargs)


class WorkflowExecution(models.Model):
    """
    Records the execution of a workflow instance.
    Tracks overall status and results.
    """
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('running', 'Running'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
        ('paused', 'Paused'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workflow = models.ForeignKey(
        Workflow,
        on_delete=models.CASCADE,
        related_name='executions',
        help_text="Workflow being executed"
    )
    workflow_version = models.PositiveIntegerField(
        help_text="Immutable published workflow version used by this execution",
    )

    # Trigger information
    trigger_source = models.CharField(
        max_length=200,
        blank=True,
        help_text="What triggered this execution (e.g., 'ticket:SEC-001', 'manual')"
    )
    trigger_data = models.JSONField(
        default=dict,
        help_text="Data from the trigger event"
    )

    # Execution status
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending',
        help_text="Current execution status"
    )
    current_step = models.PositiveIntegerField(
        default=0,
        help_text="Index of the currently executing step"
    )

    # Progress tracking
    total_steps = models.PositiveIntegerField(
        default=0,
        help_text="Total number of steps"
    )
    completed_steps = models.PositiveIntegerField(
        default=0,
        help_text="Number of completed steps"
    )
    progress_percent = models.FloatField(
        default=0.0,
        help_text="Execution progress percentage"
    )

    # Timing
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    # Results
    result_data = models.JSONField(
        default=dict,
        help_text="Aggregated results from all steps"
    )
    error_message = models.TextField(
        blank=True,
        help_text="Error message if execution failed"
    )

    # Context variables accumulated during execution
    context = models.JSONField(
        default=dict,
        help_text="Execution context (variables, step outputs)"
    )

    # Who triggered it
    executed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='workflow_executions',
        help_text="User who triggered this execution"
    )

    # Kept under its existing name to avoid a schema-only rename. Prefect-backed
    # executions store the Prefect Flow Run ID here.
    task_result_id = models.CharField(
        max_length=64,
        blank=True,
        default='',
        help_text="ID of the django.tasks TaskResult for this execution"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Workflow Execution'
        verbose_name_plural = 'Workflow Executions'
        constraints = [
            models.UniqueConstraint(
                fields=['task_result_id'],
                condition=~models.Q(task_result_id=''),
                name='uniq_workflow_execution_flow_run',
            ),
        ]

    def __str__(self):
        return f"{self.workflow.name} - {self.status} ({self.id})"

    def get_duration_seconds(self):
        """Calculate execution duration in seconds."""
        if self.started_at:
            end_time = self.completed_at or timezone.now()
            return (end_time - self.started_at).total_seconds()
        return 0

    def get_duration_display(self):
        """Return human-readable duration string."""
        seconds = int(self.get_duration_seconds())
        if seconds < 60:
            return f"{seconds}s"
        elif seconds < 3600:
            return f"{seconds // 60}m {seconds % 60}s"
        else:
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            return f"{hours}h {minutes}m"

    def update_progress(self):
        """Update progress percentage based on completed steps."""
        if self.total_steps > 0:
            self.progress_percent = (self.completed_steps / self.total_steps) * 100
        else:
            self.progress_percent = 0


class StepExecution(models.Model):
    """
    Records the execution of an individual workflow step.
    """
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('running', 'Running'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('skipped', 'Skipped'),
        ('cancelled', 'Cancelled'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workflow_execution = models.ForeignKey(
        WorkflowExecution,
        on_delete=models.CASCADE,
        related_name='step_executions',
        help_text="Parent workflow execution"
    )
    step = models.ForeignKey(
        WorkflowStep,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='executions',
        help_text="Current draft step when it still exists",
    )
    source_step_id = models.UUIDField(
        help_text="Immutable step ID from the published workflow manifest",
    )
    step_name = models.CharField(
        max_length=200,
        help_text="Immutable step name from the published workflow manifest",
    )
    step_order = models.PositiveIntegerField(
        help_text="Immutable step order from the published workflow manifest",
    )
    action_type = models.CharField(
        max_length=100,
        blank=True,
        help_text="Immutable action type from the published workflow manifest",
    )

    # Execution status
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending',
        help_text="Current step status"
    )
    attempt_number = models.PositiveIntegerField(
        default=1,
        help_text="Current attempt number (for retries)"
    )

    # Timing
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    # Input/Output
    input_data = models.JSONField(
        default=dict,
        help_text="Input data passed to this step"
    )
    output_data = models.JSONField(
        default=dict,
        help_text="Output data from this step"
    )

    # Error tracking
    error_message = models.TextField(
        blank=True,
        help_text="Error message if step failed"
    )
    error_traceback = models.TextField(
        blank=True,
        help_text="Full error traceback"
    )

    # Logs
    logs = models.TextField(
        blank=True,
        help_text="Execution logs"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['workflow_execution', 'step_order']
        verbose_name = 'Step Execution'
        verbose_name_plural = 'Step Executions'
        constraints = [
            models.UniqueConstraint(
                fields=['workflow_execution', 'source_step_id'],
                name='uniq_step_exec_source',
            ),
        ]

    def __str__(self):
        return f"{self.step_name} - {self.status}"

    def save(self, *args, **kwargs):
        from .secret_config import prepare_config_for_storage

        existing = {}
        if self.pk:
            existing = (
                StepExecution.objects.filter(pk=self.pk)
                .values_list("input_data", flat=True)
                .first()
                or {}
            )
        current = self.input_data or {}
        if not isinstance(current, dict):
            return super().save(*args, **kwargs)
        if isinstance(current.get("action_config"), dict):
            secured = dict(current)
            existing_action_config = (
                existing.get("action_config", {})
                if isinstance(existing.get("action_config"), dict)
                else {}
            )
            secured["action_config"] = prepare_config_for_storage(
                self.action_type,
                current["action_config"],
                existing=existing_action_config,
            )
        else:
            secured = prepare_config_for_storage(
                self.action_type,
                current,
                existing=existing,
            )
        if secured != current:
            self.input_data = secured
            _include_secured_field(kwargs, "input_data")
        return super().save(*args, **kwargs)

    def get_duration_seconds(self):
        """Calculate step duration in seconds."""
        if self.started_at:
            end_time = self.completed_at or timezone.now()
            return (end_time - self.started_at).total_seconds()
        return 0


class WorkflowSchedule(models.Model):
    """Execution schedules for workflows (cron/interval)."""

    SCHEDULE_TYPES = [
        ('cron', 'Cron'),
        ('interval', 'Interval (seconds)'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workflow = models.ForeignKey(
        Workflow,
        on_delete=models.CASCADE,
        related_name='schedules',
        help_text="Workflow this schedule belongs to",
    )
    name = models.CharField(max_length=200, default='default')
    schedule_type = models.CharField(
        max_length=20,
        choices=SCHEDULE_TYPES,
        default='cron',
    )
    cron = models.CharField(max_length=100, blank=True, null=True)
    interval_seconds = models.PositiveIntegerField(blank=True, null=True)
    timezone = models.CharField(max_length=64, default='UTC')
    is_active = models.BooleanField(default=True)
    trigger_source = models.CharField(max_length=200, default='schedule')
    trigger_data = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='workflow_schedules',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Workflow Schedule'
        verbose_name_plural = 'Workflow Schedules'

    def __str__(self):
        return f"{self.workflow.name} - {self.name}"
