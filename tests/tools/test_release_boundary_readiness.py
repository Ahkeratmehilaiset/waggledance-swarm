# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import importlib
import json
import os
import py_compile
import stat
import subprocess
import sys
import types
from pathlib import Path

import pytest

import tools.run_release_boundary_readiness as boundary
from tools.run_release_boundary_readiness import (
    CANONICAL_SOAK_EVIDENCE,
    DEFAULT_PHASE_SYNTHESIS_REFRESH,
    DEFAULT_RELEASE_GATE_RECHECK,
    FALSE_RELEASE_BOUNDARY,
    READ_ONLY_INVARIANTS,
    ROOT,
    SCHEMA_VERSION,
    build_report_from_paths,
    main,
    strict_exit_code,
)


FIXED_NOW = dt.datetime(2026, 6, 1, 3, 0, tzinfo=dt.UTC)
CANONICAL_SOAK_COMMIT = json.loads(
    CANONICAL_SOAK_EVIDENCE.read_text(encoding="utf-8")
)["commit"]
TEST_CARRIER_HEAD = "f" * 40
TEST_SUBJECT_TREE = "a" * 40
TEST_CARRIER_TREE = "b" * 40
OPERATOR_SIGNER = "operator:jani:2026-05-22T18:14:34Z"
TEST_GIT_EXECUTABLE = str(Path(Path(ROOT).anchor) / "wd-test-git")
EXPECTED_PYYAML_602_MANIFEST = (
    ("yaml/__init__.py", 12311,
     "377e52d351cc7ac1537b469144c5a43e3d0f6bc2046c7a44f452bb72be4176dc"),
    ("yaml/composer.py", 4883,
     "fcaa37d16afa783594794a5ab94193dcb720f503c19ce3d59539c8311189f453"),
    ("yaml/constructor.py", 28639,
     "90d8247da78b524c10618fd0e857f54f3d97570fe91b5c5513d024ef3faf88b0"),
    ("yaml/cyaml.py", 3851,
     "e99ac01bd7c062f7557b614aff0d21997a06ed962ca185306a91bc0a20bbd87d"),
    ("yaml/dumper.py", 2837,
     "3cb72d66563064ba7b5e679477046ebf89d8399d940670c8532f3e94a7cb17ea"),
    ("yaml/emitter.py", 43006,
     "8e086d694ede170837d5b1b407b45979aff6f40762f422a65eafd08e04290a44"),
    ("yaml/error.py", 2533,
     "021f73fada072546c4f63f8cf18a7181244ce4280b09cc15cc980b2d1176171a"),
    ("yaml/events.py", 2445,
     "e74fd392c810884e2ea7e94aa3f57e9c1cbeb402319083d0c58e6a0e1282787c"),
    ("yaml/loader.py", 2061,
     "5156becc8aa6905482218abf3e04869b835226db4763645fff3438fdbd5f1cdd"),
    ("yaml/nodes.py", 1440,
     "80f28d8fca4a09d87677882bde021820d9cf39a3b11a12405226211919cf13ce"),
    ("yaml/parser.py", 25495,
     "8a55a9e6fbe0a07146cef3990c8b45a068c3e83e369e1959ad9ca30306b4a09a"),
    ("yaml/reader.py", 6794,
     "d1d9b38ab3a20c6e17a38d519ee412ecaf6b918df18c78956ac7c330d4ea08dc"),
    ("yaml/representer.py", 14190,
     "22e58ff9c016f6c1ca1274b4802a926bcf78935060e1c813c5a0f021c6d143e6"),
    ("yaml/resolver.py", 9004,
     "f4bf9561f9b89961f1503d558385fbae30d12bfed565de9bf76c33abb63620a6"),
    ("yaml/scanner.py", 51279,
     "60433788b652690c17710460da5d91e0c753d3318fd85f5e1e42862a71f25906"),
    ("yaml/serializer.py", 4165,
     "0a1b85826854d35863e31808f0668abfabdf33606e8f06bd8bb7761401e3edc0"),
    ("yaml/tokens.py", 2573,
     "953408cd2570f0c83dc2fe39f7e4e388e41eeb05738aa69196a5f6ffcf6ba79e"),
)


def _configure_output_test_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Give writer tests an existing, repository-shaped temporary root."""
    test_root = tmp_path / "output-root"
    audit_root = test_root / ".codex-audit"
    audit_root.mkdir(parents=True)
    monkeypatch.setattr(boundary, "ROOT", test_root)
    monkeypatch.setattr(boundary, "_PYCACHE_ROOT", audit_root)
    return audit_root


def _phase_synthesis_refresh(
    *,
    status: str = "ready_for_release_boundary_review",
) -> dict[str, object]:
    return {
        "schema_version": "waggledance.magma_100h_phase_synthesis_refresh.v0",
        "sprint_id": "magma-100h-sprint3-2026-05-26",
        "generated_at_utc": "2026-06-01T02:40:00Z",
        "ok": True,
        "blockers": [],
        "release_boundary": dict(FALSE_RELEASE_BOUNDARY),
        "remaining_work_packages": [
            {
                "id": "operator_gated_authority_activation_decision",
                "status": "operator_approval_missing_decision_packet_recorded",
                "owner": "operator",
            },
            {
                "id": "release_soak_evidence_blocker_resolution",
                "status": status,
                "owner": "operator,codex",
                "acceptance": (
                    "release boundary remains false until release gate passes "
                    "and an explicit release-boundary authorization exists"
                ),
            },
        ],
    }


def _release_gate_recheck(
    *,
    decision: str = "pass",
    blockers: list[str] | None = None,
    checked_at_utc: str = "2026-06-01T02:55:00Z",
) -> dict[str, object]:
    blockers = list(blockers or [])
    return {
        "schema_version": "waggledance.release_gate_readonly_recheck.v0",
        "checked_at_utc": checked_at_utc,
        "ok": True,
        "read_only": True,
        "release_gate_decision": decision,
        "blockers": blockers,
        "release_gate_effect": "none",
        "release_boundary": dict(FALSE_RELEASE_BOUNDARY),
        "read_only_invariants": dict(READ_ONLY_INVARIANTS),
        "gate": {"decision": decision, "blockers": blockers},
    }


def _pass_live_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolated pass fixture for the private, pure report seam.

    Canonical wrapper and child protocol tests exercise their own seams below.
    """
    monkeypatch.setattr(
        boundary,
        "_run_live_release_gate",
        lambda checked_at_utc: _release_gate_recheck(
            checked_at_utc=boundary._format_utc(checked_at_utc)
        ),
    )


def _pack_entry(path: Path) -> dict[str, object]:
    raw = path.read_bytes()
    return {
        "path": str(path),
        "raw": raw,
        "digest": hashlib.sha256(raw).hexdigest(),
        "identity": (1, 2, stat.S_IFREG, len(raw), 3, 4),
        "error": None,
    }


def build_report(
    *,
    phase_synthesis_refresh: dict[str, object],
    release_gate_recheck: dict[str, object],
    torch_decision_pack: Path,
    docker_decision_pack: Path,
    checked_at_utc: dt.datetime | None = None,
) -> dict[str, object]:
    """Exercise the source's pure assembly seam without canonical I/O."""
    report_time, gate_time, time_blockers = boundary._checked_at_evaluation(
        checked_at_utc, wall_now=FIXED_NOW
    )
    live_summary, live_blockers = boundary._live_gate_evaluation(
        gate_time, wall_now=FIXED_NOW
    )
    return boundary._assemble_report(
        phase_synthesis_refresh=phase_synthesis_refresh,
        release_gate_recheck=release_gate_recheck,
        torch_pack=boundary._torch_pack_summary(_pack_entry(torch_decision_pack)),
        docker_pack=boundary._docker_pack_summary(_pack_entry(docker_decision_pack)),
        live_gate_summary=live_summary,
        binding={
            "git_head": CANONICAL_SOAK_COMMIT,
            "soak_commit": CANONICAL_SOAK_COMMIT,
            "soak_evidence_path": CANONICAL_SOAK_EVIDENCE.relative_to(ROOT).as_posix(),
        },
        report_checked_at=report_time,
        wall_now=FIXED_NOW,
        blockers=[*time_blockers, *live_blockers],
    )


def _write_torch_pack(
    path: Path,
    *,
    signed: bool = True,
    scope_updates_yaml: str = "",
    signed_by: str = OPERATOR_SIGNER,
    structural_invariants_yaml: str | None = None,
) -> Path:
    signed_by = f'"{signed_by}"' if signed else '""'
    chosen = "A2_cu126" if signed else ""
    invariants = structural_invariants_yaml or """structural_invariants:
  no_main_branch_auto_merge: true
  dependency_change_lands_via_pr: true
  agent_must_not_self_resolve: true
"""
    path.write_text(
        f"""schema_version: waggledance.operator_decision_pack.v1
decision_id: torch-cuda-vs-cpu
category: dependency_security
created_utc: 2026-05-22T14:00:00Z
author_agent: claude
options:
  - id: A1_cpu_only
    summary: CPU only
  - id: A2_cu126
    summary: CUDA 12.6
operator_signoff:
  signed_by: {signed_by}
  chosen_option: "{chosen}"
{scope_updates_yaml}{invariants}
""",
        encoding="utf-8",
    )
    return path


def _write_docker_pack(
    path: Path,
    *,
    signed: bool = True,
    chosen: str = "ghcr_stable_only",
    moves_latest_for_stable_only: bool = False,
) -> Path:
    signed_by = '"operator:jani:2026-05-22T18:14:34Z"' if signed else '""'
    chosen_option = chosen if signed else ""
    moves_latest = "true" if moves_latest_for_stable_only else "false"
    path.write_text(
        f"""schema_version: waggledance.operator_decision_pack.v1
decision_id: docker-latest-promotion
category: docker_promotion
created_utc: 2026-05-22T14:00:00Z
author_agent: claude
options:
  - id: ghcr_stable_only
    summary: Stable and version tag only
    data:
      moves_latest: {moves_latest}
      registries: ["ghcr.io"]
  - id: ghcr_stable_and_latest
    summary: Stable, version tag, and latest
    data:
      moves_latest: true
      registries: ["ghcr.io"]
operator_signoff:
  signed_by: {signed_by}
  chosen_option: "{chosen_option}"
structural_invariants:
  no_main_branch_auto_merge: true
  latest_move_is_operator_only: true
  agent_must_not_self_resolve: true
""",
        encoding="utf-8",
    )
    return path


def _build_pass_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    torch_scope_yaml: str = "",
) -> dict[str, object]:
    _pass_live_gate(monkeypatch)
    return build_report(
        phase_synthesis_refresh=_phase_synthesis_refresh(),
        release_gate_recheck=_release_gate_recheck(),
        torch_decision_pack=_write_torch_pack(
            tmp_path / "torch.yaml",
            scope_updates_yaml=torch_scope_yaml,
        ),
        docker_decision_pack=_write_docker_pack(tmp_path / "docker.yaml"),
        checked_at_utc=FIXED_NOW,
    )


def test_report_records_ready_for_operator_finalization_without_release_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _build_pass_report(tmp_path, monkeypatch)

    assert report["schema_version"] == SCHEMA_VERSION
    assert report["checked_at_utc"] == "2026-06-01T03:00:00Z"
    assert report["ok"] is True
    assert report["release_boundary_status"] == "ready_for_operator_finalization"
    assert report["release_boundary_blockers"] == []
    assert report["operator_finalization_required"] is True
    assert report["release_boundary"] == FALSE_RELEASE_BOUNDARY
    assert report["source_live_release_gate"]["release_gate_decision"] == "pass"
    assert report["head_soak_binding"]["git_head"] == CANONICAL_SOAK_COMMIT
    assert report["head_soak_binding"]["soak_commit"] == CANONICAL_SOAK_COMMIT
    assert report["release_boundary_guardrails"] == {
        "release_boundary_effect": "none",
        "tag_creation_applied": False,
        "docker_latest_move_applied": False,
        "docker_stable_move_applied": False,
        "stable_release_claim_applied": False,
        "external_effect_authority_change_applied": False,
        "requires_operator_only_finalization": True,
    }
    assert report["read_only_invariants"] == {
        "no_tag_created": True,
        "no_docker_latest_moved": True,
        "no_docker_stable_moved": True,
        "no_stable_release_claim": True,
        "no_external_effect_authority_change": True,
        "release_boundary_effect": "none",
    }
    docker_pack = report["operator_decision_packs"]["docker_latest_promotion"]
    assert docker_pack["chosen_option"] == "ghcr_stable_only"
    assert docker_pack["chosen_option_data"]["moves_latest"] is False
    packet = report["release_decision_packet"]
    assert packet["decision_status"] == "operator_finalization_required"
    assert packet["release_boundary_effect_before_followup"] == "none"
    assert packet["operator_finalization_required"] is True
    assert all(
        option["tag_creation_allowed"] is False
        and option["docker_latest_move_allowed"] is False
        and option["docker_stable_move_allowed"] is False
        and option["stable_release_claim_allowed"] is False
        and option["external_effect_authority_change_allowed"] is False
        for option in packet["decision_options"]
    )
    assert strict_exit_code(report) == 0


def test_landed_release_soak_package_keeps_readiness_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pass_live_gate(monkeypatch)
    phase = _phase_synthesis_refresh()
    phase["remaining_work_packages"] = [
        package
        for package in phase["remaining_work_packages"]
        if package["id"] != "release_soak_evidence_blocker_resolution"
    ]
    phase["landed_work_packages"] = [
        {
            "id": "release_soak_evidence_blocker_resolution",
            "status": "complete_release_boundary_readiness_recorded",
            "owner": "operator,codex",
        }
    ]

    report = build_report(
        phase_synthesis_refresh=phase,
        release_gate_recheck=_release_gate_recheck(),
        torch_decision_pack=_write_torch_pack(tmp_path / "torch.yaml"),
        docker_decision_pack=_write_docker_pack(tmp_path / "docker.yaml"),
        checked_at_utc=FIXED_NOW,
    )

    assert report["ok"] is True
    assert report["source_phase_synthesis_refresh"][
        "remaining_release_soak_package"
    ] == {
        "id": "release_soak_evidence_blocker_resolution",
        "status": None,
        "owner": None,
    }
    assert report["source_phase_synthesis_refresh"][
        "landed_release_soak_package"
    ] == {
        "id": "release_soak_evidence_blocker_resolution",
        "status": "complete_release_boundary_readiness_recorded",
        "owner": "operator,codex",
    }
    assert strict_exit_code(report) == 0


def test_unsigned_docker_decision_pack_blocks_readiness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pass_live_gate(monkeypatch)
    report = build_report(
        phase_synthesis_refresh=_phase_synthesis_refresh(),
        release_gate_recheck=_release_gate_recheck(),
        torch_decision_pack=_write_torch_pack(tmp_path / "torch.yaml"),
        docker_decision_pack=_write_docker_pack(
            tmp_path / "docker.yaml",
            signed=False,
        ),
        checked_at_utc=FIXED_NOW,
    )

    assert report["ok"] is False
    assert report["release_boundary_status"] == "hold_release_boundary_review_required"
    assert "docker_operator_decision_pack_unsigned" in report[
        "release_boundary_blockers"
    ]
    assert report["release_boundary"] == FALSE_RELEASE_BOUNDARY
    assert strict_exit_code(report) == 2


def test_docker_latest_move_in_signed_pack_blocks_readiness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pass_live_gate(monkeypatch)
    report = build_report(
        phase_synthesis_refresh=_phase_synthesis_refresh(),
        release_gate_recheck=_release_gate_recheck(),
        torch_decision_pack=_write_torch_pack(tmp_path / "torch.yaml"),
        docker_decision_pack=_write_docker_pack(
            tmp_path / "docker.yaml",
            moves_latest_for_stable_only=True,
        ),
        checked_at_utc=FIXED_NOW,
    )

    assert report["ok"] is False
    assert "docker_docker_latest_move_not_forbidden_by_pack" in report[
        "release_boundary_blockers"
    ]
    assert report["release_boundary"] == FALSE_RELEASE_BOUNDARY


def test_stale_snapshot_hold_still_blocks_as_continuity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pass_live_gate(monkeypatch)
    report = build_report(
        phase_synthesis_refresh=_phase_synthesis_refresh(),
        release_gate_recheck=_release_gate_recheck(
            decision="hold",
            blockers=["soak_evidence_duration_lt_336h"],
        ),
        torch_decision_pack=_write_torch_pack(tmp_path / "torch.yaml"),
        docker_decision_pack=_write_docker_pack(tmp_path / "docker.yaml"),
        checked_at_utc=FIXED_NOW,
    )

    assert report["ok"] is False
    assert "release_gate_not_passed" in report["release_boundary_blockers"]
    assert report["release_boundary"] == FALSE_RELEASE_BOUNDARY


def test_phase_status_must_be_ready_for_release_boundary_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pass_live_gate(monkeypatch)
    report = build_report(
        phase_synthesis_refresh=_phase_synthesis_refresh(
            status="blocked_until_release_gate_soak_evidence_passes"
        ),
        release_gate_recheck=_release_gate_recheck(),
        torch_decision_pack=_write_torch_pack(tmp_path / "torch.yaml"),
        docker_decision_pack=_write_docker_pack(tmp_path / "docker.yaml"),
        checked_at_utc=FIXED_NOW,
    )

    assert report["ok"] is False
    assert "release_soak_package_state_ambiguous" in report[
        "release_boundary_blockers"
    ]
    assert report["release_boundary"] == FALSE_RELEASE_BOUNDARY


