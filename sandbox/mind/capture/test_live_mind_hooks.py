"""Offline cases for live lens hooks: fake trees and a fake process runner, no producer, no capture."""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import live_mind  # noqa: E402


def run_hooks(tmp_path, monkeypatch, runner, dirty=()):
    hooks_file = tmp_path / "hooks.json"
    hooks_file.write_text(json.dumps([{"name": name, "tree": str(tmp_path / name), "argv": ["x", "{cycle_dir}"]}
                                      for name in ("slow", "fast", "dirty")]), encoding="utf-8")

    def git(tree, *options):
        if options[0] == "rev-parse":
            return "head-" + Path(tree).name
        return " M file" if Path(tree).name in dirty else ""

    monkeypatch.setattr(live_mind.driver, "git", git)
    monkeypatch.setattr(live_mind.subprocess, "run", runner)
    return live_mind.hooks(SimpleNamespace(lens_hook=hooks_file), tmp_path / "cycle", tmp_path / "audit")


def test_a_hook_timeout_is_that_hooks_gap_and_the_other_hooks_still_run(tmp_path, monkeypatch):
    def runner(argv, **kwargs):
        if kwargs.get("timeout") and argv[0] == "x" and runner.calls == 0:
            runner.calls += 1
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        runner.calls += 1
        return SimpleNamespace(returncode=0)
    runner.calls = 0
    results = run_hooks(tmp_path, monkeypatch, runner, dirty=("dirty",))
    assert results["slow"]["timeout_s"] == 120 and "exit" not in results["slow"]
    assert results["slow"]["head"] == "head-slow" and isinstance(results["slow"]["ms"], int)
    assert results["fast"] == {"exit": 0, "head": "head-fast", "ms": results["fast"]["ms"]}
    assert results["dirty"] == {"skipped": "tree_not_clean", "head": "head-dirty"}
    assert runner.calls == 2


def test_hook_placeholders_are_filled_and_every_run_has_a_timeout(tmp_path, monkeypatch):
    seen = []

    def runner(argv, **kwargs):
        seen.append((argv, kwargs.get("timeout")))
        return SimpleNamespace(returncode=3)
    results = run_hooks(tmp_path, monkeypatch, runner)
    assert [entry[1] for entry in seen] == [120, 120, 120]
    assert all(argv == ["x", str(tmp_path / "cycle")] for argv, _ in seen)
    assert {name: row["exit"] for name, row in results.items()} == {"slow": 3, "fast": 3, "dirty": 3}
