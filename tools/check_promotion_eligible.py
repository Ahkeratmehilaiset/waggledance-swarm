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
import math
from pathlib import Path, PurePosixPath
import re
import sys
from typing import Any, Mapping, Sequence
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.check_bridge_changes_requested import (  # noqa: E402
    APPROVAL_STATUSES as PEER_APPROVAL_STATUSES,
    BLOCKING_STATUSES as PEER_BLOCKING_STATUSES,
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
from waggledance.core.work_queue import resolve_bridge_root  # noqa: E402

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
AGENT_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,32}$")
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
MESSAGE_SHA_RE = re.compile(
    r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{40}(?![0-9A-Fa-f])"
)
MAX_AUTHORITY_INTEGER = (1 << 63) - 1
AUTHORITY_HEAD_KEYS = (
    "head",
    "exact_head",
    "head_sha",
    "expected_head",
    "head_oid",
    "head_commit",
)
AUTHORITY_PR_KEYS = (
    "pr",
    "pr_number",
    "pull_request",
    "pull_request_number",
)
AUTHORITY_TASK_KEYS = ("task_id", "bridge_task_id")
AUTHORITY_APPROVAL_STATUSES = frozenset(
    {
        "approved",
        "approved_ci_green",
        "build_consensus",
        "build_consensus_pass",
        "concur",
        "concurred",
        "agree",
        "agreed",
        "rco_pass",
    }
)
AUTHORITY_CANONICAL_STATUSES = frozenset(
    {
        *AUTHORITY_APPROVAL_STATUSES,
        *PEER_APPROVAL_STATUSES,
        *PEER_BLOCKING_STATUSES,
        "rco_pass_pending_ci",
    }
)
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


class _StrictJsonInputError(ValueError):
    """Raised for ambiguous or non-standard JSON input."""


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _StrictJsonInputError("duplicate object key")
        result[key] = value
    return result


def _reject_nonfinite_json_constant(_constant: str) -> Any:
    raise _StrictJsonInputError("non-finite numeric constant")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fail-closed autonomous promotion eligibility verifier.",
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
    events_path = (
        args.events
        or resolve_bridge_root(args.bridge_root) / "shared" / "events.jsonl"
    )
    try:
        pr_status = _read_pr_status_fail_closed(args.pr_status_file)
        events = _read_events_fail_closed(events_path)
        prior_approved_diff_text = None
        if args.prior_approved_diff_file is not None:
            prior_approved_diff_text = _read_utf8_text_fail_closed(
                args.prior_approved_diff_file,
                "prior approved diff file",
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
    except (OSError, json.JSONDecodeError, UnicodeError) as exc:
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
    _validate_direct_inputs(
        pr_status=pr_status,
        events=events,
        charter_path=charter_path,
        prior_approved_diff_text=prior_approved_diff_text,
    )
    task_id = _required_str(task_id, "task_id")
    head = _required_sha(head, "head")
    origin_main_sha = _required_sha(origin_main_sha, "origin_main_sha")
    prior_approved_head = _optional_sha(prior_approved_head, "prior_approved_head")
    author_agent = _required_agent_id(author_agent, "author_agent")
    from_agent = _required_agent_id(from_agent, "from_agent")
    rco_agent_set = _recognized_rco_agents(
        rco_agents,
        author_agent=author_agent,
    )
    number = _pr_number(pr_status, pr_number)
    status_head = _required_sha(
        pr_status.get("head_sha"),
        "pr_status.head_sha",
    )
    base_sha = _required_sha(
        pr_status.get("base_sha"),
        "pr_status.base_sha",
    )
    changed_paths = _changed_paths(pr_status)
    diff_text = _diff_text(pr_status)
    checks = _checks(pr_status)
    snapshot_state, mergeable, is_draft = _snapshot_state_fields(pr_status)
    _validate_event_authority_consistency(
        events,
        expected_task_id=task_id,
        expected_pr_number=number,
        expected_head=head,
    )
    prior_diff_text = _prior_approved_diff_text(
        pr_status=pr_status,
        explicit=prior_approved_diff_text,
    )
    _assert_no_private_markers(
        {
            "pr_status": pr_status,
            "events": list(events),
            "task_id": task_id,
            "head": head,
            "origin_main_sha": origin_main_sha,
            "prior_approved_head": prior_approved_head,
            "prior_approved_diff_text": prior_approved_diff_text or "",
            "rco_agents": list(rco_agent_set),
            "author_agent": author_agent,
            "from_agent": from_agent,
        }
    )
    hex_acceptance_gate = _hex_promotion_acceptance_gate(
        pr_status.get("hex_cell_promotion_acceptance")
    )

    charter = _load_charter_fail_closed(charter_path)
    path_gate_raw = evaluate_paths(charter, changed_paths)
    diff_gate_raw = evaluate_diff_content(charter, diff_text)
    path_gate = _gate_to_dict(path_gate_raw)
    diff_gate = _gate_to_dict(diff_gate_raw)
    ci_gate = _ci_gate(checks)
    snapshot_state_gate = {
        "ok": snapshot_state == "OPEN" and mergeable == "MERGEABLE",
        "state": snapshot_state,
        "mergeable": mergeable,
        "is_draft": is_draft,
    }
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
        prior_approved_diff_text=prior_diff_text,
    )
    approval_head = str(base_gate["approval_head"])
    try:
        peer_gate = check_bridge_clear_to_merge(
            events=events,
            task_id=task_id,
            merging_agent=from_agent,
            author_agent=author_agent,
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
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise PromotionEligibilityError(
            _invalid_report(
                "invalid_input",
                "bridge event evidence is malformed",
            )
        ) from exc
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
    if snapshot_state != "OPEN":
        reasons.append("PR state snapshot must be OPEN")
    if mergeable != "MERGEABLE":
        reasons.append("PR mergeable snapshot must be MERGEABLE")
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
    if not hex_acceptance_gate["ok"]:
        reasons.append(
            "hex promotion acceptance failed: "
            f"{hex_acceptance_gate['reason']}"
        )

    eligible = not reasons
    report: dict[str, Any] = {
        "ok": eligible,
        "eligible": eligible,
        "decision": "promotion_eligible" if eligible else "promotion_not_eligible",
        "dry_run": True,
        "would_promote": eligible,
        "would_undraft": eligible and is_draft,
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
            "snapshot_state": snapshot_state_gate,
            "base": base_gate,
            "peer_veto": dict(peer_gate),
            "rco_pass": rco_pass_gate,
            "bridge_consensus": bridge_consensus_gate,
            "hex_promotion_acceptance": hex_acceptance_gate,
        },
    }
    for flag in SAFETY_FLAGS:
        report[flag] = False
    return report


