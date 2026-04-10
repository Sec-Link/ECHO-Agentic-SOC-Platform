"""
Workflow Actions

Defines the available actions that can be used in workflow steps.
Each action is a class that implements the execute() method.

Action categories:
  - control_flow : Start / End / Condition  (handled as node types, not registered here)
  - enrichment   : IP Lookup, Hash Lookup   (call external threat-intel platforms)
  - containment  : Block IP, Disable User
  - release      : Release IP, Enable User  (reverse of containment)
  - notification : Send Email, Send Webhook
  - integration  : Update Ticket (ticket number can be dynamic)
  - utility      : Log, Delay
"""
import json
import logging
import re
import requests
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

logger = logging.getLogger(__name__)


class ActionResult:
    """Result of an action execution."""

    def __init__(
        self,
        success: bool,
        data: Optional[Dict] = None,
        error: Optional[str] = None,
        logs: Optional[str] = None
    ):
        self.success = success
        self.data = data or {}
        self.error = error or ''
        self.logs = logs or ''

    def to_dict(self) -> Dict:
        return {
            'success': self.success,
            'data': self.data,
            'error': self.error,
            'logs': self.logs,
        }


class BaseAction(ABC):
    """Base class for all workflow actions."""

    # Action metadata
    name: str = "Base Action"
    description: str = ""
    category: str = "utility"
    config_schema: Dict = {}

    @abstractmethod
    def execute(self, config: Dict, context: Dict) -> ActionResult:
        """
        Execute the action.

        Args:
            config: Action configuration from the workflow step
            context: Execution context (trigger data, previous step outputs, etc.)

        Returns:
            ActionResult with success status and output data
        """
        pass

    def resolve_variables(self, value: Any, context: Dict) -> Any:
        """
        Resolve {{variable}} placeholders in config values.

        Supports nested paths like {{trigger_data.ticket_number}}
        """
        if isinstance(value, str):
            def replace_var(match):
                var_path = match.group(1)
                result = context
                for key in var_path.split('.'):
                    if isinstance(result, dict):
                        result = result.get(key, '')
                    else:
                        result = getattr(result, key, '')
                return str(result) if result else ''

            return re.sub(r'\{\{(\w+(?:\.\w+)*)\}\}', replace_var, value)
        elif isinstance(value, dict):
            return {k: self.resolve_variables(v, context) for k, v in value.items()}
        elif isinstance(value, list):
            return [self.resolve_variables(item, context) for item in value]
        return value


# ============ Utility Actions ============

class LogAction(BaseAction):
    """Log a message (for debugging)."""

    name = "Log Message"
    description = "Log a message for debugging purposes"
    category = "utility"
    config_schema = {
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "Message to log"},
            "level": {"type": "string", "enum": ["info", "warning", "error"], "default": "info"}
        },
        "required": ["message"]
    }

    def execute(self, config: Dict, context: Dict) -> ActionResult:
        message = self.resolve_variables(config.get('message', ''), context)
        level = config.get('level', 'info')

        log_func = getattr(logger, level, logger.info)
        log_func(f"[Workflow] {message}")

        return ActionResult(
            success=True,
            data={'message': message, 'level': level},
            logs=f"[{level.upper()}] {message}"
        )


class DelayAction(BaseAction):
    """Wait for a specified duration."""

    name = "Delay"
    description = "Wait for a specified number of seconds"
    category = "utility"
    config_schema = {
        "type": "object",
        "properties": {
            "seconds": {"type": "integer", "minimum": 1, "maximum": 3600, "default": 5}
        },
        "required": ["seconds"]
    }

    def execute(self, config: Dict, context: Dict) -> ActionResult:
        seconds = int(config.get('seconds', 5))
        seconds = min(max(seconds, 1), 3600)  # Clamp to 1-3600

        time.sleep(seconds)

        return ActionResult(
            success=True,
            data={'delayed_seconds': seconds},
            logs=f"Waited for {seconds} seconds"
        )


class SetVariableAction(BaseAction):
    """Set a context variable for use in later steps."""

    name = "Set Variable"
    description = "Set a variable in the execution context"
    category = "utility"
    config_schema = {
        "type": "object",
        "properties": {
            "variable_name": {"type": "string", "description": "Name of the variable"},
            "value": {"description": "Value to set (can be any type)"}
        },
        "required": ["variable_name", "value"]
    }

    def execute(self, config: Dict, context: Dict) -> ActionResult:
        var_name = config.get('variable_name', '')
        value = self.resolve_variables(config.get('value'), context)

        return ActionResult(
            success=True,
            data={var_name: value},
            logs=f"Set variable '{var_name}' = {value}"
        )


