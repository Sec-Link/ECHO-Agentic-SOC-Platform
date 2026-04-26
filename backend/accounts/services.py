from __future__ import annotations

import hashlib
import logging
import secrets
import time
from datetime import timedelta
from dataclasses import dataclass
from typing import Optional

from django.conf import settings
from django.contrib.auth.models import Group, Permission, User
from django.core.mail import send_mail, get_connection
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare

from .audit import AuditService
from .models import EmailOTP, RegistrationRequest, SystemSettings, UserAuthProfile

logger = logging.getLogger(__name__)

READONLY_GROUP_NAME = "readonly_user"
PANEL_APP_LABELS = [
    "es_integration",
    "dashboards",
    "tickets",
    "datasource",
    "integrations",
    "cmdb",
    "workflows",
    "correlation",
    "orchestrator",
    "ticket_policies",
]


def get_setting_bool(name: str, default: bool) -> bool:
    return bool(getattr(settings, name, default))


def get_setting_int(name: str, default: int) -> int:
    raw = getattr(settings, name, default)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def get_setting_list(name: str, default: list[str]) -> list[str]:
    raw = getattr(settings, name, default)
    if isinstance(raw, str):
        return [x.strip() for x in raw.split(",") if x.strip()]
    if isinstance(raw, (list, tuple)):
        return [str(x).strip() for x in raw if str(x).strip()]
    return list(default)


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def otp_secret() -> str:
    return getattr(settings, "OTP_HASH_SECRET", settings.SECRET_KEY)


def hash_otp(code: str) -> str:
    return hashlib.sha256(f"{otp_secret()}:{code}".encode("utf-8")).hexdigest()


def generate_otp_code(length: int = 6) -> str:
    min_v = 10 ** (length - 1)
    max_v = (10**length) - 1
    return str(secrets.randbelow(max_v - min_v + 1) + min_v)


def otp_expiry_minutes() -> int:
    return get_setting_int("OTP_EXPIRY_MINUTES", 10)


def otp_max_attempts() -> int:
    return get_setting_int("OTP_MAX_ATTEMPTS", 5)


def otp_resend_cooldown_seconds() -> int:
    return get_setting_int("OTP_RESEND_COOLDOWN_SECONDS", 60)


def admin_registration_emails() -> list[str]:
    return get_setting_list("ADMIN_REGISTRATION_EMAILS", [])


@dataclass
class OTPIssueResult:
    issued: bool
    reason: Optional[str] = None
    otp_id: Optional[str] = None


@dataclass
class RegistrationApprovalResult:
    user: User
    otp_result: OTPIssueResult


def otp_email_retry_attempts() -> int:
    return max(1, get_setting_int("OTP_EMAIL_RETRY_ATTEMPTS", 3))


def otp_email_retry_backoff_seconds() -> float:
    raw = getattr(settings, "OTP_EMAIL_RETRY_BACKOFF_SECONDS", 1)
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 1.0


def _smtp_config_failure_reason() -> Optional[str]:
    backend = str(getattr(settings, "EMAIL_BACKEND", "") or "")
    if backend != "django.core.mail.backends.smtp.EmailBackend":
        return None
    host = str(getattr(settings, "EMAIL_HOST", "") or "").strip()
    if not host:
        return "smtp_host_missing"
    if getattr(settings, "EMAIL_USE_TLS", False) and getattr(settings, "EMAIL_USE_SSL", False):
        return "smtp_tls_ssl_conflict"
    return None


