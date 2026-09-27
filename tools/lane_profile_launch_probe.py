#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Launcher shadow read of the D2 lane profile record (lane profile switching PR-4).

start-wd-agent.ps1 and start-wd-tools-consumer.ps1 run this through the pinned
bridge package just before they build a lane's argv. It answers the launcher's
question with ``launch_decision`` and appends one line to
``<runtime_root>/lane_profiles/launch-shadow.jsonl``. That line is the shadow
evidence the catalog's exit criterion counts.

It is a read, not a switch. The launcher always keeps its own model, effort and
argv; it never reads this tool's output to decide anything. Even a decision of
``apply`` (possible only in a future signed ``auto`` catalog) is logged as
``apply_suppressed``, because PR-4 wires the read and nothing else. Every
failure prints a ``native`` line, so a broken record, catalog or log can never
block or alter a launch.

Launch preflight (PR-7b): given ``--cli``, the entry also records the model and
effort the launch will ACTUALLY get and where each comes from
(``tools/lane_effective_model``), classified against the catalog. The exit code
is the only signal the launcher reads: ``0`` when the effective profile is
``allowed`` (or no ``--cli`` was given), ``3`` ("attention") for anything else,
including a preflight that could not be evaluated. The launcher answers ``3``
with one bridge event and still launches (alert mode); refusing is a later,
separately signed step.

See docs/BRIDGE_LANE_PROFILES.md.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SCHEMA = "wd.lane-profile-launch-shadow.v1"
LOG_NAME = "launch-shadow.jsonl"
MAX_LOG_BYTES = 4 * 1024 * 1024
LAUNCHERS = ("start-wd-agent", "start-wd-tools-consumer")
CLIS = ("claude", "codex")
EXIT_OK, EXIT_ATTENTION = 0, 3
EFFECTIVE_KEYS = ("provider", "model", "effort", "model_source", "effort_source", "resolved", "issues")
DEFAULT_CATALOG = Path(__file__).resolve().parents[1] / "configs" / "lane_profile_catalog.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _short(value: Any, limit: int = 160) -> str:
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "..."


def probe(runtime_root: str | Path, lane: str, launcher: str, argv_model: str, argv_effort: str, *,
          catalog_path: str | Path = DEFAULT_CATALOG, now: datetime | None = None) -> dict:
    """The shadow entry for one launch. Never raises; the launch stays native."""
    now = now or _now()
    entry: dict[str, Any] = {
        "schema": SCHEMA, "ts_utc": _iso(now), "lane": lane, "launcher": launcher,
        "argv_model": argv_model, "argv_effort": argv_effort,
        "launch": "native_argv_unchanged", "mode": None, "decision_action": None,
        "would_apply": None, "apply_suppressed": False, "fallback_event": None,
        "catalog_sha256": None,
    }
    try:
        from tools.lane_profile_catalog import load_catalog
        from tools.lane_profile_record import launch_decision
        catalog, digest = load_catalog(catalog_path)
    except Exception as exc:  # noqa: BLE001 - an unusable catalog means a native launch
        entry["fallback_event"] = {"reason": "catalog_unusable",
                                   "detail": _short(f"{exc.__class__.__name__}: {exc}")}
        return entry
    entry["catalog_sha256"] = digest
    try:
        decision = launch_decision(runtime_root, lane, catalog, digest, now=now)
    except Exception as exc:  # noqa: BLE001 - launch_decision never raises; belt and braces
        entry["fallback_event"] = {"reason": "decision_failed", "detail": exc.__class__.__name__}
        return entry
    entry.update(mode=decision.get("mode"), decision_action=decision.get("action"),
                 would_apply=decision.get("would_apply"), fallback_event=decision.get("fallback_event"))
    if decision.get("action") == "apply":
        entry["apply_suppressed"] = True  # PR-4 is a shadow read: the launcher never applies
    return entry


