from django.db.models import Avg
from datetime import datetime, time, timedelta
from django.utils import timezone
from django.utils.dateparse import parse_datetime, parse_date
from rest_framework import status, viewsets
import json
from copy import deepcopy
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import EventTicket, TicketSLA, TicketWorkLog, EventTicketAttachment, TicketHandleLog
from .serializers import (
    EventTicketSerializer,
    EventTicketListSerializer,
    TicketSLASerializer,
    TicketWorkLogSerializer,
    EventTicketAttachmentSerializer,
    TicketHandleLogSerializer,
)


from ai_assistant.assistant import AIAssistantError, generate_ai_assistant_output
from ai_assistant.models import ExternalMCPServer, TicketAIChatMessage
from ai_assistant.skill_config import get_enabled_skill_configs
from ai_assistant.serializers import AIAssistantRequestSerializer


class EventTicketViewSet(viewsets.ModelViewSet):
    """API ViewSet for managing EventTicket model."""

    permission_classes = [IsAuthenticated]
    lookup_field = "ticket_number"

    @staticmethod
    def _parse_dt(value, end_of_day=False):
        if not value:
            return None
        dt = parse_datetime(value)
        if dt is None:
            d = parse_date(value)
            if d:
                dt = datetime.combine(d, time.max if end_of_day else time.min)
        if dt is None:
            return None
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_current_timezone())
        return dt

    def get_queryset(self):
        qs = EventTicket.objects.select_related("sla", "assigned_user").filter(
            is_deleted=False
        )
        params = getattr(self.request, "query_params", {})
        created_from = self._parse_dt(params.get("created_from"))
        created_to = self._parse_dt(params.get("created_to"), end_of_day=True)

        if created_from:
            qs = qs.filter(created_time__gte=created_from)
        if created_to:
            qs = qs.filter(created_time__lte=created_to)

        if not created_from and not created_to:
            range_key = params.get("range")
            if range_key:
                now = timezone.now()
                start = None
                if range_key == "today":
                    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                elif range_key == "24h":
                    start = now - timedelta(hours=24)
                elif range_key == "7d":
                    start = now - timedelta(days=7)
                elif range_key == "30d":
                    start = now - timedelta(days=30)
                if start:
                    qs = qs.filter(created_time__gte=start, created_time__lte=now)
        return qs

    def get_serializer_class(self):
        if self.action == "list":
            return EventTicketListSerializer
        return EventTicketSerializer

    def perform_create(self, serializer):
        instance = serializer.save()
        TicketSLA.objects.get_or_create(ticket=instance)
        labels = getattr(instance, "labels", [])
        labels = labels if isinstance(labels, list) else []
        if labels:
            added = ", ".join(self._format_label_item(item) for item in labels)
            TicketHandleLog.objects.create(
                ticket=instance,
                handler=self.request.user,
                action_taken=f"Labels updated. Added: {added}",
            )

    def perform_update(self, serializer):
        before_labels = deepcopy(getattr(serializer.instance, "labels", []) or [])
        instance = serializer.save()

        # Only record handle logs for requests that actually touched labels.
        if "labels" not in serializer.validated_data:
            return

        after_labels = getattr(instance, "labels", []) or []
        changes = self._build_labels_change_message(before_labels, after_labels)
        if not changes:
            return

        TicketHandleLog.objects.create(
            ticket=instance,
            handler=self.request.user,
            action_taken=f"Labels updated. {changes}",
        )

    @staticmethod
    def _format_label_item(item):
        name = str(item.get("label_name", "")).strip() if isinstance(item, dict) else ""
        raw_value = item.get("label_value", "") if isinstance(item, dict) else ""
        value = "" if raw_value is None else str(raw_value).strip()
        return f"{name}:{value}"

    def _build_labels_change_message(self, before_labels, after_labels):
        def as_pair_set(labels):
            pairs = set()
            for raw in labels:
                if not isinstance(raw, dict):
                    continue
                name = str(raw.get("label_name", "")).strip()
                raw_value = raw.get("label_value", "")
                value = "" if raw_value is None else str(raw_value).strip()
                if not name:
                    continue
                pairs.add((name, value))
            return pairs

        before_set = as_pair_set(before_labels)
        after_set = as_pair_set(after_labels)

        added = sorted(after_set - before_set)
        removed = sorted(before_set - after_set)

        parts = []
        if added:
            parts.append("Added: " + ", ".join(f"{k}:{v}" for k, v in added))
        if removed:
            parts.append("Removed: " + ", ".join(f"{k}:{v}" for k, v in removed))

        return " | ".join(parts)

    @action(detail=True, methods=["post"])
    def update_status(self, request, ticket_number=None):
        ticket = self.get_object()
        new_status = request.data.get("status")
        notes = request.data.get("notes", "")

        valid_statuses = [choice[0] for choice in EventTicket.STATUS_CHOICES]
        if new_status not in valid_statuses:
            return Response(
                {
                    "error": f"Invalid status. Must be one of: {', '.join(valid_statuses)}",
                    "valid_statuses": valid_statuses,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        old_status = ticket.status
        ticket.status = new_status
        ticket.save()

        if notes:
            TicketWorkLog.objects.create(
                ticket=ticket,
                log_entry=f"Status changed from '{old_status}' to '{new_status}': {notes}",
                created_by=request.user,
            )
        else:
            TicketWorkLog.objects.create(
                ticket=ticket,
                log_entry=f"Status changed from '{old_status}' to '{new_status}'",
                created_by=request.user,
            )

        serializer = self.get_serializer(ticket)
        return Response(serializer.data)

    @action(detail=True, methods=["post"])
    def resolve(self, request, ticket_number=None):
        ticket = self.get_object()
        event_category = request.data.get("event_category")
        event_result = request.data.get("event_result")
        notes = request.data.get("notes", "")

        if event_category:
            valid_categories = [choice[0] for choice in EventTicket.EVENT_CATEGORY_CHOICES]
            if event_category not in valid_categories:
                return Response(
                    {
                        "error": f"Invalid event_category. Must be one of: {', '.join(valid_categories)}",
                        "valid_categories": valid_categories,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        if event_result:
            valid_results = [choice[0] for choice in EventTicket.EVENT_RESULT_CHOICES]
            if event_result not in valid_results:
                return Response(
                    {
                        "error": f"Invalid event_result. Must be one of: {', '.join(valid_results)}",
                        "valid_results": valid_results,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        old_status = ticket.status

        if event_category:
            ticket.event_category = event_category
        if event_result:
            ticket.event_result = event_result

        ticket.status = "resolved"
        ticket.save()

        log_parts = [f"Status changed from '{old_status}' to 'resolved'"]
        if event_category:
            category_display = dict(EventTicket.EVENT_CATEGORY_CHOICES).get(
                event_category, event_category
            )
            log_parts.append(f"Event Category: {category_display}")
        if event_result:
            result_display = dict(EventTicket.EVENT_RESULT_CHOICES).get(
                event_result, event_result
            )
            log_parts.append(f"Event Result: {result_display}")
        if notes:
            log_parts.append(f"Notes: {notes}")

        TicketWorkLog.objects.create(
            ticket=ticket,
            log_entry="\n".join(log_parts),
            created_by=request.user,
        )

        serializer = self.get_serializer(ticket)
        return Response(serializer.data)

    @action(detail=False, methods=["get"])
    def field_choices(self, request):
        return Response(
            {
                "status_choices": [
                    {"value": choice[0], "label": choice[1]}
                    for choice in EventTicket.STATUS_CHOICES
                ],
                "priority_choices": [
                    {"value": choice[0], "label": choice[1]}
                    for choice in EventTicket.PRIORITY_CHOICES
                ],
                "event_category_choices": [
                    {"value": choice[0], "label": choice[1]}
                    for choice in EventTicket.EVENT_CATEGORY_CHOICES
                ],
                "event_result_choices": [
                    {"value": choice[0], "label": choice[1]}
                    for choice in EventTicket.EVENT_RESULT_CHOICES
                ],
            }
        )

    def sla_metrics(self, request, ticket_number=None):
        ticket = self.get_object()
        try:
            sla = ticket.sla
            sla_serializer = TicketSLASerializer(sla)
        except TicketSLA.DoesNotExist:
            sla_serializer = None

        return Response(
            {
                "ticket_number": ticket.ticket_number,
                "title": ticket.title,
                "status": ticket.status,
                "created_time": ticket.created_time,
                "event_response_time": ticket.event_response_time,
                "event_analysis_time": ticket.event_analysis_time,
                "event_containment_time": ticket.event_containment_time,
                "ticket_resolved_time": ticket.ticket_resolved_time,
                "sla": sla_serializer.data if sla_serializer else None,
            }
        )

    @action(detail=True, methods=["get"])
    def timeline(self, request, ticket_number=None):
        ticket = self.get_object()
        work_logs = ticket.ticketworklog_set.all().order_by("created_at")
        serializer = TicketWorkLogSerializer(work_logs, many=True)

        return Response(
            {
                "ticket_number": ticket.ticket_number,
                "status_timeline": [
                    {
                        "status": ticket.status,
                        "created": ticket.created_time,
                        "acknowledged": ticket.event_response_time,
                        "triaged": ticket.event_analysis_time,
                        "contained": ticket.event_containment_time,
                        "resolved": ticket.ticket_resolved_time,
                    },
                ],
                "work_logs": serializer.data,
            }
        )

    @action(detail=True, methods=["post"])
    def add_worklog(self, request, ticket_number=None):
        ticket = self.get_object()
        log_entry = request.data.get("log_entry", "")

        if not log_entry:
            return Response(
                {"error": "log_entry is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        work_log = TicketWorkLog.objects.create(
            ticket=ticket,
            log_entry=log_entry,
            created_by=request.user,
        )

        serializer = TicketWorkLogSerializer(work_log)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"])
    def worklogs(self, request, ticket_number=None):
        ticket = self.get_object()
        work_logs = ticket.ticketworklog_set.all().order_by("-created_at")
        serializer = TicketWorkLogSerializer(work_logs, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["post"])
    def add_attachment(self, request, ticket_number=None):
        ticket = self.get_object()

        if "file_path" not in request.FILES:
            return Response(
                {"error": "file_path is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        file_obj = request.FILES["file_path"]
        file_name = request.data.get("file_name", file_obj.name)

        attachment = EventTicketAttachment.objects.create(
            ticket=ticket,
            file_name=file_name,
            file_path=file_obj,
            uploaded_user=request.user,
        )

        serializer = EventTicketAttachmentSerializer(attachment)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"])
    def attachments(self, request, ticket_number=None):
        ticket = self.get_object()
        attachments = ticket.eventticketattachment_set.all().order_by("-uploaded_time")
        serializer = EventTicketAttachmentSerializer(attachments, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["post"])
    def add_handlelog(self, request, ticket_number=None):
        ticket = self.get_object()
        action_taken = request.data.get("action_taken", "")

        if not action_taken:
            return Response(
                {"error": "action_taken is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        handle_log = TicketHandleLog.objects.create(
            ticket=ticket,
            handler=request.user,
            action_taken=action_taken,
        )

        serializer = TicketHandleLogSerializer(handle_log)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"])
    def handlelogs(self, request, ticket_number=None):
        ticket = self.get_object()
        handle_logs = ticket.tickethandlelog_set.all().order_by("-handled_at")
        serializer = TicketHandleLogSerializer(handle_logs, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["post"])
    def ai_assistant(self, request, ticket_number=None):
        ticket = self.get_object()
        serializer = AIAssistantRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            if data.get("enabled") is False:
                return Response({"error": "AI assistant is disabled"}, status=status.HTTP_400_BAD_REQUEST)
            overrides = {
                "api_key": data.get("api_key"),
                "model": data.get("model"),
                "base_url": data.get("base_url"),
                "timeout_seconds": data.get("timeout_seconds"),
                "mcp": {
                    "enabled": data.get("mcp_enabled"),
                    "base_url": data.get("mcp_base_url"),
                    "servers": data.get("mcp_servers"),
                    "token": data.get("mcp_token"),
                    "timeout_seconds": data.get("mcp_timeout_seconds"),
                    "ticket_context_path": data.get("mcp_ticket_context_path"),
                    "ticket_search_path": data.get("mcp_ticket_search_path"),
                    "cmdb_lookup_path": data.get("mcp_cmdb_lookup_path"),
                    "observables_extract_path": data.get("mcp_observables_extract_path"),
                },
                "skills": data.get("skills") or [],
            }
            if not overrides.get("skills"):
                overrides["skills"] = get_enabled_skill_configs()
            mcp_overrides = overrides.get("mcp") if isinstance(overrides.get("mcp"), dict) else None
            if isinstance(mcp_overrides, dict):
                if mcp_overrides.get("enabled") is not False:
                    mcp_overrides["enabled"] = True
                    # Always prefer built-in MCP endpoint for ticket assistant.
                    mcp_overrides["base_url"] = request.build_absolute_uri("/api/v1/mcp").rstrip("/")
                    mcp_overrides["force_internal"] = True
                if not mcp_overrides.get("token"):
                    auth_header = request.META.get("HTTP_AUTHORIZATION")
                    if isinstance(auth_header, str) and auth_header.strip():
                        # Prefer forwarding the original Authorization header verbatim.
                        mcp_overrides["token"] = auth_header.strip()
                if not mcp_overrides.get("token"):
                    auth_obj = getattr(request, "auth", None)
                    token_key = getattr(auth_obj, "key", None) if auth_obj is not None else None
                    if isinstance(token_key, str) and token_key:
                        mcp_overrides["token"] = f"Token {token_key}"
                if not mcp_overrides.get("servers"):
                    servers = ExternalMCPServer.objects.filter(enabled=True).order_by("name")
                    mcp_overrides["servers"] = [
                        {
                            "endpoint": s.endpoint,
                            "title": s.title,
                            "token": s.token,
                        }
                        for s in servers
                    ]
                if mcp_overrides.get("enabled") is None and mcp_overrides.get("servers"):
                    mcp_overrides["enabled"] = True
            result = generate_ai_assistant_output(
                ticket=ticket,
                alert_json=data.get("alert_json"),
                trigger_rule=data.get("trigger_rule", ""),
                related_logs=data.get("related_logs", []),
                user_prompt=data.get("prompt") or None,
                overrides=overrides,
            )
            try:
                assistant = result.get("assistant") if isinstance(result, dict) else None
                if assistant:
                    completed = assistant.get("completed_tasks") if isinstance(assistant, dict) else None
                    next_tasks = assistant.get("next_tasks") if isinstance(assistant, dict) else None
                    completed_text = ""
                    if isinstance(completed, list) and completed:
                        completed_text = "\n".join([
                            f"- {t.get('title')}: {t.get('detail')}".strip() if isinstance(t, dict) else f"- {str(t)}"
                            for t in completed
                        ])
                    next_text = ""
                    if isinstance(next_tasks, list) and next_tasks:
                        next_text = "\n".join([
                            f"- {t.get('title')}: {t.get('detail')}".strip() if isinstance(t, dict) else f"- {str(t)}"
                            for t in next_tasks
                        ])
                    log_lines = ["AI Assistant Result"]
                    header = assistant.get("header") if isinstance(assistant, dict) else None
                    if isinstance(header, dict):
                        try:
                            log_lines.append(f"AI Header JSON: {json.dumps(header, ensure_ascii=True)}")
                        except Exception:
                            pass
                    if isinstance(assistant.get("alert_explanation"), str):
                        log_lines.append(f"Alert Explanation: {assistant.get('alert_explanation')}")
                    risk = assistant.get("risk_level_recommendation") if isinstance(assistant, dict) else None
                    if isinstance(risk, dict):
                        level = risk.get("level")
                        rationale = risk.get("rationale")
                        if level or rationale:
                            log_lines.append(f"Risk Level: {level or ''} {('- ' + rationale) if rationale else ''}".strip())
                    if completed_text:
                        log_lines.append("AI Tasks:")
                        log_lines.append(completed_text)
                    if next_text:
                        log_lines.append("Next Tasks:")
                        log_lines.append(next_text)
                    observables_payload = result.get("observables") if isinstance(result, dict) else None
                    if isinstance(observables_payload, dict):
                        try:
                            log_lines.append(f"AI Observables JSON: {json.dumps(observables_payload, ensure_ascii=True)}")
                        except Exception:
                            pass
                    TicketWorkLog.objects.create(
                        ticket=ticket,
                        log_entry="\n".join([l for l in log_lines if l]),
                        created_by=request.user,
                    )
            except Exception:
                # Do not block API response on logging failure
                pass
            return Response(result)
        except AIAssistantError as exc:
            return Response(
                {"error": str(exc)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

    @action(detail=True, methods=["get"])
    def ai_chat_history(self, request, ticket_number=None):
        ticket = self.get_object()
        limit_raw = request.query_params.get("limit", 200)
        before_raw = request.query_params.get("before")
        try:
            limit = int(limit_raw)
        except Exception:
            limit = 200
        limit = max(1, min(limit, 500))
        before_dt = None
        if before_raw:
            before_dt = parse_datetime(before_raw)
            if before_dt is None:
                try:
                    before_dt = datetime.fromisoformat(str(before_raw))
                except Exception:
                    before_dt = None
            if before_dt and timezone.is_naive(before_dt):
                before_dt = timezone.make_aware(before_dt, timezone.get_current_timezone())

        rows = TicketAIChatMessage.objects.filter(ticket=ticket)
        if before_dt:
            rows = rows.filter(created_at__lt=before_dt)
        rows = rows.order_by("-created_at")
        rows = rows[:limit]
        rows = list(rows)
        rows_display = list(reversed(rows))
        next_before = None
        if rows:
            oldest = rows[-1]
            next_before = oldest.created_at.isoformat()
        payload = [
            {
                "id": str(row.id),
                "role": row.role,
                "content": self._fix_mojibake(row.content),
                "trace": self._fix_mojibake_in_obj(row.trace) if isinstance(row.trace, list) else [],
                "created_at": row.created_at.isoformat(),
                "created_by": getattr(row.created_by, "username", None),
            }
            for row in rows_display
        ]
        return Response({"messages": payload, "next_before": next_before})

    @action(detail=True, methods=["delete"])
    def ai_chat_clear(self, request, ticket_number=None):
        ticket = self.get_object()
        TicketAIChatMessage.objects.filter(ticket=ticket).delete()
        return Response({"message": "cleared"})

    @staticmethod
    def _fix_mojibake(value: str) -> str:
        text = str(value or "")
        if not text:
            return text
        if not any(ch in text for ch in ("Ã", "Â", "â", "å", "ä", "æ", "ç", "è", "é", "ê", "ë", "ì", "í", "î", "ï", "ð", "ñ", "ò", "ó", "ô", "ö", "õ", "ø", "ù", "ú", "û", "ü", "ý", "ÿ")):
            return text
        replacements = {
            "â": "’",
            "â": "‘",
            "â": "“",
            "â": "”",
            "â": "–",
            "â": "—",
            "â¦": "…",
            "Â ": " ",
            "Â": "",
        }
        if any(k in text for k in replacements):
            patched = text
            for bad, good in replacements.items():
                patched = patched.replace(bad, good)
            text = patched
        try:
            repaired = text.encode("latin1", errors="strict").decode("utf-8", errors="strict")
        except Exception:
            return text
        if any("\u4e00" <= c <= "\u9fff" for c in repaired):
            return repaired
        return text

    @classmethod
    def _fix_mojibake_in_obj(cls, value):
        if isinstance(value, str):
            return cls._fix_mojibake(value)
        if isinstance(value, list):
            return [cls._fix_mojibake_in_obj(v) for v in value]
        if isinstance(value, dict):
            return {k: cls._fix_mojibake_in_obj(v) for k, v in value.items()}
        return value

    @action(detail=True, methods=["post"])
    def ai_mention(self, request, ticket_number=None):
        ticket = self.get_object()
        serializer = AIAssistantRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            if data.get("enabled") is False:
                return Response({"error": "AI assistant is disabled"}, status=status.HTTP_400_BAD_REQUEST)
            overrides = {
                "api_key": data.get("api_key"),
                "model": data.get("model"),
                "base_url": data.get("base_url"),
                "timeout_seconds": data.get("timeout_seconds"),
                "mcp": {
                    "enabled": data.get("mcp_enabled"),
                    "base_url": data.get("mcp_base_url"),
                    "servers": data.get("mcp_servers"),
                    "token": data.get("mcp_token"),
                    "timeout_seconds": data.get("mcp_timeout_seconds"),
                    "ticket_context_path": data.get("mcp_ticket_context_path"),
                    "ticket_search_path": data.get("mcp_ticket_search_path"),
                    "cmdb_lookup_path": data.get("mcp_cmdb_lookup_path"),
                    "observables_extract_path": data.get("mcp_observables_extract_path"),
                },
                "skills": data.get("skills") or [],
            }
            mcp_overrides = overrides.get("mcp") if isinstance(overrides.get("mcp"), dict) else None
            if isinstance(mcp_overrides, dict):
                if mcp_overrides.get("enabled") and not mcp_overrides.get("base_url"):
                    mcp_overrides["base_url"] = request.build_absolute_uri("/api/v1/mcp").rstrip("/")
                if not mcp_overrides.get("token"):
                    auth_header = request.META.get("HTTP_AUTHORIZATION")
                    if isinstance(auth_header, str) and auth_header.strip():
                        mcp_overrides["token"] = auth_header.strip()
                if not mcp_overrides.get("token"):
                    auth_obj = getattr(request, "auth", None)
                    token_key = getattr(auth_obj, "key", None) if auth_obj is not None else None
                    if isinstance(token_key, str) and token_key:
                        mcp_overrides["token"] = f"Token {token_key}"
            result = generate_ai_assistant_output(
                ticket=ticket,
                alert_json=data.get("alert_json"),
                trigger_rule=data.get("trigger_rule", ""),
                related_logs=data.get("related_logs", []),
                user_prompt=data.get("prompt") or None,
                overrides=overrides,
            )
            return Response(result)
        except AIAssistantError as exc:
            return Response(
                {"error": str(exc)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )


class TicketSLAViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only API ViewSet for TicketSLA metrics."""

    serializer_class = TicketSLASerializer
    permission_classes = [IsAuthenticated]
    lookup_field = "ticket__ticket_number"
    lookup_url_kwarg = "ticket_number"

    def get_queryset(self):
        return TicketSLA.objects.select_related("ticket").filter(
            ticket__is_deleted=False
        )

    @action(detail=False, methods=["get"])
    def summary(self, request):
        slas = self.get_queryset()
        resolved_slas = slas.filter(ticket__status="resolved")

        return Response(
            {
                "total_tickets": slas.count(),
                "avg_mtta_seconds": slas.aggregate(Avg("mtta_seconds"))["mtta_seconds__avg"],
                "avg_mtti_seconds": slas.aggregate(Avg("mtti_seconds"))["mtti_seconds__avg"],
                "avg_mttc_seconds": slas.aggregate(Avg("mttc_seconds"))["mttc_seconds__avg"],
                "avg_mttr_seconds": slas.aggregate(Avg("mttr_seconds"))["mttr_seconds__avg"],
                "resolved_count": resolved_slas.count(),
                "avg_mttr_resolved": resolved_slas.aggregate(Avg("mttr_seconds"))["mttr_seconds__avg"],
            }
        )
