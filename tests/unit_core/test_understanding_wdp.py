from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from waggledance.core.hex_topology.understanding_wdp import (
    AuthenticatedDanceSignalEnvelopeV1,
    AuthenticatedProposalEnvelopeV1,
    HexRingSnapshotV1,
    TrustedCellRecordV1,
    TrustedWDPRegistryV1,
    WDPContractError,
    WDPDecision,
    WDPPolicyV1,
    WDPShadowReceiptV1,
    evaluate_waggle_decision,
)
from waggledance.core.learning.understanding_contracts import (
    DanceSignalKind,
    DanceSignalV1,
    HexCellAddressV1,
    IndependenceProfileV1,
    KnowledgeClaimKind,
    KnowledgeDeltaV1,
    PrivacyClass,
)


NOW = "2026-08-02T12:00:00Z"
CREATED = "2026-08-02T11:55:00Z"
EXPIRES = "2026-08-02T12:30:00Z"
KEY_EPOCH = "2026-08-02"


def _digest(number: int) -> str:
    return f"sha256:{number:064x}"


def _key(identity: str) -> bytes:
    return hashlib.sha256(f"test-auth-key:{identity}".encode()).digest()


def _address(
    cell_id: str,
    q: int,
    r: int,
    *,
    incarnation: str | None = None,
    generation: int = 1,
    fence: int = 1,
) -> HexCellAddressV1:
    return HexCellAddressV1(
        cell_id=cell_id,
        q=q,
        r=r,
        incarnation_id=incarnation or f"inc-{cell_id}",
        generation=generation,
        fence=fence,
    )


def _ring() -> HexRingSnapshotV1:
    return HexRingSnapshotV1(
        center=_address("center", 0, 0),
        neighbors=(
            _address("east", 1, 0),
            _address("north-east", 1, -1),
            _address("north-west", 0, -1),
            _address("west", -1, 0),
            _address("south-west", -1, 1),
            _address("south-east", 0, 1),
        ),
    )


def _profile(
    cell: HexCellAddressV1,
    *,
    method: str,
    evidence: str,
    failure_domain: str,
    genesis: str,
) -> IndependenceProfileV1:
    return IndependenceProfileV1(
        reviewer_cell_id=cell.cell_id,
        identity_incarnation=cell.incarnation_id,
        genesis_root_digest=_digest(
            sum(ord(character) for character in genesis) + 10_000
        ),
        verifier_code_family=f"code-{method}",
        model_provider_lineage=f"model-{method}",
        prompt_policy_lineage=f"prompt-{method}",
        evidence_root_lineage=f"root-{evidence}",
        toolchain_image_lineage=f"image-{method}",
        physical_failure_domain=failure_domain,
    )


def _trust(
    ring: HexRingSnapshotV1,
    *,
    overrides: dict[str, tuple[str, str, str, str]] | None = None,
) -> tuple[
    TrustedWDPRegistryV1,
    dict[tuple[str, str], bytes],
    dict[str, tuple[str, str, str, str]],
]:
    defaults = {
        "center": ("center", "center", "host-center", "genesis-center"),
        "east": ("a", "a", "host-a", "genesis-a"),
        "north-east": ("b", "b", "host-b", "genesis-b"),
        "north-west": ("c", "c", "host-c", "genesis-c"),
        "west": ("d", "d", "host-d", "genesis-d"),
        "south-west": ("e", "e", "host-e", "genesis-e"),
        "south-east": ("f", "f", "host-f", "genesis-f"),
    }
    defaults.update(overrides or {})
    records: list[TrustedCellRecordV1] = []
    keys: dict[tuple[str, str], bytes] = {}
    for cell in (ring.center, *ring.neighbors):
        method, evidence, domain, genesis = defaults[cell.cell_id]
        profile = _profile(
            cell,
            method=method,
            evidence=evidence,
            failure_domain=domain,
            genesis=genesis,
        )
        key_id = f"key-{cell.cell_id}"
        records.append(
            TrustedCellRecordV1(
                cell=cell,
                auth_key_id=key_id,
                key_epoch=KEY_EPOCH,
                genesis_root_digest=profile.genesis_root_digest,
                method_group_digest=profile.method_group_digest,
                evidence_group_digest=profile.evidence_group_digest,
                physical_failure_domain=profile.physical_failure_domain,
            )
        )
        keys[(key_id, KEY_EPOCH)] = _key(cell.cell_id)
    return TrustedWDPRegistryV1.create(tuple(records)), keys, defaults


