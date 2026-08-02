from __future__ import annotations

import copy
import hashlib
import itertools
from dataclasses import replace

import pytest

from waggledance.core.hex_topology.understanding_cell_recovery import (
    CHECKPOINT_SCHEMA,
    AuthenticatedCheckpointEnvelopeV1,
    CellCheckpointManifestV1,
    CellFenceError,
    CellRecoveryError,
    NoRecoveryQuorumError,
    StaleCellFenceError,
    TrustedRecoveryRegistryV1,
    TrustedReplicaRecordV1,
    plan_cell_recovery,
    rebuild_from_selection,
    select_recovery_checkpoint,
    validate_cell_message_fence,
)
from waggledance.core.learning.understanding_contracts import HexCellAddressV1
from waggledance.core.magma.canonical import sha256_digest


KEY_EPOCH = "2026-08-02"
CELL_IDENTITY = sha256_digest({"cell_identity": "understanding-temperature"})
HEAD_A = sha256_digest({"ledger_head": "a"})
HEAD_B = sha256_digest({"ledger_head": "b"})
HEAD_C = sha256_digest({"ledger_head": "c"})
PROJECTION_A = sha256_digest({"projection": "a"})
PROJECTION_B = sha256_digest({"projection": "b"})
PROJECTION_C = sha256_digest({"projection": "c"})


def _key(replica: str) -> bytes:
    return hashlib.sha256(f"recovery-key:{replica}".encode()).digest()


def _domain(label: str) -> str:
    return sha256_digest({"replica_failure_domain": label})


def _cell(
    *,
    cell_id: str = "understanding-temperature",
    q: int = 2,
    r: int = -1,
    incarnation_id: str = "incarnation-7",
    generation: int = 7,
    fence: int = 19,
) -> HexCellAddressV1:
    return HexCellAddressV1(
        cell_id=cell_id,
        q=q,
        r=r,
        incarnation_id=incarnation_id,
        generation=generation,
        fence=fence,
    )


def _trust(
    *,
    domains: dict[str, str] | None = None,
) -> tuple[
    TrustedRecoveryRegistryV1,
    dict[tuple[str, str], bytes],
]:
    assignments = domains or {
        "replica-a": "host-a",
        "replica-b": "host-b",
        "replica-c": "host-c",
    }
    records = tuple(
        TrustedReplicaRecordV1(
            replica_identity=replica,
            auth_key_id=f"key-{replica}",
            key_epoch=KEY_EPOCH,
            failure_domain_digest=_domain(assignments[replica]),
            logical_cell_id="understanding-temperature",
            q=2,
            r=-1,
            cell_identity_digest=CELL_IDENTITY,
        )
        for replica in ("replica-a", "replica-b", "replica-c")
    )
    keys = {
        (f"key-{replica}", KEY_EPOCH): _key(replica)
        for replica in ("replica-a", "replica-b", "replica-c")
    }
    return TrustedRecoveryRegistryV1.create(records), keys


def _resolver(keys: dict[tuple[str, str], bytes]):
    def resolve(key_id: str, key_epoch: str) -> bytes:
        return keys[(key_id, key_epoch)]

    return resolve


def _manifest(
    replica: str,
    registry: TrustedRecoveryRegistryV1,
    *,
    cell: HexCellAddressV1 | None = None,
    identity: str = CELL_IDENTITY,
    head: str = HEAD_A,
    count: int = 41,
    projection: str = PROJECTION_A,
    declared_failure_domain: str | None = None,
) -> CellCheckpointManifestV1:
    record = registry.record_for(replica)
    assert record is not None
    return CellCheckpointManifestV1.create(
        cell=cell or _cell(),
        cell_identity_digest=identity,
        replica_identity=replica,
        replica_failure_domain_digest=(
            declared_failure_domain or record.failure_domain_digest
        ),
        ledger_head_digest=head,
        ledger_event_count=count,
        projection_digest=projection,
    )


