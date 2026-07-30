# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from copy import deepcopy
import hashlib
from itertools import combinations
import json
from pathlib import Path

import pytest
from jsonschema import Draft7Validator, FormatChecker

from waggledance.core.hex_cell_topology import ALL_CELLS, _ADJACENCY
from waggledance.core.hex_topology import recovery_contract as recovery
from waggledance.core.magma.canonical import sha256_digest


COMMIT = "a" * 40
DIGEST = "sha256:" + ("1" * 64)
CREATED_AT = "2026-07-29T10:00:00Z"


def _capabilities(cell_id: str, mesh_kind: str) -> list[dict]:
    kind = "solver" if mesh_kind == "logical_solver_overlay" else "agent"
    return [
        {
            "kind": kind,
            "capability_id": f"{kind}.{cell_id}",
            "source_ref": f"capabilities/{cell_id}.yaml",
            "source_digest": DIGEST,
            "required": True,
        }
    ]


def _inputs(cell_id: str) -> list[dict]:
    return [
        {
            "input_id": f"source.{cell_id}",
            "kind": "repo_artifact",
            "source_ref": f"capabilities/{cell_id}.yaml",
            "source_digest": DIGEST,
            "rebuild_strategy": "git_checkout",
            "replay_checkpoint": None,
            "required": True,
        }
    ]


def _genome(
    cell_id: str,
    neighbors: list[str],
    *,
    mesh_id: str = "solver.hex",
    mesh_kind: str = "logical_solver_overlay",
    topology_epoch: int = 1,
    coord: dict[str, int] | None = None,
    parent: str | None = None,
    children: list[str] | None = None,
) -> dict:
    return recovery.build_hex_cell_genome(
        cell_id=cell_id,
        mesh_id=mesh_id,
        mesh_kind=mesh_kind,
        topology_epoch=topology_epoch,
        axial_coord=coord,
        parent_cell_id=parent,
        child_cell_ids=children or [],
        neighbor_cell_ids=neighbors,
        repair_peer_cell_ids=neighbors[:2],
        capabilities=_capabilities(cell_id, mesh_kind),
        durable_inputs=_inputs(cell_id),
    )


def _triangle_genomes() -> dict[str, dict]:
    cell_ids = ("general", "math", "system")
    return {
        f"genomes/{cell_id}.json": _genome(
            cell_id,
            [other for other in cell_ids if other != cell_id],
        )
        for cell_id in cell_ids
    }


def _topology(
    genomes: dict[str, dict],
    *,
    mesh_id: str = "solver.hex",
    mesh_kind: str = "logical_solver_overlay",
    topology_epoch: int = 1,
    max_route_hops: int = 1,
) -> dict:
    return {
        "mesh_id": mesh_id,
        "mesh_kind": mesh_kind,
        "topology_epoch": topology_epoch,
        "cells": [
            {
                "cell_id": genome["cell_id"],
                "genome_ref": ref,
                "genome_digest": genome["genome_digest"],
                "expected_cell_state_root": genome["expected_cell_state_root"],
            }
            for ref, genome in genomes.items()
        ],
        "routing_invariants": {
            "connected": True,
            "bidirectional_neighbors": True,
            "survive_single_cell_loss": True,
            "max_route_hops": max_route_hops,
        },
    }


def _artifacts(genomes: dict[str, dict], *, include_state: bool = False) -> list[dict]:
    artifacts = [
        {
            "artifact_id": f"genome.{genome['cell_id']}",
            "relative_path": ref,
            "content_digest": sha256_digest(genome),
            "byte_size": 1,
            "classification": "genome",
            "required": True,
            "restore_strategy": "git_checkout",
        }
        for ref, genome in genomes.items()
    ]
    if include_state:
        artifacts.append(
            {
                "artifact_id": "state.ledger",
                "relative_path": "state/ledger.jsonl",
                "content_digest": "sha256:" + ("2" * 64),
                "byte_size": 10,
                "classification": "mutable_state",
                "required": True,
                "restore_strategy": "verified_copy",
            }
        )
    return artifacts


