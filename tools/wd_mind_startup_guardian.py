"""Dormant startup guardian core; no launcher, transport, or runtime installation.

An independent host must call serve(). Client detach never stops that host.
Resource adapters must be inert when supplied, and their start/status/close must
never terminate processes. This component retains references; it cannot prove
native ownership, authenticate peers, recover after its host crashes, or verify
the meaning/privacy of source references. Integration must establish those
separate boundaries. No input, result, or raw text is written to disk here.
"""
from dataclasses import dataclass
import math
import re
import threading
import time


LANES = frozenset(("codex-lead-1", "codex-tools-1", "claude-rco-1",
                   "claude-rco-2", "fable-5"))
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_NAMES = ("owner", "probe", "pipe")


def _lane(value):
    return type(value) is str and value in LANES


def _digest(value):
    return type(value) is str and _HEX.fullmatch(value) is not None


@dataclass(frozen=True)
class Resources:
    owner: object
    probe: object
    pipe: object

    def ordered(self):
        return tuple((name, getattr(self, name)) for name in _NAMES)


@dataclass(frozen=True)
class Ticket:
    lane: str
    session: str
    nonce: str
    manifest_sha256: str
    deadline: float
    query_id: str = "startup_work_overview"


def _reported(value, *, stopped=False):
    return (type(value) is dict
            and value.get("state") in (("STOPPED",) if stopped else ("READY", "RUNNING"))
            and value.get("owned") is (False if stopped else True))


def _references(value, ticket):
    keys = {"schema", "lane", "nonce", "manifest_sha256", "query_id", "status", "source_refs"}
    if (type(value) is not dict or set(value) != keys
            or any(type(value[name]) is not str for name in keys - {"source_refs"})):
        raise ValueError("guardian_result_refused")
    if (value["schema"] != "wd.mind-guardian-candidates.v1"
            or value["lane"] != ticket.lane or value["nonce"] != ticket.nonce
            or value["manifest_sha256"] != ticket.manifest_sha256
            or value["query_id"] != ticket.query_id):
        raise ValueError("guardian_result_refused")
    refs = value["source_refs"]
    if type(refs) is not list or len(refs) > 16 or not all(_digest(ref) for ref in refs):
        raise ValueError("guardian_result_refused")
    if not ((value["status"] == "ABSTAIN" and not refs)
            or (value["status"] == "CANDIDATES" and bool(refs))):
        raise ValueError("guardian_result_refused")
    # Construct a bounded reference-only copy, never retain arbitrary result fields.
    return dict(schema="wd.mind-guardian-candidates.v1", lane=ticket.lane,
                nonce=ticket.nonce, manifest_sha256=ticket.manifest_sha256,
                query_id=ticket.query_id, status=value["status"], source_refs=list(refs))


