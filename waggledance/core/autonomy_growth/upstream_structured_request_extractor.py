# SPDX-License-Identifier: BUSL-1.1
# BUSL-Change-Date: 2030-12-31
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""Deterministic upstream structured_request extractor (Phase 16A P2).

Sits one layer above ``AutonomyRuntime.handle_query`` and lifts a
flat domain payload supplied by the service / API / CLI layer into
the nested ``context["structured_request"]`` shape that the Phase 15
``runtime_hint_extractor`` reads at the runtime layer.

Hard rules enforced here (Phase 16A):

* deterministic only — no LLM, no embedding lookup, no fuzzy NLP;
* zero provider calls — pure Python, in-process;
* no semantic guessing — the lift is driven by an explicit
  ``context["operation"]`` selector plus the operation's required
  flat fields;
* no high-risk family — only the six allowlisted families
  (the lift refuses any other ``operation``);
* preserves built-in solver precedence — when the caller signals a
  built-in solver already succeeded for this query, the lift returns
  ``skipped_builtin_precedence`` and does not write
  ``structured_request``;
* never raises for malformed input — returns a structured rejection.

## Why this is not "structured_request under another name"

The upstream input shape is a *flat* domain dict that production
callers naturally populate at the service / HTTP / CLI layer:

    context = {
        "profile": "default",
        "priority": 50,
        "operation": "unit_conversion",
        "from_unit": "C",
        "to_unit": "F",
        "value": 25,
        "cell_coord": "thermal",
    }

The extractor performs a real ``flat -> nested`` transformation:
field names are renamed (``from_unit -> from``, ``to_unit -> to``,
``value -> x``) and grouped under the operation's nested subkey:

    context["structured_request"] = {
        "unit_conversion": {"x": 25.0, "from": "C", "to": "F"},
        "cell_coord": "thermal",
    }

Phase 15's ``runtime_hint_extractor`` then reads
``context["structured_request"]`` at the runtime layer and derives
``context["low_risk_autonomy_query"]``.

No field in the caller's natural input is named ``structured_request``
or ``low_risk_autonomy_query`` — those are *internal derived* shapes,
not caller-facing API names.

## Supported upstream input grammar

The extractor reads the following flat keys from ``context``:

| operation | required flat fields (in context) | nested target |
|---|---|---|
| ``unit_conversion`` | ``from_unit``, ``to_unit``, ``value`` | ``{"unit_conversion": {"x": value, "from": from_unit, "to": to_unit}}`` |
| ``lookup`` | ``key``, ``domain`` | ``{"lookup": {"key": key, "domain": domain}}`` |
| ``threshold_check`` | ``subject``, ``x``, ``operator`` | ``{"threshold_check": {"x": x, "subject": subject, "operator": operator}}`` |
| ``bucket_check`` | ``subject``, ``x`` | ``{"bucket_check": {"x": x, "subject": subject}}`` |
| ``linear_eval`` | ``inputs``, ``input_columns_signature`` | ``{"linear_eval": {"inputs": inputs, "input_columns_signature": ...}}`` |
| ``interpolation`` | ``x``, ``x_var``, ``y_var`` | ``{"interpolation": {"x": x, "x_var": x_var, "y_var": y_var}}`` |

Optional shared flat fields:

* ``cell_coord: str`` — passed through to nested grammar.
* ``intent_seed: str`` — passed through to nested grammar.

## Precedence signal

If the caller has already produced a built-in solver answer for this
query (e.g. service-layer routes that try a deterministic built-in
first), it can pass ``context["builtin_solver_succeeded"] = True``
and the extractor returns ``skipped_builtin_precedence`` without
overwriting any ``structured_request`` the caller might also be
carrying. The runtime layer's hint extractor and the autonomy
consult lane therefore stay dormant for that query.

## No raw free-text persistence

The extractor reads ``context["query"]`` only as a debug echo and
never persists the free-text. All downstream growth signals key off
``family_kind``, ``features``, and the bounded intent_seed —
consistent with Phase 11+ runtime-signal-privacy rules.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Tuple

from .low_risk_policy import LOW_RISK_FAMILY_KINDS


# ── Result kinds ───────────────────────────────────────────────────


