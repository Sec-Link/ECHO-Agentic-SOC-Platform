"""Elasticsearch access + alert listing helpers.

Data flow (high level):
- Frontend -> `AlertListView` -> `AlertService.list_alerts()`.
- Prefer DB cache (`alerts_alert`) when present.
- Otherwise fetch from ES (client or HTTP fallback) and upsert into DB.
"""

import json
import os
import hashlib
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Tuple
import logging
import inspect
import base64
import time
import threading

import requests

from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import Case, CharField, Count, IntegerField, Sum, Value, When
from django.db.models.functions import TruncHour
from django.utils import timezone

from .models import Alert, ESIntegrationConfig

MOCK_FILE = Path(__file__).resolve().parent / 'mock_alerts.json'

try:
    from elasticsearch import Elasticsearch
except Exception:
    Elasticsearch = None

logger = logging.getLogger(__name__)


def _get_global_es_config() -> ESIntegrationConfig | None:
    """Return the current ES config record."""
    cfg = ESIntegrationConfig.objects.filter(enabled=True).order_by('-id').first()
    if cfg:
        return cfg
    return ESIntegrationConfig.objects.order_by('-id').first()


def _get_active_index_name() -> str | None:
    try:
        cfg = _get_global_es_config()
        idx = (cfg.index or '').strip() if cfg else ''
        return idx or None
    except Exception:
        return None


def _alerts_queryset_for_active_index():
    qs = Alert.objects.all()
    active_index = _get_active_index_name()
    if active_index:
        qs = qs.filter(source_index=active_index)
    # Guard dashboard/list APIs from legacy/broken rows that were inserted
    # without a stable identity.
    qs = qs.exclude(alert_id__isnull=True).exclude(alert_id='')
    return qs


def _parse_es_timestamp(value) -> datetime | None:
    """Parse timestamps like `2025-12-16T12:00:00Z` (with/without fractions)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        value = str(value)
    raw = value.strip()
    if raw.endswith('Z'):
        raw = raw[:-1] + '+00:00'
    try:
        return datetime.fromisoformat(raw)
    except Exception:
        return None


def _coerce_int(value):
    try:
        if value is None or value == '':
            return None
        return int(value)
    except Exception:
        return None


def _compute_alert_identity(doc: Dict, fallback_index: str | None = None) -> str | None:
    """Return a stable alert identity to prevent duplicate inserts.

    Priority:
    1) source alert_id if present
    2) deterministic hash of key fields + raw doc content
    """
    raw_alert_id = doc.get('alert_id')
    if raw_alert_id not in (None, ''):
        return str(raw_alert_id)[:64]
    raw_es_id = doc.get('_es_id')
    if raw_es_id not in (None, ''):
        return str(raw_es_id)[:64]

    payload = {
        'timestamp': doc.get('timestamp'),
        'severity': doc.get('severity'),
        'message': doc.get('message'),
        'source_index': doc.get('source_index') or fallback_index,
        'rule_id': doc.get('rule_id'),
        'title': doc.get('title'),
        'status': doc.get('status'),
        'description': doc.get('description'),
        'category': doc.get('category'),
        'doc': doc,
    }
    raw = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=True)
    digest = hashlib.sha256(raw.encode('utf-8')).hexdigest()
    return f"fp_{digest}"[:64]


def _ensure_alert_identity(doc: Dict, fallback_index: str | None = None) -> str:
    aid = _compute_alert_identity(doc, fallback_index)
    if aid:
        return aid
    raw = json.dumps({'doc': doc, 'idx': fallback_index}, sort_keys=True, default=str, ensure_ascii=True)
    return f"fp_{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"[:64]


def _serialize_alert_row(row: Alert) -> Dict:
    # Keep output ES-like for the frontend.
    payload = dict(row.source_data) if isinstance(row.source_data, dict) else {}
    payload.pop('tenant_id', None)
    payload.update(
        {
            'alert_id': row.alert_id,
            'timestamp': row.timestamp.isoformat().replace('+00:00', 'Z') if row.timestamp else None,
            'severity': row.severity,
            'message': row.message,
            'source_index': row.source_index,
            'rule_id': row.rule_id,
            'title': row.title,
            'status': row.status,
            'description': row.description,
            'category': row.category,
        }
    )
    return payload


def _upsert_docs_to_db(docs: List[Dict]) -> None:
    """Upsert ES docs into Postgres (best-effort).

    This runs inline in the request path, so keep it resilient.
    """
    active_index = _get_active_index_name()
    for doc in docs:
        try:
            src_idx = doc.get('source_index') or active_index
            alert_id = _ensure_alert_identity(doc, active_index)
            doc['alert_id'] = alert_id
            doc['source_index'] = src_idx
            defaults = {
                'timestamp': _parse_es_timestamp(doc.get('timestamp')),
                'severity': doc.get('severity'),
                'message': doc.get('message'),
                'source_index': src_idx,
                'rule_id': doc.get('rule_id'),
                'title': doc.get('title'),
                'status': _coerce_int(doc.get('status')),
                'description': doc.get('description'),
                'category': doc.get('category'),
                'source_data': {**doc, 'alert_id': alert_id},
            }
            with transaction.atomic():
                if alert_id:
                    existing = Alert.objects.filter(alert_id=alert_id, source_index=src_idx).order_by('-id').first()
                    if existing:
                        for k, v in defaults.items():
                            setattr(existing, k, v)
                        existing.save(update_fields=list(defaults.keys()))
                    else:
                        Alert.objects.create(alert_id=alert_id, **defaults)
        except (IntegrityError, DatabaseError):
            logger.exception('DB upsert failed for alert_id=%s', doc.get('alert_id'))
        except Exception:
            logger.exception('Unexpected upsert error for alert_id=%s', doc.get('alert_id'))


def _detect_es_major_version(host_url: str, timeout: int = 5) -> int:
    """Try a simple HTTP GET to the ES host root and parse version number.

    Returns the major version (int) or a sensible default (8) on failure.
    """
    try:
        if not host_url.startswith('http'):
            host_url = 'http://' + host_url
        if not host_url.endswith('/'):
            host_url = host_url + '/'
        resp = requests.get(host_url, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        ver = data.get('version', {}).get('number')
        if ver:
            major = int(str(ver).split('.')[0])
            return major
    except Exception as e:
        logger.debug('ES version detection failed for %s: %s', host_url, e)
    return 8  # sensible default


def _detect_python_es_client_major_version() -> int | None:
    """Best-effort detect the installed `elasticsearch` Python client's major version."""
    try:
        import elasticsearch as es_mod

        ver = getattr(es_mod, '__version__', None)
        if isinstance(ver, tuple) and ver:
            return int(ver[0])
        if isinstance(ver, str) and ver:
            return int(ver.split('.')[0])
    except Exception:
        return None
    return None


