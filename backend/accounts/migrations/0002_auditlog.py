from __future__ import annotations

import uuid

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="AuditLog",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "event_type",
                    models.CharField(
                        choices=[
                            ("otp_request", "OTP request"),
                            ("otp_verify", "OTP verify"),
                            ("admin_approve", "Admin approve"),
                            ("admin_reject", "Admin reject"),
                            ("registration", "Registration"),
                            ("email_sent", "Email sent"),
                        ],
                        db_index=True,
                        max_length=32,
                    ),
                ),
                ("user_email", models.EmailField(blank=True, db_index=True, max_length=254, null=True)),
                ("admin_email", models.EmailField(blank=True, db_index=True, max_length=254, null=True)),
                ("ip_address", models.CharField(blank=True, default="", max_length=64)),
                ("user_agent", models.CharField(blank=True, default="", max_length=255)),
                (
                    "status",
                    models.CharField(
                        choices=[("success", "Success"), ("failure", "Failure")],
                        db_index=True,
                        max_length=16,
                    ),
                ),
                ("failure_reason", models.TextField(blank=True, null=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
            ],
            options={
                "db_table": "accounts_audit_log",
            },
        ),
        migrations.AddIndex(
            model_name="auditlog",
            index=models.Index(fields=["event_type", "status", "created_at"], name="audit_evt_status_ts_idx"),
        ),
        migrations.AddIndex(
            model_name="auditlog",
            index=models.Index(fields=["user_email", "created_at"], name="audit_user_ts_idx"),
        ),
    ]
