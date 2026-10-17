"""Offline cases for the capture producer's committed state and its window read (``advance``, ``read_state``), its
seed file read, its start read of its own latest.json (``read_latest``), which must read back the forms the producer
itself writes, its fresh-state check (``refuse_legacy``), its live dir lock (``lock_live_dir``: this platform's real
lock, and each platform's calls with stand-ins), its cycle dir names (``new_cycle_dir``, with a stand-in
``cycle_name`` whose second repeats or goes back), its retention (``prune``), its writes (``atomic_write``,
``write_new``), its validation of every payload before the first write (``prewrite``), the previous publication it
hands the shadow run (``previous_publication``), its PR context (``pr_context``, gh stubbed), its refusal record
(``record_refusal``, ``refused``) and its commit order (``one_cycle``, with the candidate stubbed).

The producer's functions read the module globals its script program sets (the parsed arguments, the live dir, the
code tree's modules), so these cases execute its own functions, classes and constants, taken from the module file,
in a namespace that holds those globals, with a fake bridge as ``subprocess``: each would-be reader start is
recorded and answered from fixed rows (or raises, as subprocess.run raises a timeout or a failed start), and
nothing starts. ``event_id`` is the compact view's real canonical ref, and the core, shadow and workspace modules
are this tree's own. A cursor is a compact position cursor of the tool's own form (``position``).
``BoundedBridge`` answers like the compact tool's bounded snapshot: an -EventId read finds a ref only among the
last lines it reads (RCO1's L1 shape, a fake-reader reproduction), and a listing reports the rows it read after
the cursor as stats.snapshot_events, as the tool does.
There is no reseed (Lead's B3): a reader refusal, or a committed state the producer cannot verify, refuses the
cycle, and the next cycle starts from the same committed state. The cases that pinned the earlier reseed now pin
that refusal with the same inputs.
Each refusal is compared by its exception's arguments: exactly the fixed reason, no added text (``refused``).
"""

import ast
import base64
import concurrent.futures
import contextlib
import copy
import datetime
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

import pytest

from tools import bridge_mind_workspace as ws
from tools import wd_bridge_mind as core
from tools import wd_bridge_mind_shadow as shadow
from tools.bridge_compact_view import event_id
from waggledance.core.bridge_log_reader import parse_bridge_json_object

PRODUCER = Path(__file__).resolve().parent / "mind_capture_producer.py"
# the producer's constants; READER_OPTION is the per-token check the slot grammar replaced, listed so that these
# cases also run over a producer from before that change
CONSTANTS = ("READER", "READER_OPTION", "DETAIL_TAIL", "TAIL_CAP", "SEED_MARGIN", "COUNT", "EVENT_REF", "CURSOR",
             "REPORT_TASK", "LANE", "LATEST_SCHEMA", "STATE_SCHEMA", "STATE_KEYS", "ROW_KEYS", "VIEW_SCHEMA",
             "VIEW_KEYS", "LIST_FIELDS", "REFUSAL_SCHEMA", "VIEW_CAP", "GH_TIMEOUT", "GH_BUDGET", "PRUNED",
             "STOP_EXIT", "CYCLE_DIR", "CYCLE_TIME", "LOCK_BYTE", "LIST_LIMIT")
MARKER = "PRIVATE" + "_MARKER"  # a guarded word, composed so that this file holds no marker literal
WINDOW = 4
EVENTS = [{"agent": "lane-a", "type": "message", "task_id": "lane-a/state-case",
           "ts_utc": f"2026-10-16T00:00:{n:02d}Z", "message": f"row {n} äö", "weight": n / 10}
          for n in range(6)]  # non-ASCII text and floats: the committed rows must survive their JSON round trip
REFS = [event_id(event) for event in EVENTS]
BY_REF = dict(zip(REFS, EVENTS))
DEEP = b"[" * 100000 + b"]" * 100000  # nested beyond the JSON parser's depth: a RecursionError, not a ValueError


def position(offset, identity="log-a", generation=None):
    """A compact position cursor of the tool's own form (tools/bridge_compact_view.py): bc1. and the unpadded
    url-safe base64 of the sorted-key JSON of its three keys."""
    data = json.dumps({"offset": offset, "file_identity": identity, "generation": generation}, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")
    return "bc1." + base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def offset_of(cursor):
    encoded = cursor[len("bc1."):]
    return json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))["offset"]


GOOD, STALE = position(3), position(99)  # a cursor the fake compact tool knows, and one it refuses
AFTER, SEEDED = position(5), position(6)  # the cursors it hands out after a delta and a seed read (known too)
OTHER_LOG = position(5, identity="log-b")  # a cursor into another bridge log
SOURCE = {"file_identity": "log-a", "log_generation": None}  # the bridge log identity the log-a cursors name


def answer(code, out):
    return types.SimpleNamespace(returncode=code, stdout=out)


def view(refs, cursor, read=None):
    """A compact listing; stats.snapshot_events is the rows it read (by default as many as it lists)."""
    return json.dumps({"schema": "wd.bridge.compact-view.v1", "cursor": cursor,
                       "events": [{"ref": ref} for ref in refs],
                       "stats": {"listed": len(refs), "snapshot_events": len(refs) if read is None else read}}
                      ).encode("utf-8")


class FakeBridge:
    """Records each would-be reader start (its options) and answers it from EVENTS; starts no process."""

    def __init__(self, after=(), listing=None, detail=None):
        self.after, self.listing, self.detail, self.calls = list(after), listing, detail, []

    def __call__(self, argv, **kwargs):
        options = argv[3].split("Read-AgentBridge.ps1' -Compact ", 1)[1].split(" ")
        self.calls.append(options)
        if options[0] == "-AfterEvent" and self.listing is not None:
            return answer(0, self.listing)
        if options[0] == "-AfterEvent":
            return answer(0, view(self.after, AFTER)) if options[1] in (GOOD, AFTER, SEEDED) else answer(1, b"")
        if options[2:3] == ["-EventId"] and self.detail is not None:
            return answer(0, self.detail)
        if options[2:3] == ["-EventId"]:
            report = {"schema": "wd.bridge.event-detail.v1", "ref": options[3], "event": BY_REF[options[3]]}
            return answer(0, json.dumps(report).encode("utf-8"))
        return answer(0, view(REFS[-int(options[1]):], SEEDED))

    def detail_refs(self):
        return [call[3] for call in self.calls if call[2:3] == ["-EventId"]]


def logged(start, stop):
    """Distinct rows for a bounded log."""
    return [{"agent": "lane-b", "type": "message", "task_id": "lane-b/bounded-case",
             "ts_utc": f"2026-10-16T01:{n // 60:02d}:{n % 60:02d}Z", "message": f"logged {n}"}
            for n in range(start, stop)]


class BoundedBridge:
    """A log of rows read like the compact tool's bounded snapshot; starts no process. A cursor is the position
    of the rows before it; -AfterEvent lists the rows after it (and reports them as read), -ContinuityTail T the
    last T rows, and -ContinuityTail T -EventId ref answers only a ref among the last T rows. A refused read exits
    non-zero with no stdout, as the tool does; a ref in ``refused`` is refused in every read, and a ref in
    ``forged`` is answered with another row."""

    def __init__(self, events, refused=(), forged=()):
        self.events, self.refs, self.calls = [], [], []
        self.refused, self.forged = set(refused), set(forged)
        self.extend(events)

    def extend(self, events):
        self.events += events
        self.refs += [event_id(event) for event in events]

    def __call__(self, argv, **kwargs):
        options = argv[3].split("Read-AgentBridge.ps1' -Compact ", 1)[1].split(" ")
        self.calls.append(options)
        end = position(len(self.refs))
        if options[0] == "-AfterEvent":
            after = self.refs[offset_of(options[1]):]
            return answer(0, view(after, end))
        last = self.refs[-int(options[1]):]
        if options[2:3] != ["-EventId"]:
            return answer(0, view(last, end))
        ref = options[3]
        if ref not in last or ref in self.refused:
            return answer(2, b"")
        event = self.events[self.refs.index(ref) - 1 if ref in self.forged else self.refs.index(ref)]
        report = {"schema": "wd.bridge.event-detail.v1", "ref": ref, "event": event}
        return answer(0, json.dumps(report).encode("utf-8"))


class StartFails(FakeBridge):
    """Raises ``error`` at each start of the given read (``listing`` or ``detail``), as subprocess.run raises a
    timeout or a failed start; answers every other read like FakeBridge."""

    def __init__(self, error, read, after=()):
        super().__init__(after)
        self.error, self.read = error, read

    def __call__(self, argv, **kwargs):
        options = argv[3].split("Read-AgentBridge.ps1' -Compact ", 1)[1].split(" ")
        if ("detail" if options[2:3] == ["-EventId"] else "listing") == self.read:
            self.calls.append(options)
            raise self.error
        return super().__call__(argv, **kwargs)


def load(after=(), bridge=None, live=None):
    tree = ast.parse(PRODUCER.read_text(encoding="utf-8"))
    nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))
             or (isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) in CONSTANTS)]
    bridge = FakeBridge(after) if bridge is None else bridge
    live = Path("live") if live is None else live
    space = {"re": re, "json": json, "os": os, "hashlib": hashlib, "concurrent": concurrent, "time": time,
             "dt": datetime, "Path": Path, "base64": base64, "stat": stat, "tempfile": tempfile, "sys": sys,
             "bin_dir": Path("bin"), "live": live, "audit_root": live.parent,
             "subprocess": types.SimpleNamespace(run=bridge, TimeoutExpired=subprocess.TimeoutExpired),
             "args": types.SimpleNamespace(window=WINDOW, workers=1, seed_from=None, keep=2, repo="owner/repo"),
             "event_id": event_id, "core": core, "shadow": shadow, "ws": ws,
             "parse_bridge_json_object": parse_bridge_json_object}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(PRODUCER), "exec"), space)
    return space, bridge


@contextlib.contextmanager
def refused(error, reason):
    """pytest.raises(error) whose exception's only argument is exactly the fixed reason: a match= search, ^ and $
    included, would also pass the reason with a trailing newline."""
    with pytest.raises(error) as raised:
        yield raised
    assert raised.value.args == (reason,), raised.value.args


def rows_for(space, refs):
    """Window rows as the producer keeps them after a detail read."""
    return [{"ref": ref, "row_sha256": space["row_digest"](BY_REF[ref]), "source": "detail", "event": BY_REF[ref]}
            for ref in refs]


def committed(space, rows, cursor, generation=1):
    """A committed state of the producer's own form for these rows and this cursor."""
    return space["state_envelope"](generation, cursor, space["cursor_source"](cursor), rows)


def verified(space, rows):
    return all(row["ref"] == event_id(row["event"]) and row["event"] == BY_REF[row["ref"]]
               and row["row_sha256"] == space["row_digest"](row["event"]) for row in rows)


def logged_rows(space, events):
    """Window rows of a bounded log as the producer keeps them after a detail read."""
    return [{"ref": event_id(event), "row_sha256": space["row_digest"](event), "source": "detail", "event": event}
            for event in events]


def from_the_log(space, bridge, rows):
    return all(row["ref"] == event_id(row["event"]) and row["event"] == bridge.events[bridge.refs.index(row["ref"])]
               and row["row_sha256"] == space["row_digest"](row["event"]) for row in rows)


def listings(bridge):
    return [call for call in bridge.calls if call[2:3] != ["-EventId"]]


def test_without_a_committed_state_the_first_cycle_seeds_and_reads_every_listed_row():
    space, bridge = load()
    rows, cursor, source, record = space["advance"](None)
    assert bridge.calls[0] == ["-ContinuityTail", str(WINDOW)] and len(bridge.calls) == 1 + WINDOW
    assert bridge.calls[1:] == [["-ContinuityTail", str(WINDOW + 500), "-EventId", ref] for ref in REFS[-WINDOW:]]
    assert record["mode"] == "seed" and record["detail_calls"] == WINDOW and record["new_rows"] == WINDOW
    assert [row["ref"] for row in rows] == REFS[-WINDOW:] and verified(space, rows)
    assert (cursor, source) == (SEEDED, SOURCE) and "reseed_after" not in record


def test_a_committed_cursor_reads_only_the_rows_after_it_within_the_rows_it_listed():
    space, bridge = load(after=REFS[3:5])
    state = committed(space, rows_for(space, REFS[:3]), GOOD)
    rows, cursor, source, record = space["advance"](state)
    tail = str(space["DETAIL_TAIL"] + 2)  # the last DETAIL_TAIL lines plus the 2 rows the listing read after it
    assert bridge.calls == [["-AfterEvent", GOOD], ["-ContinuityTail", tail, "-EventId", REFS[3]],
                            ["-ContinuityTail", tail, "-EventId", REFS[4]]]
    assert record["mode"] == "delta" and record["new_rows"] == 2 and record["detail_calls"] == 2
    assert [row["ref"] for row in rows] == REFS[1:5] and verified(space, rows)
    assert (cursor, source) == (AFTER, SOURCE)


def test_a_committed_cursor_outside_the_reader_shapes_starts_no_reader_and_refuses_the_cycle():
    # the -AfterEvent read is refused before a start; nothing is listed again (no reseed)
    space, bridge = load()
    state = dict(committed(space, rows_for(space, REFS[:2]), GOOD), cursor="x y")
    with refused(space["CaptureRefused"], "reader_option_invalid"):
        space["advance"](state)
    assert bridge.calls == []


def test_a_committed_cursor_the_compact_tool_refuses_refuses_the_cycle_without_a_reseed():
    # also the case that once reseeded and read only the listed rows the window did not hold: no row is read again
    space, bridge = load()
    state = committed(space, rows_for(space, REFS[:4]), STALE)
    before = copy.deepcopy(state)
    with refused(space["CaptureRefused"], "compact_reader_failed"):
        space["advance"](state)
    assert bridge.calls == [["-AfterEvent", STALE]] and state == before


