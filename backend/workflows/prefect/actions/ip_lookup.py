from __future__ import annotations

from typing import Any, Dict

import requests

from .base import ActionResult, BaseAction


class IPLookupAction(BaseAction):
    name = "IP Lookup"
    description = "Check IP reputation via a configurable threat-intel platform"
    category = "enrichment"
    config_schema = {
        "type": "object",
        "properties": {
            "ip_address": {"type": "string", "description": "IP address to look up"},
            "api_url": {"type": "string", "description": "Threat-intelligence API endpoint"},
            "api_key": {"type": "string", "writeOnly": True, "x-sensitive": True, "x-secret-bindings": ["api_url"]},
            "timeout": {"type": "integer", "default": 15},
        },
        "required": ["ip_address", "api_url", "api_key"],
    }

    def execute(self, config: Dict[str, Any], context: Dict[str, Any]) -> ActionResult:
        ip = self.resolve_variables(config.get("ip_address", ""), context)
        if not ip:
            return ActionResult(False, error="ip_address is empty after variable resolution", logs="IP Lookup aborted: no IP address provided")
        try:
            response = requests.get(
                self.resolve_variables(config.get("api_url", ""), context),
                headers={"Key": self.resolve_variables(config.get("api_key", ""), context), "Accept": "application/json"},
                params={"ipAddress": ip, "maxAgeInDays": "90"},
                timeout=int(config.get("timeout", 15)),
            )
            raw = response.json() if response.content else {}
            block = raw.get("data", {}) if isinstance(raw, dict) else {}
            country = block.get("countryCode") or raw.get("country", "Unknown")
            asn = block.get("isp") or raw.get("asn", "Unknown")
            risk_score, summary = 0, ""
            if isinstance(block, dict) and "attributes" in block:
                count = block["attributes"].get("last_analysis_stats", {}).get("malicious", 0)
                risk_score, summary = min(count * 5, 100), f"Malicious engines: {count}"
            elif isinstance(block, dict):
                risk_score = block.get("abuseConfidenceScore", 0)
                summary = f"Total reports: {block.get('totalReports', 0)}"
            data = {"ip": ip, "is_malicious": risk_score >= 25, "risk_score": risk_score, "country": country, "asn": asn, "summary": summary, "raw_response": raw}
            return ActionResult(True, data, logs=f"IP lookup for {ip}: risk_score={risk_score}, is_malicious={data['is_malicious']}")
        except Exception as exc:
            return ActionResult(False, error=str(exc), logs=f"IP lookup failed for {ip}: {exc}")

