"""Targeted tests for tools/gap_miner.py — Phase 8.5 curiosity organ.

Determinism is the core guarantee: every emitted artifact family must
be byte-identical across two runs against the same pinned fixture.
Other tests cover empty/missing input tolerance, malformed-JSONL
handling, cell attribution, ranking, the higher-order curiosity
contract, and absence of secret/absolute-path leakage.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "gap_miner_sample"


def _load_mod():
    path = ROOT / "tools" / "gap_miner.py"
    spec = importlib.util.spec_from_file_location("gap_miner", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["gap_miner"] = mod
    spec.loader.exec_module(mod)
    return mod


mod = _load_mod()


# ── Pinning ────────────────────────────────────────────────────────

def test_pin_includes_fixture_artifacts():
    pinned = mod.pin_artifacts(None, fixture_dir=FIXTURE_DIR)
    relpaths = {a.relpath for a in pinned.artifacts}
    assert any("hot_results.jsonl" in p for p in relpaths)
    assert any("query_corpus.json" in p for p in relpaths)


def test_pin_hash_stable_across_runs():
    a = mod.pin_artifacts(None, fixture_dir=FIXTURE_DIR)
    b = mod.pin_artifacts(None, fixture_dir=FIXTURE_DIR)
    assert a.pin_hash == b.pin_hash


def test_pin_hash_changes_when_fixture_changes(tmp_path):
    seed = tmp_path / "fix"
    seed.mkdir()
    (seed / "hot_results.jsonl").write_text(
        '{"query_id": 0, "bucket": "normal", "responded": true, "latency_ms": 1000}\n',
        encoding="utf-8",
    )
    (seed / "query_corpus.json").write_text(
        '[{"query_id": 0, "bucket": "normal", "query": "test query"}]',
        encoding="utf-8",
    )
    a = mod.pin_artifacts(None, fixture_dir=seed)
    # Mutate
    (seed / "hot_results.jsonl").write_text(
        '{"query_id": 0, "bucket": "normal", "responded": false, "latency_ms": 9000}\n',
        encoding="utf-8",
    )
    b = mod.pin_artifacts(None, fixture_dir=seed)
    assert a.pin_hash != b.pin_hash


# ── Mining basics ─────────────────────────────────────────────────

def test_missing_inputs_yield_empty_report(tmp_path):
    """Pin against an empty dir → no signals, no curiosities."""
    empty = tmp_path / "empty"
    empty.mkdir()
    report = mod.mine(fixture_dir=empty)
    assert report.rows_scanned == 0
    assert report.curiosities == ()


def test_empty_jsonl_handled(tmp_path):
    seed = tmp_path / "fix"
    seed.mkdir()
    (seed / "hot_results.jsonl").write_text("", encoding="utf-8")
    (seed / "query_corpus.json").write_text("[]", encoding="utf-8")
    report = mod.mine(fixture_dir=seed)
    assert report.rows_scanned == 0


def test_malformed_jsonl_lines_skipped():
    """Fixture's hot_results contains one `not-valid-json` line; the
    miner must tolerate it and still produce signals."""
    report = mod.mine(fixture_dir=FIXTURE_DIR)
    assert report.rows_scanned > 0


def test_real_fixture_produces_curiosities():
    report = mod.mine(fixture_dir=FIXTURE_DIR)
    assert len(report.curiosities) >= 1


# ── Determinism ────────────────────────────────────────────────────

def test_summary_dict_byte_identical_across_runs():
    a = mod.mine(fixture_dir=FIXTURE_DIR)
    b = mod.mine(fixture_dir=FIXTURE_DIR)
    sa = mod._to_summary_dict(a)
    sb = mod._to_summary_dict(b)
    # Compare canonical JSON bytes for byte-identity
    ja = json.dumps(sa, sort_keys=True, indent=2)
    jb = json.dumps(sb, sort_keys=True, indent=2)
    assert ja == jb


def test_jsonl_lines_byte_identical_across_runs():
    a = mod.mine(fixture_dir=FIXTURE_DIR)
    b = mod.mine(fixture_dir=FIXTURE_DIR)
    assert mod._to_jsonl_lines(a) == mod._to_jsonl_lines(b)


def test_markdown_byte_identical_across_runs():
    a = mod.mine(fixture_dir=FIXTURE_DIR)
    b = mod.mine(fixture_dir=FIXTURE_DIR)
    assert mod._to_markdown(a) == mod._to_markdown(b)


def test_teacher_packs_byte_identical_across_runs():
    a = mod.mine(fixture_dir=FIXTURE_DIR)
    b = mod.mine(fixture_dir=FIXTURE_DIR)
    pa = mod._to_teacher_packs(a)
    pb = mod._to_teacher_packs(b)
    # Same set of cells, same dumped JSON
    assert set(pa) == set(pb)
    for cell in pa:
        ja = json.dumps(pa[cell], sort_keys=True, indent=2)
        jb = json.dumps(pb[cell], sort_keys=True, indent=2)
        assert ja == jb, f"teacher pack for {cell} drifted"


def test_emit_writes_byte_identical_files_on_repeat(tmp_path):
    """End-to-end: emit on disk twice; both runs must produce
    byte-identical files (R6-style strict determinism)."""
    out_a = tmp_path / "run_a"
    out_b = tmp_path / "run_b"
    rep = mod.mine(fixture_dir=FIXTURE_DIR)
    mod.emit(rep, out_a)
    rep2 = mod.mine(fixture_dir=FIXTURE_DIR)
    mod.emit(rep2, out_b)

    for name in ("curiosity_summary.json", "curiosity_log.jsonl",
                 "curiosity_report.md"):
        assert (out_a / name).read_text("utf-8") == \
               (out_b / name).read_text("utf-8"), f"{name} drifted"
    # Per-cell packs too
    pack_files_a = sorted((out_a / "teacher_packs").iterdir())
    pack_files_b = sorted((out_b / "teacher_packs").iterdir())
    assert [p.name for p in pack_files_a] == [p.name for p in pack_files_b]
    for pa, pb in zip(pack_files_a, pack_files_b):
        assert pa.read_text("utf-8") == pb.read_text("utf-8")


# ── Cell attribution ──────────────────────────────────────────────

def test_cell_attribution_hits_thermal_for_thermal_query():
    cell = mod._attribute_to_cell(
        "What thermal insulation thickness for heat loss"
    )
    assert cell == "thermal"


def test_cell_attribution_returns_none_for_unrelated_query():
    cell = mod._attribute_to_cell("Random unrelated text without keywords")
    assert cell is None


def test_real_fixture_attributes_thermal_cluster():
    """Fixture row qid=4 is about thermal insulation; a curiosity
    item should attribute it to the thermal cell."""
    report = mod.mine(fixture_dir=FIXTURE_DIR)
    cells_seen = {c.candidate_cell for c in report.curiosities}
    assert "thermal" in cells_seen


# ── Higher-order curiosity contract ───────────────────────────────

def test_recommended_action_set_subset_of_taxonomy():
    report = mod.mine(fixture_dir=FIXTURE_DIR)
    valid = set(mod.NEXT_ACTIONS)
    for c in report.curiosities:
        assert c.recommended_next_action in valid


def test_gap_type_set_subset_of_taxonomy():
    report = mod.mine(fixture_dir=FIXTURE_DIR)
    valid = set(mod.GAP_TYPES)
    for c in report.curiosities:
        assert c.suspected_gap_type in valid


def test_low_evidence_downgrades_to_do_nothing(tmp_path):
    """A single-row cluster with low evidence and no subdivision
    pressure should downgrade to do_nothing."""
    seed = tmp_path / "fix"
    seed.mkdir()
    (seed / "hot_results.jsonl").write_text(
        '{"query_id": 0, "bucket": "normal", "responded": true, "latency_ms": 2000}\n',
        encoding="utf-8",
    )
    (seed / "query_corpus.json").write_text(
        '[{"query_id": 0, "bucket": "normal", "query": "isolated lone query xyz"}]',
        encoding="utf-8",
    )
    report = mod.mine(fixture_dir=seed)
    assert all(
        c.recommended_next_action == "do_nothing"
        or c.evidence_strength != "low"
        for c in report.curiosities
    )


def test_unresolved_dominant_cluster_classifies_as_missing_solver(tmp_path):
    seed = tmp_path / "fix"
    seed.mkdir()
    # qid=1 has 4 unresolved entries / 0 resolved → fallback_rate 1.0
    (seed / "hot_results.jsonl").write_text(
        "\n".join([
            '{"query_id": 1, "bucket": "normal", "responded": false, "latency_ms": 8000, "error": "x"}',
            '{"query_id": 1, "bucket": "normal", "responded": false, "latency_ms": 8500, "error": "x"}',
            '{"query_id": 1, "bucket": "normal", "responded": false, "latency_ms": 9000, "error": "x"}',
            '{"query_id": 1, "bucket": "normal", "responded": false, "latency_ms": 9500, "error": "x"}',
        ]) + "\n",
        encoding="utf-8",
    )
    (seed / "query_corpus.json").write_text(
        '[{"query_id": 1, "bucket": "normal", "query": "thermal heat pump for cold winter"}]',
        encoding="utf-8",
    )
    report = mod.mine(fixture_dir=seed)
    assert any(c.suspected_gap_type == "missing_solver" for c in report.curiosities)


# ── Ranking ────────────────────────────────────────────────────────

def test_curiosities_sorted_by_estimated_value_desc():
    report = mod.mine(fixture_dir=FIXTURE_DIR)
    values = [c.estimated_value for c in report.curiosities]
    assert values == sorted(values, reverse=True)


# ── No secret / absolute-path leakage ─────────────────────────────

def test_emitted_artifacts_have_no_secret_tokens(tmp_path):
    out = tmp_path / "out"
    rep = mod.mine(fixture_dir=FIXTURE_DIR)
    mod.emit(rep, out)
    for f in out.rglob("*"):
        if f.is_file():
            text = f.read_text("utf-8", errors="replace")
            assert "WAGGLE_API_KEY" not in text
            assert "Bearer " not in text
            assert "gnt_" not in text


def test_emitted_artifacts_have_no_windows_user_paths(tmp_path):
    out = tmp_path / "out"
    rep = mod.mine(fixture_dir=FIXTURE_DIR)
    mod.emit(rep, out)
    for f in out.rglob("*"):
        if f.is_file():
            text = f.read_text("utf-8", errors="replace")
            assert "C:\\Users" not in text
            assert "U:\\" not in text


# ── CLI smoke ─────────────────────────────────────────────────────

def test_cli_help_exits_zero():
    import subprocess
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "gap_miner.py"), "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "gap_miner" in result.stdout.lower() or "usage" in result.stdout.lower()


def test_cli_dry_run_against_fixture_exits_zero(tmp_path):
    import subprocess
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "gap_miner.py"),
         "--fixture", str(FIXTURE_DIR)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "rows_scanned" in result.stdout.lower() or \
           "rows scanned" in result.stdout.lower()


# ── Pinned-set integrity ──────────────────────────────────────────

def test_curiosity_carries_pinned_artifact_root():
    report = mod.mine(fixture_dir=FIXTURE_DIR)
    assert all(c.pinned_artifact_root == report.pinned_set.campaign_dir
                for c in report.curiosities)


def test_curiosity_id_deterministic_across_mines():
    a = mod.mine(fixture_dir=FIXTURE_DIR)
    b = mod.mine(fixture_dir=FIXTURE_DIR)
    ids_a = sorted(c.curiosity_id for c in a.curiosities)
    ids_b = sorted(c.curiosity_id for c in b.curiosities)
    assert ids_a == ids_b