def test_cli_writes_readiness_report_and_honors_strict(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _build_pass_report(tmp_path, monkeypatch)
    monkeypatch.setattr(
        boundary, "build_report_from_paths", lambda **kwargs: expected
    )
    audit_root = _configure_output_test_root(tmp_path, monkeypatch)
    output_path = audit_root / f"{tmp_path.name}-readiness.json"

    rc = main(
        [
            "--checked-at-utc",
            "2026-06-01T03:00:00Z",
            "--output",
            str(output_path),
            "--json",
            "--strict",
        ]
    )

    assert rc == 0
    stdout_report = json.loads(capsys.readouterr().out)
    disk_report = json.loads(output_path.read_text(encoding="utf-8"))
    assert stdout_report == disk_report
    assert disk_report["ok"] is True
    assert disk_report["release_boundary_status"] == (
        "ready_for_operator_finalization"
    )
    output_path.unlink()


def test_cli_hold_returns_nonzero_without_strict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit_root = _configure_output_test_root(tmp_path, monkeypatch)
    output_path = audit_root / f"{tmp_path.name}-readiness.json"
    live_hold = _release_gate_recheck(
        decision="hold",
        blockers=["soak_evidence_not_reproducible"],
        checked_at_utc="2026-06-01T03:00:00Z",
    )
    monkeypatch.setattr(
        boundary,
        "_run_live_release_gate",
        lambda checked_at_utc: live_hold,
    )
    expected = build_report(
        phase_synthesis_refresh=_phase_synthesis_refresh(),
        release_gate_recheck=_release_gate_recheck(),
        torch_decision_pack=_write_torch_pack(tmp_path / "torch.yaml"),
        docker_decision_pack=_write_docker_pack(tmp_path / "docker.yaml"),
        checked_at_utc=FIXED_NOW,
    )
    monkeypatch.setattr(
        boundary, "build_report_from_paths", lambda **kwargs: expected
    )

    rc = main(
        [
            "--checked-at-utc",
            "2026-06-01T03:00:00Z",
            "--output",
            str(output_path),
        ]
    )

    assert rc == 2
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["ok"] is False
    assert report["release_boundary"] == FALSE_RELEASE_BOUNDARY
    assert "live_release_gate_not_passed" in report["release_boundary_blockers"]
    output_path.unlink()


# --- Live-gate authority: the stale-snapshot false green cannot recur ---


def test_noncanonical_cli_overrides_can_only_add_hold_blockers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []

    def fake_build(checked_at_utc, extra_blockers=None):
        captured.extend(extra_blockers or [])
        return {
            "ok": not extra_blockers,
            "release_boundary": dict(FALSE_RELEASE_BOUNDARY),
            "release_boundary_blockers": list(extra_blockers or []),
        }

    monkeypatch.setattr(boundary, "_build_canonical_report", fake_build)
    report = build_report_from_paths(
        phase_synthesis_refresh_path=tmp_path / "phase.json",
        release_gate_recheck_path=tmp_path / "gate.json",
        torch_decision_pack=tmp_path / "torch.yaml",
        docker_decision_pack=tmp_path / "docker.yaml",
        checked_at_utc=FIXED_NOW,
    )

    assert report["ok"] is False
    assert captured == [
        "noncanonical_cli_override_phase_synthesis_refresh",
        "noncanonical_cli_override_release_gate_recheck",
        "noncanonical_cli_override_torch_decision_pack",
        "noncanonical_cli_override_docker_decision_pack",
    ]
    assert report["release_boundary"] == FALSE_RELEASE_BOUNDARY
    assert strict_exit_code(report) == 2


def test_main_never_writes_a_disallowed_canonical_input_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = CANONICAL_SOAK_EVIDENCE
    original = target.read_bytes()

    def fake_build(checked_at_utc, extra_blockers=None):
        return {
            "ok": False,
            "release_boundary": dict(FALSE_RELEASE_BOUNDARY),
            "release_boundary_blockers": list(extra_blockers or []),
        }

    monkeypatch.setattr(boundary, "_build_canonical_report", fake_build)
    rc = main(["--output", str(target)])

    assert rc == 2
    assert target.read_bytes() == original
    report = build_report_from_paths(
        phase_synthesis_refresh_path=DEFAULT_PHASE_SYNTHESIS_REFRESH,
        release_gate_recheck_path=DEFAULT_RELEASE_GATE_RECHECK,
        torch_decision_pack=boundary.DEFAULT_TORCH_DECISION_PACK,
        docker_decision_pack=boundary.DEFAULT_DOCKER_DECISION_PACK,
        output_path=target,
    )
    assert "output_path_not_allowed" in report[
        "release_boundary_blockers"
    ]


def test_writer_rejects_allowed_hardlink_to_noncanonical_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "disposable-source.json"
    source.write_text("unchanged", encoding="utf-8")
    original = source.read_bytes()
    audit_root = _configure_output_test_root(tmp_path, monkeypatch)
    alias = audit_root / f"{tmp_path.name}-{source.name}.json"
    alias.unlink(missing_ok=True)
    os.link(source, alias)
    try:
        assert boundary._output_path_blocker(alias) == "output_path_multiple_links"
        with pytest.raises(OSError, match="output_path_multiple_links"):
            boundary._write_output(alias, "must-not-be-written")
        assert source.read_bytes() == original
    finally:
        alias.unlink(missing_ok=True)


def test_canonical_input_capture_rejects_multiple_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(boundary, "ROOT", tmp_path)
    source = tmp_path / "captured.json"
    alias = tmp_path / "captured-alias.json"
    source.write_text("{}", encoding="utf-8")
    os.link(source, alias)

    assert boundary._capture_input(source)["error"] == "multiple_links"


@pytest.mark.parametrize(
    "path",
    [Path(boundary.__file__), ROOT / "pyproject.toml", ROOT / ".git",
     boundary._PYCACHE_ROOT],
)
def test_output_allowlist_rejects_source_git_and_audit_root(path: Path) -> None:
    assert boundary._output_path_blocker(path) == "output_path_not_allowed"


def test_output_allowlist_accepts_only_default_or_audit_descendant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert boundary._output_path_blocker(boundary.DEFAULT_OUTPUT) is None
    audit_root = _configure_output_test_root(tmp_path, monkeypatch)
    assert boundary._output_path_blocker(
        audit_root / "readiness.json"
    ) is None


def _directory_symlink(source: Path, target: Path) -> None:
    try:
        source.symlink_to(target, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")


def test_output_default_parent_reparse_cannot_redirect_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    safe_root = tmp_path / "safe"
    outside = tmp_path / "outside"
    safe_root.mkdir()
    outside.mkdir()
    _directory_symlink(safe_root / "docs", outside)
    target = safe_root / "docs" / "readiness.json"
    monkeypatch.setattr(boundary, "ROOT", safe_root)
    monkeypatch.setattr(boundary, "DEFAULT_OUTPUT", target)
    monkeypatch.setattr(boundary, "_PYCACHE_ROOT", safe_root / ".codex-audit")

    assert boundary._output_path_blocker(target) == "output_parent_reparse"
    with pytest.raises(OSError, match="output_parent_reparse"):
        boundary._write_output(target, "blocked")
    assert not (outside / target.name).exists()


def test_output_audit_parent_reparse_ignores_stale_safe_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    safe_root = tmp_path / "safe"
    outside = tmp_path / "outside"
    safe_root.mkdir()
    outside.mkdir()
    _directory_symlink(safe_root / ".codex-audit", outside)
    target = safe_root / ".codex-audit" / "readiness.json"
    monkeypatch.setattr(boundary, "ROOT", safe_root)
    monkeypatch.setattr(
        boundary, "DEFAULT_OUTPUT", safe_root / "existing" / "default.json"
    )
    monkeypatch.setattr(boundary, "_PYCACHE_ROOT", safe_root / ".codex-audit")
    monkeypatch.setattr(boundary, "_PYCACHE_ROOT_SAFE", True)

    assert boundary._output_path_blocker(target) == "output_parent_reparse"
    with pytest.raises(OSError, match="output_parent_reparse"):
        boundary._write_output(target, "blocked")
    assert not (outside / target.name).exists()


def test_output_parent_reparse_is_detected_without_platform_symlink_support(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    safe_root = tmp_path / "safe"
    audit_root = safe_root / ".codex-audit"
    audit_root.mkdir(parents=True)
    target = audit_root / "readiness.json"
    reparse_inode = os.lstat(audit_root).st_ino
    original_is_reparse = boundary._is_reparse
    monkeypatch.setattr(boundary, "ROOT", safe_root)
    monkeypatch.setattr(boundary, "_PYCACHE_ROOT", audit_root)
    monkeypatch.setattr(
        boundary,
        "_is_reparse",
        lambda info: info.st_ino == reparse_inode or original_is_reparse(info),
    )

    assert boundary._output_path_blocker(target) == "output_parent_reparse"
    with pytest.raises(OSError, match="output_parent_reparse"):
        boundary._write_output(target, "blocked")


def test_output_missing_parent_is_not_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = boundary._PYCACHE_ROOT / tmp_path.name / "readiness.json"
    assert not target.parent.exists()
    assert boundary._output_path_blocker(target) == "output_parent_unavailable"
    with pytest.raises(OSError, match="output_parent_unavailable"):
        boundary._write_output(target, "blocked")
    assert not target.parent.exists()


def test_output_parent_change_prevents_replace_and_cleans_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit_root = _configure_output_test_root(tmp_path, monkeypatch)
    target = audit_root / f"{tmp_path.name}-parent-change.json"
    target.write_text("old", encoding="utf-8")
    real_snapshot = boundary._output_parent_snapshot
    replace_called = False

    def changing_snapshot(candidate: Path):
        snapshot, blocker = real_snapshot(candidate)
        if list(target.parent.glob(f".{target.name}.tmp.*")):
            changed = list(snapshot)
            path, dev, ino, mode, attributes = changed[-1]
            changed[-1] = (path, dev, ino + 1, mode, attributes)
            return tuple(changed), blocker
        return snapshot, blocker

    def forbidden_replace(*args, **kwargs):
        nonlocal replace_called
        replace_called = True
        raise AssertionError("replace must not run after parent drift")

    monkeypatch.setattr(boundary, "_output_parent_snapshot", changing_snapshot)
    monkeypatch.setattr(boundary.os, "replace", forbidden_replace)
    try:
        with pytest.raises(OSError, match="output_parent_changed"):
            boundary._write_output(target, "new")
        assert replace_called is False
        assert target.read_text(encoding="utf-8") == "old"
        assert list(target.parent.glob(f".{target.name}.tmp.*")) == []
    finally:
        target.unlink(missing_ok=True)


def test_posix_output_parent_is_opened_relative_to_each_pinned_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path("C:/safe")
    nested = root / "nested"
    candidate = nested / "report.json"
    components = (root, nested)
    snapshot = (
        (str(root), 1, 10, stat.S_IFDIR, 0),
        (str(nested), 1, 11, stat.S_IFDIR, 0),
    )
    calls: list[tuple[object, int | None]] = []
    closed: list[int] = []
    stats = {
        40: types.SimpleNamespace(
            st_dev=1, st_ino=10, st_mode=stat.S_IFDIR, st_file_attributes=0
        ),
        41: types.SimpleNamespace(
            st_dev=1, st_ino=11, st_mode=stat.S_IFDIR, st_file_attributes=0
        ),
    }

    def fake_open(path, flags, mode=0o777, *, dir_fd=None):
        calls.append((path, dir_fd))
        return 40 + len(calls) - 1

    monkeypatch.setattr(boundary, "_output_parent_components", lambda path: components)
    monkeypatch.setattr(boundary.os, "name", "posix")
    monkeypatch.setattr(boundary.os, "O_DIRECTORY", 0x10000, raising=False)
    monkeypatch.setattr(boundary.os, "O_NOFOLLOW", 0x20000, raising=False)
    monkeypatch.setattr(boundary.os, "open", fake_open)
    monkeypatch.setattr(boundary.os, "supports_dir_fd", {fake_open})
    monkeypatch.setattr(boundary.os, "fstat", lambda descriptor: stats[descriptor])
    monkeypatch.setattr(boundary.os, "close", closed.append)

    handles, parent_fd = boundary._open_output_parent_handles(candidate, snapshot)
    boundary._close_output_parent_handles(handles)

    assert handles == [40, 41]
    assert parent_fd == 41
    assert calls == [(root, None), (nested.name, 40)]
    assert closed == [41, 40]


def test_posix_output_parent_identity_mismatch_closes_all_descriptors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path("C:/safe")
    nested = root / "nested"
    components = (root, nested)
    snapshot = (
        (str(root), 1, 10, stat.S_IFDIR, 0),
        (str(nested), 1, 11, stat.S_IFDIR, 0),
    )
    descriptors = iter((50, 51))
    closed: list[int] = []

    def fake_open(path, flags, mode=0o777, *, dir_fd=None):
        return next(descriptors)

    def fake_fstat(descriptor: int):
        inode = 10 if descriptor == 50 else 999
        return types.SimpleNamespace(
            st_dev=1, st_ino=inode, st_mode=stat.S_IFDIR, st_file_attributes=0
        )

    monkeypatch.setattr(boundary, "_output_parent_components", lambda path: components)
    monkeypatch.setattr(boundary.os, "name", "posix")
    monkeypatch.setattr(boundary.os, "O_DIRECTORY", 0x10000, raising=False)
    monkeypatch.setattr(boundary.os, "O_NOFOLLOW", 0x20000, raising=False)
    monkeypatch.setattr(boundary.os, "open", fake_open)
    monkeypatch.setattr(boundary.os, "supports_dir_fd", {fake_open})
    monkeypatch.setattr(boundary.os, "fstat", fake_fstat)
    monkeypatch.setattr(boundary.os, "close", closed.append)

    with pytest.raises(OSError, match="output_parent_changed"):
        boundary._open_output_parent_handles(nested / "report.json", snapshot)

    assert closed == [51, 50]


def test_writer_atomically_replaces_allowed_output_and_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit_root = _configure_output_test_root(tmp_path, monkeypatch)
    target = audit_root / f"{tmp_path.name}-atomic.json"
    target.write_text("old", encoding="utf-8")
    pattern = f".{target.name}.tmp.*"
    try:
        boundary._write_output(target, "new")
        assert target.read_text(encoding="utf-8") == "new"
        assert list(target.parent.glob(pattern)) == []
    finally:
        target.unlink(missing_ok=True)


@pytest.mark.parametrize("constant", [b"NaN", b"Infinity", b"-Infinity"])
def test_json_object_rejects_nonfinite_constants(constant: bytes) -> None:
    with pytest.raises(ValueError, match="non-finite JSON constant"):
        boundary._json_object(b'{"acceptance":' + constant + b"}")


@pytest.mark.parametrize("constant", [b"1e999", b"-1e999"])
def test_json_object_rejects_float_overflow(constant: bytes) -> None:
    with pytest.raises(ValueError, match="non-finite JSON float"):
        boundary._json_object(
            b'{"nested":{"acceptance":' + constant + b"}}"
        )


def test_main_rejects_nonfinite_internal_report_before_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    audit_root = _configure_output_test_root(tmp_path, monkeypatch)
    target = audit_root / f"{tmp_path.name}-nonfinite.json"
    target.write_text("old", encoding="utf-8")
    monkeypatch.setattr(
        boundary,
        "build_report_from_paths",
        lambda **kwargs: {"ok": True, "value": float("nan")},
    )
    try:
        assert boundary.main(["--output", str(target), "--json"]) == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "release_boundary_output_encoding_blocked" in captured.err
        assert target.read_text(encoding="utf-8") == "old"
    finally:
        target.unlink(missing_ok=True)


def test_live_evaluator_exception_is_a_named_blocker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(checked_at_utc):
        raise RuntimeError("evaluator exploded")

    monkeypatch.setattr(boundary, "_run_live_release_gate", _raise)
    report = build_report(
        phase_synthesis_refresh=_phase_synthesis_refresh(),
        release_gate_recheck=_release_gate_recheck(),
        torch_decision_pack=_write_torch_pack(tmp_path / "torch.yaml"),
        docker_decision_pack=_write_docker_pack(tmp_path / "docker.yaml"),
        checked_at_utc=FIXED_NOW,
    )

    assert report["ok"] is False
    assert "live_release_gate_evaluator_error" in report[
        "release_boundary_blockers"
    ]
    assert report["source_live_release_gate"]["ok"] is False
    assert strict_exit_code(report) == 2


@pytest.mark.parametrize(
    ("source", "value", "expected"),
    [
        (
            "phase",
            None,
            "phase_synthesis_generated_at_utc_missing",
        ),
        (
            "phase",
            "not-a-time",
            "phase_synthesis_generated_at_utc_malformed",
        ),
        (
            "phase",
            "2026-06-01T02:40:00",
            "phase_synthesis_generated_at_utc_naive",
        ),
        (
            "phase",
            "2999-01-01T00:00:00Z",
            "phase_synthesis_generated_at_utc_in_future",
        ),
        (
            "gate",
            None,
            "release_gate_recheck_checked_at_utc_missing",
        ),
        (
            "gate",
            "not-a-time",
            "release_gate_recheck_checked_at_utc_malformed",
        ),
        (
            "gate",
            "2026-06-01T02:55:00",
            "release_gate_recheck_checked_at_utc_naive",
        ),
        (
            "gate",
            "2999-01-01T00:00:00Z",
            "release_gate_recheck_checked_at_utc_in_future",
        ),
    ],
)
def test_lineage_timestamps_fail_closed_without_an_age_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
    value: str | None,
    expected: str,
) -> None:
    _pass_live_gate(monkeypatch)
    phase = _phase_synthesis_refresh()
    gate = _release_gate_recheck()
    if source == "phase":
        phase["generated_at_utc"] = value
    else:
        gate["checked_at_utc"] = value

    report = build_report(
        phase_synthesis_refresh=phase,
        release_gate_recheck=gate,
        torch_decision_pack=_write_torch_pack(tmp_path / "torch.yaml"),
        docker_decision_pack=_write_docker_pack(tmp_path / "docker.yaml"),
        checked_at_utc=FIXED_NOW,
    )

    assert report["ok"] is False
    assert expected in report["release_boundary_blockers"]
    assert report["release_boundary"] == FALSE_RELEASE_BOUNDARY


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "live_release_gate_checked_at_utc_missing"),
        ("not-a-time", "live_release_gate_checked_at_utc_malformed"),
        ("2026-06-01T02:55:00", "live_release_gate_checked_at_utc_naive"),
        ("2999-01-01T00:00:00Z", "live_release_gate_checked_at_utc_in_future"),
    ],
)
def test_invalid_live_timestamp_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    value: str | None,
    expected: str,
) -> None:
    live_report = _release_gate_recheck()
    if value is None:
        live_report.pop("checked_at_utc")
    else:
        live_report["checked_at_utc"] = value
    monkeypatch.setattr(
        boundary,
        "_run_live_release_gate",
        lambda checked_at_utc: live_report,
    )
    report = build_report(
        phase_synthesis_refresh=_phase_synthesis_refresh(),
        release_gate_recheck=_release_gate_recheck(),
        torch_decision_pack=_write_torch_pack(tmp_path / "torch.yaml"),
        docker_decision_pack=_write_docker_pack(tmp_path / "docker.yaml"),
        checked_at_utc=FIXED_NOW,
    )

    assert report["ok"] is False
    assert expected in report["release_boundary_blockers"]


