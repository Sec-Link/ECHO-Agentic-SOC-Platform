from rest_framework import viewsets
from .models import Dashboard
from .serializers import DashboardSerializer
from accounts.permissions import RbacModelPermissions

# -----------------------------
# 中文注释：
# 本模块定义了 Dashboard 的 REST API 视图集（ViewSet），使用 DRF 的 ModelViewSet 提供标准的 CRUD 行为。
# DashboardViewSet 暴露的接口由路由器在 `backend/urls.py` 中注册为 `/api/dashboards/`。
# 本文件仅添加文档注释，不修改运行时逻辑。
# -----------------------------

class DashboardViewSet(viewsets.ModelViewSet):
    queryset = Dashboard.objects.all().order_by('-created_at')
    serializer_class = DashboardSerializer
    permission_classes = [RbacModelPermissions]
