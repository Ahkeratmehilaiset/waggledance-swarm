# SPDX-License-Identifier: BUSL-1.1
"""Read-only runtime mirror for capability activation selections.

The mirror measures whether a production-routed capability family appears in
an independently supplied active selection.  It never filters the route,
grants execution permission, or authenticates the supplied current-head
claims.  A production snapshot provider must obtain all six current pointers
from one authenticated, transactionally stable control-plane read; copying the
expected values from the expression context would merely let stale state
certify itself.

Raw query, context, input, output, and capability identifiers are never emitted
in records.  Only the routed capability digest, current-head digests, and the
matched immutable variant digest are retained.
"""

from __future__ import annotations

import re
from typing import Optional

from waggledance.core.capabilities.activation_contracts import (
    ACTIVATION_HEAD_KEYS,
    AUTHORITY_CEILING_KEYS,
    CAPABILITY_VARIANT_KEYS,
    EXPRESSION_CONTEXT_KEYS,
    MAX_AUTHORITY_SCOPES,
    MAX_VARIANTS_PER_SET,
    verify_activation_selection,
)
from waggledance.core.magma.canonical import sha256_digest

SNAPSHOT_SCHEMA = "wd.activation_snapshot.v1"
MIRROR_RECORD_SCHEMA = "wd.activation_mirror_record.v1"
MIRROR_REPORT_SCHEMA = "wd.activation_mirror_report.v1"

RECORD_DIGEST_DOMAIN = "wd.activation_mirror_record.digest.v1"
REPORT_DIGEST_DOMAIN = "wd.activation_mirror_report.digest.v1"
ROUTED_CAPABILITY_DIGEST_DOMAIN = "wd.routed_capability.digest.v1"

MAX_MIRROR_RECORDS = 256
MAX_CAPABILITY_ID_LENGTH = 256

ACTIVE_MATCH = "active_match"
ACTIVE_MISS = "active_miss"
SELECTION_INVALID = "selection_invalid"
CLASSIFICATIONS = (ACTIVE_MATCH, ACTIVE_MISS, SELECTION_INVALID)

NO_ACTIVE_FAMILY = "no_active_family"
SNAPSHOT_INVALID = "snapshot_invalid"
SELECTION_VERIFICATION_FAILED = "selection_verification_failed"
FAILURE_CODES = frozenset(
    {NO_ACTIVE_FAMILY, SNAPSHOT_INVALID, SELECTION_VERIFICATION_FAILED}
)

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")

_AUTHORITY_FLAGS = {
    "runtime_authority_granted": False,
    "routing_influence_applied": False,
    "production_decision_unchanged": True,
    "execution_permission_granted": False,
}

SNAPSHOT_KEYS = frozenset(
    {
        "schema_version",
        "head",
        "expected_activation_head_digest",
        "context",
        "variants",
        "variant_ceilings",
        "charter_ceiling",
        "expressed_ceiling",
        "expected_profile_head_digest",
        "expected_policy_head_digest",
        "expected_resource_head_digest",
        "expected_domain_head_digest",
        "expected_environment_head_digest",
    }
)

_HEAD_FIELDS = (
    "activation_head_digest",
    "profile_head_digest",
    "policy_head_digest",
    "resource_head_digest",
    "domain_head_digest",
    "environment_head_digest",
)

RECORD_CORE_KEYS = frozenset(
    {
        "schema_version",
        "routed_capability_digest",
        *_HEAD_FIELDS,
        "selection_valid",
        "active_family_match",
        "matched_variant_digest",
        "classification",
        "failure_code",
        *_AUTHORITY_FLAGS,
    }
)
RECORD_KEYS = RECORD_CORE_KEYS | {"record_digest"}

REPORT_CORE_KEYS = frozenset(
    {
        "schema_version",
        "measurement_scope",
        "sample_count",
        "selection_valid_count",
        "selection_invalid_count",
        "active_match_count",
        "active_miss_count",
        "by_classification",
        "record_digests",
        *_AUTHORITY_FLAGS,
    }
)
REPORT_KEYS = REPORT_CORE_KEYS | {"report_digest"}


