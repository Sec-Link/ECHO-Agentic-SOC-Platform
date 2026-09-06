from __future__ import annotations

import re
from fnmatch import fnmatchcase
from typing import Any, Callable, Dict

Resolver = Callable[[str], Any]


def normalize_condition_field(field: str) -> str:
    field = (field or "").strip()
    if field.startswith("{{") and field.endswith("}}"):
        field = field[2:-2].strip()
    if field.startswith("trigger.data"):
        field = field.replace("trigger.data", "trigger_data", 1)
    if field.startswith("context."):
        field = field[len("context."):]
    return field


def resolve_context_path(context: Dict[str, Any], path: str) -> Any:
    normalized = normalize_condition_field(path)
    aliases = [normalized]
    if normalized.startswith("ticket."):
        aliases.append("trigger_data." + normalized[len("ticket."):])
    if normalized.startswith("workflow."):
        aliases.append(normalized[len("workflow."):])
    for alias in aliases:
        value: Any = context
        for key in alias.split("."):
            if not isinstance(value, dict) or key not in value:
                break
            value = value[key]
        else:
            return value
    return None


def coerce_number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def evaluate_condition_rule(rule: Dict[str, Any], resolver: Resolver, context: Dict[str, Any] | None = None) -> bool:
    if not isinstance(rule, dict):
        return True
    field = normalize_condition_field(rule.get("field", ""))
    operator = rule.get("operator") or "equals"
    expected = rule.get("value", rule.get("compare_to"))
    if isinstance(expected, str) and expected.startswith("{{") and expected.endswith("}}") and context is not None:
        resolved = resolve_context_path(context, expected)
        if resolved is not None:
            expected = resolved
    if not field:
        return True
    value = resolver(field)
    if operator in {"equals", "=="}:
        return value == expected
    if operator in {"not_equals", "!="}:
        return value != expected
    if operator == "contains":
        return expected in str(value) if value is not None else False
    if operator == "not_contains":
        return expected not in str(value) if value is not None else True
    if operator == "starts_with":
        return str(value).startswith(str(expected)) if value is not None else False
    if operator == "ends_with":
        return str(value).endswith(str(expected)) if value is not None else False
    if operator in {"greater_than", ">", "less_than", "<", "greater_equal", ">=", "less_equal", "<="}:
        left, right = coerce_number(value), coerce_number(expected)
        if left is None or right is None:
            return False
        return {
            "greater_than": left > right,
            ">": left > right,
            "less_than": left < right,
            "<": left < right,
            "greater_equal": left >= right,
            ">=": left >= right,
            "less_equal": left <= right,
            "<=": left <= right,
        }[operator]
    if operator in {"in_list", "not_in_list"}:
        options = [item.strip() for item in str(expected or "").split(",") if item.strip()]
        found = str(value) in options if value is not None else False
        return found if operator == "in_list" else not found
    if operator == "is_empty":
        return value in (None, "")
    if operator in {"is_not_empty", "not_empty"}:
        return value not in (None, "")
    if operator == "matches_regex":
        try:
            return re.search(str(expected), str(value or "")) is not None
        except (re.error, TypeError):
            return False
    if operator == "wildcard":
        return fnmatchcase(str(value or ""), str(expected or ""))
    return True


def evaluate_condition_object(condition: Dict[str, Any], resolver: Resolver, context: Dict[str, Any] | None = None) -> bool:
    if not condition or not isinstance(condition, dict):
        return True
    if condition.get("groups"):
        results = []
        for group in condition["groups"]:
            rules = group.get("rules", []) if isinstance(group, dict) else []
            values = [evaluate_condition_rule(rule, resolver, context) for rule in rules]
            results.append(all(values) if str(group.get("logic", "AND")).upper() == "AND" else any(values))
        return all(results) if str(condition.get("logic", "AND")).upper() == "AND" else any(results)
    if condition.get("field"):
        return evaluate_condition_rule(condition, resolver, context)
    values = [evaluate_condition_rule(rule, resolver, context) for rule in condition.get("rules", [])]
    return all(values) if str(condition.get("logic", "AND")).upper() == "AND" else any(values)


def extract_condition_fields(condition: Dict[str, Any]) -> list[str]:
    fields: list[str] = []
    if not isinstance(condition, dict):
        return fields
    if condition.get("field"):
        fields.append(normalize_condition_field(condition["field"]))
    for rule in condition.get("rules", []) or []:
        if isinstance(rule, dict) and rule.get("field"):
            fields.append(normalize_condition_field(rule["field"]))
    for group in condition.get("groups", []) or []:
        if not isinstance(group, dict):
            continue
        for rule in group.get("rules", []) or []:
            if isinstance(rule, dict) and rule.get("field"):
                fields.append(normalize_condition_field(rule["field"]))
    return [field for field in fields if field]

