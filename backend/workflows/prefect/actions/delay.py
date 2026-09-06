from __future__ import annotations

import time
from typing import Any, Dict

from .base import ActionResult, BaseAction


class DelayAction(BaseAction):
    name = "Delay"
    description = "Wait for a specified number of seconds"
    category = "utility"
    config_schema = {
        "type": "object",
        "properties": {"seconds": {"type": "integer", "minimum": 1, "maximum": 3600, "default": 5}},
        "required": ["seconds"],
    }

    def execute(self, config: Dict[str, Any], context: Dict[str, Any]) -> ActionResult:
        seconds = min(max(int(config.get("seconds", 5)), 1), 3600)
        time.sleep(seconds)
        return ActionResult(True, {"delayed_seconds": seconds}, logs=f"Waited for {seconds} seconds")

