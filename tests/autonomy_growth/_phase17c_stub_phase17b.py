# SPDX-License-Identifier: BUSL-1.1
"""Test stub for the Phase 17B aggregator.

When the Phase 17C harness is exercised in unit tests, we don't want
to actually run the WaggleDance A-E proof tools (each takes seconds
to minutes). This stub replicates the minimal Phase 17B JSON contract
the 17C harness reads.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--skip-ollama", action="store_true")
    args, _unknown = parser.parse_known_args()

    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "phase17b_local_efficiency_benchmark.json"

    payload = {
        "phase": "phase17b_local_efficiency_benchmark",
        "benchmark_version": "phase17b.v1",
        "schema_version": 1,
        "branch": "phase17b/local-efficiency-benchmark",
        "started_at_utc": "2026-05-04T00:00:00Z",
        "finished_at_utc": "2026-05-04T00:00:01Z",
        "git_sha": "stubsha",
        "python_version": "3.13.7",
        "platform": "stub-platform",
        "docker_mode": "host",
        "tracks": {
            "A_solver_hot_path": {"raw": {"passed": True}, "metrics": {
                "claim_label": "PROVEN",
                "provider_jobs_delta": 0, "builder_jobs_delta": 0,
            }},
            "B_capability_lookup_10k": {"raw": {"passed": True}, "metrics": {
                "claim_label": "PROVEN",
                "provider_jobs_delta": 0, "builder_jobs_delta": 0,
            }},
            "C_handle_query_e2e": {"raw": {"passed": True}, "metrics": {
                "claim_label": "PROVEN",
                "provider_jobs_delta": 0, "builder_jobs_delta": 0,
            }},
            "D_restart_continuity": {"raw": {"passed": True}, "metrics": {
                "claim_label": "PROVEN",
                "provider_jobs_delta": 0, "builder_jobs_delta": 0,
            }},
            "E_producer_fabric": {"raw": {"passed": True}, "metrics": {
                "claim_label": "PROVEN",
                "provider_jobs_delta": 0, "builder_jobs_delta": 0,
            }},
        },
        "scenarios": {
            "F_ollama_baseline": {"status": "SKIPPED",
                                       "reason": "stub: --skip-ollama"},
            "G_external_competitor_slots": {
                "status": "NOT_RUN",
                "policy": "stub: external slots policy unchanged",
                "slots": [
                    {"slot": "frontier_anthropic_claude",
                     "status": "NOT_RUN",
                     "reason_not_run": "stub",
                     "requirements_to_upgrade_to_measured": []},
                ],
            },
        },
        "claim_labels": {
            "zero_provider_inner_loop": "PROVEN",
            "deterministic_routing_solver_first": "PROVEN",
            "raw_intelligence_vs_frontier_moe": "NOT_CLAIMED",
        },
        "not_claimed": ["raw_intelligence_vs_frontier_moe"],
        "summary": {
            "all_waggledance_scenarios_pass": True,
            "provider_jobs_delta_total": 0,
            "builder_jobs_delta_total": 0,
            "ollama_baseline_status": "SKIPPED",
            "external_competitor_slots_status": "NOT_RUN",
            "overall_pass": True,
        },
        "provider_jobs_delta": 0,
        "builder_jobs_delta": 0,
        "release_gate_pass": True,
        "no_consciousness_claim": True,
        "no_beats_all_competitors_claim": True,
        "no_cloud_api_calls_this_session": True,
        "no_pull_or_download_this_session": True,
        "forbidden_claims_absent": True,
        "forbidden_vocabulary_excluded": [],
    }
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True),
                            encoding="utf-8")
    md_path = out_dir / "phase17b_local_efficiency_benchmark.md"
    md_path.write_text("# stub\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
