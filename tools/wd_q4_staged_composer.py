"""Compose a hash-bound staged Q4 generation without touching active pointers.

The caller supplies independently pinned candidate bytes via an existing strict
Fable descriptor request, a staged bundle, and an empty staging directory. This
module has no environment discovery and never activates or installs anything.
A PREPARED result is byte consistency, not a runtime, freshness or identity proof.
A failure after the first write is INCOMPLETE: inspect the isolated staging
folder before any retry; partial generation content is never silently removed.
The stage directory is also the permanent generations root: the descriptor pins
absolute artifact paths under it. Do not move, rename or delete it after staging.
"""
import hashlib
import hmac
import json
import os

from sandbox.mind.step2 import shared_capture_adapter as capture
from tools import wd_q4_descriptor_cli as cli
from tools import wd_q4_descriptor_pointer as pointer
from tools import wd_q4_lane_binding_producer as binding_producer
from tools import wd_q4_secure_transport as transport


class ComposeRefused(ValueError):
    """Inputs were refused before any attempted write."""


class ComposeIncomplete(RuntimeError):
    """A write was attempted; staged files may be partial."""


def _same(a, b):
    return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))


def _contains(parent, child):
    try:
        return os.path.normcase(os.path.commonpath((str(parent), str(child)))) == os.path.normcase(str(parent))
    except ValueError:
        return False


