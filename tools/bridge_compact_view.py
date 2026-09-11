# SPDX-License-Identifier: BUSL-1.1
"""Read-only, opt-in attention view, NOT a task selector or approval verifier.

Examples::

    python tools/bridge_compact_view.py --events PATH --tail 5000
    python tools/bridge_compact_view.py --events PATH --after POSITION_CURSOR
    python tools/bridge_compact_view.py --events PATH --event-id FULL_EVENT_DIGEST

Reuse the bounded stable snapshot and position-cursor reader. File rotation or
truncation fails explicitly. Event content hashes are NEVER position cursors.
Initial tails retain the legacy reader's historical normalization; deltas use
the canonical writer's strict complete-JSON-object contract. A newly appended
blank/null/malformed record stops the delta without advancing its cursor.
Digests identify canonical JSON content, not authenticated agent identities.
No ACK, event append, claim sweep, cursor write or audit-log mutation occurs.
The literal private-marker guard is not a general secret or PII scanner.
"""
from __future__ import annotations

import argparse
import base64
from dataclasses import asdict
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


class CompactViewError(ValueError):
    """Fixed diagnostic codes safe to print without echoing source content."""


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
    if after:
        raise ValueError("position cursor requires the log reader, not event content")
    ids = [event_id(row) for row in events]
    visible = []
    observed = {}
    seen = set()
    heartbeats = duplicates = 0
    for row, ref in zip(events, ids):
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
        "last_event_ref": ids[-1] if ids else "",
        "stats": {"snapshot_events": len(events), "delta_events": len(events),
                  "heartbeats": heartbeats, "duplicates": duplicates,
                  "visible_events": len(visible),
                  "source_json_bytes": len(_json(events).encode("utf-8"))},
        "events": visible, "observations": list(observed.values()),
    }


def _read_view_rows(path: Path, tail: int, after: str):
    from waggledance.core.bridge_log_reader import (
        BridgeCursor, BridgeReadStatus, MAX_MAX_BYTES, MAX_MAX_ROWS,
        read_bridge_log, read_bridge_log_tail_lines,
    )
    from tools.bridge_next_action import _parse_selected_bridge_row
    generation_path = path.with_name("events.generation.json")
    if after:
        if len(after) > 2048 or not re.fullmatch(r"bc1\.[A-Za-z0-9_-]+", after):
            raise CompactViewError("position_cursor_invalid")
        encoded = after[4:]
        data = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        if not isinstance(data, dict) or set(data) != {"offset", "file_identity", "generation"}:
            raise CompactViewError("position_cursor_invalid")
        cursor = BridgeCursor(**data)
        result = read_bridge_log(
            path, cursor=cursor, max_bytes=MAX_MAX_BYTES, max_rows=tail or MAX_MAX_ROWS,
            generation_path=generation_path if cursor.generation is not None or generation_path.exists() else None,
        )
        if result.status not in {BridgeReadStatus.OK, BridgeReadStatus.IDLE} or result.candidate_cursor is None:
            raise CompactViewError("position_cursor_unavailable_fresh_snapshot_required")
        rows = list(result.rows)
        next_cursor = result.candidate_cursor
    else:
        result = read_bridge_log_tail_lines(
            path, tail_rows=tail or MAX_MAX_ROWS, max_bytes=MAX_MAX_BYTES,
            generation_path=generation_path,
        )
        if result.status not in {BridgeReadStatus.OK, BridgeReadStatus.IDLE}:
            raise CompactViewError("stable_snapshot_unavailable")
        rows = []
        for line in result.lines:
            if line.strip(" \t\r") in {"", "null"}:
                continue
            rows.extend(_parse_selected_bridge_row(line))
        next_cursor = (BridgeCursor(result.end_offset, result.file_identity, result.generation)
                       if result.end_offset is not None and result.file_identity else None)
    token = ("bc1." + base64.urlsafe_b64encode(_json(asdict(next_cursor)).encode()).decode().rstrip("=")
             if next_cursor is not None else "")
    return rows, token, {
        "bytes_read": result.bytes_read,
        "snapshot_length": result.snapshot_length,
        "position": next_cursor.offset if next_cursor else None,
        "unconsumed_bytes": (result.snapshot_length - next_cursor.offset
                             if result.snapshot_length is not None and next_cursor else 0),
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
    from tools.bridge_next_action import BridgeNextActionError
    try:
        rows, cursor, reader_stats = _read_view_rows(args.events, args.tail, args.after)
        if args.event_id:
            matches = [row for row in rows if event_id(row) == args.event_id]
            if not matches:
                raise ValueError("event reference unavailable in bounded snapshot")
            report = {"schema": "wd.bridge.event-detail.v1", "authority": "none",
                      "ref": args.event_id, "event": matches[0]}
        else:
            report = compact_view(rows)
            report["cursor"] = cursor
            report["reader"] = reader_stats
            if args.after:
                report["scope"] = "position_delta_batch_not_complete_history"
        rendered = _json(report)
        if any(marker in rendered for marker in ("PRIVATE_MARKER", "_DO_NOT_LEAK")):
            raise ValueError("private marker in selected output")
        print(rendered)
        return 0
    except (OSError, ValueError, BridgeNextActionError) as exc:
        # Avoid printing event content in parse errors.
        print(_json({"ok": False, "error": type(exc).__name__,
                     "reason": str(exc) if isinstance(exc, CompactViewError) else "invalid_source_or_input",
                     "action": "inspect raw snapshot; do not infer no work"}),
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