def _get_es_headers(cfg: ESIntegrationConfig) -> dict:
    """Generate headers for Elasticsearch requests, including Authorization."""
    headers = {}
    if cfg.username and cfg.password:
        credentials = f"{cfg.username}:{cfg.password}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()
        headers["Authorization"] = f"Basic {encoded_credentials}"
    headers["Content-Type"] = "application/json"
    headers["Accept"] = "application/json"
    return headers


def _get_http_timeouts(default_read_timeout: int) -> tuple[float, float]:
    """Return (connect_timeout, read_timeout) for requests."""
    try:
        connect_timeout = float(os.getenv('ES_HTTP_CONNECT_TIMEOUT_SECONDS', '5'))
    except Exception:
        connect_timeout = 5.0
    try:
        read_timeout = float(os.getenv('ES_HTTP_READ_TIMEOUT_SECONDS', str(default_read_timeout)))
    except Exception:
        read_timeout = float(default_read_timeout)
    return connect_timeout, read_timeout


def _http_search(cfg: ESIntegrationConfig, body: dict, timeout: int = 30) -> List[Dict]:
    """Perform a direct HTTP POST to ES _search.

    Uses `requests` for better timeout/retry behavior than urllib.
    """
    hosts = cfg.hosts_list()
    if not hosts:
        return []
    host = hosts[0]
    if not host.startswith('http'):
        host = 'http://' + host
    if host.endswith('/'):
        host = host[:-1]
    url = f"{host}/{cfg.index}/_search"

    headers = _get_es_headers(cfg)
    auth = (cfg.username, cfg.password) if cfg.username and cfg.password else None

    connect_timeout, read_timeout = _get_http_timeouts(timeout)
    retries = 0
    try:
        retries = int(os.getenv('ES_HTTP_RETRIES', '2'))
    except Exception:
        retries = 2

    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = requests.post(
                url,
                headers=headers,
                json=body,
                auth=auth,
                timeout=(connect_timeout, read_timeout),
                verify=bool(getattr(cfg, 'verify_certs', True)),
            )
            resp.raise_for_status()
            res = resp.json()
            hits = []
            for h in res.get('hits', {}).get('hits', []):
                doc = h.get('_source', {}) or {}
                if isinstance(doc, dict):
                    doc = {**doc, '_es_id': h.get('_id'), 'source_index': h.get('_index') or cfg.index}
                hits.append(doc)
            logger.info(
                'HTTP _search succeeded (attempt=%d url=%s timeout=%ss), returned %d hits',
                attempt + 1,
                url,
                read_timeout,
                len(hits),
            )
            return hits
        except requests.Timeout as e:
            last_exc = e
            logger.warning('HTTP _search timeout (attempt=%d/%d url=%s timeout=%ss)', attempt + 1, retries + 1, url, read_timeout)
        except requests.HTTPError as e:
            # no point retrying most HTTP errors; log response for debugging
            logger.error('HTTP _search failed (status=%s url=%s): %s', getattr(e.response, 'status_code', None), url, e)
            try:
                logger.error('HTTP _search response body: %s', (e.response.text or '')[:2000])
            except Exception:
                pass
            return []
        except requests.RequestException as e:
            last_exc = e
            logger.warning('HTTP _search request error (attempt=%d/%d url=%s): %s', attempt + 1, retries + 1, url, e)

        # small backoff between retries
        if attempt < retries:
            time.sleep(min(0.5 * (attempt + 1), 2.0))

    if last_exc:
        logger.exception('HTTP _search failed after retries (url=%s): %s', url, last_exc)
    return []


