# SPDX-License-Identifier: BUSL-1.1
"""Pure append-only completeness contracts for committed attestations.

The log is a bounded, deterministic *structural* commitment.  Given a log
snapshot and a log-head digest obtained independently by the caller,
``derive_committed_attestation_set`` proves which entries are the complete
committed set for one activation scope and admission challenge.  It does not
authenticate the head, signatures, evidence, ballots, lineage, or storage.

Every contract in this module is authority-free.  A valid entry, snapshot,
transition, or derived set cannot activate a candidate, mutate routing, or
grant runtime authority.  Persistence adapters remain responsible for an
atomic compare-and-swap against the independently trusted current head and
for replication/durability across cell failure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from waggledance.core.magma.canonical import sha256_digest

ATTESTATION_LOG_ENTRY_SCHEMA = "wd.attestation_log_entry.v1"
ATTESTATION_LOG_SNAPSHOT_SCHEMA = "wd.attestation_log_snapshot.v1"
COMMITTED_ATTESTATION_SET_SCHEMA = "wd.committed_attestation_set.v1"

ATTESTATION_LOG_ENTRY_DIGEST_DOMAIN = "wd.attestation_log_entry.digest.v1"
ATTESTATION_LOG_HEAD_DIGEST_DOMAIN = "wd.attestation_log_head.digest.v1"
COMMITTED_ATTESTATION_SET_DIGEST_DOMAIN = (
    "wd.committed_attestation_set.digest.v1"
)

INITIAL_PREVIOUS_LOG_HEAD_DIGEST = "sha256:" + "0" * 64
MAX_ATTESTATION_LOG_ENTRIES = 4_096
MAX_GENERATION = (1 << 63) - 1

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")

ATTESTATION_LOG_ENTRY_KEYS = frozenset(
    {
        "schema_version",
        "activation_scope_digest",
        "admission_challenge_digest",
        "evidence_digest",
        "ballot_digest",
        "attestation_digest",
        "reviewer_lineage_digest",
        "entry_digest",
        "advisory_only",
        "authority_granted",
    }
)
ATTESTATION_LOG_SNAPSHOT_KEYS = frozenset(
    {
        "schema_version",
        "generation",
        "previous_log_head_digest",
        "entries",
        "log_head_digest",
        "advisory_only",
        "authority_granted",
    }
)
COMMITTED_ATTESTATION_SET_KEYS = frozenset(
    {
        "schema_version",
        "log_head_digest",
        "activation_scope_digest",
        "admission_challenge_digest",
        "entries",
        "entry_count",
        "empty",
        "committed_attestation_set_digest",
        "advisory_only",
        "authority_granted",
    }
)


class AttestationLogContractError(ValueError):
    """A value is outside the pure attestation-log contracts."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _refuse(reason: str, message: str) -> None:
    raise AttestationLogContractError(reason, message)


def _wire_snapshot(value: object, keys: frozenset[str], label: str) -> dict:
    """Return a private copy of an exact decoded-JSON object."""

    if type(value) is not dict:
        _refuse(f"{label}_not_mapping", f"{label} must be an exact dict")
    # Bound before copying.  Exact built-in dict operations cannot dispatch to
    # an attacker-controlled mapping implementation.
    if dict.__len__(value) != len(keys):
        _refuse(f"{label}_keyset", f"{label} has a non-exact keyset")
    snapshot = value.copy()
    if any(type(key) is not str for key in snapshot):
        _refuse(f"{label}_not_mapping", f"{label} keys must be exact strings")
    if set(snapshot) != keys:
        _refuse(f"{label}_keyset", f"{label} has a non-exact keyset")
    return snapshot


def _require_schema(value: object, expected: str) -> str:
    if type(value) is not str or value != expected:
        _refuse("schema_version", f"schema_version must be {expected!r}")
    return value


def _require_digest(value: object, label: str) -> str:
    if type(value) is not str or not _SHA256.fullmatch(value):
        _refuse(label, f"{label} must be a sha256:<64 lowercase hex> digest")
    return value


