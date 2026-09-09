from __future__ import annotations

import logging
from typing import Any, Dict

from .base import ActionResult, BaseAction

logger = logging.getLogger(__name__)


class LogAction(BaseAction):
    name = "Log Message"
    description = "Log a message for debugging purposes"
    category = "utility"
    config_schema = {
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "Message to log"},
            "level": {"type": "string", "enum": ["info", "warning", "error"], "default": "info"},
        },
        "required": ["message"],
    }

    def execute(self, config: Dict[str, Any], context: Dict[str, Any]) -> ActionResult:
        message = self.resolve_variables(config.get("message", ""), context)
        level = str(config.get("level", "info"))
        getattr(logger, level, logger.info)("[Workflow] %s", message)
        return ActionResult(True, {"message": message, "level": level}, logs=f"[{level.upper()}] {message}")

