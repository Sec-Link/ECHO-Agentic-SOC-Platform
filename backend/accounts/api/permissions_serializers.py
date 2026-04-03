from django.contrib.auth.models import Group, Permission, User
from rest_framework import serializers


class PermissionSerializer(serializers.ModelSerializer):
    app_label = serializers.CharField(source='content_type.app_label', read_only=True)
    model = serializers.CharField(source='content_type.model', read_only=True)

    class Meta:
        model = Permission
        fields = ['id', 'name', 'codename', 'app_label', 'model']


class GroupPermissionUpdateSerializer(serializers.Serializer):
    permission_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        allow_empty=True,
        required=True,
        help_text='List of Permission IDs to set on the group (replaces existing group permissions).',
    )


class UserPermissionUpdateSerializer(serializers.Serializer):
    permission_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        allow_empty=True,
        required=True,
        help_text='List of Permission IDs to set on the user (replaces existing user direct permissions).',
    )


class UserGroupUpdateSerializer(serializers.Serializer):
    group_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        allow_empty=True,
        required=True,
        help_text='List of Group IDs to set on the user (replaces existing user groups).',
    )

