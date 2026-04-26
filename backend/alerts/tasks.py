"""ES -> Postgres sync helpers.

Problem this fixes:
- The project is Django-based, but an older SQLAlchemy-based implementation lived here.
- Timestamp parsing didn't match ES payloads like `2025-12-16T12:00:00Z`.
- A background scheduler won't run unless explicitly started by Django.

Use `sync_es_alerts_to_db()` from a management command or an API endpoint.
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone as django_timezone

from .models import Alert, ESIntegrationConfig, AlertSyncSchedule
from .services import _http_search, _detect_timestamp_field, _resolve_timestamp_sort_field, _ensure_alert_identity

logger = logging.getLogger(__name__)


def _fetch_all_docs_via_scroll(
    cfg: ESIntegrationConfig,
    *,
    batch_size: int = 1000,
    scroll_ttl: str = '2m',
) -> tuple[List[Dict[str, Any]], List[str]]:
    """Fetch all docs from ES index via scroll API.

    Used for full sync after saving ES config so DB reflects index totals.
    """
    import requests

    docs: List[Dict[str, Any]] = []
    errors: List[str] = []

    hosts = cfg.hosts_list() if cfg else []
    if not hosts:
        return docs, ['scroll_error: ES hosts are empty']

    host = hosts[0]
    if not host.startswith('http'):
        host = f'http://{host}'
    host = host.rstrip('/')
    index_name = (cfg.index or '').strip()
    if not index_name:
        return docs, ['scroll_error: ES index is empty']

    search_url = f'{host}/{index_name}/_search'
    scroll_url = f'{host}/_search/scroll'
    auth = (cfg.username, cfg.password) if cfg.username and cfg.password else None
    verify = bool(getattr(cfg, 'verify_certs', True))

    try:
        connect_timeout = float(os.getenv('ES_HTTP_CONNECT_TIMEOUT_SECONDS', '5'))
    except Exception:
        connect_timeout = 5.0
    try:
        read_timeout = float(os.getenv('ES_HTTP_READ_TIMEOUT_SECONDS', '30'))
    except Exception:
        read_timeout = 30.0

    req_timeout = (connect_timeout, read_timeout)
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    scroll_id: Optional[str] = None

    try:
        body = {
            'size': max(1, min(batch_size, 5000)),
            'query': {'match_all': {}},
            # Scroll performs best with _doc order for deep pagination.
            'sort': ['_doc'],
        }
        resp = requests.post(
            f'{search_url}?scroll={scroll_ttl}',
            headers=headers,
            json=body,
            auth=auth,
            timeout=req_timeout,
            verify=verify,
        )
        resp.raise_for_status()
        payload = resp.json()
        scroll_id = payload.get('_scroll_id')

        while True:
            hits = payload.get('hits', {}).get('hits', []) or []
            if not hits:
                break
            for h in hits:
                doc = h.get('_source', {}) or {}
                if isinstance(doc, dict):
                    doc = {**doc, '_es_id': h.get('_id'), 'source_index': h.get('_index') or index_name}
                docs.append(doc)

            if not scroll_id:
                break
            scroll_resp = requests.post(
                scroll_url,
                headers=headers,
                json={'scroll': scroll_ttl, 'scroll_id': scroll_id},
                auth=auth,
                timeout=req_timeout,
                verify=verify,
            )
            scroll_resp.raise_for_status()
            payload = scroll_resp.json()
            scroll_id = payload.get('_scroll_id') or scroll_id
    except Exception as e:
        msg = f'scroll_error index={index_name}: {e}'
        errors.append(msg)
        logger.exception(msg)
    finally:
        if scroll_id:
            try:
                requests.delete(
                    scroll_url,
                    headers=headers,
                    json={'scroll_id': [scroll_id]},
                    auth=auth,
                    timeout=req_timeout,
                    verify=verify,
                )
            except Exception:
                # best effort cleanup
                pass

    return docs, errors


def _fetch_es_index_count(cfg: ESIntegrationConfig) -> tuple[Optional[int], List[str]]:
    import requests

    errors: List[str] = []
    hosts = cfg.hosts_list() if cfg else []
    if not hosts:
        return None, ['count_error: ES hosts are empty']
    host = hosts[0]
    if not host.startswith('http'):
        host = f'http://{host}'
    host = host.rstrip('/')
    index_name = (cfg.index or '').strip()
    if not index_name:
        return None, ['count_error: ES index is empty']

    auth = (cfg.username, cfg.password) if cfg.username and cfg.password else None
    verify = bool(getattr(cfg, 'verify_certs', True))
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    url = f'{host}/{index_name}/_count'

    try:
        connect_timeout = float(os.getenv('ES_HTTP_CONNECT_TIMEOUT_SECONDS', '5'))
    except Exception:
        connect_timeout = 5.0
    try:
        read_timeout = float(os.getenv('ES_HTTP_READ_TIMEOUT_SECONDS', '30'))
    except Exception:
        read_timeout = 30.0

    try:
        resp = requests.get(url, headers=headers, auth=auth, timeout=(connect_timeout, read_timeout), verify=verify)
        resp.raise_for_status()
        payload = resp.json() or {}
        count = payload.get('count')
        if isinstance(count, int):
            return count, errors
        try:
            return int(count), errors
        except Exception:
            return None, errors
    except Exception as e:
        msg = f'count_error index={index_name}: {e}'
        errors.append(msg)
        logger.exception(msg)
        return None, errors


def _sanitize_index_table_name(index: str) -> str:
    raw = (index or 'alerts').strip().lower()
    safe = re.sub(r'[^a-z0-9_]+', '_', raw).strip('_')
    if not safe:
        safe = 'alerts'
    if safe[0].isdigit():
        safe = f"idx_{safe}"
    return f"es_idx_{safe[:40]}"


def _create_or_update_index_table(index: str, docs: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not docs:
        return {"ok": False, "table": None, "imported": 0, "detail": "no docs"}

    imported = 0
    index_name = (index or '').strip() or 'alerts'
    for doc in docs:
        alert_id = doc.get('alert_id')
        if not alert_id:
            continue

        defaults = {
            'timestamp': _parse_es_timestamp(doc.get('timestamp')),
            'severity': doc.get('severity'),
            'message': doc.get('message'),
            'source_index': doc.get('source_index') or index_name,
            'rule_id': doc.get('rule_id'),
            'title': doc.get('title'),
            'status': _coerce_int(doc.get('status')),
            'description': doc.get('description'),
            'category': doc.get('category'),
            'source_data': doc,
        }
        Alert.objects.update_or_create(
            alert_id=alert_id,
            source_index=index_name,
            defaults=defaults,
        )
        imported += 1

    return {"ok": True, "table": "alerts_alert", "imported": imported}


def _log_es_diagnostics(cfg: ESIntegrationConfig, index: str) -> List[str]:
    import requests

    diagnostics: List[str] = []
    hosts = cfg.hosts_list() if cfg else []
    if not hosts:
        msg = 'diagnostic_error: ES hosts are empty'
        logger.error(msg)
        diagnostics.append(msg)
        return diagnostics

    host = hosts[0]
    if not host.startswith('http'):
        host = f'http://{host}'
    host = host.rstrip('/')
    auth = (cfg.username, cfg.password) if cfg and cfg.username and cfg.password else None

    try:
        root_resp = requests.get(host, auth=auth, timeout=(5, 10), verify=bool(getattr(cfg, 'verify_certs', True)))
        diagnostics.append(f'root_status={root_resp.status_code}')
        if root_resp.status_code >= 400:
            logger.error('ES diagnostic root request failed: host=%s status=%s body=%s', host, root_resp.status_code, (root_resp.text or '')[:800])
    except Exception as e:
        msg = f'root_connect_error={e}'
        diagnostics.append(msg)
        logger.exception('ES diagnostic root request error: host=%s', host)

    try:
        count_url = f'{host}/{index}/_count'
        count_resp = requests.get(count_url, auth=auth, timeout=(5, 12), verify=bool(getattr(cfg, 'verify_certs', True)))
        diagnostics.append(f'index_count_status={count_resp.status_code}')
        if count_resp.status_code >= 400:
            logger.error(
                'ES diagnostic index count failed: host=%s index=%s status=%s body=%s',
                host,
                index,
                count_resp.status_code,
                (count_resp.text or '')[:800],
            )
    except Exception as e:
        msg = f'index_count_connect_error={e}'
        diagnostics.append(msg)
        logger.exception('ES diagnostic index count request error: host=%s index=%s', host, index)

    return diagnostics


def _deduplicate_alerts_for_index(index_name: str | None) -> int:
    if not index_name:
        return 0
    seen: set[str] = set()
    delete_ids: List[int] = []
    rows = Alert.objects.filter(
        source_index=index_name,
    ).exclude(
        alert_id__isnull=True,
    ).exclude(
        alert_id='',
    ).order_by(
        'alert_id',
        '-timestamp',
        '-id',
    ).values(
        'id',
        'alert_id',
    )
    for row in rows:
        alert_id = row.get('alert_id')
        if not alert_id:
            continue
        if alert_id in seen:
            delete_ids.append(row['id'])
        else:
            seen.add(alert_id)
    if not delete_ids:
        return 0
    deleted, _ = Alert.objects.filter(id__in=delete_ids).delete()
    return int(deleted or 0)


def _backfill_missing_alert_ids(index_name: str | None = None, limit: int = 5000) -> int:
    qs = Alert.objects.filter(Q(alert_id__isnull=True) | Q(alert_id=''))
    if index_name:
        qs = qs.filter(source_index=index_name)
    rows = list(qs.order_by('id')[:limit])
    updated = 0
    for row in rows:
        doc = row.source_data if isinstance(row.source_data, dict) else {}
        aid = _ensure_alert_identity(doc, row.source_index or index_name)
        row.alert_id = aid
        # keep source_data identity aligned for future debugging
        if isinstance(doc, dict):
            doc['alert_id'] = aid
            row.source_data = doc
            row.save(update_fields=['alert_id', 'source_data'])
        else:
            row.save(update_fields=['alert_id'])
        updated += 1
    return updated


def _build_es_query(*, size: int, sort_field: Optional[str] = None) -> Dict[str, Any]:
    """Build a search body for ES sync.

    Uses newest-first sorting when a sortable timestamp/date field is known.
    """
    body: Dict[str, Any] = {'size': size, 'query': {'match_all': {}}}
    if sort_field:
        body['sort'] = [{sort_field: {'order': 'desc'}}]
    return body


def _parse_es_timestamp(value: Any) -> Optional[datetime]:
    """Parse timestamps like `2025-12-16T12:00:00Z` (with/without fractions)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str):
        value = str(value)
    raw = value.strip()
    if raw.endswith('Z'):
        raw = raw[:-1] + '+00:00'
    try:
        dt = datetime.fromisoformat(raw)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _coerce_int(value: Any) -> Optional[int]:
    if value is None or value == '':
        return None
    try:
        return int(value)
    except Exception:
        return None


