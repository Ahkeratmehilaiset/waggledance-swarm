"""Synthetic contract for associating interrupted runner reservations with an outer attempt.

The outer owner must persist its attempt and a fresh, private token before invoking
the runner. This binding is evidence of reservation ownership, not evidence that
the process tree stopped: recovery must still require an independent cleanup receipt.
"""
import importlib.util
import json
from pathlib import Path

import pytest


_path = Path(__file__).with_name("test_wd_q4_status_prefix_runner.py")
_spec = importlib.util.spec_from_file_location("q4_crash_synthetic_fixture", _path)
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)
runner = _fixture.runner
case = _fixture.case


def test_interrupted_runner_preserves_outer_attempt_and_token_in_both_markers(case, monkeypatch):
    """Proposed opt-in recovery_reservation preserves an exact, already-persisted binding."""
    worker, args, _, checkpoint, private, _ = case
    reservation = {"attempt": "a" * 64, "token": "b" * 64}
    seen = []

    def interrupted_child(**_kwargs):
        seen.append(True)
        raise KeyboardInterrupt()

    _fixture.fake_child(monkeypatch, interrupted_child)
    with pytest.raises(worker.StatusRunnerInterrupted):
        worker.run_once(**args(), renewable=True, recovery_reservation=reservation)

    assert seen == [True]
    for root in (checkpoint, private):
        assert json.loads((root / worker._PENDING).read_bytes()) == {
            "schema": "wd.q4-runner-reservation.v1", **reservation,
        }


@pytest.mark.parametrize("reservation", [
    {"attempt": "a" * 64},
    {"attempt": "a" * 64, "token": "not-a-sha256"},
    {"attempt": "a" * 64, "token": "b" * 64, "extra": "c"},
])
def test_bad_outer_reservation_refuses_before_creating_markers(case, monkeypatch, reservation):
    worker, args, _, checkpoint, private, _ = case
    _fixture.fake_child(monkeypatch, lambda **_kwargs: pytest.fail("child was launched"))
    with pytest.raises(worker.StatusRunnerRefused):
        worker.run_once(**args(), renewable=True, recovery_reservation=reservation)
    assert not (checkpoint / worker._PENDING).exists()
    assert not (private / worker._PENDING).exists()


def test_outer_owner_persists_binding_before_runner_can_receive_it(tmp_path, monkeypatch):
    from tools import wd_q4_live_update_once as update

    monkeypatch.setattr(update.secrets, "token_hex", lambda size: "b" * (size * 2))
    attempt, intent_sha = "a" * 64, "c" * 64
    reservation = update._create_recovery_reservation(tmp_path, attempt, intent_sha)
    record = json.loads((tmp_path / ("q4-live-runner-reservation-" + attempt + ".json")).read_bytes())
    assert reservation == {"attempt": attempt, "token": "b" * 64}
    assert record == {"schema": "wd.q4-live-runner-reservation.v1", **reservation, "intent_sha256": intent_sha}
    with pytest.raises(FileExistsError):
        update._create_recovery_reservation(tmp_path, attempt, intent_sha)
