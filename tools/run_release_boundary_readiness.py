#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Record release-boundary readiness without performing release actions.

This tool is deliberately read-only with respect to the release boundary. It
can observe that the release gate and operator decision packs are in place, but
it never creates a tag, moves a Docker alias, claims stable release, or changes
external authority. Finalization remains operator-only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.operator_decision_pack import DecisionPackError, is_signed, load_pack
from tools.run_release_docker_policy_evidence import (
    COMMIT_PATTERN,
    DEFAULT_OUTPUT as DEFAULT_DOCKER_POLICY_EVIDENCE,
    DEFAULT_TARGET_VERSION as DOCKER_TARGET_VERSION,
    SCHEMA_VERSION as DOCKER_POLICY_SCHEMA_VERSION,
    evaluate_report as evaluate_docker_policy_report,
)


SCHEMA_VERSION = "waggledance.release_boundary_readiness.v0"
DECISION_PACKET_SCHEMA_VERSION = "waggledance.release_boundary_decision_packet.v0"
SPRINT_DIR = Path("docs/runs/magma_100h_sprint_2026_05_26")
DEFAULT_PHASE_SYNTHESIS_REFRESH = SPRINT_DIR / "phase_synthesis_refresh.json"
DEFAULT_RELEASE_GATE_RECHECK = SPRINT_DIR / "release_gate_readonly_recheck.json"
DEFAULT_TORCH_DECISION_PACK = Path("docs/operator_inbox/torch-cuda-vs-cpu.yaml")
DEFAULT_DOCKER_DECISION_PACK = Path(
    "docs/operator_inbox/docker-v3-12-0-stable-promotion.yaml"
)
DEFAULT_SOAK_EVIDENCE = Path(
    "docs/runs/release_soak_evidence/v3.12.0.json"
)
DEFAULT_OUTPUT = SPRINT_DIR / "release_boundary_readiness.json"

RELEASE_SOAK_TASK_ID = "release_soak_evidence_blocker_resolution"
FINALIZATION_TASK_ID = "operator_release_finalization_decision"
STRICT_BLOCKED_EXIT_CODE = 2
RELEASE_SOAK_SCHEMA_VERSION = "waggledance.release_soak.v1"
LOCAL_ARTIFACT_COLLECTION_MODE = "local_artifacts"
PHASE_SYNTHESIS_SCHEMA_VERSION = (
    "waggledance.magma_100h_phase_synthesis_refresh.v0"
)
RELEASE_GATE_RECHECK_SCHEMA_VERSION = (
    "waggledance.release_gate_readonly_recheck.v0"
)
REQUIRED_SOAK_STATUS_FIELDS = (
    "axis_a_regression",
    "axis_b_gate",
    "ci_status",
    "profile_s_smoke",
    "release_notes_anti_claims",
    "security_privacy_gate",
)
REQUIRED_SOAK_DURATION_HOURS = 336
REQUIRED_SOAK_START_UTC = dt.datetime(2026, 5, 10, tzinfo=dt.UTC)
REQUIRED_SOAK_END_UTC = dt.datetime(2026, 5, 24, tzinfo=dt.UTC)
WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
WINDOWS_IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003

FALSE_RELEASE_BOUNDARY = {
    "stable_release_claim": False,
    "tag_creation": False,
    "docker_latest_move": False,
    "external_effect_authority_change": False,
}


def _release_boundary_is_exact_false(value: object) -> bool:
    return (
        isinstance(value, Mapping)
        and set(value) == set(FALSE_RELEASE_BOUNDARY)
        and all(value.get(field) is False for field in FALSE_RELEASE_BOUNDARY)
    )


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(microsecond=0)


def _format_utc(value: dt.datetime) -> str:
    return value.astimezone(dt.UTC).replace(microsecond=0).isoformat().replace(
        "+00:00",
        "Z",
    )


def _parse_timestamp(value: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.UTC)
        return parsed.astimezone(dt.UTC)
    except (ValueError, OverflowError) as exc:
        raise ValueError("invalid ISO-8601 timestamp") from exc


