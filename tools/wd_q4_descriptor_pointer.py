"""Explicit, default-off publisher of a pinned Q4 descriptor pointer.

This does not install, start, or authenticate anything. It verifies a caller-pinned
installed bundle and generation, then publishes one sibling pointer. The temporary
file is exclusive and in the same directory; on Windows MoveFileExW uses REPLACE_EXISTING
and WRITE_THROUGH. Atomic visibility is not a guarantee against hardware power loss.
Path checks and replacement are separate calls: a concurrent same-user adversary can
swap files or junctions in between. This is not an ACL or TOCTOU security boundary.
An interrupted publish has UNKNOWN outcome; inspect before any retry. No files are
removed on failure. The installer must separately pin the resulting pointer digest.

The separate staged-target preparation reads independently pinned candidate bytes,
returns the exact pointer bytes and their digest, and does not read or write either
CURRENT pointer. A digest is not installation authority or proof of activation. Its
caller may save these bytes in its audit staging; an installer must validate the
tuple and authorization again at installation time.
"""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat

from tools import wd_q4_lane_binding as binding_mod

SCHEMA = 'wd.q4-descriptor-pointer.v1'
_HEX40 = re.compile(r'[0-9a-f]{40}\Z')
_HEX64 = re.compile(r'[0-9a-f]{64}\Z')
_HEX_UPPER = re.compile(r'[0-9A-F]{64}\Z')
_GEN = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})\Z')
_DESC_KEYS = frozenset({'schema', 'fleet_sha256', 'lanes', 'bundle_root', 'public_plan', 'interpreter', 'cwd',
    'binding_path', 'fixed_plan_path', 'snapshot_path', 'runtime_manifest_path', 'interpreter_sha256',
    'binding_sha256', 'fixed_plan_sha256', 'snapshot_sha256', 'runtime_manifest_sha256', 'interpreter_max_bytes',
    'timeout_seconds', 'max_output_bytes', 'outer', 'expected_binding'})


class PointerRefused(Exception):
    """Closed refusal; no paths or inputs are included in the message."""


class PointerOutcomeUnknown(Exception):
    """A temporary write or replacement was attempted; do not blindly retry."""


class PointerInterrupted(BaseException):
    """An interruption occurred during publication; do not swallow or retry it."""


