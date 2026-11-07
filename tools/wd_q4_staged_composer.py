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
from pathlib import Path
import re

from sandbox.mind.step2 import shared_capture_adapter as capture
from tools import wd_q4_descriptor_cli as cli
from tools import wd_q4_descriptor_pointer as pointer
from tools import wd_q4_lane_binding_producer as binding_producer
from tools import wd_q4_secure_transport as transport

_STATE_POINTER = Path(r'C:\Python\WD_REBOOT_STATE_CURRENT.json')
_HEX40 = re.compile(r'[0-9a-f]{40}\Z')
_HEX64 = re.compile(r'[0-9a-fA-F]{64}\Z')
_GENERATION = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})\Z')
_LIVE_PUBLICATION = 'q4-live-current.json'  # the live selector's own publication (wd_q4_live_snapshot_publish._POINTER)
_SHARED_POINTER = re.compile(r'candidate-pointer-(?:[0-9a-f]{40}|[0-9a-f]{64})\.json\Z')


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


def _identity_contains(parent, child):
    """Point-in-time ancestor identity test, including Windows 8.3 aliases."""
    identity = os.stat(parent)
    return any(os.path.samestat(identity, os.stat(node)) for node in (child, *child.parents))


def _state_pairs(pairs):
    result, seen = {}, set()
    for key, value in pairs:
        if not key.isascii() or key.lower() in seen:
            raise ValueError()
        seen.add(key.lower())
        result[key] = value
    return result


def _anchored_active(expected_state_sha256, active, store):
    """Check the fixed CURRENT pointer against an independent caller pin."""
    state_raw = cli._read_pinned(str(_STATE_POINTER), 65536, expected_state_sha256, 'source_refused')
    state = json.loads(state_raw.decode('utf-8'), object_pairs_hook=_state_pairs, parse_constant=cli._constant)
    if (type(state) is not dict or type(state.get('schema_version')) is not int or state['schema_version'] != 1
            or type(state.get('source_commit')) is not str or _HEX40.fullmatch(state['source_commit']) is None
            or state.get('final_commit') != state['source_commit']
            or type(state.get('manifest_sha256')) is not str or _HEX64.fullmatch(state['manifest_sha256']) is None
            or type(state.get('final_manifest_sha256')) is not str
            or state['manifest_sha256'].upper() != state['final_manifest_sha256'].upper()):
        raise ValueError()
    current = pointer._absolute(state.get('active_bundle'))
    pointer._chain(current)
    if not current.is_dir() or not os.path.samefile(current, active) or not os.path.samefile(current.parent, store):
        raise ValueError()


def _shared_stage_ok(stage, generation):
    """A shared generations root may hold only earlier generations and their own candidate pointers.

    Every entry must be a generation directory, a regular per-generation candidate pointer, the live
    selector's regular publication file, or its regular persistent publication lock. The composer never
    reads or touches the publication or lock; pending and temporary files and other entries refuse.
    The new generation's directory and pointer must both be absent. Nothing here proves earlier
    generations or the publication valid.
    """
    if type(generation) is not str or _GENERATION.fullmatch(generation) is None:
        return False
    for entry in stage.iterdir():
        pointer._chain(entry)
        if _GENERATION.fullmatch(entry.name):
            if entry.is_symlink() or not entry.is_dir():
                return False
        elif (_SHARED_POINTER.fullmatch(entry.name) or entry.name == _LIVE_PUBLICATION
              or entry.name == 'q4-live-publication.lock'):
            if entry.is_symlink() or not entry.is_file():
                return False
        else:
            return False
    return not any(os.path.lexists(item) for item in
                   (stage / generation, stage / ('candidate-pointer-' + generation + '.json')))


def _check_roots(stage, bundle, root, store, active, shared_generation=None):
    for path in (stage, bundle, root, store, active):
        pointer._chain(path)
    if (not all(path.is_dir() for path in (stage, bundle, root, store, active))
            or not _contains(root, stage) or not _identity_contains(root, stage)
            or any(_contains(root, item) or _contains(item, root)
                   or _identity_contains(root, item) or _identity_contains(item, root)
                   for item in (bundle, store, active))
            or (any(stage.iterdir()) if shared_generation is None
                else not _shared_stage_ok(stage, shared_generation))):
        raise ValueError()


