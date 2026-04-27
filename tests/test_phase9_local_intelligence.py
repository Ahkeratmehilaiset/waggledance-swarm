# SPDX-License-Identifier: Apache-2.0
"""Targeted tests for Phase 9 §N Local Model Distillation scaffold."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.local_intelligence import (
    DriftDetector,
    DriftReport,
    FineTuneJobReport,
    FineTuneJobSpec,
    FineTunePipeline,
    FineTunePipelineError,
    InferenceDecision,
    InferenceRouter,
    InferenceRouterError,
    LocalModelManager,
    LocalModelManagerError,
    LocalModelRecord,
    ModelEvaluator,
)


# ═══════════════════ LocalModelManager ═════════════════════════════

def _mk_record(model_id: str = "lm.curiosity_clf", version: str = "v0.1",
                status: str = "shadow_only") -> LocalModelRecord:
    return LocalModelRecord(
        model_id=model_id, version=version,
        base_model_family="qwen-2.5-3b",
        training_corpus_hash="a" * 12,
        fine_tune_job_id="job_001",
        lifecycle_status=status,
        advisory_only=True,
        no_runtime_auto_promotion=True,
        no_foundational_mutation=True,
    )


def test_record_must_be_advisory_only():
    with pytest.raises(LocalModelManagerError, match="advisory_only"):
        LocalModelRecord(
            model_id="x", version="v1",
            base_model_family="f", training_corpus_hash="h",
            fine_tune_job_id="j", lifecycle_status="shadow_only",
            advisory_only=False,
            no_runtime_auto_promotion=True,
            no_foundational_mutation=True,
        )


def test_record_unknown_lifecycle_status_rejected():
    with pytest.raises(LocalModelManagerError, match="lifecycle_status"):
        LocalModelRecord(
            model_id="x", version="v1",
            base_model_family="f", training_corpus_hash="h",
            fine_tune_job_id="j", lifecycle_status="production",
            advisory_only=True,
            no_runtime_auto_promotion=True,
            no_foundational_mutation=True,
        )


def test_register_returns_deterministic_record_id():
    mgr1 = LocalModelManager()
    mgr2 = LocalModelManager()
    rid1 = mgr1.register(_mk_record())
    rid2 = mgr2.register(_mk_record())
    assert rid1 == rid2
    assert len(rid1) == 12


def test_register_rejects_duplicate_model_version():
    mgr = LocalModelManager()
    mgr.register(_mk_record())
    with pytest.raises(LocalModelManagerError, match="duplicate"):
        mgr.register(_mk_record())


def test_transition_requires_rationale():
    mgr = LocalModelManager()
    mgr.register(_mk_record())
    with pytest.raises(LocalModelManagerError, match="rationale"):
        mgr.transition("lm.curiosity_clf", "v0.1", "advisory", "")


def test_transition_unknown_status_rejected():
    mgr = LocalModelManager()
    mgr.register(_mk_record())
    with pytest.raises(LocalModelManagerError, match="lifecycle_status"):
        mgr.transition("lm.curiosity_clf", "v0.1",
                        "production", "promote me")


def test_transition_to_retired_terminal():
    mgr = LocalModelManager()
    mgr.register(_mk_record())
    mgr.transition("lm.curiosity_clf", "v0.1",
                     "retired", "shadow eval failed")
    with pytest.raises(LocalModelManagerError, match="retired"):
        mgr.transition("lm.curiosity_clf", "v0.1",
                         "advisory", "rehydrate")


def test_transition_history_is_appended():
    mgr = LocalModelManager()
    mgr.register(_mk_record())
    mgr.transition("lm.curiosity_clf", "v0.1",
                     "advisory", "shadow eval green")
    events = [h["event"] for h in mgr.history]
    assert events == ["register", "transition"]
    assert mgr.history[-1]["from_status"] == "shadow_only"
    assert mgr.history[-1]["to_status"] == "advisory"


def test_to_json_is_byte_stable_for_same_state():
    mgr_a = LocalModelManager()
    mgr_b = LocalModelManager()
    mgr_a.register(_mk_record())
    mgr_b.register(_mk_record())
    assert mgr_a.to_json() == mgr_b.to_json()


# ═══════════════════ FineTunePipeline ══════════════════════════════

def _mk_spec() -> FineTuneJobSpec:
    return FineTuneJobSpec(
        base_model_family="qwen-2.5-3b",
        training_corpus_id="cache.v1",
        training_corpus_hash="b" * 12,
        target_capability="curiosity_classification",
        training_data_source_kind="provider_consultation_cache",
        max_train_examples=10_000,
        no_foundational_data=True,
        no_runtime_mutation=True,
    )


def test_spec_rejects_forbidden_data_source():
    with pytest.raises(FineTunePipelineError, match="forbidden"):
        FineTuneJobSpec(
            base_model_family="x", training_corpus_id="y",
            training_corpus_hash="z" * 12, target_capability="t",
            training_data_source_kind="foundational_invariants",
            max_train_examples=100,
            no_foundational_data=True, no_runtime_mutation=True,
        )


def test_spec_requires_no_foundational_data_true():
    with pytest.raises(FineTunePipelineError, match="no_foundational_data"):
        FineTuneJobSpec(
            base_model_family="x", training_corpus_id="y",
            training_corpus_hash="z" * 12, target_capability="t",
            training_data_source_kind="provider_consultation_cache",
            max_train_examples=100,
            no_foundational_data=False, no_runtime_mutation=True,
        )


def test_pipeline_plan_is_deterministic():
    p1 = FineTunePipeline()
    p2 = FineTunePipeline()
    r1 = p1.plan(_mk_spec())
    r2 = p2.plan(_mk_spec())
    assert r1.job_id == r2.job_id
    assert r1.pipeline_stages == r2.pipeline_stages


def test_plan_report_has_no_execution_flags():
    p = FineTunePipeline()
    r = p.plan(_mk_spec())
    assert r.fine_tune_executed is False
    assert r.human_required is True
    assert r.advisory_only is True
    assert r.no_runtime_auto_promotion is True


def test_plan_stages_are_complete():
    p = FineTunePipeline()
    r = p.plan(_mk_spec())
    assert r.pipeline_stages == (
        "spec_validated",
        "corpus_pinned",
        "shadow_eval_planned",
        "drift_check_planned",
        "human_review_required",
    )


def test_pipeline_execute_raises_by_design():
    p = FineTunePipeline()
    with pytest.raises(FineTunePipelineError, match="not implemented"):
        p.execute()


def test_report_to_dict_round_trip():
    p = FineTunePipeline()
    r = p.plan(_mk_spec())
    d = r.to_dict()
    assert d["fine_tune_executed"] is False
    assert d["human_required"] is True
    assert "pipeline_stages" in d
    json.dumps(d)  # serializable


# ═══════════════════ InferenceRouter ═══════════════════════════════

def test_router_refuses_critical_task_kind():
    r = InferenceRouter(local_model_enabled=True)
    d = r.decide({"task_kind": "foundational_axiom_authoring"})
    assert d.chosen_destination == "refuse"


def test_router_refuses_runtime_mutation_proposal():
    r = InferenceRouter(local_model_enabled=True)
    d = r.decide({"task_kind": "runtime_mutation_proposal"})
    assert d.chosen_destination == "refuse"


def test_router_refuses_main_branch_merge_decision():
    r = InferenceRouter(local_model_enabled=True)
    d = r.decide({"task_kind": "main_branch_merge_decision"})
    assert d.chosen_destination == "refuse"


def test_router_default_is_external_when_disabled():
    r = InferenceRouter(local_model_enabled=False)
    d = r.decide({"task_kind": "scratch_summarization"})
    assert d.chosen_destination == "external_provider"


def test_router_unknown_task_kind_falls_back_external():
    r = InferenceRouter(local_model_enabled=True)
    d = r.decide({"task_kind": "novel_unrecognized_kind"})
    assert d.chosen_destination == "external_provider"


def test_router_enabled_routes_to_shadow_with_external_fallback():
    r = InferenceRouter(local_model_enabled=True)
    d = r.decide({"task_kind": "scratch_summarization"})
    assert d.chosen_destination == "local_model_shadow"
    assert d.fallback_destination == "external_provider"


def test_router_decision_carries_invariants():
    r = InferenceRouter(local_model_enabled=True)
    d = r.decide({"task_kind": "advisory_synthesis"})
    assert d.advisory_only is True
    assert d.no_runtime_mutation is True
    assert d.no_foundational_authority is True


def test_router_call_local_model_raises():
    r = InferenceRouter()
    with pytest.raises(InferenceRouterError, match="not implemented"):
        r.call_local_model()


def test_router_request_id_deterministic():
    r = InferenceRouter()
    d1 = r.decide({"task_kind": "scratch_summarization", "x": 1})
    d2 = r.decide({"task_kind": "scratch_summarization", "x": 1})
    assert d1.request_id == d2.request_id


def test_router_requires_task_kind():
    r = InferenceRouter()
    with pytest.raises(InferenceRouterError, match="task_kind"):
        r.decide({})


# ═══════════════════ ModelEvaluator ════════════════════════════════

def test_evaluator_scores_pairs():
    ev = ModelEvaluator()
    rep = ev.score("lm.x", "v1",
                     [("a", "a"), ("b", "b"), ("c", "x")])
    assert rep.n_examples == 3
    assert rep.n_correct == 2
    assert abs(rep.accuracy - (2 / 3)) < 1e-9
    assert rep.advisory_only is True
    assert rep.no_runtime_authority is True


def test_evaluator_rejects_empty_pairs():
    ev = ModelEvaluator()
    with pytest.raises(ValueError, match="non-empty"):
        ev.score("lm.x", "v1", [])


def test_evaluator_call_model_raises():
    ev = ModelEvaluator()
    with pytest.raises(ValueError, match="precomputed"):
        ev.call_model()


# ═══════════════════ DriftDetector ═════════════════════════════════

def test_drift_nominal_under_threshold():
    d = DriftDetector()
    rep = d.compare(
        candidate_eval_set_id="c1", candidate_accuracy=0.81,
        baseline_eval_set_id="b1", baseline_accuracy=0.80,
    )
    assert rep.severity == "nominal"


def test_drift_watch_band():
    d = DriftDetector()
    rep = d.compare(
        candidate_eval_set_id="c1", candidate_accuracy=0.76,
        baseline_eval_set_id="b1", baseline_accuracy=0.80,
    )
    assert rep.severity == "watch"


def test_drift_elevated_band():
    d = DriftDetector()
    rep = d.compare(
        candidate_eval_set_id="c1", candidate_accuracy=0.70,
        baseline_eval_set_id="b1", baseline_accuracy=0.78,
    )
    assert rep.severity == "elevated"


def test_drift_critical_band():
    d = DriftDetector()
    rep = d.compare(
        candidate_eval_set_id="c1", candidate_accuracy=0.50,
        baseline_eval_set_id="b1", baseline_accuracy=0.80,
    )
    assert rep.severity == "critical"


def test_drift_rejects_out_of_range_accuracy():
    d = DriftDetector()
    with pytest.raises(ValueError, match="must be in"):
        d.compare(
            candidate_eval_set_id="c1", candidate_accuracy=1.5,
            baseline_eval_set_id="b1", baseline_accuracy=0.5,
        )


def test_drift_report_carries_invariants():
    d = DriftDetector()
    rep = d.compare(
        candidate_eval_set_id="c1", candidate_accuracy=0.80,
        baseline_eval_set_id="b1", baseline_accuracy=0.80,
    )
    assert rep.advisory_only is True
    assert rep.no_runtime_auto_action is True
    assert "advisory" in rep.rationale


# ═══════════════════ Source-grep safety ════════════════════════════

def test_local_intelligence_has_no_live_llm_or_runtime_imports():
    forbidden = [
        "import torch",
        "import transformers",
        "import ollama",
        "import openai",
        "import anthropic",
        "import requests",
        "import httpx",
        "import urllib.request",
        "subprocess.run(",
        "subprocess.Popen(",
        "from waggledance.runtime",
        "promote_to_runtime(",
    ]
    pkg = ROOT / "waggledance" / "core" / "local_intelligence"
    for p in pkg.glob("*.py"):
        text = p.read_text(encoding="utf-8")
        for pat in forbidden:
            assert pat not in text, f"{p.name}: {pat}"


def test_no_advisory_only_false_in_source():
    pkg = ROOT / "waggledance" / "core" / "local_intelligence"
    for p in pkg.glob("*.py"):
        text = p.read_text(encoding="utf-8")
        assert "advisory_only=False" not in text
        assert "no_runtime_auto_promotion=False" not in text
        assert "no_foundational_mutation=False" not in text


def test_schema_carries_const_invariants():
    schema = json.loads(
        (ROOT / "schemas" / "local_model_record.schema.json")
        .read_text(encoding="utf-8")
    )
    assert schema["properties"]["advisory_only"]["const"] is True
    assert schema["properties"]["no_runtime_auto_promotion"]["const"] is True
    assert schema["properties"]["no_foundational_mutation"]["const"] is True
    assert schema["properties"]["lifecycle_status"]["enum"] == [
        "shadow_only", "advisory", "retired",
    ]


def test_doc_present_and_mentions_safe_routing_contract():
    doc = (ROOT / "docs" / "architecture"
           / "LOCAL_MODEL_DISTILLATION.md").read_text(encoding="utf-8")
    assert "Safe Routing Contract" in doc
    assert "human-gated" in doc.lower()
    assert "scaffold" in doc.lower()
