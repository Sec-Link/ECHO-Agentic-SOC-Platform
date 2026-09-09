"""
Prefect REST client for the workflows app.

This is a thin wrapper around `requests` that talks to a Prefect Server's
REST API. It is intentionally self-contained: configuration is read directly
from environment variables here so that wiring up Prefect does not require
edits to ``settings.py``, ``requirements.txt``, or ``env.example``.

Environment variables (all optional except the first two when Prefect is in use):
    PREFECT_API_URL          Base URL of Prefect API, e.g. http://prefect-server:4200/api
    PREFECT_DEPLOYMENT_ID    UUID of the generic SOAR deployment registered on Prefect.
    PREFECT_API_KEY          Optional bearer token for Prefect Cloud / secured server.
    PREFECT_TIMEOUT_SECONDS  HTTP timeout for individual calls (default 10).

Prefect flow run states are mapped here to the ``WorkflowExecution.STATUS_CHOICES``
enum used elsewhere in the app, so the rest of the codebase never deals with
Prefect-specific vocabulary.
"""
from __future__ import annotations

import logging
import os
from base64 import b64encode
from urllib.parse import parse_qs, urlsplit
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)
PREFECT_API_VERSION = '0.8.4'


# Prefect state names → WorkflowExecution status values.
# Prefect 2.x/3.x both use the same canonical state types here.
PREFECT_STATE_TO_STATUS = {
    'SCHEDULED': 'pending',
    'PENDING': 'pending',
    'RUNNING': 'running',
    'PAUSED': 'paused',
    'COMPLETED': 'completed',
    'FAILED': 'failed',
    'CRASHED': 'failed',
    'CANCELLED': 'cancelled',
    'CANCELLING': 'cancelled',
}

TERMINAL_STATUSES = {'completed', 'failed', 'cancelled'}


class PrefectConfigError(RuntimeError):
    """Raised when Prefect env vars are missing or malformed."""


class PrefectAPIError(RuntimeError):
    """Raised when Prefect API returns a non-2xx response."""


class PrefectFlowRunNotFound(PrefectAPIError):
    """A retained event can outlive its deleted flow run."""


class PrefectDeploymentNotFound(PrefectAPIError):
    """A local workflow can reference a deployment removed from Prefect."""


def _api_base() -> str:
    url = os.getenv('PREFECT_API_URL', '').rstrip('/')
    if not url:
        raise PrefectConfigError(
            'PREFECT_API_URL is not set; cannot dispatch to Prefect.'
        )
    return url


def _deployment_id() -> str:
    deployment_id = os.getenv('PREFECT_DEPLOYMENT_ID', '').strip()
    if not deployment_id:
        raise PrefectConfigError(
            'PREFECT_DEPLOYMENT_ID is not set; register the generic SOAR '
            'deployment on Prefect first.'
        )
    return deployment_id


def resolve_deployment_id(override: Optional[str] = None) -> str:
    if override:
        return str(override).strip()
    return _deployment_id()


def _headers() -> Dict[str, str]:
    headers = {
        'Content-Type': 'application/json',
        'X-PREFECT-API-VERSION': PREFECT_API_VERSION,
    }
    api_key = os.getenv('PREFECT_API_KEY', '').strip()
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'
    elif auth_string := os.getenv('PREFECT_API_AUTH_STRING', '').strip():
        headers['Authorization'] = 'Basic ' + b64encode(auth_string.encode()).decode()
    return headers


def _timeout() -> float:
    try:
        return float(os.getenv('PREFECT_TIMEOUT_SECONDS', '10'))
    except (TypeError, ValueError):
        return 10.0


def _request(
    method: str,
    path: str,
    *,
    operation: str,
    allowed_statuses: tuple[int, ...] = (),
    **kwargs: Any,
):
    try:
        response = getattr(requests, method)(
            f'{_api_base()}{path}',
            headers=_headers(),
            timeout=_timeout(),
            **kwargs,
        )
    except requests.RequestException as exc:
        raise PrefectAPIError(f'Prefect {operation} request failed: {exc}') from exc
    if response.status_code >= 400 and response.status_code not in allowed_statuses:
        raise PrefectAPIError(
            f'Prefect {operation} returned {response.status_code}: {response.text[:500]}'
        )
    return response