def _send_mail_with_retry(*, subject: str, message: str, recipients: list[str], context: dict) -> bool:
    if not recipients:
        logger.warning("No recipients provided for email send", extra={"context": context})
        AuditService.log_safe(
            event_type="email_sent",
            status="failure",
            user_email=recipients[0] if recipients else None,
            failure_reason="no_recipients",
            metadata={**context, "recipient_count": len(recipients or [])},
        )
        return False

    smtp_config_reason = _smtp_config_failure_reason()
    if smtp_config_reason:
        logger.error(
            "Email send skipped due to SMTP configuration error",
            extra={
                "context": {
                    **context,
                    "failure_reason": smtp_config_reason,
                    "recipients": recipients,
                }
            },
        )
        AuditService.log_safe(
            event_type="email_sent",
            status="failure",
            user_email=recipients[0] if recipients else None,
            failure_reason=smtp_config_reason,
            metadata={
                **context,
                "recipient_count": len(recipients),
                "email_backend": str(getattr(settings, "EMAIL_BACKEND", "") or ""),
                "email_host_configured": bool(str(getattr(settings, "EMAIL_HOST", "") or "").strip()),
            },
        )
        return False

    attempts = otp_email_retry_attempts()
    backoff = otp_email_retry_backoff_seconds()
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            sent = send_mail(
                subject=subject,
                message=message,
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
                recipient_list=recipients,
                fail_silently=False,
            )
            # Some SMTP servers in dev env do not support STARTTLS but still accept plain SMTP.
            # If explicit TLS is enabled and primary send path fails, fallback to one-shot plain SMTP.
            if sent != 1 and getattr(settings, "EMAIL_USE_TLS", False):
                conn = get_connection(
                    fail_silently=False,
                    host=getattr(settings, "EMAIL_HOST", ""),
                    port=getattr(settings, "EMAIL_PORT", 587),
                    username=getattr(settings, "EMAIL_HOST_USER", ""),
                    password=getattr(settings, "EMAIL_HOST_PASSWORD", ""),
                    use_tls=False,
                    use_ssl=getattr(settings, "EMAIL_USE_SSL", False),
                    timeout=getattr(settings, "EMAIL_TIMEOUT", 15),
                )
                sent = send_mail(
                    subject=subject,
                    message=message,
                    from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
                    recipient_list=recipients,
                    fail_silently=False,
                    connection=conn,
                )
            if sent == 1:
                logger.info(
                    "Email sent successfully",
                    extra={"context": {**context, "attempt": attempt, "recipients": recipients}},
                )
                AuditService.log_safe(
                    event_type="email_sent",
                    status="success",
                    user_email=recipients[0] if recipients else None,
                    metadata={
                        **context,
                        "attempt": attempt,
                        "recipient_count": len(recipients),
                    },
                )
                return True
            logger.error(
                "Email send returned non-success count",
                extra={"context": {**context, "attempt": attempt, "sent_count": sent, "recipients": recipients}},
            )
            AuditService.log_safe(
                event_type="email_sent",
                status="failure",
                user_email=recipients[0] if recipients else None,
                failure_reason="unexpected_send_count",
                metadata={
                    **context,
                    "attempt": attempt,
                    "sent_count": sent,
                    "recipient_count": len(recipients),
                },
            )
        except Exception as exc:
            if getattr(settings, "EMAIL_USE_TLS", False):
                try:
                    logger.warning(
                        "Primary SMTP send failed; retrying without STARTTLS once",
                        extra={"context": {**context, "attempt": attempt, "error_type": exc.__class__.__name__}},
                    )
                    conn = get_connection(
                        fail_silently=False,
                        host=getattr(settings, "EMAIL_HOST", ""),
                        port=getattr(settings, "EMAIL_PORT", 587),
                        username=getattr(settings, "EMAIL_HOST_USER", ""),
                        password=getattr(settings, "EMAIL_HOST_PASSWORD", ""),
                        use_tls=False,
                        use_ssl=getattr(settings, "EMAIL_USE_SSL", False),
                        timeout=getattr(settings, "EMAIL_TIMEOUT", 15),
                    )
                    sent = send_mail(
                        subject=subject,
                        message=message,
                        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
                        recipient_list=recipients,
                        fail_silently=False,
                        connection=conn,
                    )
                    if sent == 1:
                        AuditService.log_safe(
                            event_type="email_sent",
                            status="success",
                            user_email=recipients[0] if recipients else None,
                            metadata={
                                **context,
                                "attempt": attempt,
                                "fallback": "smtp_without_starttls",
                                "recipient_count": len(recipients),
                            },
                        )
                        return True
                except Exception:
                    pass
            last_exc = exc
            logger.exception(
                "Email send failed",
                extra={"context": {**context, "attempt": attempt, "recipients": recipients}},
            )
            AuditService.log_safe(
                event_type="email_sent",
                status="failure",
                user_email=recipients[0] if recipients else None,
                failure_reason="provider_exception",
                metadata={
                    **context,
                    "attempt": attempt,
                    "error_type": exc.__class__.__name__,
                    "error_message": str(exc)[:300],
                    "recipient_count": len(recipients),
                },
            )
        if attempt < attempts and backoff > 0:
            time.sleep(backoff)
    if last_exc:
        logger.error("Email send failed after retries", extra={"context": {**context, "error": str(last_exc)}})
    return False


