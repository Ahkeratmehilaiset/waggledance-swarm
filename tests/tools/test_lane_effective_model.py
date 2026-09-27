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
# What /effort writes today: the level saved under the model's canonical id.
OPUS_HIGH = {"model": "claude-opus-5-5", "modelSettings": {"claude-opus-5-5": {"effortLevel": "high"}}}


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
    js(c.user, {"model": "claude-sonnet-5", "effortLevel": "medium"})
    r = c.resolve()
    assert (r["model"], r["effort"], r["model_source"], r["effort_source"], r["resolved"]) == \
        ("claude-sonnet-5", "medium", "user", "user", True)


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


@pytest.mark.parametrize("layer", ["user", "project"])
def test_the_models_own_level_beats_effortLevel_in_the_same_file(tmp_path, layer):
    # settings-reference modelSettings: "A model's effortLevel here takes precedence over the
    # top-level effortLevel in the same settings file."
    c = Claude(tmp_path)
    path = c.user if layer == "user" else c.worktree / ".claude" / "settings.json"
    js(path, {"model": "claude-sonnet-5", "effortLevel": "xhigh",
              "modelSettings": {"claude-sonnet-5": {"effortLevel": "medium"}}})
    r = c.resolve()
    assert (r["effort"], r["effort_source"], r["resolved"]) == ("medium", f"{layer}:modelSettings", True)


def test_per_model_and_global_effort_that_agree_resolve(tmp_path):
    c = Claude(tmp_path)
    js(c.user, {"model": "claude-opus-5-5", "effortLevel": "xhigh",
                "modelSettings": {"claude-opus-5-5": {"effortLevel": "xhigh"}}})
    assert (c.resolve()["effort"], c.resolve()["resolved"]) == ("xhigh", True)


@pytest.mark.parametrize("where", ["settings", "env"])
def test_effort_auto_is_the_model_tuned_default(tmp_path, where):
    c = Claude(tmp_path)
    js(c.user, {"model": "claude-sonnet-5", "effortLevel": "auto" if where == "settings" else "high"})
    r = c.resolve(env={"CLAUDE_CODE_EFFORT_LEVEL": "auto"} if where == "env" else None)
    assert r["effort"] is None and "effort_auto_is_model_tuned_default" in r["issues"]


def test_managed_settings_fail_closed(tmp_path):
    c = Claude(tmp_path)
    js(c.user, dict(OPUS_HIGH))
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
    js(c.user, dict(OPUS_HIGH))
    monkeypatch.setattr(eff_module, "MAX_SOURCE_BYTES", 10)
    r = c.resolve()
    assert "user_unreadable" in r["issues"]
    assert "exceeds the size bound" in next(s["detail"] for s in r["sources"] if s["layer"] == "user")


def test_a_settings_file_exactly_at_the_size_bound_is_read(tmp_path, monkeypatch):
    c = Claude(tmp_path)
    path = js(c.user, dict(OPUS_HIGH))
    monkeypatch.setattr(eff_module, "MAX_SOURCE_BYTES", path.stat().st_size)
    assert c.resolve()["resolved"] is True


def test_a_symlinked_settings_file_fails_closed(tmp_path):
    c = Claude(tmp_path)
    real = js(tmp_path / "real.json", dict(OPUS_HIGH))
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
    assert (r["model"], r["effort"], r["model_source"], r["effort_source"]) == ("gpt-6-sol", "high", "argv", "argv")


