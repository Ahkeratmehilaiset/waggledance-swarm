#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Replay bridge events through Approach-B dev-orchestrator.

Approach B (per orchestrator simulation discussion 2026-05-12):
* Reuses .orchestrator/ surface (bridge_classify, eig2_bridge_projection).
* Adds handshake-claim protocol layer on top of existing `claim` events.
* Measures retrospectively: would Approach B have caught the actual session
  pain points (duplicate convergences, dangling-dep failures, false-positive
  batches, RCO gaps)?

Read-only — does not write to events.jsonl, does not modify state. Pure
simulation against historical data.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict, Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Reuse the existing classifier from .orchestrator/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / ".orchestrator"))
try:
    from bridge_classify import classify, RegressionClass  # type: ignore
except Exception:
    classify = None
    RegressionClass = None  # type: ignore


REPO = Path(__file__).resolve().parent.parent
EVENTS_PATH = REPO / ".agent-bridge" / "shared" / "events.jsonl"


@dataclass
class Event:
    ts: datetime
    agent: str
    type: str
    task_id: str
    status: str
    message: str
    payload: dict
    raw: dict


@dataclass
class TaskThread:
    task_id: str
    first_agent: str
    first_ts: datetime
    claim_agent: str | None = None
    claim_ts: datetime | None = None
    participants: set[str] = field(default_factory=set)
    events: list[Event] = field(default_factory=list)


def parse_events(path: Path, since: datetime) -> list[Event]:
    out: list[Event] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts_raw = d.get("ts_utc", "")
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            except ValueError:
                continue
            if ts < since:
                continue
            out.append(Event(
                ts=ts,
                agent=str(d.get("agent", "")),
                type=str(d.get("type", "")),
                task_id=str(d.get("task_id", "")),
                status=str(d.get("status", "")),
                message=str(d.get("message", "")),
                payload=d.get("payload") if isinstance(d.get("payload"), dict) else {},
                raw=d,
            ))
    out.sort(key=lambda e: e.ts)
    return out


def build_threads(events: list[Event]) -> dict[str, TaskThread]:
    threads: dict[str, TaskThread] = {}
    for ev in events:
        if not ev.task_id:
            continue
        th = threads.get(ev.task_id)
        if th is None:
            th = TaskThread(
                task_id=ev.task_id,
                first_agent=ev.agent,
                first_ts=ev.ts,
            )
            threads[ev.task_id] = th
        th.participants.add(ev.agent)
        th.events.append(ev)
        if ev.type == "claim" and th.claim_agent is None:
            th.claim_agent = ev.agent
            th.claim_ts = ev.ts
    return threads


# --- Approach B simulation rules ------------------------------------------

HANDSHAKE_WINDOW_MIN = 15  # peer must claim or ack within 15 min of first work post


def measure_handshake_coverage(threads: dict[str, TaskThread]) -> dict:
    """How often did a task start with a claim before substantive work?"""
    total = 0
    with_claim = 0
    claim_before_work = 0
    multi_agent = 0
    solo_to_multi_no_handshake = 0
    examples_no_handshake: list[str] = []
    for th in threads.values():
        if th.first_agent == "system":
            continue
        total += 1
        if th.claim_agent:
            with_claim += 1
            if th.claim_ts and th.claim_ts <= th.first_ts + timedelta(seconds=60):
                claim_before_work += 1
        non_system = {a for a in th.participants if a != "system"}
        if len(non_system) > 1:
            multi_agent += 1
            if not th.claim_agent:
                solo_to_multi_no_handshake += 1
                if len(examples_no_handshake) < 8:
                    examples_no_handshake.append(th.task_id)
    return {
        "total_task_threads": total,
        "with_claim_event": with_claim,
        "claim_before_or_with_first_work": claim_before_work,
        "multi_agent_threads": multi_agent,
        "multi_agent_no_handshake": solo_to_multi_no_handshake,
        "examples_no_handshake": examples_no_handshake,
    }


# Heuristic: detect "independent convergence" — same conceptual work, different
# task_ids, both agents post on the same theme within a short window.
CONVERGENCE_KEYWORDS = [
    (re.compile(r"\bai[- ]?(?:assisted|mentor)\b", re.I), "ai_mentor_bootstrap"),
    (re.compile(r"\bbootstrap[- ]?kit\b", re.I), "ai_mentor_bootstrap"),
    (re.compile(r"\bsolver[- ]?bootcamp\b", re.I), "ai_mentor_bootstrap"),
    (re.compile(r"\bl54[- ]?reframed\b", re.I), "l54_capability_factory"),
    (re.compile(r"\bcapability[- ]?factory\b", re.I), "l54_capability_factory"),
    (re.compile(r"\blazy[- ]?bind\b", re.I), "l54_capability_factory"),
    (re.compile(r"\bL33\b"), "l33"),
    (re.compile(r"\bmock\.patch\b"), "mock_patch"),
    (re.compile(r"\bgod[- ]?class\b", re.I), "l52_god_class"),
    (re.compile(r"\bdecomposition\b", re.I), "l52_god_class"),
]


