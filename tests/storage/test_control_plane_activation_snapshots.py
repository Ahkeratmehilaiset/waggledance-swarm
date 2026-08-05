# SPDX-License-Identifier: BUSL-1.1
"""Integration tests for scoped immutable activation pointer CAS storage."""

from __future__ import annotations

import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pytest

from waggledance.core.capabilities.activation_contracts import (
    INITIAL_PREVIOUS_HEAD_DIGEST,
    build_activation_head,
    build_authority_ceiling,
    build_capability_variant,
    build_expression_context,
)
from waggledance.core.capabilities.activation_snapshot import (
    INITIAL_PREVIOUS_BUNDLE_DIGEST,
    build_activation_scope,
    build_activation_snapshot_bundle,
    canonicalize_activation_snapshot_publication,
    project_activation_snapshot_for_mirror,
)
from waggledance.core.capabilities.activation_provider import (
    ControlPlaneActivationProvider,
)
from waggledance.core.cell_identity import build_cell_identity
from waggledance.core.magma.activation_snapshot_artifact_store import (
    ActivationSnapshotArtifactStore,
)
from waggledance.core.storage.control_plane import (
    ActivationSnapshotCASConflict,
    ControlPlaneDB,
    ControlPlaneError,
)


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("ascii")).hexdigest()


def _identity(label: str):
    return build_cell_identity(
        pubkey_digest=_digest(f"pubkey:{label}"),
        genesis_material_digest=_digest(f"genesis:{label}"),
        created_at_utc="2026-08-05T08:00:00Z",
    )


@dataclass(frozen=True)
class _Publication:
    identity: object
    deployment_scope_digest: str
    bundle: dict
    canonical_bundle: bytes
    current_bindings: dict[str, str]


def _publication(
    *,
    identity,
    deployment_scope_digest: str,
    generation: int = 0,
    previous_head_digest: str = INITIAL_PREVIOUS_HEAD_DIGEST,
    previous_bundle_digest: str = INITIAL_PREVIOUS_BUNDLE_DIGEST,
    suffix: str = "a",
) -> _Publication:
    variant_ceiling = build_authority_ceiling(
        max_risk_class="local_artifact",
        authority_scope_digests=[_digest("scope:a"), _digest("scope:b")],
    )
    charter_ceiling = build_authority_ceiling(
        max_risk_class="internal_memory",
        authority_scope_digests=[_digest("scope:a"), _digest("scope:c")],
    )
    expressed_ceiling = build_authority_ceiling(
        max_risk_class="internal_memory",
        authority_scope_digests=[_digest("scope:a")],
    )
    variant = build_capability_variant(
        family_id="detect.fixture",
        risk_class="internal_memory",
        artifact_digest=_digest(f"artifact:{suffix}"),
        input_schema_digest=_digest("input"),
        output_schema_digest=_digest("output"),
        compatibility_digest=_digest("compatibility"),
        authority_ceiling_digest=variant_ceiling.ceiling_digest,
    )
    context = build_expression_context(
        profile_head_digest=_digest(f"profile:{suffix}"),
        policy_head_digest=_digest(f"policy:{suffix}"),
        resource_head_digest=_digest(f"resource:{suffix}"),
        domain_head_digest=_digest(f"domain:{suffix}"),
        environment_head_digest=_digest(f"environment:{suffix}"),
        charter_ceiling_digest=charter_ceiling.ceiling_digest,
        expressed_ceiling_digest=expressed_ceiling.ceiling_digest,
    )
    head = build_activation_head(
        generation=generation,
        previous_head_digest=previous_head_digest,
        expression_context_digest=context.context_digest,
        active_variant_digests=[variant.variant_digest],
        shadow_variant_digests=[],
    )
    bundle = build_activation_snapshot_bundle(
        deployment_scope_digest=deployment_scope_digest,
        cell_identity=identity,
        store_revision=generation,
        previous_bundle_digest=previous_bundle_digest,
        head=head.to_mapping(),
        expected_activation_head_digest=head.head_digest,
        context=context.to_mapping(),
        variants=[variant.to_mapping()],
        variant_ceilings=[variant_ceiling.to_mapping()],
        charter_ceiling=charter_ceiling.to_mapping(),
        expressed_ceiling=expressed_ceiling.to_mapping(),
        expected_profile_head_digest=context.profile_head_digest,
        expected_policy_head_digest=context.policy_head_digest,
        expected_resource_head_digest=context.resource_head_digest,
        expected_domain_head_digest=context.domain_head_digest,
        expected_environment_head_digest=context.environment_head_digest,
    )
    bindings = {
        "expected_profile_head_digest": context.profile_head_digest,
        "expected_policy_head_digest": context.policy_head_digest,
        "expected_resource_head_digest": context.resource_head_digest,
        "expected_domain_head_digest": context.domain_head_digest,
        "expected_environment_head_digest": context.environment_head_digest,
        "expected_charter_ceiling_digest": charter_ceiling.ceiling_digest,
        "expected_expressed_ceiling_digest": expressed_ceiling.ceiling_digest,
    }
    canonical = canonicalize_activation_snapshot_publication(
        bundle,
        cell_identity=identity,
        expected_deployment_scope_digest=deployment_scope_digest,
        **bindings,
    )
    return _Publication(
        identity=identity,
        deployment_scope_digest=deployment_scope_digest,
        bundle=bundle,
        canonical_bundle=canonical,
        current_bindings=bindings,
    )


