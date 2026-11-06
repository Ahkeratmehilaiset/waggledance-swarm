"""Opt-in one-shot status-index publication. No scheduler or authorization service.

The explicit, hash-pinned configuration and existing baseline descriptor are
trust inputs, not proof of their publisher. The runner's cooperative fence and
private state guard are required. Same-user interference, source authenticity,
wall-clock freshness and rollback resistance are not established by this tool.
After a write begins, interruption or failure is UNKNOWN: reconcile, never retry
automatically. No raw bridge text or query is included in the receipt.
"""
import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys

from sandbox.mind.step2 import prefix_private_state as private_state
from sandbox.mind.step2 import shared_index_checkpoint as checkpoint
from sandbox.mind.step2 import shared_capture_adapter as capture_adapter
from sandbox.mind.step2.reader_slot import reader_slot
from tools import wd_q4_descriptor_materializer as materializer
from tools import wd_q4_lane_binding_producer as binding_producer
from tools import wd_q4_lane_caller_cli as caller
from tools import wd_q4_lane_link as link
from tools import wd_q4_live_snapshot_publish as live
from tools import wd_q4_secure_transport as transport
from tools import wd_q4_snapshot_candidate as capture
from tools import wd_q4_status_prefix_runner as runner

_HEX = re.compile(r'[0-9a-f]{64}\Z')
_CONFIG_KEYS = frozenset({'schema', 'route_path', 'route_sha256', 'plan_path', 'anchor_path',
    'policy_path', 'public_policy_path', 'baseline_path', 'baseline_sha256', 'manifest_sha256'})


class UpdateRefused(ValueError):
    """No mutation was attempted."""


class UpdateOutcomeUnknown(RuntimeError):
    """A write may have completed; reconcile before proceeding."""


class UpdateInterrupted(BaseException):
    """An interrupted write remains uncertain and must not be retried."""


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')


def _read(path, limit, expected):
    return caller._read(path, limit, expected)


def _spec(root, path):
    path = checkpoint._secure(path)
    relative = path.relative_to(root)
    raw = checkpoint._read(path, max_bytes=8 * 1024 * 1024)
    return {'path': str(relative), 'sha256': _sha(raw)}


def _snapshot(root, pointer_pin, policy_path, roster_path, public_policy_path):
    """Discover a bounded local candidate; capture rechecks bytes under lock."""
    root = checkpoint._secure(root)
    pointer_path = root / checkpoint.POINTER
    pointer_raw = checkpoint._read(pointer_path, max_bytes=16384)
    if _sha(pointer_raw) != pointer_pin:
        raise ValueError()
    pointer = caller._json(pointer_raw)
    generation = pointer['generation']
    if checkpoint.NAME.fullmatch(generation) is None:
        raise ValueError()
    active = {name: _spec(root, root / generation / name) for name in checkpoint.ARTIFACTS.values()}
    history = {}
    for path in root.iterdir():
        if path.is_file() and (path.name.startswith('q4-pointer-') or path.name.startswith('q4-delta-')):
            history[path.name] = _spec(root, path)
        elif path.is_dir() and path.name != generation and checkpoint.NAME.fullmatch(path.name):
            for name in checkpoint.ARTIFACTS.values():
                item = path / name
                if item.is_file():
                    history[f'{path.name}/{name}'] = _spec(root, item)
        if len(history) > 2048:
            raise ValueError()
    memory = caller._json(checkpoint._read(root / generation / 'memory.json', max_bytes=8 * 1024 * 1024))
    from waggledance.core.hex_cell_topology import ALL_CELLS
    cells = {}
    for cell, info in memory['cells'].items():
        if cell not in ALL_CELLS or type(info['generation']) is not int or info['generation'] < 0:
            raise ValueError()
        cell_root = root / generation / 'cells' / cell
        version = cell_root / f"gen-{info['generation']:06d}"
        files = {'index': version / 'faiss' / f'cell_{cell}' / 'index.faiss',
                 'meta': version / 'faiss' / f'cell_{cell}' / 'meta.json',
                 'vectors': version / 'vectors.f32', 'provenance': version / 'provenance.jsonl',
                 'receipt': version / 'receipt.json'}
        cells[cell] = {'pointer': _spec(root, cell_root / 'current.json'),
                       'log': _spec(root, cell_root / 'LOG.jsonl'),
                       'artifacts': {name: _spec(root, path) for name, path in files.items()}}
    descriptors = {'pointer_bytes': _spec(root, pointer_path), 'policy_bytes': _spec(root, Path(policy_path)),
        'roster_bytes': _spec(root, Path(roster_path)), 'public_policy_bytes': _spec(root, Path(public_policy_path)),
        'active_artifact_bytes': active, 'history_bytes': history, 'cell_snapshots': cells}
    result = capture.capture(root=root, descriptors=descriptors, enabled=True)
    if result['status'] != 'CANDIDATE_UNVERIFIED':
        raise ValueError()
    return pointer, result['bundle_bytes']


