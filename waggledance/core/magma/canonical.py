# SPDX-License-Identifier: BUSL-1.1
"""MAGMA canonical JSON helpers.

This is ``magma-jcs-subset-v1``, not a full RFC 8785 implementation:
keys are sorted, whitespace is removed, non-ASCII is escaped, and NaN /
Infinity are rejected. Producer and verifier code must use this module until
a future schema version introduces an explicit canonicalization version.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json_bytes(value: Any) -> bytes:
    """Return deterministic JSON bytes for MAGMA v1 digests."""
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_digest(value: Any) -> str:
    """Return a ``sha256:<hex>`` digest over MAGMA canonical JSON bytes."""
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()
