from django.urls import path

from accounts.rbac_views import AppPermissionMatrixAPIView, CurrentUserPermissionsAPIView

urlpatterns = [
    path("me/", CurrentUserPermissionsAPIView.as_view(), name="rbac-me"),
    path("permission_matrix/", AppPermissionMatrixAPIView.as_view(), name="rbac-permission-matrix"),
]
