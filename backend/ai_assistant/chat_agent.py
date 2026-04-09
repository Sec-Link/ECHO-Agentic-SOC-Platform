import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests
from django.conf import settings

from ai_assistant.knowledge_base import ensure_index, format_search_results, get_categories, scan_knowledge_base, search_knowledge_base
from ai_assistant.mcp_gateway import get_mcp_tools_catalog, invoke_mcp_tool_explicit
from ai_assistant.skill_library import list_skills, read_skill
from ai_assistant.monitoring import finish_mcp_execution, record_skill_call, start_mcp_execution, update_mcp_stats

logger = logging.getLogger(__name__)


@dataclass
class ToolExecutionResult:
    content: str
    is_error: bool = False


def _normalize_base_url(raw: str) -> str:
    base = (raw or "").strip()
    if not base:
        return base
    if "/v1" in base:
        return base.rstrip("/")
    if base.rstrip("/").endswith("/openai"):
        return f"{base.rstrip('/')}/v1"
    return base.rstrip("/")


def _openai_chat_config(overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    overrides = overrides or {}
    api_key = overrides.get("api_key") or getattr(settings, "OPENAI_API_KEY", "")
    base_url = overrides.get("base_url") or getattr(settings, "OPENAI_BASE_URL", "https://api.openai.com/v1")
    base_url = _normalize_base_url(base_url)
    model = overrides.get("model") or getattr(settings, "OPENAI_MODEL", "gpt-5.1-codex")
    timeout_value = overrides.get("timeout_seconds")
    if timeout_value in (None, ""):
        timeout_value = getattr(settings, "OPENAI_TIMEOUT_SECONDS", 45)
    try:
        timeout = int(timeout_value or 45)
    except Exception:
        timeout = 45
    return {"api_key": api_key, "base_url": base_url, "model": model, "timeout": timeout}


def _parse_chat_stream(res: requests.Response) -> Dict[str, Any]:
    message: Dict[str, Any] = {"role": "assistant", "content": ""}
    tool_calls: Dict[int, Dict[str, Any]] = {}
    finish_reason = None

    for raw in res.iter_lines(decode_unicode=False):
        if raw is None:
            continue
        if not raw:
            continue
        if isinstance(raw, bytes):
            try:
                line = raw.decode("utf-8", errors="replace").strip()
            except Exception:
                line = raw.decode(errors="replace").strip()
        else:
            line = str(raw).strip()
        if line.startswith("data:"):
            line = line[len("data:"):].strip()
        if not line or line == "[DONE]":
            if line == "[DONE]":
                break
            continue
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        choices = payload.get("choices")
        if not isinstance(choices, list):
            continue
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            if choice.get("finish_reason") is not None:
                finish_reason = choice.get("finish_reason")
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                continue
            if delta.get("role"):
                message["role"] = delta.get("role")
            if isinstance(delta.get("content"), str):
                message["content"] = (message.get("content") or "") + delta.get("content")
            if isinstance(delta.get("tool_calls"), list):
                for tc in delta.get("tool_calls"):
                    if not isinstance(tc, dict):
                        continue
                    index = tc.get("index")
                    if index is None:
                        continue
                    if index not in tool_calls:
                        tool_calls[index] = {
                            "id": tc.get("id"),
                            "type": tc.get("type"),
                            "function": {
                                "name": "",
                                "arguments": "",
                            },
                        }
                    current = tool_calls[index]
                    if tc.get("id"):
                        current["id"] = tc.get("id")
                    if tc.get("type"):
                        current["type"] = tc.get("type")
                    fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                    if fn.get("name"):
                        current["function"]["name"] = fn.get("name")
                    if isinstance(fn.get("arguments"), str):
                        current["function"]["arguments"] = (current["function"].get("arguments") or "") + fn.get("arguments")

    if tool_calls:
        message["tool_calls"] = [tool_calls[k] for k in sorted(tool_calls.keys())]

    return {"choices": [{"message": message, "finish_reason": finish_reason}]}


def _call_openai_chat(messages: List[Dict[str, Any]], tools: List[Dict[str, Any]], overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = _openai_chat_config(overrides)
    if not cfg["api_key"]:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    url = f"{cfg['base_url'].rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }
    payload: Dict[str, Any] = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": 0.2,
        "stream": True,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    res = requests.post(url, headers=headers, json=payload, timeout=cfg["timeout"], stream=True)
    res.encoding = "utf-8"
    if res.status_code >= 400:
        raise RuntimeError(f"OpenAI chat error: {res.status_code} {res.text[:500]}")
    content_type = str(res.headers.get("content-type") or "")
    if "text/event-stream" in content_type:
        return _parse_chat_stream(res)
    try:
        return res.json()
    except Exception:
        return _parse_chat_stream(res)


def _safe_tool_name(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", name).strip("_")


def _internal_tools() -> Tuple[List[Dict[str, Any]], Dict[str, Callable[[Dict[str, Any]], ToolExecutionResult]]]:
    tools: List[Dict[str, Any]] = []
    handlers: Dict[str, Callable[[Dict[str, Any]], ToolExecutionResult]] = {}

    def register(name: str, description: str, schema: Dict[str, Any], handler: Callable[[Dict[str, Any]], ToolExecutionResult]) -> None:
        tools.append({
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": schema,
            },
        })
        handlers[name] = handler

    register(
        "list_skills",
        "List all available skills from the local skills library.",
        {"type": "object", "properties": {}, "required": []},
        lambda args: ToolExecutionResult("\n".join(list_skills()) or "No skills found."),
    )

    def _read_skill(args: Dict[str, Any]) -> ToolExecutionResult:
        name = str(args.get("skill_name") or "").strip()
        if not name:
            return ToolExecutionResult("skill_name is required", is_error=True)
        doc = read_skill(name)
        if not doc:
            record_skill_call(name, False)
            return ToolExecutionResult(f"Skill not found: {name}", is_error=True)
        record_skill_call(name, True)
        body = [f"## Skill: {doc.name}"]
        if doc.description:
            body.append(f"**Description**: {doc.description}")
        body.append(doc.content)
        body.append(f"*Path: {doc.path}*")
        return ToolExecutionResult("\n\n".join(body))

    register(
        "read_skill",
        "Read a specific skill document by name.",
        {
            "type": "object",
            "properties": {
                "skill_name": {"type": "string", "description": "Skill directory name"},
            },
            "required": ["skill_name"],
        },
        _read_skill,
    )

    def _list_risk_types(args: Dict[str, Any]) -> ToolExecutionResult:
        categories = get_categories()
        if not categories:
            scan_knowledge_base()
            categories = get_categories()
        if not categories:
            return ToolExecutionResult("No knowledge base categories found.")
        return ToolExecutionResult("\n".join(categories))

    register(
        "list_knowledge_risk_types",
        "List available knowledge base risk types (categories).",
        {"type": "object", "properties": {}, "required": []},
        _list_risk_types,
    )

    def _search_kb(args: Dict[str, Any]) -> ToolExecutionResult:
        query = str(args.get("query") or "").strip()
        if not query:
            return ToolExecutionResult("query is required", is_error=True)
        risk_type = str(args.get("risk_type") or "").strip()
        ensure_index()
        results = search_knowledge_base(query=query, risk_type=risk_type)
        expanded = results
        text, item_ids = format_search_results(expanded)
        return ToolExecutionResult(text)

    register(
        "search_knowledge_base",
        "Search the knowledge base with semantic + keyword matching.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "risk_type": {"type": "string", "description": "Optional category"},
            },
            "required": ["query"],
        },
        _search_kb,
    )

    return tools, handlers