def test_a_delta_listing_without_the_view_schema_refuses_the_cycle_without_a_reseed():
    space, bridge = load()
    bridge.listing = json.dumps({"schema": "other", "cursor": AFTER, "events": []}).encode("utf-8")
    state = committed(space, rows_for(space, REFS[:2]), GOOD)
    with refused(space["CaptureRefused"], "compact_view_shape"):
        space["advance"](state)
    assert bridge.calls == [["-AfterEvent", GOOD]]


ROWS_READ_REFUSED = {  # a delta listing's stats.snapshot_events -> refused: without it no detail read is bounded
    "absent": {},
    "negative": {"snapshot_events": -1},
    "a_string": {"snapshot_events": "2"},
    "a_float": {"snapshot_events": 2.0},
    "true": {"snapshot_events": True},
    "null": {"snapshot_events": None},
}


@pytest.mark.parametrize("case", ROWS_READ_REFUSED)
def test_a_delta_listing_without_the_count_of_rows_it_read_refuses_before_any_detail_read(case):
    space, bridge = load()
    bridge.listing = json.dumps({"schema": "wd.bridge.compact-view.v1", "cursor": AFTER,
                                 "events": [{"ref": REFS[3]}], "stats": ROWS_READ_REFUSED[case]}).encode("utf-8")
    state = committed(space, rows_for(space, REFS[:3]), GOOD)
    with refused(space["CaptureRefused"], "compact_view_shape") as raised:
        space["advance"](state)
    assert type(raised.value) is space["CycleRefused"] and bridge.calls == [["-AfterEvent", GOOD]]


def test_a_delta_listing_whose_stats_is_not_an_object_refuses_before_any_detail_read():
    space, bridge = load()
    bridge.listing = json.dumps({"schema": "wd.bridge.compact-view.v1", "cursor": AFTER,
                                 "events": [{"ref": REFS[3]}], "stats": [2]}).encode("utf-8")
    state = committed(space, rows_for(space, REFS[:3]), GOOD)
    with refused(space["CaptureRefused"], "compact_view_shape"):
        space["advance"](state)
    assert bridge.calls == [["-AfterEvent", GOOD]]


def test_a_listed_ref_outside_the_reader_shapes_refuses_the_detail_read_before_a_start_and_the_cycle():
    space, bridge = load()
    bridge.listing = view(["not-a-ref"], AFTER)
    state = committed(space, rows_for(space, REFS[:2]), GOOD)
    with refused(space["CaptureRefused"], "reader_option_invalid"):
        space["advance"](state)
    assert bridge.calls == [["-AfterEvent", GOOD]]


def test_a_delta_whose_cursor_fell_far_behind_reads_every_listed_row_within_the_rows_read_after_it():
    # RCO1's L1: with -EventId over only the last DETAIL_TAIL lines, the oldest refs listed after a cursor that fell
    # further behind were refused; each detail read now spans the last DETAIL_TAIL lines plus every row the listing
    # read after the cursor, so every listed row lies within it, and the next cycle continues from the new state
    bridge = BoundedBridge([])
    space, _ = load(bridge=bridge)
    tail = space["DETAIL_TAIL"]
    bridge.extend(logged(0, tail + 10))
    state = committed(space, logged_rows(space, bridge.events[:WINDOW]), position(WINDOW))
    rows, cursor, source, record = space["advance"](state)
    listed = tail + 10 - WINDOW
    assert bridge.calls == [["-AfterEvent", position(WINDOW)]] + [
        ["-ContinuityTail", str(tail + listed), "-EventId", ref] for ref in bridge.refs[WINDOW:]]
    assert record["mode"] == "delta" and record["new_rows"] == listed and record["detail_calls"] == listed
    assert [row["ref"] for row in rows] == bridge.refs[-WINDOW:] and from_the_log(space, bridge, rows)
    assert (cursor, source) == (position(tail + 10), SOURCE)
    bridge.extend(logged(tail + 10, tail + 12))
    bridge.calls.clear()
    rows, cursor, _, record = space["advance"](committed(space, rows, cursor, 2))
    assert record["mode"] == "delta" and record["new_rows"] == 2
    assert bridge.calls == [["-AfterEvent", position(tail + 10)]] + [
        ["-ContinuityTail", str(tail + 2), "-EventId", ref] for ref in bridge.refs[-2:]]
    assert [row["ref"] for row in rows] == bridge.refs[-WINDOW:] and from_the_log(space, bridge, rows)


def test_a_delta_needing_more_than_the_tool_tail_bound_refuses_before_any_detail_read():
    space, bridge = load()
    cap, tail = space["TAIL_CAP"], space["DETAIL_TAIL"]
    bridge.listing = view(REFS[3:5], AFTER, read=cap - tail + 1)
    state = committed(space, rows_for(space, REFS[:3]), GOOD)
    with refused(space["CaptureRefused"], "delta_beyond_tail_cap") as raised:
        space["advance"](state)
    assert type(raised.value) is space["CycleRefused"] and bridge.calls == [["-AfterEvent", GOOD]]


def test_a_delta_at_the_tool_tail_bound_reads_with_that_bound():
    space, bridge = load()
    cap, tail = space["TAIL_CAP"], space["DETAIL_TAIL"]
    bridge.listing = view(REFS[3:4], AFTER, read=cap - tail)
    rows, _, _, record = space["advance"](committed(space, rows_for(space, REFS[:3]), GOOD))
    assert bridge.calls == [["-AfterEvent", GOOD], ["-ContinuityTail", str(cap), "-EventId", REFS[3]]]
    assert [row["ref"] for row in rows] == REFS[:4] and record["mode"] == "delta"


def test_a_delta_detail_report_of_another_row_refuses_the_cycle_and_names_the_mismatch():
    bridge = BoundedBridge(logged(0, 2 * WINDOW + 1))
    space, _ = load(bridge=bridge)
    bridge.forged.add(bridge.refs[WINDOW])  # every read of it answers the row before it
    state = committed(space, logged_rows(space, bridge.events[:WINDOW]), position(WINDOW))
    with refused(space["CaptureRefused"], "detail_ref_mismatch"):
        space["advance"](state)
    assert listings(bridge) == [["-AfterEvent", position(WINDOW)]]


def test_a_refused_detail_read_refuses_the_cycle_and_leaves_the_committed_state_as_it_was():
    # the case that once reseeded and reused the verified rows it listed again: now nothing is listed again
    bridge = BoundedBridge(logged(0, WINDOW + 3))
    space, _ = load(bridge=bridge)
    bridge.refused.add(bridge.refs[WINDOW])
    state = committed(space, logged_rows(space, bridge.events[:WINDOW]), position(WINDOW))
    before = copy.deepcopy(state)
    with refused(space["CaptureRefused"], "compact_reader_failed") as raised:
        space["advance"](state)
    assert type(raised.value) is space["CaptureRefused"] and state == before
    assert listings(bridge) == [["-AfterEvent", position(WINDOW)]]


def test_an_event_refused_in_every_read_refuses_every_cycle_from_the_same_committed_state():
    # RCO1's L2 under Lead's B3: no reseed, so the stall stays visible, also after more rows arrive (each read spans
    # every row after the cursor, so the refused row never leaves it); recovering is an explicit operator step
    bridge = BoundedBridge(logged(0, 2 * WINDOW))
    space, _ = load(bridge=bridge)
    bridge.refused.add(bridge.refs[WINDOW + 1])
    state = committed(space, logged_rows(space, bridge.events[:WINDOW]), position(WINDOW))
    before = copy.deepcopy(state)
    for more in (0, 2 * WINDOW):
        bridge.extend(logged(2 * WINDOW, 2 * WINDOW + more))
        bridge.calls.clear()
        with refused(space["CaptureRefused"], "compact_reader_failed"):
            space["advance"](state)
        assert listings(bridge) == [["-AfterEvent", position(WINDOW)]] and state == before


def test_a_listing_that_repeats_a_ref_is_read_and_kept_once():
    space, bridge = load(after=[REFS[3], REFS[3], REFS[4]])
    rows, _, _, record = space["advance"](committed(space, rows_for(space, REFS[:3]), GOOD))
    assert bridge.detail_refs() == REFS[3:5] and record["new_rows"] == 2
    assert [row["ref"] for row in rows] == REFS[1:5] and verified(space, rows)


def test_a_delta_listing_that_lists_a_window_row_again_does_not_read_or_keep_it_twice():
    space, bridge = load(after=REFS[2:4])
    rows, _, _, record = space["advance"](committed(space, rows_for(space, REFS[:3]), GOOD))
    assert bridge.detail_refs() == REFS[3:4] and record["new_rows"] == 1
    assert [row["ref"] for row in rows] == REFS[:4] and verified(space, rows)


def test_a_seed_listing_that_repeats_a_ref_reads_it_and_keeps_it_once():
    bridge = BoundedBridge(logged(0, 3) + logged(1, 2))  # the last row repeats the second
    space, _ = load(bridge=bridge)
    rows, _, _, record = space["advance"](None)
    assert bridge.calls == [["-ContinuityTail", str(WINDOW)]] + [
        ["-ContinuityTail", str(WINDOW + 500), "-EventId", ref] for ref in bridge.refs[:3]]
    assert [row["ref"] for row in rows] == bridge.refs[:3] and record["new_rows"] == 3
    assert from_the_log(space, bridge, rows)


ABSENT = object()


def listing_with(events):
    """A delta listing of the view schema and a known cursor whose events are ``events`` (no events key: ABSENT)."""
    listing = {"schema": "wd.bridge.compact-view.v1", "cursor": AFTER, "stats": {}}
    if events is not ABSENT:
        listing["events"] = events
    return json.dumps(listing).encode("utf-8")


def detail_of(ref, event):
    return json.dumps({"schema": "wd.bridge.event-detail.v1", "ref": ref, "event": event}).encode("utf-8")


LISTING_UNREADABLE = {  # a delta listing the producer cannot read -> its fixed reason: the cycle, no reseed
    "not_utf8": (b"\xff", "compact_output_unparseable"),
    "empty": (b"", "compact_output_unparseable"),
    "not_json": (b"not json", "compact_output_unparseable"),
    "nested_too_deep": (DEEP, "compact_output_unparseable"),
    "a_list": (b"[]", "compact_output_unparseable"),
    "null": (b"null", "compact_output_unparseable"),
    "a_string": (b'"x"', "compact_output_unparseable"),
    "without_events": (listing_with(ABSENT), "compact_events_shape"),
    "events_null": (listing_with(None), "compact_events_shape"),
    "events_a_number": (listing_with(5), "compact_events_shape"),
    "events_an_empty_object": (listing_with({}), "compact_events_shape"),  # read as no new rows before
    "events_an_empty_string": (listing_with(""), "compact_events_shape"),  # read as no new rows before
    "events_a_string": (listing_with("ab"), "compact_events_shape"),
    "events_an_object": (listing_with({"ref": REFS[3]}), "compact_events_shape"),
    "an_item_a_ref_string": (listing_with([REFS[3]]), "compact_events_shape"),
    "an_item_a_string_holding_ref": (listing_with(["the ref"]), "compact_events_shape"),
    "an_item_a_list_holding_ref": (listing_with([["ref"]]), "compact_events_shape"),
    "an_item_without_a_ref": (listing_with([{}]), "compact_events_shape"),
    "a_ref_a_list": (listing_with([{"ref": [REFS[3]]}]), "compact_events_shape"),
    "a_ref_an_object": (listing_with([{"ref": {"ref": REFS[3]}}]), "compact_events_shape"),
}


@pytest.mark.parametrize("case", LISTING_UNREADABLE)
def test_a_delta_listing_the_producer_cannot_read_refuses_the_cycle_without_a_reseed(case):
    out, reason = LISTING_UNREADABLE[case]
    space, bridge = load(after=REFS[3:5])
    bridge.listing = out
    state = committed(space, rows_for(space, REFS[:3]), GOOD)
    before = copy.deepcopy(state)
    with refused(space["CaptureRefused"], reason) as raised:
        space["advance"](state)
    assert type(raised.value) is space["CycleRefused"] and state == before
    assert bridge.calls == [["-AfterEvent", GOOD]]


NAN_ROW = dict(EVENTS[3], weight=float("nan"))  # the canonical form allows no non-finite number
SURROGATE_ROW = dict(EVENTS[3], message="\ud800")  # nor a lone surrogate
DETAIL_UNREADABLE = {  # the delta's one detail read answered with -> its fixed reason: the cycle, no reseed
    "not_utf8": (b"\xff", "compact_output_unparseable"),
    "not_json": (b"{", "compact_output_unparseable"),
    "nested_too_deep": (DEEP, "compact_output_unparseable"),
    "a_list": (b"[]", "compact_output_unparseable"),
    "null": (b"null", "compact_output_unparseable"),
    "an_event_with_a_nan": (detail_of(REFS[3], NAN_ROW), "detail_ref_unverifiable"),
    "an_event_with_a_lone_surrogate": (detail_of(REFS[3], SURROGATE_ROW), "detail_ref_unverifiable"),
}


@pytest.mark.parametrize("case", DETAIL_UNREADABLE)
def test_a_delta_detail_the_producer_cannot_read_refuses_the_cycle_without_a_reseed(case):
    out, reason = DETAIL_UNREADABLE[case]
    space, bridge = load(after=REFS[3:4])
    bridge.detail = out
    state = committed(space, rows_for(space, REFS[:3]), GOOD)
    before = copy.deepcopy(state)
    with refused(space["CaptureRefused"], reason) as raised:
        space["advance"](state)
    assert type(raised.value) is space["CycleRefused"] and state == before
    tail = str(space["DETAIL_TAIL"] + 1)
    assert bridge.calls == [["-AfterEvent", GOOD], ["-ContinuityTail", tail, "-EventId", REFS[3]]]


