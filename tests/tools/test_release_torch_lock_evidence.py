# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json

from tools.run_release_torch_lock_evidence import (
    SCHEMA_VERSION,
    build_report,
    evaluate_report,
    main,
)


COMMIT = "dc76e81cd8c804608bfaedf951220e46ff1baffa"


def _write_pack(
    root,
    *,
    chosen_option: str = "A2_cu126",
    scope_update: bool = False,
    scope_signed: bool = True,
    scope_operator_id: str = "jani",
    transition_accepts_prior: bool = True,
    malformed_scope_update: bool = False,
    scalar_required_absent: bool = False,
    duplicate_scope_timestamp: bool = False,
    recorded_utc: str = "2026-08-26T05:06:00Z",
):
    path = root / "torch-cuda-vs-cpu.yaml"
    scope_text = ""
    if scope_update or malformed_scope_update:
        windows_pin = "2.12.0+cu126" if malformed_scope_update else "2.13.0+cu126"
        signed_line = (
            f'      signed_by: "operator:{scope_operator_id}:2026-08-26T06:30:00Z"\n'
            if scope_signed
            else ""
        )
        absent = (
            "torchvision"
            if scalar_required_absent
            else "[chromadb, torchvision, torchaudio, torchao, xformers]"
        )
        scope_item = (
            f"    - recorded_utc: {recorded_utc}\n"
            "      recorded_by: codex-lead-1\n"
            "      source: operator-directive:2026-08-26:swarm-efficiency-overhaul-release-burndown-E1-E2\n"
            f"{signed_line}"
            "      lock_evidence_contract:\n"
            "        schema_version: waggledance.torch_lock_scope_update.v1\n"
            "        strategy: a2_cu126_torch_2_13_chroma_descope\n"
            "        pytorch_extra_index_url: https://download.pytorch.org/whl/cu126\n"
            "        windows_pins:\n"
            f"          torch: {windows_pin}\n"
            "        non_windows_pins:\n"
            "          torch: 2.13.0\n"
            f"        required_absent: {absent}\n"
            "        transition_accepts_signed_prior: "
            f"{'true' if transition_accepts_prior else 'false'}\n"
        )
        scope_text = "  scope_updates:\n" + scope_item
        if duplicate_scope_timestamp:
            scope_text += scope_item
    path.write_text(
        "schema_version: waggledance.operator_decision_pack.v1\n"
        "decision_id: torch-cuda-vs-cpu\n"
        "category: dependency_security\n"
        "created_utc: 2026-05-22T14:00:00Z\n"
        "author_agent: claude\n"
        "options:\n"
        "  - id: A1_cpu_only\n"
        "    summary: CPU wheels\n"
        "    data:\n"
        '      packages: ["torch==2.11.0", "torchvision==0.26.0", "torchaudio==2.11.0"]\n'
        "      fixes_osv_vulns: true\n"
        "      keeps_gpu: false\n"
        "  - id: A2_cu126\n"
        "    summary: CUDA 12.6 wheels\n"
        "    data:\n"
        '      packages: ["torch==2.11.0+cu126", "torchvision==0.26.0+cu126", "torchaudio==2.11.0+cu126"]\n'
        '      index_url: "https://download.pytorch.org/whl/cu126"\n'
        "      fixes_osv_vulns: true\n"
        "      keeps_gpu: true\n"
        "  - id: B_descope\n"
        "    summary: Drop torch family\n"
        "    data:\n"
        "      fixes_osv_vulns: true\n"
        "      keeps_gpu: false\n"
        "operator_signoff:\n"
        '  signed_by: "operator:jani:2026-05-22T18:14:34Z"\n'
        f'  chosen_option: "{chosen_option}"\n'
        + scope_text
        + "structural_invariants:\n"
        "  no_main_branch_auto_merge: true\n"
        "  dependency_change_lands_via_pr: true\n"
        "  agent_must_not_self_resolve: true\n",
        encoding="utf-8",
    )
    return path