class Guardian:
    """One background operation, three retained adapters, at most five client slots.

    Deadlines bound result admission, not native execution. A timed-out callback
    retains the sole operation slot until its thread actually finishes. Adapters'
    STOPPED/owned=False reports are required in pipe -> probe -> owner order.
    """

    def __init__(self, *, enabled=False, manifest_sha256=None, resources=None,
                 query=None, clock=time.monotonic, max_budget=30.0):
        if type(enabled) is not bool:
            raise ValueError("guardian_enabled")
        if (type(max_budget) not in (int, float) or not math.isfinite(max_budget)
                or not 0 < max_budget <= 300):
            raise ValueError("guardian_budget")
        if enabled and (not _digest(manifest_sha256) or type(resources) is not Resources
                        or not callable(query)):
            raise ValueError("guardian_binding")
        self.enabled, self.manifest_sha256 = enabled, manifest_sha256
        self._resources, self._query = resources, query
        self._clock, self._max_budget = clock, max_budget
        self._lock, self._wake = threading.RLock(), threading.Event()
        self._state = "NEW" if enabled else "DISABLED"
        self._started = self._stopping = False
        self._thread = self._phase = self._active = None
        self._finished = False
        self._outcome = self._provisional = None
        self._closed, self._clients, self._slots = set(), {}, {}
        self._retry_after = 0.0

    def _now(self):
        try:
            now = self._clock()
            if type(now) not in (int, float) or not math.isfinite(now):
                raise ValueError
            return now
        except BaseException:
            raise ValueError("guardian_clock") from None

    def _expire(self, slot):
        slot["state"], slot["result"] = "EXPIRED", None

    def _begin(self, phase, work, ticket=None):
        # Caller holds the lock. Register the thread and ticket before starting.
        self._phase, self._active, self._finished = phase, ticket, False
        self._outcome = self._provisional = None

        def run():
            try:
                outcome, result = work()
            except BaseException:
                outcome, result = "HOLD", None
            with self._lock:
                self._outcome, self._provisional = outcome, result
                self._finished = True
            self._wake.set()

        self._thread = threading.Thread(target=run, name="mind-startup-guardian-" + phase,
                                        daemon=False)
        try:
            self._thread.start()
        except BaseException:
            # Uncertain start is retained. Do not infer that no work was created.
            self._state = "HOLD"

    def _start_resources(self):
        for _, resource in self._resources.ordered():
            if not _reported(resource.start()):
                return "HOLD", None
        return "READY", None

    def _execute(self, ticket):
        for _, resource in self._resources.ordered():
            if not _reported(resource.status()):
                return "HOLD", None
        return "READY", _references(self._query(self._resources, ticket), ticket)

    def _cleanup(self):
        for name, resource in reversed(self._resources.ordered()):
            if name in self._closed:
                continue
            if not _reported(resource.close(), stopped=True):
                return "HOLD", None
            with self._lock:
                self._closed.add(name)
        return "STOPPED", None

    def start(self):
        with self._lock:
            if self.enabled and not self._started and not self._stopping:
                self._started, self._state = True, "STARTING"
                self._begin("start", self._start_resources)
        return self.status()

    def attach(self, *, lane, session, expected_manifest_sha256):
        if (not _lane(lane) or not _digest(session)
                or not _digest(expected_manifest_sha256)):
            return False
        with self._lock:
            if (not self.enabled or self._stopping
                    or expected_manifest_sha256 != self.manifest_sha256):
                return False
            if self._clients.get(lane) != session and lane in self._slots:
                self._expire(self._slots[lane])
            self._clients[lane] = session
            return True

    def detach(self, *, lane, session):
        if not _lane(lane) or not _digest(session):
            return False
        with self._lock:
            if self._clients.get(lane) != session:
                return False
            del self._clients[lane]
            if lane in self._slots:
                self._expire(self._slots[lane])
            # Deliberately no stop, close, cancellation, or worker-slot release.
            return True

    def submit(self, *, lane, session, nonce, budget_seconds):
        if (not _lane(lane) or not _digest(session) or not _digest(nonce)
                or type(budget_seconds) not in (int, float)
                or not math.isfinite(budget_seconds)
                or not 0 < budget_seconds <= self._max_budget):
            return {"state": "REFUSED"}
        self.tick()
        with self._lock:
            if not self.enabled:
                return {"state": "CONTINUE_WITHOUT_MIND", "reason": "DISABLED"}
            if self._clients.get(lane) != session or self._stopping:
                return {"state": "REFUSED"}
            if self._thread is not None or self._state != "READY":
                return {"state": "CONTINUE_WITHOUT_MIND", "reason": "BUSY_OR_HOLD"}
            old = self._slots.get(lane)
            if old is not None and old["ticket"].nonce == nonce:
                return {"state": "REFUSED"}
            ticket = Ticket(lane, session, nonce, self.manifest_sha256,
                            self._now() + budget_seconds)
            self._slots[lane] = dict(ticket=ticket, state="PENDING", result=None)
            self._state = "BUSY"
            self._begin("query", lambda: self._execute(ticket), ticket)
            return {"state": "PENDING"}

    def tick(self):
        with self._lock:
            if not self.enabled:
                return
            now = self._now()
            for slot in self._slots.values():
                if slot["state"] in ("PENDING", "READY") and now >= slot["ticket"].deadline:
                    self._expire(slot)
            if (self._thread is not None and self._finished
                    and not self._thread.is_alive()):
                phase, ticket = self._phase, self._active
                outcome, result = self._outcome, self._provisional
                self._thread = self._phase = self._active = None
                self._outcome = self._provisional = None
                self._finished = False
                if phase == "query":
                    slot = self._slots[ticket.lane]
                    if (outcome == "READY" and not self._stopping
                            and slot["state"] == "PENDING"
                            and self._clients.get(ticket.lane) == ticket.session
                            and now < ticket.deadline):
                        slot["state"], slot["result"] = "READY", result
                    elif slot["state"] == "PENDING":
                        slot["state"] = "REFUSED"
                self._state = "HOLD" if self._stopping and outcome != "STOPPED" else outcome
            if (self._stopping and self._state != "STOPPED" and self._thread is None
                    and now >= self._retry_after):
                self._retry_after = now + 0.05
                self._begin("close", self._cleanup)

    def poll(self, *, lane, session, nonce):
        if not _lane(lane) or not _digest(session) or not _digest(nonce):
            return {"state": "CONTINUE_WITHOUT_MIND", "reason": "INVALID_BINDING"}
        self.tick()
        with self._lock:
            slot = self._slots.get(lane)
            if (self._clients.get(lane) != session or slot is None
                    or slot["ticket"].session != session or slot["ticket"].nonce != nonce):
                return {"state": "CONTINUE_WITHOUT_MIND", "reason": "NO_BOUND_RESULT"}
            now = self._now()
            if slot["state"] in ("PENDING", "READY") and now >= slot["ticket"].deadline:
                self._expire(slot)
            if slot["state"] == "READY" and now < slot["ticket"].deadline:
                result, slot["result"], slot["state"] = slot["result"], None, "CONSUMED"
                return {"state": "RESULT", "result": result}
            if slot["state"] == "PENDING" and self._state != "HOLD":
                return {"state": "PENDING"}
            return {"state": "CONTINUE_WITHOUT_MIND", "reason": slot["state"]}

    def request_stop(self):
        with self._lock:
            if not self.enabled:
                return self.status()
            self._stopping = True
            if self._state != "STOPPED":
                self._state = "HOLD"
            for slot in self._slots.values():
                self._expire(slot)
        self.tick()
        return self.status()

    def status(self):
        self.tick()
        with self._lock:
            state = self._state
            if (self._active is not None
                    and self._slots[self._active.lane]["state"] == "EXPIRED"):
                state = "HOLD"
            return dict(schema="wd.mind-startup-guardian-status.v1", state=state,
                        inflight=self._thread is not None,
                        retained_resources=([name for name in _NAMES if name not in self._closed]
                                            if self.enabled else []),
                        peer_authentication="UNKNOWN", guardian_crash_ownership="UNKNOWN",
                        ownership_basis="RETAINED_REFERENCES_AND_ADAPTER_REPORTS")

    def serve(self, stop_event):
        """Call in a separate long-lived host, never in a lane's startup wait."""
        self.start()
        while True:
            if stop_event.is_set():
                self.request_stop()
            state = self.status()["state"]
            if state in ("DISABLED", "STOPPED"):
                return self.status()
            self._wake.wait(0.05)
            self._wake.clear()
