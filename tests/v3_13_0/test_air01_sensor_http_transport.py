# SPDX-License-Identifier: BUSL-1.1
"""Tests for AIR-01 explicit-host sensor HTTP transport."""
from __future__ import annotations

import json
from typing import Mapping

import httpx
import pytest

from waggledance.core.v3_13_0.air01_sensor_http_transport import (
    DEFAULT_ACCEPT_HEADER,
    DEFAULT_USER_AGENT,
    Air01SensorHttpResponse,
    Air01SensorHttpTransportError,
    fetch_air_quality_sensor_response,
)
from waggledance.core.v3_13_0.air01_air_quality_advisor import (
    AIR_QUALITY_WARNING,
    assess_air_quality,
)
from waggledance.core.v3_13_0.air01_digheran_adapter import (
    parse_digheran_air_quality_response,
)


PUBLIC_URL = "https://air.example.test/current.json"
LAN_URL = "http://192.168.1.44/api/air/current"


def _body() -> bytes:
    return json.dumps({
        "timestamp_utc": "2026-05-15T18:00:00Z",
        "readings": {
            "pm25": {"value": 57.25, "unit": "ug/m3"},
            "co": {"value": 2.1, "unit": "ppm"},
        },
    }).encode("utf-8")


def _response(
    *,
    body: bytes | None = None,
    content_type: str = "application/json; charset=utf-8",
    status_code: int = 200,
    source_url: str = PUBLIC_URL,
) -> Air01SensorHttpResponse:
    return Air01SensorHttpResponse(
        body=_body() if body is None else body,
        content_type=content_type,
        status_code=status_code,
        source_url=source_url,
    )