class NotificationService:
    @staticmethod
    def send_admin_registration_notice(*, email: str, request_id: str, requested_at):
        recipients = admin_registration_emails()
        if not recipients:
            logger.warning("ADMIN_REGISTRATION_EMAILS not configured; skipping admin registration email")
            return False
        subject = f"New registration request: {email}"
        message = (
            f"A new registration request was submitted.\n\n"
            f"Email: {email}\n"
            f"Request ID: {request_id}\n"
            f"Requested at: {requested_at.isoformat()}\n"
        )
        return _send_mail_with_retry(
            subject=subject,
            message=message,
            recipients=recipients,
            context={
                "kind": "registration_pending_admin",
                "email": email,
                "request_id": request_id,
                "email_type": "admin_notification",
            },
        )

    @staticmethod
    def send_user_otp(*, email: str, code: str, purpose: str):
        email = normalize_email(email)
        if not email:
            logger.error("Cannot send OTP email: empty recipient")
            return False
        minutes = otp_expiry_minutes()
        subject = "Your login code"
        if purpose == EmailOTP.Purpose.ACTIVATION:
            subject = "Your activation code"
        message = (
            f"Your one-time code is: {code}\n\n"
            f"It expires in {minutes} minutes.\n"
            f"Do not share this code with anyone.\n"
        )
        return _send_mail_with_retry(
            subject=subject,
            message=message,
            recipients=[email],
            context={"kind": "otp_login_user", "email": email, "purpose": purpose, "email_type": "otp"},
        )

    @staticmethod
    def send_registration_rejected(*, email: str, reason: str):
        email = normalize_email(email)
        if not email:
            logger.error("Cannot send rejection email: empty recipient")
            return False
        subject = "Registration request update"
        message = "Your registration request was rejected."
        if reason:
            message += f"\nReason: {reason}"
        return _send_mail_with_retry(
            subject=subject,
            message=message,
            recipients=[email],
            context={"kind": "registration_rejected_user", "email": email, "email_type": "rejection"},
        )

    @staticmethod
    def send_registration_approved(*, email: str, auto_approved: bool = False):
        email = normalize_email(email)
        if not email:
            logger.error("Cannot send approval email: empty recipient")
            return False
        subject = "Registration request approved"
        source = "automatically" if auto_approved else "by an administrator"
        message = (
            f"Your registration request has been approved {source}.\n\n"
            "You can now request an OTP code and sign in.\n"
        )
        return _send_mail_with_retry(
            subject=subject,
            message=message,
            recipients=[email],
            context={
                "kind": "registration_approved_user",
                "email": email,
                "email_type": "approval",
                "auto_approved": auto_approved,
            },
        )


class ReadonlyRoleService:
    @staticmethod
    @transaction.atomic
    def ensure_readonly_group() -> Group:
        group, _ = Group.objects.get_or_create(name=READONLY_GROUP_NAME)
        perms = Permission.objects.filter(
            content_type__app_label__in=PANEL_APP_LABELS,
            codename__startswith="view_",
        )
        group.permissions.set(perms)
        return group

    @staticmethod
    @transaction.atomic
    def assign_readonly(user: User):
        group = ReadonlyRoleService.ensure_readonly_group()
        user.groups.add(group)