def _get_env_es_config() -> Tuple[Optional[str], Optional[str], Optional[str], str]:
    host = os.getenv('ES_HOST')
    username = os.getenv('ES_USERNAME')
    password = os.getenv('ES_PASSWORD')
    index = os.getenv('ES_INDEX', 'alerts_test')
    return host, username, password, index


def _fetch_docs_from_es_via_env(size: int) -> List[Dict]:
    """Best-effort ES _search using env vars (dev/local fallback).

    Uses `requests` to get proper connect/read timeouts.
    """
    import requests

    host, username, password, index = _get_env_es_config()
    if not host:
        logger.warning('ES_HOST is not set; cannot fetch from ES')
        return []
    if not host.startswith('http'):
        host = 'http://' + host
    host = host.rstrip('/')

    url = f"{host}/{index}/_search"
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    auth = (username, password) if username and password else None

    # Env-based fallback cannot reliably inspect mapping first, so use an
    # optional env hint for newest-first ordering.
    sort_field = (os.getenv('ES_SYNC_SORT_FIELD') or '').strip() or None
    body = _build_es_query(size=size, sort_field=sort_field)

    try:
        connect_timeout = float(os.getenv('ES_HTTP_CONNECT_TIMEOUT_SECONDS', '5'))
        read_timeout = float(os.getenv('ES_HTTP_READ_TIMEOUT_SECONDS', '30'))
        resp = requests.post(url, headers=headers, json=body, auth=auth, timeout=(connect_timeout, read_timeout))
        resp.raise_for_status()
        res = resp.json()
        docs: List[Dict[str, Any]] = []
        for h in res.get('hits', {}).get('hits', []):
            doc = h.get('_source', {}) or {}
            if isinstance(doc, dict):
                doc = {**doc, '_es_id': h.get('_id'), 'source_index': h.get('_index') or index}
            docs.append(doc)
        return docs
    except requests.Timeout as e:
        logger.exception('ES timeout when fetching %s: %s', url, e)
    except requests.HTTPError as e:
        logger.exception('ES HTTPError when fetching %s: %s', url, e)
        try:
            logger.error('ES response body: %s', (e.response.text or '')[:2000])
        except Exception:
            pass
    except requests.RequestException as e:
        logger.exception('ES network error when fetching %s: %s', url, e)
    except Exception as e:
        logger.exception('ES unexpected error when fetching %s: %s', url, e)
    return []


