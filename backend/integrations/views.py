import json
import requests
import traceback
from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from .models import Integration
from .serializers import IntegrationSerializer
from rest_framework.decorators import api_view
from requests.auth import HTTPBasicAuth
from rest_framework.permissions import IsAuthenticated
from accounts.permissions import HasDjangoPermissions, RbacModelPermissions
from django.utils.dateparse import parse_datetime
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
# 涓枃娉ㄩ噴锛堟枃浠剁骇鍒鏄庯級
#
# 璇ユ枃浠跺寘鍚?Integrations 鐩稿叧鐨勮鍥惧嚱鏁板拰宸ュ叿鏂规硶锛屼富瑕佽亴璐ｅ寘鎷細
# - 鎻愪緵 Integration 鐨勬祴璇曟帴鍙ｏ紙IntegrationViewSet.test锛夌敤浜庢鏌ュ閮ㄦ湇鍔¤繛閫氭€э紙渚嬪 Elasticsearch锛?
# - 浠?Elasticsearch 鎶撳彇鏁版嵁骞跺悓姝ュ埌鐩爣鏁版嵁搴擄紙sync_es_to_db锛夛紝鏀寔 PostgreSQL銆丮ySQL 鍜岄€氳繃 Django settings 鐨?DB 杩炴帴
# - 鎻愪緵棰勮 ES 绱㈠紩鏍锋湰锛坧review_es_index锛夌敤浜庡湪鍒涘缓琛ㄦ垨鎺ㄦ柇鏄犲皠涔嬪墠鏌ョ湅绀轰緥鏂囨。
# - 鎻愪緵鏌ヨ鐩爣鏁版嵁搴撹〃鍒楄〃鐨勬帴鍙ｏ紙integrations_db_tables锛?
# - 鎻愪緵鎸?ES 鏄犲皠鍒涘缓鐩爣琛ㄧ殑鎺ュ彛锛坕ntegrations_create_table_from_es / integrations_create_table锛?
# - 鎻愪緵杩斿洖 ES 鏄犲皠鎺ㄦ柇鍒椾俊鎭殑鎺ュ彛锛坕ntegrations_preview_es_mapping锛夛紝鍓嶇浼氫娇鐢ㄨ鎺ュ彛璁╃敤鎴风紪杈戝垪鍚嶅拰 SQL 绫诲瀷
#
# 娉ㄦ剰锛氭湰鏂囦欢涓柊澧炵殑娉ㄩ噴浠呯敤浜庤鏄庝唬鐮侀€昏緫锛屾湭瀵圭幇鏈夎涓哄仛鍑轰慨鏀广€傝鍦ㄨ繍琛屾椂纭繚 Python 鐜鍖呭惈 requests/psycopg2/pymysql 绛変緷璧栦互渚垮畬鏁村姛鑳藉彲鐢ㄣ€?
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
def sync_es_to_db(alerts: Integration, index: str, dest_integration: Integration, query: dict = None, limit: int = 1000):
    # ORM-only: sync ES docs into alerts_alert.
    try:
        from alerts.models import Alert

        es_cfg = alerts.config or {}
        dest_cfg = dest_integration.config or {}
        host = es_cfg.get('host')
        auth = None
        if es_cfg.get('username'):
            auth = (es_cfg.get('username'), es_cfg.get('password'))

        q = query or {"query": {"match_all": {}}}
        search_url = host.rstrip('/') + f"/{index}/_search?size={limit}"
        r = requests.post(search_url, json=q, auth=auth, timeout=30)
        r.raise_for_status()
        hits = r.json().get('hits', {}).get('hits', [])
        docs = [h.get('_source', {}) for h in hits]
        es_ids = [h.get('_id') for h in hits]
        docs_with_ids = [
            {'es_id': es_ids[i] if i < len(es_ids) else None, 'source': doc}
            for i, doc in enumerate(docs)
        ]

        imported = 0
        errors = []
        inserted_es_ids = []
        extraction_results = []
        table = dest_cfg.get('table') or 'alerts_alert'

        mapping_columns = None
        try:
            from .models import ESMapping
            em = ESMapping.objects.filter(index=index, table=table).first()
            if em:
                mapping_columns = em.columns
        except Exception:
            mapping_columns = None

        if not mapping_columns:
            try:
                mappings_dir = os.path.join(os.path.dirname(__file__), 'es_mappings')
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

        def _get_in(d, path):
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

        def _coerce_int(value):
            if value is None or value == '':
                return None
            try:
                return int(value)
            except Exception:
                return None

        def _coerce_dt(value):
            if value is None:
                return None
            if isinstance(value, datetime.datetime):
                return value
            raw = str(value).strip()
            if raw.endswith('Z'):
                raw = raw[:-1] + '+00:00'
            return parse_datetime(raw)

        for i, doc in enumerate(docs):
            try:
                src = doc if isinstance(doc, dict) else {}
                esid = es_ids[i] if i < len(es_ids) else None

                if mapping_columns and isinstance(mapping_columns, list):
                    mapped_map = {}
                    for mc in mapping_columns:
                        orig = mc.get('orig_name') or mc.get('orig') or mc.get('name')
                        colname = mc.get('colname') or mc.get('name')
                        val = _get_in(src, orig) if isinstance(orig, str) and '.' in orig else (src.get(orig) if orig else None)
                        if colname:
                            mapped_map[colname] = val
                    extraction_results.append({'es_id': esid, 'mapped': mapped_map, 'raw': src})

                alert_id = src.get('alert_id') or src.get('id') or esid
                if not alert_id:
                    errors.append(f"missing alert_id/es_id at row={i}")
                    continue

                payload = dict(src)
                payload.setdefault('_es_id', esid)
                payload['alert_id'] = alert_id
                payload['source_index'] = index

                defaults = {
                    'timestamp': _coerce_dt(src.get('timestamp')),
                    'severity': src.get('severity'),
                    'message': src.get('message'),
                    'source_index': index,
                    'rule_id': src.get('rule_id'),
                    'title': src.get('title'),
                    'status': _coerce_int(src.get('status')),
                    'description': src.get('description'),
                    'category': src.get('category'),
                    'ticket_number': src.get('ticket_number') or src.get('ticket'),
                    'source_data': payload,
                }

                _, created = Alert.objects.update_or_create(
                    alert_id=alert_id,
                    source_index=index,
                    defaults=defaults,
                )
                if created:
                    imported += 1
                    inserted_es_ids.append(esid if esid is not None else alert_id)
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
        try:
            tb = traceback.format_exc()
            log_path = _write_sync_debug_log(index if 'index' in locals() else None, None, None, extraction_results=None, errors=[str(e)], exc_tb=tb, name=(dest_cfg.get('table') if 'dest_cfg' in locals() else None))
        except Exception:
            log_path = None
        res = {'status': 'error', 'message': str(e)}
        if log_path:
            res['log_path'] = log_path
        return res


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

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def integrations_preview_es_mapping(request):
    denied = _deny_if_no_perm(request, "integrations.view_integration")
    if denied:
        return denied
    """Preview Elasticsearch index mapping and return inferred columns without creating a table.
    POST payload: { alerts: id, index: name, db_type?: 'postgres'|'mysql', conn_str?, host?, user?, password?, database?, port?, django_db? }
    Response: { ok: True, columns: [{ orig_name, colname, es_type, sql_type, sample }] }
    """
    try:
        data = request.data if hasattr(request, 'data') else {}
        es_iid = data.get('alerts') or data.get('alerts_id')
        index = data.get('index')
        if not es_iid or not index:
            return Response({'error': 'alerts and index are required'}, status=status.HTTP_400_BAD_REQUEST)

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
 

