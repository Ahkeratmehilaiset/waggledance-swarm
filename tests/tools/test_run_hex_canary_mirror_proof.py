# SPDX-License-Identifier: BUSL-1.1
"""Unit tests for tools/run_hex_canary_mirror_proof.py.

Exercises the demo corpus, JSONL input path, the closed input contract,
privacy of the emitted artifact, digest re-derivation, and the advisory
agreement floor. All fixtures are synthetic tmp_path files.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools.run_hex_canary_mirror_proof import (
    BUILTIN_CORPORA,
    CLAIM_GATES,
    DEMO_DECISIONS,
    REPRESENTATIVE_DECISIONS,
    build_canary_mirror_proof,
    main,
)
from waggledance.core.hex_topology.canary_mirror import CANARY_CLASSIFICATIONS
from waggledance.core.magma.canonical import sha256_digest

NOW_TEXT = "2026-06-10T12:00:00Z"
NOW = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)


def _decision(**overrides) -> dict:
    base = {
        "query": "calculate the heating formula",
        "intent": "math",
        "production_capability_id": "cap.math.formula",
        "quality_path": "silver",
    }
    base.update(overrides)
    return base


def _write_jsonl(tmp_path: Path, records: list, name: str = "decisions.jsonl") -> Path:
    p = tmp_path / name
    p.write_text(
        "\n".join(
            json.dumps(r) if isinstance(r, dict) else r for r in records
        )
        + "\n",
        encoding="utf-8",
    )
    return p


# --- demo corpus --------------------------------------------------------------


def test_demo_covers_all_four_classifications_and_is_deterministic(capsys):
    assert main(["--demo", "--now", NOW_TEXT, "--json"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert main(["--demo", "--now", NOW_TEXT, "--json"]) == 0
    second = json.loads(capsys.readouterr().out)
    assert first == second

    report = first["mirror_report"]
    assert first["ok"] is True
    assert report["sample_count"] == len(DEMO_DECISIONS) == 4
    assert all(count == 1 for count in report["by_classification"].values())
    assert report["agreement_rate"] == 0.5
    assert first["input_source"] == "demo"


def test_demo_flag_equals_corpus_demo(capsys):
    assert main(["--demo", "--now", NOW_TEXT, "--json"]) == 0
    demo_flag = json.loads(capsys.readouterr().out)
    assert main(["--corpus", "demo", "--now", NOW_TEXT, "--json"]) == 0
    corpus_demo = json.loads(capsys.readouterr().out)
    assert demo_flag == corpus_demo


# --- representative corpus -----------------------------------------------------


def test_representative_corpus_is_richer_and_deterministic(capsys):
    assert main(["--corpus", "representative", "--now", NOW_TEXT, "--json"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert main(["--corpus", "representative", "--now", NOW_TEXT, "--json"]) == 0
    second = json.loads(capsys.readouterr().out)
    assert first == second

    report = first["mirror_report"]
    assert first["ok"] is True
    assert first["input_source"] == "representative"
    assert report["sample_count"] == len(REPRESENTATIVE_DECISIONS) == 20
    # Locked empirical spread: exercises all four classifications.
    assert report["by_classification"] == {
        "match_production_cell": 8,
        "divergent_production_cell": 4,
        "match_intent_cell": 5,
        "divergent_keyword_override": 3,
    }
    assert set(report["by_classification"]) == set(CANARY_CLASSIFICATIONS)
    assert all(count > 0 for count in report["by_classification"].values())
    assert report["agreement_count"] == 13
    assert report["agreement_rate"] == 0.65
    # Both real routing methods exercised (intent + keyword), plus default.
    assert report["by_mesh_method"]["intent"] > 0
    assert report["by_mesh_method"]["keyword"] > 0
    assert len([c for c in report["by_mesh_cell"].values() if c > 0]) >= 3


def test_representative_corpus_spans_three_intents():
    intents = {d["intent"] for d in REPRESENTATIVE_DECISIONS}
    assert intents == {"math", "chat", "code"}


def test_representative_records_pass_closed_contract():
    allowed = {
        "query",
        "intent",
        "production_capability_id",
        "quality_path",
        "production_cell_id",
    }
    required = {"query", "intent", "production_capability_id", "quality_path"}
    for record in REPRESENTATIVE_DECISIONS:
        keys = set(record)
        assert required <= keys
        assert keys <= allowed


def test_builtin_corpora_registry_maps_both():
    assert BUILTIN_CORPORA["demo"] == DEMO_DECISIONS
    assert BUILTIN_CORPORA["representative"] == REPRESENTATIVE_DECISIONS


def test_corpus_and_demo_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        main(["--demo", "--corpus", "representative", "--now", NOW_TEXT])


# --- artifact contract ---------------------------------------------------------


def test_artifact_digest_rederives_and_claim_gates_false():
    artifact = build_canary_mirror_proof(
        decisions=DEMO_DECISIONS,
        source_label="demo",
        now=NOW,
    )
    report = artifact["mirror_report"]
    core = {k: v for k, v in report.items() if k != "canonical_digest"}
    assert report["canonical_digest"] == sha256_digest(core)
    for gate in CLAIM_GATES:
        assert artifact[gate] is False
    assert artifact["claim_label"] == "MEASURED_LOCAL_SHADOW_MIRROR"
    assert artifact["generated_at_utc"] == "2026-06-10T12:00:00Z"


def test_artifact_is_privacy_safe():
    secret = "operator-secret pakkanen question never-in-artifact"
    artifact = build_canary_mirror_proof(
        decisions=[_decision(query=secret)],
        source_label="demo",
        now=NOW,
    )
    assert secret not in json.dumps(artifact)
    assert "operator-secret" not in json.dumps(artifact)


def test_source_label_is_bounded():
    artifact = build_canary_mirror_proof(
        decisions=[_decision()],
        source_label="x" * 5000,
        now=NOW,
    )
    assert len(artifact["input_source"]) == 200


# --- JSONL input ---------------------------------------------------------------


def test_input_jsonl_happy_path(tmp_path, capsys):
    path = _write_jsonl(
        tmp_path,
        [
            _decision(production_cell_id="math"),
            _decision(production_cell_id="general"),
        ],
    )
    rc = main(["--input", str(path), "--now", NOW_TEXT, "--json"])
    assert rc == 0
    artifact = json.loads(capsys.readouterr().out)
    report = artifact["mirror_report"]
    assert report["sample_count"] == 2
    assert report["agreement_count"] == 1
    assert artifact["input_record_count"] == 2


def test_out_file_matches_stdout(tmp_path, capsys):
    path = _write_jsonl(tmp_path, [_decision()])
    out = tmp_path / "artifact.json"
    rc = main(
        ["--input", str(path), "--now", NOW_TEXT, "--json", "--out", str(out)]
    )
    assert rc == 0
    stdout_artifact = json.loads(capsys.readouterr().out)
    file_artifact = json.loads(out.read_text(encoding="utf-8"))
    assert stdout_artifact == file_artifact


# --- closed input contract (fail-closed) ----------------------------------------


def test_unknown_key_refused(tmp_path):
    path = _write_jsonl(tmp_path, [_decision(extra_field="smuggled")])
    assert main(["--input", str(path), "--now", NOW_TEXT]) == 2


def test_missing_key_refused(tmp_path):
    bad = _decision()
    del bad["quality_path"]
    path = _write_jsonl(tmp_path, [bad])
    assert main(["--input", str(path), "--now", NOW_TEXT]) == 2


def test_invalid_json_line_refused(tmp_path):
    path = _write_jsonl(tmp_path, [_decision(), "{not json"])
    assert main(["--input", str(path), "--now", NOW_TEXT]) == 2


def test_non_object_line_refused(tmp_path):
    path = _write_jsonl(tmp_path, [_decision(), json.dumps(["a", "list"])])
    assert main(["--input", str(path), "--now", NOW_TEXT]) == 2


def test_core_validation_propagates_as_refusal(tmp_path):
    path = _write_jsonl(tmp_path, [_decision(intent="   ")])
    assert main(["--input", str(path), "--now", NOW_TEXT]) == 2


def test_missing_input_file_exit_3(tmp_path):
    assert main(["--input", str(tmp_path / "absent.jsonl")]) == 3


# --- advisory floor --------------------------------------------------------------


def test_agreement_floor_below_fails(capsys):
    rc = main(
        ["--demo", "--now", NOW_TEXT, "--min-agreement-rate", "0.9", "--json"]
    )
    assert rc == 1
    artifact = json.loads(capsys.readouterr().out)
    assert artifact["ok"] is False
    assert artifact["below_agreement_floor"] is True


def test_agreement_floor_met_passes():
    rc = main(["--demo", "--now", NOW_TEXT, "--min-agreement-rate", "0.5"])
    assert rc == 0


def test_floor_out_of_range_refused():
    assert main(["--demo", "--min-agreement-rate", "1.5"]) == 2
    assert main(["--demo", "--min-agreement-rate", "-0.1"]) == 2
    assert main(["--demo", "--min-agreement-rate", "nan"]) == 2


def test_empty_batch_not_ok(tmp_path):
    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")
    assert main(["--input", str(path), "--now", NOW_TEXT]) == 1


def test_bad_now_refused():
    assert main(["--demo", "--now", "junk"]) == 2


def test_demo_corpus_records_pass_closed_contract():
    for record in DEMO_DECISIONS:
        keys = set(record.keys())
        assert {"query", "intent", "production_capability_id", "quality_path"} <= keys
        assert keys <= {
            "query",
            "intent",
            "production_capability_id",
            "quality_path",
            "production_cell_id",
        }


def test_pytest_import_side_effect_free():
    # importing the tool must not mutate sys.path beyond the repo root insert
    import tools.run_hex_canary_mirror_proof as mod

    assert mod.REPORT_VERSION == "wd.v12.hex_canary_mirror_proof.v0"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
