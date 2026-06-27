# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import subprocess
import sys

from tools.run_hex_ring_delivery_observability_proof import (
    build_hex_ring_delivery_observability_proof,
)


def test_ring_observability_proof_writes_report(tmp_path):
    out_dir = tmp_path / "proof"

    report = build_hex_ring_delivery_observability_proof(out_dir=out_dir)

    assert report["ok"] is True
    assert report["blockers"] == []
    assert report["proof_checks"] == {
        "healthy_ring_fully_delivers": True,
        "topology_fragmenting_blocks_topology_class": True,
        "schema_malformed_blocks_schema_class": True,
        "topology_vs_schema_distinguishable": True,
        "blocked_category_totals_reconcile": True,
        "by_message_kind_accounting_consistent": True,
        "no_transport_in_any_summary": True,
    }

    # the two failure modes are distinguishable at a glance by blocked_by_class
    frag = report["topology_fragmenting_summary"]
    malformed = report["schema_malformed_summary"]
    assert frag["blocked_by_class"] == {"topology_boundary": frag["total"]}
    assert malformed["blocked_by_class"] == {"schema_invalid": malformed["total"]}
    assert "topology_boundary" not in malformed["blocked_by_class"]
    assert "schema_invalid" not in frag["blocked_by_class"]

    # observability-only: no summary ever transports
    for key in (
        "healthy_summary",
        "topology_fragmenting_summary",
        "schema_malformed_summary",
    ):
        assert report[key]["transport_applied"] is False

    proof_path = out_dir / "hex_ring_delivery_observability_proof.json"
    assert proof_path.exists()
    saved = json.loads(proof_path.read_text(encoding="utf-8"))
    assert saved == report


def test_ring_observability_proof_refuses_existing_out_dir(tmp_path):
    out_dir = tmp_path / "proof"
    out_dir.mkdir()

    cmd = [
        sys.executable,
        "tools/run_hex_ring_delivery_observability_proof.py",
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


def test_ring_observability_proof_cli_json(tmp_path):
    out_dir = tmp_path / "proof"
    cmd = [
        sys.executable,
        "tools/run_hex_ring_delivery_observability_proof.py",
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
        "wd.hex_ring_delivery_observability_proof.v0"
    )
