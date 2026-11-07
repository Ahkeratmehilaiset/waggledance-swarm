"""Default-off exclusive writer for a caller-pinned staged fixed plan.

The input pins are caller assertions, not authorization, authenticity, or proof
that a runtime is installed. A post-entry I/O failure is UNKNOWN and requires
inspection; this module never activates a descriptor or starts a process.
"""
import hashlib
import json
import os
import sys

from tools import wd_q4_fixed_plan_producer as producer
from tools import wd_q4_public_card_plan_writer as common
from tools import wd_q4_secure_transport as transport

SCHEMA = 'wd.q4-staged-fixed-plan-request.v1'


class Refused(ValueError):
    """No output write was attempted."""


class Unknown(RuntimeError):
    """A write may have occurred; inspect before retrying."""


def write(*, enabled=False, arguments=None, output_dir=None):
    if enabled is False:
        return {'status': 'OFF'}
    try:
        if enabled is not True or type(arguments) is not dict or 'enabled' in arguments:
            raise ValueError()
        directory = common._path(output_dir)
        if not directory.is_dir() or any(directory.iterdir()):
            raise ValueError()
        result = producer.build_staged_fixed_plan(enabled=True, **arguments)
        raw, pin = result['plan_bytes'], result['plan_sha256']
        if (type(raw) is not bytes or hashlib.sha256(raw).hexdigest() != pin
                or transport._fixed_plan(raw)['schema'] != 'wd.q4-fixed-plan.v3'):
            raise ValueError()
        target = directory / 'fixed-plan.v3.json'
    except Exception:
        raise Refused('fixed_plan_write_refused') from None
    try:
        with target.open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if target.read_bytes() != raw or hashlib.sha256(target.read_bytes()).hexdigest() != pin:
            raise ValueError()
        return {'status': 'STAGED', 'path': str(target), 'sha256': pin, 'bytes': len(raw)}
    except BaseException:
        raise Unknown('fixed_plan_write_unknown') from None


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if args == []:
        print('{"status":"OFF"}')
        return 0
    try:
        if len(args) != 5 or args[0] != '--enabled' or args[1] != '--request' or args[3] != '--request-sha256':
            raise Refused('fixed_plan_write_refused')
        raw = common._read_pinned(args[2], args[4])
        request = common._json(raw)
        if (type(request) is not dict or set(request) != {'schema', 'arguments', 'output_dir'}
                or request['schema'] != SCHEMA):
            raise Refused('fixed_plan_write_refused')
        result = write(enabled=True, arguments=request['arguments'], output_dir=request['output_dir'])
        try:
            if common._read_pinned(args[2], args[4]) != raw:
                raise ValueError()
        except BaseException:
            raise Unknown('fixed_plan_write_unknown') from None
        print(json.dumps(result, sort_keys=True, separators=(',', ':')))
        return 0
    except Unknown:
        print('{"status":"UNKNOWN"}', file=sys.stderr)
        return 3
    except Exception:
        print('{"status":"REFUSED"}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