def _write_refresh_lock(
    root,
    *,
    keep_companion: bool = False,
    keep_chromadb: bool = False,
    index_option: str = "--extra-index-url https://download.pytorch.org/whl/cu126",
    extra_index_option: str = "",
):
    path = root / "requirements.lock.txt"
    lines = [
        index_option,
        'torch==2.13.0+cu126 ; sys_platform == "win32"',
        'torch==2.13.0 ; sys_platform != "win32"',
    ]
    if keep_companion:
        lines.append("xformers==0.0.35")
    if keep_chromadb:
        lines.append("chromadb==1.5.9")
    if extra_index_option:
        lines.append(extra_index_option)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_lock(root, *, extra_index: bool = True, stale: bool = False):
    path = root / "requirements.lock.txt"
    if stale:
        lines = [
            "torch==2.7.1+cu118 ; sys_platform == \"win32\"",
            "torch==2.7.1 ; sys_platform != \"win32\"",
            "torchao==0.16.0",
            "torchaudio==2.7.1+cu118 ; sys_platform == \"win32\"",
            "torchaudio==2.7.1 ; sys_platform != \"win32\"",
            "torchvision==0.22.1+cu118 ; sys_platform == \"win32\"",
            "torchvision==0.22.1 ; sys_platform != \"win32\"",
            "xformers==0.0.35",
        ]
    else:
        lines = [
            "torch==2.11.0+cu126 ; sys_platform == \"win32\"",
            "torch==2.11.0 ; sys_platform != \"win32\"",
            "torchao==0.17.0",
            "torchaudio==2.11.0+cu126 ; sys_platform == \"win32\"",
            "torchaudio==2.11.0 ; sys_platform != \"win32\"",
            "torchvision==0.26.0+cu126 ; sys_platform == \"win32\"",
            "torchvision==0.26.0 ; sys_platform != \"win32\"",
            "xformers==0.0.35",
        ]
    prefix = ["--extra-index-url https://download.pytorch.org/whl/cu126"] if extra_index else []
    path.write_text("\n".join(prefix + lines) + "\n", encoding="utf-8")
    return path


def test_a2_lock_evidence_passes_only_for_matching_cu126_lock(tmp_path) -> None:
    pack = _write_pack(tmp_path)
    lock = _write_lock(tmp_path)

    report = build_report(
        commit=COMMIT,
        requirements_lock=lock,
        operator_decision_pack=pack,
    )

    assert report["schema_version"] == SCHEMA_VERSION
    assert report["torch_lock_status"] == "implemented"
    assert report["release_gate_effect"] == "none"
    assert report["security_privacy_gate_status"] == "unchanged"
    assert report["fresh_pip_audit_required"] is True
    assert report["lock_summary"]["windows_pins"]["torch"] == "2.11.0+cu126"
    assert report["lock_summary"]["linux_pins"]["torch"] == "2.11.0"
    assert evaluate_report(report, expected_commit=COMMIT) == []


def test_a2_lock_evidence_blocks_stale_cu118_lock(tmp_path) -> None:
    pack = _write_pack(tmp_path)
    lock = _write_lock(tmp_path, stale=True)

    report = build_report(
        commit=COMMIT,
        requirements_lock=lock,
        operator_decision_pack=pack,
    )

    assert report["torch_lock_status"] == "blocked"
    assert "windows_cu126_pins_mismatch" in report["blockers"]
    assert "stale_cu118_or_torch_2_7_1_pins_present" in report["blockers"]


def test_a2_lock_evidence_blocks_missing_pytorch_index(tmp_path) -> None:
    pack = _write_pack(tmp_path)
    lock = _write_lock(tmp_path, extra_index=False)

    report = build_report(
        commit=COMMIT,
        requirements_lock=lock,
        operator_decision_pack=pack,
    )

    assert report["torch_lock_status"] == "blocked"
    assert "pytorch_cu126_extra_index_missing" in report["blockers"]


def test_scope_update_accepts_exact_torch_2_13_descope_lock(tmp_path) -> None:
    pack = _write_pack(tmp_path, scope_update=True)
    lock = _write_refresh_lock(tmp_path)

    report = build_report(
        commit=COMMIT,
        requirements_lock=lock,
        operator_decision_pack=pack,
    )

    assert report["torch_lock_status"] == "implemented"
    assert report["lock_summary"]["windows_pins"] == {
        "torch": "2.13.0+cu126"
    }
    assert report["lock_summary"]["unexpected_present"] == []
    assert report["prior_signed_authorization"]["packages"][0] == (
        "torch==2.11.0+cu126"
    )
    assert report["active_lock_authorization"]["packages"] == [
        'torch==2.13.0+cu126 ; sys_platform == "win32"',
        'torch==2.13.0 ; sys_platform != "win32"',
    ]
    assert evaluate_report(report, expected_commit=COMMIT) == []


