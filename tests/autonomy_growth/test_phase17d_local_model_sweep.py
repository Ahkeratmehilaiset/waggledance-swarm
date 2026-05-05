# SPDX-License-Identifier: BUSL-1.1
"""Phase 17D - Local Ollama Multi-Model Sweep harness tests.

Covers:

    * MEASURED-PANEL (4 models, all succeed, --repeat-count 2 for speed)
    * NOT_AVAILABLE_NOT_RUN (no ollama on PATH; --allow-no-ollama-track)
    * TOO_FEW_MODELS (only 1 model in `ollama list`)
    * PULL_DETECTED (subprocess stderr contains "pulling manifest")
    * FAILED-FOR-MODEL (one model fails half its prompts)
    * Forbidden substring (rendered MD contains a ranking-guard word)
    * Override --models with one absent
    * --repeat-count semantics

Subprocess `ollama` is replaced by a fake via monkeypatch so the test
suite runs in well under a second per case.
"""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))


def _import_harness():
    name = "run_phase17d_local_model_sweep"
    if name in sys.modules:
        del sys.modules[name]
    return importlib.import_module(name)


# ---------------------------------------------------------------------------
# Fake ollama factory
# ---------------------------------------------------------------------------

DEFAULT_FAKE_LIST = [
    ("gemma4:e4b", "c6eb396dbd59", "9.6", "GB"),
    ("gemma3:4b", "a2af6cc3eb7f", "3.3", "GB"),
    ("llama3.2:3b", "a80c4f17acd5", "2.0", "GB"),
    ("phi4-mini:latest", "78fad5d182a7", "2.5", "GB"),
    ("qwen2.5:7b", "845dbda0ea48", "4.7", "GB"),
]


def make_fake_run(*, real_run, fake_binary: str,
                    behavior: str = "all_succeed",
                    list_entries=None,
                    pull_signature_in_stderr: str | None = None,
                    fail_for_model: str | None = None,
                    version: str = "ollama version is 0.99.0"):
    """Return a fake subprocess.run that handles fake-ollama invocations."""
    if list_entries is None:
        list_entries = DEFAULT_FAKE_LIST
    state = {"prompt_count_per_model": {}}
    fail_indices_for_target = set(range(0, 30, 2))  # alternating

    def fake_run(args, **kwargs):
        if isinstance(args, list) and args:
            cmd = str(args[0])
            if cmd == fake_binary or cmd.endswith("ollama") or cmd.endswith("ollama.exe"):
                if len(args) >= 2 and args[1] == "--version":
                    return subprocess.CompletedProcess(
                        args=args, returncode=0,
                        stdout=version.encode("utf-8"), stderr=b"",
                    )
                if len(args) >= 2 and args[1] == "list":
                    header = "NAME ID SIZE UNIT MODIFIED\n"
                    body = "\n".join(
                        f"{n} {i} {s} {u} now" for (n, i, s, u) in list_entries
                    )
                    return subprocess.CompletedProcess(
                        args=args, returncode=0,
                        stdout=(header + body + "\n").encode("utf-8"),
                        stderr=b"",
                    )
                if len(args) >= 4 and args[1] == "run":
                    model = args[2]
                    idx = state["prompt_count_per_model"].get(model, 0)
                    state["prompt_count_per_model"][model] = idx + 1
                    if pull_signature_in_stderr:
                        # Inject pull-detection signature into stderr of the
                        # FIRST prompt of the FIRST model only.
                        if idx == 0:
                            return subprocess.CompletedProcess(
                                args=args, returncode=0,
                                stdout=f"OK-{idx}\n".encode("utf-8"),
                                stderr=pull_signature_in_stderr.encode("utf-8"),
                            )
                    if fail_for_model and model == fail_for_model:
                        if idx in fail_indices_for_target:
                            return subprocess.CompletedProcess(
                                args=args, returncode=1,
                                stdout=b"", stderr=b"failure\n",
                            )
                    if behavior == "all_fail":
                        return subprocess.CompletedProcess(
                            args=args, returncode=1,
                            stdout=b"", stderr=b"all fail\n",
                        )
                    return subprocess.CompletedProcess(
                        args=args, returncode=0,
                        stdout=f"OK-{model}-{idx}\n".encode("utf-8"),
                        stderr=b"",
                    )
        return real_run(args, **kwargs)

    return fake_run


