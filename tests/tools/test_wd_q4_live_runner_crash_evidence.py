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


def test_once_cli_forwards_the_host_attempt_without_replacing_it(monkeypatch, capsys):
    from tools import wd_q4_live_update_once as update

    received = []
    def run(**kwargs):
        received.append(kwargs)
        return {"status": "synthetic"}
    monkeypatch.setattr(update, "run_once", run)
    assert update.main(["--once", "--config", "synthetic", "--sha256", "a" * 64,
                        "--host-attempt", "b" * 64]) == 0
    assert received[0]["host_attempt"] == "b" * 64


@pytest.mark.parametrize("damage", ["none", "missing", "wrong_attempt", "cleanup_unknown", "helper_mismatch"])
def test_job_attestation_requires_exact_attempt_and_confirmed_cleanup(tmp_path, damage):
    from tools import wd_q4_live_update_recovery as recovery
    from tools import wd_q4_live_update_once as update

    attempt, config, helper = "a" * 64, "b" * 64, "c" * 64
    intent = update._encode({"schema": "wd.q4-live-update-intent.v1", "attempt": attempt})
    intent_sha = update._sha(intent)
    marker = update._encode({"attempt": attempt, "intent_sha256": intent_sha, "config_sha256": config})
    reservation = update._encode({"schema": "wd.q4-live-runner-reservation.v1", "attempt": attempt,
                                  "token": "d" * 64, "intent_sha256": intent_sha})
    (tmp_path / ("q4-live-runner-reservation-" + attempt + ".json")).write_bytes(reservation)
    record = {"schema": "wd.q4-live-job-result.v1", "attempt": attempt, "config_sha256": config,
              "intent_sha256": intent_sha, "marker_sha256": update._sha(marker),
              "reservation_sha256": update._sha(reservation), "job_helper_sha256": helper,
              "cleanup": "confirmed", "child_outcome": "completed", "exit_code": 3}
    if damage == "wrong_attempt": record["attempt"] = "e" * 64
    if damage == "cleanup_unknown": record["cleanup"] = "unknown"
    if damage == "helper_mismatch": record["job_helper_sha256"] = "f" * 64
    raw = update._encode(record)
    if damage != "missing":
        (tmp_path / ("q4-live-job-result-" + attempt + ".json")).write_bytes(raw)
    args = (tmp_path, attempt, config, intent_sha, marker, update._sha(raw), helper)
    if damage == "none":
        assert recovery._verify_job_attestation(*args) == json.loads(reservation)
    else:
        with pytest.raises(recovery.RecoveryOutcomeUnknown):
            recovery._verify_job_attestation(*args)
