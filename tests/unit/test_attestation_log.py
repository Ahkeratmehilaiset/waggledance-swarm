"""Focused tests for the pure append-only attestation-log contract."""

from __future__ import annotations

import hashlib

import pytest

from waggledance.core.orchestration import attestation_log as L


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("ascii")).hexdigest()


def _entry(
    label: str,
    *,
    scope: str = "scope:a",
    challenge: str = "challenge:a",
    ballot: str | None = None,
    attestation: str | None = None,
    lineage: str | None = None,
) -> L.AttestationLogEntryV1:
    return L.build_attestation_log_entry(
        activation_scope_digest=_digest(scope),
        admission_challenge_digest=_digest(challenge),
        evidence_digest=_digest(f"evidence:{label}"),
        ballot_digest=_digest(ballot or f"ballot:{label}"),
        attestation_digest=_digest(attestation or f"attestation:{label}"),
        reviewer_lineage_digest=_digest(lineage or f"lineage:{label}"),
    )


def _initial(*entries: object) -> L.AttestationLogSnapshotV1:
    return L.build_attestation_log_snapshot(
        generation=0,
        previous_log_head_digest=L.INITIAL_PREVIOUS_LOG_HEAD_DIGEST,
        entries=list(entries),
    )


def test_entry_is_deterministic_exact_bound_and_authority_free() -> None:
    first = _entry("one")
    second = _entry("one")

    assert first == second
    assert first.entry_digest == second.entry_digest
    assert set(first.to_mapping()) == L.ATTESTATION_LOG_ENTRY_KEYS
    assert first.advisory_only is True
    assert first.authority_granted is False
    assert L.verify_attestation_log_entry(first.to_mapping()) == (True, None)

    smuggled = {**first.to_mapping(), "routing_authority": True}
    assert L.verify_attestation_log_entry(smuggled) == (
        False,
        "entry_keyset",
    )

    tampered = first.to_mapping()
    tampered["evidence_digest"] = _digest("replacement")
    assert L.verify_attestation_log_entry(tampered) == (
        False,
        "entry_digest_mismatch",
    )


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("advisory_only", False, "entry_advisory_only"),
        ("advisory_only", 1, "entry_advisory_only"),
        ("authority_granted", True, "entry_authority_granted"),
        ("authority_granted", 0, "entry_authority_granted"),
    ],
)
def test_entry_no_authority_flags_are_literal(
    field: str, value: object, reason: str
) -> None:
    wire = _entry("flags").to_mapping()
    wire[field] = value
    assert L.verify_attestation_log_entry(wire) == (False, reason)


def test_wire_boundaries_require_exact_json_types() -> None:
    class DictAlias(dict):
        pass

    class ListAlias(list):
        pass

    entry = _entry("wire").to_mapping()
    assert L.verify_attestation_log_entry(DictAlias(entry)) == (
        False,
        "entry_not_mapping",
    )

    snapshot = _initial(_entry("wire")).to_mapping()
    snapshot["entries"] = tuple(snapshot["entries"])
    assert L.verify_attestation_log_snapshot(snapshot) == (
        False,
        "entries_type",
    )

    snapshot = _initial(_entry("wire")).to_mapping()
    snapshot["entries"] = ListAlias(snapshot["entries"])
    assert L.verify_attestation_log_snapshot(snapshot) == (
        False,
        "entries_type",
    )

    snapshot = _initial(_entry("wire")).to_mapping()
    snapshot["entries"][0] = L.parse_attestation_log_entry(
        snapshot["entries"][0]
    )
    assert L.verify_attestation_log_snapshot(snapshot) == (
        False,
        "entry_not_mapping",
    )

    # Internal immutable types are exact too; a tuple of wire dicts cannot be
    # retained inside a dataclass after only temporary validation.
    entry_object = _entry("internal")
    head = L.derive_attestation_log_head_digest(
        generation=0,
        previous_log_head_digest=L.INITIAL_PREVIOUS_LOG_HEAD_DIGEST,
        entries=[entry_object.to_mapping()],
    )
    with pytest.raises(L.AttestationLogContractError) as internal:
        L.AttestationLogSnapshotV1(
            generation=0,
            previous_log_head_digest=L.INITIAL_PREVIOUS_LOG_HEAD_DIGEST,
            entries=(entry_object.to_mapping(),),
            log_head_digest=head,
        )
    assert internal.value.reason == "entries_item_type"

    malformed_instance = _initial(entry_object)
    object.__setattr__(malformed_instance, "entries", object())
    assert L.verify_attestation_log_snapshot(malformed_instance) == (
        False,
        "entries_type",
    )


