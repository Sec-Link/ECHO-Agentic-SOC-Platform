"""
Workflow Template URL Configuration

URL routes for the Django template-based workflow UI.
"""
from django.urls import path
from .views_template import (
    WorkflowListView,
    WorkflowDetailView,
    WorkflowCreateView,
    WorkflowEditView,
    WorkflowExecutionListView,
    WorkflowExecutionDetailView,
)

urlpatterns = [
    path('', WorkflowListView.as_view(), name='workflow-list'),
    path('create/', WorkflowCreateView.as_view(), name='workflow-create'),
    path('<uuid:pk>/', WorkflowDetailView.as_view(), name='workflow-detail'),
    path('<uuid:pk>/edit/', WorkflowEditView.as_view(), name='workflow-edit'),
    path('executions/', WorkflowExecutionListView.as_view(), name='execution-list'),
    path('executions/<uuid:pk>/', WorkflowExecutionDetailView.as_view(), name='execution-detail'),
]

