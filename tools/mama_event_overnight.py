# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""Overnight real-data collector for the Mama Event Observatory.

This tool tails ``data/chat_history.db`` for new ``messages`` rows and
runs them through a single :class:`MamaEventObserver` instance, writing
all artifacts under ``logs/observatory/overnight/``.

Design contract
---------------
* **Real data only.** This collector NEVER fabricates events. If the
  ``messages`` table has no new rows since the checkpoint, the
  collector takes a snapshot with ``new_real_events=0`` and returns;
  no synthetic events are emitted, ever.
* **Append-only.** Every NDJSON sink is opened in append mode. The
  checkpoint file uses an atomic ``write tmp + os.replace`` pattern
  so a crash mid-write cannot corrupt the resume point.
* **Restart-safe.** The collector resumes from the last processed
  ``messages.id`` and rebuilds a 3-turn rolling context window from
  the rows immediately preceding that id. This matches the windowing
  the long-run replay uses, so the contamination guard sees the same
  context across restarts.
* **Tail-only by default.** On the very first invocation (no
  checkpoint file), the collector sets the checkpoint to the current
  ``MAX(id)`` WITHOUT processing those rows. The user can pass
  ``--include-existing`` to baseline-ingest the current table on
  first run instead.
* **Honest snapshots.** Snapshots record wall-clock time, the row-id
  delta, candidate counts, contamination hits, caregiver-binding
  reinforcements, NDJSON sizes, and (if available) process RSS.
  ``data_status`` and ``observatory_verdict`` are kept as separate
  fields so a "no new rows tonight" run does NOT get conflated with
  a "framework returned NO_CANDIDATES on real new data" run.
* **UTF-8 hard.** Every file open uses ``encoding="utf-8"``. The
  SQLite reader uses ``text_factory=lambda b: b.decode("utf-8",
  errors="replace")`` so a stray byte in legacy chat history cannot
  abort the overnight run.

CLI
---
Single-shot mode (default — process whatever is new and return)::

    python tools/mama_event_overnight.py

Long-running watch mode (poll every 60 s for ``--duration-seconds``,
take snapshots every ``--snapshot-interval-seconds``)::

    python tools/mama_event_overnight.py --watch \\
        --duration-seconds 28800 \\
        --snapshot-interval-seconds 1800

The collector exits cleanly on Ctrl+C and on duration expiry; the
checkpoint and all NDJSON sinks are flushed and closed in both
cases.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.observatory.mama_events.observer import (
    FileNdjsonSink,
    MamaEventObserver,
)
from waggledance.observatory.mama_events.taxonomy import (
    EventType,
    MamaCandidateEvent,
    UtteranceKind,
)


# ── paths ────────────────────────────────────────────────

OVERNIGHT_DIR = ROOT / "logs" / "observatory" / "overnight"
DEFAULT_DB_PATH = ROOT / "data" / "chat_history.db"

CHECKPOINT_NAME = "checkpoint.json"
EVENTS_NAME = "events_real.ndjson"
SELF_STATE_NAME = "self_state_real.ndjson"
BINDING_NAME = "binding_real.ndjson"
SNAPSHOTS_NAME = "overnight_snapshots.ndjson"
PROCESS_NAME = "overnight_process.ndjson"


log = logging.getLogger("mama_event_overnight")


# ── checkpoint ───────────────────────────────────────────


@dataclass
class Checkpoint:
    """Resume point for the overnight collector.

    ``last_row_id`` is the highest ``messages.id`` already observed.
    ``cumulative_real_events`` counts how many candidate events the
    observer has processed across the entire overnight history (not
    just the current process). ``next_snapshot_index`` keeps the
    snapshot counter globally monotonic across restarts so the
    morning stability check can verify the snapshot stream is
    well-formed even after a resume. The other fields are advisory.
    """

    last_row_id: int = 0
    cumulative_real_events: int = 0
    next_snapshot_index: int = 0
    first_started_at: str = ""
    last_run_started_at: str = ""
    last_run_ended_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "last_row_id": int(self.last_row_id),
            "cumulative_real_events": int(self.cumulative_real_events),
            "next_snapshot_index": int(self.next_snapshot_index),
            "first_started_at": self.first_started_at,
            "last_run_started_at": self.last_run_started_at,
            "last_run_ended_at": self.last_run_ended_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Checkpoint":
        return cls(
            last_row_id=int(data.get("last_row_id", 0) or 0),
            cumulative_real_events=int(data.get("cumulative_real_events", 0) or 0),
            next_snapshot_index=int(data.get("next_snapshot_index", 0) or 0),
            first_started_at=str(data.get("first_started_at", "") or ""),
            last_run_started_at=str(data.get("last_run_started_at", "") or ""),
            last_run_ended_at=str(data.get("last_run_ended_at", "") or ""),
        )


