"""Synthetic guardian unit tests and a separate Windows host/client process test."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

import pytest
from tools import wd_mind_startup_guardian as module
from tools.wd_mind_startup_guardian import Guardian, Resources


MANIFEST, SESSION, NONCE = "a" * 64, "b" * 64, "c" * 64
LANE = "codex-tools-1"


class Clock:
    def __init__(self):
        self.now = 10.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class Resource:
    def __init__(self, name, log):
        self.name, self.log = name, log
        self.close_gate = threading.Event()
        self.close_gate.set()
        self.start_status = dict(state="READY", owned=True)

    def start(self):
        self.log.append(("start", self.name))
        return self.start_status

    def status(self):
        return dict(state="READY", owned=True)

    def close(self):
        self.log.append(("close", self.name))
        if not self.close_gate.is_set():
            return dict(state="HOLD", owned=None)
        return dict(state="STOPPED", owned=False)


def result(ticket):
    return dict(schema="wd.mind-guardian-candidates.v1", lane=ticket.lane,
                nonce=ticket.nonce, manifest_sha256=ticket.manifest_sha256,
                query_id=ticket.query_id, status="CANDIDATES", source_refs=["d" * 64])


def until(guardian, state):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if guardian.status()["state"] == state and not guardian.status()["inflight"]:
            return
        time.sleep(0.001)
    pytest.fail("Synthetic operation did not reach " + state)


@pytest.fixture
def make():
    created = []

    def factory(*, query=None, enabled=True, start=True):
        clock, log = Clock(), []
        resources = Resources(*(Resource(name, log) for name in ("owner", "probe", "pipe")))
        guardian = Guardian(enabled=enabled, manifest_sha256=MANIFEST, resources=resources,
                            query=query or (lambda resources, ticket: result(ticket)), clock=clock)
        created.append((guardian, resources, clock))
        assert guardian.attach(lane=LANE, session=SESSION,
                               expected_manifest_sha256=MANIFEST) is enabled
        if start:
            guardian.start()
            if enabled:
                until(guardian, "READY")
        return guardian, resources, clock, log

    yield factory
    for guardian, resources, clock in created:
        for _, resource in resources.ordered():
            resource.close_gate.set()
        guardian.request_stop()
        deadline = time.monotonic() + 6
        while guardian.status()["state"] not in ("STOPPED", "DISABLED"):
            assert time.monotonic() < deadline, "Retained synthetic callback still running; no kill"
            clock.advance(0.1)
            time.sleep(0.001)


def submit(guardian, nonce=NONCE, budget=1):
    return guardian.submit(lane=LANE, session=SESSION, nonce=nonce, budget_seconds=budget)


def poll(guardian, nonce=NONCE, session=SESSION):
    return guardian.poll(lane=LANE, session=session, nonce=nonce)


def test_disabled_is_inert(make):
    guardian, _, _, log = make(enabled=False)
    assert guardian.serve(threading.Event())["state"] == "DISABLED"
    assert submit(guardian)["reason"] == "DISABLED"
    assert log == []


def test_start_is_once_and_never_claims_authentication_or_crash_ownership(make):
    guardian, _, _, log = make()
    for _ in range(3):
        guardian.start()
    assert log == [("start", "owner"), ("start", "probe"), ("start", "pipe")]
    assert guardian.status()["peer_authentication"] == "UNKNOWN"
    assert guardian.status()["guardian_crash_ownership"] == "UNKNOWN"


@pytest.mark.parametrize("options", [
    dict(enabled=1), dict(max_budget=True), dict(max_budget=float("inf")),
    dict(max_budget=0), dict(enabled=True),
])
def test_invalid_configuration_refuses(options):
    with pytest.raises(ValueError):
        Guardian(**options)


def test_bindings_and_bounded_budget_are_checked(make):
    guardian, _, _, _ = make()
    assert not guardian.attach(lane=LANE, session="e" * 64,
                               expected_manifest_sha256="f" * 64)
    assert not guardian.attach(lane=[], session=SESSION, expected_manifest_sha256=MANIFEST)
    for budget in (True, 0, -1, float("nan"), 301):
        assert submit(guardian, budget=budget)["state"] == "REFUSED"
    assert guardian.submit(lane=LANE, session=SESSION, nonce="raw text",
                           budget_seconds=1)["state"] == "REFUSED"


def test_single_operation_remains_owned_after_deadline(make):
    entered, release, calls = threading.Event(), threading.Event(), []

    def query(resources, ticket):
        calls.append(ticket)
        entered.set()
        assert release.wait(5)
        return result(ticket)

    guardian, resources, clock, _ = make(query=query)
    try:
        assert submit(guardian)["state"] == "PENDING"
        assert entered.wait(2)
        clock.advance(2)
        assert poll(guardian)["state"] == "CONTINUE_WITHOUT_MIND"
        status = guardian.status()
        assert status["state"] == "HOLD" and status["inflight"]
        assert status["retained_resources"] == ["owner", "probe", "pipe"]
        assert guardian._resources is resources
        assert submit(guardian, "e" * 64)["reason"] == "BUSY_OR_HOLD"
        assert len(calls) == 1
    finally:
        release.set()
    until(guardian, "READY")
    assert poll(guardian)["state"] == "CONTINUE_WITHOUT_MIND"


def test_result_is_consumed_once_and_rechecked_at_client_deadline(make):
    guardian, _, clock, _ = make()
    submit(guardian)
    until(guardian, "READY")
    assert poll(guardian)["state"] == "RESULT"
    assert poll(guardian)["state"] == "CONTINUE_WITHOUT_MIND"
    assert submit(guardian)["state"] == "REFUSED"
    submit(guardian, "e" * 64)
    until(guardian, "READY")
    clock.advance(1)
    assert poll(guardian, "e" * 64)["state"] == "CONTINUE_WITHOUT_MIND"


def test_callback_finishing_late_never_becomes_a_result(make):
    entered, release = threading.Event(), threading.Event()

    def query(resources, ticket):
        entered.set()
        assert release.wait(5)
        return result(ticket)

    guardian, _, clock, _ = make(query=query)
    try:
        submit(guardian)
        assert entered.wait(2)
        clock.advance(2)  # No intervening poll is needed for late rejection.
    finally:
        release.set()
    until(guardian, "READY")
    assert poll(guardian)["state"] == "CONTINUE_WITHOUT_MIND"
    assert guardian._provisional is None


def test_launcher_replacement_does_not_close_or_duplicate_live_work(make):
    entered, release = threading.Event(), threading.Event()

    def query(resources, ticket):
        entered.set()
        assert release.wait(5)
        return result(ticket)

    guardian, resources, _, log = make(query=query)
    try:
        submit(guardian)
        assert entered.wait(2)
        assert guardian.detach(lane=LANE, session=SESSION)
        assert guardian.attach(lane=LANE, session="e" * 64,
                               expected_manifest_sha256=MANIFEST)
        assert guardian._resources is resources
        assert guardian.status()["inflight"]
        assert not any(action == "close" for action, _ in log)
        assert poll(guardian, session="e" * 64)["state"] == "CONTINUE_WITHOUT_MIND"
    finally:
        release.set()
    until(guardian, "READY")
    assert poll(guardian, session="e" * 64)["state"] == "CONTINUE_WITHOUT_MIND"
    assert len([row for row in log if row[0] == "start"]) == 3


def test_stop_waits_for_active_query_without_closing_resources(make):
    entered, release = threading.Event(), threading.Event()

    def query(resources, ticket):
        entered.set()
        assert release.wait(5)
        return result(ticket)

    guardian, _, clock, log = make(query=query)
    try:
        submit(guardian)
        assert entered.wait(2)
        guardian.request_stop()
        assert guardian.status()["state"] == "HOLD"
        assert not any(action == "close" for action, _ in log)
    finally:
        release.set()
    clock.advance(1)
    until(guardian, "STOPPED")
    assert [name for action, name in log if action == "close"] == ["pipe", "probe", "owner"]
    assert poll(guardian)["state"] == "CONTINUE_WITHOUT_MIND"


def test_uncertain_pipe_close_retains_probe_and_owner(make):
    guardian, resources, clock, log = make()
    resources.pipe.close_gate.clear()
    guardian.request_stop()
    until(guardian, "HOLD")
    assert guardian.status()["retained_resources"] == ["owner", "probe", "pipe"]
    assert not any(action == "close" and name != "pipe" for action, name in log)
    resources.pipe.close_gate.set()
    clock.advance(1)
    until(guardian, "STOPPED")
    assert guardian.status()["retained_resources"] == []


def test_partial_start_remains_held_until_explicit_cleanup(make):
    guardian, resources, _, log = make(start=False)
    resources.probe.start_status = dict(state="READY", owned=None)
    guardian.start()
    until(guardian, "HOLD")
    assert guardian.status()["retained_resources"] == ["owner", "probe", "pipe"]
    assert ("start", "pipe") not in log
    assert submit(guardian)["state"] == "CONTINUE_WITHOUT_MIND"


@pytest.mark.parametrize("bad", ["raw", "nonce", "manifest", "many", "owned_object"])
def test_untrusted_results_are_reference_only_and_fail_closed(make, bad):
    def query(resources, ticket):
        value = result(ticket)
        if bad == "raw":
            value["text"] = "synthetic-private-payload"
        elif bad == "nonce":
            value["nonce"] = "e" * 64
        elif bad == "manifest":
            value["manifest_sha256"] = "e" * 64
        elif bad == "many":
            value["source_refs"] = ["d" * 64] * 17
        else:
            value["status"] = object()
        return value

    guardian, _, _, _ = make(query=query)
    submit(guardian)
    until(guardian, "HOLD")
    assert poll(guardian)["state"] == "CONTINUE_WITHOUT_MIND"
    assert guardian._provisional is None
    assert guardian._slots[LANE]["result"] is None


_HOST = r'''
import importlib.util, json, os, sys, threading, time
from multiprocessing.connection import Listener
spec = importlib.util.spec_from_file_location("test_guardian_host", sys.argv[1])
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)
counts = []
class Resource:
    def start(self):
        counts.append(1)
        return {"state":"READY","owned":True}
    def status(self): return {"state":"READY","owned":True}
    def close(self): return {"state":"STOPPED","owned":False}
resources = m.Resources(Resource(), Resource(), Resource())
g = m.Guardian(enabled=True, manifest_sha256="a"*64, resources=resources,
               query=lambda resources, ticket: {})
stop = threading.Event()
server = threading.Thread(target=g.serve, args=(stop,), daemon=False)
server.start()
deadline = time.monotonic()+5
while g.status()["state"] != "READY":
    if time.monotonic() >= deadline: raise SystemExit(3)
    time.sleep(.001)
with Listener(sys.argv[2], family="AF_PIPE", authkey=None) as listener:
    print(json.dumps({"state":"LISTENING","pid":os.getpid()}), flush=True)
    while True:
        with listener.accept() as conn:
            try:
                command = conn.recv_bytes(32)
            except EOFError:
                continue  # Launcher EOF is not the host stop event.
            if command == b"launcher_one":
                assert g.attach(lane="codex-tools-1",session="b"*64,expected_manifest_sha256="a"*64)
                assert g.detach(lane="codex-tools-1",session="b"*64)
            elif command == b"launcher_two":
                assert g.attach(lane="codex-tools-1",session="c"*64,expected_manifest_sha256="a"*64)
            elif command == b"stop":
                stop.set()
                server.join(5)
                if server.is_alive(): raise SystemExit(4)
            else:
                raise SystemExit(5)
            conn.send_bytes(json.dumps({"pid":os.getpid(),"starts":len(counts),
                                       "status":g.status()}).encode())
            if command == b"stop": break
server.join(5)
raise SystemExit(0 if not server.is_alive() else 6)
'''

_CLIENT = r'''
import sys
from multiprocessing.connection import Client
with Client(sys.argv[1], family="AF_PIPE", authkey=None) as conn:
    conn.send_bytes(sys.argv[2].encode("ascii"))
    sys.stdout.buffer.write(conn.recv_bytes(8192))
'''


@pytest.mark.skipif(os.name != "nt", reason="Synthetic Windows AF_PIPE host")
def test_separate_guardian_host_survives_two_launcher_process_exits():
    # Test-only unauthenticated reference/metadata IPC, not a production transport.
    from multiprocessing.connection import Client
    endpoint = r"\\.\pipe\wd-guardian-test-" + uuid.uuid4().hex
    host = subprocess.Popen([sys.executable, "-I", "-u", "-c", _HOST,
                             str(Path(module.__file__).resolve()), endpoint],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
    ready = []
    reader = threading.Thread(target=lambda: ready.append(host.stdout.readline()), daemon=True)
    reader.start()
    try:
        reader.join(10)
        assert not reader.is_alive() and ready
        assert json.loads(ready[0])["state"] == "LISTENING"
        for command in ("launcher_one", "launcher_two"):
            client = subprocess.Popen([sys.executable, "-I", "-c", _CLIENT, endpoint, command],
                                      stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            output, error = client.communicate(timeout=10)  # Does not kill on timeout.
            assert client.returncode == 0, error
            row = json.loads(output)
            assert row["pid"] == host.pid and row["starts"] == 3
            assert row["status"]["state"] == "READY" and host.poll() is None
    finally:
        if host.poll() is None and ready:
            with Client(endpoint, family="AF_PIPE", authkey=None) as conn:
                conn.send_bytes(b"stop")
                assert json.loads(conn.recv_bytes(8192))["status"]["state"] == "STOPPED"
        assert host.wait(timeout=10) == 0  # No terminate/kill fallback.
        host.stdout.close()
        host.stderr.close()
