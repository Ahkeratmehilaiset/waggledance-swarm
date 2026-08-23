# SPDX-License-Identifier: BUSL-1.1
"""Write PR review wake events with a GitHub-authoritative head SHA.

This helper avoids hand-copying PR heads into bridge wake payloads. It resolves
``headRefOid`` through ``gh pr view``, validates the exact 40-hex SHA, and only
then builds or emits the wake event.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SAFE_STATUS_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")
GH_JSON_FIELDS = "number,headRefName,headRefOid,url"
BRIDGE_RUNTIME_ROOT_ENV = "AGENT_BRIDGE_RUNTIME_ROOT"
BRIDGE_WRITER_RELATIVE_PATH = Path("bin") / "Write-AgentEvent.ps1"
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
    writer = _resolve_bridge_writer(bridge_root)
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


def _resolve_bridge_writer(bridge_root: Path | None) -> Path:
    runtime_root_is_set = BRIDGE_RUNTIME_ROOT_ENV in os.environ
    if runtime_root_is_set:
        runtime_value = os.environ[BRIDGE_RUNTIME_ROOT_ENV]
        if not runtime_value.strip():
            raise _invalid(
                "invalid_bridge_root",
                f"{BRIDGE_RUNTIME_ROOT_ENV} must be a non-empty absolute path",
            )
        root = Path(runtime_value)
        runtime_key = _lexical_path_key(root, source=BRIDGE_RUNTIME_ROOT_ENV)
        if bridge_root is not None:
            explicit_root = Path(bridge_root)
            explicit_key = _lexical_path_key(explicit_root, source="bridge_root")
            if explicit_key != runtime_key:
                raise _invalid(
                    "ambiguous_bridge_root",
                    "bridge_root conflicts with AGENT_BRIDGE_RUNTIME_ROOT",
                )
    elif bridge_root is not None:
        root = Path(bridge_root)
        _lexical_path_key(root, source="bridge_root")
    else:
        root = ROOT / ".agent-bridge"
        _lexical_path_key(root, source="source bridge root")

    return _validate_bridge_writer(root)


def _lexical_path_key(path: Path, *, source: str) -> str:
    if not path.is_absolute() or ".." in path.parts:
        raise _invalid(
            "invalid_bridge_root",
            f"{source} must be an absolute path without parent traversal",
        )
    return os.path.normcase(os.path.normpath(str(path)))


def _validate_bridge_writer(root: Path) -> Path:
    try:
        root_stat = root.lstat()
    except FileNotFoundError as exc:
        raise _invalid("missing_writer", f"bridge root does not exist: {root}") from exc
    except OSError as exc:
        raise _invalid("invalid_bridge_root", f"cannot inspect bridge root: {exc}") from exc
    if not stat.S_ISDIR(root_stat.st_mode) or _is_reparse_point(root_stat):
        raise _invalid(
            "unsafe_writer_path",
            f"bridge root must be an ordinary directory: {root}",
        )

    writer = root / BRIDGE_WRITER_RELATIVE_PATH
    writer_bin = writer.parent
    try:
        bin_stat = writer_bin.lstat()
        writer_stat = writer.lstat()
    except FileNotFoundError as exc:
        raise _invalid(
            "missing_writer",
            f"Write-AgentEvent.ps1 not found at {writer}",
        ) from exc
    except OSError as exc:
        raise _invalid("unsafe_writer_path", f"cannot inspect bridge writer: {exc}") from exc

    if not stat.S_ISDIR(bin_stat.st_mode) or _is_reparse_point(bin_stat):
        raise _invalid(
            "unsafe_writer_path",
            f"bridge writer bin must be an ordinary directory: {writer_bin}",
        )
    if not stat.S_ISREG(writer_stat.st_mode) or _is_reparse_point(writer_stat):
        raise _invalid(
            "unsafe_writer_path",
            f"bridge writer must be an ordinary file: {writer}",
        )

    try:
        resolved_root = root.resolve(strict=True)
        resolved_bin = writer_bin.resolve(strict=True)
        resolved_writer = writer.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise _invalid("unsafe_writer_path", f"cannot resolve bridge writer: {exc}") from exc

    expected_bin = resolved_root / "bin"
    expected_writer = expected_bin / "Write-AgentEvent.ps1"
    if (
        _path_key(resolved_root) != _path_key(root)
        or _path_key(resolved_bin) != _path_key(expected_bin)
        or _path_key(resolved_writer) != _path_key(expected_writer)
        or not resolved_writer.is_relative_to(resolved_root)
    ):
        raise _invalid(
            "unsafe_writer_path",
            f"bridge writer escapes its exact root boundary: {writer}",
        )
    return resolved_writer


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _is_reparse_point(path_stat: os.stat_result) -> bool:
    if stat.S_ISLNK(path_stat.st_mode):
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    file_attributes = getattr(path_stat, "st_file_attributes", 0)
    return bool(reparse_flag and file_attributes & reparse_flag)


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
