#!/usr/bin/env python3
"""run_claude_builder_lane — Phase 9 §U2 CLI driver.

Builds a BuilderRequest, allocates an isolated worktree, plans the
routing, and emits the request pack + an empty result-pack scaffold.

CRITICAL: this tool DOES NOT invoke Claude Code from this session.
Per Prompt_1_Master §U2 SUBPROCESS EXCEPTION, subprocess invocation
is allowed but is gated by an explicit operator step (--invoke-now,
not implemented in this scaffold). The default behavior is plan +
record only.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.builder_lane import (  # noqa: E402
    builder_request_pack as brp,
    builder_result_pack as brres,
    session_forge as sf,
    worktree_allocator as wa,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-kind", type=str, required=True,
                    choices=brp.TASK_KINDS)
    ap.add_argument("--intent", type=str, required=True)
    ap.add_argument("--capsule-context", type=str, default="neutral_v1")
    ap.add_argument("--worktree-root", type=Path,
                    default=ROOT.parent)
    ap.add_argument("--invocation-log",
                    type=Path,
                    default=ROOT / "docs" / "runs" / "phase9"
                          / "builder_lane" / "builder_invocation_log.jsonl")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    # Pre-allocate to derive worktree path and branch name
    placeholder_id = brp.compute_request_id(
        task_kind=args.task_kind, intent=args.intent,
        capsule_context=args.capsule_context, input_payload={},
    )
    allocation = wa.allocate(
        request_id=placeholder_id,
        root=args.worktree_root,
    )
    request = brp.make_request(
        task_kind=args.task_kind,
        intent=args.intent,
        isolated_worktree_path=allocation.base_path.as_posix(),
        isolated_branch_name=allocation.branch_name,
        capsule_context=args.capsule_context,
        max_invocations=1,
        max_wall_seconds=600,
    )
    plan = sf.plan(
        request=request, worktree_root=args.worktree_root,
        invocation_log_path=args.invocation_log,
    )

    summary = {
        "request_id": request.request_id,
        "task_kind": request.task_kind,
        "isolated_branch_name": request.isolated_branch_name,
        "isolated_worktree_path": request.isolated_worktree_path,
        "no_main_branch_auto_merge": request.no_main_branch_auto_merge,
        "no_runtime_mutation": request.no_runtime_mutation,
        "chosen_provider_type": plan.routing.chosen_provider_type,
        "rationale": plan.routing.rationale,
        "dry_run": not args.apply,
    }

    if args.apply:
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        # Record the invocation log entry. NOTE: this CLI does NOT
        # actually call Claude Code in this session; outcome marked
        # accordingly.
        wa.append_invocation(
            args.invocation_log,
            wa.InvocationLogEntry(
                request_id=request.request_id,
                ts_iso=ts,
                isolated_worktree_path=request.isolated_worktree_path,
                isolated_branch_name=request.isolated_branch_name,
                outcome="advisory_only",
                rationale=("scaffold-only invocation; "
                            "subprocess execution is gated for human review"),
            ),
        )
        # Emit an advisory-only result pack
        result = brres.make_result(
            request_id=request.request_id,
            outcome="advisory_only",
            isolated_branch_name=request.isolated_branch_name,
            isolated_worktree_path=request.isolated_worktree_path,
            ts_iso=ts,
            human_review_required=True,
        )
        out_dir = args.invocation_log.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"request_{request.request_id}.json").write_text(
            json.dumps(request.to_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        (out_dir / f"result_{result.result_id}.json").write_text(
            json.dumps(result.to_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        summary["request_pack_path"] = (
            out_dir / f"request_{request.request_id}.json"
        ).as_posix()
        summary["result_pack_path"] = (
            out_dir / f"result_{result.result_id}.json"
        ).as_posix()
        summary["invocation_log_path"] = args.invocation_log.as_posix()

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print("=== run_claude_builder_lane ===")
        for k, v in summary.items():
            print(f"{k}: {v}")
        if not args.apply:
            print("(use --apply to write packs and append invocation log)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
