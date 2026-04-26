import json
import logging
import re
import threading
import time
from collections import deque
from typing import Any, Dict, List
from urllib.parse import quote

import requests
from django.conf import settings
from django.utils import timezone

from ai_assistant.models import MCPToolExecution
from ai_assistant.monitoring import update_mcp_stats

logger = logging.getLogger(__name__)

_MONITOR_LOCK = threading.Lock()
_MONITOR_ROWS: deque[Dict[str, Any]] = deque(maxlen=500)
_TOOLS_CACHE_LOCK = threading.Lock()
_TOOLS_CACHE: Dict[str, Dict[str, Any]] = {}
_TOOLS_CACHE_TTL_SECONDS = 180


def _record_monitor(
    tool: str,
    status: str,
    start_ts: float,
    request_url: str,
    http_status: int | None = None,
    error: str | None = None,
    request_payload: Any | None = None,
    response_payload: Any | None = None,
) -> None:
    end_ts = time.time()
    req_text = ""
    resp_text = ""
    if request_payload is not None:
        try:
            req_text = json.dumps(request_payload, ensure_ascii=True, sort_keys=True)
        except Exception:
            req_text = str(request_payload)
    if response_payload is not None:
        try:
            resp_text = json.dumps(response_payload, ensure_ascii=True, sort_keys=True)
        except Exception:
            resp_text = str(response_payload)
    row = {
        "tool": tool,
        "status": status,
        "start_ts": start_ts,
        "start_at": time.strftime("%Y/%m/%d %H:%M:%S", time.localtime(start_ts)),
        "duration_ms": int((end_ts - start_ts) * 1000),
        "http_status": http_status,
        "error": (error or "")[:300],
        "request_url": request_url,
        "request_payload": req_text[:4000],
        "response_payload": resp_text[:4000],
    }
    with _MONITOR_LOCK:
        _MONITOR_ROWS.appendleft(row)

    try:
        exec_row = MCPToolExecution.objects.create(
            tool_name=str(tool or ""),
            arguments=request_payload or {},
            response_payload=response_payload or {},
            status=str(status or ""),
            error=(error or "")[:2000],
            endpoint=str(request_url or "")[:500],
            source="mcp_gateway",
        )
        start_dt = timezone.datetime.fromtimestamp(start_ts, tz=timezone.get_current_timezone())
        end_dt = timezone.datetime.fromtimestamp(end_ts, tz=timezone.get_current_timezone())
        duration_ms = int(max(0, (end_ts - start_ts) * 1000))
        MCPToolExecution.objects.filter(id=exec_row.id).update(
            start_time=start_dt,
            end_time=end_dt,
            duration_ms=duration_ms,
        )
        update_mcp_stats(str(tool or ""), success=(status == "completed"))
    except Exception:
        pass


def get_mcp_monitor_snapshot(tool: str | None = None, status: str | None = None, limit: int = 100) -> Dict[str, Any]:
    with _MONITOR_LOCK:
        rows = list(_MONITOR_ROWS)

    if tool:
        tool_kw = tool.strip().lower()
        rows = [r for r in rows if tool_kw in str(r.get("tool") or "").lower()]
    if status and status != "all":
        rows = [r for r in rows if str(r.get("status") or "") == status]

    total = len(rows)
    success = sum(1 for r in rows if r.get("status") == "completed")
    failed = sum(1 for r in rows if r.get("status") == "failed")
    running = sum(1 for r in rows if r.get("status") == "running")
    success_rate = round((success / total) * 100, 1) if total else 0.0

    by_tool: Dict[str, Dict[str, int]] = {}
    for r in rows:
        t = str(r.get("tool") or "unknown")
        if t not in by_tool:
            by_tool[t] = {"total": 0, "success": 0, "failed": 0, "running": 0}
        by_tool[t]["total"] += 1
        s = str(r.get("status") or "")
        if s == "completed":
            by_tool[t]["success"] += 1
        elif s == "failed":
            by_tool[t]["failed"] += 1
        elif s == "running":
            by_tool[t]["running"] += 1

    recent = rows[: max(1, min(int(limit), 200))]
    return {
        "summary": {
            "total_calls": total,
            "success": success,
            "failed": failed,
            "running": running,
            "success_rate": success_rate,
            "last_invocation": recent[0]["start_at"] if recent else "",
        },
        "by_tool": by_tool,
        "recent": recent,
    }


def record_mcp_monitor_event(
    tool: str,
    status: str,
    request_url: str = "",
    http_status: int | None = None,
    error: str | None = None,
    request_payload: Any | None = None,
    response_payload: Any | None = None,
) -> None:
    _record_monitor(
        tool=tool,
        status=status,
        start_ts=time.time(),
        request_url=request_url,
        http_status=http_status,
        error=error,
        request_payload=request_payload,
        response_payload=response_payload,
    )