def test_naive_or_future_checked_at_blocks_and_uses_safe_live_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dt.datetime] = []
    monkeypatch.setattr(boundary, "_utc_now", lambda: FIXED_NOW)
    monkeypatch.setattr(
        boundary,
        "_run_live_release_gate",
        lambda checked_at_utc: (
            seen.append(checked_at_utc) or _release_gate_recheck()
        ),
    )
    common = {
        "phase_synthesis_refresh": _phase_synthesis_refresh(),
        "release_gate_recheck": _release_gate_recheck(),
        "torch_decision_pack": _write_torch_pack(tmp_path / "torch.yaml"),
        "docker_decision_pack": _write_docker_pack(tmp_path / "docker.yaml"),
    }

    naive = build_report(
        **common,
        checked_at_utc=dt.datetime(2026, 6, 1, 3, 0),
    )
    future = build_report(
        **common,
        checked_at_utc=FIXED_NOW + dt.timedelta(seconds=1),
    )

    assert "checked_at_utc_naive" in naive["release_boundary_blockers"]
    assert "checked_at_utc_in_future" in future["release_boundary_blockers"]
    assert seen == [FIXED_NOW, FIXED_NOW]


# --- Head/soak binding ---


@pytest.mark.parametrize(
    ("head", "soak", "expected"),
    [
        (None, {"commit": CANONICAL_SOAK_COMMIT}, "git_head_unavailable"),
        ("abc123", {"commit": CANONICAL_SOAK_COMMIT}, "git_head_not_full_hex"),
        ("A" * 40, {"commit": CANONICAL_SOAK_COMMIT}, "git_head_not_full_hex"),
        (TEST_CARRIER_HEAD, None, "soak_evidence_unreadable"),
        (TEST_CARRIER_HEAD, [], "soak_evidence_unreadable"),
        (TEST_CARRIER_HEAD, {"commit": "A" * 40}, "soak_commit_missing_or_malformed"),
    ],
)
def test_head_soak_binding_rejects_malformed_inputs_before_git(
    head: str | None,
    soak: object,
    expected: str,
) -> None:
    raw = None if soak is None else json.dumps(soak).encode("utf-8")
    binding, blockers = boundary._head_soak_binding(
        {"head": head}, {"raw": raw}
    )

    assert binding["git_head"] == head
    assert expected in blockers


def _install_subject_carrier_git(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ancestry_returncode: int = 0,
    delta: bytes | None = None,
) -> None:
    carrier = boundary.SOAK_EVIDENCE_CARRIER_PATH.encode("utf-8") + b"\0"
    delta = carrier if delta is None else delta

    def fake_git(*args: str) -> bytes:
        if args[:2] == ("rev-parse", "--verify"):
            return (
                TEST_SUBJECT_TREE
                if args[2] == f"{CANONICAL_SOAK_COMMIT}^{{tree}}"
                else TEST_CARRIER_TREE
            ).encode("ascii") + b"\n"
        if args and args[0] == "diff-tree":
            return delta
        raise AssertionError(f"unexpected git command: {args}")

    def fake_git_result(*args: str) -> subprocess.CompletedProcess[bytes]:
        assert args == (
            "merge-base", "--is-ancestor",
            CANONICAL_SOAK_COMMIT, TEST_CARRIER_HEAD,
        )
        return subprocess.CompletedProcess(
            ["git", *args], ancestry_returncode, b"", b""
        )

    monkeypatch.setattr(boundary, "_git", fake_git)
    monkeypatch.setattr(boundary, "_git_result", fake_git_result)


def _subject_binding_state() -> dict[str, object]:
    return {
        "head": TEST_CARRIER_HEAD,
        "index": {
            boundary.SOAK_EVIDENCE_CARRIER_PATH: (
                "100644", "c" * 40, "0", "H"
            )
        },
    }


def test_head_soak_binding_accepts_only_ancestor_carrier_delta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_subject_carrier_git(monkeypatch)
    raw = json.dumps({"commit": CANONICAL_SOAK_COMMIT}).encode()

    binding, blockers = boundary._head_soak_binding(
        _subject_binding_state(), {"raw": raw}
    )

    assert blockers == []
    assert binding == {
        "schema_version": boundary.HEAD_SOAK_BINDING_SCHEMA_VERSION,
        "git_head": TEST_CARRIER_HEAD,
        "soak_commit": CANONICAL_SOAK_COMMIT,
        "soak_subject_tree": TEST_SUBJECT_TREE,
        "head_tree": TEST_CARRIER_TREE,
        "subject_is_ancestor": True,
        "carrier_only_delta": True,
        "carrier_delta_paths": [boundary.SOAK_EVIDENCE_CARRIER_PATH],
        "soak_evidence_path": boundary.SOAK_EVIDENCE_CARRIER_PATH,
        "carrier_blob": "c" * 40,
    }


@pytest.mark.parametrize(
    ("ancestry_returncode", "delta", "expected"),
    [
        (1, None, "soak_subject_commit_not_ancestor_of_head"),
        (2, None, "soak_subject_git_binding_unavailable"),
        (0, b"tools/runtime.py\0", "soak_subject_noncarrier_tree_delta"),
        (
            0,
            boundary.SOAK_EVIDENCE_CARRIER_PATH.encode() + b"\0tools/runtime.py\0",
            "soak_subject_noncarrier_tree_delta",
        ),
        (0, b"not-nul-terminated", "soak_subject_tree_delta_malformed"),
        (0, b"\xff\0", "soak_subject_git_binding_unavailable"),
    ],
)
def test_head_soak_binding_rejects_unproved_or_noncarrier_relation(
    monkeypatch: pytest.MonkeyPatch,
    ancestry_returncode: int,
    delta: bytes | None,
    expected: str,
) -> None:
    _install_subject_carrier_git(
        monkeypatch, ancestry_returncode=ancestry_returncode, delta=delta
    )
    raw = json.dumps({"commit": CANONICAL_SOAK_COMMIT}).encode()

    _, blockers = boundary._head_soak_binding(
        _subject_binding_state(), {"raw": raw}
    )

    assert expected in blockers


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("canonical", "phase_synthesis_refresh_malformed_json"),
        ("soak", "soak_evidence_unreadable"),
    ],
)
def test_deep_json_recursion_is_a_named_hold(
    monkeypatch: pytest.MonkeyPatch, source: str, expected: str
) -> None:
    depth = sys.getrecursionlimit() + 50
    raw = b'{"x":' * depth + b"0" + b"}" * depth

    def recursion(*args, **kwargs):
        raise RecursionError

    monkeypatch.setattr(boundary.json, "loads", recursion)
    if source == "canonical":
        _, blockers = boundary._canonical_json(
            {"inputs": {"phase_synthesis_refresh": {"raw": raw}}},
            "phase_synthesis_refresh",
        )
    else:
        _, blockers = boundary._head_soak_binding(
            {"head": CANONICAL_SOAK_COMMIT}, {"raw": raw}
        )
    assert expected in blockers


# --- Torch operator_signoff.scope_updates fail-closed inspection ---


_SCOPE_SIGNED = """  scope_updates:
    - recorded_utc: 2026-08-26T07:00:00Z
      recorded_by: "codex-lead-1"
      signed_by: "operator:jani:2026-08-26T07:00:00Z"
      lock_evidence_contract:
        operator_signature_required: true
"""

_SCOPE_MISSING_SIGNER = """  scope_updates:
    - recorded_utc: 2026-08-26T07:00:00Z
      recorded_by: "codex-lead-1"
      lock_evidence_contract:
        operator_signature_required: true
"""

_SCOPE_WRONG_SIGNER = """  scope_updates:
    - recorded_utc: 2026-08-26T07:00:00Z
      signed_by: "agent:codex-lead-1:2026-08-26T07:00:00Z"
      lock_evidence_contract:
        operator_signature_required: true
"""

_SCOPE_TRUTHY_FLAG = """  scope_updates:
    - recorded_utc: 2026-08-26T07:00:00Z
      signed_by: "operator:jani:2026-08-26T07:00:00Z"
      lock_evidence_contract:
        operator_signature_required: "yes"
"""

_SCOPE_FLAG_MISSING = """  scope_updates:
    - recorded_utc: 2026-08-26T07:00:00Z
      lock_evidence_contract:
        note: "contract present but required flag absent"
"""

_SCOPE_HISTORICAL = """  scope_updates:
    - recorded_utc: 2026-05-27T08:14:26Z
      recorded_by: "codex-lead-1"
      summary: "historical dependency-floor authorization, no contract"
"""

_SCOPE_NOT_A_LIST = """  scope_updates: "not-a-list"
"""

_SCOPE_NULL_CONTRACT = """  scope_updates:
    - recorded_utc: 2026-08-26T07:00:00Z
      lock_evidence_contract: null
"""


def test_exact_signed_required_scope_update_passes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _build_pass_report(
        tmp_path,
        monkeypatch,
        torch_scope_yaml=_SCOPE_SIGNED,
    )
    assert report["ok"] is True
    assert report["release_boundary_blockers"] == []


def test_required_scope_update_without_direct_signer_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _build_pass_report(
        tmp_path,
        monkeypatch,
        torch_scope_yaml=_SCOPE_MISSING_SIGNER,
    )
    assert report["ok"] is False
    assert "torch_scope_update_0_missing_direct_signed_by" in report[
        "release_boundary_blockers"
    ]


def test_required_scope_update_with_wrong_identity_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _build_pass_report(
        tmp_path,
        monkeypatch,
        torch_scope_yaml=_SCOPE_WRONG_SIGNER,
    )
    assert report["ok"] is False
    assert "torch_scope_update_0_signer_identity_mismatch" in report[
        "release_boundary_blockers"
    ]


def test_truthy_non_bool_required_flag_blocks_as_malformed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _build_pass_report(
        tmp_path,
        monkeypatch,
        torch_scope_yaml=_SCOPE_TRUTHY_FLAG,
    )
    assert report["ok"] is False
    assert "torch_scope_update_0_required_flag_malformed" in report[
        "release_boundary_blockers"
    ]


def test_contract_without_required_flag_blocks_as_ambiguous(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _build_pass_report(
        tmp_path,
        monkeypatch,
        torch_scope_yaml=_SCOPE_FLAG_MISSING,
    )
    assert report["ok"] is False
    assert "torch_scope_update_0_required_flag_missing" in report[
        "release_boundary_blockers"
    ]


def test_historical_update_without_contract_does_not_block(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _build_pass_report(
        tmp_path,
        monkeypatch,
        torch_scope_yaml=_SCOPE_HISTORICAL,
    )
    assert report["ok"] is True
    assert report["release_boundary_blockers"] == []


def test_scope_updates_not_a_list_blocks_as_malformed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _build_pass_report(
        tmp_path,
        monkeypatch,
        torch_scope_yaml=_SCOPE_NOT_A_LIST,
    )
    assert report["ok"] is False
    assert "torch_scope_updates_malformed" in report["release_boundary_blockers"]


def test_explicit_null_scope_contract_is_malformed_not_historical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _build_pass_report(
        tmp_path, monkeypatch, torch_scope_yaml=_SCOPE_NULL_CONTRACT
    )
    assert "torch_scope_update_0_contract_malformed" in report[
        "release_boundary_blockers"
    ]


@pytest.mark.parametrize("duplicate", ["top_level", "signoff", "critical_contract"])
def test_duplicate_decision_pack_yaml_keys_fail_closed(
    tmp_path: Path, duplicate: str
) -> None:
    path = _write_torch_pack(
        tmp_path / "torch-duplicate.yaml",
        scope_updates_yaml=_SCOPE_SIGNED if duplicate == "critical_contract" else "",
    )
    text = path.read_text(encoding="utf-8")
    if duplicate == "top_level":
        text = text.replace(
            "decision_id: torch-cuda-vs-cpu",
            "decision_id: torch-cuda-vs-cpu\ndecision_id: duplicate",
        )
    elif duplicate == "signoff":
        text = text.replace(
            '  chosen_option: "A2_cu126"',
            '  chosen_option: "A2_cu126"\n  chosen_option: "duplicate"',
        )
    else:
        text = text.replace(
            "        operator_signature_required: true",
            "        operator_signature_required: true\n"
            "        operator_signature_required: false",
        )
    path.write_text(text, encoding="utf-8")
    summary = boundary._torch_pack_summary(_pack_entry(path))
    assert "operator_decision_pack_missing_or_invalid" in summary["blockers"]


@pytest.mark.parametrize(
    "mutation",
    [
        ("  - id: A2_cu126", "  - id: 1"),
        ('  chosen_option: "A2_cu126"', "  chosen_option: 1"),
    ],
)
def test_decision_pack_option_identity_never_coerces_yaml_scalars(
    tmp_path: Path,
    mutation: tuple[str, str],
) -> None:
    path = _write_torch_pack(tmp_path / "torch-option-type.yaml")
    text = path.read_text(encoding="utf-8").replace(*mutation)
    path.write_text(text, encoding="utf-8")

    summary = boundary._torch_pack_summary(_pack_entry(path))

    assert summary["signed"] is False
    assert "operator_decision_pack_missing_or_invalid" in summary["blockers"]


# --- Stage-A process boundary and captured-input forges ---


LIVE_CHILD_SENTINEL_BUNDLE = b"stage-a-s4-live-child-sentinel"


def _completed(
    report: dict[str, object],
    *,
    returncode: int | None = None,
    stdout: bytes | None = None,
    stderr: bytes = b"",
) -> subprocess.CompletedProcess[bytes]:
    if returncode is None:
        returncode = (
            1
            if report.get("ok") is not True
            else 0
            if report.get("release_gate_decision") == "pass"
            and not report.get("blockers")
            else 2
        )
    payload = stdout if stdout is not None else json.dumps(report).encode() + b"\n"
    return subprocess.CompletedProcess(["child"], returncode, payload, stderr)


def _git_snapshot(
    inputs: dict[str, dict[str, object]] | None = None, **changes: object
) -> dict[str, object]:
    inputs = inputs or {"soak_evidence": _input_entry()}
    index = {}
    for name, entry in inputs.items():
        raw = entry["raw"]
        assert isinstance(raw, bytes)
        relative = boundary.CANONICAL_INPUTS[name].relative_to(ROOT).as_posix()
        index[relative] = (
            "100644", boundary._git_blob_oid(raw), "0", "H"
        )
    snapshot: dict[str, object] = {
        "toplevel": str(ROOT),
        "head": TEST_CARRIER_HEAD,
        "clean": True,
        "tracked": True,
        "tracked_flags_normal": True,
        "index": index,
        "object_format": "sha1",
        "error": None,
    }
    snapshot.update(changes)
    return snapshot


def _input_entry(*, error: str | None = None) -> dict[str, object]:
    return {
        "path": str(CANONICAL_SOAK_EVIDENCE),
        "raw": b"payload",
        "digest": "digest",
        "identity": (1, 2, stat.S_IFREG, 7, 3),
        "error": error,
    }


def _canonical_entries(tmp_path: Path) -> dict[str, dict[str, object]]:
    entries = {name: _input_entry() for name in boundary.CANONICAL_INPUTS}
    entries["phase_synthesis_refresh"]["raw"] = json.dumps(
        _phase_synthesis_refresh()
    ).encode()
    entries["release_gate_recheck"]["raw"] = json.dumps(
        _release_gate_recheck()
    ).encode()
    entries["torch_decision_pack"] = _pack_entry(
        _write_torch_pack(tmp_path / "torch.yaml")
    )
    entries["docker_decision_pack"] = _pack_entry(
        _write_docker_pack(tmp_path / "docker.yaml")
    )
    entries["soak_evidence"]["raw"] = json.dumps(
        {"commit": CANONICAL_SOAK_COMMIT}
    ).encode()
    return entries


def test_canonical_wrapper_captures_the_exact_sixteen_input_closure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = {
        "boundary_script",
        "phase_synthesis_refresh", "release_gate_recheck", "torch_decision_pack",
        "docker_decision_pack", "release_readiness", "soak_evidence",
        "tools_package", "live_gate_script", "check_release_gate",
        "verify_release_soak_evidence", "collect_soak_evidence",
        "release_security_attestation", "run_release_ci_status_evidence",
        "run_release_docker_policy_evidence", "operator_decision_pack",
    }
    assert set(boundary.CANONICAL_INPUTS) == expected
    assert all(path.is_absolute() and path.is_relative_to(ROOT)
               for path in boundary.CANONICAL_INPUTS.values())

    entries = _canonical_entries(tmp_path)
    window = {"git_before": _git_snapshot(entries), "inputs": entries}
    validator = __import__("tools.operator_decision_pack", fromlist=["*"])
    monkeypatch.setattr(boundary, "_utc_now", lambda: FIXED_NOW)
    monkeypatch.setattr(boundary, "_open_window", lambda: window)
    monkeypatch.setattr(
        boundary, "_close_window", boundary._pre_window_blockers
    )
    monkeypatch.setattr(boundary, "_TOOLS_PACKAGE_PRELOADED", False)
    monkeypatch.setattr(boundary, "_DECISION_PACK_PRELOADED", False)
    monkeypatch.setattr(boundary, "_DECISION_PACK_MODULE", None)
    monkeypatch.setattr(boundary, "_PARENT_ISOLATED", True)
    monkeypatch.setattr(boundary, "_PARENT_NO_SITE", True)
    monkeypatch.setattr(boundary, "_YAML_PRELOADED", False)
    monkeypatch.setattr(boundary, "_trusted_pyyaml_current_blocker", lambda: None)
    monkeypatch.setattr(boundary, "_decision_pack_module", lambda: validator)
    monkeypatch.setattr(
        boundary, "_run_live_release_gate",
        lambda when: _release_gate_recheck(checked_at_utc=boundary._format_utc(when)),
    )
    _install_subject_carrier_git(monkeypatch)

    report = boundary.build_report(checked_at_utc=FIXED_NOW)
    assert report["ok"] is True
    assert report["head_soak_binding"]["git_head"] == TEST_CARRIER_HEAD


def test_direct_file_prunes_script_dir_and_pythonpath_before_imports(
    tmp_path: Path,
) -> None:
    fake_root = tmp_path / "root"
    fake_tools = fake_root / "tools"
    fake_tools.mkdir(parents=True)
    script = fake_tools / "run_release_boundary_readiness.py"
    script.write_bytes(Path(boundary.__file__).read_bytes())
    (fake_tools / "argparse.py").write_text(
        "raise RuntimeError('argparse shadow loaded')\n", encoding="utf-8"
    )
    json_source = fake_tools / "json.py"
    json_source.write_text(
        "raise RuntimeError('sourceless json shadow loaded')\n", encoding="utf-8"
    )
    py_compile.compile(
        str(json_source), cfile=str(fake_tools / "json.pyc"), doraise=True
    )
    json_source.unlink()
    ambient = tmp_path / "ambient-pythonpath"
    ambient.mkdir()
    (ambient / "hashlib.py").write_text(
        "raise RuntimeError('PYTHONPATH shadow loaded')\n", encoding="utf-8"
    )
    environment = boundary._sanitized_environment()
    environment["PYTHONPATH"] = str(ambient)
    completed = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(script), "--help"], cwd=tmp_path,
        env=environment, capture_output=True,
        timeout=30, check=False,
    )
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    assert b"release-boundary readiness" in completed.stdout


