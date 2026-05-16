# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""Secret marker detection helpers for sanitized metadata and transport fields."""
from __future__ import annotations

import re


_SECRET_MARKERS = (
    "access_key",
    "api_key",
    "authorization",
    "bearer",
    "cookie",
    "credential",
    "credentials",
    "passwd",
    "password",
    "private_key",
    "secret",
    "secrets",
    "token",
    "tokens",
    "x-api-key",
)
_BOUNDARY = r"[\\/._?&=:\-\s]"
_SECRET_MARKER_RE = re.compile(
    r"(?:^|" + _BOUNDARY + r")(?:"
    + "|".join(re.escape(marker) for marker in sorted(
        _SECRET_MARKERS,
        key=len,
        reverse=True,
    ))
    + r")(?:$|" + _BOUNDARY + r")",
    re.IGNORECASE,
)


def contains_secret_marker(value: str) -> bool:
    """Return True for bounded markers in metadata fields."""
    return _SECRET_MARKER_RE.search(value) is not None


def contains_secret_marker_substring(value: str) -> bool:
    """Return True for any marker substring in URL/header-like fields."""
    lowered = value.casefold()
    return any(marker in lowered for marker in _SECRET_MARKERS)


__all__ = [
    "contains_secret_marker",
    "contains_secret_marker_substring",
]
