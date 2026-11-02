"""Default-OFF stdin/stdout adapter for the in-memory bound session plan.

The caller independently authenticates the interpreter, code package, manifest
anchor and all supplied inputs before invoking this adapter.  This adapter does
not load files, start a session, authenticate an identity, or protect against
TOCTOU.  Input contains plan metadata only; query text is not an accepted key.
On any nonzero exit the parent must discard *all* stdout, including partial
output.  Pipe reads/writes are not a whole-call deadline; the parent owns one.
"""

import base64
import json
import sys

from tools.wd_q4_bound_session_plan import build_bound_session_plan


_MAX_INPUT = 16 * 1024 * 1024
_MAX_OUTPUT = 256 * 1024
_KEYS = frozenset({'fleet_bytes_b64', 'expected_fleet_sha256', 'binding_bytes_b64',
                   'expected_binding_sha256', 'lane', 'descriptor', 'runtime_identity', 'host',
                   'manifest_bytes_b64', 'expected_manifest_sha256', 'expected_manifest_commit'})
_BYTE_FIELDS = {'fleet_bytes_b64': ('fleet_bytes', 1024 * 1024),
                'binding_bytes_b64': ('binding_bytes', 1024 * 1024),
                'manifest_bytes_b64': ('manifest_bytes', 8 * 1024 * 1024)}


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate')
        value[key] = item
    return value


def _invalid_constant(_value):
    raise ValueError('constant')


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        if argv == []:
            output = b'{"status":"OFF"}\n'
        elif argv == ['--enabled']:
            raw = sys.stdin.buffer.read(_MAX_INPUT + 1)
            if not raw or len(raw) > _MAX_INPUT:
                raise ValueError('input')
            data = json.loads(raw.decode('utf-8', 'strict'), object_pairs_hook=_unique_object,
                              parse_constant=_invalid_constant)
            if type(data) is not dict or set(data) != _KEYS:
                raise ValueError('schema')
            for name, (target, maximum) in _BYTE_FIELDS.items():
                value = data.pop(name)
                if type(value) is not str or len(value) > 4 * ((maximum + 2) // 3):
                    raise ValueError('base64')
                decoded = base64.b64decode(value, validate=True)
                if not decoded or len(decoded) > maximum:
                    raise ValueError('base64')
                data[target] = decoded
            plan = build_bound_session_plan(**data, enabled=True)
            output = json.dumps(plan, ensure_ascii=True, allow_nan=False,
                                sort_keys=True, separators=(',', ':')).encode('ascii') + b'\n'
            if len(output) > _MAX_OUTPUT:
                raise ValueError('output')
        else:
            raise ValueError('arguments')
        if sys.stdout.buffer.write(output) != len(output):
            raise OSError('write')
        sys.stdout.buffer.flush()
        return 0
    except Exception:
        try:
            sys.stderr.buffer.write(b'{"error":"bound_session_plan_cli_refused"}\n')
            sys.stderr.buffer.flush()
        except Exception:
            pass
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