def _hex_promotion_acceptance_gate(raw: object) -> dict[str, Any]:
    from waggledance.core.solver_synthesis.hex_cell_competition import (
        HEX_CELL_COMPETITION_DIGEST_ALGORITHM,
        HEX_CELL_PROMOTION_ACCEPTANCE_NEXT_GATE,
        HEX_CELL_PROMOTION_ACCEPTANCE_SCHEMA_VERSION,
        HEX_CELL_PROMOTION_ACCEPTANCE_STATUS,
    )

    if raw is None:
        return {
            "ok": True,
            "decision": "hex_promotion_acceptance_not_present",
            "present": False,
            "reason": "",
        }
    if type(raw) is not dict:
        return _hex_acceptance_failure(
            "hex_cell_promotion_acceptance must be object"
        )

    errors: list[str] = []
    _require_exact(
        raw,
        "schema_version",
        HEX_CELL_PROMOTION_ACCEPTANCE_SCHEMA_VERSION,
        errors,
    )
    _require_exact(
        raw,
        "evidence_digest_algorithm",
        HEX_CELL_COMPETITION_DIGEST_ALGORITHM,
        errors,
    )
    _require_exact(
        raw,
        "promotion_acceptance_status",
        HEX_CELL_PROMOTION_ACCEPTANCE_STATUS,
        errors,
    )
    _require_exact(
        raw,
        "required_next_gate",
        HEX_CELL_PROMOTION_ACCEPTANCE_NEXT_GATE,
        errors,
    )
    _require_bool(
        raw,
        "operator_gate_required",
        _hex_operator_gate_required_expected(),
        errors,
    )
    _require_bool(raw, "operator_gate_cleared", False, errors)
    _require_bool(raw, "runtime_authority_granted", False, errors)
    _require_bool(raw, "runtime_traffic_mutation_applied", False, errors)
    _require_bool(raw, "candidate_state_mutation_applied", False, errors)

    for field in (
        "acceptance_id",
        "competition_id",
        "cell_id",
        "capability_id",
        "accepted_candidate_id",
    ):
        _require_non_empty_snapshot_str(raw, field, errors)
    _require_digest(raw, "competition_evidence_digest", errors)
    _require_digest(raw, "acceptance_digest", errors)

    acceptance_id = raw.get("acceptance_id")
    if type(acceptance_id) is str and not acceptance_id.startswith(
        "hexcellaccept:"
    ):
        errors.append("acceptance_id must start with hexcellaccept:")

    rejected = raw.get("rejected_candidate_ids")
    if type(rejected) is not list or not rejected:
        errors.append("rejected_candidate_ids must be a non-empty list")
        rejected_values: list[str] = []
    else:
        rejected_values = []
        for index, candidate_id in enumerate(rejected, 1):
            if (
                type(candidate_id) is not str
                or not candidate_id
                or candidate_id != candidate_id.strip()
            ):
                errors.append(
                    "rejected_candidate_ids item "
                    f"{index} must be a non-empty exact string"
                )
            elif candidate_id in rejected_values:
                errors.append("rejected_candidate_ids must be distinct")
            else:
                rejected_values.append(candidate_id)
    accepted = raw.get("accepted_candidate_id")
    if type(accepted) is str and accepted in rejected_values:
        errors.append("accepted_candidate_id must not be rejected")

    if errors:
        return _hex_acceptance_failure(errors[0], errors=errors)
    return {
        "ok": True,
        "decision": "hex_promotion_acceptance_valid",
        "present": True,
        "reason": "",
        "schema_version": raw["schema_version"],
        "acceptance_id": raw["acceptance_id"],
        "accepted_candidate_id": raw["accepted_candidate_id"],
        "rejected_candidate_count": len(rejected_values),
        "operator_gate_required": raw["operator_gate_required"],
        "operator_gate_cleared": raw["operator_gate_cleared"],
        "runtime_authority_granted": raw["runtime_authority_granted"],
        "runtime_traffic_mutation_applied": raw[
            "runtime_traffic_mutation_applied"
        ],
        "candidate_state_mutation_applied": raw[
            "candidate_state_mutation_applied"
        ],
    }


