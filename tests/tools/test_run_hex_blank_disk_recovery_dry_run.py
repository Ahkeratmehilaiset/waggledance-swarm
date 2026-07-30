# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path, PurePosixPath

import pytest

from tools import run_hex_blank_disk_recovery_dry_run as tool
from waggledance.core.hex_topology.recovery_contract import (
    ContractValidationError,
    build_hex_cell_genome,
    build_hive_recovery_manifest,
    compute_hive_state_root,
    compute_memory_root,
    sha256_file,
)
from waggledance.core.magma.canonical import sha256_digest


COMMIT = "1" * 40
ZERO_DIGEST = "sha256:" + ("0" * 64)


def _artifact(
    relative_path: str,
    *,
    digest: str = ZERO_DIGEST,
    byte_size: int = 0,
    classification: str = "mutable_state",
    artifact_id: str = "artifact.one",
) -> dict:
    return {
        "artifact_id": artifact_id,
        "relative_path": relative_path,
        "content_digest": digest,
        "byte_size": byte_size,
        "classification": classification,
        "required": True,
        "restore_strategy": "verified_copy",
    }


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _genome(
    *,
    cell_id: str,
    neighbors: list[str],
    repair_peers: list[str],
    include_unbundled_magma_input: bool = False,
) -> dict:
    """Build one real core-contract genome for the integration fixture."""
    durable_inputs = [
        {
            "input_id": f"input.{cell_id}",
            "kind": "repo_artifact",
            "source_ref": f"configs/axioms/{cell_id}.yaml",
            "source_digest": ZERO_DIGEST,
            "rebuild_strategy": "git_checkout",
            "replay_checkpoint": None,
            "required": True,
        }
    ]
    if include_unbundled_magma_input:
        durable_inputs.append(
            {
                "input_id": f"magma.{cell_id}",
                "kind": "magma_ledger",
                "source_ref": f"state/{cell_id}.jsonl",
                "source_digest": "sha256:" + ("3" * 64),
                "rebuild_strategy": "replay",
                "replay_checkpoint": 42,
                "required": True,
            }
        )
    return build_hex_cell_genome(
        cell_id=cell_id,
        mesh_id="solver.hex",
        mesh_kind="logical_solver_overlay",
        topology_epoch=1,
        axial_coord=None,
        parent_cell_id=None,
        child_cell_ids=[],
        neighbor_cell_ids=neighbors,
        repair_peer_cell_ids=repair_peers,
        capabilities=[
            {
                "kind": "solver",
                "capability_id": f"solver.{cell_id}",
                "source_ref": f"configs/axioms/{cell_id}.yaml",
                "source_digest": ZERO_DIGEST,
                "required": True,
            }
        ],
        durable_inputs=durable_inputs,
    )


