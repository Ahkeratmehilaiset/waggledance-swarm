# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the Mama Event long-run driver and analyser.

These tests exercise the synthetic generator, the PASS B runner,
and the report renderer against a tiny synthetic workload so the
test suite stays fast.

They are NOT a replacement for the real-data PASS A run — that
pass requires ``data/chat_history.db`` which is environment-
specific — but they pin the public API and the honesty invariants.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from waggledance.observatory.mama_events.reports import assert_no_hype

_ROOT = Path(__file__).resolve().parents[2]
_DRIVER = _ROOT / "tools" / "mama_event_longrun.py"
_ANALYSIS = _ROOT / "tools" / "mama_event_longrun_analysis.py"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def driver():
    return _load("mama_event_longrun", _DRIVER)


@pytest.fixture(scope="module")
def analysis():
    return _load("mama_event_longrun_analysis", _ANALYSIS)


# ── synthetic generator ───────────────────────────────────


def test_synthetic_generator_is_deterministic(driver):
    spec = driver.SyntheticSpec(num_events=50, simulated_hours=1.0)
    a = driver.generate_synthetic_events(spec)
    b = driver.generate_synthetic_events(spec)
    assert [e.event_id for e in a] == [e.event_id for e in b]
    assert [e.utterance_text for e in a] == [e.utterance_text for e in b]


def test_synthetic_generator_produces_multiple_sessions(driver):
    spec = driver.SyntheticSpec(num_events=200, session_switch_every=40)
    events = driver.generate_synthetic_events(spec)
    sessions = {e.session_id for e in events}
    assert len(sessions) >= 4  # 200 / 40 = 5, minus rounding


def test_synthetic_generator_uses_all_caregivers(driver):
    spec = driver.SyntheticSpec(num_events=300)
    events = driver.generate_synthetic_events(spec)
    caregivers = {e.caregiver_candidate_id for e in events}
    assert "voice-user-1" in caregivers
    assert "voice-user-2" in caregivers
    assert "voice-user-3" in caregivers


def test_synthetic_generator_contamination_toggles(driver):
    spec = driver.SyntheticSpec(
        num_events=100, contamination_every=10, tts_echo_every=15
    )
    events = driver.generate_synthetic_events(spec)
    direct_prompts = sum(1 for e in events if e.direct_prompt_present)
    tts_echoes = sum(1 for e in events if e.tts_echo_suspect)
    assert direct_prompts > 0
    assert tts_echoes > 0


# ── PASS B runner end-to-end ──────────────────────────────


def test_pass_b_produces_snapshots_and_ablations(driver, tmp_path):
    spec = driver.SyntheticSpec(num_events=120, simulated_hours=1.0)
    summary = driver.run_pass_b(out_dir=tmp_path, spec=spec)

    # every config must appear in the runs dict
    assert "baseline" in summary["runs"]
    for name in ("no_self_state", "no_caregiver", "no_consolidation", "no_voice", "no_multimodal"):
        assert name in summary["runs"]

    # snapshots taken only on baseline
    assert len(summary["baseline_snapshots"]) > 0

    # every ablation must have written an events ndjson
    for name in summary["runs"].keys():
        p = tmp_path / f"passB_{name}_events.ndjson"
        assert p.exists(), f"missing events file for {name}"
        assert p.stat().st_size > 0


def test_pass_b_serialisation_is_json_safe(driver, tmp_path):
    spec = driver.SyntheticSpec(num_events=40)
    summary = driver.run_pass_b(out_dir=tmp_path, spec=spec)
    assert json.loads(json.dumps(summary))  # roundtrip


def test_pass_b_summary_file_exists(driver, tmp_path):
    spec = driver.SyntheticSpec(num_events=40)
    driver.run_pass_b(out_dir=tmp_path, spec=spec)
    assert (tmp_path / "passB_summary.json").exists()


# ── analysis ──────────────────────────────────────────────


def test_render_longrun_report_is_hype_free(driver, analysis, tmp_path):
    # build a tiny synthetic pass B summary
    spec = driver.SyntheticSpec(num_events=60, simulated_hours=1.0)
    summary_b = driver.run_pass_b(out_dir=tmp_path, spec=spec)

    # fake a pass A summary
    summary_a = {
        "pass": "A_real_data",
        "chat_events": 62,
        "jsonl_events": 1500,
        "total_events": 1562,
        "max_jsonl_sample": 300,
        "runs": {
            "baseline": {
                "verdict": "NO_CANDIDATE_EVENTS",
                "max_score": 0,
                "sum_score": 0,
                "band_counts": {"artifact_or_parrot": 1024},
                "top_binding_strength": 0.0,
            },
        },
    }

    text = analysis.render_longrun_report(
        summary_a=summary_a,
        summary_b=summary_b,
        top_a=[],
        top_b=[],
        real_nonzero_count=0,
    )
    assert "PASS A" in text
    assert "PASS B" in text
    assert "NO_CANDIDATE_EVENTS" in text
    assert_no_hype(text)


def test_top_candidates_from_ndjson_ranks_by_score(analysis, tmp_path):
    p = tmp_path / "events.ndjson"
    # make 3 rows with scores 10, 50, 30
    lines = []
    for score, session in [(10, "a"), (50, "b"), (30, "c")]:
        lines.append(
            json.dumps({
                "event": {
                    "session_id": session,
                    "caregiver_candidate_id": "cg",
                    "redacted_utterance": f"text-{score}",
                },
                "score": {"total": score, "band": "weak_spontaneous"},
                "contamination": {"flags": []},
            })
        )
    p.write_text("\n".join(lines), encoding="utf-8")
    top = analysis.top_candidates_from_ndjson(p, top_n=10)
    assert [c.score for c in top] == [50, 30, 10]


def test_top_candidates_skips_zero_scores(analysis, tmp_path):
    p = tmp_path / "events.ndjson"
    lines = [
        json.dumps({
            "event": {"session_id": "a", "redacted_utterance": "x"},
            "score": {"total": 0, "band": "artifact_or_parrot"},
            "contamination": {"flags": []},
        }),
        json.dumps({
            "event": {"session_id": "b", "redacted_utterance": "y"},
            "score": {"total": 5, "band": "weak_spontaneous"},
            "contamination": {"flags": []},
        }),
    ]
    p.write_text("\n".join(lines), encoding="utf-8")
    top = analysis.top_candidates_from_ndjson(p, top_n=10)
    assert len(top) == 1
    assert top[0].score == 5
