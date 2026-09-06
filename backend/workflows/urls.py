"""
Workflow URL Configuration

API routes for the workflows app.
"""
from django.urls import path, include
from rest_framework.routers import SimpleRouter
from .worker_views import WorkerTicketViewSet

from .views import (
    ActionTemplateViewSet,
    PrefectDeploymentListView,
    PrefectDeploymentSyncView,
    WorkflowViewSet,
    WorkflowStepViewSet,
    WorkflowExecutionViewSet,
    WorkflowStatsView,
    WorkflowPublishView,
    # WorkflowPublishedListView,  # Disabled: no server-manifest disaster recovery.
    WorkflowImportView,
    SavedWorkflowNodeViewSet,
    WorkflowScheduleViewSet,
    TicketWorkflowBindingViewSet,
    TicketCallablePlaybookSuggestView,
    TicketCallablePlaybookSchemaView,
    TicketCallablePlaybookInvokeView,
    TicketWorkflowDispatchView,
    TicketWorkflowWorkplanView,
)

# API Router - use SimpleRouter to avoid duplicate format suffix converter registration
router = SimpleRouter()
router.register(r'action-templates', ActionTemplateViewSet, basename='action-template')
router.register(r'workflows', WorkflowViewSet, basename='workflow')
router.register(r'executions', WorkflowExecutionViewSet, basename='execution')
router.register(r'steps', WorkflowStepViewSet, basename='step')
router.register(r'saved-nodes', SavedWorkflowNodeViewSet, basename='saved-node')
router.register(r'schedules', WorkflowScheduleViewSet, basename='schedule')
router.register(r'ticket-workflow-bindings', TicketWorkflowBindingViewSet, basename='ticket-workflow-binding')

# API URL patterns
urlpatterns = [
    path('worker/tickets/', WorkerTicketViewSet.as_view({'get': 'list', 'post': 'create'}),
         name='workflow-worker-ticket-list'),
    path('worker/tickets/<str:ticket_number>/',
         WorkerTicketViewSet.as_view({'get': 'retrieve', 'patch': 'partial_update'}),
         name='workflow-worker-ticket-detail'),
    path('', include(router.urls)),
    path('stats/', WorkflowStatsView.as_view(), name='workflow-stats'),
    path('prefect/deployments/', PrefectDeploymentListView.as_view(), name='prefect-deployments'),
    path('prefect/sync/', PrefectDeploymentSyncView.as_view(), name='prefect-sync'),
    path('workflows/<uuid:pk>/publish/', WorkflowPublishView.as_view(), name='workflow-publish'),
    # Intentionally disabled rather than deleted. Server-manifest recovery can
    # mismatch manifest and database UUIDs, and disaster recovery is out of scope.
    # path('publish/manifests/', WorkflowPublishedListView.as_view(), name='workflow-published-list'),
    path('import/', WorkflowImportView.as_view(), name='workflow-import'),
    path('ticket-playbooks/suggest/', TicketCallablePlaybookSuggestView.as_view(), name='ticket-playbook-suggest'),
    path('ticket-playbooks/<uuid:workflow_id>/inputs-schema/', TicketCallablePlaybookSchemaView.as_view(), name='ticket-playbook-inputs-schema'),
    path('ticket-playbooks/<uuid:workflow_id>/invoke/', TicketCallablePlaybookInvokeView.as_view(), name='ticket-playbook-invoke'),
    path('ticket-playbooks/dispatch/', TicketWorkflowDispatchView.as_view(), name='ticket-playbook-dispatch'),
    path('ticket-playbooks/workplan/', TicketWorkflowWorkplanView.as_view(), name='ticket-playbook-workplan'),
]
