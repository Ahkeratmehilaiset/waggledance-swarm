"""The bounded Mind release is executed AND collected, never all of sandbox.

Only parse workflow text here: no Mind import, dataset discovery or CLI launch.
The final composition supplies 33 explicitly admitted synthetic test files.
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
    "sandbox/mind/step2/test_q4_readonly_port.py",
    "sandbox/mind/step2/test_wd_q4_private_read.py",
    "sandbox/mind/step2/test_reader_slot.py",
    "sandbox/mind/step2/test_runtime_manifest.py",
    "sandbox/mind/step2/test_runtime_bootstrap.py",
    "sandbox/mind/step2/test_runtime_builder.py",
    "sandbox/mind/step2/test_runtime_launcher.py",
    "sandbox/mind/step2/test_shared_projection.py",
    "sandbox/mind/step2/test_shared_projection_q4_synthetic.py",
    "sandbox/mind/step2/test_shared_result_resolver.py",
    "sandbox/mind/step2/test_shared_capture_adapter.py",
    "sandbox/mind/step2/test_shared_bridge_snapshot.py",
    "sandbox/mind/step2/test_shared_index_checkpoint.py",
    "sandbox/mind/step2/test_shared_index_builder.py",
    "sandbox/mind/step2/test_shared_source_anchor.py",
    "sandbox/mind/step2/test_shared_strict_chain.py",
    "sandbox/mind/step2/test_shared_builder_e2e.py",
    "sandbox/mind/step2/test_shared_history.py",
    "sandbox/mind/step2/test_shared_index_watermark.py",
    "sandbox/mind/step2/test_shared_index_history_commit.py",
    "sandbox/mind/step2/test_status_metadata_producer.py",
    "sandbox/mind/step2/test_status_public_filter.py",
    "sandbox/mind/step2/test_shared_producer.py",
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
    assert shlex.split(match.group(1)) == ["pytest", "tests/", "--collect-only", "-q"]
    mind = re.search(r'if ! MIND_OUT=\$\((python -m pytest [^\n]+) 2>/dev/null\); then', text)
    assert mind, "the separate Mind collection must also refuse failure"
    assert shlex.split(mind.group(1)) == ["python", "-m", "pytest", *MIND_TESTS, "--collect-only", "-q"]
    assert '"$COUNT" -ge 4200' in text, "never lower the existing full-suite floor"
    assert '"$SUMMARIES" != "1"' in text, "retain the exact-one-summary guard"


def test_matrix_executes_the_existing_full_suite_then_exact_finite_mind_scope():
    _assert_runs(_workflow("ci.yml"), "test", "Run tests",
                 "pytest tests/ -x -q --tb=short -p no:warnings",
                 ["python", "-m", "pytest"], ["-x", "-q", "--tb=short", "-p", "no:warnings"])


def test_unified_executes_the_existing_full_suite_then_exact_finite_mind_scope():
    _assert_runs(_workflow("tests.yml"), "unified", "Run pytest",
                 "python -m pytest tests/ -v --tb=short",
                 ["python", "-m", "pytest"], ["-v", "--tb=short"])


def test_collection_covers_the_same_finite_scope_and_preserves_count_guards():
    _assert_collection(_workflow("ci.yml"))


def _assert_mind_entrypoint(workflow, step_name):
    # CI ffa21 passed tests/ but the separate console pytest could not import tools.
    # Both execution and collection must keep the repository on sys.path without a global env override.
    text = _step(workflow, "test", step_name)["run"]
    if step_name == "Run tests":
        command = text.strip().splitlines()[1]
    else:
        match = re.search(r'if ! MIND_OUT=\$\(([^;\n]+) 2>/dev/null\); then', text)
        assert match
        command = match.group(1)
    assert shlex.split(command)[:3] == ["python", "-m", "pytest"], "console entrypoint loses repo imports"


@pytest.mark.parametrize("step_name", ["Run tests", "Check test count"])
def test_matrix_mind_uses_repository_aware_module_entrypoint(step_name):
    _assert_mind_entrypoint(_workflow("ci.yml"), step_name)


@pytest.mark.parametrize("step_name", ["Run tests", "Check test count"])
def test_mutant_console_mind_entrypoint_is_killed_with_positive_control(step_name):
    workflow = _workflow("ci.yml")
    _assert_mind_entrypoint(workflow, step_name)
    mutant = deepcopy(workflow)
    step = _step(mutant, "test", step_name)
    original = step["run"]
    step["run"] = original.replace("python -m pytest sandbox/mind/", "pytest sandbox/mind/")
    assert step["run"] != original
    # MUTANT_CONSOLE_MIND_RUN and MUTANT_CONSOLE_MIND_COLLECTION keep all paths/options, lose repo imports.
    with pytest.raises(AssertionError, match="console entrypoint"):
        _assert_mind_entrypoint(mutant, step_name)


@pytest.mark.parametrize("filename,job,name,full,prefix,options", [
    ("ci.yml", "test", "Run tests", "pytest tests/ -x -q --tb=short -p no:warnings",
     ["python", "-m", "pytest"], ["-x", "-q", "--tb=short", "-p", "no:warnings"]),
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
    step["run"] = step["run"].replace("pytest " + " ".join(MIND_TESTS), "pytest tests/")
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


def test_scope_contains_only_33_unique_explicit_python_test_files():
    assert len(MIND_TESTS) == len(set(MIND_TESTS)) == 33
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


def _run_block(tmp_path, text, exit_code=0, full_count=4200, mind_count=10,
               mind_exit_code=None, mind_summary=None):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    # Synthetic argv recorder only: no Python/Mind module is executed or imported.
    fake = ('#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$MIND_CI_TEST_TRACE"\n'
            'COUNT=0; HAS_MIND=0\nfor ARG in "$@"; do\n'
            '  case "$ARG" in tests/) COUNT=$((COUNT + MIND_CI_TEST_FULL_COUNT));; '
            'sandbox/mind/*) HAS_MIND=1;; esac\ndone\n'
            'if [ "$HAS_MIND" = 1 ]; then COUNT=$((COUNT + MIND_CI_TEST_MIND_COUNT)); fi\n'
            'if [ "$HAS_MIND" = 1 ] && [ "$MIND_CI_TEST_CUSTOM_OUT" = 1 ]; then\n'
            '  printf "%s" "$MIND_CI_TEST_MIND_OUT"\n'
            'else printf "%s tests collected in 1.00s\\n" "$COUNT"; fi\n'
            'if [ "$HAS_MIND" = 1 ]; then exit "$MIND_CI_TEST_MIND_EXIT"; fi\n'
            'exit "$MIND_CI_TEST_EXIT"\n')
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
    env["MIND_CI_TEST_FULL_COUNT"] = str(full_count)
    env["MIND_CI_TEST_MIND_COUNT"] = str(mind_count)
    env["MIND_CI_TEST_MIND_EXIT"] = str(exit_code if mind_exit_code is None else mind_exit_code)
    env["MIND_CI_TEST_CUSTOM_OUT"] = "0" if mind_summary is None else "1"
    env["MIND_CI_TEST_MIND_OUT"] = "" if mind_summary is None else mind_summary
    done = subprocess.run([_bash(), "-e", script.name], cwd=tmp_path, env=env,
                          capture_output=True, text=True, timeout=30)
    calls = [shlex.split(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    return done, calls


@pytest.mark.parametrize("filename,job,name,prefix,mind_prefix", [
    ("ci.yml", "test", "Run tests", [], ["-m", "pytest"]),
    ("tests.yml", "unified", "Run pytest", ["-m", "pytest"], ["-m", "pytest"]),
])
@pytest.mark.parametrize("exit_code", [0, 7])
def test_native_bash_runs_both_scopes_and_stops_on_full_suite_failure(
        tmp_path, filename, job, name, prefix, mind_prefix, exit_code):
    step = _step(_workflow(filename), job, name)
    done, calls = _run_block(tmp_path, step["run"], exit_code)
    assert done.returncode == exit_code, done.stdout + done.stderr
    assert calls[0][:len(prefix) + 1] == [*prefix, "tests/"]
    assert len(calls) == (2 if exit_code == 0 else 1)
    if exit_code == 0:
        assert calls[1][:len(mind_prefix)] == mind_prefix
        assert calls[1][len(mind_prefix):len(mind_prefix) + len(MIND_TESTS)] == list(MIND_TESTS)


def test_native_bash_collection_passes_the_same_explicit_paths(tmp_path):
    done, calls = _run_block(tmp_path, _step(_workflow("ci.yml"), "test", "Check test count")["run"])
    assert done.returncode == 0, done.stdout + done.stderr
    assert calls == [["tests/", "--collect-only", "-q"], ["-m", "pytest", *MIND_TESTS, "--collect-only", "-q"]]


def test_mind_count_cannot_mask_a_full_suite_drop_below_the_existing_floor(tmp_path):
    done, calls = _run_block(tmp_path, _step(_workflow("ci.yml"), "test", "Check test count")["run"],
                             full_count=4199, mind_count=1000)
    assert done.returncode != 0, "Mind tests must not inflate the original tests/ floor"
    assert "dropped below 4200" in done.stdout
    assert calls == [["tests/", "--collect-only", "-q"]]


def test_native_mind_collection_failure_is_not_swallowed(tmp_path):
    done, calls = _run_block(tmp_path, _step(_workflow("ci.yml"), "test", "Check test count")["run"],
                             mind_exit_code=7)
    assert done.returncode != 0
    assert "Mind test collection failed" in done.stdout
    assert len(calls) == 2


@pytest.mark.parametrize("summary", ["", "no tests collected\n", "0 tests collected in 1.00s\n",
    "10 tests collected in 1.00s trailing\n", "10 tests collected in 1.00s\n20 tests collected in 1.00s\n"])
def test_native_mind_collection_requires_one_positive_unambiguous_summary(tmp_path, summary):
    done, calls = _run_block(tmp_path, _step(_workflow("ci.yml"), "test", "Check test count")["run"],
                             mind_summary=summary)
    assert done.returncode != 0, done.stdout + done.stderr
    assert len(calls) == 2


def test_mutant_combined_collection_can_mask_the_old_floor_and_is_killed(tmp_path):
    mutant = deepcopy(_workflow("ci.yml"))
    step = _step(mutant, "test", "Check test count")
    step["run"] = step["run"].replace("pytest tests/ --collect-only",
        "pytest tests/ " + " ".join(MIND_TESTS) + " --collect-only")
    # MUTANT_COMBINED_COLLECTION_FLOOR: 4199 full + 1000 Mind looks like 5199.
    done, _ = _run_block(tmp_path, step["run"], full_count=4199, mind_count=1000)
    assert done.returncode == 0 and "Test count: 5199" in done.stdout
    with pytest.raises(AssertionError):
        _assert_collection(mutant)
