"""Offline cases for the capture producer's persisted window, its reseed (``advance``), its seed file read, its
start read of its own latest.json (``read_latest``), which must read back the forms the producer itself writes, its
live dir lock (``lock_live_dir``: this platform's real lock, and each platform's calls with stand-ins) and its cycle
dir names (``new_cycle_dir``, with a stand-in ``cycle_name`` whose second repeats or goes back, and ``prune`` over
the dirs it makes).

The producer parses its arguments and runs its cycles at import, so these cases execute its own functions, classes
and reader constants, taken from the module file, with a fake bridge as ``subprocess``: each would-be reader start
is recorded and answered from fixed rows (or raises, as subprocess.run raises a timeout or a failed start), and
nothing starts. ``event_id`` is the compact view's real canonical ref.
``BoundedBridge`` answers like the compact tool's bounded snapshot: an -EventId read finds a ref only among the
last lines it reads (RCO1's L1 shape, a fake-reader reproduction).
Each refusal is compared by its exception's arguments: exactly the fixed reason, no added text (``refused``).
"""

import ast
import concurrent.futures
import contextlib
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

from tools.bridge_compact_view import event_id

PRODUCER = Path(__file__).resolve().parent / "mind_capture_producer.py"
# the producer's reader constants; READER_OPTION is the per-token check the slot grammar replaced, listed so that
# these cases also run over a producer from before that change
CONSTANTS = ("READER", "READER_OPTION", "DETAIL_TAIL", "TAIL_CAP", "SEED_MARGIN", "COUNT", "EVENT_REF", "CURSOR",
             "REPORT_TASK", "LATEST_SCHEMA", "CYCLE_DIR", "LOCK_BYTE", "CYCLE_TIME")
WINDOW = 4
EVENTS = [{"agent": "lane-a", "type": "message", "task_id": "lane-a/state-case",
           "ts_utc": f"2026-10-16T00:00:{n:02d}Z", "message": f"row {n} äö", "weight": n / 10}
          for n in range(6)]  # non-ASCII text and floats: the persisted rows must survive their JSON round trip
REFS = [event_id(event) for event in EVENTS]
BY_REF = dict(zip(REFS, EVENTS))
GOOD, STALE = "bc1.Z29vZA", "bc1.c3RhbGU"  # a cursor the fake compact tool knows, and one it refuses
AFTER, SEEDED = "bc1.YWZ0ZXI", "bc1.c2VlZA"  # the cursors it hands out after a delta and a seed read (known too)
CORRUPT = ("window_not_utf8", "window_line_not_json", "window_line_nested_too_deep", "window_line_a_list",
           "window_line_the_key_names", "row_with_an_extra_key", "row_with_a_foreign_source", "row_event_altered",
           "row_digest_altered", "row_event_not_an_object", "row_event_with_nan", "window_with_a_repeated_row",
           "cursor_not_ascii")
DEEP = b"[" * 100000 + b"]" * 100000  # nested beyond the JSON parser's depth: a RecursionError, not a ValueError


def answer(code, out):
    return types.SimpleNamespace(returncode=code, stdout=out)


def view(refs, cursor):
    return json.dumps({"schema": "wd.bridge.compact-view.v1", "cursor": cursor,
                       "events": [{"ref": ref} for ref in refs], "stats": {"listed": len(refs)}}).encode("utf-8")


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
    """A log of rows read like the compact tool's bounded snapshot; starts no process. A position cursor is
    ``bc1.p<rows before it>``; -AfterEvent lists the rows after it, -ContinuityTail T the last T rows, and
    -ContinuityTail T -EventId ref answers only a ref among the last T rows. A refused read exits non-zero with no
    stdout, as the tool does; a ref in ``refused`` is refused in every read, and a ref in ``forged`` is answered
    with another row."""

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
        end = f"bc1.p{len(self.refs)}"
        if options[0] == "-AfterEvent":
            return answer(0, view(self.refs[int(options[1][len("bc1.p"):]):], end))
        last = self.refs[-int(options[1]):]
        if options[2:3] != ["-EventId"]:
            return answer(0, view(last, end))
        ref = options[3]
        if ref not in last or ref in self.refused:
            return answer(2, b"")
        event = self.events[self.refs.index(ref) - 1 if ref in self.forged else self.refs.index(ref)]
        report = {"schema": "wd.bridge.event-detail.v1", "ref": ref, "event": event}
        return answer(0, json.dumps(report).encode("utf-8"))

    def split(self, listing):
        """(delta detail reads, reseed detail reads) around the reseed's listing call."""
        at = self.calls.index(listing)
        return self.calls[1:at], self.calls[at + 1:]


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


