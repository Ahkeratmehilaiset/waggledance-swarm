"""Hex-Mind step 1b adapter tests: identity stability, parity with full_rebuild, restart and crash safety, and the
negative controls (gap, identity and generation change, corrupted checkpoint). Synthetic rows only; the cursor mode
runs the real compact reader over a temporary log file."""
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import adapter  # noqa: E402
import run_parity_1b as harness  # noqa: E402
from tools import wd_bridge_mind as core  # noqa: E402
from tools.bridge_compact_view import event_id  # noqa: E402

REPO = "Ahkeratmehilaiset/waggledance-swarm"
UUID = "0f5e1d2c-3b4a-4c5d-8e9f-a0b1c2d3e4f5"
MARK = "synthetic-step1b-text"


def ts(second):
    return "2026-10-14T04:%02d:%02dZ" % divmod(second, 60)


def row(n):
    """A small but varied bridge row: lane observations, PR heads, an RCO pass, a claim and a release."""
    agent = ("codex-lead-1", "claude-rco-1", "codex-tools-1", "fable-5")[n % 4]
    event = {"ts_utc": ts(n), "agent": agent, "agent_uuid": UUID, "session_id": "s-" + agent, "run_id": "r1",
             "type": "message", "task_id": "%s/t%d" % (agent, n % 5), "status": "info", "message": "%s %d" % (MARK, n),
             "payload": {}}
    if n % 3 == 0:
        event["payload"] = {"pr": 1900 + n % 4, "head": ("%x" % (n % 16)) * 40}
    if agent == "claude-rco-1" and n % 6 == 1:
        event.update(type="decision", status="rco_pass", payload={"pr": 1901, "head": "1" * 40})
    if agent == "fable-5" and n % 8 == 3:
        event.update(type="claim", write_scope=["sandbox/mind/step1/adapter.py"])
    if agent == "fable-5" and n % 8 == 7:
        event.update(type="release", task_id="fable-5/t%d" % ((n - 4) % 5))
    return event


ROWS = [row(n) for n in range(60)]


def capture_bytes(end, size=12, observed=None, repo=REPO, rows=None):
    window = (rows if rows is not None else ROWS[max(0, end - size):end])
    manifest = "\n".join("%s %s seed" % (event_id(e), event_id(e)) for e in window).encode("ascii")
    capture = {"schema": adapter.CAPTURE_SCHEMA, "reader": adapter.PINNED_READER,
               "reader_stdout_sha256": hashlib.sha256(manifest).hexdigest(),
               "observed_utc": observed or ts(end + 1), "events": window}
    data = json.dumps(capture, ensure_ascii=False).encode("utf-8")
    record = {"schema": adapter.CAPTURE_RECORD_SCHEMA, "authority": "none", "repo": repo,
              "bridge": {"events": len(window), "observed_utc": capture["observed_utc"], "detail_calls": 0},
              "files": {"capture.json": hashlib.sha256(data).hexdigest()}}
    return data, json.dumps(record, indent=2, sort_keys=True).encode("ascii")


def page(end, **options):
    return adapter.capture_page(*capture_bytes(end, **options))


@pytest.fixture
def audit(tmp_path):
    root = tmp_path / ".codex-audit"
    root.mkdir()
    return root


def digests(state, as_of):
    return (hashlib.sha256(core.state_bytes(state)).hexdigest(),
            hashlib.sha256(core.canonical_bytes(core.snapshot(state, as_of_utc=as_of))).hexdigest())


def full(rows, identity, as_of):
    envelopes = [{"ref": event_id(r), "event": r} for r in rows]
    return core.full_rebuild(envelopes, source=adapter._source(identity, 0, len(envelopes)), as_of_utc=as_of)


ENDS = [12, 12, 15, 16, 16, 21, 30, 33, 40]


def run(directory, audit, ends=ENDS):
    return [adapter.step(directory, audit, page(end)) for end in ends]


def test_cursor_round_trip_and_refusals():
    token = adapter.encode_cursor("windows-v1:0000abcd:0000000000001234", "g1", 4096)
    assert adapter.decode_cursor(token) == {"file_identity": "windows-v1:0000abcd:0000000000001234",
                                            "generation": "g1", "offset": 4096}
    assert adapter.decode_cursor(adapter.encode_cursor("posix-v1:1:2", None, 0))["generation"] is None
    for bad in ("", "bc2." + token[4:], token + "!", "bc1." + "A" * 10, 7,
                adapter.encode_cursor("windows-v1:1:2", "g1", 1)[:-2]):
        with pytest.raises(adapter.AdapterError):
            adapter.decode_cursor(bad)
    for body in ({"file_identity": "x", "generation": None, "offset": -1},
                 {"file_identity": "x", "generation": None, "offset": 1, "extra": 1},
                 {"file_identity": "x", "generation": "-g", "offset": 1}):
        raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        with pytest.raises(adapter.AdapterError):
            adapter.decode_cursor("bc1." + base64.urlsafe_b64encode(raw).decode().rstrip("="))
    spaced = json.dumps({"file_identity": "x", "generation": None, "offset": 1}).encode()  # not the reader's form
    with pytest.raises(adapter.AdapterError, match="not_canonical"):
        adapter.decode_cursor("bc1." + base64.urlsafe_b64encode(spaced).decode().rstrip("="))


