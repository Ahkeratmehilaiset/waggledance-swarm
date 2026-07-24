# SPDX-License-Identifier: BUSL-1.1
"""Structured GitHub PR status snapshot for idle auto-merge input.

This helper queries ``gh pr view --json ...`` and normalizes the result into
the ``pr_status`` shape consumed by ``tools/idle_consensus_auto_merge.py``.
It never parses human-readable ``gh`` output and never performs a merge.
Default mode prints the snapshot JSON to stdout; ``--out`` writes that same
snapshot to a local file.

When the caller knows the current base branch SHA, ``--expected-base-sha``
turns the snapshot into a stale-base preflight: the command refuses if
GitHub's ``baseRefOid`` is not the supplied current main SHA.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from typing import Any, Callable, Mapping, Sequence
import unicodedata
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.bridge_pr_author import (  # noqa: E402
    github_pr_git_identity_evidence,
)

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
PRIVATE_MARKERS = ("PRIVATE_MARKER", "_DO_NOT_LEAK")
GH_JSON_FIELDS = (
    "number,title,headRefOid,headRefName,baseRefOid,baseRefName,mergeable,"
    "statusCheckRollup,reviewDecision,isDraft,url,updatedAt,changedFiles,"
    "author,commits"
)
GH_FILES_PER_PAGE = 100
GH_MAX_PULL_FILES = 3000
GH_GRAPHQL_CONNECTION_PAGE_SIZE = 100
GH_FILE_STATUSES = {
    "added",
    "changed",
    "copied",
    "modified",
    "removed",
    "renamed",
    "unchanged",
}

Runner = Callable[[Sequence[str]], Any]


class PrStatusSnapshotError(ValueError):
    """Raised when a PR status snapshot cannot be produced safely."""

    def __init__(self, report: dict[str, Any]) -> None:
        super().__init__("; ".join(str(error) for error in report.get("errors", [])))
        self.report = report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Write a structured PR status snapshot from gh pr view JSON.",
    )
    parser.add_argument("pr_number", type=int)
    parser.add_argument("--repo", default="")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--operator-approved",
        action="store_true",
        help=(
            "Record operator_approved=true as snapshot metadata. "
            "Idle-charter auto-merge does not require this flag."
        ),
    )
    parser.add_argument(
        "--receipt-verified",
        action="store_true",
        help="Set receipt_verified=true in the snapshot.",
    )
    parser.add_argument(
        "--expected-base-sha",
        default="",
        help=(
            "Optional current base branch SHA. When set, fail closed if "
            "GitHub reports the PR baseRefOid at any other commit."
        ),
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        snapshot = build_pr_status_snapshot(
            pr_number=args.pr_number,
            repo=args.repo,
            operator_approved=args.operator_approved,
            receipt_verified=args.receipt_verified,
            expected_base_sha=args.expected_base_sha,
        )
        if args.out is not None:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(snapshot, sort_keys=True), encoding="utf-8")
            report = {
                "decision": "written",
                "pr_number": snapshot["pr_number"],
                "out": str(args.out),
            }
        else:
            report = snapshot
    except PrStatusSnapshotError as exc:
        report = exc.report
        exit_code = int(report.get("exit_code", 2))
    else:
        exit_code = 0

    if args.json or args.out is None:
        print(json.dumps(report, sort_keys=True))
    else:
        print(report["decision"])
        if "out" in report:
            print(report["out"])
        for error in report.get("errors", []):
            print(f"- {error}", file=sys.stderr)
    return exit_code


def build_pr_status_snapshot(
    *,
    pr_number: int,
    repo: str = "",
    operator_approved: bool = False,
    receipt_verified: bool = False,
    expected_base_sha: str = "",
    runner: Runner | None = None,
) -> dict[str, Any]:
    """Query GitHub through ``gh`` and normalize the PR status snapshot."""
    if type(pr_number) is not int or pr_number < 1:
        raise _invalid("invalid_pr_number", "pr_number must be positive")
    if type(repo) is not str:
        raise _invalid("invalid_repo", "repo must be OWNER/NAME")
    if repo and not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise _invalid("invalid_repo", "repo must be OWNER/NAME")
    if type(expected_base_sha) is not str:
        raise _invalid(
            "invalid_expected_base_sha",
            "expected_base_sha must be a 40-char lowercase sha",
        )
    if type(operator_approved) is not bool:
        raise _invalid(
            "invalid_operator_approved",
            "operator_approved must be a boolean",
        )
    if type(receipt_verified) is not bool:
        raise _invalid(
            "invalid_receipt_verified",
            "receipt_verified must be a boolean",
        )
    if runner is not None and not callable(runner):
        raise _invalid("invalid_runner", "runner must be callable or null")
    if expected_base_sha and not SHA_RE.fullmatch(expected_base_sha):
        raise _invalid(
            "invalid_expected_base_sha",
            "expected_base_sha must be a 40-char lowercase sha",
        )

    command = _gh_pr_view_command(pr_number=pr_number, repo=repo)
    run = runner if runner is not None else _run_command
    stdout = _run_gh_text(
        run,
        command,
        failure_decision="gh_pr_view_failed",
        label="gh pr view",
    )
    _assert_no_private_markers(stdout)
    raw = _parse_json(stdout, decision="invalid_gh_json")
    if not isinstance(raw, Mapping):
        raise _invalid("invalid_gh_json", "gh pr view JSON must be an object")
    _assert_no_private_markers(raw)
    initial_anchor = _normalize_anchor(raw, expected_pr_number=pr_number)
    initial_base_tip = _fetch_base_ref_tip(
        run=run,
        repo=repo,
        base_ref_name=initial_anchor["base_ref"],
    )
    if initial_base_tip != initial_anchor["base_sha"]:
        raise PrStatusSnapshotError(
            {
                "decision": "stale_base_ref",
                "ok": False,
                "errors": [
                    "PR baseRefOid does not match the live base branch tip"
                ],
                "exit_code": 1,
            }
        )

    file_records = _fetch_pr_file_records(
        run=run,
        repo=repo,
        pr_number=pr_number,
        expected_count=initial_anchor["changed_files_count"],
    )
    changed_paths = _normalize_changed_paths(file_records)

    diff_command = _gh_pr_diff_command(pr_number=pr_number, repo=repo)
    diff_text = _run_gh_text(
        run,
        diff_command,
        failure_decision="gh_pr_diff_failed",
        label="gh pr diff",
    )
    _assert_no_private_markers(diff_text)
    verified_base_tip = _fetch_base_ref_tip(
        run=run,
        repo=repo,
        base_ref_name=initial_anchor["base_ref"],
    )
    if verified_base_tip != initial_base_tip:
        raise PrStatusSnapshotError(
            {
                "decision": "gh_pr_diff_base_tip_drift",
                "ok": False,
                "errors": ["live base branch tip changed during snapshot capture"],
                "exit_code": 1,
            }
        )

    # Bracket every paginated file/diff read with the same complete PR anchor.
    verify_stdout = _run_gh_text(
        run,
        command,
        failure_decision="gh_pr_view_recheck_failed",
        label="gh pr view recheck",
    )
    _assert_no_private_markers(verify_stdout)
    verify_raw = _parse_json(
        verify_stdout,
        decision="invalid_gh_recheck_json",
    )
    if not isinstance(verify_raw, Mapping):
        raise _invalid("invalid_gh_recheck_json", "gh pr view JSON must be an object")
    _assert_raw_reference_stable(initial=raw, verified=verify_raw)
    verify_anchor = _normalize_anchor(verify_raw, expected_pr_number=pr_number)
    _assert_anchor_stable(initial=initial_anchor, verified=verify_anchor)
    initial_anchor["base_tip_sha"] = initial_base_tip
    return _normalize_snapshot(
        raw,
        anchor=initial_anchor,
        expected_pr_number=pr_number,
        operator_approved=operator_approved,
        receipt_verified=receipt_verified,
        expected_base_sha=expected_base_sha,
        changed_paths=changed_paths,
        diff_text=diff_text,
    )


def _gh_pr_view_command(*, pr_number: int, repo: str) -> list[str]:
    command = ["gh", "pr", "view", str(pr_number), "--json", f"{GH_JSON_FIELDS},state"]
    if repo:
        command.extend(["--repo", repo])
    return command


def _gh_pr_diff_command(*, pr_number: int, repo: str) -> list[str]:
    command = ["gh", "pr", "diff", str(pr_number), "--patch"]
    if repo:
        command.extend(["--repo", repo])
    return command


def _gh_pr_files_command(
    *,
    pr_number: int,
    repo: str,
    page: int,
) -> list[str]:
    repo_token = repo or "{owner}/{repo}"
    return [
        "gh",
        "api",
        "--method",
        "GET",
        f"repos/{repo_token}/pulls/{pr_number}/files",
        "-f",
        f"per_page={GH_FILES_PER_PAGE}",
        "-f",
        f"page={page}",
    ]


def _gh_base_ref_command(*, repo: str, base_ref_name: str) -> list[str]:
    repo_token = repo or "{owner}/{repo}"
    encoded_ref = quote(base_ref_name, safe="")
    return [
        "gh",
        "api",
        "--method",
        "GET",
        f"repos/{repo_token}/git/ref/heads/{encoded_ref}",
    ]


def _fetch_base_ref_tip(
    *,
    run: Runner,
    repo: str,
    base_ref_name: str,
) -> str:
    stdout = _run_gh_text(
        run,
        _gh_base_ref_command(repo=repo, base_ref_name=base_ref_name),
        failure_decision="gh_base_ref_failed",
        label="gh api base ref",
    )
    _assert_no_private_markers(stdout)
    payload = _parse_json(stdout, decision="invalid_gh_base_ref_json")
    if not isinstance(payload, Mapping):
        raise _invalid(
            "invalid_gh_base_ref_json",
            "base ref JSON must be an object",
        )
    ref = payload.get("ref")
    expected_ref = f"refs/heads/{base_ref_name}"
    if ref != expected_ref:
        raise _invalid(
            "invalid_gh_base_ref_json",
            "base ref response did not match the requested branch",
        )
    target = payload.get("object")
    if not isinstance(target, Mapping) or target.get("type") != "commit":
        raise _invalid(
            "invalid_gh_base_ref_json",
            "base ref target must be a commit object",
        )
    return _require_sha(target.get("sha"), "base ref sha")


def _run_gh_text(
    run: Runner,
    command: Sequence[str],
    *,
    failure_decision: str,
    label: str,
) -> str:
    result = run(command)
    return_code = getattr(result, "returncode", None)
    if type(return_code) is not int:
        raise _invalid(failure_decision, f"{label} returned an invalid exit code")
    stdout = _strict_utf8_stream(
        getattr(result, "stdout", ""),
        label=label,
        stream_name="stdout",
    )
    _strict_utf8_stream(
        getattr(result, "stderr", ""),
        label=label,
        stream_name="stderr",
    )
    if return_code != 0:
        raise PrStatusSnapshotError(
            {
                "decision": failure_decision,
                "ok": False,
                "errors": [f"{label} failed with exit code {return_code}"],
                "exit_code": 1,
            }
        )
    return stdout


def _strict_utf8_stream(value: object, *, label: str, stream_name: str) -> str:
    if type(value) is str:
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise _invalid(
                "invalid_utf8",
                f"{label} emitted invalid UTF-8 on {stream_name}",
            ) from exc
        return value
    if type(value) is bytes:
        try:
            return value.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise _invalid(
                "invalid_utf8",
                f"{label} emitted invalid UTF-8 on {stream_name}",
            ) from exc
    raise _invalid(
        "invalid_subprocess_result",
        f"{label} {stream_name} must be bytes or text",
    )


def _parse_json(text: str, *, decision: str) -> object:
    def reject_constant(value: str) -> object:
        raise ValueError(f"non-standard JSON constant: {value}")

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate JSON object key: {key}")
            value[key] = item
        return value

    try:
        return json.loads(
            text,
            parse_constant=reject_constant,
            object_pairs_hook=unique_object,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        message = exc.msg if isinstance(exc, json.JSONDecodeError) else str(exc)
        raise _invalid(decision, message) from exc


def _normalize_anchor(
    raw: Mapping[str, Any],
    *,
    expected_pr_number: int,
) -> dict[str, Any]:
    number = _require_int(raw.get("number"), "number")
    if number != expected_pr_number:
        raise _invalid("pr_number_mismatch", "gh response PR number did not match request")
    head_ref = _require_exact_string(raw.get("headRefName"), "headRefName")
    head_sha = _require_sha(raw.get("headRefOid"), "headRefOid")
    base_ref = _require_exact_string(raw.get("baseRefName"), "baseRefName")
    base_sha = _require_sha(raw.get("baseRefOid"), "baseRefOid")
    updated_at = _require_exact_string(raw.get("updatedAt"), "updatedAt")
    state = _require_exact_string(raw.get("state"), "state")
    if state != "OPEN":
        raise _invalid("invalid_pr_state", "PR state must be OPEN")
    is_draft = raw.get("isDraft")
    if type(is_draft) is not bool:
        raise _invalid("invalid_gh_json", "isDraft must be a boolean")
    mergeable = _require_exact_string(raw.get("mergeable"), "mergeable")
    checks = _normalize_checks(raw.get("statusCheckRollup"))
    changed_files_count = _require_int(raw.get("changedFiles"), "changedFiles")
    if changed_files_count < 1:
        raise _invalid("invalid_files", "changedFiles must be a positive integer")
    if changed_files_count > GH_MAX_PULL_FILES:
        raise _invalid(
            "pull_files_limit_exceeded",
            f"changedFiles exceeds the REST pull-files limit of {GH_MAX_PULL_FILES}",
        )
    _assert_identity_connections_complete(raw)
    try:
        material = github_pr_git_identity_evidence(
            raw,
            expected_head_sha=head_sha,
        )
    except ValueError as exc:
        raise _invalid("invalid_git_identities", str(exc)) from exc
    git_identities = list(material.pop("identities"))
    return {
        "number": number,
        "head_ref": head_ref,
        "head_sha": head_sha,
        "base_ref": base_ref,
        "base_sha": base_sha,
        "updated_at": updated_at,
        "state": state,
        "is_draft": is_draft,
        "mergeable": mergeable,
        "checks": checks,
        "changed_files_count": changed_files_count,
        "git_identities": git_identities,
        "git_identity_evidence": material,
    }


def _assert_identity_connections_complete(raw: Mapping[str, Any]) -> None:
    commits = raw.get("commits")
    if not isinstance(commits, list):
        return
    if len(commits) >= GH_GRAPHQL_CONNECTION_PAGE_SIZE:
        raise _invalid(
            "incomplete_git_identities",
            "PR commit identity metadata may be truncated",
        )
    for index, commit in enumerate(commits, 1):
        if not isinstance(commit, Mapping):
            continue
        authors = commit.get("authors")
        if (
            isinstance(authors, list)
            and len(authors) >= GH_GRAPHQL_CONNECTION_PAGE_SIZE
        ):
            raise _invalid(
                "incomplete_git_identities",
                f"PR commit {index} author metadata may be truncated",
            )


def _assert_anchor_stable(
    *,
    initial: Mapping[str, Any],
    verified: Mapping[str, Any],
) -> None:
    fields = (
        ("number", "gh_pr_diff_number_drift"),
        ("head_ref", "gh_pr_diff_head_ref_drift"),
        ("head_sha", "gh_pr_diff_head_drift"),
        ("base_ref", "gh_pr_diff_base_ref_drift"),
        ("base_sha", "gh_pr_diff_base_drift"),
        ("updated_at", "gh_pr_diff_updated_at_drift"),
        ("state", "gh_pr_diff_state_drift"),
        ("is_draft", "gh_pr_diff_draft_drift"),
        ("mergeable", "gh_pr_diff_mergeable_drift"),
        ("checks", "gh_pr_diff_checks_drift"),
        ("changed_files_count", "gh_pr_diff_file_count_drift"),
    )
    for field, decision in fields:
        if verified.get(field) != initial.get(field):
            raise PrStatusSnapshotError(
                {
                    "decision": decision,
                    "ok": False,
                    "errors": [f"PR {field} changed during snapshot capture"],
                    "exit_code": 1,
                }
            )
    for field in ("git_identities", "git_identity_evidence"):
        if verified.get(field) != initial.get(field):
            raise PrStatusSnapshotError(
                {
                    "decision": "gh_pr_identity_drift",
                    "ok": False,
                    "errors": [
                        "PR Git identity evidence changed during snapshot capture"
                    ],
                    "exit_code": 1,
                }
            )


def _assert_raw_reference_stable(
    *,
    initial: Mapping[str, Any],
    verified: Mapping[str, Any],
) -> None:
    fields = (
        ("number", "gh_pr_diff_number_drift"),
        ("headRefName", "gh_pr_diff_head_ref_drift"),
        ("headRefOid", "gh_pr_diff_head_drift"),
        ("baseRefName", "gh_pr_diff_base_ref_drift"),
        ("baseRefOid", "gh_pr_diff_base_drift"),
    )
    for field, decision in fields:
        if verified.get(field) != initial.get(field):
            raise PrStatusSnapshotError(
                {
                    "decision": decision,
                    "ok": False,
                    "errors": [f"PR {field} changed during snapshot capture"],
                    "exit_code": 1,
                }
            )


def _fetch_pr_file_records(
    *,
    run: Runner,
    repo: str,
    pr_number: int,
    expected_count: int,
) -> list[Mapping[str, Any]]:
    records: list[Mapping[str, Any]] = []
    page_count = (
        expected_count + GH_FILES_PER_PAGE - 1
    ) // GH_FILES_PER_PAGE
    for page in range(1, page_count + 1):
        command = _gh_pr_files_command(
            pr_number=pr_number,
            repo=repo,
            page=page,
        )
        stdout = _run_gh_text(
            run,
            command,
            failure_decision="gh_pr_files_failed",
            label=f"gh api pull files page {page}",
        )
        _assert_no_private_markers(stdout)
        payload = _parse_json(stdout, decision="invalid_gh_files_json")
        if not isinstance(payload, list):
            raise _invalid(
                "invalid_gh_files_json",
                f"pull files page {page} JSON must be a list",
            )
        if len(payload) > GH_FILES_PER_PAGE:
            raise _invalid(
                "invalid_gh_files_count",
                f"pull files page {page} exceeds per_page",
            )
        expected_page_count = min(
            GH_FILES_PER_PAGE,
            expected_count - len(records),
        )
        if len(payload) != expected_page_count:
            raise _invalid(
                "invalid_gh_files_count",
                f"pull files page {page} record count {len(payload)} did not "
                f"match expected {expected_page_count}",
            )
        for index, item in enumerate(payload, 1):
            if not isinstance(item, Mapping):
                raise _invalid(
                    "invalid_files",
                    f"pull files page {page} item {index} must be an object",
                )
            records.append(item)
    if len(records) != expected_count:
        raise _invalid(
            "invalid_gh_files_count",
            f"pull files record count {len(records)} did not match changedFiles "
            f"{expected_count}",
        )
    return records


def _normalize_snapshot(
    raw: Mapping[str, Any],
    *,
    anchor: Mapping[str, Any],
    expected_pr_number: int,
    operator_approved: bool,
    receipt_verified: bool,
    expected_base_sha: str,
    changed_paths: Sequence[str],
    diff_text: str,
) -> dict[str, Any]:
    number = _require_int(anchor.get("number"), "number")
    if number != expected_pr_number:
        raise _invalid("pr_number_mismatch", "gh response PR number did not match request")
    head_sha = _require_sha(anchor.get("head_sha"), "headRefOid")
    base_sha = _require_sha(anchor.get("base_sha"), "baseRefOid")
    if expected_base_sha and base_sha != expected_base_sha:
        raise PrStatusSnapshotError(
            {
                "decision": "stale_base_ref",
                "ok": False,
                "errors": [
                    f"PR baseRefOid {base_sha} does not match expected base "
                    f"{expected_base_sha}",
                ],
                "exit_code": 1,
            }
        )

    checks = list(anchor["checks"])
    git_identities = list(anchor["git_identities"])
    git_identity_evidence = dict(anchor["git_identity_evidence"])
    snapshot = {
        "pr_number": number,
        "title": str(raw.get("title", "")),
        "head_sha": head_sha,
        "head_ref": _require_exact_string(anchor.get("head_ref"), "headRefName"),
        "base_ref": _require_exact_string(anchor.get("base_ref"), "baseRefName"),
        "base_sha": base_sha,
        "base_tip_sha": _require_sha(anchor.get("base_tip_sha"), "base tip sha"),
        "mergeable": anchor["mergeable"],
        "state": anchor["state"],
        "is_draft": anchor["is_draft"],
        "updated_at": anchor["updated_at"],
        "url": str(raw.get("url", "")),
        "review_decision": str(raw.get("reviewDecision", "")),
        "operator_approved": bool(operator_approved),
        "receipt_verified": bool(receipt_verified),
        "checks": checks,
        "statusCheckRollup": checks,
        "changed_paths": changed_paths,
        "diff_text": diff_text,
        "git_identities": git_identities,
        "git_identity_evidence": git_identity_evidence,
    }
    _assert_no_private_markers(snapshot)
    return snapshot


def _normalize_checks(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise _invalid("invalid_status_check_rollup", "statusCheckRollup must be a list")
    checks: list[dict[str, str]] = []
    for index, item in enumerate(value, 1):
        if not isinstance(item, Mapping):
            raise _invalid(
                "invalid_status_check_rollup",
                f"statusCheckRollup item {index} must be an object",
            )
        name_value = item.get("name")
        if name_value is None:
            name_value = item.get("context")
        if type(name_value) is not str or not name_value:
            raise _invalid(
                "invalid_status_check_rollup",
                f"statusCheckRollup item {index} name/context is required",
            )
        normalized_fields: dict[str, str] = {}
        for field in ("state", "status", "conclusion"):
            raw = item.get(field, "")
            if type(raw) is not str:
                raise _invalid(
                    "invalid_status_check_rollup",
                    f"statusCheckRollup item {index} {field} must be a string",
                )
            normalized_fields[field] = raw
        checks.append(
            {
                "name": name_value,
                **normalized_fields,
            }
        )
    return checks


def _normalize_changed_paths(value: object) -> list[str]:
    """Return stable opaque repository labels; never resolve them on disk."""
    if not isinstance(value, list):
        raise _invalid("invalid_files", "files must be a list")
    paths: list[str] = []
    seen_paths: set[str] = set()
    seen_aliases: dict[str, str] = {}
    seen_target_aliases: set[str] = set()
    for index, item in enumerate(value, 1):
        if not isinstance(item, Mapping):
            raise _invalid("invalid_files", f"files item {index} must be an object")
        status = item.get("status")
        if type(status) is not str or status not in GH_FILE_STATUSES:
            raise _invalid(
                "invalid_files",
                f"files item {index} has an unknown status",
            )
        target = _require_repo_path(
            item.get("filename"),
            f"files item {index} filename",
        )
        target_alias = _path_alias_key(target)
        if target_alias in seen_target_aliases:
            raise _invalid(
                "invalid_files",
                f"files item {index} repeats target filename",
            )
        seen_target_aliases.add(target_alias)
        source: str | None = None
        if status in {"renamed", "copied"}:
            if "previous_filename" not in item:
                raise _invalid(
                    "invalid_files",
                    f"files item {index} {status} record requires previous_filename",
                )
            source = _require_repo_path(
                item.get("previous_filename"),
                f"files item {index} previous_filename",
            )
            if _path_alias_key(source) == target_alias:
                raise _invalid(
                    "invalid_files",
                    f"files item {index} source and target must be distinct",
                )
        elif "previous_filename" in item:
            raise _invalid(
                "invalid_files",
                f"files item {index} unexpected previous_filename for {status}",
            )
        for path in (source, target):
            if path is None:
                continue
            alias = _path_alias_key(path)
            prior = seen_aliases.get(alias)
            if prior is not None and prior != path:
                raise _invalid(
                    "invalid_files",
                    f"files item {index} introduces a path alias",
                )
            seen_aliases[alias] = path
            if path not in seen_paths:
                seen_paths.add(path)
                paths.append(path)
    if not paths:
        raise _invalid("invalid_files", "pull files did not contain any paths")
    return sorted(paths)


def _require_int(value: object, field: str) -> int:
    if type(value) is not int:
        raise _invalid("invalid_gh_json", f"{field} must be an integer")
    return value


def _require_exact_string(value: object, field: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise _invalid("invalid_gh_json", f"{field} must be a non-empty exact string")
    return value


def _require_sha(value: object, field: str) -> str:
    if type(value) is not str or not SHA_RE.fullmatch(value):
        decision = "invalid_head_sha" if "head" in field.lower() else "invalid_base_sha"
        raise _invalid(decision, f"{field} must be a 40-char lowercase sha")
    return value


def _require_repo_path(value: object, field: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise _invalid("invalid_files", f"{field} must be a non-empty exact string")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise _invalid("invalid_files", f"{field} contains invalid Unicode") from exc
    if "\\" in value:
        raise _invalid("invalid_files", f"{field} is not a canonical repository path")
    path = value
    if (
        path.startswith("/")
        or ":" in path
        or "*" in path
        or any(ord(character) < 32 for character in path)
    ):
        raise _invalid("invalid_files", f"{field} is not a safe repository path")
    pure = PurePosixPath(path)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise _invalid("invalid_files", f"{field} is not a safe repository path")
    normalized = pure.as_posix()
    if normalized != path:
        raise _invalid("invalid_files", f"{field} is not a canonical repository path")
    return normalized


def _path_alias_key(path: str) -> str:
    return unicodedata.normalize("NFC", path).casefold()


def _invalid(decision: str, message: str) -> PrStatusSnapshotError:
    return PrStatusSnapshotError(
        {
            "decision": decision,
            "ok": False,
            "errors": [message],
            "exit_code": 2,
        }
    )


def _run_command(command: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        list(command),
        check=False,
        capture_output=True,
    )


def _assert_no_private_markers(value: object) -> None:
    marker = _find_private_marker(value)
    if marker is not None:
        raise PrStatusSnapshotError(
            {
                "decision": "privacy_marker_refused",
                "ok": False,
                "errors": [f"privacy marker refused: {marker}"],
                "exit_code": 2,
            }
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