def _source_repository() -> dict:
    return {
        "repository_ref": "github.com/example/waggledance",
        "commit_sha": COMMIT,
        "source_of_truth": "git_primary",
        "require_clean_clone": True,
        "backup_as_primary": False,
    }


def _manifest(
    genomes: dict[str, dict] | None = None,
    *,
    topologies: list[dict] | None = None,
    artifacts: list[dict] | None = None,
    replicas: list[dict] | None = None,
    external_verified: bool = False,
    blank_disk_verified: bool = False,
    created_at: str = CREATED_AT,
) -> dict:
    genomes = genomes or _triangle_genomes()
    return recovery.build_hive_recovery_manifest(
        manifest_id="manifest.test",
        created_at_utc=created_at,
        source_repository=_source_repository(),
        topologies=topologies or [_topology(genomes)],
        artifacts=artifacts or _artifacts(genomes),
        replicas=replicas or [],
        external_replication_verified=external_verified,
        blank_disk_dry_run_verified=blank_disk_verified,
        previous_manifest_digest=None,
        genomes_by_ref=genomes,
    )


def _reforge_genome(genome: dict) -> dict:
    forged = deepcopy(genome)
    forged["expected_cell_state_root"] = recovery.compute_cell_state_root(forged)
    forged["genome_digest"] = sha256_digest(
        {key: value for key, value in forged.items() if key != "genome_digest"}
    )
    return forged


def test_cell_builder_round_trip_is_shadow_only_and_does_not_mutate_inputs() -> None:
    neighbors = ["math", "system"]
    capabilities = _capabilities("general", "logical_solver_overlay")
    durable_inputs = _inputs("general")
    before = deepcopy((neighbors, capabilities, durable_inputs))
    genome = recovery.build_hex_cell_genome(
        cell_id="general",
        mesh_id="solver.hex",
        mesh_kind="logical_solver_overlay",
        topology_epoch=1,
        axial_coord=None,
        neighbor_cell_ids=neighbors,
        repair_peer_cell_ids=neighbors,
        capabilities=capabilities,
        durable_inputs=durable_inputs,
    )

    assert (neighbors, capabilities, durable_inputs) == before
    assert recovery.validate_hex_cell_genome(genome) == genome
    assert genome["restore_state"] == "shadow_only"
    assert genome["runtime_activation_authority_granted"] is False
    assert genome["expected_cell_state_root"].startswith("sha256:")
    assert genome["genome_digest"].startswith("sha256:")


def test_neighbor_routing_order_is_bound_into_cell_root() -> None:
    forward = _genome("general", ["math", "system"])
    reverse = _genome("general", ["system", "math"])
    assert forward["expected_cell_state_root"] != reverse["expected_cell_state_root"]
    assert forward["genome_digest"] != reverse["genome_digest"]


def test_cell_tamper_and_bool_as_int_fail_closed() -> None:
    genome = _genome("general", ["math", "system"])
    tampered = deepcopy(genome)
    tampered["expected_cell_state_root"] = DIGEST
    tampered["genome_digest"] = sha256_digest(
        {key: value for key, value in tampered.items() if key != "genome_digest"}
    )
    with pytest.raises(
        recovery.ContractValidationError,
        match="cell_state_root_mismatch",
    ):
        recovery.validate_hex_cell_genome(tampered)

    epoch_bool = deepcopy(genome)
    epoch_bool["topology_epoch"] = True
    with pytest.raises(recovery.ContractValidationError, match="invalid_integer"):
        recovery.validate_hex_cell_genome(epoch_bool)


