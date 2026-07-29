# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
from types import MappingProxyType

import pytest

from tools import build_hex_recovery_bundle as tool
from tools.run_hex_blank_disk_recovery_dry_run import build_recovery_plan
from waggledance.core.hex_topology.recovery_contract import (
    ContractValidationError,
    validate_hive_recovery_manifest,
)
from waggledance.core.magma.canonical import canonical_json_bytes


def _head() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=tool.REPO_ROOT,
        text=True,
    ).strip()


@pytest.fixture(scope="module")
def current_snapshot() -> tool.ExactCommitSnapshot:
    return tool.read_exact_commit_snapshot(tool.REPO_ROOT, _head())


@pytest.fixture(scope="module")
def current_image(
    current_snapshot: tool.ExactCommitSnapshot,
) -> tool.HexRecoveryBundleImage:
    return tool._build_hex_recovery_bundle_from_snapshot(current_snapshot)


def _git_blob(relative_path: str, raw: bytes) -> tool.ExactGitBlob:
    object_id = hashlib.sha1(
        b"blob " + str(len(raw)).encode("ascii") + b"\x00" + raw,
        usedforsecurity=False,
    ).hexdigest()
    return tool.ExactGitBlob(
        relative_path=relative_path,
        mode="100644",
        object_id=object_id,
        data=raw,
        content_digest=f"sha256:{hashlib.sha256(raw).hexdigest()}",
    )


def _replace_blob(
    snapshot: tool.ExactCommitSnapshot,
    relative_path: str,
    raw: bytes,
) -> tool.ExactCommitSnapshot:
    blobs = dict(snapshot.blobs_by_path)
    blobs[relative_path] = _git_blob(relative_path, raw)
    return replace(snapshot, blobs_by_path=MappingProxyType(blobs))


def _remove_blob(
    snapshot: tool.ExactCommitSnapshot,
    relative_path: str,
) -> tool.ExactCommitSnapshot:
    blobs = dict(snapshot.blobs_by_path)
    del blobs[relative_path]
    return replace(snapshot, blobs_by_path=MappingProxyType(blobs))


def _genomes(
    image: tool.HexRecoveryBundleImage,
) -> dict[str, dict]:
    return {
        reference: json.loads(raw.decode("utf-8"))
        for reference, raw in image.genome_bytes_by_ref.items()
    }


def test_current_exact_head_bundle_is_valid_and_complete(
    current_snapshot: tool.ExactCommitSnapshot,
    current_image: tool.HexRecoveryBundleImage,
) -> None:
    manifest = current_image.manifest
    genomes = _genomes(current_image)
    validated = validate_hive_recovery_manifest(
        manifest,
        genomes_by_ref=genomes,
        expected_commit=current_snapshot.commit_sha,
        require_recovery_ready=False,
    )

    topologies = {item["mesh_id"]: item for item in validated["topologies"]}
    assert set(topologies) == {"agent.axial", "solver.logical"}
    assert len(topologies["agent.axial"]["cells"]) == 7
    assert len(topologies["solver.logical"]["cells"]) == 8
    assert topologies["agent.axial"]["routing_invariants"]["max_route_hops"] == 2
    assert topologies["solver.logical"]["routing_invariants"]["max_route_hops"] == 3
    assert len(genomes) == 15
    assert len(validated["artifacts"]) == 15
    assert validated["replicas"] == []

    axiom_capabilities = [
        capability
        for genome in genomes.values()
        for capability in genome["capabilities"]
        if capability["capability_id"].startswith("solver.axiom.")
    ]
    assert len(axiom_capabilities) == 22
    assert len({item["capability_id"] for item in axiom_capabilities}) == 22
    assert all(
        genome["repair_peer_cell_ids"] == genome["neighbor_cell_ids"]
        and len(genome["repair_peer_cell_ids"]) >= 2
        for genome in genomes.values()
    )
    assert {
        genome["mesh_kind"] for genome in genomes.values()
    } == {"axial_agent_mesh", "logical_solver_overlay"}
    assert all(
        genome["parent_cell_id"] is None
        and genome["child_cell_ids"] == []
        and genome["restore_state"] == "shadow_only"
        and genome["runtime_activation_authority_granted"] is False
        for genome in genomes.values()
    )

    assert current_image.manifest_bytes == canonical_json_bytes(dict(manifest))
    assert current_image.manifest_file_digest == (
        "sha256:" + hashlib.sha256(current_image.manifest_bytes).hexdigest()
    )
    for artifact in validated["artifacts"]:
        raw = current_image.genome_bytes_by_ref[artifact["relative_path"]]
        assert artifact["byte_size"] == len(raw)
        assert artifact["content_digest"] == (
            "sha256:" + hashlib.sha256(raw).hexdigest()
        )


