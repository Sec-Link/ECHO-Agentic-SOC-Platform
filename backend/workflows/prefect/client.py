from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime, time as datetime_time, timezone
from typing import Any, Dict, Iterable

import requests

logger = logging.getLogger(__name__)


class BackendAPIError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _url(path: str) -> str:
    base = os.getenv("BACKEND_ORIGIN", "").rstrip("/")
    if not base:
        raise BackendAPIError("BACKEND_ORIGIN is required by the workflow worker.")
    return f"{base}{path}"


def _headers() -> Dict[str, str]:
    token = os.getenv("PREFECT_WORKER_TOKEN", "").strip()
    if not token:
        raise BackendAPIError("PREFECT_WORKER_TOKEN is required by the workflow worker.")
    return {"Authorization": f"Token {token}", "Content-Type": "application/json"}


def _request(method: str, path: str, **kwargs: Any) -> Any:
    try:
        response = requests.request(method, _url(path), headers=_headers(), timeout=15, **kwargs)
    except requests.RequestException as exc:
        raise BackendAPIError(f"Argus API {method} {path} request failed: {exc}") from exc
    if response.status_code >= 400:
        raise BackendAPIError(
            f"Argus API {method} {path} returned {response.status_code}: {response.text[:500]}",
            response.status_code,
        )
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError as exc:
        raise BackendAPIError(
            f"Argus API {method} {path} returned invalid JSON.",
            response.status_code,
        ) from exc


def register_runtime(payload: Dict[str, Any]) -> str:
    result = _request("POST", "/api/v1/workflows/executions/register-runtime/", json=payload)
    execution_id = str(result.get("execution_id") or "")
    if not execution_id:
        raise BackendAPIError("Argus runtime registration did not return execution_id.")
    return execution_id


def get_runtime_policy() -> Dict[str, Any]:
    result = _request("GET", "/api/v1/workflows/executions/runtime-policy/")
    return result if isinstance(result, dict) else {}


def sync_runtime(execution_id: str, payload: Dict[str, Any], attempts: int = 3) -> bool:
    path = f"/api/v1/workflows/executions/{execution_id}/runtime-sync/"
    for attempt in range(1, attempts + 1):
        try:
            _request("POST", path, json=payload)
            return True
        except (BackendAPIError, requests.RequestException) as exc:
            if attempt == attempts:
                logger.error("Runtime snapshot delivery failed after %s attempts: %s", attempts, exc)
                return False
            time.sleep(attempt)
    return False


def create_ticket(payload: Dict[str, Any]) -> Dict[str, Any]:
    return _request("POST", "/api/v1/tickets/", json=payload)


def get_ticket(ticket_number: str) -> Dict[str, Any] | None:
    try:
        return _request("GET", f"/api/v1/tickets/{ticket_number}/")
    except BackendAPIError as exc:
        if exc.status_code == 404:
            return None
        raise


def _as_utc(value: Any, end_of_day: bool = False) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        if len(text) == 10:
            return datetime.combine(
                date.fromisoformat(text),
                datetime_time.max if end_of_day else datetime_time.min,
                timezone.utc,
            )
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return (parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed).astimezone(timezone.utc)
    except ValueError:
        return None


def _matches_ticket(ticket: Dict[str, Any], params: Dict[str, Any]) -> bool:
    exact_fields = {
        "title": "title",
        "priority": "priority",
        "status": "status",
        "current_assign_group": "current_assign_group",
        "current_assign_owner": "current_assign_owner",
        "assign_group": "current_assign_group",
        "assign_owner": "current_assign_owner",
    }
    for parameter, field in exact_fields.items():
        expected = params.get(parameter)
        if expected not in (None, "") and str(ticket.get(field, "")).casefold() != str(expected).casefold():
            return False
    for field in ("created", "updated"):
        actual = _as_utc(ticket.get(f"{field}_time"))
        lower = _as_utc(params.get(f"{field}_from") or params.get(f"{field}_time_from"))
        upper = _as_utc(params.get(f"{field}_to") or params.get(f"{field}_time_to"), end_of_day=True)
        if (lower or upper) and not actual:
            return False
        if actual and ((lower and actual < lower) or (upper and actual > upper)):
            return False
    return True


def list_tickets(params: Dict[str, Any]) -> list[Dict[str, Any]]:
    server_params = {key: params[key] for key in ("created_from", "created_to") if params.get(key) not in (None, "")}
    result = _request("GET", "/api/v1/tickets/", params=server_params)
    if isinstance(result, list):
        tickets = result
    else:
        tickets = list(result.get("results") or []) if isinstance(result, dict) else []
    if any(params.get(key) not in (None, "") for key in ("current_assign_group", "current_assign_owner", "assign_group", "assign_owner")):
        tickets = [detail for ticket in tickets if (detail := get_ticket(str(ticket["ticket_number"]))) is not None]
    return [ticket for ticket in tickets if _matches_ticket(ticket, params)]


def update_ticket(ticket_number: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return _request("PATCH", f"/api/v1/tickets/{ticket_number}/", json=payload)


def get_tickets(ticket_numbers: Iterable[str]) -> list[Dict[str, Any]]:
    return [ticket for number in ticket_numbers if (ticket := get_ticket(number))]
