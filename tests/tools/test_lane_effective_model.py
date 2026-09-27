# SPDX-License-Identifier: BUSL-1.1
"""Effective model/effort resolution and catalog classification (PR-7a): fail closed, never guess."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

import tools.lane_effective_model as eff_module
from tools.lane_effective_model import classify, main, normalize_claude_model, resolve_claude, resolve_codex
from tools.lane_profile_catalog import load_catalog

ROOT = Path(__file__).resolve().parents[2]
CATALOG, DIGEST = load_catalog(ROOT / "configs" / "lane_profile_catalog.json")


def js(path: Path, value) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")
    return path


class Claude:
    """A throwaway home/worktree layout for Claude Code settings."""

    def __init__(self, tmp_path):
        self.user = tmp_path / "home" / ".claude" / "settings.json"
        self.worktree = tmp_path / "wt"
        self.cli = tmp_path / "cli-settings.json"
        self.managed = tmp_path / "managed-settings.json"
        self.worktree.mkdir(parents=True)

    def resolve(self, *, argv_model="native", argv_effort="native", env=None, cli=False, registry=None):
        return resolve_claude(argv_model=argv_model, argv_effort=argv_effort, env=env or {},
                              user_settings=self.user, worktree=self.worktree,
                              cli_settings=self.cli if cli else None, managed_settings=self.managed,
                              managed_registry=registry or (lambda: []))


# ---------------------------------------------------------------- Claude

def test_claude_user_settings_decide_when_nothing_overrides(tmp_path):
    c = Claude(tmp_path)
    js(c.user, {"model": "claude-opus-5-5", "effortLevel": "medium"})
    r = c.resolve()
    assert (r["model"], r["effort"], r["model_source"], r["effort_source"], r["resolved"]) == \
        ("claude-opus-5-5", "medium", "user", "user", True)


def test_claude_argv_beats_env_and_every_file(tmp_path):
    c = Claude(tmp_path)
    js(c.user, {"model": "claude-sonnet-5", "effortLevel": "low"})
    js(c.cli, {"model": "claude-sonnet-5", "effortLevel": "low"})
    r = c.resolve(argv_model="claude-opus-5-5", argv_effort="high", env={"ANTHROPIC_MODEL": "claude-sonnet-5"}, cli=True)
    assert (r["model"], r["effort"], r["model_source"], r["effort_source"]) == ("claude-opus-5-5", "high", "argv", "argv")


def test_anthropic_model_env_beats_every_settings_file_but_not_argv(tmp_path):
    c = Claude(tmp_path)
    js(c.cli, {"model": "claude-sonnet-5", "effortLevel": "low"})
    r = c.resolve(env={"ANTHROPIC_MODEL": "claude-opus-5-5"}, cli=True)
    assert (r["model"], r["model_source"]) == ("claude-opus-5-5", "env:ANTHROPIC_MODEL")


@pytest.mark.parametrize("present,expected", [
    (("cli", "local", "project", "user"), "cli_settings"),
    (("local", "project", "user"), "project_local"),
    (("project", "user"), "project"),
    (("user",), "user"),
])
def test_claude_settings_layer_precedence(tmp_path, present, expected):
    c = Claude(tmp_path)
    paths = {"cli": c.cli, "local": c.worktree / ".claude" / "settings.local.json",
             "project": c.worktree / ".claude" / "settings.json", "user": c.user}
    for layer in present:
        js(paths[layer], {"model": f"claude-{layer}-model", "effortLevel": "high"})
    r = c.resolve(cli="cli" in present)
    assert r["model_source"] == expected == r["effort_source"]
    assert r["model"] == f"claude-{ {'cli_settings': 'cli', 'project_local': 'local'}.get(expected, expected)}-model"


def test_anthropic_default_model_only_when_no_file_sets_model(tmp_path):
    c = Claude(tmp_path)
    js(c.user, {"effortLevel": "high"})
    assert c.resolve(env={"ANTHROPIC_DEFAULT_MODEL": "claude-opus-5-5"})["model_source"] == "env:ANTHROPIC_DEFAULT_MODEL"
    js(c.user, {"model": "claude-sonnet-5", "effortLevel": "high"})
    assert c.resolve(env={"ANTHROPIC_DEFAULT_MODEL": "claude-opus-5-5"})["model"] == "claude-sonnet-5"


def test_the_builtin_default_model_is_never_guessed(tmp_path):
    c = Claude(tmp_path)
    js(c.user, {"effortLevel": "xhigh"})                      # today's machine: no model key anywhere
    r = c.resolve()
    assert (r["model"], r["model_source"], r["resolved"]) == (None, "builtin_default", False)
    assert "model_from_unpinned_builtin_default" in r["issues"]


@pytest.mark.parametrize("value,expected", [
    ("claude-opus-5-5[1m]", ("claude-opus-5-5", None)), ("claude-sonnet-5", ("claude-sonnet-5", None)),
    ("opus", (None, "model_alias_unresolved:opus")), ("Sonnet[1m]", (None, "model_alias_unresolved:Sonnet")),
    ("default", (None, "model_alias_unresolved:default")), ("", (None, "model_missing")), (5, (None, "model_missing")),
])
def test_claude_model_normalization(value, expected):
    assert normalize_claude_model(value) == expected


def test_per_model_effort_is_used_for_the_resolved_model(tmp_path):
    c = Claude(tmp_path)
    js(c.user, {"model": "claude-opus-5-5", "modelSettings": {"claude-opus-5-5": {"effortLevel": "high"},
                                                               "claude-sonnet-5": {"effortLevel": "low"}}})
    assert c.resolve()["effort"] == "high"


def test_per_model_and_global_effort_that_disagree_are_ambiguous(tmp_path):
    c = Claude(tmp_path)
    js(c.user, {"model": "claude-opus-5-5", "effortLevel": "xhigh",
                "modelSettings": {"claude-opus-5-5": {"effortLevel": "medium"}}})
    r = c.resolve()
    assert (r["effort"], r["resolved"]) == (None, False) and "effort_ambiguous_in_user" in r["issues"]


def test_per_model_and_global_effort_that_agree_resolve(tmp_path):
    c = Claude(tmp_path)
    js(c.user, {"model": "claude-opus-5-5", "effortLevel": "xhigh",
                "modelSettings": {"claude-opus-5-5": {"effortLevel": "xhigh"}}})
    assert (c.resolve()["effort"], c.resolve()["resolved"]) == ("xhigh", True)


def test_effort_auto_is_the_model_tuned_default(tmp_path):
    c = Claude(tmp_path)
    js(c.user, {"model": "claude-opus-5-5", "effortLevel": "auto"})
    r = c.resolve()
    assert r["effort"] is None and "effort_auto_is_model_tuned_default" in r["issues"]


def test_managed_settings_fail_closed(tmp_path):
    c = Claude(tmp_path)
    js(c.user, {"model": "claude-opus-5-5", "effortLevel": "high"})
    js(c.managed, {"maxEffortLevel": "medium"})
    r = c.resolve()
    assert r["resolved"] is False and "managed_settings_present" in r["issues"]


@pytest.mark.parametrize("content", ["not json", "[1, 2]", '{"model": "a", "model": "b"}'])
def test_an_unreadable_settings_file_fails_closed(tmp_path, content):
    c = Claude(tmp_path)
    js(c.user, content)
    r = c.resolve(argv_model="claude-opus-5-5", argv_effort="high")
    assert r["resolved"] is False and "user_unreadable" in r["issues"]


def test_an_oversized_settings_file_fails_closed(tmp_path, monkeypatch):
    c = Claude(tmp_path)
    js(c.user, {"model": "claude-opus-5-5", "effortLevel": "high"})
    monkeypatch.setattr(eff_module, "MAX_SOURCE_BYTES", 10)
    r = c.resolve()
    assert "user_unreadable" in r["issues"]
    assert "exceeds the size bound" in next(s["detail"] for s in r["sources"] if s["layer"] == "user")


def test_a_settings_file_exactly_at_the_size_bound_is_read(tmp_path, monkeypatch):
    c = Claude(tmp_path)
    path = js(c.user, {"model": "claude-opus-5-5", "effortLevel": "high"})
    monkeypatch.setattr(eff_module, "MAX_SOURCE_BYTES", path.stat().st_size)
    assert c.resolve()["resolved"] is True


def test_a_symlinked_settings_file_fails_closed(tmp_path):
    c = Claude(tmp_path)
    real = js(tmp_path / "real.json", {"model": "claude-opus-5-5", "effortLevel": "high"})
    c.user.parent.mkdir(parents=True)
    try:
        c.user.symlink_to(real)
    except OSError:
        pytest.skip("symlinks unavailable")
    assert "user_unreadable" in c.resolve()["issues"]


# ---------------------------------------------------------------- Codex

def toml(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_codex_top_level_config_decides(tmp_path):
    cfg = toml(tmp_path / "config.toml", 'model = "gpt-6-luna"\nmodel_reasoning_effort = "low"\n[tui]\nx = 1\n')
    r = resolve_codex(argv_model="native", argv_effort="native", config=cfg)
    assert (r["model"], r["effort"], r["model_source"], r["resolved"]) == ("gpt-6-luna", "low", "user_config", True)


def test_codex_argv_beats_the_config(tmp_path):
    cfg = toml(tmp_path / "config.toml", 'model = "gpt-6-luna"\nmodel_reasoning_effort = "low"\n')
    r = resolve_codex(argv_model="gpt-6-sol", argv_effort="high", config=cfg)
    assert (r["model"], r["effort"], r["model_source"]) == ("gpt-6-sol", "high", "argv")


def test_codex_selected_profile_beats_top_level(tmp_path):
    cfg = toml(tmp_path / "config.toml", 'model = "gpt-6-luna"\nmodel_reasoning_effort = "low"\nprofile = "lead"\n'
                                         '[profiles.lead]\nmodel = "gpt-6-sol"\nmodel_reasoning_effort = "high"\n')
    r = resolve_codex(argv_model="native", argv_effort="native", config=cfg)
    assert (r["model"], r["effort"], r["model_source"]) == ("gpt-6-sol", "high", "profile:lead")


def test_codex_a_missing_selected_profile_fails_closed(tmp_path):
    cfg = toml(tmp_path / "config.toml", 'model = "gpt-6-luna"\nmodel_reasoning_effort = "low"\nprofile = "gone"\n')
    r = resolve_codex(argv_model="native", argv_effort="native", config=cfg)
    assert r["resolved"] is False and "selected_profile_missing" in r["issues"]


def test_codex_builtin_default_is_never_guessed(tmp_path):
    r = resolve_codex(argv_model="native", argv_effort="native", config=tmp_path / "absent.toml")
    assert (r["model"], r["effort"], r["resolved"]) == (None, None, False)
    assert {"model_from_unpinned_builtin_default", "effort_from_model_tuned_default"} <= set(r["issues"])


def test_codex_unreadable_config_fails_closed(tmp_path):
    cfg = toml(tmp_path / "config.toml", "model = = broken")
    r = resolve_codex(argv_model="gpt-6-sol", argv_effort="high", config=cfg)
    assert r["resolved"] is False and "user_config_unreadable" in r["issues"]


def test_codex_project_config_that_sets_a_model_is_unverified(tmp_path):
    cfg = toml(tmp_path / "config.toml", 'model = "gpt-6-sol"\nmodel_reasoning_effort = "high"\n')
    worktree = tmp_path / "wt"
    toml(worktree / ".codex" / "config.toml", 'model = "gpt-6-luna"\n')
    r = resolve_codex(argv_model="native", argv_effort="native", config=cfg, worktree=worktree)
    assert r["resolved"] is False and "project_config_precedence_unverified" in r["issues"]


def test_codex_project_config_without_model_keys_is_harmless(tmp_path):
    cfg = toml(tmp_path / "config.toml", 'model = "gpt-6-sol"\nmodel_reasoning_effort = "high"\n')
    worktree = tmp_path / "wt"
    toml(worktree / ".codex" / "config.toml", '[tui]\nx = 1\n')
    assert resolve_codex(argv_model="native", argv_effort="native", config=cfg, worktree=worktree)["resolved"]


# ---------------------------------------------------------------- classification

def resolved(provider, model, effort):
    return {"provider": provider, "model": model, "effort": effort, "resolved": True, "issues": []}


@pytest.mark.parametrize("lane,res,verdict,reason", [
    ("codex-lead-1", resolved("codex", "gpt-5.6-sol", "medium"), "allowed", "default"),
    ("codex-lead-1", resolved("codex", "gpt-6-sol", "high"), "allowed", "in_allowlist"),
    ("codex-lead-1", resolved("codex", "gpt-5.6-terra", "medium"), "not_in_lane_allowlist", "in_catalog_for_other_lanes"),
    ("codex-lead-1", resolved("codex", "gpt-6-luna", "xhigh"), "not_in_lane_allowlist", "not_in_catalog"),
    ("codex-lead-1", resolved("claude", "claude-opus-5-5", "xhigh"), "provider_mismatch", "lane_provider_codex"),
    ("claude-rco-1", resolved("claude", "claude-sonnet-5", "xhigh"), "allowed", "default"),
])
def test_classification(lane, res, verdict, reason):
    result = classify(CATALOG, lane, res)
    assert (result["verdict"], result["reasons"][0]) == (verdict, reason)


def test_below_floor_is_its_own_verdict():
    catalog = copy.deepcopy(CATALOG)
    catalog["lanes"]["claude-rco-1"]["floor"] = 0          # raise-or-same: the second profile is below the floor
    result = classify(catalog, "claude-rco-1", resolved("claude", "claude-sonnet-5", "xhigh"))
    assert (result["verdict"], result["profile"]) == ("below_floor", "claude-sonnet-5-xhigh")
    at_floor = classify(catalog, "claude-rco-1", resolved("claude", "claude-opus-5-5", "xhigh"))
    assert at_floor["verdict"] == "allowed"


def test_an_unresolved_result_is_unknown_with_its_issues():
    res = dict(resolved("codex", None, None), resolved=False, issues=["model_from_unpinned_builtin_default"])
    assert classify(CATALOG, "codex-lead-1", res) == {"verdict": "unknown", "profile": None,
                                                     "reasons": ["model_from_unpinned_builtin_default"]}


def test_an_unknown_lane_is_unknown():
    assert classify(CATALOG, "grok-scout-1", resolved("grok", "g", "high"))["verdict"] == "unknown"


# ---------------------------------------------------------------- CLI

def test_cli_exit_codes(tmp_path, capsys):
    cfg = toml(tmp_path / "config.toml", 'model = "gpt-5.6-sol"\nmodel_reasoning_effort = "medium"\n')
    assert main(["--lane", "codex-lead-1", "--cli", "codex", "--codex-config", str(cfg)]) == 0
    assert json.loads(capsys.readouterr().out)["classification"]["verdict"] == "allowed"
    luna = toml(tmp_path / "luna.toml", 'model = "gpt-6-luna"\nmodel_reasoning_effort = "low"\n')
    assert main(["--lane", "codex-lead-1", "--cli", "codex", "--codex-config", str(luna)]) == 3
    assert json.loads(capsys.readouterr().out)["classification"]["verdict"] == "not_in_lane_allowlist"
    assert main(["--lane", "codex-lead-1", "--cli", "codex", "--catalog", str(tmp_path / "none.json")]) == 2


def test_cli_planted_fault_luna_low_is_flagged(tmp_path, capsys):
    """The operator's planted-fault check: a luna/low config for Lead must not pass as allowed."""
    planted = toml(tmp_path / "config.toml", 'model = "gpt-6-luna"\nmodel_reasoning_effort = "low"\n')
    code = main(["--lane", "codex-lead-1", "--cli", "codex", "--codex-config", str(planted)])
    report = json.loads(capsys.readouterr().out)
    assert code == 3 and report["classification"]["verdict"] != "allowed"
    assert (report["model"], report["effort"], report["model_source"]) == ("gpt-6-luna", "low", "user_config")



