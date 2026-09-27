"""Scheduled-task containment accepts a verified bridge pin on WD-AgentValue-Weekly (cold-boot rehearsal, 2026-09-27)."""
import hashlib
import json
import os
from pathlib import Path

import pytest

from test_wd_reboot_bundle import REBOOT, LANE_TEST_SHELLS, _run_powershell
from test_wd_startup_recovery import load, q

SCRIPT = REBOOT / "Set-WdTaskConsoleContainment.ps1"
BASE = '"C:\\Python\\project2-master\\.python\\Python313\\python.exe" "C:\\Python\\wd-agent-value-metric.py" --days 7 --post-bridge'
ORIGINAL = "C:\\Python\\wd-agent-value-metric.py --days 7 --post-bridge"
GEN = "d26357e14885e3d9b6d316de1806bdfab6d41026"


def bundle(store: Path, generation: str = GEN, content: bytes = b'{"source_commit":"x"}') -> str:
    root = store / generation
    root.mkdir(parents=True)
    (root / "deployment-manifest.json").write_bytes(content)
    return hashlib.sha256(content).hexdigest().upper()


def pin(store: Path, generation: str, sha: str) -> str:
    return f' --bridge-bundle "{store}\\{generation}" --bridge-manifest-sha256 {sha}'


def suffix_of(ps: str, arguments: str, store: Path, base: str = BASE):
    script = load(SCRIPT, "Get-VerifiedBridgePinSuffix") + f"""
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$r = Get-VerifiedBridgePinSuffix -Arguments {q(arguments)} -Base {q(base)} -BundleStore {q(store)}
@{{ is_null = ($null -eq $r); value = [string]$r }} | ConvertTo-Json -Compress
"""
    record = json.loads(_run_powershell(script, executable=ps).stdout)
    return None if record["is_null"] else record["value"]


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_bare_arguments_carry_no_pin(ps, tmp_path):
    assert suffix_of(ps, BASE, tmp_path) == ""


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_a_pin_to_a_deployed_bundle_with_its_manifest_hash_is_accepted(ps, tmp_path):
    sha = bundle(tmp_path)
    assert suffix_of(ps, BASE + pin(tmp_path, GEN, sha), tmp_path) == pin(tmp_path, GEN, sha)
    assert suffix_of(ps, ORIGINAL + pin(tmp_path, GEN, sha), tmp_path, base=ORIGINAL) == pin(tmp_path, GEN, sha)


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("case", ["wrong_hash", "lowercase_hash", "no_manifest", "short_generation", "other_store",
                                  "trailing_argument", "base_mismatch", "unquoted_bundle", "extra_space"])
def test_anything_but_an_exact_verified_pin_is_refused(ps, tmp_path, case):
    store = tmp_path / "store"
    sha = bundle(store)
    arguments = BASE + pin(store, GEN, sha)
    if case == "wrong_hash":
        arguments = BASE + pin(store, GEN, "0" * 64)
    elif case == "lowercase_hash":
        arguments = BASE + pin(store, GEN, sha.lower())
    elif case == "no_manifest":
        (store / GEN / "deployment-manifest.json").unlink()
    elif case == "short_generation":
        arguments = BASE + pin(store, GEN[:39], sha)
    elif case == "other_store":
        other = tmp_path / "other"
        other_sha = bundle(other)
        arguments = BASE + pin(other, GEN, other_sha)
    elif case == "trailing_argument":
        arguments += " --days 30"
    elif case == "base_mismatch":
        arguments = BASE.replace("--days 7", "--days 8") + pin(store, GEN, sha)
    elif case == "unquoted_bundle":
        arguments = BASE + f" --bridge-bundle {store}\\{GEN} --bridge-manifest-sha256 {sha}"
    elif case == "extra_space":
        arguments = BASE + " " + pin(store, GEN, sha)
    assert suffix_of(ps, arguments, store) is None


@pytest.mark.skipif(os.name != "nt", reason="junctions are Windows reparse points")
@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_a_bundle_that_is_a_junction_is_refused(ps, tmp_path):
    real = tmp_path / "real"
    sha = bundle(real)
    store = tmp_path / "store"
    store.mkdir()
    _run_powershell(f"New-Item -ItemType Junction -Path {q(store / GEN)} -Target {q(real / GEN)} | Out-Null", executable=ps)
    assert (store / GEN / "deployment-manifest.json").is_file()        # the junction resolves
    assert suffix_of(ps, BASE + pin(store, GEN, sha), store) is None


def task_pin(ps: str, arguments: list[str], bridge_pin: bool | None, store: Path) -> str:
    actions = ",".join(f"[pscustomobject]@{{Arguments={q(a)}}}" for a in arguments)
    script = load(SCRIPT, "Get-VerifiedBridgePinSuffix") + load(SCRIPT, "Get-TaskBridgePin") + f"""
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$task = [pscustomobject]@{{ Actions = @({actions}) }}
$job = [pscustomobject]@{{ hidden_arguments = {q(BASE)}; original_arguments = {q(ORIGINAL)} }}
if ({'$null' if bridge_pin is None else '$true'}) {{ $job | Add-Member -NotePropertyName bridge_pin -NotePropertyValue ${str(bool(bridge_pin)).lower()} }}
@{{ pin = [string](Get-TaskBridgePin -Task $task -Job $job -BundleStore {q(store)}) }} | ConvertTo-Json -Compress
"""
    return json.loads(_run_powershell(script, executable=ps).stdout)["pin"]


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_the_task_pin_comes_from_either_base_form_and_only_for_a_pinned_job(ps, tmp_path):
    sha = bundle(tmp_path)
    good = pin(tmp_path, GEN, sha)
    assert task_pin(ps, [BASE + good], True, tmp_path) == good
    assert task_pin(ps, [ORIGINAL + good], True, tmp_path) == good
    assert task_pin(ps, [BASE + good], False, tmp_path) == ""               # a job with bridge_pin false never takes one
    assert task_pin(ps, [BASE + good], None, tmp_path) == ""                # nor does a job without the property (StrictMode)
    assert task_pin(ps, [BASE + good, BASE], True, tmp_path) == ""         # not a single action
    assert task_pin(ps, [BASE + pin(tmp_path, GEN, "F" * 64)], True, tmp_path) == ""


def test_only_the_agent_value_job_takes_a_pin_and_apply_keeps_it():
    text = SCRIPT.read_text(encoding="utf-8")
    weekly = text.index("name = 'WD-AgentValue-Weekly'")
    stall = text.index("name = 'WD-ConsensusStallDetector'")
    assert "bridge_pin = $true" in text[weekly:text.index("}", weekly)]
    assert "bridge_pin" not in text[stall:text.index("}", stall)]
    assert text.count("bridge_pin = $true") == 1
    apply = text.index("# Wrapping keeps the verified pin the plan saw")
    assert text.index("$pins[[string]$job.name] = $pin") < apply
    assert text.count("-Arguments $hiddenArguments") == 2                  # the wrap check and the postcondition
    assert "Argument = $hiddenArguments" in text
    assert "-Arguments ([string]$job.hidden_arguments + $pin)" in text
    assert "-Arguments ([string]$job.original_arguments + $pin)" in text