def test_snapshot_is_deterministic_canonical_complete_and_exact_keyed() -> None:
    entries = [_entry("one"), _entry("two"), _entry("three")]
    forward = _initial(*entries)
    reverse = _initial(*reversed(entries))

    assert forward == reverse
    assert tuple(item.entry_digest for item in forward.entries) == tuple(
        sorted(item.entry_digest for item in entries)
    )
    assert set(forward.to_mapping()) == L.ATTESTATION_LOG_SNAPSHOT_KEYS
    assert forward.advisory_only is True
    assert forward.authority_granted is False
    assert L.verify_attestation_log_snapshot(forward.to_mapping()) == (True, None)

    noncanonical = forward.to_mapping()
    noncanonical["entries"].reverse()
    assert L.verify_attestation_log_snapshot(noncanonical) == (
        False,
        "entries_order",
    )

    smuggled = {**forward.to_mapping(), "authority_source": "operator"}
    assert L.verify_attestation_log_snapshot(smuggled) == (
        False,
        "snapshot_keyset",
    )


def test_snapshot_refuses_duplicate_and_ambiguous_identity_slots() -> None:
    original = _entry("original")
    with pytest.raises(L.AttestationLogContractError) as duplicate:
        _initial(original, original.to_mapping())
    assert duplicate.value.reason == "duplicate_entry_digest"

    # One scope/challenge/ballot slot cannot be remapped to a second signed
    # attestation, even if its evidence and lineage claims differ.
    remapped_ballot = _entry(
        "remapped",
        ballot="ballot:original",
        attestation="attestation:remapped",
    )
    with pytest.raises(L.AttestationLogContractError) as ambiguous_ballot:
        _initial(original, remapped_ballot)
    assert ambiguous_ballot.value.reason == "ambiguous_ballot_slot"

    # One attestation digest likewise cannot name two different ballots.
    remapped_attestation = _entry(
        "other-ballot",
        attestation="attestation:original",
    )
    with pytest.raises(L.AttestationLogContractError) as ambiguous_attestation:
        _initial(original, remapped_attestation)
    assert ambiguous_attestation.value.reason == "ambiguous_attestation_digest"

    # The same reviewer may legitimately sign distinct ballots; lineage is a
    # consistency binding and not a cardinality key.
    same_reviewer = _entry("second-ballot", lineage="lineage:original")
    assert len(_initial(original, same_reviewer).entries) == 2


def test_snapshot_bound_and_generation_predecessor_rules_fail_closed() -> None:
    entry = _entry("bounded").to_mapping()
    oversized = _initial().to_mapping()
    oversized["entries"] = [entry] * (L.MAX_ATTESTATION_LOG_ENTRIES + 1)
    assert L.verify_attestation_log_snapshot(oversized) == (
        False,
        "entries_count_exceeded",
    )

    for malformed in (True, -1, L.MAX_GENERATION + 1, 1.0, "1"):
        with pytest.raises(L.AttestationLogContractError) as exc:
            L.build_attestation_log_snapshot(
                generation=malformed,
                previous_log_head_digest=L.INITIAL_PREVIOUS_LOG_HEAD_DIGEST,
                entries=[],
            )
        assert exc.value.reason == "generation"

    with pytest.raises(L.AttestationLogContractError) as initial:
        L.build_attestation_log_snapshot(
            generation=0,
            previous_log_head_digest=_digest("not-sentinel"),
            entries=[],
        )
    assert initial.value.reason == "initial_previous_log_head"

    with pytest.raises(L.AttestationLogContractError) as noninitial:
        L.build_attestation_log_snapshot(
            generation=1,
            previous_log_head_digest=L.INITIAL_PREVIOUS_LOG_HEAD_DIGEST,
            entries=[],
        )
    assert noninitial.value.reason == "noninitial_previous_log_head"