def _exclusive(path, raw):
    fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_BINARY', 0), 0o600)
    with os.fdopen(fd, 'wb') as stream:
        if stream.write(raw) != len(raw):
            raise OSError()
        stream.flush()
        os.fsync(stream.fileno())


def _finish_open_marker(fd, raw):
    """Finish only a marker whose O_EXCL open was already successful."""
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError()
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            if stream.write(raw) != len(raw):
                raise OSError()
            stream.flush()
        os.fsync(fd)
        return info
    finally:
        os.close(fd)


def _runner_receipt(private_root, previous_files, report, previous_pin, route_pin, plan_pin):
    candidates = {p.name for p in private_root.glob('q4-status-result-*.json')}
    if len(candidates) > 50000:
        raise ValueError()
    new = candidates - previous_files
    if len(new) != 1:
        raise ValueError()
    name = next(iter(new))
    if re.fullmatch(r'q4-status-result-[0-9a-f]{64}\.json', name) is None:
        raise ValueError()
    raw = live._read(private_root / name, 65536, report['runner_receipt_sha256'])
    receipt = caller._json(raw)
    if (receipt.get('schema') != 'wd.q4-status-result.v1' or receipt.get('pointer_sha256') != report['pointer_sha256']
            or receipt.get('prior_pointer_sha256') != previous_pin or receipt.get('route_sha256') != route_pin
            or receipt.get('plan_sha256') != plan_pin or receipt.get('status') != report['status']):
        raise ValueError()
    return name


def _require_write_roots(baseline_path, bundle_root, checkpoint_root, private_root):
    generations = Path(baseline_path).parent.parent
    live._validate_roots(generations, Path(bundle_root), Path(checkpoint_root))
    for path in (Path(checkpoint_root), Path(private_root)):
        live.pointer_io._chain(path)
        if (not path.is_dir() or not live._disjoint(path, Path(bundle_root))
                or not live._disjoint(path, generations)):
            raise ValueError()


