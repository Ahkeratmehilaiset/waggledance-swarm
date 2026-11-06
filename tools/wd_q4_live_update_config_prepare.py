"""Default-off materialization of one externally pinned updater configuration.

Inputs are private and must be access-controlled by the caller. This does not
install, schedule, activate, authenticate a writer, or establish freshness.
The genesis tuple's cutoff remains fixed; this is not a rolling-time policy.
Any failure after the first exclusive write leaves an UNKNOWN partial output.
"""
import argparse
import os
from pathlib import Path
import sys

from tools import wd_q4_lane_caller_cli as caller
from tools import wd_q4_live_snapshot_publish as live
from tools import wd_q4_live_update_once as update
from tools import wd_q4_production_continuation as continuation
from tools import wd_q4_status_prefix_runner as runner


class ConfigPrepareRefused(ValueError):
    """No output write was attempted."""


class ConfigPrepareUnknown(RuntimeError):
    """An output write was attempted; reconcile before another attempt."""


class ConfigPrepareInterrupted(BaseException):
    """An interrupted write remains uncertain and must not be retried."""


def prepare_config(*, enabled=False, output_path=None, baseline_path=None, expected_baseline_sha256=None,
                   expected_manifest_sha256=None, tuple_path=None, expected_tuple_sha256=None):
    if enabled is False:
        return {'status': 'OFF'}
    attempted = False
    try:
        if (enabled is not True or any(type(pin) is not str or not update._HEX.fullmatch(pin)
                for pin in (expected_baseline_sha256, expected_manifest_sha256, expected_tuple_sha256))
                or os.environ.get('WD_REBOOT_EXPECTED_MANIFEST_HASH', '').lower() != expected_manifest_sha256):
            raise ValueError()
        baseline = caller._configuration(baseline_path, expected_baseline_sha256)
        descriptor, _, _, fleet, binding, inputs = baseline
        manifest = caller._json(update._read(str(Path(descriptor['bundle_root']) / 'deployment-manifest.json'),
                                              caller.MAX_MANIFEST, expected_manifest_sha256))
        for name in ('WdQ4ProcessJob.ps1', 'tools-bootstrap/tools/wd_q4_live_update_bootstrap.py',
                     'tools-bootstrap/tools/wd_q4_live_update_once.py'):
            digest = caller._hex(manifest['files'][name])
            update._read(str(Path(descriptor['bundle_root']) / name), 1024 * 1024, digest)
        public, _, _ = live._plan(descriptor, inputs)
        policy = update._read(public['policy_path'], 65536, binding['policy_sha256'])
        public_policy = update._read(public['public_policy_path'], 65536, binding['public_policy_sha256'])
        tuple_bytes = update._read(tuple_path, 65536, expected_tuple_sha256)
        route, plan, pin, roster, tasks = continuation._inputs(tuple_bytes, expected_tuple_sha256,
            binding['pointer_sha256'], policy, public_policy, fleet, descriptor['fleet_sha256'])
        if (Path(public['root']) != Path(route['checkpoint_root'])
                or any(binding[key] != route[key] for key in ('policy_sha256', 'roster_sha256', 'public_policy_sha256'))):
            raise ValueError()
        selector = Path(baseline_path).parent.parent / 'q4-live-current.json'
        if os.path.lexists(selector):
            selected = live.select(enabled=True, baseline_path=str(baseline_path),
                baseline_sha256=expected_baseline_sha256, baseline=baseline,
                manifest_sha256=expected_manifest_sha256, load_configuration=caller._configuration)
            current_pin = selected['configuration'][4]['pointer_sha256']
        else:
            caller._require_current_snapshot(descriptor, binding, inputs)
            current_pin = binding['pointer_sha256']
        continuation._verified(current_pin, route, plan, pin, roster, tasks, policy)
        output = Path(output_path)
        directory = runner.roots._root(str(output.parent))
        runner.roots._observe(directory)
        if not directory.is_dir() or output.name != 'updater-config.json':
            raise ValueError()
        for protected in (Path(descriptor['bundle_root']), Path(route['checkpoint_root']),
                          Path(route['private_state_root']), Path(route['trusted_model_root']),
                          Path(route['source']).parent, Path(route['generation_path']).parent,
                          Path(baseline_path).parent.parent):
            if not runner._separate(directory, protected):
                raise ValueError()
        document = runner._json(tuple_bytes, 65536)
        parts = {name: continuation._part(document[name], limit)
                 for name, limit in (('route', 65536), ('plan', 65536), ('anchor', 16384))}
        paths = {name: directory / ('q4-updater-' + name + '.json') for name in parts}
        if any(os.path.lexists(path) for path in (*paths.values(), output)):
            raise ValueError()
        config = {'schema': 'wd.q4-live-update-once.v1', 'route_path': str(paths['route']),
                  'route_sha256': update._sha(parts['route']), 'plan_path': str(paths['plan']),
                  'anchor_path': str(paths['anchor']), 'policy_path': public['policy_path'],
                  'public_policy_path': public['public_policy_path'], 'baseline_path': str(baseline_path),
                  'baseline_sha256': expected_baseline_sha256, 'manifest_sha256': expected_manifest_sha256}
        raw = update._encode(config)
        for name, path in paths.items():
            attempted = True
            info = runner._create(path, parts[name])
            runner._verify_own(path, parts[name], info)
        info = runner._create(output, raw)
        runner._verify_own(output, raw, info)
        return {'status': 'PREPARED', 'config_path': str(output), 'config_sha256': update._sha(raw),
                'cutoff_utc': route['cutoff_utc'], 'freshness': 'UNKNOWN'}
    except Exception:
        if attempted:
            raise ConfigPrepareUnknown('config_prepare_unknown') from None
        raise ConfigPrepareRefused('config_prepare_refused') from None
    except BaseException:
        if attempted:
            raise ConfigPrepareInterrupted('config_prepare_unknown') from None
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument('--prepare', action='store_true')
    for name in ('output-path', 'baseline-path', 'baseline-sha256', 'manifest-sha256', 'tuple-path', 'tuple-sha256'):
        parser.add_argument('--' + name)
    supplied = list(sys.argv[1:] if argv is None else argv)
    try:
        args = parser.parse_args(supplied)
    except SystemExit as exc:
        return int(exc.code) if type(exc.code) is int else 2
    if not args.prepare:
        print('{"status":"OFF"}')
        return 0
    flags = ('--prepare', '--output-path', '--baseline-path', '--baseline-sha256', '--manifest-sha256',
             '--tuple-path', '--tuple-sha256')
    if any(sum(token == flag or token.startswith(flag + '=') for token in supplied) > 1 for flag in flags):
        print('{"status":"REFUSED"}', file=sys.stderr)
        return 2
    try:
        result = prepare_config(enabled=True, output_path=args.output_path, baseline_path=args.baseline_path,
            expected_baseline_sha256=args.baseline_sha256, expected_manifest_sha256=args.manifest_sha256,
            tuple_path=args.tuple_path, expected_tuple_sha256=args.tuple_sha256)
    except ConfigPrepareUnknown:
        print('{"status":"UNKNOWN"}', file=sys.stderr)
        return 3
    except ConfigPrepareInterrupted:
        print('{"status":"UNKNOWN"}', file=sys.stderr)
        return 130
    except Exception:
        print('{"status":"REFUSED"}', file=sys.stderr)
        return 2
    result = {key: value for key, value in result.items() if key != 'config_path'}
    print(update._encode(result).decode('ascii'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