class ConditionAction(BaseAction):
    """Evaluate a condition and return result."""

    name = "Condition Check"
    description = "Evaluate a condition expression"
    category = "utility"
    config_schema = {
        "type": "object",
        "properties": {
            "left": {"description": "Left operand"},
            "operator": {"type": "string", "enum": ["==", "!=", ">", "<", ">=", "<=", "contains", "not_contains"]},
            "right": {"description": "Right operand"}
        },
        "required": ["left", "operator", "right"]
    }

    def execute(self, config: Dict, context: Dict) -> ActionResult:
        left = self.resolve_variables(config.get('left'), context)
        right = self.resolve_variables(config.get('right'), context)
        operator = config.get('operator', '==')

        # Evaluate condition
        result = False
        if operator == '==':
            result = left == right
        elif operator == '!=':
            result = left != right
        elif operator == '>':
            result = float(left) > float(right)
        elif operator == '<':
            result = float(left) < float(right)
        elif operator == '>=':
            result = float(left) >= float(right)
        elif operator == '<=':
            result = float(left) <= float(right)
        elif operator == 'contains':
            result = str(right) in str(left)
        elif operator == 'not_contains':
            result = str(right) not in str(left)

        return ActionResult(
            success=True,
            data={'condition_result': result, 'left': left, 'operator': operator, 'right': right},
            logs=f"Condition: {left} {operator} {right} = {result}"
        )


# ============ Notification Actions ============

class SendEmailAction(BaseAction):
    """Send an email notification."""

    name = "Send Email"
    description = "Send an email notification"
    category = "notification"
    config_schema = {
        "type": "object",
        "properties": {
            "to": {"type": "array", "items": {"type": "string"}, "description": "List of recipient emails"},
            "subject": {"type": "string", "description": "Email subject"},
            "body": {"type": "string", "description": "Email body (plain text or HTML)"},
            "is_html": {"type": "boolean", "default": False}
        },
        "required": ["to", "subject", "body"]
    }

    def execute(self, config: Dict, context: Dict) -> ActionResult:
        to_list = config.get('to', [])
        if isinstance(to_list, str):
            to_list = [to_list]

        subject = self.resolve_variables(config.get('subject', ''), context)
        body = self.resolve_variables(config.get('body', ''), context)
        is_html = config.get('is_html', False)

        try:
            send_mail(
                subject=subject,
                message=body if not is_html else '',
                from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@siem.local'),
                recipient_list=to_list,
                html_message=body if is_html else None,
                fail_silently=False,
            )

            return ActionResult(
                success=True,
                data={'sent_to': to_list, 'subject': subject},
                logs=f"Email sent to {', '.join(to_list)}"
            )
        except Exception as e:
            return ActionResult(
                success=False,
                error=str(e),
                logs=f"Failed to send email: {e}"
            )


class SendWebhookAction(BaseAction):
    """Send a webhook/HTTP request.

    The ``body_template`` field accepts a JSON string.  Variable placeholders
    (``{{variable.path}}``) are resolved before the request is sent.  If the
    resulting string is not valid JSON, the step fails early with a descriptive
    error so the user knows exactly what went wrong.
    """

    name = "Send Webhook"
    description = "Send an HTTP request to a webhook URL with a configurable JSON body"
    category = "notification"
    config_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Webhook URL"},
            "method": {
                "type": "string",
                "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"],
                "default": "POST",
            },
            "headers": {
                "type": "object",
                "description": "Request headers (key-value pairs)",
            },
            "body_template": {
                "type": "string",
                "description": (
                    "JSON request body as a string.  "
                    "Use {{variable.path}} placeholders for dynamic values."
                ),
            },
            "timeout": {"type": "integer", "default": 30},
        },
        "required": ["url"],
    }

    def execute(self, config: Dict, context: Dict) -> ActionResult:
        url = self.resolve_variables(config.get("url", ""), context)
        method = config.get("method", "POST").upper()
        headers = self.resolve_variables(config.get("headers", {}), context)
        timeout = config.get("timeout", 30)

        # ---------- body resolution & validation ----------
        body_template = config.get("body_template", "")
        body: Any = {}

        if body_template:
            resolved_body_str = self.resolve_variables(str(body_template), context)
            try:
                body = json.loads(resolved_body_str)
            except json.JSONDecodeError as exc:
                return ActionResult(
                    success=False,
                    error=f"Request body is not valid JSON after variable substitution: {exc}",
                    logs=(
                        f"Webhook body validation failed.\n"
                        f"Resolved body string:\n{resolved_body_str}"
                    ),
                )

        try:
            if method == "GET":
                response = requests.get(
                    url, headers=headers, params=body, timeout=timeout
                )
            else:
                response = requests.request(
                    method, url, headers=headers, json=body, timeout=timeout
                )

            return ActionResult(
                success=response.ok,
                data={
                    "status_code": response.status_code,
                    "response_body": response.text[:2000],
                },
                logs=f"HTTP {method} {url} -> {response.status_code}",
            )
        except Exception as e:
            return ActionResult(
                success=False,
                error=str(e),
                logs=f"Webhook failed: {e}",
            )


# ============ Ticket Actions ============