def _resolver(keys: dict[tuple[str, str], bytes]):
    def resolve(key_id: str, key_epoch: str) -> bytes:
        return keys[(key_id, key_epoch)]

    return resolve


def _proposal(
    *,
    proposer: str = "center",
    created: str = CREATED,
    expires: str = EXPIRES,
) -> KnowledgeDeltaV1:
    return KnowledgeDeltaV1(
        proposal_id="proposal-1",
        proposer_cell_id=proposer,
        claim_kind=KnowledgeClaimKind.HYPOTHESIS,
        aggregate_digest=_digest(100),
        evidence_refs=tuple(_digest(index) for index in range(1, 6)),
        confidence=0.8,
        privacy_class=PrivacyClass.PUBLIC,
        created_at_utc=created,
        expires_at_utc=expires,
    )


def _proposal_envelope(
    proposal: KnowledgeDeltaV1,
    proposer: HexCellAddressV1,
    registry: TrustedWDPRegistryV1,
    keys: dict[tuple[str, str], bytes],
    *,
    hmac_key: bytes | None = None,
) -> AuthenticatedProposalEnvelopeV1:
    record = registry.record_for("center")
    assert record is not None
    return AuthenticatedProposalEnvelopeV1.create(
        proposal=proposal,
        proposer=proposer,
        registry_digest=registry.registry_digest,
        key_id=record.auth_key_id,
        key_epoch=record.key_epoch,
        hmac_key=hmac_key or keys[(record.auth_key_id, record.key_epoch)],
    )


def _signal(
    proposal: KnowledgeDeltaV1,
    cell: HexCellAddressV1,
    registry: TrustedWDPRegistryV1,
    keys: dict[tuple[str, str], bytes],
    facts: dict[str, tuple[str, str, str, str]],
    *,
    kind: DanceSignalKind = DanceSignalKind.SUPPORT,
    method: str | None = None,
    evidence: str | None = None,
    failure_domain: str | None = None,
    genesis: str | None = None,
    evidence_digest: int = 200,
    created: str = "2026-08-02T11:58:00Z",
    expires: str = "2026-08-02T12:20:00Z",
    supersedes: str | None = None,
    proposal_digest: str | None = None,
    emitter: HexCellAddressV1 | None = None,
    hmac_key: bytes | None = None,
) -> AuthenticatedDanceSignalEnvelopeV1:
    expected = facts[cell.cell_id]
    profile = _profile(
        cell,
        method=method or expected[0],
        evidence=evidence or expected[1],
        failure_domain=failure_domain or expected[2],
        genesis=genesis or expected[3],
    )
    signal = DanceSignalV1(
        proposal_digest=proposal_digest or proposal.proposal_digest,
        signal_kind=kind,
        reviewer=profile,
        evidence_digest=_digest(evidence_digest),
        created_at_utc=created,
        expires_at_utc=expires,
        supersedes_signal_digest=supersedes,
    )
    record = registry.record_for(cell.cell_id)
    assert record is not None
    return AuthenticatedDanceSignalEnvelopeV1.create(
        signal=signal,
        emitter=emitter or cell,
        registry_digest=registry.registry_digest,
        key_id=record.auth_key_id,
        key_epoch=record.key_epoch,
        hmac_key=hmac_key or keys[(record.auth_key_id, record.key_epoch)],
    )


