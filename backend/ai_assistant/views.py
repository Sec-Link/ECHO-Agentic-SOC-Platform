import requests
from django.conf import settings
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from ai_assistant.assistant import AIAssistantError, test_openai_connectivity
from ai_assistant.chat_agent import run_chat_agent
from ai_assistant.monitoring import get_mcp_monitor, get_skill_monitor
from ai_assistant.serializers import (
    AIAssistantRequestSerializer,
    AIChatRequestSerializer,
    ExternalMCPServerSerializer,
    SkillConfigSerializer,
)
from ai_assistant.models import ExternalMCPServer, SkillConfig, TicketAIChatMessage
from tickets.models import EventTicket
import re

from ai_assistant.skill_config import get_enabled_skill_names, list_skill_catalog, normalize_skill_names
from ai_assistant.skill_library import read_skill_content, write_skill_content

_MOJIBAKE_CHARS = re.compile(r"[ÃÂâåäæçèéêëìíîïðñòóôöõøùúûüýÿ]")


def _fix_mojibake(value: str) -> str:
    text = str(value or "")
    if not text:
        return text
    if not _MOJIBAKE_CHARS.search(text):
        return text
    # First handle common UTF-8 -> CP1252 mojibake for punctuation in English text.
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
    # Keep only if it actually looks better (e.g., has CJK characters)
    if re.search(r"[\u4e00-\u9fff]", repaired):
        return repaired
    return text

