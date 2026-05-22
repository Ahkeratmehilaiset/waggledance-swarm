# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json

from tools.run_release_docker_policy_evidence import (
    AUTH_SCHEMA_VERSION,
    CANONICAL_ENTRYPOINT,
    CANONICAL_SCRIPT,
    SCHEMA_VERSION,
    build_report,
    main,
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


def test_build_report_is_draft_without_operator_authorization(tmp_path) -> None:
    _write_source_tree(tmp_path)

    report = build_report(source_root=tmp_path, commit=COMMIT)

    assert report["schema_version"] == SCHEMA_VERSION
    assert all(report["static_checks"].values())
    assert report["operator_authorization"] is None
    assert report["docker_stable_policy"] == "draft"
    assert report["blockers"] == ["operator_authorization_missing"]


def test_build_report_finalizes_with_operator_authorization(tmp_path) -> None:
    _write_source_tree(tmp_path)

    report = build_report(
        source_root=tmp_path,
        commit=COMMIT,
        operator_authorization=_authorization(),
    )

    assert report["docker_stable_policy"] == "finalized"
    assert report["blockers"] == []


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