class CreateTicketAction(BaseAction):
    """Create a new ticket."""

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
            "assign_group": {"type": "string"},
            "assign_owner": {"type": "string"},
            "create_uid": {"type": "string", "description": "Creator user ID"}
        },
        "required": ["title"]
    }

    def execute(self, config: Dict, context: Dict) -> ActionResult:
        from tickets.models import EventTicket

        title = self.resolve_variables(config.get('title', ''), context)
        description = self.resolve_variables(config.get('description', ''), context)
        priority = config.get('priority', 'medium')
        status = config.get('status', 'new')
        create_uid = self.resolve_variables(config.get('create_uid', ''), context)

        try:
            ticket = EventTicket.objects.create(
                title=title,
                description=description,
                priority=priority,
                status=status,
                current_assign_group=config.get('assign_group', ''),
                current_assign_owner=config.get('assign_owner', ''),
                create_uid=create_uid or None,
            )

            return ActionResult(
                success=True,
                data={'ticket_number': ticket.ticket_number, 'title': title},
                logs=f"Created ticket: {ticket.ticket_number}"
            )
        except Exception as e:
            return ActionResult(
                success=False,
                error=str(e),
                logs=f"Failed to create ticket: {e}"
            )


class UpdateTicketAction(BaseAction):
    """Update an existing ticket.

    ``title`` supports ``{{variable.path}}`` placeholders so the value
    can be pulled dynamically from the triggering case or alert.
    """

    name = "Update Ticket"
    description = "Update an existing ticket's status or fields (ticket title can be dynamic)"
    category = "integration"
    config_schema = {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": (
                    "Ticket title to update.  "
                    "Supports dynamic values, e.g. {{trigger_data.title}}"
                ),
            },
            "ticket_number": {
                "type": "string",
                "description": (
                    "Optional ticket number selector for backward compatibility.  "
                    "Supports dynamic values, e.g. {{trigger_data.ticket_number}}"
                ),
            },
            "filters": {
                "type": "object",
                "description": "Filter conditions when title is not provided",
                "properties": {
                    "priority": {"type": "string"},
                    "status": {"type": "string"},
                    "assign_group": {"type": "string"},
                    "assign_owner": {"type": "string"},
                    "created_time_from": {"type": "string", "description": "ISO datetime"},
                    "created_time_to": {"type": "string", "description": "ISO datetime"},
                    "updated_time_from": {"type": "string", "description": "ISO datetime"},
                    "updated_time_to": {"type": "string", "description": "ISO datetime"}
                }
            },
            "match_status": {"type": "string", "description": "Target tickets with this current status"},
            "match_priority": {"type": "string", "description": "Target tickets with this current priority"},
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
            "add_comment": {"type": "string"}
        },
        "required": []
    }

    def execute(self, config: Dict, context: Dict) -> ActionResult:
        from tickets.models import EventTicket
        from django.utils import timezone
        from django.utils.dateparse import parse_datetime

        def _as_ticket_number_list(value):
            if value is None:
                return []
            if isinstance(value, str):
                item = value.strip()
                return [item] if item else []
            if isinstance(value, dict):
                nested = value.get('ticket_number') or value.get('ticket_numbers')
                if nested is not None:
                    return _as_ticket_number_list(nested)
                return []
            if isinstance(value, list):
                collected = []
                for item in value:
                    if isinstance(item, dict):
                        ticket_no = str(item.get('ticket_number') or '').strip()
                        if ticket_no:
                            collected.append(ticket_no)
                    else:
                        ticket_no = str(item or '').strip()
                        if ticket_no:
                            collected.append(ticket_no)
                return collected
            return []

        def _extract_upstream_ticket_scope(ctx):
            if not isinstance(ctx, dict):
                return []

            candidates = []
            trigger_data = ctx.get('trigger_data')
            variables = ctx.get('variables')
            step_results = ctx.get('step_results')

            # Prefer explicit scope passed by trigger or previous steps.
            if isinstance(trigger_data, dict):
                candidates.extend(
                    _as_ticket_number_list(trigger_data.get('target_ticket_numbers'))
                    + _as_ticket_number_list(trigger_data.get('ticket_numbers'))
                    + _as_ticket_number_list(trigger_data.get('tickets'))
                )

            if isinstance(variables, dict):
                candidates.extend(
                    _as_ticket_number_list(variables.get('target_ticket_numbers'))
                    + _as_ticket_number_list(variables.get('ticket_numbers'))
                    + _as_ticket_number_list(variables.get('tickets'))
                )

            if isinstance(step_results, dict):
                for result in step_results.values():
                    if isinstance(result, dict):
                        candidates.extend(
                            _as_ticket_number_list(result.get('target_ticket_numbers'))
                            + _as_ticket_number_list(result.get('ticket_numbers'))
                            + _as_ticket_number_list(result.get('tickets'))
                        )

            # Preserve order while removing duplicates.
            seen = set()
            deduped = []
            for item in candidates:
                if item not in seen:
                    seen.add(item)
                    deduped.append(item)
            return deduped

        trigger_data = context.get('trigger_data', {}) if isinstance(context, dict) else {}

        title = self.resolve_variables(config.get('title', ''), context)
        ticket_number = self.resolve_variables(config.get('ticket_number', ''), context)
        if not ticket_number and isinstance(trigger_data, dict):
            ticket_number = str(trigger_data.get('ticket_number') or '').strip()

        if not title and isinstance(trigger_data, dict):
            title = str(trigger_data.get('title') or '').strip()

        filters = config.get('filters') or {}
        if not isinstance(filters, dict):
            filters = {}

        # Flat match_* keys are easier for visual-form editing; merge into filters.
        merged_filters = {
            'priority': config.get('match_priority', filters.get('priority', '')),
            'status': config.get('match_status', filters.get('status', '')),
            'assign_group': config.get('match_assign_group', filters.get('assign_group', '')),
            'assign_owner': config.get('match_assign_owner', filters.get('assign_owner', '')),
            'created_time_from': filters.get('created_time_from'),
            'created_time_to': filters.get('created_time_to'),
            'updated_time_from': filters.get('updated_time_from'),
            'updated_time_to': filters.get('updated_time_to'),
        }

        try:
            if ticket_number:
                tickets = EventTicket.objects.filter(ticket_number=ticket_number)
            elif title:
                tickets = EventTicket.objects.filter(title=title)
            else:
                upstream_ticket_numbers = _extract_upstream_ticket_scope(context)
                if upstream_ticket_numbers:
                    tickets = EventTicket.objects.filter(ticket_number__in=upstream_ticket_numbers)
                else:
                    query = EventTicket.objects.all()
                    priority = self.resolve_variables(merged_filters.get('priority', ''), context)
                    status = self.resolve_variables(merged_filters.get('status', ''), context)
                    assign_group = self.resolve_variables(merged_filters.get('assign_group', ''), context)
                    assign_owner = self.resolve_variables(merged_filters.get('assign_owner', ''), context)
                    created_time_from = merged_filters.get('created_time_from')
                    created_time_to = merged_filters.get('created_time_to')
                    updated_time_from = merged_filters.get('updated_time_from')
                    updated_time_to = merged_filters.get('updated_time_to')

                    if priority:
                        query = query.filter(priority__iexact=priority)
                    if status:
                        query = query.filter(status__iexact=status)
                    if assign_group:
                        query = query.filter(current_assign_group__iexact=assign_group)
                    if assign_owner:
                        query = query.filter(current_assign_owner__iexact=assign_owner)

                    def _parse_dt(value: Optional[str]):
                        if not value:
                            return None
                        parsed = parse_datetime(value)
                        if parsed and timezone.is_naive(parsed):
                            return timezone.make_aware(parsed)
                        return parsed

                    dt_from = _parse_dt(created_time_from)
                    dt_to = _parse_dt(created_time_to)
                    if dt_from:
                        query = query.filter(created_time__gte=dt_from)
                    if dt_to:
                        query = query.filter(created_time__lte=dt_to)

                    dt_from = _parse_dt(updated_time_from)
                    dt_to = _parse_dt(updated_time_to)
                    if dt_from:
                        query = query.filter(updated_time__gte=dt_from)
                    if dt_to:
                        query = query.filter(updated_time__lte=dt_to)

                    if not (priority or status or assign_group or assign_owner or dt_from or dt_to or created_time_from or created_time_to or updated_time_from or updated_time_to):
                        return ActionResult(
                            success=False,
                            error="No title/ticket_number/upstream ticket scope/filters provided",
                            logs="Update ticket aborted: missing selector and upstream scope"
                        )

                    tickets = query

            if not tickets.exists():
                return ActionResult(
                    success=False,
                    error="No matching tickets found",
                    logs="No tickets matched update criteria"
                )

            status_value = self.resolve_variables(config.get('status'), context) if 'status' in config else None
            priority_value = self.resolve_variables(config.get('priority'), context) if 'priority' in config else None
            assign_group_value = self.resolve_variables(
                config.get('current_assign_group', config.get('assign_group')), context
            ) if ('assign_group' in config or 'current_assign_group' in config) else None
            assign_owner_value = self.resolve_variables(
                config.get('current_assign_owner', config.get('assign_owner')), context
            ) if ('assign_owner' in config or 'current_assign_owner' in config) else None
            event_result_value = self.resolve_variables(config.get('event_result'), context) if 'event_result' in config else None
            event_category_value = self.resolve_variables(config.get('event_category'), context) if 'event_category' in config else None
            ticket_records_value = self.resolve_variables(
                config.get('ticket_records', config.get('add_comment')), context
            ) if ('ticket_records' in config or 'add_comment' in config) else None

            updates = []
            if 'status' in config:
                updates.append(f"status={status_value}")
            if 'priority' in config:
                updates.append(f"priority={priority_value}")
            if 'assign_group' in config or 'current_assign_group' in config:
                updates.append(f"current_assign_group={assign_group_value}")
            if 'assign_owner' in config or 'current_assign_owner' in config:
                updates.append(f"current_assign_owner={assign_owner_value}")
            if 'event_result' in config:
                updates.append(f"event_result={event_result_value}")
            if 'event_category' in config:
                updates.append(f"event_category={event_category_value}")
            if 'ticket_records' in config or 'add_comment' in config:
                updates.append("ticket_records=<updated>")

            if not updates:
                return ActionResult(
                    success=False,
                    error="No update fields provided",
                    logs="Update ticket aborted: no update fields in config"
                )

            updated = 0
            for ticket in tickets:
                if 'status' in config:
                    ticket.status = status_value
                if 'priority' in config:
                    ticket.priority = priority_value
                if 'assign_group' in config or 'current_assign_group' in config:
                    ticket.current_assign_group = assign_group_value
                if 'assign_owner' in config or 'current_assign_owner' in config:
                    ticket.current_assign_owner = assign_owner_value
                if 'event_result' in config:
                    ticket.event_result = event_result_value
                if 'event_category' in config:
                    ticket.event_category = event_category_value
                if 'ticket_records' in config or 'add_comment' in config:
                    ticket.ticket_records = ticket_records_value
                ticket.save()
                updated += 1

            return ActionResult(
                success=True,
                data={'updated_count': updated, 'updates': updates},
                logs=f"Updated {updated} ticket(s): {', '.join(updates)}"
            )
        except Exception as e:
            return ActionResult(
                success=False,
                error=str(e),
                logs=f"Failed to update ticket: {e}"
            )


