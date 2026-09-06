"""Encryption and redaction helpers for workflow action configuration.

Stored values intentionally remain encrypted. Callers may decrypt only the
short-lived copy passed directly to an action's ``execute`` method.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError

from .prefect.actions import ActionRegistry
from .prefect.secrets import (
    ENCRYPTED_PREFIX,
    RuntimeSecretError,
    decrypt_payload,
    encrypt_payload,
)


HEADER_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")



class SecretConfigError(ValidationError):
    """Raised when a sensitive action configuration cannot be handled safely."""


@dataclass(frozen=True)
class SensitiveField:
    name: str
    required: bool
    bindings: tuple[str, ...]
    rebindable: tuple[str, ...]


def _action_schema(action_type: str) -> Dict[str, Any]:
    action_class = ActionRegistry.get_all_actions().get(action_type)
    return copy.deepcopy(getattr(action_class, "config_schema", {}) or {})


def sensitive_fields(action_type: str) -> Dict[str, SensitiveField]:
    schema = _action_schema(action_type)
    required = set(schema.get("required") or [])
    result: Dict[str, SensitiveField] = {}
    for name, definition in (schema.get("properties") or {}).items():
        if not isinstance(definition, dict) or not definition.get("x-sensitive"):
            continue
        result[name] = SensitiveField(
            name=name,
            required=name in required,
            bindings=tuple(definition.get("x-secret-bindings") or ()),
            rebindable=tuple(definition.get("x-secret-rebindable") or ()),
        )
    return result


def is_encrypted(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(ENCRYPTED_PREFIX)


def _normalised_binding_value(field: str, value: Any, schema: Dict[str, Any]) -> Any:
    if value in (None, ""):
        value = ((schema.get("properties") or {}).get(field) or {}).get("default")
    if field == "provider" and value in (None, ""):
        return "generic"
    if isinstance(value, str):
        value = value.strip()
        if field in {"api_url", "url"}:
            value = value.rstrip("/")
    return value


def _binding_snapshot(action_type: str, config: Dict[str, Any], spec: SensitiveField) -> Dict[str, Any]:
    schema = _action_schema(action_type)
    return {
        field: _normalised_binding_value(field, config.get(field), schema)
        for field in spec.bindings
    }


def _binding_digest(action_type: str, field: str, binding: Dict[str, Any]) -> str:
    canonical = json.dumps(
        {"action_type": action_type, "field": field, "binding": binding},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _item_identity(section: str, key: str) -> str:
    return key.casefold() if section == "headers" else key


def _item_binding_digest(section: str, key: str, url: Any) -> str:
    canonical = json.dumps(
        {
            "action_type": "api_call",
            "field": section,
            "item_key": key,
            "binding": {"url": _normalised_binding_value("url", url, _action_schema("api_call"))},
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _encrypt_item_value(section: str, key: str, value: Any, url: Any) -> str:
    payload = {
        "version": 1,
        "action_type": "api_call",
        "field": section,
        "item_key": _item_identity(section, key),
        "binding_digest": _item_binding_digest(section, key, url),
        "value": value,
    }
    try:
        return encrypt_payload(payload, getattr(settings, "WORKFLOW_ENCRYPTION_KEYS", None) or [])
    except RuntimeSecretError as exc:
        raise ImproperlyConfigured(str(exc)) from exc


def _decrypt_item_value(section: str, key: str, value: Any, url: Any) -> Any:
    if not is_encrypted(value):
        raise SecretConfigError(f"Sensitive {section} value is not encrypted.")
    try:
        payload = decrypt_payload(value, getattr(settings, "WORKFLOW_ENCRYPTION_KEYS", None) or [])
    except RuntimeSecretError as exc:
        raise SecretConfigError(f"Sensitive {section} value could not be decrypted.") from exc
    if (
        payload.get("version") != 1
        or payload.get("action_type") != "api_call"
        or payload.get("field") != section
        or payload.get("item_key") != _item_identity(section, key)
        or payload.get("binding_digest") != _item_binding_digest(section, key, url)
    ):
        raise SecretConfigError(
            f"Sensitive {section} value is not valid for the current URL and key; submit it again."
        )
    return payload.get("value")


def _prepare_api_entries(
    section: str,
    incoming: Dict[str, Any],
    existing: Dict[str, Any],
    merged: Dict[str, Any],
) -> None:
    source = incoming.get(section) if section in incoming else existing.get(section, [])
    if source is None:
        source = []
    if not isinstance(source, list):
        raise SecretConfigError(f"{section} must be a list.")
    old_items = {
        _item_identity(section, str(item.get("key") or "").strip()): item
        for item in (existing.get(section) or [])
        if isinstance(item, dict) and str(item.get("key") or "").strip()
    }
    result = []
    seen: set[str] = set()
    for source_item in source:
        if not isinstance(source_item, dict):
            raise SecretConfigError(f"Each {section} entry must be an object.")
        item = copy.deepcopy(source_item)
        key = str(item.get("key") or "").strip()
        identity = _item_identity(section, key)
        if not key or identity in seen:
            raise SecretConfigError(f"{section} contains an empty or duplicate key.")
        if section == "headers" and not HEADER_NAME.fullmatch(key):
            raise SecretConfigError(f"Invalid header key: {key}.")
        if not isinstance(item.get("sensitive", False), bool):
            raise SecretConfigError(f"Sensitive flag for {section} key {key} must be boolean.")
        seen.add(identity)
        sensitive = bool(item.get("sensitive", False))
        old = old_items.get(identity) or {}
        supplied = "value" in item and item.get("value") not in (None, "")
        value = item.get("value")
        if value is not None and not isinstance(value, str):
            raise SecretConfigError(f"Value for {section} key {key} must be a string.")

        if sensitive:
            if not supplied:
                value = old.get("value")
                if not old.get("sensitive") or value in (None, ""):
                    raise SecretConfigError(f"Sensitive {section} value for {key} is required.")
            if is_encrypted(value):
                _decrypt_item_value(section, key, value, merged.get("url"))
            else:
                value = _encrypt_item_value(section, key, value, merged.get("url"))
        else:
            if "value" not in item:
                raise SecretConfigError(f"Value for {section} key {key} is required.")
            if is_encrypted(value):
                raise SecretConfigError(f"Encrypted {section} values must remain marked sensitive.")

        result.append({"key": key, "value": value, "sensitive": sensitive})
    merged[section] = result


def encrypt_sensitive_value(
    action_type: str,
    field: str,
    value: Any,
    config: Dict[str, Any],
) -> str:
    spec = sensitive_fields(action_type).get(field)
    if spec is None:
        raise SecretConfigError(f"{field} is not declared as a sensitive field for {action_type}.")
    binding = _binding_snapshot(action_type, config, spec)
    payload = {
        "version": 1,
        "action_type": action_type,
        "field": field,
        "binding_digest": _binding_digest(action_type, field, binding),
        "value": value,
    }
    try:
        return encrypt_payload(payload, getattr(settings, "WORKFLOW_ENCRYPTION_KEYS", None) or [])
    except RuntimeSecretError as exc:
        raise ImproperlyConfigured(str(exc)) from exc


def decrypt_sensitive_value(
    action_type: str,
    field: str,
    value: Any,
    config: Dict[str, Any],
) -> Any:
    if not is_encrypted(value):
        raise SecretConfigError(f"Sensitive field {field} is not encrypted.")
    spec = sensitive_fields(action_type).get(field)
    if spec is None:
        raise SecretConfigError(f"{field} is not declared as a sensitive field for {action_type}.")
    try:
        payload = decrypt_payload(value, getattr(settings, "WORKFLOW_ENCRYPTION_KEYS", None) or [])
    except RuntimeSecretError as exc:
        raise SecretConfigError(f"Sensitive field {field} could not be decrypted.") from exc

    binding = _binding_snapshot(action_type, config, spec)
    expected = _binding_digest(action_type, field, binding)
    if (
        payload.get("version") != 1
        or payload.get("action_type") != action_type
        or payload.get("field") != field
        or payload.get("binding_digest") != expected
    ):
        raise SecretConfigError(
            f"Sensitive field {field} is not valid for the current action target; submit it again."
        )
    return payload.get("value")


def prepare_config_for_storage(
    action_type: str,
    incoming: Dict[str, Any] | None,
    *,
    existing: Dict[str, Any] | None = None,
    require_sensitive: bool = False,
) -> Dict[str, Any]:
    """Merge write-only values, validate imported tokens, and encrypt plaintext."""
    incoming = copy.deepcopy(incoming or {})
    existing = copy.deepcopy(existing or {})
    merged = copy.deepcopy(existing)
    merged.update(incoming)
    specs = sensitive_fields(action_type)

    for field, spec in specs.items():
        supplied = field in incoming
        supplied_value = incoming.get(field)

        if (not supplied or supplied_value == "") and field in existing:
            existing_value = existing[field]
            if is_encrypted(existing_value):
                try:
                    decrypt_sensitive_value(action_type, field, existing_value, merged)
                    merged[field] = existing_value
                except SecretConfigError as target_error:
                    old_binding = _binding_snapshot(action_type, existing, spec)
                    new_binding = _binding_snapshot(action_type, merged, spec)
                    changed_bindings = {
                        name for name in spec.bindings
                        if old_binding.get(name) != new_binding.get(name)
                    }
                    if changed_bindings and changed_bindings.issubset(set(spec.rebindable)):
                        plaintext = decrypt_sensitive_value(
                            action_type, field, existing_value, existing
                        )
                        merged[field] = encrypt_sensitive_value(
                            action_type, field, plaintext, merged
                        )
                    else:
                        raise target_error
            else:
                # Legacy plaintext encountered through an update path is
                # upgraded immediately instead of being preserved as plaintext.
                merged[field] = encrypt_sensitive_value(
                    action_type, field, existing_value, merged
                )
        elif not supplied or supplied_value in (None, ""):
            merged.pop(field, None)
        elif is_encrypted(supplied_value):
            try:
                # Imported ciphertext must belong to this environment and target.
                decrypt_sensitive_value(action_type, field, supplied_value, merged)
                merged[field] = supplied_value
            except SecretConfigError as target_error:
                if existing.get(field) != supplied_value:
                    raise target_error
                old_binding = _binding_snapshot(action_type, existing, spec)
                new_binding = _binding_snapshot(action_type, merged, spec)
                changed_bindings = {
                    name for name in spec.bindings
                    if old_binding.get(name) != new_binding.get(name)
                }
                if changed_bindings and changed_bindings.issubset(set(spec.rebindable)):
                    plaintext = decrypt_sensitive_value(
                        action_type, field, supplied_value, existing
                    )
                    merged[field] = encrypt_sensitive_value(
                        action_type, field, plaintext, merged
                    )
                else:
                    raise target_error
        else:
            merged[field] = encrypt_sensitive_value(action_type, field, supplied_value, merged)

        if field in merged and is_encrypted(merged[field]):
            # Reject preserving credentials when a bound target has changed.
            decrypt_sensitive_value(action_type, field, merged[field], merged)

        if require_sensitive and spec.required and field not in merged:
            raise SecretConfigError(f"Sensitive field {field} is required.")

    if action_type == "api_call":
        for section in ("headers", "query_params"):
            _prepare_api_entries(section, incoming, existing, merged)
        auth_type = str(merged.get("auth_type") or "none").lower()
        if auth_type != "none" and any(
            item["key"].casefold() == "authorization" for item in merged["headers"]
        ):
            raise SecretConfigError(
                "Built-in authentication cannot be combined with an Authorization header."
            )
        if require_sensitive:
            if auth_type in {"bearer", "basic"} and "auth_secret" not in merged:
                raise SecretConfigError("Sensitive field auth_secret is required for the selected authentication type.")
            if auth_type == "basic" and not str(merged.get("auth_username") or "").strip():
                raise SecretConfigError("auth_username is required for Basic authentication.")

    if require_sensitive and action_type in {"block_ip", "release_ip"}:
        if str(merged.get("provider") or "generic").lower() == "opnsense":
            for field in ("api_key", "api_secret"):
                if field not in merged:
                    raise SecretConfigError(f"Sensitive field {field} is required for OPNsense.")
    return merged


def decrypt_config_for_execution(action_type: str, config: Dict[str, Any] | None) -> Dict[str, Any]:
    result = copy.deepcopy(config or {})
    for field in sensitive_fields(action_type):
        if field in result:
            result[field] = decrypt_sensitive_value(action_type, field, result[field], result)
    if action_type == "api_call":
        for section in ("headers", "query_params"):
            for item in result.get(section) or []:
                if item.get("sensitive"):
                    item["value"] = _decrypt_item_value(section, item["key"], item.get("value"), result.get("url"))
    return result


def rotate_config(action_type: str, config: Dict[str, Any] | None) -> Dict[str, Any]:
    plaintext = decrypt_config_for_execution(action_type, config or {})
    result = copy.deepcopy(config or {})
    for field in sensitive_fields(action_type):
        if field in plaintext:
            result[field] = encrypt_sensitive_value(action_type, field, plaintext[field], result)
    if action_type == "api_call":
        for section in ("headers", "query_params"):
            for item in result.get(section) or []:
                if item.get("sensitive"):
                    plain = next(
                        candidate["value"]
                        for candidate in plaintext.get(section) or []
                        if _item_identity(section, candidate["key"]) == _item_identity(section, item["key"])
                    )
                    item["value"] = _encrypt_item_value(section, item["key"], plain, result.get("url"))
    return result


def redact_config(action_type: str, config: Dict[str, Any] | None) -> tuple[Dict[str, Any], list[str]]:
    result = copy.deepcopy(config or {})
    configured = []
    for field in sensitive_fields(action_type):
        if field in result and result[field] not in (None, ""):
            configured.append(field)
        result.pop(field, None)
    if action_type == "api_call":
        for section in ("headers", "query_params"):
            redacted = []
            for item in result.get(section) or []:
                safe = {"key": item.get("key", ""), "sensitive": bool(item.get("sensitive"))}
                if safe["sensitive"] and item.get("value") not in (None, ""):
                    safe["configured"] = True
                    configured.append(f"{section}.{safe['key']}")
                elif not safe["sensitive"]:
                    safe["value"] = item.get("value", "")
                redacted.append(safe)
            result[section] = redacted
    return result, sorted(configured)


def redact_values(value: Any, secrets: Iterable[Any]) -> Any:
    """Remove plaintext credential values from result/log structures."""
    def collect_strings(items):
        collected = []
        for item in items:
            if isinstance(item, str) and item:
                collected.append(item)
            elif isinstance(item, dict):
                collected.extend(collect_strings(item.values()))
            elif isinstance(item, (list, tuple, set)):
                collected.extend(collect_strings(item))
        return collected

    secret_strings = collect_strings(secrets)
    if isinstance(value, dict):
        return {key: redact_values(item, secret_strings) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_values(item, secret_strings) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_values(item, secret_strings) for item in value)
    if isinstance(value, str):
        for secret in secret_strings:
            value = value.replace(secret, "[REDACTED]")
    return value
