"""Fixed, opt-in wrapper for one hash-bound genesis attempt.

The source prefix pin is checked before and after bootstrap; it is not an
atomic admission pin inside bootstrap. A post-admission mismatch is UNKNOWN.
This module never prints source content or paths and cannot authenticate pins.

Late public (optional config key resume_genesis_skip, default absent = off and
the call is unchanged): it requires allow_partial_genesis and also pins the
running status runner to the manifest. A bootstrap refusal
genesis_skip_progress is verified here (no sidecar or private tuple exists, the
config source pin still holds, and the private skip receipt names this source
identity, generation and pins and re-hashes from the source) and reported as
GENESIS_SKIP_PROGRESS with exit code 4: nothing is published and the one-shot
is not spent, so the same pinned config is run again to resume. Only the
receipt sha256 is printed, never its offset or prefix digest. After a
publication the private route must bind the current receipt exactly when
genesis started past offset zero.
"""
import hashlib
import hmac
import json
import os
import datetime as dt
from pathlib import Path
import re
import sys

_HEX = re.compile(r'[0-9a-f]{64}\Z')
_KEYS = frozenset({'schema', 'bundle_root', 'manifest_sha256', 'source_path',
    'source_sha256', 'source_bytes', 'source_file_identity', 'generation_path', 'generation', 'checkpoint_root',
    'private_state_root', 'trusted_model_root', 'policy_path', 'policy_sha256',
    'public_policy_path', 'public_policy_sha256', 'fleet_sha256', 'max_prefix_bytes',
    'max_bytes', 'max_rows', 'plan_template', 'renewable', 'cutoff_utc', 'batch_rows',
    'min_headroom_bytes', 'allow_partial_genesis'})
_OPTIONAL = frozenset({'resume_genesis_skip'})


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _pairs(items):
    out = {}
    for k, v in items:
        if k in out:
            raise ValueError()
        out[k] = v
    return out


def _doc(raw):
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    if type(value) is not dict:
        raise ValueError()
    return value


def _read_config(path, digest):
    from tools.wd_q4_descriptor_cli import _read_pinned
    return _doc(_read_pinned(path, 65536, digest, 'config_refused'))


def _c_path(value):
    if (type(value) is not str or not value or os.name != 'nt'
            or Path(value).drive.upper() != 'C:' or not Path(value).is_absolute()):
        raise ValueError()
    return value