def _envelope(
    manifest: CellCheckpointManifestV1,
    registry: TrustedRecoveryRegistryV1,
    keys: dict[tuple[str, str], bytes],
    *,
    hmac_key: bytes | None = None,
) -> AuthenticatedCheckpointEnvelopeV1:
    record = registry.record_for(manifest.replica_identity)
    assert record is not None
    return AuthenticatedCheckpointEnvelopeV1.create(
        manifest=manifest,
        registry_digest=registry.registry_digest,
        key_id=record.auth_key_id,
        key_epoch=record.key_epoch,
        hmac_key=hmac_key or keys[(record.auth_key_id, record.key_epoch)],
    )


def _evidence(
    registry: TrustedRecoveryRegistryV1,
    keys: dict[tuple[str, str], bytes],
    *,
    cells: dict[str, HexCellAddressV1] | None = None,
    states: dict[str, tuple[str, int, str]] | None = None,
) -> tuple[AuthenticatedCheckpointEnvelopeV1, ...]:
    cells = cells or {}
    states = states or {}
    result = []
    for replica in ("replica-a", "replica-b", "replica-c"):
        head, count, projection = states.get(
            replica, (HEAD_A, 41, PROJECTION_A)
        )
        result.append(
            _envelope(
                _manifest(
                    replica,
                    registry,
                    cell=cells.get(replica),
                    head=head,
                    count=count,
                    projection=projection,
                ),
                registry,
                keys,
            )
        )
    return tuple(result)


def _select(
    evidence: tuple[AuthenticatedCheckpointEnvelopeV1, ...]
    | list[AuthenticatedCheckpointEnvelopeV1],
    registry: TrustedRecoveryRegistryV1,
    keys: dict[tuple[str, str], bytes],
):
    return select_recovery_checkpoint(
        evidence,
        trust_registry=registry,
        key_resolver=_resolver(keys),
    )


def test_checkpoint_round_trip_is_exact_and_digest_bound() -> None:
    registry, _ = _trust()
    manifest = _manifest("replica-a", registry)

    parsed = CellCheckpointManifestV1.from_mapping(manifest.to_mapping())

    assert parsed == manifest
    assert parsed.state_digest == manifest.state_digest
    assert parsed.to_mapping()["schema_version"] == CHECKPOINT_SCHEMA


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("cell_identity_digest", sha256_digest({"identity": "other"})),
        ("replica_identity", "replica-z"),
        ("replica_failure_domain_digest", _domain("other")),
        ("ledger_head_digest", HEAD_B),
        ("ledger_event_count", 42),
        ("projection_digest", PROJECTION_B),
    ],
)
def test_checkpoint_tampering_is_rejected(field: str, replacement: object) -> None:
    registry, _ = _trust()
    document = _manifest("replica-a", registry).to_mapping()
    document[field] = replacement

    with pytest.raises(CellRecoveryError, match="digest mismatch"):
        CellCheckpointManifestV1.from_mapping(document)


def test_torn_unknown_and_noncanonical_manifests_fail_closed() -> None:
    registry, _ = _trust()
    complete = _manifest("replica-a", registry).to_mapping()
    torn = copy.deepcopy(complete)
    del torn["projection_digest"]
    unknown = copy.deepcopy(complete)
    unknown["schema_version"] = "wd.understanding_cell_checkpoint.v2"
    extra = copy.deepcopy(complete)
    extra["unverified"] = True
    invalid_count = copy.deepcopy(complete)
    invalid_count["ledger_event_count"] = True

    with pytest.raises(CellRecoveryError, match="torn"):
        CellCheckpointManifestV1.from_mapping(torn)
    with pytest.raises(CellRecoveryError, match="unknown checkpoint schema"):
        CellCheckpointManifestV1.from_mapping(unknown)
    with pytest.raises(CellRecoveryError, match="unknown fields"):
        CellCheckpointManifestV1.from_mapping(extra)
    with pytest.raises(CellRecoveryError, match="ledger_event_count"):
        CellCheckpointManifestV1.from_mapping(invalid_count)


def test_authenticated_two_of_three_selects_current_state_and_stale_minority() -> None:
    registry, keys = _trust()
    stale = _cell(
        incarnation_id="incarnation-6", generation=6, fence=18
    )
    evidence = _evidence(
        registry,
        keys,
        cells={"replica-c": stale},
        states={"replica-c": (HEAD_B, 40, PROJECTION_B)},
    )

    selection = _select(list(reversed(evidence)), registry, keys)

    assert selection.source_cell == _cell()
    assert selection.ledger_head_digest == HEAD_A
    assert selection.agreeing_replica_identities == ("replica-a", "replica-b")
    assert selection.observed_max_generation == 7
    assert selection.observed_max_fence == 19


