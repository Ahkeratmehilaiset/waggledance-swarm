# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

import tools.run_release_docker_policy_evidence as docker_policy
from tools.run_release_docker_policy_evidence import (
    AUTH_SCHEMA_VERSION,
    CANONICAL_ENTRYPOINT,
    CANONICAL_SCRIPT,
    REQUIRED_SOURCE_FILES,
    SCHEMA_VERSION,
    build_report,
    evaluate_report,
    main,
    operator_authorization_from_decision_pack,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
ALPHA_WORKFLOW = (
    REPO_ROOT / ".github" / "workflows" / "release-docker.yml"
).read_text(encoding="utf-8")
WORKFLOW = (
    REPO_ROOT / ".github" / "workflows" / "release-docker-stable.yml"
).read_text(encoding="utf-8")


def _write_source_tree(
    root,
    *,
    workflow: str = WORKFLOW,
    alpha_workflow: str = ALPHA_WORKFLOW,
) -> str:
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "release-docker.yml").write_text(
        alpha_workflow,
        encoding="utf-8",
    )
    (root / ".github" / "workflows" / "release-docker-stable.yml").write_text(
        workflow,
        encoding="utf-8",
    )
    (root / "Dockerfile").write_text(
        f"CMD {json.dumps(CANONICAL_ENTRYPOINT)}\n",
        encoding="utf-8",
    )
    (root / "docker-compose.yml").write_text(
        "services:\n"
        "  waggledance:\n"
        f"    command: {json.dumps(CANONICAL_ENTRYPOINT)}\n",
        encoding="utf-8",
    )
    (root / "pyproject.toml").write_text(
        "[project]\n"
        'name = "waggledance-test"\n'
        "[project.scripts]\n"
        f'waggledance = "{CANONICAL_SCRIPT}"\n',
        encoding="utf-8",
    )
    (root / "docs" / "deployment").mkdir(parents=True)
    (root / "docs" / "deployment" / "DOCKER_QUICKSTART.md").write_text(
        "GHCR is the primary stable registry.\n",
        encoding="utf-8",
    )
    for item in (
        "tools/run_release_docker_policy_evidence.py",
        "tools/operator_decision_pack.py",
    ):
        destination = root / item
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((REPO_ROOT / item).read_bytes())

    subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.name", "WD Policy Test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "wd-policy@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "core.autocrlf", "false"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "commit.gpgsign", "false"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "add", "--", *REQUIRED_FIXTURE_FILES],
        check=True,
    )
    commit_env = os.environ.copy()
    commit_env.update(
        {
            "GIT_AUTHOR_DATE": "2026-05-22T14:00:00Z",
            "GIT_COMMITTER_DATE": "2026-05-22T14:00:00Z",
        }
    )
    subprocess.run(
        ["git", "-C", str(root), "commit", "-m", "fixture"],
        check=True,
        capture_output=True,
        env=commit_env,
    )
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commit_paths(root: Path, *paths: str) -> str:
    subprocess.run(
        ["git", "-C", str(root), "add", "--", *paths],
        check=True,
    )
    commit_env = os.environ.copy()
    commit_env.update(
        {
            "GIT_AUTHOR_DATE": "2026-05-22T14:01:00Z",
            "GIT_COMMITTER_DATE": "2026-05-22T14:01:00Z",
        }
    )
    subprocess.run(
        ["git", "-C", str(root), "commit", "-m", "fixture update"],
        check=True,
        capture_output=True,
        env=commit_env,
    )
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


REQUIRED_FIXTURE_FILES = REQUIRED_SOURCE_FILES


def _authorization(*, commit: str) -> dict:
    return {
        "schema_version": AUTH_SCHEMA_VERSION,
        "target_version": "v3.12.0",
        "commit": commit,
        "stable_promotion_authorized": True,
        "move_latest": "no",
        "authorization_id": "operator-docker-stable-v3.12.0",
        "authorized_at_utc": "2026-05-24T00:00:00Z",
    }


def _write_decision_pack(
    root,
    *,
    chosen_option: str = "",
    signed_by: str = "",
    decision_id: str = "docker-latest-promotion",
    category: str = "docker_promotion",
    latest_move_is_operator_only: str = "true",
    target_version: str | None = None,
    commit: str | None = None,
):
    path = root / "docker-latest-promotion.yaml"
    target_line = f"target_version: {target_version}\n" if target_version else ""
    commit_line = f"commit: {commit}\n" if commit else ""
    path.write_text(
        "schema_version: waggledance.operator_decision_pack.v1\n"
        f"decision_id: {decision_id}\n"
        f"category: {category}\n"
        "created_utc: 2026-05-22T14:00:00Z\n"
        "author_agent: claude\n"
        f"{target_line}"
        f"{commit_line}"
        "options:\n"
        "  - id: ghcr_stable_only\n"
        "    summary: Stable only\n"
        "  - id: ghcr_stable_and_latest\n"
        "    summary: Stable and latest\n"
        "  - id: defer_docker\n"
        "    summary: Defer Docker\n"
        "operator_signoff:\n"
        f'  signed_by: "{signed_by}"\n'
        f'  chosen_option: "{chosen_option}"\n'
        "structural_invariants:\n"
        "  no_main_branch_auto_merge: true\n"
        f"  latest_move_is_operator_only: {latest_move_is_operator_only}\n"
        "  agent_must_not_self_resolve: true\n",
        encoding="utf-8",
    )
    return path