def _exclusive(path, raw):
    fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_BINARY', 0), 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _prepare_staged_card_binding_impl(*, expected_commit, manifest_bytes, expected_manifest_sha256,
        fleet_bytes, expected_fleet_sha256, wrapper_bytes, expected_wrapper_sha256,
        public_plan_bytes, expected_public_plan_sha256):
    """Validate caller-pinned v3 inputs and C2 before any composer write.

    This helper does not build a fixed plan, materialize a descriptor, activate
    a lane, or prove source authenticity or snapshot freshness. The caller must
    independently anchor every supplied pin and manage any later writes.
    """
    from tools import wd_q4_lane_link as link

    if (type(public_plan_bytes) is not bytes or type(expected_public_plan_sha256) is not str
            or _HEX64.fullmatch(expected_public_plan_sha256) is None
            or not hmac.compare_digest(hashlib.sha256(public_plan_bytes).hexdigest(), expected_public_plan_sha256)):
        raise ValueError()
    plan = link._json(public_plan_bytes)
    if (type(plan) is not dict or plan.get('schema') != 'wd.q4-public-wrapper-plan.v3'
            or set(plan) != link._PUBLIC_PLAN_SCHEMAS['wd.q4-public-wrapper-plan.v3']):
        raise ValueError()
    limits = {'pointer': 1024*1024, 'policy': 65536, 'roster': 1024*1024,
              'public_policy': 65536, 'card_decision': 262144, 'card_policy': 65536}
    inputs = {name: cli._read_pinned(plan[name + '_path'], limit, plan[name + '_sha256'], 'source_refused')
              for name, limit in limits.items()}
    producer_args = dict(enabled=True, expected_commit=expected_commit,
        manifest_bytes=manifest_bytes, expected_manifest_sha256=expected_manifest_sha256,
        fleet_bytes=fleet_bytes, expected_fleet_sha256=expected_fleet_sha256,
        wrapper_bytes=wrapper_bytes, expected_wrapper_sha256=expected_wrapper_sha256,
        public_plan_bytes=public_plan_bytes, expected_public_plan_sha256=expected_public_plan_sha256,
        **{name + '_bytes': raw for name, raw in inputs.items()})
    built = binding_producer.build_staged_lane_binding(**producer_args)
    tasks, _ = capture.strict_pins(inputs['policy'], plan['policy_sha256'],
                                   inputs['roster'], plan['roster_sha256'])
    document = link._json(built['binding_bytes'])
    pinned_pointer = link._json(inputs['pointer'])
    artifacts = pinned_pointer['artifacts']
    memory_root = Path(plan['config']['memory'])
    pinned = {}
    for name in ('memory', 'projection', 'descriptor'):
        pin = artifacts[name + '.json']
        if name != 'memory' and pin != document['binding'][name + '_sha256']:
            raise ValueError()
        pinned[name] = cli._read_pinned(str(memory_root / (name + '.json')),
                                        65536 if name == 'descriptor' else transport._MAX_BUNDLE,
                                        pin, 'source_refused')
    bundle = transport.fixed_card_bundle(enabled=True, **inputs, **pinned)
    if type(bundle) is not bytes or binding_producer.build_staged_lane_binding(**producer_args) != built:
        raise ValueError()
    return {'binding_bytes': built['binding_bytes'], 'binding_sha256': built['binding_sha256'],
            'approved_tasks': sorted(tasks), 'bundle_bytes': bundle,
            'bundle_sha256': hashlib.sha256(bundle).hexdigest()}


def _prepare_staged_card_binding(**kwargs):
    """Collapse pre-write validation failures to a fixed, context-free refusal."""
    result = None
    try:
        result = _prepare_staged_card_binding_impl(**kwargs)
    except Exception:
        pass
    if result is None:
        raise ComposeRefused('input_refused') from None
    return result


