from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict

import requests

from ._opnsense import alias_action, boolean
from .base import ActionResult, BaseAction

logger = logging.getLogger(__name__)


class BlockIPAction(BaseAction):
    name = "Block IP"
    description = "Block an IP address via a security-device API"
    category = "containment"
    config_schema = {
        "type": "object",
        "properties": {
            "provider": {"type": "string", "enum": ["generic", "opnsense"], "default": "generic"},
            "ip_address": {"type": "string"},
            "api_url": {"type": "string"},
            "api_key": {"type": "string", "writeOnly": True, "x-sensitive": True, "x-secret-bindings": ["provider", "api_url", "alias_name"], "x-secret-rebindable": ["alias_name"]},
            "api_secret": {"type": "string", "writeOnly": True, "x-sensitive": True, "x-secret-bindings": ["provider", "api_url", "alias_name"], "x-secret-rebindable": ["alias_name"]},
            "alias_name": {"type": "string", "default": "ARGUS_BLOCKLIST"},
            "verify_tls": {"type": "boolean", "default": True},
            "kill_states": {"type": "boolean", "default": False},
            "duration_hours": {"type": "integer", "default": 24},
            "reason": {"type": "string"},
            "timeout": {"type": "integer", "default": 15},
        },
        "required": ["ip_address", "api_url", "api_key"],
    }

    def execute(self, config: Dict[str, Any], context: Dict[str, Any]) -> ActionResult:
        provider = str(config.get("provider") or "generic").strip().lower()
        ip = self.resolve_variables(config.get("ip_address", ""), context)
        url = self.resolve_variables(config.get("api_url", ""), context)
        key = self.resolve_variables(config.get("api_key", ""), context)
        try:
            timeout = int(config.get("timeout", 15))
        except (TypeError, ValueError):
            return ActionResult(False, error="timeout must be an integer number of seconds")
        if not ip:
            return ActionResult(False, error="ip_address is empty after variable resolution", logs="Block IP aborted: no IP address provided")
        if provider == "opnsense":
            return alias_action(
                operation="add",
                ip=ip,
                api_url=url,
                api_key=key,
                api_secret=self.resolve_variables(config.get("api_secret", ""), context),
                alias_name=str(config.get("alias_name") or "ARGUS_BLOCKLIST"),
                timeout=max(1, min(timeout, 120)),
                verify_tls=boolean(config.get("verify_tls"), True),
                kill_states=boolean(config.get("kill_states"), False),
            )
        if provider != "generic":
            return ActionResult(False, error=f"Unsupported Block IP provider: {provider}")
        try:
            response = requests.post(
                url,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "ip": ip,
                    "action": "block",
                    "duration_hours": config.get("duration_hours", 24),
                    "reason": self.resolve_variables(config.get("reason", "Blocked by SOAR workflow"), context),
                },
                timeout=timeout,
            )
            logger.warning("[CONTAINMENT] Block IP %s via %s -> %s", ip, url, response.status_code)
            return ActionResult(response.ok, {"ip": ip, "blocked": response.ok, "status_code": response.status_code, "response_body": response.text[:2000], "blocked_at": datetime.now(timezone.utc).isoformat()}, logs=f"Block IP {ip}: HTTP {response.status_code}")
        except Exception as exc:
            return ActionResult(False, error=str(exc), logs=f"Block IP failed for {ip}: {exc}")