class RegistrationService:
    @staticmethod
    def _auto_approver_user() -> User:
        user, created = User.objects.get_or_create(
            username="system_auto_approver",
            defaults={
                "email": "system-auto-approver@localhost",
                "is_active": False,
                "is_staff": True,
            },
        )
        if created:
            user.set_unusable_password()
            user.save(update_fields=["password"])
        return user

    @staticmethod
    def _ensure_otp_readonly_user(email: str) -> User:
        user = User.objects.filter(email__iexact=email).first()
        if user is None:
            user = User.objects.create(
                username=email,
                email=email,
                is_active=True,
            )
            user.set_unusable_password()
            user.save(update_fields=["password"])
        else:
            if not user.username:
                user.username = email
            user.email = email
            user.is_active = True
            user.save(update_fields=["username", "email", "is_active"])

        profile, _ = UserAuthProfile.objects.get_or_create(user=user)
        profile.auth_method = UserAuthProfile.AuthMethod.OTP_ONLY
        profile.is_readonly = True
        profile.save(update_fields=["auth_method", "is_readonly", "updated_at"])
        ReadonlyRoleService.assign_readonly(user)
        return user

    @staticmethod
    @transaction.atomic
    def submit(email: str) -> RegistrationRequest:
        normalized = normalize_email(email)
        if not normalized:
            raise ValueError("Email is required")

        if SystemSettings.is_auto_approve_enabled():
            approver = RegistrationService._auto_approver_user()
            user = RegistrationService._ensure_otp_readonly_user(normalized)
            existing_req = RegistrationRequest.objects.filter(email=normalized).order_by("-requested_at").first()
            if existing_req is None:
                req = RegistrationRequest.objects.create(
                    email=normalized,
                    status=RegistrationRequest.Status.APPROVED,
                    reviewed_at=timezone.now(),
                    reviewed_by=approver,
                    review_reason="auto-approved",
                    approved_user=user,
                )
            else:
                existing_req.status = RegistrationRequest.Status.APPROVED
                existing_req.reviewed_at = timezone.now()
                existing_req.reviewed_by = approver
                existing_req.review_reason = "auto-approved"
                existing_req.approved_user = user
                existing_req.save(
                    update_fields=["status", "reviewed_at", "reviewed_by", "review_reason", "approved_user"]
                )
                req = existing_req
            NotificationService.send_registration_approved(email=normalized, auto_approved=True)
            return req

        existing_active = User.objects.filter(email__iexact=normalized, is_active=True).first()
        if existing_active:
            req = RegistrationRequest.objects.filter(email=normalized).order_by("-requested_at").first()
            if req:
                return req
            req = RegistrationRequest.objects.create(
                email=normalized,
                status=RegistrationRequest.Status.APPROVED,
                approved_user=existing_active,
                reviewed_at=timezone.now(),
            )
            return req

        req, created = RegistrationRequest.objects.get_or_create(
            email=normalized,
            status=RegistrationRequest.Status.PENDING,
            defaults={},
        )
        if created:
            NotificationService.send_admin_registration_notice(
                email=normalized, request_id=str(req.id), requested_at=req.requested_at
            )
        return req

    @staticmethod
    @transaction.atomic
    def approve(
        request_obj: RegistrationRequest, admin_user: User, note: str | None = None
    ) -> RegistrationApprovalResult:
        if request_obj.status != RegistrationRequest.Status.PENDING:
            raise ValueError("Registration request is not pending")

        email = request_obj.email
        user = RegistrationService._ensure_otp_readonly_user(email)

        request_obj.status = RegistrationRequest.Status.APPROVED
        request_obj.reviewed_at = timezone.now()
        request_obj.reviewed_by = admin_user
        request_obj.review_reason = (note or "").strip() or None
        request_obj.approved_user = user
        request_obj.save(
            update_fields=["status", "reviewed_at", "reviewed_by", "review_reason", "approved_user"]
        )

        otp_result = OtpService.issue(
            user=user,
            purpose=EmailOTP.Purpose.ACTIVATION,
            sent_to_email=email,
            request_ip=None,
            user_agent="",
            bypass_cooldown=True,
        )
        NotificationService.send_registration_approved(email=email, auto_approved=False)
        return RegistrationApprovalResult(user=user, otp_result=otp_result)

    @staticmethod
    @transaction.atomic
    def reject(request_obj: RegistrationRequest, admin_user: User, reason: str | None = None):
        if request_obj.status != RegistrationRequest.Status.PENDING:
            raise ValueError("Registration request is not pending")

        request_obj.status = RegistrationRequest.Status.REJECTED
        request_obj.reviewed_at = timezone.now()
        request_obj.reviewed_by = admin_user
        request_obj.review_reason = (reason or "").strip() or None
        request_obj.save(update_fields=["status", "reviewed_at", "reviewed_by", "review_reason"])

        NotificationService.send_registration_rejected(
            email=request_obj.email,
            reason=request_obj.review_reason or "",
        )


