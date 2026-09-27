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
* effort: managed settings > ``--effort`` > ``--settings`` > local > project >
  user. In a settings file the per-model ``modelSettings.<model>.effortLevel``
  and the global ``effortLevel`` may both be present. When they disagree, the
  precedence between them is not documented, so the result is ambiguous and
  therefore unknown.

Codex CLI: ``--model`` and ``-c model_reasoning_effort=`` on argv >
``config.toml``'s selected ``[profiles.<profile>]`` > top-level ``model`` and
``model_reasoning_effort`` > built-in default. A project ``.codex/config.toml``
inside the worktree is reported as a source whose precedence is not verified;
when it sets a value, the result is unknown.

Fail closed
-----------
A value from a built-in default, an unresolved alias (``opus``, ``sonnet``,
``default`` ...), an unreadable or ambiguous source, or a managed-settings file
yields ``None``. ``classify`` then gives ``unknown``, never a guess.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import tomllib
from typing import Any, Mapping

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.lane_profile_catalog import LANES, load_catalog  # noqa: E402

SCHEMA = "wd.lane-effective-model.v1"
MAX_SOURCE_BYTES = 1024 * 1024
NATIVE = ("", "native", None)
CLAUDE_ALIASES = frozenset({"opus", "sonnet", "haiku", "fable", "default", "opusplan", "best", "latest"})
_SUFFIX = re.compile(r"\[[^\]]*\]$")
EXIT_ALLOWED, EXIT_ERROR, EXIT_ATTENTION = 0, 2, 3
DEFAULT_CATALOG = Path(__file__).resolve().parents[1] / "configs" / "lane_profile_catalog.json"
# Documented Windows location of Claude Code managed settings.
DEFAULT_CLAUDE_MANAGED = Path(r"C:\Program Files\ClaudeCode\managed-settings.json")


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
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise SourceError(f"{path.name}: not TOML ({exc.__class__.__name__})") from None


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def normalize_claude_model(value: Any) -> tuple[str | None, str | None]:
    """(model id, issue): strip a context suffix; aliases are unresolved, never guessed."""
    text = _text(value)
    if text is None:
        return None, "model_missing"
    base = _SUFFIX.sub("", text)
    if base.lower() in CLAUDE_ALIASES:
        return None, f"model_alias_unresolved:{base}"
    return base, None


def _result(provider: str, model: str | None, effort: str | None, model_source: str, effort_source: str,
            issues: list[str], sources: list[dict]) -> dict:
    return {"schema": SCHEMA, "provider": provider, "model": model, "effort": effort,
            "model_source": model_source, "effort_source": effort_source,
            "resolved": model is not None and effort is not None and not issues,
            "issues": issues, "sources": sources}


def resolve_claude(*, argv_model: str | None, argv_effort: str | None, env: Mapping[str, str],
                   user_settings: Path, worktree: Path | None, cli_settings: Path | None = None,
                   managed_settings: Path = DEFAULT_CLAUDE_MANAGED) -> dict:
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
    if any(name == "managed" for name, _ in layers):
        # Managed settings can pin or cap model and effort; we do not model them - fail closed.
        issues.append("managed_settings_present")

    # ---- model
    model, model_source = None, "builtin_default"
    if _text(argv_model) not in NATIVE:
        model, model_source = argv_model, "argv"
    elif _text(env.get("ANTHROPIC_MODEL")):
        model, model_source = env["ANTHROPIC_MODEL"], "env:ANTHROPIC_MODEL"
    else:
        for name, value in layers:
            if name != "managed" and _text(value.get("model")):
                model, model_source = value["model"], name
                break
        else:
            if _text(env.get("ANTHROPIC_DEFAULT_MODEL")):
                model, model_source = env["ANTHROPIC_DEFAULT_MODEL"], "env:ANTHROPIC_DEFAULT_MODEL"
    if model_source == "builtin_default":
        issues.append("model_from_unpinned_builtin_default")
        model_id = None
    else:
        model_id, issue = normalize_claude_model(model)
        if issue:
            issues.append(issue)

    # ---- effort
    effort, effort_source = None, "builtin_default"
    if _text(argv_effort) not in NATIVE:
        effort, effort_source = argv_effort, "argv"
    else:
        for name, value in layers:
            if name == "managed":
                continue
            per_model = None
            model_settings = value.get("modelSettings")
            if model_id is not None and isinstance(model_settings, dict):
                entry = model_settings.get(model_id)
                per_model = _text(entry.get("effortLevel")) if isinstance(entry, dict) else None
            global_level = _text(value.get("effortLevel"))
            if per_model and global_level and per_model != global_level:
                issues.append(f"effort_ambiguous_in_{name}")
                effort_source = name
                break
            if per_model or global_level:
                effort, effort_source = per_model or global_level, name
                break
    if effort_source == "builtin_default":
        issues.append("effort_from_model_tuned_default")
    if effort == "auto":
        issues.append("effort_auto_is_model_tuned_default")
        effort = None
    return _result("claude", model_id, effort, model_source, effort_source, issues, sources)


def resolve_codex(*, argv_model: str | None, argv_effort: str | None, config: Path,
                  worktree: Path | None = None) -> dict:
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
    selected: dict = {}
    profile_name = cfg.get("profile")
    if profile_name is not None:
        profiles = cfg.get("profiles")
        entry = profiles.get(profile_name) if isinstance(profiles, dict) and isinstance(profile_name, str) else None
        if isinstance(entry, dict):
            selected = entry
        else:
            issues.append("selected_profile_missing")
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
            if any(key in project_cfg for key in ("model", "model_reasoning_effort", "profile")):
                issues.append("project_config_precedence_unverified")

    def pick(argv: str | None, key: str) -> tuple[str | None, str]:
        if _text(argv) not in NATIVE:
            return argv, "argv"
        if _text(selected.get(key)):
            return selected[key], f"profile:{profile_name}"
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
    parser.add_argument("--claude-user-settings", default=str(Path.home() / ".claude" / "settings.json"))
    parser.add_argument("--claude-cli-settings", default=None)
    parser.add_argument("--claude-managed-settings", default=str(DEFAULT_CLAUDE_MANAGED))
    parser.add_argument("--codex-config", default=str(Path.home() / ".codex" / "config.toml"))
    args = parser.parse_args(argv)
    try:
        catalog, digest = load_catalog(args.catalog)
    except (OSError, ValueError) as exc:
        print(json.dumps({"schema": SCHEMA, "error": f"{exc.__class__.__name__}: {exc}"}))
        return EXIT_ERROR
    worktree = Path(args.worktree) if args.worktree else None
    if args.cli == "claude":
        resolved = resolve_claude(argv_model=args.argv_model, argv_effort=args.argv_effort, env=os.environ,
                                  user_settings=Path(args.claude_user_settings), worktree=worktree,
                                  cli_settings=Path(args.claude_cli_settings) if args.claude_cli_settings else None,
                                  managed_settings=Path(args.claude_managed_settings))
    else:
        resolved = resolve_codex(argv_model=args.argv_model, argv_effort=args.argv_effort,
                                 config=Path(args.codex_config), worktree=worktree)
    verdict = classify(catalog, args.lane, resolved)
    print(json.dumps(dict(resolved, lane=args.lane, catalog_sha256=digest, classification=verdict),
                     indent=2, sort_keys=True))
    return EXIT_ALLOWED if verdict["verdict"] == "allowed" else EXIT_ATTENTION


if __name__ == "__main__":
    sys.exit(main())
