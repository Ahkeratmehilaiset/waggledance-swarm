#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Write v3.12 Docker stable-policy evidence from repo and operator facts."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any, Mapping

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.operator_decision_pack import (
    DecisionPackError,
    _validate_pack,
    is_signed,
)


SCHEMA_VERSION = "waggledance.release_docker_policy.v2"
AUTH_SCHEMA_VERSION = "waggledance.operator_docker_stable_authorization.v2"
DOCKER_DECISION_PACK_CATEGORY = "docker_promotion"
DOCKER_DECISION_OPTIONS = {
    "ghcr_stable_only": {
        "move_latest": "no",
        "stable_promotion_authorized": True,
        "docker_promotion_deferred": False,
    },
    "ghcr_stable_and_latest": {
        "move_latest": "yes",
        "stable_promotion_authorized": True,
        "docker_promotion_deferred": False,
    },
    "defer_docker": {
        "move_latest": "no",
        "stable_promotion_authorized": False,
        "docker_promotion_deferred": True,
    },
}
REQUIRED_DOCKER_DECISION_OPTION = "ghcr_stable_only"
REQUIRED_DECISION_PACK_INVARIANTS = (
    "latest_move_is_operator_only",
    "agent_must_not_self_resolve",
)
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
DEFAULT_OUTPUT = (
    Path("docs")
    / "runs"
    / "release_soak_evidence"
    / "v3.12.0_docker_policy.json"
)
DEFAULT_TARGET_VERSION = "v3.12.0"


def _docker_decision_pack_id(target_version: object) -> str:
    if not isinstance(target_version, str):
        return ""
    slug = re.sub(r"[^a-z0-9]+", "-", target_version.lower()).strip("-")
    return f"docker-{slug}-stable-promotion" if slug else ""


def _docker_decision_pack_relative_path(target_version: str) -> Path:
    return (
        Path("docs")
        / "operator_inbox"
        / f"{_docker_decision_pack_id(target_version)}.yaml"
    )


def _sanitized_git_env() -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


CANONICAL_ENTRYPOINT = ["python", "-m", "waggledance.adapters.cli.start_runtime"]
CANONICAL_SCRIPT = "waggledance.adapters.cli.start_runtime:main"
REQUIRED_SOURCE_FILES = (
    ".github/workflows/release-docker-stable.yml",
    ".github/workflows/release-docker.yml",
    "Dockerfile",
    "docker-compose.yml",
    "pyproject.toml",
    "docs/deployment/DOCKER_QUICKSTART.md",
)
REVIEWED_WORKFLOW_HASHES = {
    ".github/workflows/release-docker-stable.yml": (
        "sha256:4c567285627e2c6f95bf0afca46ed90e"
        "35a003a11febc2fb02194a786f21c9f8"
    ),
    ".github/workflows/release-docker.yml": (
        "sha256:8f5f76c476229bf1d26d97722c19dd9c"
        "14dedd481e788caece20107258e3c4df"
    ),
}
REVIEWED_DOCKERFILE_HASH = (
    "sha256:08773687fbdd6a3021a087ab4b9c8535"
    "1f37924fd323fef5a1212ea4d7aaad91"
)
REQUIRED_STATIC_CHECKS = (
    "stable_workflow_policy_hash_pinned",
    "prerelease_workflow_policy_hash_pinned",
    "workflow_dispatch_only",
    "tag_input_required",
    "stable_tag_shape_guard",
    "prerelease_tag_refused",
    "stable_tag_validation_expression_safe",
    "stable_checkout_tag_ref_qualified",
    "move_latest_operator_gated",
    "move_latest_default_no",
    "ghcr_primary",
    "canonical_image_pushed",
    "stable_canonical_digest_bound",
    "stable_aliases_absent_before_smoke",
    "stable_canonical_smoke_before_alias_promotion",
    "stable_alias_tagged",
    "profile_aliases_opt_in",
    "stable_profile_s_smoke",
    "stable_alias_smoke",
    "stable_alias_fail_closed",
    "latest_alias_smoke_if_moved",
    "stable_alias_verification_after_promotion",
    "prerelease_tag_shape_guard",
    "prerelease_alias_shape_guard",
    "prerelease_protected_aliases_refused",
    "prerelease_validation_expression_safe",
    "prerelease_checkout_tag_ref_qualified",
    "prerelease_canonical_image_pushed",
    "prerelease_canonical_digest_bound",
    "prerelease_aliases_absent_before_smoke",
    "prerelease_canonical_smoke_before_alias_promotion",
    "prerelease_alias_verification_after_promotion",
    "release_alias_concurrency_serialized",
    "dockerfile_policy_hash_pinned",
    "dockerfile_entrypoint_canonical",
    "compose_entrypoint_canonical",
    "pyproject_script_canonical",
)


def _format_utc(value: dt.datetime) -> str:
    normalized = value.astimezone(dt.UTC).replace(microsecond=0)
    return normalized.isoformat().replace("+00:00", "Z")


def _parse_utc(value: object) -> dt.datetime | None:
    if isinstance(value, dt.datetime):
        parsed = value
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.UTC)
        return parsed.astimezone(dt.UTC)
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.UTC)
        return parsed.astimezone(dt.UTC)
    except (ValueError, OverflowError):
        return None


def _dockerfile_final_stage_json_instruction(
    text: str,
    instruction_name: str,
    *,
    allow_empty: bool = False,
) -> list[str] | None:
    """Return one JSON-form instruction from the final Docker stage."""

    final_stage_instructions: list[list[str]] = []
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        instruction_match = re.match(r"^([A-Za-z]+)\s+(.*)$", stripped)
        if instruction_match is None:
            continue
        instruction = instruction_match.group(1).upper()
        argument = instruction_match.group(2).strip()
        if instruction == "FROM":
            final_stage_instructions = []
            continue
        if instruction != instruction_name:
            continue
        try:
            parsed = json.loads(argument)
        except (json.JSONDecodeError, TypeError):
            return None
        if (
            not isinstance(parsed, list)
            or (not parsed and not allow_empty)
            or any(not isinstance(item, str) for item in parsed)
        ):
            return None
        final_stage_instructions.append(parsed)

    if len(final_stage_instructions) != 1:
        return None
    return final_stage_instructions[0]


def _dockerfile_final_stage_json_cmd(text: str) -> list[str] | None:
    return _dockerfile_final_stage_json_instruction(text, "CMD")


def _dockerfile_final_stage_json_entrypoint(text: str) -> list[str] | None:
    return _dockerfile_final_stage_json_instruction(
        text,
        "ENTRYPOINT",
        allow_empty=True,
    )


def _current_commit(source_root: Path | str = Path(".")) -> str:
    try:
        completed = subprocess.run(
            [
                "git",
                "--no-replace-objects",
                "-C",
                str(source_root),
                "rev-parse",
                "HEAD",
            ],
            check=True,
            capture_output=True,
            text=True,
            env=_sanitized_git_env(),
        )
    except (OSError, subprocess.CalledProcessError):
        return ""
    return completed.stdout.strip()


