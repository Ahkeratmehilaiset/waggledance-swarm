"""Structural (text-only) contract of the Mind shadow job adapter and its opt-in native acceptance harness.

Nothing here starts PowerShell or any process: the adapter and the harness are read as text. The native harness is
never collected or run by CI. Design provenance: RCO1 (A1-A7, job reuse, R1, R2, the throwing-Stop rule), RCO2
(G1-G4 and the member witness), Lead (planning and Source-prep decisions); fable-5 wrote this test and the adapter.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADAPTER = ROOT / "ops" / "windows" / "reboot" / "Start-WdMindShadowJob.ps1"
HARNESS = ROOT / "tests" / "windows" / "wd_mind_shadow_job_native_acceptance.ps1"
PRODUCER = ROOT / "sandbox" / "mind" / "mind_capture_producer.py"


def _code(path):
    """The file's lines without comment-only lines and without the comment-based help block."""
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"<#.*?#>", "", text, flags=re.S)
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


@pytest.fixture(scope="module")
def adapter():
    assert ADAPTER.is_file(), ADAPTER
    return _code(ADAPTER)


@pytest.fixture(scope="module")
def harness():
    assert HARNESS.is_file(), HARNESS
    return _code(HARNESS)


def test_adapter_refuses_a_preloaded_type_before_reading_the_lane_loop(adapter):
    guard = adapter.index("'WdManagedTurnProcess' -as [type]")
    assert guard < adapter.index("ReadAllBytes(")
    call = re.search(r"^Initialize-WdNativeTurnType$", adapter, flags=re.M)
    assert call and guard < call.start()
    assert "Get-Command -Name 'Initialize-WdNativeTurnType'" in adapter


def test_adapter_imports_exactly_one_function_from_one_verified_read(adapter):
    assert adapter.count("ReadAllBytes(") == 1
    assert adapter.count("$loopPath") == 2
    assert adapter.count("ParseInput(") == 1
    assert "-ceq 'Initialize-WdNativeTurnType'" in adapter
    assert re.search(r"\$matched\.Count -ne 1\)\s*\{\s*throw", adapter)
    assert "Get-WdNativeToolsRuntimeFunctions" not in adapter
    assert not re.search(r"^\s*\.\s+\$loop", adapter, flags=re.M)
    read = adapter.index("$loopBytes = [IO.File]::ReadAllBytes(")
    assert (read < adapter.index("ComputeHash($loopBytes)") < adapter.index("ParseInput($loopText")
            < adapter.index("[scriptblock]::Create($matched[0].Extent.Text)"))
    assert adapter.count("[scriptblock]::Create(") == 1


def test_adapter_deadline_and_stop_wait_are_fixed_once_on_a_stopwatch(adapter):
    assert re.findall(r"\$DeadlineSeconds = (\d+)", adapter) == ["1800"]
    assert re.findall(r"\$StopConfirmationWaitSeconds = (\d+)", adapter) == ["30"]
    assert adapter.count("[Diagnostics.Stopwatch]::StartNew()") == 1
    assert adapter.count("$clock = ") == 1


def test_adapter_has_no_pid_kill_and_never_deletes(adapter):
    for word in ("Stop-Process", "taskkill", "TerminateProcess", ".Kill(", "Remove-Item", "Delete("):
        assert word not in adapter, word


def test_adapter_starts_the_producer_only_through_the_wrapper_surface(adapter):
    for text in ("$env:WD_BRIDGE_PYTHON_WRAPPER", "$env:WD_BRIDGE_CODE_ROOT", "$env:WD_BRIDGE_BIN",
                 "'sandbox/mind/mind_capture_producer.py'", "'--live-dir'"):
        assert text in adapter, text
    assert "python.exe" not in adapter and "python -B" not in adapter
    assert adapter.count("[WdManagedTurnProcess]::new(") == 1


def test_adapter_inputs_are_explicit_and_placeholders_are_refused(adapter):
    for name in ("ExpectedSourceCommit", "ExpectedManifestSha256", "ExpectedAdapterSha256", "ApprovedRunRoot",
                 "LiveDir"):
        assert re.search(r"\[Parameter\(Mandatory\)\] \[string\] \$" + name + r"\b", adapter), name
    assert "synthetic placeholder" in adapter
    assert adapter.index("[guid]::NewGuid()") < adapter.index("[WdManagedTurnProcess]::new(")


def _function(text, name):
    start = text.index("function " + name)
    match = re.search(r"\n\}\n", text[start:])
    assert match, name
    return text[start:start + match.end()]


def test_outcome_rules_completed_needs_drain_lock_and_no_unknown(adapter):
    body = _function(adapter, "Resolve-WdShadowOutcome")
    assert set(re.findall(r"return '([a-z_]+)'", body)) == {"completed", "stopped_at_deadline", "stop_unconfirmed"}
    completed = body[:body.index("return 'completed'")].rsplit("if (", 1)[1]
    for need in ("$Drained", "$LockAcquired", "$Unknown.Count -eq 0", "-not $StopCalled"):
        assert need in completed, need
    stopped = body[:body.index("return 'stopped_at_deadline'")].rsplit("if (", 1)[1]
    for need in ("$StopCalled", "$StopOk", "$StopConfirmed", "$LockAcquired", "$Unknown.Count -eq 0"):
        assert need in stopped, need
    assert " -or " not in completed and " -or " not in stopped
    assert body.rstrip().endswith("return 'stop_unconfirmed'\n}")


def test_records_are_published_once_without_overwrite(adapter):
    body = _function(adapter, "Publish-WdShadowRecord")
    assert "[IO.FileMode]::CreateNew" in body and "[IO.File]::Move(" in body
    assert "Set-Content" not in adapter and "Out-File" not in adapter
    for kind in ("'run_intent'", "'run_start'", "'controller_outcome'"):
        assert kind in adapter, kind
    assert adapter.index("'run_intent'") < adapter.index("[WdManagedTurnProcess]::new(") < adapter.index("'run_start'")


def test_lock_reacquire_uses_the_producer_lock_byte(adapter):
    producer = PRODUCER.read_text(encoding="utf-8")
    assert re.search(r"^LOCK_BYTE = 2 \*\* 30$", producer, flags=re.M)
    assert re.findall(r"\$LockByte = (\d+)", adapter) == [str(2 ** 30)]
    body = _function(adapter, "Test-WdShadowLiveDirLock")
    assert ".Lock($LockByte, 1)" in body and ".Unlock($LockByte, 1)" in body and "[IO.FileMode]::Open," in body


def test_harness_refuses_by_default_before_any_process_or_type(harness):
    lines = [line for line in harness.splitlines() if line.strip()]
    first = next(i for i, line in enumerate(lines) if not line.lstrip().startswith(("param", "[", ")", "#requires")))
    assert "OptInNativeAcceptance" in lines[first] and "exit 3" in " ".join(lines[first:first + 4])
    for word in ("Add-Type", "Start-Process", "::new(", "Invoke-Expression"):
        assert word not in harness, word


def test_harness_witness_is_fail_closed_and_no_case_passes_yet(harness):
    body = _function(harness, "Test-WdShadowMemberWitness")
    assert body.count("return 'PASS'") == 1
    passed = body[:body.index("return 'PASS'")]
    for need in ("$Expected.Count -eq 0", "start_witness", "released"):
        assert need in passed, need
    cases = re.findall(r"'(A[1-7])' = 'UNKNOWN'", harness)
    assert sorted(cases) == [f"A{i}" for i in range(1, 8)]
