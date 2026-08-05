# SPDX-License-Identifier: BUSL-1.1
"""Integration tests for immutable scope-local consensus-expectation pins."""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

import pytest

from waggledance.core.capabilities.activation_admission_intent import (
    build_activation_admission_intent,
)
from waggledance.core.capabilities.activation_snapshot import (
    build_activation_scope,
)
from waggledance.core.cell_identity import build_cell_identity
from waggledance.core.orchestration.attested_consensus_expectation import (
    INITIAL_PREVIOUS_EXPECTATION_HEAD_DIGEST,
    build_attested_consensus_expectation,
    canonicalize_attested_consensus_expectation,
    expectation_bindings_from_attested_consensus_expectation,
)
from waggledance.core.orchestration.attested_consensus_expectation_provider import (
    AttestedConsensusExpectationProviderError,
    ControlPlaneAttestedConsensusExpectationProvider,
)
from waggledance.core.orchestration.attested_consensus_shadow import (
    GATE_EXPECTATION_KEYS,
)
from waggledance.core.storage.control_plane import (
    AttestedConsensusExpectationCASConflict,
    ControlPlaneDB,
    ControlPlaneError,
)
from waggledance.core.storage.control_plane_schema import (
    MIGRATIONS,
    SCHEMA_VERSION,
)


ZERO_DIGEST = "sha256:" + "0" * 64
CREATED_AT = "2026-08-05T12:00:00+00:00"


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("ascii")).hexdigest()


def _identity(label: str):
    return build_cell_identity(
        pubkey_digest=_digest(f"pubkey:{label}"),
        genesis_material_digest=_digest(f"genesis:{label}"),
        created_at_utc="2026-08-05T12:00:00Z",
    )


@dataclass(frozen=True)
class _Activation:
    identity: object
    deployment_scope_digest: str
    activation_scope_digest: str
    bundle_digest: str
    activation_head_digest: str
    store_revision: int


@dataclass(frozen=True)
class _Candidate:
    intent: dict[str, object]
    pin: dict[str, object]
    canonical: bytes
    closed_head: str


