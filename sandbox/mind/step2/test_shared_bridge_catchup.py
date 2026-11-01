"""Opt-in bounded chained public-status catch-up (W8a); synthetic tmp files only, no live bridge.

Expected API, intentionally absent when these tests were written:
``read_public_catchup(path, cursor, tasks, *, anchor_bytes, anchor_sha256, generation_path, max_bytes,
max_rows, max_windows, policy_bytes, policy_sha256, roster_bytes, roster_sha256, public_policy_bytes,
public_policy_sha256, expected_prefix_sha256, max_prefix_bytes, enabled=False)`` reads successive
nonterminal windows from one caller-anchored start cursor, re-pinning the byte prefix every window, and
``verify_public_catchup(result, *, expected_anchor_bytes, expected_anchor_sha256, expected_policy_sha256,
expected_roster_sha256, expected_public_policy_sha256, expected_start_prefix)`` rechecks the chain.
Partial coverage is never reported as caught up; oversized rows are never skipped.
"""
from dataclasses import asdict, replace
import hashlib
import json

import pytest

from sandbox.mind.step2 import shared_bridge_snapshot as snapshot
from sandbox.mind.step2 import shared_capture_adapter as adapter
from sandbox.mind.step2.test_status_public_filter import ROSTER
from waggledance.core.bridge_log_reader import BridgePrefixPin, read_bridge_log

TASKS = frozenset({"public-task", "nonreview-private"})


