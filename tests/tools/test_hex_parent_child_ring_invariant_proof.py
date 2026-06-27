# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import subprocess
import sys

from tools.run_hex_parent_child_ring_invariant_proof import (
    build_hex_parent_child_ring_invariant_proof,
)


def test_invariant_proof_writes_report(tmp_path):
    out_dir = tmp_path / "proof"

    report = build_hex_parent_child_ring_invariant_proof(out_dir=out_dir)

    assert report["ok"] is True
    assert report["blockers"] == []
    assert report["proof_checks"] == {
        "parent_child_bidirectional_consistent": True,
        "hierarchy_acyclic": True,
        "ancestor_descendant_duality": True,
        "sibling_consistency": True,
        "root_has_no_parent": True,
        "ring_valid_neighbor_delivered": True,
        "ring_non_neighbor_blocked_topology_class": True,
        "ring_child_to_parent_edge_enforced": True,
        "ring_parent_to_child_edge_enforced": True,
        "ring_unknown_cell_blocked_schema_class": True,
        "ring_summary_internally_consistent": True,
        "runtime_mutation_authority_false": True,
    }

    # the dormant / observability-only invariant: nothing mutated, nothing transported
    assert report["topology_digest_before"] == report["topology_digest_after"]
    assert report["ring_delivery_summary"]["transport_applied"] is False
    # the batch summary balances
    summary = report["ring_delivery_summary"]
    assert summary["total"] == (
        summary["delivered_count"] + summary["blocked_count"]
    )

    proof_path = out_dir / "hex_parent_child_ring_invariant_proof.json"
    assert proof_path.exists()
    saved = json.loads(proof_path.read_text(encoding="utf-8"))
    assert saved == report


def test_invariant_proof_refuses_existing_out_dir(tmp_path):
    out_dir = tmp_path / "proof"
    out_dir.mkdir()

    cmd = [
        sys.executable,
        "tools/run_hex_parent_child_ring_invariant_proof.py",
        "--out-dir",
        str(out_dir),
        "--json",
    ]

    proc = subprocess.run(
        cmd,
        check=False,
        cwd=".",
        text=True,
        capture_output=True,
    )

    assert proc.returncode == 1
    assert "out_dir must not exist" in proc.stderr


def test_invariant_proof_cli_json(tmp_path):
    out_dir = tmp_path / "proof"
    cmd = [
        sys.executable,
        "tools/run_hex_parent_child_ring_invariant_proof.py",
        "--out-dir",
        str(out_dir),
        "--now",
        "2026-06-27T00:00:00Z",
        "--json",
    ]

    proc = subprocess.run(
        cmd,
        check=False,
        cwd=".",
        text=True,
        capture_output=True,
    )

    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["ok"] is True
    assert report["generated_at_utc"] == "2026-06-27T00:00:00Z"
    assert report["report_version"] == (
        "wd.hex_parent_child_ring_invariant_proof.v0"
    )