def measure_convergence(events: list[Event]) -> dict:
    """Detect themes that both agents independently surfaced."""
    by_theme: dict[str, dict[str, list[Event]]] = defaultdict(lambda: defaultdict(list))
    for ev in events:
        text = f"{ev.message} {ev.task_id} {json.dumps(ev.payload, ensure_ascii=False)}"
        for pattern, theme in CONVERGENCE_KEYWORDS:
            if pattern.search(text):
                by_theme[theme][ev.agent].append(ev)
                break
    convergences: list[dict] = []
    for theme, by_agent in by_theme.items():
        if "claude" in by_agent and "codex" in by_agent:
            c_first = min(e.ts for e in by_agent["claude"])
            x_first = min(e.ts for e in by_agent["codex"])
            gap_min = abs((c_first - x_first).total_seconds()) / 60.0
            convergences.append({
                "theme": theme,
                "claude_first_post": c_first.isoformat(),
                "codex_first_post": x_first.isoformat(),
                "gap_minutes": round(gap_min, 1),
                "claude_event_count": len(by_agent["claude"]),
                "codex_event_count": len(by_agent["codex"]),
            })
    convergences.sort(key=lambda c: c["gap_minutes"])
    return {
        "convergent_themes": len(convergences),
        "themes": convergences,
    }


def measure_pr_lifecycle(events: list[Event]) -> dict:
    """For each PR mentioned in payloads, who opened, who reviewed, who merged."""
    opens: dict[int, list[Event]] = defaultdict(list)
    reviews: dict[int, set[str]] = defaultdict(set)
    merges: dict[int, list[Event]] = defaultdict(list)
    for ev in events:
        pr = ev.payload.get("pr") or ev.payload.get("pr_number") or ev.payload.get("pull_request")
        if not isinstance(pr, int):
            continue
        text = (ev.message or "").lower()
        s = ev.status.lower()
        if "pr_open" in s or s in ("opened", "pushed", "open"):
            opens[pr].append(ev)
        if any(k in text for k in ("rco", "review", "endorse", "approve")) or s in ("endorsed", "reviewed"):
            reviews[pr].add(ev.agent)
        if "merged" in s:
            merges[pr].append(ev)
    rco_coverage_hits = 0
    rco_coverage_total = 0
    missing_rco: list[int] = []
    for pr, open_evs in opens.items():
        if not open_evs:
            continue
        opener = open_evs[0].agent
        peer = "codex" if opener == "claude" else "claude" if opener == "codex" else None
        if peer is None:
            continue
        rco_coverage_total += 1
        if peer in reviews.get(pr, set()):
            rco_coverage_hits += 1
        else:
            missing_rco.append(pr)
    return {
        "prs_touched": len(opens),
        "prs_merged": len([p for p, m in merges.items() if m]),
        "rco_total": rco_coverage_total,
        "rco_with_peer_review": rco_coverage_hits,
        "rco_missing": sorted(missing_rco)[:20],
        "rco_coverage_pct": round(100.0 * rco_coverage_hits / max(1, rco_coverage_total), 1),
    }


def measure_finding_quality(events: list[Event]) -> dict:
    """Look at 'finding' type events and classify the false-positive ratio,
    using explicit retract / false-positive ack messages in the same thread."""
    findings: dict[str, list[Event]] = defaultdict(list)
    for ev in events:
        if ev.type == "finding":
            findings[ev.task_id or f"_{ev.ts}"].append(ev)
    retracted = 0
    total = 0
    fp_keywords = re.compile(
        r"\b(false[- ]?positive|retract|withdraw|correction|not[- ]?actually|"
        r"acknowledged.*not|disagree)\b", re.I)
    fp_examples: list[str] = []
    for tid, evs in findings.items():
        total += len(evs)
        for ev in evs:
            for peer_ev in events:
                if peer_ev.task_id == tid and peer_ev.ts > ev.ts and peer_ev.agent != ev.agent:
                    if fp_keywords.search(peer_ev.message or ""):
                        retracted += 1
                        if len(fp_examples) < 5:
                            fp_examples.append(f"{tid}: peer flagged via '{peer_ev.message[:80]}'")
                        break
    return {
        "total_findings": total,
        "retracted_or_disputed": retracted,
        "fp_rate_pct": round(100.0 * retracted / max(1, total), 1),
        "examples": fp_examples,
    }