class OtpService:
    @staticmethod
    @transaction.atomic
    def issue(
        *,
        user: User,
        purpose: str,
        sent_to_email: str,
        request_ip: str | None,
        user_agent: str,
        bypass_cooldown: bool = False,
    ) -> OTPIssueResult:
        now = timezone.now()
        email = normalize_email(sent_to_email or user.email)
        if not email:
            logger.warning("OTP issue skipped: missing user email", extra={"user_id": user.id, "purpose": purpose})
            return OTPIssueResult(issued=False, reason="email_missing")

        cooldown = otp_resend_cooldown_seconds()
        recent = (
            EmailOTP.objects.filter(user=user, purpose=purpose, used_at__isnull=True)
            .order_by("-created_at")
            .first()
        )
        if (
            recent
            and not bypass_cooldown
            and (now - recent.created_at).total_seconds() < cooldown
            and recent.is_usable()
        ):
            logger.info(
                "OTP issue skipped by cooldown",
                extra={"user_id": user.id, "purpose": purpose, "cooldown_seconds": cooldown},
            )
            return OTPIssueResult(issued=False, reason="cooldown")

        EmailOTP.objects.filter(user=user, purpose=purpose, used_at__isnull=True).update(used_at=now)

        code = generate_otp_code()
        expires = now + timedelta(minutes=otp_expiry_minutes())
        otp_obj = EmailOTP.objects.create(
            user=user,
            purpose=purpose,
            otp_hash=hash_otp(code),
            expires_at=expires,
            max_attempts=otp_max_attempts(),
            sent_to_email=email,
            request_ip=request_ip,
            user_agent=(user_agent or "")[:255],
        )

        sent = NotificationService.send_user_otp(email=email, code=code, purpose=purpose)
        if not sent:
            # Keep failed code unusable so next request can generate another OTP.
            otp_obj.used_at = now
            otp_obj.save(update_fields=["used_at"])
            logger.error(
                "OTP email send failed; OTP invalidated",
                extra={"user_id": user.id, "otp_id": str(otp_obj.id), "purpose": purpose, "email": email},
            )
            return OTPIssueResult(issued=False, reason="email_send_failed", otp_id=str(otp_obj.id))

        logger.info(
            "OTP issued and email sent",
            extra={"user_id": user.id, "otp_id": str(otp_obj.id), "purpose": purpose, "email": email},
        )
        return OTPIssueResult(issued=True, otp_id=str(otp_obj.id))

    @staticmethod
    @transaction.atomic
    def verify(*, user: User, code: str, purpose: str = EmailOTP.Purpose.LOGIN) -> bool:
        ok, _ = OtpService.verify_with_reason(user=user, code=code, purpose=purpose)
        return ok

    @staticmethod
    @transaction.atomic
    def verify_with_reason(*, user: User, code: str, purpose: str = EmailOTP.Purpose.LOGIN) -> tuple[bool, str]:
        now = timezone.now()
        otp = (
            EmailOTP.objects.select_for_update()
            .filter(
                user=user,
                purpose=purpose,
                used_at__isnull=True,
            )
            .order_by("-created_at")
            .first()
        )
        if otp is None:
            return False, "no_active_otp"
        if otp.is_expired() or otp.attempt_count >= otp.max_attempts:
            if otp.used_at is None:
                otp.used_at = now
                otp.save(update_fields=["used_at"])
            if otp.attempt_count >= otp.max_attempts:
                return False, "max_attempts"
            return False, "expired_code"

        expected = otp_hash = otp.otp_hash
        provided = hash_otp((code or "").strip())
        if not constant_time_compare(provided, expected):
            otp.attempt_count += 1
            updates = ["attempt_count"]
            if otp.attempt_count >= otp.max_attempts:
                otp.used_at = now
                updates.append("used_at")
            otp.save(update_fields=updates)
            if otp.attempt_count >= otp.max_attempts:
                return False, "max_attempts"
            return False, "invalid_code"

        otp.used_at = now
        otp.save(update_fields=["used_at"])
        return True, "ok"


def should_deny_write_for_readonly_user(user: User, path: str, method: str) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if method in {"GET", "HEAD", "OPTIONS"}:
        return False
    if getattr(user, "is_superuser", False) or getattr(user, "is_staff", False):
        return False

    profile = UserAuthProfile.objects.filter(user=user).first()
    if not profile or not profile.is_readonly:
        return False

    # Allow OTP authentication endpoints for readonly users.
    allowed = {
        "/api/v1/auth/otp/request/",
        "/api/v1/auth/otp/request",
        "/api/v1/auth/otp/verify/",
        "/api/v1/auth/otp/verify",
        "/api/v1/auth/logout/",
        "/api/v1/auth/logout",
    }
    return path not in allowed