@pytest.mark.parametrize("legacy,keys", [
    ('profile = "lead"\n[profiles.lead]\nmodel = "gpt-6-sol"\nmodel_reasoning_effort = "high"\n', ["profile", "profiles"]),
    ('profile = "lead"\n', ["profile"]),
    ('[profiles.lead]\nmodel = "gpt-6-sol"\nmodel_reasoning_effort = "high"\n', ["profiles"]),
])
def test_codex_legacy_profiles_are_never_active_and_fail_closed(tmp_path, legacy, keys):
    # OpenAI Codex docs (config-advanced): since 0.134.0 the profile = selector and inline
    # [profiles.<name>] are no longer supported (codex-tools-1 B2 on #1744).
    cfg = toml(tmp_path / "config.toml", 'model = "gpt-6-luna"\nmodel_reasoning_effort = "low"\n' + legacy)
    r = resolve_codex(argv_model="native", argv_effort="native", config=cfg)
    assert (r["model"], r["effort"], r["model_source"]) == ("gpt-6-luna", "low", "user_config")
    assert r["resolved"] is False
    assert [i for i in r["issues"] if i.startswith("codex_legacy_profile_unsupported")] == \
        [f"codex_legacy_profile_unsupported:{k}" for k in keys]
    assert classify(CATALOG, "codex-lead-1", r)["verdict"] == "unknown"


def test_codex_top_level_values_resolve_without_legacy_keys(tmp_path):
    cfg = toml(tmp_path / "config.toml", 'model = "gpt-6-sol"\nmodel_reasoning_effort = "high"\n')
    r = resolve_codex(argv_model="native", argv_effort="native", config=cfg)
    assert (r["model"], r["effort"], r["model_source"], r["resolved"]) == ("gpt-6-sol", "high", "user_config", True)


def test_codex_a_legacy_selector_naming_no_table_fails_closed(tmp_path):
    cfg = toml(tmp_path / "config.toml", 'model = "gpt-6-luna"\nmodel_reasoning_effort = "low"\nprofile = "gone"\n')
    r = resolve_codex(argv_model="native", argv_effort="native", config=cfg)
    assert r["resolved"] is False and "codex_legacy_profile_unsupported:profile" in r["issues"]


def test_codex_builtin_default_is_never_guessed(tmp_path):
    r = resolve_codex(argv_model="native", argv_effort="native", config=tmp_path / "absent.toml")
    assert (r["model"], r["effort"], r["resolved"]) == (None, None, False)
    assert {"model_from_unpinned_builtin_default", "effort_from_model_tuned_default"} <= set(r["issues"])


def test_codex_unreadable_config_fails_closed(tmp_path):
    cfg = toml(tmp_path / "config.toml", "model = = broken")
    r = resolve_codex(argv_model="gpt-6-sol", argv_effort="high", config=cfg)
    assert r["resolved"] is False and "user_config_unreadable" in r["issues"]


@pytest.mark.parametrize("project", ['model = "gpt-6-luna"\n', 'model_reasoning_effort = "low"\n', 'profile = "p"\n',
                                     '[profiles.p]\nmodel = "gpt-6-luna"\n'])
def test_codex_project_config_that_sets_a_model_is_unverified(tmp_path, project):
    cfg = toml(tmp_path / "config.toml", 'model = "gpt-6-sol"\nmodel_reasoning_effort = "high"\n')
    worktree = tmp_path / "wt"
    toml(worktree / ".codex" / "config.toml", project)
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
    js(c.user, dict(OPUS_HIGH))
    r = c.resolve(registry=lambda: hits)
    assert r["resolved"] is False and "managed_settings_present" in r["issues"]
    assert {"layer": "managed_registry", "path": hits[0], "state": "present"} in r["sources"]


def test_no_registry_policy_still_resolves(tmp_path):
    c = Claude(tmp_path)
    js(c.user, dict(OPUS_HIGH))
    assert c.resolve(registry=lambda: [])["resolved"] is True


def test_an_unreadable_registry_is_not_no_policy(tmp_path):
    c = Claude(tmp_path)
    js(c.user, dict(OPUS_HIGH))

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
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\WaggleDanceTest")
    except OSError:
        pass                                                   # another run still has a key under it


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


# ---------------------------------------------------------------- documented sources (claude-rco-1 review of #1744)

def _lane_default():
    spec = CATALOG["lanes"]["claude-rco-1"]
    profile = CATALOG["capacity_policy"]["profiles"][spec["default"]]
    return profile["model"], profile["effort"]