def sync_es_alerts_to_db(
    *,
    size: int = 100,
    force_config: bool = False,
    create_index_table_on_success: bool = False,
    fetch_all: bool = False,
) -> Dict[str, Any]:
    """Fetch alerts from ES and upsert them into `alerts_alert`.

    Returns: {source, fetched, inserted, updated, skipped, errors}.
    """
    if size <= 0:
        size = 100

    docs: List[Dict] = []
    source = 'none'
    sort_field: Optional[str] = None
    index_name = 'alerts_test'
    es_total_count: Optional[int] = None

    cfg = None
    try:
        cfg = ESIntegrationConfig.objects.filter(enabled=True).order_by('-id').first()
        if not cfg:
            cfg = ESIntegrationConfig.objects.order_by('-id').first()
    except Exception:
        cfg = None

    if cfg and (cfg.enabled or force_config):
        index_name = cfg.index or index_name
        es_total_count, count_errors = _fetch_es_index_count(cfg)
        try:
            detected_ts = _detect_timestamp_field(cfg)
            sort_field = _resolve_timestamp_sort_field(cfg, detected_ts)
        except Exception:
            sort_field = None

        if fetch_all:
            docs, scroll_errors = _fetch_all_docs_via_scroll(cfg, batch_size=max(size, 1000))
            source = 'es-scroll(cfg)'
        else:
            body = _build_es_query(size=size, sort_field=sort_field)
            docs = _http_search(cfg, body)
            source = 'es-http(cfg)'
            scroll_errors = []
        errors = [*count_errors]
    else:
        docs = _fetch_docs_from_es_via_env(size=size)
        source = 'es-http(env)'
        scroll_errors = []
        _, _, _, env_index = _get_env_es_config()
        if env_index:
            index_name = env_index
        errors = []

    inserted = 0
    updated = 0
    skipped = 0
    if scroll_errors:
        errors.extend(scroll_errors)
    index_table: Dict[str, Any] | None = None
    backfilled_missing_ids = 0
    dedup_removed = 0

    def _run_cleanup_for_index() -> tuple[int, int]:
        local_backfilled_missing_ids = 0
        local_dedup_removed = 0

        try:
            local_backfilled_missing_ids = _backfill_missing_alert_ids(index_name=index_name)
            if local_backfilled_missing_ids:
                logger.info('Backfilled %d missing alert_id rows for index=%s', local_backfilled_missing_ids, index_name)
        except Exception as e:
            msg = f'backfill_missing_id_error index={index_name}: {e}'
            errors.append(msg)
            logger.exception(msg)

        try:
            local_dedup_removed = _deduplicate_alerts_for_index(index_name)
            if local_dedup_removed:
                logger.info('Deduplicated %d rows for index=%s', local_dedup_removed, index_name)
        except Exception as e:
            msg = f'dedup_error index={index_name}: {e}'
            errors.append(msg)
            logger.exception(msg)

        return local_backfilled_missing_ids, local_dedup_removed

    if not docs:
        logger.error('No ES docs fetched (source=%s index=%s). Running diagnostics.', source, index_name)
        if cfg:
            errors.extend(_log_es_diagnostics(cfg, index_name))
        backfilled_missing_ids, dedup_removed = _run_cleanup_for_index()
        db_total_count = Alert.objects.filter(source_index=index_name).exclude(alert_id__isnull=True).exclude(alert_id='').count()
        return {
            "source": source,
            "index": index_name,
            "es_total": es_total_count,
            "db_total": db_total_count,
            "fetched": 0,
            "inserted": 0,
            "updated": 0,
            "skipped": 0,
            "errors": errors,
            "index_table": index_table,
            "dedup_removed": dedup_removed,
            "backfilled_missing_ids": backfilled_missing_ids,
        }

    def _upsert_docs(doc_list: List[Dict]) -> None:
        nonlocal inserted, updated, skipped
        for doc in doc_list:
            try:
                alert_id = _ensure_alert_identity(doc, index_name)
                doc['alert_id'] = alert_id
                doc['source_index'] = index_name

                defaults = {
                    'timestamp': _parse_es_timestamp(doc.get('timestamp')),
                    'severity': doc.get('severity'),
                    'message': doc.get('message'),
                    'source_index': index_name,
                    'rule_id': doc.get('rule_id'),
                    'title': doc.get('title'),
                    'status': _coerce_int(doc.get('status')),
                    'description': doc.get('description'),
                    'category': doc.get('category'),
                    'source_data': {**doc, 'alert_id': alert_id},
                }

                with transaction.atomic():
                    if alert_id:
                        existing = Alert.objects.filter(alert_id=alert_id, source_index=index_name).order_by('-id').first()
                        if existing:
                            for k, v in defaults.items():
                                setattr(existing, k, v)
                            existing.save(update_fields=list(defaults.keys()))
                            created = False
                        else:
                            Alert.objects.create(alert_id=alert_id, **defaults)
                            created = True

                inserted += 1 if created else 0
                updated += 0 if created else 1
            except (IntegrityError, DatabaseError) as db_err:
                skipped += 1
                msg = f"db_error alert_id={doc.get('alert_id')}: {db_err}"
                errors.append(msg)
                logger.exception(msg)
            except Exception as e:
                skipped += 1
                msg = f"unexpected_error alert_id={doc.get('alert_id')}: {e}"
                errors.append(msg)
                logger.exception(msg)

    _upsert_docs(docs)

    logger.info(
        'ES->DB sync done (source=%s index=%s fetched=%d inserted=%d updated=%d skipped=%d)',
        source,
        index_name,
        len(docs),
        inserted,
        updated,
        skipped,
    )
    if create_index_table_on_success and (inserted > 0 or updated > 0):
        try:
            index_table = _create_or_update_index_table(index_name, docs)
            logger.info(
                'Index mirror table updated: index=%s table=%s imported=%s',
                index_name,
                index_table.get('table'),
                index_table.get('imported'),
            )
        except Exception as e:
            msg = f'index_table_error index={index_name}: {e}'
            errors.append(msg)
            logger.exception(msg)
    backfilled_missing_ids, dedup_removed = _run_cleanup_for_index()
    db_total_count = Alert.objects.filter(source_index=index_name).exclude(alert_id__isnull=True).exclude(alert_id='').count()

    # One retry pass for full sync if DB still trails ES count.
    if fetch_all and cfg and es_total_count is not None and db_total_count < es_total_count:
        logger.warning(
            'Full sync mismatch detected, retrying once. index=%s es_total=%s db_total=%s',
            index_name,
            es_total_count,
            db_total_count,
        )
        retry_docs, retry_errors = _fetch_all_docs_via_scroll(cfg, batch_size=max(size, 1000), scroll_ttl='5m')
        if retry_errors:
            errors.extend(retry_errors)
        if retry_docs:
            _upsert_docs(retry_docs)
            backfilled_missing_ids_2, dedup_removed_2 = _run_cleanup_for_index()
            backfilled_missing_ids += backfilled_missing_ids_2
            dedup_removed += dedup_removed_2
            db_total_count = Alert.objects.filter(source_index=index_name).exclude(alert_id__isnull=True).exclude(alert_id='').count()
        if db_total_count < es_total_count:
            errors.append(
                f'sync_mismatch index={index_name}: es_total={es_total_count} db_total={db_total_count}'
            )

    return {
        'source': source,
        'index': index_name,
        'es_total': es_total_count,
        'db_total': db_total_count,
        'fetched': len(docs),
        'inserted': inserted,
        'updated': updated,
        'skipped': skipped,
        'errors': errors[:10],
        'index_table': index_table,
        'dedup_removed': dedup_removed,
        'backfilled_missing_ids': backfilled_missing_ids,
    }


