# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import tools.run_release_docker_policy_evidence as docker_policy
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
REAL_INSPECT_SOURCE_COMMIT_BINDING = (
    docker_policy.inspect_source_commit_binding
)
REAL_LOAD_COMMITTED_SOURCE_SNAPSHOT = (
    docker_policy._load_committed_source_snapshot
)
REAL_INSPECT_OPERATOR_AUTHORIZATION_SOURCE = (
    docker_policy.inspect_operator_authorization_source
)


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = (
    REPO_ROOT / ".github" / "workflows" / "release-docker-stable.yml"
).read_text(encoding="utf-8")
PRERELEASE_WORKFLOW = (
    REPO_ROOT / ".github" / "workflows" / "release-docker.yml"
).read_text(encoding="utf-8")
DOCKERFILE = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _stub_source_commit_binding(monkeypatch) -> None:
    monkeypatch.setattr(
        docker_policy,
        "inspect_source_commit_binding",
        lambda source_root, commit: {
            "commit": commit,
            "verified": True,
            "reason": "verified",
            "source_blob_oids": {},
        },
    )
    monkeypatch.setattr(
        docker_policy,
        "inspect_operator_authorization_source",
        lambda authorization, **kwargs: {
            "verified": True,
            "reason": "verified",
            "decision_pack_path": (
                authorization.get("decision_pack_path", "")
                if isinstance(authorization, dict)
                else ""
            ),
            "decision_pack_sha256": (
                authorization.get("decision_pack_sha256", "")
                if isinstance(authorization, dict)
                else ""
            ),
        },
    )
    monkeypatch.setattr(
        docker_policy,
        "_load_committed_source_snapshot",
        lambda source_root, commit: {
            str(path): (Path(source_root) / path).read_bytes()
            for path in docker_policy.REQUIRED_SOURCE_FILES
            if (Path(source_root) / path).is_file()
        },
    )


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
    (root / "Dockerfile").write_text(DOCKERFILE, encoding="utf-8")
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


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _commit_source_tree(root: Path) -> str:
    _git(root, "init")
    _git(root, "config", "user.name", "WaggleDance Test")
    _git(root, "config", "user.email", "test@waggledance.invalid")
    _git(root, "config", "core.autocrlf", "true")
    _git(root, "add", "--all")
    _git(root, "commit", "-m", "fixture")
    return _git(root, "rev-parse", "HEAD").stdout.strip()


def _authorization(*, commit: str = COMMIT) -> dict:
    return {
        "schema_version": AUTH_SCHEMA_VERSION,
        "target_version": "v3.12.0",
        "commit": commit,
        "commit_scope": "exact",
        "decision_pack_target_version": "v3.12.0",
        "decision_pack_commit": commit,
        "stable_promotion_authorized": True,
        "docker_promotion_deferred": False,
        "move_latest": "no",
        "authorization_id": (
            "decision-pack:docker-v3-12-0-stable-promotion:"
            "ghcr_stable_only:janik"
        ),
        "authorized_at_utc": "2026-05-24T00:00:00Z",
        "decision_pack_created_at_utc": "2026-05-22T14:00:00Z",
        "decision_pack_path": (
            "docs/operator_inbox/"
            "docker-v3-12-0-stable-promotion.yaml"
        ),
        "decision_pack_sha256": "sha256:" + ("1" * 64),
        "source": "operator_decision_pack",
        "decision_id": "docker-v3-12-0-stable-promotion",
        "chosen_option": "ghcr_stable_only",
        "operator_id": "janik",
    }


