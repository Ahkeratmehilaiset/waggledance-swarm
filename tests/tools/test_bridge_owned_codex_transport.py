"""Mock-protocol and injection-boundary tests for the owned app-server transport.

NOTHING HERE SPAWNS A PROCESS. The fake child is a plain Python object that
speaks the documented newline-delimited JSON protocol, so the transport's real
framing, deadline, validation and cleanup paths run while no CLI, no provider
and no model is ever reached. That is also the honest limit of this suite: it
proves protocol handling, not live control.
"""
from __future__ import annotations

import hashlib
import json
import queue
import threading

import pytest

from tools import bridge_owned_codex_transport as transport
from tools.bridge_owned_codex_transport import (
    ClientInfo,
    OwnedAppServer,
    TransportChildError,
    TransportProtocolError,
    TransportRefused,
    TransportTimeout,
)

CLIENT = ClientInfo(name="waggledance", title="WaggleDance", version="1.0.0")


class _FakeStdout:
    def __init__(self) -> None:
        self._chunks: queue.Queue = queue.Queue()
        self._buffer = b""
        self._eof = False

    def push(self, data: bytes) -> None:
        self._chunks.put(data)

    def close(self) -> None:
        self._chunks.put(None)

    def readline(self, limit: int = -1) -> bytes:
        while b"\n" not in self._buffer:
            if limit and limit > 0 and len(self._buffer) >= limit:
                head, self._buffer = self._buffer[:limit], self._buffer[limit:]
                return head
            if self._eof:
                head, self._buffer = self._buffer, b""
                return head
            chunk = self._chunks.get()
            if chunk is None:
                self._eof = True
                continue
            self._buffer += chunk
        line, _, self._buffer = self._buffer.partition(b"\n")
        if limit and limit > 0 and len(line) + 1 > limit:
            head, self._buffer = (line + b"\n")[:limit], (line + b"\n")[limit:]
            return head
        return line + b"\n"