STARTS_RAISING = {
    "timed_out": (subprocess.TimeoutExpired(["pwsh"], 300), "compact_reader_timed_out"),
    "not_found": (FileNotFoundError(2, "not found"), "compact_reader_not_started"),
    "not_permitted": (PermissionError(13, "denied"), "compact_reader_not_started"),
}


@pytest.mark.parametrize("read", ["listing", "detail"])
@pytest.mark.parametrize("case", STARTS_RAISING)
def test_a_delta_reader_that_times_out_or_cannot_start_refuses_the_cycle_without_a_reseed(case, read):
    error, reason = STARTS_RAISING[case]
    bridge = StartFails(error, read, after=REFS[3:4])
    space, _ = load(bridge=bridge)
    state = committed(space, rows_for(space, REFS[:3]), GOOD)
    before = copy.deepcopy(state)
    with refused(space["CaptureRefused"], reason) as raised:
        space["advance"](state)
    assert type(raised.value) is space["CycleRefused"] and state == before
    assert bridge.calls[0] == ["-AfterEvent", GOOD] and ["-ContinuityTail", str(WINDOW)] not in bridge.calls


def test_a_seed_reader_that_cannot_start_refuses_the_cycle_with_its_reason(tmp_path):
    bridge = StartFails(FileNotFoundError(2, "not found"), "listing")
    space, _ = load(bridge=bridge, live=tmp_path)
    with refused(space["CaptureRefused"], "compact_reader_not_started") as raised:
        space["advance"](None)
    assert type(raised.value) is space["CycleRefused"] and bridge.calls == [["-ContinuityTail", str(WINDOW)]]
    assert list(tmp_path.iterdir()) == []  # the read writes nothing


REF_SHAPES_REFUSED = {  # a listed scalar ref not of the ref shape -> no detail read starts for it; refuses the cycle
    "a_number": 5,
    "null": None,
    "true": True,
    "an_upper_case_ref": REFS[3].upper(),
    "a_count": "600",
    "a_flag": "-AfterEvent",
}


@pytest.mark.parametrize("case", REF_SHAPES_REFUSED)
def test_a_listed_ref_of_another_scalar_shape_starts_no_detail_read_for_it_and_refuses_the_cycle(case):
    space, bridge = load()
    bridge.listing = view([REF_SHAPES_REFUSED[case]], AFTER)
    with refused(space["CaptureRefused"], "reader_option_invalid"):
        space["advance"](committed(space, rows_for(space, REFS[:2]), GOOD))
    assert bridge.calls == [["-AfterEvent", GOOD]]


def test_a_delta_listing_whose_cursor_is_not_a_string_refuses_the_cycle_and_names_it():
    space, bridge = load()
    bridge.listing = json.dumps({"schema": "wd.bridge.compact-view.v1", "cursor": 5, "events": []}).encode("utf-8")
    with refused(space["CaptureRefused"], "compact_view_shape"):
        space["advance"](committed(space, rows_for(space, REFS[:2]), GOOD))
    assert bridge.calls == [["-AfterEvent", GOOD]]


NEW_CURSOR_REFUSED = {  # the cursor a listing hands out -> refused: the state must keep naming one bridge log
    "another_bridge_log": (OTHER_LOG, "state_source_changed"),
    "another_log_generation": (position(5, generation="g2"), "state_source_changed"),
    "not_a_position": ("bc1." + base64.urlsafe_b64encode(b'"good"').decode("ascii").rstrip("="),
                       "compact_cursor_invalid"),
    "a_position_with_another_key": ("bc1." + base64.urlsafe_b64encode(
        b'{"file_identity":"log-a","generation":null,"offset":5,"x":1}').decode("ascii").rstrip("="),
        "compact_cursor_invalid"),
    "an_offset_not_an_int": (position("5"), "compact_cursor_invalid"),
    "a_generation_not_a_string": (position(5, generation=7), "compact_cursor_invalid"),
}


@pytest.mark.parametrize("case", NEW_CURSOR_REFUSED)
def test_a_delta_whose_new_cursor_names_no_or_another_bridge_log_refuses_the_cycle(case):
    cursor, reason = NEW_CURSOR_REFUSED[case]
    space, bridge = load()
    bridge.listing = view(REFS[3:4], cursor)
    state = committed(space, rows_for(space, REFS[:3]), GOOD)
    before = copy.deepcopy(state)
    with refused(space["CaptureRefused"], reason) as raised:
        space["advance"](state)
    assert type(raised.value) is space["CycleRefused"] and state == before


def test_a_seed_whose_cursor_names_no_bridge_log_refuses_the_cycle():
    space, bridge = load()

    def seed_listing(argv, **kwargs):
        options = argv[3].split("Read-AgentBridge.ps1' -Compact ", 1)[1].split(" ")
        bridge.calls.append(options)
        if options[2:3] == ["-EventId"]:
            return answer(0, detail_of(options[3], BY_REF[options[3]]))
        return answer(0, view(REFS[-WINDOW:], "bc1.eA"))

    space["subprocess"] = types.SimpleNamespace(run=seed_listing, TimeoutExpired=subprocess.TimeoutExpired)
    with refused(space["CaptureRefused"], "compact_cursor_invalid"):
        space["advance"](None)


def test_a_delta_listing_with_no_events_keeps_the_window_and_takes_the_new_cursor():
    space, bridge = load()
    rows, cursor, _, record = space["advance"](committed(space, rows_for(space, REFS[:3]), GOOD))
    assert bridge.calls == [["-AfterEvent", GOOD]] and (record["mode"], record["new_rows"]) == ("delta", 0)
    assert [row["ref"] for row in rows] == REFS[:3] and verified(space, rows) and cursor == AFTER


def test_an_empty_committed_window_continues_as_a_delta_from_its_cursor():
    # the earlier producer listed the window again for an empty one; a committed empty window is still the state
    space, bridge = load(after=REFS[3:5])
    rows, cursor, _, record = space["advance"](committed(space, [], GOOD))
    assert bridge.calls[0] == ["-AfterEvent", GOOD] and ["-ContinuityTail", str(WINDOW)] not in bridge.calls
    assert record["mode"] == "delta" and [row["ref"] for row in rows] == REFS[3:5] and verified(space, rows)


def test_the_state_a_cycle_commits_reads_back_and_verifies_for_the_next_cycle(tmp_path):
    # else every cycle would refuse: the rows must pass the check after their JSON round trip, as written
    space, bridge = load(after=REFS[5:6])
    rows, cursor, source, _ = space["advance"](None)
    state = space["state_envelope"](1, cursor, source, rows)
    data = space["compact"](state)  # the bytes the candidate writes as state.json
    path = tmp_path / "state.json"
    path.write_bytes(data)
    again = space["read_state"](path, hashlib.sha256(data).hexdigest(), 1)
    assert again == state and set(again) == space["STATE_KEYS"] and again["schema"] == space["STATE_SCHEMA"]
    assert again["window_sha256"] == hashlib.sha256(space["window_bytes"](rows)).hexdigest()
    rows, cursor, _, record = space["advance"](again)
    assert record["mode"] == "delta" and bridge.calls[1 + WINDOW] == ["-AfterEvent", SEEDED] and verified(space, rows)


def corrupt_state(space, case):
    """The bytes of a committed state with one fault, the sha256 latest.json names for it and its generation; the
    faulty row cases keep window_sha256 consistent, so the row fault is the only one."""
    rows = rows_for(space, REFS[:3])
    state = committed(space, rows, GOOD, 3)
    digest, generation = None, 3
    if case == "missing" or case == "a_directory":
        return None, "0" * 64, generation
    if case == "another_generation_named":
        generation = 2
    elif case == "generation_a_boolean":
        state["generation"], generation = True, 1
    elif case == "another_schema":
        state["schema"] = "wd.other.v1"
    elif case == "an_extra_key":
        state["note"] = 1
    elif case == "a_missing_key":
        del state["source"]
    elif case == "window_not_a_list":
        state["window"] = {}
    elif case == "a_cursor_not_naming_its_source":
        state["source"] = {"file_identity": "log-b", "log_generation": None}
    elif case == "a_cursor_not_a_position":
        state["cursor"] = "bc1.eA"
    elif case == "window_sha256_altered":
        state["window_sha256"] = "0" * 64
    elif case.startswith("row_") or case == "window_with_a_repeated_row":
        if case == "window_with_a_repeated_row":
            rows.append(dict(rows[1]))
        elif case == "row_with_an_extra_key":
            rows[1]["note"] = "x"
        elif case == "row_with_a_foreign_source":
            rows[1]["source"] = "forged"
        elif case == "row_event_altered":  # a consistent forgery: only the ref no longer matches
            rows[1]["event"] = dict(rows[1]["event"], message="altered")
            rows[1]["row_sha256"] = space["row_digest"](rows[1]["event"])
        elif case == "row_digest_altered":
            rows[1]["row_sha256"] = "0" * 64
        elif case == "row_event_not_an_object":
            rows[1] = {"ref": event_id([1]), "row_sha256": space["row_digest"]([1]), "source": "detail", "event": [1]}
        state["window"] = rows
        state["window_sha256"] = hashlib.sha256(space["window_bytes"](rows)).hexdigest()
    data = space["compact"](state)
    if case == "bytes_changed_after_commit":
        digest = hashlib.sha256(data).hexdigest()
        data = data.replace(b"row 1", b"row 9")
    elif case == "not_utf8":
        data = b"\xff" + data
    elif case == "not_json":
        data = b"{"
    elif case == "nested_too_deep":
        data = DEEP
    elif case == "a_list":
        data = b"[]"
    elif case == "a_duplicate_key":
        data = data.replace(b'{"cursor"', b'{"generation":3,"cursor"', 1)
    elif case == "a_nan":
        data = data.replace(b'"weight":0.1', b'"weight":NaN', 1)
    return data, digest or hashlib.sha256(data).hexdigest(), generation


CORRUPT_STATE = ("missing", "a_directory", "bytes_changed_after_commit", "not_utf8", "not_json", "nested_too_deep",
                 "a_list", "a_duplicate_key", "a_nan", "another_generation_named", "generation_a_boolean",
                 "another_schema", "an_extra_key", "a_missing_key", "window_not_a_list",
                 "a_cursor_not_naming_its_source", "a_cursor_not_a_position", "window_sha256_altered",
                 "row_with_an_extra_key", "row_with_a_foreign_source", "row_event_altered", "row_digest_altered",
                 "row_event_not_an_object", "window_with_a_repeated_row")


@pytest.mark.parametrize("case", CORRUPT_STATE)
def test_a_committed_state_the_producer_cannot_verify_refuses_the_cycle_and_nothing_of_it_is_used(tmp_path, case):
    # each fault the earlier window/cursor pair reseeded over now refuses: nothing of the state is reused
    space, bridge = load()
    data, digest, generation = corrupt_state(space, case)
    path = tmp_path / "state.json"
    if case == "a_directory":
        path.mkdir()
    elif data is not None:
        path.write_bytes(data)
    with refused(space["CaptureRefused"], "committed_state_unverified") as raised:
        space["read_state"](path, digest, generation)
    assert type(raised.value) is space["CycleRefused"] and bridge.calls == []


DIGIT_LIMIT = sys.get_int_max_str_digits()  # 0 when no limit is configured (-X int_max_str_digits=0)


def capture_bytes(events, **keys):
    """A --seed-from file as the producer writes a capture (``capture_object``), with these events and keys."""
    return json.dumps({"schema": "wd.bridge-mind-capture.v1", **keys, "events": events},
                      ensure_ascii=False).encode("utf-8")


def test_a_seed_row_whose_own_ref_is_listed_is_reused_and_only_the_other_listed_rows_are_read(tmp_path):
    space, bridge = load()
    seed = tmp_path / "seed.json"
    seed.write_bytes(capture_bytes([EVENTS[0], *EVENTS[2:5]]))  # EVENTS[0] is not among the last WINDOW rows
    rows, _, _, record = space["advance"](None, space["read_seed"](seed))
    assert bridge.calls[0] == ["-ContinuityTail", str(WINDOW)] and bridge.detail_refs() == REFS[5:]
    assert [row["source"] for row in rows] == ["seed", "seed", "seed", "detail"] and verified(space, rows)
    assert [row["ref"] for row in rows] == REFS[-WINDOW:] and record["mode"] == "seed"
    assert (record["new_rows"], record["detail_calls"]) == (WINDOW, 1)


def test_a_committed_state_never_reuses_seed_rows():
    # --seed-from serves a fresh start only: a delta reads each fresh listed row
    space, bridge = load(after=REFS[3:5])
    seeded = {ref: BY_REF[ref] for ref in REFS}
    rows, _, _, record = space["advance"](committed(space, rows_for(space, REFS[:3]), GOOD), seeded)
    assert bridge.detail_refs() == REFS[3:5] and [row["source"] for row in rows][-2:] == ["detail", "detail"]


def test_advance_reads_no_seed_file_itself(tmp_path):
    # the module reads --seed-from once, before any write or process start, and hands advance its rows
    space, bridge = load()
    space["args"].seed_from = tmp_path / "missing.json"
    rows, _, _, record = space["advance"](None)
    assert bridge.detail_refs() == REFS[-WINDOW:] and record["detail_calls"] == WINDOW and verified(space, rows)