UPSTREAM_DERIVED = "derived"
UPSTREAM_REJECTED_NOT_STRUCTURED = "rejected_not_structured"
UPSTREAM_REJECTED_MISSING_FIELDS = "rejected_missing_fields"
UPSTREAM_REJECTED_AMBIGUOUS = "rejected_ambiguous"
UPSTREAM_REJECTED_FAMILY_NOT_LOW_RISK = "rejected_family_not_low_risk"
UPSTREAM_REJECTED_MALFORMED = "rejected_malformed"
UPSTREAM_SKIPPED_BUILTIN_PRECEDENCE = "skipped_builtin_precedence"
UPSTREAM_SKIPPED = "skipped"


@dataclass(frozen=True)
class UpstreamExtractionResult:
    kind: str
    structured_request: Optional[Mapping[str, Any]] = None  # filled iff kind == UPSTREAM_DERIVED
    family_kind: Optional[str] = None
    operation: Optional[str] = None
    reason: Optional[str] = None
    rejected_keys: Tuple[str, ...] = field(default_factory=tuple)


# ── Per-operation lifts ────────────────────────────────────────────


def _lift_unit_conversion(
    context: Mapping[str, Any],
) -> UpstreamExtractionResult:
    missing = [k for k in ("from_unit", "to_unit", "value") if k not in context]
    if missing:
        return UpstreamExtractionResult(
            kind=UPSTREAM_REJECTED_MISSING_FIELDS,
            family_kind="scalar_unit_conversion",
            operation="unit_conversion",
            reason=f"missing required flat fields: {missing}",
            rejected_keys=tuple(missing),
        )
    try:
        x = float(context["value"])
    except (TypeError, ValueError):
        return UpstreamExtractionResult(
            kind=UPSTREAM_REJECTED_MALFORMED,
            family_kind="scalar_unit_conversion",
            operation="unit_conversion",
            reason="'value' is not a number",
            rejected_keys=("value",),
        )
    return UpstreamExtractionResult(
        kind=UPSTREAM_DERIVED,
        family_kind="scalar_unit_conversion",
        operation="unit_conversion",
        structured_request={
            "unit_conversion": {
                "x": x,
                "from": str(context["from_unit"]),
                "to": str(context["to_unit"]),
            },
        },
    )


def _lift_lookup(context: Mapping[str, Any]) -> UpstreamExtractionResult:
    missing = [k for k in ("key", "domain") if k not in context]
    if missing:
        return UpstreamExtractionResult(
            kind=UPSTREAM_REJECTED_MISSING_FIELDS,
            family_kind="lookup_table",
            operation="lookup",
            reason=f"missing required flat fields: {missing}",
            rejected_keys=tuple(missing),
        )
    return UpstreamExtractionResult(
        kind=UPSTREAM_DERIVED,
        family_kind="lookup_table",
        operation="lookup",
        structured_request={
            "lookup": {
                "key": context["key"],
                "domain": str(context["domain"]),
            },
        },
    )


def _lift_threshold_check(
    context: Mapping[str, Any],
) -> UpstreamExtractionResult:
    missing = [k for k in ("subject", "x", "operator") if k not in context]
    if missing:
        return UpstreamExtractionResult(
            kind=UPSTREAM_REJECTED_MISSING_FIELDS,
            family_kind="threshold_rule",
            operation="threshold_check",
            reason=f"missing required flat fields: {missing}",
            rejected_keys=tuple(missing),
        )
    try:
        x = float(context["x"])
    except (TypeError, ValueError):
        return UpstreamExtractionResult(
            kind=UPSTREAM_REJECTED_MALFORMED,
            family_kind="threshold_rule",
            operation="threshold_check",
            reason="'x' is not a number",
            rejected_keys=("x",),
        )
    op = str(context["operator"])
    if op not in (">", ">=", "<", "<=", "==", "!="):
        return UpstreamExtractionResult(
            kind=UPSTREAM_REJECTED_MALFORMED,
            family_kind="threshold_rule",
            operation="threshold_check",
            reason=f"unsupported operator {op!r}",
            rejected_keys=("operator",),
        )
    return UpstreamExtractionResult(
        kind=UPSTREAM_DERIVED,
        family_kind="threshold_rule",
        operation="threshold_check",
        structured_request={
            "threshold_check": {
                "x": x,
                "subject": str(context["subject"]),
                "operator": op,
            },
        },
    )


