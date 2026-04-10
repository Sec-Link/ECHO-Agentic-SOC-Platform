"""
Workflow API Views

REST API endpoints for managing workflows, executions, and actions.
"""
import re

from django.db.models import Count, Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from .actions import ActionRegistry
from .engine import execute_workflow
from .models import (
    ActionTemplate,
    SavedWorkflowNode,
    Workflow,
    WorkflowExecution,
    WorkflowStep,
)
from .serializers import (
    ActionTemplateSerializer,
    SavedWorkflowNodeSerializer,
    WorkflowCreateSerializer,
    WorkflowDetailSerializer,
    WorkflowExecuteSerializer,
    WorkflowExecutionDetailSerializer,
    WorkflowExecutionListSerializer,
    WorkflowListSerializer,
    WorkflowStepCreateSerializer,
    WorkflowStepSerializer,
)


class ActionTemplateViewSet(viewsets.ModelViewSet):
    queryset = ActionTemplate.objects.all()
    serializer_class = ActionTemplateSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        queryset = ActionTemplate.objects.all()

        category = self.request.query_params.get('category')
        if category:
            queryset = queryset.filter(category=category)

        is_active = self.request.query_params.get('is_active')
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == 'true')

        return queryset

    @action(detail=False, methods=['get'])
    def available_actions(self, request):
        return Response(ActionRegistry.get_action_info())