def _hex_acceptance_failure(
    reason: str,
    *,
    errors: Sequence[str] | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "decision": "hex_promotion_acceptance_invalid",
        "present": True,
        "reason": reason,
        "errors": list(errors or [reason]),
    }


def _hex_operator_gate_required_expected() -> bool:
    return True


def _require_exact(
    source: Mapping[str, Any],
    field: str,
    expected: str,
    errors: list[str],
) -> None:
    if source.get(field) != expected:
        errors.append(f"{field} must be {expected}")


def _require_bool(
    source: Mapping[str, Any],
    field: str,
    expected: bool,
    errors: list[str],
) -> None:
    if source.get(field) is not expected:
        errors.append(f"{field} must be {str(expected).lower()}")


def _require_non_empty_snapshot_str(
    source: Mapping[str, Any],
    field: str,
    errors: list[str],
) -> None:
    value = source.get(field)
    if (
        type(value) is not str
        or not value
        or value != value.strip()
    ):
        errors.append(f"{field} must be a non-empty exact string")


def _require_digest(
    source: Mapping[str, Any],
    field: str,
    errors: list[str],
) -> None:
    value = source.get(field)
    if type(value) is not str or not _is_sha256_digest(value):
        errors.append(f"{field} must be a sha256 digest")


def _is_sha256_digest(value: str) -> bool:
    prefix = "sha256:"
    digest = value.removeprefix(prefix)
    return (
        value.startswith(prefix)
        and len(digest) == 64
        and all(char in "0123456789abcdef" for char in digest)
    )


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
    invalid = [
        result
        for result in by_agent.values()
        if result.get("decision")
        in {
            "invalid_consensus_config",
            "invalid_consensus_head",
            "invalid_identity_registry",
        }
    ]
    passing = [agent for agent, result in by_agent.items() if result.get("ok") is True]
    if invalid:
        return {
            "ok": False,
            "decision": str(invalid[0]["decision"]),
            "satisfying_rco_agent": "",
            "recognized_rco_agents": list(rco_agents),
            "by_agent": by_agent,
        }
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
    if type(value) is not str:
        raise PromotionEligibilityError(
            _invalid_report(
                "invalid_input",
                "prior_approved_diff_text must be a string when provided",
            )
        )
    return value


