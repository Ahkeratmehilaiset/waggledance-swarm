# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Mapping

import pytest

from tools.governance_throughput_report import (
    INSUFFICIENT_DATA_THRESHOLD,
    compute_governance_throughput_report,
    main,
)


NOW = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)


def _ev(
    *,
    ts: datetime,
    agent: str,
    type_: str,
    status: str,
    task_id: str = "",
    message: str = "",
    to: str = "",
    payload: Mapping[str, Any] | None = None,
) -> dict:
    return {
        "ts_utc": ts.isoformat(),
        "agent": agent,
        "type": type_,
        "task_id": task_id,
        "status": status,
        "message": message,
        "to": to,
        "severity": "",
        "paths": [],
        "write_scope": [],
        "run_id": "",
        "pid": 0,
        "cwd": "",
        "payload": dict(payload or {}),
    }


def _pr_thread(
    *,
    task_id: str,
    pr_number: int,
    start: datetime,
    proposal_agent: str = "claude",
    rco_agent: str = "codex",
    rco_status: str = "rco_approved",
    add_changes_round: bool = False,
    changes_agent: str = "codex",
    add_merge: bool = False,
    add_revert: bool = False,
) -> list[dict]:
    events = []
    events.append(_ev(
        ts=start, agent=proposal_agent, type_="message",
        status="proposal",
        task_id=task_id,
        message=f"proposing pr#{pr_number}",
    ))
    if add_changes_round:
        events.append(_ev(
            ts=start + timedelta(minutes=10),
            agent=changes_agent, type_="finding",
            status="changes_requested",
            task_id=task_id,
            message=f"changes requested on pr#{pr_number}",
        ))
        events.append(_ev(
            ts=start + timedelta(minutes=20),
            agent=proposal_agent, type_="message",
            status="fix_pushed",
            task_id=task_id,
            message=f"fix pushed for pr#{pr_number}",
        ))
    events.append(_ev(
        ts=start + timedelta(minutes=30),
        agent=rco_agent, type_="decision",
        status=rco_status,
        task_id=task_id,
        message=f"rco for pr#{pr_number}",
    ))
    if add_merge:
        events.append(_ev(
            ts=start + timedelta(minutes=40),
            agent=rco_agent, type_="done",
            status="merged",
            task_id=task_id,
            message=f"pr#{pr_number} merged",
        ))
    if add_revert:
        events.append(_ev(
            ts=start + timedelta(hours=2),
            agent="claude", type_="done",
            status="revert",
            task_id=f"revert-{task_id}",
            message=f"pr#{pr_number} reverted",
        ))
    return events


# ---------------------------------------------------------------------------
# input validation
# ---------------------------------------------------------------------------


def test_window_days_negative_rejected() -> None:
    with pytest.raises(ValueError):
        compute_governance_throughput_report(
            events=[], now_utc=NOW, window_days=-1,
        )


def test_threshold_zero_rejected() -> None:
    with pytest.raises(ValueError):
        compute_governance_throughput_report(
            events=[], now_utc=NOW, insufficient_threshold=0,
        )


# ---------------------------------------------------------------------------
# empty / insufficient-data behavior
# ---------------------------------------------------------------------------


def test_empty_events_returns_all_insufficient() -> None:
    report = compute_governance_throughput_report(
        events=[], now_utc=NOW,
    )
    assert report["decision"] == "governance_throughput_report"
    assert report["event_count_in_window"] == 0
    statuses = {m["name"]: m["status"] for m in report["metrics"]}
    # All measurable metrics report insufficient_data; the shadow→live one
    # stays deferred until lifecycle events carry payload.risk_class.
    for name in (
        "proposal_to_rco_latency",
        "unsafe_diff_blocked_rate",
        "regression_catch_rate",
        "synthetic_defect_catch_by_reviewer",
        "postmerge_failure_rate",
        "review_disagreement_rate",
        "promotion_reversal_rate",
    ):
        assert statuses[name] == "insufficient_data"
    assert statuses["shadow_to_live_latency_per_risk_class"] == "deferred"


def test_below_threshold_keeps_metric_insufficient() -> None:
    events = _pr_thread(
        task_id="task-1", pr_number=101, start=NOW - timedelta(hours=1)
    )
    report = compute_governance_throughput_report(
        events=events, now_utc=NOW, insufficient_threshold=5,
    )
    m = {x["name"]: x for x in report["metrics"]}
    assert m["proposal_to_rco_latency"]["status"] == "insufficient_data"
    assert m["proposal_to_rco_latency"]["sample_size"] == 1
    assert m["unsafe_diff_blocked_rate"]["sample_size"] == 1