# ---------------------------------------------------------------- managed settings in the registry (rco-2)

@pytest.mark.parametrize("hits", [["HKLM"], ["HKCU"], ["HKLM:unreadable"]])
def test_registry_managed_settings_fail_closed(tmp_path, hits):
    c = Claude(tmp_path)
    js(c.user, {"model": "claude-opus-5-5", "effortLevel": "high"})
    r = c.resolve(registry=lambda: hits)
    assert r["resolved"] is False and "managed_settings_present" in r["issues"]
    assert {"layer": "managed_registry", "path": hits[0], "state": "present"} in r["sources"]


def test_no_registry_policy_still_resolves(tmp_path):
    c = Claude(tmp_path)
    js(c.user, {"model": "claude-opus-5-5", "effortLevel": "high"})
    assert c.resolve(registry=lambda: [])["resolved"] is True


def test_an_unreadable_registry_is_not_no_policy(tmp_path):
    c = Claude(tmp_path)
    js(c.user, {"model": "claude-opus-5-5", "effortLevel": "high"})

    def boom():
        raise PermissionError("denied")
    r = c.resolve(registry=boom)
    assert r["resolved"] is False and "managed_settings_present" in r["issues"]


@pytest.fixture
def temp_policy_key():
    """A throwaway HKCU key standing in for the policy key; the real policy key is never touched."""
    if os.name != "nt":
        pytest.skip("Windows registry")
    import uuid
    import winreg
    path = rf"Software\WaggleDanceTest\{uuid.uuid4().hex}"
    winreg.CreateKey(winreg.HKEY_CURRENT_USER, path)
    yield path
    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)


