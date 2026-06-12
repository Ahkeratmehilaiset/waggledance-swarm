# SPDX-License-Identifier: BUSL-1.1
"""Dry-run-first auto-merge gate for idle consensus pull requests.

The tool verifies a pre-collected PR status snapshot and composes the exact
``gh pr merge --match-head-commit`` command. Default mode is a report only.
Only explicit ``--apply`` invokes the merge command, and tests inject a runner
so no GitHub call is made by the suite. Idle-charter merges are intentionally
not blocked on an operator approval flag; the charter's explicit merge gates
are the authority for this narrow idle-consensus path.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.idle_check import DEFAULT_EVENTS_PATH  # noqa: E402
from tools.check_bridge_changes_requested import (  # noqa: E402
    check_bridge_clear_to_merge,
)
from tools.check_rco_pass_present import (  # noqa: E402
    DEFAULT_RCO_AGENTS,
    check_rco_pass_present,
)
from tools.idle_consensus_artifact import (  # noqa: E402
    DEFAULT_OUT_DIR as DEFAULT_ARTIFACT_OUT_DIR,
    write_idle_consensus_artifact,
)
from waggledance.core.idle_consensus_charter import (  # noqa: E402
    DEFAULT_CHARTER_PATH,
    evaluate_diff_content,
    evaluate_paths,
    load_charter,
)
from waggledance.core.bridge_identity_registry import (  # noqa: E402
    bridge_identity_binding_status,
    load_bridge_identity_registry,
)

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
BRIDGE_AGENT_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,32}$")
PRIVATE_MARKERS = ("PRIVATE_MARKER", "_DO_NOT_LEAK")
PASS_STATES = {"pass", "passed", "success", "successful", "ok"}
MERGEABLE_STATES = {"clean", "mergeable", "MERGEABLE", "CLEAN"}

# --- Bridge-consensus approver (T0b) -------------------------------------
# Per docs/architecture/BRIDGE_CONSENSUS_APPROVAL_V1.md + CLAUDE.md Rule 9a.
# A fail-closed three-identity consensus replaces the per-action operator
# query for autonomous MERGE. Any missing/duplicate/stale/forged signal
# refuses; silence never default-allows.
BRIDGE_CONSENSUS_LEAD = "codex-lead-1"
BRIDGE_CONSENSUS_TOOLS = "codex-tools-1"
BRIDGE_CONSENSUS_RCO = "claude-rco-1"
BRIDGE_CONSENSUS_RCO_AGENTS = DEFAULT_RCO_AGENTS
BRIDGE_CONSENSUS_AUTHOR_AGENTS = (
    BRIDGE_CONSENSUS_LEAD,
    BRIDGE_CONSENSUS_TOOLS,
    *BRIDGE_CONSENSUS_RCO_AGENTS,
)
DECISION_EVENT_TYPES = frozenset({"decision", "rco_review", "finding"})
# Note: a bare "acknowledged" is deliberately NOT a build-consensus vote — an
# ack of receipt is not an approval (RCO T0b note N2).
BUILD_CONSENSUS_STATUSES = frozenset(
    {
        "approved",
        "build_consensus",
        "build_consensus_pass",
        "concur",
        "concurred",
        "agree",
        "agreed",
    }
)
RCO_PASS_STATUSES = frozenset(
    {
        "rco_pass",
    }
)
LEAD_STALL_KEEPALIVE_TYPES = frozenset({"heartbeat", "liveness"})
DEFAULT_LEAD_STALL_THRESHOLD_MINUTES = 90.0
# Mirrors tools/check_bridge_changes_requested.BLOCKING_STATUSES so the
# consensus approver and the veto preflight agree on what a block looks like.
CONSENSUS_BLOCKING_STATUSES = frozenset(
    {
        "changes_requested",
        "rco_block",
        "blocked",
        "rco_blocked",
        "block_requested",
    }
)
CONSENSUS_BLOCKING_CLEAR_TOKENS = frozenset({"clear", "cleared"})
CONSENSUS_BLOCKING_WORD_TOKENS = frozenset(
    {"block", "blocked", "blocks", "blocking"}
)

Runner = Callable[[Sequence[str]], Any]
ArtifactWriter = Callable[[], Mapping[str, Any]]
MergeVerifier = Callable[[int, str, str], Mapping[str, Any]]


class AutoMergeGateError(ValueError):
    """Raised when auto-merge input cannot be evaluated safely."""

    def __init__(self, report: dict[str, Any]) -> None:
        super().__init__("; ".join(str(error) for error in report.get("errors", [])))
        self.report = report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Idle consensus auto-merge gate.")
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS_PATH)
    parser.add_argument("--charter", type=Path, default=DEFAULT_CHARTER_PATH)
    parser.add_argument("--pr-status-file", type=Path, required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument(
        "--expected-base-sha",
        default="",
        help=(
            "Optional current base branch SHA. When set, the PR status "
            "snapshot base_sha must match before merge."
        ),
    )
    parser.add_argument("--consensus-proposal-id", required=True)
    parser.add_argument("--receipt-bundle-path", default="")
    parser.add_argument("--artifact-out-dir", type=Path, default=None)
    parser.add_argument("--receipt-out-dir", type=Path, default=None)
    parser.add_argument(
        "--utc-date",
        default=None,
        help="UTC date for daily rate-limit evaluation. Defaults to today.",
    )
    parser.add_argument(
        "--now",
        default=None,
        help=(
            "UTC timestamp for apply-time artifact writing. Defaults to now. "
            "Ignored by the CLI lead-stall idle proof, which always uses the "
            "runtime system clock; Python API tests may still inject now_utc."
        ),
    )
    parser.add_argument("--repo", default="")
    parser.add_argument(
        "--from-agent",
        default="",
        help=(
            "Agent attempting the merge. When omitted, every bridge "
            "decision for the task is treated as a peer signal."
        ),
    )
    parser.add_argument(
        "--bridge-task-id",
        default="",
        help=(
            "Bridge task_id whose peer review state must be clear before "
            "autonomous --apply may merge."
        ),
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--require-bridge-consensus",
        action="store_true",
        help=(
            "Require a fail-closed three-identity bridge consensus (lead + "
            "tools build-consensus + claude-rco-1 RCO_PASS, head-bound) before "
            "merge. See docs/architecture/BRIDGE_CONSENSUS_APPROVAL_V1.md."
        ),
    )
    parser.add_argument(
        "--lead-stall-failover",
        action="store_true",
        help=(
            "Allow a default-off lead-stall failover inside bridge consensus: "
            "for charter-clean PRs only, codex-tools-1 + recognized RCO may "
            "satisfy consensus when codex-lead-1 is durably idle. Gate-code "
            "and off-allowlist PRs remain operator-gated."
        ),
    )
    parser.add_argument(
        "--lead-stall-threshold-minutes",
        type=float,
        default=DEFAULT_LEAD_STALL_THRESHOLD_MINUTES,
        help="Minimum durable lead idle gap before --lead-stall-failover can engage.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        status = json.loads(args.pr_status_file.read_text(encoding="utf-8"))
        evaluation_now_utc = (
            None
            if args.lead_stall_failover
            else (_parse_utc(args.now) if args.now else None)
        )
        report = evaluate_auto_merge_gate(
            pr_status=status,
            expected_head=args.expected_head,
            expected_base_sha=args.expected_base_sha,
            consensus_proposal_id=args.consensus_proposal_id,
            receipt_bundle_path=args.receipt_bundle_path,
            events_path=args.events,
            charter_path=args.charter,
            utc_date=args.utc_date,
            repo=args.repo,
            from_agent=args.from_agent,
            bridge_task_id=args.bridge_task_id,
            apply=args.apply,
            require_bridge_consensus=args.require_bridge_consensus,
            lead_stall_failover=args.lead_stall_failover,
            lead_stall_threshold_minutes=args.lead_stall_threshold_minutes,
            now_utc=evaluation_now_utc,
            artifact_writer=_cli_artifact_writer(args),
        )
    except (json.JSONDecodeError, OSError) as exc:
        report = {
            "decision": "invalid_pr_status",
            "ok": False,
            "errors": [exc.__class__.__name__],
            "dry_run": True,
            "external_effect": False,
            "would_merge": False,
        }
        exit_code = 2
    except AutoMergeGateError as exc:
        report = exc.report
        exit_code = int(report.get("exit_code", 2))
    else:
        exit_code = 0

    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(report["decision"])
        for reason in report.get("reasons", []):
            print(f"- {reason}")
        for error in report.get("errors", []):
            print(f"- {error}", file=sys.stderr)
    return exit_code


def evaluate_auto_merge_gate(
    *,
    pr_status: Mapping[str, Any],
    expected_head: str,
    consensus_proposal_id: str,
    expected_base_sha: str = "",
    receipt_bundle_path: str = "",
    events_path: Path | None = None,
    charter_path: Path = DEFAULT_CHARTER_PATH,
    utc_date: str | None = None,
    repo: str = "",
    from_agent: str = "",
    bridge_task_id: str = "",
    apply: bool = False,
    require_bridge_consensus: bool = False,
    lead_stall_failover: bool = False,
    lead_stall_threshold_minutes: float = DEFAULT_LEAD_STALL_THRESHOLD_MINUTES,
    now_utc: datetime | None = None,
    runner: Runner | None = None,
    merge_verifier: MergeVerifier | None = None,
    artifact_writer: ArtifactWriter | None = None,
) -> dict[str, Any]:
    """Evaluate and optionally apply the final idle auto-merge gate."""
    _assert_no_private_markers(
        {
            "pr_status": pr_status,
            "expected_head": expected_head,
            "expected_base_sha": expected_base_sha,
            "consensus_proposal_id": consensus_proposal_id,
            "receipt_bundle_path": receipt_bundle_path,
            "events_path": str(events_path) if events_path is not None else "",
            "charter_path": str(charter_path),
            "repo": repo,
            "from_agent": from_agent,
            "bridge_task_id": bridge_task_id,
            "lead_stall_failover": lead_stall_failover,
            "lead_stall_threshold_minutes": lead_stall_threshold_minutes,
        }
    )
    _validate_sha(expected_head, "expected_head")
    expected_base_sha = expected_base_sha.strip().lower()
    if expected_base_sha:
        _validate_sha(expected_base_sha, "expected_base_sha")
    if repo and not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise _invalid("invalid_repo", "repo must be OWNER/NAME")

    events = _read_bridge_events(events_path) if events_path is not None else []
    pr_number = _require_int(pr_status.get("pr_number"), "pr_number")
    bridge_gate_task_id = bridge_task_id.strip() or consensus_proposal_id
    author_agent = _pr_author_agent(
        pr_status,
        bridge_gate_task_id,
        events=events,
    )
    bridge_peer_gate = _bridge_peer_gate(
        events=events,
        task_id=bridge_gate_task_id,
        pr_number=pr_number,
        from_agent=from_agent,
        checked=events_path is not None,
    )
    charter = load_charter(charter_path)
    changed_paths = _changed_paths(pr_status)
    diff_text = _diff_text(pr_status)
    path_gate = evaluate_paths(charter, changed_paths)
    diff_gate = evaluate_diff_content(charter, diff_text)
    rate_date = utc_date or _today_utc()
    quota_used = _count_daily_auto_merges(events, rate_date)
    quota_total = int(charter.daily_quota)
    rate_gate = {
        "allowed": quota_used < quota_total,
        "utc_date": rate_date,
        "quota_used": quota_used,
        "quota_total": quota_total,
    }

    head_sha = str(pr_status.get("head_sha", ""))
    _validate_sha(head_sha, "head_sha")
    title = str(pr_status.get("title", ""))
    mergeable = str(pr_status.get("mergeable", ""))
    base_gate = _base_ref_gate(
        snapshot_base_sha=str(pr_status.get("base_sha", "")),
        expected_base_sha=expected_base_sha,
        required=apply,
    )
    receipt_verified = bool(pr_status.get("receipt_verified", False))
    checks = _checks(pr_status)
    artifact_hook_configured = artifact_writer is not None
    rco_pass_gate = _bridge_rco_pass_gate(
        events=events,
        task_id=bridge_gate_task_id,
        pr_number=pr_number,
        head_sha=head_sha,
        author_agent=author_agent,
        checked=events_path is not None,
    )

    blockers: list[str] = []
    if head_sha != expected_head:
        blockers.append("exact head mismatch")
    if not path_gate.allowed:
        blockers.append(f"path gate failed: {path_gate.reason}")
    if not diff_gate.allowed:
        blockers.append(f"diff gate failed: {diff_gate.reason}")
    if mergeable not in MERGEABLE_STATES:
        blockers.append(f"mergeable state is not clean: {mergeable}")
    if not bool(base_gate.get("allowed", False)):
        blockers.append(str(base_gate.get("reason", "base freshness gate failed")))
    if not rate_gate["allowed"]:
        blockers.append(
            f"daily rate limit exceeded: {quota_used}/{quota_total} for {rate_date}"
        )
    failing_checks = [check for check in checks if not _check_passed(check)]
    if not checks:
        blockers.append("status checks snapshot is required before merge")
    if failing_checks:
        names = ", ".join(str(check.get("name", "")) for check in failing_checks)
        blockers.append(f"status checks not green: {names}")
    if apply and events_path is None:
        blockers.append("bridge events path is required before merge")
    if apply and not bridge_task_id.strip():
        blockers.append("bridge task id is required before merge")
    if not bool(bridge_peer_gate.get("clear_to_merge", False)):
        latest_block = bridge_peer_gate.get("latest_blocking_event")
        if isinstance(latest_block, Mapping):
            blockers.append(
                "unresolved peer bridge block: "
                f"agent={latest_block.get('agent')} "
                f"status={latest_block.get('status')}"
            )
        else:
            blockers.append("unresolved peer bridge block")
    if events_path is not None and not bool(rco_pass_gate.get("ok", False)):
        blockers.append("missing exact-head RCO_PASS from recognized non-author RCO")
    if not receipt_bundle_path and not (apply and artifact_hook_configured):
        blockers.append("receipt_bundle_path is required before merge")
    if (
        receipt_bundle_path
        and not receipt_verified
        and not (apply and artifact_hook_configured)
    ):
        blockers.append("receipt bundle verification is required before merge")

    bridge_consensus = _evaluate_bridge_consensus(
        require=require_bridge_consensus,
        events=events,
        events_path=events_path,
        task_id=bridge_gate_task_id,
        head_sha=head_sha,
        pr_number=pr_number,
        author_agent=author_agent,
        lead_stall_failover=lead_stall_failover,
        lead_stall_threshold_minutes=lead_stall_threshold_minutes,
        lead_stall_charter_clean=bool(path_gate.allowed and diff_gate.allowed),
        now_utc=now_utc,
    )
    if require_bridge_consensus:
        if events_path is None:
            blockers.append("bridge events path is required for bridge consensus")
        if not bridge_consensus.get("ok", False):
            reasons = bridge_consensus.get("reasons") or ["bridge consensus incomplete"]
            for reason in reasons:
                blockers.append(f"bridge consensus incomplete: {reason}")

    command = _merge_command(
        pr_number=pr_number,
        expected_head=expected_head,
        repo=repo,
    )
    base = _base_report(
        pr_number=pr_number,
        title=title,
        expected_head=expected_head,
        consensus_proposal_id=consensus_proposal_id,
        receipt_bundle_path=receipt_bundle_path,
        command=command,
        rate_gate=rate_gate,
        receipt_verified=receipt_verified,
        artifact_hook_configured=artifact_hook_configured,
        bridge_peer_gate=bridge_peer_gate,
        rco_pass_gate=rco_pass_gate,
        path_gate=_gate_to_dict(path_gate),
        diff_gate=_gate_to_dict(diff_gate),
        base_gate=base_gate,
        bridge_consensus=bridge_consensus,
    )
    if blockers:
        return {
            **base,
            "decision": (
                "rate_limited"
                if not rate_gate["allowed"]
                else "operator_review_required"
            ),
            "ok": False,
            "operator_review_required": True,
            "reasons": blockers,
        }

    report = {
        **base,
        "decision": "auto_merge_plan_ready",
        "ok": True,
        "would_merge": True,
        "operator_review_required": False,
        "reasons": ["all auto-merge gates passed"],
    }
    if not apply:
        return report

    artifact_report: Mapping[str, Any] | None = None
    if artifact_writer is not None:
        artifact_report = _run_artifact_writer(artifact_writer)
        manifest_path = _verified_artifact_manifest(artifact_report)
        receipt_bundle_path = manifest_path
        report["receipt_bundle_path"] = manifest_path
        report["artifact_report"] = dict(artifact_report)

    run = runner or _run_command
    result = run(command)
    return_code = int(getattr(result, "returncode", 0))
    if return_code != 0:
        merge_recovery = _recover_merge_state_after_failure(
            pr_number=pr_number,
            expected_head=expected_head,
            repo=repo,
            return_code=return_code,
            verifier=(
                merge_verifier
                if merge_verifier is not None
                else (None if runner is not None else _query_pr_merge_state)
            ),
        )
        if merge_recovery["merged"]:
            report.update(
                _auto_merged_fields(
                    pr_number=pr_number,
                    title=title,
                    consensus_proposal_id=consensus_proposal_id,
                    merge_commit_sha=str(merge_recovery["merge_commit_sha"]),
                    receipt_bundle_path=receipt_bundle_path,
                    rate_gate=rate_gate,
                    merge_recovery=merge_recovery,
                )
            )
            return report
        raise AutoMergeGateError(
            {
                **base,
                "decision": "auto_merge_failed",
                "ok": False,
                "operator_review_required": True,
                "errors": [f"gh pr merge failed with exit code {return_code}"],
                "merge_recovery": merge_recovery,
                "exit_code": 1,
            }
        )

    report.update(
        _auto_merged_fields(
            pr_number=pr_number,
            title=title,
            consensus_proposal_id=consensus_proposal_id,
            merge_commit_sha=str(getattr(result, "stdout", "")).strip(),
            receipt_bundle_path=receipt_bundle_path,
            rate_gate=rate_gate,
        )
    )
    return report


def _auto_merged_fields(
    *,
    pr_number: int,
    title: str,
    consensus_proposal_id: str,
    merge_commit_sha: str,
    receipt_bundle_path: str,
    rate_gate: Mapping[str, Any],
    merge_recovery: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "auto_merged": True,
        "pr_number": pr_number,
        "pr_title": title,
        "consensus_proposal_id": consensus_proposal_id,
        "merge_commit_sha": merge_commit_sha,
        "receipt_bundle_path": receipt_bundle_path,
        "rate_gate": dict(rate_gate),
    }
    fields: dict[str, Any] = {
        "decision": "auto_merged",
        "dry_run": False,
        "external_effect": True,
        "auto_merge_event_payload": payload,
    }
    if merge_recovery is not None:
        fields["merge_recovery"] = dict(merge_recovery)
        payload["merge_recovery"] = dict(merge_recovery)
    return fields


def _recover_merge_state_after_failure(
    *,
    pr_number: int,
    expected_head: str,
    repo: str,
    return_code: int,
    verifier: MergeVerifier | None,
) -> dict[str, Any]:
    base = {
        "merged": False,
        "decision": "not_checked",
        "return_code": return_code,
        "merge_commit_sha": "",
    }
    if verifier is None:
        return base
    try:
        state = verifier(pr_number, expected_head, repo)
    except Exception as exc:  # pragma: no cover - verifier is external.
        return {
            **base,
            "decision": "verifier_exception",
            "error": exc.__class__.__name__,
        }
    if not isinstance(state, Mapping):
        return {**base, "decision": "verifier_returned_non_object"}
    _assert_no_private_markers(state)

    if str(state.get("state", "")) != "MERGED":
        return {**base, "decision": "pr_not_merged"}
    if str(state.get("headRefOid", "")) != expected_head:
        return {**base, "decision": "merged_head_mismatch"}
    merge_commit = state.get("mergeCommit")
    merge_commit_sha = ""
    if isinstance(merge_commit, Mapping):
        merge_commit_sha = str(merge_commit.get("oid", ""))
    if not SHA_RE.fullmatch(merge_commit_sha):
        return {**base, "decision": "missing_merge_commit_sha"}
    return {
        **base,
        "merged": True,
        "decision": "merged_after_merge_command_failure",
        "merge_commit_sha": merge_commit_sha,
    }


def _base_report(
    *,
    pr_number: int,
    title: str,
    expected_head: str,
    consensus_proposal_id: str,
    receipt_bundle_path: str,
    command: Sequence[str],
    rate_gate: Mapping[str, Any],
    receipt_verified: bool,
    artifact_hook_configured: bool,
    bridge_peer_gate: Mapping[str, Any],
    rco_pass_gate: Mapping[str, Any],
    path_gate: Mapping[str, Any],
    diff_gate: Mapping[str, Any],
    base_gate: Mapping[str, Any],
    bridge_consensus: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "decision": "auto_merge_gate",
        "ok": False,
        "dry_run": True,
        "external_effect": False,
        "would_merge": False,
        "auto_execute": False,
        "pr_number": pr_number,
        "pr_title": title,
        "expected_head": expected_head,
        "consensus_proposal_id": consensus_proposal_id,
        "receipt_bundle_path": receipt_bundle_path,
        "receipt_gate": {
            "verified": receipt_verified,
            "artifact_hook_configured": artifact_hook_configured,
        },
        "path_gate": dict(path_gate),
        "diff_gate": dict(diff_gate),
        "base_gate": dict(base_gate),
        "bridge_peer_gate": dict(bridge_peer_gate),
        "rco_pass_gate": dict(rco_pass_gate),
        "bridge_consensus": dict(bridge_consensus),
        "rate_gate": dict(rate_gate),
        "gh_command": list(command),
    }


def _merge_command(*, pr_number: int, expected_head: str, repo: str) -> list[str]:
    command = [
        "gh",
        "pr",
        "merge",
        str(pr_number),
        "--squash",
        "--delete-branch",
        f"--match-head-commit={expected_head}",
    ]
    if repo:
        command.extend(["--repo", repo])
    return command


def _checks(pr_status: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    checks = pr_status.get("checks")
    if checks is None:
        checks = pr_status.get("statusCheckRollup", [])
    if not isinstance(checks, list):
        raise _invalid("invalid_pr_status", "checks must be a list")
    normalized: list[Mapping[str, Any]] = []
    for check in checks:
        if not isinstance(check, Mapping):
            raise _invalid("invalid_pr_status", "checks entries must be objects")
        normalized.append(check)
    return normalized


def _changed_paths(pr_status: Mapping[str, Any]) -> list[str]:
    raw = pr_status.get("changed_paths")
    if not isinstance(raw, list):
        raise _invalid("invalid_pr_status", "changed_paths must be a list")
    paths: list[str] = []
    for index, value in enumerate(raw, 1):
        if not isinstance(value, str) or not value.strip():
            raise _invalid(
                "invalid_pr_status",
                f"changed_paths item {index} must be a non-empty string",
            )
        paths.append(value)
    return paths


def _diff_text(pr_status: Mapping[str, Any]) -> str:
    raw = pr_status.get("diff_text")
    if not isinstance(raw, str):
        raise _invalid("invalid_pr_status", "diff_text must be a string")
    return raw


def _gate_to_dict(gate: Any) -> dict[str, Any]:
    return {
        "allowed": bool(gate.allowed),
        "reason": str(gate.reason),
        "blocked_paths": list(gate.blocked_paths),
        "unmatched_paths": list(gate.unmatched_paths),
        "code_pattern_hits": list(gate.code_pattern_hits),
    }


def _base_ref_gate(
    *,
    snapshot_base_sha: str,
    expected_base_sha: str,
    required: bool,
) -> dict[str, Any]:
    snapshot_base_sha = snapshot_base_sha.strip().lower()
    expected_base_sha = expected_base_sha.strip().lower()
    gate = {
        "allowed": True,
        "required": bool(required),
        "configured": bool(expected_base_sha),
        "snapshot_base_sha": snapshot_base_sha,
        "expected_base_sha": expected_base_sha,
        "reason": "",
    }
    if not expected_base_sha:
        if required:
            return {
                **gate,
                "allowed": False,
                "reason": "expected_base_sha is required before merge",
            }
        return gate
    if not SHA_RE.fullmatch(snapshot_base_sha):
        return {
            **gate,
            "allowed": False,
            "reason": "base_sha snapshot is required before merge",
        }
    if snapshot_base_sha != expected_base_sha:
        return {
            **gate,
            "allowed": False,
            "reason": "base sha mismatch",
        }
    return gate


def _normalize_rco_agents(value: str | Sequence[str] | None) -> tuple[str, ...]:
    raw: Sequence[str]
    if value is None:
        raw = BRIDGE_CONSENSUS_RCO_AGENTS
    elif isinstance(value, str):
        raw = (value,)
    else:
        raw = value
    normalized: list[str] = []
    for item in raw:
        agent = str(item or "").strip()
        if agent and agent not in normalized:
            normalized.append(agent)
    return tuple(normalized)


def _pr_author_agent(
    pr_status: Mapping[str, Any],
    bridge_task_id: str,
    *,
    events: Sequence[Mapping[str, Any]] = (),
) -> str:
    """Resolve the author to a bridge-agent id, or empty to fail closed.

    GitHub authorship is not a bridge identity in this repository: PRs are
    pushed by the operator account, while the merge gate compares reviewers
    against bridge agents such as ``claude-rco-1``. Prefer the bridge claim
    that owns the task and only accept explicit/fallback values when they are
    known bridge-agent ids.
    """
    known_agents = _known_bridge_agents(events)
    claimed_author = _bridge_claim_author_agent(
        events=events,
        bridge_task_id=bridge_task_id,
        known_agents=known_agents,
    )
    if claimed_author:
        return claimed_author

    for key in ("author_agent", "author_login", "author"):
        value = pr_status.get(key)
        candidate = _known_bridge_agent_from_value(value, known_agents=known_agents)
        if candidate:
            return candidate

    task_prefix = bridge_task_id.split("/", 1)[0].strip()
    return _known_bridge_agent_from_value(task_prefix, known_agents=known_agents)


def _known_bridge_agents(events: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    known: list[str] = []
    for agent in BRIDGE_CONSENSUS_AUTHOR_AGENTS:
        if agent not in known:
            known.append(agent)
    for event in events:
        if not isinstance(event, Mapping):
            continue
        agent = str(event.get("agent", "")).strip()
        if _is_bridge_agent_id(agent) and agent not in known:
            known.append(agent)
    return tuple(known)


def _bridge_claim_author_agent(
    *,
    events: Sequence[Mapping[str, Any]],
    bridge_task_id: str,
    known_agents: Sequence[str],
) -> str:
    for event in events:
        if not isinstance(event, Mapping):
            continue
        if str(event.get("task_id", "")) != bridge_task_id:
            continue
        if str(event.get("type", "")).lower() != "claim":
            continue
        candidate = _known_bridge_agent_from_value(
            event.get("agent"),
            known_agents=known_agents,
        )
        if candidate:
            return candidate
    return ""


def _known_bridge_agent_from_value(
    value: Any,
    *,
    known_agents: Sequence[str],
) -> str:
    candidates: list[Any] = [value]
    if isinstance(value, Mapping):
        candidates = [
            value.get("author_agent"),
            value.get("bridge_agent"),
            value.get("agent"),
            value.get("login"),
        ]
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        normalized = candidate.strip()
        if _is_bridge_agent_id(normalized) and normalized in known_agents:
            return normalized
    return ""


def _is_bridge_agent_id(value: str) -> bool:
    return bool(BRIDGE_AGENT_ID_RE.fullmatch(value))


def verify_bridge_consensus(
    *,
    events: Sequence[Mapping[str, Any]],
    task_id: str,
    head_sha: str,
    pr_number: int | None = None,
    lead_agent: str = BRIDGE_CONSENSUS_LEAD,
    tools_agent: str = BRIDGE_CONSENSUS_TOOLS,
    rco_agent: str | Sequence[str] | None = BRIDGE_CONSENSUS_RCO_AGENTS,
    author_agent: str = "",
    identity_registry: Mapping[str, str] | None = None,
    lead_stall_failover: bool = False,
    lead_stall_threshold_minutes: float = DEFAULT_LEAD_STALL_THRESHOLD_MINUTES,
    lead_stall_charter_clean: bool = False,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Fail-closed three-identity bridge-consensus verification (T0b).

    Requires a positive, head-bound approval from THREE DISTINCT agent
    identities — the lead and the tools peer (build consensus) plus an
    independent recognized RCO ``rco_pass``. Any of the following refuses
    (``ok=False``); the function never default-allows on silence:

    * a missing approval from any of the three identities (RCO absence = no
      merge; a 2-of-3 set fails closed);
    * a duplicate/forged stand-in (a non-expected agent cannot satisfy a
      required identity — identities are matched by exact agent id);
    * an approval not bound to ``head_sha`` (a re-push changes the head and
      strands prior approvals — re-consensus is required);
    * a later block from the same identity (a fresh block invalidates an
      older approval).

    The returned ``identities``/``head_sha``/``rco_pass_ref`` are recorded in
    the MAGMA receipt so a consumer can re-derive the verdict rather than
    trust a bare flag.
    """
    recognized_rco_agents = _normalize_rco_agents(rco_agent)
    author_agent = (author_agent or "").strip()
    eligible_rco_agents = tuple(
        agent for agent in recognized_rco_agents if agent != author_agent
    )
    registry = (
        load_bridge_identity_registry()
        if identity_registry is None
        else dict(identity_registry)
    )
    base: dict[str, Any] = {
        "ok": False,
        "decision": "bridge_consensus_incomplete",
        "reasons": [],
        "head_sha": head_sha,
        "identities": {},
        "rco_pass_ref": None,
        "recognized_rco_agents": list(recognized_rco_agents),
        "eligible_rco_agents": list(eligible_rco_agents),
        "author_agent": author_agent,
        "blocking_rco_agents": [],
        "ignored_identity_mismatch_events": [],
        "lead_stall_failover": {
            "enabled": bool(lead_stall_failover),
            "engaged": False,
            "threshold_minutes": lead_stall_threshold_minutes,
            "charter_clean": bool(lead_stall_charter_clean),
            "lead_agent": lead_agent,
            "tools_agent": tools_agent,
            "lead_last_substantive_ts_utc": "",
            "lead_last_substantive_index": None,
            "lead_idle_minutes": None,
            "reason": "disabled" if not lead_stall_failover else "",
        },
    }
    build_expected = (lead_agent, tools_agent)
    if len({a for a in build_expected if a and a.strip()}) != 2:
        return {
            **base,
            "decision": "invalid_consensus_config",
            "reasons": ["bridge consensus requires distinct lead/tools identities"],
        }
    if not recognized_rco_agents:
        return {
            **base,
            "decision": "invalid_consensus_config",
            "reasons": [
                "bridge consensus requires at least one recognized RCO identity"
            ],
        }
    if any(agent in build_expected for agent in recognized_rco_agents):
        return {
            **base,
            "decision": "invalid_consensus_config",
            "reasons": [
                "recognized RCO identities must be distinct from build identities"
            ],
        }
    if not author_agent:
        return {
            **base,
            "decision": "invalid_consensus_config",
            "reasons": ["author_agent is required to enforce author != reviewer"],
        }
    if not eligible_rco_agents:
        return {
            **base,
            "decision": "bridge_consensus_incomplete",
            "reasons": ["no recognized RCO remains eligible after author exclusion"],
        }
    if not SHA_RE.fullmatch(head_sha or ""):
        return {
            **base,
            "decision": "invalid_consensus_head",
            "reasons": [
                "head_sha must be a 40-char lowercase sha for consensus binding"
            ],
        }

    latest_build_approval: dict[str, tuple[int, Mapping[str, Any]]] = {}
    latest_build_block: dict[str, int] = {}
    latest_build_task_mismatch: dict[str, str] = {}
    latest_rco_approval: dict[str, tuple[int, Mapping[str, Any]]] = {}
    latest_rco_block: dict[str, int] = {}
    watched_agents = set(build_expected) | set(recognized_rco_agents)
    ignored_identity_mismatch_events: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        if not isinstance(event, Mapping):
            continue
        agent = str(event.get("agent", ""))
        if agent not in watched_agents:
            continue
        binding_status = bridge_identity_binding_status(
            event,
            registry=registry,
            restricted_agents=watched_agents,
        )
        if binding_status in {"missing_uuid", "mismatch_uuid"}:
            ignored_identity_mismatch_events.append(
                {
                    "ts_utc": str(event.get("ts_utc", "")),
                    "agent": agent,
                    "agent_uuid": str(event.get("agent_uuid", "")),
                    "type": str(event.get("type", "")),
                    "status": str(event.get("status", "")),
                    "task_id": str(event.get("task_id", "")),
                    "identity_binding_status": binding_status,
                }
            )
            continue
        status = str(event.get("status", "")).lower()
        scoped = _consensus_scope_match(
            event,
            task_id=task_id,
            pr_number=pr_number,
        )
        # Block detection is TYPE-AGNOSTIC (fail-closed): a veto from an
        # on-scope expected identity must invalidate consensus regardless of
        # the event type the vetoer used. If this honoured the
        # DECISION_EVENT_TYPES filter first, a veto posted as e.g.
        # type=blocked/status=blocked would be silently dropped and a stale
        # earlier approval would stand -- the exact fail-open T0b prevents.
        if _is_consensus_block(status):
            if not _consensus_block_scope_match(
                event,
                task_id=task_id,
                pr_number=pr_number,
                head_sha=head_sha,
                canonical_scope=scoped,
            ):
                continue
            if agent in recognized_rco_agents:
                latest_rco_block[agent] = index
            else:
                latest_build_block[agent] = index
            continue
        # Approvals remain type-restricted to decision/rco_review/finding.
        if str(event.get("type", "")).lower() not in DECISION_EVENT_TYPES:
            continue
        binds_head = _event_binds_head(event, head_sha)
        if not binds_head:
            continue
        if agent in recognized_rco_agents:
            if not scoped:
                continue
            if agent != author_agent and status in RCO_PASS_STATUSES:
                latest_rco_approval[agent] = (index, event)
        elif status in BUILD_CONSENSUS_STATUSES:
            if not _build_consensus_task_scope_match(event, task_id=task_id):
                latest_build_task_mismatch[agent] = str(event.get("task_id", ""))
                continue
            latest_build_approval[agent] = (index, event)

    reasons: list[str] = []
    build_role_reasons: dict[str, str] = {}
    identities: dict[str, Any] = {}
    for agent, role in (
        (lead_agent, "build_lead"),
        (tools_agent, "build_tools"),
    ):
        approval = latest_build_approval.get(agent)
        block_index = latest_build_block.get(agent)
        approved = approval is not None and (
            block_index is None or approval[0] > block_index
        )
        identities[role] = {
            "agent": agent,
            "approved": approved,
            "approval_index": approval[0] if approval is not None else None,
            "block_index": block_index,
            "task_id_mismatch": latest_build_task_mismatch.get(agent),
        }
        if not approved:
            if approval is None:
                mismatch = latest_build_task_mismatch.get(agent)
                if mismatch is not None:
                    reasons.append(
                        f"{role} ({agent}): head-bound approval used "
                        f"non-canonical task_id {mismatch!r}; expected {task_id!r}"
                    )
                else:
                    build_role_reasons[role] = (
                        f"{role} ({agent}): no head-bound approval at {head_sha}"
                    )
            else:
                build_role_reasons[role] = (
                    f"{role} ({agent}): a later block invalidates the approval"
                )
        if role in build_role_reasons:
            reasons.append(build_role_reasons[role])

    blocking_rco_agents: list[str] = []
    rco_identities: dict[str, Any] = {}
    for agent in recognized_rco_agents:
        approval = latest_rco_approval.get(agent)
        block_index = latest_rco_block.get(agent)
        approved = (
            agent in eligible_rco_agents
            and approval is not None
            and (block_index is None or approval[0] > block_index)
        )
        if block_index is not None and (approval is None or block_index > approval[0]):
            blocking_rco_agents.append(agent)
        rco_identities[agent] = {
            "agent": agent,
            "eligible": agent in eligible_rco_agents,
            "approved": approved,
            "approval_index": approval[0] if approval is not None else None,
            "block_index": block_index,
        }

    rco_approval: tuple[int, Mapping[str, Any]] | None = None
    satisfying_rco_agent = ""
    if not blocking_rco_agents:
        approved_rco = [
            (agent, approval)
            for agent, approval in latest_rco_approval.items()
            if agent in eligible_rco_agents
            and (
                latest_rco_block.get(agent) is None
                or approval[0] > latest_rco_block[agent]
            )
        ]
        if approved_rco:
            satisfying_rco_agent, rco_approval = max(
                approved_rco, key=lambda item: item[1][0]
            )

    identities["rco"] = {
        "agent": satisfying_rco_agent,
        "recognized_agents": list(recognized_rco_agents),
        "eligible_agents": list(eligible_rco_agents),
        "approved": rco_approval is not None and not blocking_rco_agents,
        "approval_index": rco_approval[0] if rco_approval is not None else None,
        "block_index": None,
        "by_agent": rco_identities,
    }
    if blocking_rco_agents:
        reasons.append(
            "recognized RCO veto blocks consensus: "
            + ", ".join(sorted(blocking_rco_agents))
        )
    if rco_approval is None:
        reasons.append(
            "rco (recognized non-author RCO): no head-bound approval at " f"{head_sha}"
        )

    lead_failover = _lead_stall_failover_verdict(
        enabled=lead_stall_failover,
        events=events,
        now_utc=now_utc,
        threshold_minutes=lead_stall_threshold_minutes,
        charter_clean=lead_stall_charter_clean,
        lead_agent=lead_agent,
        tools_agent=tools_agent,
        author_agent=author_agent,
        identity_registry=registry,
        lead_approved=bool(identities.get("build_lead", {}).get("approved")),
        tools_approved=bool(identities.get("build_tools", {}).get("approved")),
        lead_block_index=latest_build_block.get(lead_agent),
        rco_approved=rco_approval is not None and not blocking_rco_agents,
        blocking_rco_agents=blocking_rco_agents,
    )
    if lead_failover["engaged"]:
        lead_reason = build_role_reasons.get("build_lead")
        if lead_reason in reasons:
            reasons.remove(lead_reason)
        identities["build_lead"]["waived_by_lead_stall_failover"] = True
        identities["build_lead"]["lead_stall_failover"] = lead_failover
    elif lead_stall_failover and not identities.get("build_lead", {}).get("approved"):
        identities["build_lead"]["lead_stall_failover"] = lead_failover

    rco_pass_ref: dict[str, Any] | None = None
    if rco_approval is not None and identities["rco"]["approved"]:
        rco_event = rco_approval[1]
        rco_pass_ref = {
            "agent": satisfying_rco_agent,
            "agent_uuid": str(rco_event.get("agent_uuid", "")),
            "ts_utc": str(rco_event.get("ts_utc", "")),
            "status": str(rco_event.get("status", "")),
            "task_id": str(rco_event.get("task_id", "")),
        }

    ok = not reasons
    decision = "bridge_consensus_incomplete"
    if ok:
        decision = (
            "bridge_consensus_verified_lead_stall_failover"
            if lead_failover["engaged"]
            else "bridge_consensus_verified"
        )
    return {
        "ok": ok,
        "decision": decision,
        "reasons": reasons,
        "head_sha": head_sha,
        "identities": identities,
        "rco_pass_ref": rco_pass_ref,
        "recognized_rco_agents": list(recognized_rco_agents),
        "eligible_rco_agents": list(eligible_rco_agents),
        "author_agent": author_agent,
        "blocking_rco_agents": sorted(blocking_rco_agents),
        "ignored_identity_mismatch_events": ignored_identity_mismatch_events,
        "lead_stall_failover": lead_failover,
    }


