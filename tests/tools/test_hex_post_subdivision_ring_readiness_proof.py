# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import subprocess
import sys

from tools.run_hex_post_subdivision_ring_readiness_proof import (
    build_hex_post_subdivision_ring_readiness_proof,
)


def test_post_subdivision_ring_readiness_proof_writes_report(tmp_path):
    out_dir = tmp_path / "proof"

    report = build_hex_post_subdivision_ring_readiness_proof(out_dir=out_dir)

    assert report["ok"] is True
    assert report["blockers"] == []
    assert report["proof_checks"] == {
        "new_children_form_sibling_ring": True,
        "sibling_ring_message_delivers": True,
        "child_to_parent_to_subdivided_parent_delivers": True,
        "non_sibling_message_blocks_not_neighbor": True,
        "candidate_preserves_hierarchy_invariants": True,
        "source_topology_unchanged": True,
        "no_transport_in_delivery_summary": True,
    }

    # the new shadow children appeared under the subdivided parent
    assert report["candidate_cell_ids"] == [
        "root",
        "thermal",
        "thermal.a",
        "thermal.b",
    ]
    assert report["new_children"] == ["thermal.a", "thermal.b"]

    # RUNTIME MUTATION AUTHORITY FALSE: source byte-identical, no transport
    assert (
        report["source_topology_digest_before"]
        == report["source_topology_digest_after"]
    )
    assert report["ring_delivery_summary"]["transport_applied"] is False
    # the one non-sibling ring message blocked on the topology boundary
    assert report["ring_delivery_summary"]["blocked_by_category"] == {
        "not_neighbor": 1
    }

    proof_path = out_dir / "hex_post_subdivision_ring_readiness_proof.json"
    assert proof_path.exists()
    saved = json.loads(proof_path.read_text(encoding="utf-8"))
    assert saved == report


def test_post_subdivision_ring_readiness_proof_refuses_existing_out_dir(
    tmp_path,
):
    out_dir = tmp_path / "proof"
    out_dir.mkdir()

    cmd = [
        sys.executable,
        "tools/run_hex_post_subdivision_ring_readiness_proof.py",
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


def test_post_subdivision_ring_readiness_proof_cli_json(tmp_path):
    out_dir = tmp_path / "proof"
    cmd = [
        sys.executable,
        "tools/run_hex_post_subdivision_ring_readiness_proof.py",
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
        "wd.hex_post_subdivision_ring_readiness_proof.v0"
    )
