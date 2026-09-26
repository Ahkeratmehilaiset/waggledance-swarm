#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Bounded read-only observation of an app-server child THIS process spawned.

Why a transport at all, when a descriptor validator already exists: a validator
can only judge a claim someone else made. This module makes the observation
itself, and it can do so honestly for exactly one reason -- it owns the child.

WHAT THE PROTOCOL ACTUALLY ALLOWS. Per the official app-server documentation,
a client spawns ``codex app-server`` as its own child and speaks
newline-delimited JSON over that child's stdio. There is **no mechanism to
attach to, or observe, an app-server instance launched by anyone else**. So the
scope of any owned observation is structurally bounded: it describes the child
we started, and it can never describe a peer lane's live session. Anything that
claims otherwise is claiming something the protocol does not offer.

WHAT THIS MODULE REFUSES TO DO, by construction rather than by convention:

* It never attaches to an existing process. There is no pid parameter and no
  way to hand it a running child; it calls an injected ``spawn`` callable and
  owns whatever that returns, exclusively, for the lifetime of the context.
* It never sends a method outside :data:`ALLOWED_METHODS`. The allow-list is
  checked against the literal method name at the single send site, so a method
  smuggled through params cannot reach the wire.
* It never starts a turn, resumes a thread, changes a model, or spends a token.
  ``turn/start`` is the only model-execution path in the protocol and it is not
  in the allow-list, so this transport cannot cause a model call.
* It never runs a shell. The default spawn builds an argv list for
  ``subprocess.Popen`` with ``shell=False``; no string is ever handed to a shell.
* It never logs credentials. Nothing from the environment is recorded, and the
  only response content it retains is the bounded model summary below.

WHAT IT CANNOT PROVE. Mock evidence proves protocol handling, not live control.
Until a real cold start has been observed and accepted, nothing here shows that
a spawned child would behave as these tests do.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import queue
import threading
import time
from typing import Any
import weakref

#: The complete set of methods this transport may ever send. Read-only and
#: pre-turn by design: initialize/initialized are the mandatory handshake, and
#: model/list is discovery. Nothing here can start work or spend a token.
ALLOWED_METHODS = frozenset({"initialize", "initialized", "model/list"})

#: Methods that are notifications, so no response is expected or awaited.
NOTIFICATION_METHODS = frozenset({"initialized"})

MAX_LINE_BYTES = 1 << 20          #: one frame; a larger line is a protocol fault
MAX_TOTAL_BYTES = 8 << 20         #: whole session; bounds a chatty or hostile child
MAX_MODELS_RECORDED = 64          #: bounded observation, never the whole payload
DEFAULT_DEADLINE_SECONDS = 20.0
#: How long cleanup may wait on a single pipe close before giving up and
#: recording it. Cleanup must be bounded too, or the unwind becomes the hang.
CLEANUP_JOIN_SECONDS = 1.0
MAX_DEADLINE_SECONDS = 120.0

OBSERVATION_SCHEMA = "wd.owned-codex-observation.v1"


class TransportError(RuntimeError):
    """Base class, so a caller can catch everything this module raises."""


class TransportRefused(TransportError):
    """A policy or input refusal. Nothing was sent to the child."""


class TransportProtocolError(TransportError):
    """The child said something outside the documented protocol."""


class TransportTimeout(TransportError):
    """A deadline expired while waiting for the child."""


class TransportChildError(TransportError):
    """The child exited, crashed, or could not be started."""


@dataclass(frozen=True)
class ClientInfo:
    """The identity this client presents in `initialize`."""

    name: str
    title: str
    version: str

    def as_params(self) -> dict[str, str]:
        for field in (self.name, self.title, self.version):
            if not isinstance(field, str) or not field or len(field) > 128:
                raise TransportRefused("clientInfo fields must be short non-empty text")
        return {"name": self.name, "title": self.title, "version": self.version}