def test_direct_file_without_no_site_aborts_before_output(tmp_path: Path) -> None:
    output = tmp_path / "must-not-exist.json"
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(boundary.__file__),
            "--output",
            str(output),
        ],
        cwd=tmp_path,
        env=boundary._sanitized_environment(),
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == boundary.STRICT_BLOCKED_EXIT_CODE
    assert completed.stdout == b""
    assert completed.stderr.splitlines() == [b"parent_python_site_enabled"]
    assert not output.exists()


def test_parent_bootstrap_resets_meta_path_to_standard_finders() -> None:
    probe = (
        "import runpy,sys; "
        "sentinel=type('Sentinel',(),{'find_spec':lambda *a:None})(); "
        "sys.meta_path.insert(0,sentinel); "
        "sys.path_hooks.insert(0,lambda path:(_ for _ in ()).throw(ImportError())); "
        "sys.path_importer_cache['attacker-cache']=object(); "
        f"state=runpy.run_path({str(boundary.__file__)!r},run_name='parent_probe'); "
        "names=[getattr(f,'__name__',type(f).__name__) for f in sys.meta_path]; "
        "assert names == ['BuiltinImporter','FrozenImporter','PathFinder']; "
        "assert not any(getattr(h,'__name__','') == '<lambda>' for h in sys.path_hooks); "
        "assert 'attacker-cache' not in sys.path_importer_cache; "
        "assert state['_PARENT_ISOLATED']; "
        "assert state['_PARENT_NO_SITE']"
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", probe], cwd=ROOT,
        env=boundary._sanitized_environment(), capture_output=True,
        timeout=30, check=False,
    )
    assert completed.returncode == 0, (completed.stdout, completed.stderr)


def test_pyyaml_authority_is_the_reviewed_official_602_source_manifest() -> None:
    assert boundary.PYYAML_VERSION == "6.0.2"
    assert boundary.PYYAML_SDIST_SHA256 == (
        "d584d9ec91ad65861cc08d42e834324ef890a082e591037abe114850ff7bbc3e"
    )
    assert boundary.PYYAML_SOURCE_MANIFEST == EXPECTED_PYYAML_602_MANIFEST
    assert len({path for path, _, _ in boundary.PYYAML_SOURCE_MANIFEST}) == 17


def _require_trusted_pyyaml_602() -> None:
    if isinstance(boundary._TRUSTED_PYYAML, dict):
        return
    assert boundary._TRUSTED_PYYAML_BLOCKER in {
        "trusted_pyyaml_unavailable",
        "trusted_pyyaml_version_unpinned",
    }
    pytest.skip(boundary._TRUSTED_PYYAML_BLOCKER)


def test_pyyaml_unpinned_version_blocks_before_any_source_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    site_root = runtime_root / "site-packages"
    (site_root / "yaml").mkdir(parents=True)

    class UnpinnedDistribution:
        metadata = {"Name": "PyYAML"}
        version = "6.0.3"

        @property
        def files(self):
            pytest.fail("RECORD was read after the unpinned version was known")

    monkeypatch.setattr(
        boundary.importlib.metadata.Distribution,
        "discover",
        lambda **kwargs: [UnpinnedDistribution()],
    )
    monkeypatch.setattr(
        boundary,
        "_runtime_file_bytes",
        lambda *args, **kwargs: pytest.fail("source read crossed version fence"),
    )

    match, artifact, blocker = boundary._pyyaml_distribution_at(
        site_root, runtime_root=runtime_root
    )

    assert match is None
    assert artifact is True
    assert blocker == "trusted_pyyaml_version_unpinned"


def test_pyyaml_fixed_sources_are_captured_once_with_record_only_corrobating(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _require_trusted_pyyaml_602()
    runtime_root = tmp_path / "runtime"
    site_root = runtime_root / "site-packages"
    yaml_dir = site_root / "yaml"
    yaml_dir.mkdir(parents=True)
    for relative, raw in boundary._TRUSTED_PYYAML["source_items"]:
        (site_root / relative).write_bytes(raw)

    class CorroboratingDistribution:
        metadata = {"Name": "PyYAML"}
        version = "6.0.2"
        files = [
            *(path for path, _, _ in EXPECTED_PYYAML_602_MANIFEST),
            "PyYAML-6.0.2.dist-info/METADATA",
            "PyYAML-6.0.2.dist-info/RECORD",
        ]

    monkeypatch.setattr(
        boundary.importlib.metadata.Distribution,
        "discover",
        lambda **kwargs: [CorroboratingDistribution()],
    )
    original_read = boundary._runtime_file_bytes
    reads: list[str] = []

    def count_read(path: Path, anchor: Path) -> bytes:
        reads.append(path.relative_to(site_root).as_posix())
        return original_read(path, anchor)

    monkeypatch.setattr(boundary, "_runtime_file_bytes", count_read)

    match, artifact, blocker = boundary._pyyaml_distribution_at(
        site_root, runtime_root=runtime_root
    )

    assert blocker is None and artifact is True and match is not None
    assert reads == [path for path, _, _ in EXPECTED_PYYAML_602_MANIFEST]
    assert tuple(path for path, _ in match["source_items"]) == tuple(reads)


def test_isolated_no_site_parent_authenticates_or_named_holds_pyyaml() -> None:
    probe = (
        "import json,os,runpy,sys; "
        f"state=runpy.run_path({str(boundary.__file__)!r},run_name='pyyaml_probe'); "
        "blocker=state['_TRUSTED_PYYAML_BLOCKER']; "
        "result={'isolated':bool(sys.flags.isolated),"
        "'no_site':bool(sys.flags.no_site),"
        "'site_paths':[value for value in sys.path "
        "if 'site-packages' in str(value).replace('\\\\','/').casefold()],"
        "'blocker':blocker}; "
        "module=None if blocker else state['_load_trusted_pyyaml'](); "
        "result.update({} if module is None else {"
        "'version':module.__version__,"
        "'origin':module.__file__,"
        "'loaded':sorted(name for name in sys.modules "
        "if name=='yaml' or name.startswith('yaml.')),'libyaml':"
        "bool(module.__with_libyaml__)}); print(json.dumps(result))"
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", probe],
        cwd=ROOT,
        env=boundary._sanitized_environment(),
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    assert completed.stderr == b""
    result = json.loads(completed.stdout)
    assert result["isolated"] is True and result["no_site"] is True
    assert result["site_paths"] == []
    if result["blocker"] is not None:
        assert result["blocker"] in {
            "trusted_pyyaml_unavailable",
            "trusted_pyyaml_version_unpinned",
        }
        assert result == {
            "isolated": True,
            "no_site": True,
            "site_paths": [],
            "blocker": result["blocker"],
        }
    else:
        assert result["version"] == "6.0.2"
        assert result["origin"] == "<trusted-pyyaml:yaml/__init__.py>"
        assert result["loaded"] == sorted(boundary.PYYAML_EXECUTABLE_MODULES)
        assert result["libyaml"] is False


def test_decision_pack_loader_builds_the_canonical_tools_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _require_trusted_pyyaml_602()
    class RejectToolsFinder:
        @staticmethod
        def find_spec(fullname, path=None, target=None):
            if fullname == "tools" or fullname.startswith("tools."):
                raise AssertionError("meta finder intercepted canonical tools")
            return None

    monkeypatch.delitem(sys.modules, "tools.operator_decision_pack", raising=False)
    monkeypatch.delitem(sys.modules, "tools", raising=False)
    for name in tuple(sys.modules):
        if name == "yaml" or name.startswith("yaml."):
            monkeypatch.delitem(sys.modules, name)
    monkeypatch.setattr(sys, "meta_path", [RejectToolsFinder(), *sys.meta_path])
    monkeypatch.setattr(boundary, "_TOOLS_PACKAGE_PRELOADED", False)
    monkeypatch.setattr(boundary, "_DECISION_PACK_PRELOADED", False)
    monkeypatch.setattr(boundary, "_DECISION_PACK_MODULE", None)

    module = boundary._decision_pack_module()
    package = sys.modules["tools"]
    assert Path(package.__file__).resolve() == boundary.CANONICAL_INPUTS["tools_package"]
    assert Path(module.__file__).resolve() == boundary.CANONICAL_INPUTS[
        "operator_decision_pack"
    ]
    assert boundary._DECISION_PACK_MODULE is module


def test_trusted_pyyaml_loader_rejects_preloaded_yaml(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _require_trusted_pyyaml_602()
    sentinel = types.ModuleType("yaml")
    monkeypatch.setitem(sys.modules, "yaml", sentinel)

    with pytest.raises(ValueError, match="trusted pyyaml preloaded"):
        boundary._load_trusted_pyyaml()

    assert sys.modules["yaml"] is sentinel


def test_trusted_pyyaml_loader_rejects_orphan_preloaded_yaml_descendant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _require_trusted_pyyaml_602()
    sentinel = types.ModuleType("yaml.loader")
    monkeypatch.delitem(sys.modules, "yaml", raising=False)
    monkeypatch.setitem(sys.modules, "yaml.loader", sentinel)

    with pytest.raises(ValueError, match="trusted pyyaml preloaded"):
        boundary._load_trusted_pyyaml()

    assert sys.modules["yaml.loader"] is sentinel


def test_trusted_pyyaml_loader_cleans_modules_and_finder_after_execution_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _require_trusted_pyyaml_602()
    assert isinstance(boundary._TRUSTED_PYYAML, dict)
    for name in tuple(sys.modules):
        if name == "yaml" or name.startswith("yaml."):
            monkeypatch.delitem(sys.modules, name)
    previous_finder = boundary._TRUSTED_PYYAML_FINDER

    def fail_execution(self, module):
        raise RuntimeError("synthetic execution failure")

    monkeypatch.setattr(boundary._ExactYamlLoader, "exec_module", fail_execution)

    with pytest.raises(RuntimeError, match="synthetic execution failure"):
        boundary._load_trusted_pyyaml()

    assert not any(
        name == "yaml" or name.startswith("yaml.") for name in sys.modules
    )
    assert boundary._TRUSTED_PYYAML_FINDER is None
    assert previous_finder not in sys.meta_path


@pytest.mark.parametrize("mutation", ["missing", "extra", "changed"])
def test_trusted_pyyaml_in_memory_source_mutation_is_a_named_blocker(
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    _require_trusted_pyyaml_602()
    assert isinstance(boundary._TRUSTED_PYYAML, dict)
    malformed = dict(boundary._TRUSTED_PYYAML)
    sources = list(malformed["source_items"])
    if mutation == "missing":
        sources.pop()
        expected = "trusted_pyyaml_source_manifest_mismatch"
    elif mutation == "extra":
        sources.append(("yaml/unknown.py", b"pass\n"))
        expected = "trusted_pyyaml_source_manifest_mismatch"
    else:
        path, raw = sources[0]
        sources[0] = (path, raw + b"changed")
        expected = "trusted_pyyaml_source_changed"
    malformed["source_items"] = tuple(sources)
    monkeypatch.setattr(boundary, "_TRUSTED_PYYAML", malformed)

    assert boundary._trusted_pyyaml_current_blocker() == expected


def test_trusted_pyyaml_executes_captured_bytes_without_path_reopen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _require_trusted_pyyaml_602()
    assert isinstance(boundary._TRUSTED_PYYAML, dict)
    for name in tuple(sys.modules):
        if name == "yaml" or name.startswith("yaml."):
            monkeypatch.delitem(sys.modules, name)
    monkeypatch.setattr(
        boundary,
        "_runtime_file_bytes",
        lambda *args, **kwargs: pytest.fail("captured source path was reopened"),
    )

    module = boundary._load_trusted_pyyaml()

    assert module.__version__ == "6.0.2"
    assert module.__with_libyaml__ is False
    assert sorted(
        name for name in sys.modules if name == "yaml" or name.startswith("yaml.")
    ) == sorted(boundary.PYYAML_EXECUTABLE_MODULES)


def test_trusted_pyyaml_denies_native_and_unknown_modules_before_path_finder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _require_trusted_pyyaml_602()
    assert isinstance(boundary._TRUSTED_PYYAML, dict)
    for name in tuple(sys.modules):
        if name == "yaml" or name.startswith("yaml."):
            monkeypatch.delitem(sys.modules, name)

    def native_loader_called(*args, **kwargs):
        pytest.fail("native YAML loader was reached")

    monkeypatch.setattr(
        boundary.importlib.machinery.ExtensionFileLoader,
        "exec_module",
        native_loader_called,
    )
    module = boundary._load_trusted_pyyaml()

    assert module.__with_libyaml__ is False
    for name in ("yaml.cyaml", "yaml._yaml", "yaml.unknown"):
        with pytest.raises(ModuleNotFoundError, match="not in authenticated source set"):
            importlib.import_module(name)


@pytest.mark.parametrize(
    "dependency_blocker",
    ["trusted_pyyaml_unavailable", "trusted_pyyaml_distribution_ambiguous"],
)
def test_missing_or_ambiguous_pyyaml_blocks_without_live_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    dependency_blocker: str,
) -> None:
    entries = _canonical_entries(tmp_path)
    window = {"git_before": _git_snapshot(entries), "inputs": entries}
    monkeypatch.setattr(boundary, "_utc_now", lambda: FIXED_NOW)
    monkeypatch.setattr(boundary, "_open_window", lambda: window)
    monkeypatch.setattr(boundary, "_close_window", lambda opened: [])
    monkeypatch.setattr(boundary, "_yaml_shadow_blocker", lambda: None)
    monkeypatch.setattr(
        boundary, "_trusted_pyyaml_current_blocker", lambda: dependency_blocker
    )
    monkeypatch.setattr(boundary, "_pycache_prefix_blocker", lambda **kwargs: None)
    monkeypatch.setattr(boundary, "_TOOLS_PACKAGE_PRELOADED", False)
    monkeypatch.setattr(boundary, "_DECISION_PACK_PRELOADED", False)
    monkeypatch.setattr(boundary, "_DECISION_PACK_MODULE", None)
    monkeypatch.setattr(boundary, "_PARENT_ISOLATED", True)
    monkeypatch.setattr(boundary, "_PARENT_NO_SITE", True)
    monkeypatch.setattr(boundary, "_YAML_PRELOADED", False)
    monkeypatch.setattr(
        boundary,
        "_decision_pack_module",
        lambda: pytest.fail("dependency blocker did not fence the validator"),
    )
    monkeypatch.setattr(
        boundary,
        "_run_live_release_gate",
        lambda when: pytest.fail("dependency blocker did not fence the child"),
    )

    report = boundary.build_report(checked_at_utc=FIXED_NOW)

    assert report["ok"] is False
    assert dependency_blocker in report["release_boundary_blockers"]
    assert "live_release_gate_not_run_input_integrity" in report[
        "release_boundary_blockers"
    ]


@pytest.mark.parametrize(
    ("git_changes", "prefix_blocker", "shadow_path", "parent_isolated",
     "yaml_preloaded", "expected"),
    [
        ({"clean": False}, None, None, True, False,
         "git_worktree_not_clean_before"),
        ({}, "live_release_gate_pycache_prefix_preexists", None, True, False,
         "live_release_gate_pycache_prefix_preexists"),
        ({}, None, ROOT / "yaml.pyc", True, False,
         "third_party_yaml_shadow_present"),
        ({}, None, ROOT / "yaml", True, False, "third_party_yaml_shadow_present"),
        ({}, None, None, False, False, "parent_python_not_isolated"),
        ({}, None, None, True, True, "repository_dependency_preloaded"),
    ],
)
def test_pre_integrity_hold_skips_validator_import_and_live_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    git_changes: dict[str, object], prefix_blocker: str | None,
    shadow_path: Path | None, parent_isolated: bool, yaml_preloaded: bool,
    expected: str,
) -> None:
    entries = _canonical_entries(tmp_path)
    window = {"git_before": _git_snapshot(entries, **git_changes), "inputs": entries}
    monkeypatch.setattr(boundary, "_utc_now", lambda: FIXED_NOW)
    monkeypatch.setattr(boundary, "_open_window", lambda: window)
    monkeypatch.setattr(
        boundary, "_close_window", boundary._pre_window_blockers
    )
    monkeypatch.setattr(boundary, "_TOOLS_PACKAGE_PRELOADED", False)
    monkeypatch.setattr(boundary, "_DECISION_PACK_PRELOADED", False)
    monkeypatch.setattr(boundary, "_DECISION_PACK_MODULE", None)
    monkeypatch.setattr(boundary, "_PARENT_ISOLATED", parent_isolated)
    monkeypatch.setattr(boundary, "_PARENT_NO_SITE", True)
    monkeypatch.setattr(boundary, "_YAML_PRELOADED", yaml_preloaded)
    monkeypatch.setattr(boundary, "_trusted_pyyaml_current_blocker", lambda: None)
    if shadow_path is not None:
        assert shadow_path in boundary._YAML_SHADOW_PATHS
        monkeypatch.setattr(
            boundary, "_yaml_shadow_blocker",
            lambda: "third_party_yaml_shadow_present",
        )
    else:
        monkeypatch.setattr(boundary, "_yaml_shadow_blocker", lambda: None)
    monkeypatch.setattr(
        boundary, "_pycache_prefix_blocker", lambda **kwargs: prefix_blocker
    )
    monkeypatch.setattr(
        boundary, "_decision_pack_module",
        lambda: pytest.fail("validator import crossed the pre-integrity fence"),
    )
    monkeypatch.setattr(
        boundary, "_run_live_release_gate",
        lambda when: pytest.fail("live child crossed the pre-integrity fence"),
    )

    report = boundary.build_report(checked_at_utc=FIXED_NOW)
    blockers = report["release_boundary_blockers"]
    assert expected in blockers
    assert "live_release_gate_not_run_input_integrity" in blockers
    assert "torch_operator_decision_pack_not_evaluated_input_integrity" in blockers
    assert "docker_operator_decision_pack_not_evaluated_input_integrity" in blockers


def test_live_child_uses_exact_protocol_environment_cwd_and_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {}
    prefix_checks: list[bool] = []
    child_env = {"PATH": "trusted", "PYTHONPYCACHEPREFIX": "controlled"}

    def run(command, **kwargs):
        calls.update(command=command, **kwargs)
        return _completed(_release_gate_recheck(checked_at_utc="2026-06-01T03:00:00Z"))

    monkeypatch.setattr(
        boundary, "_pycache_prefix_blocker",
        lambda *, before_spawn: prefix_checks.append(before_spawn) or None,
    )
    monkeypatch.setattr(boundary, "_trusted_pyyaml_current_blocker", lambda: None)
    monkeypatch.setattr(
        boundary, "_build_live_child_bundle", lambda: LIVE_CHILD_SENTINEL_BUNDLE
    )
    monkeypatch.setattr(boundary, "_child_environment", lambda: child_env)
    monkeypatch.setattr(boundary.subprocess, "run", run)

    result = boundary._run_live_release_gate(FIXED_NOW)
    assert calls["command"] == [
        sys.executable, "-B", "-X", f"pycache_prefix={boundary._PYCACHE_PREFIX}",
        "-I", "-S", "-c", boundary._LIVE_CHILD_BOOTSTRAP, "--release-readiness",
        str(boundary.CANONICAL_RELEASE_READINESS), "--soak-evidence",
        str(CANONICAL_SOAK_EVIDENCE), "--checked-at-utc",
        "2026-06-01T03:00:00Z", "--strict",
    ]
    assert calls["command"].count("-S") == 1
    assert calls["cwd"] == ROOT and calls["env"] is child_env
    assert calls["capture_output"] is True and calls["check"] is False
    assert calls["input"] == LIVE_CHILD_SENTINEL_BUNDLE
    assert calls["timeout"] == 300 and prefix_checks == [True, False]
    assert result["release_gate_decision"] == "pass"


def test_live_child_parent_synthesizes_hold_for_empty_violation_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(boundary, "_trusted_pyyaml_current_blocker", lambda: None)
    monkeypatch.setattr(
        boundary, "_build_live_child_bundle", lambda: LIVE_CHILD_SENTINEL_BUNDLE
    )
    monkeypatch.setattr(boundary, "_pycache_prefix_blocker", lambda **kwargs: None)
    monkeypatch.setattr(boundary, "_child_environment", lambda: {})
    monkeypatch.setattr(
        boundary.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], boundary._LIVE_CHILD_VIOLATION_EXIT_CODE, b"", b""
        ),
    )

    assert boundary._run_live_release_gate(FIXED_NOW) == (
        boundary._sandbox_hold_report(FIXED_NOW)
    )