def _prepare(path, digest):
    from tools.wd_q4_descriptor_cli import _read_pinned
    from tools import wd_q4_guard_state_binding as roots
    from tools import wd_q4_status_prefix_runner as runner
    from waggledance.core.bridge_log_reader import _file_identity
    config = _read_config(_c_path(path), digest)
    if (frozenset(config) not in (_KEYS, _KEYS | _OPTIONAL) or config['schema'] != 'wd.q4-genesis-cli.v1'
            or type(config.get('resume_genesis_skip', False)) is not bool
            or (config.get('resume_genesis_skip') is True and config['allow_partial_genesis'] is not True)):
        raise ValueError()
    for key in ('manifest_sha256', 'source_sha256', 'policy_sha256', 'public_policy_sha256', 'fleet_sha256'):
        if type(config[key]) is not str or not _HEX.fullmatch(config[key]):
            raise ValueError()
    if (type(config['source_bytes']) is not int or not 0 < config['source_bytes'] <= config['max_prefix_bytes']
            or type(config['source_file_identity']) is not str
            or not runner._IDENTITY.fullmatch(config['source_file_identity'])
            or type(config['renewable']) is not bool or type(config['plan_template']) is not dict
            or type(config['batch_rows']) is not bool or type(config['allow_partial_genesis']) is not bool
            or type(config['min_headroom_bytes']) is not int or config['min_headroom_bytes'] < 1):
        raise ValueError()
    cutoff = config['cutoff_utc']
    if cutoff is not None:
        if (type(cutoff) is not str or re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', cutoff) is None
                or dt.datetime.strptime(cutoff, '%Y-%m-%dT%H:%M:%SZ').strftime('%Y-%m-%dT%H:%M:%SZ') != cutoff):
            raise ValueError()
    if ((config['batch_rows'] and cutoff is None) or
            (config['allow_partial_genesis'] and (not config['batch_rows'] or cutoff is None))):
        raise ValueError()
    for key in ('bundle_root', 'source_path', 'generation_path', 'checkpoint_root',
                'private_state_root', 'trusted_model_root', 'policy_path', 'public_policy_path'):
        _c_path(config[key])
    _c_path(config['plan_template']['encoder']['model_dir'])
    parents = [roots._root(config['source_path']).parent,
               roots._root(config['generation_path']).parent]
    outputs = [roots._root(config[key]) for key in ('checkpoint_root', 'private_state_root', 'trusted_model_root')]
    for root in parents + outputs:
        roots._observe(root)
    if (not runner._separate(*parents) or
            any(not runner._separate(parent, root) for parent in parents for root in outputs) or
            any(not runner._separate(left, right) for i, left in enumerate(outputs) for right in outputs[i + 1:])):
        raise ValueError()
    from sandbox.mind.step2 import wd_q4_prefix_build_child as child
    plan = config['plan_template']
    if ('pins' in plan or plan.get('generation') != config['generation'] or
            plan.get('expected_pointer_sha256') is not None or
            plan.get('max_prefix_bytes') != config['max_prefix_bytes'] or
            not child._plan_shape({**plan, 'pins': {name: '0' * 64 for name in child.PIN_KEYS}})):
        raise ValueError()
    encoder = plan['encoder']
    model_files = child._model_root_bound(encoder['model_dir'], config['trusted_model_root'],
                                           config['checkpoint_root'], config['private_state_root'])
    if (model_files is None or encoder['assets'][child.WEIGHTS_FILE] != encoder['weights_sha256'] or
            not child._assets_bound(encoder['model_dir'], encoder['assets'], model_files)):
        raise ValueError()
    # Each root is independently checked for links and Windows path identity;
    # the existing bootstrap also checks all producer/output/model separation.
    bundle = roots._root(config['bundle_root'])
    roots._observe(bundle)
    manifest = _doc(_read_pinned(str(bundle / 'deployment-manifest.json'), 2 * 1024 * 1024,
                                 config['manifest_sha256'], 'manifest_refused'))
    files = manifest.get('files')
    if type(files) is not dict or os.environ.get('WD_REBOOT_EXPECTED_MANIFEST_HASH', '').lower() != config['manifest_sha256']:
        raise ValueError()
    fleet_hash = files.get('wd-fleet.json', '').lower()
    if fleet_hash != config['fleet_sha256']:
        raise ValueError()
    fleet = _read_pinned(str(bundle / 'wd-fleet.json'), 1024 * 1024, fleet_hash, 'fleet_refused')
    policy = _read_pinned(config['policy_path'], 65536, config['policy_sha256'], 'policy_refused')
    public = _read_pinned(config['public_policy_path'], 65536, config['public_policy_sha256'], 'policy_refused')
    runner._pinned(policy, config['policy_sha256'], runner._POLICY_SHA)
    runner._pinned(public, config['public_policy_sha256'], runner._PUBLIC_SHA)
    runner._pinned(fleet, fleet_hash, runner._FLEET_SHA, limit=1024 * 1024)
    # The running modules themselves must be the exact manifest-listed files.
    from tools import wd_q4_genesis_bootstrap as genesis
    pinned_code = [('tools/wd_q4_genesis_cli.py', __file__), ('tools/wd_q4_genesis_bootstrap.py', genesis.__file__)]
    if config.get('resume_genesis_skip') is True:
        # The runner decides whether a genesis past offset zero is admitted.
        pinned_code.append(('tools/wd_q4_status_prefix_runner.py', runner.__file__))
    for relative, actual in pinned_code:
        code = bundle / 'tools-bootstrap' / relative
        pin = files.get('tools-bootstrap/' + relative, '').lower()
        if not _HEX.fullmatch(pin) or not os.path.samefile(code, actual):
            raise ValueError()
        _read_pinned(str(code), 1024 * 1024, pin, 'code_refused')
    source = runner._plain_file(config['source_path'])
    with source.open('rb') as f:
        info = os.fstat(f.fileno())
        if info.st_size < config['source_bytes'] or _file_identity(f) != config['source_file_identity']:
            raise ValueError()
        actual = genesis._snapshot(f, config['source_bytes'])
        if not hmac.compare_digest(actual, config['source_sha256']):
            raise ValueError()
    return config, policy, public, fleet, genesis


