import math
from typing import Any, Dict, List, Optional

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from ai_assistant.models import MCPToolExecution, MCPToolStats, SkillStats


def start_mcp_execution(
    tool_name: str,
    arguments: Optional[Dict[str, Any]] = None,
    source: str = "",
    endpoint: str = "",
) -> str:
    exec_row = MCPToolExecution.objects.create(
        tool_name=tool_name or "",
        arguments=arguments or {},
        status="running",
        source=source or "",
        endpoint=endpoint or "",
    )
    return str(exec_row.id)


def finish_mcp_execution(exec_id: str, success: bool, error: str = "") -> None:
    if not exec_id:
        return
    now = timezone.now()
    row = MCPToolExecution.objects.filter(id=exec_id).first()
    if not row:
        return
    duration_ms = None
    if row.start_time:
        duration_ms = int(max(0, (now - row.start_time).total_seconds() * 1000))
    row.status = "completed" if success else "failed"
    row.error = (error or "")[:2000]
    row.end_time = now
    row.duration_ms = duration_ms
    row.save(update_fields=["status", "error", "end_time", "duration_ms"])


def update_mcp_stats(tool_name: str, success: bool) -> None:
    if not tool_name:
        return
    now = timezone.now()
    with transaction.atomic():
        stats, created = MCPToolStats.objects.select_for_update().get_or_create(tool_name=tool_name)
        MCPToolStats.objects.filter(id=stats.id).update(
            total_calls=F("total_calls") + 1,
            success_calls=F("success_calls") + (1 if success else 0),
            failed_calls=F("failed_calls") + (0 if success else 1),
            last_call_time=now,
        )


def record_skill_call(skill_name: str, success: bool) -> None:
    if not skill_name:
        return
    now = timezone.now()
    with transaction.atomic():
        stats, created = SkillStats.objects.select_for_update().get_or_create(skill_name=skill_name)
        SkillStats.objects.filter(id=stats.id).update(
            total_calls=F("total_calls") + 1,
            success_calls=F("success_calls") + (1 if success else 0),
            failed_calls=F("failed_calls") + (0 if success else 1),
            last_call_time=now,
        )


def _serialize_execution(row: MCPToolExecution) -> Dict[str, Any]:
    return {
        "id": str(row.id),
        "tool_name": row.tool_name,
        "arguments": row.arguments,
        "response_payload": row.response_payload,
        "status": row.status,
        "error": row.error,
        "start_time": row.start_time.isoformat() if row.start_time else "",
        "end_time": row.end_time.isoformat() if row.end_time else "",
        "duration_ms": row.duration_ms,
        "endpoint": row.endpoint,
        "source": row.source,
    }


def _serialize_stats(row: MCPToolStats) -> Dict[str, Any]:
    return {
        "tool_name": row.tool_name,
        "total_calls": row.total_calls,
        "success_calls": row.success_calls,
        "failed_calls": row.failed_calls,
        "last_call_time": row.last_call_time.isoformat() if row.last_call_time else "",
    }


def _serialize_skill_stats(row: SkillStats) -> Dict[str, Any]:
    return {
        "skill_name": row.skill_name,
        "total_calls": row.total_calls,
        "success_calls": row.success_calls,
        "failed_calls": row.failed_calls,
        "last_call_time": row.last_call_time.isoformat() if row.last_call_time else "",
    }


def get_mcp_monitor(page: int, page_size: int, status: str = "", tool_name: str = "") -> Dict[str, Any]:
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 20), 100))

    qs = MCPToolExecution.objects.all().order_by("-start_time")
    if status:
        qs = qs.filter(status=status)
    if tool_name:
        qs = qs.filter(tool_name__icontains=tool_name)

    total = qs.count()
    offset = (page - 1) * page_size
    rows = list(qs[offset : offset + page_size])
    total_pages = max(1, int(math.ceil(total / float(page_size))) if page_size else 1)

    stats_rows = MCPToolStats.objects.all().order_by("tool_name")
    stats = {row.tool_name: _serialize_stats(row) for row in stats_rows}

    return {
        "executions": [_serialize_execution(r) for r in rows],
        "stats": stats,
        "timestamp": timezone.now().isoformat(),
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


def get_skill_monitor() -> Dict[str, Any]:
    rows = list(SkillStats.objects.all().order_by("skill_name"))
    total_calls = sum(r.total_calls for r in rows)
    total_success = sum(r.success_calls for r in rows)
    total_failed = sum(r.failed_calls for r in rows)
    return {
        "summary": {
            "total_calls": total_calls,
            "success": total_success,
            "failed": total_failed,
        },
        "stats": [_serialize_skill_stats(r) for r in rows],
        "timestamp": timezone.now().isoformat(),
    }
