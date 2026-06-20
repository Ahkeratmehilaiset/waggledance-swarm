# SPDX-License-Identifier: BUSL-1.1
"""Read-only diagnostic: WARN when a GATE-DECISION bridge signal carries a
task_id that is not a known PR headRefName.

Rationale (wd/ops/bridge-gate-taskid-variance-reemit-churn-20260620): the merge
gate keys on the canonical task_id = PR headRefName. When an RCO/build agent
posts a gate-decision (rco_pass / build_consensus_pass / changes_requested) on a
COORDINATION task -- e.g. a reanchor task ``pr<N>-reanchor-post<M>`` -- instead
of the headRefName, the gate does not recognize it and a re-emit is required
(the #1300/#1330 variance churn). This tool WARNS on that mis-post pattern so it
is caught early, before it stalls a ready PR.

Governance: this is a STANDALONE, READ-ONLY, WARN-only diagnostic. It NEVER
blocks (always exits 0) and is NOT wired into the denylist gate-checkers.
Coordination statuses (message/handoff/wake_request/claim/status/...) legitimately
use coordination tasks and are never warned -- only the gate-decision statuses
are scoped.

The core ``find_gate_signal_taskid_warnings`` is offline/deterministic and pure
(the caller supplies the known PR headRefNames); the CLI fetches open and
recently-merged PR headRefNames via ``gh`` so legitimate signals on a
just-merged PR are not falsely warned.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.work_queue import resolve_bridge_root  # noqa: E402

# Gate-decision statuses whose task_id is expected to be a PR headRefName.
# Coordination statuses are intentionally excluded (they legitimately use
# coordination/reanchor tasks).
GATE_DECISION_STATUSES = frozenset(
    {
        "rco_pass",
        "build_consensus_pass",
        "changes_requested",
    }
)

DEFAULT_EVENTS_PATH = Path(".agent-bridge") / "shared" / "events.jsonl"
DEFAULT_REPO = "Ahkeratmehilaiset/waggledance-swarm"


def _read_events(events_path: Path) -> list[dict[str, Any]]:
    """Read JSONL bridge events, skipping blank/malformed/non-object lines."""
    out: list[dict[str, Any]] = []
    try:
        text = events_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except Exception:
            continue
        if not isinstance(event, dict):  # bare null / array / scalar lines
            continue
        out.append(event)
    return out


def find_gate_signal_taskid_warnings(
    events: Iterable[Mapping[str, Any]],
    known_pr_headrefs: Iterable[str],
) -> list[dict[str, Any]]:
    """Return one warning per gate-decision signal whose task_id is not a known
    PR headRefName.

    Read-only/advisory: this function never raises and never blocks. Only events
    whose ``status`` is in :data:`GATE_DECISION_STATUSES` are considered; all
    coordination traffic is ignored. ``known_pr_headrefs`` is supplied by the
    caller (typically open + recently-merged PR headRefNames) so legitimate
    signals on a just-merged PR are not falsely flagged.
    """
    known = {str(h).strip() for h in known_pr_headrefs if str(h).strip()}
    warnings: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        if not isinstance(event, Mapping):
            continue
        status = str(event.get("status", "") or "")
        if status not in GATE_DECISION_STATUSES:
            continue
        task_id = str(event.get("task_id", "") or "").strip()
        if task_id in known:
            continue
        warnings.append(
            {
                "index": index,
                "ts_utc": str(event.get("ts_utc", "") or ""),
                "agent": str(event.get("agent", "") or ""),
                "type": str(event.get("type", "") or ""),
                "status": status,
                "task_id": task_id,
                "reason": "gate_decision_signal_taskid_not_pr_headref",
            }
        )
    return warnings


def _pr_headrefs(repo: str, *, merged_limit: int = 60) -> list[str]:
    """Best-effort fetch of open + recently-merged PR headRefNames via gh.

    Network/CLI failures degrade to an empty list (the tool stays read-only and
    non-fatal); the caller may also pass headRefNames explicitly.
    """
    headrefs: list[str] = []
    for state, limit in (("open", 200), ("merged", merged_limit)):
        try:
            result = subprocess.run(
                [
                    "gh",
                    "pr",
                    "list",
                    "--repo",
                    repo,
                    "--state",
                    state,
                    "--json",
                    "headRefName",
                    "--limit",
                    str(limit),
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            data = json.loads(result.stdout or "[]")
            headrefs.extend(str(item.get("headRefName", "")) for item in data)
        except Exception:
            continue
    return headrefs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only WARN diagnostic: flag gate-decision bridge signals "
            "(rco_pass/build_consensus_pass/changes_requested) whose task_id is "
            "not a known PR headRefName (the #1300/#1330 coordination-task "
            "mis-post pattern). WARN-only; never blocks (always exits 0)."
        )
    )
    parser.add_argument(
        "--events",
        type=Path,
        default=None,
        help="Bridge events JSONL path. Defaults to <bridge-root>/shared/events.jsonl.",
    )
    parser.add_argument(
        "--bridge-root",
        type=Path,
        default=None,
        help=(
            "Path to .agent-bridge directory (default: "
            "AGENT_BRIDGE_RUNTIME_ROOT/AGENT_BRIDGE_ROOT or repo-local)."
        ),
    )
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument(
        "--headref",
        action="append",
        default=[],
        help=(
            "Known PR headRefName (repeatable). If omitted, open + "
            "recently-merged headRefNames are fetched via gh."
        ),
    )
    parser.add_argument(
        "--tail",
        type=int,
        default=2000,
        help="Only consider the last N events (recency bound; 0 = all).",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    events_path = _resolve_events_path(args.events, args.bridge_root)
    events = _read_events(events_path)
    if args.tail and args.tail > 0:
        events = events[-args.tail :]
    headrefs = list(args.headref) or _pr_headrefs(args.repo)
    warnings = find_gate_signal_taskid_warnings(events, headrefs)
    if args.json:
        print(
            json.dumps(
                {
                    "warnings": warnings,
                    "warning_count": len(warnings),
                    "known_pr_headref_count": len({h for h in headrefs if h}),
                },
                indent=2,
                sort_keys=True,
            )
        )
    elif not warnings:
        print("OK: no gate-decision signals on non-headRefName task_ids.")
    else:
        print(
            f"WARN: {len(warnings)} gate-decision signal(s) on "
            "non-headRefName task_id(s) (possible coordination-task mis-post):"
        )
        for warning in warnings:
            print(
                f"  {warning['ts_utc']} {warning['agent']} "
                f"{warning['type']}/{warning['status']} "
                f"task_id={warning['task_id']}"
            )
    # WARN-only: never a hard block.
    return 0


def _resolve_events_path(events_path: Path | None, bridge_root: Path | None) -> Path:
    if events_path is not None:
        return events_path
    return resolve_bridge_root(bridge_root) / "shared" / "events.jsonl"


if __name__ == "__main__":
    raise SystemExit(main())
