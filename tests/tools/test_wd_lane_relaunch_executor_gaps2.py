# SPDX-License-Identifier: BUSL-1.1
"""Journal-accounting guards the F2/F3 revision leaves unpinned: corrupt markers and a forged cancelled flag."""
from __future__ import annotations

import pytest

from test_wd_lane_relaunch_executor import (  # sibling test module: its fakes are the fixtures here
    DIGEST, Executor, FakePorts, RecoveryStore, _StopKillsThenRaises, auto_mode, iso, NOW, phase, request, signed_catalog,
)


def counted(store, tmp_path):
    """The relaunches the journal counts, read by a fresh executor exactly as a later request would."""
    return Executor(catalog=signed_catalog(), catalog_sha256=DIGEST, store=store, ports=FakePorts(),
                    runtime_root=str(tmp_path / "rt-read"), request=request())._journal_history()


def held_transition(tmp_path):
    """A transition held at checkpointed after a raising stop that killed the source: it counts through its intent row."""
    store = RecoveryStore(tmp_path / "journal.sqlite")
    ex = Executor(catalog=signed_catalog(), catalog_sha256=DIGEST, store=store, ports=_StopKillsThenRaises(),
                  runtime_root=str(tmp_path / "rt"), request=request())
    assert "operator_required" in ex.run()["payload"]["reasons"]
    assert phase(store, ex.tid) == "checkpointed"
    return ex, store


def set_intent_reason(store, tid, reason):
    with store.connect() as db:
        db.execute("UPDATE journal SET reason=? WHERE transition_id=? AND phase='checkpointed' AND reason LIKE '%stop_intent_at%'",
                   (reason, tid))


def test_a_held_transition_counts_through_its_intent_marker(tmp_path, auto_mode):
    ex, store = held_transition(tmp_path)
    assert counted(store, tmp_path) == [{"lane": "claude-rco-1", "ts_utc": iso(NOW), "outcome": "stop_attempted"}]


@pytest.mark.parametrize("bad", ['{"stop_intent_at": "garbage"}', '{"stop_intent_at": 12345}', '{"stop_intent_at": null}',
                                 '{"stop_intent_at": "2026-09-26T12:00:00"}'],
                         ids=["garbage", "int", "null", "naive"])
def test_a_present_but_unusable_intent_marker_makes_the_history_unknown_instead_of_dropping_the_stop(tmp_path, auto_mode, bad):
    ex, store = held_transition(tmp_path)
    set_intent_reason(store, ex.tid, bad)
    assert counted(store, tmp_path) is None
    later = Executor(catalog=signed_catalog(), catalog_sha256=DIGEST, store=store, ports=FakePorts(),
                     runtime_root=str(tmp_path / "rt2"), request=dict(request(), request_id="req-2")).run()
    assert (later["payload"]["outcome"], later["payload"]["reasons"]) == ("park", ["relaunch_history_unknown"])


def test_an_erased_intent_marker_is_the_documented_never_attempted_case_and_is_not_counted(tmp_path, auto_mode):
    # Control for the case above: no marker at all cannot be told from an attempt that crashed before stop().
    ex, store = held_transition(tmp_path)
    set_intent_reason(store, ex.tid, None)
    assert counted(store, tmp_path) == []


def test_a_forged_cancelled_flag_cannot_drop_an_applied_transition_from_the_budget(tmp_path, auto_mode):
    store = RecoveryStore(tmp_path / "journal.sqlite")
    first = Executor(catalog=signed_catalog(), catalog_sha256=DIGEST, store=store, ports=FakePorts(),
                     runtime_root=str(tmp_path / "rt"), request=request())
    assert first.run()["payload"]["outcome"] == "applied"
    with store.connect() as db:
        db.execute("UPDATE transitions SET phase='cancelled_before_apply' WHERE id=?", (first.tid,))
    assert [e["lane"] for e in counted(store, tmp_path)] == ["claude-rco-1"]