def get_or_create_alert_sync_schedule() -> AlertSyncSchedule:
    """Return the latest schedule row, creating defaults when absent."""
    schedule = AlertSyncSchedule.objects.order_by('-id').first()
    if schedule:
        return schedule
    return AlertSyncSchedule.objects.create(
        enabled=False,
        interval_seconds=300,
        batch_size=500,
        fetch_all=False,
    )


def run_alert_sync_by_schedule(*, force: bool = False) -> Dict[str, Any]:
    """Execute one sync using saved schedule settings and update run status."""
    schedule = get_or_create_alert_sync_schedule()
    if not schedule.enabled and not force:
        return {
            'ok': False,
            'skipped': True,
            'reason': 'schedule_disabled',
            'schedule': {
                'enabled': schedule.enabled,
                'interval_seconds': schedule.interval_seconds,
                'batch_size': schedule.batch_size,
                'fetch_all': schedule.fetch_all,
            },
        }

    started = time.monotonic()
    try:
        result = sync_es_alerts_to_db(
            size=max(1, int(schedule.batch_size or 500)),
            force_config=True,
            create_index_table_on_success=True,
            fetch_all=bool(schedule.fetch_all),
        )
        duration_ms = int((time.monotonic() - started) * 1000)
        result['duration_ms'] = duration_ms

        errors = result.get('errors') or []
        schedule.last_run_at = django_timezone.now()
        schedule.last_status = 'success' if not errors else 'partial'
        schedule.last_error = '\n'.join(str(x) for x in errors[:5])
        schedule.save(update_fields=['last_run_at', 'last_status', 'last_error', 'updated_at'])
        return {'ok': True, **result}
    except Exception as exc:
        schedule.last_run_at = django_timezone.now()
        schedule.last_status = 'failed'
        schedule.last_error = str(exc)
        schedule.save(update_fields=['last_run_at', 'last_status', 'last_error', 'updated_at'])
        raise

