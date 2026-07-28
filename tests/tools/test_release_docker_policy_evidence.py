# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tools.run_release_docker_policy_evidence import (
    AUTH_SCHEMA_VERSION,
    CANONICAL_ENTRYPOINT,
    CANONICAL_SCRIPT,
    SCHEMA_VERSION,
    build_report,
    evaluate_report,
    main,
    operator_authorization_from_decision_pack,
)


COMMIT = "dc76e81cd8c804608bfaedf951220e46ff1baffa"


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = (
    REPO_ROOT / ".github" / "workflows" / "release-docker-stable.yml"
).read_text(encoding="utf-8")
PRERELEASE_WORKFLOW = (
    REPO_ROOT / ".github" / "workflows" / "release-docker.yml"
).read_text(encoding="utf-8")


def _write_source_tree(
    root,
    *,
    workflow: str = WORKFLOW,
    prerelease_workflow: str = PRERELEASE_WORKFLOW,
) -> None:
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "release-docker-stable.yml").write_bytes(
        workflow.encode("utf-8"),
    )
    (root / ".github" / "workflows" / "release-docker.yml").write_bytes(
        prerelease_workflow.encode("utf-8"),
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


def _run_validator(
    workflow_text: str,
    *,
    job_name: str,
    step_name: str,
    values: dict[str, str],
    output,
) -> tuple[subprocess.CompletedProcess[str], str]:
    workflow = yaml.safe_load(workflow_text)
    steps = workflow["jobs"][job_name]["steps"]
    step = next(item for item in steps if item["name"] == step_name)
    env = os.environ.copy()
    env.update(values)
    env["GITHUB_OUTPUT"] = str(output)
    completed = subprocess.run(
        [sys.executable, "-c", step["run"]],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    written = output.read_text(encoding="utf-8") if output.exists() else ""
    return completed, written


def _authorization(*, commit: str = COMMIT) -> dict:
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
    _write_source_tree(tmp_path)

    report = build_report(source_root=tmp_path, commit=COMMIT)

    assert report["schema_version"] == SCHEMA_VERSION
    assert all(report["static_checks"].values())
    assert report["operator_authorization"] is None
    assert report["docker_stable_policy"] == "draft"
    assert report["blockers"] == ["operator_authorization_missing"]


def test_unsigned_decision_pack_does_not_authorize(tmp_path) -> None:
    _write_source_tree(tmp_path)
    pack = _write_decision_pack(tmp_path)

    auth = operator_authorization_from_decision_pack(
        pack,
        commit=COMMIT,
        target_version="v3.12.0",
    )
    report = build_report(
        source_root=tmp_path,
        commit=COMMIT,
        operator_authorization=auth,
    )

    assert auth is None
    assert report["docker_stable_policy"] == "draft"
    assert report["blockers"] == ["operator_authorization_missing"]


def test_signed_stable_only_decision_pack_authorizes_without_latest(tmp_path) -> None:
    _write_source_tree(tmp_path)
    pack = _write_decision_pack(
        tmp_path,
        chosen_option="ghcr_stable_only",
        signed_by="operator:janik:2026-05-24T00:00:00Z",
    )

    auth = operator_authorization_from_decision_pack(
        pack,
        commit=COMMIT,
        target_version="v3.12.0",
    )
    report = build_report(
        source_root=tmp_path,
        commit=COMMIT,
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
    _write_source_tree(tmp_path)
    pack = _write_decision_pack(
        tmp_path,
        chosen_option="ghcr_stable_and_latest",
        signed_by="operator:janik:2026-05-24T00:00:00Z",
    )

    auth = operator_authorization_from_decision_pack(
        pack,
        commit=COMMIT,
        target_version="v3.12.0",
    )
    report = build_report(
        source_root=tmp_path,
        commit=COMMIT,
        operator_authorization=auth,
    )

    assert auth is not None
    assert auth["stable_promotion_authorized"] is True
    assert auth["docker_promotion_deferred"] is False
    assert auth["move_latest"] == "yes"
    assert report["docker_stable_policy"] == "finalized"
    assert report["blockers"] == []


def test_signed_defer_docker_decision_pack_finalizes_no_move_policy(tmp_path) -> None:
    _write_source_tree(tmp_path)
    pack = _write_decision_pack(
        tmp_path,
        chosen_option="defer_docker",
        signed_by="operator:janik:2026-05-24T00:00:00Z",
    )

    auth = operator_authorization_from_decision_pack(
        pack,
        commit=COMMIT,
        target_version="v3.12.0",
    )
    report = build_report(
        source_root=tmp_path,
        commit=COMMIT,
        operator_authorization=auth,
    )

    assert auth is not None
    assert auth["stable_promotion_authorized"] is False
    assert auth["docker_promotion_deferred"] is True
    assert auth["move_latest"] == "no"
    assert report["docker_stable_policy"] == "finalized"
    assert report["blockers"] == []


def test_wrong_decision_pack_id_fails_closed(tmp_path) -> None:
    _write_source_tree(tmp_path)
    pack = _write_decision_pack(
        tmp_path,
        decision_id="wrong-decision",
        chosen_option="ghcr_stable_only",
        signed_by="operator:janik:2026-05-24T00:00:00Z",
    )

    auth = operator_authorization_from_decision_pack(
        pack,
        commit=COMMIT,
        target_version="v3.12.0",
    )

    assert auth is None


def test_flipped_decision_pack_invariant_fails_closed(tmp_path) -> None:
    _write_source_tree(tmp_path)
    pack = _write_decision_pack(
        tmp_path,
        chosen_option="ghcr_stable_only",
        signed_by="operator:janik:2026-05-24T00:00:00Z",
        latest_move_is_operator_only="false",
    )

    auth = operator_authorization_from_decision_pack(
        pack,
        commit=COMMIT,
        target_version="v3.12.0",
    )

    assert auth is None


def test_decision_pack_target_or_commit_mismatch_fails_closed(tmp_path) -> None:
    _write_source_tree(tmp_path)
    target_mismatch = _write_decision_pack(
        tmp_path,
        chosen_option="ghcr_stable_only",
        signed_by="operator:janik:2026-05-24T00:00:00Z",
        target_version="v3.13.0",
    )
    assert operator_authorization_from_decision_pack(
        target_mismatch,
        commit=COMMIT,
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
        commit=COMMIT,
        target_version="v3.12.0",
    ) is None


def test_decision_pack_rejects_non_operator_timestamp_signoff(tmp_path) -> None:
    _write_source_tree(tmp_path)
    pack = _write_decision_pack(
        tmp_path,
        chosen_option="ghcr_stable_only",
        signed_by="operator:janik",
    )

    assert operator_authorization_from_decision_pack(
        pack,
        commit=COMMIT,
        target_version="v3.12.0",
    ) is None


def test_build_report_finalizes_with_operator_authorization(tmp_path) -> None:
    _write_source_tree(tmp_path)

    report = build_report(
        source_root=tmp_path,
        commit=COMMIT,
        operator_authorization=_authorization(),
    )

    assert report["docker_stable_policy"] == "finalized"
    assert report["blockers"] == []


def test_stable_policy_requires_smoke_before_alias_promotion(tmp_path) -> None:
    unsafe_workflow = WORKFLOW.replace(
        "needs: [validate-tag, build-and-push, smoke-test]",
        "needs: [validate-tag, build-and-push]",
        1,
    )
    _write_source_tree(tmp_path, workflow=unsafe_workflow)

    report = build_report(
        source_root=tmp_path,
        commit=COMMIT,
        operator_authorization=_authorization(),
    )

    assert report["static_checks"][
        "stable_canonical_smoke_before_alias_promotion"
    ] is False
    assert (
        "static_check_not_pass:"
        "stable_canonical_smoke_before_alias_promotion"
    ) in report["blockers"]


def test_stable_policy_rejects_alias_mutation_in_build_job(tmp_path) -> None:
    unsafe_step = """
      - name: Unsafe early stable alias
        run: docker buildx imagetools create --tag image:stable image:canonical

"""
    unsafe_workflow = WORKFLOW.replace(
        "\n  smoke-test:\n",
        unsafe_step + "  smoke-test:\n",
        1,
    )
    _write_source_tree(tmp_path, workflow=unsafe_workflow)

    report = build_report(
        source_root=tmp_path,
        commit=COMMIT,
        operator_authorization=_authorization(),
    )

    assert report["static_checks"]["stable_aliases_absent_before_smoke"] is False


def test_stable_policy_requires_digest_bound_smoke(tmp_path) -> None:
    unsafe_workflow = WORKFLOW.replace(
        (
            "ghcr.io/ahkeratmehilaiset/waggledance@"
            "${{ needs.build-and-push.outputs.digest }}"
        ),
        (
            "ghcr.io/ahkeratmehilaiset/waggledance:"
            "${{ needs.validate-tag.outputs.tag }}"
        ),
        1,
    )
    _write_source_tree(tmp_path, workflow=unsafe_workflow)

    report = build_report(
        source_root=tmp_path,
        commit=COMMIT,
        operator_authorization=_authorization(),
    )

    assert report["static_checks"]["stable_canonical_digest_bound"] is False


def test_stable_policy_requires_fully_qualified_tag_checkout(tmp_path) -> None:
    unsafe_workflow = WORKFLOW.replace(
        "ref: refs/tags/${{ needs.validate-tag.outputs.tag }}",
        "ref: ${{ needs.validate-tag.outputs.tag }}",
        1,
    )
    _write_source_tree(tmp_path, workflow=unsafe_workflow)

    report = build_report(
        source_root=tmp_path,
        commit=COMMIT,
        operator_authorization=_authorization(),
    )

    assert report["static_checks"]["stable_checkout_tag_ref_qualified"] is False


def test_stable_policy_requires_digest_preserving_alias_copy(tmp_path) -> None:
    unsafe_workflow = WORKFLOW.replace(
        "imagetools create --prefer-index=false",
        "imagetools create",
        1,
    )
    _write_source_tree(tmp_path, workflow=unsafe_workflow)

    report = build_report(
        source_root=tmp_path,
        commit=COMMIT,
        operator_authorization=_authorization(),
    )

    assert report["static_checks"]["stable_canonical_digest_bound"] is False


def test_stable_policy_requires_remote_registry_alias_verification(
    tmp_path,
) -> None:
    unsafe_workflow = WORKFLOW.replace(
        "docker buildx imagetools inspect",
        "docker inspect",
        1,
    )
    _write_source_tree(tmp_path, workflow=unsafe_workflow)

    report = build_report(
        source_root=tmp_path,
        commit=COMMIT,
        operator_authorization=_authorization(),
    )

    assert report["static_checks"]["stable_alias_smoke"] is False


def test_prerelease_policy_requires_smoke_before_alias_promotion(tmp_path) -> None:
    unsafe_workflow = PRERELEASE_WORKFLOW.replace(
        "needs: [validate-inputs, build-and-push, smoke-test]",
        "needs: [validate-inputs, build-and-push]",
        1,
    )
    _write_source_tree(tmp_path, prerelease_workflow=unsafe_workflow)

    report = build_report(
        source_root=tmp_path,
        commit=COMMIT,
        operator_authorization=_authorization(),
    )

    assert report["static_checks"][
        "prerelease_canonical_smoke_before_alias_promotion"
    ] is False


def test_prerelease_policy_requires_verification_after_promotion(
    tmp_path,
) -> None:
    unsafe_workflow = PRERELEASE_WORKFLOW.replace(
        "needs: [validate-inputs, build-and-push, promote-aliases]",
        "needs: [validate-inputs, build-and-push]",
        1,
    )
    _write_source_tree(tmp_path, prerelease_workflow=unsafe_workflow)

    report = build_report(
        source_root=tmp_path,
        commit=COMMIT,
        operator_authorization=_authorization(),
    )

    assert report["static_checks"][
        "prerelease_alias_verification_after_promotion"
    ] is False


def test_prerelease_policy_requires_fully_qualified_tag_checkout(
    tmp_path,
) -> None:
    unsafe_workflow = PRERELEASE_WORKFLOW.replace(
        "ref: refs/tags/${{ needs.validate-inputs.outputs.tag }}",
        "ref: ${{ needs.validate-inputs.outputs.tag }}",
        1,
    )
    _write_source_tree(tmp_path, prerelease_workflow=unsafe_workflow)

    report = build_report(
        source_root=tmp_path,
        commit=COMMIT,
        operator_authorization=_authorization(),
    )

    assert report["static_checks"][
        "prerelease_checkout_tag_ref_qualified"
    ] is False


def test_prerelease_policy_rejects_direct_expression_in_validator(tmp_path) -> None:
    unsafe_workflow = PRERELEASE_WORKFLOW.replace(
        "run: |\n          import os",
        "run: |\n          # ${{ inputs.promote_alias }}\n          import os",
        1,
    )
    _write_source_tree(tmp_path, prerelease_workflow=unsafe_workflow)

    report = build_report(
        source_root=tmp_path,
        commit=COMMIT,
        operator_authorization=_authorization(),
    )

    assert report["static_checks"][
        "prerelease_validation_expression_safe"
    ] is False


def test_release_policy_requires_lossless_shared_concurrency_queue(
    tmp_path,
) -> None:
    unsafe_workflow = PRERELEASE_WORKFLOW.replace(
        "  queue: max\n",
        "",
        1,
    )
    _write_source_tree(tmp_path, prerelease_workflow=unsafe_workflow)

    report = build_report(
        source_root=tmp_path,
        commit=COMMIT,
        operator_authorization=_authorization(),
    )

    assert report["static_checks"][
        "release_alias_concurrency_serialized"
    ] is False


def test_stable_validator_accepts_strict_semver(tmp_path) -> None:
    completed, output = _run_validator(
        WORKFLOW,
        job_name="validate-tag",
        step_name="Validate strict stable tag",
        values={"INPUT_TAG": "v3.12.0"},
        output=tmp_path / "stable-output.txt",
    )

    assert completed.returncode == 0, completed.stderr
    assert output == "tag=v3.12.0\n"


@pytest.mark.parametrize(
    "tag",
    [
        "",
        "v3.12.0-alpha",
        "v3.12",
        "3.12.0",
        "v3.12.0;echo injected",
        "v3.12.0$(echo injected)",
        "v3.12.0`echo injected`",
        "v3.12.0\ninjected",
        "v3.12.0 ",
        "v" + ("1" * 129) + ".2.3",
    ],
)
def test_stable_validator_rejects_non_semver_without_output(
    tmp_path,
    tag,
) -> None:
    completed, output = _run_validator(
        WORKFLOW,
        job_name="validate-tag",
        step_name="Validate strict stable tag",
        values={"INPUT_TAG": tag},
        output=tmp_path / "stable-invalid-output.txt",
    )

    assert completed.returncode == 1
    assert output == ""


@pytest.mark.parametrize("alias", ["axis-b-alpha", ""])
def test_prerelease_validator_accepts_documented_aliases(
    tmp_path,
    alias,
) -> None:
    completed, output = _run_validator(
        PRERELEASE_WORKFLOW,
        job_name="validate-inputs",
        step_name="Validate prerelease tag and aliases",
        values={
            "EVENT_NAME": "workflow_dispatch",
            "RELEASE_TAG": "",
            "INPUT_TAG": "v3.11.0-r20-axis-b-activated-alpha",
            "INPUT_PROMOTE_ALIAS": alias,
            "INPUT_PROFILE_ALIASES": "yes",
        },
        output=tmp_path / f"prerelease-valid-{alias or 'empty'}.txt",
    )

    assert completed.returncode == 0, completed.stderr
    assert output == (
        "tag=v3.11.0-r20-axis-b-activated-alpha\n"
        f"promote_alias={alias}\n"
        "profile_aliases=yes\n"
    )


@pytest.mark.parametrize(
    "alias",
    [
        "latest",
        "stable",
        "small-stable",
        "medium-stable",
        "small-axis-b-alpha",
        "medium-axis-b-alpha",
        "small-custom-alpha",
        "medium-custom-alpha",
        "v3.10.0-old-alpha",
        "Axis-B-Alpha",
        "axis alpha",
        "axis/alpha",
        "axis:alpha",
        "axis;echo-alpha",
        "axis$(echo injected)-alpha",
        "axis`echo injected`-alpha",
        "axis\n-alpha",
        "axis\"-alpha",
        ("a" * 123) + "-alpha",
    ],
)
def test_prerelease_validator_rejects_hostile_alias_without_output(
    tmp_path,
    alias,
) -> None:
    completed, output = _run_validator(
        PRERELEASE_WORKFLOW,
        job_name="validate-inputs",
        step_name="Validate prerelease tag and aliases",
        values={
            "EVENT_NAME": "workflow_dispatch",
            "RELEASE_TAG": "",
            "INPUT_TAG": "v3.11.0-r20-axis-b-activated-alpha",
            "INPUT_PROMOTE_ALIAS": alias,
            "INPUT_PROFILE_ALIASES": "yes",
        },
        output=tmp_path / "prerelease-invalid-alias.txt",
    )

    assert completed.returncode == 1
    assert output == ""


@pytest.mark.parametrize(
    "tag",
    [
        "",
        "v3.11.0",
        "v3.11.0-ALPHA",
        "v3.11.0-rc-alpha;echo injected",
        "v3.11.0-rc-alpha$(echo injected)",
        "v3.11.0-rc-alpha\ninjected",
        ("v3.11.0-" + ("a" * 120) + "-alpha"),
    ],
)
def test_prerelease_validator_rejects_hostile_tag_without_output(
    tmp_path,
    tag,
) -> None:
    completed, output = _run_validator(
        PRERELEASE_WORKFLOW,
        job_name="validate-inputs",
        step_name="Validate prerelease tag and aliases",
        values={
            "EVENT_NAME": "workflow_dispatch",
            "RELEASE_TAG": "",
            "INPUT_TAG": tag,
            "INPUT_PROMOTE_ALIAS": "axis-b-alpha",
            "INPUT_PROFILE_ALIASES": "yes",
        },
        output=tmp_path / "prerelease-invalid-tag.txt",
    )

    assert completed.returncode == 1
    assert output == ""


def test_prerelease_workflow_change_invalidates_existing_report(tmp_path) -> None:
    _write_source_tree(tmp_path)
    report = build_report(
        source_root=tmp_path,
        commit=COMMIT,
        operator_authorization=_authorization(),
    )
    prerelease_path = (
        tmp_path / ".github" / "workflows" / "release-docker.yml"
    )
    prerelease_path.write_text(
        prerelease_path.read_text(encoding="utf-8") + "\n# changed\n",
        encoding="utf-8",
    )

    assert "source_hashes_mismatch" in evaluate_report(
        report,
        expected_commit=COMMIT,
        source_root=tmp_path,
    )


@pytest.mark.parametrize(
    ("workflow_name", "check_name"),
    [
        ("release-docker-stable.yml", "stable_workflow_policy_hash_pinned"),
        ("release-docker.yml", "prerelease_workflow_policy_hash_pinned"),
    ],
)
def test_unreviewed_workflow_change_fails_template_hash_pin(
    tmp_path,
    workflow_name,
    check_name,
) -> None:
    _write_source_tree(tmp_path)
    path = tmp_path / ".github" / "workflows" / workflow_name
    path.write_bytes(path.read_bytes() + b"\n# unreviewed change\n")

    report = build_report(
        source_root=tmp_path,
        commit=COMMIT,
        operator_authorization=_authorization(),
    )

    assert report["static_checks"][check_name] is False
    assert report["docker_stable_policy"] == "draft"
    assert f"static_check_not_pass:{check_name}" in report["blockers"]


def test_workflow_template_hash_pins_are_newline_portable(tmp_path) -> None:
    _write_source_tree(tmp_path)
    for workflow_name in (
        "release-docker-stable.yml",
        "release-docker.yml",
    ):
        path = tmp_path / ".github" / "workflows" / workflow_name
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))

    report = build_report(
        source_root=tmp_path,
        commit=COMMIT,
        operator_authorization=_authorization(),
    )

    assert report["static_checks"]["stable_workflow_policy_hash_pinned"]
    assert report["static_checks"]["prerelease_workflow_policy_hash_pinned"]


def test_evaluate_report_accepts_fresh_source_bound_bundle(tmp_path) -> None:
    _write_source_tree(tmp_path)
    report = build_report(
        source_root=tmp_path,
        commit=COMMIT,
        operator_authorization=_authorization(),
    )

    assert evaluate_report(
        report,
        expected_commit=COMMIT,
        source_root=tmp_path,
    ) == []


def test_evaluate_report_rejects_forged_source_hashes(tmp_path) -> None:
    _write_source_tree(tmp_path)
    report = build_report(
        source_root=tmp_path,
        commit=COMMIT,
        operator_authorization=_authorization(),
    )
    report["source_hashes"] = {
        path: "sha256:" + ("0" * 64)
        for path in report["source_files"]
    }

    assert "source_hashes_mismatch" in evaluate_report(
        report,
        expected_commit=COMMIT,
        source_root=tmp_path,
    )


def test_evaluate_report_rejects_reordered_source_manifest(tmp_path) -> None:
    _write_source_tree(tmp_path)
    report = build_report(
        source_root=tmp_path,
        commit=COMMIT,
        operator_authorization=_authorization(),
    )
    report["source_files"].reverse()

    assert "source_files_mismatch" in evaluate_report(
        report,
        expected_commit=COMMIT,
        source_root=tmp_path,
    )


def test_evaluate_report_rejects_source_changed_after_report(tmp_path) -> None:
    _write_source_tree(tmp_path)
    report = build_report(
        source_root=tmp_path,
        commit=COMMIT,
        operator_authorization=_authorization(),
    )
    (tmp_path / "docs" / "deployment" / "DOCKER_QUICKSTART.md").write_text(
        "changed after report\n",
        encoding="utf-8",
    )

    assert "source_hashes_mismatch" in evaluate_report(
        report,
        expected_commit=COMMIT,
        source_root=tmp_path,
    )


def test_evaluate_report_rejects_forged_static_checks(tmp_path) -> None:
    warning_workflow = WORKFLOW.replace(
        'echo "::error::stable alias does not resolve to canonical digest"\n'
        "            exit 1",
        'echo "::warning::stable alias does not resolve to canonical digest"',
    )
    _write_source_tree(tmp_path, workflow=warning_workflow)
    report = build_report(
        source_root=tmp_path,
        commit=COMMIT,
        operator_authorization=_authorization(),
    )
    report["static_checks"] = {
        check: True
        for check in report["static_checks"]
    }

    assert "static_checks_source_mismatch" in evaluate_report(
        report,
        expected_commit=COMMIT,
        source_root=tmp_path,
    )


def test_evaluate_report_rejects_forged_entrypoints(tmp_path) -> None:
    _write_source_tree(tmp_path)
    report = build_report(
        source_root=tmp_path,
        commit=COMMIT,
        operator_authorization=_authorization(),
    )
    report["entrypoints"] = {
        "expected": ["untrusted"],
        "dockerfile_cmd": None,
        "compose_command": None,
        "pyproject_script": "untrusted:main",
    }

    assert "entrypoints_mismatch" in evaluate_report(
        report,
        expected_commit=COMMIT,
        source_root=tmp_path,
    )


def test_build_report_blocks_warning_only_stable_alias_smoke(tmp_path) -> None:
    warning_workflow = WORKFLOW.replace(
        'echo "::error::stable alias does not resolve to canonical digest"\n'
        "            exit 1",
        'echo "::warning::stable alias does not resolve to canonical digest"',
    )
    _write_source_tree(tmp_path, workflow=warning_workflow)

    report = build_report(
        source_root=tmp_path,
        commit=COMMIT,
        operator_authorization=_authorization(),
    )

    assert report["docker_stable_policy"] == "draft"
    assert "static_check_not_pass:stable_alias_fail_closed" in report["blockers"]


def test_main_writes_draft_with_allow_draft(tmp_path) -> None:
    source_root = tmp_path / "repo"
    source_root.mkdir()
    _write_source_tree(source_root)
    output = tmp_path / "evidence.json"

    rc = main([
        "--source-root",
        str(source_root),
        "--commit",
        COMMIT,
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
    _write_source_tree(source_root)
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
        COMMIT,
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
    _write_source_tree(source_root)
    pack = _write_decision_pack(
        tmp_path,
        chosen_option="ghcr_stable_only",
        signed_by="operator:janik:2026-05-24T00:00:00Z",
    )
    auth = tmp_path / "authorization.json"
    auth.write_text(json.dumps(_authorization()), encoding="utf-8")
    output = tmp_path / "evidence.json"

    rc = main([
        "--source-root",
        str(source_root),
        "--commit",
        COMMIT,
        "--operator-authorization",
        str(auth),
        "--operator-decision-pack",
        str(pack),
        "--output",
        str(output),
    ])

    assert rc == 2
    assert not output.exists()
