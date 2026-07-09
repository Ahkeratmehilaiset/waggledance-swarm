# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from tools.audit_claim_safe_read_paths import build_claim_safe_read_path_audit


def test_claim_safe_audit_accepts_false_writes_and_reads(tmp_path) -> None:
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "good.py").write_text(
        "\n".join(
            [
                "report = {'claim_safe': False}",
                "if report.get('claim_safe') is True:",
                "    raise RuntimeError('unexpected live flip')",
            ]
        ),
        encoding="utf-8",
    )

    audit = build_claim_safe_read_path_audit(
        tmp_path,
        include_manifest_gate=False,
    )

    assert audit["ok"] is True
    assert audit["blockers"] == []
    assert audit["read_path_count"] == 1
    assert audit["production_true_literal_count"] == 0
    assert audit["guardrails"]["read_only"] is True


def test_claim_safe_audit_blocks_production_true_literal(tmp_path) -> None:
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "bad.py").write_text(
        "report = {'claim_safe': True}\n",
        encoding="utf-8",
    )

    audit = build_claim_safe_read_path_audit(
        tmp_path,
        include_manifest_gate=False,
    )

    assert audit["ok"] is False
    assert audit["production_true_literal_count"] == 1
    assert audit["blockers"] == [
        "production_true_claim_safe_literal:tools/bad.py:1:claim_safe",
    ]


def test_claim_safe_audit_blocks_production_true_assignment_forms(
    tmp_path,
) -> None:
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "bad_assignments.py").write_text(
        "\n".join(
            [
                "report = {}",
                "report['claim_safe'] = True",
                "report.claim_safe = True",
                "report['claim_safe']: bool = True",
                "report.setdefault('claim_safe', True)",
                "setattr(report, 'claim_safe', True)",
                "claim_safe = True",
                "literal_claim_safe = True",
            ]
        ),
        encoding="utf-8",
    )

    audit = build_claim_safe_read_path_audit(
        tmp_path,
        include_manifest_gate=False,
    )

    assert audit["ok"] is False
    assert audit["production_true_literal_count"] == 7
    assert audit["blockers"] == [
        "production_true_claim_safe_literal:tools/bad_assignments.py:2:claim_safe",
        "production_true_claim_safe_literal:tools/bad_assignments.py:3:claim_safe",
        "production_true_claim_safe_literal:tools/bad_assignments.py:4:claim_safe",
        "production_true_claim_safe_literal:tools/bad_assignments.py:5:claim_safe",
        "production_true_claim_safe_literal:tools/bad_assignments.py:6:claim_safe",
        "production_true_claim_safe_literal:tools/bad_assignments.py:7:claim_safe",
        "production_true_claim_safe_literal:tools/bad_assignments.py:8:literal_claim_safe",
    ]
    assert {
        item["kind"] for item in audit["production_true_literals"]
    } == {"assignment", "ann_assignment", "mapping_setdefault", "setattr"}


def test_claim_safe_audit_ignores_test_true_fixture(tmp_path) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "fixture.py").write_text(
        "report = {'claim_safe': True}\n",
        encoding="utf-8",
    )

    audit = build_claim_safe_read_path_audit(
        tmp_path,
        include_manifest_gate=False,
    )

    assert audit["ok"] is True
    assert audit["production_true_literal_count"] == 0
    assert audit["write_path_files"] == ["tests/fixture.py"]
