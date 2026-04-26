# SPDX-License-Identifier: Apache-2.0
"""Targeted tests for Phase 9 §M Promotion Ladder."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.promotion import (
    RUNTIME_STAGES,
    STAGE_CRITERIA,
    STAGES,
    ladder as pl,
    rollback_engine as re_,
    stage_validators as sv,
)


# ═══════════════════ ladder structure ══════════════════════════════

def test_14_stages_in_order():
    assert len(STAGES) == 14
    # Spot check critical ordering
    idx = STAGES.index
    assert idx("curiosity") < idx("tension") < idx("dream_target") \
        < idx("meta_proposal") < idx("human_review") \
        < idx("post_campaign_runtime_candidate") < idx("canary_cell") \
        < idx("limited_runtime") < idx("full_runtime")


def test_runtime_stages_subset():
    for s in RUNTIME_STAGES:
        assert s in STAGES
    assert "human_review" not in RUNTIME_STAGES


def test_every_stage_except_archived_has_criteria_or_root():
    """curiosity is the root; archived is special; all others have
    explicit criteria."""
    for stage in STAGES:
        if stage in ("curiosity", "archived"):
            continue
        assert stage in STAGE_CRITERIA
        assert len(STAGE_CRITERIA[stage]) >= 1


# ═══════════════════ stage_validators ══════════════════════════════

def test_run_unknown_criterion():
    ok, msg = sv.run_criterion("invent_criterion", {})
    assert not ok
    assert "unknown criterion" in msg


def test_from_stage_validators():
    ok, _ = sv.run_criterion("from_stage_is_curiosity",
                                  {"from_stage": "curiosity"})
    assert ok
    ok, _ = sv.run_criterion("from_stage_is_curiosity",
                                  {"from_stage": "tension"})
    assert not ok


def test_passes_proposal_gate_validator():
    ok, _ = sv.run_criterion(
        "passes_proposal_gate",
        {"collapse_verdict": "ACCEPT_CANDIDATE"},
    )
    assert ok
    ok, _ = sv.run_criterion(
        "passes_proposal_gate",
        {"collapse_verdict": "REJECT_HARD"},
    )
    assert not ok


def test_human_approval_validator():
    ok, _ = sv.run_criterion("human_approval_id_present",
                                  {"human_approval_id": "human:r:t"})
    assert ok
    ok, _ = sv.run_criterion("human_approval_id_present",
                                  {"human_approval_id": ""})
    assert not ok


def test_no_critical_regressions_validator():
    ok, _ = sv.run_criterion("no_critical_regressions",
                                  {"critical_regressions": 0})
    assert ok
    ok, _ = sv.run_criterion("no_critical_regressions",
                                  {"critical_regressions": 1})
    assert not ok


def test_run_all_returns_satisfied_and_failed():
    ctx = {"from_stage": "curiosity", "human_approval_id": ""}
    sat, fail = sv.run_all(
        ("from_stage_is_curiosity", "human_approval_id_present"),
        ctx,
    )
    assert "from_stage_is_curiosity" in sat
    assert "human_approval_id_present" in fail


# ═══════════════════ ladder.attempt_promotion ═════════════════════

def test_attempt_promotion_rejects_unknown_to_stage():
    with pytest.raises(pl.PromotionViolation, match="unknown to_stage"):
        pl.attempt_promotion(
            ir_id="x", from_stage="curiosity",
            to_stage="bogus", ts_iso="t",
        )


def test_attempt_promotion_rejects_unknown_from_stage():
    with pytest.raises(pl.PromotionViolation, match="unknown from_stage"):
        pl.attempt_promotion(
            ir_id="x", from_stage="bogus",
            to_stage="tension", ts_iso="t",
        )


def test_curiosity_to_tension_admitted():
    t = pl.attempt_promotion(
        ir_id="x", from_stage="curiosity", to_stage="tension",
        ts_iso="t",
    )
    assert pl.transition_admitted(t)
    assert t.no_runtime_auto_promotion is True
    assert t.no_bypass is True


def test_dream_target_requires_deferred_to_dream():
    """tension → dream_target needs resolution_path = deferred_to_dream."""
    t_ok = pl.attempt_promotion(
        ir_id="x", from_stage="tension", to_stage="dream_target",
        ts_iso="t",
        ctx={"tension_resolution_path": "deferred_to_dream"},
    )
    assert pl.transition_admitted(t_ok)
    t_fail = pl.attempt_promotion(
        ir_id="x", from_stage="tension", to_stage="dream_target",
        ts_iso="t",
        ctx={"tension_resolution_path": "merge_evidence"},
    )
    assert not pl.transition_admitted(t_fail)


def test_runtime_stage_requires_human_approval():
    """post_campaign_runtime_candidate REFUSES without
    human_approval_id even if all other criteria pass."""
    t = pl.attempt_promotion(
        ir_id="x", from_stage="human_review",
        to_stage="post_campaign_runtime_candidate", ts_iso="t",
        ctx={},
    )
    assert not pl.transition_admitted(t)
    assert "human_approval_id" in " ".join(t.criteria_failed)


def test_runtime_stage_admitted_with_human_approval():
    t = pl.attempt_promotion(
        ir_id="x", from_stage="human_review",
        to_stage="post_campaign_runtime_candidate", ts_iso="t",
        ctx={"human_approval_id": "human:reviewer:2026-04-26"},
    )
    assert pl.transition_admitted(t)
    assert t.human_approval_id == "human:reviewer:2026-04-26"


def test_full_runtime_requires_all_observations():
    ctx_partial = {
        "human_approval_id": "human:r:t",
        "limited_runtime_observation_window_passed": False,
        "critical_regressions": 0,
    }
    t = pl.attempt_promotion(
        ir_id="x", from_stage="limited_runtime",
        to_stage="full_runtime", ts_iso="t",
        ctx=ctx_partial,
    )
    assert not pl.transition_admitted(t)


def test_full_runtime_admitted_with_clean_history():
    ctx = {
        "human_approval_id": "human:r:t",
        "limited_runtime_observation_window_passed": True,
        "critical_regressions": 0,
    }
    t = pl.attempt_promotion(
        ir_id="x", from_stage="limited_runtime",
        to_stage="full_runtime", ts_iso="t", ctx=ctx,
    )
    assert pl.transition_admitted(t)


def test_transition_id_deterministic():
    a = pl.compute_transition_id(
        ir_id="x", from_stage="curiosity", to_stage="tension",
        ts_iso="t",
    )
    b = pl.compute_transition_id(
        ir_id="x", from_stage="curiosity", to_stage="tension",
        ts_iso="t",
    )
    assert a == b


def test_transition_to_dict_carries_const_invariants():
    t = pl.attempt_promotion(
        ir_id="x", from_stage="curiosity", to_stage="tension",
        ts_iso="t",
    )
    d = t.to_dict()
    assert d["no_runtime_auto_promotion"] is True
    assert d["no_bypass"] is True


# ═══════════════════ bypass detection ══════════════════════════════

def test_detect_bypass_curiosity_to_full_runtime():
    """Skipping all intermediate stages must be flagged as a bypass."""
    t = pl.attempt_promotion(
        ir_id="x", from_stage="curiosity", to_stage="full_runtime",
        ts_iso="t",
        ctx={"human_approval_id": "human:r:t"},
    )
    bypass = pl.detect_bypass(t)
    assert bypass is not None
    assert "bypass" in bypass.lower()


def test_detect_bypass_single_step_ok():
    t = pl.attempt_promotion(
        ir_id="x", from_stage="curiosity", to_stage="tension",
        ts_iso="t",
    )
    assert pl.detect_bypass(t) is None


def test_detect_bypass_to_archived_allowed_anywhere():
    """Archive can happen from any stage; not a bypass."""
    t = pl.attempt_promotion(
        ir_id="x", from_stage="meta_proposal", to_stage="archived",
        ts_iso="t",
    )
    assert pl.detect_bypass(t) is None


# ═══════════════════ rollback_engine ═══════════════════════════════

def test_rollback_to_earlier_stage_ok():
    plan = re_.plan_rollback(
        ir_id="x", from_stage="meta_proposal",
        rollback_target_stage="replay", ts_iso="t",
    )
    assert plan.rollback_target_stage == "replay"
    assert plan.no_runtime_auto_promotion is True


def test_rollback_from_runtime_stage_requires_human():
    with pytest.raises(re_.RollbackViolation,
                          match="requires human_approval_id"):
        re_.plan_rollback(
            ir_id="x", from_stage="canary_cell",
            rollback_target_stage="post_campaign_runtime_candidate",
            ts_iso="t",
        )


def test_rollback_from_runtime_admitted_with_human_id():
    plan = re_.plan_rollback(
        ir_id="x", from_stage="canary_cell",
        rollback_target_stage="post_campaign_runtime_candidate",
        ts_iso="t",
        human_approval_id="human:r:t",
        rationale="canary metric regression",
    )
    assert plan.human_approval_id == "human:r:t"


def test_rollback_to_self_rejected():
    with pytest.raises(re_.RollbackViolation,
                          match="cannot equal from_stage"):
        re_.plan_rollback(
            ir_id="x", from_stage="replay",
            rollback_target_stage="replay", ts_iso="t",
        )


def test_rollback_unknown_stage_rejected():
    with pytest.raises(re_.RollbackViolation,
                          match="unknown"):
        re_.plan_rollback(
            ir_id="x", from_stage="bogus",
            rollback_target_stage="curiosity", ts_iso="t",
        )


# ═══════════════════ no auto-promotion / no bypass invariants ────-

def test_no_auto_promotion_const_in_source():
    pkg = ROOT / "waggledance" / "core" / "promotion"
    for p in pkg.glob("*.py"):
        text = p.read_text(encoding="utf-8")
        assert "no_runtime_auto_promotion=False" not in text
        assert "no_bypass=False" not in text


def test_promotion_source_has_no_runtime_or_llm_calls():
    forbidden = ["import faiss", "from waggledance.runtime",
                  "ollama.generate(", "openai.chat",
                  "anthropic.messages", "requests.post(",
                  "axiom_write(", "promote_to_runtime("]
    pkg = ROOT / "waggledance" / "core" / "promotion"
    for p in pkg.glob("*.py"):
        text = p.read_text(encoding="utf-8")
        for pat in forbidden:
            assert pat not in text, f"{p.name}: {pat}"


# ═══════════════════ schema enums ══════════════════════════════════

def test_schema_carries_const_invariants():
    schema = json.loads((ROOT / "schemas" / "promotion_ladder.schema.json")
                          .read_text(encoding="utf-8"))
    assert schema["properties"]["no_runtime_auto_promotion"]["const"] is True
    assert schema["properties"]["no_bypass"]["const"] is True