SEED_REFUSED = {  # a --seed-from file -> its fixed reason; None: no file (missing), or a directory in its place
    "missing": (None, "seed_unreadable"),
    "a_directory": (None, "seed_unreadable"),
    "empty": (b"", "seed_unparseable"),
    "not_utf8": (b"\xff" + capture_bytes(EVENTS[:1]), "seed_unparseable"),
    "a_utf8_bom": (b"\xef\xbb\xbf" + capture_bytes(EVENTS[:1]), "seed_unparseable"),
    "not_json": (b"{", "seed_unparseable"),
    "nested_too_deep": (DEEP, "seed_unparseable"),
    "an_int_literal_over_the_digit_limit": (b'{"events": [{"n": ' + b"1" * (DIGIT_LIMIT + 1) + b"}]}",
                                            "seed_unparseable"),
    "a_list": (b"[]", "seed_shape_unexpected"),
    "a_string": (b'"events"', "seed_shape_unexpected"),
    "null": (b"null", "seed_shape_unexpected"),
    "no_events": (b'{"schema": "wd.bridge-mind-capture.v1"}', "seed_shape_unexpected"),
    "events_null": (b'{"events": null}', "seed_shape_unexpected"),
    "events_an_object": (b'{"events": {}}', "seed_shape_unexpected"),
    "events_a_string": (b'{"events": "ab"}', "seed_shape_unexpected"),
    "events_true": (b'{"events": true}', "seed_shape_unexpected"),
    "an_event_not_an_object": (capture_bytes([EVENTS[0], [1]]), "seed_shape_unexpected"),
    "an_event_null": (capture_bytes([EVENTS[0], None]), "seed_shape_unexpected"),
    "an_event_with_nan": (json.dumps({"events": [NAN_ROW]}).encode("ascii"), "seed_ref_unverifiable"),
    "an_event_with_an_infinite_number": (b'{"events": [{"n": 1e999}]}', "seed_ref_unverifiable"),
    "an_event_with_a_lone_surrogate": (json.dumps({"events": [SURROGATE_ROW]}).encode("ascii"),
                                       "seed_ref_unverifiable"),
}


@pytest.mark.parametrize("case", SEED_REFUSED)
def test_a_seed_file_the_producer_cannot_read_whole_is_refused_with_its_fixed_reason(tmp_path, case):
    data, reason = SEED_REFUSED[case]
    if case == "an_int_literal_over_the_digit_limit" and not DIGIT_LIMIT:
        pytest.skip("no int digit limit is configured (int_max_str_digits=0)")
    space, bridge = load()
    seed = tmp_path / "seed.json"
    if case == "a_directory":
        seed.mkdir()
    elif data is not None:
        seed.write_bytes(data)
    with refused(space["SeedRefused"], reason):
        space["read_seed"](seed)
    assert bridge.calls == []


def test_a_seed_row_whose_canonical_ref_raises_recursion_error_is_refused_as_unverifiable(tmp_path):
    # a stand-in canonical ref (no real row was found that parses and then raises it): the ref step is held to its
    # fixed reason all the same, as the committed state's check is
    space, _ = load()

    def deep_ref(event):
        raise RecursionError("maximum recursion depth exceeded while encoding a JSON object")

    space["event_id"] = deep_ref
    seed = tmp_path / "seed.json"
    seed.write_bytes(capture_bytes(EVENTS[:1]))
    with refused(space["SeedRefused"], "seed_ref_unverifiable"):
        space["read_seed"](seed)


SEED_ACCEPTED = {  # a --seed-from file -> the EVENTS it is read as (their indexes), each keyed by its own ref
    "an_empty_event_list": (capture_bytes([]), []),
    "a_capture_with_its_other_keys": (capture_bytes(EVENTS[:3], reader="Read-AgentBridge.ps1 -Compact -EventId",
                                                    observed_utc="2026-10-16T00:00:00Z"), [0, 1, 2]),
    "events_only": (json.dumps({"events": EVENTS[2:4]}).encode("ascii"), [2, 3]),
    "a_repeated_row": (capture_bytes([EVENTS[1], EVENTS[4], EVENTS[1]]), [1, 4]),
    "another_schema": (capture_bytes(EVENTS[5:], schema="wd.other.v9"), [5]),
}


@pytest.mark.parametrize("case", SEED_ACCEPTED)
def test_a_seed_file_read_whole_maps_each_row_to_its_own_canonical_ref(tmp_path, case):
    # a row is reused only when its own canonical ref equals a listed one, so the file's schema is not checked
    data, indexes = SEED_ACCEPTED[case]
    space, _ = load()
    seed = tmp_path / "seed.json"
    seed.write_bytes(data)
    assert space["read_seed"](seed) == {REFS[n]: EVENTS[n] for n in indexes}


SCHEMA = "wd.bridge-mind-shadow-latest.v2"  # the producer's own latest.json schema
EARLIER_SCHEMA = "wd.fable5.mind-shadow-live-latest.v1"  # the earlier window/cursor producer's
CYCLE = "20261016T000000Z"  # a cycle dir name of the producer's form (cycle_name)
HEX = {name: (letter * 64) for name, letter in (("capture", "a"), ("context", "b"), ("snapshot", "c"),
                                                ("board", "d"), ("state", "e"))}


def last_ok_of(name, **keys):
    """A last_ok of the kind the producer commits for cycle dir ``name``, with these keys changed."""
    base = f"cycles/{name}/"
    value = {"cycle_dir": name, "generation": 1, "observed_utc": "2026-10-16T00:00:00Z",
             "capture_file": base + "capture.json", "capture_sha256": HEX["capture"],
             "context_file": base + "context.json", "context_sha256": HEX["context"],
             "state_file": base + "state.json", "state_sha256": HEX["state"],
             "publication_file": base + "run/reflected.publication.json", "board_file": base + "run/board.txt",
             "receipt_file": base + "run/receipt.json", "reader": "Read-AgentBridge.ps1 -Compact -EventId",
             "snapshot_sha256": HEX["snapshot"], "board_text_sha256": HEX["board"],
             "counts": {"prs": 1, "lanes": 2, "facts": 3, "tensions": 0, "attention": 1}, "global_question": None,
             "measures": {"capture_ms": 5, "events": 3, "new_rows": 3}}
    value.update(keys)
    return value


LAST_OK = last_ok_of(CYCLE)
OTHER_DIGITS = "".join(chr(0x0660 + int(c)) if c.isdigit() else c for c in CYCLE)  # Arabic-Indic: \d matches them


def latest_bytes(last_ok=ABSENT, **keys):
    """A latest.json of the producer's schema (or of the schema in ``keys``) whose last_ok is ``last_ok`` (ABSENT: no
    such key), every non-ASCII character escaped and a NaN written as NaN."""
    value = {"schema": SCHEMA, "authority": "none", "committed_utc": "2026-10-16T00:00:01.000000Z", **keys}
    if last_ok is not ABSENT:
        value["last_ok"] = last_ok
    return json.dumps(value, indent=2, sort_keys=True).encode("ascii")


def with_number(literal):
    """A latest.json naming LAST_OK whose extra number n is the JSON text ``literal``."""
    return latest_bytes(LAST_OK, n=0).replace(b'"n": 0', b'"n": ' + literal)


def without(key):
    return {name: value for name, value in LAST_OK.items() if name != key}


LATEST_REFUSED = {  # a latest.json -> its fixed reason; None: a directory in its place
    "a_directory": (None, "latest_unreadable"),
    "empty": (b"", "latest_unparseable"),
    "not_ascii": (latest_bytes(LAST_OK, note="n").replace(b'"n"', b'"n' + chr(0xE4).encode("utf-8") + b'"'),
                  "latest_unparseable"),
    "a_utf8_bom": (b"\xef\xbb\xbf" + latest_bytes(LAST_OK), "latest_unparseable"),
    "not_json": (b"{", "latest_unparseable"),
    "nested_too_deep": (DEEP, "latest_unparseable"),
    "an_int_literal_over_the_digit_limit": (with_number(b"1" * (DIGIT_LIMIT + 1)), "latest_unparseable"),
    "a_nan": (latest_bytes(dict(LAST_OK, measures={"run_ms": float("nan")})), "latest_unparseable"),
    "an_infinity_literal": (with_number(b"-Infinity"), "latest_unparseable"),
    "an_infinite_number": (with_number(b"1e999"), "latest_unparseable"),
    "a_list": (b"[]", "latest_shape_unexpected"),
    "a_string": (b'"latest"', "latest_shape_unexpected"),
    "null": (b"null", "latest_shape_unexpected"),
    # live_mind's schema: a live dir of that tool, whose latest.json names no last_ok, is not this producer's
    "another_tools_latest_json": (latest_bytes(schema="wd.bridge-mind-live-latest.v1", cycle_dir=CYCLE),
                                  "latest_shape_unexpected"),
    # the earlier window/cursor producer's own: fresh production state only, nothing migrated (Lead's B2)
    "the_earlier_producers_latest_json": (latest_bytes({"cycle_dir": CYCLE, "capture_file": f"cycles/{CYCLE}/capture"
                                                        ".json"}, schema=EARLIER_SCHEMA, cycle=1, status="ok"),
                                          "latest_shape_unexpected"),
    "the_earlier_schema_with_this_last_ok": (latest_bytes(LAST_OK, schema=EARLIER_SCHEMA), "latest_shape_unexpected"),
    "another_schema_with_a_last_ok": (latest_bytes(LAST_OK, schema="wd.other.v1"), "latest_shape_unexpected"),
    "no_schema": (json.dumps({"last_ok": LAST_OK}).encode("ascii"), "latest_shape_unexpected"),
    "no_last_ok": (latest_bytes(), "latest_shape_unexpected"),
    "last_ok_null": (latest_bytes(None), "latest_shape_unexpected"),  # latest.json changes only on a commit
    "last_ok_false": (latest_bytes(False), "latest_shape_unexpected"),
    "last_ok_zero": (latest_bytes(0), "latest_shape_unexpected"),
    "last_ok_an_empty_list": (latest_bytes([]), "latest_shape_unexpected"),
    "last_ok_an_empty_object": (latest_bytes({}), "latest_shape_unexpected"),
    "last_ok_a_string": (latest_bytes(CYCLE), "latest_shape_unexpected"),
    "last_ok_a_list": (latest_bytes([LAST_OK]), "latest_shape_unexpected"),
    "cycle_dir_missing": (latest_bytes(without("cycle_dir")), "latest_shape_unexpected"),
    "cycle_dir_null": (latest_bytes(dict(LAST_OK, cycle_dir=None)), "latest_shape_unexpected"),
    "cycle_dir_a_number": (latest_bytes(dict(LAST_OK, cycle_dir=20261016)), "latest_shape_unexpected"),
    "cycle_dir_empty": (latest_bytes(dict(LAST_OK, cycle_dir="")), "latest_shape_unexpected"),
    "cycle_dir_a_parent_step": (latest_bytes(last_ok_of("../" + CYCLE)), "latest_shape_unexpected"),
    "cycle_dir_a_path_below_one": (latest_bytes(last_ok_of(CYCLE + "/run")), "latest_shape_unexpected"),
    "cycle_dir_absolute": (latest_bytes(last_ok_of("C:/" + CYCLE)), "latest_shape_unexpected"),
    "cycle_dir_with_a_trailing_newline": (latest_bytes(last_ok_of(CYCLE + "\n")), "latest_shape_unexpected"),
    "cycle_dir_with_other_digits": (latest_bytes(last_ok_of(OTHER_DIGITS)), "latest_shape_unexpected"),
    "cycle_dir_with_its_separators": (latest_bytes(last_ok_of("2026-10-16T00:00:00Z")), "latest_shape_unexpected"),
    "generation_missing": (latest_bytes(without("generation")), "latest_shape_unexpected"),
    "generation_zero": (latest_bytes(dict(LAST_OK, generation=0)), "latest_shape_unexpected"),
    "generation_true": (latest_bytes(dict(LAST_OK, generation=True)), "latest_shape_unexpected"),
    "generation_a_string": (latest_bytes(dict(LAST_OK, generation="1")), "latest_shape_unexpected"),
    "generation_a_float": (latest_bytes(dict(LAST_OK, generation=1.0)), "latest_shape_unexpected"),
    "capture_file_elsewhere": (latest_bytes(dict(LAST_OK, capture_file="elsewhere")), "latest_shape_unexpected"),
    "state_file_in_another_cycle": (latest_bytes(dict(LAST_OK, state_file="cycles/20261015T000000Z/state.json")),
                                    "latest_shape_unexpected"),
    "publication_file_elsewhere": (latest_bytes(dict(LAST_OK, publication_file="run/reflected.publication.json")),
                                   "latest_shape_unexpected"),
    "board_file_missing": (latest_bytes(without("board_file")), "latest_shape_unexpected"),
    "receipt_file_elsewhere": (latest_bytes(dict(LAST_OK, receipt_file=f"cycles/{CYCLE}/receipt.json")),
                               "latest_shape_unexpected"),
    "capture_sha256_upper_case": (latest_bytes(dict(LAST_OK, capture_sha256="A" * 64)), "latest_shape_unexpected"),
    "state_sha256_short": (latest_bytes(dict(LAST_OK, state_sha256="e" * 63)), "latest_shape_unexpected"),
    "snapshot_sha256_missing": (latest_bytes(without("snapshot_sha256")), "latest_shape_unexpected"),
    "state_sha256_null": (latest_bytes(dict(LAST_OK, state_sha256=None)), "latest_shape_unexpected"),
}


@pytest.mark.parametrize("case", LATEST_REFUSED)
def test_a_latest_json_the_producer_cannot_read_whole_is_refused_with_its_fixed_reason(tmp_path, case):
    data, reason = LATEST_REFUSED[case]
    if case == "an_int_literal_over_the_digit_limit" and not DIGIT_LIMIT:
        pytest.skip("no int digit limit is configured (int_max_str_digits=0)")
    space, bridge = load()
    path = tmp_path / "latest.json"
    if data is None:
        path.mkdir()
    else:
        path.write_bytes(data)
    with refused(space["LatestRefused"], reason):
        space["read_latest"](path)
    assert bridge.calls == []


