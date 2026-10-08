"""The CI "Check test count" step must fail closed.

K 011920df job 113510581050 printed ``[: 28144\\n13\\n80: integer expression
expected`` and still passed: ``grep -oP '\\d+'`` also took the duration digits of
``28144 tests collected in 13.80s``, the ``[`` error made the ``if`` false, and a
failed collection was never checked. These tests run the real step script from
``.github/workflows/ci.yml`` under bash with a fake ``pytest`` first on PATH.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
STEP_NAME = "Check test count"
FLOOR = 4200


def _bash() -> str:
    if sys.platform == "win32":
        # Git for Windows bash, never the WSL launcher in System32.
        exec_path = subprocess.run(["git", "--exec-path"], capture_output=True, text=True, check=True).stdout
        candidate = Path(exec_path.strip()).parents[2] / "bin" / "bash.exe"
        if candidate.is_file():
            return str(candidate)
        pytest.fail(f"Git for Windows bash not found at {candidate}")
    found = shutil.which("bash")
    if found is None:
        pytest.fail("bash is required to run the CI step script")
    return found


def _step_script() -> str:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = [step for step in workflow["jobs"]["test"]["steps"] if step.get("name") == STEP_NAME]
    assert len(steps) == 1, f"exactly one {STEP_NAME!r} step expected"
    return steps[0]["run"]


def _run(tmp_path: Path, stdout: str, exit_code: int = 0) -> subprocess.CompletedProcess[str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake = fake_bin / "pytest"
    fake.write_bytes(b'#!/usr/bin/env bash\nprintf "%s" "$FAKE_PYTEST_OUT"\nexit "$FAKE_PYTEST_EXIT"\n')
    fake.chmod(0o755)
    script = tmp_path / "step.sh"
    script.write_bytes(_step_script().encode("utf-8"))
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("WD_", "AGENT_BRIDGE_"))}
    env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
    env["FAKE_PYTEST_OUT"] = stdout
    env["FAKE_PYTEST_EXIT"] = str(exit_code)
    # GitHub runs an unannotated run step as ``bash -e {0}`` (see the job log).
    return subprocess.run([_bash(), "-e", script.name], cwd=tmp_path, env=env,
                          capture_output=True, text=True, timeout=60)


@pytest.mark.parametrize("duration", ["13.80s", "60.00s (0:01:00)", "61.23s (0:01:01)", "3601.00s (1:00:01)"])
@pytest.mark.parametrize("count", [FLOOR, 28144])
def test_a_single_count_at_or_above_the_floor_passes_despite_duration_digits(tmp_path, count, duration):
    # pytest's format_session_duration appends " (H:MM:SS)" from 60 s on (Root probe 21:09Z: M refused these).
    done = _run(tmp_path, f"tests/a.py::test_x\n\n{count} tests collected in {duration}\n")
    assert done.returncode == 0, done.stdout + done.stderr
    assert f"Test count: {count}\n" in done.stdout
    # The K step "passed" this case only because the [ comparison errored
    # ("integer expression expected" on Linux bash, "integer expected" in Git Bash).
    assert done.stderr.strip() == ""


def test_a_count_below_the_floor_fails(tmp_path):
    done = _run(tmp_path, f"{FLOOR - 1} tests collected in 9.99s\n")
    assert done.returncode != 0
    assert "dropped below" in done.stdout


@pytest.mark.parametrize(
    "stdout",
    [
        "",
        "\n",
        "no tests ran in 0.01s\n",
        "28144 13 80\n",
        "28144 tests collected, 3 errors in 13.80s\n",
        "28144 tests collected in 13.80s\n4199 tests collected in 1.00s\n4\n",
        "tests collected in 13.80s\n",
        "-28144 tests collected in 13.80s\n",
        "28144 tests collected in 13.80s trailing\n",
        # Root probe 20:52Z on L 57772fb2: both of these exited 0.
        "4199 tests collected in 1.00s\n28144 tests collected in 13.80s\n",
        "999999999999999999999999999999 tests collected in 13.80s\n",
        "28144 tests collected in 13.80s\n28144 tests collected in 13.80s\n",
        "9999999999 tests collected in 13.80s\n",
        # Only the exact " (H:MM:SS)" timedelta suffix is accepted; a day count is refused.
        "28144 tests collected in 60.00s (0:1:00)\n",
        "28144 tests collected in 60.00s (0:01:60)\n",
        "28144 tests collected in 60.00s (junk)\n",
        "28144 tests collected in 60.00s(0:01:00)\n",
        "28144 tests collected in 60.00s (0:01:00\n",
        "28144 tests collected in 60.00s (0:01:00) trailing\n",
        "28144 tests collected in 60.00s (0:01:00) (0:01:00)\n",
        "28144 tests collected in 86401.00s (1 day, 0:00:01)\n",
    ],
    ids=["empty", "blank", "no-tests", "bare-numbers", "errors", "trailing-number",
         "missing-count", "negative", "trailing-text", "two-summaries", "oversized-30-digits",
         "duplicate-summary", "oversized-10-digits", "suffix-short-minutes", "suffix-bad-seconds",
         "suffix-junk", "suffix-no-space", "suffix-unclosed", "suffix-trailing-text", "suffix-twice",
         "suffix-days"],
)
def test_a_missing_multiple_or_malformed_count_fails(tmp_path, stdout):
    done = _run(tmp_path, stdout)
    assert done.returncode != 0, done.stdout + done.stderr


@pytest.mark.parametrize("exit_code", [1, 2, 4, 5])
def test_a_failed_collection_fails_even_with_a_valid_count_line(tmp_path, exit_code):
    done = _run(tmp_path, "28144 tests collected in 13.80s\n", exit_code)
    assert done.returncode != 0, done.stdout + done.stderr