def test_same_snapshot_is_byte_identical_and_never_reads_worktree_sources(
    current_snapshot: tool.ExactCommitSnapshot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tool._build_hex_recovery_bundle_from_snapshot(current_snapshot)

    def forbidden_read_bytes(_path: Path) -> bytes:
        raise AssertionError("worktree read attempted")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read_bytes)
    second_snapshot = tool.read_exact_commit_snapshot(
        tool.REPO_ROOT,
        current_snapshot.commit_sha,
    )
    second = tool._build_hex_recovery_bundle_from_snapshot(second_snapshot)

    assert second.manifest_bytes == first.manifest_bytes
    assert dict(second.genome_bytes_by_ref) == dict(first.genome_bytes_by_ref)
    assert second.manifest_file_digest == first.manifest_file_digest
    assert all(
        blob.data == current_snapshot.blobs_by_path[path].data
        for path, blob in second_snapshot.blobs_by_path.items()
    )


def test_executable_topology_sources_match_reviewed_digest_pins(
    current_snapshot: tool.ExactCommitSnapshot,
) -> None:
    assert {
        relative_path: current_snapshot.blobs_by_path[
            relative_path
        ].content_digest
        for relative_path in tool._PINNED_EXECUTABLE_TOPOLOGY_DIGESTS
    } == dict(tool._PINNED_EXECUTABLE_TOPOLOGY_DIGESTS)


@pytest.mark.parametrize(
    "relative_path",
    [
        tool.AGENT_GEOMETRY_PATH,
        tool.SOLVER_TOPOLOGY_PATH,
    ],
)
def test_unreviewed_executable_topology_source_revision_fails_closed(
    current_snapshot: tool.ExactCommitSnapshot,
    relative_path: str,
) -> None:
    raw = current_snapshot.blobs_by_path[relative_path].data
    with pytest.raises(
        tool.BundleBuildError,
        match="topology_source_revision_unreviewed",
    ):
        tool._build_hex_recovery_bundle_from_snapshot(
            _replace_blob(current_snapshot, relative_path, raw + b"\n# changed\n")
        )


