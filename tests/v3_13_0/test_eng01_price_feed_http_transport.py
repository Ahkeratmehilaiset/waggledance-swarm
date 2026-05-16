# SPDX-License-Identifier: BUSL-1.1
"""Tests for the provider-neutral ENG-01 price-feed HTTP transport."""
from __future__ import annotations

import json
from typing import Mapping

import httpx
import pytest

from waggledance.core.v3_13_0.eng01_price_feed_adapter import (
    PRICE_UNIT_EUR_PER_MWH,
    build_eng01_price_feed,
)
from waggledance.core.v3_13_0.eng01_price_feed_http_transport import (
    DEFAULT_ACCEPT_HEADER,
    DEFAULT_MAX_RESPONSE_BYTES,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_USER_AGENT,
    MAX_RESPONSE_BYTES,
    MAX_TIMEOUT_SECONDS,
    Eng01PriceFeedHttpResponse,
    Eng01PriceFeedHttpTransportError,
    fetch_price_feed_http_response,
)
from waggledance.core.v3_13_0.eng01_price_feed_response_parser import (
    parse_price_feed_response,
)
from waggledance.core.v3_13_0.eng01_spot_electricity import (
    OK,
    recommend_top_3_cheapest_hours,
)


URL = "https://prices.example.test/day-ahead.json"


def _body() -> bytes:
    return json.dumps([
        {"hour_utc": "2026-01-16T00:00:00Z", "price": 100.0},
        {"hour_utc": "2026-01-16T01:00:00Z", "price": 75.0},
        {"hour_utc": "2026-01-16T02:00:00Z", "price": 125.0},
    ]).encode("utf-8")


def _response(
    *,
    body: bytes | None = None,
    content_type: str = "application/json; charset=utf-8",
    status_code: int = 200,
    source_url: str = URL,
) -> Eng01PriceFeedHttpResponse:
    return Eng01PriceFeedHttpResponse(
        body=_body() if body is None else body,
        content_type=content_type,
        status_code=status_code,
        source_url=source_url,
    )