def test_snapshot_rechecks_bound_after_owned_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    """A concurrently enlarged input cannot cross the aggregate bound."""

    first = _entry("copy-race-first").to_mapping()
    injected = _entry("copy-race-injected")
    original_tuple = tuple

    def raced_tuple(value: object) -> tuple[object, ...]:
        copied = original_tuple(value)
        if type(value) is list:
            return copied + (injected,)
        return copied

    monkeypatch.setattr(L, "MAX_ATTESTATION_LOG_ENTRIES", 1)
    monkeypatch.setattr(L, "tuple", raced_tuple, raising=False)

    with pytest.raises(L.AttestationLogContractError) as exc:
        L.derive_attestation_log_head_digest(
            generation=0,
            previous_log_head_digest=L.INITIAL_PREVIOUS_LOG_HEAD_DIGEST,
            entries=[first],
        )
    assert exc.value.reason == "entries_count_exceeded"


def test_valid_transition_is_generation_bound_strict_append_only() -> None:
    first = _initial(_entry("one"))
    second = L.build_next_attestation_log_snapshot(
        first.to_mapping(),
        expected_current_log_head_digest=first.log_head_digest,
        appended_entries=[_entry("two")],
    )

    assert second.generation == 1
    assert second.previous_log_head_digest == first.log_head_digest
    assert set(item.entry_digest for item in first.entries) < set(
        item.entry_digest for item in second.entries
    )
    assert L.verify_attestation_log_transition(
        first,
        second,
        expected_current_log_head_digest=first.log_head_digest,
    ) == (True, None)

    no_append = L.build_attestation_log_snapshot(
        generation=1,
        previous_log_head_digest=first.log_head_digest,
        entries=first.entries,
    )
    assert L.verify_attestation_log_transition(
        first,
        no_append,
        expected_current_log_head_digest=first.log_head_digest,
    ) == (False, "append_required")

    with pytest.raises(L.AttestationLogContractError) as empty_append:
        L.build_next_attestation_log_snapshot(
            first,
            expected_current_log_head_digest=first.log_head_digest,
            appended_entries=[],
        )
    assert empty_append.value.reason == "append_required"


def test_transition_refuses_stale_skip_wrong_predecessor_replay_and_aba() -> None:
    first = _initial(_entry("one"))
    second = L.build_next_attestation_log_snapshot(
        first,
        expected_current_log_head_digest=first.log_head_digest,
        appended_entries=[_entry("two")],
    )

    # Stale-head refusal precedes parsing the proposed bounded aggregate.
    assert L.verify_attestation_log_transition(
        second,
        object(),
        expected_current_log_head_digest=first.log_head_digest,
    ) == (False, "stale_current_log_head")

    skipped = L.build_attestation_log_snapshot(
        generation=3,
        previous_log_head_digest=second.log_head_digest,
        entries=[*second.entries, _entry("three")],
    )
    assert L.verify_attestation_log_transition(
        second,
        skipped,
        expected_current_log_head_digest=second.log_head_digest,
    ) == (False, "generation_step")

    wrong_previous = L.build_attestation_log_snapshot(
        generation=2,
        previous_log_head_digest=_digest("wrong-previous"),
        entries=[*second.entries, _entry("three")],
    )
    assert L.verify_attestation_log_transition(
        second,
        wrong_previous,
        expected_current_log_head_digest=second.log_head_digest,
    ) == (False, "previous_log_head_binding")

    # A previously valid snapshot cannot be replayed as a new transition, and
    # returning to the same entry content still requires a fresh generation,
    # predecessor, and a strict append.
    assert L.verify_attestation_log_transition(
        second,
        first,
        expected_current_log_head_digest=second.log_head_digest,
    ) == (False, "generation_step")


def test_transition_refuses_deletion_and_logical_remap() -> None:
    one = _entry("one")
    two = _entry("two")
    current = _initial(one, two)

    deleted = L.build_attestation_log_snapshot(
        generation=1,
        previous_log_head_digest=current.log_head_digest,
        entries=[one, _entry("three")],
    )
    assert L.verify_attestation_log_transition(
        current,
        deleted,
        expected_current_log_head_digest=current.log_head_digest,
    ) == (False, "entry_deletion_or_remap")

    remapped = L.build_attestation_log_snapshot(
        generation=1,
        previous_log_head_digest=current.log_head_digest,
        entries=[
            two,
            _entry(
                "replacement-one",
                ballot="ballot:one",
                attestation="attestation:replacement-one",
            ),
        ],
    )
    assert L.verify_attestation_log_transition(
        current,
        remapped,
        expected_current_log_head_digest=current.log_head_digest,
    ) == (False, "entry_deletion_or_remap")


