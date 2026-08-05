# SPDX-License-Identifier: BUSL-1.1
"""Default-off shadow adapter and audit summary for consensus admission.

The adapter accepts separately injected material and expectation snapshots and
evaluates them locally.  It does not accept a provider's precomputed positive
receipt, and the material provider cannot choose its own trusted heads.  A
runtime adapter should read the expectation source before and after material
collection to fence concurrent head changes.

The summary is intentionally weaker than the gate receipt verifier: it parses
already-produced receipts and content-addresses aggregate counters, but does
not retain source payloads or re-run HMAC/provenance/log verification.  Runtime
callers must count a receipt as evaluated only when it came directly from
``evaluate_attested_consensus_shadow_request`` in the isolated observer lane.
Nothing in this module activates a candidate, changes routing, or grants
authority.
"""

from __future__ import annotations

import re
from typing import Optional

from waggledance.core.capabilities.activation_contracts import MAX_GENERATION
from waggledance.core.magma.canonical import sha256_digest
from waggledance.core.orchestration.attested_consensus_gate import (
    AttestedConsensusGateError,
    evaluate_attested_consensus_gate,
    parse_attested_consensus_gate_receipt,
)
from waggledance.core.orchestration.evidence_attestation import MAX_ATTESTATIONS

ATTESTED_CONSENSUS_SHADOW_REPORT_SCHEMA = (
    "wd.attested_consensus_shadow_report.v1"
)
ATTESTED_CONSENSUS_SHADOW_REPORT_DIGEST_DOMAIN = (
    "wd.attested_consensus_shadow_report.digest.v1"
)
MAX_SHADOW_RECEIPTS = 256

GATE_MATERIAL_KEYS = frozenset(
    {
        "activation_admission_intent",
        "policy",
        "provenance_registry_snapshot",
        "attestation_log_base_snapshot",
        "attestation_log_closed_snapshot",
        "evidence_records",
        "ballots",
        "attestations",
    }
)
GATE_EXPECTATION_KEYS = frozenset(
    {
        "expected_consensus_policy_digest",
        "expected_activation_scope_digest",
        "expected_query_digest",
        "expected_current_bundle_digest",
        "expected_current_activation_head_digest",
        "expected_current_store_revision",
        "expected_proposed_bundle_digest",
        "expected_proposed_activation_head_digest",
        "expected_proposed_store_revision",
        "expected_trust_registry_head_digest",
        "expected_attestation_log_base_head_digest",
        "expected_attestation_log_closed_head_digest",
    }
)
GATE_RUNTIME_DEPENDENCY_KEYS = frozenset({"key_lookup"})
GATE_REQUEST_KEYS = (
    GATE_MATERIAL_KEYS | GATE_EXPECTATION_KEYS | GATE_RUNTIME_DEPENDENCY_KEYS
)

_NO_AUTHORITY_FLAGS = {
    "structural_summary_only": True,
    "source_reverification_performed": False,
    "observer_only": True,
    "activation_performed": False,
    "routing_influence_applied": False,
    "production_decision_unchanged": True,
    "authority_granted": False,
}

SHADOW_REPORT_CORE_KEYS = frozenset(
    {
        "schema_version",
        "receipt_count",
        "unique_receipt_count",
        "advisory_pass_count",
        "advisory_block_count",
        "committed_entry_count_total",
        "gate_receipt_digests",
        *_NO_AUTHORITY_FLAGS,
    }
)
SHADOW_REPORT_KEYS = SHADOW_REPORT_CORE_KEYS | {"report_digest"}

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