def test_build_report_is_draft_without_operator_authorization(tmp_path) -> None:
    commit = _write_source_tree(tmp_path)

    report = build_report(source_root=tmp_path, commit=commit)

    assert report["schema_version"] == SCHEMA_VERSION
    assert all(report["static_checks"].values())
    assert report["operator_authorization"] is None
    assert report["docker_stable_policy"] == "draft"
    assert report["blockers"] == ["operator_authorization_missing"]


def test_static_policy_rejects_alpha_release_published_trigger(tmp_path) -> None:
    unsafe_alpha = ALPHA_WORKFLOW.replace(
        "\npermissions:\n",
        "  release:\n    types: [published]\n\npermissions:\n",
        1,
    )
    commit = _write_source_tree(tmp_path, alpha_workflow=unsafe_alpha)

    report = build_report(
        source_root=tmp_path,
        commit=commit,
        operator_authorization=_authorization(commit=commit),
    )

    assert report["docker_stable_policy"] == "draft"
    assert (
        "static_check_not_pass:alpha_workflow_dispatch_only"
        in report["blockers"]
    )


@pytest.mark.parametrize(
    ("workflow_name", "expected_check"),
    [
        ("alpha", "alpha_alias_promotion_after_smoke"),
        ("stable", "stable_alias_promotion_after_smoke"),
    ],
)
def test_static_policy_requires_smoke_before_alias_promotion(
    tmp_path,
    workflow_name,
    expected_check,
) -> None:
    if workflow_name == "alpha":
        unsafe_alpha = ALPHA_WORKFLOW.replace(
            "needs: [validate-tag, build-candidate, smoke-candidate]",
            "needs: [validate-tag, build-candidate]",
            1,
        )
        commit = _write_source_tree(tmp_path, alpha_workflow=unsafe_alpha)
    else:
        unsafe_stable = WORKFLOW.replace(
            "needs: [validate-tag, build-candidate, smoke-candidate]",
            "needs: [validate-tag, build-candidate]",
            1,
        )
        commit = _write_source_tree(tmp_path, workflow=unsafe_stable)

    report = build_report(
        source_root=tmp_path,
        commit=commit,
        operator_authorization=_authorization(commit=commit),
    )

    assert report["docker_stable_policy"] == "draft"
    assert f"static_check_not_pass:{expected_check}" in report["blockers"]


@pytest.mark.parametrize(
    ("workflow_name", "expected_check"),
    [
        ("alpha", "alpha_digest_smoke"),
        ("stable", "stable_digest_smoke"),
    ],
)
def test_static_policy_rejects_tag_based_smoke(
    tmp_path,
    workflow_name,
    expected_check,
) -> None:
    if workflow_name == "alpha":
        prefix, tail = ALPHA_WORKFLOW.split("  smoke-candidate:", 1)
        smoke, suffix = tail.split("  publish-tested-digest:", 1)
        unsafe_alpha = (
            prefix
            + "  smoke-candidate:"
            + smoke.replace(
                "$REGISTRY_IMAGE@$TARGET_DIGEST",
                "$REGISTRY_IMAGE:mutable-candidate",
                2,
            )
            + "  publish-tested-digest:"
            + suffix
        )
        commit = _write_source_tree(tmp_path, alpha_workflow=unsafe_alpha)
    else:
        prefix, tail = WORKFLOW.split("  smoke-candidate:", 1)
        smoke, suffix = tail.split("  publish-tested-digest:", 1)
        unsafe_stable = (
            prefix
            + "  smoke-candidate:"
            + smoke.replace(
                "$REGISTRY_IMAGE@$TARGET_DIGEST",
                "$REGISTRY_IMAGE:mutable-candidate",
                2,
            )
            + "  publish-tested-digest:"
            + suffix
        )
        commit = _write_source_tree(tmp_path, workflow=unsafe_stable)

    report = build_report(
        source_root=tmp_path,
        commit=commit,
        operator_authorization=_authorization(commit=commit),
    )

    assert report["docker_stable_policy"] == "draft"
    assert f"static_check_not_pass:{expected_check}" in report["blockers"]


