from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys

from tools.run_magma_adversarial_eval import build_adversarial_eval_report
from tools.verify_magma_receipt import verify_manifest
from waggledance.core.magma.canonical import sha256_digest
from waggledance.core.magma.adversarial_corpus_eval import REQUIRED_DEFECT_TYPES
from waggledance.core.magma.adversarial_gate import verify_adversarial_corpus_gate
from waggledance.core.magma.demo_policy import demo_policy_for_case

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "run_magma_adversarial_eval.py"
CORPUS = ROOT / "tests" / "fixtures" / "magma_adversarial_corpus" / "v0.json"
EXPECTATIONS = (
    ROOT / "tests" / "fixtures" / "magma_adversarial_corpus" / "v0_expectations.json"
)
EXPANSION = (
    ROOT
    / "tests"
    / "fixtures"
    / "magma_adversarial_corpus"
    / "v0_expansion_2026_05_23.json"
)
EXPANSION_EXPECTATIONS = (
    ROOT
    / "tests"
    / "fixtures"
    / "magma_adversarial_corpus"
    / "v0_expansion_2026_05_23_expectations.json"
)


def _run_eval(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _expected_case_count() -> int:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    return len(corpus["cases"])


def _different_gate(actual_gate: str) -> str:
    for candidate in ("allow", "refuse", "review", "require_approval"):
        if candidate != actual_gate:
            return candidate
    raise AssertionError(f"no alternate gate for {actual_gate!r}")


def _write_expectations_with_gate_mismatches(
    tmp_path: Path,
    *,
    defect_class: str,
    mismatch_count: int,
) -> tuple[Path, list[str]]:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    expectations = json.loads(EXPECTATIONS.read_text(encoding="utf-8"))
    cases_by_id = {case["case_id"]: case for case in corpus["cases"]}
    target_case_ids = [
        case["case_id"]
        for case in corpus["cases"]
        if case["defect_type"] == defect_class
    ][:mismatch_count]
    assert len(target_case_ids) == mismatch_count

    broken = copy.deepcopy(expectations)
    for expectation in broken["expectations"]:
        case_id = expectation["case_id"]
        if case_id not in target_case_ids:
            continue
        actual = demo_policy_for_case(cases_by_id[case_id])
        expectation["expected_gate"] = _different_gate(actual["actual_gate"])

    path = tmp_path / f"{defect_class}_mismatch_expectations.json"
    _write_json(path, broken)
    return path, target_case_ids


def test_scores_fixture_corpus_against_hidden_expectations() -> None:
    expected_case_count = _expected_case_count()
    report = build_adversarial_eval_report()

    assert report["eval_version"] == "magma.adversarial_eval.v1"
    assert report["writes_applied"] is False
    assert report["ok"] is True
    assert report["case_count"] == expected_case_count
    assert report["pass_count"] == expected_case_count
    assert report["fail_count"] == 0
    assert report["gate_accuracy"] == 1.0
    assert report["verdict_accuracy"] == 1.0
    assert report["reason_code_accuracy"] == 1.0
    assert all(
        case["defect_class"] in REQUIRED_DEFECT_TYPES for case in report["cases"]
    )
    assert report["full_match_count"] == expected_case_count
    assert report["partial_match_count"] == 0
    assert report["mismatch_count"] == 0
    assert report["coverage"]["evaluation_result_case_count"] >= 3
    assert report["coverage"]["receipt_binding_case_count"] >= 2
    assert report["coverage"]["counterfactual_case_count"] >= 3
    assert report["coverage"]["operator_gate_case_count"] >= 2
    assert report["coverage"]["clean_baseline_case_count"] >= 2
    assert report["per_case_coverage"]["min_critical_defect_cases"] == 2
    critical_caught = report["per_case_coverage"]["critical_defect_type_caught_counts"]
    assert critical_caught["governance_bypass"] >= 2
    assert critical_caught["path_escape"] >= 2
    assert report["per_case_coverage"]["critical_defect_types_below_floor"] == {}
    assert report["corpus_digest"].startswith("sha256:")
    assert report["expectations_digest"].startswith("sha256:")
    assert report["catch_agent_bucket_status"] == "redacted_hidden_expectations_v0"
    assert "failure_buckets" not in report
    assert "receipt_bundle" not in report


def test_report_can_feed_gate_when_bound_to_solver() -> None:
    solver_hash = "solver-artifact-hash"
    report = build_adversarial_eval_report(bound_solver_hash=solver_hash)

    gate = verify_adversarial_corpus_gate(
        report=report,
        expected_solver_hash=solver_hash,
        min_cases=len(REQUIRED_DEFECT_TYPES),
    )

    assert report["bound_solver_hash"] == solver_hash
    assert gate.ok is True
    assert gate.reasons == ()


def test_gate_rederives_critical_floor_from_real_per_case_results(
    tmp_path: Path,
) -> None:
    solver_hash = "solver-artifact-hash"
    for defect_class, mismatch_count in (
        ("path_escape", 1),
        ("governance_bypass", 2),
    ):
        expectations_path, target_case_ids = _write_expectations_with_gate_mismatches(
            tmp_path,
            defect_class=defect_class,
            mismatch_count=mismatch_count,
        )

        report = build_adversarial_eval_report(
            corpus_path=CORPUS,
            expectations_path=expectations_path,
            bound_solver_hash=solver_hash,
        )
        gate = verify_adversarial_corpus_gate(
            report=report,
            expected_solver_hash=solver_hash,
            min_cases=len(REQUIRED_DEFECT_TYPES),
        )

        assert report["ok"] is False
        assert report["fail_count"] == mismatch_count
        assert {case["case_id"] for case in report["failures"]} == set(target_case_ids)
        assert (
            report["per_case_coverage"]["critical_defect_types_below_floor"][
                defect_class
            ]
            == 1
        )
        assert gate.ok is False
        assert gate.not_caught_count == mismatch_count
        assert any(
            f"{defect_class}=1" in reason
            and "critical defect classes below caught floor" in reason
            for reason in gate.reasons
        )


def test_scores_phase_d_folded_expansion_provenance() -> None:
    expansion_fixture = json.loads(EXPANSION.read_text(encoding="utf-8"))
    assert expansion_fixture["expansion_status"] == "folded_into_v0"

    report = build_adversarial_eval_report(
        corpus_path=EXPANSION,
        expectations_path=EXPANSION_EXPECTATIONS,
    )

    assert report["ok"] is True
    assert report["case_count"] == 8
    assert report["pass_count"] == 8
    assert report["fail_count"] == 0
    assert report["gate_accuracy"] == 1.0
    assert report["verdict_accuracy"] == 1.0
    assert report["reason_code_accuracy"] == 1.0
    assert report["coverage"]["clean_baseline_case_count"] == 1


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
    assert "claude_only" not in combined
    assert "codex_only" not in combined
    assert "both" not in combined
    assert "neither" not in combined
    assert "should_claude_catch" not in combined
    assert "should_codex_catch" not in combined
    assert "v0_expectations" not in combined


def test_cli_text_mode_and_out_report(tmp_path: Path) -> None:
    expected_case_count = _expected_case_count()
    out = tmp_path / "reports" / "eval.json"
    out.parent.mkdir()

    result = _run_eval("--out", str(out))

    assert result.returncode == 0, result.stderr
    assert (
        f"magma adversarial eval OK: {expected_case_count}/{expected_case_count} cases passed"
        in result.stdout
    )
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["ok"] is True
    assert report["case_count"] == expected_case_count


def test_opt_in_receipt_bundle_verifies_report(tmp_path: Path) -> None:
    expected_case_count = _expected_case_count()
    out_dir = tmp_path / "adversarial-receipts"

    report = build_adversarial_eval_report(
        receipt_out_dir=out_dir,
        now_utc=_fixed_now(),
    )

    bundle = report["receipt_bundle"]
    assert report["writes_applied"] is False
    assert bundle["receipt_count"] == 1
    assert bundle["verifier_report"]["ok"] is True
    assert verify_manifest(out_dir / "manifest.json")["ok"] is True

    payload = json.loads(
        (out_dir / "payload-001-report.json").read_text(encoding="utf-8")
    )
    evaluation = json.loads(
        (out_dir / "evaluation-001-report.json").read_text(encoding="utf-8")
    )
    receipt = json.loads(
        (out_dir / "receipt-001-report.json").read_text(encoding="utf-8")
    )
    assert receipt["ts_utc"] == "2026-05-20T18:20:00Z"
    assert receipt["risk_class"] == "local_artifact"
    assert receipt["operator_gate_required"] is False
    assert receipt["approval_id"] is None
    assert evaluation["target_digest"] == sha256_digest(payload)
    assert receipt["canonical_payload_digest"] == sha256_digest(payload)
    assert receipt["evaluation_result_digest"] == sha256_digest(evaluation)
    assert payload["case_count"] == expected_case_count
    assert payload["coverage"] == report["coverage"]
    assert payload["per_case_coverage"] == report["per_case_coverage"]
    assert len(payload["case_evaluation_result_digests"]) == expected_case_count


def test_cli_emits_receipt_bundle_only_when_requested(tmp_path: Path) -> None:
    out_dir = tmp_path / "adversarial-receipts"

    result = _run_eval(
        "--json",
        "--receipt-out-dir",
        str(out_dir),
        "--now",
        "2026-05-20T18:20:00Z",
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["receipt_bundle"]["verifier_report"]["ok"] is True
    assert (out_dir / "manifest.json").exists()
    receipt = json.loads(
        (out_dir / "receipt-001-report.json").read_text(encoding="utf-8")
    )
    assert receipt["ts_utc"] == "2026-05-20T18:20:00Z"


def test_receipt_bundle_refuses_existing_output_directory(tmp_path: Path) -> None:
    out_dir = tmp_path / "adversarial-receipts"
    out_dir.mkdir()

    result = _run_eval("--receipt-out-dir", str(out_dir))

    assert result.returncode == 1
    assert "out_dir must not exist" in result.stderr


def test_receipt_bundle_does_not_leak_hidden_expectations(tmp_path: Path) -> None:
    out_dir = tmp_path / "adversarial-receipts"

    result = _run_eval("--json", "--receipt-out-dir", str(out_dir))

    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr
    for path in out_dir.glob("*.json"):
        combined += path.read_text(encoding="utf-8")
    assert "_DO_NOT_LEAK" not in combined
    assert "peer_review_trap_marker" not in combined
    assert "should_claude_catch" not in combined
    assert "should_codex_catch" not in combined
    assert "expected_verdict" not in combined


def test_cli_rejects_non_utc_receipt_timestamp(tmp_path: Path) -> None:
    result = _run_eval(
        "--json",
        "--receipt-out-dir",
        str(tmp_path / "adversarial-receipts"),
        "--now",
        "2026-05-20T21:20:00+03:00",
    )

    assert result.returncode == 1
    assert "--now requires a UTC timestamp" in result.stderr


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
    assert "failure_buckets" not in report
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

    result = _run_eval(
        "--corpus", str(corpus_path), "--expectations", str(EXPECTATIONS)
    )

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


def _fixed_now():
    from datetime import datetime, timezone

    return datetime(2026, 5, 20, 18, 20, tzinfo=timezone.utc)