class AttestedConsensusShadowError(ValueError):
    """A shadow request or structural report violates its exact contract."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _refuse(reason: str, message: str) -> None:
    raise AttestedConsensusShadowError(reason, message)


def _exact_dict(
    value: object, keys: frozenset[str], label: str
) -> dict[str, object]:
    if type(value) is not dict:
        _refuse(f"{label}_type", f"{label} must be an exact dict")
    if dict.__len__(value) > len(keys):
        _refuse(f"{label}_keyset", f"{label} exceeds its key bound")
    snapshot = value.copy()
    if dict.__len__(snapshot) > len(keys):
        _refuse(f"{label}_keyset", f"{label} exceeds its key bound")
    if set(snapshot) != keys or any(type(key) is not str for key in snapshot):
        _refuse(f"{label}_keyset", f"{label} has a non-exact keyset")
    return snapshot


def _bounded_list(value: object, label: str, maximum: int) -> list:
    if type(value) is not list:
        _refuse(f"{label}_type", f"{label} must be an exact list")
    if list.__len__(value) > maximum:
        _refuse(f"{label}_count_exceeded", f"{label} exceeds its bound")
    snapshot = value[: maximum + 1]
    if list.__len__(snapshot) > maximum:
        _refuse(f"{label}_count_exceeded", f"{label} exceeds its bound")
    return snapshot


def _bounded_count(value: object, label: str, maximum: int) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        _refuse(label, f"{label} must be an exact bounded integer")
    return value


def parse_attested_consensus_shadow_expectations(
    value: object,
) -> dict[str, object]:
    """Privately copy one exact independently sourced expectation snapshot."""

    expectations = _exact_dict(
        value, GATE_EXPECTATION_KEYS, "gate_expectations"
    )
    for field in GATE_EXPECTATION_KEYS - {
        "expected_current_store_revision",
        "expected_proposed_store_revision",
    }:
        item = expectations[field]
        if type(item) is not str or not _SHA256.fullmatch(item):
            _refuse(field, f"{field} must be a lowercase sha256 digest")
    for field in (
        "expected_current_store_revision",
        "expected_proposed_store_revision",
    ):
        item = expectations[field]
        if type(item) is not int or not 0 <= item <= MAX_GENERATION:
            _refuse(field, f"{field} must be an exact bounded integer")
    return expectations


def evaluate_attested_consensus_shadow_request(
    materials: object,
    *,
    expected_bindings: object,
    trusted_key_lookup: object,
) -> dict[str, object]:
    """Evaluate exact material against a separate trusted expectation view."""

    material = _exact_dict(materials, GATE_MATERIAL_KEYS, "gate_materials")
    expectations = parse_attested_consensus_shadow_expectations(
        expected_bindings
    )
    if not callable(trusted_key_lookup):
        _refuse("key_lookup_not_callable", "gate key lookup must be callable")
    try:
        return evaluate_attested_consensus_gate(
            **material,
            **expectations,
            key_lookup=trusted_key_lookup,
        )
    except AttestedConsensusGateError as exc:
        _refuse(f"gate:{exc.reason}", "attested consensus gate refused")


def _report_digest(core: dict[str, object]) -> str:
    return sha256_digest(
        {
            "domain": ATTESTED_CONSENSUS_SHADOW_REPORT_DIGEST_DOMAIN,
            **core,
        }
    )


def summarize_attested_consensus_shadow(
    receipts: object,
) -> dict[str, object]:
    """Content-address a bounded structural summary of evaluated receipts."""

    supplied = _bounded_list(receipts, "receipts", MAX_SHADOW_RECEIPTS)
    parsed: list[dict[str, object]] = []
    for receipt in supplied:
        try:
            parsed.append(parse_attested_consensus_gate_receipt(receipt))
        except AttestedConsensusGateError as exc:
            _refuse(f"receipt:{exc.reason}", "gate receipt refused")
    receipt_digests = sorted(item["gate_receipt_digest"] for item in parsed)
    pass_count = sum(item["consensus_gate_passed"] is True for item in parsed)
    core: dict[str, object] = {
        "schema_version": ATTESTED_CONSENSUS_SHADOW_REPORT_SCHEMA,
        "receipt_count": len(parsed),
        "unique_receipt_count": len(set(receipt_digests)),
        "advisory_pass_count": pass_count,
        "advisory_block_count": len(parsed) - pass_count,
        "committed_entry_count_total": sum(
            item["committed_entry_count"] for item in parsed
        ),
        "gate_receipt_digests": receipt_digests,
        **_NO_AUTHORITY_FLAGS,
    }
    return {**core, "report_digest": _report_digest(core)}


def parse_attested_consensus_shadow_report(
    value: object,
) -> dict[str, object]:
    """Parse and privately copy one structural shadow summary."""

    report = _exact_dict(value, SHADOW_REPORT_KEYS, "shadow_report")
    if (
        type(report["schema_version"]) is not str
        or report["schema_version"] != ATTESTED_CONSENSUS_SHADOW_REPORT_SCHEMA
    ):
        _refuse("report_schema", "shadow report schema refused")
    receipt_count = _bounded_count(
        report["receipt_count"], "receipt_count", MAX_SHADOW_RECEIPTS
    )
    unique_count = _bounded_count(
        report["unique_receipt_count"],
        "unique_receipt_count",
        MAX_SHADOW_RECEIPTS,
    )
    pass_count = _bounded_count(
        report["advisory_pass_count"],
        "advisory_pass_count",
        MAX_SHADOW_RECEIPTS,
    )
    block_count = _bounded_count(
        report["advisory_block_count"],
        "advisory_block_count",
        MAX_SHADOW_RECEIPTS,
    )
    committed_total = _bounded_count(
        report["committed_entry_count_total"],
        "committed_entry_count_total",
        MAX_SHADOW_RECEIPTS * MAX_ATTESTATIONS,
    )
    digests = _bounded_list(
        report["gate_receipt_digests"],
        "gate_receipt_digests",
        MAX_SHADOW_RECEIPTS,
    )
    if any(
        type(item) is not str or not _SHA256.fullmatch(item) for item in digests
    ) or digests != sorted(digests):
        _refuse("gate_receipt_digests", "receipt digest list refused")
    if len(digests) != receipt_count:
        _refuse("receipt_count_mismatch", "receipt count differs from digests")
    if len(set(digests)) != unique_count:
        _refuse("unique_receipt_count_mismatch", "unique receipt count mismatch")
    if pass_count + block_count != receipt_count:
        _refuse("advice_count_mismatch", "advice counts do not sum to receipts")
    for field, expected in _NO_AUTHORITY_FLAGS.items():
        if type(report[field]) is not bool or report[field] is not expected:
            _refuse(field, f"shadow report {field} must remain {expected!r}")
    claimed = report["report_digest"]
    if type(claimed) is not str or not _SHA256.fullmatch(claimed):
        _refuse("report_digest", "report digest must be a sha256 digest")
    core = {
        "schema_version": ATTESTED_CONSENSUS_SHADOW_REPORT_SCHEMA,
        "receipt_count": receipt_count,
        "unique_receipt_count": unique_count,
        "advisory_pass_count": pass_count,
        "advisory_block_count": block_count,
        "committed_entry_count_total": committed_total,
        "gate_receipt_digests": digests,
        **_NO_AUTHORITY_FLAGS,
    }
    if claimed != _report_digest(core):
        _refuse("report_digest_mismatch", "shadow report digest mismatch")
    return {**core, "report_digest": claimed}


def verify_attested_consensus_shadow_report(
    value: object,
    *,
    receipts: object,
) -> tuple[bool, Optional[str]]:
    """Recompute a structural report from its exact receipt list."""

    try:
        parsed = parse_attested_consensus_shadow_report(value)
        recomputed = summarize_attested_consensus_shadow(receipts)
    except AttestedConsensusShadowError as exc:
        return False, exc.reason
    if parsed != recomputed:
        return False, "shadow_report_recompute_mismatch"
    return True, None


__all__ = [
    "ATTESTED_CONSENSUS_SHADOW_REPORT_DIGEST_DOMAIN",
    "ATTESTED_CONSENSUS_SHADOW_REPORT_SCHEMA",
    "GATE_EXPECTATION_KEYS",
    "GATE_MATERIAL_KEYS",
    "GATE_REQUEST_KEYS",
    "GATE_RUNTIME_DEPENDENCY_KEYS",
    "MAX_SHADOW_RECEIPTS",
    "SHADOW_REPORT_KEYS",
    "AttestedConsensusShadowError",
    "evaluate_attested_consensus_shadow_request",
    "parse_attested_consensus_shadow_expectations",
    "parse_attested_consensus_shadow_report",
    "summarize_attested_consensus_shadow",
    "verify_attested_consensus_shadow_report",
]
