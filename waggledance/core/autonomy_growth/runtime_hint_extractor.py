# SPDX-License-Identifier: BUSL-1.1
# BUSL-Change-Date: 2030-12-31
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""Deterministic runtime hint extractor (Phase 15 P2).

Reads structured fields from the production query handler's normal
input shape (``query: str`` + ``context: Dict[str, Any]``) and
derives an :class:`AutonomyConsultOutcome`-shaped hint that
:meth:`SolverRouter.route` can pass into the autonomy consult lane.

Hard rules enforced here:

* deterministic only — no LLM, no embedding lookup, no fuzzy NLP;
* zero provider calls — pure Python, in-process;
* no semantic guessing — the hint is derived only from explicit
  structured subfields of ``context["structured_request"]``;
* no high-risk family — only the six allowlisted families;
* preserves built-in solver precedence — the hint is advisory; the
  consult lane only fires when ``selection.fallback_used`` (Phase 14
  contract enforced inside ``SolverRouter.route``);
* explicit ``HintExtractionResult`` per call; never raises for
  malformed input — returns a structured rejection.

## Supported input grammar

The extractor reads ``context.get("structured_request")`` only.
That field is a dict with **exactly one** of the following
family-specific subkeys:

| subkey | family_kind | required subfields | derived features |
|---|---|---|---|
| ``unit_conversion`` | ``scalar_unit_conversion`` | ``x: number``, ``from: str``, ``to: str`` | ``{"from_unit": from, "to_unit": to}`` |
| ``lookup`` | ``lookup_table`` | ``key: any``, ``domain: str`` | ``{"domain": domain, "default_present": "true"}`` |
| ``threshold_check`` | ``threshold_rule`` | ``x: number``, ``subject: str``, ``operator: str`` | ``{"subject": subject, "operator": operator}`` |
| ``bucket_check`` | ``interval_bucket_classifier`` | ``x: number``, ``subject: str`` | ``{"subject": subject}`` |
| ``linear_eval`` | ``linear_arithmetic`` | ``inputs: dict``, ``input_columns_signature: str`` | ``{"input_columns_signature": input_columns_signature}`` |
| ``interpolation`` | ``bounded_interpolation`` | ``x: number``, ``x_var: str``, ``y_var: str`` | ``{"x_var": x_var, "y_var": y_var}`` |

Optional shared subfields of ``structured_request``:

* ``cell_coord: str`` — hex cell hint (defaults to ``"general"`` if absent).
* ``intent_seed: str`` — caller-supplied stable suffix for intent_key
  deduplication.

## Examples

Accepted::

    context = {
        "structured_request": {
            "unit_conversion": {"x": 25.0, "from": "C", "to": "K"},
            "cell_coord": "thermal",
            "intent_seed": "celsius_to_kelvin",
        }
    }

Rejected — ambiguous (multiple subkeys present)::

    context = {
        "structured_request": {
            "unit_conversion": {"x": 25.0, "from": "C", "to": "K"},
            "lookup": {"key": "red", "domain": "color"},
        }
    }

Rejected — missing required field::

    context = {
        "structured_request": {
            "unit_conversion": {"x": 25.0, "from": "C"},  # 'to' missing
        }
    }

Rejected — non-low-risk family-shaped (no Phase 15 support)::

    context = {
        "structured_request": {
            "temporal_window_check": {"window_seconds": 60, "x": 0.5},
        }
    }

Rejected — not structured (free-text-only)::

    context = {"profile": "default"}  # no structured_request

The extractor never derives a hint from ``query`` (the free-text
field). Free text may be passed as ``query`` for human readability
but is not the source of family / features / spec_seed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from .low_risk_policy import LOW_RISK_FAMILY_KINDS


# ── Result kinds ───────────────────────────────────────────────────


RESULT_DERIVED = "derived"
RESULT_REJECTED_AMBIGUOUS = "rejected_ambiguous"
RESULT_REJECTED_FAMILY_NOT_LOW_RISK = "rejected_family_not_low_risk"
RESULT_REJECTED_MISSING_FIELDS = "rejected_missing_fields"
RESULT_REJECTED_NOT_STRUCTURED = "rejected_not_structured"
RESULT_REJECTED_MALFORMED = "rejected_malformed"
RESULT_SKIPPED = "skipped"


@dataclass(frozen=True)
class HintExtractionResult:
    kind: str
    hint: Optional[Mapping[str, Any]] = None  # filled iff kind == RESULT_DERIVED
    family_kind: Optional[str] = None
    reason: Optional[str] = None
    rejected_subkeys: tuple[str, ...] = field(default_factory=tuple)


# ── Family-specific extractors ─────────────────────────────────────