def _publish(config, baseline, index_root, public, result, *, generation=None):
    pointer, snapshot = _snapshot(index_root, result['pointer_sha256'], public['policy_path'],
                                  public['roster_path'], public['public_policy_path'])
    bd, _, _, fleet, binding, inputs = baseline
    fixed = transport._fixed_plan(inputs['fixed_plan_bytes'])
    pins = dict(binding)
    pins.update(pointer_sha256=result['pointer_sha256'], source_anchor_sha256=pointer['anchor_sha256'],
                descriptor_sha256=pointer['artifacts']['descriptor_sha256'],
                projection_sha256=pointer['artifacts']['projection_sha256'])
    plan = dict(public)
    plan.update({key: pins[key] for key in ('pointer_sha256', 'source_anchor_sha256', 'encoder_sha256',
                                            'policy_sha256', 'roster_sha256', 'public_policy_sha256')})
    plan['config'] = {**plan['config'], 'descriptor_sha256': pins['descriptor_sha256']}
    plan_raw = _encode(plan)
    bundle = Path(bd['bundle_root'])
    manifest = _read(str(bundle / 'deployment-manifest.json'), caller.MAX_MANIFEST, config['manifest_sha256'])
    wrapper = live._read(bundle / 'Invoke-WdQ4Query.ps1', 1024 * 1024)
    produced = binding_producer.build_lane_binding(enabled=True, expected_commit=caller._json(manifest)['source_commit'],
        manifest_bytes=manifest, expected_manifest_sha256=config['manifest_sha256'], fleet_bytes=fleet,
        expected_fleet_sha256=bd['fleet_sha256'], wrapper_bytes=wrapper, expected_wrapper_sha256=_sha(wrapper),
        public_plan_bytes=plan_raw, expected_public_plan_sha256=_sha(plan_raw), snapshot_bytes=snapshot,
        expected_snapshot_sha256=_sha(snapshot))
    if caller._json(produced['binding_bytes'])['binding'] != pins:
        raise ValueError()
    fixed.update(bundle_sha256=_sha(snapshot), bundle_bytes=len(snapshot),
                 expected_pointer_sha256=pins['pointer_sha256'], expected_source_sha256=pins['source_anchor_sha256'])
    fixed_raw = _encode(fixed)
    root = Path(config['baseline_path']).parent.parent
    generation = result['pointer_sha256'] if generation is None else generation
    paths = materializer.plan_generation_paths(str(root), generation)
    descriptor = dict(bd)
    descriptor.update(public_plan=str(Path(paths['generation_dir']) / 'public-plan.json'),
        binding_path=paths['binding_path'], binding_sha256=produced['binding_sha256'],
        fixed_plan_path=paths['fixed_plan_path'], fixed_plan_sha256=_sha(fixed_raw),
        snapshot_path=paths['snapshot_path'], snapshot_sha256=_sha(snapshot), expected_binding=pins)
    descriptor_raw = _encode(descriptor)
    materializer.materialize_descriptor(enabled=True, target_dir=str(root), generation=generation,
        artifacts={'binding': produced['binding_bytes'], 'fixed_plan': fixed_raw, 'snapshot': snapshot},
        descriptor_bytes=descriptor_raw, expected_descriptor_sha256=_sha(descriptor_raw))
    _exclusive(Path(descriptor['public_plan']), plan_raw)
    candidate = caller._configuration(paths['descriptor_path'], _sha(descriptor_raw))
    publication = live.publish(enabled=True, baseline_path=config['baseline_path'],
        baseline_sha256=config['baseline_sha256'], baseline=baseline, manifest_sha256=config['manifest_sha256'],
        candidate_path=paths['descriptor_path'], candidate_sha256=_sha(descriptor_raw), candidate=candidate)
    return {'status': publication['status'], 'pointer_sha256': result['pointer_sha256'],
            'descriptor_sha256': _sha(descriptor_raw), 'publication_sha256': publication['publication_sha256'],
            'freshness': 'UNKNOWN', 'source_authenticity': 'UNKNOWN', 'publisher_authentication': 'UNKNOWN'}