def test_required_capability_must_be_bound_to_a_required_durable_input() -> None:
    with pytest.raises(
        recovery.ContractValidationError,
        match="required_capability_source_unbound",
    ):
        recovery.build_hex_cell_genome(
            cell_id="general",
            mesh_id="solver.hex",
            mesh_kind="logical_solver_overlay",
            topology_epoch=1,
            axial_coord=None,
            neighbor_cell_ids=["math", "system"],
            repair_peer_cell_ids=["math", "system"],
            capabilities=_capabilities("general", "logical_solver_overlay"),
            durable_inputs=[
                {
                    **_inputs("general")[0],
                    "source_ref": "other/unbound.yaml",
                }
            ],
        )


@pytest.mark.parametrize(
    "path",
    [
        "../escape",
        "nested/../escape",
        "/absolute",
        "C:/drive",
        r"windows\path",
        "name:ads",
        "NUL.txt",
        "trailing.",
    ],
)
def test_repo_relative_paths_reject_windows_and_traversal_hazards(path: str) -> None:
    with pytest.raises(recovery.ContractValidationError) as caught:
        recovery.validate_repo_relative_path(path, "$.source_ref")
    assert path not in str(caught.value)
    assert caught.value.field_path == "$.source_ref"


def test_strict_json_rejects_duplicate_keys_and_nonfinite(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"a":1,"a":2}', encoding="utf-8")
    with pytest.raises(recovery.ContractValidationError, match="duplicate_json_key"):
        recovery.strict_json_load(duplicate)

    nonfinite = tmp_path / "nonfinite.json"
    nonfinite.write_text('{"a":NaN}', encoding="utf-8")
    with pytest.raises(recovery.ContractValidationError, match="nonfinite_json_number"):
        recovery.strict_json_load(nonfinite)

    valid = tmp_path / "valid.json"
    valid.write_bytes(b'{"a":1}')
    parsed, digest = recovery.strict_json_load_with_digest(valid)
    assert parsed == {"a": 1}
    assert digest == f"sha256:{hashlib.sha256(valid.read_bytes()).hexdigest()}"


@pytest.mark.parametrize(
    "payload",
    [
        '{"a":' + ("[" * 10000) + "0" + ("]" * 10000) + "}",
        '{"a":' + ("9" * 10000) + "}",
    ],
)
def test_strict_json_maps_parser_resource_errors_to_redacted_failure(
    payload: str,
    tmp_path: Path,
) -> None:
    path = tmp_path / "hostile.json"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(recovery.ContractValidationError) as caught:
        recovery.strict_json_load(path)
    assert caught.value.code == "invalid_json"
    assert "999999" not in str(caught.value)


def test_manifest_round_trip_roots_and_no_authority() -> None:
    genomes = _triangle_genomes()
    manifest = _manifest(genomes)
    assert (
        recovery.validate_hive_recovery_manifest(
            manifest,
            genomes_by_ref=genomes,
            expected_commit=COMMIT,
        )
        == manifest
    )
    assert manifest["recovery_policy"] == {
        "restore_state": "shadow_only",
        "require_exact_commit": True,
        "require_all_digests": True,
        "require_off_host_replica_for_non_git": True,
        "transport_enabled": False,
        "runtime_activation_authority_granted": False,
        "operator_gate_required_for_activation": True,
        "claim_safe_upgrade": False,
    }
    assert manifest["production_ready_claim"] is False


def test_manifest_builder_is_order_deterministic() -> None:
    genomes = _triangle_genomes()
    topology = _topology(genomes)
    artifacts = _artifacts(genomes)
    forward = _manifest(genomes, topologies=[topology], artifacts=artifacts)
    reverse_topology = deepcopy(topology)
    reverse_topology["cells"].reverse()
    reverse = _manifest(
        genomes,
        topologies=[reverse_topology],
        artifacts=list(reversed(artifacts)),
    )
    assert forward == reverse


def test_manifest_rejects_two_epochs_under_the_same_mesh_id() -> None:
    genomes = _triangle_genomes()
    first = _topology(genomes)
    second = deepcopy(first)
    second["topology_epoch"] = 2
    with pytest.raises(recovery.ContractValidationError, match="duplicate_topology"):
        _manifest(
            genomes,
            topologies=[first, second],
            artifacts=_artifacts(genomes),
        )


