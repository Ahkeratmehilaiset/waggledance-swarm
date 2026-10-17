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
    assert re.search(r"\$rootCommand = \$RootRunIdPrefix \+ \(ConvertTo-WdShadowCommandLiteral -Value \$launchId\) "
                     r"\+ \$rootWitness \+ '; & ' \+ \(\(@\(\s*\$env:WD_BRIDGE_PYTHON_WRAPPER, "
                     r"'sandbox/mind/mind_capture_producer\.py', '--src', \$env:WD_BRIDGE_CODE_ROOT,\s*'--bin', "
                     r"\$env:WD_BRIDGE_BIN, '--live-dir', \$liveFull\) \|\s*ForEach-Object \{ "
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
    assert "$Schema = 'wd.mind-shadow-run-outcome.v2'" in adapter
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
