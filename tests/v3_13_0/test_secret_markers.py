# SPDX-License-Identifier: BUSL-1.1
"""Tests for shared v3.13.0 secret marker detection."""
from __future__ import annotations

import pytest

from waggledance.core.v3_13_0.secret_markers import (
    contains_secret_marker,
    contains_secret_marker_substring,
)


@pytest.mark.parametrize(
    "value",
    [
        "tokenized parser output",
        "authorized advisory text",
        "credentialed review flow",
        "private-keyword remains ordinary text",
    ],
)
def test_contains_secret_marker_allows_substrings_inside_words(value: str) -> None:
    assert contains_secret_marker(value) is False


@pytest.mark.parametrize(
    "value",
    [
        "token",
        "invoice_token.pdf",
        "relative/secrets.json",
        "credential:operator-session",
        "metadata with x-api-key marker",
        "metadata with x_api_key marker",
        "contains api_key field",
        "contains api-key field",
        "query access-key=abc",
        "source has private-key material",
        "private_key",
        "Bearer session",
    ],
)
def test_contains_secret_marker_rejects_bounded_markers(value: str) -> None:
    assert contains_secret_marker(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "tokenized parser output",
        "authorizationized advisory text",
        "private_keyed value",
    ],
)
def test_contains_secret_marker_substring_allows_substrings_inside_words(
    value: str,
) -> None:
    assert contains_secret_marker(value) is False
    assert contains_secret_marker_substring(value) is False


@pytest.mark.parametrize(
    "value",
    [
        "query access_key=abc",
        "query access-key=abc",
        "source has private_key material",
        "source has private-key material",
        "relative/secrets.json",
        "tokens=abc",
        "x_api_key header alias",
    ],
)
def test_contains_secret_marker_substring_uses_union_markers(value: str) -> None:
    assert contains_secret_marker_substring(value) is True
