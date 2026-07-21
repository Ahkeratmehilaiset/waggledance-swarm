#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Fail-closed, read-only bridge runtime live-attestation gate.

Production mode is sealed: it always audits the canonical C-drive checkout and
collects Windows process and Scheduled Task state during the same invocation.
Test callers may inject repositories, clocks, executables, or collectors, but
every such invocation is terminally labelled MATCH_TEST_ONLY and exits 3.

The gate never fetches, deploys, copies runtime files, writes bridge events, or
changes process or Scheduled Task state.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
import subprocess
import sys
from typing import Callable, Mapping, Sequence
import uuid


MANIFEST_PATH = "configs/bridge_runtime_deployment.v2.json"
GATE_SOURCE_PATH = "tools/bridge_runtime_deployment_gate.py"
MANIFEST_SCHEMA = "wd.bridge_runtime_deployment.v2"
EVIDENCE_SCHEMA = "wd.bridge_runtime.windows_evidence.v2"
RAW_EVIDENCE_SCHEMA = "wd.bridge_runtime.windows_raw.v2"
REPORT_SCHEMA = "wd.bridge_runtime_deployment_gate_report.v2"
VALIDATED_INVENTORY_SCHEMA = "wd.bridge_runtime.validated_inventory.v1"
INCOMPLETE_SCOPE_REASON = (
    "non_heuristic_process_task_scope_not_implemented"
)

ACTIVATION_READY = "ready"
ACTIVATION_HOLD = "hold_pending_pr_a_and_python_writer_migration"
SUPPORTED_PROTOCOL_STAGES = frozenset({"v1_fail_closed"})

CANONICAL_SOURCE_ROOT = Path(r"C:\Python\project2")
CANONICAL_GIT_COMMON_DIR = CANONICAL_SOURCE_ROOT / ".git"
CANONICAL_RUNTIME_ROOT = Path(r"C:\Python\project2-master\.agent-bridge")
DEFAULT_GIT_EXECUTABLE = Path(r"C:\Program Files\Git\cmd\git.exe")

FULL_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ENTITY_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
SID_RE = re.compile(r"^S-\d(?:-\d+)+$", re.IGNORECASE)

EXIT_LIVE_MATCH = 0
EXIT_ERROR = 2
EXIT_REFUSE = 3
EXIT_MATCH_TEST_ONLY = EXIT_REFUSE

LiveCollector = Callable[[Mapping[str, object]], Mapping[str, object]]


class RefusalError(ValueError):
    """The deployment definition or observation is unsafe."""


class AuditError(RuntimeError):
    """The requested read-only audit could not be completed reliably."""


@dataclass(frozen=True)
class GitResult:
    returncode: int
    stdout: bytes
    stderr: bytes


@dataclass(frozen=True)
class RepoIdentity:
    repo: Path
    top_level: Path
    common_git: Path
    head: str
    origin_main: str
    local_config_bytes: bytes
    local_config_entries: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class LocalGitConfig:
    payload: bytes
    entries: tuple[tuple[str, str], ...]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only bridge runtime live-attestation. Production paths and "
            "the Windows collector are sealed; caller snapshots are forbidden."
        )
    )
    parser.add_argument(
        "--expected-commit",
        required=True,
        help="Exact lowercase 40-hex commit; refs and short SHAs are refused.",
    )
    parser.add_argument("--json", action="store_true", help="Emit canonical JSON.")
    return parser


def authority_flags() -> dict[str, bool]:
    return {
        "apply_allowed": False,
        "bridge_append_allowed": False,
        "capability_grant_allowed": False,
        "deployment_allowed": False,
        "git_mutation_allowed": False,
        "merge_allowed": False,
        "process_mutation_allowed": False,
        "runtime_write_allowed": False,
        "scheduled_task_mutation_allowed": False,
        "source_write_allowed": False,
    }


def canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def command_digest(tokens: Sequence[str]) -> str:
    return canonical_digest(list(tokens))


