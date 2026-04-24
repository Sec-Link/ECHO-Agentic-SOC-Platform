from __future__ import annotations

from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from .models import AuditLog, RegistrationRequest, SystemSettings, UserAuthProfile
from .services import normalize_email


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, validators=[validate_password])
    password_confirm = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ("username", "email", "password", "password_confirm")

    def validate(self, attrs):
        if attrs["password"] != attrs["password_confirm"]:
            raise serializers.ValidationError({"password": "Password fields didn't match."})
        attrs["email"] = normalize_email(attrs.get("email", ""))
        return attrs

    def create(self, validated_data):
        validated_data.pop("password_confirm")
        return User.objects.create_user(**validated_data)


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField()


class UserSerializer(serializers.ModelSerializer):
    is_readonly = serializers.SerializerMethodField()
    auth_method = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "date_joined",
            "is_readonly",
            "auth_method",
        )
        read_only_fields = ("date_joined",)

    @staticmethod
    def _safe_profile(obj: User):
        return UserAuthProfile.objects.filter(user=obj).first()

    def get_is_readonly(self, obj: User) -> bool:
        profile = self._safe_profile(obj)
        return bool(profile and profile.is_readonly)

    def get_auth_method(self, obj: User) -> str:
        profile = self._safe_profile(obj)
        if not profile:
            return UserAuthProfile.AuthMethod.PASSWORD
        return profile.auth_method


class PasswordChangeSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True, required=True)
    new_password = serializers.CharField(write_only=True, required=True)
    confirm_password = serializers.CharField(write_only=True, required=True)

    def validate_old_password(self, value):
        user = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError("Old password is incorrect.")
        return value

    def validate_new_password(self, value):
        validate_password(value)
        return value

    def validate(self, data):
        if data["new_password"] != data["confirm_password"]:
            raise serializers.ValidationError({"confirm_password": "Passwords do not match."})
        if data["old_password"] == data["new_password"]:
            raise serializers.ValidationError({"new_password": "New password cannot be the same as old password."})
        return data

    def save(self):
        user = self.context["request"].user
        user.set_password(self.validated_data["new_password"])
        user.save()
        return user


class EmailRegistrationSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value: str) -> str:
        return normalize_email(value)


class OTPRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value: str) -> str:
        return normalize_email(value)


class OTPVerifySerializer(serializers.Serializer):
    email = serializers.EmailField()
    otp = serializers.RegexField(regex=r"^\d{6}$", max_length=6, min_length=6)

    def validate_email(self, value: str) -> str:
        return normalize_email(value)


class RegistrationRequestListSerializer(serializers.ModelSerializer):
    reviewed_by_username = serializers.CharField(source="reviewed_by.username", read_only=True)
    reviewed_by_id = serializers.IntegerField(source="reviewed_by.id", read_only=True)
    approved_by_id = serializers.IntegerField(source="reviewed_by.id", read_only=True)
    approved_user_id = serializers.IntegerField(source="approved_user.id", read_only=True)

    class Meta:
        model = RegistrationRequest
        fields = (
            "id",
            "email",
            "status",
            "requested_at",
            "reviewed_at",
            "reviewed_by",
            "reviewed_by_id",
            "approved_by_id",
            "reviewed_by_username",
            "review_reason",
            "approved_user_id",
        )


class RegistrationApproveSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, max_length=1000)


class RegistrationRejectSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=1000)


class AuditLogListSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditLog
        fields = (
            "id",
            "event_type",
            "user_email",
            "admin_email",
            "ip_address",
            "user_agent",
            "status",
            "failure_reason",
            "metadata",
            "created_at",
        )


class SystemSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = SystemSettings
        fields = ("auto_approve_enabled", "updated_at")
        read_only_fields = ("updated_at",)
