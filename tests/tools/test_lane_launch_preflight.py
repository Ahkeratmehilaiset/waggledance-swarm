# SPDX-License-Identifier: BUSL-1.1
"""Launch preflight (PR-7b): the effective launch profile is checked at launch, alert-only."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

import tools.lane_profile_launch_probe as probe_module
from tools.lane_profile_launch_probe import EXIT_ATTENTION, EXIT_OK, main, preflight

ROOT = Path(__file__).resolve().parents[2]
REBOOT = ROOT / "ops" / "windows" / "reboot"
CATALOG = ROOT / "configs" / "lane_profile_catalog.json"
HOSTS = list(dict.fromkeys(filter(None, [shutil.which("pwsh"), shutil.which("powershell.exe")])))


def codex_config(tmp_path, model, effort) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(f'model = "{model}"\nmodel_reasoning_effort = "{effort}"\n', encoding="utf-8")
    return path


def claude_settings(tmp_path, value) -> Path:
    path = tmp_path / "home" / ".claude" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def missing_managed(tmp_path) -> Path:
    return tmp_path / "no-managed-settings.json"


# ---------------------------------------------------------------- preflight()

@pytest.mark.parametrize("model,effort,verdict", [
    ("gpt-5.6-sol", "medium", "allowed"),
    ("gpt-6-sol", "high", "allowed"),
    ("gpt-6-luna", "low", "not_in_lane_allowlist"),     # the operator's planted fault
    ("gpt-5.6-terra", "medium", "not_in_lane_allowlist"),
])
def test_codex_preflight_verdicts_for_lead(tmp_path, model, effort, verdict):
    result = preflight(CATALOG, "codex-lead-1", "codex", "native", "native",
                       codex_config=codex_config(tmp_path, model, effort))
    assert result["verdict"] == verdict
    assert (result["effective"]["model"], result["effective"]["model_source"]) == (model, "user_config")


def test_claude_preflight_allowed_when_the_profile_is_pinned(tmp_path):
    settings = claude_settings(tmp_path, {"model": "claude-sonnet-5", "effortLevel": "xhigh"})
    result = preflight(CATALOG, "claude-rco-1", "claude", "native", "native", worktree=tmp_path,
                       claude_user_settings=settings, claude_managed_settings=missing_managed(tmp_path), env={})
    assert (result["verdict"], result["profile"]) == ("allowed", "claude-sonnet-5-xhigh")


def test_claude_preflight_on_an_unpinned_default_is_unknown(tmp_path):
    settings = claude_settings(tmp_path, {"effortLevel": "xhigh"})      # today's machine
    result = preflight(CATALOG, "claude-rco-1", "claude", "native", "native", worktree=tmp_path,
                       claude_user_settings=settings, claude_managed_settings=missing_managed(tmp_path), env={})
    assert result["verdict"] == "unknown" and "model_from_unpinned_builtin_default" in result["reasons"]


def test_claude_argv_profile_is_what_counts(tmp_path):
    settings = claude_settings(tmp_path, {"model": "claude-haiku-4-5", "effortLevel": "low"})
    result = preflight(CATALOG, "fable-5", "claude", "claude-opus-5-5", "medium", worktree=tmp_path,
                       claude_user_settings=settings, claude_managed_settings=missing_managed(tmp_path), env={})
    assert (result["verdict"], result["effective"]["model_source"]) == ("allowed", "argv")


def test_codex_argv_profile_is_what_counts(tmp_path):
    result = preflight(CATALOG, "codex-lead-1", "codex", "gpt-6-sol", "high",
                       codex_config=codex_config(tmp_path, "gpt-6-luna", "low"))
    assert (result["verdict"], result["effective"]["model_source"]) == ("allowed", "argv")


def test_an_unknown_cli_is_unknown(tmp_path):
    result = preflight(CATALOG, "codex-lead-1", "grok", "native", "native")
    assert (result["verdict"], result["reasons"]) == ("unknown", ["cli_unknown"])


def test_an_unusable_catalog_is_attention_not_a_pass(tmp_path):
    result = preflight(tmp_path / "none.json", "codex-lead-1", "codex", "native", "native",
                       codex_config=codex_config(tmp_path, "gpt-5.6-sol", "medium"))
    assert result["verdict"] == "unknown" and result["reasons"][0].startswith("preflight_failed:")


def test_a_raising_resolver_is_attention_not_a_crash(tmp_path, monkeypatch):
    import tools.lane_effective_model as eff

    def boom(**kwargs):
        raise RuntimeError("resolver bug")
    monkeypatch.setattr(eff, "resolve_codex", boom)
    result = preflight(CATALOG, "codex-lead-1", "codex", "native", "native")
    assert (result["verdict"], result["reasons"]) == ("unknown", ["preflight_failed:RuntimeError"])


# ---------------------------------------------------------------- probe main(): the only signal is the code

def run_main(tmp_path, capsys, *extra):
    code = main(["--runtime-root", str(tmp_path / "rt"), "--lane", "codex-lead-1", "--launcher", "start-wd-agent",
                 "--argv-model", "native", "--argv-effort", "native", *extra])
    return code, json.loads(capsys.readouterr().out)


def test_without_cli_the_probe_behaves_as_pr4(tmp_path, capsys):
    code, entry = run_main(tmp_path, capsys)
    assert code == EXIT_OK and entry["preflight"] is None and entry["launch"] == "native_argv_unchanged"


def test_an_allowed_launch_exits_zero(tmp_path, capsys):
    cfg = codex_config(tmp_path, "gpt-5.6-sol", "medium")
    code, entry = run_main(tmp_path, capsys, "--cli", "codex", "--codex-config", str(cfg))
    assert code == EXIT_OK and entry["preflight"]["verdict"] == "allowed"


def test_the_planted_luna_low_fault_exits_attention(tmp_path, capsys):
    cfg = codex_config(tmp_path, "gpt-6-luna", "low")
    code, entry = run_main(tmp_path, capsys, "--cli", "codex", "--codex-config", str(cfg))
    assert code == EXIT_ATTENTION and entry["preflight"]["verdict"] == "not_in_lane_allowlist"
    assert entry["launch"] == "native_argv_unchanged"                  # alert only: the launch is not changed
    logged = (tmp_path / "rt" / "lane_profiles" / "launch-shadow.jsonl").read_text(encoding="utf-8")
    assert json.loads(logged.strip().splitlines()[-1])["preflight"]["effective"]["model"] == "gpt-6-luna"


def test_a_failing_preflight_exits_attention(tmp_path, capsys):
    code, entry = run_main(tmp_path, capsys, "--cli", "codex", "--codex-config", str(tmp_path / "absent.toml"))
    assert code == EXIT_ATTENTION and entry["preflight"]["verdict"] == "unknown"


def test_attention_constants_are_the_launcher_contract():
    assert (probe_module.EXIT_OK, probe_module.EXIT_ATTENTION) == (0, 3)
    for script in ("start-wd-agent.ps1", "start-wd-tools-consumer.ps1"):
        body = _function_text(script)
        assert "(Get-WdBridgeCodeLastExitCode) -eq 3" in body


# ---------------------------------------------------------------- the launcher functions

LAUNCHERS = [("start-wd-agent.ps1", "start-wd-agent"), ("start-wd-tools-consumer.ps1", "start-wd-tools-consumer")]


def _function_text(script: str) -> str:
    source = (REBOOT / script).read_text(encoding="utf-8")
    start = source.index("function Invoke-WdLaneProfileShadowRead {")
    return source[start:source.index("\n}\n", start) + 3]


@pytest.mark.parametrize("script,launcher", LAUNCHERS)
def test_the_call_site_passes_the_preflight_inputs(script, launcher):
    source = (REBOOT / script).read_text(encoding="utf-8")
    call = source[source.index(f"-Launcher '{launcher}'"):]
    call = call[:call.index("\n}\n")]
    for flag in ("-Cli ", "-Worktree $worktree", "-Writer $writer", "-RunId ", "-AgentUuid ", "-Role ", "-Capabilities "):
        assert flag in call, flag


def _write_stub_writer(tmp_path, *, fail=False) -> Path:
    log = tmp_path / "writer-calls.jsonl"
    body = "throw [IO.IOException]::new('bridge down')" if fail else (
        "[pscustomobject]@{ agent=$Agent; type=$Type; task=$TaskId; status=$Status; to=$To; message=$Message; "
        "run=$RunId; role=$Role; uuid=$AgentUuid; session=$SessionId; caps=@($Capabilities); payload=$PayloadJson } | "
        f"ConvertTo-Json -Compress | Add-Content -LiteralPath '{log}'; 'writer-output-that-must-not-leak'")
    writer = tmp_path / "Write-AgentEvent.ps1"
    writer.write_text("param($Agent,$Type,$TaskId,$Status,$To,$Message,$RunId,$Role,$AgentUuid,$SessionId,"
                      "[string[]]$Capabilities,$PayloadJson)\n" + body + "\n", encoding="utf-8")
    return writer


def _run_function(tmp_path, host, script, launcher, *, code, writer, cli="codex"):
    probe_script = tmp_path / "probe.ps1"
    probe_script.write_text(f"""