def dependency_closure_digest(
    *,
    command_tokens: Sequence[str],
    toolchain: Sequence[Mapping[str, object]],
    runtime_blobs: Sequence[Mapping[str, object]],
) -> str:
    """Hash one action plus every declared executable dependency."""

    tools = [
        {
            "id": str(item["id"]),
            "path": str(item["path"]),
            "sha256": str(item["sha256"]),
            "size": int(item["size"]),
        }
        for item in toolchain
    ]
    blobs = [
        {
            "id": str(item["id"]),
            "source_path": str(item["source_path"]),
            "runtime_path": str(item["runtime_path"]),
            "sha256": str(item["sha256"]),
            "size": int(item["size"]),
        }
        for item in runtime_blobs
    ]
    tools.sort(key=lambda item: item["id"])
    blobs.sort(key=lambda item: item["id"])
    return canonical_digest(
        {
            "command_tokens": list(command_tokens),
            "runtime_blobs": blobs,
            "toolchain": tools,
        }
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = audit_bridge_runtime_deployment(expected_commit=args.expected_commit)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(report["decision"])
        for blocker in report.get("blockers", []):
            print(f"{blocker['code']}: {blocker['detail']}")
    return int(report["exit_code"])


def audit_bridge_runtime_deployment(
    *,
    expected_commit: str,
) -> dict[str, object]:
    """Run the sealed production audit with no caller-controlled evidence."""

    report = _evaluate_bridge_runtime_deployment(
        expected_commit=expected_commit,
        collector=None,
        repo=CANONICAL_SOURCE_ROOT,
        runtime_root=CANONICAL_RUNTIME_ROOT,
        git_executable=DEFAULT_GIT_EXECUTABLE,
        python_executable=_gate_python_executable(),
        now_utc=None,
        verify_gate_source=True,
        enforce_canonical_production_paths=True,
        evidence_source="live_windows",
    )
    # The current v2 manifest is deliberately HOLD and the remaining scope proof
    # is not implemented.  Keep authoritative success unreachable until an exact,
    # independently reviewed non-heuristic inventory proof replaces this guard.
    if report["matches_expected"]:
        blockers = report["blockers"]
        assert isinstance(blockers, list)
        _block(
            blockers,
            "live_authority_hold",
            INCOMPLETE_SCOPE_REASON,
        )
        report["matches_expected"] = False
    observations = report.get("observations")
    if isinstance(observations, dict):
        projection = observations.get("validated_inventory")
        if isinstance(projection, dict):
            # The scope proof is source-owned state, never caller/fixture state.
            projection["scope_proof"] = _incomplete_scope_proof()
            projection["inventory_sha256"] = canonical_digest(
                {
                    key: value
                    for key, value in projection.items()
                    if key != "inventory_sha256"
                }
            )
    terminal_kind = report["observations"].get("terminal_error_kind")
    if terminal_kind in {"audit_error", "unexpected_audit_error"}:
        report["decision"] = "ERROR"
        report["exit_code"] = EXIT_ERROR
        report["ok"] = False
    else:
        report["decision"] = "REFUSE"
        report["exit_code"] = EXIT_REFUSE
        report["ok"] = False
    report["authority"] = authority_flags()
    return report


def audit_bridge_runtime_deployment_for_test(
    *,
    expected_commit: str,
    collector: LiveCollector,
    repo: Path,
    runtime_root: Path,
    git_executable: Path,
    now_utc: datetime,
) -> dict[str, object]:
    """Evaluate injected evidence while sealing the result to exit 3."""

    return _evaluate_bridge_runtime_deployment(
        expected_commit=expected_commit,
        collector=collector,
        repo=repo,
        runtime_root=runtime_root,
        git_executable=git_executable,
        python_executable=_gate_python_executable(),
        now_utc=now_utc,
        verify_gate_source=False,
        enforce_canonical_production_paths=False,
        evidence_source="injected_fixture",
    )


def _evaluate_bridge_runtime_deployment(
    *,
    expected_commit: str,
    collector: LiveCollector | None,
    repo: Path,
    runtime_root: Path,
    git_executable: Path,
    python_executable: Path,
    now_utc: datetime | None,
    verify_gate_source: bool,
    enforce_canonical_production_paths: bool,
    evidence_source: str,
) -> dict[str, object]:
    """Reduce selected inputs to a permanently non-authoritative report.

    This function deliberately has no authority/mode switch and never emits
    exit 0.  All seams, including direct private calls, therefore remain
    test-only.  The public zero-injection wrapper is the sole future location
    where an authoritative decision may be introduced after the explicit HOLD.
    """

    selected_repo = repo
    selected_runtime = runtime_root
    selected_git = git_executable
    selected_now = now_utc
    if selected_now is not None:
        if selected_now.tzinfo is None:
            selected_now = selected_now.replace(tzinfo=timezone.utc)
        selected_now = selected_now.astimezone(timezone.utc)

    blockers: list[dict[str, str]] = []
    observations: dict[str, object] = {
        "expected_commit": expected_commit,
        "evidence_source": evidence_source,
    }
    terminal_error: tuple[str, str] | None = None

    try:
        if not FULL_COMMIT_RE.fullmatch(expected_commit):
            raise RefusalError(
                "expected_commit must be an exact lowercase 40-hex commit"
            )
        identity = _inspect_repo(
            selected_repo, expected_commit, git_executable=selected_git
        )
        observations.update(
            {
                "repo": str(identity.repo),
                "git_top_level": str(identity.top_level),
                "git_common_dir": str(identity.common_git),
                "head": identity.head,
                "origin_main": identity.origin_main,
            }
        )
        if verify_gate_source:
            observations["gate_source"] = _audit_gate_source(
                identity,
                expected_commit=expected_commit,
                git_executable=selected_git,
            )
        try:
            manifest_bytes = _git_blob(
                identity.repo,
                expected_commit,
                MANIFEST_PATH,
                git_executable=selected_git,
            )
        except AuditError as exc:
            raise RefusalError(
                f"manifest_missing_at_expected_commit:{MANIFEST_PATH}"
            ) from exc
        manifest = _decode_json_object(manifest_bytes, "deployment manifest")
        _validate_manifest(manifest)
        observations["manifest_git_object"] = (
            f"{expected_commit}:{MANIFEST_PATH}"
        )
        observations["activation_state"] = manifest["activation_state"]
        observations["protocol_stage"] = manifest["protocol_stage"]

        _audit_canonical_paths(
            manifest,
            identity=identity,
            selected_runtime=selected_runtime,
            enforce_production_paths=enforce_canonical_production_paths,
            blockers=blockers,
        )
        _audit_git_policy(
            manifest,
            identity=identity,
            git_executable=selected_git,
            blockers=blockers,
        )

        if manifest["activation_state"] != ACTIVATION_READY:
            _verify_git_config_unchanged(
                identity.common_git, identity.local_config_bytes
            )
            _block(
                blockers,
                "activation_hold",
                "; ".join(
                    str(value)
                    for value in _as_list(
                        manifest["pending_blockers"], "manifest.pending_blockers"
                    )
                ),
            )
        else:
            observations["toolchain"] = _audit_toolchain(
                manifest,
                selected_git=selected_git,
                selected_python=python_executable,
                blockers=blockers,
            )
            observations["runtime_blobs"] = _audit_runtime_blobs(
                manifest,
                repo=identity.repo,
                expected_commit=expected_commit,
                runtime_root=selected_runtime,
                git_executable=selected_git,
                blockers=blockers,
            )
            observations["collector"] = _audit_collector_source(
                manifest,
                repo=identity.repo,
                expected_commit=expected_commit,
                git_executable=selected_git,
                blockers=blockers,
            )
            _verify_git_config_unchanged(
                identity.common_git, identity.local_config_bytes
            )
            if not blockers:
                try:
                    evidence = (
                        collector(manifest)
                        if collector is not None
                        else collect_windows_evidence(manifest)
                    )
                    # Freshness is measured against a clock sampled only after
                    # the collector has completely returned.  Tests may supply
                    # a fixed post-collection instant; production cannot.
                    evidence_now = (
                        selected_now
                        if selected_now is not None
                        else datetime.now(timezone.utc)
                    )
                    live_evidence = _audit_ab_evidence(
                        evidence,
                        manifest=manifest,
                        exact_source_head=expected_commit,
                        provenance_head=identity.head,
                        now_utc=evidence_now,
                        blockers=blockers,
                    )
                    observations["live_evidence"] = live_evidence
                    projection = live_evidence.get("validated_inventory")
                    if projection is not None:
                        observations["validated_inventory"] = projection
                finally:
                    # Collection and reduction run outside Git.  Reverify once
                    # more so even an otherwise harmless config comment changed
                    # during that window terminally refuses the result.
                    _verify_git_config_unchanged(
                        identity.common_git, identity.local_config_bytes
                    )
    except RefusalError as exc:
        terminal_error = ("deployment_definition_refused", str(exc))
    except (AuditError, OSError, UnicodeError, ValueError) as exc:
        terminal_error = ("audit_error", str(exc))
    except Exception as exc:
        terminal_error = ("unexpected_audit_error", f"{type(exc).__name__}: {exc}")

    if terminal_error is not None:
        _block(blockers, terminal_error[0], terminal_error[1])
        observations["terminal_error_kind"] = terminal_error[0]

    matches_expected = not blockers
    if not matches_expected:
        # A projection is a zero-blocker artifact.  A failure discovered after
        # reduction (for example Git-config drift during collection) must not
        # leave a reusable inventory behind.
        observations.pop("validated_inventory", None)
        live_evidence = observations.get("live_evidence")
        if isinstance(live_evidence, dict):
            live_evidence.pop("validated_inventory", None)
    decision, exit_code, ok = "MATCH_TEST_ONLY", EXIT_MATCH_TEST_ONLY, False

    return {
        "schema": REPORT_SCHEMA,
        "ok": ok,
        "matches_expected": matches_expected,
        "decision": decision,
        "exit_code": exit_code,
        "mode": "read_only",
        "blockers": blockers,
        "observations": observations,
        "authority": authority_flags(),
    }


def _audit_gate_source(
    identity: RepoIdentity,
    *,
    expected_commit: str,
    git_executable: Path,
) -> dict[str, object]:
    expected_path = identity.repo.joinpath(*PurePosixPath(GATE_SOURCE_PATH).parts)
    executing_path = Path(__file__).absolute()
    if not _same_path(executing_path, expected_path):
        raise RefusalError(
            f"production gate must execute from {expected_path}; got {executing_path}"
        )
    observed = _read_ordinary_file(executing_path, label="deployment gate source")
    expected = _git_blob(
        identity.repo,
        expected_commit,
        GATE_SOURCE_PATH,
        git_executable=git_executable,
    )
    if observed != expected:
        raise RefusalError("executing deployment gate differs from the exact commit")
    return {
        "path": str(executing_path),
        "sha256": hashlib.sha256(observed).hexdigest(),
        "size": len(observed),
    }


def _inspect_repo(
    repo: Path, expected_commit: str, *, git_executable: Path
) -> RepoIdentity:
    git_path = _absolute_existing_file(git_executable, "git executable")
    repo_path = _absolute_existing_dir(repo, "canonical repo")
    _reject_reparse_components(repo_path, label="canonical repo")

    dot_git = repo_path / ".git"
    try:
        dot_git_stat = dot_git.lstat()
    except OSError as exc:
        raise RefusalError("canonical source has no physical .git directory") from exc
    if not stat.S_ISDIR(dot_git_stat.st_mode) or _stat_is_reparse(dot_git_stat):
        raise RefusalError(
            "canonical C-drive source must have a physical .git directory"
        )
    _reject_reparse_components(dot_git, label="canonical git directory")
    _reject_git_metadata_reparse(dot_git)
    config_snapshot = _preflight_git_config(dot_git / "config")
    _reject_local_git_overrides(dot_git)

    top_raw = _git(
        repo_path,
        "rev-parse",
        "--show-toplevel",
        git_executable=git_path,
    ).stdout.decode("utf-8", errors="strict").strip()
    common_raw = _git(
        repo_path,
        "rev-parse",
        "--git-common-dir",
        git_executable=git_path,
    ).stdout.decode("utf-8", errors="strict").strip()
    top_level = Path(top_raw).resolve(strict=True)
    common_candidate = Path(common_raw)
    if not common_candidate.is_absolute():
        common_candidate = repo_path / common_candidate
    common_git = common_candidate.resolve(strict=True)
    if not _same_path(top_level, repo_path):
        raise RefusalError(
            f"git top level mismatch: observed={top_level}; expected={repo_path}"
        )
    if not _same_path(common_git, dot_git):
        raise RefusalError(
            f"git common dir mismatch: observed={common_git}; expected={dot_git}"
        )
    _reject_git_metadata_reparse(common_git)

    forbidden_files = (
        common_git / "info" / "grafts",
        common_git / "objects" / "info" / "alternates",
        common_git / "objects" / "info" / "http-alternates",
        common_git / "shallow",
    )
    for candidate in forbidden_files:
        if candidate.exists() or candidate.is_symlink():
            raise RefusalError(f"git indirection is forbidden: {candidate}")
    promisor_packs = list((common_git / "objects" / "pack").glob("*.promisor"))
    if promisor_packs:
        raise RefusalError(f"promisor object packs are forbidden: {promisor_packs[0]}")

    replace_refs = _git(
        repo_path,
        "for-each-ref",
        "--format=%(refname)",
        "refs/replace",
        git_executable=git_path,
    ).stdout.decode("utf-8", errors="strict").strip()
    if replace_refs:
        raise RefusalError(f"git replace refs are forbidden: {replace_refs}")

    shallow = _git(
        repo_path,
        "rev-parse",
        "--is-shallow-repository",
        git_executable=git_path,
    ).stdout.decode("ascii", errors="strict").strip()
    if shallow != "false":
        raise RefusalError("shallow repositories are forbidden")

    includes = _git(
        repo_path,
        "config",
        "--local",
        "--includes",
        "--get-regexp",
        r"^(include\.path|includeIf\..*\.path)$",
        git_executable=git_path,
        check=False,
    )
    if includes.returncode == 0 and includes.stdout.strip():
        raise RefusalError("Git config includes/includeIf are forbidden")
    if includes.returncode not in {0, 1}:
        raise AuditError("could not inspect Git config includes")

    partial = _git(
        repo_path,
        "config",
        "--includes",
        "--get-regexp",
        r"^(extensions\.partialClone|extensions\.worktreeConfig|"
        r"remote\..*\.(promisor|partialCloneFilter))$",
        git_executable=git_path,
        check=False,
    )
    if partial.returncode == 0 and partial.stdout.strip():
        raise RefusalError("partial-clone/promisor configuration is forbidden")
    if partial.returncode not in {0, 1}:
        raise AuditError("could not inspect partial-clone configuration")

    head = _git(
        repo_path,
        "rev-parse",
        "--verify",
        "HEAD^{commit}",
        git_executable=git_path,
    ).stdout.decode("ascii", errors="strict").strip()
    head_ref = _git(
        repo_path,
        "symbolic-ref",
        "-q",
        "HEAD",
        git_executable=git_path,
        check=False,
    )
    if head_ref.returncode != 0 or head_ref.stdout.decode(
        "utf-8", errors="strict"
    ).strip() != "refs/heads/main":
        raise RefusalError("canonical source HEAD must be attached to refs/heads/main")
    if head != expected_commit:
        raise RefusalError(
            f"HEAD must equal expected_commit: head={head}; expected={expected_commit}"
        )
    origin_result = _git(
        repo_path,
        "rev-parse",
        "--verify",
        "refs/remotes/origin/main^{commit}",
        git_executable=git_path,
        check=False,
    )
    if origin_result.returncode != 0:
        raise RefusalError("refs/remotes/origin/main is missing")
    origin_main = origin_result.stdout.decode("ascii", errors="strict").strip()
    if origin_main != expected_commit:
        raise RefusalError(
            "source requires HEAD == expected_commit == origin/main: "
            f"origin/main={origin_main}; expected={expected_commit}"
        )
    ancestry = _git(
        repo_path,
        "merge-base",
        "--is-ancestor",
        "refs/remotes/origin/main",
        "HEAD",
        git_executable=git_path,
        check=False,
    )
    if ancestry.returncode != 0:
        raise RefusalError("origin/main is not an ancestor of HEAD")

    status = _git(
        repo_path,
        "status",
        "--porcelain=v2",
        "--untracked-files=all",
        "--ignored=matching",
        "--ignore-submodules=none",
        git_executable=git_path,
    ).stdout
    if status:
        detail = status.decode("utf-8", errors="replace").strip()
        raise RefusalError(
            f"canonical repo index/worktree is not clean: {detail}"
        )
    for args, label in (
        (
            ("diff", "--quiet", "--no-ext-diff", "--no-textconv"),
            "tracked worktree",
        ),
        (
            ("diff", "--cached", "--quiet", "--no-ext-diff", "--no-textconv"),
            "index",
        ),
    ):
        result = _git(repo_path, *args, git_executable=git_path, check=False)
        if result.returncode != 0:
            raise RefusalError(f"canonical repo {label} differs from HEAD")

    flagged = _git(
        repo_path,
        "ls-files",
        "-v",
        "-z",
        git_executable=git_path,
    ).stdout.split(b"\0")
    for entry in flagged:
        if not entry:
            continue
        tag = chr(entry[0])
        if tag == "S" or tag.islower():
            path = entry[2:].decode("utf-8", errors="replace")
            raise RefusalError(
                f"skip-worktree/assume-unchanged index flag is forbidden: {path}"
            )

    _audit_index_worktree_bytes(repo_path, git_executable=git_path)

    fsck = _git(
        repo_path,
        "fsck",
        "--full",
        "--strict",
        "--no-reflogs",
        "--no-progress",
        expected_commit,
        git_executable=git_path,
        check=False,
    )
    if fsck.returncode != 0:
        detail = fsck.stderr.decode("utf-8", errors="replace").strip()
        raise RefusalError(f"git object integrity check failed: {detail}")

    # Recheck every metadata path after the Git reads so a link introduced
    # during the audit cannot silently become part of the attested result.
    _reject_git_metadata_reparse(common_git)
    _verify_git_config_unchanged(common_git, config_snapshot.payload)

    return RepoIdentity(
        repo=repo_path,
        top_level=top_level,
        common_git=common_git,
        head=head,
        origin_main=origin_main,
        local_config_bytes=config_snapshot.payload,
        local_config_entries=config_snapshot.entries,
    )


def _audit_git_policy(
    manifest: Mapping[str, object],
    *,
    identity: RepoIdentity,
    git_executable: Path,
    blockers: list[dict[str, str]],
) -> None:
    policy = _as_mapping(manifest["git_policy"], "manifest.git_policy")
    del git_executable  # Config was parsed physically before any Git process.
    observed = dict(identity.local_config_entries).get("remote.origin.url", "")
    expected = str(policy["origin_remote_url"])
    if observed != expected:
        _block(
            blockers,
            "origin_remote_url_mismatch",
            f"observed={observed!r}; expected={expected!r}",
        )


def _audit_canonical_paths(
    manifest: Mapping[str, object],
    *,
    identity: RepoIdentity,
    selected_runtime: Path,
    enforce_production_paths: bool,
    blockers: list[dict[str, str]],
) -> None:
    canonical = _as_mapping(manifest["canonical"], "manifest.canonical")
    expected_source = _absolute_path(canonical["source_root"], "source_root")
    expected_common = _absolute_path(canonical["git_common_dir"], "git_common_dir")
    expected_runtime = _absolute_path(canonical["runtime_root"], "runtime_root")
    if not _same_path(identity.repo, expected_source):
        _block(blockers, "canonical_repo_mismatch", str(identity.repo))
    if not _same_path(identity.common_git, expected_common):
        _block(blockers, "git_common_dir_mismatch", str(identity.common_git))
    if not _same_path(selected_runtime, expected_runtime):
        _block(
            blockers,
            "runtime_root_mismatch",
            f"observed={selected_runtime}; expected={expected_runtime}",
        )
    if enforce_production_paths:
        for observed, required, code in (
            (identity.repo, CANONICAL_SOURCE_ROOT, "production_source_not_canonical"),
            (
                identity.common_git,
                CANONICAL_GIT_COMMON_DIR,
                "production_git_not_canonical",
            ),
            (
                expected_runtime,
                CANONICAL_RUNTIME_ROOT,
                "production_runtime_not_canonical",
            ),
        ):
            if not _same_path(observed, required):
                _block(blockers, code, f"observed={observed}; required={required}")


def _audit_toolchain(
    manifest: Mapping[str, object],
    *,
    selected_git: Path,
    selected_python: Path,
    blockers: list[dict[str, str]],
) -> list[dict[str, object]]:
    reports: list[dict[str, object]] = []
    definitions = _as_list(manifest["toolchain"], "manifest.toolchain")
    for raw in definitions:
        item = _as_mapping(raw, "toolchain entry")
        path = _absolute_path(item["path"], f"toolchain {item['id']} path")
        report: dict[str, object] = {"id": item["id"], "path": str(path)}
        try:
            payload = _read_ordinary_file(path, label=f"toolchain {item['id']}")
            digest = hashlib.sha256(payload).hexdigest()
            report.update({"sha256": digest, "size": len(payload)})
            if digest != item["sha256"] or len(payload) != item["size"]:
                _block(
                    blockers,
                    "toolchain_hash_mismatch",
                    f"{item['id']}: observed={digest}/{len(payload)}",
                )
        except (OSError, AuditError) as exc:
            _block(blockers, "toolchain_unavailable", f"{item['id']}: {exc}")
        reports.append(report)
    git_entries = [
        item
        for item in definitions
        if isinstance(item, dict) and item.get("id") == "git"
    ]
    if len(git_entries) != 1 or not _same_path(
        _absolute_path(git_entries[0]["path"], "git toolchain path"), selected_git
    ):
        _block(blockers, "git_toolchain_mismatch", f"selected={selected_git}")
    python_entries = [
        item
        for item in definitions
        if isinstance(item, dict) and item.get("id") == "python-gate"
    ]
    if len(python_entries) != 1 or (
        len(python_entries) == 1
        and not _same_path(
            _absolute_path(
                python_entries[0]["path"], "gate Python toolchain path"
            ),
            selected_python,
        )
    ):
        _block(
            blockers,
            "python_gate_toolchain_mismatch",
            f"selected={selected_python}",
        )
    return reports


def _audit_collector_source(
    manifest: Mapping[str, object],
    *,
    repo: Path,
    expected_commit: str,
    git_executable: Path,
    blockers: list[dict[str, str]],
) -> dict[str, object]:
    collector = _as_mapping(manifest["collector"], "manifest.collector")
    source_path = _strict_git_path(collector["source_path"])
    expected = _git_blob(
        repo,
        expected_commit,
        source_path,
        git_executable=git_executable,
    )
    observed = _read_ordinary_file(
        _join_under_root(
            repo, PurePosixPath(source_path), label="collector source path"
        ),
        label="collector source",
    )
    expected_digest = hashlib.sha256(expected).hexdigest()
    observed_digest = hashlib.sha256(observed).hexdigest()
    report = {
        "source_path": source_path,
        "sha256": observed_digest,
        "size": len(observed),
    }
    if (
        expected != observed
        or observed_digest != collector["sha256"]
        or len(observed) != collector["size"]
        or expected_digest != collector["sha256"]
    ):
        _block(blockers, "collector_source_mismatch", source_path)
    return report


def _audit_runtime_blobs(
    manifest: Mapping[str, object],
    *,
    repo: Path,
    expected_commit: str,
    runtime_root: Path,
    git_executable: Path,
    blockers: list[dict[str, str]],
) -> list[dict[str, object]]:
    reports: list[dict[str, object]] = []
    payloads: dict[str, bytes] = {}
    definitions: dict[str, Mapping[str, object]] = {}
    for raw in _as_list(manifest["runtime_blobs"], "manifest.runtime_blobs"):
        item = _as_mapping(raw, "runtime blob")
        item_id = str(item["id"])
        definitions[item_id] = item
        source_path = _strict_git_path(item["source_path"])
        runtime_path = _strict_relative_path(
            item["runtime_path"], f"runtime blob {item['id']} runtime_path"
        )
        git_blob = _git_blob(
            repo,
            expected_commit,
            source_path,
            git_executable=git_executable,
        )
        payloads[item_id] = git_blob
        runtime_file = _join_under_root(
            runtime_root, runtime_path, label=f"runtime blob {item['id']}"
        )
        report: dict[str, object] = {
            "id": item["id"],
            "source_path": source_path,
            "runtime_path": str(runtime_file),
        }
        try:
            runtime_payload = _read_ordinary_file(
                runtime_file, label=f"runtime blob {item['id']}"
            )
        except (OSError, AuditError) as exc:
            _block(blockers, "runtime_blob_unavailable", f"{item['id']}: {exc}")
            reports.append(report)
            continue
        git_digest = hashlib.sha256(git_blob).hexdigest()
        runtime_digest = hashlib.sha256(runtime_payload).hexdigest()
        report.update({"sha256": runtime_digest, "size": len(runtime_payload)})
        if (
            git_blob != runtime_payload
            or git_digest != item["sha256"]
            or runtime_digest != item["sha256"]
            or len(git_blob) != item["size"]
            or len(runtime_payload) != item["size"]
        ):
            _block(blockers, "runtime_blob_mismatch", str(item["id"]))
        reports.append(report)
    for report in reports:
        item_id = str(report["id"])
        if item_id not in payloads:
            continue
        try:
            discovered = _discover_runtime_dependencies(
                item_id, payloads[item_id], definitions
            )
        except RefusalError as exc:
            _block(blockers, "runtime_dependency_discovery_refused", str(exc))
            continue
        report["discovered_dependency_ids"] = sorted(discovered)
        declared = set(
            str(value)
            for value in _as_list(
                definitions[item_id]["dependency_ids"],
                f"runtime blob {item_id} dependencies",
            )
        )
        if discovered != declared:
            _block(
                blockers,
                "runtime_dependency_discovery_mismatch",
                f"{item_id}: discovered={sorted(discovered)}; "
                f"declared={sorted(declared)}",
            )
    return reports


def _discover_runtime_dependencies(
    item_id: str,
    payload: bytes,
    definitions: Mapping[str, Mapping[str, object]],
) -> set[str]:
    """Independently discover literal file inputs in text runtime artifacts."""

    current = _strict_relative_path(
        definitions[item_id]["runtime_path"], f"runtime blob {item_id} path"
    )
    text_suffixes = {
        ".ps1",
        ".psm1",
        ".psd1",
        ".py",
        ".pyw",
        ".cmd",
        ".bat",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
        ".config",
    }
    if current.suffix.casefold() not in text_suffixes:
        return set()
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise RefusalError(f"text runtime blob {item_id} is not UTF-8") from exc

    by_path = {
        _strict_relative_path(value["runtime_path"], "runtime dependency path"):
        dependency_id
        for dependency_id, value in definitions.items()
    }
    discovered: set[str] = set()
    for match in re.finditer(r"(?P<quote>['\"])(?P<value>[^'\"\r\n]+)(?P=quote)", text):
        raw_value = match.group("value")
        if PureWindowsPath(raw_value).suffix.casefold() not in (
            text_suffixes | {".exe", ".dll"}
        ):
            continue
        if PureWindowsPath(raw_value).is_absolute() or PurePosixPath(
            raw_value
        ).is_absolute():
            raise RefusalError(
                f"runtime blob {item_id} contains an absolute literal dependency"
            )
        raw_parts = raw_value.replace("\\", "/").split("/")
        combined = list(current.parent.parts)
        for part in raw_parts:
            if part in {"", "."}:
                continue
            if part == "..":
                if not combined:
                    raise RefusalError(
                        f"runtime blob {item_id} dependency escapes runtime root"
                    )
                combined.pop()
            else:
                combined.append(part)
        reference = _strict_relative_path(
            "/".join(combined), f"runtime blob {item_id} literal dependency"
        )
        dependency = by_path.get(reference)
        if dependency is None:
            raise RefusalError(
                f"runtime blob {item_id} contains undeclared literal input "
                f"{raw_value!r}"
            )
        if dependency != item_id:
            discovered.add(dependency)
    return discovered


def _incomplete_scope_proof() -> dict[str, object]:
    return {
        "complete": False,
        "reason": INCOMPLETE_SCOPE_REASON,
    }


def _validated_inventory_projection(
    *,
    exact_source_head: str,
    provenance_head: str,
    manifest: Mapping[str, object],
    sample_a: Mapping[str, object],
    sample_b: Mapping[str, object],
    process_inventory: Mapping[str, set[tuple[object, ...]]],
    task_inventory: Mapping[str, tuple[object, ...]],
) -> dict[str, object]:
    """Project validated live state without exposing sensitive inventory data.

    The caller must already have validated the manifest, the source/runtime
    byte chain, and A/B evidence.  This final reducer binds those results to an
    exact Git commit while publishing only allow-listed metadata and digests.
    """

    if not FULL_COMMIT_RE.fullmatch(exact_source_head):
        raise RefusalError("validated inventory source head must be exact")
    if provenance_head != exact_source_head:
        raise RefusalError(
            "validated inventory provenance head does not match expected commit"
        )

    actions = _as_mapping(manifest["actions"], "manifest.actions")
    process_definitions = _projection_actions_by_id(
        _as_list(actions["processes"], "manifest.actions.processes"),
        label="process",
    )
    task_definitions = _projection_actions_by_id(
        _as_list(
            actions["scheduled_tasks"],
            "manifest.actions.scheduled_tasks",
        ),
        label="scheduled task",
    )
    if set(process_inventory) != set(process_definitions):
        raise RefusalError("validated process inventory provenance mismatch")
    if set(task_inventory) != set(task_definitions):
        raise RefusalError("validated task inventory provenance mismatch")

    tool_definitions = _definitions_by_id(
        _as_list(manifest["toolchain"], "manifest.toolchain"), "toolchain"
    )
    processes: list[dict[str, object]] = []
    seen_process_pids: set[int] = set()
    for action_id in sorted(process_definitions):
        definition = process_definitions[action_id]
        identities = process_inventory[action_id]
        if len(identities) != definition["required_count"]:
            raise RefusalError(
                f"validated process inventory cardinality mismatch: {action_id}"
            )
        for hidden_identity in identities:
            if len(hidden_identity) != 7:
                raise RefusalError("validated process identity shape is invalid")
            pid = hidden_identity[0]
            if type(pid) is not int or pid <= 0:
                raise RefusalError("validated process identity pid is invalid")
            if hidden_identity[5] != definition["command_sha256"]:
                raise RefusalError(
                    f"validated process action provenance mismatch: {action_id}"
                )
            executable_tool_id = str(definition["executable_toolchain_id"])
            if (
                executable_tool_id not in tool_definitions
                or hidden_identity[4]
                != tool_definitions[executable_tool_id]["sha256"]
            ):
                raise RefusalError(
                    f"validated process executable provenance mismatch: {action_id}"
                )
            identity_sha256 = canonical_digest(
                {
                    "action": _projection_action_binding(
                        definition, digest_field="command_sha256"
                    ),
                    "hidden_identity": list(hidden_identity),
                    "kind": "process",
                }
            )
            if pid in seen_process_pids:
                raise RefusalError("duplicate validated process identity")
            seen_process_pids.add(pid)
            processes.append(
                {
                    "action_id": action_id,
                    "pid": pid,
                    "identity_sha256": identity_sha256,
                    "command_sha256": definition["command_sha256"],
                    "closure_sha256": definition["closure_sha256"],
                    "entrypoint_blob_id": definition["entrypoint_blob_id"],
                    "dependency_blob_ids": sorted(
                        str(value)
                        for value in definition["dependency_blob_ids"]
                    ),
                    "toolchain_ids": sorted(
                        str(value) for value in definition["toolchain_ids"]
                    ),
                }
            )
    processes.sort(
        key=lambda item: (
            str(item["action_id"]),
            int(item["pid"]),
            str(item["identity_sha256"]),
        )
    )

    scheduled_tasks: list[dict[str, object]] = []
    seen_task_identities: set[str] = set()
    for action_id in sorted(task_definitions):
        definition = task_definitions[action_id]
        hidden_identity = task_inventory[action_id]
        if len(hidden_identity) != 7:
            raise RefusalError("validated task identity shape is invalid")
        if (
            hidden_identity[5] != definition["action_sha256"]
            or hidden_identity[6] != definition["definition_sha256"]
        ):
            raise RefusalError(
                f"validated task action provenance mismatch: {action_id}"
            )
        identity_sha256 = canonical_digest(
            {
                "action": _projection_action_binding(
                    definition, digest_field="action_sha256"
                ),
                "hidden_identity": list(hidden_identity),
                "kind": "scheduled_task",
            }
        )
        if identity_sha256 in seen_task_identities:
            raise RefusalError("duplicate validated task identity")
        seen_task_identities.add(identity_sha256)
        scheduled_tasks.append(
            {
                "action_id": action_id,
                "identity_sha256": identity_sha256,
                "action_sha256": definition["action_sha256"],
                "definition_sha256": definition["definition_sha256"],
                "closure_sha256": definition["closure_sha256"],
                "entrypoint_blob_id": definition["entrypoint_blob_id"],
                "dependency_blob_ids": sorted(
                    str(value)
                    for value in definition["dependency_blob_ids"]
                ),
                "toolchain_ids": sorted(
                    str(value) for value in definition["toolchain_ids"]
                ),
            }
        )
    scheduled_tasks.sort(
        key=lambda item: (str(item["action_id"]), str(item["identity_sha256"]))
    )

    runtime_blobs: list[dict[str, object]] = []
    for raw in _as_list(manifest["runtime_blobs"], "manifest.runtime_blobs"):
        item = _as_mapping(raw, "runtime blob")
        runtime_blobs.append(
            {
                "id": item["id"],
                "source_path": _strict_git_path(item["source_path"]),
                "sha256": item["sha256"],
                "size": item["size"],
            }
        )
    runtime_blobs.sort(key=lambda item: str(item["id"]))

    toolchain: list[dict[str, object]] = []
    for raw in _as_list(manifest["toolchain"], "manifest.toolchain"):
        item = _as_mapping(raw, "toolchain entry")
        toolchain.append(
            {
                "id": item["id"],
                "sha256": item["sha256"],
                "size": item["size"],
            }
        )
    toolchain.sort(key=lambda item: str(item["id"]))

    process_identity_hashes = sorted(
        str(item["identity_sha256"]) for item in processes
    )
    task_identity_hashes = sorted(
        str(item["identity_sha256"]) for item in scheduled_tasks
    )
    captures: list[dict[str, object]] = []
    for sample in (sample_a, sample_b):
        sample_host = _as_mapping(sample["host"], "sample host")
        captured_at = _format_utc(
            _parse_utc(sample["captured_at_utc"], "sample captured_at_utc")
        )
        captures.append(
            {
                "label": sample["label"],
                "captured_at_utc": captured_at,
                "sample_sha256": canonical_digest(
                    {
                        "boot_id_sha256": canonical_digest(
                            str(sample_host["boot_id"]).casefold()
                        ),
                        "captured_at_utc": captured_at,
                        "host_identity_sha256": sample_host[
                            "host_identity_sha256"
                        ],
                        "label": sample["label"],
                        "process_identity_sha256s": process_identity_hashes,
                        "task_identity_sha256s": task_identity_hashes,
                    }
                ),
            }
        )
    captures.sort(key=lambda item: str(item["label"]))

    host = _as_mapping(sample_b["host"], "sample B host")
    projection: dict[str, object] = {
        "schema": VALIDATED_INVENTORY_SCHEMA,
        "exact_source_head": exact_source_head,
        "host_identity_sha256": host["host_identity_sha256"],
        "boot_id_sha256": canonical_digest(str(host["boot_id"]).casefold()),
        "captures": captures,
        "processes": processes,
        "scheduled_tasks": scheduled_tasks,
        "runtime_blobs": runtime_blobs,
        "toolchain": toolchain,
        "scope_proof": _incomplete_scope_proof(),
    }
    projection["inventory_sha256"] = canonical_digest(projection)
    return projection


def _projection_actions_by_id(
    entries: list[object], *, label: str
) -> dict[str, Mapping[str, object]]:
    result: dict[str, Mapping[str, object]] = {}
    for raw in entries:
        item = _as_mapping(raw, f"{label} action")
        action_id = str(item["id"])
        if action_id in result:
            raise RefusalError(f"duplicate {label} projection action: {action_id}")
        result[action_id] = item
    return result


def _projection_action_binding(
    action: Mapping[str, object], *, digest_field: str
) -> dict[str, object]:
    return {
        "action_id": action["id"],
        digest_field: action[digest_field],
        "closure_sha256": action["closure_sha256"],
        "entrypoint_blob_id": action["entrypoint_blob_id"],
        "dependency_blob_ids": sorted(
            str(value) for value in action["dependency_blob_ids"]
        ),
        "toolchain_ids": sorted(
            str(value) for value in action["toolchain_ids"]
        ),
    }


def _audit_ab_evidence(
    evidence: Mapping[str, object],
    *,
    manifest: Mapping[str, object],
    exact_source_head: str,
    provenance_head: str,
    now_utc: datetime,
    blockers: list[dict[str, str]],
) -> dict[str, object]:
    initial_blocker_count = len(blockers)
    evidence_map = _as_mapping(evidence, "Windows evidence")
    _require_exact_keys(
        evidence_map, {"schema", "collection", "samples"}, "Windows evidence"
    )
    if evidence_map["schema"] != EVIDENCE_SCHEMA:
        raise RefusalError("unsupported Windows evidence schema")
    samples = _as_list(evidence_map["samples"], "Windows evidence samples")
    if len(samples) != 2:
        raise RefusalError("Windows evidence must contain exactly A and B samples")
    sample_a = _validate_sample(samples[0], expected_label="A")
    sample_b = _validate_sample(samples[1], expected_label="B")
    policy = _as_mapping(manifest["host_policy"], "manifest.host_policy")
    collection = _as_mapping(evidence_map["collection"], "evidence collection")
    _require_exact_keys(
        collection,
        {
            "started_at_utc",
            "completed_at_utc",
            "started_monotonic_ns",
            "completed_monotonic_ns",
        },
        "evidence collection",
    )

    host_a = _as_mapping(sample_a["host"], "sample A host")
    host_b = _as_mapping(sample_b["host"], "sample B host")
    for field in (
        "host_identity_sha256",
        "boot_id",
        "boot_time_utc",
        "collector_sid",
        "is_elevated",
    ):
        if host_a[field] != host_b[field]:
            _block(blockers, f"ab_host_{field}_drift", str(field))
    if host_a["host_identity_sha256"] != policy["expected_host_identity_sha256"]:
        _block(blockers, "host_identity_mismatch", str(host_a["host_identity_sha256"]))
    expected_sid = policy["expected_collector_sid"]
    if expected_sid is not None and str(host_a["collector_sid"]).casefold() != str(
        expected_sid
    ).casefold():
        _block(blockers, "collector_sid_mismatch", str(host_a["collector_sid"]))
    if policy["require_elevated"] and host_a["is_elevated"] is not True:
        _block(blockers, "collector_not_elevated", "live collection requires elevation")

    captured_a = _parse_utc(sample_a["captured_at_utc"], "sample A captured_at_utc")
    captured_b = _parse_utc(sample_b["captured_at_utc"], "sample B captured_at_utc")
    started_at = _parse_utc(collection["started_at_utc"], "collector started_at_utc")
    completed_at = _parse_utc(
        collection["completed_at_utc"], "collector completed_at_utc"
    )
    boot_time = _parse_utc(host_a["boot_time_utc"], "host boot_time_utc")
    monotonic_a = int(sample_a["monotonic_ns"])
    monotonic_b = int(sample_b["monotonic_ns"])
    monotonic_start = int(collection["started_monotonic_ns"])
    monotonic_end = int(collection["completed_monotonic_ns"])
    if not all(
        type(collection[field]) is int and collection[field] >= 0
        for field in ("started_monotonic_ns", "completed_monotonic_ns")
    ):
        raise RefusalError("collector monotonic envelope is invalid")
    gap_ms = (monotonic_b - monotonic_a) / 1_000_000
    if not (
        monotonic_start <= monotonic_a < monotonic_b <= monotonic_end
        and started_at <= captured_a <= captured_b <= completed_at
    ):
        _block(blockers, "ab_time_not_monotonic", f"A={captured_a}; B={captured_b}")
    if not (
        int(policy["sample_gap_min_ms"])
        <= gap_ms
        <= int(policy["sample_gap_max_ms"])
    ):
        _block(blockers, "ab_sample_gap_out_of_bounds", f"gap_ms={gap_ms}")
    collection_ms = (monotonic_end - monotonic_start) / 1_000_000
    wall_collection_ms = (completed_at - started_at).total_seconds() * 1000
    if (
        collection_ms > int(policy["collection_max_ms"])
        or wall_collection_ms > int(policy["collection_max_ms"])
    ):
        _block(
            blockers,
            "collection_window_too_long",
            f"collection_ms={collection_ms}",
        )
    age_ms = (now_utc - completed_at).total_seconds() * 1000
    if age_ms < -1000:
        _block(blockers, "evidence_from_future", f"age_ms={age_ms}")
    elif age_ms > int(policy["evidence_max_age_ms"]):
        _block(blockers, "evidence_stale", f"age_ms={age_ms}")
    if boot_time > captured_a:
        _block(blockers, "boot_time_after_sample", str(boot_time))

    process_a = _audit_process_inventory(
        sample_a, manifest=manifest, label="A", blockers=blockers
    )
    process_b = _audit_process_inventory(
        sample_b, manifest=manifest, label="B", blockers=blockers
    )
    if process_a != process_b:
        _block(blockers, "ab_process_inventory_drift", "A/B process identities differ")

    task_a = _audit_task_inventory(
        sample_a, manifest=manifest, label="A", blockers=blockers
    )
    task_b = _audit_task_inventory(
        sample_b, manifest=manifest, label="B", blockers=blockers
    )
    if task_a != task_b:
        _block(blockers, "ab_task_inventory_drift", "A/B task identities differ")

    evidence_digest = canonical_digest(evidence_map)
    valid_until = completed_at.timestamp() + int(policy["evidence_max_age_ms"]) / 1000
    result: dict[str, object] = {
        "host_identity_sha256": host_a["host_identity_sha256"],
        "boot_id": host_a["boot_id"],
        "sample_gap_ms": gap_ms,
        "evidence_sha256": evidence_digest,
        "captured_at_utc": _format_utc(completed_at),
        "valid_until_utc": _format_utc(
            datetime.fromtimestamp(valid_until, tz=timezone.utc)
        ),
        "process_identity_count": sum(len(value) for value in process_b.values()),
        "scheduled_task_count": len(task_b),
    }
    if len(blockers) == initial_blocker_count == 0:
        result["validated_inventory"] = _validated_inventory_projection(
            exact_source_head=exact_source_head,
            provenance_head=provenance_head,
            manifest=manifest,
            sample_a=sample_a,
            sample_b=sample_b,
            process_inventory=process_b,
            task_inventory=task_b,
        )
    return result


def _validate_sample(raw: object, *, expected_label: str) -> Mapping[str, object]:
    sample = _as_mapping(raw, f"sample {expected_label}")
    _require_exact_keys(
        sample,
        {
            "label",
            "captured_at_utc",
            "monotonic_ns",
            "host",
            "processes",
            "scheduled_tasks",
        },
        f"sample {expected_label}",
    )
    if sample["label"] != expected_label:
        raise RefusalError(f"sample order must be A then B, got {sample['label']!r}")
    if type(sample["monotonic_ns"]) is not int or sample["monotonic_ns"] < 0:
        raise RefusalError(f"sample {expected_label} monotonic_ns is invalid")
    host = _as_mapping(sample["host"], f"sample {expected_label} host")
    _require_exact_keys(
        host,
        {
            "host_identity_sha256",
            "boot_id",
            "boot_time_utc",
            "collector_sid",
            "is_elevated",
        },
        f"sample {expected_label} host",
    )
    if not SHA256_RE.fullmatch(str(host["host_identity_sha256"])):
        raise RefusalError("sample host identity must be a sha256")
    if not isinstance(host["boot_id"], str):
        raise RefusalError("sample boot_id is missing")
    try:
        uuid.UUID(host["boot_id"])
    except ValueError as exc:
        raise RefusalError("sample boot_id must be a UUID") from exc
    if not isinstance(host["collector_sid"], str) or not SID_RE.fullmatch(
        host["collector_sid"]
    ):
        raise RefusalError("sample collector_sid is invalid")
    if not isinstance(host["is_elevated"], bool):
        raise RefusalError("sample is_elevated must be boolean")
    _parse_utc(sample["captured_at_utc"], "sample captured_at_utc")
    _parse_utc(host["boot_time_utc"], "sample boot_time_utc")
    _as_list(sample["processes"], "sample processes")
    _as_list(sample["scheduled_tasks"], "sample scheduled_tasks")
    return sample


def _audit_process_inventory(
    sample: Mapping[str, object],
    *,
    manifest: Mapping[str, object],
    label: str,
    blockers: list[dict[str, str]],
) -> dict[str, set[tuple[object, ...]]]:
    sample_host = _as_mapping(sample["host"], f"sample {label} host")
    boot_time = _parse_utc(sample_host["boot_time_utc"], "host boot_time_utc")
    captured_at = _parse_utc(
        sample["captured_at_utc"], f"sample {label} captured_at_utc"
    )
    actions = _as_mapping(manifest["actions"], "manifest.actions")
    expected_raw = _as_list(actions["processes"], "manifest.actions.processes")
    expected = {
        str(_as_mapping(item, "process action")["command_sha256"]): _as_mapping(
            item, "process action"
        )
        for item in expected_raw
    }
    observed_by_digest: dict[str, list[Mapping[str, object]]] = {}
    seen_pids: set[int] = set()
    for raw in _as_list(sample["processes"], f"sample {label} processes"):
        item = _as_mapping(raw, "observed process")
        _require_exact_keys(
            item,
            {
                "pid",
                "parent_pid",
                "parent_creation_time_utc",
                "creation_time_utc",
                "executable_path",
                "executable_sha256",
                "command_tokens",
                "command_sha256",
                "owner_sid",
            },
            "observed process",
        )
        tokens = _valid_tokens(item["command_tokens"], "observed process tokens")
        if type(item["pid"]) is not int or item["pid"] <= 0:
            _block(blockers, "observed_process_pid_invalid", f"sample={label}")
            continue
        if item["pid"] in seen_pids:
            _block(
                blockers,
                "duplicate_observed_process_pid",
                f"sample={label}; pid={item['pid']}",
            )
            continue
        seen_pids.add(item["pid"])
        if type(item["parent_pid"]) is not int or item["parent_pid"] < 0:
            _block(blockers, "observed_parent_pid_invalid", f"sample={label}")
            continue
        if item["command_sha256"] != command_digest(tokens):
            _block(blockers, "observed_process_digest_invalid", f"sample={label}")
            continue
        digest = str(item["command_sha256"])
        observed_by_digest.setdefault(digest, []).append(item)
        if digest not in expected:
            _block(
                blockers,
                "unknown_bridge_process",
                f"sample={label}; digest={digest}; pid={item['pid']}",
            )

    identities: dict[str, set[tuple[object, ...]]] = {}
    tools = _definitions_by_id(
        _as_list(manifest["toolchain"], "manifest.toolchain"), "toolchain"
    )
    for digest, definition in expected.items():
        action_id = str(definition["id"])
        observed = observed_by_digest.get(digest, [])
        required = int(definition["required_count"])
        if len(observed) != required:
            _block(
                blockers,
                "process_cardinality_mismatch",
                f"{action_id} sample={label} observed={len(observed)} "
                f"required={required}",
            )
        tool = tools[str(definition["executable_toolchain_id"])]
        expected_tokens = list(definition["command_tokens"])
        action_identities: set[tuple[object, ...]] = set()
        for item in observed:
            tokens = list(item["command_tokens"])
            if tokens != expected_tokens:
                _block(blockers, "process_command_mismatch", action_id)
            if not _same_path(
                _absolute_path(item["executable_path"], "process executable"),
                _absolute_path(tool["path"], "toolchain executable"),
            ):
                _block(blockers, "process_executable_path_mismatch", action_id)
            if item["executable_sha256"] != tool["sha256"]:
                _block(blockers, "process_executable_hash_mismatch", action_id)
            expected_owner = definition["owner_sid"]
            if expected_owner is not None and str(item["owner_sid"]).casefold() != str(
                expected_owner
            ).casefold():
                _block(blockers, "process_owner_mismatch", action_id)
            created = _parse_utc(
                item["creation_time_utc"], "process creation_time_utc"
            )
            if created < boot_time or created > captured_at:
                _block(
                    blockers,
                    "process_creation_outside_boot_sample_window",
                    f"{action_id}: created={created}; boot={boot_time}; "
                    f"sample={captured_at}",
                )
            parent_created: datetime | None = None
            if item["parent_pid"]:
                if not isinstance(item["parent_creation_time_utc"], str):
                    _block(blockers, "process_parent_identity_missing", action_id)
                else:
                    parent_created = _parse_utc(
                        item["parent_creation_time_utc"],
                        "process parent_creation_time_utc",
                    )
                    if not (boot_time <= parent_created <= created):
                        _block(
                            blockers,
                            "process_parent_creation_outside_chain_window",
                            action_id,
                        )
            elif item["parent_creation_time_utc"] is not None:
                _block(blockers, "process_parent_identity_invalid", action_id)
            action_identities.add(
                (
                    int(item["pid"]),
                    _format_utc(created),
                    int(item["parent_pid"]),
                    _format_utc(parent_created) if parent_created else None,
                    str(item["executable_sha256"]),
                    digest,
                    str(item["owner_sid"]).casefold(),
                )
            )
        identities[action_id] = action_identities
    return identities


def _audit_task_inventory(
    sample: Mapping[str, object],
    *,
    manifest: Mapping[str, object],
    label: str,
    blockers: list[dict[str, str]],
) -> dict[str, tuple[object, ...]]:
    actions = _as_mapping(manifest["actions"], "manifest.actions")
    expected_raw = _as_list(
        actions["scheduled_tasks"], "manifest.actions.scheduled_tasks"
    )
    expected = {
        _task_key(
            str(_as_mapping(item, "task action")["task_path"]),
            str(_as_mapping(item, "task action")["task_name"]),
        ): _as_mapping(item, "task action")
        for item in expected_raw
    }
    observed: dict[tuple[str, str], Mapping[str, object]] = {}
    for raw in _as_list(
        sample["scheduled_tasks"], f"sample {label} scheduled_tasks"
    ):
        item = _as_mapping(raw, "observed scheduled task")
        _require_exact_keys(
            item,
            {
                "task_path",
                "task_name",
                "enabled",
                "principal_sid",
                "run_level",
                "working_directory",
                "action_tokens",
                "action_sha256",
                "definition_sha256",
            },
            "observed scheduled task",
        )
        key = _task_key(str(item["task_path"]), str(item["task_name"]))
        if key in observed:
            _block(blockers, "duplicate_scheduled_task", repr(key))
            continue
        observed[key] = item
        if key not in expected:
            _block(
                blockers,
                "unknown_bridge_scheduled_task",
                f"sample={label}; task={key}",
            )

    result: dict[str, tuple[object, ...]] = {}
    for key, definition in expected.items():
        action_id = str(definition["id"])
        item = observed.get(key)
        if item is None:
            _block(
                blockers,
                "missing_scheduled_task",
                f"sample={label}; task={key}",
            )
            continue
        tokens = _valid_tokens(item["action_tokens"], "task action tokens")
        if item["action_sha256"] != command_digest(tokens):
            _block(blockers, "observed_task_digest_invalid", action_id)
        for field in (
            "enabled",
            "principal_sid",
            "run_level",
            "working_directory",
            "action_tokens",
            "action_sha256",
            "definition_sha256",
        ):
            observed_value = item[field]
            expected_value = definition[field]
            if field == "principal_sid":
                matches = str(observed_value).casefold() == str(
                    expected_value
                ).casefold()
            elif field == "working_directory":
                matches = _same_path(
                    _absolute_path(observed_value, "task working directory"),
                    _absolute_path(expected_value, "expected task working directory"),
                )
            else:
                matches = observed_value == expected_value
            if not matches:
                _block(blockers, f"scheduled_task_{field}_mismatch", action_id)
        result[action_id] = (
            key,
            item["enabled"],
            str(item["principal_sid"]).casefold(),
            item["run_level"],
            str(item["working_directory"]).casefold(),
            item["action_sha256"],
            item["definition_sha256"],
        )
    return result


def collect_windows_evidence(
    manifest: Mapping[str, object],
) -> Mapping[str, object]:
    """Collect A/B Windows state without caller-supplied snapshots."""

    if os.name != "nt":
        raise AuditError("live bridge attestation is supported only on Windows")
    collector = _as_mapping(manifest["collector"], "manifest.collector")
    canonical = _as_mapping(manifest["canonical"], "manifest.canonical")
    repo = _absolute_path(canonical["source_root"], "canonical source_root")
    collector_script = _join_under_root(
        repo,
        PurePosixPath(_strict_git_path(collector["source_path"])),
        label="collector script",
    )
    tools = _definitions_by_id(
        _as_list(manifest["toolchain"], "manifest.toolchain"), "toolchain"
    )
    powershell = _as_mapping(
        tools[str(collector["powershell_toolchain_id"])],
        "collector PowerShell toolchain",
    )
    powershell_path = _absolute_path(powershell["path"], "collector PowerShell")
    policy = _as_mapping(manifest["host_policy"], "manifest.host_policy")
    gap_ms = max(
        int(policy["sample_gap_min_ms"]),
        min(500, int(policy["sample_gap_max_ms"])),
    )
    boot_before = _windows_boot_identifier()
    completed = subprocess.run(
        [
            str(powershell_path),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(collector_script),
            "-SampleGapMilliseconds",
            str(gap_ms),
        ],
        check=False,
        capture_output=True,
        env=_collector_environment(),
        timeout=(int(policy["collection_max_ms"]) / 1000) + 10,
    )
    boot_after = _windows_boot_identifier()
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise AuditError(
            f"Windows evidence collector failed with {completed.returncode}: {detail}"
        )
    raw = _decode_json_object(completed.stdout, "Windows raw evidence")
    return _normalize_windows_evidence(
        raw,
        manifest=manifest,
        boot_before=boot_before,
        boot_after=boot_after,
    )


def _normalize_windows_evidence(
    raw: Mapping[str, object],
    *,
    manifest: Mapping[str, object],
    boot_before: str,
    boot_after: str,
) -> Mapping[str, object]:
    _validate_raw_evidence_shape(raw)
    _require_exact_keys(
        raw,
        {
            "schema",
            "collector_pid",
            "stopwatch_frequency",
            "collector_started_at_utc",
            "collector_completed_at_utc",
            "collector_started_ticks",
            "collector_completed_ticks",
            "samples",
        },
        "raw evidence",
    )
    if raw["schema"] != RAW_EVIDENCE_SCHEMA:
        raise AuditError("Windows collector returned an unsupported schema")
    frequency = raw["stopwatch_frequency"]
    assert type(frequency) is int
    if frequency <= 0:
        raise AuditError("Windows collector returned an invalid stopwatch frequency")
    raw_samples = _as_list(raw["samples"], "raw evidence samples")
    if len(raw_samples) != 2:
        raise AuditError("Windows collector did not return exactly two samples")
    collector_pid = raw["collector_pid"]
    assert type(collector_pid) is int
    boot_ids = (boot_before, boot_after)
    normalized: list[dict[str, object]] = []
    for index, raw_sample in enumerate(raw_samples):
        sample = _as_mapping(raw_sample, "raw Windows sample")
        _require_exact_keys(
            sample,
            {
                "label",
                "captured_at_utc",
                "monotonic_ticks",
                "host",
                "processes",
                "scheduled_tasks",
            },
            "raw Windows sample",
        )
        host_raw = _as_mapping(sample["host"], "raw sample host")
        host_identity = canonical_digest(
            {
                "machine_guid": host_raw["machine_guid"].casefold(),
                "smbios_uuid": host_raw["smbios_uuid"].casefold(),
                "system_volume_serial": host_raw[
                    "system_volume_serial"
                ].casefold(),
            }
        )
        host = {
            "host_identity_sha256": host_identity,
            "boot_id": boot_ids[index],
            "boot_time_utc": host_raw["boot_time_utc"],
            "collector_sid": host_raw["collector_sid"],
            "is_elevated": host_raw["is_elevated"],
        }
        normalized.append(
            {
                "label": sample["label"],
                "captured_at_utc": sample["captured_at_utc"],
                "monotonic_ns": sample["monotonic_ticks"]
                * 1_000_000_000
                // frequency,
                "host": host,
                "processes": _normalize_candidate_processes(
                    sample["processes"],
                    manifest=manifest,
                    collector_pid=collector_pid,
                ),
                "scheduled_tasks": _normalize_candidate_tasks(
                    sample["scheduled_tasks"], manifest=manifest
                ),
            }
        )
    return {
        "schema": EVIDENCE_SCHEMA,
        "collection": {
            "started_at_utc": raw["collector_started_at_utc"],
            "completed_at_utc": raw["collector_completed_at_utc"],
            "started_monotonic_ns": raw["collector_started_ticks"]
            * 1_000_000_000
            // frequency,
            "completed_monotonic_ns": raw["collector_completed_ticks"]
            * 1_000_000_000
            // frequency,
        },
        "samples": normalized,
    }


def _validate_raw_evidence_shape(raw: Mapping[str, object]) -> None:
    """Validate collector JSON without Python truthiness or type coercion."""

    _require_exact_keys(
        raw,
        {
            "schema",
            "collector_pid",
            "stopwatch_frequency",
            "collector_started_at_utc",
            "collector_completed_at_utc",
            "collector_started_ticks",
            "collector_completed_ticks",
            "samples",
        },
        "raw evidence",
    )
    if type(raw["collector_pid"]) is not int or raw["collector_pid"] <= 0:
        raise RefusalError("raw collector_pid must be a positive integer")
    for field in (
        "stopwatch_frequency",
        "collector_started_ticks",
        "collector_completed_ticks",
    ):
        if type(raw[field]) is not int or raw[field] < 0:
            raise RefusalError(f"raw {field} must be a non-negative integer")
    if raw["stopwatch_frequency"] == 0:
        raise RefusalError("raw stopwatch_frequency must be positive")
    for field in ("collector_started_at_utc", "collector_completed_at_utc"):
        _parse_utc(raw[field], f"raw {field}")

    samples = _as_list(raw["samples"], "raw samples")
    if len(samples) != 2:
        raise RefusalError("raw evidence must contain exactly two samples")
    for expected_label, sample_raw in zip(("A", "B"), samples, strict=True):
        sample = _as_mapping(sample_raw, f"raw sample {expected_label}")
        _require_exact_keys(
            sample,
            {
                "label",
                "captured_at_utc",
                "monotonic_ticks",
                "host",
                "processes",
                "scheduled_tasks",
            },
            f"raw sample {expected_label}",
        )
        if type(sample["label"]) is not str or sample["label"] != expected_label:
            raise RefusalError("raw sample labels must be exact A then B")
        _parse_utc(sample["captured_at_utc"], "raw sample captured_at_utc")
        if type(sample["monotonic_ticks"]) is not int or sample[
            "monotonic_ticks"
        ] < 0:
            raise RefusalError("raw sample monotonic_ticks must be an integer")

        host = _as_mapping(sample["host"], "raw sample host")
        _require_exact_keys(
            host,
            {
                "machine_guid",
                "smbios_uuid",
                "system_volume_serial",
                "boot_time_utc",
                "collector_sid",
                "is_elevated",
            },
            "raw sample host",
        )
        for field in (
            "machine_guid",
            "smbios_uuid",
            "system_volume_serial",
            "boot_time_utc",
            "collector_sid",
        ):
            if type(host[field]) is not str or not host[field]:
                raise RefusalError(f"raw host {field} must be a non-empty string")
        _parse_utc(host["boot_time_utc"], "raw host boot_time_utc")
        if not SID_RE.fullmatch(host["collector_sid"]):
            raise RefusalError("raw host collector_sid is invalid")
        if type(host["is_elevated"]) is not bool:
            raise RefusalError("raw host is_elevated must be boolean")

        process_ids: set[int] = set()
        for process_raw in _as_list(sample["processes"], "raw processes"):
            process = _as_mapping(process_raw, "raw process")
            _require_exact_keys(
                process,
                {
                    "pid",
                    "parent_pid",
                    "creation_time_utc",
                    "executable_path",
                    "command_line",
                    "owner_sid",
                    "owner_error",
                },
                "raw process",
            )
            if type(process["pid"]) is not int or process["pid"] < 0:
                raise RefusalError("raw process pid must be a non-negative integer")
            if process["pid"] in process_ids:
                raise RefusalError(f"duplicate raw process pid: {process['pid']}")
            process_ids.add(process["pid"])
            if type(process["parent_pid"]) is not int or process["parent_pid"] < 0:
                raise RefusalError("raw process parent_pid must be an integer")
            for field in (
                "creation_time_utc",
                "executable_path",
                "command_line",
                "owner_sid",
                "owner_error",
            ):
                if process[field] is not None and type(process[field]) is not str:
                    raise RefusalError(f"raw process {field} has the wrong type")
            if process["creation_time_utc"] is not None:
                _parse_utc(
                    process["creation_time_utc"], "raw process creation_time_utc"
                )
            if process["owner_sid"] is not None and not SID_RE.fullmatch(
                process["owner_sid"]
            ):
                raise RefusalError("raw process owner_sid is invalid")

        task_keys: set[tuple[str, str]] = set()
        for task_raw in _as_list(
            sample["scheduled_tasks"], "raw scheduled tasks"
        ):
            task = _as_mapping(task_raw, "raw Scheduled Task")
            _require_exact_keys(
                task,
                {
                    "task_path",
                    "task_name",
                    "enabled",
                    "state",
                    "principal_sid",
                    "run_level",
                    "actions",
                    "definition_xml",
                },
                "raw Scheduled Task",
            )
            for field in (
                "task_path",
                "task_name",
                "principal_sid",
                "run_level",
                "definition_xml",
            ):
                if type(task[field]) is not str:
                    raise RefusalError(f"raw Scheduled Task {field} has wrong type")
            if type(task["enabled"]) is not bool or type(task["state"]) is not int:
                raise RefusalError("raw Scheduled Task boolean/state types are invalid")
            key = _task_key(task["task_path"], task["task_name"])
            if key in task_keys:
                raise RefusalError(f"duplicate raw Scheduled Task: {key}")
            task_keys.add(key)
            for action_raw in _as_list(task["actions"], "raw task actions"):
                action = _as_mapping(action_raw, "raw task action")
                _require_exact_keys(
                    action,
                    {"type", "path", "arguments", "working_directory"},
                    "raw task action",
                )
                if type(action["type"]) is not int:
                    raise RefusalError("raw task action type must be an integer")
                for field in ("path", "arguments", "working_directory"):
                    if type(action[field]) is not str:
                        raise RefusalError(
                            f"raw task action {field} must be a string"
                        )


def _normalize_candidate_processes(
    raw_processes: object,
    *,
    manifest: Mapping[str, object],
    collector_pid: int,
) -> list[dict[str, object]]:
    records: dict[int, Mapping[str, object]] = {}
    tokens_by_pid: dict[int, list[str]] = {}
    expected_commands = {
        tuple(_valid_tokens(_as_mapping(item, "process action")["command_tokens"], "process action tokens"))
        for item in _as_list(
            _as_mapping(manifest["actions"], "actions")["processes"],
            "process actions",
        )
    }
    scope_roots = _scope_roots(manifest)
    candidates: set[int] = set()
    for raw in _as_list(raw_processes, "raw processes"):
        item = _as_mapping(raw, "raw process")
        pid = item["pid"]
        assert type(pid) is int
        if pid in records:
            raise RefusalError(f"duplicate raw process pid: {pid}")
        records[pid] = item
        command_line = item.get("command_line")
        tokens = (
            windows_command_line_to_argv(command_line) if command_line else []
        )
        tokens_by_pid[pid] = tokens
        if tuple(tokens) in expected_commands or _tokens_touch_scope(
            [item.get("executable_path") or "", *tokens], scope_roots
        ):
            candidates.add(pid)
    candidates.discard(os.getpid())
    candidates.discard(collector_pid)

    changed = True
    while changed:
        changed = False
        for pid, item in records.items():
            if pid not in candidates and item["parent_pid"] in candidates:
                candidates.add(pid)
                changed = True

    # Bind the complete ancestry of every scoped process to the same snapshot.
    # Missing parents, cycles, and incomplete parent creation identities refuse.
    chain_members = set(candidates)
    for candidate_pid in tuple(candidates):
        seen: set[int] = set()
        current_pid = candidate_pid
        while current_pid > 0:
            if current_pid in seen:
                raise RefusalError(f"raw process ancestry cycle at pid {current_pid}")
            seen.add(current_pid)
            current = records.get(current_pid)
            if current is None:
                raise RefusalError(
                    f"missing ancestor metadata for bridge candidate pid {current_pid}"
                )
            chain_members.add(current_pid)
            parent_pid = current["parent_pid"]
            assert type(parent_pid) is int
            if parent_pid == 0:
                break
            if parent_pid not in records:
                raise RefusalError(
                    f"missing parent pid {parent_pid} for bridge candidate pid "
                    f"{current_pid}"
                )
            current_pid = parent_pid

    for pid in sorted(chain_members):
        item = records[pid]
        tokens = tokens_by_pid[pid]
        executable_path = item["executable_path"]
        creation_time = item["creation_time_utc"]
        if not executable_path or not creation_time:
            raise RefusalError(f"incomplete identity for process-chain pid {pid}")
        _reject_dynamic_wrapper(tokens, label=f"process-chain pid {pid}")
        if os.name == "nt":
            _revalidate_windows_process(
                pid,
                expected_path=Path(executable_path),
                expected_creation=_parse_utc(
                    creation_time, "WMI process creation time"
                ),
            )

    normalized: list[dict[str, object]] = []
    for pid in sorted(candidates):
        item = records[pid]
        tokens = tokens_by_pid[pid]
        executable_path = item.get("executable_path")
        if (
            not tokens
            or not executable_path
            or not item.get("creation_time_utc")
            or not item.get("owner_sid")
            or item.get("owner_error")
        ):
            raise RefusalError(
                f"incomplete metadata for bridge candidate process {pid}"
            )
        parent_pid = item["parent_pid"]
        parent_creation: str | None = None
        if parent_pid:
            parent = records[parent_pid]
            parent_creation = parent["creation_time_utc"]
            if not parent_creation:
                raise RefusalError(
                    f"missing parent creation identity for bridge candidate {pid}"
                )
        payload = _read_ordinary_file(
            Path(executable_path), label=f"process executable pid={pid}"
        )
        normalized.append(
            {
                "pid": pid,
                "parent_pid": parent_pid,
                "parent_creation_time_utc": parent_creation,
                "creation_time_utc": item["creation_time_utc"],
                "executable_path": executable_path,
                "executable_sha256": hashlib.sha256(payload).hexdigest(),
                "command_tokens": tokens,
                "command_sha256": command_digest(tokens),
                "owner_sid": item.get("owner_sid") or "",
            }
        )
    return normalized


def _revalidate_windows_process(
    pid: int, *, expected_path: Path, expected_creation: datetime
) -> None:
    """Bind a WMI row to a live process handle and reject PID reuse/exit."""

    process_query_limited_information = 0x1000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        raise RefusalError(
            f"bridge candidate pid {pid} cannot be reopened: {ctypes.get_last_error()}"
        )
    try:
        capacity = wintypes.DWORD(32768)
        image_buffer = ctypes.create_unicode_buffer(capacity.value)
        if not kernel32.QueryFullProcessImageNameW(
            handle, 0, image_buffer, ctypes.byref(capacity)
        ):
            raise RefusalError(
                f"bridge candidate pid {pid} image cannot be queried: "
                f"{ctypes.get_last_error()}"
            )
        if not _same_path(Path(image_buffer.value), expected_path):
            raise RefusalError(
                f"bridge candidate pid {pid} image changed after collection"
            )

        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel_time = wintypes.FILETIME()
        user_time = wintypes.FILETIME()
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ):
            raise RefusalError(
                f"bridge candidate pid {pid} creation cannot be queried: "
                f"{ctypes.get_last_error()}"
            )
        filetime = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        unix_seconds = (filetime - 116444736000000000) / 10_000_000
        observed_creation = datetime.fromtimestamp(unix_seconds, tz=timezone.utc)
        if abs((observed_creation - expected_creation).total_seconds()) > 0.1:
            raise RefusalError(
                f"bridge candidate pid {pid} creation identity changed"
            )
    finally:
        kernel32.CloseHandle(handle)