def _evaluate(
    proposal_envelope: AuthenticatedProposalEnvelopeV1,
    ring: HexRingSnapshotV1,
    signals: tuple[AuthenticatedDanceSignalEnvelopeV1, ...],
    registry: TrustedWDPRegistryV1,
    keys: dict[tuple[str, str], bytes],
    *,
    now: str = NOW,
    policy: WDPPolicyV1 = WDPPolicyV1(),
) -> WDPShadowReceiptV1:
    return evaluate_waggle_decision(
        proposal_envelope,
        ring,
        signals,
        trust_registry=registry,
        key_resolver=_resolver(keys),
        now_utc=now,
        policy=policy,
    )


def _context(
    *,
    overrides: dict[str, tuple[str, str, str, str]] | None = None,
    proposal: KnowledgeDeltaV1 | None = None,
) -> tuple[
    HexRingSnapshotV1,
    KnowledgeDeltaV1,
    AuthenticatedProposalEnvelopeV1,
    TrustedWDPRegistryV1,
    dict[tuple[str, str], bytes],
    dict[str, tuple[str, str, str, str]],
]:
    ring = _ring()
    registry, keys, facts = _trust(ring, overrides=overrides)
    actual_proposal = proposal or _proposal()
    envelope = _proposal_envelope(actual_proposal, ring.center, registry, keys)
    return ring, actual_proposal, envelope, registry, keys, facts


def _independent_supports(
    proposal: KnowledgeDeltaV1,
    ring: HexRingSnapshotV1,
    registry: TrustedWDPRegistryV1,
    keys: dict[tuple[str, str], bytes],
    facts: dict[str, tuple[str, str, str, str]],
) -> tuple[
    AuthenticatedDanceSignalEnvelopeV1,
    AuthenticatedDanceSignalEnvelopeV1,
]:
    return (
        _signal(
            proposal,
            ring.neighbors[0],
            registry,
            keys,
            facts,
            evidence_digest=201,
        ),
        _signal(
            proposal,
            ring.neighbors[1],
            registry,
            keys,
            facts,
            evidence_digest=202,
        ),
    )


def test_complete_ring1_requires_exactly_six_axial_neighbors() -> None:
    ring = _ring()
    with pytest.raises(WDPContractError, match="exactly six"):
        HexRingSnapshotV1(center=ring.center, neighbors=ring.neighbors[:-1])
    displaced = _address("south-east", 20, 20)
    with pytest.raises(WDPContractError, match="all six axial"):
        HexRingSnapshotV1(
            center=ring.center,
            neighbors=ring.neighbors[:-1] + (displaced,),
        )


def test_two_authenticated_independent_neighbors_approve_deterministically() -> None:
    ring, proposal, proposal_auth, registry, keys, facts = _context()
    left, right = _independent_supports(
        proposal, ring, registry, keys, facts
    )

    receipt = _evaluate(proposal_auth, ring, (left, right), registry, keys)
    reversed_receipt = _evaluate(
        proposal_auth, ring, (right, left), registry, keys
    )

    assert receipt.decision is WDPDecision.APPROVED_SHADOW
    assert receipt.to_mapping() == reversed_receipt.to_mapping()
    assert receipt.receipt_digest == reversed_receipt.receipt_digest
    assert len(receipt.distinct_lineage_group_digests) == 2
    assert receipt.distinct_physical_failure_domains == ("host-a", "host-b")
    assert receipt.hive_commit_applied is False
    assert receipt.runtime_authority_applied is False
    assert receipt.routing_influence_applied is False


def test_one_key_cannot_forge_a_second_independent_witness() -> None:
    ring, proposal, proposal_auth, registry, keys, facts = _context()
    legitimate = _signal(
        proposal, ring.neighbors[0], registry, keys, facts, evidence_digest=301
    )
    forged = _signal(
        proposal,
        ring.neighbors[1],
        registry,
        keys,
        facts,
        evidence_digest=302,
        hmac_key=keys[("key-east", KEY_EPOCH)],
    )

    receipt = _evaluate(
        proposal_auth, ring, (legitimate, forged), registry, keys
    )

    assert receipt.approved is False
    assert receipt.decision is WDPDecision.INSUFFICIENT_INDEPENDENCE
    assert any(
        item.reason_code == "signal_authentication_failed"
        for item in receipt.rejected_signals
    )


