"""Reboot restores the named provider conversation, without account-wide recency."""
import hashlib
import json
import re
from pathlib import Path

import pytest

from test_wd_reboot_bundle import REBOOT, LANE_TEST_SHELLS, _run_powershell
from test_wd_startup_recovery import load, q

OLD = "11111111-1111-4111-8111-111111111111"
CURRENT = "22222222-2222-4222-8222-222222222222"
FOREIGN = "33333333-3333-4333-8333-333333333333"


def transcript(root, worktree, thread, agent, date, *, cwd=None, sidechain=False):
    project = root / re.sub(r"[^a-zA-Z0-9]", "-", str(worktree))
    project.mkdir(parents=True, exist_ok=True)
    path = project / (thread + ".jsonl")
    records = [dict(type="agent-name", agentName=agent, sessionId=thread),
               dict(type="user", cwd=str(cwd or worktree), sessionId=thread,
                    timestamp=date, isSidechain=sidechain)]
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("case", ["resume", "fresh", "foreign", "wrong_cwd", "sidechain", "tie", "truncated"])
def test_claude_recovers_only_its_named_main_conversation(tmp_path, ps, case):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    root = tmp_path / "projects"
    root.mkdir()
    if case != "fresh":
        transcript(root, worktree, OLD, "claude-rco-1", "2026-09-14T00:00:00Z")
        current = transcript(root, worktree, CURRENT, "claude-rco-1", "2026-09-15T00:00:00Z",
                             cwd="elsewhere" if case == "wrong_cwd" else None,
                             sidechain=case == "sidechain")
        if case == "truncated":
            current.write_text(current.read_text().splitlines()[0] + "\n")
        if case == "foreign":
            # Newer activity in the same folder must not select another lane.
            transcript(root, worktree, FOREIGN, "claude-rco-2", "2026-09-16T00:00:00Z")
        if case == "tie":
            transcript(root, worktree, FOREIGN, "claude-rco-1", "2026-09-15T00:00:00Z")
    before = {p: p.read_bytes() for p in root.rglob("*.jsonl")}
    script = "$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n"
    for name in ["Assert-LanePathWithoutReparse", "Get-WdClaudeResumeState"]:
        script += load(REBOOT / "start-wd-agent.ps1", name)
    script += f"""
try {{ $s=Get-WdClaudeResumeState -Agent claude-rco-1 -Worktree {q(worktree)} -ProjectsRoot {q(root)}; @{{ok=$true;state=$s}}|ConvertTo-Json }}
catch {{ @{{ok=$false;error=$_.Exception.Message}}|ConvertTo-Json }}
"""
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    assert result["ok"] is (case in ("resume", "fresh", "foreign")), result
    if result["ok"]:
        assert result["state"]["thread_id"] == ("" if case == "fresh" else CURRENT)
    assert {p: p.read_bytes() for p in before} == before


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_claude_resume_uses_exact_id_and_continuation(ps):
    source = (REBOOT / "start-wd-agent.ps1").read_text(encoding="utf-8")
    start = source.rindex("$launchArguments = @()")
    end = source.index("$previousPreference = $ErrorActionPreference", start)
    script = f"""
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$claudeResume=[pscustomobject]@{{thread_id='{CURRENT}';initial_context_delivered=$true}}
$cliName='claude.cmd'; $Agent='claude-rco-1'; $model='sonnet'; $effort='max'
$startupPrompt='Read image first'; $continuationPrompt='Resume authorized work'
{source[start:end]}
ConvertTo-Json -InputObject $launchArguments
"""
    args = json.loads(_run_powershell(script, executable=ps).stdout)
    assert args[:2] == ["--resume", CURRENT]
    assert args[-1] == "Resume authorized work"
    assert "--continue" not in args and "--fork-session" not in args


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("case", ["anchored", "missing", "duplicate"])
def test_live_wrapper_uses_original_bundle_hash(tmp_path, ps, case):
    bundle = tmp_path / ("a" * 40)
    bundle.mkdir()
    content = b'{"source_commit":"original"}'
    (bundle / "deployment-manifest.json").write_bytes(content)
    anchor = hashlib.sha256(content if case != "missing" else b"unknown").hexdigest()
    if case == "duplicate":
        other = tmp_path / ("b" * 40)
        other.mkdir()
        (other / "deployment-manifest.json").write_bytes(content)
    script = "$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n"
    for name in ["Resolve-NormalizedPath", "Assert-WdFleetPathWithoutReparse", "Get-NamedCommandLineArgumentValue", "Resolve-WdLiveLaneManifest"]:
        script += load(REBOOT / "start-wd-all.ps1", name)
    script += f"""
try {{ $p=Resolve-WdLiveLaneManifest -CommandLine 'powershell -File C:\\Python\\start-wd-agent.ps1 -ExpectedManifestHash {anchor}' -BundleStore {q(tmp_path)}; @{{ok=$true;path=$p}}|ConvertTo-Json }}
catch {{ @{{ok=$false;error=$_.Exception.Message}}|ConvertTo-Json }}
"""
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    assert result["ok"] is (case == "anchored"), result
    if result["ok"]:
        assert Path(result["path"]) == bundle / "wd-fleet.json"


def test_native_continuation_respects_pause_but_does_not_force_idle():
    source = (REBOOT / "start-wd-agent.ps1").read_text(encoding="utf-8")
    assert "then await the operator instruction" not in source
    assert "Resume the latest unfinished operator-authorized task" in source
    assert "Preserve an explicit operator pause or HOLD" in source