@pytest.mark.parametrize(
    ("stdout", "stderr"),
    [(b"forged", b""), (b"", b"diagnostic"), (b"forged", b"diagnostic")],
)
def test_live_child_parent_rejects_violation_exit_with_child_bytes(
    monkeypatch: pytest.MonkeyPatch, stdout: bytes, stderr: bytes,
) -> None:
    monkeypatch.setattr(boundary, "_trusted_pyyaml_current_blocker", lambda: None)
    monkeypatch.setattr(
        boundary, "_build_live_child_bundle", lambda: LIVE_CHILD_SENTINEL_BUNDLE
    )
    monkeypatch.setattr(boundary, "_pycache_prefix_blocker", lambda **kwargs: None)
    monkeypatch.setattr(boundary, "_child_environment", lambda: {})
    monkeypatch.setattr(
        boundary.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], boundary._LIVE_CHILD_VIOLATION_EXIT_CODE, stdout, stderr
        ),
    )

    with pytest.raises(boundary._LiveGateProtocolError) as raised:
        boundary._run_live_release_gate(FIXED_NOW)

    assert raised.value.blocker == "live_release_gate_sandbox_report_malformed"


@pytest.mark.parametrize(
    ("completed", "expected"),
    [
        (_completed(_release_gate_recheck(), stderr=b" \n"),
         "live_release_gate_stderr_not_empty"),
        (_completed({}, stdout=b'{"x": 1, "x": 2}'),
         "live_release_gate_stdout_malformed"),
        (_completed({}, stdout=b"{}\n{}\n"), "live_release_gate_stdout_malformed"),
        (_completed({}, stdout=b"[]"), "live_release_gate_stdout_malformed"),
        (_completed({**_release_gate_recheck(), "gate": None}),
         "live_release_gate_report_malformed"),
        (_completed(_release_gate_recheck(), returncode=2),
         "live_release_gate_exit_code_mismatch"),
    ],
)
def test_live_child_rejects_ambiguous_output_exit_and_shape(
    monkeypatch: pytest.MonkeyPatch,
    completed: subprocess.CompletedProcess[bytes],
    expected: str,
) -> None:
    monkeypatch.setattr(boundary, "_trusted_pyyaml_current_blocker", lambda: None)
    monkeypatch.setattr(
        boundary, "_build_live_child_bundle", lambda: LIVE_CHILD_SENTINEL_BUNDLE
    )
    monkeypatch.setattr(boundary, "_pycache_prefix_blocker", lambda **kwargs: None)
    monkeypatch.setattr(boundary, "_child_environment", lambda: {})
    monkeypatch.setattr(boundary.subprocess, "run", lambda *args, **kwargs: completed)
    with pytest.raises(boundary._LiveGateProtocolError) as raised:
        boundary._run_live_release_gate(FIXED_NOW)
    assert raised.value.blocker == expected


@pytest.mark.parametrize(
    ("sequence", "expected", "spawned"),
    [
        (["live_release_gate_pycache_prefix_preexists"],
         "live_release_gate_pycache_prefix_preexists", False),
        ([None, "live_release_gate_pycache_prefix_created"],
         "live_release_gate_pycache_prefix_created", True),
    ],
)
def test_live_child_checks_shared_root_pycache_prefix_before_and_after(
    monkeypatch: pytest.MonkeyPatch,
    sequence: list[str | None],
    expected: str,
    spawned: bool,
) -> None:
    assert boundary._PYCACHE_PREFIX.is_relative_to(ROOT)
    results = iter(sequence)
    calls = 0

    def run(*args, **kwargs):
        nonlocal calls
        calls += 1
        return _completed(_release_gate_recheck())

    monkeypatch.setattr(boundary, "_trusted_pyyaml_current_blocker", lambda: None)
    monkeypatch.setattr(
        boundary, "_build_live_child_bundle", lambda: LIVE_CHILD_SENTINEL_BUNDLE
    )
    monkeypatch.setattr(boundary, "_pycache_prefix_blocker", lambda **kwargs: next(results))
    monkeypatch.setattr(boundary, "_child_environment", lambda: {})
    monkeypatch.setattr(boundary.subprocess, "run", run)
    with pytest.raises(boundary._LiveGateProtocolError) as raised:
        boundary._run_live_release_gate(FIXED_NOW)
    assert raised.value.blocker == expected
    assert bool(calls) is spawned


def test_live_child_names_yaml_shadow_created_after_spawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shadows = iter([None, "third_party_yaml_shadow_present"])
    monkeypatch.setattr(boundary, "_yaml_shadow_blocker", lambda: next(shadows))
    monkeypatch.setattr(boundary, "_trusted_pyyaml_current_blocker", lambda: None)
    monkeypatch.setattr(
        boundary, "_build_live_child_bundle", lambda: LIVE_CHILD_SENTINEL_BUNDLE
    )
    monkeypatch.setattr(boundary, "_pycache_prefix_blocker", lambda **kwargs: None)
    monkeypatch.setattr(boundary, "_child_environment", lambda: {})
    monkeypatch.setattr(
        boundary.subprocess, "run",
        lambda *args, **kwargs: _completed(
            _release_gate_recheck(checked_at_utc="2026-06-01T03:00:00Z")
        ),
    )
    with pytest.raises(boundary._LiveGateProtocolError) as raised:
        boundary._run_live_release_gate(FIXED_NOW)
    assert raised.value.blocker == "third_party_yaml_shadow_present"


def test_pycache_root_identity_replacement_is_a_named_hold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit_root = _configure_output_test_root(tmp_path, monkeypatch)
    info = os.lstat(audit_root)
    monkeypatch.setattr(boundary, "_PYCACHE_ROOT_SAFE", True)
    monkeypatch.setattr(
        boundary, "_PYCACHE_ROOT_IDENTITY",
        (info.st_dev, info.st_ino + 1, info.st_mode),
    )
    assert boundary._pycache_prefix_blocker(
        before_spawn=True
    ) == "live_release_gate_pycache_root_changed"


def _fake_linked_git_control_plane(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "repo"
    common_dir = tmp_path / "admin.git"
    git_dir = common_dir / "worktrees" / "lane"
    root.mkdir()
    git_dir.mkdir(parents=True)
    (common_dir / "info").mkdir()
    (common_dir / "hooks").mkdir()
    (common_dir / "objects" / "info").mkdir(parents=True)
    (common_dir / "refs" / "heads").mkdir(parents=True)
    (root / ".git").write_text(f"gitdir: {git_dir.as_posix()}\n", encoding="utf-8")
    (git_dir / "commondir").write_text("../..\n", encoding="utf-8")
    (git_dir / "gitdir").write_text(
        str(root / ".git") + "\n", encoding="utf-8"
    )
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git_dir / "index").write_bytes(b"DIRC\0synthetic-index")
    (common_dir / "config").write_text(
        "[core]\n\trepositoryformatversion = 0\n\tbare = false\n",
        encoding="utf-8",
    )
    (common_dir / "info" / "exclude").write_text("# no active rules\n", encoding="utf-8")
    (common_dir / "info" / "attributes").write_text("# none\n", encoding="utf-8")
    (common_dir / "refs" / "heads" / "main").write_text(
        "0" * 40 + "\n", encoding="ascii"
    )
    (common_dir / "hooks" / "pre-commit.sample").write_text(
        "#!/bin/sh\nexit 1\n", encoding="utf-8"
    )
    (root / ".gitattributes").write_text("docs/** binary\n", encoding="utf-8")
    return root, git_dir, common_dir


def test_git_control_plane_resolves_linked_worktree_without_git(
    tmp_path: Path,
) -> None:
    root, git_dir, common_dir = _fake_linked_git_control_plane(tmp_path)

    snapshot, blocker = boundary._git_control_plane_snapshot(root)

    assert blocker is None
    assert snapshot["git_dir"] == str(git_dir.resolve())
    assert snapshot["common_dir"] == str(common_dir.resolve())
    assert snapshot["head_ref"] == "refs/heads/main"
    assert snapshot["files"]


@pytest.mark.parametrize(
    "dangerous_config",
    [
        '[include]\n\tpath = ../attacker\n',
        '[includeIf "gitdir:**/repo"]\n\tpath = ../attacker\n',
        '[filter "owned"]\n\tclean = attacker\n',
        '[core]\n\tattributesFile = ../attacker\n',
        '[core]\n\texcludesFile = ../attacker-ignore\n',
        '[core]\n\thooksPath = ../attacker-hooks\n',
        '[core]\n\tworktree = ../attacker-worktree\n',
        '[diff "owned"]\n\ttextconv = attacker\n',
        '[diff]\n\texternal = attacker\n',
        '[alias]\n\towned = !attacker\n',
    ],
)
def test_git_control_plane_rejects_dangerous_local_config(
    tmp_path: Path,
    dangerous_config: str,
) -> None:
    root, _, common_dir = _fake_linked_git_control_plane(tmp_path)
    with (common_dir / "config").open("a", encoding="utf-8") as handle:
        handle.write(dangerous_config)

    _, blocker = boundary._git_control_plane_snapshot(root)

    assert blocker == "git_control_config_dangerous"


@pytest.mark.parametrize(
    ("relative", "contents", "expected"),
    [
        ("shallow", "0" * 40 + "\n", "git_control_shallow_repository"),
        ("info/grafts", "0" * 81 + "\n", "git_control_grafts_present"),
        ("objects/info/alternates", "../objects\n", "git_control_alternates_present"),
        ("refs/replace/" + "0" * 40, "0" * 40 + "\n",
         "git_control_replace_refs_present"),
        ("hooks/pre-commit", "#!/bin/sh\nexit 0\n", "git_control_hooks_present"),
        ("info/sparse-checkout", "/*\n", "git_control_sparse_checkout_present"),
    ],
)
def test_git_control_plane_rejects_history_and_execution_overrides(
    tmp_path: Path,
    relative: str,
    contents: str,
    expected: str,
) -> None:
    root, _, common_dir = _fake_linked_git_control_plane(tmp_path)
    target = common_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(contents, encoding="utf-8")

    _, blocker = boundary._git_control_plane_snapshot(root)

    assert blocker == expected


def test_git_control_plane_rejects_dangerous_attributes(tmp_path: Path) -> None:
    root, _, _ = _fake_linked_git_control_plane(tmp_path)
    (root / ".gitattributes").write_text(
        "tools/*.py filter=attacker\n", encoding="utf-8"
    )

    _, blocker = boundary._git_control_plane_snapshot(root)

    assert blocker == "git_control_attributes_dangerous"