def enc(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def digest(value):
    return hashlib.sha256(value).hexdigest()


def event(index, **changes):
    item = {"ts_utc": "2026-10-31T12:00:%02dZ" % index, "agent": "codex-lead-1",
            "agent_uuid": ROSTER["codex-lead-1"], "type": "message", "status": "done",
            "task_id": "public-task", "to": "", "payload": {"public_status": True}}
    item.update(changes)
    return item


def pins():
    policy = enc({"schema": "wd.q4-non-review-policy.v1", "approved_tasks": sorted(TASKS)})
    roster = enc({"lanes": [{"agent": k, "agent_uuid": v} for k, v in ROSTER.items() if k != "codex-tools-1"],
                  "tools_supervisor": {"agent": "codex-tools-1", "agent_uuid": ROSTER["codex-tools-1"]}})
    public = enc({"schema": "wd.mind-public-status-policy.v1", "public_tasks": ["public-task"],
                  "public_types": ["message"]})
    return {"policy_bytes": policy, "policy_sha256": digest(policy), "roster_bytes": roster,
            "roster_sha256": digest(roster), "public_policy_bytes": public, "public_policy_sha256": digest(public)}


def fixture(tmp_path, events, *, tail=b""):
    path = tmp_path / "synthetic.jsonl"
    path.write_bytes(b"")
    gen = tmp_path / "generation.json"
    gen.write_bytes(enc({"generation": "synthetic-generation"}))
    start = read_bridge_log(path, generation_path=gen).candidate_cursor
    path.write_bytes(b"".join(enc(item) + b"\n" for item in events) + tail)
    return path, gen, start


def anchor_for(path, gen, cursor, p):
    return enc({"schema": "wd.q4-source-anchor.v1", "source_path": str(path), "generation_path": str(gen),
                "cursor_start": asdict(cursor), "policy_sha256": p["policy_sha256"],
                "roster_sha256": p["roster_sha256"], "status_mode": "public-v1",
                "public_policy_sha256": p["public_policy_sha256"]})


def catchup(path, gen, cursor, prefix_sha256, *, max_windows=16, max_rows=3, max_bytes=65536, enabled=True, **over):
    p = pins()
    anchor = anchor_for(path, gen, cursor, p)
    kwargs = dict(anchor_bytes=anchor, anchor_sha256=digest(anchor), generation_path=gen, max_bytes=max_bytes,
                  max_rows=max_rows, max_windows=max_windows, expected_prefix_sha256=prefix_sha256,
                  max_prefix_bytes=1 << 20, enabled=enabled, **p)
    kwargs.update(over)
    return snapshot.read_public_catchup(path, cursor, TASKS, **kwargs), anchor


def verify(result, anchor, start_pin, **over):
    p = pins()
    kwargs = dict(expected_anchor_bytes=anchor, expected_anchor_sha256=digest(anchor),
                  expected_policy_sha256=p["policy_sha256"], expected_roster_sha256=p["roster_sha256"],
                  expected_public_policy_sha256=p["public_policy_sha256"], expected_start_prefix=start_pin)
    kwargs.update(over)
    return snapshot.verify_public_catchup(result, **kwargs)


def records(result):
    return sum(len(window["projection"]["records"]) for window in result["windows"])


def test_disabled_by_default_refuses_before_reading(tmp_path):
    missing = tmp_path / "absent.jsonl"
    with pytest.raises(snapshot.SnapshotRefused) as caught:
        snapshot.read_public_catchup(missing, None, TASKS, anchor_bytes=b"", anchor_sha256="0" * 64,
                                     generation_path=None, max_bytes=1, max_rows=1, max_windows=1,
                                     expected_prefix_sha256=None, max_prefix_bytes=None, **pins())
    assert caught.value.args == ("catchup_disabled",)
    for flag in (False, 1, "yes", None):
        with pytest.raises(snapshot.SnapshotRefused):
            catchup(*fixture(tmp_path, [event(1)]), digest(b""), enabled=flag)


def test_chain_reads_successive_nonterminal_windows_to_the_snapshot_end(tmp_path):
    path, gen, start = fixture(tmp_path, [event(i) for i in range(12)])
    result, anchor = catchup(path, gen, start, digest(b""))
    data = path.read_bytes()
    assert result["schema"] == "wd.q4-public-catchup.v1"
    assert result["window_count"] == len(result["windows"]) == 4
    assert result["caught_up"] is True and result["end_reason"] == "snapshot_end"
    assert result["coverage"] == "chained_delta_caught_up_to_final_snapshot_not_history"
    assert records(result) == 12
    previous = BridgePrefixPin(start, digest(b""))
    assert result["start_prefix"] == previous
    for index, window in enumerate(result["windows"]):
        assert window["start_prefix"] == previous
        candidate = window["candidate_cursor"]
        assert window["candidate_prefix"] == BridgePrefixPin(candidate, digest(data[:candidate.offset]))
        assert (candidate.file_identity, candidate.generation) == (start.file_identity, start.generation)
        last = index == len(result["windows"]) - 1
        assert (window["source"]["unconsumed_bytes"] == 0) is last
        assert window["source"]["coverage"] == ("delta_only_not_complete_history" if last
                                                else "partial_window_not_complete")
        previous = window["candidate_prefix"]
    assert result["candidate_cursor"].offset == len(data)
    assert result["candidate_prefix"] == previous
    assert verify(result, anchor, BridgePrefixPin(start, digest(b"")))


def test_window_budget_leaves_partial_coverage_resumable_never_caught_up(tmp_path):
    path, gen, start = fixture(tmp_path, [event(i) for i in range(12)])
    first, anchor = catchup(path, gen, start, digest(b""), max_windows=2)
    assert first["caught_up"] is False and first["end_reason"] == "max_windows"
    assert first["coverage"] == "chained_delta_partial_not_caught_up"
    assert first["candidate_cursor"].offset < path.stat().st_size and records(first) == 6
    assert all(w["source"]["unconsumed_bytes"] > 0 for w in first["windows"])
    assert verify(first, anchor, BridgePrefixPin(start, digest(b"")))
    resume = first["candidate_prefix"]
    second, anchor2 = catchup(path, gen, resume.cursor, resume.sha256)
    assert second["caught_up"] is True and records(first) + records(second) == 12
    assert second["start_prefix"] == resume
    assert verify(second, anchor2, resume)


def test_oversized_row_is_never_skipped_and_refuses_the_chain(tmp_path):
    big = event(50, task_id="nonreview-private", payload={}, message="x" * (adapter._EVENT_LIMIT // 6 + 10))
    rows = [event(i) for i in range(6)] + [big] + [event(i) for i in range(6, 9)]
    path, gen, start = fixture(tmp_path, rows)
    for max_bytes in (snapshot.MAX_BYTES, 4096):
        with pytest.raises(snapshot.SnapshotRefused):
            catchup(path, gen, start, digest(b""), max_bytes=max_bytes)
    before, anchor = catchup(path, gen, start, digest(b""), max_windows=2)
    assert before["caught_up"] is False and records(before) == 6
    resume = before["candidate_prefix"]
    with pytest.raises(snapshot.SnapshotRefused):
        catchup(path, gen, resume.cursor, resume.sha256)


def test_oversized_private_row_refusal_drops_all_exception_context_and_raw_locals(tmp_path):
    import traceback

    sentinel = "synthetic-private-catchup-sentinel"
    private = event(50, task_id="nonreview-private", payload={},
                    message=sentinel + "x" * (adapter._EVENT_LIMIT // 6 + 10))
    path, gen, start = fixture(tmp_path, [event(0), private])
    with pytest.raises(snapshot.SnapshotRefused) as caught:
        catchup(path, gen, start, digest(b""), max_bytes=snapshot.MAX_BYTES)
    error = caught.value
    assert error.__context__ is None
    assert sentinel not in str(error)
    checked = 0
    while error is not None:
        for frame, _ in traceback.walk_tb(error.__traceback__):
            if frame.f_code.co_filename.endswith(("shared_bridge_snapshot.py", "shared_capture_adapter.py")):
                checked += 1
                assert sentinel not in repr(frame.f_locals)
        error = error.__context__
    assert checked > 0


def test_catchup_keyboard_interrupt_is_not_a_sanitized_refusal(tmp_path, monkeypatch):
    path, gen, start = fixture(tmp_path, [event(0)])

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(snapshot, "read_bridge_log", interrupted)
    with pytest.raises(KeyboardInterrupt):
        catchup(path, gen, start, digest(b""))


def test_disabled_catchup_drops_private_arguments_from_refusal_frame():
    import traceback

    sentinel = b"synthetic-private-disabled-anchor"
    with pytest.raises(snapshot.SnapshotRefused) as caught:
        snapshot.read_public_catchup(None, None, TASKS, anchor_bytes=sentinel, anchor_sha256="0" * 64,
                                    generation_path=None, max_bytes=1, max_rows=1, max_windows=1,
                                    expected_prefix_sha256="1" * 64, max_prefix_bytes=1, **pins())
    frames = [frame for frame, _ in traceback.walk_tb(caught.value.__traceback__)
              if frame.f_code.co_name == "read_public_catchup"]
    assert len(frames) == 1
    assert sentinel not in tuple(frames[0].f_locals.values())


def test_trailing_partial_record_ends_without_claiming_caught_up(tmp_path):
    path, gen, start = fixture(tmp_path, [event(i) for i in range(4)], tail=enc(event(9))[:40])
    result, anchor = catchup(path, gen, start, digest(b""))
    assert result["caught_up"] is False and result["end_reason"] == "partial_record"
    assert result["coverage"] == "chained_delta_partial_not_caught_up"
    assert records(result) == 4
    assert result["candidate_cursor"].offset == path.stat().st_size - 40
    assert result["windows"][-1]["source"]["unconsumed_bytes"] == 40
    assert verify(result, anchor, BridgePrefixPin(start, digest(b"")))


def test_admission_policy_is_unchanged_inside_windows(tmp_path):
    rows = [event(0), event(1, payload={}), event(2, task_id="nonreview-private"), event(3, to="fable-5"),
            event(4, message="free text"), event(5)]
    path, gen, start = fixture(tmp_path, rows)
    result, _ = catchup(path, gen, start, digest(b""), max_rows=2)
    assert result["caught_up"] is True and records(result) == 2
    sub = tmp_path / "identity"
    sub.mkdir()
    path2, gen2, start2 = fixture(sub, [event(0), event(1, agent_uuid=ROSTER["fable-5"])])
    with pytest.raises(snapshot.SnapshotRefused):
        catchup(path2, gen2, start2, digest(b""), max_rows=1)


def test_prefix_generation_and_identity_pins_are_enforced_on_resume(tmp_path):
    path, gen, start = fixture(tmp_path, [event(i) for i in range(9)])
    with pytest.raises(snapshot.SnapshotRefused):
        catchup(path, gen, start, "0" * 64)
    first, _ = catchup(path, gen, start, digest(b""), max_windows=1)
    resume = first["candidate_prefix"]
    with pytest.raises(snapshot.SnapshotRefused):
        catchup(path, gen, resume.cursor, "1" * 64)
    with pytest.raises(snapshot.SnapshotRefused):
        catchup(path, gen, replace(resume.cursor, generation="other-generation"), resume.sha256)
    with pytest.raises(snapshot.SnapshotRefused):
        catchup(path, gen, replace(resume.cursor, file_identity="windows-v1:00000000:0000000000000000"),
                resume.sha256)
    with path.open("r+b") as stream:
        stream.seek(0)
        stream.write(b" ")
    with pytest.raises(snapshot.SnapshotRefused):
        catchup(path, gen, resume.cursor, resume.sha256)


def test_generation_change_between_calls_refuses(tmp_path):
    path, gen, start = fixture(tmp_path, [event(i) for i in range(9)])
    first, _ = catchup(path, gen, start, digest(b""), max_windows=1)
    gen.write_bytes(enc({"generation": "rotated-generation"}))
    resume = first["candidate_prefix"]
    with pytest.raises(snapshot.SnapshotRefused):
        catchup(path, gen, resume.cursor, resume.sha256)


@pytest.mark.parametrize("over", [
    {"max_windows": 0}, {"max_windows": True}, {"max_windows": snapshot.CATCHUP_MAX_WINDOWS + 1},
    {"max_windows": 1.0}, {"public_policy_bytes": None, "public_policy_sha256": None},
    {"expected_prefix_sha256": None, "max_prefix_bytes": None}, {"anchor_sha256": "0" * 64},
])
def test_configuration_refusals(tmp_path, over):
    path, gen, start = fixture(tmp_path, [event(i) for i in range(3)])
    with pytest.raises(snapshot.SnapshotRefused):
        catchup(path, gen, start, digest(b""), **over)


def test_anchor_must_bind_the_chain_start_cursor(tmp_path):
    path, gen, start = fixture(tmp_path, [event(i) for i in range(9)])
    first, _ = catchup(path, gen, start, digest(b""), max_windows=1)
    resume = first["candidate_prefix"]
    p = pins()
    stale = anchor_for(path, gen, start, p)
    with pytest.raises(snapshot.SnapshotRefused):
        catchup(path, gen, resume.cursor, resume.sha256, anchor_bytes=stale, anchor_sha256=digest(stale))


def test_verify_rejects_spliced_or_relabelled_chains(tmp_path):
    path, gen, start = fixture(tmp_path, [event(i) for i in range(12)])
    result, anchor = catchup(path, gen, start, digest(b""))
    pin = BridgePrefixPin(start, digest(b""))
    assert verify(result, anchor, pin)
    windows = result["windows"]
    other, _ = catchup(path, gen, start, digest(b""), max_windows=2)
    changed = [
        {**result, "windows": windows[:-1]},
        {**result, "windows": (windows[1], windows[0]) + windows[2:]},
        {**result, "windows": other["windows"] + windows[2:]},
        {**result, "caught_up": False},
        {**result, "end_reason": "max_windows"},
        {**result, "coverage": "chained_delta_partial_not_caught_up"},
        {**result, "window_count": 3},
        {**result, "candidate_cursor": windows[0]["candidate_cursor"]},
        {**result, "candidate_prefix": windows[0]["candidate_prefix"]},
        {**result, "start_prefix": replace(pin, sha256="0" * 64)},
        {k: v for k, v in result.items() if k != "_catchup_binding"},
        {**result, "_catchup_binding": other["_catchup_binding"]},
    ]
    projected = dict(windows[1], projection={**windows[1]["projection"], "records": []})
    changed.append({**result, "windows": (windows[0], projected) + windows[2:]})
    relabelled = dict(windows[0], source={**windows[0]["source"], "coverage": "delta_only_not_complete_history"})
    changed.append({**result, "windows": (relabelled,) + windows[1:]})
    for item in changed:
        with pytest.raises(snapshot.SnapshotRefused):
            verify(item, anchor, pin)
    for over in ({"expected_start_prefix": replace(pin, sha256="0" * 64)},
                 {"expected_public_policy_sha256": "0" * 64}, {"expected_anchor_bytes": anchor + b" "},
                 {"expected_policy_sha256": "0" * 64}, {"expected_roster_sha256": "0" * 64}):
        with pytest.raises(snapshot.SnapshotRefused):
            verify(result, anchor, pin, **over)
    assert other["caught_up"] is False
    with pytest.raises(snapshot.SnapshotRefused):
        verify({**other, "caught_up": True, "end_reason": "snapshot_end",
                "coverage": "chained_delta_caught_up_to_final_snapshot_not_history"}, anchor, pin)


def test_terminal_only_reader_is_unchanged_for_nonterminal_windows(tmp_path):
    path, gen, start = fixture(tmp_path, [event(i) for i in range(6)])
    p = pins()
    anchor = anchor_for(path, gen, start, p)
    with pytest.raises(snapshot.SnapshotRefused):
        snapshot.read_anchored_delta(path, start, TASKS, anchor_bytes=anchor, anchor_sha256=digest(anchor),
                                     generation_path=gen, max_bytes=65536, max_rows=3,
                                     expected_prefix_sha256=digest(b""), max_prefix_bytes=1 << 20, **p)
    whole = snapshot.read_anchored_delta(path, start, TASKS, anchor_bytes=anchor, anchor_sha256=digest(anchor),
                                         generation_path=gen, max_bytes=65536, max_rows=100,
                                         expected_prefix_sha256=digest(b""), max_prefix_bytes=1 << 20, **p)
    assert whole["source"]["unconsumed_bytes"] == 0
    assert whole["source"]["coverage"] == "delta_only_not_complete_history"


def test_adapter_refuses_nonterminal_snapshots_unless_opted_in(tmp_path):
    from sandbox.mind.step2.shared_bridge_snapshot import _token
    from sandbox.mind import mind_capture_producer as producer
    from tools.bridge_compact_view import event_id
    import datetime as dt
    path, gen, start = fixture(tmp_path, [event(0)])
    end = replace(start, offset=100)
    snap = {"file_identity": start.file_identity, "cursor_start": _token(start), "cursor_end": _token(end),
            "generation": start.generation, "snapshot_length": 140, "position": 100, "unconsumed_bytes": 40,
            "observed_utc": dt.datetime.now(dt.timezone.utc).isoformat()}
    row = event(0)
    rows = [{"event": row, "ref": event_id(row), "row_sha256": producer.row_digest(row), "source": "detail"}]
    p = pins()
    tasks, _ = adapter.strict_pins(p["policy_bytes"], p["policy_sha256"], p["roster_bytes"], p["roster_sha256"])
    public = adapter.public_status_policy(p["public_policy_bytes"], p["public_policy_sha256"], tasks)
    strict = {k: p[k] for k in ("policy_bytes", "policy_sha256", "roster_bytes", "roster_sha256")}
    with pytest.raises(adapter.AdapterRefused):
        adapter.adapt_rows(rows, snap, tasks, ttl_seconds=900, **strict, _public_tasks=public[0],
                           _public_types=public[1])
    with pytest.raises(adapter.AdapterRefused):
        adapter.adapt_rows(rows, snap, tasks, ttl_seconds=900, **strict, _allow_partial=True)
    out = adapter.adapt_rows(rows, snap, tasks, ttl_seconds=900, **strict, _public_tasks=public[0],
                             _public_types=public[1], _allow_partial=True)
    assert out["source"]["unconsumed_bytes"] == 40 and out["source"]["position"] == 100
    for bad in ({"unconsumed_bytes": 39}, {"position": 141, "snapshot_length": 140}):
        with pytest.raises(adapter.AdapterRefused):
            adapter.adapt_rows(rows, {**snap, **bad}, tasks, ttl_seconds=900, **strict, _public_tasks=public[0],
                               _public_types=public[1], _allow_partial=True)
    with pytest.raises(adapter.AdapterRefused):
        adapter.adapt_rows(rows, snap, tasks, ttl_seconds=900, **strict, _public_tasks=public[0],
                           _public_types=public[1], _allow_partial=1)