def _index_has_field(cfg: ESIntegrationConfig, field: str, timeout: int = 5) -> bool:
    """Check the index mapping to see if a top-level field exists (best-effort).

    Returns True if mapping indicates the field exists, False otherwise.
    """
    hosts = cfg.hosts_list()
    if not hosts:
        return False
    host = hosts[0]
    if not host.startswith('http'):
        host = 'http://' + host
    if host.endswith('/'):
        host = host[:-1]
    url = f"{host}/{cfg.index}/_mapping"
    headers = _get_es_headers(cfg)
    auth = (cfg.username, cfg.password) if cfg.username and cfg.password else None
    connect_timeout, read_timeout = _get_http_timeouts(timeout)
    try:
        resp = requests.get(url, headers=headers, auth=auth, timeout=(connect_timeout, read_timeout), verify=bool(getattr(cfg, 'verify_certs', True)))
        resp.raise_for_status()
        data = resp.json()
        # mapping structure may vary; search for the field name in mapping properties
        def find_field(d):
            if isinstance(d, dict):
                for k, v in d.items():
                    if k == field:
                        return True
                    if isinstance(v, dict):
                        if find_field(v):
                            return True
            return False

        return find_field(data)
    except Exception as e:
        logger.debug('Failed to fetch mapping for %s/%s: %s', cfg.hosts_list(), cfg.index, e)
        return False


def _detect_timestamp_field(cfg: ESIntegrationConfig, candidates=None) -> str:
    """Return the first matching timestamp-like field name present in the index mapping.

    Checks common candidates and returns the first found, or None if none found.
    """
    if candidates is None:
        candidates = ['timestamp', '@timestamp', 'time', 'event_time']
    try:
        hosts = cfg.hosts_list() or []
        if not hosts:
            return None
        host = hosts[0]
        url = f"{host.rstrip('/')}/{cfg.index}/_mapping"
        headers = _get_es_headers(cfg)
        auth = (cfg.username, cfg.password) if cfg.username and cfg.password else None
        connect_timeout, read_timeout = _get_http_timeouts(5)
        resp = requests.get(url, headers=headers, auth=auth, timeout=(connect_timeout, read_timeout), verify=bool(getattr(cfg, 'verify_certs', True)))
        resp.raise_for_status()
        data = resp.json()
        # Convert to string and search for field names (best-effort)
        text = json.dumps(data)
        for c in candidates:
            if f'"{c}"' in text:
                return c
    except Exception as e:
        logger.debug('Failed to detect timestamp field for %s/%s: %s', cfg.hosts_list(), cfg.index, e)
    return None


