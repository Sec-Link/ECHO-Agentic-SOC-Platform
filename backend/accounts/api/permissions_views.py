from django.contrib.auth.models import Group, Permission, User
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .permissions_serializers import (
    GroupPermissionUpdateSerializer,
    PermissionSerializer,
    UserGroupUpdateSerializer,
    UserPermissionUpdateSerializer,
)


COMMON_APP_LABELS = {
    'tickets',
    'accounts',
    'integrations',
    'es_integration',
    'dashboards',
    'datasource',
    'orchestrator',
    'correlation',
    'users',
}


class PermissionListAPIView(APIView):
    """List Django auth permissions. Supports filtering by app labels."""

    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        app_labels_param = request.query_params.get('app_labels')
        common_only = request.query_params.get('common_only')

        qs = Permission.objects.select_related('content_type').all()

        if app_labels_param:
            labels = [x.strip() for x in app_labels_param.split(',') if x.strip()]
            if labels:
                qs = qs.filter(content_type__app_label__in=labels)

        if common_only and str(common_only).lower() in {'1', 'true', 'yes'}:
            qs = qs.filter(content_type__app_label__in=COMMON_APP_LABELS)

        qs = qs.order_by('content_type__app_label', 'codename')
        return Response(PermissionSerializer(qs, many=True).data)


class GroupPermissionsAPIView(APIView):
    """Get or set permissions for a Django Group."""

    permission_classes = [permissions.IsAdminUser]

    def get(self, request, group_id: int):
        group = Group.objects.prefetch_related('permissions__content_type').get(pk=group_id)
        perms = group.permissions.all().order_by('content_type__app_label', 'codename')
        return Response(
            {
                'group_id': group.id,
                'group_name': group.name,
                'permission_ids': list(perms.values_list('id', flat=True)),
                'permissions': PermissionSerializer(perms, many=True).data,
            }
        )

    def put(self, request, group_id: int):
        group = Group.objects.get(pk=group_id)
        serializer = GroupPermissionUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        perm_ids = serializer.validated_data['permission_ids']
        perms = Permission.objects.filter(id__in=perm_ids)

        found_ids = set(perms.values_list('id', flat=True))
        missing = sorted(set(perm_ids) - found_ids)
        if missing:
            return Response(
                {'detail': 'Some permission_ids do not exist.', 'missing_permission_ids': missing},
                status=status.HTTP_400_BAD_REQUEST,
            )

        group.permissions.set(perms)
        return Response({'detail': 'Group permissions updated.', 'group_id': group.id})


class UserPermissionsAPIView(APIView):
    """Get or set direct permissions for a Django User."""

    permission_classes = [permissions.IsAdminUser]

    def get(self, request, user_id: int):
        user = User.objects.prefetch_related('user_permissions__content_type').get(pk=user_id)
        perms = user.user_permissions.all().order_by('content_type__app_label', 'codename')
        return Response(
            {
                'user_id': user.id,
                'username': user.username,
                'permission_ids': list(perms.values_list('id', flat=True)),
                'permissions': PermissionSerializer(perms, many=True).data,
            }
        )

    def put(self, request, user_id: int):
        user = User.objects.get(pk=user_id)
        serializer = UserPermissionUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        perm_ids = serializer.validated_data['permission_ids']
        perms = Permission.objects.filter(id__in=perm_ids)

        found_ids = set(perms.values_list('id', flat=True))
        missing = sorted(set(perm_ids) - found_ids)
        if missing:
            return Response(
                {'detail': 'Some permission_ids do not exist.', 'missing_permission_ids': missing},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.user_permissions.set(perms)
        return Response({'detail': 'User direct permissions updated.', 'user_id': user.id})


class UserGroupsAPIView(APIView):
    """Get or set group membership for a Django User."""

    permission_classes = [permissions.IsAdminUser]

    def get(self, request, user_id: int):
        user = User.objects.prefetch_related('groups').get(pk=user_id)
        groups = user.groups.all().order_by('name')
        return Response(
            {
                'user_id': user.id,
                'username': user.username,
                'group_ids': list(groups.values_list('id', flat=True)),
                'groups': [{'id': g.id, 'name': g.name} for g in groups],
            }
        )

    def put(self, request, user_id: int):
        user = User.objects.get(pk=user_id)
        serializer = UserGroupUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        group_ids = serializer.validated_data['group_ids']
        groups = Group.objects.filter(id__in=group_ids)

        found_ids = set(groups.values_list('id', flat=True))
        missing = sorted(set(group_ids) - found_ids)
        if missing:
            return Response(
                {'detail': 'Some group_ids do not exist.', 'missing_group_ids': missing},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.groups.set(groups)
        return Response({'detail': 'User groups updated.', 'user_id': user.id})