# ============ Enrichment Actions ============

class IPLookupAction(BaseAction):
    """Query a threat-intelligence platform for an IP address.

    The ``ip_address`` field supports ``{{variable.path}}`` placeholders so the
    value can be pulled dynamically from the triggering case or alert.

    Return value (stored in ``data``):
        {
          "ip": "<queried IP>",
          "is_malicious": <bool>,
          "risk_score": <0-100>,
          "country": "<ISO code>",
          "asn": "<AS number / org>",
          "summary": "<short description from the platform>",
          "raw_response": { ... }   // full JSON returned by the API
        }
    """

    name = "IP Lookup"
    description = "Check IP reputation via a configurable threat-intel platform (e.g. AbuseIPDB, VirusTotal)"
    category = "enrichment"
    config_schema = {
        "type": "object",
        "properties": {
            "ip_address": {
                "type": "string",
                "description": (
                    "IP address to look up.  "
                    "Supports dynamic values, e.g. {{trigger_data.source_ip}}"
                ),
            },
            "api_url": {
                "type": "string",
                "description": (
                    "Full threat-intelligence API endpoint, "
                    "e.g. https://api.abuseipdb.com/api/v2/check"
                ),
            },
            "api_key": {
                "type": "string",
                "description": "API key for the threat-intelligence platform",
            },
            "timeout": {"type": "integer", "default": 15},
        },
        "required": ["ip_address", "api_url", "api_key"],
    }

    def execute(self, config: Dict, context: Dict) -> ActionResult:
        ip = self.resolve_variables(config.get("ip_address", ""), context)
        api_url = self.resolve_variables(config.get("api_url", ""), context)
        api_key = self.resolve_variables(config.get("api_key", ""), context)
        timeout = int(config.get("timeout", 15))

        if not ip:
            return ActionResult(
                success=False,
                error="ip_address is empty after variable resolution",
                logs="IP Lookup aborted: no IP address provided",
            )

        try:
            response = requests.get(
                api_url,
                headers={"Key": api_key, "Accept": "application/json"},
                params={"ipAddress": ip, "maxAgeInDays": "90"},
                timeout=timeout,
            )
            raw = response.json() if response.content else {}

            # Normalise common response shapes (AbuseIPDB / VirusTotal / generic)
            is_malicious = False
            risk_score = 0
            country = raw.get("data", {}).get("countryCode") or raw.get("country", "Unknown")
            asn = raw.get("data", {}).get("isp") or raw.get("asn", "Unknown")
            summary = ""

            data_block = raw.get("data", {})
            if isinstance(data_block, dict) and "attributes" in data_block:
                # VirusTotal-style response
                attrs = data_block["attributes"]
                malicious_count = attrs.get("last_analysis_stats", {}).get("malicious", 0)
                risk_score = min(malicious_count * 5, 100)
                is_malicious = malicious_count > 0
                summary = f"Malicious engines: {malicious_count}"
            elif "data" in raw and isinstance(data_block, dict):
                # AbuseIPDB-style response
                risk_score = data_block.get("abuseConfidenceScore", 0)
                is_malicious = risk_score >= 25
                summary = f"Total reports: {data_block.get('totalReports', 0)}"

            result = {
                "ip": ip,
                "is_malicious": is_malicious,
                "risk_score": risk_score,
                "country": country,
                "asn": asn,
                "summary": summary,
                "raw_response": raw,
            }

            return ActionResult(
                success=True,
                data=result,
                logs=f"IP lookup for {ip}: risk_score={risk_score}, is_malicious={is_malicious}",
            )
        except Exception as exc:
            return ActionResult(
                success=False,
                error=str(exc),
                logs=f"IP lookup failed for {ip}: {exc}",
            )


