# SPDX-License-Identifier: BUSL-1.1
"""Build a fail-closed dry-run promotion snapshot for a GitHub PR.

This tool performs read-only GitHub and local event inspection, then calls
tools.check_promotion_eligible. It never undrafts, merges, pushes, or writes
runtime bridge events.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.check_promotion_eligible import (  # noqa: E402
    DEFAULT_EVENTS_PATH,
    DEFAULT_RCO_AGENTS,
    evaluate_promotion_eligibility,
)
from tools.bridge_pr_author import resolve_bridge_pr_author  # noqa: E402
from tools.pr_status_snapshot import (  # noqa: E402
    PrStatusSnapshotError,
    build_pr_status_snapshot,
)
from waggledance.core.idle_consensus_charter import (  # noqa: E402
    DEFAULT_CHARTER_PATH,
)
from waggledance.core.work_queue import resolve_bridge_root  # noqa: E402

RunnerResult = Any
Runner = Callable[[Sequence[str]], RunnerResult]
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SAFETY_FLAGS = (
    "external_effect",
    "runtime_authority_granted",
    "external_writes_applied",
)


class PromotionSnapshotError(ValueError):
    """Raised when the dry-run snapshot cannot be built safely."""

    def __init__(
        self,
        message: str,
        *,
        decision: str = "invalid_input",
        operator_required: bool = False,
        author_resolution: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.decision = decision
        self.operator_required = operator_required
        self.author_resolution = (
            dict(author_resolution) if author_resolution is not None else None
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a dry-run autonomous promotion eligibility snapshot.",
    )
    parser.add_argument("--repo", required=True, help="GitHub repository owner/name.")
    parser.add_argument("--pr-number", type=int, required=True)
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
    parser.add_argument("--charter", type=Path, default=DEFAULT_CHARTER_PATH)
    parser.add_argument(
        "--task-id",
        default="",
        help=(
            "Canonical bridge task id. Defaults to and must exactly equal the "
            "PR headRefName."
        ),
    )
    parser.add_argument(
        "--origin-main-sha",
        default="",
        help="Current origin/main SHA. Defaults to git rev-parse origin/main.",
    )
    parser.add_argument(
        "--author-agent",
        default="",
        help=(
            "Optional expected author-agent assertion. It cannot override the "
            "UUID-bound canonical claim."
        ),
    )
    parser.add_argument(
        "--from-agent",
        default="codex-lead-1",
        help="Agent identity for peer-veto preflight scoping.",
    )
    parser.add_argument("--prior-approved-head", default="")
    parser.add_argument("--prior-approved-diff-file", type=Path, default=None)
    parser.add_argument(
        "--rco-agent",
        action="append",
        default=None,
        help=(
            "Recognized RCO identity. May be repeated. Defaults to "
            "claude-rco-1 and claude-rco-2."
        ),
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None, *, runner: Runner | None = None) -> int:
    args = build_parser().parse_args(argv)
    events_path = args.events
    if events_path is None:
        events_path = resolve_bridge_root(args.bridge_root) / "shared" / "events.jsonl"
    report = build_promotion_snapshot(
        repo=args.repo,
        pr_number=args.pr_number,
        events_path=events_path,
        charter_path=args.charter,
        task_id=args.task_id,
        origin_main_sha=args.origin_main_sha,
        author_agent=args.author_agent,
        from_agent=args.from_agent,
        prior_approved_head=args.prior_approved_head,
        prior_approved_diff_file=args.prior_approved_diff_file,
        rco_agents=args.rco_agent,
        runner=runner,
    )

    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(report["decision"])
        for reason in report.get("reasons", []):
            print(f"- {reason}")
        for error in report.get("errors", []):
            print(f"- {error}", file=sys.stderr)

    if report.get("eligible") is True:
        return 0
    return 2 if report.get("decision") == "invalid_input" else 3


def build_promotion_snapshot(
    *,
    repo: str,
    pr_number: int,
    events_path: Path = DEFAULT_EVENTS_PATH,
    charter_path: Path = DEFAULT_CHARTER_PATH,
    task_id: str = "",
    origin_main_sha: str = "",
    author_agent: str = "",
    from_agent: str = "codex-lead-1",
    prior_approved_head: str = "",
    prior_approved_diff_file: Path | None = None,
    rco_agents: Sequence[str] | None = None,
    runner: Runner | None = None,
) -> dict[str, Any]:
    """Return a dry-run report and never execute promotion commands."""
    try:
        repo = _required_str(repo, "repo")
        if type(pr_number) is not int or pr_number <= 0:
            raise PromotionSnapshotError("pr_number must be a positive integer")
        origin_main_sha = _origin_main_sha(origin_main_sha, runner=runner)
        try:
            pr_status = build_pr_status_snapshot(
                pr_number=pr_number,
                repo=repo,
                runner=runner,
            )
        except PrStatusSnapshotError as exc:
            errors = exc.report.get("errors") or ["canonical PR snapshot failed"]
            raise PromotionSnapshotError(
                "canonical PR snapshot failed: "
                + "; ".join(str(error) for error in errors)
            ) from exc
        head_ref_name = _required_str(pr_status.get("head_ref"), "headRefName")
        task_id = _required_str(task_id or head_ref_name, "task_id")
        head_sha = _required_sha(pr_status.get("head_sha"), "headRefOid")
        base_sha = _required_sha(pr_status.get("base_sha"), "baseRefOid")
        changed_paths = pr_status["changed_paths"]
        diff_text = pr_status["diff_text"]
        git_identities = pr_status["git_identities"]
        git_identity_evidence = pr_status["git_identity_evidence"]
        events = _read_events_fail_closed(events_path)
        author_resolution = resolve_bridge_pr_author(
            events=events,
            pr_number=pr_number,
            task_id=task_id,
            head_ref_name=head_ref_name,
            head_sha=head_sha,
            base_sha=base_sha,
            changed_paths=changed_paths,
            expected_head_sha=head_sha,
            expected_base_sha=base_sha,
            git_identities=git_identities,
            git_identity_evidence=git_identity_evidence,
            asserted_author_agent=author_agent,
        )
        if author_resolution.get("ok") is not True:
            reasons = author_resolution.get("reasons") or [
                "PR author could not be resolved"
            ]
            raise PromotionSnapshotError(
                "PR author resolution requires operator review: "
                + "; ".join(str(reason) for reason in reasons),
                decision="operator_review_required",
                operator_required=True,
                author_resolution=author_resolution,
            )
        author_agent = str(author_resolution["author_agent"])
        prior_approved_diff_text = None
        if prior_approved_diff_file is not None:
            prior_approved_diff_text = prior_approved_diff_file.read_text(
                encoding="utf-8"
            )
        eligibility = evaluate_promotion_eligibility(
            pr_status=pr_status,
            events=events,
            task_id=task_id,
            head=head_sha,
            origin_main_sha=origin_main_sha,
            pr_number=pr_number,
            prior_approved_head=prior_approved_head,
            prior_approved_diff_text=prior_approved_diff_text,
            charter_path=charter_path,
            rco_agents=rco_agents,
            author_agent=author_agent,
            from_agent=from_agent,
        )
        return _report(
            repo=repo,
            pr_number=pr_number,
            task_id=task_id,
            head_ref_name=head_ref_name,
            head_sha=head_sha,
            base_sha=base_sha,
            origin_main_sha=origin_main_sha,
            author_agent=author_agent,
            pr_status=pr_status,
            eligibility=eligibility,
            author_resolution=author_resolution,
        )
    except (OSError, json.JSONDecodeError, PromotionSnapshotError) as exc:
        if isinstance(exc, PromotionSnapshotError):
            return _invalid_report(
                str(exc),
                decision=exc.decision,
                operator_required=exc.operator_required,
                author_resolution=exc.author_resolution,
            )
        return _invalid_report(str(exc))


def _origin_main_sha(value: str, *, runner: Runner | None) -> str:
    if value:
        return _required_sha(value, "origin_main_sha")
    completed = _run(["git", "rev-parse", "origin/main"], runner=runner)
    return _required_sha(completed.stdout.strip(), "origin_main_sha")


def _run(command: Sequence[str], *, runner: Runner | None) -> RunnerResult:
    if runner is None:
        raw_completed = subprocess.run(
            list(command),
            check=False,
            capture_output=True,
        )
        raw_streams = {
            "stdout": getattr(raw_completed, "stdout", None),
            "stderr": getattr(raw_completed, "stderr", None),
        }
        if any(type(value) is not bytes for value in raw_streams.values()):
            raise PromotionSnapshotError(
                "subprocess result streams must be bytes"
            )
        decoded: dict[str, str] = {}
        for stream_name, value in raw_streams.items():
            try:
                decoded[stream_name] = value.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise PromotionSnapshotError(
                    f"command emitted invalid UTF-8 on {stream_name}: "
                    f"{command[0]}"
                ) from exc
        completed = subprocess.CompletedProcess(
            list(command),
            int(getattr(raw_completed, "returncode", 1)),
            decoded["stdout"],
            decoded["stderr"],
        )
    else:
        completed = runner(tuple(command))
    if int(getattr(completed, "returncode", 1)) != 0:
        stderr = str(getattr(completed, "stderr", "")).strip()
        detail = f": {stderr}" if stderr else ""
        raise PromotionSnapshotError(f"command failed: {' '.join(command)}{detail}")
    stdout = getattr(completed, "stdout", None)
    stderr = getattr(completed, "stderr", None)
    if type(stdout) is not str or type(stderr) is not str:
        raise PromotionSnapshotError("runner result streams must be text")
    return completed


def _normalize_checks(raw: object) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        raise PromotionSnapshotError("statusCheckRollup must be a list")
    checks: list[dict[str, str]] = []
    for index, check in enumerate(raw, 1):
        if not isinstance(check, Mapping):
            raise PromotionSnapshotError(
                f"statusCheckRollup item {index} must be an object"
            )
        name = str(check.get("name") or check.get("context") or "").strip()
        if not name:
            raise PromotionSnapshotError(
                f"statusCheckRollup item {index} missing name/context"
            )
        state = str(check.get("state", "") or "")
        conclusion = str(check.get("conclusion", "") or "")
        status = str(check.get("status", "") or "")
        checks.append(
            {
                "name": name,
                "state": state,
                "conclusion": conclusion,
                "status": status,
            }
        )
    return checks


def _read_events_fail_closed(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise PromotionSnapshotError(f"bridge events file not found: {path}")
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PromotionSnapshotError(
                f"invalid events line {line_number}: {exc.msg}"
            ) from exc
        if not isinstance(event, dict):
            raise PromotionSnapshotError(
                f"invalid events line {line_number}: event must be object"
            )
        events.append(event)
    return events


def _promotion_gate_diagnostics(
    *, author_agent: str, eligibility: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Return informational hints without changing promotion eligibility."""
    gate_results = eligibility.get("gate_results")
    if not isinstance(gate_results, Mapping):
        return []
    bridge = gate_results.get("bridge_consensus")
    if not isinstance(bridge, Mapping):
        return []
    by_agent = bridge.get("by_agent")
    if not isinstance(by_agent, Mapping):
        return []

    diagnostics: list[dict[str, Any]] = []
    if author_agent == "codex-lead-1" and bridge.get("ok") is not True:
        for consensus in by_agent.values():
            if not isinstance(consensus, Mapping):
                continue
            identities = consensus.get("identities")
            if not isinstance(identities, Mapping):
                continue
            lead_identity = identities.get("build_lead")
            tools_identity = identities.get("build_tools")
            rco_identity = identities.get("rco")
            if not all(
                isinstance(item, Mapping)
                for item in (lead_identity, tools_identity, rco_identity)
            ):
                continue
            if (
                lead_identity.get("agent") == "codex-lead-1"
                and lead_identity.get("approved") is not True
                and tools_identity.get("approved") is True
                and rco_identity.get("approved") is True
            ):
                diagnostics.append(
                    {
                        "kind": "lead_authored_pr_missing_build_author_slot_waiver",
                        "agent": "codex-lead-1",
                        "head_bound": True,
                        "merge_authority_changed": False,
                        "reason": (
                            "current bridge-consensus contract waives the "
                            "author's build_lead slot only when the verifier "
                            "records build_author_slot_waived; tools and RCO "
                            "approvals without that recorded waiver fail closed"
                        ),
                    }
                )
                break
    return diagnostics


