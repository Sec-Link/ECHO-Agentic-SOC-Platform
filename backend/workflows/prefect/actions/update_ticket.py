from __future__ import annotations

from typing import Any, Dict, Iterable

from ..client import get_tickets, list_tickets, update_ticket
from .base import ActionResult, BaseAction


def _ticket_numbers(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, dict):
        return _ticket_numbers(value.get("ticket_number") or value.get("ticket_numbers"))
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(_ticket_numbers(item))
        return result
    return []


def _upstream_scope(context: Dict[str, Any]) -> list[str]:
    values: list[str] = []
    for source in (context.get("trigger_data"), context.get("variables")):
        if isinstance(source, dict):
            for key in ("target_ticket_numbers", "ticket_numbers", "tickets"):
                values.extend(_ticket_numbers(source.get(key)))
    for result in (context.get("step_results") or {}).values():
        if isinstance(result, dict):
            for key in ("target_ticket_numbers", "ticket_numbers", "tickets"):
                values.extend(_ticket_numbers(result.get(key)))
    return list(dict.fromkeys(values))


class UpdateTicketAction(BaseAction):
    name = "Update Ticket"
    description = "Update existing tickets selected by number, title, upstream scope, or filters"
    category = "integration"
    config_schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "ticket_number": {"type": "string"},
            "filters": {"type": "object"},
            "match_status": {"type": "string"},
            "match_priority": {"type": "string"},
            "match_assign_group": {"type": "string"},
            "match_assign_owner": {"type": "string"},
            "status": {"type": "string"},
            "priority": {"type": "string"},
            "assign_group": {"type": "string"},
            "assign_owner": {"type": "string"},
            "current_assign_group": {"type": "string"},
            "current_assign_owner": {"type": "string"},
            "event_result": {"type": "string"},
            "event_category": {"type": "string"},
            "ticket_records": {"type": "string"},
            "add_comment": {"type": "string"},
        },
    }

    def execute(self, config: Dict[str, Any], context: Dict[str, Any]) -> ActionResult:
        trigger = context.get("trigger_data") if isinstance(context.get("trigger_data"), dict) else {}
        number = self.resolve_variables(config.get("ticket_number", ""), context) or trigger.get("ticket_number", "")
        title = self.resolve_variables(config.get("title", ""), context) or trigger.get("title", "")
        filters = config.get("filters") if isinstance(config.get("filters"), dict) else {}
        try:
            if number:
                tickets = get_tickets([str(number)])
            elif title:
                tickets = list_tickets({"title": title})
            elif scope := _upstream_scope(context):
                tickets = get_tickets(scope)
            else:
                query = {
                    "priority": self.resolve_variables(config.get("match_priority", filters.get("priority", "")), context),
                    "status": self.resolve_variables(config.get("match_status", filters.get("status", "")), context),
                    "current_assign_group": self.resolve_variables(config.get("match_assign_group", filters.get("assign_group", "")), context),
                    "current_assign_owner": self.resolve_variables(config.get("match_assign_owner", filters.get("assign_owner", "")), context),
                    "created_from": filters.get("created_time_from"),
                    "created_to": filters.get("created_time_to"),
                    "updated_from": filters.get("updated_time_from"),
                    "updated_to": filters.get("updated_time_to"),
                }
                if not any(query.values()):
                    return ActionResult(False, error="No title/ticket_number/upstream ticket scope/filters provided", logs="Update ticket aborted: missing selector")
                tickets = list_tickets(query)
            if not tickets:
                return ActionResult(False, error="No matching tickets found", logs="No tickets matched update criteria")

            fields = {
                "status": "status",
                "priority": "priority",
                "current_assign_group": "current_assign_group" if "current_assign_group" in config else "assign_group",
                "current_assign_owner": "current_assign_owner" if "current_assign_owner" in config else "assign_owner",
                "event_result": "event_result",
                "event_category": "event_category",
                "ticket_records": "ticket_records" if "ticket_records" in config else "add_comment",
            }
            updates = {
                target: self.resolve_variables(config[source], context)
                for target, source in fields.items()
                if source in config
            }
            if not updates:
                return ActionResult(False, error="No update fields provided", logs="Update ticket aborted: no update fields")
            for ticket in tickets:
                update_ticket(str(ticket["ticket_number"]), updates)
            rendered = [f"{key}={value}" for key, value in updates.items()]
            return ActionResult(True, {"updated_count": len(tickets), "updates": rendered}, logs=f"Updated {len(tickets)} ticket(s): {', '.join(rendered)}")
        except Exception as exc:
            return ActionResult(False, error=str(exc), logs=f"Failed to update ticket: {exc}")