def test_selection_is_deterministic_for_every_input_permutation() -> None:
    registry, keys = _trust()
    evidence = _evidence(
        registry,
        keys,
        states={"replica-b": (HEAD_B, 42, PROJECTION_B)},
    )

    selections = [
        _select(order, registry, keys) for order in itertools.permutations(evidence)
    ]

    assert len({item.selection_digest for item in selections}) == 1
    assert all(
        item.agreeing_replica_identities == ("replica-a", "replica-c")
        for item in selections
    )


def test_one_key_cannot_forge_three_recovery_replicas() -> None:
    registry, keys = _trust()
    forged = tuple(
        _envelope(
            _manifest(replica, registry),
            registry,
            keys,
            hmac_key=keys[("key-replica-a", KEY_EPOCH)],
        )
        for replica in ("replica-a", "replica-b", "replica-c")
    )

    with pytest.raises(CellRecoveryError, match="authentication failed"):
        _select(forged, registry, keys)


def test_recovery_registry_rejects_duplicate_key_metadata() -> None:
    registry, _ = _trust()
    first, second, third = registry.records
    duplicate = replace(
        second,
        auth_key_id=first.auth_key_id,
        key_epoch=first.key_epoch,
    )

    with pytest.raises(CellRecoveryError, match="key metadata"):
        TrustedRecoveryRegistryV1.create((first, duplicate, third))


def test_distinct_replica_ids_with_shared_key_material_cannot_form_quorum() -> None:
    registry, keys = _trust()
    shared = keys[("key-replica-a", KEY_EPOCH)]
    for replica in ("replica-a", "replica-b", "replica-c"):
        keys[(f"key-{replica}", KEY_EPOCH)] = shared
    evidence = _evidence(registry, keys)

    with pytest.raises(CellRecoveryError, match="key material is not independent"):
        _select(evidence, registry, keys)


def test_resolver_callback_cannot_mutate_registry_or_manifest_snapshots() -> None:
    registry, keys = _trust()
    evidence = _evidence(registry, keys)
    record = registry.record_for("replica-b")
    assert record is not None
    original_domain = record.failure_domain_digest
    replacement_domain = _domain("callback-mutated-host")
    mutated = False

    def mutating_resolver(key_id: str, key_epoch: str) -> bytes:
        nonlocal mutated
        if not mutated:
            object.__setattr__(record, "failure_domain_digest", replacement_domain)
            object.__setattr__(
                evidence[1].manifest,
                "replica_failure_domain_digest",
                replacement_domain,
            )
            mutated = True
        return keys[(key_id, key_epoch)]

    selection = select_recovery_checkpoint(
        evidence,
        trust_registry=registry,
        key_resolver=mutating_resolver,
    )

    assert mutated is True
    witness = next(
        item for item in selection.witnesses if item.replica_identity == "replica-b"
    )
    assert witness.failure_domain_digest == original_domain
    assert witness.failure_domain_digest != replacement_domain


def test_mutated_manifest_type_is_normalized_to_recovery_error() -> None:
    registry, keys = _trust()
    evidence = list(_evidence(registry, keys))
    object.__setattr__(evidence[0], "manifest", "not-a-manifest")

    with pytest.raises(CellRecoveryError, match="manifest is invalid"):
        _select(evidence, registry, keys)


def test_bare_self_hashed_manifests_cannot_manufacture_quorum() -> None:
    registry, keys = _trust()
    bare = [
        _manifest(replica, registry)
        for replica in ("replica-a", "replica-b", "replica-c")
    ]

    with pytest.raises(CellRecoveryError, match="bare checkpoint"):
        _select(bare, registry, keys)  # type: ignore[arg-type]


def test_stale_majority_is_rejected_when_authenticated_minority_has_newer_fence() -> None:
    registry, keys = _trust()
    old = _cell(incarnation_id="inc-old", generation=6, fence=18)
    newer = _cell(incarnation_id="inc-new", generation=7, fence=19)
    evidence = _evidence(
        registry,
        keys,
        cells={
            "replica-a": old,
            "replica-b": old,
            "replica-c": newer,
        },
        states={
            "replica-a": (HEAD_B, 40, PROJECTION_B),
            "replica-b": (HEAD_B, 40, PROJECTION_B),
            "replica-c": (HEAD_A, 41, PROJECTION_A),
        },
    )

    with pytest.raises(NoRecoveryQuorumError, match="newer generation or fence"):
        _select(evidence, registry, keys)


