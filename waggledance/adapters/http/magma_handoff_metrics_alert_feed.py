# SPDX-License-Identifier: BUSL-1.1
"""Read-only Alertmanager feed for MAGMA handoff metric alerts."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from ipaddress import ip_address
import json
import math
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from waggledance.core.v3_13_0.secret_markers import (
    contains_secret_marker_substring,
)


DEFAULT_TIMEOUT_SECONDS = 3.0
MAX_TIMEOUT_SECONDS = 15.0
DEFAULT_MAX_RESPONSE_BYTES = 1_000_000
MAX_RESPONSE_BYTES = 5_000_000
DEFAULT_ACCEPT_HEADER = "application/json"
DEFAULT_USER_AGENT = "waggledance-magma-handoff-metrics-alert-feed/3.8"

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_SUPPORTED_CONTENT_TYPES = frozenset({"application/json"})
_CREDENTIAL_HEADER_NAMES = frozenset({
    "authorization",
    "cookie",
    "proxy-authorization",
    "x-api-key",
})


@dataclass(frozen=True)
class MagmaHandoffMetricsAlertFeedHttpResponse:
    body: bytes
    content_type: str
    status_code: int
    source_url: str


class MagmaHandoffMetricsAlertFeedError(ValueError):
    """Invalid operator feed config or failed read-only feed fetch."""


MagmaHandoffMetricsAlertFeedTransport = Callable[
    [str, Mapping[str, str], float, Mapping[str, str]],
    MagmaHandoffMetricsAlertFeedHttpResponse,
]


class UnavailableMagmaHandoffMetricsAlertFeed:
    """Provider object that makes config errors visible as feed unavailable."""

    def snapshot(self) -> dict[str, Any]:
        raise MagmaHandoffMetricsAlertFeedError(
            "MAGMA_HANDOFF_METRICS_ALERT_FEED_UNAVAILABLE"
        )


class MagmaHandoffMetricsAlertmanagerFeed:
    """Fetch MAGMA handoff metric alert state from an operator Alertmanager."""

    def __init__(
        self,
        *,
        alertmanager_base_url: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        allowed_private_hosts: Sequence[str] = (),
        headers: Mapping[str, str] | None = None,
        transport: MagmaHandoffMetricsAlertFeedTransport | None = None,
    ) -> None:
        allowed_hosts = _normalize_allowed_hosts(allowed_private_hosts)
        self._alertmanager_alerts_url = _endpoint_url(
            alertmanager_base_url,
            "/api/v2/alerts",
            allowed_private_hosts=allowed_hosts,
        )
        if self._alertmanager_alerts_url is None:
            raise MagmaHandoffMetricsAlertFeedError("ENDPOINT_EMPTY")
        self._timeout_seconds = _validate_timeout(timeout_seconds)
        self._max_response_bytes = _validate_size_cap(max_response_bytes)
        self._transport = transport or _httpx_transport
        self._headers = _validate_headers(headers)

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        transport: MagmaHandoffMetricsAlertFeedTransport | None = None,
    ) -> "MagmaHandoffMetricsAlertmanagerFeed":
        if not isinstance(config, Mapping):
            raise MagmaHandoffMetricsAlertFeedError("CONFIG_REFUSED")
        return cls(
            alertmanager_base_url=_string_or_none(
                config.get("alertmanager_base_url")
                or config.get("alertmanager_url")
            ),
            timeout_seconds=config.get("timeout_s", DEFAULT_TIMEOUT_SECONDS),
            max_response_bytes=config.get(
                "max_response_bytes",
                DEFAULT_MAX_RESPONSE_BYTES,
            ),
            allowed_private_hosts=config.get("allowed_private_hosts", ()),
            headers=config.get("headers"),
            transport=transport,
        )

    def snapshot(self) -> dict[str, Any]:
        payload = self._get_json(self._alertmanager_alerts_url, {})
        return {
            "updated_at": _utc_now_rfc3339(),
            "active_alerts": _alertmanager_active_alerts(payload),
        }

    def _get_json(self, url: str, params: Mapping[str, str]) -> Any:
        try:
            response = self._transport(
                url,
                self._headers,
                self._timeout_seconds,
                params,
            )
        except MagmaHandoffMetricsAlertFeedError:
            raise
        except httpx.TimeoutException as exc:
            raise MagmaHandoffMetricsAlertFeedError("NETWORK_TIMEOUT") from exc
        except (
            httpx.ConnectError,
            httpx.DecodingError,
            httpx.HTTPError,
            httpx.RemoteProtocolError,
        ) as exc:
            raise MagmaHandoffMetricsAlertFeedError(
                "NETWORK_REQUEST_FAILED"
            ) from exc
        except Exception as exc:
            raise MagmaHandoffMetricsAlertFeedError(
                "NETWORK_REQUEST_FAILED"
            ) from exc
        response = _validate_response(response, url, self._max_response_bytes)
        try:
            return json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MagmaHandoffMetricsAlertFeedError(
                "RESPONSE_JSON_REFUSED"
            ) from exc


def _httpx_transport(
    url: str,
    headers: Mapping[str, str],
    timeout_seconds: float,
    params: Mapping[str, str],
) -> MagmaHandoffMetricsAlertFeedHttpResponse:
    timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 3.0))
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        response = client.get(url, headers=dict(headers), params=dict(params))
    return MagmaHandoffMetricsAlertFeedHttpResponse(
        body=response.content,
        content_type=response.headers.get("Content-Type", ""),
        status_code=response.status_code,
        source_url=url,
    )


def _endpoint_url(
    base_url: str | None,
    endpoint_path: str,
    *,
    allowed_private_hosts: frozenset[str],
) -> str | None:
    if base_url is None:
        return None
    normalized = _validate_base_url(
        base_url,
        allowed_private_hosts=allowed_private_hosts,
    )
    parsed = urlsplit(normalized)
    path = f"{parsed.path.rstrip('/')}{endpoint_path}"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _validate_base_url(
    url: str,
    *,
    allowed_private_hosts: frozenset[str],
) -> str:
    if not isinstance(url, str) or not url.strip():
        raise MagmaHandoffMetricsAlertFeedError("URL_EMPTY")
    normalized = url.strip()
    parsed = urlsplit(normalized)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES or not parsed.netloc:
        raise MagmaHandoffMetricsAlertFeedError("URL_SCHEME_REFUSED")
    if parsed.username or parsed.password:
        raise MagmaHandoffMetricsAlertFeedError("URL_USERINFO_REFUSED")
    if parsed.query or parsed.fragment:
        raise MagmaHandoffMetricsAlertFeedError("URL_QUERY_REFUSED")
    if contains_secret_marker_substring(normalized):
        raise MagmaHandoffMetricsAlertFeedError("URL_SECRET_REFUSED")
    _validate_host(parsed.hostname or "", allowed_private_hosts)
    return normalized


def _validate_host(hostname: str, allowed_private_hosts: frozenset[str]) -> None:
    normalized = _normalize_host(hostname)
    if normalized in {"localhost", "localhost."} or normalized.endswith(
        ".localhost"
    ):
        if normalized not in allowed_private_hosts:
            raise MagmaHandoffMetricsAlertFeedError("URL_LOCAL_HOST_REFUSED")
        return
    try:
        parsed_ip = ip_address(normalized)
    except ValueError:
        return
    if parsed_ip.is_loopback or parsed_ip.is_link_local or parsed_ip.is_unspecified:
        if normalized not in allowed_private_hosts:
            raise MagmaHandoffMetricsAlertFeedError("URL_LOCAL_HOST_REFUSED")
        return
    if parsed_ip.is_private and normalized not in allowed_private_hosts:
        raise MagmaHandoffMetricsAlertFeedError("URL_PRIVATE_HOST_REFUSED")


def _normalize_allowed_hosts(raw_hosts: Sequence[str]) -> frozenset[str]:
    if not isinstance(raw_hosts, Sequence) or isinstance(raw_hosts, (str, bytes)):
        raise MagmaHandoffMetricsAlertFeedError("ALLOWLIST_HOSTS_REFUSED")
    normalized: set[str] = set()
    for index, host in enumerate(raw_hosts):
        if not isinstance(host, str) or not host.strip():
            raise MagmaHandoffMetricsAlertFeedError(
                f"ALLOWLIST_HOSTS_REFUSED_{index}"
            )
        clean = _normalize_host(host)
        if "://" in clean or "/" in clean or "?" in clean or "@" in clean:
            raise MagmaHandoffMetricsAlertFeedError(
                f"ALLOWLIST_HOSTS_REFUSED_{index}"
            )
        normalized.add(clean)
    return frozenset(normalized)


def _normalize_host(host: str) -> str:
    return host.strip().lower().strip("[]")


def _validate_headers(headers: Mapping[str, str] | None) -> Mapping[str, str]:
    normalized: dict[str, str] = {
        "Accept": DEFAULT_ACCEPT_HEADER,
        "User-Agent": DEFAULT_USER_AGENT,
    }
    if headers is None:
        return normalized
    if not isinstance(headers, Mapping):
        raise MagmaHandoffMetricsAlertFeedError("HEADER_TYPE_REFUSED")
    for key, value in headers.items():
        if not isinstance(key, str) or not key.strip():
            raise MagmaHandoffMetricsAlertFeedError("HEADER_TYPE_REFUSED")
        if not isinstance(value, str):
            raise MagmaHandoffMetricsAlertFeedError("HEADER_TYPE_REFUSED")
        clean_key = key.strip()
        clean_value = value.strip()
        lowered_key = clean_key.lower()
        if _has_header_injection(clean_key) or _has_header_injection(clean_value):
            raise MagmaHandoffMetricsAlertFeedError("HEADER_CONTROL_REFUSED")
        if (
            lowered_key in _CREDENTIAL_HEADER_NAMES
            or contains_secret_marker_substring(clean_key)
            or lowered_key.endswith("-token")
            or contains_secret_marker_substring(clean_value)
        ):
            raise MagmaHandoffMetricsAlertFeedError("CREDENTIAL_HEADER_REFUSED")
        normalized[clean_key] = clean_value
    return normalized


def _validate_timeout(timeout_seconds: float) -> float:
    if isinstance(timeout_seconds, bool) or not isinstance(
        timeout_seconds,
        (int, float),
    ):
        raise MagmaHandoffMetricsAlertFeedError("TIMEOUT_OUT_OF_RANGE")
    normalized = float(timeout_seconds)
    if (
        not math.isfinite(normalized)
        or normalized <= 0
        or normalized > MAX_TIMEOUT_SECONDS
    ):
        raise MagmaHandoffMetricsAlertFeedError("TIMEOUT_OUT_OF_RANGE")
    return normalized


def _validate_size_cap(max_response_bytes: int) -> int:
    if isinstance(max_response_bytes, bool) or not isinstance(
        max_response_bytes,
        int,
    ):
        raise MagmaHandoffMetricsAlertFeedError("SIZE_CAP_OUT_OF_RANGE")
    if max_response_bytes <= 0 or max_response_bytes > MAX_RESPONSE_BYTES:
        raise MagmaHandoffMetricsAlertFeedError("SIZE_CAP_OUT_OF_RANGE")
    return max_response_bytes


def _validate_response(
    response: MagmaHandoffMetricsAlertFeedHttpResponse,
    expected_url: str,
    max_response_bytes: int,
) -> MagmaHandoffMetricsAlertFeedHttpResponse:
    if not isinstance(response, MagmaHandoffMetricsAlertFeedHttpResponse):
        raise MagmaHandoffMetricsAlertFeedError("RESPONSE_SHAPE_REFUSED")
    if (
        not isinstance(response.status_code, int)
        or response.status_code < 200
        or response.status_code >= 300
    ):
        raise MagmaHandoffMetricsAlertFeedError("RESPONSE_STATUS_REFUSED")
    if not isinstance(response.body, bytes):
        raise MagmaHandoffMetricsAlertFeedError("RESPONSE_BODY_REFUSED")
    if len(response.body) > max_response_bytes:
        raise MagmaHandoffMetricsAlertFeedError("RESPONSE_TOO_LARGE")
    if not isinstance(response.content_type, str):
        raise MagmaHandoffMetricsAlertFeedError("RESPONSE_CONTENT_TYPE_REFUSED")
    normalized_content_type = response.content_type.split(";", 1)[0].strip()
    if normalized_content_type.lower() not in _SUPPORTED_CONTENT_TYPES:
        raise MagmaHandoffMetricsAlertFeedError("RESPONSE_CONTENT_TYPE_REFUSED")
    if response.source_url != expected_url:
        raise MagmaHandoffMetricsAlertFeedError("RESPONSE_SOURCE_URL_REFUSED")
    return response


def _alertmanager_active_alerts(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise MagmaHandoffMetricsAlertFeedError("ALERTMANAGER_RESULT_REFUSED")
    active = []
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        labels = item.get("labels")
        labels = labels if isinstance(labels, Mapping) else {}
        status = item.get("status")
        status = status if isinstance(status, Mapping) else {}
        state = status.get("state", item.get("state", "active"))
        alertname = labels.get("alertname", item.get("alertname", item.get("id")))
        severity = labels.get("severity", item.get("severity", "warning"))
        if not isinstance(alertname, str):
            continue
        sanitized = {
            "labels": {
                "alertname": alertname,
                "severity": severity if isinstance(severity, str) else "warning",
            },
            "state": state if isinstance(state, str) else "active",
        }
        value = _number_or_none(item.get("value"))
        if value is not None:
            sanitized["value"] = value
        active.append(sanitized)
    return active


def _number_or_none(value: Any) -> float | None:
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return None
    return normalized if math.isfinite(normalized) else None


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) and value.strip() else None


def _utc_now_rfc3339() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _has_header_injection(value: str) -> bool:
    return "\r" in value or "\n" in value


__all__ = [
    "MagmaHandoffMetricsAlertFeedError",
    "MagmaHandoffMetricsAlertFeedHttpResponse",
    "MagmaHandoffMetricsAlertFeedTransport",
    "MagmaHandoffMetricsAlertmanagerFeed",
    "UnavailableMagmaHandoffMetricsAlertFeed",
]