def _require_generation(value: object) -> int:
    if type(value) is not int or not 0 <= value <= MAX_GENERATION:
        _refuse(
            "generation",
            f"generation must be an exact integer within 0..{MAX_GENERATION}",
        )
    return value


def _require_no_authority_flags(
    advisory_only: object, authority_granted: object, *, label: str
) -> tuple[bool, bool]:
    if type(advisory_only) is not bool or advisory_only is not True:
        _refuse(
            f"{label}_advisory_only",
            f"{label}.advisory_only must be literal true",
        )
    if type(authority_granted) is not bool or authority_granted is not False:
        _refuse(
            f"{label}_authority_granted",
            f"{label}.authority_granted must be literal false",
        )
    return True, False


def derive_attestation_log_entry_digest(
    *,
    activation_scope_digest: str,
    admission_challenge_digest: str,
    evidence_digest: str,
    ballot_digest: str,
    attestation_digest: str,
    reviewer_lineage_digest: str,
    advisory_only: bool = True,
    authority_granted: bool = False,
) -> str:
    """Derive one domain-separated, authority-free entry digest."""

    scope = _require_digest(activation_scope_digest, "activation_scope_digest")
    challenge = _require_digest(
        admission_challenge_digest, "admission_challenge_digest"
    )
    evidence = _require_digest(evidence_digest, "evidence_digest")
    ballot = _require_digest(ballot_digest, "ballot_digest")
    attestation = _require_digest(attestation_digest, "attestation_digest")
    lineage = _require_digest(
        reviewer_lineage_digest, "reviewer_lineage_digest"
    )
    advisory, authority = _require_no_authority_flags(
        advisory_only, authority_granted, label="entry"
    )
    return sha256_digest(
        {
            "domain": ATTESTATION_LOG_ENTRY_DIGEST_DOMAIN,
            "schema_version": ATTESTATION_LOG_ENTRY_SCHEMA,
            "activation_scope_digest": scope,
            "admission_challenge_digest": challenge,
            "evidence_digest": evidence,
            "ballot_digest": ballot,
            "attestation_digest": attestation,
            "reviewer_lineage_digest": lineage,
            "advisory_only": advisory,
            "authority_granted": authority,
        }
    )


@dataclass(frozen=True)
class AttestationLogEntryV1:
    """One content-addressed attestation commitment, never a grant."""

    activation_scope_digest: str
    admission_challenge_digest: str
    evidence_digest: str
    ballot_digest: str
    attestation_digest: str
    reviewer_lineage_digest: str
    entry_digest: str
    advisory_only: bool = True
    authority_granted: bool = False
    schema_version: str = ATTESTATION_LOG_ENTRY_SCHEMA

    def __post_init__(self) -> None:
        _require_schema(self.schema_version, ATTESTATION_LOG_ENTRY_SCHEMA)
        _require_digest(self.entry_digest, "entry_digest")
        expected = derive_attestation_log_entry_digest(
            activation_scope_digest=self.activation_scope_digest,
            admission_challenge_digest=self.admission_challenge_digest,
            evidence_digest=self.evidence_digest,
            ballot_digest=self.ballot_digest,
            attestation_digest=self.attestation_digest,
            reviewer_lineage_digest=self.reviewer_lineage_digest,
            advisory_only=self.advisory_only,
            authority_granted=self.authority_granted,
        )
        if self.entry_digest != expected:
            _refuse("entry_digest_mismatch", "entry_digest does not match content")

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "activation_scope_digest": self.activation_scope_digest,
            "admission_challenge_digest": self.admission_challenge_digest,
            "evidence_digest": self.evidence_digest,
            "ballot_digest": self.ballot_digest,
            "attestation_digest": self.attestation_digest,
            "reviewer_lineage_digest": self.reviewer_lineage_digest,
            "entry_digest": self.entry_digest,
            "advisory_only": self.advisory_only,
            "authority_granted": self.authority_granted,
        }