def run_once(*, enabled=False, config_path=None, expected_config_sha256=None):
    """Verify explicit config and execute at most one fenced update/publication."""
    if enabled is False:
        return {'status': 'OFF'}
    entered = False
    failure = None
    slot = None
    slot_acquired = False
    try:
        if enabled is not True or type(expected_config_sha256) is not str or not _HEX.fullmatch(expected_config_sha256):
            raise ValueError()
        config = caller._json(_read(config_path, 65536, expected_config_sha256), _CONFIG_KEYS)
        if config['schema'] != 'wd.q4-live-update-once.v1' or any(
                type(config[key]) is not str or not _HEX.fullmatch(config[key])
                for key in ('route_sha256', 'baseline_sha256', 'manifest_sha256')):
            raise ValueError()
        if os.environ.get('WD_REBOOT_EXPECTED_MANIFEST_HASH', '').lower() != config['manifest_sha256']:
            raise ValueError()
        baseline = caller._configuration(config['baseline_path'], config['baseline_sha256'])
        descriptor, _, _, fleet, binding, inputs = baseline
        route_raw = _read(config['route_path'], 65536, config['route_sha256'])
        route = caller._json(route_raw)
        plan = caller._json(_read(config['plan_path'], 65536, route['plan_sha256']))
        anchor = caller._json(_read(config['anchor_path'], 16384, route['anchor_sha256']))
        policy = _read(config['policy_path'], 65536, route['policy_sha256'])
        public_policy = _read(config['public_policy_path'], 65536, route['public_policy_sha256'])
        _, _, base_plan_raw = live._plan(descriptor, inputs)
        public = caller._json(base_plan_raw)
        if (Path(public['root']) != Path(route['checkpoint_root'])
                or Path(public['policy_path']) != Path(config['policy_path'])
                or Path(public['public_policy_path']) != Path(config['public_policy_path'])
                or any(binding[key] != route[key] for key in ('policy_sha256', 'roster_sha256', 'public_policy_sha256'))
                or plan.get('schema') != 'wd.q4-prefix-build-plan.v3' or plan.get('fenced') is not True):
            raise ValueError()
        roster = _read(public['roster_path'], 65536, route['roster_sha256'])
        tasks, _ = capture_adapter.strict_pins(policy, route['policy_sha256'], roster, route['roster_sha256'])
        root = Path(route['checkpoint_root'])
        _require_write_roots(config['baseline_path'], descriptor['bundle_root'], root, route['private_state_root'])
        private_root = Path(route['private_state_root'])
        private_info = private_root.stat()
        lock_path = private_root / 'q4-live-update.lock'
        if os.path.lexists(lock_path):
            live.pointer_io._chain(lock_path)
        slot = reader_slot(lock_path, timeout_seconds=0.2)
        slot.__enter__()
        slot_acquired = True
        marker = private_root / 'q4-live-update.pending.json'
        if not runner._absent(marker):
            raise ValueError()
        selector = Path(config['baseline_path']).parent.parent / 'q4-live-current.json'
        if os.path.lexists(selector):
            chosen = live.select(enabled=True, baseline_path=config['baseline_path'],
                baseline_sha256=config['baseline_sha256'], baseline=baseline,
                manifest_sha256=config['manifest_sha256'], load_configuration=caller._configuration)
            current_pin = chosen['configuration'][4]['pointer_sha256']
        else:
            caller._require_current_snapshot(descriptor, binding, inputs)
            current_pin = binding['pointer_sha256']
        _, prefix = checkpoint.read_verified_prefix_pointer(root, tasks, expected_pointer_sha256=current_pin,
            policy_bytes=policy, policy_sha256=route['policy_sha256'], roster_bytes=roster,
            roster_sha256=route['roster_sha256'], expected_public_policy_sha256=route['public_policy_sha256'])
        with private_state.locked_state(Path(route['private_state_root']), root, enabled=True) as state:
            state.require(current_pin, prefix)
        anchor['cursor_start'] = asdict(prefix.cursor)
        anchor_raw = _encode(anchor)
        plan['generation'] = 'live-' + secrets.token_hex(16)
        plan['expected_pointer_sha256'] = current_pin
        plan['pins']['anchor_sha256'] = _sha(anchor_raw)
        plan_raw = _encode(plan)
        route.update(cursor=asdict(prefix.cursor), prefix_sha256=prefix.sha256,
                     anchor_sha256=_sha(anchor_raw), plan_sha256=_sha(plan_raw))
        route_raw = _encode(route)
        nonce = secrets.token_hex(32)
        intent = _encode({'schema': 'wd.q4-live-update-intent.v1', 'attempt': nonce,
            'config_sha256': expected_config_sha256, 'previous_pointer_sha256': current_pin,
            'route': route, 'plan': plan, 'anchor': anchor})
        marker_raw = _encode({'schema': 'wd.q4-live-update-pending.v1', 'attempt': nonce,
                              'config_sha256': expected_config_sha256, 'previous_pointer_sha256': current_pin,
                              'intent_sha256': _sha(intent)})
        old_receipts = {p.name for p in private_root.glob('q4-status-result-*.json')}
        if len(old_receipts) > 50000:
            raise ValueError()
        entered = True
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOINHERIT', 0)
        flags |= getattr(os, 'O_NOFOLLOW', 0)
        try:
            marker_fd = os.open(marker, flags, 0o600)
        except FileExistsError:
            entered = False
            raise
        marker_info = _finish_open_marker(marker_fd, marker_raw)
        intent_path = private_root / ('q4-live-update-intent-' + nonce + '.json')
        intent_info = runner._create(intent_path, intent)
        runner._verify_own(intent_path, intent, intent_info)
        report = runner.run_once(enabled=True, route_bytes=route_raw, expected_route_sha256=_sha(route_raw),
            plan_bytes=plan_raw, policy_bytes=policy, public_policy_bytes=public_policy, fleet_bytes=fleet,
            expected_fleet_sha256=descriptor['fleet_sha256'], roster_bytes=roster, anchor_bytes=anchor_raw,
            renewable=True)
        receipt_name = _runner_receipt(private_root, old_receipts, report, current_pin, _sha(route_raw), _sha(plan_raw))
        progress = _encode({'schema': 'wd.q4-live-update-progress.v1', 'attempt': nonce,
            'marker_sha256': _sha(marker_raw), 'config_sha256': expected_config_sha256,
            'intent_sha256': _sha(intent),
            'previous_pointer_sha256': current_pin, 'pointer_sha256': report['pointer_sha256'],
            'runner_receipt_sha256': report['runner_receipt_sha256'], 'runner_receipt_file': receipt_name,
            'route_sha256': _sha(route_raw), 'plan_sha256': _sha(plan_raw), 'status': report['status']})
        progress_path = private_root / ('q4-live-update-progress-' + nonce + '.json')
        progress_info = runner._create(progress_path, progress)
        runner._verify_own(progress_path, progress, progress_info)
        if report['status'] == 'EMPTY_NO_CHANGE':
            live._read(root / checkpoint.POINTER, 16384, current_pin)
            result = {'status': 'NO_CHANGE', 'pointer_sha256': current_pin, 'freshness': 'UNKNOWN'}
        else:
            result = _publish(config, baseline, root, public, report)
        receipt = _encode({'schema': 'wd.q4-live-update-result.v1', 'attempt': nonce,
                           'config_sha256': expected_config_sha256, 'previous_pointer_sha256': current_pin,
                           'runner_receipt_sha256': report['runner_receipt_sha256'], 'result': result})
        receipt_path = private_root / ('q4-live-update-result-' + nonce + '.json')
        receipt_info = runner._create(receipt_path, receipt)
        runner._verify_own(receipt_path, receipt, receipt_info)
        live.pointer_io._chain(private_root)
        if not os.path.samestat(private_root.stat(), private_info):
            raise ValueError()
        runner._verify_own(marker, marker_raw, marker_info)
        marker.unlink()
        slot.__exit__(None, None, None)
        slot_acquired = False
        return {**result, 'receipt_sha256': _sha(receipt), 'receipt_file': receipt_path.name}
    except Exception:
        failure = 'unknown' if entered else 'refused'
    except BaseException:
        if not entered:
            if slot_acquired:
                slot.__exit__(None, None, None)
            raise
        failure = 'interrupt'
    if slot_acquired:
        try:
            slot.__exit__(None, None, None)
        except Exception:
            failure = 'unknown' if entered else 'refused'
    config_path = expected_config_sha256 = config = baseline = route_raw = route = plan = anchor = None
    policy = public_policy = roster = inputs = prefix = report = receipt = marker_raw = None
    if failure == 'unknown':
        raise UpdateOutcomeUnknown('update_outcome_unknown') from None
    if failure == 'interrupt':
        raise UpdateInterrupted('update_outcome_unknown') from None
    raise UpdateRefused('update_refused') from None


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--config')
    parser.add_argument('--sha256')
    args = parser.parse_args(argv)
    if not args.once:
        print('{"status":"OFF"}')
        return 0
    try:
        result = run_once(enabled=True, config_path=args.config, expected_config_sha256=args.sha256)
    except UpdateOutcomeUnknown:
        print('{"status":"UNKNOWN"}', file=sys.stderr)
        return 3
    except UpdateInterrupted:
        print('{"status":"UNKNOWN"}', file=sys.stderr)
        return 130
    except Exception:
        print('{"status":"REFUSED"}', file=sys.stderr)
        return 2
    print(_encode(result).decode('ascii'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
