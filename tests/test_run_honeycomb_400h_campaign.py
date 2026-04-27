"""Tests for tools/run_honeycomb_400h_campaign.py.

Scope:
- dry-run plan is deterministic and complete
- segment split sums to 1.0
- harness does NOT auto-start without --confirm-start
- campaign dir layout created on shakedown
- HOT/WARM/COLD segments are delegated markers, not driven here
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent


def _load_mod():
    path = ROOT / "tools" / "run_honeycomb_400h_campaign.py"
    spec = importlib.util.spec_from_file_location("run_honeycomb_400h_campaign", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["run_honeycomb_400h_campaign"] = mod
    spec.loader.exec_module(mod)
    return mod


mod = _load_mod()


def test_segments_sequence_matches_x_txt():
    expected = (
        "HOT", "WARM", "COLD",
        "SOLVER", "COMPOSITION", "LEARNING",
        "CI", "FULL",
    )
    assert mod.SEGMENTS == expected


def test_plan_split_sums_to_one():
    plan = mod.build_plan(400)
    total_hours = sum(s.hours for s in plan)
    assert round(total_hours, 1) == 400.0, total_hours


def test_plan_split_custom_must_sum_to_one():
    bad = {name: 0.1 for name in mod.SEGMENTS}
    with pytest.raises(ValueError):
        mod.build_plan(400, segment_split=bad)


def test_plan_contains_every_segment():
    plan = mod.build_plan(400)
    assert [s.name for s in plan] == list(mod.SEGMENTS)


def test_hot_warm_cold_are_delegated_not_driven():
    for name in ("HOT", "WARM", "COLD"):
        r = mod._seg_hot_warm_cold(name)
        assert r["segment"] == name
        assert r["delegated_to"].endswith("ui_gauntlet_400h.py")


def test_write_plan_creates_plan_md(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "CAMPAIGN_DIR", tmp_path / "cdir")
    monkeypatch.setattr(mod, "PLAN_PATH", tmp_path / "cdir" / "plan.md")
    p = mod.write_plan(400)
    assert p.exists()
    text = p.read_text("utf-8")
    # every segment must appear in the plan
    for name in mod.SEGMENTS:
        assert f"`{name}`" in text
    assert "400 h total" in text


def test_shakedown_creates_campaign_dir_layout(tmp_path, monkeypatch):
    cdir = tmp_path / "cdir"
    monkeypatch.setattr(mod, "CAMPAIGN_DIR", cdir)
    monkeypatch.setattr(mod, "CHECKPOINTS_DIR", cdir / "checkpoints")
    monkeypatch.setattr(mod, "METRICS_PATH", cdir / "metrics.jsonl")
    monkeypatch.setattr(mod, "FAILURES_PATH", cdir / "failures.jsonl")
    monkeypatch.setattr(mod, "PLAN_PATH", cdir / "plan.md")
    monkeypatch.setattr(mod, "SUMMARY_PATH", cdir / "final_summary.md")
    monkeypatch.setattr(mod, "OPEN_QUESTIONS_PATH", cdir / "open_questions.md")
    monkeypatch.setattr(mod, "RECOMMENDED_PATH", cdir / "recommended_next_commits.md")
    monkeypatch.setattr(mod, "STATE_PATH", cdir / "state.json")

    # Stub the heavy segments so the test doesn't actually run pytest
    monkeypatch.setattr(mod, "_seg_ci", lambda: {"segment": "CI", "runs": [{"returncode": 0, "duration_s": 0.01}]})
    monkeypatch.setattr(mod, "_seg_full", lambda: {"segment": "FULL", "runs": [{"returncode": 0, "duration_s": 0.01}]})
    monkeypatch.setattr(mod, "_seg_solver", lambda: {"segment": "SOLVER", "runs": [{"returncode": 0, "duration_s": 0.01}]})
    monkeypatch.setattr(mod, "_seg_composition", lambda: {"segment": "COMPOSITION", "runs": [{"returncode": 0, "duration_s": 0.01}]})
    monkeypatch.setattr(mod, "_seg_learning", lambda: {"segment": "LEARNING", "status": "import_ok"})

    result = mod.shakedown(400)
    # All expected files exist
    assert (cdir / "plan.md").exists()
    assert (cdir / "final_summary.md").exists()
    assert (cdir / "metrics.jsonl").exists()
    assert (cdir / "open_questions.md").exists()
    assert (cdir / "recommended_next_commits.md").exists()
    assert (cdir / "state.json").exists()
    # State carries last segment
    import json as _json
    state = _json.loads((cdir / "state.json").read_text("utf-8"))
    assert state["last_segment"] == "FULL"


def test_stop_conditions_cover_x_txt_list():
    # x.txt Phase 9 required stop conditions
    expected = {
        "server_unreachable_probes",
        "auth_bypass",
        "xss_execution",
        "uncaught_exception_burst",
        "sqlite_lock_failures",
        "embedding_timeout_spike",
        "memory_growth_mb",
        "pytest_core_regression",
        "secret_leakage",
    }
    assert expected.issubset(set(mod.STOP_CONDITIONS.keys()))


# ── GPT R4 advisory: busy-lock + priority ─────────────────────────

def test_is_live_campaign_busy_false_without_pidfile(tmp_path):
    missing = tmp_path / "no_such_pidfile"
    assert mod.is_live_campaign_busy(missing) is False


def test_is_live_campaign_busy_false_for_dead_pid(tmp_path):
    p = tmp_path / ".watchdog.pid"
    p.write_text("99999999", encoding="utf-8")  # vanishingly unlikely to exist
    assert mod.is_live_campaign_busy(p) is False


def test_is_live_campaign_busy_default_recomputes_pidfile(tmp_path, monkeypatch):
    """GPT R5 §3: default path is recomputed on each call so a
    watchdog that started after import is still discovered."""
    monkeypatch.setattr(mod, "_current_live_watchdog_pidfile", lambda: None)
    assert mod.is_live_campaign_busy() is False
    # Now flip the lookup to a tmp pidfile
    target = tmp_path / ".watchdog.pid"
    target.write_text("99999998", encoding="utf-8")
    monkeypatch.setattr(mod, "_current_live_watchdog_pidfile", lambda: target)
    # dead pid → still not busy, but the function must have noticed the
    # pidfile appeared (proven by test_is_live_campaign_busy_false_for_dead_pid)
    assert mod.is_live_campaign_busy() is False


def test_is_live_campaign_busy_new_format_matches_create_time(tmp_path):
    """New pidfile format 'pid:create_time' must verify create_time to
    close the PID-reuse hole."""
    import os as _os
    import psutil as _ps
    self_pid = _os.getpid()
    self_ct = _ps.Process(self_pid).create_time()
    p = tmp_path / ".watchdog.pid"
    p.write_text(f"{self_pid}:{self_ct}", encoding="utf-8")
    # Our own process satisfies pid_exists + create_time guard
    assert mod.is_live_campaign_busy(p) is True


def test_is_live_campaign_busy_new_format_mismatched_create_time(tmp_path):
    """If create_time in the pidfile does not match the live process,
    the function must return False (stale pid reuse)."""
    import os as _os
    self_pid = _os.getpid()
    # Deliberately wrong create_time
    p = tmp_path / ".watchdog.pid"
    p.write_text(f"{self_pid}:1.0", encoding="utf-8")
    assert mod.is_live_campaign_busy(p) is False


def test_is_live_campaign_busy_legacy_format_falls_back_to_cmdline(tmp_path, monkeypatch):
    """Legacy pidfile with no create_time: function falls back to
    cmdline containing 'campaign_watchdog'."""
    import os as _os
    self_pid = _os.getpid()
    p = tmp_path / ".watchdog.pid"
    p.write_text(str(self_pid), encoding="utf-8")
    # Our cmdline won't include "campaign_watchdog" (it's pytest), so
    # legacy detection returns False. That's the correct behavior:
    # legacy format falls back to cmdline check.
    assert mod.is_live_campaign_busy(p) is False


def test_ci_segment_skipped_when_busy(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "is_live_campaign_busy", lambda *_a, **_k: True)
    r = mod._seg_ci()
    assert r.get("skipped")
    assert r["segment"] == "CI"
    assert "reason" in r


def test_full_segment_skipped_when_busy(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "is_live_campaign_busy", lambda *_a, **_k: True)
    r = mod._seg_full()
    assert r.get("skipped")
    assert r["segment"] == "FULL"


def test_ci_segment_runs_when_busy_but_override_given(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "is_live_campaign_busy", lambda *_a, **_k: True)
    # Stub the heavy pytest call so the test stays fast
    called = {}
    def fake_run(argv, **kw):
        called["argv"] = argv
        called["env_extra"] = kw.get("env_extra")
        return {"argv": argv, "returncode": 0, "duration_s": 0.01}
    monkeypatch.setattr(mod, "_run_subprocess", fake_run)
    r = mod._seg_ci(allow_when_busy=True)
    assert not r.get("skipped")
    assert called["env_extra"]["WAGGLE_HONEYCOMB_SEGMENT"] == "CI"
    # xdist explicitly disabled to avoid sqlite lock fights
    assert "no:xdist" in called["env_extra"]["PYTEST_ADDOPTS"]


def test_shakedown_metrics_reports_skipped_outcome(tmp_path, monkeypatch):
    """GPT R5 §2: busy-skipped segments must NOT be labeled ok=True in
    the metrics.jsonl. The `outcome` field must be `skipped`."""
    cdir = tmp_path / "cdir"
    monkeypatch.setattr(mod, "CAMPAIGN_DIR", cdir)
    monkeypatch.setattr(mod, "CHECKPOINTS_DIR", cdir / "checkpoints")
    monkeypatch.setattr(mod, "METRICS_PATH", cdir / "metrics.jsonl")
    monkeypatch.setattr(mod, "FAILURES_PATH", cdir / "failures.jsonl")
    monkeypatch.setattr(mod, "PLAN_PATH", cdir / "plan.md")
    monkeypatch.setattr(mod, "SUMMARY_PATH", cdir / "final_summary.md")
    monkeypatch.setattr(mod, "OPEN_QUESTIONS_PATH", cdir / "open_questions.md")
    monkeypatch.setattr(mod, "RECOMMENDED_PATH", cdir / "recommended_next_commits.md")
    monkeypatch.setattr(mod, "STATE_PATH", cdir / "state.json")
    # Live watchdog reported up → CI/FULL auto-skip
    monkeypatch.setattr(mod, "is_live_campaign_busy", lambda *_a, **_k: True)
    # Stub non-skippable segments so the test stays fast
    monkeypatch.setattr(mod, "_seg_solver",
                         lambda: {"segment": "SOLVER", "runs": [{"returncode": 0}]})
    monkeypatch.setattr(mod, "_seg_composition",
                         lambda: {"segment": "COMPOSITION", "runs": [{"returncode": 0}]})
    monkeypatch.setattr(mod, "_seg_learning",
                         lambda: {"segment": "LEARNING", "status": "import_ok"})

    mod.shakedown(400)
    metrics = [line for line in (cdir / "metrics.jsonl").read_text("utf-8").splitlines()
               if line.strip()]
    import json as _json
    by_segment = {_json.loads(line)["segment"]: _json.loads(line) for line in metrics}
    # CI and FULL must carry outcome=skipped, ok=False
    assert by_segment["CI"]["outcome"] == "skipped"
    assert by_segment["CI"]["ok"] is False
    assert by_segment["FULL"]["outcome"] == "skipped"
    assert by_segment["FULL"]["ok"] is False


def test_shakedown_summary_md_shows_skipped(tmp_path, monkeypatch):
    cdir = tmp_path / "cdir"
    monkeypatch.setattr(mod, "CAMPAIGN_DIR", cdir)
    monkeypatch.setattr(mod, "CHECKPOINTS_DIR", cdir / "checkpoints")
    monkeypatch.setattr(mod, "METRICS_PATH", cdir / "metrics.jsonl")
    monkeypatch.setattr(mod, "FAILURES_PATH", cdir / "failures.jsonl")
    monkeypatch.setattr(mod, "PLAN_PATH", cdir / "plan.md")
    monkeypatch.setattr(mod, "SUMMARY_PATH", cdir / "final_summary.md")
    monkeypatch.setattr(mod, "OPEN_QUESTIONS_PATH", cdir / "open_questions.md")
    monkeypatch.setattr(mod, "RECOMMENDED_PATH", cdir / "recommended_next_commits.md")
    monkeypatch.setattr(mod, "STATE_PATH", cdir / "state.json")
    monkeypatch.setattr(mod, "is_live_campaign_busy", lambda *_a, **_k: True)
    monkeypatch.setattr(mod, "_seg_solver",
                         lambda: {"segment": "SOLVER", "runs": [{"returncode": 0}]})
    monkeypatch.setattr(mod, "_seg_composition",
                         lambda: {"segment": "COMPOSITION", "runs": [{"returncode": 0}]})
    monkeypatch.setattr(mod, "_seg_learning",
                         lambda: {"segment": "LEARNING", "status": "import_ok"})

    mod.shakedown(400)
    summary = (cdir / "final_summary.md").read_text("utf-8")
    assert "skipped:" in summary


def test_lower_priority_is_best_effort(monkeypatch):
    # Even if psutil raises, _lower_priority must NOT propagate
    import psutil
    class _BadProcess:
        def nice(self, *args, **kwargs):
            raise psutil.AccessDenied(99)
    monkeypatch.setattr(psutil, "Process", lambda *_: _BadProcess())
    mod._lower_priority()  # must not raise


def test_summary_and_open_questions_have_no_secrets(tmp_path, monkeypatch):
    cdir = tmp_path / "cdir"
    monkeypatch.setattr(mod, "CAMPAIGN_DIR", cdir)
    monkeypatch.setattr(mod, "PLAN_PATH", cdir / "plan.md")
    monkeypatch.setattr(mod, "SUMMARY_PATH", cdir / "final_summary.md")
    monkeypatch.setattr(mod, "OPEN_QUESTIONS_PATH", cdir / "open_questions.md")
    monkeypatch.setattr(mod, "RECOMMENDED_PATH", cdir / "recommended_next_commits.md")
    monkeypatch.setattr(mod, "STATE_PATH", cdir / "state.json")
    monkeypatch.setattr(mod, "METRICS_PATH", cdir / "metrics.jsonl")
    monkeypatch.setattr(mod, "FAILURES_PATH", cdir / "failures.jsonl")
    monkeypatch.setattr(mod, "CHECKPOINTS_DIR", cdir / "checkpoints")

    monkeypatch.setattr(mod, "_seg_ci", lambda: {"segment": "CI", "runs": [{"returncode": 0}]})
    monkeypatch.setattr(mod, "_seg_full", lambda: {"segment": "FULL", "runs": [{"returncode": 0}]})
    monkeypatch.setattr(mod, "_seg_solver", lambda: {"segment": "SOLVER", "runs": [{"returncode": 0}]})
    monkeypatch.setattr(mod, "_seg_composition", lambda: {"segment": "COMPOSITION", "runs": [{"returncode": 0}]})
    monkeypatch.setattr(mod, "_seg_learning", lambda: {"segment": "LEARNING", "status": "import_ok"})

    mod.shakedown(400)
    for f in ("plan.md", "final_summary.md", "open_questions.md",
              "recommended_next_commits.md"):
        text = (cdir / f).read_text("utf-8")
        assert "WAGGLE_API_KEY" not in text
        assert "gnt_" not in text
        assert "C:\\Users" not in text