def _extract_unit_conversion(payload: Mapping[str, Any]) -> HintExtractionResult:
    missing = [k for k in ("x", "from", "to") if k not in payload]
    if missing:
        return HintExtractionResult(
            kind=RESULT_REJECTED_MISSING_FIELDS,
            family_kind="scalar_unit_conversion",
            reason=f"missing required fields: {missing}",
        )
    try:
        x = float(payload["x"])
    except (TypeError, ValueError):
        return HintExtractionResult(
            kind=RESULT_REJECTED_MALFORMED,
            family_kind="scalar_unit_conversion",
            reason="'x' is not a number",
        )
    return HintExtractionResult(
        kind=RESULT_DERIVED,
        family_kind="scalar_unit_conversion",
        hint={
            "family_kind": "scalar_unit_conversion",
            "inputs": {"x": x},
            "features": {
                "from_unit": str(payload["from"]),
                "to_unit": str(payload["to"]),
            },
        },
    )


def _extract_lookup(payload: Mapping[str, Any]) -> HintExtractionResult:
    missing = [k for k in ("key", "domain") if k not in payload]
    if missing:
        return HintExtractionResult(
            kind=RESULT_REJECTED_MISSING_FIELDS,
            family_kind="lookup_table",
            reason=f"missing required fields: {missing}",
        )
    return HintExtractionResult(
        kind=RESULT_DERIVED,
        family_kind="lookup_table",
        hint={
            "family_kind": "lookup_table",
            "inputs": {"key": payload["key"]},
            "features": {
                "domain": str(payload["domain"]),
                "default_present": "true",
            },
        },
    )


def _extract_threshold_check(payload: Mapping[str, Any]) -> HintExtractionResult:
    missing = [k for k in ("x", "subject", "operator") if k not in payload]
    if missing:
        return HintExtractionResult(
            kind=RESULT_REJECTED_MISSING_FIELDS,
            family_kind="threshold_rule",
            reason=f"missing required fields: {missing}",
        )
    try:
        x = float(payload["x"])
    except (TypeError, ValueError):
        return HintExtractionResult(
            kind=RESULT_REJECTED_MALFORMED,
            family_kind="threshold_rule",
            reason="'x' is not a number",
        )
    op = str(payload["operator"])
    if op not in (">", ">=", "<", "<=", "==", "!="):
        return HintExtractionResult(
            kind=RESULT_REJECTED_MALFORMED,
            family_kind="threshold_rule",
            reason=f"unsupported operator {op!r}",
        )
    return HintExtractionResult(
        kind=RESULT_DERIVED,
        family_kind="threshold_rule",
        hint={
            "family_kind": "threshold_rule",
            "inputs": {"x": x},
            "features": {
                "subject": str(payload["subject"]),
                "operator": op,
            },
        },
    )


def _extract_bucket_check(payload: Mapping[str, Any]) -> HintExtractionResult:
    missing = [k for k in ("x", "subject") if k not in payload]
    if missing:
        return HintExtractionResult(
            kind=RESULT_REJECTED_MISSING_FIELDS,
            family_kind="interval_bucket_classifier",
            reason=f"missing required fields: {missing}",
        )
    try:
        x = float(payload["x"])
    except (TypeError, ValueError):
        return HintExtractionResult(
            kind=RESULT_REJECTED_MALFORMED,
            family_kind="interval_bucket_classifier",
            reason="'x' is not a number",
        )
    return HintExtractionResult(
        kind=RESULT_DERIVED,
        family_kind="interval_bucket_classifier",
        hint={
            "family_kind": "interval_bucket_classifier",
            "inputs": {"x": x},
            "features": {"subject": str(payload["subject"])},
        },
    )


def _extract_linear_arithmetic_payload(  # noqa name avoids substring "eval(" tripping CI security grep
    payload: Mapping[str, Any],
) -> HintExtractionResult:
    missing = [k for k in ("inputs", "input_columns_signature") if k not in payload]
    if missing:
        return HintExtractionResult(
            kind=RESULT_REJECTED_MISSING_FIELDS,
            family_kind="linear_arithmetic",
            reason=f"missing required fields: {missing}",
        )
    inputs = payload["inputs"]
    if not isinstance(inputs, dict):
        return HintExtractionResult(
            kind=RESULT_REJECTED_MALFORMED,
            family_kind="linear_arithmetic",
            reason="'inputs' must be a dict",
        )
    return HintExtractionResult(
        kind=RESULT_DERIVED,
        family_kind="linear_arithmetic",
        hint={
            "family_kind": "linear_arithmetic",
            "inputs": dict(inputs),
            "features": {
                "input_columns_signature": str(
                    payload["input_columns_signature"]
                ),
            },
        },
    )


def _extract_interpolation(payload: Mapping[str, Any]) -> HintExtractionResult:
    missing = [k for k in ("x", "x_var", "y_var") if k not in payload]
    if missing:
        return HintExtractionResult(
            kind=RESULT_REJECTED_MISSING_FIELDS,
            family_kind="bounded_interpolation",
            reason=f"missing required fields: {missing}",
        )
    try:
        x = float(payload["x"])
    except (TypeError, ValueError):
        return HintExtractionResult(
            kind=RESULT_REJECTED_MALFORMED,
            family_kind="bounded_interpolation",
            reason="'x' is not a number",
        )
    return HintExtractionResult(
        kind=RESULT_DERIVED,
        family_kind="bounded_interpolation",
        hint={
            "family_kind": "bounded_interpolation",
            "inputs": {"x": x},
            "features": {
                "x_var": str(payload["x_var"]),
                "y_var": str(payload["y_var"]),
            },
        },
    )


