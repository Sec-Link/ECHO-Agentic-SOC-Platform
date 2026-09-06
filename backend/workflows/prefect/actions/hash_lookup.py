from __future__ import annotations

from typing import Any, Dict

import requests

from .base import ActionResult, BaseAction


class HashLookupAction(BaseAction):
    name = "Hash Lookup"
    description = "Check file-hash reputation via a configurable threat-intel platform"
    category = "enrichment"
    config_schema = {
        "type": "object",
        "properties": {
            "hash_value": {"type": "string", "description": "MD5, SHA-1, or SHA-256 value"},
            "hash_type": {"type": "string", "enum": ["md5", "sha1", "sha256"], "default": "sha256"},
            "api_url": {"type": "string", "description": "Threat-intelligence API endpoint"},
            "api_key": {"type": "string", "writeOnly": True, "x-sensitive": True, "x-secret-bindings": ["api_url"]},
            "timeout": {"type": "integer", "default": 15},
        },
        "required": ["hash_value", "api_url", "api_key"],
    }

    def execute(self, config: Dict[str, Any], context: Dict[str, Any]) -> ActionResult:
        value = self.resolve_variables(config.get("hash_value", ""), context)
        if not value:
            return ActionResult(False, error="hash_value is empty after variable resolution", logs="Hash Lookup aborted: no hash provided")
        url = self.resolve_variables(str(config.get("api_url", "")).replace("{hash}", value), context)
        try:
            response = requests.get(url, headers={"x-apikey": self.resolve_variables(config.get("api_key", ""), context), "Accept": "application/json"}, timeout=int(config.get("timeout", 15)))
            raw = response.json() if response.content else {}
            attrs = raw.get("data", {}).get("attributes", {}) if isinstance(raw, dict) else {}
            stats = attrs.get("last_analysis_stats", {})
            detections = stats.get("malicious", 0)
            total = sum(stats.values()) if stats else 0
            data = {
                "hash": value,
                "hash_type": config.get("hash_type", "sha256"),
                "is_malicious": detections > 0,
                "detections": detections,
                "total_engines": total,
                "file_name": attrs.get("meaningful_name", ""),
                "file_type": attrs.get("type_description", ""),
                "summary": f"{detections}/{total} engines flagged as malicious" if attrs else "",
                "raw_response": raw,
            }
            return ActionResult(True, data, logs=f"Hash lookup for {value}: {detections}/{total} detections")
        except Exception as exc:
            return ActionResult(False, error=str(exc), logs=f"Hash lookup failed for {value}: {exc}")