def _resolve_config(overrides: Dict[str, Any] | None = None) -> Dict[str, Any]:
    mcp = {}
    if isinstance(overrides, dict):
        raw = overrides.get("mcp")
        if isinstance(raw, dict):
            mcp = raw

    enabled = mcp.get("enabled")
    if enabled is None:
        enabled = bool(getattr(settings, "MCP_GATEWAY_ENABLED", False))

    base_url = (mcp.get("base_url") or getattr(settings, "MCP_GATEWAY_BASE_URL", "") or "").strip()
    token = (mcp.get("token") or getattr(settings, "MCP_GATEWAY_TOKEN", "") or "").strip()
    raw_servers = mcp.get("servers")
    servers: List[Dict[str, str]] = []
    if isinstance(raw_servers, list):
        for item in raw_servers:
            if not isinstance(item, dict):
                continue
            endpoint = str(item.get("endpoint") or "").strip()
            if not endpoint:
                continue
            servers.append(
                {
                    "endpoint": endpoint,
                    "title": str(item.get("title") or "").strip(),
                    "token": str(item.get("token") or "").strip(),
                }
            )
    # Keep base_url as the primary (usually internal) MCP endpoint.

    timeout_value = mcp.get("timeout_seconds")
    if timeout_value in (None, ""):
        timeout_value = getattr(settings, "MCP_GATEWAY_TIMEOUT_SECONDS", 8)
    try:
        timeout_seconds = int(timeout_value or 8)
    except Exception:
        timeout_seconds = 8

    ticket_context_path = (
        mcp.get("ticket_context_path")
        or getattr(settings, "MCP_TICKET_CONTEXT_PATH", "/ticket-context/{ticket_number}")
    )
    ticket_search_path = (
        mcp.get("ticket_search_path")
        or getattr(settings, "MCP_TICKET_SEARCH_PATH", "/ticket-search/similar-cases")
    )
    cmdb_lookup_path = (
        mcp.get("cmdb_lookup_path")
        or getattr(settings, "MCP_CMDB_LOOKUP_PATH", "/cmdb/asset-lookup")
    )
    observables_extract_path = (
        mcp.get("observables_extract_path")
        or getattr(settings, "MCP_OBSERVABLES_EXTRACT_PATH", "/observables/extract")
    )

    return {
        "enabled": bool(enabled),
        "base_url": base_url,
        "token": token,
        "servers": servers,
        "force_internal": bool(mcp.get("force_internal")),
        "timeout_seconds": timeout_seconds,
        "ticket_context_path": str(ticket_context_path),
        "ticket_search_path": str(ticket_search_path),
        "cmdb_lookup_path": str(cmdb_lookup_path),
        "observables_extract_path": str(observables_extract_path),
    }


def _enabled(config: Dict[str, Any]) -> bool:
    return bool(config.get("enabled")) and bool(config.get("base_url"))


def _timeout(config: Dict[str, Any]) -> int:
    return int(config.get("timeout_seconds", 8))


def _headers(config: Dict[str, Any]) -> Dict[str, str]:
    token = config.get("token") or ""
    headers = {"Content-Type": "application/json"}
    if token:
        token_str = str(token).strip()
        if token_str.lower().startswith("bearer ") or token_str.lower().startswith("token "):
            headers["Authorization"] = token_str
        else:
            headers["Authorization"] = f"Bearer {token_str}"
    return headers


def _full_url(path: str, config: Dict[str, Any]) -> str:
    base_url = str(config.get("base_url", "")).rstrip("/")
    return f"{base_url}/{path.lstrip('/')}"


def _candidate_configs(config: Dict[str, Any], tool_hint: str | None = None) -> List[Dict[str, Any]]:
    seen: set[str] = set()
    items: List[Dict[str, str]] = []

    base_url = str(config.get("base_url") or "").strip()
    if base_url:
        items.append({"endpoint": base_url, "title": ""})

    if config.get("force_internal"):
        candidates: List[Dict[str, Any]] = []
        for item in items:
            url = str(item.get("endpoint") or "").strip()
            if not url:
                continue
            key = url.rstrip("/").lower()
            if key in seen:
                continue
            seen.add(key)
            cfg = dict(config)
            cfg["base_url"] = url
            cfg["server_title"] = str(item.get("title") or "").strip()
            candidates.append(cfg)
        return candidates

    for item in config.get("servers") or []:
        if not isinstance(item, dict):
            continue
        endpoint = str(item.get("endpoint") or "").strip()
        if endpoint:
            items.append(
                {
                    "endpoint": endpoint,
                    "title": str(item.get("title") or "").strip(),
                    "token": str(item.get("token") or "").strip(),
                }
            )

    # Route by server name/title: endpoints whose title/url match tool words go first.
    hint_tokens: List[str] = []
    if tool_hint:
        hint_tokens = [t for t in re.split(r"[^a-z0-9]+", tool_hint.lower()) if len(t) >= 3]
    weighted: List[tuple[int, int, Dict[str, str]]] = []
    for idx, item in enumerate(items):
        haystack = f"{item.get('title', '')} {item.get('endpoint', '')}".lower()
        score = sum(1 for t in hint_tokens if t in haystack)
        weighted.append((score, -idx, item))
    weighted.sort(reverse=True)

    candidates: List[Dict[str, Any]] = []
    for _, __, item in weighted:
        url = str(item.get("endpoint") or "").strip()
        key = url.rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        cfg = dict(config)
        cfg["base_url"] = url
        cfg["server_title"] = str(item.get("title") or "").strip()
        if item.get("token"):
            cfg["token"] = str(item.get("token") or "").strip()
        candidates.append(cfg)
    return candidates


def _is_mcp_jsonrpc_endpoint(config: Dict[str, Any]) -> bool:
    base_url = str(config.get("base_url", "") or "").lower()
    return "/mcp-connect/" in base_url or base_url.endswith("/mcp") or base_url.endswith("/mcp/")


def _extract_tool_result(data: Any) -> Any:
    if not isinstance(data, dict):
        return None
    result = data.get("result")
    if isinstance(result, dict):
        if isinstance(result.get("structuredContent"), (dict, list)):
            return result.get("structuredContent")
        content = result.get("content")
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    try:
                        return json.loads(text)
                    except Exception:
                        return {"text": text}
    return None


def _normalize_tool_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name or "").lower()).strip("_")