def load_checkpoint(path: Path) -> Optional[Checkpoint]:
    """Return the saved checkpoint, or ``None`` if missing/unreadable.

    A missing or corrupt checkpoint is not an error: the collector
    treats it as a fresh start and the caller decides whether to
    skip-existing or include-existing.
    """
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("checkpoint unreadable, treating as fresh: %s", exc)
        return None
    if not isinstance(data, dict):
        return None
    return Checkpoint.from_dict(data)


def save_checkpoint(path: Path, checkpoint: Checkpoint) -> None:
    """Atomically persist the checkpoint to disk.

    Writes ``path.tmp`` first, then ``os.replace`` to the final name.
    A crash mid-write therefore leaves either the previous good
    checkpoint or the new good checkpoint, never a half-written one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(checkpoint.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp, path)


# ── database reader ──────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ChatRow:
    """One ``messages`` row from ``chat_history.db``.

    Field order matches the column order in the SELECT statement so
    we can construct directly from a sqlite3 row tuple.
    """

    id: int
    conversation_id: int
    role: str
    content: str
    agent_name: str
    language: str
    created_at: str


_SELECT_COLS = (
    "id, conversation_id, role, content, agent_name, language, created_at"
)


def _open_db(db_path: Path) -> sqlite3.Connection:
    """Return a read-only sqlite connection with UTF-8 forgiving decode."""
    conn = sqlite3.connect(str(db_path))
    # The legacy chat history occasionally contains code-page-encoded
    # rows from earlier Windows runs. Force UTF-8 with replacement so
    # one bad byte cannot abort the overnight collector.
    conn.text_factory = lambda b: b.decode("utf-8", errors="replace")
    return conn


def _coerce_text(value: Any) -> str:
    """Defensively decode any value to a UTF-8 string.

    SQLite's ``text_factory`` covers values that come back as TEXT,
    but a row whose content was bound to the cursor as Python bytes
    is stored (and returned) as a BLOB and bypasses the factory.
    This helper handles both shapes so the collector never crashes
    on a stray byte sequence.
    """
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def fetch_max_row_id(db_path: Path) -> int:
    """Return ``MAX(messages.id)`` or 0 if the table is empty/missing."""
    if not db_path.is_file():
        return 0
    conn = _open_db(db_path)
    try:
        cur = conn.execute("SELECT COALESCE(MAX(id), 0) FROM messages")
        row = cur.fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def fetch_rows_after(db_path: Path, after_id: int, limit: int = 5_000) -> List[ChatRow]:
    """Return ``messages`` rows whose id is strictly greater than ``after_id``.

    The result is ordered by id so checkpoint advancement is monotonic.
    A safety ``LIMIT`` keeps a single batch bounded.
    """
    if not db_path.is_file():
        return []
    conn = _open_db(db_path)
    try:
        cur = conn.execute(
            f"SELECT {_SELECT_COLS} FROM messages "
            "WHERE id > ? ORDER BY id LIMIT ?",
            (int(after_id), int(limit)),
        )
        rows: List[ChatRow] = []
        for raw in cur.fetchall():
            rows.append(ChatRow(
                id=int(raw[0]),
                conversation_id=int(raw[1] or 0),
                role=_coerce_text(raw[2]),
                content=_coerce_text(raw[3]),
                agent_name=_coerce_text(raw[4]),
                language=_coerce_text(raw[5]),
                created_at=_coerce_text(raw[6]),
            ))
        return rows
    finally:
        conn.close()


def fetch_window_rows_before(
    db_path: Path,
    before_id: int,
    n: int = 3,
) -> List[str]:
    """Return up to ``n`` content strings for the rows immediately
    preceding ``before_id`` in id order.

    Used on restart to seed the rolling context window so the
    contamination guard observes the same lexical context as a
    one-shot replay would.
    """
    if not db_path.is_file() or before_id <= 0 or n <= 0:
        return []
    conn = _open_db(db_path)
    try:
        cur = conn.execute(
            "SELECT content FROM messages WHERE id <= ? AND content IS NOT NULL "
            "ORDER BY id DESC LIMIT ?",
            (int(before_id), int(n)),
        )
        contents = [_coerce_text(r[0]) for r in cur.fetchall() if r and r[0]]
        return list(reversed([c for c in contents if c]))
    finally:
        conn.close()


# ── event construction ──────────────────────────────────


def row_to_event(
    row: ChatRow,
    window: Sequence[str],
    *,
    base_ts_ms: int,
) -> Optional[MamaCandidateEvent]:
    """Convert one ``messages`` row to a candidate event.

    Returns ``None`` if the row should be skipped (empty content,
    user turn — user turns become context for the *next* candidate
    instead of candidates themselves, mirroring the long-run replay).
    """
    text = (row.content or "").strip()
    if not text:
        return None
    if row.role == "user":
        return None
    # Use the row id as the deterministic timestamp offset so two runs
    # produce identical event ids for the same row.
    ts_ms = base_ts_ms + int(row.id) * 1_000
    return MamaCandidateEvent(
        event_type=EventType.LEXICAL,
        utterance_text=text,
        speaker_id=row.agent_name or "agent",
        timestamp_ms=ts_ms,
        session_id=f"chat-{row.conversation_id}",
        last_n_turns=tuple(window[-3:]),
        utterance_kind=UtteranceKind.GENERATED_TEXT,
    )


# ── runtime stats ────────────────────────────────────────


@dataclass
class RuntimeStats:
    """In-memory counters maintained while the collector runs.

    These feed both the per-snapshot record and the morning analysis.
    Reset on each process start (the per-process delta is what makes
    a snapshot meaningful) but ``cumulative_real_events`` is kept on
    the checkpoint so multi-night totals stay correct.

    Fields that the observer already tracks internally
    (``self_state_emissions``, ``consolidation_writes``,
    ``strongest_verdict``) are NOT mirrored here — they are read
    straight from :meth:`MamaEventObserver.summary` at snapshot time
    so the snapshot record cannot drift from the live observer state.
    """

    new_real_events_this_run: int = 0
    candidate_events: int = 0
    max_score: int = 0
    contamination_hits: int = 0
    caregiver_binding_hits: int = 0


# ── process memory ───────────────────────────────────────


def sample_process_memory_mb() -> Optional[float]:
    """Return process RSS in MB, or ``None`` if unavailable.

    Tries ``psutil`` first (cross-platform). Falls back to
    ``resource.getrusage`` on POSIX. Never raises — a missing memory
    sample is not a reason to abort the overnight run.
    """
    try:
        import psutil  # type: ignore[import-not-found]

        rss = psutil.Process(os.getpid()).memory_info().rss
        return round(rss / (1024 * 1024), 3)
    except Exception:
        pass
    try:
        import resource  # type: ignore[import-not-found]

        ru = resource.getrusage(resource.RUSAGE_SELF)
        # ru_maxrss is kilobytes on Linux, bytes on macOS
        return round(ru.ru_maxrss / 1024, 3)
    except Exception:
        return None


# ── observer wiring ──────────────────────────────────────


def build_observer(out_dir: Path) -> MamaEventObserver:
    """Construct a single baseline observer with file-backed sinks.

    All three sinks open in append mode (see :class:`FileNdjsonSink`),
    so resuming after a restart continues writing to the same files
    without truncation.
    """
    return MamaEventObserver(
        sink=FileNdjsonSink(out_dir / EVENTS_NAME),
        binding_sink=FileNdjsonSink(out_dir / BINDING_NAME),
        self_state_sink=FileNdjsonSink(out_dir / SELF_STATE_NAME),
    )


def _ndjson_size_bytes(path: Path) -> int:
    try:
        return path.stat().st_size if path.is_file() else 0
    except OSError:
        return 0


def _now_iso_z() -> str:
    """Return the current UTC timestamp in ISO-8601 Z form."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── snapshot ─────────────────────────────────────────────