def test_claude_code_effort_level_env_beats_settings(tmp_path):
    c = Claude(tmp_path)
    model, effort = _lane_default()
    js(c.user, {"model": model, "effortLevel": effort})
    r = c.resolve(env={"CLAUDE_CODE_EFFORT_LEVEL": "low"})
    assert (r["effort"], r["effort_source"]) == ("low", "env:CLAUDE_CODE_EFFORT_LEVEL")
    assert classify(CATALOG, "claude-rco-1", r)["verdict"] != "allowed"


def test_the_effort_env_beats_argv(tmp_path):
    # settings-reference effortLevel: "--effort takes precedence over this key for one session,
    # and CLAUDE_CODE_EFFORT_LEVEL takes precedence over both".
    c = Claude(tmp_path)
    js(c.user, {"model": "claude-opus-5-5"})
    r = c.resolve(argv_effort="high", env={"CLAUDE_CODE_EFFORT_LEVEL": "low"})
    assert (r["effort"], r["effort_source"], r["resolved"]) == ("low", "env:CLAUDE_CODE_EFFORT_LEVEL", True)


@pytest.mark.parametrize("key", ["ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_MODEL", "CLAUDE_CODE_EFFORT_LEVEL"])
def test_a_settings_env_block_that_sets_model_or_effort_is_unknown(tmp_path, key):
    c = Claude(tmp_path)
    model, effort = _lane_default()
    js(c.user, {"model": model, "effortLevel": effort, "env": {key: "x"}})
    r = c.resolve()
    assert r["resolved"] is False and f"env_block_sets_{key}_in_user" in r["issues"]


def test_an_unrelated_env_block_is_harmless(tmp_path):
    c = Claude(tmp_path)
    model, effort = _lane_default()
    js(c.user, {"model": model, "effortLevel": effort, "env": {"DISABLE_TELEMETRY": "1"}})
    assert c.resolve()["resolved"] is True


@pytest.mark.parametrize("payload", [{"model": "claude-haiku-4-5"}, {}])
def test_server_managed_settings_cache_fails_closed(tmp_path, payload):
    c = Claude(tmp_path)
    model, effort = _lane_default()
    js(c.user, {"model": model, "effortLevel": effort})
    js(c.user.parent / "remote-settings.json", payload)
    r = c.resolve()
    assert r["resolved"] is False and "managed_settings_present" in r["issues"]
    assert (r["model"], r["model_source"]) == (model, "user")    # the cache's own model is never read


def test_an_unreadable_server_managed_cache_fails_closed(tmp_path):
    c = Claude(tmp_path)
    model, effort = _lane_default()
    js(c.user, {"model": model, "effortLevel": effort})
    js(c.user.parent / "remote-settings.json", "{not json")
    r = c.resolve()
    assert r["resolved"] is False and "managed_remote_cache_unreadable" in r["issues"]


@pytest.mark.parametrize("key,bad", [("model", 123), ("effortLevel", ["low"])])
def test_a_non_string_value_in_a_higher_file_does_not_fall_through(tmp_path, key, bad):
    c = Claude(tmp_path)
    js(c.user, {"model": "claude-sonnet-5", "effortLevel": "low"})
    js(c.worktree / ".claude" / "settings.json", {key: bad})
    r = c.resolve()
    assert r["resolved"] is False and f"{key}_not_a_string_in_project" in r["issues"]


@pytest.mark.parametrize("key", ["model", "model_reasoning_effort"])
def test_codex_a_non_string_value_does_not_fall_through(tmp_path, key):
    text = 'model = 5\nmodel_reasoning_effort = "low"\n' if key == "model" else 'model = "gpt-6-sol"\nmodel_reasoning_effort = [1]\n'
    r = resolve_codex(argv_model="native", argv_effort="native", config=toml(tmp_path / "config.toml", text))
    assert r["resolved"] is False and f"{key}_not_a_string_in_user_config" in r["issues"]


