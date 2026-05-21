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
from waggledance.core.idle_consensus_charter import (  # noqa: E402
    DEFAULT_CHARTER_PATH,
    load_charter,
)


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
PRIVATE_MARKERS = ("PRIVATE_MARKER", "_DO_NOT_LEAK")
PASS_STATES = {"pass", "passed", "success", "successful", "ok"}
MERGEABLE_STATES = {"clean", "mergeable", "MERGEABLE", "CLEAN"}

Runner = Callable[[Sequence[str]], Any]
ArtifactWriter = Callable[[], Mapping[str, Any]]


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
    receipt_bundle_path: str = "",
    events_path: Path | None = None,
    charter_path: Path = DEFAULT_CHARTER_PATH,
    utc_date: str | None = None,
    repo: str = "",
    from_agent: str = "",
    bridge_task_id: str = "",
    apply: bool = False,
    runner: Runner | None = None,
    artifact_writer: ArtifactWriter | None = None,
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
    bridge_gate_task_id = bridge_task_id.strip() or consensus_proposal_id
    bridge_peer_gate = _bridge_peer_gate(
        events=events,
        task_id=bridge_gate_task_id,
        from_agent=from_agent,
        checked=events_path is not None,
    )
    charter = load_charter(charter_path)
    rate_date = utc_date or _today_utc()
    quota_used = _count_daily_auto_merges(events, rate_date)
    quota_total = int(charter.daily_quota)
    rate_gate = {
        "allowed": quota_used < quota_total,
        "utc_date": rate_date,
        "quota_used": quota_used,
        "quota_total": quota_total,
    }

    pr_number = _require_int(pr_status.get("pr_number"), "pr_number")
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

    run = runner or _run_command
    result = run(command)
    return_code = int(getattr(result, "returncode", 0))
    if return_code != 0:
        raise AutoMergeGateError(
            {
                **base,
                "decision": "auto_merge_failed",
                "ok": False,
                "operator_review_required": True,
                "errors": [f"gh pr merge failed with exit code {return_code}"],
                "exit_code": 1,
            }
        )

    report.update(
        {
            "decision": "auto_merged",
            "dry_run": False,
            "external_effect": True,
            "auto_merge_event_payload": {
                "auto_merged": True,
                "pr_number": pr_number,
                "pr_title": title,
                "consensus_proposal_id": consensus_proposal_id,
                "merge_commit_sha": str(getattr(result, "stdout", "")).strip(),
                "receipt_bundle_path": receipt_bundle_path,
                "rate_gate": rate_gate,
            },
        }
    )
    return report


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
        "bridge_peer_gate": dict(bridge_peer_gate),
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


def _bridge_peer_gate(
    *,
    events: Sequence[Mapping[str, Any]],
    task_id: str,
    from_agent: str,
    checked: bool,
) -> dict[str, Any]:
    if not checked:
        return {
            "ok": True,
            "clear_to_merge": True,
            "decision": "not_checked",
            "task_id": task_id,
            "merging_agent": from_agent,
            "latest_blocking_event": None,
            "latest_approval_event": None,
        }
    return check_bridge_clear_to_merge(
        events=events,
        task_id=task_id,
        merging_agent=from_agent,
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
