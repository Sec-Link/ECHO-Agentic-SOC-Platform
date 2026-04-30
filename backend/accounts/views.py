from __future__ import annotations

from datetime import datetime, time
import logging

from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.db.models import Q
from django.utils import timezone

from django.utils.dateparse import parse_datetime, parse_date
from rest_framework import permissions, status
from rest_framework.authentication import TokenAuthentication
from rest_framework.authtoken.models import Token
from rest_framework.response import Response
from rest_framework.views import APIView
from django.conf import settings

from .audit import AuditService
from .models import AuditLog, EmailOTP, RegistrationRequest, SystemSettings, UserAuthProfile
from .serializers import (
    AuditLogListSerializer,
    EmailRegistrationSerializer,
    LoginSerializer,
    OTPRequestSerializer,
    OTPVerifySerializer,
    PasswordChangeSerializer,
    RegisterSerializer,
    RegistrationApproveSerializer,
    RegistrationRejectSerializer,
    RegistrationRequestListSerializer,
    SystemSettingsSerializer,
    UserSerializer,
)
from .services import OtpService, RegistrationService
from .rate_limit import (
    check_otp_email_cooldown,
    check_otp_email_window_limit,
    check_otp_ip_limit,
    check_reject_admin_user_limit,
    check_reject_ip_limit,
    log_rate_limit_violation,
    set_otp_cooldown,
)

logger = logging.getLogger(__name__)


def _request_ip(request) -> str:
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "") or ""


class RegisterAPIView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        token, _ = Token.objects.get_or_create(user=user)
        return Response({"token": token.key, "user": UserSerializer(user).data}, status=status.HTTP_201_CREATED)


class LoginAPIView(APIView):
    permission_classes = [permissions.AllowAny]

    @staticmethod
    def _safe_profile(user: User):
        return UserAuthProfile.objects.filter(user=user).first()

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = authenticate(
            username=serializer.validated_data["username"],
            password=serializer.validated_data["password"],
        )
        if user is None:
            return Response({"error": "Invalid password or username"}, status=status.HTTP_401_UNAUTHORIZED)
        profile = self._safe_profile(user)
        if profile and profile.auth_method == UserAuthProfile.AuthMethod.OTP_ONLY:
            return Response({"error": "This account requires OTP login."}, status=status.HTTP_403_FORBIDDEN)
        token, _ = Token.objects.get_or_create(user=user)
        return Response({"token": token.key, "user": UserSerializer(user).data}, status=status.HTTP_200_OK)


