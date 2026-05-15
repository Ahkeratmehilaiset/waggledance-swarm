# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""Provider-neutral ENG-01 price-feed HTTP transport.

This module fetches an operator-selected URL and returns the raw response body
plus content type for ``eng01_price_feed_response_parser``. It does not know any
provider-specific URL or schema, and it refuses credential-like URL/header
inputs by default instead of implementing auth.
"""
from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address
from typing import Callable, Mapping
from urllib.parse import urlsplit

import httpx

from waggledance.core.v3_13_0.eng01_price_feed_response_parser import (
    SUPPORTED_CONTENT_TYPES,
)


DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_RESPONSE_BYTES = 5_000_000
MAX_TIMEOUT_SECONDS = 60.0
MAX_RESPONSE_BYTES = 50_000_000
DEFAULT_ACCEPT_HEADER = "application/json, application/ld+json;q=0.9"
DEFAULT_USER_AGENT = "waggledance-eng01-price-feed/3.13"

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_CREDENTIAL_HEADER_NAMES = frozenset({
    "authorization",
    "cookie",
    "proxy-authorization",
    "x-api-key",
})
_SECRET_MARKERS = (
    "authorization",
    "bearer",
    "cookie",
    "credential",
    "passwd",
    "password",
    "secret",
    "token",
    "x-api-key",
    "api_key",
)


@dataclass(frozen=True)
class Eng01PriceFeedHttpResponse:
    """Raw HTTP response data for the ENG-01 parser layer."""

    body: bytes
    content_type: str
    status_code: int
    source_url: str


class Eng01PriceFeedHttpTransportError(ValueError):
    """Invalid HTTP transport input or failed price-feed fetch."""


Eng01PriceFeedTransport = Callable[
    [str, Mapping[str, str], float],
    Eng01PriceFeedHttpResponse,
]


def fetch_price_feed_http_response(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    allow_credential_headers: bool = False,
    transport: Eng01PriceFeedTransport | None = None,
) -> Eng01PriceFeedHttpResponse:
    """Fetch an operator-selected price-feed URL.

    The default transport performs one blocking GET call with sync ``httpx``.
    Tests and higher-level adapters can inject ``transport`` to avoid live
    network calls while preserving the same validation contract.
    """
    normalized_url = _validate_url(url)
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
    except Eng01PriceFeedHttpTransportError:
        raise
    except httpx.TimeoutException as exc:
        raise Eng01PriceFeedHttpTransportError("NETWORK_TIMEOUT") from exc
    except httpx.ConnectError as exc:
        raise Eng01PriceFeedHttpTransportError(
            "NETWORK_CONNECT_FAILED"
        ) from exc
    except (
        httpx.DecodingError,
        httpx.HTTPError,
        httpx.RemoteProtocolError,
    ) as exc:
        raise Eng01PriceFeedHttpTransportError(
            "NETWORK_PROTOCOL_FAILED"
        ) from exc
    except Exception as exc:
        raise Eng01PriceFeedHttpTransportError(
            "NETWORK_REQUEST_FAILED"
        ) from exc
    return _validate_response(response, normalized_url, normalized_size_cap)


def _httpx_transport(
    url: str,
    headers: Mapping[str, str],
    timeout_seconds: float,
) -> Eng01PriceFeedHttpResponse:
    timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 10.0))
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        response = client.get(url, headers=dict(headers))
    return Eng01PriceFeedHttpResponse(
        body=response.content,
        content_type=response.headers.get("Content-Type", ""),
        status_code=response.status_code,
        source_url=url,
    )


def _validate_url(url: str) -> str:
    if not isinstance(url, str) or not url.strip():
        raise Eng01PriceFeedHttpTransportError("URL_EMPTY")
    normalized = url.strip()
    parsed = urlsplit(normalized)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES or not parsed.netloc:
        raise Eng01PriceFeedHttpTransportError("URL_SCHEME_REFUSED")
    if parsed.username or parsed.password:
        raise Eng01PriceFeedHttpTransportError("URL_USERINFO_REFUSED")
    if _contains_secret_marker(parsed.query):
        raise Eng01PriceFeedHttpTransportError("URL_SECRET_REFUSED")
    _validate_public_host(parsed.hostname or "")
    return normalized


def _validate_public_host(hostname: str) -> None:
    normalized = hostname.strip().lower()
    if normalized in {"localhost", "localhost."} or normalized.endswith(
        ".localhost"
    ):
        raise Eng01PriceFeedHttpTransportError("URL_LOCAL_HOST_REFUSED")
    try:
        parsed_ip = ip_address(normalized.strip("[]"))
    except ValueError:
        return
    if parsed_ip.is_loopback or parsed_ip.is_link_local:
        raise Eng01PriceFeedHttpTransportError("URL_LOCAL_HOST_REFUSED")
    if parsed_ip.is_private:
        raise Eng01PriceFeedHttpTransportError("URL_PRIVATE_HOST_REFUSED")


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
        raise Eng01PriceFeedHttpTransportError("HEADER_TYPE_REFUSED")
    for key, value in headers.items():
        if not isinstance(key, str) or not key.strip():
            raise Eng01PriceFeedHttpTransportError("HEADER_TYPE_REFUSED")
        if not isinstance(value, str):
            raise Eng01PriceFeedHttpTransportError("HEADER_TYPE_REFUSED")
        clean_key = key.strip()
        clean_value = value.strip()
        lowered_key = clean_key.lower()
        if _has_header_injection(clean_key) or _has_header_injection(clean_value):
            raise Eng01PriceFeedHttpTransportError("HEADER_CONTROL_REFUSED")
        if not allow_credential_headers and (
            lowered_key in _CREDENTIAL_HEADER_NAMES
            or lowered_key.endswith("-token")
            or _contains_secret_marker(clean_value)
        ):
            raise Eng01PriceFeedHttpTransportError(
                "CREDENTIAL_HEADER_REFUSED"
            )
        normalized[clean_key] = clean_value
    return normalized


def _validate_timeout(timeout_seconds: float) -> float:
    if isinstance(timeout_seconds, bool) or not isinstance(
        timeout_seconds,
        (int, float),
    ):
        raise Eng01PriceFeedHttpTransportError("TIMEOUT_OUT_OF_RANGE")
    normalized = float(timeout_seconds)
    if normalized <= 0 or normalized > MAX_TIMEOUT_SECONDS:
        raise Eng01PriceFeedHttpTransportError("TIMEOUT_OUT_OF_RANGE")
    return normalized


def _validate_size_cap(max_response_bytes: int) -> int:
    if isinstance(max_response_bytes, bool) or not isinstance(
        max_response_bytes,
        int,
    ):
        raise Eng01PriceFeedHttpTransportError("SIZE_CAP_OUT_OF_RANGE")
    if max_response_bytes <= 0 or max_response_bytes > MAX_RESPONSE_BYTES:
        raise Eng01PriceFeedHttpTransportError("SIZE_CAP_OUT_OF_RANGE")
    return max_response_bytes


def _validate_response(
    response: Eng01PriceFeedHttpResponse,
    expected_url: str,
    max_response_bytes: int,
) -> Eng01PriceFeedHttpResponse:
    if not isinstance(response, Eng01PriceFeedHttpResponse):
        raise Eng01PriceFeedHttpTransportError("RESPONSE_SHAPE_REFUSED")
    if isinstance(response.status_code, bool) or not isinstance(
        response.status_code,
        int,
    ):
        raise Eng01PriceFeedHttpTransportError("RESPONSE_STATUS_REFUSED")
    if response.status_code < 200 or response.status_code >= 300:
        raise Eng01PriceFeedHttpTransportError(
            f"HTTP_STATUS_{response.status_code}"
        )
    if not isinstance(response.body, bytes):
        raise Eng01PriceFeedHttpTransportError("RESPONSE_BODY_REFUSED")
    if len(response.body) > max_response_bytes:
        raise Eng01PriceFeedHttpTransportError("RESPONSE_TOO_LARGE")
    if not isinstance(response.content_type, str):
        raise Eng01PriceFeedHttpTransportError(
            "RESPONSE_CONTENT_TYPE_REFUSED"
        )
    normalized_content_type = response.content_type.split(";", 1)[0].strip()
    if normalized_content_type.lower() not in SUPPORTED_CONTENT_TYPES:
        raise Eng01PriceFeedHttpTransportError(
            "RESPONSE_CONTENT_TYPE_REFUSED"
        )
    if response.source_url != expected_url:
        raise Eng01PriceFeedHttpTransportError("RESPONSE_SOURCE_URL_REFUSED")
    return response


def _contains_secret_marker(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in _SECRET_MARKERS)


def _has_header_injection(value: str) -> bool:
    return "\r" in value or "\n" in value


__all__ = [
    "DEFAULT_ACCEPT_HEADER",
    "DEFAULT_MAX_RESPONSE_BYTES",
    "DEFAULT_TIMEOUT_SECONDS",
    "DEFAULT_USER_AGENT",
    "Eng01PriceFeedHttpResponse",
    "Eng01PriceFeedHttpTransportError",
    "Eng01PriceFeedTransport",
    "MAX_RESPONSE_BYTES",
    "MAX_TIMEOUT_SECONDS",
    "fetch_price_feed_http_response",
]