def preflight(catalog_path: str | Path, lane: str, cli: str, argv_model: str, argv_effort: str, *,
              worktree: str | Path | None = None, claude_cli_settings: str | Path | None = None,
              codex_config: str | Path | None = None, claude_user_settings: str | Path | None = None,
              claude_managed_settings: str | Path | None = None, env: dict | None = None) -> dict:
    """The effective launch profile and its catalog verdict. Never raises: failure is ``unknown``."""
    result: dict[str, Any] = {"cli": cli, "effective": None, "verdict": "unknown", "reasons": [], "profile": None}
    try:
        from tools.lane_effective_model import DEFAULT_CLAUDE_MANAGED, classify, resolve_claude, resolve_codex
        from tools.lane_profile_catalog import load_catalog
        catalog, _ = load_catalog(catalog_path)
        tree = Path(worktree) if worktree else None
        if cli == "claude":
            resolved = resolve_claude(
                argv_model=argv_model, argv_effort=argv_effort, env=os.environ if env is None else env,
                user_settings=Path(claude_user_settings) if claude_user_settings
                else Path.home() / ".claude" / "settings.json",
                worktree=tree, cli_settings=Path(claude_cli_settings) if claude_cli_settings else None,
                managed_settings=Path(claude_managed_settings) if claude_managed_settings else DEFAULT_CLAUDE_MANAGED)
        elif cli == "codex":
            resolved = resolve_codex(argv_model=argv_model, argv_effort=argv_effort, worktree=tree,
                                     config=Path(codex_config) if codex_config else Path.home() / ".codex" / "config.toml")
        else:
            result["reasons"] = ["cli_unknown"]
            return result
        verdict = classify(catalog, lane, resolved)
        result.update(effective={key: resolved[key] for key in EFFECTIVE_KEYS}, verdict=verdict["verdict"],
                      reasons=list(verdict["reasons"]), profile=verdict["profile"])
    except Exception as exc:  # noqa: BLE001 - a preflight that cannot run is attention, never a pass
        result["reasons"] = [f"preflight_failed:{exc.__class__.__name__}"]
    return result


def append_entry(runtime_root: str | Path, entry: dict) -> str:
    """Append one line to the shadow log; return ``logged`` or why it was not."""
    try:
        from tools.lane_profile_record import _refuse_reparse_ancestry
        path = Path(runtime_root) / "lane_profiles" / LOG_NAME
        _refuse_reparse_ancestry(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        _refuse_reparse_ancestry(path)
        if path.exists() and path.stat().st_size >= MAX_LOG_BYTES:
            return "log_full"
        line = (json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0)
        descriptor = os.open(path, flags, 0o644)
        try:
            os.write(descriptor, line)
        finally:
            os.close(descriptor)
        return "logged"
    except Exception as exc:  # noqa: BLE001 - logging never blocks a launch
        return f"log_failed:{exc.__class__.__name__}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--lane", required=True)
    parser.add_argument("--launcher", required=True, choices=LAUNCHERS)
    parser.add_argument("--argv-model", required=True)
    parser.add_argument("--argv-effort", required=True)
    parser.add_argument("--catalog", default=str(DEFAULT_CATALOG))
    parser.add_argument("--cli", choices=CLIS, default=None)
    parser.add_argument("--worktree", default=None)
    parser.add_argument("--claude-cli-settings", default=None)
    parser.add_argument("--codex-config", default=None, help="default: ~/.codex/config.toml")
    parser.add_argument("--claude-user-settings", default=None, help="default: ~/.claude/settings.json")
    args = parser.parse_args(argv)
    entry = probe(args.runtime_root, args.lane, args.launcher, args.argv_model, args.argv_effort,
                  catalog_path=args.catalog)
    entry["preflight"] = None if args.cli is None else preflight(
        args.catalog, args.lane, args.cli, args.argv_model, args.argv_effort,
        worktree=args.worktree, claude_cli_settings=args.claude_cli_settings,
        codex_config=args.codex_config, claude_user_settings=args.claude_user_settings)
    entry["log"] = append_entry(args.runtime_root, entry)
    print(json.dumps(entry, sort_keys=True, separators=(",", ":")))
    attention = entry["preflight"] is not None and entry["preflight"]["verdict"] != "allowed"
    return EXIT_ATTENTION if attention else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
