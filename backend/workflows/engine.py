"""
Workflow Execution Engine

This module handles the orchestration and execution of workflows.
It contains two main concerns:

1. ``WorkflowEngine`` – pure execution logic that processes steps in order,
   evaluates conditions, and persists ``StepExecution`` records.  This class
   is called directly by the Django 6.0 background task defined in
   ``workflows.tasks``.

2. ``execute_workflow`` – a convenience service function that creates a
   ``WorkflowExecution`` record and enqueues ``run_workflow_task`` via
   Django 6.0's ``django.tasks`` backend.  Callers (views, signals) should
   use this function rather than calling ``WorkflowEngine`` directly.
"""
import logging
import traceback
import re
from typing import Optional, Dict, Any
from django.utils import timezone

from .models import Workflow, WorkflowStep, WorkflowExecution, StepExecution
from .actions import ActionRegistry, ActionResult

logger = logging.getLogger(__name__)


class WorkflowEngine:
    """
    Engine for executing workflows.

    Handles the orchestration of workflow steps, context management,
    error handling, and result aggregation.
    """

    def __init__(self, execution: WorkflowExecution):
        self.execution = execution
        self.workflow = execution.workflow
        self.context = {
            'trigger_data': execution.trigger_data,
            'trigger_source': execution.trigger_source,
            'variables': {},
            'step_results': {},
            'execution_id': str(execution.id),
            'workflow_id': str(self.workflow.id),
            'workflow_name': self.workflow.name,
        }

    def run(self) -> Dict[str, Any]:
        """
        Execute the workflow.

        Returns:
            Dict containing execution results
        """
        logger.info(f"Starting workflow execution: {self.execution.id}")

        # Update execution status
        self.execution.status = 'running'
        self.execution.started_at = timezone.now()
        steps = list(self.workflow.steps.filter(is_active=True).order_by('order'))
        self.execution.total_steps = len(steps)
        self.execution.save()

        try:
            for index, step in enumerate(steps):
                self.execution.current_step = index
                self.execution.save()

                if step.node_type in ['start', 'end']:
                    logger.info(f"Skipping {step.node_type} node: {step.name}")
                    self._create_step_execution(step, 'skipped')
                    continue

                if step.node_type == 'condition':
                    result = self._execute_condition_step(step)
                    if not result.success:
                        if step.on_failure == 'stop':
                            raise Exception(f"Condition step '{step.name}' failed: {result.error}")

                    self.context['step_results'][step.name] = result.data
                    if result.data:
                        self.context['variables'].update(result.data)

                    self.execution.completed_steps = index + 1
                    self.execution.update_progress()
                    self.execution.context = self.context
                    self.execution.save()
                    continue

                # Check if step should be executed based on condition
                if not self._evaluate_condition(step):
                    logger.info(f"Skipping step {step.name} - condition not met")
                    self._create_step_execution(step, 'skipped')
                    continue

                # Execute the step
                result = self._execute_step(step)

                # Handle step result
                if not result.success:
                    if step.on_failure == 'stop':
                        raise Exception(f"Step '{step.name}' failed: {result.error}")
                    elif step.on_failure == 'retry':
                        # Retry the step
                        for attempt in range(step.retry_count):
                            logger.info(f"Retrying step {step.name}, attempt {attempt + 2}")
                            result = self._execute_step(step, attempt + 2)
                            if result.success:
                                break

                        if not result.success and step.on_failure != 'continue':
                            raise Exception(f"Step '{step.name}' failed after retries: {result.error}")

                # Store step result in context
                self.context['step_results'][step.name] = result.data
                if result.data:
                    self.context['variables'].update(result.data)

                # Update progress
                self.execution.completed_steps = index + 1
                self.execution.update_progress()
                self.execution.context = self.context
                self.execution.save()

            # Workflow completed successfully
            self.execution.status = 'completed'
            self.execution.completed_at = timezone.now()
            self.execution.result_data = self.context
            self.execution.save()

            logger.info(f"Workflow execution completed: {self.execution.id}")

        except Exception as e:
            logger.exception(f"Workflow execution failed: {self.execution.id}")
            self.execution.status = 'failed'
            self.execution.completed_at = timezone.now()
            self.execution.error_message = str(e)
            self.execution.result_data = self.context
            self.execution.save()
            raise

        return self.context

    def _execute_step(self, step: WorkflowStep, attempt: int = 1) -> ActionResult:
        """
        Execute a single workflow step.

        Args:
            step: The workflow step to execute
            attempt: Current attempt number (for retries)

        Returns:
            ActionResult from the action execution
        """
        logger.info(f"Executing step: {step.name} (attempt {attempt})")

        # Create step execution record
        step_exec = self._create_step_execution(step, 'running', attempt)
        step_exec.input_data = self.context.copy()
        step_exec.save()

        try:
            # Get the action handler
            action = ActionRegistry.get_action(step.action_type)

            # Merge default config with step config
            config = {}
            if step.action_template and step.action_template.default_config:
                config.update(step.action_template.default_config)
            config.update(step.action_config)

            # Execute the action
            result = action.execute(config, self.context)

            # Update step execution
            step_exec.status = 'completed' if result.success else 'failed'
            step_exec.completed_at = timezone.now()
            step_exec.output_data = result.data
            step_exec.logs = result.logs
            if not result.success:
                step_exec.error_message = result.error
            step_exec.save()

            return result

        except Exception as e:
            logger.exception(f"Step execution error: {step.name}")
            step_exec.status = 'failed'
            step_exec.completed_at = timezone.now()
            step_exec.error_message = str(e)
            step_exec.error_traceback = traceback.format_exc()
            step_exec.save()

            return ActionResult(success=False, error=str(e))

    def _create_step_execution(
        self,
        step: WorkflowStep,
        status: str,
        attempt: int = 1
    ) -> StepExecution:
        """Create a step execution record."""
        return StepExecution.objects.create(
            workflow_execution=self.execution,
            step=step,
            status=status,
            attempt_number=attempt,
            started_at=timezone.now() if status == 'running' else None,
        )

    @staticmethod
    def _normalize_condition_field(field: str) -> str:
        field = (field or '').strip()
        if field.startswith('{{') and field.endswith('}}'):
            field = field[2:-2].strip()
        if field.startswith('trigger.data.'):
            field = 'trigger_data.' + field[len('trigger.data.'):]
        if field.startswith('trigger.data'):
            field = field.replace('trigger.data', 'trigger_data', 1)
        return field

    @staticmethod
    def _coerce_number(value: Any) -> Optional[float]:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _evaluate_condition_rule(self, rule: Dict[str, Any], resolver) -> bool:
        field = self._normalize_condition_field(rule.get('field', ''))
        operator = rule.get('operator') or 'equals'
        compare_to = rule.get('value')
        if compare_to is None:
            compare_to = rule.get('compare_to')

        if not field:
            return True

        value = resolver(field)

        if operator in ('equals', '=='):
            return value == compare_to
        if operator in ('not_equals', '!='):
            return value != compare_to
        if operator in ('contains',):
            return compare_to in str(value) if value is not None else False
        if operator in ('not_contains',):
            return compare_to not in str(value) if value is not None else True
        if operator in ('starts_with',):
            return str(value).startswith(str(compare_to)) if value is not None else False
        if operator in ('ends_with',):
            return str(value).endswith(str(compare_to)) if value is not None else False
        if operator in ('greater_than', '>'):
            left = self._coerce_number(value)
            right = self._coerce_number(compare_to)
            return left is not None and right is not None and left > right
        if operator in ('less_than', '<'):
            left = self._coerce_number(value)
            right = self._coerce_number(compare_to)
            return left is not None and right is not None and left < right
        if operator in ('greater_equal', '>='):
            left = self._coerce_number(value)
            right = self._coerce_number(compare_to)
            return left is not None and right is not None and left >= right
        if operator in ('less_equal', '<='):
            left = self._coerce_number(value)
            right = self._coerce_number(compare_to)
            return left is not None and right is not None and left <= right
        if operator == 'in_list':
            if compare_to is None:
                return False
            options = [item.strip() for item in str(compare_to).split(',') if item.strip()]
            return str(value) in options if value is not None else False
        if operator == 'not_in_list':
            if compare_to is None:
                return True
            options = [item.strip() for item in str(compare_to).split(',') if item.strip()]
            return str(value) not in options if value is not None else True
        if operator in ('is_empty',):
            return value is None or value == ''
        if operator in ('is_not_empty', 'not_empty'):
            return value is not None and value != ''
        if operator == 'matches_regex':
            if compare_to is None:
                return False
            try:
                return re.search(str(compare_to), str(value or '')) is not None
            except re.error:
                return False

        return True

    def _evaluate_condition_object(self, condition: Dict[str, Any], resolver) -> bool:
        if not condition or not isinstance(condition, dict):
            return True

        if condition.get('groups'):
            groups = condition.get('groups') or []
            logic = (condition.get('logic') or 'AND').upper()
            results = []
            for group in groups:
                rules = group.get('rules') or []
                group_logic = (group.get('logic') or 'AND').upper()
                rule_results = [self._evaluate_condition_rule(rule, resolver) for rule in rules]
                group_result = all(rule_results) if group_logic == 'AND' else any(rule_results)
                results.append(group_result)
            return all(results) if logic == 'AND' else any(results)

        if condition.get('field'):
            return self._evaluate_condition_rule(condition, resolver)

        if condition.get('rules'):
            logic = (condition.get('logic') or 'AND').upper()
            rule_results = [self._evaluate_condition_rule(rule, resolver) for rule in condition.get('rules', [])]
            return all(rule_results) if logic == 'AND' else any(rule_results)

        return True

    def _extract_condition_fields(self, condition: Dict[str, Any]) -> list[str]:
        fields = []
        if not isinstance(condition, dict):
            return fields

        if condition.get('field'):
            fields.append(self._normalize_condition_field(condition.get('field', '')))

        for rule in condition.get('rules', []) or []:
            if isinstance(rule, dict) and rule.get('field'):
                fields.append(self._normalize_condition_field(rule.get('field', '')))

        for group in condition.get('groups', []) or []:
            if not isinstance(group, dict):
                continue
            for rule in group.get('rules', []) or []:
                if isinstance(rule, dict) and rule.get('field'):
                    fields.append(self._normalize_condition_field(rule.get('field', '')))

        return [f for f in fields if f]

    def _execute_condition_step(self, step: WorkflowStep) -> ActionResult:
        condition = step.condition or {}

        step_exec = self._create_step_execution(step, 'running')
        step_exec.input_data = self.context.copy()
        step_exec.save()

        try:
            fields = self._extract_condition_fields(condition)
            uses_ticket_scope = any(f.startswith('ticket.') for f in fields)
            uses_alert_scope = any(f.startswith('alert.') for f in fields)

            result_data: Dict[str, Any] = {}

            if uses_ticket_scope:
                from tickets.models import EventTicket

                records = list(
                    EventTicket.objects.filter(is_deleted=False).values(
                        'ticket_number',
                        'status',
                        'priority',
                        'title',
                        'current_assign_group',
                        'current_assign_owner',
                        'event_category',
                        'event_result',
                    )
                )

                def resolver(path: str, record=None):
                    target = record or {}
                    normalized = self._normalize_condition_field(path)
                    if normalized.startswith('ticket.'):
                        key = normalized.split('.', 1)[1]
                        mapping = {
                            'ticket_number': 'ticket_number',
                            'status': 'status',
                            'priority': 'priority',
                            'title': 'title',
                            'assign_group': 'current_assign_group',
                            'assign_owner': 'current_assign_owner',
                            'event_category': 'event_category',
                            'event_result': 'event_result',
                        }
                        return target.get(mapping.get(key, key))
                    return None

                matched_ticket_numbers = []
                for rec in records:
                    if self._evaluate_condition_object(condition, lambda path: resolver(path, rec)):
                        matched_ticket_numbers.append(rec['ticket_number'])

                result_data.update(
                    {
                        'target_ticket_numbers': matched_ticket_numbers,
                        'matched_ticket_count': len(matched_ticket_numbers),
                        'condition_matched': len(matched_ticket_numbers) > 0,
                    }
                )

            elif uses_alert_scope:
                from es_integration.models import Alert

                records = list(
                    Alert.objects.all().values(
                        'alert_id',
                        'severity',
                        'title',
                        'category',
                        'source_index',
                        'message',
                        'rule_id',
                    )
                )

                def resolver(path: str, record=None):
                    target = record or {}
                    normalized = self._normalize_condition_field(path)
                    if normalized.startswith('alert.'):
                        key = normalized.split('.', 1)[1]
                        return target.get(key)
                    return None

                matched_alert_ids = []
                for rec in records:
                    if self._evaluate_condition_object(condition, lambda path: resolver(path, rec)):
                        matched_alert_ids.append(rec['alert_id'])

                result_data.update(
                    {
                        'target_alert_ids': matched_alert_ids,
                        'matched_alert_count': len(matched_alert_ids),
                        'condition_matched': len(matched_alert_ids) > 0,
                    }
                )

            else:
                def resolver(path: str):
                    value = self.context
                    for key in self._normalize_condition_field(path).split('.'):
                        if isinstance(value, dict):
                            value = value.get(key)
                        else:
                            value = None
                            break
                    return value

                matched = self._evaluate_condition_object(condition, resolver)
                result_data.update({'condition_matched': matched})

            step_exec.status = 'completed'
            step_exec.completed_at = timezone.now()
            step_exec.output_data = result_data
            step_exec.logs = f"Condition evaluated successfully ({step.name})"
            step_exec.save()

            return ActionResult(success=True, data=result_data, logs=step_exec.logs)

        except Exception as e:
            logger.exception(f"Condition step execution error: {step.name}")
            step_exec.status = 'failed'
            step_exec.completed_at = timezone.now()
            step_exec.error_message = str(e)
            step_exec.error_traceback = traceback.format_exc()
            step_exec.save()
            return ActionResult(success=False, error=str(e))

    def _evaluate_condition(self, step: WorkflowStep) -> bool:
        """
        Evaluate step condition to determine if it should execute.

        Args:
            step: The workflow step with optional condition

        Returns:
            True if step should execute, False otherwise
        """
        if not step.condition:
            return True

        try:
            condition = step.condition

            if not condition or not isinstance(condition, dict):
                return True

            def resolver(path: str):
                value = self.context
                for key in self._normalize_condition_field(path).split('.'):
                    if isinstance(value, dict):
                        value = value.get(key)
                    else:
                        value = None
                        break
                return value

            return self._evaluate_condition_object(condition, resolver)

        except Exception as e:
            logger.warning(f"Condition evaluation error: {e}")
            return True