def test_committed_set_is_complete_canonical_head_bound_and_cross_scope_safe() -> None:
    scope_a_challenge_a = [_entry("one"), _entry("two")]
    other_scope = _entry("scope-b", scope="scope:b")
    other_challenge = _entry("challenge-b", challenge="challenge:b")
    snapshot = _initial(
        other_scope,
        scope_a_challenge_a[1],
        other_challenge,
        scope_a_challenge_a[0],
    )

    committed = L.derive_committed_attestation_set(
        snapshot.to_mapping(),
        snapshot.log_head_digest,
        _digest("scope:a"),
        _digest("challenge:a"),
    )
    assert committed.log_head_digest == snapshot.log_head_digest
    assert committed.entry_count == 2
    assert committed.empty is False
    assert committed.advisory_only is True
    assert committed.authority_granted is False
    assert tuple(item.entry_digest for item in committed.entries) == tuple(
        sorted(item.entry_digest for item in scope_a_challenge_a)
    )
    assert all(
        item.activation_scope_digest == _digest("scope:a")
        and item.admission_challenge_digest == _digest("challenge:a")
        for item in committed.entries
    )
    assert set(committed.to_mapping()) == L.COMMITTED_ATTESTATION_SET_KEYS

    repeated = L.derive_committed_attestation_set(
        snapshot,
        snapshot.log_head_digest,
        _digest("scope:a"),
        _digest("challenge:a"),
    )
    assert repeated == committed
    assert L.parse_committed_attestation_set(committed.to_mapping()) == committed
    assert L.verify_committed_attestation_set(
        committed.to_mapping(),
        snapshot.to_mapping(),
        expected_log_head_digest=snapshot.log_head_digest,
        activation_scope_digest=_digest("scope:a"),
        admission_challenge_digest=_digest("challenge:a"),
    ) == (True, None)


def test_persisted_committed_set_wire_is_exact_and_rebound_to_expected_context() -> None:
    snapshot = _initial(_entry("one"), _entry("two"))
    committed = L.derive_committed_attestation_set(
        snapshot,
        snapshot.log_head_digest,
        _digest("scope:a"),
        _digest("challenge:a"),
    )
    wire = committed.to_mapping()

    smuggled = {**wire, "authenticated": True}
    with pytest.raises(L.AttestationLogContractError) as keyset:
        L.parse_committed_attestation_set(smuggled)
    assert keyset.value.reason == "committed_set_keyset"

    noncanonical = committed.to_mapping()
    noncanonical["entries"].reverse()
    with pytest.raises(L.AttestationLogContractError) as order:
        L.parse_committed_attestation_set(noncanonical)
    assert order.value.reason == "entries_order"

    tuple_wire = committed.to_mapping()
    tuple_wire["entries"] = tuple(tuple_wire["entries"])
    with pytest.raises(L.AttestationLogContractError) as wire_type:
        L.parse_committed_attestation_set(tuple_wire)
    assert wire_type.value.reason == "entries_type"

    authority = committed.to_mapping()
    authority["authority_granted"] = True
    with pytest.raises(L.AttestationLogContractError) as authority_error:
        L.parse_committed_attestation_set(authority)
    assert authority_error.value.reason == "committed_set_authority_granted"

    assert L.verify_committed_attestation_set(
        wire,
        snapshot,
        expected_log_head_digest=_digest("wrong-head"),
        activation_scope_digest=_digest("scope:a"),
        admission_challenge_digest=_digest("challenge:a"),
    ) == (False, "stale_log_head")
    assert L.verify_committed_attestation_set(
        wire,
        snapshot,
        expected_log_head_digest=snapshot.log_head_digest,
        activation_scope_digest=_digest("scope:other"),
        admission_challenge_digest=_digest("challenge:a"),
    ) == (False, "activation_scope_binding")
    assert L.verify_committed_attestation_set(
        wire,
        snapshot,
        expected_log_head_digest=snapshot.log_head_digest,
        activation_scope_digest=_digest("scope:a"),
        admission_challenge_digest=_digest("challenge:other"),
    ) == (False, "admission_challenge_binding")