def _validate_direct_inputs(
    *,
    pr_status: object,
    events: object,
    charter_path: object,
    prior_approved_diff_text: object,
) -> None:
    if type(pr_status) is not dict:
        raise PromotionEligibilityError(
            _invalid_report("invalid_input", "pr_status must be an object")
        )
    if type(events) is not list or not events:
        raise PromotionEligibilityError(
            _invalid_report(
                "invalid_input",
                "events must be a non-empty list",
            )
        )
    if any(type(event) is not dict for event in events):
        raise PromotionEligibilityError(
            _invalid_report("invalid_input", "events entries must be objects")
        )
    if type(charter_path) is not type(Path()):
        raise PromotionEligibilityError(
            _invalid_report("invalid_input", "charter_path must be a Path")
        )
    if (
        prior_approved_diff_text is not None
        and type(prior_approved_diff_text) is not str
    ):
        raise PromotionEligibilityError(
            _invalid_report(
                "invalid_input",
                "prior_approved_diff_text must be a string or null",
            )
        )
    _validate_json_tree(pr_status, "pr_status")
    _validate_json_tree(list(events), "events")
    _validate_event_envelopes(events)


def _load_charter_fail_closed(path: Path) -> Any:
    try:
        return load_charter(path)
    except Exception as exc:
        raise PromotionEligibilityError(
            _invalid_report(
                "invalid_input",
                "charter could not be loaded",
            )
        ) from exc


def _validate_event_envelopes(events: object) -> None:
    assert type(events) is list
    for index, event in enumerate(events, 1):
        assert type(event) is dict
        core_fields = (
            "ts_utc",
            "agent",
            "type",
            "status",
            "task_id",
            "message",
            "payload",
        )
        if any(field not in event for field in core_fields):
            raise PromotionEligibilityError(
                _invalid_report(
                    "invalid_input",
                    f"events item {index} is missing a core envelope field",
                )
            )
        for field in ("ts_utc", "agent", "type"):
            value = event[field]
            if (
                type(value) is not str
                or not value
                or value != value.strip()
            ):
                raise PromotionEligibilityError(
                    _invalid_report(
                        "invalid_input",
                        f"events item {index} {field} must be an exact string",
                    )
                )
        agent = event["agent"]
        if AGENT_ID_RE.fullmatch(agent) is None:
            raise PromotionEligibilityError(
                _invalid_report(
                    "invalid_input",
                    f"events item {index} agent must be a canonical agent id",
                )
            )
        event_type = event["type"]
        if event_type != event_type.lower():
            raise PromotionEligibilityError(
                _invalid_report(
                    "invalid_input",
                    f"events item {index} type must be lowercase",
                )
            )
        for field in ("status", "task_id", "message"):
            if type(event[field]) is not str:
                raise PromotionEligibilityError(
                    _invalid_report(
                        "invalid_input",
                        f"events item {index} {field} must be a string",
                    )
                )
        status = event["status"]
        if (
            status
            and _status_has_authority_semantics(status)
            and (status != status.strip() or status != status.lower())
        ):
            raise PromotionEligibilityError(
                _invalid_report(
                    "invalid_input",
                    f"events item {index} authority status must be exact lowercase",
                )
            )
        payload = event["payload"]
        if payload is not None and type(payload) not in {dict, list}:
            raise PromotionEligibilityError(
                _invalid_report(
                    "invalid_input",
                    f"events item {index} payload must be an object, list, or null",
                )
            )
        if "agent_uuid" in event:
            agent_uuid = event["agent_uuid"]
            if (
                type(agent_uuid) is not str
                or UUID_RE.fullmatch(agent_uuid) is None
            ):
                raise PromotionEligibilityError(
                    _invalid_report(
                        "invalid_input",
                        f"events item {index} agent_uuid must be canonical",
                    )
                )
        for field in ("to", "from_agent"):
            if field in event and type(event[field]) is not str:
                raise PromotionEligibilityError(
                    _invalid_report(
                        "invalid_input",
                        f"events item {index} {field} must be a string",
                    )
                )
        for field in ("paths", "write_scope", "capabilities"):
            if field not in event:
                continue
            value = event[field]
            if (
                type(value) is not list
                or any(
                    type(item) is not str
                    or (item and item != item.strip())
                    for item in value
                )
            ):
                raise PromotionEligibilityError(
                    _invalid_report(
                        "invalid_input",
                        f"events item {index} {field} must be an exact string list",
                    )
                )
        if "pid" in event and (
            type(event["pid"]) is not int
            or event["pid"] < 0
            or event["pid"] > MAX_AUTHORITY_INTEGER
        ):
            raise PromotionEligibilityError(
                _invalid_report(
                    "invalid_input",
                    f"events item {index} pid must be a nonnegative integer",
                )
            )


