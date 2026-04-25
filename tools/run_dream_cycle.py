#!/usr/bin/env python3
"""Run one Dream cycle — Phase 8.5 Session C.

Pipeline (deterministic, shadow-only, no live LLM):

  1. Load pinned inputs (state.json with pinned_inputs)
  2. Build dream curriculum (reuse tools/dream_curriculum.build)
  3. Emit dream_request_pack.{json,md} per night (capped at top-nights)
  4. Ingest external proposals from --proposal / --proposal-dir
  5. Collapse proposals through Phase 8 gates
  6. Emit proposal_collapse_report.{json,md}

Runtime safety: zero touch. No port 8002. No live LLM. No runtime
mutation. Crown-jewel area is `waggledance/core/dreaming/*`.

CLI:
  python tools/run_dream_cycle.py --help
  python tools/run_dream_cycle.py --apply
  python tools/run_dream_cycle.py --apply --proposal path/to/p.json
  python tools/run_dream_cycle.py --apply --proposal-dir packs/
  python tools/run_dream_cycle.py --apply --max-proposals 5
  python tools/run_dream_cycle.py --apply --real-data-only
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.dreaming import collapse as col  # noqa: E402
from waggledance.core.dreaming import curriculum as cu  # noqa: E402
from waggledance.core.dreaming import meta_proposal as mp  # noqa: E402
from waggledance.core.dreaming import replay as rep  # noqa: E402
from waggledance.core.dreaming import request_pack as rp  # noqa: E402
from waggledance.core.dreaming import shadow_graph as sg  # noqa: E402

DEFAULT_OUT_ROOT = ROOT / "docs" / "runs" / "dream"


def _load_dream_curriculum_tool():
    path = ROOT / "tools" / "dream_curriculum.py"
    spec = importlib.util.spec_from_file_location("dream_curriculum_tool", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["dream_curriculum_tool"] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_cell_manifest(pinned_inputs: list[dict], cell_id: str) -> dict:
    suffix = f"cells/{cell_id}/manifest.json"
    for entry in pinned_inputs:
        path = entry.get("path", "")
        if path.endswith(suffix):
            sz = int(entry.get("size_bytes") or 0)
            text = (ROOT / path).read_text(encoding="utf-8")[:sz] if sz else \
                    (ROOT / path).read_text(encoding="utf-8")
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {}
    return {"cell_id": cell_id, "solver_count": None}


def _load_replay_case_manifest(pinned_inputs: list[dict]) -> dict | None:
    """Optional — Session C bootstrap may not have one yet."""
    for entry in pinned_inputs:
        if entry.get("path", "").endswith("replay_case_manifest.json"):
            try:
                return json.loads((ROOT / entry["path"]).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
    return None


def _attention_focus(self_model: dict) -> list[dict]:
    return list(self_model.get("attention_focus") or [])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-manifest", type=Path,
                    default=ROOT / "docs" / "runs" / "phase8_5_dream_session_state.json")
    ap.add_argument("--replay-manifest", type=Path, default=None,
                    help="Optional pinned replay_case_manifest.json")
    ap.add_argument("--output-dir", type=Path, default=None)
    ap.add_argument("--proposal", type=Path, default=None)
    ap.add_argument("--proposal-dir", type=Path, default=None)
    ap.add_argument("--max-proposals", type=int, default=col.DEFAULT_MAX_PROPOSALS)
    ap.add_argument("--top-nights", type=int, default=7)
    ap.add_argument("--shadow-only", type=str, default="true",
                    help="Session C ALWAYS runs shadow-only; --shadow-only=false is ignored with a warning")
    ap.add_argument("--history-path", type=Path, default=None,
                    help="Optional dream-history file (reserved for exploration_bonus)")
    ap.add_argument("--real-data-only", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if str(args.shadow_only).lower() in ("false", "0", "no"):
        print(
            "Session C ignores --shadow-only=false; runtime mutation is "
            "out of scope until a later gated session.",
        )

    if not args.input_manifest.exists():
        print(f"input manifest missing: {args.input_manifest}", file=sys.stderr)
        return 2 if args.real_data_only else 1

    dc_tool = _load_dream_curriculum_tool()
    pin_hash, pinned = dc_tool.load_pin_manifest(args.input_manifest)

    if args.real_data_only:
        for entry in pinned:
            if not (ROOT / entry.get("path", "")).exists():
                print(f"real-data-only: missing {entry.get('path')}",
                       file=sys.stderr)
                return 2

    self_model = dc_tool._load_self_model(pinned) or {}
    curiosity_log = dc_tool._load_curiosity_log(pinned)
    calibration_corrections = dc_tool._load_calibration_corrections(pinned)
    replay_case_manifest: dict | None = None
    if args.replay_manifest is not None and args.replay_manifest.exists():
        try:
            replay_case_manifest = json.loads(
                args.replay_manifest.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            replay_case_manifest = None
    if replay_case_manifest is None:
        replay_case_manifest = _load_replay_case_manifest(pinned)

    branch = dc_tool._detect_branch() or "phase8.5/dream-curriculum"
    base_commit = dc_tool._detect_base_commit() or ""

    curriculum = dc_tool.build(
        self_model=self_model,
        curiosity_log=curiosity_log,
        calibration_corrections=calibration_corrections,
        branch_name=branch,
        base_commit_hash=base_commit,
        pin_hash=pin_hash,
        top_nights=args.top_nights,
    )

    out_dir = args.output_dir or (DEFAULT_OUT_ROOT /
                                    pin_hash.replace("sha256:", "")[:12])
    if args.apply:
        out_dir.mkdir(parents=True, exist_ok=True)

    # ── 1) Emit per-night request packs ────────────────────────-
    pack_paths: list[Path] = []
    pack_sha12s: set[str] = set()
    attention = _attention_focus(self_model)
    for night in curriculum.nights:
        if night.mode == "wait" or not night.target_items:
            continue
        cell_id = (night.primary_cells[0]
                    if night.primary_cells else "general")
        cell_manifest = _load_cell_manifest(pinned, cell_id)
        pack = rp.build_request_pack(
            night=night, self_model=self_model,
            cell_manifest=cell_manifest,
            attention_focus=attention,
            replay_case_manifest=replay_case_manifest,
            branch_name=branch, base_commit_hash=base_commit,
            pinned_input_manifest_sha256=pin_hash,
        )
        pack_sha12s.add(pack.dream_request_pack_sha12)
        if args.apply:
            paths = rp.emit_pack(pack, out_dir / "request_packs")
            pack_paths.extend(paths.values())

    # ── 2) Ingest external proposals + collapse ─────────────────
    selected, truncated = col.discover_proposals(
        proposal=args.proposal, proposal_dir=args.proposal_dir,
        max_proposals=args.max_proposals,
    )
    report = col.collapse_many(
        proposals=selected, truncated=truncated,
        known_pack_sha12s=pack_sha12s,
        branch_name=branch, base_commit_hash=base_commit,
        pinned_input_manifest_sha256=pin_hash,
        replay_manifest_sha256=None,
        repo_root=ROOT, capabilities=None,
        max_proposals=args.max_proposals,
    )

    collapse_paths: dict[str, Path] = {}
    if args.apply:
        collapse_paths = col.emit_report(report, out_dir)

    # ── 3) Shadow graph + replay (structural counterfactual) ────
    accepted_proposals: list[dict] = []
    for evaluated in report.proposals_evaluated:
        if evaluated.collapse_verdict == "ACCEPT_CANDIDATE":
            try:
                proposal_data = json.loads(
                    Path(evaluated.proposal_path).read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError):
                continue
            accepted_proposals.append(proposal_data)

    live_graph = sg.build_live_graph(library_solvers=[])
    shadow = sg.add_shadow_proposals(live_graph, accepted_proposals)
    diff = sg.diff_graphs(live_graph, shadow)

    tension_ids = []
    curiosity_ids = []
    for night in curriculum.nights:
        for it in night.target_items:
            if it.source_kind == "tension":
                tension_ids.append(it.source_id)
            elif it.source_kind == "curiosity":
                curiosity_ids.append(it.source_id)
    cases = rep.select_replay_cases(replay_case_manifest,
                                     tension_ids, curiosity_ids)
    collapse_passed = any(
        e.collapse_verdict == "ACCEPT_CANDIDATE"
        for e in report.proposals_evaluated
    )
    replay_report = rep.build_report(
        cases=cases, live=live_graph, shadow=shadow, diff=diff,
        branch_name=branch, base_commit_hash=base_commit,
        pinned_input_manifest_sha256=pin_hash,
        replay_manifest_sha256=None,
        tension_ids_targeted=tension_ids,
        collapse_passed=collapse_passed,
    )
    replay_paths: dict[str, Path] = {}
    if args.apply:
        replay_paths = rep.emit_report(replay_report, out_dir)

    # ── 4) Meta-proposal (shadow-only recommendation) ──────────-
    consumed_hooks: list[dict] = []
    try:
        state = json.loads(args.input_manifest.read_text(encoding="utf-8"))
        consumed_hooks = list(state.get("consumed_hook_contracts") or [])
    except (OSError, json.JSONDecodeError):
        consumed_hooks = []
    hook_errors = mp.validate_hook_contracts(consumed_hooks, ROOT)
    if hook_errors:
        for err in hook_errors:
            print(f"hook contract error: {err}", file=sys.stderr)

    meta = mp.build_meta_proposal(
        collapse=report, replay=replay_report,
        self_model=self_model,
        consumed_hook_contracts=consumed_hooks,
    )
    meta_paths: dict[str, Path] = {}
    if meta is not None and args.apply and not hook_errors:
        meta_paths = mp.emit_meta_proposal(meta, out_dir)

    summary = {
        "pin_hash": pin_hash,
        "primary_source": curriculum.primary_source,
        "nights": len(curriculum.nights),
        "request_packs_emitted": len(pack_paths),
        "proposals_evaluated": len(report.proposals_evaluated),
        "truncated_proposals": len(report.truncated_proposals),
        "counts_by_verdict": report.counts_by_verdict,
        "replay_case_count": replay_report.replay_case_count,
        "structural_gain_count": replay_report.structural_gain_count,
        "structurally_promising": replay_report.structurally_promising,
    }
    summary["meta_proposal_emitted"] = bool(meta_paths)
    if args.apply:
        summary["out_dir"] = out_dir.as_posix()
        summary["collapse_report"] = {k: p.as_posix()
                                       for k, p in collapse_paths.items()}
        summary["replay_report"] = {k: p.as_posix()
                                     for k, p in replay_paths.items()}
        if meta_paths:
            summary["meta_proposal"] = {k: p.as_posix()
                                          for k, p in meta_paths.items()}
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print("=== dream cycle ===")
        for k, v in summary.items():
            print(f"{k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