def _is_reparse(path, info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def _chain(path):
    path = Path(path)
    for item in reversed((path, *path.parents)):
        info = item.lstat()
        if _is_reparse(item, info):
            raise PointerRefused('path_refused')
    return path


def _absolute(value):
    if type(value) is not str or not value or not os.path.isabs(value) or os.path.normpath(value) != value:
        raise PointerRefused('path_refused')
    if os.name == 'nt' and (not re.match(r'^[A-Za-z]:\\', value) or ':' in value[2:]):
        raise PointerRefused('path_refused')
    return Path(value)


def _same(a, b):
    return os.path.normcase(str(a)) == os.path.normcase(str(b))


def _read(path, limit):
    _chain(path)
    if not path.is_file():
        raise PointerRefused('path_refused')
    with path.open('rb') as stream:
        value = stream.read(limit + 1)
    if not 0 < len(value) <= limit:
        raise PointerRefused('size_refused')
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate')
        result[key] = value
    return result


def _json(value):
    def invalid(_):
        raise ValueError('constant')
    result = json.loads(value.decode('utf-8'), object_pairs_hook=_pairs, parse_constant=invalid)
    if type(result) is not dict:
        raise PointerRefused('schema_refused')
    return result


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _atomic_replace(source, destination):
    if os.name != 'nt':
        raise PointerRefused('platform_refused')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    move = kernel.MoveFileExW
    move.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
    move.restype = ctypes.c_int
    if not move(str(source), str(destination), 0x1 | 0x8):
        raise OSError(ctypes.get_last_error(), 'MoveFileExW failed')


def _verify(*, state_path, expected_bundle, expected_commit, expected_manifest_sha256,
            generations_root, generation, descriptor_sha256, staged=False):
    bundle, root = map(_absolute, (expected_bundle, generations_root))
    state_path = None if staged else _absolute(state_path)
    if ((not staged and state_path.name != 'WD_REBOOT_STATE_CURRENT.json') or not _HEX40.fullmatch(expected_commit or '')
            or not _HEX_UPPER.fullmatch(expected_manifest_sha256 or '') or not _GEN.fullmatch(generation or '')
            or not _HEX64.fullmatch(descriptor_sha256 or '')):
        raise PointerRefused('input_refused')
    target = None if staged else state_path.parent / 'WD_Q4_DESCRIPTOR_CURRENT.json'
    if target is not None:
        for parent in (bundle, root):
            if os.path.commonpath((str(target), str(parent))) == str(parent):
                raise PointerRefused('path_refused')
    _chain(bundle)
    _chain(root)
    if not bundle.is_dir() or not root.is_dir():
        raise PointerRefused('path_refused')
    manifest_path, fleet_path = bundle / 'deployment-manifest.json', bundle / 'wd-fleet.json'
    state_raw = None
    if not staged:
        state_raw = _read(state_path, 65536)
        state = _json(state_raw)
        if (state.get('schema_version') != 1 or state.get('source_commit') != expected_commit
                or state.get('final_commit') != expected_commit or not _same(state.get('active_bundle', ''), bundle)
                or not _same(state.get('fleet_manifest', ''), fleet_path)
                or state.get('manifest_sha256') != expected_manifest_sha256
                or state.get('final_manifest_sha256') != expected_manifest_sha256):
            raise PointerRefused('state_refused')
    manifest_raw = _read(manifest_path, 4 * 1024 * 1024)
    manifest = _json(manifest_raw)
    if (_sha(manifest_raw).upper() != expected_manifest_sha256 or manifest.get('source_commit') != expected_commit
            or type(manifest.get('files')) is not dict):
        raise PointerRefused('manifest_refused')
    fleet_raw = _read(fleet_path, 1048576)
    wrapper_raw = _read(bundle / 'Invoke-WdQ4Query.ps1', 1048576)
    files = manifest['files']
    if (str(files.get('wd-fleet.json', '')).lower() != _sha(fleet_raw)
            or str(files.get('Invoke-WdQ4Query.ps1', '')).lower() != _sha(wrapper_raw)):
        raise PointerRefused('manifest_refused')
    descriptor_path = root / generation / 'shared-session-config.v2.json'
    binding_path = root / generation / 'lane-binding.v1.json'
    descriptor_raw = _read(descriptor_path, 65536)
    desc = _json(descriptor_raw)
    if (_sha(descriptor_raw) != descriptor_sha256 or set(desc) != _DESC_KEYS
            or desc.get('schema') != 'wd.q4-shared-session-config.v2'
            or not _same(desc.get('bundle_root', ''), bundle)
            or not _same(desc.get('binding_path', ''), binding_path)
            or not _same(desc.get('fixed_plan_path', ''), root / generation / 'fixed-plan.v2.json')
            or not _same(desc.get('snapshot_path', ''), root / generation / 'public-snapshot.v1.json')
            or desc.get('fleet_sha256') != _sha(fleet_raw)):
        raise PointerRefused('descriptor_refused')
    binding_raw = _read(binding_path, 65536)
    if desc.get('binding_sha256') != _sha(binding_raw):
        raise PointerRefused('binding_refused')
    binding = binding_mod._parse(binding_raw)
    lanes, roster_digest = binding_mod._roster(fleet_raw, _sha(fleet_raw))
    if (not binding_mod._shape(binding, lanes) or set(binding['lanes']) != set(lanes)
            or set(desc.get('lanes', [])) != set(lanes)
            or binding['binding']['roster_sha256'] != roster_digest
            or binding['binding'] != desc.get('expected_binding')
            or binding['bundle_commit'] != expected_commit
            or binding['manifest_sha256'] != expected_manifest_sha256.lower()
            or binding['wrapper_sha256'] != _sha(wrapper_raw)):
        raise PointerRefused('binding_refused')
    if target is not None and os.path.lexists(target):
        _chain(target)
        if not target.is_file():
            raise PointerRefused('path_refused')
    data = {'schema': SCHEMA, 'active_bundle': str(bundle), 'manifest_sha256': expected_manifest_sha256,
            'bundle_commit': expected_commit, 'descriptor_path': str(descriptor_path),
            'descriptor_sha256': descriptor_sha256, 'generation': generation}
    blob = json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')
    if len(blob) > 4096:
        raise PointerRefused('size_refused')
    return target, blob, state_raw


def prepare_staged_descriptor_pointer(*, enabled=False, expected_bundle=None, expected_commit=None,
                                      expected_manifest_sha256=None, generations_root=None, generation=None,
                                      descriptor_sha256=None):
    """Prepare pinned bytes only; no active-state access, authority, publication or writes."""
    if enabled is False:
        return {'status': 'OFF'}
    if enabled is not True:
        raise PointerRefused('input_refused')
    try:
        kwargs = dict(state_path=None, expected_bundle=expected_bundle, expected_commit=expected_commit,
                      expected_manifest_sha256=expected_manifest_sha256, generations_root=generations_root,
                      generation=generation, descriptor_sha256=descriptor_sha256, staged=True)
        _, blob, _ = _verify(**kwargs)
        _, rechecked, _ = _verify(**kwargs)
        if blob != rechecked:
            raise PointerRefused('verification_refused')
    except Exception:
        raise PointerRefused('verification_refused') from None
    return {'status': 'PREPARED', 'pointer_bytes': blob, 'pointer_sha256': _sha(blob)}


def write_descriptor_pointer(*, enabled=False, state_path=None, expected_bundle=None, expected_commit=None,
                             expected_manifest_sha256=None, generations_root=None, generation=None,
                             descriptor_sha256=None):
    if enabled is False:
        return {'status': 'OFF'}
    if enabled is not True:
        raise PointerRefused('input_refused')
    try:
        if os.name != 'nt':
            raise PointerRefused('platform_refused')
        target, blob, state_raw = _verify(state_path=state_path, expected_bundle=expected_bundle,
            expected_commit=expected_commit, expected_manifest_sha256=expected_manifest_sha256,
            generations_root=generations_root, generation=generation, descriptor_sha256=descriptor_sha256)
        if _read(Path(state_path), 65536) != state_raw:
            raise PointerRefused('state_refused')
    except Exception:
        raise PointerRefused('verification_refused') from None
    temp = target.parent / ('.WD_Q4_DESCRIPTOR_CURRENT.' + secrets.token_hex(16) + '.tmp')
    attempted = False
    try:
        fd = os.open(str(temp), os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_BINARY', 0), 0o600)
        attempted = True
        with os.fdopen(fd, 'wb') as stream:
            stream.write(blob)
            stream.flush()
            os.fsync(stream.fileno())
        checked_target, checked_blob, checked_state = _verify(state_path=state_path, expected_bundle=expected_bundle,
            expected_commit=expected_commit, expected_manifest_sha256=expected_manifest_sha256,
            generations_root=generations_root, generation=generation, descriptor_sha256=descriptor_sha256)
        if checked_target != target or checked_blob != blob or checked_state != state_raw:
            raise PointerRefused('state_refused')
        _atomic_replace(temp, target)
    except Exception:
        if attempted:
            raise PointerOutcomeUnknown('publication_state_unknown') from None
        raise PointerRefused('write_refused') from None
    except BaseException:
        if attempted:
            raise PointerInterrupted('publication_state_unknown') from None
        raise
    return {'status': 'WRITTEN', 'pointer_sha256': _sha(blob)}
