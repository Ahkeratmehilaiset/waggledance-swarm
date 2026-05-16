# SPDX-License-Identifier: BUSL-1.1
"""Tests for shared v3.13.0 secret marker detection."""
from __future__ import annotations

import pytest

from waggledance.core.v3_13_0.secret_markers import contains_secret_marker


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
        "contains api_key field",
        "private_key",
        "Bearer session",
    ],
)
def test_contains_secret_marker_rejects_bounded_markers(value: str) -> None:
    assert contains_secret_marker(value) is True