def _report(
    *,
    repo: str,
    pr_number: int,
    task_id: str,
    head_ref_name: str,
    head_sha: str,
    base_sha: str,
    origin_main_sha: str,
    author_agent: str,
    pr_status: Mapping[str, Any],
    eligibility: Mapping[str, Any],
    author_resolution: Mapping[str, Any],
) -> dict[str, Any]:
    eligible = eligibility.get("eligible") is True
    route = _queue_route(eligibility)
    report = {
        "ok": eligible,
        "eligible": eligible,
        "decision": str(eligibility.get("decision", "promotion_not_eligible")),
        "dry_run": True,
        "would_execute": False,
        "queue_route": route["queue_route"],
        "next_action": route["next_action"],
        "operator_required": route["operator_required"],
        "repo": repo,
        "pr_number": pr_number,
        "task_id": task_id,
        "head_ref_name": head_ref_name,
        "head": head_sha,
        "base": base_sha,
        "origin_main_sha": origin_main_sha,
        "author_agent": author_agent,
        "author_resolution": dict(author_resolution),
        "reasons": list(eligibility.get("reasons", [])),
        "errors": list(eligibility.get("errors", [])),
        "gate_diagnostics": _promotion_gate_diagnostics(
            author_agent=author_agent,
            eligibility=eligibility,
        ),
        "pr_status": dict(pr_status),
        "eligibility": dict(eligibility),
        "undraft_cmd": [],
        "merge_cmd": [],
    }
    if eligible:
        report["undraft_cmd"] = [
            "gh",
            "pr",
            "ready",
            str(pr_number),
            "--repo",
            repo,
        ]
        report["merge_cmd"] = [
            "gh",
            "pr",
            "merge",
            str(pr_number),
            "--repo",
            repo,
            "--match-head-commit",
            head_sha,
            "--squash",
        ]
    for flag in SAFETY_FLAGS:
        report[flag] = False
    return report