def test_public_builder_rechecks_git_and_running_implementation(
    current_snapshot: tool.ExactCommitSnapshot,
    current_image: tool.HexRecoveryBundleImage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    monkeypatch.setattr(
        tool,
        "read_exact_commit_snapshot",
        lambda repo_root, expected_head: current_snapshot,
    )
    monkeypatch.setattr(
        tool,
        "_build_hex_recovery_bundle_from_snapshot",
        lambda snapshot: current_image,
    )
    monkeypatch.setattr(
        tool,
        "_require_running_implementation_exact",
        lambda repo_root, expected_head: calls.append("implementation"),
    )
    monkeypatch.setattr(
        tool,
        "_require_expected_head",
        lambda repo_root, expected_head: calls.append("head"),
    )

    assert tool.build_hex_recovery_bundle(
        tool.REPO_ROOT,
        current_snapshot.commit_sha,
    ) is current_image
    assert calls == ["implementation", "head", "implementation"]


@pytest.mark.parametrize(
    "invalid",
    [
        "HEAD",
        "a" * 39,
        "A" * 40,
        "--help",
        "0" * 41,
    ],
)
def test_snapshot_reader_rejects_nonliteral_commit(
    invalid: str,
) -> None:
    with pytest.raises(tool.BundleBuildError, match="expected_head_invalid"):
        tool.read_exact_commit_snapshot(tool.REPO_ROOT, invalid)


def test_snapshot_reader_rejects_non_head_commit_and_ignores_git_environment(
    current_snapshot: tool.ExactCommitSnapshot,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    parent = subprocess.check_output(
        ["git", "rev-parse", "HEAD^"],
        cwd=tool.REPO_ROOT,
        text=True,
    ).strip()
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "hostile-git-dir"))
    reread = tool.read_exact_commit_snapshot(
        tool.REPO_ROOT,
        current_snapshot.commit_sha,
    )
    assert reread.commit_sha == current_snapshot.commit_sha

    with pytest.raises(tool.BundleBuildError, match="head_mismatch"):
        tool.read_exact_commit_snapshot(tool.REPO_ROOT, parent)


def _tree_record(
    relative_path: str,
    *,
    mode: str = "100644",
    object_type: str = "blob",
    object_id: str = "1" * 40,
) -> bytes:
    return (
        f"{mode} {object_type} {object_id}\t{relative_path}".encode("ascii")
        + b"\x00"
    )


def _minimal_tree_records() -> bytes:
    paths = [*sorted(tool._STATIC_SOURCE_PATHS), "configs/axioms/test/a.yaml"]
    return b"".join(_tree_record(path) for path in paths)


def test_tree_parser_accepts_only_exact_regular_allowlisted_blobs() -> None:
    parsed = tool._parse_tree_records(_minimal_tree_records())
    assert set(tool._STATIC_SOURCE_PATHS).issubset(parsed)
    assert "configs/axioms/test/a.yaml" in parsed


@pytest.mark.parametrize(
    "bad_record",
    [
        _tree_record(tool.AGENT_CONFIG_PATH, mode="100755"),
        _tree_record(tool.AGENT_CONFIG_PATH, mode="120000"),
        _tree_record(tool.AGENT_CONFIG_PATH, mode="160000", object_type="commit"),
        _tree_record("configs/axioms/test/not-yaml.txt"),
        _tree_record("../configs/axioms/test/a.yaml"),
        _tree_record(tool.AGENT_CONFIG_PATH, object_id="z" * 40),
    ],
)
def test_tree_parser_rejects_unsafe_git_entries(bad_record: bytes) -> None:
    safe = b"".join(
        _tree_record(path)
        for path in sorted(tool._STATIC_SOURCE_PATHS - {tool.AGENT_CONFIG_PATH})
    )
    safe += _tree_record("configs/axioms/test/a.yaml")
    with pytest.raises(tool.BundleBuildError):
        tool._parse_tree_records(safe + bad_record)


def test_tree_parser_rejects_duplicate_and_missing_records() -> None:
    valid = _minimal_tree_records()
    with pytest.raises(tool.BundleBuildError, match="git_tree_path_collision"):
        tool._parse_tree_records(valid + _tree_record(tool.AGENT_CONFIG_PATH))
    missing = b"".join(
        _tree_record(path)
        for path in sorted(tool._STATIC_SOURCE_PATHS - {tool.AGENT_CONFIG_PATH})
    )
    missing += _tree_record("configs/axioms/test/a.yaml")
    with pytest.raises(tool.BundleBuildError, match="git_required_source_missing"):
        tool._parse_tree_records(missing)


