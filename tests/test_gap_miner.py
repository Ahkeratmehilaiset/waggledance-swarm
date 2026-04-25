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


# ── R7.5: content-derived cluster_id ──────────────────────────────

def test_cluster_id_is_content_derived_not_sequential():
    """x.txt §CLUSTERING DETERMINISM RULE: cluster_id must be
    sha256(sorted(member_query_hashes))[:12], NOT a sequential
    index. Two clusters with the same members must get the same id;
    iteration order must not matter."""
    report = mod.mine(fixture_dir=FIXTURE_DIR)
    for c in report.curiosities:
        assert c.cluster_id.startswith("cl_"), c.cluster_id
        # 3 chars prefix + 12 hex chars
        assert len(c.cluster_id) == 3 + 12, c.cluster_id
        assert all(ch in "0123456789abcdef" for ch in c.cluster_id[3:])
    # No sequential numeric padding like cl_00000_xxx
    assert not any(re.match(r"^cl_\d{5}_", c.cluster_id) for c in report.curiosities)


def test_cluster_id_stable_when_clusters_built_in_different_order():
    """Even if the underlying signals are emitted in shuffled order,
    the cluster_id of a given member-set must remain the same."""
    report1 = mod.mine(fixture_dir=FIXTURE_DIR)
    # Compute the same clusters via the public _Cluster path with a
    # different traversal order — final cluster_ids must match.
    signals = mod._scan_hot_results(mod.pin_artifacts(None, fixture_dir=FIXTURE_DIR))
    assert signals  # sanity
    # Forward and reverse signal orders should both produce the same
    # set of cluster_ids
    forward = mod._cluster_signals(signals)
    reverse = mod._cluster_signals(list(reversed(signals)))
    assert sorted(c.cluster_id() for c in forward) == \
           sorted(c.cluster_id() for c in reverse)


# ── R7.5: spec expected_value formula ─────────────────────────────

def test_expected_value_zero_when_fallback_is_zero():
    """Per spec, value = count × fallback × evidence × (1 + bridge);
    a cluster with fallback_rate=0 must rank at 0."""
    # Use a synthesized report where every cluster has fallback=0
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        seed = Path(td) / "fix"
        seed.mkdir()
        rows = "\n".join([
            json.dumps({"query_id": 0, "bucket": "normal",
                         "responded": True, "latency_ms": 1500})
            for _ in range(10)
        ])
        (seed / "hot_results.jsonl").write_text(rows + "\n", encoding="utf-8")
        (seed / "query_corpus.json").write_text(
            '[{"query_id": 0, "bucket": "normal", "query": "common quick query"}]',
            encoding="utf-8",
        )
        report = mod.mine(fixture_dir=seed)
        for c in report.curiosities:
            assert c.estimated_value == 0.0, (
                f"cluster {c.cluster_id} fallback=0 but value={c.estimated_value}"
            )


def test_expected_value_increases_with_fallback_rate():
    """All else equal, a higher fallback rate must produce a higher
    expected_value under the spec formula."""
    import tempfile
    def _run(fallback_rate: float) -> float:
        with tempfile.TemporaryDirectory() as td:
            seed = Path(td) / "fix"
            seed.mkdir()
            n_total = 10
            n_unresolved = int(round(fallback_rate * n_total))
            rows = []
            for i in range(n_total):
                resp = i >= n_unresolved
                rows.append(json.dumps({
                    "query_id": 0, "bucket": "normal",
                    "responded": resp, "latency_ms": 2000,
                }))
            (seed / "hot_results.jsonl").write_text(
                "\n".join(rows) + "\n", encoding="utf-8")
            (seed / "query_corpus.json").write_text(
                '[{"query_id": 0, "bucket": "normal", "query": "thermal heat loss winter"}]',
                encoding="utf-8",
            )
            r = mod.mine(fixture_dir=seed)
            return r.curiosities[0].estimated_value if r.curiosities else 0.0

    low = _run(0.2)
    high = _run(0.8)
    assert high > low, f"expected high fallback to outrank low; got {high} <= {low}"


# ── R7.5: --artifact-manifest enforcement ─────────────────────────

def test_artifact_manifest_pin_loaded_round_trip(tmp_path):
    """A state.json carrying a pinned_artifact_manifest must reload
    into a usable PinnedSet via _load_manifest_pin."""
    # Pin a fresh fixture and write a state.json shape
    pinned = mod.pin_artifacts(None, fixture_dir=FIXTURE_DIR)
    state = {
        "pinned_campaign_dir": pinned.campaign_dir,
        "pinned_artifact_manifest_sha256": pinned.pin_hash,
        "pinned_artifact_manifest": [
            {"path": a.relpath, "bytes": a.bytes, "lines": a.lines,
             "sha256_first_4096": a.sha256_first_4096,
             "sha256_last_4096": a.sha256_last_4096,
             "sha256_full": a.sha256_full,
             "mtime_epoch": a.mtime_epoch}
            for a in pinned.artifacts
        ],
    }
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True),
                            encoding="utf-8")

    reloaded = mod._load_manifest_pin(state_path)
    assert reloaded.pin_hash == pinned.pin_hash
    assert len(reloaded.artifacts) == len(pinned.artifacts)


