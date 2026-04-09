import json
import re
from typing import Any, Dict, List
from urllib.parse import urlparse

from django.db.models import Q
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

#from cmdb.models import Asset
from tickets.models import EventTicket, TicketWorkLog


def _json_dumps_safe(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True)
    except Exception:
        return str(value)


def _extract_iocs(text: str) -> Dict[str, List[str]]:
    if not text:
        return {"ips": [], "hashes": [], "users": [], "commands": []}

    ips = re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text)
    hashes = re.findall(r"\b[a-fA-F0-9]{32}\b|\b[a-fA-F0-9]{40}\b|\b[a-fA-F0-9]{64}\b", text)
    users = re.findall(r"(?i)\buser(?:name)?\s*[:=]\s*([A-Za-z0-9_.@-]+)", text)
    commands = [c.strip() for c in re.findall(r"(?i)(?:cmdline|command\s*line)\s*[:=]\s*(.+)", text)]
    return {
        "ips": sorted(set(ips)),
        "hashes": sorted(set(hashes)),
        "users": sorted(set(users)),
        "commands": sorted(set(commands)),
    }


def _extract_observables(text: str) -> Dict[str, List[str]]:
    if not text:
        return {
            "ip": [],
            "domain": [],
            "url": [],
            "hash": [],
            "email": [],
            "user": [],
            "process": [],
            "file_path": [],
            "registry_key": [],
            "cve": [],
            "asn": [],
        }

    urls = re.findall(r"\bhttps?://[^\s\"')]+", text, flags=re.IGNORECASE)
    ips = re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text)
    hashes = re.findall(r"\b[a-fA-F0-9]{32}\b|\b[a-fA-F0-9]{40}\b|\b[a-fA-F0-9]{64}\b", text)
    emails = re.findall(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", text)
    domains = re.findall(r"\b(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}\b", text)
    users = re.findall(r"(?i)\buser(?:name)?\s*[:=]\s*([A-Za-z0-9_.@-]+)", text)
    processes = re.findall(r"(?i)\b([a-z0-9_.-]+(?:\.exe|\.dll|\.ps1|\.bat|\.cmd|\.sh|\.py))\b", text)
    win_paths = re.findall(r"(?i)\b[A-Z]:\\(?:[^\\\r\n\t]+\\)*[^\\\r\n\t]+\b", text)
    unix_paths = re.findall(r"\b/(?:[^/\s]+/)*[^/\s]+\b", text)
    registry = re.findall(r"(?i)\b(?:HKLM|HKCU|HKCR|HKU|HKEY_LOCAL_MACHINE|HKEY_CURRENT_USER|HKEY_CLASSES_ROOT|HKEY_USERS)\\[^\s\"']+", text)
    cves = re.findall(r"(?i)\bCVE-\d{4}-\d{4,7}\b", text)
    asns = re.findall(r"(?i)\bAS\d{1,10}\b", text)

    # Add domains parsed from urls.
    url_domains = []
    for u in urls:
        try:
            host = (urlparse(u).hostname or "").strip().lower()
            if host:
                url_domains.append(host)
        except Exception:
            continue

    domain_set = sorted(set([d.lower() for d in domains + url_domains]))
    # Remove obvious non-domain/process false positives.
    domain_set = [d for d in domain_set if "." in d and not d.endswith(".exe")]

    return {
        "ip": sorted(set(ips)),
        "domain": domain_set,
        "url": sorted(set(urls)),
        "hash": sorted(set(hashes)),
        "email": sorted(set(emails)),
        "user": sorted(set(users)),
        "process": sorted(set([p.lower() for p in processes])),
        "file_path": sorted(set(win_paths + unix_paths)),
        "registry_key": sorted(set(registry)),
        "cve": sorted(set([c.upper() for c in cves])),
        "asn": sorted(set([a.upper() for a in asns])),
    }


def _flatten_observables(obs: Dict[str, List[str]]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for typ, values in obs.items():
        if not isinstance(values, list):
            continue
        for value in values:
            v = str(value).strip()
            if not v:
                continue
            out.append({"type": typ, "value": v})
    return out


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def mcp_ticket_context(request, ticket_number: str):
    try:
        ticket = EventTicket.objects.get(ticket_number=ticket_number, is_deleted=False)
    except EventTicket.DoesNotExist:
        return Response({"error": "ticket not found"}, status=status.HTTP_404_NOT_FOUND)

    work_logs = (
        TicketWorkLog.objects.filter(ticket=ticket)
        .order_by("-created_at")
        .values("created_at", "log_entry", "created_by_id")[:20]
    )
    timeline = []
    for w in reversed(list(work_logs)):
        timeline.append(
            {
                "time": str(w.get("created_at") or ""),
                "event": str(w.get("log_entry") or ""),
            }
        )

    payload = {
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
    return Response(payload)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def mcp_ticket_search_similar_cases(request):
    ticket_number = str(request.data.get("ticket_number") or "").strip()
    iocs = request.data.get("iocs") if isinstance(request.data.get("iocs"), dict) else {}
    limit_raw = request.data.get("limit", 5)
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
        # fallback: extract from raw payload text
        iocs = _extract_iocs(_json_dumps_safe(request.data))
        for key in ("ips", "hashes", "users", "commands"):
            keywords.extend([str(v).strip() for v in iocs.get(key, []) if str(v).strip()])

    if not keywords:
        return Response({"items": []})

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
    return Response({"items": scored[:limit]})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def mcp_observables_extract(request):
    ticket_number = str(request.data.get("ticket_number") or "").strip()
    raw_message = str(request.data.get("raw_message") or "")
    extra_text = str(request.data.get("text") or "")
    alert_json = request.data.get("alert_json")

    source_chunks: List[str] = []
    if raw_message:
        source_chunks.append(raw_message)
    if extra_text:
        source_chunks.append(extra_text)
    if alert_json is not None:
        source_chunks.append(_json_dumps_safe(alert_json))

    if ticket_number and not source_chunks:
        try:
            ticket = EventTicket.objects.get(ticket_number=ticket_number, is_deleted=False)
            source_chunks.extend(
                [
                    str(ticket.alert_message or ""),
                    str(ticket.description or ""),
                    str(ticket.title or ""),
                ]
            )
        except EventTicket.DoesNotExist:
            pass

    full_text = "\n".join([c for c in source_chunks if c]).strip()
    observables = _extract_observables(full_text)
    items = _flatten_observables(observables)

    return Response(
        {
            "ticket_number": ticket_number or None,
            "counts": {k: len(v) for k, v in observables.items()},
            "observables": observables,
            "items": items,
        }
    )


def _extract_hostnames(text: str) -> List[str]:
    if not text:
        return []
    hosts = re.findall(r"\b(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}\b", text)
    names = re.findall(r"(?i)\bhost(?:name)?\s*[:=]\s*([A-Za-z0-9_.-]+)", text)
    out = [h.strip().lower() for h in (hosts + names) if h and h.strip()]
    return sorted(set(out))


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def mcp_cmdb_asset_lookup(request):
    ticket_number = str(request.data.get("ticket_number") or "").strip()
    indicators = request.data.get("indicators") if isinstance(request.data.get("indicators"), dict) else {}
    limit_raw = request.data.get("limit", 10)
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
        try:
            ticket = EventTicket.objects.get(ticket_number=ticket_number, is_deleted=False)
            source_text = "\n".join(
                [
                    str(ticket.alert_message or ""),
                    str(ticket.description or ""),
                    str(ticket.title or ""),
                ]
            )
            extracted_iocs = _extract_iocs(source_text)
            ips.extend(extracted_iocs.get("ips", []))
            hostnames.extend(_extract_hostnames(source_text))
        except EventTicket.DoesNotExist:
            pass

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
        return Response({"items": []})

    assets = Asset.objects.filter(query).order_by("-updated_at")[:limit]
    items = [
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
    return Response({"items": items})