# ---------------------------------------------------------------- documented effort order (rco-1 N3/N4)

def test_opus_5_5_ignores_a_user_file_effortLevel(tmp_path):
    # settings-reference effortLevel: "Opus 5.5 and models released after it ignore it".
    c = Claude(tmp_path)
    js(c.user, {"model": "claude-opus-5-5", "effortLevel": "xhigh"})
    r = c.resolve()
    assert (r["effort"], r["effort_source"], r["resolved"]) == (None, "builtin_default", False)
    assert "effort_from_model_tuned_default" in r["issues"]


@pytest.mark.parametrize("model", ["claude-opus-5-5", "claude-opus-5-5[1m]"])
def test_a_project_file_effortLevel_applies_to_every_model(tmp_path, model):
    c = Claude(tmp_path)
    js(c.user, {"model": model, "effortLevel": "low"})
    js(c.worktree / ".claude" / "settings.json", {"effortLevel": "high"})
    r = c.resolve()
    assert (r["effort"], r["effort_source"], r["resolved"]) == ("high", "project", True)


@pytest.mark.parametrize("model", ["claude-sonnet-5", "claude-opus-5", "claude-fable-5-1"])
def test_a_user_file_effortLevel_applies_to_earlier_models(tmp_path, model):
    c = Claude(tmp_path)
    js(c.user, {"model": model, "effortLevel": "high"})
    assert (c.resolve()["effort"], c.resolve()["resolved"]) == ("high", True)


def test_a_user_file_effortLevel_for_an_unlisted_model_is_unknown(tmp_path):
    c = Claude(tmp_path)
    js(c.user, {"model": "claude-opus-6", "effortLevel": "high"})
    r = c.resolve()
    assert (r["effort"], r["resolved"]) == (None, False)
    assert "user_effort_level_applicability_unverified" in r["issues"]
    js(c.user, {"model": "claude-opus-6", "modelSettings": {"claude-opus-6": {"effortLevel": "high"}}})
    assert c.resolve()["resolved"] is True                    # its own saved level is fine


def test_across_files_the_highest_file_that_sets_a_level_for_the_model_decides(tmp_path):
    # settings-reference modelSettings: "an effortLevel in managed settings outranks a level
    # you saved in user settings" - the same rule for any higher-precedence file.
    c = Claude(tmp_path)
    js(c.user, OPUS_HIGH)
    project = c.worktree / ".claude" / "settings.json"
    js(project, {"effortLevel": "low"})
    assert (c.resolve()["effort"], c.resolve()["effort_source"]) == ("low", "project")
    js(project, {"modelSettings": {"claude-opus-5-5": {"effortLevel": "medium"}}})
    js(c.user, {"model": "claude-opus-5-5", "effortLevel": "xhigh"})
    assert (c.resolve()["effort"], c.resolve()["effort_source"]) == ("medium", "project:modelSettings")


def test_another_models_saved_level_does_not_decide(tmp_path):
    c = Claude(tmp_path)
    js(c.worktree / ".claude" / "settings.json", {"modelSettings": {"claude-sonnet-5": {"effortLevel": "low"}}})
    js(c.user, OPUS_HIGH)
    assert (c.resolve()["effort"], c.resolve()["effort_source"]) == ("high", "user:modelSettings")


@pytest.mark.parametrize("model,key", [("claude-opus-5-5[1m]", "claude-opus-5-5"),
                                       ("claude-haiku-4-5-20251001", "claude-haiku-4-5")])
def test_saved_levels_match_suffixed_and_dated_model_ids(tmp_path, model, key):
    c = Claude(tmp_path)
    js(c.user, {"model": model, "modelSettings": {key: {"effortLevel": "low"}}})
    r = c.resolve()
    assert (r["effort"], r["resolved"]) == ("low", True)
    assert r["model"] == model.replace("[1m]", "")           # the reported id keeps its date