class HashLookupAction(BaseAction):
    """Query a threat-intelligence platform for a file hash.

    The ``hash_value`` field supports ``{{variable.path}}`` placeholders so the
    value can be pulled dynamically from the triggering case or alert.

    Return value (stored in ``data``):
        {
          "hash": "<queried hash>",
          "hash_type": "md5|sha1|sha256",
          "is_malicious": <bool>,
          "detections": <int>,
          "total_engines": <int>,
          "file_name": "<if available>",
          "file_type": "<if available>",
          "summary": "<short description>",
          "raw_response": { ... }
        }
    """

    name = "Hash Lookup"
    description = "Check file-hash reputation via a configurable threat-intel platform (e.g. VirusTotal)"
    category = "enrichment"
    config_schema = {
        "type": "object",
        "properties": {
            "hash_value": {
                "type": "string",
                "description": (
                    "File hash (MD5 / SHA-1 / SHA-256).  "
                    "Supports dynamic values, e.g. {{trigger_data.file_hash}}"
                ),
            },
            "hash_type": {
                "type": "string",
                "enum": ["md5", "sha1", "sha256"],
                "default": "sha256",
            },
            "api_url": {
                "type": "string",
                "description": (
                    "Full threat-intelligence API endpoint, "
                    "e.g. https://www.virustotal.com/api/v3/files/{hash}"
                ),
            },
            "api_key": {
                "type": "string",
                "description": "API key for the threat-intelligence platform",
            },
            "timeout": {"type": "integer", "default": 15},
        },
        "required": ["hash_value", "api_url", "api_key"],
    }

    def execute(self, config: Dict, context: Dict) -> ActionResult:
        hash_value = self.resolve_variables(config.get("hash_value", ""), context)
        hash_type = config.get("hash_type", "sha256")
        # Allow the URL template to contain the hash placeholder
        api_url_template = config.get("api_url", "")
        api_url = self.resolve_variables(
            api_url_template.replace("{hash}", hash_value), context
        )
        api_key = self.resolve_variables(config.get("api_key", ""), context)
        timeout = int(config.get("timeout", 15))

        if not hash_value:
            return ActionResult(
                success=False,
                error="hash_value is empty after variable resolution",
                logs="Hash Lookup aborted: no hash provided",
            )

        try:
            response = requests.get(
                api_url,
                headers={"x-apikey": api_key, "Accept": "application/json"},
                timeout=timeout,
            )
            raw = response.json() if response.content else {}

            # Normalise VirusTotal-style response
            detections = 0
            total_engines = 0
            file_name = ""
            file_type = ""
            is_malicious = False
            summary = ""

            if "data" in raw and "attributes" in raw.get("data", {}):
                attrs = raw["data"]["attributes"]
                stats = attrs.get("last_analysis_stats", {})
                detections = stats.get("malicious", 0)
                total_engines = sum(stats.values()) if stats else 0
                is_malicious = detections > 0
                file_name = attrs.get("meaningful_name", "")
                file_type = attrs.get("type_description", "")
                summary = f"{detections}/{total_engines} engines flagged as malicious"

            result = {
                "hash": hash_value,
                "hash_type": hash_type,
                "is_malicious": is_malicious,
                "detections": detections,
                "total_engines": total_engines,
                "file_name": file_name,
                "file_type": file_type,
                "summary": summary,
                "raw_response": raw,
            }

            return ActionResult(
                success=True,
                data=result,
                logs=f"Hash lookup for {hash_value}: {detections}/{total_engines} detections",
            )
        except Exception as exc:
            return ActionResult(
                success=False,
                error=str(exc),
                logs=f"Hash lookup failed for {hash_value}: {exc}",
            )


