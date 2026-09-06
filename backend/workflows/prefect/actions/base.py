from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class ActionResult:
    success: bool
    data: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    logs: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "logs": self.logs,
        }


class BaseAction(ABC):
    name = "Base Action"
    description = ""
    category = "utility"
    config_schema: Dict[str, Any] = {}

    @abstractmethod
    def execute(self, config: Dict[str, Any], context: Dict[str, Any]) -> ActionResult:
        raise NotImplementedError

    def resolve_variables(self, value: Any, context: Dict[str, Any]) -> Any:
        if isinstance(value, str):
            def replace(match: re.Match[str]) -> str:
                result: Any = context
                for key in match.group(1).split("."):
                    result = result.get(key, "") if isinstance(result, dict) else getattr(result, key, "")
                return str(result) if result not in (None, "") else ""

            return re.sub(r"\{\{(\w+(?:\.\w+)*)\}\}", replace, value)
        if isinstance(value, dict):
            return {key: self.resolve_variables(item, context) for key, item in value.items()}
        if isinstance(value, list):
            return [self.resolve_variables(item, context) for item in value]
        return value

