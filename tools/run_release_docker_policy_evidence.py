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
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any, Mapping

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.operator_decision_pack import DecisionPackError, is_signed, load_pack


SCHEMA_VERSION = "waggledance.release_docker_policy.v2"
AUTH_SCHEMA_VERSION = "waggledance.operator_docker_stable_authorization.v1"
DOCKER_DECISION_PACK_ID = "docker-latest-promotion"
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
REQUIRED_DECISION_PACK_INVARIANTS = (
    "latest_move_is_operator_only",
    "agent_must_not_self_resolve",
)
DEFAULT_OUTPUT = (
    Path("docs")
    / "runs"
    / "release_soak_evidence"
    / "v3.12.0_docker_policy.json"
)
DEFAULT_TARGET_VERSION = "v3.12.0"
CANONICAL_ENTRYPOINT = ["python", "-m", "waggledance.adapters.cli.start_runtime"]
CANONICAL_SCRIPT = "waggledance.adapters.cli.start_runtime:main"
REQUIRED_SOURCE_FILES = (
    ".github/workflows/release-docker.yml",
    ".github/workflows/release-docker-stable.yml",
    "Dockerfile",
    "docker-compose.yml",
    "pyproject.toml",
    "docs/deployment/DOCKER_QUICKSTART.md",
    "tools/run_release_docker_policy_evidence.py",
    "tools/operator_decision_pack.py",
)
SOURCE_TEXT_NORMALIZATION = "utf8_no_nul_or_bare_cr_crlf_to_lf_v1"
REQUIRED_STATIC_CHECKS = (
    "release_workflow_templates_exact",
    "alpha_workflow_dispatch_only",
    "release_concurrency_shared",
    "alpha_dispatch_inputs_env_only",
    "alpha_strict_tag_authority",
    "alpha_alias_policy_fail_closed",
    "alpha_build_digest_exported",
    "alpha_digest_smoke",
    "alpha_alias_promotion_after_smoke",
    "alpha_alias_sources_tested_digest",
    "alpha_alias_verification_fail_closed",
    "alpha_no_alias_mutation_before_smoke",
    "workflow_dispatch_only",
    "stable_dispatch_inputs_env_only",
    "stable_strict_tag_authority",
    "tag_input_required",
    "stable_tag_shape_guard",
    "prerelease_tag_refused",
    "move_latest_operator_gated",
    "move_latest_default_no",
    "ghcr_primary",
    "canonical_image_pushed",
    "stable_build_digest_exported",
    "stable_digest_smoke",
    "stable_alias_promotion_after_smoke",
    "stable_alias_sources_tested_digest",
    "stable_no_alias_mutation_before_smoke",
    "canonical_tag_idempotent_fail_closed",
    "alias_prestate_required",
    "alias_rollback_restores_and_verifies",
    "imagetools_prefer_index_false",
    "stable_alias_tagged",
    "profile_aliases_opt_in",
    "stable_profile_s_smoke",
    "stable_alias_smoke",
    "stable_alias_fail_closed",
    "latest_alias_smoke_if_moved",
    "dockerfile_entrypoint_canonical",
    "compose_entrypoint_canonical",
    "pyproject_script_canonical",
)

APPROVED_WORKFLOW_SHA256 = {
    ".github/workflows/release-docker.yml": (
        "sha256:2948b20be41eb5e37ab026c5368ee854a54c86d298debe7c6839fc608434a442"
    ),
    ".github/workflows/release-docker-stable.yml": (
        "sha256:e7f2b809a7bc80b6692027bdc99de8bd6e5f66f3c47076940768305f2c12bb14"
    ),
}


def _trusted_git_candidates() -> tuple[Path, ...]:
    """Return platform-owned Git locations without consulting PATH or GIT_* env."""

    if os.name == "nt":
        return (
            Path(r"C:\Program Files\Git\cmd\git.exe"),
            Path(r"C:\Program Files\Git\bin\git.exe"),
        )
    return (
        Path("/usr/bin/git"),
        Path("/usr/local/bin/git"),
        Path("/opt/homebrew/bin/git"),
    )


def _trusted_git_executable() -> Path | None:
    for candidate in _trusted_git_candidates():
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if resolved.is_file():
            return resolved
    return None


def _sanitized_git_environment() -> dict[str, str]:
    """Remove repository/object/config overrides from evidence Git reads."""

    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
        }
    )
    return environment


def _format_utc(value: dt.datetime) -> str:
    normalized = value.astimezone(dt.UTC).replace(microsecond=0)
    return normalized.isoformat().replace("+00:00", "Z")


def _parse_utc(value: object) -> dt.datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)


def _current_commit(source_root: Path | str = Path(".")) -> str:
    value = _git(
        Path(source_root).resolve(),
        "rev-parse",
        "--verify",
        "HEAD^{commit}",
    )
    return value if isinstance(value, str) else ""


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


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