def build_attestation_log_entry(
    *,
    activation_scope_digest: str,
    admission_challenge_digest: str,
    evidence_digest: str,
    ballot_digest: str,
    attestation_digest: str,
    reviewer_lineage_digest: str,
) -> AttestationLogEntryV1:
    """Build one exact immutable entry with literal no-authority flags."""

    return AttestationLogEntryV1(
        activation_scope_digest=activation_scope_digest,
        admission_challenge_digest=admission_challenge_digest,
        evidence_digest=evidence_digest,
        ballot_digest=ballot_digest,
        attestation_digest=attestation_digest,
        reviewer_lineage_digest=reviewer_lineage_digest,
        entry_digest=derive_attestation_log_entry_digest(
            activation_scope_digest=activation_scope_digest,
            admission_challenge_digest=admission_challenge_digest,
            evidence_digest=evidence_digest,
            ballot_digest=ballot_digest,
            attestation_digest=attestation_digest,
            reviewer_lineage_digest=reviewer_lineage_digest,
        ),
    )


def parse_attestation_log_entry(value: object) -> AttestationLogEntryV1:
    """Parse and verify an entry into a private immutable copy."""

    if type(value) is AttestationLogEntryV1:
        try:
            return AttestationLogEntryV1(
                schema_version=value.schema_version,
                activation_scope_digest=value.activation_scope_digest,
                admission_challenge_digest=value.admission_challenge_digest,
                evidence_digest=value.evidence_digest,
                ballot_digest=value.ballot_digest,
                attestation_digest=value.attestation_digest,
                reviewer_lineage_digest=value.reviewer_lineage_digest,
                entry_digest=value.entry_digest,
                advisory_only=value.advisory_only,
                authority_granted=value.authority_granted,
            )
        except AttributeError:
            _refuse("entry_malformed_instance", "entry instance is malformed")
    snapshot = _wire_snapshot(value, ATTESTATION_LOG_ENTRY_KEYS, "entry")
    return AttestationLogEntryV1(
        schema_version=snapshot["schema_version"],
        activation_scope_digest=snapshot["activation_scope_digest"],
        admission_challenge_digest=snapshot["admission_challenge_digest"],
        evidence_digest=snapshot["evidence_digest"],
        ballot_digest=snapshot["ballot_digest"],
        attestation_digest=snapshot["attestation_digest"],
        reviewer_lineage_digest=snapshot["reviewer_lineage_digest"],
        entry_digest=snapshot["entry_digest"],
        advisory_only=snapshot["advisory_only"],
        authority_granted=snapshot["authority_granted"],
    )


def verify_attestation_log_entry(value: object) -> tuple[bool, Optional[str]]:
    try:
        parse_attestation_log_entry(value)
    except AttestationLogContractError as exc:
        return False, exc.reason
    return True, None


def _bounded_entry_sequence(
    value: object,
    *,
    normalize: bool,
    container_types: tuple[type, ...],
    wire_items: bool = False,
) -> tuple[AttestationLogEntryV1, ...]:
    if type(value) not in container_types:
        allowed = " or ".join(item.__name__ for item in container_types)
        _refuse("entries_type", f"entries must be an exact {allowed}")
    if len(value) > MAX_ATTESTATION_LOG_ENTRIES:
        _refuse(
            "entries_count_exceeded",
            f"entries exceeds its {MAX_ATTESTATION_LOG_ENTRIES}-item bound",
        )
    # Copy the bounded aggregate before parsing nested values.  The caller owns
    # input quiescence during this synchronous boundary call.
    aggregate = tuple(value)
    if len(aggregate) > MAX_ATTESTATION_LOG_ENTRIES:
        _refuse(
            "entries_count_exceeded",
            f"entries exceeds its {MAX_ATTESTATION_LOG_ENTRIES}-item bound",
        )
    if wire_items and any(type(item) is not dict for item in aggregate):
        _refuse("entry_not_mapping", "wire entries must contain exact dicts")
    parsed = tuple(parse_attestation_log_entry(item) for item in aggregate)
    _validate_entry_relations(parsed)
    ordered = tuple(sorted(parsed, key=lambda item: item.entry_digest))
    if not normalize and parsed != ordered:
        _refuse("entries_order", "entries must be in canonical entry-digest order")
    return ordered


