# SPDX-License-Identifier: BUSL-1.1
"""Governance throughput metrics report for the WD agent-bridge.

Computes the eight metrics the 2026-05-20 V12 RFC named as the test for
"bridge governance is a real differentiator only when measured":

1. proposal -> RCO latency (p50/p99 seconds)
2. unsafe-diff blocked rate (PRs where a reviewer emitted
   finding/changes_requested or decision/rejected pre-merge)
3. regression catch rate (PRs where finding/changes_requested led to a
   revision before merge)
4. synthetic-defect catch rate, split by reviewer (claude only / codex
   only / both / neither — addresses the correlated-peer-review risk
   the RFC called out)
5. postmerge failure rate (revert PRs / merged PRs in window)
6. review disagreement rate (PRs with two or more
   finding/changes_requested events on the same task_id)
7. promotion reversal rate (merges later reverted in same window)
8. shadow -> canary -> live latency per risk class (computed only when
   bridge events carry promotion-lifecycle transitions plus
   payload.risk_class; otherwise reported as status=deferred or
   status=insufficient_data)

All inputs are read from the bridge event JSONL plus an optional PR
history list. The tool never writes events, never claims work-queue
tasks, and never opens or merges PRs.

Companion to `tools/idle_loop_once.py` and `tools/agent_next_task.py`.
This is Slice "Candidate B" from bridge consensus task_id
`wd-v12-rfc-next-slice-2026-05-20`: Codex re-routed Candidate B to
Claude after taking Candidate E.1 (receipt-adoption inventory) himself.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.work_queue import resolve_bridge_root  # noqa: E402


PROPOSAL_STATUS_TOKENS = {"proposal", "proposed", "proposal_to_pr"}
RCO_STATUS_TOKENS = {
    "rco_pass", "rco_approved", "rco_changes_requested",
    "approved_with_constraints", "approved_round_2_pending_ci",
    "approved_ci_green", "approved", "rco_pass_pr",
}
CHANGES_REQUESTED_TOKENS = {
    "changes_requested", "rco_changes_requested",
    "rco_request_changes", "changes_requested_round_1",
    "changes_requested_round_2", "rejected", "request_changes",
    "rejected_event_id",
}
REJECTION_TOKENS = {"rejected", "rejection", "refused_live_db"}
MERGE_TOKENS = {"merged", "merged_verified", "merged_pr", "merged_round"}
REVERT_TOKENS = {"revert", "reverted", "promotion_reversal", "rolled_back"}
PROMOTION_LIFECYCLE_STAGES = ("shadow", "canary", "live")

PR_REFERENCE_RE = re.compile(r"\bpr[_ #]?(\d{2,5})\b", re.IGNORECASE)
INSUFFICIENT_DATA_THRESHOLD = 5
DEFAULT_WINDOW_DAYS = 7


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Report governance-throughput metrics for the WD agent bridge."
        ),
    )
    parser.add_argument(
        "--events",
        type=Path,
        default=None,
        help=(
            "Bridge event JSONL path. Defaults to "
            "<runtime bridge root>/shared/events.jsonl."
        ),
    )
    parser.add_argument(
        "--bridge-root",
        type=Path,
        default=None,
        help=(
            "Runtime bridge root used when --events is omitted. Defaults to "
            "AGENT_BRIDGE_RUNTIME_ROOT, AGENT_BRIDGE_ROOT, then repo .agent-bridge."
        ),
    )
    parser.add_argument(
        "--window-days",
        type=int,
        default=DEFAULT_WINDOW_DAYS,
        help=(
            "How many UTC days back from --now to include. Default 7. "
            "Use 0 for the full event history."
        ),
    )
    parser.add_argument(
        "--insufficient-threshold",
        type=int,
        default=INSUFFICIENT_DATA_THRESHOLD,
        help=(
            "Sample size below which a metric reports "
            "status=insufficient_data. Default 5."
        ),
    )
    parser.add_argument("--now", default=None)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    now_utc = (
        _parse_utc(args.now) if args.now else datetime.now(timezone.utc)
    )
    events_path = args.events
    if events_path is None:
        events_path = resolve_bridge_root(args.bridge_root) / "shared" / "events.jsonl"
    try:
        events = read_events(events_path)
    except OSError as exc:
        report = {
            "decision": "unknown",
            "errors": [str(exc)],
            "exit_code": 2,
        }
        print(json.dumps(report, sort_keys=True))
        return 2

    report = compute_governance_throughput_report(
        events=events,
        now_utc=now_utc,
        window_days=args.window_days,
        insufficient_threshold=args.insufficient_threshold,
    )

    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(f"governance throughput report ({report['window_label']})")
        for metric in report["metrics"]:
            print(f"  {metric['name']:42s} status={metric['status']}")
            for k, v in metric.items():
                if k in {"name", "status", "notes"}:
                    continue
                print(f"    {k}: {v}")
            for note in metric.get("notes", []):
                print(f"    note: {note}")
    return 0


def read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def compute_governance_throughput_report(
    *,
    events: Sequence[Mapping[str, Any]],
    now_utc: datetime,
    window_days: int = DEFAULT_WINDOW_DAYS,
    insufficient_threshold: int = INSUFFICIENT_DATA_THRESHOLD,
) -> dict[str, Any]:
    """Compute the 8-metric governance-throughput report."""
    if window_days < 0:
        raise ValueError("window_days must be >= 0")
    if insufficient_threshold < 1:
        raise ValueError("insufficient_threshold must be >= 1")

    if window_days == 0:
        windowed = list(events)
        window_label = "all-time"
        window_start: datetime | None = None
    else:
        window_start = now_utc - timedelta(days=window_days)
        windowed = [
            event
            for event in events
            if _event_ts(event) is not None
            and _event_ts(event) >= window_start
        ]
        window_label = (
            f"last-{window_days}d (from {window_start.isoformat()})"
        )

    by_task: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for event in windowed:
        task_id = str(event.get("task_id") or "")
        if task_id:
            by_task[task_id].append(event)

    metrics: list[dict[str, Any]] = [
        _metric_proposal_to_rco_latency(by_task, insufficient_threshold),
        _metric_unsafe_diff_blocked_rate(by_task, insufficient_threshold),
        _metric_regression_catch_rate(by_task, insufficient_threshold),
        _metric_synthetic_defect_catch_by_reviewer(
            by_task, insufficient_threshold
        ),
        _metric_postmerge_failure_rate(windowed, insufficient_threshold),
        _metric_review_disagreement_rate(by_task, insufficient_threshold),
        _metric_promotion_reversal_rate(windowed, insufficient_threshold),
        _metric_shadow_to_live_latency(windowed, insufficient_threshold),
    ]

    return {
        "decision": "governance_throughput_report",
        "ok": True,
        "computed_at_utc": now_utc.isoformat(),
        "window_days": window_days,
        "window_label": window_label,
        "event_count_in_window": len(windowed),
        "task_count_in_window": len(by_task),
        "insufficient_threshold": insufficient_threshold,
        "metrics": metrics,
    }


# ---------------------------------------------------------------------------
# metric implementations
# ---------------------------------------------------------------------------


def _metric_proposal_to_rco_latency(
    by_task: Mapping[str, list[Mapping[str, Any]]],
    threshold: int,
) -> dict[str, Any]:
    samples: list[float] = []
    for task_id, events in by_task.items():
        proposals = [
            e
            for e in events
            if _status_has_any(e, PROPOSAL_STATUS_TOKENS)
        ]
        rcos = [e for e in events if _status_has_any(e, RCO_STATUS_TOKENS)]
        if not proposals or not rcos:
            continue
        proposal_ts = min(_event_ts(e) for e in proposals if _event_ts(e))
        rco_ts = min(
            (_event_ts(e) for e in rcos if _event_ts(e) and _event_ts(e) > proposal_ts),
            default=None,
        )
        if proposal_ts and rco_ts:
            samples.append((rco_ts - proposal_ts).total_seconds())
    return _summarize_latency(
        name="proposal_to_rco_latency",
        samples=samples,
        threshold=threshold,
        unit_note=(
            "time from any proposal/proposed status to the first matching "
            "RCO pass/approved/changes-requested status on the same task_id"
        ),
    )


def _metric_unsafe_diff_blocked_rate(
    by_task: Mapping[str, list[Mapping[str, Any]]],
    threshold: int,
) -> dict[str, Any]:
    pr_tasks: set[str] = set()
    blocked_tasks: set[str] = set()
    for task_id, events in by_task.items():
        touches_pr = any(_extract_pr_numbers(e) for e in events)
        if not touches_pr:
            continue
        pr_tasks.add(task_id)
        if any(
            _status_has_any(e, CHANGES_REQUESTED_TOKENS) for e in events
        ):
            blocked_tasks.add(task_id)
    sample_size = len(pr_tasks)
    if sample_size < threshold:
        return _insufficient(
            "unsafe_diff_blocked_rate",
            sample_size=sample_size,
            threshold=threshold,
            unit_note=(
                "fraction of PR-touching task threads where a reviewer "
                "emitted finding/changes_requested or decision/rejected"
            ),
        )
    return {
        "name": "unsafe_diff_blocked_rate",
        "status": "ok",
        "sample_size": sample_size,
        "blocked_task_count": len(blocked_tasks),
        "rate_pct": round(100.0 * len(blocked_tasks) / sample_size, 3),
        "notes": [
            "0% means no peer-review caught anything; very high values "
            "may indicate a noisy reviewer or fragile PR pipeline"
        ],
    }


def _metric_regression_catch_rate(
    by_task: Mapping[str, list[Mapping[str, Any]]],
    threshold: int,
) -> dict[str, Any]:
    pr_tasks: set[str] = set()
    catch_and_revise_tasks: set[str] = set()
    for task_id, events in by_task.items():
        if not any(_extract_pr_numbers(e) for e in events):
            continue
        pr_tasks.add(task_id)
        # Codex RCO finding 2026-05-20T16:28:03Z: must check timestamp
        # order. Earliest changes_requested timestamp must strictly precede
        # at least one rco_pass timestamp on the same task_id; otherwise a
        # pass-then-changes thread would falsely score as catch-then-fix.
        change_times = [
            _event_ts(e) for e in events
            if _status_has_any(e, CHANGES_REQUESTED_TOKENS) and _event_ts(e)
        ]
        if not change_times:
            continue
        earliest_change = min(change_times)
        has_subsequent_pass = any(
            _event_ts(e) is not None
            and _event_ts(e) > earliest_change
            and _status_has_any(e, RCO_STATUS_TOKENS)
            and not _status_has_any(e, CHANGES_REQUESTED_TOKENS)
            for e in events
        )
        if has_subsequent_pass:
            catch_and_revise_tasks.add(task_id)
    sample_size = len(pr_tasks)
    if sample_size < threshold:
        return _insufficient(
            "regression_catch_rate",
            sample_size=sample_size,
            threshold=threshold,
            unit_note=(
                "fraction of PR threads that had a changes-requested then "
                "a subsequent rco pass on the same task_id (proxy: catch "
                "then fix)"
            ),
        )
    return {
        "name": "regression_catch_rate",
        "status": "ok",
        "sample_size": sample_size,
        "catch_and_revise_count": len(catch_and_revise_tasks),
        "rate_pct": round(
            100.0 * len(catch_and_revise_tasks) / sample_size, 3
        ),
        "notes": [
            "higher = peer review actually drove revisions before merge"
        ],
    }


def _metric_synthetic_defect_catch_by_reviewer(
    by_task: Mapping[str, list[Mapping[str, Any]]],
    threshold: int,
) -> dict[str, Any]:
    by_reviewer = Counter()
    pr_tasks_with_any_catch: set[str] = set()
    for task_id, events in by_task.items():
        if not any(_extract_pr_numbers(e) for e in events):
            continue
        agents_who_caught: set[str] = set()
        for event in events:
            if _status_has_any(event, CHANGES_REQUESTED_TOKENS):
                agents_who_caught.add(_event_agent(event))
        if not agents_who_caught:
            continue
        pr_tasks_with_any_catch.add(task_id)
        if "claude" in agents_who_caught and "codex" in agents_who_caught:
            by_reviewer["both"] += 1
        elif "claude" in agents_who_caught:
            by_reviewer["claude_only"] += 1
        elif "codex" in agents_who_caught:
            by_reviewer["codex_only"] += 1
        else:
            by_reviewer["other_only"] += 1
    sample_size = len(pr_tasks_with_any_catch)
    if sample_size < threshold:
        return _insufficient(
            "synthetic_defect_catch_by_reviewer",
            sample_size=sample_size,
            threshold=threshold,
            unit_note=(
                "tasks with any catch split by which agent(s) emitted it; "
                "'both' is the diversity-of-review proxy that addresses "
                "the RFC's correlated-peer-review risk"
            ),
        )
    return {
        "name": "synthetic_defect_catch_by_reviewer",
        "status": "ok",
        "sample_size": sample_size,
        "by_reviewer": dict(by_reviewer),
        "diversity_pct": round(
            100.0 * by_reviewer.get("both", 0) / sample_size, 3
        ),
        "notes": [
            "diversity_pct = fraction of catches where BOTH agents caught "
            "the same issue (high = good independent review; low = "
            "correlated review or single-reviewer bottleneck)"
        ],
    }


def _metric_postmerge_failure_rate(
    events: Sequence[Mapping[str, Any]],
    threshold: int,
) -> dict[str, Any]:
    merged_prs: set[int] = set()
    reverted_prs: set[int] = set()
    for event in events:
        status = _event_status(event)
        message = _event_message(event)
        prs = _extract_pr_numbers(event)
        if not prs:
            continue
        if _status_has_any(event, MERGE_TOKENS):
            merged_prs.update(prs)
        if _status_has_any(event, REVERT_TOKENS) or any(
            token in message.lower()
            for token in ("revert", "rolled back", "reverted")
        ):
            reverted_prs.update(prs)
    # Codex RCO finding 2026-05-20T16:28:03Z: rate must not exceed 100%.
    # Reverts can reference PRs whose merge event is outside the window
    # (or never emitted to the bridge). Counting them in the numerator
    # while their merge is absent from the denominator pushed the ratio
    # over 1.0. Intersect first: only reverts of in-window-merged PRs
    # count toward the rate. The out-of-window reverts surface as their
    # own field for visibility.
    reverted_in_window = reverted_prs & merged_prs
    reverted_out_of_window = reverted_prs - merged_prs
    sample_size = len(merged_prs)
    if sample_size < threshold:
        return _insufficient(
            "postmerge_failure_rate",
            sample_size=sample_size,
            threshold=threshold,
            unit_note=(
                "in-window reverted_prs / merged_prs from bridge events "
                "(only reverts whose merge is also in window)"
            ),
        )
    return {
        "name": "postmerge_failure_rate",
        "status": "ok",
        "sample_size": sample_size,
        "merged_pr_count": len(merged_prs),
        "reverted_pr_count": len(reverted_in_window),
        "reverted_out_of_window_pr_count": len(reverted_out_of_window),
        "rate_pct": round(
            100.0 * len(reverted_in_window) / sample_size, 3
        ),
        "notes": [
            "if reverted_pr_count = 0, value is 0% but that does NOT prove "
            "good substrate — it can mean low coverage of revert detection",
            "reverted_out_of_window_pr_count reports reverts whose merge "
            "is outside the window; they are excluded from rate to keep "
            "rate <= 100%",
        ],
    }


def _metric_review_disagreement_rate(
    by_task: Mapping[str, list[Mapping[str, Any]]],
    threshold: int,
) -> dict[str, Any]:
    pr_tasks: set[str] = set()
    multi_changes_tasks: set[str] = set()
    for task_id, events in by_task.items():
        if not any(_extract_pr_numbers(e) for e in events):
            continue
        pr_tasks.add(task_id)
        changes_count = sum(
            1 for e in events if _status_has_any(e, CHANGES_REQUESTED_TOKENS)
        )
        if changes_count >= 2:
            multi_changes_tasks.add(task_id)
    sample_size = len(pr_tasks)
    if sample_size < threshold:
        return _insufficient(
            "review_disagreement_rate",
            sample_size=sample_size,
            threshold=threshold,
            unit_note=(
                "fraction of PR threads with two or more changes-requested "
                "events on the same task_id (proxy for back-and-forth "
                "rework)"
            ),
        )
    return {
        "name": "review_disagreement_rate",
        "status": "ok",
        "sample_size": sample_size,
        "multi_round_task_count": len(multi_changes_tasks),
        "rate_pct": round(
            100.0 * len(multi_changes_tasks) / sample_size, 3
        ),
        "notes": [
            "0% can mean review is converging in one round (good) OR "
            "reviewers are not pushing back enough (bad). Pair with "
            "synthetic_defect_catch_by_reviewer to disambiguate"
        ],
    }


def _metric_promotion_reversal_rate(
    events: Sequence[Mapping[str, Any]],
    threshold: int,
) -> dict[str, Any]:
    """Tighter sibling of postmerge_failure_rate: only counts reversions
    that explicitly call out promotion / rollback semantics."""
    promotion_events = [
        event
        for event in events
        if "promot" in _event_status(event).lower()
        or "promot" in _event_message(event).lower()
    ]
    reversal_events = [
        event
        for event in promotion_events
        if _status_has_any(event, REVERT_TOKENS)
        or "rolled back" in _event_message(event).lower()
        or "reverted" in _event_message(event).lower()
    ]
    sample_size = len(promotion_events)
    if sample_size < threshold:
        return _insufficient(
            "promotion_reversal_rate",
            sample_size=sample_size,
            threshold=threshold,
            unit_note=(
                "promotion_events with revert/rollback semantics / total "
                "promotion-tagged events"
            ),
        )
    return {
        "name": "promotion_reversal_rate",
        "status": "ok",
        "sample_size": sample_size,
        "promotion_event_count": len(promotion_events),
        "reversal_event_count": len(reversal_events),
        "rate_pct": round(
            100.0 * len(reversal_events) / sample_size, 3
        ),
        "notes": [
            "narrower than postmerge_failure_rate; requires explicit "
            "promotion vocabulary in bridge events"
        ],
    }


def _metric_shadow_to_live_latency(
    events: Sequence[Mapping[str, Any]],
    threshold: int,
) -> dict[str, Any]:
    lifecycle_events: list[Mapping[str, Any]] = []
    by_lifecycle_id: dict[str, dict[str, Any]] = {}
    for event in events:
        stage = _promotion_lifecycle_stage(event)
        if stage is None:
            continue
        lifecycle_events.append(event)
        risk_class = _promotion_risk_class(event)
        lifecycle_id = _promotion_lifecycle_id(event)
        ts = _event_ts(event)
        if not risk_class or not lifecycle_id or ts is None:
            continue

        record = by_lifecycle_id.setdefault(
            lifecycle_id,
            {
                "risk_class": risk_class,
                "risk_mismatch": False,
                "stages": {},
            },
        )
        if record["risk_class"] != risk_class:
            record["risk_mismatch"] = True
            continue

        stages = record["stages"]
        previous = stages.get(stage)
        if previous is None or ts < previous:
            stages[stage] = ts

    by_risk_class: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: {
            "shadow_to_canary": [],
            "canary_to_live": [],
            "shadow_to_live": [],
        }
    )
    for record in by_lifecycle_id.values():
        if record["risk_mismatch"]:
            continue
        stages = record["stages"]
        if not all(stage in stages for stage in PROMOTION_LIFECYCLE_STAGES):
            continue
        shadow = stages["shadow"]
        canary = stages["canary"]
        live = stages["live"]
        if not (shadow <= canary <= live):
            continue
        risk_samples = by_risk_class[record["risk_class"]]
        risk_samples["shadow_to_canary"].append(
            (canary - shadow).total_seconds()
        )
        risk_samples["canary_to_live"].append((live - canary).total_seconds())
        risk_samples["shadow_to_live"].append((live - shadow).total_seconds())

    complete_sample_size = sum(
        len(samples["shadow_to_live"]) for samples in by_risk_class.values()
    )
    if complete_sample_size == 0:
        return _metric_shadow_to_live_latency_deferred(
            observed_lifecycle_event_count=len(lifecycle_events),
        )
    if complete_sample_size < threshold:
        return _insufficient(
            "shadow_to_live_latency_per_risk_class",
            sample_size=complete_sample_size,
            threshold=threshold,
            unit_note=(
                "complete shadow/canary/live lifecycle threads with a stable "
                "payload.risk_class, grouped by task_id or payload lifecycle id"
            ),
        )

    all_shadow_to_live: list[float] = []
    summarized_by_risk: dict[str, dict[str, Any]] = {}
    for risk_class in sorted(by_risk_class):
        samples = by_risk_class[risk_class]
        all_shadow_to_live.extend(samples["shadow_to_live"])
        summarized_by_risk[risk_class] = {
            "sample_size": len(samples["shadow_to_live"]),
            "shadow_to_canary_seconds": _latency_stats(
                samples["shadow_to_canary"]
            ),
            "canary_to_live_seconds": _latency_stats(
                samples["canary_to_live"]
            ),
            "shadow_to_live_seconds": _latency_stats(
                samples["shadow_to_live"]
            ),
        }

    return {
        "name": "shadow_to_live_latency_per_risk_class",
        "status": "ok",
        "sample_size": complete_sample_size,
        "risk_class_count": len(summarized_by_risk),
        "shadow_to_live_seconds": _latency_stats(all_shadow_to_live),
        "by_risk_class": summarized_by_risk,
        "notes": [
            "computed from complete promotion shadow/canary/live transitions "
            "with stable payload.risk_class on the same lifecycle id",
            "this report remains read-only and does not promote, merge, claim, "
            "or change queue gates",
        ],
    }


def _metric_shadow_to_live_latency_deferred(
    *,
    observed_lifecycle_event_count: int = 0,
) -> dict[str, Any]:
    return {
        "name": "shadow_to_live_latency_per_risk_class",
        "status": "deferred",
        "rate_pct": None,
        "sample_size": 0,
        "observed_lifecycle_event_count": observed_lifecycle_event_count,
        "notes": [
            "no complete shadow/canary/live lifecycle sample with "
            "payload.risk_class was found in bridge events",
            "emit one event per promotion transition with status or payload "
            "stage shadow/canary/live plus payload.risk_class",
            "group transitions with a shared task_id or payload "
            "promotion_lifecycle_id / lifecycle_id / promotion_id",
        ],
    }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _summarize_latency(
    *,
    name: str,
    samples: list[float],
    threshold: int,
    unit_note: str,
) -> dict[str, Any]:
    sample_size = len(samples)
    if sample_size < threshold:
        return _insufficient(
            name,
            sample_size=sample_size,
            threshold=threshold,
            unit_note=unit_note,
        )
    ordered = sorted(samples)
    return {
        "name": name,
        "status": "ok",
        "sample_size": sample_size,
        "p50_seconds": round(_percentile(ordered, 0.50), 1),
        "p99_seconds": round(_percentile(ordered, 0.99), 1),
        "min_seconds": round(ordered[0], 1),
        "max_seconds": round(ordered[-1], 1),
        "notes": [unit_note],
    }


def _latency_stats(samples: Sequence[float]) -> dict[str, Any]:
    ordered = sorted(samples)
    if not ordered:
        return {
            "sample_size": 0,
            "p50_seconds": 0.0,
            "p99_seconds": 0.0,
            "min_seconds": 0.0,
            "max_seconds": 0.0,
        }
    return {
        "sample_size": len(ordered),
        "p50_seconds": round(_percentile(ordered, 0.50), 1),
        "p99_seconds": round(_percentile(ordered, 0.99), 1),
        "min_seconds": round(ordered[0], 1),
        "max_seconds": round(ordered[-1], 1),
    }


def _percentile(ordered: Sequence[float], pct: float) -> float:
    if not ordered:
        return 0.0
    rank = (len(ordered) - 1) * pct
    idx = int(rank)
    if idx >= len(ordered) - 1:
        return float(ordered[-1])
    lo = ordered[idx]
    hi = ordered[idx + 1]
    return float(lo + (hi - lo) * (rank - idx))


def _insufficient(
    name: str,
    *,
    sample_size: int,
    threshold: int,
    unit_note: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "status": "insufficient_data",
        "sample_size": sample_size,
        "required_for_status_ok": threshold,
        "notes": [
            unit_note,
            (
                f"sample_size {sample_size} below threshold {threshold}; "
                "collect more bridge events before relying on this metric"
            ),
        ],
    }


def _event_ts(event: Mapping[str, Any]) -> datetime | None:
    raw = str(event.get("ts_utc") or event.get("timestamp") or "")
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _event_agent(event: Mapping[str, Any]) -> str:
    return str(event.get("agent") or "").lower()


def _event_status(event: Mapping[str, Any]) -> str:
    return str(event.get("status") or "").lower()


def _event_type(event: Mapping[str, Any]) -> str:
    return str(event.get("type") or "").lower()


def _event_message(event: Mapping[str, Any]) -> str:
    return str(event.get("message") or "")


def _event_payload(event: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = event.get("payload")
    if isinstance(payload, Mapping):
        return payload
    return {}


def _payload_str(payload: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        raw = payload.get(key)
        if raw is None:
            continue
        value = str(raw).strip()
        if value:
            return value
    return ""


def _promotion_lifecycle_stage(event: Mapping[str, Any]) -> str | None:
    payload = _event_payload(event)
    explicit_stage = _payload_str(
        payload,
        "promotion_lifecycle_stage",
        "lifecycle_stage",
        "promotion_stage",
    )
    normalized_stage = _normalize_lifecycle_text(explicit_stage)
    for stage in PROMOTION_LIFECYCLE_STAGES:
        if stage in normalized_stage.split():
            return stage

    text = _normalize_lifecycle_text(
        " ".join([_event_status(event), _event_message(event)])
    )
    generic_stage = _normalize_lifecycle_text(_payload_str(payload, "stage"))
    if "promotion" in text.split():
        for stage in PROMOTION_LIFECYCLE_STAGES:
            if stage in generic_stage.split():
                return stage
    if "promotion" not in text.split():
        return None
    for stage in PROMOTION_LIFECYCLE_STAGES:
        if stage in text.split():
            return stage
    return None


def _promotion_risk_class(event: Mapping[str, Any]) -> str:
    raw = _payload_str(_event_payload(event), "risk_class", "risk")
    return re.sub(r"[^a-z0-9_.:-]+", "_", raw.lower()).strip("_")


def _promotion_lifecycle_id(event: Mapping[str, Any]) -> str:
    payload = _event_payload(event)
    raw = _payload_str(
        payload,
        "promotion_lifecycle_id",
        "lifecycle_id",
        "promotion_id",
    )
    if not raw:
        raw = str(event.get("task_id") or "")
    return raw.strip()


def _normalize_lifecycle_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _status_has_any(
    event: Mapping[str, Any],
    candidates: Iterable[str],
) -> bool:
    status = _event_status(event)
    if not status:
        return False
    return any(token in status for token in candidates)


def _extract_pr_numbers(event: Mapping[str, Any]) -> set[int]:
    text = " ".join(
        [_event_status(event), _event_message(event), str(event.get("task_id") or "")]
    )
    return {int(m.group(1)) for m in PR_REFERENCE_RE.finditer(text)}


def _parse_utc(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


if __name__ == "__main__":
    raise SystemExit(main())