def test_git_control_plane_rejects_multiple_link_control_file(
    tmp_path: Path,
) -> None:
    root, git_dir, _ = _fake_linked_git_control_plane(tmp_path)
    os.link(git_dir / "HEAD", tmp_path / "head-alias")

    _, blocker = boundary._git_control_plane_snapshot(root)

    assert blocker == "git_control_file_multiple_links"


def test_trusted_git_and_child_environments_strip_hostile_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trusted = (tmp_path / "trusted" / "git.exe").resolve()
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_CONFIG_COUNT", "PYTHONPATH",
                "PYTHONHOME", "PYTHONINSPECT"):
        monkeypatch.setenv(key, "hostile")
    monkeypatch.setenv("PATH", str(tmp_path / "fake"))
    monkeypatch.setenv("SystemRoot", str(tmp_path / "ambient-system-root"))
    monkeypatch.setattr(boundary, "_trusted_git_executable", lambda: trusted)

    git_env = boundary._git_environment()
    child_env = boundary._child_environment()
    assert all(key not in git_env for key in (
        "GIT_DIR", "GIT_WORK_TREE", "GIT_CONFIG_COUNT", "PYTHONPATH", "PYTHONHOME"
    ))
    assert git_env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert child_env["PATH"] == str(trusted.parent)
    assert str(tmp_path / "ambient-system-root") not in child_env["PATH"]
    assert child_env["PYTHONPYCACHEPREFIX"] == str(boundary._PYCACHE_PREFIX)


def test_live_child_git_cross_view_identity_ignores_only_permission_projection(
) -> None:
    common = {
        "st_dev": 11,
        "st_ino": 22,
        "st_size": 33,
        "st_mtime_ns": 44,
        "st_nlink": 2,
        "st_file_attributes": 32,
        "st_reparse_tag": 0,
    }
    path_view = types.SimpleNamespace(
        **common, st_mode=stat.S_IFREG | 0o777
    )
    handle_view = types.SimpleNamespace(
        **common, st_mode=stat.S_IFREG | 0o666
    )

    assert boundary._identity(path_view) != boundary._identity(handle_view)
    expected = boundary._live_child_git_cross_view_identity(path_view)
    assert boundary._live_child_git_cross_view_identity(handle_view) == expected

    mutations = {
        "st_dev": 12,
        "st_ino": 23,
        "st_mode": stat.S_IFDIR | 0o777,
        "st_size": 34,
        "st_mtime_ns": 45,
        "st_nlink": 3,
        "st_file_attributes": 33,
        "st_reparse_tag": 1,
    }
    for field, value in mutations.items():
        mutated = {**common, "st_mode": stat.S_IFREG | 0o666, field: value}
        assert boundary._live_child_git_cross_view_identity(
            types.SimpleNamespace(**mutated)
        ) != expected


def test_trusted_git_candidate_is_lexical_regular_file(tmp_path: Path) -> None:
    candidate = (tmp_path / "trusted" / "git.exe").absolute()
    candidate.parent.mkdir()
    candidate.write_bytes(b"trusted git")

    assert boundary._trusted_git_candidate(candidate) == candidate


def test_trusted_git_candidate_rejects_redirected_allowlist_path(
    tmp_path: Path,
) -> None:
    target = (tmp_path / "outside" / "attacker.exe").absolute()
    target.parent.mkdir()
    target.write_bytes(b"not trusted")
    candidate = (tmp_path / "allowlisted" / "git.exe").absolute()
    candidate.parent.mkdir()
    try:
        candidate.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    assert boundary._trusted_git_candidate(candidate) is None


def test_live_child_git_executable_provenance_accepts_stable_exe_views(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = (tmp_path / "git.exe").resolve()
    payload = b"stable trusted git executable\n"
    executable.write_bytes(payload)
    monkeypatch.setattr(
        boundary, "_trusted_git_executable", lambda: executable
    )

    assert boundary._live_child_git_executable_provenance() == {
        "path": str(executable),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def test_live_child_git_executable_provenance_rejects_path_mode_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = (tmp_path / "git.exe").resolve()
    executable.write_bytes(b"stable trusted git executable\n")
    actual = os.lstat(executable)

    def path_view(mode: int) -> types.SimpleNamespace:
        return types.SimpleNamespace(
            st_dev=actual.st_dev,
            st_ino=actual.st_ino,
            st_mode=mode,
            st_size=actual.st_size,
            st_mtime_ns=actual.st_mtime_ns,
            st_nlink=actual.st_nlink,
            st_file_attributes=getattr(actual, "st_file_attributes", 0),
            st_reparse_tag=getattr(actual, "st_reparse_tag", 0),
        )

    observations = iter((
        path_view(stat.S_IFREG | 0o777),
        path_view(stat.S_IFREG | 0o755),
    ))
    monkeypatch.setattr(
        boundary, "_trusted_git_executable", lambda: executable
    )
    monkeypatch.setattr(boundary.os, "lstat", lambda _path: next(observations))

    with pytest.raises(
        boundary._LiveChildManifestError,
        match="live_child_git_executable_unverifiable",
    ):
        boundary._live_child_git_executable_provenance()


def test_git_exec_is_absolute_pinned_and_rejects_nonempty_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trusted = (tmp_path / "trusted" / "git.exe").resolve()
    seen: dict[str, object] = {}
    control = {
        "git_dir": str((tmp_path / "admin.git").resolve()),
        "common_dir": str((tmp_path / "admin.git").resolve()),
        "head_ref": "refs/heads/main",
        "files": {"control": (1,)},
        "directories": {"control": (1,)},
    }
    monkeypatch.setattr(boundary, "_trusted_git_executable", lambda: trusted)
    monkeypatch.setattr(
        boundary, "_git_control_plane_snapshot", lambda root=ROOT: (control, None)
    )

    def run(command, **kwargs):
        seen.update(command=command, **kwargs)
        return subprocess.CompletedProcess(command, 0, b"ok", b"")

    monkeypatch.setattr(boundary.subprocess, "run", run)
    assert boundary._git("status") == b"ok"
    command = seen["command"]
    assert Path(command[0]).is_absolute() and command[0] == str(trusted)
    assert command[-1] == "status"
    assert command[1:6] == [
        "--no-pager", "--no-optional-locks", "--no-replace-objects",
        "--no-lazy-fetch", "--literal-pathspecs",
    ]
    hooks_setting = next(
        item for item in command if item.startswith("core.hooksPath=")
    )
    assert hooks_setting.startswith("core.hooksPath=")
    hooks_path = Path(hooks_setting.partition("=")[2])
    assert hooks_path.is_absolute() and not os.path.lexists(hooks_path)
    for prefix in ("core.attributesFile=", "core.excludesFile="):
        absent_setting = next(item for item in command if item.startswith(prefix))
        absent_path = Path(absent_setting.partition("=")[2])
        assert absent_path.is_absolute() and not os.path.lexists(absent_path)
    assert "core.fsmonitor=false" in command
    git_dir_index = command.index("--git-dir")
    work_tree_index = command.index("--work-tree")
    assert command[git_dir_index + 1] == control["git_dir"]
    assert command[work_tree_index + 1] == str(ROOT)
    assert seen["cwd"] == ROOT and seen["capture_output"] is True
    assert seen["env"]["PATH"] == str(trusted.parent)
    monkeypatch.setattr(
        boundary.subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, b"ok", b"warning"),
    )
    with pytest.raises(RuntimeError, match="trusted git command failed"):
        boundary._git("status")


def test_git_exec_blocks_before_spawn_on_control_plane_hold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        boundary,
        "_git_control_plane_snapshot",
        lambda root=ROOT: ({}, "git_control_shallow_repository"),
    )
    monkeypatch.setattr(
        boundary.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("Git spawned through control-plane HOLD"),
    )

    with pytest.raises(RuntimeError, match="git_control_shallow_repository"):
        boundary._git("status")


def test_git_exec_rejects_post_command_control_plane_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trusted = (tmp_path / "trusted" / "git.exe").resolve()
    before = {
        "git_dir": str((tmp_path / "admin.git").resolve()),
        "common_dir": str((tmp_path / "admin.git").resolve()),
        "head_ref": "refs/heads/main",
        "files": {"config": (1,)},
        "directories": {},
    }
    after = {**before, "files": {"config": (2,)}}
    snapshots = iter(((before, None), (after, None)))
    monkeypatch.setattr(boundary, "_trusted_git_executable", lambda: trusted)
    monkeypatch.setattr(
        boundary, "_git_control_plane_snapshot", lambda root=ROOT: next(snapshots)
    )
    monkeypatch.setattr(
        boundary.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, b"ok", b""),
    )

    with pytest.raises(RuntimeError, match="git_control_plane_changed"):
        boundary._git("status")


def test_git_exec_rejects_materialized_absent_hook_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trusted = (tmp_path / "trusted" / "git.exe").resolve()
    control = {
        "git_dir": str((tmp_path / "admin.git").resolve()),
        "common_dir": str((tmp_path / "admin.git").resolve()),
        "head_ref": "refs/heads/main",
        "files": {},
        "directories": {},
    }
    monkeypatch.setattr(boundary, "_trusted_git_executable", lambda: trusted)
    monkeypatch.setattr(boundary, "_PYCACHE_ROOT", tmp_path)
    monkeypatch.setattr(
        boundary, "_git_control_plane_snapshot", lambda root=ROOT: (control, None)
    )

    def materialize_hook_path(command, **kwargs):
        setting = next(item for item in command if item.startswith("core.hooksPath="))
        Path(setting.partition("=")[2]).mkdir()
        return subprocess.CompletedProcess(command, 0, b"untrusted", b"")

    monkeypatch.setattr(boundary.subprocess, "run", materialize_hook_path)

    with pytest.raises(RuntimeError, match="git_control_absent_overrides_changed"):
        boundary._git("status")


@pytest.mark.parametrize("noncanonical_flag", ["h", "S"])
def test_noncanonical_git_flags_block_even_when_canonical_flags_are_h(
    monkeypatch: pytest.MonkeyPatch, noncanonical_flag: str
) -> None:
    oid = "0" * 40
    index = "".join(
        f"100644 {oid} 0\t{path}\0" for path in boundary._CANONICAL_RELATIVE_PATHS
    ).encode()
    flags = (
        "".join(f"H {path}\0" for path in boundary._CANONICAL_RELATIVE_PATHS)
        + f"{noncanonical_flag} docs/noncanonical.txt\0"
    ).encode()

    def git(*args: str) -> bytes:
        outputs = {
            ("rev-parse", "--show-toplevel"): str(ROOT).encode(),
            ("rev-parse", "--verify", "HEAD^{commit}"): CANONICAL_SOAK_COMMIT.encode(),
            ("rev-parse", "--show-object-format"): b"sha1",
            ("status", "--porcelain=v1", "-z", "--untracked-files=all",
             "--ignore-submodules=all", "--no-renames"): b"",
            ("ls-files", "-v", "-z"): flags,
        }
        return index if args[:3] == ("ls-files", "-s", "-z") else outputs[args]

    monkeypatch.setattr(boundary, "_git", git)
    monkeypatch.setattr(boundary, "_yaml_shadow_blocker", lambda: None)
    monkeypatch.setattr(boundary, "_trusted_pyyaml_current_blocker", lambda: None)
    state = boundary._git_state()
    assert state["tracked"] is True
    assert state["tracked_flags_normal"] is False
    assert boundary._pre_window_blockers(
        {"git_before": state, "inputs": {}}
    ) == ["git_tracked_flags_not_normal"]


@pytest.mark.parametrize(
    ("before_changes", "entry_error", "expected"),
    [
        ({"clean": False}, None, "git_worktree_not_clean_before"),
        ({"tracked": False}, None, "canonical_inputs_not_tracked_regular"),
        ({}, "reparse_point", "canonical_input_soak_evidence_reparse_point"),
        ({}, "not_regular_file", "canonical_input_soak_evidence_not_regular_file"),
        ({}, "unreadable_or_changed", "canonical_input_soak_evidence_unreadable_or_changed"),
    ],
)
def test_pre_window_rejects_dirty_index_type_reparse_and_read_drift(
    monkeypatch: pytest.MonkeyPatch,
    before_changes: dict[str, object], entry_error: str | None, expected: str
) -> None:
    monkeypatch.setattr(boundary, "_yaml_shadow_blocker", lambda: None)
    monkeypatch.setattr(boundary, "_trusted_pyyaml_current_blocker", lambda: None)
    window = {
        "git_before": _git_snapshot(**before_changes),
        "inputs": {"soak_evidence": _input_entry(error=entry_error)},
    }
    assert expected in boundary._pre_window_blockers(window)


@pytest.mark.parametrize(
    ("raw", "oid", "expected"),
    [
        (b"a\r\nb\r\n", boundary._git_blob_oid(b"a\nb\n"), None),
        (b"a\n", "0" * 40, "canonical_input_soak_evidence_index_blob_mismatch"),
        (b"a\0b", boundary._git_blob_oid(b"a\0b"),
         "canonical_input_soak_evidence_index_blob_mismatch"),
    ],
)
def test_index_binding_accepts_crlf_only_and_rejects_blob_or_nul(
    monkeypatch: pytest.MonkeyPatch,
    raw: bytes, oid: str, expected: str | None
) -> None:
    monkeypatch.setattr(boundary, "_yaml_shadow_blocker", lambda: None)
    monkeypatch.setattr(boundary, "_trusted_pyyaml_current_blocker", lambda: None)
    entry = {**_input_entry(), "raw": raw}
    relative = CANONICAL_SOAK_EVIDENCE.relative_to(ROOT).as_posix()
    git = _git_snapshot({"soak_evidence": entry})
    git["index"] = {relative: ("100644", oid, "0", "H")}
    blockers = boundary._pre_window_blockers(
        {"git_before": git, "inputs": {"soak_evidence": entry}}
    )
    assert (expected in blockers) if expected else blockers == []


@pytest.mark.parametrize(
    ("after_entry", "git_changes", "expected"),
    [
        ({"identity": (9, 9, 9, 9, 9, 9)}, {}, "canonical_input_soak_evidence_changed"),
        ({"digest": "changed"}, {}, "canonical_input_soak_evidence_changed"),
        ({"error": "unreadable_or_changed"}, {}, "canonical_input_soak_evidence_changed"),
        ({}, {"clean": False}, "git_worktree_not_clean_after"),
        ({}, {"head": "e" * 40}, "git_head_changed_during_evaluation"),
        ({}, {"index": {}}, "git_index_changed_during_evaluation"),
        ({}, {"tracked": False}, "canonical_inputs_not_tracked_regular_after"),
    ],
)
def test_close_window_rejects_identity_digest_and_git_pre_post_drift(
    monkeypatch: pytest.MonkeyPatch,
    after_entry: dict[str, object],
    git_changes: dict[str, object],
    expected: str,
) -> None:
    original = _input_entry()
    monkeypatch.setattr(boundary, "_capture_input", lambda path: {**original, **after_entry})
    monkeypatch.setattr(boundary, "_git_state", lambda: _git_snapshot(**git_changes))
    window = {"git_before": _git_snapshot(), "inputs": {"soak_evidence": original}}
    assert expected in boundary._close_window(window)


def test_close_window_names_post_yaml_shadow(monkeypatch: pytest.MonkeyPatch) -> None:
    original = _input_entry()
    monkeypatch.setattr(boundary, "_capture_input", lambda path: original)
    monkeypatch.setattr(boundary, "_git_state", _git_snapshot)
    monkeypatch.setattr(
        boundary, "_yaml_shadow_blocker",
        lambda: "third_party_yaml_shadow_present",
    )
    window = {"git_before": _git_snapshot(), "inputs": {"soak_evidence": original}}
    assert "third_party_yaml_shadow_present" in boundary._close_window(window)


@pytest.mark.parametrize(
    ("signed_by", "valid"),
    [
        (OPERATOR_SIGNER, True),
        ("operator:jani:2026-05-22T18:14:34+00:00", True),
        ("agent:jani:2026-05-22T18:14:34Z", False),
        ("operator: :2026-05-22T18:14:34Z", False),
        ("operator:jani:2026-05-22T18:14:34", False),
        ("operator:jani:2026-05-22T19:14:34+01:00", False),
        (None, False),
    ],
)
def test_operator_identity_is_total_and_requires_exact_zero_offset(
    signed_by: object, valid: bool
) -> None:
    assert (boundary._identity_tuple(signed_by) is not None) is valid


@pytest.mark.parametrize(
    ("kind", "mutation", "expected"),
    [
        ("phase", {"remaining_work_packages": 1}, "remaining_work_packages_malformed"),
        ("phase", {"landed_work_packages": [1]}, "landed_work_packages_0_malformed"),
        ("phase", {"blockers": "none"}, "phase_synthesis_blockers_malformed"),
        ("phase", {"sprint_id": "other"}, "phase_synthesis_sprint_id_mismatch"),
        ("continuity", {"gate": None}, "release_gate_recheck_nested_gate_malformed"),
        ("continuity", {"gate": {"decision": "hold", "blockers": []}},
         "release_gate_recheck_nested_gate_inconsistent"),
        ("continuity", {"read_only_invariants": {}},
         "release_gate_recheck_invariants_malformed"),
        ("continuity", {"blockers": [1]}, "release_gate_recheck_blockers_malformed"),
    ],
)
def test_nested_phase_and_continuity_shapes_hold_without_traceback(
    kind: str, mutation: dict[str, object], expected: str
) -> None:
    source = _phase_synthesis_refresh() if kind == "phase" else _release_gate_recheck()
    source.update(mutation)
    blockers = (
        boundary._phase_shape_blockers(source)
        if kind == "phase"
        else boundary._continuity_shape_blockers(source)
    )
    assert expected in blockers


