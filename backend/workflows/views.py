"""
Workflow API Views

REST API endpoints for managing workflows, executions, and actions.
"""
import os
import re
import logging
from typing import Any, Dict

from django.db.models import Count, Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.text import slugify

from rest_framework import permissions, status, viewsets
from rest_framework.authentication import TokenAuthentication
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from .actions import ActionRegistry
from .engine import WorkflowExecutionUnavailable, execute_workflow
from .models import (
    ActionTemplate,
    SavedWorkflowNode,
    Workflow,
    WorkflowExecution,
    WorkflowStep,
    WorkflowSchedule,
    TicketWorkflowBinding,
)
from .serializers import (
    ActionTemplateSerializer,
    SavedWorkflowNodeSerializer,
    WorkflowCreateSerializer,
    WorkflowDetailSerializer,
    WorkflowExecuteSerializer,
    WorkflowExecutionDetailSerializer,
    WorkflowExecutionListSerializer,
    RuntimeRegistrationSerializer,
    RuntimeSnapshotSerializer,
    WorkflowListSerializer,
    WorkflowStepCreateSerializer,
    WorkflowStepSerializer,
    WorkflowScheduleSerializer,
    TicketWorkflowBindingSerializer,
)
from . import prefect_client
from .parameter_binder import bind_workflow_parameters
from .ticket_invocation import dispatch_ticket_event, find_callable_workflows, get_ticket_workplan, invoke_workflow_from_ticket


logger = logging.getLogger(__name__)


