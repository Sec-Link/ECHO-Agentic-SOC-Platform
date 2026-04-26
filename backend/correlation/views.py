from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from accounts.permissions import HasDjangoPermissions
from datetime import datetime, timedelta
from django.utils import timezone
from django.db.models import Count, Max
from .models import CorrelationPolicy, CorrelationEvent
from .serializers import CorrelationPolicySerializer
from alerts.models import Alert
try:
    from orchestrator.utils import seed_correlation_events
except Exception:
    def seed_correlation_events(*args, **kwargs):
        return {'created': 0, 'tickets': 0}


class CorrelationPolicyView(APIView):
    permission_classes = [IsAuthenticated, HasDjangoPermissions]
    required_permissions = {"GET": "correlation.view_correlationpolicy", "POST": "correlation.change_correlationpolicy"}

    def get(self, request):
        policy = CorrelationPolicy.objects.order_by('id').first()
        if not policy:
            policy = CorrelationPolicy.objects.create(
                enabled=False,
                window_minutes=30,
                match_keys=['threat_object', 'alert_type'],
                match_risk_object=True,
                match_detection_rule=True,
                match_source_ip=False,
                match_username=False,
                time_window_hours=8,
                match_action='attach',
                rules_expression={
                    'window_minutes': 30,
                    'order_by': ['threat_object', 'alert_type'],
                },
            )
        data = CorrelationPolicySerializer(policy).data
        return Response(data)

    def post(self, request):
        policy = CorrelationPolicy.objects.order_by('id').first()
        if not policy:
            policy = CorrelationPolicy.objects.create()
        serializer = CorrelationPolicySerializer(policy, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class CorrelationEventsView(APIView):
    permission_classes = [IsAuthenticated, HasDjangoPermissions]
    required_permissions = {"GET": "correlation.view_correlationevent"}

    def get(self, request):
        # Parse time range (fallback to last 1h)
        try:
            to_str = request.query_params.get('to')
            from_str = request.query_params.get('from')
            to_ts = datetime.fromisoformat(to_str.replace('Z','+00:00')) if to_str else timezone.now()
            from_ts = datetime.fromisoformat(from_str.replace('Z','+00:00')) if from_str else to_ts - timedelta(hours=1)
        except Exception:
            to_ts = timezone.now()
            from_ts = to_ts - timedelta(hours=1)

        bucket = request.query_params.get('bucket', '5m')
        seed_flag = str(request.query_params.get('seed', '')).lower() in ('1', 'true', 'yes')
        if seed_flag:
            user = getattr(request, "user", None)
            if not getattr(user, "is_superuser", False) and not user.has_perm("correlation.add_correlationevent"):
                return Response({"detail": "Permission denied."}, status=403)
        seed_result = None
        if seed_flag:
            try:
                seed_result = seed_correlation_events(
                    max_tickets=int(request.query_params.get('seed_tickets') or 20),
                    min_events=int(request.query_params.get('seed_min') or 2),
                    max_events=int(request.query_params.get('seed_max') or 5),
                    hours=int(request.query_params.get('seed_hours') or 6),
                )
            except Exception:
                seed_result = {'created': 0, 'tickets': 0}
        bucket_minutes = 5
        if bucket.endswith('m'):
            bucket_minutes = int(bucket[:-1])
        elif bucket.endswith('h'):
            bucket_minutes = int(bucket[:-1]) * 60
        bucket_seconds = bucket_minutes * 60
        series = []
        table = []

        alerts_qs = Alert.objects.filter(
            timestamp__isnull=False,
            timestamp__gte=from_ts,
            timestamp__lte=to_ts,
        ).exclude(
            ticket_number__isnull=True,
        ).exclude(
            ticket_number='',
        )
        ticket_rows = list(
            alerts_qs.values('ticket_number')
            .annotate(alert_count=Count('id'), last_alert_time=Max('timestamp'))
            .order_by('-last_alert_time')[:100]
        )
        for row in ticket_rows:
            top_alert = alerts_qs.filter(ticket_number=row['ticket_number']).order_by('-timestamp', '-id').first()
            ticket_alert_ids = list(
                alerts_qs.filter(ticket_number=row['ticket_number'])
                .exclude(alert_id__isnull=True)
                .exclude(alert_id='')
                .values_list('alert_id', flat=True)
            )
            table.append({
                'ticket_id': row['ticket_number'],
                'alert_count': row['alert_count'],
                'last_alert_time': row['last_alert_time'].isoformat() if row['last_alert_time'] else None,
                'top_threat_object': (top_alert.title if top_alert else None),
                'top_rule': (top_alert.rule_id if top_alert else None),
                'alert_ids': ticket_alert_ids,
            })

        bucket_map = {}
        for ts, ticket in alerts_qs.values_list('timestamp', 'ticket_number'):
            if ts is None:
                continue
            ts_utc = ts.astimezone(timezone.UTC).replace(tzinfo=None)
            bucket_time = datetime.utcfromtimestamp(
                (int(ts_utc.timestamp()) // bucket_seconds) * bucket_seconds
            )
            bucket_map.setdefault(bucket_time, set()).add(ticket)
        bucket_rows = [(k, len(v)) for k, v in bucket_map.items()]

        if not table:
            events = CorrelationEvent.objects.filter(occurred_at__range=(from_ts, to_ts)).order_by('occurred_at')
            bucket_map = {}
            for ev in events:
                ts = ev.occurred_at
                ts_utc = ts.astimezone(timezone.UTC).replace(tzinfo=None)
                bucket_time = datetime.utcfromtimestamp(
                    (int(ts_utc.timestamp()) // bucket_seconds) * bucket_seconds
                )
                bucket_map[bucket_time] = bucket_map.get(bucket_time, 0) + 1
            ticket_map = {}
            for ev in events:
                key = ev.ticket_id
                item = ticket_map.get(key)
                if not item:
                    item = {
                        'ticket_id': key,
                        'alert_count': 0,
                        'last_alert_time': None,
                        'top_threat_object': ev.threat_object,
                        'top_rule': None,
                        'alert_ids': [],
                    }
                    ticket_map[key] = item
                item['alert_count'] += len(ev.alert_ids or [])
                for aid in ev.alert_ids or []:
                    if aid not in item['alert_ids']:
                        item['alert_ids'].append(aid)
                if not item['last_alert_time'] or (ev.occurred_at and ev.occurred_at > item['last_alert_time']):
                    item['last_alert_time'] = ev.occurred_at
                    item['top_threat_object'] = ev.threat_object
                    item['top_rule'] = item.get('top_rule') or None
            table = []
            for row in ticket_map.values():
                table.append({
                    'ticket_id': row['ticket_id'],
                    'alert_count': row['alert_count'],
                    'last_alert_time': row['last_alert_time'].isoformat() if row['last_alert_time'] else None,
                    'top_threat_object': row['top_threat_object'],
                    'top_rule': row.get('top_rule'),
                    'alert_ids': row['alert_ids'],
                })
            table.sort(key=lambda r: r['last_alert_time'] or '', reverse=True)
            bucket_rows = [(k, v) for k, v in bucket_map.items()]

        bucket_map = {}
        for row in bucket_rows:
            bucket_time = row[0]
            if bucket_time and hasattr(bucket_time, 'tzinfo') and bucket_time.tzinfo is not None:
                bucket_time = bucket_time.astimezone(timezone.UTC).replace(tzinfo=None)
            bucket_map[bucket_time] = row[1]
        current = from_ts
        while current <= to_ts:
            current_utc = current.astimezone(timezone.UTC).replace(tzinfo=None)
            bucket_time = datetime.utcfromtimestamp(
                (int(current_utc.timestamp()) // bucket_seconds) * bucket_seconds
            )
            count = bucket_map.get(bucket_time, 0)
            series.append({'time': bucket_time.isoformat(), 'count': count})
            current = current + timedelta(minutes=bucket_minutes)
        payload = {'bucket': bucket, 'series': series, 'table': table}
        if seed_flag:
            payload['seeded'] = seed_result
        return Response(payload)
