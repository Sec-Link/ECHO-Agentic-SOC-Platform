from django.contrib import admin

from .models import InterfaceEndpoint, InterfaceRequestLog


class InterfaceRequestLogInline(admin.TabularInline):
    model = InterfaceRequestLog
    extra = 0
    fields = ['method', 'response_status', 'source_ip', 'created_at']
    readonly_fields = ['method', 'response_status', 'source_ip', 'created_at']


@admin.register(InterfaceEndpoint)
class InterfaceEndpointAdmin(admin.ModelAdmin):
    list_display = ['name', 'interface_type', 'is_active', 'created_by', 'created_at', 'last_event_at']
    list_filter = ['interface_type', 'is_active']
    search_fields = ['name', 'description']
    readonly_fields = ['id', 'created_at', 'updated_at', 'last_event_at']
    inlines = [InterfaceRequestLogInline]


@admin.register(InterfaceRequestLog)
class InterfaceRequestLogAdmin(admin.ModelAdmin):
    list_display = ['endpoint', 'method', 'response_status', 'source_ip', 'created_at']
    list_filter = ['method', 'response_status']
    readonly_fields = ['id', 'created_at']

