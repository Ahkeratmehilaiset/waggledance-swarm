"""Default-off private prefix continuity state for cooperating publishers.

The caller separately authenticates directory ownership and access control.
This is not protection from a hostile same-user process or filesystem TOCTOU.
An unclosed pending marker always requires external reconciliation.
"""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import secrets

from sandbox.mind.step2 import shared_index_checkpoint as cp
from sandbox.mind.step2.reader_slot import reader_slot
from waggledance.core.bridge_log_reader import BridgeCursor, BridgePrefixPin

_STATE = "prefix-private-state.json"
_PENDING = "prefix-private-state.pending"
_LOCK = "prefix-private-state.lock"
_MARK = b"wd-prefix-private-pending-v1\n"


class StateRefused(ValueError):
    """Fixed, path-free refusal; state bytes are private."""


def _fail():
    raise StateRefused("private_state_refused") from None


def _pin(value):
    if type(value) is not BridgePrefixPin or type(value.sha256) is not str or cp.HEX.fullmatch(value.sha256) is None:
        _fail()
    cursor = cp._cursor(value.cursor)
    return {"cursor": cursor, "sha256": value.sha256}


def _value(pointer, prefix):
    if type(pointer) is not str or cp.HEX.fullmatch(pointer) is None:
        _fail()
    return {"schema": "wd.q4-private-prefix-state.v1", "pointer_sha256": pointer, "prefix": _pin(prefix)}


def _load(path):
    raw = cp._read(path, max_bytes=8192)
    value = cp._parse(raw)
    if (set(value) != {"schema", "pointer_sha256", "prefix"} or value["schema"] != "wd.q4-private-prefix-state.v1"
            or type(value["prefix"]) is not dict or set(value["prefix"]) != {"cursor", "sha256"}
            or type(value["prefix"]["cursor"]) is not dict
            or set(value["prefix"]["cursor"]) != {"offset", "file_identity", "generation"}):
        _fail()
    prefix = BridgePrefixPin(BridgeCursor(**value["prefix"]["cursor"]), value["prefix"]["sha256"])
    if cp._canonical(_value(value["pointer_sha256"], prefix)) != raw:
        _fail()
    return value


def _root_disjoint(private, shared):
    """Compare root identities against the other's ancestors, including aliases."""
    return not (any(os.path.samefile(ancestor, shared) for ancestor in (private, *private.parents))
                or any(os.path.samefile(ancestor, private) for ancestor in (shared, *shared.parents)))


class _LockedState:
    __slots__ = ("_root", "_owns_pending")

    def __init__(self, root):
        self._root = root
        self._owns_pending = False

    def load(self, expected_pointer):
        """Return the private in-process start pin, never a public report."""
        try:
            pending, target = self._root / _PENDING, self._root / _STATE
            cp._secure(pending, missing_leaf=True)
            if os.path.lexists(pending):
                _fail()
            value = _load(target)
            if (type(expected_pointer) is not str or cp.HEX.fullmatch(expected_pointer) is None
                    or value["pointer_sha256"] != expected_pointer):
                _fail()
            prefix = value["prefix"]
            return BridgePrefixPin(BridgeCursor(**prefix["cursor"]), prefix["sha256"])
        except Exception:
            pass
        _fail()

    def require(self, expected_pointer, prefix):
        try:
            _pin(prefix)
            pending, target = self._root / _PENDING, self._root / _STATE
            cp._secure(pending, missing_leaf=True)
            cp._secure(target, missing_leaf=True)
            if os.path.lexists(pending):
                _fail()
            if expected_pointer is None:
                if os.path.lexists(target):
                    _fail()
            elif not os.path.lexists(target) or _load(target) != _value(expected_pointer, prefix):
                _fail()
            return
        except Exception:
            pass
        _fail()

    def begin(self, expected_pointer):
        try:
            target = self._root / _STATE
            cp._secure(target, missing_leaf=True)
            if expected_pointer is None:
                if os.path.lexists(target):
                    _fail()
            elif not os.path.lexists(target) or _load(target)["pointer_sha256"] != expected_pointer:
                _fail()
            cp._write_new(self._root / _PENDING, _MARK)
            self._owns_pending = True
            return
        except Exception:
            pass
        _fail()

    def commit(self, expected_pointer, pointer, prefix):
        try:
            if not self._owns_pending or cp._read(self._root / _PENDING, max_bytes=128) != _MARK:
                _fail()
            target = self._root / _STATE
            cp._secure(target, missing_leaf=True)
            if expected_pointer is None:
                if os.path.lexists(target):
                    _fail()
            elif not os.path.lexists(target) or _load(target)["pointer_sha256"] != expected_pointer:
                _fail()
            raw = cp._canonical(_value(pointer, prefix))
            stage = self._root / (".prefix-private-" + secrets.token_hex(16) + ".staging")
            cp._write_new(stage, raw)
            if cp._read(stage, max_bytes=8192) != raw:
                _fail()
            cp._secure(target, missing_leaf=True)
            os.replace(stage, target)
            if cp._read(target, max_bytes=8192) != raw:
                _fail()
            self.finish()
            return
        except Exception:
            pass
        _fail()

    def finish(self):
        try:
            pending = self._root / _PENDING
            if not self._owns_pending or cp._read(pending, max_bytes=128) != _MARK:
                _fail()
            os.unlink(pending)
            self._owns_pending = False
            return
        except Exception:
            pass
        _fail()


@contextmanager
def locked_state(private_root, shared_root, *, enabled=False, timeout=0.2):
    if enabled is not True:
        _fail()
    entered = False
    try:
        private, shared = cp._secure(private_root), cp._secure(shared_root)
        if not private.is_dir() or not shared.is_dir():
            _fail()
        left, right = os.path.normcase(str(private)), os.path.normcase(str(shared))
        try:
            overlap = os.path.commonpath((left, right)) in (left, right)
        except ValueError:  # distinct fixed drives
            overlap = False
        if overlap or not _root_disjoint(private, shared):
            _fail()
        lock = cp._secure(private / _LOCK, missing_leaf=True)
        with reader_slot(lock, timeout_seconds=timeout):
            cp._secure(lock)
            entered = True
            yield _LockedState(Path(private))
    except Exception:
        if entered:
            raise
    if not entered:
        _fail()
