from __future__ import annotations

import ipaddress
import json
import re
import socket
from typing import Any, Dict
from urllib.parse import urlsplit

import idna
import requests

from .base import ActionResult, BaseAction


MAX_RESPONSE_BYTES = 64 * 1024
HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
HEADER_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
BLOCKED_METADATA_HOSTS = {
    "metadata",
    "metadata.google.internal",
    "instance-data",
    "metadata.tencentyun.com",
}
BLOCKED_METADATA_ADDRESSES = {
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("100.100.100.200"),
    ipaddress.ip_address("fd00:ec2::254"),
}


class ApiCallAction(BaseAction):
    name = "API Call"
    description = "Call an HTTP API with configurable authentication, headers, query parameters, and JSON body"
    category = "notification"
    config_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Absolute HTTP or HTTPS URL"},
            "method": {
                "type": "string",
                "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"],
                "default": "GET",
            },
            "auth_type": {
                "type": "string",
                "enum": ["none", "bearer", "basic"],
                "default": "none",
            },
            "auth_username": {"type": "string", "description": "Basic authentication username"},
            "auth_secret": {
                "type": "string",
                "description": "Bearer token or Basic authentication password",
                "writeOnly": True,
                "x-sensitive": True,
                "x-secret-bindings": ["url", "auth_type", "auth_username"],
            },
            "headers": {
                "type": "array",
                "description": "Request headers",
                "x-sensitive-items": True,
                "items": {"type": "object"},
                "default": [],
            },
            "query_params": {
                "type": "array",
                "description": "Query parameters",
                "x-sensitive-items": True,
                "items": {"type": "object"},
                "default": [],
            },
            "body_template": {
                "type": "string",
                "description": "JSON body supporting {{variable.path}} placeholders",
            },
            "timeout": {"type": "integer", "minimum": 1, "maximum": 120, "default": 30},
            "verify_tls": {"type": "boolean", "default": True},
        },
        "required": ["url"],
    }

    @staticmethod
    def _boolean(value: Any, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, str):
            return value.strip().lower() not in {"false", "0", "no", "off"}
        return bool(value)

    @staticmethod
    def _allowlist(
        context: Dict[str, Any],
    ) -> tuple[set[str], list[ipaddress.IPv4Network | ipaddress.IPv6Network]]:
        values = (context.get("_runtime_policy") or {}).get("workflow_http_allowlist") or []
        hosts: set[str] = set()
        networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
        for raw in values:
            value = str(raw).strip().rstrip(".")
            try:
                networks.append(ipaddress.ip_network(value, strict=False))
            except ValueError:
                try:
                    hosts.add(idna.encode(value, uts46=True).decode("ascii").lower())
                except idna.IDNAError:
                    continue
        return hosts, networks

    @staticmethod
    def _blocked_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        classified = (address.ipv4_mapped or address) if isinstance(address, ipaddress.IPv6Address) else address
        return bool(
            classified in BLOCKED_METADATA_ADDRESSES
            or classified.is_loopback
            or classified.is_link_local
            or classified.is_unspecified
            or classified.is_multicast
        )

    @staticmethod
    def _network_allowed(
        address: ipaddress.IPv4Address | ipaddress.IPv6Address,
        networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network],
    ) -> bool:
        classified = (address.ipv4_mapped or address) if isinstance(address, ipaddress.IPv6Address) else address
        return any(address in network or classified in network for network in networks)

    def _validated_target(self, raw_url: str, context: Dict[str, Any]) -> tuple[str, str]:
        configured = urlsplit(raw_url)
        if (
            configured.scheme not in {"http", "https"}
            or not configured.hostname
            or "{{" in (configured.netloc or "")
            or "}}" in (configured.netloc or "")
        ):
            raise ValueError("API target must have a fixed HTTP or HTTPS hostname.")
        url = str(self.resolve_variables(raw_url, context)).strip()
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or not parsed.netloc:
            raise ValueError("API target must be an absolute HTTP or HTTPS URL.")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("API target cannot contain embedded credentials.")
        if parsed.fragment:
            raise ValueError("API target cannot contain a URL fragment.")
        if parsed.hostname.lower().rstrip(".") != configured.hostname.lower().rstrip("."):
            raise ValueError("API target hostname cannot contain workflow variables.")

        raw_hostname = parsed.hostname.rstrip(".")
        try:
            literal_address = ipaddress.ip_address(raw_hostname.split("%", 1)[0])
            hostname = str(literal_address)
        except ValueError:
            literal_address = None
            try:
                hostname = idna.encode(raw_hostname, uts46=True).decode("ascii").lower()
            except idna.IDNAError as exc:
                raise ValueError("API target hostname is invalid.") from exc
        if hostname in BLOCKED_METADATA_HOSTS:
            raise ValueError("API target is not permitted by the workflow network policy.")

        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("API target port is invalid.") from exc
        request_hostname = f"[{hostname}]" if literal_address and literal_address.version == 6 else hostname
        request_netloc = f"{request_hostname}:{port}" if port is not None else request_hostname
        url = parsed._replace(netloc=request_netloc).geturl()

        allowed_hosts, allowed_networks = self._allowlist(context)
        if literal_address is not None:
            addresses = {literal_address}
            target_allowed = self._network_allowed(literal_address, allowed_networks)
        else:
            hostname_allowed = hostname in allowed_hosts
            if not hostname_allowed and not allowed_networks:
                raise ValueError("API target is not present in the administrator allowlist.")
            try:
                addresses = {
                    ipaddress.ip_address(item[4][0].split("%", 1)[0])
                    for item in socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
                }
            except (OSError, ValueError) as exc:
                raise ValueError("API target hostname could not be resolved.") from exc
            if not addresses:
                raise ValueError("API target hostname could not be resolved.")
            target_allowed = hostname_allowed or all(
                self._network_allowed(address, allowed_networks) for address in addresses
            )

        for address in addresses:
            if self._blocked_address(address):
                raise ValueError("API target is not permitted by the workflow network policy.")
        if not target_allowed:
            raise ValueError("API target is not present in the administrator allowlist.")
        return url, hostname

    def _entries(self, entries: Any, context: Dict[str, Any], *, headers: bool) -> Dict[str, str]:
        result: Dict[str, str] = {}
        seen: set[str] = set()
        for item in entries or []:
            if not isinstance(item, dict):
                raise ValueError("API request entries must be objects.")
            key = str(item.get("key") or "").strip()
            identity = key.casefold() if headers else key
            if not key or identity in seen or (headers and not HEADER_NAME.fullmatch(key)):
                raise ValueError("API request contains an invalid or duplicate header/query key.")
            seen.add(identity)
            result[key] = str(self.resolve_variables(item.get("value", ""), context))
        return result

    def execute(self, config: Dict[str, Any], context: Dict[str, Any]) -> ActionResult:
        method = str(config.get("method") or "GET").upper()
        auth_type = str(config.get("auth_type") or "none").lower()
        try:
            if method not in HTTP_METHODS:
                raise ValueError("Unsupported API request method.")
            if auth_type not in {"none", "bearer", "basic"}:
                raise ValueError("Unsupported API authentication type.")
            timeout = int(config.get("timeout", 30))
            if not 1 <= timeout <= 120:
                raise ValueError("API request timeout must be between 1 and 120 seconds.")
            url, hostname = self._validated_target(str(config.get("url") or ""), context)
            headers = self._entries(config.get("headers"), context, headers=True)
            query = self._entries(config.get("query_params"), context, headers=False)
            if auth_type != "none" and any(key.casefold() == "authorization" for key in headers):
                raise ValueError("Built-in authentication cannot be combined with an Authorization header.")

            secret = str(config.get("auth_secret") or "")
            auth = None
            if auth_type == "bearer":
                if not secret:
                    raise ValueError("Bearer authentication requires an auth secret.")
                headers["Authorization"] = f"Bearer {secret}"
            elif auth_type == "basic":
                username = str(config.get("auth_username") or "")
                if not username or not secret:
                    raise ValueError("Basic authentication requires a username and auth secret.")
                auth = (username, secret)

            body = None
            body_template = config.get("body_template")
            if method == "GET" and body_template not in (None, ""):
                raise ValueError("GET API requests do not accept a body.")
            if method != "GET" and body_template not in (None, ""):
                try:
                    body = json.loads(self.resolve_variables(str(body_template), context))
                except json.JSONDecodeError as exc:
                    raise ValueError("API request body is not valid JSON after variable substitution.") from exc

            verify_tls = self._boolean(config.get("verify_tls"), True)
            warning = " TLS verification disabled." if not verify_tls else ""
            # DNS is intentionally not pinned; the configured resolver remains
            # an infrastructure trust boundary for the subsequent request.
            response = requests.request(
                method,
                url,
                headers=headers,
                params=query,
                json=body,
                auth=auth,
                timeout=timeout,
                verify=verify_tls,
                allow_redirects=False,
            )
            raw = response.content
            truncated = len(raw) > MAX_RESPONSE_BYTES
            try:
                response_body = raw[:MAX_RESPONSE_BYTES].decode(response.encoding or "utf-8", errors="replace")
            except LookupError:
                response_body = raw[:MAX_RESPONSE_BYTES].decode("utf-8", errors="replace")
            encoded_body = response_body.encode("utf-8")
            if len(encoded_body) > MAX_RESPONSE_BYTES:
                response_body = encoded_body[:MAX_RESPONSE_BYTES].decode("utf-8", errors="ignore")
                truncated = True
            response_json = None
            if not truncated and response_body:
                try:
                    response_json = json.loads(response_body)
                except json.JSONDecodeError:
                    pass
            success = 200 <= response.status_code < 300
            return ActionResult(
                success,
                {
                    "status_code": response.status_code,
                    "response_body": response_body,
                    "response_json": response_json,
                    "truncated": truncated,
                },
                "" if success else f"API request returned HTTP {response.status_code}.",
                f"HTTP {method} {hostname} -> {response.status_code}.{warning}".strip(),
            )
        except requests.RequestException as exc:
            return ActionResult(
                False,
                error=f"API request failed ({type(exc).__name__}).",
                logs="API request failed without exposing request data.",
            )
        except (TypeError, ValueError) as exc:
            return ActionResult(False, error=str(exc), logs="API request validation failed.")
