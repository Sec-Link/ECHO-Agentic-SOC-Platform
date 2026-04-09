"""
Workflow Admin Configuration
"""
from django.contrib import admin
from .models import (
    ActionTemplate,
    Workflow,
    WorkflowStep,
    WorkflowExecution,
    StepExecution,
    SavedWorkflowNode,
)


@admin.register(ActionTemplate)
class ActionTemplateAdmin(admin.ModelAdmin):
    list_display = ['name', 'action_type', 'category', 'is_active', 'created_at']
    list_filter = ['category', 'is_active']
    search_fields = ['name', 'action_type', 'description']
    readonly_fields = ['id', 'created_at', 'updated_at']


class WorkflowStepInline(admin.TabularInline):
    model = WorkflowStep
    extra = 0
    fields = ['order', 'name', 'action_type', 'is_active']
    readonly_fields = ['id']


@admin.register(Workflow)
class WorkflowAdmin(admin.ModelAdmin):
    list_display = ['name', 'trigger_type', 'is_active', 'is_draft', 'version', 'created_by', 'created_at']
    list_filter = ['trigger_type', 'is_active', 'is_draft']
    search_fields = ['name', 'description']
    readonly_fields = ['id', 'created_at', 'updated_at']
    inlines = [WorkflowStepInline]


@admin.register(WorkflowStep)
class WorkflowStepAdmin(admin.ModelAdmin):
    list_display = ['name', 'workflow', 'order', 'action_type', 'is_active']
    list_filter = ['workflow', 'is_active', 'on_failure']
    search_fields = ['name', 'action_type']
    readonly_fields = ['id', 'created_at', 'updated_at']


class StepExecutionInline(admin.TabularInline):
    model = StepExecution
    extra = 0
    fields = ['step', 'status', 'started_at', 'completed_at']
    readonly_fields = ['id', 'step', 'status', 'started_at', 'completed_at']


@admin.register(WorkflowExecution)
class WorkflowExecutionAdmin(admin.ModelAdmin):
    list_display = ['id', 'workflow', 'status', 'progress_percent', 'executed_by', 'started_at', 'completed_at']
    list_filter = ['status', 'workflow']
    search_fields = ['workflow__name', 'trigger_source']
    readonly_fields = ['id', 'created_at', 'updated_at']
    inlines = [StepExecutionInline]


@admin.register(StepExecution)
class StepExecutionAdmin(admin.ModelAdmin):
    list_display = ['id', 'step', 'status', 'attempt_number', 'started_at', 'completed_at']
    list_filter = ['status']


@admin.register(SavedWorkflowNode)
class SavedWorkflowNodeAdmin(admin.ModelAdmin):
    list_display = ['name', 'node_type', 'node_category', 'action_type', 'created_by', 'updated_at']
    list_filter = ['node_type', 'node_category', 'is_active']
    search_fields = ['name', 'action_type']
    readonly_fields = ['id', 'created_at', 'updated_at']



