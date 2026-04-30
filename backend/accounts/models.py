from __future__ import annotations
import uuid
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class RegistrationRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(db_index=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_registration_requests",
    )
    review_reason = models.TextField(null=True, blank=True)
    approved_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_registration_requests",
    )

    class Meta:
        db_table = "accounts_registration_request"
        indexes = [
            models.Index(fields=["status", "requested_at"], name="regreq_status_reqat_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["email"],
                condition=Q(status="pending"),
                name="uniq_pending_registration_email",
            ),
        ]

    def save(self, *args, **kwargs):
        self.email = (self.email or "").strip().lower()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.email} ({self.status})"


class UserAuthProfile(models.Model):
    class AuthMethod(models.TextChoices):
        PASSWORD = "password", "Password"
        OTP_ONLY = "otp_only", "OTP only"

    class LoginMethod(models.TextChoices):
        PASSWORD = "password", "Password"
        OTP = "otp", "OTP"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="user_auth_profile")
    auth_method = models.CharField(max_length=16, choices=AuthMethod.choices, default=AuthMethod.PASSWORD)
    is_readonly = models.BooleanField(default=False, db_index=True)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    last_login_method = models.CharField(max_length=16, choices=LoginMethod.choices, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_user_auth_profile"

    def __str__(self) -> str:
        return f"{self.user.username} ({self.auth_method})"


class EmailOTP(models.Model):
    class Purpose(models.TextChoices):
        LOGIN = "login", "Login"
        ACTIVATION = "activation", "Activation"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="email_otps")
    purpose = models.CharField(max_length=16, choices=Purpose.choices, default=Purpose.LOGIN)
    otp_hash = models.CharField(max_length=255)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=5)
    sent_to_email = models.EmailField()
    request_ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "accounts_email_otp"
        indexes = [
            models.Index(fields=["user", "purpose", "expires_at", "used_at"], name="emailotp_up_exp_used_idx"),
        ]

    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    def is_usable(self) -> bool:
        return self.used_at is None and not self.is_expired() and self.attempt_count < self.max_attempts

    def __str__(self) -> str:
        return f"{self.user.username} ({self.purpose})"


class AuditLog(models.Model):
    class EventType(models.TextChoices):
        OTP_REQUEST = "otp_request", "OTP request"
        OTP_VERIFY = "otp_verify", "OTP verify"
        ADMIN_APPROVE = "admin_approve", "Admin approve"
        ADMIN_REJECT = "admin_reject", "Admin reject"
        REGISTRATION = "registration", "Registration"
        EMAIL_SENT = "email_sent", "Email sent"

    class Status(models.TextChoices):
        SUCCESS = "success", "Success"
        FAILURE = "failure", "Failure"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event_type = models.CharField(max_length=32, choices=EventType.choices, db_index=True)
    user_email = models.EmailField(null=True, blank=True, db_index=True)
    admin_email = models.EmailField(null=True, blank=True, db_index=True)
    ip_address = models.CharField(max_length=64, blank=True, default="")
    user_agent = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=16, choices=Status.choices, db_index=True)
    failure_reason = models.TextField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "accounts_audit_log"
        indexes = [
            models.Index(fields=["event_type", "status", "created_at"], name="audit_evt_status_ts_idx"),
            models.Index(fields=["user_email", "created_at"], name="audit_user_ts_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.event_type} ({self.status})"


class SystemSettings(models.Model):
    auto_approve_enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_system_settings"

    @classmethod
    def get_solo(cls) -> "SystemSettings":
        obj = cls.objects.order_by("id").first()
        if obj is None:
            obj = cls.objects.create(auto_approve_enabled=True)
        return obj

    @classmethod
    def is_auto_approve_enabled(cls) -> bool:
        return bool(cls.get_solo().auto_approve_enabled)

    def __str__(self) -> str:
        return f"SystemSettings(auto_approve_enabled={self.auto_approve_enabled})"
