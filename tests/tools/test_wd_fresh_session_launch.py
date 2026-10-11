"""An operator-explicit fresh Claude session supersedes exactly one recorded conversation.

The default launch keeps resuming the newest named lane conversation. With
-FreshSession the launcher runs every resume check first, then refuses unless
-SupersededThreadId is exactly the conversation a default launch would resume,
starts the CLI without --resume and with the full startup prompt, and records
the superseded thread and the reason. Each check below runs the real launcher
code (the whole script for parameter validation, the exact source slices for the
decision, prompt, re-bind and DryRun paths) under Windows PowerShell and
PowerShell 7, and every named mutant must be caught by the same check.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from test_wd_conversation_resume import CURRENT, FOREIGN, OLD, transcript
from test_wd_reboot_bundle import (
    LANE_TEST_SHELLS,
    POWERSHELL,
    REBOOT,
    WINDOWS_POWERSHELL,
    _run_powershell,
)
from test_wd_startup_recovery import load, q

SOURCE = REBOOT / "start-wd-agent.ps1"
AGENT = "claude-rco-2"
NEWER = "44444444-4444-4444-8444-444444444444"
LETTERED = "abcdef01-2345-4678-89ab-cdef01234567"
MISSING_MANIFEST = "fleet manifest is missing"
BAD_ARGS = "FreshSession requires the exact lowercase superseded thread id"
UNPAIRED = "superseded thread and fresh-session reason require -FreshSession"
EXCLUSIVE = "FreshSession cannot be combined with manual Lead recovery or managed admission probing"
MUTANT_SHELL = WINDOWS_POWERSHELL or POWERSHELL

pytestmark = pytest.mark.skipif(not LANE_TEST_SHELLS, reason="PowerShell is unavailable")


def _between(source_path: Path, start: str, end: str) -> str:
    text = source_path.read_text(encoding="utf-8")
    first = text.index(start)
    return text[first:text.index(end, first)]


def _json(script: str, ps: str):
    return json.loads(_run_powershell(script, executable=ps).stdout)


# ---------------------------------------------------------------- validation

VALIDATION_CASES = {
    "valid": f"FreshSession=$true; SupersededThreadId='{CURRENT}'; FreshSessionReason='new-session canary'",
    "superseded_without_switch": f"SupersededThreadId='{CURRENT}'",
    "empty_superseded_without_switch": "SupersededThreadId=''",
    "reason_without_switch": "FreshSessionReason='why'",
    "missing_superseded": "FreshSession=$true; FreshSessionReason='why'",
    "lowercase_lettered_superseded": f"FreshSession=$true; SupersededThreadId='{LETTERED}'; FreshSessionReason='why'",
    "uppercase_superseded": f"FreshSession=$true; SupersededThreadId='{LETTERED.upper()}'; FreshSessionReason='why'",
    "newline_after_superseded": f"FreshSession=$true; SupersededThreadId=('{CURRENT}' + [char]10); FreshSessionReason='why'",
    "missing_reason": f"FreshSession=$true; SupersededThreadId='{CURRENT}'",
    "blank_reason": f"FreshSession=$true; SupersededThreadId='{CURRENT}'; FreshSessionReason='   '",
    "long_reason": f"FreshSession=$true; SupersededThreadId='{CURRENT}'; FreshSessionReason=('x' * 513)",
    "max_reason": f"FreshSession=$true; SupersededThreadId='{CURRENT}'; FreshSessionReason=('x' * 512)",
    "bell_reason": f"FreshSession=$true; SupersededThreadId='{CURRENT}'; FreshSessionReason=('a' + [char]7)",
    "c1_reason": f"FreshSession=$true; SupersededThreadId='{CURRENT}'; FreshSessionReason=('a' + [char]0x85)",
    "line_separator_reason": f"FreshSession=$true; SupersededThreadId='{CURRENT}'; FreshSessionReason=('a' + [char]0x2028)",
    "with_recover_interactive": f"FreshSession=$true; SupersededThreadId='{CURRENT}'; FreshSessionReason='why'; RecoverInteractive=$true",
    "with_retire": (f"FreshSession=$true; SupersededThreadId='{CURRENT}'; FreshSessionReason='why'; "
                    "RetireManagedAttempt=$true; ReviewedJournalDigest=('a' * 64); RetirementReason='retire'"),
    "with_admission_probe": f"FreshSession=$true; SupersededThreadId='{CURRENT}'; FreshSessionReason='why'; CheckManagedAdmission=$true",
    "default_launch": "",
}
EXPECTED_VALIDATION = {
    "valid": MISSING_MANIFEST,
    "superseded_without_switch": UNPAIRED,
    "empty_superseded_without_switch": UNPAIRED,
    "reason_without_switch": UNPAIRED,
    "missing_superseded": BAD_ARGS,
    "lowercase_lettered_superseded": MISSING_MANIFEST,
    "uppercase_superseded": BAD_ARGS,
    "newline_after_superseded": BAD_ARGS,
    "missing_reason": BAD_ARGS,
    "blank_reason": BAD_ARGS,
    "long_reason": BAD_ARGS,
    "max_reason": MISSING_MANIFEST,
    "bell_reason": BAD_ARGS,
    "c1_reason": BAD_ARGS,
    "line_separator_reason": BAD_ARGS,
    "with_recover_interactive": EXCLUSIVE,
    "with_retire": EXCLUSIVE,
    "with_admission_probe": EXCLUSIVE,
    "default_launch": MISSING_MANIFEST,
}


def _validation(source_path: Path, ps: str, tmp_path: Path) -> dict[str, str]:
    """Run the whole launcher once per case; a missing manifest proves validation passed."""
    missing = tmp_path / "missing" / "wd-fleet.json"
    rows = []
    for name, extra in VALIDATION_CASES.items():
        rows.append(
            f"$p=@{{Agent='{AGENT}'; DryRun=$true; ManifestPath={q(missing)}; {extra}}}\n"
            f"try {{ & {q(source_path)} @p | Out-Null; $r['{name}']='launched' }} "
            f"catch {{ $r['{name}']=$_.Exception.Message }}\n"
        )
    script = "$r=[ordered]@{}\n" + "".join(rows) + "$r | ConvertTo-Json -Compress\n"
    observed = _json(script, ps)
    return {
        name: next((label for label in (MISSING_MANIFEST, BAD_ARGS, UNPAIRED, EXCLUSIVE)
                    if label in message), message)
        for name, message in observed.items()
    }


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_fresh_session_arguments_are_validated_before_any_lane_work(tmp_path, ps):
    assert _validation(SOURCE, ps, tmp_path) == EXPECTED_VALIDATION


# ------------------------------------------------------------ exact binding

BINDING_CASES = {
    # case: (superseded, transcript layout)
    "exact": (CURRENT, "two"),
    "older": (OLD, "two"),
    "foreign_newer": (FOREIGN, "foreign"),
    "own_beside_foreign": (CURRENT, "foreign"),
    "no_history": (CURRENT, "none"),
    "unnamed_history": (CURRENT, "unnamed"),
    "conflicting_header": (CURRENT, "wrong_cwd"),
    "ambiguous_latest": (CURRENT, "tie"),
    "not_a_claude_lane": (CURRENT, "not_claude"),
}
EXPECTED_BINDING = {
    "exact": {"ok": True, "thread_id": "", "superseded": CURRENT, "initial": False},
    "older": "is not the conversation this launch would resume",
    "foreign_newer": "is not the conversation this launch would resume",
    "own_beside_foreign": {"ok": True, "thread_id": "", "superseded": CURRENT, "initial": False},
    "no_history": "no recorded named conversation to supersede",
    "unnamed_history": "refusing a silent fresh start",
    "conflicting_header": "incomplete or conflicting session header",
    "ambiguous_latest": "Ambiguous latest named Claude conversation",
    "not_a_claude_lane": "applies only to an interactive Claude lane",
}


def _layout(root: Path, worktree: Path, kind: str) -> None:
    if kind in ("none", "not_claude"):
        return
    if kind == "unnamed":
        transcript(root, worktree, FOREIGN, "claude-rco-1", "2026-09-16T00:00:00Z")
        return
    transcript(root, worktree, OLD, AGENT, "2026-09-14T00:00:00Z")
    transcript(root, worktree, CURRENT, AGENT, "2026-09-15T00:00:00Z",
               cwd="elsewhere" if kind == "wrong_cwd" else None)
    if kind == "foreign":
        transcript(root, worktree, FOREIGN, "claude-rco-1", "2026-09-16T00:00:00Z")
    if kind == "tie":
        transcript(root, worktree, NEWER, AGENT, "2026-09-15T00:00:00Z")


def _binding(source_path: Path, ps: str, tmp_path: Path) -> dict:
    script = "$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n"
    for name in ("Assert-LanePathWithoutReparse", "Get-WdClaudeResumeState", "Resolve-WdClaudeFreshSession"):
        script += load(source_path, name)
    script += "$r=[ordered]@{}\n"
    before = {}
    for case, (superseded, kind) in BINDING_CASES.items():
        worktree = tmp_path / case / "worktree"
        worktree.mkdir(parents=True)
        root = tmp_path / case / "projects"
        root.mkdir()
        _layout(root, worktree, kind)
        before.update({p: p.read_bytes() for p in root.rglob("*.jsonl")})
        state = ("$null" if kind == "not_claude" else
                 f"(Get-WdClaudeResumeState -Agent {AGENT} -Worktree {q(worktree)} -ProjectsRoot {q(root)})")
        script += f"""