@pytest.mark.parametrize(
    ("workflow_name", "expected_check"),
    [
        ("alpha", "alpha_alias_sources_tested_digest"),
        ("stable", "stable_alias_sources_tested_digest"),
    ],
)
def test_static_policy_rejects_mutable_alias_sources(
    tmp_path,
    workflow_name,
    expected_check,
) -> None:
    safe_command = (
        'docker buildx imagetools create --prefer-index=false --tag '
        '"$REGISTRY_IMAGE:$alias" "$REGISTRY_IMAGE@$TARGET_DIGEST"'
    )
    unsafe_command = (
        'docker buildx imagetools create --prefer-index=false --tag '
        '"$REGISTRY_IMAGE:$alias" "$REGISTRY_IMAGE:$TARGET_TAG" '
        '# "$REGISTRY_IMAGE@$TARGET_DIGEST"'
    )
    if workflow_name == "alpha":
        unsafe_alpha = ALPHA_WORKFLOW.replace(safe_command, unsafe_command, 1)
        commit = _write_source_tree(tmp_path, alpha_workflow=unsafe_alpha)
    else:
        unsafe_stable = WORKFLOW.replace(safe_command, unsafe_command, 1)
        commit = _write_source_tree(tmp_path, workflow=unsafe_stable)

    report = build_report(
        source_root=tmp_path,
        commit=commit,
        operator_authorization=_authorization(commit=commit),
    )

    assert report["docker_stable_policy"] == "draft"
    assert f"static_check_not_pass:{expected_check}" in report["blockers"]


def test_static_policy_rejects_direct_dispatch_input_in_shell(tmp_path) -> None:
    unsafe_alpha = ALPHA_WORKFLOW.replace(
        'echo "tag=$INPUT_TAG"',
        'echo "tag=${{ inputs.tag }}"',
        1,
    )
    commit = _write_source_tree(tmp_path, alpha_workflow=unsafe_alpha)

    report = build_report(
        source_root=tmp_path,
        commit=commit,
        operator_authorization=_authorization(commit=commit),
    )

    assert "static_check_not_pass:alpha_dispatch_inputs_env_only" in report["blockers"]


def test_static_policy_rejects_tag_checkout_without_peeled_sha(tmp_path) -> None:
    unsafe_stable = WORKFLOW.replace(
        'commit_sha=$(git rev-parse --verify "$tag_ref^{commit}")',
        'commit_sha=$(git rev-parse --verify "$tag_ref")',
        1,
    )
    commit = _write_source_tree(tmp_path, workflow=unsafe_stable)

    report = build_report(
        source_root=tmp_path,
        commit=commit,
        operator_authorization=_authorization(commit=commit),
    )

    assert "static_check_not_pass:stable_strict_tag_authority" in report["blockers"]


def test_static_policy_rejects_reserved_alpha_alias_allowance(tmp_path) -> None:
    unsafe_alpha = ALPHA_WORKFLOW.replace(
        "stable|latest|small-stable|medium-stable)",
        "stable-only)",
        1,
    ).replace("axis-b-alpha)", "axis-b-alpha|latest)", 1)
    commit = _write_source_tree(tmp_path, alpha_workflow=unsafe_alpha)

    report = build_report(
        source_root=tmp_path,
        commit=commit,
        operator_authorization=_authorization(commit=commit),
    )

    assert "static_check_not_pass:alpha_alias_policy_fail_closed" in report["blockers"]


def test_static_policy_rejects_tagged_candidate_build(tmp_path) -> None:
    unsafe_stable = WORKFLOW.replace(
        "push-by-digest=true,name-canonical=true,push=true",
        "push=true",
        1,
    )
    commit = _write_source_tree(tmp_path, workflow=unsafe_stable)

    report = build_report(
        source_root=tmp_path,
        commit=commit,
        operator_authorization=_authorization(commit=commit),
    )

    assert "static_check_not_pass:stable_build_digest_exported" in report["blockers"]


def test_static_policy_rejects_missing_canonical_idempotency_guard(tmp_path) -> None:
    unsafe_stable = WORKFLOW.replace(
        "already exists at a different digest",
        "will be overwritten despite a different digest",
        1,
    )
    commit = _write_source_tree(tmp_path, workflow=unsafe_stable)

    report = build_report(
        source_root=tmp_path,
        commit=commit,
        operator_authorization=_authorization(commit=commit),
    )

    assert "static_check_not_pass:canonical_tag_idempotent_fail_closed" in report["blockers"]


