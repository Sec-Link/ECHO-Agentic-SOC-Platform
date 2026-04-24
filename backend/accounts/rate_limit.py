from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Optional

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)


def _int_setting(name: str, default: int) -> int:
    raw = getattr(settings, name, default)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _hash_email(email: str) -> str:
    normalized = (email or "").strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


def _fixed_window_consume(*, key_prefix: str, identifier: str, limit: int, window_seconds: int) -> tuple[bool, int]:
    now = int(time.time())
    bucket = now // window_seconds
    key = f"{key_prefix}:{identifier}:{bucket}"
    expires_in = ((bucket + 1) * window_seconds) - now

    cache.add(key, 0, timeout=window_seconds + 5)
    try:
        current = cache.incr(key)
    except Exception:
        current = int(cache.get(key) or 0) + 1
        cache.set(key, current, timeout=window_seconds + 5)

    if current > limit:
        return False, max(1, expires_in)
    return True, 0


def _cooldown_key(email: str) -> str:
    return f"otp:cooldown:email:{_hash_email(email)}"


def get_otp_cooldown_remaining_seconds(email: str) -> int:
    until = cache.get(_cooldown_key(email))
    if until is None:
        return 0
    now = int(time.time())
    remaining = int(until) - now
    return max(0, remaining)


def set_otp_cooldown(email: str):
    cooldown = _int_setting("OTP_EMAIL_COOLDOWN_SECONDS", 60)
    now = int(time.time())
    cache.set(_cooldown_key(email), now + cooldown, timeout=cooldown + 5)


def check_otp_ip_limit(ip: str) -> tuple[bool, int]:
    limit = _int_setting("OTP_IP_LIMIT_COUNT", 10)
    window = _int_setting("OTP_IP_LIMIT_WINDOW_SECONDS", 300)
    return _fixed_window_consume(
        key_prefix="otp:limit:ip",
        identifier=(ip or "unknown"),
        limit=limit,
        window_seconds=window,
    )


def check_otp_email_window_limit(email: str) -> tuple[bool, int]:
    limit = _int_setting("OTP_EMAIL_LIMIT_COUNT", 3)
    window = _int_setting("OTP_EMAIL_LIMIT_WINDOW_SECONDS", 600)
    return _fixed_window_consume(
        key_prefix="otp:limit:email",
        identifier=_hash_email(email),
        limit=limit,
        window_seconds=window,
    )


def check_otp_email_cooldown(email: str) -> tuple[bool, int]:
    remaining = get_otp_cooldown_remaining_seconds(email)
    if remaining > 0:
        return False, remaining
    return True, 0


def check_reject_admin_user_limit(admin_id: int) -> tuple[bool, int]:
    limit = _int_setting("REJECT_ADMIN_LIMIT_COUNT", 30)
    window = _int_setting("REJECT_ADMIN_LIMIT_WINDOW_SECONDS", 3600)
    return _fixed_window_consume(
        key_prefix="reject:limit:admin",
        identifier=str(admin_id),
        limit=limit,
        window_seconds=window,
    )


def check_reject_ip_limit(ip: str) -> tuple[bool, int]:
    limit = _int_setting("REJECT_IP_LIMIT_COUNT", 30)
    window = _int_setting("REJECT_IP_LIMIT_WINDOW_SECONDS", 3600)
    return _fixed_window_consume(
        key_prefix="reject:limit:ip",
        identifier=(ip or "unknown"),
        limit=limit,
        window_seconds=window,
    )


def log_rate_limit_violation(*, event: str, ip: str, email: Optional[str] = None, admin_id: Optional[int] = None, retry_after: int = 0, detail: str = ""):
    payload = {
        "event": event,
        "ip": ip or "unknown",
        "retry_after_seconds": retry_after,
        "detail": detail,
    }
    if email:
        payload["email_hash"] = _hash_email(email)
    if admin_id is not None:
        payload["admin_id"] = admin_id
    logger.warning("rate_limit_violation", extra=payload)