def _insert_activation_pointer(
    db: ControlPlaneDB,
    *,
    identity,
    deployment_scope_digest: str,
    suffix: str,
    previous: _Activation | None = None,
) -> _Activation:
    scope = build_activation_scope(
        deployment_scope_digest=deployment_scope_digest,
        cell_identity=identity,
    )
    if previous is None:
        db._conn.execute(  # noqa: SLF001 - controlled storage fixture
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
                scope["deployment_scope_digest"],
                scope["cell_id"],
                CREATED_AT,
            ),
        )
        revision = 0
        previous_bundle = ZERO_DIGEST
        previous_head = ZERO_DIGEST
    else:
        revision = previous.store_revision + 1
        previous_bundle = previous.bundle_digest
        previous_head = previous.activation_head_digest
    bundle = _digest(f"activation-bundle:{suffix}")
    head = _digest(f"activation-head:{suffix}")
    db._conn.execute(  # noqa: SLF001 - controlled storage fixture
        """
        INSERT INTO activation_snapshot_pointers(
            activation_scope_digest,
            bundle_digest,
            store_revision,
            previous_bundle_digest,
            activation_head_digest,
            previous_activation_head_digest,
            expression_context_digest,
            expected_profile_head_digest,
            expected_policy_head_digest,
            expected_resource_head_digest,
            expected_domain_head_digest,
            expected_environment_head_digest,
            charter_ceiling_digest,
            expressed_ceiling_digest,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            scope["activation_scope_digest"],
            bundle,
            revision,
            previous_bundle,
            head,
            previous_head,
            _digest(f"context:{suffix}"),
            _digest(f"profile:{suffix}"),
            _digest(f"policy-head:{suffix}"),
            _digest(f"resource:{suffix}"),
            _digest(f"domain:{suffix}"),
            _digest(f"environment:{suffix}"),
            _digest(f"charter:{suffix}"),
            _digest(f"expressed:{suffix}"),
            CREATED_AT,
        ),
    )
    return _Activation(
        identity=identity,
        deployment_scope_digest=deployment_scope_digest,
        activation_scope_digest=scope["activation_scope_digest"],
        bundle_digest=bundle,
        activation_head_digest=head,
        store_revision=revision,
    )


def _candidate(
    current: _Activation,
    *,
    expectation_generation: int,
    previous_expectation_head_digest: str,
    suffix: str,
) -> _Candidate:
    closed = _digest(f"closed-log:{suffix}")
    intent = build_activation_admission_intent(
        activation_scope_digest=current.activation_scope_digest,
        query_digest=_digest(f"query:{suffix}"),
        expected_current_bundle_digest=current.bundle_digest,
        expected_current_activation_head_digest=(
            current.activation_head_digest
        ),
        expected_current_store_revision=current.store_revision,
        proposed_bundle_digest=_digest(f"proposed-bundle:{suffix}"),
        proposed_activation_head_digest=_digest(f"proposed-head:{suffix}"),
        proposed_store_revision=current.store_revision + 1,
        proposed_previous_bundle_digest=current.bundle_digest,
        proposed_previous_activation_head_digest=(
            current.activation_head_digest
        ),
        trust_registry_head_digest=_digest(f"trust:{suffix}"),
        attestation_log_base_head_digest=_digest(f"base-log:{suffix}"),
        consensus_policy_digest=_digest(f"consensus-policy:{suffix}"),
        required_independent_support=2,
    )
    pin = build_attested_consensus_expectation(
        generation=expectation_generation,
        previous_expectation_head_digest=(
            previous_expectation_head_digest
        ),
        activation_admission_intent=intent,
        expected_attestation_log_closed_head_digest=closed,
    )
    return _Candidate(
        intent=intent,
        pin=pin,
        canonical=canonicalize_attested_consensus_expectation(pin),
        closed_head=closed,
    )


def _rechain_candidate(
    source: _Candidate,
    *,
    generation: int,
    previous: str,
) -> _Candidate:
    pin = build_attested_consensus_expectation(
        generation=generation,
        previous_expectation_head_digest=previous,
        activation_admission_intent=source.intent,
        expected_attestation_log_closed_head_digest=source.closed_head,
    )
    return _Candidate(
        intent=source.intent,
        pin=pin,
        canonical=canonicalize_attested_consensus_expectation(pin),
        closed_head=source.closed_head,
    )


def _append(
    db: ControlPlaneDB,
    current: _Activation,
    candidate: _Candidate,
    expected=None,
):
    return db.append_attested_consensus_expectation_cas(
        canonical_expectation=candidate.canonical,
        activation_admission_intent=candidate.intent,
        expected_attestation_log_closed_head_digest=candidate.closed_head,
        cell_identity=current.identity,
        expected_deployment_scope_digest=current.deployment_scope_digest,
        expected_current_expectation_head_digest=(
            None if expected is None else expected.expectation_head_digest
        ),
        expected_current_expectation_generation=(
            None if expected is None else expected.generation
        ),
    )


def _raw_insert_candidate(
    conn: sqlite3.Connection,
    candidate: _Candidate,
    *,
    explicit_id: int | None = None,
) -> None:
    pin = candidate.pin
    bindings = expectation_bindings_from_attested_consensus_expectation(pin)
    values = {
        "activation_scope_digest": bindings[
            "expected_activation_scope_digest"
        ],
        "generation": pin["generation"],
        "previous_expectation_head_digest": pin[
            "previous_expectation_head_digest"
        ],
        "expectation_head_digest": pin["expectation_head_digest"],
        "admission_challenge_digest": pin["admission_challenge_digest"],
        **{
            key: value
            for key, value in bindings.items()
            if key != "expected_activation_scope_digest"
        },
        "canonical_expectation": candidate.canonical,
        "created_at": CREATED_AT,
    }
    if explicit_id is not None:
        values = {"id": explicit_id, **values}
    columns = tuple(values)
    conn.execute(
        "INSERT INTO attested_consensus_expectations("
        + ", ".join(columns)
        + ") VALUES ("
        + ", ".join("?" for _ in columns)
        + ")",
        tuple(values[column] for column in columns),
    )


def _create_v5_database(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        for version in range(1, 6):
            for statement in MIGRATIONS[version]:
                conn.execute(statement)
        conn.execute(
            """
            INSERT OR REPLACE INTO schema_meta(key, value, updated_at)
            VALUES ('schema_version', '5', ?)
            """,
            (CREATED_AT,),
        )
        scope = _digest("v5-preserved-scope")
        conn.execute(
            "INSERT INTO activation_scopes VALUES (?, ?, ?, ?)",
            (
                scope,
                _digest("v5-preserved-deployment"),
                _digest("v5-preserved-cell"),
                CREATED_AT,
            ),
        )
        conn.execute(
            """
            INSERT INTO activation_snapshot_pointers(
                activation_scope_digest,
                bundle_digest,
                store_revision,
                previous_bundle_digest,
                activation_head_digest,
                previous_activation_head_digest,
                expression_context_digest,
                expected_profile_head_digest,
                expected_policy_head_digest,
                expected_resource_head_digest,
                expected_domain_head_digest,
                expected_environment_head_digest,
                charter_ceiling_digest,
                expressed_ceiling_digest,
                created_at
            ) VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                scope,
                _digest("v5-preserved-bundle"),
                ZERO_DIGEST,
                _digest("v5-preserved-head"),
                ZERO_DIGEST,
                _digest("v5-preserved-context"),
                _digest("v5-preserved-profile"),
                _digest("v5-preserved-policy"),
                _digest("v5-preserved-resource"),
                _digest("v5-preserved-domain"),
                _digest("v5-preserved-environment"),
                _digest("v5-preserved-charter"),
                _digest("v5-preserved-expressed"),
                CREATED_AT,
            ),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def db(tmp_path: Path):
    value = ControlPlaneDB(tmp_path / "control-plane.sqlite")
    yield value
    value.close()