@pytest.mark.parametrize(
    "unsafe_workflow",
    [
        WORKFLOW.replace(
            'echo "::error::$canonical_ref already exists at a different digest"\n'
            "              exit 1",
            'echo "::error::$canonical_ref already exists at a different digest"\n'
            "              # exit 1",
            1,
        ),
        WORKFLOW.replace(
            "      - name: Build and push untagged candidate by digest\n",
            "      - name: Unsafe mutable pre-smoke publisher\n"
            "        uses: docker/build-push-action@v7\n"
            "        with:\n"
            "          context: .\n"
            "          push: true\n"
            "          tags: ghcr.io/ahkeratmehilaiset/waggledance:latest\n\n"
            "      - name: Build and push untagged candidate by digest\n",
            1,
        ),
        WORKFLOW.replace(
            '            docker buildx imagetools create --prefer-index=false --tag '
            '"$canonical_ref" "$REGISTRY_IMAGE@$TARGET_DIGEST"',
            '            if false; then docker buildx imagetools create '
            '--prefer-index=false --tag "$canonical_ref" '
            '"$REGISTRY_IMAGE@$TARGET_DIGEST"; fi\n'
            "            DOCKER_BIN=docker\n"
            '            "$DOCKER_BIN" buildx imagetools create '
            '--prefer-index=false --tag "$canonical_ref" '
            '"$REGISTRY_IMAGE:$TARGET_TAG"',
            1,
        ),
    ],
    ids=("comment-decoy", "second-build-action", "dead-code-indirection"),
)
def test_static_policy_rejects_any_drift_from_reviewed_workflow_templates(
    tmp_path,
    unsafe_workflow,
) -> None:
    commit = _write_source_tree(tmp_path, workflow=unsafe_workflow)

    report = build_report(
        source_root=tmp_path,
        commit=commit,
        operator_authorization=_authorization(commit=commit),
    )

    assert report["docker_stable_policy"] == "draft"
    assert (
        "static_check_not_pass:release_workflow_templates_exact"
        in report["blockers"]
    )


def test_static_policy_rejects_prefer_index_default(tmp_path) -> None:
    unsafe_alpha = ALPHA_WORKFLOW.replace(" --prefer-index=false", "", 1)
    commit = _write_source_tree(tmp_path, alpha_workflow=unsafe_alpha)

    report = build_report(
        source_root=tmp_path,
        commit=commit,
        operator_authorization=_authorization(commit=commit),
    )

    assert "static_check_not_pass:imagetools_prefer_index_false" in report["blockers"]


def test_static_policy_rejects_early_alias_mutation(tmp_path) -> None:
    prefix, tail = ALPHA_WORKFLOW.split("  smoke-candidate:", 1)
    smoke, suffix = tail.split("  publish-tested-digest:", 1)
    smoke = smoke.replace(
        "          set -euo pipefail\n",
        "          set -euo pipefail\n"
        "          docker buildx imagetools create --prefer-index=false --tag early source\n",
        1,
    )
    unsafe_alpha = prefix + "  smoke-candidate:" + smoke + "  publish-tested-digest:" + suffix
    commit = _write_source_tree(tmp_path, alpha_workflow=unsafe_alpha)

    report = build_report(
        source_root=tmp_path,
        commit=commit,
        operator_authorization=_authorization(commit=commit),
    )

    assert "static_check_not_pass:alpha_no_alias_mutation_before_smoke" in report["blockers"]


def test_static_policy_rejects_release_concurrency_split(tmp_path) -> None:
    unsafe_stable = WORKFLOW.replace(
        "group: waggledance-ghcr-release",
        "group: stable-release-only",
        1,
    )
    commit = _write_source_tree(tmp_path, workflow=unsafe_stable)

    report = build_report(
        source_root=tmp_path,
        commit=commit,
        operator_authorization=_authorization(commit=commit),
    )

    assert "static_check_not_pass:release_concurrency_shared" in report["blockers"]


def test_static_policy_rejects_alias_write_without_prestate(tmp_path) -> None:
    unsafe_stable = WORKFLOW.replace("declare -A PRESTATE=()", "PRESTATE=()", 1)
    commit = _write_source_tree(tmp_path, workflow=unsafe_stable)

    report = build_report(
        source_root=tmp_path,
        commit=commit,
        operator_authorization=_authorization(commit=commit),
    )

    assert "static_check_not_pass:alias_prestate_required" in report["blockers"]


def test_static_policy_rejects_rollback_to_candidate_instead_of_prestate(
    tmp_path,
) -> None:
    safe_restore = (
        'docker buildx imagetools create --prefer-index=false --tag "$ref" '
        '"$REGISTRY_IMAGE@$old_digest"'
    )
    unsafe_restore = (
        'docker buildx imagetools create --prefer-index=false --tag "$ref" '
        '"$REGISTRY_IMAGE@$TARGET_DIGEST"'
    )
    unsafe_alpha = ALPHA_WORKFLOW.replace(safe_restore, unsafe_restore, 1)
    commit = _write_source_tree(tmp_path, alpha_workflow=unsafe_alpha)

    report = build_report(
        source_root=tmp_path,
        commit=commit,
        operator_authorization=_authorization(commit=commit),
    )

    assert (
        "static_check_not_pass:alias_rollback_restores_and_verifies"
        in report["blockers"]
    )