def snapshot_state(
    *,
    out_dir: Path,
    observer: MamaEventObserver,
    stats: RuntimeStats,
    checkpoint: Checkpoint,
    snapshot_index: int,
    delta_events_since_last_snapshot: int,
    process_sink: FileNdjsonSink,
    snapshot_sink: FileNdjsonSink,
) -> Dict[str, Any]:
    """Write one snapshot record + matching process record. Returns the snapshot dict.

    ``self_state_emissions`` and ``consolidation_writes`` are read
    from :meth:`MamaEventObserver.summary` (which exposes the live
    episodic store size as ``total_events``) so they cannot drift
    from the actual observer state. They equal
    ``new_real_events_this_run`` for the baseline observer, which is
    documented behaviour: every observed event triggers exactly one
    self-state update and one episodic write when both subsystems
    are enabled.

    The ``ndjson_bytes`` map intentionally does NOT include the
    snapshots and process files themselves — those grow as a
    side-effect of taking the snapshot, so sampling their size at
    this point would be off-by-one.
    """
    summary = observer.summary()
    verdict = str(summary.get("verdict", ""))
    observer_total_events = int(summary.get("total_events", 0) or 0)
    rss_mb = sample_process_memory_mb()
    snapshot = {
        "snapshot_index": int(snapshot_index),
        "timestamp": _now_iso_z(),
        "checkpoint_row_id": int(checkpoint.last_row_id),
        "delta_real_events_since_last_snapshot": int(delta_events_since_last_snapshot),
        "new_real_events_this_run": int(stats.new_real_events_this_run),
        "cumulative_real_events": int(checkpoint.cumulative_real_events),
        "candidate_events": int(stats.candidate_events),
        "max_score": int(stats.max_score),
        "strongest_verdict": verdict,
        "contamination_hits": int(stats.contamination_hits),
        "caregiver_binding_hits": int(stats.caregiver_binding_hits),
        "self_state_emissions": observer_total_events,
        "consolidation_writes": observer_total_events,
        "band_counts": dict(summary.get("band_counts", {}) or {}),
        "verdict": verdict,
        "preferred_caregiver": summary.get("preferred_caregiver"),
        "ndjson_bytes": {
            EVENTS_NAME: _ndjson_size_bytes(out_dir / EVENTS_NAME),
            SELF_STATE_NAME: _ndjson_size_bytes(out_dir / SELF_STATE_NAME),
            BINDING_NAME: _ndjson_size_bytes(out_dir / BINDING_NAME),
        },
        "process_rss_mb": rss_mb,
    }
    snapshot_sink.write(snapshot)
    process_sink.write({
        "snapshot_index": int(snapshot_index),
        "timestamp": snapshot["timestamp"],
        "process_rss_mb": rss_mb,
        "pid": os.getpid(),
    })
    return snapshot


