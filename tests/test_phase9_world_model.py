"""Targeted tests for Phase 9 §I World Model + Calibration Drift."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.world_model import (
    DEFAULT_DRIFT_THRESHOLD,
    FACT_KINDS,
    PREDICTION_HORIZONS,
    calibration_drift_detector as cdd,
    causal_engine as ce,
    external_evidence_collector as eec,
    prediction_calibrator as pcal,
    prediction_engine as pe,
    world_model_delta as wmd,
    world_model_snapshot as wms,
)


def _prov_kwargs():
    return dict(
        branch_name="phase9/autonomy-fabric",
        base_commit_hash="ddb0821",
        pinned_input_manifest_sha256="sha256:test",
    )


# ═══════════════════ schema enums match constants ══════════════════

def test_fact_kinds_match_schema():
    schema = json.loads((ROOT / "schemas" / "world_model.schema.json")
                          .read_text(encoding="utf-8"))
    fact_kinds = tuple(schema["properties"]["external_facts"]["items"]
                          ["properties"]["kind"]["enum"])
    assert fact_kinds == FACT_KINDS


# ═══════════════════ ExternalFact / CausalRelation / Prediction ════

def test_external_fact_rejects_unknown_kind():
    with pytest.raises(ValueError):
        wms.ExternalFact(fact_id="f1", kind="bogus", claim="x",
                            confidence=0.5, source_refs=())


def test_external_fact_confidence_bounds():
    with pytest.raises(ValueError):
        wms.ExternalFact(fact_id="f1", kind="observation",
                            claim="x", confidence=1.5, source_refs=())


def test_causal_relation_rejects_self_loop():
    with pytest.raises(ValueError):
        wms.CausalRelation(cause_fact_id="f1", effect_fact_id="f1",
                              strength=0.5)


def test_prediction_rejects_unknown_horizon():
    with pytest.raises(ValueError):
        wms.Prediction(prediction_id="p1", claim="x", predicted_value=1,
                          confidence=0.5, horizon="bogus")


# ═══════════════════ snapshot id determinism ═══════════════════════

def test_snapshot_id_deterministic_across_facts_order():
    facts_a = [
        wms.ExternalFact(fact_id="f1", kind="observation",
                            claim="x", confidence=0.7, source_refs=()),
        wms.ExternalFact(fact_id="f2", kind="report",
                            claim="y", confidence=0.5, source_refs=()),
    ]
    facts_b = list(reversed(facts_a))
    sid_a = wms.compute_snapshot_id(external_facts=facts_a,
                                         causal_relations=[],
                                         predictions=[])
    sid_b = wms.compute_snapshot_id(external_facts=facts_b,
                                         causal_relations=[],
                                         predictions=[])
    assert sid_a == sid_b   # sorting inside compute makes it stable


def test_make_snapshot_byte_stable_canonical_json():
    facts = [
        wms.ExternalFact(fact_id="f1", kind="observation", claim="x",
                            confidence=0.7, source_refs=("ref1",)),
    ]
    s1 = wms.make_snapshot(
        produced_at_iso="2026-04-26T03:00:00+00:00",
        external_facts=facts, causal_relations=[], predictions=[],
        **_prov_kwargs(),
    )
    s2 = wms.make_snapshot(
        produced_at_iso="2026-04-26T03:00:00+00:00",
        external_facts=facts, causal_relations=[], predictions=[],
        **_prov_kwargs(),
    )
    assert wms.to_canonical_json(s1) == wms.to_canonical_json(s2)


def test_uncertainty_summary_counts():
    facts = [
        wms.ExternalFact(fact_id="f1", kind="observation",
                            claim="x", confidence=0.3, source_refs=()),
        wms.ExternalFact(fact_id="f2", kind="observation",
                            claim="y", confidence=0.9, source_refs=()),
    ]
    pred_unevaluated = wms.Prediction(
        prediction_id="p1", claim="z", predicted_value=42,
        confidence=0.5, horizon="short_term",
    )
    s = wms.make_snapshot(
        produced_at_iso="t",
        external_facts=facts, causal_relations=[],
        predictions=[pred_unevaluated], **_prov_kwargs(),
    )
    assert s.uncertainty_summary["facts_with_low_confidence"] == 1
    assert s.uncertainty_summary["predictions_pending_eval"] == 1


# ═══════════════════ delta detection ═══════════════════════════════

def test_delta_detects_added_and_removed_facts():
    f_a = wms.ExternalFact(fact_id="f_keep", kind="observation",
                              claim="k", confidence=0.5, source_refs=())
    f_b = wms.ExternalFact(fact_id="f_old", kind="observation",
                              claim="o", confidence=0.5, source_refs=())
    f_c = wms.ExternalFact(fact_id="f_new", kind="observation",
                              claim="n", confidence=0.5, source_refs=())
    prev = wms.make_snapshot(
        produced_at_iso="t1",
        external_facts=[f_a, f_b], causal_relations=[],
        predictions=[], **_prov_kwargs(),
    )
    curr = wms.make_snapshot(
        produced_at_iso="t2",
        external_facts=[f_a, f_c], causal_relations=[],
        predictions=[], **_prov_kwargs(),
    )
    delta = wmd.compute_delta(prev, curr)
    assert "f_new" in delta.facts_added
    assert "f_old" in delta.facts_removed


def test_delta_detects_confidence_shift():
    prev_f = wms.ExternalFact(fact_id="f", kind="observation",
                                  claim="x", confidence=0.3, source_refs=())
    curr_f = wms.ExternalFact(fact_id="f", kind="observation",
                                  claim="x", confidence=0.7, source_refs=())
    prev = wms.make_snapshot(produced_at_iso="t1",
                                  external_facts=[prev_f],
                                  causal_relations=[], predictions=[],
                                  **_prov_kwargs())
    curr = wms.make_snapshot(produced_at_iso="t2",
                                  external_facts=[curr_f],
                                  causal_relations=[], predictions=[],
                                  **_prov_kwargs())
    delta = wmd.compute_delta(prev, curr,
                                   confidence_shift_threshold=0.10)
    shifts = delta.facts_confidence_shifted
    assert len(shifts) == 1
    assert shifts[0]["fact_id"] == "f"
    assert abs(shifts[0]["delta"] - 0.4) < 1e-9


# ═══════════════════ external_evidence_collector ═══════════════════

def test_curiosity_log_to_external_facts():
    rows = [
        {"curiosity_id": "cur_1", "candidate_cell": "thermal",
         "estimated_value": 9.5, "suspected_gap_type": "missing_solver"},
    ]
    facts = eec.from_curiosity_log(rows)
    assert len(facts) == 1
    assert facts[0].kind == "observation"


def test_replay_report_to_external_facts():
    report = {
        "replay_case_count": 5,
        "structural_gain_count": 2,
        "estimated_fallback_delta": 0.4,
        "tension_ids_targeted": ["ten_001"],
    }
    facts = eec.from_dream_replay_report(report)
    assert len(facts) == 1
    assert facts[0].kind == "report"
    assert facts[0].confidence == pytest.approx(0.4)


def test_replay_report_empty_yields_no_facts():
    facts = eec.from_dream_replay_report({"replay_case_count": 0})
    assert facts == []


def test_mentor_pack_excludes_self_referential_kinds():
    """anti_pattern + open_question are about WD's internals;
    they belong to self_model, not world_model."""
    pack = {
        "items": [
            {"item_id": "i1", "kind": "design_note",
             "content": "external pattern note"},
            {"item_id": "i2", "kind": "anti_pattern",
             "content": "we should not do X"},
            {"item_id": "i3", "kind": "open_question",
             "content": "is Y true?"},
        ],
    }
    facts = eec.from_mentor_context_pack(pack)
    assert len(facts) == 1
    assert "external pattern" in facts[0].claim


# ═══════════════════ causal_engine ═════════════════════════════════

def test_build_relations_dedups_and_sorts():
    rels = ce.build_relations(known_pairs=[
        ("a", "b", 0.5), ("b", "c", 0.7), ("a", "b", 0.9),
    ])
    assert len(rels) == 2   # dup (a, b) collapsed
    assert rels[0].cause_fact_id < rels[1].cause_fact_id or \
        rels[0].effect_fact_id < rels[1].effect_fact_id


def test_build_relations_skips_self_loop():
    rels = ce.build_relations(known_pairs=[("a", "a", 0.5)])
    assert rels == []


def test_ancestors_and_descendants():
    rels = ce.build_relations(known_pairs=[
        ("a", "b", 0.5), ("b", "c", 0.5), ("c", "d", 0.5),
    ])
    assert ce.ancestors("d", rels) == {"a", "b", "c"}
    assert ce.descendants("a", rels) == {"b", "c", "d"}


def test_detect_causal_cycles():
    rels = [
        wms.CausalRelation(cause_fact_id="a", effect_fact_id="b",
                              strength=0.5),
        wms.CausalRelation(cause_fact_id="b", effect_fact_id="c",
                              strength=0.5),
        wms.CausalRelation(cause_fact_id="c", effect_fact_id="a",
                              strength=0.5),
    ]
    cycles = ce.detect_cycles(rels)
    assert len(cycles) >= 1


# ═══════════════════ prediction_engine + calibrator ═══════════════-

def test_make_prediction_id_deterministic():
    p1 = pe.make_prediction(
        claim="x", predicted_value=10, horizon="short_term",
        based_on_facts=("f1", "f2"), confidence=0.7,
    )
    p2 = pe.make_prediction(
        claim="x", predicted_value=10, horizon="short_term",
        based_on_facts=("f2", "f1"),  # reversed
        confidence=0.5,   # different confidence — id excludes this
    )
    assert p1.prediction_id == p2.prediction_id


def test_make_prediction_rejects_unknown_horizon():
    with pytest.raises(ValueError):
        pe.make_prediction(
            claim="x", predicted_value=1, horizon="bogus",
            based_on_facts=(), confidence=0.5,
        )


def test_evaluate_prediction_computes_calibration_error():
    p = pe.make_prediction(
        claim="x", predicted_value=10.0, horizon="short_term",
        based_on_facts=(), confidence=0.7,
    )
    p2 = pe.evaluate_prediction(p, actual_value=12.0,
                                     evaluated_at_iso="t")
    assert p2.calibration_error == pytest.approx(2.0)
    assert p2.actual_value == 12.0


def test_evaluate_non_numeric_prediction_yields_none_error():
    p = pe.make_prediction(
        claim="categorical", predicted_value="A", horizon="short_term",
        based_on_facts=(), confidence=0.7,
    )
    p2 = pe.evaluate_prediction(p, actual_value="B",
                                     evaluated_at_iso="t")
    assert p2.calibration_error is None


def test_calibrate_per_dimension():
    preds = []
    for i in range(3):
        p = pe.make_prediction(
            claim=f"c{i}", predicted_value=10.0, horizon="short_term",
            based_on_facts=(f"f{i}",), confidence=0.7,
        )
        preds.append(pe.evaluate_prediction(p, actual_value=11.0,
                                                  evaluated_at_iso="t"))
    records = pcal.calibrate_per_dimension(preds)
    assert "short_term" in records
    r = records["short_term"]
    assert r.n_observations == 3
    assert r.abs_error == pytest.approx(1.0)


# ═══════════════════ drift detector ═══════════════════════════════-

def test_drift_overconfident_detected():
    """Prior_score consistently > evidence_implied → overconfident."""
    history = {
        "short_term": [
            pcal.CalibrationRecord(
                dimension="short_term",
                prior_score=0.9, evidence_implied_score=0.5,
                abs_error=0.4, n_observations=3,
            )
            for _ in range(6)
        ],
    }
    alerts = cdd.detect_drift(history, threshold=0.20, window=5)
    assert len(alerts) == 1
    assert alerts[0].direction == "drift_overconfident"


def test_drift_underconfident_detected():
    history = {
        "long_term": [
            pcal.CalibrationRecord(
                dimension="long_term",
                prior_score=0.4, evidence_implied_score=0.8,
                abs_error=0.4, n_observations=3,
            )
            for _ in range(6)
        ],
    }
    alerts = cdd.detect_drift(history, threshold=0.20, window=5)
    assert len(alerts) == 1
    assert alerts[0].direction == "drift_low_confidence"


def test_drift_below_threshold_no_alert():
    history = {
        "x": [
            pcal.CalibrationRecord(
                dimension="x", prior_score=0.55,
                evidence_implied_score=0.50,
                abs_error=0.05, n_observations=3,
            )
            for _ in range(6)
        ],
    }
    alerts = cdd.detect_drift(history, threshold=0.20, window=5)
    assert alerts == []


def test_drift_window_under_minimum_no_alert():
    """Window of 2 records < default 5 → no alert even if gap is large."""
    history = {
        "x": [
            pcal.CalibrationRecord(
                dimension="x", prior_score=0.9,
                evidence_implied_score=0.1,
                abs_error=0.8, n_observations=1,
            )
            for _ in range(2)
        ],
    }
    alerts = cdd.detect_drift(history, threshold=0.20, window=5)
    assert alerts == []


# ═══════════════════ separation from self_model ═══════════════════-

def test_world_model_module_does_not_import_self_model():
    """Constitution-level invariant: world_model is distinct from
    self_model. Source must not import from waggledance.core.magma
    (where self_model lives)."""
    pkg = ROOT / "waggledance" / "core" / "world_model"
    for p in pkg.glob("*.py"):
        text = p.read_text(encoding="utf-8")
        assert "from waggledance.core.magma" not in text
        assert "import waggledance.core.magma" not in text


# ═══════════════════ CLI tool ══════════════════════════════════════

def test_build_world_model_snapshot_help():
    r = subprocess.run(
        [sys.executable,
         str(ROOT / "tools" / "build_world_model_snapshot.py"),
         "--help"],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0
    for flag in ("--curiosity-log", "--replay-report",
                  "--mentor-pack", "--apply", "--json"):
        assert flag in r.stdout


def test_build_world_model_dry_run(tmp_path):
    """Smoke test: empty inputs → empty snapshot, exit 0."""
    r = subprocess.run(
        [sys.executable,
         str(ROOT / "tools" / "build_world_model_snapshot.py"),
         "--ts", "2026-04-26T03:00:00+00:00",
         "--json"],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0
    summary = json.loads(r.stdout)
    assert "snapshot_id" in summary
    assert summary["facts"] == 0


# ═══════════════════ source safety ═════════════════════════════════

def test_phase_i_source_safety():
    forbidden = ["import faiss", "from waggledance.runtime",
                  "ollama.generate(", "openai.chat",
                  "anthropic.messages", "requests.get(",
                  "requests.post(", "axiom_write(",
                  "promote_to_runtime("]
    forbidden_metaphors = ["bee ", "honeycomb ", "swarm ", "pdam",
                             "beverage"]
    pkg = ROOT / "waggledance" / "core" / "world_model"
    for p in pkg.glob("*.py"):
        text = p.read_text(encoding="utf-8")
        for pat in forbidden:
            assert pat not in text, f"{p.name}: {pat}"
        text_l = text.lower()
        for pat in forbidden_metaphors:
            assert pat not in text_l, f"{p.name}: {pat}"
