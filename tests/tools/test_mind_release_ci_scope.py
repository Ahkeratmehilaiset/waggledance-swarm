"""The bounded Mind release is executed AND collected, never all of sandbox.

Only parse workflow text here: no Mind import, dataset discovery or CLI launch.
The final composition supplies the ten explicitly admitted synthetic test files.
"""
from copy import deepcopy
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
MIND_TESTS = (
    "sandbox/mind/test_mind_capture_producer_reader.py",
    "sandbox/mind/test_mind_capture_producer_state.py",
    "sandbox/mind/test_join_turn.py",
    "sandbox/mind/test_join_card.py",
    "sandbox/mind/test_join_card_delivery.py",
    "sandbox/mind/step1/test_adapter.py",
    "sandbox/mind/step1/test_lane_view.py",
    "sandbox/mind/capture/test_capacity_lens.py",
    "sandbox/mind/capture/test_overlap_lens.py",
    "sandbox/mind/test_mind_chain_driver.py",
)


def _workflow(name):
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))


def _step(workflow, job, name):
    matches = [s for s in workflow["jobs"][job]["steps"] if s.get("name") == name]
    assert len(matches) == 1
    return matches[0]


def _assert_runs(workflow, job, name, full_command, mind_prefix, mind_options):
    step = _step(workflow, job, name)
    assert set(step) == {"name", "run"}, "a conditional/optional run is not scope coverage"
    commands = step["run"].strip().splitlines()
    assert len(commands) == 2, "the full-suite run and finite Mind run must both exist"
    assert commands[0].strip() == full_command, "keep the full-suite command unchanged"
    assert shlex.split(commands[1]) == [*mind_prefix, *MIND_TESTS, *mind_options]


def _assert_collection(workflow):
    text = _step(workflow, "test", "Check test count")["run"]
    match = re.search(r'if ! OUT=\$\((pytest [^\n]+) 2>/dev/null\); then', text)
    assert match, "failed collection must still refuse"
    assert shlex.split(match.group(1)) == ["pytest", "tests/", *MIND_TESTS, "--collect-only", "-q"]
    assert '"$COUNT" -ge 4200' in text, "never lower the existing full-suite floor"
    assert '"$SUMMARIES" != "1"' in text, "retain the exact-one-summary guard"


def test_matrix_executes_the_existing_full_suite_then_exact_finite_mind_scope():
    _assert_runs(_workflow("ci.yml"), "test", "Run tests",
                 "pytest tests/ -x -q --tb=short -p no:warnings",
                 ["pytest"], ["-x", "-q", "--tb=short", "-p", "no:warnings"])


def test_unified_executes_the_existing_full_suite_then_exact_finite_mind_scope():
    _assert_runs(_workflow("tests.yml"), "unified", "Run pytest",
                 "python -m pytest tests/ -v --tb=short",
                 ["python", "-m", "pytest"], ["-v", "--tb=short"])


def test_collection_covers_the_same_finite_scope_and_preserves_count_guards():
    _assert_collection(_workflow("ci.yml"))


@pytest.mark.parametrize("filename,job,name,full,prefix,options", [
    ("ci.yml", "test", "Run tests", "pytest tests/ -x -q --tb=short -p no:warnings",
     ["pytest"], ["-x", "-q", "--tb=short", "-p", "no:warnings"]),
    ("tests.yml", "unified", "Run pytest", "python -m pytest tests/ -v --tb=short",
     ["python", "-m", "pytest"], ["-v", "--tb=short"]),
])
def test_mutant_drop_mind_run_is_killed(filename, job, name, full, prefix, options):
    mutant = deepcopy(_workflow(filename))
    _step(mutant, job, name)["run"] = full  # MUTANT_DROP_MIND_RUN
    with pytest.raises(AssertionError, match="both exist"):
        _assert_runs(mutant, job, name, full, prefix, options)


def test_mutant_drop_mind_collection_is_killed():
    mutant = deepcopy(_workflow("ci.yml"))
    step = _step(mutant, "test", "Check test count")
    step["run"] = step["run"].replace("tests/ " + " ".join(MIND_TESTS), "tests/")
    # MUTANT_DROP_MIND_COLLECTION: all existing tests remain, only Mind disappears.
    with pytest.raises(AssertionError):
        _assert_collection(mutant)