def _valid_bundle(
    tmp_path: Path,
    *,
    omit_blob_for: str | None = None,
    add_extra_blob: bool = False,
    mismatched_genome_ref: bool = False,
    include_unbundled_magma_input: bool = False,
) -> dict[str, object]:
    """Create a three-cell ring using only core contract builders."""
    bundle = tmp_path / "bundle"
    blob_root = bundle / "blobs" / "sha256"
    blob_root.mkdir(parents=True)

    cell_ids = ("general", "math", "system")
    genomes = {
        cell_id: _genome(
            cell_id=cell_id,
            neighbors=[other for other in cell_ids if other != cell_id],
            repair_peers=[other for other in cell_ids if other != cell_id],
            include_unbundled_magma_input=(
                include_unbundled_magma_input and cell_id == "general"
            ),
        )
        for cell_id in cell_ids
    }

    artifacts: list[dict] = []
    cells: list[dict] = []
    for index, (cell_id, genome) in enumerate(genomes.items(), 1):
        relative_path = f"genomes/{cell_id}.json"
        blob_genome = genome
        if mismatched_genome_ref and cell_id == "general":
            blob_genome = _genome(
                cell_id="rogue",
                neighbors=["math", "system"],
                repair_peers=["math", "system"],
            )
        raw = _canonical_json_bytes(blob_genome)
        digest = "sha256:" + hashlib.sha256(raw).hexdigest()
        if cell_id != omit_blob_for:
            (blob_root / digest.removeprefix("sha256:")).write_bytes(raw)
        artifacts.append(
            _artifact(
                relative_path,
                digest=digest,
                byte_size=len(raw),
                classification="genome",
                artifact_id=f"genome.{index}",
            )
        )
        cells.append(
            {
                "cell_id": cell_id,
                "genome_ref": relative_path,
                "genome_digest": genome["genome_digest"],
                "expected_cell_state_root": genome["expected_cell_state_root"],
            }
        )

    state_raw = b'{"shadow":"state"}\n'
    state_digest = "sha256:" + hashlib.sha256(state_raw).hexdigest()
    if omit_blob_for != "state":
        (blob_root / state_digest.removeprefix("sha256:")).write_bytes(state_raw)
    artifacts.append(
        _artifact(
            "state/shadow.json",
            digest=state_digest,
            byte_size=len(state_raw),
            artifact_id="state.shadow",
        )
    )

    if add_extra_blob:
        (blob_root / ("f" * 64)).write_bytes(b"extra")

    source_repository = {
        "repository_ref": "github:example/waggledance",
        "commit_sha": COMMIT,
        "source_of_truth": "git_primary",
        "require_clean_clone": True,
        "backup_as_primary": False,
    }
    topologies = [
        {
            "mesh_id": "solver.hex",
            "mesh_kind": "logical_solver_overlay",
            "topology_epoch": 1,
            "cells": cells,
            "routing_invariants": {
                "connected": True,
                "bidirectional_neighbors": True,
                "survive_single_cell_loss": True,
                "max_route_hops": 1,
            },
        }
    ]
    builder_artifacts = list(artifacts)
    if include_unbundled_magma_input:
        builder_artifacts.append(
            {
                "artifact_id": "temporary.bound.general",
                "relative_path": "state/general.jsonl",
                "content_digest": "sha256:" + ("3" * 64),
                "byte_size": 1,
                "classification": "mutable_state",
                "required": True,
                "restore_strategy": "rebuild",
            }
        )
    manifest = build_hive_recovery_manifest(
        manifest_id="manifest.shadow.test",
        created_at_utc="2026-07-29T18:00:00Z",
        source_repository=source_repository,
        topologies=topologies,
        artifacts=builder_artifacts,
        replicas=[],
        external_replication_verified=False,
        blank_disk_dry_run_verified=False,
        previous_manifest_digest=None,
        genomes_by_ref={
            f"genomes/{cell_id}.json": genome
            for cell_id, genome in genomes.items()
        },
    )
    if include_unbundled_magma_input:
        manifest["artifacts"] = [
            artifact
            for artifact in manifest["artifacts"]
            if artifact["artifact_id"] != "temporary.bound.general"
        ]
        manifest["memory_root"] = compute_memory_root(manifest["artifacts"])
        manifest["hive_state_root"] = compute_hive_state_root(
            manifest["source_repository"],
            manifest["genome_root"],
            manifest["memory_root"],
        )
        manifest["manifest_digest"] = sha256_digest(
            {
                key: value
                for key, value in manifest.items()
                if key != "manifest_digest"
            }
        )
    manifest_path = bundle / "hive_recovery_manifest.v1.json"
    manifest_path.write_bytes(_canonical_json_bytes(manifest))
    return {
        "bundle": bundle,
        "manifest_path": manifest_path,
        "manifest_file_digest": sha256_file(manifest_path),
        "manifest": manifest,
        "artifacts": artifacts,
        "destination": tmp_path / "shadow-output",
    }


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "../escape.json",
        "/absolute.json",
        "C:/drive.json",
        r"nested\windows.json",
        "safe/name:ads",
    ],
)
def test_artifact_paths_fail_closed(unsafe_path: str, tmp_path: Path) -> None:
    manifest = {"artifacts": [_artifact(unsafe_path)]}
    with pytest.raises((ContractValidationError, tool.ShadowRecoveryError)):
        tool._artifact_plans(manifest, tmp_path)


