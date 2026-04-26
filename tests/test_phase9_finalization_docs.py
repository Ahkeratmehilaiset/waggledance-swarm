# SPDX-License-Identifier: Apache-2.0
"""Targeted tests for Phase 9 finalization docs (Prompt 2 prep + roadmap)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ═══════════════════ PROMPT_2_INPUTS_AND_CONTRACTS.md ══════════════

def _prompt2_doc() -> str:
    p = ROOT / "docs" / "architecture" / "PROMPT_2_INPUTS_AND_CONTRACTS.md"
    return p.read_text(encoding="utf-8")


def test_prompt2_doc_exists():
    p = ROOT / "docs" / "architecture" / "PROMPT_2_INPUTS_AND_CONTRACTS.md"
    assert p.exists()


def test_prompt2_doc_lists_all_preconditions():
    text = _prompt2_doc()
    for needle in (
        "Phases F–Q are review-green",
        "400h gauntlet campaign is finished or frozen",
        "Human review has happened",
        "No live LLM dependency",
    ):
        assert needle in text, needle


def test_prompt2_doc_specifies_approval_artifact_fields():
    text = _prompt2_doc()
    for field in (
        "human_approval_id",
        "approval_kind",
        "branch_under_review",
        "commit_under_review",
        "rationale",
        "rollback_target_sha",
        "no_runtime_auto_promotion",
        "no_main_branch_auto_merge",
        "no_foundational_mutation",
        "no_raw_data_leakage",
    ):
        assert field in text, field


def test_prompt2_doc_forbids_force_push():
    text = _prompt2_doc()
    assert "without `--force`" in text or "no force-push" in text.lower()


def test_prompt2_doc_forbids_full_pytest():
    text = _prompt2_doc()
    assert "full `pytest -q`" in text


def test_prompt2_doc_specifies_rollback_contract():
    text = _prompt2_doc()
    assert "Rollback contract" in text
    assert "previous_main_tip_sha" in text


def test_prompt2_doc_lists_escalation_triggers():
    text = _prompt2_doc()
    assert "human review" in text.lower()
    for needle in (
        "non-fast-forward",
        "force-push",
        "approval artifact is missing",
    ):
        assert needle in text, needle


def test_prompt2_doc_recommended_state_file_shape():
    text = _prompt2_doc()
    for field in (
        "prompt_2_atomic_flip_state.json",
        "previous_main_tip_sha",
        "flip_target_sha",
        "post_flip_verified",
        "rollback_executed",
    ):
        assert field in text, field


# ═══════════════════ PHASE_9_ROADMAP.md ════════════════════════════

def _roadmap_doc() -> str:
    p = ROOT / "docs" / "architecture" / "PHASE_9_ROADMAP.md"
    return p.read_text(encoding="utf-8")


def test_roadmap_doc_exists():
    p = ROOT / "docs" / "architecture" / "PHASE_9_ROADMAP.md"
    assert p.exists()


def test_roadmap_lists_all_16_phases():
    text = _roadmap_doc()
    for phase in ("F", "G", "H", "I", "P", "V", "J",
                   "U1", "U2", "U3", "L", "K", "M", "O", "N", "Q"):
        # Format used in the table: "| F | ", "| U1 | ", etc.
        assert f"| {phase} |" in text, phase


def test_roadmap_states_total_test_count():
    text = _roadmap_doc()
    assert "626" in text
    assert "626/626" in text


def test_roadmap_documents_cross_phase_invariants():
    text = _roadmap_doc()
    for needle in (
        "no_runtime_auto_promotion",
        "no_main_branch_auto_merge",
        "no_foundational_mutation",
        "no_raw_data_leakage",
    ):
        assert needle in text, needle


def test_roadmap_lists_what_is_NOT_in_branch():
    text = _roadmap_doc()
    assert "NOT in this branch" in text
    for needle in (
        "atomic runtime flip",
        "parallel provider ensembles",
        "Predictive cache preheating",
        "experimental autonomy profile",
        "Generative memory compression",
    ):
        assert needle.lower() in text.lower(), needle


def test_roadmap_acceptance_check_table_present():
    text = _roadmap_doc()
    assert "Acceptance check" in text
    assert "MASTER ACCEPTANCE CRITERIA" in text


def test_roadmap_recommends_next_step():
    text = _roadmap_doc()
    assert "next post-campaign step" in text.lower()
    assert "approval artifact" in text.lower()
    assert "Prompt 2" in text


# ═══════════════════ State.json reflects all 16 phases ═════════════

def test_state_file_reflects_all_16_phases():
    state_path = (ROOT / "docs" / "runs"
                    / "phase9_autonomy_fabric_state.json")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    completed = state.get("completed_phases", [])
    # There may be sub-component entries for Phase F; require that at
    # least one entry exists for each top-level phase letter.
    text = "\n".join(completed)
    for phase in ("F.", "G.", "H.", "I.", "P.", "V.", "J.",
                   "U1.", "U2.", "U3.", "L.", "K.", "M.", "O.", "N.", "Q."):
        assert phase in text, phase


def test_state_file_next_action_says_complete():
    state_path = (ROOT / "docs" / "runs"
                    / "phase9_autonomy_fabric_state.json")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    nra = state.get("next_recommended_action", "")
    assert "16 PHASES COMPLETE" in nra or "ALL 16 PHASES" in nra