def test_rebuild_advances_above_all_authenticated_observations_and_has_no_authority() -> None:
    registry, keys = _trust()
    evidence = _evidence(
        registry,
        keys,
        states={"replica-c": (HEAD_B, 42, PROJECTION_B)},
    )

    rebuilt = plan_cell_recovery(
        evidence,
        trust_registry=registry,
        key_resolver=_resolver(keys),
        new_incarnation_id="incarnation-8",
    )

    assert rebuilt.source_cell == _cell()
    assert rebuilt.rebuilt_cell == _cell(
        incarnation_id="incarnation-8", generation=8, fence=20
    )
    assert rebuilt.agreeing_replica_identities == ("replica-a", "replica-b")
    assert len(rebuilt.witnesses) == 3
    assert rebuilt.router_authority is False
    assert rebuilt.action_authority is False
    assert rebuilt.builder_authority is False
    assert rebuilt.to_mapping()["router_authority"] is False


def test_rebuild_serialization_rejects_mutated_authority() -> None:
    registry, keys = _trust()
    rebuilt = plan_cell_recovery(
        _evidence(registry, keys),
        trust_registry=registry,
        key_resolver=_resolver(keys),
        new_incarnation_id="incarnation-8",
    )
    object.__setattr__(rebuilt, "builder_authority", True)

    with pytest.raises(CellRecoveryError, match="cannot grant authority"):
        rebuilt.to_mapping()


def test_witness_digest_preserves_replica_to_failure_domain_pairing() -> None:
    first_registry, first_keys = _trust(
        domains={
            "replica-a": "host-1",
            "replica-b": "host-2",
            "replica-c": "host-3",
        }
    )
    second_registry, second_keys = _trust(
        domains={
            "replica-a": "host-2",
            "replica-b": "host-1",
            "replica-c": "host-3",
        }
    )
    first = _select(
        _evidence(first_registry, first_keys), first_registry, first_keys
    )
    second = _select(
        _evidence(second_registry, second_keys), second_registry, second_keys
    )

    first_pairs = {
        witness.replica_identity: witness.failure_domain_digest
        for witness in first.witnesses
    }
    second_pairs = {
        witness.replica_identity: witness.failure_domain_digest
        for witness in second.witnesses
    }
    assert first_pairs["replica-a"] == _domain("host-1")
    assert second_pairs["replica-a"] == _domain("host-2")
    assert first.selection_digest != second.selection_digest


def test_failure_domain_claim_is_derived_from_registry_not_manifest() -> None:
    registry, keys = _trust()
    forged_manifest = _manifest(
        "replica-a",
        registry,
        declared_failure_domain=_domain("invented-host"),
    )
    forged = _envelope(forged_manifest, registry, keys)
    evidence = (
        forged,
        _envelope(_manifest("replica-b", registry), registry, keys),
        _envelope(_manifest("replica-c", registry), registry, keys),
    )

    with pytest.raises(CellRecoveryError, match="not registry-derived"):
        _select(evidence, registry, keys)


def test_bare_selection_is_not_a_public_rebuild_authority() -> None:
    registry, keys = _trust()
    selection = _select(_evidence(registry, keys), registry, keys)

    with pytest.raises(CellRecoveryError, match="bare recovery selections"):
        rebuild_from_selection(
            selection,
            new_incarnation_id="incarnation-8",
        )


def test_rebuild_requires_incarnation_not_seen_in_any_witness() -> None:
    registry, keys = _trust()
    evidence = _evidence(registry, keys)

    with pytest.raises(CellRecoveryError, match="globally new"):
        plan_cell_recovery(
            evidence,
            trust_registry=registry,
            key_resolver=_resolver(keys),
            new_incarnation_id="incarnation-7",
        )


