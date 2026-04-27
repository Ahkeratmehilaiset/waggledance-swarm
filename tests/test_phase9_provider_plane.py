# SPDX-License-Identifier: Apache-2.0
"""Targeted tests for Phase 9 §J Provider Plane + Distillation Core."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.api_distillation import (
    TRUST_GATE_LAYERS,
    api_consultant as ac,
    knowledge_extractor as ke,
    offline_replay_engine as ore,
)
from waggledance.core.provider_plane import (
    PROVIDERS,
    TASK_CLASSES,
    TASK_CLASS_PRIORITY,
    agent_pool_registry as apr,
    provider_budget_engine as pbe,
    provider_registry as pr,
    provider_router as proute,
    request_pack_router as rpr,
    response_normalizer as rn,
)


# ═══════════════════ schema enums match constants ══════════════════

def test_provider_request_schema_enums_match():
    schema = json.loads((ROOT / "schemas" / "provider_request.schema.json")
                          .read_text(encoding="utf-8"))
    assert tuple(schema["properties"]["task_class"]["enum"]) == TASK_CLASSES
    assert tuple(
        schema["properties"]["provider_priority_list"]["items"]["enum"]
    ) == PROVIDERS


def test_provider_response_no_direct_mutation_const_true():
    schema = json.loads((ROOT / "schemas" / "provider_response.schema.json")
                          .read_text(encoding="utf-8"))
    assert schema["properties"]["no_direct_mutation"]["const"] is True


def test_consultation_record_trust_layer_enum_matches():
    schema = json.loads((ROOT / "schemas" / "consultation_record.schema.json")
                          .read_text(encoding="utf-8"))
    enum = tuple(schema["properties"]["trust_layer_reached"]["enum"])
    # The schema enum lists 6 trust states; TRUST_GATE_LAYERS in api_distillation has equivalent
    assert "raw_quarantine" in enum
    assert "human_gated" in enum


# ═══════════════════ provider_registry ════════════════════════════

def test_provider_record_rejects_unknown_type():
    with pytest.raises(ValueError):
        pr.ProviderRecord(
            schema_version=1, provider_id="x",
            provider_type="bogus_provider",
            daily_budget_calls=10, warm=True, capabilities=(),
        )


def test_provider_registry_register_and_get():
    reg = pr.ProviderRegistry()
    reg.register(pr.ProviderRecord(
        schema_version=1, provider_id="claude_main",
        provider_type="claude_code_builder_lane",
        daily_budget_calls=100, warm=True,
        capabilities=("code", "repair"),
    ))
    got = reg.get("claude_main")
    assert got is not None
    assert got.provider_type == "claude_code_builder_lane"


def test_provider_registry_warm_filter():
    reg = pr.ProviderRegistry()
    reg.register(pr.ProviderRecord(
        schema_version=1, provider_id="warm_one",
        provider_type="anthropic_api", daily_budget_calls=50,
        warm=True, capabilities=(),
    ))
    reg.register(pr.ProviderRecord(
        schema_version=1, provider_id="cold_one",
        provider_type="anthropic_api", daily_budget_calls=50,
        warm=False, capabilities=(),
    ))
    warm = reg.warm_providers()
    assert {p.provider_id for p in warm} == {"warm_one"}


# ═══════════════════ provider_router ══════════════════════════════

def test_router_picks_first_in_priority_chain():
    reg = pr.ProviderRegistry()
    for ptype in ("claude_code_builder_lane", "anthropic_api"):
        reg.register(pr.ProviderRecord(
            schema_version=1, provider_id=f"id_{ptype}",
            provider_type=ptype, daily_budget_calls=100,
            warm=True, capabilities=(),
        ))
    decision = proute.route(task_class="code_or_repair", registry=reg)
    assert decision.chosen_provider_type == "claude_code_builder_lane"


def test_router_honors_agent_id_hint():
    reg = pr.ProviderRegistry()
    reg.register(pr.ProviderRecord(
        schema_version=1, provider_id="opus_factory",
        provider_type="anthropic_api", daily_budget_calls=100,
        warm=True, capabilities=(),
    ))
    reg.register(pr.ProviderRecord(
        schema_version=1, provider_id="claude_default",
        provider_type="claude_code_builder_lane",
        daily_budget_calls=100, warm=True, capabilities=(),
    ))
    decision = proute.route(task_class="code_or_repair", registry=reg,
                                agent_id_hint="opus_factory")
    assert decision.chosen_provider_id == "opus_factory"


def test_router_falls_through_when_top_priority_unavailable():
    reg = pr.ProviderRegistry()
    # claude builder lane absent → router falls to anthropic_api
    reg.register(pr.ProviderRecord(
        schema_version=1, provider_id="opus",
        provider_type="anthropic_api", daily_budget_calls=100,
        warm=True, capabilities=(),
    ))
    decision = proute.route(task_class="code_or_repair", registry=reg)
    assert decision.chosen_provider_type == "anthropic_api"


def test_router_returns_none_when_chain_empty():
    reg = pr.ProviderRegistry()
    decision = proute.route(task_class="code_or_repair", registry=reg)
    assert decision.chosen_provider_id is None


def test_router_unknown_task_class():
    reg = pr.ProviderRegistry()
    decision = proute.route(task_class="bogus", registry=reg)
    assert decision.chosen_provider_id is None
    assert "unknown task_class" in decision.rationale


def test_router_priority_for_bulk_classification():
    reg = pr.ProviderRegistry()
    for ptype in PROVIDERS:
        reg.register(pr.ProviderRecord(
            schema_version=1, provider_id=f"id_{ptype}",
            provider_type=ptype, daily_budget_calls=10,
            warm=True, capabilities=(),
        ))
    decision = proute.route(task_class="bulk_classification", registry=reg)
    # bulk_classification prefers local_model_service first
    assert decision.chosen_provider_type == "local_model_service"


def test_task_class_priority_lists_all_4_providers():
    for tc, chain in TASK_CLASS_PRIORITY.items():
        assert set(chain) == set(PROVIDERS)


# ═══════════════════ agent_pool_registry ══════════════════════════

def test_agent_record_rejects_invalid_max_concurrent():
    with pytest.raises(ValueError):
        apr.AgentRecord(
            schema_version=1, agent_id="x",
            provider_type="anthropic_api",
            specialization_tags=(), preferred_capsules=(),
            context_pack_refs=(),
            max_concurrent_jobs=0, daily_budget_calls=10,
            warm_availability=True,
        )


def test_agent_pool_for_capsule_filters():
    reg = apr.AgentPoolRegistry()
    reg.register(apr.AgentRecord(
        schema_version=1, agent_id="opus_factory",
        provider_type="anthropic_api",
        specialization_tags=("factory",),
        preferred_capsules=("factory_v1",),
        context_pack_refs=(), max_concurrent_jobs=2,
        daily_budget_calls=50, warm_availability=True,
    ))
    reg.register(apr.AgentRecord(
        schema_version=1, agent_id="opus_personal",
        provider_type="anthropic_api",
        specialization_tags=("personal",),
        preferred_capsules=("personal_v1",),
        context_pack_refs=(), max_concurrent_jobs=2,
        daily_budget_calls=30, warm_availability=True,
    ))
    factory = reg.for_capsule("factory_v1")
    assert {a.agent_id for a in factory} == {"opus_factory"}


def test_agent_pool_for_specialization():
    reg = apr.AgentPoolRegistry()
    reg.register(apr.AgentRecord(
        schema_version=1, agent_id="x",
        provider_type="claude_code_builder_lane",
        specialization_tags=("repair", "code"),
        preferred_capsules=(), context_pack_refs=(),
        max_concurrent_jobs=1, daily_budget_calls=10,
        warm_availability=True,
    ))
    out = reg.for_specialization("repair")
    assert len(out) == 1


# ═══════════════════ request_pack_router ══════════════════════════

def test_make_request_id_deterministic():
    a = rpr.make_request(
        task_class="code_or_repair", intent="x",
        input_payload={"k": "v"},
    )
    b = rpr.make_request(
        task_class="code_or_repair", intent="x",
        input_payload={"k": "v"},
    )
    assert a.request_id == b.request_id


def test_make_request_no_runtime_mutation_const():
    r = rpr.make_request(task_class="code_or_repair", intent="x")
    assert r.no_runtime_mutation is True
    d = r.to_dict()
    assert d["no_runtime_mutation"] is True


def test_make_request_rejects_unknown_task_class():
    with pytest.raises(ValueError):
        rpr.make_request(task_class="bogus", intent="x")


# ═══════════════════ response_normalizer ══════════════════════════

def test_normalize_starts_in_raw_quarantine():
    r = rn.normalize(
        request_id="req_x", provider_used="anthropic_api",
        raw_payload={"k": "v"}, ts_iso="t",
    )
    assert r.trust_layer_state == "raw_quarantine"
    assert r.no_direct_mutation is True


def test_normalize_response_id_deterministic():
    a = rn.normalize(request_id="r", provider_used="p",
                          raw_payload={}, ts_iso="t")
    b = rn.normalize(request_id="r", provider_used="p",
                          raw_payload={}, ts_iso="t")
    assert a.response_id == b.response_id


# ═══════════════════ provider_budget_engine ═══════════════════════

def test_budget_consume_within_cap():
    state = pbe.ProviderBudgetState()
    state.register(pbe.ProviderBudgetEntry(
        provider_id="x", daily_cap_calls=10,
    ))
    state, err = state.consume("x", 3)
    assert err is None
    assert state.entries["x"].consumed_calls == 3


def test_budget_consume_over_cap_blocked():
    state = pbe.ProviderBudgetState()
    state.register(pbe.ProviderBudgetEntry(
        provider_id="x", daily_cap_calls=5,
    ))
    state, err = state.consume("x", 6)
    assert err == "over_consumed"


def test_budget_consume_unknown_provider():
    state = pbe.ProviderBudgetState()
    _, err = state.consume("ghost", 1)
    assert err == "unknown_provider"


def test_budget_reset_daily():
    state = pbe.ProviderBudgetState()
    state.register(pbe.ProviderBudgetEntry(
        provider_id="x", daily_cap_calls=10, consumed_calls=8,
    ))
    state.reset_daily()
    assert state.entries["x"].consumed_calls == 0


# ═══════════════════ knowledge_extractor ══════════════════════════

def test_extractor_handles_full_payload():
    response = rn.normalize(
        request_id="r", provider_used="claude_code_builder_lane",
        raw_payload={
            "extracted_facts": [
                {"claim": "thermal scoring drifts on cold days",
                 "confidence": 0.8},
            ],
            "extracted_solver_specs": [
                {"solver_family": "scalar_unit_conversion",
                 "spec": {"in": "celsius", "out": "kelvin"}},
            ],
            "extracted_lessons": [
                {"lesson_kind": "design_note",
                 "content": "prefer deterministic specs"},
            ],
        },
        ts_iso="t",
    )
    out = ke.extract(response)
    assert len(out["facts"]) == 1
    assert len(out["solver_specs"]) == 1
    assert len(out["lessons"]) == 1


def test_extractor_handles_empty_payload():
    response = rn.normalize(
        request_id="r", provider_used="anthropic_api",
        raw_payload={}, ts_iso="t",
    )
    out = ke.extract(response)
    assert out["facts"] == []
    assert out["solver_specs"] == []
    assert out["lessons"] == []


def test_extractor_skips_invalid_items():
    response = rn.normalize(
        request_id="r", provider_used="gpt_api",
        raw_payload={
            "extracted_facts": [
                {"confidence": 0.5},   # missing claim → skipped
                "not a dict",          # invalid type → skipped
                {"claim": "valid claim text", "confidence": 0.7},
            ],
        }, ts_iso="t",
    )
    out = ke.extract(response)
    assert len(out["facts"]) == 1


# ═══════════════════ api_consultant trust gate ════════════════════

def _resp(payload, provider="anthropic_api"):
    return rn.normalize(
        request_id="req_x", provider_used=provider,
        raw_payload=payload, ts_iso="t",
    )


def test_trust_gate_empty_response_blocks_at_internal_consistency():
    response = _resp({})
    extracted = ke.extract(response)
    gate = ac.evaluate_trust_gate(response=response, extracted=extracted)
    assert gate.layer_reached == "raw_quarantine"


def test_trust_gate_invalid_confidence_blocks():
    response = _resp({
        "extracted_facts": [
            {"claim": "x" * 20, "confidence": 5.0},   # invalid
        ],
    })
    extracted = ke.extract(response)
    # Manually patch the extracted fact's confidence to bypass extractor
    # validation — test the gate's own check:
    extracted["facts"][0] = ke.ExtractedFact(
        extracted_id="x", claim="x"*20, confidence=5.0,
        source_provider="p",
    )
    gate = ac.evaluate_trust_gate(response=response, extracted=extracted)
    assert gate.blocked_at == "internal_consistency"


def test_trust_gate_short_claim_blocks_cross_check():
    response = _resp({
        "extracted_facts": [{"claim": "x", "confidence": 0.7}],
    })
    extracted = ke.extract(response)
    gate = ac.evaluate_trust_gate(response=response, extracted=extracted)
    assert gate.blocked_at == "existing_knowledge_cross_check"


def test_trust_gate_no_corroboration_blocks_at_layer_4():
    response = _resp({
        "extracted_facts": [
            {"claim": "thermal drift observed today",
             "confidence": 0.8},
        ],
    })
    extracted = ke.extract(response)
    gate = ac.evaluate_trust_gate(response=response, extracted=extracted)
    assert gate.blocked_at == "multi_source_corroboration"


def test_trust_gate_low_confidence_blocks_at_layer_5():
    response = _resp({
        "extracted_facts": [
            {"claim": "thermal drift observed today",
             "confidence": 0.3},
        ],
    }, provider="anthropic_api")
    other = _resp({
        "extracted_facts": [
            {"claim": "other corroborating fact",
             "confidence": 0.4},
        ],
    }, provider="gpt_api")
    extracted = ke.extract(response)
    gate = ac.evaluate_trust_gate(
        response=response, extracted=extracted,
        corroborating_responses=[other],
        calibration_threshold=0.6,
    )
    assert gate.blocked_at == "calibration_threshold"


def test_trust_gate_all_passes_blocks_at_human_gated():
    """Even when all auto-checks pass, human_gated layer remains as
    the final approval boundary."""
    response = _resp({
        "extracted_facts": [
            {"claim": "well-formed claim about external state",
             "confidence": 0.85},
        ],
    }, provider="claude_code_builder_lane")
    other = _resp({
        "extracted_facts": [
            {"claim": "corroborating fact from second provider",
             "confidence": 0.75},
        ],
    }, provider="gpt_api")
    extracted = ke.extract(response)
    gate = ac.evaluate_trust_gate(
        response=response, extracted=extracted,
        corroborating_responses=[other],
        calibration_threshold=0.6,
    )
    assert gate.layer_reached == "calibration_threshold"
    assert gate.blocked_at == "human_gated"


# ═══════════════════ consult end-to-end ══════════════════════════-

def test_consult_emits_record():
    response = _resp({
        "extracted_facts": [
            {"claim": "well formed external claim",
             "confidence": 0.8},
        ],
        "extracted_lessons": [
            {"lesson_kind": "design_note",
             "content": "deterministic specs preferred"},
        ],
    })
    rec = ac.consult(response=response)
    assert rec.consultation_id.startswith("consult_")
    assert rec.trust_layer_reached in TRUST_GATE_LAYERS or \
        rec.trust_layer_reached in ("calibration_threshold",
                                       "existing_knowledge_cross_check")
    assert len(rec.extracted_lessons) == 1


# ═══════════════════ offline_replay_engine ════════════════════════

def test_offline_replay_loads_cache(tmp_path):
    p = tmp_path / "cache.jsonl"
    rec = ac.consult(response=_resp({
        "extracted_facts": [{"claim": "well formed claim",
                              "confidence": 0.8}],
    }))
    p.write_text(json.dumps(rec.to_dict()) + "\n", encoding="utf-8")
    loaded = ore.load_cache(p)
    assert len(loaded) == 1
    assert loaded[0].consultation_id == rec.consultation_id


def test_offline_replay_summary_aggregates(tmp_path):
    p = tmp_path / "cache.jsonl"
    rec = ac.consult(response=_resp({
        "extracted_facts": [{"claim": "well formed claim",
                              "confidence": 0.8}],
    }))
    p.write_text(json.dumps(rec.to_dict()) + "\n", encoding="utf-8")
    records = ore.load_cache(p)
    summary = ore.replay_summary(records)
    assert summary["records_total"] == 1
    assert summary["facts_total"] == 1


def test_offline_replay_handles_missing_file():
    out = ore.load_cache(Path("/nonexistent_path_xyz.jsonl"))
    assert out == []


# ═══════════════════ source safety ═════════════════════════════════

def test_phase_j_source_safety():
    """Source must NOT make live LLM calls; the plane is request-pack
    driven, never direct API invocation."""
    forbidden = ["import faiss", "from waggledance.runtime",
                  "ollama.generate(", "openai.chat",
                  "anthropic.messages.create",
                  "anthropic.Anthropic(",
                  "requests.post(", "axiom_write(",
                  "promote_to_runtime("]
    for pkg_name in ("provider_plane", "api_distillation"):
        pkg = ROOT / "waggledance" / "core" / pkg_name
        for p in pkg.glob("*.py"):
            text = p.read_text(encoding="utf-8")
            for pat in forbidden:
                assert pat not in text, f"{p.name}: {pat}"


def test_no_direct_mutation_const_in_response():
    r = rn.normalize(request_id="r", provider_used="p",
                          raw_payload={}, ts_iso="t")
    assert r.no_direct_mutation is True
    src = (ROOT / "waggledance" / "core" / "provider_plane"
            / "response_normalizer.py").read_text(encoding="utf-8")
    assert "no_direct_mutation=False" not in src
    assert "no_direct_mutation = False" not in src
