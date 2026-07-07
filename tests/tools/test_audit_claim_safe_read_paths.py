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