$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest
function Invoke-WdBridgePythonTool {{ param($BundleRoot,$Tool,$ToolArguments) $global:seen = @($Tool) + @($ToolArguments); '{{"launch":"native_argv_unchanged"}}' }}
function Get-WdBridgeCodeLastExitCode {{ {code} }}
{_function_text(script)}
$model='gpt-5.6-sol'; $effort='medium'
$result = @(Invoke-WdLaneProfileShadowRead -BundleRoot 'C:\\b' -RuntimeRoot '{tmp_path}' -Lane 'codex-lead-1' `
  -Launcher '{launcher}' -Model '' -Effort $effort -Cli '{cli}' -Worktree 'C:\\wt' -Writer '{writer or ''}' `
  -Role 'lead' -AgentUuid 'd3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101' -RunId 'run-7' -Capabilities @('bridge_event'))
[pscustomobject]@{{ count = $result.Count; model = $model; effort = $effort; seen = @($global:seen) }} | ConvertTo-Json -Compress
""", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k.upper() != "PSMODULEPATH"}
    result = subprocess.run([host, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                             str(probe_script)], capture_output=True, text=True, timeout=90, env=env)
    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout.strip().splitlines()[-1])
    state["writer_attempted"] = "attention event" in result.stdout
    return state


def _needs_windows(host):
    if host is None or os.name != "nt":
        pytest.skip("Windows PowerShell launcher")


@pytest.mark.parametrize("host", HOSTS or [None])
@pytest.mark.parametrize("script,launcher", LAUNCHERS)
def test_attention_posts_exactly_one_fixed_event_and_leaks_nothing(tmp_path, host, script, launcher):
    _needs_windows(host)
    writer = _write_stub_writer(tmp_path)
    state = _run_function(tmp_path, host, script, launcher, code=3, writer=writer)
    assert state["count"] == 0 and (state["model"], state["effort"]) == ("gpt-5.6-sol", "medium")
    calls = [json.loads(line) for line in (tmp_path / "writer-calls.jsonl").read_text(encoding="utf-8-sig").splitlines()]
    assert len(calls) == 1
    call = calls[0]
    assert (call["agent"], call["type"], call["task"], call["status"]) == \
        ("codex-lead-1", "status", "lane-profile-switching", "launch_preflight_attention")
    assert (call["run"], call["session"], call["uuid"], call["role"]) == \
        ("run-7", "run-7", "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101", "lead")
    payload = json.loads(call["payload"])
    assert payload["enforcement"] == "alert_only" and payload["shadow_log"].endswith("launch-shadow.jsonl")
    seen = state["seen"]
    assert seen[seen.index("--cli") + 1] == "codex" and seen[seen.index("--worktree") + 1] == "C:\\wt"
    assert seen[seen.index("--argv-model") + 1] == "unset"


@pytest.mark.parametrize("host", HOSTS or [None])
@pytest.mark.parametrize("script,launcher", LAUNCHERS)
@pytest.mark.parametrize("code", [0, 1, 2])
def test_no_attention_posts_nothing(tmp_path, host, script, launcher, code):
    _needs_windows(host)
    writer = _write_stub_writer(tmp_path)
    state = _run_function(tmp_path, host, script, launcher, code=code, writer=writer)
    assert state["count"] == 0 and not (tmp_path / "writer-calls.jsonl").exists()


@pytest.mark.parametrize("host", HOSTS or [None])
@pytest.mark.parametrize("script,launcher", LAUNCHERS)
def test_a_failing_writer_never_breaks_the_launch(tmp_path, host, script, launcher):
    _needs_windows(host)
    writer = _write_stub_writer(tmp_path, fail=True)
    state = _run_function(tmp_path, host, script, launcher, code=3, writer=writer)
    assert state["count"] == 0 and (state["model"], state["effort"]) == ("gpt-5.6-sol", "medium")


@pytest.mark.parametrize("host", HOSTS or [None])
@pytest.mark.parametrize("script,launcher", LAUNCHERS)
def test_attention_without_a_writer_posts_nothing(tmp_path, host, script, launcher):
    _needs_windows(host)
    state = _run_function(tmp_path, host, script, launcher, code=3, writer=None)
    assert state["count"] == 0 and not state["writer_attempted"]
