# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _load_module():
    path = ROOT / ".orchestrator" / "no_human_prompt_lint.py"
    spec = importlib.util.spec_from_file_location("eig2_no_human_prompt_lint", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_forbidden_phrase_detected_in_proposal(tmp_path: Path) -> None:
    mod = _load_module()
    proposal = tmp_path / "proposal.md"
    proposal.write_text("Deployment cannot continue without approval.\n")
    findings = mod.scan_file(proposal)
    assert len(findings) == 1
    assert findings[0]["phrase"] == "cannot continue without approval"


def test_allowed_phrase_in_event_name_not_flagged(tmp_path: Path) -> None:
    mod = _load_module()
    event = tmp_path / "event.json"
    event.write_text('{"type": "human_review_required", "status": "open"}\n')
    assert mod.scan_file(event) == []


def test_allowed_phrase_in_operator_runbook_not_flagged(tmp_path: Path) -> None:
    mod = _load_module()
    runbook = tmp_path / "operator_runbook.md"
    runbook.write_text(
        "Operator runbook: wait for operator only during external recovery.\n"
    )
    assert mod.scan_file(runbook) == []


def test_lint_returns_nonzero_exit_when_findings_present(tmp_path: Path, capsys) -> None:
    mod = _load_module()
    proposal = tmp_path / "generated_proposal.md"
    proposal.write_text("This step should ask user for approval.\n")
    exit_code = mod.main([str(proposal)])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert '"total_findings": 1' in captured.out


def test_lint_source_file_does_not_flag_its_own_phrase_table() -> None:
    mod = _load_module()
    source = ROOT / ".orchestrator" / "no_human_prompt_lint.py"
    assert mod.scan_file(source) == []
