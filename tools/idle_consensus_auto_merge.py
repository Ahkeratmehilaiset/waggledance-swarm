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
from datetime import datetime, timedelta, timezone
import json
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
    _is_blocking_status as _bridge_is_blocking_status,
    _is_clear_status as _bridge_is_clear_status,
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
from tools.bridge_pr_author import resolve_bridge_pr_author  # noqa: E402
from tools.bridge_accepted_queue_preflight import (  # noqa: E402
    bridge_events_path_matches_root,
    check_accepted_queue_complete,
)
from tools.pr_status_snapshot import (  # noqa: E402
    PrStatusSnapshotError,
    build_pr_status_snapshot,
)
from tools.check_standing_consensus_sign_class import (  # noqa: E402
    evaluate_standing_consensus_sign,
)
from waggledance.core.idle_consensus_charter import (  # noqa: E402
    DEFAULT_CHARTER_PATH,
    evaluate_diff_content,
    evaluate_paths,
    load_charter,
)
from waggledance.core.bridge_identity_registry import (  # noqa: E402
    AGENT_ID_PATTERN,
    bridge_identity_binding_status,
    load_bridge_identity_registry,
)
from waggledance.core.work_queue import resolve_bridge_root  # noqa: E402

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
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
CONSENSUS_CLEAR_EVENT_TYPES = DECISION_EVENT_TYPES | frozenset({"done", "test"})
LEAD_STALL_FAILOVER_THRESHOLD_SECONDS = 90 * 60
LEAD_STALL_NON_SUBSTANTIVE_TYPES = frozenset({"heartbeat", "liveness"})
LEAD_STALL_NON_SUBSTANTIVE_STATUSES = frozenset(
    {"alive", "heartbeat", "heartbeat_ok", "idle_heartbeat", "liveness"}
)

Runner = Callable[[Sequence[str]], Any]
ArtifactWriter = Callable[[], Mapping[str, Any]]
MergeVerifier = Callable[[int, str, str], Mapping[str, Any]]
AcceptedQueueChecker = Callable[..., Mapping[str, Any]]