@pytest.mark.parametrize(
    "raw",
    [
        b"a: 1\na: 2\n",
        b"a: &anchor 1\nb: *anchor\n",
        b"base: &base {a: 1}\nmerged: {<<: *base}\n",
        b"{1: value}\n",
        b"a: !!str value\n",
        b"a: !!python/object:os.system {}\n",
        b"a: .nan\n",
        b"a: .inf\n",
        b"a: -.inf\n",
        b"a: 1.0e9999\n",
        b"a: yes\n",
        b"a: True\n",
        b"a: 0x10\n",
        b"a: 2026-07-29\n",
        b"---\na: 1\n---\nb: 2\n",
        b"\xef\xbb\xbfa: 1\n",
        b"a:\x00 1\n",
        b"\xff\n",
    ],
)
def test_strict_yaml_rejects_ambiguous_or_executable_forms(raw: bytes) -> None:
    with pytest.raises(tool.BundleBuildError):
        tool._strict_yaml_load(raw)


def test_strict_yaml_accepts_current_config_and_axioms(
    current_snapshot: tool.ExactCommitSnapshot,
) -> None:
    config = tool._strict_yaml_load(
        current_snapshot.blobs_by_path[tool.AGENT_CONFIG_PATH].data
    )
    assert len(config["cells"]) == 7
    axiom_paths = [
        path
        for path in current_snapshot.blobs_by_path
        if path.startswith(f"{tool.AXIOM_ROOT}/")
    ]
    assert len(axiom_paths) == 22
    assert all(
        tool._strict_yaml_load(
            current_snapshot.blobs_by_path[path].data
        )["model_id"]
        for path in axiom_paths
    )


@pytest.mark.parametrize(
    ("old", "new", "error"),
    [
        ("enabled: true", "enabled: false", "agent_cell_disabled"),
        (
            "coord: {q: 1, r: 0}",
            "coord: {q: 0, r: 0}",
            "agent_cell_coord_duplicate",
        ),
        (
            "neighbor_policy: default",
            "neighbor_policy: remote",
            "agent_neighbor_policy_unsupported",
        ),
    ],
)
def test_agent_topology_drift_fails_closed(
    current_snapshot: tool.ExactCommitSnapshot,
    old: str,
    new: str,
    error: str,
) -> None:
    original = current_snapshot.blobs_by_path[tool.AGENT_CONFIG_PATH].data.decode(
        "utf-8"
    )
    assert old in original
    mutated = original.replace(old, new, 1).encode("utf-8")
    with pytest.raises(tool.BundleBuildError, match=error):
        tool._build_hex_recovery_bundle_from_snapshot(
            _replace_blob(current_snapshot, tool.AGENT_CONFIG_PATH, mutated)
        )


def test_ast_parsers_reject_execution_and_post_assignment_mutation(
    current_snapshot: tool.ExactCommitSnapshot,
    tmp_path: Path,
) -> None:
    marker = tmp_path / "must-not-exist"
    payload = (
        "AXIAL_DIRECTIONS = "
        f"__import__('pathlib').Path({str(marker)!r}).write_text('owned')\n"
    ).encode("utf-8")
    with pytest.raises(
        tool.BundleBuildError,
        match="dynamic_namespace_operation_forbidden",
    ):
        tool._extract_axial_directions(payload)
    assert not marker.exists()

    solver = current_snapshot.blobs_by_path[tool.SOLVER_TOPOLOGY_PATH].data
    with pytest.raises(tool.BundleBuildError, match="topology_mutation_forbidden"):
        tool._extract_solver_topology(solver + b"\n_ADJACENCY.clear()\n")
    with pytest.raises(tool.BundleBuildError, match="topology_mutation_forbidden"):
        tool._extract_solver_topology(
            solver + b"\nALL_CELLS.append(CELL_GENERAL)\n"
        )
    with pytest.raises(tool.BundleBuildError, match="frozenset_binding_forbidden"):
        tool._extract_solver_topology(
            solver + b"\nfrozenset = lambda value: value\n"
        )
    with pytest.raises(tool.BundleBuildError, match="topology_alias_forbidden"):
        tool._extract_solver_topology(
            solver + b"\n_ALIAS = _ADJACENCY\n_ALIAS.clear()\n"
        )
    with pytest.raises(
        tool.BundleBuildError,
        match="dynamic_namespace_operation_forbidden",
    ):
        tool._extract_solver_topology(
            solver + b'\nglobals()["_ADJACENCY"] = {}\n'
        )
    dynamic = solver.replace(b"frozenset({", b"set({", 1)
    with pytest.raises(tool.BundleBuildError, match="solver_adjacency_value_invalid"):
        tool._extract_solver_topology(dynamic)


