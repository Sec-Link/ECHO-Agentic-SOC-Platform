from rest_framework import serializers
from .models import Dashboard

# -----------------------------
# 中文注释：
# 该模块定义 Dashboard 的序列化器（用于 API 输入/输出转换）。
# - `DashboardSerializer` 将 Dashboard 的主要字段（id,name,layout,widgets,created_at,updated_at）序列化
# - 序列化器主要用于 REST API 的请求/响应转换，未改变模型行为。
# -----------------------------

class DashboardSerializer(serializers.ModelSerializer):
    class Meta:
        model = Dashboard
        fields = ['id','name','layout','widgets','created_at','updated_at',
                  'timestamp_field','time_selector','timestamp_relative','timestamp_relative_custom_value','timestamp_relative_custom_unit','timestamp_from','timestamp_to']
