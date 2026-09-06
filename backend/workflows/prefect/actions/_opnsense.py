from __future__ import annotations

import ipaddress
import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlparse

import requests

from .base import ActionResult

logger = logging.getLogger("workflows.prefect.actions")
_ALIAS_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,31}$")


def boolean(value: Any, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"} if isinstance(value, str) else bool(value)


def alias_action(*, operation: str, ip: str, api_url: str, api_key: str, api_secret: str, alias_name: str, timeout: int, verify_tls: bool, kill_states: bool = False) -> ActionResult:
    try:
        normalised_ip = str(ipaddress.ip_address(str(ip).strip()))
    except ValueError:
        return ActionResult(False, error="ip_address is not a valid IPv4 or IPv6 address")
    parsed = urlparse(api_url.strip())
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        return ActionResult(False, error="OPNsense api_url must be an absolute HTTPS URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        return ActionResult(False, error="OPNsense api_url must be a base URL without credentials, path, query, or fragment")
    alias_name = alias_name.strip()
    if not _ALIAS_PATTERN.fullmatch(alias_name):
        return ActionResult(False, error="alias_name must be 1-32 characters using letters, numbers, underscore, dot, or hyphen")
    if not api_key or not api_secret:
        return ActionResult(False, error="OPNsense api_key and api_secret are required")
    if operation not in {"add", "delete"}:
        return ActionResult(False, error="Unsupported OPNsense alias operation")
    base = api_url.strip().rstrip("/")
    if not verify_tls:
        logger.warning("[SECURITY] OPNsense TLS certificate verification is disabled for host %s", parsed.hostname)
    try:
        response = requests.post(
            f"{base}/api/firewall/alias_util/{operation}/{quote(alias_name, safe='')}",
            auth=(api_key, api_secret),
            json={"address": normalised_ip},
            timeout=timeout,
            verify=verify_tls,
            allow_redirects=False,
        )
        try:
            body = response.json() if response.content else {}
        except ValueError:
            body = {}
        success = 200 <= response.status_code < 300 and isinstance(body, dict) and body.get("status") == "done"
        data = {
            "provider": "opnsense",
            "ip": normalised_ip,
            "alias_name": alias_name,
            "operation": operation,
            "status_code": response.status_code,
            "api_status": body.get("status") if isinstance(body, dict) else None,
            "state_cleanup": {"requested": False},
        }
        if not success:
            return ActionResult(False, data, f"OPNsense alias {operation} failed: HTTP {response.status_code}, status={data['api_status']!r}", f"OPNsense alias {operation} for {normalised_ip} was not completed")
        warning = ""
        if operation == "add" and kill_states:
            try:
                cleanup = requests.post(
                    f"{base}/api/diagnostics/firewall/kill_states",
                    auth=(api_key, api_secret),
                    json={"filter": normalised_ip},
                    timeout=timeout,
                    verify=verify_tls,
                    allow_redirects=False,
                )
                try:
                    cleanup_body = cleanup.json() if cleanup.content else {}
                except ValueError:
                    cleanup_body = {}
                cleanup_status = cleanup_body.get("status") if isinstance(cleanup_body, dict) else None
                cleanup_ok = 200 <= cleanup.status_code < 300 and cleanup_status in {"done", "ok"}
                data["state_cleanup"] = {"requested": True, "success": cleanup_ok, "status_code": cleanup.status_code, "api_status": cleanup_status}
            except requests.RequestException as exc:
                cleanup_ok = False
                data["state_cleanup"] = {"requested": True, "success": False, "error": exc.__class__.__name__}
            if not cleanup_ok:
                warning = "State cleanup failed after the IP was blocked"
                logger.warning("OPNsense state cleanup failed for %s", normalised_ip)
        past = "blocked" if operation == "add" else "released"
        data[past] = True
        data[f"{past}_at"] = datetime.now(timezone.utc).isoformat()
        if warning:
            data["warning"] = warning
        return ActionResult(True, data, logs=f"OPNsense {past} {normalised_ip} in alias {alias_name}" + (f"; warning: {warning}" if warning else ""))
    except requests.RequestException as exc:
        return ActionResult(False, error=f"OPNsense request failed: {exc.__class__.__name__}", logs=f"OPNsense alias {operation} request failed for {normalised_ip}")