@pytest.mark.parametrize("model_settings,issue", [
    ({"opus": {"effortLevel": "low"}}, "modelSettings_key_unresolved_in_user"),
    ({"claude-opus-5-5[1m]": {"effortLevel": "low"}}, "modelSettings_key_not_canonical_in_user"),
    ({"claude-opus-5-5": {"effortLevel": "low"}, "claude-opus-5-5[1m]": {"effortLevel": "low"}},
     "modelSettings_key_not_canonical_in_user"),
    ({"claude-opus-5-5": {"effortLevel": 3}}, "modelSettings_effortLevel_not_a_string_in_user"),
    ({"claude-opus-5-5": "high"}, "modelSettings_entry_not_an_object_in_user"),
    (["claude-opus-5-5"], "modelSettings_not_an_object_in_user"),
])
def test_an_undecidable_saved_level_fails_closed(tmp_path, model_settings, issue):
    c = Claude(tmp_path)
    js(c.user, {"model": "claude-opus-5-5", "modelSettings": model_settings})
    r = c.resolve()
    assert (r["effort"], r["resolved"]) == (None, False) and issue in r["issues"]


def test_a_dated_key_for_the_model_fails_closed(tmp_path):
    # Claude Code writes the canonical id; a dated key is not documented as read.
    c = Claude(tmp_path)
    js(c.user, {"model": "claude-haiku-4-5", "modelSettings": {"claude-haiku-4-5-20251001": {"effortLevel": "low"}}})
    r = c.resolve()
    assert (r["effort"], r["resolved"]) == (None, False)
    assert "modelSettings_key_not_canonical_in_user" in r["issues"]


@pytest.mark.parametrize("settings,capped", [
    ({"maxEffortLevel": "medium"}, True),
    ({"maxEffortLevel": "max"}, False),
    ({"modelSettings": {"claude-opus-5-5": {"maxEffortLevel": "low"}}}, True),
    ({"modelSettings": {"claude-opus-5-5": {"maxEffortLevel": "max"}}}, False),
    ({"modelSettings": {"claude-sonnet-5": {"maxEffortLevel": "low"}}}, False),
])
def test_an_effort_cap_fails_closed_even_under_argv(tmp_path, settings, capped):
    # settings-reference maxEffortLevel: any higher level "runs at the cap instead, including one
    # from ... --effort, CLAUDE_CODE_EFFORT_LEVEL".
    c = Claude(tmp_path)
    js(c.worktree / ".claude" / "settings.json", settings)
    js(c.user, OPUS_HIGH)
    r = c.resolve(argv_effort="xhigh")
    assert ("effort_cap_in_project" in r["issues"]) is capped
    assert r["resolved"] is (not capped)


@pytest.mark.parametrize("entry,capped", [({"maxEffortLevel": "low"}, True), ({"effortLevel": "xhigh"}, False),
                                          ({"maxEffortLevel": "max"}, False)])
def test_a_saved_cap_with_an_unresolved_model_fails_closed(tmp_path, entry, capped):
    c = Claude(tmp_path)
    js(c.user, {"modelSettings": {"claude-opus-5-5": entry}})     # today's machine: no model key
    assert ("effort_cap_in_user" in c.resolve()["issues"]) is capped


@pytest.mark.parametrize("layer", ["user", "project"])
def test_the_ultracode_setting_runs_at_xhigh_over_saved_levels(tmp_path, layer):
    # settings-reference ultracode: "runs the session at xhigh effort and takes precedence over
    # effortLevel and modelSettings entries".
    c = Claude(tmp_path)
    js(c.user, OPUS_HIGH)
    path = c.user if layer == "user" else c.worktree / ".claude" / "settings.json"
    js(path, {**(OPUS_HIGH if layer == "user" else {}), "ultracode": True})
    r = c.resolve()
    assert (r["effort"], r["effort_source"], r["resolved"]) == ("xhigh", f"ultracode:{layer}", True)


