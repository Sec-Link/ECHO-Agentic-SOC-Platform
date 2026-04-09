from rest_framework.routers import DefaultRouter
from django.urls import path, include
from .views import AssetViewSet, AssetColumnViewSet, AssetAuditLogViewSet

router = DefaultRouter()
router.register(r'assets', AssetViewSet, basename='asset')
router.register(r'columns', AssetColumnViewSet, basename='asset-column')
router.register(r'logs', AssetAuditLogViewSet, basename='asset-log')

urlpatterns = [
    path('', include(router.urls)),
]