def test_fetch_uses_injected_transport_and_default_headers() -> None:
    calls: list[tuple[str, Mapping[str, str], float]] = []

    def transport(
        url: str,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> Air01SensorHttpResponse:
        calls.append((url, headers, timeout_seconds))
        return _response(source_url=url)

    response = fetch_air_quality_sensor_response(
        PUBLIC_URL,
        timeout_seconds=2,
        transport=transport,
    )

    assert response.body == _body()
    assert calls == [(
        PUBLIC_URL,
        {
            "Accept": DEFAULT_ACCEPT_HEADER,
            "User-Agent": DEFAULT_USER_AGENT,
        },
        2.0,
    )]


def test_private_lan_host_refused_without_explicit_allowlist() -> None:
    with pytest.raises(Air01SensorHttpTransportError,
                       match="URL_PRIVATE_HOST_REFUSED"):
        fetch_air_quality_sensor_response(
            LAN_URL,
            transport=lambda *_: _response(source_url=LAN_URL),
        )


def test_private_lan_host_allowed_only_by_exact_host_allowlist() -> None:
    seen: list[str] = []

    def transport(
        url: str,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> Air01SensorHttpResponse:
        seen.append(url)
        return _response(source_url=url)

    response = fetch_air_quality_sensor_response(
        LAN_URL,
        allowed_private_hosts=("192.168.1.44",),
        transport=transport,
    )

    assert seen == [LAN_URL]
    assert response.source_url == LAN_URL


def test_internal_dns_host_refused_without_allowlist() -> None:
    # SSRF regression (#1443): a local-use DNS name resolves to an internal
    # address; it must be refused unless allowlisted even though it is not a
    # literal private IP. The transport must never be reached.
    def transport(*_):
        raise AssertionError("transport must not run for a refused host")

    for url in (
        "http://air.internal/api/air/current",
        "http://host.lan/current.json",
        "http://sensor.local/current.json",
    ):
        with pytest.raises(Air01SensorHttpTransportError,
                           match="URL_LOCAL_HOST_REFUSED"):
            fetch_air_quality_sensor_response(url, transport=transport)


def test_single_label_host_refused_without_allowlist() -> None:
    def transport(*_):
        raise AssertionError("transport must not run for a refused host")

    with pytest.raises(Air01SensorHttpTransportError,
                       match="URL_LOCAL_HOST_REFUSED"):
        fetch_air_quality_sensor_response(
            "http://air/current.json", transport=transport
        )


def test_internal_dns_host_allowed_only_by_exact_allowlist() -> None:
    internal_url = "http://air.internal/api/air/current"
    seen: list[str] = []

    def transport(
        url: str,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> Air01SensorHttpResponse:
        seen.append(url)
        return _response(source_url=url)

    response = fetch_air_quality_sensor_response(
        internal_url,
        allowed_private_hosts=("air.internal",),
        transport=transport,
    )

    assert seen == [internal_url]
    assert response.source_url == internal_url


def test_public_fqdn_still_allowed_without_allowlist() -> None:
    # Guard against over-blocking: a normal dotted public FQDN must remain
    # reachable without an allowlist entry.
    seen: list[str] = []

    def transport(
        url: str,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> Air01SensorHttpResponse:
        seen.append(url)
        return _response(source_url=url)

    fetch_air_quality_sensor_response(PUBLIC_URL, transport=transport)
    assert seen == [PUBLIC_URL]


def test_allowlisted_lan_response_composes_with_adapter_and_advisor() -> None:
    response = fetch_air_quality_sensor_response(
        LAN_URL,
        allowed_private_hosts=("192.168.1.44",),
        transport=lambda *_: _response(source_url=LAN_URL),
    )
    observation = parse_digheran_air_quality_response(
        response.body,
        content_type=response.content_type,
        source_url=response.source_url,
        fetched_at_utc="2026-05-15T18:02:00Z",
    )
    result = assess_air_quality(observation)

    assert result.result_marker == AIR_QUALITY_WARNING
    assert result.triggered_metrics[0]["metric"] == "pm25_ug_m3"


def test_localhost_refused_even_with_injected_transport_by_default() -> None:
    with pytest.raises(Air01SensorHttpTransportError,
                       match="URL_LOCAL_HOST_REFUSED"):
        fetch_air_quality_sensor_response(
            "http://localhost:8080/current.json",
            transport=lambda *_: _response(
                source_url="http://localhost:8080/current.json"
            ),
        )


@pytest.mark.parametrize("url", [
    "http://0.0.0.0/current.json",
    "http://[::]/current.json",
])
def test_unspecified_ip_refused_by_default(url: str) -> None:
    with pytest.raises(Air01SensorHttpTransportError,
                       match="URL_LOCAL_HOST_REFUSED"):
        fetch_air_quality_sensor_response(
            url,
            transport=lambda *_: _response(source_url=url),
        )


def test_refuses_secret_query_and_credential_header_by_default() -> None:
    with pytest.raises(Air01SensorHttpTransportError, match="URL_SECRET_REFUSED"):
        fetch_air_quality_sensor_response(
            "https://air.example.test/current.json?api_key=abc",
            transport=lambda *_: _response(),
        )

    with pytest.raises(Air01SensorHttpTransportError,
                       match="CREDENTIAL_HEADER_REFUSED"):
        fetch_air_quality_sensor_response(
            PUBLIC_URL,
            headers={"Authorization": "Bearer abc"},
            transport=lambda *_: _response(),
        )


@pytest.mark.parametrize("query", [
    "access_key=abc",
    "private_key=abc",
    "secrets=abc",
    "tokens=abc",
])
def test_refuses_union_secret_query_markers(query: str) -> None:
    with pytest.raises(Air01SensorHttpTransportError, match="URL_SECRET_REFUSED"):
        fetch_air_quality_sensor_response(
            f"https://air.example.test/current.json?{query}",
            transport=lambda *_: _response(),
        )


def test_refuses_union_secret_markers_in_header_values() -> None:
    with pytest.raises(Air01SensorHttpTransportError,
                       match="CREDENTIAL_HEADER_REFUSED"):
        fetch_air_quality_sensor_response(
            PUBLIC_URL,
            headers={"X-Trace": "private_key material"},
            transport=lambda *_: _response(),
        )


@pytest.mark.parametrize("header_name", [
    "X-Access-Key",
    "X-Private-Key",
    "X-My-Tokens",
])
def test_refuses_union_secret_markers_in_header_names(header_name: str) -> None:
    with pytest.raises(Air01SensorHttpTransportError,
                       match="CREDENTIAL_HEADER_REFUSED"):
        fetch_air_quality_sensor_response(
            PUBLIC_URL,
            headers={header_name: "trace-value"},
            transport=lambda *_: _response(),
        )


def test_refuses_redirect_or_html_response_from_transport() -> None:
    with pytest.raises(Air01SensorHttpTransportError, match="HTTP_STATUS_302"):
        fetch_air_quality_sensor_response(
            PUBLIC_URL,
            transport=lambda *_: _response(status_code=302),
        )
    with pytest.raises(Air01SensorHttpTransportError,
                       match="RESPONSE_CONTENT_TYPE_REFUSED"):
        fetch_air_quality_sensor_response(
            PUBLIC_URL,
            transport=lambda *_: _response(content_type="text/html"),
        )


def test_default_httpx_transport_uses_sync_client_without_live_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, *, timeout: httpx.Timeout,
                     follow_redirects: bool) -> None:
            captured["timeout"] = timeout
            captured["follow_redirects"] = follow_redirects

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def get(
            self,
            url: str,
            *,
            headers: Mapping[str, str],
        ) -> httpx.Response:
            captured["url"] = url
            captured["headers"] = headers
            return httpx.Response(
                200,
                content=_body(),
                headers={"Content-Type": "application/json"},
            )

    monkeypatch.setattr(
        "waggledance.core.v3_13_0.air01_sensor_http_transport.httpx.Client",
        FakeClient,
    )

    response = fetch_air_quality_sensor_response(PUBLIC_URL, timeout_seconds=3)

    assert captured["url"] == PUBLIC_URL
    assert captured["headers"] == {
        "Accept": DEFAULT_ACCEPT_HEADER,
        "User-Agent": DEFAULT_USER_AGENT,
    }
    assert captured["follow_redirects"] is False
    assert response == _response(content_type="application/json")