# ---------------------------------------------------------------------------
# latency metric
# ---------------------------------------------------------------------------


def test_proposal_to_rco_latency_computes_seconds_when_sample_sufficient() -> None:
    events = []
    for i in range(6):
        events.extend(_pr_thread(
            task_id=f"task-{i}", pr_number=200 + i,
            start=NOW - timedelta(hours=i + 1),
        ))
    report = compute_governance_throughput_report(
        events=events, now_utc=NOW, insufficient_threshold=5,
    )
    latency = next(
        m for m in report["metrics"] if m["name"] == "proposal_to_rco_latency"
    )
    assert latency["status"] == "ok"
    assert latency["sample_size"] == 6
    # _pr_thread places RCO 30 minutes after proposal -> p50 ~= 1800s.
    assert latency["p50_seconds"] == 1800.0


# ---------------------------------------------------------------------------
# blocked rate + catch rate
# ---------------------------------------------------------------------------


def test_unsafe_diff_blocked_rate_counts_change_requests() -> None:
    events = []
    # 5 PRs total, 3 with changes_requested round.
    for i in range(5):
        events.extend(_pr_thread(
            task_id=f"task-{i}", pr_number=300 + i,
            start=NOW - timedelta(hours=i + 1),
            add_changes_round=(i < 3),
        ))
    report = compute_governance_throughput_report(
        events=events, now_utc=NOW, insufficient_threshold=5,
    )
    blocked = next(
        m for m in report["metrics"]
        if m["name"] == "unsafe_diff_blocked_rate"
    )
    assert blocked["status"] == "ok"
    assert blocked["sample_size"] == 5
    assert blocked["blocked_task_count"] == 3
    assert blocked["rate_pct"] == 60.0


def test_regression_catch_rate_counts_catch_then_revise() -> None:
    events = []
    for i in range(5):
        # All 5 have catch+revise (changes_requested followed by rco pass).
        events.extend(_pr_thread(
            task_id=f"task-{i}", pr_number=400 + i,
            start=NOW - timedelta(hours=i + 1),
            add_changes_round=True,
        ))
    report = compute_governance_throughput_report(
        events=events, now_utc=NOW, insufficient_threshold=5,
    )
    catch = next(
        m for m in report["metrics"]
        if m["name"] == "regression_catch_rate"
    )
    assert catch["status"] == "ok"
    assert catch["catch_and_revise_count"] == 5
    assert catch["rate_pct"] == 100.0


# ---------------------------------------------------------------------------
# reviewer split
# ---------------------------------------------------------------------------


def test_synthetic_defect_catch_split_by_reviewer() -> None:
    events = []
    # 2 codex-only, 1 claude-only, 2 both -> diversity_pct = 40%
    layouts = [
        ("codex", None),
        ("codex", None),
        ("claude", None),
        ("codex", "claude"),
        ("claude", "codex"),
    ]
    for i, (a1, a2) in enumerate(layouts):
        task_id = f"task-{i}"
        start = NOW - timedelta(hours=i + 1)
        thread = _pr_thread(
            task_id=task_id, pr_number=500 + i, start=start,
            add_changes_round=True, changes_agent=a1,
        )
        if a2 is not None:
            thread.append(_ev(
                ts=start + timedelta(minutes=12),
                agent=a2, type_="finding",
                status="changes_requested",
                task_id=task_id,
                message=f"second reviewer changes_requested on pr#{500 + i}",
            ))
        events.extend(thread)
    report = compute_governance_throughput_report(
        events=events, now_utc=NOW, insufficient_threshold=5,
    )
    split = next(
        m for m in report["metrics"]
        if m["name"] == "synthetic_defect_catch_by_reviewer"
    )
    assert split["status"] == "ok"
    assert split["sample_size"] == 5
    assert split["by_reviewer"]["both"] == 2
    assert split["by_reviewer"]["claude_only"] == 1
    assert split["by_reviewer"]["codex_only"] == 2
    assert split["diversity_pct"] == 40.0


# ---------------------------------------------------------------------------
# postmerge + reversal
# ---------------------------------------------------------------------------