def test_identity_ignores_observation_time_and_capture_bytes(audit):
    first, second = capture_bytes(12), capture_bytes(12, observed=ts(50))
    assert first[0] != second[0]  # the old identity: "capture-window:" + sha256(capture) changes ...
    old = ["capture-window:" + hashlib.sha256(data).hexdigest() for data, _ in (first, second)]
    assert old[0] != old[1]
    one, two = adapter.capture_page(*first), adapter.capture_page(*second)
    assert one["identity"] == two["identity"]  # ... the declared identity does not
    assert adapter.step(audit / "s", audit, one)["action"] == "origin"
    receipt = adapter.step(audit / "s", audit, two)
    assert (receipt["action"], receipt["reason"], receipt["coverage"]) == ("no_change", "no_new_rows", "contiguous")
    assert receipt["written"] == {"core_bytes": 0, "pointer_bytes": 0}
    assert adapter.step(audit / "s", audit, page(14))["action"] == "advance"  # and the next rows fold incrementally


def test_incremental_fold_equals_full_rebuild_reloading_every_step(audit):
    directory = audit / "s"
    for end in ENDS:
        receipt = adapter.step(directory, audit, page(end))  # step() reloads the checkpoint from disk each time
        assert receipt["action"] in ("origin", "advance", "no_change")
        state, record = adapter.load(directory, audit)
        identity = page(end)["identity"]
        assert digests(state, ts(end + 1)) == digests(full(ROWS[:end], identity, ts(end + 1)), ts(end + 1))
        assert record["core_cursor"]["end"] == end and record["anchor"] == [event_id(r) for r in ROWS[:end]][-8:]
    actions = [r["action"] for r in run(audit / "again", audit)]
    assert actions == ["origin", "no_change", "advance", "advance", "no_change", "advance", "advance", "advance",
                       "advance"]


def test_already_applied_and_stale_pages_change_nothing(audit):
    directory = audit / "s"
    run(directory, audit)
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    again = adapter.step(directory, audit, page(40))
    older = adapter.step(directory, audit, page(33))
    assert (again["action"], older["action"], older["reason"]) == ("no_change", "already_applied",
                                                                   "window_refs_all_seen")
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == before


def test_gap_gives_unknown_and_rebuild_required_never_a_fold(audit):
    directory = audit / "s"
    run(directory, audit, [12, 20])
    before = (directory / adapter.POINTER).read_bytes()
    gap = adapter.step(directory, audit, page(33))  # window rows 21..32: row 20 is missing
    assert (gap["action"], gap["reason"], gap["coverage"]) == ("rebuild_required", "window_does_not_cover_checkpoint",
                                                               "UNKNOWN")
    mismatch = adapter.step(directory, audit, page(24, rows=ROWS[10:17] + ROWS[18:24]))  # a hole inside the anchor run
    assert (mismatch["action"], mismatch["reason"]) == ("rebuild_required", "window_overlap_mismatch")
    assert (directory / adapter.POINTER).read_bytes() == before
    rebuilt = adapter.step(directory, audit, page(33), rebuild=True)
    record = adapter.load(directory, audit)[1]
    assert rebuilt["action"] == "origin" and record["coverage"]["rebuilt_after"] == "window_does_not_cover_checkpoint"
    assert record["core_cursor"]["end"] == 12 and record["step"] == 2


def test_declared_identity_change_is_rebuild_required(audit):
    directory = audit / "s"
    run(directory, audit, [12])
    other = adapter.step(directory, audit, page(14, repo="someone/else"))
    assert (other["action"], other["reason"]) == ("rebuild_required", "log_identity_changed")


def test_refusals_keep_the_checkpoint(audit):
    directory = audit / "s"
    run(directory, audit, [12])
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    older = adapter.step(directory, audit, page(14, observed=ts(5)))  # new rows, yet observed before the checkpoint
    early = adapter.step(directory, audit, page(16, observed=ts(14)))  # a row newer than its own observation time
    assert (older["action"], older["reason"]) == ("refused", "observation_time_regressed")
    assert (early["action"], early["reason"]) == ("refused", "core:as_of_precedes_event")
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == before
    assert adapter.step(directory, audit, page(16))["action"] == "advance"  # a corrected page at the same cursor