def test_a_higher_file_can_switch_ultracode_off(tmp_path):
    c = Claude(tmp_path)
    js(c.user, {**OPUS_HIGH, "ultracode": True})
    js(c.worktree / ".claude" / "settings.json", {"ultracode": False})
    assert (c.resolve()["effort"], c.resolve()["effort_source"]) == ("high", "user:modelSettings")


def test_argv_against_the_ultracode_setting_is_unknown(tmp_path):
    c = Claude(tmp_path)
    js(c.user, {**OPUS_HIGH, "ultracode": True})
    r = c.resolve(argv_effort="medium")
    assert (r["effort"], r["resolved"]) == (None, False)
    assert "argv_effort_against_ultracode_setting_unverified" in r["issues"]
    assert (c.resolve(argv_effort="xhigh")["effort"], c.resolve(argv_effort="xhigh")["resolved"]) == ("xhigh", True)


def test_the_effort_env_beats_the_ultracode_setting(tmp_path):
    # model-config: "When CLAUDE_CODE_EFFORT_LEVEL is set to a level other than xhigh, requests
    # run at that level".
    c = Claude(tmp_path)
    js(c.user, {**OPUS_HIGH, "ultracode": True})
    r = c.resolve(env={"CLAUDE_CODE_EFFORT_LEVEL": "low"})
    assert (r["effort"], r["effort_source"], r["resolved"]) == ("low", "env:CLAUDE_CODE_EFFORT_LEVEL", True)


def test_argv_ultracode_is_xhigh(tmp_path):
    c = Claude(tmp_path)
    js(c.user, OPUS_HIGH)
    r = c.resolve(argv_effort="ultracode")
    assert (r["effort"], r["effort_source"], r["resolved"]) == ("xhigh", "argv", True)


def test_the_effort_env_does_not_accept_ultracode(tmp_path):
    c = Claude(tmp_path)
    js(c.user, OPUS_HIGH)
    r = c.resolve(env={"CLAUDE_CODE_EFFORT_LEVEL": "ultracode"})
    assert (r["effort"], r["resolved"]) == (None, False) and "env_effort_ultracode_not_accepted" in r["issues"]


def test_a_non_boolean_ultracode_fails_closed(tmp_path):
    c = Claude(tmp_path)
    js(c.user, {**OPUS_HIGH, "ultracode": "yes"})
    r = c.resolve()
    assert r["resolved"] is False and "ultracode_not_a_boolean_in_user" in r["issues"]



# ---------------------------------------------------------------- config directories (codex-tools-1 B1 on #1744)

def _cli(capsys, *args):
    code = main(list(args))
    return code, json.loads(capsys.readouterr().out)


