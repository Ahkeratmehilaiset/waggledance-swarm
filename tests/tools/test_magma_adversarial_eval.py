from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys

from tools.run_magma_adversarial_eval import build_adversarial_eval_report
from waggledance.core.magma.demo_policy import demo_policy_for_case


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
    assert report["reason_code_accuracy"] == 1.0
    assert report["full_match_count"] == 9
    assert report["partial_match_count"] == 0
    assert report["mismatch_count"] == 0
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
        assert case["status"] in {"full_match", "partial_match", "mismatch"}
        assert "actual_gate" not in case


def test_report_does_not_leak_privacy_canaries_or_review_traps(tmp_path: Path) -> None:
    out = tmp_path / "eval.json"

    result = _run_eval("--json", "--out", str(out))

    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr + out.read_text(encoding="utf-8")
    assert "_DO_NOT_LEAK" not in combined
    assert "peer_review_trap_marker" not in combined
    assert "ambiguous_allow_language" not in combined
    assert "approval_wording_trap" not in combined
    assert "authority_confusion" not in combined
    assert "digest_without_payload" not in combined
    assert "hidden_write_intent" not in combined
    assert "privacy_redaction_trap" not in combined
    assert "state_window_blindspot" not in combined
    assert "should_claude_catch" not in combined
    assert "should_codex_catch" not in combined
    assert "v0_expectations" not in combined


def test_cli_text_mode_and_out_report(tmp_path: Path) -> None:
    out = tmp_path / "reports" / "eval.json"
    out.parent.mkdir()

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


def test_cli_refuses_missing_out_parent(tmp_path: Path) -> None:
    out = tmp_path / "missing" / "eval.json"

    result = _run_eval("--out", str(out))

    assert result.returncode == 1
    assert "out report parent does not exist" in result.stderr


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
    assert failure["status"] == "mismatch"
    assert failure["gate_mismatch"] is True
    assert failure["verdict_mismatch"] is False
    assert failure["reason_codes_mismatch"] is False
    assert "expected_gate" not in failure
    assert "expected_verdict" not in failure
    assert "actual_gate" not in failure
    assert "actual_verdict" not in failure
    assert "missing_reason_codes" not in failure


def test_passing_cases_do_not_dump_expected_values() -> None:
    report = build_adversarial_eval_report()

    assert report["failures"] == []
    for case in report["cases"]:
        assert "expected_gate" not in case
        assert "expected_verdict" not in case
        assert "actual_gate" not in case
        assert "actual_verdict" not in case
        assert "missing_reason_codes" not in case


def test_validation_errors_are_redacted_on_cli_error(tmp_path: Path) -> None:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    broken = copy.deepcopy(corpus)
    broken["cases"][1]["privacy_canary"] = broken["cases"][0]["privacy_canary"]
    corpus_path = tmp_path / "broken_corpus.json"
    _write_json(corpus_path, broken)

    result = _run_eval("--corpus", str(corpus_path), "--expectations", str(EXPECTATIONS))

    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert "corpus validation failed" in result.stderr
    assert "_DO_NOT_LEAK" not in combined
    assert broken["cases"][0]["privacy_canary"] not in combined
    assert "duplicate privacy_canary" not in combined


def test_demo_policy_is_visible_field_derived_not_case_id_keyed() -> None:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    first = corpus["cases"][0]
    renamed = copy.deepcopy(first)
    renamed["case_id"] = "case:adv:charter_violation:renamed"

    assert demo_policy_for_case(renamed) == demo_policy_for_case(first)