def test_postmerge_failure_rate_counts_reverts(monkeypatch=None) -> None:
    events = []
    # 5 merged PRs, 1 reverted.
    for i in range(5):
        events.extend(_pr_thread(
            task_id=f"task-{i}", pr_number=600 + i,
            start=NOW - timedelta(hours=i + 1),
            add_merge=True,
            add_revert=(i == 0),
        ))
    report = compute_governance_throughput_report(
        events=events, now_utc=NOW, insufficient_threshold=5,
    )
    pm = next(
        m for m in report["metrics"]
        if m["name"] == "postmerge_failure_rate"
    )
    assert pm["status"] == "ok"
    assert pm["merged_pr_count"] == 5
    assert pm["reverted_pr_count"] == 1
    assert pm["rate_pct"] == 20.0


def test_postmerge_failure_rate_caps_at_100_pct_when_revert_out_of_window() -> None:
    """Codex RCO regression 2026-05-20T16:28:03Z: a revert whose merged PR
    is outside the window must not push the rate above 100%. The out-of-
    window revert reports as a separate field for visibility."""
    events = []
    # 5 in-window merged PRs; only 1 in-window revert (= 20%).
    for i in range(5):
        events.extend(_pr_thread(
            task_id=f"task-{i}", pr_number=700 + i,
            start=NOW - timedelta(hours=i + 1),
            add_merge=True,
            add_revert=(i == 0),
        ))
    # Plus 3 EXTRA reverts referencing PRs whose merge events are NOT
    # in the window. Earlier buggy version counted these in numerator
    # and pushed rate over 100%.
    for stranded_pr in (900, 901, 902):
        events.append(_ev(
            ts=NOW - timedelta(hours=1),
            agent="claude", type_="done",
            status="revert",
            task_id=f"revert-stranded-{stranded_pr}",
            message=f"pr#{stranded_pr} reverted; merge happened before window",
        ))
    report = compute_governance_throughput_report(
        events=events, now_utc=NOW, insufficient_threshold=5,
    )
    pm = next(
        m for m in report["metrics"]
        if m["name"] == "postmerge_failure_rate"
    )
    assert pm["status"] == "ok"
    assert pm["merged_pr_count"] == 5
    assert pm["reverted_pr_count"] == 1
    assert pm["reverted_out_of_window_pr_count"] == 3
    # Rate is in-window-reverts / merged. Capped at 100%.
    assert pm["rate_pct"] == 20.0
    assert pm["rate_pct"] <= 100.0


def test_regression_catch_rate_requires_changes_before_pass_in_time() -> None:
    """Codex RCO regression 2026-05-20T16:28:03Z: event order matters.
    A pass-then-changes_requested thread is NOT catch-and-revise and
    must not score. Only changes_requested STRICTLY BEFORE a subsequent
    rco_pass counts."""
    events = []
    # Three PR threads:
    #  - thread A,B,C,D,E: pass happens BEFORE any changes_requested
    #    -> NOT catch-and-revise, must not count
    #  - To reach the threshold we also add 5 valid catch-and-revise.
    # Inverted: pass first, changes_requested later (must NOT count)
    for i in range(5):
        task_id = f"inverted-{i}"
        start = NOW - timedelta(hours=i + 1)
        events.append(_ev(
            ts=start, agent="claude", type_="message",
            status="proposal",
            task_id=task_id,
            message=f"pr#{2000 + i} proposed",
        ))
        # pass FIRST
        events.append(_ev(
            ts=start + timedelta(minutes=5),
            agent="codex", type_="decision",
            status="rco_approved",
            task_id=task_id,
            message=f"pr#{2000 + i} early pass",
        ))
        # then changes_requested LATER (should be ignored as catch-revise)
        events.append(_ev(
            ts=start + timedelta(minutes=30),
            agent="codex", type_="finding",
            status="changes_requested",
            task_id=task_id,
            message=f"pr#{2000 + i} late changes",
        ))
    # Valid catch-and-revise (changes first, pass later) x5:
    for i in range(5):
        events.extend(_pr_thread(
            task_id=f"valid-{i}", pr_number=2100 + i,
            start=NOW - timedelta(hours=i + 1, minutes=30),
            add_changes_round=True,
        ))
    report = compute_governance_throughput_report(
        events=events, now_utc=NOW, insufficient_threshold=5,
    )
    catch = next(
        m for m in report["metrics"]
        if m["name"] == "regression_catch_rate"
    )
    assert catch["status"] == "ok"
    # 10 PR threads total (5 inverted + 5 valid); only 5 valid count.
    assert catch["sample_size"] == 10
    assert catch["catch_and_revise_count"] == 5
    assert catch["rate_pct"] == 50.0