def _fix_mojibake_in_obj(value: any) -> any:
    if isinstance(value, str):
        return _fix_mojibake(value)
    if isinstance(value, list):
        return [_fix_mojibake_in_obj(v) for v in value]
    if isinstance(value, dict):
        return {k: _fix_mojibake_in_obj(v) for k, v in value.items()}
    return value


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def test_connectivity(request):
    serializer = AIAssistantRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data

    overrides = {
        "api_key": data.get("api_key"),
        "model": data.get("model"),
        "base_url": data.get("base_url"),
        "timeout_seconds": data.get("timeout_seconds"),
    }

    try:
        result = test_openai_connectivity(overrides=overrides)
        return Response(result)
    except AIAssistantError as exc:
        return Response({"ok": False, "error": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def mcp_monitor(request):
    tool = str(request.query_params.get("tool") or "").strip()
    status_filter = str(request.query_params.get("status") or "").strip()
    if status_filter == "all":
        status_filter = ""
    page_raw = request.query_params.get("page", 1)
    page_size_raw = request.query_params.get("page_size", 20)
    try:
        page = int(page_raw)
    except Exception:
        page = 1
    try:
        page_size = int(page_size_raw)
    except Exception:
        page_size = 20

    payload = get_mcp_monitor(page=page, page_size=page_size, status=status_filter, tool_name=tool)
    return Response(payload)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def mcp_registry_servers(request):
    data = request.data if isinstance(request.data, dict) else {}
    base_url = str(data.get("base_url") or getattr(settings, "MCP_GATEWAY_BASE_URL", "") or "").strip()
    token = str(data.get("token") or getattr(settings, "MCP_GATEWAY_TOKEN", "") or "").strip()
    query = str(data.get("query") or "").strip()
    cursor = str(data.get("cursor") or "").strip()
    try:
        limit = int(data.get("limit", 50))
    except Exception:
        limit = 50
    limit = max(1, min(limit, 200))

    if not base_url:
        return Response({"error": "base_url is required"}, status=status.HTTP_400_BAD_REQUEST)

    url = f"{base_url.rstrip('/')}/v0.1/servers"
    headers = {"Accept": "application/json"}
    if token:
        if token.lower().startswith("bearer ") or token.lower().startswith("token "):
            headers["Authorization"] = token
        else:
            headers["Authorization"] = f"Bearer {token}"

    params = {"limit": limit}
    if query:
        params["query"] = query
    if cursor:
        params["cursor"] = cursor

    try:
        res = requests.get(url, headers=headers, params=params, timeout=12)
    except Exception as exc:
        return Response({"error": f"failed to request registry: {exc}"}, status=status.HTTP_502_BAD_GATEWAY)

    if res.status_code >= 400:
        return Response(
            {
                "error": f"registry request failed: {res.status_code}",
                "response_text": (res.text or "")[:2000],
            },
            status=status.HTTP_502_BAD_GATEWAY,
        )
    try:
        payload = res.json()
    except Exception:
        return Response(
            {"error": "registry returned non-json response", "response_text": (res.text or "")[:2000]},
            status=status.HTTP_502_BAD_GATEWAY,
        )
    next_cursor = (
        res.headers.get("x-next-cursor")
        or res.headers.get("X-Next-Cursor")
        or res.headers.get("next-cursor")
        or res.headers.get("Next-Cursor")
        or ""
    )

    # Normalize pagination metadata so frontend can always paginate even when
    # upstream registry provides cursor in response headers.
    if isinstance(payload, list):
        return Response({"servers": payload, "next_cursor": next_cursor})
    if isinstance(payload, dict):
        if next_cursor and not payload.get("next_cursor") and not payload.get("nextCursor"):
            payload["next_cursor"] = next_cursor
        return Response(payload)
    return Response({"data": payload, "next_cursor": next_cursor})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def skill_monitor(request):
    payload = get_skill_monitor()
    return Response(payload)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def ai_chat(request):
    serializer = AIChatRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    ticket_number = str(data.get("ticket_number") or "").strip()
    ticket = None
    if ticket_number:
        ticket = EventTicket.objects.filter(ticket_number=ticket_number, is_deleted=False).first()
        if not ticket:
            return Response({"error": "ticket not found"}, status=status.HTTP_404_NOT_FOUND)

    overrides = {
        "api_key": data.get("api_key"),
        "model": data.get("model"),
        "base_url": data.get("base_url"),
        "timeout_seconds": data.get("timeout_seconds"),
        "max_iterations": data.get("max_iterations"),
        "mcp": {
            "enabled": data.get("mcp_enabled"),
            "base_url": data.get("mcp_base_url"),
            "servers": data.get("mcp_servers"),
            "token": data.get("mcp_token"),
            "timeout_seconds": data.get("mcp_timeout_seconds"),
        },
    }

    mcp_overrides = overrides.get("mcp") if isinstance(overrides.get("mcp"), dict) else None
    if isinstance(mcp_overrides, dict):
        if mcp_overrides.get("enabled") is not False:
            mcp_overrides["enabled"] = True
            mcp_overrides["base_url"] = request.build_absolute_uri("/api/v1/mcp").rstrip("/")
            mcp_overrides["force_internal"] = True
        if not mcp_overrides.get("token"):
            auth_header = request.META.get("HTTP_AUTHORIZATION")
            if isinstance(auth_header, str) and auth_header.strip():
                mcp_overrides["token"] = auth_header.strip()
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

    recommended_skills = normalize_skill_names(data.get("skills"))
    if not recommended_skills:
        recommended_skills = get_enabled_skill_names()

    if ticket:
        try:
            TicketAIChatMessage.objects.create(
                ticket=ticket,
                created_by=request.user,
                role="user",
                content=_fix_mojibake(str(data.get("message") or "")),
                trace=[],
            )
        except Exception:
            pass

    try:
        result = run_chat_agent(
            user_input=data.get("message"),
            history_messages=data.get("messages") or [],
            overrides=overrides,
            recommended_skills=recommended_skills,
        )
        if ticket:
            try:
                trace = result.get("trace") if isinstance(result, dict) else None
                if not isinstance(trace, list):
                    trace = []
                TicketAIChatMessage.objects.create(
                    ticket=ticket,
                    created_by=None,
                    role="assistant",
                    content=_fix_mojibake(str(result.get("response") or "No response")) if isinstance(result, dict) else "No response",
                    trace=_fix_mojibake_in_obj(trace),
                )
            except Exception:
                pass
        if isinstance(result, dict):
            result["response"] = _fix_mojibake(str(result.get("response") or ""))
            if isinstance(result.get("trace"), list):
                result["trace"] = _fix_mojibake_in_obj(result.get("trace"))
        return Response(result)
    except Exception as exc:
        if ticket:
            try:
                TicketAIChatMessage.objects.create(
                    ticket=ticket,
                    created_by=None,
                    role="assistant",
                    content=_fix_mojibake(str(exc)),
                    trace=[],
                )
            except Exception:
                pass
        return Response({"error": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def external_mcp_servers(request):
    if request.method == "GET":
        rows = ExternalMCPServer.objects.all().order_by("name")
        serializer = ExternalMCPServerSerializer(rows, many=True)
        return Response(serializer.data)

    serializer = ExternalMCPServerSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(serializer.data, status=status.HTTP_201_CREATED)


@api_view(["GET", "PUT", "DELETE"])
@permission_classes([IsAuthenticated])
def external_mcp_detail(request, name: str):
    server = ExternalMCPServer.objects.filter(name=name).first()
    if not server:
        return Response({"error": "server not found"}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "GET":
        return Response(ExternalMCPServerSerializer(server).data)

    if request.method == "DELETE":
        server.delete()
        return Response({"message": "deleted"})

    serializer = ExternalMCPServerSerializer(server, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(serializer.data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def external_mcp_start(request, name: str):
    server = ExternalMCPServer.objects.filter(name=name).first()
    if not server:
        return Response({"error": "server not found"}, status=status.HTTP_404_NOT_FOUND)
    server.enabled = True
    server.save(update_fields=["enabled", "updated_at"])
    return Response({"message": "started", "name": server.name})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def external_mcp_stop(request, name: str):
    server = ExternalMCPServer.objects.filter(name=name).first()
    if not server:
        return Response({"error": "server not found"}, status=status.HTTP_404_NOT_FOUND)
    server.enabled = False
    server.save(update_fields=["enabled", "updated_at"])
    return Response({"message": "stopped", "name": server.name})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def skill_catalog(request):
    return Response({"skills": list_skill_catalog()})


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def skill_configs(request):
    if request.method == "GET":
        rows = SkillConfig.objects.all().order_by("name")
        serializer = SkillConfigSerializer(rows, many=True)
        return Response(serializer.data)

    serializer = SkillConfigSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(serializer.data, status=status.HTTP_201_CREATED)


@api_view(["GET", "PUT", "DELETE"])
@permission_classes([IsAuthenticated])
def skill_config_detail(request, name: str):
    row = SkillConfig.objects.filter(name=name).first()
    if not row:
        return Response({"error": "skill not found"}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "GET":
        return Response(SkillConfigSerializer(row).data)

    if request.method == "DELETE":
        row.delete()
        return Response({"message": "deleted"})

    serializer = SkillConfigSerializer(row, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(serializer.data)


def _sanitize_skill_name(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]", "", name or "").strip()


@api_view(["GET", "PUT"])
@permission_classes([IsAuthenticated])
def skill_content_detail(request, name: str):
    safe_name = _sanitize_skill_name(name)
    if not safe_name:
        return Response({"error": "invalid skill name"}, status=status.HTTP_400_BAD_REQUEST)

    if request.method == "GET":
        content = read_skill_content(safe_name)
        if content is None:
            return Response({"error": "skill content not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response({"name": safe_name, "content": content})

    data = request.data if isinstance(request.data, dict) else {}
    content = str(data.get("content") or "")
    title = str(data.get("title") or "")
    description = str(data.get("description") or "")
    path = write_skill_content(safe_name, content, title=title, description=description)
    return Response({"name": safe_name, "path": path})