def _external_tools(overrides: Optional[Dict[str, Any]] = None) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, str]]]:
    catalog = get_mcp_tools_catalog(overrides=overrides)
    tools: List[Dict[str, Any]] = []
    mapping: Dict[str, Dict[str, str]] = {}
    for idx, entry in enumerate(catalog):
        endpoint = str(entry.get("endpoint") or "")
        if not endpoint:
            continue
        for tool in entry.get("tools") or []:
            name = str(tool.get("name") or "").strip()
            if not name:
                continue
            safe_name = _safe_tool_name(name)
            openai_name = f"mcp__{idx}__{safe_name}"
            mapping[openai_name] = {"endpoint": endpoint, "tool_name": name}
            schema = tool.get("inputSchema") if isinstance(tool.get("inputSchema"), dict) else {}
            tools.append({
                "type": "function",
                "function": {
                    "name": openai_name,
                    "description": f"External MCP tool: {name}",
                    "parameters": schema or {"type": "object", "properties": {}},
                },
            })
    return tools, mapping


def _tool_result_to_text(result: Any) -> str:
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=True)
    except Exception:
        return str(result)


def _execute_external_tool(tool_name: str, args: Dict[str, Any], mapping: Dict[str, Dict[str, str]], overrides: Optional[Dict[str, Any]]) -> ToolExecutionResult:
    meta = mapping.get(tool_name) or {}
    endpoint = meta.get("endpoint")
    actual_tool = meta.get("tool_name")
    if not endpoint or not actual_tool:
        return ToolExecutionResult(f"External tool mapping not found for {tool_name}", is_error=True)
    payload = invoke_mcp_tool_explicit(tool_name=actual_tool, arguments=args, target_mcp=endpoint, overrides=overrides)
    if payload is None:
        return ToolExecutionResult(f"External MCP tool call failed: {actual_tool}", is_error=True)
    return ToolExecutionResult(_tool_result_to_text(payload))


