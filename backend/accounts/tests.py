from __future__ import annotations

from unittest.mock import patch
from django.core.cache import cache

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import AuditLog, RegistrationRequest, SystemSettings, UserAuthProfile


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    ADMIN_REGISTRATION_EMAILS=["admin@example.com"],
)
class OTPRegistrationFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.admin = User.objects.create_superuser("admin", "admin@example.com", "Password123!")

    def test_register_email_creates_pending_request(self):
        resp = self.client.post("/api/v1/auth/register-email/", {"email": "new.user@example.com"}, format="json")
        self.assertEqual(resp.status_code, 202)
        req = RegistrationRequest.objects.get(email="new.user@example.com")
        self.assertEqual(req.status, RegistrationRequest.Status.PENDING)
        self.assertGreaterEqual(len(mail.outbox), 1)
        self.assertTrue(
            AuditLog.objects.filter(
                event_type=AuditLog.EventType.REGISTRATION,
                user_email="new.user@example.com",
                status=AuditLog.Status.SUCCESS,
            ).exists()
        )

    def test_admin_approve_creates_readonly_otp_user(self):
        self.client.post("/api/v1/auth/register-email/", {"email": "approved@example.com"}, format="json")
        req = RegistrationRequest.objects.get(email="approved@example.com")

        admin_client = APIClient()
        admin_login = admin_client.post(
            "/api/v1/auth/login/",
            {"username": "admin", "password": "Password123!"},
            format="json",
        )
        self.assertEqual(admin_login.status_code, 200)
        admin_client.credentials(HTTP_AUTHORIZATION=f"Token {admin_login.data['token']}")

        resp = admin_client.post(f"/api/v1/accounts/registration-requests/{req.id}/approve/", {"note": "ok"}, format="json")
        self.assertEqual(resp.status_code, 200)
        user = User.objects.get(email="approved@example.com")
        profile = UserAuthProfile.objects.get(user=user)
        self.assertEqual(profile.auth_method, UserAuthProfile.AuthMethod.OTP_ONLY)
        self.assertTrue(profile.is_readonly)
        self.assertTrue(
            AuditLog.objects.filter(
                event_type=AuditLog.EventType.ADMIN_APPROVE,
                user_email="approved@example.com",
                status=AuditLog.Status.SUCCESS,
            ).exists()
        )

    def test_otp_login_for_approved_user(self):
        self.client.post("/api/v1/auth/register-email/", {"email": "otp.user@example.com"}, format="json")
        req = RegistrationRequest.objects.get(email="otp.user@example.com")

        admin_client = APIClient()
        admin_login = admin_client.post(
            "/api/v1/auth/login/",
            {"username": "admin", "password": "Password123!"},
            format="json",
        )
        admin_client.credentials(HTTP_AUTHORIZATION=f"Token {admin_login.data['token']}")
        approve_resp = admin_client.post(f"/api/v1/accounts/registration-requests/{req.id}/approve/", {}, format="json")
        self.assertEqual(approve_resp.status_code, 200)

        request_resp = self.client.post("/api/v1/auth/otp/request/", {"email": "otp.user@example.com"}, format="json")
        self.assertEqual(request_resp.status_code, 200)
        self.assertGreaterEqual(len(mail.outbox), 1)
        otp_mail = mail.outbox[-1]
        self.assertIn("code is:", otp_mail.body)
        code = otp_mail.body.split("code is:")[1].splitlines()[0].strip()

        verify_resp = self.client.post(
            "/api/v1/auth/otp/verify/",
            {"email": "otp.user@example.com", "otp": code},
            format="json",
        )
        self.assertEqual(verify_resp.status_code, 200)
        self.assertIn("token", verify_resp.data)
        self.assertTrue(verify_resp.data["user"]["is_readonly"])
        self.assertTrue(
            AuditLog.objects.filter(
                event_type=AuditLog.EventType.OTP_REQUEST,
                user_email="otp.user@example.com",
                status=AuditLog.Status.SUCCESS,
            ).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(
                event_type=AuditLog.EventType.OTP_VERIFY,
                user_email="otp.user@example.com",
                status=AuditLog.Status.SUCCESS,
            ).exists()
        )

    def test_admin_audit_log_list_endpoint(self):
        self.client.post("/api/v1/auth/register-email/", {"email": "log.list@example.com"}, format="json")
        req = RegistrationRequest.objects.get(email="log.list@example.com")

        admin_client = APIClient()
        admin_login = admin_client.post(
            "/api/v1/auth/login/",
            {"username": "admin", "password": "Password123!"},
            format="json",
        )
        self.assertEqual(admin_login.status_code, 200)
        admin_client.credentials(HTTP_AUTHORIZATION=f"Token {admin_login.data['token']}")
        admin_client.post(f"/api/v1/accounts/registration-requests/{req.id}/approve/", {}, format="json")

        resp = admin_client.get(
            "/api/v1/accounts/audit-logs/",
            {"event_type": "admin_approve", "status": "success", "limit": 5},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("results", resp.data)
        self.assertGreaterEqual(resp.data.get("count", 0), 1)

    @patch("accounts.services.NotificationService.send_user_otp", return_value=False)
    def test_admin_approve_returns_otp_send_failure_when_email_fails(self, _mock_send):
        self.client.post("/api/v1/auth/register-email/", {"email": "fail.mail@example.com"}, format="json")
        req = RegistrationRequest.objects.get(email="fail.mail@example.com")

        admin_client = APIClient()
        admin_login = admin_client.post(
            "/api/v1/auth/login/",
            {"username": "admin", "password": "Password123!"},
            format="json",
        )
        self.assertEqual(admin_login.status_code, 200)
        admin_client.credentials(HTTP_AUTHORIZATION=f"Token {admin_login.data['token']}")

        resp = admin_client.post(f"/api/v1/accounts/registration-requests/{req.id}/approve/", {}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.data["otp_sent"])
        self.assertEqual(resp.data.get("otp_reason"), "email_send_failed")

    @override_settings(
        OTP_EMAIL_COOLDOWN_SECONDS=60,
        OTP_EMAIL_LIMIT_COUNT=3,
        OTP_EMAIL_LIMIT_WINDOW_SECONDS=600,
        OTP_IP_LIMIT_COUNT=100,
        OTP_IP_LIMIT_WINDOW_SECONDS=300,
    )
    def test_otp_request_rate_limited_by_email_window(self):
        self.client.post("/api/v1/auth/register-email/", {"email": "window.limit@example.com"}, format="json")
        req = RegistrationRequest.objects.get(email="window.limit@example.com")

        admin_client = APIClient()
        admin_login = admin_client.post(
            "/api/v1/auth/login/",
            {"username": "admin", "password": "Password123!"},
            format="json",
        )
        admin_client.credentials(HTTP_AUTHORIZATION=f"Token {admin_login.data['token']}")
        approve_resp = admin_client.post(f"/api/v1/accounts/registration-requests/{req.id}/approve/", {}, format="json")
        self.assertEqual(approve_resp.status_code, 200)

        with patch("accounts.rate_limit.get_otp_cooldown_remaining_seconds", return_value=0), patch(
            "accounts.rate_limit.set_otp_cooldown", return_value=None
        ):
            r1 = self.client.post("/api/v1/auth/otp/request/", {"email": "window.limit@example.com"}, format="json")
            r2 = self.client.post("/api/v1/auth/otp/request/", {"email": "window.limit@example.com"}, format="json")
            r3 = self.client.post("/api/v1/auth/otp/request/", {"email": "window.limit@example.com"}, format="json")
            r4 = self.client.post("/api/v1/auth/otp/request/", {"email": "window.limit@example.com"}, format="json")

        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r3.status_code, 200)
        self.assertEqual(r4.status_code, 429)
        self.assertEqual(r4.data.get("error"), "rate_limited")

    @override_settings(
        OTP_EMAIL_COOLDOWN_SECONDS=60,
        OTP_EMAIL_LIMIT_COUNT=10,
        OTP_EMAIL_LIMIT_WINDOW_SECONDS=600,
        OTP_IP_LIMIT_COUNT=100,
        OTP_IP_LIMIT_WINDOW_SECONDS=300,
    )
    def test_otp_request_rate_limited_by_email_cooldown(self):
        self.client.post("/api/v1/auth/register-email/", {"email": "cooldown.limit@example.com"}, format="json")
        req = RegistrationRequest.objects.get(email="cooldown.limit@example.com")

        admin_client = APIClient()
        admin_login = admin_client.post(
            "/api/v1/auth/login/",
            {"username": "admin", "password": "Password123!"},
            format="json",
        )
        admin_client.credentials(HTTP_AUTHORIZATION=f"Token {admin_login.data['token']}")
        approve_resp = admin_client.post(f"/api/v1/accounts/registration-requests/{req.id}/approve/", {}, format="json")
        self.assertEqual(approve_resp.status_code, 200)

        r1 = self.client.post("/api/v1/auth/otp/request/", {"email": "cooldown.limit@example.com"}, format="json")
        r2 = self.client.post("/api/v1/auth/otp/request/", {"email": "cooldown.limit@example.com"}, format="json")

        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 429)
        self.assertEqual(r2.data.get("error"), "rate_limited")

    @override_settings(
        REJECT_ADMIN_LIMIT_COUNT=100,
        REJECT_ADMIN_LIMIT_WINDOW_SECONDS=3600,
        REJECT_IP_LIMIT_COUNT=100,
        REJECT_IP_LIMIT_WINDOW_SECONDS=3600,
    )
    def test_reject_same_request_twice_returns_400(self):
        self.client.post("/api/v1/auth/register-email/", {"email": "double.reject@example.com"}, format="json")
        req = RegistrationRequest.objects.get(email="double.reject@example.com")

        admin_client = APIClient()
        admin_login = admin_client.post(
            "/api/v1/auth/login/",
            {"username": "admin", "password": "Password123!"},
            format="json",
        )
        admin_client.credentials(HTTP_AUTHORIZATION=f"Token {admin_login.data['token']}")

        first = admin_client.post(f"/api/v1/accounts/registration-requests/{req.id}/reject/", {"reason": "x"}, format="json")
        second = admin_client.post(f"/api/v1/accounts/registration-requests/{req.id}/reject/", {"reason": "x"}, format="json")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 400)
        self.assertEqual(second.data.get("error"), "invalid_state")

    def test_admin_approve_sends_approval_email(self):
        self.client.post("/api/v1/auth/register-email/", {"email": "approved.mail@example.com"}, format="json")
        req = RegistrationRequest.objects.get(email="approved.mail@example.com")

        admin_client = APIClient()
        admin_login = admin_client.post(
            "/api/v1/auth/login/",
            {"username": "admin", "password": "Password123!"},
            format="json",
        )
        admin_client.credentials(HTTP_AUTHORIZATION=f"Token {admin_login.data['token']}")
        resp = admin_client.post(f"/api/v1/accounts/registration-requests/{req.id}/approve/", {}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertGreaterEqual(len(mail.outbox), 2)
        self.assertTrue(any("Registration request approved" in m.subject for m in mail.outbox))

    def test_auto_approve_creates_eligible_user_and_marks_request(self):
        settings_row = SystemSettings.get_solo()
        settings_row.auto_approve_enabled = True
        settings_row.save(update_fields=["auto_approve_enabled"])

        resp = self.client.post("/api/v1/auth/register-email/", {"email": "auto.user@example.com"}, format="json")
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.data.get("status"), "active")

        req = RegistrationRequest.objects.get(email="auto.user@example.com")
        self.assertEqual(req.status, RegistrationRequest.Status.APPROVED)
        self.assertEqual(req.review_reason, "auto-approved")
        self.assertIsNotNone(req.reviewed_by_id)
        self.assertIsNotNone(req.approved_user_id)

        user = User.objects.get(email="auto.user@example.com")
        profile = UserAuthProfile.objects.get(user=user)
        self.assertEqual(profile.auth_method, UserAuthProfile.AuthMethod.OTP_ONLY)
        self.assertTrue(profile.is_readonly)

        otp_resp = self.client.post("/api/v1/auth/otp/request/", {"email": "auto.user@example.com"}, format="json")
        self.assertEqual(otp_resp.status_code, 200)
        self.assertTrue(any("activation code" in m.subject.lower() or "login code" in m.subject.lower() for m in mail.outbox))

    def test_otp_request_uses_user_profile_eligibility_even_if_request_row_missing(self):
        user = User.objects.create(username="otp_no_req", email="otp.no.req@example.com", is_active=True)
        user.set_unusable_password()
        user.save(update_fields=["password"])
        UserAuthProfile.objects.create(
            user=user,
            auth_method=UserAuthProfile.AuthMethod.OTP_ONLY,
            is_readonly=True,
        )
        RegistrationRequest.objects.filter(email="otp.no.req@example.com").delete()

        resp = self.client.post("/api/v1/auth/otp/request/", {"email": "otp.no.req@example.com"}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(any("login code" in m.subject.lower() for m in mail.outbox))

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
        EMAIL_HOST="",
        EMAIL_USE_TLS=True,
    )
    def test_otp_request_with_missing_smtp_host_fails_gracefully(self):
        self.client.post("/api/v1/auth/register-email/", {"email": "smtp.missing@example.com"}, format="json")
        req = RegistrationRequest.objects.get(email="smtp.missing@example.com")

        admin_client = APIClient()
        admin_login = admin_client.post(
            "/api/v1/auth/login/",
            {"username": "admin", "password": "Password123!"},
            format="json",
        )
        admin_client.credentials(HTTP_AUTHORIZATION=f"Token {admin_login.data['token']}")
        approve_resp = admin_client.post(f"/api/v1/accounts/registration-requests/{req.id}/approve/", {}, format="json")
        self.assertEqual(approve_resp.status_code, 200)

        resp = self.client.post("/api/v1/auth/otp/request/", {"email": "smtp.missing@example.com"}, format="json")
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.data.get("error"), "otp_send_failed")
        self.assertTrue(
            AuditLog.objects.filter(
                event_type=AuditLog.EventType.EMAIL_SENT,
                user_email="smtp.missing@example.com",
                status=AuditLog.Status.FAILURE,
                failure_reason="smtp_host_missing",
            ).exists()
        )
