from __future__ import annotations

from typing import Any, Dict

from ..client import create_ticket
from .base import ActionResult, BaseAction


class CreateTicketAction(BaseAction):
    name = "Create Ticket"
    description = "Create a new incident ticket"
    category = "integration"
    config_schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Ticket title"},
            "description": {"type": "string", "description": "Ticket description"},
            "priority": {"type": "string", "enum": ["critical", "high", "medium", "low"], "default": "medium"},
            "status": {"type": "string", "default": "new"},
            "event_category": {"type": "string"},
            "current_assign_group": {"type": "string"},
            "current_assign_owner": {"type": "string"},
            "assign_group": {"type": "string"},
            "assign_owner": {"type": "string"},
            "alert_message": {"type": "string"},
            "create_uid": {"type": "string"},
        },
        "required": ["title"],
    }

    def execute(self, config: Dict[str, Any], context: Dict[str, Any]) -> ActionResult:
        payload = {
            "title": self.resolve_variables(config.get("title", ""), context),
            "description": self.resolve_variables(config.get("description", ""), context),
            "priority": self.resolve_variables(config.get("priority", "medium"), context),
            "status": self.resolve_variables(config.get("status", "new"), context),
            "event_category": self.resolve_variables(config.get("event_category", ""), context) or None,
            "current_assign_group": self.resolve_variables(config.get("current_assign_group", config.get("assign_group", "")), context) or None,
            "current_assign_owner": self.resolve_variables(config.get("current_assign_owner", config.get("assign_owner", "")), context) or None,
            "alert_message": self.resolve_variables(config.get("alert_message", ""), context) or None,
            "create_uid": self.resolve_variables(config.get("create_uid", ""), context) or f"workflow:{context.get('execution_id', 'worker')}",
        }
        try:
            ticket = create_ticket(payload)
            number = str(ticket.get("ticket_number") or "")
            return ActionResult(True, {"ticket_number": number, "title": payload["title"]}, logs=f"Created ticket: {number}")
        except Exception as exc:
            return ActionResult(False, error=str(exc), logs=f"Failed to create ticket: {exc}")

