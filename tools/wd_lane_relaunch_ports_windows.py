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
   source the executor measured ``started_at`` from) and requires the same pid and EXACTLY the
   same creation instant (``_same_source_instant``; RCO1 N5: the executor's 2.0 s skew is for
   different sources and would accept a different process started nearby). A missing, reused
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

from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from tools.bridge_v2_activation import Decision
from tools.lane_profile_record import _utc

FEATURE = "F16"
MAX_EVIDENCE = 256


def _same_source_instant(a: Any, b: Any) -> bool:
    """Both creation times parse and are the SAME instant (RCO1 N5, the #1751 same-source lesson).

    ``started_at`` and ``process_facts`` come from one measuring source, so no tolerance applies: the
    executor's 2.0 s skew is for comparing DIFFERENT sources, and here it would accept another process
    that started within two seconds of the recorded one."""
    left, right = _utc(a), _utc(b)
    return left is not None and right is not None and left == right


_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259
_ERROR_INVALID_PARAMETER = 87
_FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


def windows_process_facts(pid: int) -> dict | None:
    """READ-ONLY facts of one live process from ONE kernel source: ``{pid, process_started_at, source}``.

    The creation instant comes from GetProcessTimes (100 ns FILETIME, reported in whole microseconds, so the
    same process always yields the same string). A process that does not exist, or has exited, is None. Any
    other failure to read it (for example access denied) raises OSError: unknown, never "absent". It opens
    the process for limited query only and closes the handle; it never signals, suspends or stops anything.
    The executor's ``processes``/``measure`` ports must use this same source for the exact-instant identity."""
    if type(pid) is not int or not 0 < pid <= 0xFFFFFFFF:
        raise ValueError("a process id must be a positive 32-bit integer")
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error == _ERROR_INVALID_PARAMETER:
            return None   # no such process
        raise OSError(error, "OpenProcess failed; the process facts are unknown")
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            raise OSError(ctypes.get_last_error(), "GetExitCodeProcess failed; the process facts are unknown")
        if code.value != _STILL_ACTIVE:
            return None   # exited: its times still exist, but it is not a live process
        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel),
                                        ctypes.byref(user)):
            raise OSError(ctypes.get_last_error(), "GetProcessTimes failed; the process facts are unknown")
    finally:
        kernel32.CloseHandle(handle)
    ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
    started = _FILETIME_EPOCH + timedelta(microseconds=ticks // 10)
    return {"pid": pid, "process_started_at": started.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "source": "GetProcessTimes"}


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
        # A ring of the newest outcomes; the count of older entries it dropped is kept (RCO1 N7).
        self.evidence: deque[dict] = deque(maxlen=MAX_EVIDENCE)
        self.evidence_dropped = 0

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
        if len(self.evidence) == MAX_EVIDENCE:
            self.evidence_dropped += 1
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
        if not _same_source_instant(facts.get("process_started_at"), started_at):
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
            if _same_source_instant(after.get("process_started_at"), started_at):
                return self._record("stop", lane, False, "still_running", pid=pid)
            # Only a STRICTLY LATER start means the pid was reused by a new process (RCO1 N6); an older start
            # from the same source is inconsistent, so the verified instance is not proven gone.
            if not _utc(after.get("process_started_at")) > _utc(started_at):
                return self._record("stop", lane, False, "post_stop_identity_unknown", pid=pid)
        # Gone, or the pid now names a strictly newer process: the verified instance is stopped.
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