def _resolve_timestamp_sort_field(cfg: ESIntegrationConfig, detected_field: str, candidates=None) -> str:
    """Return a field name usable for sorting/aggregation.

    Prefers a field mapped as 'date' (or date_nanos), otherwise a '.keyword' subfield if present.
    Returns None if no sortable field is found.
    """
    if candidates is None:
        # ensure detected_field is first
        candidates = [detected_field] if detected_field else []
        for c in ['@timestamp', 'timestamp', 'time', 'event_time']:
            if c not in candidates:
                candidates.append(c)

    # fetch mapping
    hosts = cfg.hosts_list() or []
    if not hosts:
        return None
    host = hosts[0]
    try:
        url = f"{host.rstrip('/')}/{cfg.index}/_mapping"
        headers = _get_es_headers(cfg)
        auth = (cfg.username, cfg.password) if cfg.username and cfg.password else None
        connect_timeout, read_timeout = _get_http_timeouts(5)
        resp = requests.get(url, headers=headers, auth=auth, timeout=(connect_timeout, read_timeout), verify=bool(getattr(cfg, 'verify_certs', True)))
        resp.raise_for_status()
        mapping = resp.json()
    except Exception as e:
        logger.debug('Failed to fetch mapping for resolving timestamp field: %s', e)
        return None

    def search_properties(obj, prefix=''):
        # obj is a mapping dict; look into 'properties'
        props = obj.get('properties') if isinstance(obj, dict) else None
        if not props:
            return None
        for name, meta in props.items():
            full = f"{prefix}{name}" if prefix == '' else f"{prefix}.{name}"
            # check if this name is one of candidates
            if name in candidates:
                ftype = meta.get('type')
                if ftype and ftype.startswith('date'):
                    return full
                # check for keyword subfield
                fields = meta.get('fields') or {}
                if 'keyword' in fields:
                    return f"{full}.keyword"
            # recurse into nested properties
            nested = search_properties(meta, full)
            if nested:
                return nested
        return None

    # mapping may have index -> mappings -> properties or older shapes
    # try several likely roots
    roots = []
    if isinstance(mapping, dict):
        for k, v in mapping.items():
            roots.append(v.get('mappings') or v)
    else:
        roots = [mapping]

    for r in roots:
        res = search_properties(r)
        if res:
            return res
    return None


def _get_source_field_value(doc: Dict, field: str):
    """Get value from document _source for a field name, handling '.keyword' by using base field."""
    if not isinstance(doc, dict):
        return None
    base = field.split('.')[0]
    return doc.get(base)