# ── ingest one batch ─────────────────────────────────────


def _push_window(window: List[str], text: str, n: int = 3) -> None:
    """Append ``text`` to the rolling window and keep at most ``n`` entries."""
    window.append(text)
    del window[:-n]


def ingest_batch(
    *,
    db_path: Path,
    observer: MamaEventObserver,
    stats: RuntimeStats,
    checkpoint: Checkpoint,
    base_ts_ms: int,
    window: List[str],
    batch_limit: int,
) -> int:
    """Ingest up to ``batch_limit`` new rows. Returns the number ingested.

    The window list is mutated in place so the next batch (or the
    next watch iteration) sees the correct rolling context.
    """
    rows = fetch_rows_after(db_path, checkpoint.last_row_id, limit=batch_limit)
    if not rows:
        return 0
    ingested = 0
    for row in rows:
        text = (row.content or "").strip()
        # advance checkpoint regardless of whether the row produced an event
        # so user turns are not re-tailed forever
        checkpoint.last_row_id = max(checkpoint.last_row_id, int(row.id))
        if not text:
            continue
        if row.role == "user":
            _push_window(window, text)
            continue
        event = row_to_event(row, window, base_ts_ms=base_ts_ms)
        if event is None:
            _push_window(window, text)
            continue
        result = observer.observe(event)
        ingested += 1
        stats.new_real_events_this_run += 1
        checkpoint.cumulative_real_events += 1
        # update derived counters from the observation result
        score_total = int(result.breakdown.total)
        if score_total > 0:
            stats.candidate_events += 1
            if score_total > stats.max_score:
                stats.max_score = score_total
        if result.contamination.flags:
            stats.contamination_hits += 1
        if event.caregiver_candidate_id and score_total >= 20:
            stats.caregiver_binding_hits += 1
        # advance the rolling window with the assistant turn too
        _push_window(window, text)
    return ingested