def _validate_entry_relations(entries: tuple[AttestationLogEntryV1, ...]) -> None:
    entry_digests: set[str] = set()
    attestation_digests: dict[str, str] = {}
    ballot_slots: dict[tuple[str, str, str], str] = {}

    for entry in entries:
        if entry.entry_digest in entry_digests:
            _refuse("duplicate_entry_digest", "entries contains a duplicate digest")
        entry_digests.add(entry.entry_digest)

        prior = attestation_digests.setdefault(
            entry.attestation_digest, entry.entry_digest
        )
        if prior != entry.entry_digest:
            _refuse(
                "ambiguous_attestation_digest",
                "one attestation_digest maps to multiple log entries",
            )

        # One ballot gets one immutable attestation slot in each independently
        # pinned activation-scope/challenge pair.  Reviewer lineage remains a
        # signed consistency binding, not a cardinality key: one reviewer may
        # legitimately produce more than one distinct ballot.
        ballot_slot = (
            entry.activation_scope_digest,
            entry.admission_challenge_digest,
            entry.ballot_digest,
        )
        prior = ballot_slots.setdefault(ballot_slot, entry.entry_digest)
        if prior != entry.entry_digest:
            _refuse(
                "ambiguous_ballot_slot",
                "one ballot maps to multiple attestations for the same scope/challenge",
            )


def _validate_snapshot_fields(
    *,
    generation: object,
    previous_log_head_digest: object,
    entries: object,
    advisory_only: object,
    authority_granted: object,
    normalize: bool,
    container_types: tuple[type, ...],
    wire_items: bool = False,
) -> tuple[int, str, tuple[AttestationLogEntryV1, ...], bool, bool]:
    parsed_generation = _require_generation(generation)
    previous = _require_digest(
        previous_log_head_digest, "previous_log_head_digest"
    )
    parsed_entries = _bounded_entry_sequence(
        entries,
        normalize=normalize,
        container_types=container_types,
        wire_items=wire_items,
    )
    advisory, authority = _require_no_authority_flags(
        advisory_only, authority_granted, label="snapshot"
    )
    if (
        parsed_generation == 0
        and previous != INITIAL_PREVIOUS_LOG_HEAD_DIGEST
    ):
        _refuse(
            "initial_previous_log_head",
            "generation zero must use the initial predecessor sentinel",
        )
    if (
        parsed_generation > 0
        and previous == INITIAL_PREVIOUS_LOG_HEAD_DIGEST
    ):
        _refuse(
            "noninitial_previous_log_head",
            "a noninitial generation must bind a real predecessor head",
        )
    return parsed_generation, previous, parsed_entries, advisory, authority


def derive_attestation_log_head_digest(
    *,
    generation: int,
    previous_log_head_digest: str,
    entries: object,
    advisory_only: bool = True,
    authority_granted: bool = False,
) -> str:
    """Derive a head over the canonical, complete entry set."""

    generation, previous, parsed, advisory, authority = _validate_snapshot_fields(
        generation=generation,
        previous_log_head_digest=previous_log_head_digest,
        entries=entries,
        advisory_only=advisory_only,
        authority_granted=authority_granted,
        normalize=True,
        container_types=(list, tuple),
    )
    return sha256_digest(
        {
            "domain": ATTESTATION_LOG_HEAD_DIGEST_DOMAIN,
            "schema_version": ATTESTATION_LOG_SNAPSHOT_SCHEMA,
            "generation": generation,
            "previous_log_head_digest": previous,
            "entries": [item.to_mapping() for item in parsed],
            "advisory_only": advisory,
            "authority_granted": authority,
        }
    )


