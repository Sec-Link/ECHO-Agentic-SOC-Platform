from __future__ import annotations

from typing import Dict

from .base import BaseAction
from .api_call import ApiCallAction
from .block_ip import BlockIPAction
from .create_ticket import CreateTicketAction
from .delay import DelayAction
from .disable_user import DisableUserAction
from .enable_user import EnableUserAction
from .hash_lookup import HashLookupAction
from .ip_lookup import IPLookupAction
from .log import LogAction
from .release_ip import ReleaseIPAction
from .send_email import SendEmailAction
from .update_ticket import UpdateTicketAction


class ActionRegistry:
    _actions: Dict[str, type[BaseAction]] = {
        "log": LogAction,
        "delay": DelayAction,
        "send_email": SendEmailAction,
        "api_call": ApiCallAction,
        "create_ticket": CreateTicketAction,
        "update_ticket": UpdateTicketAction,
        "ip_lookup": IPLookupAction,
        "hash_lookup": HashLookupAction,
        "block_ip": BlockIPAction,
        "disable_user": DisableUserAction,
        "release_ip": ReleaseIPAction,
        "enable_user": EnableUserAction,
    }

    @classmethod
    def get_action(cls, action_type: str) -> BaseAction:
        try:
            return cls._actions[action_type]()
        except KeyError as exc:
            raise ValueError(f"Unknown action type: {action_type}") from exc

    @classmethod
    def get_all_actions(cls) -> Dict[str, type[BaseAction]]:
        return cls._actions.copy()

    @classmethod
    def get_action_info(cls) -> list[dict]:
        return [
            {
                "action_type": action_type,
                "name": action.name,
                "description": action.description,
                "category": action.category,
                "config_schema": action.config_schema,
            }
            for action_type, action in cls._actions.items()
        ]