def test_scope_update_reports_signed_prior_as_transition_pending(tmp_path) -> None:
    pack = _write_pack(tmp_path, scope_update=True)
    lock = _write_lock(tmp_path)

    report = build_report(
        commit=COMMIT,
        requirements_lock=lock,
        operator_decision_pack=pack,
    )

    assert report["torch_lock_status"] == "transition_pending"
    assert report["lock_summary"]["transition_requires"] == (
        "a2_cu126_torch_2_13_chroma_descope"
    )
    blockers = evaluate_report(report, expected_commit=COMMIT)
    assert "torch_lock_transition_pending" in blockers
    assert "torch_lock_not_implemented" in blockers

    output = tmp_path / "transition.json"
    rc = main([
        "--commit",
        COMMIT,
        "--operator-decision-pack",
        str(pack),
        "--requirements-lock",
        str(lock),
        "--output",
        str(output),
    ])
    assert rc == 1


def test_unsigned_scope_update_cannot_authorize_refresh_lock(tmp_path) -> None:
    pack = _write_pack(tmp_path, scope_update=True, scope_signed=False)
    lock = _write_refresh_lock(tmp_path)

    report = build_report(
        commit=COMMIT,
        requirements_lock=lock,
        operator_decision_pack=pack,
    )

    assert report["torch_lock_status"] == "blocked"
    assert "operator_scope_update_signature_required" in report["blockers"]
    assert report["active_scope_update"] is None
    assert report["pending_scope_update"]["operator_signed"] is False
    assert evaluate_report(report, expected_commit=COMMIT)


def test_scope_update_rejects_different_operator_identity(tmp_path) -> None:
    pack = _write_pack(
        tmp_path,
        scope_update=True,
        scope_operator_id="someone-else",
    )
    lock = _write_refresh_lock(tmp_path)

    report = build_report(
        commit=COMMIT,
        requirements_lock=lock,
        operator_decision_pack=pack,
    )

    assert "operator_scope_update_signer_mismatch" in report["blockers"]


def test_transition_false_still_accepts_exact_refresh_lock(tmp_path) -> None:
    pack = _write_pack(
        tmp_path,
        scope_update=True,
        transition_accepts_prior=False,
    )
    lock = _write_refresh_lock(tmp_path)

    report = build_report(
        commit=COMMIT,
        requirements_lock=lock,
        operator_decision_pack=pack,
    )

    assert report["torch_lock_status"] == "implemented"
    assert evaluate_report(report, expected_commit=COMMIT) == []


def test_transition_false_blocks_signed_prior_lock(tmp_path) -> None:
    pack = _write_pack(
        tmp_path,
        scope_update=True,
        transition_accepts_prior=False,
    )
    lock = _write_lock(tmp_path)

    report = build_report(
        commit=COMMIT,
        requirements_lock=lock,
        operator_decision_pack=pack,
    )

    assert report["torch_lock_status"] == "blocked"
    assert "torch_lock_matches_no_authorized_contract" in report["blockers"]


def test_scope_update_blocks_retained_companion(tmp_path) -> None:
    pack = _write_pack(tmp_path, scope_update=True)
    lock = _write_refresh_lock(tmp_path, keep_companion=True)

    report = build_report(
        commit=COMMIT,
        requirements_lock=lock,
        operator_decision_pack=pack,
    )

    assert report["torch_lock_status"] == "blocked"
    assert "descope_required_packages_present" in report["blockers"]


def test_scope_update_blocks_retained_chromadb(tmp_path) -> None:
    pack = _write_pack(tmp_path, scope_update=True)
    lock = _write_refresh_lock(tmp_path, keep_chromadb=True)

    report = build_report(
        commit=COMMIT,
        requirements_lock=lock,
        operator_decision_pack=pack,
    )

    assert report["torch_lock_status"] == "blocked"
    assert report["lock_summary"]["unexpected_present"] == ["chromadb"]


def test_scope_update_rejects_scalar_required_absent(tmp_path) -> None:
    pack = _write_pack(
        tmp_path,
        scope_update=True,
        scalar_required_absent=True,
    )
    lock = _write_refresh_lock(tmp_path)

    report = build_report(
        commit=COMMIT,
        requirements_lock=lock,
        operator_decision_pack=pack,
    )

    assert "operator_scope_update_required_absent_invalid" in report["blockers"]


