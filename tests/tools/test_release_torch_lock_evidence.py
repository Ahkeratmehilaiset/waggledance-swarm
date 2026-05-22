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


def _write_pack(root, *, chosen_option: str = "A2_cu126"):
    path = root / "torch-cuda-vs-cpu.yaml"
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
        "structural_invariants:\n"
        "  no_main_branch_auto_merge: true\n"
        "  dependency_change_lands_via_pr: true\n"
        "  agent_must_not_self_resolve: true\n",
        encoding="utf-8",
    )
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

    assert rc == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["torch_lock_status"] == "implemented"
    assert report["lock_summary"]["windows_pins"] == {
        "torch": "2.11.0+cu126",
        "torchvision": "0.26.0+cu126",
        "torchaudio": "2.11.0+cu126",
    }
