"""Manual-only Windows coverage must not create a seventh PR-required context."""
from copy import deepcopy
from pathlib import Path
import json

import pytest
import yaml
from test_wd_reboot_bundle import LANE_TEST_SHELLS, _run_powershell
from test_wd_startup_recovery import q

PATH = Path(__file__).resolve().parents[2] / ".github/workflows/windows-shell-advisory.yml"


def _workflow():
    assert PATH.is_file(), "MUTANT_OMIT_WINDOWS_ADVISORY"
    return yaml.safe_load(PATH.read_text(encoding="utf-8"))


def _assert_advisory(workflow):
    assert set(workflow[True]) == {"workflow_dispatch"}, "MUTANT_ADD_PR_REQUIRED_CONTEXT"
    head = workflow[True]["workflow_dispatch"]["inputs"]["head"]
    assert head["type"] == "string" and head["required"] is True
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["jobs"]) == {"windows-shell-advisory"}
    job = workflow["jobs"]["windows-shell-advisory"]
    assert job["runs-on"] == "windows-latest", "MUTANT_KEEP_UBUNTU_ONLY"
    assert job["timeout-minutes"] == 20
    assert "continue-on-error" not in job  # advisory failures stay visible, not false green
    assert job["env"] == {"EXPECTED_HEAD": "${{ inputs.head }}"}
    steps = job["steps"]
    assert steps[0]["name"] == "Validate exact requested head"
    assert r"\A[0-9a-f]{40}\z" in steps[0]["run"]
    checkout = next(s for s in steps if s.get("uses") == "actions/checkout@v7")
    assert checkout["with"] == {"ref": "${{ inputs.head }}", "fetch-depth": 0, "persist-credentials": False}
    probe = next(s for s in steps if s.get("name") == "Verify exact checkout and distinct PS5/PS7")
    assert probe["shell"] == "pwsh" and "-cne $env:EXPECTED_HEAD" in probe["run"]
    assert "$v5 -cne '5'" in probe["run"] and "$v7 -lt 7" in probe["run"]
    assert "OrdinalIgnoreCase" in probe["run"] and "throw" in probe["run"]
    test = next(s for s in steps if s.get("name") == "Run affected Windows shell tests")
    assert "test_wd_lead_reply_delivery.py" in test["run"]
    assert "test_wd_native_tools_wake.py" in test["run"]
    assert "--junitxml=" in test["run"] and "exit $code" in test["run"]
    assert all("continue-on-error" not in s for s in steps)
    upload = next(s for s in steps if s.get("uses") == "actions/upload-artifact@v7")
    assert upload["if"] == "always()" and upload["with"]["path"] == ".codex-audit/windows-shell-advisory"
    assert "${{ inputs.head }}" not in "\n".join(s.get("run", "") for s in steps), "MUTANT_INTERPOLATE_HEAD_IN_SHELL"


def test_manual_advisory_pins_head_and_reports_real_distinct_engines():
    """MUTANT_OMIT_WINDOWS_ADVISORY / MUTANT_PS7_EQUALS_PS5."""
    _assert_advisory(_workflow())


@pytest.mark.parametrize("mutant", [
    "MUTANT_ADD_PR_REQUIRED_CONTEXT", "MUTANT_KEEP_UBUNTU_ONLY",
    "MUTANT_PERSIST_CHECKOUT_CREDENTIALS", "MUTANT_SKIP_ENGINE_PROOF",
    "MUTANT_FALSE_GREEN_ON_TEST_FAILURE",
])
def test_named_advisory_mutants_are_rejected(mutant):
    changed = deepcopy(_workflow())
    job = changed["jobs"]["windows-shell-advisory"]
    if mutant == "MUTANT_ADD_PR_REQUIRED_CONTEXT":
        changed[True]["pull_request"] = {}
    elif mutant == "MUTANT_KEEP_UBUNTU_ONLY":
        job["runs-on"] = "ubuntu-latest"
    elif mutant == "MUTANT_PERSIST_CHECKOUT_CREDENTIALS":
        next(s for s in job["steps"] if s.get("uses") == "actions/checkout@v7")["with"]["persist-credentials"] = True
    elif mutant == "MUTANT_SKIP_ENGINE_PROOF":
        next(s for s in job["steps"] if s.get("name") == "Verify exact checkout and distinct PS5/PS7")["run"] = "echo ok"
    else:
        job["continue-on-error"] = True
    with pytest.raises(AssertionError):
        _assert_advisory(changed)


def _execute_validation(ps, guard, candidate):
    script = f"""
$env:EXPECTED_HEAD={q(candidate)}
try {{
 & {{
{guard}
 }}
 @{{accepted=$true;major=$PSVersionTable.PSVersion.Major}}|ConvertTo-Json -Compress
}} catch {{
 @{{accepted=$false;major=$PSVersionTable.PSVersion.Major}}|ConvertTo-Json -Compress
}}
"""
    return json.loads(_run_powershell(script, executable=ps).stdout)


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("candidate,accepted", [
    pytest.param("0" * 40, True, id="valid-40"),
    pytest.param("0" * 40 + "\n", False, id="MUTANT_DROP_ABSOLUTE_SHA_END_ANCHOR-lf"),
    pytest.param("0" * 40 + "\r\n", False, id="crlf"),
    pytest.param("A" * 40, False, id="MUTANT_CASE_INSENSITIVE_SHA_MATCH-uppercase"),
    pytest.param("refs/heads/main", False, id="ref"),
])
def test_actual_validation_step_accepts_only_exact_lowercase_sha(ps, candidate, accepted):
    guard = _workflow()["jobs"]["windows-shell-advisory"]["steps"][0]["run"]
    result = _execute_validation(ps, guard, candidate)
    assert result["major"] >= 5
    assert result["accepted"] is accepted


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("mutant", ["MUTANT_DROP_ABSOLUTE_SHA_END_ANCHOR", "MUTANT_CASE_INSENSITIVE_SHA_MATCH"])
def test_executed_validation_source_mutants_are_killed(ps, mutant):
    guard = _workflow()["jobs"]["windows-shell-advisory"]["steps"][0]["run"]
    if mutant == "MUTANT_DROP_ABSOLUTE_SHA_END_ANCHOR":
        assert r"\A[0-9a-f]{40}\z" in guard
        faulty = guard.replace(r"\A[0-9a-f]{40}\z", "^[0-9a-f]{40}$")
        candidate = "0" * 40 + "\n"
    else:
        assert "-cnotmatch" in guard
        faulty = guard.replace("-cnotmatch", "-notmatch")
        candidate = "A" * 40
    assert _execute_validation(ps, guard, candidate)["accepted"] is False
    assert _execute_validation(ps, faulty, candidate)["accepted"] is True
