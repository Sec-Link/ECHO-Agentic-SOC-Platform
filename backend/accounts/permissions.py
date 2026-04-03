from __future__ import annotations

from typing import Iterable, Optional

from rest_framework.permissions import BasePermission


class RbacModelPermissions(BasePermission):
    def _get_model(self, view):
        qs = getattr(view, "queryset", None)
        if qs is not None:
            return getattr(qs, "model", None)
        get_qs = getattr(view, "get_queryset", None)
        if callable(get_qs):
            try:
                return get_qs().model
            except Exception:
                return None
        return None

    def _normalize_perm(self, model, perm: str) -> str:
        if "." in perm:
            return perm
        app_label = model._meta.app_label
        model_name = model._meta.model_name
        return f"{app_label}.{perm}_{model_name}"

    def _perm_for(self, request, view, model) -> Optional[Iterable[str]]:
        action = getattr(view, "action", None)
        action_perms = getattr(view, "rbac_action_perms", None)
        if action and isinstance(action_perms, dict) and action in action_perms:
            p = action_perms[action]
            if p is None:
                return None
            if isinstance(p, str):
                return [self._normalize_perm(model, p)]
            return [self._normalize_perm(model, x) for x in p]

        if action in {"list", "retrieve"}:
            return [self._normalize_perm(model, "view")]
        if action == "create":
            return [self._normalize_perm(model, "add")]
        if action in {"update", "partial_update"}:
            return [self._normalize_perm(model, "change")]
        if action == "destroy":
            return [self._normalize_perm(model, "delete")]

        if request.method in {"GET", "HEAD", "OPTIONS"}:
            return [self._normalize_perm(model, "view")]
        if request.method == "DELETE":
            return [self._normalize_perm(model, "delete")]
        return [self._normalize_perm(model, "change")]

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False
        if getattr(user, "is_superuser", False):
            return True

        model = self._get_model(view)
        if model is None:
            return True

        perms = self._perm_for(request, view, model)
        if perms is None:
            return True
        return all(user.has_perm(p) for p in perms)


class HasDjangoPermissions(BasePermission):
    def _get_required(self, request, view) -> Optional[Iterable[str]]:
        mapping = getattr(view, "required_permissions", None)
        if not isinstance(mapping, dict):
            return None
        perms = mapping.get(request.method)
        if perms is None:
            return None
        if isinstance(perms, str):
            return [perms]
        return list(perms)

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False
        if getattr(user, "is_superuser", False):
            return True
        perms = self._get_required(request, view)
        if not perms:
            return True
        return all(user.has_perm(p) for p in perms)
