# SPDX-License-Identifier: BUSL-1.1
"""Read an existing Mind publication for the current agent session.

This command does not start or install a producer and cannot authenticate an
agent.  The compact view must have been obtained by the calling session; the
fleet hash is supplied by the caller and is not, by itself, an authority check.
Only the lane reader's allowlisted projection is printed.  Any refusal is a
fixed token, never raw input, paths, capture rows, or a partial projection.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys

from tools import wd_bridge_mind_lane_read as lane_reader

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_FLEET_LIMIT = 1024 * 1024
_VIEW_LIMIT = 8 * 1024 * 1024


def _bounded_json(path: Path, limit: int) -> tuple[object, bytes]:
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("input_too_large")
    return json.loads(raw.decode("utf-8")), raw


def _fleet_roster(path: Path, expected_sha256: str) -> dict[str, str]:
    if not _SHA256.fullmatch(expected_sha256):
        raise ValueError("fleet_pin_invalid")
    fleet, raw = _bounded_json(path, _FLEET_LIMIT)
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("fleet_pin_mismatch")
    if not isinstance(fleet, dict) or not isinstance(fleet.get("lanes"), list):
        raise ValueError("fleet_invalid")
    roster: dict[str, str] = {}
    for entry in fleet["lanes"]:
        if not isinstance(entry, dict):
            raise ValueError("fleet_invalid")
        agent, uuid = entry.get("agent"), entry.get("agent_uuid")
        if agent in lane_reader.LANES:
            if agent in roster or not isinstance(uuid, str):
                raise ValueError("fleet_invalid")
            roster[agent] = uuid
    if set(roster) != set(lane_reader.LANES):
        raise ValueError("fleet_invalid")
    return roster


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--latest", required=True, type=Path)
    parser.add_argument("--audit-root", required=True, type=Path)
    parser.add_argument("--view", required=True, type=Path,
                        help="caller-obtained compact reader view inside the audit root")
    parser.add_argument("--fleet", required=True, type=Path)
    parser.add_argument("--fleet-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        agent = os.environ.get("AGENT_BRIDGE_AGENT")
        uuid = os.environ.get("AGENT_BRIDGE_AGENT_UUID")
        roster = _fleet_roster(args.fleet, args.fleet_sha256.lower())
        if agent not in roster or not isinstance(uuid, str) or roster[agent].lower() != uuid.lower():
            raise ValueError("session_identity_unmatched")
        view_path = lane_reader._owned(args.view, args.audit_root)
        view, _ = _bounded_json(view_path, _VIEW_LIMIT)
        result = lane_reader.lane_read(latest_path=args.latest, audit_root=args.audit_root,
                                       lane=agent, as_of=datetime.now(timezone.utc), binding=view, roster=roster)
        print(json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=True))
        return 0
    except (OSError, ValueError, UnicodeError, RecursionError):
        print('{"error":"mind_session_read_refused","authority":"none"}', file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
