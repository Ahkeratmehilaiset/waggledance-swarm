# SPDX-License-Identifier: BUSL-1.1
"""Phase 17B — Local AI Efficiency Benchmark harness tests.

These tests run ``tools/run_phase17b_local_efficiency_benchmark.py``
against a temp directory at small scale (--scale-descriptors 1000,
--scale-lookups 200) and assert the contract documented in
``docs/runs/phase17b_local_efficiency_benchmark_2026_05_04/benchmark_design.md``.

Why small scale inside the test:
    Full 10000 descriptors take ~150 s in the build phase. The
    harness is exercised at full 10k scale by the operator's local
    run (recorded in the committed
    `phase17b_local_efficiency_benchmark.json`) and by the post-merge
    rerun. The test here only proves the CODE PATH works end-to-end.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def out_dir(tmp_path: Path) -> Path:
    return tmp_path / "phase17b_benchmark_artifacts"


@pytest.fixture
def benchmark(out_dir: Path) -> dict:
    """Run the harness once at small scale and return the JSON."""
    saved = sys.argv[:]
    try:
        sys.argv = [
            "run_phase17b_local_efficiency_benchmark.py",
            "--out-dir", str(out_dir),
            "--skip-ollama",
            "--canonical-repeat", "1",
            "--scale-descriptors", "240",
            "--scale-lookups", "120",
            "--producer-repeat", "1",
        ]
        import run_phase17b_local_efficiency_benchmark as harness  # type: ignore
        rc = harness.main()
        assert rc == 0, (
            "benchmark harness exited non-zero (exit 2 means the Axis V2 "
            "entry preflight refused a dirty or unresolvable ROOT; run from "
            "a clean committed HEAD)"
        )
    finally:
        sys.argv = saved
    out = out_dir / "phase17b_local_efficiency_benchmark.json"
    assert out.is_file(), "benchmark JSON missing"
    return json.loads(out.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Top-level shape (master prompt P2 keys)
# ---------------------------------------------------------------------------

def test_benchmark_top_level_keys(benchmark: dict) -> None:
    required = {
        "phase", "benchmark_version", "schema_version",
        "branch", "started_at_utc", "finished_at_utc",
        "git_sha", "python_version", "platform", "docker_mode",
        "tracks", "scenarios", "claim_labels", "not_claimed",
        "summary", "provider_jobs_delta", "builder_jobs_delta",
        "release_gate_pass",
        "no_consciousness_claim", "no_beats_all_competitors_claim",
        "no_cloud_api_calls_this_session", "no_pull_or_download_this_session",
        "forbidden_claims_absent", "forbidden_vocabulary_excluded",
    }
    missing = required - set(benchmark.keys())
    assert not missing, f"missing top-level keys: {missing}"


def test_benchmark_phase_marker(benchmark: dict) -> None:
    assert benchmark["phase"] == "phase17b_local_efficiency_benchmark"
    assert benchmark["benchmark_version"] == "phase17b.v1"


def test_benchmark_release_gate_pass(benchmark: dict) -> None:
    assert benchmark["release_gate_pass"] is True


# ---------------------------------------------------------------------------
# Required tracks
# ---------------------------------------------------------------------------

REQUIRED_TRACKS = (
    "A_solver_hot_path",
    "B_capability_lookup_10k",
    "C_handle_query_e2e",
    "D_restart_continuity",
    "E_producer_fabric",
)


def test_benchmark_has_all_required_tracks(benchmark: dict) -> None:
    tracks = benchmark["tracks"]
    missing = set(REQUIRED_TRACKS) - set(tracks.keys())
    assert not missing, f"missing tracks: {missing}"


def test_each_track_has_required_metric_keys(benchmark: dict) -> None:
    required = {
        "correctness_count", "correctness_rate",
        "latency_ms_p50", "latency_ms_p95", "latency_ms_p99",
        "throughput_queries_per_second",
        "fallback_rate", "fifo_fallback_count",
        "provider_jobs_delta", "builder_jobs_delta",
        "rss_memory_mb", "docker_network_mode",
        "reproducibility_status", "audit_or_provenance_coverage",
        "claim_label",
    }
    for sid in REQUIRED_TRACKS:
        m = benchmark["tracks"][sid]["metrics"]
        missing = required - set(m.keys())
        assert not missing, f"track {sid} missing metric keys: {missing}"


# ---------------------------------------------------------------------------
# Inner-loop invariants (master prompt rule 13)
# ---------------------------------------------------------------------------

def test_provider_builder_delta_zero_top_level(benchmark: dict) -> None:
    assert benchmark["provider_jobs_delta"] == 0
    assert benchmark["builder_jobs_delta"] == 0


def test_each_track_provider_builder_delta_zero(benchmark: dict) -> None:
    for sid in REQUIRED_TRACKS:
        m = benchmark["tracks"][sid]["metrics"]
        assert m["provider_jobs_delta"] == 0, \
            f"{sid} provider_jobs_delta != 0"
        assert m["builder_jobs_delta"] == 0, \
            f"{sid} builder_jobs_delta != 0"


# ---------------------------------------------------------------------------
# Capability-lookup track honesty (synthetic vs canonical)
# ---------------------------------------------------------------------------

def test_capability_lookup_track_synthetic_label(benchmark: dict) -> None:
    raw = benchmark["tracks"]["B_capability_lookup_10k"]["raw"]
    assert raw.get("is_synthetic_scale") is True
    assert raw.get("not_canonical_corpus") is True


def test_capability_lookup_no_fifo_fallback(benchmark: dict) -> None:
    """All sampled capability queries hit the auto_promoted_solver
    source; zero FIFO fallback or miss is the strict pass criterion."""
    m = benchmark["tracks"]["B_capability_lookup_10k"]["metrics"]
    assert m["fifo_fallback_count"] == 0
    assert m["correctness_count"] >= 100
    assert m["correctness_rate"] is not None
    assert m["correctness_rate"] >= 0.999


# ---------------------------------------------------------------------------
# Canonical corpus size
# ---------------------------------------------------------------------------

def test_canonical_corpus_track_meets_phase17a_minimum(benchmark: dict) -> None:
    """Phase 17A grew the canonical seed corpus to 128. This track
    asserts the corpus is at least 128."""
    raw_a = benchmark["tracks"]["A_solver_hot_path"]["raw"]
    raw_c = benchmark["tracks"]["C_handle_query_e2e"]["raw"]
    raw_d = benchmark["tracks"]["D_restart_continuity"]["raw"]
    for raw in (raw_a, raw_c, raw_d):
        if raw.get("corpus_total") is not None:
            assert raw["corpus_total"] >= 128, (
                f"corpus_total < 128 in {raw.get('name')}"
            )


# ---------------------------------------------------------------------------
# Producer fabric honesty
# ---------------------------------------------------------------------------

def test_producer_fabric_track_emits_ir_objects(benchmark: dict) -> None:
    raw = benchmark["tracks"]["E_producer_fabric"]["raw"]
    assert raw.get("ir_objects_emitted_total") == 68


# ---------------------------------------------------------------------------
# Optional Ollama path does not affect required gates (rule 14)
# ---------------------------------------------------------------------------

def test_ollama_skipped_does_not_fail_gate(benchmark: dict) -> None:
    f_status = benchmark["scenarios"]["F_ollama_baseline"]["status"]
    # We ran with --skip-ollama in the test fixture.
    assert f_status in ("SKIPPED", "NOT_AVAILABLE_NOT_RUN",
                            "AVAILABLE_NOT_RUN_BY_DEFAULT")
    assert benchmark["release_gate_pass"] is True


# ---------------------------------------------------------------------------
# External competitor slots are NOT_RUN by design
# ---------------------------------------------------------------------------

def test_external_competitor_slots_documented_not_run(benchmark: dict) -> None:
    g = benchmark["scenarios"]["G_external_competitor_slots"]
    assert g["status"] == "NOT_RUN"
    slots = g["slots"]
    slot_names = {s["slot"] for s in slots}
    expected = {
        "frontier_anthropic_claude",
        "frontier_openai_gpt",
        "frontier_google_gemini",
        "local_llama_cpp",
        "local_vllm",
        "local_mistral_rs",
    }
    assert expected.issubset(slot_names)
    for s in slots:
        assert s["status"] == "NOT_RUN"
        assert "reason_not_run" in s
        assert "requirements_to_upgrade_to_measured" in s


# ---------------------------------------------------------------------------
# Claim labels — raw intelligence superiority is NOT CLAIMED
# ---------------------------------------------------------------------------

def test_raw_intelligence_not_claimed(benchmark: dict) -> None:
    cl = benchmark["claim_labels"]
    assert cl.get("raw_intelligence_vs_frontier_moe") == "NOT_CLAIMED"
    assert "raw_intelligence_vs_frontier_moe" in benchmark["not_claimed"]


def test_zero_provider_inner_loop_proven(benchmark: dict) -> None:
    cl = benchmark["claim_labels"]
    assert cl["zero_provider_inner_loop"] == "PROVEN"
    assert cl["deterministic_routing_solver_first"] == "PROVEN"


# ---------------------------------------------------------------------------
# Forbidden vocabulary check (master prompt rule 18)
# ---------------------------------------------------------------------------

FORBIDDEN_LITERAL: tuple[str, ...] = (
    "conscious", "sentient", "AGI",
    "revolutionary", "magical", "human-like mind", "self-aware",
    "explosive intelligence", "emergent",
    "beats all competitors", "world's best", "world's fastest",
)


def test_benchmark_md_no_forbidden_vocabulary(out_dir: Path,
                                                    benchmark: dict
                                                    ) -> None:
    md_path = out_dir / "phase17b_local_efficiency_benchmark.md"
    assert md_path.is_file()
    text = md_path.read_text(encoding="utf-8").lower()
    # The harness embeds the forbidden_vocabulary list in the JSON for
    # auditability. The MD itself must not USE those words in claim
    # text. We allow the literal denylist appearing inside a JSON
    # block by checking only outside ```json``` fences.
    in_json = False
    flat: list[str] = []
    for line in text.splitlines():
        if line.strip().startswith("```json"):
            in_json = True
            continue
        if line.strip() == "```" and in_json:
            in_json = False
            continue
        if in_json:
            continue
        flat.append(line)
    body = "\n".join(flat)
    for word in FORBIDDEN_LITERAL:
        assert word.lower() not in body, (
            f"forbidden vocabulary {word!r} appears in benchmark MD body "
            f"outside JSON fences"
        )


def test_benchmark_explicitly_disclaims_consciousness(benchmark: dict
                                                            ) -> None:
    assert benchmark["no_consciousness_claim"] is True
    assert benchmark["no_beats_all_competitors_claim"] is True
    assert benchmark["forbidden_claims_absent"] is True
    assert benchmark["no_cloud_api_calls_this_session"] is True
    assert benchmark["no_pull_or_download_this_session"] is True


# ---------------------------------------------------------------------------
# Axis V2 (2026-09-02): entry source-subject peel + B-first --source-commit
# ---------------------------------------------------------------------------

FAKE_SHA = "0123456789abcdef0123456789abcdef01234567"
SCENARIO_ORDER = (
    "B_capability_lookup_10k",
    "A_solver_hot_path",
    "C_handle_query_e2e",
    "D_restart_continuity",
    "E_producer_fabric",
)


def _harness():
    import run_phase17b_local_efficiency_benchmark as harness  # type: ignore
    return harness


def _fake_proof(harness, **kwargs):
    return harness.ProofRunResult(
        name=kwargs["name"],
        tool=kwargs["tool"],
        out_json_path=None,
        runtime_seconds=0.01,
        exit_code=0,
        parsed_json={},
        stderr_tail="",
        passed=True,
    )


def _git(root, *args):
    return subprocess.run(
        [
            "git",
            "-c", "user.name=axis-v2-test",
            "-c", "user.email=axis-v2-test@example.invalid",
            "-c", "commit.gpgsign=false",
            "-c", "core.autocrlf=false",
            "-c", "core.longpaths=true",
            "-C", str(root),
            *args,
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def test_scenario_b_runs_first_with_peeled_source_commit(tmp_path, monkeypatch) -> None:
    """Unit lock: B first, exact --source-commit argv, same SHA as git_sha.

    The proof subprocesses and the entry peel are replaced so the live
    ROOT is never read, run or dirtied by this test.
    """
    harness = _harness()
    calls: list[tuple[str, list[str]]] = []

    def _record(**kwargs):
        calls.append((kwargs["name"], list(kwargs.get("extra_args") or [])))
        return _fake_proof(harness, **kwargs)

    monkeypatch.setattr(harness, "run_proof_subprocess", _record)
    monkeypatch.setattr(
        harness, "peel_source_subject", lambda root=None: (FAKE_SHA, None)
    )
    out_dir = tmp_path / "unit_lock_out"

    rc = harness.main([
        "--out-dir", str(out_dir),
        "--skip-ollama",
        "--scale-descriptors", "240",
        "--scale-lookups", "120",
    ])

    assert rc == 0
    assert tuple(name for name, _ in calls) == SCENARIO_ORDER
    b_args = calls[0][1]
    assert b_args[b_args.index("--source-commit") + 1] == FAKE_SHA
    assert b_args[b_args.index("--descriptors") + 1] == "240"
    assert b_args[b_args.index("--lookup-pass-count") + 1] == "120"
    assert all("--source-commit" not in args for _, args in calls[1:])
    benchmark = json.loads(
        (out_dir / "phase17b_local_efficiency_benchmark.json").read_text(
            encoding="utf-8"
        )
    )
    assert benchmark["git_sha"] == FAKE_SHA
    assert benchmark["tracks"]["B_capability_lookup_10k"]["raw"]["tool"] == (
        "tools/run_solver_scale_proof.py"
    )


@pytest.mark.parametrize(
    "blocker",
    ["worktree_dirty", "git_unavailable", "git_revision_unresolvable",
     "git_status_unavailable"],
)
def test_entry_preflight_failure_aborts_before_any_scenario(
    tmp_path, monkeypatch, capsys, blocker
) -> None:
    harness = _harness()
    calls: list[str] = []

    def _record(**kwargs):
        calls.append(kwargs["name"])
        return _fake_proof(harness, **kwargs)

    monkeypatch.setattr(harness, "run_proof_subprocess", _record)
    monkeypatch.setattr(
        harness, "peel_source_subject", lambda root=None: (None, blocker)
    )
    out_dir = tmp_path / "abort_out"

    rc = harness.main(["--out-dir", str(out_dir), "--skip-ollama"])

    assert rc == 2
    assert calls == []
    assert not out_dir.exists()
    assert blocker in capsys.readouterr().err


def test_peel_source_subject_on_throwaway_repo(tmp_path) -> None:
    """The peel is exact and fail-closed; exercised on a throwaway repo only."""
    harness = _harness()
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    (root / "f.txt").write_text("x\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "x")
    head = _git(root, "rev-parse", "HEAD").stdout.strip()

    assert harness.peel_source_subject(root) == (head, None)

    (root / "stray.txt").write_text("stray\n", encoding="utf-8")
    assert harness.peel_source_subject(root) == (None, "worktree_dirty")

    (root / "stray.txt").unlink()
    (root / "f.txt").write_text("y\n", encoding="utf-8")
    assert harness.peel_source_subject(root) == (None, "worktree_dirty")

    plain = tmp_path / "plain"
    plain.mkdir()
    assert harness.peel_source_subject(plain) == (
        None, "git_revision_unresolvable"
    )


def test_peel_source_subject_git_unavailable(tmp_path, monkeypatch) -> None:
    harness = _harness()
    import tools.verify_release_soak_evidence as verifier

    monkeypatch.setattr(
        verifier, "GIT_EXECUTABLE", "git-axis-v2-definitely-missing"
    )

    assert harness.peel_source_subject(tmp_path) == (None, "git_unavailable")


@pytest.mark.parametrize(
    "argv",
    [
        ["--source-commit", FAKE_SHA],
        ["--allow-dirty"],
        ["--skip-preflight"],
        ["--git-sha", FAKE_SHA],
    ],
    ids=["source-commit", "allow-dirty", "skip-preflight", "git-sha"],
)
def test_harness_exposes_no_bypass_or_stamp_flags(tmp_path, argv) -> None:
    harness = _harness()

    with pytest.raises(SystemExit) as excinfo:
        harness.main(["--out-dir", str(tmp_path / "never"), *argv])

    assert excinfo.value.code == 2
    assert not (tmp_path / "never").exists()