def test_fetch_uses_injected_transport_and_composes_with_parser() -> None:
    calls: list[tuple[str, Mapping[str, str], float]] = []

    def transport(
        url: str,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> Eng01PriceFeedHttpResponse:
        calls.append((url, headers, timeout_seconds))
        return _response()

    response = fetch_price_feed_http_response(
        URL,
        headers={"Accept": "application/json"},
        timeout_seconds=2,
        transport=transport,
    )
    rows = parse_price_feed_response(
        response.body,
        content_type=response.content_type,
    )

    assert rows == [
        {"hour_utc": "2026-01-16T00:00:00Z", "price": 100.0},
        {"hour_utc": "2026-01-16T01:00:00Z", "price": 75.0},
        {"hour_utc": "2026-01-16T02:00:00Z", "price": 125.0},
    ]
    assert calls == [(
        URL,
        {
            "Accept": "application/json",
            "User-Agent": DEFAULT_USER_AGENT,
        },
        2.0,
    )]


def test_fetch_adds_default_headers_for_injected_transport() -> None:
    seen_headers: list[Mapping[str, str]] = []

    def transport(
        url: str,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> Eng01PriceFeedHttpResponse:
        seen_headers.append(headers)
        assert timeout_seconds == DEFAULT_TIMEOUT_SECONDS
        return _response(source_url=url)

    fetch_price_feed_http_response(URL, transport=transport)

    assert seen_headers == [{
        "Accept": DEFAULT_ACCEPT_HEADER,
        "User-Agent": DEFAULT_USER_AGENT,
    }]


def test_transport_rows_compose_with_adapter_and_solver() -> None:
    response = fetch_price_feed_http_response(
        URL,
        transport=lambda *_: _response(),
    )
    rows = parse_price_feed_response(
        response.body,
        content_type=response.content_type,
    )
    feed = build_eng01_price_feed(
        rows,
        fetched_at_utc="2026-01-15T20:00:00Z",
        horizon_start_utc="2026-01-16T00:00:00Z",
        horizon_hours=3,
        price_unit=PRICE_UNIT_EUR_PER_MWH,
    )

    result = recommend_top_3_cheapest_hours(feed)

    assert result.result_marker == OK
    assert result.to_payload()["top_3_cheapest_hours_utc"][0] == {
        "hour_utc": "2026-01-16T01:00:00Z",
        "price_eur_per_kwh": 0.075,
        "rank": 1,
    }


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
        "waggledance.core.v3_13_0.eng01_price_feed_http_transport."
        "httpx.Client",
        FakeClient,
    )

    response = fetch_price_feed_http_response(URL, timeout_seconds=3)

    assert captured["url"] == URL
    assert captured["headers"] == {
        "Accept": DEFAULT_ACCEPT_HEADER,
        "User-Agent": DEFAULT_USER_AGENT,
    }
    assert captured["follow_redirects"] is False
    assert response == _response(content_type="application/json")


def test_normalizes_default_transport_connect_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            return None

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def get(self, *args: object, **kwargs: object) -> httpx.Response:
            raise httpx.ConnectError("offline")

    monkeypatch.setattr(
        "waggledance.core.v3_13_0.eng01_price_feed_http_transport."
        "httpx.Client",
        FakeClient,
    )

    with pytest.raises(Eng01PriceFeedHttpTransportError,
                       match="NETWORK_CONNECT_FAILED"):
        fetch_price_feed_http_response(URL)


@pytest.mark.parametrize("url", ["", "   "])
def test_refuses_empty_url(url: str) -> None:
    with pytest.raises(Eng01PriceFeedHttpTransportError, match="URL_EMPTY"):
        fetch_price_feed_http_response(url, transport=lambda *_: _response())


@pytest.mark.parametrize("url", [
    "file:///tmp/prices.json",
    "ftp://prices.example.test/feed.json",
    "prices.example.test/feed.json",
])
def test_refuses_non_http_url_scheme(url: str) -> None:
    with pytest.raises(Eng01PriceFeedHttpTransportError,
                       match="URL_SCHEME_REFUSED"):
        fetch_price_feed_http_response(url, transport=lambda *_: _response())


def test_refuses_url_credentials() -> None:
    with pytest.raises(Eng01PriceFeedHttpTransportError,
                       match="URL_USERINFO_REFUSED"):
        fetch_price_feed_http_response(
            "https://user:pass@prices.example.test/feed.json",
            transport=lambda *_: _response(),
        )


@pytest.mark.parametrize("query", [
    "api_key=abc",
    "access_key=abc",
    "private_key=abc",
    "secrets=abc",
    "tokens=abc",
])
def test_refuses_secret_like_url_query(query: str) -> None:
    with pytest.raises(Eng01PriceFeedHttpTransportError,
                       match="URL_SECRET_REFUSED"):
        fetch_price_feed_http_response(
            f"https://prices.example.test/feed.json?{query}",
            transport=lambda *_: _response(),
        )


@pytest.mark.parametrize("url", [
    "https://localhost/feed.json",
    "https://127.0.0.1/feed.json",
    "https://[::1]/feed.json",
])
def test_refuses_loopback_or_localhost_url(url: str) -> None:
    with pytest.raises(Eng01PriceFeedHttpTransportError,
                       match="URL_LOCAL_HOST_REFUSED"):
        fetch_price_feed_http_response(url, transport=lambda *_: _response())


@pytest.mark.parametrize("url", [
    "https://10.0.0.1/feed.json",
    "https://172.16.0.5/feed.json",
    "https://192.168.1.10/feed.json",
])
def test_refuses_private_ip_url(url: str) -> None:
    with pytest.raises(Eng01PriceFeedHttpTransportError,
                       match="URL_PRIVATE_HOST_REFUSED"):
        fetch_price_feed_http_response(url, transport=lambda *_: _response())


def test_refuses_secret_like_header_name_by_default() -> None:
    with pytest.raises(Eng01PriceFeedHttpTransportError,
                       match="CREDENTIAL_HEADER_REFUSED"):
        fetch_price_feed_http_response(
            URL,
            headers={"Authorization": "Bearer abc"},
            transport=lambda *_: _response(),
        )


def test_refuses_union_secret_markers_in_header_values() -> None:
    with pytest.raises(Eng01PriceFeedHttpTransportError,
                       match="CREDENTIAL_HEADER_REFUSED"):
        fetch_price_feed_http_response(
            URL,
            headers={"X-Trace": "private_key material"},
            transport=lambda *_: _response(),
        )


def test_allows_credential_header_only_with_explicit_opt_in() -> None:
    seen_headers: list[Mapping[str, str]] = []

    def transport(
        url: str,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> Eng01PriceFeedHttpResponse:
        seen_headers.append(headers)
        return _response()

    fetch_price_feed_http_response(
        URL,
        headers={"Authorization": "Bearer abc"},
        allow_credential_headers=True,
        transport=transport,
    )

    assert seen_headers[0]["Authorization"] == "Bearer abc"


def test_refuses_header_control_characters() -> None:
    with pytest.raises(Eng01PriceFeedHttpTransportError,
                       match="HEADER_CONTROL_REFUSED"):
        fetch_price_feed_http_response(
            URL,
            headers={"X-Trace": "ok\r\nInjected: true"},
            transport=lambda *_: _response(),
        )


@pytest.mark.parametrize("timeout", [0, -1, True, "1", 61])
def test_refuses_invalid_timeout(timeout: object) -> None:
    with pytest.raises(Eng01PriceFeedHttpTransportError,
                       match="TIMEOUT_OUT_OF_RANGE"):
        fetch_price_feed_http_response(
            URL,
            timeout_seconds=timeout,
            transport=lambda *_: _response(),
        )


@pytest.mark.parametrize(
    "size_cap",
    [0, -1, True, MAX_RESPONSE_BYTES + 1],
)
def test_refuses_invalid_size_cap(size_cap: object) -> None:
    with pytest.raises(Eng01PriceFeedHttpTransportError,
                       match="SIZE_CAP_OUT_OF_RANGE"):
        fetch_price_feed_http_response(
            URL,
            max_response_bytes=size_cap,
            transport=lambda *_: _response(),
        )


def test_refuses_non_2xx_response_from_transport() -> None:
    with pytest.raises(Eng01PriceFeedHttpTransportError,
                       match="HTTP_STATUS_503"):
        fetch_price_feed_http_response(
            URL,
            transport=lambda *_: _response(status_code=503),
        )


def test_refuses_response_over_size_cap() -> None:
    with pytest.raises(Eng01PriceFeedHttpTransportError,
                       match="RESPONSE_TOO_LARGE"):
        fetch_price_feed_http_response(
            URL,
            max_response_bytes=1,
            transport=lambda *_: _response(body=b"{}"),
        )


def test_refuses_unsupported_response_content_type() -> None:
    with pytest.raises(Eng01PriceFeedHttpTransportError,
                       match="RESPONSE_CONTENT_TYPE_REFUSED"):
        fetch_price_feed_http_response(
            URL,
            transport=lambda *_: _response(content_type="text/html"),
        )


def test_refuses_transport_mismatched_source_url() -> None:
    with pytest.raises(Eng01PriceFeedHttpTransportError,
                       match="RESPONSE_SOURCE_URL_REFUSED"):
        fetch_price_feed_http_response(
            URL,
            transport=lambda *_: _response(
                source_url="https://other.example.test/feed.json"
            ),
        )


def test_refuses_invalid_body_from_transport() -> None:
    response = Eng01PriceFeedHttpResponse(
        body="not-bytes",
        content_type="application/json",
        status_code=200,
        source_url=URL,
    )
    with pytest.raises(Eng01PriceFeedHttpTransportError,
                       match="RESPONSE_BODY_REFUSED"):
        fetch_price_feed_http_response(URL, transport=lambda *_: response)
