from django.contrib import admin
from .models import Integration


@admin.register(Integration)
class IntegrationAdmin(admin.ModelAdmin):
    list_display = ('name', 'type', 'created_at')
    readonly_fields = ('created_at', 'updated_at')
    search_fields = ('name', 'type')

    def config_preview(self, obj):
        cfg = obj.config or {}
        # mask passwords in preview
        cfg_masked = {k: ('***' if 'pass' in k.lower() or 'secret' in k.lower() else v) for k, v in cfg.items()}
        return str(cfg_masked)

    config_preview.short_description = 'config'

# -----------------------------
# 中文注释：
# 该模块为 Django admin 的集成（Integration）模型管理器配置，定义了后台展示字段和搜索项。
# - `list_display`: 在 admin 列表视图中显示的列
# - `readonly_fields`: 不可编辑的只读字段
# - `config_preview`: 一个简易的配置预览方法，会对包含密码或 secret 的键做掩码处理，避免在 admin 中泄露敏感信息
#
# 本文件仅添加文档注释，不更改任何运行时逻辑。
# -----------------------------
