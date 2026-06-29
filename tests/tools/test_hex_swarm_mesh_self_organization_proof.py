# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import subprocess
import sys

from tools.run_hex_swarm_mesh_self_organization_proof import (
    build_hex_swarm_mesh_self_organization_proof,
)


def test_swarm_mesh_proof_writes_report(tmp_path):
    out_dir = tmp_path / "proof"

    report = build_hex_swarm_mesh_self_organization_proof(out_dir=out_dir)

    assert report["ok"] is True
    assert report["blockers"] == []
    assert report["proof_checks"] == {
        "multi_level_hierarchy_registered": True,
        "whole_tree_bidirectional_and_acyclic": True,
        "per_level_sibling_rings": True,
        "same_level_ring_delivers": True,
        "cross_level_child_to_parent_delivers": True,
        "cross_subtree_non_neighbor_blocks": True,
        "ancestor_descendant_duality_across_levels": True,
        "self_organization_deterministic": True,
        "source_topology_unchanged": True,
        "no_transport_in_delivery_summary": True,
    }
    # two-level mesh: root + 2 branches + 4 leaves
    assert report["mesh_cell_ids"] == [
        "root",
        "root.alpha",
        "root.alpha.a",
        "root.alpha.b",
        "root.beta",
        "root.beta.a",
        "root.beta.b",
    ]
    # RUNTIME MUTATION AUTHORITY FALSE: source byte-identical, no transport
    assert (
        report["source_topology_digest_before"]
        == report["source_topology_digest_after"]
    )
    assert report["ring_delivery_summary"]["transport_applied"] is False
    # the cross-subtree ring message blocked on the topology boundary
    assert report["ring_delivery_summary"]["blocked_by_category"] == {
        "not_neighbor": 1
    }

    proof_path = out_dir / "hex_swarm_mesh_self_organization_proof.json"
    assert proof_path.exists()
    assert json.loads(proof_path.read_text(encoding="utf-8")) == report


def test_swarm_mesh_proof_refuses_existing_out_dir(tmp_path):
    out_dir = tmp_path / "proof"
    out_dir.mkdir()

    proc = subprocess.run(
        [
            sys.executable,
            "tools/run_hex_swarm_mesh_self_organization_proof.py",
            "--out-dir",
            str(out_dir),
            "--json",
        ],
        check=False,
        cwd=".",
        text=True,
        capture_output=True,
    )

    assert proc.returncode == 1
    assert "out_dir must not exist" in proc.stderr


def test_swarm_mesh_proof_cli_json(tmp_path):
    out_dir = tmp_path / "proof"
    proc = subprocess.run(
        [
            sys.executable,
            "tools/run_hex_swarm_mesh_self_organization_proof.py",
            "--out-dir",
            str(out_dir),
            "--now",
            "2026-06-29T00:00:00Z",
            "--json",
        ],
        check=False,
        cwd=".",
        text=True,
        capture_output=True,
    )

    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["ok"] is True
    assert report["generated_at_utc"] == "2026-06-29T00:00:00Z"
    assert report["report_version"] == (
        "wd.hex_swarm_mesh_self_organization_proof.v0"
    )
