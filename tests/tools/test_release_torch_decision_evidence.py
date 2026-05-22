# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import datetime as dt
import json

from tools.collect_soak_evidence import build_soak_evidence
from tools.run_release_torch_decision_evidence import (
    AUTH_SCHEMA_VERSION,
    SCHEMA_VERSION,
    build_report,
    evaluate_report,
    implementation_authorization_from_decision_pack,
    main,
)


COMMIT = "dc76e81cd8c804608bfaedf951220e46ff1baffa"


def _write_pack(
    root,
    *,
    chosen_option: str = "",
    signed_by: str = "",
    decision_id: str = "torch-cuda-vs-cpu",
    category: str = "dependency_security",
    dependency_change_lands_via_pr: str = "true",
    target_version: str | None = None,
    commit: str | None = None,
    a2_index_url: str = "https://download.pytorch.org/whl/cu126",
    a1_torch: str = "torch==2.11.0",
):
    path = root / "torch-cuda-vs-cpu.yaml"
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
        "  - id: A1_cpu_only\n"
        "    summary: CPU wheels\n"
        "    data:\n"
        f'      packages: ["{a1_torch}", "torchvision==0.26.0", "torchaudio==2.11.0"]\n'
        "      fixes_osv_vulns: true\n"
        "      keeps_gpu: false\n"
        "  - id: A2_cu126\n"
        "    summary: CUDA 12.6 wheels\n"
        "    data:\n"
        '      packages: ["torch==2.11.0+cu126", "torchvision==0.27.0+cu126", "torchaudio==2.11.0+cu126"]\n'
        f'      index_url: "{a2_index_url}"\n'
        "      fixes_osv_vulns: true\n"
        "      keeps_gpu: true\n"
        "  - id: B_descope\n"
        "    summary: Drop torch family\n"
        "    data:\n"
        "      fixes_osv_vulns: true\n"
        "      keeps_gpu: false\n"
        "operator_signoff:\n"
        f'  signed_by: "{signed_by}"\n'
        f'  chosen_option: "{chosen_option}"\n'
        "structural_invariants:\n"
        "  no_main_branch_auto_merge: true\n"
        f"  dependency_change_lands_via_pr: {dependency_change_lands_via_pr}\n"
        "  agent_must_not_self_resolve: true\n",
        encoding="utf-8",
    )
    return path


def _write_security_artifacts(root) -> None:
    (root / "v3.12.0_bandit_report_after_static_hardening_zero_medium.json").write_text(
        json.dumps({
            "metrics": {
                "_totals": {
                    "SEVERITY.HIGH": 0,
                    "SEVERITY.MEDIUM": 0,
                }
            },
            "results": [],
        }),
        encoding="utf-8",
    )
    (root / "v3.12.0_security_privacy_precheck.md").write_text(
        "74 passed\nSMOKE_OK\n",
        encoding="utf-8",
    )
    (root / "v3.12.0_pip_audit_report_lock_after_prune_osv.json").write_text(
        json.dumps({
            "dependencies": [
                {"name": "torch", "skip_reason": "Dependency could not be audited"}
            ]
        }),
        encoding="utf-8",
    )


def test_unsigned_pack_stays_draft(tmp_path) -> None:
    pack = _write_pack(tmp_path)

    auth = implementation_authorization_from_decision_pack(
        pack,
        commit=COMMIT,
        target_version="v3.12.0",
    )
    report = build_report(commit=COMMIT, implementation_authorization=auth)

    assert auth is None
    assert report["torch_decision_status"] == "draft"
    assert report["release_gate_effect"] == "none"
    assert report["blockers"] == ["operator_decision_pack_unsigned_or_invalid"]


