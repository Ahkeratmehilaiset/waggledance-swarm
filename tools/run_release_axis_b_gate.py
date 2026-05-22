#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Write v3.12 Axis B hex-aligned oracle evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.run_r21_oracle_ab_proof import load_oracle_corpus, quality_arm
from waggledance.application.services.hex_topology_registry import HexTopologyRegistry


DEFAULT_OUTPUT = (
    Path("docs")
    / "runs"
    / "release_soak_evidence"
    / "v3.12.0_axis_b_hex_aligned_eval.json"
)
DEFAULT_ORACLE_DIR = Path("tests") / "oracle_hex"
DEFAULT_HEX_CONFIG = Path("configs") / "hex_cells.yaml"
EXPECTED_CELLS = {
    "hub",
    "bee_ops",
    "environment",
    "home_comfort",
    "safety_security",
    "production",
    "logistics",
}
QUALITY_FLOOR = 0.74
MISMATCHED_BASELINE_QUALITY = 0.5
MINIMUM_BASELINE_DELTA = 0.20
PER_CELL_QUALITY_FLOOR = 0.6


def build_axis_b_report(
    *,
    oracle_dir: Path = DEFAULT_ORACLE_DIR,
    hex_config: Path = DEFAULT_HEX_CONFIG,
) -> dict[str, Any]:
    registry = HexTopologyRegistry(config_path=str(hex_config), agents=[])
    oracles = load_oracle_corpus(oracle_dir)
    result = quality_arm(oracles, registry.select_origin_cell)

    cells = {oracle["cell"] for oracle in oracles}
    blockers: list[str] = []
    if len(oracles) != 7:
        blockers.append("oracle_file_count_not_7")
    if cells != EXPECTED_CELLS:
        blockers.append("oracle_cells_do_not_match_hex_registry")
    total_positive = sum(len(oracle["positive"]) for oracle in oracles)
    total_negative = sum(len(oracle["negative"]) for oracle in oracles)
    if total_positive != 105 or total_negative != 35:
        blockers.append("oracle_shape_not_105_positive_35_negative")
    quality = float(result["quality"])
    if quality < QUALITY_FLOOR:
        blockers.append("quality_below_floor")
    if quality <= MISMATCHED_BASELINE_QUALITY + MINIMUM_BASELINE_DELTA:
        blockers.append("quality_delta_below_floor")
    for row in result["per_file"]:
        if row["file_score"] < PER_CELL_QUALITY_FLOOR:
            blockers.append(f"{row['cell']}_quality_below_floor")
        if row["neg_correct"] != row["neg_total"]:
            blockers.append(f"{row['cell']}_negative_routing_not_perfect")

    return {
        "schema_version": "waggledance.axis_b_hex_eval.v1",
        "target_version": "v3.12.0",
        "benchmark_id": "v3.12-axis-b-hex-aligned-eval",
        "command": "python tools/run_release_axis_b_gate.py",
        "corpus": {
            "oracle_dir": str(oracle_dir),
            "files": len(oracles),
            "cells": sorted(cells),
            "total_positive": total_positive,
            "total_negative": total_negative,
        },
        "thresholds": {
            "quality_floor": QUALITY_FLOOR,
            "mismatched_baseline_quality": MISMATCHED_BASELINE_QUALITY,
            "minimum_baseline_delta": MINIMUM_BASELINE_DELTA,
            "per_cell_quality_floor": PER_CELL_QUALITY_FLOOR,
        },
        "quality": quality,
        "micro_pos": result["micro_pos_correct"],
        "micro_pos_total": result["micro_pos_total"],
        "micro_neg": result["micro_neg_correct"],
        "micro_neg_total": result["micro_neg_total"],
        "per_file": result["per_file"],
        "blockers": blockers,
        "result": "pass" if not blockers else "blocked",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--oracle-dir", type=Path, default=DEFAULT_ORACLE_DIR)
    parser.add_argument("--hex-config", type=Path, default=DEFAULT_HEX_CONFIG)
    args = parser.parse_args(argv)

    report = build_axis_b_report(
        oracle_dir=args.oracle_dir,
        hex_config=args.hex_config,
    )
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if report["result"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