class WorkflowViewSet(viewsets.ModelViewSet):
    queryset = Workflow.objects.all()
    permission_classes = [permissions.IsAuthenticated]

    def get_serializer_class(self):
        if self.action == 'list':
            return WorkflowListSerializer
        if self.action in ['create', 'update', 'partial_update']:
            return WorkflowCreateSerializer
        return WorkflowDetailSerializer

    def get_queryset(self):
        queryset = Workflow.objects.all()

        trigger_type = self.request.query_params.get('trigger_type')
        if trigger_type:
            queryset = queryset.filter(trigger_type=trigger_type)

        is_active = self.request.query_params.get('is_active')
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == 'true')

        is_draft = self.request.query_params.get('is_draft')
        if is_draft is not None:
            queryset = queryset.filter(is_draft=is_draft.lower() == 'true')

        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(Q(name__icontains=search) | Q(description__icontains=search))

        return queryset

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @staticmethod
    def _resolve_trigger_template(value, trigger_data):
        if not isinstance(value, str):
            return value

        trigger_data = trigger_data or {}

        def replace_var(match):
            var_path = match.group(1)
            result = trigger_data
            for key in var_path.split('.'):
                if isinstance(result, dict):
                    result = result.get(key, '')
                else:
                    result = ''
                    break
            return str(result) if result is not None else ''

        return re.sub(r'\{\{(\w+(?:\.\w+)*)\}\}', replace_var, value)

    @staticmethod
    def _normalize_condition_field(field: str) -> str:
        field = (field or '').strip()
        if field.startswith('{{') and field.endswith('}}'):
            field = field[2:-2].strip()
        return field

    @classmethod
    def _uses_ticket_fields(cls, condition: dict) -> bool:
        if not isinstance(condition, dict):
            return False

        fields = []
        if condition.get('field'):
            fields.append(condition.get('field'))
        for rule in condition.get('rules', []) or []:
            if isinstance(rule, dict) and rule.get('field'):
                fields.append(rule.get('field'))
        for group in condition.get('groups', []) or []:
            if not isinstance(group, dict):
                continue
            for rule in group.get('rules', []) or []:
                if isinstance(rule, dict) and rule.get('field'):
                    fields.append(rule.get('field'))

        for raw in fields:
            normalized = cls._normalize_condition_field(str(raw or ''))
            if normalized.startswith('ticket.'):
                return True
        return False

    @classmethod
    def _evaluate_condition_rule_on_ticket(cls, rule: dict, ticket_record: dict) -> bool:
        if not isinstance(rule, dict):
            return True
        field = cls._normalize_condition_field(rule.get('field', ''))
        operator = rule.get('operator') or 'equals'
        compare_to = rule.get('value')
        if compare_to is None:
            compare_to = rule.get('compare_to')

        if not field:
            return True
        if not field.startswith('ticket.'):
            # Non-ticket field cannot be evaluated in pre-run estimate.
            return True

        key = field.split('.', 1)[1]
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
        actual = ticket_record.get(mapping.get(key, key))

        if operator in ('equals', '=='):
            return actual == compare_to
        if operator in ('not_equals', '!='):
            return actual != compare_to
        if operator == 'contains':
            return str(compare_to) in str(actual or '')
        if operator == 'not_contains':
            return str(compare_to) not in str(actual or '')
        if operator == 'starts_with':
            return str(actual or '').startswith(str(compare_to))
        if operator == 'ends_with':
            return str(actual or '').endswith(str(compare_to))
        if operator == 'in_list':
            opts = [x.strip() for x in str(compare_to or '').split(',') if x.strip()]
            return str(actual) in opts
        if operator == 'not_in_list':
            opts = [x.strip() for x in str(compare_to or '').split(',') if x.strip()]
            return str(actual) not in opts
        if operator == 'is_empty':
            return actual in (None, '')
        if operator in ('is_not_empty', 'not_empty'):
            return actual not in (None, '')
        return True

    @classmethod
    def _estimate_ticket_condition_impact(cls, condition: dict) -> int:
        from tickets.models import EventTicket

        if not isinstance(condition, dict) or not cls._uses_ticket_fields(condition):
            return 0

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

        def eval_condition(obj: dict, rec: dict) -> bool:
            if obj.get('groups'):
                logic = (obj.get('logic') or 'AND').upper()
                group_results = []
                for group in obj.get('groups', []) or []:
                    rules = group.get('rules', []) if isinstance(group, dict) else []
                    group_logic = (group.get('logic') or 'AND').upper() if isinstance(group, dict) else 'AND'
                    rule_results = [cls._evaluate_condition_rule_on_ticket(rule, rec) for rule in rules]
                    group_results.append(all(rule_results) if group_logic == 'AND' else any(rule_results))
                return all(group_results) if logic == 'AND' else any(group_results)

            if obj.get('rules'):
                logic = (obj.get('logic') or 'AND').upper()
                rule_results = [cls._evaluate_condition_rule_on_ticket(rule, rec) for rule in obj.get('rules', [])]
                return all(rule_results) if logic == 'AND' else any(rule_results)

            if obj.get('field'):
                return cls._evaluate_condition_rule_on_ticket(obj, rec)

            return True

        return sum(1 for rec in records if eval_condition(condition, rec))

    @classmethod
    def _has_update_ticket_local_scope(cls, action_config: dict, trigger_data: dict) -> bool:
        if not isinstance(action_config, dict):
            return False

        ticket_number = str(cls._resolve_trigger_template(action_config.get('ticket_number', ''), trigger_data) or '').strip()
        title = str(cls._resolve_trigger_template(action_config.get('title', ''), trigger_data) or '').strip()
        if ticket_number or title:
            return True

        filters = action_config.get('filters') or {}
        if not isinstance(filters, dict):
            filters = {}

        merged_filters = {
            'priority': action_config.get('match_priority', filters.get('priority', '')),
            'status': action_config.get('match_status', filters.get('status', '')),
            'assign_group': action_config.get('match_assign_group', filters.get('assign_group', '')),
            'assign_owner': action_config.get('match_assign_owner', filters.get('assign_owner', '')),
            'created_time_from': filters.get('created_time_from'),
            'created_time_to': filters.get('created_time_to'),
            'updated_time_from': filters.get('updated_time_from'),
            'updated_time_to': filters.get('updated_time_to'),
        }
        return any(str(v or '').strip() for v in merged_filters.values())

    @classmethod
    def _estimate_update_ticket_impact(cls, action_config, trigger_data):
        from tickets.models import EventTicket

        if not isinstance(action_config, dict):
            action_config = {}

        filters = action_config.get('filters') or {}
        if not isinstance(filters, dict):
            filters = {}

        merged_filters = {
            'priority': action_config.get('match_priority', filters.get('priority', '')),
            'status': action_config.get('match_status', filters.get('status', '')),
            'assign_group': action_config.get('match_assign_group', filters.get('assign_group', '')),
            'assign_owner': action_config.get('match_assign_owner', filters.get('assign_owner', '')),
            'created_time_from': filters.get('created_time_from'),
            'created_time_to': filters.get('created_time_to'),
            'updated_time_from': filters.get('updated_time_from'),
            'updated_time_to': filters.get('updated_time_to'),
        }

        ticket_number = str(cls._resolve_trigger_template(action_config.get('ticket_number', ''), trigger_data) or '').strip()
        title = str(cls._resolve_trigger_template(action_config.get('title', ''), trigger_data) or '').strip()

        if ticket_number:
            return EventTicket.objects.filter(ticket_number=ticket_number).count()
        if title:
            return EventTicket.objects.filter(title=title).count()

        query = EventTicket.objects.all()

        priority = str(cls._resolve_trigger_template(merged_filters.get('priority', ''), trigger_data) or '').strip()
        status_filter = str(cls._resolve_trigger_template(merged_filters.get('status', ''), trigger_data) or '').strip()
        assign_group = str(cls._resolve_trigger_template(merged_filters.get('assign_group', ''), trigger_data) or '').strip()
        assign_owner = str(cls._resolve_trigger_template(merged_filters.get('assign_owner', ''), trigger_data) or '').strip()

        if priority:
            query = query.filter(priority__iexact=priority)
        if status_filter:
            query = query.filter(status__iexact=status_filter)
        if assign_group:
            query = query.filter(current_assign_group__iexact=assign_group)
        if assign_owner:
            query = query.filter(current_assign_owner__iexact=assign_owner)

        def _parse_dt(value):
            if not value:
                return None
            parsed = parse_datetime(str(value))
            if parsed and timezone.is_naive(parsed):
                return timezone.make_aware(parsed)
            return parsed

        created_from = _parse_dt(merged_filters.get('created_time_from'))
        created_to = _parse_dt(merged_filters.get('created_time_to'))
        updated_from = _parse_dt(merged_filters.get('updated_time_from'))
        updated_to = _parse_dt(merged_filters.get('updated_time_to'))

        if created_from:
            query = query.filter(created_time__gte=created_from)
        if created_to:
            query = query.filter(created_time__lte=created_to)
        if updated_from:
            query = query.filter(updated_time__gte=updated_from)
        if updated_to:
            query = query.filter(updated_time__lte=updated_to)

        return query.count()

    @action(detail=True, methods=['post'])
    def execute(self, request, pk=None):
        workflow = self.get_object()

        if not workflow.is_active:
            return Response({'error': 'Cannot execute inactive workflow'}, status=status.HTTP_400_BAD_REQUEST)

        serializer = WorkflowExecuteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        trigger_data = serializer.validated_data.get('trigger_data', {})
        confirm_mass_update = serializer.validated_data.get('confirm_mass_update', False)

        risky_nodes = []
        total_estimated_impact = 0

        for step in workflow.steps.filter(is_active=True, action_type='update_ticket'):
            action_config = step.action_config if isinstance(step.action_config, dict) else {}
            title = str(self._resolve_trigger_template(action_config.get('title', ''), trigger_data) or '').strip()
            if title:
                continue

            estimated_count = self._estimate_update_ticket_impact(action_config, trigger_data)

            # If Update Ticket has no local selector/filter, estimate from nearest
            # upstream ticket condition so status=resolved vs triaged diverges.
            if not self._has_update_ticket_local_scope(action_config, trigger_data):
                upstream_condition = (
                    workflow.steps.filter(
                        is_active=True,
                        node_type='condition',
                        order__lt=step.order,
                    )
                    .order_by('-order')
                    .first()
                )
                if upstream_condition and self._uses_ticket_fields(upstream_condition.condition or {}):
                    condition_estimate = self._estimate_ticket_condition_impact(upstream_condition.condition or {})
                    if condition_estimate > 0:
                        estimated_count = condition_estimate

            total_estimated_impact += estimated_count
            risky_nodes.append(
                {
                    'step_id': str(step.id),
                    'step_name': step.name,
                    'estimated_impact_count': estimated_count,
                }
            )

        if risky_nodes and not confirm_mass_update:
            return Response(
                {
                    'error': 'Update Ticket step has empty Ticket Title. Confirm before execution.',
                    'requires_confirmation': True,
                    'estimated_impact_count': total_estimated_impact,
                    'affected_nodes': risky_nodes,
                },
                status=status.HTTP_409_CONFLICT,
            )

        execution = execute_workflow(
            workflow=workflow,
            trigger_data=trigger_data,
            trigger_source=serializer.validated_data.get('trigger_source', 'manual'),
            executed_by=request.user,
        )

        return Response(WorkflowExecutionDetailSerializer(execution).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def clone(self, request, pk=None):
        workflow = self.get_object()
        new_workflow = workflow.clone(new_name=request.data.get('name'), user=request.user)
        return Response(WorkflowDetailSerializer(new_workflow).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def activate(self, request, pk=None):
        workflow = self.get_object()
        workflow.is_active = True
        workflow.is_draft = False
        workflow.save(update_fields=['is_active', 'is_draft'])
        return Response({'status': 'activated'})

    @action(detail=True, methods=['post'])
    def deactivate(self, request, pk=None):
        workflow = self.get_object()
        workflow.is_active = False
        workflow.save(update_fields=['is_active'])
        return Response({'status': 'deactivated'})

    @action(detail=True, methods=['get'])
    def executions(self, request, pk=None):
        workflow = self.get_object()
        executions = workflow.executions.all()

        page = self.paginate_queryset(executions)
        if page is not None:
            serializer = WorkflowExecutionListSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = WorkflowExecutionListSerializer(executions, many=True)
        return Response(serializer.data)


class WorkflowStepViewSet(viewsets.ModelViewSet):
    serializer_class = WorkflowStepSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        queryset = WorkflowStep.objects.all()
        workflow_id = self.request.query_params.get('workflow')
        if workflow_id:
            queryset = queryset.filter(workflow_id=workflow_id)

        node_category = self.request.query_params.get('node_category')
        if node_category:
            queryset = queryset.filter(node_category=node_category)

        return queryset.order_by('workflow', 'order')

    def get_serializer_class(self):
        if self.action in ['create', 'update', 'partial_update']:
            return WorkflowStepCreateSerializer
        return WorkflowStepSerializer

    @action(detail=False, methods=['post'])
    def reorder(self, request):
        step_orders = request.data.get('step_orders', [])

        for item in step_orders:
            step_id = item.get('id')
            order = item.get('order')
            if step_id is not None and order is not None:
                WorkflowStep.objects.filter(id=step_id).update(order=order)

        return Response({'status': 'reordered'})


class WorkflowExecutionViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = WorkflowExecution.objects.all()
    permission_classes = [permissions.IsAuthenticated]

    def get_serializer_class(self):
        if self.action == 'list':
            return WorkflowExecutionListSerializer
        return WorkflowExecutionDetailSerializer

    def get_queryset(self):
        queryset = WorkflowExecution.objects.all()

        workflow_id = self.request.query_params.get('workflow')
        if workflow_id:
            queryset = queryset.filter(workflow_id=workflow_id)

        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        start_date = self.request.query_params.get('start_date')
        if start_date:
            queryset = queryset.filter(created_at__gte=start_date)

        end_date = self.request.query_params.get('end_date')
        if end_date:
            queryset = queryset.filter(created_at__lte=end_date)

        return queryset

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        execution = self.get_object()

        if execution.status not in ['pending', 'running', 'paused']:
            return Response({'error': 'Cannot cancel execution in current state'}, status=status.HTTP_400_BAD_REQUEST)

        execution.status = 'cancelled'
        execution.completed_at = timezone.now()
        execution.save(update_fields=['status', 'completed_at'])

        return Response(
            {
                'status': 'cancelled',
                'execution_id': str(execution.id),
                'task_result_id': execution.task_result_id or None,
            }
        )

    @action(detail=True, methods=['get'])
    def steps(self, request, pk=None):
        execution = self.get_object()
        from .serializers import StepExecutionSerializer
        steps = execution.step_executions.all()
        serializer = StepExecutionSerializer(steps, many=True)
        return Response(serializer.data)


class SavedWorkflowNodeViewSet(viewsets.ModelViewSet):
    serializer_class = SavedWorkflowNodeSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        queryset = SavedWorkflowNode.objects.filter(created_by=self.request.user)

        node_category = self.request.query_params.get('node_category')
        if node_category:
            queryset = queryset.filter(node_category=node_category)

        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(Q(name__icontains=search) | Q(action_type__icontains=search))

        return queryset

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)



class WorkflowStatsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        total_workflows = Workflow.objects.count()
        active_workflows = Workflow.objects.filter(is_active=True).count()

        total_executions = WorkflowExecution.objects.count()
        completed_executions = WorkflowExecution.objects.filter(status='completed').count()
        failed_executions = WorkflowExecution.objects.filter(status='failed').count()
        running_executions = WorkflowExecution.objects.filter(status='running').count()

        recent_executions = WorkflowExecution.objects.order_by('-created_at')[:10]
        status_counts = WorkflowExecution.objects.values('status').annotate(count=Count('id'))

        return Response(
            {
                'workflows': {
                    'total': total_workflows,
                    'active': active_workflows,
                    'inactive': total_workflows - active_workflows,
                },
                'executions': {
                    'total': total_executions,
                    'completed': completed_executions,
                    'failed': failed_executions,
                    'running': running_executions,
                    'success_rate': (completed_executions / total_executions * 100) if total_executions > 0 else 0,
                },
                'status_breakdown': {item['status']: item['count'] for item in status_counts},
                'recent_executions': WorkflowExecutionListSerializer(recent_executions, many=True).data,
            }
        )