def _status_has_authority_semantics(status: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", status.casefold()).strip("_")
    return (
        normalized in AUTHORITY_CANONICAL_STATUSES
        or normalized.startswith(
            (
                "changes_requested",
                "rco_changes_requested",
                "rco_block",
                "block_",
                "blocked_",
            )
        )
        or (
            "build_consensus" in normalized
            and "pass" in normalized
        )
    )


def _validate_event_authority_consistency(
    events: object,
    *,
    expected_task_id: str,
    expected_pr_number: int,
    expected_head: str,
) -> None:
    assert type(events) is list
    for index, event in enumerate(events, 1):
        assert type(event) is dict
        payload = event["payload"]
        message = event["message"]
        event_task = event["task_id"]
        payload_mapping = payload if type(payload) is dict else {}
        relevant_to_task = event_task == expected_task_id
        relevant_to_payload_task = any(
            type(payload_mapping.get(key)) is str
            and payload_mapping.get(key) == expected_task_id
            for key in AUTHORITY_TASK_KEYS
        )
        relevant_to_pr = any(
            _authority_pr_value_matches(
                payload_mapping.get(key),
                expected_pr_number,
            )
            for key in AUTHORITY_PR_KEYS
            if key in payload_mapping
        )
        relevant_to_head = (
            expected_head in message.casefold()
            or any(
                type(payload_mapping.get(key)) is str
                and payload_mapping.get(key).strip().casefold() == expected_head
                for key in AUTHORITY_HEAD_KEYS
            )
        )
        if not (
            relevant_to_task
            or relevant_to_payload_task
            or relevant_to_pr
            or relevant_to_head
        ):
            continue

        message_heads = MESSAGE_SHA_RE.findall(message)
        if any(SHA_RE.fullmatch(value) is None for value in message_heads):
            raise PromotionEligibilityError(
                _invalid_report(
                    "invalid_input",
                    f"events item {index} message contains a noncanonical sha",
                )
            )
        if type(payload) is not dict:
            continue

        payload_heads: list[str] = []
        for key in AUTHORITY_HEAD_KEYS:
            if key not in payload:
                continue
            value = payload[key]
            if type(value) is not str or SHA_RE.fullmatch(value) is None:
                raise PromotionEligibilityError(
                    _invalid_report(
                        "invalid_input",
                        f"events item {index} {key} must be an exact lowercase sha",
                    )
                )
            payload_heads.append(value)
        if len(set(payload_heads)) != len(payload_heads):
            payload_heads = list(dict.fromkeys(payload_heads))
        if len(set(payload_heads)) > 1:
            raise PromotionEligibilityError(
                _invalid_report(
                    "invalid_input",
                    f"events item {index} has contradictory head evidence",
                )
            )

        payload_prs: list[int] = []
        for key in AUTHORITY_PR_KEYS:
            if key not in payload:
                continue
            value = payload[key]
            if (
                type(value) is not int
                or value <= 0
                or value > MAX_AUTHORITY_INTEGER
            ):
                raise PromotionEligibilityError(
                    _invalid_report(
                        "invalid_input",
                        f"events item {index} {key} must be a positive integer",
                    )
                )
            payload_prs.append(value)
        if len(set(payload_prs)) > 1:
            raise PromotionEligibilityError(
                _invalid_report(
                    "invalid_input",
                    f"events item {index} has contradictory PR evidence",
                )
            )

        payload_tasks: list[str] = []
        for key in AUTHORITY_TASK_KEYS:
            if key not in payload:
                continue
            value = payload[key]
            if (
                type(value) is not str
                or not value
                or value != value.strip()
            ):
                raise PromotionEligibilityError(
                    _invalid_report(
                        "invalid_input",
                        f"events item {index} {key} must be an exact string",
                    )
                )
            payload_tasks.append(value)
        if len(set(payload_tasks)) > 1:
            raise PromotionEligibilityError(
                _invalid_report(
                    "invalid_input",
                    f"events item {index} has contradictory task evidence",
                )
            )
        if event_task and payload_tasks and payload_tasks[0] != event_task:
            raise PromotionEligibilityError(
                _invalid_report(
                    "invalid_input",
                    f"events item {index} has contradictory task evidence",
                )
            )
        if (
            event_task == expected_task_id
            and payload_prs
            and payload_prs[0] != expected_pr_number
        ):
            raise PromotionEligibilityError(
                _invalid_report(
                    "invalid_input",
                    f"events item {index} has contradictory PR evidence",
                )
            )

        if (
            _status_has_authority_semantics(event["status"])
            and payload_heads
            and message_heads
            and len(set([*payload_heads, *message_heads])) > 1
        ):
            raise PromotionEligibilityError(
                _invalid_report(
                    "invalid_input",
                    f"events item {index} has contradictory head evidence",
                )
            )


def _authority_pr_value_matches(
    value: object,
    expected_pr_number: int,
) -> bool:
    if type(value) in {bool, int, float}:
        return value == expected_pr_number
    if type(value) is str:
        return value.strip() == str(expected_pr_number)
    return False


def _validate_json_tree(
    value: object,
    field: str,
    *,
    _active: set[int] | None = None,
    _depth: int = 0,
) -> None:
    if _depth > 64:
        raise PromotionEligibilityError(
            _invalid_report(
                "invalid_input",
                f"{field} exceeds the maximum JSON nesting depth",
            )
        )
    active = set() if _active is None else _active
    value_type = type(value)
    if value is None or value_type in {str, bool, int}:
        return
    if value_type is float:
        if math.isfinite(value):
            return
        raise PromotionEligibilityError(
            _invalid_report(
                "invalid_input",
                f"{field} contains a non-finite number",
            )
        )
    if value_type is list:
        identity = id(value)
        if identity in active:
            raise PromotionEligibilityError(
                _invalid_report("invalid_input", f"{field} contains a cycle")
            )
        active.add(identity)
        try:
            for index, item in enumerate(value):
                _validate_json_tree(
                    item,
                    f"{field}[{index}]",
                    _active=active,
                    _depth=_depth + 1,
                )
        finally:
            active.remove(identity)
        return
    if value_type is dict:
        identity = id(value)
        if identity in active:
            raise PromotionEligibilityError(
                _invalid_report("invalid_input", f"{field} contains a cycle")
            )
        active.add(identity)
        try:
            for key, item in value.items():
                if type(key) is not str:
                    raise PromotionEligibilityError(
                        _invalid_report(
                            "invalid_input",
                            f"{field} contains a non-string object key",
                        )
                    )
                _validate_json_tree(
                    item,
                    f"{field} object value",
                    _active=active,
                    _depth=_depth + 1,
                )
        finally:
            active.remove(identity)
        return
    raise PromotionEligibilityError(
        _invalid_report(
            "invalid_input",
            f"{field} contains a non-JSON value",
        )
    )


def _recognized_rco_agents(
    rco_agents: Sequence[str] | None,
    *,
    author_agent: str,
) -> tuple[str, ...]:
    if rco_agents is None:
        raw: Sequence[object] = DEFAULT_RCO_AGENTS
    elif type(rco_agents) is not list:
        raise PromotionEligibilityError(
            _invalid_report(
                "invalid_input",
                "rco_agents must be a non-string sequence or null",
            )
        )
    elif len(rco_agents) == 0:
        raise PromotionEligibilityError(
            _invalid_report("invalid_input", "rco_agents must not be empty")
        )
    else:
        raw = rco_agents
    cleaned: list[str] = []
    seen: set[str] = set()
    for index, agent in enumerate(raw, 1):
        value = _required_agent_id(agent, f"rco_agents item {index}")
        if value in seen:
            raise PromotionEligibilityError(
                _invalid_report(
                    "invalid_input",
                    "rco_agents must contain distinct identities",
                )
            )
        seen.add(value)
        if value == author_agent:
            continue
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
        check["name"] for check in checks if not _check_passed(check)
    ]
    return {
        "ok": bool(checks) and not failing,
        "check_count": len(checks),
        "failing_checks": failing,
    }