def test_stale_proposer_incarnation_cannot_replay_delta_after_rebuild() -> None:
    ring, proposal, _, registry, keys, _ = _context()
    old_center = _address(
        "center", 0, 0, incarnation="inc-old", generation=0, fence=0
    )
    stale = _proposal_envelope(proposal, old_center, registry, keys)

    receipt = _evaluate(stale, ring, (), registry, keys)

    assert receipt.decision is WDPDecision.PROPOSAL_INVALID
    assert "stale_or_foreign_proposer_fence" in receipt.reason_codes


def test_invalid_stale_variant_cannot_poison_current_authenticated_signal() -> None:
    ring, proposal, proposal_auth, registry, keys, facts = _context()
    current = _signal(
        proposal, ring.neighbors[0], registry, keys, facts, evidence_digest=401
    )
    cell = ring.neighbors[0]
    stale_address = _address(
        cell.cell_id,
        cell.q,
        cell.r,
        incarnation=cell.incarnation_id,
        generation=0,
        fence=0,
    )
    record = registry.record_for(cell.cell_id)
    assert record is not None
    stale = AuthenticatedDanceSignalEnvelopeV1.create(
        signal=current.signal,
        emitter=stale_address,
        registry_digest=registry.registry_digest,
        key_id=record.auth_key_id,
        key_epoch=record.key_epoch,
        hmac_key=keys[(record.auth_key_id, record.key_epoch)],
    )

    first = _evaluate(
        proposal_auth, ring, (current, stale), registry, keys
    )
    second = _evaluate(
        proposal_auth, ring, (stale, current), registry, keys
    )

    assert first.to_mapping() == second.to_mapping()
    assert first.receipt_digest == second.receipt_digest
    assert first.decision is WDPDecision.INSUFFICIENT_INDEPENDENCE
    assert first.active_support_signal_digests == (current.signal.signal_digest,)
    assert first.rejected_signals[0].reason_code == (
        "stale_incarnation_generation_or_fence"
    )


@pytest.mark.parametrize(
    ("second_method", "second_evidence", "second_domain", "second_genesis"),
    (
        ("a", "b", "host-b", "genesis-b"),
        ("b", "a", "host-b", "genesis-b"),
        ("b", "b", "host-a", "genesis-b"),
        ("b", "b", "host-b", "genesis-a"),
    ),
)
def test_support_must_differ_on_all_four_registry_derived_axes(
    second_method: str,
    second_evidence: str,
    second_domain: str,
    second_genesis: str,
) -> None:
    overrides = {
        "north-east": (
            second_method,
            second_evidence,
            second_domain,
            second_genesis,
        )
    }
    ring, proposal, proposal_auth, registry, keys, facts = _context(
        overrides=overrides
    )
    supports = _independent_supports(proposal, ring, registry, keys, facts)

    receipt = _evaluate(proposal_auth, ring, supports, registry, keys)

    assert receipt.decision is WDPDecision.INSUFFICIENT_INDEPENDENCE
    assert receipt.independent_support_pair == ()


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    (
        ("method", "forged", "untrusted_method_claim"),
        ("evidence", "forged", "untrusted_evidence_claim"),
        ("failure_domain", "forged-host", "untrusted_failure_domain_claim"),
        ("genesis", "forged-genesis", "untrusted_genesis_claim"),
    ),
)
def test_authenticated_cell_cannot_self_assert_registry_independence(
    field: str, value: str, reason: str
) -> None:
    ring, proposal, proposal_auth, registry, keys, facts = _context()
    kwargs = {field: value}
    forged = _signal(
        proposal,
        ring.neighbors[0],
        registry,
        keys,
        facts,
        **kwargs,
    )

    receipt = _evaluate(proposal_auth, ring, (forged,), registry, keys)

    assert receipt.decision is WDPDecision.SILENCE
    assert receipt.rejected_signals[0].reason_code == reason


