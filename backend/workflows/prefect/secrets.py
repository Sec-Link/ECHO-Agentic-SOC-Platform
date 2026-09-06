from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from typing import Any, Dict, Iterable

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from .actions import ActionRegistry

ENCRYPTED_PREFIX = "enc:v1:"


class RuntimeSecretError(ValueError):
    pass


def _keys_from_env() -> list[str]:
    keys = [item.strip() for item in os.getenv("WORKFLOW_ENCRYPTION_KEYS", "").split(",") if item.strip()]
    if not keys:
        raise RuntimeSecretError("WORKFLOW_ENCRYPTION_KEYS is required by the workflow worker.")
    return keys


def fernet_ring(keys: Iterable[str]) -> tuple[Fernet, MultiFernet]:
    try:
        fernets = [Fernet(str(key).encode("ascii")) for key in keys]
        if not fernets:
            raise ValueError("no keys")
        return fernets[0], MultiFernet(fernets)
    except (TypeError, ValueError) as exc:
        raise RuntimeSecretError("WORKFLOW_ENCRYPTION_KEYS contains an invalid Fernet key.") from exc


def encrypt_payload(payload: Dict[str, Any], keys: Iterable[str]) -> str:
    primary, _ = fernet_ring(keys)
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return ENCRYPTED_PREFIX + primary.encrypt(raw).decode("ascii")


def decrypt_payload(value: Any, keys: Iterable[str]) -> Dict[str, Any]:
    if not isinstance(value, str) or not value.startswith(ENCRYPTED_PREFIX):
        raise RuntimeSecretError("Sensitive value is not encrypted.")
    _, ring = fernet_ring(keys)
    try:
        raw = ring.decrypt(value[len(ENCRYPTED_PREFIX):].encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
    except (InvalidToken, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeSecretError("Sensitive value could not be decrypted.") from exc
    if not isinstance(payload, dict):
        raise RuntimeSecretError("Sensitive value payload is invalid.")
    return payload


def _normalise(field: str, value: Any, schema: Dict[str, Any]) -> Any:
    if value in (None, ""):
        value = ((schema.get("properties") or {}).get(field) or {}).get("default")
    if field == "provider" and value in (None, ""):
        return "generic"
    if isinstance(value, str):
        value = value.strip()
        if field in {"api_url", "url"}:
            value = value.rstrip("/")
    return value


def _item_identity(section: str, key: str) -> str:
    return key.casefold() if section == "headers" else key


def _item_digest(section: str, key: str, url: Any, schema: Dict[str, Any]) -> str:
    canonical = json.dumps(
        {
            "action_type": "api_call",
            "field": section,
            "item_key": key,
            "binding": {"url": _normalise("url", url, schema)},
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def decrypt_config_for_execution(action_type: str, config: Dict[str, Any] | None, keys: Iterable[str] | None = None) -> Dict[str, Any]:
    result = deepcopy(config or {})
    action = ActionRegistry.get_all_actions().get(action_type)
    schema = deepcopy(getattr(action, "config_schema", {}) or {})
    encryption_keys = list(keys) if keys is not None else None
    for field, definition in (schema.get("properties") or {}).items():
        if not isinstance(definition, dict) or not definition.get("x-sensitive") or field not in result:
            continue
        if encryption_keys is None:
            encryption_keys = _keys_from_env()
        payload = decrypt_payload(result[field], encryption_keys)
        binding = {name: _normalise(name, result.get(name), schema) for name in definition.get("x-secret-bindings", [])}
        canonical = json.dumps({"action_type": action_type, "field": field, "binding": binding}, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        expected = hashlib.sha256(canonical).hexdigest()
        if payload.get("version") != 1 or payload.get("action_type") != action_type or payload.get("field") != field or payload.get("binding_digest") != expected:
            raise RuntimeSecretError(f"Sensitive field {field} is not valid for the current action target.")
        result[field] = payload.get("value")
    if action_type == "api_call":
        for section in ("headers", "query_params"):
            for item in result.get(section) or []:
                if not item.get("sensitive"):
                    continue
                if encryption_keys is None:
                    encryption_keys = _keys_from_env()
                key = str(item.get("key") or "")
                payload = decrypt_payload(item.get("value"), encryption_keys)
                if (
                    payload.get("version") != 1
                    or payload.get("action_type") != "api_call"
                    or payload.get("field") != section
                    or payload.get("item_key") != _item_identity(section, key)
                    or payload.get("binding_digest") != _item_digest(section, key, result.get("url"), schema)
                ):
                    raise RuntimeSecretError(
                        f"Sensitive {section} value is not valid for the current URL and key."
                    )
                item["value"] = payload.get("value")
    return result


def secret_values(action_type: str, config: Dict[str, Any], schema: Dict[str, Any]) -> list[Any]:
    values = [
        config.get(name)
        for name, spec in (schema.get("properties") or {}).items()
        if isinstance(spec, dict) and spec.get("x-sensitive")
    ]
    if action_type == "api_call":
        values.extend(
            item.get("value")
            for section in ("headers", "query_params")
            for item in config.get(section) or []
            if item.get("sensitive")
        )
    return values


def redact_values(value: Any, secrets: Iterable[Any]) -> Any:
    def strings(items: Iterable[Any]) -> list[str]:
        result: list[str] = []
        for item in items:
            if isinstance(item, str) and item:
                result.append(item)
            elif isinstance(item, dict):
                result.extend(strings(item.values()))
            elif isinstance(item, (list, tuple, set)):
                result.extend(strings(item))
        return result

    secret_strings = strings(secrets)
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
