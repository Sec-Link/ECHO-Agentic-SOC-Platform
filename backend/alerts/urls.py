from django.urls import path
from .views import AlertListView, AlertDashboardView, ESConfigView, WebhookConfigView, ESDiagnosticsView, AlertSyncView

urlpatterns = [
    path('list/', AlertListView.as_view(), name='alert-list'),
    path('dashboard/', AlertDashboardView.as_view(), name='alert-dashboard'),
    path('sync/', AlertSyncView.as_view(), name='alert-sync'),
    path('config/es/', ESConfigView.as_view(), name='es-config'),
    path('config/webhook/', WebhookConfigView.as_view(), name='webhook-config'),
    path('debug/es_status/', ESDiagnosticsView.as_view(), name='es-diagnostics'),
]
