#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Effective model and effort a lane WILL launch on (lane profile switching PR-7a).

Fleet lanes launch with ``model: native``: the launcher passes no model or
effort, so each CLI falls back to its own configuration. That configuration is
shared by sibling lanes. A single ``/model`` or ``/effort`` in one window, or an
edit to ``~/.codex/config.toml``, silently changes every sibling's next launch
(operator finding, 2026-09-27). This module resolves, from the same sources the
CLI reads, which model and effort a launch will actually get, and classifies
that against the signed catalog.

It is read-only and wired to nothing; the launch preflight that emits a bridge
event on anything but ``allowed`` is PR-7b.

Precedence
----------
Claude Code (documented: code.claude.com/docs/en/settings.md "settings
precedence", model-config.md, env-vars):

* model: managed settings > ``--model`` > ``ANTHROPIC_MODEL`` (overrides every
  settings file's ``model``) > ``--settings`` file > project
  ``.claude/settings.local.json`` > project ``.claude/settings.json`` > user
  ``~/.claude/settings.json`` > ``ANTHROPIC_DEFAULT_MODEL`` (only when no file
  sets ``model``) > built-in default;
* effort (settings-reference.md ``effortLevel``, ``modelSettings``,
  ``maxEffortLevel`` and ``ultracode``; model-config.md "Adjust effort level"):
  ``CLAUDE_CODE_EFFORT_LEVEL`` > ``--effort`` > the ``ultracode`` setting (runs
  at ``xhigh``) > settings > the model's default. In settings, per model, the
  highest-precedence file that sets either that model's
  ``modelSettings.<model>.effortLevel`` or a top-level ``effortLevel`` that
  applies to the model decides; within one file the model's own level wins. A
  top-level ``effortLevel`` in the USER file applies only to Opus 5, Fable 5.1
  and earlier models: Opus 5.5 and later ignore it. Any ``maxEffortLevel`` cap
  below ``max`` fails closed, as does ``--effort`` against ``ultracode: true``,
  whose order is not documented. A settings level outside ``low``, ``medium``,
  ``high`` and ``xhigh`` is not accepted by the CLI and fails closed.
* thinking (model-config.md "Extended thinking", env-vars ``MAX_THINKING_TOKENS``,
  settings-reference ``alwaysThinkingEnabled``): ``MAX_THINKING_TOKENS=0`` or
  ``alwaysThinkingEnabled: false`` turns thinking off on every model except Opus
  5.5 and the Fable models. A profile's effort then no longer describes the
  session, so thinking that may be off fails closed.

Codex CLI: ``--model`` and ``-c model_reasoning_effort=`` on argv > top-level
``model`` and ``model_reasoning_effort`` in ``$CODEX_HOME/config.toml`` (default
``~/.codex``) > built-in default. Since Codex 0.134.0 a profile is a separate
``<name>.config.toml`` chosen only with ``--profile``; the old ``profile =``
selector and inline ``[profiles.<name>]`` tables are "no longer supported"
(OpenAI Codex docs, config-advanced). The launchers never pass ``--profile``,
and what 0.157 does with the legacy keys is not verified, so either one fails
closed. A project ``.codex/config.toml`` inside the worktree is reported as a
source whose precedence is not verified; when it sets a value, the result is
unknown.

Configuration directories: ``CLAUDE_CONFIG_DIR`` ("All settings ... are stored
under this path") and ``CODEX_HOME`` move the user files, as they do for the
launchers; the CLI defaults follow them.

Fail closed
-----------
A value from a built-in default, an unresolved alias (``opus``, ``sonnet``,
``default`` ...), an unreadable or undecidable source, or managed settings yields
``None``. ``classify`` then gives ``unknown``, never a guess. Managed settings are
detected in three forms: the ``managed-settings.json`` file, a ``Settings``
registry value under ``SOFTWARE/Policies/ClaudeCode`` in HKLM (Group Policy or
MDM) or HKCU, and the local cache of server-managed settings,
``~/.claude/remote-settings.json``. A registry key that exists but cannot be read
counts as managed too (claude-rco-2 and claude-rco-1 reviews of #1744).

Not modelled offline: server-managed settings on a first launch before any cache
exists, an organization default model that overrides user selection, and
organization effort limits. These live in the account, not on this machine.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import tomllib
from typing import Any, Callable, Mapping

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.lane_profile_catalog import LANES, load_catalog  # noqa: E402

SCHEMA = "wd.lane-effective-model.v1"
MAX_SOURCE_BYTES = 1024 * 1024
# "unset" is how the launchers spell an empty value to the probe (Windows PowerShell 5.1 drops an
# empty native argument), so it means "nothing on argv", exactly like "native".
NATIVE = ("", "native", "unset", None)
CLAUDE_ALIASES = frozenset({"opus", "sonnet", "haiku", "fable", "default", "opusplan", "best", "latest"})
KNOWN_MODEL_SUFFIXES = frozenset({"[1m]"})
_SUFFIX = re.compile(r"\[[^\]]*\]$")
EXIT_ALLOWED, EXIT_ERROR, EXIT_ATTENTION = 0, 2, 3
DEFAULT_CATALOG = Path(__file__).resolve().parents[1] / "configs" / "lane_profile_catalog.json"
# Documented Windows location of Claude Code managed settings.
DEFAULT_CLAUDE_MANAGED = Path(r"C:\Program Files\ClaudeCode\managed-settings.json")
# Documented registry delivery of the same managed settings (value "Settings").
MANAGED_REGISTRY_KEYS = (("HKLM", r"SOFTWARE\Policies\ClaudeCode"), ("HKCU", r"SOFTWARE\Policies\ClaudeCode"))
MANAGED_REGISTRY_VALUE = "Settings"
MANAGED_LAYERS = frozenset({"managed", "managed_remote_cache"})
# A top-level effortLevel in the USER file "keeps applying where it applied before, on Opus 5,
# Fable 5.1, and earlier models"; "Opus 5.5 and models released after it ignore it"
# (settings-reference.md effortLevel). Earlier by the CLI release that added each model
# (model-config.md: Sonnet 5 v2.1.197, Opus 5 v2.1.219, Opus 5.5 v2.1.280). A model in
# neither set is unverified, and a user-file effortLevel that would decide for it fails closed.
USER_EFFORT_LEVEL_APPLIES = frozenset({
    "claude-opus-5", "claude-fable-5-1", "claude-fable-5", "claude-sonnet-5", "claude-opus-4-8",
    "claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5"})
USER_EFFORT_LEVEL_IGNORED = frozenset({"claude-opus-5-5"})
# The only accepted settings levels: the effortLevel and modelSettings type lists in
# settings-reference.md, and model-config.md: "`max` isn't accepted as a level in either key".
# "auto" keeps its own model-default handling.
SETTINGS_EFFORT_LEVELS = frozenset({"low", "medium", "high", "xhigh", "auto"})
# "You can't turn thinking off on Opus 5.5 or the Fable models" (model-config.md).
THINKING_ALWAYS_ON = frozenset({"claude-opus-5-5"})
THINKING_ALWAYS_ON_PREFIXES = ("claude-fable-",)
_DATE = re.compile(r"-\d{8}$")
# A resumed session "keeps the model it was using when the transcript was saved, regardless of the
# current model setting"; --model and ANTHROPIC_MODEL still win, and so does ANTHROPIC_DEFAULT_MODEL
# when a new session would start on it (model-config.md). The family variables below "take
# precedence over the restored model" in a way the docs do not spell out, so they fail closed.
MODEL_FAMILY_ENV = ("ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
                    "ANTHROPIC_DEFAULT_HAIKU_MODEL", "ANTHROPIC_DEFAULT_FABLE_MODEL")
RESUME_WINS_OVER_TRANSCRIPT = ("argv", "env:ANTHROPIC_MODEL", "env:ANTHROPIC_DEFAULT_MODEL")
TRANSCRIPT_TAIL_BYTES = 8 * 1024 * 1024
_THREAD = re.compile(r"^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$")
_MODEL_COMMAND = "<command-name>/model</command-name>"
# Another provider or a gateway changes the model ids and the resume semantics ("the transcript
# model isn't restored at all" on provider deployment ids, model-config.md). The fleet uses the
# Anthropic API; any of these set fails closed (claude-rco-1 NB-a on #1745).
THIRD_PARTY_ENV = ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
                   "CLAUDE_CODE_USE_MANTLE", "CLAUDE_CODE_USE_ANTHROPIC_AWS",
                   "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST", "ANTHROPIC_BASE_URL")
ENV_MODEL_KEYS = ("ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_MODEL", "CLAUDE_CODE_EFFORT_LEVEL",
                  "MAX_THINKING_TOKENS", "CLAUDE_CODE_DISABLE_THINKING", "CLAUDE_CONFIG_DIR") + THIRD_PARTY_ENV


def claude_config_dir(env: Mapping[str, str]) -> Path:
    """``CLAUDE_CONFIG_DIR`` ("All settings ... are stored under this path", env-vars.md), else ``~/.claude``."""
    value = _text(env.get("CLAUDE_CONFIG_DIR"))
    return Path(os.path.abspath(value)) if value else Path.home() / ".claude"


def codex_home(env: Mapping[str, str]) -> Path:
    """``CODEX_HOME`` (read the same way by start-wd-agent), else ``~/.codex``."""
    value = _text(env.get("CODEX_HOME"))
    return Path(os.path.abspath(value)) if value else Path.home() / ".codex"


class SourceError(ValueError):
    """A configuration source exists but cannot be read safely; its value is unknown."""


def _read(path: Path) -> bytes | None:
    """The file's bytes; None when it does not exist; SourceError when unsafe or unreadable."""
    try:
        if not path.exists() and not path.is_symlink():
            return None
        if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
            raise SourceError(f"{path.name}: symlink or reparse point")
        with path.open("rb") as stream:
            data = stream.read(MAX_SOURCE_BYTES + 1)
    except OSError as exc:
        raise SourceError(f"{path.name}: unreadable ({exc.__class__.__name__})") from None
    if len(data) > MAX_SOURCE_BYTES:
        raise SourceError(f"{path.name}: exceeds the size bound")
    return data


def _pairs(pairs: list) -> dict:
    seen: dict = {}
    for key, value in pairs:
        if key in seen:
            raise SourceError(f"duplicate key {key!r}")
        seen[key] = value
    return seen


def _json(path: Path) -> dict | None:
    data = _read(path)
    if data is None:
        return None
    try:
        value = json.loads(data.decode("utf-8-sig"), object_pairs_hook=_pairs)
    except SourceError:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise SourceError(f"{path.name}: not JSON ({exc.__class__.__name__})") from None
    if not isinstance(value, dict):
        raise SourceError(f"{path.name}: not a JSON object")
    return value


def _toml(path: Path) -> dict | None:
    data = _read(path)
    if data is None:
        return None
    try:
        return tomllib.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, RecursionError) as exc:
        raise SourceError(f"{path.name}: not TOML ({exc.__class__.__name__})") from None


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def claude_transcript_path(config_dir: Path, worktree: Path, thread: str) -> Path:
    """Where Claude Code keeps a lane's conversation: the launcher's Get-WdClaudeResumeState rule."""
    if not isinstance(thread, str) or not _THREAD.match(thread):
        raise SourceError("resume thread is not a session id")
    return config_dir / "projects" / re.sub(r"[^a-zA-Z0-9]", "-", str(worktree)) / f"{thread}.jsonl"


def _is_model_command(record: dict) -> bool:
    message = record.get("message")
    contents = [record.get("content"), message.get("content") if isinstance(message, dict) else None]
    texts: list[str] = []
    for content in contents:
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            # Block-list content: a /model marker in any text block counts (Grok review of #1745).
            texts += [block.get("text") for block in content
                      if isinstance(block, dict) and isinstance(block.get("text"), str)]
    return any(_MODEL_COMMAND in text for text in texts)


def transcript_model(path: Path) -> str:
    """The model of the last main-thread assistant turn; SourceError when that is not decidable.

    Only the tail is read (``TRANSCRIPT_TAIL_BYTES``). A ``/model`` command after the last turn,
    an unreadable line before the answer, or no assistant turn in the tail fails closed.
    """
    try:
        if not path.exists() and not path.is_symlink():
            raise SourceError(f"{path.name}: resume transcript missing")
        if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
            raise SourceError(f"{path.name}: symlink or reparse point")
        with path.open("rb") as stream:
            start = max(0, stream.seek(0, os.SEEK_END) - TRANSCRIPT_TAIL_BYTES)
            stream.seek(start)
            data = stream.read(TRANSCRIPT_TAIL_BYTES)
    except OSError as exc:
        raise SourceError(f"{path.name}: unreadable ({exc.__class__.__name__})") from None
    lines = data.split(b"\n")
    if start > 0:
        lines = lines[1:]                       # the first line of a tail may be cut
    for raw in reversed(lines):
        if not raw.strip():
            continue
        try:
            record = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            raise SourceError(f"{path.name}: unreadable line before the last turn") from None
        if not isinstance(record, dict):
            raise SourceError(f"{path.name}: a line that is not an object")
        # The CLI records /model as a user record or as a system/local_command record with the
        # command at the top level (claude-rco-2 B3 on #1745); any non-assistant record with the
        # marker after the last turn is undecidable.
        if record.get("type") != "assistant" and _is_model_command(record):
            raise SourceError(f"{path.name}: /model after the last turn")
        if record.get("type") != "assistant" or record.get("isSidechain") is True:
            continue
        message = record.get("message")
        model = message.get("model") if isinstance(message, dict) else None
        if model == "<synthetic>":
            continue
        if not _text(model):
            raise SourceError(f"{path.name}: the last turn names no model")
        return _text(model)
    raise SourceError(f"{path.name}: no assistant turn in the transcript tail")


def managed_registry_settings(keys: tuple[tuple[str, str], ...] = MANAGED_REGISTRY_KEYS) -> list[str]:
    """Hives whose Claude Code policy key carries managed settings.

    Returns ``[]`` off Windows or when no key exists. ``"HKLM"`` or ``"HKCU"``
    marks a present, non-empty ``Settings`` value; ``"<hive>:unreadable"`` marks
    a key or value that exists but cannot be read. Both count as managed.
    """
    if sys.platform != "win32":
        return []
    import winreg
    hives = {"HKLM": winreg.HKEY_LOCAL_MACHINE, "HKCU": winreg.HKEY_CURRENT_USER}
    found: list[str] = []
    for hive_name, path in keys:
        try:
            with winreg.OpenKey(hives[hive_name], path) as key:
                value, _ = winreg.QueryValueEx(key, MANAGED_REGISTRY_VALUE)
        except FileNotFoundError:
            continue
        except OSError:
            found.append(f"{hive_name}:unreadable")
            continue
        if value not in (None, "") and not (isinstance(value, str) and not value.strip()):
            found.append(hive_name)
    return found


def normalize_claude_model(value: Any) -> tuple[str | None, str | None]:
    """(model id, issue): strip a context suffix; aliases are unresolved, never guessed."""
    text = _text(value)
    if text is None:
        return None, "model_missing"
    suffix = _SUFFIX.search(text)
    if suffix is not None and suffix.group(0).lower() not in KNOWN_MODEL_SUFFIXES:
        # Only "[1m]" is documented (Grok third-family review of #1745); never guess another away.
        return None, f"model_suffix_unknown:{suffix.group(0)}"
    base = _SUFFIX.sub("", text)
    if not base.strip():
        return None, "model_missing"
    if base.lower() in CLAUDE_ALIASES:
        return None, f"model_alias_unresolved:{base}"
    return base, None


def canonical_claude_model(model_id: str) -> str:
    """The ``modelSettings`` key for a model id: no ``[1m]`` suffix, no date suffix (documented matching)."""
    return _DATE.sub("", _SUFFIX.sub("", model_id))


def _model_entries(name: str, value: dict, key: str, issues: list[str]) -> list[dict]:
    """This model's ``modelSettings`` entry in one settings file (at most one: keys are unique).

    Claude Code writes entries "under the model's canonical name" and matches the model's
    suffixed and dated ids to that entry. Whether it also reads a suffixed, dated or alias KEY
    is not documented, so such a key for this model fails closed.
    """
    entries = value.get("modelSettings")
    if entries is None:
        return []
    if not isinstance(entries, dict):
        issues.append(f"modelSettings_not_an_object_in_{name}")
        return []
    found: list[dict] = []
    for entry_key, entry in entries.items():
        entry_model, key_issue = normalize_claude_model(entry_key)
        if key_issue:
            # An alias or blank key may name this model.
            issues.append(f"modelSettings_key_unresolved_in_{name}")
            continue
        if canonical_claude_model(entry_model) != key:
            continue
        if entry_key != key:
            issues.append(f"modelSettings_key_not_canonical_in_{name}")
            continue
        if not isinstance(entry, dict):
            issues.append(f"modelSettings_entry_not_an_object_in_{name}")
            continue
        found.append(entry)
    return found


def _uncertain_entries(value: dict, key: str) -> list[tuple[str, dict]]:
    """``modelSettings`` entries whose key may or may not name this model: an alias or blank key,
    or a suffixed or dated key that canonicalizes to it."""
    entries = value.get("modelSettings")
    if not isinstance(entries, dict):
        return []
    found = []
    for entry_key, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        entry_model, key_issue = normalize_claude_model(entry_key)
        if key_issue or (canonical_claude_model(entry_model) == key and entry_key != key):
            found.append((entry_key, entry))
    return found


def _malformed_entry_for(value: dict, key: str) -> bool:
    """True when a ``modelSettings`` entry that is or may be this model's is not an object."""
    entries = value.get("modelSettings")
    if not isinstance(entries, dict):
        return False
    for entry_key, entry in entries.items():
        if isinstance(entry, dict):
            continue
        entry_model, key_issue = normalize_claude_model(entry_key)
        if key_issue or canonical_claude_model(entry_model) == key:
            return True
    return False


def _settings_effort(layers: list[tuple[str, dict]], model_id: str | None,
                     issues: list[str]) -> tuple[str | None, str]:
    """(effort, source) from settings files, or (None, source) with an issue when not decidable."""
    local: list[str] = []
    effort, source = None, "builtin_default"
    key = canonical_claude_model(model_id) if model_id is not None else None
    for name, value in layers:
        saved = None
        for entry in _model_entries(name, value, key, local) if key is not None else []:
            if "effortLevel" in entry:
                saved = _text(entry["effortLevel"])
                if saved is None:
                    local.append(f"modelSettings_effortLevel_not_a_string_in_{name}")
        top = _text(value.get("effortLevel"))
        if top and name == "user" and not saved:
            if key in USER_EFFORT_LEVEL_IGNORED:
                top = None
            elif key not in USER_EFFORT_LEVEL_APPLIES:
                local.append("user_effort_level_applicability_unverified")
                source = name
                break
        if saved:
            effort, source = saved, f"{name}:modelSettings"
            break
        if top:
            effort, source = top, name
            break
    if effort is not None and effort not in SETTINGS_EFFORT_LEVELS:
        # e.g. "max" or "ultracode": not accepted in a settings file; what the CLI does instead
        # is not documented (claude-rco-1 NB1 on #1744).
        local.append(f"effortLevel_not_accepted:{effort}")
    issues.extend(local)
    return (None if local else effort), source


def _effort_caps(layers: list[tuple[str, dict]], model_id: str | None, issues: list[str]) -> None:
    """Any ``maxEffortLevel`` below ``max`` that may apply: caps lower every source; fail closed."""
    key = canonical_claude_model(model_id) if model_id is not None else None
    for name, value in layers:
        caps = [value.get("maxEffortLevel")]
        if key is not None:
            own = [entry["maxEffortLevel"] for entry in _model_entries(name, value, key, [])
                   if "maxEffortLevel" in entry]
            if own:
                # "That entry replaces this key for the model only within the settings source
                # that sets both" (settings-reference maxEffortLevel; claude-rco-1 NB3 on #1744).
                caps = own
            # A cap under a key that MAY name this model (alias, suffixed or dated key) is not
            # documented as read or ignored, so it counts (Grok third-family review of #1745).
            caps += [entry.get("maxEffortLevel") for entry_key, entry in _uncertain_entries(value, key)]
            if "modelSettings" in value and not isinstance(value["modelSettings"], dict):
                caps.append("undecidable")
            elif _malformed_entry_for(value, key):
                # A non-object entry that may be this model's: its cap is undecidable
                # (codex-tools-1 B4 on #1745 at 638299db).
                caps.append("undecidable")
        elif isinstance(value.get("modelSettings"), dict):
            # Unknown model: any per-model cap in the file may be its own.
            caps += [entry.get("maxEffortLevel") for entry in value["modelSettings"].values()
                     if isinstance(entry, dict)]
        if any(cap is not None and cap != "max" for cap in caps):
            issues.append(f"effort_cap_in_{name}")


def _thinking_issues(layers: list[tuple[str, dict]], model_id: str | None, env: Mapping[str, str]) -> list[str]:
    """Issues when thinking may be off for this model (claude-rco-1 NB4 on #1744)."""
    if model_id is None:
        return []
    key = canonical_claude_model(model_id)
    if key in THINKING_ALWAYS_ON or key.startswith(THINKING_ALWAYS_ON_PREFIXES):
        return []
    issues: list[str] = []
    budget = env.get("MAX_THINKING_TOKENS")
    if _text(env.get("CLAUDE_CODE_DISABLE_THINKING")) not in (None, "0"):
        # Omits the thinking parameter; "the model may still think" - not decidable.
        issues.append("thinking_parameter_omitted:CLAUDE_CODE_DISABLE_THINKING")
    if budget is not None:
        text = _text(budget)
        digits = text is not None and text.isascii() and text.isdigit()
        if digits and int(text) == 0:          # "0", "00", "0000": all zero
            issues.append("thinking_off:MAX_THINKING_TOKENS")
        elif not digits:
            issues.append("thinking_budget_unreadable:MAX_THINKING_TOKENS")
        return issues                       # a positive budget turns thinking on over the setting
    for name, value in layers:
        if "alwaysThinkingEnabled" in value:   # a plain key: the highest-precedence file decides
            if value["alwaysThinkingEnabled"] is False:
                issues.append(f"thinking_off:alwaysThinkingEnabled_in_{name}")
            elif value["alwaysThinkingEnabled"] is not True:
                issues.append(f"alwaysThinkingEnabled_not_a_boolean_in_{name}")
            break
    return issues


def _result(provider: str, model: str | None, effort: str | None, model_source: str, effort_source: str,
            issues: list[str], sources: list[dict]) -> dict:
    return {"schema": SCHEMA, "provider": provider, "model": model, "effort": effort,
            "model_source": model_source, "effort_source": effort_source,
            "resolved": model is not None and effort is not None and not issues,
            "issues": issues, "sources": sources}


def resolve_claude(*, argv_model: str | None, argv_effort: str | None, env: Mapping[str, str],
                   user_settings: Path, worktree: Path | None, cli_settings: Path | None = None,
                   managed_settings: Path = DEFAULT_CLAUDE_MANAGED,
                   managed_registry: Callable[[], list[str]] | None = None,
                   resume_transcript: Path | None = None) -> dict:
    """The model and effort a Claude Code launch will start with, and where each comes from."""
    issues: list[str] = []
    sources: list[dict] = []
    layers: list[tuple[str, dict]] = []   # settings files, highest precedence first
    candidates = [("managed", managed_settings)]
    if cli_settings is not None:
        candidates.append(("cli_settings", cli_settings))
    if worktree is not None:
        candidates += [("project_local", worktree / ".claude" / "settings.local.json"),
                       ("project", worktree / ".claude" / "settings.json")]
    candidates.append(("user", user_settings))
    # Server-managed settings from the claude.ai console are cached next to the user file.
    candidates.append(("managed_remote_cache", user_settings.parent / "remote-settings.json"))
    for name, path in candidates:
        try:
            value = _json(path)
        except SourceError as exc:
            issues.append(f"{name}_unreadable")
            sources.append({"layer": name, "path": str(path), "state": "unreadable", "detail": str(exc)})
            continue
        if value is None:
            continue
        sources.append({"layer": name, "path": str(path), "state": "read"})
        layers.append((name, value))
    try:
        # Looked up at call time, so the CLI path can be isolated in tests.
        registry_hits = list((managed_registry or managed_registry_settings)())
    except Exception as exc:  # noqa: BLE001 - a policy store we cannot read is not "no policy"
        registry_hits = [f"registry:unreadable:{exc.__class__.__name__}"]
    for hit in registry_hits:
        sources.append({"layer": "managed_registry", "path": hit, "state": "present"})
    if any(name in MANAGED_LAYERS for name, _ in layers) or registry_hits:
        # Managed settings can pin or cap model and effort; we do not model them - fail closed.
        issues.append("managed_settings_present")
    layers = [(name, value) for name, value in layers if name not in MANAGED_LAYERS]
    for name, value in layers:
        block = value.get("env")
        if isinstance(block, dict):
            for key in ENV_MODEL_KEYS:
                if key in block:
                    # A settings env block sets these for the session; its order against the
                    # process environment and the file keys is not documented - fail closed.
                    issues.append(f"env_block_sets_{key}_in_{name}")
        for key in ("model", "effortLevel"):
            if key in value and not isinstance(value[key], str):
                issues.append(f"{key}_not_a_string_in_{name}")
            elif key in value and not value[key].strip():
                # Whether the CLI treats a blank value as unset is not documented (Grok review).
                issues.append(f"{key}_blank_in_{name}")
        if "ultracode" in value and not isinstance(value["ultracode"], bool):
            issues.append(f"ultracode_not_a_boolean_in_{name}")
        if "availableModels" in value:
            # "Any file" scope. A blocked model setting (or restored resume model) is replaced by
            # the default model at startup (model-config.md "Restrict model selection"); its
            # alias, prefix and substitution matching is not modelled here - fail closed.
            issues.append(f"available_models_in_{name}")

    for key in THIRD_PARTY_ENV:
        if _text(env.get(key)):
            issues.append(f"third_party_provider:{key}")

    # ---- model
    model, model_source = None, "builtin_default"
    if _text(argv_model) not in NATIVE:
        model, model_source = argv_model, "argv"
    elif _text(env.get("ANTHROPIC_MODEL")):
        model, model_source = env["ANTHROPIC_MODEL"], "env:ANTHROPIC_MODEL"
    else:
        for name, value in layers:
            if _text(value.get("model")):
                model, model_source = value["model"], name
                break
        else:
            if _text(env.get("ANTHROPIC_DEFAULT_MODEL")):
                model, model_source = env["ANTHROPIC_DEFAULT_MODEL"], "env:ANTHROPIC_DEFAULT_MODEL"
    if resume_transcript is not None and model_source not in RESUME_WINS_OVER_TRANSCRIPT:
        model, model_source = None, "resume_transcript"
        if any(_text(env.get(key)) for key in MODEL_FAMILY_ENV):
            issues.append("resume_model_family_variable_unverified")
            sources.append({"layer": "resume_transcript", "path": str(resume_transcript), "state": "not_read"})
        else:
            try:
                model = transcript_model(resume_transcript)
                sources.append({"layer": "resume_transcript", "path": str(resume_transcript), "state": "read"})
            except SourceError as exc:
                issues.append("resume_transcript_model_undecidable")
                sources.append({"layer": "resume_transcript", "path": str(resume_transcript),
                                "state": "unreadable", "detail": str(exc)})
    if model_source == "builtin_default":
        issues.append("model_from_unpinned_builtin_default")
        model_id = None
    elif model is None:
        model_id = None
    else:
        model_id, issue = normalize_claude_model(model)
        if issue:
            issues.append(issue)

    # ---- effort
    effort, effort_source = None, "builtin_default"
    env_effort = _text(env.get("CLAUDE_CODE_EFFORT_LEVEL"))
    argv_level = _text(argv_effort) if _text(argv_effort) not in NATIVE else None
    ultracode, ultracode_layer = None, None
    for name, value in layers:
        if "ultracode" in value:           # a plain key: the highest-precedence file that sets it
            ultracode, ultracode_layer = value["ultracode"], name
            break
    if env_effort:
        # "CLAUDE_CODE_EFFORT_LEVEL takes precedence over both" (--effort and effortLevel).
        effort, effort_source = env_effort, "env:CLAUDE_CODE_EFFORT_LEVEL"
        if env_effort == "ultracode":
            issues.append("env_effort_ultracode_not_accepted")    # documented: the variable rejects it
            effort = None
    elif argv_level:
        effort, effort_source = argv_level, "argv"
        if argv_level == "ultracode":
            effort = "xhigh"               # "starts the session at xhigh effort with ultracode on"
        elif ultracode is True and argv_level != "xhigh":
            issues.append("argv_effort_against_ultracode_setting_unverified")
            effort = None
    elif ultracode is True:
        # "Ultracode runs the session at xhigh effort and takes precedence over effortLevel and modelSettings."
        effort, effort_source = "xhigh", f"ultracode:{ultracode_layer}"
    else:
        effort, effort_source = _settings_effort(layers, model_id, issues)
    _effort_caps(layers, model_id, issues)
    issues.extend(_thinking_issues(layers, model_id, env))
    if effort_source == "builtin_default":
        issues.append("effort_from_model_tuned_default")
    if effort == "auto":
        issues.append("effort_auto_is_model_tuned_default")
        effort = None
    return _result("claude", model_id, effort, model_source, effort_source, issues, sources)


# A provider override sends the same model name to another provider or endpoint (OpenAI Codex docs,
# config-advanced: model_provider, [model_providers.<id>], openai_base_url). The fleet uses the built-in
# openai provider; anything else fails closed (Grok third-family review of #1745).
CODEX_PROVIDER_KEYS = ("model_provider", "model_providers", "openai_base_url")
CODEX_PROVIDER_ENV = ("OPENAI_BASE_URL",)


def resolve_codex(*, argv_model: str | None, argv_effort: str | None, config: Path,
                  worktree: Path | None = None, env: Mapping[str, str] | None = None) -> dict:
    """The model and effort a Codex CLI launch will start with, and where each comes from."""
    issues: list[str] = []
    sources: list[dict] = []
    try:
        cfg = _toml(config)
        sources.append({"layer": "user_config", "path": str(config), "state": "read" if cfg is not None else "absent"})
    except SourceError as exc:
        cfg = None
        issues.append("user_config_unreadable")
        sources.append({"layer": "user_config", "path": str(config), "state": "unreadable", "detail": str(exc)})
    cfg = cfg or {}
    for key in ("profile", "profiles"):
        if key in cfg:
            # "In Codex 0.134.0 and later, --profile no longer reads [profiles.profile-name] from
            # config.toml, and the top-level profile = "profile-name" selector is no longer
            # supported." What the CLI does with them is not verified - fail closed.
            issues.append(f"codex_legacy_profile_unsupported:{key}")
    for key in CODEX_PROVIDER_KEYS:
        if key in cfg and not (key == "model_provider" and cfg[key] == "openai"):
            issues.append(f"codex_provider_override:{key}")
    for key in CODEX_PROVIDER_ENV:
        if _text((env or {}).get(key)):
            issues.append(f"codex_provider_override:{key}")
    if worktree is not None:
        project = worktree / ".codex" / "config.toml"
        try:
            project_cfg = _toml(project)
        except SourceError as exc:
            project_cfg = None
            issues.append("project_config_unreadable")
            sources.append({"layer": "project_config", "path": str(project), "state": "unreadable", "detail": str(exc)})
        if project_cfg is not None:
            sources.append({"layer": "project_config", "path": str(project), "state": "read"})
            if any(key in project_cfg for key in ("model", "model_reasoning_effort", "profile", "profiles",
                                                  *CODEX_PROVIDER_KEYS)):
                issues.append("project_config_precedence_unverified")

    for key in ("model", "model_reasoning_effort"):
        if key in cfg and not isinstance(cfg[key], str):
            issues.append(f"{key}_not_a_string_in_user_config")

    def pick(argv: str | None, key: str) -> tuple[str | None, str]:
        if _text(argv) not in NATIVE:
            return argv, "argv"
        if _text(cfg.get(key)):
            return cfg[key], "user_config"
        return None, "builtin_default"

    model, model_source = pick(argv_model, "model")
    effort, effort_source = pick(argv_effort, "model_reasoning_effort")
    if model_source == "builtin_default":
        issues.append("model_from_unpinned_builtin_default")
    if effort_source == "builtin_default":
        issues.append("effort_from_model_tuned_default")
    return _result("codex", _text(model), _text(effort), model_source, effort_source, issues, sources)


def classify(catalog: dict, lane: str, resolved: dict) -> dict:
    """``allowed`` | ``below_floor`` | ``not_in_lane_allowlist`` | ``provider_mismatch`` | ``unknown``."""
    spec = catalog["lanes"].get(lane)
    if spec is None:
        return {"verdict": "unknown", "reasons": ["lane_not_in_catalog"], "profile": None}
    profiles = catalog["capacity_policy"]["profiles"]
    allowed = spec["allowed_profiles"]
    provider = profiles[allowed[0]]["provider"]
    if not resolved.get("resolved"):
        return {"verdict": "unknown", "reasons": list(resolved.get("issues") or ["unresolved"]), "profile": None}
    if resolved["provider"] != provider:
        return {"verdict": "provider_mismatch", "reasons": [f"lane_provider_{provider}"], "profile": None}
    for index, profile_id in enumerate(allowed):
        profile = profiles[profile_id]
        if (profile["model"], profile["effort"]) == (resolved["model"], resolved["effort"]):
            if index > spec["floor"]:
                return {"verdict": "below_floor", "reasons": [f"index_{index}_floor_{spec['floor']}"],
                        "profile": profile_id}
            return {"verdict": "allowed", "reasons": ["default" if profile_id == spec["default"] else "in_allowlist"],
                    "profile": profile_id}
    elsewhere = [pid for pid, p in profiles.items()
                 if (p["provider"], p["model"], p["effort"]) == (provider, resolved["model"], resolved["effort"])]
    return {"verdict": "not_in_lane_allowlist",
            "reasons": ["in_catalog_for_other_lanes" if elsewhere else "not_in_catalog"], "profile": None}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lane", required=True, choices=LANES)
    parser.add_argument("--cli", required=True, choices=("claude", "codex"))
    parser.add_argument("--argv-model", default="native")
    parser.add_argument("--argv-effort", default="native")
    parser.add_argument("--worktree", default=None)
    parser.add_argument("--catalog", default=str(DEFAULT_CATALOG))
    parser.add_argument("--claude-user-settings", default=str(claude_config_dir(os.environ) / "settings.json"))
    parser.add_argument("--claude-resume-thread", default=None,
                        help="the session a Claude launch resumes; needs --worktree")
    parser.add_argument("--claude-cli-settings", default=None)
    parser.add_argument("--claude-managed-settings", default=str(DEFAULT_CLAUDE_MANAGED))
    parser.add_argument("--codex-config", default=str(codex_home(os.environ) / "config.toml"))
    args = parser.parse_args(argv)
    try:
        catalog, digest = load_catalog(args.catalog)
    except (OSError, ValueError) as exc:
        print(json.dumps({"schema": SCHEMA, "error": f"{exc.__class__.__name__}: {exc}"}))
        return EXIT_ERROR
    worktree = Path(args.worktree) if args.worktree else None
    resume = None
    if args.claude_resume_thread is not None:
        try:
            if worktree is None:
                raise SourceError("--claude-resume-thread needs --worktree")
            resume = claude_transcript_path(claude_config_dir(os.environ), worktree, args.claude_resume_thread)
        except SourceError as exc:
            print(json.dumps({"schema": SCHEMA, "error": f"SourceError: {exc}"}))
            return EXIT_ERROR
    if args.cli == "claude":
        resolved = resolve_claude(argv_model=args.argv_model, argv_effort=args.argv_effort, env=os.environ,
                                  user_settings=Path(args.claude_user_settings), worktree=worktree,
                                  cli_settings=Path(args.claude_cli_settings) if args.claude_cli_settings else None,
                                  managed_settings=Path(args.claude_managed_settings), resume_transcript=resume)
    else:
        resolved = resolve_codex(argv_model=args.argv_model, argv_effort=args.argv_effort,
                                 config=Path(args.codex_config), worktree=worktree, env=os.environ)
    verdict = classify(catalog, args.lane, resolved)
    print(json.dumps(dict(resolved, lane=args.lane, catalog_sha256=digest, classification=verdict),
                     indent=2, sort_keys=True))
    return EXIT_ALLOWED if verdict["verdict"] == "allowed" else EXIT_ATTENTION


if __name__ == "__main__":
    sys.exit(main())