def _append(
    db: ControlPlaneDB,
    publication: _Publication,
    *,
    expected=None,
):
    return db.append_activation_snapshot_pointer_cas(
        canonical_bundle=publication.canonical_bundle,
        cell_identity=publication.identity,
        expected_deployment_scope_digest=(
            publication.deployment_scope_digest
        ),
        expected_current_bundle_digest=(
            None if expected is None else expected.bundle_digest
        ),
        expected_current_activation_head_digest=(
            None if expected is None else expected.activation_head_digest
        ),
        expected_current_store_revision=(
            None if expected is None else expected.store_revision
        ),
        **publication.current_bindings,
    )


@pytest.fixture()
def db(tmp_path: Path):
    value = ControlPlaneDB(tmp_path / "control-plane.sqlite")
    yield value
    value.close()


def test_bootstrap_read_and_restart_preserve_exact_current_pointer(
    tmp_path: Path,
) -> None:
    path = tmp_path / "restart.sqlite"
    identity = _identity("cell-a")
    deployment = _digest("deployment")
    publication = _publication(
        identity=identity,
        deployment_scope_digest=deployment,
    )
    first_db = ControlPlaneDB(path)
    inserted = _append(first_db, publication)
    assert inserted.store_revision == 0
    assert inserted.bundle_digest == publication.bundle["bundle_digest"]
    assert inserted.scope_status == "active"
    first_db.close()

    restarted = ControlPlaneDB(path)
    try:
        current = restarted.get_current_activation_snapshot_pointer(
            deployment_scope_digest=deployment,
            cell_identity=identity,
        )
        assert current == inserted
        assert restarted.get_current_activation_snapshot_pointer(
            deployment_scope_digest=deployment,
            cell_identity=_identity("unknown"),
        ) is None
    finally:
        restarted.close()


def test_artifact_store_control_plane_and_mirror_provider_round_trip(
    db: ControlPlaneDB, tmp_path: Path
) -> None:
    identity = _identity("provider-cell")
    deployment = _digest("provider-deployment")
    publication = _publication(
        identity=identity,
        deployment_scope_digest=deployment,
    )
    store = ActivationSnapshotArtifactStore(tmp_path / "activation-artifacts")
    artifact = store.append(
        publication.canonical_bundle,
        cell_identity=identity,
        expected_deployment_scope_digest=deployment,
        **publication.current_bindings,
    )
    pointer = _append(db, publication)
    assert artifact.bundle_digest == pointer.bundle_digest

    provider = ControlPlaneActivationProvider(
        control_plane=db,
        artifact_reader=store.read,
        deployment_scope_digest=deployment,
        cell_identity=identity,
    )
    assert provider() == project_activation_snapshot_for_mirror(
        publication.bundle,
        cell_identity=identity,
        expected_deployment_scope_digest=deployment,
    )


def test_cells_with_identical_heads_have_independent_scoped_chains(
    db: ControlPlaneDB,
) -> None:
    deployment = _digest("deployment")
    first = _publication(
        identity=_identity("cell-a"),
        deployment_scope_digest=deployment,
    )
    second = _publication(
        identity=_identity("cell-b"),
        deployment_scope_digest=deployment,
    )
    assert (
        first.bundle["head"]["head_digest"]
        == second.bundle["head"]["head_digest"]
    )
    pointer_a = _append(db, first)
    pointer_b = _append(db, second)
    assert pointer_a.activation_scope_digest != pointer_b.activation_scope_digest
    assert pointer_a.bundle_digest != pointer_b.bundle_digest
    assert db._conn.execute(  # noqa: SLF001 - atomic row-count assertion
        "SELECT COUNT(*) AS count FROM activation_snapshot_pointers"
    ).fetchone()["count"] == 2


