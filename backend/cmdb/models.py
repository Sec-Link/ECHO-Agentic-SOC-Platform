from django.db import models
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _

User = get_user_model()

class Asset(models.Model):
    """
    Represents an IT Asset in the CMDB.
    """
    # Auto-generated unique serial number (read-only via serializer, editable=True so DRF can expose it)
    asset_number = models.CharField(max_length=30, unique=True, verbose_name=_("Asset Number"), blank=True)
    hostname = models.CharField(max_length=255, blank=True, null=True, verbose_name=_("Hostname"))
    ip_address = models.GenericIPAddressField(verbose_name=_("IP Address"), null=True, blank=True)
    asset_type = models.CharField(max_length=100, verbose_name=_("Asset Type"), help_text=_("Server, Switch, Router, etc."), default="Unknown", blank=True)
    asset_level = models.CharField(max_length=50, verbose_name=_("Asset Level"), help_text=_("Critical, High, Medium, Low"), default="Medium")
    description = models.TextField(verbose_name=_("Description"), blank=True, null=True)
    is_alive = models.BooleanField(default=True, verbose_name=_("Is Alive"))

    # Stores custom attributes as a JSON object (flexible schema)
    # e.g., {"location": "NYC", "owner": "John Doe", "custom_field_1": "value"}
    custom_attributes = models.JSONField(default=dict, blank=True, verbose_name=_("Custom Attributes"))

    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Created At"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("Updated At"))

    # Track who created/last modified (if available via context)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="created_assets")
    updated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="updated_assets")

    def save(self, *args, **kwargs):
        if not self.asset_number:
            import datetime
            prefix = datetime.datetime.now().strftime("ASSET-%Y%m%d-")
            last = Asset.objects.filter(asset_number__startswith=prefix).order_by('-asset_number').first()
            if last:
                try:
                    seq = int(last.asset_number.split('-')[-1]) + 1
                except (ValueError, IndexError):
                    seq = 1
            else:
                seq = 1
            self.asset_number = f"{prefix}{seq:04d}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.asset_number} ({self.hostname or 'N/A'})"

class AssetColumn(models.Model):
    """
    Defines custom columns that should be displayed or used for validation.
    This allows the UI to know what keys to look for in 'custom_attributes'.
    """
    name = models.CharField(max_length=100, unique=True, verbose_name=_("Column Name"))
    label = models.CharField(max_length=100, verbose_name=_("Display Label"))
    data_type = models.CharField(max_length=50, choices=[
        ('text', 'Text'),
        ('number', 'Number'),
        ('boolean', 'Boolean'),
        ('date', 'Date'),
    ], default='text', verbose_name=_("Data Type"))
    is_required = models.BooleanField(default=False, verbose_name=_("Is Required"))

    def __str__(self):
        return self.label

class AssetAuditLog(models.Model):
    """
    Audit log for tracking changes to Assets.
    """
    ACTION_CHOICES = [
        ('CREATE', 'Create'),
        ('UPDATE', 'Update'),
        ('DELETE', 'Delete'),
        ('IMPORT', 'Import'),
    ]

    asset_number = models.CharField(max_length=30, verbose_name=_("Asset Number"))  # Store even if asset is deleted
    action = models.CharField(max_length=20, choices=ACTION_CHOICES, verbose_name=_("Action"))
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name=_("User"))
    timestamp = models.DateTimeField(auto_now_add=True, verbose_name=_("Timestamp"))
    details = models.JSONField(default=dict, blank=True, verbose_name=_("Change Details"))

    def __str__(self):
        return f"{self.action} - {self.asset_number} - {self.timestamp}"






