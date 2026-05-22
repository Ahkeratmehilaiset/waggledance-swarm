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
from typing import Any

import yaml


SCHEMA_VERSION = "waggledance.release_docker_policy.v1"
AUTH_SCHEMA_VERSION = "waggledance.operator_docker_stable_authorization.v1"
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
    "Dockerfile",
    "docker-compose.yml",
    "pyproject.toml",
    "docs/deployment/DOCKER_QUICKSTART.md",
)
REQUIRED_STATIC_CHECKS = (
    "workflow_dispatch_only",
    "tag_input_required",
    "stable_tag_shape_guard",
    "prerelease_tag_refused",
    "move_latest_operator_gated",
    "move_latest_default_no",
    "ghcr_primary",
    "canonical_image_pushed",
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _load_yaml(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
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


def inspect_static_policy(source_root: Path | str = Path(".")) -> dict[str, Any]:
    """Inspect static Docker release policy files without running Docker."""

    root = Path(source_root)
    workflow_path = root / ".github/workflows/release-docker-stable.yml"
    dockerfile_path = root / "Dockerfile"
    compose_path = root / "docker-compose.yml"
    pyproject_path = root / "pyproject.toml"

    workflow = _load_yaml(workflow_path) if workflow_path.exists() else {}
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

    validate_run = "\n".join(str(step.get("run", "")) for step in _steps(validate_job))
    build_run = "\n".join(str(step.get("run", "")) for step in _steps(build_job))
    smoke_run = "\n".join(str(step.get("run", "")) for step in _steps(smoke_job))
    canonical_build = _find_step(build_job, "canonical stable image")
    latest_step = _find_step(build_job, "latest")
    latest_smoke = _find_step(smoke_job, "latest")
    canonical_build_with = (
        canonical_build.get("with", {}) if isinstance(canonical_build, dict) else {}
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

    checks = {
        "workflow_dispatch_only": isinstance(workflow_on, dict)
        and set(workflow_on) == {"workflow_dispatch"},
        "tag_input_required": isinstance(tag_input, dict)
        and tag_input.get("required") is True,
        "stable_tag_shape_guard": "v[0-9]+\\.[0-9]+\\.[0-9]+" in validate_run,
        "prerelease_tag_refused": "alpha|beta|rc|dev|pre" in validate_run,
        "move_latest_operator_gated": isinstance(latest_step, dict)
        and "inputs.move_latest == 'yes'" in str(latest_step.get("if", "")),
        "move_latest_default_no": isinstance(move_latest, dict)
        and move_latest.get("default") == "no"
        and set(move_latest.get("options", [])) == {"yes", "no"},
        "ghcr_primary": "ghcr.io/ahkeratmehilaiset/waggledance" in build_run
        and "docker.io" not in build_run.lower(),
        "canonical_image_pushed": isinstance(canonical_build, dict)
        and "docker/build-push-action" in str(canonical_build.get("uses", ""))
        and isinstance(canonical_build_with, dict)
        and canonical_build_with.get("push") is True,
        "stable_alias_tagged": "waggledance:stable" in build_run,
        "profile_aliases_opt_in": isinstance(profile_aliases, dict)
        and profile_aliases.get("default") == "yes"
        and "small-stable" in build_run
        and "medium-stable" in build_run,
        "stable_profile_s_smoke": "SMOKE_OK_STABLE" in smoke_run
        and "WAGGLE_PROFILE=small" in smoke_run,
        "stable_alias_smoke": "waggledance:stable" in smoke_run
        and "CANONICAL_DIGEST" in smoke_run,
        "stable_alias_fail_closed": "::error::stable alias" in smoke_run
        and "exit 1" in smoke_run
        and "::warning::stable alias" not in smoke_run,
        "latest_alias_smoke_if_moved": isinstance(latest_smoke, dict)
        and "inputs.move_latest == 'yes'" in str(latest_smoke.get("if", ""))
        and "waggledance:latest" in str(latest_smoke.get("run", "")),
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
    source_files = report.get("source_files")
    if not isinstance(source_files, list):
        blockers.append("source_files_missing")
        source_files = []
    if set(source_files) != set(REQUIRED_SOURCE_FILES):
        blockers.append("source_files_mismatch")
    for item in source_files:
        if not isinstance(item, str) or not (source_root / item).is_file():
            blockers.append(f"source_file_missing:{item}")

    static_checks = report.get("static_checks")
    if not isinstance(static_checks, dict):
        blockers.append("static_checks_missing")
        static_checks = {}
    for check in REQUIRED_STATIC_CHECKS:
        if static_checks.get(check) is not True:
            blockers.append(f"static_check_not_pass:{check}")

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
        if authorization.get("stable_promotion_authorized") is not True:
            blockers.append("stable_promotion_not_authorized")
        if authorization.get("move_latest") not in {"yes", "no"}:
            blockers.append("move_latest_policy_missing")
        if not isinstance(authorization.get("authorization_id"), str) or not authorization.get("authorization_id"):
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
    parser.add_argument(
        "--allow-draft",
        action="store_true",
        help="Exit 0 even when the policy remains draft/fail-closed.",
    )
    args = parser.parse_args(argv)

    commit = args.commit or _current_commit()
    authorization = (
        _read_json(args.operator_authorization)
        if args.operator_authorization is not None
        else None
    )
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