def test_v5_to_v6_migration_is_additive_empty_and_idempotent(
    tmp_path: Path,
) -> None:
    path = tmp_path / "v5.sqlite"
    _create_v5_database(path)
    first = ControlPlaneDB(path)
    try:
        assert SCHEMA_VERSION == 6
        assert first.schema_version() == 6
        assert first._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM attested_consensus_expectations"
        ).fetchone()[0] == 0
        assert first._conn.execute(  # noqa: SLF001
            "SELECT bundle_digest FROM activation_snapshot_pointers"
        ).fetchone()[0] == _digest("v5-preserved-bundle")
    finally:
        first.close()
    reopened = ControlPlaneDB(path)
    try:
        assert reopened.schema_version() == 6
    finally:
        reopened.close()


def test_bootstrap_restart_and_provider_return_exact_private_bindings(
    tmp_path: Path,
) -> None:
    path = tmp_path / "restart.sqlite"
    identity = _identity("restart")
    deployment = _digest("deployment:restart")
    first = ControlPlaneDB(path)
    activation = _insert_activation_pointer(
        first,
        identity=identity,
        deployment_scope_digest=deployment,
        suffix="restart-current",
    )
    candidate = _candidate(
        activation,
        expectation_generation=0,
        previous_expectation_head_digest=(
            INITIAL_PREVIOUS_EXPECTATION_HEAD_DIGEST
        ),
        suffix="restart-a",
    )
    inserted = _append(first, activation, candidate)
    first.close()

    restarted = ControlPlaneDB(path)
    try:
        current = restarted.get_current_attested_consensus_expectation(
            deployment_scope_digest=deployment,
            cell_identity=identity,
        )
        assert current == inserted
        provider = ControlPlaneAttestedConsensusExpectationProvider(
            control_plane=restarted,
            deployment_scope_digest=deployment,
            cell_identity=identity,
        )
        projected = provider()
        assert type(projected) is dict
        assert set(projected) == GATE_EXPECTATION_KEYS
        assert projected == (
            expectation_bindings_from_attested_consensus_expectation(
                candidate.pin
            )
        )
        projected["expected_query_digest"] = _digest("mutated")
        assert provider()["expected_query_digest"] == _digest(
            "query:restart-a"
        )
    finally:
        restarted.close()


