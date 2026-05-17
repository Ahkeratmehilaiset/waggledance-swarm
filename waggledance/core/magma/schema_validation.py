# SPDX-License-Identifier: BUSL-1.1
"""Shared JSON schema validation helpers for MAGMA CLIs."""
from __future__ import annotations

from typing import Any

import jsonschema


def schema_error_path(error: jsonschema.ValidationError) -> str:
    """Return only the failing instance path, never the rejected value."""
    return ".".join(str(part) for part in error.path) or "<root>"


def redacted_schema_errors(
    validator: jsonschema.Draft7Validator,
    value: dict[str, Any],
    label: str,
) -> list[str]:
    """Format schema errors without jsonschema.error.message.

    jsonschema messages can echo rejected values, including operator payloads,
    URLs, or planted privacy canaries. MAGMA-facing CLIs should report where the
    schema failed and keep the raw value out of stdout/stderr.
    """
    return [
        f"{label}: schema error at {schema_error_path(error)}"
        for error in sorted(
            validator.iter_errors(value),
            key=lambda item: list(item.path),
        )
    ]