def corrupt_variants(directory, audit):
    record = json.loads((directory / adapter.POINTER).read_text(encoding="utf-8"))
    step_file = directory / record["core_checkpoint"]["file"]

    def flip_core(d):
        data = bytearray((d / step_file.name).read_bytes())
        index = data.index(b'"duplicates":') + len(b'"duplicates":')
        data[index] = ord("7") if data[index] != ord("7") else ord("8")
        (d / step_file.name).write_bytes(bytes(data))

    def flip_pointer(d):
        data = (d / adapter.POINTER).read_bytes()
        anchor = record["anchor"][-1].encode("ascii")
        (d / adapter.POINTER).write_bytes(data.replace(anchor, anchor[::-1], 1))  # one field, bytes exact

    def truncate_pointer(d):
        data = (d / adapter.POINTER).read_bytes()
        (d / adapter.POINTER).write_bytes(data[:len(data) // 2])

    def drop_core(d):
        (d / step_file.name).unlink()

    def swap_core(d):
        older = sorted(p for p in d.glob("core-*.json") if p.name != step_file.name)[-1]
        (d / step_file.name).write_bytes(older.read_bytes())

    def rebind(d):  # a pointer with a fresh digest that points at another stream: binding refuses it
        changed = dict(record, anchor=record["anchor"][:-1] + ["0" * 64])
        changed["record_sha256"] = adapter._record_digest(changed)
        (d / adapter.POINTER).write_bytes(core.canonical_bytes(changed))

    return [("checkpoint_core_mismatch", flip_core), ("checkpoint_digest_mismatch", flip_pointer),
            ("checkpoint_pointer_unreadable", truncate_pointer), ("checkpoint_core_missing", drop_core),
            ("checkpoint_core_mismatch", swap_core), ("checkpoint_binding_mismatch", rebind)]


def test_corrupted_checkpoint_is_refused_never_used(audit, monkeypatch):
    directory = audit / "s"
    run(directory, audit, [12, 15, 16])
    for expected, corrupt in corrupt_variants(directory, audit):
        copy = audit / ("bad-" + corrupt.__name__)
        shutil.copytree(directory, copy)
        corrupt(copy)
        with pytest.raises(adapter.AdapterError, match=expected):
            adapter.load(copy, audit)
        files = {p.name: p.read_bytes() for p in copy.iterdir()}
        receipt = adapter.step(copy, audit, page(21))
        assert (receipt["action"], receipt["reason"]) == ("rebuild_required", "checkpoint_refused:" + expected)
        assert {p.name: p.read_bytes() for p in copy.iterdir()} == files  # nothing written, nothing used
    monkeypatch.setitem(adapter.CODE, "adapter_version", "1b.0")
    with pytest.raises(adapter.AdapterError, match="checkpoint_code_changed"):
        adapter.load(directory, audit)


class Crash(BaseException):
    """A crash, not an error: nothing in the adapter or the core may catch it."""


@pytest.mark.parametrize("target", ["core-", adapter.POINTER])
def test_crash_before_a_replace_then_restart_gives_the_same_final_state(audit, monkeypatch, target):
    clean = audit / "clean"
    run(clean, audit)
    final = digests(adapter.load(clean, audit)[0], ts(41))
    directory = audit / "crash"
    run(directory, audit, ENDS[:5])
    pointer = (directory / adapter.POINTER).read_bytes()
    real = os.replace

    def crash_replace(src, dst, *args, **kwargs):
        if Path(dst).name.startswith(target):
            raise Crash()
        return real(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", crash_replace)
    with pytest.raises(Crash):
        adapter.step(directory, audit, page(ENDS[5]))
    monkeypatch.setattr(os, "replace", real)
    assert (directory / adapter.POINTER).read_bytes() == pointer  # the previous checkpoint is still in force
    (directory / ".mind-orphan.tmp").write_bytes(b"{half")  # what a hard crash leaves behind
    run(directory, audit, ENDS[5:])
    assert digests(adapter.load(directory, audit)[0], ts(41)) == final
    assert not list(directory.glob(".mind-*.tmp"))


@pytest.mark.parametrize("target", ["core-", adapter.POINTER])
def test_hard_crash_in_a_child_process_between_temp_and_replace(audit, tmp_path, target):
    directory = audit / "child"
    run(directory, audit, ENDS[:5])
    pointer = (directory / adapter.POINTER).read_bytes()
    assert harness.crash_child(page(ENDS[5]), target, directory, audit, tmp_path) == 77  # died inside that replace
    assert (directory / adapter.POINTER).read_bytes() == pointer
    assert list(directory.glob(".mind-*.tmp"))  # the hard crash left its temp file
    run(directory, audit, ENDS[5:])
    clean = audit / "clean"
    run(clean, audit)
    assert digests(adapter.load(directory, audit)[0], ts(41)) == digests(adapter.load(clean, audit)[0], ts(41))


def test_receipts_and_pointer_carry_no_event_text(audit):
    directory = audit / "s"
    receipts = run(directory, audit)
    assert MARK not in json.dumps(receipts) and MARK.encode() not in (directory / adapter.POINTER).read_bytes()


# -- reader cursor mode: the real compact reader over a temporary log ----------------------------------------------

HEARTBEAT = {"ts_utc": ts(0), "agent": "fable-5", "type": "heartbeat", "task_id": "fable-5/hb", "status": "alive",
             "message": "", "payload": {}}


class Log:
    def __init__(self, path, generation="g1"):
        self.path = path
        path.parent.mkdir(parents=True)
        path.write_bytes(b"")
        self.generation(generation)

    def generation(self, value):
        self.path.with_name("events.generation.json").write_text(json.dumps({"generation": value}), encoding="ascii")

    def append(self, rows):
        harness.append_rows(self.path, rows)

    def page(self, observed, after="", tail=200):
        view, refused = harness.reader_view(self.path, tail=tail, after=after)
        if refused:
            return refused
        by_ref = {event_id(r): r for r in ROWS}
        return adapter.reader_page(view, [by_ref[item["ref"]] for item in view["events"]], observed_utc=observed,
                                   after=after)


def test_reader_cursor_mode_parity_duplicates_gap_overlap_generation_and_rotation(audit, tmp_path):
    log = Log(tmp_path / "log" / "events.jsonl")
    directory = audit / "cursor"
    log.append(ROWS[:12] + [HEARTBEAT])
    origin = log.page(ts(13))
    assert origin["identity"]["kind"] == "reader_cursor" and origin["after"] is None
    assert adapter.step(directory, audit, origin)["action"] == "origin"
    end = 12
    for count in (3, 0, 1, 5):
        log.append(ROWS[end:end + count] + [HEARTBEAT])
        end += count
        cursor = adapter.load(directory, audit)[1]["reader_cursor"]["token"]
        delta = log.page(ts(end + 1), after=cursor)
        receipt = adapter.step(directory, audit, delta)
        assert (receipt["action"], receipt["proof"], receipt["rows_folded"]) == ("advance", "reader_cursor", count)
        assert receipt["written"]["core_bytes"] > 0 if count else receipt["written"]["core_bytes"] == 0
        state, record = adapter.load(directory, audit)
        assert record["reader_cursor"]["offset"] == log.path.stat().st_size
        assert digests(state, ts(end + 1)) == digests(full(ROWS[:end], delta["identity"], ts(end + 1)), ts(end + 1))
    last = delta
    files = {p.name: p.read_bytes() for p in directory.iterdir()}
    duplicate = adapter.step(directory, audit, last)
    assert (duplicate["action"], duplicate["reason"]) == ("already_applied", "page_at_or_before_checkpoint")
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == files
    mark = adapter.load(directory, audit)[1]["reader_cursor"]["token"]
    log.append(ROWS[end:end + 2])
    skipped = log.page(ts(end + 3), after=mark)
    log.append(ROWS[end + 2:end + 4])
    gap = adapter.step(directory, audit, log.page(ts(end + 5), after=skipped["token"]))
    assert (gap["action"], gap["reason"], gap["coverage"]) == ("rebuild_required", "cursor_gap", "UNKNOWN")
    overlap = adapter.step(directory, audit, log.page(ts(end + 5), after=origin["token"]))  # read from an older cursor
    assert (overlap["action"], overlap["reason"]) == ("refused", "page_overlaps_checkpoint")
    window = adapter.step(directory, audit, log.page(ts(end + 5)))  # a tail window with the same identity continues
    assert (window["action"], window["proof"], window["rows_folded"]) == ("advance", "content_overlap_reader_identity", 4)
    assert {p.name: p.read_bytes() for p in directory.iterdir()} != files
    log.generation("g2")
    assert log.page(ts(end + 6), after=adapter.load(directory, audit)[1]["reader_cursor"]["token"]) == \
        "position_cursor_unavailable_fresh_snapshot_required"  # the reader itself refuses the old cursor
    regenerated = adapter.step(directory, audit, log.page(ts(end + 6)))
    assert (regenerated["action"], regenerated["reason"]) == ("rebuild_required", "generation_changed")
    log.generation("g1")
    rotated = log.path.with_name("events.rotated")
    rotated.write_bytes(log.path.read_bytes())
    os.replace(rotated, log.path)
    moved = adapter.step(directory, audit, log.page(ts(end + 6)))
    assert (moved["action"], moved["reason"]) == ("rebuild_required", "file_identity_changed")
    kind = adapter.step(directory, audit, page(end + 4))
    assert (kind["action"], kind["reason"]) == ("rebuild_required", "identity_kind_changed")