class ActivationMirrorError(ValueError):
    """A mirror input or artifact violates the authority-free contract."""


def _exact_dict(value: object, *, maximum_keys: int, label: str) -> dict:
    if type(value) is not dict:
        raise ActivationMirrorError(f"{label} must be an exact dict")
    if dict.__len__(value) > maximum_keys:
        raise ActivationMirrorError(f"{label} keyset")
    snapshot = value.copy()
    if any(type(key) is not str for key in snapshot):
        raise ActivationMirrorError(f"{label} keys must be exact strings")
    return snapshot


def _exact_list(value: object, *, maximum: int, label: str) -> list:
    if type(value) is not list:
        raise ActivationMirrorError(f"{label} must be an exact list")
    if list.__len__(value) > maximum:
        raise ActivationMirrorError(f"{label} exceeds bound")
    return value.copy()


def _snapshot_ceiling(value: object, label: str) -> dict:
    ceiling = _exact_dict(
        value,
        maximum_keys=len(AUTHORITY_CEILING_KEYS),
        label=label,
    )
    if set(ceiling) != AUTHORITY_CEILING_KEYS:
        raise ActivationMirrorError(f"{label} keyset")
    ceiling["authority_scope_digests"] = _exact_list(
        ceiling["authority_scope_digests"],
        maximum=MAX_AUTHORITY_SCOPES,
        label=f"{label}.authority_scope_digests",
    )
    return ceiling


def _stable_selection_snapshot(supplied: dict) -> dict:
    """Take one bounded private snapshot without hostile copy protocols."""

    stable = dict(supplied)
    head = _exact_dict(
        stable["head"],
        maximum_keys=len(ACTIVATION_HEAD_KEYS),
        label="activation snapshot.head",
    )
    if set(head) != ACTIVATION_HEAD_KEYS:
        raise ActivationMirrorError("activation snapshot.head keyset")
    head["active_variant_digests"] = _exact_list(
        head["active_variant_digests"],
        maximum=MAX_VARIANTS_PER_SET,
        label="activation snapshot.head.active_variant_digests",
    )
    head["shadow_variant_digests"] = _exact_list(
        head["shadow_variant_digests"],
        maximum=MAX_VARIANTS_PER_SET,
        label="activation snapshot.head.shadow_variant_digests",
    )
    stable["head"] = head

    context = _exact_dict(
        stable["context"],
        maximum_keys=len(EXPRESSION_CONTEXT_KEYS),
        label="activation snapshot.context",
    )
    if set(context) != EXPRESSION_CONTEXT_KEYS:
        raise ActivationMirrorError("activation snapshot.context keyset")
    stable["context"] = context

    raw_variants = _exact_list(
        stable["variants"],
        maximum=MAX_VARIANTS_PER_SET * 2,
        label="activation snapshot.variants",
    )
    variants: list[dict] = []
    for index, raw in enumerate(raw_variants):
        variant = _exact_dict(
            raw,
            maximum_keys=len(CAPABILITY_VARIANT_KEYS),
            label=f"activation snapshot.variants[{index}]",
        )
        if set(variant) != CAPABILITY_VARIANT_KEYS:
            raise ActivationMirrorError(
                f"activation snapshot.variants[{index}] keyset"
            )
        variants.append(variant)
    stable["variants"] = variants

    raw_ceilings = _exact_list(
        stable["variant_ceilings"],
        maximum=MAX_VARIANTS_PER_SET * 2,
        label="activation snapshot.variant_ceilings",
    )
    stable["variant_ceilings"] = [
        _snapshot_ceiling(
            raw, f"activation snapshot.variant_ceilings[{index}]"
        )
        for index, raw in enumerate(raw_ceilings)
    ]
    stable["charter_ceiling"] = _snapshot_ceiling(
        stable["charter_ceiling"], "activation snapshot.charter_ceiling"
    )
    stable["expressed_ceiling"] = _snapshot_ceiling(
        stable["expressed_ceiling"], "activation snapshot.expressed_ceiling"
    )
    return stable