@pytest.fixture
def out_dir(tmp_path: Path) -> Path:
    return tmp_path / "phase17d_artifacts"


def _run_harness(*, out_dir: Path, monkeypatch,
                  ollama_present: bool = True,
                  behavior: str = "all_succeed",
                  list_entries=None,
                  pull_signature_in_stderr: str | None = None,
                  fail_for_model: str | None = None,
                  inject_md_word: str | None = None,
                  argv_extra: list[str] | None = None,
                  expected_rc: int | None = None) -> dict:
    harness = _import_harness()
    fake_binary = "/fake/path/ollama"
    real_subprocess_run = subprocess.run

    if ollama_present:
        monkeypatch.setattr(
            harness.shutil, "which",
            lambda name: fake_binary if name == "ollama" else None,
        )
    else:
        monkeypatch.setattr(harness.shutil, "which", lambda name: None)

    monkeypatch.setattr(
        harness.subprocess, "run",
        make_fake_run(
            real_run=real_subprocess_run,
            fake_binary=fake_binary,
            behavior=behavior,
            list_entries=list_entries,
            pull_signature_in_stderr=pull_signature_in_stderr,
            fail_for_model=fail_for_model,
        ),
    )
    # Also patch the Phase 17C module's subprocess.run since 17C's
    # run_one_prompt is what 17D calls into.
    p17c = sys.modules.get("run_phase17c_local_ollama_baseline")
    if p17c is not None:
        monkeypatch.setattr(
            p17c.subprocess, "run",
            make_fake_run(
                real_run=real_subprocess_run,
                fake_binary=fake_binary,
                behavior=behavior,
                list_entries=list_entries,
                pull_signature_in_stderr=pull_signature_in_stderr,
                fail_for_model=fail_for_model,
            ),
        )

    if inject_md_word is not None:
        original_render = harness.render_md

        def patched_render(bench):
            return original_render(bench) + f"\n[INJECTED: {inject_md_word}]\n"

        monkeypatch.setattr(harness, "render_md", patched_render)

    argv = ["--out-dir", str(out_dir)]
    if argv_extra:
        argv += argv_extra

    rc = harness.main(argv)
    if expected_rc is not None:
        assert rc == expected_rc, f"exit {rc} != expected {expected_rc}"
    return json.loads(
        (out_dir / "phase17d_local_model_sweep.json").read_text(encoding="utf-8")
    )


# ---------------------------------------------------------------------------
# MEASURED-PANEL path
# ---------------------------------------------------------------------------

def test_measured_panel_4_models_2_repeats(out_dir, monkeypatch):
    bench = _run_harness(
        out_dir=out_dir, monkeypatch=monkeypatch,
        ollama_present=True, behavior="all_succeed",
        argv_extra=["--repeat-count", "2", "--prompt-count", "5",
                      "--max-models", "4"],
        expected_rc=0,
    )
    assert bench["benchmark_version"] == "phase17d.v1"
    assert len(bench["selected_models"]) == 4
    assert bench["selected_models"] == [
        "gemma4:e4b", "gemma3:4b", "llama3.2:3b", "phi4-mini:latest",
    ]
    assert bench["release_gate_pass"] is True
    assert bench["forbidden_claims_absent"] is True
    assert bench["no_model_pull_or_download"] is True
    assert bench["no_cloud_api_calls"] is True
    assert bench["claim_labels"]["ollama_local_baseline"] == "MEASURED-LOCAL-OLLAMA-PANEL"
    assert bench["claim_labels"]["competitive_evidence_axis_J"] == "MEASURED-LOCAL-OLLAMA-PANEL"
    assert bench["claim_labels"]["raw_intelligence_vs_frontier_moe"] == "NOT_CLAIMED"
    for name in bench["selected_models"]:
        m = bench["model_results"][name]
        assert m["claim_label"] == "MEASURED-FOR-THIS-MODEL-AND-PROMPT-SET"
        assert m["repeat_count"] == 2
        assert m["prompts_succeeded"] == 10  # 2 repeats x 5 prompts
        assert m["prompts_failed"] == 0