# ============ Containment Actions ============

class BlockIPAction(BaseAction):
    """Block an IP address via an external security-device API.

    ``ip_address`` supports ``{{variable.path}}`` placeholders so the value
    can be pulled dynamically from the triggering case or alert.

    Return value (stored in ``data``):
        {
          "ip": "<blocked IP>",
          "blocked": <bool>,
          "status_code": <HTTP status>,
          "response_body": "<truncated API response>",
          "blocked_at": "<ISO timestamp>"
        }
    """

    name = "Block IP"
    description = "Block an IP address via a security-device API (firewall / EDR)"
    category = "containment"
    config_schema = {
        "type": "object",
        "properties": {
            "ip_address": {
                "type": "string",
                "description": (
                    "IP address to block.  "
                    "Supports dynamic values, e.g. {{trigger_data.source_ip}}"
                ),
            },
            "api_url": {
                "type": "string",
                "description": "Security-device API endpoint for blocking an IP",
            },
            "api_key": {
                "type": "string",
                "description": "API key / token for the security device",
            },
            "duration_hours": {
                "type": "integer",
                "default": 24,
                "description": "Block duration in hours (0 = permanent)",
            },
            "reason": {
                "type": "string",
                "description": "Reason for blocking (logged on the device)",
            },
            "timeout": {"type": "integer", "default": 15},
        },
        "required": ["ip_address", "api_url", "api_key"],
    }

    def execute(self, config: Dict, context: Dict) -> ActionResult:
        ip = self.resolve_variables(config.get("ip_address", ""), context)
        api_url = self.resolve_variables(config.get("api_url", ""), context)
        api_key = self.resolve_variables(config.get("api_key", ""), context)
        duration = config.get("duration_hours", 24)
        reason = self.resolve_variables(
            config.get("reason", "Blocked by SOAR workflow"), context
        )
        timeout = int(config.get("timeout", 15))

        if not ip:
            return ActionResult(
                success=False,
                error="ip_address is empty after variable resolution",
                logs="Block IP aborted: no IP address provided",
            )

        try:
            response = requests.post(
                api_url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "ip": ip,
                    "action": "block",
                    "duration_hours": duration,
                    "reason": reason,
                },
                timeout=timeout,
            )

            blocked_at = timezone.now().isoformat()
            logger.warning(
                f"[CONTAINMENT] Block IP {ip} via {api_url} -> {response.status_code}"
            )

            return ActionResult(
                success=response.ok,
                data={
                    "ip": ip,
                    "blocked": response.ok,
                    "status_code": response.status_code,
                    "response_body": response.text[:2000],
                    "blocked_at": blocked_at,
                },
                logs=f"Block IP {ip}: HTTP {response.status_code}",
            )
        except Exception as exc:
            return ActionResult(
                success=False,
                error=str(exc),
                logs=f"Block IP failed for {ip}: {exc}",
            )


