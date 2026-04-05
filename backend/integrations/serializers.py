from rest_framework import serializers
from .models import Integration


class IntegrationSerializer(serializers.ModelSerializer):
    """
    Integration 序列化器

    中文说明：
    - 将 `Integration` 模型映射为 API 的输入/输出数据结构。
    - 在 validate 方法中对数据库类型的集成（postgresql/mysql）做额外校验，确保提供足够的连接信息。
    """
    class Meta:
        model = Integration
        fields = '__all__'

    def validate(self, data):
        # 取得当前输入或已有实例的 type/config（支持更新场景）
        t = data.get('type') or (self.instance.type if self.instance else None)
        cfg = data.get('config') or (self.instance.config if self.instance else {})
        # 对数据库类型做额外验证：必须至少提供 conn_str、django_db，或 host+user+dbname
        if t in ('postgresql', 'mysql'):
            # 若 config 缺失，直接报错
            if not cfg:
                raise serializers.ValidationError('config required for database integrations')
            if not (cfg.get('conn_str') or cfg.get('django_db') or (cfg.get('host') and cfg.get('user') and (cfg.get('dbname') or cfg.get('database')))):
                # 未提供足够的连接信息，返回可读错误信息
                raise serializers.ValidationError('postgresql/mysql integrations require conn_str or django_db or host+user+dbname')
        return data