try {{
  $f=Resolve-WdClaudeFreshSession -ResumeState {state} -SupersededThreadId '{superseded}'
  $r['{case}']=[ordered]@{{ok=$true; thread_id=[string]$f.thread_id; superseded=[string]$f.superseded_thread_id; initial=[bool]$f.initial_context_delivered}}
}} catch {{ $r['{case}']=$_.Exception.Message }}
"""
    script += "$r | ConvertTo-Json -Compress -Depth 4\n"
    observed = _json(script, ps)
    assert {p: p.read_bytes() for p in before} == before, "resume inspection changed a transcript"
    return {
        case: value if isinstance(value, dict) else next(
            (label for label in EXPECTED_BINDING.values() if isinstance(label, str) and label in value), value)
        for case, value in observed.items()
    }


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_fresh_session_supersedes_only_the_exact_conversation_a_resume_would_pick(tmp_path, ps):
    assert _binding(SOURCE, ps, tmp_path) == EXPECTED_BINDING


# ------------------------------------------------ launch decision and prompt

def _launch(source_path: Path, ps: str, tmp_path: Path) -> dict:
    """Real selection, prompt and CLI-argument slices for a fresh and a default launch."""
    worktree = tmp_path / "worktree"
    worktree.mkdir(parents=True)
    claude_home = tmp_path / "claude"
    root = claude_home / "projects"
    root.mkdir(parents=True)
    _layout(root, worktree, "two")
    settings = tmp_path / "wd-claude-event-driven-settings.json"
    settings.write_bytes((REBOOT / settings.name).read_bytes())
    pin = hashlib.sha256(settings.read_bytes()).hexdigest().upper()
    text = source_path.read_text(encoding="utf-8")
    launch_start = text.rindex("$launchArguments = @()")
    launch = text[launch_start:text.index("$previousPreference = $ErrorActionPreference", launch_start)]
    selection = _between(source_path, "$supersededThread = ''", "if ($script:WdScrubbedClaudeMarkers.Count)")
    prompt = _between(source_path, "if ($FreshSession) {\n  # Only the validated", "if (-not $HandshakeDirectory)")
    script = "$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\nfunction Write-Host { param($Object) }\n"
    for name in ("Assert-LanePathWithoutReparse", "Get-WdClaudeResumeState", "Resolve-WdClaudeFreshSession"):
        script += load(source_path, name)
    script += f"""