def _verify_result(config, genesis, result, policy, fleet):
    """Check the pinned start prefix and privately verified end after admission.

    A valid append is allowed; a replaced, truncated or altered prefix is not.
    This is a cooperating-file check, not same-user adversary authentication.
    """
    from tools import wd_q4_status_prefix_runner as runner
    from tools import wd_q4_roster_from_fleet as roster_producer
    from tools.wd_q4_descriptor_cli import _read_pinned
    from sandbox.mind.step2 import shared_capture_adapter as capture, shared_index_checkpoint as checkpoint
    from waggledance.core.bridge_log_reader import _file_identity
    if type(result) is not dict or result.get('status') != 'PUBLISHED_GENESIS':
        raise ValueError()
    pointer_hash = result.get('pointer_sha256')
    tuple_hash = result.get('genesis_tuple_sha256')
    if any(type(h) is not str or not _HEX.fullmatch(h) for h in (pointer_hash, tuple_hash)):
        raise ValueError()
    private_tuple = _doc(_read_pinned(str(Path(config['private_state_root']) / 'q4-genesis-tuple-v1.json'),
                                     1024 * 1024, tuple_hash, 'tuple_refused'))
    if private_tuple.get('schema') != 'wd.q4-genesis-private-tuple.v1':
        raise ValueError()
    if config.get('resume_genesis_skip') is True:
        _verify_skip_binding(config, private_tuple)
    roster, roster_hash = roster_producer.build_roster_snapshot(fleet, config['fleet_sha256'], enabled=True)
    tasks, _ = capture.strict_pins(policy, config['policy_sha256'], roster, roster_hash)
    _, end_pin = checkpoint.read_verified_prefix_pointer(config['checkpoint_root'], tasks,
        expected_pointer_sha256=pointer_hash, policy_bytes=policy, policy_sha256=config['policy_sha256'],
        roster_bytes=roster, roster_sha256=roster_hash, expected_public_policy_sha256=config['public_policy_sha256'])
    end, end_hash = end_pin.cursor.offset, end_pin.sha256
    if (type(end) is not int or not 0 <= end <= config['max_prefix_bytes'] or
            end_pin.cursor.file_identity != config['source_file_identity'] or
            end_pin.cursor.generation != config['generation'] or
            type(end_hash) is not str or not _HEX.fullmatch(end_hash)):
        raise ValueError()
    snapshot_bytes = result.get('genesis_snapshot_bytes')
    fully_covered = result.get('genesis_initial_prefix_fully_covered')
    if (type(snapshot_bytes) is not int or not config['source_bytes'] <= snapshot_bytes <= config['max_prefix_bytes']
            or type(fully_covered) is not bool or fully_covered != (end >= snapshot_bytes)
            or (not fully_covered and (not config['allow_partial_genesis'] or
                                       result.get('coverage') != 'partial_ready_prefix'))):
        raise ValueError()
    source = runner._plain_file(config['source_path'])
    with source.open('rb') as stream:
        if (os.fstat(stream.fileno()).st_size < max(end, config['source_bytes']) or
                _file_identity(stream) != config['source_file_identity'] or
                not hmac.compare_digest(genesis._snapshot(stream, config['source_bytes']), config['source_sha256']) or
                not hmac.compare_digest(genesis._snapshot(stream, end), end_hash)):
            raise ValueError()
    return fully_covered


def _skip_receipt(config, genesis):
    """The current skip receipt bytes after verifying them against this config and the source; ValueError otherwise."""
    from tools import wd_q4_status_prefix_runner as runner
    from waggledance.core.bridge_log_reader import _file_identity
    raw = runner._read_genesis_skip(Path(config['private_state_root']))
    value = runner._genesis_skip(raw)
    if (value['file_identity'] != config['source_file_identity'] or value['generation'] != config['generation']
            or value['policy_sha256'] != config['policy_sha256']
            or value['public_policy_sha256'] != config['public_policy_sha256']
            or value['offset'] > config['max_prefix_bytes']):
        raise ValueError()
    source = runner._plain_file(config['source_path'])
    with source.open('rb') as stream:
        if (os.fstat(stream.fileno()).st_size < max(value['offset'], config['source_bytes'])
                or _file_identity(stream) != config['source_file_identity']
                or not hmac.compare_digest(genesis._snapshot(stream, config['source_bytes']), config['source_sha256'])
                or not hmac.compare_digest(genesis._snapshot(stream, value['offset']), value['prefix_sha256'])):
            raise ValueError()
    return raw


