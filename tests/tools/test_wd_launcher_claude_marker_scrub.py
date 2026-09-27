"""Lane launchers scrub inherited Claude session markers before starting anything (claude-rco-1 root cause, 2026-09-27)."""
import json
import re
from pathlib import Path

import pytest

from test_wd_reboot_bundle import REBOOT, LANE_TEST_SHELLS, _run_powershell
from test_wd_startup_recovery import load

LAUNCHERS = [REBOOT / "start-wd-agent.ps1", REBOOT / "start-wd-tools-consumer.ps1"]
MARKERS = ["CLAUDECODE", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PID",
           "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_EXECPATH",
           "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN"]
KEEP = ["CLAUDE_CODE_DISABLE_CRON", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_MAX_OUTPUT_TOKENS", "CLAUDE_CONFIG_DIR"]
SECRET = "tok-9f3759-secret-value"


def scrub_script(launcher: Path, present: list[str]) -> str:
    # Hermetic: the runner may itself be a Claude Code tool shell carrying these markers.
    sets = "\n".join([f"Remove-Item -LiteralPath 'Env:{name}' -ErrorAction SilentlyContinue" for name in MARKERS]
                     + [f"$env:{name}='{SECRET}-{name}'" for name in present + KEEP])
    names = ",".join(f"'{name}'" for name in MARKERS)
    keep = ",".join(f"'{name}'" for name in KEEP)
    return load(launcher, "Clear-WdInheritedClaudeSessionMarkers") + f"""
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
{sets}
$removed = @(Clear-WdInheritedClaudeSessionMarkers)
$exe = if (Test-Path (Join-Path $PSHOME 'powershell.exe')) {{ Join-Path $PSHOME 'powershell.exe' }} else {{ Join-Path $PSHOME 'pwsh.exe' }}
$child = & $exe -NoProfile -NonInteractive -Command "@({names}) | Where-Object {{ [Environment]::GetEnvironmentVariable(`$_) }}"
[pscustomobject]@{{
  removed = @($removed)
  still_here = @(@({names}) | Where-Object {{ $null -ne [Environment]::GetEnvironmentVariable($_, 'Process') }})
  child_sees = @($child | Where-Object {{ $_ }})
  kept = @(@({keep}) | Where-Object {{ [Environment]::GetEnvironmentVariable($_, 'Process') -eq ('{SECRET}-' + $_) }})
}} | ConvertTo-Json -Compress
"""


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("launcher", LAUNCHERS, ids=lambda p: p.stem)
def test_every_inherited_marker_is_removed_before_a_child_can_see_it(ps, launcher):
    result = _run_powershell(scrub_script(launcher, MARKERS), executable=ps)
    record = json.loads(result.stdout)
    assert record["removed"] == MARKERS
    assert record["still_here"] == [] and record["child_sees"] == []
    assert record["kept"] == KEEP                          # operator configuration is untouched
    assert SECRET not in result.stdout + result.stderr     # names only, never values


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("launcher", LAUNCHERS, ids=lambda p: p.stem)
def test_a_clean_environment_removes_nothing(ps, launcher):
    record = json.loads(_run_powershell(scrub_script(launcher, []), executable=ps).stdout)
    assert record["removed"] == [] and record["kept"] == KEEP


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_only_the_markers_that_are_present_are_reported(ps):
    record = json.loads(_run_powershell(scrub_script(LAUNCHERS[0], ["CLAUDE_CODE_CHILD_SESSION", "CLAUDE_PID"]),
                                        executable=ps).stdout)
    assert record["removed"] == ["CLAUDE_CODE_CHILD_SESSION", "CLAUDE_PID"]


@pytest.mark.parametrize("launcher", LAUNCHERS, ids=lambda p: p.stem)
def test_the_scrub_runs_first_before_anything_is_started(launcher):
    source = launcher.read_text(encoding="utf-8")
    call = source.index("$script:WdScrubbedClaudeMarkers = @(Clear-WdInheritedClaudeSessionMarkers)")
    head = source.index("Set-StrictMode -Version Latest\n")
    assert head < call
    # Nothing but the scrub function sits between StrictMode and the call.
    between = source[head:call]
    assert "Start-Process" not in between and "& $" not in between and between.count("function ") == 1
    for spawn in (r"Start-Process", r"& \$cliPath", r"Invoke-WdLaneProfileShadowRead -BundleRoot", r"& \$[A-Za-z]*[Pp]ython"):
        for match in re.finditer(spawn, source):
            assert match.start() > call, (launcher.name, spawn)


def test_both_launchers_carry_the_same_scrub():
    def body(path):
        text = path.read_text(encoding="utf-8")
        start = text.index("# A lane must never inherit another Claude Code session's identity.")
        return text[start:text.index("$script:WdScrubbedClaudeMarkers = @(Clear-WdInheritedClaudeSessionMarkers)", start)]
    assert body(LAUNCHERS[0]) == body(LAUNCHERS[1])


def test_the_docs_name_every_scrubbed_marker():
    text = (REBOOT.parents[2] / "docs" / "operations" / "LANE_LAUNCH_ENVIRONMENT.md").read_text(encoding="utf-8")
    for name in MARKERS + ["CLAUDE_CODE_DISABLE_CRON"]:
        assert f"`{name}`" in text, name
