import json
import requests
from urllib.parse import quote_plus
from django.conf import settings
import traceback
from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from .models import Integration
from .serializers import IntegrationSerializer
from django.db import connections, transaction
from django.views.decorators.csrf import csrf_exempt
from rest_framework.decorators import api_view
from requests.auth import HTTPBasicAuth
from rest_framework.permissions import IsAuthenticated
from accounts.permissions import HasDjangoPermissions, RbacModelPermissions
import os
import datetime
from rest_framework import status as rf_status

# Helper to write a detailed sync debug log when sync fails or imports zero rows.
def _write_sync_debug_log(index, mapping_columns, docs, extraction_results=None, errors=None, exc_tb=None, name=None):
    try:
        from django.conf import settings as _dj_settings
        if not getattr(_dj_settings, 'WRITE_CONFIG_TO_DISK', False):
            return None
        out_dir = os.path.join(os.path.dirname(__file__), 'es_mappings', 'sync_logs')
        os.makedirs(out_dir, exist_ok=True)
        ts = datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
        if name:
            import re
            safe_name = re.sub(r'[^0-9a-zA-Z_]', '_', str(name))
            fn = f"sync_{safe_name}_{ts}.json"
        else:
            safe_index = str(index).replace('/', '_').replace('\\', '_')
            fn = f"sync_{safe_index}_{ts}.json"
        path = os.path.join(out_dir, fn)
        payload = {
            'index': index,
            'mapping_columns': mapping_columns,
            'sample_docs': (docs or [])[:10],
            'extraction': extraction_results or [],
            'errors': errors or []
        }
        if exc_tb:
            payload['traceback'] = exc_tb
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        return path
    except Exception:
        return None


def _deny_if_no_perm(request, perm):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return Response({"detail": "Authentication credentials were not provided."}, status=rf_status.HTTP_401_UNAUTHORIZED)
    if user.is_superuser or user.has_perm(perm):
        return None
    return Response({"detail": "Permission denied."}, status=rf_status.HTTP_403_FORBIDDEN)
# -----------------------------
# 中文注释（文件级别说明）
#
# 该文件包含 Integrations 相关的视图函数和工具方法，主要职责包括：
# - 提供 Integration 的测试接口（IntegrationViewSet.test）用于检查外部服务连通性（例如 Elasticsearch）
# - 从 Elasticsearch 抓取数据并同步到目标数据库（sync_es_to_db），支持 PostgreSQL、MySQL 和通过 Django settings 的 DB 连接
# - 提供预览 ES 索引样本（preview_es_index）用于在创建表或推断映射之前查看示例文档
# - 提供查询目标数据库表列表的接口（integrations_db_tables）
# - 提供按 ES 映射创建目标表的接口（integrations_create_table_from_es / integrations_create_table）
# - 提供返回 ES 映射推断列信息的接口（integrations_preview_es_mapping），前端会使用该接口让用户编辑列名和 SQL 类型
#
# 注意：本文件中新增的注释仅用于说明代码逻辑，未对现有行为做出修改。请在运行时确保 Python 环境包含 requests/psycopg2/pymysql 等依赖以便完整功能可用。
# -----------------------------


