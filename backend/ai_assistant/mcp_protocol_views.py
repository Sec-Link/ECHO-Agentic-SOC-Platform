import json
import time
from typing import Any, Dict, List, Tuple

from django.db.models import Q
from django.http import JsonResponse
from django.http import StreamingHttpResponse
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from ai_assistant.mcp_views import _extract_hostnames, _extract_iocs, _extract_observables, _flatten_observables, _json_dumps_safe
#from cmdb.models import Asset
from tickets.models import EventTicket, TicketWorkLog


def _rpc_ok(rpc_id: Any, result: Dict[str, Any]) -> JsonResponse:
    return JsonResponse({"jsonrpc": "2.0", "id": rpc_id, "result": result})


def _rpc_error(rpc_id: Any, code: int, message: str) -> JsonResponse:
    return JsonResponse(
        {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}},
        status=400,
    )


def _tool_defs() -> List[Dict[str, Any]]:
    return [
        {
            "name": "ticket_context",
            "description": "Get ticket context and recent timeline by ticket number",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "ticket_number": {"type": "string"},
                },
                "required": ["ticket_number"],
            },
        },
        {
            "name": "ticket_search_similar_cases",
            "description": "Search similar historical tickets by IOC keywords",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "ticket_number": {"type": "string"},
                    "iocs": {
                        "type": "object",
                        "properties": {
                            "ips": {"type": "array", "items": {"type": "string"}},
                            "hashes": {"type": "array", "items": {"type": "string"}},
                            "users": {"type": "array", "items": {"type": "string"}},
                            "commands": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                    "limit": {"type": "integer"},
                },
            },
        },
        {
            "name": "cmdb_asset_lookup",
            "description": "Lookup CMDB assets by indicators (ips, hostnames, asset numbers)",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "ticket_number": {"type": "string"},
                    "indicators": {
                        "type": "object",
                        "properties": {
                            "ips": {"type": "array", "items": {"type": "string"}},
                            "hostnames": {"type": "array", "items": {"type": "string"}},
                            "asset_numbers": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                    "limit": {"type": "integer"},
                },
            },
        },
        {
            "name": "observables_extract",
            "description": "Extract observables from raw message / text / alert json",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "ticket_number": {"type": "string"},
                    "raw_message": {"type": "string"},
                    "text": {"type": "string"},
                    "alert_json": {"type": "object"},
                },
            },
        },
    ]


def _call_ticket_context(args: Dict[str, Any]) -> Dict[str, Any]:
    ticket_number = str(args.get("ticket_number") or "").strip()
    if not ticket_number:
        raise ValueError("ticket_number is required")
    ticket = EventTicket.objects.filter(ticket_number=ticket_number, is_deleted=False).first()
    if not ticket:
        raise ValueError("ticket not found")

    work_logs = (
        TicketWorkLog.objects.filter(ticket=ticket)
        .order_by("-created_at")
        .values("created_at", "log_entry")[:20]
    )
    timeline = [
        {
            "time": str(w.get("created_at") or ""),
            "event": str(w.get("log_entry") or ""),
        }
        for w in reversed(list(work_logs))
    ]

    return {
        "ticket": {
            "ticket_number": ticket.ticket_number,
            "title": ticket.title,
            "status": ticket.status,
            "priority": ticket.priority,
            "event_category": ticket.event_category,
            "event_result": ticket.event_result,
            "event_platform": ticket.event_platform,
            "event_sources": ticket.event_sources,
            "event_risk_score": ticket.event_risk_score,
            "created_time": str(ticket.created_time),
            "updated_time": str(ticket.updated_time),
        },
        "timeline": timeline,
    }