def test_valid_stop_and_challenge_each_veto_independent_support() -> None:
    ring, proposal, proposal_auth, registry, keys, facts = _context()
    supports = _independent_supports(proposal, ring, registry, keys, facts)
    stop = _signal(
        proposal,
        ring.neighbors[2],
        registry,
        keys,
        facts,
        kind=DanceSignalKind.STOP,
        evidence_digest=501,
    )
    stopped = _evaluate(
        proposal_auth, ring, supports + (stop,), registry, keys
    )
    assert stopped.decision is WDPDecision.STOPPED

    challenge = _signal(
        proposal,
        ring.neighbors[2],
        registry,
        keys,
        facts,
        kind=DanceSignalKind.CHALLENGE,
        evidence_digest=502,
    )
    challenged = _evaluate(
        proposal_auth, ring, supports + (challenge,), registry, keys
    )
    assert challenged.decision is WDPDecision.CHALLENGED


def test_unauthenticated_conflict_cannot_strip_an_authenticated_stop() -> None:
    ring, proposal, proposal_auth, registry, keys, facts = _context()
    supports = _independent_supports(proposal, ring, registry, keys, facts)
    stop = _signal(
        proposal,
        ring.neighbors[2],
        registry,
        keys,
        facts,
        kind=DanceSignalKind.STOP,
        evidence_digest=503,
    )
    forged_variant = AuthenticatedDanceSignalEnvelopeV1(
        signal=stop.signal,
        emitter=stop.emitter,
        registry_digest=stop.registry_digest,
        key_id=stop.key_id,
        key_epoch=stop.key_epoch,
        auth_tag="hmac-sha256:" + "0" * 64,
    )

    receipt = _evaluate(
        proposal_auth,
        ring,
        supports + (stop, forged_variant),
        registry,
        keys,
    )

    assert receipt.decision is WDPDecision.STOPPED
    assert stop.signal.signal_digest in receipt.stop_signal_digests
    assert any(
        item.reason_code == "signal_authentication_failed"
        for item in receipt.rejected_signals
    )


def test_revision_replaces_prior_support_without_double_counting() -> None:
    ring, proposal, proposal_auth, registry, keys, facts = _context()
    first = _signal(
        proposal,
        ring.neighbors[0],
        registry,
        keys,
        facts,
        evidence_digest=601,
        created="2026-08-02T11:57:00Z",
    )
    revised = _signal(
        proposal,
        ring.neighbors[0],
        registry,
        keys,
        facts,
        evidence_digest=602,
        created="2026-08-02T11:58:00Z",
        supersedes=first.signal.signal_digest,
    )
    independent = _signal(
        proposal,
        ring.neighbors[1],
        registry,
        keys,
        facts,
        evidence_digest=603,
    )

    receipt = _evaluate(
        proposal_auth,
        ring,
        (first, revised, independent),
        registry,
        keys,
    )

    assert receipt.decision is WDPDecision.APPROVED_SHADOW
    assert first.signal.signal_digest not in receipt.active_support_signal_digests
    assert revised.signal.signal_digest in receipt.active_support_signal_digests


def test_revision_depth_removes_every_over_depth_descendant_together() -> None:
    ring, proposal, proposal_auth, registry, keys, facts = _context()
    chain: list[AuthenticatedDanceSignalEnvelopeV1] = []
    supersedes: str | None = None
    for index in range(8):
        item = _signal(
            proposal,
            ring.neighbors[0],
            registry,
            keys,
            facts,
            evidence_digest=620 + index,
            created=f"2026-08-02T11:56:0{index}Z",
            supersedes=supersedes,
        )
        chain.append(item)
        supersedes = item.signal.signal_digest

    receipt = _evaluate(
        proposal_auth,
        ring,
        tuple(chain),
        registry,
        keys,
        policy=WDPPolicyV1(max_revision_depth=2),
    )

    assert receipt.decision is WDPDecision.INSUFFICIENT_INDEPENDENCE
    assert receipt.active_support_signal_digests == (
        chain[2].signal.signal_digest,
    )
    assert set(receipt.accepted_signal_digests) == {
        item.signal.signal_digest for item in chain[:3]
    }
    assert {
        item.signal_digest
        for item in receipt.rejected_signals
        if item.reason_code == "revision_depth_exceeded"
    } == {item.signal.signal_digest for item in chain[3:]}


