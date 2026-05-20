# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json
from pathlib import Path

from tools.magma_receipt_adoption_report import (
    AdoptionTarget,
    build_adoption_report,
    main,
    render_markdown,
)


def test_build_adoption_report_classifies_receipt_and_gap_paths(
    tmp_path: Path,
) -> None:
    receipt_path = tmp_path / "tools" / "demo.py"
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_text(
        "\n".join(
            [
                "from waggledance.core.magma.receipt import build_magma_receipt",
                "from waggledance.core.magma.evaluation_result import build_evaluation_result",
                "from waggledance.core.magma.receipt_bundle import write_receipt_bundle",
                "build_evaluation_result",
                "build_magma_receipt",
                "write_receipt_bundle",
            ]
        ),
        encoding="utf-8",
    )
    runtime_path = tmp_path / "waggledance" / "core" / "autonomy" / "runtime.py"
    runtime_path.parent.mkdir(parents=True)
    runtime_path.write_text(
        "_magma_safe('audit.action', self.audit.record_action_event)\n",
        encoding="utf-8",
    )

    targets = (
        AdoptionTarget("tools/demo.py", "demo", "medium", "fixture"),
        AdoptionTarget(
            "waggledance/core/autonomy/runtime.py",
            "runtime",
            "medium",
            "fixture",
        ),
        AdoptionTarget("missing.py", "missing", "high", "fixture"),
    )
    report = build_adoption_report(root=tmp_path, targets=targets)

    statuses = {entry["path"]: entry["status"] for entry in report["entries"]}
    assert statuses["tools/demo.py"] == "receipt_bound"
    assert statuses["waggledance/core/autonomy/runtime.py"] == "magma_event_only"
    assert statuses["missing.py"] == "missing"
    assert report["status_counts"]["receipt_bound"] == 1
    assert report["high_criticality_gap_count"] == 1


def test_render_markdown_includes_gap_count(tmp_path: Path) -> None:
    report = build_adoption_report(
        root=tmp_path,
        targets=(
            AdoptionTarget("missing.py", "missing", "high", "fixture"),
        ),
    )

    markdown = render_markdown(report)

    assert "MAGMA receipt adoption report" in markdown
    assert "high criticality gaps" in markdown
    assert "`missing.py`" in markdown


def test_cli_json_emits_report(capsys) -> None:
    exit_code = main(["--json"])

    assert exit_code == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["report_version"] == "magma.receipt_adoption_report.v0"
    assert parsed["target_count"] >= 1
    assert "entries" in parsed