def _call_similar_cases(args: Dict[str, Any]) -> Dict[str, Any]:
    ticket_number = str(args.get("ticket_number") or "").strip()
    iocs = args.get("iocs") if isinstance(args.get("iocs"), dict) else {}
    limit_raw = args.get("limit", 5)
    try:
        limit = max(1, min(int(limit_raw), 20))
    except Exception:
        limit = 5

    keywords: List[str] = []
    for key in ("ips", "hashes", "users", "commands"):
        value = iocs.get(key)
        if isinstance(value, list):
            keywords.extend([str(v).strip() for v in value if str(v).strip()])
    if not keywords:
        extracted = _extract_iocs(_json_dumps_safe(args))
        for key in ("ips", "hashes", "users", "commands"):
            keywords.extend([str(v).strip() for v in extracted.get(key, []) if str(v).strip()])
    if not keywords:
        return {"items": []}

    candidates = EventTicket.objects.filter(is_deleted=False).order_by("-created_time")
    if ticket_number:
        candidates = candidates.exclude(ticket_number=ticket_number)
    candidates = candidates[:300]

    scored: List[Dict[str, Any]] = []
    for c in candidates:
        hay = " ".join(
            [
                c.title or "",
                c.description or "",
                c.alert_message or "",
                c.ticket_records or "",
                c.event_sources or "",
                c.event_platform or "",
            ]
        ).lower()
        matched = [k for k in keywords if k and k.lower() in hay]
        if not matched:
            continue
        scored.append(
            {
                "ticket_number": c.ticket_number,
                "title": c.title,
                "verdict": c.event_result or "",
                "matched_on": sorted(set(matched)),
                "status": c.status,
                "score": len(set(matched)),
            }
        )
    scored.sort(key=lambda x: (-x["score"], x.get("ticket_number", "")))
    return {"items": scored[:limit]}


def _call_cmdb_lookup(args: Dict[str, Any]) -> Dict[str, Any]:
    ticket_number = str(args.get("ticket_number") or "").strip()
    indicators = args.get("indicators") if isinstance(args.get("indicators"), dict) else {}
    limit_raw = args.get("limit", 10)
    try:
        limit = max(1, min(int(limit_raw), 50))
    except Exception:
        limit = 10

    ips = indicators.get("ips") if isinstance(indicators.get("ips"), list) else []
    hostnames = indicators.get("hostnames") if isinstance(indicators.get("hostnames"), list) else []
    asset_numbers = indicators.get("asset_numbers") if isinstance(indicators.get("asset_numbers"), list) else []

    ips = [str(v).strip() for v in ips if str(v).strip()]
    hostnames = [str(v).strip().lower() for v in hostnames if str(v).strip()]
    asset_numbers = [str(v).strip() for v in asset_numbers if str(v).strip()]

    if ticket_number and not (ips or hostnames or asset_numbers):
        ticket = EventTicket.objects.filter(ticket_number=ticket_number, is_deleted=False).first()
        if ticket:
            source_text = "\n".join([str(ticket.alert_message or ""), str(ticket.description or ""), str(ticket.title or "")])
            extracted_iocs = _extract_iocs(source_text)
            ips.extend(extracted_iocs.get("ips", []))
            hostnames.extend(_extract_hostnames(source_text))

    query = Q()
    has_filter = False
    for ip in sorted(set(ips)):
        query |= Q(ip_address=ip)
        has_filter = True
    for hn in sorted(set(hostnames)):
        query |= Q(hostname__icontains=hn)
        has_filter = True
    for asset_no in sorted(set(asset_numbers)):
        query |= Q(asset_number__icontains=asset_no)
        has_filter = True

    if not has_filter:
        return {"items": []}

    assets = Asset.objects.filter(query).order_by("-updated_at")[:limit]
    return {
        "items": [
            {
                "asset_number": a.asset_number,
                "hostname": a.hostname,
                "ip_address": a.ip_address,
                "asset_type": a.asset_type,
                "asset_level": a.asset_level,
                "description": a.description or "",
                "is_alive": bool(a.is_alive),
                "updated_at": str(a.updated_at),
            }
            for a in assets
        ]
    }