def test_challenge_revision_is_a_fail_closed_veto() -> None:
    ring, proposal, proposal_auth, registry, keys, facts = _context()
    first = _signal(
        proposal,
        ring.neighbors[0],
        registry,
        keys,
        facts,
        evidence_digest=701,
        created="2026-08-02T11:57:00Z",
    )
    challenge = _signal(
        proposal,
        ring.neighbors[0],
        registry,
        keys,
        facts,
        kind=DanceSignalKind.CHALLENGE,
        evidence_digest=702,
        created="2026-08-02T11:58:00Z",
        supersedes=first.signal.signal_digest,
    )

    receipt = _evaluate(
        proposal_auth, ring, (first, challenge), registry, keys
    )

    assert receipt.decision is WDPDecision.CHALLENGED
    assert first.signal.signal_digest not in receipt.active_support_signal_digests


def test_self_vote_wrong_proposal_and_stale_neighbor_fail_closed() -> None:
    ring, proposal, proposal_auth, registry, keys, facts = _context()
    self_vote = _signal(
        proposal, ring.center, registry, keys, facts, evidence_digest=801
    )
    wrong = _signal(
        proposal,
        ring.neighbors[0],
        registry,
        keys,
        facts,
        proposal_digest=_digest(999),
        evidence_digest=802,
    )
    current = ring.neighbors[1]
    stale_cell = _address(
        current.cell_id,
        current.q,
        current.r,
        incarnation=current.incarnation_id,
        generation=0,
        fence=0,
    )
    stale = _signal(
        proposal,
        stale_cell,
        registry,
        keys,
        facts,
        evidence_digest=803,
    )

    receipt = _evaluate(
        proposal_auth, ring, (self_vote, wrong, stale), registry, keys
    )

    reasons = {item.reason_code for item in receipt.rejected_signals}
    assert reasons == {
        "self_vote",
        "proposal_digest_mismatch",
        "stale_incarnation_generation_or_fence",
    }
    assert receipt.decision is WDPDecision.SILENCE


def test_silence_expiry_and_signal_time_bounds_fail_closed() -> None:
    ring, proposal, proposal_auth, registry, keys, facts = _context()
    silence = _evaluate(proposal_auth, ring, (), registry, keys)
    assert silence.decision is WDPDecision.SILENCE

    expired_proposal = _proposal(expires="2026-08-02T11:59:59Z")
    _, _, expired_auth, _, _, _ = _context(proposal=expired_proposal)
    expired = _evaluate(expired_auth, ring, (), registry, keys)
    assert expired.decision is WDPDecision.PROPOSAL_EXPIRED

    future = _signal(
        proposal,
        ring.neighbors[0],
        registry,
        keys,
        facts,
        created="2026-08-02T12:01:00Z",
        expires="2026-08-02T12:20:00Z",
    )
    bounded = _evaluate(
        proposal_auth,
        ring,
        (future,),
        registry,
        keys,
        policy=WDPPolicyV1(max_signal_ttl_seconds=1_500),
    )
    assert bounded.rejected_signals[0].reason_code == "signal_from_future"


def test_registry_snapshot_tampering_and_short_hmac_keys_fail_closed() -> None:
    ring, proposal, _, registry, keys, _ = _context()
    with pytest.raises(WDPContractError, match="digest mismatch"):
        TrustedWDPRegistryV1(
            records=registry.records,
            registry_digest=_digest(999),
        )
    with pytest.raises(WDPContractError, match="at least 32"):
        AuthenticatedProposalEnvelopeV1.create(
            proposal=proposal,
            proposer=ring.center,
            registry_digest=registry.registry_digest,
            key_id="key-center",
            key_epoch=KEY_EPOCH,
            hmac_key=b"short",
        )


