"""Experimental, opt-in live snapshot selection under an existing pinned anchor.

This module provides byte consistency, not publisher or lane authentication,
wall-clock freshness, source authenticity, anti-rollback, same-user isolation,
or a scheduler. The caller must independently authorize and protect the source,
generation root, publisher and input pins. A same-user writer may forge all files.
Only immutable generations underneath the original descriptor's generation root
are eligible. An interrupted pointer write has UNKNOWN outcome; do not retry.
"""
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets

from tools import wd_q4_descriptor_pointer as pointer_io
from tools import wd_q4_lane_binding_producer as binding_producer
from tools import wd_q4_lane_link as link
from tools import wd_q4_secure_transport as transport

_GEN = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})\Z')
_HEX = re.compile(r'[0-9a-f]{64}\Z')
_SCHEMA = 'wd.q4-live-descriptor.v1'
_POINTER = 'q4-live-current.json'
_KEYS = frozenset({'schema', 'baseline_sha256', 'generation', 'descriptor_sha256', 'index_pointer_sha256'})
_DYNAMIC = frozenset({'public_plan', 'binding_path', 'binding_sha256', 'fixed_plan_path', 'fixed_plan_sha256',
                      'snapshot_path', 'snapshot_sha256', 'expected_binding'})
_STABLE_PINS = frozenset({'encoder_sha256', 'policy_sha256', 'roster_sha256', 'public_policy_sha256'})
_PLAN_DYNAMIC = frozenset({'bundle_sha256', 'bundle_bytes', 'expected_pointer_sha256', 'expected_source_sha256'})
_GENERATION_PATHS = (('binding_path', 'lane-binding.v1.json'), ('fixed_plan_path', 'fixed-plan.v2.json'),
                     ('snapshot_path', 'public-snapshot.v1.json'), ('public_plan', 'public-plan.json'))
# The staged baseline composer pins its separately supplied public plan by
# digest in the lane binding; it materializes the other three files in the
# baseline generation. Refreshed candidates materialize all four locally.
_BASELINE_GENERATION_PATHS = tuple(item for item in _GENERATION_PATHS if item[0] != 'public_plan')
_FIXED_BINDING_PINS = (('expected_pointer_sha256', 'pointer_sha256'),
                       ('expected_source_sha256', 'source_anchor_sha256'),
                       ('expected_encoder_sha256', 'encoder_sha256'), ('policy_sha256', 'policy_sha256'),
                       ('roster_sha256', 'roster_sha256'),
                       ('expected_public_policy_sha256', 'public_policy_sha256'))


class LiveSnapshotRefused(ValueError):
    """No query or path content is included in a refusal."""


class LiveSnapshotOutcomeUnknown(RuntimeError):
    """A pointer write began; reconcile before another attempt."""


class LiveSnapshotInterrupted(BaseException):
    """An interrupted pointer write may have committed; never auto-retry."""


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _disjoint(left, right):
    left, right = Path(left), Path(right)
    left_key, right_key = os.path.normcase(str(left)), os.path.normcase(str(right))
    try:
        if os.path.commonpath((left_key, right_key)) in (left_key, right_key):
            return False
    except ValueError:
        pass  # Different local drives cannot contain one another.
    return not (any(os.path.samefile(left, part) for part in (right, *right.parents))
                or any(os.path.samefile(right, part) for part in (left, *left.parents)))


def _validate_roots(generations, bundle, index):
    for path in (generations, bundle, index):
        pointer_io._chain(path)
        if not Path(path).is_dir():
            raise ValueError()
    if not _disjoint(generations, bundle) or not _disjoint(generations, index):
        raise ValueError()


def _parse(raw):
    return pointer_io._json(raw)


def _read(path, limit, pin=None):
    raw = pointer_io._read(Path(path), limit)
    if pin is not None and (type(pin) is not str or not _HEX.fullmatch(pin)
                            or not hmac.compare_digest(_sha(raw), pin)):
        raise ValueError()
    return raw


def _plan(descriptor, inputs):
    binding = _parse(inputs['binding_bytes'])
    if binding.get('binding') != descriptor['expected_binding']:
        raise ValueError()
    path, raw, pins = link._public_plan(descriptor['public_plan'], binding['public_plan_sha256'])
    if any(pins[key] != descriptor['expected_binding'][key] for key in pins):
        raise ValueError()
    return _parse(raw), binding, raw


def _check_disk(descriptor, descriptor_raw, descriptor_pin, fleet, inputs):
    if (_parse(descriptor_raw) != descriptor or _sha(descriptor_raw) != descriptor_pin
            or _read(Path(descriptor['bundle_root']) / 'wd-fleet.json', 1048576,
                     descriptor['fleet_sha256']) != fleet):
        raise ValueError()
    for path_key, pin_key, data_key, limit in (
            ('binding_path', 'binding_sha256', 'binding_bytes', 65536),
            ('fixed_plan_path', 'fixed_plan_sha256', 'fixed_plan_bytes', 65536),
            ('snapshot_path', 'snapshot_sha256', 'fixed_bundle_bytes', 16 * 1024 * 1024),
            ('runtime_manifest_path', 'runtime_manifest_sha256', 'runtime_manifest_bytes', 8 * 1024 * 1024)):
        if _read(descriptor[path_key], limit, descriptor[pin_key]) != inputs[data_key]:
            raise ValueError()


