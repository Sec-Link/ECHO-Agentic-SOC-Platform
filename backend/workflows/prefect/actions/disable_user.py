from __future__ import annotations

import logging
from typing import Any, Dict

import requests

from .base import ActionResult, BaseAction

logger = logging.getLogger(__name__)


class DisableUserAction(BaseAction):
    name = "Disable User"
    description = "Disable a user account via a security-device or AD API"
    category = "containment"
    config_schema = {
        "type": "object",
        "properties": {
            "username": {"type": "string"},
            "api_url": {"type": "string"},
            "api_key": {"type": "string", "writeOnly": True, "x-sensitive": True, "x-secret-bindings": ["api_url"]},
            "reason": {"type": "string"},
            "timeout": {"type": "integer", "default": 15},
        },
        "required": ["username", "api_url", "api_key"],
    }

    def execute(self, config: Dict[str, Any], context: Dict[str, Any]) -> ActionResult:
        username = self.resolve_variables(config.get("username", ""), context)
        if not username:
            return ActionResult(False, error="username is empty after variable resolution", logs="Disable User aborted: no username provided")
        url = self.resolve_variables(config.get("api_url", ""), context)
        try:
            response = requests.post(
                url,
                headers={"Authorization": f"Bearer {self.resolve_variables(config.get('api_key', ''), context)}", "Content-Type": "application/json"},
                json={"username": username, "action": "disable", "reason": self.resolve_variables(config.get("reason", "Disabled by SOAR workflow"), context)},
                timeout=int(config.get("timeout", 15)),
            )
            logger.warning("[CONTAINMENT] Disable user %s via %s -> %s", username, url, response.status_code)
            return ActionResult(response.ok, {"username": username, "disabled": response.ok, "status_code": response.status_code, "response_body": response.text[:2000]}, logs=f"Disable user {username}: HTTP {response.status_code}")
        except Exception as exc:
            return ActionResult(False, error=str(exc), logs=f"Disable user failed for {username}: {exc}")

