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
    for need in ("$Drained", "$RootExited", "$LockAcquired", "$Unknown.Count -eq 0", "-not $StopCalled"):
        assert need in completed, need
    stopped = body[:body.index("return 'stopped_at_deadline'")].rsplit("if (", 1)[1]
    for need in ("$StopCalled", "$StopOk", "$StopConfirmed", "$RootExited", "$LockAcquired", "$Unknown.Count -eq 0"):
        assert need in stopped, need
    assert " -or " not in completed and " -or " not in stopped
    assert body.rstrip().endswith("return 'stop_unconfirmed'\n}")


def test_drain_stop_and_lock_flags_come_from_their_own_checks(adapter):
    drain = r"if \(\$null -ne \$rootExitAt -and \$managed\.ActiveProcesses -eq 0\) \{\s*\$drained = \$true"
    assert re.search(drain, adapter) and adapter.count("$drained = $true") == 1
    confirm = r"if \(\$managed\.ActiveProcesses -eq 0\) \{ \$stopConfirmed = \$true; break \}"
    assert re.search(confirm, adapter) and adapter.count("$stopConfirmed = $true") == 1
    assert re.search(r"try \{\s*\$managed\.Stop\(\)\s*\$stopOk = \$true\s*\} catch", adapter)
    assert adapter.count("$stopOk = $true") == 1
    assert "$lockOk = [bool]$lock.acquired -and [bool]$lock.released" in adapter
    assert adapter.count("$lockOk = ") == 1 and "-LockAcquired $lockOk" in adapter


def test_stop_path_reobserves_the_root_and_records_match_the_outcome_inputs(adapter):
    # D1 (cp2): every root-exit time comes from a Wait on the managed handle; after a returned Stop and a confirmed
    # drain the root is observed again before the lock; the recorded fields use the same variables as the outcome.
    sets = [line.strip() for line in adapter.splitlines() if "$rootExitAt = $clock" in line]
    assert sets == ["if ($managed.Wait($slice)) { $rootExitAt = $clock.Elapsed.TotalSeconds }",
                    "if ($rootGone -eq $true -and $null -eq $rootExitAt) { $rootExitAt = $clock.Elapsed.TotalSeconds }"]
    assert adapter.count("$rootGone = $managed.Wait(0)") == 1
    assert (adapter.index("$stopConfirmed = $true") < adapter.index("if ($stopConfirmed) {")
            < adapter.index("$rootGone = $managed.Wait(0)") < adapter.index("$lock = Test-WdShadowLiveDirLock"))
    assert re.search(r"if \(\$stopConfirmed\) \{\s*\$drainAt = \$clock\.Elapsed\.TotalSeconds", adapter)
    assert re.search(r"\$rootGone = \$managed\.Wait\(0\)\s*\} catch \{\s*\$rootGone = \$null\s*"
                     r"Add-WdShadowUnknown \$unknown 'root_exit_utc'", adapter)
    assert re.search(r"if \(\$rootGone -eq \$false\) \{\s*Add-WdShadowUnknown \$unknown 'root_exit_utc'", adapter)
    assert re.search(r"if \(\$drained -or \$stopConfirmed\) \{\s*\$lock = Test-WdShadowLiveDirLock", adapter)
    for text in ("$rootExited = $null -ne $rootExitAt", "-RootExited $rootExited", "-Drained $drained",
                 "$jobDrained = $drained -or $stopConfirmed", "drained = $jobDrained;", "-StopConfirmed $stopConfirmed",
                 "lock_reacquire = $lock",
                 "if ($rootExited) { $rootExitUtc = Get-WdShadowUtc -ElapsedSeconds $rootExitAt }"):
        assert adapter.count(text) == 1, text


def test_intent_follows_input_and_type_preparation_right_before_the_create(adapter):
    # D2 (cp2): fallible input and type preparation first, then run_intent, then at once the single constructor.
    stdin = adapter.index("(New-Object IO.FileStream($inputPath, [IO.FileMode]::CreateNew)).Dispose()")
    init = re.search(r"^Initialize-WdNativeTurnType$", adapter, flags=re.M).start()
    verify = adapter.index("$managedType = 'WdManagedTurnProcess' -as [type]")
    intent = adapter.index("Publish-WdShadowRecord -Directory $runDir -Name 'run_intent' -Record $intent")
    assert stdin < init < verify < intent < adapter.index("[WdManagedTurnProcess]::new(")
    assert adapter.count("-Name 'run_intent'") == 1
    assert re.search(r"-Name 'run_intent' -Record \$intent\n\s*try \{\n\s*\$managed = \[WdManagedTurnProcess\]::new\(",
                     adapter)
    shape = adapter[verify:intent]
    for need in ("GetConstructor(", "GetMethod('Stop')", "GetMethod('Wait')", "GetProperty('ActiveProcesses')",
                 "throw"):
        assert need in shape, need


def test_identity_is_bound_to_the_integrity_label_and_an_explicit_user_sid(adapter):
    # T1 (cp2): the actual mandatory integrity label and an explicitly supplied approved user SID; never group
    # membership, never a default taken from the caller.
    assert re.search(r"\[Parameter\(Mandatory\)\] \[string\] \$ApprovedUserSid\b", adapter)
    assert re.search(r"\[Parameter\(Mandatory\)\] \[ValidateSet\('medium', 'high', IgnoreCase = \$false\)\] "
                     r"\[string\] \$ApprovedIntegrityLevel\b", adapter)
    for word in ("IsInRole", "WindowsBuiltInRole", "Administrator", "ApprovedTokenClass", ".Groups"):
        assert word not in adapter, word
    assert not re.search(r"\$Approved(UserSid|IntegrityLevel)\s*=", adapter)
    assert "-Name 'ApprovedUserSid' -Value $ApprovedUserSid -Pattern '^S-1-5-21-\\d+-\\d+-\\d+-\\d+$'" in adapter
    assert "$whoami = Join-Path $env:SystemRoot 'System32\\whoami.exe'" in adapter
    assert "& $whoami '/groups' '/fo' 'csv' '/nh'" in adapter
    assert "Where-Object { [string]$_.sid -cmatch '^S-1-16-(\\d+)$' }" in adapter
    assert "$levels = @{ '8192' = 'medium'; '12288' = 'high' }" in adapter
    for check in (r"if \(\$labels\.Count -ne 1\) \{ throw", r"if \(-not \$integrity\) \{ throw",
                  r"if \(\$integrity -cne \$ApprovedIntegrityLevel\) \{\s*throw",
                  r"if \(\$actualUserSid -cne \$ApprovedUserSid\) \{ throw"):
        assert re.search(check, adapter), check
    assert "$actualUserSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value" in adapter
    checked = adapter.index("$actualUserSid -cne $ApprovedUserSid")
    assert checked < adapter.index("ReadAllBytes(") and checked < adapter.index("'run_intent'")


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
    for need in ("$Expected.Count -eq 0", "start_witness", "released", "[string]$seen.launch_id -cne $LaunchId"):
        assert need in passed, need
    cases = re.findall(r"'(A[1-7])' = 'UNKNOWN'", harness)
    assert sorted(cases) == [f"A{i}" for i in range(1, 8)]
