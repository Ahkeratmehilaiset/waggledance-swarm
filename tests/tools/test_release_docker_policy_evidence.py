# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json

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


WORKFLOW = r"""
name: Release stable Docker images to GHCR
on:
  workflow_dispatch:
    inputs:
      tag:
        required: true
        type: string
      move_latest:
        required: true
        type: choice
        options: ["yes", "no"]
        default: "no"
      profile_aliases:
        required: false
        type: choice
        options: ["yes", "no"]
        default: "yes"
jobs:
  validate-tag:
    steps:
      - name: Refuse if tag is alpha/beta/rc/dev/pre
        run: |
          if [[ "$TAG" =~ (alpha|beta|rc|dev|pre)$ ]]; then exit 1; fi
          if [[ ! "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then exit 1; fi
  build-and-push:
    steps:
      - name: Build and push canonical stable image
        uses: docker/build-push-action@v6
        with:
          push: true
          tags: ghcr.io/ahkeratmehilaiset/waggledance:${{ needs.validate-tag.outputs.tag }}
      - name: Tag :stable sliding alias
        run: docker buildx imagetools create --tag ghcr.io/ahkeratmehilaiset/waggledance:stable ghcr.io/ahkeratmehilaiset/waggledance:${{ needs.validate-tag.outputs.tag }}
      - name: Tag :latest (operator-gated)
        if: ${{ inputs.move_latest == 'yes' }}
        run: docker buildx imagetools create --tag ghcr.io/ahkeratmehilaiset/waggledance:latest ghcr.io/ahkeratmehilaiset/waggledance:${{ needs.validate-tag.outputs.tag }}
      - name: Tag Profile S stable alias
        if: ${{ inputs.profile_aliases == 'yes' }}
        run: docker buildx imagetools create --tag ghcr.io/ahkeratmehilaiset/waggledance:small-stable ghcr.io/ahkeratmehilaiset/waggledance:${{ needs.validate-tag.outputs.tag }}
      - name: Tag Profile M stable alias
        if: ${{ inputs.profile_aliases == 'yes' }}
        run: docker buildx imagetools create --tag ghcr.io/ahkeratmehilaiset/waggledance:medium-stable ghcr.io/ahkeratmehilaiset/waggledance:${{ needs.validate-tag.outputs.tag }}
  smoke-test:
    steps:
      - name: Smoke - Profile S clean import + redactor + LLM disabled
        run: |
          docker run -e WAGGLE_PROFILE=small image python -c "print('SMOKE_OK_STABLE')"
      - name: Smoke - :stable alias resolves to canonical
        run: |
          docker pull ghcr.io/ahkeratmehilaiset/waggledance:stable
          CANONICAL_DIGEST=1
          if [ "$STABLE_DIGEST" != "$CANONICAL_DIGEST" ]; then
            echo "::error::stable alias does not resolve to canonical digest"
            exit 1
          fi
      - name: Smoke - :latest only if moved
        if: ${{ inputs.move_latest == 'yes' }}
        run: |
          docker pull ghcr.io/ahkeratmehilaiset/waggledance:latest
          CANONICAL_DIGEST=1
"""


def _write_source_tree(root, *, workflow: str = WORKFLOW) -> None:
    (root / ".github" / "workflows").mkdir(parents=True)
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