function Invoke-Launch([bool] $Fresh) {{
  $FreshSession=$Fresh; $SupersededThreadId='{CURRENT}'; $FreshSessionReason='reason & "quoted" | text'
  $PSScriptRoot={q(tmp_path)}; $laneTrustedDrive={q(tmp_path)}
  $deploymentAnchor=[pscustomobject]@{{files=[pscustomobject]@{{'wd-claude-event-driven-settings.json'='{pin}'}}}}
  $cliName='claude.cmd'; $Agent='{AGENT}'; $model='sonnet'; $effort='max'; $autoCompactTokens=$null
  $startupPrompt='VISUAL+FULL'; $continuationPrompt='CONTINUE'
{prompt}
  $nativeResume=$null
  $claudeResume=Get-WdClaudeResumeState -Agent $Agent -Worktree {q(worktree)} -ProjectsRoot {q(root)}
{selection}
{launch}
  [ordered]@{{args=@($launchArguments); resume=$resumeThread; superseded=$supersededThread}}
}}
[ordered]@{{fresh=(Invoke-Launch $true); default=(Invoke-Launch $false)}} | ConvertTo-Json -Compress -Depth 5
"""
    return _json(script, ps)


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_fresh_launch_starts_new_conversation_with_full_prompt_and_default_still_resumes(tmp_path, ps):
    observed = _launch(SOURCE, ps, tmp_path)
    fresh, default = observed["fresh"], observed["default"]
    assert "--resume" not in fresh["args"] and "--continue" not in fresh["args"]
    assert "--fork-session" not in fresh["args"]
    assert fresh["args"][0] == "--settings"
    assert fresh["args"][-3:-1] == ["--name", AGENT]
    assert fresh["args"][-1] == (
        "VISUAL+FULL OPERATOR-EXPLICIT FRESH SESSION: the previous lane conversation "
        f"{CURRENT} was deliberately not resumed. Rebuild context from the compact lane state, "
        "the live bridge and the repository before resuming any task; do not assume facts from "
        "that conversation."
    )
    assert "reason" not in fresh["args"][-1]
    assert (fresh["resume"], fresh["superseded"]) == ("", CURRENT)
    assert default["args"][:2] == ["--resume", CURRENT]
    assert default["args"][-1] == "CONTINUE"
    assert (default["resume"], default["superseded"]) == (CURRENT, "")


def test_operator_reason_never_reaches_the_cli_and_note_follows_the_continuation_split():
    text = SOURCE.read_text(encoding="utf-8")
    prompt_start = text.index("$startupPrompt = (")
    prompt_end = text.index("if (-not $HandshakeDirectory)")
    launch_start = text.rindex("$launchArguments = @()")
    launch_end = text.index("$previousPreference = $ErrorActionPreference", launch_start)
    assert "$FreshSessionReason" not in text[prompt_start:prompt_end]
    assert "$FreshSessionReason" not in text[launch_start:launch_end]
    note = text.index("if ($FreshSession) {\n  # Only the validated")
    assert text.index("$continuationPrompt = $startupPrompt.Substring") < note < prompt_end
    fleet = (REBOOT / "start-wd-all.ps1").read_text(encoding="utf-8")
    assert "FreshSession" not in fleet and "SupersededThreadId" not in fleet


# ------------------------------------------------------ re-bind before launch

def _rebind(source_path: Path, ps: str, tmp_path: Path) -> dict:
    worktree = tmp_path / "worktree"
    worktree.mkdir(parents=True)
    claude_home = tmp_path / "claude"
    root = claude_home / "projects"
    root.mkdir(parents=True)
    _layout(root, worktree, "two")
    # Everything between the recovery re-check and the CLI arguments, so a mutated guard still runs.
    recovery = "throw 'managed attempt evidence changed before interactive recovery launch'\n  }\n}\n"
    block = _between(source_path, recovery, "$launchArguments = @()")[len(recovery):]
    script = "$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n"
    for name in ("Assert-LanePathWithoutReparse", "Get-WdClaudeResumeState", "Resolve-WdClaudeFreshSession"):
        script += load(source_path, name)
    script += f"""