def _write_decision_pack(
    root,
    *,
    chosen_option: str = "",
    signed_by: str = "",
    decision_id: str = "docker-v3-12-0-stable-promotion",
    category: str = "docker_promotion",
    latest_move_is_operator_only: str = "true",
    target_version: str | None = "v3.12.0",
    commit: str | None = COMMIT,
    created_utc: str = "2026-05-22T14:00:00Z",
):
    inbox = root / "docs" / "operator_inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    path = inbox / "docker-v3-12-0-stable-promotion.yaml"
    target_line = f"target_version: {target_version}\n" if target_version else ""
    commit_line = f"commit: {commit}\n" if commit else ""
    path.write_text(
        "schema_version: waggledance.operator_decision_pack.v1\n"
        f"decision_id: {decision_id}\n"
        f"category: {category}\n"
        f"created_utc: {created_utc}\n"
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


def test_signed_option_with_contradictory_machine_data_fails_closed(
    tmp_path,
) -> None:
    _write_source_tree(tmp_path)
    pack = _write_decision_pack(
        tmp_path,
        chosen_option="ghcr_stable_only",
        signed_by="operator:janik:2026-05-24T00:00:00Z",
    )
    pack.write_text(
        pack.read_text(encoding="utf-8").replace(
            "  - id: ghcr_stable_only\n"
            "    summary: Stable only\n",
            "  - id: ghcr_stable_only\n"
            "    summary: Stable only\n"
            "    data:\n"
            "      moves_latest: true\n",
        ),
        encoding="utf-8",
    )

    auth = operator_authorization_from_decision_pack(
        pack,
        commit=COMMIT,
        target_version="v3.12.0",
    )

    assert auth is None


def test_signed_option_rejects_integer_boolean_lookalikes(tmp_path) -> None:
    _write_source_tree(tmp_path)
    pack = _write_decision_pack(
        tmp_path,
        chosen_option="ghcr_stable_only",
        signed_by="operator:janik:2026-05-24T00:00:00Z",
    )
    pack.write_text(
        pack.read_text(encoding="utf-8").replace(
            "  - id: ghcr_stable_only\n"
            "    summary: Stable only\n",
            "  - id: ghcr_stable_only\n"
            "    summary: Stable only\n"
            "    data:\n"
            "      moves_latest: 0\n"
            "      stable_promotion_authorized: 1\n"
            "      docker_promotion_deferred: 0\n",
        ),
        encoding="utf-8",
    )

    auth = operator_authorization_from_decision_pack(
        pack,
        commit=COMMIT,
        target_version="v3.12.0",
    )

    assert auth is None


def test_signed_stable_and_latest_choice_does_not_authorize_this_release(
    tmp_path,
) -> None:
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

    assert auth is None
    assert report["docker_stable_policy"] == "draft"
    assert "operator_authorization_missing" in report["blockers"]


def test_signed_defer_docker_choice_does_not_authorize_this_release(
    tmp_path,
) -> None:
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

    assert auth is None
    assert report["docker_stable_policy"] == "draft"
    assert "operator_authorization_missing" in report["blockers"]


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


def test_decision_pack_rejects_alias_only_or_conflicting_scope(
    tmp_path,
) -> None:
    _write_source_tree(tmp_path)
    alias_only = _write_decision_pack(
        tmp_path,
        chosen_option="ghcr_stable_only",
        signed_by="operator:janik:2026-05-24T00:00:00Z",
        target_version=None,
        commit=None,
    )
    alias_only.write_text(
        alias_only.read_text(encoding="utf-8")
        + f"release_version: v3.12.0\nsubject_commit: {COMMIT}\n",
        encoding="utf-8",
    )
    assert operator_authorization_from_decision_pack(
        alias_only,
        commit=COMMIT,
        target_version="v3.12.0",
    ) is None

    conflicting = _write_decision_pack(
        tmp_path,
        chosen_option="ghcr_stable_only",
        signed_by="operator:janik:2026-05-24T00:00:00Z",
    )
    conflicting.write_text(
        conflicting.read_text(encoding="utf-8")
        + "release_version: v999.0.0\n"
        + f"target_commit: {'0' * 40}\n",
        encoding="utf-8",
    )
    assert operator_authorization_from_decision_pack(
        conflicting,
        commit=COMMIT,
        target_version="v3.12.0",
    ) is None


@pytest.mark.parametrize("commit", [None, 1, b"0" * 40, "A" * 40])
def test_decision_pack_rejects_noncanonical_commit_values(
    tmp_path,
    commit,
) -> None:
    pack = _write_decision_pack(
        tmp_path,
        chosen_option="ghcr_stable_only",
        signed_by="operator:janik:2026-05-24T00:00:00Z",
    )

    assert operator_authorization_from_decision_pack(
        pack,
        commit=commit,
        target_version="v3.12.0",
    ) is None


def test_decision_pack_rejects_duplicate_yaml_keys(tmp_path) -> None:
    _write_source_tree(tmp_path)
    pack = _write_decision_pack(
        tmp_path,
        chosen_option="ghcr_stable_only",
        signed_by="operator:janik:2026-05-24T00:00:00Z",
    )
    pack.write_text(
        pack.read_text(encoding="utf-8") + f"commit: {'f' * 40}\n",
        encoding="utf-8",
    )

    assert operator_authorization_from_decision_pack(
        pack,
        commit=COMMIT,
        target_version="v3.12.0",
    ) is None


@pytest.mark.parametrize(
    ("target_version", "commit"),
    [
        (None, COMMIT),
        ("v3.12.0", None),
        (None, None),
    ],
)
def test_signed_decision_pack_requires_exact_machine_scope(
    tmp_path,
    target_version,
    commit,
) -> None:
    _write_source_tree(tmp_path)
    pack = _write_decision_pack(
        tmp_path,
        chosen_option="ghcr_stable_only",
        signed_by="operator:janik:2026-05-24T00:00:00Z",
        target_version=target_version,
        commit=commit,
    )

    assert operator_authorization_from_decision_pack(
        pack,
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


@pytest.mark.parametrize(
    "signed_by",
    [
        "operator:janik:0001-01-01T00:00:00+14:00",
        "operator:janik:9999-12-31T23:59:59-14:00",
    ],
)
def test_decision_pack_rejects_timestamp_normalization_overflow(
    tmp_path,
    signed_by,
) -> None:
    _write_source_tree(tmp_path)
    pack = _write_decision_pack(
        tmp_path,
        chosen_option="ghcr_stable_only",
        signed_by=signed_by,
    )

    assert operator_authorization_from_decision_pack(
        pack,
        commit=COMMIT,
        target_version="v3.12.0",
    ) is None


def test_decision_pack_huge_yaml_integer_fails_closed(tmp_path) -> None:
    _write_source_tree(tmp_path)
    pack = _write_decision_pack(
        tmp_path,
        chosen_option="ghcr_stable_only",
        signed_by="operator:janik:2026-05-24T00:00:00Z",
    )
    pack.write_text(
        pack.read_text(encoding="utf-8")
        + "oversized_integer: "
        + ("9" * 5000)
        + "\n",
        encoding="utf-8",
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


def test_commented_canonical_cmd_cannot_mask_effective_docker_cmd(
    tmp_path,
) -> None:
    _write_source_tree(tmp_path)
    malicious_cmd = ["python", "-c", "print('not the runtime')"]
    (tmp_path / "Dockerfile").write_text(
        "# CMD "
        + json.dumps(CANONICAL_ENTRYPOINT)
        + "\nCMD "
        + json.dumps(malicious_cmd)
        + "\n",
        encoding="utf-8",
    )

    report = build_report(
        source_root=tmp_path,
        commit=COMMIT,
        operator_authorization=_authorization(),
    )

    assert report["static_checks"]["dockerfile_entrypoint_canonical"] is False
    assert report["entrypoints"]["dockerfile_cmd"] == malicious_cmd
    assert report["docker_stable_policy"] == "draft"
    assert "static_check_not_pass:dockerfile_entrypoint_canonical" in report[
        "blockers"
    ]


@pytest.mark.parametrize(
    ("dockerfile_text", "entrypoint_check"),
    [
        (
            "FROM python:3.13-slim\n"
            'ENTRYPOINT ["python", "-c", "print(\\"hostile\\")"]\n'
            f"CMD {json.dumps(CANONICAL_ENTRYPOINT)}\n",
            False,
        ),
        (
            "FROM python:3.13-slim\n"
            "ENTRYPOINT []\n"
            "RUN printf ignored \\\n"
            f"    CMD {json.dumps(CANONICAL_ENTRYPOINT)}\n",
            True,
        ),
        (
            "FROM python:3.13-slim\n"
            "ENTRYPOINT []\n"
            "RUN <<EOF\n"
            f"CMD {json.dumps(CANONICAL_ENTRYPOINT)}\n"
            "EOF\n",
            True,
        ),
    ],
)
def test_dockerfile_policy_pin_blocks_effective_command_parser_tricks(
    tmp_path,
    dockerfile_text,
    entrypoint_check,
) -> None:
    _write_source_tree(tmp_path)
    (tmp_path / "Dockerfile").write_text(
        dockerfile_text,
        encoding="utf-8",
    )

    report = build_report(
        source_root=tmp_path,
        commit=COMMIT,
        operator_authorization=_authorization(),
    )

    assert report["static_checks"]["dockerfile_policy_hash_pinned"] is False
    assert (
        report["static_checks"]["dockerfile_entrypoint_canonical"]
        is entrypoint_check
    )
    assert not all(report["static_checks"].values())
    assert report["docker_stable_policy"] == "draft"


def test_build_report_rejects_future_generation_time(tmp_path) -> None:
    _write_source_tree(tmp_path)

    report = build_report(
        source_root=tmp_path,
        commit=COMMIT,
        operator_authorization=_authorization(),
        generated_at_utc=dt.datetime(2999, 1, 1, tzinfo=dt.UTC),
    )

    assert report["docker_stable_policy"] == "draft"
    assert "generated_at_utc_in_future" in report["blockers"]


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


def test_build_report_verifies_clean_required_sources_at_exact_commit(
    tmp_path,
    monkeypatch,
) -> None:
    source_root = tmp_path / "repo"
    source_root.mkdir()
    _write_source_tree(source_root)
    commit = _commit_source_tree(source_root)
    monkeypatch.setattr(
        docker_policy,
        "inspect_source_commit_binding",
        REAL_INSPECT_SOURCE_COMMIT_BINDING,
    )
    monkeypatch.setattr(
        docker_policy,
        "_load_committed_source_snapshot",
        REAL_LOAD_COMMITTED_SOURCE_SNAPSHOT,
    )

    report = build_report(
        source_root=source_root,
        commit=commit,
        operator_authorization=_authorization(commit=commit),
    )

    assert report["source_commit_binding"]["verified"] is True
    assert set(report["source_commit_binding"]["source_blob_oids"]) == {
        str(path) for path in docker_policy.REQUIRED_SOURCE_FILES
    }
    assert report["docker_stable_policy"] == "finalized"
    assert report["blockers"] == []


def test_sha256_git_repository_can_finalize_source_bound_report(
    tmp_path,
    monkeypatch,
) -> None:
    source_root = tmp_path / "repo-sha256"
    source_root.mkdir()
    initialized = subprocess.run(
        ["git", "-C", str(source_root), "init", "--object-format=sha256"],
        check=False,
        capture_output=True,
        text=True,
    )
    if initialized.returncode != 0:
        pytest.skip("installed Git does not support SHA-256 repositories")
    _git(source_root, "config", "user.name", "WaggleDance Test")
    _git(source_root, "config", "user.email", "test@waggledance.invalid")
    _git(source_root, "config", "core.autocrlf", "true")
    _write_source_tree(source_root)
    _git(source_root, "add", "--all")
    _git(source_root, "commit", "-m", "sha256 subject")
    subject_commit = _git(source_root, "rev-parse", "HEAD").stdout.strip()
    assert len(subject_commit) == 64
    subject_time = dt.datetime.fromisoformat(
        _git(
            source_root,
            "show",
            "-s",
            "--format=%cI",
            subject_commit,
        ).stdout.strip(),
    ).astimezone(dt.UTC)
    timestamp = docker_policy._format_utc(subject_time)
    pack = _write_decision_pack(
        source_root,
        chosen_option="ghcr_stable_only",
        signed_by=f"operator:janik:{timestamp}",
        commit=subject_commit,
        created_utc=timestamp,
    )
    _git(source_root, "add", pack.relative_to(source_root).as_posix())
    _git(source_root, "commit", "-m", "retain sha256 scoped pack")
    authorization = operator_authorization_from_decision_pack(
        pack,
        commit=subject_commit,
        target_version="v3.12.0",
        source_root=source_root,
    )
    assert authorization is not None
    monkeypatch.setattr(
        docker_policy,
        "inspect_source_commit_binding",
        REAL_INSPECT_SOURCE_COMMIT_BINDING,
    )
    monkeypatch.setattr(
        docker_policy,
        "_load_committed_source_snapshot",
        REAL_LOAD_COMMITTED_SOURCE_SNAPSHOT,
    )
    monkeypatch.setattr(
        docker_policy,
        "inspect_operator_authorization_source",
        REAL_INSPECT_OPERATOR_AUTHORIZATION_SOURCE,
    )

    report = build_report(
        source_root=source_root,
        commit=subject_commit,
        operator_authorization=authorization,
    )

    assert report["source_commit_binding"]["verified"] is True
    assert report["operator_authorization_source_binding"]["verified"] is True
    assert report["docker_stable_policy"] == "finalized"
    assert report["blockers"] == []


@pytest.mark.parametrize("staged", [False, True])
def test_build_report_rejects_required_source_changed_from_commit(
    tmp_path,
    monkeypatch,
    staged,
) -> None:
    source_root = tmp_path / "repo"
    source_root.mkdir()
    _write_source_tree(source_root)
    commit = _commit_source_tree(source_root)
    quickstart = (
        source_root / "docs" / "deployment" / "DOCKER_QUICKSTART.md"
    )
    committed_quickstart_hash = docker_policy.inspect_static_policy(
        source_root,
    )["source_hashes"]["docs/deployment/DOCKER_QUICKSTART.md"]
    quickstart.write_text("changed after commit\n", encoding="utf-8")
    if staged:
        _git(source_root, "add", quickstart.relative_to(source_root).as_posix())
    monkeypatch.setattr(
        docker_policy,
        "inspect_source_commit_binding",
        REAL_INSPECT_SOURCE_COMMIT_BINDING,
    )
    monkeypatch.setattr(
        docker_policy,
        "_load_committed_source_snapshot",
        REAL_LOAD_COMMITTED_SOURCE_SNAPSHOT,
    )

    report = build_report(
        source_root=source_root,
        commit=commit,
        operator_authorization=_authorization(commit=commit),
    )

    expected_reason = (
        "source_index_mismatch:docs/deployment/DOCKER_QUICKSTART.md"
        if staged
        else "source_mismatch:docs/deployment/DOCKER_QUICKSTART.md"
    )
    assert report["source_commit_binding"] == {
        "commit": commit,
        "verified": False,
        "reason": expected_reason,
        "source_blob_oids": {},
    }
    assert report["docker_stable_policy"] == "draft"
    assert (
        report["source_hashes"]["docs/deployment/DOCKER_QUICKSTART.md"]
        == committed_quickstart_hash
    )
    assert f"source_commit_not_verified:{expected_reason}" in report["blockers"]


def test_source_commit_binding_rejects_non_repo_and_unknown_commit(
    tmp_path,
) -> None:
    source_root = tmp_path / "repo"
    source_root.mkdir()
    _write_source_tree(source_root)

    no_repo = REAL_INSPECT_SOURCE_COMMIT_BINDING(source_root, COMMIT)
    assert no_repo["verified"] is False
    assert no_repo["reason"] in {
        "git_root_unavailable",
        "source_root_not_git_top_level",
    }

    commit = _commit_source_tree(source_root)
    unknown = REAL_INSPECT_SOURCE_COMMIT_BINDING(
        source_root,
        "0" * len(commit),
    )
    assert unknown["verified"] is False
    assert unknown["reason"] == "commit_not_found"


@pytest.mark.parametrize("commit", [None, 1, b"0" * 40, "A" * 40])
def test_source_commit_binding_rejects_noncanonical_commit_values(
    tmp_path,
    commit,
) -> None:
    binding = REAL_INSPECT_SOURCE_COMMIT_BINDING(tmp_path, commit)

    assert binding["verified"] is False
    assert binding["reason"] == "commit_invalid"


def test_git_bindings_ignore_inherited_git_repository_overrides(
    tmp_path,
    monkeypatch,
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    _write_source_tree(source_root)
    source_commit = _commit_source_tree(source_root)

    attacker_root = tmp_path / "attacker"
    attacker_root.mkdir()
    _write_source_tree(attacker_root)
    _commit_source_tree(attacker_root)
    monkeypatch.setenv("GIT_DIR", str(attacker_root / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(attacker_root))

    assert docker_policy._current_commit(source_root) == source_commit
    binding = REAL_INSPECT_SOURCE_COMMIT_BINDING(
        source_root,
        source_commit,
    )
    assert binding["verified"] is True
    assert binding["reason"] == "verified"


def test_source_commit_binding_rejects_nonancestor_subject(tmp_path) -> None:
    source_root = tmp_path / "repo"
    source_root.mkdir()
    _write_source_tree(source_root)
    subject_commit = _commit_source_tree(source_root)
    tree = _git(
        source_root,
        "rev-parse",
        f"{subject_commit}^{{tree}}",
    ).stdout.strip()
    unrelated_head = _git(
        source_root,
        "commit-tree",
        tree,
        "-m",
        "unrelated storage root",
    ).stdout.strip()
    _git(source_root, "update-ref", "HEAD", unrelated_head)

    binding = REAL_INSPECT_SOURCE_COMMIT_BINDING(
        source_root,
        subject_commit,
    )

    assert binding["verified"] is False
    assert binding["reason"] == "commit_not_ancestor_of_head"


def test_source_commit_binding_rejects_untracked_required_replacement(
    tmp_path,
) -> None:
    source_root = tmp_path / "repo"
    source_root.mkdir()
    _write_source_tree(source_root)
    quickstart = (
        source_root / "docs" / "deployment" / "DOCKER_QUICKSTART.md"
    )
    quickstart.unlink()
    commit = _commit_source_tree(source_root)
    quickstart.write_text("# replacement\n", encoding="utf-8")

    binding = REAL_INSPECT_SOURCE_COMMIT_BINDING(source_root, commit)

    assert binding["verified"] is False
    assert binding["reason"] == (
        "source_missing_at_commit:docs/deployment/DOCKER_QUICKSTART.md"
    )


def test_source_commit_binding_rejects_nested_git_copy(tmp_path) -> None:
    repository = tmp_path / "repo"
    source_root = repository / "nested"
    source_root.mkdir(parents=True)
    _write_source_tree(source_root)
    commit = _commit_source_tree(repository)

    binding = REAL_INSPECT_SOURCE_COMMIT_BINDING(source_root, commit)

    assert binding["verified"] is False
    assert binding["reason"] == "source_root_not_git_top_level"


def test_source_commit_binding_accepts_clean_crlf_worktree_text(
    tmp_path,
) -> None:
    source_root = tmp_path / "repo"
    source_root.mkdir()
    _write_source_tree(source_root)
    commit = _commit_source_tree(source_root)
    dockerfile = source_root / "Dockerfile"
    source_hash_before = docker_policy.inspect_static_policy(
        source_root,
    )["source_hashes"]["Dockerfile"]
    dockerfile.write_bytes(
        dockerfile.read_bytes().replace(b"\r\n", b"\n").replace(
            b"\n",
            b"\r\n",
        ),
    )

    binding = REAL_INSPECT_SOURCE_COMMIT_BINDING(source_root, commit)

    assert binding["verified"] is True
    assert binding["reason"] == "verified"
    assert (
        docker_policy.inspect_static_policy(
            source_root,
        )["source_hashes"]["Dockerfile"]
        == source_hash_before
    )


def test_source_commit_binding_ignores_local_clean_filter_mask(
    tmp_path,
) -> None:
    source_root = tmp_path / "repo"
    source_root.mkdir()
    _write_source_tree(source_root)
    commit = _commit_source_tree(source_root)
    info_attributes = source_root / ".git" / "info" / "attributes"
    info_attributes.write_text(
        "Dockerfile filter=auditmask\n",
        encoding="utf-8",
    )
    _git(
        source_root,
        "config",
        "filter.auditmask.clean",
        "git show HEAD:Dockerfile",
    )
    _git(source_root, "config", "filter.auditmask.required", "true")
    (source_root / "Dockerfile").write_text(
        "RUN echo HOSTILE_UNCOMMITTED_CONTENT\n",
        encoding="utf-8",
    )

    binding = REAL_INSPECT_SOURCE_COMMIT_BINDING(source_root, commit)

    assert binding["verified"] is False
    assert binding["reason"] == "source_mismatch:Dockerfile"


def test_source_commit_binding_ignores_git_replace_objects(tmp_path) -> None:
    source_root = tmp_path / "repo"
    source_root.mkdir()
    _write_source_tree(source_root)
    claimed_commit = _commit_source_tree(source_root)
    (source_root / "Dockerfile").write_text(
        "RUN echo HOSTILE_REPLACEMENT_TREE\n",
        encoding="utf-8",
    )
    _git(source_root, "add", "Dockerfile")
    _git(source_root, "commit", "-m", "different tree")
    replacement_commit = _git(
        source_root,
        "rev-parse",
        "HEAD",
    ).stdout.strip()
    _git(source_root, "read-tree", claimed_commit)
    _git(source_root, "replace", claimed_commit, replacement_commit)

    binding = REAL_INSPECT_SOURCE_COMMIT_BINDING(
        source_root,
        claimed_commit,
    )

    assert binding["verified"] is False
    assert binding["reason"] == "source_mismatch:Dockerfile"


def test_source_commit_binding_rejects_nonregular_commit_entry(
    tmp_path,
) -> None:
    source_root = tmp_path / "repo"
    source_root.mkdir()
    _write_source_tree(source_root)
    _git(source_root, "init")
    _git(source_root, "config", "user.name", "WaggleDance Test")
    _git(source_root, "config", "user.email", "test@waggledance.invalid")
    _git(source_root, "config", "core.autocrlf", "true")
    _git(source_root, "add", "--all")
    dockerfile_oid = _git(
        source_root,
        "hash-object",
        "Dockerfile",
    ).stdout.strip()
    _git(
        source_root,
        "update-index",
        "--cacheinfo",
        f"120000,{dockerfile_oid},Dockerfile",
    )
    _git(source_root, "commit", "-m", "symlink-mode fixture")
    commit = _git(source_root, "rev-parse", "HEAD").stdout.strip()

    binding = REAL_INSPECT_SOURCE_COMMIT_BINDING(source_root, commit)

    assert binding["verified"] is False
    assert binding["reason"] == "source_not_regular_at_commit:Dockerfile"


def test_main_default_commit_is_resolved_from_source_root(
    tmp_path,
    monkeypatch,
) -> None:
    source_root = tmp_path / "repo"
    source_root.mkdir()
    _write_source_tree(source_root)
    commit = _commit_source_tree(source_root)
    monkeypatch.setattr(
        docker_policy,
        "inspect_source_commit_binding",
        REAL_INSPECT_SOURCE_COMMIT_BINDING,
    )
    monkeypatch.setattr(
        docker_policy,
        "_load_committed_source_snapshot",
        REAL_LOAD_COMMITTED_SOURCE_SNAPSHOT,
    )
    output = tmp_path / "evidence.json"
    monkeypatch.chdir(tmp_path)

    rc = main([
        "--source-root",
        str(source_root),
        "--output",
        str(output),
        "--allow-draft",
    ])

    assert rc == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["commit"] == commit
    assert report["docker_stable_policy"] == "draft"


def test_main_finalizes_only_from_tracked_exact_scoped_decision_pack(
    tmp_path,
    monkeypatch,
) -> None:
    source_root = tmp_path / "repo-\u03a9"
    source_root.mkdir()
    _write_source_tree(source_root)
    subject_commit = _commit_source_tree(source_root)
    subject_time = dt.datetime.fromisoformat(
        _git(
            source_root,
            "show",
            "-s",
            "--format=%cI",
            subject_commit,
        ).stdout.strip(),
    ).astimezone(dt.UTC)
    created_at = docker_policy._format_utc(subject_time)
    authorized_at = docker_policy._format_utc(subject_time)
    pack = _write_decision_pack(
        source_root,
        chosen_option="ghcr_stable_only",
        signed_by=f"operator:janik:{authorized_at}",
        commit=subject_commit,
        created_utc=created_at,
    )
    _git(
        source_root,
        "add",
        pack.relative_to(source_root).as_posix(),
    )
    _git(source_root, "commit", "-m", "retain scoped operator pack")
    monkeypatch.setattr(
        docker_policy,
        "inspect_source_commit_binding",
        REAL_INSPECT_SOURCE_COMMIT_BINDING,
    )
    monkeypatch.setattr(
        docker_policy,
        "_load_committed_source_snapshot",
        REAL_LOAD_COMMITTED_SOURCE_SNAPSHOT,
    )
    monkeypatch.setattr(
        docker_policy,
        "inspect_operator_authorization_source",
        REAL_INSPECT_OPERATOR_AUTHORIZATION_SOURCE,
    )
    output = tmp_path / "evidence.json"

    rc = main([
        "--source-root",
        str(source_root),
        "--commit",
        subject_commit,
        "--operator-decision-pack",
        str(pack),
        "--output",
        str(output),
    ])

    assert rc == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["docker_stable_policy"] == "finalized"
    assert report["operator_authorization_source_binding"]["verified"] is True

    clean_pack = pack.read_bytes()
    pack.write_bytes(clean_pack + b"# staged replacement\n")
    _git(source_root, "add", pack.relative_to(source_root).as_posix())
    pack.write_bytes(clean_pack)
    staged_blockers = evaluate_report(
        report,
        expected_commit=subject_commit,
        source_root=source_root,
    )
    assert (
        "operator_authorization_source_not_verified:"
        "decision_pack_index_mismatch"
    ) in staged_blockers
    _git(source_root, "read-tree", "HEAD")

    pack.write_text(
        pack.read_text(encoding="utf-8") + "# tampered after evidence\n",
        encoding="utf-8",
    )
    blockers = evaluate_report(
        report,
        expected_commit=subject_commit,
        source_root=source_root,
    )
    assert (
        "operator_authorization_source_not_verified:"
        "decision_pack_worktree_mismatch"
    ) in blockers


def test_build_report_rejects_self_asserted_authorization_without_pack(
    tmp_path,
    monkeypatch,
) -> None:
    source_root = tmp_path / "repo"
    source_root.mkdir()
    _write_source_tree(source_root)
    commit = _commit_source_tree(source_root)
    monkeypatch.setattr(
        docker_policy,
        "inspect_operator_authorization_source",
        REAL_INSPECT_OPERATOR_AUTHORIZATION_SOURCE,
    )

    report = build_report(
        source_root=source_root,
        commit=commit,
        operator_authorization=_authorization(commit=commit),
    )

    assert report["docker_stable_policy"] == "draft"
    assert (
        "operator_authorization_source_not_verified:"
        "decision_pack_not_tracked"
    ) in report["blockers"]


def test_build_report_rejects_scoped_pack_with_stale_signoff(
    tmp_path,
    monkeypatch,
) -> None:
    source_root = tmp_path / "repo"
    source_root.mkdir()
    _write_source_tree(source_root)
    subject_commit = _commit_source_tree(source_root)
    pack = _write_decision_pack(
        source_root,
        chosen_option="ghcr_stable_only",
        signed_by="operator:janik:2026-05-24T00:00:00Z",
        commit=subject_commit,
    )
    _git(
        source_root,
        "add",
        pack.relative_to(source_root).as_posix(),
    )
    _git(source_root, "commit", "-m", "retain stale copied signoff")
    authorization = operator_authorization_from_decision_pack(
        pack,
        commit=subject_commit,
        target_version="v3.12.0",
        source_root=source_root,
    )
    assert authorization is not None
    monkeypatch.setattr(
        docker_policy,
        "inspect_source_commit_binding",
        REAL_INSPECT_SOURCE_COMMIT_BINDING,
    )
    monkeypatch.setattr(
        docker_policy,
        "_load_committed_source_snapshot",
        REAL_LOAD_COMMITTED_SOURCE_SNAPSHOT,
    )
    monkeypatch.setattr(
        docker_policy,
        "inspect_operator_authorization_source",
        REAL_INSPECT_OPERATOR_AUTHORIZATION_SOURCE,
    )

    report = build_report(
        source_root=source_root,
        commit=subject_commit,
        operator_authorization=authorization,
    )

    assert report["docker_stable_policy"] == "draft"
    assert (
        "operator_authorization_source_not_verified:"
        "decision_pack_signoff_predates_subject"
    ) in report["blockers"]


def test_build_report_rejects_pack_time_after_storage_commit(
    tmp_path,
    monkeypatch,
) -> None:
    source_root = tmp_path / "repo"
    source_root.mkdir()
    _write_source_tree(source_root)
    subject_commit = _commit_source_tree(source_root)
    pack = _write_decision_pack(
        source_root,
        chosen_option="ghcr_stable_only",
        signed_by="operator:janik:2999-01-01T00:00:00Z",
        commit=subject_commit,
        created_utc="2999-01-01T00:00:00Z",
    )
    _git(
        source_root,
        "add",
        pack.relative_to(source_root).as_posix(),
    )
    _git(source_root, "commit", "-m", "retain impossible future signoff")
    authorization = operator_authorization_from_decision_pack(
        pack,
        commit=subject_commit,
        target_version="v3.12.0",
        source_root=source_root,
    )
    assert authorization is not None
    monkeypatch.setattr(
        docker_policy,
        "inspect_source_commit_binding",
        REAL_INSPECT_SOURCE_COMMIT_BINDING,
    )
    monkeypatch.setattr(
        docker_policy,
        "_load_committed_source_snapshot",
        REAL_LOAD_COMMITTED_SOURCE_SNAPSHOT,
    )
    monkeypatch.setattr(
        docker_policy,
        "inspect_operator_authorization_source",
        REAL_INSPECT_OPERATOR_AUTHORIZATION_SOURCE,
    )

    report = build_report(
        source_root=source_root,
        commit=subject_commit,
        operator_authorization=authorization,
    )

    assert report["docker_stable_policy"] == "draft"
    assert (
        "operator_authorization_source_not_verified:"
        "decision_pack_time_after_storage_commit"
    ) in report["blockers"]


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


@pytest.mark.parametrize(
    ("field", "value", "expected_blocker"),
    [
        (
            "post_tag_runtime_verification_required",
            False,
            "post_tag_runtime_verification_not_required",
        ),
        (
            "latest_move_requires_operator_opt_in",
            False,
            "latest_move_operator_opt_in_not_required",
        ),
        (
            "blockers",
            ["forged"],
            "reported_blockers_mismatch",
        ),
        (
            "docker_stable_policy",
            "draft",
            "docker_stable_policy_mismatch",
        ),
    ],
)
def test_evaluate_report_rejects_forged_derived_fields(
    tmp_path,
    field,
    value,
    expected_blocker,
) -> None:
    _write_source_tree(tmp_path)
    report = build_report(
        source_root=tmp_path,
        commit=COMMIT,
        operator_authorization=_authorization(),
    )
    report[field] = value

    assert expected_blocker in evaluate_report(
        report,
        expected_commit=COMMIT,
        source_root=tmp_path,
    )


def test_public_evaluator_cannot_disable_derived_field_validation(
    tmp_path,
) -> None:
    _write_source_tree(tmp_path)
    report = build_report(
        source_root=tmp_path,
        commit=COMMIT,
        operator_authorization=_authorization(),
    )

    with pytest.raises(TypeError):
        evaluate_report(
            report,
            expected_commit=COMMIT,
            source_root=tmp_path,
            validate_derived_fields=False,
        )


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


def test_evaluate_report_rejects_forged_source_commit_binding(tmp_path) -> None:
    _write_source_tree(tmp_path)
    report = build_report(
        source_root=tmp_path,
        commit=COMMIT,
        operator_authorization=_authorization(),
    )
    report["source_commit_binding"]["source_blob_oids"] = {
        "Dockerfile": "0" * 40,
    }

    assert "source_commit_binding_mismatch" in evaluate_report(
        report,
        expected_commit=COMMIT,
        source_root=tmp_path,
    )


def test_evaluate_report_binds_authorization_to_report_commit(tmp_path) -> None:
    _write_source_tree(tmp_path)
    authorization = _authorization(commit="f" * 40)
    report = build_report(
        source_root=tmp_path,
        commit=COMMIT,
        operator_authorization=authorization,
    )

    assert "operator_authorization_commit_mismatch" in evaluate_report(
        report,
        source_root=tmp_path,
    )


@pytest.mark.parametrize(
    ("field", "value", "expected_blocker"),
    [
        (
            "source",
            "forged_json",
            "operator_authorization_source_invalid",
        ),
        (
            "decision_id",
            "other",
            "operator_authorization_decision_id_invalid",
        ),
        (
            "chosen_option",
            "unknown",
            "operator_authorization_chosen_option_invalid",
        ),
        (
            "stable_promotion_authorized",
            1,
            "operator_authorization_stable_promotion_authorized_mismatch",
        ),
        (
            "docker_promotion_deferred",
            True,
            "operator_authorization_docker_promotion_deferred_mismatch",
        ),
        (
            "move_latest",
            "yes",
            "operator_authorization_move_latest_mismatch",
        ),
        (
            "operator_id",
            "",
            "operator_authorization_operator_id_missing",
        ),
        (
            "authorization_id",
            "forged",
            "operator_authorization_id_mismatch",
        ),
        (
            "authorized_at_utc",
            "2999-01-01T00:00:00Z",
            "operator_authorized_after_report_generation",
        ),
    ],
)
def test_evaluate_report_rejects_forged_authorization_provenance(
    tmp_path,
    field,
    value,
    expected_blocker,
) -> None:
    _write_source_tree(tmp_path)
    authorization = _authorization()
    authorization[field] = value
    report = build_report(
        source_root=tmp_path,
        commit=COMMIT,
        operator_authorization=authorization,
    )

    assert expected_blocker in evaluate_report(
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
        source_root,
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


def test_main_rejects_signed_decision_pack_without_machine_scope(
    tmp_path,
) -> None:
    source_root = tmp_path / "repo"
    source_root.mkdir()
    _write_source_tree(source_root)
    pack = _write_decision_pack(
        source_root,
        chosen_option="ghcr_stable_only",
        signed_by="operator:janik:2026-05-24T00:00:00Z",
        target_version=None,
        commit=None,
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

    assert rc == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["docker_stable_policy"] == "draft"
    assert "operator_authorization_missing" in report["blockers"]


def test_main_rejects_raw_operator_authorization_json(tmp_path) -> None:
    source_root = tmp_path / "repo"
    source_root.mkdir()
    _write_source_tree(source_root)
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
        "--output",
        str(output),
    ])

    assert rc == 2
    assert not output.exists()


def test_main_rejects_ambiguous_authorization_sources(tmp_path) -> None:
    source_root = tmp_path / "repo"
    source_root.mkdir()
    _write_source_tree(source_root)
    pack = _write_decision_pack(
        source_root,
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