@dataclass(frozen=True)
class AttestationLogSnapshotV1:
    """One immutable, bounded view of the complete committed log."""

    generation: int
    previous_log_head_digest: str
    entries: tuple[AttestationLogEntryV1, ...]
    log_head_digest: str
    advisory_only: bool = True
    authority_granted: bool = False
    schema_version: str = ATTESTATION_LOG_SNAPSHOT_SCHEMA

    def __post_init__(self) -> None:
        _require_schema(self.schema_version, ATTESTATION_LOG_SNAPSHOT_SCHEMA)
        _require_digest(self.log_head_digest, "log_head_digest")
        if type(self.entries) is not tuple or any(
            type(item) is not AttestationLogEntryV1 for item in self.entries
        ):
            _refuse(
                "entries_item_type",
                "AttestationLogSnapshotV1 stores exact entries in a tuple",
            )
        generation, previous, entries, advisory, authority = (
            _validate_snapshot_fields(
                generation=self.generation,
                previous_log_head_digest=self.previous_log_head_digest,
                entries=self.entries,
                advisory_only=self.advisory_only,
                authority_granted=self.authority_granted,
                normalize=False,
                container_types=(tuple,),
            )
        )
        expected = derive_attestation_log_head_digest(
            generation=generation,
            previous_log_head_digest=previous,
            entries=entries,
            advisory_only=advisory,
            authority_granted=authority,
        )
        if self.log_head_digest != expected:
            _refuse(
                "log_head_digest_mismatch",
                "log_head_digest does not match snapshot content",
            )

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "generation": self.generation,
            "previous_log_head_digest": self.previous_log_head_digest,
            "entries": [item.to_mapping() for item in self.entries],
            "log_head_digest": self.log_head_digest,
            "advisory_only": self.advisory_only,
            "authority_granted": self.authority_granted,
        }


def build_attestation_log_snapshot(
    *,
    generation: int,
    previous_log_head_digest: str,
    entries: object,
) -> AttestationLogSnapshotV1:
    """Build a deterministic snapshot, normalizing its bounded entry order."""

    generation, previous, parsed, _, _ = _validate_snapshot_fields(
        generation=generation,
        previous_log_head_digest=previous_log_head_digest,
        entries=entries,
        advisory_only=True,
        authority_granted=False,
        normalize=True,
        container_types=(list, tuple),
    )
    return AttestationLogSnapshotV1(
        generation=generation,
        previous_log_head_digest=previous,
        entries=parsed,
        log_head_digest=derive_attestation_log_head_digest(
            generation=generation,
            previous_log_head_digest=previous,
            entries=parsed,
        ),
    )


def parse_attestation_log_snapshot(value: object) -> AttestationLogSnapshotV1:
    """Parse and verify a snapshot into a private immutable copy."""

    if type(value) is AttestationLogSnapshotV1:
        try:
            # Reparse every entry rather than returning a caller-owned object.
            entries = _bounded_entry_sequence(
                value.entries,
                normalize=False,
                container_types=(tuple,),
            )
            return AttestationLogSnapshotV1(
                schema_version=value.schema_version,
                generation=value.generation,
                previous_log_head_digest=value.previous_log_head_digest,
                entries=entries,
                log_head_digest=value.log_head_digest,
                advisory_only=value.advisory_only,
                authority_granted=value.authority_granted,
            )
        except AttributeError:
            _refuse(
                "snapshot_malformed_instance", "snapshot instance is malformed"
            )
    snapshot = _wire_snapshot(
        value, ATTESTATION_LOG_SNAPSHOT_KEYS, "snapshot"
    )
    entries = _bounded_entry_sequence(
        snapshot["entries"],
        normalize=False,
        container_types=(list,),
        wire_items=True,
    )
    return AttestationLogSnapshotV1(
        schema_version=snapshot["schema_version"],
        generation=snapshot["generation"],
        previous_log_head_digest=snapshot["previous_log_head_digest"],
        entries=entries,
        log_head_digest=snapshot["log_head_digest"],
        advisory_only=snapshot["advisory_only"],
        authority_granted=snapshot["authority_granted"],
    )


def verify_attestation_log_snapshot(value: object) -> tuple[bool, Optional[str]]:
    try:
        parse_attestation_log_snapshot(value)
    except AttestationLogContractError as exc:
        return False, exc.reason
    return True, None


