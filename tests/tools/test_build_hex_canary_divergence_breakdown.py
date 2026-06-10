# SPDX-License-Identifier: BUSL-1.1
"""Unit tests for tools/build_hex_canary_divergence_breakdown.py."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from tools.build_hex_canary_divergence_breakdown import (
    DETAIL_FIELDS,
    build_divergence_breakdown,
    main,
)
from tools.run_hex_canary_mirror_proof import CLAIM_GATES, DEMO_DECISIONS
from waggledance.core.magma.canonical import sha256_digest

NOW_TEXT = "2026-06-10T12:00:00Z"
NOW = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)

SECRET = "operator-secret kova pakkanen ja matala lämpötila, heating tarvitaan"


def _decision(**overrides) -> dict:
    base = {
        "query": "calculate the heating formula",
        "intent": "math",
        "production_capability_id": "cap.math.formula",
        "quality_path": "silver",
    }
    base.update(overrides)
    return base


def _divergent_decision(**overrides) -> dict:
    # math intent vs production cell "general" -> divergent_production_cell
    return _decision(production_cell_id="general", **overrides)


def _write_jsonl(tmp_path: Path, records: list) -> Path:
    p = tmp_path / "decisions.jsonl"
    p.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8"
    )
    return p


# --- aggregation ----------------------------------------------------------------


def test_demo_breakdown_counts_and_rates():
    artifact = build_divergence_breakdown(
        decisions=DEMO_DECISIONS, source_label="demo", now=NOW
    )
    assert artifact["decision_count"] == 4
    assert artifact["divergence_count"] == 2
    assert artifact["divergence_rate"] == 0.5
    math_bucket = artifact["by_intent"]["math"]
    assert math_bucket["decisions"] == 2
    assert math_bucket["divergences"] == 1
    assert math_bucket["divergence_rate"] == 0.5
    chat_bucket = artifact["by_intent"]["chat"]
    assert chat_bucket["divergences"] == 1
    assert chat_bucket["divergent_mesh_cells"] == {"thermal": 1}


def test_top_divergent_intents_sorted_and_bounded():
    decisions = (
        [_divergent_decision() for _ in range(3)]
        + [
            _decision(
                query=SECRET,
                intent="chat",
                production_capability_id="cap.chat.general",
            )
        ]
    )
    artifact = build_divergence_breakdown(
        decisions=decisions, source_label="x", now=NOW, top=1
    )
    top = artifact["top_divergent_intents"]
    assert len(top) == 1
    assert top[0]["intent"] == "math"
    assert top[0]["divergences"] == 3


def test_agreeing_intents_not_in_top():
    artifact = build_divergence_breakdown(
        decisions=[_decision(production_cell_id="math")],
        source_label="x",
        now=NOW,
    )
    assert artifact["divergence_count"] == 0
    assert artifact["top_divergent_intents"] == []
    assert artifact["by_intent"]["math"]["divergence_rate"] == 0.0


def test_empty_input_zero_rates():
    artifact = build_divergence_breakdown(
        decisions=[], source_label="x", now=NOW
    )
    assert artifact["decision_count"] == 0
    assert artifact["divergence_rate"] == 0.0
    assert artifact["by_intent"] == {}


# --- privacy and bounds -----------------------------------------------------------


def test_details_are_privacy_safe_and_closed():
    artifact = build_divergence_breakdown(
        decisions=[
            _divergent_decision(query=SECRET),
        ],
        source_label="demo",
        now=NOW,
    )
    rendered = json.dumps(artifact)
    assert SECRET not in rendered
    assert "operator-secret" not in rendered
    detail = artifact["divergence_details"][0]
    assert set(detail.keys()) == set(DETAIL_FIELDS)
    assert detail["query_length"] == len(SECRET)
    assert detail["classification"] == "divergent_production_cell"


def test_detail_bounding_reports_truncation():
    decisions = [_divergent_decision() for _ in range(5)]
    artifact = build_divergence_breakdown(
        decisions=decisions, source_label="x", now=NOW, max_detail=2
    )
    assert len(artifact["divergence_details"]) == 2
    assert artifact["divergence_details_truncated"] is True
    assert artifact["divergence_details_omitted_count"] == 3


def test_no_truncation_flag_when_within_bound():
    artifact = build_divergence_breakdown(
        decisions=[_divergent_decision()], source_label="x", now=NOW
    )
    assert artifact["divergence_details_truncated"] is False
    assert artifact["divergence_details_omitted_count"] == 0


def test_source_label_bounded():
    artifact = build_divergence_breakdown(
        decisions=[], source_label="x" * 5000, now=NOW
    )
    assert len(artifact["input_source"]) == 200


# --- artifact contract -------------------------------------------------------------


def test_digest_rederives_and_gates_false():
    artifact = build_divergence_breakdown(
        decisions=DEMO_DECISIONS, source_label="demo", now=NOW
    )
    core = {k: v for k, v in artifact.items() if k != "canonical_digest"}
    assert artifact["canonical_digest"] == sha256_digest(core)
    for gate in CLAIM_GATES:
        assert artifact[gate] is False
    assert artifact["no_runtime_mutation"] is True
    assert artifact["routing_influence_applied"] is False
    assert artifact["generated_at_utc"] == "2026-06-10T12:00:00Z"


def test_deterministic():
    one = build_divergence_breakdown(
        decisions=DEMO_DECISIONS, source_label="demo", now=NOW
    )
    two = build_divergence_breakdown(
        decisions=DEMO_DECISIONS, source_label="demo", now=NOW
    )
    assert one == two


# --- CLI ----------------------------------------------------------------------------


def test_main_demo_json(capsys):
    assert main(["--demo", "--now", NOW_TEXT, "--json"]) == 0
    artifact = json.loads(capsys.readouterr().out)
    assert artifact["divergence_count"] == 2


def test_main_input_and_out(tmp_path, capsys):
    path = _write_jsonl(tmp_path, [_divergent_decision(), _decision()])
    out = tmp_path / "breakdown.json"
    rc = main(
        ["--input", str(path), "--now", NOW_TEXT, "--json", "--out", str(out)]
    )
    assert rc == 0
    stdout_artifact = json.loads(capsys.readouterr().out)
    assert stdout_artifact == json.loads(out.read_text(encoding="utf-8"))
    assert stdout_artifact["divergence_count"] == 1


def test_main_closed_contract_refusals(tmp_path):
    unknown = _write_jsonl(tmp_path, [_decision(extra="smuggled")])
    assert main(["--input", str(unknown), "--now", NOW_TEXT]) == 2


def test_main_missing_file_exit_3(tmp_path):
    assert main(["--input", str(tmp_path / "absent.jsonl")]) == 3


def test_main_bad_args_exit_2(tmp_path):
    path = _write_jsonl(tmp_path, [_decision()])
    assert main(["--input", str(path), "--now", "junk"]) == 2
    assert main(["--input", str(path), "--top", "0"]) == 2
    assert main(["--input", str(path), "--max-detail", "-1"]) == 2