class RegisterEmailAPIView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        if not getattr(settings, "OTP_AUTH_ENABLED", True):
            AuditService.log_from_request(
                request,
                event_type=AuditLog.EventType.REGISTRATION,
                status=AuditLog.Status.FAILURE,
                failure_reason="otp_auth_disabled",
                metadata={"path": request.path},
            )
            return Response({"detail": "OTP auth is disabled."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        serializer = EmailRegistrationSerializer(data=request.data)
        if not serializer.is_valid():
            candidate_email = (request.data or {}).get("email")
            AuditService.log_from_request(
                request,
                event_type=AuditLog.EventType.REGISTRATION,
                status=AuditLog.Status.FAILURE,
                user_email=str(candidate_email or "")[:254],
                failure_reason="invalid_email",
                metadata={"errors": serializer.errors},
            )
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        email = serializer.validated_data["email"]
        req = RegistrationService.submit(email)
        state = "active" if req.status == RegistrationRequest.Status.APPROVED else "pending"
        AuditService.log_from_request(
            request,
            event_type=AuditLog.EventType.REGISTRATION,
            status=AuditLog.Status.SUCCESS,
            user_email=email,
            metadata={"state": state},
        )
        return Response(
            {"status": state, "message": "Registration submitted"},
            status=status.HTTP_202_ACCEPTED,
        )


class OTPRequestAPIView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_scope = "otp_request"

    @staticmethod
    def _safe_profile(user: User):
        return UserAuthProfile.objects.filter(user=user).first()

    def post(self, request):
        if not getattr(settings, "OTP_AUTH_ENABLED", True):
            AuditService.log_from_request(
                request,
                event_type=AuditLog.EventType.OTP_REQUEST,
                status=AuditLog.Status.FAILURE,
                failure_reason="otp_auth_disabled",
                metadata={"path": request.path},
            )
            return Response({"detail": "OTP auth is disabled."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        serializer = OTPRequestSerializer(data=request.data)
        if not serializer.is_valid():
            candidate_email = (request.data or {}).get("email")
            AuditService.log_from_request(
                request,
                event_type=AuditLog.EventType.OTP_REQUEST,
                status=AuditLog.Status.FAILURE,
                user_email=str(candidate_email or "")[:254],
                failure_reason="invalid_email",
                metadata={"errors": serializer.errors},
            )
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        email = serializer.validated_data["email"]
        client_ip = _request_ip(request)

        user = User.objects.filter(email__iexact=email, is_active=True).first()
        eligible = False
        profile = None
        if user is not None:
            profile = self._safe_profile(user)
            if profile is None:
                profile = UserAuthProfile.objects.create(user=user, auth_method=UserAuthProfile.AuthMethod.PASSWORD)

            eligible = profile.auth_method == UserAuthProfile.AuthMethod.OTP_ONLY

        if not eligible:
            if user is not None and profile is not None:
                logger.info(
                    "OTP request skipped: user not eligible",
                    extra={"user_id": user.id, "email": email, "auth_method": profile.auth_method},
                )
            else:
                logger.info("OTP request skipped: no active user", extra={"email": email})
            AuditService.log_from_request(
                request,
                event_type=AuditLog.EventType.OTP_REQUEST,
                status=AuditLog.Status.FAILURE,
                user_email=email,
                failure_reason="ineligible_email",
                metadata={"auth_method": getattr(profile, "auth_method", None)},
            )
            return Response(
                {"message": "If this account is eligible, an OTP has been sent"},
                status=status.HTTP_200_OK,
            )

        ip_allowed, ip_retry = check_otp_ip_limit(client_ip)
        if not ip_allowed:
            log_rate_limit_violation(
                event="otp_rate_limited_ip",
                ip=client_ip,
                email=email,
                retry_after=ip_retry,
                detail="otp_ip_limit",
            )
            AuditService.log_from_request(
                request,
                event_type=AuditLog.EventType.OTP_REQUEST,
                status=AuditLog.Status.FAILURE,
                user_email=email,
                failure_reason="rate_limited_ip",
                metadata={"retry_after_seconds": ip_retry},
            )
            return Response(
                {
                    "error": "rate_limited",
                    "message": "Too many OTP requests. Please try again later.",
                    "retry_after_seconds": ip_retry,
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        cooldown_allowed, cooldown_retry = check_otp_email_cooldown(email)
        if not cooldown_allowed:
            log_rate_limit_violation(
                event="otp_rate_limited_email_cooldown",
                ip=client_ip,
                email=email,
                retry_after=cooldown_retry,
                detail="otp_email_cooldown",
            )
            AuditService.log_from_request(
                request,
                event_type=AuditLog.EventType.OTP_REQUEST,
                status=AuditLog.Status.FAILURE,
                user_email=email,
                failure_reason="rate_limited_email_cooldown",
                metadata={"retry_after_seconds": cooldown_retry},
            )
            return Response(
                {
                    "error": "rate_limited",
                    "message": "Too many OTP requests. Please try again later.",
                    "retry_after_seconds": cooldown_retry,
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        email_allowed, email_retry = check_otp_email_window_limit(email)
        if not email_allowed:
            log_rate_limit_violation(
                event="otp_rate_limited_email_window",
                ip=client_ip,
                email=email,
                retry_after=email_retry,
                detail="otp_email_window_limit",
            )
            AuditService.log_from_request(
                request,
                event_type=AuditLog.EventType.OTP_REQUEST,
                status=AuditLog.Status.FAILURE,
                user_email=email,
                failure_reason="rate_limited_email_window",
                metadata={"retry_after_seconds": email_retry},
            )
            return Response(
                {
                    "error": "rate_limited",
                    "message": "Too many OTP requests. Please try again later.",
                    "retry_after_seconds": email_retry,
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        result = OtpService.issue(
            user=user,
            purpose=EmailOTP.Purpose.LOGIN,
            sent_to_email=email,
            request_ip=client_ip,
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
        )
        if not result.issued:
            logger.warning(
                "OTP request failed to send",
                extra={
                    "user_id": user.id,
                    "email": email,
                    "reason": result.reason,
                    "otp_id": result.otp_id,
                },
            )
            AuditService.log_from_request(
                request,
                event_type=AuditLog.EventType.OTP_REQUEST,
                status=AuditLog.Status.FAILURE,
                user_email=email,
                failure_reason=result.reason or "otp_send_failed",
                metadata={"otp_id": result.otp_id, "purpose": EmailOTP.Purpose.LOGIN},
            )
            return Response(
                {"error": "otp_send_failed", "message": "Unable to send OTP now. Please try again later."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        set_otp_cooldown(email)

        if user is None:
            logger.info("OTP request skipped: no active user", extra={"email": email})

        AuditService.log_from_request(
            request,
            event_type=AuditLog.EventType.OTP_REQUEST,
            status=AuditLog.Status.SUCCESS,
            user_email=email,
            metadata={"otp_id": result.otp_id, "purpose": EmailOTP.Purpose.LOGIN},
        )
        return Response(
            {"message": "If this account is eligible, an OTP has been sent"},
            status=status.HTTP_200_OK,
        )


class GuestEmailStatusAPIView(APIView):
    permission_classes = [permissions.AllowAny]

    @staticmethod
    def _safe_profile(user: User):
        return UserAuthProfile.objects.filter(user=user).first()

    def post(self, request):
        serializer = OTPRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]
        user = User.objects.filter(email__iexact=email, is_active=True).first()
        if user is None:
            return Response(
                {
                    "email": email,
                    "is_registered_readonly": False,
                    "next_action": "register",
                },
                status=status.HTTP_200_OK,
            )

        profile = self._safe_profile(user)
        is_registered_readonly = bool(
            profile and profile.auth_method == UserAuthProfile.AuthMethod.OTP_ONLY and profile.is_readonly
        )
        return Response(
            {
                "email": email,
                "is_registered_readonly": is_registered_readonly,
                "next_action": "send_otp" if is_registered_readonly else "register",
            },
            status=status.HTTP_200_OK,
        )


class OTPVerifyAPIView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_scope = "otp_verify"

    def post(self, request):
        if not getattr(settings, "OTP_AUTH_ENABLED", True):
            AuditService.log_from_request(
                request,
                event_type=AuditLog.EventType.OTP_VERIFY,
                status=AuditLog.Status.FAILURE,
                failure_reason="otp_auth_disabled",
                metadata={"path": request.path},
            )
            return Response({"detail": "OTP auth is disabled."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        serializer = OTPVerifySerializer(data=request.data)
        if not serializer.is_valid():
            candidate_email = (request.data or {}).get("email")
            AuditService.log_from_request(
                request,
                event_type=AuditLog.EventType.OTP_VERIFY,
                status=AuditLog.Status.FAILURE,
                user_email=str(candidate_email or "")[:254],
                failure_reason="invalid_payload",
                metadata={"errors": serializer.errors},
            )
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        email = serializer.validated_data["email"]
        code = serializer.validated_data["otp"]

        user = User.objects.filter(email__iexact=email, is_active=True).first()
        if user is None:
            AuditService.log_from_request(
                request,
                event_type=AuditLog.EventType.OTP_VERIFY,
                status=AuditLog.Status.FAILURE,
                user_email=email,
                failure_reason="user_not_found_or_inactive",
            )
            return Response({"error": "Invalid email or OTP"}, status=status.HTTP_400_BAD_REQUEST)

        profile, _ = UserAuthProfile.objects.get_or_create(user=user)
        if profile.auth_method != UserAuthProfile.AuthMethod.OTP_ONLY:
            AuditService.log_from_request(
                request,
                event_type=AuditLog.EventType.OTP_VERIFY,
                status=AuditLog.Status.FAILURE,
                user_email=email,
                failure_reason="auth_method_not_otp_only",
            )
            return Response({"error": "Invalid email or OTP"}, status=status.HTTP_400_BAD_REQUEST)

        ok, verify_reason = OtpService.verify_with_reason(user=user, code=code, purpose=EmailOTP.Purpose.LOGIN)
        if not ok:
            activation_ok, activation_reason = OtpService.verify_with_reason(
                user=user,
                code=code,
                purpose=EmailOTP.Purpose.ACTIVATION,
            )
            if not activation_ok:
                AuditService.log_from_request(
                    request,
                    event_type=AuditLog.EventType.OTP_VERIFY,
                    status=AuditLog.Status.FAILURE,
                    user_email=email,
                    failure_reason=verify_reason or activation_reason or "invalid_code",
                    metadata={"login_reason": verify_reason, "activation_reason": activation_reason},
                )
                return Response({"error": "Invalid email or OTP"}, status=status.HTTP_400_BAD_REQUEST)

        if profile.email_verified_at is None:
            profile.email_verified_at = timezone.now()
        profile.last_login_method = UserAuthProfile.LoginMethod.OTP
        profile.save(update_fields=["email_verified_at", "last_login_method", "updated_at"])

        token, _ = Token.objects.get_or_create(user=user)
        AuditService.log_from_request(
            request,
            event_type=AuditLog.EventType.OTP_VERIFY,
            status=AuditLog.Status.SUCCESS,
            user_email=email,
            metadata={"last_login_method": profile.last_login_method},
        )
        return Response({"token": token.key, "user": UserSerializer(user).data}, status=status.HTTP_200_OK)


class LogoutAPIView(APIView):
    authentication_classes = [TokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        Token.objects.filter(user=request.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class PasswordChangeAPIView(APIView):
    authentication_classes = [TokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    @staticmethod
    def _safe_profile(user: User):
        return UserAuthProfile.objects.filter(user=user).first()

    def post(self, request):
        profile = self._safe_profile(request.user)
        if profile and profile.auth_method == UserAuthProfile.AuthMethod.OTP_ONLY:
            return Response({"detail": "Password login is disabled for this account."}, status=status.HTTP_403_FORBIDDEN)
        serializer = PasswordChangeSerializer(data=request.data, context={"request": request})
        if serializer.is_valid():
            serializer.save()
            return Response({"message": "Password changed successfully."}, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class RegistrationRequestListAPIView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        qs = RegistrationRequest.objects.select_related("reviewed_by", "approved_user").all().order_by("-requested_at")
        status_param = request.query_params.get("status")
        email_param = request.query_params.get("email")
        if status_param:
            qs = qs.filter(status=status_param)
        if email_param:
            qs = qs.filter(email__icontains=(email_param or "").strip().lower())
        data = RegistrationRequestListSerializer(qs[:200], many=True).data
        return Response({"count": qs.count(), "results": data})


class RegistrationApproveAPIView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def post(self, request, request_id):
        serializer = RegistrationApproveSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        req = RegistrationRequest.objects.filter(id=request_id).first()
        if req is None:
            AuditService.log_from_request(
                request,
                event_type=AuditLog.EventType.ADMIN_APPROVE,
                status=AuditLog.Status.FAILURE,
                admin_email=request.user.email or request.user.username,
                failure_reason="request_not_found",
                metadata={"request_id": str(request_id)},
            )
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        try:
            result = RegistrationService.approve(req, request.user, serializer.validated_data.get("note"))
        except ValueError as exc:
            AuditService.log_from_request(
                request,
                event_type=AuditLog.EventType.ADMIN_APPROVE,
                status=AuditLog.Status.FAILURE,
                user_email=req.email,
                admin_email=request.user.email or request.user.username,
                failure_reason="invalid_state",
                metadata={"detail": str(exc), "request_id": str(request_id)},
            )
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        payload = {
            "status": "approved",
            "user_id": result.user.id,
            "otp_sent": result.otp_result.issued,
        }
        if not result.otp_result.issued:
            payload["otp_reason"] = result.otp_result.reason
            payload["otp_id"] = result.otp_result.otp_id
            logger.error(
                "Approval completed but activation OTP email was not sent",
                extra={
                    "request_id": str(request_id),
                    "user_id": result.user.id,
                    "reason": result.otp_result.reason,
                    "otp_id": result.otp_result.otp_id,
                },
            )
            AuditService.log_from_request(
                request,
                event_type=AuditLog.EventType.ADMIN_APPROVE,
                status=AuditLog.Status.FAILURE,
                user_email=req.email,
                admin_email=request.user.email or request.user.username,
                failure_reason=result.otp_result.reason or "otp_send_failed",
                metadata={"request_id": str(request_id), "otp_id": result.otp_result.otp_id},
            )
        else:
            AuditService.log_from_request(
                request,
                event_type=AuditLog.EventType.ADMIN_APPROVE,
                status=AuditLog.Status.SUCCESS,
                user_email=req.email,
                admin_email=request.user.email or request.user.username,
                metadata={"request_id": str(request_id), "user_id": result.user.id},
            )
        return Response(payload)


class RegistrationRejectAPIView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def post(self, request, request_id):
        client_ip = _request_ip(request)

        admin_allowed, admin_retry = check_reject_admin_user_limit(request.user.id)
        if not admin_allowed:
            log_rate_limit_violation(
                event="reject_rate_limited_admin",
                ip=client_ip,
                admin_id=request.user.id,
                retry_after=admin_retry,
                detail="reject_admin_limit",
            )
            AuditService.log_from_request(
                request,
                event_type=AuditLog.EventType.ADMIN_REJECT,
                status=AuditLog.Status.FAILURE,
                admin_email=request.user.email or request.user.username,
                failure_reason="rate_limited_admin",
                metadata={"retry_after_seconds": admin_retry, "request_id": str(request_id)},
            )
            return Response(
                {
                    "error": "rate_limited",
                    "message": "Too many reject operations. Please try again later.",
                    "retry_after_seconds": admin_retry,
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        ip_allowed, ip_retry = check_reject_ip_limit(client_ip)
        if not ip_allowed:
            log_rate_limit_violation(
                event="reject_rate_limited_ip",
                ip=client_ip,
                admin_id=request.user.id,
                retry_after=ip_retry,
                detail="reject_ip_limit",
            )
            AuditService.log_from_request(
                request,
                event_type=AuditLog.EventType.ADMIN_REJECT,
                status=AuditLog.Status.FAILURE,
                admin_email=request.user.email or request.user.username,
                failure_reason="rate_limited_ip",
                metadata={"retry_after_seconds": ip_retry, "request_id": str(request_id)},
            )
            return Response(
                {
                    "error": "rate_limited",
                    "message": "Too many reject operations. Please try again later.",
                    "retry_after_seconds": ip_retry,
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        serializer = RegistrationRejectSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        req = RegistrationRequest.objects.filter(id=request_id).first()
        if req is None:
            AuditService.log_from_request(
                request,
                event_type=AuditLog.EventType.ADMIN_REJECT,
                status=AuditLog.Status.FAILURE,
                admin_email=request.user.email or request.user.username,
                failure_reason="request_not_found",
                metadata={"request_id": str(request_id)},
            )
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        try:
            RegistrationService.reject(req, request.user, serializer.validated_data.get("reason"))
        except ValueError:
            AuditService.log_from_request(
                request,
                event_type=AuditLog.EventType.ADMIN_REJECT,
                status=AuditLog.Status.FAILURE,
                user_email=req.email,
                admin_email=request.user.email or request.user.username,
                failure_reason="invalid_state",
                metadata={"request_id": str(request_id)},
            )
            return Response({"error": "invalid_state", "message": "Request already processed."}, status=status.HTTP_400_BAD_REQUEST)
        AuditService.log_from_request(
            request,
            event_type=AuditLog.EventType.ADMIN_REJECT,
            status=AuditLog.Status.SUCCESS,
            user_email=req.email,
            admin_email=request.user.email or request.user.username,
            metadata={
                "request_id": str(request_id),
                "reason": serializer.validated_data.get("reason") or "",
            },
        )
        return Response({"status": "rejected"})


class AuditLogListAPIView(APIView):
    permission_classes = [permissions.IsAdminUser]
    throttle_scope = "audit_logs"

    def get(self, request):
        qs = AuditLog.objects.all().order_by("-created_at")

        event_type = (request.query_params.get("event_type") or "").strip()
        email = (request.query_params.get("email") or "").strip().lower()
        status_param = (request.query_params.get("status") or "").strip()
        from_date = (request.query_params.get("from_date") or "").strip()
        to_date = (request.query_params.get("to_date") or "").strip()
        sort = (request.query_params.get("sort") or "-created_at").strip()

        if event_type:
            qs = qs.filter(event_type=event_type)
        if status_param:
            qs = qs.filter(status=status_param)
        if email:
            qs = qs.filter(Q(user_email__icontains=email) | Q(admin_email__icontains=email))

        from_dt = parse_datetime(from_date) if from_date else None
        if from_dt and timezone.is_naive(from_dt):
            from_dt = timezone.make_aware(from_dt)
        if from_dt is None and from_date:
            from_d = parse_date(from_date)
            if from_d:
                from_dt = timezone.make_aware(datetime.combine(from_d, time.min))
        if from_dt:
            qs = qs.filter(created_at__gte=from_dt)

        to_dt = parse_datetime(to_date) if to_date else None
        if to_dt and timezone.is_naive(to_dt):
            to_dt = timezone.make_aware(to_dt)
        if to_dt is None and to_date:
            to_d = parse_date(to_date)
            if to_d:
                to_dt = timezone.make_aware(datetime.combine(to_d, time.max))
        if to_dt:
            qs = qs.filter(created_at__lte=to_dt)

        allowed_sort = {"created_at", "-created_at"}
        if sort not in allowed_sort:
            sort = "-created_at"
        qs = qs.order_by(sort)

        try:
            page = max(1, int(request.query_params.get("page", 1)))
        except ValueError:
            page = 1
        try:
            limit = int(request.query_params.get("limit", 20))
        except ValueError:
            limit = 20
        limit = max(1, min(100, limit))

        total = qs.count()
        start = (page - 1) * limit
        end = start + limit
        rows = qs[start:end]

        data = AuditLogListSerializer(rows, many=True).data
        return Response(
            {
                "count": total,
                "page": page,
                "limit": limit,
                "results": data,
            }
        )


class SystemSettingsAPIView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        row = SystemSettings.get_solo()
        return Response(SystemSettingsSerializer(row).data)

    def put(self, request):
        row = SystemSettings.get_solo()
        serializer = SystemSettingsSerializer(instance=row, data=request.data or {}, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)
