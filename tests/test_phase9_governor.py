"""Targeted tests for waggledance/core/autonomy/governor.py
(Phase 9 §F.governor).

Covers Prompt_1_Master §F minimum tests:
- deterministic tick()
- action gate no-auto-execute (kernel never executes)
- constitution immutability check
- domain-neutrality
- CLI help and dry-run
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.autonomy import (
    governor as gov,
    kernel_state as ks,
)


def _make_state() -> ks.KernelState:
    return ks.initial_state(
        constitution_id="wd_autonomy_constitution_v1",
        constitution_sha256="sha256:" + "a" * 64,
        pinned_input_manifest_sha256="sha256:test",
        branch_name="phase9/autonomy-fabric",
        base_commit_hash="ddb0821",
    )


# ── 1. deterministic tick() ──────────────────────────────────────-

def test_tick_is_deterministic_for_same_signals():
    s = _make_state()
    signals = [
        {"kind": "consultation_request", "lane": "provider_plane",
         "intent": "explain why thermal score drifted",
         "rationale": "session B blind spot needs deeper analysis"},
        {"kind": "builder_request", "lane": "builder_lane",
         "intent": "draft solver_family_consolidation patch",
         "rationale": "session D meta-proposal #1"},
    ]
    r1 = gov.tick(
        state=s, ts_iso="2026-04-26T03:00:00+00:00",
        constitution_id=s.constitution_id,
        constitution_sha256=s.constitution_sha256,
        inbound_signals=signals,
    )
    r2 = gov.tick(
        state=s, ts_iso="2099-01-01T00:00:00+00:00",   # different ts
        constitution_id=s.constitution_id,
        constitution_sha256=s.constitution_sha256,
        inbound_signals=signals,
    )
    # Same state + same signals → same recommendation_ids (ts excluded)
    ids1 = [r.recommendation_id for r in r1.recommendations]
    ids2 = [r.recommendation_id for r in r2.recommendations]
    assert ids1 == ids2
    # Same length
    assert len(r1.recommendations) == len(r2.recommendations) == 2


def test_tick_is_deterministic_for_signal_reordering():
    s = _make_state()
    signals_a = [
        {"kind": "ingest_request", "lane": "ingestion",
         "intent": "ingest pinned curiosity log", "rationale": "A"},
        {"kind": "consultation_request", "lane": "provider_plane",
         "intent": "explain dream proposal #1", "rationale": "B"},
    ]
    signals_b = list(reversed(signals_a))
    r1 = gov.tick(state=s, ts_iso="t1",
                     constitution_id=s.constitution_id,
                     constitution_sha256=s.constitution_sha256,
                     inbound_signals=signals_a)
    r2 = gov.tick(state=s, ts_iso="t1",
                     constitution_id=s.constitution_id,
                     constitution_sha256=s.constitution_sha256,
                     inbound_signals=signals_b)
    ids1 = [r.recommendation_id for r in r1.recommendations]
    ids2 = [r.recommendation_id for r in r2.recommendations]
    assert ids1 == ids2


# ── 2. action gate no-auto-execute (kernel never executes) ───────-

def test_tick_never_executes_recommendations():
    """Every recommendation MUST carry no_runtime_mutation=True and
    requires_human_review (default True for action_gate handoff)."""
    s = _make_state()
    r = gov.tick(state=s, ts_iso="t",
                    constitution_id=s.constitution_id,
                    constitution_sha256=s.constitution_sha256,
                    inbound_signals=[
                        {"kind": "consultation_request",
                         "lane": "provider_plane", "intent": "x",
                         "rationale": "y"}
                    ])
    for rec in r.recommendations:
        assert rec.no_runtime_mutation is True


def test_governor_source_has_no_runtime_or_llm_calls():
    """Crown-jewel safety: the kernel must not call live LLMs or
    runtime registries from inside tick()."""
    src = (ROOT / "waggledance" / "core" / "autonomy" / "governor.py")\
        .read_text(encoding="utf-8")
    forbidden = ["import faiss", "from waggledance.runtime",
                 "ollama.generate(", "openai.chat", "anthropic.messages",
                 "requests.post(", "axiom_write(",
                 "promote_to_runtime(", "register_solver_in_runtime("]
    for pat in forbidden:
        assert pat not in src, f"forbidden pattern {pat} in governor.py"


# ── 3. constitution immutability check ──────────────────────────-

def test_tick_raises_on_constitution_sha_mismatch():
    s = _make_state()
    with pytest.raises(RuntimeError, match="constitution sha256 mismatch"):
        gov.tick(
            state=s, ts_iso="t",
            constitution_id=s.constitution_id,
            constitution_sha256="sha256:" + "b" * 64,   # different
            inbound_signals=[],
        )


def test_load_constitution_sha256_real_file():
    """The shipped constitution.yaml must hash deterministically."""
    p = ROOT / "waggledance" / "core" / "autonomy" / "constitution.yaml"
    sha1 = gov.load_constitution_sha256(p)
    sha2 = gov.load_constitution_sha256(p)
    assert sha1 == sha2
    assert sha1.startswith("sha256:")
    assert len(sha1) == len("sha256:") + 64


def test_load_constitution_id_parses():
    p = ROOT / "waggledance" / "core" / "autonomy" / "constitution.yaml"
    cid = gov.load_constitution_id(p)
    assert cid == "wd_autonomy_constitution_v1"


# ── 4. domain-neutrality ────────────────────────────────────────-

def test_governor_source_has_no_domain_metaphors():
    """Per Prompt_1_Master §DOMAIN-NEUTRALITY core kernel must not
    contain bee/hive/swarm/honeycomb/factory/PDAM/beverage metaphors."""
    src = (ROOT / "waggledance" / "core" / "autonomy" / "governor.py")\
        .read_text(encoding="utf-8")
    # Lower-case match — the governor itself must use neutral terms
    forbidden_metaphors = ["bee ", "bees ", "hive ", "swarm ",
                            "honeycomb ", "waggle", "factory ",
                            "PDAM", "beverage"]
    src_l = src.lower()
    for pat in forbidden_metaphors:
        # Allow occurrences inside import paths or filenames since
        # legacy waggledance/* must be referenced.
        if pat.strip() == "waggle":
            continue   # waggledance package import is unavoidable
        assert pat.lower() not in src_l, (
            f"governor.py contains domain metaphor: {pat!r}"
        )


def test_default_capsule_context_is_neutral():
    s = _make_state()
    rec = gov.make_recommendation(
        tick_id=1, kind="noop", lane="wait",
        intent="x", rationale="y",
    )
    assert rec.capsule_context == "neutral_v1"


# ── 5. tick advances state correctly ──────────────────────────────

def test_tick_advances_state():
    s = _make_state()
    r = gov.tick(state=s, ts_iso="2026-04-26T03:00:00+00:00",
                    constitution_id=s.constitution_id,
                    constitution_sha256=s.constitution_sha256,
                    inbound_signals=[{"kind": "noop", "lane": "wait",
                                       "intent": "x", "rationale": "y"}])
    assert r.state_after.last_tick is not None
    assert r.state_after.last_tick.tick_id == 1
    assert r.state_after.next_tick_id == 2
    assert r.state_after.actions_recommended_total == 1
    assert r.state_after.persisted_revision == 1


def test_tick_dry_run_does_not_persist(tmp_path):
    s = _make_state()
    target = tmp_path / "kernel_state.json"
    gov.tick(state=s, ts_iso="t",
                constitution_id=s.constitution_id,
                constitution_sha256=s.constitution_sha256,
                dry_run=True, kernel_state_path=target)
    assert not target.exists()


def test_tick_apply_persists(tmp_path):
    s = _make_state()
    target = tmp_path / "kernel_state.json"
    r = gov.tick(state=s, ts_iso="2026-04-26T03:00:00+00:00",
                    constitution_id=s.constitution_id,
                    constitution_sha256=s.constitution_sha256,
                    dry_run=False, kernel_state_path=target)
    assert target.exists()
    assert r.kernel_state_path == target
    s2 = ks.load_state(target)
    assert s2.next_tick_id == 2


# ── 6. recommendation_id structural determinism ──────────────────-

def test_recommendation_id_excludes_ts():
    """The id is sha256[:12] of (tick_id, kind, lane, intent) — does
    not depend on ts or rationale."""
    rid_a = gov.compute_recommendation_id(1, "consultation_request",
                                              "provider_plane", "x")
    rid_b = gov.compute_recommendation_id(1, "consultation_request",
                                              "provider_plane", "x")
    rid_c = gov.compute_recommendation_id(1, "consultation_request",
                                              "provider_plane", "y")
    assert rid_a == rid_b
    assert rid_a != rid_c
    assert len(rid_a) == 12


# ── 7. CLI help and dry-run ──────────────────────────────────────-

def test_cli_help_exits_zero_and_lists_flags():
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "wd_kernel_tick.py"), "--help"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0
    assert result.stdout.strip()
    for flag in ("--constitution", "--kernel-state", "--signals",
                  "--apply", "--dry-run", "--json"):
        assert flag in result.stdout


def test_cli_dry_run_on_fresh_state(tmp_path):
    """Smoke test: with no prior state file and no signals, the tick
    succeeds in dry-run and emits a non-empty JSON summary."""
    target = tmp_path / "kernel_state.json"
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "wd_kernel_tick.py"),
         "--kernel-state", str(target),
         "--ts", "2026-04-26T03:00:00+00:00",
         "--json"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0
    assert result.stdout.strip()
    summary = json.loads(result.stdout)
    assert summary["dry_run"] is True
    assert summary["recommendations"] == 0   # no signals, no recs
    assert not target.exists()   # dry-run did not persist


def test_cli_apply_persists_state(tmp_path):
    target = tmp_path / "kernel_state.json"
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "wd_kernel_tick.py"),
         "--kernel-state", str(target),
         "--ts", "2026-04-26T03:00:00+00:00",
         "--apply", "--json"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0
    assert target.exists()
    summary = json.loads(result.stdout)
    assert summary["dry_run"] is False
    assert summary["tick_id"] == 1


# ── 8. recommendation schema-shape conforms ──────────────────────-

def test_recommendation_to_dict_has_required_fields():
    r = gov.make_recommendation(
        tick_id=1, kind="consultation_request", lane="provider_plane",
        intent="x", rationale="y",
    )
    d = r.to_dict()
    required = {"schema_version", "recommendation_id", "tick_id",
                 "kind", "lane", "intent", "rationale", "risk",
                 "reversibility", "no_runtime_mutation",
                 "requires_human_review", "produced_by",
                 "capsule_context", "evidence_refs"}
    assert required.issubset(d.keys())
    assert d["no_runtime_mutation"] is True
