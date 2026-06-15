from __future__ import annotations

import json
from pathlib import Path

import pytest

from waggledance.core.idle_daily_summary import (
    DEFAULT_DAILY_QUOTA,
    AutoMergeEntry,
    DailySummary,
    PendingDraftEntry,
    SummaryEventError,
    SummaryPrivacyError,
    build_daily_summary,
    read_bridge_events,
    render_summary_markdown,
    resolve_events_path,
    write_summary_file,
)


def _auto_merge_event(
    *,
    ts: str,
    pr: int,
    title: str = "feat(idle): test merge",
    proposal_id: str = "idle-prop-test-001",
    merge_commit: str = "abc1234",
    receipt: str | None = None,
) -> dict:
    return {
        "ts_utc": ts,
        "agent": "codex",
        "type": "done",
        "status": "merged_main_ci_green_auto_merge",
        "to": "claude",
        "task_id": "idle-auto-merge-test",
        "payload": {
            "auto_merged": True,
            "pr_number": pr,
            "pr_title": title,
            "consensus_proposal_id": proposal_id,
            "merge_commit_sha": merge_commit,
            **({"receipt_bundle_path": receipt} if receipt else {}),
        },
    }


def _pending_draft_event(
    *,
    ts: str,
    pr: int,
    title: str = "feat(idle): test draft",
    proposal_id: str = "idle-prop-test-002",
    reason: str = "denylist hit",
) -> dict:
    return {
        "ts_utc": ts,
        "agent": "codex",
        "type": "message",
        "status": "draft_pending_operator_review",
        "to": "claude",
        "task_id": "idle-auto-merge-test",
        "payload": {
            "draft_pending_operator": True,
            "pr_number": pr,
            "pr_title": title,
            "consensus_proposal_id": proposal_id,
            "pending_reason": reason,
        },
    }


def _write_events(path: Path, events: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in events) + "\n",
        encoding="utf-8",
    )


def test_empty_bridge_produces_zero_quota_summary() -> None:
    summary = build_daily_summary(utc_date="2026-05-18", events=[])
    assert summary.utc_date == "2026-05-18"
    assert summary.auto_merges == ()
    assert summary.pending_drafts == ()
    assert summary.quota_used == 0
    assert summary.quota_total == DEFAULT_DAILY_QUOTA


def test_single_auto_merge_counted_for_quota() -> None:
    events = [
        _auto_merge_event(
            ts="2026-05-18T03:14:22.123456Z",
            pr=470,
            title="feat(magma): shared bundle indexer",
            proposal_id="idle-prop-20260518t031422-claude",
            merge_commit="a1b2c3d4",
            receipt=".codex-audit/idle-receipts/2026-05-18-pr470/manifest.json",
        )
    ]
    summary = build_daily_summary(utc_date="2026-05-18", events=events)
    assert summary.quota_used == 1
    assert len(summary.auto_merges) == 1
    entry = summary.auto_merges[0]
    assert entry.pr_number == 470
    assert entry.consensus_proposal_id == "idle-prop-20260518t031422-claude"
    assert entry.receipt_bundle_path is not None
    assert entry.revert_command == "git revert a1b2c3d4"


def test_pending_draft_listed_separately() -> None:
    events = [
        _pending_draft_event(
            ts="2026-05-18T04:00:00Z",
            pr=472,
            title="docs(idle): clarify Deferred section",
            proposal_id="idle-prop-test-072",
            reason="denylist hit on IDLE_PROTOCOL_V1.md",
        )
    ]
    summary = build_daily_summary(utc_date="2026-05-18", events=events)
    assert summary.quota_used == 0
    assert len(summary.pending_drafts) == 1
    draft = summary.pending_drafts[0]
    assert draft.pr_number == 472
    assert draft.pending_reason == "denylist hit on IDLE_PROTOCOL_V1.md"


