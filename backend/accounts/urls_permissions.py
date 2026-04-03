from django.urls import path

from accounts.api.permissions_views import (
    GroupPermissionsAPIView,
    PermissionListAPIView,
    UserGroupsAPIView,
    UserPermissionsAPIView,
)

app_name = 'permissions_api'

urlpatterns = [
    # List permissions (optionally filtered by app_labels or common_only)
    path('permissions/', PermissionListAPIView.as_view(), name='permission-list'),

    # Group permissions
    path('groups/<int:group_id>/permissions/', GroupPermissionsAPIView.as_view(), name='group-permissions'),

    # User direct permissions
    path('users/<int:user_id>/permissions/', UserPermissionsAPIView.as_view(), name='user-permissions'),

    # User groups
    path('users/<int:user_id>/groups/', UserGroupsAPIView.as_view(), name='user-groups'),
]