def test_state_root_ignores_timestamp_but_manifest_digest_does_not() -> None:
    genomes = _triangle_genomes()
    first = _manifest(genomes, created_at="2026-07-29T10:00:00Z")
    second = _manifest(genomes, created_at="2026-07-29T10:00:01Z")
    assert first["hive_state_root"] == second["hive_state_root"]
    assert first["manifest_digest"] != second["manifest_digest"]


def test_memory_and_hive_roots_bind_logical_restore_placement() -> None:
    genomes = _triangle_genomes()
    first_artifacts = _artifacts(genomes, include_state=True)
    moved_artifacts = deepcopy(first_artifacts)
    state = next(
        artifact
        for artifact in moved_artifacts
        if artifact["artifact_id"] == "state.ledger"
    )
    state["relative_path"] = "state/moved-ledger.jsonl"
    first_memory = recovery.compute_memory_root(first_artifacts)
    moved_memory = recovery.compute_memory_root(moved_artifacts)
    assert first_memory != moved_memory
    assert recovery.compute_hive_state_root(
        _source_repository(),
        DIGEST,
        first_memory,
    ) != recovery.compute_hive_state_root(
        _source_repository(),
        DIGEST,
        moved_memory,
    )


def test_manifest_tamper_and_exact_commit_mismatch_fail_closed() -> None:
    genomes = _triangle_genomes()
    manifest = _manifest(genomes)
    tampered = deepcopy(manifest)
    tampered["memory_root"] = DIGEST
    tampered["hive_state_root"] = recovery.compute_hive_state_root(
        tampered["source_repository"],
        tampered["genome_root"],
        tampered["memory_root"],
    )
    tampered["manifest_digest"] = sha256_digest(
        {key: value for key, value in tampered.items() if key != "manifest_digest"}
    )
    with pytest.raises(recovery.ContractValidationError, match="memory_root_mismatch"):
        recovery.validate_hive_recovery_manifest(tampered, genomes)
    with pytest.raises(recovery.ContractValidationError, match="exact_commit_mismatch"):
        recovery.validate_hive_recovery_manifest(
            manifest,
            genomes,
            expected_commit="b" * 40,
        )


def test_unknown_and_asymmetric_neighbors_fail_at_hive_boundary() -> None:
    unknown = _triangle_genomes()
    ref = "genomes/general.json"
    changed = deepcopy(unknown[ref])
    changed["neighbor_cell_ids"] = ["math", "ghost"]
    changed["repair_peer_cell_ids"] = ["math", "ghost"]
    unknown[ref] = _reforge_genome(changed)
    with pytest.raises(recovery.ContractValidationError, match="unknown_neighbor"):
        _manifest(unknown)

    ring = {
        "genomes/a.json": _genome("a", ["b", "d"]),
        "genomes/b.json": _genome("b", ["a", "c"]),
        "genomes/c.json": _genome("c", ["b", "d"]),
        "genomes/d.json": _genome("d", ["b", "c"]),
    }
    with pytest.raises(recovery.ContractValidationError, match="asymmetric_neighbor"):
        _manifest(
            ring,
            topologies=[_topology(ring, max_route_hops=3)],
            artifacts=_artifacts(ring),
        )


def test_articulation_point_fails_single_cell_loss_invariant() -> None:
    adjacency = {
        "hub": ["a", "b", "c", "d"],
        "a": ["hub", "b"],
        "b": ["hub", "a"],
        "c": ["hub", "d"],
        "d": ["hub", "c"],
    }
    genomes = {
        f"genomes/{cell}.json": _genome(cell, neighbors)
        for cell, neighbors in adjacency.items()
    }
    with pytest.raises(
        recovery.ContractValidationError,
        match="single_cell_loss_not_survivable",
    ):
        _manifest(
            genomes,
            topologies=[_topology(genomes, max_route_hops=2)],
            artifacts=_artifacts(genomes),
        )