def test_unsigned_decision_pack_does_not_authorize(tmp_path) -> None:
    commit = _write_source_tree(tmp_path)
    pack = _write_decision_pack(tmp_path)

    auth = operator_authorization_from_decision_pack(
        pack,
        commit=commit,
        target_version="v3.12.0",
    )
    report = build_report(
        source_root=tmp_path,
        commit=commit,
        operator_authorization=auth,
    )

    assert auth is None
    assert report["docker_stable_policy"] == "draft"
    assert report["blockers"] == ["operator_authorization_missing"]


def test_signed_stable_only_decision_pack_authorizes_without_latest(tmp_path) -> None:
    commit = _write_source_tree(tmp_path)
    pack = _write_decision_pack(
        tmp_path,
        chosen_option="ghcr_stable_only",
        signed_by="operator:janik:2026-05-24T00:00:00Z",
    )

    auth = operator_authorization_from_decision_pack(
        pack,
        commit=commit,
        target_version="v3.12.0",
    )
    report = build_report(
        source_root=tmp_path,
        commit=commit,
        operator_authorization=auth,
    )

    assert auth is not None
    assert auth["stable_promotion_authorized"] is True
    assert auth["docker_promotion_deferred"] is False
    assert auth["move_latest"] == "no"
    assert auth["authorized_at_utc"] == "2026-05-24T00:00:00Z"
    assert report["docker_stable_policy"] == "finalized"
    assert report["blockers"] == []


def test_signed_stable_and_latest_decision_pack_authorizes_latest(tmp_path) -> None:
    commit = _write_source_tree(tmp_path)
    pack = _write_decision_pack(
        tmp_path,
        chosen_option="ghcr_stable_and_latest",
        signed_by="operator:janik:2026-05-24T00:00:00Z",
    )

    auth = operator_authorization_from_decision_pack(
        pack,
        commit=commit,
        target_version="v3.12.0",
    )
    report = build_report(
        source_root=tmp_path,
        commit=commit,
        operator_authorization=auth,
    )

    assert auth is not None
    assert auth["stable_promotion_authorized"] is True
    assert auth["docker_promotion_deferred"] is False
    assert auth["move_latest"] == "yes"
    assert report["docker_stable_policy"] == "finalized"
    assert report["blockers"] == []


def test_signed_defer_docker_decision_pack_finalizes_no_move_policy(tmp_path) -> None:
    commit = _write_source_tree(tmp_path)
    pack = _write_decision_pack(
        tmp_path,
        chosen_option="defer_docker",
        signed_by="operator:janik:2026-05-24T00:00:00Z",
    )

    auth = operator_authorization_from_decision_pack(
        pack,
        commit=commit,
        target_version="v3.12.0",
    )
    report = build_report(
        source_root=tmp_path,
        commit=commit,
        operator_authorization=auth,
    )

    assert auth is not None
    assert auth["stable_promotion_authorized"] is False
    assert auth["docker_promotion_deferred"] is True
    assert auth["move_latest"] == "no"
    assert report["docker_stable_policy"] == "finalized"
    assert report["blockers"] == []


def test_wrong_decision_pack_id_fails_closed(tmp_path) -> None:
    commit = _write_source_tree(tmp_path)
    pack = _write_decision_pack(
        tmp_path,
        decision_id="wrong-decision",
        chosen_option="ghcr_stable_only",
        signed_by="operator:janik:2026-05-24T00:00:00Z",
    )

    auth = operator_authorization_from_decision_pack(
        pack,
        commit=commit,
        target_version="v3.12.0",
    )

    assert auth is None


def test_flipped_decision_pack_invariant_fails_closed(tmp_path) -> None:
    commit = _write_source_tree(tmp_path)
    pack = _write_decision_pack(
        tmp_path,
        chosen_option="ghcr_stable_only",
        signed_by="operator:janik:2026-05-24T00:00:00Z",
        latest_move_is_operator_only="false",
    )

    auth = operator_authorization_from_decision_pack(
        pack,
        commit=commit,
        target_version="v3.12.0",
    )

    assert auth is None


def test_decision_pack_target_or_commit_mismatch_fails_closed(tmp_path) -> None:
    commit = _write_source_tree(tmp_path)
    target_mismatch = _write_decision_pack(
        tmp_path,
        chosen_option="ghcr_stable_only",
        signed_by="operator:janik:2026-05-24T00:00:00Z",
        target_version="v3.13.0",
    )
    assert operator_authorization_from_decision_pack(
        target_mismatch,
        commit=commit,
        target_version="v3.12.0",
    ) is None

    commit_mismatch = _write_decision_pack(
        tmp_path,
        chosen_option="ghcr_stable_only",
        signed_by="operator:janik:2026-05-24T00:00:00Z",
        commit="other",
    )
    assert operator_authorization_from_decision_pack(
        commit_mismatch,
        commit=commit,
        target_version="v3.12.0",
    ) is None