def test_measured_panel_disclaimers_present(out_dir, monkeypatch):
    bench = _run_harness(
        out_dir=out_dir, monkeypatch=monkeypatch,
        argv_extra=["--repeat-count", "1", "--prompt-count", "3",
                      "--max-models", "2"],
        expected_rc=0,
    )
    nc = set(bench["not_claimed"])
    assert {"no_consciousness", "no_sentience", "no_raw_intelligence_superiority",
            "no_cross_vendor_ranking"}.issubset(nc)


def test_md_sibling_no_forbidden_or_ranking(out_dir, monkeypatch):
    _run_harness(
        out_dir=out_dir, monkeypatch=monkeypatch,
        argv_extra=["--repeat-count", "1", "--prompt-count", "3",
                      "--max-models", "2"],
        expected_rc=0,
    )
    md = (out_dir / "phase17d_local_model_sweep.md").read_text(encoding="utf-8")
    assert "Phase 17D" in md
    assert "No cloud API calls were made." in md
    assert "No model was pulled or downloaded." in md
    assert "No cross-vendor ranking is implied." in md
    lower = md.lower()
    for word in ("conscious", "sentient", "agi", "beats all competitors",
                  "is faster than", "outperforms"):
        assert word not in lower, f"forbidden substring '{word}' in MD"


# ---------------------------------------------------------------------------
# NOT_AVAILABLE_NOT_RUN path
# ---------------------------------------------------------------------------

def test_no_ollama_with_allow_flag(out_dir, monkeypatch):
    bench = _run_harness(
        out_dir=out_dir, monkeypatch=monkeypatch,
        ollama_present=False,
        argv_extra=["--allow-no-ollama-track"],
        expected_rc=0,
    )
    assert bench["selected_models"] == []
    assert bench["release_gate_pass"] is True


def test_no_ollama_without_allow_flag_fails(out_dir, monkeypatch):
    bench = _run_harness(
        out_dir=out_dir, monkeypatch=monkeypatch,
        ollama_present=False,
    )
    assert bench["selected_models"] == []
    assert bench["release_gate_pass"] is False


# ---------------------------------------------------------------------------
# TOO_FEW_MODELS path
# ---------------------------------------------------------------------------

def test_only_one_local_model_fails(out_dir, monkeypatch):
    bench = _run_harness(
        out_dir=out_dir, monkeypatch=monkeypatch,
        list_entries=[("gemma4:e4b", "c6eb396dbd59", "9.6", "GB")],
        argv_extra=["--repeat-count", "1", "--prompt-count", "3"],
    )
    # Only 1 measured -> at_least_two_models_measured fails.
    assert bench["release_gate_pass"] is False
    assert bench["release_gates"]["at_least_two_models_measured"] is False


# ---------------------------------------------------------------------------
# PULL_DETECTED path
# ---------------------------------------------------------------------------

def test_pull_detected_aborts(out_dir, monkeypatch):
    bench = _run_harness(
        out_dir=out_dir, monkeypatch=monkeypatch,
        pull_signature_in_stderr="pulling manifest from registry\n",
        argv_extra=["--repeat-count", "1", "--prompt-count", "3",
                      "--max-models", "2"],
    )
    assert bench["release_gate_pass"] is False
    assert bench["release_gates"]["no_pull_download_detected"] is False
    assert bench["no_model_pull_or_download"] is False


# ---------------------------------------------------------------------------
# FAILED-FOR-MODEL path
# ---------------------------------------------------------------------------