def test_the_real_registry_reader_finds_a_settings_value(temp_policy_key):
    import winreg
    from tools.lane_effective_model import managed_registry_settings
    keys = (("HKCU", temp_policy_key),)
    assert managed_registry_settings(keys) == []                         # key exists, no Settings value
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, temp_policy_key, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "Settings", 0, winreg.REG_SZ, '{"model": "claude-sonnet-5"}')
    assert managed_registry_settings(keys) == ["HKCU"]
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, temp_policy_key, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "Settings", 0, winreg.REG_SZ, "   ")         # blank is not a policy
    assert managed_registry_settings(keys) == []
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, temp_policy_key, 0, winreg.KEY_SET_VALUE) as key:
        winreg.DeleteValue(key, "Settings")


def test_a_missing_policy_key_is_no_policy():
    from tools.lane_effective_model import managed_registry_settings
    assert managed_registry_settings((("HKCU", r"Software\WaggleDanceTest\does-not-exist-7f3a"),)) == []


def test_off_windows_the_registry_is_not_consulted(monkeypatch):
    from tools.lane_effective_model import managed_registry_settings
    monkeypatch.setattr(eff_module.sys, "platform", "linux")
    assert managed_registry_settings() == []


def test_a_policy_key_that_cannot_be_read_counts_as_managed(monkeypatch):
    if os.name != "nt":
        pytest.skip("Windows registry")
    import winreg
    from tools.lane_effective_model import managed_registry_settings

    def denied(*args, **kwargs):
        raise PermissionError("access denied")
    monkeypatch.setattr(winreg, "OpenKey", denied)
    assert managed_registry_settings((("HKLM", r"SOFTWARE\Policies\ClaudeCode"),)) == ["HKLM:unreadable"]
