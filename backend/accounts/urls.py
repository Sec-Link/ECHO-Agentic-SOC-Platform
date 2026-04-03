# accounts/urls.py
from django.urls import path, include
from rest_framework.routers import SimpleRouter
from accounts.views import RegisterAPIView, LoginAPIView, LogoutAPIView, PasswordChangeAPIView
from accounts.api.views import UserAdminViewSet, GroupAdminViewSet

app_name = 'accounts'

router = SimpleRouter()
router.register(r'users', UserAdminViewSet, basename='user')
router.register(r'groups', GroupAdminViewSet, basename='group')

urlpatterns = [
    # auth endpoints
    path('auth/register/', RegisterAPIView.as_view(), name='register'),
    path('auth/login/', LoginAPIView.as_view(), name='login'),
    path('auth/logout/', LogoutAPIView.as_view(), name='logout'),
    path('change-password/', PasswordChangeAPIView.as_view(), name='change_password'),
    path('rbac/', include('accounts.urls_rbac')),
    # api resources
    path('', include(router.urls)),
]
