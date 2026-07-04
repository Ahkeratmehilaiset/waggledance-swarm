# SPDX-License-Identifier: BUSL-1.1
"""Write PR review wake events with a GitHub-authoritative head SHA.

This helper avoids hand-copying PR heads into bridge wake payloads. It resolves
``headRefOid`` through ``gh pr view``, validates the exact 40-hex SHA, and only
then builds or emits the wake event.
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

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SAFE_STATUS_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")
GH_JSON_FIELDS = "number,headRefName,headRefOid,url"
Runner = Callable[[Sequence[str]], Any]


class PrBridgeWakeError(ValueError):
    """Raised when a head-safe bridge wake cannot be built."""

    def __init__(self, report: dict[str, Any]) -> None:
        super().__init__("; ".join(str(error) for error in report.get("errors", [])))
        self.report = report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build or emit a bridge wake_request for PR review.",
    )
    parser.add_argument("pr_number", type=int)
    parser.add_argument("--repo", default="")
    parser.add_argument("--agent", default="codex-lead-1")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--to", required=True)
    parser.add_argument("--status", default="")
    parser.add_argument("--body", default="")
    parser.add_argument("--declared-head", default="")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--bridge-root", type=Path, default=None)
    parser.add_argument("--emit", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        event = build_pr_review_wake_event(
            pr_number=args.pr_number,
            repo=args.repo,
            agent=args.agent,
            task_id=args.task_id,
            to=args.to,
            status=args.status,
            body=args.body,
            declared_head=args.declared_head,
        )
        report: dict[str, Any] = {
            "decision": "built",
            "ok": True,
            "event": event,
        }
        if args.emit:
            writer_report = emit_bridge_event(
                event,
                bridge_root=args.bridge_root,
                run_id=args.run_id,
            )
            report = {
                "decision": "emitted",
                "ok": True,
                "event": event,
                "writer": writer_report,
            }
        exit_code = 0
    except PrBridgeWakeError as exc:
        report = exc.report
        exit_code = int(report.get("exit_code", 2))

    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(report["decision"])
        if not report.get("ok", False):
            for error in report.get("errors", []):
                print(f"- {error}", file=sys.stderr)
    return exit_code


def build_pr_review_wake_event(
    *,
    pr_number: int,
    repo: str = "",
    agent: str,
    task_id: str,
    to: str,
    status: str = "",
    body: str = "",
    declared_head: str = "",
    runner: Runner | None = None,
) -> dict[str, Any]:
    snapshot = resolve_pr_head(pr_number=pr_number, repo=repo, runner=runner)
    head = str(snapshot["head"])
    declared = declared_head.strip().lower()
    if declared:
        if not SHA_RE.fullmatch(declared):
            raise _invalid(
                "invalid_declared_head",
                "declared_head must be a lowercase 40-hex SHA",
            )
        if declared != head:
            raise _invalid(
                "declared_head_mismatch",
                "declared_head does not match GitHub headRefOid",
            )
    status_text = status.strip() or f"review_pr{pr_number}_head_{head[:12]}"
    if not SAFE_STATUS_RE.fullmatch(status_text):
        raise _invalid("invalid_status", "status must be a safe bridge token")
    body_text = body.strip()
    suffix = f" {body_text}" if body_text else ""
    message = (
        f"Wake: review PR #{pr_number} head {head} "
        f"({snapshot['head_ref']}) resolved via gh pr view headRefOid.{suffix}"
    )
    return {
        "agent": agent,
        "type": "wake_request",
        "task_id": task_id,
        "status": status_text,
        "to": to,
        "message": message,
        "payload": {
            "schema": "wd.pr_bridge_wake.v1",
            "pr": pr_number,
            "head": head,
            "head_ref": snapshot["head_ref"],
            "head_source": "gh_pr_view.headRefOid",
            "url": snapshot["url"],
            "declared_head_checked": bool(declared),
        },
    }


def resolve_pr_head(
    *,
    pr_number: int,
    repo: str = "",
    runner: Runner | None = None,
) -> dict[str, Any]:
    if pr_number < 1:
        raise _invalid("invalid_pr_number", "pr_number must be positive")
    if repo and not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise _invalid("invalid_repo", "repo must be OWNER/NAME")
    command = ["gh", "pr", "view", str(pr_number), "--json", GH_JSON_FIELDS]
    if repo:
        command.extend(["--repo", repo])
    run = runner or _run_command
    result = run(command)
    return_code = int(getattr(result, "returncode", 0))
    if return_code != 0:
        raise PrBridgeWakeError(
            {
                "decision": "gh_pr_view_failed",
                "ok": False,
                "errors": [f"gh pr view failed with exit code {return_code}"],
                "exit_code": 1,
            }
        )
    try:
        raw = json.loads(str(getattr(result, "stdout", "")))
    except json.JSONDecodeError as exc:
        raise _invalid("invalid_gh_json", exc.msg) from exc
    if not isinstance(raw, Mapping):
        raise _invalid("invalid_gh_json", "gh pr view JSON must be an object")
    number = raw.get("number")
    if number != pr_number:
        raise _invalid("pr_number_mismatch", "gh response PR number mismatch")
    head = str(raw.get("headRefOid", "")).strip().lower()
    if not SHA_RE.fullmatch(head):
        raise _invalid("invalid_head_sha", "headRefOid must be a lowercase 40-hex SHA")
    head_ref = str(raw.get("headRefName", "")).strip()
    if not head_ref:
        raise _invalid("invalid_head_ref", "headRefName is required")
    return {
        "pr": pr_number,
        "head": head,
        "head_ref": head_ref,
        "url": str(raw.get("url", "")),
    }


def emit_bridge_event(
    event: Mapping[str, Any],
    *,
    bridge_root: Path | None = None,
    run_id: str = "",
    runner: Runner | None = None,
) -> dict[str, Any]:
    root = Path(bridge_root) if bridge_root is not None else ROOT / ".agent-bridge"
    writer = root / "bin" / "Write-AgentEvent.ps1"
    if not writer.exists():
        raise _invalid("missing_writer", f"Write-AgentEvent.ps1 not found at {writer}")
    payload = json.dumps(event["payload"], sort_keys=True, separators=(",", ":"))
    command = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(writer),
        "-Agent",
        str(event["agent"]),
        "-Type",
        str(event["type"]),
        "-TaskId",
        str(event["task_id"]),
        "-Status",
        str(event["status"]),
        "-To",
        str(event["to"]),
        "-Message",
        str(event["message"]),
        "-PayloadJson",
        payload,
    ]
    if run_id:
        command.extend(["-RunId", run_id])
    run = runner or _run_command
    result = run(command)
    return_code = int(getattr(result, "returncode", 0))
    if return_code != 0:
        raise PrBridgeWakeError(
            {
                "decision": "bridge_write_failed",
                "ok": False,
                "errors": [f"Write-AgentEvent.ps1 failed with exit code {return_code}"],
                "exit_code": 1,
            }
        )
    return {"returncode": return_code}


def _run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, capture_output=True, text=True)


def _invalid(decision: str, message: str) -> PrBridgeWakeError:
    return PrBridgeWakeError(
        {
            "decision": decision,
            "ok": False,
            "errors": [message],
            "exit_code": 2,
        }
    )


if __name__ == "__main__":
    raise SystemExit(main())