def verify_executable(path: Path, expected_sha256: str) -> str:
    """Hash the CLI and refuse a mismatch BEFORE anything is spawned.

    The hash is checked against the file we are about to execute, not against a
    name or a version string a process could claim about itself later.
    """
    if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
        raise TransportRefused("expected_sha256 must be a 64-character digest")
    expected = expected_sha256.lower()
    if any(character not in "0123456789abcdef" for character in expected):
        raise TransportRefused("expected_sha256 must be hexadecimal")
    path = Path(path)
    if not path.is_file():
        raise TransportRefused("app-server executable does not exist")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    observed = digest.hexdigest()
    if observed != expected:
        raise TransportRefused("app-server executable hash does not match the pin")
    return observed


def default_spawn(executable: Path, arguments: Sequence[str]) -> Any:
    """Spawn the child with an argv list and no shell.

    Imported lazily and never called by the test suite: this task ships the
    transport, not a live start. A caller supplies its own spawn in tests.
    """
    import subprocess

    return subprocess.Popen(                       # noqa: S603 - argv list, shell=False
        [str(executable), *arguments],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        shell=False,
        close_fds=True,
    )


#: Module-issued spawn objects whose child provenance this module can describe.
#: The value is an immutable copy of the launch inputs captured at issuance.
#: A public callback attribute was forgeable, and a function closure exposes
#: writable cells, so neither can support an identity claim.  Weak keys keep no
#: spawn alive; the copied tuple detects even forced mutation of object slots.
_PinnedState = tuple[str, str, tuple[str, ...]]
_PinnedRegistration = tuple[weakref.ReferenceType[Any], _PinnedState]
_PINNED_SPAWNS: dict[int, _PinnedRegistration] = {}
_PINNED_SPAWNS_LOCK = threading.Lock()


def _registered_pinned_state(spawn: Callable[[], Any]) -> _PinnedState | None:
    """Return issuance state only for this exact live object, never an equal one."""
    with _PINNED_SPAWNS_LOCK:
        registration = _PINNED_SPAWNS.get(id(spawn))
        if registration is None or registration[0]() is not spawn:
            return None
        return registration[1]


def _register_pinned_spawn(spawn: "_PinnedSpawn") -> None:
    key = id(spawn)

    def discard(reference: weakref.ReferenceType[Any]) -> None:
        with _PINNED_SPAWNS_LOCK:
            current = _PINNED_SPAWNS.get(key)
            if current is not None and current[0] is reference:
                del _PINNED_SPAWNS[key]

    reference = weakref.ref(spawn, discard)
    with _PINNED_SPAWNS_LOCK:
        _PINNED_SPAWNS[key] = (reference, spawn._state())


class _PinnedSpawn:
    __slots__ = ("_path", "_expected_sha256", "_argv", "__weakref__")

    def __init__(self, path: Path, expected_sha256: str,
                 argv: tuple[str, ...]) -> None:
        self._path = path
        self._expected_sha256 = expected_sha256.lower()
        self._argv = argv

    def _state(self) -> _PinnedState:
        return (str(self._path), self._expected_sha256, self._argv)

    def __call__(self) -> Any:
        issued = _registered_pinned_state(self)
        if issued is None or self._state() != issued:
            raise TransportRefused("pinned spawn launch inputs changed after issuance")
        verify_executable(self._path, self._expected_sha256)
        return default_spawn(self._path, self._argv)


def pinned_spawn(executable: Path | str, expected_sha256: str,
                 arguments: Sequence[str] = ("app-server",)) -> Callable[[], Any]:
    """A spawn that verifies the pin AND starts that exact file.

    Hashing a path and then calling an unrelated callable proves nothing about
    what was started: the pin and the spawn were two separate decisions, and
    only the first was checked. This binds them. The digest is verified when
    the callable runs, immediately before the process is created, so the file
    cannot be swapped between the check and the start any more than the
    filesystem already allows.

    A spawn built here is the only kind whose child provenance this module is
    entitled to report. Anything else -- including every fake used in tests --
    is an arbitrary callback that may start anything at all, and the
    observation says so.
    """
    path = Path(executable)
    argv = tuple(str(argument) for argument in arguments)

    spawn = _PinnedSpawn(path, expected_sha256, argv)
    _register_pinned_spawn(spawn)
    return spawn


