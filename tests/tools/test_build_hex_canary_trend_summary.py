# SPDX-License-Identifier: BUSL-1.1
"""Unit tests for tools/build_hex_canary_trend_summary.py."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools.build_hex_canary_trend_summary import (
    CLAIM_GATES,
    build_trend_summary,
    main,
)
from tools.run_hex_canary_mirror_proof import build_canary_mirror_proof
from waggledance.core.magma.canonical import sha256_digest


def _decision(production_cell_id=None, **ov) -> dict:
    base = {
        "query": "calculate the heating formula",
        "intent": "math",
        "production_capability_id": "cap.math.formula",
        "quality_path": "silver",
    }
    if production_cell_id is not None:
        base["production_cell_id"] = production_cell_id
    base.update(ov)
    return base


def _proof(*, decisions, when: str) -> dict:
    return build_canary_mirror_proof(
        decisions=decisions,
        source_label=when,
        now=datetime.fromisoformat(when.replace("Z", "+00:00")),
    )


# Three runs with rising agreement: 0/2, 1/2, 2/2.
def _low() -> dict:
    return _proof(
        decisions=[_decision(production_cell_id="general"), _decision(production_cell_id="general")],
        when="2026-06-08T00:00:00Z",
    )


def _mid() -> dict:
    return _proof(
        decisions=[_decision(production_cell_id="math"), _decision(production_cell_id="general")],
        when="2026-06-09T00:00:00Z",
    )


def _high() -> dict:
    return _proof(
        decisions=[_decision(production_cell_id="math"), _decision(production_cell_id="math")],
        when="2026-06-10T00:00:00Z",
    )


# --- trend direction ----------------------------------------------------------


def test_improving_trend_ordered_by_timestamp():
    # Pass out of chronological order; the tool must sort by generated_at_utc.
    summary = build_trend_summary(artifacts=[_high(), _low(), _mid()])
    assert summary["run_count"] == 3
    assert summary["agreement_rate_series"] == [0.0, 0.5, 1.0]
    assert summary["first_agreement_rate"] == 0.0
    assert summary["last_agreement_rate"] == 1.0
    assert summary["agreement_rate_delta"] == 1.0
    assert summary["trend_direction"] == "improving"
    assert summary["mean_agreement_rate"] == 0.5
    assert summary["min_agreement_rate"] == 0.0
    assert summary["max_agreement_rate"] == 1.0
    assert summary["total_samples"] == 6


def test_degrading_trend():
    # High agreement earlier, low agreement later -> degrading after sort.
    early_high = _proof(
        decisions=[_decision(production_cell_id="math"), _decision(production_cell_id="math")],
        when="2026-06-08T00:00:00Z",
    )
    late_low = _proof(
        decisions=[_decision(production_cell_id="general"), _decision(production_cell_id="general")],
        when="2026-06-10T00:00:00Z",
    )
    summary = build_trend_summary(artifacts=[early_high, late_low])
    assert summary["agreement_rate_series"] == [1.0, 0.0]
    assert summary["trend_direction"] == "degrading"
    assert summary["agreement_rate_delta"] == -1.0


def test_stable_trend_within_epsilon():
    summary = build_trend_summary(artifacts=[_mid(), _mid()], epsilon=0.01)
    assert summary["trend_direction"] == "stable"
    assert summary["agreement_rate_delta"] == 0.0


def test_classification_shift_first_vs_last():
    summary = build_trend_summary(artifacts=[_low(), _high()])
    shift = summary["classification_shift"]
    # low: both divergent_production_cell; high: both match_production_cell
    assert shift["match_production_cell"]["first"] == 0
    assert shift["match_production_cell"]["last"] == 2
    assert shift["match_production_cell"]["delta"] == 2
    assert shift["divergent_production_cell"]["first"] == 2
    assert shift["divergent_production_cell"]["last"] == 0
    assert shift["divergent_production_cell"]["delta"] == -2
    assert set(shift) == set(summary["classification_shift"])


def test_single_run_is_stable():
    summary = build_trend_summary(artifacts=[_mid()])
    assert summary["run_count"] == 1
    assert summary["trend_direction"] == "stable"
    assert summary["first_agreement_rate"] == summary["last_agreement_rate"]


# --- contract -----------------------------------------------------------------


def test_digest_rederives_and_gates_false():
    summary = build_trend_summary(artifacts=[_low(), _high()])
    core = {k: v for k, v in summary.items() if k != "canonical_digest"}
    assert summary["canonical_digest"] == sha256_digest(core)
    for gate in CLAIM_GATES:
        assert summary[gate] is False
    assert summary["advisory_only"] is True
    assert summary["read_only"] is True


def test_deterministic():
    one = build_trend_summary(artifacts=[_low(), _mid(), _high()])
    two = build_trend_summary(artifacts=[_low(), _mid(), _high()])
    assert one == two


# --- fail-closed verification -------------------------------------------------


def test_empty_set_refused():
    with pytest.raises(ValueError, match="at least one"):
        build_trend_summary(artifacts=[])


def test_forged_artifact_refused():
    bad = _high()
    bad["mirror_report"]["agreement_count"] = 99  # digest no longer matches
    with pytest.raises(ValueError, match="failed verification"):
        build_trend_summary(artifacts=[_low(), bad])


def test_wrong_report_version_refused():
    bad = _high()
    # keep it internally consistent so the verifier passes, but wrong top version
    bad["report_version"] = "evil.v9"
    with pytest.raises(ValueError):
        build_trend_summary(artifacts=[bad])


def test_missing_generated_at_refused():
    bad = _high()
    del bad["generated_at_utc"]
    with pytest.raises(ValueError):
        build_trend_summary(artifacts=[bad])


# --- CLI ----------------------------------------------------------------------


def _write(tmp_path: Path, artifact: dict, name: str) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(artifact), encoding="utf-8")
    return p


def test_main_artifacts_json(tmp_path, capsys):
    a = _write(tmp_path, _low(), "a.json")
    b = _write(tmp_path, _high(), "b.json")
    rc = main(["--artifact", str(a), "--artifact", str(b), "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["trend_direction"] == "improving"
    assert out["run_count"] == 2


def test_main_dir_mode(tmp_path, capsys):
    _write(tmp_path, _low(), "01.json")
    _write(tmp_path, _mid(), "02.json")
    _write(tmp_path, _high(), "03.json")
    rc = main(["--dir", str(tmp_path), "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["run_count"] == 3
    assert out["agreement_rate_series"] == [0.0, 0.5, 1.0]


def test_main_out_matches_stdout(tmp_path, capsys):
    a = _write(tmp_path, _low(), "a.json")
    out_path = tmp_path / "trend.json"
    rc = main(["--artifact", str(a), "--json", "--out", str(out_path)])
    assert rc == 0
    stdout = json.loads(capsys.readouterr().out)
    assert stdout == json.loads(out_path.read_text(encoding="utf-8"))


def test_main_fail_under(tmp_path, capsys):
    a = _write(tmp_path, _low(), "a.json")  # last rate 0.0
    rc = main(["--artifact", str(a), "--fail-under", "0.5", "--json"])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["below_fail_under"] is True


def test_main_fail_under_met(tmp_path):
    a = _write(tmp_path, _high(), "a.json")  # last rate 1.0
    rc = main(["--artifact", str(a), "--fail-under", "0.5"])
    assert rc == 0


def test_main_missing_file_exit_3(tmp_path):
    assert main(["--artifact", str(tmp_path / "absent.json")]) == 3


def test_main_unreadable_exit_2(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    assert main(["--artifact", str(p)]) == 2


def test_main_forged_exit_2(tmp_path):
    bad = _high()
    bad["mirror_report"]["sample_count"] = 999
    p = _write(tmp_path, bad, "bad.json")
    assert main(["--artifact", str(p)]) == 2


def test_main_bad_args_exit_2(tmp_path):
    a = _write(tmp_path, _high(), "a.json")
    assert main(["--artifact", str(a), "--epsilon", "2"]) == 2
    assert main(["--artifact", str(a), "--fail-under", "-0.1"]) == 2


def test_main_empty_dir_exit_2(tmp_path):
    (tmp_path / "sub").mkdir()
    assert main(["--dir", str(tmp_path / "sub")]) == 2
