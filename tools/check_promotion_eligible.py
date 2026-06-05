# SPDX-License-Identifier: BUSL-1.1
"""Fail-closed verifier for autonomous draft-to-merge promotion.

This is the read-only policy surface for
docs/architecture/AUTONOMOUS_PROMOTION_PIPELINE_V1.md. It does not undraft,
merge, push, or call GitHub. It only re-derives whether a PR snapshot is
eligible for a later executor to promote.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.check_bridge_changes_requested import (  # noqa: E402
    check_bridge_clear_to_merge,
)
from tools.check_rco_pass_present import (  # noqa: E402
    check_rco_pass_present,
)
from tools.idle_check import DEFAULT_EVENTS_PATH  # noqa: E402
from tools.idle_consensus_auto_merge import (  # noqa: E402
    verify_bridge_consensus,
)
from waggledance.core.idle_consensus_charter import (  # noqa: E402
    DEFAULT_CHARTER_PATH,
    evaluate_diff_content,
    evaluate_paths,
    load_charter,
)

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DEFAULT_RCO_AGENTS = ("claude-rco-1", "claude-rco-2")
PASS_STATES = {"pass", "passed", "success", "successful", "ok"}
PRIVATE_MARKERS = ("PRIVATE_MARKER", "_DO_NOT_LEAK")

SAFETY_FLAGS = (
    "external_effect",
    "runtime_authority_granted",
    "external_writes_applied",
)


class PromotionEligibilityError(ValueError):
    """Raised for malformed verifier inputs."""

    def __init__(self, report: dict[str, Any]) -> None:
        super().__init__("; ".join(str(error) for error in report.get("errors", [])))
        self.report = report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fail-closed autonomous promotion eligibility verifier.",
    )
    parser.add_argument("--pr-status-file", type=Path, required=True)
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS_PATH)
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
        help="Prior approved diff text for content-identical carry-forward.",
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
    parser.add_argument(
        "--author-agent",
        required=True,
        help="PR author agent; a recognized RCO cannot review its own PR.",
    )
    parser.add_argument(
        "--from-agent",
        default="promotion-pipeline",
        help="Agent identity for peer-veto preflight scoping.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        pr_status = json.loads(args.pr_status_file.read_text(encoding="utf-8"))
        if not isinstance(pr_status, Mapping):
            raise PromotionEligibilityError(
                _invalid_report(
                    "invalid_pr_status", "pr status file must contain a JSON object"
                )
            )
        events = _read_events_fail_closed(args.events)
        prior_approved_diff_text = None
        if args.prior_approved_diff_file is not None:
            prior_approved_diff_text = args.prior_approved_diff_file.read_text(
                encoding="utf-8"
            )
        report = evaluate_promotion_eligibility(
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
    except (OSError, json.JSONDecodeError) as exc:
        report = _invalid_report("invalid_input", exc.__class__.__name__)
    except PromotionEligibilityError as exc:
        report = exc.report

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


def evaluate_promotion_eligibility(
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
    author_agent: str = "",
    from_agent: str = "promotion-pipeline",
) -> dict[str, Any]:
    """Return whether the PR snapshot is eligible for autonomous promotion."""
    try:
        return _evaluate_promotion_eligibility(
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
    except PromotionEligibilityError as exc:
        return exc.report


def _evaluate_promotion_eligibility(
    *,
    pr_status: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    task_id: str,
    head: str,
    origin_main_sha: str,
    pr_number: int | None,
    prior_approved_head: str,
    prior_approved_diff_text: str | None,
    charter_path: Path,
    rco_agents: Sequence[str] | None,
    author_agent: str,
    from_agent: str,
) -> dict[str, Any]:
    _assert_no_private_markers(
        {
            "pr_status": pr_status,
            "task_id": task_id,
            "head": head,
            "origin_main_sha": origin_main_sha,
            "prior_approved_head": prior_approved_head,
            "prior_approved_diff_text": prior_approved_diff_text or "",
            "rco_agents": list(rco_agents or DEFAULT_RCO_AGENTS),
            "author_agent": author_agent,
            "from_agent": from_agent,
        }
    )
    task_id = _required_str(task_id, "task_id")
    head = _required_sha(head, "head")
    origin_main_sha = _required_sha(origin_main_sha, "origin_main_sha")
    prior_approved_head = _optional_sha(prior_approved_head, "prior_approved_head")
    author_agent = (author_agent or "").strip()
    if not author_agent:
        raise PromotionEligibilityError(
            _invalid_report("invalid_input", "author_agent is required")
        )
    from_agent = (from_agent or "promotion-pipeline").strip()
    if not from_agent:
        raise PromotionEligibilityError(
            _invalid_report("invalid_input", "from_agent is required")
        )

    rco_agent_set = _recognized_rco_agents(rco_agents, author_agent=author_agent)
    number = _pr_number(pr_status, pr_number)
    status_head = _required_sha(
        str(pr_status.get("head_sha", "")), "pr_status.head_sha"
    )
    base_sha = _required_sha(str(pr_status.get("base_sha", "")), "pr_status.base_sha")
    changed_paths = _changed_paths(pr_status)
    diff_text = _diff_text(pr_status)
    checks = _checks(pr_status)

    charter = load_charter(charter_path)
    path_gate_raw = evaluate_paths(charter, changed_paths)
    diff_gate_raw = evaluate_diff_content(charter, diff_text)
    path_gate = _gate_to_dict(path_gate_raw)
    diff_gate = _gate_to_dict(diff_gate_raw)
    ci_gate = _ci_gate(checks)
    head_gate = {
        "ok": status_head == head,
        "expected_head": head,
        "snapshot_head": status_head,
    }
    base_gate = _base_status_gate(
        base_sha=base_sha,
        origin_main_sha=origin_main_sha,
        head=head,
        prior_approved_head=prior_approved_head,
        diff_text=diff_text,
        prior_approved_diff_text=_prior_approved_diff_text(
            pr_status=pr_status,
            explicit=prior_approved_diff_text,
        ),
    )
    approval_head = str(base_gate["approval_head"])
    peer_gate = check_bridge_clear_to_merge(
        events=events,
        task_id=task_id,
        merging_agent=from_agent,
        pr_number=number,
    )
    rco_pass_gate = _rco_pass_set_gate(
        events=events,
        task_id=task_id,
        head=approval_head,
        rco_agents=rco_agent_set,
        author_agent=author_agent,
    )
    bridge_consensus_gate = _bridge_consensus_set_gate(
        events=events,
        task_id=task_id,
        head=approval_head,
        pr_number=number,
        rco_agents=rco_agent_set,
        author_agent=author_agent,
    )

    reasons: list[str] = []
    if not head_gate["ok"]:
        reasons.append("head mismatch")
    if not path_gate["allowed"]:
        reasons.append(f"path gate failed: {path_gate['reason']}")
    if not diff_gate["allowed"]:
        reasons.append(f"diff gate failed: {diff_gate['reason']}")
    if not ci_gate["ok"]:
        failing = ", ".join(ci_gate["failing_checks"]) or "unknown"
        reasons.append(f"status checks not green: {failing}")
    if not base_gate["ok"]:
        reason = str(base_gate["reason"])
        reasons.append(reason if reason else "base gate failed")
    if not bool(peer_gate.get("clear_to_merge", False)):
        latest = peer_gate.get("latest_blocking_event") or {}
        agent = (
            latest.get("agent", "unknown") if isinstance(latest, Mapping) else "unknown"
        )
        status = (
            latest.get("status", "unknown")
            if isinstance(latest, Mapping)
            else "unknown"
        )
        reasons.append(f"unresolved peer bridge block: agent={agent} status={status}")
    if not rco_pass_gate["ok"]:
        reasons.append("missing exact-head RCO_PASS from recognized non-author RCO")
    if not bridge_consensus_gate["ok"]:
        reasons.append("bridge consensus incomplete")

    eligible = not reasons
    report: dict[str, Any] = {
        "ok": eligible,
        "eligible": eligible,
        "decision": "promotion_eligible" if eligible else "promotion_not_eligible",
        "dry_run": True,
        "would_promote": eligible,
        "would_undraft": eligible,
        "would_merge": eligible,
        "task_id": task_id,
        "pr_number": number,
        "head": head,
        "origin_main_sha": origin_main_sha,
        "base_status": str(base_gate["base_status"]),
        "carry_forward": bool(base_gate["carry_forward"]),
        "approval_head": approval_head,
        "prior_approved_head": prior_approved_head,
        "recognized_rco_agents": list(rco_agent_set),
        "author_agent": author_agent,
        "reasons": reasons,
        "errors": [],
        "gate_results": {
            "head": head_gate,
            "paths": path_gate,
            "diff": diff_gate,
            "ci": ci_gate,
            "base": base_gate,
            "peer_veto": dict(peer_gate),
            "rco_pass": rco_pass_gate,
            "bridge_consensus": bridge_consensus_gate,
        },
    }
    for flag in SAFETY_FLAGS:
        report[flag] = False
    return report


def _rco_pass_set_gate(
    *,
    events: Sequence[Mapping[str, Any]],
    task_id: str,
    head: str,
    rco_agents: Sequence[str],
    author_agent: str,
) -> dict[str, Any]:
    by_agent = {
        agent: check_rco_pass_present(
            events=events,
            task_id=task_id,
            head=head,
            rco_agent=agent,
            author_agent=author_agent,
        )
        for agent in rco_agents
    }
    passing = [agent for agent, result in by_agent.items() if result.get("ok") is True]
    return {
        "ok": bool(passing),
        "decision": "rco_pass_present" if passing else "rco_pass_absent",
        "satisfying_rco_agent": passing[0] if passing else "",
        "recognized_rco_agents": list(rco_agents),
        "by_agent": by_agent,
    }


def _bridge_consensus_set_gate(
    *,
    events: Sequence[Mapping[str, Any]],
    task_id: str,
    head: str,
    pr_number: int,
    rco_agents: Sequence[str],
    author_agent: str,
) -> dict[str, Any]:
    by_agent = {
        agent: verify_bridge_consensus(
            events=events,
            task_id=task_id,
            head_sha=head,
            pr_number=pr_number,
            rco_agent=agent,
            author_agent=author_agent,
        )
        for agent in rco_agents
    }
    passing = [agent for agent, result in by_agent.items() if result.get("ok") is True]
    return {
        "ok": bool(passing),
        "decision": (
            "bridge_consensus_verified" if passing else "bridge_consensus_incomplete"
        ),
        "satisfying_rco_agent": passing[0] if passing else "",
        "recognized_rco_agents": list(rco_agents),
        "by_agent": by_agent,
    }


def _base_status_gate(
    *,
    base_sha: str,
    origin_main_sha: str,
    head: str,
    prior_approved_head: str,
    diff_text: str,
    prior_approved_diff_text: str | None,
) -> dict[str, Any]:
    gate: dict[str, Any] = {
        "ok": True,
        "base_status": "fresh",
        "carry_forward": False,
        "approval_head": head,
        "snapshot_base_sha": base_sha,
        "origin_main_sha": origin_main_sha,
        "prior_approved_head": prior_approved_head,
        "reason": "",
    }
    if base_sha != origin_main_sha:
        return {
            **gate,
            "ok": False,
            "base_status": "stale",
            "reason": "base is stale",
        }
    if not prior_approved_head or prior_approved_head == head:
        return gate
    if prior_approved_diff_text is None:
        return {
            **gate,
            "ok": False,
            "base_status": "content_changed",
            "reason": "prior approved diff required for carry-forward",
        }
    if diff_text != prior_approved_diff_text:
        return {
            **gate,
            "ok": False,
            "base_status": "content_changed",
            "reason": "content changed since prior approved head",
        }
    return {
        **gate,
        "base_status": "content_identical_rebase",
        "carry_forward": True,
        "approval_head": prior_approved_head,
    }


def _prior_approved_diff_text(
    *,
    pr_status: Mapping[str, Any],
    explicit: str | None,
) -> str | None:
    if explicit is not None:
        return explicit
    value = pr_status.get("prior_approved_diff_text")
    if value is None:
        value = pr_status.get("prior_diff_text")
    if value is None:
        return None
    if not isinstance(value, str):
        raise PromotionEligibilityError(
            _invalid_report(
                "invalid_input",
                "prior_approved_diff_text must be a string when provided",
            )
        )
    return value


def _recognized_rco_agents(
    rco_agents: Sequence[str] | None,
    *,
    author_agent: str,
) -> tuple[str, ...]:
    raw = tuple(rco_agents or DEFAULT_RCO_AGENTS)
    cleaned: list[str] = []
    for agent in raw:
        value = str(agent).strip()
        if not value:
            raise PromotionEligibilityError(
                _invalid_report("invalid_input", "rco_agent must not be empty")
            )
        if value == author_agent:
            continue
        if value not in cleaned:
            cleaned.append(value)
    if not cleaned:
        raise PromotionEligibilityError(
            _invalid_report(
                "invalid_input", "no non-author recognized RCO agents configured"
            )
        )
    return tuple(cleaned)


def _ci_gate(checks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    failing = [
        str(check.get("name", "")) for check in checks if not _check_passed(check)
    ]
    return {
        "ok": bool(checks) and not failing,
        "check_count": len(checks),
        "failing_checks": failing,
    }


def _check_passed(check: Mapping[str, Any]) -> bool:
    state = str(check.get("state", "")).lower()
    conclusion = str(check.get("conclusion", "")).lower()
    status = str(check.get("status", "")).lower()
    if state in PASS_STATES or conclusion in PASS_STATES:
        return True
    if status in {"completed", "complete"} and conclusion in {"neutral", "skipped"}:
        return True
    return False


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
            raise PromotionEligibilityError(
                _invalid_report(
                    "invalid_input", f"invalid events line {line_number}: {exc.msg}"
                )
            ) from exc
        if not isinstance(event, dict):
            raise PromotionEligibilityError(
                _invalid_report(
                    "invalid_input",
                    f"invalid events line {line_number}: event must be object",
                )
            )
        events.append(event)
    _assert_no_private_markers(events)
    return events


def _pr_number(pr_status: Mapping[str, Any], override: int | None) -> int:
    value = override if override is not None else pr_status.get("pr_number")
    if not isinstance(value, int) or value <= 0:
        raise PromotionEligibilityError(
            _invalid_report("invalid_input", "pr_number must be a positive integer")
        )
    return value


def _required_str(value: str, field: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise PromotionEligibilityError(
            _invalid_report("invalid_input", f"{field} is required")
        )
    return cleaned


def _required_sha(value: str, field: str) -> str:
    cleaned = (value or "").strip().lower()
    if not SHA_RE.fullmatch(cleaned):
        raise PromotionEligibilityError(
            _invalid_report("invalid_input", f"{field} must be a 40-char lowercase sha")
        )
    return cleaned


def _optional_sha(value: str, field: str) -> str:
    cleaned = (value or "").strip().lower()
    if not cleaned:
        return ""
    if not SHA_RE.fullmatch(cleaned):
        raise PromotionEligibilityError(
            _invalid_report("invalid_input", f"{field} must be a 40-char lowercase sha")
        )
    return cleaned


def _changed_paths(pr_status: Mapping[str, Any]) -> list[str]:
    raw = pr_status.get("changed_paths")
    if not isinstance(raw, list):
        raise PromotionEligibilityError(
            _invalid_report("invalid_input", "changed_paths must be a list")
        )
    paths: list[str] = []
    for index, value in enumerate(raw, 1):
        if not isinstance(value, str) or not value.strip():
            raise PromotionEligibilityError(
                _invalid_report(
                    "invalid_input",
                    f"changed_paths item {index} must be a non-empty string",
                )
            )
        paths.append(value.strip())
    return paths


def _diff_text(pr_status: Mapping[str, Any]) -> str:
    raw = pr_status.get("diff_text")
    if not isinstance(raw, str):
        raise PromotionEligibilityError(
            _invalid_report("invalid_input", "diff_text must be a string")
        )
    return raw


def _checks(pr_status: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    checks = pr_status.get("checks")
    if checks is None:
        checks = pr_status.get("statusCheckRollup", [])
    if not isinstance(checks, list):
        raise PromotionEligibilityError(
            _invalid_report("invalid_input", "checks must be a list")
        )
    normalized: list[Mapping[str, Any]] = []
    for check in checks:
        if not isinstance(check, Mapping):
            raise PromotionEligibilityError(
                _invalid_report("invalid_input", "checks entries must be objects")
            )
        normalized.append(check)
    return normalized


def _gate_to_dict(gate: Any) -> dict[str, Any]:
    return {
        "allowed": bool(gate.allowed),
        "reason": str(gate.reason),
        "blocked_paths": list(gate.blocked_paths),
        "unmatched_paths": list(gate.unmatched_paths),
        "code_pattern_hits": list(gate.code_pattern_hits),
    }


def _invalid_report(decision: str, error: str) -> dict[str, Any]:
    report: dict[str, Any] = {
        "ok": False,
        "eligible": False,
        "decision": decision,
        "dry_run": True,
        "would_promote": False,
        "would_undraft": False,
        "would_merge": False,
        "reasons": [],
        "errors": [error],
    }
    for flag in SAFETY_FLAGS:
        report[flag] = False
    return report


def _assert_no_private_markers(value: object) -> None:
    marker = _find_private_marker(value)
    if marker is not None:
        raise PromotionEligibilityError(
            _invalid_report("invalid_input", f"privacy marker refused: {marker}")
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