def test_one_one_one_split_and_wrong_cardinality_have_no_quorum() -> None:
    registry, keys = _trust()
    split = _evidence(
        registry,
        keys,
        states={
            "replica-a": (HEAD_A, 41, PROJECTION_A),
            "replica-b": (HEAD_B, 42, PROJECTION_B),
            "replica-c": (HEAD_C, 43, PROJECTION_C),
        },
    )
    with pytest.raises(NoRecoveryQuorumError, match="no unambiguous"):
        _select(split, registry, keys)
    with pytest.raises(NoRecoveryQuorumError, match="exactly three"):
        _select(list(split[:2]), registry, keys)


def test_duplicate_or_missing_trusted_replica_cannot_manufacture_quorum() -> None:
    registry, keys = _trust()
    evidence = _evidence(registry, keys)
    duplicate = (evidence[0], evidence[0], evidence[2])

    with pytest.raises(NoRecoveryQuorumError, match="each trusted replica"):
        _select(duplicate, registry, keys)


def test_registry_rejects_shared_failure_domain_and_digest_tampering() -> None:
    records = tuple(
        TrustedReplicaRecordV1(
            replica_identity=replica,
            auth_key_id=f"key-{replica}",
            key_epoch=KEY_EPOCH,
            failure_domain_digest=_domain(
                "shared" if replica != "replica-c" else "other"
            ),
            logical_cell_id="understanding-temperature",
            q=2,
            r=-1,
            cell_identity_digest=CELL_IDENTITY,
        )
        for replica in ("replica-a", "replica-b", "replica-c")
    )
    with pytest.raises(CellRecoveryError, match="distinct failure domains"):
        TrustedRecoveryRegistryV1.create(records)

    registry, _ = _trust()
    with pytest.raises(CellRecoveryError, match="registry digest mismatch"):
        TrustedRecoveryRegistryV1(
            records=registry.records,
            registry_digest=sha256_digest({"forged": True}),
        )


def test_logical_cell_claim_and_corrupt_manifest_fail_before_voting() -> None:
    registry, keys = _trust()
    foreign = _manifest(
        "replica-c",
        registry,
        cell=_cell(cell_id="other-cell", q=3),
    )
    evidence = (
        _envelope(_manifest("replica-a", registry), registry, keys),
        _envelope(_manifest("replica-b", registry), registry, keys),
        _envelope(foreign, registry, keys),
    )
    with pytest.raises(CellRecoveryError, match="logical cell"):
        _select(evidence, registry, keys)

    corrupt = _manifest("replica-c", registry).to_mapping()
    corrupt["projection_digest"] = PROJECTION_B
    with pytest.raises(CellRecoveryError, match="digest mismatch"):
        CellCheckpointManifestV1.from_mapping(corrupt)


def test_authenticated_envelope_tampering_and_short_keys_fail_closed() -> None:
    registry, keys = _trust()
    evidence = list(_evidence(registry, keys))
    object.__setattr__(
        evidence[0],
        "auth_tag",
        "hmac-sha256:" + ("0" * 64),
    )
    with pytest.raises(CellRecoveryError, match="authentication failed"):
        _select(evidence, registry, keys)

    with pytest.raises(CellRecoveryError, match="at least 32"):
        AuthenticatedCheckpointEnvelopeV1.create(
            manifest=_manifest("replica-a", registry),
            registry_digest=registry.registry_digest,
            key_id="key-replica-a",
            key_epoch=KEY_EPOCH,
            hmac_key=b"short",
        )


def test_fence_validator_rejects_old_and_noncurrent_messages() -> None:
    current = _cell(
        incarnation_id="incarnation-8", generation=8, fence=20
    )

    validate_cell_message_fence(current, current)
    with pytest.raises(StaleCellFenceError, match="older generation fence"):
        validate_cell_message_fence(_cell(), current)
    with pytest.raises(CellFenceError, match="current incarnation"):
        validate_cell_message_fence(
            _cell(incarnation_id="intruder", generation=8, fence=20),
            current,
        )
    with pytest.raises(CellFenceError, match="different logical cell"):
        validate_cell_message_fence(
            _cell(cell_id="other-cell", q=3, generation=8, fence=20),
            current,
        )
    with pytest.raises(CellFenceError, match="current incarnation"):
        validate_cell_message_fence(
            _cell(incarnation_id="future", generation=9, fence=21),
            current,
        )
