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
from dashboards.views import DashboardViewSet
from integrations.views import test_es_connection
from integrations.views import IntegrationViewSet, preview_es_index
from integrations.views import integrations_db_tables, integrations_create_table, integrations_create_table_from_es, integrations_preview_es_mapping
from rest_framework import routers
from ai_assistant.mcp_views import mcp_ticket_context, mcp_ticket_search_similar_cases, mcp_cmdb_asset_lookup, mcp_observables_extract
from ai_assistant.mcp_protocol_views import mcp_rpc, mcp_tools_manifest
from ai_assistant.views import test_connectivity as ai_test_connectivity
from ai_assistant.views import mcp_monitor as ai_mcp_monitor
from ai_assistant.views import mcp_registry_servers as ai_mcp_registry_servers
from ai_assistant.views import ai_chat as ai_chat
from ai_assistant.views import skill_monitor as ai_skill_monitor
from ai_assistant.views import (
    external_mcp_servers,
    external_mcp_detail,
    external_mcp_start,
    external_mcp_stop,
    skill_catalog as ai_skill_catalog,
    skill_configs as ai_skill_configs,
    skill_config_detail as ai_skill_config_detail,
    skill_content_detail as ai_skill_content_detail,
)

router = routers.DefaultRouter()
router.register(r'integrations', IntegrationViewSet, basename='integration')
router.register(r'dashboards', DashboardViewSet, basename='dashboard')

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/v1/auth/login/', LoginAPIView.as_view(), name='login'),
    path('api/v1/auth/login', LoginAPIView.as_view()),
    path('api/v1/auth/logout/', LogoutAPIView.as_view(), name='logout'),
    path('api/v1/auth/logout', LogoutAPIView.as_view()),
    path('api/v1/auth/register/', RegisterAPIView.as_view(), name='register'),
    path('api/v1/auth/register', RegisterAPIView.as_view()),
    path('api/v1/alerts/', include('alerts.urls')),
    path('api/v1/correlation/', include('correlation.urls')),
    path('api/v1/accounts/', include('accounts.urls')),
    path('api/v1/permissions/', include('accounts.urls_permissions')),  
    path('api/v1/', include(router.urls)),  
    path('api/v1/integrations/test_es', test_es_connection),
    path('api/v1/integrations/preview_es', preview_es_index),
    path('api/v1/integrations/db_tables', integrations_db_tables),
    path('api/v1/integrations/create_table', integrations_create_table),
    path('api/v1/integrations/create_table_from_es', integrations_create_table_from_es),
    path('api/v1/integrations/preview_es_mapping', integrations_preview_es_mapping),
    # Tickets API (v1, versioned like alerts)
    path('api/v1/tickets/', include('tickets.urls')),
    path('api/v1/ai-assistant/test-connectivity', ai_test_connectivity),
    path('api/v1/ai-assistant/test-connectivity/', ai_test_connectivity),
    path('api/v1/ai-assistant/mcp-monitor', ai_mcp_monitor),
    path('api/v1/ai-assistant/mcp-monitor/', ai_mcp_monitor),
    path('api/v1/ai-assistant/mcp-registry/servers', ai_mcp_registry_servers),
    path('api/v1/ai-assistant/mcp-registry/servers/', ai_mcp_registry_servers),
    path('api/v1/ai-assistant/skill-monitor', ai_skill_monitor),
    path('api/v1/ai-assistant/skill-monitor/', ai_skill_monitor),
    path('api/v1/ai-assistant/skills/catalog', ai_skill_catalog),
    path('api/v1/ai-assistant/skills/catalog/', ai_skill_catalog),
    path('api/v1/ai-assistant/skills/config', ai_skill_configs),
    path('api/v1/ai-assistant/skills/config/', ai_skill_configs),
    path('api/v1/ai-assistant/skills/config/<str:name>', ai_skill_config_detail),
    path('api/v1/ai-assistant/skills/config/<str:name>/', ai_skill_config_detail),
    path('api/v1/ai-assistant/skills/content/<str:name>', ai_skill_content_detail),
    path('api/v1/ai-assistant/skills/content/<str:name>/', ai_skill_content_detail),
    path('api/v1/ai-assistant/chat', ai_chat),
    path('api/v1/ai-assistant/chat/', ai_chat),
    path('api/v1/ai-assistant/external-mcp', external_mcp_servers),
    path('api/v1/ai-assistant/external-mcp/', external_mcp_servers),
    path('api/v1/ai-assistant/external-mcp/<str:name>', external_mcp_detail),
    path('api/v1/ai-assistant/external-mcp/<str:name>/', external_mcp_detail),
    path('api/v1/ai-assistant/external-mcp/<str:name>/start', external_mcp_start),
    path('api/v1/ai-assistant/external-mcp/<str:name>/start/', external_mcp_start),
    path('api/v1/ai-assistant/external-mcp/<str:name>/stop', external_mcp_stop),
    path('api/v1/ai-assistant/external-mcp/<str:name>/stop/', external_mcp_stop),
    # Standard MCP JSON-RPC endpoint (for remote MCP clients)
    path('api/v1/mcp', mcp_rpc),
    path('api/v1/mcp/', mcp_rpc),
    path('api/v1/mcp/tools', mcp_tools_manifest),
    path('api/v1/mcp/tools/', mcp_tools_manifest),
    # Built-in MCP endpoints (same backend port)
    path('api/v1/mcp/ticket-context/<str:ticket_number>', mcp_ticket_context),
    path('api/v1/mcp/ticket-search/similar-cases', mcp_ticket_search_similar_cases),
    path('api/v1/mcp/cmdb/asset-lookup', mcp_cmdb_asset_lookup),
    path('api/v1/mcp/observables/extract', mcp_observables_extract),
]
