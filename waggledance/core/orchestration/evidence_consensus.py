# SPDX-License-Identifier: BUSL-1.1
"""Pure evidence-diversity and inhibitory-consensus contracts.

This module is an admission *measurement* layer, never an authority layer.  It
binds reviewer provenance to one query/decision/candidate/activation-head and
evaluates typed ballots without clocks, I/O, randomness, ambient registry
reads, or side effects.

The independence rule is deliberately conservative.  Two evidence records are
correlated when they share *any* declared provenance dimension (reviewer
lineage, model, provider, tool, data/corpus, host, or policy).  Correlation is
transitive, so a connected component counts once.  This prevents aliases,
replicas, or a crowd of agents backed by the same substrate from manufacturing
quorum.  The contract proves only consistency of the supplied digest claims;
authenticating those claims belongs to a signed evidence layer.

Within one correlated component the most inhibitory valid ballot wins:
``veto > stop > abstain > support``.  A stop or veto anywhere latches the whole
evaluation closed.  Malformed, unbound, stale-head, or missing-evidence input
also fails closed.  Negative evidence is retained in the result rather than
being averaged away.

No confidence number is emitted.  The only positive measure is the count of
independent provenance components supporting the decision; raw participant or
ballot count is audit metadata and never changes quorum.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from waggledance.core.magma.canonical import sha256_digest

EVIDENCE_SCHEMA = "wd.evidence_diversity.v1"
BALLOT_SCHEMA = "wd.inhibitory_ballot.v1"
EVALUATION_SCHEMA = "wd.inhibitory_consensus_evaluation.v1"

EVIDENCE_DIGEST_DOMAIN = "wd.evidence_diversity.digest.v1"
BALLOT_DIGEST_DOMAIN = "wd.inhibitory_ballot.digest.v1"
PROVENANCE_GROUP_DIGEST_DOMAIN = "wd.provenance_group.digest.v1"
EVALUATION_DIGEST_DOMAIN = "wd.inhibitory_consensus_evaluation.digest.v1"

MAX_EVIDENCE_RECORDS = 128
MAX_BALLOTS = 256
MAX_REQUIRED_SUPPORT = 128

BALLOT_TYPES = frozenset({"support", "abstain", "stop", "veto"})
_INHIBITION_RANK = {
    "support": 0,
    "abstain": 1,
    "stop": 2,
    "veto": 3,
}
_INVALID_REASON_ALLOWLIST = frozenset(
    {
        "evidence:not_sequence",
        "evidence:count_exceeded",
        "evidence:malformed",
        *{
            f"evidence:{field}_mismatch"
            for field in (
                "query_digest",
                "decision_digest",
                "candidate_digest",
                "activation_head_digest",
            )
        },
        "ballot:not_sequence",
        "ballot:count_exceeded",
        "ballot:malformed",
        *{
            f"ballot:{field}_mismatch"
            for field in (
                "query_digest",
                "decision_digest",
                "candidate_digest",
                "activation_head_digest",
            )
        },
        "ballot:evidence_missing",
        "ballot:evidence_binding_mismatch",
    }
)
_BLOCKER_REASON_ALLOWLIST = frozenset(
    {
        "invalid_input",
        "silence",
        "insufficient_independent_support",
        "stop_latched",
        "veto_latched",
    }
)

PROVENANCE_DIMENSIONS = (
    "reviewer_lineage_digest",
    "model_digest",
    "provider_digest",
    "tool_digest",
    "data_corpus_digest",
    "host_digest",
    # Reviewer-specific method/policy, not the common governance policy.  The
    # latter is already pinned by activation_head_digest and must be identical
    # for every ballot in one evaluation.
    "review_policy_digest",
)
_BINDING_FIELDS = (
    "query_digest",
    "decision_digest",
    "candidate_digest",
    "activation_head_digest",
)

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")

EVIDENCE_KEYS = frozenset(
    {
        "schema_version",
        *_BINDING_FIELDS,
        *PROVENANCE_DIMENSIONS,
        "evidence_digest",
    }
)
BALLOT_KEYS = frozenset(
    {
        "schema_version",
        "ballot_type",
        *_BINDING_FIELDS,
        "evidence_digest",
        "ballot_digest",
        "advisory_only",
    }
)
EVALUATION_KEYS = frozenset(
    {
        "schema_version",
        *_BINDING_FIELDS,
        "required_independent_support",
        "submitted_evidence_count",
        "unique_evidence_count",
        "submitted_ballot_count",
        "unique_ballot_count",
        "independent_provenance_count",
        "independent_support_count",
        "independent_abstain_count",
        "independent_stop_count",
        "independent_veto_count",
        "support_group_digests",
        "abstain_group_digests",
        "stop_group_digests",
        "veto_group_digests",
        "stop_evidence_digests",
        "veto_evidence_digests",
        "negative_evidence_digests",
        "invalid_item_count",
        "invalid_reasons",
        "blocker_reasons",
        "quorum_reached",
        "stop_latched",
        "veto_latched",
        "acceptance_blocked",
        "acceptance_advised",
        "advisory_only",
        "authority_granted",
        "evaluation_digest",
    }
)


class EvidenceConsensusError(ValueError):
    """A value is outside the pure evidence-consensus contracts."""


def _wire_dict(value: object, *, exact_key_count: int) -> Optional[dict]:
    """Return a private copy of an exact decoded-JSON object.

    Arbitrary mappings and dict subclasses are rejected before invoking their
    potentially hostile protocols.  Exact-str keys prevent a ``str`` subclass
    from impersonating a canonical field name.
    """

    if type(value) is not dict:
        return None
    # Reject an oversized exact built-in before allocating a private copy.
    # ``dict.__len__`` cannot dispatch to attacker-controlled subclass code
    # because subclasses were rejected above.
    if dict.__len__(value) > exact_key_count:
        # An empty bounded sentinel lets the caller report an exact-keyset
        # refusal without copying the oversized input.
        return {}
    snapshot = value.copy()
    if any(type(key) is not str for key in snapshot):
        return None
    return snapshot


def _wire_list(value: object, *, maximum: int) -> Optional[list]:
    """Copy a bounded exact decoded-JSON list.

    The length check intentionally happens before the allocation.  Tuples are
    useful as internal Python values but are not JSON wire values and are
    therefore refused at this contract boundary.
    """

    if type(value) is not list:
        return None
    if list.__len__(value) > maximum:
        return None
    return value.copy()


def _require_digest(value: object, label: str) -> str:
    if type(value) is not str or not _SHA256.fullmatch(value):
        raise EvidenceConsensusError(
            f"{label} must be a sha256:<64 lowercase hex> digest"
        )
    return value


def _require_bool(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise EvidenceConsensusError(f"{label} must be an exact bool")
    return value


def _require_count(value: object, label: str, *, maximum: int) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise EvidenceConsensusError(
            f"{label} must be an exact int within 0..{maximum}"
        )
    return value


def _validated_bindings(
    *,
    query_digest: object,
    decision_digest: object,
    candidate_digest: object,
    activation_head_digest: object,
) -> dict[str, str]:
    return {
        "query_digest": _require_digest(query_digest, "query_digest"),
        "decision_digest": _require_digest(decision_digest, "decision_digest"),
        "candidate_digest": _require_digest(candidate_digest, "candidate_digest"),
        "activation_head_digest": _require_digest(
            activation_head_digest, "activation_head_digest"
        ),
    }


def derive_evidence_digest(
    *,
    query_digest: str,
    decision_digest: str,
    candidate_digest: str,
    activation_head_digest: str,
    reviewer_lineage_digest: str,
    model_digest: str,
    provider_digest: str,
    tool_digest: str,
    data_corpus_digest: str,
    host_digest: str,
    review_policy_digest: str,
) -> str:
    """Content-address one binding and all seven provenance dimensions."""

    bindings = _validated_bindings(
        query_digest=query_digest,
        decision_digest=decision_digest,
        candidate_digest=candidate_digest,
        activation_head_digest=activation_head_digest,
    )
    dimensions = {
        "reviewer_lineage_digest": _require_digest(
            reviewer_lineage_digest, "reviewer_lineage_digest"
        ),
        "model_digest": _require_digest(model_digest, "model_digest"),
        "provider_digest": _require_digest(provider_digest, "provider_digest"),
        "tool_digest": _require_digest(tool_digest, "tool_digest"),
        "data_corpus_digest": _require_digest(
            data_corpus_digest, "data_corpus_digest"
        ),
        "host_digest": _require_digest(host_digest, "host_digest"),
        "review_policy_digest": _require_digest(
            review_policy_digest, "review_policy_digest"
        ),
    }
    return sha256_digest(
        {
            "domain": EVIDENCE_DIGEST_DOMAIN,
            "schema_version": EVIDENCE_SCHEMA,
            **bindings,
            **dimensions,
        }
    )


@dataclass(frozen=True)
class EvidenceDiversityV1:
    """Immutable, content-addressed provenance claim with zero authority."""

    query_digest: str
    decision_digest: str
    candidate_digest: str
    activation_head_digest: str
    reviewer_lineage_digest: str
    model_digest: str
    provider_digest: str
    tool_digest: str
    data_corpus_digest: str
    host_digest: str
    review_policy_digest: str
    evidence_digest: str
    schema_version: str = EVIDENCE_SCHEMA

    def __post_init__(self) -> None:
        if type(self.schema_version) is not str or self.schema_version != EVIDENCE_SCHEMA:
            raise EvidenceConsensusError("evidence schema_version refused")
        _require_digest(self.evidence_digest, "evidence_digest")
        expected = derive_evidence_digest(
            **{
                name: getattr(self, name)
                for name in (*_BINDING_FIELDS, *PROVENANCE_DIMENSIONS)
            }
        )
        if self.evidence_digest != expected:
            raise EvidenceConsensusError(
                "evidence_digest does not match the derived digest"
            )

    def to_mapping(self) -> dict[str, str]:
        return {
            "schema_version": self.schema_version,
            **{name: getattr(self, name) for name in _BINDING_FIELDS},
            **{name: getattr(self, name) for name in PROVENANCE_DIMENSIONS},
            "evidence_digest": self.evidence_digest,
        }


def build_evidence_diversity(
    *,
    query_digest: str,
    decision_digest: str,
    candidate_digest: str,
    activation_head_digest: str,
    reviewer_lineage_digest: str,
    model_digest: str,
    provider_digest: str,
    tool_digest: str,
    data_corpus_digest: str,
    host_digest: str,
    review_policy_digest: str,
) -> EvidenceDiversityV1:
    fields = {
        "query_digest": query_digest,
        "decision_digest": decision_digest,
        "candidate_digest": candidate_digest,
        "activation_head_digest": activation_head_digest,
        "reviewer_lineage_digest": reviewer_lineage_digest,
        "model_digest": model_digest,
        "provider_digest": provider_digest,
        "tool_digest": tool_digest,
        "data_corpus_digest": data_corpus_digest,
        "host_digest": host_digest,
        "review_policy_digest": review_policy_digest,
    }
    return EvidenceDiversityV1(
        **fields,
        evidence_digest=derive_evidence_digest(**fields),
    )


def parse_evidence_diversity(value: object) -> dict[str, str]:
    evidence = _wire_dict(value, exact_key_count=len(EVIDENCE_KEYS))
    if evidence is None:
        raise EvidenceConsensusError("evidence must be an exact dict")
    if set(evidence) != EVIDENCE_KEYS:
        raise EvidenceConsensusError("evidence keyset")
    if (
        type(evidence["schema_version"]) is not str
        or evidence["schema_version"] != EVIDENCE_SCHEMA
    ):
        raise EvidenceConsensusError("evidence schema_version refused")
    fields = {
        name: _require_digest(evidence[name], f"evidence.{name}")
        for name in (*_BINDING_FIELDS, *PROVENANCE_DIMENSIONS)
    }
    expected = derive_evidence_digest(**fields)
    if _require_digest(
        evidence["evidence_digest"], "evidence.evidence_digest"
    ) != expected:
        raise EvidenceConsensusError("evidence_digest mismatch")
    return {
        "schema_version": EVIDENCE_SCHEMA,
        **fields,
        "evidence_digest": expected,
    }


def verify_evidence_diversity(value: object) -> tuple[bool, Optional[str]]:
    try:
        parse_evidence_diversity(value)
        return True, None
    except EvidenceConsensusError as exc:
        return False, str(exc)


def derive_ballot_digest(
    *,
    ballot_type: str,
    query_digest: str,
    decision_digest: str,
    candidate_digest: str,
    activation_head_digest: str,
    evidence_digest: str,
) -> str:
    if type(ballot_type) is not str or ballot_type not in BALLOT_TYPES:
        raise EvidenceConsensusError("ballot_type refused")
    bindings = _validated_bindings(
        query_digest=query_digest,
        decision_digest=decision_digest,
        candidate_digest=candidate_digest,
        activation_head_digest=activation_head_digest,
    )
    evidence_digest = _require_digest(evidence_digest, "evidence_digest")
    return sha256_digest(
        {
            "domain": BALLOT_DIGEST_DOMAIN,
            "schema_version": BALLOT_SCHEMA,
            "ballot_type": ballot_type,
            **bindings,
            "evidence_digest": evidence_digest,
            "advisory_only": True,
        }
    )


@dataclass(frozen=True)
class InhibitoryBallotV1:
    """A typed advisory ballot bound to immutable review evidence."""

    ballot_type: str
    query_digest: str
    decision_digest: str
    candidate_digest: str
    activation_head_digest: str
    evidence_digest: str
    ballot_digest: str
    advisory_only: bool = True
    schema_version: str = BALLOT_SCHEMA

    def __post_init__(self) -> None:
        if type(self.schema_version) is not str or self.schema_version != BALLOT_SCHEMA:
            raise EvidenceConsensusError("ballot schema_version refused")
        if type(self.advisory_only) is not bool or self.advisory_only is not True:
            raise EvidenceConsensusError("ballot must remain advisory_only")
        _require_digest(self.ballot_digest, "ballot_digest")
        expected = derive_ballot_digest(
            ballot_type=self.ballot_type,
            query_digest=self.query_digest,
            decision_digest=self.decision_digest,
            candidate_digest=self.candidate_digest,
            activation_head_digest=self.activation_head_digest,
            evidence_digest=self.evidence_digest,
        )
        if self.ballot_digest != expected:
            raise EvidenceConsensusError("ballot_digest mismatch")

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "ballot_type": self.ballot_type,
            **{name: getattr(self, name) for name in _BINDING_FIELDS},
            "evidence_digest": self.evidence_digest,
            "ballot_digest": self.ballot_digest,
            "advisory_only": self.advisory_only,
        }


def build_inhibitory_ballot(
    *, ballot_type: str, evidence: object
) -> InhibitoryBallotV1:
    if type(evidence) is EvidenceDiversityV1:
        evidence = evidence.to_mapping()
    parsed = parse_evidence_diversity(evidence)
    fields = {name: parsed[name] for name in _BINDING_FIELDS}
    ballot_digest = derive_ballot_digest(
        ballot_type=ballot_type,
        **fields,
        evidence_digest=parsed["evidence_digest"],
    )
    return InhibitoryBallotV1(
        ballot_type=ballot_type,
        **fields,
        evidence_digest=parsed["evidence_digest"],
        ballot_digest=ballot_digest,
    )


def parse_inhibitory_ballot(value: object) -> dict[str, object]:
    ballot = _wire_dict(value, exact_key_count=len(BALLOT_KEYS))
    if ballot is None:
        raise EvidenceConsensusError("ballot must be an exact dict")
    if set(ballot) != BALLOT_KEYS:
        raise EvidenceConsensusError("ballot keyset")
    if (
        type(ballot["schema_version"]) is not str
        or ballot["schema_version"] != BALLOT_SCHEMA
    ):
        raise EvidenceConsensusError("ballot schema_version refused")
    ballot_type = ballot["ballot_type"]
    if type(ballot_type) is not str or ballot_type not in BALLOT_TYPES:
        raise EvidenceConsensusError("ballot_type refused")
    bindings = {
        name: _require_digest(ballot[name], f"ballot.{name}")
        for name in _BINDING_FIELDS
    }
    evidence_digest = _require_digest(
        ballot["evidence_digest"], "ballot.evidence_digest"
    )
    if _require_bool(ballot["advisory_only"], "ballot.advisory_only") is not True:
        raise EvidenceConsensusError("ballot must remain advisory_only")
    expected = derive_ballot_digest(
        ballot_type=ballot_type,
        **bindings,
        evidence_digest=evidence_digest,
    )
    if _require_digest(ballot["ballot_digest"], "ballot.ballot_digest") != expected:
        raise EvidenceConsensusError("ballot_digest mismatch")
    return {
        "schema_version": BALLOT_SCHEMA,
        "ballot_type": ballot_type,
        **bindings,
        "evidence_digest": evidence_digest,
        "ballot_digest": expected,
        "advisory_only": True,
    }


def verify_inhibitory_ballot(value: object) -> tuple[bool, Optional[str]]:
    try:
        parse_inhibitory_ballot(value)
        return True, None
    except EvidenceConsensusError as exc:
        return False, str(exc)


def _first_binding_mismatch(
    value: dict[str, object], expected: dict[str, str]
) -> Optional[str]:
    for field in _BINDING_FIELDS:
        if value[field] != expected[field]:
            return field
    return None


def _correlated_components(
    evidence_by_digest: dict[str, dict[str, str]],
) -> list[tuple[str, ...]]:
    """Return deterministic transitive components sharing any dimension."""

    pending = set(evidence_by_digest)
    components: list[tuple[str, ...]] = []
    while pending:
        seed = min(pending)
        pending.remove(seed)
        component = {seed}
        frontier = [seed]
        while frontier:
            current = frontier.pop()
            current_record = evidence_by_digest[current]
            correlated = []
            for candidate in sorted(pending):
                candidate_record = evidence_by_digest[candidate]
                if any(
                    current_record[field] == candidate_record[field]
                    for field in PROVENANCE_DIMENSIONS
                ):
                    correlated.append(candidate)
            for candidate in correlated:
                pending.remove(candidate)
                component.add(candidate)
                frontier.append(candidate)
        components.append(tuple(sorted(component)))
    return sorted(components)


def _derive_group_digest(evidence_digests: tuple[str, ...]) -> str:
    return sha256_digest(
        {
            "domain": PROVENANCE_GROUP_DIGEST_DOMAIN,
            "evidence_digests": list(evidence_digests),
        }
    )


def _derive_evaluation_digest(evaluation_without_digest: dict[str, object]) -> str:
    return sha256_digest(
        {
            "domain": EVALUATION_DIGEST_DOMAIN,
            **evaluation_without_digest,
        }
    )


def evaluate_inhibitory_consensus(
    *,
    query_digest: str,
    decision_digest: str,
    candidate_digest: str,
    activation_head_digest: str,
    evidence_records: object,
    ballots: object,
    required_independent_support: int,
) -> dict[str, object]:
    """Evaluate one bounded advisory ballot set, deterministically.

    Context digests and the threshold are trusted call-site *inputs* and must
    themselves be well-formed.  Untrusted evidence/ballot members are handled
    totally: malformed members are counted, make the result fail closed, and
    never contribute support.  A caller must supply a stable list/tuple snapshot
    for the duration of the call; this pure function cannot synchronize writers.
    """

    expected = _validated_bindings(
        query_digest=query_digest,
        decision_digest=decision_digest,
        candidate_digest=candidate_digest,
        activation_head_digest=activation_head_digest,
    )
    if (
        type(required_independent_support) is not int
        or not 1 <= required_independent_support <= MAX_REQUIRED_SUPPORT
    ):
        raise EvidenceConsensusError(
            "required_independent_support must be an exact int within "
            f"1..{MAX_REQUIRED_SUPPORT}"
        )

    invalid_item_count = 0
    invalid_reasons: set[str] = set()

    if type(evidence_records) is not list:
        submitted_evidence_count = 0
        invalid_item_count += 1
        invalid_reasons.add("evidence:not_sequence")
        evidence_input = []
    else:
        # Inspect the exact built-in's O(1) length before copying it.
        actual_evidence_count = list.__len__(evidence_records)
        submitted_evidence_count = min(
            actual_evidence_count, MAX_EVIDENCE_RECORDS + 1
        )
        if actual_evidence_count > MAX_EVIDENCE_RECORDS:
            invalid_item_count += 1
            invalid_reasons.add("evidence:count_exceeded")
            evidence_input = []
        else:
            evidence_input = evidence_records.copy()

    evidence_by_digest: dict[str, dict[str, str]] = {}
    for raw_evidence in evidence_input:
        try:
            evidence = parse_evidence_diversity(raw_evidence)
        except EvidenceConsensusError:
            invalid_item_count += 1
            invalid_reasons.add("evidence:malformed")
            continue
        mismatch = _first_binding_mismatch(evidence, expected)
        if mismatch is not None:
            invalid_item_count += 1
            invalid_reasons.add(f"evidence:{mismatch}_mismatch")
            continue
        evidence_by_digest.setdefault(evidence["evidence_digest"], evidence)

    if type(ballots) is not list:
        submitted_ballot_count = 0
        invalid_item_count += 1
        invalid_reasons.add("ballot:not_sequence")
        ballot_input = []
    else:
        # Inspect the exact built-in's O(1) length before copying it.
        actual_ballot_count = list.__len__(ballots)
        submitted_ballot_count = min(actual_ballot_count, MAX_BALLOTS + 1)
        if actual_ballot_count > MAX_BALLOTS:
            invalid_item_count += 1
            invalid_reasons.add("ballot:count_exceeded")
            ballot_input = []
        else:
            ballot_input = ballots.copy()

    ballot_by_digest: dict[str, dict[str, object]] = {}
    for raw_ballot in ballot_input:
        try:
            ballot = parse_inhibitory_ballot(raw_ballot)
        except EvidenceConsensusError:
            invalid_item_count += 1
            invalid_reasons.add("ballot:malformed")
            continue
        mismatch = _first_binding_mismatch(ballot, expected)
        if mismatch is not None:
            invalid_item_count += 1
            invalid_reasons.add(f"ballot:{mismatch}_mismatch")
            continue
        evidence = evidence_by_digest.get(ballot["evidence_digest"])
        if evidence is None:
            invalid_item_count += 1
            invalid_reasons.add("ballot:evidence_missing")
            continue
        # Defense in depth: the ballot and its resolved evidence must bind the
        # same exact context, not merely share a claimed evidence digest.
        if any(ballot[field] != evidence[field] for field in _BINDING_FIELDS):
            invalid_item_count += 1
            invalid_reasons.add("ballot:evidence_binding_mismatch")
            continue
        ballot_by_digest.setdefault(ballot["ballot_digest"], ballot)

    ballots_by_evidence: dict[str, set[str]] = {}
    for ballot in ballot_by_digest.values():
        ballots_by_evidence.setdefault(ballot["evidence_digest"], set()).add(
            ballot["ballot_type"]
        )

    voted_evidence = {
        digest: evidence_by_digest[digest]
        for digest in ballots_by_evidence
    }
    groups_by_type: dict[str, list[str]] = {
        ballot_type: [] for ballot_type in BALLOT_TYPES
    }
    for component in _correlated_components(voted_evidence):
        component_types = {
            ballot_type
            for evidence_digest in component
            for ballot_type in ballots_by_evidence[evidence_digest]
        }
        effective_type = max(component_types, key=_INHIBITION_RANK.__getitem__)
        groups_by_type[effective_type].append(_derive_group_digest(component))
    for group_digests in groups_by_type.values():
        group_digests.sort()

    stop_evidence_digests = sorted(
        {
            ballot["evidence_digest"]
            for ballot in ballot_by_digest.values()
            if ballot["ballot_type"] == "stop"
        }
    )
    veto_evidence_digests = sorted(
        {
            ballot["evidence_digest"]
            for ballot in ballot_by_digest.values()
            if ballot["ballot_type"] == "veto"
        }
    )
    negative_evidence_digests = sorted(
        set(stop_evidence_digests) | set(veto_evidence_digests)
    )

    support_count = len(groups_by_type["support"])
    abstain_count = len(groups_by_type["abstain"])
    stop_count = len(groups_by_type["stop"])
    veto_count = len(groups_by_type["veto"])
    independent_count = support_count + abstain_count + stop_count + veto_count

    input_valid = invalid_item_count == 0
    quorum_reached = (
        input_valid and support_count >= required_independent_support
    )
    stop_latched = bool(stop_evidence_digests)
    veto_latched = bool(veto_evidence_digests)

    blocker_reasons: set[str] = set()
    if not input_valid:
        blocker_reasons.add("invalid_input")
    if not ballot_by_digest:
        blocker_reasons.add("silence")
    if support_count < required_independent_support:
        blocker_reasons.add("insufficient_independent_support")
    if stop_latched:
        blocker_reasons.add("stop_latched")
    if veto_latched:
        blocker_reasons.add("veto_latched")
    acceptance_advised = quorum_reached and not stop_latched and not veto_latched

    evaluation: dict[str, object] = {
        "schema_version": EVALUATION_SCHEMA,
        **expected,
        "required_independent_support": required_independent_support,
        "submitted_evidence_count": submitted_evidence_count,
        "unique_evidence_count": len(evidence_by_digest),
        "submitted_ballot_count": submitted_ballot_count,
        "unique_ballot_count": len(ballot_by_digest),
        "independent_provenance_count": independent_count,
        "independent_support_count": support_count,
        "independent_abstain_count": abstain_count,
        "independent_stop_count": stop_count,
        "independent_veto_count": veto_count,
        "support_group_digests": groups_by_type["support"],
        "abstain_group_digests": groups_by_type["abstain"],
        "stop_group_digests": groups_by_type["stop"],
        "veto_group_digests": groups_by_type["veto"],
        "stop_evidence_digests": stop_evidence_digests,
        "veto_evidence_digests": veto_evidence_digests,
        "negative_evidence_digests": negative_evidence_digests,
        "invalid_item_count": invalid_item_count,
        "invalid_reasons": sorted(invalid_reasons),
        "blocker_reasons": sorted(blocker_reasons),
        "quorum_reached": quorum_reached,
        "stop_latched": stop_latched,
        "veto_latched": veto_latched,
        "acceptance_blocked": not acceptance_advised,
        "acceptance_advised": acceptance_advised,
        "advisory_only": True,
        "authority_granted": False,
    }
    evaluation["evaluation_digest"] = _derive_evaluation_digest(evaluation)
    return evaluation


def _digest_list(value: object, label: str, *, maximum: int) -> list[str]:
    values = _wire_list(value, maximum=maximum)
    if values is None:
        raise EvidenceConsensusError(f"{label} must be a bounded list")
    parsed = [_require_digest(item, f"{label}[]") for item in values]
    if parsed != sorted(set(parsed)):
        raise EvidenceConsensusError(f"{label} must be sorted and unique")
    return parsed


def _reason_list(
    value: object,
    label: str,
    *,
    allowlist: frozenset[str],
) -> list[str]:
    values = _wire_list(value, maximum=32)
    if values is None:
        raise EvidenceConsensusError(f"{label} must be a bounded list")
    if any(type(item) is not str or not 1 <= len(item) <= 96 for item in values):
        raise EvidenceConsensusError(f"{label} entries refused")
    if any(item not in allowlist for item in values):
        raise EvidenceConsensusError(f"{label} entry not allowlisted")
    if values != sorted(set(values)):
        raise EvidenceConsensusError(f"{label} must be sorted and unique")
    return values


def _parse_consensus_evaluation_structure(value: object) -> dict[str, object]:
    """Verify aggregate structure only, without authenticating its sources.

    This helper is deliberately private.  A self-addressed aggregate is not
    evidence that the ballots it describes ever existed.  Callers must use
    :func:`verify_consensus_evaluation`, which recomputes from the exact source
    evidence and ballots under caller-supplied context bindings.
    """

    evaluation = _wire_dict(value, exact_key_count=len(EVALUATION_KEYS))
    if evaluation is None:
        raise EvidenceConsensusError("evaluation must be an exact dict")
    if set(evaluation) != EVALUATION_KEYS:
        raise EvidenceConsensusError("evaluation keyset")
    if (
        type(evaluation["schema_version"]) is not str
        or evaluation["schema_version"] != EVALUATION_SCHEMA
    ):
        raise EvidenceConsensusError("evaluation schema_version refused")
    for field in _BINDING_FIELDS:
        _require_digest(evaluation[field], f"evaluation.{field}")

    required = _require_count(
        evaluation["required_independent_support"],
        "required_independent_support",
        maximum=MAX_REQUIRED_SUPPORT,
    )
    if required == 0:
        raise EvidenceConsensusError("required_independent_support must be positive")
    for field, maximum in (
        ("submitted_evidence_count", MAX_EVIDENCE_RECORDS + 1),
        ("unique_evidence_count", MAX_EVIDENCE_RECORDS),
        ("submitted_ballot_count", MAX_BALLOTS + 1),
        ("unique_ballot_count", MAX_BALLOTS),
        ("independent_provenance_count", MAX_BALLOTS),
        ("independent_support_count", MAX_BALLOTS),
        ("independent_abstain_count", MAX_BALLOTS),
        ("independent_stop_count", MAX_BALLOTS),
        ("independent_veto_count", MAX_BALLOTS),
        ("invalid_item_count", MAX_EVIDENCE_RECORDS + MAX_BALLOTS + 2),
    ):
        _require_count(evaluation[field], field, maximum=maximum)

    group_lists = {
        name: _digest_list(evaluation[name], name, maximum=MAX_BALLOTS)
        for name in (
            "support_group_digests",
            "abstain_group_digests",
            "stop_group_digests",
            "veto_group_digests",
        )
    }
    stop_evidence = _digest_list(
        evaluation["stop_evidence_digests"],
        "stop_evidence_digests",
        maximum=MAX_BALLOTS,
    )
    veto_evidence = _digest_list(
        evaluation["veto_evidence_digests"],
        "veto_evidence_digests",
        maximum=MAX_BALLOTS,
    )
    negative_evidence = _digest_list(
        evaluation["negative_evidence_digests"],
        "negative_evidence_digests",
        maximum=MAX_BALLOTS,
    )
    if negative_evidence != sorted(set(stop_evidence) | set(veto_evidence)):
        raise EvidenceConsensusError("negative evidence retention mismatch")

    invalid_reasons = _reason_list(
        evaluation["invalid_reasons"],
        "invalid_reasons",
        allowlist=_INVALID_REASON_ALLOWLIST,
    )
    blocker_reasons = _reason_list(
        evaluation["blocker_reasons"],
        "blocker_reasons",
        allowlist=_BLOCKER_REASON_ALLOWLIST,
    )
    counts = {
        "support": evaluation["independent_support_count"],
        "abstain": evaluation["independent_abstain_count"],
        "stop": evaluation["independent_stop_count"],
        "veto": evaluation["independent_veto_count"],
    }
    for ballot_type, count in counts.items():
        if count != len(group_lists[f"{ballot_type}_group_digests"]):
            raise EvidenceConsensusError(f"{ballot_type} group count mismatch")
    all_group_digests = [
        digest for values in group_lists.values() for digest in values
    ]
    if len(all_group_digests) != len(set(all_group_digests)):
        raise EvidenceConsensusError("provenance groups must be mutually disjoint")
    if evaluation["independent_provenance_count"] != sum(counts.values()):
        raise EvidenceConsensusError("independent provenance count mismatch")
    if evaluation["unique_evidence_count"] > evaluation["submitted_evidence_count"]:
        raise EvidenceConsensusError("unique evidence exceeds submitted evidence")
    if evaluation["unique_ballot_count"] > evaluation["submitted_ballot_count"]:
        raise EvidenceConsensusError("unique ballots exceed submitted ballots")
    if evaluation["independent_provenance_count"] > evaluation["unique_ballot_count"]:
        raise EvidenceConsensusError("provenance groups exceed unique ballots")
    if evaluation["independent_provenance_count"] > evaluation["unique_evidence_count"]:
        raise EvidenceConsensusError("provenance groups exceed unique evidence")

    bools = {
        field: _require_bool(evaluation[field], f"evaluation.{field}")
        for field in (
            "quorum_reached",
            "stop_latched",
            "veto_latched",
            "acceptance_blocked",
            "acceptance_advised",
            "advisory_only",
            "authority_granted",
        )
    }
    if bools["advisory_only"] is not True or bools["authority_granted"] is not False:
        raise EvidenceConsensusError("evaluation must remain advisory with no authority")
    input_valid = evaluation["invalid_item_count"] == 0
    expected_quorum = (
        input_valid and evaluation["independent_support_count"] >= required
    )
    if bools["quorum_reached"] != expected_quorum:
        raise EvidenceConsensusError("quorum invariant mismatch")
    if bools["stop_latched"] != bool(stop_evidence):
        raise EvidenceConsensusError("stop latch mismatch")
    if bools["veto_latched"] != bool(veto_evidence):
        raise EvidenceConsensusError("veto latch mismatch")
    expected_acceptance = (
        expected_quorum
        and not bools["stop_latched"]
        and not bools["veto_latched"]
    )
    if bools["acceptance_advised"] != expected_acceptance:
        raise EvidenceConsensusError("advisory acceptance invariant mismatch")
    if bools["acceptance_blocked"] != (not expected_acceptance):
        raise EvidenceConsensusError("advisory blocker invariant mismatch")
    if bool(invalid_reasons) != (evaluation["invalid_item_count"] > 0):
        raise EvidenceConsensusError("invalid reason/count mismatch")
    expected_blockers: set[str] = set()
    if not input_valid:
        expected_blockers.add("invalid_input")
    if evaluation["unique_ballot_count"] == 0:
        expected_blockers.add("silence")
    if evaluation["independent_support_count"] < required:
        expected_blockers.add("insufficient_independent_support")
    if bools["stop_latched"]:
        expected_blockers.add("stop_latched")
    if bools["veto_latched"]:
        expected_blockers.add("veto_latched")
    if blocker_reasons != sorted(expected_blockers):
        raise EvidenceConsensusError("blocker reason invariant mismatch")

    # Replace every nested collection with its validated private snapshot
    # before digesting and returning the normalized structure.
    for name, values in group_lists.items():
        evaluation[name] = values
    evaluation["stop_evidence_digests"] = stop_evidence
    evaluation["veto_evidence_digests"] = veto_evidence
    evaluation["negative_evidence_digests"] = negative_evidence
    evaluation["invalid_reasons"] = invalid_reasons
    evaluation["blocker_reasons"] = blocker_reasons

    claimed_digest = _require_digest(
        evaluation["evaluation_digest"], "evaluation.evaluation_digest"
    )
    unsigned = dict(evaluation)
    del unsigned["evaluation_digest"]
    if claimed_digest != _derive_evaluation_digest(unsigned):
        raise EvidenceConsensusError("evaluation_digest mismatch")
    return evaluation


def verify_consensus_evaluation(
    value: object,
    *,
    query_digest: str,
    decision_digest: str,
    candidate_digest: str,
    activation_head_digest: str,
    evidence_records: object,
    ballots: object,
    required_independent_support: int,
) -> tuple[bool, Optional[str]]:
    """Verify an aggregate by recomputing it from its exact bounded sources.

    The four context digests and quorum threshold are independent caller-owned
    expectations; they are never learned from ``value``.  Evidence provenance
    remains a structural digest claim until a higher layer authenticates the
    corresponding signed receipts.  Consequently success grants no authority.
    """

    try:
        parsed = _parse_consensus_evaluation_structure(value)
        recomputed = evaluate_inhibitory_consensus(
            query_digest=query_digest,
            decision_digest=decision_digest,
            candidate_digest=candidate_digest,
            activation_head_digest=activation_head_digest,
            evidence_records=evidence_records,
            ballots=ballots,
            required_independent_support=required_independent_support,
        )
    except EvidenceConsensusError as exc:
        return False, str(exc)
    if parsed != recomputed:
        return False, "evaluation does not match recomputed source evidence"
    return True, None