class StaffTokenAuthentication(TokenAuthentication):
    """Keep internal endpoint authentication failures consistently at 403."""

    def authenticate_header(self, request):
        return None


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

    @action(detail=False, methods=['post'], url_path='bootstrap-presets')
    def bootstrap_presets(self, request):
        presets = [
            {
                'action_type': 'block_ip',
                'name': 'Block IP (Isolation)',
                'description': 'Block a suspicious IP on the security device',
                'category': 'containment',
            },
            {
                'action_type': 'disable_user',
                'name': 'Disable User (Isolation)',
                'description': 'Disable a compromised user account',
                'category': 'containment',
            },
            {
                'action_type': 'send_email',
                'name': 'Security Alert Email',
                'description': 'Send a security notification email',
                'category': 'notification',
            },
            {
                'action_type': 'api_call',
                'name': 'API Call',
                'description': 'Call an HTTP API with configurable request data',
                'category': 'notification',
            },
        ]

        info = {item['action_type']: item for item in ActionRegistry.get_action_info()}
        created = 0
        updated = 0
        for preset in presets:
            action_type = preset['action_type']
            meta = info.get(action_type, {})
            defaults = {
                'name': preset['name'],
                'description': preset['description'],
                'category': preset['category'],
                'config_schema': meta.get('config_schema', {}),
                'is_active': True,
            }
            obj, was_created = ActionTemplate.objects.update_or_create(
                action_type=action_type,
                defaults=defaults,
            )
            if was_created:
                created += 1
            else:
                updated += 1

        return Response({'created': created, 'updated': updated})


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

    def _ensure_prefect_deployment(self, workflow: Workflow) -> None:
        if workflow.execution_engine != 'prefect':
            return
        deployment_id = (workflow.prefect_deployment_id or '').strip()
        if deployment_id:
            return
        if prefect_client.is_configured(None):
            workflow.prefect_deployment_id = prefect_client.resolve_deployment_id(None)
            workflow.save(update_fields=['prefect_deployment_id'])

    @action(detail=True, methods=['post'], url_path='execute')
    def execute(self, request, pk=None):
        workflow = self.get_object()
        serializer = WorkflowExecuteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            execution = execute_workflow(
                workflow=workflow,
                trigger_source=serializer.validated_data.get('trigger_source', 'manual'),
                trigger_data=serializer.validated_data.get('trigger_data', {}),
                executed_by=request.user if request.user.is_authenticated else None,
            )
        except WorkflowExecutionUnavailable as exc:
            return Response({'error': str(exc)}, status=status.HTTP_409_CONFLICT)
        response = WorkflowExecutionDetailSerializer(execution)
        return Response(response.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get'], url_path='export')
    def export_published(self, request, pk=None):
        """Download the last published manifest with secrets kept encrypted."""
        workflow = self.get_object()
        from .publisher import build_published_export

        try:
            payload, pointer = build_published_export(workflow)
        except FileNotFoundError:
            return Response(
                {'error': 'Workflow must be published before it can be exported.'},
                status=status.HTTP_409_CONFLICT,
            )
        except (OSError, ValueError, TypeError, KeyError) as exc:
            return Response(
                {'error': f'Published workflow manifest is unavailable or invalid: {exc}'},
                status=status.HTTP_409_CONFLICT,
            )

        version = int(pointer.get('current_version') or 0)
        filename = f'{slugify(workflow.name) or "workflow"}-published-v{version}.json'
        response = Response(payload, content_type='application/json')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response

    def _sync_prefect_schedule(self, schedule: WorkflowSchedule | None, workflow: Workflow) -> None:
        if schedule is None or workflow.execution_engine != 'prefect':
            return
        deployment_id = (workflow.prefect_deployment_id or '').strip() or None
        if not prefect_client.is_configured(deployment_id):
            return
        try:
            from .prefect_dispatcher import sync_schedule
            sync_schedule(schedule)
        except (OSError, ValueError, prefect_client.PrefectAPIError) as exc:
            logger.warning('Prefect schedule sync failed for workflow %s: %s', workflow.id, exc)

    def _sync_default_schedule(self, workflow: Workflow) -> None:
        if workflow.trigger_type != 'scheduled' or not workflow.schedule_cron:
            schedule = WorkflowSchedule.objects.filter(workflow=workflow, name='default').first()
            if schedule:
                schedule.is_active = False
                schedule.save(update_fields=['is_active'])
                self._sync_prefect_schedule(schedule, workflow)
            return

        schedule, _ = WorkflowSchedule.objects.update_or_create(
            workflow=workflow,
            name='default',
            defaults={
                'schedule_type': 'cron',
                'cron': workflow.schedule_cron,
                'interval_seconds': None,
                'timezone': 'UTC',
                'is_active': workflow.is_active,
                'trigger_source': 'schedule',
                'trigger_data': {},
                'created_by': workflow.created_by,
            },
        )
        self._sync_prefect_schedule(schedule, workflow)

    def _sync_prefect_deployment(self, workflow: Workflow) -> None:
        return

    def perform_create(self, serializer):
        workflow = serializer.save(created_by=self.request.user)
        self._ensure_prefect_deployment(workflow)
        self._sync_default_schedule(workflow)
        self._sync_prefect_deployment(workflow)

    def perform_update(self, serializer):
        workflow = serializer.save()
        self._ensure_prefect_deployment(workflow)
        self._sync_default_schedule(workflow)
        self._sync_prefect_deployment(workflow)


class PrefectDeploymentListView(APIView):
    """Expose Prefect deployments so the UI can display them alongside workflows."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if not prefect_client.has_api():
            return Response({'deployments': [], 'error': 'Prefect not configured.'})
        try:
            deployments = prefect_client.list_deployments()
        except prefect_client.PrefectAPIError as exc:
            return Response({'deployments': [], 'error': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response({'deployments': deployments})


class PrefectDeploymentSyncView(APIView):
    """Sync Prefect deployments into Django workflows for bidirectional visibility."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        if not prefect_client.has_api():
            return Response({'synced': 0, 'error': 'Prefect not configured.'}, status=status.HTTP_400_BAD_REQUEST)

        dry_run = str(request.data.get('dry_run', 'false')).lower() == 'true'
        synced = 0
        created = 0
        updated = 0
        try:
            deployments = prefect_client.list_deployments()
        except prefect_client.PrefectAPIError as exc:
            return Response({'synced': 0, 'error': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        for dep in deployments:
            dep_id = dep.get('id') or dep.get('deployment_id')
            if not dep_id:
                continue
            name = dep.get('name') or 'Prefect Deployment'
            description = dep.get('description') or ''

            defaults = {
                'description': description,
                'execution_engine': 'prefect',
                'is_active': False,
                'is_draft': True,
                'tags': list(set((dep.get('tags') or []) + ['prefect'])),
            }

            if dry_run:
                synced += 1
                continue

            obj, was_created = Workflow.objects.update_or_create(
                prefect_deployment_id=str(dep_id),
                defaults={
                    'name': name,
                    **defaults,
                },
            )
            synced += 1
            if was_created:
                created += 1
            else:
                updated += 1

        return Response({'synced': synced, 'created': created, 'updated': updated, 'dry_run': dry_run})


class WorkflowScheduleViewSet(viewsets.ModelViewSet):
    queryset = WorkflowSchedule.objects.select_related('workflow')
    serializer_class = WorkflowScheduleSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        queryset = WorkflowSchedule.objects.select_related('workflow')
        workflow_id = self.request.query_params.get('workflow')
        if workflow_id:
            queryset = queryset.filter(workflow_id=workflow_id)
        is_active = self.request.query_params.get('is_active')
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == 'true')
        return queryset

    def _sync_prefect_schedule(self, schedule: WorkflowSchedule) -> None:
        workflow = schedule.workflow
        if workflow.execution_engine != 'prefect':
            return
        deployment_id = (workflow.prefect_deployment_id or '').strip() or None
        if not prefect_client.is_configured(deployment_id):
            return
        from .prefect_dispatcher import sync_schedule
        sync_schedule(schedule)

    def perform_create(self, serializer):
        schedule = serializer.save(created_by=self.request.user)
        self._sync_prefect_schedule(schedule)

    def perform_update(self, serializer):
        schedule = serializer.save()
        self._sync_prefect_schedule(schedule)

    def perform_destroy(self, instance):
        from .prefect_dispatcher import delete_schedule
        delete_schedule(instance)
        instance.delete()

    @action(detail=True, methods=['post'])
    def pause(self, request, pk=None):
        schedule = self.get_object()
        schedule.is_active = False
        schedule.save(update_fields=['is_active'])
        self._sync_prefect_schedule(schedule)
        return Response({'status': 'paused'})

    @action(detail=True, methods=['post'])
    def resume(self, request, pk=None):
        schedule = self.get_object()
        schedule.is_active = True
        schedule.save(update_fields=['is_active'])
        self._sync_prefect_schedule(schedule)
        return Response({'status': 'resumed'})

    @action(detail=True, methods=['post'], url_path='execute')
    def execute_plan(self, request, pk=None):
        schedule = self.get_object()
        try:
            execution = execute_workflow(
                workflow=schedule.workflow,
                trigger_data=schedule.trigger_data or {},
                trigger_source=schedule.trigger_source or 'schedule',
                executed_by=request.user,
            )
        except WorkflowExecutionUnavailable as exc:
            return Response({'error': str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(WorkflowExecutionDetailSerializer(execution).data, status=status.HTTP_201_CREATED)



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

    @action(
        detail=False,
        methods=['post'],
        url_path='register-runtime',
        authentication_classes=[StaffTokenAuthentication],
        permission_classes=[permissions.IsAdminUser],
    )
    def register_runtime(self, request):
        serializer = RuntimeRegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            from .prefect_dispatcher import register_runtime_execution

            execution = register_runtime_execution(serializer.data)
        except Workflow.DoesNotExist:
            return Response(
                {'error': 'Active Prefect workflow not found.'},
                status=status.HTTP_409_CONFLICT,
            )
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(
            {'execution_id': str(execution.id)},
            status=status.HTTP_201_CREATED,
        )

    @action(
        detail=False,
        methods=['get'],
        url_path='runtime-policy',
        authentication_classes=[StaffTokenAuthentication],
        permission_classes=[permissions.IsAdminUser],
    )
    def runtime_policy(self, request):
        from accounts.models import SystemSettings

        return Response({
            'workflow_http_allowlist': SystemSettings.get_solo().workflow_http_allowlist,
        })

    @action(
        detail=True,
        methods=['post'],
        url_path='runtime-sync',
        authentication_classes=[StaffTokenAuthentication],
        permission_classes=[permissions.IsAdminUser],
    )
    def runtime_sync(self, request, pk=None):
        serializer = RuntimeSnapshotSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            from .prefect_dispatcher import apply_runtime_snapshot

            execution = apply_runtime_snapshot(self.get_object(), serializer.data)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(WorkflowExecutionDetailSerializer(execution).data)

    def retrieve(self, request, *args, **kwargs):
        # Opportunistically reconcile non-terminal Prefect-backed executions
        # with the upstream flow run before serializing. Failures are
        # swallowed so a Prefect outage never breaks the detail page.
        execution = self.get_object()
        if (
            execution.workflow.execution_engine == 'prefect'
            and (
                execution.status not in {'completed', 'failed', 'cancelled'}
                or (execution.status == 'failed' and not execution.error_message)
            )
            and execution.task_result_id
        ):
            try:
                from . import prefect_dispatcher
                prefect_dispatcher.sync_status(execution)
                execution.refresh_from_db()
            except Exception:  # pragma: no cover - defensive
                pass
        serializer = self.get_serializer(execution)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        execution = self.get_object()

        if execution.status not in ['pending', 'running', 'paused']:
            return Response({'error': 'Cannot cancel execution in current state'}, status=status.HTTP_400_BAD_REQUEST)

        # For Prefect-backed runs we forward the cancel to Prefect first; the
        # local DB row is then marked cancelled regardless so the UI reflects
        # the operator's intent immediately even if Prefect is slow.
        if execution.workflow.execution_engine == 'prefect' and execution.task_result_id:
            try:
                from . import prefect_dispatcher
                prefect_dispatcher.cancel(execution)
            except Exception:  # pragma: no cover - defensive
                pass

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

    @action(detail=True, methods=['post'], url_path='refresh-prefect-status')
    def refresh_prefect_status(self, request, pk=None):
        """
        Force-sync a Prefect-backed execution from the Prefect Server.

        Used by the executions UI when the operator clicks 'Refresh from
        Prefect'. Returns the up-to-date detail payload.
        """
        execution = self.get_object()
        if execution.workflow.execution_engine != 'prefect':
            return Response(
                {'error': 'Execution is not running on the Prefect engine.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not execution.task_result_id:
            return Response(
                {'error': 'No Prefect flow run id recorded for this execution.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            from . import prefect_dispatcher
            prefect_dispatcher.sync_status(execution)
            execution.refresh_from_db()
        except Exception as exc:
            return Response(
                {'error': f'Prefect sync failed: {exc}'},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response(WorkflowExecutionDetailSerializer(execution).data)

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


class WorkflowPublishView(APIView):
    """Publish a Django workflow to persistent Prefect flow files and deployment."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk=None):
        try:
            workflow = Workflow.objects.get(pk=pk)
        except Workflow.DoesNotExist:
            return Response({'error': 'Workflow not found'}, status=status.HTTP_404_NOT_FOUND)

        if not workflow.steps.filter(is_active=True).exists():
            return Response(
                {'error': 'Cannot publish a workflow with no active steps.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        register_deployment = str(request.data.get('register_deployment', 'true')).lower() == 'true'

        from .publisher import publish_workflow
        try:
            result = publish_workflow(workflow, register_deployment=register_deployment)
        except ValueError as exc:
            return Response(
                {'error': str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as exc:
            logger.exception('Workflow publish failed for workflow %s', workflow.id)
            return Response(
                {'error': f'Workflow publish failed: {exc}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        for schedule in workflow.schedules.select_related('workflow').all():
            try:
                from .prefect_dispatcher import sync_schedule
                sync_schedule(schedule)
            except (OSError, ValueError, prefect_client.PrefectAPIError) as exc:
                logger.warning('Published workflow schedule sync failed for %s: %s', schedule.id, exc)

        return Response({
            'status': 'published',
            'workflow_id': str(workflow.id),
            'workflow_name': workflow.name,
            **result,
        }, status=status.HTTP_200_OK)


class WorkflowPublishedListView(APIView):
    """Dormant server-manifest recovery endpoint; intentionally not routed."""

    # Kept (rather than deleted) so the previous recovery implementation is
    # documented in place. It is disabled because this product does not offer
    # disaster recovery and imported manifest UUIDs can diverge from DB UUIDs.

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from .publisher import list_published_manifests
        manifests = list_published_manifests()
        return Response({'manifests': manifests})


class WorkflowImportView(APIView):
    """Import a workflow from a published manifest file or uploaded JSON."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        from .publisher import import_workflow_from_json_payload
        # Server-local manifest recovery is intentionally disabled. Keep the
        # old import available in source history without exposing it here:
        # from .publisher import import_workflow_from_manifest

        update_existing = str(request.data.get('update_existing', 'true')).lower() == 'true'

        uploaded_file = request.FILES.get('file')
        if uploaded_file:
            import json
            try:
                content = uploaded_file.read().decode('utf-8')
                payload = json.loads(content)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                return Response(
                    {'error': f'Invalid JSON file: {exc}'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            import_report = {}
            workflow = import_workflow_from_json_payload(
                payload,
                created_by=request.user,
                update_existing=update_existing,
                import_report=import_report,
            )
            return Response({
                'status': 'imported',
                'source': 'upload',
                'workflow_id': str(workflow.id),
                'workflow_name': workflow.name,
                'removed_secret_fields': import_report.get('removed_secret_fields', []),
            }, status=status.HTTP_201_CREATED)

        # Disabled server-manifest recovery path. It is preserved as comments
        # because recovery is out of scope and the old implementation could
        # create a DB workflow whose UUID does not match the manifest pointer.
        # filename = request.data.get('filename')
        # if filename:
        #     try:
        #         workflow = import_workflow_from_manifest(
        #             filename,
        #             created_by=request.user,
        #             update_existing=update_existing,
        #         )
        #     except FileNotFoundError as exc:
        #         return Response({'error': str(exc)}, status=status.HTTP_404_NOT_FOUND)
        #     return Response({
        #         'status': 'imported',
        #         'source': 'manifest',
        #         'workflow_id': str(workflow.id),
        #         'workflow_name': workflow.name,
        #     }, status=status.HTTP_201_CREATED)

        workflow_definition = request.data.get('workflow_definition')
        if isinstance(workflow_definition, dict):
            import_report = {}
            workflow = import_workflow_from_json_payload(
                workflow_definition,
                created_by=request.user,
                update_existing=update_existing,
                import_report=import_report,
            )
            return Response({
                'status': 'imported',
                'source': 'payload',
                'workflow_id': str(workflow.id),
                'workflow_name': workflow.name,
                'removed_secret_fields': import_report.get('removed_secret_fields', []),
            }, status=status.HTTP_201_CREATED)

        return Response(
            {'error': 'Provide either a JSON file upload or workflow_definition payload.'},
            status=status.HTTP_400_BAD_REQUEST,
        )


class TicketWorkflowBindingViewSet(viewsets.ModelViewSet):
    serializer_class = TicketWorkflowBindingSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        queryset = TicketWorkflowBinding.objects.select_related('workflow', 'created_by')
        workflow_id = self.request.query_params.get('workflow')
        if workflow_id:
            queryset = queryset.filter(workflow_id=workflow_id)
        return queryset

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class TicketCallablePlaybookSuggestView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        workflows = find_callable_workflows()
        serializer = WorkflowListSerializer(workflows, many=True)
        return Response({'results': serializer.data, 'count': workflows.count()})


class TicketCallablePlaybookSchemaView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, workflow_id):
        workflow = find_callable_workflows().filter(id=workflow_id).first()
        if not workflow:
            return Response({'error': 'Workflow not found or not callable from ticket'}, status=status.HTTP_404_NOT_FOUND)
        return Response({
            'workflow_id': str(workflow.id),
            'workflow_name': workflow.name,
            'inputs_schema': workflow.inputs_schema,
            'allowed_invoker_roles': workflow.allowed_invoker_roles,
        })


class TicketCallablePlaybookInvokeView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = 'ticket_playbook_invoke'

    def post(self, request, workflow_id):
        workflow = find_callable_workflows().filter(id=workflow_id).first()
        if not workflow:
            return Response({'error': 'Workflow not found or not callable from ticket'}, status=status.HTTP_404_NOT_FOUND)

        allowed_roles = workflow.allowed_invoker_roles or []
        if allowed_roles:
            user_roles = set(request.user.groups.values_list('name', flat=True))
            if not user_roles.intersection(set(allowed_roles)):
                return Response({'error': 'You do not have permission to invoke this playbook'}, status=status.HTTP_403_FORBIDDEN)

        ticket_data = request.data.get('ticket') or {}
        if not isinstance(ticket_data, dict) or not ticket_data.get('ticket_number'):
            return Response({'error': 'ticket.ticket_number is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            execution = invoke_workflow_from_ticket(
                workflow=workflow,
                ticket_data=ticket_data,
                user_inputs=request.data.get('inputs') or {},
                executed_by=request.user,
                comment=request.data.get('comment', ''),
            )
        except Exception as exc:
            return Response({'error': f'Workflow invocation failed: {exc}'}, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            'execution_id': str(execution.id),
            'status': execution.status,
            'trigger_source': execution.trigger_source,
        }, status=status.HTTP_201_CREATED)


class TicketWorkflowDispatchView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        trigger_event = request.data.get('trigger_event')
        if trigger_event not in ('on_create', 'on_status_change'):
            return Response({'error': "trigger_event must be 'on_create' or 'on_status_change'"}, status=status.HTTP_400_BAD_REQUEST)

        ticket_data = request.data.get('ticket') or {}
        if not isinstance(ticket_data, dict) or not ticket_data.get('ticket_number'):
            return Response({'error': 'ticket.ticket_number is required'}, status=status.HTTP_400_BAD_REQUEST)

        result = dispatch_ticket_event(trigger_event, ticket_data, executed_by=request.user)
        return Response(result, status=status.HTTP_202_ACCEPTED)


class TicketWorkflowWorkplanView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        ticket_number = str(request.query_params.get('ticket_number') or '').strip()
        if not ticket_number:
            return Response({'error': 'ticket_number is required'}, status=status.HTTP_400_BAD_REQUEST)
        items = get_ticket_workplan(ticket_number)
        return Response({'ticket_number': ticket_number, 'results': items, 'count': len(items)})
