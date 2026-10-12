"""Contract for the time budget of the ``unified`` job in .github/workflows/tests.yml.

The job runs the whole pytest suite in one serial step. On 2026-10-12 the 60-minute
limit cancelled it on PR #1864 (job 38228348100): pytest had started at 09:15:31Z,
collected 29,344 items, and reached the [ 95%] progress mark after 56.2 minutes.
main's own run 38221355158 spent 53.2 minutes in pytest, and the three ci.yml matrix
jobs (no limit, ``-q``) spent 49.2-55.6 minutes in their test step, so the cost is the
suite, not ``-v`` output. About 40 of the 56 minutes were the 1,931 parametrized tests
that start PowerShell (1.23 s each).

The fix is a bounded budget, not fewer tests: the job keeps every step, the pytest
step keeps the full ``tests/`` run, and the limit is an explicit integer above the
measured need (about 64 minutes: 3.3 before pytest, about 60 in it) but bounded.
"""
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "tests.yml"

# Measured full-job need in minutes (see the module docstring) and a fixed ceiling, so the
# limit cannot silently fall back under the need or grow without a bound.
MEASURED_NEED_MINUTES = 64
MIN_BUDGET_MINUTES = 75
MAX_BUDGET_MINUTES = 120

UNIFIED_STEPS = [
    "actions/checkout@v7",
    "Set up Python 3.13",
    "Install system dependencies",
    "Install Python dependencies",
    "Upload pip log on failure",
    "Compile all Python files",
    "Run legacy tests (informational)",
    "Run pytest",
    "Run benchmark baseline",
    "Upload benchmark artifacts",
]


def _jobs():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]


def _step(job, name):
    matches = [step for step in job["steps"] if step.get("name") == name]
    assert len(matches) == 1, (name, matches)
    return matches[0]


def test_the_workflow_keeps_both_jobs():
    assert set(_jobs()) == {"unified", "lock-resolver-check"}


def test_the_unified_job_keeps_every_step_in_order():
    steps = _jobs()["unified"]["steps"]
    assert [step.get("name") or step.get("uses") for step in steps] == UNIFIED_STEPS


def test_the_pytest_step_still_runs_the_whole_suite_and_can_fail():
    job = _jobs()["unified"]
    step = _step(job, "Run pytest")

    assert step["run"].strip() == "python -m pytest tests/ -v --tb=short"
    assert "continue-on-error" not in step and "continue-on-error" not in job
    assert "if" not in step and "timeout-minutes" not in step


def test_the_unified_budget_is_a_bounded_integer_above_the_measured_need():
    budget = _jobs()["unified"]["timeout-minutes"]

    assert type(budget) is int, budget
    assert MEASURED_NEED_MINUTES < MIN_BUDGET_MINUTES <= budget <= MAX_BUDGET_MINUTES, budget


def test_the_lock_resolver_budget_is_unchanged():
    assert _jobs()["lock-resolver-check"]["timeout-minutes"] == 10