class _LineReader:
    """Pump the child's stdout into a queue so deadlines work on every platform.

    A blocking readline cannot be given a timeout portably, and a transport
    whose deadline only works on one OS is a transport that hangs on the other.
    """

    def __init__(self, stream: Any) -> None:
        self._stream = stream
        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._total = 0
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def _pump(self) -> None:
        try:
            while True:
                line = self._stream.readline(MAX_LINE_BYTES + 1)
                if not line:
                    self._queue.put(("eof", None))
                    return
                if len(line) > MAX_LINE_BYTES:
                    self._queue.put(("oversized", len(line)))
                    return
                self._total += len(line)
                if self._total > MAX_TOTAL_BYTES:
                    self._queue.put(("flooded", self._total))
                    return
                self._queue.put(("line", line))
        except Exception as exc:                    # noqa: BLE001 - reported, not raised
            self._queue.put(("error", exc))

    def next_line(self, deadline: float) -> bytes:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TransportTimeout("deadline expired before a frame arrived")
        try:
            kind, payload = self._queue.get(timeout=remaining)
        except queue.Empty:
            raise TransportTimeout("deadline expired waiting for a frame") from None
        if kind == "line":
            return payload
        if kind == "eof":
            raise TransportChildError("child closed stdout")
        if kind == "oversized":
            raise TransportProtocolError(f"frame exceeds {MAX_LINE_BYTES} bytes")
        if kind == "flooded":
            raise TransportProtocolError(f"child exceeded {MAX_TOTAL_BYTES} bytes")
        raise TransportChildError(f"child stdout failed: {type(payload).__name__}")


