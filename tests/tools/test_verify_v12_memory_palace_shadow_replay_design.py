# SPDX-License-Identifier: BUSL-1.1
"""Unit tests for tools/verify_v12_memory_palace_shadow_replay_design.py."""
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

from tools.run_v12_memory_palace_shadow_replay_design import (
    build_memory_palace_shadow_replay_design,
)
from tools.verify_v12_memory_palace_shadow_replay_design import (
    VERIFICATION_VERSION,
    main,
    verify_memory_palace_shadow_replay_design,
)
from waggledance.core.magma.canonical import sha256_digest

FIXED_NOW = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)


def _design() -> dict:
    return build_memory_palace_shadow_replay_design(now_utc=FIXED_NOW)


def _retamper_digest(report: dict) -> dict:
    core = {k: v for k, v in report.items() if k != "canonical_digest"}
    report["canonical_digest"] = sha256_digest(core)
    return report


# --- genuine report verifies --------------------------------------------------


def test_genuine_design_verifies():
    v = verify_memory_palace_shadow_replay_design(_design())
    assert v["ok"] is True
    assert v["blockers"] == []
    assert v["verification_version"] == VERIFICATION_VERSION
    assert v["canonical_digest_rederived"] is True
    assert v["source_report_version_check"] == "match"
    assert v["shadow_replay_design_count_checked"] >= 1
    # the verification artifact is itself action-free
    assert v["runtime_route_changed"] is False
    assert v["runtime_authority_granted"] is False


# --- tampers fail closed ------------------------------------------------------


def test_stale_digest_after_count_tamper_refused():
    bad = _design()
    bad["shadow_replay_designs"][0]["hop_reduction"] = 999  # digest now stale
    v = verify_memory_palace_shadow_replay_design(bad)
    assert v["ok"] is False
    assert "canonical_digest_mismatch" in v["blockers"]


def test_self_consistent_hop_reduction_tamper_refused_by_arithmetic():
    # Recompute the digest after tampering -> digest passes, arithmetic refuses.
    bad = _design()
    bad["shadow_replay_designs"][0]["hop_reduction"] = 999
    _retamper_digest(bad)
    v = verify_memory_palace_shadow_replay_design(bad)
    assert v["ok"] is False
    assert any(
        b.startswith("replay_row_hop_reduction_mismatch") for b in v["blockers"]
    )


def test_report_authority_flag_flip_refused():
    bad = _design()
    bad["runtime_route_changed"] = True
    _retamper_digest(bad)
    v = verify_memory_palace_shadow_replay_design(bad)
    assert v["ok"] is False
    assert "report_authority_not_false:runtime_route_changed" in v["blockers"]


def test_row_authority_flag_flip_refused():
    bad = _design()
    bad["shadow_replay_designs"][0]["shadow_replay_executed"] = True
    _retamper_digest(bad)
    v = verify_memory_palace_shadow_replay_design(bad)
    assert v["ok"] is False
    assert any(
        b.startswith("replay_row_authority_not_false") and "shadow_replay_executed" in b
        for b in v["blockers"]
    )


def test_guardrail_flip_refused():
    bad = _design()
    bad["no_overclaim_guardrails"]["shadow_replay_not_executed"] = False
    _retamper_digest(bad)
    v = verify_memory_palace_shadow_replay_design(bad)
    assert v["ok"] is False
    assert "guardrail_not_true:shadow_replay_not_executed" in v["blockers"]


def test_wrong_version_refused():
    bad = _design()
    bad["report_version"] = "evil.v9"
    _retamper_digest(bad)
    v = verify_memory_palace_shadow_replay_design(bad)
    assert v["ok"] is False
    assert "source_report_version_mismatch" in v["blockers"]


def test_replay_status_tamper_refused():
    bad = _design()
    bad["shadow_replay_designs"][0]["replay_status"] = "executed"
    _retamper_digest(bad)
    v = verify_memory_palace_shadow_replay_design(bad)
    assert v["ok"] is False
    assert any(b.startswith("replay_row_status_not_design_only") for b in v["blockers"])


def test_no_rows_refused():
    bad = _design()
    bad["shadow_replay_designs"] = []
    _retamper_digest(bad)
    v = verify_memory_palace_shadow_replay_design(bad)
    assert v["ok"] is False
    assert "no_shadow_replay_rows" in v["blockers"]


def test_non_object_refused():
    v = verify_memory_palace_shadow_replay_design  # type: ignore[assignment]
    import pytest

    with pytest.raises(Exception):
        v("not-a-dict")  # type: ignore[arg-type]


# --- CLI ----------------------------------------------------------------------


def _write(tmp_path: Path, report: dict, name: str = "design.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(report), encoding="utf-8")
    return p


def test_main_verified_exit_0(tmp_path, capsys):
    p = _write(tmp_path, _design())
    rc = main(["--report-json", str(p), "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True


def test_main_tampered_exit_1(tmp_path):
    bad = _design()
    bad["shadow_replay_designs"][0]["hop_reduction"] = 999
    p = _write(tmp_path, bad, "bad.json")
    assert main(["--report-json", str(p)]) == 1


def test_main_missing_file_exit_1(tmp_path):
    assert main(["--report-json", str(tmp_path / "absent.json")]) == 1


def test_main_invalid_json_exit_1(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    assert main(["--report-json", str(p)]) == 1
