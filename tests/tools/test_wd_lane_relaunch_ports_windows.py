# SPDX-License-Identifier: BUSL-1.1
"""Windows relaunch ports (tools/wd_lane_relaunch_ports_windows.py): fixture-only, default OFF.

Every effect is a recording fake; no real process is queried, stopped or launched.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import inspect
from pathlib import Path

import pytest

import tools.wd_lane_relaunch_executor as executor
import tools.wd_lane_relaunch_ports_windows as ports_module
from tools.bridge_v2_activation import Decision
from tools.wd_lane_relaunch_ports_windows import WindowsRelaunchPorts

ROOT = Path(__file__).resolve().parents[2]
STARTED = "2026-09-30T16:24:30.508449Z"
PID = 4242
ENABLED = Decision("F16", True, "enabled by the signed policy", "a" * 64, 3)


class Fakes:
    """Recording fakes for the gate, the one process source and the effects."""

    def __init__(self, *, decision=ENABLED, alive=True, started=STARTED, after=None, confirm=True) -> None:
        self.decision, self.alive, self.started, self.confirm = decision, alive, started, confirm
        self.after = after  # facts the source reports after terminate; None means gone
        self.calls: list[tuple] = []
        self.terminated = False

    def gate(self):
        self.calls.append(("gate",))
        if isinstance(self.decision, BaseException):
            raise self.decision
        return self.decision

    def process_facts(self, pid):
        self.calls.append(("process_facts", pid))
        if self.terminated:
            return self.after
        return {"pid": pid, "process_started_at": self.started} if self.alive else None

    def terminate(self, pid, started_at):
        self.calls.append(("terminate", pid, started_at))
        self.terminated = True

    def confirm_continuity(self, lane, epoch, checkpoint):
        self.calls.append(("confirm", lane, checkpoint))
        if isinstance(self.confirm, BaseException):
            raise self.confirm
        return self.confirm

    def ports(self, **overrides) -> WindowsRelaunchPorts:
        wiring = {"gate": self.gate, "process_facts": self.process_facts, "terminate": self.terminate,
                  "confirm_continuity": self.confirm_continuity,
                  "clock": lambda: datetime(2026, 9, 30, 17, 5, tzinfo=timezone.utc)}
        wiring.update(overrides)
        return WindowsRelaunchPorts(**wiring)

    def effects(self) -> list[tuple]:
        return [call for call in self.calls if call[0] in ("terminate", "confirm")]


def test_success_twin_stops_only_the_verified_instance():
    fakes = Fakes()
    ports = fakes.ports()
    assert ports.stop("codex-tools-1", PID, STARTED) is True
    assert fakes.effects() == [("terminate", PID, STARTED)]
    assert ports.evidence[-1] | {"observed_at_utc": None} == {
        "port": "stop", "lane": "codex-tools-1", "outcome": True, "cause": "stopped", "pid": PID,
        "observed_at_utc": None}
    assert ports.evidence[-1]["observed_at_utc"] == "2026-09-30T17:05:00+00:00"


@pytest.mark.parametrize("decision, cause", [
    (Decision("F16", False, "feature off in the signed policy"), "f0_disabled"),
    (Decision("F15", True, "enabled by the signed policy"), "f0_gate_invalid"),
    ({"feature": "F16", "enabled": True}, "f0_gate_invalid"),
    (Decision("F16", 1, "truthy is not enabled"), "f0_disabled"),
    (RuntimeError("revocation unreadable"), "f0_gate_failed:RuntimeError"),
])
def test_a_disabled_or_unreadable_f0_gate_makes_zero_effect_calls(decision, cause):
    fakes = Fakes(decision=decision)
    ports = fakes.ports()
    assert ports.stop("codex-tools-1", PID, STARTED) is False
    assert ports.resume_lane("codex-tools-1", {"pid": PID}, "checkpoint-1") is False
    assert fakes.effects() == []
    assert [entry["cause"] for entry in ports.evidence] == [cause, cause]


def test_an_unwired_gate_refuses_before_measuring():
    fakes = Fakes()
    ports = fakes.ports(gate=None)
    assert ports.stop("codex-tools-1", PID, STARTED) is False
    assert fakes.calls == [] and ports.evidence[-1]["cause"] == "f0_gate_unwired"


@pytest.mark.parametrize("fakes, pid, started, cause", [
    (Fakes(started="2026-09-30T16:24:35Z"), PID, STARTED, "process_identity_mismatch"),  # a reused pid
    (Fakes(alive=False), PID, STARTED, "process_absent"),
    (Fakes(started="yesterday"), PID, STARTED, "process_identity_mismatch"),
    (Fakes(), 0, STARTED, "target_invalid"),
    (Fakes(), True, STARTED, "target_invalid"),
    (Fakes(), PID, "2026-09-30T16:24:30", "target_invalid"),  # a naive time is not an instant
])
def test_a_reused_unknown_or_invalid_process_is_never_touched(fakes, pid, started, cause):
    ports = fakes.ports()
    assert ports.stop("codex-tools-1", pid, started) is False
    assert fakes.effects() == []
    assert ports.evidence[-1]["cause"] == cause


def test_same_source_identity_needs_the_exact_instant_rco1_n5():
    # Failure twin: a DIFFERENT process that started 1.9 s after the recorded one (inside the executor's
    # cross-source skew) is refused and never terminated.
    nearby = datetime(2026, 9, 30, 16, 24, 30, 508449, tzinfo=timezone.utc) + timedelta(seconds=1.9)
    fakes = Fakes(started=nearby.isoformat())
    ports = fakes.ports()
    assert ports.stop("codex-tools-1", PID, STARTED) is False
    assert fakes.effects() == [] and ports.evidence[-1]["cause"] == "process_identity_mismatch"
    # Success twin: the same instant in another spelling is the same process.
    exact = Fakes(started="2026-09-30T16:24:30.508449+00:00")
    assert exact.ports().stop("codex-tools-1", PID, STARTED) is True
    assert not hasattr(ports_module, "EPOCH_SKEW_SECONDS")  # no cross-source tolerance in the same-source port


def test_unwired_effects_refuse_after_verification():
    fakes = Fakes()
    ports = fakes.ports(terminate=None, confirm_continuity=None)
    assert ports.stop("codex-tools-1", PID, STARTED) is False
    assert ports.resume_lane("codex-tools-1", {}, "checkpoint-1") is False
    assert [entry["cause"] for entry in ports.evidence] == ["effect_port_unwired", "continuity_port_unwired"]


@pytest.mark.parametrize("after, outcome, cause", [
    ({"pid": PID, "process_started_at": STARTED}, False, "still_running"),
    ({"pid": PID, "process_started_at": "2026-09-30T17:00:00Z"}, True, "stopped"),  # pid now reused
    ({"pid": PID, "process_started_at": "2026-09-30T16:20:00Z"}, False, "post_stop_identity_unknown"),  # N6: older
    ({"pid": PID, "process_started_at": "2026-09-30T16:24:30.508448Z"}, False, "post_stop_identity_unknown"),
    ({"pid": PID + 1, "process_started_at": STARTED}, False, "post_stop_facts_invalid"),
    ("gone", False, "post_stop_facts_invalid"),
])
def test_stop_is_true_only_when_the_verified_instance_is_proven_gone(after, outcome, cause):
    fakes = Fakes(after=after)
    ports = fakes.ports()
    assert ports.stop("codex-tools-1", PID, STARTED) is outcome
    assert ports.evidence[-1]["cause"] == cause


def test_ordinary_failures_become_false_with_evidence_and_interrupts_propagate():
    def failing(pid, started_at):
        raise PermissionError("access denied")

    fakes = Fakes()
    ports = fakes.ports(terminate=failing)
    assert ports.stop("codex-tools-1", PID, STARTED) is False
    assert ports.evidence[-1]["cause"] == "terminate_failed:PermissionError"

    def interrupted(pid, started_at):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        fakes.ports(terminate=interrupted).stop("codex-tools-1", PID, STARTED)
    with pytest.raises(SystemExit):
        Fakes(decision=SystemExit(3)).ports().stop("codex-tools-1", PID, STARTED)


@pytest.mark.parametrize("confirm, outcome, cause", [
    (True, True, "continuity_confirmed"),
    (1, False, "continuity_not_confirmed"),
    ("yes", False, "continuity_not_confirmed"),
    (None, False, "continuity_not_confirmed"),
    (TimeoutError("no receipt"), False, "continuity_check_failed:TimeoutError"),
])
def test_resume_is_confirmed_only_by_an_exact_true(confirm, outcome, cause):
    fakes = Fakes(confirm=confirm)
    ports = fakes.ports()
    assert ports.resume_lane("codex-tools-1", {"pid": PID}, "checkpoint-1") is outcome
    assert ports.evidence[-1]["cause"] == cause


def test_the_ports_match_the_executor_protocol_signatures():
    for name in ("stop", "resume_lane"):
        expected = list(inspect.signature(getattr(executor.Ports, name)).parameters)
        assert list(inspect.signature(getattr(WindowsRelaunchPorts, name)).parameters) == expected


def test_no_default_effect_exists_and_nothing_imports_the_module():
    bare = WindowsRelaunchPorts()
    assert bare.stop("codex-tools-1", PID, STARTED) is False
    assert bare.resume_lane("codex-tools-1", {}, "c") is False
    assert [entry["cause"] for entry in bare.evidence] == ["f0_gate_unwired", "f0_gate_unwired"]
    needle = "wd_lane_relaunch_ports_windows"
    hits = []
    for base in ("ops", ".agent-bridge", "tools", "configs", "waggledance/core"):
        for path in (ROOT / base).rglob("*"):
            if path.is_file() and path.suffix in {".py", ".ps1", ".psm1", ".json", ".cmd"} \
                    and path.name != "wd_lane_relaunch_ports_windows.py":
                if needle in path.read_text(encoding="utf-8", errors="replace"):
                    hits.append(str(path.relative_to(ROOT)))
    assert hits == []


def test_evidence_keeps_the_newest_outcomes_and_counts_what_it_dropped_rco1_n7():
    ports = WindowsRelaunchPorts()
    for index in range(300):
        ports.stop("lane-" + str(index), PID, STARTED)
    assert len(ports.evidence) == ports_module.MAX_EVIDENCE == 256
    assert ports.evidence_dropped == 44
    assert ports.evidence[-1]["lane"] == "lane-299" and ports.evidence[0]["lane"] == "lane-44"