def _verify_skip_progress(config, genesis):
    """genesis_skip_progress spent nothing: no sidecar or tuple, and a receipt that matches this config."""
    from tools import wd_q4_status_prefix_runner as runner
    if (not runner._absent(Path(config['generation_path']))
            or not runner._absent(Path(config['private_state_root']) / 'q4-genesis-tuple-v1.json')):
        raise ValueError()
    return {'skip_receipt_sha256': _sha(_skip_receipt(config, genesis))}


def _verify_skip_binding(config, private_tuple):
    """A genesis past offset zero binds the current receipt exactly; one at offset zero binds none."""
    import base64
    from tools import wd_q4_status_prefix_runner as runner
    entry = private_tuple.get('route')
    if type(entry) is not dict or type(entry.get('bytes_b64')) is not str or type(entry.get('sha256')) is not str:
        raise ValueError()
    route_raw = base64.b64decode(entry['bytes_b64'], validate=True)
    if not hmac.compare_digest(_sha(route_raw), entry['sha256']):
        raise ValueError()
    route = _doc(route_raw)
    cursor = route.get('cursor')
    if type(cursor) is not dict or type(cursor.get('offset')) is not int:
        raise ValueError()
    bound = route.get('genesis_skip_sha256')
    if cursor['offset'] == 0:
        if bound is not None:
            raise ValueError()
        return
    raw = runner._read_genesis_skip(Path(config['private_state_root']))
    value = runner._genesis_skip(raw)
    if (type(bound) is not str or not hmac.compare_digest(_sha(raw), bound) or value['offset'] != cursor['offset']
            or value['prefix_sha256'] != route.get('prefix_sha256')
            or value['file_identity'] != config['source_file_identity'] or value['generation'] != config['generation']):
        raise ValueError()


def _emit(status, hashes=None):
    print(json.dumps({'status': status, **(hashes or {})}, separators=(',', ':')))


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        _emit('OFF')
        return 0
    if (len(args) != 5 or args[0] != '--enable' or args[1] != '--config' or
            args[3] != '--sha256' or not _HEX.fullmatch(args[4])):
        _emit('REFUSED')
        return 2
    try:
        config, policy, public, fleet, genesis = _prepare(args[2], args[4])
    except Exception:
        _emit('REFUSED')
        return 2
    except BaseException:
        _emit('UNKNOWN')
        return 3
    try:
        result = genesis.bootstrap(enabled=True, source_path=config['source_path'],
            generation_path=config['generation_path'], generation=config['generation'],
            checkpoint_root=config['checkpoint_root'], private_state_root=config['private_state_root'],
            trusted_model_root=config['trusted_model_root'], policy_bytes=policy,
            policy_sha256=config['policy_sha256'], public_policy_bytes=public,
            public_policy_sha256=config['public_policy_sha256'], fleet_bytes=fleet,
            expected_fleet_sha256=config['fleet_sha256'], max_prefix_bytes=config['max_prefix_bytes'],
            max_bytes=config['max_bytes'], max_rows=config['max_rows'],
            plan_template=config['plan_template'], renewable=config['renewable'], cutoff_utc=config['cutoff_utc'],
            batch_rows=config['batch_rows'], min_headroom_bytes=config['min_headroom_bytes'],
            allow_partial_genesis=config['allow_partial_genesis'],
            expected_source_identity=config['source_file_identity'],
            expected_source_prefix_bytes=config['source_bytes'],
            expected_source_prefix_sha256=config['source_sha256'],
            **({'resume_genesis_skip': True} if config.get('resume_genesis_skip') is True else {}))
        fully_covered = _verify_result(config, genesis, result, policy, fleet)
        hashes = {key: result[key] for key in ('pointer_sha256', 'runner_receipt_sha256') if key in result}
        if any(type(value) is not str or _HEX.fullmatch(value) is None for value in hashes.values()):
            raise ValueError()
    except genesis.GenesisRefused as refused:
        if config.get('resume_genesis_skip') is True and str(refused) == 'genesis_skip_progress':
            try:
                progress = _verify_skip_progress(config, genesis)
            except BaseException:
                _emit('UNKNOWN')
                return 3
            _emit('GENESIS_SKIP_PROGRESS', progress)
            return 4
        _emit('REFUSED')
        return 2
    except BaseException:
        _emit('UNKNOWN')
        return 3
    _emit('PUBLISHED_GENESIS' if fully_covered else 'PUBLISHED_GENESIS_PARTIAL', hashes)
    return 0


if __name__ == '__main__':
    sys.exit(main())
