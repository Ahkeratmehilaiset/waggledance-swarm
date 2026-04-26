# SPDX-License-Identifier: Apache-2.0
"""Targeted tests for Phase 9 §V Conversation Layer + Personality Constants."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.conversation import (
    PRESENCE_KINDS,
    context_synthesizer as cs,
    meta_dialogue as md,
    presence_log as pl,
)

FORBIDDEN_PATH = (
    ROOT / "waggledance" / "core" / "identity" / "forbidden_patterns.yaml"
)
PERSONALITY_PATH = (
    ROOT / "waggledance" / "core" / "identity" / "personality.yaml"
)


# ═══════════════════ schema enums match constants ══════════════════

def test_presence_kinds_match_schema():
    schema = json.loads((ROOT / "schemas" / "presence_log.schema.json")
                          .read_text(encoding="utf-8"))
    assert tuple(schema["properties"]["kind"]["enum"]) == PRESENCE_KINDS


# ═══════════════════ presence_log chain integrity ══════════════════

def test_make_entry_sha_excludes_self():
    e = pl.make_entry(
        ts_iso="t", kind="turn", summary="hello there",
        prev_entry_sha256=pl.GENESIS_PREV,
    )
    d = e.to_dict()
    d.pop("entry_sha256")
    assert pl.compute_entry_sha256(d) == e.entry_sha256
    with pytest.raises(ValueError):
        pl.compute_entry_sha256({"entry_sha256": e.entry_sha256})


def test_presence_log_chain_validates(tmp_path):
    p = tmp_path / "presence.jsonl"
    e1 = pl.make_entry(ts_iso="t1", kind="turn", summary="first turn",
                          prev_entry_sha256=pl.GENESIS_PREV)
    pl.append_entry(p, e1)
    e2 = pl.make_entry(ts_iso="t2", kind="reflection", summary="reflected",
                          prev_entry_sha256=e1.entry_sha256)
    pl.append_entry(p, e2)
    entries = pl.read_entries(p)
    ok, broken = pl.validate_chain(entries)
    assert ok and broken is None


def test_presence_log_duplicate_skipped(tmp_path):
    p = tmp_path / "presence.jsonl"
    e = pl.make_entry(ts_iso="t", kind="turn", summary="hello",
                         prev_entry_sha256=pl.GENESIS_PREV)
    pl.append_entry(p, e)
    pl.append_entry(p, e)
    pl.append_entry(p, e)
    assert len(pl.read_entries(p)) == 1


def test_presence_log_malformed_skipped(tmp_path):
    p = tmp_path / "presence.jsonl"
    e = pl.make_entry(ts_iso="t", kind="turn", summary="real",
                         prev_entry_sha256=pl.GENESIS_PREV)
    pl.append_entry(p, e)
    with open(p, "a", encoding="utf-8") as f:
        f.write("{ malformed\n")
        f.write('{"missing": "fields"}\n')
    assert len(pl.read_entries(p)) == 1


def test_presence_log_chain_break_detected(tmp_path):
    p = tmp_path / "presence.jsonl"
    e1 = pl.make_entry(ts_iso="t1", kind="turn", summary="first turn",
                          prev_entry_sha256=pl.GENESIS_PREV)
    e2 = pl.make_entry(ts_iso="t2", kind="reflection", summary="reflected",
                          prev_entry_sha256="deadbeef" * 8)   # broken
    pl.append_entry(p, e1)
    pl.append_entry(p, e2)
    entries = pl.read_entries(p)
    ok, broken = pl.validate_chain(entries)
    assert not ok
    assert broken == e2.entry_sha256


def test_presence_kind_validated():
    with pytest.raises(ValueError):
        pl.PresenceEntry(
            schema_version=1, entry_sha256="x"*12, ts_iso="t",
            kind="bogus", summary="x", prev_entry_sha256=pl.GENESIS_PREV,
            capsule_context="neutral_v1",
        )


# ═══════════════════ context_synthesizer ═══════════════════════════

def test_load_forbidden_patterns_from_real_file():
    forb, over = cs.load_forbidden_patterns(FORBIDDEN_PATH)
    assert "absolutely" in forb
    assert "i'm just an ai" in forb
    assert "definitely" in over
    # All values lowercase
    for s in forb | over:
        assert s == s.lower()


def test_load_personality_self_reference():
    may, never = cs.load_personality_self_reference(PERSONALITY_PATH)
    assert any("uncertain" in m.lower() for m in may)
    assert any("just an ai" in n.lower() for n in never)


def test_synthesize_deterministic():
    sm = {
        "workspace_tensions": [
            {"tension_id": "t1", "type": "scorecard_drift",
             "severity": "high", "lifecycle_status": "persisting"},
        ],
        "blind_spots": [
            {"domain": "safety", "severity": "high",
             "detectors": ["coverage_negative_space"]},
        ],
    }
    c1 = cs.synthesize(
        recent_presence=[], self_model=sm, prev_self_model=None,
        forbidden_patterns_path=FORBIDDEN_PATH,
    )
    c2 = cs.synthesize(
        recent_presence=[], self_model=sm, prev_self_model=None,
        forbidden_patterns_path=FORBIDDEN_PATH,
    )
    assert c1.to_dict() == c2.to_dict()


def test_synthesize_extracts_uncertainty():
    sm = {
        "workspace_tensions": [
            {"tension_id": "t1", "type": "calibration_oscillation",
             "severity": "high", "lifecycle_status": "active"},
            {"tension_id": "t2", "type": "scorecard_drift",
             "severity": "low", "lifecycle_status": "active"},
        ],
    }
    ctx = cs.synthesize(
        recent_presence=[], self_model=sm,
        forbidden_patterns_path=FORBIDDEN_PATH,
    )
    # Only high/medium severity surface
    assert len(ctx.current_uncertainty_summary) == 1


def test_synthesize_extracts_blind_spots():
    sm = {
        "blind_spots": [
            {"domain": "safety", "severity": "high"},
            {"domain": "logistics", "severity": "low"},
        ],
    }
    ctx = cs.synthesize(
        recent_presence=[], self_model=sm,
        forbidden_patterns_path=FORBIDDEN_PATH,
    )
    assert len(ctx.blind_spots_surfaced) == 2


def test_synthesize_delta_since_last_turn():
    prev_sm = {
        "workspace_tensions": [
            {"tension_id": "t1"}, {"tension_id": "t2"},
        ],
    }
    cur_sm = {
        "workspace_tensions": [
            {"tension_id": "t1"}, {"tension_id": "t3"},   # t2 gone, t3 new
        ],
    }
    ctx = cs.synthesize(
        recent_presence=[], self_model=cur_sm,
        prev_self_model=prev_sm,
        forbidden_patterns_path=FORBIDDEN_PATH,
    )
    deltas = ctx.delta_since_last_turn
    assert any("t3" in d and "new" in d for d in deltas)
    assert any("t2" in d and "resolved" in d for d in deltas)


def test_synthesize_references_to_past_excerpts():
    entries = []
    for i in range(5):
        entries.append(pl.make_entry(
            ts_iso=f"t{i}", kind="turn",
            summary=f"summary {i}",
            user_turn_excerpt=f"user said something at turn {i}",
            prev_entry_sha256=pl.GENESIS_PREV if i == 0 else "abc"*4,
        ))
    ctx = cs.synthesize(
        recent_presence=entries, self_model=None,
        forbidden_patterns_path=FORBIDDEN_PATH,
        max_references=3,
    )
    assert len(ctx.references_to_past) == 3


def test_synthesize_learning_intents_from_persisting_tensions():
    sm = {
        "blind_spots": [{"domain": "math", "severity": "high"}],
        "workspace_tensions": [
            {"tension_id": "t1", "type": "scorecard_drift",
             "severity": "high", "lifecycle_status": "persisting"},
        ],
    }
    ctx = cs.synthesize(
        recent_presence=[], self_model=sm,
        forbidden_patterns_path=FORBIDDEN_PATH,
    )
    intents = " ".join(ctx.learning_intents)
    assert "math" in intents
    assert "t1" in intents


# ═══════════════════ scan_for_forbidden ════════════════════════════

def test_scan_for_forbidden_finds_violations():
    forb, over = cs.load_forbidden_patterns(FORBIDDEN_PATH)
    text = "This is absolutely the right answer, definitely."
    violations = cs.scan_for_forbidden(text, forb, over)
    kinds = {v.kind for v in violations}
    assert "forbidden" in kinds
    assert "confidence_overclaim" in kinds


def test_scan_for_forbidden_clean_text():
    forb, over = cs.load_forbidden_patterns(FORBIDDEN_PATH)
    text = "I think this approach holds, with one open uncertainty."
    violations = cs.scan_for_forbidden(text, forb, over)
    assert violations == []


def test_scan_for_forbidden_deterministic_order():
    forb, over = cs.load_forbidden_patterns(FORBIDDEN_PATH)
    text = "absolutely obviously definitely"
    v1 = cs.scan_for_forbidden(text, forb, over)
    v2 = cs.scan_for_forbidden(text, forb, over)
    assert [(v.pattern, v.location) for v in v1] == \
        [(v.pattern, v.location) for v in v2]


# ═══════════════════ meta_dialogue ═════════════════════════════════

def test_respond_what_changed_with_delta():
    prev_sm = {"workspace_tensions": [{"tension_id": "t1"}]}
    cur_sm = {"workspace_tensions": [{"tension_id": "t2"}]}
    ctx = cs.synthesize(
        recent_presence=[], self_model=cur_sm,
        prev_self_model=prev_sm,
        forbidden_patterns_path=FORBIDDEN_PATH,
    )
    r = md.respond_what_changed(ctx)
    assert "Since last time" in r.rendered_text
    assert r.is_clean


def test_respond_what_changed_no_delta():
    ctx = cs.synthesize(
        recent_presence=[], self_model=None,
        forbidden_patterns_path=FORBIDDEN_PATH,
    )
    r = md.respond_what_changed(ctx)
    assert "structural change" in r.rendered_text
    assert r.is_clean


def test_respond_what_to_learn():
    sm = {"blind_spots": [{"domain": "safety", "severity": "high"}]}
    ctx = cs.synthesize(
        recent_presence=[], self_model=sm,
        forbidden_patterns_path=FORBIDDEN_PATH,
    )
    r = md.respond_what_to_learn(ctx)
    assert "want to learn" in r.rendered_text.lower()
    assert "safety" in r.rendered_text


def test_respond_uncertainty():
    sm = {
        "workspace_tensions": [
            {"tension_id": "t1", "type": "scorecard_drift",
             "severity": "high", "lifecycle_status": "active"},
        ],
    }
    ctx = cs.synthesize(
        recent_presence=[], self_model=sm,
        forbidden_patterns_path=FORBIDDEN_PATH,
    )
    r = md.respond_uncertainty(ctx)
    assert "uncertainty" in r.rendered_text.lower()
    assert r.is_clean


def test_respond_blind_spots():
    sm = {"blind_spots": [{"domain": "math", "severity": "medium"}]}
    ctx = cs.synthesize(
        recent_presence=[], self_model=sm,
        forbidden_patterns_path=FORBIDDEN_PATH,
    )
    r = md.respond_blind_spots(ctx)
    assert "blind" in r.rendered_text.lower()
    assert "math" in r.rendered_text


def test_respond_unknown_question_raises():
    ctx = cs.synthesize(
        recent_presence=[], self_model=None,
        forbidden_patterns_path=FORBIDDEN_PATH,
    )
    with pytest.raises(ValueError, match="unknown meta question"):
        md.respond("invent_question_kind", ctx)


def test_meta_responses_are_clean_of_forbidden_substrings():
    """Critical: WD's own meta-responses must NEVER produce forbidden
    substrings (sycophancy, AI self-deprecation, theatrics)."""
    sm = {
        "workspace_tensions": [
            {"tension_id": "t1", "type": "x", "severity": "high",
             "lifecycle_status": "persisting"},
        ],
        "blind_spots": [{"domain": "math", "severity": "high"}],
    }
    ctx = cs.synthesize(
        recent_presence=[], self_model=sm,
        forbidden_patterns_path=FORBIDDEN_PATH,
    )
    for q in md.META_QUESTION_KINDS:
        r = md.respond(q, ctx)
        assert r.is_clean, (
            f"meta question {q} produced forbidden pattern in output"
        )


# ═══════════════════ CLI ═══════════════════════════════════════════

def test_cli_help():
    r = subprocess.run(
        [sys.executable,
         str(ROOT / "tools" / "wd_conversation_probe.py"), "--help"],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0
    for flag in ("--question", "--self-model-path",
                  "--presence-log-path", "--json"):
        assert flag in r.stdout


def test_cli_runs_clean_with_no_inputs():
    r = subprocess.run(
        [sys.executable,
         str(ROOT / "tools" / "wd_conversation_probe.py"),
         "--json"],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["is_clean"] is True


# ═══════════════════ source safety ═════════════════════════════════

def test_phase_v_source_safety():
    forbidden = ["import faiss", "from waggledance.runtime",
                  "ollama.generate(", "openai.chat",
                  "anthropic.messages", "requests.post(",
                  "axiom_write(", "promote_to_runtime("]
    pkgs = [
        ROOT / "waggledance" / "core" / "conversation",
        ROOT / "waggledance" / "core" / "identity",
    ]
    for pkg in pkgs:
        for p in pkg.glob("*.py"):
            text = p.read_text(encoding="utf-8")
            for pat in forbidden:
                assert pat not in text, f"{p.name}: {pat}"