def _operator_signoff(value: object) -> tuple[str, str] | None:
    if not isinstance(value, str):
        return None
    parts = value.strip().split(":", 2)
    if len(parts) != 3 or parts[0] != "operator" or not parts[1].strip():
        return None
    signed_at = _parse_utc(parts[2])
    if signed_at is None:
        return None
    return parts[1].strip(), _format_utc(signed_at)


def _canonical_pack_scope(
    pack: Mapping[str, Any],
    canonical_name: str,
    aliases: tuple[str, ...],
) -> str:
    value = pack.get(canonical_name)
    if not isinstance(value, str) or not value.strip():
        return ""
    canonical = value.strip()
    for alias in aliases:
        if alias not in pack:
            continue
        alias_value = pack.get(alias)
        if not isinstance(alias_value, str) or alias_value.strip() != canonical:
            return ""
    return canonical


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: yaml.SafeLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _load_unique_decision_pack(
    content: bytes,
    *,
    source: str,
) -> dict[str, Any] | None:
    try:
        loaded = yaml.load(
            content.decode("utf-8"),
            Loader=_UniqueKeyLoader,
        )
        if not isinstance(loaded, Mapping):
            return None
        _validate_pack(loaded, source=source)
    except (
        UnicodeDecodeError,
        ValueError,
        OverflowError,
        yaml.YAMLError,
        DecisionPackError,
    ):
        return None
    return dict(loaded)


def _authorization_from_pack_bytes(
    content: bytes,
    *,
    source: str,
    commit: str,
    target_version: str,
    expected_relative_path: Path,
) -> dict[str, Any] | None:
    pack = _load_unique_decision_pack(content, source=source)
    if pack is None:
        return None
    decision_id = _docker_decision_pack_id(target_version)
    if not decision_id or pack.get("decision_id") != decision_id:
        return None
    if pack.get("category") != DOCKER_DECISION_PACK_CATEGORY:
        return None
    if not is_signed(pack):
        return None
    invariants = pack.get("structural_invariants")
    if not isinstance(invariants, Mapping):
        return None
    if any(
        invariants.get(name) is not True
        for name in REQUIRED_DECISION_PACK_INVARIANTS
    ):
        return None

    pack_target = _canonical_pack_scope(
        pack,
        "target_version",
        ("release_version",),
    )
    if pack_target != target_version:
        return None
    pack_commit = _canonical_pack_scope(
        pack,
        "commit",
        ("target_commit", "subject_commit"),
    )
    if pack_commit != commit:
        return None

    signoff = pack.get("operator_signoff")
    if not isinstance(signoff, Mapping):
        return None
    chosen = str(signoff.get("chosen_option", "") or "").strip()
    if chosen != REQUIRED_DOCKER_DECISION_OPTION:
        return None
    option_policy = DOCKER_DECISION_OPTIONS.get(chosen)
    signed = _operator_signoff(signoff.get("signed_by"))
    if option_policy is None or signed is None:
        return None
    matching_options = [
        option
        for option in pack.get("options") or []
        if isinstance(option, Mapping) and option.get("id") == chosen
    ]
    if len(matching_options) != 1:
        return None
    option_data = matching_options[0].get("data")
    if option_data is not None:
        if not isinstance(option_data, Mapping):
            return None
        expected_moves_latest = option_policy["move_latest"] == "yes"
        recognized_expectations = {
            "move_latest": option_policy["move_latest"],
            "moves_latest": expected_moves_latest,
            "stable_promotion_authorized": option_policy[
                "stable_promotion_authorized"
            ],
            "docker_promotion_deferred": option_policy[
                "docker_promotion_deferred"
            ],
        }
        if any(
            key in option_data
            and (
                type(option_data.get(key)) is not type(expected)
                or option_data.get(key) != expected
            )
            for key, expected in recognized_expectations.items()
        ):
            return None
    operator_id, authorized_at_utc = signed
    created_at = _parse_utc(pack.get("created_utc"))
    if created_at is None:
        return None

    return {
        "schema_version": AUTH_SCHEMA_VERSION,
        "target_version": target_version,
        "commit": commit,
        "commit_scope": "exact",
        "decision_pack_target_version": pack_target,
        "decision_pack_commit": pack_commit,
        "stable_promotion_authorized": option_policy[
            "stable_promotion_authorized"
        ],
        "docker_promotion_deferred": option_policy[
            "docker_promotion_deferred"
        ],
        "move_latest": option_policy["move_latest"],
        "authorization_id": (
            f"decision-pack:{decision_id}:{chosen}:{operator_id}"
        ),
        "authorized_at_utc": authorized_at_utc,
        "decision_pack_created_at_utc": _format_utc(created_at),
        "decision_pack_path": expected_relative_path.as_posix(),
        "decision_pack_sha256": _source_bytes_sha256(content),
        "source": "operator_decision_pack",
        "decision_id": decision_id,
        "chosen_option": chosen,
        "operator_id": operator_id,
    }


def operator_authorization_from_decision_pack(
    path: Path | str,
    *,
    commit: str,
    target_version: str,
    source_root: Path | str | None = None,
) -> dict[str, Any] | None:
    """Convert a signed Docker operator decision pack into authorization.

    Draft, malformed, unsigned, wrong-scope, or structurally weakened packs
    return None. That keeps the policy evidence in draft/fail-closed state.
    """

    if not isinstance(commit, str) or COMMIT_PATTERN.fullmatch(commit) is None:
        return None
    decision_id = _docker_decision_pack_id(target_version)
    if not decision_id:
        return None
    pack_path = Path(path).resolve()
    expected_relative_path = _docker_decision_pack_relative_path(target_version)
    if source_root is None:
        try:
            root = pack_path.parents[2]
        except IndexError:
            return None
    else:
        root = Path(source_root).resolve()
    expected_path = root / expected_relative_path
    if pack_path != expected_path or not expected_path.exists():
        return None
    try:
        if not stat.S_ISREG(expected_path.lstat().st_mode):
            return None
        content = expected_path.read_bytes()
    except OSError:
        return None
    return _authorization_from_pack_bytes(
        content,
        source=expected_relative_path.name,
        commit=commit,
        target_version=target_version,
        expected_relative_path=expected_relative_path,
    )


def _source_bytes_sha256(content: bytes) -> str:
    normalized = content.replace(b"\r\n", b"\n")
    return "sha256:" + hashlib.sha256(normalized).hexdigest()


