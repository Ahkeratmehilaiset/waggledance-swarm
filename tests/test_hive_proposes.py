"""Targeted tests for the review bundle, history chain, CLI, and
byte-identical determinism (Phase 8.5 Session D, commit 3).

Covers D.txt §TESTING REQUIREMENTS items: 5, 7, 23, 24, 25, 28, 29,
30, 31, 32, 33, 34, 35, 37, 42, 44, 45, 46, 47, 48.
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

from waggledance.core.meta import (
    HUMAN_REVIEW_BOUNDARY_TEXT,
    META_SCHEMA_VERSION,
    history as hist,
    meta_learner as ml,
    review_bundle as rb,
)


def _load_tool():
    path = ROOT / "tools" / "hive_proposes.py"
    spec = importlib.util.spec_from_file_location("hive_proposes_tool", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["hive_proposes_tool"] = mod
    spec.loader.exec_module(mod)
    return mod


tool = _load_tool()


# ── Fixture builders ─────────────────────────────────────────────-

def _make_proposal(*, mid="abc123def456", proposal_type="solver_family_growth",
                       priority=0.4, confidence=0.7, scope="solver_library",
                       cell="thermal", lifecycle="new") -> ml.MetaProposal:
    return ml.MetaProposal(
        schema_version=META_SCHEMA_VERSION,
        meta_proposal_id=mid,
        proposal_type=proposal_type,
        scope_class=scope,
        impacted_cells=(cell,),
        evidence_planes=("curiosity", "self_model"),
        evidence_strength=0.8,
        expected_value=0.6,
        confidence=confidence,
        proposal_priority=priority,
        cross_plane_support_factor=1.25,
        urgency_factor=1.0,
        uncertainty="low" if confidence > 0.7 else "medium",
        risk="low",
        why_now="evidence converged",
        why_human_review_required="shadow only",
        no_mutation_in_session=True,
        source_curiosity_ids=("cur_a",),
        source_tension_ids=("ten_a",),
        source_dream_meta_proposal_ids=(),
        source_resilience_refs=(),
        canonical_target=cell,
        provenance={
            "branch_name": "phase8.5/hive-proposes",
            "base_commit_hash": "abc1234",
            "pinned_input_manifest_sha256": "sha256:test",
            "consumed_hook_contracts": [],
            "fixture_fallback_used": False,
        },
        lifecycle_status=lifecycle,
        resolution_reason="n/a",
    )


# ── 1. recommend_action_for: post_campaign_runtime_review_candidate -

def test_recommend_post_campaign_runtime_review_candidate():
    p = _make_proposal(priority=0.10, confidence=0.8,
                         scope="solver_library")
    assert rb.recommend_action_for(p) == "post_campaign_runtime_review_candidate"


# ── 2. recommend_action_for: review_for_future_PR ──────────────-

def test_recommend_review_for_future_pr():
    p = _make_proposal(priority=0.03, confidence=0.5,
                         scope="introspection")
    assert rb.recommend_action_for(p) == "review_for_future_PR"


# ── 3. recommend_action_for: archive_as_low_value ──────────────-

def test_recommend_archive_as_low_value():
    p = _make_proposal(priority=0.001, confidence=0.1,
                         scope="archival")
    assert rb.recommend_action_for(p) == "archive_as_low_value"


# ── 4. recommend_action_for: wait_for_more_evidence ────────────-

def test_recommend_wait_for_more_evidence():
    p = _make_proposal(priority=0.03, confidence=0.35,
                         scope="introspection")
    assert rb.recommend_action_for(p) == "wait_for_more_evidence"


# ── 5. review_bundle structure ─────────────────────────────────-

def test_review_bundle_has_required_fields():
    bundle = rb.build_review_bundle(
        proposals=[_make_proposal()],
        insufficient_evidence=[{"candidate_target": "energy",
                                  "missing_planes": ["dream"],
                                  "evidence_strength_seen": 0.4,
                                  "why_below_threshold": "single plane"}],
        rejected_candidates=[],
        resolved_proposal_ids=[],
        branch_name="b", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:t",
        consumed_hook_contracts=[],
        fixture_fallback_used={"dream_plane": {"used": True}},
    )
    required = {"schema_version", "human_review_boundary", "provenance",
                 "consumed_hook_contracts", "summary_text", "proposals",
                 "insufficient_evidence", "rejected_candidates",
                 "counts_by_recommended_next_human_action",
                 "why_human_review_required",
                 "why_no_runtime_mutation_occurred",
                 "fixture_fallback_used"}
    assert required.issubset(bundle.keys())


# ── 6. why_human_review_required always present ────────────────-

def test_why_human_review_required_present():
    bundle = rb.build_review_bundle(
        proposals=[], insufficient_evidence=[], rejected_candidates=[],
        resolved_proposal_ids=[], branch_name="b", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:t",
        consumed_hook_contracts=[], fixture_fallback_used={},
    )
    assert bundle["why_human_review_required"]
    assert bundle["why_no_runtime_mutation_occurred"]
    assert bundle["human_review_boundary"] == HUMAN_REVIEW_BOUNDARY_TEXT


# ── 7. counts_by_recommended_next_human_action consistent ──────-

def test_counts_by_recommended_action_consistent():
    proposals = [
        _make_proposal(mid="a"*12, priority=0.10, confidence=0.8,
                        scope="solver_library"),
        _make_proposal(mid="b"*12, priority=0.001, confidence=0.1,
                        scope="archival"),
    ]
    bundle = rb.build_review_bundle(
        proposals=proposals, insufficient_evidence=[],
        rejected_candidates=[], resolved_proposal_ids=[],
        branch_name="b", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:t",
        consumed_hook_contracts=[], fixture_fallback_used={},
    )
    counts = bundle["counts_by_recommended_next_human_action"]
    assert sum(counts.values()) == len(proposals)


# ── 8. counts include all 4 enum keys ──────────────────────────-

def test_counts_have_all_enum_keys():
    bundle = rb.build_review_bundle(
        proposals=[_make_proposal()], insufficient_evidence=[],
        rejected_candidates=[], resolved_proposal_ids=[],
        branch_name="b", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:t",
        consumed_hook_contracts=[], fixture_fallback_used={},
    )
    counts = bundle["counts_by_recommended_next_human_action"]
    expected = {"review_for_future_PR", "archive_as_low_value",
                 "wait_for_more_evidence",
                 "post_campaign_runtime_review_candidate"}
    assert set(counts.keys()) == expected


# ── 9. hive_proposals.json byte-identical determinism ─────────-

def test_hive_proposals_json_byte_identical(tmp_path):
    p = _make_proposal()
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    rb.emit_hive_proposals([p], "br", "ab", "sha256:t", out_a)
    rb.emit_hive_proposals([p], "br", "ab", "sha256:t", out_b)
    a = (out_a / "hive_proposals.json").read_text(encoding="utf-8")
    b = (out_b / "hive_proposals.json").read_text(encoding="utf-8")
    assert a == b


# ── 10. hive_proposals.md byte-identical determinism ──────────-

def test_hive_proposals_md_byte_identical(tmp_path):
    p = _make_proposal()
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    rb.emit_hive_proposals([p], "br", "ab", "sha256:t", out_a)
    rb.emit_hive_proposals([p], "br", "ab", "sha256:t", out_b)
    a = (out_a / "hive_proposals.md").read_text(encoding="utf-8")
    b = (out_b / "hive_proposals.md").read_text(encoding="utf-8")
    assert a == b
    assert HUMAN_REVIEW_BOUNDARY_TEXT in a


# ── 11. meta_evidence_map.json byte-identical ─────────────────-

def test_meta_evidence_map_byte_identical(tmp_path):
    items = {
        "thermal": [
            ml.EvidenceItem("self_model", "thermal", "ten_a", "thermal",
                              1.0, "r"),
            ml.EvidenceItem("curiosity", "thermal", "cur_a", "thermal",
                              0.9, "x"),
        ],
    }
    proposals = [_make_proposal(cell="thermal")]
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    rb.emit_meta_evidence_map(items, proposals, "br", "ab", "sha256:t", out_a)
    rb.emit_meta_evidence_map(items, proposals, "br", "ab", "sha256:t", out_b)
    a = (out_a / "meta_evidence_map.json").read_text(encoding="utf-8")
    b = (out_b / "meta_evidence_map.json").read_text(encoding="utf-8")
    assert a == b


# ── 12. review_bundle.json byte-identical ─────────────────────-

def test_review_bundle_json_byte_identical(tmp_path):
    bundle = rb.build_review_bundle(
        proposals=[_make_proposal()], insufficient_evidence=[],
        rejected_candidates=[], resolved_proposal_ids=[],
        branch_name="b", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:t",
        consumed_hook_contracts=[], fixture_fallback_used={},
    )
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    rb.emit_review_bundle(bundle, out_a)
    rb.emit_review_bundle(bundle, out_b)
    a = (out_a / "review_bundle.json").read_text(encoding="utf-8")
    b = (out_b / "review_bundle.json").read_text(encoding="utf-8")
    assert a == b


# ── 13. review_bundle.md byte-identical + boundary text ───────-

def test_review_bundle_md_byte_identical(tmp_path):
    bundle = rb.build_review_bundle(
        proposals=[_make_proposal()], insufficient_evidence=[],
        rejected_candidates=[], resolved_proposal_ids=[],
        branch_name="b", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:t",
        consumed_hook_contracts=[], fixture_fallback_used={},
    )
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    rb.emit_review_bundle(bundle, out_a)
    rb.emit_review_bundle(bundle, out_b)
    a = (out_a / "review_bundle.md").read_text(encoding="utf-8")
    b = (out_b / "review_bundle.md").read_text(encoding="utf-8")
    assert a == b
    assert HUMAN_REVIEW_BOUNDARY_TEXT in a


# ── 14. history: entry_sha256 excludes self ───────────────────-

def test_history_entry_sha_excludes_self():
    e = hist.make_entry(
        meta_proposal_id="abc", proposal_type="introspection_gap",
        output_dir="docs/runs/hive/x", base_commit_hash="ab12",
        pinned_input_manifest_sha256="sha256:test",
        prev_entry_sha256=hist.GENESIS_PREV,
        ts="2026-04-26T00:00:00+00:00",
    )
    # Recompute from dict-without-sha
    d = hist.entry_to_dict(e)
    d.pop("entry_sha256")
    assert hist.compute_entry_sha256(d) == e.entry_sha256
    # Including the sha in input is rejected
    with pytest.raises(ValueError):
        hist.compute_entry_sha256({"entry_sha256": e.entry_sha256})


# ── 15. history: prev_entry_sha256 chain integrity ────────────-

def test_history_chain_integrity(tmp_path):
    p = tmp_path / "HISTORY.jsonl"
    e1 = hist.make_entry(
        meta_proposal_id="abc1", proposal_type="introspection_gap",
        output_dir="d1", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:t",
        prev_entry_sha256=hist.GENESIS_PREV,
        ts="2026-04-26T00:00:00+00:00",
    )
    hist.append_entry(p, e1)
    e2 = hist.make_entry(
        meta_proposal_id="abc2", proposal_type="introspection_gap",
        output_dir="d2", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:t",
        prev_entry_sha256=e1.entry_sha256,
        ts="2026-04-26T00:01:00+00:00",
    )
    hist.append_entry(p, e2)
    entries = hist.read_entries(p)
    ok, broken = hist.validate_chain(entries)
    assert ok and broken is None


# ── 16. history: duplicate entry_sha256 skipped on append ────-

def test_history_duplicate_entry_skipped(tmp_path):
    p = tmp_path / "HISTORY.jsonl"
    e = hist.make_entry(
        meta_proposal_id="abc", proposal_type="introspection_gap",
        output_dir="d", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:t",
        prev_entry_sha256=hist.GENESIS_PREV,
        ts="2026-04-26T00:00:00+00:00",
    )
    hist.append_entry(p, e)
    hist.append_entry(p, e)   # duplicate skip
    hist.append_entry(p, e)   # duplicate skip
    assert len(hist.read_entries(p)) == 1


# ── 17. history: malformed line skipped ───────────────────────-

def test_history_malformed_line_skipped(tmp_path):
    p = tmp_path / "HISTORY.jsonl"
    e = hist.make_entry(
        meta_proposal_id="abc", proposal_type="introspection_gap",
        output_dir="d", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:t",
        prev_entry_sha256=hist.GENESIS_PREV,
        ts="2026-04-26T00:00:00+00:00",
    )
    hist.append_entry(p, e)
    with open(p, "a", encoding="utf-8") as f:
        f.write("{ this is not json\n")
        f.write('{"missing": "required_fields"}\n')
    entries = hist.read_entries(p)
    assert len(entries) == 1
    assert entries[0].meta_proposal_id == "abc"


# ── 18. history: genesis marker is 0*64 ──────────────────────-

def test_history_genesis_marker():
    assert hist.GENESIS_PREV == "0" * 64
    e = hist.make_entry(
        meta_proposal_id="abc", proposal_type="introspection_gap",
        output_dir="d", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:t",
        prev_entry_sha256=hist.GENESIS_PREV,
        ts="2026-04-26T00:00:00+00:00",
    )
    assert e.prev_entry_sha256 == "0" * 64


# ── 19. history: chain detects break ──────────────────────────-

def test_history_chain_detects_break(tmp_path):
    p = tmp_path / "HISTORY.jsonl"
    e1 = hist.make_entry(
        meta_proposal_id="abc1", proposal_type="introspection_gap",
        output_dir="d", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:t",
        prev_entry_sha256=hist.GENESIS_PREV,
        ts="2026-04-26T00:00:00+00:00",
    )
    e2 = hist.make_entry(
        meta_proposal_id="abc2", proposal_type="introspection_gap",
        output_dir="d", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:t",
        prev_entry_sha256="deadbeef" * 8,   # broken — not e1.sha
        ts="2026-04-26T00:01:00+00:00",
    )
    hist.append_entry(p, e1)
    hist.append_entry(p, e2)
    entries = hist.read_entries(p)
    ok, broken = hist.validate_chain(entries)
    assert not ok
    assert broken == e2.entry_sha256


# ── 20. CLI: --help exits 0 with non-empty stdout ─────────────-

def test_cli_help_exits_zero():
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "hive_proposes.py"), "--help"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0
    assert result.stdout.strip()
    for flag in ("--input-manifest", "--output-dir", "--real-data-only",
                  "--cell", "--proposal-type", "--apply", "--dry-run",
                  "--json"):
        assert flag in result.stdout


# ── 21. CLI: --real-data-only fails when pinned path missing -

def test_cli_real_data_only_fails_on_missing(tmp_path):
    state_path = tmp_path / "s.json"
    state_path.write_text(json.dumps({
        "pinned_input_manifest_sha256": "sha256:abc",
        "pinned_inputs": [
            {"path": str(tmp_path / "does_not_exist.json"),
             "size_bytes": 100, "sha256_full": "sha256:0"}
        ],
        "consumed_hook_contracts": [],
    }), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "hive_proposes.py"),
         "--input-manifest", str(state_path), "--real-data-only"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 2


# ── 22. CLI dry-run produces summary on the real pinned set ──-

def test_cli_dry_run_on_real_pinned_set():
    """Smoke test: the bundled state.json points at real Session A/B
    artifacts; dry-run must succeed and produce non-empty output."""
    state_path = ROOT / "docs" / "runs" / "phase8_5_hive_session_state.json"
    if not state_path.exists():
        pytest.skip("state.json not present in this worktree")
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "hive_proposes.py"),
         "--input-manifest", str(state_path), "--json"],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0
    assert result.stdout.strip()
    summary = json.loads(result.stdout)
    assert "proposals" in summary
    assert summary["pin_hash"]


# ── 23. no secrets in emitted artifacts ───────────────────────-

def test_no_secrets_in_review_bundle(tmp_path):
    p = _make_proposal()
    bundle = rb.build_review_bundle(
        proposals=[p], insufficient_evidence=[],
        rejected_candidates=[], resolved_proposal_ids=[],
        branch_name="b", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:t",
        consumed_hook_contracts=[], fixture_fallback_used={},
    )
    blob = json.dumps(bundle).lower()
    for pat in ("password", "secret", "api_key", "token=",
                 "private key", "begin rsa"):
        assert pat not in blob


# ── 24. no absolute paths in emitted bundle ───────────────────-

def test_no_absolute_paths_in_emitted_bundle(tmp_path):
    p = _make_proposal()
    bundle = rb.build_review_bundle(
        proposals=[p], insufficient_evidence=[],
        rejected_candidates=[], resolved_proposal_ids=[],
        branch_name="b", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:t",
        consumed_hook_contracts=[], fixture_fallback_used={},
    )
    blob = json.dumps(bundle)
    assert "C:\\" not in blob
    assert "/home/" not in blob


# ── 25. tools never imports runtime / faiss / port-8002 ───────-

def test_no_runtime_or_faiss_imports_in_tool():
    src = (ROOT / "tools" / "hive_proposes.py").read_text(encoding="utf-8")
    # Code-only patterns that would indicate a runtime or LLM call
    # path — string "8002" is allowed inside docstrings as a safety
    # statement; we forbid actual binding/connecting to it.
    forbidden = ["import faiss", "from waggledance.runtime",
                  "from waggledance.core.faiss_store",
                  "ollama.generate(", "openai.chat", "anthropic.messages",
                  "requests.post(", ":8002/", "localhost:8002",
                  "127.0.0.1:8002"]
    for pat in forbidden:
        assert pat not in src, f"forbidden pattern {pat} in hive_proposes.py"


# ── 26. lifecycle persisting detected from history ────────────-

def test_lifecycle_persisting_detected_from_history(tmp_path):
    p = tmp_path / "HISTORY.jsonl"
    e = hist.make_entry(
        meta_proposal_id="aabbccddeeff", proposal_type="introspection_gap",
        output_dir="d_prev", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:t",
        prev_entry_sha256=hist.GENESIS_PREV,
        ts="2026-04-26T00:00:00+00:00",
    )
    hist.append_entry(p, e)
    entries = hist.read_entries(p)
    seen = hist.all_seen_ids(entries)
    prev = hist.latest_immediate_prev_run_ids(entries)
    assert "aabbccddeeff" in seen
    assert "aabbccddeeff" in prev
    assert ml.lifecycle_for("aabbccddeeff", seen, prev) == "persisting"


# ── 27. resolved proposals: prev minus current ────────────────-

def test_resolved_proposals_diff(tmp_path):
    p = tmp_path / "HISTORY.jsonl"
    for mid in ("aaa1", "bbb2", "ccc3"):
        e = hist.make_entry(
            meta_proposal_id=mid, proposal_type="introspection_gap",
            output_dir="d_prev", base_commit_hash="ab",
            pinned_input_manifest_sha256="sha256:t",
            prev_entry_sha256=hist.latest_prev_entry_sha256(
                hist.read_entries(p)
            ),
            ts="2026-04-26T00:00:00+00:00",
        )
        hist.append_entry(p, e)
    entries = hist.read_entries(p)
    prev = hist.latest_immediate_prev_run_ids(entries)
    curr = {"aaa1"}
    assert ml.resolved_proposals(prev, curr) == ["bbb2", "ccc3"]


# ── 28. fixture_fallback_used echoed into bundle ──────────────-

def test_fixture_fallback_used_propagated_into_bundle():
    bundle = rb.build_review_bundle(
        proposals=[], insufficient_evidence=[], rejected_candidates=[],
        resolved_proposal_ids=[], branch_name="b", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:t",
        consumed_hook_contracts=[],
        fixture_fallback_used={"dream_plane": {"used": True,
                                                  "reason": "no real artifact"}},
    )
    assert bundle["fixture_fallback_used"]["dream_plane"]["used"] is True


# ── 29. recommended_next_human_action only valid enum values ──-

def test_recommended_action_is_valid_enum():
    valid = {"review_for_future_PR", "archive_as_low_value",
             "wait_for_more_evidence",
             "post_campaign_runtime_review_candidate"}
    for confidence, priority in [(0.8, 0.10), (0.5, 0.03),
                                   (0.1, 0.001), (0.4, 0.005)]:
        p = _make_proposal(confidence=confidence, priority=priority)
        action = rb.recommend_action_for(p)
        assert action in valid


# ── 30. post_campaign_runtime_review_candidate stays advisory --

def test_post_campaign_marker_is_advisory_only():
    """Source must not act on the post_campaign marker — no merge,
    no axiom write, no runtime push."""
    pkg = ROOT / "waggledance" / "core" / "meta"
    for p in pkg.glob("*.py"):
        text = p.read_text(encoding="utf-8")
        forbidden = ["promote_to_runtime(", "axiom_write(",
                      "register_solver_in_runtime(", "merge_proposal_now("]
        for pat in forbidden:
            assert pat not in text, f"forbidden action {pat} in {p.name}"
    tools_p = ROOT / "tools" / "hive_proposes.py"
    text = tools_p.read_text(encoding="utf-8")
    for pat in forbidden:
        assert pat not in text
