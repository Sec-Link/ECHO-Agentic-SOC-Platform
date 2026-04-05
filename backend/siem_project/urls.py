"""
URL configuration for siem_project project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import include, path
from accounts.views import LoginAPIView, LogoutAPIView, RegisterAPIView
from integrations.views import test_es_connection
from integrations.views import IntegrationViewSet, preview_es_index
from integrations.views import integrations_db_tables, integrations_create_table, integrations_create_table_from_es, integrations_preview_es_mapping
from rest_framework import routers

router = routers.DefaultRouter()
router.register(r'integrations', IntegrationViewSet, basename='integration')

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/v1/auth/login/', LoginAPIView.as_view(), name='login'),
    path('api/v1/auth/login', LoginAPIView.as_view()),
    path('api/v1/auth/logout/', LogoutAPIView.as_view(), name='logout'),
    path('api/v1/auth/logout', LogoutAPIView.as_view()),
    path('api/v1/auth/register/', RegisterAPIView.as_view(), name='register'),
    path('api/v1/auth/register', RegisterAPIView.as_view()),
    path('api/v1/accounts/', include('accounts.urls')),
    path('api/v1/permissions/', include('accounts.urls_permissions')),  
    path('api/v1/', include(router.urls)),  
    path('api/v1/integrations/test_es', test_es_connection),
    path('api/v1/integrations/preview_es', preview_es_index),
    path('api/v1/integrations/db_tables', integrations_db_tables),
    path('api/v1/integrations/create_table', integrations_create_table),
    path('api/v1/integrations/create_table_from_es', integrations_create_table_from_es),
    path('api/v1/integrations/preview_es_mapping', integrations_preview_es_mapping),
]
