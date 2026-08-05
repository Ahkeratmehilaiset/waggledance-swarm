# SPDX-License-Identifier: BUSL-1.1
"""Adversarial tests for the local activation snapshot artifact store."""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
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
    build_activation_snapshot_bundle,
    canonicalize_activation_snapshot_publication,
)
from waggledance.core.cell_identity import build_cell_identity
from waggledance.core.magma import activation_snapshot_artifact_store as store_module
from waggledance.core.magma.activation_snapshot_artifact_store import (
    ActivationSnapshotArtifactStore,
    ActivationSnapshotArtifactStoreError,
)


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("ascii")).hexdigest()


def _publication_fixture():
    identity = build_cell_identity(
        pubkey_digest=_digest("pubkey"),
        genesis_material_digest=_digest("genesis"),
        created_at_utc="2026-08-05T08:00:00Z",
    )
    deployment = _digest("deployment")
    variant_ceiling = build_authority_ceiling(
        max_risk_class="local_artifact",
        authority_scope_digests=[_digest("scope:a"), _digest("scope:b")],
    )
    charter = build_authority_ceiling(
        max_risk_class="internal_memory",
        authority_scope_digests=[_digest("scope:a"), _digest("scope:c")],
    )
    expressed = build_authority_ceiling(
        max_risk_class="internal_memory",
        authority_scope_digests=[_digest("scope:a")],
    )
    variant = build_capability_variant(
        family_id="detect.fixture",
        risk_class="internal_memory",
        artifact_digest=_digest("artifact"),
        input_schema_digest=_digest("input"),
        output_schema_digest=_digest("output"),
        compatibility_digest=_digest("compatibility"),
        authority_ceiling_digest=variant_ceiling.ceiling_digest,
    )
    context = build_expression_context(
        profile_head_digest=_digest("profile"),
        policy_head_digest=_digest("policy"),
        resource_head_digest=_digest("resource"),
        domain_head_digest=_digest("domain"),
        environment_head_digest=_digest("environment"),
        charter_ceiling_digest=charter.ceiling_digest,
        expressed_ceiling_digest=expressed.ceiling_digest,
    )
    head = build_activation_head(
        generation=0,
        previous_head_digest=INITIAL_PREVIOUS_HEAD_DIGEST,
        expression_context_digest=context.context_digest,
        active_variant_digests=[variant.variant_digest],
        shadow_variant_digests=[],
    )
    bundle = build_activation_snapshot_bundle(
        deployment_scope_digest=deployment,
        cell_identity=identity,
        store_revision=0,
        previous_bundle_digest=INITIAL_PREVIOUS_BUNDLE_DIGEST,
        head=head.to_mapping(),
        expected_activation_head_digest=head.head_digest,
        context=context.to_mapping(),
        variants=[variant.to_mapping()],
        variant_ceilings=[variant_ceiling.to_mapping()],
        charter_ceiling=charter.to_mapping(),
        expressed_ceiling=expressed.to_mapping(),
        expected_profile_head_digest=context.profile_head_digest,
        expected_policy_head_digest=context.policy_head_digest,
        expected_resource_head_digest=context.resource_head_digest,
        expected_domain_head_digest=context.domain_head_digest,
        expected_environment_head_digest=context.environment_head_digest,
    )
    external = {
        "cell_identity": identity,
        "expected_deployment_scope_digest": deployment,
        "expected_profile_head_digest": context.profile_head_digest,
        "expected_policy_head_digest": context.policy_head_digest,
        "expected_resource_head_digest": context.resource_head_digest,
        "expected_domain_head_digest": context.domain_head_digest,
        "expected_environment_head_digest": context.environment_head_digest,
        "expected_charter_ceiling_digest": charter.ceiling_digest,
        "expected_expressed_ceiling_digest": expressed.ceiling_digest,
    }
    canonical = canonicalize_activation_snapshot_publication(bundle, **external)
    return canonical, bundle, external


def test_round_trip_uses_digest_only_sharded_path_and_immutable_bytes(
    tmp_path: Path,
) -> None:
    canonical, bundle, external = _publication_fixture()
    store = ActivationSnapshotArtifactStore(tmp_path / "artifacts")
    record = store.append(canonical, **external)
    hexadecimal = bundle["bundle_digest"].removeprefix("sha256:")

    assert record.bundle_digest == bundle["bundle_digest"]
    assert record.path == (
        store.root
        / hexadecimal[:2]
        / hexadecimal[2:4]
        / f"{hexadecimal}.json"
    )
    assert record.size == len(canonical)
    loaded = store.read(record.bundle_digest)
    assert type(loaded) is bytes
    assert loaded == canonical
    assert store.read(_digest("absent")) is None