def test_successor_chain_and_nonadjacent_a_b_a_replay_fail_atomically(
    db: ControlPlaneDB,
) -> None:
    identity = _identity("replay")
    activation = _insert_activation_pointer(
        db,
        identity=identity,
        deployment_scope_digest=_digest("deployment:replay"),
        suffix="replay-current",
    )
    candidate_a = _candidate(
        activation,
        expectation_generation=0,
        previous_expectation_head_digest=(
            INITIAL_PREVIOUS_EXPECTATION_HEAD_DIGEST
        ),
        suffix="a",
    )
    record_a = _append(db, activation, candidate_a)
    candidate_b = _candidate(
        activation,
        expectation_generation=1,
        previous_expectation_head_digest=record_a.expectation_head_digest,
        suffix="b",
    )
    record_b = _append(db, activation, candidate_b, record_a)
    replay_a = _rechain_candidate(
        candidate_a,
        generation=2,
        previous=record_b.expectation_head_digest,
    )

    with pytest.raises(AttestedConsensusExpectationCASConflict) as error:
        _append(db, activation, replay_a, record_b)
    assert error.value.reason == "admission_challenge_replay"
    with pytest.raises(sqlite3.IntegrityError, match="immutable key collision"):
        _raw_insert_candidate(db._conn, replay_a)  # noqa: SLF001
    assert db._conn.execute(  # noqa: SLF001
        "SELECT COUNT(*) FROM attested_consensus_expectations"
    ).fetchone()[0] == 2


def test_two_database_writers_have_exactly_one_successor(
    tmp_path: Path,
) -> None:
    path = tmp_path / "race.sqlite"
    db_a = ControlPlaneDB(path)
    db_b = ControlPlaneDB(path)
    identity = _identity("race")
    deployment = _digest("deployment:race")
    activation = _insert_activation_pointer(
        db_a,
        identity=identity,
        deployment_scope_digest=deployment,
        suffix="race-current",
    )
    initial = _candidate(
        activation,
        expectation_generation=0,
        previous_expectation_head_digest=(
            INITIAL_PREVIOUS_EXPECTATION_HEAD_DIGEST
        ),
        suffix="race-initial",
    )
    current = _append(db_a, activation, initial)
    candidates = tuple(
        _candidate(
            activation,
            expectation_generation=1,
            previous_expectation_head_digest=(
                current.expectation_head_digest
            ),
            suffix=suffix,
        )
        for suffix in ("writer-a", "writer-b")
    )
    barrier = threading.Barrier(2)

    def write(one_db: ControlPlaneDB, candidate: _Candidate):
        barrier.wait(timeout=5)
        try:
            return "ok", _append(one_db, activation, candidate, current)
        except AttestedConsensusExpectationCASConflict as exc:
            return "conflict", exc.reason

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(write, (db_a, db_b), candidates))
        assert sorted(kind for kind, _value in results) == ["conflict", "ok"]
        assert [value for kind, value in results if kind == "conflict"] == [
            "stale_current_expectation"
        ]
        assert db_a._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM attested_consensus_expectations"
        ).fetchone()[0] == 2
    finally:
        db_a.close()
        db_b.close()


def test_source_scope_canonical_and_nested_transaction_refusals_insert_nothing(
    db: ControlPlaneDB,
) -> None:
    identity = _identity("refusal")
    activation = _insert_activation_pointer(
        db,
        identity=identity,
        deployment_scope_digest=_digest("deployment:refusal"),
        suffix="refusal-current",
    )
    candidate = _candidate(
        activation,
        expectation_generation=0,
        previous_expectation_head_digest=(
            INITIAL_PREVIOUS_EXPECTATION_HEAD_DIGEST
        ),
        suffix="refusal",
    )

    foreign = _candidate(
        activation,
        expectation_generation=0,
        previous_expectation_head_digest=(
            INITIAL_PREVIOUS_EXPECTATION_HEAD_DIGEST
        ),
        suffix="foreign-source",
    )
    with pytest.raises(AttestedConsensusExpectationCASConflict) as binding:
        db.append_attested_consensus_expectation_cas(
            canonical_expectation=candidate.canonical,
            activation_admission_intent=foreign.intent,
            expected_attestation_log_closed_head_digest=foreign.closed_head,
            cell_identity=identity,
            expected_deployment_scope_digest=(
                activation.deployment_scope_digest
            ),
            expected_current_expectation_head_digest=None,
            expected_current_expectation_generation=None,
        )
    assert binding.value.reason == "admission_challenge_binding"

    with pytest.raises(ControlPlaneError, match="canonical form"):
        db.append_attested_consensus_expectation_cas(
            canonical_expectation=b" " + candidate.canonical,
            activation_admission_intent=candidate.intent,
            expected_attestation_log_closed_head_digest=candidate.closed_head,
            cell_identity=identity,
            expected_deployment_scope_digest=(
                activation.deployment_scope_digest
            ),
            expected_current_expectation_head_digest=None,
            expected_current_expectation_generation=None,
        )
    with db.transaction():
        with pytest.raises(ControlPlaneError, match="cannot nest"):
            _append(db, activation, candidate)
    assert db._conn.execute(  # noqa: SLF001
        "SELECT COUNT(*) FROM attested_consensus_expectations"
    ).fetchone()[0] == 0


