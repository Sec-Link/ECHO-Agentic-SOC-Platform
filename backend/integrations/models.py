"""
integrations.models

中文说明：
定义 Integration 模型用于存储外部系统的连接信息（Elasticsearch、Postgres、MySQL 等）。
本文件包含模型字段及若干辅助方法，均用于在后端调用时方便获取连接字符串、目标表名等信息。

注意：此处仅为文档性注释，未修改现有业务逻辑或字段定义。
"""

from django.db import models
import uuid
from urllib.parse import quote_plus


class Integration(models.Model):
    """
    Integration 模型

    字段说明：
    - id: 使用 UUID 作为主键，便于跨系统引用与合并
    - name: 人类可读名称
    - type: 集成类型字符串（例如 'elasticsearch'、'postgresql'、'mysql' 等）
    - config: 存放连接参数的 JSONField（结构随 type 不同而不同）
    - created_at / updated_at: 自动维护的时间戳
    """
    # 主键使用 UUID，便于跨系统引用
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # 人类可读的名称
    name = models.CharField(max_length=200)
    # 集成类型，例如 'elasticsearch', 'postgresql', 'mysql' 等
    type = models.CharField(max_length=100)
    # 存放各种连接参数和元数据的 JSON 字段（灵活可扩展）
    config = models.JSONField(default=dict)
    # 创建/更新时间戳
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        # 字符串表示，便于在 admin/日志中查看
        return f"{self.name} ({self.type})"

class ESMapping(models.Model):
    """Persisted mapping between an Elasticsearch index and a target SQL table.

    Fields:
    - index: ES index name
    - table: target SQL table name
    - columns: list of column descriptors [{ orig_name, colname, sql_type }, ...]
    - created_at
    """
    index = models.CharField(max_length=256, db_index=True)
    table = models.CharField(max_length=256, db_index=True)
    columns = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('index', 'table')

    def __str__(self):
        return f"ESMapping({self.index} -> {self.table})"

    # 以下是与 DB 集成相关的帮助函数（用于构造或读取连接信息）
    def is_db(self):
        # 判断当前集成类型是否为数据库类（Postgres / MySQL）
        return self.type in ('postgresql', 'mysql')

    def get_table(self, default='es_imports'):
        # 从 config 中读取目标表名，若不存在则返回默认表名
        cfg = self.config or {}
        return cfg.get('table') or default

    def get_django_db(self):
        # 返回 config 中的 django_db 别名（用于使用 Django 的多 DB 连接）
        cfg = self.config or {}
        return cfg.get('django_db')

    def get_conn_str(self):
        """
        返回连接字符串（如果 config 中已有或可以从字段构造）。

        - 对于 Postgres，返回类似：postgresql://user:pass@host:port/dbname
        - 对于 MySQL，脚本通常使用单独的连接参数而非单一 conn_str，因此可能返回 None
        """
        cfg = self.config or {}
        conn = cfg.get('conn_str')
        if conn:
            # 若配置中显式提供 conn_str，优先使用
            return conn
        if self.type == 'postgresql':
            # 从散装配置项构造 postgres 的 conn_str
            host = cfg.get('host')
            user = cfg.get('user')
            password = cfg.get('password')
            dbname = cfg.get('dbname') or cfg.get('database')
            port = cfg.get('port')
            if host and user and dbname:
                auth = f"{quote_plus(str(user))}:{quote_plus(str(password))}@" if user else ''
                hostpart = f"{host}:{port}" if port else f"{host}"
                return f"postgresql://{auth}{hostpart}/{dbname}"
        # 若无法构造或类型不匹配，返回 None
        return None
