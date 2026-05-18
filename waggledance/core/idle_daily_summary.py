# SPDX-License-Identifier: BUSL-1.1
"""Idle autonomous-merge daily summary builder.

Aggregates the day's autonomous-merge activity into an operator-readable
markdown handoff. Source records are bridge events on the .agent-bridge
stream plus optional MAGMA receipt-bundle paths.

This module is intentionally read-only with respect to the bridge stream and
git history. It does not modify, append, or otherwise mutate either. The
companion CLI ``tools/idle_daily_summary.py`` writes the rendered markdown
under ``docs/handoffs/`` when invoked with ``--emit``.

Charter alignment: this module does not implement any auto-merge logic. It
only summarises auto-merges that ``tools/idle_consensus_to_pr.py`` (Slice 5b,
future) will perform. Until Slice 5b ships, the summary lists zero auto-merges
and shows only DRAFT pull requests pending operator review.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EVENTS_PATH = ROOT / ".agent-bridge" / "shared" / "events.jsonl"
DEFAULT_HANDOFF_DIR = ROOT / "docs" / "handoffs"
DEFAULT_DAILY_QUOTA = 5
PRIVATE_MARKERS = ("PRIVATE_MARKER", "_DO_NOT_LEAK")


@dataclass(frozen=True)
class AutoMergeEntry:
    """One autonomous-merge entry in the daily summary."""

    pr_number: int
    consensus_proposal_id: str
    pr_title: str
    merged_at_utc: str
    merge_commit_sha: str
    receipt_bundle_path: str | None = None
    revert_command: str = field(default="")


@dataclass(frozen=True)
class PendingDraftEntry:
    """One DRAFT pull request kept for operator review."""

    pr_number: int
    consensus_proposal_id: str
    pr_title: str
    created_at_utc: str
    pending_reason: str


@dataclass(frozen=True)
class DailySummary:
    """Aggregated autonomous-merge state for one UTC day."""

    utc_date: str
    auto_merges: tuple[AutoMergeEntry, ...]
    pending_drafts: tuple[PendingDraftEntry, ...]
    quota_used: int
    quota_total: int


def build_daily_summary(
    *,
    utc_date: str,
    events: Sequence[Mapping[str, object]],
    daily_quota: int = DEFAULT_DAILY_QUOTA,
) -> DailySummary:
    """Aggregate bridge events into a daily summary for the supplied UTC date.

    The events sequence is expected to be the chronological order from
    ``.agent-bridge/shared/events.jsonl``. Auto-merges and pending DRAFTs are
    inferred from ``done`` events carrying merge metadata in their payload.
    """
    auto_merges: list[AutoMergeEntry] = []
    pending_drafts: list[PendingDraftEntry] = []

    for event in events:
        ts = str(event.get("ts_utc", ""))
        if not ts.startswith(utc_date):
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        kind = _classify_event(event, payload)
        if kind == "auto_merge":
            _assert_no_private_markers(event)
            entry = _build_auto_merge_entry(event, payload)
            if entry is not None:
                auto_merges.append(entry)
        elif kind == "pending_draft":
            _assert_no_private_markers(event)
            draft = _build_pending_draft_entry(event, payload)
            if draft is not None:
                pending_drafts.append(draft)

    return DailySummary(
        utc_date=utc_date,
        auto_merges=tuple(auto_merges),
        pending_drafts=tuple(pending_drafts),
        quota_used=len(auto_merges),
        quota_total=daily_quota,
    )


def render_summary_markdown(summary: DailySummary) -> str:
    """Render the daily summary into operator-readable markdown."""
    _assert_no_private_markers(summary)
    lines: list[str] = []
    lines.append(f"# Idle Auto-Merges: {summary.utc_date}")
    lines.append("")
    lines.append(
        f"{summary.quota_used} auto-merge(s) this UTC day. "
        f"Rate-limit: {summary.quota_used}/{summary.quota_total}."
    )
    lines.append("")

    if summary.auto_merges:
        lines.append("## Auto-merged")
        lines.append("")
        for entry in summary.auto_merges:
            lines.append(f"### PR #{entry.pr_number}: {entry.pr_title}")
            lines.append(f"- Consensus reached: {entry.consensus_proposal_id}")
            lines.append(f"- Merged at: {entry.merged_at_utc}")
            lines.append(f"- Commit: {entry.merge_commit_sha}")
            if entry.receipt_bundle_path:
                lines.append(f"- Receipt bundle: {entry.receipt_bundle_path}")
            if entry.revert_command:
                lines.append(f"- Revert: `{entry.revert_command}`")
            lines.append("")
    else:
        lines.append("## Auto-merged")
        lines.append("")
        lines.append("_No autonomous merges this UTC day._")
        lines.append("")

    if summary.pending_drafts:
        lines.append("## Operator review required (DRAFT pull requests)")
        lines.append("")
        for draft in summary.pending_drafts:
            lines.append(f"### PR #{draft.pr_number}: {draft.pr_title}")
            lines.append(f"- Consensus reached: {draft.consensus_proposal_id}")
            lines.append(f"- Created at: {draft.created_at_utc}")
            lines.append(f"- Reason held: {draft.pending_reason}")
            lines.append("")
    else:
        lines.append("## Operator review required (DRAFT pull requests)")
        lines.append("")
        lines.append("_No pending DRAFT pull requests this UTC day._")
        lines.append("")

    return "\n".join(lines)


def write_summary_file(
    summary: DailySummary,
    handoff_dir: Path | None = None,
) -> Path:
    """Write the markdown summary to ``docs/handoffs/idle_auto_merges_{date}.md``.

    Returns the path of the written file. Idempotent: existing file is
    overwritten so the summary always reflects the latest bridge state.
    """
    out_dir = handoff_dir or DEFAULT_HANDOFF_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"idle_auto_merges_{summary.utc_date}.md"
    out_path.write_text(render_summary_markdown(summary), encoding="utf-8")
    return out_path


def read_bridge_events(events_path: Path) -> list[dict[str, object]]:
    """Read newline-delimited JSON bridge events from disk."""
    if not events_path.exists():
        return []
    events: list[dict[str, object]] = []
    for line_no, line in enumerate(events_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SummaryEventError(
                f"invalid bridge event JSON at line {line_no}: {exc.msg}"
            ) from exc
        if isinstance(event, dict):
            events.append(event)
    return events


def today_utc_iso() -> str:
    """Return the current UTC date as an ISO-8601 calendar date string."""
    return datetime.now(timezone.utc).date().isoformat()


class SummaryPrivacyError(ValueError):
    """Raised when a daily summary input contains a private canary marker."""


class SummaryEventError(ValueError):
    """Raised when bridge events cannot be parsed safely."""


def _assert_no_private_markers(value: object) -> None:
    marker = _find_private_marker(value)
    if marker is not None:
        raise SummaryPrivacyError(f"privacy marker refused in daily summary input: {marker}")


def _find_private_marker(value: object) -> str | None:
    if isinstance(value, str):
        for marker in PRIVATE_MARKERS:
            if marker in value:
                return marker
        return None
    if is_dataclass(value) and not isinstance(value, type):
        return _find_private_marker(asdict(value))
    if isinstance(value, Mapping):
        for key, item in value.items():
            marker = _find_private_marker(key)
            if marker is not None:
                return marker
            marker = _find_private_marker(item)
            if marker is not None:
                return marker
        return None
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        for item in value:
            marker = _find_private_marker(item)
            if marker is not None:
                return marker
    return None


def _classify_event(
    event: Mapping[str, object],
    payload: Mapping[str, object],
) -> str:
    event_type = str(event.get("type", ""))
    status = str(event.get("status", ""))
    if event_type == "done" and "auto_merge" in status:
        return "auto_merge"
    if isinstance(payload, Mapping) and bool(payload.get("auto_merged")):
        return "auto_merge"
    if isinstance(payload, Mapping) and bool(payload.get("draft_pending_operator")):
        return "pending_draft"
    if event_type == "message" and "draft_pending" in status:
        return "pending_draft"
    return ""


def _build_auto_merge_entry(
    event: Mapping[str, object],
    payload: Mapping[str, object],
) -> AutoMergeEntry | None:
    pr_number = payload.get("pr_number") if isinstance(payload, Mapping) else None
    if not isinstance(pr_number, int):
        match = re.search(r"PR\s*#(\d+)", str(event.get("message", "")))
        if not match:
            return None
        pr_number = int(match.group(1))
    consensus_id = (
        str(payload.get("consensus_proposal_id", ""))
        if isinstance(payload, Mapping)
        else ""
    )
    pr_title = (
        str(payload.get("pr_title", ""))
        if isinstance(payload, Mapping)
        else ""
    )
    if not pr_title:
        pr_title = str(event.get("status", ""))
    merge_commit = (
        str(payload.get("merge_commit_sha", ""))
        if isinstance(payload, Mapping)
        else ""
    )
    receipt_bundle = (
        payload.get("receipt_bundle_path") if isinstance(payload, Mapping) else None
    )
    return AutoMergeEntry(
        pr_number=pr_number,
        consensus_proposal_id=consensus_id,
        pr_title=pr_title,
        merged_at_utc=str(event.get("ts_utc", "")),
        merge_commit_sha=merge_commit,
        receipt_bundle_path=str(receipt_bundle) if receipt_bundle else None,
        revert_command=f"git revert {merge_commit}" if merge_commit else "",
    )


def _build_pending_draft_entry(
    event: Mapping[str, object],
    payload: Mapping[str, object],
) -> PendingDraftEntry | None:
    pr_number = payload.get("pr_number") if isinstance(payload, Mapping) else None
    if not isinstance(pr_number, int):
        match = re.search(r"PR\s*#(\d+)", str(event.get("message", "")))
        if not match:
            return None
        pr_number = int(match.group(1))
    consensus_id = (
        str(payload.get("consensus_proposal_id", ""))
        if isinstance(payload, Mapping)
        else ""
    )
    pr_title = (
        str(payload.get("pr_title", ""))
        if isinstance(payload, Mapping)
        else ""
    )
    if not pr_title:
        pr_title = str(event.get("status", ""))
    pending_reason = (
        str(payload.get("pending_reason", ""))
        if isinstance(payload, Mapping)
        else ""
    )
    return PendingDraftEntry(
        pr_number=pr_number,
        consensus_proposal_id=consensus_id,
        pr_title=pr_title,
        created_at_utc=str(event.get("ts_utc", "")),
        pending_reason=pending_reason,
    )