def execute_workflow(
    workflow: Workflow,
    trigger_data: Optional[Dict] = None,
    trigger_source: str = 'manual',
    executed_by=None
) -> WorkflowExecution:
    """
    Create a ``WorkflowExecution`` record and enqueue it as a Django 6.0
    background task via ``run_workflow_task.enqueue()``.

    With the default ``ImmediateBackend`` the task runs synchronously in the
    same thread before this function returns.  Switching to an async backend
    in settings will make it non-blocking without any code changes here.

    Args:
        workflow: The ``Workflow`` instance to run.
        trigger_data: Optional context dict forwarded to the engine.
        trigger_source: Human-readable description of what caused execution
            (e.g. ``"manual"``, ``"ticket:SEC-001"``).
        executed_by: Optional Django ``User`` instance.

    Returns:
        The ``WorkflowExecution`` record (already persisted, status reflects
        the outcome when using ``ImmediateBackend``).
    """
    from .tasks import run_workflow_task

    # Create the execution record in PENDING state before enqueueing.
    execution = WorkflowExecution.objects.create(
        workflow=workflow,
        trigger_source=trigger_source,
        trigger_data=trigger_data or {},
        status='pending',
        executed_by=executed_by,
    )

    # Enqueue the background task.  With ImmediateBackend this blocks until
    # completion and the execution status is already updated by the engine.
    try:
        task_result = run_workflow_task.enqueue(str(execution.id))
        # Refresh all fields so the returned object reflects the final state
        # written by WorkflowEngine (status, completed_steps, result_data, etc.).
        execution.refresh_from_db()
        execution.task_result_id = task_result.id
        execution.save(update_fields=['task_result_id'])
    except Exception as exc:
        logger.exception("Failed to enqueue workflow execution %s: %s", execution.id, exc)
        # Reload to pick up any partial updates written by the engine.
        execution.refresh_from_db()
        if execution.status == 'pending':
            execution.status = 'failed'
            execution.error_message = str(exc)
            execution.save(update_fields=['status', 'error_message'])

    return execution


