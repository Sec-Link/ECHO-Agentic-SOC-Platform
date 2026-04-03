from rest_framework import serializers
from django.contrib.auth.models import User, Group, Permission


class UserAdminSerializer(serializers.ModelSerializer):
    # Allow setting groups by ID in admin API
    groups = serializers.PrimaryKeyRelatedField(many=True, queryset=Group.objects.all())

    class Meta:
        model = User
        fields = (
            'id', 'username', 'email', 'first_name', 'last_name',
            'is_active', 'is_staff', 'is_superuser', 'date_joined', 'groups',
        )
        read_only_fields = ('date_joined',)


class GroupSerializer(serializers.ModelSerializer):
    permissions = serializers.PrimaryKeyRelatedField(many=True, queryset=Permission.objects.all())

    class Meta:
        model = Group
        fields = ('id', 'name', 'permissions')