def _parse_tool_arguments(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {"raw": raw}
    return {}


def run_chat_agent(
    user_input: str,
    history_messages: Optional[List[Dict[str, Any]]] = None,
    overrides: Optional[Dict[str, Any]] = None,
    recommended_skills: Optional[List[str]] = None,
) -> Dict[str, Any]:
    if not user_input:
        raise RuntimeError("message is required")

    history_messages = history_messages or []
    recommended_skills = recommended_skills or []

    system_prompt = (
        "You are a SOC analyst assistant. If tool calls are required, choose the most appropriate tool. "
        "If you do not need a tool, reply directly. "
        "When responding to the user, include a short analysis summary in a second paragraph starting with "
        "\"AI thinking:\" (max 80 words)."
    )
    if recommended_skills:
        skills_hint = ", ".join([f"`{s}`" for s in recommended_skills])
        system_prompt += f"\nRecommended skills: {skills_hint}. Use read_skill to load details when needed."

    messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for msg in history_messages:
        role = str(msg.get("role") or "").strip()
        content = msg.get("content")
        if role and content is not None:
            messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": user_input})

    internal_tools, internal_handlers = _internal_tools()
    external_tools, external_mapping = _external_tools(overrides=overrides)
    tools = internal_tools + external_tools

    max_iter_value = None
    if isinstance(overrides, dict):
        max_iter_value = overrides.get("max_iterations")
    if max_iter_value in (None, ""):
        max_iter_value = getattr(settings, "AI_CHAT_MAX_ITERATIONS", 6)
    try:
        max_iterations = int(max_iter_value or 6)
    except Exception:
        max_iterations = 6

    trace: List[Dict[str, Any]] = []
    iteration = 0
    for _ in range(max_iterations):
        iteration += 1
        trace.append({"type": "iteration_start", "iteration": iteration})
        trace.append({"type": "model_call", "iteration": iteration})
        response = _call_openai_chat(messages, tools, overrides=overrides)
        choices = response.get("choices") if isinstance(response, dict) else None
        if not isinstance(choices, list) or not choices:
            trace.append({"type": "model_error", "iteration": iteration, "error": "No response"})
            return {"response": "No response", "raw": response, "trace": trace}
        message = choices[0].get("message") or {}
        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            trace.append({"type": "tool_calls_detected", "iteration": iteration, "count": len(tool_calls)})
            messages.append({
                "role": "assistant",
                "content": message.get("content") or "",
                "tool_calls": tool_calls,
            })
            for tool_call in tool_calls:
                tool_id = tool_call.get("id")
                func = tool_call.get("function") or {}
                tool_name = func.get("name") or ""
                args = _parse_tool_arguments(func.get("arguments"))
                exec_id = ""
                actual_tool_name = tool_name
                endpoint = ""
                source = "internal"
                if tool_name in external_mapping:
                    actual_tool_name = external_mapping[tool_name].get("tool_name") or tool_name
                    endpoint = external_mapping[tool_name].get("endpoint") or ""
                    source = "external"
                trace.append({
                    "type": "tool_call",
                    "iteration": iteration,
                    "tool": actual_tool_name,
                    "source": source,
                    "endpoint": endpoint,
                    "arguments": args,
                })
                exec_id = start_mcp_execution(actual_tool_name, args, source=source, endpoint=endpoint)
                try:
                    if tool_name in internal_handlers:
                        result = internal_handlers[tool_name](args)
                    elif tool_name in external_mapping:
                        result = _execute_external_tool(tool_name, args, external_mapping, overrides)
                    else:
                        result = ToolExecutionResult(f"Unknown tool: {tool_name}", is_error=True)
                except Exception as exc:
                    result = ToolExecutionResult(f"Tool execution failed: {exc}", is_error=True)
                finish_mcp_execution(exec_id, not result.is_error, error=result.content if result.is_error else "")
                update_mcp_stats(actual_tool_name, not result.is_error)
                trace.append({
                    "type": "tool_result",
                    "iteration": iteration,
                    "tool": actual_tool_name,
                    "success": not result.is_error,
                    "content": result.content,
                    "execution_id": exec_id,
                })
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "content": result.content,
                })
            continue

        content = message.get("content") or ""
        summary = ""
        if "AI thinking:" in content:
            parts = content.split("AI thinking:", 1)
            content = parts[0].strip()
            summary = parts[1].strip()
        trace.append({"type": "assistant_response", "iteration": iteration, "content": content})
        if summary:
            trace.append({"type": "analysis_summary", "iteration": iteration, "content": summary})
        return {
            "response": content,
            "raw": response,
            "trace": trace,
        }

    trace.append({"type": "max_iterations", "iteration": iteration})
    return {"response": "Reached max iterations without final response.", "trace": trace}
