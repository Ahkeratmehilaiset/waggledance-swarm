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
        self.server_info: dict[str, Any] | None = None

    # --- lifecycle -------------------------------------------------------

    def __enter__(self) -> OwnedAppServer:
        child = self._spawn()
        if child is None or getattr(child, "stdin", None) is None \
                or getattr(child, "stdout", None) is None:
            raise TransportChildError("spawn did not return a child with stdio")
        self._child = child
        self._reader = _LineReader(child.stdout)
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        child, self._child = self._child, None
        self._reader = None
        if child is None:
            return
        for step in ("stdin", "terminate", "kill"):
            try:
                if step == "stdin":
                    if child.stdin is not None:
                        child.stdin.close()
                elif step == "terminate":
                    if child.poll() is None:
                        child.terminate()
                else:
                    if child.poll() is None:
                        child.kill()
            except Exception:                        # noqa: BLE001,S110 - cleanup only
                continue
        try:
            child.wait(timeout=5)
        except Exception:                            # noqa: BLE001,S110 - cleanup only
            pass

    def _assert_alive(self) -> None:
        if self._child is None or self._reader is None:
            raise TransportChildError("transport is not open")
        code = self._child.poll()
        if code is not None:
            raise TransportChildError(f"child exited with code {code}")

    # --- framing ---------------------------------------------------------

    def _send(self, method: str, params: Mapping[str, Any] | None,
              *, request_id: int | None) -> None:
        """The single send site, and therefore the only injection boundary."""
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
        try:
            self._child.stdin.write(encoded)
            self._child.stdin.flush()
        except Exception as exc:                     # noqa: BLE001
            raise TransportChildError(f"child stdin failed: {type(exc).__name__}") from exc

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
        self._send(method, params, request_id=request_id)
        return self._await_response(request_id, deadline)

    # --- the bounded read-only surface ------------------------------------

    def initialize(self) -> dict[str, Any]:
        """The mandatory handshake. No other method may precede it."""
        if self._initialised:
            raise TransportRefused("initialize was already completed")
        result = self._request("initialize", {"clientInfo": self._client_info.as_params()})
        self._send("initialized", {}, request_id=None)
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
        entries = result.get("models")
        if not isinstance(entries, list):
            raise TransportProtocolError("model/list result has no models list")
        summary: list[dict[str, Any]] = []
        for entry in entries[:MAX_MODELS_RECORDED]:
            if not isinstance(entry, Mapping):
                raise TransportProtocolError("model entry is not an object")
            summary.append({
                "id": _bounded_text(entry.get("id")),
                "display_name": _bounded_text(entry.get("displayName")),
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
                             ) -> dict[str, Any]:
    """One bounded observation of a child we spawn, own, and then terminate.

    The return value is an observation, not a capability: it says what a freshly
    started app-server reports about itself, and nothing about any other
    process. ``control_allowed`` is structurally false on every path, mirroring
    the owning-session descriptor validator.
    """
    with OwnedAppServer(spawn, client_info=client_info,
                        deadline_seconds=deadline_seconds) as server:
        handshake = server.initialize()
        models = server.list_models(limit=limit)
    return {
        "schema": OBSERVATION_SCHEMA,
        "observed": "spawned_child_only",
        "control_allowed": False,
        "methods_used": sorted(ALLOWED_METHODS),
        "server": handshake,
        "models": models,
        "model_count_recorded": len(models),
    }
