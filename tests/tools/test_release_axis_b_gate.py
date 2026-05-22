# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json

from tools.run_release_axis_b_gate import build_axis_b_report, main


def test_axis_b_report_passes_hex_aligned_oracle_gate() -> None:
    report = build_axis_b_report()

    assert report["schema_version"] == "waggledance.axis_b_hex_eval.v1"
    assert report["result"] == "pass"
    assert report["corpus"]["files"] == 7
    assert report["corpus"]["total_positive"] == 105
    assert report["corpus"]["total_negative"] == 35
    assert report["quality"] >= report["thresholds"]["quality_floor"]
    assert report["quality"] > (
        report["thresholds"]["mismatched_baseline_quality"]
        + report["thresholds"]["minimum_baseline_delta"]
    )
    assert all(
        row["file_score"] >= report["thresholds"]["per_cell_quality_floor"]
        for row in report["per_file"]
    )
    assert all(row["neg_correct"] == row["neg_total"] for row in report["per_file"])


def test_axis_b_cli_writes_machine_readable_evidence(tmp_path) -> None:
    output = tmp_path / "axis_b.json"

    rc = main(["--output", str(output)])

    assert rc == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["result"] == "pass"
    assert report["benchmark_id"] == "v3.12-axis-b-hex-aligned-eval"
