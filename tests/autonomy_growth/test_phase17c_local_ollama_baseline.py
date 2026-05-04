# SPDX-License-Identifier: BUSL-1.1
"""Phase 17C - Local Ollama Baseline harness tests.

Covers:

    * MEASURED path (fake-ollama shim returns success for all 30 prompts).
    * NOT_AVAILABLE_NOT_RUN path (no ollama binary; --allow-no-ollama-track).
    * FAILED path (fake-ollama shim returns non-zero for half the prompts).
    * Forbidden-substring path (rendered MD contains a denylist word).

The Phase 17B aggregator is replaced with a tiny stub
(``_phase17c_stub_phase17b.py``) so the unit test suite runs in well
under a second.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = ROOT / "tools"
TESTS_DIR = Path(__file__).resolve().parent
STUB_BINARY = TESTS_DIR / "_phase17c_stub_phase17b.py"

if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))


def _import_harness():
    """Force-reimport the harness module so monkeypatched globals
    (shutil, subprocess) are picked up fresh per-test."""
    name = "run_phase17c_local_ollama_baseline"
    if name in sys.modules:
        del sys.modules[name]
    import importlib
    return importlib.import_module(name)


# ---------------------------------------------------------------------------
# Fake ollama: replaces shutil.which AND subprocess.run inside the harness
# ---------------------------------------------------------------------------

def make_fake_run(*,
                    real_run,
                    fake_binary: str,
                    behavior: str,
                    models: list[str],
                    version: str = "ollama version is 0.99.0",
                    inject_md_word: str | None = None):
    fail_indices = set()
    if behavior == "fail_half":
        # Fail prompts 0, 2, 4, ... (alternating).
        fail_indices = set(range(0, 30, 2))
    state = {"prompt_count": 0}

    def fake_run(args, **kwargs):
        if isinstance(args, list) and args:
            cmd = str(args[0])
            if cmd == fake_binary or cmd.endswith("ollama") or cmd.endswith("ollama.exe"):
                # Decode the subcommand.
                if len(args) >= 2 and args[1] == "--version":
                    return subprocess.CompletedProcess(
                        args=args, returncode=0,
                        stdout=version, stderr="",
                    )
                if len(args) >= 2 and args[1] == "list":
                    header = "NAME ID SIZE MODIFIED\n"
                    body = "\n".join(f"{m} fakeid 1.0GB now" for m in models)
                    return subprocess.CompletedProcess(
                        args=args, returncode=0,
                        stdout=header + body + "\n", stderr="",
                    )
                if len(args) >= 4 and args[1] == "run":
                    idx = state["prompt_count"]
                    state["prompt_count"] += 1
                    if behavior == "all_succeed":
                        return subprocess.CompletedProcess(
                            args=args, returncode=0,
                            stdout=f"OK-{idx}\n", stderr="",
                        )
                    if behavior == "fail_half":
                        if idx in fail_indices:
                            return subprocess.CompletedProcess(
                                args=args, returncode=1,
                                stdout="", stderr="model crashed\n",
                            )
                        return subprocess.CompletedProcess(
                            args=args, returncode=0,
                            stdout=f"OK-{idx}\n", stderr="",
                        )
                    if behavior == "all_fail":
                        return subprocess.CompletedProcess(
                            args=args, returncode=1,
                            stdout="", stderr="all fail\n",
                        )
        # Anything else (git rev-parse, the python stub aggregator):
        # delegate to the real subprocess.run.
        return real_run(args, **kwargs)

    return fake_run


@pytest.fixture
def out_path(tmp_path: Path) -> Path:
    return tmp_path / "phase17c_local_ollama_baseline.json"


def _run_harness(*, out_path: Path,
                  monkeypatch,
                  ollama_present: bool,
                  behavior: str = "all_succeed",
                  models: list[str] | None = None,
                  argv_extra: list[str] | None = None,
                  inject_md_word: str | None = None,
                  expected_rc: int | None = None) -> dict:
    """Run the harness, return parsed JSON. If expected_rc is None,
    accept any exit code; otherwise assert it."""

    harness = _import_harness()

    fake_binary = "/fake/path/ollama"
    real_subprocess_run = subprocess.run

    if ollama_present:
        monkeypatch.setattr(
            harness.shutil, "which",
            lambda name: fake_binary if name == "ollama" else None,
        )
    else:
        monkeypatch.setattr(
            harness.shutil, "which",
            lambda name: None,
        )

    monkeypatch.setattr(
        harness.subprocess, "run",
        make_fake_run(
            real_run=real_subprocess_run,
            fake_binary=fake_binary,
            behavior=behavior,
            models=models or [
                "gemma4:e4b", "gemma4:26b", "gemma3:4b",
                "qwen2.5:7b", "phi4-mini:latest", "llama3.2:3b",
            ],
        ),
    )

    if inject_md_word is not None:
        original_render = harness.render_md

        def patched_render(bench):
            return original_render(bench) + f"\n[INJECTED: {inject_md_word}]\n"

        monkeypatch.setattr(harness, "render_md", patched_render)

    argv = [
        "--output", str(out_path),
        "--phase17b-binary", str(STUB_BINARY),
    ]
    if argv_extra:
        argv += argv_extra

    rc = harness.main(argv)
    if expected_rc is not None:
        assert rc == expected_rc, f"exit code {rc} != expected {expected_rc}"
    return json.loads(out_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Schema (top-level keys)
# ---------------------------------------------------------------------------

REQUIRED_KEYS = {
    "benchmark_version", "git_sha", "python_version", "platform",
    "started_utc", "finished_utc", "duration_seconds",
    "selected_ollama_model", "ollama_baseline_status",
    "no_model_pull_or_download", "no_cloud_api_calls",
    "waggle_tracks", "ollama_track",
    "claim_labels", "not_claimed",
    "provider_jobs_delta", "builder_jobs_delta",
    "release_gate_pass", "forbidden_claims_absent",
    "release_gates",
}


def test_measured_path_has_required_keys(out_path, monkeypatch):
    bench = _run_harness(out_path=out_path, monkeypatch=monkeypatch,
                              ollama_present=True, behavior="all_succeed",
                              expected_rc=0)
    missing = REQUIRED_KEYS - set(bench.keys())
    assert not missing, f"missing top-level keys: {missing}"


def test_measured_path_status_and_gates(out_path, monkeypatch):
    bench = _run_harness(out_path=out_path, monkeypatch=monkeypatch,
                              ollama_present=True, behavior="all_succeed",
                              expected_rc=0)
    assert bench["benchmark_version"] == "phase17c.v1"
    assert bench["ollama_baseline_status"] == "MEASURED"
    assert bench["selected_ollama_model"] == "gemma4:e4b"
    assert bench["no_model_pull_or_download"] is True
    assert bench["no_cloud_api_calls"] is True
    assert bench["release_gate_pass"] is True
    assert bench["forbidden_claims_absent"] is True
    assert bench["provider_jobs_delta"] == 0
    assert bench["builder_jobs_delta"] == 0


def test_measured_path_ollama_track_metrics(out_path, monkeypatch):
    bench = _run_harness(out_path=out_path, monkeypatch=monkeypatch,
                              ollama_present=True, behavior="all_succeed",
                              expected_rc=0)
    track = bench["ollama_track"]
    assert track["model"] == "gemma4:e4b"
    assert track["prompt_count"] == 30
    assert track["prompts_run"] == 30
    assert track["prompts_succeeded"] == 30
    assert track["prompts_failed"] == 0
    assert track["median_latency_seconds"] is not None
    assert track["p95_latency_seconds"] is not None
    assert track["hash_chain_sha256"]
    assert len(track["per_prompt"]) == 30


def test_measured_path_claim_labels(out_path, monkeypatch):
    bench = _run_harness(out_path=out_path, monkeypatch=monkeypatch,
                              ollama_present=True, behavior="all_succeed",
                              expected_rc=0)
    cl = bench["claim_labels"]
    assert cl["ollama_local_baseline"] == "MEASURED-LOCAL-OLLAMA-ONE-MODEL"
    assert cl["competitive_evidence_axis_J"] == "MEASURED-LOCAL-OLLAMA-ONE-MODEL"
    assert cl["raw_intelligence_vs_frontier_moe"] == "NOT_CLAIMED"
    assert cl["no_cross_model_ranking"] is True
    assert cl["no_cloud_api_comparison"] is True


def test_not_claimed_disclaimer_flags(out_path, monkeypatch):
    bench = _run_harness(out_path=out_path, monkeypatch=monkeypatch,
                              ollama_present=True, behavior="all_succeed",
                              expected_rc=0)
    nc = set(bench["not_claimed"])
    expected = {
        "no_consciousness", "no_sentience", "no_human_like_mind",
        "no_beats_all_competitors", "no_world_best", "no_world_fastest",
    }
    assert expected.issubset(nc)


# ---------------------------------------------------------------------------
# NOT_AVAILABLE_NOT_RUN path (Ollama absent + --allow-no-ollama-track)
# ---------------------------------------------------------------------------

def test_no_ollama_with_allow_flag_passes_gate(out_path, monkeypatch):
    bench = _run_harness(out_path=out_path, monkeypatch=monkeypatch,
                              ollama_present=False,
                              argv_extra=["--allow-no-ollama-track"],
                              expected_rc=0)
    assert bench["ollama_baseline_status"] == "NOT_AVAILABLE_NOT_RUN"
    assert bench["selected_ollama_model"] is None
    assert bench["release_gate_pass"] is True
    assert bench["claim_labels"]["ollama_local_baseline"] == "NOT_AVAILABLE_NOT_RUN"


def test_no_ollama_without_allow_flag_fails_gate(out_path, monkeypatch):
    # Without --allow-no-ollama-track, NOT_AVAILABLE_NOT_RUN should fail
    # the release gate (we wanted MEASURED but got nothing).
    bench = _run_harness(out_path=out_path, monkeypatch=monkeypatch,
                              ollama_present=False)
    assert bench["ollama_baseline_status"] == "NOT_AVAILABLE_NOT_RUN"
    assert bench["release_gate_pass"] is False


def test_skip_ollama_flag_records_skip(out_path, monkeypatch):
    bench = _run_harness(out_path=out_path, monkeypatch=monkeypatch,
                              ollama_present=True,
                              argv_extra=["--skip-ollama",
                                            "--allow-no-ollama-track"],
                              expected_rc=0)
    assert bench["ollama_baseline_status"] == "NOT_AVAILABLE_NOT_RUN"
    assert bench["release_gate_pass"] is True


# ---------------------------------------------------------------------------
# FAILED path
# ---------------------------------------------------------------------------

def test_failed_path_blocks_gate(out_path, monkeypatch):
    bench = _run_harness(out_path=out_path, monkeypatch=monkeypatch,
                              ollama_present=True, behavior="fail_half")
    assert bench["ollama_baseline_status"] == "FAILED"
    assert bench["release_gate_pass"] is False
    track = bench["ollama_track"]
    assert track["prompts_failed"] > 0


# ---------------------------------------------------------------------------
# Forbidden substring path
# ---------------------------------------------------------------------------

def test_injected_forbidden_word_blocks_gate(out_path, monkeypatch):
    bench = _run_harness(out_path=out_path, monkeypatch=monkeypatch,
                              ollama_present=True, behavior="all_succeed",
                              inject_md_word="conscious ")
    assert bench["forbidden_claims_absent"] is False
    assert bench["release_gate_pass"] is False
    md_hits = bench["forbidden_substring_hits"]["md_hits"]
    assert "conscious" in md_hits


# ---------------------------------------------------------------------------
# Override model selection
# ---------------------------------------------------------------------------

def test_override_model_present(out_path, monkeypatch):
    bench = _run_harness(out_path=out_path, monkeypatch=monkeypatch,
                              ollama_present=True, behavior="all_succeed",
                              models=["llama3.2:3b", "gemma4:e4b"],
                              argv_extra=["--ollama-model", "llama3.2:3b"],
                              expected_rc=0)
    assert bench["selected_ollama_model"] == "llama3.2:3b"
    assert bench["ollama_baseline_status"] == "MEASURED"


def test_override_model_absent_fails(out_path, monkeypatch):
    bench = _run_harness(out_path=out_path, monkeypatch=monkeypatch,
                              ollama_present=True, behavior="all_succeed",
                              models=["gemma4:e4b"],
                              argv_extra=["--ollama-model", "not-installed:1b"])
    assert bench["selected_ollama_model"] is None
    assert bench["ollama_baseline_status"] == "FAILED"
    assert bench["release_gate_pass"] is False


# ---------------------------------------------------------------------------
# Pass-through of WaggleDance tracks
# ---------------------------------------------------------------------------

def test_waggle_tracks_pass_through(out_path, monkeypatch):
    bench = _run_harness(out_path=out_path, monkeypatch=monkeypatch,
                              ollama_present=True, behavior="all_succeed",
                              expected_rc=0)
    tracks = bench["waggle_tracks"]
    expected = {
        "A_solver_hot_path", "B_capability_lookup_10k",
        "C_handle_query_e2e", "D_restart_continuity",
        "E_producer_fabric",
    }
    assert expected.issubset(set(tracks.keys()))
    summary = bench["waggle_tracks_summary"]
    assert summary["all_waggledance_scenarios_pass"] is True
    assert summary["provider_jobs_delta_total"] == 0
    assert summary["builder_jobs_delta_total"] == 0


def test_md_sibling_written(out_path, monkeypatch):
    _run_harness(out_path=out_path, monkeypatch=monkeypatch,
                  ollama_present=True, behavior="all_succeed",
                  expected_rc=0)
    md = out_path.with_suffix(".md").read_text(encoding="utf-8")
    assert "Phase 17C" in md
    assert "No cloud API calls were made." in md
    assert "No model was pulled or downloaded." in md
    # Forbidden vocabulary substrings must NOT appear in the rendered MD.
    lower = md.lower()
    for word in ("conscious", "sentient", "agi", "beats all competitors"):
        assert word not in lower, f"forbidden substring '{word}' in MD"


def test_release_gates_subdict_complete(out_path, monkeypatch):
    bench = _run_harness(out_path=out_path, monkeypatch=monkeypatch,
                              ollama_present=True, behavior="all_succeed",
                              expected_rc=0)
    gates = bench["release_gates"]
    expected = {
        "tests_pass", "phase17b_aggregator_clean_exit",
        "ollama_track_status_in_allowed_set", "ollama_track_release_ok",
        "no_forbidden_substring_in_json_or_md",
        "no_provider_jobs_added", "no_builder_jobs_added",
        "no_allowlist_widened", "no_stable_release_in_phase17c",
    }
    missing = expected - set(gates.keys())
    assert not missing
    assert all(v is True for v in gates.values()), f"failing gates: {gates}"