def _gate_python_executable() -> Path:
    """Return the native image that is hosting this Python process."""

    if os.name != "nt":
        return Path(sys.executable).resolve(strict=True)
    process_query_limited_information = 0x1000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(
        process_query_limited_information, False, os.getpid()
    )
    if not handle:
        raise AuditError(
            f"cannot open gate Python process: {ctypes.get_last_error()}"
        )
    try:
        capacity = wintypes.DWORD(32768)
        image_buffer = ctypes.create_unicode_buffer(capacity.value)
        if not kernel32.QueryFullProcessImageNameW(
            handle, 0, image_buffer, ctypes.byref(capacity)
        ):
            raise AuditError(
                f"cannot query gate Python image: {ctypes.get_last_error()}"
            )
        return _absolute_existing_file(
            Path(image_buffer.value), "gate Python executable"
        )
    finally:
        kernel32.CloseHandle(handle)


def _normalize_candidate_tasks(
    raw_tasks: object, *, manifest: Mapping[str, object]
) -> list[dict[str, object]]:
    actions = _as_mapping(manifest["actions"], "actions")
    expected_keys = {
        _task_key(
            str(_as_mapping(item, "task action")["task_path"]),
            str(_as_mapping(item, "task action")["task_name"]),
        )
        for item in _as_list(actions["scheduled_tasks"], "task actions")
    }
    scope_roots = _scope_roots(manifest)
    normalized: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for raw in _as_list(raw_tasks, "raw scheduled tasks"):
        item = _as_mapping(raw, "raw scheduled task")
        key = _task_key(item["task_path"], item["task_name"])
        if key in seen:
            raise RefusalError(f"duplicate raw Scheduled Task: {key}")
        seen.add(key)
        raw_actions = _as_list(item["actions"], "raw task actions")
        action_tokens: list[str] = []
        if len(raw_actions) == 1:
            preview = _as_mapping(raw_actions[0], "raw task action")
            action_tokens = [
                preview["path"],
                *_windows_arguments_to_argv(preview["arguments"]),
            ]
        candidate = key in expected_keys or _tokens_touch_scope(
            action_tokens, scope_roots
        )
        if not candidate:
            continue
        if len(raw_actions) != 1:
            raise RefusalError(
                f"bridge task {key} must have exactly one Exec action"
            )
        action = _as_mapping(raw_actions[0], "raw task action")
        if action["type"] != 0:
            raise RefusalError(f"bridge task {key} uses a non-Exec action")
        executable = action["path"]
        arguments = action["arguments"]
        tokens = [executable, *_windows_arguments_to_argv(arguments)]
        _reject_dynamic_wrapper(tokens, label=f"Scheduled Task {key}")
        definition_xml = item["definition_xml"]
        normalized.append(
            {
                "task_path": item["task_path"],
                "task_name": item["task_name"],
                "enabled": item["enabled"],
                "principal_sid": item["principal_sid"],
                "run_level": item["run_level"],
                "working_directory": action["working_directory"],
                "action_tokens": tokens,
                "action_sha256": command_digest(tokens),
                "definition_sha256": hashlib.sha256(
                    definition_xml.encode("utf-8")
                ).hexdigest(),
            }
        )
    return normalized


