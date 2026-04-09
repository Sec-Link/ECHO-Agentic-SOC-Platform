import hashlib
import hmac
from datetime import datetime, timezone as dt_timezone
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from workflows.engine import execute_workflow
from workflows.models import Workflow

from .models import InterfaceEndpoint, InterfaceRequestLog
from .serializers import InterfaceEndpointSerializer, InterfaceRequestLogSerializer


class InterfaceEndpointViewSet(viewsets.ModelViewSet):
    serializer_class = InterfaceEndpointSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        queryset = InterfaceEndpoint.objects.filter(created_by=self.request.user)

        interface_type = self.request.query_params.get('interface_type')
        if interface_type:
            queryset = queryset.filter(interface_type=interface_type)

        is_active = self.request.query_params.get('is_active')
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == 'true')

        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(Q(name__icontains=search) | Q(description__icontains=search))

        return queryset

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['request'] = self.request
        return context

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=True, methods=['get'])
    def logs(self, request, pk=None):
        endpoint = self.get_object()
        logs = endpoint.logs.all()[:100]
        serializer = InterfaceRequestLogSerializer(logs, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def test(self, request, pk=None):
        endpoint = self.get_object()
        payload = dict(request.data) if isinstance(request.data, dict) else {'payload': request.data}
        result = dispatch_interface_payload(endpoint, payload, trigger_source='interface:test')

        InterfaceRequestLog.objects.create(
            endpoint=endpoint,
            method='TEST',
            source_ip=_extract_client_ip(request),
            response_status=status.HTTP_202_ACCEPTED,
            request_body=payload,
            response_body=result,
        )

        endpoint.last_event_at = timezone.now()
        endpoint.save(update_fields=['last_event_at'])

        return Response(result, status=status.HTTP_202_ACCEPTED)


def _extract_client_ip(request):
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def _extract_token(request):
    data_token = request.data.get('token') if isinstance(request.data, dict) else None
    return request.headers.get('X-Interface-Token') or request.query_params.get('token') or data_token


def _verify_hmac_if_enabled(endpoint: InterfaceEndpoint, request) -> bool:
    if not endpoint.hmac_secret:
        return True

    signature = request.headers.get('X-Interface-Signature', '')
    timestamp = request.headers.get('X-Interface-Timestamp', '')
    if not signature or not timestamp:
        return False

    try:
        ts = datetime.fromtimestamp(int(timestamp), tz=dt_timezone.utc)
    except (ValueError, TypeError):
        return False

    if abs((timezone.now() - ts).total_seconds()) > timedelta(minutes=5).total_seconds():
        return False

    body = request.body or b''
    signed = f"{timestamp}.".encode('utf-8') + body
    expected = hmac.new(
        endpoint.hmac_secret.encode('utf-8'),
        signed,
        hashlib.sha256,
    ).hexdigest()

    provided = signature.replace('sha256=', '')
    return hmac.compare_digest(expected, provided)


def dispatch_interface_payload(endpoint: InterfaceEndpoint, payload, trigger_source='interface:ingest'):
    workflow_qs = Workflow.objects.filter(
        is_active=True,
        trigger_type='webhook',
        trigger_conditions__webhook_source_id=str(endpoint.id),
    )

    executions = []
    for workflow in workflow_qs:
        execution = execute_workflow(
            workflow=workflow,
            trigger_data=payload,
            trigger_source=f"{trigger_source}:{endpoint.id}",
            executed_by=None,
        )
        executions.append(
            {
                'workflow_id': str(workflow.id),
                'workflow_name': workflow.name,
                'execution_id': str(execution.id),
            }
        )

    return {
        'status': 'accepted',
        'interface_id': str(endpoint.id),
        'execution_count': len(executions),
        'executions': executions,
    }


class InterfaceIngestView(APIView):
    permission_classes = [AllowAny]

    def _ingest(self, request, endpoint_id):
        try:
            endpoint = InterfaceEndpoint.objects.get(id=endpoint_id)
        except InterfaceEndpoint.DoesNotExist:
            return Response({'error': 'Interface endpoint not found'}, status=status.HTTP_404_NOT_FOUND)

        if not endpoint.is_active:
            return Response({'error': 'Interface endpoint is inactive'}, status=status.HTTP_400_BAD_REQUEST)

        provided_token = _extract_token(request)
        if not provided_token or provided_token != endpoint.secret_token:
            return Response({'error': 'Invalid interface token'}, status=status.HTTP_401_UNAUTHORIZED)

        if not _verify_hmac_if_enabled(endpoint, request):
            return Response({'error': 'Invalid or expired HMAC signature'}, status=status.HTTP_401_UNAUTHORIZED)

        payload = dict(request.data) if isinstance(request.data, dict) else {'payload': request.data}
        payload.pop('token', None)

        result = dispatch_interface_payload(endpoint, payload)

        InterfaceRequestLog.objects.create(
            endpoint=endpoint,
            method=request.method,
            source_ip=_extract_client_ip(request),
            response_status=status.HTTP_202_ACCEPTED,
            request_body=payload,
            response_body=result,
        )

        endpoint.last_event_at = timezone.now()
        endpoint.save(update_fields=['last_event_at'])

        return Response(result, status=status.HTTP_202_ACCEPTED)

    def post(self, request, endpoint_id):
        return self._ingest(request, endpoint_id)

    def put(self, request, endpoint_id):
        return self._ingest(request, endpoint_id)

    def patch(self, request, endpoint_id):
        return self._ingest(request, endpoint_id)


