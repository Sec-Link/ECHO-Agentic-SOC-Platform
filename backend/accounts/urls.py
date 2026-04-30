# accounts/urls.py
from django.urls import path, include
from rest_framework.routers import SimpleRouter
from accounts.views import (
    GuestEmailStatusAPIView,
    LoginAPIView,
    LogoutAPIView,
    OTPRequestAPIView,
    OTPVerifyAPIView,
    PasswordChangeAPIView,
    RegisterAPIView,
    RegisterEmailAPIView,
    RegistrationApproveAPIView,
    AuditLogListAPIView,
    RegistrationRejectAPIView,
    RegistrationRequestListAPIView,
    SystemSettingsAPIView,
)
from accounts.api.views import UserAdminViewSet, GroupAdminViewSet

app_name = 'accounts'

router = SimpleRouter()
router.register(r'users', UserAdminViewSet, basename='user')
router.register(r'groups', GroupAdminViewSet, basename='group')

urlpatterns = [
    # auth endpoints
    path('auth/register/', RegisterAPIView.as_view(), name='register'),
    path('auth/register-email/', RegisterEmailAPIView.as_view(), name='register_email'),
    path('auth/guest-email-status/', GuestEmailStatusAPIView.as_view(), name='guest_email_status'),
    path('auth/login/', LoginAPIView.as_view(), name='login'),
    path('auth/otp/request/', OTPRequestAPIView.as_view(), name='otp_request'),
    path('auth/otp/verify/', OTPVerifyAPIView.as_view(), name='otp_verify'),
    path('auth/logout/', LogoutAPIView.as_view(), name='logout'),
    path('change-password/', PasswordChangeAPIView.as_view(), name='change_password'),
    # registration approval APIs
    path('registration-requests/', RegistrationRequestListAPIView.as_view(), name='registration_requests'),
    path('audit-logs/', AuditLogListAPIView.as_view(), name='audit_logs'),
    path('system-settings/', SystemSettingsAPIView.as_view(), name='system_settings'),
    path(
        'registration-requests/<uuid:request_id>/approve/',
        RegistrationApproveAPIView.as_view(),
        name='registration_approve',
    ),
    path(
        'registration-requests/<uuid:request_id>/reject/',
        RegistrationRejectAPIView.as_view(),
        name='registration_reject',
    ),
    # api resources
    path('', include(router.urls)),
]
