#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Pure append-only validation for bounded soak JSONL snapshots.

This module proves only that a valid current JSONL byte stream preserves a
valid subject stream as its exact normalized byte prefix.  It deliberately
does not claim freshness, duration, timestamps, source identity, Git ancestry,
or runtime-process identity; those properties belong to separate evidence.
"""

from __future__ import annotations

import json


MAX_SOAK_APPEND_BYTES = 16 * 1024 * 1024
_UTF8_BOM = b"\xef\xbb\xbf"


def _reject_json_constant(value: str) -> None:
    """Reject NaN and infinities, which are not JSON values."""

    raise ValueError(f"non-standard JSON constant: {value}")


def _validated_stream(
    value: object,
    *,
    role: str,
    allow_empty: bool,
) -> tuple[bytes | None, list[str]]:
    prefix = f"soak_append_{role}"
    if type(value) is not bytes:
        return None, [f"{prefix}_type_invalid"]
    if len(value) > MAX_SOAK_APPEND_BYTES:
        return None, [f"{prefix}_too_large"]
    if not value:
        if allow_empty:
            return b"", []
        return None, [f"{prefix}_empty"]

    normalized = value.replace(b"\r\n", b"\n")
    if b"\r" in normalized:
        return None, [f"{prefix}_bare_cr"]
    if normalized.startswith(_UTF8_BOM):
        return None, [f"{prefix}_bom"]
    try:
        text = normalized.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None, [f"{prefix}_utf8_invalid"]
    if not normalized.endswith(b"\n"):
        return None, [f"{prefix}_final_lf_missing"]

    # The final split item is the required terminator after the last record.
    for line in text.split("\n")[:-1]:
        if not line.strip():
            return None, [f"{prefix}_blank_line"]
        try:
            record = json.loads(line, parse_constant=_reject_json_constant)
        except (json.JSONDecodeError, RecursionError, ValueError):
            return None, [f"{prefix}_json_invalid"]
        if not isinstance(record, dict):
            return None, [f"{prefix}_record_not_object"]
    return normalized, []


def evaluate_soak_append_only(subject: bytes, current: bytes) -> list[str]:
    """Return stable blockers for a bounded append-only JSONL comparison.

    ``b""`` is a valid, explicitly supplied empty subject.  The current stream
    must always contain at least one complete JSON object record.  CRLF is
    folded to LF before the exact byte-prefix comparison; a bare CR is invalid.
    An unchanged valid nonempty stream satisfies the append-only property.
    """

    subject_normalized, subject_blockers = _validated_stream(
        subject,
        role="subject",
        allow_empty=True,
    )
    current_normalized, current_blockers = _validated_stream(
        current,
        role="current",
        allow_empty=False,
    )
    blockers = subject_blockers + current_blockers
    if blockers:
        return blockers
    if not current_normalized.startswith(subject_normalized):
        return ["soak_append_prefix_mismatch"]
    return []