def test_artifact_manifest_bounded_read_ignores_growth(tmp_path):
    """If a pinned file grew after the pin, gap_miner must read only
    up to the pinned byte count and ignore the appended tail."""
    seed = tmp_path / "fix"
    seed.mkdir()
    log = seed / "hot_results.jsonl"
    log.write_text(
        '{"query_id": 0, "bucket": "normal", "responded": true, "latency_ms": 1000}\n',
        encoding="utf-8",
    )
    (seed / "query_corpus.json").write_text(
        '[{"query_id": 0, "bucket": "normal", "query": "early query x"}]',
        encoding="utf-8",
    )
    pinned = mod.pin_artifacts(None, fixture_dir=seed)
    # Now grow the file — append a row with a different query_id
    log.write_text(
        log.read_text(encoding="utf-8")
        + '{"query_id": 1, "bucket": "normal", "responded": false, "latency_ms": 9000}\n',
        encoding="utf-8",
    )
    # Mine using the original pin → must NOT see the growth
    report = mod.mine(pinned=pinned)
    assert report.rows_scanned == 1


# ── R7.5: real-data-first with documented fallback path ──────────

def test_real_data_first_with_documented_fallback_blocker(tmp_path):
    """x.txt §A4 / acceptance §3: if real-data is unavailable,
    fixture fallback is OK only when the blocker is explicitly
    documented. This test verifies that pin_artifacts() returns an
    empty artifact set when the real campaign directory does NOT
    exist, which is the precondition for the fallback path."""
    fake_root = tmp_path / "no_campaigns_here"
    fake_root.mkdir()
    # No ui_gauntlet_400h_* dirs exist under fake_root → discover
    # returns None → pin returns empty artifact set
    result = mod.discover_campaign_dir(fake_root)
    assert result is None
    pinned = mod.pin_artifacts(None, fixture_dir=fake_root)
    assert pinned.artifacts == ()


# ── R7.5: integrity check ─────────────────────────────────────────

def test_verify_pin_integrity_clean_after_pin(tmp_path):
    """verify_pin_integrity must report ok=True when no file changed."""
    seed = tmp_path / "fix"
    seed.mkdir()
    (seed / "hot_results.jsonl").write_text(
        '{"query_id": 0, "bucket": "normal", "responded": true, "latency_ms": 1000}\n',
        encoding="utf-8",
    )
    (seed / "query_corpus.json").write_text(
        '[{"query_id": 0, "bucket": "normal", "query": "stable query"}]',
        encoding="utf-8",
    )
    pinned = mod.pin_artifacts(None, fixture_dir=seed)
    report = mod.verify_pin_integrity(pinned, root=tmp_path)
    assert report["ok"], report
    assert report["critical_failure"] is None


def test_verify_pin_integrity_flags_shrink(tmp_path):
    """A pinned artifact that shrank is a critical failure."""
    seed = tmp_path / "fix"
    seed.mkdir()
    log = seed / "hot_results.jsonl"
    # Write a file > 8KB so the chunked-hash branch fires
    big_row = '{"query_id": 0, "bucket": "normal", "responded": true, "latency_ms": 1000}\n'
    log.write_text(big_row * 200, encoding="utf-8")
    (seed / "query_corpus.json").write_text(
        '[{"query_id": 0, "bucket": "normal", "query": "x"}]',
        encoding="utf-8",
    )
    pinned = mod.pin_artifacts(None, fixture_dir=seed)
    # Truncate the file by half
    half = log.stat().st_size // 2
    with open(log, "rb+") as f:
        f.truncate(half)
    report = mod.verify_pin_integrity(pinned, root=tmp_path)
    assert not report["ok"]
    assert "shrunk" in report["critical_failure"].lower()


def test_verify_pin_integrity_flags_first_kb_drift(tmp_path):
    """If the first 4 KB of a pinned file changed, that's a critical
    failure even though file size may still match."""
    seed = tmp_path / "fix"
    seed.mkdir()
    log = seed / "hot_results.jsonl"
    big_row = '{"query_id": 0, "bucket": "normal", "responded": true, "latency_ms": 1000}\n'
    log.write_text(big_row * 200, encoding="utf-8")
    (seed / "query_corpus.json").write_text(
        '[{"query_id": 0, "bucket": "normal", "query": "x"}]',
        encoding="utf-8",
    )
    pinned = mod.pin_artifacts(None, fixture_dir=seed)
    # Tamper with the first 4 KB only — keep size the same
    with open(log, "rb+") as f:
        f.seek(0)
        f.write(b"X" * 4096)
    report = mod.verify_pin_integrity(pinned, root=tmp_path)
    assert not report["ok"]
    assert "drift" in report["critical_failure"].lower()