def _check_passed(check: Mapping[str, Any]) -> bool:
    state_value = check.get("state", "")
    conclusion_value = check.get("conclusion", "")
    status_value = check.get("status", "")
    if any(
        type(value) is not str
        for value in (state_value, conclusion_value, status_value)
    ):
        return False
    state = state_value.lower()
    conclusion = conclusion_value.lower()
    status = status_value.lower()
    if state in PASS_STATES or conclusion in PASS_STATES:
        return True
    if status in {"completed", "complete"} and conclusion in {"neutral", "skipped"}:
        return True
    return False


def _read_utf8_text_fail_closed(path: Path, context: str) -> str:
    try:
        return path.read_bytes().decode("utf-8", errors="strict")
    except FileNotFoundError as exc:
        raise PromotionEligibilityError(
            _invalid_report("invalid_input", f"{context} was not found")
        ) from exc
    except UnicodeError as exc:
        raise PromotionEligibilityError(
            _invalid_report("invalid_input", f"{context} is not valid UTF-8")
        ) from exc
    except OSError as exc:
        raise PromotionEligibilityError(
            _invalid_report("invalid_input", f"{context} could not be read")
        ) from exc


def _strict_json_loads(text: str, context: str) -> object:
    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_nonfinite_json_constant,
        )
    except (
        _StrictJsonInputError,
        json.JSONDecodeError,
        RecursionError,
        ValueError,
    ) as exc:
        raise PromotionEligibilityError(
            _invalid_report("invalid_input", f"{context} is not strict JSON")
        ) from exc
    _validate_json_tree(value, context)
    return value


