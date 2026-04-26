"""Targeted tests for waggledance/core/autonomy/policy_core.py."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.autonomy import policy_core as pc


def test_make_rule_rejects_forbidden_verbs():
    for verb in pc.ADAPTIVE_VERBS_FORBIDDEN:
        with pytest.raises(pc.PolicyValidationError):
            pc.make_policy_rule(
                refines_hard_rule_id="domain_neutrality",
                verb=verb, statement="x", source="human",
            )


def test_make_rule_rejects_unknown_verb():
    with pytest.raises(pc.PolicyValidationError, match="unknown verb"):
        pc.make_policy_rule(
            refines_hard_rule_id="domain_neutrality",
            verb="invent_new_verb", statement="x", source="human",
        )


def test_make_rule_requires_refines():
    with pytest.raises(pc.PolicyValidationError):
        pc.make_policy_rule(
            refines_hard_rule_id="", verb="tighten",
            statement="x", source="human",
        )


def test_make_rule_happy_path():
    r = pc.make_policy_rule(
        refines_hard_rule_id="budget_respect",
        verb="tighten",
        statement="FORBID:provider_plane:consultation_request when budget_remaining<2",
        source="human",
    )
    assert r.verb == "tighten"
    assert len(r.rule_id) == 12


def test_rule_id_is_deterministic():
    a = pc.compute_rule_id(refines="x", verb="tighten",
                              statement="s", source="src",
                              capsule_scope=None)
    b = pc.compute_rule_id(refines="x", verb="tighten",
                              statement="s", source="src",
                              capsule_scope=None)
    assert a == b
    c = pc.compute_rule_id(refines="x", verb="tighten",
                              statement="s", source="src",
                              capsule_scope="factory_v1")
    assert a != c


def test_load_hard_rules_from_real_constitution():
    p = ROOT / "waggledance" / "core" / "autonomy" / "constitution.yaml"
    rules = pc.load_hard_rules(p)
    ids = {r.id for r in rules}
    assert "no_runtime_mutation" in ids
    assert "action_gate_is_only_exit" in ids
    assert "no_constitution_self_mutation" in ids
    assert "domain_neutrality" in ids
    assert "deterministic_tick" in ids
    for r in rules:
        assert r.severity in ("warning", "recoverable", "fatal", "info")


def test_evaluate_blocks_when_no_runtime_mutation_false():
    rules = pc.load_hard_rules(
        ROOT / "waggledance" / "core" / "autonomy" / "constitution.yaml"
    )
    ev = pc.evaluate(
        action_id="a"*12, action_kind="builder_request",
        action_lane="builder_lane",
        requires_human_review=True,
        no_runtime_mutation=False,   # ← violation
        hard_rules=rules,
    )
    assert ev.allowed is False
    assert "action_gate_is_only_exit" in ev.blocking_rule_ids


def test_evaluate_allows_clean_action():
    rules = pc.load_hard_rules(
        ROOT / "waggledance" / "core" / "autonomy" / "constitution.yaml"
    )
    ev = pc.evaluate(
        action_id="a"*12, action_kind="consultation_request",
        action_lane="provider_plane",
        requires_human_review=True,
        no_runtime_mutation=True,
        hard_rules=rules,
    )
    assert ev.allowed is True
    assert ev.blocking_rule_ids == ()


def test_adaptive_advisory_does_not_block():
    rules = pc.load_hard_rules(
        ROOT / "waggledance" / "core" / "autonomy" / "constitution.yaml"
    )
    advisory = pc.make_policy_rule(
        refines_hard_rule_id="domain_neutrality",
        verb="add_advisory_check",
        statement="prefer neutral capsule names in intent text",
        source="human",
    )
    ev = pc.evaluate(
        action_id="a"*12, action_kind="ingest_request",
        action_lane="ingestion",
        requires_human_review=True, no_runtime_mutation=True,
        hard_rules=rules, adaptive_rules=(advisory,),
    )
    assert ev.allowed is True
    assert advisory.rule_id in ev.advisory_rule_ids


def test_adaptive_tighten_can_block_specific_lane_kind():
    rules = pc.load_hard_rules(
        ROOT / "waggledance" / "core" / "autonomy" / "constitution.yaml"
    )
    tight = pc.make_policy_rule(
        refines_hard_rule_id="budget_respect",
        verb="tighten",
        statement="FORBID:provider_plane:consultation_request",
        source="proposal:test",
    )
    ev = pc.evaluate(
        action_id="a"*12, action_kind="consultation_request",
        action_lane="provider_plane",
        requires_human_review=True, no_runtime_mutation=True,
        hard_rules=rules, adaptive_rules=(tight,),
    )
    assert ev.allowed is False
    assert tight.rule_id in ev.blocking_rule_ids
    # But other lane/kind combos still allowed
    ev2 = pc.evaluate(
        action_id="b"*12, action_kind="builder_request",
        action_lane="builder_lane",
        requires_human_review=True, no_runtime_mutation=True,
        hard_rules=rules, adaptive_rules=(tight,),
    )
    assert ev2.allowed is True


def test_capsule_scope_only_applies_to_matching_capsule():
    rules = pc.load_hard_rules(
        ROOT / "waggledance" / "core" / "autonomy" / "constitution.yaml"
    )
    factory_only = pc.make_policy_rule(
        refines_hard_rule_id="budget_respect",
        verb="tighten",
        statement="FORBID:builder_lane:builder_request",
        source="capsule:factory_v1",
        capsule_scope="factory_v1",
    )
    # Wrong capsule → rule does not apply
    ev = pc.evaluate(
        action_id="a"*12, action_kind="builder_request",
        action_lane="builder_lane",
        requires_human_review=True, no_runtime_mutation=True,
        hard_rules=rules, adaptive_rules=(factory_only,),
        capsule_context="personal_v1",
    )
    assert ev.allowed is True
    # Matching capsule → rule applies
    ev2 = pc.evaluate(
        action_id="b"*12, action_kind="builder_request",
        action_lane="builder_lane",
        requires_human_review=True, no_runtime_mutation=True,
        hard_rules=rules, adaptive_rules=(factory_only,),
        capsule_context="factory_v1",
    )
    assert ev2.allowed is False


def test_policy_source_safety():
    src = (ROOT / "waggledance" / "core" / "autonomy"
            / "policy_core.py").read_text(encoding="utf-8")
    for pat in ("import faiss", "ollama.generate(", "openai.chat",
                 "anthropic.messages", "requests.post(",
                 "axiom_write(", "promote_to_runtime("):
        assert pat not in src
    src_l = src.lower()
    for pat in ("bee ", "hive ", "honeycomb ", "swarm "):
        assert pat not in src_l


def test_rule_to_dict_includes_capsule_scope_only_when_set():
    a = pc.make_policy_rule(
        refines_hard_rule_id="domain_neutrality",
        verb="add_advisory_check", statement="x", source="human",
    )
    assert "capsule_scope" not in a.to_dict()
    b = pc.make_policy_rule(
        refines_hard_rule_id="domain_neutrality",
        verb="add_advisory_check", statement="x", source="human",
        capsule_scope="factory_v1",
    )
    assert b.to_dict()["capsule_scope"] == "factory_v1"
