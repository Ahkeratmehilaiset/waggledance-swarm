# SPDX-License-Identifier: BUSL-1.1
"""Read-only, opt-in attention view, NOT a task selector or approval verifier.

Examples::

    python tools/bridge_compact_view.py --events PATH --tail 5000
    python tools/bridge_compact_view.py --events PATH --after FULL_EVENT_DIGEST
    python tools/bridge_compact_view.py --events PATH --event-id FULL_EVENT_DIGEST

Reuse the bounded stable snapshot reader. Cursor loss (rotation/window eviction)
is explicit: read a fresh view, never silently assume there was no new work.
Digests identify canonical JSON content, not authenticated agent identities.
No ACK, event append, claim sweep, cursor write or audit-log mutation occurs.
The literal private-marker guard is not a general secret or PII scanner.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False)


def event_id(event: Mapping[str, Any]) -> str:
    return hashlib.sha256(_json(event).encode("utf-8")).hexdigest()


def compact_view(events: Sequence[Mapping[str, Any]], *, after: str = "") -> dict:
    """Preserve chronology; deduplicate ONLY byte-equivalent canonical JSON.

    Observations are grouped by exact author/session/task/head, never inferred
    global task status. Explicit supersession is displayed, not used to hide
    requests, decisions, vetoes, or unknown records. Consumers must consult the
    original event and live next-action/claims before taking action.
    """
    if any(not isinstance(row, Mapping) for row in events):
        raise ValueError("event must be an object")
    if any(not isinstance(row.get("type", ""), str) for row in events):
        raise ValueError("event type must be a string")
    ids = [event_id(row) for row in events]
    start = 0
    if after:
        if not re.fullmatch(r"[0-9a-f]{64}", after) or after not in ids:
            raise ValueError("cursor unavailable; fresh bounded view required")
        # First occurrence avoids dropping intervening rows on duplicate replay.
        start = ids.index(after) + 1
    visible = []
    observed = {}
    seen = set()
    heartbeats = duplicates = 0
    for row, ref in zip(events[start:], ids[start:]):
        if ref in seen:
            duplicates += 1
            continue
        seen.add(ref)
        kind = row.get("type", "")
        if kind in {"heartbeat", "liveness"}:
            heartbeats += 1
            continue
        payload = row.get("payload", {})
        payload = payload if isinstance(payload, Mapping) else {}
        bindings = {key: payload[key] for key in ("exact_head", "head_sha", "head")
                    if key in payload}
        head = next(iter(bindings.values()), "")
        item = {
            "ref": ref, "ts": row.get("ts_utc", ""),
            "agent": row.get("agent", ""), "session": row.get("session_id", ""),
            "task": row.get("task_id", ""), "head": head,
            "type": kind, "status": row.get("status", ""),
            "to": row.get("to", ""),
        }
        item["head_bindings"] = bindings
        item["head_conflict"] = len({_json(value) for value in bindings.values()}) > 1
        message = str(row.get("message", ""))
        item["summary"] = message[:240]
        item["requires_detail"] = True
        if "supersedes_event_id" in payload:
            item["supersedes"] = payload["supersedes_event_id"]
        if kind in {"decision", "finding", "blocked", "rco_review"}:
            item["detail"] = dict(row)
        visible.append(item)
        key = _json([item[k] for k in ("agent", "session", "task", "head")])
        observed[key] = {k: item[k] for k in
                         ("agent", "session", "task", "head", "head_bindings",
                          "head_conflict", "status", "ref")}
    return {
        "schema": "wd.bridge.compact-view.v1", "authority": "none",
        "scope": "bounded_snapshot_not_complete_history",
        "cursor": ids[-1] if ids else after,
        "stats": {"snapshot_events": len(events), "delta_events": len(events) - start,
                  "heartbeats": heartbeats, "duplicates": duplicates,
                  "visible_events": len(visible),
                  "source_json_bytes": len(_json(events[start:]).encode("utf-8"))},
        "events": visible, "observations": list(observed.values()),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--tail", type=int, default=5000)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--after", default="")
    group.add_argument("--event-id", default="")
    args = parser.parse_args(argv)
    if not 0 <= args.tail <= 100000:
        parser.error("--tail must be 0..100000 (0 = bounded full snapshot)")
    from tools.bridge_next_action import read_events, BridgeNextActionError
    try:
        rows = read_events(args.events, tail=args.tail)
        if args.event_id:
            matches = [row for row in rows if event_id(row) == args.event_id]
            if not matches:
                raise ValueError("event reference unavailable in bounded snapshot")
            report = {"schema": "wd.bridge.event-detail.v1", "authority": "none",
                      "ref": args.event_id, "event": matches[0]}
        else:
            report = compact_view(rows, after=args.after)
        rendered = _json(report)
        if any(marker in rendered for marker in ("PRIVATE_MARKER", "_DO_NOT_LEAK")):
            raise ValueError("private marker in selected output")
        print(rendered)
        return 0
    except (OSError, ValueError, BridgeNextActionError) as exc:
        # Avoid printing event content in parse errors.
        print(_json({"ok": False, "error": type(exc).__name__,
                     "action": "inspect raw snapshot; do not infer no work"}),
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