def test_scope_update_rejects_duplicate_contract_timestamp(tmp_path) -> None:
    pack = _write_pack(
        tmp_path,
        scope_update=True,
        duplicate_scope_timestamp=True,
    )
    lock = _write_refresh_lock(tmp_path)

    report = build_report(
        commit=COMMIT,
        requirements_lock=lock,
        operator_decision_pack=pack,
    )

    assert "operator_scope_update_timestamp_ambiguous" in report["blockers"]


def test_scope_update_rejects_unparseable_timestamp(tmp_path) -> None:
    pack = _write_pack(
        tmp_path,
        scope_update=True,
        recorded_utc="not-a-timestamp",
    )
    lock = _write_refresh_lock(tmp_path)

    report = build_report(
        commit=COMMIT,
        requirements_lock=lock,
        operator_decision_pack=pack,
    )

    assert "operator_scope_update_timestamp_invalid" in report["blockers"]


def test_scope_update_requires_exact_pytorch_index_option(tmp_path) -> None:
    pack = _write_pack(tmp_path, scope_update=True)
    lock = _write_refresh_lock(
        tmp_path,
        index_option=(
            "--extra-index-url "
            "https://download.pytorch.org/whl/cu126.attacker.invalid"
        ),
    )

    report = build_report(
        commit=COMMIT,
        requirements_lock=lock,
        operator_decision_pack=pack,
    )

    assert "pytorch_cu126_extra_index_missing" in report["blockers"]


def test_scope_update_rejects_additional_extra_index(tmp_path) -> None:
    pack = _write_pack(tmp_path, scope_update=True)
    lock = _write_refresh_lock(
        tmp_path,
        extra_index_option="--extra-index-url https://attacker.invalid/simple",
    )

    report = build_report(
        commit=COMMIT,
        requirements_lock=lock,
        operator_decision_pack=pack,
    )

    assert report["lock_summary"]["unexpected_extra_indexes"] == [
        "--extra-index-url https://attacker.invalid/simple"
    ]
    assert "unexpected_extra_index_present" in report["blockers"]


def test_malformed_scope_update_fails_closed(tmp_path) -> None:
    pack = _write_pack(tmp_path, malformed_scope_update=True)
    lock = _write_lock(tmp_path)

    report = build_report(
        commit=COMMIT,
        requirements_lock=lock,
        operator_decision_pack=pack,
    )

    assert report["torch_lock_status"] == "blocked"
    assert "operator_scope_update_contract_invalid" in report["blockers"]


def test_report_rejects_any_security_gate_flip(tmp_path) -> None:
    pack = _write_pack(tmp_path)
    lock = _write_lock(tmp_path)
    report = build_report(
        commit=COMMIT,
        requirements_lock=lock,
        operator_decision_pack=pack,
    )
    report["security_privacy_gate_status"] = "pass"
    report["fresh_pip_audit_required"] = False

    blockers = evaluate_report(report, expected_commit=COMMIT)

    assert "security_privacy_gate_must_stay_unchanged" in blockers
    assert "fresh_pip_audit_not_required" in blockers


def test_report_rejects_refresh_authorization_package_tamper(tmp_path) -> None:
    pack = _write_pack(tmp_path, scope_update=True)
    lock = _write_refresh_lock(tmp_path)
    report = build_report(
        commit=COMMIT,
        requirements_lock=lock,
        operator_decision_pack=pack,
    )
    report["active_lock_authorization"]["packages"] = ["torch==9.9.9"]

    blockers = evaluate_report(report, expected_commit=COMMIT)

    assert "active_lock_authorization_packages_mismatch" in blockers


def test_report_rejects_missing_refresh_authorization_without_crashing(
    tmp_path,
) -> None:
    pack = _write_pack(tmp_path, scope_update=True)
    lock = _write_refresh_lock(tmp_path)
    report = build_report(
        commit=COMMIT,
        requirements_lock=lock,
        operator_decision_pack=pack,
    )
    report["active_lock_authorization"] = None

    blockers = evaluate_report(report, expected_commit=COMMIT)

    assert "active_lock_authorization_missing" in blockers


def test_main_writes_current_repository_lock_evidence(tmp_path) -> None:
    output = tmp_path / "torch_lock_evidence.json"

    rc = main([
        "--commit",
        COMMIT,
        "--operator-decision-pack",
        "docs/operator_inbox/torch-cuda-vs-cpu.yaml",
        "--requirements-lock",
        "requirements.lock.txt",
        "--output",
        str(output),
    ])

    assert rc == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["torch_lock_status"] == "blocked"
    assert "operator_scope_update_signature_required" in report["blockers"]