def verify_attestation_log_transition(
    current: object,
    proposed: object,
    *,
    expected_current_log_head_digest: str,
) -> tuple[bool, Optional[str]]:
    """Verify one strict append-only CAS transition without doing any I/O."""

    try:
        expected = _require_digest(
            expected_current_log_head_digest,
            "expected_current_log_head_digest",
        )
        current_snapshot = parse_attestation_log_snapshot(current)
    except AttestationLogContractError as exc:
        return False, exc.reason
    if expected != current_snapshot.log_head_digest:
        return False, "stale_current_log_head"

    # A stale compare-and-swap is rejected before parsing the proposed bounded
    # entry set.  The adapter still must make its external CAS atomic.
    try:
        proposed_snapshot = parse_attestation_log_snapshot(proposed)
    except AttestationLogContractError as exc:
        return False, exc.reason
    if current_snapshot.generation == MAX_GENERATION:
        return False, "generation_exhausted"
    if proposed_snapshot.generation != current_snapshot.generation + 1:
        return False, "generation_step"
    if (
        proposed_snapshot.previous_log_head_digest
        != current_snapshot.log_head_digest
    ):
        return False, "previous_log_head_binding"

    current_by_digest = {
        item.entry_digest: item.to_mapping() for item in current_snapshot.entries
    }
    proposed_by_digest = {
        item.entry_digest: item.to_mapping() for item in proposed_snapshot.entries
    }
    if not set(current_by_digest).issubset(proposed_by_digest):
        return False, "entry_deletion_or_remap"
    for digest, prior in current_by_digest.items():
        if proposed_by_digest[digest] != prior:
            return False, "entry_remap"
    if len(proposed_by_digest) <= len(current_by_digest):
        return False, "append_required"
    return True, None


def build_next_attestation_log_snapshot(
    current: object,
    *,
    expected_current_log_head_digest: str,
    appended_entries: object,
) -> AttestationLogSnapshotV1:
    """Build a strict one-generation append proposal from an expected head."""

    expected = _require_digest(
        expected_current_log_head_digest, "expected_current_log_head_digest"
    )
    current_snapshot = parse_attestation_log_snapshot(current)
    if expected != current_snapshot.log_head_digest:
        _refuse("stale_current_log_head", "expected current log head is stale")
    if current_snapshot.generation == MAX_GENERATION:
        _refuse("generation_exhausted", "attestation-log generation is exhausted")
    appended = _bounded_entry_sequence(
        appended_entries,
        normalize=True,
        container_types=(list, tuple),
    )
    if not appended:
        _refuse("append_required", "at least one new entry must be appended")
    if len(current_snapshot.entries) + len(appended) > MAX_ATTESTATION_LOG_ENTRIES:
        _refuse(
            "entries_count_exceeded",
            f"entries exceeds its {MAX_ATTESTATION_LOG_ENTRIES}-item bound",
        )
    proposed = build_attestation_log_snapshot(
        generation=current_snapshot.generation + 1,
        previous_log_head_digest=current_snapshot.log_head_digest,
        entries=(*current_snapshot.entries, *appended),
    )
    ok, reason = verify_attestation_log_transition(
        current_snapshot,
        proposed,
        expected_current_log_head_digest=expected,
    )
    if not ok:
        _refuse(reason or "transition", "proposed append is not a valid transition")
    return proposed


def _derive_committed_attestation_set_digest(
    *,
    log_head_digest: str,
    activation_scope_digest: str,
    admission_challenge_digest: str,
    entries: tuple[AttestationLogEntryV1, ...],
    empty: bool,
) -> str:
    return sha256_digest(
        {
            "domain": COMMITTED_ATTESTATION_SET_DIGEST_DOMAIN,
            "schema_version": COMMITTED_ATTESTATION_SET_SCHEMA,
            "log_head_digest": log_head_digest,
            "activation_scope_digest": activation_scope_digest,
            "admission_challenge_digest": admission_challenge_digest,
            "entries": [item.to_mapping() for item in entries],
            "entry_count": len(entries),
            "empty": empty,
            "advisory_only": True,
            "authority_granted": False,
        }
    )


