# SPDX-License-Identifier: BUSL-1.1
"""Dormant, pure task-admission decision function.

This module decides *nothing about execution*. It answers one question --
"at this task boundary, may the already authorized profile simply continue?"
-- and it answers it deterministically, with zero model calls, from data the
caller supplies. It is not wired into any runtime path. Nothing here starts,
stops, switches or proposes a model.

Two invariants are structural rather than conventional, so that a future
caller cannot read authority into a result that never carried any:

* ``execution_allowed`` is ``False`` in every return value, on every path.
* ``proposed_profile`` is ``None`` in every return value, on every path.

The three verdicts follow the agreed semantics (final-plan.md section 2):

``KEEP``
    Retain the profile that is *already* authorized and healthy. KEEP grants
    no new authority; it is the absence of a change, not a permission.
``PARK``
    Stop admitting new work. This is the answer whenever anything needed for
    a safe decision is missing, unverified, held, cancelled or failed. Unknown
    is never a pass.
``ESCALATE``
    A bounded human/Lead judgment is genuinely needed. Only a *classified*
    admission-relevant failure can reach this verdict, and only once per
    decision key: a repeat of the same key returns the cached verdict with no
    new judgment. A clock tick can never produce ESCALATE.

Compatible with the existing capacity contracts: the binding tuple is
``bridge_capacity_recovery.BINDING_FIELDS`` verbatim, and the validation
helpers come from ``bridge_capacity_advisor`` so that "valid" means the same
thing here as it does there.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

try:  # package import first, mirroring bridge_capacity_recovery
    from tools.bridge_capacity_advisor import InputError, _dict, _text, _time
    from tools.bridge_capacity_recovery import BINDING_FIELDS, _valid_process_epoch
except ModuleNotFoundError:  # pragma: no cover - exercised by flat-layout callers
    from bridge_capacity_advisor import InputError, _dict, _text, _time
    from bridge_capacity_recovery import BINDING_FIELDS, _valid_process_epoch

ADMISSION_SCHEMA = "wd.task-admission-decision.v1"
REVALIDATION_SCHEMA = "wd.task-admission-revalidation.v1"

KEEP = "KEEP"
PARK = "PARK"
ESCALATE = "ESCALATE"

#: Versions that must all be pinned for a decision to be reusable. A decision
#: taken under one policy/catalog/qualification/profile/native epoch says
#: nothing about another, so the epoch set is part of the cache key.
EPOCH_FIELDS = (
    "policy_epoch",
    "catalog_epoch",
    "qualification_epoch",
    "profile_epoch",
    "native_epoch",
)

#: A failure must be classified before it can influence admission at all.
FAILURE_CLASSES = (
    "quota",
    "tool",
    "auth",
    "permission",
    "safety",
    "model_quality",
)

#: Only these two can ever justify a bounded judgment. Tool, auth, permission
#: and safety failures are diagnosis work; a profile change would hide them.
ADMISSION_RELEVANT = frozenset({"quota", "model_quality"})

#: model_quality counts only once it has actually repeated. One bad turn is
#: not evidence about a profile.
REPEATED_QUALITY_THRESHOLD = 2

#: A decision request is a small object by construction. Anything larger is
#: refused rather than truncated.
MAX_REQUEST_BYTES = 256 * 1024

UNKNOWN_FAILURE = "unknown"


def _canonical(value: Any) -> str:
    """Canonical JSON, or ``InputError``.

    Anything the caller hands us that cannot be represented exactly -- a
    datetime, a set, NaN, Infinity -- is malformed input, not a reason to
    guess. Without this, such a value escaped as a bare ``TypeError`` or
    ``ValueError`` and skipped the module's fail-closed contract.
    """
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise InputError(f"request is not canonically serialisable: {type(exc).__name__}")


def _required_mapping(value: Any, label: str) -> dict:
    if not isinstance(value, Mapping):
        raise InputError(f"{label} must be an object")
    return dict(value)


def _is_false(value: Any) -> bool:
    """Exactly the boolean False. Absent, None, 0 and "false" are all unknown."""
    return value is False


def _binding_gaps(binding: Mapping[str, Any]) -> list[str]:
    """Report every missing or malformed binding field, not just the first."""
    gaps: list[str] = []
    for field in BINDING_FIELDS:
        if field in ("native_pid", "native_process_started_at"):
            continue
        if not _text(binding.get(field)):
            gaps.append(f"binding_incomplete:{field}")
    if not _valid_process_epoch(dict(binding)):
        # A PID alone is reusable after a restart; the start epoch is what makes
        # it an identity. Recovery already enforces this and so must admission.
        gaps.append("binding_incomplete:native_process_epoch")
    return gaps


def _epoch_gaps(epochs: Mapping[str, Any]) -> list[str]:
    return [f"epoch_unknown:{name}" for name in EPOCH_FIELDS
            if not _text(epochs.get(name))]


def classify_failure(failure: Any) -> str | None:
    """Return a known class, ``None`` for "no failure", or ``"unknown"``.

    An unrecognised or malformed failure is deliberately *not* treated as
    absent. It becomes ``unknown``, which parks.
    """
    if failure is None:
        return None
    if not isinstance(failure, Mapping):
        return UNKNOWN_FAILURE
    kind = failure.get("kind")
    if isinstance(kind, str) and kind in FAILURE_CLASSES:
        return kind
    return UNKNOWN_FAILURE


def decision_key(request: Mapping[str, Any]) -> str:
    """Stable key over binding + epochs only.

    Deliberately excludes every wall-clock field. Re-observing the same task a
    second later must land on the same key, or a timer would silently buy a
    fresh judgment budget -- which is the exact behaviour the design forbids.
    """
    payload = _required_mapping(request, "request")
    binding = _required_mapping(payload.get("binding"), "request.binding")
    epochs = _required_mapping(payload.get("epochs"), "request.epochs")
    material = {
        "binding": {field: binding.get(field) for field in BINDING_FIELDS},
        "epochs": {name: epochs.get(name) for name in EPOCH_FIELDS},
    }
    return hashlib.sha256(_canonical(material).encode("utf-8")).hexdigest()


def _result(verdict: str, *, key: str, reasons: list[str], failure_class: str | None,
            judgment_requested: bool = False, suppressed: bool = False) -> dict:
    return {
        "schema": ADMISSION_SCHEMA,
        "verdict": verdict,
        "reasons": sorted(set(reasons)),
        "decision_key": key,
        "failure_class": failure_class,
        "judgment_requested": judgment_requested,
        "suppressed": suppressed,
        # Structural, not incidental: this module never spends a model call and
        # never hands back execution authority or a profile to switch to.
        "model_calls": 0,
        "execution_allowed": False,
        "proposed_profile": None,
    }


def admit(request: Any, *, judgments: Mapping[str, Any] | None = None) -> dict:
    """Decide KEEP / PARK / ESCALATE for one task boundary. Pure.

    ``judgments`` maps a previously judged ``decision_key`` to that judgment's
    verdict (or a record carrying ``verdict``). A hit returns the cached
    verdict with ``suppressed=True`` and ``judgment_requested=False``; the same
    key never buys a second judgment.

    Raises ``InputError`` only for structurally malformed input. Everything
    that is merely missing, unverified or unknown resolves to PARK.
    """
    payload = _required_mapping(request, "request")
    encoded = _canonical(payload)
    if len(encoded.encode("utf-8")) > MAX_REQUEST_BYTES:
        raise InputError("request exceeds the admission size bound")

    binding = _required_mapping(payload.get("binding"), "request.binding")
    epochs = _required_mapping(payload.get("epochs"), "request.epochs")
    key = decision_key(payload)

    reasons: list[str] = []
    reasons.extend(_binding_gaps(binding))
    reasons.extend(_epoch_gaps(epochs))

    if not _is_false(payload.get("hold")):
        reasons.append("hold_unknown_or_set")
    if not _is_false(payload.get("cancelled")):
        reasons.append("cancelled_unknown_or_set")

    owner = _dict(payload.get("owner"))
    if owner.get("verified") is not True or not _text(owner.get("principal")) \
            or not _text(owner.get("verification_ref")):
        reasons.append("owner_unverified_or_unknown")

    profile = _dict(payload.get("current_profile"))
    if not _text(profile.get("profile_id")) or profile.get("authorized") is not True \
            or not _text(profile.get("authorization_ref")):
        reasons.append("profile_not_already_authorized")

    if payload.get("ttl_expired") is True:
        # A lapsed cache entry is missing information, not a trigger. It parks;
        # it must never wake a judgment.
        reasons.append("ttl_expired_is_unknown_not_a_trigger")

    failure_class = classify_failure(payload.get("failure"))

    if reasons:
        return _result(PARK, key=key, reasons=reasons, failure_class=failure_class)

    if failure_class is not None:
        if failure_class == UNKNOWN_FAILURE:
            return _result(PARK, key=key, reasons=["failure_unclassified"],
                           failure_class=failure_class)
        if failure_class not in ADMISSION_RELEVANT:
            return _result(PARK, key=key,
                           reasons=["failure_requires_diagnosis_not_model_switch"],
                           failure_class=failure_class)
        if failure_class == "model_quality":
            observed = payload.get("quality_failures")
            if type(observed) is not int or observed < REPEATED_QUALITY_THRESHOLD:
                return _result(PARK, key=key,
                               reasons=["quality_failure_not_repeated_or_uncounted"],
                               failure_class=failure_class)
        cached = (judgments or {}).get(key)
        if cached is not None:
            verdict = cached.get("verdict") if isinstance(cached, Mapping) else cached
            if verdict not in (KEEP, PARK, ESCALATE):
                # A cache entry we cannot read is not permission to re-ask.
                return _result(PARK, key=key, reasons=["cached_judgment_unreadable"],
                               failure_class=failure_class, suppressed=True)
            if verdict == KEEP:
                # A prior judgment cannot override an observed current failure.
                return _result(PARK, key=key, reasons=["current_failure_overrides_cached_keep"],
                               failure_class=failure_class, suppressed=True)
            return _result(verdict, key=key, reasons=["judgment_already_taken_for_key"],
                           failure_class=failure_class, suppressed=True)
        return _result(ESCALATE, key=key, reasons=["classified_admission_relevant_failure"],
                       failure_class=failure_class, judgment_requested=True)

    if profile.get("healthy") is not True:
        return _result(PARK, key=key, reasons=["current_work_not_observed_healthy"],
                       failure_class=None)

    return _result(KEEP, key=key, reasons=["authorized_healthy_profile_retained"],
                   failure_class=None)


def revalidate(decision: Any, observed: Any) -> dict:
    """Re-check the volatile facts immediately before acting on a decision.

    A cached KEEP is not a licence. HOLD, cancellation, claim and owner can all
    change after admission, so they are checked again here and block on any
    change or any unknown. Binding drift blocks too: a decision belongs to the
    exact binding it was taken under.
    """
    record = _required_mapping(decision, "decision")
    current = _required_mapping(observed, "observed")
    reasons: list[str] = []

    if record.get("schema") != ADMISSION_SCHEMA:
        reasons.append("decision_schema_unrecognised")
    if record.get("verdict") not in (KEEP, PARK, ESCALATE):
        reasons.append("decision_verdict_unrecognised")
    if record.get("verdict") != KEEP:
        reasons.append("only_a_keep_decision_is_actionable")

    # Repeat the complete current health/authorization validation, not merely
    # the stable binding. Same identity does not imply the work is still safe.
    try:
        fresh = admit(current)
        if fresh["verdict"] != KEEP:
            reasons.append("current_observation_does_not_allow_keep")
    except InputError:
        reasons.append("current_observation_invalid")

    if not _is_false(current.get("hold")):
        reasons.append("hold_unknown_or_set_at_execution")
    if not _is_false(current.get("cancelled")):
        reasons.append("cancelled_unknown_or_set_at_execution")

    owner = _dict(current.get("owner"))
    if owner.get("verified") is not True or not _text(owner.get("principal")):
        reasons.append("owner_unverified_at_execution")

    expected_key = record.get("decision_key")
    if not _text(expected_key):
        reasons.append("decision_key_missing")
    else:
        binding = _dict(current.get("binding"))
        epochs = _dict(current.get("epochs"))
        try:
            observed_key = decision_key({"binding": binding, "epochs": epochs})
        except InputError:
            observed_key = None
        if observed_key != expected_key:
            reasons.append("binding_or_epoch_changed_since_admission")

    return {
        "schema": REVALIDATION_SCHEMA,
        "revalidated": not reasons,
        "reasons": sorted(set(reasons)),
        # Even a clean revalidation grants nothing. Acting is a separate,
        # separately authorized step that this module does not model.
        "execution_allowed": False,
    }


def observed_at(value: Any):
    """Parse an observation stamp with the shared rule (naive is refused)."""
    return _time(value)