class _FakeStdin:
    def __init__(self, child: "FakeAppServer") -> None:
        self._child = child
        self.closed = False

    def write(self, data: bytes) -> None:
        if self.closed:
            raise ValueError("write to closed stdin")
        self._child.frames.append(json.loads(data.decode("utf-8")))
        self._child.react(self._child.frames[-1])

    def flush(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class FakeAppServer:
    """A scriptable stand-in that speaks the documented protocol."""

    def __init__(self, *, behaviour: str = "normal", models: int = 2,
                 user_agent: str = "codex/1.2.3") -> None:
        self.behaviour = behaviour
        self.models = models
        self.user_agent = user_agent
        self.frames: list[dict] = []
        self.stdin = _FakeStdin(self)
        self.stdout = _FakeStdout()
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False
        self.waited = False

    # -- protocol -------------------------------------------------------
    def _emit(self, message: dict) -> None:
        self.stdout.push(json.dumps(message).encode("utf-8") + b"\n")

    def react(self, frame: dict) -> None:
        method = frame.get("method")
        request_id = frame.get("id")
        if method == "initialized":
            return                                  # notification: no response
        if self.behaviour == "silent":
            return
        if self.behaviour == "crash":
            self.returncode = 9
            self.stdout.close()
            return
        if self.behaviour == "not_json":
            self.stdout.push(b"this is not json\n")
            return
        if self.behaviour == "oversized":
            self.stdout.push(b"x" * (transport.MAX_LINE_BYTES + 64) + b"\n")
            return
        if self.behaviour == "wrong_id":
            self._emit({"id": (request_id or 0) + 99, "result": {}})
            return
        if self.behaviour == "error":
            self._emit({"id": request_id, "error": {"code": -32600, "message": "no"}})
            return
        if self.behaviour == "result_not_object":
            self._emit({"id": request_id, "result": ["nope"]})
            return
        if self.behaviour == "chatty":
            self._emit({"method": "thread/status/changed", "params": {}})
        if method == "initialize":
            self._emit({"id": request_id, "result": {
                "userAgent": self.user_agent,
                "platformFamily": "windows",
                "platformOs": "Windows 11",
            }})
        elif method == "model/list":
            self._emit({"id": request_id, "result": {"models": [
                {"id": f"model-{index}", "displayName": f"Model {index}"}
                for index in range(self.models)
            ]}})

    # -- Popen-compatible surface ---------------------------------------
    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15
        self.stdout.close()

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9
        self.stdout.close()

    def wait(self, timeout: float | None = None) -> int:
        self.waited = True
        return self.returncode if self.returncode is not None else 0


def spawner(child: FakeAppServer):
    return lambda: child


def methods_sent(child: FakeAppServer) -> list[str]:
    return [frame["method"] for frame in child.frames]


# --- the guarantee that matters most -------------------------------------


def test_the_allow_list_cannot_reach_a_model_call():
    """turn/start is the only model-execution path, and it is not reachable."""
    assert transport.ALLOWED_METHODS == {"initialize", "initialized", "model/list"}
    for forbidden in ("turn/start", "turn/steer", "thread/start", "thread/resume",
                      "thread/fork", "turn/interrupt"):
        assert forbidden not in transport.ALLOWED_METHODS


def test_a_method_outside_the_allow_list_is_refused_before_the_wire():
    child = FakeAppServer()
    with OwnedAppServer(spawner(child), client_info=CLIENT) as server:
        with pytest.raises(TransportRefused, match="allow-list"):
            server._send("turn/start", {}, request_id=1)
    assert child.frames == [], "a refused method still reached the child"


def test_params_cannot_smuggle_a_method_onto_the_wire():
    """Injection boundary: the method is the literal argument, never params."""
    child = FakeAppServer()
    with OwnedAppServer(spawner(child), client_info=CLIENT) as server:
        server.initialize()
        server.list_models(limit=1)
    for frame in child.frames:
        assert frame["method"] in transport.ALLOWED_METHODS
        params = frame.get("params", {})
        assert "method" not in params
    assert methods_sent(child) == ["initialize", "initialized", "model/list"]


def test_a_pid_is_never_accepted_as_a_child():
    with pytest.raises(TransportRefused, match="pid is never accepted"):
        OwnedAppServer(4321, client_info=CLIENT)          # type: ignore[arg-type]


# --- handshake and bounded observation ------------------------------------


def test_the_happy_path_returns_a_bounded_observation():
    child = FakeAppServer(models=3)
    result = transport.observe_owned_app_server(spawner(child), client_info=CLIENT)
    assert result["schema"] == transport.OBSERVATION_SCHEMA
    assert result["observed"] == "spawned_child_only"
    assert result["control_allowed"] is False
    assert result["server"]["platform_os"] == "Windows 11"
    assert [m["id"] for m in result["models"]] == ["model-0", "model-1", "model-2"]
    assert methods_sent(child) == ["initialize", "initialized", "model/list"]


def test_initialized_is_sent_as_a_notification_without_an_id():
    child = FakeAppServer()
    with OwnedAppServer(spawner(child), client_info=CLIENT) as server:
        server.initialize()
    notification = [f for f in child.frames if f["method"] == "initialized"][0]
    assert "id" not in notification


def test_model_list_before_the_handshake_is_refused():
    child = FakeAppServer()
    with OwnedAppServer(spawner(child), client_info=CLIENT) as server:
        with pytest.raises(TransportRefused, match="before the initialize handshake"):
            server.list_models()
    assert child.frames == []


def test_initialize_twice_is_refused():
    child = FakeAppServer()
    with OwnedAppServer(spawner(child), client_info=CLIENT) as server:
        server.initialize()
        with pytest.raises(TransportRefused, match="already completed"):
            server.initialize()


def test_a_notification_before_the_response_is_skipped_not_mistaken_for_it():
    child = FakeAppServer(behaviour="chatty")
    with OwnedAppServer(spawner(child), client_info=CLIENT) as server:
        assert server.initialize()["user_agent"] == "codex/1.2.3"


def test_an_oversized_response_field_is_truncated():
    child = FakeAppServer(user_agent="A" * 5000)
    with OwnedAppServer(spawner(child), client_info=CLIENT) as server:
        assert len(server.initialize()["user_agent"]) == 256


def test_only_bounded_fields_are_retained_so_nothing_can_leak():
    child = FakeAppServer()
    result = transport.observe_owned_app_server(spawner(child), client_info=CLIENT)
    assert set(result) == {"schema", "observed", "control_allowed", "methods_used",
                           "server", "models", "model_count_recorded"}
    assert set(result["server"]) == {"user_agent", "platform_family", "platform_os"}
    for entry in result["models"]:
        assert set(entry) == {"id", "display_name"}


# --- input validation ------------------------------------------------------


@pytest.mark.parametrize("limit", [0, -1, 101, True, "20", 1.5, None])
def test_an_invalid_limit_is_refused(limit):
    child = FakeAppServer()
    with OwnedAppServer(spawner(child), client_info=CLIENT) as server:
        server.initialize()
        before = len(child.frames)
        with pytest.raises(TransportRefused, match="limit must be"):
            server.list_models(limit=limit)
        assert len(child.frames) == before, "a refused call still reached the child"


def test_a_non_boolean_include_hidden_is_refused():
    child = FakeAppServer()
    with OwnedAppServer(spawner(child), client_info=CLIENT) as server:
        server.initialize()
        with pytest.raises(TransportRefused, match="include_hidden"):
            server.list_models(include_hidden="yes")     # type: ignore[arg-type]


@pytest.mark.parametrize("deadline", [0, -1, 1000, "20", None])
def test_an_invalid_deadline_is_refused(deadline):
    with pytest.raises(TransportRefused, match="deadline_seconds"):
        OwnedAppServer(lambda: FakeAppServer(), client_info=CLIENT,
                       deadline_seconds=deadline)        # type: ignore[arg-type]


def test_empty_or_oversized_client_info_is_refused():
    for bad in (ClientInfo(name="", title="t", version="1"),
                ClientInfo(name="n", title="t", version="v" * 200)):
        with pytest.raises(TransportRefused, match="clientInfo"):
            bad.as_params()


# --- hostile or broken child ------------------------------------------------


def test_a_mismatched_response_id_is_a_protocol_error():
    child = FakeAppServer(behaviour="wrong_id")
    with OwnedAppServer(spawner(child), client_info=CLIENT) as server:
        with pytest.raises(TransportProtocolError, match="expected"):
            server.initialize()


def test_an_error_response_is_a_protocol_error():
    child = FakeAppServer(behaviour="error")
    with OwnedAppServer(spawner(child), client_info=CLIENT) as server:
        with pytest.raises(TransportProtocolError, match="code"):
            server.initialize()


def test_a_non_json_frame_is_a_protocol_error():
    child = FakeAppServer(behaviour="not_json")
    with OwnedAppServer(spawner(child), client_info=CLIENT) as server:
        with pytest.raises(TransportProtocolError, match="not JSON"):
            server.initialize()


def test_a_non_object_result_is_a_protocol_error():
    child = FakeAppServer(behaviour="result_not_object")
    with OwnedAppServer(spawner(child), client_info=CLIENT) as server:
        with pytest.raises(TransportProtocolError, match="result is not an object"):
            server.initialize()


def test_an_oversized_frame_is_refused_rather_than_buffered():
    child = FakeAppServer(behaviour="oversized")
    with OwnedAppServer(spawner(child), client_info=CLIENT) as server:
        with pytest.raises(TransportProtocolError, match="exceeds"):
            server.initialize()


def test_a_silent_child_hits_the_deadline_instead_of_hanging():
    child = FakeAppServer(behaviour="silent")
    with OwnedAppServer(spawner(child), client_info=CLIENT,
                        deadline_seconds=0.2) as server:
        with pytest.raises(TransportTimeout, match="deadline expired"):
            server.initialize()


def test_a_crashed_child_is_reported_not_silently_retried():
    child = FakeAppServer(behaviour="crash")
    with OwnedAppServer(spawner(child), client_info=CLIENT) as server:
        with pytest.raises(TransportChildError):
            server.initialize()


def test_a_spawn_that_returns_no_stdio_is_refused():
    class NoStdio:
        stdin = None
        stdout = None

    with pytest.raises(TransportChildError, match="stdio"):
        with OwnedAppServer(lambda: NoStdio(), client_info=CLIENT):
            pass
    with pytest.raises(TransportChildError, match="stdio"):
        with OwnedAppServer(lambda: None, client_info=CLIENT):
            pass


# --- lifecycle and cleanup ---------------------------------------------------


def test_the_child_is_terminated_on_normal_exit():
    child = FakeAppServer()
    with OwnedAppServer(spawner(child), client_info=CLIENT) as server:
        server.initialize()
    assert child.stdin.closed and child.terminated and child.waited


def test_the_child_is_terminated_even_when_the_body_raises():
    """The error path is the one that leaks a child, so it is the one tested."""
    child = FakeAppServer()
    with pytest.raises(ValueError):
        with OwnedAppServer(spawner(child), client_info=CLIENT) as server:
            server.initialize()
            raise ValueError("boom")
    assert child.terminated, "a child was leaked on the error path"
    assert child.waited


def test_close_is_idempotent_and_safe_before_open():
    server = OwnedAppServer(lambda: FakeAppServer(), client_info=CLIENT)
    server.close()
    server.close()
    with pytest.raises(TransportChildError, match="not open"):
        server._assert_alive()


def test_no_reader_thread_survives_a_closed_transport():
    before = threading.active_count()
    for _ in range(3):
        child = FakeAppServer()
        transport.observe_owned_app_server(spawner(child), client_info=CLIENT)
    for _ in range(50):
        if threading.active_count() <= before:
            break
        threading.Event().wait(0.05)
    assert threading.active_count() <= before + 1, "reader threads accumulated"


# --- executable pinning --------------------------------------------------------


def test_the_executable_hash_is_verified_against_the_real_bytes(tmp_path):
    binary = tmp_path / "codex.exe"
    binary.write_bytes(b"pretend cli")
    digest = hashlib.sha256(b"pretend cli").hexdigest()
    assert transport.verify_executable(binary, digest) == digest
    assert transport.verify_executable(binary, digest.upper()) == digest


def test_a_mismatched_executable_hash_is_refused(tmp_path):
    binary = tmp_path / "codex.exe"
    binary.write_bytes(b"pretend cli")
    with pytest.raises(TransportRefused, match="does not match the pin"):
        transport.verify_executable(binary, "0" * 64)


def test_a_missing_executable_is_refused(tmp_path):
    with pytest.raises(TransportRefused, match="does not exist"):
        transport.verify_executable(tmp_path / "absent.exe", "0" * 64)


@pytest.mark.parametrize("bad", ["", "abc", "z" * 64, 12345, None])
def test_a_malformed_expected_digest_is_refused(tmp_path, bad):
    binary = tmp_path / "codex.exe"
    binary.write_bytes(b"x")
    with pytest.raises(TransportRefused, match="expected_sha256"):
        transport.verify_executable(binary, bad)          # type: ignore[arg-type]


# --- no shell, no dependencies --------------------------------------------------


def test_the_default_spawn_builds_an_argv_list_and_never_a_shell(monkeypatch):
    """Asserted without spawning: the Popen call is intercepted."""
    import subprocess

    captured: dict = {}

    class FakePopen:
        def __init__(self, argv, **kwargs):
            captured["argv"] = argv
            captured["kwargs"] = kwargs

    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    transport.default_spawn("C:/codex.exe", ["app-server"])
    assert captured["argv"] == ["C:/codex.exe", "app-server"]
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["close_fds"] is True


def test_the_module_adds_no_third_party_dependency():
    source = (transport.__file__ or "")
    assert source.endswith("bridge_owned_codex_transport.py")
    text = open(source, encoding="utf-8").read()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")) and " import " in f" {stripped} ":
            root = stripped.split()[1].split(".")[0]
            assert root in {
                "__future__", "collections", "dataclasses", "hashlib", "json",
                "pathlib", "queue", "threading", "time", "typing", "subprocess",
            }, f"unexpected dependency: {root}"