def _scope_roots(manifest: Mapping[str, object]) -> tuple[Path, Path]:
    canonical = _as_mapping(manifest["canonical"], "canonical")
    return (
        _absolute_path(canonical["source_root"], "scope source_root"),
        _absolute_path(canonical["runtime_root"], "scope runtime_root"),
    )


def _tokens_touch_scope(tokens: Sequence[object], roots: Sequence[Path]) -> bool:
    """Classify only lexical absolute paths under the two declared roots."""

    for raw in tokens:
        if not isinstance(raw, str) or not raw:
            continue
        candidate = Path(raw)
        if candidate.is_absolute() and any(_is_within(candidate, root) for root in roots):
            return True
    return False


def _reject_dynamic_wrapper(tokens: Sequence[str], *, label: str) -> None:
    if not tokens:
        raise RefusalError(f"{label} has no exact command tokens")
    executable = PureWindowsPath(tokens[0]).name.casefold()
    options = {token.casefold() for token in tokens[1:]}
    powershell = executable in {"powershell.exe", "powershell", "pwsh.exe", "pwsh"}
    if powershell and options & {
        "-command",
        "-c",
        "-encodedcommand",
        "-enc",
        "-e",
    }:
        raise RefusalError(f"{label} uses an encoded/dynamic PowerShell command")
    if executable in {"cmd.exe", "cmd"} and options & {"/c", "/k"}:
        raise RefusalError(f"{label} uses a generic cmd wrapper")
    if executable in {"python.exe", "python", "python3.exe", "python3"} and options & {
        "-c",
        "-m",
    }:
        raise RefusalError(f"{label} uses a generic Python wrapper")