function Invoke-Rebind {{
  $FreshSession=$true; $supersededThread='{CURRENT}'; $Agent='{AGENT}'
  $worktree={q(worktree)}; $claudeHome={q(claude_home)}
  try {{
{block}
    'ok'
  }} catch {{ $_.Exception.Message }}
}}
ConvertTo-Json -InputObject ([string](Invoke-Rebind)) -Compress
"""
    stable = _json(script, ps)
    # A newer conversation of the same lane appearing during bootstrap must stop the launch.
    transcript(root, worktree, NEWER, AGENT, "2026-09-17T00:00:00Z")
    return {"stable": stable, "changed": _json(script, ps)}


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_fresh_session_rebinds_the_superseded_thread_just_before_the_cli_starts(tmp_path, ps):
    observed = _rebind(SOURCE, ps, tmp_path)
    assert observed["stable"] == "ok"
    assert "is not the conversation this launch would resume" in observed["changed"]
    text = SOURCE.read_text(encoding="utf-8")
    rebind = text.index("if ($FreshSession) {\n  # Re-bind after bootstrap")
    assert text.index("managed attempt evidence changed before interactive recovery launch") < rebind
    assert rebind < text.rindex("$launchArguments = @()")
    assert text.index("Assert-LaneBootstrapIntegrity") < rebind


# ----------------------------------------------------- recorded fresh state

def _dry_run(source_path: Path, ps: str) -> dict:
    script = f"""
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile({q(source_path)},[ref]$tokens,[ref]$errors)
$dry=$ast.Find({{param($n) $n -is [Management.Automation.Language.IfStatementAst] -and $n.Clauses[0].Item1.Extent.Text -eq '$DryRun'}},$true)
if (-not $dry) {{ throw 'missing DryRun branch' }}
function Write-Host {{ param($Object) }}
function Invoke-Dry([bool] $Fresh) {{
  $DryRun=$true; $turnMode='interactive'; $CheckManagedAdmission=$false; $Agent='{AGENT}'
  $FreshSession=$Fresh; $resumeThread=$(if ($Fresh) {{ '' }} else {{ '{CURRENT}' }})
  $supersededThread=$(if ($Fresh) {{ '{CURRENT}' }} else {{ '' }})
  $FreshSessionReason=$(if ($Fresh) {{ 'new-session canary' }} else {{ '' }})
  $manualAttemptEvidence=$null; $manualAttemptLease=$null
  $r=& ([scriptblock]::Create($dry.Extent.Text))
  [ordered]@{{native=$r.native_thread_id; fresh=$r.fresh_session; superseded=$r.superseded_thread_id; reason=$r.fresh_session_reason}}
}}
[ordered]@{{fresh=(Invoke-Dry $true); default=(Invoke-Dry $false)}} | ConvertTo-Json -Compress -Depth 4
"""
    return _json(script, ps)


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_fresh_session_is_recorded_in_dry_run_handshake_and_target_event(ps):
    assert _dry_run(SOURCE, ps) == {
        "fresh": {"native": "", "fresh": True, "superseded": CURRENT, "reason": "new-session canary"},
        "default": {"native": CURRENT, "fresh": False, "superseded": "", "reason": ""},
    }
    handshake = _between(SOURCE, "$handshake = [ordered]@{", "created_at_utc =")
    assert "  native_thread_id = $resumeThread\n" in handshake
    for line in ("fresh_session = [bool]$FreshSession", "superseded_thread_id = $supersededThread",
                 "fresh_session_reason = $FreshSessionReason"):
        assert f"  {line}\n" in handshake
    target = _between(SOURCE, "$targetPayload = [ordered]@{", "} | ConvertTo-Json -Compress")
    assert "  fresh_session = [bool]$FreshSession\n" in target
    assert "  superseded_thread_id = $supersededThread\n" in target
    assert "FreshSessionReason" not in target


# ------------------------------------------------ generated wrapper forwarding

@pytest.mark.skipif(WINDOWS_POWERSHELL is None, reason="Windows PowerShell is unavailable")
def test_generated_agent_wrappers_forward_fresh_session_only_when_bound(tmp_path):
    target = tmp_path / "agent-target.ps1"
    target.write_text(
        "[CmdletBinding()]\n"
        "param([Parameter(Mandatory)] [string] $Agent, [switch] $DryRun, [switch] $FreshSession,\n"
        "  [string] $SupersededThreadId = '', [string] $FreshSessionReason = '')\n"
        "[pscustomobject]@{ agent = $Agent; bound = (@($PSBoundParameters.Keys | Sort-Object) -join ',');\n"
        "  superseded = $SupersededThreadId; reason = $FreshSessionReason }\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "deployment-manifest.json"
    manifest.write_text('{"schema_version":1}\n', encoding="utf-8")
    final_commit = "f" * 40
    fixed = tmp_path / "fixed-wrapper.ps1"
    open_ = tmp_path / "open-wrapper.ps1"
    reason = "new-session canary; it's operator text"
    script = f"""
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $env:SystemRoot (
  'System32\\WindowsPowerShell\\v1.0\\Modules\\Microsoft.PowerShell.Utility\\Microsoft.PowerShell.Utility.psd1'
)) -Force -ErrorAction Stop
{load(REBOOT / 'Deploy-WdRebootBundle.ps1', 'New-ForwardingWrapper')}
$utf8 = New-Object Text.UTF8Encoding($false)
$manifestHash = (Get-FileHash -LiteralPath {q(manifest)} -Algorithm SHA256).Hash
$pointer = [ordered]@{{ source_commit = '{final_commit}'; final_commit = '{final_commit}'
  manifest_sha256 = $manifestHash; final_manifest_sha256 = $manifestHash; active_bundle = {q(tmp_path)} }}
