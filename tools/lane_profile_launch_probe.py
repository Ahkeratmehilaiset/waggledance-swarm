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
failure prints a ``native`` line and exits 0, so a broken record, catalog or
log can never block or alter a launch.

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
    args = parser.parse_args(argv)
    entry = probe(args.runtime_root, args.lane, args.launcher, args.argv_model, args.argv_effort,
                  catalog_path=args.catalog)
    entry["log"] = append_entry(args.runtime_root, entry)
    print(json.dumps(entry, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
