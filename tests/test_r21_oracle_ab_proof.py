"""R21.1 — unit tests for the oracle-backed A/B proof harness."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))


# ─── Oracle loader ───────────────────────────────────────────────

def test_load_oracle_corpus_returns_routable_files():
    """tests/oracle/ has 15 .yaml files: 14 routable solver oracles plus
    _off_domain.yaml (special, no cell). The loader must skip files
    whose name starts with `_` AND files with no `cell:` field."""
    import run_r21_oracle_ab_proof as proof  # type: ignore[import-not-found]
    oracles = proof.load_oracle_corpus(REPO_ROOT / "tests" / "oracle")
    assert len(oracles) == 14
    for o in oracles:
        assert o["cell"], f"missing cell in {o.get('file')}"
        assert o["positive"], f"empty positives in {o.get('file')}"
        assert o["negative"], f"empty negatives in {o.get('file')}"


def test_load_oracle_corpus_excludes_off_domain():
    """The `_off_domain.yaml` file is special and must NOT be in the
    loaded corpus (it doesn't have a single `cell` to route to)."""
    import run_r21_oracle_ab_proof as proof
    oracles = proof.load_oracle_corpus(REPO_ROOT / "tests" / "oracle")
    files = {o["file"] for o in oracles}
    assert "_off_domain.yaml" not in files


# ─── quality_arm ──────────────────────────────────────────────────

def test_quality_arm_perfect_routing_score_is_one():
    import run_r21_oracle_ab_proof as proof
    oracles = [{
        "file": "f.yaml",
        "cell": "hub",
        "positive": ["a", "b"],
        "negative": ["x", "y"],
    }]

    def perfect(query):
        return "hub" if query in ("a", "b") else "other"

    metrics = proof.quality_arm(oracles, perfect)
    assert metrics["quality"] == 1.0
    assert metrics["micro_pos_correct"] == 2
    assert metrics["micro_neg_correct"] == 2


def test_quality_arm_zero_routing_score_is_zero():
    import run_r21_oracle_ab_proof as proof
    oracles = [{
        "file": "f.yaml",
        "cell": "hub",
        "positive": ["a", "b"],
        "negative": ["x", "y"],
    }]

    # Always returns the wrong cell for positives, the right cell for
    # negatives — both halves of the metric score 0.
    def worst(query):
        return "hub" if query in ("x", "y") else "other"

    metrics = proof.quality_arm(oracles, worst)
    assert metrics["quality"] == 0.0


def test_quality_arm_macro_average_across_files():
    """Macro-average treats each oracle file equally, regardless of
    how many utterances it has. A small file scoring 1.0 and a big
    file scoring 0.0 average to 0.5, NOT a tiny number weighted by
    utterance count."""
    import run_r21_oracle_ab_proof as proof
    oracles = [
        {
            "file": "small.yaml", "cell": "a",
            "positive": ["small_pos_1"], "negative": ["small_neg_1"],
        },
        {
            "file": "big.yaml", "cell": "b",
            "positive": [f"big_p{i}" for i in range(50)],
            "negative": [f"big_n{i}" for i in range(50)],
        },
    ]

    def small_perfect_big_zero(query):
        # Small file: positive routes to "a", negative rejects (returns "z").
        if query == "small_pos_1":
            return "a"
        if query == "small_neg_1":
            return "z"
        # Big file: positive routes wrong ("z" not "b"), negative routes
        # to "b" (FAIL — should reject), giving 0/50 + 0/50 = 0.0.
        if query.startswith("big_p"):
            return "z"
        return "b"

    metrics = proof.quality_arm(oracles, small_perfect_big_zero)
    # small file: pos_correct=1/1, neg_correct=1/1 → file_score=1.0
    # big file: pos_correct=0/50, neg_correct=0/50 → file_score=0.0
    # macro avg = 0.5
    assert metrics["quality"] == 0.5


def test_quality_arm_symmetric_pos_neg():
    """The metric weights positive routing accuracy and negative
    rejection accuracy equally. A function that's perfect on
    positives but terrible on negatives scores 0.5, NOT 1.0."""
    import run_r21_oracle_ab_proof as proof
    oracles = [{
        "file": "f.yaml",
        "cell": "hub",
        "positive": ["a", "b"],
        "negative": ["x", "y"],
    }]

    # Always returns "hub" — perfect on positives, 0 on negatives.
    def always_hub(query):
        return "hub"

    metrics = proof.quality_arm(oracles, always_hub)
    # pos_correct=2/2 → pos_score=1.0
    # neg_correct=0/2 → neg_score=0.0
    # file_score = (1.0 + 0.0) / 2 = 0.5
    assert metrics["quality"] == 0.5


# ─── parse_cell_from_llm_text ─────────────────────────────────────

def test_parse_cell_finds_exact_match():
    import run_r21_oracle_ab_proof as proof
    valid = {"hub", "bee_ops", "environment"}
    assert proof.parse_cell_from_llm_text("bee_ops", valid) == "bee_ops"


def test_parse_cell_finds_substring_in_explanation():
    import run_r21_oracle_ab_proof as proof
    valid = {"hub", "bee_ops", "environment"}
    text = "I think this should route to bee_ops because of beekeeping."
    assert proof.parse_cell_from_llm_text(text, valid) == "bee_ops"


def test_parse_cell_returns_none_for_no_match():
    import run_r21_oracle_ab_proof as proof
    valid = {"hub", "bee_ops", "environment"}
    assert proof.parse_cell_from_llm_text("nothing matches", valid) is None


def test_parse_cell_case_insensitive():
    import run_r21_oracle_ab_proof as proof
    valid = {"hub", "bee_ops", "environment"}
    # The LLM might return "BEE_OPS" or "Bee_Ops"
    assert proof.parse_cell_from_llm_text("BEE_OPS", valid) == "bee_ops"
    assert proof.parse_cell_from_llm_text("Bee_Ops", valid) == "bee_ops"


def test_parse_cell_handles_empty_string():
    import run_r21_oracle_ab_proof as proof
    assert proof.parse_cell_from_llm_text("", {"hub"}) is None
    assert proof.parse_cell_from_llm_text(None, {"hub"}) is None  # type: ignore[arg-type]


# ─── End-to-end with treatment disabled (Profile S compatible) ──

def test_run_proof_with_treatment_disabled_records_zero_delta(tmp_path):
    """When treatment is disabled (Profile S), the treatment arm
    falls through to control on every utterance, so quality_treatment
    == quality_control and delta_quality_pct == 0. The harness must
    still complete and emit a valid result JSON.
    """
    import run_r21_oracle_ab_proof as proof
    result = proof.run_proof(
        oracle_dir=REPO_ROOT / "tests" / "oracle",
        hex_config=REPO_ROOT / "configs" / "hex_cells.yaml",
        treatment_share=1.0,
        treatment_enabled=False,
    )
    assert result["control"]["quality"] >= 0.0
    assert result["treatment"]["quality"] >= 0.0
    # With treatment disabled, both arms route via the same heuristic
    # (the treatment arm falls through to control on every call), so
    # the qualities must be equal and the delta must be 0.
    assert result["control"]["quality"] == result["treatment"]["quality"]
    assert result["delta_quality_pct"] == 0.0
    assert result["deployment_recommendation"] == "keep_disabled"


def test_run_proof_emits_required_evidence_fields(tmp_path):
    """Every R21.1 evidence record must surface the operator-decisions
    Decision 8 fields so downstream readers can distinguish 'LLM
    unavailable' from 'LLM measurably worse'."""
    import run_r21_oracle_ab_proof as proof
    result = proof.run_proof(
        oracle_dir=REPO_ROOT / "tests" / "oracle",
        hex_config=REPO_ROOT / "configs" / "hex_cells.yaml",
        treatment_share=1.0,
        treatment_enabled=False,
    )
    # Per operator Decision 8
    assert "local_llm_status" in result["configuration"]
    assert result["configuration"]["local_llm_status"] in (
        "available", "unavailable",
    )
    # R22.3 Profile L: cloud (Anthropic) availability must be surfaced too,
    # so a zero-delta treatment can be read as "no operator API key" vs
    # "LLM measurably worse".
    assert "anthropic_status" in result["configuration"]
    assert result["configuration"]["anthropic_status"] in (
        "available", "unavailable",
    )
    assert "fallthrough_uses" in result["treatment"]
    assert "local_llm_uses" in result["treatment"]
    assert "unparsed_responses" in result["treatment"]
    assert result["deployment_recommendation"] in (
        "deploy_behind_flag", "keep_disabled",
    )
    # Per Decision 6: micro counters surfaced as secondary diagnostics
    assert result["control"]["micro_pos"][1] > 0
    assert result["treatment"]["micro_pos"][1] > 0


def test_run_proof_corpus_has_expected_size(tmp_path):
    """Sanity: the oracle corpus matches the synthesis claim of
    ~450 utterances across 15 files."""
    import run_r21_oracle_ab_proof as proof
    result = proof.run_proof(
        oracle_dir=REPO_ROOT / "tests" / "oracle",
        hex_config=REPO_ROOT / "configs" / "hex_cells.yaml",
        treatment_share=1.0,
        treatment_enabled=False,
    )
    assert result["corpus"]["files"] == 14
    # The synthesis claims ~450 total utterances. With 14 routable
    # oracles × ~30 utterances each, expected total ~420; allow a
    # generous 350-500 band.
    total = (
        result["corpus"]["total_positive"]
        + result["corpus"]["total_negative"]
    )
    assert 350 <= total <= 500, (
        f"oracle corpus size drifted: {total} utterances; expected ~420"
    )