# ── main run loop ────────────────────────────────────────


def run_once(
    *,
    db_path: Path,
    out_dir: Path,
    include_existing: bool,
    base_ts_ms: int,
    snapshot_interval_seconds: float,
    duration_seconds: float,
    poll_interval_seconds: float,
    watch: bool,
    batch_limit: int,
    stop_flag: Optional[Dict[str, bool]] = None,
) -> Dict[str, Any]:
    """Single overnight collection cycle.

    Returns a result dict suitable for the morning analysis tool.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = out_dir / CHECKPOINT_NAME
    checkpoint = load_checkpoint(checkpoint_path)
    fresh_start = checkpoint is None
    if fresh_start:
        checkpoint = Checkpoint()
        checkpoint.first_started_at = _now_iso_z()
        if not include_existing:
            # Tail-only first run: skip everything currently in the DB
            # so the overnight verdict is based on rows that arrive
            # AFTER the collector started.
            current_max = fetch_max_row_id(db_path)
            checkpoint.last_row_id = current_max
            log.info(
                "fresh-start tail mode: checkpoint set to current MAX(id)=%d",
                current_max,
            )

    checkpoint.last_run_started_at = _now_iso_z()
    save_checkpoint(checkpoint_path, checkpoint)

    # capture the row id at the start of the run so the morning analysis
    # can compare "where we started" against "where we ended" without
    # back-calculating from new_real_events_this_run (which would be
    # off by the number of skipped user turns)
    start_row_id = int(checkpoint.last_row_id)

    observer = build_observer(out_dir)
    stats = RuntimeStats()

    snapshot_sink = FileNdjsonSink(out_dir / SNAPSHOTS_NAME)
    process_sink = FileNdjsonSink(out_dir / PROCESS_NAME)

    # seed the rolling window from rows immediately preceding the checkpoint
    window: List[str] = list(fetch_window_rows_before(db_path, checkpoint.last_row_id, n=3))

    snapshots_count = 0
    snapshot_index = int(checkpoint.next_snapshot_index)
    last_snapshot_at = time.monotonic()
    last_snapshot_event_count = 0
    deadline = time.monotonic() + duration_seconds if duration_seconds > 0 else float("inf")
    started_monotonic = time.monotonic()

    def _take_snapshot() -> None:
        nonlocal snapshot_index, last_snapshot_at, last_snapshot_event_count, snapshots_count
        delta = stats.new_real_events_this_run - last_snapshot_event_count
        snapshot_state(
            out_dir=out_dir,
            observer=observer,
            stats=stats,
            checkpoint=checkpoint,
            snapshot_index=snapshot_index,
            delta_events_since_last_snapshot=delta,
            process_sink=process_sink,
            snapshot_sink=snapshot_sink,
        )
        snapshots_count += 1
        snapshot_index += 1
        checkpoint.next_snapshot_index = snapshot_index
        last_snapshot_at = time.monotonic()
        last_snapshot_event_count = stats.new_real_events_this_run

    try:
        # initial snapshot for the audit trail (start-of-run state)
        _take_snapshot()
        while True:
            if stop_flag and stop_flag.get("stop"):
                break
            ingested = ingest_batch(
                db_path=db_path,
                observer=observer,
                stats=stats,
                checkpoint=checkpoint,
                base_ts_ms=base_ts_ms,
                window=window,
                batch_limit=batch_limit,
            )
            if ingested:
                save_checkpoint(checkpoint_path, checkpoint)

            now = time.monotonic()
            if snapshot_interval_seconds <= 0 or (now - last_snapshot_at) >= snapshot_interval_seconds:
                _take_snapshot()

            if not watch:
                break
            if now >= deadline:
                break
            # Sleep in small slices so Ctrl+C and stop_flag respond promptly.
            sleep_until = min(now + poll_interval_seconds, deadline)
            while time.monotonic() < sleep_until:
                if stop_flag and stop_flag.get("stop"):
                    break
                time.sleep(min(0.5, max(0.0, sleep_until - time.monotonic())))
            if stop_flag and stop_flag.get("stop"):
                break

        # final snapshot so the morning report sees the closing state
        _take_snapshot()
        # Cache the live observer summary BEFORE close() so the result
        # dict reflects the same state the last snapshot recorded.
        final_summary = observer.summary()
    finally:
        checkpoint.last_run_ended_at = _now_iso_z()
        save_checkpoint(checkpoint_path, checkpoint)
        observer.close()
        snapshot_sink.close()
        process_sink.close()

    wall_clock = time.monotonic() - started_monotonic
    observer_total_events = int(final_summary.get("total_events", 0) or 0)
    return {
        "fresh_start": fresh_start,
        "wall_clock_seconds": round(wall_clock, 3),
        "snapshots_taken": snapshots_count,
        "new_real_events_this_run": stats.new_real_events_this_run,
        "cumulative_real_events": checkpoint.cumulative_real_events,
        "checkpoint_start_row_id": start_row_id,
        "checkpoint_end_row_id": int(checkpoint.last_row_id),
        "candidate_events": stats.candidate_events,
        "max_score": stats.max_score,
        "strongest_verdict": str(final_summary.get("verdict", "")),
        "contamination_hits": stats.contamination_hits,
        "caregiver_binding_hits": stats.caregiver_binding_hits,
        "self_state_emissions": observer_total_events,
        "consolidation_writes": observer_total_events,
    }


# ── CLI ──────────────────────────────────────────────────


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Mama Event Observatory overnight real-data collector.",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="Path to chat_history.db (default: %(default)s)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=OVERNIGHT_DIR,
        help="Output directory for NDJSON + checkpoint (default: %(default)s)",
    )
    parser.add_argument(
        "--include-existing",
        action="store_true",
        help="On first run, ingest rows already present in the DB instead of skipping them.",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Stay running and poll for new rows. Without this flag the collector returns after one ingest pass.",
    )
    parser.add_argument(
        "--duration-seconds",
        type=float,
        default=0.0,
        help="Watch-mode wall-clock budget. 0 = run forever (until Ctrl+C).",
    )
    parser.add_argument(
        "--snapshot-interval-seconds",
        type=float,
        default=1800.0,
        help="Wall-clock interval between snapshots in watch mode (default: 1800 = 30 min).",
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=float,
        default=60.0,
        help="How often to poll the DB in watch mode (default: 60 s).",
    )
    parser.add_argument(
        "--batch-limit",
        type=int,
        default=5_000,
        help="Maximum rows to ingest per polling cycle.",
    )
    parser.add_argument(
        "--base-ts-ms",
        type=int,
        default=1_700_000_000_000,
        help="Deterministic timestamp base for synthetic event ids (matches long-run replay).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    stop_flag: Dict[str, bool] = {"stop": False}

    def _sigint(_signum, _frame) -> None:  # noqa: ANN001
        log.warning("interrupt received, finishing current cycle")
        stop_flag["stop"] = True

    signal.signal(signal.SIGINT, _sigint)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _sigint)

    result = run_once(
        db_path=args.db_path,
        out_dir=args.out_dir,
        include_existing=args.include_existing,
        base_ts_ms=args.base_ts_ms,
        snapshot_interval_seconds=args.snapshot_interval_seconds,
        duration_seconds=args.duration_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
        watch=args.watch,
        batch_limit=args.batch_limit,
        stop_flag=stop_flag,
    )
    log.info("overnight collector result: %s", json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