def test_signed_cpu_only_pack_authorizes_implementation_not_gate(tmp_path) -> None:
    pack = _write_pack(
        tmp_path,
        chosen_option="A1_cpu_only",
        signed_by="operator:janik:2026-05-24T00:00:00Z",
    )

    auth = implementation_authorization_from_decision_pack(
        pack,
        commit=COMMIT,
        target_version="v3.12.0",
    )
    report = build_report(commit=COMMIT, implementation_authorization=auth)

    assert auth is not None
    assert auth["schema_version"] == AUTH_SCHEMA_VERSION
    assert auth["chosen_option"] == "A1_cpu_only"
    assert auth["implementation_authorized"] is True
    assert auth["security_privacy_gate_pass_authorized"] is False
    assert auth["clean_osv_or_pip_audit_required"] is True
    assert auth["pip_audit_skip_is_not_clean"] is True
    assert auth["keeps_gpu"] is False
    assert auth["lock_followups_required"] == [
        "torchao>=0.17.0",
        "drop_or_cpu-pin_xformers",
    ]
    assert auth["packages"] == [
        "torch==2.11.0",
        "torchvision==0.26.0",
        "torchaudio==2.11.0",
    ]
    assert report["schema_version"] == SCHEMA_VERSION
    assert report["torch_decision_status"] == "implementation_authorized"
    assert report["security_privacy_gate_status"] == "unchanged"
    assert report["blockers"] == []


def test_signed_cu126_pack_requires_xformers_and_driver_verification(tmp_path) -> None:
    pack = _write_pack(
        tmp_path,
        chosen_option="A2_cu126",
        signed_by="operator:janik:2026-05-24T00:00:00Z",
    )

    auth = implementation_authorization_from_decision_pack(
        pack,
        commit=COMMIT,
        target_version="v3.12.0",
    )

    assert auth is not None
    assert auth["chosen_option"] == "A2_cu126"
    assert auth["keeps_gpu"] is True
    assert auth["requires_cuda_12_6_driver"] is True
    assert auth["xformers_cu126_verification_required"] is True
    assert auth["lock_followups_required"] == [
        "torchao_compatible_with_torch_2.11",
        "xformers_cu126_wheel_or_drop",
    ]
    assert auth["index_url"] == "https://download.pytorch.org/whl/cu126"


def test_signed_descope_pack_authorizes_descope_only(tmp_path) -> None:
    pack = _write_pack(
        tmp_path,
        chosen_option="B_descope",
        signed_by="operator:janik:2026-05-24T00:00:00Z",
    )

    auth = implementation_authorization_from_decision_pack(
        pack,
        commit=COMMIT,
        target_version="v3.12.0",
    )
    report = build_report(commit=COMMIT, implementation_authorization=auth)

    assert auth is not None
    assert auth["chosen_option"] == "B_descope"
    assert auth["descope_torch_family"] is True
    assert auth["lock_followups_required"] == [
        "move_torch_family_to_optional_extra",
        "move_xformers_to_optional_extra",
    ]
    assert auth["packages"] == []
    assert report["torch_decision_status"] == "implementation_authorized"
    assert report["release_gate_effect"] == "none"


def test_wrong_scope_or_flipped_invariant_fails_closed(tmp_path) -> None:
    wrong_id = _write_pack(
        tmp_path,
        decision_id="docker-latest-promotion",
        chosen_option="A1_cpu_only",
        signed_by="operator:janik:2026-05-24T00:00:00Z",
    )
    assert implementation_authorization_from_decision_pack(
        wrong_id,
        commit=COMMIT,
        target_version="v3.12.0",
    ) is None

    flipped = _write_pack(
        tmp_path,
        chosen_option="A1_cpu_only",
        signed_by="operator:janik:2026-05-24T00:00:00Z",
        dependency_change_lands_via_pr="false",
    )
    assert implementation_authorization_from_decision_pack(
        flipped,
        commit=COMMIT,
        target_version="v3.12.0",
    ) is None


def test_target_commit_and_signoff_mismatches_fail_closed(tmp_path) -> None:
    target_mismatch = _write_pack(
        tmp_path,
        chosen_option="A1_cpu_only",
        signed_by="operator:janik:2026-05-24T00:00:00Z",
        target_version="v3.13.0",
    )
    assert implementation_authorization_from_decision_pack(
        target_mismatch,
        commit=COMMIT,
        target_version="v3.12.0",
    ) is None

    commit_mismatch = _write_pack(
        tmp_path,
        chosen_option="A1_cpu_only",
        signed_by="operator:janik:2026-05-24T00:00:00Z",
        commit="other",
    )
    assert implementation_authorization_from_decision_pack(
        commit_mismatch,
        commit=COMMIT,
        target_version="v3.12.0",
    ) is None

    bad_signoff = _write_pack(
        tmp_path,
        chosen_option="A1_cpu_only",
        signed_by="operator:janik",
    )
    assert implementation_authorization_from_decision_pack(
        bad_signoff,
        commit=COMMIT,
        target_version="v3.12.0",
    ) is None