class OwnedAppServer:
    """A child we spawned, spoken to over a bounded read-only surface.

    Use as a context manager. On exit the child is terminated and reaped even
    if the body raised, because a transport that leaks a child on the error
    path is the one that matters.
    """

    def __init__(self, spawn: Callable[[], Any], *,
                 client_info: ClientInfo,
                 deadline_seconds: float = DEFAULT_DEADLINE_SECONDS) -> None:
        if not callable(spawn):
            raise TransportRefused("spawn must be callable; a pid is never accepted")
        if not isinstance(deadline_seconds, (int, float)):
            raise TransportRefused("deadline_seconds must be a number")
        if not 0 < float(deadline_seconds) <= MAX_DEADLINE_SECONDS:
            raise TransportRefused(
                f"deadline_seconds must be within (0, {MAX_DEADLINE_SECONDS}]")
        self._spawn = spawn
        self._client_info = client_info
        self._deadline_seconds = float(deadline_seconds)
        self._child: Any = None
        self._reader: _LineReader | None = None
        self._next_id = 0
        self._initialised = False
        self._poisoned: str | None = None
        #: Methods ACTUALLY written to the child, in order. Reported instead of
        #: the allow-list, because the allow-list states an intention and this
        #: states an observation, and only one of those is evidence.
        self.methods_sent: list[str] = []
        #: Cleanup problems, so a failed kill cannot pass as a clean exit.
        self.cleanup_errors: list[str] = []
        self.server_info: dict[str, Any] | None = None
        self.next_cursor: str | None = None

    # --- lifecycle -------------------------------------------------------

    def __enter__(self) -> OwnedAppServer:
        child = self._spawn()
        # OWN IT FIRST, then validate. Validating before ownership meant a child
        # that came back without usable stdio was never cleaned up: the raise
        # happened while nothing yet referred to the process, so it leaked.
        self._child = child
        if child is None:
            self._child = None
            raise TransportChildError("spawn did not return a child with stdio")
        if getattr(child, "stdin", None) is None or getattr(child, "stdout", None) is None:
            self.close()
            raise TransportChildError("spawn did not return a child with stdio")
        self._reader = _LineReader(child.stdout)
        return self

    def __exit__(self, exc_type: object, *_rest: object) -> None:
        self.close()
        # Raise only for failures that mean the CHILD may still be alive. A
        # pipe that would not close is recorded but does not raise: by then
        # terminate, kill and wait have already run, so it is a stray handle
        # rather than a leaked process, and turning it into an exception would
        # blunt the signal that actually matters. A cleanup failure also stays
        # quiet when raising would mask a more informative body exception.
        blocking = [entry for entry in self.cleanup_errors
                    if not entry.startswith("stdin_close:")]
        if blocking and exc_type is None:
            raise TransportChildError("child cleanup failed: " + "; ".join(blocking))

    def close(self) -> None:
        """STOP THE CHILD FIRST, then close the pipe. Order is the whole point.

        Closing stdin first deadlocks the unwind. A write that is blocked holds
        the BufferedWriter lock, ``close()`` waits for that lock, and the thing
        that would release the writer -- killing the child -- was queued behind
        the close. So the deadline expired, the unwind began, and then hung on
        exactly the cleanup meant to rescue it.

        Terminating first breaks the cycle: the dead child releases the blocked
        write, and only then is there any point closing the pipe. Every step
        records rather than swallows, and the final close is itself bounded, so
        a pipe that still refuses to close cannot hold the caller either.
        """
        child, self._child = self._child, None
        self._reader = None
        if child is None:
            return
        for step in ("terminate", "kill"):
            try:
                if child.poll() is None:
                    (child.terminate if step == "terminate" else child.kill)()
            except Exception as exc:                 # noqa: BLE001 - recorded, not raised
                # Failing to STOP the child is the difference between cleanup
                # and a leak, so it is recorded rather than swallowed.
                self.cleanup_errors.append(f"{step}: {type(exc).__name__}")
        try:
            child.wait(timeout=5)
        except Exception as exc:                     # noqa: BLE001 - recorded, not raised
            self.cleanup_errors.append(f"wait: {type(exc).__name__}")
        self._close_stdin_bounded(child)

    def _close_stdin_bounded(self, child: Any) -> None:
        """Close the pipe last, and never wait on it indefinitely."""
        stdin = getattr(child, "stdin", None)
        if stdin is None:
            return
        failure: list[BaseException] = []

        def shut() -> None:
            try:
                stdin.close()
            except BaseException as exc:             # noqa: BLE001 - recorded below
                failure.append(exc)

        worker = threading.Thread(target=shut, daemon=True)
        worker.start()
        worker.join(CLEANUP_JOIN_SECONDS)
        if worker.is_alive():
            self.cleanup_errors.append("stdin_close: did not return")
        elif failure:
            self.cleanup_errors.append(
                f"stdin_close: {type(failure[0]).__name__}")

    def _assert_alive(self) -> None:
        if self._child is None or self._reader is None:
            raise TransportChildError("transport is not open")
        code = self._child.poll()
        if code is not None:
            raise TransportChildError(f"child exited with code {code}")

    # --- framing ---------------------------------------------------------

    def _send(self, method: str, params: Mapping[str, Any] | None,
              *, request_id: int | None, deadline: float) -> None:
        """The single send site, and therefore the only injection boundary."""
        if self._poisoned:
            raise TransportRefused(f"transport is unusable: {self._poisoned}")
        if method not in ALLOWED_METHODS:
            raise TransportRefused(f"method is not in the read-only allow-list: {method!r}")
        frame: dict[str, Any] = {"method": method}
        if request_id is not None:
            frame["id"] = request_id
        frame["params"] = dict(params or {})
        encoded = json.dumps(frame, separators=(",", ":"),
                             ensure_ascii=True).encode("ascii") + b"\n"
        if len(encoded) > MAX_LINE_BYTES:
            raise TransportRefused("outgoing frame exceeds the frame bound")
        self._assert_alive()
        self._write_before(encoded, deadline)
        self.methods_sent.append(method)

    def _write_before(self, encoded: bytes, deadline: float) -> None:
        """Write, but stop WAITING at the deadline.

        A pipe write to a child that is not draining blocks, and the deadline
        used to cover only the read half, so a slow or stuck writer ignored it
        entirely. A blocking write cannot be cancelled portably, so instead of
        pretending otherwise: the write runs on ONE short-lived daemon thread,
        joined for the remaining time only. If it has not finished, we stop
        waiting, mark the transport unusable so a half-written frame can never
        be followed by another, and let close() terminate the child -- which is
        what actually releases the blocked write. The thread is bounded by the
        child's death, not left to run forever pretending to be a deadline.
        """
        failure: list[BaseException] = []

        def write() -> None:
            try:
                self._child.stdin.write(encoded)
                self._child.stdin.flush()
            except BaseException as exc:             # noqa: BLE001 - reported below
                failure.append(exc)

        worker = threading.Thread(target=write, daemon=True)
        worker.start()
        worker.join(max(0.0, deadline - time.monotonic()))
        if worker.is_alive():
            self._poisoned = "a write did not complete before its deadline"
            raise TransportTimeout("deadline expired while writing to the child")
        if failure:
            raise TransportChildError(
                f"child stdin failed: {type(failure[0]).__name__}") from failure[0]

    def _await_response(self, request_id: int, deadline: float) -> dict[str, Any]:
        """Read until the matching id, skipping notifications, bounded throughout."""
        assert self._reader is not None
        while True:
            raw = self._reader.next_line(deadline)
            try:
                message = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise TransportProtocolError("child sent a frame that is not JSON") from exc
            if not isinstance(message, dict):
                raise TransportProtocolError("child sent a frame that is not an object")
            if "id" not in message:
                continue                            # a notification; not our concern here
            if message["id"] != request_id:
                raise TransportProtocolError(
                    f"child answered id {message['id']!r}, expected {request_id!r}")
            if "error" in message:
                error = message["error"]
                code = error.get("code") if isinstance(error, dict) else None
                raise TransportProtocolError(f"child returned an error (code {code!r})")
            result = message.get("result")
            if not isinstance(result, dict):
                raise TransportProtocolError("child result is not an object")
            return result

    def _request(self, method: str, params: Mapping[str, Any] | None) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        deadline = time.monotonic() + self._deadline_seconds
        self._send(method, params, request_id=request_id, deadline=deadline)
        return self._await_response(request_id, deadline)

    # --- the bounded read-only surface ------------------------------------

    def initialize(self) -> dict[str, Any]:
        """The mandatory handshake. No other method may precede it."""
        if self._initialised:
            raise TransportRefused("initialize was already completed")
        result = self._request("initialize", {"clientInfo": self._client_info.as_params()})
        self._send("initialized", {}, request_id=None,
                   deadline=time.monotonic() + self._deadline_seconds)
        self._initialised = True
        self.server_info = {
            "user_agent": _bounded_text(result.get("userAgent")),
            "platform_family": _bounded_text(result.get("platformFamily")),
            "platform_os": _bounded_text(result.get("platformOs")),
        }
        return dict(self.server_info)

    def list_models(self, *, limit: int = 20,
                    include_hidden: bool = False) -> list[dict[str, Any]]:
        """Discovery only. Returns a bounded summary, never the raw payload."""
        if not self._initialised:
            raise TransportRefused("model/list before the initialize handshake")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise TransportRefused("limit must be an integer within [1, 100]")
        if not isinstance(include_hidden, bool):
            raise TransportRefused("include_hidden must be a boolean")
        result = self._request("model/list",
                               {"limit": limit, "includeHidden": include_hidden})
        # The documented envelope is result.data plus nextCursor. An earlier
        # version read result.models, a key the protocol does not define, so it
        # would have refused every real response while passing its own fixture.
        entries = result.get("data")
        if not isinstance(entries, list):
            raise TransportProtocolError("model/list result has no data list")
        self.next_cursor = _bounded_text(result.get("nextCursor"))
        summary: list[dict[str, Any]] = []
        for entry in entries[:MAX_MODELS_RECORDED]:
            if not isinstance(entry, Mapping):
                raise TransportProtocolError("model entry is not an object")
            summary.append({
                "id": _bounded_text(entry.get("id")),
                "display_name": _bounded_text(entry.get("displayName")),
                "is_default": entry.get("isDefault") if isinstance(
                    entry.get("isDefault"), bool) else None,
                "hidden": entry.get("hidden") if isinstance(
                    entry.get("hidden"), bool) else None,
                "default_reasoning_effort": _bounded_text(
                    entry.get("defaultReasoningEffort")),
            })
        return summary


