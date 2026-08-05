"""Adversarial tests for scope-bound attestation-log MAGMA storage."""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from waggledance.core.magma import attestation_log_artifact_store as store_module
from waggledance.core.magma.attestation_log_artifact_store import (
    AttestationLogArtifactStore,
    AttestationLogArtifactStoreError,
)
from waggledance.core.orchestration.attestation_log import (
    INITIAL_PREVIOUS_LOG_HEAD_DIGEST,
    build_attestation_log_entry,
    build_attestation_log_snapshot,
)
from waggledance.core.orchestration.attestation_log_artifact import (
    build_attestation_log_artifact,
    canonicalize_attestation_log_artifact,
)


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("ascii")).hexdigest()


def _artifact_fixture(*, scope_label: str = "scope"):
    scope = _digest(scope_label)
    entry = build_attestation_log_entry(
        activation_scope_digest=scope,
        admission_challenge_digest=_digest("challenge"),
        evidence_digest=_digest("evidence"),
        ballot_digest=_digest("ballot"),
        attestation_digest=_digest("attestation"),
        reviewer_lineage_digest=_digest("lineage"),
    )
    snapshot = build_attestation_log_snapshot(
        generation=0,
        previous_log_head_digest=INITIAL_PREVIOUS_LOG_HEAD_DIGEST,
        entries=[entry],
    )
    artifact = build_attestation_log_artifact(
        activation_scope_digest=scope,
        snapshot=snapshot,
        expected_log_head_digest=snapshot.log_head_digest,
    )
    canonical = canonicalize_attestation_log_artifact(
        artifact,
        expected_activation_scope_digest=scope,
        expected_log_head_digest=snapshot.log_head_digest,
    )
    return scope, snapshot.log_head_digest, artifact, canonical


def _append(store: AttestationLogArtifactStore, fixture):
    scope, head, _artifact, canonical = fixture
    return store.append(
        canonical,
        expected_activation_scope_digest=scope,
        expected_log_head_digest=head,
    )


def test_round_trip_uses_digest_shards_and_returns_immutable_bytes(
    tmp_path: Path,
) -> None:
    fixture = _artifact_fixture()
    scope, head, artifact, canonical = fixture
    store = AttestationLogArtifactStore(tmp_path / "attestation-artifacts")
    record = _append(store, fixture)
    hexadecimal = artifact["artifact_digest"].removeprefix("sha256:")

    assert record.artifact_digest == artifact["artifact_digest"]
    assert record.log_head_digest == head
    assert record.activation_scope_digest == scope
    assert record.path == (
        store.root
        / hexadecimal[:2]
        / hexadecimal[2:4]
        / f"{hexadecimal}.json"
    )
    assert record.size == len(canonical)
    loaded = store.read(
        record.artifact_digest,
        expected_activation_scope_digest=scope,
        expected_log_head_digest=head,
    )
    assert type(loaded) is bytes
    assert loaded == canonical
    assert store.read(
        _digest("absent"),
        expected_activation_scope_digest=scope,
        expected_log_head_digest=head,
    ) is None


def test_exact_reappend_is_idempotent_and_corruption_fails_loud(
    tmp_path: Path,
) -> None:
    fixture = _artifact_fixture()
    scope, head, _artifact, canonical = fixture
    store = AttestationLogArtifactStore(tmp_path / "artifacts")
    first = _append(store, fixture)
    assert _append(store, fixture) == first
    assert len(list(store.root.rglob("*.json"))) == 1

    first.path.write_bytes(b'{"corrupt":true}')
    with pytest.raises(AttestationLogArtifactStoreError) as read_error:
        store.read(
            first.artifact_digest,
            expected_activation_scope_digest=scope,
            expected_log_head_digest=head,
        )
    assert read_error.value.reason.startswith("stored_artifact")
    with pytest.raises(AttestationLogArtifactStoreError) as append_error:
        store.append(
            canonical,
            expected_activation_scope_digest=scope,
            expected_log_head_digest=head,
        )
    assert append_error.value.reason.startswith("stored_artifact")


def test_append_refuses_noncanonical_wrong_bindings_type_and_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _artifact_fixture()
    scope, head, _artifact, canonical = fixture
    store = AttestationLogArtifactStore(tmp_path / "artifacts")
    noncanonical = json.dumps(json.loads(canonical), indent=2).encode("utf-8")
    with pytest.raises(AttestationLogArtifactStoreError) as error:
        store.append(
            noncanonical,
            expected_activation_scope_digest=scope,
            expected_log_head_digest=head,
        )
    assert error.value.reason == "canonical_artifact_noncanonical"

    with pytest.raises(AttestationLogArtifactStoreError) as error:
        store.append(
            canonical,
            expected_activation_scope_digest=_digest("stale-scope"),
            expected_log_head_digest=head,
        )
    assert error.value.reason == "canonical_artifact:activation_scope_binding"
    with pytest.raises(AttestationLogArtifactStoreError) as error:
        store.append(
            canonical,
            expected_activation_scope_digest=scope,
            expected_log_head_digest=_digest("stale-head"),
        )
    assert error.value.reason == "canonical_artifact:log_head_binding"
    with pytest.raises(AttestationLogArtifactStoreError) as error:
        store.append(
            bytearray(canonical),  # type: ignore[arg-type]
            expected_activation_scope_digest=scope,
            expected_log_head_digest=head,
        )
    assert error.value.reason == "canonical_artifact_type"

    monkeypatch.setattr(
        store_module,
        "MAX_ATTESTATION_LOG_ARTIFACT_BYTES",
        len(canonical) - 1,
    )
    with pytest.raises(AttestationLogArtifactStoreError) as error:
        store.append(
            canonical,
            expected_activation_scope_digest=scope,
            expected_log_head_digest=head,
        )
    assert error.value.reason == "canonical_artifact_size"