def _initialize_mcp_session(config: Dict[str, Any], base_headers: Dict[str, str]) -> Dict[str, str]:
    request_url = str(config.get("base_url", "")).rstrip("/")
    headers = dict(base_headers)
    timeout = _timeout(config)
    try:
        init_payload = {
            "jsonrpc": "2.0",
            "id": int(time.time() * 1000),
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {
                    "name": "Obot MCP Gateway (via nanobot v0.0.0-dev+07f62acb)",
                    "version": "",
                },
            },
        }
        init_res = requests.post(
            request_url,
            headers=headers,
            json=init_payload,
            timeout=timeout,
        )
        session_id = (
            init_res.headers.get("mcp-session-id")
            or init_res.headers.get("Mcp-Session-Id")
            or init_res.headers.get("MCP-Session-Id")
            or ""
        )
        if session_id:
            headers["mcp-session-id"] = session_id

        notif_payload = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        }
        try:
            requests.post(
                request_url,
                headers=headers,
                json=notif_payload,
                timeout=timeout,
            )
        except Exception:
            pass
    except Exception:
        pass
    return headers


def _extract_tools_list(data: Any) -> List[str]:
    result = data.get("result") if isinstance(data, dict) else None
    if not isinstance(result, dict):
        return []
    tools = result.get("tools")
    if not isinstance(tools, list):
        return []
    out: List[str] = []
    for item in tools:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            if name:
                out.append(name)
    return out