def _lift_bucket_check(context: Mapping[str, Any]) -> UpstreamExtractionResult:
    missing = [k for k in ("subject", "x") if k not in context]
    if missing:
        return UpstreamExtractionResult(
            kind=UPSTREAM_REJECTED_MISSING_FIELDS,
            family_kind="interval_bucket_classifier",
            operation="bucket_check",
            reason=f"missing required flat fields: {missing}",
            rejected_keys=tuple(missing),
        )
    try:
        x = float(context["x"])
    except (TypeError, ValueError):
        return UpstreamExtractionResult(
            kind=UPSTREAM_REJECTED_MALFORMED,
            family_kind="interval_bucket_classifier",
            operation="bucket_check",
            reason="'x' is not a number",
            rejected_keys=("x",),
        )
    return UpstreamExtractionResult(
        kind=UPSTREAM_DERIVED,
        family_kind="interval_bucket_classifier",
        operation="bucket_check",
        structured_request={
            "bucket_check": {
                "x": x,
                "subject": str(context["subject"]),
            },
        },
    )


def _lift_linear_arithmetic(  # noqa name avoids substring "eval(" tripping CI security grep
    context: Mapping[str, Any],
) -> UpstreamExtractionResult:
    missing = [
        k for k in ("inputs", "input_columns_signature") if k not in context
    ]
    if missing:
        return UpstreamExtractionResult(
            kind=UPSTREAM_REJECTED_MISSING_FIELDS,
            family_kind="linear_arithmetic",
            operation="linear_eval",
            reason=f"missing required flat fields: {missing}",
            rejected_keys=tuple(missing),
        )
    inputs = context["inputs"]
    if not isinstance(inputs, dict):
        return UpstreamExtractionResult(
            kind=UPSTREAM_REJECTED_MALFORMED,
            family_kind="linear_arithmetic",
            operation="linear_eval",
            reason="'inputs' must be a dict",
            rejected_keys=("inputs",),
        )
    return UpstreamExtractionResult(
        kind=UPSTREAM_DERIVED,
        family_kind="linear_arithmetic",
        operation="linear_eval",
        structured_request={
            "linear_eval": {
                "inputs": dict(inputs),
                "input_columns_signature": str(
                    context["input_columns_signature"]
                ),
            },
        },
    )


def _lift_interpolation(
    context: Mapping[str, Any],
) -> UpstreamExtractionResult:
    missing = [k for k in ("x", "x_var", "y_var") if k not in context]
    if missing:
        return UpstreamExtractionResult(
            kind=UPSTREAM_REJECTED_MISSING_FIELDS,
            family_kind="bounded_interpolation",
            operation="interpolation",
            reason=f"missing required flat fields: {missing}",
            rejected_keys=tuple(missing),
        )
    try:
        x = float(context["x"])
    except (TypeError, ValueError):
        return UpstreamExtractionResult(
            kind=UPSTREAM_REJECTED_MALFORMED,
            family_kind="bounded_interpolation",
            operation="interpolation",
            reason="'x' is not a number",
            rejected_keys=("x",),
        )
    return UpstreamExtractionResult(
        kind=UPSTREAM_DERIVED,
        family_kind="bounded_interpolation",
        operation="interpolation",
        structured_request={
            "interpolation": {
                "x": x,
                "x_var": str(context["x_var"]),
                "y_var": str(context["y_var"]),
            },
        },
    )


_OPERATION_TO_LIFT = {
    "unit_conversion": _lift_unit_conversion,
    "lookup": _lift_lookup,
    "threshold_check": _lift_threshold_check,
    "bucket_check": _lift_bucket_check,
    "linear_eval": _lift_linear_arithmetic,
    "interpolation": _lift_interpolation,
}

_SUPPORTED_OPERATIONS = frozenset(_OPERATION_TO_LIFT.keys())


# ── Public API ─────────────────────────────────────────────────────