@pytest.mark.parametrize(
    "hostile_digest",
    [
        "../../outside",
        "sha256:" + "A" * 64,
        "sha256:" + "0" * 63 + "/",
        "0" * 64,
        b"sha256:" + b"0" * 64,
    ],
)
def test_read_refuses_traversal_and_noncanonical_names(
    tmp_path: Path, hostile_digest: object
) -> None:
    scope, head, _artifact, _canonical = _artifact_fixture()
    store = AttestationLogArtifactStore(tmp_path / "artifacts")
    with pytest.raises(AttestationLogArtifactStoreError) as error:
        store.read(
            hostile_digest,  # type: ignore[arg-type]
            expected_activation_scope_digest=scope,
            expected_log_head_digest=head,
        )
    assert error.value.reason == "artifact_digest"
    assert list(tmp_path.rglob("outside")) == []


def test_root_and_shard_links_are_refused_when_supported(
    tmp_path: Path,
) -> None:
    real_root = tmp_path / "real-root"
    real_root.mkdir()
    root_link = tmp_path / "root-link"
    try:
        root_link.symlink_to(real_root, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory links are unavailable")

    with pytest.raises(AttestationLogArtifactStoreError) as root_error:
        AttestationLogArtifactStore(root_link)
    assert root_error.value.reason == "root_symlink"

    fixture = _artifact_fixture()
    _scope, _head, artifact, _canonical = fixture
    store = AttestationLogArtifactStore(tmp_path / "artifacts")
    hexadecimal = artifact["artifact_digest"].removeprefix("sha256:")
    external = tmp_path / "external-shard"
    external.mkdir()
    (store.root / hexadecimal[:2]).symlink_to(
        external, target_is_directory=True
    )
    with pytest.raises(AttestationLogArtifactStoreError) as shard_error:
        _append(store, fixture)
    assert shard_error.value.reason == "first_shard_symlink"


def test_concurrent_identical_append_publishes_once_and_cleans_temporaries(
    tmp_path: Path,
) -> None:
    fixture = _artifact_fixture()
    _scope, _head, artifact, canonical = fixture
    root = tmp_path / "artifacts"

    def publish(_index: int):
        return _append(AttestationLogArtifactStore(root), fixture)

    with ThreadPoolExecutor(max_workers=8) as pool:
        records = list(pool.map(publish, range(24)))

    assert {record.artifact_digest for record in records} == {
        artifact["artifact_digest"]
    }
    assert {record.path for record in records} == {records[0].path}
    assert records[0].path.read_bytes() == canonical
    assert len(list(root.rglob("*.json"))) == 1
    assert list(root.rglob("*.pending")) == []


def test_read_refuses_file_growth_after_payload_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _artifact_fixture()
    scope, head, _artifact, _canonical = fixture
    store = AttestationLogArtifactStore(tmp_path / "artifacts")
    record = _append(store, fixture)
    original_read = store_module.os.read
    mutated = False

    def append_after_read(descriptor: int, count: int) -> bytes:
        nonlocal mutated
        chunk = original_read(descriptor, count)
        if chunk and not mutated:
            mutated = True
            with record.path.open("ab") as artifact_file:
                artifact_file.write(b"x")
                artifact_file.flush()
        return chunk

    monkeypatch.setattr(store_module.os, "read", append_after_read)
    with pytest.raises(AttestationLogArtifactStoreError) as error:
        store.read(
            record.artifact_digest,
            expected_activation_scope_digest=scope,
            expected_log_head_digest=head,
        )
    assert error.value.reason == "target_raced"


def test_empty_root_and_existing_file_root_are_refused(tmp_path: Path) -> None:
    with pytest.raises(AttestationLogArtifactStoreError) as empty_error:
        AttestationLogArtifactStore("")
    assert empty_error.value.reason == "root"

    file_root = tmp_path / "not-a-directory"
    file_root.write_text("fixture", encoding="utf-8")
    with pytest.raises(AttestationLogArtifactStoreError) as file_error:
        AttestationLogArtifactStore(file_root)
    assert file_error.value.reason == "root_not_directory"

    with pytest.raises(AttestationLogArtifactStoreError) as nul_error:
        AttestationLogArtifactStore("bad\0root")
    assert nul_error.value.reason == "root_create"


def test_post_publish_cleanup_failure_is_typed_and_artifact_is_readable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _artifact_fixture()
    scope, head, artifact, canonical = fixture
    store = AttestationLogArtifactStore(tmp_path / "artifacts")

    def refuse_cleanup(_path):
        raise PermissionError("SECRET_TEMP_PATH")

    monkeypatch.setattr(store_module.os, "unlink", refuse_cleanup)
    with pytest.raises(AttestationLogArtifactStoreError) as error:
        _append(store, fixture)
    assert error.value.reason == "post_publish_cleanup"
    assert "SECRET_TEMP_PATH" not in str(error.value)
    assert len(list(store.root.rglob("*.json"))) == 1
    assert len(list(store.root.rglob("*.pending"))) == 1
    assert store.read(
        artifact["artifact_digest"],
        expected_activation_scope_digest=scope,
        expected_log_head_digest=head,
    ) == canonical