def test_promotion_reversal_rate_requires_promotion_vocabulary() -> None:
    events = []
    for i in range(5):
        events.append(_ev(
            ts=NOW - timedelta(hours=i + 1),
            agent="codex", type_="decision",
            status=f"promotion_pr_{700+i}",
            task_id=f"prom-{i}",
            message=f"promotion event {i} pr#{700+i}",
        ))
    events.append(_ev(
        ts=NOW - timedelta(minutes=30),
        agent="claude", type_="done",
        status="promotion_rolled_back",
        task_id="prom-rollback",
        message="rolled back promotion for pr#700",
    ))
    report = compute_governance_throughput_report(
        events=events, now_utc=NOW, insufficient_threshold=5,
    )
    prr = next(
        m for m in report["metrics"]
        if m["name"] == "promotion_reversal_rate"
    )
    assert prr["status"] == "ok"
    assert prr["sample_size"] == 6
    assert prr["reversal_event_count"] == 1


# ---------------------------------------------------------------------------
# review disagreement
# ---------------------------------------------------------------------------


def test_review_disagreement_rate_counts_multi_changes() -> None:
    events = []
    for i in range(5):
        task_id = f"task-{i}"
        start = NOW - timedelta(hours=i + 1)
        thread = _pr_thread(
            task_id=task_id, pr_number=800 + i, start=start,
            add_changes_round=True,
        )
        if i < 3:
            thread.append(_ev(
                ts=start + timedelta(minutes=22),
                agent="codex", type_="finding",
                status="rco_changes_requested",
                task_id=task_id,
                message=f"second changes_requested round on pr#{800 + i}",
            ))
        events.extend(thread)
    report = compute_governance_throughput_report(
        events=events, now_utc=NOW, insufficient_threshold=5,
    )
    dis = next(
        m for m in report["metrics"]
        if m["name"] == "review_disagreement_rate"
    )
    assert dis["status"] == "ok"
    assert dis["multi_round_task_count"] == 3
    assert dis["rate_pct"] == 60.0


# ---------------------------------------------------------------------------
# shadow/canary/live lifecycle latency
# ---------------------------------------------------------------------------


def test_shadow_to_live_latency_defers_without_risk_class_payload() -> None:
    events = [
        _ev(ts=NOW - timedelta(hours=2), agent="claude", type_="status",
            status="promotion_shadow", task_id="x",
            message="promotion shadow start"),
        _ev(ts=NOW - timedelta(hours=1), agent="claude", type_="status",
            status="promotion_canary", task_id="x",
            message="promotion canary start"),
        _ev(ts=NOW, agent="claude", type_="done",
            status="promotion_live", task_id="x",
            message="promotion live"),
    ]
    report = compute_governance_throughput_report(
        events=events, now_utc=NOW,
    )
    shadow = next(
        m for m in report["metrics"]
        if m["name"] == "shadow_to_live_latency_per_risk_class"
    )
    assert shadow["status"] == "deferred"
    assert shadow["sample_size"] == 0
    assert shadow["observed_lifecycle_event_count"] == 3


