# SPDX-License-Identifier: BUSL-1.1
"""Read-only Git-lineage verification for MAGMA FAISS holdout states.

The pure holdout protocol deliberately accepts declared commit identifiers.
This adapter verifies the narrow fact that the candidate and every declared
stage commit exist in one local SHA-1 Git object graph, form the required
ancestry chain, and are contained by one pinned local ref.

The caller must provide an absolute Git executable path and its independently
expected SHA-256 digest.  The adapter binds those bytes but cannot establish
that the executable or its dynamically loaded runtime is trustworthy.  It does
not fetch, inspect the worktree, read evidence blobs, verify remote-ref
freshness, authenticate actors or timestamps, prove seed independence, or
enforce a single attempt.  Those boundaries remain explicit in the returned
projection.  In particular, local ref containment is not remote publication.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools import magma_faiss_holdout_protocol as protocol_contract
from waggledance.core.magma.canonical import canonical_json_bytes


GIT_LINEAGE_SCHEMA = "wd.magma.faiss_holdout_git_lineage_verification.v1"
GIT_COMMAND_TIMEOUT_SECONDS = 30
_PATH_TYPE = type(Path("."))

_FULL_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_LOCAL_REF = re.compile(
    r"^refs/(?:heads|remotes)/[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$"
)
_PROJECTION_KEYS = frozenset(
    {
        "schema_version",
        "protocol_digest",
        "candidate_commit",
        "verified_stage",
        "sequence",
        "verified_declared_commits",
        "final_state_digest",
        "local_ref",
        "local_ref_tip_commit",
        "git_executable_sha256",
        "relationship",
        "capability_boundary",
    }
)
_CAPABILITY_KEYS = frozenset(
    {
        "artifact_class",
        "local_git_commit_existence_verified",
        "git_ancestry_verified",
        "local_ref_containment_verified",
        "git_executable_digest_verified",
        "git_executable_trust_verified",
        "concurrent_git_executable_mutation_absence_verified",
        "atomic_repository_snapshot_verified",
        "concurrent_repository_mutation_absence_verified",
        "remote_publication_verified",
        "artifact_blob_digest_verified",
        "external_provenance_verified",
        "actor_identity_verified",
        "trusted_time_verified",
        "one_shot_enforced",
        "private_attempt_absence_verified",
        "seed_unbiased_verified",
        "holdout_pack_created",
        "holdout_evidence_gate_met",
        "runtime_threshold_selected",
        "runtime_configuration_written",
        "production_runtime_path_evaluated",
        "runtime_authority_granted",
        "candidate_mode_change_authorized",
        "cell_pruning_authorized",
        "production_promotion_gate_pass",
    }
)
_TRUE_CAPABILITIES = frozenset(
    {
        "local_git_commit_existence_verified",
        "git_ancestry_verified",
        "local_ref_containment_verified",
        "git_executable_digest_verified",
    }
)


class HoldoutGitLineageError(ValueError):
    """The declared holdout Git lineage cannot be verified."""


@dataclass(frozen=True)
class _GitResult:
    returncode: int
    stdout: bytes
    stderr: bytes


@dataclass(frozen=True)
class _GitRuntime:
    executable: Path
    executable_sha256: str


def _exact_dict(
    value: Any,
    expected_keys: frozenset[str],
    label: str,
) -> dict[str, Any]:
    if type(value) is not dict:
        raise HoldoutGitLineageError(f"{label}_must_be_exact_object")
    if any(type(key) is not str for key in value):
        raise HoldoutGitLineageError(f"{label}_keys_must_be_exact_strings")
    actual = frozenset(value)
    if actual != expected_keys:
        raise HoldoutGitLineageError(
            f"{label}_keys_mismatch:missing={sorted(expected_keys - actual)},"
            f"extra={sorted(actual - expected_keys)}"
        )
    return value


def _exact_list(value: Any, label: str) -> list[Any]:
    if type(value) is not list:
        raise HoldoutGitLineageError(f"{label}_must_be_exact_list")
    return value


def _exact_string(value: Any, label: str) -> str:
    if type(value) is not str or not value:
        raise HoldoutGitLineageError(f"{label}_must_be_nonempty_exact_string")
    return value


def _regex_string(value: Any, pattern: re.Pattern[str], label: str) -> str:
    result = _exact_string(value, label)
    if pattern.fullmatch(result) is None:
        raise HoldoutGitLineageError(f"{label}_invalid")
    return result


def _exact_int(value: Any, expected: int, label: str) -> None:
    if type(value) is not int or value != expected:
        raise HoldoutGitLineageError(f"{label}_must_equal_{expected}")


def _exact_bool(value: Any, expected: bool, label: str) -> None:
    if type(value) is not bool or value is not expected:
        raise HoldoutGitLineageError(
            f"{label}_must_be_{str(expected).lower()}"
        )


def _canonical_clone(value: Any, label: str) -> Any:
    try:
        return json.loads(canonical_json_bytes(value))
    except (TypeError, ValueError, OverflowError) as exc:
        raise HoldoutGitLineageError(f"{label}_is_not_strict_json") from exc


def _validate_local_ref(value: Any) -> str:
    ref = _regex_string(value, _LOCAL_REF, "local_ref")
    if (
        ".." in ref
        or "//" in ref
        or "@{" in ref
        or ref.endswith(("/", ".", ".lock"))
        or any(part.startswith(".") for part in ref.split("/"))
    ):
        raise HoldoutGitLineageError("local_ref_invalid")
    return ref


def _capability_boundary() -> dict[str, Any]:
    return {
        "artifact_class": "local_git_lineage_verification_only",
        **{
            key: key in _TRUE_CAPABILITIES
            for key in _CAPABILITY_KEYS - {"artifact_class"}
        },
    }


def _validate_capability_boundary(value: Any) -> None:
    boundary = _exact_dict(value, _CAPABILITY_KEYS, "capability_boundary")
    if _exact_string(
        boundary["artifact_class"], "capability_boundary_artifact_class"
    ) != "local_git_lineage_verification_only":
        raise HoldoutGitLineageError(
            "capability_boundary_artifact_class_mismatch"
        )
    for key in _CAPABILITY_KEYS - {"artifact_class"}:
        _exact_bool(
            boundary[key],
            key in _TRUE_CAPABILITIES,
            f"capability_boundary_{key}",
        )


def _validated_protocol(value: Any) -> dict[str, Any]:
    try:
        return protocol_contract.validate_preregistration(value)
    except protocol_contract.HoldoutProtocolError as exc:
        raise HoldoutGitLineageError("preregistration_invalid") from exc


def _validated_states(
    value: Any,
    *,
    protocol: dict[str, Any],
) -> list[dict[str, Any]]:
    raw_states = _exact_list(value, "states")
    if not raw_states or len(raw_states) > len(protocol_contract.STAGES):
        raise HoldoutGitLineageError("states_must_be_nonempty_stage_prefix")
    states: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_states):
        try:
            state = protocol_contract.validate_state(raw, protocol=protocol)
        except protocol_contract.HoldoutProtocolError as exc:
            raise HoldoutGitLineageError(f"state_{index}_invalid") from exc
        if state["sequence"] != index:
            raise HoldoutGitLineageError("states_must_form_exact_sequence")
        if state["stage"] != protocol_contract.STAGES[index]:
            raise HoldoutGitLineageError("states_must_form_exact_stage_prefix")
        if index == 0:
            if state["evidence"]["artifact_digest"] != state["protocol_digest"]:
                raise HoldoutGitLineageError(
                    "preregistration_artifact_digest_mismatch"
                )
        else:
            previous = states[index - 1]
            try:
                previous_digest = protocol_contract.state_digest(
                    previous,
                    protocol=protocol,
                )
            except protocol_contract.HoldoutProtocolError as exc:
                raise HoldoutGitLineageError("previous_state_invalid") from exc
            if state["previous_state_digest"] != previous_digest:
                raise HoldoutGitLineageError("state_digest_chain_mismatch")
            if state["declared_commit_chain"][:-1] != previous[
                "declared_commit_chain"
            ]:
                raise HoldoutGitLineageError("declared_commit_chain_forked")
        states.append(state)
    return states


def _git_executable_digest(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise HoldoutGitLineageError("git_executable_read_failed") from exc
    return f"sha256:{digest.hexdigest()}"


def _validated_git_runtime(
    executable: Any,
    expected_sha256: Any,
) -> _GitRuntime:
    expected = _regex_string(
        expected_sha256,
        _FULL_SHA256,
        "expected_git_executable_sha256",
    )
    if type(executable) is not _PATH_TYPE or not executable.is_absolute():
        raise HoldoutGitLineageError("git_executable_must_be_exact_absolute_path")
    try:
        metadata = executable.lstat()
        resolved = executable.resolve(strict=True)
    except OSError as exc:
        raise HoldoutGitLineageError("git_executable_resolution_failed") from exc
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    file_attributes = getattr(metadata, "st_file_attributes", 0)
    if (
        resolved != executable
        or not stat.S_ISREG(metadata.st_mode)
        or bool(file_attributes & reparse_flag)
    ):
        raise HoldoutGitLineageError(
            "git_executable_must_be_direct_regular_file"
        )
    actual = _git_executable_digest(resolved)
    if not hmac.compare_digest(actual, expected):
        raise HoldoutGitLineageError("git_executable_digest_mismatch")
    return _GitRuntime(
        executable=resolved,
        executable_sha256=actual,
    )


def _assert_git_runtime_unchanged(runtime: _GitRuntime) -> None:
    if not hmac.compare_digest(
        _git_executable_digest(runtime.executable),
        runtime.executable_sha256,
    ):
        raise HoldoutGitLineageError("git_executable_changed_during_verification")


def _run_git(
    runtime: _GitRuntime,
    repo: Path,
    *args: str,
    check: bool = True,
) -> _GitResult:
    environment = os.environ.copy()
    for name in tuple(environment):
        if name.upper().startswith("GIT_"):
            environment.pop(name, None)
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    environment["GIT_NO_LAZY_FETCH"] = "1"
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    environment["PATH"] = str(runtime.executable.parent)
    command = [
        str(runtime.executable),
        "--no-replace-objects",
        "-c",
        "core.commitGraph=false",
        "-C",
        str(repo),
        *args,
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=GIT_COMMAND_TIMEOUT_SECONDS,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HoldoutGitLineageError("git_command_failed") from exc
    result = _GitResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
    if check and result.returncode != 0:
        raise HoldoutGitLineageError("git_command_failed")
    return result


def _stdout_text(result: _GitResult, label: str) -> str:
    try:
        return result.stdout.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise HoldoutGitLineageError(f"{label}_not_ascii") from exc


def _resolve_commit(
    runtime: _GitRuntime,
    repo: Path,
    revision: str,
    label: str,
) -> str:
    result = _run_git(
        runtime,
        repo,
        "rev-parse",
        "--verify",
        "--end-of-options",
        f"{revision}^{{commit}}",
    )
    resolved = _stdout_text(result, label)
    return _regex_string(resolved, _GIT_COMMIT, label)


def _resolve_local_ref(
    runtime: _GitRuntime,
    repo: Path,
    ref: str,
) -> str:
    symbolic = _run_git(
        runtime,
        repo,
        "symbolic-ref",
        "--quiet",
        ref,
        check=False,
    )
    if symbolic.returncode == 0:
        raise HoldoutGitLineageError("local_ref_must_not_be_symbolic")
    if symbolic.returncode != 1:
        raise HoldoutGitLineageError("local_ref_symbolic_check_failed")
    result = _run_git(runtime, repo, "show-ref", "--verify", "--hash", ref)
    resolved = _stdout_text(result, "local_ref_tip_commit")
    raw_oid = _regex_string(resolved, _GIT_COMMIT, "local_ref_tip_commit")
    if (
        _resolve_commit(
            runtime,
            repo,
            raw_oid,
            "local_ref_peeled_commit",
        )
        != raw_oid
    ):
        raise HoldoutGitLineageError(
            "local_ref_tip_must_point_directly_to_commit"
        )
    return raw_oid


def _is_ancestor(
    runtime: _GitRuntime,
    repo: Path,
    ancestor: str,
    descendant: str,
) -> bool:
    result = _run_git(
        runtime,
        repo,
        "merge-base",
        "--is-ancestor",
        ancestor,
        descendant,
        check=False,
    )
    if result.returncode not in {0, 1}:
        raise HoldoutGitLineageError("git_ancestry_check_failed")
    return result.returncode == 0


def _verified_repo_root(runtime: _GitRuntime, value: Any) -> Path:
    if type(value) is not _PATH_TYPE:
        raise HoldoutGitLineageError("repo_must_be_exact_path")
    try:
        supplied = value.resolve(strict=True)
    except OSError as exc:
        raise HoldoutGitLineageError("repo_resolution_failed") from exc
    root_text = _stdout_text(
        _run_git(runtime, supplied, "rev-parse", "--show-toplevel"),
        "git_root",
    )
    try:
        root = Path(root_text).resolve(strict=True)
    except OSError as exc:
        raise HoldoutGitLineageError("git_root_resolution_failed") from exc
    if supplied != root:
        raise HoldoutGitLineageError("repo_must_equal_git_toplevel")
    object_format = _stdout_text(
        _run_git(runtime, root, "rev-parse", "--show-object-format"),
        "git_object_format",
    )
    if object_format != "sha1":
        raise HoldoutGitLineageError("git_object_format_must_be_sha1")
    git_dir_text = _stdout_text(
        _run_git(runtime, root, "rev-parse", "--absolute-git-dir"),
        "git_dir",
    )
    try:
        git_dir = Path(git_dir_text).resolve(strict=True)
    except OSError as exc:
        raise HoldoutGitLineageError("git_dir_resolution_failed") from exc
    common_dir_text = _stdout_text(
        _run_git(runtime, root, "rev-parse", "--git-common-dir"),
        "git_common_dir",
    )
    common_dir_path = Path(common_dir_text)
    if not common_dir_path.is_absolute():
        common_dir_path = root / common_dir_path
    try:
        common_dir = common_dir_path.resolve(strict=True)
    except OSError as exc:
        raise HoldoutGitLineageError("git_common_dir_resolution_failed") from exc
    if not git_dir.is_dir() or not common_dir.is_dir():
        raise HoldoutGitLineageError("git_metadata_directory_invalid")
    forbidden_graph_overlays = (
        common_dir / "info" / "grafts",
        common_dir / "objects" / "info" / "alternates",
    )
    if any(os.path.lexists(path) for path in forbidden_graph_overlays):
        raise HoldoutGitLineageError("git_graph_overlay_not_allowed")
    return root


def verify_git_lineage(
    repo: Path,
    *,
    git_executable: Path,
    expected_git_executable_sha256: str,
    protocol: Any,
    states: Any,
    local_ref: str,
) -> dict[str, Any]:
    """Verify a complete state prefix against one pinned local Git ref."""

    runtime = _validated_git_runtime(
        git_executable,
        expected_git_executable_sha256,
    )
    validated_protocol = _validated_protocol(protocol)
    validated_states = _validated_states(states, protocol=validated_protocol)
    ref = _validate_local_ref(local_ref)
    root = _verified_repo_root(runtime, repo)
    local_ref_tip = _resolve_local_ref(runtime, root, ref)
    candidate_commit = validated_protocol["candidate_identity"][
        "candidate_commit"
    ]
    declared_commits = [
        state["evidence"]["declared_commit"] for state in validated_states
    ]
    ordered_commits = [candidate_commit, *declared_commits]
    for index, commit in enumerate(ordered_commits):
        if _resolve_commit(runtime, root, commit, f"commit_{index}") != commit:
            raise HoldoutGitLineageError("declared_commit_resolution_mismatch")
    for ancestor, descendant in zip(
        ordered_commits,
        ordered_commits[1:],
    ):
        if not _is_ancestor(runtime, root, ancestor, descendant):
            raise HoldoutGitLineageError("declared_commit_ancestry_mismatch")
    if not _is_ancestor(
        runtime,
        root,
        declared_commits[-1],
        local_ref_tip,
    ):
        raise HoldoutGitLineageError(
            "local_ref_does_not_contain_final_stage"
        )
    if _resolve_local_ref(runtime, root, ref) != local_ref_tip:
        raise HoldoutGitLineageError("local_ref_changed_during_verification")
    _assert_git_runtime_unchanged(runtime)
    final_state = validated_states[-1]
    try:
        final_digest = protocol_contract.state_digest(
            final_state,
            protocol=validated_protocol,
        )
    except protocol_contract.HoldoutProtocolError as exc:
        raise HoldoutGitLineageError("final_state_invalid") from exc
    projection = {
        "schema_version": GIT_LINEAGE_SCHEMA,
        "protocol_digest": protocol_contract.preregistration_digest(
            validated_protocol
        ),
        "candidate_commit": candidate_commit,
        "verified_stage": final_state["stage"],
        "sequence": final_state["sequence"],
        "verified_declared_commits": declared_commits,
        "final_state_digest": final_digest,
        "local_ref": ref,
        "local_ref_tip_commit": local_ref_tip,
        "git_executable_sha256": runtime.executable_sha256,
        "relationship": "final_stage_ancestor_of_pinned_local_ref",
        "capability_boundary": _capability_boundary(),
    }
    return _validate_projection_shape(projection)


def _validate_projection_shape(value: Any) -> dict[str, Any]:
    projection = _exact_dict(value, _PROJECTION_KEYS, "git_lineage_projection")
    if _exact_string(
        projection["schema_version"], "git_lineage_schema_version"
    ) != GIT_LINEAGE_SCHEMA:
        raise HoldoutGitLineageError("git_lineage_schema_mismatch")
    _regex_string(
        projection["protocol_digest"], _FULL_SHA256, "protocol_digest"
    )
    _regex_string(
        projection["candidate_commit"], _GIT_COMMIT, "candidate_commit"
    )
    stage = _exact_string(projection["verified_stage"], "verified_stage")
    if stage not in protocol_contract.STAGES:
        raise HoldoutGitLineageError("verified_stage_invalid")
    sequence = protocol_contract.STAGES.index(stage)
    _exact_int(projection["sequence"], sequence, "sequence")
    commits = _exact_list(
        projection["verified_declared_commits"],
        "verified_declared_commits",
    )
    if len(commits) != sequence + 1:
        raise HoldoutGitLineageError("verified_declared_commit_count_mismatch")
    for index, commit in enumerate(commits):
        _regex_string(commit, _GIT_COMMIT, f"verified_declared_commit_{index}")
    if len(set(commits)) != len(commits):
        raise HoldoutGitLineageError("verified_declared_commits_not_unique")
    _regex_string(
        projection["final_state_digest"],
        _FULL_SHA256,
        "final_state_digest",
    )
    _validate_local_ref(projection["local_ref"])
    _regex_string(
        projection["local_ref_tip_commit"],
        _GIT_COMMIT,
        "local_ref_tip_commit",
    )
    _regex_string(
        projection["git_executable_sha256"],
        _FULL_SHA256,
        "git_executable_sha256",
    )
    if _exact_string(
        projection["relationship"], "relationship"
    ) != "final_stage_ancestor_of_pinned_local_ref":
        raise HoldoutGitLineageError("relationship_mismatch")
    _validate_capability_boundary(projection["capability_boundary"])
    return _canonical_clone(projection, "git_lineage_projection")


def validate_git_lineage_projection(
    value: Any,
    *,
    repo: Path,
    git_executable: Path,
    expected_git_executable_sha256: str,
    protocol: Any,
    states: Any,
) -> dict[str, Any]:
    """Validate a projection by re-running the read-only Git checks."""

    observed = _validate_projection_shape(value)
    expected = verify_git_lineage(
        repo,
        git_executable=git_executable,
        expected_git_executable_sha256=expected_git_executable_sha256,
        protocol=protocol,
        states=states,
        local_ref=observed["local_ref"],
    )
    if not hmac.compare_digest(
        canonical_json_bytes(observed), canonical_json_bytes(expected)
    ):
        raise HoldoutGitLineageError("git_lineage_recomputation_mismatch")
    return observed


__all__ = [
    "GIT_LINEAGE_SCHEMA",
    "HoldoutGitLineageError",
    "validate_git_lineage_projection",
    "verify_git_lineage",
]