@dataclass(frozen=True)
class CommittedAttestationSetV1:
    """Complete matching entries relative to one independently trusted head."""

    log_head_digest: str
    activation_scope_digest: str
    admission_challenge_digest: str
    entries: tuple[AttestationLogEntryV1, ...]
    entry_count: int
    empty: bool
    committed_attestation_set_digest: str
    advisory_only: bool = True
    authority_granted: bool = False
    schema_version: str = COMMITTED_ATTESTATION_SET_SCHEMA

    def __post_init__(self) -> None:
        _require_schema(self.schema_version, COMMITTED_ATTESTATION_SET_SCHEMA)
        log_head = _require_digest(self.log_head_digest, "log_head_digest")
        scope = _require_digest(
            self.activation_scope_digest, "activation_scope_digest"
        )
        challenge = _require_digest(
            self.admission_challenge_digest, "admission_challenge_digest"
        )
        if type(self.entries) is not tuple or any(
            type(item) is not AttestationLogEntryV1 for item in self.entries
        ):
            _refuse(
                "entries_item_type",
                "CommittedAttestationSetV1 stores exact entries in a tuple",
            )
        entries = _bounded_entry_sequence(
            self.entries,
            normalize=False,
            container_types=(tuple,),
        )
        if any(
            item.activation_scope_digest != scope
            or item.admission_challenge_digest != challenge
            for item in entries
        ):
            _refuse(
                "committed_set_binding",
                "every committed entry must match the requested scope/challenge",
            )
        if type(self.entry_count) is not int or self.entry_count != len(entries):
            _refuse("entry_count", "entry_count must exactly match entries")
        if type(self.empty) is not bool or self.empty is not (not entries):
            _refuse("empty", "empty must explicitly describe the matching set")
        _require_no_authority_flags(
            self.advisory_only,
            self.authority_granted,
            label="committed_set",
        )
        _require_digest(
            self.committed_attestation_set_digest,
            "committed_attestation_set_digest",
        )
        expected = _derive_committed_attestation_set_digest(
            log_head_digest=log_head,
            activation_scope_digest=scope,
            admission_challenge_digest=challenge,
            entries=entries,
            empty=self.empty,
        )
        if self.committed_attestation_set_digest != expected:
            _refuse(
                "committed_attestation_set_digest_mismatch",
                "committed set digest does not match content",
            )

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "log_head_digest": self.log_head_digest,
            "activation_scope_digest": self.activation_scope_digest,
            "admission_challenge_digest": self.admission_challenge_digest,
            "entries": [item.to_mapping() for item in self.entries],
            "entry_count": self.entry_count,
            "empty": self.empty,
            "committed_attestation_set_digest": (
                self.committed_attestation_set_digest
            ),
            "advisory_only": self.advisory_only,
            "authority_granted": self.authority_granted,
        }


def parse_committed_attestation_set(value: object) -> CommittedAttestationSetV1:
    """Parse a persisted committed set into a private verified copy.

    Structural parsing alone does not prove completeness.  Call
    :func:`verify_committed_attestation_set` with the full source snapshot and
    independently expected bindings before relying on a replayed mapping.
    """

    if type(value) is CommittedAttestationSetV1:
        try:
            entries = _bounded_entry_sequence(
                value.entries,
                normalize=False,
                container_types=(tuple,),
            )
            return CommittedAttestationSetV1(
                schema_version=value.schema_version,
                log_head_digest=value.log_head_digest,
                activation_scope_digest=value.activation_scope_digest,
                admission_challenge_digest=value.admission_challenge_digest,
                entries=entries,
                entry_count=value.entry_count,
                empty=value.empty,
                committed_attestation_set_digest=(
                    value.committed_attestation_set_digest
                ),
                advisory_only=value.advisory_only,
                authority_granted=value.authority_granted,
            )
        except AttributeError:
            _refuse(
                "committed_set_malformed_instance",
                "committed set instance is malformed",
            )
    snapshot = _wire_snapshot(
        value, COMMITTED_ATTESTATION_SET_KEYS, "committed_set"
    )
    entries = _bounded_entry_sequence(
        snapshot["entries"],
        normalize=False,
        container_types=(list,),
        wire_items=True,
    )
    return CommittedAttestationSetV1(
        schema_version=snapshot["schema_version"],
        log_head_digest=snapshot["log_head_digest"],
        activation_scope_digest=snapshot["activation_scope_digest"],
        admission_challenge_digest=snapshot["admission_challenge_digest"],
        entries=entries,
        entry_count=snapshot["entry_count"],
        empty=snapshot["empty"],
        committed_attestation_set_digest=snapshot[
            "committed_attestation_set_digest"
        ],
        advisory_only=snapshot["advisory_only"],
        authority_granted=snapshot["authority_granted"],
    )