def test_current_read_fails_loud_on_corrupt_scope_registry_binding(
    db: ControlPlaneDB,
) -> None:
    identity = _identity("cell")
    deployment = _digest("deployment")
    scope = build_activation_scope(
        deployment_scope_digest=deployment,
        cell_identity=identity,
    )
    db._conn.execute(  # noqa: SLF001 - adversarial corruption fixture
        """
        INSERT INTO activation_scopes(
            activation_scope_digest,
            deployment_scope_digest,
            cell_id,
            created_at
        ) VALUES (?, ?, ?, ?)
        """,
        (
            scope["activation_scope_digest"],
            _digest("wrong-deployment"),
            _digest("wrong-cell"),
            "2026-08-05T08:00:00+00:00",
        ),
    )
    with pytest.raises(ControlPlaneError, match="registry binding is corrupt"):
        db.get_current_activation_snapshot_pointer(
            deployment_scope_digest=deployment,
            cell_identity=identity,
        )


def test_successful_cas_and_stale_loser_leave_one_successor(
    db: ControlPlaneDB,
) -> None:
    identity = _identity("cell")
    deployment = _digest("deployment")
    initial = _publication(
        identity=identity,
        deployment_scope_digest=deployment,
    )
    first_pointer = _append(db, initial)
    winner = _publication(
        identity=identity,
        deployment_scope_digest=deployment,
        generation=1,
        previous_head_digest=first_pointer.activation_head_digest,
        previous_bundle_digest=first_pointer.bundle_digest,
        suffix="winner",
    )
    loser = _publication(
        identity=identity,
        deployment_scope_digest=deployment,
        generation=1,
        previous_head_digest=first_pointer.activation_head_digest,
        previous_bundle_digest=first_pointer.bundle_digest,
        suffix="loser",
    )
    winner_pointer = _append(db, winner, expected=first_pointer)
    assert winner_pointer.store_revision == 1
    with pytest.raises(ActivationSnapshotCASConflict) as exc_info:
        _append(db, loser, expected=first_pointer)
    assert exc_info.value.reason == "stale_current_snapshot"
    assert db._conn.execute(  # noqa: SLF001
        "SELECT COUNT(*) AS count FROM activation_snapshot_pointers"
    ).fetchone()["count"] == 2


def test_two_database_writers_with_same_expected_pointer_have_one_winner(
    tmp_path: Path,
) -> None:
    path = tmp_path / "race.sqlite"
    db_a = ControlPlaneDB(path)
    db_b = ControlPlaneDB(path)
    identity = _identity("cell")
    deployment = _digest("deployment")
    initial = _publication(
        identity=identity,
        deployment_scope_digest=deployment,
    )
    current = _append(db_a, initial)
    candidates = [
        _publication(
            identity=identity,
            deployment_scope_digest=deployment,
            generation=1,
            previous_head_digest=current.activation_head_digest,
            previous_bundle_digest=current.bundle_digest,
            suffix=suffix,
        )
        for suffix in ("writer-a", "writer-b")
    ]
    barrier = threading.Barrier(2)

    def write(one_db: ControlPlaneDB, candidate: _Publication):
        barrier.wait(timeout=5)
        try:
            return "ok", _append(one_db, candidate, expected=current)
        except ActivationSnapshotCASConflict as exc:
            return "conflict", exc.reason

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(write, (db_a, db_b), candidates)
            )
        assert sorted(kind for kind, _value in results) == ["conflict", "ok"]
        conflicts = [value for kind, value in results if kind == "conflict"]
        assert conflicts == ["stale_current_snapshot"]
        assert db_a._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) AS count FROM activation_snapshot_pointers"
        ).fetchone()["count"] == 2
    finally:
        db_a.close()
        db_b.close()


