# SPDX-License-Identifier: Apache-2.0
"""Smoke test for the autonomy proof script."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.run_autonomy_proof import run as run_autonomy_proof  # noqa: E402


def test_autonomy_proof_runs_and_promotes_three_solvers(tmp_path: Path) -> None:
    out_dir = tmp_path / "proof_out"
    proof = run_autonomy_proof(out_dir, tmp_path / "proof.db")

    # Three families, three solvers, all accepted
    assert len(proof["gaps"]) == 3
    assert all(g["accepted"] for g in proof["gaps"])
    assert all(g["validation_pass_rate"] == 1.0 for g in proof["gaps"])
    assert all(g["shadow_agreement_rate"] == 1.0 for g in proof["gaps"])

    # Six runtime probes, all hits
    assert len(proof["dispatcher_runs"]) == 6
    assert all(d["matched"] for d in proof["dispatcher_runs"])
    assert all(d["reason"] == "hit" for d in proof["dispatcher_runs"])

    # Aggregate KPIs match what we expect
    k = proof["kpis"]
    assert k["candidates_total"] == 3
    assert k["auto_promotions_total"] == 3
    assert k["rejections_total"] == 0
    assert k["rollbacks_total"] == 0
    assert k["dispatcher_hits_total"] == 6
    assert k["dispatcher_misses_total"] == 0

    # Artifact written
    out_file = out_dir / "autonomy_proof.json"
    assert out_file.is_file()


def test_autonomy_proof_records_primary_teacher_lane(tmp_path: Path) -> None:
    proof = run_autonomy_proof(tmp_path / "out", tmp_path / "p.db")
    assert proof["primary_teacher_lane_id"] == "claude_code_builder_lane"
    assert proof["schema_version"] == 2
