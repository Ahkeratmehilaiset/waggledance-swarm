# SPDX-License-Identifier: Apache-2.0
#!/usr/bin/env python3
"""wd_kernel_tick — drive one autonomy kernel tick (Phase 9 §F).

Reads the persisted kernel state, applies one stateless tick(), and
optionally persists the post-tick state. Inbound signals (a JSONL
file) are fed in to drive deterministic recommendation generation.

Runtime safety: zero touch. No port 8002. No live LLM. No runtime
mutation. Recommendations are emitted to stdout / a JSON file but
never executed by this tool — action_gate (a later F sub-component)
is the only exit point.

CLI:
  python tools/wd_kernel_tick.py --help
  python tools/wd_kernel_tick.py                       # dry-run
  python tools/wd_kernel_tick.py --apply               # persist post-tick state
  python tools/wd_kernel_tick.py --signals path/to/signals.jsonl
  python tools/wd_kernel_tick.py --kernel-state path/to/kernel_state.json --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.autonomy import (  # noqa: E402
    background_scheduler as bg,
    governor as gov,
    kernel_state as ks,
    mission_queue as mq,
    policy_core as pc,
)


DEFAULT_CONSTITUTION = ROOT / "waggledance" / "core" / "autonomy" / "constitution.yaml"
DEFAULT_KERNEL_STATE = ROOT / "docs" / "runs" / "phase9" / "kernel_state.json"


def _load_signals(path: Path | None) -> list[dict]:
    if path is None or not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--constitution", type=Path, default=DEFAULT_CONSTITUTION)
    ap.add_argument("--kernel-state", type=Path, default=DEFAULT_KERNEL_STATE)
    ap.add_argument("--signals", type=Path, default=None,
                    help="Optional JSONL of inbound signals to route")
    ap.add_argument("--missions", type=Path, default=None,
                    help="Optional JSONL mission queue to schedule for this tick")
    ap.add_argument("--max-dispatched", type=int, default=20,
                    help="Maximum missions selected from --missions")
    ap.add_argument("--ts", type=str, default=None,
                    help="Override the tick timestamp (UTC ISO 8601). "
                         "Defaults to datetime.now(UTC). The ts is never "
                         "used in determinism hashes.")
    ap.add_argument("--apply", action="store_true",
                    help="Persist post-tick state (default: dry-run)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not args.constitution.exists():
        print(f"constitution not found: {args.constitution}", file=sys.stderr)
        return 2
    if args.missions is not None and not args.missions.exists():
        print(f"missions file not found: {args.missions}", file=sys.stderr)
        return 2
    constitution_sha = gov.load_constitution_sha256(args.constitution)
    constitution_id = gov.load_constitution_id(args.constitution)

    state = ks.load_state(args.kernel_state)
    if state is None:
        state = ks.initial_state(
            constitution_id=constitution_id,
            constitution_sha256=constitution_sha,
        )

    signals = _load_signals(args.signals)
    ts_iso = args.ts or datetime.now(timezone.utc).isoformat(timespec="seconds")

    report = gov.tick(
        state=state, ts_iso=ts_iso,
        constitution_id=constitution_id,
        constitution_sha256=constitution_sha,
        inbound_signals=signals,
        dry_run=not args.apply,
        kernel_state_path=args.kernel_state,
    )

    mission_summary = {
        "mission_queue_input_path": args.missions.as_posix() if args.missions else None,
        "mission_queue_path": None,
        "missions_loaded": 0,
        "missions_selected": 0,
        "missions_deferred": 0,
        "missions_blocked": 0,
    }
    if args.missions is not None:
        missions = mq.load_missions(args.missions)
        hard_rules = pc.load_hard_rules(args.constitution)
        dispatch_report = bg.schedule_one_tick(
            state=report.state_after,
            missions=missions,
            hard_rules=hard_rules,
            max_dispatched=args.max_dispatched,
        )
        updated_missions = bg.apply_dispatch_report(missions, dispatch_report)
        mission_queue_path = None
        if args.apply:
            mission_queue_path = mq.save_missions(updated_missions, args.missions)
        mission_summary = {
            "mission_queue_input_path": args.missions.as_posix(),
            "mission_queue_path": (
                mission_queue_path.as_posix() if mission_queue_path else None
            ),
            "missions_loaded": len(missions),
            "missions_selected": len(dispatch_report.selected_missions),
            "missions_deferred": len(dispatch_report.deferred_missions),
            "missions_blocked": len(dispatch_report.blocked_missions),
        }

    summary = {
        "tick_id": report.state_after.last_tick.tick_id if report.state_after.last_tick else None,
        "next_tick_id": report.state_after.next_tick_id,
        "constitution_sha256": constitution_sha,
        "recommendations": len(report.recommendations),
        "actions_recommended_total": report.state_after.actions_recommended_total,
        "persisted_revision": report.state_after.persisted_revision,
        "kernel_state_path": (report.kernel_state_path.as_posix()
                                if report.kernel_state_path else None),
        "notes": list(report.notes),
        "dry_run": not args.apply,
        **mission_summary,
    }
    if args.json:
        out = dict(summary)
        out["recommendations_detail"] = [r.to_dict() for r in report.recommendations]
        print(json.dumps(out, indent=2, sort_keys=True))
    else:
        print("=== wd_kernel_tick ===")
        for k, v in summary.items():
            print(f"{k}: {v}")
        for r in report.recommendations[:5]:
            print(f"  - {r.recommendation_id} {r.kind}/{r.lane} risk={r.risk}")
        if not args.apply:
            print("(use --apply to persist post-tick state)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
