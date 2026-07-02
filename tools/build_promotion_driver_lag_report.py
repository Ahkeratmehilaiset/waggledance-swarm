# SPDX-License-Identifier: BUSL-1.1
"""Read-only report for promotion-eligible PRs awaiting the merge driver.

This tool is a diagnostic wrapper around ``check_promotion_eligible``. It does
not re-derive promotion policy, call GitHub, undraft, merge, or write bridge
events. It only summarizes whether an already eligible open PR still requires
the fail-closed promotion executor to undraft and/or merge it.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.check_promotion_eligible import (  # noqa: E402
    DEFAULT_RCO_AGENTS,
    evaluate_promotion_eligibility,
)
from waggledance.core.idle_consensus_charter import DEFAULT_CHARTER_PATH  # noqa: E402
from waggledance.core.work_queue import resolve_bridge_root  # noqa: E402

SAFETY_FALSE_FLAGS = (
    "external_effect",
    "runtime_authority_granted",
    "external_writes_applied",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a read-only promotion driver lag report.",
    )
    parser.add_argument("--pr-status-file", type=Path, required=True)
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
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--pr-number", type=int, default=None)
    parser.add_argument("--origin-main-sha", required=True)
    parser.add_argument("--prior-approved-head", default="")
    parser.add_argument(
        "--prior-approved-diff-file",
        type=Path,
        default=None,
        help=(
            "Prior approved diff text for diagnostics; carry-forward is inactive "
            "until reviewed gate wiring lands."
        ),
    )
    parser.add_argument(
        "--rco-agent",
        action="append",
        default=None,
        help=(
            "Recognized RCO identity. May be repeated. Defaults to "
            "claude-rco-1 and claude-rco-2."
        ),
    )
    parser.add_argument("--author-agent", required=True)
    parser.add_argument("--from-agent", default="promotion-driver-lag-report")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    events_path = (
        args.events
        or resolve_bridge_root(args.bridge_root) / "shared" / "events.jsonl"
    )
    try:
        pr_status = _read_json_object(args.pr_status_file, "pr status file")
        events = _read_jsonl_objects(events_path, "events")
        prior_approved_diff_text = None
        if args.prior_approved_diff_file is not None:
            prior_approved_diff_text = args.prior_approved_diff_file.read_text(
                encoding="utf-8"
            )
        report = build_promotion_driver_lag_report(
            pr_status=pr_status,
            events=events,
            task_id=args.task_id,
            head=args.head,
            origin_main_sha=args.origin_main_sha,
            pr_number=args.pr_number,
            prior_approved_head=args.prior_approved_head,
            prior_approved_diff_text=prior_approved_diff_text,
            charter_path=args.charter,
            rco_agents=args.rco_agent,
            author_agent=args.author_agent,
            from_agent=args.from_agent,
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        report = _invalid_report(str(exc))

    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        _print_human(report)
    return 2 if report.get("decision") == "invalid_input" else 0


def build_promotion_driver_lag_report(
    *,
    pr_status: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    task_id: str,
    head: str,
    origin_main_sha: str,
    pr_number: int | None = None,
    prior_approved_head: str = "",
    prior_approved_diff_text: str | None = None,
    charter_path: Path = DEFAULT_CHARTER_PATH,
    rco_agents: Sequence[str] | None = None,
    author_agent: str,
    from_agent: str = "promotion-driver-lag-report",
) -> dict[str, Any]:
    """Return a diff-free report describing pending promotion-driver work."""
    promotion = evaluate_promotion_eligibility(
        pr_status=pr_status,
        events=events,
        task_id=task_id,
        head=head,
        origin_main_sha=origin_main_sha,
        pr_number=pr_number,
        prior_approved_head=prior_approved_head,
        prior_approved_diff_text=prior_approved_diff_text,
        charter_path=charter_path,
        rco_agents=rco_agents,
        author_agent=author_agent,
        from_agent=from_agent,
    )
    if promotion.get("decision") == "invalid_input":
        return _invalid_report("; ".join(map(str, promotion.get("errors", []))))

    pr_summary = _pr_summary(pr_status=pr_status, head=head, pr_number=pr_number)
    required_actions: list[str] = []
    lag_reason = ""
    promotion_eligible = bool(promotion.get("eligible"))
    state = str(pr_summary["state"])
    is_draft = pr_summary["is_draft"]

    if not promotion_eligible:
        lag_reason = "promotion_not_eligible"
    elif state != "open":
        lag_reason = "pr_not_open"
    else:
        if is_draft is True and promotion.get("would_undraft") is True:
            required_actions.append("undraft")
        if promotion.get("would_merge") is True:
            required_actions.append("merge")
        lag_reason = "driver_action_pending" if required_actions else "driver_noop"

    driver_action_required = bool(required_actions)
    report: dict[str, Any] = {
        "ok": True,
        "decision": (
            "promotion_driver_lag_detected"
            if driver_action_required
            else "promotion_driver_lag_absent"
        ),
        "dry_run": True,
        "read_only": True,
        "network_access": False,
        "writes_bridge_events": False,
        "driver_action_required": driver_action_required,
        "required_driver_actions": required_actions,
        "lag_reason": lag_reason,
        "would_promote": bool(promotion.get("would_promote")),
        "would_undraft": bool(promotion.get("would_undraft")),
        "would_merge": bool(promotion.get("would_merge")),
        "pr": pr_summary,
        "promotion": _promotion_summary(promotion),
    }
    for flag in SAFETY_FALSE_FLAGS:
        report[flag] = False
    return report


def _promotion_summary(promotion: Mapping[str, Any]) -> dict[str, Any]:
    gate_results = promotion.get("gate_results")
    gate_ok: dict[str, bool] = {}
    if isinstance(gate_results, Mapping):
        for name, gate in gate_results.items():
            if isinstance(gate, Mapping):
                gate_ok[str(name)] = bool(
                    gate.get("ok", gate.get("allowed", gate.get("clear_to_merge")))
                )
    return {
        "eligible": bool(promotion.get("eligible")),
        "decision": str(promotion.get("decision", "")),
        "task_id": str(promotion.get("task_id", "")),
        "pr_number": promotion.get("pr_number"),
        "head": str(promotion.get("head", "")),
        "approval_head": str(promotion.get("approval_head", "")),
        "base_status": str(promotion.get("base_status", "")),
        "carry_forward": bool(promotion.get("carry_forward")),
        "recognized_rco_agents": list(
            promotion.get("recognized_rco_agents", DEFAULT_RCO_AGENTS)
        ),
        "author_agent": str(promotion.get("author_agent", "")),
        "reasons": list(promotion.get("reasons", [])),
        "errors": list(promotion.get("errors", [])),
        "gate_ok": gate_ok,
    }


def _pr_summary(
    *, pr_status: Mapping[str, Any], head: str, pr_number: int | None
) -> dict[str, Any]:
    number = pr_number if pr_number is not None else pr_status.get("pr_number")
    state = _normalized_state(pr_status)
    return {
        "number": number,
        "state": state,
        "is_draft": _draft_value(pr_status),
        "merge_state": _first_str(
            pr_status, ("merge_state", "mergeStateStatus", "mergeable")
        ),
        "head": _first_str(pr_status, ("head_sha", "headRefOid")) or head,
        "base": _first_str(pr_status, ("base_sha", "baseRefOid")),
        "merged_at": _first_str(pr_status, ("merged_at", "mergedAt")),
    }


def _normalized_state(pr_status: Mapping[str, Any]) -> str:
    state = _first_str(pr_status, ("state", "pr_state"))
    return state.lower() if state else "unknown"


def _draft_value(pr_status: Mapping[str, Any]) -> bool | None:
    for key in ("is_draft", "isDraft", "draft"):
        if key in pr_status:
            value = pr_status[key]
            if isinstance(value, bool):
                return value
    return None


def _first_str(pr_status: Mapping[str, Any], keys: Sequence[str]) -> str:
    for key in keys:
        value = pr_status.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _read_json_object(path: Path, label: str) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must contain a JSON object")
    return value


def _read_jsonl_objects(path: Path, label: str) -> list[Mapping[str, Any]]:
    if not path.exists():
        return []
    events: list[Mapping[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, Mapping):
            raise ValueError(f"{label} line {line_number} must be a JSON object")
        events.append(value)
    return events


def _invalid_report(error: str) -> dict[str, Any]:
    report: dict[str, Any] = {
        "ok": False,
        "decision": "invalid_input",
        "dry_run": True,
        "read_only": True,
        "network_access": False,
        "writes_bridge_events": False,
        "driver_action_required": False,
        "required_driver_actions": [],
        "lag_reason": "invalid_input",
        "would_promote": False,
        "would_undraft": False,
        "would_merge": False,
        "errors": [error],
    }
    for flag in SAFETY_FALSE_FLAGS:
        report[flag] = False
    return report


def _print_human(report: Mapping[str, Any]) -> None:
    print(report.get("decision", "unknown"))
    if not report.get("ok", False):
        for error in report.get("errors", []):
            print(f"- {error}", file=sys.stderr)
        return
    print(f"driver_action_required: {report.get('driver_action_required')}")
    actions = report.get("required_driver_actions", [])
    if actions:
        print(f"required_driver_actions: {', '.join(map(str, actions))}")
    print(f"lag_reason: {report.get('lag_reason')}")


if __name__ == "__main__":
    raise SystemExit(main())