def _lead_stall_failover_verdict(
    *,
    enabled: bool,
    events: Sequence[Mapping[str, Any]],
    now_utc: datetime | None,
    threshold_minutes: float,
    charter_clean: bool,
    lead_agent: str,
    tools_agent: str,
    author_agent: str,
    identity_registry: Mapping[str, str],
    lead_approved: bool,
    tools_approved: bool,
    lead_block_index: int | None,
    rco_approved: bool,
    blocking_rco_agents: Sequence[str],
) -> dict[str, Any]:
    verdict: dict[str, Any] = {
        "enabled": bool(enabled),
        "engaged": False,
        "threshold_minutes": threshold_minutes,
        "charter_clean": bool(charter_clean),
        "lead_agent": lead_agent,
        "tools_agent": tools_agent,
        "lead_last_substantive_ts_utc": "",
        "lead_last_substantive_index": None,
        "lead_last_substantive_type": "",
        "lead_last_substantive_status": "",
        "lead_idle_minutes": None,
        "source": "durable_bridge_events",
        "reason": "disabled" if not enabled else "",
    }
    if not enabled:
        return verdict
    if not math.isfinite(threshold_minutes) or threshold_minutes <= 0:
        verdict["reason"] = "invalid_threshold"
        return verdict
    if not charter_clean:
        verdict["reason"] = "charter_not_clean"
        return verdict
    if author_agent == tools_agent:
        verdict["reason"] = "tools_is_author"
        return verdict
    if lead_approved:
        verdict["reason"] = "lead_approval_present"
        return verdict
    if lead_block_index is not None:
        verdict["lead_block_index"] = lead_block_index
        verdict["reason"] = "lead_block_present"
        return verdict
    if not tools_approved:
        verdict["reason"] = "tools_build_consensus_missing"
        return verdict
    if not rco_approved:
        verdict["blocking_rco_agents"] = sorted(str(agent) for agent in blocking_rco_agents)
        verdict["reason"] = "rco_missing_or_blocked"
        return verdict

    latest = _latest_substantive_bridge_event(
        events=events,
        agent=lead_agent,
        identity_registry=identity_registry,
    )
    if latest is None:
        verdict["reason"] = "lead_substantive_event_missing"
        return verdict
    index, event = latest
    verdict["lead_last_substantive_index"] = index
    verdict["lead_last_substantive_ts_utc"] = str(event.get("ts_utc", ""))
    verdict["lead_last_substantive_type"] = str(event.get("type", ""))
    verdict["lead_last_substantive_status"] = str(event.get("status", ""))
    try:
        last_ts = _parse_utc(str(event.get("ts_utc", "")))
    except (TypeError, ValueError):
        verdict["reason"] = "lead_substantive_ts_invalid"
        return verdict
    now = _coerce_utc(now_utc or datetime.now(timezone.utc))
    idle_minutes = max(0.0, (now - last_ts).total_seconds() / 60.0)
    verdict["lead_idle_minutes"] = round(idle_minutes, 1)
    if idle_minutes <= threshold_minutes:
        verdict["reason"] = "lead_not_idle"
        return verdict
    verdict["engaged"] = True
    verdict["reason"] = "engaged"
    return verdict


