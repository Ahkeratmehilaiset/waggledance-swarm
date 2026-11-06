"""Default-off in-memory plan for a manifest-bound Q4 session command.

No process, file, environment, query, or network access occurs here.  This is
not authorization, identity attestation, file verification, or TOCTOU safety.
The caller must independently authenticate the manifest anchor and all inputs,
protect this module and its imports, and enforce the execution boundary.
"""

import os
import re

from tools.wd_q4_descriptor_host_tuple import resolve_descriptor_host_tuple
from tools.wd_q4_session_tuple import build_session_argv


_HEX = re.compile(r'[0-9a-f]{64}\Z')
_OUTPUT_KEYS = frozenset({'status', 'attribution', 'freshness', 'host_script', 'host_sha256',
                          'host_arguments', 'manifest_binding', 'manifest_additional_pins'})
_EXTRA_PINS = frozenset({'Invoke-WdQ4SessionCommand.ps1',
                         'tools-bootstrap/sandbox/mind/step2/runtime_bootstrap.py'})
_HOST_KEYS = frozenset({'JobHelper', 'JobHelperSha256', 'Interpreter', 'InterpreterSha256',
                        'Bootstrap', 'BootstrapSha256', 'BootstrapArguments', 'WorkingDirectory',
                        'ScratchRoot', 'TimeoutSeconds', 'MaxOutputBytes'})


def build_bound_session_plan(*, fleet_bytes=None, expected_fleet_sha256=None,
                             binding_bytes=None, expected_binding_sha256=None,
                             lane=None, descriptor=None, runtime_identity=None, host=None,
                             manifest_bytes=None, expected_manifest_sha256=None,
                             expected_manifest_commit=None, enabled=False):
    """Create a typed, unexecuted plan; no query input or caller overrides."""
    if enabled is False:
        return {'status': 'OFF'}
    try:
        if enabled is not True:
            raise ValueError()
        result = resolve_descriptor_host_tuple(fleet_bytes=fleet_bytes,
            expected_fleet_sha256=expected_fleet_sha256, binding_bytes=binding_bytes,
            expected_binding_sha256=expected_binding_sha256, lane=lane, descriptor=descriptor,
            runtime_identity=runtime_identity, host=host, manifest_bytes=manifest_bytes,
            expected_manifest_sha256=expected_manifest_sha256,
            expected_manifest_commit=expected_manifest_commit, enabled=True)
        if (type(result) is not dict or set(result) != _OUTPUT_KEYS or
                result['status'] != 'TUPLE_CONSISTENT' or
                result['manifest_binding'] != 'MANIFEST_PINS_ONLY' or
                result['attribution'] != 'DECLARED_NOT_AUTHENTICATED' or
                result['freshness'] != 'FRESHNESS_UNKNOWN'):
            raise ValueError()
        pins = result['manifest_additional_pins']
        args = result['host_arguments']
        if (type(pins) is not dict or set(pins) != _EXTRA_PINS or
                any(type(pin) is not str or not _HEX.fullmatch(pin) for pin in pins.values()) or
                type(args) is not dict or set(args) != _HOST_KEYS or
                type(host) is not dict or type(descriptor) is not dict or
                result['host_script'] != host['HostScript'] or result['host_sha256'] != host['HostSha256'] or
                any(args[key] != host[key] for key in _HOST_KEYS - {'BootstrapArguments'})):
            raise ValueError()
        expected_argv = build_session_argv(descriptor['worker'], descriptor['outer'],
                                           descriptor['expected_binding'], enabled=True)
        if type(args['BootstrapArguments']) is not list or args['BootstrapArguments'] != expected_argv:
            raise ValueError()
        host_arguments = dict(args)
        host_arguments['BootstrapArguments'] = list(expected_argv)
        return {'status': 'PLAN_ONLY', 'manifest_binding': 'MANIFEST_PINS_ONLY',
                'command_script': os.path.join(descriptor['bundle_root'], 'Invoke-WdQ4SessionCommand.ps1'),
                'command_sha256': pins['Invoke-WdQ4SessionCommand.ps1'],
                'host_script': result['host_script'], 'host_sha256': result['host_sha256'],
                'host_arguments': host_arguments}
    except Exception:
        pass
    fleet_bytes = expected_fleet_sha256 = binding_bytes = expected_binding_sha256 = lane = None
    descriptor = runtime_identity = host = manifest_bytes = expected_manifest_sha256 = None
    expected_manifest_commit = enabled = result = pins = args = expected_argv = host_arguments = None
    raise ValueError('bound_session_plan_refused') from None
