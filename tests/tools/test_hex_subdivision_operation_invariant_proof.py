# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import subprocess
import sys

from tools.run_hex_subdivision_operation_invariant_proof import (
    build_hex_subdivision_operation_invariant_proof,
)


def test_subdivision_operation_proof_writes_report(tmp_path):
    out_dir = tmp_path / "proof"

    report = build_hex_subdivision_operation_invariant_proof(out_dir=out_dir)

    assert report["ok"] is True
    assert report["blockers"] == []
    assert report["proof_checks"] == {
        "plan_id_deterministic": True,
        "plan_validates_fail_closed": True,
        "plan_no_runtime_mutation_flag": True,
        "apply_registers_children_under_parent": True,
        "apply_children_are_shadow_leaves": True,
        "candidate_preserves_hierarchy_invariants": True,
        "source_topology_unchanged": True,
        "apply_deterministic": True,
    }

    # the dormant invariant: the source topology is never mutated by an apply
    assert (
        report["source_topology_digest_before"]
        == report["source_topology_digest_after"]
    )
    # the candidate gained exactly the two shadow children under the parent
    assert report["candidate_cell_ids"] == [
        "root",
        "thermal",
        "thermal.cooling",
        "thermal.heating",
    ]
    assert report["plan"]["no_runtime_mutation"] is True

    proof_path = out_dir / "hex_subdivision_operation_invariant_proof.json"
    assert proof_path.exists()
    saved = json.loads(proof_path.read_text(encoding="utf-8"))
    assert saved == report


def test_subdivision_operation_proof_refuses_existing_out_dir(tmp_path):
    out_dir = tmp_path / "proof"
    out_dir.mkdir()

    cmd = [
        sys.executable,
        "tools/run_hex_subdivision_operation_invariant_proof.py",
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


def test_subdivision_operation_proof_cli_json(tmp_path):
    out_dir = tmp_path / "proof"
    cmd = [
        sys.executable,
        "tools/run_hex_subdivision_operation_invariant_proof.py",
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
        "wd.hex_subdivision_operation_invariant_proof.v0"
    )
