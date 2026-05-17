from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys

from tools.run_magma_adversarial_eval import build_adversarial_eval_report


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "run_magma_adversarial_eval.py"
CORPUS = ROOT / "tests" / "fixtures" / "magma_adversarial_corpus" / "v0.json"
EXPECTATIONS = ROOT / "tests" / "fixtures" / "magma_adversarial_corpus" / "v0_expectations.json"


def _run_eval(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_scores_fixture_corpus_against_hidden_expectations() -> None:
    report = build_adversarial_eval_report()

    assert report["eval_version"] == "magma.adversarial_eval.v0"
    assert report["writes_applied"] is False
    assert report["ok"] is True
    assert report["case_count"] == 9
    assert report["pass_count"] == 9
    assert report["fail_count"] == 0
    assert report["gate_accuracy"] == 1.0
    assert report["verdict_accuracy"] == 1.0
    assert report["reason_code_recall"] == 1.0
    assert report["corpus_digest"].startswith("sha256:")
    assert report["expectations_digest"].startswith("sha256:")
    assert report["failure_buckets"] == {
        "both": 0,
        "claude_only": 0,
        "codex_only": 0,
        "neither": 0,
    }


def test_external_effect_cases_are_scored_without_writes() -> None:
    report = build_adversarial_eval_report()

    external_cases = [
        case for case in report["cases"] if case["risk_class"] == "external_effect"
    ]
    assert len(external_cases) >= 1
    assert report["writes_applied"] is False
    for case in external_cases:
        assert case["operator_required"] is True
        assert case["actual_gate"] in {"refuse", "review", "require_approval"}


def test_report_does_not_leak_privacy_canaries_or_review_traps() -> None:
    result = _run_eval("--json")

    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr
    assert "_DO_NOT_LEAK" not in combined
    assert "peer_review_trap_marker" not in combined
    assert "approval_wording_trap" not in combined
    assert "hidden_write_intent" not in combined
    assert "should_claude_catch" not in combined
    assert "should_codex_catch" not in combined
    assert "v0_expectations" not in combined


def test_cli_text_mode_and_out_report(tmp_path: Path) -> None:
    out = tmp_path / "reports" / "eval.json"

    result = _run_eval("--out", str(out))

    assert result.returncode == 0, result.stderr
    assert "magma adversarial eval OK: 9/9 cases passed" in result.stdout
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["ok"] is True
    assert report["case_count"] == 9


def test_cli_refuses_to_overwrite_out_report(tmp_path: Path) -> None:
    out = tmp_path / "eval.json"
    out.write_text("{}\n", encoding="utf-8")

    result = _run_eval("--out", str(out))

    assert result.returncode == 1
    assert "out report already exists" in result.stderr


def test_mismatched_expectation_reports_failure_and_bucket(tmp_path: Path) -> None:
    expectations = json.loads(EXPECTATIONS.read_text(encoding="utf-8"))
    broken = copy.deepcopy(expectations)
    broken["expectations"][0]["expected_gate"] = "allow"
    path = tmp_path / "broken_expectations.json"
    _write_json(path, broken)

    report = build_adversarial_eval_report(
        corpus_path=CORPUS,
        expectations_path=path,
    )

    assert report["ok"] is False
    assert report["fail_count"] == 1
    assert report["failure_buckets"]["both"] == 1
    failure = report["failures"][0]
    assert failure["case_id"] == "case:adv:charter_violation:001"
    assert failure["expected_gate"] == "allow"
    assert failure["actual_gate"] == "refuse"


def test_passing_cases_do_not_dump_expected_values() -> None:
    report = build_adversarial_eval_report()

    assert report["failures"] == []
    for case in report["cases"]:
        assert "expected_gate" not in case
        assert "expected_verdict" not in case
        assert "missing_reason_codes" not in case