@pytest.mark.parametrize(
    ("transform", "error"),
    [
        (
            lambda raw: raw.replace(b"cell_id: general\n", b"", 1),
            "axiom_cell_id_invalid",
        ),
        (
            lambda raw: raw.replace(
                b"cell_id: general",
                b"cell_id: missing_cell",
                1,
            ),
            "axiom_cell_id_unknown",
        ),
        (
            lambda raw: raw.replace(
                b"model_id: indoor_air_quality",
                b"model_id: heating_cost",
                1,
            ),
            "axiom_model_id_duplicate",
        ),
    ],
)
def test_axiom_identity_drift_fails_closed(
    current_snapshot: tool.ExactCommitSnapshot,
    transform: object,
    error: str,
) -> None:
    path = "configs/axioms/home/indoor_air_quality.yaml"
    raw = current_snapshot.blobs_by_path[path].data
    mutated = transform(raw)  # type: ignore[operator]
    with pytest.raises(tool.BundleBuildError, match=error):
        tool._build_hex_recovery_bundle_from_snapshot(
            _replace_blob(current_snapshot, path, mutated)
        )


def test_solver_cell_without_axiom_fails_closed(
    current_snapshot: tool.ExactCommitSnapshot,
) -> None:
    with pytest.raises(tool.BundleBuildError, match="solver_cell_axioms_missing"):
        tool._build_hex_recovery_bundle_from_snapshot(
            _remove_blob(
                current_snapshot,
                "configs/axioms/home/indoor_air_quality.yaml",
            )
        )


@pytest.mark.parametrize(
    "relative_path",
    [
        tool.AGENT_REGISTRY_PATH,
        tool.SYMBOLIC_SOLVER_PATH,
    ],
)
def test_declared_python_capability_surface_cannot_be_empty(
    current_snapshot: tool.ExactCommitSnapshot,
    relative_path: str,
) -> None:
    with pytest.raises(
        tool.BundleBuildError,
        match="python_required_surface_missing",
    ):
        tool._build_hex_recovery_bundle_from_snapshot(
            _replace_blob(current_snapshot, relative_path, b"")
        )


def test_axiom_requires_minimal_executable_source_shape(
    current_snapshot: tool.ExactCommitSnapshot,
) -> None:
    path = "configs/axioms/home/indoor_air_quality.yaml"
    minimal = b"model_id: indoor_air_quality\ncell_id: general\n"
    with pytest.raises(
        tool.BundleBuildError,
        match="axiom_executable_shape_missing",
    ):
        tool._build_hex_recovery_bundle_from_snapshot(
            _replace_blob(current_snapshot, path, minimal)
        )


