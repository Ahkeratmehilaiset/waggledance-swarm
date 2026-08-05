# SPDX-License-Identifier: BUSL-1.1
"""Pure local authentication for inhibitory-consensus source claims.

``EvidenceAttestationV1`` uses a domain-separated HMAC-SHA256 over one exact
evidence/ballot pair.  The independently supplied trust-registry head,
activation scope, and admission challenge prevent replay across key epochs,
cells/deployments, and ledger challenges.

This is deliberately an observer layer.  HMAC proves possession of a local
shared secret, not reviewer independence, public non-repudiation, the truth of
the six non-lineage provenance claims, or completeness of the submitted source
set.  Consequently neither an attestation nor the batch observation grants
authority, advises activation, changes routing, or performs activation.  A
future admission gate must obtain the bounded source set from a pinned
authoritative ledger before it can reason about omitted stop/veto ballots.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from typing import Callable, Optional

from waggledance.core.magma.canonical import canonical_json_bytes, sha256_digest
from waggledance.core.orchestration.evidence_consensus import (
    MAX_BALLOTS,
    MAX_EVIDENCE_RECORDS,
    MAX_REQUIRED_SUPPORT,
    EvidenceConsensusError,
    evaluate_inhibitory_consensus,
    parse_evidence_diversity,
    parse_inhibitory_ballot,
    verify_consensus_evaluation,
)

ATTESTATION_SCHEMA = "wd.evidence_attestation.v1"
ATTESTATION_SCHEME = "wd.evidence_attestation.hmac_sha256.v1"
ATTESTATION_SIGNING_DOMAIN = "wd.evidence_attestation.signing.v1"
ATTESTATION_DIGEST_DOMAIN = "wd.evidence_attestation.digest.v1"

OBSERVATION_SCHEMA = "wd.attested_inhibitory_consensus_observation.v1"
OBSERVATION_DIGEST_DOMAIN = (
    "wd.attested_inhibitory_consensus_observation.digest.v1"
)

MAX_ATTESTATIONS = MAX_BALLOTS
KEY_BYTES = 32

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_SIGNATURE = re.compile(r"^hmac-sha256:[0-9a-f]{64}$")
_BINDING_FIELDS = (
    "query_digest",
    "decision_digest",
    "candidate_digest",
    "activation_head_digest",
)

ATTESTATION_KEYS = frozenset(
    {
        "schema_version",
        "signature_scheme",
        "evidence_digest",
        "ballot_digest",
        "reviewer_lineage_digest",
        "trust_registry_head_digest",
        "activation_scope_digest",
        "admission_challenge_digest",
        "signing_key_digest",
        "advisory_only",
        "authority_granted",
        "signature",
        "attestation_digest",
    }
)

OBSERVATION_KEYS = frozenset(
    {
        "schema_version",
        *_BINDING_FIELDS,
        "trust_registry_head_digest",
        "activation_scope_digest",
        "admission_challenge_digest",
        "required_independent_support",
        "evidence_digests",
        "ballot_digests",
        "attestation_digests",
        "claimed_provenance_consensus",
        "claimed_provenance_consensus_digest",
        "claimed_provenance_only",
        "source_set_completeness_verified",
        "positive_admission_ready",
        "activation_admission_advised",
        "activation_performed",
        "routing_influence_applied",
        "advisory_only",
        "authority_granted",
        "observation_digest",
    }
)

KeyLookup = Callable[[str, str, str], Optional[bytes]]


class EvidenceAttestationError(ValueError):
    """A stable, payload-free refusal at the signed-evidence boundary."""


def _wire_dict(value: object, *, exact_key_count: int) -> Optional[dict]:
    if type(value) is not dict:
        return None
    if dict.__len__(value) > exact_key_count:
        return {}
    snapshot = value.copy()
    if any(type(key) is not str for key in snapshot):
        return None
    return snapshot


def _wire_list(value: object, *, maximum: int) -> list:
    if type(value) is not list:
        raise EvidenceAttestationError("not_list")
    if list.__len__(value) > maximum:
        raise EvidenceAttestationError("count_exceeded")
    snapshot = value.copy()
    if list.__len__(snapshot) > maximum:
        raise EvidenceAttestationError("count_exceeded")
    return snapshot


def _require_digest(value: object, reason: str) -> str:
    if type(value) is not str or not _SHA256.fullmatch(value):
        raise EvidenceAttestationError(reason)
    return value


def _require_key(key: object) -> bytes:
    if type(key) is not bytes or len(key) != KEY_BYTES:
        raise EvidenceAttestationError("key_invalid")
    return key


def derive_signing_key_digest(key: bytes) -> str:
    """Return the full SHA-256 digest of an exact 32-byte HMAC key."""

    key = _require_key(key)
    return "sha256:" + hashlib.sha256(key).hexdigest()


def _attestation_body(
    *,
    evidence_digest: object,
    ballot_digest: object,
    reviewer_lineage_digest: object,
    trust_registry_head_digest: object,
    activation_scope_digest: object,
    admission_challenge_digest: object,
    signing_key_digest: object,
    advisory_only: object = True,
    authority_granted: object = False,
) -> dict[str, object]:
    if type(advisory_only) is not bool or advisory_only is not True:
        raise EvidenceAttestationError("advisory_only")
    if type(authority_granted) is not bool or authority_granted is not False:
        raise EvidenceAttestationError("authority_granted")
    return {
        "schema_version": ATTESTATION_SCHEMA,
        "signature_scheme": ATTESTATION_SCHEME,
        "evidence_digest": _require_digest(evidence_digest, "evidence_digest"),
        "ballot_digest": _require_digest(ballot_digest, "ballot_digest"),
        "reviewer_lineage_digest": _require_digest(
            reviewer_lineage_digest, "reviewer_lineage_digest"
        ),
        "trust_registry_head_digest": _require_digest(
            trust_registry_head_digest, "trust_registry_head_digest"
        ),
        "activation_scope_digest": _require_digest(
            activation_scope_digest, "activation_scope_digest"
        ),
        "admission_challenge_digest": _require_digest(
            admission_challenge_digest, "admission_challenge_digest"
        ),
        "signing_key_digest": _require_digest(
            signing_key_digest, "signing_key_digest"
        ),
        "advisory_only": True,
        "authority_granted": False,
    }


def canonical_signing_bytes(
    *,
    evidence_digest: str,
    ballot_digest: str,
    reviewer_lineage_digest: str,
    trust_registry_head_digest: str,
    activation_scope_digest: str,
    admission_challenge_digest: str,
    signing_key_digest: str,
    advisory_only: bool = True,
    authority_granted: bool = False,
) -> bytes:
    """Canonical domain-separated bytes signed by EvidenceAttestationV1."""

    body = _attestation_body(
        evidence_digest=evidence_digest,
        ballot_digest=ballot_digest,
        reviewer_lineage_digest=reviewer_lineage_digest,
        trust_registry_head_digest=trust_registry_head_digest,
        activation_scope_digest=activation_scope_digest,
        admission_challenge_digest=admission_challenge_digest,
        signing_key_digest=signing_key_digest,
        advisory_only=advisory_only,
        authority_granted=authority_granted,
    )
    return canonical_json_bytes(
        {"domain": ATTESTATION_SIGNING_DOMAIN, **body}
    )


def _derive_attestation_digest(
    body: dict[str, object], signature: str
) -> str:
    return sha256_digest(
        {
            "domain": ATTESTATION_DIGEST_DOMAIN,
            **body,
            "signature": signature,
        }
    )


@dataclass(frozen=True)
class EvidenceAttestationV1:
    evidence_digest: str
    ballot_digest: str
    reviewer_lineage_digest: str
    trust_registry_head_digest: str
    activation_scope_digest: str
    admission_challenge_digest: str
    signing_key_digest: str
    signature: str
    attestation_digest: str
    advisory_only: bool = True
    authority_granted: bool = False
    signature_scheme: str = ATTESTATION_SCHEME
    schema_version: str = ATTESTATION_SCHEMA

    def __post_init__(self) -> None:
        canonicalize_evidence_attestation(self.to_mapping())

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "signature_scheme": self.signature_scheme,
            "evidence_digest": self.evidence_digest,
            "ballot_digest": self.ballot_digest,
            "reviewer_lineage_digest": self.reviewer_lineage_digest,
            "trust_registry_head_digest": self.trust_registry_head_digest,
            "activation_scope_digest": self.activation_scope_digest,
            "admission_challenge_digest": self.admission_challenge_digest,
            "signing_key_digest": self.signing_key_digest,
            "advisory_only": self.advisory_only,
            "authority_granted": self.authority_granted,
            "signature": self.signature,
            "attestation_digest": self.attestation_digest,
        }


def _parse_evidence(value: object) -> dict[str, str]:
    try:
        return parse_evidence_diversity(value)
    except EvidenceConsensusError:
        raise EvidenceAttestationError("evidence_invalid") from None


def _parse_ballot(value: object) -> dict[str, object]:
    try:
        return parse_inhibitory_ballot(value)
    except EvidenceConsensusError:
        raise EvidenceAttestationError("ballot_invalid") from None


def _validate_source_pair(
    evidence: dict[str, str], ballot: dict[str, object]
) -> None:
    if ballot["evidence_digest"] != evidence["evidence_digest"]:
        raise EvidenceAttestationError("source_binding_mismatch")
    if any(ballot[field] != evidence[field] for field in _BINDING_FIELDS):
        raise EvidenceAttestationError("source_binding_mismatch")


def build_evidence_attestation(
    *,
    evidence: object,
    ballot: object,
    trust_registry_head_digest: str,
    activation_scope_digest: str,
    admission_challenge_digest: str,
    key: bytes,
) -> EvidenceAttestationV1:
    """Sign one exact evidence/ballot pair without granting authority."""

    parsed_evidence = _parse_evidence(evidence)
    parsed_ballot = _parse_ballot(ballot)
    _validate_source_pair(parsed_evidence, parsed_ballot)
    key = _require_key(key)
    key_digest = derive_signing_key_digest(key)
    body = _attestation_body(
        evidence_digest=parsed_evidence["evidence_digest"],
        ballot_digest=parsed_ballot["ballot_digest"],
        reviewer_lineage_digest=parsed_evidence["reviewer_lineage_digest"],
        trust_registry_head_digest=trust_registry_head_digest,
        activation_scope_digest=activation_scope_digest,
        admission_challenge_digest=admission_challenge_digest,
        signing_key_digest=key_digest,
    )
    signature = "hmac-sha256:" + hmac.new(
        key,
        canonical_signing_bytes(
            **{
                name: body[name]
                for name in (
                    "evidence_digest",
                    "ballot_digest",
                    "reviewer_lineage_digest",
                    "trust_registry_head_digest",
                    "activation_scope_digest",
                    "admission_challenge_digest",
                    "signing_key_digest",
                    "advisory_only",
                    "authority_granted",
                )
            }
        ),
        hashlib.sha256,
    ).hexdigest()
    return EvidenceAttestationV1(
        **{
            name: body[name]
            for name in (
                "evidence_digest",
                "ballot_digest",
                "reviewer_lineage_digest",
                "trust_registry_head_digest",
                "activation_scope_digest",
                "admission_challenge_digest",
                "signing_key_digest",
            )
        },
        signature=signature,
        attestation_digest=_derive_attestation_digest(body, signature),
    )


def canonicalize_evidence_attestation(value: object) -> dict[str, object]:
    """Strictly parse and privately copy an attestation wire object."""

    snapshot = _wire_dict(value, exact_key_count=len(ATTESTATION_KEYS))
    if snapshot is None:
        raise EvidenceAttestationError("attestation_not_dict")
    if set(snapshot) != ATTESTATION_KEYS:
        raise EvidenceAttestationError("attestation_keyset")
    if (
        type(snapshot["schema_version"]) is not str
        or snapshot["schema_version"] != ATTESTATION_SCHEMA
    ):
        raise EvidenceAttestationError("schema_version")
    if (
        type(snapshot["signature_scheme"]) is not str
        or snapshot["signature_scheme"] != ATTESTATION_SCHEME
    ):
        raise EvidenceAttestationError("signature_scheme")
    body = _attestation_body(
        evidence_digest=snapshot["evidence_digest"],
        ballot_digest=snapshot["ballot_digest"],
        reviewer_lineage_digest=snapshot["reviewer_lineage_digest"],
        trust_registry_head_digest=snapshot["trust_registry_head_digest"],
        activation_scope_digest=snapshot["activation_scope_digest"],
        admission_challenge_digest=snapshot["admission_challenge_digest"],
        signing_key_digest=snapshot["signing_key_digest"],
        advisory_only=snapshot["advisory_only"],
        authority_granted=snapshot["authority_granted"],
    )
    signature = snapshot["signature"]
    if type(signature) is not str or not _SIGNATURE.fullmatch(signature):
        raise EvidenceAttestationError("signature_shape")
    claimed_digest = _require_digest(
        snapshot["attestation_digest"], "attestation_digest"
    )
    if claimed_digest != _derive_attestation_digest(body, signature):
        raise EvidenceAttestationError("attestation_digest_mismatch")
    return {
        **body,
        "signature": signature,
        "attestation_digest": claimed_digest,
    }


def _lookup_key(
    key_lookup: KeyLookup,
    *,
    expected_trust_registry_head_digest: str,
    reviewer_lineage_digest: str,
    signing_key_digest: str,
) -> bytes:
    try:
        key = key_lookup(
            expected_trust_registry_head_digest,
            reviewer_lineage_digest,
            signing_key_digest,
        )
    except Exception:
        raise EvidenceAttestationError("key_lookup_failed") from None
    if key is None:
        raise EvidenceAttestationError("key_unavailable")
    key = _require_key(key)
    if not hmac.compare_digest(derive_signing_key_digest(key), signing_key_digest):
        raise EvidenceAttestationError("key_digest_mismatch")
    return key


def _authenticate_attestation(
    attestation: dict[str, object],
    *,
    evidence: dict[str, str],
    ballot: dict[str, object],
    expected_trust_registry_head_digest: str,
    expected_activation_scope_digest: str,
    expected_admission_challenge_digest: str,
    key_lookup: KeyLookup,
) -> None:
    _validate_source_pair(evidence, ballot)
    if attestation["evidence_digest"] != evidence["evidence_digest"]:
        raise EvidenceAttestationError("evidence_binding_mismatch")
    if attestation["ballot_digest"] != ballot["ballot_digest"]:
        raise EvidenceAttestationError("ballot_binding_mismatch")
    if (
        attestation["reviewer_lineage_digest"]
        != evidence["reviewer_lineage_digest"]
    ):
        raise EvidenceAttestationError("lineage_binding_mismatch")
    if (
        attestation["trust_registry_head_digest"]
        != expected_trust_registry_head_digest
    ):
        raise EvidenceAttestationError("trust_registry_head_mismatch")
    if attestation["activation_scope_digest"] != expected_activation_scope_digest:
        raise EvidenceAttestationError("activation_scope_mismatch")
    if (
        attestation["admission_challenge_digest"]
        != expected_admission_challenge_digest
    ):
        raise EvidenceAttestationError("admission_challenge_mismatch")
    key = _lookup_key(
        key_lookup,
        expected_trust_registry_head_digest=expected_trust_registry_head_digest,
        reviewer_lineage_digest=attestation["reviewer_lineage_digest"],
        signing_key_digest=attestation["signing_key_digest"],
    )
    expected_signature = "hmac-sha256:" + hmac.new(
        key,
        canonical_signing_bytes(
            **{
                name: attestation[name]
                for name in (
                    "evidence_digest",
                    "ballot_digest",
                    "reviewer_lineage_digest",
                    "trust_registry_head_digest",
                    "activation_scope_digest",
                    "admission_challenge_digest",
                    "signing_key_digest",
                    "advisory_only",
                    "authority_granted",
                )
            }
        ),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(attestation["signature"], expected_signature):
        raise EvidenceAttestationError("signature_invalid")


def verify_evidence_attestation(
    value: object,
    *,
    evidence: object,
    ballot: object,
    expected_trust_registry_head_digest: str,
    expected_activation_scope_digest: str,
    expected_admission_challenge_digest: str,
    key_lookup: KeyLookup,
) -> tuple[bool, Optional[str]]:
    try:
        expected_head = _require_digest(
            expected_trust_registry_head_digest, "trust_registry_head_digest"
        )
        expected_scope = _require_digest(
            expected_activation_scope_digest, "activation_scope_digest"
        )
        expected_challenge = _require_digest(
            expected_admission_challenge_digest, "admission_challenge_digest"
        )
        attestation = canonicalize_evidence_attestation(value)
        parsed_evidence = _parse_evidence(evidence)
        parsed_ballot = _parse_ballot(ballot)
        _authenticate_attestation(
            attestation,
            evidence=parsed_evidence,
            ballot=parsed_ballot,
            expected_trust_registry_head_digest=expected_head,
            expected_activation_scope_digest=expected_scope,
            expected_admission_challenge_digest=expected_challenge,
            key_lookup=key_lookup,
        )
    except EvidenceAttestationError as exc:
        return False, str(exc)
    return True, None


def _validated_context(
    *,
    query_digest: object,
    decision_digest: object,
    candidate_digest: object,
    activation_head_digest: object,
) -> dict[str, str]:
    return {
        name: _require_digest(value, name)
        for name, value in (
            ("query_digest", query_digest),
            ("decision_digest", decision_digest),
            ("candidate_digest", candidate_digest),
            ("activation_head_digest", activation_head_digest),
        )
    }


def _context_matches(value: dict[str, object], expected: dict[str, str]) -> bool:
    return all(value[field] == expected[field] for field in _BINDING_FIELDS)


def evaluate_attested_inhibitory_consensus(
    *,
    query_digest: str,
    decision_digest: str,
    candidate_digest: str,
    activation_head_digest: str,
    expected_trust_registry_head_digest: str,
    expected_activation_scope_digest: str,
    expected_admission_challenge_digest: str,
    evidence_records: object,
    ballots: object,
    attestations: object,
    required_independent_support: int,
    key_lookup: KeyLookup,
) -> dict[str, object]:
    """Authenticate a complete bounded batch, then observe its consensus.

    Every source is parsed before any subset can reach the evaluator.  A single
    malformed, stale, orphaned, unsigned, or unverifiable item raises a stable
    error and no partial consensus result is produced.
    """

    context = _validated_context(
        query_digest=query_digest,
        decision_digest=decision_digest,
        candidate_digest=candidate_digest,
        activation_head_digest=activation_head_digest,
    )
    trust_head = _require_digest(
        expected_trust_registry_head_digest, "trust_registry_head_digest"
    )
    scope = _require_digest(
        expected_activation_scope_digest, "activation_scope_digest"
    )
    challenge = _require_digest(
        expected_admission_challenge_digest, "admission_challenge_digest"
    )
    if (
        type(required_independent_support) is not int
        or not 1 <= required_independent_support <= MAX_REQUIRED_SUPPORT
    ):
        raise EvidenceAttestationError("required_independent_support")

    evidence_input = _wire_list(
        evidence_records, maximum=MAX_EVIDENCE_RECORDS
    )
    ballot_input = _wire_list(ballots, maximum=MAX_BALLOTS)
    attestation_input = _wire_list(attestations, maximum=MAX_ATTESTATIONS)

    parsed_evidence_list = [_parse_evidence(item) for item in evidence_input]
    parsed_ballot_list = [_parse_ballot(item) for item in ballot_input]
    parsed_attestation_list = [
        canonicalize_evidence_attestation(item) for item in attestation_input
    ]
    if any(not _context_matches(item, context) for item in parsed_evidence_list):
        raise EvidenceAttestationError("evidence_context_mismatch")
    if any(not _context_matches(item, context) for item in parsed_ballot_list):
        raise EvidenceAttestationError("ballot_context_mismatch")

    evidence_by_digest: dict[str, dict[str, str]] = {}
    for evidence in parsed_evidence_list:
        evidence_by_digest.setdefault(evidence["evidence_digest"], evidence)
    ballot_by_digest: dict[str, dict[str, object]] = {}
    for ballot in parsed_ballot_list:
        evidence = evidence_by_digest.get(ballot["evidence_digest"])
        if evidence is None:
            raise EvidenceAttestationError("ballot_orphan")
        _validate_source_pair(evidence, ballot)
        ballot_by_digest.setdefault(ballot["ballot_digest"], ballot)

    referenced_evidence = {
        ballot["evidence_digest"] for ballot in ballot_by_digest.values()
    }
    if referenced_evidence != set(evidence_by_digest):
        raise EvidenceAttestationError("evidence_unreferenced")

    attestation_digests_by_ballot: dict[str, set[str]] = {}
    lineages_by_key: dict[str, set[str]] = {}
    for attestation in parsed_attestation_list:
        ballot = ballot_by_digest.get(attestation["ballot_digest"])
        if ballot is None:
            raise EvidenceAttestationError("attestation_orphan")
        evidence = evidence_by_digest[ballot["evidence_digest"]]
        _authenticate_attestation(
            attestation,
            evidence=evidence,
            ballot=ballot,
            expected_trust_registry_head_digest=trust_head,
            expected_activation_scope_digest=scope,
            expected_admission_challenge_digest=challenge,
            key_lookup=key_lookup,
        )
        attestation_digests_by_ballot.setdefault(
            ballot["ballot_digest"], set()
        ).add(attestation["attestation_digest"])
        lineages_by_key.setdefault(attestation["signing_key_digest"], set()).add(
            attestation["reviewer_lineage_digest"]
        )

    if any(len(lineages) > 1 for lineages in lineages_by_key.values()):
        raise EvidenceAttestationError("cross_lineage_key_reuse")
    if set(attestation_digests_by_ballot) != set(ballot_by_digest):
        raise EvidenceAttestationError("ballot_unattested")
    if any(len(items) != 1 for items in attestation_digests_by_ballot.values()):
        raise EvidenceAttestationError("ballot_attestation_cardinality")

    consensus = evaluate_inhibitory_consensus(
        **context,
        evidence_records=parsed_evidence_list,
        ballots=parsed_ballot_list,
        required_independent_support=required_independent_support,
    )
    observation: dict[str, object] = {
        "schema_version": OBSERVATION_SCHEMA,
        **context,
        "trust_registry_head_digest": trust_head,
        "activation_scope_digest": scope,
        "admission_challenge_digest": challenge,
        "required_independent_support": required_independent_support,
        "evidence_digests": sorted(evidence_by_digest),
        "ballot_digests": sorted(ballot_by_digest),
        "attestation_digests": sorted(
            {
                item["attestation_digest"]
                for item in parsed_attestation_list
            }
        ),
        "claimed_provenance_consensus": consensus,
        "claimed_provenance_consensus_digest": consensus["evaluation_digest"],
        "claimed_provenance_only": True,
        "source_set_completeness_verified": False,
        "positive_admission_ready": False,
        "activation_admission_advised": False,
        "activation_performed": False,
        "routing_influence_applied": False,
        "advisory_only": True,
        "authority_granted": False,
    }
    observation["observation_digest"] = sha256_digest(
        {"domain": OBSERVATION_DIGEST_DOMAIN, **observation}
    )
    return observation


def _canonicalize_observation_shell(value: object) -> dict[str, object]:
    snapshot = _wire_dict(value, exact_key_count=len(OBSERVATION_KEYS))
    if snapshot is None:
        raise EvidenceAttestationError("observation_not_dict")
    if set(snapshot) != OBSERVATION_KEYS:
        raise EvidenceAttestationError("observation_keyset")
    if (
        type(snapshot["schema_version"]) is not str
        or snapshot["schema_version"] != OBSERVATION_SCHEMA
    ):
        raise EvidenceAttestationError("observation_schema")
    for field in (
        *_BINDING_FIELDS,
        "trust_registry_head_digest",
        "activation_scope_digest",
        "admission_challenge_digest",
        "claimed_provenance_consensus_digest",
        "observation_digest",
    ):
        _require_digest(snapshot[field], f"observation_{field}")
    if (
        type(snapshot["required_independent_support"]) is not int
        or not 1
        <= snapshot["required_independent_support"]
        <= MAX_REQUIRED_SUPPORT
    ):
        raise EvidenceAttestationError("observation_required_support")
    for field, maximum in (
        ("evidence_digests", MAX_EVIDENCE_RECORDS),
        ("ballot_digests", MAX_BALLOTS),
        ("attestation_digests", MAX_ATTESTATIONS),
    ):
        values = _wire_list(snapshot[field], maximum=maximum)
        if any(type(item) is not str or not _SHA256.fullmatch(item) for item in values):
            raise EvidenceAttestationError("observation_digest_list")
        if values != sorted(set(values)):
            raise EvidenceAttestationError("observation_digest_list")
        snapshot[field] = values
    literal_flags = {
        "claimed_provenance_only": True,
        "source_set_completeness_verified": False,
        "positive_admission_ready": False,
        "activation_admission_advised": False,
        "activation_performed": False,
        "routing_influence_applied": False,
        "advisory_only": True,
        "authority_granted": False,
    }
    if any(
        type(snapshot[name]) is not bool or snapshot[name] is not expected
        for name, expected in literal_flags.items()
    ):
        raise EvidenceAttestationError("observation_authority_flags")
    if type(snapshot["claimed_provenance_consensus"]) is not dict:
        raise EvidenceAttestationError("claimed_consensus_not_dict")
    return snapshot


def verify_attested_inhibitory_consensus(
    value: object,
    *,
    query_digest: str,
    decision_digest: str,
    candidate_digest: str,
    activation_head_digest: str,
    expected_trust_registry_head_digest: str,
    expected_activation_scope_digest: str,
    expected_admission_challenge_digest: str,
    evidence_records: object,
    ballots: object,
    attestations: object,
    required_independent_support: int,
    key_lookup: KeyLookup,
) -> tuple[bool, Optional[str]]:
    """Re-authenticate exact sources and recompute the observer result."""

    try:
        parsed = _canonicalize_observation_shell(value)
        ok, _ = verify_consensus_evaluation(
            parsed["claimed_provenance_consensus"],
            query_digest=query_digest,
            decision_digest=decision_digest,
            candidate_digest=candidate_digest,
            activation_head_digest=activation_head_digest,
            evidence_records=evidence_records,
            ballots=ballots,
            required_independent_support=required_independent_support,
        )
        if not ok:
            raise EvidenceAttestationError("claimed_consensus_invalid")
        if (
            parsed["claimed_provenance_consensus"]["evaluation_digest"]
            != parsed["claimed_provenance_consensus_digest"]
        ):
            raise EvidenceAttestationError("claimed_consensus_digest_mismatch")
        unsigned = dict(parsed)
        claimed_observation_digest = unsigned.pop("observation_digest")
        if claimed_observation_digest != sha256_digest(
            {"domain": OBSERVATION_DIGEST_DOMAIN, **unsigned}
        ):
            raise EvidenceAttestationError("observation_digest_mismatch")
        recomputed = evaluate_attested_inhibitory_consensus(
            query_digest=query_digest,
            decision_digest=decision_digest,
            candidate_digest=candidate_digest,
            activation_head_digest=activation_head_digest,
            expected_trust_registry_head_digest=expected_trust_registry_head_digest,
            expected_activation_scope_digest=expected_activation_scope_digest,
            expected_admission_challenge_digest=(
                expected_admission_challenge_digest
            ),
            evidence_records=evidence_records,
            ballots=ballots,
            attestations=attestations,
            required_independent_support=required_independent_support,
            key_lookup=key_lookup,
        )
    except EvidenceAttestationError as exc:
        return False, str(exc)
    if parsed != recomputed:
        return False, "observation_recompute_mismatch"
    return True, None
