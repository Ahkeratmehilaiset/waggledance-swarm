# SPDX-License-Identifier: BUSL-1.1
"""Unit tests for tools/build_hex_canary_coverage_summary.py."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools.build_hex_canary_coverage_summary import (
    CLAIM_GATES,
    build_coverage_summary,
    main,
)
from tools.run_hex_canary_mirror_proof import (
    DEMO_DECISIONS,
    build_canary_mirror_proof,
)
from waggledance.core.hex_topology.canary_mirror import CANARY_CLASSIFICATIONS
from waggledance.core.magma.canonical import sha256_digest

NOW_TEXT = "2026-06-10T12:00:00Z"
NOW = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)


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


def _demo_artifact() -> dict:
    return build_canary_mirror_proof(
        decisions=[dict(d) for d in DEMO_DECISIONS], source_label="demo", now=NOW
    )


# --- coverage roll-up ---------------------------------------------------------


def test_demo_exercises_all_four_classifications():
    art = _demo_artifact()
    summary = build_coverage_summary(artifact=art, now=NOW, intent_count=2)
    c = summary["coverage"]
    assert c["classifications_total"] == 4
    assert sorted(c["classifications_exercised"]) == sorted(CANARY_CLASSIFICATIONS)
    assert c["classifications_exercised_count"] == 4
    assert c["classifications_missing"] == []
    assert c["all_classifications_exercised"] is True
    assert c["sample_count"] == 4
    assert summary["ok"] is True


def test_missing_classifications_are_listed_and_targeted():
    # All-agreement, single classification only.
    art = build_canary_mirror_proof(
        decisions=[_decision(production_cell_id="math"), _decision(production_cell_id="math")],
        source_label="x",
        now=NOW,
    )
    summary = build_coverage_summary(artifact=art, now=NOW, intent_count=1)
    c = summary["coverage"]
    assert c["classifications_exercised"] == ["match_production_cell"]
    assert c["all_classifications_exercised"] is False
    assert "divergent_production_cell" in c["classifications_missing"]
    assert any(
        "exercise all four canary classifications" in t
        for t in summary["next_coverage_targets"]
    )


def test_next_targets_flag_thin_corpus_and_small_sample():
    art = build_canary_mirror_proof(
        decisions=[_decision(production_cell_id="math")], source_label="x", now=NOW
    )
    summary = build_coverage_summary(artifact=art, now=NOW, intent_count=1)
    targets = summary["next_coverage_targets"]
    assert any("multi-intent corpus" in t for t in targets)
    assert any("at least 20 mirrored decisions" in t for t in targets)


def test_intent_unknown_target_when_intent_count_none():
    art = _demo_artifact()
    summary = build_coverage_summary(artifact=art, now=NOW, intent_count=None)
    assert any(
        "production-intent breadth" in t for t in summary["next_coverage_targets"]
    )


def test_representative_run_has_no_targets():
    # 22 samples across 3 intents, both methods, all classifications, agreement mix.
    decisions = (
        [_decision(production_cell_id="math") for _ in range(5)]
        + [_decision(production_cell_id="general") for _ in range(5)]
        + [_decision(query="hello there friend", intent="chat",
                     production_capability_id="cap.chat.general") for _ in range(5)]
        + [_decision(query="kova pakkanen ja lampotila heating", intent="chat",
                     production_capability_id="cap.chat.general") for _ in range(5)]
        + [_decision(query="write a function", intent="code",
                     production_capability_id="cap.code.gen") for _ in range(2)]
    )
    art = build_canary_mirror_proof(decisions=decisions, source_label="x", now=NOW)
    summary = build_coverage_summary(artifact=art, now=NOW, intent_count=3)
    assert summary["coverage"]["sample_count"] == 22
    assert summary["coverage"]["all_classifications_exercised"] is True
    assert summary["coverage"]["distinct_mesh_methods"] >= 2
    assert summary["next_coverage_targets"] == []


# --- contract -----------------------------------------------------------------


def test_digest_rederives_and_gates_false():
    summary = build_coverage_summary(artifact=_demo_artifact(), now=NOW, intent_count=2)
    core = {k: v for k, v in summary.items() if k != "canonical_digest"}
    assert summary["canonical_digest"] == sha256_digest(core)
    for gate in CLAIM_GATES:
        assert summary[gate] is False
    assert summary["advisory_only"] is True
    assert summary["read_only"] is True


def test_deterministic():
    one = build_coverage_summary(artifact=_demo_artifact(), now=NOW, intent_count=2)
    two = build_coverage_summary(artifact=_demo_artifact(), now=NOW, intent_count=2)
    assert one == two


# --- fail-closed verification -------------------------------------------------


def test_forged_artifact_refused():
    bad = _demo_artifact()
    bad["mirror_report"]["agreement_count"] = 99  # digest mismatch
    with pytest.raises(ValueError, match="failed verification"):
        build_coverage_summary(artifact=bad, now=NOW)


def test_non_proof_artifact_refused():
    with pytest.raises(ValueError):
        build_coverage_summary(artifact={"report_version": "evil.v9"}, now=NOW)


# --- CLI ----------------------------------------------------------------------


def test_main_demo_json(capsys):
    rc = main(["--demo", "--now", NOW_TEXT, "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["coverage"]["all_classifications_exercised"] is True
    assert out["coverage"]["intent_count"] == 2  # demo has math + chat


def test_main_artifact_file(tmp_path, capsys):
    art = _demo_artifact()
    p = tmp_path / "proof.json"
    p.write_text(json.dumps(art), encoding="utf-8")
    rc = main(["--artifact", str(p), "--now", NOW_TEXT, "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    # file mode is intent-unknown -> breadth target present
    assert out["coverage"]["intent_count"] is None
    assert any("production-intent breadth" in t for t in out["next_coverage_targets"])


def test_main_out_matches_stdout(tmp_path, capsys):
    out_path = tmp_path / "cov.json"
    rc = main(["--demo", "--now", NOW_TEXT, "--json", "--out", str(out_path)])
    assert rc == 0
    stdout = json.loads(capsys.readouterr().out)
    assert stdout == json.loads(out_path.read_text(encoding="utf-8"))


def test_main_missing_file_exit_3(tmp_path):
    assert main(["--artifact", str(tmp_path / "absent.json")]) == 3


def test_main_unreadable_exit_2(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    assert main(["--artifact", str(p)]) == 2


def test_main_forged_exit_2(tmp_path):
    bad = _demo_artifact()
    bad["mirror_report"]["sample_count"] = 999
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(bad), encoding="utf-8")
    assert main(["--artifact", str(p)]) == 2


def test_main_bad_now_exit_2():
    assert main(["--demo", "--now", "junk"]) == 2
