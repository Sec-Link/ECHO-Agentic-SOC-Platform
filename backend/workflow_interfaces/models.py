import secrets
import uuid

from django.contrib.auth.models import User
from django.db import models


class InterfaceEndpoint(models.Model):
    INTERFACE_TYPE_CHOICES = [
        ('api', 'API'),
        ('webhook', 'Webhook'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    interface_type = models.CharField(max_length=20, choices=INTERFACE_TYPE_CHOICES, default='api')
    secret_token = models.CharField(max_length=128, default='', blank=True)
    hmac_secret = models.CharField(max_length=128, blank=True, default='')
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='interface_endpoints',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_event_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} ({self.interface_type})"

    def save(self, *args, **kwargs):
        if not self.secret_token:
            self.secret_token = secrets.token_urlsafe(24)
        super().save(*args, **kwargs)


class InterfaceRequestLog(models.Model):
    endpoint = models.ForeignKey(
        InterfaceEndpoint,
        on_delete=models.CASCADE,
        related_name='logs',
    )
    method = models.CharField(max_length=10)
    source_ip = models.GenericIPAddressField(null=True, blank=True)
    response_status = models.PositiveSmallIntegerField(default=200)
    request_body = models.JSONField(default=dict, blank=True)
    response_body = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.endpoint.name} {self.method} {self.response_status}"

