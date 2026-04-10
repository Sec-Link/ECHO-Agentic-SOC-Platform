from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views import InterfaceEndpointViewSet, InterfaceIngestView

router = SimpleRouter()
router.register(r'endpoints', InterfaceEndpointViewSet, basename='interface-endpoint')

urlpatterns = [
    path('', include(router.urls)),
    path('endpoints/<uuid:endpoint_id>/ingest/', InterfaceIngestView.as_view(), name='interface-endpoint-ingest'),
    path('webhooks/<uuid:endpoint_id>/', InterfaceIngestView.as_view(), name='interface-webhook-ingest'),
]