def _checked_pair(base, candidate, manifest_sha256):
    """Compare immutable runtime/fleet/policy and check the real binding producer."""
    bd, _, base_pin, base_fleet, _, bi = base
    cd, _, candidate_pin, fleet, _, ci = candidate
    _check_disk(bd, base[1], base_pin, base_fleet, bi)
    _check_disk(cd, candidate[1], candidate_pin, fleet, ci)
    if (set(bd) != set(cd) or any(bd[key] != cd[key] for key in set(bd) - _DYNAMIC)
            or any(bd['expected_binding'][key] != cd['expected_binding'][key] for key in _STABLE_PINS)
            or fleet != base_fleet):
        raise ValueError()
    bp, _, _ = _plan(bd, bi)
    cp, cb, public_raw = _plan(cd, ci)
    if any(bp[key] != cp[key] for key in ('root', 'policy_path', 'roster_path', 'public_policy_path')):
        raise ValueError()
    old_fixed = transport._fixed_plan(bi['fixed_plan_bytes'])
    new_fixed = transport._fixed_plan(ci['fixed_plan_bytes'])
    for fixed, descriptor in ((old_fixed, bd), (new_fixed, cd)):
        if (any(fixed[left] != descriptor['expected_binding'][right] for left, right in _FIXED_BINDING_PINS)
                or fixed['runtime_manifest_path'] != descriptor['runtime_manifest_path']
                or fixed['runtime_manifest_sha256'] != descriptor['runtime_manifest_sha256']):
            raise ValueError()
    if (set(old_fixed) != set(new_fixed)
            or any(old_fixed[key] != new_fixed[key] for key in set(old_fixed) - _PLAN_DYNAMIC)
            or old_fixed['bundle_sha256'] != _sha(bi['fixed_bundle_bytes'])
            or old_fixed['bundle_bytes'] != len(bi['fixed_bundle_bytes'])
            or new_fixed['bundle_sha256'] != _sha(ci['fixed_bundle_bytes'])
            or new_fixed['bundle_bytes'] != len(ci['fixed_bundle_bytes'])):
        raise ValueError()
    bundle_root = Path(cd['bundle_root'])
    manifest_raw = _read(bundle_root / 'deployment-manifest.json', 4 * 1024 * 1024, manifest_sha256.lower())
    manifest = _parse(manifest_raw)
    wrapper_raw = _read(bundle_root / 'Invoke-WdQ4Query.ps1', 1024 * 1024)
    produced = binding_producer.build_lane_binding(enabled=True, expected_commit=manifest['source_commit'],
        manifest_bytes=manifest_raw, expected_manifest_sha256=manifest_sha256.lower(), fleet_bytes=fleet,
        expected_fleet_sha256=_sha(fleet), wrapper_bytes=wrapper_raw, expected_wrapper_sha256=_sha(wrapper_raw),
        public_plan_bytes=public_raw,
        expected_public_plan_sha256=cb['public_plan_sha256'], snapshot_bytes=ci['fixed_bundle_bytes'],
        expected_snapshot_sha256=cd['snapshot_sha256'])
    if not hmac.compare_digest(produced['binding_bytes'], ci['binding_bytes']):
        raise ValueError()
    if base_pin == candidate_pin:
        raise ValueError()
    return cp['root']


def select(*, enabled=False, baseline_path=None, baseline_sha256=None, baseline=None,
           manifest_sha256=None, load_configuration=None):
    """Select once; caller must recheck the publication bytes before output."""
    if enabled is False:
        return {'status': 'OFF'}
    result = None
    try:
        if (enabled is not True or type(baseline_path) is not str or type(baseline_sha256) is not str
                or not _HEX.fullmatch(baseline_sha256) or not callable(load_configuration)):
            raise ValueError()
        base_path = pointer_io._absolute(baseline_path)
        if not base_path.is_absolute() or base_path.name != 'shared-session-config.v2.json':
            raise ValueError()
        root = base_path.parent.parent
        if not _GEN.fullmatch(base_path.parent.name):
            raise ValueError()
        if any(Path(baseline[0][key]) != base_path.parent / name for key, name in _BASELINE_GENERATION_PATHS):
            raise ValueError()
        publication_path = root / _POINTER
        raw = _read(publication_path, 4096)
        publication = _parse(raw)
        if (set(publication) != _KEYS or publication['schema'] != _SCHEMA
                or publication['baseline_sha256'] != baseline_sha256
                or type(publication['generation']) is not str or not _GEN.fullmatch(publication['generation'])
                or type(publication['descriptor_sha256']) is not str or not _HEX.fullmatch(publication['descriptor_sha256'])
                or type(publication['index_pointer_sha256']) is not str
                or not _HEX.fullmatch(publication['index_pointer_sha256'])):
            raise ValueError()
        path = root / publication['generation'] / 'shared-session-config.v2.json'
        candidate = load_configuration(str(path), publication['descriptor_sha256'])
        if any(Path(candidate[0][key]) != path.parent / name for key, name in _GENERATION_PATHS):
            raise ValueError()
        index_root = Path(_checked_pair(baseline, candidate, manifest_sha256))
        _validate_roots(root, Path(baseline[0]['bundle_root']), index_root)
        if candidate[0]['expected_binding']['pointer_sha256'] != publication['index_pointer_sha256']:
            raise ValueError()
        _read(index_root / 'q4-latest.json', 16384, publication['index_pointer_sha256'])
        _read(base_path, 65536, baseline_sha256)
        _read(path, 65536, publication['descriptor_sha256'])
        if _read(publication_path, 4096) != raw:
            raise ValueError()
        result = {'status': 'SELECTED', 'configuration': candidate, 'publication_path': str(publication_path),
                  'publication_sha256': _sha(raw)}
    except Exception:
        pass
    if result is not None:
        return result
    baseline_path = baseline_sha256 = baseline = manifest_sha256 = load_configuration = None
    base_path = root = publication_path = raw = publication = path = candidate = index_root = None
    raise LiveSnapshotRefused('live_snapshot_refused') from None


