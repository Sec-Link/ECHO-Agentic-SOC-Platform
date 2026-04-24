from __future__ import annotations

import logging
from typing import Any

from .models import AuditLog

logger = logging.getLogger(__name__)


def _normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def request_ip(request) -> str:
    if not request:
        return ""
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "") or ""


def request_user_agent(request) -> str:
    if not request:
        return ""
    return (request.META.get("HTTP_USER_AGENT", "") or "")[:255]


class AuditService:
    @staticmethod
    def log_event(
        *,
        event_type: str,
        status: str,
        user_email: str | None = None,
        admin_email: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        failure_reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        data = {
            "event_type": event_type,
            "status": status,
            "user_email": _normalize_email(user_email or "") or None,
            "admin_email": _normalize_email(admin_email or "") or None,
            "ip_address": (ip_address or "")[:64],
            "user_agent": (user_agent or "")[:255],
            "failure_reason": (failure_reason or "").strip() or None,
            "metadata": metadata or {},
        }
        AuditLog.objects.create(**data)

    @staticmethod
    def log_safe(**kwargs) -> None:
        try:
            AuditService.log_event(**kwargs)
        except Exception:
            logger.exception("Failed to write audit log", extra={"audit_context": kwargs})

    @staticmethod
    def log_from_request(
        request,
        *,
        event_type: str,
        status: str,
        user_email: str | None = None,
        admin_email: str | None = None,
        failure_reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        AuditService.log_safe(
            event_type=event_type,
            status=status,
            user_email=user_email,
            admin_email=admin_email,
            ip_address=request_ip(request),
            user_agent=request_user_agent(request),
            failure_reason=failure_reason,
            metadata=metadata,
        )
