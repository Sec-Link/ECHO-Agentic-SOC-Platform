import csv
import io
import logging
from django.db import transaction
from django.http import HttpResponse
from rest_framework import viewsets, status, parsers
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.filters import SearchFilter, OrderingFilter
from rest_framework.pagination import PageNumberPagination
from .models import Asset, AssetColumn, AssetAuditLog
from .serializers import AssetSerializer, AssetColumnSerializer, AssetAuditLogSerializer
from .exporters import AssetExportService


logger = logging.getLogger(__name__)


class AssetPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 200


class AssetViewSet(viewsets.ModelViewSet):
    """
    CRUD for CMDB Assets.
    """
    queryset = Asset.objects.all().order_by('-updated_at')
    serializer_class = AssetSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = AssetPagination
    filter_backends = [SearchFilter, OrderingFilter]
    search_fields = ['asset_number', 'hostname', 'ip_address', 'description']
    ordering_fields = ['asset_number', 'hostname', 'ip_address', 'asset_type', 'asset_level', 'updated_at', 'created_at']

    # ---------- helpers ----------
    def _safe_details(self, data):
        """Convert data to a JSON-safe dict for audit log storage."""
        if isinstance(data, str):
            return {"info": data}
        try:
            return {k: str(v) for k, v in data.items()} if hasattr(data, 'items') else {"info": str(data)}
        except Exception:
            return {"info": str(data)}

    def _log_audit(self, instance, action_type, user, data):
        try:
            AssetAuditLog.objects.create(
                asset_number=instance.asset_number,
                action=action_type,
                user=user,
                details=self._safe_details(data),
            )
        except Exception as exc:
            # Audit logging should not break primary CMDB CRUD actions.
            logger.warning(
                "CMDB audit log skipped for asset %s (%s): %s",
                getattr(instance, 'asset_number', 'unknown'),
                action_type,
                exc,
            )

    # ---------- CRUD hooks ----------
    def perform_create(self, serializer):
        user = self.request.user if self.request.user.is_authenticated else None
        instance = serializer.save(created_by=user, updated_by=user)
        self._log_audit(instance, 'CREATE', user, serializer.validated_data)

    def perform_update(self, serializer):
        user = self.request.user if self.request.user.is_authenticated else None
        instance = serializer.save(updated_by=user)
        self._log_audit(instance, 'UPDATE', user, serializer.validated_data)

    def perform_destroy(self, instance):
        user = self.request.user if self.request.user.is_authenticated else None
        self._log_audit(instance, 'DELETE', user, AssetSerializer(instance).data)
        instance.delete()

    # ---------- CSV / Excel import ----------
    @action(detail=False, methods=['POST'], parser_classes=[parsers.MultiPartParser])
    def import_excel(self, request):
        """Import assets from a CSV file."""
        file_obj = request.FILES.get('file')
        if not file_obj:
            return Response({"error": "No file provided"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            raw = file_obj.read()
            try:
                text = raw.decode('utf-8-sig')
            except UnicodeDecodeError:
                text = raw.decode('latin1')

            reader = csv.DictReader(io.StringIO(text))

            standard_map = {
                'hostname': 'hostname', 'Hostname': 'hostname',
                'ip_address': 'ip_address', 'IP Address': 'ip_address',
                'asset_type': 'asset_type', 'Asset Type': 'asset_type',
                'asset_level': 'asset_level', 'Asset Level': 'asset_level',
                'description': 'description', 'Description': 'description',
                'is_alive': 'is_alive', 'Is Alive': 'is_alive',
            }

            created_count = 0
            updated_count = 0
            errors = []
            user = request.user if request.user.is_authenticated else None

            with transaction.atomic():
                for idx, row in enumerate(reader, start=2):
                    std = {}
                    custom = {}
                    for col_name, value in row.items():
                        if col_name is None:
                            continue
                        mapped = standard_map.get(col_name.strip())
                        if mapped:
                            std[mapped] = (value or '').strip()
                        else:
                            if value and str(value).strip():
                                custom[col_name.strip()] = value.strip()

                    is_alive_str = std.get('is_alive', '').lower()
                    is_alive = is_alive_str in ('true', 'yes', '1', 'alive')

                    obj = Asset(
                        hostname=std.get('hostname') or None,
                        ip_address=std.get('ip_address') or None,
                        asset_type=std.get('asset_type') or 'Unknown',
                        asset_level=std.get('asset_level') or 'Medium',
                        description=std.get('description') or '',
                        is_alive=is_alive,
                        custom_attributes=custom,
                        created_by=user,
                        updated_by=user,
                    )
                    obj.save()  # auto-generates asset_number
                    created_count += 1
                    self._log_audit(obj, 'IMPORT', user, f"Row {idx}")

            return Response({
                "message": "Import successful",
                "created": created_count,
                "updated": updated_count,
                "errors": errors,
            })

        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['GET'], url_path='export')
    def export_data(self, request):
        export_format = request.query_params.get('file_format', 'xlsx').lower()
        if export_format not in {'xlsx', 'csv'}:
            return Response(
                {"error": "Unsupported export format. Use 'xlsx' or 'csv'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        queryset = self.filter_queryset(self.get_queryset()).select_related('created_by', 'updated_by')
        exporter = AssetExportService(queryset)
        content = exporter.export(export_format)

        response = HttpResponse(content, content_type=exporter.get_content_type(export_format))
        response['Content-Disposition'] = f'attachment; filename="{exporter.get_filename(export_format)}"'
        return response


class AssetColumnViewSet(viewsets.ModelViewSet):
    """
    Manage custom column definitions.
    Returns a flat list (no pagination).
    """
    queryset = AssetColumn.objects.all().order_by('id')
    serializer_class = AssetColumnSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None


class AssetAuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only audit logs."""
    queryset = AssetAuditLog.objects.all().order_by('-timestamp')
    serializer_class = AssetAuditLogSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = AssetPagination
    filter_backends = [SearchFilter]
    search_fields = ['asset_number', 'action']