def derive_committed_attestation_set(
    snapshot: object,
    expected_log_head_digest: str,
    activation_scope_digest: str,
    admission_challenge_digest: str,
) -> CommittedAttestationSetV1:
    """Return the complete canonical matching set for an expected log head.

    ``expected_log_head_digest`` must come from an independently trusted head
    source.  A mismatch refuses; this function never searches another scope,
    retries another head, or falls back to uncommitted input.  A genuinely
    empty match is represented explicitly by ``empty=True`` and has its own
    deterministic committed-set digest.
    """

    expected = _require_digest(expected_log_head_digest, "expected_log_head_digest")
    scope = _require_digest(activation_scope_digest, "activation_scope_digest")
    challenge = _require_digest(
        admission_challenge_digest, "admission_challenge_digest"
    )
    # Parsing creates a private, verified snapshot and private entry copies
    # before any selection is made.
    parsed = parse_attestation_log_snapshot(snapshot)
    if parsed.log_head_digest != expected:
        _refuse("stale_log_head", "snapshot does not match the expected log head")
    matching = tuple(
        item
        for item in parsed.entries
        if item.activation_scope_digest == scope
        and item.admission_challenge_digest == challenge
    )
    empty = not matching
    digest = _derive_committed_attestation_set_digest(
        log_head_digest=expected,
        activation_scope_digest=scope,
        admission_challenge_digest=challenge,
        entries=matching,
        empty=empty,
    )
    return CommittedAttestationSetV1(
        log_head_digest=expected,
        activation_scope_digest=scope,
        admission_challenge_digest=challenge,
        entries=matching,
        entry_count=len(matching),
        empty=empty,
        committed_attestation_set_digest=digest,
    )


def verify_committed_attestation_set(
    value: object,
    snapshot: object,
    *,
    expected_log_head_digest: str,
    activation_scope_digest: str,
    admission_challenge_digest: str,
) -> tuple[bool, Optional[str]]:
    """Rebind and rederive a persisted complete set for deterministic replay.

    The independently expected bindings prevent a self-consistent persisted
    object from choosing its own head or scope.  Requiring the full snapshot
    is essential: a head digest by itself cannot prove that an arbitrary
    persisted subset contains *all* matching entries.  This verifier parses a
    private copy, rederives the matching set from the full verified snapshot,
    and requires exact equality.  Success remains structural and grants no
    authentication, admission, routing, or runtime authority.
    """

    try:
        expected_head = _require_digest(
            expected_log_head_digest, "expected_log_head_digest"
        )
        expected_scope = _require_digest(
            activation_scope_digest, "activation_scope_digest"
        )
        expected_challenge = _require_digest(
            admission_challenge_digest, "admission_challenge_digest"
        )
        persisted = parse_committed_attestation_set(value)
    except AttestationLogContractError as exc:
        return False, exc.reason

    if persisted.log_head_digest != expected_head:
        return False, "stale_log_head"
    if persisted.activation_scope_digest != expected_scope:
        return False, "activation_scope_binding"
    if persisted.admission_challenge_digest != expected_challenge:
        return False, "admission_challenge_binding"

    try:
        rederived = derive_committed_attestation_set(
            snapshot,
            expected_head,
            expected_scope,
            expected_challenge,
        )
    except AttestationLogContractError as exc:
        return False, f"snapshot:{exc.reason}"
    if persisted.to_mapping() != rederived.to_mapping():
        return False, "committed_set_replay_mismatch"
    return True, None