def test_materialization_size_limit_fails_before_blob_access(tmp_path: Path) -> None:
    artifact = _artifact(
        "state/oversized.bin",
        byte_size=tool._MAX_SINGLE_ARTIFACT_BYTES + 1,
    )
    with pytest.raises(tool.ShadowRecoveryError, match="artifact_too_large"):
        tool._artifact_plans({"artifacts": [artifact]}, tmp_path)


@pytest.mark.parametrize(
    "reserved",
    [
        tool.REPORT_FILENAME,
        tool.COMPLETION_FILENAME,
        f"{tool.REPORT_FILENAME}/child",
        f"{tool.COMPLETION_FILENAME}/child",
    ],
)
def test_generated_report_names_are_reserved(
    reserved: str,
    tmp_path: Path,
) -> None:
    with pytest.raises(tool.ShadowRecoveryError, match="artifact_collides_with_report"):
        tool._artifact_plans({"artifacts": [_artifact(reserved)]}, tmp_path)


@pytest.mark.parametrize("name", ["dest:ads", "CON", "COM1.txt", "trailing."])
def test_unsafe_destination_leaf_is_rejected(name: str, tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    with pytest.raises(tool.ShadowRecoveryError, match="unsafe_destination_name"):
        tool._resolve_destination(str(tmp_path / name), bundle.resolve())


def test_unc_bundle_is_rejected_before_filesystem_access() -> None:
    with pytest.raises(
        tool.ShadowRecoveryError,
        match="bundle_must_be_local_filesystem",
    ):
        tool._resolve_bundle_root(r"\\server\share\bundle")


def test_nonempty_destination_is_rejected(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    destination = tmp_path / "destination"
    destination.mkdir()
    (destination / "existing.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(tool.ShadowRecoveryError, match="destination_not_empty"):
        tool._resolve_destination(str(destination), bundle.resolve())
    assert (destination / "existing.txt").read_text(encoding="utf-8") == "keep"


def test_existing_empty_destination_fails_closed(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    destination = tmp_path / "destination"
    destination.mkdir()

    with pytest.raises(
        tool.ShadowRecoveryError,
        match="destination_must_not_exist_for_atomic_promotion",
    ):
        tool._resolve_destination(str(destination), bundle.resolve())
    assert list(destination.iterdir()) == []


@pytest.mark.parametrize("relative", [".", "child", ".."])
def test_destination_cannot_overlap_source(
    relative: str,
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    destination = (bundle / relative).resolve(strict=False)
    with pytest.raises(tool.ShadowRecoveryError, match="unsafe_destination_scope"):
        tool._resolve_destination(str(destination), bundle.resolve())


def test_symlinked_blob_is_rejected(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    blob_root = bundle / "blobs" / "sha256"
    blob_root.mkdir(parents=True)
    manifest_path = bundle / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    raw = b"genome"
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    outside = tmp_path / "outside"
    outside.write_bytes(raw)
    link = blob_root / digest.removeprefix("sha256:")
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {type(exc).__name__}")
    plan = tool.ArtifactPlan(
        artifact_id="genome.one",
        relative_path=PurePosixPath("genomes/one.json"),
        content_digest=digest,
        byte_size=len(raw),
        classification="genome",
        blob_path=link,
    )
    with pytest.raises(tool.ShadowRecoveryError, match="path_reparse_not_allowed"):
        tool._preflight_blob_directory(bundle, manifest_path, (plan,))


def test_detected_reparse_blob_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = tmp_path / "bundle"
    manifest_path = bundle / "manifest.json"
    raw = b"genome"
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    blob = bundle / "blobs" / "sha256" / digest.removeprefix("sha256:")
    blob.parent.mkdir(parents=True)
    blob.write_bytes(raw)
    manifest_path.write_text("{}", encoding="utf-8")
    plan = tool.ArtifactPlan(
        artifact_id="genome.one",
        relative_path=PurePosixPath("genomes/one.json"),
        content_digest=digest,
        byte_size=len(raw),
        classification="genome",
        blob_path=blob,
    )
    real_check = tool._lstat_is_reparse
    monkeypatch.setattr(
        tool,
        "_lstat_is_reparse",
        lambda path: Path(path) == blob or real_check(Path(path)),
    )
    with pytest.raises(tool.ShadowRecoveryError, match="path_reparse_not_allowed"):
        tool._preflight_blob_directory(bundle, manifest_path, (plan,))


def test_authority_claims_are_always_false() -> None:
    report = tool._base_report()
    assert report["restore_state"] == "shadow_only"
    assert report["restore_applied"] is False
    assert report["shadow_rebuild_completed"] is False
    assert report["shadow_tree_materialized"] is False
    assert report["artifact_materialization_completed"] is False
    assert report["promotion_completed"] is False
    assert report["source_commit_anchor_matched"] is False
    assert report["exact_commit_checkout_verified"] is False
    assert report["runtime_started"] is False
    assert report["transport_enabled"] is False
    assert report["production_ready_claim"] is False
    assert report["blank_disk_claim_safe"] is False


def _plan(case: dict[str, object], **overrides: str) -> tool.RecoveryPlan:
    arguments = {
        "bundle_root_arg": str(case["bundle"]),
        "manifest_arg": str(case["manifest_path"]),
        "destination_arg": str(case["destination"]),
        "expected_commit": COMMIT,
        "expected_manifest_digest": str(case["manifest_file_digest"]),
    }
    arguments.update(overrides)
    return tool.build_recovery_plan(**arguments)


def test_valid_shadow_rebuild_and_cli_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = _valid_bundle(tmp_path)
    argv = [
        "--bundle-root",
        str(case["bundle"]),
        "--manifest",
        str(case["manifest_path"]),
        "--destination",
        str(case["destination"]),
        "--expected-commit",
        COMMIT,
        "--expected-manifest-digest",
        str(case["manifest_file_digest"]),
        "--json",
    ]
    assert tool.main(argv) == 0
    stdout_report = json.loads(capsys.readouterr().out)
    destination = Path(case["destination"])
    disk_report = json.loads(
        (destination / tool.COMPLETION_FILENAME).read_text(encoding="utf-8")
    )
    staging_report = json.loads(
        (destination / tool.REPORT_FILENAME).read_text(encoding="utf-8")
    )
    assert stdout_report == disk_report
    assert staging_report["ok"] is False
    assert staging_report["promotion_completed"] is False
    assert disk_report["ok"] is True
    assert disk_report["manifest_file_digest"] == case["manifest_file_digest"]
    assert disk_report["manifest_digest"] == case["manifest"]["manifest_digest"]
    assert disk_report["source_commit"] == COMMIT
    assert disk_report["artifact_count"] == len(case["artifacts"])
    assert disk_report["genome_count"] == 3
    assert disk_report["shadow_rebuild_completed"] is False
    assert disk_report["shadow_tree_materialized"] is True
    assert disk_report["artifact_materialization_completed"] is True
    assert disk_report["promotion_completed"] is True
    assert disk_report["source_commit_anchor_matched"] is True
    assert disk_report["exact_commit_checkout_verified"] is False
    assert disk_report["restore_state"] == "shadow_only"
    assert disk_report["restore_applied"] is False
    assert disk_report["runtime_started"] is False
    assert disk_report["transport_enabled"] is False
    assert disk_report["production_ready_claim"] is False
    assert disk_report["blank_disk_claim_safe"] is False

    for artifact in case["artifacts"]:
        rebuilt = destination.joinpath(*PurePosixPath(artifact["relative_path"]).parts)
        assert rebuilt.stat().st_size == artifact["byte_size"]
        assert sha256_file(rebuilt) == artifact["content_digest"]
    assert not list(tmp_path.glob(".shadow-output.shadow-stage-*"))


def test_valid_plan_and_direct_materialization(tmp_path: Path) -> None:
    case = _valid_bundle(tmp_path)
    plan = _plan(case)
    assert plan.manifest_file_digest == case["manifest_file_digest"]
    assert len(plan.artifacts) == len(case["artifacts"])
    assert plan.genome_count == 3
    report = tool.materialize_shadow(plan)
    assert report["ok"] is True
    assert report["shadow_rebuild_completed"] is False
    assert report["shadow_tree_materialized"] is True
    assert report["promotion_completed"] is True
    assert Path(case["destination"]).is_dir()


def test_materialize_rebuilds_plan_and_ignores_forged_traversal(
    tmp_path: Path,
) -> None:
    case = _valid_bundle(tmp_path)
    plan = _plan(case)
    forged_artifact = replace(
        plan.artifacts[0],
        relative_path=PurePosixPath("../escape.txt"),
    )
    forged_plan = replace(
        plan,
        artifacts=(forged_artifact, *plan.artifacts[1:]),
    )
    report = tool.materialize_shadow(forged_plan)
    assert report["ok"] is True
    assert not (tmp_path / "escape.txt").exists()


def test_injected_staging_entry_blocks_promotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _valid_bundle(tmp_path)
    plan = _plan(case)
    real_write = tool._exclusive_write_json

    def inject(path: Path, payload: dict) -> None:
        real_write(path, payload)
        if path.name == tool.REPORT_FILENAME:
            (path.parent / "unexpected-injected.txt").write_text(
                "unexpected",
                encoding="utf-8",
            )

    monkeypatch.setattr(tool, "_exclusive_write_json", inject)
    with pytest.raises(tool.ShadowRecoveryError, match="staging_inventory_mismatch"):
        tool.materialize_shadow(plan)
    assert not Path(case["destination"]).exists()


def test_rename_failure_leaves_only_non_success_staging_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _valid_bundle(tmp_path)
    plan = _plan(case)

    def fail_rename(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("simulated")

    monkeypatch.setattr(tool.os, "rename", fail_rename)
    with pytest.raises(PermissionError):
        tool.materialize_shadow(plan)
    assert not Path(case["destination"]).exists()
    residue = next(tmp_path.glob(".shadow-output.shadow-stage-*"))
    report = json.loads(
        (residue / tool.REPORT_FILENAME).read_text(encoding="utf-8")
    )
    assert report["ok"] is False
    assert report["promotion_completed"] is False
    assert not (residue / tool.COMPLETION_FILENAME).exists()


def test_free_space_gate_runs_before_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _valid_bundle(tmp_path)
    plan = _plan(case)
    monkeypatch.setattr(
        tool.shutil,
        "disk_usage",
        lambda _path: type("Usage", (), {"free": 0})(),
    )
    with pytest.raises(
        tool.ShadowRecoveryError,
        match="destination_free_space_insufficient",
    ):
        tool.materialize_shadow(plan)
    assert not Path(case["destination"]).exists()
    assert not list(tmp_path.glob(".shadow-output.shadow-stage-*"))


def test_tampered_blob_fails_before_destination_write(tmp_path: Path) -> None:
    case = _valid_bundle(tmp_path)
    state = next(
        artifact
        for artifact in case["artifacts"]
        if artifact["artifact_id"] == "state.shadow"
    )
    blob = (
        Path(case["bundle"])
        / "blobs"
        / "sha256"
        / state["content_digest"].removeprefix("sha256:")
    )
    blob.write_bytes(b"X" * state["byte_size"])
    with pytest.raises(tool.ShadowRecoveryError, match="blob_digest_mismatch"):
        _plan(case)
    assert not Path(case["destination"]).exists()


def test_blob_size_mismatch_is_rejected_before_hashing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _valid_bundle(tmp_path)
    state = next(
        artifact
        for artifact in case["artifacts"]
        if artifact["artifact_id"] == "state.shadow"
    )
    blob = (
        Path(case["bundle"])
        / "blobs"
        / "sha256"
        / state["content_digest"].removeprefix("sha256:")
    )
    blob.write_bytes(b"oversized")
    real_open = open

    def guarded_open(path: object, *args: object, **kwargs: object):
        if Path(path) == blob:
            raise AssertionError("oversized blob must be rejected before open")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", guarded_open)
    with pytest.raises(tool.ShadowRecoveryError, match="source_size_mismatch"):
        _plan(case)
    assert not Path(case["destination"]).exists()


def test_blob_size_swap_after_plan_is_rejected_before_staging(
    tmp_path: Path,
) -> None:
    case = _valid_bundle(tmp_path)
    plan = _plan(case)
    state = next(
        artifact
        for artifact in case["artifacts"]
        if artifact["artifact_id"] == "state.shadow"
    )
    blob = (
        Path(case["bundle"])
        / "blobs"
        / "sha256"
        / state["content_digest"].removeprefix("sha256:")
    )
    blob.write_bytes(blob.read_bytes() + b"unexpected-growth")
    with pytest.raises(tool.ShadowRecoveryError, match="source_size_mismatch"):
        tool.materialize_shadow(plan)
    assert not Path(case["destination"]).exists()
    assert not list(tmp_path.glob(".shadow-output.shadow-stage-*"))


def test_wrong_trusted_manifest_digest_fails_without_write(tmp_path: Path) -> None:
    case = _valid_bundle(tmp_path)
    with pytest.raises(
        tool.ShadowRecoveryError,
        match="trusted_manifest_digest_mismatch",
    ):
        _plan(case, expected_manifest_digest=ZERO_DIGEST)
    assert not Path(case["destination"]).exists()


def test_wrong_exact_commit_fails_without_write(tmp_path: Path) -> None:
    case = _valid_bundle(tmp_path)
    with pytest.raises(ContractValidationError):
        _plan(case, expected_commit="2" * 40)
    assert not Path(case["destination"]).exists()


def test_wrong_semantic_manifest_digest_fails_without_write(
    tmp_path: Path,
) -> None:
    case = _valid_bundle(tmp_path)
    manifest_path = Path(case["manifest_path"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["manifest_digest"] = ZERO_DIGEST
    manifest_path.write_bytes(_canonical_json_bytes(manifest))
    with pytest.raises(ContractValidationError):
        _plan(
            case,
            expected_manifest_digest=sha256_file(manifest_path),
        )
    assert not Path(case["destination"]).exists()


@pytest.mark.parametrize(
    ("fixture_kwargs", "error"),
    [
        ({"omit_blob_for": "state"}, "blob_missing"),
        ({"add_extra_blob": True}, "unexpected_blob"),
    ],
)
def test_missing_or_extra_blob_fails_without_write(
    fixture_kwargs: dict[str, object],
    error: str,
    tmp_path: Path,
) -> None:
    case = _valid_bundle(tmp_path, **fixture_kwargs)
    with pytest.raises(tool.ShadowRecoveryError, match=error):
        _plan(case)
    assert not Path(case["destination"]).exists()


def test_genome_topology_reference_mismatch_fails_without_write(
    tmp_path: Path,
) -> None:
    case = _valid_bundle(tmp_path, mismatched_genome_ref=True)
    with pytest.raises(
        tool.ShadowRecoveryError,
        match="genome_topology_reference_mismatch",
    ):
        _plan(case)
    assert not Path(case["destination"]).exists()


def test_unbundled_required_magma_input_fails_without_write(
    tmp_path: Path,
) -> None:
    case = _valid_bundle(tmp_path, include_unbundled_magma_input=True)
    with pytest.raises(
        ContractValidationError,
        match="required_input_artifact_missing",
    ):
        _plan(case)
    assert not Path(case["destination"]).exists()


def test_copy_failure_cleans_owned_staging_and_never_creates_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _valid_bundle(tmp_path)
    plan = _plan(case)

    def fail_copy(*_args: object, **_kwargs: object) -> None:
        raise tool.ShadowRecoveryError("simulated_copy_failure")

    monkeypatch.setattr(tool, "_copy_exclusive", fail_copy)
    with pytest.raises(tool.ShadowRecoveryError, match="simulated_copy_failure"):
        tool.materialize_shadow(plan)
    assert not Path(case["destination"]).exists()
    assert not list(tmp_path.glob(".shadow-output.shadow-stage-*"))