class AlertService:
    @staticmethod
    def load_mock_alerts() -> List[Dict]:
        with open(MOCK_FILE) as f:
            data = json.load(f)
        return data

    @staticmethod
    def _build_es_client(cfg: ESIntegrationConfig):
        if not Elasticsearch:
            return None
        hosts = cfg.hosts_list()
        auth = None
        if cfg.username:
            auth = (cfg.username, cfg.password)

        # determine compatible media type based on ES server version
        compat_version = 8
        if hosts:
            try:
                compat_version = _detect_es_major_version(hosts[0])
                # cap to 8 to avoid sending unsupported compatible-with values (some clusters reject >8)
                if compat_version and isinstance(compat_version, int):
                    compat_version = min(compat_version, 8)
            except Exception:
                compat_version = 8

        media_type = f"application/vnd.elasticsearch+json; compatible-with={compat_version}"
        default_headers = {"Accept": media_type, "Content-Type": media_type}

        # adapt to elasticsearch client versions
        try:
            sig = inspect.signature(Elasticsearch.__init__)
            params = sig.parameters
            init_args = {}
            if 'basic_auth' in params:
                if auth:
                    init_args['basic_auth'] = auth
            elif 'http_auth' in params:
                if auth:
                    init_args['http_auth'] = auth
            # pass headers if client supports it
            if 'headers' in params:
                init_args['headers'] = default_headers
            elif 'default_headers' in params:
                init_args['default_headers'] = default_headers

            client = Elasticsearch(hosts=hosts, **init_args)
            return client
        except Exception as e:
            logger.exception('Failed to build Elasticsearch client: %s', e)
            try:
                return Elasticsearch(hosts=hosts)
            except Exception:
                return None

    @staticmethod
    def list_alerts(
        force_es: bool = False,
        force_mock: bool = False,
        force_db: bool = False,
    ) -> Tuple[List[Dict], str]:
        """Return (alerts, source) where source is 'db', 'es', 'es-http' or 'mock'.

        Note: this method is instrumented with timing logs to help diagnose slow
        fetches; DB upserts are performed on a background thread to avoid
        blocking the response when talking to ES.
        """
        start_time = time.time()
        logger.info('list_alerts start force_es=%s force_db=%s', force_es, force_db)

        if force_mock:
            alerts = AlertService.load_mock_alerts()
            elapsed = int((time.time() - start_time) * 1000)
            logger.info('list_alerts mock return count=%d elapsed_ms=%d', len(alerts), elapsed)
            return alerts, 'mock'

        # Force DB means: never hit ES, return only cached DB rows (may be empty).
        if force_db:
            try:
                cached = list(
                    _alerts_queryset_for_active_index().order_by('-timestamp')[:100]
                )
                elapsed_ms = int((time.time() - start_time) * 1000)
                logger.info('list_alerts force_db cached_count=%d elapsed_ms=%d', len(cached), elapsed_ms)
                return [_serialize_alert_row(r) for r in cached], 'db'
            except Exception as e:
                logger.exception('DB read failed in force_db mode: %s', e)
                return [], 'db'

        if not force_es:
            try:
                cached = list(
                    _alerts_queryset_for_active_index().order_by('-timestamp')[:100]
                )
                if cached:
                    elapsed_ms = int((time.time() - start_time) * 1000)
                    logger.info('list_alerts db cache hit cached_count=%d elapsed_ms=%d', len(cached), elapsed_ms)
                    return [_serialize_alert_row(r) for r in cached], 'db'
            except Exception as e:
                logger.exception('DB read failed, falling back to ES/mock: %s', e)

        # Check ES config
        try:
            cfg = _get_global_es_config()
        except Exception:
            cfg = None

        if cfg and (cfg.enabled or force_es):
            # Prefer the python client when it's compatible; fall back to HTTP when needed.
            prefer_http = False
            hosts = []
            try:
                hosts = cfg.hosts_list() or []
            except Exception:
                hosts = []

            server_major = None
            if hosts:
                try:
                    server_major = _detect_es_major_version(hosts[0])
                    client_major = _detect_python_es_client_major_version()
                    # Only force HTTP when the installed python client is newer than the cluster.
                    if server_major and client_major and client_major > server_major:
                        logger.info(
                            'ES server major=%s, python client major=%s; preferring HTTP fallback to avoid media-type mismatch',
                            server_major,
                            client_major,
                        )
                        prefer_http = True
                except Exception:
                    # leave prefer_http as False; we'll attempt client then fallback
                    server_major = None

            # Determine the timestamp field to use (support different mappings)
            detected_ts = _detect_timestamp_field(cfg)
            # try to resolve a sortable field (date type or .keyword)
            resolved_sort_field = _resolve_timestamp_sort_field(cfg, detected_ts)
            include_sort = bool(resolved_sort_field)

            # Try using python client if available and not explicitly preferring HTTP
            if Elasticsearch and not prefer_http:
                es = AlertService._build_es_client(cfg)
                if es:
                    try:
                        body = {"size": 100, "query": {"match_all": {}}}
                        if include_sort and resolved_sort_field:
                            body['sort'] = [{resolved_sort_field: {"order": "desc"}}]
                        res = es.search(index=cfg.index, body=body, request_timeout=30)
                        hits = []
                        for h in res.get('hits', {}).get('hits', []):
                            doc = h.get('_source', {}) or {}
                            if isinstance(doc, dict):
                                doc = {**doc, '_es_id': h.get('_id'), 'source_index': h.get('_index') or cfg.index}
                            hits.append(doc)
                        elapsed_ms = int((time.time() - start_time) * 1000)
                        logger.info('Fetched %d alerts from ES elapsed_ms=%d', len(hits), elapsed_ms)
                        try:
                            # perform DB upsert asynchronously to avoid adding latency to the response
                            threading.Thread(target=_upsert_docs_to_db, args=(hits,), daemon=True).start()
                        except Exception:
                            logger.exception('Best-effort DB upsert failed (source=es)')
                        return hits, 'es'
                    except Exception as e:
                        logger.exception('Elasticsearch query failed: %s', e)
                        # fallthrough to HTTP fallback below
            # HTTP fallback (either because client not available/failed, or server advised to prefer HTTP)
            try:
                body = {"size": 100, "query": {"match_all": {}}}
                if include_sort and resolved_sort_field:
                    body['sort'] = [{resolved_sort_field: {"order": "desc"}}]
                hits = _http_search(cfg, body, timeout=30)
                if hits:
                    elapsed_ms = int((time.time() - start_time) * 1000)
                    logger.info('Fetched %d alerts from ES via HTTP fallback elapsed_ms=%d', len(hits), elapsed_ms)
                    try:
                        threading.Thread(target=_upsert_docs_to_db, args=(hits,), daemon=True).start()
                    except Exception:
                        logger.exception('Best-effort DB upsert failed (source=es-http)')
                    return hits, 'es-http'
            except Exception as e2:
                logger.exception('HTTP fallback failed: %s', e2)

        alerts = AlertService.load_mock_alerts()
        return alerts, 'mock'

    @staticmethod
    def aggregate_dashboard(force_es: bool = False, force_mock: bool = False, force_db: bool = False) -> Dict:
        # Default behavior: keep existing fields for backward compatibility.
        # Additionally, compute richer dashboard metrics directly from Postgres so
        # counts are not limited to the latest 100 cached rows.

        alerts, source = AlertService.list_alerts(
            force_es=force_es,
            force_mock=force_mock,
            force_db=force_db,
        )

        severity_counts: Dict[str, int] = {}
        timeline: Dict[str, int] = {}
        source_index_counts: Dict[str, int] = {}
        daily_trend: Dict[str, int] = {}
        message_topN: Dict[str, int] = {}

        ts_field = None
        try:
            cfg = _get_global_es_config()
            if cfg:
                ts_field = _detect_timestamp_field(cfg) or 'timestamp'
        except Exception:
            ts_field = 'timestamp'

        for a in alerts:
            sev = a.get('severity', 'unknown')
            severity_counts[sev] = severity_counts.get(sev, 0) + 1
            # group by hour
            raw_ts = _get_source_field_value(a, ts_field) if isinstance(a, dict) else None
            hour = 'unknown'
            day = 'unknown'
            if raw_ts is None:
                hour = 'unknown'
                day = 'unknown'
            else:
                if isinstance(raw_ts, str):
                    try:
                        dt = datetime.fromisoformat(raw_ts.replace('Z', '+00:00'))
                        hour = dt.strftime('%Y-%m-%dT%H')
                        day = dt.strftime('%Y-%m-%d')
                    except Exception:
                        hour = raw_ts[:13]
                        day = raw_ts[:10]
                else:
                    try:
                        dt = datetime.fromisoformat(str(raw_ts).replace('Z', '+00:00'))
                        hour = dt.strftime('%Y-%m-%dT%H')
                        day = dt.strftime('%Y-%m-%d')
                    except Exception:
                        hour = 'unknown'
                        day = 'unknown'
            timeline[hour] = timeline.get(hour, 0) + 1
            daily_trend[day] = daily_trend.get(day, 0) + 1
            # 鎸?source_index 缁熻
            idx = a.get('source_index')
            if not idx:
                idx = a.get('_index', 'unknown')
            source_index_counts[idx] = source_index_counts.get(idx, 0) + 1
            # message 鏁存潯缁熻
            msg = a.get('message', '')
            if msg:
                message_topN[msg] = message_topN.get(msg, 0) + 1

        top_messages = dict(sorted(message_topN.items(), key=lambda x: x[1], reverse=True)[:20])

        # DB-based aggregates (preferred when DB is available)
        now = timezone.now()
        cutoff_1h = now - timedelta(hours=1)
        cutoff_trend = now - timedelta(days=7)

        total_alerts_db = None
        last_1h_alerts_db = None
        data_source_count_db = None
        enabled_rule_count_db = None
        detected_rule_count_1h_db = None
        category_counts_db: Dict[str, int] = {}
        severity_level_counts_db: Dict[str, int] = {}
        alert_trend_db: Dict[str, int] = {}
        alert_score_trend_db: Dict[str, int] = {}
        alert_trend_series_db: List[Dict[str, int | str]] = []
        alert_score_trend_series_db: List[Dict[str, int | str]] = []
        top_source_ips: List[Dict[str, int | str]] = []
        top_users: List[Dict[str, int | str]] = []
        top_sources: List[Dict[str, int | str]] = []
        top_rules: List[Dict[str, int | str]] = []

        try:
            qs = _alerts_queryset_for_active_index()
            total_alerts_db = qs.count()
            last_1h_alerts_db = qs.filter(timestamp__gte=cutoff_1h).count()
            data_source_count_db = qs.exclude(source_index__isnull=True).exclude(source_index='').values('source_index').distinct().count()
            enabled_rule_count_db = qs.exclude(rule_id__isnull=True).exclude(rule_id='').values('rule_id').distinct().count()
            detected_rule_count_1h_db = (
                qs.filter(timestamp__gte=cutoff_1h)
                .exclude(rule_id__isnull=True)
                .exclude(rule_id='')
                .values('rule_id')
                .distinct()
                .count()
            )

            # category pie
            for row in (
                qs.values('category')
                .annotate(c=Count('id'))
                .order_by('-c')[:20]
            ):
                k = row.get('category') or 'unknown'
                category_counts_db[str(k)] = int(row.get('c') or 0)

            def _severity_to_tier(raw: object) -> str:
                """Normalize raw severities into 4 tiers.

                Accepts:
                - common strings: critical/high/medium/low + variants (warn/info/fatal/error...)
                - numeric severities stored as strings/ints:
                  - 0-15 (e.g. Wazuh rule.level)
                  - 0-100 (some SIEM scores)
                """

                if raw is None:
                    return 'unknown'

                # numeric handling (int-like strings included)
                try:
                    if isinstance(raw, (int, float)):
                        n = int(raw)
                    else:
                        s0 = str(raw).strip()
                        if s0 and (s0.isdigit() or (s0.startswith('-') and s0[1:].isdigit())):
                            n = int(s0)
                        else:
                            n = None
                except Exception:
                    n = None

                if n is not None:
                    # Heuristic: treat <=15 as 0-15 scale; otherwise assume 0-100.
                    if n <= 15:
                        if n >= 12:
                            return 'critical'
                        if n >= 9:
                            return 'high'
                        if n >= 6:
                            return 'medium'
                        return 'low'
                    # 0-100-ish
                    if n >= 90:
                        return 'critical'
                    if n >= 70:
                        return 'high'
                    if n >= 40:
                        return 'medium'
                    return 'low'

                s = str(raw).strip().lower()
                # tolerate common variants / typos
                if s in {'critical', 'crtical', 'crit', 'fatal', 'emergency', 'emerg', 'panic'}:
                    return 'critical'
                if s in {'high', 'error', 'err', 'severe'}:
                    return 'high'
                if s in {'warning', 'warn', 'medium', 'med', 'moderate'}:
                    return 'medium'
                if s in {'info', 'informational', 'notice', 'low', 'debug'}:
                    return 'low'
                return 'unknown'

            tier_weight = {
                'critical': 4,
                'high': 3,
                'medium': 2,
                'low': 1,
                'unknown': 0,
            }

            # severity distribution (tiered)
            for row in (
                qs.values('severity')
                .annotate(c=Count('id'))
                .order_by('-c')
            ):
                tier = _severity_to_tier(row.get('severity'))
                severity_level_counts_db[tier] = severity_level_counts_db.get(tier, 0) + int(row.get('c') or 0)

            # alert trend (hour buckets, last 7d)
            for row in (
                qs.filter(timestamp__gte=cutoff_trend)
                .exclude(timestamp__isnull=True)
                .annotate(h=TruncHour('timestamp'))
                .values('h')
                .annotate(c=Count('id'))
                .order_by('h')
            ):
                h = row.get('h')
                if h is None:
                    continue
                alert_trend_db[h.isoformat(timespec='hours')] = int(row.get('c') or 0)

            # Build stacked series and score trend from a simple per-hour/per-severity rollup.
            per_hour_sev_rows = (
                qs.filter(timestamp__gte=cutoff_trend)
                .exclude(timestamp__isnull=True)
                .annotate(h=TruncHour('timestamp'))
                .values('h', 'severity')
                .annotate(c=Count('id'))
                .order_by('h')
            )

            counts_by_bucket: Dict[tuple[str, str], int] = {}
            score_by_hour: Dict[str, int] = {}
            score_by_bucket: Dict[tuple[str, str], int] = {}
            for row in per_hour_sev_rows:
                h = row.get('h')
                if h is None:
                    continue
                hour_key = h.isoformat(timespec='hours')
                tier = _severity_to_tier(row.get('severity'))
                c = int(row.get('c') or 0)

                counts_by_bucket[(hour_key, tier)] = counts_by_bucket.get((hour_key, tier), 0) + c
                tier_score = c * int(tier_weight.get(tier, 0))
                score_by_bucket[(hour_key, tier)] = score_by_bucket.get((hour_key, tier), 0) + tier_score
                score_by_hour[hour_key] = score_by_hour.get(hour_key, 0) + tier_score

            # Stacked series outputs
            for (hour_key, tier), c in sorted(counts_by_bucket.items()):
                alert_trend_series_db.append({'time': hour_key, 'series': tier, 'value': int(c)})
            for (hour_key, tier), s in sorted(score_by_bucket.items()):
                alert_score_trend_series_db.append({'time': hour_key, 'series': tier, 'value': int(s)})

            # Total score trend per hour
            for hour_key, s in sorted(score_by_hour.items()):
                alert_score_trend_db[hour_key] = int(s)

            # top sources (source_index)
            for row in (
                qs.exclude(source_index__isnull=True)
                .exclude(source_index='')
                .values('source_index')
                .annotate(c=Count('id'))
                .order_by('-c')[:10]
            ):
                top_sources.append({'name': row.get('source_index') or 'unknown', 'count': int(row.get('c') or 0)})

            # top rules (rule_id)
            for row in (
                qs.exclude(rule_id__isnull=True)
                .exclude(rule_id='')
                .values('rule_id')
                .annotate(c=Count('id'))
                .order_by('-c')[:10]
            ):
                top_rules.append({'name': row.get('rule_id') or 'unknown', 'count': int(row.get('c') or 0)})

            # For top IP/users we do best-effort extraction from JSON.
            # Use a bounded window to avoid full-table scans.
            recent_payloads = list(
                qs.order_by('-timestamp')
                .values_list('source_data', flat=True)[:5000]
            )
            ip_counts: Dict[str, int] = {}
            user_counts: Dict[str, int] = {}
            ip_keys = ['source_ip', 'src_ip', 'client_ip']
            user_keys = ['username', 'user', 'user_name', 'account', 'user_id', 'src_user']
            for payload in recent_payloads:
                if not isinstance(payload, dict):
                    continue
                ip_val = None
                for k in ip_keys:
                    v = payload.get(k)
                    if v:
                        ip_val = str(v)
                        break
                if ip_val:
                    ip_counts[ip_val] = ip_counts.get(ip_val, 0) + 1

                user_val = None
                for k in user_keys:
                    v = payload.get(k)
                    if v:
                        user_val = str(v)
                        break
                if user_val:
                    user_counts[user_val] = user_counts.get(user_val, 0) + 1

            for name, c in sorted(ip_counts.items(), key=lambda x: x[1], reverse=True)[:10]:
                top_source_ips.append({'name': name, 'count': int(c)})
            for name, c in sorted(user_counts.items(), key=lambda x: x[1], reverse=True)[:10]:
                top_users.append({'name': name, 'count': int(c)})
        except Exception:
            logger.exception('DB aggregate_dashboard failed; using limited in-memory aggregates')

        return {
            # existing keys
            'severity': severity_counts,
            'timeline': timeline,
            'total': total_alerts_db if total_alerts_db is not None else len(alerts),
            'source': source,
            'source_index': source_index_counts,
            'daily_trend': daily_trend,
            'top_messages': top_messages,

            # new metrics requested
            'recent_1h_alerts': last_1h_alerts_db,
            'data_source_count': data_source_count_db,
            'enabled_siem_rule_count': enabled_rule_count_db,
            'siem_rule_detected_count_1h': detected_rule_count_1h_db,

            # new dashboard blocks
            'category_breakdown': category_counts_db,
            'severity_distribution': severity_level_counts_db,
            'alert_trend': alert_trend_db,
            'alert_score_trend': alert_score_trend_db,
            'alert_trend_series': alert_trend_series_db,
            'alert_score_trend_series': alert_score_trend_series_db,
            'top_source_ips': top_source_ips,
            'top_users': top_users,
            'top_sources': top_sources,
            'top_rules': top_rules,
        }