def test_replay_verifier_rederives_completeness_not_only_self_consistency() -> None:
    snapshot = _initial(_entry("one"), _entry("two"))
    complete = L.derive_committed_attestation_set(
        snapshot,
        snapshot.log_head_digest,
        _digest("scope:a"),
        _digest("challenge:a"),
    )
    subset = complete.entries[:1]

    # This subset is structurally self-consistent and binds the trusted head,
    # but it is not complete.  Structural parsing accepts it; replay against
    # the full head-bound snapshot must reject it.
    persisted_subset = L.CommittedAttestationSetV1(
        log_head_digest=snapshot.log_head_digest,
        activation_scope_digest=_digest("scope:a"),
        admission_challenge_digest=_digest("challenge:a"),
        entries=subset,
        entry_count=1,
        empty=False,
        committed_attestation_set_digest=(
            L._derive_committed_attestation_set_digest(
                log_head_digest=snapshot.log_head_digest,
                activation_scope_digest=_digest("scope:a"),
                admission_challenge_digest=_digest("challenge:a"),
                entries=subset,
                empty=False,
            )
        ),
    )
    assert L.parse_committed_attestation_set(
        persisted_subset.to_mapping()
    ) == persisted_subset
    assert L.verify_committed_attestation_set(
        persisted_subset.to_mapping(),
        snapshot.to_mapping(),
        expected_log_head_digest=snapshot.log_head_digest,
        activation_scope_digest=_digest("scope:a"),
        admission_challenge_digest=_digest("challenge:a"),
    ) == (False, "committed_set_replay_mismatch")


def test_empty_committed_set_is_explicit_and_never_falls_back() -> None:
    snapshot = _initial(_entry("only", scope="scope:present"))
    missing = L.derive_committed_attestation_set(
        snapshot,
        snapshot.log_head_digest,
        _digest("scope:missing"),
        _digest("challenge:a"),
    )

    assert missing.entries == ()
    assert missing.entry_count == 0
    assert missing.empty is True
    assert missing.committed_attestation_set_digest == (
        L.derive_committed_attestation_set(
            snapshot.to_mapping(),
            snapshot.log_head_digest,
            _digest("scope:missing"),
            _digest("challenge:a"),
        ).committed_attestation_set_digest
    )
    assert missing.committed_attestation_set_digest != (
        L.derive_committed_attestation_set(
            snapshot,
            snapshot.log_head_digest,
            _digest("scope:present"),
            _digest("challenge:a"),
        ).committed_attestation_set_digest
    )


def test_committed_set_refuses_untrusted_head_and_uses_private_verified_copy() -> None:
    snapshot = _initial(_entry("private"))
    wire = snapshot.to_mapping()
    committed = L.derive_committed_attestation_set(
        wire,
        snapshot.log_head_digest,
        _digest("scope:a"),
        _digest("challenge:a"),
    )
    original_digest = committed.entries[0].entry_digest

    # Caller mutation after the boundary cannot mutate the returned result.
    wire["entries"][0]["entry_digest"] = _digest("post-call-mutation")
    wire["entries"].clear()
    assert committed.entries[0].entry_digest == original_digest
    assert committed.entry_count == 1

    with pytest.raises(L.AttestationLogContractError) as stale:
        L.derive_committed_attestation_set(
            snapshot,
            _digest("untrusted-or-stale-head"),
            _digest("scope:a"),
            _digest("challenge:a"),
        )
    assert stale.value.reason == "stale_log_head"


def test_head_and_committed_digest_bind_every_relevant_dimension() -> None:
    baseline = _initial(_entry("baseline"))
    changed_scope = _initial(_entry("baseline", scope="scope:changed"))
    changed_challenge = _initial(
        _entry("baseline", challenge="challenge:changed")
    )
    changed_lineage = _initial(_entry("baseline", lineage="lineage:changed"))

    assert len(
        {
            baseline.log_head_digest,
            changed_scope.log_head_digest,
            changed_challenge.log_head_digest,
            changed_lineage.log_head_digest,
        }
    ) == 4

    empty_a = L.derive_committed_attestation_set(
        baseline,
        baseline.log_head_digest,
        _digest("scope:empty-a"),
        _digest("challenge:a"),
    )
    empty_b = L.derive_committed_attestation_set(
        baseline,
        baseline.log_head_digest,
        _digest("scope:empty-b"),
        _digest("challenge:a"),
    )
    assert (
        empty_a.committed_attestation_set_digest
        != empty_b.committed_attestation_set_digest
    )