def _normalized_bytes_sha256(content: bytes) -> str:
    """Hash UTF-8 text with platform newline translation removed."""

    normalized = (
        content.decode("utf-8")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def inspect_source_commit_binding(
    source_root: Path | str,
    commit: str,
) -> dict[str, Any]:
    """Verify required working-tree sources equal one exact Git commit."""

    result: dict[str, Any] = {
        "commit": commit,
        "verified": False,
        "reason": "",
        "source_blob_oids": {},
    }
    if not isinstance(commit, str) or COMMIT_PATTERN.fullmatch(commit) is None:
        result["reason"] = "commit_invalid"
        return result

    root = Path(source_root).resolve()

    def git(
        *args: str,
        text: bool = True,
    ) -> subprocess.CompletedProcess[Any] | None:
        try:
            return subprocess.run(
                ["git", "--no-replace-objects", "-C", str(root), *args],
                check=False,
                capture_output=True,
                text=text,
                env=_sanitized_git_env(),
            )
        except OSError:
            return None

    top_level = git("rev-parse", "--show-toplevel", text=False)
    if top_level is None or top_level.returncode != 0:
        result["reason"] = "git_root_unavailable"
        return result
    try:
        resolved_top_level = Path(
            os.fsdecode(top_level.stdout.rstrip(b"\r\n")),
        ).resolve()
    except OSError:
        result["reason"] = "git_root_unavailable"
        return result
    if resolved_top_level != root:
        result["reason"] = "source_root_not_git_top_level"
        return result

    resolved_commit = git("rev-parse", "--verify", f"{commit}^{{commit}}")
    if resolved_commit is None or resolved_commit.returncode != 0:
        result["reason"] = "commit_not_found"
        return result
    if resolved_commit.stdout.strip().lower() != commit:
        result["reason"] = "commit_not_exact"
        return result

    ancestry = git("merge-base", "--is-ancestor", commit, "HEAD")
    if ancestry is None or ancestry.returncode != 0:
        result["reason"] = "commit_not_ancestor_of_head"
        return result

    source_blob_oids: dict[str, str] = {}
    for source in REQUIRED_SOURCE_FILES:
        path = str(source)
        tree_entry = git(
            "ls-tree",
            "-z",
            "--full-tree",
            commit,
            "--",
            path,
            text=False,
        )
        if (
            tree_entry is None
            or tree_entry.returncode != 0
            or not tree_entry.stdout
        ):
            result["reason"] = f"source_missing_at_commit:{path}"
            return result
        try:
            entry = tree_entry.stdout.rstrip(b"\0")
            metadata, entry_path = entry.split(b"\t", 1)
            mode, object_type, oid = metadata.split(b" ", 2)
        except ValueError:
            result["reason"] = f"source_tree_entry_invalid:{path}"
            return result
        if (
            entry_path != path.encode("utf-8")
            or object_type != b"blob"
            or mode not in {b"100644", b"100755"}
        ):
            result["reason"] = f"source_not_regular_at_commit:{path}"
            return result

        index_entry = git(
            "ls-files",
            "--stage",
            "-z",
            "--",
            path,
            text=False,
        )
        if (
            index_entry is None
            or index_entry.returncode != 0
            or not index_entry.stdout
        ):
            result["reason"] = f"source_index_mismatch:{path}"
            return result
        try:
            index_line = index_entry.stdout.rstrip(b"\0")
            index_metadata, index_path = index_line.split(b"\t", 1)
            index_mode, index_oid, index_stage = index_metadata.split(b" ", 2)
        except ValueError:
            result["reason"] = f"source_index_mismatch:{path}"
            return result
        if (
            index_path != path.encode("utf-8")
            or index_mode != mode
            or index_oid.lower() != oid.lower()
            or index_stage != b"0"
        ):
            result["reason"] = f"source_index_mismatch:{path}"
            return result

        working_path = root / source
        try:
            working_stat = working_path.lstat()
            if not stat.S_ISREG(working_stat.st_mode):
                result["reason"] = f"source_not_regular_in_worktree:{path}"
                return result
            working_bytes = working_path.read_bytes()
        except OSError:
            result["reason"] = f"source_missing_in_worktree:{path}"
            return result

        committed = git("cat-file", "blob", f"{commit}:{path}", text=False)
        if committed is None or committed.returncode != 0:
            result["reason"] = f"source_blob_unavailable_at_commit:{path}"
            return result
        committed_bytes = committed.stdout
        if (
            working_bytes.replace(b"\r\n", b"\n")
            != committed_bytes.replace(b"\r\n", b"\n")
        ):
            result["reason"] = f"source_mismatch:{path}"
            return result
        source_blob_oids[path] = oid.decode("ascii").lower()

    result["verified"] = True
    result["reason"] = "verified"
    result["source_blob_oids"] = source_blob_oids
    return result


def _load_committed_source_snapshot(
    source_root: Path | str,
    commit: str,
) -> dict[str, bytes]:
    """Read one immutable snapshot of every required source from Git."""

    if not isinstance(commit, str) or COMMIT_PATTERN.fullmatch(commit) is None:
        return {}
    root = Path(source_root).resolve()
    snapshot: dict[str, bytes] = {}
    for source in REQUIRED_SOURCE_FILES:
        path = str(source)
        try:
            completed = subprocess.run(
                [
                    "git",
                    "--no-replace-objects",
                    "-C",
                    str(root),
                    "cat-file",
                    "blob",
                    f"{commit}:{path}",
                ],
                check=False,
                capture_output=True,
                text=False,
                env=_sanitized_git_env(),
            )
        except OSError:
            return {}
        if completed.returncode != 0:
            return {}
        snapshot[path] = completed.stdout
    return snapshot


def inspect_operator_authorization_source(
    authorization: Mapping[str, Any] | None,
    *,
    source_root: Path | str,
    commit: str,
    target_version: str,
) -> dict[str, Any]:
    """Bind authorization to one retained, tracked decision pack."""

    result: dict[str, Any] = {
        "verified": False,
        "reason": "",
        "decision_pack_path": "",
        "decision_pack_sha256": "",
    }
    if not isinstance(authorization, Mapping):
        result["reason"] = "operator_authorization_missing"
        return result
    if not isinstance(commit, str) or COMMIT_PATTERN.fullmatch(commit) is None:
        result["reason"] = "commit_invalid"
        return result

    decision_id = _docker_decision_pack_id(target_version)
    expected_relative = _docker_decision_pack_relative_path(target_version)
    expected_path_text = expected_relative.as_posix()
    if (
        not decision_id
        or authorization.get("decision_id") != decision_id
        or authorization.get("decision_pack_path") != expected_path_text
    ):
        result["reason"] = "decision_pack_path_invalid"
        return result

    root = Path(source_root).resolve()
    pack_path = root / expected_relative
    result["decision_pack_path"] = expected_path_text

    def git(
        *args: str,
        text: bool = True,
    ) -> subprocess.CompletedProcess[Any] | None:
        try:
            return subprocess.run(
                ["git", "--no-replace-objects", "-C", str(root), *args],
                check=False,
                capture_output=True,
                text=text,
                env=_sanitized_git_env(),
            )
        except OSError:
            return None

    top_level = git("rev-parse", "--show-toplevel", text=False)
    if top_level is None or top_level.returncode != 0:
        result["reason"] = "decision_pack_git_root_unavailable"
        return result
    try:
        resolved_top_level = Path(
            os.fsdecode(top_level.stdout.rstrip(b"\r\n")),
        ).resolve()
    except OSError:
        result["reason"] = "decision_pack_git_root_unavailable"
        return result
    if resolved_top_level != root:
        result["reason"] = "decision_pack_source_root_not_git_top_level"
        return result

    tree_entry = git(
        "ls-tree",
        "-z",
        "--full-tree",
        "HEAD",
        "--",
        expected_path_text,
        text=False,
    )
    if (
        tree_entry is None
        or tree_entry.returncode != 0
        or not tree_entry.stdout
    ):
        result["reason"] = "decision_pack_not_tracked"
        return result
    try:
        entry = tree_entry.stdout.rstrip(b"\0")
        metadata, entry_path = entry.split(b"\t", 1)
        mode, object_type, tree_oid = metadata.split(b" ", 2)
    except ValueError:
        result["reason"] = "decision_pack_tree_entry_invalid"
        return result
    if (
        entry_path != expected_path_text.encode("utf-8")
        or object_type != b"blob"
        or mode not in {b"100644", b"100755"}
    ):
        result["reason"] = "decision_pack_not_regular_at_head"
        return result

    index_entry = git(
        "ls-files",
        "--stage",
        "-z",
        "--",
        expected_path_text,
        text=False,
    )
    if (
        index_entry is None
        or index_entry.returncode != 0
        or not index_entry.stdout
    ):
        result["reason"] = "decision_pack_index_mismatch"
        return result
    try:
        index_line = index_entry.stdout.rstrip(b"\0")
        index_metadata, index_path = index_line.split(b"\t", 1)
        index_mode, index_oid, index_stage = index_metadata.split(b" ", 2)
    except ValueError:
        result["reason"] = "decision_pack_index_mismatch"
        return result
    if (
        index_path != expected_path_text.encode("utf-8")
        or index_mode != mode
        or index_oid.lower() != tree_oid.lower()
        or index_stage != b"0"
    ):
        result["reason"] = "decision_pack_index_mismatch"
        return result

    try:
        pack_stat = pack_path.lstat()
        if not stat.S_ISREG(pack_stat.st_mode):
            result["reason"] = "decision_pack_not_regular_in_worktree"
            return result
        working_bytes = pack_path.read_bytes()
    except OSError:
        result["reason"] = "decision_pack_missing_in_worktree"
        return result
    committed = git(
        "cat-file",
        "blob",
        f"HEAD:{expected_path_text}",
        text=False,
    )
    if committed is None or committed.returncode != 0:
        result["reason"] = "decision_pack_blob_unavailable"
        return result
    if (
        working_bytes.replace(b"\r\n", b"\n")
        != committed.stdout.replace(b"\r\n", b"\n")
    ):
        result["reason"] = "decision_pack_worktree_mismatch"
        return result

    actual_digest = _source_bytes_sha256(committed.stdout)
    result["decision_pack_sha256"] = actual_digest
    if authorization.get("decision_pack_sha256") != actual_digest:
        result["reason"] = "decision_pack_digest_mismatch"
        return result

    converted = _authorization_from_pack_bytes(
        committed.stdout,
        source=expected_relative.name,
        commit=commit,
        target_version=target_version,
        expected_relative_path=expected_relative,
    )
    if converted is None:
        result["reason"] = "decision_pack_invalid"
        return result
    if dict(authorization) != converted:
        result["reason"] = "decision_pack_authorization_mismatch"
        return result

    commit_time_result = git("show", "-s", "--format=%cI", commit)
    storage_time_result = git("show", "-s", "--format=%cI", "HEAD")
    if (
        commit_time_result is None
        or commit_time_result.returncode != 0
        or storage_time_result is None
        or storage_time_result.returncode != 0
    ):
        result["reason"] = "decision_pack_subject_time_unavailable"
        return result
    commit_time = _parse_utc(commit_time_result.stdout.strip())
    storage_time = _parse_utc(storage_time_result.stdout.strip())
    created_at = _parse_utc(authorization.get("decision_pack_created_at_utc"))
    authorized_at = _parse_utc(authorization.get("authorized_at_utc"))
    if (
        commit_time is None
        or storage_time is None
        or created_at is None
        or authorized_at is None
    ):
        result["reason"] = "decision_pack_time_invalid"
        return result
    if created_at > authorized_at:
        result["reason"] = "decision_pack_signed_before_creation"
        return result
    if authorized_at < commit_time:
        result["reason"] = "decision_pack_signoff_predates_subject"
        return result
    if created_at > storage_time or authorized_at > storage_time:
        result["reason"] = "decision_pack_time_after_storage_commit"
        return result

    result["verified"] = True
    result["reason"] = "verified"
    return result


def _load_yaml(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _workflow_on(workflow: dict[str, Any]) -> Any:
    return workflow.get("on", workflow.get(True, {}))


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    steps = job.get("steps", [])
    return [step for step in steps if isinstance(step, dict)] if isinstance(steps, list) else []


def _needs(job: dict[str, Any]) -> set[str]:
    needs = job.get("needs", [])
    if isinstance(needs, str):
        return {needs}
    if isinstance(needs, list):
        return {item for item in needs if isinstance(item, str)}
    return set()


def _find_step(job: dict[str, Any], needle: str) -> dict[str, Any] | None:
    needle = needle.lower()
    for step in _steps(job):
        if needle in str(step.get("name", "")).lower():
            return step
    return None


def _find_uses_step(job: dict[str, Any], needle: str) -> dict[str, Any] | None:
    needle = needle.lower()
    for step in _steps(job):
        if needle in str(step.get("uses", "")).lower():
            return step
    return None


def inspect_static_policy(
    source_root: Path | str = Path("."),
    *,
    source_snapshot: Mapping[str, bytes] | None = None,
) -> dict[str, Any]:
    """Inspect static Docker release policy files without running Docker."""

    root = Path(source_root)

    def source_bytes(relative_path: str) -> bytes | None:
        if source_snapshot is not None:
            return source_snapshot.get(relative_path)
        path = root / relative_path
        return path.read_bytes() if path.is_file() else None

    def source_text(relative_path: str) -> str | None:
        content = source_bytes(relative_path)
        return content.decode("utf-8") if content is not None else None

    def source_yaml(relative_path: str) -> dict[str, Any]:
        text = source_text(relative_path)
        if text is None:
            return {}
        loaded = yaml.safe_load(text)
        return loaded if isinstance(loaded, dict) else {}

    workflow_bytes = source_bytes(
        ".github/workflows/release-docker-stable.yml",
    )
    prerelease_workflow_bytes = source_bytes(
        ".github/workflows/release-docker.yml",
    )
    dockerfile_bytes = source_bytes("Dockerfile")
    workflow = source_yaml(".github/workflows/release-docker-stable.yml")
    prerelease_workflow = (
        source_yaml(".github/workflows/release-docker.yml")
    )
    workflow_on = _workflow_on(workflow)
    workflow_dispatch = (
        workflow_on.get("workflow_dispatch", {})
        if isinstance(workflow_on, dict)
        else {}
    )
    inputs = workflow_dispatch.get("inputs", {}) if isinstance(workflow_dispatch, dict) else {}
    move_latest = inputs.get("move_latest", {}) if isinstance(inputs, dict) else {}
    tag_input = inputs.get("tag", {}) if isinstance(inputs, dict) else {}
    profile_aliases = inputs.get("profile_aliases", {}) if isinstance(inputs, dict) else {}
    jobs = workflow.get("jobs", {}) if isinstance(workflow.get("jobs"), dict) else {}
    validate_job = jobs.get("validate-tag", {}) if isinstance(jobs.get("validate-tag"), dict) else {}
    build_job = jobs.get("build-and-push", {}) if isinstance(jobs.get("build-and-push"), dict) else {}
    smoke_job = jobs.get("smoke-test", {}) if isinstance(jobs.get("smoke-test"), dict) else {}
    promote_job = jobs.get("promote-aliases", {}) if isinstance(jobs.get("promote-aliases"), dict) else {}
    verify_job = jobs.get("verify-aliases", {}) if isinstance(jobs.get("verify-aliases"), dict) else {}

    validate_run = "\n".join(str(step.get("run", "")) for step in _steps(validate_job))
    build_run = "\n".join(str(step.get("run", "")) for step in _steps(build_job))
    smoke_run = "\n".join(str(step.get("run", "")) for step in _steps(smoke_job))
    promote_run = "\n".join(str(step.get("run", "")) for step in _steps(promote_job))
    verify_run = "\n".join(str(step.get("run", "")) for step in _steps(verify_job))
    validate_step = _find_step(validate_job, "validate strict stable tag")
    stable_checkout = _find_uses_step(build_job, "actions/checkout")
    canonical_build = _find_step(build_job, "canonical stable image")
    latest_step = _find_step(promote_job, "latest")
    latest_smoke = _find_step(verify_job, "latest")
    canonical_build_with = (
        canonical_build.get("with", {}) if isinstance(canonical_build, dict) else {}
    )
    canonical_tags = (
        str(canonical_build_with.get("tags", ""))
        if isinstance(canonical_build_with, dict)
        else ""
    )
    stable_validation_env = (
        validate_step.get("env", {}) if isinstance(validate_step, dict) else {}
    )
    stable_build_outputs = (
        build_job.get("outputs", {}) if isinstance(build_job.get("outputs"), dict) else {}
    )
    stable_checkout_with = (
        stable_checkout.get("with", {}) if isinstance(stable_checkout, dict) else {}
    )
    stable_smoke_env = (
        smoke_job.get("env", {}) if isinstance(smoke_job.get("env"), dict) else {}
    )
    stable_promote_env = (
        promote_job.get("env", {}) if isinstance(promote_job.get("env"), dict) else {}
    )
    stable_verify_env = (
        verify_job.get("env", {}) if isinstance(verify_job.get("env"), dict) else {}
    )

    prerelease_jobs = (
        prerelease_workflow.get("jobs", {})
        if isinstance(prerelease_workflow.get("jobs"), dict)
        else {}
    )
    prerelease_validate_job = (
        prerelease_jobs.get("validate-inputs", {})
        if isinstance(prerelease_jobs.get("validate-inputs"), dict)
        else {}
    )
    prerelease_build_job = (
        prerelease_jobs.get("build-and-push", {})
        if isinstance(prerelease_jobs.get("build-and-push"), dict)
        else {}
    )
    prerelease_smoke_job = (
        prerelease_jobs.get("smoke-test", {})
        if isinstance(prerelease_jobs.get("smoke-test"), dict)
        else {}
    )
    prerelease_promote_job = (
        prerelease_jobs.get("promote-aliases", {})
        if isinstance(prerelease_jobs.get("promote-aliases"), dict)
        else {}
    )
    prerelease_verify_job = (
        prerelease_jobs.get("verify-aliases", {})
        if isinstance(prerelease_jobs.get("verify-aliases"), dict)
        else {}
    )
    prerelease_validate_step = _find_step(
        prerelease_validate_job,
        "validate prerelease tag and aliases",
    )
    prerelease_checkout = _find_uses_step(
        prerelease_build_job,
        "actions/checkout",
    )
    prerelease_canonical_build = _find_step(
        prerelease_build_job,
        "canonical image",
    )
    prerelease_validate_run = "\n".join(
        str(step.get("run", "")) for step in _steps(prerelease_validate_job)
    )
    prerelease_build_run = "\n".join(
        str(step.get("run", "")) for step in _steps(prerelease_build_job)
    )
    prerelease_smoke_run = "\n".join(
        str(step.get("run", "")) for step in _steps(prerelease_smoke_job)
    )
    prerelease_promote_run = "\n".join(
        str(step.get("run", "")) for step in _steps(prerelease_promote_job)
    )
    prerelease_verify_run = "\n".join(
        str(step.get("run", "")) for step in _steps(prerelease_verify_job)
    )
    prerelease_validation_env = (
        prerelease_validate_step.get("env", {})
        if isinstance(prerelease_validate_step, dict)
        else {}
    )
    prerelease_canonical_with = (
        prerelease_canonical_build.get("with", {})
        if isinstance(prerelease_canonical_build, dict)
        else {}
    )
    prerelease_build_outputs = (
        prerelease_build_job.get("outputs", {})
        if isinstance(prerelease_build_job.get("outputs"), dict)
        else {}
    )
    prerelease_checkout_with = (
        prerelease_checkout.get("with", {})
        if isinstance(prerelease_checkout, dict)
        else {}
    )
    prerelease_smoke_env = (
        prerelease_smoke_job.get("env", {})
        if isinstance(prerelease_smoke_job.get("env"), dict)
        else {}
    )
    prerelease_promote_env = (
        prerelease_promote_job.get("env", {})
        if isinstance(prerelease_promote_job.get("env"), dict)
        else {}
    )
    prerelease_verify_env = (
        prerelease_verify_job.get("env", {})
        if isinstance(prerelease_verify_job.get("env"), dict)
        else {}
    )

    try:
        pyproject_text = source_text("pyproject.toml")
        pyproject = tomllib.loads(pyproject_text) if pyproject_text else {}
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        pyproject = {}
    try:
        compose = source_yaml("docker-compose.yml")
    except (UnicodeDecodeError, yaml.YAMLError):
        compose = {}
    try:
        dockerfile_text = (
            dockerfile_bytes.decode("utf-8")
            if dockerfile_bytes is not None
            else ""
        )
    except UnicodeDecodeError:
        dockerfile_text = ""
    dockerfile_cmd = _dockerfile_final_stage_json_cmd(dockerfile_text)
    dockerfile_entrypoint = _dockerfile_final_stage_json_entrypoint(
        dockerfile_text,
    )

    compose_command = (
        compose.get("services", {})
        .get("waggledance", {})
        .get("command")
        if isinstance(compose.get("services"), dict)
        else None
    )
    script = (
        pyproject.get("project", {})
        .get("scripts", {})
        .get("waggledance")
        if isinstance(pyproject.get("project"), dict)
        else None
    )

    stable_concurrency = (
        workflow.get("concurrency", {})
        if isinstance(workflow.get("concurrency"), dict)
        else {}
    )
    prerelease_concurrency = (
        prerelease_workflow.get("concurrency", {})
        if isinstance(prerelease_workflow.get("concurrency"), dict)
        else {}
    )
    stable_digest_ref = (
        "ghcr.io/ahkeratmehilaiset/waggledance@"
        "${{ needs.build-and-push.outputs.digest }}"
    )
    prerelease_digest_ref = stable_digest_ref
    stable_alias_tokens = (
        "waggledance:stable",
        "waggledance:latest",
        "waggledance:small-stable",
        "waggledance:medium-stable",
    )

    checks = {
        "stable_workflow_policy_hash_pinned": workflow_bytes is not None
        and _normalized_bytes_sha256(workflow_bytes)
        == REVIEWED_WORKFLOW_HASHES[str(REQUIRED_SOURCE_FILES[0])],
        "prerelease_workflow_policy_hash_pinned": (
            prerelease_workflow_bytes is not None
            and _normalized_bytes_sha256(prerelease_workflow_bytes)
            == REVIEWED_WORKFLOW_HASHES[str(REQUIRED_SOURCE_FILES[1])]
        ),
        "workflow_dispatch_only": isinstance(workflow_on, dict)
        and set(workflow_on) == {"workflow_dispatch"},
        "tag_input_required": isinstance(tag_input, dict)
        and tag_input.get("required") is True,
        "stable_tag_shape_guard": "v[0-9]+\\.[0-9]+\\.[0-9]+" in validate_run
        and "re.fullmatch" in validate_run
        and "len(tag) > 128" in validate_run,
        "prerelease_tag_refused": "re.fullmatch" in validate_run
        and "v[0-9]+\\.[0-9]+\\.[0-9]+" in validate_run,
        "stable_tag_validation_expression_safe": isinstance(
            stable_validation_env,
            dict,
        )
        and stable_validation_env.get("INPUT_TAG") == "${{ inputs.tag }}"
        and "${{ inputs.tag }}" not in validate_run
        and 0 <= validate_run.find("re.fullmatch") < validate_run.find("with open"),
        "stable_checkout_tag_ref_qualified": isinstance(
            stable_checkout_with,
            dict,
        )
        and stable_checkout_with.get("ref")
        == "refs/tags/${{ needs.validate-tag.outputs.tag }}",
        "move_latest_operator_gated": isinstance(latest_step, dict)
        and "inputs.move_latest == 'yes'" in str(latest_step.get("if", "")),
        "move_latest_default_no": isinstance(move_latest, dict)
        and move_latest.get("default") == "no"
        and set(move_latest.get("options", [])) == {"yes", "no"},
        "ghcr_primary": "ghcr.io/ahkeratmehilaiset/waggledance" in canonical_tags
        and "docker.io" not in canonical_tags.lower(),
        "canonical_image_pushed": isinstance(canonical_build, dict)
        and "docker/build-push-action" in str(canonical_build.get("uses", ""))
        and isinstance(canonical_build_with, dict)
        and canonical_build_with.get("push") is True
        and canonical_build.get("id") == "canonical",
        "stable_canonical_digest_bound": stable_build_outputs.get("digest")
        == "${{ steps.canonical.outputs.digest }}"
        and stable_smoke_env.get("SOURCE_REF") == stable_digest_ref
        and stable_promote_env.get("SOURCE_REF") == stable_digest_ref
        and stable_verify_env.get("SOURCE_REF") == stable_digest_ref
        and '"$SOURCE_REF"' in smoke_run
        and promote_run.count('"$SOURCE_REF"')
        == promote_run.count("imagetools create")
        and promote_run.count("imagetools create")
        == promote_run.count("--prefer-index=false")
        and promote_run.count("imagetools create") > 0,
        "stable_aliases_absent_before_smoke": "imagetools create" not in build_run
        and not any(token in canonical_tags for token in stable_alias_tokens),
        "stable_canonical_smoke_before_alias_promotion": {
            "validate-tag",
            "build-and-push",
            "smoke-test",
        }
        <= _needs(promote_job),
        "stable_alias_tagged": "waggledance:stable" in promote_run,
        "profile_aliases_opt_in": isinstance(profile_aliases, dict)
        and profile_aliases.get("default") == "yes"
        and "small-stable" in promote_run
        and "medium-stable" in promote_run,
        "stable_profile_s_smoke": "SMOKE_OK_STABLE" in smoke_run
        and "WAGGLE_PROFILE=small" in smoke_run,
        "stable_alias_smoke": "waggledance:stable" in verify_run
        and "waggledance:small-stable" in verify_run
        and "waggledance:medium-stable" in verify_run
        and verify_run.count("docker buildx imagetools inspect") == 4
        and verify_run.count("{{json .Manifest}}") == 4
        and verify_run.count('EXPECTED_DIGEST="${SOURCE_REF#*@}"') == 4,
        "stable_alias_fail_closed": "::error::stable alias" in verify_run
        and "exit 1" in verify_run
        and "::warning::stable alias" not in verify_run,
        "latest_alias_smoke_if_moved": isinstance(latest_smoke, dict)
        and "inputs.move_latest == 'yes'" in str(latest_smoke.get("if", ""))
        and "waggledance:latest" in str(latest_smoke.get("run", "")),
        "stable_alias_verification_after_promotion": "promote-aliases"
        in _needs(verify_job)
        and "waggledance:stable" in verify_run
        and "docker buildx imagetools inspect" in verify_run,
        "prerelease_tag_shape_guard": "prerelease_shape" in prerelease_validate_run
        and "fullmatch(tag)" in prerelease_validate_run
        and "len(tag) <= 128" in prerelease_validate_run,
        "prerelease_alias_shape_guard": "alias_shape" in prerelease_validate_run
        and "fullmatch(promote_alias)" in prerelease_validate_run
        and "len(promote_alias) > 128" in prerelease_validate_run,
        "prerelease_protected_aliases_refused": all(
            alias in prerelease_validate_run
            for alias in ("latest", "stable", "small-stable", "medium-stable")
        )
        and "promote_alias in protected_aliases" in prerelease_validate_run
        and "version_alias_shape.match(promote_alias)"
        in prerelease_validate_run
        and 'promote_alias.startswith(("small-", "medium-"))'
        in prerelease_validate_run,
        "prerelease_validation_expression_safe": isinstance(
            prerelease_validation_env,
            dict,
        )
        and prerelease_validation_env.get("INPUT_TAG") == "${{ inputs.tag }}"
        and prerelease_validation_env.get("INPUT_PROMOTE_ALIAS")
        == "${{ inputs.promote_alias }}"
        and prerelease_validation_env.get("RELEASE_TAG")
        == "${{ github.event.release.tag_name }}"
        and "${{" not in prerelease_validate_run
        and 0
        <= prerelease_validate_run.find("if errors:")
        < prerelease_validate_run.find("with open"),
        "prerelease_checkout_tag_ref_qualified": isinstance(
            prerelease_checkout_with,
            dict,
        )
        and prerelease_checkout_with.get("ref")
        == "refs/tags/${{ needs.validate-inputs.outputs.tag }}",
        "prerelease_canonical_image_pushed": isinstance(
            prerelease_canonical_build,
            dict,
        )
        and "docker/build-push-action"
        in str(prerelease_canonical_build.get("uses", ""))
        and isinstance(prerelease_canonical_with, dict)
        and prerelease_canonical_with.get("push") is True
        and prerelease_canonical_build.get("id") == "canonical",
        "prerelease_canonical_digest_bound": prerelease_build_outputs.get(
            "digest",
        )
        == "${{ steps.canonical.outputs.digest }}"
        and prerelease_smoke_env.get("SOURCE_REF") == prerelease_digest_ref
        and prerelease_promote_env.get("SOURCE_REF") == prerelease_digest_ref
        and prerelease_verify_env.get("SOURCE_REF") == prerelease_digest_ref
        and '"$SOURCE_REF"' in prerelease_smoke_run
        and prerelease_promote_run.count('"$SOURCE_REF"')
        == prerelease_promote_run.count("imagetools create")
        and prerelease_promote_run.count("imagetools create")
        == prerelease_promote_run.count("--prefer-index=false")
        and prerelease_promote_run.count("imagetools create") > 0,
        "prerelease_aliases_absent_before_smoke": "imagetools create"
        not in prerelease_build_run,
        "prerelease_canonical_smoke_before_alias_promotion": {
            "validate-inputs",
            "build-and-push",
            "smoke-test",
        }
        <= _needs(prerelease_promote_job)
        and "imagetools create" in prerelease_promote_run,
        "prerelease_alias_verification_after_promotion": "promote-aliases"
        in _needs(prerelease_verify_job)
        and prerelease_verify_env.get("SOURCE_REF") == prerelease_digest_ref
        and prerelease_verify_run.count("docker buildx imagetools inspect") == 3
        and prerelease_verify_run.count("{{json .Manifest}}") == 3
        and prerelease_verify_run.count(
            'EXPECTED_DIGEST="${SOURCE_REF#*@}"'
        )
        == 3
        and "::error::requested prerelease alias" in prerelease_verify_run
        and "exit 1" in prerelease_verify_run,
        "release_alias_concurrency_serialized": stable_concurrency.get("group")
        == "waggledance-docker-release-aliases"
        and stable_concurrency.get("queue") == "max"
        and stable_concurrency.get("cancel-in-progress") is False
        and prerelease_concurrency.get("group")
        == "waggledance-docker-release-aliases"
        and prerelease_concurrency.get("queue") == "max"
        and prerelease_concurrency.get("cancel-in-progress") is False,
        "dockerfile_policy_hash_pinned": dockerfile_bytes is not None
        and _normalized_bytes_sha256(dockerfile_bytes)
        == REVIEWED_DOCKERFILE_HASH,
        "dockerfile_entrypoint_canonical": dockerfile_cmd
        == CANONICAL_ENTRYPOINT
        and dockerfile_entrypoint == [],
        "compose_entrypoint_canonical": compose_command == CANONICAL_ENTRYPOINT,
        "pyproject_script_canonical": script == CANONICAL_SCRIPT,
    }
    return {
        "checks": checks,
        "entrypoints": {
            "expected": CANONICAL_ENTRYPOINT,
            "dockerfile_cmd": dockerfile_cmd,
            "dockerfile_entrypoint": dockerfile_entrypoint,
            "compose_command": compose_command,
            "pyproject_script": script,
        },
        "source_files": [str(path) for path in REQUIRED_SOURCE_FILES],
        "source_hashes": {
            str(path): _source_bytes_sha256(content)
            for path in REQUIRED_SOURCE_FILES
            if (content := source_bytes(str(path))) is not None
        },
    }


def _evaluate_report(
    report: dict[str, Any],
    *,
    expected_commit: str | None = None,
    target_version: str = DEFAULT_TARGET_VERSION,
    source_root: Path | str = Path("."),
    validate_derived_fields: bool,
) -> list[str]:
    """Return fail-closed blockers for a Docker policy evidence report."""

    blockers: list[str] = []
    if report.get("schema_version") != SCHEMA_VERSION:
        blockers.append("schema_version_invalid")
    if report.get("target_version") != target_version:
        blockers.append("target_version_mismatch")
    generated_at = _parse_utc(report.get("generated_at_utc"))
    if generated_at is None:
        blockers.append("generated_at_utc_invalid")
    elif generated_at > dt.datetime.now(dt.UTC) + dt.timedelta(minutes=5):
        blockers.append("generated_at_utc_in_future")
    commit = report.get("commit")
    if not isinstance(commit, str) or not commit.strip():
        blockers.append("commit_missing")
    elif expected_commit is not None and commit != expected_commit:
        blockers.append("commit_mismatch")

    source_root = Path(source_root)
    actual_source_commit_binding = inspect_source_commit_binding(
        source_root,
        commit if isinstance(commit, str) else "",
    )
    committed_source_snapshot = _load_committed_source_snapshot(
        source_root,
        commit if isinstance(commit, str) else "",
    )
    source_commit_binding = report.get("source_commit_binding")
    if not isinstance(source_commit_binding, dict):
        blockers.append("source_commit_binding_missing")
    elif source_commit_binding != actual_source_commit_binding:
        blockers.append("source_commit_binding_mismatch")
    if actual_source_commit_binding.get("verified") is not True:
        blockers.append(
            "source_commit_not_verified:"
            + str(actual_source_commit_binding.get("reason") or "unknown")
        )

    inspected = inspect_static_policy(
        source_root,
        source_snapshot=committed_source_snapshot,
    )
    source_files = report.get("source_files")
    if not isinstance(source_files, list):
        blockers.append("source_files_missing")
        source_files = []
    if source_files != inspected["source_files"]:
        blockers.append("source_files_mismatch")
    for item in source_files:
        if (
            not isinstance(item, str)
            or item not in committed_source_snapshot
        ):
            blockers.append(f"source_file_missing:{item}")

    source_hashes = report.get("source_hashes")
    if not isinstance(source_hashes, dict):
        blockers.append("source_hashes_missing")
    elif source_hashes != inspected["source_hashes"]:
        blockers.append("source_hashes_mismatch")

    static_checks = report.get("static_checks")
    if not isinstance(static_checks, dict):
        blockers.append("static_checks_missing")
        static_checks = {}
    elif static_checks != inspected["checks"]:
        blockers.append("static_checks_source_mismatch")
    for check in REQUIRED_STATIC_CHECKS:
        if static_checks.get(check) is not True:
            blockers.append(f"static_check_not_pass:{check}")

    entrypoints = report.get("entrypoints")
    if not isinstance(entrypoints, dict):
        blockers.append("entrypoints_missing")
    elif entrypoints != inspected["entrypoints"]:
        blockers.append("entrypoints_mismatch")

    authorization = report.get("operator_authorization")
    if not isinstance(authorization, dict):
        blockers.append("operator_authorization_missing")
    else:
        actual_authorization_source_binding = (
            inspect_operator_authorization_source(
                authorization,
                source_root=source_root,
                commit=commit if isinstance(commit, str) else "",
                target_version=target_version,
            )
        )
        authorization_source_binding = report.get(
            "operator_authorization_source_binding",
        )
        if not isinstance(authorization_source_binding, dict):
            blockers.append("operator_authorization_source_binding_missing")
        elif (
            authorization_source_binding
            != actual_authorization_source_binding
        ):
            blockers.append("operator_authorization_source_binding_mismatch")
        if actual_authorization_source_binding.get("verified") is not True:
            blockers.append(
                "operator_authorization_source_not_verified:"
                + str(
                    actual_authorization_source_binding.get("reason")
                    or "unknown"
                )
            )
        if authorization.get("schema_version") != AUTH_SCHEMA_VERSION:
            blockers.append("operator_authorization_schema_invalid")
        if authorization.get("target_version") != target_version:
            blockers.append("operator_authorization_target_mismatch")
        if authorization.get("commit") != commit:
            blockers.append("operator_authorization_commit_mismatch")
        commit_scope = authorization.get("commit_scope")
        if commit_scope != "exact":
            blockers.append("operator_authorization_commit_scope_invalid")
        if authorization.get("decision_pack_target_version") != target_version:
            blockers.append("operator_authorization_pack_target_mismatch")
        decision_pack_commit = authorization.get("decision_pack_commit")
        if decision_pack_commit != authorization.get("commit"):
            blockers.append("operator_authorization_exact_commit_mismatch")
        if authorization.get("source") != "operator_decision_pack":
            blockers.append("operator_authorization_source_invalid")
        expected_decision_id = _docker_decision_pack_id(target_version)
        if authorization.get("decision_id") != expected_decision_id:
            blockers.append("operator_authorization_decision_id_invalid")
        chosen_option = authorization.get("chosen_option")
        option_policy = (
            DOCKER_DECISION_OPTIONS.get(chosen_option)
            if chosen_option == REQUIRED_DOCKER_DECISION_OPTION
            else None
        )
        if option_policy is None:
            blockers.append("operator_authorization_chosen_option_invalid")
        else:
            for field in (
                "stable_promotion_authorized",
                "docker_promotion_deferred",
                "move_latest",
            ):
                actual = authorization.get(field)
                required = option_policy[field]
                if type(actual) is not type(required) or actual != required:
                    blockers.append(
                        f"operator_authorization_{field}_mismatch"
                    )
        operator_id = authorization.get("operator_id")
        if (
            not isinstance(operator_id, str)
            or not operator_id.strip()
            or operator_id != operator_id.strip()
        ):
            blockers.append("operator_authorization_operator_id_missing")
        authorization_id = authorization.get("authorization_id")
        if not isinstance(authorization_id, str) or not authorization_id:
            blockers.append("operator_authorization_id_missing")
        elif (
            option_policy is not None
            and isinstance(operator_id, str)
            and operator_id.strip()
            and authorization_id
            != (
                f"decision-pack:{expected_decision_id}:"
                f"{chosen_option}:{operator_id}"
            )
        ):
            blockers.append("operator_authorization_id_mismatch")
        authorized_at = _parse_utc(authorization.get("authorized_at_utc"))
        if authorized_at is None:
            blockers.append("operator_authorized_at_invalid")
        elif generated_at is not None and authorized_at > generated_at:
            blockers.append("operator_authorized_after_report_generation")

    if validate_derived_fields:
        semantic_blockers = list(blockers)
        if report.get("post_tag_runtime_verification_required") is not True:
            blockers.append("post_tag_runtime_verification_not_required")
        if report.get("latest_move_requires_operator_opt_in") is not True:
            blockers.append("latest_move_operator_opt_in_not_required")
        if report.get("blockers") != semantic_blockers:
            blockers.append("reported_blockers_mismatch")
        expected_policy = "finalized" if not semantic_blockers else "draft"
        if report.get("docker_stable_policy") != expected_policy:
            blockers.append("docker_stable_policy_mismatch")

    return blockers


def evaluate_report(
    report: dict[str, Any],
    *,
    expected_commit: str | None = None,
    target_version: str = DEFAULT_TARGET_VERSION,
    source_root: Path | str = Path("."),
) -> list[str]:
    """Strictly validate semantic, provenance, and derived report fields."""

    return _evaluate_report(
        report,
        expected_commit=expected_commit,
        target_version=target_version,
        source_root=source_root,
        validate_derived_fields=True,
    )


def build_report(
    *,
    source_root: Path | str = Path("."),
    commit: str,
    target_version: str = DEFAULT_TARGET_VERSION,
    operator_authorization: dict[str, Any] | None = None,
    generated_at_utc: dt.datetime | None = None,
) -> dict[str, Any]:
    generated_at_utc = generated_at_utc or dt.datetime.now(dt.UTC)
    source_commit_binding = inspect_source_commit_binding(
        source_root,
        commit,
    )
    committed_source_snapshot = _load_committed_source_snapshot(
        source_root,
        commit,
    )
    static = inspect_static_policy(
        source_root,
        source_snapshot=committed_source_snapshot,
    )
    authorization_source_binding = (
        inspect_operator_authorization_source(
            operator_authorization,
            source_root=source_root,
            commit=commit,
            target_version=target_version,
        )
        if isinstance(operator_authorization, Mapping)
        else None
    )
    report = {
        "schema_version": SCHEMA_VERSION,
        "target_version": target_version,
        "commit": commit,
        "generated_at_utc": _format_utc(generated_at_utc),
        "source_commit_binding": source_commit_binding,
        "source_files": static["source_files"],
        "source_hashes": static["source_hashes"],
        "static_checks": static["checks"],
        "entrypoints": static["entrypoints"],
        "operator_authorization": operator_authorization,
        "operator_authorization_source_binding": (
            authorization_source_binding
        ),
        "post_tag_runtime_verification_required": True,
        "latest_move_requires_operator_opt_in": True,
    }
    blockers = _evaluate_report(
        report,
        expected_commit=commit,
        target_version=target_version,
        source_root=source_root,
        validate_derived_fields=False,
    )
    report["blockers"] = blockers
    report["docker_stable_policy"] = "finalized" if not blockers else "draft"
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path("."))
    parser.add_argument("--commit", default="")
    parser.add_argument("--target-version", default=DEFAULT_TARGET_VERSION)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--operator-authorization", type=Path)
    parser.add_argument("--operator-decision-pack", type=Path)
    parser.add_argument(
        "--allow-draft",
        action="store_true",
        help="Exit 0 even when the policy remains draft/fail-closed.",
    )
    args = parser.parse_args(argv)

    commit = args.commit or _current_commit(args.source_root)
    if (
        args.operator_authorization is not None
        and args.operator_decision_pack is not None
    ):
        print(
            "run_release_docker_policy_evidence: use either "
            "--operator-authorization or --operator-decision-pack, not both",
            file=sys.stderr,
        )
        return 2
    if args.operator_authorization is not None:
        print(
            "run_release_docker_policy_evidence: raw operator authorization "
            "JSON is not accepted; use an exact-scoped signed "
            "--operator-decision-pack",
            file=sys.stderr,
        )
        return 2
    if args.operator_decision_pack is not None:
        authorization = operator_authorization_from_decision_pack(
            args.operator_decision_pack,
            commit=commit,
            target_version=args.target_version,
            source_root=args.source_root,
        )
    else:
        authorization = None
    report = build_report(
        source_root=args.source_root,
        commit=commit,
        target_version=args.target_version,
        operator_authorization=authorization,
    )
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    if report["docker_stable_policy"] == "finalized" or args.allow_draft:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