def test_trust_registry_rejects_duplicate_key_metadata() -> None:
    _, _, _, registry, _, _ = _context()
    first, second, *rest = registry.records
    duplicate = replace(
        second,
        auth_key_id=first.auth_key_id,
        key_epoch=first.key_epoch,
    )

    with pytest.raises(WDPContractError, match="key metadata"):
        TrustedWDPRegistryV1.create((first, duplicate, *rest))


def test_distinct_key_ids_with_shared_material_cannot_form_quorum() -> None:
    ring, proposal, proposal_auth, registry, keys, facts = _context()
    keys[("key-north-east", KEY_EPOCH)] = keys[("key-east", KEY_EPOCH)]
    supports = _independent_supports(proposal, ring, registry, keys, facts)

    receipt = _evaluate(proposal_auth, ring, supports, registry, keys)

    assert receipt.decision is WDPDecision.PROPOSAL_INVALID
    assert "trusted_key_material_not_independent" in receipt.reason_codes


def test_resolver_callback_cannot_mutate_digest_bound_registry_snapshot() -> None:
    ring, proposal, proposal_auth, registry, keys, facts = _context()
    supports = _independent_supports(proposal, ring, registry, keys, facts)
    target = registry.record_for("north-east")
    source = registry.record_for("east")
    assert target is not None and source is not None
    mutated = False

    def mutating_resolver(key_id: str, key_epoch: str) -> bytes:
        nonlocal mutated
        if not mutated:
            object.__setattr__(
                target,
                "method_group_digest",
                source.method_group_digest,
            )
            mutated = True
        return keys[(key_id, key_epoch)]

    receipt = evaluate_waggle_decision(
        proposal_auth,
        ring,
        supports,
        trust_registry=registry,
        key_resolver=mutating_resolver,
        now_utc=NOW,
    )

    assert mutated is True
    assert receipt.decision is WDPDecision.APPROVED_SHADOW
    assert receipt.trust_registry_digest == registry.registry_digest


def test_policy_and_signal_collection_bounds_fail_closed_without_overflow() -> None:
    with pytest.raises(WDPContractError, match="max_proposal_ttl_seconds"):
        WDPPolicyV1(max_proposal_ttl_seconds=10**100)

    ring, proposal, proposal_auth, registry, keys, facts = _context()
    signal = _signal(proposal, ring.neighbors[0], registry, keys, facts)
    receipt = _evaluate(
        proposal_auth,
        ring,
        (signal, signal),
        registry,
        keys,
        policy=WDPPolicyV1(max_signal_envelopes=1),
    )

    assert receipt.decision is WDPDecision.PROPOSAL_INVALID
    assert "signal_envelope_limit_exceeded" in receipt.reason_codes


def test_shadow_receipt_rejects_authority_or_incoherent_approval() -> None:
    ring, proposal, proposal_auth, registry, keys, _ = _context()
    silence = _evaluate(proposal_auth, ring, (), registry, keys)
    data = {
        "proposal_digest": proposal.proposal_digest,
        "trust_registry_digest": registry.registry_digest,
        "decision": WDPDecision.SILENCE,
        "evaluated_at_utc": NOW,
        "reason_codes": (),
        "accepted_signal_digests": (),
        "active_support_signal_digests": (),
        "stop_signal_digests": (),
        "challenge_signal_digests": (),
        "rejected_signals": (),
        "distinct_method_group_digests": (),
        "distinct_evidence_group_digests": (),
        "distinct_lineage_group_digests": (),
        "distinct_physical_failure_domains": (),
    }
    with pytest.raises(WDPContractError, match="hive commit"):
        WDPShadowReceiptV1(**data, hive_commit_applied=True)
    with pytest.raises(WDPContractError, match="support pair"):
        WDPShadowReceiptV1(
            **{**data, "decision": WDPDecision.APPROVED_SHADOW}
        )
    assert silence.approved is False