def compose_staged_card(*, enabled=False, stage_dir=None, generation=None, expected_bundle=None,
        expected_commit=None, expected_manifest_sha256=None, public_plan_path=None,
        expected_public_plan_sha256=None, fixed_plan_bytes=None, expected_fixed_plan_sha256=None,
        expected_host_sha256=None, descriptor_options=None, generation_root=None,
        bundle_store_root=None, active_bundle_root=None, expected_state_sha256=None, shared_stage=False):
    """Materialize a pinned v3 card bundle and descriptor; never activate it.

    A caller supplies an independently pinned fixed plan and v3 public plan.
    Their pins must agree with the actual candidate bundle and a fresh C2
    validation. A failure or interruption after a materialization attempt is
    INCOMPLETE (unknown). This does not enable the guarded backend or prove
    runtime readiness. With ``shared_stage=True`` the stage is one shared
    immutable generations root: it may already hold earlier generations and
    their ``candidate-pointer-GEN.json`` files, which are never touched; any
    other entry, or an existing directory or pointer for this generation,
    refuses before any write, and this run's pointer is
    ``candidate-pointer-GEN.json``. In shared mode the fixed plan and the
    descriptor must name ``GEN/public-plan.json``; the caller's pinned public
    plan bytes are copied there exclusively after the generation artifacts
    and before the pointer, and the copy and the source are re-read against
    the same pin. The default keeps the empty stage, binds the caller's public
    plan path, and uses the fixed ``candidate-pointer.json`` name.
    """
    if enabled is False:
        return {'status': 'OFF'}
    if enabled is not True or (shared_stage is not True and shared_stage is not False):
        raise ComposeRefused('input_refused')
    shared_generation = generation if shared_stage else None
    from tools import wd_q4_descriptor_materializer as materializer
    from tools import wd_q4_shared_descriptor_producer as descriptor_producer
    from tools import wd_q4_lane_caller_cli as lane_caller
    from tools import wd_q4_lane_link as link
    attempted, refusal, result = False, None, None
    try:
        stage, bundle, root, store, active = map(pointer._absolute,
            (stage_dir, expected_bundle, generation_root, bundle_store_root, active_bundle_root))
        _check_roots(stage, bundle, root, store, active, shared_generation)
        _anchored_active(expected_state_sha256, active, store)
        if (type(expected_manifest_sha256) is not str or _HEX64.fullmatch(expected_manifest_sha256) is None
                or expected_manifest_sha256 != expected_manifest_sha256.upper()
                or type(expected_fixed_plan_sha256) is not str
                or _HEX64.fullmatch(expected_fixed_plan_sha256) is None
                or expected_fixed_plan_sha256 != expected_fixed_plan_sha256.lower()
                or type(expected_host_sha256) is not str or _HEX64.fullmatch(expected_host_sha256) is None
                or expected_host_sha256 != expected_host_sha256.lower()
                or type(fixed_plan_bytes) is not bytes
                or not hmac.compare_digest(hashlib.sha256(fixed_plan_bytes).hexdigest(), expected_fixed_plan_sha256)
                or type(descriptor_options) is not dict or set(descriptor_options) != cli._DESCRIPTOR_KEYS
                or type(descriptor_options.get('interpreter_max_bytes')) is not int
                or not 0 < descriptor_options['interpreter_max_bytes'] <= lane_caller.MAX_INTERPRETER
                # the lane caller's _configuration caps the descriptor timeout at 600 s (strict int)
                or type(descriptor_options.get('timeout_seconds')) is not int
                or not 1 <= descriptor_options['timeout_seconds'] <= 600):
            raise ValueError()
        manifest = cli._read_pinned(str(bundle / 'deployment-manifest.json'), 2*1024*1024,
                                    expected_manifest_sha256.lower(), 'source_refused')
        manifest_doc = link._json(manifest)
        fleet_pin = manifest_doc['files']['wd-fleet.json'].lower()
        fleet = cli._read_pinned(str(bundle / 'wd-fleet.json'), cli.LIMITS['fleet'], fleet_pin, 'source_refused')
        wrapper = cli._read_regular(str(bundle / 'Invoke-WdQ4Query.ps1'), 1024*1024, 'source_refused')
        public_plan = cli._read_pinned(public_plan_path, 65536, expected_public_plan_sha256, 'source_refused')
        # shared mode binds the new generation's own copy; the default binds the caller's path
        public_copy = stage / generation / 'public-plan.json' if shared_stage else None
        bound_public = public_plan_path if public_copy is None else public_copy
        fixed = transport._fixed_plan(fixed_plan_bytes)
        if fixed['backend'] != 'staged-card-wrapper-v1':
            raise ValueError()
        prep_args = dict(expected_commit=expected_commit, manifest_bytes=manifest,
            expected_manifest_sha256=expected_manifest_sha256.lower(), fleet_bytes=fleet,
            expected_fleet_sha256=fleet_pin, wrapper_bytes=wrapper,
            expected_wrapper_sha256=hashlib.sha256(wrapper).hexdigest(), public_plan_bytes=public_plan,
            expected_public_plan_sha256=expected_public_plan_sha256)
        prepared = _prepare_staged_card_binding(**prep_args)
        pins = link._json(prepared['binding_bytes'])['binding']
        public = link._json(public_plan)
        config, outer = public['config'], descriptor_options['outer']
        if (descriptor_options['expected_binding'] != pins
                or not _same(descriptor_options['bundle_root'], bundle)
                or not _same(descriptor_options['public_plan'], bound_public)
                or not _same(fixed['bundle_root'], bundle) or fixed['bundle_commit'] != expected_commit
                or fixed['deployment_manifest_sha256'] != expected_manifest_sha256.lower()
                or fixed['wrapper_sha256'] != prep_args['expected_wrapper_sha256']
                or not _same(fixed['wrapper_path'], bundle / 'Invoke-WdQ4Query.ps1')
                or not _same(fixed['public_plan_path'], bound_public)
                or fixed['public_plan_sha256'] != expected_public_plan_sha256
                or fixed['host_sha256'] != expected_host_sha256
                or fixed['bundle_sha256'] != prepared['bundle_sha256']
                or fixed['bundle_bytes'] != len(prepared['bundle_bytes'])
                or fixed['approved_tasks'] != prepared['approved_tasks']
                or any(fixed[left] != pins[right] for left, right in (
                    ('expected_pointer_sha256','pointer_sha256'), ('policy_sha256','policy_sha256'),
                    ('roster_sha256','roster_sha256'), ('expected_public_policy_sha256','public_policy_sha256'),
                    ('expected_source_sha256','source_anchor_sha256'), ('expected_encoder_sha256','encoder_sha256'),
                    ('card_decision_sha256','card_decision_sha256'), ('card_policy_sha256','card_policy_sha256')))
                or any(fixed[left] != config[right] for left, right in (
                    ('interpreter_sha256','interpreter_sha256'), ('verifier_sha256','verifier_sha256'),
                    ('runtime_manifest_sha256','runtime_manifest_sha256'), ('runtime_generation','runtime_generation'),
                    ('runtime_revision','runtime_revision')))
                or not _same(fixed['runtime_root'], config['runtime_root'])
                or not _same(fixed['runtime_manifest_path'], config['runtime_manifest'])
                or fixed['interpreter_sha256'] != descriptor_options['interpreter_sha256']
                or not _same(config['interpreter'], descriptor_options['interpreter'])
                or fixed['runtime_manifest_sha256'] != descriptor_options['runtime_manifest_sha256']
                or not _same(fixed['runtime_manifest_path'], descriptor_options['runtime_manifest_path'])
                or fixed['runtime_generation'] != outer['runtime-generation']
                or fixed['runtime_revision'] != outer['runtime-revision']
                or not _same(fixed['runtime_root'], outer['runtime-root'])
                or fixed['verifier_sha256'] != outer['verifier-sha256']):
            raise ValueError()
        peer = bundle / 'tools-bootstrap' / 'tools' / 'wd_q4_secure_transport.py'
        transport._read_staged_backend(str(peer), fixed)
        paths = materializer.plan_generation_paths(str(stage), generation)
        built = descriptor_producer.build_shared_descriptor(enabled=True, fleet_bytes=fleet,
            expected_fleet_sha256=fleet_pin, lanes=sorted(link._LANES), binding_path=paths['binding_path'],
            binding_sha256=prepared['binding_sha256'], fixed_plan_path=paths['fixed_plan_path'],
            fixed_plan_sha256=expected_fixed_plan_sha256, snapshot_path=paths['snapshot_path'],
            snapshot_sha256=prepared['bundle_sha256'], **descriptor_options)
        if (_prepare_staged_card_binding(**prep_args) != prepared
                or cli._read_pinned(public_plan_path, 65536, expected_public_plan_sha256, 'source_refused') != public_plan
                or transport._read_staged_backend(str(peer), fixed) is None):
            raise ValueError()
        _anchored_active(expected_state_sha256, active, store)
        _check_roots(stage, bundle, root, store, active, shared_generation)
        attempted = True
        out = materializer.materialize_descriptor(enabled=True, target_dir=str(stage), generation=generation,
            artifacts={'binding': prepared['binding_bytes'], 'fixed_plan': fixed_plan_bytes,
                       'snapshot': prepared['bundle_bytes']}, descriptor_bytes=built['descriptor_bytes'],
            expected_descriptor_sha256=built['descriptor_sha256'])
        if out.get('status') != 'MATERIALIZED' or out.get('descriptor_path') != paths['descriptor_path']:
            raise ValueError()
        if public_copy is not None:
            _exclusive(public_copy, public_plan)
            if (cli._read_pinned(str(public_copy), 65536, expected_public_plan_sha256, 'source_refused') != public_plan
                    or cli._read_pinned(public_plan_path, 65536, expected_public_plan_sha256,
                                        'source_refused') != public_plan):
                raise ValueError()
        generated = pointer.prepare_staged_descriptor_pointer(enabled=True, expected_bundle=str(bundle),
            expected_commit=expected_commit, expected_manifest_sha256=expected_manifest_sha256,
            generations_root=str(stage), generation=generation, descriptor_sha256=built['descriptor_sha256'])
        if hashlib.sha256(generated['pointer_bytes']).hexdigest() != generated['pointer_sha256']:
            raise ValueError()
        pointer_path = stage / ('candidate-pointer-' + generation + '.json' if shared_stage else 'candidate-pointer.json')
        _exclusive(pointer_path, generated['pointer_bytes'])
        if cli._read_pinned(str(pointer_path), 4096, generated['pointer_sha256'], 'source_refused') != generated['pointer_bytes']:
            raise ValueError()
        result = {'status': 'PREPARED', 'bundle_commit': expected_commit,
                  'manifest_sha256': expected_manifest_sha256, 'binding_sha256': prepared['binding_sha256'],
                  'descriptor_path': paths['descriptor_path'], 'descriptor_sha256': built['descriptor_sha256'],
                  'pointer_path': str(pointer_path), 'pointer_sha256': generated['pointer_sha256']}
    except BaseException as exc:
        if not attempted and not isinstance(exc, Exception):
            raise  # an interrupt before any write changed nothing
        refusal = ComposeIncomplete if attempted else ComposeRefused
    if refusal:
        raise refusal('staging_incomplete' if attempted else 'input_refused') from None
    return result


def compose(*, enabled=False, request_path=None, stage_dir=None, expected_bundle=None,
            expected_commit=None, expected_manifest_sha256=None, expected_public_plan_sha256=None,
            generation_root=None, bundle_store_root=None, active_bundle_root=None, expected_state_sha256=None):
    """Return OFF or PREPARED with exact candidate paths/pins; never update CURRENT.

    The strict request uses stage_dir as target_dir and stage_dir/binding-source.json
    as binding_path; the binding hash placeholder is replaced by the computed pin.
    The stage directory must pre-exist, be empty, be disjoint from the bundle,
    and remain at that exact path as the permanent generations root.
    generation_root, bundle_store_root and active_bundle_root are explicit,
    independently preflighted paths; the stage must remain in generation_root,
    which must not overlap the bundle store, active bundle or candidate bundle.
    The fixed CURRENT state pointer must match an independent expected_state_sha256
    and the active and store directory identities. The caller independently pins
    the state bytes; these checks are point-in-time and are not an atomic lock.
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
        _check_roots(stage, bundle, root, store, active)
        _anchored_active(expected_state_sha256, active, store)
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
        _anchored_active(expected_state_sha256, active, store)
        _check_roots(stage, bundle, root, store, active)
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
