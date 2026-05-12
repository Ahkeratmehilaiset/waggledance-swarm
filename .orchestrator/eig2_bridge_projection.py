#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Adapter-first projection from live bridge events to EIG2 event view."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any


PROTOCOL_VERSION = "eig2-bridge-v1"
MESSAGE_ANSWER_STATUSES = {
    "answered",
    "answered_plus_reminder",
    "answered_after_recovery",
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def payload_hash(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _payload_dict(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    return payload if isinstance(payload, dict) else {}


def project_live_event(event: dict[str, Any]) -> dict[str, Any]:
    """Return the EIG2 schema view without mutating the live bridge event."""
    payload = _payload_dict(event)
    event_id = str(event.get("task_id") or event.get("id") or "")
    message_type = str(event.get("type") or event.get("message_type") or "")
    timestamp = str(event.get("ts_utc") or event.get("timestamp") or "")
    author = str(event.get("agent") or event.get("author") or "")
    status = str(event.get("status") or "")

    return {
        "protocol_version": PROTOCOL_VERSION,
        "message_type": message_type,
        "id": event_id,
        "timestamp": timestamp,
        "author": author,
        "related_milestone": str(
            event.get("related_milestone") or payload.get("related_milestone") or ""
        ),
        "parent_id": str(event.get("parent_id") or payload.get("parent_id") or ""),
        "status": status,
        "to": str(event.get("to") or ""),
        "payload_hash": payload_hash(payload),
        "live_schema": {
            "task_id": event_id,
            "type": message_type,
            "ts_utc": timestamp,
            "agent": author,
        },
    }


def is_message_answer(event: dict[str, Any]) -> bool:
    return str(event.get("type") or "") == "message" and str(
        event.get("status") or ""
    ) in MESSAGE_ANSWER_STATUSES


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not path.exists():
        return events
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        events.append(json.loads(raw))
    return events


def main() -> int:
    raw = sys.stdin.read().strip()
    if not raw:
        print(json.dumps({"error": "expected one JSON object on stdin"}, indent=2))
        return 2
    event = json.loads(raw)
    if not isinstance(event, dict):
        print(json.dumps({"error": "expected JSON object"}, indent=2))
        return 2
    print(json.dumps(project_live_event(event), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