def test_events_from_other_days_ignored() -> None:
    events = [
        _auto_merge_event(ts="2026-05-17T12:00:00Z", pr=400),
        _auto_merge_event(ts="2026-05-18T01:00:00Z", pr=470),
        _auto_merge_event(ts="2026-05-19T01:00:00Z", pr=500),
    ]
    summary = build_daily_summary(utc_date="2026-05-18", events=events)
    assert summary.quota_used == 1
    assert summary.auto_merges[0].pr_number == 470


def test_render_markdown_lists_both_sections() -> None:
    summary = DailySummary(
        utc_date="2026-05-18",
        auto_merges=(
            AutoMergeEntry(
                pr_number=470,
                consensus_proposal_id="idle-prop-001",
                pr_title="feat(magma): shared bundle indexer",
                merged_at_utc="2026-05-18T03:18:45Z",
                merge_commit_sha="abc1234",
                receipt_bundle_path=".codex-audit/idle-receipts/2026-05-18-pr470/manifest.json",
                revert_command="git revert abc1234",
            ),
        ),
        pending_drafts=(
            PendingDraftEntry(
                pr_number=472,
                consensus_proposal_id="idle-prop-002",
                pr_title="docs(idle): clarify Deferred",
                created_at_utc="2026-05-18T04:00:00Z",
                pending_reason="denylist hit on IDLE_PROTOCOL_V1.md",
            ),
        ),
        quota_used=1,
        quota_total=5,
    )
    md = render_summary_markdown(summary)
    assert "# Idle Auto-Merges: 2026-05-18" in md
    assert "1 auto-merge(s) this UTC day. Rate-limit: 1/5." in md
    assert "PR #470" in md
    assert "git revert abc1234" in md
    assert "PR #472" in md
    assert "denylist hit on IDLE_PROTOCOL_V1.md" in md


def test_empty_day_renders_explicit_no_merges_message() -> None:
    summary = DailySummary(
        utc_date="2026-05-18",
        auto_merges=(),
        pending_drafts=(),
        quota_used=0,
        quota_total=5,
    )
    md = render_summary_markdown(summary)
    assert "_No autonomous merges this UTC day._" in md
    assert "_No pending DRAFT pull requests this UTC day._" in md


def test_write_summary_file_creates_handoff_with_expected_name(tmp_path: Path) -> None:
    summary = DailySummary(
        utc_date="2026-05-18",
        auto_merges=(),
        pending_drafts=(),
        quota_used=0,
        quota_total=5,
    )
    out_path = write_summary_file(summary, handoff_dir=tmp_path)
    assert out_path.name == "idle_auto_merges_2026-05-18.md"
    assert out_path.exists()
    content = out_path.read_text(encoding="utf-8")
    assert "Idle Auto-Merges: 2026-05-18" in content


def test_read_bridge_events_refuses_malformed_lines(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        '{"ts_utc": "2026-05-18T01:00:00Z", "agent": "claude"}\n'
        'not valid json\n'
        '{"ts_utc": "2026-05-18T02:00:00Z", "agent": "codex"}\n',
        encoding="utf-8",
    )
    with pytest.raises(SummaryEventError, match="line 2"):
        read_bridge_events(events_path)


def test_read_bridge_events_missing_file_returns_empty(tmp_path: Path) -> None:
    events_path = tmp_path / "missing.jsonl"
    events = read_bridge_events(events_path)
    assert events == []


def test_read_bridge_events_uses_runtime_bridge_root_env_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_bridge = tmp_path / "runtime" / ".agent-bridge"
    runtime_events = runtime_bridge / "shared" / "events.jsonl"
    runtime_events.parent.mkdir(parents=True)
    runtime_event = _auto_merge_event(ts="2026-05-18T01:00:00Z", pr=470)
    _write_events(runtime_events, [runtime_event])

    shadow_root = tmp_path / "shadow"
    shadow_events = shadow_root / ".agent-bridge" / "shared" / "events.jsonl"
    shadow_events.parent.mkdir(parents=True)
    _write_events(
        shadow_events,
        [_auto_merge_event(ts="2026-05-18T01:00:00Z", pr=999)],
    )

    monkeypatch.chdir(shadow_root)
    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(runtime_bridge))
    monkeypatch.delenv("AGENT_BRIDGE_ROOT", raising=False)

    assert resolve_events_path() == runtime_events
    events = read_bridge_events()
    assert events == [runtime_event]