def test_exact_reappend_is_idempotent_and_different_existing_content_fails(
    tmp_path: Path,
) -> None:
    canonical, _bundle, external = _publication_fixture()
    store = ActivationSnapshotArtifactStore(tmp_path / "artifacts")
    first = store.append(canonical, **external)
    second = store.append(canonical, **external)
    assert second == first
    assert len(list(store.root.rglob("*.json"))) == 1

    first.path.write_bytes(b"{\"corrupt\":true}")
    with pytest.raises(ActivationSnapshotArtifactStoreError) as read_error:
        store.read(first.bundle_digest)
    assert read_error.value.reason.startswith("stored_")
    with pytest.raises(ActivationSnapshotArtifactStoreError) as append_error:
        store.append(canonical, **external)
    assert append_error.value.reason.startswith("stored_")


def test_append_rejects_noncanonical_bytes_wrong_external_heads_and_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    canonical, _bundle, external = _publication_fixture()
    store = ActivationSnapshotArtifactStore(tmp_path / "artifacts")
    noncanonical = json.dumps(json.loads(canonical), indent=2).encode("utf-8")
    with pytest.raises(ActivationSnapshotArtifactStoreError) as error:
        store.append(noncanonical, **external)
    assert error.value.reason == "canonical_bundle_noncanonical"

    stale = {**external, "expected_policy_head_digest": _digest("stale-policy")}
    with pytest.raises(ActivationSnapshotArtifactStoreError) as error:
        store.append(canonical, **stale)
    assert error.value.reason == "publication:current_policy_head_digest_binding"

    monkeypatch.setattr(
        store_module,
        "MAX_ACTIVATION_SNAPSHOT_ARTIFACT_BYTES",
        len(canonical) - 1,
    )
    with pytest.raises(ActivationSnapshotArtifactStoreError) as error:
        store.append(canonical, **external)
    assert error.value.reason == "canonical_bundle_size"


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
def test_read_refuses_traversal_and_noncanonical_digest_names(
    tmp_path: Path, hostile_digest: object
) -> None:
    store = ActivationSnapshotArtifactStore(tmp_path / "artifacts")
    with pytest.raises(ActivationSnapshotArtifactStoreError) as error:
        store.read(hostile_digest)  # type: ignore[arg-type]
    assert error.value.reason == "bundle_digest"
    assert list(tmp_path.rglob("outside")) == []


def test_root_and_shard_symlinks_are_refused_when_supported(
    tmp_path: Path,
) -> None:
    real_root = tmp_path / "real-root"
    real_root.mkdir()
    root_link = tmp_path / "root-link"
    try:
        root_link.symlink_to(real_root, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks are unavailable")

    with pytest.raises(ActivationSnapshotArtifactStoreError) as root_error:
        ActivationSnapshotArtifactStore(root_link)
    assert root_error.value.reason == "root_symlink"

    canonical, bundle, external = _publication_fixture()
    store = ActivationSnapshotArtifactStore(tmp_path / "artifacts")
    hexadecimal = bundle["bundle_digest"].removeprefix("sha256:")
    shard_target = tmp_path / "external-shard"
    shard_target.mkdir()
    (store.root / hexadecimal[:2]).symlink_to(
        shard_target, target_is_directory=True
    )
    with pytest.raises(ActivationSnapshotArtifactStoreError) as shard_error:
        store.append(canonical, **external)
    assert shard_error.value.reason == "first_shard_symlink"


def test_concurrent_identical_append_publishes_once_and_cleans_temporaries(
    tmp_path: Path,
) -> None:
    canonical, bundle, external = _publication_fixture()
    root = tmp_path / "artifacts"

    def publish(_index: int):
        # Separate instances exercise the filesystem publication boundary,
        # not an in-process lock shared by one object.
        return ActivationSnapshotArtifactStore(root).append(
            canonical, **external
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        records = list(pool.map(publish, range(24)))

    assert {record.bundle_digest for record in records} == {
        bundle["bundle_digest"]
    }
    assert {record.path for record in records} == {records[0].path}
    assert records[0].path.read_bytes() == canonical
    assert len(list(root.rglob("*.json"))) == 1
    assert list(root.rglob("*.pending")) == []


def test_read_refuses_file_growth_after_payload_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    canonical, _bundle, external = _publication_fixture()
    store = ActivationSnapshotArtifactStore(tmp_path / "artifacts")
    record = store.append(canonical, **external)
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
    with pytest.raises(ActivationSnapshotArtifactStoreError) as error:
        store.read(record.bundle_digest)
    assert error.value.reason == "target_raced"