def test_current_seven_cell_axial_mesh_passes_with_separate_namespace() -> None:
    coordinates = {
        "hub": (0, 0),
        "bee_ops": (1, 0),
        "environment": (0, -1),
        "home_comfort": (-1, 0),
        "safety_security": (-1, 1),
        "production": (0, 1),
        "logistics": (1, -1),
    }

    def distance(left: tuple[int, int], right: tuple[int, int]) -> int:
        dq, dr = left[0] - right[0], left[1] - right[1]
        return (abs(dq) + abs(dr) + abs(dq + dr)) // 2

    genomes: dict[str, dict] = {}
    for cell, coord in coordinates.items():
        neighbors = [
            other
            for other, other_coord in coordinates.items()
            if other != cell and distance(coord, other_coord) == 1
        ]
        ref = f"genomes/agent/{cell}.json"
        genomes[ref] = _genome(
            cell,
            neighbors,
            mesh_id="agent.axial",
            mesh_kind="axial_agent_mesh",
            coord={"q": coord[0], "r": coord[1]},
        )
    manifest = _manifest(
        genomes,
        topologies=[
            _topology(
                genomes,
                mesh_id="agent.axial",
                mesh_kind="axial_agent_mesh",
                max_route_hops=2,
            )
        ],
        artifacts=_artifacts(genomes),
    )
    assert manifest["topologies"][0]["mesh_kind"] == "axial_agent_mesh"
    assert len(manifest["topologies"][0]["cells"]) == 7


def test_current_eight_cell_solver_overlay_passes_and_ids_do_not_mix() -> None:
    genomes = {
        f"genomes/solver/{cell}.json": _genome(
            cell,
            sorted(_ADJACENCY[cell]),
            mesh_id="solver.logical",
        )
        for cell in ALL_CELLS
    }
    manifest = _manifest(
        genomes,
        topologies=[
            _topology(
                genomes,
                mesh_id="solver.logical",
                max_route_hops=3,
            )
        ],
        artifacts=_artifacts(genomes),
    )
    assert len(manifest["topologies"][0]["cells"]) == 8
    assert set(ALL_CELLS).isdisjoint(
        {
            "hub",
            "bee_ops",
            "environment",
            "home_comfort",
            "safety_security",
            "production",
            "logistics",
        }
    )


def test_current_seven_and_eight_cell_graphs_survive_any_two_cell_losses() -> None:
    coordinates = {
        "hub": (0, 0),
        "bee_ops": (1, 0),
        "environment": (0, -1),
        "home_comfort": (-1, 0),
        "safety_security": (-1, 1),
        "production": (0, 1),
        "logistics": (1, -1),
    }

    def axial_distance(left: tuple[int, int], right: tuple[int, int]) -> int:
        dq, dr = left[0] - right[0], left[1] - right[1]
        return (abs(dq) + abs(dr) + abs(dq + dr)) // 2

    agent_graph = {
        cell: {
            other
            for other, other_coord in coordinates.items()
            if other != cell and axial_distance(coord, other_coord) == 1
        }
        for cell, coord in coordinates.items()
    }
    solver_graph = {cell: set(_ADJACENCY[cell]) for cell in ALL_CELLS}

    def connected_after(graph: dict[str, set[str]], removed: set[str]) -> bool:
        remaining = set(graph) - removed
        origin = next(iter(remaining))
        seen = {origin}
        frontier = [origin]
        while frontier:
            node = frontier.pop()
            for neighbor in graph[node] - removed - seen:
                seen.add(neighbor)
                frontier.append(neighbor)
        return seen == remaining

    for graph in (agent_graph, solver_graph):
        for loss_count in (1, 2):
            assert all(
                connected_after(graph, set(removed))
                for removed in combinations(graph, loss_count)
            )


