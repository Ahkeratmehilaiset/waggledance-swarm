"""Structural (text-only) contract of the Mind shadow job adapter and its opt-in native acceptance harness.

Nothing here starts PowerShell or any process: the adapter and the harness are read as text. The native harness is
never collected or run by CI. Design provenance: RCO1 (A1-A7, job reuse, R1, R2, the throwing-Stop rule), RCO2
(G1-G4 and the member witness), Lead (planning and Source-prep decisions, and the 2026-10-21 guard Source-GO whose
disk rule is Lead's deployment budget at 542e26be, with its follow-up on reparse and mount ancestry after Root's
static finding); fable-5 wrote this test and the adapter.
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


def test_adapter_deadline_is_bounded_and_stop_wait_fixed_once_on_a_stopwatch(adapter):
    assert re.findall(r"\[ValidateRange\(60, 1800\)\] \[int\] \$RunDeadlineSeconds = (\d+)", adapter) == ["1800"]
    assert re.findall(r"(?m)^\$DeadlineSeconds = (.+)$", adapter) == ["$RunDeadlineSeconds"]
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
    assert set(re.findall(r"return '([a-z_]+)'", body)) == {
        "completed", "stopped_by_off_switch", "stopped_at_disk_floor", "stopped_at_deadline", "stop_unconfirmed"}
    completed = body[:body.index("return 'completed'")].rsplit("if (", 1)[1]
    for need in ("$Drained", "$RootExited", "$LockAcquired", "$Unknown.Count -eq 0", "-not $StopCalled"):
        assert need in completed, need
    stopped = body[:body.index("return 'stopped_at_deadline'")].rsplit("if (", 1)[1]
    for need in ("$StopCalled", "$StopOk", "$StopConfirmed", "$RootExited", "$LockAcquired", "$Unknown.Count -eq 0"):
        assert need in stopped, need
    assert " -or " not in completed and " -or " not in stopped
    assert body.rstrip().endswith("return 'stop_unconfirmed'\n}")


def test_drain_stop_and_lock_flags_come_from_their_own_checks(adapter):
    # Tools P2 (late drain): the drain is credited only from one clock reading after the observation, strictly
    # before the deadline; tests/tools/test_wd_mind_shadow_deadline.py runs this loop on a fake clock.
    drain = (r"if \(\$null -ne \$rootExitAt -and \$managed\.ActiveProcesses -eq 0\) \{\s*"
             r"\$observed = \$clock\.Elapsed\s*if \(\$observed\.TotalMilliseconds -lt \$deadlineMs\) \{\s*"
             r"\$drained = \$true\s*\$drainAt = \$observed\.TotalSeconds\s*\}\s*break")
    assert re.search(drain, adapter) and adapter.count("$drained = $true") == 1
    confirm = (r"if \(\$managed\.ActiveProcesses -eq 0 -and \$managed\.Wait\(0\)\) "
               r"\{ \$stopConfirmed = \$true; break \}")
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


def test_whoami_path_comes_from_the_os_system_directory_never_the_environment(adapter):
    # T1 trust path (cp3, RCO1 finding): an environment variable such as SystemRoot can point whoami.exe at another
    # file that fakes the label, so the path comes from the OS system directory and is verified before it runs.
    for word in ("systemroot", "windir", "$env:path", "get-command whoami"):
        assert word not in adapter.lower(), word
    assert adapter.count("[Environment]::SystemDirectory") == 1
    assert adapter.count("$whoami = ") == 1
    for text in ("$systemDir = [Environment]::SystemDirectory",
                 "[IO.Path]::GetFullPath($systemDir) -cne $systemDir",
                 "$whoami = [IO.Path]::Combine($systemDir, 'whoami.exe')",
                 "$whoamiInfo = New-Object IO.FileInfo($whoami)"):
        assert adapter.count(text) == 1, text
    assert re.search(r"\$systemDir -cnotmatch '\^\[A-Za-z\]:\\\\", adapter)
    assert re.search(r"\[IO\.Path\]::GetFullPath\(\$systemDir\) -cne \$systemDir\) \{\s*throw", adapter)
    assert re.search(r"if \(-not \$whoamiInfo\.Exists -or\s+\(\(\$whoamiInfo\.Attributes -band "
                     r"\[IO\.FileAttributes\]::ReparsePoint\) -ne 0\)\) \{\s*throw", adapter)
    assert re.search(r"for \(\$dir = \$whoamiInfo\.Directory; \$null -ne \$dir; \$dir = \$dir\.Parent\) \{\s*"
                     r"if \(\(\$dir\.Attributes -band \[IO\.FileAttributes\]::ReparsePoint\) -ne 0\) \{\s*throw",
                     adapter)
    source = adapter.index("$systemDir = [Environment]::SystemDirectory")
    chain = adapter.index("for ($dir = $whoamiInfo.Directory;")
    assert source < chain < adapter.index("& $whoami '/groups'")


def test_root_command_forwards_the_wrapper_exit_code_and_quotes_every_data_argument(adapter):
    # cp4 (Tools PS5 and Root PS7 finding): -File turns the wrapper's published exit code into 0, so the caller runs
    # the wrapper through -Command with a literal exit-forwarding shim, as the wrapper's own help asks.
    assert "'-File'" not in adapter
    assert ("$arguments = [string[]]@('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', "
            "$rootCommand)") in adapter
    assert "$RootExitShim = '; if ($LASTEXITCODE -is [int]) { exit $LASTEXITCODE }; exit 255'" in adapter
    body = _function(adapter, "ConvertTo-WdShadowCommandLiteral")
    assert "[Management.Automation.Language.CodeGeneration]::EscapeSingleQuotedStringContent($Value)" in body
    assert re.search(r"if \(\$Value -match '\[\"\\x00-\\x1F\]'\) \{\s*throw", body)
    assert "return \"'\" + " in body
    # PR1916 P1-A and P1-B: the validated wrapper, code root and bin paths, and the real -VerifyPackage switch.
    assert re.search(r"\$rootCommand = \$RootRunIdPrefix \+ \(ConvertTo-WdShadowCommandLiteral -Value \$launchId\) "
                     r"\+ \$rootWitness \+ '; & ' \+\s*\(ConvertTo-WdShadowCommandLiteral -Value \$wrapperPath\) "
                     r"\+ ' -VerifyPackage ' \+ \(\(@\(\s*'sandbox/mind/mind_capture_producer\.py', '--src', "
                     r"\$codeRootPath, '--bin', \$binPath, '--live-dir', \$liveFull,\s*"
                     r"'--window', \(\$CaptureWindow\.ToString\(\[Globalization\.CultureInfo\]::InvariantCulture\)\),\s*"
                     r"'--workers', \(\$CaptureWorkers\.ToString\(\[Globalization\.CultureInfo\]::InvariantCulture\)\),\s*"
                     r"'--cycles', \(\$CaptureCycles\.ToString\(\[Globalization\.CultureInfo\]::InvariantCulture\)\),\s*"
                     r"'--interval', \(\$CaptureIntervalSeconds\.ToString\(\[Globalization\.CultureInfo\]::InvariantCulture\)\)\) "
                     r"\|\s*ForEach-Object \{ "
                     r"ConvertTo-WdShadowCommandLiteral -Value \$_ \}\) -join ' '\) \+ \$RootExitShim", adapter)
    intent = adapter.index("Publish-WdShadowRecord -Directory $runDir -Name 'run_intent'")
    assert adapter.index("$rootCommand = ") < intent


def test_child_command_carries_the_run_id_as_metadata_and_refuses_an_inherited_one(adapter):
    # cp5 (Lead): the actual launch id reaches the root only through its own command as WD_MIND_SHADOW_RUN_ID; an
    # inherited value is refused before run_intent here and with exit 254 in the child, never reused. This process's
    # environment is only read.
    assert adapter.count("$RootRunIdPrefix = 'if ($null -ne $env:WD_MIND_SHADOW_RUN_ID) { exit 254 }; "
                         "$env:WD_MIND_SHADOW_RUN_ID = '") == 1
    check = re.search(r"if \(\$null -ne \[Environment\]::GetEnvironmentVariable\('WD_MIND_SHADOW_RUN_ID', "
                      r"'Process'\)\) \{\s*throw '", adapter)
    assert check
    # cp5 hardening (holes named by one Grok advisory, candidates and GO by Lead; each form was first shown by a named
    # mutant passing the old checks): one prefix assignment in any letter case or scope, no SetEnvironmentVariable or
    # one-line Set-Item env: in any letter case, and the parent check at column 0 outside every column-0 function
    # span and not under a line ending in '{'. A finite grammar of this file's layout only: braced, indirect,
    # continued or commented variants are declared limits, not an AST or security proof.
    assert len(re.findall(r"(?i)\$(?:script:|global:|local:|private:)?RootRunIdPrefix\s*[-+*/%]?=", adapter)) == 1
    assert not re.search(r"(?i)SetEnvironmentVariable|\bSet-Item\b[^\n]*\benv:", adapter)
    assert not re.search(r"(?m)^\s*\$env:\w+\s*=", adapter)
    top = re.search(r"(?m)^if \(\$null -ne \[Environment\]::GetEnvironmentVariable\('WD_MIND_SHADOW_RUN_ID', "
                    r"'Process'\)\) \{\n    throw '[^'\n]+'\n\}$", adapter)
    assert top and top.start() == check.start()
    for function in re.finditer(r"(?m)^function [\w-]+ \{$", adapter):
        end = re.search(r"\n\}(?:\n|\Z)", adapter[function.start():])
        assert end and not function.start() < top.start() < function.start() + end.end()
    assert not [line for line in adapter[:top.start()].split("\n") if line.strip()][-1].rstrip().endswith("{")
    assert adapter.count("WD_MIND_SHADOW_RUN_ID") == 3
    launch = adapter.index("$launchId = [guid]::NewGuid().ToString('D')")
    command = adapter.index("$rootCommand = $RootRunIdPrefix + (ConvertTo-WdShadowCommandLiteral -Value $launchId)")
    intent = adapter.index("Publish-WdShadowRecord -Directory $runDir -Name 'run_intent'")
    assert check.start() < launch < command < intent
    assert "job_drained = [ordered]@{" in adapter and "=[ordered]" not in adapter


def _joined_literal(text, name):
    """The value of a constant built only from single-quoted pieces joined by + (no code, no expansion)."""
    start = text.index("$" + name + " = ")
    lines = []
    for line in text[start:].split("\n"):
        lines.append(line)
        if not line.rstrip().endswith("+"):
            break
    body = "\n".join(lines)[len("$" + name + " = "):]
    assert re.fullmatch(r"'(?:[^']|'')*'(?:\s*\+\s*'(?:[^']|'')*')*\s*", body), name
    return "".join(piece.replace("''", "'") for piece in re.findall(r"'((?:[^']|'')*)'", body))


def test_root_witness_prologue_holds_a_detached_exclusive_token_then_publishes_one_start_record(adapter):
    # Shadow root witness (Lead decision v1): after the run id assignment and before the unchanged wrapper, the root's
    # own fixed prologue opens a fixed token under the fresh run directory (CreateNew, FileShare.None, never
    # inheritable), writes and flushes it, marks its SafeFileHandle invalid so only OS teardown releases the raw handle
    # (no later stream call), and only then publishes one no-overwrite root_member_start record; any error exits 253
    # before the wrapper. A job root witness only (never members, death or PASS); text checks, not native evidence.
    assert len(re.findall(r"(?i)\$(?:script:|global:|local:|private:)?RootWitnessPrologue\s*[-+*/%]?=", adapter)) == 1
    prologue = _joined_literal(adapter, "RootWitnessPrologue")
    assert prologue.startswith("; try { ") and prologue.endswith(" } catch { exit 253 }")
    token = prologue.index("$wdRootTokenStream = [IO.FileStream]::new($wdRootTokenPath, [IO.FileMode]::CreateNew, "
                           "[IO.FileAccess]::Write, [IO.FileShare]::None); ")
    data = prologue.index("$wdRootBytes = [Convert]::FromBase64String($wdRootTokenData); ")
    write = prologue.index("$wdRootTokenStream.Write($wdRootBytes, 0, $wdRootBytes.Length); ")
    flush = prologue.index("$wdRootTokenStream.Flush($true); ")
    detach = prologue.index("$wdRootTokenStream.SafeFileHandle.SetHandleAsInvalid(); ")
    start = prologue.index("$wdRootStartStream = [IO.FileStream]::new($wdRootStartTemporary, [IO.FileMode]::CreateNew, "
                           "[IO.FileAccess]::Write, [IO.FileShare]::None); try { "
                           "$wdRootBytes = [Convert]::FromBase64String($wdRootStartData); "
                           "$wdRootStartStream.Write($wdRootBytes, 0, $wdRootBytes.Length); "
                           "$wdRootStartStream.Flush($true) } finally { $wdRootStartStream.Dispose() }; ")
    move = prologue.index("[IO.File]::Move($wdRootStartTemporary, $wdRootStartPath) } catch { exit 253 }")
    assert token < data < write < flush < detach < start < move
    assert "$wdRootTokenStream" not in prologue[detach + len("$wdRootTokenStream.SafeFileHandle.SetHandleAsInvalid()"):]
    assert prologue.count("$wdRootTokenStream") == 4 and prologue.count("exit ") == 1
    assert not re.search(r"(?i)Inheritable|Duplicate|Close\(|Delete|Remove-Item|Copy\(|Open\(|OpenOrCreate", prologue)
    assert adapter.count("SetHandleAsInvalid") == 1
    assert not re.search(r"(?i)OpenProcess|OpenJobObject|DuplicateHandle|Inheritable", adapter)
    for name, leaf in (("rootTokenPath", "root.token"), ("rootStartPath", "root_member_start.json"),
                       ("rootStartTemporary", ".root_member_start.json.tmp")):
        assert adapter.count("$" + name + " = ") == 1
        assert ("$" + name + " = Join-Path $runDir '" + leaf + "'") in adapter
    assert adapter.count("$rootTokenPath") == 2
    assert adapter.count("root.token") == 2  # the token name only in its fixed path and start record (no parent use)
    assert re.search(r"\$rootTokenData = ConvertTo-WdShadowBase64Json -Record \(\[ordered\]@\{\s*schema = "
                     r"'wd\.mind-shadow-root-token\.v1'; run_id = \$launchId; authority = 'none'\s*\}\)", adapter)
    assert re.search(r"\$rootStartData = ConvertTo-WdShadowBase64Json -Record \(\[ordered\]@\{\s*schema = "
                     r"'wd\.mind-shadow-root-member-start\.v1'; record = 'root_member_start'; authority = 'none'\s*"
                     r"binding = \[ordered\]@\{\s*run_id = \$launchId; source_commit = \$ExpectedSourceCommit; "
                     r"manifest_sha256 = \$ExpectedManifestSha256\s*\}\s*token = 'root\.token'\s*\}\)", adapter)
    body = _function(adapter, "ConvertTo-WdShadowBase64Json")
    assert "$json = $Record | ConvertTo-Json -Compress -Depth 4" in body
    assert re.search(r"if \(\$json -cnotmatch '\^\[\\x20-\\x7E\]\+\$'\) \{\s*throw", body)
    assert "[Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes($json + \"`n\"))" in body
    witness = re.search(r"\$rootWitness = '; \$wdRootTokenPath = ' \+ \(ConvertTo-WdShadowCommandLiteral -Value "
                        r"\$rootTokenPath\) \+\s*'; \$wdRootTokenData = ' \+ \(ConvertTo-WdShadowCommandLiteral -Value "
                        r"\$rootTokenData\) \+\s*'; \$wdRootStartTemporary = ' \+ \(ConvertTo-WdShadowCommandLiteral "
                        r"-Value \$rootStartTemporary\) \+\s*'; \$wdRootStartPath = ' \+ "
                        r"\(ConvertTo-WdShadowCommandLiteral -Value \$rootStartPath\) \+\s*'; \$wdRootStartData = ' \+ "
                        r"\(ConvertTo-WdShadowCommandLiteral -Value \$rootStartData\) \+ \$RootWitnessPrologue\n",
                        adapter)
    assert witness and adapter.count("$rootWitness = ") == 1
    assert re.search(r"\$rootCommand = \$RootRunIdPrefix \+ \(ConvertTo-WdShadowCommandLiteral -Value \$launchId\) "
                     r"\+ \$rootWitness \+ '; & '", adapter)
    run_dir = adapter.index("[void](New-Item -ItemType Directory -Path $runDir)")
    intent = adapter.index("Publish-WdShadowRecord -Directory $runDir -Name 'run_intent'")
    for name in ("$rootTokenPath = ", "$rootTokenData = ", "$rootStartData = ", "$rootWitness = ", "$rootCommand = "):
        assert run_dir < adapter.index(name) < intent
    assert adapter.count("expected_members") == adapter.count("expected_members = @()") == 1


def test_root_exit_code_is_raw_evidence_read_after_an_observed_exit_before_dispose(adapter):
    # cp4: the true managed ExitCode after a positive root-exit observation and before Dispose; a failed query stays
    # UNKNOWN (never 0); the containment outcome never reads it and nothing infers producer success.
    assert "$Schema = 'wd.mind-shadow-run-outcome.v3'" in adapter
    assert adapter.count("$managed.ExitCode") == 1 and adapter.count("$rootExitCode = 'UNKNOWN'") == 1
    assert re.search(r"if \(\$null -ne \$rootExitAt\) \{\s*try \{\s*\$rootExitCode = \$managed\.ExitCode\s*\} "
                     r"catch \{\s*Add-WdShadowUnknown \$unknown 'root_exit_code'", adapter)
    assert adapter.index("$rootExitCode = $managed.ExitCode") < adapter.index("$managed.Dispose()")
    assert adapter.index("$rootExitCode = 'UNKNOWN'") < adapter.index("[WdManagedTurnProcess]::new(")
    assert "root_exit_code = $rootExitCode" in adapter and "$null -eq $managedType.GetProperty('ExitCode')" in adapter
    rule = _function(adapter, "Resolve-WdShadowOutcome")
    assert "xitCode" not in rule and "exit_code" not in rule
    for word in ("success", "succeeded", "'PASS'", "pass ="):
        assert word not in adapter.lower(), word


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


def test_guards_are_fixed_once_and_refuse_before_the_first_write(adapter):
    # Guards (Lead Source-GO 2026-10-21, after the operator's shadow-run decision): Lead's deployment disk floor
    # (542e26be) on C:\ always and on the run root and live dir volumes, a reviewed bound that refuses when unknown,
    # and one fixed off switch name in the approved run root; each refuses before the run dir, the first write.
    assert re.findall(r"\$DiskFloorBytes = \[long\](\d+)", adapter) == [str(3 * 2 ** 30)]
    assert re.findall(r"\$OffSwitchName = '([^']*)'", adapter) == ["mind-shadow.off"]
    for name, count in (("DiskFloorBytes", 1), ("OffSwitchName", 1), ("offSwitchPath", 1), ("guardRoots", 1),
                        ("ExpectedPeakAdditionalBytes", 0)):
        assigned = re.findall(r"(?i)\$(?:script:|global:|local:|private:)?" + name + r"\s*[-+*/%]?=", adapter)
        assert len(assigned) == count, name
    # More optional parameters follow; the reviewed peak bound is still a
    # mandatory long without a default. Its zero/default refusal and every
    # guard-before-write check below stay unchanged.
    assert re.search(r"\[Parameter\(Mandatory\)\] \[long\] \$ExpectedPeakAdditionalBytes,\n"
                     r"\s*\[ValidateRange\(1, 99500\)\] \[int\] \$CaptureWindow = 3000,", adapter)
    assert "$offSwitchPath = Join-Path $runRootFull $OffSwitchName" in adapter
    assert re.search(r"\$guardRoots = @\(@\('C:\\', \[IO\.Path\]::GetPathRoot\(\$runRootFull\), "
                     r"\[IO\.Path\]::GetPathRoot\(\$liveFull\)\) \|\s*ForEach-Object \{ \$_\.ToUpperInvariant\(\) \} "
                     r"\| Select-Object -Unique\)", adapter)
    switch = re.search(r"if \(Test-WdShadowOffSwitch -Path \$offSwitchPath\) \{\s*throw ", adapter)
    bound = re.search(r"if \(\$ExpectedPeakAdditionalBytes -le 0\) \{\s*throw ", adapter)
    budget = re.search(r"\$shortfall = Get-WdShadowDiskShortfall -Roots \$guardRoots -AdditionalBytes "
                       r"\$ExpectedPeakAdditionalBytes\nif \(\$null -ne \$shortfall\) \{ throw ", adapter)
    assert switch and bound and budget
    validated = adapter.index("if (-not (Test-Path -LiteralPath $runRootFull -PathType Container))")
    first_write = adapter.index("[void](New-Item -ItemType Directory -Path $runDir)")
    assert validated < switch.start() < bound.start() < budget.start() < first_write
    assert adapter.count("New-Item") == 1 and budget.start() < adapter.index("[guid]::NewGuid()")
    assert re.search(r"guard = \[ordered\]@\{\s*disk_floor_bytes = \$DiskFloorBytes; expected_peak_additional_bytes = "
                     r"\$ExpectedPeakAdditionalBytes\s*volumes = @\(\$guardRoots\); off_switch = \$offSwitchPath\s*\}",
                     adapter)


def test_disk_shortfall_is_leads_deployment_rule_and_fails_closed(adapter):
    body = _function(adapter, "Get-WdShadowDiskShortfall")
    assert "if ($free -lt $DiskFloorBytes -or $AdditionalBytes -gt ($free - $DiskFloorBytes)) {" in body
    assert "$free = [long]$drive.AvailableFreeSpace" in body and "$drive = New-Object IO.DriveInfo($root)" in body
    assert re.search(r"if \(-not \$drive\.IsReady -or \$drive\.DriveType -ne \[IO\.DriveType\]::Fixed\) \{\s*"
                     r"return \"", body)
    assert re.search(r"\} catch \{\s*return \"\$root is unmeasured ", body)
    assert body.count("return $null") == 1 and body.rstrip().endswith("return $null\n}")
    floor = "if ($null -ne (Get-WdShadowDiskShortfall -Roots $Roots -AdditionalBytes 0)) { return 'disk_floor' }"
    assert floor in _function(adapter, "Get-WdShadowGuardStop")


def test_off_switch_reads_existence_only_and_counts_any_doubt_as_on(adapter):
    body = _function(adapter, "Test-WdShadowOffSwitch")
    assert "if (-not [IO.Directory]::Exists([IO.Path]::GetDirectoryName($Path))) { return $true }" in body
    assert "[void][IO.File]::GetAttributes($Path)" in body
    assert re.search(r"\} catch \[IO\.FileNotFoundException\] \{\s*return \$false\s*\} "
                     r"catch \{\s*return \$true\s*\}\n\}", body)
    assert body.count("return $false") == 1 and body.count("return $true") == 3
    for word in ("Get-Content", "ReadAll", "Open(", "StreamReader", "Remove-", "Delete", "Move(", "New-Item"):
        assert word not in body, word
    guard = _function(adapter, "Get-WdShadowGuardStop")
    switch = guard.index("if (Test-WdShadowOffSwitch -Path $OffSwitchPath) { return 'off_switch' }")
    assert switch < guard.index("Get-WdShadowDiskShortfall")
    assert set(re.findall(r"return '([a-z_]+)'", guard)) == {"off_switch", "disk_floor"}
    assert guard.rstrip().endswith("return $null\n}")


def test_a_guard_hit_in_a_slice_leaves_through_the_one_stop_path(adapter):
    start = adapter.index("while (-not $forceStop -and $clock.ElapsedMilliseconds -lt $deadlineMs) {")
    loop = adapter[start:adapter.index("if (-not $drained) {")]
    assert re.search(r"\{\s*try \{\s*\$guardStop = Get-WdShadowGuardStop -OffSwitchPath \$offSwitchPath -Roots "
                     r"\$guardRoots -Paths \$guardPaths\s*\} catch \{\s*\$guardStop = 'guard_error'\s*"
                     r"Add-WdShadowUnknown \$unknown "
                     r"'guard' ", loop)
    leave = loop.index("if ($null -ne $guardStop) { break }")
    assert loop.index("$guardStop = Get-WdShadowGuardStop") < leave < loop.index("$slice = ")
    # Tools P2 (late drain): the deadline is checked again after the guards, before the wait.
    recheck = loop.index("if ($clock.ElapsedMilliseconds -ge $deadlineMs) { break }")
    assert leave < recheck < loop.index("$slice = ")
    assert loop.index("$slice = ") < loop.index("$managed.Wait($slice)")
    assert adapter.count("Get-WdShadowGuardStop -OffSwitchPath") == 1 and adapter.count("$managed.Stop()") == 1
    assert adapter.count("$guardStop = ") == 3 and adapter.count("$guardStop = $null") == 1
    assert adapter.index("$guardStop = $null") < adapter.index("[WdManagedTurnProcess]::new(")


def test_reparse_or_mount_ancestry_is_refused_before_any_volume_check_or_write(adapter):
    # Lead follow-up 2026-10-21 (Root's static finding at 386517ac): a textual drive root measures the backing
    # volume only when neither the run root nor the live dir passes through a reparse or mount point.
    body = _function(adapter, "Get-WdShadowReparseAncestor")
    assert re.search(r"for \(\$current = \$Path; -not \[string\]::IsNullOrEmpty\(\$current\); "
                     r"\$current = \[IO\.Path\]::GetDirectoryName\(\$current\)\) \{", body)
    assert "$attributes = [IO.File]::GetAttributes($current)" in body
    assert re.search(r"\} catch \[IO\.FileNotFoundException\] \{\s*continue\s*\} "
                     r"catch \[IO\.DirectoryNotFoundException\] \{\s*continue\s*\} "
                     r"catch \{\s*return \"\$current is unreadable ", body)
    reparse = ("if ($attributes.HasFlag([IO.FileAttributes]::ReparsePoint)) "
               "{ return \"$current is a reparse point\" }")
    assert reparse in body
    assert body.count("return $null") == 1 and body.rstrip().endswith("return $null\n}")
    for word in ("Get-Content", "ReadAll", "Open(", "Remove-", "Delete", "Move(", "New-Item", "Set-", "Resolve-"):
        assert word not in body, word
    assert len(re.findall(r"(?i)\$(?:script:|global:|local:|private:)?guardPaths\s*[-+*/%]?=", adapter)) == 1
    assert "$guardPaths = @($runRootFull, $liveFull)" in adapter
    check = re.search(r"foreach \(\$guarded in \$guardPaths\) \{\s*\$reparse = Get-WdShadowReparseAncestor "
                      r"-Path \$guarded\s*if \(\$null -ne \$reparse\) \{ throw \"refused: ", adapter)
    assert check
    validated = adapter.index("if (-not (Test-Path -LiteralPath $runRootFull -PathType Container))")
    ntfs = adapter.index("$drive = New-Object IO.DriveInfo([IO.Path]::GetPathRoot($liveFull))")
    roots = adapter.index("$guardRoots = @(")
    first_write = adapter.index("[void](New-Item -ItemType Directory -Path $runDir)")
    assert validated < check.start() < ntfs < roots < first_write


def test_existence_reads_name_their_not_found_types_and_never_use_the_provider(adapter):
    # Lead follow-up 2026-10-21 (RCO1 static F1, suspected, not observed): the PowerShell provider can report a denied
    # read as not found, so both existence reads use File.GetAttributes and only the named not-found types count.
    assert "ItemNotFoundException" not in adapter and adapter.count("[IO.File]::GetAttributes(") == 2
    switch = _function(adapter, "Test-WdShadowOffSwitch")
    walk = _function(adapter, "Get-WdShadowReparseAncestor")
    for body in (switch, walk):
        assert "Get-Item" not in body and "Test-Path" not in body and body.count("[IO.File]::GetAttributes(") == 1
    assert re.findall(r"catch \[([A-Za-z.]+)\]", switch) == ["IO.FileNotFoundException"]
    assert re.findall(r"catch \[([A-Za-z.]+)\]", walk) == ["IO.FileNotFoundException", "IO.DirectoryNotFoundException"]
    assert walk.count("continue") == 2 and switch.count("return $false") == 1


def test_the_slice_guard_rechecks_both_paths_and_counts_a_reparse_as_a_floor_hit(adapter):
    guard = _function(adapter, "Get-WdShadowGuardStop")
    assert "param([string] $OffSwitchPath, [string[]] $Roots, [string[]] $Paths)" in guard
    recheck = ("foreach ($guarded in $Paths) { if ($null -ne (Get-WdShadowReparseAncestor -Path $guarded)) "
               "{ return 'disk_floor' } }")
    assert recheck in guard
    assert guard.index("return 'off_switch'") < guard.index(recheck) < guard.index("Get-WdShadowDiskShortfall")
    assert adapter.count("-Roots $guardRoots -Paths $guardPaths") == 1
    # The two guarded paths, the B1 wrapper path check (PR1915 follow-up) and the P1-A code root and bin check
    # (PR1916 follow-up).
    assert adapter.count("Get-WdShadowReparseAncestor -Path") == 4
    assert adapter.count("Get-WdShadowReparseAncestor -Path $wrapperPath") == 1


def test_a_guard_stop_is_named_and_needs_what_the_deadline_stop_needs(adapter):
    body = _function(adapter, "Resolve-WdShadowOutcome")
    assert "[string] $GuardStop" in body and "-LockAcquired $lockOk -GuardStop $guardStop -Unknown $unknown" in adapter
    needs = ("$Unknown.Count -eq 0", "$StopCalled", "$StopOk", "$StopConfirmed", "$RootExited", "$LockAcquired")
    for name, guard in (("stopped_by_off_switch", "$GuardStop -ceq 'off_switch'"),
                        ("stopped_at_disk_floor", "$GuardStop -ceq 'disk_floor'"),
                        ("stopped_at_deadline", "-not $GuardStop")):
        condition = body[:body.index("return '" + name + "'")].rsplit("if (", 1)[1]
        for need in needs + (guard,):
            assert need in condition, (name, need)
        assert " -or " not in condition and "-not $StopCalled" not in condition
    completed = body[:body.index("return 'completed'")].rsplit("if (", 1)[1]
    assert "-not $GuardStop" in completed and "-not $StopCalled" in completed
    assert adapter.count("guard_stop = $guardStop") == 1


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
    cases = re.findall(r"'(A[1-7])' = 'NOT_READY'", harness)
    assert sorted(cases) == [f"A{i}" for i in range(1, 8)]


def test_harness_case_evaluators_take_no_supplied_observation_and_never_pass_a_run(harness):
    # Checkpoint 2 (Lead Source-preparation GO): the evaluators are pure, the script takes no observation input, A4 is
    # always NOT_READY (no publication-kill evidence: Lead's v6 correction) and no whole run can PASS until it exists.
    params = re.search(r"param\((.*?)\n\)", harness, flags=re.S).group(1)
    assert re.findall(r"\$(\w+)", params) == ["OptInNativeAcceptance"]
    case = _function(harness, "Get-WdShadowCaseVerdict")
    assert "if ($CaseId -ceq 'A4') { return 'NOT_READY' }" in case
    assert case.index("return 'NOT_READY'") < case.index("$required = @{")
    run = _function(harness, "Get-WdShadowRunVerdict")
    assert "$a4TimingPointSupported = $false" in run
    assert len(re.findall(r"(?i)\$a4TimingPointSupported\s*=", harness)) == 1
    assert run.index("if (-not $a4TimingPointSupported -or $values -ccontains 'NOT_READY') { return 'NOT_READY' }") \
        < run.rindex("return 'PASS'")
    for name in ("Get-WdShadowCaseVerdict", "Get-WdShadowA7CaseVerdict", "Get-WdShadowSubcaseVerdict",
                 "Get-WdShadowStopVerdict", "Get-WdShadowRefusalVerdict", "Get-WdShadowRunVerdict",
                 "Test-WdShadowExactKeys"):
        body = _function(harness, name)
        for word in ("Start-", "Stop-Process", "Get-Process", "Get-Cim", "Invoke-", "New-Object", "[IO.", "$env:",
                     "$script:", "$global:"):
            assert word not in body, (name, word)


def test_harness_a7_drivers_use_injected_seams_and_never_kill_or_run_by_default(harness):
    # Checkpoint 3 (Lead A7 driver GO) with Lead's v6 decision: the uncontained real child and lister seams are
    # disabled (they refuse before any process exists); the driver core reaches the system only through its injected
    # seams and never turns an empty listing into no-start evidence; only the two structural lane-loop sub-cases have
    # no plan; the script still ends in its refusal on every invocation.
    for name in ("Start-WdShadowA7Child", "Get-WdShadowA7ChildProcesses"):
        seam = _function(harness, name)
        assert re.search(r"param\([^)]*\)\n    throw 'refused: [^']+'\n\}\n$", seam), name
        for word in ("Process]::Start", "ProcessStartInfo", "WaitForExit", "Get-CimInstance", "Kill", "Stop-Process",
                     "taskkill", "Start-Process"):
            assert word not in seam, (name, word)
    driver = _function(harness, "Invoke-WdShadowA7Driver")
    for word in ("Start-WdShadowA7Child", "Get-WdShadowA7ChildProcesses", "New-WdShadowA7CaseRoot", "New-Item",
                 "Get-Cim", "$env:", "$global:", "$script:"):
        assert word not in driver, word
    for need in ("& $Prepare $plan", "& $RunChild ", "& $ListChildren "):
        assert need in driver, need
    assert not re.search(r"process_started\s*=\s*\(?\s*\$false", driver)
    assert "process_started = ($children.Count -gt 0)" not in driver
    plan = _function(harness, "Get-WdShadowA7Plan")
    assert plan.count("return $null") == 1
    assert "'lane_loop_zero_functions', 'lane_loop_two_functions'" in plan
    assert harness.rstrip().endswith("exit 3")
    # Lead's v5 delta and v6 decision: the native A7 set holds both T1 variants (integrity and user) and the seven
    # reachable guards; the two loop sub-cases are structural fixture coverage only, never a native sub-case.
    a7 = _function(harness, "Get-WdShadowA7CaseVerdict")
    native = re.findall(r"'(\w+)'", re.search(r"\$native = @\((.*?)\)", a7, flags=re.S).group(1))
    assert native[:2] == ["integrity_mismatch", "user_sid_mismatch"] and len(native) == 7
    assert not any(name.startswith("lane_loop_") for name in native)
    assert r"'^wd\.mind-shadow-a7-prestart-binding\.v1\z'" in a7
    for word in ("launch_id", "root_pid"):
        assert word not in a7, word
    subcase = _function(harness, "Get-WdShadowSubcaseVerdict")
    assert "user_sid_mismatch" in subcase and "lane_loop_" not in subcase
    coverage = dict(re.findall(r"(\w+) = '(native_prestart|structural_fixture_only)'",
                               _function(harness, "Get-WdShadowA7Coverage")))
    assert sorted(name for name, scope in coverage.items() if scope == "native_prestart") == sorted(native)
    assert sorted(name for name, scope in coverage.items() if scope == "structural_fixture_only") == [
        "lane_loop_two_functions", "lane_loop_zero_functions"]
    # Lead's v6 A4 correction: A4 is the publication replacement kill; no before-resume wording remains.
    assert "before-resume" not in HARNESS.read_text(encoding="utf-8")


def test_a7_loop_count_cases_are_structural_fixture_only(adapter):
    # Lead's v6 decision: under the immutable production manifest the zero- and two-function loops cannot reach the
    # count guard (the loop bytes are checked against the manifest pin first), so their coverage is this structural
    # check of the adapter's own count guard, never a native PASS.
    guard = ('if ($matched.Count -ne 1) { throw "refused: expected one Initialize-WdNativeTurnType, found '
             '$($matched.Count)" }')
    assert adapter.count(guard) == 1
    assert adapter.index("the lane loop bytes do not match the manifest") < adapter.index(guard)
    assert adapter.index("$loopBytes = [IO.File]::ReadAllBytes($loopPath)") < adapter.index(guard)


def test_the_wrapper_pin_is_the_exact_bundle_root_file_with_manifest_bound_bytes(adapter):
    # PR1915 follow-up B1 (bot P1 at L435, RCO1 P1-b, RCO2 B1; Lead Source-GO): containment and the file name are not
    # enough for the file the root executes; the pin must be exactly the bundle-root wrapper, its path free of
    # reparse points, and its bytes must hash to the manifest's own pin (the lane loop pattern) before any write.
    assert len(re.findall(r"(?i)\$wrapperPath\s*[-+*/%]?=", adapter)) == 1
    exact = re.search(r"\$wrapperPath = \[IO\.Path\]::GetFullPath\(\(Join-Path \$bundle \$WrapperName\)\)\s*"
                      r"\$wrapperSelected = \[IO\.Path\]::GetFullPath\(\$pins\['WD_BRIDGE_PYTHON_WRAPPER'\]\)\s*"
                      r"if \(-not \[string\]::Equals\(\$wrapperSelected, \$wrapperPath, "
                      r"\[StringComparison\]::OrdinalIgnoreCase\)\) \{\s*throw \"refused: ", adapter)
    reparse = re.search(r"\$wrapperReparse = Get-WdShadowReparseAncestor -Path \$wrapperPath\s*"
                        r"if \(\$null -ne \$wrapperReparse\) \{ throw \"refused: ", adapter)
    pin = re.search(r"\$wrapperPin = \$manifest\.files\.PSObject\.Properties\[\$WrapperName\]\s*"
                    r"if \(\$null -eq \$wrapperPin -or \[string\]\$wrapperPin\.Value -cnotmatch "
                    r"'\^\[0-9A-Fa-f\]\{64\}\$'\) \{\s*throw \"refused: ", adapter)
    hashed = re.search(r"if \(\(Read-WdShadowFileSnapshot -Path \$wrapperPath\)\.Hash -cne "
                       r"\(\[string\]\$wrapperPin\.Value\)\.ToUpperInvariant\(\)\) \{\s*throw 'refused: ", adapter)
    assert exact and reparse and pin and hashed
    anchored = adapter.index("if ($manifestSnapshot.Hash -cne $ExpectedManifestSha256)")
    first_write = adapter.index("[void](New-Item -ItemType Directory -Path $runDir)")
    assert anchored < exact.start() < reparse.start() < pin.start() < hashed.start() < first_write
    assert hashed.start() < adapter.index("[WdManagedTurnProcess]::new(")
    assert adapter.count("WD_BRIDGE_PYTHON_WRAPPER = [string]$env:WD_BRIDGE_PYTHON_WRAPPER") == 1


def test_code_root_and_bin_pins_are_the_exact_bundle_paths_and_the_root_uses_them(adapter):
    # PR1916 follow-ups P1-A and P1-B (bot, RCO1 and RCO2 concurring; Lead Source-GO): the producer gets --src and
    # --bin from these pins, so each must be exactly the bundle path with no reparse point, the root uses only the
    # validated paths, and the wrapper gets its real -VerifyPackage switch. tests/tools/
    # test_wd_mind_shadow_bundle_binding.py runs these statements and the generated command on a fake bundle.
    exact = re.search(r"\$codeRootPath = \[IO\.Path\]::GetFullPath\(\(Join-Path \$bundle 'tools-bootstrap'\)\)\s*"
                      r"\$binPath = \[IO\.Path\]::GetFullPath\(\(Join-Path \$codeRootPath '\.agent-bridge\\bin'\)\)",
                      adapter)
    assert exact
    for text in ("if (-not [string]::Equals([IO.Path]::GetFullPath($pins[$name]), $expected, "
                 "[StringComparison]::OrdinalIgnoreCase)) {",
                 "if (-not (Test-Path -LiteralPath $expected -PathType Container)) {",
                 "$pinReparse = Get-WdShadowReparseAncestor -Path $expected"):
        assert adapter.count(text) == 1, text
    for name in ("$env:WD_BRIDGE_CODE_ROOT", "$env:WD_BRIDGE_BIN", "$env:WD_BRIDGE_PYTHON_WRAPPER"):
        assert adapter.count(name) == 1, name
    assert adapter.count("-VerifyPackage") == 1 and "' -VerifyPackage ' + " in adapter
    first_write = adapter.index("[void](New-Item -ItemType Directory -Path $runDir)")
    assert exact.start() < first_write < adapter.index("$rootCommand = ")
    assert "[WdManagedTurnProcess]::new($hostExe, $arguments, $codeRootPath, $inputPath," in adapter


def test_a_volume_root_run_root_or_live_dir_is_refused_before_the_separator_is_trimmed(adapter):
    # PR1915 follow-up B2 (bot P1 at L463, RCO1 P1-a, RCO2 B2; Lead Source-GO): a trimmed 'C:\' is 'C:', a
    # drive-relative path, so a volume root is refused before any trim; every other path keeps its root.
    body = _function(adapter, "Resolve-WdShadowDirectoryPath")
    full = body.index("$full = [IO.Path]::GetFullPath($Value)")
    root = body.index("$root = [IO.Path]::GetPathRoot($full)")
    root_check = body.index("[string]::Equals($full, $root, [StringComparison]::OrdinalIgnoreCase)")
    trim = body.index("$trimmed = $full.TrimEnd('\\')")
    assert full < root < root_check < trim
    assert re.search(r"OrdinalIgnoreCase\)\) \{\s*throw \"refused: \$Name is a volume root", body)
    assert re.search(r"if \(\$trimmed\.Length -le \$root\.Length -or -not \$trimmed\.StartsWith\(\$root, "
                     r"\[StringComparison\]::OrdinalIgnoreCase\)\) \{\s*throw \"refused: ", body)
    assert body.count("return $trimmed") == 1 and body.rstrip().endswith("return $trimmed\n}")
    for name, value in (("runRootFull", "ApprovedRunRoot"), ("liveFull", "LiveDir")):
        assigned = re.findall(r"(?i)\$" + name + r"\s*[-+*/%]?=[^\n]*", adapter)
        assert assigned == [f"${name} = Resolve-WdShadowDirectoryPath -Name '{value}' -Value ${value}"], name
    trims = [line.strip() for line in adapter.splitlines() if "TrimEnd(" in line]
    assert sorted(trims) == sorted(["$bundlePrefix = [IO.Path]::GetFullPath($bundle).TrimEnd('\\') + '\\'",
                                    "$trimmed = $full.TrimEnd('\\')"])
    resolved = adapter.index("$liveFull = Resolve-WdShadowDirectoryPath")
    assert resolved < adapter.index("if (-not (Test-Path -LiteralPath $runRootFull -PathType Container))")


MEMBER = ROOT / "tests" / "windows" / "wd_mind_shadow_acceptance_member.ps1"


def test_harness_lifetime_drivers_sequence_the_job_through_seams_and_the_real_job_seams_are_disabled(harness):
    # Lead's v7 GO: actual A1/A2/A3/A5/A6 driver bodies (launch, wait, stop, observation) on injected seams; the real
    # job, root-identity, context, controller-kill and handle seams refuse until the pinned owned-job API is reconciled
    # once; the script still ends in its refusal and never calls a driver.
    seams = _function(harness, "Get-WdShadowLifetimeSeams")
    for name in ("NewManaged", "RootCreation", "Context", "KillController", "InheritedHandles"):
        assert re.search(name + r" = \{\n? *param\([^)]*\)\n? *throw 'refused: [^']*disabled[^']*'\n? *\}", seams), name
    for word in ("Process]::Start", "ProcessStartInfo", "GetProcessById", ".Kill", "Stop-Process", "taskkill",
                 "Start-Process", "Start-Sleep"):
        assert word not in seams, word
    driver = _function(harness, "Invoke-WdShadowLifetimeDriver")
    for word in ("Get-WdShadowLifetimeSeams", "Process]::", "Get-Cim", "Start-Sleep", "$env:", "$global:", "$script:",
                 ".Kill", "New-Item"):
        assert word not in driver, word
    for need in ("& $Seams['NewManaged'] $plan", "$managed.Stop()", "$managed.ActiveProcesses",
                 "$managed.TotalProcesses", "& $Seams['KillController'] $managed", "$managed.Dispose()",
                 "& $Seams['InheritedHandles'] $managed", "Get-WdShadowMemberObservation"):
        assert need in driver, need
    # Normal and exceptional cleanup have two textual sites, not two stop attempts.
    # The raw fallback is guarded by the actual prior-attempt flag; owned cleanup
    # uses the facade's shared latch, including when the first Stop failed.
    assert driver.count("$managed.Stop()") == 2
    assert "$driverStopAttempted = $false" in driver
    assert "$driverStopAttempted = $true\n            try { $managed.Stop();" in driver
    assert "elseif (-not $driverStopAttempted) { $managed.Stop() }" in driver
    assert "$managed.StopForExceptionalCleanup()" in driver
    assert driver.index("$managed.StopForExceptionalCleanup()") < driver.index("$managed.Dispose()")
    facade = _function(harness, "New-WdShadowOwnedStopOnce")
    cleanup = facade[facade.index("-Name StopForExceptionalCleanup"):facade.index("-Name Wait")]
    assert cleanup.index("if ([int]$this.StopRecord['inner_calls'] -ne 0) { return }") < \
        cleanup.index("try { $this.Stop() }")
    assert "driver_exception_cleanup" in facade
    assert driver.index("& $Seams['NewManaged'] $plan") < driver.index("$managed.Stop()") < driver.index(
        "$managed.Dispose()")
    compose = _function(harness, "ConvertTo-WdShadowLifetimeCaseObservation")
    assert "'wd.mind-shadow-lifetime-subcase-binding.v1'" in compose
    # Lead's v8: A1 and A3 take the versioned per-sub-case binding; the old one-launch binding never reaches them.
    case = _function(harness, "Get-WdShadowCaseVerdict")
    assert case.index("Get-WdShadowLifetimeSubcaseCaseVerdict") < case.index("$required = @{")
    # The future owned wiring: admission before any write or load, Root's runner loaded only from its pinned path,
    # and the unsafe lifetime seams kept refused.
    for name in ("Invoke-WdShadowA7OwnedAttempt", "New-WdShadowOwnedLifetimeSeams"):
        body = _function(harness, name)
        assert body.index("if (-not $ApprovedNativeExecution)") < body.index("Test-WdShadowOwnedAdmission") < \
            body.index("$Files['runner']"), name
        for word in ("Process]::", "Get-Cim", "Start-Sleep", "$env:", ".Kill", "New-Item"):
            assert word not in body, (name, word)
    owned = _function(harness, "New-WdShadowOwnedLifetimeSeams")
    for seam in ("Context", "KillController", "InheritedHandles"):
        assert f"$seams['{seam}'] =" not in owned, seam
    tail = harness[harness.rindex("\n}\n"):]
    assert "Invoke-WdShadow" not in tail and harness.rstrip().endswith("exit 3")


def test_member_fixture_refuses_by_default_and_uses_only_preregistered_members(harness):
    assert MEMBER.is_file(), MEMBER
    text = _code(MEMBER)
    lines = [line for line in text.splitlines() if line.strip()]
    first = next(i for i, line in enumerate(lines) if not line.lstrip().startswith(("param", "[", ")", "#requires")))
    assert "OptInSyntheticMember" in lines[first] and "WD_MIND_SHADOW_SYNTHETIC_MEMBER" in lines[first]
    assert "exit 3" in " ".join(lines[first:first + 4])
    # One registry: the member refuses any name, sub-case or tree the harness drivers do not preregister.
    assert _function(text, "Get-WdShadowLifetimeMembers") == _function(harness, "Get-WdShadowLifetimeMembers")
    assert text.count("Process]::Start(") == 1
    assert "Process]::Start(" in _function(text, "Start-WdShadowMemberProcess")
    for word in ("Invoke-WebRequest", "Invoke-RestMethod", "WebClient", "Net.Sockets", "HttpClient", "events.jsonl",
                 ".agent-bridge", "Add-Type", "Start-Process", "Invoke-Expression", "Remove-Item", ".Kill",
                 "Stop-Process", "DllImport", "F:\\", "mind_capture_producer"):
        assert word not in text, word
    witness = _function(text, "Open-WdShadowMemberWitness")
    assert "[IO.FileMode]::CreateNew" in witness and "[IO.FileShare]::None" in witness
    run = _function(text, "Test-WdShadowMemberRun")
    for need in ("'.codex-audit'", "members.registration", "Get-WdShadowLifetimeMembers"):
        assert need in run, need