def _extract_tools_meta(data: Any) -> List[Dict[str, Any]]:
    result = data.get("result") if isinstance(data, dict) else None
    if not isinstance(result, dict):
        return []
    tools = result.get("tools")
    if not isinstance(tools, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in tools:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        schema = item.get("inputSchema") if isinstance(item.get("inputSchema"), dict) else {}
        out.append({"name": name, "inputSchema": schema})
    return out


def _get_endpoint_tools(config: Dict[str, Any]) -> List[str] | None:
    if not _is_mcp_jsonrpc_endpoint(config):
        return None
    endpoint = str(config.get("base_url") or "").rstrip("/")
    if not endpoint:
        return None

    now = time.time()
    with _TOOLS_CACHE_LOCK:
        cache_row = _TOOLS_CACHE.get(endpoint)
        if cache_row and now - float(cache_row.get("ts") or 0) <= _TOOLS_CACHE_TTL_SECONDS:
            meta = cache_row.get("meta")
            if isinstance(meta, list):
                names = [str(x.get("name") or "").strip() for x in meta if isinstance(x, dict)]
                names = [n for n in names if n]
                if names:
                    return names

    request_url = str(config.get("base_url", "")).rstrip("/")
    headers = _initialize_mcp_session(config, _headers(config))
    timeout = _timeout(config)
    method_candidates = ["tools/list", "tools.list", "list_tools"]
    meta: List[Dict[str, Any]] = []
    for method_name in method_candidates:
        payload = {
            "jsonrpc": "2.0",
            "id": int(time.time() * 1000),
            "method": method_name,
            "params": {},
        }
        res = requests.post(
            request_url,
            headers=headers,
            json=payload,
            timeout=timeout,
        )
        if res.status_code >= 400:
            continue
        try:
            data = res.json()
        except Exception:
            continue
        meta = _extract_tools_meta(data)
        if meta:
            break
    if not meta:
        return None
    names = [str(x.get("name") or "").strip() for x in meta if isinstance(x, dict)]
    names = [n for n in names if n]
    normalized_tools = {_normalize_tool_name(t) for t in names if t}
    with _TOOLS_CACHE_LOCK:
        _TOOLS_CACHE[endpoint] = {"ts": now, "tools": normalized_tools, "names": names, "meta": meta}
    return names


def _get_endpoint_tool_meta(config: Dict[str, Any]) -> List[Dict[str, Any]] | None:
    if not _is_mcp_jsonrpc_endpoint(config):
        return None
    endpoint = str(config.get("base_url") or "").rstrip("/")
    if not endpoint:
        return None
    now = time.time()
    with _TOOLS_CACHE_LOCK:
        cache_row = _TOOLS_CACHE.get(endpoint)
        if cache_row and now - float(cache_row.get("ts") or 0) <= _TOOLS_CACHE_TTL_SECONDS:
            meta = cache_row.get("meta")
            if isinstance(meta, list):
                return [x for x in meta if isinstance(x, dict)]
    _get_endpoint_tools(config)
    with _TOOLS_CACHE_LOCK:
        cache_row = _TOOLS_CACHE.get(endpoint) or {}
        meta = cache_row.get("meta")
        if isinstance(meta, list):
            return [x for x in meta if isinstance(x, dict)]
    return None


def _list_mcp_tools_jsonrpc(config: Dict[str, Any]) -> tuple[List[str], int | None, str]:
    request_url = str(config.get("base_url", "")).rstrip("/")
    headers = _initialize_mcp_session(config, _headers(config))
    timeout = _timeout(config)

    method_candidates = ["tools/list", "tools.list", "list_tools"]
    last_status: int | None = None
    last_error = ""
    for method_name in method_candidates:
        payload = {
            "jsonrpc": "2.0",
            "id": int(time.time() * 1000),
            "method": method_name,
            "params": {},
        }
        res = requests.post(
            request_url,
            headers=headers,
            json=payload,
            timeout=timeout,
        )
        last_status = res.status_code
        if res.status_code >= 400:
            last_error = (res.text or "")[:600]
            continue
        try:
            data = res.json()
        except Exception:
            last_error = f"invalid json response: {(res.text or '')[:200]}"
            continue
        tools = _extract_tools_list(data)
        if tools:
            return tools, res.status_code, ""
        last_error = f"invalid tools/list result: {str(data)[:300]}"
    return [], last_status, last_error or "tools/list failed"


def _endpoint_supports_tool(config: Dict[str, Any], tool_name: str) -> bool | None:
    if not _is_mcp_jsonrpc_endpoint(config):
        return None
    endpoint = str(config.get("base_url") or "").rstrip("/")
    if not endpoint:
        return None

    tools = _get_endpoint_tools(config)
    if not tools:
        return None
    req = _normalize_tool_name(tool_name)
    return any(_normalize_tool_name(t) == req for t in tools)


def _prioritize_configs_by_tool_support(configs: List[Dict[str, Any]], tool_name: str) -> List[Dict[str, Any]]:
    supported: List[Dict[str, Any]] = []
    unknown: List[Dict[str, Any]] = []
    unsupported: List[Dict[str, Any]] = []
    for cfg in configs:
        support = _endpoint_supports_tool(cfg, tool_name)
        if support is True:
            supported.append(cfg)
        elif support is False:
            unsupported.append(cfg)
        else:
            unknown.append(cfg)

    # Prefer endpoints that explicitly advertise the tool.
    if supported or unknown:
        return supported + unknown
    # If all look unsupported, keep original order as a compatibility fallback.
    return configs if configs else unsupported


def _tokenize_text(value: str) -> List[str]:
    return [t for t in re.split(r"[^a-z0-9]+", str(value or "").lower()) if len(t) >= 2]


def _filter_configs_by_target_mcp(configs: List[Dict[str, Any]], target_mcp: str | None) -> List[Dict[str, Any]]:
    if not target_mcp:
        return configs
    target_tokens = set(_tokenize_text(target_mcp))
    if not target_tokens:
        return configs
    matched: List[Dict[str, Any]] = []
    for cfg in configs:
        hay = f"{cfg.get('server_title') or ''} {cfg.get('base_url') or ''}"
        hay_tokens = set(_tokenize_text(hay))
        if target_tokens & hay_tokens:
            matched.append(cfg)
    return matched


def _extract_prompt_hints(prompt: str) -> Dict[str, str]:
    text = str(prompt or "")
    hints: Dict[str, str] = {}
    patterns = [
        ("index", r"(?:^|\b)(?:index|es_index)\s*[:=]\s*([a-zA-Z0-9._-]+)"),
        ("index", r"(?:in|from)\s+index\s+([a-zA-Z0-9._-]+)"),
    ]
    for key, pattern in patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            hints[key] = str(m.group(1)).strip()
    return hints


def _prepare_dynamic_tool_arguments(
    tool_meta: Dict[str, Any],
    prompt: str,
    ticket_number: str,
    alert_json: Dict[str, Any] | None,
    raw_message: str | None,
    related_logs: List[str] | None,
) -> tuple[Dict[str, Any], List[str]]:
    schema = tool_meta.get("inputSchema") if isinstance(tool_meta.get("inputSchema"), dict) else {}
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = schema.get("required") if isinstance(schema.get("required"), list) else []
    required = [str(x) for x in required if str(x).strip()]

    hints = _extract_prompt_hints(prompt)
    args: Dict[str, Any] = {
        "ticket_number": ticket_number,
        "prompt": prompt,
        "query": prompt,
    }
    if alert_json is not None:
        args["alert_json"] = alert_json
        if "index" not in hints and isinstance(alert_json.get("index"), str):
            hints["index"] = str(alert_json.get("index")).strip()
    if raw_message:
        args["raw_message"] = raw_message
    if related_logs:
        args["related_logs"] = related_logs

    if "index" in properties and "index" not in args:
        args["index"] = hints.get("index") or getattr(settings, "MCP_DEFAULT_INDEX", "alerts")
    if "text" in properties and "text" not in args:
        args["text"] = prompt
    if "q" in properties and "q" not in args:
        args["q"] = prompt

    # Respect strict schemas that only allow declared properties.
    if properties:
        allowed = set(properties.keys())
        args = {k: v for k, v in args.items() if k in allowed and v not in (None, "")}

    missing = [k for k in required if k not in args or args.get(k) in (None, "")]
    return args, missing


def invoke_mcp_tool_from_prompt(
    prompt: str,
    ticket_number: str,
    alert_json: Dict[str, Any] | None = None,
    raw_message: str | None = None,
    related_logs: List[str] | None = None,
    target_mcp: str | None = None,
    overrides: Dict[str, Any] | None = None,
) -> Dict[str, Any] | None:
    start_ts = time.time()
    config = _resolve_config(overrides)
    if not _enabled(config):
        _record_monitor(
            tool="dynamic-tool-router",
            status="failed",
            start_ts=start_ts,
            request_url="",
            error="mcp gateway disabled or base_url missing",
        )
        return None

    prompt_tokens = set(_tokenize_text(prompt))
    if not prompt_tokens:
        _record_monitor(
            tool="dynamic-tool-router",
            status="failed",
            start_ts=start_ts,
            request_url="",
            error="prompt is empty",
        )
        return None

    configs = _candidate_configs(config, tool_hint=prompt)
    if target_mcp:
        matched = _filter_configs_by_target_mcp(configs, target_mcp)
        if matched:
            configs = matched
        else:
            _record_monitor(
                tool="dynamic-tool-router",
                status="failed",
                start_ts=start_ts,
                request_url="",
                error=f"ai target mcp not matched: {target_mcp}",
            )
            return None
    ranked: List[tuple[int, int, str, Dict[str, Any]]] = []
    for idx, cfg in enumerate(configs):
        if not _is_mcp_jsonrpc_endpoint(cfg):
            continue
        tool_meta_list = _get_endpoint_tool_meta(cfg) or []
        endpoint_text = f"{cfg.get('server_title') or ''} {cfg.get('base_url') or ''}"
        endpoint_tokens = set(_tokenize_text(endpoint_text))
        endpoint_score = len(prompt_tokens & endpoint_tokens)
        for t_idx, meta in enumerate(tool_meta_list):
            tool_name = str(meta.get("name") or "").strip()
            if not tool_name:
                continue
            tool_tokens = set(_tokenize_text(tool_name))
            tool_score = len(prompt_tokens & tool_tokens)
            if tool_score <= 0 and endpoint_score <= 0:
                continue
            bonus = 3 if str(tool_name).lower() in str(prompt).lower() else 0
            total = tool_score * 5 + endpoint_score * 2 + bonus
            ranked.append((total, -idx, f"{10000 - t_idx:05d}", {"cfg": cfg, "tool": tool_name, "meta": meta}))

        if not tool_meta_list and endpoint_score > 0:
            # Endpoint name matches prompt but tools/list unavailable.
            ranked.append((endpoint_score, -idx, "00000", {"cfg": cfg, "tool": ""}))

    if not ranked:
        _record_monitor(
            tool="dynamic-tool-router",
            status="failed",
            start_ts=start_ts,
            request_url="",
            error="no matching tool found from tools/list",
        )
        return None

    ranked.sort(reverse=True)
    last_error = "matched endpoint but no callable tool discovered"
    for _, __, ___, selected in ranked:
        cfg = selected["cfg"]
        tool_name = str(selected.get("tool") or "").strip()
        if not tool_name:
            last_error = "matched endpoint but no callable tool discovered"
            continue
        tool_meta = selected.get("meta") if isinstance(selected.get("meta"), dict) else {}
        arguments, missing_required = _prepare_dynamic_tool_arguments(
            tool_meta=tool_meta,
            prompt=prompt,
            ticket_number=ticket_number,
            alert_json=alert_json,
            raw_message=raw_message,
            related_logs=related_logs,
        )
        if missing_required:
            last_error = f"missing required fields for tool {tool_name}: {', '.join(missing_required)}"
            continue

        tool_payload, http_status, err, rpc_req, rpc_resp = _call_mcp_tool_jsonrpc(
            config=cfg,
            tool_name=tool_name,
            arguments=arguments,
        )
        rpc_url = f"{str(cfg.get('base_url', '')).rstrip('/')}#tools/call:{tool_name}"
        if tool_payload is not None:
            _record_monitor(
                tool=f"dynamic:{tool_name}",
                status="completed",
                start_ts=start_ts,
                request_url=rpc_url,
                http_status=http_status,
                request_payload=rpc_req,
                response_payload=rpc_resp,
            )
            return {
                "endpoint": str(cfg.get("base_url") or ""),
                "title": str(cfg.get("server_title") or ""),
                "tool": tool_name,
                "result": tool_payload,
            }
        last_error = err or f"tool call failed for {tool_name}"

    _record_monitor(
        tool="dynamic-tool-router",
        status="failed",
        start_ts=start_ts,
        request_url="",
        error=last_error,
    )
    return None


def get_mcp_tools_catalog(
    prompt: str = "",
    target_mcp: str | None = None,
    overrides: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    config = _resolve_config(overrides)
    if not _enabled(config):
        return []
    configs = _candidate_configs(config, tool_hint=prompt or target_mcp or "")
    if target_mcp:
        configs = _filter_configs_by_target_mcp(configs, target_mcp)
    catalog: List[Dict[str, Any]] = []
    for cfg in configs:
        if not _is_mcp_jsonrpc_endpoint(cfg):
            continue
        meta = _get_endpoint_tool_meta(cfg) or []
        tools = []
        for item in meta:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            schema = item.get("inputSchema") if isinstance(item.get("inputSchema"), dict) else {}
            tools.append({"name": name, "inputSchema": schema})
        catalog.append(
            {
                "endpoint": str(cfg.get("base_url") or ""),
                "title": str(cfg.get("server_title") or ""),
                "tools": tools,
            }
        )
    return catalog


def invoke_mcp_tool_explicit(
    tool_name: str,
    arguments: Dict[str, Any],
    target_mcp: str | None = None,
    overrides: Dict[str, Any] | None = None,
) -> Dict[str, Any] | None:
    start_ts = time.time()
    config = _resolve_config(overrides)
    if not _enabled(config):
        _record_monitor(
            tool=f"dynamic:{tool_name}",
            status="failed",
            start_ts=start_ts,
            request_url="",
            error="mcp gateway disabled or base_url missing",
        )
        return None

    configs = _candidate_configs(config, tool_hint=tool_name)
    if target_mcp:
        configs = _filter_configs_by_target_mcp(configs, target_mcp)

    last_error = "no matching endpoint"
    for cfg in configs:
        if not _is_mcp_jsonrpc_endpoint(cfg):
            continue
        support = _endpoint_supports_tool(cfg, tool_name)
        if support is False:
            continue
        tool_payload, http_status, err, rpc_req, rpc_resp = _call_mcp_tool_jsonrpc(
            config=cfg,
            tool_name=tool_name,
            arguments=arguments,
        )
        rpc_url = f"{str(cfg.get('base_url', '')).rstrip('/')}#tools/call:{tool_name}"
        if tool_payload is not None:
            _record_monitor(
                tool=f"dynamic:{tool_name}",
                status="completed",
                start_ts=start_ts,
                request_url=rpc_url,
                http_status=http_status,
                request_payload=rpc_req,
                response_payload=rpc_resp,
            )
            return {
                "endpoint": str(cfg.get("base_url") or ""),
                "title": str(cfg.get("server_title") or ""),
                "tool": tool_name,
                "result": tool_payload,
            }
        last_error = err or f"tool call failed for {tool_name}"
        _record_monitor(
            tool=f"dynamic:{tool_name}",
            status="failed",
            start_ts=start_ts,
            request_url=rpc_url,
            http_status=http_status,
            error=last_error,
            request_payload=rpc_req,
            response_payload=rpc_resp,
        )

    _record_monitor(
        tool=f"dynamic:{tool_name}",
        status="failed",
        start_ts=start_ts,
        request_url="",
        error=last_error,
        request_payload=arguments,
    )
    return None


def _call_mcp_tool_jsonrpc(
    config: Dict[str, Any],
    tool_name: str,
    arguments: Dict[str, Any],
) -> tuple[Any, int | None, str, Any, Any]:
    base_url = str(config.get("base_url", "")).rstrip("/")
    request_url = base_url
    base_headers = _initialize_mcp_session(config, _headers(config))
    timeout = _timeout(config)

    # Use MCP-standard method first; keep only minimal compatibility fallback.
    method_candidates = ["tools/call", "tools.call"]
    params_candidates = [
        {"name": tool_name, "arguments": arguments},
        {"tool_name": tool_name, "arguments": arguments},
        {"name": tool_name, "input": arguments},
        {"tool": tool_name, "input": arguments},
    ]
    last_status: int | None = None
    last_error = ""
    last_request: Any = None
    last_response: Any = None

    for method_name in method_candidates:
        for params in params_candidates:
            payload = {
                "jsonrpc": "2.0",
                "id": int(time.time() * 1000),
                "method": method_name,
                "params": params,
            }
            last_request = payload
            res = requests.post(
                request_url,
                headers=base_headers,
                json=payload,
                timeout=timeout,
            )
            last_status = res.status_code
            if res.status_code >= 400:
                body = (res.text or "")[:600]
                last_error = body
                last_response = {"status_code": res.status_code, "body": body}
                # Retry alternative method/params shapes only for method/params errors.
                if (
                    "method not" in body.lower()
                    or "not allowed" in body.lower()
                    or "invalid params" in body.lower()
                    or "missing" in body.lower()
                ):
                    continue
                return None, res.status_code, body, last_request, last_response
            try:
                data = res.json()
            except Exception:
                text = (res.text or "")[:300]
                return None, res.status_code, f"invalid json response: {text}", last_request, {"status_code": res.status_code, "body": text}
            last_response = data
            if isinstance(data, dict) and isinstance(data.get("error"), dict):
                err_obj = data.get("error") or {}
                err_code = err_obj.get("code")
                err_msg = str(err_obj.get("message") or "")
                last_error = f"jsonrpc error {err_code}: {err_msg}".strip()
                # Keep probing other variants for method/params compatibility issues.
                lower_msg = err_msg.lower()
                if (
                    err_code == -32601
                    or err_code == -32602
                    or "method not found" in lower_msg
                    or "invalid params" in lower_msg
                    or "tool not found" in lower_msg
                    or "tool  not found" in lower_msg
                ):
                    continue
                return None, res.status_code, last_error, last_request, last_response
            tool_result = _extract_tool_result(data)
            if tool_result is not None:
                return tool_result, res.status_code, "", last_request, last_response
            last_error = f"invalid {method_name} result: {str(data)[:300]}"

    return None, last_status, last_error or "all MCP method variants failed", last_request, last_response


def fetch_ticket_context(ticket_number: str, overrides: Dict[str, Any] | None = None) -> Dict[str, Any] | None:
    start_ts = time.time()
    config = _resolve_config(overrides)
    if not _enabled(config):
        _record_monitor(
            tool="ticket-context",
            status="failed",
            start_ts=start_ts,
            request_url="",
            error="mcp gateway disabled or base_url missing",
        )
        return None
    configs = _prioritize_configs_by_tool_support(
        _candidate_configs(config, tool_hint="ticket_context"),
        "ticket_context",
    )
    for cfg in configs:
        path = str(cfg.get("ticket_context_path") or "/ticket-context/{ticket_number}")
        path = path.format(ticket_number=quote(str(ticket_number), safe=""))
        url = _full_url(path, cfg)
        timeout = _timeout(cfg)
        try:
            if _is_mcp_jsonrpc_endpoint(cfg):
                payload, http_status, err, rpc_req, rpc_resp = _call_mcp_tool_jsonrpc(
                    config=cfg,
                    tool_name="ticket_context",
                    arguments={"ticket_number": ticket_number},
                )
                rpc_url = f"{str(cfg.get('base_url', '')).rstrip('/')}#tools/call:ticket_context"
                if isinstance(payload, dict):
                    _record_monitor(
                        tool="ticket-context",
                        status="completed",
                        start_ts=start_ts,
                        request_url=rpc_url,
                        http_status=http_status,
                        request_payload=rpc_req,
                        response_payload=rpc_resp,
                    )
                    return payload
                _record_monitor(
                    tool="ticket-context",
                    status="failed",
                    start_ts=start_ts,
                    request_url=rpc_url,
                    http_status=http_status,
                    error=err or "invalid tool response",
                    request_payload=rpc_req,
                    response_payload=rpc_resp,
                )
                continue

            res = requests.get(url, headers=_headers(cfg), timeout=timeout)
            if res.status_code >= 400:
                logger.warning("MCP ticket context failed: %s %s", res.status_code, res.text[:600])
                _record_monitor(
                    tool="ticket-context",
                    status="failed",
                    start_ts=start_ts,
                    request_url=url,
                    http_status=res.status_code,
                    error=res.text[:300],
                )
                continue
            payload = res.json()
            if isinstance(payload, dict):
                _record_monitor(
                    tool="ticket-context",
                    status="completed",
                    start_ts=start_ts,
                    request_url=url,
                    http_status=res.status_code,
                )
                return payload
            _record_monitor(
                tool="ticket-context",
                status="failed",
                start_ts=start_ts,
                request_url=url,
                http_status=res.status_code,
                error="invalid payload type",
                response_payload=payload,
            )
        except Exception as exc:
            logger.warning("MCP ticket context request error: %s", exc)
            _record_monitor(
                tool="ticket-context",
                status="failed",
                start_ts=start_ts,
                request_url=url,
                error=str(exc),
            )
    return None


def fetch_similar_cases(
    ticket_number: str,
    iocs: Dict[str, List[str]],
    limit: int = 5,
    overrides: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]] | None:
    start_ts = time.time()
    config = _resolve_config(overrides)
    if not _enabled(config):
        _record_monitor(
            tool="ticket-search",
            status="failed",
            start_ts=start_ts,
            request_url="",
            error="mcp gateway disabled or base_url missing",
        )
        return None
    payload = {
        "ticket_number": ticket_number,
        "iocs": iocs,
        "limit": limit,
    }
    configs = _prioritize_configs_by_tool_support(
        _candidate_configs(config, tool_hint="ticket_search_similar_cases"),
        "ticket_search_similar_cases",
    )
    for cfg in configs:
        path = str(cfg.get("ticket_search_path") or "/ticket-search/similar-cases")
        url = _full_url(path, cfg)
        timeout = _timeout(cfg)
        try:
            if _is_mcp_jsonrpc_endpoint(cfg):
                tool_payload, http_status, err, rpc_req, rpc_resp = _call_mcp_tool_jsonrpc(
                    config=cfg,
                    tool_name="ticket_search_similar_cases",
                    arguments=payload,
                )
                rpc_url = f"{str(cfg.get('base_url', '')).rstrip('/')}#tools/call:ticket_search_similar_cases"
                if isinstance(tool_payload, dict):
                    for key in ("items", "cases", "results", "data"):
                        value = tool_payload.get(key)
                        if isinstance(value, list):
                            _record_monitor(
                                tool="ticket-search",
                                status="completed",
                                start_ts=start_ts,
                                request_url=rpc_url,
                                http_status=http_status,
                                request_payload=rpc_req,
                                response_payload=rpc_resp,
                            )
                            return [d for d in value if isinstance(d, dict)]
                if isinstance(tool_payload, list):
                    _record_monitor(
                        tool="ticket-search",
                        status="completed",
                        start_ts=start_ts,
                        request_url=rpc_url,
                        http_status=http_status,
                        request_payload=rpc_req,
                        response_payload=rpc_resp,
                    )
                    return [d for d in tool_payload if isinstance(d, dict)]
                _record_monitor(
                    tool="ticket-search",
                    status="failed",
                    start_ts=start_ts,
                    request_url=rpc_url,
                    http_status=http_status,
                    error=err or "invalid tool response",
                    request_payload=rpc_req,
                    response_payload=rpc_resp,
                )
                continue

            res = requests.post(url, headers=_headers(cfg), json=payload, timeout=timeout)
            if res.status_code >= 400:
                logger.warning("MCP ticket search failed: %s %s", res.status_code, res.text[:600])
                _record_monitor(
                    tool="ticket-search",
                    status="failed",
                    start_ts=start_ts,
                    request_url=url,
                    http_status=res.status_code,
                    error=res.text[:300],
                )
                continue
            data = res.json()
            if isinstance(data, list):
                _record_monitor(
                    tool="ticket-search",
                    status="completed",
                    start_ts=start_ts,
                    request_url=url,
                    http_status=res.status_code,
                )
                return [d for d in data if isinstance(d, dict)]
            if isinstance(data, dict):
                for key in ("items", "cases", "results", "data"):
                    value = data.get(key)
                    if isinstance(value, list):
                        _record_monitor(
                            tool="ticket-search",
                            status="completed",
                            start_ts=start_ts,
                            request_url=url,
                            http_status=res.status_code,
                        )
                        return [d for d in value if isinstance(d, dict)]
            _record_monitor(
                tool="ticket-search",
                status="failed",
                start_ts=start_ts,
                request_url=url,
                http_status=res.status_code,
                error="invalid payload type",
                response_payload=data,
            )
        except Exception as exc:
            logger.warning("MCP ticket search request error: %s", exc)
            _record_monitor(
                tool="ticket-search",
                status="failed",
                start_ts=start_ts,
                request_url=url,
                error=str(exc),
            )
    return None


def fetch_cmdb_assets(
    ticket_number: str,
    indicators: Dict[str, List[str]] | None = None,
    limit: int = 10,
    overrides: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]] | None:
    start_ts = time.time()
    config = _resolve_config(overrides)
    if not _enabled(config):
        _record_monitor(
            tool="cmdb-asset-lookup",
            status="failed",
            start_ts=start_ts,
            request_url="",
            error="mcp gateway disabled or base_url missing",
        )
        return None
    payload = {
        "ticket_number": ticket_number,
        "indicators": indicators or {},
        "limit": limit,
    }
    configs = _prioritize_configs_by_tool_support(
        _candidate_configs(config, tool_hint="cmdb_asset_lookup"),
        "cmdb_asset_lookup",
    )
    for cfg in configs:
        path = str(cfg.get("cmdb_lookup_path") or "/cmdb/asset-lookup")
        url = _full_url(path, cfg)
        timeout = _timeout(cfg)
        try:
            if _is_mcp_jsonrpc_endpoint(cfg):
                tool_payload, http_status, err, rpc_req, rpc_resp = _call_mcp_tool_jsonrpc(
                    config=cfg,
                    tool_name="cmdb_asset_lookup",
                    arguments=payload,
                )
                rpc_url = f"{str(cfg.get('base_url', '')).rstrip('/')}#tools/call:cmdb_asset_lookup"
                if isinstance(tool_payload, dict):
                    for key in ("items", "assets", "results", "data"):
                        value = tool_payload.get(key)
                        if isinstance(value, list):
                            _record_monitor(
                                tool="cmdb-asset-lookup",
                                status="completed",
                                start_ts=start_ts,
                                request_url=rpc_url,
                                http_status=http_status,
                                request_payload=rpc_req,
                                response_payload=rpc_resp,
                            )
                            return [d for d in value if isinstance(d, dict)]
                if isinstance(tool_payload, list):
                    _record_monitor(
                        tool="cmdb-asset-lookup",
                        status="completed",
                        start_ts=start_ts,
                        request_url=rpc_url,
                        http_status=http_status,
                        request_payload=rpc_req,
                        response_payload=rpc_resp,
                    )
                    return [d for d in tool_payload if isinstance(d, dict)]
                _record_monitor(
                    tool="cmdb-asset-lookup",
                    status="failed",
                    start_ts=start_ts,
                    request_url=rpc_url,
                    http_status=http_status,
                    error=err or "invalid tool response",
                    request_payload=rpc_req,
                    response_payload=rpc_resp,
                )
                continue

            res = requests.post(url, headers=_headers(cfg), json=payload, timeout=timeout)
            if res.status_code >= 400:
                logger.warning("MCP cmdb lookup failed: %s %s", res.status_code, res.text[:600])
                _record_monitor(
                    tool="cmdb-asset-lookup",
                    status="failed",
                    start_ts=start_ts,
                    request_url=url,
                    http_status=res.status_code,
                    error=res.text[:300],
                )
                continue
            data = res.json()
            if isinstance(data, list):
                _record_monitor(
                    tool="cmdb-asset-lookup",
                    status="completed",
                    start_ts=start_ts,
                    request_url=url,
                    http_status=res.status_code,
                )
                return [d for d in data if isinstance(d, dict)]
            if isinstance(data, dict):
                for key in ("items", "assets", "results", "data"):
                    value = data.get(key)
                    if isinstance(value, list):
                        _record_monitor(
                            tool="cmdb-asset-lookup",
                            status="completed",
                            start_ts=start_ts,
                            request_url=url,
                            http_status=res.status_code,
                        )
                        return [d for d in value if isinstance(d, dict)]
            _record_monitor(
                tool="cmdb-asset-lookup",
                status="failed",
                start_ts=start_ts,
                request_url=url,
                http_status=res.status_code,
                error="invalid payload type",
                response_payload=data,
            )
        except Exception as exc:
            logger.warning("MCP cmdb lookup request error: %s", exc)
            _record_monitor(
                tool="cmdb-asset-lookup",
                status="failed",
                start_ts=start_ts,
                request_url=url,
                error=str(exc),
            )
    return None