def test_the_cli_reads_codex_home(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    toml(tmp_path / "home" / ".codex" / "config.toml", 'model = "gpt-6-sol"\nmodel_reasoning_effort = "high"\n')
    toml(tmp_path / "codexhome" / "config.toml", 'model = "gpt-6-luna"\nmodel_reasoning_effort = "low"\n')
    monkeypatch.delenv("CODEX_HOME", raising=False)
    code, out = _cli(capsys, "--lane", "codex-lead-1", "--cli", "codex")
    assert (code, out["model"]) == (0, "gpt-6-sol")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codexhome"))
    code, out = _cli(capsys, "--lane", "codex-lead-1", "--cli", "codex")
    assert (code, out["model"], out["classification"]["verdict"]) == (3, "gpt-6-luna", "not_in_lane_allowlist")


def test_the_cli_reads_claude_config_dir(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for var in ("CLAUDE_CONFIG_DIR", "ANTHROPIC_MODEL", "CLAUDE_CODE_EFFORT_LEVEL", "ANTHROPIC_DEFAULT_MODEL"):
        monkeypatch.delenv(var, raising=False)
    seen = []
    js(tmp_path / "home" / ".claude" / "settings.json", {"model": "claude-sonnet-5", "effortLevel": "xhigh"})
    js(tmp_path / "claudedir" / "settings.json", {"model": "claude-haiku-4-5", "effortLevel": "low"})
    missing = str(tmp_path / "no-managed.json")
    monkeypatch.setattr(eff_module, "managed_registry_settings", lambda *a, **k: seen.append(1) or [])
    code, out = _cli(capsys, "--lane", "claude-rco-1", "--cli", "claude", "--claude-managed-settings", missing)
    assert (code, out["model"]) == (0, "claude-sonnet-5")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claudedir"))
    code, out = _cli(capsys, "--lane", "claude-rco-1", "--cli", "claude", "--claude-managed-settings", missing)
    assert (code, out["model"], out["classification"]["verdict"]) == (3, "claude-haiku-4-5", "not_in_lane_allowlist")
    assert seen == [1, 1]                         # the registry reader is looked up at call time


def test_the_cli_path_fails_closed_on_a_registry_policy(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claudedir"))
    js(tmp_path / "claudedir" / "settings.json", {"model": "claude-sonnet-5", "effortLevel": "xhigh"})
    monkeypatch.setattr(eff_module, "managed_registry_settings", lambda *a, **k: ["HKLM"])
    code, out = _cli(capsys, "--lane", "claude-rco-1", "--cli", "claude",
                     "--claude-managed-settings", str(tmp_path / "no-managed.json"))
    assert code == 3 and "managed_settings_present" in out["issues"]


def test_the_server_managed_cache_follows_claude_config_dir(tmp_path):
    from tools.lane_effective_model import claude_config_dir
    moved = claude_config_dir({"CLAUDE_CONFIG_DIR": str(tmp_path / "claudedir")})
    assert moved == tmp_path / "claudedir"
    c = Claude(tmp_path)
    c.user = moved / "settings.json"
    js(c.user, {"model": "claude-sonnet-5", "effortLevel": "xhigh"})
    js(moved / "remote-settings.json", {})
    assert "managed_settings_present" in c.resolve()["issues"]


@pytest.mark.parametrize("value", ["", "   "])
def test_a_blank_config_dir_means_the_default(value):
    from tools.lane_effective_model import claude_config_dir, codex_home
    assert claude_config_dir({"CLAUDE_CONFIG_DIR": value}) == Path.home() / ".claude"
    assert codex_home({"CODEX_HOME": value}) == Path.home() / ".codex"


def test_a_settings_env_block_that_moves_the_config_dir_fails_closed(tmp_path):
    c = Claude(tmp_path)
    js(c.user, {"model": "claude-sonnet-5", "effortLevel": "xhigh", "env": {"CLAUDE_CONFIG_DIR": str(tmp_path / "x")}})
    r = c.resolve()
    assert r["resolved"] is False and "env_block_sets_CLAUDE_CONFIG_DIR_in_user" in r["issues"]


# ---------------------------------------------------------------- availableModels (claude-rco-1 review of #1745)

@pytest.mark.parametrize("allowed", [["opus"], ["claude-sonnet-5"], []])
def test_an_available_models_list_in_any_file_fails_closed(tmp_path, allowed):
    # model-config "Restrict model selection": a blocked `model` setting "is replaced ... and the
    # session starts on the default model"; the list's matching rules are not modelled here.
    c = Claude(tmp_path)
    js(c.user, {"model": "claude-sonnet-5", "effortLevel": "xhigh", "availableModels": allowed})
    r = c.resolve()
    assert r["resolved"] is False and "available_models_in_user" in r["issues"]


def test_an_available_models_list_in_a_project_file_fails_closed(tmp_path):
    c = Claude(tmp_path)
    js(c.user, {"model": "claude-sonnet-5", "effortLevel": "xhigh"})
    js(c.worktree / ".claude" / "settings.json", {"availableModels": ["opus"]})
    assert "available_models_in_project" in c.resolve()["issues"]
