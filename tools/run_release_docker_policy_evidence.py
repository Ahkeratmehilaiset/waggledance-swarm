#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Write v3.12 Docker stable-policy evidence from repo and operator facts."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
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


SCHEMA_VERSION = "waggledance.release_docker_policy.v1"
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
    "dockerfile_entrypoint_canonical",
    "compose_entrypoint_canonical",
    "pyproject_script_canonical",
)


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


def _current_commit() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return ""
    return completed.stdout.strip()


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


def _normalized_text_sha256(path: Path) -> str:
    """Hash UTF-8 text with platform newline translation removed."""

    normalized = path.read_text(encoding="utf-8")
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


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


def inspect_static_policy(source_root: Path | str = Path(".")) -> dict[str, Any]:
    """Inspect static Docker release policy files without running Docker."""

    root = Path(source_root)
    workflow_path = root / ".github/workflows/release-docker-stable.yml"
    prerelease_workflow_path = root / ".github/workflows/release-docker.yml"
    dockerfile_path = root / "Dockerfile"
    compose_path = root / "docker-compose.yml"
    pyproject_path = root / "pyproject.toml"

    workflow = _load_yaml(workflow_path) if workflow_path.exists() else {}
    prerelease_workflow = (
        _load_yaml(prerelease_workflow_path)
        if prerelease_workflow_path.exists()
        else {}
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
        pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        pyproject = {}
    try:
        compose = _load_yaml(compose_path)
    except (OSError, yaml.YAMLError):
        compose = {}
    try:
        dockerfile_text = dockerfile_path.read_text(encoding="utf-8")
    except OSError:
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
        "stable_workflow_policy_hash_pinned": workflow_path.is_file()
        and _normalized_text_sha256(workflow_path)
        == REVIEWED_WORKFLOW_HASHES[str(REQUIRED_SOURCE_FILES[0])],
        "prerelease_workflow_policy_hash_pinned": (
            prerelease_workflow_path.is_file()
            and _normalized_text_sha256(prerelease_workflow_path)
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
        "dockerfile_entrypoint_canonical": json.dumps(CANONICAL_ENTRYPOINT)
        in dockerfile_text,
        "compose_entrypoint_canonical": compose_command == CANONICAL_ENTRYPOINT,
        "pyproject_script_canonical": script == CANONICAL_SCRIPT,
    }
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
        "source_hashes": {
            str(path): _sha256(root / path)
            for path in REQUIRED_SOURCE_FILES
            if (root / path).is_file()
        },
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
        if expected_commit is not None and authorization.get("commit") != expected_commit:
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
    report = {
        "schema_version": SCHEMA_VERSION,
        "target_version": target_version,
        "commit": commit,
        "generated_at_utc": _format_utc(generated_at_utc),
        "source_files": static["source_files"],
        "source_hashes": static["source_hashes"],
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

    commit = args.commit or _current_commit()
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
