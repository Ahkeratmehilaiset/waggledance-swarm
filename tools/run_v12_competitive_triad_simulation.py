# SPDX-License-Identifier: BUSL-1.1
"""Build a guarded WD V12 competitive triad simulation report.

The triad simulation compares WD's locally proven evidence spine against three
abstract rival capability profiles. It deliberately does not install or execute
rival SDKs, and it never upgrades the rival pilot to consensus-grade.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.run_magma_adversarial_eval import (  # noqa: E402
    build_adversarial_eval_report,
)
from tools.run_v12_rival_local_check_matrix import (  # noqa: E402
    build_rival_local_check_matrix,
)
from tools.show_v12_proof import collect_proof  # noqa: E402
from waggledance.core.solver_synthesis.hex_cell_competition import (  # noqa: E402
    HEX_CELL_COMPETITION_AUTHORITY_STATUS,
    build_hex_cell_competition_result,
)
from waggledance.core.solver_synthesis.solver_candidate_store import (  # noqa: E402
    SolverCandidate,
)


REPORT_VERSION = "wd.v12.competitive_triad_simulation.v0"
DEFAULT_EVIDENCE_DIR = ROOT / "docs" / "benchmarks" / "rival_local_checks"

RIVAL_TRIAD_PROFILES: tuple[dict[str, Any], ...] = (
    {
        "profile_id": "governance_policy_runtime",
        "label": "Governance and policy runtime",
        "mapped_rivals": ["Microsoft AGT", "Asqav", "OpenAI Agents SDK"],
        "modeled_capabilities": [
            "policy_gate",
            "human_approval",
            "audit_event",
            "fail_closed_smoke",
            "tool_guardrails",
            "trace_spans",
        ],
        "local_evidence_status": (
            "mixed: Microsoft AGT local smoke passes; Asqav receipt headline "
            "remains cloud-dependent; OpenAI Agents SDK is public-doc only "
            "in WD evidence"
        ),
        "public_source_refs": [
            "https://learn.microsoft.com/en-us/agent-framework/overview/",
            "https://platform.openai.com/docs/guides/agents-sdk/",
            "https://openai.github.io/openai-agents-python/tracing/",
        ],
    },
    {
        "profile_id": "durable_graph_runtime",
        "label": "Durable graph and replay runtime",
        "mapped_rivals": ["LangGraph", "CrewAI Flows", "Microsoft Agent Framework"],
        "modeled_capabilities": [
            "durable_execution",
            "checkpoint_resume",
            "human_interrupt",
            "workflow_state",
        ],
        "local_evidence_status": "public-doc profile only in this repo slice",
        "public_source_refs": [
            "https://docs.langchain.com/oss/python/langgraph/durable-execution",
            "https://docs.crewai.com/en/concepts/flows",
            "https://learn.microsoft.com/en-us/agent-framework/workflows/workflows",
        ],
    },
    {
        "profile_id": "multi_agent_workflow_runtime",
        "label": "Multi-agent workflow runtime",
        "mapped_rivals": ["Google ADK", "CrewAI", "LangGraph"],
        "modeled_capabilities": [
            "multi_agent_composition",
            "shared_state",
            "sequential_workflow",
            "parallel_fanout",
        ],
        "local_evidence_status": "public-doc profile only in this repo slice",
        "public_source_refs": [
            "https://google.github.io/adk-docs/agents/multi-agents/",
            "https://docs.crewai.com/en/concepts/flows",
        ],
    },
)

SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "scenario_id": "external_effect_write_gate",
        "wd_signal": "adversarial_external_effect_gate",
        "rival_required_capabilities": [
            "policy_gate",
            "human_approval",
            "audit_event",
        ],
    },
    {
        "scenario_id": "counterfactual_replay_delta",
        "wd_signal": "a3_counterfactual_receipt_delta",
        "rival_required_capabilities": [
            "durable_execution",
            "checkpoint_resume",
            "audit_event",
        ],
    },
    {
        "scenario_id": "solver_growth_hex_competition",
        "wd_signal": "a4_solver_growth_hex_non_authority",
        "rival_required_capabilities": [
            "multi_agent_composition",
            "policy_gate",
            "checkpoint_resume",
        ],
    },
    {
        "scenario_id": "offline_adversarial_review",
        "wd_signal": "magma_adversarial_offline_corpus",
        "rival_required_capabilities": [
            "policy_gate",
            "workflow_state",
            "shared_state",
        ],
    },
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        default=DEFAULT_EVIDENCE_DIR,
        help="Rival local-check evidence directory.",
    )
    parser.add_argument(
        "--markdown-out",
        type=Path,
        default=None,
        help="Optional markdown report path to write.",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override for deterministic output.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_competitive_triad_simulation(
            evidence_dir=args.evidence_dir,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except ValueError as exc:
        print(f"competitive triad simulation FAILED: {exc}", file=sys.stderr)
        return 1

    markdown = render_markdown(report)
    if args.markdown_out is not None:
        args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_out.write_text(markdown, encoding="utf-8")

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(markdown, end="")
    return 0 if report["ok"] else 1


def build_competitive_triad_simulation(
    *,
    evidence_dir: Path = DEFAULT_EVIDENCE_DIR,
    now_utc: datetime | None = None,
    v12_proof: Mapping[str, Any] | None = None,
    rival_matrix: Mapping[str, Any] | None = None,
    adversarial_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    generated_at = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if v12_proof is None:
        v12_proof = collect_proof(repo_root=ROOT, since_days=1)
    if adversarial_report is None:
        adversarial_report = build_adversarial_eval_report()
    if rival_matrix is None:
        rival_matrix = build_rival_local_check_matrix(
            evidence_dir=evidence_dir,
            now_utc=generated_at,
        )

    hex_probe = _build_hex_competition_probe()
    wd_signals = _wd_signals(
        v12_proof=v12_proof,
        rival_matrix=rival_matrix,
        adversarial_report=adversarial_report,
        hex_probe=hex_probe,
    )
    scenario_results = [
        _simulate_scenario(scenario, wd_signals)
        for scenario in SCENARIOS
    ]
    blockers = _blockers(
        v12_proof=v12_proof,
        rival_matrix=rival_matrix,
        adversarial_report=adversarial_report,
        hex_probe=hex_probe,
    )

    wd_local_only = [
        result["scenario_id"]
        for result in scenario_results
        if result["wd_status"] in {"proven", "measured"}
        and all(
            profile["status"] != "local_evidence_passed"
            for profile in result["rival_profile_results"]
        )
    ]

    return {
        "report_version": REPORT_VERSION,
        "generated_at_utc": _format_utc(generated_at),
        "ok": not blockers,
        "blockers": blockers,
        "strategy_identity": "verifiable_solver_growth_substrate",
        "triad_profiles": list(RIVAL_TRIAD_PROFILES),
        "scenario_count": len(scenario_results),
        "scenarios": scenario_results,
        "wd_signals": wd_signals,
        "hex_cell_probe": hex_probe,
        "rival_matrix_summary": {
            "passed_count": rival_matrix.get("passed_count"),
            "required_count": rival_matrix.get("required_count"),
            "blocked_count": rival_matrix.get("blocked_count"),
            "consensus_grade": rival_matrix.get("consensus_grade"),
            "rival_local_checks_status": rival_matrix.get(
                "rival_local_checks_status"
            ),
        },
        "wd_local_evidence_only_scenarios": wd_local_only,
        "recommended_100h_sequence": [
            "Keep consensus_grade=false until all four rival local manifests pass.",
            "Convert JamJet and Preloop from not_passed to pinned local_smoke or keep blocked.",
            "Add receipt-bound counterfactual replay for stored consensus artifacts.",
            "Promote hex-cell competition from non-authority evidence to operator-gated authority in a separate PR.",
            "Add performance measurements only after the evidence/authority boundary is sealed.",
        ],
        "no_overclaim_guardrails": {
            "not_a_competitor_benchmark": True,
            "does_not_execute_untrusted_rival_code": True,
            "does_not_install_rival_sdks": True,
            "does_not_rank_rivals": True,
            "does_not_claim_frontier_model_superiority": True,
            "keeps_consensus_grade_false": (
                rival_matrix.get("consensus_grade") is False
            ),
            "hex_competition_non_authority": (
                hex_probe["authority_status"]
                == HEX_CELL_COMPETITION_AUTHORITY_STATUS
            ),
        },
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# WD V12 Competitive Triad Simulation",
        "",
        f"- report_version: `{report['report_version']}`",
        f"- generated_at_utc: `{report['generated_at_utc']}`",
        f"- ok: `{str(report['ok']).lower()}`",
        f"- strategy_identity: `{report['strategy_identity']}`",
        f"- rival local checks: `{report['rival_matrix_summary']['rival_local_checks_status']}`",
        f"- consensus_grade: `{str(report['rival_matrix_summary']['consensus_grade']).lower()}`",
        "",
        "## Current WD Evidence",
        "",
    ]
    for key, value in report["wd_signals"].items():
        lines.append(f"- `{key}`: `{value}`")

    lines.extend([
        "",
        "## Triad Profiles",
        "",
    ])
    for profile in report["triad_profiles"]:
        rivals = ", ".join(profile["mapped_rivals"])
        caps = ", ".join(profile["modeled_capabilities"])
        lines.append(f"- `{profile['profile_id']}`: {profile['label']} ({rivals})")
        lines.append(f"  capabilities: `{caps}`")
        lines.append(f"  local_evidence_status: `{profile['local_evidence_status']}`")

    lines.extend([
        "",
        "## Scenario Results",
        "",
        "| Scenario | WD status | Rival profile summary |",
        "|---|---|---|",
    ])
    for scenario in report["scenarios"]:
        profile_summary = "; ".join(
            f"{row['profile_id']}={row['status']}"
            for row in scenario["rival_profile_results"]
        )
        lines.append(
            f"| `{scenario['scenario_id']}` | `{scenario['wd_status']}` | "
            f"`{profile_summary}` |"
        )

    lines.extend([
        "",
        "## Hex-Cell Probe",
        "",
        f"- authority_status: `{report['hex_cell_probe']['authority_status']}`",
        f"- winner_id: `{report['hex_cell_probe']['winner_id']}`",
        f"- candidate_count: `{report['hex_cell_probe']['candidate_count']}`",
        f"- runtime_traffic_mutation_applied: `{str(report['hex_cell_probe']['runtime_traffic_mutation_applied']).lower()}`",
        f"- candidate_state_mutation_applied: `{str(report['hex_cell_probe']['candidate_state_mutation_applied']).lower()}`",
        "",
        "## Next 100h Sequence",
        "",
    ])
    for item in report["recommended_100h_sequence"]:
        lines.append(f"- {item}")

    lines.extend([
        "",
        "## Guardrails",
        "",
    ])
    for key, value in report["no_overclaim_guardrails"].items():
        lines.append(f"- `{key}`: `{str(value).lower()}`")

    lines.extend([
        "",
        "This report is a local evidence simulation, not a live rival benchmark.",
        "It proves what WD can currently demonstrate and which rival-local",
        "checks remain blocked before any stronger comparison claim.",
        "",
    ])
    return "\n".join(lines)


def _simulate_scenario(
    scenario: Mapping[str, Any],
    wd_signals: Mapping[str, Any],
) -> dict[str, Any]:
    required = set(scenario["rival_required_capabilities"])
    profile_results = []
    for profile in RIVAL_TRIAD_PROFILES:
        present = set(profile["modeled_capabilities"])
        missing = sorted(required - present)
        if missing:
            status = "capability_gap"
        elif "public-doc profile only" in profile["local_evidence_status"]:
            status = "profile_only_not_local_proof"
        elif "cloud-dependent" in profile["local_evidence_status"]:
            status = "mixed_or_cloud_dependent"
        else:
            status = "local_evidence_passed"
        profile_results.append(
            {
                "profile_id": profile["profile_id"],
                "status": status,
                "missing_capabilities": missing,
            }
        )

    return {
        "scenario_id": scenario["scenario_id"],
        "wd_signal": scenario["wd_signal"],
        "wd_status": _wd_status_for_signal(scenario["wd_signal"], wd_signals),
        "rival_required_capabilities": sorted(required),
        "rival_profile_results": profile_results,
    }


def _wd_status_for_signal(signal: str, wd_signals: Mapping[str, Any]) -> str:
    if signal == "adversarial_external_effect_gate":
        return "proven" if wd_signals["adversarial_external_effect_cases"] else "blocked"
    if signal == "a3_counterfactual_receipt_delta":
        return "measured" if wd_signals["a3_counterfactual_receipt_delta"] else "blocked"
    if signal == "a4_solver_growth_hex_non_authority":
        return "measured" if wd_signals["a4_solver_growth_hex_non_authority"] else "blocked"
    if signal == "magma_adversarial_offline_corpus":
        return "proven" if wd_signals["adversarial_full_pass"] else "blocked"
    return "unknown"


def _wd_signals(
    *,
    v12_proof: Mapping[str, Any],
    rival_matrix: Mapping[str, Any],
    adversarial_report: Mapping[str, Any],
    hex_probe: Mapping[str, Any],
) -> dict[str, Any]:
    a3 = v12_proof.get("a3_counterfactual_axis", {})
    a4 = v12_proof.get("a4_solver_growth_axis", {})
    adoption = v12_proof.get("adoption", {})
    coverage = adversarial_report.get("coverage", {})
    risk_counts = coverage.get("risk_class_counts", {})
    return {
        "v12_proof_ok": bool(v12_proof.get("ok")),
        "adversarial_full_pass": bool(
            adversarial_report.get("ok")
            and adversarial_report.get("fail_count") == 0
            and adversarial_report.get("pass_count")
            == adversarial_report.get("case_count")
        ),
        "adversarial_case_count": adversarial_report.get("case_count"),
        "adversarial_external_effect_cases": int(
            risk_counts.get("external_effect", 0) or 0
        ),
        "receipt_high_criticality_gap_count": adoption.get(
            "high_criticality_gap_count"
        ),
        "receipt_status_counts": adoption.get("status_counts"),
        "a3_counterfactual_receipt_delta": bool(
            a3.get("counterfactual_delta_proven")
            and a3.get("receipt_chain_verified")
        ),
        "a3_claim_label": a3.get("claim_label"),
        "a4_solver_growth_proven": bool(
            a4.get("solver_growth_proven")
            and a4.get("receipt_chain_verified")
        ),
        "a4_claim_label": a4.get("claim_label"),
        "a4_solver_growth_hex_non_authority": bool(
            a4.get("solver_growth_proven")
            and hex_probe["authority_status"]
            == HEX_CELL_COMPETITION_AUTHORITY_STATUS
            and not hex_probe["runtime_traffic_mutation_applied"]
            and not hex_probe["candidate_state_mutation_applied"]
        ),
        "rival_consensus_grade": rival_matrix.get("consensus_grade"),
        "rival_local_checks_status": rival_matrix.get(
            "rival_local_checks_status"
        ),
    }


def _blockers(
    *,
    v12_proof: Mapping[str, Any],
    rival_matrix: Mapping[str, Any],
    adversarial_report: Mapping[str, Any],
    hex_probe: Mapping[str, Any],
) -> list[str]:
    blockers: list[str] = []
    if v12_proof.get("ok") is not True:
        blockers.append("v12_proof_not_ok")
    if adversarial_report.get("ok") is not True:
        blockers.append("adversarial_eval_not_ok")
    if rival_matrix.get("consensus_grade") is not False:
        blockers.append("rival_consensus_grade_must_remain_false")
    if hex_probe["authority_status"] != HEX_CELL_COMPETITION_AUTHORITY_STATUS:
        blockers.append("hex_competition_authority_status_drift")
    if hex_probe["runtime_traffic_mutation_applied"]:
        blockers.append("hex_competition_runtime_mutation_applied")
    if hex_probe["candidate_state_mutation_applied"]:
        blockers.append("hex_competition_candidate_state_mutation_applied")
    return blockers


def _build_hex_competition_probe() -> dict[str, Any]:
    capability_id = "competitive-evidence-refresh"
    candidates = [
        _candidate("triad-cand-a", "evidence_policy_gate", capability_id),
        _candidate("triad-cand-b", "receipt_counterfactual_gate", capability_id),
        _candidate("triad-cand-c", "hex_promotion_gate", capability_id),
    ]
    scores = {
        "triad-cand-a": 0.74,
        "triad-cand-b": 0.91,
        "triad-cand-c": 0.86,
    }
    result = build_hex_cell_competition_result(
        candidates=candidates,
        capability_id=capability_id,
        scores=scores,
        evidence_refs={
            "triad-cand-a": ["tools/run_v12_rival_local_check_matrix.py"],
            "triad-cand-b": ["tools/show_v12_proof.py"],
            "triad-cand-c": [
                "waggledance/core/solver_synthesis/hex_cell_competition.py"
            ],
        },
    )
    return {
        "candidate_count": len(candidates),
        "competition_id": result.competition_id,
        "winner_id": result.winner_id,
        "loser_ids": list(result.loser_ids),
        "authority_status": result.authority_status,
        "runtime_traffic_mutation_applied": (
            result.runtime_traffic_mutation_applied
        ),
        "candidate_state_mutation_applied": (
            result.candidate_state_mutation_applied
        ),
        "operator_gate_required_for_authority": (
            result.operator_gate_required_for_authority
        ),
        "evidence_digest": result.evidence_digest,
    }


def _candidate(
    candidate_id: str,
    solver_name: str,
    capability_id: str,
) -> SolverCandidate:
    return SolverCandidate(
        schema_version=1,
        candidate_id=candidate_id,
        state="shadow_only",
        solver_name=solver_name,
        cell_id="competitive-intelligence",
        spec_or_code={
            "kind": "triad_simulation_profile",
            "capability_id": capability_id,
        },
        source_gap_ref="gap:v12-competitive-triad-simulation",
        no_runtime_mutation=True,
        produced_by="tools/run_v12_competitive_triad_simulation.py",
        branch_name="local/competitive-triad-simulation",
        base_commit_hash="not-mutating",
        pinned_input_manifest_sha256="sha256:local-simulation",
        match_confidence=0.7,
    )


def _format_utc(value: datetime) -> str:
    return (
        value.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _parse_utc(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("--now requires a UTC timestamp with Z or +00:00 suffix")
    return parsed.astimezone(timezone.utc)


if __name__ == "__main__":
    raise SystemExit(main())