def _latest_substantive_bridge_event(
    *,
    events: Sequence[Mapping[str, Any]],
    agent: str,
    identity_registry: Mapping[str, str],
) -> tuple[int, Mapping[str, Any]] | None:
    latest: tuple[int, Mapping[str, Any]] | None = None
    restricted_agents = {agent}
    for index, event in enumerate(events):
        if not isinstance(event, Mapping):
            continue
        if str(event.get("agent", "")) != agent:
            continue
        binding_status = bridge_identity_binding_status(
            event,
            registry=identity_registry,
            restricted_agents=restricted_agents,
        )
        if binding_status in {"missing_uuid", "mismatch_uuid"}:
            continue
        if str(event.get("type", "")).lower() in LEAD_STALL_KEEPALIVE_TYPES:
            continue
        latest = (index, event)
    return latest


def _coerce_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _evaluate_bridge_consensus(
    *,
    require: bool,
    events: Sequence[Mapping[str, Any]],
    events_path: Path | None,
    task_id: str,
    head_sha: str,
    pr_number: int | None,
    author_agent: str,
    lead_stall_failover: bool = False,
    lead_stall_threshold_minutes: float = DEFAULT_LEAD_STALL_THRESHOLD_MINUTES,
    lead_stall_charter_clean: bool = False,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Wrap verify_bridge_consensus with a not-required passthrough.

    When consensus is not required the gate behaviour is unchanged
    (``ok=True``, ``decision='not_required'``). When required, the verdict is
    computed over the bridge events; an absent events file yields an empty
    event list, which fails closed (no identities approve).
    """
    if not require:
        return {
            "required": False,
            "ok": True,
            "decision": "not_required",
            "reasons": [],
            "identities": {},
            "head_sha": head_sha,
            "rco_pass_ref": None,
            "recognized_rco_agents": list(BRIDGE_CONSENSUS_RCO_AGENTS),
            "eligible_rco_agents": [],
            "author_agent": author_agent,
            "blocking_rco_agents": [],
            "lead_stall_failover": {
                "enabled": False,
                "engaged": False,
                "reason": "not_required",
            },
        }
    result = verify_bridge_consensus(
        events=events,
        task_id=task_id,
        head_sha=head_sha,
        pr_number=pr_number,
        author_agent=author_agent,
        lead_stall_failover=lead_stall_failover,
        lead_stall_threshold_minutes=lead_stall_threshold_minutes,
        lead_stall_charter_clean=lead_stall_charter_clean,
        now_utc=now_utc,
    )
    result["required"] = True
    if events_path is None:
        result["ok"] = False
        if "bridge events path is required for consensus" not in result["reasons"]:
            result["reasons"] = [
                "bridge events path is required for consensus",
                *result["reasons"],
            ]
    return result


def _consensus_scope_match(
    event: Mapping[str, Any], *, task_id: str, pr_number: int | None
) -> bool:
    if str(event.get("task_id", "")) == task_id:
        return True
    if pr_number is None:
        return False
    payload = event.get("payload")
    if isinstance(payload, Mapping):
        for key in ("pr", "pr_number", "pull_request", "pull_request_number"):
            value = payload.get(key)
            if value == pr_number or (
                isinstance(value, str) and value.strip() == str(pr_number)
            ):
                return True
    pattern = re.compile(rf"(?i)(?:\bpr\s*#?\s*|#){pr_number}\b")
    return pattern.search(str(event.get("task_id", ""))) is not None


def _build_consensus_task_scope_match(
    event: Mapping[str, Any], *, task_id: str
) -> bool:
    """Require build approvals to use the canonical bridge task id.

    Head binding proves freshness, not ownership. A build-consensus vote with
    a dash/slash typo in the top-level bridge ``task_id`` must fail closed
    instead of being attached to the target PR by head SHA alone.
    """
    return str(event.get("task_id", "")) == task_id


def _consensus_block_scope_match(
    event: Mapping[str, Any],
    *,
    task_id: str,
    pr_number: int | None,
    head_sha: str,
    canonical_scope: bool | None = None,
) -> bool:
    scoped = (
        canonical_scope
        if canonical_scope is not None
        else _consensus_scope_match(event, task_id=task_id, pr_number=pr_number)
    )
    if scoped:
        return True
    return _event_binds_head(event, head_sha)


def _event_binds_head(event: Mapping[str, Any], head_sha: str) -> bool:
    """True only if the event references the exact head SHA.

    Prefers a structured ``payload`` field; falls back to an exact full-SHA
    substring in the message text. Because a re-push yields a different SHA,
    an approval that named the old head will not bind the new head.
    """
    payload = event.get("payload")
    if isinstance(payload, Mapping):
        for key in ("head", "head_sha", "expected_head", "head_oid", "head_commit"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip().lower() == head_sha:
                return True
    message = event.get("message")
    if isinstance(message, str) and head_sha in message.lower():
        return True
    return False


def _is_consensus_block(status: str) -> bool:
    if status in CONSENSUS_BLOCKING_STATUSES:
        return True
    normalized = re.sub(r"[^a-z0-9]+", "_", status.lower()).strip("_")
    if normalized == "no_changes_requested" or normalized.startswith(
        "no_changes_requested_"
    ):
        return False
    tokens = {token for token in re.split(r"[^a-z0-9]+", status.lower()) if token}
    if {"changes", "requested"}.issubset(tokens):
        return True
    if not tokens.intersection(CONSENSUS_BLOCKING_WORD_TOKENS):
        return False
    if "preflight" in tokens and tokens.intersection(CONSENSUS_BLOCKING_CLEAR_TOKENS):
        return False
    return True


def _bridge_peer_gate(
    *,
    events: Sequence[Mapping[str, Any]],
    task_id: str,
    pr_number: int,
    from_agent: str,
    checked: bool,
) -> dict[str, Any]:
    if not checked:
        return {
            "ok": True,
            "clear_to_merge": True,
            "decision": "not_checked",
            "task_id": task_id,
            "pr_number": pr_number,
            "merging_agent": from_agent,
            "latest_blocking_event": None,
            "latest_approval_event": None,
        }
    return check_bridge_clear_to_merge(
        events=events,
        task_id=task_id,
        merging_agent=from_agent,
        pr_number=pr_number,
    )


def _bridge_rco_pass_gate(
    *,
    events: Sequence[Mapping[str, Any]],
    task_id: str,
    pr_number: int,
    head_sha: str,
    author_agent: str,
    checked: bool,
) -> dict[str, Any]:
    if not checked:
        return {
            "ok": False,
            "rco_pass_present": False,
            "has_qualifying_rco_pass_at_head": False,
            "decision": "not_checked_operator_review_required",
            "task_id": task_id,
            "pr_number": pr_number,
            "head_sha": head_sha,
            "rco_agent": BRIDGE_CONSENSUS_RCO,
            "rco_agents": list(BRIDGE_CONSENSUS_RCO_AGENTS),
            "author_agent": author_agent,
            "latest_rco_pass_event": None,
            "latest_blocking_event": None,
        }
    return check_rco_pass_present(
        events=events,
        task_id=task_id,
        head=head_sha,
        rco_agent=BRIDGE_CONSENSUS_RCO_AGENTS,
        author_agent=author_agent,
    )


def _check_passed(check: Mapping[str, Any]) -> bool:
    state = str(check.get("state", "")).lower()
    conclusion = str(check.get("conclusion", "")).lower()
    status = str(check.get("status", "")).lower()
    if state in PASS_STATES or conclusion in PASS_STATES:
        return True
    if status in {"completed", "complete"} and conclusion in {"neutral", "skipped"}:
        return True
    return False


def _require_int(value: object, field: str) -> int:
    if not isinstance(value, int):
        raise _invalid("invalid_pr_status", f"{field} must be an integer")
    return value


def _validate_sha(value: str, field: str) -> None:
    if not SHA_RE.fullmatch(value):
        raise _invalid("invalid_sha", f"{field} must be a 40-char lowercase sha")


def _invalid(decision: str, message: str) -> AutoMergeGateError:
    return AutoMergeGateError(
        {
            "decision": decision,
            "ok": False,
            "dry_run": True,
            "external_effect": False,
            "would_merge": False,
            "errors": [message],
            "exit_code": 2,
        }
    )


def _run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
    )


def _query_pr_merge_state(
    pr_number: int,
    expected_head: str,
    repo: str,
) -> Mapping[str, Any]:
    command = [
        "gh",
        "pr",
        "view",
        str(pr_number),
        "--json",
        "state,mergeCommit,headRefOid",
    ]
    if repo:
        command.extend(["--repo", repo])
    result = _run_command(command)
    return_code = int(getattr(result, "returncode", 0))
    if return_code != 0:
        return {
            "ok": False,
            "decision": "merge_state_query_failed",
            "return_code": return_code,
        }
    stdout = str(getattr(result, "stdout", ""))
    _assert_no_private_markers(stdout)
    try:
        state = json.loads(stdout)
    except json.JSONDecodeError:
        return {
            "ok": False,
            "decision": "merge_state_query_invalid_json",
        }
    if not isinstance(state, Mapping):
        return {
            "ok": False,
            "decision": "merge_state_query_non_object",
        }
    _assert_no_private_markers(state)
    if str(state.get("headRefOid", "")) != expected_head:
        return {
            **dict(state),
            "ok": False,
            "decision": "merge_state_query_head_mismatch",
        }
    return state


def _run_artifact_writer(artifact_writer: ArtifactWriter) -> Mapping[str, Any]:
    try:
        report = artifact_writer()
    except Exception as exc:  # pragma: no cover - exact exception type is external.
        raise AutoMergeGateError(
            {
                "decision": "artifact_receipt_failed",
                "ok": False,
                "dry_run": False,
                "external_effect": False,
                "would_merge": False,
                "operator_review_required": True,
                "errors": [f"artifact writer failed: {exc.__class__.__name__}"],
                "exit_code": 1,
            }
        ) from exc
    if not isinstance(report, Mapping):
        raise AutoMergeGateError(
            {
                "decision": "artifact_receipt_failed",
                "ok": False,
                "dry_run": False,
                "external_effect": False,
                "would_merge": False,
                "operator_review_required": True,
                "errors": ["artifact writer returned non-object report"],
                "exit_code": 1,
            }
        )
    _assert_no_private_markers(report)
    return report


def _verified_artifact_manifest(report: Mapping[str, Any]) -> str:
    bundle = report.get("receipt_bundle")
    if not isinstance(bundle, Mapping):
        raise _artifact_receipt_error("artifact report did not include receipt_bundle")
    verifier = bundle.get("verifier_report")
    if not isinstance(verifier, Mapping) or verifier.get("ok") is not True:
        raise _artifact_receipt_error(
            "artifact receipt verifier did not return ok=True"
        )
    manifest = str(bundle.get("manifest", ""))
    if not manifest:
        raise _artifact_receipt_error("artifact receipt manifest path is missing")
    return manifest


def _artifact_receipt_error(message: str) -> AutoMergeGateError:
    return AutoMergeGateError(
        {
            "decision": "artifact_receipt_failed",
            "ok": False,
            "dry_run": False,
            "external_effect": False,
            "would_merge": False,
            "operator_review_required": True,
            "errors": [message],
            "exit_code": 1,
        }
    )


def _read_bridge_events(events_path: Path) -> list[dict[str, Any]]:
    if not events_path.exists():
        raise _invalid("missing_events", f"missing bridge events file: {events_path}")
    events: list[dict[str, Any]] = []
    for line_no, line in enumerate(
        events_path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise _invalid("invalid_events", f"line {line_no}: {exc.msg}") from exc
        if not isinstance(event, dict):
            raise _invalid("invalid_events", f"line {line_no}: event must be an object")
        events.append(event)
    _assert_no_private_markers(events)
    return events


def _count_daily_auto_merges(events: Sequence[Mapping[str, Any]], utc_date: str) -> int:
    count = 0
    for event in events:
        if not str(event.get("ts_utc", "")).startswith(utc_date):
            continue
        payload = (
            event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        )
        if _is_auto_merge_event(event, payload):
            count += 1
    return count


def _is_auto_merge_event(
    event: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> bool:
    if bool(payload.get("auto_merged")):
        return True
    return event.get("type") == "done" and "auto_merge" in str(event.get("status", ""))


def _today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _cli_artifact_writer(args: argparse.Namespace) -> ArtifactWriter | None:
    if not args.apply or args.receipt_out_dir is None:
        return None

    out_dir = args.artifact_out_dir or DEFAULT_ARTIFACT_OUT_DIR
    now_utc = _parse_utc(args.now) if args.now else datetime.now(timezone.utc)

    def writer() -> Mapping[str, Any]:
        return write_idle_consensus_artifact(
            events_path=args.events,
            out_dir=out_dir,
            receipt_out_dir=args.receipt_out_dir,
            now_utc=now_utc,
        )

    return writer


def _parse_utc(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _assert_no_private_markers(value: object) -> None:
    marker = _find_private_marker(value)
    if marker is not None:
        raise AutoMergeGateError(
            {
                "decision": "privacy_marker_refused",
                "ok": False,
                "dry_run": True,
                "external_effect": False,
                "would_merge": False,
                "errors": [f"privacy marker refused: {marker}"],
                "exit_code": 2,
            }
        )


def _find_private_marker(value: object) -> str | None:
    if isinstance(value, str):
        for marker in PRIVATE_MARKERS:
            if marker in value:
                return marker
        return None
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


if __name__ == "__main__":
    raise SystemExit(main())