def _digest(
    value: object, label: str, *, optional: bool = False
) -> Optional[str]:
    if optional and value is None:
        return None
    if type(value) is not str or not _SHA256.fullmatch(value):
        raise ActivationMirrorError(f"{label} must be a sha256 digest")
    return value


def _exact_bool(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise ActivationMirrorError(f"{label} must be an exact bool")
    return value


def _bounded_count(value: object, label: str, *, maximum: int) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise ActivationMirrorError(f"{label} must be a bounded exact int")
    return value


def _routed_capability_digest(capability_id: object) -> str:
    if (
        type(capability_id) is not str
        or not capability_id
        or len(capability_id) > MAX_CAPABILITY_ID_LENGTH
    ):
        raise ActivationMirrorError("capability_id refused")
    return sha256_digest(
        {
            "domain": ROUTED_CAPABILITY_DIGEST_DOMAIN,
            "capability_id": capability_id,
        }
    )


def _optional_snapshot_digest(snapshot: dict, name: str) -> Optional[str]:
    value = snapshot.get(name)
    if type(value) is str and _SHA256.fullmatch(value):
        return value
    return None


def _record_core(
    *,
    routed_capability_digest: str,
    snapshot: Optional[dict],
    selection_valid: bool,
    active_family_match: bool,
    matched_variant_digest: Optional[str],
    classification: str,
    failure_code: Optional[str],
) -> dict[str, object]:
    heads: dict[str, Optional[str]] = {
        "activation_head_digest": None,
        "profile_head_digest": None,
        "policy_head_digest": None,
        "resource_head_digest": None,
        "domain_head_digest": None,
        "environment_head_digest": None,
    }
    if snapshot is not None:
        heads = {
            "activation_head_digest": _optional_snapshot_digest(
                snapshot, "expected_activation_head_digest"
            ),
            "profile_head_digest": _optional_snapshot_digest(
                snapshot, "expected_profile_head_digest"
            ),
            "policy_head_digest": _optional_snapshot_digest(
                snapshot, "expected_policy_head_digest"
            ),
            "resource_head_digest": _optional_snapshot_digest(
                snapshot, "expected_resource_head_digest"
            ),
            "domain_head_digest": _optional_snapshot_digest(
                snapshot, "expected_domain_head_digest"
            ),
            "environment_head_digest": _optional_snapshot_digest(
                snapshot, "expected_environment_head_digest"
            ),
        }
    core: dict[str, object] = {
        "schema_version": MIRROR_RECORD_SCHEMA,
        "routed_capability_digest": routed_capability_digest,
        **heads,
        "selection_valid": selection_valid,
        "active_family_match": active_family_match,
        "matched_variant_digest": matched_variant_digest,
        "classification": classification,
        "failure_code": failure_code,
    }
    core.update(_AUTHORITY_FLAGS)
    return core


def _finish_record(core: dict[str, object]) -> dict[str, object]:
    return {
        **core,
        "record_digest": sha256_digest(
            {"domain": RECORD_DIGEST_DOMAIN, "record": core}
        ),
    }


def build_activation_mirror_record(
    *, capability_id: str, snapshot: object
) -> dict[str, object]:
    """Build one sanitized observation of a production capability selection.

    Invalid snapshots produce a deterministic fail-closed record instead of a
    positive match.  The caller's capability identifier is digested before any
    artifact is built and is never retained verbatim.
    """

    routed_digest = _routed_capability_digest(capability_id)
    try:
        supplied = _exact_dict(
            snapshot,
            maximum_keys=len(SNAPSHOT_KEYS),
            label="activation snapshot",
        )
        if set(supplied) != SNAPSHOT_KEYS:
            raise ActivationMirrorError("activation snapshot keyset")
        if (
            type(supplied["schema_version"]) is not str
            or supplied["schema_version"] != SNAPSHOT_SCHEMA
        ):
            raise ActivationMirrorError("activation snapshot schema refused")
        supplied = _stable_selection_snapshot(supplied)
    except ActivationMirrorError:
        return _finish_record(
            _record_core(
                routed_capability_digest=routed_digest,
                snapshot=None,
                selection_valid=False,
                active_family_match=False,
                matched_variant_digest=None,
                classification=SELECTION_INVALID,
                failure_code=SNAPSHOT_INVALID,
            )
        )

    try:
        valid, _reason = verify_activation_selection(
            head=supplied["head"],
            expected_activation_head_digest=supplied[
                "expected_activation_head_digest"
            ],
            context=supplied["context"],
            variants=supplied["variants"],
            variant_ceilings=supplied["variant_ceilings"],
            charter_ceiling=supplied["charter_ceiling"],
            expressed_ceiling=supplied["expressed_ceiling"],
            expected_profile_head_digest=supplied[
                "expected_profile_head_digest"
            ],
            expected_policy_head_digest=supplied["expected_policy_head_digest"],
            expected_resource_head_digest=supplied[
                "expected_resource_head_digest"
            ],
            expected_domain_head_digest=supplied["expected_domain_head_digest"],
            expected_environment_head_digest=supplied[
                "expected_environment_head_digest"
            ],
        )
    except Exception:  # total fail-closed boundary over untrusted snapshots
        valid = False
    if not valid:
        return _finish_record(
            _record_core(
                routed_capability_digest=routed_digest,
                snapshot=supplied,
                selection_valid=False,
                active_family_match=False,
                matched_variant_digest=None,
                classification=SELECTION_INVALID,
                failure_code=SELECTION_VERIFICATION_FAILED,
            )
        )

    head = supplied["head"]
    variants = supplied["variants"]
    # Successful selection verification proves these exact wire shapes and
    # the one-active-allele-per-family invariant.  Snapshot providers must keep
    # the aggregate stable for this pure call.
    active_digests = set(head["active_variant_digests"])
    matches = [
        variant["variant_digest"]
        for variant in variants
        if variant["variant_digest"] in active_digests
        and variant["family_id"] == capability_id
    ]
    if len(matches) == 1:
        classification = ACTIVE_MATCH
        failure_code = None
        matched_variant = matches[0]
        active_match = True
    else:
        classification = ACTIVE_MISS
        failure_code = NO_ACTIVE_FAMILY
        matched_variant = None
        active_match = False
    return _finish_record(
        _record_core(
            routed_capability_digest=routed_digest,
            snapshot=supplied,
            selection_valid=True,
            active_family_match=active_match,
            matched_variant_digest=matched_variant,
            classification=classification,
            failure_code=failure_code,
        )
    )


def _parse_record(value: object) -> dict[str, object]:
    record = _exact_dict(
        value, maximum_keys=len(RECORD_KEYS), label="activation mirror record"
    )
    if set(record) != RECORD_KEYS:
        raise ActivationMirrorError("activation mirror record keyset")
    if record["schema_version"] != MIRROR_RECORD_SCHEMA:
        raise ActivationMirrorError("activation mirror record schema refused")
    _digest(record["routed_capability_digest"], "routed_capability_digest")
    for field in _HEAD_FIELDS:
        _digest(record[field], field, optional=True)
    selection_valid = _exact_bool(record["selection_valid"], "selection_valid")
    active_match = _exact_bool(
        record["active_family_match"], "active_family_match"
    )
    matched = _digest(
        record["matched_variant_digest"],
        "matched_variant_digest",
        optional=True,
    )
    classification = record["classification"]
    if type(classification) is not str or classification not in CLASSIFICATIONS:
        raise ActivationMirrorError("activation mirror classification refused")
    failure_code = record["failure_code"]
    if failure_code is not None and (
        type(failure_code) is not str or failure_code not in FAILURE_CODES
    ):
        raise ActivationMirrorError("activation mirror failure code refused")
    for flag, expected in _AUTHORITY_FLAGS.items():
        if record[flag] is not expected:
            raise ActivationMirrorError(f"activation mirror flag {flag} drifted")

    if selection_valid and any(record[field] is None for field in _HEAD_FIELDS):
        raise ActivationMirrorError("valid selection is missing current heads")
    if classification == ACTIVE_MATCH:
        if not selection_valid or not active_match or matched is None:
            raise ActivationMirrorError("active match invariant mismatch")
        if failure_code is not None:
            raise ActivationMirrorError("active match cannot carry failure")
    elif classification == ACTIVE_MISS:
        if not selection_valid or active_match or matched is not None:
            raise ActivationMirrorError("active miss invariant mismatch")
        if failure_code != NO_ACTIVE_FAMILY:
            raise ActivationMirrorError("active miss reason mismatch")
    else:
        if selection_valid or active_match or matched is not None:
            raise ActivationMirrorError("invalid selection invariant mismatch")
        if failure_code not in {SNAPSHOT_INVALID, SELECTION_VERIFICATION_FAILED}:
            raise ActivationMirrorError("invalid selection reason mismatch")

    claimed = _digest(record["record_digest"], "record_digest")
    core = {key: record[key] for key in RECORD_CORE_KEYS}
    expected = sha256_digest({"domain": RECORD_DIGEST_DOMAIN, "record": core})
    if claimed != expected:
        raise ActivationMirrorError("activation mirror record digest mismatch")
    return {**core, "record_digest": claimed}


def verify_activation_mirror_record(
    value: object,
    *,
    capability_id: str,
    snapshot: object,
) -> tuple[bool, Optional[str]]:
    """Recompute a record from its routed capability and activation snapshot.

    This proves structural source binding only.  The snapshot's current-head
    claims still require authentication by the external provider/control plane.
    """

    try:
        parsed = _parse_record(value)
        recomputed = build_activation_mirror_record(
            capability_id=capability_id,
            snapshot=snapshot,
        )
    except ActivationMirrorError as exc:
        return False, str(exc)
    if parsed != recomputed:
        return False, "activation mirror record does not match source selection"
    return True, None


def summarize_activation_mirror(records: object) -> dict[str, object]:
    """Aggregate at most 256 exact records, refusing malformed evidence."""

    if type(records) is not list:
        raise ActivationMirrorError("activation mirror records must be a list")
    if list.__len__(records) > MAX_MIRROR_RECORDS:
        raise ActivationMirrorError("activation mirror records exceed bound")
    snapshot = records.copy()
    parsed = [_parse_record(record) for record in snapshot]
    counts = {classification: 0 for classification in CLASSIFICATIONS}
    for record in parsed:
        counts[record["classification"]] += 1
    core: dict[str, object] = {
        "schema_version": MIRROR_REPORT_SCHEMA,
        "measurement_scope": "activation_snapshot_read_only",
        "sample_count": len(parsed),
        "selection_valid_count": counts[ACTIVE_MATCH] + counts[ACTIVE_MISS],
        "selection_invalid_count": counts[SELECTION_INVALID],
        "active_match_count": counts[ACTIVE_MATCH],
        "active_miss_count": counts[ACTIVE_MISS],
        "by_classification": dict(sorted(counts.items())),
        "record_digests": sorted(record["record_digest"] for record in parsed),
    }
    core.update(_AUTHORITY_FLAGS)
    return {
        **core,
        "report_digest": sha256_digest(
            {"domain": REPORT_DIGEST_DOMAIN, "report": core}
        ),
    }


def _parse_activation_mirror_report_structure(
    value: object,
) -> dict[str, object]:
    """Validate aggregate structure only; source records remain required."""

    try:
        report = _exact_dict(
            value,
            maximum_keys=len(REPORT_KEYS),
            label="activation mirror report",
        )
        if set(report) != REPORT_KEYS:
            raise ActivationMirrorError("activation mirror report keyset")
        if report["schema_version"] != MIRROR_REPORT_SCHEMA:
            raise ActivationMirrorError("activation mirror report schema refused")
        if report["measurement_scope"] != "activation_snapshot_read_only":
            raise ActivationMirrorError("activation mirror report scope refused")
        sample_count = _bounded_count(
            report["sample_count"], "sample_count", maximum=MAX_MIRROR_RECORDS
        )
        counts = {
            field: _bounded_count(
                report[field], field, maximum=MAX_MIRROR_RECORDS
            )
            for field in (
                "selection_valid_count",
                "selection_invalid_count",
                "active_match_count",
                "active_miss_count",
            )
        }
        by_classification = _exact_dict(
            report["by_classification"],
            maximum_keys=len(CLASSIFICATIONS),
            label="by_classification",
        )
        if set(by_classification) != set(CLASSIFICATIONS):
            raise ActivationMirrorError("classification count keyset")
        parsed_by_classification = {
            key: _bounded_count(
                by_classification[key],
                f"by_classification.{key}",
                maximum=MAX_MIRROR_RECORDS,
            )
            for key in CLASSIFICATIONS
        }
        if sum(parsed_by_classification.values()) != sample_count:
            raise ActivationMirrorError("classification total mismatch")
        if counts["active_match_count"] != parsed_by_classification[ACTIVE_MATCH]:
            raise ActivationMirrorError("active match count mismatch")
        if counts["active_miss_count"] != parsed_by_classification[ACTIVE_MISS]:
            raise ActivationMirrorError("active miss count mismatch")
        if (
            counts["selection_invalid_count"]
            != parsed_by_classification[SELECTION_INVALID]
        ):
            raise ActivationMirrorError("invalid selection count mismatch")
        if counts["selection_valid_count"] != (
            counts["active_match_count"] + counts["active_miss_count"]
        ):
            raise ActivationMirrorError("valid selection count mismatch")
        if counts["selection_valid_count"] + counts[
            "selection_invalid_count"
        ] != sample_count:
            raise ActivationMirrorError("selection total mismatch")
        record_digests = report["record_digests"]
        if type(record_digests) is not list:
            raise ActivationMirrorError("record_digests must be a list")
        if list.__len__(record_digests) > MAX_MIRROR_RECORDS:
            raise ActivationMirrorError("record_digests exceed bound")
        normalized_record_digests = [
            _digest(item, "record_digests[]") for item in record_digests.copy()
        ]
        if normalized_record_digests != sorted(normalized_record_digests):
            raise ActivationMirrorError("record_digests must be sorted")
        if len(normalized_record_digests) != sample_count:
            raise ActivationMirrorError("record digest count mismatch")
        for flag, expected in _AUTHORITY_FLAGS.items():
            if report[flag] is not expected:
                raise ActivationMirrorError(f"activation mirror flag {flag} drifted")
        claimed = _digest(report["report_digest"], "report_digest")
        normalized = dict(report)
        normalized["by_classification"] = dict(
            sorted(parsed_by_classification.items())
        )
        normalized["record_digests"] = normalized_record_digests
        del normalized["report_digest"]
        expected = sha256_digest(
            {"domain": REPORT_DIGEST_DOMAIN, "report": normalized}
        )
        if claimed != expected:
            raise ActivationMirrorError("activation mirror report digest mismatch")
    except ActivationMirrorError:
        raise
    return {**normalized, "report_digest": claimed}


def verify_activation_mirror_report(
    value: object, *, records: object
) -> tuple[bool, Optional[str]]:
    """Recompute aggregate structure from exact bounded record artifacts.

    Callers that need source assurance must first verify each record against
    its capability/snapshot inputs with :func:`verify_activation_mirror_record`.
    Neither check authenticates a provider or grants authority.
    """

    try:
        parsed = _parse_activation_mirror_report_structure(value)
        recomputed = summarize_activation_mirror(records)
    except ActivationMirrorError as exc:
        return False, str(exc)
    if parsed != recomputed:
        return False, "activation mirror report does not match source records"
    return True, None
