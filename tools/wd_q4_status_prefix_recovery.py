"""Complete only this live call's successfully fenced publication.

This helper is not restart recovery: incomplete or interrupted operations require
external reconciliation, including ownership of every writer on the binding.
It runs only while the invoking frame still owns both runner markers. The private
and checkpoint locks protect cooperating checkpoint writers during verification,
receipt and release, but cannot fence older/non-cooperating writers. Path checks
are not protection from a same-user file swap; fsync is not a power-loss guarantee.
The receipt and prefix values are private and are never sent to the bridge.
"""
import hashlib
import json
import os
import stat
from dataclasses import asdict

from sandbox.mind.step2 import prefix_private_state as private_state
from sandbox.mind.step2 import shared_index_checkpoint as checkpoint_mod
from sandbox.mind.step2 import shared_capture_adapter as adapter
from tools import wd_q4_guard_state_binding as roots


def _digest(value):
    return hashlib.sha256(value).hexdigest()


def _verify_marker(path, expected, identity):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)
            or not os.path.samestat(info, identity)):
        raise ValueError('marker_refused')
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0))
    try:
        if not os.path.samestat(os.fstat(fd), identity) or os.read(fd, len(expected) + 1) != expected:
            raise ValueError('marker_refused')
    finally:
        os.close(fd)
    if not os.path.samestat(path.lstat(), identity):
        raise ValueError('marker_refused')


def _write_receipt(path, data):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOINHERIT', 0)
    fd = os.open(path, flags, 0o600)
    try:
        info = os.fstat(fd)
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            if stream.write(data) != len(data):
                raise OSError('short_write')
            stream.flush()
        os.fsync(fd)
    finally:
        os.close(fd)
    _verify_marker(path, data, info)


def complete_owned_publication(*, checkpoint, private, model, source_parent, sidecar_parent, expected_ids,
                               marks, marker_infos, token, report, plan, route, expected_route_sha256,
                               policy_bytes, roster_bytes):
    """Prove one finished fenced call, save its receipt, and release its markers.

    Only the original in-process call passes its marker identities. Any exception
    leaves outcome unknown; no blind retry, rollback or interrupted-run cleanup.
    A no-change window opens no fence: it must preserve the verified pointer and
    prefix and have no open private intent or active checkpoint fence.
    """
    if (report['status'] not in ('PUBLISHED_GENESIS', 'PUBLISHED_HISTORY', 'EMPTY_ADVANCED', 'EMPTY_NO_CHANGE')
            or plan.get('schema') != 'wd.q4-prefix-build-plan.v3' or plan.get('fenced') is not True):
        raise ValueError('unsupported_result')
    tasks, _ = adapter.strict_pins(policy_bytes, route['policy_sha256'], roster_bytes, route['roster_sha256'])
    pointer = report['pointer_sha256']
    with private_state.locked_state(private, checkpoint, enabled=True) as state:
        with private_state._checkpoint_lock(checkpoint, 0.2):
            if os.path.lexists(private / 'q4-adopt-recovery-job.pending.json'):
                raise ValueError('recovery_job_pending')
            current_ids = tuple(roots._observe(p) for p in (checkpoint, private, model, source_parent, sidecar_parent))
            if current_ids != expected_ids:
                raise ValueError('root_refused')
            for path, info in zip(marks, marker_infos):
                _verify_marker(path, token, info)
            if checkpoint_mod._active_fence(checkpoint) is not None:
                raise ValueError('fence_refused')
            if report['status'] == 'EMPTY_NO_CHANGE':
                if plan['expected_pointer_sha256'] is None or pointer != plan['expected_pointer_sha256']:
                    raise ValueError('fence_refused')
            else:
                landed = checkpoint_mod._fence_record(checkpoint, pointer, 'landed')
                other = checkpoint_mod._fence_record(checkpoint, pointer, 'not_landed')
                if (os.path.lexists(other) or checkpoint_mod._read(landed, max_bytes=checkpoint_mod._MAX_FENCE_BYTES)
                        != checkpoint_mod._fence_bytes(plan['expected_pointer_sha256'], pointer)):
                    raise ValueError('fence_refused')
            view, prefix = checkpoint_mod.read_verified_prefix_pointer(checkpoint, tasks,
                expected_pointer_sha256=pointer, policy_bytes=policy_bytes, policy_sha256=route['policy_sha256'],
                roster_bytes=roster_bytes, roster_sha256=route['roster_sha256'],
                expected_public_policy_sha256=route['public_policy_sha256'])
            state.require(pointer, prefix)
            if report['status'] == 'EMPTY_NO_CHANGE' and (asdict(prefix.cursor) != route['cursor']
                                                          or prefix.sha256 != route['prefix_sha256']):
                raise ValueError('cursor_refused')
            if report['status'] == 'EMPTY_ADVANCED' and asdict(prefix.cursor) == route['cursor']:
                raise ValueError('cursor_refused')
            receipt = {'schema': 'wd.q4-status-result.v1', 'marker_token_sha256': _digest(token),
                       'marker_identities': [(info.st_dev, info.st_ino) for info in marker_infos],
                       'root_identities': current_ids, 'route_sha256': expected_route_sha256,
                       'plan_sha256': route['plan_sha256'], 'prior_pointer_sha256': plan['expected_pointer_sha256'],
                       'pointer_sha256': pointer, 'generation': view['generation'], 'status': report['status'],
                       'watermark_sha256': view['watermark_sha256'], 'prefix_sha256': prefix.sha256}
            raw = json.dumps(receipt, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')
            receipt_path = private / ('q4-status-result-' + _digest(token) + '.json')
            _write_receipt(receipt_path, raw)
            for path, info in zip(marks, marker_infos):
                _verify_marker(path, token, info)
                os.unlink(path)
            return _digest(raw)