def fetch_observables(
    ticket_number: str,
    raw_message: str | None = None,
    alert_json: Dict[str, Any] | None = None,
    text: str | None = None,
    overrides: Dict[str, Any] | None = None,
) -> Dict[str, Any] | None:
    start_ts = time.time()
    config = _resolve_config(overrides)
    if not _enabled(config):
        _record_monitor(
            tool="observables-extract",
            status="failed",
            start_ts=start_ts,
            request_url="",
            error="mcp gateway disabled or base_url missing",
        )
        return None

    payload: Dict[str, Any] = {"ticket_number": ticket_number}
    if raw_message:
        payload["raw_message"] = raw_message
    if alert_json is not None:
        payload["alert_json"] = alert_json
    if text:
        payload["text"] = text

    configs = _prioritize_configs_by_tool_support(
        _candidate_configs(config, tool_hint="observables_extract"),
        "observables_extract",
    )
    for cfg in configs:
        path = str(cfg.get("observables_extract_path") or "/observables/extract")
        url = _full_url(path, cfg)
        timeout = _timeout(cfg)
        try:
            if _is_mcp_jsonrpc_endpoint(cfg):
                tool_payload, http_status, err, rpc_req, rpc_resp = _call_mcp_tool_jsonrpc(
                    config=cfg,
                    tool_name="observables_extract",
                    arguments=payload,
                )
                rpc_url = f"{str(cfg.get('base_url', '')).rstrip('/')}#tools/call:observables_extract"
                if isinstance(tool_payload, dict):
                    _record_monitor(
                        tool="observables-extract",
                        status="completed",
                        start_ts=start_ts,
                        request_url=rpc_url,
                        http_status=http_status,
                        request_payload=rpc_req,
                        response_payload=rpc_resp,
                    )
                    return tool_payload
                _record_monitor(
                    tool="observables-extract",
                    status="failed",
                    start_ts=start_ts,
                    request_url=rpc_url,
                    http_status=http_status,
                    error=err or "invalid tool response",
                    request_payload=rpc_req,
                    response_payload=rpc_resp,
                )
                continue

            res = requests.post(url, headers=_headers(cfg), json=payload, timeout=timeout)
            if res.status_code >= 400:
                logger.warning("MCP observables extract failed: %s %s", res.status_code, res.text[:600])
                _record_monitor(
                    tool="observables-extract",
                    status="failed",
                    start_ts=start_ts,
                    request_url=url,
                    http_status=res.status_code,
                    error=res.text[:300],
                )
                continue
            data = res.json()
            if isinstance(data, dict):
                _record_monitor(
                    tool="observables-extract",
                    status="completed",
                    start_ts=start_ts,
                    request_url=url,
                    http_status=res.status_code,
                )
                return data
            _record_monitor(
                tool="observables-extract",
                status="failed",
                start_ts=start_ts,
                request_url=url,
                http_status=res.status_code,
                error="invalid payload type",
                response_payload=data,
            )
        except Exception as exc:
            logger.warning("MCP observables extract request error: %s", exc)
            _record_monitor(
                tool="observables-extract",
                status="failed",
                start_ts=start_ts,
                request_url=url,
                error=str(exc),
            )
    return None
