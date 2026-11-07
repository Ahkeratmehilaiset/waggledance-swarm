"""Synthetic phase diagnostics only: no runtime, model, guard or transport execution."""
import importlib
import json
import math
import os
import sys
from types import SimpleNamespace

import pytest


SECRET = "synthetic-owner-token-query-answer-private-path"
KEYS = {"schema", "phase", "caller_exit_code", "reported_status", "reported_cleanup", "failure_kind",
        "elapsed_ms", "phase_elapsed_ms"}
PHASES = {
    "UNKNOWN", "CONFIG_READ", "INPUT_VALIDATION", "RUNTIME_VERIFICATION",
    "INPUTS_PINNED", "CAPTURE_MAPPING", "OUTPUT_CREATED", "STAGED",
    "GUARD_PROVISIONING", "CALLER_ENTERED", "CALLER_RETURNED",
    "RESULT_VALIDATION", "TARGET_VALIDATION", "RESULT_WRITE",
}
KINDS = {"UNKNOWN", "REFUSED", "TIMEOUT", "INTERRUPTED", "IO_ERROR", "NONE"}
STAGED = {"descriptor_path": "synthetic-descriptor", "descriptor_sha256": "a" * 64}


def module():
    return importlib.import_module("tools.wd_q4_p10_guarded_connector")


def assert_closed(receipt):
    assert set(receipt) == KEYS
    assert receipt["schema"] == "wd.q4-p10-phase-receipt.v1"
    assert receipt["phase"] in PHASES
    assert receipt["failure_kind"] in KINDS
    assert receipt["caller_exit_code"] is None or (
        type(receipt["caller_exit_code"]) is int
        and receipt["caller_exit_code"] in {0, 2, 3, 4, 130}
    )
    assert receipt["reported_status"] in {"UNKNOWN", "OFF", "REFUSED", "VALIDATED"}
    assert receipt["reported_cleanup"] in {"UNKNOWN", "unknown", "chain_confirmed"}
    for key in ("elapsed_ms", "phase_elapsed_ms"):
        assert receipt[key] is None or (type(receipt[key]) in (int, float)
                                        and math.isfinite(receipt[key]) and receipt[key] >= 0)
    encoded = json.dumps(receipt, sort_keys=True)
    assert SECRET not in encoded
    assert len(encoded.encode("ascii")) <= 1024


def synthetic_run(monkeypatch, caller):
    m = module()

    def run(_config, *, _diagnostic):
        return m._invoke_guarded_caller(
            caller, STAGED, "b" * 64, "frozen", diagnostic=_diagnostic
        )

    monkeypatch.setattr(m, "_run", run)
    return m


@pytest.mark.parametrize("code", [2, 3, 4, 130, True, 999, "2", None])
def test_nonzero_or_unrecognized_caller_code_has_closed_phase_receipt(monkeypatch, code):
    entered = []

    def caller(argv):
        entered.append(argv)
        sys.stdout.write(SECRET)
        sys.stderr.write(SECRET)
        return code

    m = synthetic_run(monkeypatch, SimpleNamespace(main=caller))
    before = (sys.stdin, sys.stdout, sys.stderr)
    with pytest.raises(m.ConnectorOutcomeUnknown, match="^p10_connector_state_unknown$") as error:
        m.run_guarded_candidate({"approved_to_execute": True})
    assert entered == [["--enabled"]]
    receipt = error.value.diagnostic
    assert_closed(receipt)
    assert receipt["phase"] == "CALLER_RETURNED"
    assert receipt["caller_exit_code"] == (
        code if type(code) is int and code in {2, 3, 4, 130} else None
    )
    assert receipt["reported_status"] == receipt["reported_cleanup"] == "UNKNOWN"
    assert (sys.stdin, sys.stdout, sys.stderr) == before


@pytest.mark.parametrize(
    "kind, expected",
    [(ValueError, "UNKNOWN"), (TimeoutError, "TIMEOUT"),
     (KeyboardInterrupt, "INTERRUPTED"), (SystemExit, "INTERRUPTED")],
)
def test_caller_exception_keeps_entered_phase_and_redacts_exception_text(monkeypatch, kind, expected):
    def caller(_argv):
        raise kind(SECRET)

    m = synthetic_run(monkeypatch, SimpleNamespace(main=caller))
    before = (sys.stdin, sys.stdout, sys.stderr)
    with pytest.raises(m.ConnectorOutcomeUnknown) as error:
        m.run_guarded_candidate({"approved_to_execute": True})
    assert_closed(error.value.diagnostic)
    assert error.value.diagnostic["phase"] == "CALLER_ENTERED"
    assert error.value.diagnostic["failure_kind"] == expected
    assert SECRET not in str(error.value)
    assert error.value.__context__ is None
    assert (sys.stdin, sys.stdout, sys.stderr) == before


@pytest.mark.parametrize(
    "stdout, stderr",
    [(SECRET, ""), ("x" * 65537, ""), ("{}", SECRET),
     ('{"status":"REFUSED","status":"' + SECRET + '"}', "")],
    ids=["malformed-json", "oversized", "stderr", "duplicate-json-key"],
)
def test_zero_exit_invalid_output_stays_failed_with_returned_phase(monkeypatch, stdout, stderr):
    def caller(_argv):
        sys.stdout.write(stdout)
        sys.stderr.write(stderr)
        return 0

    m = synthetic_run(monkeypatch, SimpleNamespace(main=caller))
    with pytest.raises(m.ConnectorOutcomeUnknown) as error:
        m.run_guarded_candidate({"approved_to_execute": True})
    assert_closed(error.value.diagnostic)
    assert error.value.diagnostic["phase"] == "CALLER_RETURNED"
    assert error.value.diagnostic["caller_exit_code"] == 0