class AutoMergeGateError(ValueError):
    """Raised when auto-merge input cannot be evaluated safely."""

    def __init__(self, report: dict[str, Any]) -> None:
        super().__init__("; ".join(str(error) for error in report.get("errors", [])))
        self.report = report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Idle consensus auto-merge gate.")
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
        help="UTC timestamp for apply-time artifact writing. Defaults to now.",
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
        "--standing-consensus-sign",
        action="store_true",
        help=(
            "Default-off Rule 9b path: allow an eligible off-allowlist (b)-class "
            "PR to satisfy the operator signature via standing bridge consensus. "
            "Gate/governance/charter-denylisted paths still fail closed."
        ),
    )
    parser.add_argument(
        "--allow-lead-stall-failover",
        action="store_true",
        help=(
            "Default-off emergency path: allow a charter-clean, non-tools-authored "
            "PR to satisfy the lead build slot from durable lead-idle evidence "
            "when tools build-consensus and non-author RCO_PASS are present."
        ),
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    bridge_root = resolve_bridge_root(args.bridge_root)
    events_path = args.events or bridge_root / "shared" / "events.jsonl"

    def accepted_queue_checker(**_: object) -> Mapping[str, Any]:
        return check_accepted_queue_complete(
            bridge_root=bridge_root,
            events_path=events_path,
        )

    try:
        if (
            args.events is not None
            and not bridge_events_path_matches_root(
                bridge_root=bridge_root,
                events_path=events_path,
            )
        ):
            raise _invalid(
                "invalid_input",
                "--events must equal <bridge-root>/shared/events.jsonl",
            )
        status_text = args.pr_status_file.read_bytes().decode(
            "utf-8-sig",
            errors="strict",
        )
        status = _strict_json_loads(
            status_text,
            decision="invalid_pr_status",
            context="PR status JSON",
        )
        report = evaluate_auto_merge_gate(
            pr_status=status,
            expected_head=args.expected_head,
            expected_base_sha=args.expected_base_sha,
            consensus_proposal_id=args.consensus_proposal_id,
            receipt_bundle_path=args.receipt_bundle_path,
            events_path=events_path,
            charter_path=args.charter,
            utc_date=args.utc_date,
            repo=args.repo,
            from_agent=args.from_agent,
            bridge_task_id=args.bridge_task_id,
            apply=args.apply,
            require_bridge_consensus=args.require_bridge_consensus,
            standing_consensus_sign=args.standing_consensus_sign,
            allow_lead_stall_failover=args.allow_lead_stall_failover,
            accepted_queue_checker=accepted_queue_checker,
            artifact_writer=_cli_artifact_writer(args, events_path),
        )
    except (OSError, UnicodeError) as exc:
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
    standing_consensus_sign: bool = False,
    allow_lead_stall_failover: bool = False,
    lead_stall_failover_threshold_seconds: int = (
        LEAD_STALL_FAILOVER_THRESHOLD_SECONDS
    ),
    runner: Runner | None = None,
    merge_verifier: MergeVerifier | None = None,
    artifact_writer: ArtifactWriter | None = None,
    accepted_queue_checker: AcceptedQueueChecker | None = None,
) -> dict[str, Any]:
    """Evaluate and optionally apply the final idle auto-merge gate."""
    _validate_evaluate_inputs(
        pr_status=pr_status,
        expected_head=expected_head,
        consensus_proposal_id=consensus_proposal_id,
        expected_base_sha=expected_base_sha,
        receipt_bundle_path=receipt_bundle_path,
        events_path=events_path,
        charter_path=charter_path,
        utc_date=utc_date,
        repo=repo,
        from_agent=from_agent,
        bridge_task_id=bridge_task_id,
        apply=apply,
        require_bridge_consensus=require_bridge_consensus,
        standing_consensus_sign=standing_consensus_sign,
        allow_lead_stall_failover=allow_lead_stall_failover,
        lead_stall_failover_threshold_seconds=(
            lead_stall_failover_threshold_seconds
        ),
        runner=runner,
        merge_verifier=merge_verifier,
        artifact_writer=artifact_writer,
        accepted_queue_checker=accepted_queue_checker,
    )
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
        }
    )
    _validate_sha(expected_head, "expected_head")
    if expected_base_sha:
        _validate_sha(expected_base_sha, "expected_base_sha")
    if repo and not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise _invalid("invalid_repo", "repo must be OWNER/NAME")

    accepted_queue_preflight = _accepted_queue_preflight(
        events_path=events_path,
        checker=accepted_queue_checker,
    )
    events = _read_bridge_events(events_path) if events_path is not None else []
    pr_number = _require_int(pr_status.get("pr_number"), "pr_number")
    bridge_gate_task_id = (
        bridge_task_id if bridge_task_id != "" else consensus_proposal_id
    )
    charter = load_charter(charter_path)
    changed_paths = _changed_paths(pr_status)
    diff_text = _diff_text(pr_status)
    path_gate = evaluate_paths(charter, changed_paths)
    diff_gate = evaluate_diff_content(charter, diff_text)
    trusted_today = _today_utc()
    rate_date = trusted_today if utc_date is None else utc_date
    if apply and rate_date != trusted_today:
        raise _invalid(
            "invalid_input",
            "utc_date must equal the current UTC date when apply is true",
        )
    quota_used = _count_daily_auto_merges(events, rate_date)
    quota_total = int(charter.daily_quota)
    rate_gate = {
        "allowed": quota_used < quota_total,
        "utc_date": rate_date,
        "quota_used": quota_used,
        "quota_total": quota_total,
    }

    head_sha_value = pr_status.get("head_sha", "")
    if type(head_sha_value) is not str:
        raise _invalid("invalid_pr_status", "head_sha must be a string")
    head_sha = head_sha_value
    _validate_sha(head_sha, "head_sha")
    snapshot_base_value = pr_status.get("base_sha", "")
    if type(snapshot_base_value) is not str:
        raise _invalid("invalid_pr_status", "base_sha must be a string")
    snapshot_base_sha = snapshot_base_value
    author_resolution = _pr_author_resolution(
        pr_status,
        bridge_gate_task_id,
        events=events,
        expected_head_sha=expected_head,
        expected_base_sha=expected_base_sha or snapshot_base_sha,
    )
    author_agent = (
        str(author_resolution.get("author_agent", ""))
        if author_resolution.get("ok") is True
        else ""
    )
    bridge_peer_gate = _bridge_peer_gate(
        events=events,
        task_id=bridge_gate_task_id,
        pr_number=pr_number,
        from_agent=from_agent,
        author_agent=author_agent,
        checked=events_path is not None,
    )
    title = str(pr_status.get("title", ""))
    mergeable = str(pr_status.get("mergeable", ""))
    state = pr_status.get("state")
    is_draft = pr_status.get("is_draft")
    base_gate = _base_ref_gate(
        snapshot_base_sha=snapshot_base_sha,
        expected_base_sha=expected_base_sha,
        required=apply,
    )
    receipt_verified_value = pr_status.get("receipt_verified", False)
    receipt_verified = receipt_verified_value is True
    receipt_verified_typed = type(receipt_verified_value) is bool
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
    if author_resolution.get("ok") is not True:
        reasons = author_resolution.get("reasons") or [
            "PR author could not be resolved"
        ]
        blockers.extend(
            f"PR author resolution requires operator review: {reason}"
            for reason in reasons
        )
    if head_sha != expected_head:
        blockers.append("exact head mismatch")
    if not path_gate.allowed:
        blockers.append(f"path gate failed: {path_gate.reason}")
    if not diff_gate.allowed:
        blockers.append(f"diff gate failed: {diff_gate.reason}")
    if mergeable not in MERGEABLE_STATES:
        blockers.append(f"mergeable state is not clean: {mergeable}")
    if state != "OPEN":
        blockers.append("PR state snapshot must be OPEN")
    if is_draft is not False:
        blockers.append("PR must not be a draft")
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
    if not (
        accepted_queue_preflight.get("ok") is True
        and accepted_queue_preflight.get("complete") is True
    ):
        blockers.append(
            "accepted bridge queue is not fully visible in canonical history: "
            + str(accepted_queue_preflight.get("decision", "unknown"))
        )
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
    if not receipt_verified_typed:
        blockers.append("receipt_verified must be a boolean")
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
        charter_path=charter_path,
        changed_paths=changed_paths,
        diff_text=diff_text,
        allow_lead_stall_failover=allow_lead_stall_failover,
        lead_stall_failover_threshold_seconds=(
            lead_stall_failover_threshold_seconds
        ),
    )
    if require_bridge_consensus:
        if events_path is None:
            blockers.append("bridge events path is required for bridge consensus")
        if not bridge_consensus.get("ok", False):
            reasons = bridge_consensus.get("reasons") or ["bridge consensus incomplete"]
            for reason in reasons:
                blockers.append(f"bridge consensus incomplete: {reason}")

    # 9b standing-consensus-sign admission (DEFAULT-OFF; never self-activating).
    # When enabled, a (b)-class OFF-ALLOWLIST PR whose best-possible-consensus holds
    # has the operator's per-PR signature satisfied by the STANDING signature, so we
    # drop ONLY the off-allowlist path-gate blocker. Every OTHER gate stays enforced
    # (CI, base, mergeable, receipt, diff, bridge-consensus). The classifier is
    # fail-closed and treats gate-verdict code / governance / itself as (a), so this
    # can never weaken the gate nor auto-sign the PR that wires it.
    ci_all_green = bool(checks) and not failing_checks
    receipt_present = bool(receipt_bundle_path) or bool(apply and artifact_hook_configured)
    standing_sign_gate = evaluate_standing_consensus_sign(
        enabled=standing_consensus_sign,
        changed_paths=changed_paths,
        bridge_consensus=bridge_consensus,
        ci_all_green=ci_all_green,
        diff_gate_allowed=bool(diff_gate.allowed),
        head_matches=(head_sha == expected_head),
        receipt_present=receipt_present,
    )
    if standing_sign_gate.get("admitted"):
        blockers = [b for b in blockers if not b.startswith("path gate failed")]

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
        accepted_queue_preflight=accepted_queue_preflight,
    )
    base["author_resolution"] = author_resolution
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
            "standing_consensus_sign": standing_sign_gate,
        }

    report = {
        **base,
        "decision": "auto_merge_plan_ready",
        "ok": True,
        "would_merge": True,
        "operator_review_required": False,
        "reasons": ["all auto-merge gates passed"],
        "standing_consensus_sign": standing_sign_gate,
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

    try:
        apply_snapshot = build_pr_status_snapshot(
            pr_number=pr_number,
            repo=repo,
            receipt_verified=True,
            expected_base_sha=expected_base_sha,
            runner=runner,
        )
    except PrStatusSnapshotError as exc:
        return {
            **report,
            "decision": "operator_review_required",
            "ok": False,
            "would_merge": False,
            "operator_review_required": True,
            "reasons": [
                "apply snapshot recheck failed: "
                + "; ".join(
                    str(error)
                    for error in (
                        exc.report.get("errors")
                        or ["canonical PR snapshot failed"]
                    )
                )
            ],
            "apply_recheck": dict(exc.report),
        }
    apply_recheck = _compare_apply_snapshot(
        initial=pr_status,
        verified=apply_snapshot,
        task_id=bridge_gate_task_id,
        events=events,
        expected_head=expected_head,
        expected_base_sha=expected_base_sha,
        expected_author_agent=author_agent,
    )
    if apply_recheck.get("ok") is True:
        fresh_gate = evaluate_auto_merge_gate(
            pr_status=apply_snapshot,
            expected_head=expected_head,
            expected_base_sha=expected_base_sha,
            consensus_proposal_id=consensus_proposal_id,
            receipt_bundle_path=receipt_bundle_path,
            events_path=events_path,
            charter_path=charter_path,
            utc_date=rate_date,
            repo=repo,
            from_agent=from_agent,
            bridge_task_id=bridge_gate_task_id,
            apply=False,
            require_bridge_consensus=require_bridge_consensus,
            standing_consensus_sign=standing_consensus_sign,
            allow_lead_stall_failover=allow_lead_stall_failover,
            lead_stall_failover_threshold_seconds=(
                lead_stall_failover_threshold_seconds
            ),
            accepted_queue_checker=accepted_queue_checker,
        )
        apply_recheck["fresh_gate"] = fresh_gate
        if fresh_gate.get("ok") is not True:
            apply_recheck["ok"] = False
            apply_recheck["decision"] = "apply_snapshot_recheck_failed"
            apply_recheck["reasons"] = [
                "fresh apply safety gate failed: " + str(reason)
                for reason in (
                    fresh_gate.get("reasons")
                    or fresh_gate.get("errors")
                    or ["unknown fresh-gate refusal"]
                )
            ]
    report["apply_recheck"] = apply_recheck
    if apply_recheck.get("ok") is not True:
        return {
            **report,
            "decision": "operator_review_required",
            "ok": False,
            "would_merge": False,
            "operator_review_required": True,
            "reasons": list(apply_recheck.get("reasons", [])),
        }

    run = runner if runner is not None else _run_command
    recovery_verifier = (
        merge_verifier
        if merge_verifier is not None
        else (
            lambda number, head, repository: _query_pr_merge_state(
                number,
                head,
                repository,
                runner=run,
            )
        )
    )
    attempted_failure = {
        **base,
        "dry_run": False,
        "external_effect": None,
        "external_effect_unknown": True,
        "gh_merge_attempted": True,
    }
    try:
        result = run(command)
        return_code = getattr(result, "returncode", None)
    except Exception as exc:
        report["gh_merge_attempted"] = True
        merge_recovery = _recover_merge_state_after_failure(
            pr_number=pr_number,
            expected_head=expected_head,
            expected_head_ref=str(apply_snapshot["head_ref"]),
            expected_base_ref=str(apply_snapshot["base_ref"]),
            repo=repo,
            return_code=None,
            verifier=recovery_verifier,
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
                **attempted_failure,
                "decision": "auto_merge_runner_exception",
                "ok": False,
                "operator_review_required": True,
                "errors": [
                    f"gh pr merge runner raised {exc.__class__.__name__}"
                ],
                "merge_recovery": merge_recovery,
                "exit_code": 1,
            }
        ) from exc
    report["gh_merge_attempted"] = True
    if type(return_code) is not int:
        merge_recovery = _recover_merge_state_after_failure(
            pr_number=pr_number,
            expected_head=expected_head,
            expected_head_ref=str(apply_snapshot["head_ref"]),
            expected_base_ref=str(apply_snapshot["base_ref"]),
            repo=repo,
            return_code=None,
            verifier=recovery_verifier,
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
                **attempted_failure,
                "decision": "auto_merge_invalid_result",
                "ok": False,
                "operator_review_required": True,
                "errors": [
                    "gh pr merge returned an invalid exit code"
                ],
                "merge_recovery": merge_recovery,
                "exit_code": 1,
            }
        )
    if return_code != 0:
        merge_recovery = _recover_merge_state_after_failure(
            pr_number=pr_number,
            expected_head=expected_head,
            expected_head_ref=str(apply_snapshot["head_ref"]),
            expected_base_ref=str(apply_snapshot["base_ref"]),
            repo=repo,
            return_code=return_code,
            verifier=recovery_verifier,
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
                **attempted_failure,
                "decision": "auto_merge_failed",
                "ok": False,
                "operator_review_required": True,
                "errors": [f"gh pr merge failed with exit code {return_code}"],
                "merge_recovery": merge_recovery,
                "exit_code": 1,
            }
        )

    post_merge = _recover_merge_state_after_failure(
        pr_number=pr_number,
        expected_head=expected_head,
        expected_head_ref=str(apply_snapshot["head_ref"]),
        expected_base_ref=str(apply_snapshot["base_ref"]),
        repo=repo,
        return_code=0,
        verifier=recovery_verifier,
    )
    if not post_merge["merged"]:
        raise AutoMergeGateError(
            {
                **attempted_failure,
                "decision": "post_merge_state_unconfirmed",
                "ok": False,
                "operator_review_required": True,
                "errors": [
                    "merge command exited zero but GitHub did not confirm MERGED"
                ],
                "merge_recovery": post_merge,
                "exit_code": 1,
            }
        )
    report.update(
        _auto_merged_fields(
            pr_number=pr_number,
            title=title,
            consensus_proposal_id=consensus_proposal_id,
            merge_commit_sha=str(post_merge["merge_commit_sha"]),
            receipt_bundle_path=receipt_bundle_path,
            rate_gate=rate_gate,
            merge_recovery=post_merge,
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
    expected_head_ref: str,
    expected_base_ref: str,
    repo: str,
    return_code: int | None,
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
    if type(state) is not dict:
        return {**base, "decision": "verifier_returned_non_object"}
    try:
        _assert_no_private_markers(state)
    except Exception:
        return {**base, "decision": "verifier_returned_invalid_state"}

    if "ok" in state and state.get("ok") is not True:
        return {**base, "decision": "verifier_refused_merge_state"}
    number = state.get("number")
    if type(number) is not int or number != pr_number:
        return {**base, "decision": "merged_pr_mismatch"}
    state_value = state.get("state")
    if type(state_value) is not str or state_value != "MERGED":
        return {**base, "decision": "pr_not_merged"}
    head = state.get("headRefOid")
    if type(head) is not str or head != expected_head:
        return {**base, "decision": "merged_head_mismatch"}
    head_ref = state.get("headRefName")
    if type(head_ref) is not str or head_ref != expected_head_ref:
        return {**base, "decision": "merged_head_ref_mismatch"}
    base_ref = state.get("baseRefName")
    if type(base_ref) is not str or base_ref != expected_base_ref:
        return {**base, "decision": "merged_base_ref_mismatch"}
    merge_commit = state.get("mergeCommit")
    merge_commit_sha = ""
    if type(merge_commit) is dict:
        oid = merge_commit.get("oid")
        if type(oid) is str:
            merge_commit_sha = oid
    if not SHA_RE.fullmatch(merge_commit_sha):
        return {**base, "decision": "missing_merge_commit_sha"}
    return {
        **base,
        "merged": True,
        "decision": (
            "merged_after_merge_command"
            if return_code == 0
            else (
                "merged_after_merge_command_failure"
                if return_code is not None
                else "merged_after_merge_command_result_unknown"
            )
        ),
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
    accepted_queue_preflight: Mapping[str, Any],
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
        "accepted_queue_preflight": dict(accepted_queue_preflight),
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
    raw: Sequence[object]
    if value is None:
        raw = BRIDGE_CONSENSUS_RCO_AGENTS
    elif type(value) is str:
        raw = (value,)
    elif not isinstance(value, Sequence) or isinstance(
        value, (bytes, bytearray)
    ):
        raise ValueError("rco_agent must be a string, sequence, or null")
    else:
        raw = value
    normalized: list[str] = []
    for index, item in enumerate(raw, 1):
        if (
            type(item) is not str
            or not item
            or item != item.strip()
            or AGENT_ID_PATTERN.fullmatch(item) is None
        ):
            raise ValueError(
                f"rco_agent item {index} must be an exact canonical agent id"
            )
        if item in normalized:
            raise ValueError("rco_agent identities must be distinct")
        normalized.append(item)
    return tuple(normalized)


def _pr_author_agent(
    pr_status: Mapping[str, Any],
    bridge_task_id: str,
    *,
    events: Sequence[Mapping[str, Any]] = (),
) -> str:
    """Return the shared fail-closed resolver's agent, or empty on refusal."""
    report = _pr_author_resolution(
        pr_status,
        bridge_task_id,
        events=events,
        expected_head_sha=str(pr_status.get("head_sha", "")),
        expected_base_sha=str(pr_status.get("base_sha", "")),
    )
    return (
        str(report.get("author_agent", ""))
        if report.get("ok") is True
        else ""
    )


def _pr_author_resolution(
    pr_status: Mapping[str, Any],
    bridge_task_id: str,
    *,
    events: Sequence[Mapping[str, Any]],
    expected_head_sha: str,
    expected_base_sha: str,
) -> dict[str, Any]:
    head_ref_name = pr_status.get("head_ref")
    if head_ref_name is None:
        head_ref_name = pr_status.get("headRefName", "")
    git_identities = pr_status.get("git_identities", ())
    git_identity_evidence = pr_status.get("git_identity_evidence")
    asserted_author_agent = pr_status.get("author_agent", "")
    try:
        changed_paths = _changed_paths(pr_status)
    except AutoMergeGateError as exc:
        return {
            "ok": False,
            "decision": "invalid_author_evidence",
            "operator_review_required": True,
            "author_agent": "",
            "reasons": list(exc.report.get("errors", [])),
        }
    return resolve_bridge_pr_author(
        events=events,
        pr_number=pr_status.get("pr_number"),
        task_id=bridge_task_id,
        head_ref_name=head_ref_name,  # type: ignore[arg-type]
        head_sha=pr_status.get("head_sha", ""),  # type: ignore[arg-type]
        base_sha=pr_status.get("base_sha", ""),  # type: ignore[arg-type]
        expected_head_sha=expected_head_sha,
        expected_base_sha=expected_base_sha,
        changed_paths=changed_paths,
        git_identities=git_identities,  # type: ignore[arg-type]
        git_identity_evidence=git_identity_evidence,  # type: ignore[arg-type]
        asserted_author_agent=asserted_author_agent,  # type: ignore[arg-type]
    )


def _compare_apply_snapshot(
    *,
    initial: Mapping[str, Any],
    verified: Mapping[str, Any],
    task_id: str,
    events: Sequence[Mapping[str, Any]],
    expected_head: str,
    expected_base_sha: str,
    expected_author_agent: str,
) -> dict[str, Any]:
    initial_head_ref = initial.get("head_ref")
    if initial_head_ref is None:
        initial_head_ref = initial.get("headRefName")
    fields = {
        "pr_number": (initial.get("pr_number"), verified.get("pr_number")),
        "head_ref": (initial_head_ref, verified.get("head_ref")),
        "head_sha": (initial.get("head_sha"), verified.get("head_sha")),
        "base_ref": (initial.get("base_ref"), verified.get("base_ref")),
        "base_sha": (initial.get("base_sha"), verified.get("base_sha")),
        "base_tip_sha": (
            initial.get("base_tip_sha"),
            verified.get("base_tip_sha"),
        ),
        "state": (initial.get("state"), verified.get("state")),
        "is_draft": (initial.get("is_draft"), verified.get("is_draft")),
        "updated_at": (initial.get("updated_at"), verified.get("updated_at")),
        "mergeable": (initial.get("mergeable"), verified.get("mergeable")),
        "checks": (initial.get("checks"), verified.get("checks")),
        "changed_paths": (
            initial.get("changed_paths"),
            verified.get("changed_paths"),
        ),
        "diff_text": (initial.get("diff_text"), verified.get("diff_text")),
        "git_identities": (
            initial.get("git_identities"),
            verified.get("git_identities"),
        ),
        "git_identity_evidence": (
            initial.get("git_identity_evidence"),
            verified.get("git_identity_evidence"),
        ),
    }
    drifted = [field for field, pair in fields.items() if pair[0] != pair[1]]
    reasons = [
        f"apply snapshot recheck drifted: {field}"
        for field in drifted
    ]
    resolution = _pr_author_resolution(
        verified,
        task_id,
        events=events,
        expected_head_sha=expected_head,
        expected_base_sha=expected_base_sha,
    )
    if resolution.get("ok") is not True:
        reasons.extend(
            "apply author resolution failed: " + str(reason)
            for reason in (
                resolution.get("reasons")
                or ["PR author could not be resolved"]
            )
        )
    elif resolution.get("author_agent") != expected_author_agent:
        reasons.append("apply author resolution changed canonical owner")
    return {
        "ok": not reasons,
        "decision": "apply_snapshot_recheck_pass"
        if not reasons
        else "apply_snapshot_recheck_failed",
        "reasons": reasons,
        "author_resolution": resolution,
    }


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
    allow_lead_stall_failover: bool = False,
    lead_stall_failover_threshold_seconds: int = (
        LEAD_STALL_FAILOVER_THRESHOLD_SECONDS
    ),
    lead_stall_failover_changed_paths: Sequence[str] = (),
    lead_stall_failover_diff_text: str = "",
    lead_stall_failover_charter_path: Path = DEFAULT_CHARTER_PATH,
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
    if (
        not isinstance(events, Sequence)
        or isinstance(events, (str, bytes, bytearray))
    ):
        return _invalid_consensus_input(
            "events must be a non-string sequence",
            head_sha=head_sha,
        )
    if (
        type(task_id) is not str
        or not task_id
        or task_id != task_id.strip()
    ):
        return _invalid_consensus_input(
            "task_id must be a non-empty exact string",
            head_sha=head_sha,
        )
    if type(head_sha) is not str or SHA_RE.fullmatch(head_sha) is None:
        return _invalid_consensus_input(
            "head_sha must be a 40-char lowercase sha",
            head_sha=head_sha,
            decision="invalid_consensus_head",
        )
    if pr_number is not None and (
        type(pr_number) is not int or pr_number <= 0
    ):
        return _invalid_consensus_input(
            "pr_number must be a positive integer or null",
            head_sha=head_sha,
        )
    if type(allow_lead_stall_failover) is not bool:
        return _invalid_consensus_input(
            "allow_lead_stall_failover must be a boolean",
            head_sha=head_sha,
        )
    if (
        type(lead_stall_failover_threshold_seconds) is not int
        or lead_stall_failover_threshold_seconds <= 0
    ):
        return _invalid_consensus_input(
            "lead_stall_failover_threshold_seconds must be a positive integer",
            head_sha=head_sha,
        )
    if (
        not isinstance(lead_stall_failover_changed_paths, Sequence)
        or isinstance(
            lead_stall_failover_changed_paths,
            (str, bytes, bytearray),
        )
        or any(
            type(path) is not str
            or not path
            or path != path.strip()
            for path in lead_stall_failover_changed_paths
        )
    ):
        return _invalid_consensus_input(
            "lead_stall_failover_changed_paths must contain exact paths",
            head_sha=head_sha,
        )
    if type(lead_stall_failover_diff_text) is not str:
        return _invalid_consensus_input(
            "lead_stall_failover_diff_text must be a string",
            head_sha=head_sha,
        )
    if not isinstance(lead_stall_failover_charter_path, Path):
        return _invalid_consensus_input(
            "lead_stall_failover_charter_path must be a Path",
            head_sha=head_sha,
        )
    try:
        recognized_rco_agents = _normalize_rco_agents(rco_agent)
    except ValueError as exc:
        recognized_rco_agents = ()
        config_error = str(exc)
    else:
        config_error = ""
    if (
        type(author_agent) is not str
        or not author_agent
        or author_agent != author_agent.strip()
        or AGENT_ID_PATTERN.fullmatch(author_agent) is None
    ):
        config_error = config_error or (
            "author_agent must be an exact canonical agent id"
        )
        normalized_author_agent = ""
    else:
        normalized_author_agent = author_agent
    author_agent = normalized_author_agent
    eligible_rco_agents = tuple(
        agent for agent in recognized_rco_agents if agent != author_agent
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
        "lead_stall_failover": _lead_stall_failover_disabled_report(
            head_sha=head_sha,
            threshold_seconds=lead_stall_failover_threshold_seconds,
        ),
    }
    build_expected = (lead_agent, tools_agent)
    if config_error:
        return {
            **base,
            "decision": "invalid_consensus_config",
            "reasons": [config_error],
        }
    if any(
        type(agent) is not str
        or not agent
        or agent != agent.strip()
        or AGENT_ID_PATTERN.fullmatch(agent) is None
        for agent in build_expected
    ):
        return {
            **base,
            "decision": "invalid_consensus_config",
            "reasons": [
                "lead/tools identities must be exact canonical agent ids"
            ],
        }
    if len(set(build_expected)) != 2:
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
    waived_build_author_agents = {
        agent for agent in build_expected if agent == author_agent
    }
    eligible_build_agents = {
        agent: agent != author_agent for agent in build_expected
    }
    try:
        canonical_registry = load_bridge_identity_registry()
        if identity_registry is not None:
            if (
                not isinstance(identity_registry, Mapping)
                or dict(identity_registry) != canonical_registry
            ):
                raise ValueError(
                    "identity_registry must exactly match the canonical registry"
                )
        registry = canonical_registry
    except (TypeError, ValueError) as exc:
        return {
            **base,
            "decision": "invalid_identity_registry",
            "reasons": [str(exc)],
        }
    configured_agents = {
        lead_agent,
        tools_agent,
        author_agent,
        *recognized_rco_agents,
    }
    unregistered_agents = sorted(
        agent for agent in configured_agents if agent not in registry
    )
    if unregistered_agents:
        return {
            **base,
            "decision": "invalid_consensus_config",
            "reasons": [
                "consensus identities are not registered: "
                + ", ".join(unregistered_agents)
            ],
        }

    latest_build_approval: dict[str, tuple[int, Mapping[str, Any]]] = {}
    latest_build_block: dict[str, int] = {}
    latest_build_task_mismatch: dict[str, str] = {}
    latest_build_shape_mismatch: dict[str, dict[str, Any]] = {}
    latest_rco_approval: dict[str, tuple[int, Mapping[str, Any]]] = {}
    latest_rco_block: dict[str, int] = {}
    watched_agents = set(build_expected) | set(recognized_rco_agents)
    ignored_identity_mismatch_events: list[dict[str, Any]] = []
    identity_valid_events: list[tuple[int, Mapping[str, Any]]] = []
    for index, event in enumerate(events):
        if not isinstance(event, Mapping):
            continue
        agent_value = event.get("agent", "")
        if type(agent_value) is not str:
            continue
        agent = agent_value
        if agent not in watched_agents:
            continue
        binding_status = bridge_identity_binding_status(
            event,
            registry=registry,
            restricted_agents=watched_agents,
        )
        if binding_status != "valid":
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
        identity_valid_events.append((index, event))
        status = str(event.get("status", "")).lower()
        event_type = str(event.get("type", "")).lower()
        scoped = _consensus_scope_match(
            event,
            task_id=task_id,
            pr_number=pr_number,
        )
        # Block detection intentionally shares the peer-gate status classifier:
        # authoritative veto types invalidate consensus, but diagnostic
        # bridge chatter must not create a phantom block.
        if _is_consensus_clear(status, event_type=event_type):
            if not _consensus_block_scope_match(
                event,
                task_id=task_id,
                pr_number=pr_number,
                head_sha=head_sha,
                canonical_scope=scoped,
            ):
                continue
            if agent in recognized_rco_agents:
                latest_rco_block.pop(agent, None)
            else:
                latest_build_block.pop(agent, None)
            continue
        if _is_consensus_block(status, event_type=event_type):
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
        binds_head = _event_binds_head(event, head_sha)
        if agent not in recognized_rco_agents and binds_head:
            status_looks_build_consensus = (
                status in BUILD_CONSENSUS_STATUSES
                or ("build_consensus" in status and "pass" in status)
            )
            if status_looks_build_consensus:
                type_ok = event_type in DECISION_EVENT_TYPES
                status_ok = status in BUILD_CONSENSUS_STATUSES
                task_id_ok = _build_consensus_task_scope_match(
                    event, task_id=task_id
                )
                if not type_ok or not status_ok:
                    latest_build_shape_mismatch[agent] = {
                        "type": event_type,
                        "status": status,
                        "task_id": str(event.get("task_id", "")),
                        "type_ok": type_ok,
                        "status_ok": status_ok,
                        "task_id_ok": task_id_ok,
                    }
        # Approvals remain type-restricted to decision/rco_review/finding.
        if event_type not in DECISION_EVENT_TYPES:
            continue
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
    identities: dict[str, Any] = {}
    build_identity_reasons: dict[str, str] = {}
    for agent, role in (
        (lead_agent, "build_lead"),
        (tools_agent, "build_tools"),
    ):
        approval = latest_build_approval.get(agent)
        block_index = latest_build_block.get(agent)
        build_author_slot_waived = agent in waived_build_author_agents
        eligible = bool(eligible_build_agents.get(agent, False))
        direct_approved = eligible and approval is not None and (
            block_index is None or approval[0] > block_index
        )
        waiver_satisfied = build_author_slot_waived and block_index is None
        approved = waiver_satisfied or direct_approved
        identities[role] = {
            "agent": agent,
            "eligible": eligible,
            "approved": approved,
            "direct_approval": direct_approved,
            "build_author_slot_waived": build_author_slot_waived,
            "build_author_slot_waiver_satisfied": waiver_satisfied,
            "self_approval_ignored": bool(not eligible and approval is not None),
            "failover_engaged": False,
            "approval_index": approval[0] if approval is not None else None,
            "block_index": block_index,
            "task_id_mismatch": latest_build_task_mismatch.get(agent),
            "shape_mismatch": latest_build_shape_mismatch.get(agent),
        }
        if not approved:
            if build_author_slot_waived:
                build_identity_reasons[role] = (
                    f"{role} ({agent}): author build-slot waiver is blocked "
                    "by a later block"
                )
            elif not eligible:
                build_identity_reasons[role] = (
                    f"{role} ({agent}): author_agent cannot satisfy its own "
                    "reviewer slot"
                )
            elif approval is None:
                mismatch = latest_build_task_mismatch.get(agent)
                shape_mismatch = latest_build_shape_mismatch.get(agent)
                if shape_mismatch is not None:
                    details: list[str] = []
                    if not shape_mismatch.get("type_ok", False):
                        details.append(
                            f"type {shape_mismatch.get('type')!r} is not one of "
                            f"{sorted(DECISION_EVENT_TYPES)!r}"
                        )
                    if not shape_mismatch.get("status_ok", False):
                        details.append(
                            f"status {shape_mismatch.get('status')!r} is not a "
                            "recognized build-consensus status"
                        )
                    if not shape_mismatch.get("task_id_ok", False):
                        details.append(
                            f"task_id {shape_mismatch.get('task_id')!r} is not "
                            f"canonical {task_id!r}"
                        )
                    build_identity_reasons[role] = (
                        f"{role} ({agent}): head-bound build-consensus-shaped "
                        "event has invalid shape: "
                        + "; ".join(details)
                    )
                elif mismatch is not None:
                    build_identity_reasons[role] = (
                        f"{role} ({agent}): head-bound approval used "
                        f"non-canonical task_id {mismatch!r}; expected {task_id!r}"
                    )
                else:
                    build_identity_reasons[role] = (
                        f"{role} ({agent}): no head-bound approval at {head_sha}"
                    )
            else:
                build_identity_reasons[role] = (
                    f"{role} ({agent}): a later block invalidates the approval"
                )

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

    lead_stall_failover = _evaluate_lead_stall_failover(
        enabled=allow_lead_stall_failover,
        events=identity_valid_events,
        lead_agent=lead_agent,
        tools_agent=tools_agent,
        author_agent=author_agent,
        task_id=task_id,
        head_sha=head_sha,
        threshold_seconds=lead_stall_failover_threshold_seconds,
        lead_approval=latest_build_approval.get(lead_agent),
        lead_block_index=latest_build_block.get(lead_agent),
        tools_approval=latest_build_approval.get(tools_agent),
        rco_approval=rco_approval,
        blocking_rco_agents=blocking_rco_agents,
        changed_paths=lead_stall_failover_changed_paths,
        diff_text=lead_stall_failover_diff_text,
        charter_path=lead_stall_failover_charter_path,
    )
    if lead_stall_failover["engaged"]:
        identities["build_lead"]["approved"] = True
        identities["build_lead"]["failover_engaged"] = True
        identities["build_lead"]["failover_reason"] = (
            "durable lead-stall failover engaged"
        )

    for role in ("build_lead", "build_tools"):
        if role == "build_lead" and lead_stall_failover["engaged"]:
            continue
        reason = build_identity_reasons.get(role)
        if reason:
            reasons.append(reason)

    if (
        allow_lead_stall_failover
        and not lead_stall_failover["engaged"]
        and not identities["build_lead"]["approved"]
    ):
        reasons.extend(
            f"lead-stall failover refused: {reason}"
            for reason in lead_stall_failover["reasons"]
        )

    build_consensus_reemit_guidance = _build_consensus_reemit_guidance(
        identities=identities,
        task_id=task_id,
        head_sha=head_sha,
    )
    ok = not reasons
    return {
        "ok": ok,
        "decision": (
            "bridge_consensus_verified" if ok else "bridge_consensus_incomplete"
        ),
        "reasons": reasons,
        "head_sha": head_sha,
        "canonical_task_id": task_id,
        "build_consensus_reemit_guidance": build_consensus_reemit_guidance,
        "identities": identities,
        "rco_pass_ref": rco_pass_ref,
        "recognized_rco_agents": list(recognized_rco_agents),
        "eligible_rco_agents": list(eligible_rco_agents),
        "build_author_slot_waivers": sorted(waived_build_author_agents),
        "author_agent": author_agent,
        "blocking_rco_agents": sorted(blocking_rco_agents),
        "ignored_identity_mismatch_events": ignored_identity_mismatch_events,
        "lead_stall_failover": lead_stall_failover,
    }


def _invalid_consensus_input(
    reason: str,
    *,
    head_sha: object,
    decision: str = "invalid_consensus_config",
) -> dict[str, Any]:
    return {
        "ok": False,
        "decision": decision,
        "reasons": [reason],
        "head_sha": head_sha if type(head_sha) is str else "",
        "identities": {},
        "rco_pass_ref": None,
        "recognized_rco_agents": [],
        "eligible_rco_agents": [],
        "author_agent": "",
        "blocking_rco_agents": [],
        "ignored_identity_mismatch_events": [],
    }


def _build_consensus_reemit_guidance(
    *,
    identities: Mapping[str, Any],
    task_id: str,
    head_sha: str,
) -> list[dict[str, Any]]:
    guidance: list[dict[str, Any]] = []
    for role in ("build_lead", "build_tools"):
        identity = identities.get(role)
        if not isinstance(identity, Mapping):
            continue
        if bool(identity.get("approved", False)):
            continue
        shape_mismatch = identity.get("shape_mismatch")
        reason = "missing_head_bound_approval"
        observed_task_id = str(identity.get("task_id_mismatch") or "")
        if isinstance(shape_mismatch, Mapping):
            reason = "invalid_shape"
            observed_task_id = str(shape_mismatch.get("task_id", observed_task_id))
        elif observed_task_id:
            reason = "non_canonical_task_id"
        guidance.append(
            {
                "role": role,
                "agent": str(identity.get("agent", "")),
                "reason": reason,
                "observed_task_id": observed_task_id,
                "expected_type": "decision",
                "expected_status": "build_consensus_pass",
                "expected_task_id": task_id,
                "expected_payload_head": head_sha,
            }
        )
    return guidance


def _lead_stall_failover_disabled_report(
    *, head_sha: str, threshold_seconds: int
) -> dict[str, Any]:
    return {
        "enabled": False,
        "engaged": False,
        "eligible": False,
        "decision": "not_enabled",
        "reasons": ["lead-stall failover is disabled"],
        "head_sha": head_sha,
        "threshold_seconds": threshold_seconds,
        "clock_source": "durable_bridge_event_log_latest_ts",
        "observed_at": None,
        "lead_last_substantive_ts": None,
        "lead_last_substantive_index": None,
        "lead_idle_seconds": None,
        "charter_clean": False,
        "path_gate": None,
        "diff_gate": None,
        "tools_non_author": False,
        "tools_build_consensus_at_head": False,
        "rco_pass_at_head": False,
        "lead_direct_approval_present": False,
        "lead_block_present": False,
        "blocking_rco_agents": [],
    }


def _evaluate_lead_stall_failover(
    *,
    enabled: bool,
    events: Sequence[tuple[int, Mapping[str, Any]]],
    lead_agent: str,
    tools_agent: str,
    author_agent: str,
    task_id: str,
    head_sha: str,
    threshold_seconds: int,
    lead_approval: tuple[int, Mapping[str, Any]] | None,
    lead_block_index: int | None,
    tools_approval: tuple[int, Mapping[str, Any]] | None,
    rco_approval: tuple[int, Mapping[str, Any]] | None,
    blocking_rco_agents: Sequence[str],
    changed_paths: Sequence[str],
    diff_text: str,
    charter_path: Path,
) -> dict[str, Any]:
    report = _lead_stall_failover_disabled_report(
        head_sha=head_sha,
        threshold_seconds=threshold_seconds,
    )
    if not enabled:
        return report

    report.update(
        {
            "enabled": True,
            "decision": "refused",
            "reasons": [],
            "tools_non_author": bool(
                author_agent and author_agent != tools_agent
            ),
            "tools_build_consensus_at_head": tools_approval is not None,
            "rco_pass_at_head": (
                rco_approval is not None and not blocking_rco_agents
            ),
            "lead_direct_approval_present": lead_approval is not None,
            "lead_block_present": lead_block_index is not None,
            "blocking_rco_agents": sorted(str(a) for a in blocking_rco_agents),
            "task_id": task_id,
        }
    )
    reasons: list[str] = []
    if threshold_seconds <= 0:
        reasons.append("threshold_seconds must be positive")

    try:
        charter = load_charter(charter_path)
        path_gate = evaluate_paths(charter, changed_paths)
        diff_gate = evaluate_diff_content(charter, diff_text)
    except (OSError, ValueError) as exc:
        path_gate = None
        diff_gate = None
        reasons.append(f"charter evaluation failed: {exc.__class__.__name__}")
    else:
        report["path_gate"] = _gate_to_dict(path_gate)
        report["diff_gate"] = _gate_to_dict(diff_gate)
        report["charter_clean"] = bool(path_gate.allowed and diff_gate.allowed)
        if not path_gate.allowed:
            reasons.append(f"charter path gate failed: {path_gate.reason}")
        if not diff_gate.allowed:
            reasons.append(f"charter diff gate failed: {diff_gate.reason}")

    observed = _lead_stall_observed_at(events)
    if observed is None:
        reasons.append("no parsable durable event timestamp for idle proof")
    else:
        report["observed_at"] = observed.isoformat().replace("+00:00", "Z")

    lead_last = _last_substantive_lead_event(events, lead_agent=lead_agent)
    if lead_last is None:
        reasons.append("no substantive lead event found in durable bridge log")
    else:
        lead_index, lead_event, lead_ts = lead_last
        report["lead_last_substantive_index"] = lead_index
        report["lead_last_substantive_ts"] = (
            lead_ts.isoformat().replace("+00:00", "Z")
        )
        if observed is not None:
            idle_seconds = int((observed - lead_ts).total_seconds())
            report["lead_idle_seconds"] = idle_seconds
            if idle_seconds < threshold_seconds:
                reasons.append(
                    "lead idle duration below threshold: "
                    f"{idle_seconds}/{threshold_seconds}s"
                )

    if observed is not None:
        now_utc = datetime.now(timezone.utc)
        if observed > now_utc + timedelta(minutes=5):
            reasons.append("durable event observation timestamp is in the future")

    if lead_approval is not None:
        reasons.append("lead direct approval already satisfies the build slot")
    if lead_block_index is not None:
        reasons.append("lead block/change request at this scope hard-blocks failover")
    if author_agent == tools_agent:
        reasons.append("tools-authored PRs cannot use tools-only lead failover")
    elif not author_agent:
        reasons.append("author_agent is required for lead-stall failover")
    if tools_approval is None:
        reasons.append("tools build_consensus at exact head is required")
    if rco_approval is None:
        reasons.append("recognized non-author RCO_PASS at exact head is required")
    if blocking_rco_agents:
        reasons.append(
            "recognized RCO veto blocks failover: "
            + ", ".join(sorted(str(a) for a in blocking_rco_agents))
        )

    report["reasons"] = reasons
    report["eligible"] = not reasons
    report["engaged"] = not reasons
    report["decision"] = "engaged" if report["engaged"] else "refused"
    return report


def _lead_stall_observed_at(
    events: Sequence[tuple[int, Mapping[str, Any]]],
) -> datetime | None:
    timestamps = [
        parsed
        for _index, event in events
        for parsed in [_parse_bridge_ts(event.get("ts_utc"))]
        if parsed is not None
    ]
    if not timestamps:
        return None
    return max(timestamps)


def _last_substantive_lead_event(
    events: Sequence[tuple[int, Mapping[str, Any]]], *, lead_agent: str
) -> tuple[int, Mapping[str, Any], datetime] | None:
    latest: tuple[int, Mapping[str, Any], datetime] | None = None
    for index, event in events:
        if str(event.get("agent", "")) != lead_agent:
            continue
        if not _is_substantive_lead_event(event):
            continue
        parsed = _parse_bridge_ts(event.get("ts_utc"))
        if parsed is None:
            continue
        if latest is None or parsed > latest[2] or (
            parsed == latest[2] and index > latest[0]
        ):
            latest = (index, event, parsed)
    return latest


def _is_substantive_lead_event(event: Mapping[str, Any]) -> bool:
    event_type = str(event.get("type", "")).lower()
    status = str(event.get("status", "")).lower()
    if event_type in LEAD_STALL_NON_SUBSTANTIVE_TYPES:
        return False
    normalized_status = re.sub(r"[^a-z0-9]+", "_", status).strip("_")
    if normalized_status in LEAD_STALL_NON_SUBSTANTIVE_STATUSES:
        return False
    return True


def _parse_bridge_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _evaluate_bridge_consensus(
    *,
    require: bool,
    events: Sequence[Mapping[str, Any]],
    events_path: Path | None,
    task_id: str,
    head_sha: str,
    pr_number: int | None,
    author_agent: str,
    charter_path: Path,
    changed_paths: Sequence[str],
    diff_text: str,
    allow_lead_stall_failover: bool,
    lead_stall_failover_threshold_seconds: int,
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
            "lead_stall_failover": _lead_stall_failover_disabled_report(
                head_sha=head_sha,
                threshold_seconds=lead_stall_failover_threshold_seconds,
            ),
        }
    result = verify_bridge_consensus(
        events=events,
        task_id=task_id,
        head_sha=head_sha,
        pr_number=pr_number,
        author_agent=author_agent,
        allow_lead_stall_failover=allow_lead_stall_failover,
        lead_stall_failover_threshold_seconds=(
            lead_stall_failover_threshold_seconds
        ),
        lead_stall_failover_changed_paths=changed_paths,
        lead_stall_failover_diff_text=diff_text,
        lead_stall_failover_charter_path=charter_path,
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
        for key in (
            "head",
            "head_sha",
            "expected_head",
            "exact_head",
            "head_oid",
            "head_commit",
        ):
            value = payload.get(key)
            if isinstance(value, str) and value.strip().lower() == head_sha:
                return True
    message = event.get("message")
    if isinstance(message, str) and head_sha in message.lower():
        return True
    return False


def _is_consensus_block(status: str, *, event_type: str = "") -> bool:
    return _bridge_is_blocking_status(status, event_type=event_type)


def _is_consensus_clear(status: str, *, event_type: str = "") -> bool:
    normalized_event_type = re.sub(r"[^a-z0-9]+", "_", event_type.lower()).strip("_")
    if normalized_event_type and normalized_event_type not in CONSENSUS_CLEAR_EVENT_TYPES:
        return False
    return _bridge_is_clear_status(status)


def _accepted_queue_preflight(
    *,
    events_path: Path | None,
    checker: AcceptedQueueChecker | None,
) -> dict[str, Any]:
    if events_path is None:
        return {
            "ok": False,
            "complete": False,
            "decision": "accepted_queue_events_path_missing",
            "errors": ["bridge events path is required for accepted queue proof"],
        }
    bridge_root = (
        events_path.parent.parent
        if events_path.name == "events.jsonl" and events_path.parent.name == "shared"
        else events_path.parent
    )
    run = check_accepted_queue_complete if checker is None else checker
    try:
        report = run(bridge_root=bridge_root, events_path=events_path)
    except Exception as exc:  # noqa: BLE001 - a gate checker always fails closed
        return {
            "ok": False,
            "complete": False,
            "decision": "accepted_queue_preflight_exception",
            "errors": [f"{type(exc).__name__}: accepted queue preflight failed"],
        }
    if not isinstance(report, Mapping):
        return {
            "ok": False,
            "complete": False,
            "decision": "accepted_queue_preflight_invalid_result",
            "errors": ["accepted queue preflight must return an object"],
        }
    return dict(report)


def _bridge_peer_gate(
    *,
    events: Sequence[Mapping[str, Any]],
    task_id: str,
    pr_number: int,
    from_agent: str,
    author_agent: str,
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
            "author_agent": author_agent,
            "latest_blocking_event": None,
            "latest_approval_event": None,
        }
    return check_bridge_clear_to_merge(
        events=events,
        task_id=task_id,
        merging_agent=from_agent,
        author_agent=author_agent,
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
    if type(value) is not int:
        raise _invalid("invalid_pr_status", f"{field} must be an integer")
    return value


def _validate_evaluate_inputs(
    *,
    pr_status: object,
    expected_head: object,
    consensus_proposal_id: object,
    expected_base_sha: object,
    receipt_bundle_path: object,
    events_path: object,
    charter_path: object,
    utc_date: object,
    repo: object,
    from_agent: object,
    bridge_task_id: object,
    apply: object,
    require_bridge_consensus: object,
    standing_consensus_sign: object,
    allow_lead_stall_failover: object,
    lead_stall_failover_threshold_seconds: object,
    runner: object,
    merge_verifier: object,
    artifact_writer: object,
    accepted_queue_checker: object,
) -> None:
    if not isinstance(pr_status, Mapping):
        raise _invalid("invalid_pr_status", "pr_status must be an object")
    for field, value in (
        ("expected_head", expected_head),
        ("consensus_proposal_id", consensus_proposal_id),
        ("expected_base_sha", expected_base_sha),
        ("receipt_bundle_path", receipt_bundle_path),
        ("repo", repo),
        ("from_agent", from_agent),
        ("bridge_task_id", bridge_task_id),
    ):
        if type(value) is not str:
            raise _invalid("invalid_input", f"{field} must be a string")
    if utc_date is not None and type(utc_date) is not str:
        raise _invalid("invalid_input", "utc_date must be a string or null")
    if events_path is not None and not isinstance(events_path, Path):
        raise _invalid("invalid_input", "events_path must be a Path or null")
    if not isinstance(charter_path, Path):
        raise _invalid("invalid_input", "charter_path must be a Path")
    for field, value in (
        ("apply", apply),
        ("require_bridge_consensus", require_bridge_consensus),
        ("standing_consensus_sign", standing_consensus_sign),
        ("allow_lead_stall_failover", allow_lead_stall_failover),
    ):
        if type(value) is not bool:
            raise _invalid("invalid_input", f"{field} must be a boolean")
    if type(lead_stall_failover_threshold_seconds) is not int:
        raise _invalid(
            "invalid_input",
            "lead_stall_failover_threshold_seconds must be an integer",
        )
    for field, value in (
        ("runner", runner),
        ("merge_verifier", merge_verifier),
        ("artifact_writer", artifact_writer),
        ("accepted_queue_checker", accepted_queue_checker),
    ):
        if value is not None and not callable(value):
            raise _invalid(
                "invalid_input",
                f"{field} must be callable or null",
            )
    if (
        not consensus_proposal_id
        or consensus_proposal_id != consensus_proposal_id.strip()
    ):
        raise _invalid(
            "invalid_input",
            "consensus_proposal_id must be a non-empty exact string",
        )
    if repo != repo.strip():
        raise _invalid("invalid_input", "repo must be an exact string")
    if from_agent and (
        from_agent != from_agent.strip()
        or AGENT_ID_PATTERN.fullmatch(from_agent) is None
    ):
        raise _invalid(
            "invalid_input",
            "from_agent must be an exact agent id or empty",
        )
    if bridge_task_id and bridge_task_id != bridge_task_id.strip():
        raise _invalid(
            "invalid_input",
            "bridge_task_id must be an exact string or empty",
        )
    if utc_date is not None:
        try:
            parsed_date = datetime.strptime(utc_date, "%Y-%m-%d").date()
        except ValueError as exc:
            raise _invalid(
                "invalid_input",
                "utc_date must use exact YYYY-MM-DD format",
            ) from exc
        if parsed_date.isoformat() != utc_date:
            raise _invalid(
                "invalid_input",
                "utc_date must use exact YYYY-MM-DD format",
            )


def _validate_sha(value: object, field: str) -> None:
    if type(value) is not str or not SHA_RE.fullmatch(value):
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


def _run_command(command: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        list(command),
        check=False,
        capture_output=True,
    )


def _query_pr_merge_state(
    pr_number: int,
    expected_head: str,
    repo: str,
    *,
    runner: Runner | None = None,
) -> Mapping[str, Any]:
    if runner is not None and not callable(runner):
        return {
            "ok": False,
            "decision": "merge_state_query_invalid_runner",
        }
    command = [
        "gh",
        "pr",
        "view",
        str(pr_number),
        "--json",
        "number,state,mergeCommit,headRefOid,headRefName,baseRefName",
    ]
    if repo:
        command.extend(["--repo", repo])
    run = runner if runner is not None else _run_command
    result = run(command)
    return_code = getattr(result, "returncode", None)
    if type(return_code) is not int:
        return {
            "ok": False,
            "decision": "merge_state_query_invalid_result",
        }
    try:
        stdout = _strict_utf8_result_stream(
            getattr(result, "stdout", ""),
        )
        _strict_utf8_result_stream(getattr(result, "stderr", ""))
    except (TypeError, UnicodeError):
        return {
            "ok": False,
            "decision": "merge_state_query_invalid_utf8",
        }
    if return_code != 0:
        return {
            "ok": False,
            "decision": "merge_state_query_failed",
            "return_code": return_code,
        }
    _assert_no_private_markers(stdout)
    try:
        state = json.loads(
            stdout,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"invalid JSON constant: {value}")
            ),
            object_pairs_hook=_reject_duplicate_json_object_keys,
        )
    except (json.JSONDecodeError, ValueError):
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
    if state.get("number") != pr_number:
        return {
            **dict(state),
            "ok": False,
            "decision": "merge_state_query_pr_mismatch",
        }
    if state.get("headRefOid") != expected_head:
        return {
            **dict(state),
            "ok": False,
            "decision": "merge_state_query_head_mismatch",
        }
    return state


def _strict_utf8_result_stream(value: object) -> str:
    if type(value) is bytes:
        return value.decode("utf-8", errors="strict")
    if type(value) is str:
        value.encode("utf-8", errors="strict")
        return value
    raise TypeError("subprocess stream must be text or bytes")


def _reject_duplicate_json_object_keys(
    pairs: Sequence[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


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
    manifest = bundle.get("manifest")
    if (
        type(manifest) is not str
        or not manifest
        or manifest != manifest.strip()
    ):
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
    try:
        text = events_path.read_bytes().decode("utf-8-sig", errors="strict")
    except UnicodeError as exc:
        raise _invalid(
            "invalid_events",
            "bridge events file must be valid UTF-8",
        ) from exc
    except OSError as exc:
        raise _invalid(
            "invalid_events",
            f"bridge events file could not be read: {exc.__class__.__name__}",
        ) from exc
    events: list[dict[str, Any]] = []
    for line_no, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        event = _strict_json_loads(
            line,
            decision="invalid_events",
            context=f"line {line_no}",
        )
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
    if payload.get("auto_merged") is True:
        return True
    return event.get("type") == "done" and "auto_merge" in str(event.get("status", ""))


def _today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _strict_json_loads(
    text: object,
    *,
    decision: str,
    context: str,
) -> object:
    if type(text) is not str:
        raise _invalid(decision, f"{context}: JSON input must be text")

    def reject_constant(value: str) -> object:
        raise ValueError(f"non-standard JSON constant: {value}")

    try:
        return json.loads(
            text,
            parse_constant=reject_constant,
            object_pairs_hook=_reject_duplicate_json_object_keys,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        message = exc.msg if isinstance(exc, json.JSONDecodeError) else str(exc)
        raise _invalid(decision, f"{context}: {message}") from exc


def _cli_artifact_writer(
    args: argparse.Namespace,
    events_path: Path,
) -> ArtifactWriter | None:
    if not args.apply or args.receipt_out_dir is None:
        return None

    out_dir = args.artifact_out_dir or DEFAULT_ARTIFACT_OUT_DIR
    now_utc = _parse_utc(args.now) if args.now else datetime.now(timezone.utc)

    def writer() -> Mapping[str, Any]:
        return write_idle_consensus_artifact(
            events_path=events_path,
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