@pytest.mark.parametrize(
    ("generation", "bundle_predecessor", "head_predecessor", "reason"),
    [
        (2, "current", "current", "revision_step"),
        (1, "wrong", "current", "previous_bundle_binding"),
        (1, "current", "wrong", "previous_head_binding"),
    ],
)
def test_cas_rejects_skips_and_wrong_predecessors_without_partial_append(
    db: ControlPlaneDB,
    generation: int,
    bundle_predecessor: str,
    head_predecessor: str,
    reason: str,
) -> None:
    identity = _identity("cell")
    deployment = _digest("deployment")
    current = _append(
        db,
        _publication(
            identity=identity,
            deployment_scope_digest=deployment,
        ),
    )
    candidate = _publication(
        identity=identity,
        deployment_scope_digest=deployment,
        generation=generation,
        previous_bundle_digest=(
            current.bundle_digest
            if bundle_predecessor == "current"
            else _digest("wrong-bundle")
        ),
        previous_head_digest=(
            current.activation_head_digest
            if head_predecessor == "current"
            else _digest("wrong-head")
        ),
        suffix="candidate",
    )
    with pytest.raises(ActivationSnapshotCASConflict) as exc_info:
        _append(db, candidate, expected=current)
    assert exc_info.value.reason == reason
    assert db._conn.execute(  # noqa: SLF001
        "SELECT COUNT(*) AS count FROM activation_snapshot_pointers"
    ).fetchone()["count"] == 1


def test_noncanonical_bytes_external_head_drift_and_outer_transaction_refuse(
    db: ControlPlaneDB,
) -> None:
    identity = _identity("cell")
    deployment = _digest("deployment")
    publication = _publication(
        identity=identity,
        deployment_scope_digest=deployment,
    )
    kwargs = {
        "cell_identity": identity,
        "expected_deployment_scope_digest": deployment,
        "expected_current_bundle_digest": None,
        "expected_current_activation_head_digest": None,
        "expected_current_store_revision": None,
        **publication.current_bindings,
    }

    with pytest.raises(ControlPlaneError, match="canonical form"):
        db.append_activation_snapshot_pointer_cas(
            canonical_bundle=b" " + publication.canonical_bundle,
            **kwargs,
        )
    stale = dict(kwargs)
    stale["expected_policy_head_digest"] = _digest("stale-policy")
    with pytest.raises(ControlPlaneError, match="current_policy_head"):
        db.append_activation_snapshot_pointer_cas(
            canonical_bundle=publication.canonical_bundle,
            **stale,
        )
    with pytest.raises(ControlPlaneError, match="immutable bytes"):
        db.append_activation_snapshot_pointer_cas(
            canonical_bundle=bytearray(publication.canonical_bundle),
            **kwargs,
        )

    with db.transaction():
        with pytest.raises(ControlPlaneError, match="cannot nest"):
            db.append_activation_snapshot_pointer_cas(
                canonical_bundle=publication.canonical_bundle,
                **kwargs,
            )
    assert db._conn.execute(  # noqa: SLF001
        "SELECT COUNT(*) AS count FROM activation_scopes"
    ).fetchone()["count"] == 0
    assert db._conn.execute(  # noqa: SLF001
        "SELECT COUNT(*) AS count FROM activation_snapshot_pointers"
    ).fetchone()["count"] == 0


def test_tombstone_is_visible_irreversible_and_blocks_reinitialization(
    db: ControlPlaneDB,
) -> None:
    identity = _identity("cell")
    deployment = _digest("deployment")
    initial = _publication(
        identity=identity,
        deployment_scope_digest=deployment,
    )
    current = _append(db, initial)
    tombstone = db.retire_activation_scope_cas(
        deployment_scope_digest=deployment,
        cell_identity=identity,
        expected_current_bundle_digest=current.bundle_digest,
        expected_current_activation_head_digest=current.activation_head_digest,
        expected_current_store_revision=current.store_revision,
        reason_digest=_digest("cell-death"),
    )
    assert tombstone.activation_scope_digest == current.activation_scope_digest
    retired = db.get_current_activation_snapshot_pointer(
        deployment_scope_digest=deployment,
        cell_identity=identity,
    )
    assert retired is not None and retired.scope_status == "retired"

    successor = _publication(
        identity=identity,
        deployment_scope_digest=deployment,
        generation=1,
        previous_head_digest=current.activation_head_digest,
        previous_bundle_digest=current.bundle_digest,
        suffix="after-death",
    )
    with pytest.raises(ActivationSnapshotCASConflict) as exc_info:
        _append(db, successor, expected=current)
    assert exc_info.value.reason == "scope_retired"
    with pytest.raises(ActivationSnapshotCASConflict) as retire_again:
        db.retire_activation_scope_cas(
            deployment_scope_digest=deployment,
            cell_identity=identity,
            expected_current_bundle_digest=current.bundle_digest,
            expected_current_activation_head_digest=(
                current.activation_head_digest
            ),
            expected_current_store_revision=current.store_revision,
            reason_digest=_digest("again"),
        )
    assert retire_again.value.reason == "scope_retired"
