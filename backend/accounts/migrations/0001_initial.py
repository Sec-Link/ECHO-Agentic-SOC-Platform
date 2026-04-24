from __future__ import annotations

import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="RegistrationRequest",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("email", models.EmailField(db_index=True, max_length=254)),
                (
                    "status",
                    models.CharField(
                        choices=[("pending", "Pending"), ("approved", "Approved"), ("rejected", "Rejected")],
                        db_index=True,
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("requested_at", models.DateTimeField(auto_now_add=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("review_reason", models.TextField(blank=True, null=True)),
                (
                    "approved_user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="approved_registration_requests",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "reviewed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="reviewed_registration_requests",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "accounts_registration_request",
                "indexes": [models.Index(fields=["status", "requested_at"], name="regreq_status_reqat_idx")],
            },
        ),
        migrations.CreateModel(
            name="UserAuthProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "auth_method",
                    models.CharField(
                        choices=[("password", "Password"), ("otp_only", "OTP only")],
                        default="password",
                        max_length=16,
                    ),
                ),
                ("is_readonly", models.BooleanField(db_index=True, default=False)),
                ("email_verified_at", models.DateTimeField(blank=True, null=True)),
                (
                    "last_login_method",
                    models.CharField(
                        blank=True,
                        choices=[("password", "Password"), ("otp", "OTP")],
                        max_length=16,
                        null=True,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="user_auth_profile",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "accounts_user_auth_profile",
            },
        ),
        migrations.CreateModel(
            name="EmailOTP",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "purpose",
                    models.CharField(
                        choices=[("login", "Login"), ("activation", "Activation")], default="login", max_length=16
                    ),
                ),
                ("otp_hash", models.CharField(max_length=255)),
                ("expires_at", models.DateTimeField()),
                ("used_at", models.DateTimeField(blank=True, null=True)),
                ("attempt_count", models.PositiveSmallIntegerField(default=0)),
                ("max_attempts", models.PositiveSmallIntegerField(default=5)),
                ("sent_to_email", models.EmailField(max_length=254)),
                ("request_ip", models.GenericIPAddressField(blank=True, null=True)),
                ("user_agent", models.CharField(blank=True, default="", max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="email_otps",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "accounts_email_otp",
                "indexes": [
                    models.Index(fields=["user", "purpose", "expires_at", "used_at"], name="emailotp_up_exp_used_idx")
                ],
            },
        ),
        migrations.AddConstraint(
            model_name="registrationrequest",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status", "pending")),
                fields=("email",),
                name="uniq_pending_registration_email",
            ),
        ),
    ]