def test_mutant_whole_sandbox_collection_is_killed():
    mutant = deepcopy(_workflow("ci.yml"))
    step = _step(mutant, "test", "Check test count")
    step["run"] = step["run"].replace(" ".join(MIND_TESTS), "sandbox/")
    # MUTANT_RECURSIVE_SANDBOX: deferred/private modules are not admitted.
    with pytest.raises(AssertionError):
        _assert_collection(mutant)


def test_scope_contains_only_ten_unique_explicit_python_test_files():
    assert len(MIND_TESTS) == len(set(MIND_TESTS)) == 10
    assert all(p.startswith("sandbox/mind/") and Path(p).name.startswith("test_")
               and p.endswith(".py") and ".." not in Path(p).parts for p in MIND_TESTS)
    assert not any("remember" in p or "learn" in p or "run_" in Path(p).name for p in MIND_TESTS)


def _bash():
    if sys.platform == "win32":
        done = subprocess.run(["git", "--exec-path"], capture_output=True, text=True,
                              check=True, timeout=10)
        path = Path(done.stdout.strip()).parents[2] / "bin/bash.exe"
        assert path.is_file(), "Git Bash is required; do not fall back to WSL"
        return str(path)
    path = shutil.which("bash")
    assert path, "bash is required to exercise the actual workflow run block"
    return path


def _run_block(tmp_path, text, exit_code=0):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    # Synthetic argv recorder only: no Python/Mind module is executed or imported.
    fake = ('#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$MIND_CI_TEST_TRACE"\n'
            'printf "4200 tests collected in 1.00s\\n"\nexit "$MIND_CI_TEST_EXIT"\n')
    for name in ("pytest", "python"):
        tool = fake_bin / name
        tool.write_bytes(fake.encode("utf-8"))
        tool.chmod(0o755)
    script = tmp_path / "step.sh"
    script.write_bytes(text.encode("utf-8"))
    trace = tmp_path / "argv.txt"
    # No inherited provider credentials, bridge state or peer runtime pins needed.
    safe_keys = {"path", "systemroot", "windir", "systemdrive", "pathext", "comspec", "temp", "tmp"}
    env = {k: v for k, v in os.environ.items() if k.lower() in safe_keys}
    env["PATH"] = str(fake_bin) + os.pathsep + os.environ.get("PATH", "")
    env["MIND_CI_TEST_TRACE"] = str(trace)
    env["MIND_CI_TEST_EXIT"] = str(exit_code)
    done = subprocess.run([_bash(), "-e", script.name], cwd=tmp_path, env=env,
                          capture_output=True, text=True, timeout=30)
    calls = [shlex.split(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    return done, calls


@pytest.mark.parametrize("filename,job,name,prefix", [
    ("ci.yml", "test", "Run tests", []),
    ("tests.yml", "unified", "Run pytest", ["-m", "pytest"]),
])
@pytest.mark.parametrize("exit_code", [0, 7])
def test_native_bash_runs_both_scopes_and_stops_on_full_suite_failure(
        tmp_path, filename, job, name, prefix, exit_code):
    step = _step(_workflow(filename), job, name)
    done, calls = _run_block(tmp_path, step["run"], exit_code)
    assert done.returncode == exit_code, done.stdout + done.stderr
    assert calls[0][:len(prefix) + 1] == [*prefix, "tests/"]
    assert len(calls) == (2 if exit_code == 0 else 1)
    if exit_code == 0:
        assert calls[1][len(prefix):len(prefix) + len(MIND_TESTS)] == list(MIND_TESTS)


def test_native_bash_collection_passes_the_same_ten_paths(tmp_path):
    done, calls = _run_block(tmp_path, _step(_workflow("ci.yml"), "test", "Check test count")["run"])
    assert done.returncode == 0, done.stdout + done.stderr
    assert calls == [["tests/", *MIND_TESTS, "--collect-only", "-q"]]