def test_activation_advance_and_retirement_make_provider_fail_closed(
    db: ControlPlaneDB,
) -> None:
    identity = _identity("stale")
    deployment = _digest("deployment:stale")
    activation = _insert_activation_pointer(
        db,
        identity=identity,
        deployment_scope_digest=deployment,
        suffix="stale-current",
    )
    candidate = _candidate(
        activation,
        expectation_generation=0,
        previous_expectation_head_digest=(
            INITIAL_PREVIOUS_EXPECTATION_HEAD_DIGEST
        ),
        suffix="stale-pin",
    )
    record = _append(db, activation, candidate)
    provider = ControlPlaneAttestedConsensusExpectationProvider(
        control_plane=db,
        deployment_scope_digest=deployment,
        cell_identity=identity,
    )
    assert provider()

    advanced = _insert_activation_pointer(
        db,
        identity=identity,
        deployment_scope_digest=deployment,
        suffix="advanced",
        previous=activation,
    )
    with pytest.raises(
        AttestedConsensusExpectationProviderError
    ) as stale_provider:
        provider()
    assert stale_provider.value.reason == "activation_pointer_mismatch"

    successor = _candidate(
        activation,
        expectation_generation=1,
        previous_expectation_head_digest=record.expectation_head_digest,
        suffix="stale-successor",
    )
    with pytest.raises(AttestedConsensusExpectationCASConflict) as stale_append:
        _append(db, activation, successor, record)
    assert stale_append.value.reason == "stale_current_activation_snapshot"

    db.retire_activation_scope_cas(
        deployment_scope_digest=deployment,
        cell_identity=identity,
        expected_current_bundle_digest=advanced.bundle_digest,
        expected_current_activation_head_digest=(
            advanced.activation_head_digest
        ),
        expected_current_store_revision=advanced.store_revision,
        reason_digest=_digest("retired"),
    )
    with pytest.raises(
        AttestedConsensusExpectationProviderError
    ) as retired_provider:
        provider()
    assert retired_provider.value.reason == "expectation_retired"


def test_provider_rejects_projection_tamper_and_detects_expectation_drift(
    db: ControlPlaneDB,
) -> None:
    identity = _identity("provider-adversarial")
    deployment = _digest("deployment:provider-adversarial")
    activation = _insert_activation_pointer(
        db,
        identity=identity,
        deployment_scope_digest=deployment,
        suffix="provider-current",
    )
    first_candidate = _candidate(
        activation,
        expectation_generation=0,
        previous_expectation_head_digest=(
            INITIAL_PREVIOUS_EXPECTATION_HEAD_DIGEST
        ),
        suffix="provider-a",
    )
    first = _append(db, activation, first_candidate)
    second_candidate = _candidate(
        activation,
        expectation_generation=1,
        previous_expectation_head_digest=first.expectation_head_digest,
        suffix="provider-b",
    )
    second = _append(db, activation, second_candidate, first)

    tampered = asdict(second)
    tampered["expected_query_digest"] = _digest("tampered")

    class _Static:
        def get_current_attested_consensus_expectation(self, **_kwargs):
            return tampered

    with pytest.raises(
        AttestedConsensusExpectationProviderError
    ) as projection_error:
        ControlPlaneAttestedConsensusExpectationProvider(
            control_plane=_Static(),
            deployment_scope_digest=deployment,
            cell_identity=identity,
        )()
    assert projection_error.value.reason == "pin_expected_query_digest_mismatch"

    supplied = iter((asdict(first), asdict(second)))

    class _Changing:
        def get_current_attested_consensus_expectation(self, **_kwargs):
            return next(supplied)

    with pytest.raises(
        AttestedConsensusExpectationProviderError
    ) as drift_error:
        ControlPlaneAttestedConsensusExpectationProvider(
            control_plane=_Changing(),
            deployment_scope_digest=deployment,
            cell_identity=identity,
        )()
    assert drift_error.value.reason == "expectation_changed_during_resolution"

    activation_changed = asdict(second)
    activation_changed["current_activation_bundle_digest"] = _digest(
        "changed-during-resolution"
    )
    supplied_activation = iter((asdict(second), activation_changed))

    class _ActivationChanging:
        def get_current_attested_consensus_expectation(self, **_kwargs):
            return next(supplied_activation)

    with pytest.raises(
        AttestedConsensusExpectationProviderError
    ) as activation_drift:
        ControlPlaneAttestedConsensusExpectationProvider(
            control_plane=_ActivationChanging(),
            deployment_scope_digest=deployment,
            cell_identity=identity,
        )()
    assert (
        activation_drift.value.reason
        == "activation_pointer_changed_during_resolution"
    )