def test_live_decision_packet_copies_live_hold_and_has_no_evidence_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    live = _release_gate_recheck(
        decision="hold", blockers=["fresh_live_veto"],
        checked_at_utc="2026-06-01T03:00:00Z",
    )
    monkeypatch.setattr(boundary, "_run_live_release_gate", lambda when: live)
    report = build_report(
        phase_synthesis_refresh=_phase_synthesis_refresh(),
        release_gate_recheck=_release_gate_recheck(),
        torch_decision_pack=_write_torch_pack(tmp_path / "torch.yaml"),
        docker_decision_pack=_write_docker_pack(tmp_path / "docker.yaml"),
        checked_at_utc=FIXED_NOW,
    )
    packet = report["release_decision_packet"]
    assert packet["schema_version"] == boundary.DECISION_PACKET_SCHEMA_VERSION
    assert packet["release_gate_decision"] == "hold"
    assert packet["release_gate_blockers"] == ["fresh_live_veto"]
    assert report["ok"] is False
    assert "live_release_gate_not_passed" in report["release_boundary_blockers"]
    assert strict_exit_code(report) == 2
    assert report["source_release_gate_readonly_recheck"]["release_gate_decision"] == "pass"
    serialized = json.dumps(report).lower()
    assert "envelope" not in serialized and "frozen_source_commit" not in serialized


# --- Stage-A S4 immutable live-child manifest and syscall virtualization ---


LIVE_CHILD_MODULE_PATHS = (
    "tools/__init__.py",
    "tools/run_release_gate_readonly_recheck.py",
    "tools/check_release_gate.py",
    "tools/verify_release_soak_evidence.py",
    "tools/collect_soak_evidence.py",
    "tools/release_security_attestation.py",
    "tools/run_release_ci_status_evidence.py",
    "tools/run_release_docker_policy_evidence.py",
    "tools/operator_decision_pack.py",
)

LIVE_CHILD_DATA_PATHS = (
    "docs/release/RELEASE_READINESS.md",
    "docs/runs/release_soak_evidence/v3.12.0.json",
    "docs/runs/release_soak_evidence/v3.12.0_ci_status.json",
    "docs/runs/release_soak_evidence/v3.12.0_docker_policy.json",
    "docs/operator_inbox/docker-latest-promotion.yaml",
    "docs/runs/release_soak_evidence/v3.12.0_security_privacy_precheck.md",
    (
        "docs/runs/release_soak_evidence/"
        "v3.12.0_bandit_report_after_static_hardening_zero_medium.json"
    ),
    (
        "docs/runs/release_soak_evidence/"
        "v3.12.0_pip_audit_report_lock_after_prune_osv.json"
    ),
    (
        "docs/runs/release_soak_evidence/"
        "v3.12.0_axis_a_solver_scale/solver_scale_proof.json"
    ),
    "docs/runs/release_soak_evidence/v3.12.0_axis_b_hex_aligned_eval.json",
    "docs/releases/v3.12.0.md",
    "docs/runs/release_soak_evidence/v3.12.0_soak_log_audit.json",
    "docs/runs/error_log.jsonl",
    "docs/runs/release_soak_evidence/v3.12.0_history.jsonl",
    "requirements.lock.txt",
    ".github/workflows/release-docker.yml",
    ".github/workflows/release-docker-stable.yml",
    "Dockerfile",
    "docker-compose.yml",
    "pyproject.toml",
    "docs/deployment/DOCKER_QUICKSTART.md",
)

LIVE_CHILD_DOCKER_SOURCE_PATHS = (
    ".github/workflows/release-docker.yml",
    ".github/workflows/release-docker-stable.yml",
    "Dockerfile",
    "docker-compose.yml",
    "pyproject.toml",
    "docs/deployment/DOCKER_QUICKSTART.md",
    "tools/run_release_docker_policy_evidence.py",
    "tools/operator_decision_pack.py",
)

LIVE_CHILD_SOAK_COMMIT = "8db47f609cd3d838dbb67c94542921b391c1ac74"
LIVE_CHILD_STALE_DOCKER_COMMIT = "bbb0cc371c19884317b07b03bcaf8b1e42a46667"


def _live_child_file_records() -> list[dict[str, object]]:
    records = []
    for relative in (*LIVE_CHILD_MODULE_PATHS, *LIVE_CHILD_DATA_PATHS):
        raw = (ROOT / relative).read_bytes()
        records.append({
            "path": relative,
            "mode": "100644",
            "oid": boundary._git_blob_oid(raw),
            "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "content_b64": base64.b64encode(raw).decode("ascii"),
        })
    return records


def _live_child_file_records_with_commits(
    *, soak_commit: str, docker_commit: str,
) -> list[dict[str, object]]:
    records = _live_child_file_records()
    updates = {
        "docs/runs/release_soak_evidence/v3.12.0.json": soak_commit,
        "docs/runs/release_soak_evidence/v3.12.0_docker_policy.json": docker_commit,
    }
    for record in records:
        commit = updates.get(str(record["path"]))
        if commit is None:
            continue
        raw = base64.b64decode(str(record["content_b64"]), validate=True)
        payload = json.loads(raw)
        payload["commit"] = commit
        raw = json.dumps(payload, indent=2, sort_keys=True).encode() + b"\n"
        record.update({
            "oid": boundary._git_blob_oid(raw),
            "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "content_b64": base64.b64encode(raw).decode("ascii"),
        })
    return records


def _live_child_git_records(
    commits: tuple[str, ...] = (
        LIVE_CHILD_STALE_DOCKER_COMMIT,
        LIVE_CHILD_SOAK_COMMIT,
    ),
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = [
        {
            "args": ["rev-parse", "--show-toplevel"],
            "text": True,
            "returncode": 0,
            "stdout_b64": base64.b64encode(str(ROOT).encode()).decode("ascii"),
            "stderr_b64": "",
        },
        {
            "args": ["rev-parse", "HEAD"],
            "text": True,
            "returncode": 0,
            "stdout_b64": base64.b64encode(TEST_CARRIER_HEAD.encode()).decode(
                "ascii"
            ),
            "stderr_b64": "",
        },
    ]
    for commit in commits:
        records.append({
            "args": ["rev-parse", "--verify", f"{commit}^{{commit}}"],
            "text": True,
            "returncode": 0,
            "stdout_b64": base64.b64encode(commit.encode()).decode("ascii"),
            "stderr_b64": "",
        })
        for relative in LIVE_CHILD_DOCKER_SOURCE_PATHS:
            absent = (
                commit == LIVE_CHILD_STALE_DOCKER_COMMIT
                and relative.startswith("tools/")
            )
            historical = f"historical:{commit}:{relative}".encode()
            oid = "" if absent else boundary._git_blob_oid(historical)
            records.append({
                "args": ["rev-parse", "--verify", f"{commit}:{relative}"],
                "text": True,
                "returncode": 128 if absent else 0,
                "stdout_b64": (
                    "" if absent
                    else base64.b64encode(oid.encode()).decode("ascii")
                ),
                "stderr_b64": "",
            })
            if not absent:
                records.append({
                    "args": ["cat-file", "blob", oid],
                    "text": False,
                    "returncode": 0,
                    "stdout_b64": base64.b64encode(historical).decode("ascii"),
                    "stderr_b64": "",
                })
    return records


def _build_test_live_child_bundle() -> bytes:
    return boundary._build_live_child_bundle(
        root=ROOT,
        file_records=_live_child_file_records(),
        git_records=_live_child_git_records(),
        git_executable={
            "path": TEST_GIT_EXECUTABLE,
            "sha256": "0" * 64,
        },
    )


def _available_official_pyyaml_sources() -> tuple[tuple[str, bytes], ...]:
    trusted = boundary._TRUSTED_PYYAML
    if (
        boundary._trusted_pyyaml_current_blocker() is None
        and isinstance(trusted, dict)
    ):
        return tuple(trusted["source_items"])
    scratch = (
        ROOT / ".codex-audit" / "pyyaml-6.0.2-official-sdist"
        / "pyyaml-6.0.2" / "lib"
    )
    if not scratch.is_dir():
        pytest.skip("authenticated PyYAML 6.0.2 transport unavailable")
    return tuple(
        (relative, (scratch / Path(relative)).read_bytes())
        for relative, _, _ in boundary.PYYAML_SOURCE_MANIFEST
    )


def _mutate_live_child_bundle(
    bundle: bytes, mutation,
) -> bytes:
    payload = json.loads(bundle.decode("ascii"))
    mutation(payload)
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")


def _assert_live_child_violation(result, *, operation: str | None = None) -> None:
    assert result.returncode == boundary._LIVE_CHILD_VIOLATION_EXIT_CODE
    assert result.stderr == b""
    report = json.loads(result.stdout)
    assert report["release_gate_decision"] == "hold"
    assert report["blockers"] == ["live_release_gate_sandbox_violation"]
    assert report["gate"]["decision"] == "hold"
    assert report["gate"]["blockers"] == [
        "live_release_gate_sandbox_violation"
    ]
    assert b"forged pass" not in result.stdout
    assert result.operation is None


def test_live_child_manifest_is_exact_nine_module_thirty_file_closure() -> None:
    assert boundary._live_child_module_paths() == LIVE_CHILD_MODULE_PATHS
    assert boundary._live_child_required_paths() == (
        *LIVE_CHILD_MODULE_PATHS,
        *LIVE_CHILD_DATA_PATHS,
    )
    assert len(boundary._live_child_required_paths()) == 30
    assert len(set(boundary._live_child_required_paths())) == 30
    assert boundary._live_child_dynamic_soak_paths() == (
        "docs/runs/error_log.jsonl",
        "docs/runs/release_soak_evidence/v3.12.0_history.jsonl",
    )


def test_live_child_soak_source_digest_normalizes_only_crlf() -> None:
    expected = "sha256:" + hashlib.sha256(b"first\nsecond\n").hexdigest()
    assert boundary._live_child_source_digest(b"first\r\nsecond\r\n") == expected
    assert boundary._live_child_source_digest(b"first\nsecond\n") == expected
    with pytest.raises(
        boundary._LiveChildManifestError,
        match="live_child_soak_source_text_invalid",
    ):
        boundary._live_child_source_digest(b"first\rsecond\n")


def test_live_child_bundle_records_both_docker_commits_and_absence() -> None:
    decoded = boundary._live_child_decode_bundle(_build_test_live_child_bundle())
    subjects = decoded["git_subjects"]
    assert set(subjects) == {
        LIVE_CHILD_STALE_DOCKER_COMMIT,
        LIVE_CHILD_SOAK_COMMIT,
    }
    assert set(subjects[LIVE_CHILD_SOAK_COMMIT]) == set(
        LIVE_CHILD_DOCKER_SOURCE_PATHS
    )
    stale = subjects[LIVE_CHILD_STALE_DOCKER_COMMIT]
    assert stale["tools/run_release_docker_policy_evidence.py"] == {
        "present": False
    }
    assert stale["tools/operator_decision_pack.py"] == {"present": False}


def test_live_child_production_schema_executes_authenticated_git_closure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = boundary._trusted_git_executable()
    if executable is None:
        pytest.skip("trusted Git executable unavailable")
    source_items = _available_official_pyyaml_sources()

    def direct_git(*args: str) -> bytes:
        completed = subprocess.run(
            [
                str(executable), "--no-pager", "--no-optional-locks",
                "--no-replace-objects", "--no-lazy-fetch",
                "--literal-pathspecs", *args,
            ],
            cwd=ROOT,
            env=boundary._git_environment(),
            capture_output=True,
            check=False,
        )
        assert completed.returncode == 0, (args, completed.stderr)
        assert completed.stderr == b""
        return completed.stdout

    monkeypatch.setattr(boundary, "_git", direct_git)
    monkeypatch.setattr(boundary, "_TRUSTED_PYYAML_BLOCKER", None)
    monkeypatch.setattr(boundary, "_TRUSTED_PYYAML", {
        "version": boundary.PYYAML_VERSION,
        "sdist_sha256": boundary.PYYAML_SDIST_SHA256,
        "source_items": source_items,
    })
    head = direct_git("rev-parse", "HEAD").decode("ascii").strip()
    bundle = boundary._build_live_child_bundle(git_state={
        "toplevel": str(ROOT),
        "head": head,
        "clean": True,
        "tracked": True,
        "tracked_flags_normal": True,
        "object_format": "sha1",
        "error": None,
    })
    decoded = boundary._live_child_decode_bundle(bundle)
    assert decoded["schema_version"] == boundary._LIVE_CHILD_BUNDLE_SCHEMA
    assert decoded["head"] == head
    assert len(decoded["files"]) == 30
    assert decoded["git_objects"]

    timestamp = "2026-09-02T05:00:00Z"
    completed = subprocess.run(
        [
            sys.executable, "-B", "-I", "-S", "-c",
            boundary._LIVE_CHILD_BOOTSTRAP,
            "--release-readiness", str(boundary.CANONICAL_RELEASE_READINESS),
            "--soak-evidence", str(CANONICAL_SOAK_EVIDENCE),
            "--checked-at-utc", timestamp, "--strict",
        ],
        cwd=ROOT,
        env=boundary._child_environment(),
        input=bundle,
        capture_output=True,
        timeout=300,
        check=False,
    )

    assert completed.stderr == b""
    assert completed.returncode in {0, boundary.STRICT_BLOCKED_EXIT_CODE}
    report = json.loads(completed.stdout)
    assert report["checked_at_utc"] == timestamp
    assert report["release_gate_decision"] in {"hold", "pass"}
    assert report["release_boundary"] == boundary.FALSE_RELEASE_BOUNDARY
    assert report["read_only_invariants"] == boundary.READ_ONLY_INVARIANTS


def test_live_child_one_historical_commit_converges_when_evidence_matches() -> None:
    bundle = boundary._build_live_child_bundle(
        root=ROOT,
        file_records=_live_child_file_records_with_commits(
            soak_commit=LIVE_CHILD_SOAK_COMMIT,
            docker_commit=LIVE_CHILD_SOAK_COMMIT,
        ),
        git_records=_live_child_git_records((LIVE_CHILD_SOAK_COMMIT,)),
        git_executable={"path": TEST_GIT_EXECUTABLE, "sha256": "0" * 64},
    )
    decoded = boundary._live_child_decode_bundle(bundle)
    assert set(decoded["git_subjects"]) == {LIVE_CHILD_SOAK_COMMIT}


def test_live_child_cross_binds_both_evidence_commit_authorities() -> None:
    with pytest.raises(boundary._LiveChildManifestError) as raised:
        boundary._build_live_child_bundle(
            root=ROOT,
            file_records=_live_child_file_records_with_commits(
                soak_commit=LIVE_CHILD_SOAK_COMMIT,
                docker_commit=LIVE_CHILD_STALE_DOCKER_COMMIT,
            ),
            git_records=_live_child_git_records((LIVE_CHILD_SOAK_COMMIT,)),
            git_executable={"path": TEST_GIT_EXECUTABLE, "sha256": "0" * 64},
        )
    assert raised.value.blocker == "live_child_historical_commit_count_invalid"


def test_live_child_git_replay_rejects_one_extra_command() -> None:
    records = _live_child_git_records()
    records.append({
        "args": ["status", "--porcelain"],
        "text": True,
        "returncode": 0,
        "stdout_b64": "",
        "stderr_b64": "",
    })
    with pytest.raises(boundary._LiveChildManifestError) as raised:
        boundary._build_live_child_bundle(
            root=ROOT,
            file_records=_live_child_file_records(),
            git_records=records,
            git_executable={"path": TEST_GIT_EXECUTABLE, "sha256": "0" * 64},
        )
    assert raised.value.blocker == "live_child_git_replay_not_exact"


def test_live_child_git_replay_rejects_blob_oid_content_mismatch() -> None:
    records = _live_child_git_records()
    record = next(item for item in records if item["args"][:2] == [
        "cat-file", "blob"
    ])
    raw = base64.b64decode(str(record["stdout_b64"]), validate=True)
    record["stdout_b64"] = base64.b64encode(raw + b"forged").decode("ascii")
    with pytest.raises(boundary._LiveChildManifestError) as raised:
        boundary._build_live_child_bundle(
            root=ROOT,
            file_records=_live_child_file_records(),
            git_records=records,
            git_executable={"path": TEST_GIT_EXECUTABLE, "sha256": "0" * 64},
        )
    assert raised.value.blocker == "live_child_git_blob_replay_invalid"


def test_live_child_production_decoder_rejects_test_fixture_schema() -> None:
    with pytest.raises(boundary._LiveChildManifestError) as raised:
        boundary._live_child_decode_bundle(
            _build_test_live_child_bundle(), allow_test_fixture=False
        )
    assert raised.value.blocker == "live_child_test_bundle_not_allowed"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p["files"].append(dict(p["files"][0])),
        lambda p: p["files"][0].update(path="../outside"),
        lambda p: p["files"].append({
            **p["files"][0], "path": p["files"][0]["path"].upper()
        }),
        lambda p: p["files"][0].update(size=p["files"][0]["size"] + 1),
        lambda p: p["files"][0].update(sha256="0" * 64),
        lambda p: p["files"][0].update(mode="120000"),
        lambda p: p["files"][0].update(oid="0" * 40),
        lambda p: p.update(root_digest="0" * 64),
    ],
    ids=(
        "duplicate", "traversal", "case-collision", "size", "hash", "mode",
        "oid", "root-digest",
    ),
)
def test_live_child_bundle_rejects_ambiguous_or_unbound_content(mutation) -> None:
    forged = _mutate_live_child_bundle(_build_test_live_child_bundle(), mutation)
    with pytest.raises(ValueError, match="live child bundle"):
        boundary._live_child_decode_bundle(forged)