class IntegrationViewSet(viewsets.ModelViewSet):
    queryset = Integration.objects.all().order_by('-created_at')
    serializer_class = IntegrationSerializer
    permission_classes = [RbacModelPermissions]
    rbac_action_perms = {"test": "change"}
    @action(detail=True, methods=['post'])
    def test(self, request, pk=None):
        it = self.get_object()
        try:
            cfg = it.config or {}
            if it.type == 'elasticsearch':
                host = cfg.get('host')
                auth = None
                if cfg.get('username'):
                    auth = (cfg.get('username'), cfg.get('password'))
                r = requests.get(host, auth=auth, timeout=10)
                return Response({'status': r.status_code, 'body': r.text, 'headers': dict(r.headers)})
            # naive test for other types
            return Response({'ok': True, 'type': it.type})
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# Helper: sync documents from ES index to a destination DB using integration configs
def sync_es_to_db(es_integration: Integration, index: str, dest_integration: Integration, query: dict = None, limit: int = 1000):
    # returns dict with status, imported_count and sample errors
    try:
        es_cfg = es_integration.config or {}
        dest_cfg = dest_integration.config or {}
        host = es_cfg.get('host')
        auth = None
        if es_cfg.get('username'):
            auth = (es_cfg.get('username'), es_cfg.get('password'))
        # simple search
        q = query or {"query": {"match_all": {}}}
        search_url = host.rstrip('/') + f"/{index}/_search?size={limit}"
        r = requests.post(search_url, json=q, auth=auth, timeout=30)
        r.raise_for_status()
        hits = r.json().get('hits', {}).get('hits', [])
        docs = [h.get('_source', {}) for h in hits]
        # also capture ES document ids if present for upsert
        es_ids = [h.get('_id') for h in hits]
        docs_with_ids = [
            {'es_id': es_ids[i] if i < len(es_ids) else None, 'source': doc}
            for i, doc in enumerate(docs)
        ]
        # prepare extraction results for debug logging
        extraction_results = []

        # dest integration: expect type 'postgresql' or 'mysql' and config with connection string or params
        if dest_integration.type in ('postgresql', 'mysql'):
            imported = 0
            errors = []
            inserted_es_ids = []
            table = dest_cfg.get('table') or 'es_imports'
            # Determine mapping columns: first try persisted ESMapping model, then file-backed mappings_dir, finally integration config
            mapping_columns = None
            try:
                from .models import ESMapping
                try:
                    em = ESMapping.objects.filter(index=index, table=table).first()
                    if em:
                        mapping_columns = em.columns
                except Exception:
                    mapping_columns = None
            except Exception:
                mapping_columns = None
            # fallback to reading mapping files on disk when DB mapping not available
            if not mapping_columns:
                try:
                    import os
                    mappings_dir = os.path.join(os.path.dirname(__file__), 'es_mappings')
                    # prefer a mapping file that was saved for the target table name
                    if os.path.isdir(mappings_dir):
                        for fn in os.listdir(mappings_dir):
                            if not fn.lower().endswith('.json'):
                                continue
                            fp = os.path.join(mappings_dir, fn)
                            try:
                                with open(fp, 'r', encoding='utf-8') as fh:
                                    data = json.load(fh)
                                if isinstance(data, dict) and data.get('table') == table and isinstance(data.get('columns'), list):
                                    mapping_columns = data.get('columns')
                                    break
                            except Exception:
                                continue
                    # fallback to index-named mapping file if no table-specific mapping found
                    if not mapping_columns and os.path.isdir(mappings_dir):
                        for fn in os.listdir(mappings_dir):
                            if not fn.lower().endswith('.json'):
                                continue
                            fp = os.path.join(mappings_dir, fn)
                            try:
                                with open(fp, 'r', encoding='utf-8') as fh:
                                    data = json.load(fh)
                                if isinstance(data, dict) and data.get('index') == index and isinstance(data.get('columns'), list):
                                    mapping_columns = data.get('columns')
                                    break
                            except Exception:
                                continue
                except Exception:
                    mapping_columns = None
            # fallback to integration config if no mapping file found
            if not mapping_columns:
                mapping_columns = dest_cfg.get('columns') or None
            # prefer a direct psycopg2 connection for Postgres (conn_str) as it gives better control
            conn_str = dest_cfg.get('conn_str')
            # If conn_str not provided, try to build one from individual params in config
            if not conn_str and dest_integration.type == 'postgresql':
                host = dest_cfg.get('host')
                user = dest_cfg.get('user')
                password = dest_cfg.get('password')
                dbname = dest_cfg.get('dbname') or dest_cfg.get('database')
                port = dest_cfg.get('port')
                if host and user and dbname:
                    # build a postgres URI
                    auth = f"{quote_plus(str(user))}:{quote_plus(str(password))}@" if password or user else ''
                    hostpart = f"{host}:{port}" if port else f"{host}"
                    conn_str = f"postgresql://{auth}{hostpart}/{dbname}"

            if dest_integration.type == 'postgresql' and conn_str:
                try:
                    import psycopg2
                    from psycopg2 import sql
                    from psycopg2.extras import execute_values

                    with psycopg2.connect(conn_str) as conn:
                        with conn.cursor() as cur:
                            # default jsonb mode: store full doc in data jsonb and upsert by es_id
                            # if mapping_columns provided, also create the mapped columns
                            base_cols = [sql.SQL('id serial PRIMARY KEY'), sql.SQL('es_id text')]
                            if mapping_columns and isinstance(mapping_columns, list):
                                for mc in mapping_columns:
                                    colname = mc.get('colname') or mc.get('name')
                                    sql_type = mc.get('sql_type') or 'text'
                                    if colname:
                                        base_cols.append(sql.SQL('{} {}').format(sql.Identifier(colname), sql.SQL(sql_type)))
                            base_cols.append(sql.SQL('data jsonb'))
                            base_cols.append(sql.SQL('created_at timestamptz DEFAULT now()'))
                            cur.execute(sql.SQL(
                                "CREATE TABLE IF NOT EXISTS {} ({})"
                            ).format(sql.Identifier(table), sql.SQL(', ').join(base_cols)))
                            # create unique index on es_id to avoid duplicate inserts
                            cur.execute(sql.SQL(
                                "CREATE UNIQUE INDEX IF NOT EXISTS {idx} ON {tbl} (es_id)"
                            ).format(idx=sql.Identifier(f"{table}_es_id_idx"), tbl=sql.Identifier(table)))

                            # Defensive: ensure `data` column and any mapped columns exist (help if table pre-exists without them)
                            try:
                                cur.execute(sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS data jsonb").format(sql.Identifier(table)))
                            except Exception:
                                # ignore if ALTER not supported for some PG versions
                                pass
                            try:
                                cur.execute(sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS ticket_number text").format(sql.Identifier(table)))
                            except Exception:
                                pass
                            if mapping_columns and isinstance(mapping_columns, list):
                                for mc in mapping_columns:
                                    colname = mc.get('colname') or mc.get('name')
                                    sql_type = mc.get('sql_type') or mc.get('sqlType') or 'text'
                                    if colname:
                                        try:
                                            cur.execute(sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS {} {}")
                                                        .format(sql.Identifier(table), sql.Identifier(colname), sql.SQL(sql_type)))
                                        except Exception:
                                            # if type or add fails, ignore and continue
                                            pass

                            # prepare rows: either (es_id, data) or (es_id, mapped cols..., data)
                            rows = []
                            if mapping_columns and isinstance(mapping_columns, list):
                                # helper to extract nested value by dot path
                                def get_in(d, path):
                                    if not path:
                                        return None
                                    parts = path.split('.')
                                    curv = d
                                    for p in parts:
                                        if not isinstance(curv, dict):
                                            return None
                                        curv = curv.get(p)
                                        if curv is None:
                                            return None
                                    return curv

                                for i, doc in enumerate(docs):
                                    esid = es_ids[i] if i < len(es_ids) else None
                                    mapped_vals = []
                                    mapped_map = {}
                                    for mc in mapping_columns:
                                        orig = mc.get('orig_name') or mc.get('orig') or mc.get('name')
                                        val = get_in(doc, orig) if orig and isinstance(orig, str) and ('.' in orig) else (doc.get(orig) if orig else None)
                                        mapped_vals.append(val)
                                        colname = mc.get('colname') or mc.get('name')
                                        mapped_map[colname] = val
                                    # final row: (esid, *mapped_vals, json.dumps(doc))
                                    rows.append((esid, *mapped_vals, json.dumps(doc)))
                                    extraction_results.append({'es_id': esid, 'mapped': mapped_map, 'raw': doc})
                                if rows:
                                    # build column list for INSERT
                                    mapped_col_names = [mc.get('colname') or mc.get('name') for mc in mapping_columns if (mc.get('colname') or mc.get('name'))]
                                    insert_cols = ['es_id'] + mapped_col_names + ['data']
                                    # avoid upsert: skip duplicates by es_id
                                    insert_stmt = sql.SQL('INSERT INTO {tbl} ({cols}) VALUES %s ON CONFLICT (es_id) DO NOTHING RETURNING es_id').format(
                                        tbl=sql.Identifier(table),
                                        cols=sql.SQL(',').join([sql.Identifier(c) for c in insert_cols])
                                    )
                                    # execute_values needs a query string; use as_string
                                    execute_values(cur, insert_stmt.as_string(conn), rows, template=None, page_size=100)
                                    try:
                                        returned = cur.fetchall()
                                    except Exception:
                                        returned = []
                                    inserted_es_ids.extend([r[0] for r in returned if r and r[0] is not None])
                                    imported = len(inserted_es_ids)
                            else:
                                for i, doc in enumerate(docs):
                                    esid = es_ids[i] if i < len(es_ids) else None
                                    rows.append((esid, json.dumps(doc)))
                                if rows:
                                    # avoid upsert: skip duplicates by es_id
                                    insert_stmt = sql.SQL(
                                        "INSERT INTO {tbl} (es_id, data) VALUES %s ON CONFLICT (es_id) DO NOTHING RETURNING es_id"
                                    ).format(tbl=sql.Identifier(table))
                                    execute_values(cur, insert_stmt.as_string(conn), rows, template=None, page_size=100)
                                try:
                                    returned = cur.fetchall()
                                except Exception:
                                    returned = []
                                inserted_es_ids.extend([r[0] for r in returned if r and r[0] is not None])
                                imported = len(inserted_es_ids)
                    # if nothing imported, write a debug log and return its path
                    if imported == 0:
                        try:
                            log_path = _write_sync_debug_log(index, mapping_columns, docs, extraction_results=extraction_results, errors=errors, name=table)
                        except Exception:
                            log_path = None
                        res = {'status': 'ok', 'imported': imported, 'errors': errors, 'docs': docs, 'docs_with_ids': docs_with_ids, 'inserted_es_ids': inserted_es_ids}
                        if log_path:
                            res['log_path'] = log_path
                        return res
                    return {'status': 'ok', 'imported': imported, 'errors': errors, 'docs': docs, 'docs_with_ids': docs_with_ids, 'inserted_es_ids': inserted_es_ids}
                except Exception as e:
                    return {'status': 'error', 'message': str(e)}

            # fallback: try MySQL direct connect if mysql config provided
            if dest_integration.type == 'mysql' and not conn_str:
                try:
                    import pymysql
                    # expect host,user,password,dbname in dest_cfg
                    host = dest_cfg.get('host')
                    user = dest_cfg.get('user')
                    password = dest_cfg.get('password')
                    dbname = dest_cfg.get('dbname') or dest_cfg.get('database')
                    port = int(dest_cfg.get('port')) if dest_cfg.get('port') else 3306
                    if host and user and dbname:
                        conn = pymysql.connect(host=host, user=user, password=password, db=dbname, port=port, charset='utf8mb4')
                        try:
                            with conn.cursor() as cur:
                                # create table if not exists (use TEXT for JSON payload for compatibility)
                                cur.execute(f"CREATE TABLE IF NOT EXISTS {table} (id INT AUTO_INCREMENT PRIMARY KEY, es_id VARCHAR(255), data JSON, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
                                # create unique index on es_id
                                try:
                                    cur.execute(f"CREATE UNIQUE INDEX {table}_es_id_idx ON {table} (es_id)")
                                except Exception:
                                    pass
                                # defensive: ensure data column exists
                                try:
                                    cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS data JSON")
                                except Exception:
                                    pass
                                try:
                                    cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS ticket_number VARCHAR(255)")
                                except Exception:
                                    pass
                                # insert rows without upsert (skip duplicates by es_id)
                                for i, doc in enumerate(docs):
                                    try:
                                        esid = es_ids[i] if i < len(es_ids) else None
                                        cur.execute(f"INSERT IGNORE INTO {table} (es_id, data) VALUES (%s, %s)", (esid, json.dumps(doc)))
                                        if cur.rowcount > 0:
                                            imported += cur.rowcount
                                            if esid is not None:
                                                inserted_es_ids.append(esid)
                                    except Exception as ie:
                                        errors.append(str(ie))
                            conn.commit()
                        finally:
                            conn.close()
                        if imported == 0:
                            try:
                                log_path = _write_sync_debug_log(index, mapping_columns, docs, extraction_results=extraction_results, errors=errors, name=table)
                            except Exception:
                                log_path = None
                            res = {'status': 'ok', 'imported': imported, 'errors': errors, 'docs': docs, 'docs_with_ids': docs_with_ids, 'inserted_es_ids': inserted_es_ids}
                            if log_path:
                                res['log_path'] = log_path
                            return res
                        return {'status': 'ok', 'imported': imported, 'errors': errors, 'docs': docs, 'docs_with_ids': docs_with_ids, 'inserted_es_ids': inserted_es_ids}
                except Exception:
                    # fall through to django_db fallback
                    pass

            # fallback: try Django DB connection if 'django_db' name provided
            db_name = dest_cfg.get('django_db')
            if db_name and db_name in settings.DATABASES:
                try:
                    conn = connections[db_name]
                    with conn.cursor() as cur:
                        # create table if not exists
                        cur.execute(f"CREATE TABLE IF NOT EXISTS {table} (id serial PRIMARY KEY, es_id text, data jsonb, created_at timestamptz DEFAULT now())")
                        # create unique index on es_id
                        try:
                            cur.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {table}_es_id_idx ON {table} (es_id)")
                        except Exception:
                            # older PG versions may not support IF NOT EXISTS on index creation
                            pass
                        # ensure any mapped columns exist: prefer saved mapping file, fallback to integration config
                        try:
                            mapping_columns = None
                            try:
                                import os
                                mappings_dir = os.path.join(os.path.dirname(__file__), 'es_mappings')
                                # prefer mapping keyed by target table
                                if os.path.isdir(mappings_dir):
                                    for fn in os.listdir(mappings_dir):
                                        if not fn.lower().endswith('.json'):
                                            continue
                                        fp = os.path.join(mappings_dir, fn)
                                        try:
                                            with open(fp, 'r', encoding='utf-8') as fh:
                                                data = json.load(fh)
                                            if isinstance(data, dict) and data.get('table') == table and isinstance(data.get('columns'), list):
                                                mapping_columns = data.get('columns')
                                                break
                                        except Exception:
                                            continue
                                # fallback to index match
                                if not mapping_columns and os.path.isdir(mappings_dir):
                                    for fn in os.listdir(mappings_dir):
                                        if not fn.lower().endswith('.json'):
                                            continue
                                        fp = os.path.join(mappings_dir, fn)
                                        try:
                                            with open(fp, 'r', encoding='utf-8') as fh:
                                                data = json.load(fh)
                                            if isinstance(data, dict) and data.get('index') == index and isinstance(data.get('columns'), list):
                                                mapping_columns = data.get('columns')
                                                break
                                        except Exception:
                                            continue
                            except Exception:
                                mapping_columns = None
                            if not mapping_columns:
                                mapping_columns = dest_cfg.get('columns') or None
                            if mapping_columns and isinstance(mapping_columns, list):
                                for mc in mapping_columns:
                                    colname = mc.get('colname') or mc.get('name')
                                    sql_type = mc.get('sql_type') or mc.get('sqlType') or 'text'
                                    if colname:
                                        try:
                                            cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {colname} {sql_type}")
                                        except Exception:
                                            pass
                        except Exception:
                            pass
                        try:
                            cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS ticket_number text")
                        except Exception:
                            pass
                        for i, doc in enumerate(docs):
                            try:
                                esid = es_ids[i] if i < len(es_ids) else None
                                try:
                                    cur.execute(f"INSERT INTO {table} (es_id, data) VALUES (%s, %s) ON CONFLICT (es_id) DO NOTHING RETURNING es_id", [esid, json.dumps(doc)])
                                    row = cur.fetchone()
                                    if row and row[0] is not None:
                                        imported += 1
                                        inserted_es_ids.append(row[0])
                                except Exception:
                                    cur.execute(f"INSERT INTO {table} (es_id, data) VALUES (%s, %s) ON CONFLICT (es_id) DO NOTHING", [esid, json.dumps(doc)])
                                    if cur.rowcount > 0:
                                        imported += cur.rowcount
                                        if esid is not None:
                                            inserted_es_ids.append(esid)
                            except Exception as ie:
                                errors.append(str(ie))
                    if imported == 0:
                        try:
                            log_path = _write_sync_debug_log(index, mapping_columns, docs, extraction_results=extraction_results, errors=errors, name=table)
                        except Exception:
                            log_path = None
                        res = {'status': 'ok', 'imported': imported, 'errors': errors, 'docs': docs, 'docs_with_ids': docs_with_ids, 'inserted_es_ids': inserted_es_ids}
                        if log_path:
                            res['log_path'] = log_path
                        return res
                    return {'status': 'ok', 'imported': imported, 'errors': errors, 'docs': docs, 'docs_with_ids': docs_with_ids, 'inserted_es_ids': inserted_es_ids}
                except Exception as e:
                    return {'status': 'error', 'message': str(e)}
            return {'status': 'error', 'message': 'Unsupported dest integration or missing connection details'}
        return {'status': 'error', 'message': 'Unsupported destination integration type'}
    except Exception as e:
        try:
            tb = traceback.format_exc()
            log_path = _write_sync_debug_log(index if 'index' in locals() else None, None, None, extraction_results=None, errors=[str(e)], exc_tb=tb, name=(dest_cfg.get('table') if 'dest_cfg' in locals() else None))
        except Exception:
            log_path = None
        res = {'status': 'error', 'message': str(e)}
        if log_path:
            res['log_path'] = log_path
        return res


@csrf_exempt
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def preview_es_index(request):
    denied = _deny_if_no_perm(request, "integrations.view_integration")
    if denied:
        return denied
    """POST { integration_id, index, size=10 } -> return hits._source sample"""
    try:
        data = request.data if hasattr(request, 'data') else {}
        iid = data.get('integration_id') or data.get('integration')
        index = data.get('index')
        size = int(data.get('size', 10))
        if not iid or not index:
            return Response({'error': 'integration_id and index required'}, status=status.HTTP_400_BAD_REQUEST)
        it = Integration.objects.get(id=iid)
        es_cfg = it.config or {}
        host = es_cfg.get('host')
        if not host:
            return Response({'error': 'integration config missing host'}, status=status.HTTP_400_BAD_REQUEST)
        auth = None
        if es_cfg.get('username'):
            auth = (es_cfg.get('username'), es_cfg.get('password'))

        search_url = host.rstrip('/') + f"/{index}/_search?size={size}"
        # Allow caller to supply a custom ES query
        user_query = data.get('query')
        es_query = user_query if user_query else None

        # If no explicit query provided, but caller supplied timestamp selection fields,
        # build an ES range query so Preview Data respects the time filter.
        if not es_query:
            ts_field = data.get('timestamp_field')
            ts_from = data.get('timestamp_from')
            ts_to = data.get('timestamp_to')
            ts_rel = data.get('timestamp_relative')
            # normalize custom relative
            if isinstance(ts_rel, dict) and ts_rel.get('value') and ts_rel.get('unit'):
                ts_from = f"now-{int(ts_rel.get('value'))}{ts_rel.get('unit')}"
                ts_to = 'now'
            elif isinstance(ts_rel, str) and ts_rel:
                # support preset formats like '1h','6h','24h','7d'
                m = None
                try:
                    import re
                    m = re.match(r'^(\d+)([mhd])$', ts_rel)
                except Exception:
                    m = None
                if m:
                    ts_from = f"now-{int(m.group(1))}{m.group(2)}"
                    ts_to = 'now'

            if ts_field and ts_from:
                es_query = {"query": {"range": {ts_field: {"gte": ts_from, "lte": ts_to or 'now'}}}}

        if not es_query:
            es_query = {"query": {"match_all": {}}}
        r = requests.post(search_url, json=es_query, auth=auth, timeout=15)
        r.raise_for_status()
        hits = r.json().get('hits', {}).get('hits', [])
        docs = [h.get('_source', {}) for h in hits]
        return Response({'ok': True, 'count': len(docs), 'rows': docs})
    except Integration.DoesNotExist:
        return Response({'error': 'integration not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        tb = traceback.format_exc()
        # return traceback in dev to help debugging
        info = {'error': str(e), 'traceback': tb}
        return Response(info, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@csrf_exempt
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def integrations_db_tables(request):
    denied = _deny_if_no_perm(request, "integrations.view_integration")
    if denied:
        return denied
    """POST with connection payload -> return list of table names
    Accepts: { db_type: 'postgres'|'mysql', conn_str?, host?, user?, password?, database?, port?, django_db? }
    """
    try:
        raw = request.data if hasattr(request, 'data') else {}
        # make a mutable copy we can augment from an Integration if caller provided one
        try:
            data = dict(raw)
        except Exception:
            data = raw
        # allow passing an existing integration id to reuse its saved DB config
        dest_iid = data.get('integration') or data.get('integration_id') or data.get('dest_integration') or data.get('dest_integration_id')
        if dest_iid:
            try:
                dest_it = Integration.objects.get(id=dest_iid)
                dest_cfg = dest_it.config or {}
                # prefer explicit payload values, but fill missing ones from integration config
                if not data.get('conn_str') and dest_cfg.get('conn_str'):
                    data['conn_str'] = dest_cfg.get('conn_str')
                for k in ('host', 'user', 'username', 'password', 'database', 'dbname', 'port', 'django_db'):
                    if not data.get(k) and dest_cfg.get(k) is not None:
                        data[k] = dest_cfg.get(k)
                # normalize db_type from integration type if missing
                if not data.get('db_type'):
                    if dest_it.type == 'postgresql':
                        data['db_type'] = 'postgres'
                    elif dest_it.type == 'mysql':
                        data['db_type'] = 'mysql'
            except Integration.DoesNotExist:
                return Response({'error': 'integration not found'}, status=status.HTTP_404_NOT_FOUND)
        db_type = data.get('db_type')
        # django_db alias takes precedence for listing via Django connections
        django_db = data.get('django_db')
        if django_db:
            if django_db not in settings.DATABASES:
                return Response({'error': 'django_db alias not found in settings'}, status=status.HTTP_400_BAD_REQUEST)
            conn = connections[django_db]
            with conn.cursor() as cur:
                cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
                rows = [r[0] for r in cur.fetchall()]
            return Response({'ok': True, 'tables': rows})

        if db_type == 'postgres' or data.get('conn_str'):
            # try psycopg2 with conn_str or built connection
            conn_str = data.get('conn_str')
            if not conn_str:
                host = data.get('host')
                user = data.get('user')
                password = data.get('password')
                dbname = data.get('database') or data.get('dbname')
                port = data.get('port')
                if not (host and user and dbname):
                    return Response({'error': 'host,user,database required'}, status=status.HTTP_400_BAD_REQUEST)
                auth = f"{quote_plus(str(user))}:{quote_plus(str(password))}@" if user or password else ''
                hostpart = f"{host}:{port}" if port else f"{host}"
                conn_str = f"postgresql://{auth}{hostpart}/{dbname}"
            try:
                import psycopg2
                with psycopg2.connect(conn_str) as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
                        rows = [r[0] for r in cur.fetchall()]
                return Response({'ok': True, 'tables': rows})
            except Exception as e:
                return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        if db_type == 'mysql':
            try:
                import pymysql
                host = data.get('host')
                user = data.get('user')
                password = data.get('password')
                dbname = data.get('database') or data.get('dbname')
                port = int(data.get('port')) if data.get('port') else 3306
                if not (host and user and dbname):
                    return Response({'error': 'host,user,database required'}, status=status.HTTP_400_BAD_REQUEST)
                conn = pymysql.connect(host=host, user=user, password=password, db=dbname, port=port, charset='utf8mb4')
                try:
                    with conn.cursor() as cur:
                        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = %s", (dbname,))
                        rows = [r[0] for r in cur.fetchall()]
                    return Response({'ok': True, 'tables': rows})
                finally:
                    conn.close()
            except Exception as e:
                return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({'error': 'unsupported or missing db_type'}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
@csrf_exempt   
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def test_es_connection(request):
    denied = _deny_if_no_perm(request, "integrations.change_integration")
    if denied:
        return denied
    """Server-side proxy to test connectivity to an Elasticsearch host.
    POST body: { host: 'http://...', username: 'user', password: 'pass', path: '/_cluster/health' }
    Returns: { ok: true, status: 200, body: '...', headers: { ... } } or error details.
    """
    payload = request.data or {}
    # accept host in several common keys for flexibility
    host = payload.get('host') or payload.get('url') or (payload.get('config') and payload.get('config').get('host'))
    try:
        print('DEBUG test_es_connection payload:', payload)
    except Exception:
        pass
    if not host:
        return Response({'ok': False, 'error': 'host required'}, status=status.HTTP_400_BAD_REQUEST)
    path = payload.get('path') or '/_cluster/health'
    url = str(host).rstrip('/') + path
    try:
        print('DEBUG test_es_connection computed url:', url)
    except Exception:
        pass
    auth = None
    username = payload.get('username')
    password = payload.get('password')
    if username:
        auth = HTTPBasicAuth(username, password or '')
    try:
        resp = requests.get(url, timeout=10, auth=auth)
    except requests.exceptions.RequestException as e:
        return Response({'ok': False, 'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

    body = None
    try:
        body = resp.text
    except Exception:
        body = None
    headers = dict(resp.headers)

    # Treat non-2xx responses from ES as errors and surface details to the client
    if resp.status_code >= 400:
        parsed = body
        try:
            parsed = resp.json()
        except Exception:
            pass
        return Response({'ok': False, 'status': resp.status_code, 'body': parsed, 'headers': headers}, status=resp.status_code)

    return Response({'ok': True, 'status': resp.status_code, 'body': body, 'headers': headers})

@csrf_exempt
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def integrations_create_table(request):
    denied = _deny_if_no_perm(request, "integrations.change_integration")
    if denied:
        return denied
    """POST with connection payload + table name -> create a default table and return ok
    Accepts: { db_type, conn_str?, host?, user?, password?, database?, port?, django_db?, table }
    Creates a safe default table: id serial/auto, es_id text unique, data jsonb/json, created_at timestamp
    """
    try:
        raw = request.data if hasattr(request, 'data') else {}
        try:
            data = dict(raw)
        except Exception:
            data = raw
        # allow referring to an existing integration to read DB config
        dest_iid = data.get('integration') or data.get('integration_id') or data.get('dest_integration') or data.get('dest_integration_id')
        if dest_iid:
            try:
                dest_it = Integration.objects.get(id=dest_iid)
                dest_cfg = dest_it.config or {}
                if not data.get('conn_str') and dest_cfg.get('conn_str'):
                    data['conn_str'] = dest_cfg.get('conn_str')
                for k in ('host', 'user', 'username', 'password', 'database', 'dbname', 'port', 'django_db', 'table'):
                    if not data.get(k) and dest_cfg.get(k) is not None:
                        data[k] = dest_cfg.get(k)
                if not data.get('db_type'):
                    if dest_it.type == 'postgresql':
                        data['db_type'] = 'postgres'
                    elif dest_it.type == 'mysql':
                        data['db_type'] = 'mysql'
            except Integration.DoesNotExist:
                return Response({'error': 'integration not found'}, status=status.HTTP_404_NOT_FOUND)
        table = data.get('table')
        if not table:
            return Response({'error': 'table name required'}, status=status.HTTP_400_BAD_REQUEST)
        django_db = data.get('django_db')
        if django_db:
            if django_db not in settings.DATABASES:
                return Response({'error': 'django_db alias not found'}, status=status.HTTP_400_BAD_REQUEST)
            conn = connections[django_db]
            with conn.cursor() as cur:
                cur.execute(f"CREATE TABLE IF NOT EXISTS {table} (id serial PRIMARY KEY, es_id text, ticket_number text, data jsonb, created_at timestamptz DEFAULT now())")
                try:
                    cur.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {table}_es_id_idx ON {table} (es_id)")
                except Exception:
                    pass
            return Response({'ok': True, 'table': table})

        db_type = data.get('db_type')
        if db_type == 'postgres' or data.get('conn_str'):
            conn_str = data.get('conn_str')
            if not conn_str:
                host = data.get('host')
                user = data.get('user')
                password = data.get('password')
                dbname = data.get('database') or data.get('dbname')
                port = data.get('port')
                if not (host and user and dbname):
                    return Response({'error': 'host,user,database required'}, status=status.HTTP_400_BAD_REQUEST)
                auth = f"{quote_plus(str(user))}:{quote_plus(str(password))}@" if user or password else ''
                hostpart = f"{host}:{port}" if port else f"{host}"
                conn_str = f"postgresql://{auth}{hostpart}/{dbname}"
            try:
                import psycopg2
                from psycopg2 import sql
                with psycopg2.connect(conn_str) as conn:
                    with conn.cursor() as cur:
                        cur.execute(sql.SQL(
                            "CREATE TABLE IF NOT EXISTS {} (id serial PRIMARY KEY, es_id text, ticket_number text, data jsonb, created_at timestamptz DEFAULT now())"
                        ).format(sql.Identifier(table)))
                        try:
                            cur.execute(sql.SQL("CREATE UNIQUE INDEX IF NOT EXISTS {} ON {} (es_id)").format(sql.Identifier(f"{table}_es_id_idx"), sql.Identifier(table)))
                        except Exception:
                            pass
                return Response({'ok': True, 'table': table})
            except Exception as e:
                return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        if db_type == 'mysql':
            try:
                import pymysql
                host = data.get('host')
                user = data.get('user')
                password = data.get('password')
                dbname = data.get('database') or data.get('dbname')
                port = int(data.get('port')) if data.get('port') else 3306
                if not (host and user and dbname):
                    return Response({'error': 'host,user,database required'}, status=status.HTTP_400_BAD_REQUEST)
                conn = pymysql.connect(host=host, user=user, password=password, db=dbname, port=port, charset='utf8mb4')
                try:
                    with conn.cursor() as cur:
                        cur.execute(f"CREATE TABLE IF NOT EXISTS {table} (id INT AUTO_INCREMENT PRIMARY KEY, es_id VARCHAR(255), ticket_number VARCHAR(255), data JSON, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
                        try:
                            cur.execute(f"CREATE UNIQUE INDEX {table}_es_id_idx ON {table} (es_id)")
                        except Exception:
                            pass
                    conn.commit()
                finally:
                    conn.close()
                return Response({'ok': True, 'table': table})
            except Exception as e:
                return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({'error': 'unsupported or missing db_type'}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@csrf_exempt
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def integrations_create_table_from_es(request):
    denied = _deny_if_no_perm(request, "integrations.change_integration")
    if denied:
        return denied
    """Create table using an Elasticsearch index mapping from an existing ES Integration.
    POST payload accepts:
      - es_integration: id of an Integration of type 'elasticsearch'
      - index: the ES index name to read mapping from
      - table: target table name to create
      - optional connection fields as in integrations_create_table (db_type/conn_str/host/user/password/database/port/django_db)
    """
    try:
        raw = request.data if hasattr(request, 'data') else {}
        try:
            data = dict(raw)
        except Exception:
            data = raw
        es_iid = data.get('es_integration') or data.get('es_integration_id')
        index = data.get('index')
        table = data.get('table')
        if not es_iid or not index or not table:
            return Response({'error': 'es_integration, index and table are required'}, status=status.HTTP_400_BAD_REQUEST)

        # load ES integration
        try:
            es_it = Integration.objects.get(id=es_iid)
        except Integration.DoesNotExist:
            return Response({'error': 'es integration not found'}, status=status.HTTP_404_NOT_FOUND)

        es_cfg = es_it.config or {}
        host = es_cfg.get('host')
        if not host:
            return Response({'error': 'es integration missing host'}, status=status.HTTP_400_BAD_REQUEST)
        auth = None
        if es_cfg.get('username'):
            auth = (es_cfg.get('username'), es_cfg.get('password'))

        # If caller provided explicit columns, use them (allow frontend-edited columns)
        provided_columns = data.get('columns')
        props = {}
        used_provided = False
        if provided_columns:
            # expected format: [{ orig_name, colname, sql_type, es_type? }, ...]
            cols = []
            for c in provided_columns:
                orig = c.get('orig_name') or c.get('orig')
                colname = c.get('colname')
                # store provided sql_type in meta so later code can use it
                meta = {'sql_type': c.get('sql_type') or c.get('sqlType')}
                cols.append((orig, colname, meta))
            used_provided = True
        else:
            # fetch mapping
            mapping_url = host.rstrip('/') + f"/{index}/_mapping"
            try:
                r = requests.get(mapping_url, auth=auth, timeout=15)
                r.raise_for_status()
                mapping = r.json()
            except Exception as e:
                return Response({'error': f'could not fetch mapping: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            # extract properties for the index (support index-level mapping structure)
            try:
                # mapping could be {index: {mappings: {...}}} or {index: {mappings: {properties: {...}}}}
                top = None
                if isinstance(mapping, dict):
                    # get first key if mapping keyed by index
                    if len(mapping) == 1 and list(mapping.keys())[0] == index:
                        top = mapping[index].get('mappings') or mapping[index]
                    else:
                        # sometimes mapping is returned as mappings directly
                        top = mapping.get('mappings') or mapping
                else:
                    top = mapping

                if isinstance(top, dict) and 'properties' in top:
                    props = top.get('properties', {})
                elif isinstance(top, dict) and any(isinstance(v, dict) and 'properties' in v for v in top.values()):
                    # nested key like {"mappings": {"properties":{}}}
                    # fallback: find first properties occurrence
                    def find_props(d):
                        if not isinstance(d, dict):
                            return None
                        if 'properties' in d:
                            return d['properties']
                        for v in d.values():
                            if isinstance(v, dict):
                                res = find_props(v)
                                if res:
                                    return res
                        return None
                    props = find_props(top) or {}
                else:
                    props = {}
            except Exception:
                props = {}

            # helper: sanitize column names (replace dots with __, remove non-alnum/_)
            import re
            def sanitize_col(name: str) -> str:
                s = name.replace('.', '__')
                s = re.sub(r'[^0-9a-zA-Z_]', '_', s)
                # ensure not starting with digit
                if re.match(r'^[0-9]', s):
                    s = '_' + s
                return s.lower()

            cols = []
            for name, meta in (props or {}).items():
                colname = sanitize_col(name)
                cols.append((name, colname, meta))
        # Only infer mapping from ES mapping if caller didn't provide explicit columns
        if not used_provided:
            try:
                # mapping could be {index: {mappings: {...}}} or {index: {mappings: {properties: {...}}}}
                top = None
                if isinstance(mapping, dict):
                    # get first key if mapping keyed by index
                    if len(mapping) == 1 and list(mapping.keys())[0] == index:
                        top = mapping[index].get('mappings') or mapping[index]
                    else:
                        # sometimes mapping is returned as mappings directly
                        top = mapping.get('mappings') or mapping
                else:
                    top = mapping

                if isinstance(top, dict) and 'properties' in top:
                    props = top.get('properties', {})
                elif isinstance(top, dict) and any(isinstance(v, dict) and 'properties' in v for v in top.values()):
                    # nested key like {"mappings": {"properties":{}}}
                    # fallback: find first properties occurrence
                    def find_props(d):
                        if not isinstance(d, dict):
                            return None
                        if 'properties' in d:
                            return d['properties']
                        for v in d.values():
                            if isinstance(v, dict):
                                res = find_props(v)
                                if res:
                                    return res
                        return None
                    props = find_props(top) or {}
                else:
                    props = {}
            except Exception:
                props = {}

            # helper: sanitize column names (replace dots with __, remove non-alnum/_)
            import re
            def sanitize_col(name: str) -> str:
                s = name.replace('.', '__')
                s = re.sub(r'[^0-9a-zA-Z_]', '_', s)
                # ensure not starting with digit
                if re.match(r'^[0-9]', s):
                    s = '_' + s
                return s.lower()

            # mapping type -> SQL type
            def es_to_pg(field: dict) -> str:
                t = field.get('type')
                if not t:
                    # object/nested or missing type -> jsonb
                    return 'jsonb'
                t = t.lower()
                if t in ('text', 'keyword', 'string'):
                    return 'text'
                if t in ('integer', 'int'):
                    return 'integer'
                if t in ('long',):
                    return 'bigint'
                if t in ('short', 'byte'):
                    return 'smallint'
                if t in ('float',):
                    return 'real'
                if t in ('double', 'half_float', 'scaled_float'):
                    return 'double precision'
                if t in ('boolean',):
                    return 'boolean'
                if t in ('date',):
                    return 'timestamptz'
                if t in ('object', 'nested'):
                    return 'jsonb'
                # default
                return 'jsonb'

            def es_to_mysql(field: dict) -> str:
                t = field.get('type')
                if not t:
                    return 'JSON'
                t = t.lower()
                if t in ('text', 'keyword', 'string'):
                    return 'TEXT'
                if t in ('integer', 'int'):
                    return 'INT'
                if t in ('long',):
                    return 'BIGINT'
                if t in ('short', 'byte'):
                    return 'SMALLINT'
                if t in ('float', 'double', 'scaled_float', 'half_float'):
                    return 'DOUBLE'
                if t in ('boolean',):
                    return 'TINYINT(1)'
                if t in ('date',):
                    return 'DATETIME'
                if t in ('object', 'nested'):
                    return 'JSON'
                return 'JSON'

            # build column definitions
            cols = []
            for name, meta in (props or {}).items():
                colname = sanitize_col(name)
                cols.append((name, colname, meta))

        # If no properties found, fallback to simple jsonb table
        if not cols:
            # delegate to existing create_table logic by calling integrations_create_table with same payload
            # reuse existing handler: call integrations_create_table with provided data
            # ensure required fields are present
            return integrations_create_table(request)

        # Before building SQL, allow caller to reference an existing destination integration to reuse its DB config
        dest_iid = data.get('dest_integration') or data.get('dest_integration_id') or data.get('integration') or data.get('integration_id')
        if dest_iid:
            try:
                dest_it = Integration.objects.get(id=dest_iid)
                dest_cfg = dest_it.config or {}
                # fill missing connection fields from dest integration config
                if not data.get('conn_str') and dest_cfg.get('conn_str'):
                    data['conn_str'] = dest_cfg.get('conn_str')
                for k in ('host', 'user', 'username', 'password', 'database', 'dbname', 'port', 'django_db', 'table'):
                    if not data.get(k) and dest_cfg.get(k) is not None:
                        data[k] = dest_cfg.get(k)
                if not data.get('db_type'):
                    if dest_it.type == 'postgresql':
                        data['db_type'] = 'postgres'
                    elif dest_it.type == 'mysql':
                        data['db_type'] = 'mysql'
            except Integration.DoesNotExist:
                return Response({'error': 'dest integration not found'}, status=status.HTTP_404_NOT_FOUND)

        # build SQL depending on target DB. `cols` is available either from provided_columns or inferred mapping
        db_type = data.get('db_type')
        if data.get('conn_str'):
            # if conn_str provided, we can detect postgres by prefix
            conn_str = data.get('conn_str')
            if conn_str.startswith('postgres'):
                db_type = 'postgres'
            elif conn_str.startswith('mysql'):
                db_type = 'mysql'

        # POSTGRES
        if db_type == 'postgres' or data.get('conn_str'):
            conn_str = data.get('conn_str')
            if not conn_str:
                host = data.get('host')
                user = data.get('user')
                password = data.get('password')
                dbname = data.get('database') or data.get('dbname')
                port = data.get('port')
                if not (host and user and dbname):
                    return Response({'error': 'host,user,database required for postgres'}, status=status.HTTP_400_BAD_REQUEST)
                auth = f"{quote_plus(str(user))}:{quote_plus(str(password))}@" if user or password else ''
                hostpart = f"{host}:{port}" if port else f"{host}"
                conn_str = f"postgresql://{auth}{hostpart}/{dbname}"
            try:
                import psycopg2
                from psycopg2 import sql
                with psycopg2.connect(conn_str) as conn:
                    with conn.cursor() as cur:
                        # compose CREATE TABLE
                        col_defs = [sql.SQL('id serial PRIMARY KEY'), sql.SQL('es_id text'), sql.SQL('ticket_number text')]
                        for orig, colname, meta in cols:
                            # if frontend provided a concrete sql_type string, use it directly
                            provided_sql = None
                            if isinstance(meta, dict):
                                provided_sql = meta.get('sql_type')
                            if provided_sql:
                                col_defs.append(sql.SQL('{} {}').format(sql.Identifier(colname), sql.SQL(provided_sql)))
                            else:
                                pgtype = es_to_pg(meta or {})
                                col_defs.append(sql.SQL('{} {}').format(sql.Identifier(colname), sql.SQL(pgtype)))
                        # always include a jsonb `data` column to store the full document (sync expects it)
                        col_defs.append(sql.SQL('data jsonb'))
                        col_defs.append(sql.SQL('created_at timestamptz DEFAULT now()'))
                        create_stmt = sql.SQL('CREATE TABLE IF NOT EXISTS {} ({})').format(
                            sql.Identifier(table), sql.SQL(', ').join(col_defs)
                        )
                        cur.execute(create_stmt)
                        # create unique index on es_id
                        try:
                            cur.execute(sql.SQL('CREATE UNIQUE INDEX IF NOT EXISTS {} ON {} (es_id)').format(sql.Identifier(f"{table}_es_id_idx"), sql.Identifier(table)))
                        except Exception:
                            pass
                # return richer column metadata so frontend can persist mapping
                resp_cols = []
                for orig, colname, meta in cols:
                    provided_sql = meta.get('sql_type') if isinstance(meta, dict) else None
                    sql_t = provided_sql or es_to_pg(meta or {})
                    resp_cols.append({'orig_name': orig, 'colname': colname, 'sql_type': sql_t})
                # Persist inferred mapping to ESMapping model (database) and optionally save to disk
                saved_path = None
                try:
                    from .models import ESMapping
                    try:
                        em, created = ESMapping.objects.update_or_create(index=index, table=table, defaults={'columns': resp_cols})
                    except Exception:
                        em = None
                except Exception:
                    em = None

                try:
                    save_to_file = bool(data.get('save_to_file'))
                except Exception:
                    save_to_file = False
                if save_to_file:
                    try:
                        import os, re, datetime, json
                        base_dir = os.path.dirname(__file__)
                        out_dir = os.path.join(base_dir, 'es_mappings')
                        os.makedirs(out_dir, exist_ok=True)
                        safe_name = re.sub(r'[^0-9a-zA-Z_]', '_', str(table))
                        fname = f"{safe_name}.json"
                        file_path = os.path.join(out_dir, fname)
                        if os.path.exists(file_path):
                            ts = datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
                            file_path = os.path.join(out_dir, f"{safe_name}_{ts}.json")
                        with open(file_path, 'w', encoding='utf-8') as fh:
                            json.dump({'index': index, 'table': table, 'columns': resp_cols}, fh, ensure_ascii=False, indent=2)
                        saved_path = file_path
                    except Exception:
                        saved_path = None
                resp = {'ok': True, 'table': table, 'columns': resp_cols}
                if saved_path:
                    resp['saved_path'] = saved_path
                return Response(resp)
            except Exception as e:
                return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        # MYSQL
        if db_type == 'mysql':
            try:
                import pymysql
                host = data.get('host')
                user = data.get('user')
                password = data.get('password')
                dbname = data.get('database') or data.get('dbname')
                port = int(data.get('port')) if data.get('port') else 3306
                if not (host and user and dbname):
                    return Response({'error': 'host,user,database required for mysql'}, status=status.HTTP_400_BAD_REQUEST)
                conn = pymysql.connect(host=host, user=user, password=password, db=dbname, port=port, charset='utf8mb4')
                try:
                    with conn.cursor() as cur:
                        col_defs = ['id INT AUTO_INCREMENT PRIMARY KEY', 'es_id VARCHAR(255)', 'ticket_number VARCHAR(255)']
                        for orig, colname, meta in cols:
                            provided_sql = None
                            if isinstance(meta, dict):
                                provided_sql = meta.get('sql_type')
                            if provided_sql:
                                col_defs.append(f"{colname} {provided_sql}")
                            else:
                                mytype = es_to_mysql(meta or {})
                                col_defs.append(f"{colname} {mytype}")
                        # include a JSON `data` column so sync/upsert can store the full document
                        col_defs.append('data JSON')
                        col_defs.append('created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP')
                        cur.execute(f"CREATE TABLE IF NOT EXISTS {table} ({', '.join(col_defs)})")
                        try:
                            cur.execute(f"CREATE UNIQUE INDEX {table}_es_id_idx ON {table} (es_id)")
                        except Exception:
                            pass
                    conn.commit()
                finally:
                    conn.close()
                resp_cols = []
                for orig, colname, meta in cols:
                    provided_sql = meta.get('sql_type') if isinstance(meta, dict) else None
                    sql_t = provided_sql or es_to_mysql(meta or {})
                    resp_cols.append({'orig_name': orig, 'colname': colname, 'sql_type': sql_t})
                # Optionally persist inferred mapping to a file named after the table
                saved_path = None
                try:
                    save_to_file = bool(data.get('save_to_file'))
                except Exception:
                    save_to_file = False
                if save_to_file:
                    try:
                        import os, re, datetime, json
                        base_dir = os.path.dirname(__file__)
                        out_dir = os.path.join(base_dir, 'es_mappings')
                        os.makedirs(out_dir, exist_ok=True)
                        safe_name = re.sub(r'[^0-9a-zA-Z_]', '_', str(table))
                        fname = f"{safe_name}.json"
                        file_path = os.path.join(out_dir, fname)
                        if os.path.exists(file_path):
                            ts = datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
                            file_path = os.path.join(out_dir, f"{safe_name}_{ts}.json")
                        with open(file_path, 'w', encoding='utf-8') as fh:
                            json.dump({'index': index, 'table': table, 'columns': resp_cols}, fh, ensure_ascii=False, indent=2)
                        saved_path = file_path
                    except Exception:
                        saved_path = None
                resp = {'ok': True, 'table': table, 'columns': resp_cols}
                if saved_path:
                    resp['saved_path'] = saved_path
                return Response(resp)
            except Exception as e:
                return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        # django_db fallback
        django_db = data.get('django_db')
        if django_db and django_db in settings.DATABASES:
            try:
                conn = connections[django_db]
                with conn.cursor() as cur:
                    # build SQL with simple mapping to jsonb for complex types
                    col_parts = ['id serial PRIMARY KEY', 'es_id text', 'ticket_number text']
                    for orig, colname, meta in cols:
                        pgtype = es_to_pg(meta or {})
                        col_parts.append(f"{colname} {pgtype}")
                    # include a jsonb `data` column so the sync code can continue to write the full document
                    col_parts.append('data jsonb')
                    col_parts.append('created_at timestamptz DEFAULT now()')
                    cur.execute(f"CREATE TABLE IF NOT EXISTS {table} ({', '.join(col_parts)})")
                    try:
                        cur.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {table}_es_id_idx ON {table} (es_id)")
                    except Exception:
                        pass
                resp_cols = []
                for orig, colname, meta in cols:
                    provided_sql = meta.get('sql_type') if isinstance(meta, dict) else None
                    sql_t = provided_sql or es_to_pg(meta or {})
                    resp_cols.append({'orig_name': orig, 'colname': colname, 'sql_type': sql_t})
                # Optionally persist inferred mapping to a file named after the table
                saved_path = None
                try:
                    save_to_file = bool(data.get('save_to_file'))
                except Exception:
                    save_to_file = False
                if save_to_file:
                    try:
                        import os, re, datetime, json
                        base_dir = os.path.dirname(__file__)
                        out_dir = os.path.join(base_dir, 'es_mappings')
                        os.makedirs(out_dir, exist_ok=True)
                        safe_name = re.sub(r'[^0-9a-zA-Z_]', '_', str(table))
                        fname = f"{safe_name}.json"
                        file_path = os.path.join(out_dir, fname)
                        if os.path.exists(file_path):
                            ts = datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
                            file_path = os.path.join(out_dir, f"{safe_name}_{ts}.json")
                        with open(file_path, 'w', encoding='utf-8') as fh:
                            json.dump({'index': index, 'table': table, 'columns': resp_cols}, fh, ensure_ascii=False, indent=2)
                        saved_path = file_path
                    except Exception:
                        saved_path = None
                resp = {'ok': True, 'table': table, 'columns': resp_cols}
                if saved_path:
                    resp['saved_path'] = saved_path
                return Response(resp)
            except Exception as e:
                return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({'error': 'unsupported or missing db_type'}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@csrf_exempt
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def integrations_preview_es_mapping(request):
    denied = _deny_if_no_perm(request, "integrations.view_integration")
    if denied:
        return denied
    """Preview Elasticsearch index mapping and return inferred columns without creating a table.
    POST payload: { es_integration: id, index: name, db_type?: 'postgres'|'mysql', conn_str?, host?, user?, password?, database?, port?, django_db? }
    Response: { ok: True, columns: [{ orig_name, colname, es_type, sql_type, sample }] }
    """
    try:
        data = request.data if hasattr(request, 'data') else {}
        es_iid = data.get('es_integration') or data.get('es_integration_id')
        index = data.get('index')
        if not es_iid or not index:
            return Response({'error': 'es_integration and index are required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            es_it = Integration.objects.get(id=es_iid)
        except Integration.DoesNotExist:
            return Response({'error': 'es integration not found'}, status=status.HTTP_404_NOT_FOUND)

        es_cfg = es_it.config or {}
        host = es_cfg.get('host')
        if not host:
            return Response({'error': 'es integration missing host'}, status=status.HTTP_400_BAD_REQUEST)
        auth = None
        if es_cfg.get('username'):
            auth = (es_cfg.get('username'), es_cfg.get('password'))

        # fetch mapping
        mapping_url = host.rstrip('/') + f"/{index}/_mapping"
        try:
            r = requests.get(mapping_url, auth=auth, timeout=15)
            r.raise_for_status()
            mapping = r.json()
        except Exception as e:
            return Response({'error': f'could not fetch mapping: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        # extract properties (reuse logic from create_table_from_es)
        props = {}
        try:
            top = None
            if isinstance(mapping, dict):
                if len(mapping) == 1 and list(mapping.keys())[0] == index:
                    top = mapping[index].get('mappings') or mapping[index]
                else:
                    top = mapping.get('mappings') or mapping
            else:
                top = mapping

            if isinstance(top, dict) and 'properties' in top:
                props = top.get('properties', {})
            elif isinstance(top, dict) and any(isinstance(v, dict) and 'properties' in v for v in top.values()):
                def find_props(d):
                    if not isinstance(d, dict):
                        return None
                    if 'properties' in d:
                        return d['properties']
                    for v in d.values():
                        if isinstance(v, dict):
                            res = find_props(v)
                            if res:
                                return res
                    return None
                props = find_props(top) or {}
            else:
                props = {}
        except Exception:
            props = {}

        import re
        def sanitize_col(name: str) -> str:
            s = name.replace('.', '__')
            s = re.sub(r'[^0-9a-zA-Z_]', '_', s)
            if re.match(r'^[0-9]', s):
                s = '_' + s
            return s.lower()

        def es_to_pg(field: dict) -> str:
            t = field.get('type')
            if not t:
                return 'jsonb'
            t = t.lower()
            if t in ('text', 'keyword', 'string'):
                return 'text'
            if t in ('integer', 'int'):
                return 'integer'
            if t in ('long',):
                return 'bigint'
            if t in ('short', 'byte'):
                return 'smallint'
            if t in ('float',):
                return 'real'
            if t in ('double', 'half_float', 'scaled_float'):
                return 'double precision'
            if t in ('boolean',):
                return 'boolean'
            if t in ('date',):
                return 'timestamptz'
            if t in ('object', 'nested'):
                return 'jsonb'
            return 'jsonb'

        def es_to_mysql(field: dict) -> str:
            t = field.get('type')
            if not t:
                return 'JSON'
            t = t.lower()
            if t in ('text', 'keyword', 'string'):
                return 'TEXT'
            if t in ('integer', 'int'):
                return 'INT'
            if t in ('long',):
                return 'BIGINT'
            if t in ('short', 'byte'):
                return 'SMALLINT'
            if t in ('float', 'double', 'scaled_float', 'half_float'):
                return 'DOUBLE'
            if t in ('boolean',):
                return 'TINYINT(1)'
            if t in ('date',):
                return 'DATETIME'
            if t in ('object', 'nested'):
                return 'JSON'
            return 'JSON'

        cols = []
        for name, meta in (props or {}).items():
            colname = sanitize_col(name)
            cols.append((name, colname, meta))

        # fetch one or more sample docs to show sample values (optional)
        samples = {}
        try:
            # allow caller to specify query/size/sort
            sample_query = data.get('query')
            sample_size = int(data.get('size', 1)) if data.get('size') is not None else 1
            sample_sort = data.get('sort')
            if not sample_query:
                sample_query = {"query": {"match_all": {}}}
            # build url with requested size
            sample_url = host.rstrip('/') + f"/{index}/_search?size={sample_size}"
            # attach sort if provided
            body = dict(sample_query) if isinstance(sample_query, dict) else sample_query
            if sample_sort:
                body['sort'] = sample_sort
            r2 = requests.post(sample_url, json=body, auth=auth, timeout=10)
            r2.raise_for_status()
            hits = r2.json().get('hits', {}).get('hits', [])
            if hits:
                # take first hit as representative (ordered by sort if provided)
                src = hits[0].get('_source', {})
                # helper to get nested value by dot path
                def get_in(d, path):
                    parts = path.split('.') if isinstance(path, str) else []
                    cur = d
                    for p in parts:
                        if not isinstance(cur, dict):
                            return None
                        cur = cur.get(p)
                        if cur is None:
                            return None
                    return cur

                for orig, colname, meta in cols:
                    val = get_in(src, orig) or src.get(orig)
                    samples[orig] = val
        except Exception:
            samples = {}

        # determine target db type for sql_type hints
        db_type = data.get('db_type')
        if data.get('conn_str'):
            conn_str = data.get('conn_str')
            if conn_str.startswith('postgres'):
                db_type = 'postgres'
            elif conn_str.startswith('mysql'):
                db_type = 'mysql'

        out_cols = []
        for orig, colname, meta in cols:
            es_t = (meta.get('type') if isinstance(meta, dict) else None) or None
            if db_type == 'mysql':
                sql_t = es_to_mysql(meta or {})
            else:
                sql_t = es_to_pg(meta or {})
            out_cols.append({'orig_name': orig, 'colname': colname, 'es_type': es_t, 'sql_type': sql_t, 'sample': samples.get(orig)})

        # Persist preview mapping to ESMapping model when `table` provided; optionally write file if requested
        try:
            save_to_file = bool(data.get('save_to_file'))
        except Exception:
            save_to_file = False
        filename = data.get('filename') or None
        saved_path = None
        table = data.get('table')
        if table:
            try:
                from .models import ESMapping
                try:
                    ESMapping.objects.update_or_create(index=index, table=table, defaults={'columns': out_cols})
                except Exception:
                    pass
            except Exception:
                pass

        if save_to_file or filename:
            import os
            # ensure directory exists under the integrations app
            base_dir = os.path.dirname(__file__)
            out_dir = os.path.join(base_dir, 'es_mappings')
            try:
                os.makedirs(out_dir, exist_ok=True)
            except Exception:
                out_dir = None

            if out_dir:
                # sanitize filename or generate one
                if filename and isinstance(filename, str) and filename.strip():
                    name = filename.strip()
                    # remove suspicious path separators
                    name = name.replace('..', '')
                    name = name.replace('/', '_').replace('\\', '_')
                    if not name.lower().endswith('.json'):
                        name = name + '.json'
                else:
                    import datetime
                    ts = datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
                    name = f'preview_{index}_{ts}.json'

                file_path = os.path.join(out_dir, name)
                try:
                    with open(file_path, 'w', encoding='utf-8') as fh:
                        json.dump({'index': index, 'columns': out_cols}, fh, ensure_ascii=False, indent=2)
                    saved_path = file_path
                except Exception:
                    saved_path = None

        resp = {'ok': True, 'columns': out_cols}
        if saved_path:
            resp['saved_path'] = saved_path
        return Response(resp)
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
 