@pytest.mark.parametrize("hostile_result", (True, RuntimeError("secret")))
def test_provider_refuses_hostile_scalar_equality_without_payload_leak(
    db: ControlPlaneDB,
    hostile_result: object,
) -> None:
    identity = _identity("hostile-provider")
    deployment = _digest("deployment:hostile-provider")
    activation = _insert_activation_pointer(
        db,
        identity=identity,
        deployment_scope_digest=deployment,
        suffix="hostile-current",
    )
    candidate = _candidate(
        activation,
        expectation_generation=0,
        previous_expectation_head_digest=(
            INITIAL_PREVIOUS_EXPECTATION_HEAD_DIGEST
        ),
        suffix="hostile-pin",
    )
    record = _append(db, activation, candidate)

    class _Hostile:
        def __eq__(self, _other):
            if isinstance(hostile_result, BaseException):
                raise hostile_result
            return hostile_result

    poisoned = asdict(record)
    poisoned["created_at"] = _Hostile()

    class _Poisoned:
        def get_current_attested_consensus_expectation(self, **_kwargs):
            return poisoned

    with pytest.raises(
        AttestedConsensusExpectationProviderError
    ) as error:
        ControlPlaneAttestedConsensusExpectationProvider(
            control_plane=_Poisoned(),
            deployment_scope_digest=deployment,
            cell_identity=identity,
        )()
    assert error.value.reason == "expectation_invalid"
    assert "secret" not in str(error.value)


def test_schema_rejects_replace_update_delete_and_raw_replay(
    db: ControlPlaneDB,
) -> None:
    identity = _identity("schema")
    activation = _insert_activation_pointer(
        db,
        identity=identity,
        deployment_scope_digest=_digest("deployment:schema"),
        suffix="schema-current",
    )
    candidate = _candidate(
        activation,
        expectation_generation=0,
        previous_expectation_head_digest=(
            INITIAL_PREVIOUS_EXPECTATION_HEAD_DIGEST
        ),
        suffix="schema-a",
    )
    record = _append(db, activation, candidate)

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db._conn.execute(  # noqa: SLF001
            "UPDATE attested_consensus_expectations SET created_at = ? "
            "WHERE id = ?",
            ("later", record.id),
        )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db._conn.execute(  # noqa: SLF001
            "DELETE FROM attested_consensus_expectations WHERE id = ?",
            (record.id,),
        )

    row = db._conn.execute(  # noqa: SLF001
        "SELECT * FROM attested_consensus_expectations WHERE id = ?",
        (record.id,),
    ).fetchone()
    columns = tuple(row.keys())
    external = sqlite3.connect(db.db_path, isolation_level=None)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            external.execute(
                "INSERT OR REPLACE INTO attested_consensus_expectations("
                + ", ".join(columns)
                + ") VALUES ("
                + ", ".join("?" for _ in columns)
                + ")",
                tuple(row[column] for column in columns),
            )
    finally:
        external.close()
    assert db._conn.execute(  # noqa: SLF001
        "SELECT COUNT(*) FROM attested_consensus_expectations"
    ).fetchone()[0] == 1