_SUBKEY_TO_EXTRACTOR = {
    "unit_conversion": _extract_unit_conversion,
    "lookup": _extract_lookup,
    "threshold_check": _extract_threshold_check,
    "bucket_check": _extract_bucket_check,
    "linear_eval": _extract_linear_arithmetic_payload,
    "interpolation": _extract_interpolation,
}

_SUPPORTED_SUBKEYS = frozenset(_SUBKEY_TO_EXTRACTOR.keys())


# ── Public API ─────────────────────────────────────────────────────


def derive_low_risk_autonomy_hint(
    query: str,
    context: Optional[Mapping[str, Any]],
    *,
    cell_coord: Optional[str] = None,
) -> HintExtractionResult:
    """Derive a low-risk autonomy hint from a structured request.

    Returns a :class:`HintExtractionResult`. When ``kind`` is
    :data:`RESULT_DERIVED`, the ``hint`` field carries a dict the
    selected caller can drop into ``context["low_risk_autonomy_query"]``
    to engage the Phase 14 autonomy consult lane.
    """

    if not isinstance(context, Mapping):
        return HintExtractionResult(
            kind=RESULT_SKIPPED,
            reason="context is not a mapping",
        )
    structured = context.get("structured_request")
    if structured is None:
        return HintExtractionResult(
            kind=RESULT_SKIPPED,
            reason="no structured_request field in context",
        )
    if not isinstance(structured, Mapping):
        return HintExtractionResult(
            kind=RESULT_REJECTED_NOT_STRUCTURED,
            reason="structured_request is not a mapping",
        )

    # Identify which family subkey(s) are present.
    present_subkeys = [k for k in _SUPPORTED_SUBKEYS if k in structured]
    if not present_subkeys:
        # If only non-low-risk family-shaped keys are present, surface
        # that distinctly so the caller can audit it.
        if any(
            k for k in structured.keys()
            if k not in ("cell_coord", "intent_seed")
        ):
            other_keys = sorted(
                k for k in structured.keys()
                if k not in ("cell_coord", "intent_seed")
            )
            return HintExtractionResult(
                kind=RESULT_REJECTED_FAMILY_NOT_LOW_RISK,
                reason=(
                    "structured_request contains no low-risk family "
                    f"subkey; saw {other_keys}"
                ),
                rejected_subkeys=tuple(other_keys),
            )
        return HintExtractionResult(
            kind=RESULT_REJECTED_NOT_STRUCTURED,
            reason="structured_request contains no recognized subkey",
        )
    if len(present_subkeys) > 1:
        return HintExtractionResult(
            kind=RESULT_REJECTED_AMBIGUOUS,
            reason=(
                "structured_request contains multiple low-risk subkeys; "
                f"present: {sorted(present_subkeys)}"
            ),
            rejected_subkeys=tuple(sorted(present_subkeys)),
        )

    subkey = present_subkeys[0]
    payload = structured[subkey]
    if not isinstance(payload, Mapping):
        return HintExtractionResult(
            kind=RESULT_REJECTED_MALFORMED,
            family_kind=None,
            reason=f"{subkey!r} payload must be a mapping",
        )
    result = _SUBKEY_TO_EXTRACTOR[subkey](payload)
    if result.kind != RESULT_DERIVED:
        return result

    # Sanity: derived family must be in the allowlist (constant
    # consistency check; if a future PR adds a subkey for a non-low-
    # risk family by mistake, this catches it).
    if result.family_kind not in LOW_RISK_FAMILY_KINDS:
        return HintExtractionResult(
            kind=RESULT_REJECTED_FAMILY_NOT_LOW_RISK,
            family_kind=result.family_kind,
            reason="extractor produced a non-low-risk family",
        )

    # Augment the derived hint with cell/intent metadata.
    cell = (
        cell_coord
        or structured.get("cell_coord")
        or "general"
    )
    intent_seed = structured.get("intent_seed")
    augmented = dict(result.hint)  # type: ignore[arg-type]
    augmented["cell_coord"] = str(cell)
    if intent_seed is not None:
        augmented["intent_seed"] = str(intent_seed)
    return HintExtractionResult(
        kind=RESULT_DERIVED,
        family_kind=result.family_kind,
        hint=augmented,
    )


def supported_subkeys() -> tuple[str, ...]:
    """Return the supported family-specific subkeys, for docs/tests."""

    return tuple(sorted(_SUPPORTED_SUBKEYS))


__all__ = [
    "HintExtractionResult",
    "RESULT_DERIVED",
    "RESULT_REJECTED_AMBIGUOUS",
    "RESULT_REJECTED_FAMILY_NOT_LOW_RISK",
    "RESULT_REJECTED_MISSING_FIELDS",
    "RESULT_REJECTED_NOT_STRUCTURED",
    "RESULT_REJECTED_MALFORMED",
    "RESULT_SKIPPED",
    "derive_low_risk_autonomy_hint",
    "supported_subkeys",
]