def test_one_model_fails_panel_downgrades_per_model_only(out_dir, monkeypatch):
    bench = _run_harness(
        out_dir=out_dir, monkeypatch=monkeypatch,
        fail_for_model="gemma3:4b",
        argv_extra=["--repeat-count", "1", "--prompt-count", "5",
                      "--max-models", "3"],
    )
    # gemma3:4b should be FAILED; others MEASURED.
    assert bench["model_results"]["gemma3:4b"]["claim_label"] == "FAILED-FOR-THIS-MODEL"
    assert bench["model_results"]["gemma4:e4b"]["claim_label"] == "MEASURED-FOR-THIS-MODEL-AND-PROMPT-SET"
    # Panel still has 2 measured -> still PANEL claim allowed.
    assert bench["claim_labels"]["ollama_local_baseline"] == "MEASURED-LOCAL-OLLAMA-PANEL"


# ---------------------------------------------------------------------------
# Forbidden substring path
# ---------------------------------------------------------------------------

def test_injected_ranking_word_blocks_gate(out_dir, monkeypatch):
    bench = _run_harness(
        out_dir=out_dir, monkeypatch=monkeypatch,
        inject_md_word="is faster than",
        argv_extra=["--repeat-count", "1", "--prompt-count", "3",
                      "--max-models", "2"],
    )
    assert bench["forbidden_claims_absent"] is False
    assert bench["release_gate_pass"] is False
    md_hits = bench["forbidden_substring_hits"]["md_hits"]
    assert any("faster" in h for h in md_hits)


# ---------------------------------------------------------------------------
# Override --models with absent name
# ---------------------------------------------------------------------------

def test_override_with_absent_model_fails(out_dir, monkeypatch):
    bench = _run_harness(
        out_dir=out_dir, monkeypatch=monkeypatch,
        argv_extra=["--models", "gemma4:e4b,not-installed:1b",
                      "--repeat-count", "1", "--prompt-count", "3"],
    )
    assert bench["selected_models"] == []
    assert bench["release_gate_pass"] is False


# ---------------------------------------------------------------------------
# --repeat-count semantics
# ---------------------------------------------------------------------------

def test_repeat_count_two(out_dir, monkeypatch):
    bench = _run_harness(
        out_dir=out_dir, monkeypatch=monkeypatch,
        argv_extra=["--repeat-count", "2", "--prompt-count", "3",
                      "--max-models", "2"],
        expected_rc=0,
    )
    for name in bench["selected_models"]:
        m = bench["model_results"][name]
        assert m["repeat_count"] == 2
        assert len(m["per_repeat"]) == 2
        assert m["prompts_succeeded"] == 6  # 2 x 3


def test_repeat_count_three_default(out_dir, monkeypatch):
    bench = _run_harness(
        out_dir=out_dir, monkeypatch=monkeypatch,
        argv_extra=["--prompt-count", "3", "--max-models", "2"],
        expected_rc=0,
    )
    for name in bench["selected_models"]:
        m = bench["model_results"][name]
        assert m["repeat_count"] == 3
        assert len(m["per_repeat"]) == 3


# ---------------------------------------------------------------------------
# Release gates subdict completeness
# ---------------------------------------------------------------------------

def test_release_gates_subdict(out_dir, monkeypatch):
    bench = _run_harness(
        out_dir=out_dir, monkeypatch=monkeypatch,
        argv_extra=["--repeat-count", "1", "--prompt-count", "3",
                      "--max-models", "2"],
        expected_rc=0,
    )
    expected = {
        "at_least_two_models_measured",
        "no_pull_download_detected",
        "no_cloud_api_call_detected",
        "all_selected_models_completed",
        "no_forbidden_substring_in_json_or_md",
        "no_provider_jobs_added",
        "no_builder_jobs_added",
        "no_allowlist_widened",
        "no_stable_release_in_phase17d",
        "no_cross_vendor_ranking",
        "no_raw_intelligence_superiority_claim",
    }
    missing = expected - set(bench["release_gates"].keys())
    assert not missing
    assert all(v is True for v in bench["release_gates"].values()), \
        f"failing gates: {bench['release_gates']}"