def _pack_scalar(pack: Mapping[str, Any], names: tuple[str, ...]) -> str:
    for name in names:
        value = pack.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def operator_authorization_from_decision_pack(
    path: Path | str,
    *,
    commit: str,
    target_version: str,
) -> dict[str, Any] | None:
    """Convert a signed Docker operator decision pack into authorization.

    Draft, malformed, unsigned, wrong-scope, or structurally weakened packs
    return None. That keeps the policy evidence in draft/fail-closed state.
    """

    try:
        pack = load_pack(path)
    except (OSError, DecisionPackError):
        return None
    if pack.get("decision_id") != DOCKER_DECISION_PACK_ID:
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

    pack_target = _pack_scalar(pack, ("target_version", "release_version"))
    if pack_target and pack_target != target_version:
        return None
    pack_commit = _pack_scalar(pack, ("commit", "target_commit", "subject_commit"))
    if pack_commit and pack_commit != commit:
        return None

    signoff = pack.get("operator_signoff")
    if not isinstance(signoff, Mapping):
        return None
    chosen = str(signoff.get("chosen_option", "") or "").strip()
    option_policy = DOCKER_DECISION_OPTIONS.get(chosen)
    signed = _operator_signoff(signoff.get("signed_by"))
    if option_policy is None or signed is None:
        return None
    operator_id, authorized_at_utc = signed

    return {
        "schema_version": AUTH_SCHEMA_VERSION,
        "target_version": target_version,
        "commit": commit,
        "stable_promotion_authorized": option_policy["stable_promotion_authorized"],
        "docker_promotion_deferred": option_policy["docker_promotion_deferred"],
        "move_latest": option_policy["move_latest"],
        "authorization_id": (
            f"decision-pack:{DOCKER_DECISION_PACK_ID}:{chosen}:{operator_id}"
        ),
        "authorized_at_utc": authorized_at_utc,
        "source": "operator_decision_pack",
        "decision_id": DOCKER_DECISION_PACK_ID,
        "chosen_option": chosen,
        "operator_id": operator_id,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _canonical_source_text_bytes(value: bytes) -> bytes:
    """Validate reviewed UTF-8 text and canonicalize checkout-only CRLF."""

    if b"\x00" in value:
        raise ValueError("reviewed source text contains NUL")
    try:
        value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("reviewed source text is not valid UTF-8") from exc
    canonical = value.replace(b"\r\n", b"\n")
    if b"\r" in canonical:
        raise ValueError("reviewed source text contains bare CR")
    return canonical


def _source_hash_maps(root: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Return authoritative semantic hashes and informational checkout hashes."""

    semantic: dict[str, str] = {}
    physical: dict[str, str] = {}
    for item in REQUIRED_SOURCE_FILES:
        path = root / item
        if not path.is_file():
            continue
        raw = path.read_bytes()
        physical[item] = _sha256_bytes(raw)
        try:
            semantic[item] = _sha256_bytes(_canonical_source_text_bytes(raw))
        except ValueError:
            # inspect_git_source_binding emits the fail-closed source_text_invalid
            # blocker. Omitting the semantic hash prevents invalid bytes from
            # masquerading as a reviewed text representation.
            continue
    return semantic, physical


def _normalized_text_sha256(path: Path) -> str:
    """Hash a reviewed text template with CRLF and LF treated identically."""

    value = _canonical_source_text_bytes(path.read_bytes())
    return _sha256_bytes(value)


def _git(
    root: Path,
    *args: str,
    text: bool = True,
) -> str | bytes | None:
    executable = _trusted_git_executable()
    if executable is None:
        return None
    try:
        completed = subprocess.run(
            [
                str(executable),
                "--no-replace-objects",
                "-c",
                "core.hooksPath=",
                "-C",
                str(root),
                *args,
            ],
            check=True,
            capture_output=True,
            text=text,
            env=_sanitized_git_environment(),
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    if text:
        return str(completed.stdout).strip()
    return completed.stdout


def inspect_git_runtime_provenance() -> dict[str, str]:
    """Describe the local trusted Git reader without making evidence host-bound."""

    executable = _trusted_git_executable()
    if executable is None:
        return {
            "policy": "platform_absolute_allowlist_v1",
            "executable": "",
            "executable_sha256": "",
        }
    return {
        "policy": "platform_absolute_allowlist_v1",
        "executable": str(executable),
        "executable_sha256": _sha256(executable),
    }


def inspect_git_source_binding(
    source_root: Path | str,
    commit: str,
) -> tuple[dict[str, Any], list[str]]:
    """Bind policy inputs to one exact Git HEAD and canonical text bytes."""

    root = Path(source_root).resolve()
    blockers: list[str] = []
    git_executable = _trusted_git_executable()
    if git_executable is None:
        blockers.append("source_git_executable_untrusted")
    top_level = _git(root, "rev-parse", "--show-toplevel")
    repository_root_verified = (
        isinstance(top_level, str)
        and str(Path(top_level).resolve()).casefold() == str(root).casefold()
    )
    if not repository_root_verified:
        blockers.append("source_root_not_git_toplevel")

    head = _git(root, "rev-parse", "HEAD")
    if not isinstance(head, str) or re.fullmatch(r"[0-9a-f]{40}", head) is None:
        head = ""
        blockers.append("source_git_head_missing")

    resolved_commit = ""
    if re.fullmatch(r"[0-9a-f]{40}", commit or "") is None:
        blockers.append("source_commit_not_full_sha")
    else:
        resolved = _git(root, "rev-parse", "--verify", f"{commit}^{{commit}}")
        if isinstance(resolved, str) and resolved == commit:
            resolved_commit = resolved
        else:
            blockers.append("source_commit_unresolvable")
    if head and resolved_commit and head != resolved_commit:
        blockers.append("source_head_commit_mismatch")

    blob_oids: dict[str, str] = {}
    blob_hashes: dict[str, str] = {}
    worktree_hashes: dict[str, str] = {}
    worktree_matches_commit: dict[str, bool] = {}
    for item in REQUIRED_SOURCE_FILES:
        path = root / item
        if path.is_file():
            try:
                worktree_bytes = _canonical_source_text_bytes(path.read_bytes())
                worktree_hashes[item] = _sha256_bytes(worktree_bytes)
            except (OSError, ValueError):
                worktree_bytes = None
                blocker = f"source_text_invalid:{item}"
                if blocker not in blockers:
                    blockers.append(blocker)
        else:
            worktree_bytes = None
            blockers.append(f"source_file_missing:{item}")

        blob_oid: str | bytes | None = None
        blob_bytes: str | bytes | None = None
        if resolved_commit:
            blob_oid = _git(root, "rev-parse", "--verify", f"{resolved_commit}:{item}")
            if isinstance(blob_oid, str) and re.fullmatch(r"[0-9a-f]{40}", blob_oid):
                blob_oids[item] = blob_oid
                blob_bytes = _git(root, "cat-file", "blob", blob_oid, text=False)
            else:
                blockers.append(f"source_blob_missing:{item}")
        if isinstance(blob_bytes, bytes):
            try:
                canonical_blob_bytes = _canonical_source_text_bytes(blob_bytes)
            except ValueError:
                canonical_blob_bytes = None
                blocker = f"source_text_invalid:{item}"
                if blocker not in blockers:
                    blockers.append(blocker)
            if canonical_blob_bytes is None:
                continue
            blob_hashes[item] = _sha256_bytes(canonical_blob_bytes)
            matches = worktree_bytes == canonical_blob_bytes
            worktree_matches_commit[item] = matches
            if not matches:
                blockers.append(f"source_worktree_blob_mismatch:{item}")

    binding = {
        "text_normalization": SOURCE_TEXT_NORMALIZATION,
        "repository_root_verified": repository_root_verified,
        "head": head,
        "commit": resolved_commit,
        "blob_oids": blob_oids,
        "blob_hashes": blob_hashes,
        "worktree_hashes": worktree_hashes,
        "worktree_matches_commit": worktree_matches_commit,
    }
    return binding, blockers


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        text = _canonical_source_text_bytes(path.read_bytes()).decode("utf-8")
        loaded = yaml.safe_load(text)
    except (OSError, ValueError, yaml.YAMLError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _workflow_on(workflow: dict[str, Any]) -> Any:
    return workflow.get("on", workflow.get(True, {}))


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    steps = job.get("steps", [])
    return [step for step in steps if isinstance(step, dict)] if isinstance(steps, list) else []


def _find_step(job: dict[str, Any], needle: str) -> dict[str, Any] | None:
    needle = needle.lower()
    for step in _steps(job):
        if needle in str(step.get("name", "")).lower():
            return step
    return None


def _job_needs(job: dict[str, Any]) -> set[str]:
    value = job.get("needs", [])
    if isinstance(value, str):
        return {value}
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return set(value)
    return set()


def _job_run(job: dict[str, Any]) -> str:
    return "\n".join(str(step.get("run", "")) for step in _steps(job))


def _code_text(job: dict[str, Any]) -> str:
    """Return executable-looking shell text with comment-only lines removed."""

    lines: list[str] = []
    for raw in _job_run(job).splitlines():
        if raw.lstrip().startswith("#"):
            continue
        lines.append(raw)
    return "\n".join(lines)


def _shell_commands(job: dict[str, Any], prefix: tuple[str, ...]) -> list[list[str]]:
    """Extract simple/continued shell commands by argv prefix.

    This deliberately examines parsed argv tokens rather than searching the run
    block for a digest string. A digest mentioned in a comment, echo, or an
    unrelated variable therefore cannot certify an imagetools source operand.
    """

    commands: list[list[str]] = []
    pending = ""
    for raw in _job_run(job).splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.endswith("\\"):
            pending += stripped[:-1] + " "
            continue
        logical = pending + stripped
        pending = ""
        try:
            tokens = shlex.split(logical, comments=True, posix=True)
        except ValueError:
            continue
        for index in range(0, len(tokens) - len(prefix) + 1):
            if tuple(tokens[index : index + len(prefix)]) != prefix:
                continue
            command: list[str] = []
            for token in tokens[index:]:
                normalized = token.rstrip(";")
                if normalized:
                    command.append(normalized)
                if token.endswith(";") or token in {"&&", "||"}:
                    break
            commands.append(command)
            break
    return commands


def _find_uses_step(job: dict[str, Any], needle: str) -> dict[str, Any] | None:
    for step in _steps(job):
        if needle in str(step.get("uses", "")):
            return step
    return None


def _step_env(step: object) -> dict[str, Any]:
    if not isinstance(step, dict):
        return {}
    value = step.get("env", {})
    return value if isinstance(value, dict) else {}


def _digest_build_contract(job: dict[str, Any], build_step: object) -> bool:
    outputs = job.get("outputs", {})
    build_with = build_step.get("with", {}) if isinstance(build_step, dict) else {}
    exporter = str(build_with.get("outputs", "")) if isinstance(build_with, dict) else ""
    return (
        isinstance(build_step, dict)
        and build_step.get("id") == "build"
        and "docker/build-push-action" in str(build_step.get("uses", ""))
        and isinstance(build_with, dict)
        and "type=image" in exporter
        and "push-by-digest=true" in exporter
        and "name-canonical=true" in exporter
        and "push=true" in exporter
        and "tags" not in build_with
        and isinstance(outputs, dict)
        and outputs.get("digest") == "${{ steps.build.outputs.digest }}"
    )


def _digest_smoke_contract(job: dict[str, Any]) -> bool:
    smoke_step = _find_step(job, "exact candidate digest")
    env = _step_env(smoke_step)
    run = _code_text({"steps": [smoke_step]} if isinstance(smoke_step, dict) else {})
    return (
        "build-candidate" in _job_needs(job)
        and env.get("TARGET_DIGEST")
        == "${{ needs.build-candidate.outputs.digest }}"
        and re.search(r'(?m)^\s*docker pull "\$REGISTRY_IMAGE@\$TARGET_DIGEST"\s*$', run)
        is not None
        and re.search(r'(?m)^\s*"\$REGISTRY_IMAGE@\$TARGET_DIGEST" \\?\s*$', run)
        is not None
        and "sha256:[0-9a-f]{64}" in run
    )


def _digest_promotion_contract(job: dict[str, Any]) -> bool:
    publish_step = _find_step(job, "transactionally move")
    env = _step_env(publish_step)
    commands = _shell_commands(
        {"steps": [publish_step]} if isinstance(publish_step, dict) else {},
        ("docker", "buildx", "imagetools", "create"),
    )
    allowed_sources = {
        "$REGISTRY_IMAGE@$TARGET_DIGEST",
        "$REGISTRY_IMAGE@$old_digest",
    }
    return (
        {"build-candidate", "smoke-candidate"}.issubset(_job_needs(job))
        and env.get("TARGET_DIGEST")
        == "${{ needs.build-candidate.outputs.digest }}"
        and len(commands) == 3
        and all("--prefer-index=false" in command for command in commands)
        and all(command[-1] in allowed_sources for command in commands)
        and sum(command[-1] == "$REGISTRY_IMAGE@$TARGET_DIGEST" for command in commands)
        == 2
        and sum(command[-1] == "$REGISTRY_IMAGE@$old_digest" for command in commands)
        == 1
    )


def _alias_verification_fail_closed(job: dict[str, Any]) -> bool:
    run = _code_text(job)
    return (
        "imagetools inspect" in run
        and "verify_digest" in run
        and "declare -A PRESTATE" in run
        and "trap rollback ERR" in run
        and "$REGISTRY_IMAGE@$old_digest" in run
        and "rollback verification failed" in run
        and "::error::" in run
        and "exit 1" in run
        and "::warning::" not in run
    )


def _dispatch_env_contract(job: dict[str, Any], expected: Mapping[str, str]) -> bool:
    authority = _find_step(job, "validate dispatch inputs")
    env = _step_env(authority)
    run = str(authority.get("run", "")) if isinstance(authority, dict) else ""
    return all(env.get(name) == value for name, value in expected.items()) and (
        "${{ inputs." not in run
    )


def _tag_authority_contract(
    validate_job: dict[str, Any],
    build_job: dict[str, Any],
    publish_job: dict[str, Any],
) -> bool:
    authority = _find_step(validate_job, "exact tag authority")
    run = _code_text({"steps": [authority]} if isinstance(authority, dict) else {})
    checkout = _find_uses_step(build_job, "actions/checkout")
    checkout_with = checkout.get("with", {}) if isinstance(checkout, dict) else {}
    verify_checkout = _find_step(build_job, "exact checked-out commit")
    verify_checkout_env = _step_env(verify_checkout)
    verify_checkout_run = str(verify_checkout.get("run", "")) if isinstance(verify_checkout, dict) else ""
    publish_checkout = _find_uses_step(publish_job, "actions/checkout")
    publish_checkout_with = (
        publish_checkout.get("with", {}) if isinstance(publish_checkout, dict) else {}
    )
    publish_step = _find_step(publish_job, "transactionally move")
    publish_env = _step_env(publish_step)
    publish_run = _code_text(
        {"steps": [publish_step]} if isinstance(publish_step, dict) else {}
    )
    outputs = validate_job.get("outputs", {})
    return (
        isinstance(outputs, dict)
        and outputs.get("tag_ref_sha") == "${{ steps.authority.outputs.tag_ref_sha }}"
        and outputs.get("commit_sha") == "${{ steps.authority.outputs.commit_sha }}"
        and 'tag_ref="refs/tags/$INPUT_TAG"' in run
        and 'git show-ref --verify --quiet "$tag_ref"' in run
        and 'git rev-parse --verify "$tag_ref"' in run
        and 'git rev-parse --verify "$tag_ref^{commit}"' in run
        and isinstance(checkout_with, dict)
        and checkout_with.get("ref")
        == "${{ needs.validate-tag.outputs.commit_sha }}"
        and verify_checkout_env.get("EXPECTED_COMMIT_SHA")
        == "${{ needs.validate-tag.outputs.commit_sha }}"
        and "git rev-parse HEAD" in verify_checkout_run
        and isinstance(publish_checkout_with, dict)
        and publish_checkout_with.get("ref")
        == "${{ needs.validate-tag.outputs.commit_sha }}"
        and publish_checkout_with.get("fetch-depth") == 0
        and publish_env.get("TARGET_TAG_REF_SHA")
        == "${{ needs.validate-tag.outputs.tag_ref_sha }}"
        and publish_env.get("TARGET_COMMIT_SHA")
        == "${{ needs.validate-tag.outputs.commit_sha }}"
        and 'git rev-parse --verify "$tag_ref"' in publish_run
        and 'git rev-parse --verify "$tag_ref^{commit}"' in publish_run
        and "tag authority changed after validation" in publish_run
    )


def inspect_static_policy(source_root: Path | str = Path(".")) -> dict[str, Any]:
    """Inspect static Docker release policy files without running Docker."""

    root = Path(source_root)
    alpha_workflow_path = root / ".github/workflows/release-docker.yml"
    workflow_path = root / ".github/workflows/release-docker-stable.yml"
    dockerfile_path = root / "Dockerfile"
    compose_path = root / "docker-compose.yml"
    pyproject_path = root / "pyproject.toml"

    alpha_workflow = (
        _load_yaml(alpha_workflow_path) if alpha_workflow_path.exists() else {}
    )
    workflow = _load_yaml(workflow_path) if workflow_path.exists() else {}
    workflow_template_hashes: dict[str, str] = {}
    for relative in APPROVED_WORKFLOW_SHA256:
        path = root / relative
        if not path.is_file():
            continue
        try:
            workflow_template_hashes[relative] = _normalized_text_sha256(path)
        except (OSError, ValueError):
            continue
    alpha_workflow_on = _workflow_on(alpha_workflow)
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
    alpha_jobs = (
        alpha_workflow.get("jobs", {})
        if isinstance(alpha_workflow.get("jobs"), dict)
        else {}
    )
    alpha_validate_job = (
        alpha_jobs.get("validate-tag", {})
        if isinstance(alpha_jobs.get("validate-tag"), dict)
        else {}
    )
    alpha_build_job = (
        alpha_jobs.get("build-candidate", {})
        if isinstance(alpha_jobs.get("build-candidate"), dict)
        else {}
    )
    alpha_smoke_job = (
        alpha_jobs.get("smoke-candidate", {})
        if isinstance(alpha_jobs.get("smoke-candidate"), dict)
        else {}
    )
    alpha_promote_job = (
        alpha_jobs.get("publish-tested-digest", {})
        if isinstance(alpha_jobs.get("publish-tested-digest"), dict)
        else {}
    )
    validate_job = jobs.get("validate-tag", {}) if isinstance(jobs.get("validate-tag"), dict) else {}
    build_job = jobs.get("build-candidate", {}) if isinstance(jobs.get("build-candidate"), dict) else {}
    smoke_job = jobs.get("smoke-candidate", {}) if isinstance(jobs.get("smoke-candidate"), dict) else {}
    promote_job = (
        jobs.get("publish-tested-digest", {})
        if isinstance(jobs.get("publish-tested-digest"), dict)
        else {}
    )

    validate_run = _code_text(validate_job)
    alpha_validate_run = _code_text(alpha_validate_job)
    build_run = _job_run(build_job)
    smoke_run = _job_run(smoke_job)
    promote_run = _job_run(promote_job)
    alpha_build_run = _job_run(alpha_build_job)
    alpha_smoke_run = _job_run(alpha_smoke_job)
    alpha_promote_run = _job_run(alpha_promote_job)
    alpha_build = _find_step(alpha_build_job, "untagged candidate by digest")
    canonical_build = _find_step(build_job, "untagged candidate by digest")
    canonical_build_with = (
        canonical_build.get("with", {}) if isinstance(canonical_build, dict) else {}
    )

    try:
        pyproject_text = _canonical_source_text_bytes(
            pyproject_path.read_bytes()
        ).decode("utf-8")
        pyproject = tomllib.loads(pyproject_text)
    except (OSError, ValueError, tomllib.TOMLDecodeError):
        pyproject = {}
    try:
        compose = _load_yaml(compose_path)
    except (OSError, yaml.YAMLError):
        compose = {}
    try:
        dockerfile_text = _canonical_source_text_bytes(
            dockerfile_path.read_bytes()
        ).decode("utf-8")
    except (OSError, ValueError):
        dockerfile_text = ""

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

    alpha_concurrency = alpha_workflow.get("concurrency", {})
    stable_concurrency = workflow.get("concurrency", {})
    alpha_dispatch_env = {
        "INPUT_TAG": "${{ inputs.tag }}",
        "INPUT_PROMOTE_ALIAS": "${{ inputs.promote_alias }}",
        "INPUT_PROFILE_ALIASES": "${{ inputs.profile_aliases }}",
    }
    stable_dispatch_env = {
        "INPUT_TAG": "${{ inputs.tag }}",
        "INPUT_MOVE_LATEST": "${{ inputs.move_latest }}",
        "INPUT_PROFILE_ALIASES": "${{ inputs.profile_aliases }}",
    }
    alpha_promotion_contract = _digest_promotion_contract(alpha_promote_job)
    stable_promotion_contract = _digest_promotion_contract(promote_job)
    alpha_prestate_before_write = (
        "declare -A PRESTATE" in alpha_promote_run
        and "canonical_ref=" in alpha_promote_run
        and alpha_promote_run.index("declare -A PRESTATE")
        < alpha_promote_run.index("canonical_ref=")
    )
    stable_prestate_before_write = (
        "declare -A PRESTATE" in promote_run
        and "canonical_ref=" in promote_run
        and promote_run.index("declare -A PRESTATE")
        < promote_run.index("canonical_ref=")
    )

    checks = {
        "release_workflow_templates_exact": (
            workflow_template_hashes == APPROVED_WORKFLOW_SHA256
        ),
        "alpha_workflow_dispatch_only": isinstance(alpha_workflow_on, dict)
        and set(alpha_workflow_on) == {"workflow_dispatch"},
        "release_concurrency_shared": isinstance(alpha_concurrency, dict)
        and isinstance(stable_concurrency, dict)
        and alpha_concurrency.get("group") == "waggledance-ghcr-release"
        and stable_concurrency.get("group") == "waggledance-ghcr-release"
        and alpha_concurrency.get("cancel-in-progress") is False
        and stable_concurrency.get("cancel-in-progress") is False,
        "alpha_dispatch_inputs_env_only": _dispatch_env_contract(
            alpha_validate_job,
            alpha_dispatch_env,
        )
        and "${{ inputs." not in alpha_promote_run,
        "alpha_strict_tag_authority": _tag_authority_contract(
            alpha_validate_job,
            alpha_build_job,
            alpha_promote_job,
        )
        and "^v[0-9]+\\.[0-9]+\\.[0-9]+" in alpha_validate_run
        and "-alpha$" in alpha_validate_run,
        "alpha_alias_policy_fail_closed": all(
            marker in alpha_validate_run
            for marker in (
                "axis-b-alpha",
                "stable|latest|small-stable|medium-stable",
                "alpha alias is not allowlisted",
            )
        ),
        "alpha_build_digest_exported": _digest_build_contract(
            alpha_build_job,
            alpha_build,
        ),
        "alpha_digest_smoke": _digest_smoke_contract(alpha_smoke_job),
        "alpha_alias_promotion_after_smoke": {
            "validate-tag",
            "build-candidate",
            "smoke-candidate",
        }.issubset(_job_needs(alpha_promote_job)),
        "alpha_alias_sources_tested_digest": alpha_promotion_contract,
        "alpha_alias_verification_fail_closed": (
            _alias_verification_fail_closed(alpha_promote_job)
        ),
        "alpha_no_alias_mutation_before_smoke": (
            "imagetools create" not in alpha_build_run
            and "imagetools create" not in alpha_smoke_run
        ),
        "workflow_dispatch_only": isinstance(workflow_on, dict)
        and set(workflow_on) == {"workflow_dispatch"},
        "stable_dispatch_inputs_env_only": _dispatch_env_contract(
            validate_job,
            stable_dispatch_env,
        )
        and "${{ inputs." not in promote_run,
        "stable_strict_tag_authority": _tag_authority_contract(
            validate_job,
            build_job,
            promote_job,
        ),
        "tag_input_required": isinstance(tag_input, dict)
        and tag_input.get("required") is True,
        "stable_tag_shape_guard": "^v[0-9]+\\.[0-9]+\\.[0-9]+$" in validate_run,
        "prerelease_tag_refused": "^v[0-9]+\\.[0-9]+\\.[0-9]+$" in validate_run,
        "move_latest_operator_gated": 'case "$MOVE_LATEST"' in promote_run
        and 'yes) aliases+=("latest")' in promote_run
        and "INPUT_MOVE_LATEST" in validate_run,
        "move_latest_default_no": isinstance(move_latest, dict)
        and move_latest.get("default") == "no"
        and set(move_latest.get("options", [])) == {"yes", "no"},
        "ghcr_primary": all(
            "ghcr.io/ahkeratmehilaiset/waggledance" in text
            and "docker.io" not in text.lower()
            for text in (
                json.dumps(alpha_workflow),
                json.dumps(workflow),
            )
        ),
        "canonical_image_pushed": _digest_build_contract(
            build_job,
            canonical_build,
        ),
        "stable_build_digest_exported": _digest_build_contract(
            build_job,
            canonical_build,
        ),
        "stable_digest_smoke": _digest_smoke_contract(smoke_job),
        "stable_alias_promotion_after_smoke": {
            "validate-tag",
            "build-candidate",
            "smoke-candidate",
        }.issubset(_job_needs(promote_job)),
        "stable_alias_sources_tested_digest": stable_promotion_contract,
        "stable_no_alias_mutation_before_smoke": (
            "imagetools create" not in build_run
            and "imagetools create" not in smoke_run
        ),
        "canonical_tag_idempotent_fail_closed": all(
            marker in run
            for run in (alpha_promote_run, promote_run)
            for marker in (
                "already exists at a different digest",
                "already resolves to the tested digest",
                'verify_digest "$canonical_ref" "$TARGET_DIGEST"',
            )
        ),
        "alias_prestate_required": alpha_prestate_before_write
        and stable_prestate_before_write
        and "automatic rollback to absence is impossible" in alpha_promote_run
        and "automatic rollback to absence is impossible" in promote_run,
        "alias_rollback_restores_and_verifies": _alias_verification_fail_closed(
            alpha_promote_job
        )
        and _alias_verification_fail_closed(promote_job),
        "imagetools_prefer_index_false": alpha_promotion_contract
        and stable_promotion_contract,
        "stable_alias_tagged": 'aliases=("stable")' in promote_run,
        "profile_aliases_opt_in": isinstance(profile_aliases, dict)
        and profile_aliases.get("default") == "yes"
        and "small-stable" in promote_run
        and "medium-stable" in promote_run,
        "stable_profile_s_smoke": "SMOKE_OK_STABLE" in smoke_run
        and "WAGGLE_PROFILE=small" in smoke_run,
        "stable_alias_smoke": 'verify_digest "$REGISTRY_IMAGE:$alias"' in promote_run
        and 'aliases=("stable")' in promote_run,
        "stable_alias_fail_closed": _alias_verification_fail_closed(promote_job),
        "latest_alias_smoke_if_moved": 'yes) aliases+=("latest")' in promote_run
        and 'verify_digest "$REGISTRY_IMAGE:$alias"' in promote_run,
        "dockerfile_entrypoint_canonical": json.dumps(CANONICAL_ENTRYPOINT)
        in dockerfile_text,
        "compose_entrypoint_canonical": compose_command == CANONICAL_ENTRYPOINT,
        "pyproject_script_canonical": script == CANONICAL_SCRIPT,
    }
    source_hashes, physical_source_hashes = _source_hash_maps(root)
    return {
        "checks": checks,
        "entrypoints": {
            "expected": CANONICAL_ENTRYPOINT,
            "dockerfile_cmd": CANONICAL_ENTRYPOINT
            if checks["dockerfile_entrypoint_canonical"]
            else None,
            "compose_command": compose_command,
            "pyproject_script": script,
        },
        "source_files": [str(path) for path in REQUIRED_SOURCE_FILES],
        "source_hashes": source_hashes,
        "physical_source_hashes": physical_source_hashes,
    }


def evaluate_report(
    report: dict[str, Any],
    *,
    expected_commit: str | None = None,
    target_version: str = DEFAULT_TARGET_VERSION,
    source_root: Path | str = Path("."),
) -> list[str]:
    """Return fail-closed blockers for a Docker policy evidence report."""

    blockers: list[str] = []
    if report.get("schema_version") != SCHEMA_VERSION:
        blockers.append("schema_version_invalid")
    if report.get("target_version") != target_version:
        blockers.append("target_version_mismatch")
    commit = report.get("commit")
    if not isinstance(commit, str) or not commit.strip():
        blockers.append("commit_missing")
    elif expected_commit is not None and commit != expected_commit:
        blockers.append("commit_mismatch")

    source_root = Path(source_root)
    inspected = inspect_static_policy(source_root)
    binding_commit = commit if isinstance(commit, str) else ""
    inspected_binding, binding_blockers = inspect_git_source_binding(
        source_root,
        binding_commit,
    )
    blockers.extend(binding_blockers)
    source_files = report.get("source_files")
    if not isinstance(source_files, list):
        blockers.append("source_files_missing")
        source_files = []
    if source_files != inspected["source_files"]:
        blockers.append("source_files_mismatch")
    for item in source_files:
        if not isinstance(item, str) or not (source_root / item).is_file():
            blockers.append(f"source_file_missing:{item}")

    source_hashes = report.get("source_hashes")
    if not isinstance(source_hashes, dict):
        blockers.append("source_hashes_missing")
    elif source_hashes != inspected["source_hashes"]:
        blockers.append("source_hashes_mismatch")

    source_git = report.get("source_git")
    if not isinstance(source_git, dict):
        blockers.append("source_git_missing")
    elif source_git != inspected_binding:
        blockers.append("source_git_binding_mismatch")

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
        if authorization.get("schema_version") != AUTH_SCHEMA_VERSION:
            blockers.append("operator_authorization_schema_invalid")
        if authorization.get("target_version") != target_version:
            blockers.append("operator_authorization_target_mismatch")
        if authorization.get("commit") != commit:
            blockers.append("operator_authorization_commit_mismatch")
        stable_authorized = authorization.get("stable_promotion_authorized") is True
        docker_deferred = authorization.get("docker_promotion_deferred") is True
        if not stable_authorized and not docker_deferred:
            blockers.append("stable_promotion_not_authorized")
        if authorization.get("move_latest") not in {"yes", "no"}:
            blockers.append("move_latest_policy_missing")
        if docker_deferred and authorization.get("move_latest") != "no":
            blockers.append("deferred_docker_cannot_move_latest")
        if not isinstance(
            authorization.get("authorization_id"),
            str,
        ) or not authorization.get("authorization_id"):
            blockers.append("operator_authorization_id_missing")
        if _parse_utc(authorization.get("authorized_at_utc")) is None:
            blockers.append("operator_authorized_at_invalid")

    return blockers


def build_report(
    *,
    source_root: Path | str = Path("."),
    commit: str,
    target_version: str = DEFAULT_TARGET_VERSION,
    operator_authorization: dict[str, Any] | None = None,
    generated_at_utc: dt.datetime | None = None,
) -> dict[str, Any]:
    generated_at_utc = generated_at_utc or dt.datetime.now(dt.UTC)
    static = inspect_static_policy(source_root)
    source_git, _ = inspect_git_source_binding(source_root, commit)
    report = {
        "schema_version": SCHEMA_VERSION,
        "target_version": target_version,
        "commit": commit,
        "generated_at_utc": _format_utc(generated_at_utc),
        "source_files": static["source_files"],
        "source_hashes": static["source_hashes"],
        "source_materialization": {
            "informational_only": True,
            "physical_source_hashes": static["physical_source_hashes"],
        },
        "source_git": source_git,
        "git_runtime_provenance": inspect_git_runtime_provenance(),
        "static_checks": static["checks"],
        "entrypoints": static["entrypoints"],
        "operator_authorization": operator_authorization,
        "post_tag_runtime_verification_required": True,
        "latest_move_requires_operator_opt_in": True,
    }
    blockers = evaluate_report(
        report,
        expected_commit=commit,
        target_version=target_version,
        source_root=source_root,
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
        authorization = _read_json(args.operator_authorization)
    elif args.operator_decision_pack is not None:
        authorization = operator_authorization_from_decision_pack(
            args.operator_decision_pack,
            commit=commit,
            target_version=args.target_version,
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
