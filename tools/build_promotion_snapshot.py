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
        help="Bridge task id. Defaults to the PR headRefName.",
    )
    parser.add_argument(
        "--origin-main-sha",
        default="",
        help="Current origin/main SHA. Defaults to git rev-parse origin/main.",
    )
    parser.add_argument(
        "--author-agent",
        default="",
        help="Override PR author agent. Default derives from matching bridge claim.",
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
        if pr_number <= 0:
            raise PromotionSnapshotError("pr_number must be a positive integer")
        view = _gh_pr_view(repo=repo, pr_number=pr_number, runner=runner)
        head_ref_name = _required_str(str(view.get("headRefName", "")), "headRefName")
        task_id = _required_str(task_id or head_ref_name, "task_id")
        head_sha = _required_sha(str(view.get("headRefOid", "")), "headRefOid")
        base_sha = _required_sha(str(view.get("baseRefOid", "")), "baseRefOid")
        origin_main_sha = _origin_main_sha(origin_main_sha, runner=runner)
        changed_paths = _gh_changed_paths(repo=repo, pr_number=pr_number, runner=runner)
        diff_text = _gh_diff_text(repo=repo, pr_number=pr_number, runner=runner)
        checks = _normalize_checks(view.get("statusCheckRollup", []))
        pr_status = {
            "pr_number": pr_number,
            "head_sha": head_sha,
            "base_sha": base_sha,
            "changed_paths": changed_paths,
            "diff_text": diff_text,
            "checks": checks,
        }
        events = _read_events_fail_closed(events_path)
        author_agent = (author_agent or "").strip() or _derive_author_agent(
            events=events,
            pr_number=pr_number,
            task_id=task_id,
            head_ref_name=head_ref_name,
            changed_paths=changed_paths,
        )
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
        )
    except (OSError, json.JSONDecodeError, PromotionSnapshotError) as exc:
        return _invalid_report(str(exc))


def _gh_pr_view(
    *, repo: str, pr_number: int, runner: Runner | None
) -> Mapping[str, Any]:
    return _run_json(
        [
            "gh",
            "pr",
            "view",
            str(pr_number),
            "--repo",
            repo,
            "--json",
            "number,headRefName,headRefOid,baseRefOid,statusCheckRollup",
        ],
        runner=runner,
    )


def _gh_changed_paths(*, repo: str, pr_number: int, runner: Runner | None) -> list[str]:
    completed = _run(
        ["gh", "pr", "diff", str(pr_number), "--repo", repo, "--name-only"],
        runner=runner,
    )
    paths = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if not paths:
        raise PromotionSnapshotError("changed_paths could not be derived")
    return paths


def _gh_diff_text(*, repo: str, pr_number: int, runner: Runner | None) -> str:
    completed = _run(
        ["gh", "pr", "diff", str(pr_number), "--repo", repo, "--patch"],
        runner=runner,
    )
    if not completed.stdout:
        raise PromotionSnapshotError("diff_text could not be derived")
    return completed.stdout


def _origin_main_sha(value: str, *, runner: Runner | None) -> str:
    if value:
        return _required_sha(value, "origin_main_sha")
    completed = _run(["git", "rev-parse", "origin/main"], runner=runner)
    return _required_sha(completed.stdout.strip(), "origin_main_sha")


def _run_json(command: Sequence[str], *, runner: Runner | None) -> Mapping[str, Any]:
    completed = _run(command, runner=runner)
    payload = json.loads(completed.stdout)
    if not isinstance(payload, Mapping):
        raise PromotionSnapshotError(f"{command[0]} JSON output must be an object")
    return payload


def _run(command: Sequence[str], *, runner: Runner | None) -> RunnerResult:
    if runner is None:
        completed = subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            text=True,
        )
    else:
        completed = runner(tuple(command))
    if int(getattr(completed, "returncode", 1)) != 0:
        stderr = str(getattr(completed, "stderr", "")).strip()
        detail = f": {stderr}" if stderr else ""
        raise PromotionSnapshotError(f"command failed: {' '.join(command)}{detail}")
    stdout = getattr(completed, "stdout", None)
    if stdout is None:
        raise PromotionSnapshotError("runner result missing stdout")
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
        return []
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


def _derive_author_agent(
    *,
    events: Sequence[Mapping[str, Any]],
    pr_number: int,
    task_id: str,
    head_ref_name: str,
    changed_paths: Sequence[str],
) -> str:
    changed = {path.lower().replace("\\", "/") for path in changed_paths}
    for event in reversed(events):
        if event.get("type") != "claim":
            continue
        agent = str(event.get("agent", "")).strip()
        write_scope = _string_list(event.get("write_scope"))
        if not agent or not write_scope:
            continue
        normalized_scope = {path.lower().replace("\\", "/") for path in write_scope}
        if changed and normalized_scope.isdisjoint(changed):
            continue
        if _claim_matches(
            event=event,
            pr_number=pr_number,
            task_id=task_id,
            head_ref_name=head_ref_name,
        ):
            return agent
    raise PromotionSnapshotError(
        "author_agent could not be derived from a matching bridge write claim"
    )


def _claim_matches(
    *,
    event: Mapping[str, Any],
    pr_number: int,
    task_id: str,
    head_ref_name: str,
) -> bool:
    payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
    if isinstance(payload, Mapping):
        if payload.get("pr") == pr_number or payload.get("pr_number") == pr_number:
            return True
        for key in ("task_id", "headRefName", "head_ref_name", "branch", "branch_name"):
            if str(payload.get(key, "")) in {task_id, head_ref_name}:
                return True

    haystack = " ".join(
        [
            str(event.get("task_id", "")),
            str(event.get("message", "")),
            " ".join(_string_list(event.get("paths"))),
            " ".join(_string_list(event.get("write_scope"))),
            json.dumps(payload, sort_keys=True),
        ]
    ).lower()
    if task_id.lower() and task_id.lower() in haystack:
        return True
    if head_ref_name.lower() and head_ref_name.lower() in haystack:
        return True
    pr_tokens = (
        f"pr #{pr_number}",
        f"pr#{pr_number}",
        f"pr{pr_number}",
        f"pull-requests/{pr_number}",
        f'"pr": {pr_number}',
        f'"pr_number": {pr_number}',
    )
    return any(token in haystack for token in pr_tokens)


def _string_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


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


def _invalid_report(error: str) -> dict[str, Any]:
    report: dict[str, Any] = {
        "ok": False,
        "eligible": False,
        "decision": "invalid_input",
        "dry_run": True,
        "would_execute": False,
        "queue_route": "manual_triage_required",
        "next_action": "fix_snapshot_input_then_rerun",
        "operator_required": False,
        "reasons": [],
        "errors": [error],
        "undraft_cmd": [],
        "merge_cmd": [],
        "pr_status": {},
        "eligibility": {},
    }
    for flag in SAFETY_FLAGS:
        report[flag] = False
    return report


def _required_str(value: str, field: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise PromotionSnapshotError(f"{field} is required")
    return cleaned


def _required_sha(value: str, field: str) -> str:
    cleaned = (value or "").strip().lower()
    if not SHA_RE.fullmatch(cleaned):
        raise PromotionSnapshotError(f"{field} must be a 40-char lowercase sha")
    return cleaned


if __name__ == "__main__":
    raise SystemExit(main())
