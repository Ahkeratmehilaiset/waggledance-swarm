# SPDX-License-Identifier: BUSL-1.1
"""Opt-in, read-only compact-view measurement of one bounded supplied corpus.

Example: python -B tools/measure_bridge_compact_view.py --events PATH --tail 5000

Compare canonical UTF-8 JSON of the selected normalized event array with the
actual compact CLI envelope (including reader/cursor metadata), excluding the
stdout newline from both representations. This is neither an on-disk byte
savings claim nor a token/cost measurement. Blank/null historical rows follow
the compact reader's normalization. Coverage means unique non-heartbeat event
references and chronology, not full payload retention. Consult event detail.

Received ACK correlation is advisory and limited to explicit existing payload
fields; it proves neither delivery durability, task completion nor authority.
No event, ACK, claim, cursor, or scratch file is written. Output is stdout JSON.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sys
from time import perf_counter
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.bridge_compact_view import _json, _read_view_rows, compact_view, event_id
from tools.bridge_next_action import BridgeNextActionError, _is_request_like
from waggledance.core.bridge_event_schema import KNOWN_ACK_STATUSES
from waggledance.core.bridge_log_reader import MAX_MAX_BYTES, MAX_MAX_ROWS


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else None
    except ValueError:
        return None


def _elapsed(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    seconds = (end - start).total_seconds()
    return seconds if seconds >= 0 else None


def _traffic(rows: Sequence[Mapping[str, Any]]) -> dict:
    heartbeat = ack = 0
    for row in rows:
        if row.get("type") in {"heartbeat", "liveness"}:
            heartbeat += 1
        elif row.get("type") == "message" and row.get("status") in KNOWN_ACK_STATUSES:
            ack += 1
    return {"heartbeat_or_liveness": heartbeat, "ack": ack,
            "useful_work_proxy": len(rows) - heartbeat - ack}


def _key(values: Sequence[Any]) -> tuple[str, ...] | None:
    return tuple(values) if all(isinstance(v, str) and v for v in values) else None


def _lifecycle(rows: Sequence[Mapping[str, Any]], now: datetime) -> dict:
    # Index once: avoid an ACK-by-request quadratic scan at the 100k-row bound.
    requests: dict[tuple[str, ...], list[Mapping[str, Any]]] = {}
    request_count = 0
    for row in rows:
        if _is_request_like(row):
            request_count += 1
            key = _key([row.get(k) for k in ("task_id", "ts_utc", "agent", "type", "status")])
            if key is not None:
                requests.setdefault(key, []).append(row)
    observations = []
    for row in rows:
        if row.get("type") != "message" or row.get("status") != "received":
            continue
        item = {"ack_ref": event_id(row), "request_ref": None,
                "correlation": "unknown", "ack_wait_seconds": None,
                "request_age_seconds": None, "reason": "missing_explicit_request_fields"}
        payload = row.get("payload")
        payload = payload if isinstance(payload, Mapping) else {}
        key = _key([row.get("task_id"), *[payload.get(k) for k in
                    ("request_ts_utc", "request_agent", "request_type", "request_status")]])
        if key is not None:
            matches = requests.get(key, [])
            item["reason"] = "request_unavailable_in_snapshot"
            if len(matches) > 1:
                item["reason"] = "ambiguous_request_in_snapshot"
            elif len(matches) == 1:
                request = matches[0]
                # Broadcast, multiple recipients and missing recipient are unknown.
                if (not isinstance(request.get("to"), str) or not request.get("to")
                        or request["to"] != row.get("agent")
                        or row.get("to") != request.get("agent")):
                    item["reason"] = "recipient_binding_unavailable_or_mismatched"
                else:
                    item["request_ref"] = event_id(request)
                    item["correlation"] = "unique_explicit_fields_in_snapshot"
                    requested = _timestamp(request.get("ts_utc"))
                    acknowledged = _timestamp(row.get("ts_utc"))
                    item["request_age_seconds"] = _elapsed(requested, now)
                    item["ack_wait_seconds"] = (_elapsed(requested, acknowledged)
                                                if _elapsed(acknowledged, now) is not None else None)
                    item["reason"] = ("observed" if item["ack_wait_seconds"] is not None
                                      and item["request_age_seconds"] is not None
                                      else "missing_invalid_or_negative_time")
        observations.append(item)
    return {
        "authority": "none", "scope": "advisory_observed_fields_in_selected_snapshot",
        "request_like_events": request_count,
        "request_classification": "existing_bridge_next_action_heuristic",
        "received_ack_observations": observations,
        "unobserved_ack_state": "unknown_not_proof_of_no_ack",
        "completion_wait_seconds": None,
        "completion_reason": "no_unambiguous_completion_binding_interpreted",
        "delivery_durability": "unknown_not_inferred_from_ack_or_suppression",
    }


def measure(path: Path, *, tail: int = 5000, now: datetime | None = None) -> dict:
    """Read one stable bounded snapshot; never persist the returned cursor."""
    if not 0 <= tail <= MAX_MAX_ROWS:
        raise ValueError("tail must be 0..100000")
    now = now if now is not None else datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must include a timezone")
    started = perf_counter()
    rows, cursor, reader = _read_view_rows(Path(path), tail, "")
    read_seconds = perf_counter() - started
    started = perf_counter()
    compact = compact_view(rows) | {"cursor": cursor, "reader": reader}
    compact_bytes = len(_json(compact).encode("utf-8"))
    render_seconds = perf_counter() - started
    source = _json(rows).encode("utf-8")
    source_bytes = len(source)
    missing = reader["snapshot_length"] is None
    unique = {event_id(row): row for row in rows}
    meaningful = [ref for ref, row in unique.items()
                  if row.get("type") not in {"heartbeat", "liveness"}]
    visible = [row["ref"] for row in compact["events"]]
    return {
        "schema": "wd.bridge.compact-measurement.v1", "authority": "none",
        "scope": "bounded_snapshot_not_complete_history",
        "source_availability": "missing" if missing else "observed",
        "selected_events": len(rows),
        "selected_corpus_sha256": None if missing else hashlib.sha256(source).hexdigest(),
        "as_of_utc": now.astimezone(timezone.utc).isoformat(),
        "limits": {"tail_rows": tail or MAX_MAX_ROWS, "max_input_bytes": MAX_MAX_BYTES},
        "reader": reader,
        "bytes": {
            "source_json_utf8": None if missing else source_bytes,
            "compact_json_utf8": compact_bytes,
            "compact_to_source_ratio": None if missing else compact_bytes / source_bytes,
            "source_minus_compact": None if missing else source_bytes - compact_bytes,
            "denominator": "canonical_json_array_of_selected_normalized_events_utf8",
            "numerator": "compact_cli_json_envelope_including_cursor_and_reader_utf8",
            "newline_included": False,
            "on_disk_savings": "not_measured",
            "normalization": "existing_tail_reader_blank_null_and_historical_row_rules",
        },
        "coverage": {
            "unique_meaningful_source_events": len(meaningful), "visible_events": len(visible),
            "all_meaningful_refs_preserved": None if missing else set(meaningful) == set(visible),
            "meaningful_chronology_preserved": None if missing else meaningful == visible,
            "full_payload_preservation": "not_claimed_use_event_detail",
        },
        "exclusions": {
            "exact_canonical_duplicates": compact["stats"]["duplicates"],
            "unique_heartbeat_or_liveness_events": compact["stats"]["heartbeats"],
        },
        "traffic": {
            "all_rows": _traffic(rows), "unique_rows": _traffic(list(unique.values())),
            "useful_work_proxy_definition": "non_heartbeat_non_message_ack_including_unknown_types",
            "productive_work_or_success": "not_inferred",
        },
        "lifecycle": _lifecycle(list(unique.values()), now),
        "timings_seconds": {"read_parse": read_seconds, "compact_render": render_seconds},
        "timing_scope": "single_observed_run_not_a_benchmark_or_deterministic_guarantee",
        "provider_metrics": {"tokens": None, "cost": None, "reason": "no_provider_measurement"},
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--tail", type=int, default=5000)
    parser.add_argument("--now", help="ISO-8601 time with timezone for reproducible ages")
    args = parser.parse_args(argv)
    try:
        now = _timestamp(args.now) if args.now is not None else datetime.now(timezone.utc)
        if now is None:
            raise ValueError("invalid now")
        print(_json(measure(args.events, tail=args.tail, now=now)))
        return 0
    except (OSError, ValueError, TypeError, BridgeNextActionError):
        print(_json({"ok": False, "error": "invalid_source_or_input",
                     "action": "inspect raw snapshot; do not infer no work"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
