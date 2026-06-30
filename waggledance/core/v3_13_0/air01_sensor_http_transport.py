# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""AIR-01 read-only HTTP transport for operator-allowlisted sensors.

This module performs one operator-selected JSON GET. It deliberately does not
scan networks, follow redirects, store credentials, or infer LAN devices. Every
host is refused unless the caller explicitly allowlists the exact host for this
read-only request.
"""
from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address
from typing import Callable, Mapping, Sequence
from urllib.parse import urlsplit

import httpx

from waggledance.core.v3_13_0.secret_markers import (
    contains_secret_marker_substring,
)


DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_MAX_RESPONSE_BYTES = 2_000_000
MAX_TIMEOUT_SECONDS = 30.0
MAX_RESPONSE_BYTES = 10_000_000
DEFAULT_ACCEPT_HEADER = "application/json, application/ld+json;q=0.9"
DEFAULT_USER_AGENT = "waggledance-air01-sensor-bridge/3.13"

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_SUPPORTED_CONTENT_TYPES = frozenset({"application/json", "application/ld+json"})
_CREDENTIAL_HEADER_NAMES = frozenset({
    "authorization",
    "cookie",
    "proxy-authorization",
    "x-api-key",
})
@dataclass(frozen=True)
class Air01SensorHttpResponse:
    """Raw HTTP response data for the AIR-01 parser layer."""

    body: bytes
    content_type: str
    status_code: int
    source_url: str


class Air01SensorHttpTransportError(ValueError):
    """Invalid HTTP transport input or failed sensor fetch."""


Air01SensorTransport = Callable[
    [str, Mapping[str, str], float],
    Air01SensorHttpResponse,
]


def fetch_air_quality_sensor_response(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    allowed_private_hosts: Sequence[str] = (),
    allow_credential_headers: bool = False,
    transport: Air01SensorTransport | None = None,
) -> Air01SensorHttpResponse:
    """Fetch one explicit, operator-allowlisted air-quality JSON endpoint."""
    normalized_allowed_hosts = _normalize_allowed_hosts(allowed_private_hosts)
    normalized_url = _validate_url(
        url,
        allowed_private_hosts=normalized_allowed_hosts,
    )
    normalized_headers = _validate_headers(
        headers,
        allow_credential_headers=allow_credential_headers,
    )
    normalized_timeout = _validate_timeout(timeout_seconds)
    normalized_size_cap = _validate_size_cap(max_response_bytes)
    fetcher = transport if transport is not None else _httpx_transport
    try:
        response = fetcher(
            normalized_url,
            normalized_headers,
            normalized_timeout,
        )
    except Air01SensorHttpTransportError:
        raise
    except httpx.TimeoutException as exc:
        raise Air01SensorHttpTransportError("NETWORK_TIMEOUT") from exc
    except httpx.ConnectError as exc:
        raise Air01SensorHttpTransportError("NETWORK_CONNECT_FAILED") from exc
    except (
        httpx.DecodingError,
        httpx.HTTPError,
        httpx.RemoteProtocolError,
    ) as exc:
        raise Air01SensorHttpTransportError(
            "NETWORK_PROTOCOL_FAILED"
        ) from exc
    except Exception as exc:
        raise Air01SensorHttpTransportError("NETWORK_REQUEST_FAILED") from exc
    return _validate_response(response, normalized_url, normalized_size_cap)


def _httpx_transport(
    url: str,
    headers: Mapping[str, str],
    timeout_seconds: float,
) -> Air01SensorHttpResponse:
    timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 5.0))
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        response = client.get(url, headers=dict(headers))
    return Air01SensorHttpResponse(
        body=response.content,
        content_type=response.headers.get("Content-Type", ""),
        status_code=response.status_code,
        source_url=url,
    )


def _validate_url(
    url: str,
    *,
    allowed_private_hosts: frozenset[str],
) -> str:
    if not isinstance(url, str) or not url.strip():
        raise Air01SensorHttpTransportError("URL_EMPTY")
    normalized = url.strip()
    parsed = urlsplit(normalized)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES or not parsed.netloc:
        raise Air01SensorHttpTransportError("URL_SCHEME_REFUSED")
    if parsed.username or parsed.password:
        raise Air01SensorHttpTransportError("URL_USERINFO_REFUSED")
    if contains_secret_marker_substring(parsed.query):
        raise Air01SensorHttpTransportError("URL_SECRET_REFUSED")
    _validate_host(parsed.hostname or "", allowed_private_hosts)
    return normalized


def _validate_host(hostname: str, allowed_private_hosts: frozenset[str]) -> None:
    normalized = _normalize_host(hostname)
    if normalized in allowed_private_hosts:
        return
    if normalized in {"localhost", "localhost."} or normalized.endswith(
        ".localhost"
    ):
        raise Air01SensorHttpTransportError("URL_LOCAL_HOST_REFUSED")
    try:
        parsed_ip = ip_address(normalized)
    except ValueError:
        raise Air01SensorHttpTransportError("URL_HOST_NOT_ALLOWLISTED") from None
    if parsed_ip.is_loopback or parsed_ip.is_link_local or parsed_ip.is_unspecified:
        raise Air01SensorHttpTransportError("URL_LOCAL_HOST_REFUSED")
    if parsed_ip.is_private:
        raise Air01SensorHttpTransportError("URL_PRIVATE_HOST_REFUSED")
    raise Air01SensorHttpTransportError("URL_HOST_NOT_ALLOWLISTED")


def _normalize_allowed_hosts(raw_hosts: Sequence[str]) -> frozenset[str]:
    if not isinstance(raw_hosts, Sequence) or isinstance(raw_hosts, (str, bytes)):
        raise Air01SensorHttpTransportError("ALLOWLIST_HOSTS_REFUSED")
    normalized: set[str] = set()
    for index, host in enumerate(raw_hosts):
        if not isinstance(host, str) or not host.strip():
            raise Air01SensorHttpTransportError(
                f"ALLOWLIST_HOSTS_REFUSED_{index}"
            )
        clean = _normalize_host(host)
        if "://" in clean or "/" in clean or "?" in clean or "@" in clean:
            raise Air01SensorHttpTransportError(
                f"ALLOWLIST_HOSTS_REFUSED_{index}"
            )
        normalized.add(clean)
    return frozenset(normalized)


def _normalize_host(host: str) -> str:
    return host.strip().lower().strip("[]").rstrip(".")


def _validate_headers(
    headers: Mapping[str, str] | None,
    *,
    allow_credential_headers: bool,
) -> Mapping[str, str]:
    normalized: dict[str, str] = {
        "Accept": DEFAULT_ACCEPT_HEADER,
        "User-Agent": DEFAULT_USER_AGENT,
    }
    if headers is None:
        return normalized
    if not isinstance(headers, Mapping):
        raise Air01SensorHttpTransportError("HEADER_TYPE_REFUSED")
    for key, value in headers.items():
        if not isinstance(key, str) or not key.strip():
            raise Air01SensorHttpTransportError("HEADER_TYPE_REFUSED")
        if not isinstance(value, str):
            raise Air01SensorHttpTransportError("HEADER_TYPE_REFUSED")
        clean_key = key.strip()
        clean_value = value.strip()
        lowered_key = clean_key.lower()
        if _has_header_injection(clean_key) or _has_header_injection(clean_value):
            raise Air01SensorHttpTransportError("HEADER_CONTROL_REFUSED")
        if not allow_credential_headers and (
            lowered_key in _CREDENTIAL_HEADER_NAMES
            or contains_secret_marker_substring(clean_key)
            or lowered_key.endswith("-token")
            or contains_secret_marker_substring(clean_value)
        ):
            raise Air01SensorHttpTransportError("CREDENTIAL_HEADER_REFUSED")
        normalized[clean_key] = clean_value
    return normalized


def _validate_timeout(timeout_seconds: float) -> float:
    if isinstance(timeout_seconds, bool) or not isinstance(
        timeout_seconds,
        (int, float),
    ):
        raise Air01SensorHttpTransportError("TIMEOUT_OUT_OF_RANGE")
    normalized = float(timeout_seconds)
    if normalized <= 0 or normalized > MAX_TIMEOUT_SECONDS:
        raise Air01SensorHttpTransportError("TIMEOUT_OUT_OF_RANGE")
    return normalized


def _validate_size_cap(max_response_bytes: int) -> int:
    if isinstance(max_response_bytes, bool) or not isinstance(
        max_response_bytes,
        int,
    ):
        raise Air01SensorHttpTransportError("SIZE_CAP_OUT_OF_RANGE")
    if max_response_bytes <= 0 or max_response_bytes > MAX_RESPONSE_BYTES:
        raise Air01SensorHttpTransportError("SIZE_CAP_OUT_OF_RANGE")
    return max_response_bytes


def _validate_response(
    response: Air01SensorHttpResponse,
    expected_url: str,
    max_response_bytes: int,
) -> Air01SensorHttpResponse:
    if not isinstance(response, Air01SensorHttpResponse):
        raise Air01SensorHttpTransportError("RESPONSE_SHAPE_REFUSED")
    if isinstance(response.status_code, bool) or not isinstance(
        response.status_code,
        int,
    ):
        raise Air01SensorHttpTransportError("RESPONSE_STATUS_REFUSED")
    if response.status_code < 200 or response.status_code >= 300:
        raise Air01SensorHttpTransportError(
            f"HTTP_STATUS_{response.status_code}"
        )
    if not isinstance(response.body, bytes):
        raise Air01SensorHttpTransportError("RESPONSE_BODY_REFUSED")
    if len(response.body) > max_response_bytes:
        raise Air01SensorHttpTransportError("RESPONSE_TOO_LARGE")
    if not isinstance(response.content_type, str):
        raise Air01SensorHttpTransportError("RESPONSE_CONTENT_TYPE_REFUSED")
    normalized_content_type = response.content_type.split(";", 1)[0].strip()
    if normalized_content_type.lower() not in _SUPPORTED_CONTENT_TYPES:
        raise Air01SensorHttpTransportError(
            "RESPONSE_CONTENT_TYPE_REFUSED"
        )
    if response.source_url != expected_url:
        raise Air01SensorHttpTransportError("RESPONSE_SOURCE_URL_REFUSED")
    return response


def _has_header_injection(value: str) -> bool:
    return "\r" in value or "\n" in value


__all__ = [
    "Air01SensorHttpResponse",
    "Air01SensorHttpTransportError",
    "Air01SensorTransport",
    "DEFAULT_ACCEPT_HEADER",
    "DEFAULT_MAX_RESPONSE_BYTES",
    "DEFAULT_TIMEOUT_SECONDS",
    "DEFAULT_USER_AGENT",
    "MAX_RESPONSE_BYTES",
    "MAX_TIMEOUT_SECONDS",
    "fetch_air_quality_sensor_response",
]