@pytest.mark.parametrize("phase", [
    "INPUT_VALIDATION", "RUNTIME_VERIFICATION", "INPUTS_PINNED", "CAPTURE_MAPPING",
    "OUTPUT_CREATED", "STAGED", "GUARD_PROVISIONING", "RESULT_VALIDATION",
    "TARGET_VALIDATION", "RESULT_WRITE",
])
def test_noncaller_failure_keeps_last_closed_phase_and_restores_process_state(monkeypatch, phase):
    m = module()
    monkeypatch.setenv("WD_SYNTHETIC_PHASE_TEST", "before")
    before_env, before_path = dict(os.environ), list(sys.path)

    def run(_config, *, _diagnostic):
        _diagnostic["phase"] = phase
        os.environ["WD_SYNTHETIC_PHASE_TEST"] = SECRET
        sys.path.append(SECRET)
        raise OSError(SECRET)

    monkeypatch.setattr(m, "_run", run)
    with pytest.raises(m.ConnectorOutcomeUnknown) as error:
        m.run_guarded_candidate({"approved_to_execute": True})
    assert_closed(error.value.diagnostic)
    assert error.value.diagnostic["phase"] == phase
    assert error.value.diagnostic["failure_kind"] == "IO_ERROR"
    assert dict(os.environ) == before_env
    assert sys.path == before_path


def test_unrecognized_phase_and_reported_fields_are_unknown_not_echoed(monkeypatch):
    m = module()

    def run(_config, *, _diagnostic):
        _diagnostic.update(phase=SECRET, caller_exit_code=True,
                           reported_status=SECRET, reported_cleanup=SECRET,
                           unexpected=SECRET)
        raise ValueError(SECRET)

    monkeypatch.setattr(m, "_run", run)
    with pytest.raises(m.ConnectorOutcomeUnknown) as error:
        m.run_guarded_candidate({"approved_to_execute": True})
    assert_closed(error.value.diagnostic)
    assert error.value.diagnostic["phase"] == "UNKNOWN"
    assert error.value.diagnostic["caller_exit_code"] is None
    assert error.value.diagnostic["reported_status"] == "UNKNOWN"
    assert error.value.diagnostic["reported_cleanup"] == "UNKNOWN"


def test_cli_failure_publishes_only_closed_phase_receipt(monkeypatch, capsys):
    def caller(_argv):
        sys.stdout.write(SECRET)
        sys.stderr.write(SECRET)
        return 3

    m = synthetic_run(monkeypatch, SimpleNamespace(main=caller))
    config_bytes = b'{"approved_to_execute":true}'
    monkeypatch.setattr(m, "_path", lambda _value: SimpleNamespace(
        stat=lambda: SimpleNamespace(st_size=len(config_bytes))
    ))
    monkeypatch.setattr(m, "_read", lambda _descriptor, _maximum: config_bytes)
    code = m.main(["--config", SECRET, "a" * 64])
    captured = capsys.readouterr()
    assert code == 2
    assert not captured.err
    assert SECRET not in captured.out
    document = json.loads(captured.out)
    assert set(document) == {"status", "authority", "diagnostic"}
    assert document["status"] == "CONNECTOR_NOT_COMPLETED"
    assert document["authority"] == "none"
    assert_closed(document["diagnostic"])
    assert document["diagnostic"]["phase"] == "CALLER_RETURNED"
    assert document["diagnostic"]["caller_exit_code"] == 3


def test_cli_argument_refusal_never_echoes_argv_or_claims_query_execution(capsys):
    m = module()
    assert m.main([SECRET]) == 2
    captured = capsys.readouterr()
    assert not captured.err
    assert SECRET not in captured.out
    document = json.loads(captured.out)
    assert document["status"] == "CONNECTOR_NOT_COMPLETED"
    assert_closed(document["diagnostic"])
    assert document["diagnostic"]["phase"] == "CONFIG_READ"
    assert document["diagnostic"]["caller_exit_code"] is None


def test_admission_and_current_phase_elapsed_time_are_observed_not_inferred(monkeypatch):
    m = module()
    now = [10.0]
    monkeypatch.setattr(m, "_monotonic", lambda: now[0], raising=False)

    def run(_config, *, _diagnostic):
        now[0] = 10.25
        _diagnostic["phase"] = "STAGED"
        now[0] = 11.0
        raise RuntimeError(SECRET)

    monkeypatch.setattr(m, "_run", run)
    with pytest.raises(m.ConnectorOutcomeUnknown) as error:
        m.run_guarded_candidate({"approved_to_execute": True})
    assert_closed(error.value.diagnostic)
    assert error.value.diagnostic["elapsed_ms"] == 1000.0
    assert error.value.diagnostic["phase_elapsed_ms"] == 750.0


def test_unavailable_timing_is_unknown_without_losing_the_failure(monkeypatch):
    m = module()
    monkeypatch.setattr(m, "_monotonic", lambda: float("nan"), raising=False)

    def run(_config, *, _diagnostic):
        _diagnostic["phase"] = "INPUTS_PINNED"
        raise RuntimeError(SECRET)

    monkeypatch.setattr(m, "_run", run)
    with pytest.raises(m.ConnectorOutcomeUnknown) as error:
        m.run_guarded_candidate({"approved_to_execute": True})
    assert_closed(error.value.diagnostic)
    assert error.value.diagnostic["elapsed_ms"] is None
    assert error.value.diagnostic["phase_elapsed_ms"] is None

