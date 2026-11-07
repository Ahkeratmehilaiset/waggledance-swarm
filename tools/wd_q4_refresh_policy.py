"""Default-off consistency checks for independently pinned Q4 refresh policy.

The caller must independently authorize and protect all three expected hashes.
This checks bytes and path separation only: it does not authenticate the source,
publisher, operator, file owners, or prevent same-user check/use races. It does
not publish, activate, create files, grant authority, or check freshness.
"""
import hashlib
import hmac
import json
import os
from pathlib import Path
import re

from tools import wd_q4_descriptor_pointer as pointer

_SCHEMA = 'wd.q4-refresh-policy.v1'
_KEYS = frozenset({'schema', 'baseline_descriptor_sha256', 'deployment_manifest_sha256',
                   'snapshot_root', 'anchor_root', 'source_path'})
_HEX = re.compile(r'[0-9a-f]{64}\Z')


def _pin(value):
    if type(value) is not str or _HEX.fullmatch(value) is None:
        raise ValueError()
    return value


def _digest(value):
    return hashlib.sha256(value).hexdigest()


def _disjoint(left, right):
    left, right = Path(left), Path(right)
    a, b = os.path.normcase(str(left)), os.path.normcase(str(right))
    try:
        if os.path.commonpath((a, b)) in (a, b):
            return False
    except ValueError:
        pass
    return not (any(os.path.samefile(left, part) for part in (right, *right.parents))
                or any(os.path.samefile(right, part) for part in (left, *left.parents)))


def _observed(path, directory):
    pointer._chain(path)
    if not (path.is_dir() if directory else path.is_file()):
        raise ValueError()
    info = path.stat()
    return (info.st_dev, info.st_ino)


def validate(*, enabled=False, policy_bytes=None, expected_policy_sha256=None,
             baseline_path=None, expected_baseline_sha256=None, expected_manifest_sha256=None):
    """Validate one explicit policy against caller-pinned descriptor and bundle."""
    if enabled is False:
        return {'status':'OFF'}
    try:
        if enabled is not True or type(policy_bytes) is not bytes or not 0 < len(policy_bytes) <= 16384:
            raise ValueError()
        policy_pin, baseline_pin, manifest_pin = map(_pin, (expected_policy_sha256,
            expected_baseline_sha256, expected_manifest_sha256))
        if not hmac.compare_digest(_digest(policy_bytes), policy_pin):
            raise ValueError()
        policy = pointer._json(policy_bytes)
        if (set(policy) != _KEYS or policy['schema'] != _SCHEMA
                or policy['baseline_descriptor_sha256'] != baseline_pin
                or policy['deployment_manifest_sha256'] != manifest_pin
                or json.dumps(policy, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                              allow_nan=False).encode('ascii') != policy_bytes):
            raise ValueError()
        baseline = pointer._absolute(baseline_path)
        if (baseline.name != 'shared-session-config.v2.json' or not pointer._GEN.fullmatch(baseline.parent.name)):
            raise ValueError()
        raw = pointer._read(baseline, 65536)
        if not hmac.compare_digest(_digest(raw), baseline_pin):
            raise ValueError()
        descriptor = pointer._json(raw)
        if (set(descriptor) != pointer._DESC_KEYS or descriptor['schema'] != 'wd.q4-shared-session-config.v2'
                or type(descriptor['outer']) is not dict):
            raise ValueError()
        bundle = pointer._absolute(descriptor['bundle_root'])
        runtime = pointer._absolute(descriptor['outer']['runtime-root'])
        stage = baseline.parent.parent
        manifest_path = bundle / 'deployment-manifest.json'
        manifest_raw = pointer._read(manifest_path, 4 * 1024 * 1024)
        if not hmac.compare_digest(_digest(manifest_raw), manifest_pin):
            raise ValueError()
        manifest = pointer._json(manifest_raw)
        if (type(manifest.get('schema_version')) is not int or manifest['schema_version'] != 1
                or type(manifest.get('files')) is not dict
                or type(manifest['files'].get('wd-fleet.json')) is not str
                or manifest['files']['wd-fleet.json'].lower() != descriptor['fleet_sha256']):
            raise ValueError()
        # The existing read-only staged verifier also binds the descriptor's
        # fleet and lane-binding bytes to this exact bundle commit/manifest.
        # It does not publish the resulting pointer or confer authority.
        verify_args = dict(state_path=None, expected_bundle=str(bundle), expected_commit=manifest.get('source_commit'),
                           expected_manifest_sha256=manifest_pin.upper(), generations_root=str(stage),
                           generation=baseline.parent.name, descriptor_sha256=baseline_pin, staged=True)
        verified = pointer._verify(**verify_args)
        snapshot = pointer._absolute(policy['snapshot_root'])
        anchor = pointer._absolute(policy['anchor_root'])
        source = pointer._absolute(policy['source_path'])
        paths = ((baseline, False), (manifest_path, False), (bundle, True), (runtime, True),
                 (stage, True), (snapshot, True), (anchor, True), (source, False))
        identities = [_observed(path, directory) for path, directory in paths]
        for trusted in (snapshot, anchor):
            for protected in (bundle, runtime, stage, source):
                if not _disjoint(trusted, protected):
                    raise ValueError()
        if not _disjoint(snapshot, anchor):
            raise ValueError()
        if any(not _disjoint(source, protected) for protected in (bundle, runtime, stage)):
            raise ValueError()
        if (pointer._read(baseline, 65536) != raw or pointer._read(manifest_path, 4 * 1024 * 1024) != manifest_raw
                or [_observed(path, directory) for path, directory in paths] != identities
                or pointer._verify(**verify_args) != verified):
            raise ValueError()
        return {'status':'VALIDATED', 'policy_sha256':policy_pin, 'baseline_descriptor_sha256':baseline_pin,
                'deployment_manifest_sha256':manifest_pin, 'snapshot_root':str(snapshot), 'anchor_root':str(anchor),
                'source_path':str(source), 'source_authentication':'UNKNOWN', 'publisher_authentication':'UNKNOWN',
                'freshness':'UNKNOWN'}
    except Exception:
        raise ValueError('refresh_policy_refused') from None