def test_decision_pack_rejects_non_operator_timestamp_signoff(tmp_path) -> None:
    commit = _write_source_tree(tmp_path)
    pack = _write_decision_pack(
        tmp_path,
        chosen_option="ghcr_stable_only",
        signed_by="operator:janik",
    )

    assert operator_authorization_from_decision_pack(
        pack,
        commit=commit,
        target_version="v3.12.0",
    ) is None


def test_build_report_finalizes_with_operator_authorization(tmp_path) -> None:
    commit = _write_source_tree(tmp_path)

    report = build_report(
        source_root=tmp_path,
        commit=commit,
        operator_authorization=_authorization(commit=commit),
    )

    assert report["docker_stable_policy"] == "finalized"
    assert report["blockers"] == []


def test_evaluate_report_accepts_fresh_source_bound_bundle(tmp_path) -> None:
    commit = _write_source_tree(tmp_path)
    report = build_report(
        source_root=tmp_path,
        commit=commit,
        operator_authorization=_authorization(commit=commit),
    )

    assert evaluate_report(
        report,
        expected_commit=commit,
        source_root=tmp_path,
    ) == []


def test_build_report_rejects_abbreviated_source_commit(tmp_path) -> None:
    commit = _write_source_tree(tmp_path)
    abbreviated = commit[:12]

    report = build_report(
        source_root=tmp_path,
        commit=abbreviated,
        operator_authorization=_authorization(commit=abbreviated),
    )

    assert report["docker_stable_policy"] == "draft"
    assert "source_commit_not_full_sha" in report["blockers"]


def test_synthetic_annotated_tag_requires_commit_peeling(tmp_path) -> None:
    commit = _write_source_tree(tmp_path)
    tag_env = os.environ.copy()
    tag_env["GIT_COMMITTER_DATE"] = "2026-05-22T14:02:00Z"
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "tag",
            "-a",
            "v3.12.0",
            "-m",
            "annotated release fixture",
        ],
        check=True,
        capture_output=True,
        env=tag_env,
    )

    tag_object = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "refs/tags/v3.12.0"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    peeled_commit = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "refs/tags/v3.12.0^{commit}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert tag_object != commit
    assert peeled_commit == commit


def test_build_report_rejects_head_different_from_bound_commit(tmp_path) -> None:
    original_commit = _write_source_tree(tmp_path)
    quickstart = tmp_path / "docs" / "deployment" / "DOCKER_QUICKSTART.md"
    quickstart.write_text("second committed version\n", encoding="utf-8")
    new_commit = _commit_paths(
        tmp_path,
        "docs/deployment/DOCKER_QUICKSTART.md",
    )
    assert new_commit != original_commit

    report = build_report(
        source_root=tmp_path,
        commit=original_commit,
        operator_authorization=_authorization(commit=original_commit),
    )

    assert report["docker_stable_policy"] == "draft"
    assert "source_head_commit_mismatch" in report["blockers"]
    assert (
        "source_worktree_blob_mismatch:docs/deployment/DOCKER_QUICKSTART.md"
        in report["blockers"]
    )


def test_build_report_rejects_dirty_required_source_bytes(tmp_path) -> None:
    commit = _write_source_tree(tmp_path)
    quickstart = tmp_path / "docs" / "deployment" / "DOCKER_QUICKSTART.md"
    quickstart.write_text("dirty worktree bytes\n", encoding="utf-8")

    report = build_report(
        source_root=tmp_path,
        commit=commit,
        operator_authorization=_authorization(commit=commit),
    )

    blocker = "source_worktree_blob_mismatch:docs/deployment/DOCKER_QUICKSTART.md"
    assert report["docker_stable_policy"] == "draft"
    assert blocker in report["blockers"]
    assert report["source_git"]["worktree_matches_commit"][
        "docs/deployment/DOCKER_QUICKSTART.md"
    ] is False


