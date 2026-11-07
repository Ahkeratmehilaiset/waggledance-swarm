"""Experimental, default-off, pre-publication lifecycle admission.

This component is NOT integrated with any writer, installer, or recovery path.
It establishes only a cooperative process lock and one persistent pending
record for a hash-pinned new-generation binding. It never clears the record.
It does not inspect other private roots, migrate old records, authenticate the
caller or defend against a hostile same-user process. A process crash can leave
an incomplete record, which is UNKNOWN; hardware power-loss durability and
races against path replacement are not established. Do not use as a release
or production-readiness signal.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from sandbox.mind.step2.reader_slot import reader_slot, ReaderBusy
from tools import wd_q4_descriptor_pointer as pointer

_HEX = re.compile(r'[0-9a-f]{64}\Z')
_BINDING_SCHEMA = 'wd.q4-lifecycle-binding.v2'
_PENDING_SCHEMA = 'wd.q4-lifecycle-pending.v2'
_PENDING = 'q4-lifecycle-v2.pending.json'
_LOCK = 'q4-lifecycle-v2.lock'
_PRIVATE_SUFFIX = '.q4-lifecycle-private-v2'
_LEGACY_PRIVATE = ('q4-card-refresh.pending.json', 'q4-live-update.pending.json',
                   'q4-continuation-adopt.pending.json', 'q4-adopt-recovery-job.pending.json',
                   'prefix-private-state.pending', '.wd-q4-status-prefix-runner.pending',
                   'q4-cutoff.pending.json', 'q4-continuation-orchestrator.pending.json',
                   'q4-continuation-orchestrator-job.pending.json')
_LEGACY_GENERATION = ('q4-live-publication.pending.json', 'q4-prefix-fence.json',
                      '.wd-q4-status-prefix-runner.pending')


class Refused(ValueError):
    """Admission was refused before attempting a lifecycle marker."""


class Unknown(RuntimeError):
    """Pending state, concurrent work, or outcome must be inspected externally."""


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def _pin(value):
    if type(value) is not str or not _HEX.fullmatch(value):
        raise ValueError()
    return value


def _binding(raw, pin):
    if type(raw) is not bytes or not 0 < len(raw) <= 65536 or hashlib.sha256(raw).hexdigest() != _pin(pin):
        raise ValueError()
    doc = pointer._json(raw)
    if (set(doc) != {'schema', 'generation_root', 'private_root'} or doc['schema'] != _BINDING_SCHEMA
            or _encode(doc) != raw):
        raise ValueError()
    root = pointer._absolute(doc['generation_root'])
    private = pointer._absolute(doc['private_root'])
    expected = root.parent / (root.name + _PRIVATE_SUFFIX)
    if not root.name or not pointer._same(private, expected):
        raise ValueError()
    pointer._chain(root); pointer._chain(private)
    if not root.is_dir() or not private.is_dir():
        raise ValueError()
    return root, private


def _check_paths(root, private, lock):
    pointer._chain(root); pointer._chain(private); pointer._chain(lock)
    if not root.is_dir() or not private.is_dir() or not lock.is_file():
        raise ValueError()


def _legacy_pending(root, private, *, ignore_private=()):
    return any(os.path.lexists(parent / name) for parent, names in
               ((root, _LEGACY_GENERATION), (private, _LEGACY_PRIVATE)) for name in names
               if parent != private or name not in ignore_private)


def _record(binding_sha256, attempt, previous_selector_sha256):
    _pin(binding_sha256); _pin(attempt)
    if previous_selector_sha256 is not None: _pin(previous_selector_sha256)
    return _encode({'schema': _PENDING_SCHEMA, 'binding_sha256': binding_sha256,
                    'attempt': attempt, 'previous_selector_sha256': previous_selector_sha256})


def _clear_owned(root, binding_sha256, attempt, previous_selector_sha256):
    """Clear only exact owned bytes; caller holds the lock and verifies outcome.

    This is an internal primitive, not independent proof of publication. The
    integration must verify the durable result and selector while locked.
    """
    pending = root / _PENDING
    expected = _record(binding_sha256, attempt, previous_selector_sha256)
    if _read_pending(pending) != expected:
        raise Unknown('lifecycle_pending_mismatch')
    pointer._chain(pending)
    pending.unlink()


def _read_pending(path):
    pointer._chain(path)
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOINHERIT', 0) | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(str(path), flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 65536:
            raise ValueError()
        data = bytearray()
        while len(data) <= 65536:
            chunk = os.read(fd, min(65537-len(data), 8192))
            if not chunk: break
            data.extend(chunk)
        pointer._chain(path)
        if len(data) != info.st_size or not os.path.samestat(info, path.stat()):
            raise ValueError()
        return bytes(data)
    finally:
        os.close(fd)


@contextmanager
def admit(*, enabled=False, binding_bytes=None, binding_sha256=None, attempt=None,
          previous_selector_sha256=None):
    """Reserve once and keep a marker even on success; never publish or clear it."""
    if enabled is False:
        yield {'status': 'OFF'}
        return
    try:
        if enabled is not True: raise ValueError()
        root, private = _binding(binding_bytes, binding_sha256)
        _pin(attempt)
        if previous_selector_sha256 is not None: _pin(previous_selector_sha256)
    except Exception:
        raise Refused('lifecycle_refused') from None
    lock = root / _LOCK
    pending = root / _PENDING
    try:
        if os.path.lexists(lock): pointer._chain(lock)
        with reader_slot(lock, timeout_seconds=0.2):
            _check_paths(root, private, lock)
            if os.path.lexists(pending) or _legacy_pending(root, private):
                raise Unknown('lifecycle_pending_or_unknown')
            raw = _record(binding_sha256, attempt, previous_selector_sha256)
            flags = (os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_BINARY', 0)
                     | getattr(os, 'O_NOINHERIT', 0) | getattr(os, 'O_NOFOLLOW', 0))
            # From the create attempt onward, an interruption or failure can
            # leave partial state. Never remove, recreate, or retry the marker.
            try:
                fd = os.open(str(pending), flags, 0o600)
                with os.fdopen(fd, 'wb') as stream:
                    if stream.write(raw) != len(raw): raise OSError()
                    stream.flush(); os.fsync(stream.fileno())
                _check_paths(root, private, lock)
                if _read_pending(pending) != raw: raise ValueError()
            except Exception:
                raise Unknown('lifecycle_write_unknown') from None
            yield {'status': 'ADMITTED', 'attempt': attempt, 'binding_sha256': binding_sha256}
    except ReaderBusy:
        raise Unknown('lifecycle_busy') from None
    except Unknown:
        raise
    except Exception:
        raise Unknown('lifecycle_unknown') from None


@contextmanager
def _resume(*, binding_bytes, binding_sha256, attempt, previous_selector_sha256):
    """Lock and verify the exact pending record; never infer an outcome."""
    try:
        root, private = _binding(binding_bytes, binding_sha256)
        expected = _record(binding_sha256, attempt, previous_selector_sha256)
        lock = root / _LOCK
        if os.path.lexists(lock): pointer._chain(lock)
        with reader_slot(lock, timeout_seconds=0.2):
            _check_paths(root, private, lock)
            if _legacy_pending(root, private, ignore_private=('q4-card-refresh.pending.json',)):
                raise Unknown('legacy_pending')
            if _read_pending(root / _PENDING) != expected:
                raise Unknown('lifecycle_pending_mismatch')
            yield root
    except Unknown:
        raise
    except Exception:
        raise Unknown('lifecycle_unknown') from None


def inspect(*, enabled=False, binding_bytes=None, binding_sha256=None):
    """Observe the v2 pending state under its lock; never clear anything.

    May create the persistent lock file. ABSENT_V2_ONLY does not attest that
    an old private root has no pending data.
    """
    if enabled is False: return {'status': 'OFF'}
    try:
        if enabled is not True: raise ValueError()
        root, private = _binding(binding_bytes, binding_sha256)
    except Exception:
        raise Refused('lifecycle_refused') from None
    lock = root / _LOCK
    pending = root / _PENDING
    try:
        if os.path.lexists(lock): pointer._chain(lock)
        with reader_slot(lock, timeout_seconds=0.2):
            _check_paths(root, private, lock)
            if _legacy_pending(root, private): raise Unknown('legacy_pending')
            if not os.path.lexists(pending): return {'status': 'ABSENT_V2_ONLY'}
            raw = _read_pending(pending)
            record = pointer._json(raw)
            if (set(record) != {'schema', 'binding_sha256', 'attempt', 'previous_selector_sha256'}
                    or record['schema'] != _PENDING_SCHEMA or record['binding_sha256'] != binding_sha256
                    or _encode(record) != raw): raise ValueError()
            _pin(record['attempt'])
            if record['previous_selector_sha256'] is not None: _pin(record['previous_selector_sha256'])
            return {'status': 'PENDING', 'attempt': record['attempt'],
                    'pending_sha256': hashlib.sha256(raw).hexdigest()}
    except Exception:
        raise Unknown('lifecycle_unknown') from None