def _queue_route(eligibility: Mapping[str, Any]) -> dict[str, Any]:
    """Classify a dry-run snapshot into the next queue lane.

    This is deliberately diagnostic only. The underlying eligibility report
    remains the authority for whether an executor may promote or merge.
    """
    if eligibility.get("eligible") is True:
        return {
            "queue_route": "autonomous_promotion_ready",
            "next_action": "run_promotion_executor_with_match_head",
            "operator_required": False,
        }

    gate_results = eligibility.get("gate_results")
    gates = gate_results if isinstance(gate_results, Mapping) else {}
    paths = gates.get("paths") if isinstance(gates.get("paths"), Mapping) else {}
    diff = gates.get("diff") if isinstance(gates.get("diff"), Mapping) else {}
    ci = gates.get("ci") if isinstance(gates.get("ci"), Mapping) else {}
    base = gates.get("base") if isinstance(gates.get("base"), Mapping) else {}
    rco_pass = (
        gates.get("rco_pass") if isinstance(gates.get("rco_pass"), Mapping) else {}
    )
    bridge = (
        gates.get("bridge_consensus")
        if isinstance(gates.get("bridge_consensus"), Mapping)
        else {}
    )
    peer_veto = (
        gates.get("peer_veto") if isinstance(gates.get("peer_veto"), Mapping) else {}
    )
    hex_acceptance = (
        gates.get("hex_promotion_acceptance")
        if isinstance(gates.get("hex_promotion_acceptance"), Mapping)
        else {}
    )

    if paths.get("allowed") is False or diff.get("allowed") is False:
        return {
            "queue_route": "operator_signature_required",
            "next_action": "leave_pr_for_operator_gated_review",
            "operator_required": True,
        }
    if hex_acceptance.get("ok") is False:
        return {
            "queue_route": "operator_signature_required",
            "next_action": "fix_or_route_hex_acceptance_operator_gate",
            "operator_required": True,
        }
    if ci.get("ok") is False:
        return {
            "queue_route": "await_ci_green",
            "next_action": "wait_for_or_debug_required_status_checks",
            "operator_required": False,
        }
    if base.get("ok") is False:
        base_status = str(base.get("base_status", ""))
        if base_status == "stale":
            return {
                "queue_route": "refresh_base_required",
                "next_action": "attempt_content_identical_rebase_then_recheck_ci",
                "operator_required": False,
            }
        return {
            "queue_route": "reconsensus_required",
            "next_action": "collect_fresh_head_bound_consensus",
            "operator_required": False,
        }
    if peer_veto.get("clear_to_merge") is False:
        return {
            "queue_route": "peer_blocked",
            "next_action": "address_latest_bridge_block_before_promotion",
            "operator_required": False,
        }
    if rco_pass.get("ok") is False or bridge.get("ok") is False:
        return {
            "queue_route": "await_bridge_consensus",
            "next_action": "request_missing_head_bound_build_or_rco_consensus",
            "operator_required": False,
        }
    return {
        "queue_route": "manual_triage_required",
        "next_action": "inspect_fail_closed_eligibility_reasons",
        "operator_required": False,
    }


def _invalid_report(
    error: str,
    *,
    decision: str = "invalid_input",
    operator_required: bool = False,
    author_resolution: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "ok": False,
        "eligible": False,
        "decision": decision,
        "dry_run": True,
        "would_execute": False,
        "queue_route": "manual_triage_required",
        "next_action": (
            "inspect_pr_author_evidence"
            if operator_required
            else "fix_snapshot_input_then_rerun"
        ),
        "operator_required": operator_required,
        "reasons": [],
        "errors": [error],
        "undraft_cmd": [],
        "merge_cmd": [],
        "pr_status": {},
        "eligibility": {},
    }
    if author_resolution is not None:
        report["author_resolution"] = dict(author_resolution)
    for flag in SAFETY_FLAGS:
        report[flag] = False
    return report


def _required_str(value: object, field: str) -> str:
    if type(value) is not str:
        raise PromotionSnapshotError(f"{field} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise PromotionSnapshotError(f"{field} is required")
    return cleaned


def _required_sha(value: object, field: str) -> str:
    if type(value) is not str:
        raise PromotionSnapshotError(f"{field} must be a string")
    if not SHA_RE.fullmatch(value):
        raise PromotionSnapshotError(f"{field} must be a 40-char lowercase sha")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