def test_idle_daily_summary_cli_uses_runtime_bridge_root_env_by_default(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools import idle_daily_summary as cli

    runtime_bridge = tmp_path / "runtime" / ".agent-bridge"
    runtime_events = runtime_bridge / "shared" / "events.jsonl"
    runtime_events.parent.mkdir(parents=True)
    _write_events(
        runtime_events,
        [_auto_merge_event(ts="2026-05-18T01:00:00Z", pr=470)],
    )

    shadow_root = tmp_path / "shadow"
    shadow_events = shadow_root / ".agent-bridge" / "shared" / "events.jsonl"
    shadow_events.parent.mkdir(parents=True)
    _write_events(
        shadow_events,
        [_pending_draft_event(ts="2026-05-18T01:00:00Z", pr=999)],
    )

    monkeypatch.chdir(shadow_root)
    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(runtime_bridge))
    monkeypatch.delenv("AGENT_BRIDGE_ROOT", raising=False)

    rc = cli.main(["--utc-date", "2026-05-18", "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["decision"] == "dry_run"
    assert payload["auto_merge_count"] == 1
    assert payload["pending_draft_count"] == 0


def test_quota_two_merges_counted() -> None:
    events = [
        _auto_merge_event(ts="2026-05-18T01:00:00Z", pr=470),
        _auto_merge_event(ts="2026-05-18T02:00:00Z", pr=471),
    ]
    summary = build_daily_summary(utc_date="2026-05-18", events=events)
    assert summary.quota_used == 2
    assert {entry.pr_number for entry in summary.auto_merges} == {470, 471}


def test_pr_number_extracted_from_message_when_payload_missing() -> None:
    events = [
        {
            "ts_utc": "2026-05-18T01:00:00Z",
            "agent": "codex",
            "type": "done",
            "status": "merged_main_ci_green_auto_merge",
            "to": "claude",
            "task_id": "idle-auto-merge-test",
            "message": "PR #480 auto-merged",
        }
    ]
    summary = build_daily_summary(utc_date="2026-05-18", events=events)
    assert summary.quota_used == 1
    assert summary.auto_merges[0].pr_number == 480


def test_event_without_pr_number_skipped() -> None:
    events = [
        {
            "ts_utc": "2026-05-18T01:00:00Z",
            "agent": "codex",
            "type": "done",
            "status": "merged_main_ci_green_auto_merge",
            "message": "some unrelated done event with no PR reference",
            "payload": {"auto_merged": True},
        }
    ]
    summary = build_daily_summary(utc_date="2026-05-18", events=events)
    assert summary.quota_used == 0


def test_private_marker_in_auto_merge_event_refused() -> None:
    events = [
        _auto_merge_event(
            ts="2026-05-18T01:00:00Z",
            pr=470,
            title="feat(idle): PRIVATE_MARKER",
        )
    ]
    with pytest.raises(SummaryPrivacyError):
        build_daily_summary(utc_date="2026-05-18", events=events)


def test_private_marker_in_pending_draft_event_refused() -> None:
    events = [
        _pending_draft_event(
            ts="2026-05-18T01:00:00Z",
            pr=471,
            reason="_DO_NOT_LEAK in candidate diff",
        )
    ]
    with pytest.raises(SummaryPrivacyError):
        build_daily_summary(utc_date="2026-05-18", events=events)


def test_render_refuses_private_marker_in_manual_summary() -> None:
    summary = DailySummary(
        utc_date="2026-05-18",
        auto_merges=(
            AutoMergeEntry(
                pr_number=470,
                consensus_proposal_id="idle-prop-001",
                pr_title="feat(idle): PRIVATE_MARKER",
                merged_at_utc="2026-05-18T03:18:45Z",
                merge_commit_sha="abc1234",
            ),
        ),
        pending_drafts=(),
        quota_used=1,
        quota_total=5,
    )
    with pytest.raises(SummaryPrivacyError):
        render_summary_markdown(summary)