def test_pack_option_tampering_fails_closed(tmp_path) -> None:
    bad_package = _write_pack(
        tmp_path,
        chosen_option="A1_cpu_only",
        signed_by="operator:janik:2026-05-24T00:00:00Z",
        a1_torch="torch==0.0.0",
    )
    assert implementation_authorization_from_decision_pack(
        bad_package,
        commit=COMMIT,
        target_version="v3.12.0",
    ) is None

    bad_index = _write_pack(
        tmp_path,
        chosen_option="A2_cu126",
        signed_by="operator:janik:2026-05-24T00:00:00Z",
        a2_index_url="https://example.invalid",
    )
    assert implementation_authorization_from_decision_pack(
        bad_index,
        commit=COMMIT,
        target_version="v3.12.0",
    ) is None


def test_report_rejects_any_pack_claiming_release_gate_pass(tmp_path) -> None:
    pack = _write_pack(
        tmp_path,
        chosen_option="A1_cpu_only",
        signed_by="operator:janik:2026-05-24T00:00:00Z",
    )
    auth = implementation_authorization_from_decision_pack(
        pack,
        commit=COMMIT,
        target_version="v3.12.0",
    )
    assert auth is not None
    report = build_report(commit=COMMIT, implementation_authorization=auth)
    report["release_gate_effect"] = "pass"
    report["implementation_authorization"][
        "security_privacy_gate_pass_authorized"
    ] = True

    blockers = evaluate_report(report, expected_commit=COMMIT)

    assert "release_gate_effect_must_be_none" in blockers
    assert "security_gate_must_not_be_pack_authorized" in blockers


def test_signed_pack_does_not_make_skipped_torch_audit_clean(tmp_path) -> None:
    pack = _write_pack(
        tmp_path,
        chosen_option="A2_cu126",
        signed_by="operator:janik:2026-05-24T00:00:00Z",
    )
    auth = implementation_authorization_from_decision_pack(
        pack,
        commit=COMMIT,
        target_version="v3.12.0",
    )
    assert auth is not None

    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    _write_security_artifacts(evidence_root)
    release_notes = tmp_path / "v3.12.0.md"
    release_notes.write_text(
        "Does **not** claim AGI, consciousness, model superiority\n"
        "States Docker `:latest` will remain `v3.8.0`\n",
        encoding="utf-8",
    )

    evidence = build_soak_evidence(
        "docs/release/RELEASE_READINESS.md",
        commit=COMMIT,
        ended_at_utc=dt.datetime(2026, 5, 24, tzinfo=dt.UTC),
        status_overrides={"security_privacy_gate": "pass"},
        use_local_artifacts=True,
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert evidence["security_privacy_gate"] == "blocked"


def test_main_uses_signed_pack(tmp_path) -> None:
    pack = _write_pack(
        tmp_path,
        chosen_option="A1_cpu_only",
        signed_by="operator:janik:2026-05-24T00:00:00Z",
    )
    output = tmp_path / "torch_decision.json"

    rc = main([
        "--commit",
        COMMIT,
        "--operator-decision-pack",
        str(pack),
        "--output",
        str(output),
    ])

    assert rc == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["torch_decision_status"] == "implementation_authorized"
    assert report["release_gate_effect"] == "none"
    assert report["implementation_authorization"]["pip_audit_skip_is_not_clean"] is True


def test_main_current_unsigned_pack_stays_draft(tmp_path) -> None:
    output = tmp_path / "torch_decision.json"

    rc = main([
        "--commit",
        COMMIT,
        "--operator-decision-pack",
        "docs/operator_inbox/torch-cuda-vs-cpu.yaml",
        "--output",
        str(output),
        "--allow-draft",
    ])

    assert rc == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["torch_decision_status"] == "draft"
    assert report["blockers"] == ["operator_decision_pack_unsigned_or_invalid"]