def test_hierarchical_shadow_requires_reciprocal_acyclic_parent_links() -> None:
    neighbors = {
        "a": ["b", "d"],
        "b": ["a", "c"],
        "c": ["b", "d"],
        "d": ["c", "a"],
    }
    parents = {"a": None, "b": "a", "c": "a", "d": "a"}
    children = {"a": ["b", "c", "d"], "b": [], "c": [], "d": []}
    genomes = {
        f"genomes/shadow/{cell}.json": _genome(
            cell,
            adjacent,
            mesh_id="runtime.shadow",
            mesh_kind="hierarchical_runtime_shadow",
            parent=parents[cell],
            children=children[cell],
        )
        for cell, adjacent in neighbors.items()
    }
    valid = _manifest(
        genomes,
        topologies=[
            _topology(
                genomes,
                mesh_id="runtime.shadow",
                mesh_kind="hierarchical_runtime_shadow",
                max_route_hops=2,
            )
        ],
        artifacts=_artifacts(genomes),
    )
    assert valid["topologies"][0]["mesh_kind"] == "hierarchical_runtime_shadow"

    broken = deepcopy(genomes)
    ref = "genomes/shadow/c.json"
    changed = deepcopy(broken[ref])
    changed["parent_cell_id"] = None
    broken[ref] = _reforge_genome(changed)
    with pytest.raises(
        recovery.ContractValidationError,
        match="parent_child_not_reciprocal",
    ):
        _manifest(
            broken,
            topologies=[
                _topology(
                    broken,
                    mesh_id="runtime.shadow",
                    mesh_kind="hierarchical_runtime_shadow",
                    max_route_hops=2,
                )
            ],
            artifacts=_artifacts(broken),
        )


def test_hierarchical_shadow_cannot_omit_the_hierarchy() -> None:
    cell_ids = ("a", "b", "c")
    genomes = {
        f"genomes/shadow/{cell}.json": _genome(
            cell,
            [other for other in cell_ids if other != cell],
            mesh_id="runtime.shadow",
            mesh_kind="hierarchical_runtime_shadow",
        )
        for cell in cell_ids
    }
    with pytest.raises(
        recovery.ContractValidationError,
        match="hierarchy_requires_single_root",
    ):
        _manifest(
            genomes,
            topologies=[
                _topology(
                    genomes,
                    mesh_id="runtime.shadow",
                    mesh_kind="hierarchical_runtime_shadow",
                    max_route_hops=1,
                )
            ],
            artifacts=_artifacts(genomes),
        )


def test_v1_cannot_self_certify_external_replication_or_recovery_ready() -> None:
    genomes = _triangle_genomes()
    artifacts = _artifacts(genomes, include_state=True)
    incomplete_replica = [
        {
            "artifact_id": "state.ledger",
            "replica_id": "replica.local",
            "failure_domain": "same_host",
            "locator_ref": "provider/local/state",
            "verified_at_utc": CREATED_AT,
            "content_digest": "sha256:" + ("2" * 64),
        }
    ]
    with pytest.raises(
        recovery.ContractValidationError,
        match="v1_external_replication_claim_forbidden",
    ):
        _manifest(
            genomes,
            artifacts=artifacts,
            replicas=incomplete_replica,
            external_verified=True,
        )

    off_host = deepcopy(incomplete_replica)
    off_host[0]["replica_id"] = "replica.remote"
    off_host[0]["failure_domain"] = "host.remote"
    off_host[0]["locator_ref"] = "provider/remote/state"
    manifest = _manifest(
        genomes,
        artifacts=artifacts,
        replicas=off_host,
        external_verified=False,
        blank_disk_verified=False,
    )
    with pytest.raises(
        recovery.ContractValidationError,
        match="recovery_readiness_not_available_in_v1",
    ):
        recovery.validate_hive_recovery_manifest(
            manifest,
            genomes,
            require_recovery_ready=True,
        )


