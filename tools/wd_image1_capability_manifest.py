# SPDX-License-Identifier: BUSL-1.1
"""Read-only capability manifest for the WD Image #1 storyboard.

The tool deliberately separates the visual claim from repo-safe wording.
It does not mutate runtime state, bridge state, GitHub state, or tracked files.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Iterable, Sequence


ROOT = Path(__file__).resolve().parents[1]

STATUS_IMPLEMENTED = "implemented"
STATUS_PARTIAL = "partial"
STATUS_PLANNED = "planned"
STATUS_BLOCKED = "blocked"
VALID_STATUSES = {
    STATUS_IMPLEMENTED,
    STATUS_PARTIAL,
    STATUS_PLANNED,
    STATUS_BLOCKED,
}


@dataclass(frozen=True)
class Evidence:
    path: str
    present: bool
    note: str


@dataclass(frozen=True)
class Capability:
    capability_id: str
    title: str
    image_claim: str
    safe_statement: str
    status: str
    claim_safe: bool
    evidence: tuple[Evidence, ...]
    gaps: tuple[str, ...]
    next_smallest_pr: str

    def to_dict(self) -> dict:
        data = asdict(self)
        data["evidence"] = [asdict(item) for item in self.evidence]
        return data


def _exists(root: Path, rel_path: str) -> bool:
    return (root / rel_path).exists()


def _evidence(root: Path, items: Iterable[tuple[str, str]]) -> tuple[Evidence, ...]:
    return tuple(
        Evidence(path=path, present=_exists(root, path), note=note)
        for path, note in items
    )


def _all_present(items: Sequence[Evidence]) -> bool:
    return all(item.present for item in items)


def _some_present(items: Sequence[Evidence]) -> bool:
    return any(item.present for item in items)


def _status_for(
    evidence: Sequence[Evidence],
    *,
    complete: bool = False,
    planned: bool = False,
) -> str:
    if complete and _all_present(evidence):
        return STATUS_IMPLEMENTED
    if _some_present(evidence):
        return STATUS_PARTIAL
    if planned:
        return STATUS_PLANNED
    return STATUS_BLOCKED


def _capabilities(root: Path) -> tuple[Capability, ...]:
    hex_evidence = _evidence(
        root,
        (
            (
                "waggledance/core/hex_cell_topology.py",
                "8-cell solver-retrieval topology.",
            ),
            (
                "configs/hex_cells.yaml",
                "7-cell agent-routing topology source of truth.",
            ),
            (
                "docs/architecture/HEX_TOPOLOGIES.md",
                "Disambiguates the two independent hex topologies.",
            ),
        ),
    )
    solver_evidence = _evidence(
        root,
        (
            (
                "waggledance/core/reasoning/solver_router.py",
                "Solver-first reasoning route surface.",
            ),
            (
                "waggledance/core/capabilities/selector.py",
                "Capability selection backing the solver route.",
            ),
            (
                "docs/architecture/HONEYCOMB_SOLVER_SCALING.md",
                "Architecture doc records solver-first and current gaps.",
            ),
        ),
    )
    magma_evidence = _evidence(
        root,
        (
            (
                "waggledance/core/magma/event_log_adapter.py",
                "Structured event log adapter.",
            ),
            (
                "waggledance/core/magma/receipt_bundle.py",
                "MAGMA receipt bundle writer/verifier surface.",
            ),
            (
                "docs/architecture/CONTROL_PLANE_AND_DATA_PLANE.md",
                "Storage truth doc records append-only boundary.",
            ),
        ),
    )
    autogrowth_evidence = _evidence(
        root,
        (
            (
                "waggledance/core/autonomy_growth/low_risk_policy.py",
                "Low-risk family allowlist.",
            ),
            (
                "waggledance/core/autonomy_growth/runtime_query_router.py",
                "Runtime gap detector and low-risk dispatch seam.",
            ),
            (
                "waggledance/core/autonomy_growth/autogrowth_scheduler.py",
                "Queue consumer for bounded autogrowth ticks.",
            ),
        ),
    )
    hex_upgrade_evidence = _evidence(
        root,
        (
            (
                "waggledance/core/hex_topology/subdivision_operator.py",
                "Pure shadow-first subdivision planner.",
            ),
            (
                "waggledance/core/hex_topology/ring_messaging.py",
                "Pure deterministic ring delivery primitive.",
            ),
            (
                "waggledance/core/hex_topology/parent_child_relations.py",
                "Pure parent/child/sibling/neighbor relation helpers.",
            ),
        ),
    )
    future_evidence = _evidence(
        root,
        (
            (
                "docs/architecture/explosive_intelligence_growth_2.md",
                "Future scale architecture and risk register.",
            ),
            (
                "docs/architecture/HONEYCOMB_SOLVER_SCALING.md",
                "Scoreboard and non-goals for capability growth.",
            ),
        ),
    )

    return (
        Capability(
            capability_id="hex_mesh_entry",
            title="Hex-mesh query entry",
            image_claim=(
                "Every query first enters an intelligent 8-cell honeycomb "
                "topology."
            ),
            safe_statement=(
                "WD has two independent topologies: an 8-cell "
                "solver-retrieval topology and a 7-cell agent-routing "
                "topology; exact runtime entry order depends on flags and "
                "call path."
            ),
            status=_status_for(hex_evidence),
            claim_safe=False,
            evidence=hex_evidence,
            gaps=(
                "The repo explicitly documents two different hex topologies.",
                "The 7-cell hex_mesh path can be disabled by runtime config.",
                "The literal 'every query first enters' claim needs an "
                "end-to-end active-runtime proof.",
            ),
            next_smallest_pr=(
                "Add a read-only chat route proof that emits the active "
                "hex/retrieval/fallback order for current config flags."
            ),
        ),
        Capability(
            capability_id="deterministic_solver_first",
            title="Deterministic solver-first routing",
            image_claim=(
                "Queries are routed first through authoritative solvers, then "
                "specialist models, with LLM only advisory."
            ),
            safe_statement=(
                "Solver-first routing surfaces exist; full per-solver-call "
                "MAGMA trace coverage is still the next proof boundary."
            ),
            status=_status_for(solver_evidence),
            claim_safe=False,
            evidence=solver_evidence,
            gaps=(
                "Architecture docs record MAGMA coverage as goal/action/"
                "capability-level rather than per-solver-call.",
                "The image's 'full MAGMA provenance' wording should wait for "
                "trace-completeness evidence.",
            ),
            next_smallest_pr=(
                "Thread a per-solver-call trace event through SolverRouter "
                "and add a focused route test."
            ),
        ),
        Capability(
            capability_id="magma_audit_log",
            title="MAGMA audit log",
            image_claim="MAGMA is an append-only provenance trail.",
            safe_statement=(
                "MAGMA audit/provenance wrappers and receipt bundles exist; "
                "hard append-only enforcement is not yet safe to claim."
            ),
            status=_status_for(magma_evidence),
            claim_safe=False,
            evidence=magma_evidence,
            gaps=(
                "The storage truth doc says append-only is convention for "
                "some MAGMA-backed paths.",
                "A hardening pass is needed before literal append-only "
                "language is safe.",
            ),
            next_smallest_pr=(
                "Add append-only enforcement or a failing proof that clearly "
                "keeps wording at audit-wrapper level."
            ),
        ),
        Capability(
            capability_id="low_risk_autonomy_loop",
            title="Low-risk autonomy loop",
            image_claim=(
                "The system autonomously grows new low-risk solvers through "
                "gap mining without human intervention."
            ),
            safe_statement=(
                "A bounded low-risk autogrowth substrate exists with an "
                "allowlist, runtime gap seam, scheduler ticks, and proof "
                "fixtures; unrestricted runtime authority is not claimed."
            ),
            status=_status_for(autogrowth_evidence),
            claim_safe=False,
            evidence=autogrowth_evidence,
            gaps=(
                "AutogrowthScheduler is caller-driven and explicitly "
                "bounded.",
                "The low-risk allowlist is fixed; adding families requires "
                "reviewed deterministic compiler and executor support.",
            ),
            next_smallest_pr=(
                "Add a read-only proof that one gap signal can become a "
                "low-risk queued intent and a scheduler outcome in a temp DB."
            ),
        ),
        Capability(
            capability_id="hexagonal_upgrades",
            title="Hexagonal upgrades",
            image_claim=(
                "Full hexagonal upgrades implement dynamic subdivision, ring "
                "messaging, and parent-child hierarchy."
            ),
            safe_statement=(
                "Pure primitives for subdivision planning, ring messaging, "
                "and parent-child relation queries exist; runtime mutation is "
                "intentionally gated."
            ),
            status=_status_for(hex_upgrade_evidence),
            claim_safe=False,
            evidence=hex_upgrade_evidence,
            gaps=(
                "Subdivision is shadow-first and does not mutate runtime "
                "topology.",
                "Ring delivery is pure validation, not a networked runtime "
                "delivery layer.",
            ),
            next_smallest_pr=(
                "Add an integration proof that applies a subdivision plan to "
                "a temp topology and delivers valid parent/ring messages."
            ),
        ),
        Capability(
            capability_id="future_waggledance_swarm",
            title="Future WaggleDance swarm",
            image_claim=(
                "The future swarm has emergent intelligence, infinite "
                "scalability, and industrial-grade efficiency."
            ),
            safe_statement=(
                "The repo has future scale architecture and measurable axes; "
                "unlimited scalability remains a target, not a fact."
            ),
            status=(
                STATUS_PLANNED
                if _some_present(future_evidence)
                else STATUS_BLOCKED
            ),
            claim_safe=False,
            evidence=future_evidence,
            gaps=(
                "No finite software system can honestly prove infinite "
                "scalability.",
                "Future claims must be tied to measured axes such as "
                "coverage, fallback rate, latency, and audit completeness.",
            ),
            next_smallest_pr=(
                "Create a scale-axis scorecard artifact and link each future "
                "claim to a measurable proof."
            ),
        ),
    )


def build_manifest(root: Path | str = ROOT) -> dict:
    """Build the read-only Image #1 capability manifest."""

    repo_root = Path(root)
    capabilities = _capabilities(repo_root)
    counts = {status: 0 for status in sorted(VALID_STATUSES)}
    unsafe_claim_ids: list[str] = []
    for capability in capabilities:
        counts[capability.status] += 1
        if not capability.claim_safe:
            unsafe_claim_ids.append(capability.capability_id)

    return {
        "schema_version": "wd_image1_capability_manifest.v1",
        "claim_policy": (
            "Literal image claims are unsafe unless claim_safe=true; use "
            "safe_statement for docs and user-facing copy."
        ),
        "capabilities": [capability.to_dict() for capability in capabilities],
        "summary": {
            "capability_count": len(capabilities),
            "status_counts": counts,
            "unsafe_literal_claim_ids": unsafe_claim_ids,
            "all_literal_claims_safe": len(unsafe_claim_ids) == 0,
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Emit the read-only WD Image #1 capability manifest."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="Repository root to inspect. Defaults to this checkout.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON. Present for explicitness; JSON is the only output.",
    )
    parser.add_argument(
        "--strict-claims",
        action="store_true",
        help="Exit 2 when any literal image claim is not safe to repeat.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    manifest = build_manifest(args.root)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    if args.strict_claims and not manifest["summary"]["all_literal_claims_safe"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