def measure_classifier_coverage(events: list[Event]) -> dict:
    """Run .orchestrator/bridge_classify.py over events that mention failures.
    Approach B uses this for PR failure triage."""
    if classify is None:
        return {"classifier_available": False}
    failure_evs = [
        e for e in events
        if "fail" in (e.message or "").lower()
        or "error" in (e.message or "").lower()
        or e.status.lower() in ("failed", "failure", "blocked")
    ]
    klass_counts: Counter = Counter()
    for ev in failure_evs:
        text = (ev.message or "") + " " + json.dumps(ev.payload, ensure_ascii=False)
        try:
            klass = classify(text)
            klass_counts[klass.value if hasattr(klass, "value") else str(klass)] += 1
        except Exception:
            klass_counts["_classifier_error"] += 1
    return {
        "classifier_available": True,
        "failure_events_seen": len(failure_evs),
        "classification_distribution": dict(klass_counts.most_common()),
    }


def measure_lane_balance(events: list[Event]) -> dict:
    """How were the lanes actually distributed?"""
    by_agent_type: dict[str, Counter] = defaultdict(Counter)
    by_agent_status: dict[str, Counter] = defaultdict(Counter)
    for ev in events:
        if ev.agent == "system":
            continue
        by_agent_type[ev.agent][ev.type] += 1
        if ev.status:
            by_agent_status[ev.agent][ev.status] += 1
    impl_keywords = re.compile(r"\b(impl|implement|capability_loader|registry|adapter|container)\b", re.I)
    substrate_keywords = re.compile(r"\b(adr|substrate|contract|invariant|index|docs)\b", re.I)
    impl_lane = Counter()
    substrate_lane = Counter()
    for ev in events:
        if ev.agent == "system":
            continue
        text = ev.message + " " + ev.task_id + " " + json.dumps(ev.payload, ensure_ascii=False)
        if impl_keywords.search(text):
            impl_lane[ev.agent] += 1
        if substrate_keywords.search(text):
            substrate_lane[ev.agent] += 1
    return {
        "events_per_agent_per_type": {a: dict(c.most_common(8)) for a, c in by_agent_type.items()},
        "impl_lane_mentions": dict(impl_lane),
        "substrate_lane_mentions": dict(substrate_lane),
    }


# --- Approach B "what-if" projection --------------------------------------

def project_approach_b_savings(threads: dict[str, TaskThread], convergence: dict,
                                pr: dict, findings: dict) -> dict:
    """Translate observed gaps into estimates of what Approach B would have caught."""
    # Multi-agent threads without handshake -> Approach B requires claim first.
    multi_no_hs = sum(1 for t in threads.values()
                      if len({a for a in t.participants if a != "system"}) > 1
                      and t.claim_agent is None)
    # Convergence themes -> if peer had ack'd theme via handshake on first agent's
    # claim, second agent would not have started parallel work.
    duplicated_themes_under_60min = sum(
        1 for c in convergence["themes"] if c["gap_minutes"] < 60.0
    )
    missing_rco = pr["rco_total"] - pr["rco_with_peer_review"]
    # Findings retracted = false-positive yield Approach B's classifier
    # could not directly catch (bridge_classify is a regression-class triage,
    # not a substance reviewer) BUT the handshake-then-RCO step would have
    # forced peer-review before publishing the finding upstream.
    fp_caught_by_pre_rco = findings["retracted_or_disputed"]
    return {
        "duplicate_work_prevented_estimate": duplicated_themes_under_60min,
        "rco_gaps_to_close": missing_rco,
        "false_positive_findings_caught_pre_publish": fp_caught_by_pre_rco,
        "multi_agent_threads_without_handshake": multi_no_hs,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", default=str(EVENTS_PATH))
    ap.add_argument("--since-hours", type=float, default=48.0)
    ap.add_argument("--approach", choices=["B"], default="B")
    ap.add_argument("--out", default="-")
    args = ap.parse_args()

    cutoff = datetime.now(timezone.utc) - timedelta(hours=args.since_hours)
    events = parse_events(Path(args.events), cutoff)
    threads = build_threads(events)

    report = {
        "approach": args.approach,
        "events_path": args.events,
        "since_hours": args.since_hours,
        "cutoff_utc": cutoff.isoformat(),
        "event_count": len(events),
        "unique_task_threads": len(threads),
        "agents": dict(Counter(e.agent for e in events).most_common()),
        "handshake_coverage": measure_handshake_coverage(threads),
        "independent_convergences": measure_convergence(events),
        "pr_lifecycle": measure_pr_lifecycle(events),
        "finding_quality": measure_finding_quality(events),
        "bridge_classify_coverage": measure_classifier_coverage(events),
        "lane_balance": measure_lane_balance(events),
    }
    report["approach_b_projection"] = project_approach_b_savings(
        threads, report["independent_convergences"],
        report["pr_lifecycle"], report["finding_quality"],
    )
    body = json.dumps(report, indent=2, ensure_ascii=False, default=str)
    if args.out == "-":
        print(body)
    else:
        Path(args.out).write_text(body, encoding="utf-8")
        print(f"wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
