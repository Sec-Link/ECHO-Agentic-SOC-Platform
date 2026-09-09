from __future__ import annotations

from typing import Any, Dict

from .actions import ActionRegistry
from .secrets import decrypt_config_for_execution, redact_values, secret_values


def execute_action(action_type: str, action_config: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    execution_config = decrypt_config_for_execution(action_type, action_config)
    schema = ActionRegistry.get_all_actions()[action_type].config_schema
    secrets = secret_values(action_type, execution_config, schema)
    try:
        result = ActionRegistry.get_action(action_type).execute(execution_config, context)
    except Exception as exc:
        raise RuntimeError(redact_values(str(exc), secrets)) from None
    return redact_values(result.to_dict(), secrets)