def test_writer_is_atomic_exact_and_existing_dry_run_accepts_bundle(
    current_image: tool.HexRecoveryBundleImage,
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "bundle"
    report = dict(tool.write_hex_recovery_bundle(current_image, bundle))
    expected_directory_durability = os.name != "nt"
    assert report["ok"] is expected_directory_durability
    assert report["topology_count"] == 2
    assert report["cell_count"] == 15
    assert report["genome_count"] == 15
    assert report["axiom_capability_count"] == 22
    assert report["candidate_digest_is_independently_trusted"] is False
    assert report["authoritative_hierarchical_runtime_shadow_available"] is False
    assert report["hierarchical_runtime_shadow_included"] is False
    assert report["target_state_topology_coverage_complete"] is False
    assert report["replica_count"] == 0
    assert report["external_replication_verified"] is False
    assert report["blank_disk_dry_run_verified"] is False
    assert report["transport_enabled"] is False
    assert report["runtime_activation_authority_granted"] is False
    assert report["claim_safe_upgrade"] is False
    assert report["production_ready_claim"] is False
    assert report["functional_capability_recovery_verified"] is False
    assert report["mutable_state_coverage_complete"] is False
    assert report["source_inventory_complete"] is False
    assert report["bundle_artifact_inventory_complete"] is True
    assert report["bundle_complete"] is True
    assert report["promotion_completed"] is True
    assert (
        report["staging_directory_fsync_completed"]
        is expected_directory_durability
    )
    assert (
        report["parent_directory_fsync_completed"]
        is expected_directory_durability
    )
    assert (
        report["directory_durability_verified"]
        is expected_directory_durability
    )
    assert report["error"] == (
        None
        if expected_directory_durability
        else "directory_fsync_unavailable_after_promotion"
    )

    assert {path.name for path in bundle.iterdir()} == {
        tool.MANIFEST_FILENAME,
        "blobs",
    }
    blob_root = bundle / "blobs" / "sha256"
    blobs = list(blob_root.iterdir())
    assert len(blobs) == 15
    assert all(
        path.name == hashlib.sha256(path.read_bytes()).hexdigest()
        for path in blobs
    )
    assert not any(path.name.endswith("report.json") for path in bundle.rglob("*"))

    plan = build_recovery_plan(
        bundle_root_arg=str(bundle),
        manifest_arg=str(bundle / tool.MANIFEST_FILENAME),
        destination_arg=str(tmp_path / "shadow-destination"),
        expected_commit=current_image.commit_sha,
        expected_manifest_digest=current_image.manifest_file_digest,
    )
    assert len(plan.artifacts) == 15
    assert plan.genome_count == 15
    assert plan.blob_count == 15


def test_writer_rejects_existing_and_reserved_destinations_without_mutation(
    current_image: tool.HexRecoveryBundleImage,
    tmp_path: Path,
) -> None:
    existing = tmp_path / "existing"
    existing.mkdir()
    marker = existing / "marker"
    marker.write_text("preserve", encoding="utf-8")
    with pytest.raises(tool.BundleBuildError, match="output_must_not_exist"):
        tool.write_hex_recovery_bundle(current_image, existing)
    assert marker.read_text(encoding="utf-8") == "preserve"

    with pytest.raises(tool.BundleBuildError, match="output_name_invalid"):
        tool.write_hex_recovery_bundle(current_image, tmp_path / "CON")


def test_writer_rejects_source_repo_and_git_metadata_destinations(
    current_image: tool.HexRecoveryBundleImage,
) -> None:
    with pytest.raises(tool.BundleBuildError, match="output_scope_unsafe"):
        tool.write_hex_recovery_bundle(
            current_image,
            tool.REPO_ROOT / "bundle-must-not-be-created",
        )
    with pytest.raises(tool.BundleBuildError, match="output_scope_unsafe"):
        tool.write_hex_recovery_bundle(
            current_image,
            tool.REPO_ROOT / ".git" / "bundle-must-not-be-created",
        )


def test_writer_failure_never_promotes_partial_destination(
    current_image: tool.HexRecoveryBundleImage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "partial"
    original = tool._exclusive_write
    calls = 0

    def fail_second(path: Path, raw: bytes) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise tool.BundleBuildError("injected_write_failure")
        original(path, raw)

    monkeypatch.setattr(tool, "_exclusive_write", fail_second)
    with pytest.raises(tool.BundleBuildError, match="injected_write_failure"):
        tool.write_hex_recovery_bundle(current_image, destination)
    assert not destination.exists()
    assert not list(tmp_path.glob(".partial.hex-bundle-stage-*"))


def test_post_promotion_parent_fsync_failure_is_explicit_terminal(
    current_image: tool.HexRecoveryBundleImage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "durability-warning"
    original = tool._fsync_directory
    calls = 0

    def fail_parent_sync(path: Path) -> bool:
        nonlocal calls
        calls += 1
        if calls == 4:
            raise OSError("injected parent fsync failure")
        return original(path)

    monkeypatch.setattr(tool, "_fsync_directory", fail_parent_sync)
    report = dict(tool.write_hex_recovery_bundle(current_image, destination))
    assert destination.is_dir()
    assert report["ok"] is False
    assert report["bundle_complete"] is True
    assert report["promotion_completed"] is True
    assert report["parent_directory_fsync_completed"] is False
    assert report["directory_durability_verified"] is False
    assert report["error"] == "parent_directory_fsync_failed_after_promotion"
    assert (destination / tool.MANIFEST_FILENAME).is_file()
    assert len(list((destination / "blobs" / "sha256").iterdir())) == 15


def test_unavailable_directory_fsync_is_never_reported_as_success(
    current_image: tool.HexRecoveryBundleImage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Path] = []

    def unavailable(path: Path) -> bool:
        calls.append(path)
        return False

    monkeypatch.setattr(tool, "_fsync_directory", unavailable)
    destination = tmp_path / "durability-unavailable"
    report = dict(tool.write_hex_recovery_bundle(current_image, destination))

    assert len(calls) == 4
    assert destination.is_dir()
    assert report["ok"] is False
    assert report["bundle_complete"] is True
    assert report["promotion_completed"] is True
    assert report["staging_directory_fsync_completed"] is False
    assert report["parent_directory_fsync_completed"] is False
    assert report["directory_durability_verified"] is False
    assert report["error"] == "directory_fsync_unavailable_after_promotion"


def test_writer_does_not_replace_concurrently_created_empty_destination(
    current_image: tool.HexRecoveryBundleImage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "raced"
    original = tool._rename_no_replace

    def race(source: Path, final: Path) -> None:
        final.mkdir()
        original(source, final)

    monkeypatch.setattr(tool, "_rename_no_replace", race)
    with pytest.raises(
        tool.BundleBuildError,
        match="output_changed_before_promotion",
    ):
        tool.write_hex_recovery_bundle(current_image, destination)
    assert destination.is_dir()
    assert not list(destination.iterdir())
    assert not list(tmp_path.glob(".raced.hex-bundle-stage-*"))


def test_writer_rejects_reparse_parent_when_supported(
    current_image: tool.HexRecoveryBundleImage,
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("directory symlink creation unavailable")
    with pytest.raises(
        tool.BundleBuildError,
        match="output_path_reparse_forbidden",
    ):
        tool.write_hex_recovery_bundle(current_image, link / "bundle")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("production_ready_claim", True),
        ("external_replication_verified", True),
        ("blank_disk_dry_run_verified", "false"),
    ],
)
def test_writer_revalidates_authority_locks(
    current_image: tool.HexRecoveryBundleImage,
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    manifest = dict(current_image.manifest)
    manifest[field] = value
    raw = canonical_json_bytes(manifest)
    hostile = replace(
        current_image,
        manifest=manifest,
        manifest_bytes=raw,
        manifest_file_digest=(
            "sha256:" + hashlib.sha256(raw).hexdigest()
        ),
    )
    with pytest.raises(ContractValidationError):
        tool.write_hex_recovery_bundle(hostile, tmp_path / f"hostile-{field}")


def test_cli_failure_is_path_free_and_does_not_create_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret_named_output = tmp_path / "PRIVATE-output-path"
    result = tool.main(
        [
            "--expected-head",
            "HEAD",
            "--out-dir",
            str(secret_named_output),
            "--json",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert result == 2
    assert payload == {
        "ok": False,
        "build_contract": tool.BUNDLE_BUILD_VERSION,
        "error": "expected_head_invalid",
    }
    assert str(secret_named_output) not in captured.out
    assert str(secret_named_output) not in captured.err
    assert not secret_named_output.exists()