def load(after=(), bridge=None):
    tree = ast.parse(PRODUCER.read_text(encoding="utf-8"))
    nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))
             or (isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) in CONSTANTS)]
    bridge = FakeBridge(after) if bridge is None else bridge
    space = {"re": re, "json": json, "os": os, "hashlib": hashlib, "concurrent": concurrent, "time": time,
             "dt": datetime, "Path": Path, "bin_dir": Path("bin"),
             "subprocess": types.SimpleNamespace(run=bridge, TimeoutExpired=subprocess.TimeoutExpired),
             "args": types.SimpleNamespace(window=WINDOW, workers=1, seed_from=None), "event_id": event_id}
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
    """Window rows as the producer writes them after a detail read."""
    return [{"ref": ref, "row_sha256": space["row_digest"](BY_REF[ref]), "source": "detail", "event": BY_REF[ref]}
            for ref in refs]


def lines(rows):
    return b"".join((json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8") for row in rows)


def put(state_dir, window, cursor):
    (state_dir / "window.jsonl").write_bytes(window)
    (state_dir / "cursor.txt").write_bytes(cursor)


def verified(space, rows):
    return all(row["ref"] == event_id(row["event"]) and row["event"] == BY_REF[row["ref"]]
               and row["row_sha256"] == space["row_digest"](row["event"]) for row in rows)


def logged_rows(space, events):
    """Window rows of a bounded log as the producer writes them after a detail read."""
    return [{"ref": event_id(event), "row_sha256": space["row_digest"](event), "source": "detail", "event": event}
            for event in events]


def from_the_log(space, bridge, rows):
    return all(row["ref"] == event_id(row["event"]) and row["event"] == bridge.events[bridge.refs.index(row["ref"])]
               and row["row_sha256"] == space["row_digest"](row["event"]) for row in rows)


def state_bytes(state_dir):
    return [(state_dir / name).read_bytes() for name in ("window.jsonl", "cursor.txt")]


def test_without_a_state_the_first_cycle_seeds_and_reads_every_listed_row(tmp_path):
    space, bridge = load()
    rows, record = space["advance"](tmp_path)
    assert bridge.calls[0] == ["-ContinuityTail", str(WINDOW)] and len(bridge.calls) == 1 + WINDOW
    assert bridge.detail_refs() == REFS[-WINDOW:] and record["mode"] == "seed" and record["detail_calls"] == WINDOW
    assert [row["ref"] for row in rows] == REFS[-WINDOW:] and verified(space, rows)
    assert (tmp_path / "cursor.txt").read_text(encoding="ascii") == SEEDED and record["reseed_after"] is None


def test_a_known_cursor_reads_only_the_rows_after_it(tmp_path):
    space, bridge = load(after=REFS[3:5])
    put(tmp_path, lines(rows_for(space, REFS[:3])), (GOOD + "\n").encode("ascii"))  # a line end is tolerated
    rows, record = space["advance"](tmp_path)
    tail = str(space["DETAIL_TAIL"])
    assert bridge.calls == [["-AfterEvent", GOOD], ["-ContinuityTail", tail, "-EventId", REFS[3]],
                            ["-ContinuityTail", tail, "-EventId", REFS[4]]]
    assert record["mode"] == "delta" and record["new_rows"] == 2 and record["detail_calls"] == 2
    assert [row["ref"] for row in rows] == REFS[1:5] and verified(space, rows) and record["reseed_after"] is None
    assert (tmp_path / "cursor.txt").read_text(encoding="ascii") == AFTER


def test_a_cursor_outside_the_reader_shapes_starts_no_reader_for_it_and_reseeds_from_one_list_read(tmp_path):
    # the refused -AfterEvent read starts no process; the reseed that follows is one -ContinuityTail list read
    space, bridge = load()
    put(tmp_path, lines(rows_for(space, REFS[:2])), b"x y")
    rows, record = space["advance"](tmp_path)
    assert bridge.calls[0] == ["-ContinuityTail", str(WINDOW)]
    assert [call for call in bridge.calls if call[2:3] != ["-EventId"]] == [["-ContinuityTail", str(WINDOW)]]
    assert record["mode"] == "seed" and [row["ref"] for row in rows] == REFS[-WINDOW:] and verified(space, rows)
    assert (tmp_path / "cursor.txt").read_text(encoding="ascii") == SEEDED
    assert record["reseed_after"] == {"read": "listing", "reason": "reader_option_invalid"}


def test_a_cursor_the_compact_tool_refuses_reseeds(tmp_path):
    space, bridge = load()
    put(tmp_path, lines(rows_for(space, REFS[:2])), STALE.encode("ascii"))
    rows, record = space["advance"](tmp_path)
    assert bridge.calls[:2] == [["-AfterEvent", STALE], ["-ContinuityTail", str(WINDOW)]]
    assert record["mode"] == "seed" and [row["ref"] for row in rows] == REFS[-WINDOW:] and verified(space, rows)
    assert record["reseed_after"] == {"read": "listing", "reason": "compact_reader_failed"}


def test_a_delta_listing_without_the_view_schema_reseeds_and_names_it(tmp_path):
    space, bridge = load()
    bridge.listing = json.dumps({"schema": "other", "cursor": AFTER, "events": []}).encode("utf-8")
    put(tmp_path, lines(rows_for(space, REFS[:2])), GOOD.encode("ascii"))
    rows, record = space["advance"](tmp_path)
    assert bridge.calls[:2] == [["-AfterEvent", GOOD], ["-ContinuityTail", str(WINDOW)]]
    assert record["mode"] == "seed" and [row["ref"] for row in rows] == REFS[-WINDOW:] and verified(space, rows)
    assert record["reseed_after"] == {"read": "listing", "reason": "compact_view_shape"}


def test_a_listed_ref_outside_the_reader_shapes_refuses_the_detail_read_before_a_start_and_reseeds(tmp_path):
    space, bridge = load()
    bridge.listing = view(["not-a-ref"], AFTER)
    put(tmp_path, lines(rows_for(space, REFS[:2])), GOOD.encode("ascii"))
    rows, record = space["advance"](tmp_path)
    assert bridge.calls[:2] == [["-AfterEvent", GOOD], ["-ContinuityTail", str(WINDOW)]]
    assert "not-a-ref" not in bridge.detail_refs() and bridge.detail_refs() == REFS[-WINDOW:]
    assert record["mode"] == "seed" and record["reseed_after"] == {"read": "detail", "reason": "reader_option_invalid"}
    assert [row["ref"] for row in rows] == REFS[-WINDOW:] and verified(space, rows)


def test_a_delta_whose_detail_read_falls_outside_the_bounded_tail_reseeds_and_the_next_cycle_continues(tmp_path):
    # RCO1's L1 with the producer's own DETAIL_TAIL: -EventId reads only the last DETAIL_TAIL rows, so the oldest
    # ref listed after a cursor that has fallen further behind is refused; the refused delta reseeds instead of
    # refusing every cycle at the same cursor, and the read record names the refused read
    bridge = BoundedBridge([])
    space, _ = load(bridge=bridge)
    tail = space["DETAIL_TAIL"]
    bridge.extend(logged(0, tail + 10))
    put(tmp_path, lines(logged_rows(space, bridge.events[:WINDOW])), f"bc1.p{WINDOW}".encode("ascii"))
    rows, record = space["advance"](tmp_path)
    assert bridge.calls[0] == ["-AfterEvent", f"bc1.p{WINDOW}"]
    delta, reseed = bridge.split(["-ContinuityTail", str(WINDOW)])
    assert delta and [call[:3] for call in delta] == [["-ContinuityTail", str(tail), "-EventId"]] * len(delta)
    assert [call[3] for call in delta] == bridge.refs[WINDOW:WINDOW + len(delta)]  # the oldest first, refused
    assert reseed == [["-ContinuityTail", str(WINDOW + 500), "-EventId", ref] for ref in bridge.refs[-WINDOW:]]
    assert record["mode"] == "seed" and record["reseed_after"] == {"read": "detail", "reason": "compact_reader_failed"}
    assert [row["ref"] for row in rows] == bridge.refs[-WINDOW:] and from_the_log(space, bridge, rows)
    assert (tmp_path / "cursor.txt").read_text(encoding="ascii") == f"bc1.p{tail + 10}"
    bridge.extend(logged(tail + 10, tail + 12))
    bridge.calls.clear()
    rows, record = space["advance"](tmp_path)
    assert record["mode"] == "delta" and record["reseed_after"] is None and record["new_rows"] == 2
    assert bridge.calls == [["-AfterEvent", f"bc1.p{tail + 10}"]] + [
        ["-ContinuityTail", str(tail), "-EventId", ref] for ref in bridge.refs[-2:]]
    assert [row["ref"] for row in rows] == bridge.refs[-WINDOW:] and from_the_log(space, bridge, rows)


def test_a_delta_detail_report_of_another_row_reseeds_and_names_the_mismatch(tmp_path):
    bridge = BoundedBridge(logged(0, 2 * WINDOW + 1))
    space, _ = load(bridge=bridge)
    bridge.forged.add(bridge.refs[WINDOW])  # every read of it answers the row before it
    put(tmp_path, lines(logged_rows(space, bridge.events[:WINDOW])), f"bc1.p{WINDOW}".encode("ascii"))
    rows, record = space["advance"](tmp_path)
    assert record["mode"] == "seed" and record["reseed_after"] == {"read": "detail", "reason": "detail_ref_mismatch"}
    assert [row["ref"] for row in rows] == bridge.refs[-WINDOW:] and from_the_log(space, bridge, rows)


def test_after_a_refused_detail_read_the_reseed_reuses_the_verified_rows_it_lists_again(tmp_path):
    bridge = BoundedBridge(logged(0, WINDOW + 3))
    space, _ = load(bridge=bridge)
    space["DETAIL_TAIL"] = 2  # shorter than the delta: its oldest fresh ref is refused
    put(tmp_path, lines(logged_rows(space, bridge.events[:WINDOW])), f"bc1.p{WINDOW}".encode("ascii"))
    rows, record = space["advance"](tmp_path)
    delta, reseed = bridge.split(["-ContinuityTail", str(WINDOW)])
    assert delta[0] == ["-ContinuityTail", "2", "-EventId", bridge.refs[WINDOW]]
    assert reseed == [["-ContinuityTail", str(WINDOW + 500), "-EventId", ref] for ref in bridge.refs[WINDOW:]]
    assert record["reseed_after"] == {"read": "detail", "reason": "compact_reader_failed"}
    assert record["new_rows"] == WINDOW and record["detail_calls"] == 3
    assert [row["source"] for row in rows] == ["seed", "detail", "detail", "detail"]
    assert [row["ref"] for row in rows] == bridge.refs[-WINDOW:] and from_the_log(space, bridge, rows)


def test_an_event_refused_in_every_read_refuses_each_cycle_until_the_reseed_no_longer_lists_it(tmp_path):
    # RCO1's L2: a refused reseed refuses the cycle and writes nothing; once the event is no longer among the last
    # --window rows the reseed lists, the reseed succeeds (a stall or a recorded gap instead is Lead's decision)
    bridge = BoundedBridge(logged(0, 2 * WINDOW))
    space, _ = load(bridge=bridge)
    bridge.refused.add(bridge.refs[WINDOW + 1])
    put(tmp_path, lines(logged_rows(space, bridge.events[:WINDOW])), f"bc1.p{WINDOW}".encode("ascii"))
    before = state_bytes(tmp_path)
    with refused(space["CaptureRefused"], "compact_reader_failed"):
        space["advance"](tmp_path)
    assert state_bytes(tmp_path) == before
    assert [call for call in bridge.calls if call[2:3] != ["-EventId"]] == [
        ["-AfterEvent", f"bc1.p{WINDOW}"], ["-ContinuityTail", str(WINDOW)]]
    bridge.extend(logged(2 * WINDOW, 2 * WINDOW + 2))  # the refused row is no longer among the last WINDOW
    rows, record = space["advance"](tmp_path)
    assert record["mode"] == "seed" and record["reseed_after"] == {"read": "detail", "reason": "compact_reader_failed"}
    assert [row["ref"] for row in rows] == bridge.refs[-WINDOW:] and from_the_log(space, bridge, rows)
    assert (tmp_path / "cursor.txt").read_text(encoding="ascii") == f"bc1.p{2 * WINDOW + 2}"


def test_a_reseed_reads_again_only_the_listed_rows_the_verified_window_does_not_hold(tmp_path):
    space, bridge = load()
    put(tmp_path, lines(rows_for(space, REFS[:4])), STALE.encode("ascii"))
    rows, record = space["advance"](tmp_path)
    assert bridge.detail_refs() == REFS[4:6] and record["mode"] == "seed" and record["detail_calls"] == 2
    assert [row["source"] for row in rows] == ["seed", "seed", "detail", "detail"] and verified(space, rows)


def test_a_listing_that_repeats_a_ref_is_read_and_kept_once(tmp_path):
    space, bridge = load(after=[REFS[3], REFS[3], REFS[4]])
    put(tmp_path, lines(rows_for(space, REFS[:3])), GOOD.encode("ascii"))
    rows, record = space["advance"](tmp_path)
    assert bridge.detail_refs() == REFS[3:5] and record["new_rows"] == 2
    assert [row["ref"] for row in rows] == REFS[1:5] and verified(space, rows)


def test_a_delta_listing_that_lists_a_window_row_again_does_not_read_or_keep_it_twice(tmp_path):
    space, bridge = load(after=REFS[2:4])
    put(tmp_path, lines(rows_for(space, REFS[:3])), GOOD.encode("ascii"))
    rows, record = space["advance"](tmp_path)
    assert bridge.detail_refs() == REFS[3:4] and record["new_rows"] == 1
    assert [row["ref"] for row in rows] == REFS[:4] and verified(space, rows)


def test_a_seed_listing_that_repeats_a_ref_reads_it_and_keeps_it_once(tmp_path):
    bridge = BoundedBridge(logged(0, 3) + logged(1, 2))  # the last row repeats the second
    space, _ = load(bridge=bridge)
    rows, record = space["advance"](tmp_path)
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
def test_a_delta_listing_the_producer_cannot_read_refuses_the_cycle_without_a_reseed(tmp_path, case):
    out, reason = LISTING_UNREADABLE[case]
    space, bridge = load(after=REFS[3:5])
    bridge.listing = out
    put(tmp_path, lines(rows_for(space, REFS[:3])), GOOD.encode("ascii"))
    before = state_bytes(tmp_path)
    with refused(space["CaptureRefused"], reason) as raised:
        space["advance"](tmp_path)
    assert type(raised.value) is space.get("CycleRefused") and state_bytes(tmp_path) == before
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
def test_a_delta_detail_the_producer_cannot_read_refuses_the_cycle_without_a_reseed(tmp_path, case):
    out, reason = DETAIL_UNREADABLE[case]
    space, bridge = load(after=REFS[3:4])
    bridge.detail = out
    put(tmp_path, lines(rows_for(space, REFS[:3])), GOOD.encode("ascii"))
    before = state_bytes(tmp_path)
    with refused(space["CaptureRefused"], reason) as raised:
        space["advance"](tmp_path)
    assert type(raised.value) is space.get("CycleRefused") and state_bytes(tmp_path) == before
    tail = str(space["DETAIL_TAIL"])
    assert bridge.calls == [["-AfterEvent", GOOD], ["-ContinuityTail", tail, "-EventId", REFS[3]]]


STARTS_RAISING = {
    "timed_out": (subprocess.TimeoutExpired(["pwsh"], 300), "compact_reader_timed_out"),
    "not_found": (FileNotFoundError(2, "not found"), "compact_reader_not_started"),
    "not_permitted": (PermissionError(13, "denied"), "compact_reader_not_started"),
}


@pytest.mark.parametrize("read", ["listing", "detail"])
@pytest.mark.parametrize("case", STARTS_RAISING)
def test_a_delta_reader_that_times_out_or_cannot_start_refuses_the_cycle_without_a_reseed(tmp_path, case, read):
    error, reason = STARTS_RAISING[case]
    bridge = StartFails(error, read, after=REFS[3:4])
    space, _ = load(bridge=bridge)
    put(tmp_path, lines(rows_for(space, REFS[:3])), GOOD.encode("ascii"))
    before = state_bytes(tmp_path)
    with refused(space["CaptureRefused"], reason) as raised:
        space["advance"](tmp_path)
    assert type(raised.value) is space.get("CycleRefused") and state_bytes(tmp_path) == before
    assert bridge.calls[0] == ["-AfterEvent", GOOD] and ["-ContinuityTail", str(WINDOW)] not in bridge.calls


def test_a_seed_reader_that_cannot_start_refuses_the_cycle_with_its_reason(tmp_path):
    bridge = StartFails(FileNotFoundError(2, "not found"), "listing")
    space, _ = load(bridge=bridge)
    with refused(space["CaptureRefused"], "compact_reader_not_started") as raised:
        space["advance"](tmp_path)
    assert type(raised.value) is space.get("CycleRefused") and bridge.calls == [["-ContinuityTail", str(WINDOW)]]
    assert not (tmp_path / "window.jsonl").exists() and not (tmp_path / "cursor.txt").exists()


REF_SHAPES_RESEED = {  # a listed scalar ref not of the ref shape -> no detail read starts for it; reseeds as before
    "a_number": 5,
    "null": None,
    "true": True,
    "an_upper_case_ref": REFS[3].upper(),
    "a_count": "600",
    "a_flag": "-AfterEvent",
}


@pytest.mark.parametrize("case", REF_SHAPES_RESEED)
def test_a_listed_ref_of_another_scalar_shape_starts_no_detail_read_for_it_and_reseeds(tmp_path, case):
    space, bridge = load()
    bridge.listing = view([REF_SHAPES_RESEED[case]], AFTER)
    put(tmp_path, lines(rows_for(space, REFS[:2])), GOOD.encode("ascii"))
    rows, record = space["advance"](tmp_path)
    assert record["mode"] == "seed" and record["reseed_after"] == {"read": "detail", "reason": "reader_option_invalid"}
    assert bridge.detail_refs() == REFS[-WINDOW:] and verified(space, rows)


def test_a_delta_listing_whose_cursor_is_not_a_string_reseeds_and_names_it(tmp_path):
    space, bridge = load()
    bridge.listing = json.dumps({"schema": "wd.bridge.compact-view.v1", "cursor": 5, "events": []}).encode("utf-8")
    put(tmp_path, lines(rows_for(space, REFS[:2])), GOOD.encode("ascii"))
    rows, record = space["advance"](tmp_path)
    assert record["mode"] == "seed" and record["reseed_after"] == {"read": "listing", "reason": "compact_view_shape"}
    assert [row["ref"] for row in rows] == REFS[-WINDOW:] and verified(space, rows)


def test_a_delta_listing_with_no_events_keeps_the_window_and_takes_the_new_cursor(tmp_path):
    space, bridge = load()
    put(tmp_path, lines(rows_for(space, REFS[:3])), GOOD.encode("ascii"))
    rows, record = space["advance"](tmp_path)
    assert bridge.calls == [["-AfterEvent", GOOD]] and (record["mode"], record["new_rows"]) == ("delta", 0)
    assert [row["ref"] for row in rows] == REFS[:3] and verified(space, rows)
    assert (tmp_path / "cursor.txt").read_text(encoding="ascii") == AFTER


def test_the_state_a_cycle_writes_verifies_on_the_next_cycle(tmp_path):
    # else every cycle would reseed: the written rows must pass the check after their JSON round trip
    space, bridge = load(after=REFS[5:6])
    _, first = space["advance"](tmp_path)
    rows, second = space["advance"](tmp_path)
    assert (first["mode"], second["mode"]) == ("seed", "delta")
    assert bridge.calls[1 + WINDOW] == ["-AfterEvent", SEEDED] and verified(space, rows)


def test_an_empty_window_seeds_without_a_delta_read(tmp_path):
    space, bridge = load(after=REFS[3:5])
    put(tmp_path, b"", GOOD.encode("ascii"))
    rows, record = space["advance"](tmp_path)
    assert bridge.calls[0] == ["-ContinuityTail", str(WINDOW)] and ["-AfterEvent", GOOD] not in bridge.calls
    assert record["mode"] == "seed" and [row["ref"] for row in rows] == REFS[-WINDOW:] and verified(space, rows)
    assert record["reseed_after"] is None


def corrupt(space, case):
    """A persisted state (window bytes, cursor text) with one fault; the other rows are good."""
    rows = rows_for(space, REFS[:3])
    if case == "window_not_utf8":
        return b"\xff" + lines(rows), GOOD
    if case == "window_line_not_json":
        return lines(rows) + b"{\n", GOOD
    if case == "window_line_nested_too_deep":  # a RecursionError, not a ValueError: undecodable all the same
        return lines(rows) + DEEP + b"\n", GOOD
    if case == "window_line_a_list":
        return lines(rows) + b"[1, 2]\n", GOOD
    if case == "window_line_the_key_names":
        return lines(rows) + json.dumps(sorted(rows[0])).encode("ascii") + b"\n", GOOD
    if case == "window_with_a_repeated_row":
        return lines(rows + [rows[1]]), GOOD
    if case == "cursor_not_ascii":
        return lines(rows), "bc1.é"
    row = rows[1]
    if case == "row_with_an_extra_key":
        row["note"] = "x"
    elif case == "row_with_a_foreign_source":
        row["source"] = "forged"
    elif case == "row_event_altered":  # a consistent forgery: only the ref no longer matches
        row["event"] = dict(row["event"], message="altered")
        row["row_sha256"] = space["row_digest"](row["event"])
    elif case == "row_digest_altered":
        row["row_sha256"] = "0" * 64
    elif case == "row_event_not_an_object":
        rows[1] = {"ref": event_id([1]), "row_sha256": space["row_digest"]([1]), "source": "detail", "event": [1]}
    elif case == "row_event_with_nan":  # JSON reads NaN, the canonical ref refuses it
        row["event"] = dict(row["event"], score=float("nan"))
    return lines(rows), GOOD


@pytest.mark.parametrize("case", CORRUPT)
def test_an_unreadable_or_unverified_state_reseeds_and_reuses_none_of_it(tmp_path, case):
    space, bridge = load(after=REFS[3:5])
    window, cursor = corrupt(space, case)
    put(tmp_path, window, cursor.encode("utf-8"))
    rows, record = space["advance"](tmp_path)
    assert record["mode"] == "seed" and not any(call[0] == "-AfterEvent" for call in bridge.calls)
    assert bridge.detail_refs() == REFS[-WINDOW:] and record["reseed_after"] is None
    assert [row["ref"] for row in rows] == REFS[-WINDOW:] and verified(space, rows)
    assert (tmp_path / "cursor.txt").read_text(encoding="ascii") == SEEDED


DIGIT_LIMIT = sys.get_int_max_str_digits()  # 0 when no limit is configured (-X int_max_str_digits=0)


def capture_bytes(events, **keys):
    """A --seed-from file as the producer writes a capture (``write_capture``), with these events and keys."""
    return json.dumps({"schema": "wd.bridge-mind-capture.v1", **keys, "events": events},
                      ensure_ascii=False).encode("utf-8")


def test_a_seed_row_whose_own_ref_is_listed_is_reused_and_only_the_other_listed_rows_are_read(tmp_path):
    space, bridge = load()
    seed = tmp_path / "seed.json"
    seed.write_bytes(capture_bytes([EVENTS[0], *EVENTS[2:5]]))  # EVENTS[0] is not among the last WINDOW rows
    rows, record = space["advance"](tmp_path, space["read_seed"](seed))
    assert bridge.calls[0] == ["-ContinuityTail", str(WINDOW)] and bridge.detail_refs() == REFS[5:]
    assert [row["source"] for row in rows] == ["seed", "seed", "seed", "detail"] and verified(space, rows)
    assert [row["ref"] for row in rows] == REFS[-WINDOW:] and record["mode"] == "seed"
    assert (record["new_rows"], record["detail_calls"]) == (WINDOW, 1)


def test_advance_reads_no_seed_file_itself(tmp_path):
    # the module reads --seed-from once, before any write or process start, and hands advance its rows
    space, bridge = load()
    space["args"].seed_from = tmp_path / "missing.json"
    rows, record = space["advance"](tmp_path)
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
    # fixed reason all the same, as the persisted window's check is
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


SCHEMA = "wd.fable5.mind-shadow-live-latest.v1"  # the producer's own latest.json schema
CYCLE = "20261016T000000Z"  # a cycle dir name of the producer's form (cycle_name)
LAST_OK = {"cycle_dir": CYCLE, "observed_utc": "2026-10-16T00:00:00Z", "capture_file": f"cycles/{CYCLE}/capture.json",
           "context_file": f"cycles/{CYCLE}/context.json", "capture_sha256": "a" * 64, "context_sha256": "b" * 64,
           "reader": "Read-AgentBridge.ps1 -Compact -EventId", "snapshot_sha256": "c" * 64,
           "board_text_sha256": "d" * 64, "counts": {"prs": 1, "lanes": 2, "facts": 3, "tensions": 0, "attention": 1},
           "global_question": None, "measures": {"capture_ms": 5, "events": 3, "new_rows": 3}}
OTHER_DIGITS = "".join(chr(0x0660 + int(c)) if c.isdigit() else c for c in CYCLE)  # Arabic-Indic: \d matches them


def latest_bytes(last_ok=ABSENT, **keys):
    """A latest.json of the producer's schema (or of the schema in ``keys``) whose last_ok is ``last_ok`` (ABSENT: no
    such key), every non-ASCII character escaped and a NaN written as NaN."""
    value = {"schema": SCHEMA, "authority": "none", "cycle": 1, "status": "ok", **keys}
    if last_ok is not ABSENT:
        value["last_ok"] = last_ok
    return json.dumps(value, indent=2, sort_keys=True).encode("ascii")


def with_cycle(literal):
    """A latest.json naming no last_ok whose cycle number is the JSON text ``literal``."""
    return latest_bytes(None, cycle=0).replace(b'"cycle": 0', b'"cycle": ' + literal)


LATEST_REFUSED = {  # a latest.json -> its fixed reason; None: a directory in its place
    "a_directory": (None, "latest_unreadable"),
    "empty": (b"", "latest_unparseable"),
    "not_ascii": (latest_bytes(None, note="n").replace(b'"n"', b'"n' + chr(0xE4).encode("utf-8") + b'"'),
                  "latest_unparseable"),
    "a_utf8_bom": (b"\xef\xbb\xbf" + latest_bytes(None), "latest_unparseable"),
    "not_json": (b"{", "latest_unparseable"),
    "nested_too_deep": (DEEP, "latest_unparseable"),
    "an_int_literal_over_the_digit_limit": (with_cycle(b"1" * (DIGIT_LIMIT + 1)), "latest_unparseable"),
    "a_nan": (latest_bytes(dict(LAST_OK, measures={"run_ms": float("nan")})), "latest_unparseable"),
    "an_infinity_literal": (with_cycle(b"-Infinity"), "latest_unparseable"),
    "an_infinite_number": (with_cycle(b"1e999"), "latest_unparseable"),
    "a_list": (b"[]", "latest_shape_unexpected"),
    "a_string": (b'"latest"', "latest_shape_unexpected"),
    "null": (b"null", "latest_shape_unexpected"),
    # live_mind's schema: a live dir of that tool, whose latest.json names no last_ok, is not this producer's
    "another_tools_latest_json": (latest_bytes(schema="wd.bridge-mind-live-latest.v1", cycle_dir=CYCLE),
                                  "latest_shape_unexpected"),
    "another_schema_with_a_last_ok": (latest_bytes(LAST_OK, schema="wd.other.v1"), "latest_shape_unexpected"),
    "no_schema": (json.dumps({"last_ok": None}).encode("ascii"), "latest_shape_unexpected"),
    "no_last_ok": (latest_bytes(), "latest_shape_unexpected"),
    "last_ok_false": (latest_bytes(False), "latest_shape_unexpected"),
    "last_ok_zero": (latest_bytes(0), "latest_shape_unexpected"),
    "last_ok_an_empty_list": (latest_bytes([]), "latest_shape_unexpected"),
    "last_ok_an_empty_object": (latest_bytes({}), "latest_shape_unexpected"),
    "last_ok_a_string": (latest_bytes(CYCLE), "latest_shape_unexpected"),
    "last_ok_a_list": (latest_bytes([LAST_OK]), "latest_shape_unexpected"),
    "cycle_dir_missing": (latest_bytes({k: v for k, v in LAST_OK.items() if k != "cycle_dir"}),
                          "latest_shape_unexpected"),
    "cycle_dir_null": (latest_bytes(dict(LAST_OK, cycle_dir=None)), "latest_shape_unexpected"),
    "cycle_dir_a_number": (latest_bytes(dict(LAST_OK, cycle_dir=20261016)), "latest_shape_unexpected"),
    "cycle_dir_empty": (latest_bytes(dict(LAST_OK, cycle_dir="")), "latest_shape_unexpected"),
    "cycle_dir_a_parent_step": (latest_bytes(dict(LAST_OK, cycle_dir="../" + CYCLE)), "latest_shape_unexpected"),
    "cycle_dir_a_path_below_one": (latest_bytes(dict(LAST_OK, cycle_dir=CYCLE + "/run")), "latest_shape_unexpected"),
    "cycle_dir_absolute": (latest_bytes(dict(LAST_OK, cycle_dir="C:/" + CYCLE)), "latest_shape_unexpected"),
    "cycle_dir_with_a_trailing_newline": (latest_bytes(dict(LAST_OK, cycle_dir=CYCLE + "\n")),
                                          "latest_shape_unexpected"),
    "cycle_dir_with_other_digits": (latest_bytes(dict(LAST_OK, cycle_dir=OTHER_DIGITS)), "latest_shape_unexpected"),
    "cycle_dir_with_its_separators": (latest_bytes(dict(LAST_OK, cycle_dir="2026-10-16T00:00:00Z")),
                                      "latest_shape_unexpected"),
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


WITHOUT_READER = {key: value for key, value in LAST_OK.items() if key != "reader"}
ELSEWHERE = dict(LAST_OK, capture_file="elsewhere", extra=[1, "x"])
QUESTION = dict(LAST_OK, global_question="q " + chr(0xE4))
LATEST_ACCEPTED = {  # a latest.json -> the last_ok it is read as; None: no file (a first start)
    "missing": (None, None),
    "last_ok_null": (latest_bytes(None), None),  # the first cycles failed, or the privacy guard refused the last one
    "a_last_ok": (latest_bytes(LAST_OK), LAST_OK),
    "a_last_ok_from_before_the_reader_key": (latest_bytes(WITHOUT_READER), WITHOUT_READER),  # as on 10-13
    "a_refused_cycle_keeping_its_last_ok": (latest_bytes(LAST_OK, status="refused", reason="compact_reader_timed_out"),
                                            LAST_OK),
    "other_last_ok_keys_as_read": (latest_bytes(ELSEWHERE), ELSEWHERE),  # not read: carried forward as read
    "an_escaped_character": (latest_bytes(QUESTION), QUESTION),
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
    # the writer's own forms (cycle_name, latest_object, canonical): an ok cycle, a refused one keeping the last ok
    # cycle, a refused one before any ok cycle, and the privacy guard's refusal, which names no last_ok
    space, _ = load()
    name = space["cycle_name"]()
    last_ok = dict(LAST_OK, cycle_dir=name, capture_file=f"cycles/{name}/capture.json",
                   context_file=f"cycles/{name}/context.json")
    finished = space["utc_now"]()
    path = tmp_path / "latest.json"
    for rec, kept in (({"status": "ok", "finished_utc": finished}, last_ok),
                      ({"status": "refused", "reason": "compact_reader_timed_out", "finished_utc": finished}, last_ok),
                      ({"status": "refused", "reason": "ValueError", "finished_utc": finished}, None),
                      (None, None)):
        path.write_bytes(space["canonical"](space["latest_object"](7, rec, kept)))
        assert space["read_latest"](path) == kept, rec


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
        space["prune"](tmp_path, 2)
    assert [path.name for path in made] == ["20261016T120000Z", "20261016T120100Z", "20261016T120200Z",
                                            "20261016T120201Z", "20261016T120202Z", "20261016T120203Z"]
    assert sorted(made) == made
    assert [path.name for path in made if (path / "capture.json").exists()] == ["20261016T120202Z",
                                                                               "20261016T120203Z"]
    assert bridge.calls == []
