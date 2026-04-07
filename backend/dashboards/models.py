from django.db import models
import uuid

# -----------------------------
# 中文注释：
# Dashboard 模型用于表示一个仪表盘（Dashboard）的元数据：
# - `layout` 存放仪表盘布局（JSON），`widgets` 存放面板集合
# - 该模型仅包含基础字段，渲染/展示交由前端负责
# -----------------------------

class Dashboard(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    layout = models.JSONField(default=dict, blank=True)
    widgets = models.JSONField(default=list, blank=True)
    # Dashboard-level time selector and timestamp field for SQL filtering
    timestamp_field = models.CharField(max_length=200, null=True, blank=True)
    time_selector = models.CharField(max_length=50, null=True, blank=True)
    timestamp_relative = models.CharField(max_length=50, null=True, blank=True)
    timestamp_relative_custom_value = models.IntegerField(null=True, blank=True)
    timestamp_relative_custom_unit = models.CharField(max_length=10, null=True, blank=True)
    timestamp_from = models.DateTimeField(null=True, blank=True)
    timestamp_to = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
