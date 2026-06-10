# SPDX-License-Identifier: BUSL-1.1
"""Unit tests for tools/run_v12_memory_palace_shadow_replay_design.py (S6)."""
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

from tools.run_v12_memory_palace_shadow_replay_design import (
    REPORT_VERSION,
    _AUTHORITY_FALSE_FIELDS,
    build_memory_palace_shadow_replay_design,
    main,
    render_markdown,
)
from tools.run_v12_memory_palace_shortcut_runtime_promotion_design import (
    build_memory_palace_shortcut_runtime_promotion_design,
)
from waggledance.core.magma.canonical import sha256_digest

FIXED_NOW = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)


def _runtime_design() -> dict:
    return build_memory_palace_shortcut_runtime_promotion_design(now_utc=FIXED_NOW)


# --- happy path ----------------------------------------------------------------


def test_shadow_replay_design_derives_rows_from_runtime_design():
    report = build_memory_palace_shadow_replay_design(now_utc=FIXED_NOW)
    assert report["report_version"] == REPORT_VERSION
    assert report["ok"] is True
    assert report["blockers"] == []
    assert report["claim_label"] == "DESIGN_ONLY_OPERATOR_GATED_SHADOW_REPLAY"
    rows = report["shadow_replay_designs"]
    assert len(rows) == report["design_summary"]["source_design_count"]
    assert rows, "expected at least one designable runtime-promotion row"
    row = rows[0]
    assert row["incumbent_route"]["kind"] == "full_hierarchy_path"
    assert row["candidate_route"]["kind"] == "shortcut_path"
    assert row["candidate_route"]["hop_count"] < row["incumbent_route"]["hop_count"]
    assert row["hop_reduction"] == (
        row["incumbent_route"]["hop_count"] - row["candidate_route"]["hop_count"]
    )
    assert row["agreement_criterion"] == "both_routes_resolve_to_same_target_node"
    assert row["replay_status"] == "design_only_not_executed"
    assert row["operator_gate_required"] is True


def test_authority_boundary_all_false_on_report_and_rows():
    report = build_memory_palace_shadow_replay_design(now_utc=FIXED_NOW)
    for field in _AUTHORITY_FALSE_FIELDS:
        assert report[field] is False, field
    for row in report["shadow_replay_designs"]:
        for field in _AUTHORITY_FALSE_FIELDS:
            assert row[field] is False, (row["shadow_replay_id"], field)


def test_digest_rederives_and_deterministic():
    one = build_memory_palace_shadow_replay_design(now_utc=FIXED_NOW)
    two = build_memory_palace_shadow_replay_design(now_utc=FIXED_NOW)
    assert one == two
    core = {k: v for k, v in one.items() if k != "canonical_digest"}
    assert one["canonical_digest"] == sha256_digest(core)


def test_pass_criteria_and_operator_controls_present():
    report = build_memory_palace_shadow_replay_design(now_utc=FIXED_NOW)
    assert "shortcut_route_resolves_to_same_target_node" in report["replay_pass_criteria"]
    assert "shortcut_hop_count_strictly_less_than_incumbent" in report["replay_pass_criteria"]
    assert "operator_authorization" in report["required_operator_controls"]
    assert "rollback_plan" in report["required_operator_controls"]
    assert report["no_overclaim_guardrails"]["shadow_replay_not_executed"] is True


# --- fail-closed on bad source -------------------------------------------------


def test_refuses_when_source_not_ok():
    bad = copy.deepcopy(_runtime_design())
    bad["ok"] = False
    report = build_memory_palace_shadow_replay_design(
        now_utc=FIXED_NOW, runtime_design=bad
    )
    assert report["ok"] is False
    assert "source_runtime_design_not_ok" in report["blockers"]
    assert report["shadow_replay_designs"] == []


def test_refuses_on_wrong_source_version():
    bad = copy.deepcopy(_runtime_design())
    bad["report_version"] = "evil.v9"
    report = build_memory_palace_shadow_replay_design(
        now_utc=FIXED_NOW, runtime_design=bad
    )
    assert report["ok"] is False
    assert "source_runtime_design_version_mismatch" in report["blockers"]


def test_refuses_on_dirty_source_authority_boundary():
    bad = copy.deepcopy(_runtime_design())
    bad["authority_boundary"]["runtime_route_changed"] = True
    report = build_memory_palace_shadow_replay_design(
        now_utc=FIXED_NOW, runtime_design=bad
    )
    assert report["ok"] is False
    assert "source_authority_boundary_not_clean" in report["blockers"]


def test_refuses_when_no_designable_rows():
    bad = copy.deepcopy(_runtime_design())
    bad["runtime_promotion_designs"] = []
    report = build_memory_palace_shadow_replay_design(
        now_utc=FIXED_NOW, runtime_design=bad
    )
    assert report["ok"] is False
    assert "no_designable_runtime_promotion_rows" in report["blockers"]


# --- CLI -----------------------------------------------------------------------


def test_main_json_exit_0(capsys):
    rc = main(["--now", "2026-06-10T12:00:00Z", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["report_version"] == REPORT_VERSION


def test_main_out_matches_stdout(tmp_path, capsys):
    out = tmp_path / "design.json"
    rc = main(["--now", "2026-06-10T12:00:00Z", "--json", "--out", str(out)])
    assert rc == 0
    stdout = json.loads(capsys.readouterr().out)
    assert stdout == json.loads(out.read_text(encoding="utf-8"))


def test_main_bad_now_exit_1():
    assert main(["--now", "junk"]) == 1


def test_markdown_renders_rows_and_design_only_note():
    report = build_memory_palace_shadow_replay_design(now_utc=FIXED_NOW)
    md = render_markdown(report)
    assert "V12 Memory Palace Shortcut Shadow-Replay Design" in md
    assert "design_only" in md.lower() or "Design-only" in md
    assert "hop_reduction" in md
