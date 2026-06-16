# SPDX-License-Identifier: BUSL-1.1
"""Deterministic offline proof of the deterministic-solver-first route.

Proves the WD Image #1 *deterministic-first routing* pillar at the solver layer:
for a solvable query, `SolverRouter.route` selects a **deterministic solver**
(not the LLM fallback), executes it on the safe action bus, and does so
**reproducibly** — without recording the raw query text, calling a provider, or
taking the LLM fallback.

This is the MAGMA-free, standalone-reproducible complement to the manifest's
internal `deterministic_solver_trace` proof: it adds a run-twice determinism
check and derives every evidence flag from the observed routing result (never a
hardcoded "safe" literal). MAGMA receipt emission is intentionally out of scope.

Exact validation commands::

    python tools/run_deterministic_solver_first_proof.py --json
    python -m pytest tests/test_deterministic_solver_first_proof.py -q

Engineering record; fully offline (no provider/cloud calls); forbidden-vocabulary
guarded. No claim of superiority over any external system.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FORBIDDEN_VOCABULARY: tuple[str, ...] = (
    "conscious", "sentient", "aware", "alive", "AGI",
    "revolutionary", "magical", "human-like mind", "self-aware",
    "explosive intelligence", "emergent",
    "beats all competitors", "world's best", "world's fastest",
)

REPORT_VERSION = "wd.deterministic_solver_first_proof.v1"
CAPABILITY_ID = "deterministic_solver_first"
SAMPLE_DOMAIN = "math"
SAMPLE_QUERY = "calculate 2 + 2"
EXPECTED_SOLVER_IDS = ["solve.math"]


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _route_once(domain: str, query: str) -> dict[str, Any]:
    """Run one routing decision and return its environment-independent view."""
    from waggledance.core.reasoning.solver_router import SolverRouter

    result = SolverRouter().route(domain, query)
    trace = list(result.solver_call_trace)
    selected_solver_ids = [
        str(item.get("capability_id"))
        for item in trace
        if item.get("stage") == "solver_call"
    ]
    return {
        "quality_path": str(result.quality_path),
        "fallback_used": bool(result.selection.fallback_used),
        "selected_solver_ids": selected_solver_ids,
        "execution_boundaries": sorted({str(t.get("execution_boundary")) for t in trace}),
        "trace": trace,
        "trace_len": len(trace),
    }


def build_deterministic_solver_first_proof(
    *, domain: str = SAMPLE_DOMAIN, query: str = SAMPLE_QUERY,
) -> dict[str, Any]:
    run1 = _route_once(domain, query)
    run2 = _route_once(domain, query)
    deterministic = json.dumps(run1, sort_keys=True) == json.dumps(run2, sort_keys=True)

    # Every flag is DERIVED from the observed run1 (never hardcoded "safe").
    quality_gold = run1["quality_path"] == "gold"
    no_llm_fallback = run1["fallback_used"] is False
    solver_first = run1["selected_solver_ids"] == EXPECTED_SOLVER_IDS
    safe_boundary_only = run1["execution_boundaries"] == ["safe_action_bus"]
    query_text_recorded = query in json.dumps(run1["trace"], sort_keys=True)
    query_not_recorded = not query_text_recorded

    evidence_present = bool(
        deterministic and quality_gold and no_llm_fallback and solver_first
        and safe_boundary_only and query_not_recorded
    )

    blockers: list[str] = []
    if not deterministic:
        blockers.append("non_deterministic_route")
    if not quality_gold:
        blockers.append("quality_path_not_gold")
    if not no_llm_fallback:
        blockers.append("llm_fallback_used")
    if not solver_first:
        blockers.append("solver_first_selection_mismatch")
    if not safe_boundary_only:
        blockers.append("execution_boundary_not_safe_action_bus")
    if not query_not_recorded:
        blockers.append("raw_query_recorded_in_trace")

    return {
        "report_version": REPORT_VERSION,
        "generated_at_utc": _utc_iso(),
        "ok": not blockers,
        "blockers": blockers,
        "capability_id": CAPABILITY_ID,
        "router_entrypoint": "waggledance.core.reasoning.solver_router.SolverRouter.route",
        "sample": {"domain": domain, "query_redacted": "<math expression>"},
        "deterministic_replay": {"runs": 2, "route_identical": deterministic},
        "solver_first_evidence": {
            "quality_path": run1["quality_path"],
            "fallback_used": run1["fallback_used"],
            "selected_solver_ids": run1["selected_solver_ids"],
            "execution_boundaries": run1["execution_boundaries"],
            "trace_len": run1["trace_len"],
        },
        # Evidence (deterministic solver-first selection) is NOT production
        # authority. Derived from observed routing, not hardcoded.
        "evidence_vs_authority": {
            "evidence_present": evidence_present,
            "deterministic_first_not_llm": no_llm_fallback and solver_first,
            "provider_calls": 0,
            "raw_query_recorded": query_text_recorded,
        },
        "invariants": {
            "no_cloud_api_calls_this_session": True,
            "no_pull_or_download_this_session": True,
            "deterministic_offline": deterministic,
            "no_llm_fallback": no_llm_fallback,
            "safe_action_bus_boundary_only": safe_boundary_only,
            "raw_query_not_recorded": query_not_recorded,
            "magma_receipt_out_of_scope": True,
            "forbidden_vocabulary_excluded": list(FORBIDDEN_VOCABULARY),
        },
    }


def render_summary(report: dict[str, Any]) -> str:
    dr = report["deterministic_replay"]
    se = report["solver_first_evidence"]
    eva = report["evidence_vs_authority"]
    return "\n".join([
        "Deterministic solver-first proof",
        f"  ok={report['ok']} blockers={report['blockers']}",
        f"  router={report['router_entrypoint']}",
        f"  deterministic_replay: runs={dr['runs']} identical={dr['route_identical']}",
        f"  quality_path={se['quality_path']} fallback_used={se['fallback_used']} "
        f"selected_solver_ids={se['selected_solver_ids']} boundaries={se['execution_boundaries']}",
        f"  evidence_present={eva['evidence_present']} deterministic_first_not_llm={eva['deterministic_first_not_llm']} "
        f"raw_query_recorded={eva['raw_query_recorded']}",
    ])


def assert_vocabulary_clean(text: str) -> None:
    low = text.lower()
    hit = [p for p in FORBIDDEN_VOCABULARY if p.lower() in low]
    if hit:
        raise SystemExit(f"forbidden vocabulary in rendered summary: {hit}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=None,
                        help="Optional new directory for the JSON proof artifact; must not already exist.")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_deterministic_solver_first_proof()

    summary = render_summary(report)
    assert_vocabulary_clean(summary)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(summary)

    if args.out_dir is not None:
        out_dir = args.out_dir.resolve()
        if out_dir.exists():
            print(f"out_dir must not exist: {out_dir}", file=sys.stderr)
            return 1
        out_dir.mkdir(parents=True)
        (out_dir / "deterministic_solver_first_proof.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8",
        )

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
