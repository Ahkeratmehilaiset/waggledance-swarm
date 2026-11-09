"""Fixed evidence reader; no filesystem I/O or state changes.

Read-only is a property of this pinned program, not an OS sandbox. A process
using the same account may still access files permitted to that account.
"""
import base64
import hashlib
import hmac
import json
import re
import sys

_HEX = re.compile(r'[0-9a-f]{64}\Z')
_KEYS = {'schema', 'tuple_b64', 'tuple_sha256', 'receipt_b64', 'receipt_sha256'}


def inspect(data):
    if type(data) is not dict or set(data) != _KEYS or data['schema'] != 'wd.q4-owned-reader.v1':
        raise ValueError('invalid evidence')
    result = {'status': 'VERIFIED'}
    for name in ('tuple', 'receipt'):
        encoded, pin = data[name + '_b64'], data[name + '_sha256']
        if (type(encoded) is not str or len(encoded) > 87384 or
                type(pin) is not str or not _HEX.fullmatch(pin)):
            raise ValueError('invalid evidence')
        try:
            raw = base64.b64decode(encoded, validate=True)
        except Exception:
            raise ValueError('invalid evidence') from None
        if not 0 < len(raw) <= 65536 or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), pin):
            raise ValueError('invalid evidence')
        result[name + '_sha256'] = pin
    return result


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print('{"status":"OFF"}')
        return 0
    try:
        if args != ['--enable']:
            raise ValueError('invalid invocation')
        raw = sys.stdin.buffer.read(180001)
        if len(raw) > 180000:
            raise ValueError('invalid evidence')
        data = json.loads(raw.decode('ascii'), object_pairs_hook=_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        print(json.dumps(inspect(data), sort_keys=True, separators=(',', ':')))
        return 0
    except Exception:
        print('{"status":"REFUSED"}')
        return 2


if __name__ == '__main__':
    sys.exit(main())