[IO.File]::WriteAllBytes({q(tmp_path / 'WD_REBOOT_STATE_CURRENT.json')}, $utf8.GetBytes([string]($pointer | ConvertTo-Json -Compress)))
$targetHash = (Get-FileHash -LiteralPath {q(target)} -Algorithm SHA256).Hash
foreach ($spec in @(@({q(fixed)}, '{AGENT}'), @({q(open_)}, ''))) {{
  $text = New-ForwardingWrapper -Target {q(target)} -ExpectedHash $targetHash -ExpectedManifestHash $manifestHash `
    -WrapperKind agent -FixedAgent $spec[1] -ExpectedFinalCommit '{final_commit}' -ExpectedFinalManifestHash $manifestHash
  [IO.File]::WriteAllBytes($spec[0], $utf8.GetBytes([string]$text))
}}
$reason = {q(reason)}
[ordered]@{{
  fixed_fresh = & {q(fixed)} -FreshSession -SupersededThreadId '{CURRENT}' -FreshSessionReason $reason -DryRun
  fixed_default = & {q(fixed)} -DryRun
  open_fresh = & {q(open_)} -Agent claude-rco-1 -FreshSession -SupersededThreadId '{CURRENT}' -FreshSessionReason $reason
}} | ConvertTo-Json -Compress -Depth 4
"""
    observed = _json(script, WINDOWS_POWERSHELL)
    assert observed["fixed_fresh"] == {
        "agent": AGENT, "bound": "Agent,DryRun,FreshSession,FreshSessionReason,SupersededThreadId",
        "superseded": CURRENT, "reason": reason,
    }
    assert observed["fixed_default"] == {"agent": AGENT, "bound": "Agent,DryRun", "superseded": "", "reason": ""}
    assert observed["open_fresh"] == {
        "agent": "claude-rco-1", "bound": "Agent,FreshSession,FreshSessionReason,SupersededThreadId",
        "superseded": CURRENT, "reason": reason,
    }


# ------------------------------------------------------------- named mutants

MUTANTS = {
    "MUT_FRESH_BINDING_SKIPPED": (
        "  if ($current -cne $SupersededThreadId) {",
        "  if ($false) {",
        "binding",
    ),
    "MUT_FRESH_EMPTY_HISTORY_ALLOWED": (
        "  if (-not $current) {",
        "  if ($false) {",
        "binding",
    ),
    "MUT_FRESH_KEEPS_RESUME": (
        "return [pscustomobject]@{thread_id=''; initial_context_delivered=$false; superseded_thread_id=$current}",
        "return [pscustomobject]@{thread_id=$current; initial_context_delivered=$true; superseded_thread_id=$current}",
        "launch",
    ),
    "MUT_FRESH_SWITCH_NOT_CONSULTED": (
        "$supersededThread = ''\nif ($FreshSession) {",
        "$supersededThread = ''\nif ($false) {",
        "launch",
    ),
    "MUT_FRESH_REBIND_REMOVED": (
        "if ($FreshSession) {\n  # Re-bind after bootstrap",
        "if ($false) {\n  # Re-bind after bootstrap",
        "rebind",
    ),
    "MUT_FRESH_UUID_END_ANCHOR": (
        "(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\\z'",
        "(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$'",
        "validation",
    ),
    "MUT_FRESH_UUID_CASE_FOLDED": (
        "$SupersededThreadId -cnotmatch '^[0-9a-f]{8}",
        "$SupersededThreadId -notmatch '^[0-9a-f]{8}",
        "validation",
    ),
    "MUT_FRESH_C1_REASON_ALLOWED": (
        "$FreshSessionReason -match '[\\x00-\\x1F\\x7F-\\x9F\\u2028\\u2029]'",
        "$FreshSessionReason -match '[\\x00-\\x1F\\x7F]'",
        "validation",
    ),
    "MUT_FRESH_RECOVERY_NOT_EXCLUSIVE": (
        "if ($FreshSession -and ($RecoverInteractive -or $RetireManagedAttempt -or $CheckManagedAdmission)) {",
        "if ($FreshSession -and ($RetireManagedAttempt -or $CheckManagedAdmission)) {",
        "validation",
    ),
    "MUT_FRESH_UNPAIRED_ARGUMENTS_ALLOWED": (
        "    $PSBoundParameters.ContainsKey('SupersededThreadId') -or\n",
        "    $false -or\n",
        "validation",
    ),
    "MUT_FRESH_DRY_RUN_UNRECORDED": (
        "      superseded_thread_id = $supersededThread\n",
        "      superseded_thread_id = ''\n",
        "dry_run",
    ),
}
CHECKS = {
    "validation": (_validation, lambda: EXPECTED_VALIDATION),
    "binding": (_binding, lambda: EXPECTED_BINDING),
    "launch": (_launch, None),
    "rebind": (_rebind, None),
    "dry_run": (lambda path, ps, _tmp: _dry_run(path, ps), None),
}


def _mutated_copy(tmp_path: Path, old: str, new: str) -> Path:
    raw = SOURCE.read_bytes().decode("utf-8")
    newline = "\r\n" if "\r\n" in raw else "\n"
    old, new = old.replace("\n", newline), new.replace("\n", newline)
    assert raw.count(old) == 1, f"mutant anchor is not unique: {old!r}"
    path = tmp_path / "mutant" / SOURCE.name
    path.parent.mkdir()
    path.write_bytes(raw.replace(old, new).encode("utf-8"))
    return path


@pytest.mark.skipif(MUTANT_SHELL is None, reason="PowerShell is unavailable")
@pytest.mark.parametrize("mutant", sorted(MUTANTS))
def test_each_named_mutant_is_caught_by_its_check(tmp_path, mutant):
    old, new, check = MUTANTS[mutant]
    run, expected = CHECKS[check]
    baseline = expected() if expected else run(SOURCE, MUTANT_SHELL, tmp_path / "real")
    mutated = run(_mutated_copy(tmp_path, old, new), MUTANT_SHELL, tmp_path / "mutated")
    assert mutated != baseline, f"{mutant} survived the {check} check"
