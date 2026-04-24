from rest_framework import viewsets, permissions
from django.contrib.auth.models import User, Group
from .serializers import UserAdminSerializer, GroupSerializer


class IsStaffOrReadOnly(permissions.BasePermission):
    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return request.user and request.user.is_authenticated
        return request.user and request.user.is_staff


class UserAdminViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all().prefetch_related('groups', 'user_permissions', 'user_auth_profile')
    serializer_class = UserAdminSerializer
    permission_classes = [IsStaffOrReadOnly]


class GroupAdminViewSet(viewsets.ModelViewSet):
    queryset = Group.objects.all().prefetch_related('permissions')
    serializer_class = GroupSerializer
    permission_classes = [permissions.IsAdminUser]
