# SPDX-License-Identifier: Apache-2.0
"""Schema smoke tests for the R22 2D branch-isolation benchmark."""

from __future__ import annotations

from pathlib import Path

import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.run_branch_isolation_benchmark import (  # noqa: E402
    BenchmarkConfig,
    run_benchmark,
)


def test_branch_isolation_benchmark_emits_expected_profiles(tmp_path: Path) -> None:
    out_json = tmp_path / "branch_isolation.json"
    result = run_benchmark(
        BenchmarkConfig(
            db_path=tmp_path / "branch_isolation.sqlite",
            out_json=out_json,
            repeats=1,
            probe_events=3,
            hot_events=8,
            uniform_events_per_branch=2,
            cold_flood_events_per_branch=3,
            probe_branch="hub",
            hot_branch="bee_ops",
        )
    )

    assert out_json.exists()
    assert result["purpose"] == "2d_branch_isolation_baseline"
    assert result["topology"]["model"] == "2d_axial_hex"
    assert result["database"]["mode"] == "single_global_control_plane_db"
    assert set(result["profiles"]) == {
        "idle_probe",
        "single_hot_interference",
        "uniform_multi_branch",
        "adversarial_cold_flood",
    }
    assert result["summary"]["branch_touch_count_hit_case_target"] == 1.0
    assert result["summary"]["idle_probe_p99_ms_mean"] >= 0.0
    assert result["summary"]["single_hot_degradation_ratio"] >= 0.0
    assert "hub" in result["topology"]["branch_ids"]
    assert "bee_ops" in result["topology"]["branch_ids"]