def test_external_fk_off_writer_cannot_pin_an_orphan_activation_pointer(
    db: ControlPlaneDB,
) -> None:
    orphan_scope = _digest("orphan-scope")
    bundle = _digest("orphan-bundle")
    head = _digest("orphan-head")
    orphan = _Activation(
        identity=_identity("orphan"),
        deployment_scope_digest=_digest("orphan-deployment"),
        activation_scope_digest=orphan_scope,
        bundle_digest=bundle,
        activation_head_digest=head,
        store_revision=0,
    )
    candidate = _candidate(
        orphan,
        expectation_generation=0,
        previous_expectation_head_digest=(
            INITIAL_PREVIOUS_EXPECTATION_HEAD_DIGEST
        ),
        suffix="orphan",
    )
    external = sqlite3.connect(db.db_path, isolation_level=None)
    try:
        assert external.execute("PRAGMA foreign_keys").fetchone()[0] == 0
        external.execute(
            """
            INSERT INTO activation_snapshot_pointers(
                activation_scope_digest,
                bundle_digest,
                store_revision,
                previous_bundle_digest,
                activation_head_digest,
                previous_activation_head_digest,
                expression_context_digest,
                expected_profile_head_digest,
                expected_policy_head_digest,
                expected_resource_head_digest,
                expected_domain_head_digest,
                expected_environment_head_digest,
                charter_ceiling_digest,
                expressed_ceiling_digest,
                created_at
            ) VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                orphan_scope,
                bundle,
                ZERO_DIGEST,
                head,
                ZERO_DIGEST,
                _digest("orphan-context"),
                _digest("orphan-profile"),
                _digest("orphan-policy"),
                _digest("orphan-resource"),
                _digest("orphan-domain"),
                _digest("orphan-environment"),
                _digest("orphan-charter"),
                _digest("orphan-expressed"),
                CREATED_AT,
            ),
        )
        with pytest.raises(
            sqlite3.IntegrityError,
            match="activation scope missing",
        ):
            _raw_insert_candidate(external, candidate)
    finally:
        external.close()
    assert db._conn.execute(  # noqa: SLF001
        "SELECT COUNT(*) FROM attested_consensus_expectations"
    ).fetchone()[0] == 0


def test_explicit_negative_rowid_cannot_poison_automatic_successors(
    db: ControlPlaneDB,
) -> None:
    identity = _identity("rowid")
    activation = _insert_activation_pointer(
        db,
        identity=identity,
        deployment_scope_digest=_digest("deployment:rowid"),
        suffix="rowid-current",
    )
    candidate = _candidate(
        activation,
        expectation_generation=0,
        previous_expectation_head_digest=(
            INITIAL_PREVIOUS_EXPECTATION_HEAD_DIGEST
        ),
        suffix="rowid-a",
    )
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint"):
        _raw_insert_candidate(db._conn, candidate, explicit_id=-1)  # noqa: SLF001
    _raw_insert_candidate(db._conn, candidate)  # noqa: SLF001
    stored = db._conn.execute(  # noqa: SLF001
        "SELECT id, generation FROM attested_consensus_expectations"
    ).fetchall()
    assert [tuple(row) for row in stored] == [(1, 0)]


def test_explicit_max_rowid_cannot_poison_automatic_successors(
    db: ControlPlaneDB,
) -> None:
    identity = _identity("max-rowid")
    deployment = _digest("deployment:max-rowid")
    activation = _insert_activation_pointer(
        db,
        identity=identity,
        deployment_scope_digest=deployment,
        suffix="max-rowid-current",
    )
    first_candidate = _candidate(
        activation,
        expectation_generation=0,
        previous_expectation_head_digest=(
            INITIAL_PREVIOUS_EXPECTATION_HEAD_DIGEST
        ),
        suffix="max-rowid-a",
    )
    _raw_insert_candidate(
        db._conn,  # noqa: SLF001
        first_candidate,
        explicit_id=(1 << 63) - 1,
    )
    first = db.get_current_attested_consensus_expectation(
        deployment_scope_digest=deployment,
        cell_identity=identity,
    )
    assert first is not None and first.id == (1 << 63) - 1

    second_candidate = _candidate(
        activation,
        expectation_generation=1,
        previous_expectation_head_digest=first.expectation_head_digest,
        suffix="max-rowid-b",
    )
    second = _append(db, activation, second_candidate, first)
    assert second.generation == 1
    assert 0 < second.id < (1 << 63) - 1
    assert db._conn.execute(  # noqa: SLF001
        "SELECT COUNT(*) FROM attested_consensus_expectations"
    ).fetchone()[0] == 2