def _bounded_text(value: Any, *, maximum: int = 256) -> str | None:
    """Keep short text, drop anything else. Never echoes an unbounded blob."""
    if not isinstance(value, str):
        return None
    return value[:maximum]


def observe_owned_app_server(spawn: Callable[[], Any], *,
                             client_info: ClientInfo,
                             limit: int = 20,
                             deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
                             executable: Path | str | None = None,
                             expected_sha256: str | None = None,
                             ) -> dict[str, Any]:
    """One bounded observation of a child we spawn, own, and then terminate.

    The return value is an observation, not a capability: it says what a freshly
    started app-server reports about itself, and nothing about any other
    process. ``control_allowed`` is structurally false on every path, mirroring
    the owning-session descriptor validator.

    TWO DIFFERENT CLAIMS, KEPT APART, because conflating them was an overclaim.

    ``file_digest_verified`` says a FILE on disk hashed to the expected digest.
    That is all it ever meant. It says nothing about what was started, because
    ``spawn`` is an arbitrary callable and hashing a path it may never open
    links the two by nothing at all.

    ``child_identity_verified`` is true only when ``spawn`` came from
    :func:`pinned_spawn`, which verifies the digest and starts THAT file, so
    the pin and the child are one decision rather than two. Every other spawn,
    including every fake in the test suite, reports false -- and ``observed``
    then says the child merely came from a supplied callback, rather than
    asserting a provenance this function cannot establish.
    """
    pinned_state = _registered_pinned_state(spawn)
    pinned_digest = pinned_state[1] if pinned_state is not None else None
    verification: dict[str, Any] = {
        "file_digest_verified": False,
        "executable_digest": None,
        "child_identity_verified": False,
        "verification_note": "no executable pin supplied; the child came from an "
                             "arbitrary callback and its identity is unverified",
    }
    if executable is not None or expected_sha256 is not None:
        if executable is None or expected_sha256 is None:
            raise TransportRefused(
                "executable and expected_sha256 must be supplied together")
        verification = {
            "file_digest_verified": True,
            "executable_digest": verify_executable(Path(executable), expected_sha256),
            "child_identity_verified": False,
            "verification_note": "a file was hashed, but this spawn is an arbitrary "
                                 "callback, so the child identity is unverified; use "
                                 "pinned_spawn to bind the two",
        }
    if pinned_digest is not None:
        verification = {
            "file_digest_verified": True,
            "executable_digest": pinned_digest,
            "child_identity_verified": True,
            "verification_note": "pinned_spawn verified the digest and started that file",
        }
    with OwnedAppServer(spawn, client_info=client_info,
                        deadline_seconds=deadline_seconds) as server:
        handshake = server.initialize()
        models = server.list_models(limit=limit)
        # MEASURED, not declared: the frames actually written, in order. The
        # allow-list is what we intended to be able to send; this is what went.
        methods_sent = list(server.methods_sent)
        next_cursor = server.next_cursor
        cleanup_errors = server.cleanup_errors
    return {
        "schema": OBSERVATION_SCHEMA,
        "observed": ("child_spawned_from_pinned_executable" if pinned_digest
                     else "child_returned_by_supplied_spawn"),
        "control_allowed": False,
        "methods_sent": methods_sent,
        "server": handshake,
        "models": models,
        "model_count_recorded": len(models),
        "next_cursor": next_cursor,
        "cleanup_clean": not cleanup_errors,
        "cleanup_errors": list(cleanup_errors),
        **verification,
    }