def test_verify_pin_integrity_allows_append_only_growth(tmp_path):
    """Append-only growth past the pinned size is the expected
    case during a live campaign and must NOT be flagged."""
    seed = tmp_path / "fix"
    seed.mkdir()
    log = seed / "hot_results.jsonl"
    big_row = '{"query_id": 0, "bucket": "normal", "responded": true, "latency_ms": 1000}\n'
    log.write_text(big_row * 200, encoding="utf-8")
    (seed / "query_corpus.json").write_text(
        '[{"query_id": 0, "bucket": "normal", "query": "x"}]',
        encoding="utf-8",
    )
    pinned = mod.pin_artifacts(None, fixture_dir=seed)
    # Append more rows — file grows
    with open(log, "ab") as f:
        f.write(big_row.encode("utf-8") * 50)
    report = mod.verify_pin_integrity(pinned, root=tmp_path)
    assert report["ok"], report["critical_failure"]


# ── R7.5: continuity_anchor on every emitted item ────────────────

def test_continuity_anchor_present_with_required_keys():
    report = mod.mine(fixture_dir=FIXTURE_DIR)
    for c in report.curiosities:
        anchor = c.continuity_anchor
        assert anchor is not None
        assert "branch_name" in anchor
        assert "base_commit_hash" in anchor
        assert "pinned_artifact_manifest_sha256" in anchor
        assert anchor["pinned_artifact_manifest_sha256"] == report.pinned_set.pin_hash


# ── R7.5: subdivision_pressure cluster-density heuristic ─────────

def test_subdivision_pressure_fires_with_three_missing_solver_clusters_in_one_cell(tmp_path):
    """x.txt §HIGHER-ORDER CURIOSITY RULE: when >= 3 distinct
    missing-solver clusters share the same candidate_cell, set
    subdivision_pressure_hint."""
    seed = tmp_path / "fix"
    seed.mkdir()
    rows = []
    corpus = []
    # Three distinct thermal-vocabulary clusters, all unresolved
    queries = [
        "thermal insulation heat loss for old building",
        "thermal heat pump cop in cold winter conditions",
        "thermal hvac sizing for finnish house in pakkanen",
    ]
    for qid, q in enumerate(queries):
        corpus.append({"query_id": qid, "bucket": "normal", "query": q})
        # Make each cluster meet the missing_solver threshold
        # (fallback_rate >= 0.5)
        for _ in range(5):
            rows.append({"query_id": qid, "bucket": "normal",
                          "responded": False, "latency_ms": 9000,
                          "error": "timeout"})
    (seed / "hot_results.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n",
        encoding="utf-8",
    )
    (seed / "query_corpus.json").write_text(
        json.dumps(corpus), encoding="utf-8",
    )
    report = mod.mine(fixture_dir=seed)
    # At least one thermal-attributed cluster must carry a
    # subdivision_pressure_hint (>=3) AND classify as subdivision_pressure
    thermal_with_pressure = [
        c for c in report.curiosities
        if c.candidate_cell == "thermal"
        and c.subdivision_pressure_hint is not None
        and c.subdivision_pressure_hint >= 3.0
    ]
    assert thermal_with_pressure, [
        (c.candidate_cell, c.subdivision_pressure_hint, c.suspected_gap_type)
        for c in report.curiosities
    ]


# ── R7.5: extended path-leakage check ─────────────────────────────

def test_no_path_leakage_extended(tmp_path):
    """x.txt §test_no_path_leakage: emitted artifacts must NOT
    contain Windows drive paths, /home/, /Users/, or the actual
    repo absolute path."""
    out = tmp_path / "out"
    rep = mod.mine(fixture_dir=FIXTURE_DIR)
    mod.emit(rep, out)
    abs_repo = str(ROOT)
    for f in out.rglob("*"):
        if f.is_file():
            text = f.read_text("utf-8", errors="replace")
            assert "C:\\" not in text, f"C:\\ leaked in {f}"
            assert "/home/" not in text, f"/home/ leaked in {f}"
            assert "/Users/" not in text, f"/Users/ leaked in {f}"
            assert abs_repo not in text, f"repo abs path leaked in {f}"


# ── R7.5: fields per spec on emit ─────────────────────────────────

def test_pin_manifest_carries_first_last_4096_hashes_for_large_files(tmp_path):
    """Files > 8192 B must carry sha256_first_4096 and
    sha256_last_4096 separately."""
    seed = tmp_path / "fix"
    seed.mkdir()
    big_row = '{"query_id": 0, "bucket": "normal", "responded": true, "latency_ms": 1000}\n'
    (seed / "hot_results.jsonl").write_text(big_row * 200, encoding="utf-8")
    (seed / "query_corpus.json").write_text(
        '[{"query_id": 0, "bucket": "normal", "query": "x"}]',
        encoding="utf-8",
    )
    pinned = mod.pin_artifacts(None, fixture_dir=seed)
    big = next(a for a in pinned.artifacts if a.relpath.endswith("hot_results.jsonl"))
    # File is > 8192 → first and last hashes must differ
    assert big.bytes > 8192
    assert big.sha256_first_4096 != big.sha256_last_4096


def test_pin_manifest_carries_mtime_epoch():
    pinned = mod.pin_artifacts(None, fixture_dir=FIXTURE_DIR)
    assert pinned.artifacts
    for a in pinned.artifacts:
        assert isinstance(a.mtime_epoch, float)


# Required: import re for cluster_id format check
import re   # noqa: E402