def test_live_child_bootstrap_executes_modules_from_memory_only() -> None:
    bootstrap = boundary._LIVE_CHILD_BOOTSTRAP
    assert "_live_child_decode_bundle" in bootstrap
    assert "_ExactLiveModuleFinder" in bootstrap
    assert "SourceFileLoader" not in bootstrap
    assert "SourcelessFileLoader" not in bootstrap
    assert "runpy.run_path" not in bootstrap
    assert "spec_from_file_location" not in bootstrap
    assert "PathFinder" not in bootstrap


def test_live_child_vfs_reads_known_and_hides_optional_missing_path() -> None:
    runtime = boundary._live_child_runtime(_build_test_live_child_bundle())
    relative = "docs/release/RELEASE_READINESS.md"
    assert runtime.read_bytes(relative) == (ROOT / relative).read_bytes()
    assert runtime.exists(relative) is True
    assert runtime.is_file(relative) is True
    assert runtime.is_dir("docs/release") is True
    absent = boundary._LIVE_CHILD_OPTIONAL_ABSENT_PATHS[0]
    assert runtime.exists(absent) is False
    assert runtime.is_file(absent) is False
    assert runtime.is_dir(absent) is False
    assert runtime.violation is None


@pytest.mark.parametrize("method", ["exists", "is_file", "is_dir"])
def test_live_child_parent_vfs_unknown_metadata_is_a_violation(method: str) -> None:
    runtime = boundary._live_child_runtime(_build_test_live_child_bundle())

    with pytest.raises(PermissionError, match="undeclared virtual metadata"):
        getattr(runtime, method)("docs/runs/undeclared-sibling.json")

    assert runtime.violation == method


def test_live_child_declared_absence_is_the_only_negative_child_fact() -> None:
    absent = repr(boundary._LIVE_CHILD_OPTIONAL_ABSENT_PATHS)
    source = f"""
import os
from pathlib import Path
for item in {absent}:
    path = Path(item)
    assert path.exists() is False
    assert path.is_file() is False
    assert path.is_dir() is False
    assert path.is_symlink() is False
    assert os.path.exists(item) is False
    assert os.path.isfile(item) is False
    assert os.path.isdir(item) is False
    assert os.path.islink(item) is False
    assert os.access(item, os.F_OK) is False
    for operation in (path.stat, path.lstat, path.read_bytes):
        try:
            operation()
        except FileNotFoundError:
            pass
        else:
            raise AssertionError('declared absence was materialized')
print('AUTHORIZED_ABSENCE')
"""

    result = boundary._live_child_execute_source(
        _build_test_live_child_bundle(), source
    )

    assert result.returncode == 0
    assert result.stdout.strip() == b"AUTHORIZED_ABSENCE"
    assert result.stderr == b""


def test_live_child_argparse_help_is_deterministic_without_shutil_import() -> None:
    result = boundary._live_child_execute_source(
        _build_test_live_child_bundle(),
        "import argparse\n"
        "formatter=argparse.HelpFormatter(prog='wd-stage-a')\n"
        "print(formatter._width)\n",
    )

    assert result.returncode == 0
    assert result.stderr == b""
    assert result.stdout.strip() == b"78"


@pytest.mark.parametrize(
    "probe",
    [
        "Path(target).exists()",
        "Path(target).is_file()",
        "Path(target).is_dir()",
        "Path(target).is_symlink()",
        "Path(target).resolve()",
        "os.path.exists(target)",
        "os.path.isfile(target)",
        "os.path.isdir(target)",
        "os.path.islink(target)",
        "os.path.realpath(target)",
        "os.access(target, os.F_OK)",
    ],
)
def test_live_child_unknown_metadata_probe_hard_holds(probe: str) -> None:
    source = (
        "import os\nfrom pathlib import Path\n"
        "target='docs/runs/undeclared-sibling.json'\n"
        "try:\n " + probe + "\nexcept BaseException:\n pass\n"
        "print('forged pass')\n"
    )

    result = boundary._live_child_execute_source(
        _build_test_live_child_bundle(), source
    )

    _assert_live_child_violation(result)


@pytest.mark.parametrize(
    ("source", "operation"),
    [
        ("open('secret.txt', 'rb')", "builtins.open"),
        ("import io; io.open('secret.txt', 'rb')", "io.open"),
        (
            "from pathlib import Path; Path('secret.txt').read_bytes()",
            "pathlib.read_bytes",
        ),
        ("import os; os.open('secret.txt', os.O_RDONLY)", "os.open"),
        ("import os; os.stat('secret.txt')", "os.stat"),
        ("open('Dockerfile', 'wb')", "builtins.open.write"),
        ("import os; os.unlink('Dockerfile')", "os.unlink"),
        (
            "from pathlib import Path; Path('Dockerfile').write_text('x')",
            "pathlib.write_text",
        ),
    ],
)
def test_live_child_unknown_reads_and_all_mutations_are_sticky(
    source: str, operation: str,
) -> None:
    result = boundary._live_child_execute_source(
        _build_test_live_child_bundle(),
        f"try:\n {source}\nexcept BaseException:\n pass\nprint('forged pass')\n",
    )
    _assert_live_child_violation(result, operation=operation)


@pytest.mark.parametrize(
    "source",
    [
        (
            "import os,sys\n"
            "low=sys.modules['nt' if os.name=='nt' else 'posix']\n"
            "try:\n low.unlink('definitely-not-present')\n"
            "except BaseException:\n pass\nprint('forged pass')\n"
        ),
        (
            "import sys\n"
            "backend=sys.modules.get('_winapi') or "
            "sys.modules.get('_posixsubprocess')\n"
            "name='GetCurrentProcess' if '_winapi' in sys.modules else "
            "'fork_exec'\n"
            "try:\n getattr(backend,name)()\n"
            "except BaseException:\n pass\nprint('forged pass')\n"
        ),
        (
            "import socket\ntry:\n socket.SocketType()\n"
            "except BaseException:\n pass\nprint('forged pass')\n"
        ),
        (
            "import importlib.machinery\ntry:\n "
            "importlib.machinery.BuiltinImporter.find_spec('gc')\n"
            "except BaseException:\n pass\nprint('forged pass')\n"
        ),
        (
            "import _imp\ntry:\n _imp.create_builtin(None)\n"
            "except BaseException:\n pass\nprint('forged pass')\n"
        ),
        (
            "import sys\ntry:\n sys._getframe(1)\n"
            "except BaseException:\n pass\nprint('forged pass')\n"
        ),
        (
            "try:\n raise RuntimeError('frame probe')\n"
            "except BaseException as exc:\n"
            " try:\n  exc.__traceback__.tb_frame\n"
            " except BaseException:\n  pass\n"
            "print('forged pass')\n"
        ),
        (
            "globals_map=open.__globals__\n"
            "globals_map['_violate']=lambda *args,**kwargs: None\n"
            "try:\n open('undeclared','rb')\n"
            "except BaseException:\n pass\nprint('forged pass')\n"
        ),
        (
            "def descendants(cls):\n"
            " for child in cls.__subclasses__():\n"
            "  yield child\n"
            "  if child is not type:\n"
            "   yield from descendants(child)\n"
            "fileio=next(child for child in descendants(object) "
            "if child.__module__=='_io' and child.__name__=='FileIO')\n"
            "try:\n fileio('definitely-not-present','rb')\n"
            "except BaseException:\n pass\nprint('forged pass')\n"
        ),
        (
            "try:\n open('undeclared','rb')\n"
            "finally:\n print('forged pass')\n"
        ),
        (
            "try:\n import atexit\n"
            "except BaseException:\n pass\nprint('forged pass')\n"
        ),
        (
            "try:\n import shutil\n"
            "except BaseException:\n pass\nprint('forged pass')\n"
        ),
    ],
    ids=(
        "low-level-backend", "native-process-backend", "socket-alias",
        "builtin-importer", "imp-builtin-loader", "sys-frame",
        "traceback-frame", "globals-rebind", "original-fileio",
        "finally", "atexit-import", "shutil-import",
    ),
)
def test_live_child_native_and_introspection_bypasses_hard_hold(
    source: str,
) -> None:
    result = boundary._live_child_execute_source(
        _build_test_live_child_bundle(), source
    )

    _assert_live_child_violation(result)


@pytest.mark.parametrize(
    "source",
    [
        (
            "g=open.__globals__\n"
            "assert '_VFILES' not in g\n"
            "g['_VFILES']={'forged':('forged',b'forged-evidence')}\n"
            "g['_vnode']=lambda *args,**kwargs: "
            "('forged','forged','file',b'forged-evidence')\n"
            "g['_vopen']=lambda *args,**kwargs: None\n"
            "try:\n open('docs/runs/undeclared-sibling.json','rb')\n"
            "except BaseException:\n pass\nprint('forged pass')\n"
        ),
        (
            "import subprocess\n"
            "g=open.__globals__\n"
            "assert '_git_records' not in g\n"
            "g['_git_records']={(('status','--porcelain'),True):"
            "(0,b'forged',b'')}\n"
            "g['_take_git']=lambda *args,**kwargs: "
            "subprocess.CompletedProcess([],0,'forged','')\n"
            "command=[g['_GIT_PATH'],'--no-replace-objects','-c',"
            "'core.hooksPath=','-C',g['_ROOT'],'status','--porcelain']\n"
            "try:\n subprocess.run(command,capture_output=True,text=True,check=True)\n"
            "except BaseException:\n pass\nprint('forged pass')\n"
        ),
        (
            "g=open.__globals__\n"
            "g['_sources']={'tools.forged':('forged.py',b\"VALUE='forged'\",False)}\n"
            "try:\n __loader__.sources=g['_sources']\n"
            "except BaseException:\n pass\n"
            "try:\n import tools.forged\n"
            "except BaseException:\n pass\nprint('forged pass')\n"
        ),
    ],
    ids=("vfs-authority-rebind", "git-replay-rebind", "loader-rebind"),
)
def test_live_child_authority_rebinding_cannot_forge_a_pass(source: str) -> None:
    result = boundary._live_child_execute_source(
        _build_test_live_child_bundle(), source
    )

    _assert_live_child_violation(result)


@pytest.mark.parametrize(
    "attribute",
    ["__defaults__", "__kwdefaults__", "__code__"],
)
def test_live_child_runtime_guard_introspection_survives_builtin_poisoning(
    attribute: str,
) -> None:
    source = (
        "import builtins,subprocess\n"
        "builtins.len=lambda value:0\n"
        "builtins.type=lambda value:str\n"
        f"try:\n getattr(subprocess.run,{attribute!r})\n"
        "except BaseException:\n pass\nprint('forged pass')\n"
    )

    result = boundary._live_child_execute_source(
        _build_test_live_child_bundle(), source
    )

    _assert_live_child_violation(result)


def test_live_child_posix_captured_fork_exec_is_explicitly_denied() -> None:
    assert '"_fork_exec",' in boundary._LIVE_CHILD_RUNTIME_SOURCE
    if not hasattr(subprocess, "_fork_exec"):
        return

    result = boundary._live_child_execute_source(
        _build_test_live_child_bundle(),
        "import subprocess\n"
        "try:\n subprocess._fork_exec()\n"
        "except BaseException:\n pass\nprint('forged pass')\n",
    )

    _assert_live_child_violation(result)


@pytest.mark.parametrize(
    "probe",
    [
        "os.pipe()",
        "os.getcwdb()",
        "os.fstat(1)",
        "os.urandom(8)",
        "os.getlogin()",
        "os.kill(999999, 0)",
    ],
)
def test_live_child_captured_os_native_aliases_hard_hold(probe: str) -> None:
    result = boundary._live_child_execute_source(
        _build_test_live_child_bundle(),
        "import os\n"
        f"try:\n {probe}\n"
        "except BaseException:\n pass\nprint('forged pass')\n",
    )

    _assert_live_child_violation(result)


@pytest.mark.skipif(os.name != "nt", reason="Windows ntpath native aliases")
@pytest.mark.parametrize(
    "probe",
    [
        "ntpath._getfullpathname('.')",
        "ntpath._getvolumepathname('.')",
        "ntpath._findfirstfile('.')",
        "ntpath.isjunction('.')",
    ],
)
def test_live_child_captured_ntpath_native_aliases_hard_hold(
    probe: str,
) -> None:
    result = boundary._live_child_execute_source(
        _build_test_live_child_bundle(),
        "import ntpath\n"
        f"try:\n {probe}\n"
        "except BaseException:\n pass\nprint('forged pass')\n",
    )

    _assert_live_child_violation(result)


@pytest.mark.skipif(os.name != "nt", reason="Windows subprocess Handle aliases")
@pytest.mark.parametrize("method", ["Close", "__del__"])
def test_live_child_subprocess_handle_default_native_alias_hard_holds(
    method: str,
) -> None:
    source = (
        "import subprocess\n"
        f"method=getattr(subprocess.Handle,{method!r})\n"
        "try:\n method.__defaults__[0](-1)\n"
        "except BaseException:\n pass\nprint('forged pass')\n"
    )

    result = boundary._live_child_execute_source(
        _build_test_live_child_bundle(), source
    )

    _assert_live_child_violation(result)


def test_live_child_thread_creation_primitive_hard_holds() -> None:
    result = boundary._live_child_execute_source(
        _build_test_live_child_bundle(),
        "import _thread\n"
        "try:\n _thread.start_new_thread(lambda:None,())\n"
        "except BaseException:\n pass\nprint('forged pass')\n",
    )

    _assert_live_child_violation(result)


@pytest.mark.skipif(os.name != "nt", reason="Windows Popen native defaults")
def test_live_child_orphaned_popen_native_defaults_hard_hold() -> None:
    source = (
        "def descendants(cls):\n"
        " for child in cls.__subclasses__():\n"
        "  yield child\n"
        "  if child is not type:\n"
        "   yield from descendants(child)\n"
        "popen=next(child for child in descendants(object) "
        "if child.__module__=='subprocess' and child.__name__=='Popen')\n"
        "try:\n popen._internal_poll.__defaults__\n"
        "except BaseException:\n pass\nprint('forged pass')\n"
    )

    result = boundary._live_child_execute_source(
        _build_test_live_child_bundle(), source
    )

    _assert_live_child_violation(result)


@pytest.mark.parametrize(
    "container_name", ["supports_fd", "supports_follow_symlinks"],
)
def test_live_child_os_native_alias_containers_are_empty_and_immutable(
    container_name: str,
) -> None:
    source = (
        "import os\n"
        f"container=getattr(os,{container_name!r})\n"
        "assert type(container) is frozenset\n"
        "assert len(container)==0\n"
        "print('NATIVE_CONTAINER_EMPTY')\n"
    )

    result = boundary._live_child_execute_source(
        _build_test_live_child_bundle(), source
    )

    assert result.returncode == 0
    assert result.stderr == b""
    assert result.stdout.strip() == b"NATIVE_CONTAINER_EMPTY"


@pytest.mark.parametrize("method", ["lstat", "scandir"])
def test_live_child_glob_static_native_aliases_hard_hold(method: str) -> None:
    source = (
        "import glob\n"
        f"try:\n getattr(glob._StringGlobber,{method!r})('.')\n"
        "except BaseException:\n pass\nprint('forged pass')\n"
    )

    result = boundary._live_child_execute_source(
        _build_test_live_child_bundle(), source
    )

    _assert_live_child_violation(result)


def test_live_child_git_uses_exact_replay_without_process_spawn() -> None:
    runtime = boundary._live_child_runtime(_build_test_live_child_bundle())
    completed = runtime.run_git(
        ["rev-parse", "--verify", f"{LIVE_CHILD_SOAK_COMMIT}^{{commit}}"],
        text=True,
    )
    assert completed.returncode == 0
    assert completed.stdout == LIVE_CHILD_SOAK_COMMIT
    absent = runtime.run_git(
        [
            "rev-parse", "--verify",
            (
                f"{LIVE_CHILD_STALE_DOCKER_COMMIT}:"
                "tools/operator_decision_pack.py"
            ),
        ],
        text=True,
        check=False,
    )
    assert absent.returncode == 128 and absent.stdout == ""
    assert runtime.process_spawn_count == 0
    with pytest.raises(PermissionError, match="unrecorded git command"):
        runtime.run_git(["status", "--porcelain"], text=True)
    assert runtime.violation == "subprocess.run"


@pytest.mark.parametrize(
    "tail",
    [
        "print('caught')",
        "raise SystemExit(0)",
        "raise RuntimeError('after caught violation')",
    ],
    ids=("normal", "system-exit", "exception"),
)
def test_live_child_caught_violation_always_discards_output(tail: str) -> None:
    source = (
        "try:\n"
        " open('not-in-manifest', 'rb')\n"
        "except BaseException:\n"
        " pass\n"
        "print('forged pass before terminal')\n"
        f"{tail}\n"
    )
    result = boundary._live_child_execute_source(
        _build_test_live_child_bundle(), source
    )
    _assert_live_child_violation(result)