def test_required_magma_input_cannot_bypass_artifact_and_off_host_gates() -> None:
    genomes = _triangle_genomes()
    ref = "genomes/general.json"
    changed = deepcopy(genomes[ref])
    changed["durable_inputs"] = [
        _inputs("general")[0],
        {
            "input_id": "magma.general",
            "kind": "magma_ledger",
            "source_ref": "state/general-ledger.jsonl",
            "source_digest": "sha256:" + ("3" * 64),
            "rebuild_strategy": "replay",
            "replay_checkpoint": 42,
            "required": True,
        }
    ]
    genomes[ref] = _reforge_genome(changed)
    topology = _topology(genomes)
    genome_artifacts = _artifacts(genomes)

    with pytest.raises(
        recovery.ContractValidationError,
        match="required_input_artifact_missing",
    ):
        _manifest(
            genomes,
            topologies=[topology],
            artifacts=genome_artifacts,
            external_verified=False,
            blank_disk_verified=False,
        )

    state_artifact = {
        "artifact_id": "state.general-ledger",
        "relative_path": "state/general-ledger.jsonl",
        "content_digest": "sha256:" + ("3" * 64),
        "byte_size": 42,
        "classification": "mutable_state",
        "required": True,
        "restore_strategy": "rebuild",
    }
    wrong_classification = {
        **state_artifact,
        "classification": "external_dependency",
    }
    with pytest.raises(
        recovery.ContractValidationError,
        match="required_input_artifact_classification_mismatch",
    ):
        _manifest(
            genomes,
            topologies=[topology],
            artifacts=[*genome_artifacts, wrong_classification],
        )

    replica = {
        "artifact_id": "state.general-ledger",
        "replica_id": "replica.general-ledger",
        "failure_domain": "host.remote",
        "locator_ref": "provider/remote/general-ledger",
        "verified_at_utc": CREATED_AT,
        "content_digest": state_artifact["content_digest"],
    }
    bound = _manifest(
        genomes,
        topologies=[topology],
        artifacts=[*genome_artifacts, state_artifact],
        replicas=[replica],
        external_verified=False,
        blank_disk_verified=False,
    )
    assert (
        recovery.validate_hive_recovery_manifest(
            bound,
            genomes,
            expected_commit=COMMIT,
        )["production_ready_claim"]
        is False
    )
    with pytest.raises(
        recovery.ContractValidationError,
        match="recovery_readiness_not_available_in_v1",
    ):
        recovery.validate_hive_recovery_manifest(
            bound,
            genomes,
            require_recovery_ready=True,
        )


def test_every_referenced_genome_artifact_is_required() -> None:
    genomes = _triangle_genomes()
    artifacts = _artifacts(genomes)
    artifacts[0]["required"] = False
    with pytest.raises(
        recovery.ContractValidationError,
        match="genome_artifact_must_be_required",
    ):
        _manifest(genomes, artifacts=artifacts)


def test_schemas_accept_builder_outputs_and_reject_authority_flip() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    genome_schema = json.loads(
        (repo_root / "schemas" / "hex_cell_genome.v1.json").read_text(
            encoding="utf-8"
        )
    )
    manifest_schema = json.loads(
        (repo_root / "schemas" / "hive_recovery_manifest.v1.json").read_text(
            encoding="utf-8"
        )
    )
    Draft7Validator.check_schema(genome_schema)
    Draft7Validator.check_schema(manifest_schema)
    genomes = _triangle_genomes()
    manifest = _manifest(genomes)
    Draft7Validator(genome_schema, format_checker=FormatChecker()).validate(
        next(iter(genomes.values()))
    )
    Draft7Validator(manifest_schema, format_checker=FormatChecker()).validate(
        manifest
    )

    authority = deepcopy(manifest)
    authority["production_ready_claim"] = True
    assert list(Draft7Validator(manifest_schema).iter_errors(authority))