class DisableUserAction(BaseAction):
    """Disable a user account via an external security-device or AD API.

    ``username`` supports ``{{variable.path}}`` placeholders so the value
    can be pulled dynamically from the triggering case or alert.

    Return value (stored in ``data``):
        {
          "username": "<disabled user>",
          "disabled": <bool>,
          "status_code": <HTTP status>,
          "response_body": "<truncated API response>"
        }
    """

    name = "Disable User"
    description = "Disable a user account via a security-device or AD API"
    category = "containment"
    config_schema = {
        "type": "object",
        "properties": {
            "username": {
                "type": "string",
                "description": (
                    "AD username or UPN to disable.  "
                    "Supports dynamic values, e.g. {{trigger_data.username}}"
                ),
            },
            "api_url": {
                "type": "string",
                "description": "Security-device / AD API endpoint for disabling a user",
            },
            "api_key": {
                "type": "string",
                "description": "API key / token for the security device",
            },
            "reason": {
                "type": "string",
                "description": "Reason for disabling (logged on the device)",
            },
            "timeout": {"type": "integer", "default": 15},
        },
        "required": ["username", "api_url", "api_key"],
    }

    def execute(self, config: Dict, context: Dict) -> ActionResult:
        username = self.resolve_variables(config.get("username", ""), context)
        api_url = self.resolve_variables(config.get("api_url", ""), context)
        api_key = self.resolve_variables(config.get("api_key", ""), context)
        reason = self.resolve_variables(
            config.get("reason", "Disabled by SOAR workflow"), context
        )
        timeout = int(config.get("timeout", 15))

        if not username:
            return ActionResult(
                success=False,
                error="username is empty after variable resolution",
                logs="Disable User aborted: no username provided",
            )

        try:
            response = requests.post(
                api_url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={"username": username, "action": "disable", "reason": reason},
                timeout=timeout,
            )

            logger.warning(
                f"[CONTAINMENT] Disable user {username} via {api_url} -> {response.status_code}"
            )

            return ActionResult(
                success=response.ok,
                data={
                    "username": username,
                    "disabled": response.ok,
                    "status_code": response.status_code,
                    "response_body": response.text[:2000],
                },
                logs=f"Disable user {username}: HTTP {response.status_code}",
            )
        except Exception as exc:
            return ActionResult(
                success=False,
                error=str(exc),
                logs=f"Disable user failed for {username}: {exc}",
            )


# ============ Release Actions ============

class ReleaseIPAction(BaseAction):
    """Release (unblock) an IP address via an external security-device API.

    This is the reverse of :class:`BlockIPAction`.

    ``ip_address`` supports ``{{variable.path}}`` placeholders so the value
    can be pulled dynamically from the triggering case or alert.

    Return value (stored in ``data``):
        {
          "ip": "<released IP>",
          "released": <bool>,
          "status_code": <HTTP status>,
          "response_body": "<truncated API response>",
          "released_at": "<ISO timestamp>"
        }
    """

    name = "Release IP"
    description = "Release (unblock) an IP address via a security-device API"
    category = "release"
    config_schema = {
        "type": "object",
        "properties": {
            "ip_address": {
                "type": "string",
                "description": (
                    "IP address to release.  "
                    "Supports dynamic values, e.g. {{trigger_data.source_ip}}"
                ),
            },
            "api_url": {
                "type": "string",
                "description": "Security-device API endpoint for releasing an IP",
            },
            "api_key": {
                "type": "string",
                "description": "API key / token for the security device",
            },
            "reason": {
                "type": "string",
                "description": "Reason for releasing (logged on the device)",
            },
            "timeout": {"type": "integer", "default": 15},
        },
        "required": ["ip_address", "api_url", "api_key"],
    }

    def execute(self, config: Dict, context: Dict) -> ActionResult:
        ip = self.resolve_variables(config.get("ip_address", ""), context)
        api_url = self.resolve_variables(config.get("api_url", ""), context)
        api_key = self.resolve_variables(config.get("api_key", ""), context)
        reason = self.resolve_variables(
            config.get("reason", "Released by SOAR workflow"), context
        )
        timeout = int(config.get("timeout", 15))

        if not ip:
            return ActionResult(
                success=False,
                error="ip_address is empty after variable resolution",
                logs="Release IP aborted: no IP address provided",
            )

        try:
            response = requests.post(
                api_url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={"ip": ip, "action": "release", "reason": reason},
                timeout=timeout,
            )

            released_at = timezone.now().isoformat()
            logger.info(
                f"[RELEASE] Release IP {ip} via {api_url} -> {response.status_code}"
            )

            return ActionResult(
                success=response.ok,
                data={
                    "ip": ip,
                    "released": response.ok,
                    "status_code": response.status_code,
                    "response_body": response.text[:2000],
                    "released_at": released_at,
                },
                logs=f"Release IP {ip}: HTTP {response.status_code}",
            )
        except Exception as exc:
            return ActionResult(
                success=False,
                error=str(exc),
                logs=f"Release IP failed for {ip}: {exc}",
            )


