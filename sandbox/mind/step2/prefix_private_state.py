"""Default-off private prefix continuity state for cooperating publishers.

The caller separately authenticates directory ownership and access control.
This is not protection from a hostile same-user process or filesystem TOCTOU.
An unclosed pending marker always requires external reconciliation.

Opt-in recoverable intent (begin_intent/resolve_intent, each enabled=True, under the
same private-state lock): the pending slot holds, instead of the generic marker, an
intent binding the exact prior pointer digest (None = genesis), the prepared attempt
pointer digest (e.g. a prepare_prefix_* attempt_sha256 later passed as
expected_attempt_sha256), the prior and the candidate prefix pin. It is created
crash-safe (fsynced unique stage, then a hard link that fails if the slot exists), so a
torn write never leaves a partial intent. Resolution reads the shared pointer digest
itself under the cooperating checkpoint lock (lock order: private-state lock, then
checkpoint lock, as in publication) and answers only "landed" (pointer == attempt; the
private state is moved to the candidate) or "unchanged" (pointer == prior); a third
pointer, a legacy marker, a malformed intent or a private state matching neither side
refuses and keeps the slot. Legacy methods refuse while an intent is open. A
no_change empty advance (attempt == prior) cannot use an intent.
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


_INTENT_SCHEMA = "wd.q4-private-prefix-intent.v1"
_INTENT_KEYS = frozenset({"schema", "prior_pointer_sha256", "attempt_pointer_sha256", "prior_prefix",
                          "candidate_prefix"})


def _hex(value):
    return type(value) is str and cp.HEX.fullmatch(value) is not None


def _intent_value(prior, prior_prefix, attempt, candidate_prefix):
    if (prior is not None and not _hex(prior)) or not _hex(attempt) or prior == attempt:
        _fail()
    return {"schema": _INTENT_SCHEMA, "prior_pointer_sha256": prior, "attempt_pointer_sha256": attempt,
            "prior_prefix": _pin(prior_prefix), "candidate_prefix": _pin(candidate_prefix)}


def _prefix_from(value):
    if (type(value) is not dict or set(value) != {"cursor", "sha256"} or type(value["cursor"]) is not dict
            or set(value["cursor"]) != {"offset", "file_identity", "generation"}):
        _fail()
    return BridgePrefixPin(BridgeCursor(**value["cursor"]), value["sha256"])


def _load_intent(raw):
    value = cp._parse(raw)
    if set(value) != _INTENT_KEYS or value["schema"] != _INTENT_SCHEMA:
        _fail()
    prior, attempt = value["prior_pointer_sha256"], value["attempt_pointer_sha256"]
    prior_prefix, candidate = _prefix_from(value["prior_prefix"]), _prefix_from(value["candidate_prefix"])
    if cp._canonical(_intent_value(prior, prior_prefix, attempt, candidate)) != raw:
        _fail()
    return prior, prior_prefix, attempt, candidate


def _stage(root, raw):
    stage = root / (".prefix-private-" + secrets.token_hex(16) + ".staging")
    cp._write_new(stage, raw)
    if cp._read(stage, max_bytes=8192) != raw:
        _fail()
    return stage


def _discard(path):
    try:
        os.unlink(path)
    except OSError:
        pass  # An orphan stage is never read as state.


def _create_slot(root, raw):
    """Exclusive crash-safe slot creation: the hard link is atomic and fails if the slot exists."""
    stage = None
    try:
        stage = _stage(root, raw)
        os.link(stage, cp._secure(root / _PENDING, missing_leaf=True))
    finally:
        if stage is not None:
            _discard(stage)


def _write_state(root, pointer, prefix):
    raw = cp._canonical(_value(pointer, prefix))
    target = root / _STATE
    stage = _stage(root, raw)
    cp._secure(target, missing_leaf=True)
    os.replace(stage, target)
    if cp._read(target, max_bytes=8192) != raw:
        _fail()


def _shared_pointer(shared, timeout):
    """Digest of the shared pointer bytes (None when absent) under the cooperating checkpoint lock."""
    lock = cp._secure(shared / cp.LOCK, missing_leaf=True)
    with reader_slot(lock, timeout_seconds=timeout):
        cp._secure(lock)
        pointer = cp._secure(shared / cp.POINTER, missing_leaf=True)
        if not os.path.lexists(pointer):
            return None
        return cp._sha(cp._read(pointer, max_bytes=cp._MAX_POINTER_BYTES))


def _root_disjoint(private, shared):
    """Compare root identities against the other's ancestors, including aliases."""
    return not (any(os.path.samefile(ancestor, shared) for ancestor in (private, *private.parents))
                or any(os.path.samefile(ancestor, private) for ancestor in (shared, *shared.parents)))


class _LockedState:
    __slots__ = ("_root", "_owns_pending", "_shared", "_timeout")

    def __init__(self, root, shared=None, timeout=0.2):
        self._root = root
        self._owns_pending = False
        self._shared = shared
        self._timeout = timeout

    def begin_intent(self, prior_pointer, prior_prefix, attempt_pointer, candidate_prefix, *, enabled=False):
        """Open one durable intent bound to the current private state AND the observed shared pointer."""
        try:
            if enabled is not True or self._shared is None:
                _fail()
            raw = cp._canonical(_intent_value(prior_pointer, prior_prefix, attempt_pointer, candidate_prefix))
            pending, target = self._root / _PENDING, self._root / _STATE
            cp._secure(pending, missing_leaf=True)
            cp._secure(target, missing_leaf=True)
            if os.path.lexists(pending):
                _fail()
            if prior_pointer is None:
                if os.path.lexists(target):
                    _fail()
            elif not os.path.lexists(target) or _load(target) != _value(prior_pointer, prior_prefix):
                _fail()
            if _shared_pointer(self._shared, self._timeout) != prior_pointer:
                _fail()
            _create_slot(self._root, raw)
            return
        except Exception:
            pass
        _fail()

    def resolve_intent(self, *, enabled=False):
        """Close the open intent: "landed" or "unchanged" from the observed shared pointer, else refuse."""
        try:
            if enabled is not True or self._shared is None:
                _fail()
            pending, target = self._root / _PENDING, self._root / _STATE
            raw = cp._read(pending, max_bytes=8192)
            if raw == _MARK:
                _fail()  # a legacy marker stays unknown
            prior, prior_prefix, attempt, candidate = _load_intent(raw)
            cp._secure(target, missing_leaf=True)
            state = _load(target) if os.path.lexists(target) else None
            current = _shared_pointer(self._shared, self._timeout)
            if state == _value(attempt, candidate):
                if current != attempt:
                    _fail()
                outcome = "landed"  # the state move already happened before an interruption
            elif (state is None and prior is None) or (prior is not None and state == _value(prior, prior_prefix)):
                if current == attempt:
                    _write_state(self._root, attempt, candidate)
                    outcome = "landed"
                elif current == prior:
                    outcome = "unchanged"
                else:
                    _fail()
            else:
                _fail()
            if cp._read(pending, max_bytes=8192) != raw:
                _fail()
            os.unlink(pending)
            return outcome
        except Exception:
            pass
        _fail()

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
            yield _LockedState(Path(private), Path(shared), timeout)
    except Exception:
        if entered:
            raise
    if not entered:
        _fail()
