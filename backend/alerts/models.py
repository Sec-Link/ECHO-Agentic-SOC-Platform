"""Django models for the `alerts` app.

This project is a Django backend (see `manage.py` / `siem_project/settings.py`).
Historically this app contained some SQLAlchemy models, but the runtime code
paths (views/serializers/auth) are Django-based.

The primary table for persisted alerts is `public.alerts_alert`.
"""

from django.db import models

class Alert(models.Model):
    """Persisted alert record (backed by `alerts_alert`).

    Per request: all fields except the primary key can be NULL.
    Note: the *database* constraints must also be updated accordingly.
    """

    alert_id = models.CharField(max_length=64, null=True, blank=True)
    timestamp = models.DateTimeField(null=True, blank=True)
    severity = models.CharField(max_length=16, null=True, blank=True)
    message = models.TextField(null=True, blank=True)
    source_index = models.CharField(max_length=64, null=True, blank=True)

    rule_id = models.CharField(max_length=100, null=True, blank=True)
    title = models.CharField(max_length=256, null=True, blank=True)
    status = models.IntegerField(null=True, blank=True, default=0)
    description = models.TextField(null=True, blank=True)
    category = models.CharField(max_length=100, null=True, blank=True)
    source_data = models.JSONField(null=True, blank=True)

    created_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.IntegerField(null=True, blank=True, default=0)

    class Meta:
        db_table = 'alerts_alert'
        managed = True

    def __str__(self):
        return str(self.alert_id)


class ESIntegrationConfig(models.Model):
    """Stores Elasticsearch integration settings."""
    enabled = models.BooleanField(default=False)
    hosts = models.TextField(help_text='Comma separated hosts, e.g. http://es1:9200,http://es2:9200', blank=True)
    index = models.CharField(max_length=128, default='alerts')
    username = models.CharField(max_length=128, blank=True)
    password = models.CharField(max_length=128, blank=True)
    use_ssl = models.BooleanField(default=False)
    verify_certs = models.BooleanField(default=True)

    def hosts_list(self):
        return [h.strip() for h in self.hosts.split(',') if h.strip()]

    def __str__(self):
        return f"ESConfig({self.id})"


class WebhookConfig(models.Model):
    """Stores webhook configuration used to notify external systems."""
    url = models.CharField(max_length=1024)
    method = models.CharField(max_length=8, default='POST')
    headers = models.JSONField(default=dict, blank=True)
    active = models.BooleanField(default=True)

    def __str__(self):
        return f"WebhookConfig({self.id})"


class ESIntegrationConfigHistory(models.Model):
    """Saved connector presets for quick switching in dashboard settings."""

    hosts = models.TextField(blank=True)
    index = models.CharField(max_length=128, default='alerts')
    username = models.CharField(max_length=128, blank=True)
    password = models.CharField(max_length=128, blank=True)
    use_ssl = models.BooleanField(default=False)
    verify_certs = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"ESConfigHistory({self.id})"


class AlertSyncSchedule(models.Model):
    """Singleton-like schedule state for periodic ES index sync."""

    enabled = models.BooleanField(default=False)
    interval_seconds = models.IntegerField(default=300)
    batch_size = models.IntegerField(default=500)
    fetch_all = models.BooleanField(default=False)
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_status = models.CharField(max_length=20, blank=True, default='')
    last_error = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"AlertSyncSchedule({self.id}, enabled={self.enabled})"


class SchedulerConfig(models.Model):
    interval_seconds = models.IntegerField()
    updated_at = models.DateTimeField(auto_now=True)

class DashboardStats(models.Model):
    timestamp = models.DateTimeField(auto_now_add=True)
    total_alerts = models.IntegerField()
    critical_alerts = models.IntegerField(null=True, blank=True)
    high_alerts = models.IntegerField(null=True, blank=True)
    medium_alerts = models.IntegerField(null=True, blank=True)
    low_alerts = models.IntegerField(null=True, blank=True)
    raw_json = models.JSONField()

"""NOTE: SQLAlchemy models were removed from this module.

If you still need SQLAlchemy for a separate pipeline, create a dedicated module
and keep Django models isolated here.
"""