def recheck(path, expected):
    ok = False
    try:
        _read(path, 4096, expected)
        ok = True
    except Exception:
        pass
    if not ok:
        path = expected = None
        raise LiveSnapshotRefused('live_snapshot_refused') from None


def publish(*, enabled=False, baseline_path=None, baseline_sha256=None, baseline=None, manifest_sha256=None,
            candidate_path=None, candidate_sha256=None, candidate=None):
    """Verify a completed candidate and atomically publish its selector, never its artifacts."""
    if enabled is False:
        return {'status': 'OFF'}
    entered = False
    failure = None
    try:
        if (enabled is not True or type(baseline_path) is not str or type(candidate_path) is not str
                or type(baseline_sha256) is not str or not _HEX.fullmatch(baseline_sha256)
                or type(candidate_sha256) is not str or not _HEX.fullmatch(candidate_sha256)):
            raise ValueError()
        bp, cp = pointer_io._absolute(baseline_path), pointer_io._absolute(candidate_path)
        if (not bp.is_absolute() or bp.name != 'shared-session-config.v2.json' or cp.name != bp.name
                or bp.parent.parent != cp.parent.parent or not _GEN.fullmatch(bp.parent.name)
                or not _GEN.fullmatch(cp.parent.name)):
            raise ValueError()
        if (any(Path(candidate[0][key]) != cp.parent / name for key, name in _GENERATION_PATHS)
                or any(Path(baseline[0][key]) != bp.parent / name for key, name in _BASELINE_GENERATION_PATHS)):
            raise ValueError()
        baseline_raw = _read(bp, 65536, baseline_sha256)
        candidate_raw = _read(cp, 65536, candidate_sha256)
        if (baseline[2] != baseline_sha256 or candidate[2] != candidate_sha256
                or baseline[1] != baseline_raw or candidate[1] != candidate_raw):
            raise ValueError()
        index_root = Path(_checked_pair(baseline, candidate, manifest_sha256))
        _validate_roots(bp.parent.parent, Path(baseline[0]['bundle_root']), index_root)
        index_pin = candidate[0]['expected_binding']['pointer_sha256']
        _read(index_root / 'q4-latest.json', 16384, index_pin)
        publication = {'schema': _SCHEMA, 'baseline_sha256': baseline_sha256, 'generation': cp.parent.name,
                       'descriptor_sha256': candidate_sha256, 'index_pointer_sha256': index_pin}
        raw = json.dumps(publication, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')
        root = bp.parent.parent
        target = root / _POINTER
        pointer_io._chain(root)
        if os.path.lexists(target):
            pointer_io._chain(target)
        temp = root / ('.q4-live-current-' + secrets.token_hex(16) + '.tmp')
        entered = True
        fd = os.open(str(temp), os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_BINARY', 0), 0o600)
        with os.fdopen(fd, 'wb') as stream:
            if stream.write(raw) != len(raw):
                raise OSError()
            stream.flush()
            os.fsync(stream.fileno())
        _read(index_root / 'q4-latest.json', 16384, index_pin)
        pointer_io._atomic_replace(temp, target)
        _read(target, 4096, _sha(raw))
        _read(index_root / 'q4-latest.json', 16384, index_pin)
        return {'status': 'PUBLISHED', 'publication_sha256': _sha(raw)}
    except Exception:
        failure = 'unknown' if entered else 'refused'
    except BaseException:
        if entered:
            failure = 'interrupt'
        else:
            raise
    baseline_path = baseline_sha256 = baseline = manifest_sha256 = candidate_path = candidate_sha256 = candidate = None
    bp = cp = baseline_raw = candidate_raw = index_root = index_pin = publication = raw = root = target = temp = fd = None
    if failure == 'unknown':
        raise LiveSnapshotOutcomeUnknown('live_publication_unknown') from None
    if failure == 'interrupt':
        raise LiveSnapshotInterrupted('live_publication_unknown') from None
    raise LiveSnapshotRefused('live_snapshot_refused') from None