ELSEWHERE = dict(LAST_OK, context_file=None, context_sha256=None, extra=[1, "x"])
QUESTION = dict(LAST_OK, global_question="q " + chr(0xE4))
LATEST_ACCEPTED = {  # a latest.json -> the last_ok it is read as; None: no file (a first start)
    "missing": (None, None),
    "a_last_ok": (latest_bytes(LAST_OK), LAST_OK),
    "a_later_generation": (latest_bytes(dict(LAST_OK, generation=12)), dict(LAST_OK, generation=12)),
    "other_last_ok_keys_as_read": (latest_bytes(ELSEWHERE), ELSEWHERE),  # not read: carried forward as read
    "an_escaped_character": (latest_bytes(QUESTION), QUESTION),
    "its_other_keys_not_read": (latest_bytes(LAST_OK, scope="x", committed_utc=None), LAST_OK),
}


@pytest.mark.parametrize("case", LATEST_ACCEPTED)
def test_a_latest_json_read_whole_gives_the_last_ok_it_names_as_read(tmp_path, case):
    data, last_ok = LATEST_ACCEPTED[case]
    space, bridge = load()
    path = tmp_path / "latest.json"
    if data is not None:
        path.write_bytes(data)
    assert space["read_latest"](path) == last_ok and bridge.calls == []


def test_every_latest_json_the_producer_writes_is_read_back_whole_on_the_next_start(tmp_path):
    # the writer's own forms (cycle_name, latest_object, canonical), with a non-ASCII question and a later generation
    space, _ = load()
    name = space["cycle_name"]()
    path = tmp_path / "latest.json"
    for last_ok in (last_ok_of(name), last_ok_of(name, generation=9, global_question="q " + chr(0xE4))):
        path.write_bytes(space["canonical"](space["latest_object"](last_ok)))
        assert space["read_latest"](path) == last_ok
        latest = json.loads(path.read_bytes().decode("ascii"))
        assert latest["schema"] == SCHEMA and latest["authority"] == "none" and latest["last_ok"] == last_ok


@pytest.mark.parametrize("kind", ["a_directory_with_its_pair", "a_file", "an_empty_directory"])
def test_a_live_dir_holding_the_earlier_producers_state_is_refused_and_never_read(tmp_path, kind):
    space, bridge = load()
    state = tmp_path / "state"
    if kind == "a_file":
        state.write_bytes(b"")
    else:
        state.mkdir()
        if kind == "a_directory_with_its_pair":
            (state / "window.jsonl").write_bytes(b"not read")
            (state / "cursor.txt").write_bytes(b"not read")
    before = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))
    with refused(space["LiveDirRefused"], "live_dir_legacy_state"):
        space["refuse_legacy"](tmp_path)
    assert sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*")) == before and bridge.calls == []


def test_a_dangling_link_named_like_the_earlier_state_is_refused_too(tmp_path):
    # a stand-in path check: lexists, which holds for a link whose target is gone, decides (not exists)
    space, _ = load()
    space["os"] = types.SimpleNamespace(path=types.SimpleNamespace(lexists=lambda path: path == tmp_path / "state"))
    with refused(space["LiveDirRefused"], "live_dir_legacy_state"):
        space["refuse_legacy"](tmp_path)


def test_a_fresh_live_dir_or_one_this_producer_made_is_not_refused(tmp_path):
    space, _ = load()
    assert space["refuse_legacy"](tmp_path / "missing") is None  # no live dir yet: a fresh start
    for name in ("cycles", "files-cache"):
        (tmp_path / name).mkdir()
    for name in ("latest.json", "live-log.jsonl", "producer.lock", "last-refusal.json"):
        (tmp_path / name).write_bytes(b"")
    assert space["refuse_legacy"](tmp_path) is None


def test_a_live_dir_lock_is_held_without_waiting_until_its_descriptor_is_closed(tmp_path):
    # this platform's real lock: a second descriptor is refused at once while the first is open, then gets it
    space, bridge = load()
    path = tmp_path / "producer.lock"
    first = space["lock_live_dir"](path)
    try:  # read through another handle while held: on Windows a lock of the file's first byte refuses that read
        assert path.read_bytes() == b"" and not os.get_inheritable(first)
        began = time.perf_counter()
        with refused(space["LiveDirRefused"], "live_dir_in_use"):
            space["lock_live_dir"](path)
        assert time.perf_counter() - began < 5  # without waiting (a waiting lock retries for seconds)
    finally:
        os.close(first)
    os.close(space["lock_live_dir"](path))  # the lock went with its descriptor
    assert bridge.calls == []


def test_a_live_dir_lock_file_keeps_its_bytes(tmp_path):
    space, _ = load()
    path = tmp_path / "producer.lock"
    path.write_bytes(b"not this producer's data")
    os.close(space["lock_live_dir"](path))
    assert path.read_bytes() == b"not this producer's data"  # opened, never truncated or written


def test_a_live_dir_lock_file_that_cannot_be_opened_is_refused(tmp_path):
    space, _ = load()
    path = tmp_path / "producer.lock"
    path.mkdir()  # a directory in its place
    with refused(space["LiveDirRefused"], "live_dir_unlockable"):
        space["lock_live_dir"](path)


LOCK_CALLS = {  # os.name, the failing call and its error (None: it locks) -> the fixed reason (None: the descriptor)
    "windows_locks": ("nt", None, None, None),
    "windows_seek_error": ("nt", "lseek", OSError(22, "seek"), "live_dir_unlockable"),
    "windows_held": ("nt", "lock", PermissionError(13, "held"), "live_dir_in_use"),
    "windows_other_error": ("nt", "lock", OSError(36, "deadlock"), "live_dir_unlockable"),
    "elsewhere_locks": ("posix", None, None, None),
    "elsewhere_held": ("posix", "lock", BlockingIOError(11, "held"), "live_dir_in_use"),
    "elsewhere_other_error": ("posix", "lock", OSError(37, "no locks"), "live_dir_unlockable"),
}


@pytest.mark.parametrize("case", LOCK_CALLS)
def test_each_platform_locks_without_waiting_and_names_a_refused_lock(monkeypatch, tmp_path, case):
    # a stand-in lock module of each platform (imported where lock_live_dir uses it) and a stand-in seek record the
    # calls in order; the real fcntl does not run here, so its own semantics are the module's, not measured
    platform, failing, error, reason = LOCK_CALLS[case]
    space, _ = load()
    calls, opened, closed = [], [], []

    def recorded(name):
        def call(*options):
            calls.append((name, *options))
            if name == failing:
                raise error
        return call

    lock_call = recorded("lock")

    def opener(*options):
        fd = os.open(*options)
        opened.append((fd, options[1:]))
        return fd

    def closer(fd):
        closed.append(fd)
        os.close(fd)

    if platform == "nt":  # distinct values, so the call names its mode
        monkeypatch.setitem(sys.modules, "msvcrt", types.SimpleNamespace(LK_LOCK=1, LK_NBLCK=2, LK_RLCK=3,
                                                                          LK_NBRLCK=4, locking=lock_call))
    else:
        monkeypatch.setitem(sys.modules, "fcntl", types.SimpleNamespace(LOCK_SH=1, LOCK_EX=2, LOCK_NB=4,
                                                                         flock=lock_call))
    space["os"] = types.SimpleNamespace(name=platform, open=opener, close=closer, lseek=recorded("lseek"),
                                        O_RDWR=os.O_RDWR, O_CREAT=os.O_CREAT, SEEK_SET=0, SEEK_CUR=1, SEEK_END=2)
    path = tmp_path / "producer.lock"
    if reason is None:
        fd = space["lock_live_dir"](path)
        os.close(fd)
        assert closed == []
    else:
        with refused(space["LiveDirRefused"], reason):
            space["lock_live_dir"](path)
        fd = opened[0][0]
        assert closed == [fd]  # a refused descriptor is closed
    assert opened == [(fd, (os.O_RDWR | os.O_CREAT, 0o600))] and space["LOCK_BYTE"] == 2 ** 30
    shape = [("lseek", fd, 2 ** 30, 0), ("lock", fd, 2, 1)] if platform == "nt" else [("lock", fd, 2 | 4)]
    assert calls == (shape[:1] if failing == "lseek" else shape)  # on Windows the seek to LOCK_BYTE, then the lock


def clock_reads(space, *names):
    """A stand-in clock: cycle_name answers these names in turn, as the clock read at each cycle start would, so a
    second may repeat or go back."""
    answers = iter(names)
    space["cycle_name"] = lambda: next(answers)


CYCLE_NAMES = {  # the names already in the cycles dir (dirs), the clock's name -> the new dir's name
    "the_first_cycle": ([], "20261016T120000Z", "20261016T120000Z"),
    "a_second_after_every_name": (["20261015T235959Z", "20261016T115900Z"], "20261016T120000Z", "20261016T120000Z"),
    "the_same_second": (["20261016T120000Z"], "20261016T120000Z", "20261016T120001Z"),
    "a_clock_set_back_an_hour": (["20261016T120000Z", "20261016T120100Z", "20261016T120200Z"], "20261016T110300Z",
                                 "20261016T120201Z"),
    "into_the_next_minute": (["20261016T115959Z"], "20261016T115959Z", "20261016T120000Z"),
    "into_the_next_year": (["20261231T235959Z"], "20261231T120000Z", "20270101T000000Z"),
    "into_a_leap_day": (["20280228T235959Z"], "20280228T120000Z", "20280229T000000Z"),
    "names_not_read_as_a_second": (["20261399T000000Z", "99999999T999999Z", "20261016T130000Z.tmp", "notes",
                                    "20261016T110000Z"], "20261016T120000Z", "20261016T120000Z"),
    "a_name_not_read_as_a_second_after_a_repeated_one": (["99999999T999999Z", "20261016T120000Z"],
                                                         "20261016T120000Z", "20261016T120001Z"),
}


@pytest.mark.parametrize("case", CYCLE_NAMES)
def test_a_cycle_dir_is_named_by_its_start_second_unless_that_is_not_after_the_newest_name(tmp_path, case):
    # then by the second after the newest name that reads as a second; the dir is new and empty, its name one that
    # read_latest accepts (CYCLE_DIR), and every entry already there is left in place
    entries, clock, expected = CYCLE_NAMES[case]
    space, bridge = load()
    for name in entries:
        (tmp_path / name).mkdir()
    clock_reads(space, clock)
    made = space["new_cycle_dir"](tmp_path)
    assert made == tmp_path / expected and made.is_dir() and list(made.iterdir()) == []
    assert sorted(path.name for path in tmp_path.iterdir()) == sorted([*entries, expected])
    assert space["CYCLE_DIR"].fullmatch(made.name) and bridge.calls == []


def test_a_file_named_like_a_cycle_dir_takes_that_name_too(tmp_path):
    space, bridge = load()
    (tmp_path / "20261016T120000Z").write_bytes(b"")  # a file, not a dir
    clock_reads(space, "20261016T120000Z")
    assert space["new_cycle_dir"](tmp_path) == tmp_path / "20261016T120001Z"
    assert (tmp_path / "20261016T120000Z").read_bytes() == b"" and bridge.calls == []


def test_the_names_sort_in_the_order_made_and_prune_keeps_the_captures_of_the_last_dirs_made(tmp_path):
    # the clock: three cycles a minute apart, then set back an hour, then a restart within the same second; each new
    # dir gets a capture and prune runs after it with --keep 2, as the loop runs them
    space, bridge = load()
    clock = ["20261016T120000Z", "20261016T120100Z", "20261016T120200Z", "20261016T110300Z", "20261016T110400Z",
             "20261016T110400Z"]
    clock_reads(space, *clock)
    made = []
    for _ in clock:
        made.append(space["new_cycle_dir"](tmp_path))
        (made[-1] / "capture.json").write_bytes(b"{}")
        space["prune"](tmp_path, 2, set())
    assert [path.name for path in made] == ["20261016T120000Z", "20261016T120100Z", "20261016T120200Z",
                                            "20261016T120201Z", "20261016T120202Z", "20261016T120203Z"]
    assert sorted(made) == made
    assert [path.name for path in made if (path / "capture.json").exists()] == ["20261016T120202Z",
                                                                               "20261016T120203Z"]
    assert bridge.calls == []


CYCLE_FILES = ("capture.json", "state.json", "detail-manifest.txt", "context.json")


def cycles_made(cycles, count):
    """``count`` cycle dirs, each with its four files and its run dir's receipt; their names in order."""
    names = [f"20261016T1200{n:02d}Z" for n in range(count)]
    for name in names:
        (cycles / name / "run").mkdir(parents=True)
        for file_name in CYCLE_FILES + ("run/receipt.json",):
            (cycles / name / file_name).write_bytes(b"{}")
    return names


def present(cycles, name):
    return sorted(str(path.relative_to(cycles / name)).replace(os.sep, "/")
                  for path in (cycles / name).rglob("*") if path.is_file())


def test_prune_keeps_the_last_names_and_every_protected_cycle_and_removes_only_the_pruned_files(tmp_path):
    space, bridge = load()
    names = cycles_made(tmp_path, 6)
    removed, skipped = space["prune"](tmp_path, 2, {names[0], names[2]})
    assert space["PRUNED"] == ("capture.json", "state.json", "detail-manifest.txt") and (removed, skipped) == (6, 0)
    full = sorted(CYCLE_FILES + ("run/receipt.json",))
    for name in (names[0], names[2], names[4], names[5]):  # protected, and the last --keep names
        assert present(tmp_path, name) == full
    for name in (names[1], names[3]):  # the context and the run dir stay; never a dir
        assert present(tmp_path, name) == ["context.json", "run/receipt.json"]
    assert bridge.calls == []


