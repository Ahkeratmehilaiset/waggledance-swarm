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
from tools.idle_consensus_artifact import (  # noqa: E402
    DEFAULT_OUT_DIR as DEFAULT_ARTIFACT_OUT_DIR,
    write_idle_consensus_artifact,
)
from waggledance.core.magma.canonical import sha256_digest
from waggledance.core.magma.consensus_receipt import (  # noqa: E402
    SCHEMA_VERSION as BRIDGE_CONSENSUS_RECEIPT_SCHEMA,
    build_bridge_consensus_receipt,
    compute_charter_digest,
    verify_bridge_consensus,
    verify_bridge_consensus_receipt,
)
from waggledance.core.idle_consensus_charter import (  # noqa: E402
    DEFAULT_CHARTER_PATH,
    evaluate_diff_content,
    evaluate_paths,
    load_charter,
)


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
        "rco_pass_operator_merge_required",
        "rco_pass_pending_ci",
    }
)
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
CONSENSUS_BLOCKING_NEGATION_TOKENS = frozenset(
    {"no", "not", "non", "none", "without"}
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
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        status = json.loads(args.pr_status_file.read_text(encoding="utf-8"))
        report = evaluate_auto_merge_gate(
            pr_status=status,
            expected_head=args.expected_head,
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
            artifact_writer=_cli_artifact_writer(args),
            receipt_out_dir=args.receipt_out_dir,
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
    receipt_bundle_path: str = "",
    events_path: Path | None = None,
    charter_path: Path = DEFAULT_CHARTER_PATH,
    utc_date: str | None = None,
    repo: str = "",
    from_agent: str = "",
    bridge_task_id: str = "",
    apply: bool = False,
    require_bridge_consensus: bool = False,
    runner: Runner | None = None,
    merge_verifier: MergeVerifier | None = None,
    artifact_writer: ArtifactWriter | None = None,
    receipt_out_dir: Path | None = None,
) -> dict[str, Any]:
    """Evaluate and optionally apply the final idle auto-merge gate."""
    _assert_no_private_markers(
        {
            "pr_status": pr_status,
            "expected_head": expected_head,
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
    if repo and not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise _invalid("invalid_repo", "repo must be OWNER/NAME")

    events = _read_bridge_events(events_path) if events_path is not None else []
    pr_number = _require_int(pr_status.get("pr_number"), "pr_number")
    bridge_gate_task_id = bridge_task_id.strip() or consensus_proposal_id
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
    receipt_verified = bool(pr_status.get("receipt_verified", False))
    checks = _checks(pr_status)
    artifact_hook_configured = artifact_writer is not None

    blockers: list[str] = []
    if head_sha != expected_head:
        blockers.append("exact head mismatch")
    if not path_gate.allowed:
        blockers.append(f"path gate failed: {path_gate.reason}")
    if not diff_gate.allowed:
        blockers.append(f"diff gate failed: {diff_gate.reason}")
    if mergeable not in MERGEABLE_STATES:
        blockers.append(f"mergeable state is not clean: {mergeable}")
    if not rate_gate["allowed"]:
        blockers.append(
            f"daily rate limit exceeded: {quota_used}/{quota_total} for {rate_date}"
        )
    failing_checks = [
        check
        for check in checks
        if not _check_passed(check)
    ]
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
    )
    if require_bridge_consensus:
        if events_path is None:
            blockers.append("bridge events path is required for bridge consensus")
        if (
            apply
            and receipt_out_dir is None
            and not artifact_writer
            and not receipt_bundle_path
        ):
            blockers.append(
                "bridge consensus receipt output directory is required before merge"
            )
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
        path_gate=_gate_to_dict(path_gate),
        diff_gate=_gate_to_dict(diff_gate),
        bridge_consensus=bridge_consensus,
    )
    if blockers:
        return {
            **base,
            "decision": "rate_limited"
            if not rate_gate["allowed"]
            else "operator_review_required",
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

    if require_bridge_consensus:
        consensus_receipt_report = _write_and_verify_bridge_consensus_receipt(
            events=events,
            task_id=bridge_gate_task_id,
            pr_number=pr_number,
            head_sha=head_sha,
            ci_status_snapshot={"checks": checks},
            bridge_consensus=bridge_consensus,
            charter_path=charter_path,
            consensus_proposal_id=consensus_proposal_id,
            receipt_out_dir=_resolve_bridge_receipt_out_dir(
                provided=receipt_out_dir,
                fallback_path=receipt_bundle_path,
            ),
        )
        report["bridge_consensus_receipt"] = consensus_receipt_report

    run = runner or _run_command
    result = run(command)
    return_code = int(getattr(result, "returncode", 0))
    if return_code != 0:
        merge_recovery = _recover_merge_state_after_failure(
            pr_number=pr_number,
            expected_head=expected_head,
            repo=repo,
            return_code=return_code,
            verifier=merge_verifier if merge_verifier is not None else (
                None if runner is not None else _query_pr_merge_state
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
    path_gate: Mapping[str, Any],
    diff_gate: Mapping[str, Any],
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
        "bridge_peer_gate": dict(bridge_peer_gate),
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


def verify_bridge_consensus(
    *,
    events: Sequence[Mapping[str, Any]],
    task_id: str,
    head_sha: str,
    pr_number: int | None = None,
    lead_agent: str = BRIDGE_CONSENSUS_LEAD,
    tools_agent: str = BRIDGE_CONSENSUS_TOOLS,
    rco_agent: str = BRIDGE_CONSENSUS_RCO,
) -> dict[str, Any]:
    """Fail-closed three-identity bridge-consensus verification (T0b).

    Requires a positive, head-bound approval from THREE DISTINCT agent
    identities — the lead and the tools peer (build consensus) plus an
    independent ``claude-rco-1`` ``rco_pass``. Any of the following refuses
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
    expected = (lead_agent, tools_agent, rco_agent)
    base: dict[str, Any] = {
        "ok": False,
        "decision": "bridge_consensus_incomplete",
        "reasons": [],
        "head_sha": head_sha,
        "identities": {},
        "rco_pass_ref": None,
    }
    if len({a for a in expected if a and a.strip()}) != 3:
        return {
            **base,
            "decision": "invalid_consensus_config",
            "reasons": ["bridge consensus requires three distinct agent identities"],
        }
    if not SHA_RE.fullmatch(head_sha or ""):
        return {
            **base,
            "decision": "invalid_consensus_head",
            "reasons": ["head_sha must be a 40-char lowercase sha for consensus binding"],
        }

    latest_approval: dict[str, tuple[int, Mapping[str, Any]]] = {}
    latest_block: dict[str, int] = {}
    for index, event in enumerate(events):
        if not isinstance(event, Mapping):
            continue
        agent = str(event.get("agent", ""))
        if agent not in expected:
            continue
        if not _consensus_scope_match(event, task_id=task_id, pr_number=pr_number):
            continue
        status = str(event.get("status", "")).lower()
        # Block detection is TYPE-AGNOSTIC (fail-closed): a veto from an
        # on-scope expected identity must invalidate consensus regardless of
        # the event type the vetoer used. If this honoured the
        # DECISION_EVENT_TYPES filter first, a veto posted as e.g.
        # type=blocked/status=blocked would be silently dropped and a stale
        # earlier approval would stand -- the exact fail-open T0b prevents.
        if _is_consensus_block(status):
            latest_block[agent] = index
            continue
        # Approvals remain type-restricted to decision/rco_review/finding.
        if str(event.get("type", "")).lower() not in DECISION_EVENT_TYPES:
            continue
        if not _event_binds_head(event, head_sha):
            continue
        if agent == rco_agent:
            if status in RCO_PASS_STATUSES:
                latest_approval[agent] = (index, event)
        elif status in BUILD_CONSENSUS_STATUSES:
            latest_approval[agent] = (index, event)

    reasons: list[str] = []
    identities: dict[str, Any] = {}
    for agent, role in (
        (lead_agent, "build_lead"),
        (tools_agent, "build_tools"),
        (rco_agent, "rco"),
    ):
        approval = latest_approval.get(agent)
        block_index = latest_block.get(agent)
        approved = approval is not None and (
            block_index is None or approval[0] > block_index
        )
        identities[role] = {
            "agent": agent,
            "approved": approved,
            "approval_index": approval[0] if approval is not None else None,
            "block_index": block_index,
        }
        if not approved:
            if approval is None:
                reasons.append(
                    f"{role} ({agent}): no head-bound approval at {head_sha}"
                )
            else:
                reasons.append(
                    f"{role} ({agent}): a later block invalidates the approval"
                )

    rco_pass_ref: dict[str, Any] | None = None
    rco_approval = latest_approval.get(rco_agent)
    if rco_approval is not None and identities["rco"]["approved"]:
        rco_event = rco_approval[1]
        rco_pass_ref = {
            "agent": rco_agent,
            "ts_utc": str(rco_event.get("ts_utc", "")),
            "status": str(rco_event.get("status", "")),
            "task_id": str(rco_event.get("task_id", "")),
        }

    ok = not reasons
    return {
        "ok": ok,
        "decision": "bridge_consensus_verified" if ok else "bridge_consensus_incomplete",
        "reasons": reasons,
        "head_sha": head_sha,
        "identities": identities,
        "rco_pass_ref": rco_pass_ref,
    }


def _evaluate_bridge_consensus(
    *,
    require: bool,
    events: Sequence[Mapping[str, Any]],
    events_path: Path | None,
    task_id: str,
    head_sha: str,
    pr_number: int | None,
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
        }
    result = verify_bridge_consensus(
        events=events,
        task_id=task_id,
        head_sha=head_sha,
        pr_number=pr_number,
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
    tokens = {token for token in re.split(r"[^a-z0-9]+", status.lower()) if token}
    has_blocking_shape = {"changes", "requested"}.issubset(tokens) or any(
        token.startswith("block") for token in tokens
    )
    if not has_blocking_shape:
        return False
    return not tokens.intersection(CONSENSUS_BLOCKING_NEGATION_TOKENS)


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


def _resolve_bridge_receipt_out_dir(
    *,
    provided: Path | None,
    fallback_path: str,
) -> Path | None:
    if provided is not None:
        return provided
    if not fallback_path:
        return None
    return Path(fallback_path).parent


def _write_and_verify_bridge_consensus_receipt(
    *,
    events: Sequence[Mapping[str, Any]],
    task_id: str,
    pr_number: int,
    head_sha: str,
    ci_status_snapshot: Mapping[str, Any],
    bridge_consensus: Mapping[str, Any],
    charter_path: Path | str,
    consensus_proposal_id: str,
    receipt_out_dir: Path | None,
) -> dict[str, Any]:
    if receipt_out_dir is None:
        raise AutoMergeGateError(
            {
                "decision": "bridge_consensus_receipt_failed",
                "ok": False,
                "dry_run": False,
                "external_effect": False,
                "would_merge": False,
                "operator_review_required": True,
                "errors": [
                    "bridge consensus receipt output directory is required before merge"
                ],
                "exit_code": 1,
            }
        )

    live_bridge_consensus = verify_bridge_consensus(
        events=events,
        task_id=task_id,
        head_sha=head_sha,
        pr_number=pr_number,
    )
    if not live_bridge_consensus.get("ok"):
        raise AutoMergeGateError(
            {
                "decision": "bridge_consensus_receipt_failed",
                "ok": False,
                "dry_run": False,
                "external_effect": False,
                "would_merge": False,
                "operator_review_required": True,
                "errors": ["live bridge consensus is not verified"],
                "exit_code": 1,
            }
        )
    if json.dumps(dict(bridge_consensus), sort_keys=True) != json.dumps(
        dict(live_bridge_consensus), sort_keys=True
    ):
        raise AutoMergeGateError(
            {
                "decision": "bridge_consensus_receipt_failed",
                "ok": False,
                "dry_run": False,
                "external_effect": False,
                "would_merge": False,
                "operator_review_required": True,
                "errors": [
                    "bridge_consensus_verdict_snapshot does not match live consensus"
                ],
                "exit_code": 1,
            }
        )

    identity_events = _bridge_consensus_identity_events(
        events=events,
        task_id=task_id,
        head_sha=head_sha,
        pr_number=pr_number,
    )
    required_roles = ("build_lead", "build_tools", "rco")
    missing_roles = [role for role in required_roles if role not in identity_events]
    if missing_roles:
        raise AutoMergeGateError(
            {
                "decision": "bridge_consensus_receipt_failed",
                "ok": False,
                "dry_run": False,
                "external_effect": False,
                "would_merge": False,
                "operator_review_required": True,
                "errors": [
                    (
                        "bridge consensus receipt missing "
                        f"{', '.join(missing_roles)} identity event"
                    )
                ],
                "exit_code": 1,
            }
        )

    identities = {
        role: {
            "agent": str(identity_events[role].get("agent", "")),
            "event_ts": str(identity_events[role].get("ts_utc", "")),
            "event_digest": sha256_digest(dict(identity_events[role])),
        }
        for role in required_roles
    }
    try:
        receipt = build_bridge_consensus_receipt(
            schema_version=BRIDGE_CONSENSUS_RECEIPT_SCHEMA,
            pr_number=pr_number,
            head_sha=head_sha,
            merge_commit_sha="",
            build_lead=identities["build_lead"],
            build_tools=identities["build_tools"],
            rco=identities["rco"],
            bridge_consensus_verdict_snapshot=bridge_consensus,
            charter_path=charter_path,
            charter_digest=compute_charter_digest(charter_path),
            ci_status_snapshot=ci_status_snapshot,
        )
    except (OSError, ValueError) as exc:
        raise AutoMergeGateError(
            {
                "decision": "bridge_consensus_receipt_failed",
                "ok": False,
                "dry_run": False,
                "external_effect": False,
                "would_merge": False,
                "operator_review_required": True,
                "errors": [f"bridge consensus receipt build failed: {exc}"],
                "exit_code": 1,
            }
        ) from exc
    try:
        receipt_out_dir.mkdir(parents=True, exist_ok=True)
        receipt_path = _bridge_consensus_receipt_path(
            out_dir=receipt_out_dir,
            consensus_proposal_id=consensus_proposal_id,
            pr_number=pr_number,
            head_sha=head_sha,
        )
        receipt_path.write_text(
            json.dumps(receipt, sort_keys=True, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        raise AutoMergeGateError(
            {
                "decision": "bridge_consensus_receipt_failed",
                "ok": False,
                "dry_run": False,
                "external_effect": False,
                "would_merge": False,
                "operator_review_required": True,
                "errors": [f"bridge consensus receipt write failed: {exc}"],
                "exit_code": 1,
            }
        ) from exc
    try:
        persisted_text = receipt_path.read_text(encoding="utf-8")
        persisted_receipt = json.loads(persisted_text)
    except OSError as exc:
        raise AutoMergeGateError(
            {
                "decision": "bridge_consensus_receipt_invalid",
                "ok": False,
                "dry_run": False,
                "external_effect": False,
                "would_merge": False,
                "operator_review_required": True,
                "errors": [f"bridge consensus receipt re-read failed: {exc}"],
                "bridge_consensus_receipt": {"path": str(receipt_path)},
                "exit_code": 1,
            }
        ) from exc
    except json.JSONDecodeError as exc:
        raise AutoMergeGateError(
            {
                "decision": "bridge_consensus_receipt_invalid",
                "ok": False,
                "dry_run": False,
                "external_effect": False,
                "would_merge": False,
                "operator_review_required": True,
                "errors": [f"bridge consensus receipt re-read is not valid JSON: {exc}"],
                "bridge_consensus_receipt": {"path": str(receipt_path)},
                "exit_code": 1,
            }
        ) from exc

    if persisted_receipt != receipt:
        raise AutoMergeGateError(
            {
                "decision": "bridge_consensus_receipt_invalid",
                "ok": False,
                "dry_run": False,
                "external_effect": False,
                "would_merge": False,
                "operator_review_required": True,
                "errors": ["bridge consensus receipt persisted payload changed"],
                "bridge_consensus_receipt": {
                    "path": str(receipt_path),
                    "verification": {
                        "ok": False,
                        "decision": "bridge_consensus_receipt_invalid",
                    },
                },
                "exit_code": 1,
            }
        )

    verification = verify_bridge_consensus_receipt(
        persisted_receipt,
        events=events,
        task_id=task_id,
        pr_number=pr_number,
        charter_path=charter_path,
        require_merge_commit_sha=False,
    )
    if not verification.get("ok"):
        raise AutoMergeGateError(
            {
                "decision": "bridge_consensus_receipt_invalid",
                "ok": False,
                "dry_run": False,
                "external_effect": False,
                "would_merge": False,
                "operator_review_required": True,
                "errors": verification.get("reasons", []),
                "bridge_consensus_receipt": {
                    "path": str(receipt_path),
                    "verification": verification,
                },
                "exit_code": 1,
            }
        )

    return {
        "path": str(receipt_path),
        "verification": verification,
    }


def _bridge_consensus_identity_events(
    *,
    events: Sequence[Mapping[str, Any]],
    task_id: str,
    head_sha: str,
    pr_number: int | None,
) -> dict[str, Mapping[str, Any]]:
    event_map: dict[str, Mapping[str, Any]] = {}
    latest: dict[str, tuple[int, Mapping[str, Any]]] = {}
    blocks: dict[str, int] = {}
    role_by_agent = {
        BRIDGE_CONSENSUS_LEAD: "build_lead",
        BRIDGE_CONSENSUS_TOOLS: "build_tools",
        BRIDGE_CONSENSUS_RCO: "rco",
    }
    for index, event in enumerate(events):
        if not isinstance(event, Mapping):
            continue
        agent = str(event.get("agent", ""))
        role = role_by_agent.get(agent)
        if role is None:
            continue
        if not _consensus_scope_match(event, task_id=task_id, pr_number=pr_number):
            continue
        status = str(event.get("status", "")).lower()
        if _is_consensus_block(status):
            blocks[agent] = index
            continue
        if str(event.get("type", "")).lower() not in DECISION_EVENT_TYPES:
            continue
        if not _event_binds_head(event, head_sha):
            continue
        if agent == BRIDGE_CONSENSUS_RCO:
            if status in RCO_PASS_STATUSES:
                latest[agent] = (index, event)
        elif status in BUILD_CONSENSUS_STATUSES:
            latest[agent] = (index, event)
    for agent, index_and_event in latest.items():
        if blocks.get(agent) is None or index_and_event[0] > blocks[agent]:
            role = role_by_agent[agent]
            event_map[role] = index_and_event[1]
    return event_map


def _bridge_consensus_receipt_path(
    *,
    out_dir: Path,
    consensus_proposal_id: str,
    pr_number: int,
    head_sha: str,
) -> Path:
    safe_id = re.sub(r"[^a-z0-9_.-]+", "-", str(consensus_proposal_id).lower()).strip("-")
    if not safe_id:
        safe_id = f"pr{pr_number}"
    return out_dir / f"bridge-consensus-receipt-{safe_id}-{head_sha[:8]}.json"


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
        raise _artifact_receipt_error("artifact receipt verifier did not return ok=True")
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
    for line_no, line in enumerate(events_path.read_text(encoding="utf-8").splitlines(), 1):
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
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
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