def _try_parse_timestamp(value: object) -> dt.datetime | None:
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.UTC)
        return value.astimezone(dt.UTC)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return _parse_timestamp(value.strip())
    except ValueError:
        return None


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _json_values_exact(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return (
            left.keys() == right.keys()
            and all(
                _json_values_exact(left[key], right[key])
                for key in left
            )
        )
    if isinstance(left, list):
        return (
            len(left) == len(right)
            and all(
                _json_values_exact(left_item, right_item)
                for left_item, right_item in zip(left, right, strict=True)
            )
        )
    return left == right


def _read_json(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_json_object,
        )
    except (OSError, ValueError):
        return {}
    return dict(loaded) if isinstance(loaded, Mapping) else {}


def _is_regular_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


def _lexical_absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _stat_is_reparse_point(metadata: os.stat_result) -> bool:
    file_attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_tag = getattr(metadata, "st_reparse_tag", 0)
    return (
        isinstance(file_attributes, int)
        and bool(file_attributes & WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT)
    ) or reparse_tag == WINDOWS_IO_REPARSE_TAG_MOUNT_POINT


def _path_has_indirection(path: Path, *, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    current = root
    try:
        for component in (None, *relative.parts):
            if component is not None:
                current /= component
            metadata = current.lstat()
            if current.is_symlink() or _stat_is_reparse_point(metadata):
                return True
            is_junction = getattr(current, "is_junction", None)
            if callable(is_junction) and is_junction():
                return True
    except OSError:
        return True
    return False


def _sanitized_git_env() -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _retained_file_source_binding(
    path: Path | None,
    *,
    expected_relative_path: Path,
    source_root: Path,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "verified": False,
        "reason": "",
        "path": str(path) if path is not None else "",
        "expected_path": expected_relative_path.as_posix(),
    }
    if path is None:
        result["reason"] = "path_missing"
        return result

    root = _lexical_absolute_path(source_root)
    expected = root / expected_relative_path
    candidate = path if path.is_absolute() else root / path
    candidate = _lexical_absolute_path(candidate)
    if candidate != expected:
        result["reason"] = "path_mismatch"
        return result
    if _path_has_indirection(candidate, root=root):
        result["reason"] = "path_indirection"
        return result
    if not _is_regular_file(candidate):
        result["reason"] = "file_not_regular"
        return result

    def git(*args: str, text: bool = False) -> subprocess.CompletedProcess[Any] | None:
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

    top_level = git("rev-parse", "--show-toplevel")
    if top_level is None or top_level.returncode != 0:
        result["reason"] = "git_root_unavailable"
        return result
    try:
        actual_root = Path(
            os.fsdecode(top_level.stdout.rstrip(b"\r\n")),
        ).resolve()
    except OSError:
        result["reason"] = "git_root_unavailable"
        return result
    if actual_root != root.resolve():
        result["reason"] = "source_root_not_git_top_level"
        return result

    relative_text = expected_relative_path.as_posix()
    tree_entry = git(
        "ls-tree",
        "-z",
        "--full-tree",
        "HEAD",
        "--",
        relative_text,
    )
    if (
        tree_entry is None
        or tree_entry.returncode != 0
        or not tree_entry.stdout
    ):
        result["reason"] = "file_not_tracked"
        return result
    try:
        metadata, entry_path = tree_entry.stdout.rstrip(b"\0").split(b"\t", 1)
        mode, object_type, tree_oid = metadata.split(b" ", 2)
    except ValueError:
        result["reason"] = "tree_entry_invalid"
        return result
    if (
        entry_path != relative_text.encode("utf-8")
        or object_type != b"blob"
        or mode not in {b"100644", b"100755"}
    ):
        result["reason"] = "file_not_regular_at_head"
        return result

    index_entry = git("ls-files", "--stage", "-z", "--", relative_text)
    if (
        index_entry is None
        or index_entry.returncode != 0
        or not index_entry.stdout
    ):
        result["reason"] = "index_mismatch"
        return result
    try:
        index_metadata, index_path = (
            index_entry.stdout.rstrip(b"\0").split(b"\t", 1)
        )
        index_mode, index_oid, index_stage = index_metadata.split(b" ", 2)
    except ValueError:
        result["reason"] = "index_mismatch"
        return result
    if (
        index_path != relative_text.encode("utf-8")
        or index_mode != mode
        or index_oid.lower() != tree_oid.lower()
        or index_stage != b"0"
    ):
        result["reason"] = "index_mismatch"
        return result

    committed = git("cat-file", "blob", f"HEAD:{relative_text}")
    if committed is None or committed.returncode != 0:
        result["reason"] = "blob_unavailable"
        return result
    try:
        working_bytes = candidate.read_bytes()
    except OSError:
        result["reason"] = "worktree_unreadable"
        return result
    if (
        working_bytes.replace(b"\r\n", b"\n")
        != committed.stdout.replace(b"\r\n", b"\n")
    ):
        result["reason"] = "worktree_mismatch"
        return result

    result["verified"] = True
    result["reason"] = "verified"
    result["blob_oid"] = tree_oid.decode("ascii").lower()
    return result


def _retained_json_source_binding(
    report: Mapping[str, Any],
    path: Path | None,
    *,
    expected_relative_path: Path,
    source_root: Path,
) -> dict[str, Any]:
    result = _retained_file_source_binding(
        path,
        expected_relative_path=expected_relative_path,
        source_root=source_root,
    )
    if result.get("verified") is not True or path is None:
        return result
    candidate = path if path.is_absolute() else source_root / path
    retained = _read_json(candidate)
    if not _json_values_exact(retained, dict(report)):
        result["verified"] = False
        result["reason"] = "content_mismatch"
    return result


def _remaining_package(
    phase_synthesis_refresh: dict[str, Any],
    package_id: str,
) -> dict[str, Any]:
    for package in phase_synthesis_refresh.get("remaining_work_packages") or []:
        if isinstance(package, dict) and package.get("id") == package_id:
            return dict(package)
    return {}


def _landed_package(
    phase_synthesis_refresh: dict[str, Any],
    package_id: str,
) -> dict[str, Any]:
    for package in phase_synthesis_refresh.get("landed_work_packages") or []:
        if isinstance(package, dict) and package.get("id") == package_id:
            return dict(package)
    return {}


def _source_release_soak_package(
    phase_synthesis_refresh: dict[str, Any],
) -> dict[str, Any]:
    return _remaining_package(
        phase_synthesis_refresh,
        RELEASE_SOAK_TASK_ID,
    ) or _landed_package(
        phase_synthesis_refresh,
        RELEASE_SOAK_TASK_ID,
    )


def _chosen_option(pack: Mapping[str, Any]) -> dict[str, Any]:
    signoff = pack.get("operator_signoff")
    chosen = ""
    if isinstance(signoff, Mapping):
        chosen = str(signoff.get("chosen_option") or "")
    for option in pack.get("options") or []:
        if isinstance(option, Mapping) and option.get("id") == chosen:
            return dict(option)
    return {}


def _operator_signoff_timestamp(value: object) -> dt.datetime | None:
    if not isinstance(value, str):
        return None
    prefix, first_separator, remainder = value.partition(":")
    operator_id, second_separator, timestamp = remainder.partition(":")
    if not (
        prefix == "operator"
        and bool(first_separator)
        and bool(second_separator)
        and re.fullmatch(r"[A-Za-z0-9._-]+", operator_id) is not None
    ):
        return None
    return _try_parse_timestamp(timestamp)


def _decision_pack_summary(
    path: Path,
    *,
    expected_decision_id: str,
    expected_category: str,
    checked_at_utc: dt.datetime,
) -> dict[str, Any]:
    blockers: list[str] = []
    summary: dict[str, Any] = {
        "path": str(path),
        "expected_decision_id": expected_decision_id,
        "expected_category": expected_category,
        "signed": False,
        "blockers": blockers,
    }
    try:
        pack = load_pack(path)
    except (OSError, DecisionPackError) as exc:
        blockers.append("operator_decision_pack_missing_or_invalid")
        summary["error"] = str(exc)
        return summary

    signoff = pack.get("operator_signoff")
    chosen_option = ""
    signed_by = ""
    if isinstance(signoff, Mapping):
        chosen_option = str(signoff.get("chosen_option") or "")
        signed_by = str(signoff.get("signed_by") or "")
    signed_at = _operator_signoff_timestamp(signed_by)
    created_at = _try_parse_timestamp(pack.get("created_utc"))
    temporal_signoff_valid = (
        signed_at is not None
        and created_at is not None
        and created_at <= signed_at <= checked_at_utc
    )

    summary.update({
        "decision_id": pack.get("decision_id"),
        "category": pack.get("category"),
        "target_version": pack.get("target_version"),
        "commit": pack.get("commit"),
        "chosen_option": chosen_option,
        "signed_by": signed_by,
        "created_at_utc": (
            _format_utc(created_at) if created_at is not None else None
        ),
        "signed_at_utc": (
            _format_utc(signed_at) if signed_at is not None else None
        ),
        "signed": is_signed(pack) and temporal_signoff_valid,
        "structural_invariants": dict(pack.get("structural_invariants") or {}),
    })
    if pack.get("decision_id") != expected_decision_id:
        blockers.append("operator_decision_pack_id_mismatch")
    if pack.get("category") != expected_category:
        blockers.append("operator_decision_pack_category_mismatch")
    if not is_signed(pack) or signed_at is None:
        blockers.append("operator_decision_pack_unsigned")
    elif created_at is None:
        blockers.append("operator_decision_pack_created_at_invalid")
    elif signed_at < created_at:
        blockers.append("operator_decision_pack_signoff_predates_creation")
    elif signed_at > checked_at_utc:
        blockers.append("operator_decision_pack_signoff_in_future")

    option = _chosen_option(pack)
    data = option.get("data") if isinstance(option, Mapping) else None
    if isinstance(data, Mapping):
        summary["chosen_option_data"] = dict(data)
    return summary


def _docker_pack_summary(
    path: Path,
    *,
    policy_evidence: Mapping[str, Any],
    policy_evidence_path: Path | None,
    expected_subject_commit: object,
    source_root: Path,
) -> dict[str, Any]:
    """Summarize only authority proven by the hardened Docker-policy report.

    The boundary report must not independently reinterpret a YAML decision
    pack. The Docker-policy evaluator already binds that pack to its exact
    target, subject commit, Git index/worktree bytes, immutable HEAD blob,
    storage history, and report semantics.
    """

    expected_decision_id = "docker-v3-12-0-stable-promotion"
    expected_pack_path = (
        source_root.resolve() / DEFAULT_DOCKER_DECISION_PACK
    ).resolve()
    blockers: list[str] = []
    pack_candidate = path if path.is_absolute() else source_root / path
    try:
        resolved_pack_path = pack_candidate.resolve()
    except OSError:
        resolved_pack_path = pack_candidate

    exact_expected_commit = (
        expected_subject_commit
        if (
            isinstance(expected_subject_commit, str)
            and COMMIT_PATTERN.fullmatch(expected_subject_commit) is not None
        )
        else None
    )
    policy_blockers = evaluate_docker_policy_report(
        dict(policy_evidence),
        expected_commit=exact_expected_commit,
        target_version=DOCKER_TARGET_VERSION,
        source_root=source_root,
    )
    authorization = policy_evidence.get("operator_authorization")
    if not isinstance(authorization, Mapping):
        authorization = {}
    source_binding = policy_evidence.get(
        "operator_authorization_source_binding",
    )
    if not isinstance(source_binding, Mapping):
        source_binding = {}

    chosen_option = authorization.get("chosen_option")
    move_latest = authorization.get("move_latest")
    commit = policy_evidence.get("commit")
    signed = bool(
        authorization.get("operator_id")
        and authorization.get("authorized_at_utc")
    )
    summary: dict[str, Any] = {
        "path": str(path),
        "expected_path": str(DEFAULT_DOCKER_DECISION_PACK),
        "policy_evidence_path": (
            str(policy_evidence_path)
            if policy_evidence_path is not None
            else ""
        ),
        "expected_decision_id": expected_decision_id,
        "expected_category": "docker_promotion",
        "decision_id": authorization.get("decision_id"),
        "category": "docker_promotion",
        "target_version": policy_evidence.get("target_version"),
        "commit": commit,
        "expected_subject_commit": expected_subject_commit,
        "chosen_option": chosen_option,
        "signed_by": authorization.get("operator_id", ""),
        "signed": signed,
        "chosen_option_data": {
            "moves_latest": (
                True
                if move_latest == "yes"
                else False
                if move_latest == "no"
                else None
            ),
        },
        "policy_schema_version": policy_evidence.get("schema_version"),
        "policy": policy_evidence.get("docker_stable_policy"),
        "policy_reported_blockers": policy_evidence.get("blockers"),
        "policy_evaluation_blockers": policy_blockers,
        "operator_authorization_source_binding": dict(source_binding),
        "blockers": blockers,
    }

    if resolved_pack_path != expected_pack_path:
        blockers.append("operator_decision_pack_path_mismatch")
    if policy_evidence.get("schema_version") != DOCKER_POLICY_SCHEMA_VERSION:
        blockers.append("docker_policy_evidence_schema_invalid")
    if policy_evidence.get("target_version") != DOCKER_TARGET_VERSION:
        blockers.append("docker_target_version_not_exact")
    if not isinstance(commit, str) or COMMIT_PATTERN.fullmatch(commit) is None:
        blockers.append("docker_commit_scope_not_exact")
    if exact_expected_commit is None:
        blockers.append("release_soak_subject_commit_invalid")
    elif commit != exact_expected_commit:
        blockers.append("docker_policy_soak_commit_mismatch")
    if authorization.get("commit") != commit:
        blockers.append("docker_authorization_commit_mismatch")
    if authorization.get("target_version") != DOCKER_TARGET_VERSION:
        blockers.append("docker_authorization_target_mismatch")
    if authorization.get("decision_id") != expected_decision_id:
        blockers.append("operator_decision_pack_id_mismatch")
    if not signed:
        blockers.append("operator_decision_pack_unsigned")
    if chosen_option != "ghcr_stable_only":
        blockers.append("docker_promotion_choice_not_ghcr_stable_only")
    if move_latest != "no":
        blockers.append("docker_latest_move_not_forbidden_by_pack")
    if source_binding.get("verified") is not True:
        blockers.append("docker_authorization_source_not_verified")
    if policy_evidence.get("blockers") != []:
        blockers.append("docker_policy_reported_blockers_present")
    if policy_evidence.get("docker_stable_policy") != "finalized":
        blockers.append("docker_policy_not_finalized")
    if policy_blockers:
        blockers.append("docker_policy_evidence_invalid")
    return summary


def _torch_pack_summary(
    path: Path,
    *,
    source_root: Path,
    checked_at_utc: dt.datetime,
) -> dict[str, Any]:
    summary = _decision_pack_summary(
        path,
        expected_decision_id="torch-cuda-vs-cpu",
        expected_category="dependency_security",
        checked_at_utc=checked_at_utc,
    )
    source_binding = _retained_file_source_binding(
        path,
        expected_relative_path=DEFAULT_TORCH_DECISION_PACK,
        source_root=source_root,
    )
    summary["source_binding"] = source_binding
    if source_binding.get("verified") is not True:
        summary["blockers"].append(
            "operator_decision_pack_source_not_verified:"
            + str(source_binding.get("reason") or "unknown")
        )
    return summary


def _release_gate_summary(report: dict[str, Any]) -> dict[str, Any]:
    raw_blockers = report.get("blockers")
    blockers_valid = (
        isinstance(raw_blockers, list)
        and all(isinstance(item, str) for item in raw_blockers)
    )
    nested_gate = report.get("gate")
    nested_blockers = (
        nested_gate.get("blockers")
        if isinstance(nested_gate, Mapping)
        else None
    )
    nested_consistent = (
        isinstance(nested_gate, Mapping)
        and nested_gate.get("decision") == report.get("release_gate_decision")
        and isinstance(nested_blockers, list)
        and all(isinstance(item, str) for item in nested_blockers)
        and nested_blockers == raw_blockers
    )
    return {
        "schema_version": report.get("schema_version"),
        "ok": report.get("ok") is True,
        "read_only": report.get("read_only") is True,
        "release_gate_decision": report.get("release_gate_decision"),
        "blockers": list(raw_blockers) if blockers_valid else [],
        "blockers_valid": blockers_valid,
        "nested_gate_consistent": nested_consistent,
        "release_gate_effect": report.get("release_gate_effect"),
        "release_boundary_all_false": (
            _release_boundary_is_exact_false(report.get("release_boundary"))
        ),
    }


def _source_phase_synthesis_summary(
    phase_synthesis_refresh: dict[str, Any],
) -> dict[str, Any]:
    remaining_package = _remaining_package(
        phase_synthesis_refresh,
        RELEASE_SOAK_TASK_ID,
    )
    landed_package = _landed_package(phase_synthesis_refresh, RELEASE_SOAK_TASK_ID)
    return {
        "schema_version": phase_synthesis_refresh.get("schema_version"),
        "sprint_id": phase_synthesis_refresh.get("sprint_id"),
        "generated_at_utc": phase_synthesis_refresh.get("generated_at_utc"),
        "ok": phase_synthesis_refresh.get("ok") is True,
        "release_boundary_all_false": (
            _release_boundary_is_exact_false(
                phase_synthesis_refresh.get("release_boundary"),
            )
        ),
        "remaining_release_soak_package": {
            "id": RELEASE_SOAK_TASK_ID,
            "status": remaining_package.get("status"),
            "owner": remaining_package.get("owner"),
        },
        "landed_release_soak_package": {
            "id": RELEASE_SOAK_TASK_ID,
            "status": landed_package.get("status"),
            "owner": landed_package.get("owner"),
        },
    }


def _revalidate_local_artifact_evidence(
    evidence: Mapping[str, Any],
    *,
    source_root: Path,
) -> dict[str, Any]:
    try:
        from tools.collect_soak_evidence import (
            revalidate_local_artifact_evidence,
        )
    except (ImportError, AttributeError) as exc:
        return {
            "verified": False,
            "reason": f"revalidator_unavailable:{exc.__class__.__name__}",
            "mismatches": [],
        }
    return revalidate_local_artifact_evidence(
        dict(evidence),
        source_root=source_root,
    )


def _release_soak_summary(
    evidence: Mapping[str, Any],
    *,
    path: Path | None,
    source_root: Path,
    checked_at_utc: dt.datetime,
) -> dict[str, Any]:
    blockers: list[str] = []
    source_root_path = _lexical_absolute_path(source_root)
    expected_path = source_root_path / DEFAULT_SOAK_EVIDENCE
    candidate = path if path is not None else Path()
    candidate = (
        candidate
        if candidate.is_absolute()
        else source_root_path / candidate
    )
    candidate_path = _lexical_absolute_path(candidate)

    commit = evidence.get("commit")
    started_at = _try_parse_timestamp(evidence.get("started_at_utc"))
    ended_at = _try_parse_timestamp(evidence.get("ended_at_utc"))
    duration = evidence.get("duration_hours")
    duration_is_finite = False
    if (
        isinstance(duration, (int, float))
        and not isinstance(duration, bool)
    ):
        try:
            duration_is_finite = math.isfinite(float(duration))
        except OverflowError:
            duration_is_finite = False
    if path is None or candidate_path != expected_path:
        blockers.append("canonical_path_mismatch")
    elif _path_has_indirection(candidate_path, root=source_root_path):
        blockers.append("canonical_path_indirection")
    elif not _is_regular_file(candidate_path):
        blockers.append("canonical_file_not_regular")
    else:
        try:
            retained = json.loads(
                candidate_path.read_text(encoding="utf-8"),
                object_pairs_hook=_unique_json_object,
            )
        except (OSError, ValueError):
            blockers.append("canonical_file_unreadable")
        else:
            if not isinstance(retained, Mapping):
                blockers.append("canonical_file_not_object")
            elif not _json_values_exact(dict(retained), dict(evidence)):
                blockers.append("canonical_content_mismatch")
    if evidence.get("schema_version") != RELEASE_SOAK_SCHEMA_VERSION:
        blockers.append("schema_version_invalid")
    if evidence.get("target_version") != DOCKER_TARGET_VERSION:
        blockers.append("target_version_mismatch")
    if evidence.get("collection_mode") != LOCAL_ARTIFACT_COLLECTION_MODE:
        blockers.append("collection_mode_invalid")
    local_revalidation = _revalidate_local_artifact_evidence(
        evidence,
        source_root=source_root,
    )
    if local_revalidation.get("verified") is not True:
        blockers.append("local_artifacts_not_verified")
    if not isinstance(commit, str) or COMMIT_PATTERN.fullmatch(commit) is None:
        blockers.append("subject_commit_invalid")
    if evidence.get("result") != "pass":
        blockers.append("result_not_pass")
    if started_at is None:
        blockers.append("started_at_invalid")
    if ended_at is None:
        blockers.append("ended_at_invalid")
    if started_at is not None and ended_at is not None and ended_at <= started_at:
        blockers.append("ended_before_started")
    if started_at is not None and started_at > REQUIRED_SOAK_START_UTC:
        blockers.append("started_after_required_window_start")
    if ended_at is not None and ended_at < REQUIRED_SOAK_END_UTC:
        blockers.append("ended_before_required_window_end")
    if ended_at is not None and ended_at > checked_at_utc:
        blockers.append("ended_at_in_future")
    if not duration_is_finite or duration < REQUIRED_SOAK_DURATION_HOURS:
        blockers.append("duration_insufficient")
    if started_at is not None and ended_at is not None:
        elapsed_hours = (ended_at - started_at).total_seconds() / 3600
        expected_duration: int | float = (
            int(elapsed_hours)
            if elapsed_hours.is_integer()
            else round(elapsed_hours, 3)
        )
        if (
            elapsed_hours < REQUIRED_SOAK_DURATION_HOURS
            and duration_is_finite
            and duration >= REQUIRED_SOAK_DURATION_HOURS
        ):
            blockers.append("elapsed_duration_insufficient")
        if duration_is_finite and duration != expected_duration:
            blockers.append("duration_mismatch")
    silent_failures = evidence.get("silent_failures")
    if type(silent_failures) is not int or silent_failures != 0:
        blockers.append("silent_failures_nonzero")
    if evidence.get("error_log_clean") is not True:
        blockers.append("error_log_not_clean")
    if evidence.get("docker_stable_policy") != "finalized":
        blockers.append("docker_policy_not_finalized")
    for field in REQUIRED_SOAK_STATUS_FIELDS:
        if evidence.get(field) != "pass":
            blockers.append(f"{field}_not_pass")

    return {
        "path": str(path) if path is not None else "",
        "expected_path": str(DEFAULT_SOAK_EVIDENCE),
        "schema_version": evidence.get("schema_version"),
        "target_version": evidence.get("target_version"),
        "collection_mode": evidence.get("collection_mode"),
        "commit": commit,
        "result": evidence.get("result"),
        "duration_hours": duration if duration_is_finite else None,
        "local_artifact_revalidation": {
            "verified": local_revalidation.get("verified") is True,
            "reason": local_revalidation.get("reason"),
            "mismatches": list(local_revalidation.get("mismatches") or []),
        },
        "blockers": blockers,
    }


def _collect_blockers(
    *,
    phase_synthesis_refresh: dict[str, Any],
    release_gate_recheck: dict[str, Any],
    release_soak: dict[str, Any],
    torch_pack: dict[str, Any],
    docker_pack: dict[str, Any],
) -> list[str]:
    blockers: list[str] = []
    phase_blockers = phase_synthesis_refresh.get("blockers")
    if (
        phase_synthesis_refresh.get("schema_version")
        != PHASE_SYNTHESIS_SCHEMA_VERSION
    ):
        blockers.append("phase_synthesis_schema_invalid")
    if not (
        isinstance(phase_blockers, list)
        and all(isinstance(item, str) for item in phase_blockers)
    ):
        blockers.append("phase_synthesis_blockers_invalid")
    elif phase_blockers:
        blockers.append("phase_synthesis_reported_blockers_present")
    if phase_synthesis_refresh.get("ok") is not True:
        blockers.append("phase_synthesis_refresh_not_ok")
    if not _release_boundary_is_exact_false(
        phase_synthesis_refresh.get("release_boundary"),
    ):
        blockers.append("phase_synthesis_release_boundary_mutated")

    remaining_package = _remaining_package(
        phase_synthesis_refresh,
        RELEASE_SOAK_TASK_ID,
    )
    landed_package = _landed_package(phase_synthesis_refresh, RELEASE_SOAK_TASK_ID)
    release_soak_ready = (
        remaining_package.get("status") == "ready_for_release_boundary_review"
        or landed_package.get("status")
        == "complete_release_boundary_readiness_recorded"
    )
    if release_soak_ready is not True:
        blockers.append("release_soak_package_not_ready_for_boundary_review")

    gate = _release_gate_summary(release_gate_recheck)
    if gate["schema_version"] != RELEASE_GATE_RECHECK_SCHEMA_VERSION:
        blockers.append("release_gate_recheck_schema_invalid")
    if gate["blockers_valid"] is not True:
        blockers.append("release_gate_recheck_blockers_invalid")
    if gate["nested_gate_consistent"] is not True:
        blockers.append("release_gate_recheck_nested_gate_mismatch")
    if gate["ok"] is not True:
        blockers.append("release_gate_recheck_report_not_ok")
    if gate["read_only"] is not True:
        blockers.append("release_gate_recheck_not_read_only")
    if gate["release_gate_effect"] != "none":
        blockers.append("release_gate_effect_not_none")
    if gate["release_boundary_all_false"] is not True:
        blockers.append("release_gate_release_boundary_mutated")
    if gate["release_gate_decision"] != "pass" or gate["blockers"]:
        blockers.append("release_gate_not_passed")

    for blocker in release_soak.get("blockers") or []:
        blockers.append(f"release_soak_{blocker}")
    for blocker in torch_pack.get("blockers") or []:
        blockers.append(f"torch_{blocker}")
    for blocker in docker_pack.get("blockers") or []:
        blockers.append(f"docker_{blocker}")
    return blockers


def _release_decision_packet(
    *,
    phase_synthesis_refresh: dict[str, Any],
    release_gate_recheck: dict[str, Any],
    ready: bool,
) -> dict[str, Any]:
    package = _source_release_soak_package(phase_synthesis_refresh)
    return {
        "schema_version": DECISION_PACKET_SCHEMA_VERSION,
        "id": FINALIZATION_TASK_ID,
        "decision_status": (
            "operator_finalization_required"
            if ready
            else "release_boundary_readiness_blocked"
        ),
        "default_recommendation": "hold_no_release_boundary_change",
        "source_status": package.get("status"),
        "source_acceptance": package.get("acceptance"),
        "release_gate_decision": release_gate_recheck.get("release_gate_decision"),
        "release_boundary_effect_before_followup": "none",
        "operator_input_required": True,
        "operator_finalization_required": True,
        "decision_options": [
            {
                "id": "hold_no_release_boundary_change",
                "summary": "Keep all release-boundary guardrails closed.",
                "operator_action_required": False,
                "tag_creation_allowed": False,
                "docker_latest_move_allowed": False,
                "docker_stable_move_allowed": False,
                "stable_release_claim_allowed": False,
                "external_effect_authority_change_allowed": False,
                "next_status": "hold_operator_finalization_required",
            },
            {
                "id": "operator_finalizes_release_boundary_separately",
                "summary": (
                    "Operator may perform a separate release finalization; "
                    "this report still performs no release action."
                ),
                "operator_action_required": True,
                "tag_creation_allowed": False,
                "docker_latest_move_allowed": False,
                "docker_stable_move_allowed": False,
                "stable_release_claim_allowed": False,
                "external_effect_authority_change_allowed": False,
                "next_status": "operator_release_finalization_required",
                "followup_requirements": [
                    "operator_only_release_finalization",
                    "fresh_exact_head_ci",
                    "rco_and_bridge_preflight",
                    "signed_tag_or_release_receipt_after_operator_action",
                ],
            },
        ],
    }


def build_report(
    *,
    phase_synthesis_refresh: dict[str, Any],
    release_gate_recheck: dict[str, Any],
    phase_synthesis_refresh_path: Path | None = None,
    release_gate_recheck_path: Path | None = None,
    torch_decision_pack: Path,
    docker_decision_pack: Path,
    docker_policy_evidence: Mapping[str, Any],
    soak_evidence: Mapping[str, Any],
    docker_policy_evidence_path: Path | None = None,
    soak_evidence_path: Path | None = None,
    source_root: Path = ROOT,
    checked_at_utc: dt.datetime | None = None,
) -> dict[str, Any]:
    actual_now_utc = _utc_now()
    checked_at_utc = checked_at_utc or actual_now_utc
    if checked_at_utc.tzinfo is None:
        checked_at_utc = checked_at_utc.replace(tzinfo=dt.UTC)
    else:
        checked_at_utc = checked_at_utc.astimezone(dt.UTC)
    phase_binding = _retained_json_source_binding(
        phase_synthesis_refresh,
        phase_synthesis_refresh_path,
        expected_relative_path=DEFAULT_PHASE_SYNTHESIS_REFRESH,
        source_root=source_root,
    )
    gate_binding = _retained_json_source_binding(
        release_gate_recheck,
        release_gate_recheck_path,
        expected_relative_path=DEFAULT_RELEASE_GATE_RECHECK,
        source_root=source_root,
    )
    torch_pack = _torch_pack_summary(
        torch_decision_pack,
        source_root=source_root,
        checked_at_utc=checked_at_utc,
    )
    release_soak = _release_soak_summary(
        soak_evidence,
        path=soak_evidence_path,
        source_root=source_root,
        checked_at_utc=checked_at_utc,
    )
    docker_pack = _docker_pack_summary(
        docker_decision_pack,
        policy_evidence=docker_policy_evidence,
        policy_evidence_path=docker_policy_evidence_path,
        expected_subject_commit=release_soak.get("commit"),
        source_root=source_root,
    )
    blockers = _collect_blockers(
        phase_synthesis_refresh=phase_synthesis_refresh,
        release_gate_recheck=release_gate_recheck,
        release_soak=release_soak,
        torch_pack=torch_pack,
        docker_pack=docker_pack,
    )
    if checked_at_utc > actual_now_utc:
        blockers.append("checked_at_utc_in_future")
    for label, binding in (
        ("phase_synthesis", phase_binding),
        ("release_gate_recheck", gate_binding),
    ):
        if binding.get("verified") is not True:
            blockers.append(
                f"{label}_source_not_verified:"
                + str(binding.get("reason") or "unknown")
            )
    ready = not blockers
    return {
        "schema_version": SCHEMA_VERSION,
        "checked_at_utc": _format_utc(checked_at_utc),
        "ok": ready,
        "release_boundary_status": (
            "ready_for_operator_finalization"
            if ready
            else "hold_release_boundary_review_required"
        ),
        "release_boundary_blockers": blockers,
        "operator_finalization_required": True,
        "source_phase_synthesis_refresh": _source_phase_synthesis_summary(
            phase_synthesis_refresh
        ),
        "source_bindings": {
            "phase_synthesis_refresh": phase_binding,
            "release_gate_recheck": gate_binding,
        },
        "source_release_gate_readonly_recheck": _release_gate_summary(
            release_gate_recheck
        ),
        "source_release_soak_evidence": release_soak,
        "operator_decision_packs": {
            "torch_cuda_vs_cpu": torch_pack,
            "docker_latest_promotion": docker_pack,
        },
        "release_decision_packet": _release_decision_packet(
            phase_synthesis_refresh=phase_synthesis_refresh,
            release_gate_recheck=release_gate_recheck,
            ready=ready,
        ),
        "release_boundary_guardrails": {
            "release_boundary_effect": "none",
            "tag_creation_applied": False,
            "docker_latest_move_applied": False,
            "docker_stable_move_applied": False,
            "stable_release_claim_applied": False,
            "external_effect_authority_change_applied": False,
            "requires_operator_only_finalization": True,
        },
        "release_boundary": dict(FALSE_RELEASE_BOUNDARY),
        "read_only_invariants": {
            "no_tag_created": True,
            "no_docker_latest_moved": True,
            "no_docker_stable_moved": True,
            "no_stable_release_claim": True,
            "no_external_effect_authority_change": True,
            "release_boundary_effect": "none",
        },
    }


def build_report_from_paths(
    *,
    phase_synthesis_refresh_path: Path,
    release_gate_recheck_path: Path,
    torch_decision_pack: Path,
    docker_decision_pack: Path,
    docker_policy_evidence_path: Path,
    soak_evidence_path: Path,
    source_root: Path = ROOT,
    checked_at_utc: dt.datetime | None = None,
) -> dict[str, Any]:
    return build_report(
        phase_synthesis_refresh=_read_json(phase_synthesis_refresh_path),
        release_gate_recheck=_read_json(release_gate_recheck_path),
        phase_synthesis_refresh_path=phase_synthesis_refresh_path,
        release_gate_recheck_path=release_gate_recheck_path,
        torch_decision_pack=torch_decision_pack,
        docker_decision_pack=docker_decision_pack,
        docker_policy_evidence=_read_json(docker_policy_evidence_path),
        soak_evidence=_read_json(soak_evidence_path),
        docker_policy_evidence_path=docker_policy_evidence_path,
        soak_evidence_path=soak_evidence_path,
        source_root=source_root,
        checked_at_utc=checked_at_utc,
    )


def strict_exit_code(report: dict[str, Any]) -> int:
    return 0 if report.get("ok") is True else STRICT_BLOCKED_EXIT_CODE


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase-synthesis-refresh",
        type=Path,
        default=DEFAULT_PHASE_SYNTHESIS_REFRESH,
    )
    parser.add_argument(
        "--release-gate-recheck",
        type=Path,
        default=DEFAULT_RELEASE_GATE_RECHECK,
    )
    parser.add_argument(
        "--torch-decision-pack",
        type=Path,
        default=DEFAULT_TORCH_DECISION_PACK,
    )
    parser.add_argument(
        "--docker-decision-pack",
        type=Path,
        default=DEFAULT_DOCKER_DECISION_PACK,
    )
    parser.add_argument(
        "--docker-policy-evidence",
        type=Path,
        default=DEFAULT_DOCKER_POLICY_EVIDENCE,
        help=(
            "Hardened v2 Docker-policy evidence that binds the decision pack "
            "to its exact target, subject commit, and retained Git source."
        ),
    )
    parser.add_argument(
        "--soak-evidence",
        type=Path,
        default=DEFAULT_SOAK_EVIDENCE,
        help=(
            "Canonical release-soak evidence whose subject commit must match "
            "the Docker-policy evidence exactly."
        ),
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        default=ROOT,
        help="Exact Git repository root used to validate Docker evidence.",
    )
    parser.add_argument(
        "--checked-at-utc",
        type=_parse_timestamp,
        help="Override report timestamp, ISO-8601 UTC.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Return a blocked exit code when readiness blockers are present.",
    )
    args = parser.parse_args(argv)

    report = build_report_from_paths(
        phase_synthesis_refresh_path=args.phase_synthesis_refresh,
        release_gate_recheck_path=args.release_gate_recheck,
        torch_decision_pack=args.torch_decision_pack,
        docker_decision_pack=args.docker_decision_pack,
        docker_policy_evidence_path=args.docker_policy_evidence,
        soak_evidence_path=args.soak_evidence,
        source_root=args.source_root,
        checked_at_utc=args.checked_at_utc,
    )
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    if args.json:
        print(encoded, end="")
    return strict_exit_code(report) if args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
