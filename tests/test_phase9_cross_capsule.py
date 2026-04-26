"""Targeted tests for Phase 9 §Q Cross-Capsule Observer."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.cross_capsule import (
    AbstractPatternRecord,
    AbstractPatternRegistry,
    AbstractPatternRegistryError,
    CapsuleSignalSummary,
    CrossCapsuleObservation,
    CrossCapsuleObserver,
    CrossCapsuleObserverError,
    OBSERVABLE_PATTERN_KINDS,
)
from waggledance.core.cross_capsule.cross_capsule_observer import (
    compute_observation_id,
)


# ═══════════════════ CapsuleSignalSummary invariants ══════════════

def test_summary_must_be_redacted():
    with pytest.raises(CrossCapsuleObserverError, match="redacted"):
        CapsuleSignalSummary(
            capsule_id="c1",
            pattern_kinds=("recurring_oscillation",),
            window_id="w1",
            redacted=False,
        )


def test_summary_unknown_pattern_kind_rejected():
    with pytest.raises(CrossCapsuleObserverError, match="unknown pattern"):
        CapsuleSignalSummary(
            capsule_id="c1",
            pattern_kinds=("invented_kind",),
            window_id="w1",
            redacted=True,
        )


def test_summary_requires_capsule_id():
    with pytest.raises(CrossCapsuleObserverError, match="capsule_id"):
        CapsuleSignalSummary(
            capsule_id="", pattern_kinds=(), window_id="w1", redacted=True,
        )


def test_summary_requires_window_id():
    with pytest.raises(CrossCapsuleObserverError, match="window_id"):
        CapsuleSignalSummary(
            capsule_id="c1", pattern_kinds=(),
            window_id="", redacted=True,
        )


# ═══════════════════ CrossCapsuleObserver.observe ══════════════════

def _summary(capsule_id: str, kinds: tuple[str, ...],
              window_id: str = "w1") -> CapsuleSignalSummary:
    return CapsuleSignalSummary(
        capsule_id=capsule_id, pattern_kinds=kinds,
        window_id=window_id, redacted=True,
    )


def test_observer_returns_empty_when_no_summaries():
    o = CrossCapsuleObserver()
    assert o.observe(()) == ()


def test_observer_filters_single_capsule_patterns_by_default():
    o = CrossCapsuleObserver()  # min_capsules_for_pattern=2
    obs = o.observe([
        _summary("c1", ("recurring_oscillation",)),
    ])
    assert obs == ()


def test_observer_emits_pattern_when_two_capsules_share_kind():
    o = CrossCapsuleObserver()
    obs = o.observe([
        _summary("c1", ("recurring_oscillation",)),
        _summary("c2", ("recurring_oscillation",)),
    ])
    assert len(obs) == 1
    assert obs[0].pattern_kind == "recurring_oscillation"
    assert obs[0].participating_capsule_ids == ("c1", "c2")
    assert obs[0].occurrence_count == 2


def test_observer_buckets_multiple_kinds_independently():
    o = CrossCapsuleObserver()
    obs = o.observe([
        _summary("c1",
                  ("recurring_oscillation", "recurring_blind_spot_class")),
        _summary("c2",
                  ("recurring_oscillation",)),
        _summary("c3",
                  ("recurring_blind_spot_class",)),
    ])
    kinds = sorted(o.pattern_kind for o in obs)
    assert kinds == ["recurring_blind_spot_class", "recurring_oscillation"]


def test_observer_rejects_mixed_window_ids():
    o = CrossCapsuleObserver()
    with pytest.raises(CrossCapsuleObserverError,
                          match="multiple windows"):
        o.observe([
            _summary("c1", ("recurring_oscillation",), window_id="w1"),
            _summary("c2", ("recurring_oscillation",), window_id="w2"),
        ])


def test_observer_output_is_deterministic():
    o = CrossCapsuleObserver()
    inputs_a = [
        _summary("c2", ("recurring_oscillation",)),
        _summary("c1", ("recurring_oscillation",)),
    ]
    inputs_b = [
        _summary("c1", ("recurring_oscillation",)),
        _summary("c2", ("recurring_oscillation",)),
    ]
    assert o.observe(inputs_a)[0].to_dict() == \
            o.observe(inputs_b)[0].to_dict()


def test_observer_sets_invariant_flags_true():
    o = CrossCapsuleObserver()
    obs = o.observe([
        _summary("c1", ("recurring_proposal_bottleneck",)),
        _summary("c2", ("recurring_proposal_bottleneck",)),
    ])[0]
    assert obs.redacted is True
    assert obs.advisory_only is True
    assert obs.no_raw_data_leakage is True


def test_observer_higher_threshold_filters():
    o = CrossCapsuleObserver(min_capsules_for_pattern=3)
    obs = o.observe([
        _summary("c1", ("recurring_oscillation",)),
        _summary("c2", ("recurring_oscillation",)),
    ])
    assert obs == ()


def test_observer_dedups_capsule_ids_in_participating_list():
    """Same capsule reporting same kind in two summaries must not
    inflate the participating set."""
    o = CrossCapsuleObserver(min_capsules_for_pattern=2)
    # c1 appears twice, only c2 makes the second capsule
    obs = o.observe([
        _summary("c1", ("recurring_contradiction_pattern",)),
        _summary("c1", ("recurring_contradiction_pattern",)),
        _summary("c2", ("recurring_contradiction_pattern",)),
    ])
    assert len(obs) == 1
    assert obs[0].participating_capsule_ids == ("c1", "c2")


# ═══════════════════ CrossCapsuleObservation invariants ════════════

def test_observation_unknown_kind_rejected():
    with pytest.raises(CrossCapsuleObserverError):
        CrossCapsuleObservation(
            pattern_kind="bogus", participating_capsule_ids=("a", "b"),
            occurrence_count=2, window_id="w1",
            redacted=True, advisory_only=True, no_raw_data_leakage=True,
        )


def test_observation_zero_count_rejected():
    with pytest.raises(CrossCapsuleObserverError, match="occurrence_count"):
        CrossCapsuleObservation(
            pattern_kind="recurring_oscillation",
            participating_capsule_ids=("a", "b"),
            occurrence_count=0, window_id="w1",
            redacted=True, advisory_only=True, no_raw_data_leakage=True,
        )


def test_observation_to_dict_round_trip():
    obs = CrossCapsuleObservation(
        pattern_kind="recurring_oscillation",
        participating_capsule_ids=("a", "b"),
        occurrence_count=2, window_id="w1",
        redacted=True, advisory_only=True, no_raw_data_leakage=True,
    )
    d = obs.to_dict()
    assert d["pattern_kind"] == "recurring_oscillation"
    assert d["redacted"] is True
    json.dumps(d)


def test_compute_observation_id_deterministic():
    obs = CrossCapsuleObservation(
        pattern_kind="recurring_oscillation",
        participating_capsule_ids=("a", "b"),
        occurrence_count=2, window_id="w1",
        redacted=True, advisory_only=True, no_raw_data_leakage=True,
    )
    assert compute_observation_id(obs) == compute_observation_id(obs)


# ═══════════════════ AbstractPatternRegistry ══════════════════════

def _obs(kind: str = "recurring_oscillation",
          ids: tuple[str, ...] = ("a", "b"),
          window: str = "w1") -> CrossCapsuleObservation:
    return CrossCapsuleObservation(
        pattern_kind=kind, participating_capsule_ids=ids,
        occurrence_count=len(ids), window_id=window,
        redacted=True, advisory_only=True, no_raw_data_leakage=True,
    )


def test_registry_add_emits_record_with_invariants():
    reg = AbstractPatternRegistry()
    rec = reg.add(_obs())
    assert rec.redacted is True
    assert rec.advisory_only is True
    assert rec.no_raw_data_leakage is True
    assert rec.no_runtime_auto_action is True


def test_registry_add_rejects_duplicate():
    reg = AbstractPatternRegistry()
    reg.add(_obs())
    with pytest.raises(AbstractPatternRegistryError, match="duplicate"):
        reg.add(_obs())


def test_registry_by_kind_filters_correctly():
    reg = AbstractPatternRegistry()
    reg.add(_obs(kind="recurring_oscillation"))
    reg.add(_obs(kind="recurring_blind_spot_class",
                  ids=("c", "d")))
    osc = reg.by_kind("recurring_oscillation")
    bs = reg.by_kind("recurring_blind_spot_class")
    assert len(osc) == 1
    assert len(bs) == 1


def test_registry_by_kind_unknown_raises():
    reg = AbstractPatternRegistry()
    with pytest.raises(AbstractPatternRegistryError, match="unknown"):
        reg.by_kind("bogus")


def test_registry_to_json_byte_stable():
    a = AbstractPatternRegistry()
    b = AbstractPatternRegistry()
    a.add(_obs())
    b.add(_obs())
    assert a.to_json() == b.to_json()


def test_observable_pattern_kinds_locked_set():
    assert OBSERVABLE_PATTERN_KINDS == (
        "recurring_oscillation",
        "recurring_blind_spot_class",
        "recurring_proposal_bottleneck",
        "recurring_contradiction_pattern",
    )


# ═══════════════════ no-raw-data-leakage source-grep ══════════════

def test_cross_capsule_source_has_no_raw_data_or_runtime_imports():
    forbidden = [
        "import requests",
        "import httpx",
        "import urllib.request",
        "subprocess.run(",
        "subprocess.Popen(",
        "from waggledance.runtime",
        "import faiss",
        "import torch",
        "import transformers",
        "import openai",
        "import anthropic",
        "import ollama",
        "promote_to_runtime(",
        "raw_capsule_text",
    ]
    pkg = ROOT / "waggledance" / "core" / "cross_capsule"
    for p in pkg.glob("*.py"):
        text = p.read_text(encoding="utf-8")
        for pat in forbidden:
            assert pat not in text, f"{p.name}: {pat}"


def test_no_redacted_or_no_leak_false_in_source():
    pkg = ROOT / "waggledance" / "core" / "cross_capsule"
    for p in pkg.glob("*.py"):
        text = p.read_text(encoding="utf-8")
        assert "redacted=False" not in text
        assert "no_raw_data_leakage=False" not in text
        assert "advisory_only=False" not in text
        assert "no_runtime_auto_action=False" not in text


def test_deferred_doc_lists_six_topics():
    doc = (ROOT / "docs" / "architecture"
           / "HIGH_RISK_VARIANTS_DEFERRED.md").read_text(encoding="utf-8")
    for needle in (
        "parallel provider ensembles",
        "Predictive cache preheating",
        "no-human-in-loop micro-learning expansions".lower(),
        "Limited canary auto-promotion",
        "Advanced local model escalation",
        "Generative memory compression",
    ):
        assert needle.lower() in doc.lower(), needle


def test_experimental_profile_doc_specifies_contract():
    doc = (ROOT / "docs" / "architecture"
           / "EXPERIMENTAL_AUTONOMY_PROFILE.md").read_text(encoding="utf-8")
    for needle in (
        "profile.safe_default",
        "human_approval_id",
        "no_runtime_auto_promotion",
        "no_foundational_mutation",
        "no_raw_data_leakage",
        "Reversible",
    ):
        assert needle in doc, needle