def windows_command_line_to_argv(command_line: str) -> list[str]:
    if "\x00" in command_line:
        raise AuditError("Windows command line contains NUL")
    if not command_line.strip():
        return []
    if os.name == "nt":
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        argc = ctypes.c_int()
        shell32.CommandLineToArgvW.argtypes = [
            wintypes.LPCWSTR,
            ctypes.POINTER(ctypes.c_int),
        ]
        shell32.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
        argv = shell32.CommandLineToArgvW(command_line, ctypes.byref(argc))
        if not argv:
            raise AuditError(
                f"CommandLineToArgvW failed: {ctypes.get_last_error()}"
            )
        try:
            return [argv[index] for index in range(argc.value)]
        finally:
            kernel32.LocalFree(argv)
    return _portable_windows_argv(command_line)


def _portable_windows_argv(command_line: str) -> list[str]:
    result: list[str] = []
    length = len(command_line)
    index = 0
    while index < length:
        while index < length and command_line[index] in " \t":
            index += 1
        if index >= length:
            break
        value: list[str] = []
        quoted = False
        while index < length:
            if command_line[index] == "\\":
                start = index
                while index < length and command_line[index] == "\\":
                    index += 1
                count = index - start
                if index < length and command_line[index] == '"':
                    value.extend("\\" for _ in range(count // 2))
                    if count % 2:
                        value.append('"')
                    else:
                        quoted = not quoted
                    index += 1
                else:
                    value.extend("\\" for _ in range(count))
                continue
            if command_line[index] == '"':
                quoted = not quoted
                index += 1
                continue
            if command_line[index] in " \t" and not quoted:
                break
            value.append(command_line[index])
            index += 1
        result.append("".join(value))
        while index < length and command_line[index] in " \t":
            index += 1
    return result


def _windows_arguments_to_argv(arguments: str) -> list[str]:
    if not arguments.strip():
        return []
    parsed = windows_command_line_to_argv(f"__wd_task__ {arguments}")
    if not parsed or parsed[0] != "__wd_task__":
        raise AuditError("could not parse Scheduled Task arguments")
    return parsed[1:]


def _windows_boot_identifier() -> str:
    if os.name != "nt":
        raise AuditError("Windows boot identifier requested on a non-Windows host")

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    class SYSTEM_BOOT_ENVIRONMENT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BootIdentifier", GUID),
            ("FirmwareType", wintypes.DWORD),
            ("BootFlags", ctypes.c_ulonglong),
        ]

    ntdll = ctypes.WinDLL("ntdll")
    ntdll.NtQuerySystemInformation.argtypes = [
        wintypes.ULONG,
        wintypes.LPVOID,
        wintypes.ULONG,
        ctypes.POINTER(wintypes.ULONG),
    ]
    ntdll.NtQuerySystemInformation.restype = ctypes.c_long
    info = SYSTEM_BOOT_ENVIRONMENT_INFORMATION()
    returned = wintypes.ULONG()
    status = ntdll.NtQuerySystemInformation(
        90,
        ctypes.byref(info),
        ctypes.sizeof(info),
        ctypes.byref(returned),
    )
    if status != 0:
        raise AuditError(f"NtQuerySystemInformation boot ID failed: {status:#x}")
    raw = bytes(
        ctypes.string_at(
            ctypes.byref(info.BootIdentifier), ctypes.sizeof(info.BootIdentifier)
        )
    )
    return str(uuid.UUID(bytes_le=raw))


def _validate_manifest(manifest: Mapping[str, object]) -> None:
    _require_exact_keys(
        manifest,
        {
            "schema",
            "activation_state",
            "protocol_stage",
            "canonical",
            "git_policy",
            "host_policy",
            "collector",
            "toolchain",
            "runtime_blobs",
            "actions",
            "pending_blockers",
        },
        "deployment manifest",
    )
    if manifest["schema"] != MANIFEST_SCHEMA:
        raise RefusalError("unsupported deployment manifest schema")
    if manifest["activation_state"] not in {ACTIVATION_READY, ACTIVATION_HOLD}:
        raise RefusalError("unsupported activation_state")
    if manifest["protocol_stage"] not in SUPPORTED_PROTOCOL_STAGES:
        raise RefusalError("unsupported protocol_stage")

    canonical = _as_mapping(manifest["canonical"], "manifest.canonical")
    _require_exact_keys(
        canonical,
        {"source_root", "git_common_dir", "runtime_root"},
        "manifest.canonical",
    )
    for field in canonical:
        _absolute_path(canonical[field], f"manifest.canonical.{field}")

    git_policy = _as_mapping(manifest["git_policy"], "manifest.git_policy")
    _require_exact_keys(
        git_policy,
        {
            "origin_remote_url",
            "require_head_equals_origin_main",
            "reject_replace_refs",
            "reject_grafts",
            "reject_alternates",
            "reject_shallow_or_promisor",
        },
        "manifest.git_policy",
    )
    if not isinstance(git_policy["origin_remote_url"], str) or not git_policy[
        "origin_remote_url"
    ]:
        raise RefusalError("origin_remote_url is required")
    for field in set(git_policy) - {"origin_remote_url"}:
        if git_policy[field] is not True:
            raise RefusalError(f"git policy {field} must be true")

    host = _as_mapping(manifest["host_policy"], "manifest.host_policy")
    _require_exact_keys(
        host,
        {
            "expected_host_identity_sha256",
            "expected_collector_sid",
            "require_elevated",
            "sample_gap_min_ms",
            "sample_gap_max_ms",
            "collection_max_ms",
            "evidence_max_age_ms",
        },
        "manifest.host_policy",
    )
    for field in (
        "sample_gap_min_ms",
        "sample_gap_max_ms",
        "collection_max_ms",
        "evidence_max_age_ms",
    ):
        if type(host[field]) is not int or int(host[field]) <= 0:
            raise RefusalError(f"host policy {field} must be a positive integer")
    if int(host["sample_gap_min_ms"]) > int(host["sample_gap_max_ms"]):
        raise RefusalError("sample gap min exceeds max")
    if not isinstance(host["require_elevated"], bool):
        raise RefusalError("require_elevated must be boolean")

    collector = _as_mapping(manifest["collector"], "manifest.collector")
    _require_exact_keys(
        collector,
        {"source_path", "sha256", "size", "powershell_toolchain_id"},
        "manifest.collector",
    )
    _strict_git_path(collector["source_path"])

    tools_raw = _as_list(manifest["toolchain"], "manifest.toolchain")
    blobs_raw = _as_list(manifest["runtime_blobs"], "manifest.runtime_blobs")
    actions = _as_mapping(manifest["actions"], "manifest.actions")
    _require_exact_keys(actions, {"processes", "scheduled_tasks"}, "manifest.actions")
    pending = _as_list(manifest["pending_blockers"], "manifest.pending_blockers")
    if not all(isinstance(item, str) and item for item in pending):
        raise RefusalError("pending_blockers must contain non-empty strings")

    if manifest["activation_state"] == ACTIVATION_HOLD:
        if not pending:
            raise RefusalError("HOLD manifest must declare pending blockers")
        if host["expected_host_identity_sha256"] is not None:
            raise RefusalError("HOLD manifest must not pin production host identity")
        if collector["sha256"] is not None or collector["size"] is not None:
            raise RefusalError("HOLD manifest must not pin collector bytes")
        if tools_raw or blobs_raw or actions["processes"] or actions["scheduled_tasks"]:
            raise RefusalError(
                "HOLD manifest must not contain deployment hashes/actions"
            )
        return

    if pending:
        raise RefusalError("ready manifest cannot contain pending blockers")
    if not SHA256_RE.fullmatch(str(host["expected_host_identity_sha256"])):
        raise RefusalError("ready manifest requires expected host identity sha256")
    if not isinstance(host["expected_collector_sid"], str) or not SID_RE.fullmatch(
        host["expected_collector_sid"]
    ):
        raise RefusalError("ready manifest requires an exact collector SID")
    if (
        not SHA256_RE.fullmatch(str(collector["sha256"]))
        or type(collector["size"]) is not int
        or collector["size"] <= 0
    ):
        raise RefusalError("ready manifest requires collector hash and size")

    tools = _validate_toolchain_definitions(tools_raw)
    blobs = _validate_runtime_blob_definitions(blobs_raw)
    if "git" not in tools:
        raise RefusalError("ready manifest must pin the git executable")
    if "python-gate" not in tools:
        raise RefusalError("ready manifest must pin the Python gate interpreter")
    if collector["powershell_toolchain_id"] not in tools:
        raise RefusalError("collector PowerShell toolchain is not pinned")
    if not actions["processes"] or not actions["scheduled_tasks"]:
        raise RefusalError(
            "ready manifest requires process and Scheduled Task inventories"
        )
    _validate_actions(actions, tools=tools, blobs=blobs, canonical=canonical)


def _validate_toolchain_definitions(
    definitions: list[object],
) -> dict[str, Mapping[str, object]]:
    result: dict[str, Mapping[str, object]] = {}
    for raw in definitions:
        item = _as_mapping(raw, "toolchain entry")
        _require_exact_keys(item, {"id", "path", "sha256", "size"}, "toolchain entry")
        item_id = _entity_id(item["id"], "toolchain id")
        if item_id in result:
            raise RefusalError(f"duplicate toolchain id: {item_id}")
        _absolute_path(item["path"], f"toolchain {item_id} path")
        if not SHA256_RE.fullmatch(str(item["sha256"])):
            raise RefusalError(f"toolchain {item_id} sha256 is invalid")
        if type(item["size"]) is not int or item["size"] <= 0:
            raise RefusalError(f"toolchain {item_id} size is invalid")
        result[item_id] = item
    return result


def _validate_runtime_blob_definitions(
    definitions: list[object],
) -> dict[str, Mapping[str, object]]:
    result: dict[str, Mapping[str, object]] = {}
    runtime_paths: set[str] = set()
    for raw in definitions:
        item = _as_mapping(raw, "runtime blob")
        _require_exact_keys(
            item,
            {
                "id",
                "source_path",
                "runtime_path",
                "sha256",
                "size",
                "dependency_ids",
            },
            "runtime blob",
        )
        item_id = _entity_id(item["id"], "runtime blob id")
        if item_id in result:
            raise RefusalError(f"duplicate runtime blob id: {item_id}")
        _strict_git_path(item["source_path"])
        runtime_path = str(
            _strict_relative_path(
                item["runtime_path"], f"runtime blob {item_id} runtime_path"
            )
        ).casefold()
        if runtime_path in runtime_paths:
            raise RefusalError(f"duplicate runtime path: {runtime_path}")
        runtime_paths.add(runtime_path)
        if not SHA256_RE.fullmatch(str(item["sha256"])):
            raise RefusalError(f"runtime blob {item_id} sha256 is invalid")
        if type(item["size"]) is not int or item["size"] <= 0:
            raise RefusalError(f"runtime blob {item_id} size is invalid")
        dependencies = _as_list(
            item["dependency_ids"], f"runtime blob {item_id} dependency_ids"
        )
        if dependencies != sorted(set(dependencies)) or not all(
            isinstance(value, str) and ENTITY_ID_RE.fullmatch(value)
            for value in dependencies
        ):
            raise RefusalError(
                f"runtime blob {item_id} dependency_ids must be sorted and unique"
            )
        result[item_id] = item
    for item_id, item in result.items():
        for dependency in item["dependency_ids"]:
            if dependency not in result:
                raise RefusalError(
                    f"runtime blob {item_id} has unknown dependency {dependency}"
                )
    for item_id in result:
        _transitive_blob_ids(item_id, result)
    return result


def _validate_actions(
    actions: Mapping[str, object],
    *,
    tools: Mapping[str, Mapping[str, object]],
    blobs: Mapping[str, Mapping[str, object]],
    canonical: Mapping[str, object],
) -> None:
    process_keys = {
        "id",
        "required_count",
        "command_tokens",
        "command_sha256",
        "executable_toolchain_id",
        "toolchain_ids",
        "entrypoint_blob_id",
        "dependency_blob_ids",
        "closure_sha256",
        "owner_sid",
    }
    task_keys = {
        "id",
        "task_path",
        "task_name",
        "enabled",
        "principal_sid",
        "run_level",
        "working_directory",
        "action_tokens",
        "action_sha256",
        "executable_toolchain_id",
        "toolchain_ids",
        "entrypoint_blob_id",
        "dependency_blob_ids",
        "closure_sha256",
        "definition_sha256",
    }
    seen_ids: set[str] = set()
    seen_commands: set[str] = set()
    seen_tasks: set[tuple[str, str]] = set()
    action_groups = (
        (
            "process",
            _as_list(actions["processes"], "process actions"),
            process_keys,
            "command_tokens",
            "command_sha256",
        ),
        (
            "scheduled task",
            _as_list(actions["scheduled_tasks"], "task actions"),
            task_keys,
            "action_tokens",
            "action_sha256",
        ),
    )
    for kind, raw_entries, required_keys, token_key, digest_key in action_groups:
        for raw in raw_entries:
            item = _as_mapping(raw, f"{kind} action")
            _require_exact_keys(item, required_keys, f"{kind} action")
            item_id = _entity_id(item["id"], f"{kind} action id")
            if item_id in seen_ids:
                raise RefusalError(f"duplicate action id: {item_id}")
            seen_ids.add(item_id)
            tokens = _valid_tokens(item[token_key], f"{kind} {item_id} tokens")
            digest = command_digest(tokens)
            if item[digest_key] != digest:
                raise RefusalError(f"{kind} {item_id} command digest is invalid")
            if kind == "process":
                if type(item["required_count"]) is not int or item[
                    "required_count"
                ] < 1:
                    raise RefusalError("process required_count must be positive")
                if digest in seen_commands:
                    raise RefusalError("process commands must be unique")
                seen_commands.add(digest)
                if not isinstance(item["owner_sid"], str) or not SID_RE.fullmatch(
                    item["owner_sid"]
                ):
                    raise RefusalError("process owner SID must be exact")
            else:
                if not isinstance(item["enabled"], bool):
                    raise RefusalError("task enabled must be boolean")
                if not str(item["task_path"]).startswith("\\"):
                    raise RefusalError("task_path must start with a backslash")
                if not item["task_name"]:
                    raise RefusalError("task_name is required")
                key = _task_key(str(item["task_path"]), str(item["task_name"]))
                if key in seen_tasks:
                    raise RefusalError(f"duplicate Scheduled Task: {key}")
                seen_tasks.add(key)
                if not SID_RE.fullmatch(str(item["principal_sid"])):
                    raise RefusalError("task principal SID is invalid")
                working_directory = _absolute_path(
                    item["working_directory"], "task working_directory"
                )
                if not _same_path(
                    working_directory,
                    _absolute_path(canonical["runtime_root"], "runtime_root"),
                ):
                    raise RefusalError(
                        f"scheduled task {item_id} working directory is not "
                        "the canonical runtime root"
                    )
                if not SHA256_RE.fullmatch(str(item["definition_sha256"])):
                    raise RefusalError("task definition sha256 is invalid")

            executable_tool = str(item["executable_toolchain_id"])
            tool_ids = _as_list(item["toolchain_ids"], f"{kind} toolchain_ids")
            if tool_ids != sorted(set(tool_ids)) or executable_tool not in tool_ids:
                raise RefusalError(
                    f"{kind} {item_id} toolchain_ids must be sorted and include "
                    "executable"
                )
            if any(tool_id not in tools for tool_id in tool_ids):
                raise RefusalError(f"{kind} {item_id} references unknown toolchain")
            if not _same_path(
                _absolute_path(tokens[0], f"{kind} executable token"),
                _absolute_path(tools[executable_tool]["path"], "toolchain path"),
            ):
                raise RefusalError(f"{kind} {item_id} executable is not pinned")

            entrypoint = str(item["entrypoint_blob_id"])
            if entrypoint not in blobs:
                raise RefusalError(f"{kind} {item_id} entrypoint is not pinned")
            declared_blobs = _as_list(
                item["dependency_blob_ids"], f"{kind} dependency_blob_ids"
            )
            required_blobs = sorted(_transitive_blob_ids(entrypoint, blobs))
            if declared_blobs != required_blobs:
                raise RefusalError(
                    f"{kind} {item_id} dependency closure mismatch: "
                    f"declared={declared_blobs}; required={required_blobs}"
                )
            selected_tools = [tools[tool_id] for tool_id in tool_ids]
            selected_blobs = [blobs[blob_id] for blob_id in declared_blobs]
            expected_closure = dependency_closure_digest(
                command_tokens=tokens,
                toolchain=selected_tools,
                runtime_blobs=selected_blobs,
            )
            if item["closure_sha256"] != expected_closure:
                raise RefusalError(f"{kind} {item_id} closure digest is invalid")
            _validate_action_path_coverage(
                tokens,
                entrypoint_blob_id=entrypoint,
                blob_ids=declared_blobs,
                blobs=blobs,
                tool_paths=[Path(str(tools[tool_id]["path"])) for tool_id in tool_ids],
                canonical=canonical,
                label=f"{kind} {item_id}",
            )


def _validate_action_path_coverage(
    tokens: Sequence[str],
    *,
    entrypoint_blob_id: str,
    blob_ids: Sequence[str],
    blobs: Mapping[str, Mapping[str, object]],
    tool_paths: Sequence[Path],
    canonical: Mapping[str, object],
    label: str,
) -> None:
    _reject_dynamic_wrapper(tokens, label=label)
    runtime_root = _absolute_path(canonical["runtime_root"], "runtime_root")
    source_root = _absolute_path(canonical["source_root"], "source_root")
    declared_runtime = {
        _path_key(
            _join_under_root(
                runtime_root,
                _strict_relative_path(
                    blobs[blob_id]["runtime_path"], "runtime path"
                ),
                label="declared runtime dependency",
            )
        )
        for blob_id in blob_ids
    }
    declared_source = {
        _path_key(
            _join_under_root(
                source_root,
                PurePosixPath(_strict_git_path(blobs[blob_id]["source_path"])),
                label="declared source dependency",
            )
        )
        for blob_id in blob_ids
    }
    entrypoint_runtime = _path_key(
        _join_under_root(
            runtime_root,
            _strict_relative_path(
                blobs[entrypoint_blob_id]["runtime_path"],
                "entrypoint runtime path",
            ),
            label="entrypoint runtime path",
        )
    )
    token_path_keys: set[str] = set()
    for token in tokens[1:]:
        path_value = _path_value_from_token(token)
        if path_value is None:
            continue
        candidate = Path(path_value)
        if not candidate.is_absolute():
            raise RefusalError(
                f"{label} contains a relative path-bearing token: {token}"
            )
        key = _path_key(candidate)
        token_path_keys.add(key)
        if _is_within(candidate, runtime_root) and key not in (
            declared_runtime | {_path_key(runtime_root)}
        ):
            raise RefusalError(
                f"{label} contains an undeclared runtime dependency: {token}"
            )
        if _is_within(candidate, source_root) and key not in (
            declared_source | {_path_key(source_root)}
        ):
            raise RefusalError(
                f"{label} contains an undeclared source dependency: {token}"
            )
        if (
            not _is_within(candidate, runtime_root)
            and not _is_within(candidate, source_root)
            and not any(_same_path(candidate, tool) for tool in tool_paths)
        ):
            raise RefusalError(
                f"{label} contains an unpinned external path input: {token}"
            )
    if entrypoint_runtime not in token_path_keys:
        raise RefusalError(f"{label} command does not name its pinned entrypoint")

    runtime_options = {
        index
        for index, token in enumerate(tokens[:-1])
        if token.casefold() in {"-runtimeroot", "--runtime-root"}
    }
    if len(runtime_options) != 1:
        raise RefusalError(f"{label} must declare exactly one RuntimeRoot argument")
    runtime_index = runtime_options.pop()
    if not _same_path(
        _absolute_path(tokens[runtime_index + 1], f"{label} RuntimeRoot"),
        runtime_root,
    ):
        raise RefusalError(f"{label} RuntimeRoot is not canonical")


def _looks_path_bearing(token: str) -> bool:
    return _path_value_from_token(token) is not None


def _path_value_from_token(token: str) -> str | None:
    if not token:
        return None
    value = token
    if token.startswith(("-", "/")) and "=" in token:
        value = token.split("=", 1)[1]
    else:
        option_colon = re.match(r"^[-/][^:]+:([A-Za-z]:[\\/].+)$", token)
        if option_colon:
            value = option_colon.group(1)
    windows = PureWindowsPath(value)
    suffixes = {
        ".ps1",
        ".psm1",
        ".psd1",
        ".py",
        ".pyw",
        ".cmd",
        ".bat",
        ".exe",
        ".dll",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
        ".config",
    }
    path_like = bool(
        "/" in value
        or "\\" in value
        or windows.drive
        or windows.suffix.casefold() in suffixes
        or value.startswith(".")
    )
    if not path_like:
        return None
    if value == token and token.startswith("-"):
        return None
    return value


def _transitive_blob_ids(
    entrypoint: str,
    blobs: Mapping[str, Mapping[str, object]],
    *,
    visiting: set[str] | None = None,
) -> set[str]:
    active = set() if visiting is None else set(visiting)
    if entrypoint in active:
        raise RefusalError(f"runtime dependency cycle at {entrypoint}")
    active.add(entrypoint)
    result = {entrypoint}
    for dependency in _as_list(
        blobs[entrypoint]["dependency_ids"],
        f"runtime blob {entrypoint} dependencies",
    ):
        result.update(
            _transitive_blob_ids(str(dependency), blobs, visiting=active)
        )
    return result


def _git_environment() -> dict[str, str]:
    """Return the minimal environment shared by every Git subprocess."""

    allowed = ("SystemRoot", "WINDIR", "COMSPEC", "TEMP", "TMP")
    environment = {
        key: value for key in allowed if (value := os.environ.get(key)) is not None
    }
    environment.update(
        {
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_PAGER": "cat",
            "GIT_TERMINAL_PROMPT": "0",
            "LANG": "C",
            "LC_ALL": "C",
        }
    )
    return environment


def _collector_environment() -> dict[str, str]:
    allowed = ("SystemRoot", "SystemDrive", "WINDIR", "TEMP", "TMP")
    return {
        key: value for key in allowed if (value := os.environ.get(key)) is not None
    }


def _git(
    repo: Path,
    *args: str,
    git_executable: Path,
    check: bool = True,
) -> GitResult:
    config_path = Path(repo) / ".git" / "config"
    config_before = _preflight_git_config(config_path)
    _reject_local_git_overrides(Path(repo) / ".git")
    completed = subprocess.run(
        [
            str(git_executable),
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.untrackedCache=false",
            "-c",
            "core.autocrlf=true" if os.name == "nt" else "core.autocrlf=false",
            "-c",
            f"core.hooksPath={os.devnull}",
            "-C",
            str(repo),
            *args,
        ],
        check=False,
        capture_output=True,
        env=_git_environment(),
    )
    config_after = _preflight_git_config(config_path)
    _reject_local_git_overrides(Path(repo) / ".git")
    if config_after.payload != config_before.payload:
        raise RefusalError("Git local config changed while Git was running")
    result = GitResult(completed.returncode, completed.stdout, completed.stderr)
    if check and result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise AuditError(
            f"canonical git {' '.join(args)} failed with "
            f"{result.returncode}: {stderr}"
        )
    return result


def _git_blob(
    repo: Path,
    expected_commit: str,
    source_path: str,
    *,
    git_executable: Path,
) -> bytes:
    result = _git(
        repo,
        "cat-file",
        "blob",
        f"{expected_commit}:{source_path}",
        git_executable=git_executable,
        check=False,
    )
    if result.returncode != 0:
        raise AuditError(
            f"Git object does not exist: {expected_commit}:{source_path}"
        )
    return result.stdout


def _read_ordinary_file(path: Path, *, label: str) -> bytes:
    unresolved = path.absolute()
    _reject_reparse_components(unresolved, label=label)
    candidate = unresolved.resolve(strict=True)
    before = candidate.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise AuditError(f"{label} is not an ordinary file: {candidate}")
    descriptor = os.open(candidate, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    try:
        opened = os.fstat(descriptor)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if _stat_identity(before) != _stat_identity(opened) or _stat_identity(
        opened
    ) != _stat_identity(after):
        raise AuditError(f"{label} changed while it was read: {candidate}")
    return b"".join(chunks)


def _reject_reparse_components(path: Path, *, label: str) -> None:
    resolved = path.absolute()
    anchor = Path(resolved.anchor)
    current = anchor
    parts = resolved.parts[1:] if resolved.anchor else resolved.parts
    for part in parts:
        current = current / part
        details = current.lstat()
        if _stat_is_reparse(details):
            raise AuditError(f"{label} contains a link/reparse point: {current}")


def _stat_is_reparse(details: os.stat_result) -> bool:
    attributes = int(getattr(details, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return stat.S_ISLNK(details.st_mode) or bool(attributes & reparse_flag)


def _reject_git_metadata_reparse(common_git: Path) -> None:
    """Reject links/reparse points in every Git metadata tree we consume."""

    required_files = ("HEAD", "config", "index")
    required_trees = ("refs", "objects")
    for name in required_files:
        candidate = common_git / name
        if not os.path.lexists(candidate):
            raise RefusalError(f"required Git metadata file is missing: {candidate}")
        details = candidate.lstat()
        if _stat_is_reparse(details) or not stat.S_ISREG(details.st_mode):
            raise RefusalError(
                f"Git metadata must be an ordinary physical file: {candidate}"
            )
    for name in required_trees:
        root = common_git / name
        if not os.path.lexists(root):
            raise RefusalError(f"required Git metadata tree is missing: {root}")
        details = root.lstat()
        if _stat_is_reparse(details) or not stat.S_ISDIR(details.st_mode):
            raise RefusalError(
                f"Git metadata tree contains a link/reparse point: {root}"
            )

    # Walk the entire physical metadata directory.  This also covers optional
    # packed refs, split-index sharedindex files, commit graphs, multi-pack
    # indexes, config.worktree, and nested submodule/worktree metadata.
    pending = [common_git]
    while pending:
        directory = pending.pop()
        details = directory.lstat()
        if _stat_is_reparse(details) or not stat.S_ISDIR(details.st_mode):
            raise RefusalError(
                f"Git metadata tree contains a link/reparse point: {directory}"
            )
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            raise AuditError(f"cannot enumerate Git metadata: {directory}") from exc
        for entry in entries:
            entry_path = Path(entry.path)
            entry_stat = entry.stat(follow_symlinks=False)
            if _stat_is_reparse(entry_stat):
                raise RefusalError(
                    "Git metadata tree contains a link/reparse point: "
                    f"{entry_path}"
                )
            if stat.S_ISDIR(entry_stat.st_mode):
                pending.append(entry_path)
            elif not stat.S_ISREG(entry_stat.st_mode):
                raise RefusalError(
                    f"Git metadata contains a non-ordinary entry: {entry_path}"
                )


def _preflight_git_config(config_path: Path) -> LocalGitConfig:
    """Strictly parse/allowlist local config before the first Git subprocess."""

    payload = _read_ordinary_file(config_path, label="Git local config")
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise RefusalError("Git local config must be strict UTF-8") from exc
    if text.startswith("\ufeff") or "\x00" in text:
        raise RefusalError("Git local config contains a BOM or NUL")

    section: tuple[str, str | None] | None = None
    entries: dict[str, str] = {}
    section_re = re.compile(
        r'^\[([A-Za-z][A-Za-z0-9-]*)(?:\s+"([A-Za-z0-9._/-]+)")?\]$'
    )
    assignment_re = re.compile(r"^([A-Za-z][A-Za-z0-9-]*)\s*=\s*(.*?)\s*$")
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if raw_line.rstrip().endswith("\\"):
            raise RefusalError(
                f"Git local config continuation is forbidden at line {line_number}"
            )
        section_match = section_re.fullmatch(line)
        if section_match:
            section = (
                section_match.group(1).casefold(),
                section_match.group(2),
            )
            continue
        if section is None:
            raise RefusalError(
                f"Git local config has a key outside a section at line {line_number}"
            )
        assignment = assignment_re.fullmatch(line)
        if not assignment:
            raise RefusalError(
                f"Git local config syntax is not allowlisted at line {line_number}"
            )
        key_name = assignment.group(1).casefold()
        value = _strict_git_config_value(assignment.group(2), line_number)
        section_name, subsection = section
        full_key = section_name
        if subsection is not None:
            # Git section and variable names are case-insensitive, but remote
            # and branch subsection names are case-sensitive identities.
            full_key += f".{subsection}"
        full_key += f".{key_name}"
        if full_key in entries:
            raise RefusalError(f"duplicate Git local config key: {full_key}")
        _validate_allowed_git_config_entry(full_key, value)
        entries[full_key] = value

    required = {
        "core.repositoryformatversion",
        "core.filemode",
        "core.bare",
        "core.logallrefupdates",
        "remote.origin.url",
        "remote.origin.fetch",
    }
    missing = required - set(entries)
    if missing:
        raise RefusalError(
            f"Git local config lacks required inert keys: {sorted(missing)}"
        )
    branch_keys = {"branch.main.remote", "branch.main.merge"}
    if set(entries) & branch_keys not in (set(), branch_keys):
        raise RefusalError("Git main branch tracking config must be complete")
    return LocalGitConfig(payload=payload, entries=tuple(sorted(entries.items())))


def _strict_git_config_value(raw_value: str, line_number: int) -> str:
    value = raw_value.strip()
    if not value:
        raise RefusalError(f"empty Git config value at line {line_number}")
    if value.startswith('"') or value.endswith('"'):
        if not (value.startswith('"') and value.endswith('"')):
            raise RefusalError(f"unbalanced Git config quote at line {line_number}")
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise RefusalError(
                f"unsupported Git config quoting at line {line_number}"
            ) from exc
        if not isinstance(decoded, str):
            raise RefusalError(f"Git config value must be text at line {line_number}")
        value = decoded
    if not value or any(ord(character) < 32 for character in value):
        raise RefusalError(f"unsafe Git config value at line {line_number}")
    if "#" in value or ";" in value:
        raise RefusalError(
            f"inline Git config comments/separators are forbidden at line {line_number}"
        )
    return value


def _validate_allowed_git_config_entry(full_key: str, value: str) -> None:
    boolean = {"true", "false"}
    validators: dict[str, Callable[[str], bool]] = {
        "core.repositoryformatversion": lambda item: item == "0",
        "core.filemode": lambda item: item.casefold() in boolean,
        "core.bare": lambda item: item.casefold() == "false",
        "core.logallrefupdates": lambda item: item.casefold() == "true",
        "core.ignorecase": lambda item: item.casefold() in boolean,
        "core.symlinks": lambda item: item.casefold() in boolean,
        "core.protectntfs": lambda item: item.casefold() == "true",
        "core.protecthfs": lambda item: item.casefold() == "true",
        "core.longpaths": lambda item: item.casefold() == "true",
        "remote.origin.url": lambda item: bool(
            re.fullmatch(r"https://[A-Za-z0-9.-]+(?::\d+)?/[A-Za-z0-9._~%/@+-]+", item)
        ),
        "remote.origin.fetch": lambda item: item
        == "+refs/heads/*:refs/remotes/origin/*",
        "branch.main.remote": lambda item: item == "origin",
        "branch.main.merge": lambda item: item == "refs/heads/main",
        "user.name": lambda item: 0 < len(item) <= 256,
        "user.email": lambda item: bool(
            re.fullmatch(r"[^\s@]+@[^\s@]+", item)
        ),
    }
    validator = validators.get(full_key)
    if validator is None:
        dangerous_prefixes = (
            "alias.",
            "diff.",
            "filter.",
            "fsck.",
            "include.",
            "includeif.",
            "merge.",
        )
        dangerous_exact = {
            "core.attributesfile",
            "core.excludesfile",
            "core.hookspath",
            "core.sshcommand",
            "diff.external",
            "extensions.partialclone",
            "extensions.worktreeconfig",
            "fsck.skiplist",
        }
        classification = (
            "external-path/command-bearing"
            if full_key in dangerous_exact
            or full_key.startswith(dangerous_prefixes)
            or full_key.endswith((".promisor", ".partialclonefilter", ".driver"))
            else "unknown unsafe"
        )
        raise RefusalError(
            f"Git local config {classification} key is forbidden: {full_key}"
        )
    if not validator(value):
        raise RefusalError(
            f"Git local config value is not allowlisted: {full_key}={value!r}"
        )


def _reject_local_git_overrides(common_git: Path) -> None:
    """Reject local ignore/attribute/object indirections before invoking Git."""

    for relative, label in (
        (("info", "exclude"), "Git info exclude"),
        (("info", "attributes"), "Git info attributes"),
    ):
        candidate = common_git.joinpath(*relative)
        if not os.path.lexists(candidate):
            continue
        payload = _read_ordinary_file(candidate, label=label)
        try:
            text = payload.decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise RefusalError(f"{label} must be strict UTF-8") from exc
        active = [
            line
            for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        if active:
            raise RefusalError(f"{label} patterns are forbidden")

    forbidden = (
        common_git / "info" / "grafts",
        common_git / "objects" / "info" / "alternates",
        common_git / "objects" / "info" / "http-alternates",
        common_git / "info" / "sparse-checkout",
        common_git / "shallow",
    )
    for candidate in forbidden:
        if os.path.lexists(candidate):
            raise RefusalError(f"Git local indirection is forbidden: {candidate}")
    promisor_packs = list((common_git / "objects" / "pack").glob("*.promisor"))
    if promisor_packs:
        raise RefusalError(f"promisor object packs are forbidden: {promisor_packs[0]}")


def _verify_git_config_unchanged(common_git: Path, expected: bytes) -> None:
    observed = _preflight_git_config(common_git / "config")
    if observed.payload != expected:
        raise RefusalError("Git local config changed during repository audit")


def _audit_index_worktree_bytes(repo: Path, *, git_executable: Path) -> None:
    """Compare physical tracked bytes to stage-0 blobs without clean filters."""

    raw_entries = _git(
        repo,
        "ls-files",
        "--stage",
        "-z",
        git_executable=git_executable,
    ).stdout.split(b"\0")
    seen: set[str] = set()
    for raw_entry in raw_entries:
        if not raw_entry:
            continue
        try:
            metadata, raw_path = raw_entry.split(b"\t", 1)
            mode, object_id, stage = metadata.decode("ascii").split(" ")
            source_path = raw_path.decode("utf-8", errors="strict")
        except (ValueError, UnicodeError) as exc:
            raise RefusalError("Git index contains an unparseable entry") from exc
        if stage != "0" or mode not in {"100644", "100755"}:
            raise RefusalError(
                f"Git index mode/stage is not an ordinary stage-0 file: "
                f"{mode}/{stage} {source_path}"
            )
        if not FULL_COMMIT_RE.fullmatch(object_id):
            raise RefusalError(f"Git index object id is not exact SHA-1: {source_path}")
        strict_path = _strict_relative_path(source_path, "Git index path")
        normalized_path = strict_path.as_posix().casefold()
        if normalized_path in seen:
            raise RefusalError(f"duplicate/case-colliding Git index path: {source_path}")
        seen.add(normalized_path)
        payload = _read_ordinary_file(
            _join_under_root(repo, strict_path, label="tracked worktree path"),
            label=f"tracked worktree file {source_path}",
        )
        header = f"blob {len(payload)}\0".encode("ascii")
        physical_object_id = hashlib.sha1(
            header + payload, usedforsecurity=False
        ).hexdigest()
        if physical_object_id != object_id:
            raise RefusalError(
                f"tracked worktree bytes differ from index blob: {source_path}"
            )


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int]:
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)


def _definitions_by_id(
    definitions: list[object], label: str
) -> dict[str, Mapping[str, object]]:
    result: dict[str, Mapping[str, object]] = {}
    for raw in definitions:
        item = _as_mapping(raw, label)
        item_id = str(item["id"])
        if item_id in result:
            raise RefusalError(f"duplicate {label} id: {item_id}")
        result[item_id] = item
    return result


def _valid_tokens(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or not value or not all(
        isinstance(token, str) and token and "\x00" not in token for token in value
    ):
        raise RefusalError(f"{label} must be a non-empty string list")
    return list(value)


def _entity_id(value: object, label: str) -> str:
    if not isinstance(value, str) or not ENTITY_ID_RE.fullmatch(value):
        raise RefusalError(f"{label} is invalid")
    return value


def _strict_git_path(value: object) -> str:
    path = _strict_relative_path(value, "Git path")
    return path.as_posix()


def _strict_relative_path(value: object, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise RefusalError(f"{label} must be a strict forward-slash relative path")
    windows = PureWindowsPath(value)
    raw_parts = value.split("/")
    if (
        value.startswith("/")
        or windows.drive
        or windows.root
        or windows.anchor
        or ":" in value
        or any(part in {"", ".", ".."} for part in raw_parts)
    ):
        raise RefusalError(f"{label} contains unsafe path components")
    reserved = {
        "con",
        "prn",
        "aux",
        "nul",
        "clock$",
        "conin$",
        "conout$",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }
    for part in raw_parts:
        if part.endswith((".", " ")) or any(ord(character) < 32 for character in part):
            raise RefusalError(f"{label} contains a Windows-unsafe path component")
        device_stem = part.split(".", 1)[0].casefold()
        if device_stem in reserved:
            raise RefusalError(f"{label} contains a reserved Windows device name")
    path = PurePosixPath(*raw_parts)
    return path


def _join_under_root(root: Path, relative: PurePosixPath, *, label: str) -> Path:
    candidate = root.joinpath(*relative.parts)
    if not _is_within(candidate, root):
        raise RefusalError(f"{label} escapes its canonical root")
    return candidate


def _absolute_path(value: object, label: str) -> Path:
    if not isinstance(value, (str, os.PathLike)):
        raise RefusalError(f"{label} must be an absolute path")
    path = Path(value)
    if not path.is_absolute():
        raise RefusalError(f"{label} must be an absolute path: {value}")
    return Path(os.path.abspath(str(path)))


def _absolute_existing_file(value: object, label: str) -> Path:
    if not isinstance(value, (str, os.PathLike)):
        raise RefusalError(f"{label} must be an absolute path")
    path = Path(value)
    if not path.is_absolute():
        raise RefusalError(f"{label} must be an absolute path: {value}")
    if not path.is_file():
        raise AuditError(f"{label} is unavailable: {path}")
    _reject_reparse_components(path.absolute(), label=label)
    return path.resolve(strict=True)


def _absolute_existing_dir(value: object, label: str) -> Path:
    if not isinstance(value, (str, os.PathLike)):
        raise RefusalError(f"{label} must be an absolute path")
    path = Path(value)
    if not path.is_absolute():
        raise RefusalError(f"{label} must be an absolute path: {value}")
    if not path.is_dir():
        raise AuditError(f"{label} is unavailable: {path}")
    _reject_reparse_components(path.absolute(), label=label)
    return path.resolve(strict=True)


def _decode_json_object(payload: bytes, label: str) -> dict[str, object]:
    try:
        value = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=_object_without_duplicate_keys,
        )
    except (UnicodeError, json.JSONDecodeError, RefusalError) as exc:
        raise RefusalError(f"{label} is not strict UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise RefusalError(f"{label} must be a JSON object")
    return value


def _object_without_duplicate_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise RefusalError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _require_exact_keys(
    value: Mapping[str, object], expected: set[str], label: str
) -> None:
    missing = expected - set(value)
    unknown = set(value) - expected
    if missing or unknown:
        raise RefusalError(
            f"{label} keys mismatch: missing={sorted(missing)} "
            f"unknown={sorted(unknown)}"
        )


def _as_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise RefusalError(f"{label} must be an object")
    return value


def _as_list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise RefusalError(f"{label} must be a list")
    return value


def _parse_utc(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise RefusalError(f"{label} must be an ISO UTC string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RefusalError(f"{label} is invalid: {value}") from exc
    if parsed.tzinfo is None:
        raise RefusalError(f"{label} must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _same_path(left: Path, right: Path) -> bool:
    return _path_key(left) == _path_key(right)


def _path_key(value: Path) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(str(value))))


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def _task_key(task_path: str, task_name: str) -> tuple[str, str]:
    return (task_path.replace("/", "\\").casefold(), task_name.casefold())


def _block(blockers: list[dict[str, str]], code: str, detail: str) -> None:
    blockers.append({"code": code, "detail": detail})


if __name__ == "__main__":
    raise SystemExit(main())
