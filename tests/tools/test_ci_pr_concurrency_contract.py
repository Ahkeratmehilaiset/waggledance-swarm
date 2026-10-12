"""Superseded PR runs share a workflow-scoped group; required checks stay intact.

Names below are mutation controls, not simulated GitHub cancellation receipts.
Actual cancellation is GitHub's workflow-level concurrency behavior.
"""
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ["ci.yml", "tests.yml"]
GROUP = (
    "${{ github.workflow }}-"
    "${{ github.event_name == 'pull_request' && 'pr' || 'run' }}-"
    "${{ github.event.pull_request.number || github.run_id }}"
)
CANCEL = "${{ github.event_name == 'pull_request' }}"


def _workflow(name):
    return yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))


def _assert_policy(workflow):
    concurrency = workflow.get("concurrency")
    assert isinstance(concurrency, dict), "MUTANT_REMOVE_PR_CONCURRENCY"
    assert set(concurrency) == {"group", "cancel-in-progress"}
    assert concurrency["group"] == GROUP, "MUTANT_HEAD_OR_GLOBAL_GROUP"
    assert concurrency["cancel-in-progress"] == CANCEL, "MUTANT_CANCEL_MAIN_RUNS"


@pytest.mark.parametrize("name", WORKFLOWS)
def test_superseded_pr_heads_share_workflow_scoped_group(name):
    """MUTANT_REMOVE_PR_CONCURRENCY / MUTANT_HEAD_OR_GLOBAL_GROUP."""
    _assert_policy(_workflow(name))


@pytest.mark.parametrize("name", WORKFLOWS)
def test_non_pr_runs_have_unique_group_and_never_cancel_running_main(name):
    """MUTANT_CANCEL_MAIN_RUNS / MUTANT_COLLIDE_PR_AND_PUSH_GROUPS."""
    policy = _workflow(name)["concurrency"]
    assert policy["cancel-in-progress"] == CANCEL
    assert "github.event_name == 'pull_request' && 'pr' || 'run'" in policy["group"]
    assert "github.event.pull_request.number || github.run_id" in policy["group"]
    assert "github.sha" not in policy["group"] and "github.run_attempt" not in policy["group"]


def test_six_required_check_names_and_full_test_commands_are_unchanged():
    """MUTANT_RENAME_REQUIRED_CHECK / MUTANT_NARROW_TEST_COMMAND."""
    ci, unified = (_workflow(name) for name in WORKFLOWS)
    assert set(ci["jobs"]) == {"test", "security-scan"}
    assert ci["jobs"]["test"]["strategy"] == {
        "fail-fast": False, "matrix": {"python-version": ["3.11", "3.12", "3.13"]}
    }
    assert set(unified["jobs"]) == {"unified", "lock-resolver-check"}
    assert all("name" not in job for workflow in (ci, unified) for job in workflow["jobs"].values())
    for workflow, job_id, step_name, command in [
        (ci, "test", "Run tests", "pytest tests/ -x -q --tb=short -p no:warnings"),
        (unified, "unified", "Run pytest", "python -m pytest tests/ -v --tb=short"),
    ]:
        job = workflow["jobs"][job_id]
        steps = [s for s in job["steps"] if s.get("name") == step_name]
        assert len(steps) == 1 and steps[0]["run"].strip() == command
        assert "if" not in job and "continue-on-error" not in job
        assert "if" not in steps[0] and "continue-on-error" not in steps[0]


@pytest.mark.parametrize("name", WORKFLOWS)
def test_workflow_triggers_are_not_narrowed(name):
    """MUTANT_SKIP_PR_TRIGGER / MUTANT_ADD_WORKFLOW_PATH_FILTER."""
    triggers = _workflow(name)[True]  # PyYAML YAML1.1 maps `on` to True.
    assert set(triggers) == {"push", "pull_request", "workflow_dispatch"}
    for event in ("push", "pull_request"):
        assert set(triggers[event]) == {"branches"}
        assert set(triggers[event]["branches"]) == {"main", "master"}
    assert triggers["workflow_dispatch"] is None


@pytest.mark.parametrize("name", WORKFLOWS)
@pytest.mark.parametrize("mutant", [
    "MUTANT_REMOVE_PR_CONCURRENCY",
    "MUTANT_HEAD_OR_GLOBAL_GROUP",
    "MUTANT_DROP_WORKFLOW_NAMESPACE",
    "MUTANT_CANCEL_MAIN_RUNS",
    "MUTANT_NEVER_CANCEL_SUPERSEDED_HEAD",
])
def test_named_mutants_are_rejected_without_editing_source(name, mutant):
    changed = deepcopy(_workflow(name))
    changed["concurrency"] = {"group": GROUP, "cancel-in-progress": CANCEL}
    if mutant == "MUTANT_REMOVE_PR_CONCURRENCY":
        del changed["concurrency"]
    elif mutant == "MUTANT_HEAD_OR_GLOBAL_GROUP":
        changed["concurrency"]["group"] += "-${{ github.sha }}"
    elif mutant == "MUTANT_DROP_WORKFLOW_NAMESPACE":
        changed["concurrency"]["group"] = GROUP.replace("${{ github.workflow }}-", "")
    elif mutant == "MUTANT_CANCEL_MAIN_RUNS":
        changed["concurrency"]["cancel-in-progress"] = True
    else:
        changed["concurrency"]["cancel-in-progress"] = False
    with pytest.raises(AssertionError):
        _assert_policy(changed)
