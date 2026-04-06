"""API views for alerts, dashboard aggregation and ES integration config."""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import ESIntegrationConfig, WebhookConfig, ESIntegrationConfigHistory
from .serializers import (
    AlertSyncScheduleSerializer,
    ESIntegrationConfigHistorySerializer,
    ESIntegrationConfigSerializer,
    WebhookConfigSerializer,
)
from .services import AlertService, _detect_es_major_version, _http_search, _index_has_field
from .tasks import get_or_create_alert_sync_schedule, sync_es_alerts_to_db

logger = logging.getLogger(__name__)

ES_CONFIG_FIELDS = ['enabled', 'hosts', 'index', 'username', 'password', 'use_ssl', 'verify_certs']


def _boolify(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


def _parse_schedule_payload(raw: Any) -> Dict[str, Any] | None:
    if raw is None or raw == '':
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        txt = raw.strip()
        if not txt:
            return None
        try:
            parsed = json.loads(txt)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None
    return None


def _build_history_payload() -> list[Dict[str, Any]]:
    rows = ESIntegrationConfigHistory.objects.order_by('-last_used_at', '-id')[:20]
    return ESIntegrationConfigHistorySerializer(rows, many=True).data


def _upsert_history_from_config(cfg: ESIntegrationConfig) -> ESIntegrationConfigHistory:
    row = (
        ESIntegrationConfigHistory.objects.filter(
            hosts=cfg.hosts or '',
            index=cfg.index or 'alerts',
            username=cfg.username or '',
            use_ssl=bool(cfg.use_ssl),
            verify_certs=bool(cfg.verify_certs),
        )
        .order_by('-id')
        .first()
    )
    if row:
        update_fields = []
        if cfg.password and cfg.password != row.password:
            row.password = cfg.password
            update_fields.append('password')
        row.save(update_fields=update_fields + ['last_used_at'])
    else:
        row = ESIntegrationConfigHistory.objects.create(
            hosts=cfg.hosts or '',
            index=cfg.index or 'alerts',
            username=cfg.username or '',
            password=cfg.password or '',
            use_ssl=bool(cfg.use_ssl),
            verify_certs=bool(cfg.verify_certs),
        )

    stale_ids = list(
        ESIntegrationConfigHistory.objects.order_by('-last_used_at', '-id')
        .values_list('id', flat=True)[20:]
    )
    if stale_ids:
        ESIntegrationConfigHistory.objects.filter(id__in=stale_ids).delete()
    return row


def _build_es_config_response(cfg: ESIntegrationConfig | None, *, sync: Dict[str, Any] | None = None):
    schedule = get_or_create_alert_sync_schedule()
    payload = ESIntegrationConfigSerializer(cfg).data if cfg else {}
    payload['history'] = _build_history_payload()
    payload['schedule'] = AlertSyncScheduleSerializer(schedule).data
    if sync is not None:
        payload['sync'] = sync
    return payload


def _save_schedule(schedule_payload: Dict[str, Any] | None):
    if schedule_payload is None:
        return get_or_create_alert_sync_schedule()
    schedule = get_or_create_alert_sync_schedule()
    schedule.enabled = _boolify(schedule_payload.get('enabled'), default=schedule.enabled)
    try:
        interval = int(schedule_payload.get('interval_seconds', schedule.interval_seconds))
    except Exception:
        interval = schedule.interval_seconds
    try:
        batch = int(schedule_payload.get('batch_size', schedule.batch_size))
    except Exception:
        batch = schedule.batch_size
    schedule.interval_seconds = max(10, interval)
    schedule.batch_size = max(1, batch)
    schedule.fetch_all = _boolify(schedule_payload.get('fetch_all'), default=schedule.fetch_all)
    schedule.save(
        update_fields=['enabled', 'interval_seconds', 'batch_size', 'fetch_all', 'updated_at']
    )
    return schedule


class AlertListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        alerts, source = AlertService.list_alerts(force_db=True)
        page = int(request.GET.get('page', 1))
        page_size = int(request.GET.get('page_size', 20))
        start = (page - 1) * page_size
        end = start + page_size
        resp = {
            'alerts': alerts[start:end],
            'page': page,
            'page_size': page_size,
            'total': len(alerts),
            'source': source,
        }
        if source == 'mock' and len(alerts) == 0:
            try:
                sample_alerts = AlertService.load_mock_alerts()
                resp['mock_total_available'] = len(sample_alerts)
            except Exception:
                pass
        return Response(resp)


class AlertDashboardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            data = AlertService.aggregate_dashboard(force_db=True)
            return Response(data)
        except Exception as exc:
            logger.exception('Error in dashboard_alerts: %s', exc)
            return Response(
                {'error': 'Internal Server Error'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class AlertSyncView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        schedule = get_or_create_alert_sync_schedule()
        size_param = request.GET.get('size')
        fetch_all_param = request.GET.get('fetch_all')
        try:
            size = int(size_param) if size_param is not None else int(schedule.batch_size or 100)
        except Exception:
            size = int(schedule.batch_size or 100)
        fetch_all = (
            _boolify(fetch_all_param)
            if fetch_all_param is not None
            else bool(schedule.fetch_all)
        )

        started = time.monotonic()
        try:
            result = sync_es_alerts_to_db(size=size, fetch_all=fetch_all, force_config=True)
            result['duration_ms'] = int((time.monotonic() - started) * 1000)
            return Response({'ok': True, **(result or {})})
        except Exception as exc:
            logger.exception('Failed to sync ES->DB: %s', exc)
            return Response(
                {'ok': False, 'detail': str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ESConfigView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        cfg = ESIntegrationConfig.objects.order_by('-id').first()
        return Response(_build_es_config_response(cfg))

    def post(self, request):
        incoming = dict(request.data.copy() if hasattr(request.data, 'copy') else request.data)
        schedule_payload = _parse_schedule_payload(incoming.pop('schedule', None))
        history_id_raw = incoming.pop('history_id', None)
        if isinstance(history_id_raw, list):
            history_id_raw = history_id_raw[0] if history_id_raw else None

        history_row = None
        if history_id_raw not in (None, ''):
            try:
                history_id = int(history_id_raw)
            except Exception:
                return Response({'detail': 'history_id must be an integer'}, status=400)
            history_row = ESIntegrationConfigHistory.objects.filter(id=history_id).first()
            if not history_row:
                return Response({'detail': 'history connector not found'}, status=404)
            for field in ['hosts', 'index', 'username', 'use_ssl', 'verify_certs']:
                if incoming.get(field) in (None, ''):
                    incoming[field] = getattr(history_row, field)

        if incoming.get('password') in (None, ''):
            if history_row and history_row.password:
                incoming['password'] = history_row.password
            else:
                incoming.pop('password', None)

        serializer = ESIntegrationConfigSerializer(data=incoming, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            cfg = ESIntegrationConfig.objects.order_by('-id').first()
            if cfg:
                for key, value in serializer.validated_data.items():
                    setattr(cfg, key, value)
                if serializer.validated_data:
                    cfg.save(update_fields=list(serializer.validated_data.keys()))
            else:
                base = {k: serializer.validated_data.get(k) for k in ES_CONFIG_FIELDS if k in serializer.validated_data}
                cfg = ESIntegrationConfig.objects.create(**base)

            ESIntegrationConfig.objects.exclude(id=cfg.id).delete()
            _upsert_history_from_config(cfg)
            _save_schedule(schedule_payload)

            sync_started = time.monotonic()
            sync_result = sync_es_alerts_to_db(
                size=1000,
                force_config=True,
                create_index_table_on_success=True,
                fetch_all=True,
            )
            sync_result['duration_ms'] = int((time.monotonic() - sync_started) * 1000)
            return Response(_build_es_config_response(cfg, sync=sync_result))
        except Exception as exc:
            logger.exception('Failed to save ES config or run sync: %s', exc)
            return Response(
                {'detail': str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class WebhookConfigView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        cfg = WebhookConfig.objects.order_by('-id').first()
        if not cfg:
            return Response({})
        return Response(WebhookConfigSerializer(cfg).data)

    def post(self, request):
        data = request.data.copy()
        serializer = WebhookConfigSerializer(data=data, partial=True)
        if serializer.is_valid():
            try:
                cfg = WebhookConfig.objects.order_by('-id').first()
                if cfg:
                    for key, value in serializer.validated_data.items():
                        setattr(cfg, key, value)
                    if serializer.validated_data:
                        cfg.save(update_fields=list(serializer.validated_data.keys()))
                else:
                    cfg = WebhookConfig.objects.create(**serializer.validated_data)
                WebhookConfig.objects.exclude(id=cfg.id).delete()
                return Response(WebhookConfigSerializer(cfg).data)
            except Exception as exc:
                return Response(
                    {'detail': str(exc)},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ESDiagnosticsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        cfg = ESIntegrationConfig.objects.order_by('-id').first()
        if not cfg:
            return Response({'es': False, 'detail': 'no config found'}, status=status.HTTP_200_OK)

        hosts = cfg.hosts_list() or []
        host = hosts[0] if hosts else None
        try:
            server_version = _detect_es_major_version(host) if host else None
        except Exception:
            server_version = None

        try:
            mapping_has_timestamp = _index_has_field(cfg, 'timestamp')
        except Exception:
            mapping_has_timestamp = False

        try:
            body = {'size': 5, 'query': {'match_all': {}}}
            if mapping_has_timestamp:
                body['sort'] = [{'timestamp': {'order': 'desc'}}]
            samples = _http_search(cfg, body, timeout=10)
        except Exception:
            samples = []

        return Response(
            {
                'es': True,
                'host': host,
                'server_version': server_version,
                'mapping_has_timestamp': mapping_has_timestamp,
                'sample_count': len(samples),
                'samples': samples,
            }
        )