class EnableUserAction(BaseAction):
    """Enable (re-activate) a user account via an external security-device or AD API.

    This is the reverse of :class:`DisableUserAction`.

    ``username`` supports ``{{variable.path}}`` placeholders so the value
    can be pulled dynamically from the triggering case or alert.

    Return value (stored in ``data``):
        {
          "username": "<enabled user>",
          "enabled": <bool>,
          "status_code": <HTTP status>,
          "response_body": "<truncated API response>"
        }
    """

    name = "Enable User"
    description = "Enable (re-activate) a user account via a security-device or AD API"
    category = "release"
    config_schema = {
        "type": "object",
        "properties": {
            "username": {
                "type": "string",
                "description": (
                    "AD username or UPN to enable.  "
                    "Supports dynamic values, e.g. {{trigger_data.username}}"
                ),
            },
            "api_url": {
                "type": "string",
                "description": "Security-device / AD API endpoint for enabling a user",
            },
            "api_key": {
                "type": "string",
                "description": "API key / token for the security device",
            },
            "reason": {
                "type": "string",
                "description": "Reason for enabling (logged on the device)",
            },
            "timeout": {"type": "integer", "default": 15},
        },
        "required": ["username", "api_url", "api_key"],
    }

    def execute(self, config: Dict, context: Dict) -> ActionResult:
        username = self.resolve_variables(config.get("username", ""), context)
        api_url = self.resolve_variables(config.get("api_url", ""), context)
        api_key = self.resolve_variables(config.get("api_key", ""), context)
        reason = self.resolve_variables(
            config.get("reason", "Enabled by SOAR workflow"), context
        )
        timeout = int(config.get("timeout", 15))

        if not username:
            return ActionResult(
                success=False,
                error="username is empty after variable resolution",
                logs="Enable User aborted: no username provided",
            )

        try:
            response = requests.post(
                api_url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={"username": username, "action": "enable", "reason": reason},
                timeout=timeout,
            )

            logger.info(
                f"[RELEASE] Enable user {username} via {api_url} -> {response.status_code}"
            )

            return ActionResult(
                success=response.ok,
                data={
                    "username": username,
                    "enabled": response.ok,
                    "status_code": response.status_code,
                    "response_body": response.text[:2000],
                },
                logs=f"Enable user {username}: HTTP {response.status_code}",
            )
        except Exception as exc:
            return ActionResult(
                success=False,
                error=str(exc),
                logs=f"Enable user failed for {username}: {exc}",
            )


# ============ Action Registry ============

class ActionRegistry:
    """Registry of available actions."""

    _actions: Dict[str, type] = {}

    @classmethod
    def register(cls, action_type: str, action_class: type):
        """Register an action class."""
        cls._actions[action_type] = action_class

    @classmethod
    def get_action(cls, action_type: str) -> BaseAction:
        """Get an instance of an action by type."""
        action_class = cls._actions.get(action_type)
        if action_class is None:
            raise ValueError(f"Unknown action type: {action_type}")
        return action_class()

    @classmethod
    def get_all_actions(cls) -> Dict[str, type]:
        """Get all registered actions."""
        return cls._actions.copy()

    @classmethod
    def get_action_info(cls) -> list:
        """Get info about all registered actions."""
        result = []
        for action_type, action_class in cls._actions.items():
            result.append({
                'action_type': action_type,
                'name': action_class.name,
                'description': action_class.description,
                'category': action_class.category,
                'config_schema': action_class.config_schema,
            })
        return result


# Register all built-in actions
ActionRegistry.register('log', LogAction)
ActionRegistry.register('delay', DelayAction)
# Utility: set_variable and condition_check are intentionally excluded;
# conditional branching is handled by the Condition node type in the visual editor.
ActionRegistry.register('send_email', SendEmailAction)
ActionRegistry.register('send_webhook', SendWebhookAction)
ActionRegistry.register('create_ticket', CreateTicketAction)
ActionRegistry.register('update_ticket', UpdateTicketAction)
ActionRegistry.register('ip_lookup', IPLookupAction)
ActionRegistry.register('hash_lookup', HashLookupAction)
ActionRegistry.register('block_ip', BlockIPAction)
ActionRegistry.register('disable_user', DisableUserAction)
ActionRegistry.register('release_ip', ReleaseIPAction)
ActionRegistry.register('enable_user', EnableUserAction)