def _read_pr_status_fail_closed(path: Path) -> dict[str, Any]:
    text = _read_utf8_text_fail_closed(path, "pr status file")
    value = _strict_json_loads(text, "pr status file")
    if type(value) is not dict:
        raise PromotionEligibilityError(
            _invalid_report(
                "invalid_input",
                "pr status file must contain a JSON object",
            )
        )
    return value


def _read_events_fail_closed(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    text = _read_utf8_text_fail_closed(path, "bridge events file")
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        text.splitlines(), 1
    ):
        if not line.strip():
            continue
        try:
            event = _strict_json_loads(
                line,
                f"bridge events line {line_number}",
            )
        except PromotionEligibilityError as exc:
            raise PromotionEligibilityError(
                _invalid_report(
                    "invalid_input",
                    f"bridge events line {line_number} is not strict JSON",
                )
            ) from exc
        if type(event) is not dict:
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
    if (
        type(value) is not int
        or value <= 0
        or value > MAX_AUTHORITY_INTEGER
    ):
        raise PromotionEligibilityError(
            _invalid_report("invalid_input", "pr_number must be a positive integer")
        )
    return value


def _required_str(value: object, field: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise PromotionEligibilityError(
            _invalid_report(
                "invalid_input",
                f"{field} must be a non-empty exact string",
            )
        )
    return value


def _required_agent_id(value: object, field: str) -> str:
    if type(value) is not str:
        raise PromotionEligibilityError(
            _invalid_report(
                "invalid_input",
                f"{field} must be an exact canonical agent id",
            )
        )
    if value == "":
        raise PromotionEligibilityError(
            _invalid_report("invalid_input", f"{field} is required")
        )
    if AGENT_ID_RE.fullmatch(value) is None:
        raise PromotionEligibilityError(
            _invalid_report(
                "invalid_input",
                f"{field} must be an exact canonical agent id",
            )
        )
    return value


def _required_sha(value: object, field: str) -> str:
    if type(value) is not str or not SHA_RE.fullmatch(value):
        raise PromotionEligibilityError(
            _invalid_report("invalid_input", f"{field} must be a 40-char lowercase sha")
        )
    return value


def _optional_sha(value: object, field: str) -> str:
    if type(value) is not str:
        raise PromotionEligibilityError(
            _invalid_report("invalid_input", f"{field} must be a string")
        )
    if value == "":
        return ""
    if not SHA_RE.fullmatch(value):
        raise PromotionEligibilityError(
            _invalid_report("invalid_input", f"{field} must be a 40-char lowercase sha")
        )
    return value


def _changed_paths(pr_status: Mapping[str, Any]) -> list[str]:
    raw = pr_status.get("changed_paths")
    if type(raw) is not list:
        raise PromotionEligibilityError(
            _invalid_report("invalid_input", "changed_paths must be a list")
        )
    if not raw:
        raise PromotionEligibilityError(
            _invalid_report(
                "invalid_input",
                "changed_paths must be a non-empty list",
            )
        )
    paths: list[str] = []
    seen: set[str] = set()
    seen_aliases: set[str] = set()
    for index, value in enumerate(raw, 1):
        if (
            type(value) is not str
            or not value
            or value != value.strip()
            or "\\" in value
            or value.startswith("/")
            or value.endswith("/")
            or value == "."
            or "//" in value
            or "*" in value
            or ":" in value
            or any(ord(character) < 32 for character in value)
            or unicodedata.normalize("NFC", value) != value
            or PurePosixPath(value).as_posix() != value
            or any(part in {"", ".", ".."} for part in PurePosixPath(value).parts)
        ):
            raise PromotionEligibilityError(
                _invalid_report(
                    "invalid_input",
                    f"changed_paths item {index} must be a canonical repository path",
                )
            )
        if value in seen:
            raise PromotionEligibilityError(
                _invalid_report(
                    "invalid_input",
                    "changed_paths must contain distinct paths",
                )
            )
        alias = unicodedata.normalize("NFC", value).casefold()
        if alias in seen_aliases:
            raise PromotionEligibilityError(
                _invalid_report(
                    "invalid_input",
                    "changed_paths contains case or Unicode aliases",
                )
            )
        seen.add(value)
        seen_aliases.add(alias)
        paths.append(value)
    return paths


def _diff_text(pr_status: Mapping[str, Any]) -> str:
    raw = pr_status.get("diff_text")
    if type(raw) is not str or not raw:
        raise PromotionEligibilityError(
            _invalid_report(
                "invalid_input",
                "diff_text must be a non-empty string",
            )
        )
    return raw


def _snapshot_state_fields(
    pr_status: Mapping[str, Any],
) -> tuple[str, str, bool]:
    state = pr_status.get("state")
    if (
        type(state) is not str
        or not state
        or state != state.strip()
    ):
        raise PromotionEligibilityError(
            _invalid_report(
                "invalid_input",
                "state must be a non-empty exact string",
            )
        )
    mergeable = pr_status.get("mergeable")
    if (
        type(mergeable) is not str
        or not mergeable
        or mergeable != mergeable.strip()
    ):
        raise PromotionEligibilityError(
            _invalid_report(
                "invalid_input",
                "mergeable must be a non-empty exact string",
            )
        )
    is_draft = pr_status.get("is_draft")
    if type(is_draft) is not bool:
        raise PromotionEligibilityError(
            _invalid_report("invalid_input", "is_draft must be a boolean")
        )
    return state, mergeable, is_draft


def _checks(pr_status: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    checks = pr_status.get("checks")
    if checks is None:
        checks = pr_status.get("statusCheckRollup")
    if type(checks) is not list:
        raise PromotionEligibilityError(
            _invalid_report("invalid_input", "checks must be a list")
        )
    if not checks:
        raise PromotionEligibilityError(
            _invalid_report("invalid_input", "checks must be a non-empty list")
        )
    normalized: list[Mapping[str, Any]] = []
    seen_names: set[str] = set()
    for index, check in enumerate(checks, 1):
        if type(check) is not dict:
            raise PromotionEligibilityError(
                _invalid_report("invalid_input", "checks entries must be objects")
            )
        name = check.get("name")
        if (
            type(name) is not str
            or not name
            or name != name.strip()
        ):
            raise PromotionEligibilityError(
                _invalid_report(
                    "invalid_input",
                    f"checks item {index} name must be a non-empty exact string",
                )
            )
        if name in seen_names:
            raise PromotionEligibilityError(
                _invalid_report("invalid_input", "check names must be distinct")
            )
        seen_names.add(name)
        fields: dict[str, str] = {}
        for field in ("state", "status", "conclusion"):
            value = check.get(field, "")
            if (
                type(value) is not str
                or value != value.strip()
            ):
                raise PromotionEligibilityError(
                    _invalid_report(
                        "invalid_input",
                        f"checks item {index} {field} must be an exact string",
                    )
                )
            fields[field] = value
        if not any(fields.values()):
            raise PromotionEligibilityError(
                _invalid_report(
                    "invalid_input",
                    f"checks item {index} has no status value",
                )
            )
        normalized.append({"name": name, **fields})
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


def _is_allowed_private_marker_helper_reference(
    value: str, start: int, marker: str
) -> bool:
    end = start + len(marker)
    while start > 0 and _is_private_marker_identifier_char(value[start - 1]):
        start -= 1
    while end < len(value) and _is_private_marker_identifier_char(value[end]):
        end += 1
    if value[start:end] != f"{marker}S":
        return False
    line_start = value.rfind("\n", 0, start) + 1
    line_end = value.find("\n", end)
    if line_end == -1:
        line_end = len(value)
    line = value[line_start:line_end]
    if line.startswith(("+++", "---")) or not line.startswith(("+", "-")):
        return False
    before = value[start - 1] if start > line_start else ""
    after = value[end] if end < line_end else ""
    return before not in {"'", '"'} and after not in {"'", '"'}


def _is_private_marker_identifier_char(character: str) -> bool:
    return (
        character == "_"
        or "0" <= character <= "9"
        or "A" <= character <= "Z"
        or "a" <= character <= "z"
    )


def _find_private_marker(value: object) -> str | None:
    if isinstance(value, str):
        for marker in PRIVATE_MARKERS:
            if marker == PRIVATE_MARKERS[0]:
                for match in re.finditer(re.escape(marker), value):
                    if _is_allowed_private_marker_helper_reference(
                        value, match.start(), marker
                    ):
                        continue
                    return marker
                continue
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
