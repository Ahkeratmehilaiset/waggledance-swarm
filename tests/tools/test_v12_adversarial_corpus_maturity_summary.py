# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import copy
import json
from pathlib import Path
import subprocess
import sys

from tools.build_v12_adversarial_corpus_maturity_summary import (
    build_adversarial_corpus_maturity_summary,
    render_markdown,
)
from tools.validate_synthetic_adversarial_corpus import (
    DEFAULT_CORPUS,
    DEFAULT_EXPECTATIONS,
    validate_corpus,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "build_v12_adversarial_corpus_maturity_summary.py"
FIXED_NOW = datetime(2026, 6, 8, 4, 5, tzinfo=timezone.utc)


def _run_summary(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _source() -> dict:
    return validate_corpus(DEFAULT_CORPUS, DEFAULT_EXPECTATIONS)


def test_summary_reports_mature_adversarial_corpus_without_authority() -> None:
    report = build_adversarial_corpus_maturity_summary(now_utc=FIXED_NOW)

    assert report["report_version"] == (
        "wd.v12.adversarial_corpus_maturity_summary.v0"
    )
    assert report["ok"] is True
    assert report["blockers"] == []
    assert report["claim_label"] == "MEASURED_LOCAL_PARTIAL"
    assert report["source"]["corpus_redacted"] is True
    assert report["source"]["expectations_redacted"] is True
    maturity = report["maturity"]
    assert maturity["case_count"] == 59
    assert maturity["defect_type_count"] == 15
    assert maturity["held_out_case_count"] == 6
    assert maturity["privacy_canary_count"] == 45
    assert maturity["peer_review_trap_count"] == 57
    assert report["historical_expansion"]["fold_in_verified"] is True
    assert report["historical_expansion"]["case_count"] == 8
    assert report["maturation_targets"]
    authority = report["authority_boundary"]
    assert authority == {
        "read_only_summary": True,
        "corpus_mutation": False,
        "expectation_mutation": False,
        "runtime_authority": False,
        "promotion_authority": False,
        "scheduler_authority": False,
        "bridge_write_authority": False,
        "network_authority": False,
        "storage_write_authority": False,
        "solver_execution_authority": False,
        "external_writes_applied": False,
    }


def test_summary_fails_closed_when_source_validation_failed() -> None:
    source = _source()
    source["ok"] = False
    source["errors"] = ["synthetic fixture error"]

    report = build_adversarial_corpus_maturity_summary(
        now_utc=FIXED_NOW,
        corpus_report=source,
    )

    assert report["ok"] is False
    assert "source_validation_not_ok" in report["blockers"]
    assert "source_errors_present" in report["blockers"]


def test_summary_fails_closed_when_critical_floor_regresses() -> None:
    source = _source()
    source = copy.deepcopy(source)
    source["coverage"]["critical_defect_type_counts"]["path_escape"] = 1

    report = build_adversarial_corpus_maturity_summary(
        now_utc=FIXED_NOW,
        corpus_report=source,
    )

    assert report["ok"] is False
    assert "critical_defect_floor_missing:path_escape" in report["blockers"]


def test_summary_fails_closed_when_expansion_fold_in_breaks() -> None:
    expansion = _source()
    expansion = copy.deepcopy(expansion)
    expansion["expansion_summary"] = {
        "is_expansion": True,
        "folded_into_v0_claim": True,
        "fold_in": {
            "status": "fail",
            "missing_case_count": 1,
            "missing_expectation_count": 0,
        },
    }

    report = build_adversarial_corpus_maturity_summary(
        now_utc=FIXED_NOW,
        expansion_report=expansion,
    )

    assert report["ok"] is False
    assert "historical_expansion_fold_in_not_pass" in report["blockers"]
    assert "historical_expansion_missing_cases" in report["blockers"]


def test_markdown_carries_maturation_targets_and_authority_boundary() -> None:
    report = build_adversarial_corpus_maturity_summary(now_utc=FIXED_NOW)

    markdown = render_markdown(report)

    assert "# V12 Adversarial Corpus Maturity Summary" in markdown
    assert "cases: `59/50`" in markdown
    assert "historical expansion folded into v0: `true`" in markdown
    assert "corpus mutation: `false`" in markdown
    assert "runtime authority: `false`" in markdown
    assert "network authority: `false`" in markdown


def test_cli_json_smoke() -> None:
    completed = _run_summary("--now", "2026-06-08T04:05:00Z", "--json")

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["maturity"]["case_count"] == 59
    assert payload["authority_boundary"]["promotion_authority"] is False