def is_configured(deployment_id: Optional[str] = None) -> bool:
    """Cheap pre-flight check used by callers that want to fall back gracefully."""
    if not os.getenv('PREFECT_API_URL'):
        return False
    if deployment_id:
        return True
    return bool(os.getenv('PREFECT_DEPLOYMENT_ID'))


def map_state_to_status(state_type: Optional[str]) -> str:
    """Map a Prefect state_type string to our internal execution status."""
    if not state_type:
        return 'pending'
    return PREFECT_STATE_TO_STATUS.get(str(state_type).upper(), 'running')


def create_flow_run(
    *,
    parameters: Dict[str, Any],
    name: Optional[str] = None,
    tags: Optional[list] = None,
    deployment_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create a flow run on the configured generic deployment.

    Returns the parsed JSON body, which contains at minimum ``id`` (the flow
    run UUID) and ``state``.
    """
    path = f"/deployments/{resolve_deployment_id(deployment_id)}/create_flow_run"
    payload: Dict[str, Any] = {'parameters': parameters}
    if name:
        payload['name'] = name
    if tags:
        payload['tags'] = list(tags)
    if idempotency_key:
        payload['idempotency_key'] = str(idempotency_key)
    return _request('post', path, operation='create_flow_run', json=payload).json()


def get_flow_run(flow_run_id: str) -> Dict[str, Any]:
    """Fetch the full flow run record (state, timestamps, parameters)."""
    resp = _request(
        'get',
        f"/flow_runs/{flow_run_id}",
        operation='get_flow_run',
        allowed_statuses=(404,),
    )
    if resp.status_code == 404:
        raise PrefectFlowRunNotFound(f'Flow run {flow_run_id} not found on Prefect.')
    return resp.json()


def iter_events(event_filter: Dict[str, Any]):
    """Page through retained events without trusting a server-supplied host."""
    page = _request('post', '/events/filter', operation='read events',
                    json={'filter': event_filter}).json()
    while True:
        yield from page.get('events', [])
        next_page = page.get('next_page')
        if not next_page:
            return
        token = parse_qs(urlsplit(next_page).query).get('page-token')
        if not token:
            raise PrefectAPIError('Prefect event page is missing its continuation token.')
        page = _request('get', '/events/filter/next', operation='read next events',
                        params={'page-token': token[0]}).json()


def cancel_flow_run(flow_run_id: str) -> None:
    """
    Ask Prefect to cancel an in-flight flow run. Idempotent: cancelling an
    already-terminal run is a no-op from our perspective.
    """
    payload = {
        'state': {'type': 'CANCELLING', 'name': 'Cancelling'},
        'force': False,
    }
    _request(
        'post',
        f"/flow_runs/{flow_run_id}/set_state",
        operation='cancel_flow_run',
        allowed_statuses=(409,),
        json=payload,
    )


def list_deployment_schedules(deployment_id: str) -> list[Dict[str, Any]]:
    resp = _request(
        'get',
        f"/deployments/{deployment_id}/schedules",
        operation='list schedules',
    )
    data = resp.json()
    return data if isinstance(data, list) else []


def create_deployment_schedule(
    *,
    deployment_id: str,
    slug: str,
    schedule: Dict[str, Any],
    is_active: bool,
    parameters: Dict[str, Any],
) -> Dict[str, Any]:
    payload = [{
        'slug': slug,
        'schedule': schedule,
        'active': bool(is_active),
        'parameters': parameters,
    }]
    resp = _request(
        'post',
        f"/deployments/{deployment_id}/schedules",
        operation='create schedule',
        json=payload,
    )
    data = resp.json()
    return data[0] if isinstance(data, list) and data else {}


def patch_deployment_schedule(
    *,
    deployment_id: str,
    schedule_id: str,
    slug: str,
    schedule: Dict[str, Any],
    is_active: bool,
    parameters: Dict[str, Any],
) -> Dict[str, Any]:
    payload = {
        'slug': slug,
        'schedule': schedule,
        'active': bool(is_active),
        'parameters': parameters,
    }
    resp = _request(
        'patch',
        f"/deployments/{deployment_id}/schedules/{schedule_id}",
        operation='update schedule',
        json=payload,
    )
    return resp.json() if resp.text.strip() else {}


def delete_deployment_schedule(*, deployment_id: str, schedule_id: str) -> None:
    _request(
        'delete',
        f"/deployments/{deployment_id}/schedules/{schedule_id}",
        operation='delete schedule',
        allowed_statuses=(404,),
    )


def upsert_deployment_schedule(
    *,
    deployment_id: str,
    slug: str,
    schedule: Dict[str, Any],
    is_active: bool,
    parameters: Dict[str, Any],
) -> Dict[str, Any]:
    existing = next((item for item in list_deployment_schedules(deployment_id) if item.get('slug') == slug), None)
    if existing and existing.get('id'):
        return patch_deployment_schedule(
            deployment_id=deployment_id,
            schedule_id=str(existing['id']),
            slug=slug,
            schedule=schedule,
            is_active=is_active,
            parameters=parameters,
        )
    return create_deployment_schedule(
        deployment_id=deployment_id,
        slug=slug,
        schedule=schedule,
        is_active=is_active,
        parameters=parameters,
    )


def delete_deployment_schedule_by_slug(*, deployment_id: str, slug: str) -> None:
    existing = next((item for item in list_deployment_schedules(deployment_id) if item.get('slug') == slug), None)
    if existing and existing.get('id'):
        delete_deployment_schedule(deployment_id=deployment_id, schedule_id=str(existing['id']))


def list_deployments(limit: int = 200) -> list[Dict[str, Any]]:
    """List Prefect deployments for UI sync."""
    resp = _request(
        'post',
        '/deployments/filter',
        operation='list_deployments',
        json={'limit': max(int(limit), 1)},
    )
    data = resp.json()
    if isinstance(data, dict) and 'deployments' in data:
        return data.get('deployments') or []
    return data if isinstance(data, list) else []


def get_deployment(deployment_id: str) -> Dict[str, Any]:
    """Fetch a single Prefect deployment by id."""
    resp = _request(
        'get',
        f"/deployments/{deployment_id}",
        operation='get_deployment',
        allowed_statuses=(404,),
    )
    if resp.status_code == 404:
        raise PrefectDeploymentNotFound(f'Deployment {deployment_id} not found on Prefect.')
    return resp.json()


def update_deployment(
    *,
    deployment_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Update deployment metadata (name/description/tags/parameters)."""
    resp = _request(
        'patch',
        f"/deployments/{deployment_id}",
        operation='update_deployment',
        json=payload,
    )
    if not resp.text.strip():
        return {}
    return resp.json()


def has_api() -> bool:
    """Return True when Prefect API URL is configured."""
    return bool(os.getenv('PREFECT_API_URL'))


def get_flow_by_name(flow_name: str) -> Dict[str, Any] | None:
    """Fetch a Prefect flow by name if it exists."""
    resp = _request(
        'get',
        f"/flows/name/{flow_name}",
        operation='get_flow_by_name',
        allowed_statuses=(404,),
    )
    if resp.status_code == 404:
        return None
    return resp.json()


def create_flow(flow_name: str) -> Dict[str, Any]:
    """Create a Prefect flow if it does not already exist."""
    payload = {'name': flow_name}
    resp = _request('post', '/flows/', operation='create_flow', json=payload)
    return resp.json()


def get_or_create_flow_id(flow_name: str) -> str:
    """Return the flow id for the given name, creating the flow if needed."""
    existing = get_flow_by_name(flow_name)
    if existing and existing.get('id'):
        return str(existing['id'])
    created = create_flow(flow_name)
    return str(created.get('id'))


def create_deployment(
    *,
    flow_id: str,
    name: str,
    entrypoint: str,
    parameters: Dict[str, Any] | None = None,
    tags: list[str] | None = None,
    work_pool_name: Optional[str] = None,
    work_queue_name: Optional[str] = None,
    job_variables: Dict[str, Any] | None = None,
    path: Optional[str] = None,
    pull_steps: list[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    """Create a Prefect deployment for the given flow."""
    payload: Dict[str, Any] = {
        'flow_id': flow_id,
        'name': name,
        'entrypoint': entrypoint,
    }
    if parameters is not None:
        payload['parameters'] = parameters
    if tags:
        payload['tags'] = list(tags)
    if work_pool_name:
        payload['work_pool_name'] = str(work_pool_name)
    if work_queue_name:
        payload['work_queue_name'] = str(work_queue_name)
    if job_variables:
        payload['job_variables'] = dict(job_variables)
    if path:
        payload['path'] = str(path)
    if pull_steps is not None:
        payload['pull_steps'] = list(pull_steps)
    resp = _request('post', '/deployments/', operation='create_deployment', json=payload)
    return resp.json()
