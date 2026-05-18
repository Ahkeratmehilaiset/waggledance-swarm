# SPDX-License-Identifier: BUSL-1.1
"""Dry-run-first DRAFT PR composer for idle consensus follow-up.

This slice composes the output of ``tools/idle_consensus_to_pr.py`` into a
draft pull-request creation plan. By default it performs no external effect.
The only effectful mode is explicit ``--apply``, which can create a DRAFT PR
from an already-existing branch; this tool never creates branches and never
merges pull requests.
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

from tools.idle_check import DEFAULT_EVENTS_PATH  # noqa: E402
from tools.idle_consensus_to_pr import (  # noqa: E402
    ELIGIBLE_DECISION,
    ConsensusToPrGateError,
    evaluate_consensus_to_pr_gate,
)
from waggledance.core.idle_consensus_charter import DEFAULT_CHARTER_PATH  # noqa: E402


PRIVATE_MARKERS = ("PRIVATE_MARKER", "_DO_NOT_LEAK")
SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,180}$")
SAFE_HEAD_RE = re.compile(
    r"^[a-z][a-z0-9_-]{1,32}/idle-consensus-[A-Za-z0-9][A-Za-z0-9._/-]{0,150}$"
)

Runner = Callable[[Sequence[str]], Any]


class DraftPrPlanError(ValueError):
    """Raised when a DRAFT PR plan cannot be evaluated safely."""

    def __init__(self, report: dict[str, Any]) -> None:
        super().__init__("; ".join(str(error) for error in report.get("errors", [])))
        self.report = report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compose an idle consensus DRAFT PR plan.",
    )
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS_PATH)
    parser.add_argument("--charter", type=Path, default=DEFAULT_CHARTER_PATH)
    parser.add_argument("--changed-path", action="append", default=[])
    parser.add_argument("--diff-file", type=Path, default=None)
    parser.add_argument("--utc-date", default=None)
    parser.add_argument("--title", default=None)
    parser.add_argument("--body-file", type=Path, default=None)
    parser.add_argument("--head", default="")
    parser.add_argument("--base", default="main")
    parser.add_argument("--repo", default="")
    parser.add_argument("--artifact-path", default="")
    parser.add_argument("--receipt-manifest", default="")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    diff_text = ""
    if args.diff_file is not None:
        diff_text = args.diff_file.read_text(encoding="utf-8")
    body_text = None
    if args.body_file is not None:
        body_text = args.body_file.read_text(encoding="utf-8")

    try:
        report = build_draft_pr_plan(
            events_path=args.events,
            changed_paths=args.changed_path,
            diff_text=diff_text,
            charter_path=args.charter,
            utc_date=args.utc_date,
            title=args.title,
            body=body_text,
            head=args.head,
            base=args.base,
            repo=args.repo,
            artifact_path=args.artifact_path,
            receipt_manifest=args.receipt_manifest,
            apply=args.apply,
        )
    except (ConsensusToPrGateError, DraftPrPlanError) as exc:
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


def build_draft_pr_plan(
    *,
    events_path: Path,
    changed_paths: Sequence[str],
    diff_text: str,
    charter_path: Path = DEFAULT_CHARTER_PATH,
    utc_date: str | None = None,
    title: str | None = None,
    body: str | None = None,
    head: str = "",
    base: str = "main",
    repo: str = "",
    artifact_path: str = "",
    receipt_manifest: str = "",
    apply: bool = False,
    runner: Runner | None = None,
) -> dict[str, Any]:
    """Build or explicitly apply a DRAFT PR creation plan."""
    _assert_no_private_markers(
        {
            "title": title,
            "body": body,
            "head": head,
            "base": base,
            "repo": repo,
            "artifact_path": artifact_path,
            "receipt_manifest": receipt_manifest,
        }
    )
    _validate_ref("base", base)
    if head:
        _validate_head_ref(head)
    if repo:
        _validate_repo(repo)

    gate_report = evaluate_consensus_to_pr_gate(
        events_path=events_path,
        changed_paths=changed_paths,
        diff_text=diff_text,
        charter_path=charter_path,
        utc_date=utc_date,
    )
    if gate_report.get("decision") != ELIGIBLE_DECISION:
        return _base_report(
            decision=str(gate_report.get("decision", "operator_review_required")),
            gate_report=gate_report,
            operator_review_required=True,
            reasons=["gate decision is not eligible for draft PR creation"],
        )

    draft_title = title or _default_title(gate_report)
    transcript = _read_idle_transcript(events_path)
    draft_body = body or _default_body(
        gate_report=gate_report,
        changed_paths=changed_paths,
        transcript=transcript,
        artifact_path=artifact_path,
        receipt_manifest=receipt_manifest,
    )
    _assert_no_private_markers({"draft_title": draft_title, "draft_body": draft_body})

    command = _gh_pr_create_command(
        title=draft_title,
        body=draft_body,
        head=head,
        base=base,
        repo=repo,
    )
    report = _base_report(
        decision="draft_pr_plan_ready",
        gate_report=gate_report,
        operator_review_required=True,
        reasons=["eligible gate result composed into draft PR plan"],
    )
    report.update(
        {
            "draft_pr": {
                "title": draft_title,
                "body": draft_body,
                "head": head,
                "base": base,
                "repo": repo,
                "artifact_path": artifact_path,
                "receipt_manifest": receipt_manifest,
                "consensus_artifact_path": artifact_path or None,
                "artifact_receipt_path": receipt_manifest or None,
                "gh_command": command,
            },
            "would_create_pr": True,
        }
    )
    if not apply:
        return report

    if not head:
        raise DraftPrPlanError(
            {
                **_base_report(
                    decision="missing_head_ref",
                    gate_report=gate_report,
                    operator_review_required=True,
                ),
                "errors": ["--apply requires --head for an existing branch"],
                "exit_code": 2,
            }
        )
    run = runner or _run_command
    result = run(command)
    return_code = int(getattr(result, "returncode", 0))
    if return_code != 0:
        raise DraftPrPlanError(
            {
                **_base_report(
                    decision="draft_pr_create_failed",
                    gate_report=gate_report,
                    operator_review_required=True,
                ),
                "errors": [f"gh pr create failed with exit code {return_code}"],
                "exit_code": 1,
            }
        )

    created_url = str(getattr(result, "stdout", "")).strip()
    report.update(
        {
            "decision": "draft_pr_created",
            "dry_run": False,
            "external_effect": True,
            "created_pr_url": created_url,
        }
    )
    return report


def _base_report(
    *,
    decision: str,
    gate_report: Mapping[str, Any],
    operator_review_required: bool = False,
    reasons: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "decision": decision,
        "dry_run": True,
        "external_effect": False,
        "would_create_pr": False,
        "would_merge": False,
        "auto_execute": False,
        "operator_review_required": operator_review_required,
        "gate_report": dict(gate_report),
        "reasons": list(reasons),
    }


def _gh_pr_create_command(
    *,
    title: str,
    body: str,
    head: str,
    base: str,
    repo: str,
) -> list[str]:
    command = ["gh", "pr", "create", "--draft", "--base", base]
    if head:
        command.extend(["--head", head])
    if repo:
        command.extend(["--repo", repo])
    command.extend(["--title", title, "--body", body])
    return command


def _default_title(gate_report: Mapping[str, Any]) -> str:
    convergence = gate_report.get("convergence")
    if isinstance(convergence, Mapping):
        target = convergence.get("target_proposal_id") or convergence.get(
            "finalist_proposal_ids"
        )
        if target:
            return f"Idle consensus follow-up: {target}"
    return "Idle consensus follow-up"


def _default_body(
    *,
    gate_report: Mapping[str, Any],
    changed_paths: Sequence[str],
    transcript: Sequence[Mapping[str, Any]],
    artifact_path: str,
    receipt_manifest: str,
) -> str:
    convergence = gate_report.get("convergence")
    convergence_status = ""
    if isinstance(convergence, Mapping):
        convergence_status = str(convergence.get("status", ""))
    lines = [
        "## Idle Consensus Gate",
        "",
        f"- Gate decision: {gate_report.get('decision')}",
        f"- Convergence: {convergence_status}",
        "- Auto execute: false",
        "- Merge: not requested",
        "",
        "## Changed Paths",
        "",
    ]
    lines.extend(f"- `{path}`" for path in changed_paths)
    lines.extend(
        [
            "",
            "## Evidence",
            "",
            f"- Artifact path: {artifact_path or 'required before merge'}",
            f"- Receipt manifest: {receipt_manifest or 'required before merge'}",
            "",
            "## Gate Report",
            "",
            "```json",
            json.dumps(gate_report, indent=2, sort_keys=True),
            "```",
            "",
            "## Transcript",
            "",
            "```json",
            json.dumps(list(transcript), indent=2, sort_keys=True),
            "```",
        ]
    )
    return "\n".join(lines)


def _run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
    )


def _validate_ref(label: str, value: str) -> None:
    if not value or not SAFE_REF_RE.fullmatch(value) or value.startswith("-"):
        raise DraftPrPlanError(
            {
                **_base_report(
                    decision="invalid_ref",
                    gate_report={},
                    operator_review_required=True,
                ),
                "errors": [f"{label} ref is invalid"],
                "exit_code": 2,
            }
        )


def _validate_head_ref(value: str) -> None:
    if not SAFE_HEAD_RE.fullmatch(value) or value.startswith("-"):
        raise DraftPrPlanError(
            {
                **_base_report(
                    decision="invalid_head_ref",
                    gate_report={},
                    operator_review_required=True,
                ),
                "errors": ["head ref must use <agent>/idle-consensus-* namespace"],
                "exit_code": 2,
            }
        )


def _validate_repo(value: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        raise DraftPrPlanError(
            {
                **_base_report(
                    decision="invalid_repo",
                    gate_report={},
                    operator_review_required=True,
                ),
                "errors": ["repo must be OWNER/NAME"],
                "exit_code": 2,
            }
        )


def _read_idle_transcript(events_path: Path) -> list[dict[str, Any]]:
    transcript: list[dict[str, Any]] = []
    for line in events_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        payload = event.get("payload") if isinstance(event, Mapping) else None
        if isinstance(payload, dict) and payload.get("protocol_version") == "idle-protocol.v1":
            transcript.append(payload)
    _assert_no_private_markers(transcript)
    return transcript


def _assert_no_private_markers(value: object) -> None:
    marker = _find_private_marker(value)
    if marker is not None:
        raise DraftPrPlanError(
            {
                **_base_report(
                    decision="privacy_marker_refused",
                    gate_report={},
                    operator_review_required=True,
                ),
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
