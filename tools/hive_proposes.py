#!/usr/bin/env python3
"""hive_proposes — bounded self-proposal pass (Phase 8.5 Session D, D3).

Consumes pinned upstream session outputs (Session A curiosity,
Session B self-model, Session C dream meta-proposals, optional R7.5
resilience) and emits a deterministic review handoff:
- hive_proposals.{json,md}
- meta_evidence_map.json
- review_bundle.{json,md}
- HISTORY.jsonl entry per proposal

Runtime safety: zero touch. No port 8002. No live LLM. No runtime
mutation. Crown-jewel area is `waggledance/core/meta/*`.

CLI:
  python tools/hive_proposes.py --help
  python tools/hive_proposes.py                       # dry-run
  python tools/hive_proposes.py --apply               # write
  python tools/hive_proposes.py --input-manifest PATH
  python tools/hive_proposes.py --apply --real-data-only
  python tools/hive_proposes.py --apply --cell thermal
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.meta import (  # noqa: E402
    HUMAN_REVIEW_BOUNDARY_TEXT,
    history as hist,
    inputs as mi,
    meta_learner as ml,
    review_bundle as rb,
)

DEFAULT_OUT_ROOT = ROOT / "docs" / "runs" / "hive"
DEFAULT_HISTORY_PATH = ROOT / "docs" / "runs" / "hive" / "HISTORY.jsonl"
DEFAULT_STATE_PATH = ROOT / "docs" / "runs" / "phase8_5_hive_session_state.json"


def _detect_branch() -> str:
    head = ROOT / ".git" / "HEAD"
    if not head.exists():
        return ""
    try:
        text = head.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if text.startswith("ref: "):
        return text[5:].strip().rsplit("/", 1)[-1]
    return ""


def _detect_base_commit() -> str:
    head = ROOT / ".git" / "HEAD"
    if not head.exists():
        return ""
    try:
        text = head.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if text.startswith("ref: "):
        ref_path = ROOT / ".git" / text[5:].strip()
        if ref_path.exists():
            try:
                return ref_path.read_text(encoding="utf-8").strip()
            except OSError:
                return ""
        return ""
    return text or ""


def _default_out_dir(pin_hash: str) -> Path:
    sha12 = pin_hash.replace("sha256:", "")[:12]
    return DEFAULT_OUT_ROOT / sha12


def _detect_fixture_fallback(state: dict) -> dict:
    return state.get("fixture_fallback_status") or {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-manifest", type=Path, default=DEFAULT_STATE_PATH)
    ap.add_argument("--output-dir", type=Path, default=None)
    ap.add_argument("--history-path", type=Path, default=None)
    ap.add_argument("--real-data-only", action="store_true")
    ap.add_argument("--cell", type=str, default=None,
                    help="Optional: filter proposals to those whose "
                          "impacted_cells contain this cell_id")
    ap.add_argument("--proposal-type", type=str, default=None,
                    help="Optional: filter to a specific proposal_type")
    ap.add_argument("--min-evidence", type=float, default=0.10)
    ap.add_argument("--apply", action="store_true",
                    help="perform writes (default: dry-run)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not args.input_manifest.exists():
        print(f"input manifest missing: {args.input_manifest}", file=sys.stderr)
        return 2 if args.real_data_only else 1

    state_data = json.loads(args.input_manifest.read_text(encoding="utf-8"))
    pin_hash, pinned, hooks = mi.load_state(args.input_manifest)
    fixture_fallback = _detect_fixture_fallback(state_data)
    fixture_used = any(
        isinstance(v, dict) and v.get("used") is True
        for v in fixture_fallback.values()
    )

    # Real-data-only enforcement
    if args.real_data_only:
        for entry in pinned:
            if not Path(entry.get("path", "")).exists():
                print(f"real-data-only: missing {entry.get('path')}",
                       file=sys.stderr)
                return 2

    # Hook contract validation
    hook_errors = mi.validate_hook_contracts(hooks, repo_root=ROOT)
    if hook_errors:
        for err in hook_errors:
            print(f"hook contract error: {err}", file=sys.stderr)
        if args.real_data_only:
            return 2

    # Load upstream artifacts
    self_model = mi.load_self_model(pinned) or {}
    curiosity_summary = mi.load_curiosity_summary(pinned)
    curiosity_log = mi.load_curiosity_log(pinned)
    calibration_corr = mi.load_calibration_corrections(pinned)
    dream_meta_proposals = mi.load_dream_meta_proposals(pinned)
    resilience_doc = mi.load_resilience_doc(pinned)

    # Fixture fallback for the dream plane if real artifacts missing
    if not dream_meta_proposals and fixture_fallback.get(
        "dream_plane", {}
    ).get("used"):
        dream_meta_proposals = []  # leave empty; lack of dream evidence
                                     # is honest — no fabricated input

    # Build evidence items
    items = (
        ml.gather_curiosity_evidence(curiosity_summary, curiosity_log)
        + ml.gather_self_model_evidence(self_model, calibration_corr)
        + ml.gather_dream_evidence(dream_meta_proposals)
        + ml.gather_resilience_evidence(resilience_doc)
    )
    items_by_target = ml.aggregate_by_target(items)

    # History context
    history_path = args.history_path or DEFAULT_HISTORY_PATH
    history_entries = hist.read_entries(history_path)
    history_seen = hist.all_seen_ids(history_entries)
    history_prev_run = hist.latest_immediate_prev_run_ids(history_entries)

    branch = _detect_branch() or "phase8.5/hive-proposes"
    base_commit = _detect_base_commit() or ""

    res = ml.synthesize_proposals(
        items=items, self_model=self_model,
        dream_meta_proposals=dream_meta_proposals,
        resilience_doc=resilience_doc,
        branch_name=branch, base_commit_hash=base_commit,
        pinned_input_manifest_sha256=pin_hash,
        consumed_hook_contracts=hooks,
        fixture_fallback_used=fixture_used,
        history_ids_seen=history_seen,
        history_ids_in_immediate_prev=history_prev_run,
        min_evidence=args.min_evidence,
    )

    # Apply CLI filters (do not mutate ranking, only emission)
    proposals = list(res.proposals)
    if args.cell:
        proposals = [p for p in proposals
                       if args.cell in p.impacted_cells]
    if args.proposal_type:
        proposals = [p for p in proposals
                       if p.proposal_type == args.proposal_type]

    # Resolved proposals = ids that appeared in the immediate prev
    # run but not in the current set
    current_ids = {p.meta_proposal_id for p in proposals}
    resolved = sorted(history_prev_run - current_ids) if history_prev_run else []

    out_dir = args.output_dir or _default_out_dir(pin_hash)

    summary = {
        "pin_hash": pin_hash,
        "branch": branch,
        "proposals": len(proposals),
        "insufficient_evidence": len(res.insufficient_evidence),
        "rejected": len(res.rejected_candidates),
        "resolved": len(resolved),
        "fixture_fallback_used": fixture_used,
        "hook_contract_errors": len(hook_errors),
        "out_dir": out_dir.as_posix(),
    }

    if args.dry_run or not args.apply:
        if args.json:
            print(json.dumps(summary, indent=2, sort_keys=True))
        else:
            print("=== hive_proposes (dry-run) ===")
            for k, v in summary.items():
                print(f"{k}: {v}")
            for p in proposals[:5]:
                print(f"  - {p.meta_proposal_id} {p.proposal_type} "
                       f"prio={p.proposal_priority:.4f}")
            if not args.apply:
                print("(use --apply to write artifacts)")
        return 0

    # --apply: write artifacts
    rb.emit_hive_proposals(
        proposals=proposals,
        branch_name=branch, base_commit_hash=base_commit,
        pinned_input_manifest_sha256=pin_hash,
        out_dir=out_dir,
    )
    rb.emit_meta_evidence_map(
        items_by_target=items_by_target, proposals=proposals,
        branch_name=branch, base_commit_hash=base_commit,
        pinned_input_manifest_sha256=pin_hash,
        out_dir=out_dir,
    )
    bundle = rb.build_review_bundle(
        proposals=proposals,
        insufficient_evidence=list(res.insufficient_evidence),
        rejected_candidates=list(res.rejected_candidates),
        resolved_proposal_ids=resolved,
        branch_name=branch, base_commit_hash=base_commit,
        pinned_input_manifest_sha256=pin_hash,
        consumed_hook_contracts=hooks,
        fixture_fallback_used=fixture_fallback,
    )
    rb.emit_review_bundle(bundle, out_dir=out_dir)

    # History append: one entry per proposal in this run.
    # ts is fixed at the run-start UTC second so reruns of this exact
    # input set produce byte-identical entries (deterministic).
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    prev = hist.latest_prev_entry_sha256(history_entries)
    for p in proposals:
        e = hist.make_entry(
            meta_proposal_id=p.meta_proposal_id,
            proposal_type=p.proposal_type,
            output_dir=out_dir.as_posix(),
            base_commit_hash=base_commit,
            pinned_input_manifest_sha256=pin_hash,
            prev_entry_sha256=prev,
            ts=ts,
        )
        hist.append_entry(history_path, e)
        prev = e.entry_sha256

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print("=== hive_proposes (apply) ===")
        for k, v in summary.items():
            print(f"{k}: {v}")
        print(f"history: {history_path.as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
