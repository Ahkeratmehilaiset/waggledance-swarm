"""Proposal ingestion and deterministic collapse — Phase 8.5 Session
C, deliverable C.4 (proposal ingestion side).

This module ingests externally-generated solver proposals and collapses
them through the Phase 8 proposal gate logic. It NEVER calls a live
LLM; it consumes proposal files supplied externally.

Verdict handling matrix (c.txt §C4):
- ACCEPT_CANDIDATE → demoted to shadow-only within this session
- REJECT_HARD (e.g. schema/cell error) → log and skip
- REJECT_SOFT (gate failure) → log and skip
- ABSTAIN/inconclusive → treated as REJECT_SOFT

CRITICAL SHADOW RULE (c.txt §C4):
Even when the underlying proposal gate returns ACCEPT_CANDIDATE,
Session C must demote the proposal to shadow-only. No proposal is live
in Session C.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import COLLAPSE_VERDICTS, DREAMING_SCHEMA_VERSION


# ── Underlying gate access ────────────────────────────────────────

def _load_propose_solver_module(repo_root: Path):
    """Lazy-load tools/propose_solver.py without importing it as a
    package (it is intentionally a CLI script)."""
    path = repo_root / "tools" / "propose_solver.py"
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location(
        "propose_solver_for_dream", path,
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["propose_solver_for_dream"] = mod
    spec.loader.exec_module(mod)
    return mod


# Map propose_solver verdicts → Session C collapse verdicts
_HARD_REJECTS = {"REJECT_SCHEMA", "REJECT_DUPLICATE", "REJECT_CONTRADICTION"}
_SOFT_REJECTS = {"REJECT_LOW_VALUE", "ABSTAIN", "INCONCLUSIVE"}


def _to_collapse_verdict(raw: str) -> str:
    if raw == "ACCEPT_CANDIDATE":
        return "ACCEPT_CANDIDATE"
    if raw in _HARD_REJECTS:
        return "REJECT_HARD"
    if raw in _SOFT_REJECTS:
        return "REJECT_SOFT"
    if raw.startswith("ACCEPT_"):
        return "ACCEPT_CANDIDATE"
    return "REJECT_SOFT"


# ── Sidecar / linkage rules ──────────────────────────────────────-

SIDECAR_SCHEMA_REQUIRED = (
    "responding_to_dream_request_pack_sha12",
    "generation_method",
)


def sidecar_path_for(proposal_path: Path) -> Path:
    return proposal_path.parent / f"{proposal_path.name}.dream_metadata.json"


def load_sidecar(proposal_path: Path) -> dict | None:
    sp = sidecar_path_for(proposal_path)
    if not sp.exists():
        return None
    try:
        return json.loads(sp.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def validate_sidecar(sidecar: dict) -> tuple[bool, list[str]]:
    errors: list[str] = []
    for field_name in SIDECAR_SCHEMA_REQUIRED:
        if field_name not in sidecar:
            errors.append(f"sidecar missing required field: {field_name}")
    method = sidecar.get("generation_method")
    if method is not None and method not in ("manual", "llm", "unknown"):
        errors.append(
            f"generation_method must be one of manual/llm/unknown, got {method!r}",
        )
    return (not errors, errors)


def linkage_for_proposal(
    proposal: dict,
    proposal_path: Path,
    known_pack_sha12s: set[str],
) -> tuple[bool, str | None, str]:
    """Return (linked, pack_sha12, source).

    source ∈ {"sidecar", "inline", "missing"}. If the proposal schema
    forbids extra fields, inline linkage is impossible and the sidecar
    must be present.
    """
    sidecar = load_sidecar(proposal_path)
    if sidecar is not None:
        ok, _ = validate_sidecar(sidecar)
        sha = sidecar.get("responding_to_dream_request_pack_sha12")
        if ok and sha and sha in known_pack_sha12s:
            return True, sha, "sidecar"
        if ok and sha:
            return False, sha, "sidecar"
        return False, sha, "sidecar"
    # No sidecar — try inline linkage if the proposal carries the field
    inline = proposal.get("responding_to_dream_request_pack_sha12")
    if inline and inline in known_pack_sha12s:
        return True, inline, "inline"
    return False, None, "missing"


# ── Per-proposal collapse ────────────────────────────────────────-

@dataclass(frozen=True)
class CollapsedProposal:
    proposal_path: str
    proposal_id: str | None
    solver_name: str | None
    cell_id: str | None
    raw_verdict: str
    collapse_verdict: str        # one of COLLAPSE_VERDICTS
    shadow_only: bool             # always True for ACCEPT_CANDIDATE in Session C
    linkage_source: str           # "sidecar" | "inline" | "missing"
    linkage_pack_sha12: str | None
    gate_provenance: dict[str, str]   # gate_name → native|shadow_proxy|unavailable
    gate_results: list[dict]
    notes: tuple[str, ...]


@dataclass(frozen=True)
class CollapseReport:
    schema_version: int
    branch_name: str
    base_commit_hash: str
    pinned_input_manifest_sha256: str
    replay_manifest_sha256: str | None
    max_proposals_effective: int
    proposals_evaluated: tuple[CollapsedProposal, ...]
    truncated_proposals: tuple[str, ...]
    counts_by_verdict: dict[str, int]
    gate_provenance_summary: dict[str, str]


# ── Discovery / ordering ─────────────────────────────────────────-

DEFAULT_MAX_PROPOSALS = 3
HARD_MAX_PROPOSALS = 10


def discover_proposals(
    proposal: Path | None,
    proposal_dir: Path | None,
    max_proposals: int = DEFAULT_MAX_PROPOSALS,
) -> tuple[list[Path], list[str]]:
    """Return (selected, truncated). Lexicographic ordering inside dirs."""
    if max_proposals < 1 or max_proposals > HARD_MAX_PROPOSALS:
        raise ValueError(
            f"max_proposals must be in [1, {HARD_MAX_PROPOSALS}], got {max_proposals}",
        )
    paths: list[Path] = []
    if proposal is not None:
        paths.append(proposal)
    if proposal_dir is not None:
        # Skip sidecar files
        candidates = sorted(
            p for p in proposal_dir.glob("*.json")
            if not p.name.endswith(".dream_metadata.json")
        )
        paths.extend(candidates)
    selected = paths[:max_proposals]
    truncated = [p.as_posix() for p in paths[max_proposals:]]
    return selected, truncated


# ── Collapse driver ──────────────────────────────────────────────-

def collapse_one(
    proposal_path: Path,
    known_pack_sha12s: set[str],
    repo_root: Path,
    capabilities: dict[str, str] | None = None,
) -> CollapsedProposal:
    """Run gates + linkage + verdict mapping on one proposal."""
    capabilities = capabilities or {}
    notes: list[str] = []
    proposal: dict = {}
    raw_verdict = "REJECT_SCHEMA"
    gate_results: list[dict] = []
    try:
        proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        notes.append(f"failed to read proposal: {exc}")
        return CollapsedProposal(
            proposal_path=proposal_path.as_posix(),
            proposal_id=None, solver_name=None, cell_id=None,
            raw_verdict="REJECT_SCHEMA",
            collapse_verdict="REJECT_HARD",
            shadow_only=True,
            linkage_source="missing", linkage_pack_sha12=None,
            gate_provenance={}, gate_results=[],
            notes=tuple(notes),
        )

    # Linkage check first — orphan proposals are rejected outright
    linked, pack_sha, source = linkage_for_proposal(
        proposal, proposal_path, known_pack_sha12s,
    )
    if not linked:
        notes.append(
            "orphan proposal — no matching dream_request_pack reference"
            f" (source={source}, sha={pack_sha})",
        )
        return CollapsedProposal(
            proposal_path=proposal_path.as_posix(),
            proposal_id=proposal.get("proposal_id"),
            solver_name=proposal.get("solver_name"),
            cell_id=proposal.get("cell_id"),
            raw_verdict="REJECT_SCHEMA",
            collapse_verdict="REJECT_HARD",
            shadow_only=True,
            linkage_source=source, linkage_pack_sha12=pack_sha,
            gate_provenance={}, gate_results=[],
            notes=tuple(notes),
        )

    mod = _load_propose_solver_module(repo_root)
    if mod is None:
        notes.append("propose_solver module unavailable — cannot collapse")
        return CollapsedProposal(
            proposal_path=proposal_path.as_posix(),
            proposal_id=proposal.get("proposal_id"),
            solver_name=proposal.get("solver_name"),
            cell_id=proposal.get("cell_id"),
            raw_verdict="REJECT_SCHEMA",
            collapse_verdict="REJECT_SOFT",
            shadow_only=True,
            linkage_source=source, linkage_pack_sha12=pack_sha,
            gate_provenance={}, gate_results=[],
            notes=tuple(notes),
        )

    result = mod.evaluate_proposal(proposal)
    raw_verdict = result["verdict"]
    gate_results = list(result.get("gates") or [])
    collapse_verdict = _to_collapse_verdict(raw_verdict)

    # CRITICAL SHADOW RULE: even ACCEPT_CANDIDATE is demoted to
    # shadow-only in Session C
    shadow_only = True

    # Gate provenance — default to "native" since we used the in-repo
    # propose_solver, but the caller can override via capabilities.
    provenance = {g["gate"]: capabilities.get(g["gate"], "native")
                   for g in gate_results}

    return CollapsedProposal(
        proposal_path=proposal_path.as_posix(),
        proposal_id=proposal.get("proposal_id"),
        solver_name=proposal.get("solver_name"),
        cell_id=proposal.get("cell_id"),
        raw_verdict=raw_verdict,
        collapse_verdict=collapse_verdict,
        shadow_only=shadow_only,
        linkage_source=source,
        linkage_pack_sha12=pack_sha,
        gate_provenance=provenance,
        gate_results=gate_results,
        notes=tuple(notes),
    )


def collapse_many(
    proposals: list[Path],
    truncated: list[str],
    known_pack_sha12s: set[str],
    branch_name: str,
    base_commit_hash: str,
    pinned_input_manifest_sha256: str,
    replay_manifest_sha256: str | None,
    repo_root: Path,
    capabilities: dict[str, str] | None = None,
    max_proposals: int = DEFAULT_MAX_PROPOSALS,
) -> CollapseReport:
    evaluated: list[CollapsedProposal] = []
    for p in proposals:
        evaluated.append(collapse_one(
            p, known_pack_sha12s, repo_root, capabilities,
        ))

    counts: dict[str, int] = {v: 0 for v in COLLAPSE_VERDICTS}
    for c in evaluated:
        counts[c.collapse_verdict] = counts.get(c.collapse_verdict, 0) + 1

    summary: dict[str, str] = {}
    for c in evaluated:
        for gate, prov in c.gate_provenance.items():
            # Worst-case wins: unavailable > shadow_proxy > native
            order = {"native": 0, "shadow_proxy": 1, "unavailable": 2}
            cur = summary.get(gate, "native")
            if order.get(prov, 0) > order.get(cur, 0):
                summary[gate] = prov
            elif gate not in summary:
                summary[gate] = prov

    return CollapseReport(
        schema_version=DREAMING_SCHEMA_VERSION,
        branch_name=branch_name,
        base_commit_hash=base_commit_hash,
        pinned_input_manifest_sha256=pinned_input_manifest_sha256,
        replay_manifest_sha256=replay_manifest_sha256,
        max_proposals_effective=max_proposals,
        proposals_evaluated=tuple(evaluated),
        truncated_proposals=tuple(truncated),
        counts_by_verdict=dict(sorted(counts.items())),
        gate_provenance_summary=dict(sorted(summary.items())),
    )


# ── Emission ─────────────────────────────────────────────────────-

def report_to_dict(r: CollapseReport) -> dict:
    return {
        "schema_version": r.schema_version,
        "continuity_anchor": {
            "branch_name": r.branch_name,
            "base_commit_hash": r.base_commit_hash,
            "pinned_input_manifest_sha256": r.pinned_input_manifest_sha256,
            "replay_manifest_sha256": r.replay_manifest_sha256,
        },
        "max_proposals_effective": r.max_proposals_effective,
        "counts_by_verdict": dict(sorted(r.counts_by_verdict.items())),
        "gate_provenance_summary": dict(sorted(r.gate_provenance_summary.items())),
        "truncated_proposals": list(r.truncated_proposals),
        "proposals_evaluated": [
            {
                "proposal_path": c.proposal_path,
                "proposal_id": c.proposal_id,
                "solver_name": c.solver_name,
                "cell_id": c.cell_id,
                "raw_verdict": c.raw_verdict,
                "collapse_verdict": c.collapse_verdict,
                "shadow_only": c.shadow_only,
                "linkage_source": c.linkage_source,
                "linkage_pack_sha12": c.linkage_pack_sha12,
                "gate_provenance": dict(sorted(c.gate_provenance.items())),
                "gate_results": c.gate_results,
                "notes": list(c.notes),
            }
            for c in r.proposals_evaluated
        ],
    }


def render_report_md(d: dict) -> str:
    lines = [
        "# Proposal collapse report",
        "",
        f"- **Schema version:** {d['schema_version']}",
        f"- **Branch:** `{d['continuity_anchor']['branch_name']}`",
        f"- **Base commit:** `{d['continuity_anchor']['base_commit_hash']}`",
        f"- **Pin manifest:** `{d['continuity_anchor']['pinned_input_manifest_sha256']}`",
        f"- **Replay manifest:** `{d['continuity_anchor']['replay_manifest_sha256'] or '(none)'}`",
        f"- **Max proposals (effective):** {d['max_proposals_effective']}",
        f"- **Counts by verdict:** {d['counts_by_verdict']}",
        f"- **Gate provenance summary:** {d['gate_provenance_summary']}",
        "",
        "## Proposals evaluated",
        "",
        "| path | id | cell | raw | collapse | shadow | linkage |",
        "|---|---|---|---|---|---|---|",
    ]
    for c in d["proposals_evaluated"]:
        lines.append(
            f"| `{Path(c['proposal_path']).name}` | `{c['proposal_id']}` | "
            f"`{c['cell_id']}` | {c['raw_verdict']} | "
            f"**{c['collapse_verdict']}** | {c['shadow_only']} | "
            f"{c['linkage_source']} |"
        )
    if d["truncated_proposals"]:
        lines.extend(["", "## Truncated Proposals (Not Evaluated)", ""])
        for p in d["truncated_proposals"]:
            lines.append(f"- `{p}`")
    lines.append("")
    return "\n".join(lines)


def emit_report(r: CollapseReport, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    d = report_to_dict(r)
    json_path = out_dir / "proposal_collapse_report.json"
    md_path = out_dir / "proposal_collapse_report.md"
    json_path.write_text(
        json.dumps(d, indent=2, sort_keys=True), encoding="utf-8",
    )
    md_path.write_text(render_report_md(d), encoding="utf-8")
    return {"collapse_json": json_path, "collapse_md": md_path}
