#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Windows relaunch ports (F16): the stop and resume boundary of the lane relaunch executor.

Fixture-only and default OFF. ``WindowsRelaunchPorts`` implements the executor's ``stop``
and ``resume_lane`` ports (``tools/wd_lane_relaunch_executor.Ports``; switch interface
contract section 4) around INJECTED functions. There is no default effect: without an
injected ``terminate`` or ``confirm_continuity`` nothing can be stopped or confirmed, so
nothing here can stop, launch or resume a real process. Nothing in the runtime imports it.

Ports do not decide policy; they only refuse. Before any effect ``stop``:

1. re-checks the F0 gate: the injected ``gate()`` must return an enabled F0 ``Decision`` for
   ``F16`` at that moment (a decision taken earlier is never reused);
2. re-measures the process through the ONE injected source (``process_facts(pid)``, the same
   source the executor measured ``started_at`` from) and requires the same pid and a creation
   time within the executor's own binding skew (``_same_instant``: 2.0 s). A missing, reused
   or unreadable process is refused: an unknown process tree is never touched.

The injected ``terminate(pid, started_at)`` receives the verified creation time and, in a
future production adapter (``Stop-VerifiedProcessTree``), must re-verify it through an opened
process handle before acting, closing the gap between this check and the effect.

Every ordinary failure (an exception from any injected function, a refusal, a process still
present after the effect) returns ``False`` and records its cause in ``evidence``; ``stop``
never raises such errors, as the executor requires (it calls ``stop`` without a catch).
``KeyboardInterrupt`` and ``SystemExit`` are not caught. ``resume_lane`` returns ``True``
only when the injected continuity check returns exactly ``True``; a launcher that merely
started, a truthy value or an exception is "not confirmed".
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from tools.bridge_v2_activation import Decision
from tools.lane_profile_record import _utc

FEATURE = "F16"
EPOCH_SKEW_SECONDS = 2.0  # equal to tools.wd_lane_relaunch_executor.EPOCH_SKEW_SECONDS (pinned by a test)
MAX_EVIDENCE = 256


def _same_instant(a: Any, b: Any) -> bool:
    """The executor's rule, with its parser: both times parse and agree within the skew."""
    left, right = _utc(a), _utc(b)
    return left is not None and right is not None and abs((left - right).total_seconds()) <= EPOCH_SKEW_SECONDS


class WindowsRelaunchPorts:
    """The stop/resume ports; every effect is injected and gated, none is defaulted."""

    def __init__(self, *, gate: Callable[[], Any] | None = None,
                 process_facts: Callable[[int], Any] | None = None,
                 terminate: Callable[[int, str], Any] | None = None,
                 confirm_continuity: Callable[[str, dict, str], Any] | None = None,
                 clock: Callable[[], datetime] | None = None) -> None:
        self._gate = gate
        self._process_facts = process_facts
        self._terminate = terminate
        self._confirm_continuity = confirm_continuity
        self._clock = clock
        self.evidence: list[dict] = []

    # -- evidence ---------------------------------------------------------------------------
    def _record(self, port: str, lane: Any, outcome: bool, cause: str, **facts: Any) -> bool:
        stamp = None
        if self._clock is not None:
            try:
                # Exact type, ONE offset read, subtraction: never an astimezone local-time fallback.
                moment = self._clock()
                offset = moment.utcoffset() if type(moment) is datetime else None
                if type(offset) is timedelta:
                    stamp = (moment.replace(tzinfo=None) - offset).replace(tzinfo=timezone.utc).isoformat()
            except Exception:  # noqa: BLE001 - evidence time is best effort, never a decision input
                stamp = None
        if len(self.evidence) < MAX_EVIDENCE:
            self.evidence.append({"port": port, "lane": str(lane)[:64], "outcome": outcome, "cause": cause,
                                  "observed_at_utc": stamp, **facts})
        return outcome

    # -- gate and identity ------------------------------------------------------------------
    def _gate_cause(self) -> str | None:
        """None when F16 is enabled right now; otherwise the refusal cause."""
        if self._gate is None:
            return "f0_gate_unwired"
        try:
            decision = self._gate()
        except Exception as exc:  # noqa: BLE001 - an unreadable gate is a refusal
            return "f0_gate_failed:" + type(exc).__name__
        if type(decision) is not Decision or decision.feature != FEATURE:
            return "f0_gate_invalid"
        if decision.enabled is not True:
            return "f0_disabled"
        return None

    def _identity_cause(self, pid: Any, started_at: Any) -> str | None:
        """None when the ONE measuring source still shows this exact process."""
        if type(pid) is not int or pid <= 0 or _utc(started_at) is None:
            return "target_invalid"
        if self._process_facts is None:
            return "process_source_unwired"
        try:
            facts = self._process_facts(pid)
        except Exception as exc:  # noqa: BLE001 - an unreadable process is unknown, never stopped
            return "process_query_failed:" + type(exc).__name__
        if facts is None:
            return "process_absent"
        if not isinstance(facts, dict) or facts.get("pid") != pid:
            return "process_facts_invalid"
        if not _same_instant(facts.get("process_started_at"), started_at):
            return "process_identity_mismatch"
        return None

    # -- ports ------------------------------------------------------------------------------
    def stop(self, lane: str, pid: int, started_at: str) -> bool:
        """Stop the verified source instance. True only when it is proven gone afterwards."""
        cause = self._gate_cause() or self._identity_cause(pid, started_at)
        if cause is not None:
            return self._record("stop", lane, False, cause, pid=pid if type(pid) is int else None)
        if self._terminate is None:
            return self._record("stop", lane, False, "effect_port_unwired", pid=pid)
        try:
            self._terminate(pid, started_at)
        except Exception as exc:  # noqa: BLE001 - the executor calls stop without a catch
            return self._record("stop", lane, False, "terminate_failed:" + type(exc).__name__, pid=pid)
        try:
            after = self._process_facts(pid)
        except Exception as exc:  # noqa: BLE001
            return self._record("stop", lane, False, "post_stop_query_failed:" + type(exc).__name__, pid=pid)
        if after is not None:
            if not isinstance(after, dict) or after.get("pid") != pid or _utc(after.get("process_started_at")) is None:
                return self._record("stop", lane, False, "post_stop_facts_invalid", pid=pid)
            if _same_instant(after.get("process_started_at"), started_at):
                return self._record("stop", lane, False, "still_running", pid=pid)
        # Gone, or the pid now names a different (newer) process: the verified instance is stopped.
        return self._record("stop", lane, True, "stopped", pid=pid)

    def resume_lane(self, lane: str, epoch: dict, checkpoint: str) -> bool:
        """True only on confirmed continuity for this exact epoch and checkpoint."""
        cause = self._gate_cause()
        if cause is not None:
            return self._record("resume_lane", lane, False, cause)
        if self._confirm_continuity is None:
            return self._record("resume_lane", lane, False, "continuity_port_unwired")
        try:
            confirmed = self._confirm_continuity(lane, dict(epoch) if isinstance(epoch, dict) else epoch, checkpoint)
        except Exception as exc:  # noqa: BLE001 - an exception is "not confirmed"
            return self._record("resume_lane", lane, False, "continuity_check_failed:" + type(exc).__name__)
        if confirmed is not True:
            return self._record("resume_lane", lane, False, "continuity_not_confirmed")
        return self._record("resume_lane", lane, True, "continuity_confirmed")