def test_build_report_ignores_git_environment_and_path_redirection(
    tmp_path,
    monkeypatch,
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    source_commit = _write_source_tree(source_root)

    authority_root = tmp_path / "authority"
    authority_root.mkdir()
    _write_source_tree(authority_root)
    authority_quickstart = (
        authority_root / "docs" / "deployment" / "DOCKER_QUICKSTART.md"
    )
    authority_quickstart.write_text("redirected authority bytes\n", encoding="utf-8")
    authority_commit = _commit_paths(
        authority_root,
        "docs/deployment/DOCKER_QUICKSTART.md",
    )
    assert authority_commit != source_commit

    shadow_path = tmp_path / "path-shadow"
    shadow_path.mkdir()
    (shadow_path / ("git.exe" if os.name == "nt" else "git")).write_text(
        "not a trusted Git executable\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PATH", str(shadow_path))
    monkeypatch.setenv("GIT_DIR", str(authority_root / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(source_root))
    monkeypatch.setenv("GIT_COMMON_DIR", str(authority_root / ".git"))
    monkeypatch.setenv(
        "GIT_OBJECT_DIRECTORY",
        str(authority_root / ".git" / "objects"),
    )
    monkeypatch.setenv(
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        str(authority_root / ".git" / "objects"),
    )
    monkeypatch.setenv("GIT_REPLACE_REF_BASE", "refs/replace-poison/")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.useReplaceRefs")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")

    report = build_report(
        source_root=source_root,
        commit=source_commit,
        operator_authorization=_authorization(commit=source_commit),
    )

    assert report["docker_stable_policy"] == "finalized"
    assert report["blockers"] == []
    assert report["source_git"]["head"] == source_commit
    assert report["source_git"]["commit"] == source_commit
    provenance = report["git_runtime_provenance"]
    assert provenance["policy"] == "platform_absolute_allowlist_v1"
    assert Path(provenance["executable"]).is_absolute()
    assert provenance["executable_sha256"].startswith("sha256:")


def test_build_report_ignores_replace_objects_and_detects_dirty_blob(tmp_path) -> None:
    commit = _write_source_tree(tmp_path)
    quickstart_relative = "docs/deployment/DOCKER_QUICKSTART.md"
    quickstart = tmp_path / quickstart_relative
    original_blob = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", f"{commit}:{quickstart_relative}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    quickstart.write_text("dirty replacement bytes\n", encoding="utf-8")
    replacement_blob = subprocess.run(
        ["git", "-C", str(tmp_path), "hash-object", "-w", str(quickstart)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "-C", str(tmp_path), "replace", original_blob, replacement_blob],
        check=True,
        capture_output=True,
    )

    report = build_report(
        source_root=tmp_path,
        commit=commit,
        operator_authorization=_authorization(commit=commit),
    )

    blocker = f"source_worktree_blob_mismatch:{quickstart_relative}"
    assert report["docker_stable_policy"] == "draft"
    assert blocker in report["blockers"]
    assert report["source_git"]["blob_oids"][quickstart_relative] == original_blob
    assert report["source_git"]["worktree_matches_commit"][quickstart_relative] is False


def test_evaluate_report_is_portable_across_trusted_git_candidates(
    tmp_path,
    monkeypatch,
) -> None:
    candidates: list[Path] = []
    for candidate in docker_policy._trusted_git_candidates():
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if resolved.is_file() and resolved not in candidates:
            candidates.append(resolved)
    if len(candidates) < 2:
        pytest.skip("platform exposes only one approved Git executable")

    commit = _write_source_tree(tmp_path)
    monkeypatch.setattr(
        docker_policy,
        "_trusted_git_candidates",
        lambda: (candidates[0],),
    )
    report = build_report(
        source_root=tmp_path,
        commit=commit,
        operator_authorization=_authorization(commit=commit),
    )
    first_path = report["git_runtime_provenance"]["executable"]

    monkeypatch.setattr(
        docker_policy,
        "_trusted_git_candidates",
        lambda: (candidates[1],),
    )
    blockers = evaluate_report(
        report,
        expected_commit=commit,
        source_root=tmp_path,
    )

    assert str(candidates[1]) != first_path
    assert "source_git_binding_mismatch" not in blockers
    assert blockers == []


def test_evaluate_report_rejects_forged_git_blob_binding(tmp_path) -> None:
    commit = _write_source_tree(tmp_path)
    report = build_report(
        source_root=tmp_path,
        commit=commit,
        operator_authorization=_authorization(commit=commit),
    )
    report["source_git"]["blob_oids"]["Dockerfile"] = "0" * 40

    blockers = evaluate_report(
        report,
        expected_commit=commit,
        source_root=tmp_path,
    )

    assert "source_git_binding_mismatch" in blockers


def test_evaluate_report_rejects_forged_source_hashes(tmp_path) -> None:
    commit = _write_source_tree(tmp_path)
    report = build_report(
        source_root=tmp_path,
        commit=commit,
        operator_authorization=_authorization(commit=commit),
    )
    report["source_hashes"] = {
        path: "sha256:" + ("0" * 64)
        for path in report["source_files"]
    }

    assert "source_hashes_mismatch" in evaluate_report(
        report,
        expected_commit=commit,
        source_root=tmp_path,
    )


def test_evaluate_report_rejects_reordered_source_manifest(tmp_path) -> None:
    commit = _write_source_tree(tmp_path)
    report = build_report(
        source_root=tmp_path,
        commit=commit,
        operator_authorization=_authorization(commit=commit),
    )
    report["source_files"].reverse()

    assert "source_files_mismatch" in evaluate_report(
        report,
        expected_commit=commit,
        source_root=tmp_path,
    )


def test_evaluate_report_rejects_source_changed_after_report(tmp_path) -> None:
    commit = _write_source_tree(tmp_path)
    report = build_report(
        source_root=tmp_path,
        commit=commit,
        operator_authorization=_authorization(commit=commit),
    )
    (tmp_path / "docs" / "deployment" / "DOCKER_QUICKSTART.md").write_text(
        "changed after report\n",
        encoding="utf-8",
    )

    assert "source_hashes_mismatch" in evaluate_report(
        report,
        expected_commit=commit,
        source_root=tmp_path,
    )


def test_evaluate_report_rejects_forged_static_checks(tmp_path) -> None:
    warning_workflow = WORKFLOW.replace(
        'echo "::error::rollback verification failed for $ref" >&2',
        'echo "::warning::rollback verification failed for $ref" >&2',
        1,
    )
    commit = _write_source_tree(tmp_path, workflow=warning_workflow)
    report = build_report(
        source_root=tmp_path,
        commit=commit,
        operator_authorization=_authorization(commit=commit),
    )
    report["static_checks"] = {
        check: True
        for check in report["static_checks"]
    }

    assert "static_checks_source_mismatch" in evaluate_report(
        report,
        expected_commit=commit,
        source_root=tmp_path,
    )


def test_evaluate_report_rejects_forged_entrypoints(tmp_path) -> None:
    commit = _write_source_tree(tmp_path)
    report = build_report(
        source_root=tmp_path,
        commit=commit,
        operator_authorization=_authorization(commit=commit),
    )
    report["entrypoints"] = {
        "expected": ["untrusted"],
        "dockerfile_cmd": None,
        "compose_command": None,
        "pyproject_script": "untrusted:main",
    }

    assert "entrypoints_mismatch" in evaluate_report(
        report,
        expected_commit=commit,
        source_root=tmp_path,
    )


def test_build_report_blocks_warning_only_stable_alias_smoke(tmp_path) -> None:
    warning_workflow = WORKFLOW.replace(
        'echo "::error::rollback verification failed for $ref" >&2',
        'echo "::warning::rollback verification failed for $ref" >&2',
        1,
    )
    commit = _write_source_tree(tmp_path, workflow=warning_workflow)

    report = build_report(
        source_root=tmp_path,
        commit=commit,
        operator_authorization=_authorization(commit=commit),
    )

    assert report["docker_stable_policy"] == "draft"
    assert "static_check_not_pass:stable_alias_fail_closed" in report["blockers"]


def test_main_writes_draft_with_allow_draft(tmp_path) -> None:
    source_root = tmp_path / "repo"
    source_root.mkdir()
    commit = _write_source_tree(source_root)
    output = tmp_path / "evidence.json"

    rc = main([
        "--source-root",
        str(source_root),
        "--commit",
        commit,
        "--output",
        str(output),
        "--allow-draft",
    ])

    assert rc == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["docker_stable_policy"] == "draft"
    assert report["blockers"] == ["operator_authorization_missing"]


def test_main_uses_signed_operator_decision_pack(tmp_path) -> None:
    source_root = tmp_path / "repo"
    source_root.mkdir()
    commit = _write_source_tree(source_root)
    pack = _write_decision_pack(
        tmp_path,
        chosen_option="ghcr_stable_only",
        signed_by="operator:janik:2026-05-24T00:00:00Z",
    )
    output = tmp_path / "evidence.json"

    rc = main([
        "--source-root",
        str(source_root),
        "--commit",
        commit,
        "--operator-decision-pack",
        str(pack),
        "--output",
        str(output),
    ])

    assert rc == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["docker_stable_policy"] == "finalized"
    assert report["operator_authorization"]["source"] == "operator_decision_pack"


def test_main_rejects_ambiguous_authorization_sources(tmp_path) -> None:
    source_root = tmp_path / "repo"
    source_root.mkdir()
    commit = _write_source_tree(source_root)
    pack = _write_decision_pack(
        tmp_path,
        chosen_option="ghcr_stable_only",
        signed_by="operator:janik:2026-05-24T00:00:00Z",
    )
    auth = tmp_path / "authorization.json"
    auth.write_text(json.dumps(_authorization(commit=commit)), encoding="utf-8")
    output = tmp_path / "evidence.json"

    rc = main([
        "--source-root",
        str(source_root),
        "--commit",
        commit,
        "--operator-authorization",
        str(auth),
        "--operator-decision-pack",
        str(pack),
        "--output",
        str(output),
    ])

    assert rc == 2
    assert not output.exists()
