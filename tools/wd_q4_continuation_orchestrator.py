"""One explicitly requested V3 continuation; never schedules or retries.

The immutable template and manifest are caller-supplied trust anchors, not
authentication. An uncertainty after a pending marker is created remains
UNKNOWN; neither elapsed time nor the absence of a process clears that marker.
Same-user tampering, clock authenticity and process-tree cleanup are unproven.
"""
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess

from sandbox.mind.step2 import prefix_private_state as private_state
from sandbox.mind.step2 import shared_index_checkpoint as checkpoint
from sandbox.mind.step2.reader_slot import reader_slot
from tools import wd_q4_cutoff_watermark as watermark
from tools import wd_q4_production_continuation as continuation
from tools import wd_q4_production_continuation_cli as cli
from tools import wd_q4_status_prefix_runner as runner


class OrchestrationRefused(ValueError):
    """No pending attempt was created."""


class OrchestrationUnknown(RuntimeError):
    """A pending attempt needs reconciliation; never automatically retry."""


class OrchestrationInterrupted(BaseException):
    """Interruption preserves stop semantics and cannot imply safe retry."""


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _raw(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')


def _read(path, pin, limit):
    return cli._read_pinned(cli._c_path(str(path)), limit, pin, 'orchestration_refused')


def _run_fixed(bundle, script, host, config, config_pin, manifest_pin, commit, runtime, runtime_pin, scratch, host_pin, timeout):
    env = {name: os.environ[name] for name in ('SystemRoot', 'WINDIR') if name in os.environ}
    env.update(TEMP=str(scratch), TMP=str(scratch))
    args = [str(host), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(script), '-Run',
            '-BundleRoot', str(bundle), '-ExpectedCommit', commit, '-ExpectedManifestSha256', manifest_pin,
            '-ConfigPath', str(config), '-ExpectedConfigSha256', config_pin, '-RuntimePath', str(runtime),
            '-ExpectedRuntimeSha256', runtime_pin, '-ScratchRoot', str(scratch), '-ExpectedHostSha256', host_pin,
            '-TimeoutSeconds', str(timeout)]
    return subprocess.run(args, cwd=str(bundle), env=env, capture_output=True, timeout=timeout + 60, check=False)


def _validate_anchor(template_path, template_sha256, expected_commit, expected_manifest_sha256,
                     runtime_path, runtime_sha256, scratch_root, expected_host_sha256):
    if any(not watermark._pin(pin) for pin in (template_sha256, expected_manifest_sha256,
                                                runtime_sha256, expected_host_sha256)):
        raise ValueError()
    if type(expected_commit) is not str or len(expected_commit) != 40 or any(c not in '0123456789abcdef' for c in expected_commit):
        raise ValueError()
    template = cli._doc(_read(template_path, template_sha256, 65536))
    if (set(template) != cli._KEYS | {'cutoff_utc', 'previous_cutoff_utc', 'watermark_previous_record_sha256'}
            or template['schema'] != 'wd.q4-continuation-cli.v3'
            or template['manifest_sha256'] != expected_manifest_sha256):
        raise ValueError()
    if any(not watermark._pin(template[key]) for key in ('tuple_sha256', 'policy_sha256',
            'public_policy_sha256', 'fleet_sha256', 'source_prefix_sha256')):
        raise ValueError()
    if (type(template['source_identity']) is not str or not template['source_identity']
            or type(template['source_prefix_bytes']) is not int or template['source_prefix_bytes'] < 1):
        raise ValueError()
    bundle = Path(cli._c_path(template['bundle_root']))
    manifest = cli._doc(_read(bundle / 'deployment-manifest.json', expected_manifest_sha256, 2 * 1024 * 1024))
    if manifest.get('schema_version') != 1 or manifest.get('source_commit') != expected_commit:
        raise ValueError()
    if str(manifest['files']['wd-fleet.json']).lower() != template['fleet_sha256']:
        raise ValueError()
    script = bundle / 'Invoke-WdQ4Continuation.ps1'
    script_pin = str(manifest['files']['Invoke-WdQ4Continuation.ps1']).lower()
    _read(script, script_pin, 1024 * 1024)
    runtime = Path(cli._c_path(runtime_path))
    _read(runtime, runtime_sha256, 65536)
    scratch = runner.roots._root(cli._c_path(scratch_root))
    runner.roots._observe(scratch)
    host = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    _read(host, expected_host_sha256, 128 * 1024 * 1024)
    tuple_bytes = _read(template['tuple_path'], template['tuple_sha256'], 65536)
    source_pin = cli._doc(tuple_bytes).get('independent_source_pin')
    if source_pin != {'identity': template['source_identity'], 'prefix_bytes': template['source_prefix_bytes'],
                      'prefix_sha256': template['source_prefix_sha256']}:
        raise ValueError()
    policy = _read(template['policy_path'], template['policy_sha256'], 65536)
    public = _read(template['public_policy_path'], template['public_policy_sha256'], 65536)
    fleet = _read(bundle / 'wd-fleet.json', template['fleet_sha256'], 1024 * 1024)
    return template, bundle, script, host, runtime, scratch, tuple_bytes, policy, public, fleet


def run_once(*, enabled=False, template_path=None, template_sha256=None, expected_commit=None,
             expected_manifest_sha256=None, runtime_path=None, runtime_sha256=None, scratch_root=None,
             expected_host_sha256=None, cutoff_utc=None, timeout_seconds=1800):
    if enabled is False:
        return {'status': 'OFF'}
    try:
        if enabled is not True or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 1800:
            raise ValueError()
        requested = runner._cutoff_datetime(cutoff_utc)
        template, bundle, script, host, runtime, scratch, tuple_bytes, policy, public, fleet = _validate_anchor(
            template_path, template_sha256, expected_commit, expected_manifest_sha256,
            runtime_path, runtime_sha256, scratch_root, expected_host_sha256)
        tuple_doc = cli._doc(tuple_bytes)
        route = cli._doc(continuation._part(tuple_doc['route'], 65536))
        private = runner.roots._root(route['private_state_root'])
        shared = runner.roots._root(route['checkpoint_root'])
        private_identity = runner.roots._observe(private)
        runner.roots._observe(shared)
        lock = checkpoint._secure(private / 'q4-continuation-orchestrator.lock', missing_leaf=True)
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        raise OrchestrationInterrupted('orchestration_interrupted') from None
    except Exception:
        raise OrchestrationRefused('orchestration_refused') from None
    entered = False
    try:
        with reader_slot(lock, timeout_seconds=0.2):
            checkpoint._secure(lock)
            pending = private / 'q4-continuation-orchestrator.pending.json'
            if os.path.lexists(pending):
                raise OrchestrationUnknown('orchestration_unknown')
            head = runner._json(checkpoint._read(checkpoint._secure(private / 'q4-cutoff-current.json'), max_bytes=4096), 4096)
            previous_pin = head['record_sha256']
            previous = watermark._load_head(private, previous_pin)
            if previous is None or requested < runner._cutoff_datetime(previous['cutoff_utc']):
                raise ValueError()
            pointer = private_state._shared_pointer(shared)
            if previous['pointer_sha256'] != pointer:
                raise ValueError()
            route, plan, pin, roster, tasks = continuation._inputs(tuple_bytes, template['tuple_sha256'], pointer,
                policy, public, fleet, template['fleet_sha256'])
            continuation._verified(pointer, route, plan, pin, roster, tasks, policy)
            config = dict(template, pointer_sha256=pointer, cutoff_utc=cutoff_utc,
                          previous_cutoff_utc=previous['cutoff_utc'], watermark_previous_record_sha256=previous_pin)
            config_raw = _raw(config)
            attempt = secrets.token_hex(32)
            config_path = private / ('q4-continuation-orchestrator-config-' + attempt + '.json')
            marker_raw = _raw({'schema': 'wd.q4-continuation-orchestrator-pending.v1', 'attempt': attempt,
                               'template_sha256': template_sha256, 'config_sha256': _sha(config_raw),
                               'previous_record_sha256': previous_pin, 'pointer_sha256': pointer})
            marker_info = runner._create(pending, marker_raw)
            entered = True
            runner._verify_own(pending, marker_raw, marker_info)
            config_info = runner._create(config_path, config_raw)
            runner._verify_own(config_path, config_raw, config_info)
            result = _run_fixed(bundle, script, host, config_path, _sha(config_raw), expected_manifest_sha256,
                                expected_commit, runtime, runtime_sha256, scratch, expected_host_sha256, timeout_seconds)
            if result.returncode != 0 or len(result.stdout) > 1024 or result.stderr:
                raise ValueError()
            answer = runner._json(result.stdout, 1024)
            keys = {'status', 'pointer_sha256', 'runner_receipt_sha256', 'cutoff_utc', 'previous_cutoff_utc',
                    'route_sha256', 'watermark_record_sha256', 'cutoff_monotonicity'}
            if (set(answer) != keys or answer['status'] not in ('DEFERRED', 'partial_ready_prefix', 'caught_up_to_cutoff')
                    or answer['cutoff_utc'] != cutoff_utc or answer['previous_cutoff_utc'] != previous['cutoff_utc']
                    or answer['cutoff_monotonicity'] != 'COOPERATIVE_LEASE_PERSISTED_CRASH_UNKNOWN'
                    or any(not watermark._pin(answer[key]) for key in ('pointer_sha256', 'runner_receipt_sha256',
                                                                       'route_sha256', 'watermark_record_sha256'))):
                raise ValueError()
            record = watermark._load_head(private, answer['watermark_record_sha256'])
            if (record['previous_record_sha256'] != previous_pin or record['cutoff_utc'] != cutoff_utc
                    or any(record[key] != answer[key] for key in ('pointer_sha256', 'route_sha256', 'runner_receipt_sha256'))):
                raise ValueError()
            continuation._verified(answer['pointer_sha256'], route, plan, pin, roster, tasks, policy)
            if runner.roots._observe(private) != private_identity:
                raise ValueError()
            runner._verify_own(config_path, config_raw, config_info)
            runner._verify_own(pending, marker_raw, marker_info)
            os.unlink(pending)
            return answer
    except OrchestrationUnknown:
        raise
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        raise OrchestrationInterrupted('orchestration_interrupted') from None
    except Exception:
        if entered or os.path.lexists(private / 'q4-continuation-orchestrator.pending.json'):
            raise OrchestrationUnknown('orchestration_unknown') from None
        raise OrchestrationRefused('orchestration_refused') from None