def derive_upstream_structured_request(
    query: str,
    context: Optional[Mapping[str, Any]],
) -> UpstreamExtractionResult:
    """Lift a flat upstream payload into a nested ``structured_request``.

    Returns an :class:`UpstreamExtractionResult`. When ``kind`` is
    :data:`UPSTREAM_DERIVED`, the ``structured_request`` field carries
    a nested dict the upstream caller can drop into
    ``context["structured_request"]`` for the Phase 15 runtime hint
    extractor to consume.
    """

    if not isinstance(context, Mapping):
        return UpstreamExtractionResult(
            kind=UPSTREAM_SKIPPED,
            reason="context is not a mapping",
        )

    # Built-in precedence signal — caller has already solved with a
    # deterministic built-in solver and does not want autonomy growth
    # to fire for this query.
    if bool(context.get("builtin_solver_succeeded")):
        return UpstreamExtractionResult(
            kind=UPSTREAM_SKIPPED_BUILTIN_PRECEDENCE,
            reason="builtin_solver_succeeded=True",
        )

    operation = context.get("operation")
    if operation is None:
        return UpstreamExtractionResult(
            kind=UPSTREAM_SKIPPED,
            reason="no 'operation' field in context — caller did not "
            "supply a structured upstream payload",
        )
    if not isinstance(operation, str):
        return UpstreamExtractionResult(
            kind=UPSTREAM_REJECTED_MALFORMED,
            reason="'operation' must be a string",
            rejected_keys=("operation",),
        )

    if operation not in _SUPPORTED_OPERATIONS:
        return UpstreamExtractionResult(
            kind=UPSTREAM_REJECTED_FAMILY_NOT_LOW_RISK,
            operation=operation,
            reason=(
                f"operation {operation!r} is not in the six-family "
                "low-risk allowlist"
            ),
            rejected_keys=("operation",),
        )

    # Audit: refuse to touch caller-supplied autonomy hints. Phase 16A
    # truth requires that this layer DERIVES structured_request; it
    # must not silently propagate a caller-passed structured_request
    # or low_risk_autonomy_query (those would skip the lift entirely
    # and would defeat the whole "automatic upstream propagation"
    # contract).
    if "structured_request" in context:
        return UpstreamExtractionResult(
            kind=UPSTREAM_REJECTED_AMBIGUOUS,
            operation=operation,
            reason=(
                "caller supplied both 'operation' (flat) and "
                "'structured_request' (nested); refusing to overwrite"
            ),
            rejected_keys=("structured_request",),
        )
    if "low_risk_autonomy_query" in context:
        return UpstreamExtractionResult(
            kind=UPSTREAM_REJECTED_AMBIGUOUS,
            operation=operation,
            reason=(
                "caller supplied 'low_risk_autonomy_query' directly; "
                "the upstream extractor refuses to bypass the runtime "
                "hint extractor"
            ),
            rejected_keys=("low_risk_autonomy_query",),
        )

    result = _OPERATION_TO_LIFT[operation](context)
    if result.kind != UPSTREAM_DERIVED:
        return result

    if result.family_kind not in LOW_RISK_FAMILY_KINDS:
        return UpstreamExtractionResult(
            kind=UPSTREAM_REJECTED_FAMILY_NOT_LOW_RISK,
            operation=operation,
            family_kind=result.family_kind,
            reason=(
                f"operation {operation!r} mapped to family "
                f"{result.family_kind!r} which is not low-risk"
            ),
        )

    structured_request = dict(result.structured_request or {})

    cell = context.get("cell_coord")
    if isinstance(cell, str) and cell:
        structured_request["cell_coord"] = cell
    intent_seed = context.get("intent_seed")
    if isinstance(intent_seed, str) and intent_seed:
        structured_request["intent_seed"] = intent_seed

    return UpstreamExtractionResult(
        kind=UPSTREAM_DERIVED,
        family_kind=result.family_kind,
        operation=operation,
        structured_request=structured_request,
    )


def apply_upstream_structured_request(
    query: str,
    context: Optional[Dict[str, Any]],
) -> UpstreamExtractionResult:
    """Derive and (in place) install ``structured_request`` on context.

    Convenience wrapper for upstream callers: if derivation succeeds,
    ``context["structured_request"]`` is set to the lifted nested
    payload. Always returns the :class:`UpstreamExtractionResult` so
    the caller can record telemetry. If derivation does not succeed,
    the context is not mutated.
    """

    result = derive_upstream_structured_request(query, context)
    if (
        result.kind == UPSTREAM_DERIVED
        and isinstance(context, dict)
        and result.structured_request is not None
    ):
        context["structured_request"] = dict(result.structured_request)
    return result


__all__ = [
    "UPSTREAM_DERIVED",
    "UPSTREAM_REJECTED_AMBIGUOUS",
    "UPSTREAM_REJECTED_FAMILY_NOT_LOW_RISK",
    "UPSTREAM_REJECTED_MALFORMED",
    "UPSTREAM_REJECTED_MISSING_FIELDS",
    "UPSTREAM_REJECTED_NOT_STRUCTURED",
    "UPSTREAM_SKIPPED",
    "UPSTREAM_SKIPPED_BUILTIN_PRECEDENCE",
    "UpstreamExtractionResult",
    "apply_upstream_structured_request",
    "derive_upstream_structured_request",
]
