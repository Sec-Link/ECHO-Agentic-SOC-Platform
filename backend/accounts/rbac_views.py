from __future__ import annotations

from django.contrib.auth.models import Permission
from django.http import JsonResponse
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView


COMMON_APP_LABELS = {
    "tickets",
    "accounts",
    "integrations",
    "es_integration",
    "dashboards",
    "datasource",
    "orchestrator",
    "correlation",
}


class CurrentUserPermissionsAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        perms = user.get_all_permissions()
        role = "superuser" if user.is_superuser else ("admin" if user.is_staff else "user")
        return JsonResponse(
            {
                "user_id": user.id,
                "username": user.username,
                "is_staff": user.is_staff,
                "is_superuser": user.is_superuser,
                "role": role,
                "permissions": sorted(perms),
            }
        )


class AppPermissionMatrixAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        app_labels_param = request.query_params.get("app_labels")
        if app_labels_param:
            labels = [x.strip() for x in app_labels_param.split(",") if x.strip()]
            app_labels = set(labels)
        else:
            app_labels = COMMON_APP_LABELS

        qs = Permission.objects.select_related("content_type").filter(
            content_type__app_label__in=app_labels
        )
        qs = qs.order_by("content_type__app_label", "codename")
        data = {}
        for perm in qs:
            app_label = perm.content_type.app_label
            data.setdefault(app_label, []).append(
                {
                    "id": perm.id,
                    "codename": perm.codename,
                    "name": perm.name,
                    "app_label": app_label,
                    "model": perm.content_type.model,
                    "full": f"{app_label}.{perm.codename}",
                }
            )
        return JsonResponse({"apps": data})
