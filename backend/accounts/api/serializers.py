from rest_framework import serializers
from django.contrib.auth.models import User, Group, Permission
from accounts.models import UserAuthProfile


class UserAdminSerializer(serializers.ModelSerializer):
    # Allow setting groups by ID in admin API
    groups = serializers.PrimaryKeyRelatedField(many=True, queryset=Group.objects.all())
    is_readonly = serializers.SerializerMethodField()
    auth_method = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            'id', 'username', 'email', 'first_name', 'last_name',
            'is_active', 'is_staff', 'is_superuser', 'date_joined', 'groups', 'is_readonly', 'auth_method',
        )
        read_only_fields = ('date_joined',)

    def get_is_readonly(self, obj: User) -> bool:
        profile = getattr(obj, "user_auth_profile", None)
        return bool(profile and profile.is_readonly)

    def get_auth_method(self, obj: User) -> str:
        profile = getattr(obj, "user_auth_profile", None)
        if profile is None:
            return UserAuthProfile.AuthMethod.PASSWORD
        return profile.auth_method


class GroupSerializer(serializers.ModelSerializer):
    permissions = serializers.PrimaryKeyRelatedField(many=True, queryset=Permission.objects.all())

    class Meta:
        model = Group
        fields = ('id', 'name', 'permissions')