def test_prune_touches_no_entry_that_is_not_a_cycle_dir_name(tmp_path):
    space, _ = load()
    names = cycles_made(tmp_path, 3)
    for other in ("notes", "20261016T120000Z.tmp", "20261399T000000Z.old"):
        (tmp_path / other).mkdir()
        (tmp_path / other / "capture.json").write_bytes(b"{}")
    assert space["prune"](tmp_path, 1, set()) == (6, 0)
    for other in ("notes", "20261016T120000Z.tmp", "20261399T000000Z.old"):
        assert (tmp_path / other / "capture.json").exists()
    assert present(tmp_path, names[2]) == sorted(CYCLE_FILES + ("run/receipt.json",))


def shim(module, **overrides):
    """A stand-in module answering ``overrides`` and delegating every other name to ``module``."""
    class Shim:
        def __getattr__(self, name):
            return overrides[name] if name in overrides else getattr(module, name)
    return Shim()


def test_prune_never_follows_or_removes_a_link_or_other_reparse_point(tmp_path):
    # stand-in lstat answers: a cycle dir that is a link, and a capture that is a Windows reparse point (a junction
    # or a mount point reads as a directory or file without the flag); every other path is the real lstat
    space, _ = load()
    names = cycles_made(tmp_path, 4)
    link_dir, reparse_file = tmp_path / names[0], tmp_path / names[1] / "capture.json"

    def lstat(path):
        if Path(path) == link_dir:
            return types.SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_file_attributes=0)
        if Path(path) == reparse_file:
            return types.SimpleNamespace(st_mode=stat.S_IFREG | 0o644,
                                         st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
        return os.lstat(path)

    space["os"] = shim(os, lstat=lstat)
    assert space["prune"](tmp_path, 1, set()) == (5, 2)  # names[1]: 2 files, names[2]: 3 files
    assert present(tmp_path, names[0]) == sorted(CYCLE_FILES + ("run/receipt.json",))  # not followed
    assert present(tmp_path, names[1]) == ["capture.json", "context.json", "run/receipt.json"]


def test_prune_that_cannot_list_the_cycles_refuses_the_cycle_with_its_fixed_reason(tmp_path):
    space, _ = load()
    with refused(space["CaptureRefused"], "prune_failed") as raised:
        space["prune"](tmp_path / "missing", 1, set())
    assert type(raised.value) is space["CycleRefused"]


def test_prune_that_cannot_remove_a_file_refuses_the_cycle_with_its_fixed_reason(tmp_path):
    class NoUnlink(type(tmp_path)):  # every path joined from it is one too (pathlib's with_segments)
        def unlink(self, missing_ok=False):
            raise PermissionError(13, "denied")

    space, _ = load()
    names = cycles_made(tmp_path, 2)
    with refused(space["CaptureRefused"], "prune_failed"):
        space["prune"](NoUnlink(tmp_path), 1, set())
    assert present(tmp_path, names[0]) == sorted(CYCLE_FILES + ("run/receipt.json",))


def test_atomic_write_replaces_through_the_core_sharing_retry_and_leaves_no_temporary_file(tmp_path):
    space, _ = load()
    seen = []

    def recorded(operation):
        seen.append(operation)
        return core._sharing_retry(operation)

    space["core"] = types.SimpleNamespace(_sharing_retry=recorded)
    path = tmp_path / "latest.json"
    path.write_bytes(b"old")
    space["atomic_write"](path, b"new")
    assert path.read_bytes() == b"new" and len(seen) == 1 and [p.name for p in tmp_path.iterdir()] == ["latest.json"]


def test_atomic_write_retries_a_sharing_refusal_of_the_replace_and_nothing_else(tmp_path, monkeypatch):
    # the core's own retry with its waits stubbed: a PermissionError (the Windows sharing window) is retried, any
    # other OSError reaches the caller at once; the own temporary file is removed when the replace did not happen
    monkeypatch.setattr(core, "_sleep", lambda seconds: None)
    for errors, expected in (([PermissionError(13, "shared")], None), ([OSError(28, "no space")], OSError)):
        space, _ = load()
        pending, tries = list(errors), []

        def replace(source, target):
            tries.append(source)
            if pending:
                raise pending.pop(0)
            os.replace(source, target)

        space["os"] = shim(os, replace=replace)
        path = tmp_path / "latest.json"
        path.write_bytes(b"old")
        if expected is None:
            space["atomic_write"](path, b"new")
            assert path.read_bytes() == b"new" and len(tries) == 2
        else:
            with pytest.raises(expected) as raised:
                space["atomic_write"](path, b"new")
            assert raised.value.errno == 28 and path.read_bytes() == b"old" and len(tries) == 1
        assert [p.name for p in tmp_path.iterdir()] == ["latest.json"]


def test_atomic_write_into_a_dir_that_is_gone_raises_and_writes_nothing(tmp_path):
    space, _ = load()
    with pytest.raises(OSError):
        space["atomic_write"](tmp_path / "missing" / "latest.json", b"new")
    assert list(tmp_path.iterdir()) == []


def test_a_cycle_file_is_created_once_and_never_replaced(tmp_path):
    space, _ = load()
    path = tmp_path / "capture.json"
    assert space["write_new"](path, b"first") == hashlib.sha256(b"first").hexdigest()
    with pytest.raises(FileExistsError):
        space["write_new"](path, b"second")
    assert path.read_bytes() == b"first"


def context_of(space, prs, observed=None, **keys):
    """A PR context of the producer's own form (as pr_context builds it) observed now."""
    observed = observed or space["utc_now"](seconds=True)
    value = {"schema": ws.CONTEXT_SCHEMA_V2, "authority": "none", "capture_sha256": HEX["capture"],
             "observed_utc": observed, "charter": {"loaded": False, "charter_blob": None, "classifier_blob": None},
             "prs": prs}
    value.update(keys)
    return value


OPEN_PR = {"state": "OPEN", "head": "a" * 40, "head_ref_name": "lane-a/case", "path_class": None,
           "commit_authors": ["lane-a"]}


def candidate_parts(space, rows=None):
    rows = rows_for(space, REFS[:3]) if rows is None else rows
    capture, manifest = space["capture_object"](rows)
    return capture, manifest, committed(space, rows, GOOD, 2)


def test_prewrite_reads_back_every_payload_as_its_consumer_does_and_writes_nothing(tmp_path):
    space, _ = load(live=tmp_path)
    capture, manifest, state = candidate_parts(space)
    for context in (None, context_of(space, {"7": OPEN_PR})):
        payloads = space["prewrite"](capture, manifest, context, state)
        names = {"capture.json", "detail-manifest.txt", "state.json"} | ({"context.json"} if context else set())
        assert set(payloads) == names and payloads["state.json"] == space["compact"](state)
        assert payloads["detail-manifest.txt"] == manifest
        assert shadow.read_capture(json.loads(payloads["capture.json"].decode("utf-8")))
    assert list(tmp_path.iterdir()) == []


def test_prewrite_refuses_a_guarded_word_in_any_payload_through_the_guard_before_any_read_back(tmp_path):
    space, _ = load(live=tmp_path)
    capture, manifest, state = candidate_parts(space)
    guarded = [dict(capture, events=capture["events"] + [{"note": MARKER}]),
               dict(state, cursor=state["cursor"] + MARKER)]
    for value in guarded[:1]:
        with pytest.raises(ws.WorkspaceError):
            space["prewrite"](value, manifest, None, state)
    with pytest.raises(ws.WorkspaceError):
        space["prewrite"](capture, manifest, None, guarded[1])
    with pytest.raises(ws.WorkspaceError):
        space["prewrite"](capture, manifest, context_of(space, {"7": dict(OPEN_PR, head_ref_name=MARKER)}), state)
    assert list(tmp_path.iterdir()) == []


PREWRITE_REFUSED = {  # a candidate part the producer would write -> candidate_invalid before any write
    "a_capture_of_another_schema": ("capture", lambda value: dict(value, schema="wd.other.v1")),
    "a_capture_without_events": ("capture", lambda value: {k: v for k, v in value.items() if k != "events"}),
    "a_state_whose_window_digest_differs": ("state", lambda value: dict(value, window_sha256="0" * 64)),
    "a_state_of_another_schema": ("state", lambda value: dict(value, schema="wd.other.v1")),
    "a_state_whose_cursor_names_another_log": ("state", lambda value: dict(value, cursor=OTHER_LOG)),
    "a_context_of_another_schema": ("context", lambda value: dict(value, schema="wd.other.v1")),
    "a_context_with_a_bad_pr_row": ("context", lambda value: dict(value, prs={"7": dict(OPEN_PR, state="DRAFT")})),
}


@pytest.mark.parametrize("case", [*PREWRITE_REFUSED, "a_context_the_workspace_reads_as_stale"])
def test_prewrite_refuses_a_payload_its_consumer_would_not_read_before_any_write(tmp_path, case):
    space, _ = load(live=tmp_path)
    capture, manifest, state = candidate_parts(space)
    parts = {"capture": capture, "state": state, "context": context_of(space, {"7": OPEN_PR})}
    if case in PREWRITE_REFUSED:
        part, change = PREWRITE_REFUSED[case]
        parts[part] = change(parts[part])
    else:  # read back at a later now (a stand-in parse_time): its own observed time cannot make it stale
        space["ws"] = shim(ws, parse_time=lambda value: datetime.datetime(2030, 1, 1, tzinfo=datetime.timezone.utc))
    with refused(space["CaptureRefused"], "candidate_invalid") as raised:
        space["prewrite"](parts["capture"], manifest, parts["context"], parts["state"])
    assert type(raised.value) is space["CycleRefused"] and list(tmp_path.iterdir()) == []


def test_the_previous_publication_is_handed_on_only_when_it_verifies_and_hashes_to_last_ok(tmp_path):
    live = tmp_path / ".codex-audit" / "live"
    space, _ = load(live=live)
    snapshot = {"schema": "stand-in", "n": 1}
    digest = hashlib.sha256(core.canonical_bytes(snapshot)).hexdigest()
    reads = []

    def read_snapshot(path, audit_root):
        reads.append((path, audit_root))
        return snapshot

    space["core"] = types.SimpleNamespace(read_snapshot=read_snapshot, canonical_bytes=core.canonical_bytes,
                                          ProjectionError=core.ProjectionError)
    last_ok = last_ok_of(CYCLE, snapshot_sha256=digest)
    expected = live / last_ok["publication_file"]
    assert space["previous_publication"](last_ok) == (expected, "verified")
    assert reads == [(expected, live.parent)]
    assert space["previous_publication"](dict(last_ok, snapshot_sha256=HEX["snapshot"])) == (
        None, "previous_identity_mismatch")
    assert space["previous_publication"](None) == (None, None) and len(reads) == 2


def test_a_previous_publication_the_core_refuses_or_that_is_gone_is_a_mismatch_never_a_synthetic_delta(tmp_path):
    live = tmp_path / ".codex-audit" / "live"
    live.mkdir(parents=True)
    space, _ = load(live=live)
    assert space["previous_publication"](LAST_OK) == (None, "previous_identity_mismatch")  # the real core: no file
    publication = live / LAST_OK["publication_file"]
    publication.parent.mkdir(parents=True)
    publication.write_bytes(b'{"schema": "wd.bridge-mind-publication.v1"}')  # not a publication the core reads
    assert space["previous_publication"](LAST_OK) == (None, "previous_identity_mismatch")


class FakeGh:
    """Answers gh pr list and gh pr view from fixed bytes (None: gh exits non-zero); records each call."""

    def __init__(self, listing, views=None):
        self.listing, self.views, self.calls = listing, views or {}, []

    def __call__(self, argv, **kwargs):
        assert argv[0] == "gh" and kwargs.get("timeout")
        self.calls.append(argv[1:])
        data = self.listing if argv[1:3] == ["pr", "list"] else self.views.get(argv[3])
        return answer(1, b"") if data is None else answer(0, data)

    def view_calls(self):
        return [call[2] for call in self.calls if call[:2] == ["pr", "view"]]


def pr_list(*rows, **changes):
    return json.dumps([dict({"number": number, "state": state, "headRefOid": head, "headRefName": branch}, **changes)
                       for number, state, head, branch in rows]).encode("utf-8")


def pr_view(head, *authors, commits=None):
    """A gh pr view answer of the kind the producer asks for, with a message and a body it must not keep."""
    listed = commits if commits is not None else [
        {"oid": "f" * 40, "messageHeadline": "headline", "messageBody": "body",
         "authors": [{"name": name, "email": "fixture-address-not-kept"} for name in authors]}]
    return json.dumps({"headRefOid": head, "commits": listed, "body": "pr body"}).encode("utf-8")


def gh_space(tmp_path, gh):
    space, _ = load(live=tmp_path)
    space["subprocess"] = types.SimpleNamespace(run=gh, TimeoutExpired=subprocess.TimeoutExpired)
    cache = tmp_path / "files-cache"
    cache.mkdir()
    return space, cache


def test_without_a_usable_gh_list_the_context_is_unknown_and_never_refuses_the_cycle(tmp_path):
    for listing, reason in ((None, "gh_list_unavailable"), (b"not json", "gh_list_unexpected"),
                            (pr_list((7, "OPEN", "a" * 40, "lane-a/x"), extra=1), "gh_list_unexpected")):
        gh = FakeGh(listing)
        space, cache = gh_space(tmp_path, gh)
        context, record = space["pr_context"](cache)
        assert context is None and (record["status"], record["reason"]) == ("UNKNOWN", reason)
        assert record["charter_loaded"] is False and gh.view_calls() == [] and list(cache.iterdir()) == []
        cache.rmdir()


def test_a_gh_list_that_reaches_its_limit_leaves_the_context_unknown_before_any_view(tmp_path):
    # PR1910 bot finding (truncated PR context): gh pr list stops at --limit, so a list that reaches it may leave out
    # a PR the bridge snapshot names (an old open PR, an older PR merged lately), and the workspace reads an absent
    # row as no suppression. Without the snapshot's PR numbers coverage is unknown, so the context is UNKNOWN
    # (which suppresses nothing; the end-to-end test below covers the board); a skipped row counts too.
    head = "a" * 40
    full = [(n, "OPEN", head, f"lane-a/x{n}") for n in (1, 2, 3)]
    views = {str(n): pr_view(head, "lane-a") for n in (1, 2, 3)}
    for rows in (full, full[:2] + [(4, "DRAFT", head, "lane-a/x4")]):
        gh = FakeGh(pr_list(*rows), views)
        space, cache = gh_space(tmp_path, gh)
        space["LIST_LIMIT"] = 3
        context, record = space["pr_context"](cache)
        assert context is None and (record["status"], record["reason"]) == ("UNKNOWN", "gh_list_truncated")
        listed = gh.calls[0]
        assert listed[:2] == ["pr", "list"] and listed[listed.index("--limit") + 1] == "3"
        assert gh.view_calls() == [] and list(cache.iterdir()) == []
        cache.rmdir()
    gh = FakeGh(pr_list(*full[:2]), views)
    space, cache = gh_space(tmp_path, gh)
    space["LIST_LIMIT"] = 3
    context, record = space["pr_context"](cache)
    assert record["status"] == "observed" and sorted(context["prs"]) == ["1", "2"] and gh.view_calls() == ["1", "2"]


class PrBridge(FakeBridge):
    """A fake bridge whose seed listing and details are the given events only."""

    def __init__(self, events):
        super().__init__()
        self.events = {event_id(event): event for event in events}

    def __call__(self, argv, **kwargs):
        options = argv[3].split("Read-AgentBridge.ps1' -Compact ", 1)[1].split(" ")
        self.calls.append(options)
        if options[2:3] == ["-EventId"]:
            report = {"schema": "wd.bridge.event-detail.v1", "ref": options[3], "event": self.events[options[3]]}
            return answer(0, json.dumps(report).encode("utf-8"))
        return answer(0, view(list(self.events), SEEDED))


PR_HEAD, MOVED_HEAD = "5" * 40, "6" * 40
PR_EVENT = {"ts_utc": "2026-10-16T00:00:50Z", "agent": "codex-lead-1",
            "agent_uuid": "00000000-0000-4000-8000-000000000003", "type": "decision",
            "status": "build_consensus_pass", "task_id": "codex-lead-1/task", "session_id": "s-lead",
            "run_id": "r-lead", "message": "pr fixture",
            "payload": {"pr": 5, "head": PR_HEAD, "required_checks_green": True}}


@pytest.mark.parametrize("state, head", [("MERGED", PR_HEAD), ("OPEN", MOVED_HEAD), (None, None)])
def test_a_snapshot_pr_a_full_gh_list_leaves_out_gets_its_own_row_or_the_context_stays_unknown(tmp_path, state, head):
    # End to end through the real core, shadow and workspace (Lead 09:05Z): PR 5 is in the bridge view, reported
    # green with one build vote, so the bridge-only board suggests its missing votes. The gh list reaches its limit
    # without PR 5. Its own gh pr view row enters the context, so a merged PR 5, or one whose head moved, gets no
    # suggestion. Without that row the context is UNKNOWN, which suppresses nothing: the board is then the
    # bridge-only one and still suggests PR 5 (the disclosed fallback, not a fail-closed result).
    live = tmp_path / ".codex-audit" / "live"
    for name in ("cycles", "files-cache"):
        (live / name).mkdir(parents=True)
    space, bridge = load(bridge=PrBridge([PR_EVENT]), live=live)
    row = {"number": 5, "state": state, "headRefOid": head, "headRefName": "codex-lead-1/task"}
    gh = FakeGh(pr_list(*[(n, "MERGED", "a" * 40, f"lane-a/x{n}") for n in (1, 2, 3)]),
                {} if state is None else {"5": json.dumps(row).encode("utf-8")})
    space["subprocess"] = types.SimpleNamespace(run=lambda argv, **kwargs: (gh if argv[0] == "gh" else bridge)(
        argv, **kwargs), TimeoutExpired=subprocess.TimeoutExpired)
    space["LIST_LIMIT"] = 3
    rec = {"cycle": 1, "started_utc": "s", "reader": space["READER"]}
    last_ok = space["candidate_cycle"](rec, None, {})
    snapshot = core.read_snapshot(live / last_ok["publication_file"], audit_root=live.parent)
    subjects = [item["subject"] for item in snapshot["attention"]]
    rules = [tension["rule"] for tension in snapshot["tensions"]]
    assert "5" in snapshot["prs"]
    if state is None:
        assert "pr:5" in subjects and "context_applied" not in rules
        assert (last_ok["context_file"], rec["context"]["reason"]) == (None, "gh_list_truncated")
        assert gh.view_calls() == ["5"]
        return
    assert "pr:5" not in subjects  # the omitted merged PR, or the one whose head moved, gets no suggestion
    context = json.loads((live / last_ok["context_file"]).read_bytes().decode("utf-8"))
    assert (context["prs"]["5"]["state"], context["prs"]["5"]["head"]) == (state, head)
    assert context["capture_sha256"] == hashlib.sha256(b"\n".join([gh.listing, gh.views["5"]])).hexdigest()
    assert rec["context"]["rows_added"] == 1 and gh.view_calls()[:1] == ["5"]
    if state == "MERGED":
        assert "PR5 MERGED" in json.dumps(snapshot["tensions"])
    else:
        assert "context_head_mismatch" in rules


@pytest.mark.parametrize("branch", ["feature/\u00e4", None, "codex-lead-1/task"])
def test_a_snapshot_pr_a_shorter_gh_list_leaves_out_keeps_the_context_unknown(tmp_path, branch):
    # Tools static witness (Lead 09:53Z): the list holds two rows, far below LIST_LIMIT, and PR 5 of the bridge
    # snapshot is MERGED on a Git-valid branch outside the task grammar, so pr_rows skips its row (the grammar stays
    # as it is). Coverage was checked only at the limit, so the context said observed without PR 5 and the board
    # applied it. A shorter list holds every PR, so a snapshot PR it leaves out (skipped, or not on GitHub: None)
    # keeps the context UNKNOWN before any view, which suppresses nothing (the bridge-only board, the disclosed
    # fallback); the control's ASCII branch keeps PR 5 in the observed context.
    live = tmp_path / ".codex-audit" / "live"
    for name in ("cycles", "files-cache"):
        (live / name).mkdir(parents=True)
    space, bridge = load(bridge=PrBridge([PR_EVENT]), live=live)
    rows = [(1, "MERGED", "a" * 40, "lane-a/x1")] + ([] if branch is None else [(5, "MERGED", PR_HEAD, branch)])
    gh = FakeGh(pr_list(*rows))
    space["subprocess"] = types.SimpleNamespace(run=lambda argv, **kwargs: (gh if argv[0] == "gh" else bridge)(
        argv, **kwargs), TimeoutExpired=subprocess.TimeoutExpired)
    assert len(rows) < space["LIST_LIMIT"]
    rec = {"cycle": 1, "started_utc": "s", "reader": space["READER"]}
    last_ok = space["candidate_cycle"](rec, None, {})
    snapshot = core.read_snapshot(live / last_ok["publication_file"], audit_root=live.parent)
    subjects = [item["subject"] for item in snapshot["attention"]]
    rules = [tension["rule"] for tension in snapshot["tensions"]]
    assert "5" in snapshot["prs"] and gh.view_calls() == []
    if branch != "codex-lead-1/task":
        outcome = (last_ok["context_file"], rec["context"]["status"], rec["context"].get("reason"))
        assert outcome == (None, "UNKNOWN", "needed_pr_missing")
        assert "pr:5" in subjects and "context_applied" not in rules
        return
    assert rec["context"]["status"] == "observed" and last_ok["context_file"] is not None
    assert "pr:5" not in subjects and "PR5 MERGED" in json.dumps(snapshot["tensions"])


def test_an_open_pr_view_is_kept_as_typed_metadata_only_and_every_class_stays_unknown(tmp_path):
    head, other = "a" * 40, "b" * 40
    gh = FakeGh(pr_list((7, "OPEN", head, "lane-a/x"), (8, "MERGED", other, "lane-b/y")),
                {"7": pr_view(head, "lane-a", "Not A Lane", "lane-c")})
    space, cache = gh_space(tmp_path, gh)
    context, record = space["pr_context"](cache)
    assert record["status"] == "observed" and record["charter_loaded"] is False and gh.view_calls() == ["7"]
    assert context["charter"] == {"loaded": False, "charter_blob": None, "classifier_blob": None}
    assert {number: row["path_class"] for number, row in context["prs"].items()} == {"7": None, "8": None}
    assert context["prs"]["7"]["commit_authors"] == ["lane-a", "lane-c"]
    assert context["prs"]["8"]["commit_authors"] is None
    cached = json.loads((cache / f"7-{head}.view.v1.json").read_bytes().decode("ascii"))
    assert cached == {"schema": space["VIEW_SCHEMA"], "number": 7, "head": head, "commit_authors": ["lane-a", "lane-c"]}
    assert set(cached) == space["VIEW_KEYS"] and [p.name for p in cache.iterdir()] == [f"7-{head}.view.v1.json"]
    assert ws.read_context(context, ws.parse_time(context["observed_utc"]))[1] is None


def test_a_cached_view_that_does_not_validate_leaves_the_whole_context_unknown_before_any_view_is_used(tmp_path):
    head, other = "a" * 40, "b" * 40
    gh = FakeGh(pr_list((7, "OPEN", head, "lane-a/x"), (9, "OPEN", other, "lane-b/y")), {"7": pr_view(head, "lane-a")})
    space, cache = gh_space(tmp_path, gh)
    (cache / f"9-{other}.view.v1.json").write_bytes(b'{"schema": "wd.bridge-mind-pr-view.v1"}')
    context, record = space["pr_context"](cache)
    assert context is None and (record["status"], record["reason"]) == ("UNKNOWN", "context_cache_invalid")
    assert gh.view_calls() == []  # the whole cached set is validated before any member is used or fetched


def test_a_valid_cached_view_is_reused_without_a_gh_view_call(tmp_path):
    head = "a" * 40
    gh = FakeGh(pr_list((7, "OPEN", head, "lane-a/x")))
    space, cache = gh_space(tmp_path, gh)
    (cache / f"7-{head}.view.v1.json").write_bytes(space["compact"](space["view_object"](7, head, ["lane-a"])))
    context, record = space["pr_context"](cache)
    assert gh.view_calls() == [] and context["prs"]["7"]["commit_authors"] == ["lane-a"]
    assert record["views_fetched"] == 0 and record["views_unknown"] == 0


def test_views_past_the_cycle_cap_or_unusable_ones_leave_those_authors_unknown(tmp_path):
    heads = {n: str(n) * 40 for n in (1, 2, 3)}
    gh = FakeGh(pr_list(*[(n, "OPEN", heads[n], f"lane-a/x{n}") for n in heads]),
                {"1": pr_view(heads[1], commits=[{"authors": []}] * 100), "2": pr_view("e" * 40, "lane-a")})
    space, cache = gh_space(tmp_path, gh)
    space["VIEW_CAP"] = 2
    context, record = space["pr_context"](cache)
    assert gh.view_calls() == ["1", "2"] and record["views_fetched"] == 2 and record["views_unknown"] == 2
    assert [context["prs"][n]["commit_authors"] for n in ("1", "2", "3")] == [None, None, None]
    assert [p.name for p in cache.iterdir()] == [f"1-{heads[1]}.view.v1.json"]  # 100 commits: authors unknown


def test_a_context_the_workspace_refuses_is_unknown_and_named(tmp_path):
    head = "a" * 40
    gh = FakeGh(pr_list((7, "OPEN", head, "lane-a/x")), {"7": pr_view(head, "lane-a")})
    space, cache = gh_space(tmp_path, gh)
    space["ws"] = shim(ws, read_context=lambda value, now: (None, "context_stale"))
    context, record = space["pr_context"](cache)
    assert context is None and (record["status"], record["reason"]) == ("UNKNOWN", "context_stale")


def rec_of(cycle=1):
    return {"cycle": cycle, "started_utc": "2026-10-16T00:00:00.000000Z", "reader": "r", "cycle_dir": CYCLE,
            "status": "refused", "reason": "compact_reader_failed", "finished_utc": "2026-10-16T00:00:01.000000Z"}


def log_lines(live):
    return [json.loads(line) for line in (live / "live-log.jsonl").read_text(encoding="ascii").splitlines()]


def test_a_refusal_is_appended_to_the_log_and_nothing_else_is_written(tmp_path):
    space, _ = load(live=tmp_path)
    space["record_refusal"](rec_of())
    space["record_refusal"](rec_of(2))
    assert log_lines(tmp_path) == [rec_of(), rec_of(2)] and [p.name for p in tmp_path.iterdir()] == ["live-log.jsonl"]


def test_when_the_log_cannot_be_written_the_refusal_falls_back_to_one_replaced_record(tmp_path):
    space, _ = load(live=tmp_path)
    (tmp_path / "live-log.jsonl").mkdir()  # a directory in its place: the append fails
    for cycle in (1, 2):
        space["record_refusal"](rec_of(cycle))
        fallback = json.loads((tmp_path / "last-refusal.json").read_bytes().decode("ascii"))
        assert fallback == {"schema": space["REFUSAL_SCHEMA"], "authority": "none", "status": "refused",
                            "cycle": cycle, "cycle_dir": CYCLE, "reason": "compact_reader_failed",
                            "finished_utc": "2026-10-16T00:00:01.000000Z"}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["last-refusal.json", "live-log.jsonl"]


def test_when_neither_can_be_written_the_producer_stops_with_one_fixed_line(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(core, "_sleep", lambda seconds: None)
    space, _ = load(live=tmp_path)
    for name in ("live-log.jsonl", "last-refusal.json"):
        (tmp_path / name).mkdir()  # a directory in each place: neither the append nor the replace can happen
    with pytest.raises(SystemExit) as stopped:
        space["record_refusal"](rec_of())
    assert stopped.value.code == space["STOP_EXIT"] == 3
    assert capsys.readouterr().err == "mind_capture_producer STOP: refusal_unrecordable\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["last-refusal.json", "live-log.jsonl"]  # no temporary file


@pytest.mark.parametrize("cut", [False, True])
def test_a_refusal_after_an_append_cut_part_way_is_still_one_whole_parseable_line(tmp_path, cut):
    # Root build audit (Lead 11:33Z, B2/B3 observability): a journal append that wrote 17 bytes and then failed left a
    # cut line, and the next refusal was appended to it: the only line did not parse, and record_refusal returned
    # without its fallback. The real journal and record_refusal run; only the cut write is synthetic (a stream that
    # writes the first 17 bytes, then raises the no-space error). cut=False is the positive control.
    space, _ = load(live=tmp_path)
    log = tmp_path / "live-log.jsonl"
    if cut:
        real_open = open

        class CutStream:
            def __init__(self, handle):
                self.handle = handle

            def __getattr__(self, name):
                return getattr(self.handle, name)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self.handle.close()

            def write(self, data):
                self.handle.write(data[:17])
                self.handle.flush()
                raise no_space(space)

        prepared = dict(rec_of(), status="prepared")
        space["open"] = lambda path, mode: CutStream(real_open(path, mode))  # only the producer's own open
        with pytest.raises(OSError):
            space["journal"](prepared)
        del space["open"]
        assert log.read_bytes() == (json.dumps(prepared, sort_keys=True) + "\n").encode("ascii")[:17]
    space["record_refusal"](rec_of(2))
    lines = log.read_bytes().split(b"\n")
    assert (len(lines), lines[-1], json.loads(lines[-2])) == (2 + cut, b"", rec_of(2))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["live-log.jsonl"]  # the log took it: no fallback needed


def test_a_refusal_record_the_guard_refuses_is_cut_to_its_fixed_fields(tmp_path, capsys):
    space, _ = load(live=tmp_path)
    rec = {"cycle": 2, "started_utc": "s", "reader": "r", "cycle_dir": CYCLE, "read": {"note": MARKER}}
    assert space["refused"](rec, "compact_reader_failed", time.perf_counter(), LAST_OK) is LAST_OK
    [logged] = log_lines(tmp_path)
    assert set(logged) == {"cycle", "started_utc", "cycle_dir", "cycle_ms", "finished_utc", "status", "reason"}
    assert (logged["status"], logged["reason"]) == ("refused", "output_guard")
    text = (tmp_path / "live-log.jsonl").read_text(encoding="ascii") + capsys.readouterr().out
    assert MARKER not in text


class Interrupted(BaseException):
    """A stand-in for the process ending between two writes (never caught by the producer)."""


def cycle_space(tmp_path, last_ok, fail=None):
    """The producer's one_cycle with its candidate stubbed (it returns the last_ok of a completed candidate dir) and
    its prune, log and pointer writes recorded in order. ``fail`` is (the step keys that raise, a callable making
    the error from the loaded space, so that a refusal is the producer's own class); each raises at every try."""
    space, _ = load(live=tmp_path)
    (tmp_path / "cycles").mkdir()
    order, candidate = [], last_ok_of("20261016T000005Z", generation=(last_ok or {}).get("generation", 0) + 1)
    journal, atomic_write = space["journal"], space["atomic_write"]
    steps, make = fail or ((), None)

    def step(name, key=None):
        order.append(name)
        if (key or name) in steps:
            raise make(space)

    def candidate_cycle(rec, kept, seeded):
        step("candidate")
        rec["cycle_dir"] = candidate["cycle_dir"]
        return candidate

    def prune(cycles_dir, keep, protected):
        step(("prune", tuple(sorted(protected))), "prune")
        return 0, 0

    def recorded_journal(rec):
        step(("journal", rec["status"]), "journal_" + rec["status"])
        journal(rec)

    def recorded_atomic_write(path, data):
        step(("replace", path.name), "replace_" + path.name)
        atomic_write(path, data)

    space.update(candidate_cycle=candidate_cycle, prune=prune, journal=recorded_journal,
                 atomic_write=recorded_atomic_write)
    return space, order, candidate


def latest_of(tmp_path):
    path = tmp_path / "latest.json"
    return path.read_bytes() if path.exists() else None


def test_a_cycle_commits_with_one_prepared_record_and_the_pointer_last(tmp_path, capsys):
    for last_ok in (None, LAST_OK):
        space, order, candidate = cycle_space(tmp_path, last_ok)
        assert space["one_cycle"](1, last_ok, {}) == candidate
        protected = tuple(sorted({candidate["cycle_dir"]} | ({last_ok["cycle_dir"]} if last_ok else set())))
        assert order == ["candidate", ("prune", protected), ("journal", "prepared"), ("replace", "latest.json")]
        assert space["read_latest"](tmp_path / "latest.json") == candidate
        [logged] = log_lines(tmp_path)
        assert logged["status"] == "prepared" and logged["candidate"] == candidate and "ok" not in logged
        assert json.loads(capsys.readouterr().out.splitlines()[-1])["status"] == "committed"
        for name in ("latest.json", "live-log.jsonl"):
            (tmp_path / name).unlink()
        (tmp_path / "cycles").rmdir()


def no_space(space):
    return OSError(28, "no space")


CYCLE_REFUSED = {  # the steps that fail, the error, the refusal reason, the log statuses after it (None: no log)
    "the_candidate": (("candidate",), lambda space: shadow.ShadowRefused("shadow_input_invalid"),
                      "shadow_input_invalid", ["refused"]),
    "a_reader": (("candidate",), lambda space: space["CycleRefused"]("compact_reader_timed_out"),
                 "compact_reader_timed_out", ["refused"]),
    "retention": (("prune",), lambda space: space["CycleRefused"]("prune_failed"), "prune_failed", ["refused"]),
    "another_error": (("candidate",), lambda space: ValueError("content " + MARKER), "ValueError", ["refused"]),
    "the_guard": (("candidate",), lambda space: ws.WorkspaceError("a guarded word"), "output_guard", ["refused"]),
    "the_prepared_record": (("journal_prepared",), no_space, "log_unwritable", ["refused"]),
    "the_log": (("journal_prepared", "journal_refused"), no_space, "log_unwritable", None),
    "the_pointer": (("replace_latest.json",), no_space, "latest_unwritable", ["prepared", "refused"]),
}


@pytest.mark.parametrize("case", CYCLE_REFUSED)
def test_a_refused_cycle_keeps_latest_json_and_last_ok_and_records_its_reason(tmp_path, capsys, case):
    steps, make, reason, statuses = CYCLE_REFUSED[case]
    (tmp_path / "latest.json").write_bytes(b"the committed pointer")
    space, order, _ = cycle_space(tmp_path, LAST_OK, (steps, make))
    assert space["one_cycle"](3, LAST_OK, {}) is LAST_OK
    assert latest_of(tmp_path) == b"the committed pointer"
    if statuses is None:  # the log refused both records: the bounded fallback holds the refusal
        assert not (tmp_path / "live-log.jsonl").exists()
        fallback = json.loads((tmp_path / "last-refusal.json").read_bytes().decode("ascii"))
        assert (fallback["status"], fallback["reason"], fallback["cycle"]) == ("refused", reason, 3)
        assert order[-3:] == [("journal", "prepared"), ("journal", "refused"), ("replace", "last-refusal.json")]
    else:
        records = log_lines(tmp_path)
        assert [record["status"] for record in records] == statuses and records[-1]["reason"] == reason
        assert not (tmp_path / "last-refusal.json").exists()
    assert ("replace", "latest.json") not in order or case == "the_pointer"
    text = "".join(path.read_text(encoding="ascii") for path in tmp_path.iterdir() if path.is_file()
                   and path.name != "latest.json") + capsys.readouterr().out
    assert MARKER not in text and "content" not in text


def test_a_cycle_whose_refusal_cannot_be_recorded_stops_the_producer_with_one_fixed_line(tmp_path, capsys):
    (tmp_path / "latest.json").write_bytes(b"the committed pointer")
    space, order, _ = cycle_space(tmp_path, LAST_OK, (("journal_prepared", "journal_refused",
                                                       "replace_last-refusal.json"), no_space))
    with pytest.raises(SystemExit) as stopped:
        space["one_cycle"](5, LAST_OK, {})
    assert stopped.value.code == 3 and latest_of(tmp_path) == b"the committed pointer"
    assert capsys.readouterr().err == "mind_capture_producer STOP: refusal_unrecordable\n"
    assert order[-3:] == [("journal", "prepared"), ("journal", "refused"), ("replace", "last-refusal.json")]


def test_a_process_ended_between_the_log_and_the_pointer_leaves_no_success_claim(tmp_path):
    (tmp_path / "latest.json").write_bytes(b"the committed pointer")
    space, order, _ = cycle_space(tmp_path, LAST_OK, (("replace_latest.json",), lambda space: Interrupted()))
    with pytest.raises(Interrupted):
        space["one_cycle"](4, LAST_OK, {})
    assert order[-2:] == [("journal", "prepared"), ("replace", "latest.json")]
    assert latest_of(tmp_path) == b"the committed pointer"
    assert [record["status"] for record in log_lines(tmp_path)] == ["prepared"]


def candidate_space(tmp_path, after=()):
    """The producer's candidate_cycle over a live dir inside a .codex-audit root, the fake bridge and the real core,
    shadow and workspace; the PR context stubbed as UNKNOWN (no gh call)."""
    live = tmp_path / ".codex-audit" / "live"
    for name in ("cycles", "files-cache"):
        (live / name).mkdir(parents=True)
    space, bridge = load(after=after, live=live)
    space["pr_context"] = lambda cache_dir, needed=None: (None, {"charter_loaded": False, "status": "UNKNOWN",
                                                                 "reason": "gh_list_unavailable", "ms": 0})
    return space, bridge, live


def file_sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_a_candidate_cycle_builds_one_immutable_dir_whose_last_ok_names_and_hashes_its_files(tmp_path):
    space, bridge, live = candidate_space(tmp_path, after=REFS[5:6])
    rec = {"cycle": 1, "started_utc": "s", "reader": space["READER"]}
    first = space["candidate_cycle"](rec, None, {})
    assert space["committed_last_ok"](first) and first["generation"] == 1 and rec["read"]["mode"] == "seed"
    assert [path.name for path in (live / "cycles").iterdir()] == [first["cycle_dir"]] == [rec["cycle_dir"]]
    assert file_sha(live / first["capture_file"]) == first["capture_sha256"]
    assert file_sha(live / first["state_file"]) == first["state_sha256"]
    assert (first["context_file"], first["context_sha256"], rec["previous"]) == (None, None, None)
    state = space["read_state"](live / first["state_file"], first["state_sha256"], 1)
    assert [row["ref"] for row in state["window"]] == REFS[-WINDOW:] and state["cursor"] == SEEDED
    snapshot = core.read_snapshot(live / first["publication_file"], audit_root=live.parent)
    assert hashlib.sha256(core.canonical_bytes(snapshot)).hexdigest() == first["snapshot_sha256"]
    assert file_sha(live / first["board_file"]) == first["board_text_sha256"]
    kept = {path: file_sha(path) for path in (live / "cycles").rglob("*") if path.is_file()}

    rec = {"cycle": 2, "started_utc": "s", "reader": space["READER"]}
    second = space["candidate_cycle"](rec, first, {})  # the next cycle continues from the committed state
    assert second["generation"] == 2 and second["cycle_dir"] > first["cycle_dir"] and rec["read"]["mode"] == "delta"
    assert bridge.calls[1 + WINDOW] == ["-AfterEvent", SEEDED] and rec["previous"] == "verified"
    again = space["read_state"](live / second["state_file"], second["state_sha256"], 2)
    assert again["window"] == state["window"] and again["cursor"] == AFTER
    assert {path: file_sha(path) for path in kept} == kept  # the first cycle's files are never rewritten


def test_a_candidate_cycle_from_a_state_that_does_not_verify_refuses_before_any_write(tmp_path):
    space, bridge, live = candidate_space(tmp_path)
    rec = {"cycle": 2, "started_utc": "s", "reader": space["READER"]}
    with refused(space["CaptureRefused"], "committed_state_unverified"):
        space["candidate_cycle"](rec, LAST_OK, {})  # LAST_OK names a state file that is not there
    assert list((live / "cycles").iterdir()) == [] and bridge.calls == [] and "cycle_dir" not in rec


def test_a_candidate_whose_shadow_run_read_other_bytes_is_refused(tmp_path):
    space, bridge, live = candidate_space(tmp_path)
    real = shadow.run

    def run(capture_path, audit_root, out_dir, **options):
        receipt = real(capture_path, audit_root, out_dir, **options)
        return dict(receipt, capture=dict(receipt["capture"], sha256="0" * 64))

    space["shadow"] = shim(shadow, run=run)
    with refused(space["CaptureRefused"], "candidate_changed"):
        space["candidate_cycle"]({"cycle": 1, "started_utc": "s", "reader": space["READER"]}, None, {})