def _call_observables_extract(args: Dict[str, Any]) -> Dict[str, Any]:
    ticket_number = str(args.get("ticket_number") or "").strip()
    raw_message = str(args.get("raw_message") or "")
    extra_text = str(args.get("text") or "")
    alert_json = args.get("alert_json")

    source_chunks: List[str] = []
    if raw_message:
        source_chunks.append(raw_message)
    if extra_text:
        source_chunks.append(extra_text)
    if alert_json is not None:
        source_chunks.append(_json_dumps_safe(alert_json))
    if ticket_number and not source_chunks:
        ticket = EventTicket.objects.filter(ticket_number=ticket_number, is_deleted=False).first()
        if ticket:
            source_chunks.extend([str(ticket.alert_message or ""), str(ticket.description or ""), str(ticket.title or "")])

    full_text = "\n".join([c for c in source_chunks if c]).strip()
    observables = _extract_observables(full_text)
    return {
        "ticket_number": ticket_number or None,
        "counts": {k: len(v) for k, v in observables.items()},
        "observables": observables,
        "items": _flatten_observables(observables),
    }


def _call_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    if name == "ticket_context":
        return _call_ticket_context(args)
    if name == "ticket_search_similar_cases":
        return _call_similar_cases(args)
    if name == "cmdb_asset_lookup":
        return _call_cmdb_lookup(args)
    if name == "observables_extract":
        return _call_observables_extract(args)
    raise ValueError(f"unknown tool: {name}")


def _parse_jsonrpc_request(body: bytes) -> Tuple[Any, str, Dict[str, Any]]:
    try:
        payload = json.loads(body.decode("utf-8") if body else "{}")
    except Exception:
        raise ValueError("invalid JSON body")
    if not isinstance(payload, dict):
        raise ValueError("request body must be object")
    rpc_id = payload.get("id")
    method = str(payload.get("method") or "")
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    if not method:
        raise ValueError("method is required")
    return rpc_id, method, params


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def mcp_tools_manifest(request):
    return JsonResponse({"tools": _tool_defs()})


def _sse_stream():
    # Minimal SSE stream for MCP HTTP streaming clients.
    # Keep the connection valid and provide an initial event.
    yield "event: ready\n"
    yield "data: {\"ok\": true, \"server\": \"siem-mcp\"}\n\n"
    while True:
        yield ": keep-alive\n\n"
        time.sleep(15)


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def mcp_rpc(request):
    if request.method == "GET":
        response = StreamingHttpResponse(_sse_stream(), content_type="text/event-stream")
        response["Cache-Control"] = "no-cache"
        return response

    try:
        rpc_id, method, params = _parse_jsonrpc_request(request.body)
    except ValueError as exc:
        return _rpc_error(None, -32600, str(exc))

    try:
        if method == "initialize":
            return _rpc_ok(
                rpc_id,
                {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "siem-mcp", "version": "1.0.0"},
                    "capabilities": {"tools": {"listChanged": False}},
                },
            )

        if method == "ping":
            return _rpc_ok(rpc_id, {})

        if method in ("notifications/initialized", "initialized"):
            return _rpc_ok(rpc_id, {})

        if method == "tools/list":
            return _rpc_ok(rpc_id, {"tools": _tool_defs()})

        if method == "tools/call":
            name = str(params.get("name") or "")
            args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
            if not name:
                return _rpc_error(rpc_id, -32602, "tools/call requires params.name")
            payload = _call_tool(name, args)
            return _rpc_ok(
                rpc_id,
                {
                    "content": [{"type": "text", "text": _json_dumps_safe(payload)}],
                    "structuredContent": payload,
                    "isError": False,
                },
            )

        return _rpc_error(rpc_id, -32601, f"method not found: {method}")
    except ValueError as exc:
        return _rpc_error(rpc_id, -32602, str(exc))
    except Exception as exc:
        return _rpc_error(rpc_id, -32000, str(exc))