def test_shadow_to_live_latency_computes_by_risk_class() -> None:
    events = []
    layouts = [
        ("low", 60),
        ("low", 120),
        ("low", 180),
        ("medium", 240),
        ("medium", 300),
    ]
    for i, (risk_class, total_minutes) in enumerate(layouts):
        task_id = f"promotion-{i}"
        start = NOW - timedelta(hours=12 - i)
        payload = {"risk_class": risk_class}
        events.extend([
            _ev(
                ts=start,
                agent="claude",
                type_="status",
                status="promotion_shadow",
                task_id=task_id,
                message="promotion shadow start",
                payload=payload,
            ),
            _ev(
                ts=start + timedelta(minutes=total_minutes // 2),
                agent="claude",
                type_="status",
                status="promotion_canary",
                task_id=task_id,
                message="promotion canary start",
                payload=payload,
            ),
            _ev(
                ts=start + timedelta(minutes=total_minutes),
                agent="claude",
                type_="done",
                status="promotion_live",
                task_id=task_id,
                message="promotion live",
                payload=payload,
            ),
        ])

    report = compute_governance_throughput_report(
        events=events, now_utc=NOW, insufficient_threshold=5,
    )
    shadow = next(
        m for m in report["metrics"]
        if m["name"] == "shadow_to_live_latency_per_risk_class"
    )
    assert shadow["status"] == "ok"
    assert shadow["sample_size"] == 5
    assert shadow["risk_class_count"] == 2
    assert shadow["shadow_to_live_seconds"]["p50_seconds"] == 10800.0
    assert shadow["shadow_to_live_seconds"]["min_seconds"] == 3600.0
    assert shadow["shadow_to_live_seconds"]["max_seconds"] == 18000.0
    low = shadow["by_risk_class"]["low"]
    medium = shadow["by_risk_class"]["medium"]
    assert low["sample_size"] == 3
    assert low["shadow_to_live_seconds"]["p50_seconds"] == 7200.0
    assert medium["sample_size"] == 2
    assert medium["canary_to_live_seconds"]["max_seconds"] == 9000.0


def test_shadow_to_live_latency_below_threshold_is_insufficient() -> None:
    start = NOW - timedelta(hours=2)
    events = [
        _ev(
            ts=start,
            agent="claude",
            type_="status",
            status="promotion_shadow",
            task_id="promotion-one",
            payload={"risk_class": "low"},
        ),
        _ev(
            ts=start + timedelta(minutes=30),
            agent="claude",
            type_="status",
            status="promotion_canary",
            task_id="promotion-one",
            payload={"risk_class": "low"},
        ),
        _ev(
            ts=start + timedelta(minutes=60),
            agent="claude",
            type_="done",
            status="promotion_live",
            task_id="promotion-one",
            payload={"risk_class": "low"},
        ),
    ]

    report = compute_governance_throughput_report(
        events=events, now_utc=NOW, insufficient_threshold=5,
    )
    shadow = next(
        m for m in report["metrics"]
        if m["name"] == "shadow_to_live_latency_per_risk_class"
    )
    assert shadow["status"] == "insufficient_data"
    assert shadow["sample_size"] == 1


# ---------------------------------------------------------------------------
# window filtering
# ---------------------------------------------------------------------------


def test_window_days_filters_old_events() -> None:
    # 5 PRs inside 7d window, 5 PRs outside (30 days ago).
    inside = []
    outside = []
    for i in range(5):
        inside.extend(_pr_thread(
            task_id=f"in-{i}", pr_number=900 + i,
            start=NOW - timedelta(days=1),
        ))
        outside.extend(_pr_thread(
            task_id=f"out-{i}", pr_number=1000 + i,
            start=NOW - timedelta(days=30),
        ))
    report = compute_governance_throughput_report(
        events=inside + outside, now_utc=NOW, window_days=7,
    )
    latency = next(
        m for m in report["metrics"]
        if m["name"] == "proposal_to_rco_latency"
    )
    # Only the 5 inside the 7d window count.
    assert latency["sample_size"] == 5


def test_window_days_zero_is_all_time() -> None:
    events = []
    for i in range(5):
        events.extend(_pr_thread(
            task_id=f"t-{i}", pr_number=1100 + i,
            start=NOW - timedelta(days=60),
        ))
    report = compute_governance_throughput_report(
        events=events, now_utc=NOW, window_days=0,
    )
    assert report["window_label"] == "all-time"
    latency = next(
        m for m in report["metrics"]
        if m["name"] == "proposal_to_rco_latency"
    )
    assert latency["sample_size"] == 5


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


def test_cli_main_emits_json(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        "\n".join(
            json.dumps(e, sort_keys=True)
            for e in _pr_thread(
                task_id="task-cli", pr_number=1200,
                start=NOW - timedelta(hours=1),
            )
        ),
        encoding="utf-8",
    )
    exit_code = main(
        [
            "--events", str(events_path),
            "--now", "2026-05-20T12:00:00Z",
            "--window-days", "7",
            "--json",
        ]
    )
    assert exit_code == 0
    out = capsys.readouterr().out.strip()
    parsed = json.loads(out)
    assert parsed["decision"] == "governance_throughput_report"
    names = {m["name"] for m in parsed["metrics"]}
    assert "proposal_to_rco_latency" in names
    assert "shadow_to_live_latency_per_risk_class" in names