def _exclusive(path, raw):
    fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_BINARY', 0), 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def compose(*, enabled=False, request_path=None, stage_dir=None, expected_bundle=None,
            expected_commit=None, expected_manifest_sha256=None, expected_public_plan_sha256=None,
            generation_root=None, bundle_store_root=None, active_bundle_root=None):
    """Return OFF or PREPARED with exact candidate paths/pins; never update CURRENT.

    The strict request uses stage_dir as target_dir and stage_dir/binding-source.json
    as binding_path; the binding hash placeholder is replaced by the computed pin.
    The stage directory must pre-exist, be empty, be disjoint from the bundle,
    and remain at that exact path as the permanent generations root.
    generation_root, bundle_store_root and active_bundle_root are explicit,
    independently preflighted paths; the stage must remain in generation_root,
    which must not overlap the bundle store, active bundle or candidate bundle.
    The caller must establish which active bundle is current; these supplied
    paths alone do not authenticate machine state and are not an atomic lock.
    Inputs must be independently authenticated by the caller, not inferred here.
    Process termination or KeyboardInterrupt gives no successful receipt; the caller
    must inspect the staging folder before another attempt. Path checking and an
    exclusive file create are not protection against a hostile same-user actor.
    """
    if enabled is False:
        return {'status': 'OFF'}
    if enabled is not True:
        raise ComposeRefused('input_refused')
    attempted = False
    refusal = None
    result = None
    manifest = fleet = wrapper = public_plan = snapshot = binding_bytes = decoded = None
    request = built = None
    try:
        stage = pointer._absolute(stage_dir)
        bundle = pointer._absolute(expected_bundle)
        root = pointer._absolute(generation_root)
        store = pointer._absolute(bundle_store_root)
        active = pointer._absolute(active_bundle_root)
        for path in (stage, bundle, root, store, active):
            pointer._chain(path)
        if (not all(path.is_dir() for path in (stage, bundle, root, store, active))
                or not _contains(root, stage)
                or any(_contains(root, item) or _contains(item, root) for item in (bundle, store, active))
                or any(stage.iterdir())):
            raise ValueError()
        request = cli._request(request_path)
        if (not _same(request['target_dir'], stage) or not _same(request['descriptor']['bundle_root'], bundle)
                or not _same(request['fleet_path'], bundle / 'wd-fleet.json')
                or not _same(request['binding_path'], stage / 'binding-source.json')):
            raise ValueError()
        if (type(expected_manifest_sha256) is not str or len(expected_manifest_sha256) != 64
                or expected_manifest_sha256 != expected_manifest_sha256.upper()):
            raise ValueError()
        manifest = cli._read_pinned(str(bundle / 'deployment-manifest.json'), 2 * 1024 * 1024,
                                    expected_manifest_sha256.lower(), 'source_refused')
        fleet = cli._read_pinned(request['fleet_path'], cli.LIMITS['fleet'], request['fleet_sha256'], 'source_refused')
        wrapper = cli._read_regular(str(bundle / 'Invoke-WdQ4Query.ps1'), 1024 * 1024, 'source_refused')
        public_plan = cli._read_pinned(request['descriptor']['public_plan'], 65536,
                                       expected_public_plan_sha256, 'source_refused')
        snapshot = cli._read_pinned(request['snapshot_path'], cli.LIMITS['snapshot'], request['snapshot_sha256'], 'source_refused')
        built = binding_producer.build_lane_binding(enabled=True, expected_commit=expected_commit,
            manifest_bytes=manifest, expected_manifest_sha256=expected_manifest_sha256.lower(),
            fleet_bytes=fleet, expected_fleet_sha256=request['fleet_sha256'], wrapper_bytes=wrapper,
            expected_wrapper_sha256=hashlib.sha256(wrapper).hexdigest(), public_plan_bytes=public_plan,
            expected_public_plan_sha256=expected_public_plan_sha256, snapshot_bytes=snapshot,
            expected_snapshot_sha256=request['snapshot_sha256'])
        binding_bytes = built['binding_bytes']
        pins = json.loads(binding_bytes.decode('ascii'))['binding']
        decoded = transport._fixed_bundle_decode(snapshot)
        tasks, _ = capture.strict_pins(decoded['policy_bytes'], pins['policy_sha256'],
                                       decoded['roster_bytes'], pins['roster_sha256'])
        fixed = request['fixed_plan']
        if (request['descriptor']['expected_binding'] != pins or fixed['bundle_bytes'] != len(snapshot)
                or fixed['bundle_sha256'] != request['snapshot_sha256']
                or set(fixed['approved_tasks']) != tasks
                or any(fixed[left] != pins[right] for left, right in (
                    ('expected_pointer_sha256', 'pointer_sha256'), ('policy_sha256', 'policy_sha256'),
                    ('roster_sha256', 'roster_sha256'), ('expected_public_policy_sha256', 'public_policy_sha256'),
                    ('expected_source_sha256', 'source_anchor_sha256'), ('expected_encoder_sha256', 'encoder_sha256')))):
            raise ValueError()
        attempted = True
        _exclusive(stage / 'binding-source.json', binding_bytes)
        request['binding_sha256'] = built['binding_sha256']
        materialized = cli._compose(request)
        prepared = pointer.prepare_staged_descriptor_pointer(enabled=True, expected_bundle=str(bundle),
            expected_commit=expected_commit, expected_manifest_sha256=expected_manifest_sha256,
            generations_root=str(stage), generation=request['generation'], descriptor_sha256=materialized['descriptor_sha256'])
        raw = prepared['pointer_bytes']
        if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), prepared['pointer_sha256']):
            raise ValueError()
        pointer_path = stage / 'candidate-pointer.json'
        _exclusive(pointer_path, raw)
        result = {'status': 'PREPARED', 'bundle_commit': expected_commit,
                  'manifest_sha256': expected_manifest_sha256, 'binding_sha256': built['binding_sha256'],
                  'descriptor_path': materialized['descriptor_path'], 'descriptor_sha256': materialized['descriptor_sha256'],
                  'pointer_path': str(pointer_path), 'pointer_sha256': prepared['pointer_sha256']}
    except Exception:
        refusal = ComposeIncomplete if attempted else ComposeRefused
    finally:
        manifest = fleet = wrapper = public_plan = snapshot = binding_bytes = decoded = None
        request = built = None
    if refusal:
        raise refusal('staging_incomplete' if attempted else 'input_refused')
    return result
