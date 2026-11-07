"""Create an external audit anchor from a separately pinned bridge prefix.

The anchor is an audit label, not authentication, freshness or provenance.
The source log is never modified. Partial output is UNKNOWN and cannot be
retried in the same output directory.
"""
import json
import sys

from tools import wd_q4_external_generation_anchor as anchor
from tools import wd_q4_real_card_snapshot_cli as io


class Refused(ValueError): pass
class Unknown(RuntimeError): pass


def run(*, enabled=False, source_path=None, pin_path=None, pin_sha256=None, output_root=None):
    if enabled is False:
        return {'status': 'OFF'}
    try:
        if enabled is not True:
            raise ValueError()
        source = io._path(source_path)
        pin_file = io._path(pin_path)
        root = io._path(output_root, directory=True)
        if ('.codex-audit' not in pin_file.parts or pin_file.is_relative_to(root)
                or pin_file.is_relative_to(source.parent)):
            raise ValueError()
        raw = io._read(pin_file, pin_sha256, 4096)
        pin = io._json(raw)
        if (type(pin) is not dict or set(pin) != {
                'schema', 'cursor', 'prefix_sha256', 'source_authentication',
                'freshness', 'completeness', 'scope'}
                or pin['schema'] != 'wd.q4-private-bridge-prefix-pin.v1'
                or any(pin[key] != 'UNKNOWN' for key in ('source_authentication', 'freshness', 'completeness'))
                or pin['scope'] != 'bounded_observed_prefix'):
            raise ValueError()
        c = pin['cursor']
        if (type(c) is not dict or set(c) != {'offset', 'file_identity', 'generation'}
                or type(c['offset']) is not int or type(c['file_identity']) is not str
                or c['generation'] is not None):
            raise ValueError()
        from waggledance.core.bridge_log_reader import BridgeCursor
        cursor = BridgeCursor(c['offset'], c['file_identity'], None)
        args = dict(enabled=True, source_path=str(source), original_cursor=cursor,
                    expected_prefix_sha256=pin['prefix_sha256'], output_root=str(root))
    except Exception:
        raise Refused('anchor_cli_refused') from None
    try:
        result = anchor.prepare(**args)
    except anchor.Refused:
        raise Refused('anchor_cli_refused') from None
    except (anchor.Unknown, anchor.Interrupted):
        raise Unknown('anchor_cli_unknown') from None
    except BaseException:
        # The call was entered, so unexpected errors may follow a completed write.
        raise Unknown('anchor_cli_unknown') from None
    try:
        if io._read(pin_file, pin_sha256, 4096) != raw:
            raise ValueError()
        check = anchor.verify(**args, expected_generation_sha256=result['generation_sha256'],
                              expected_binding_sha256=result['binding_sha256'])
        if (check['status'] != 'VERIFIED_WITHIN_PREFIX' or io._path(str(pin_file)) != pin_file
                or io._read(pin_file, pin_sha256, 4096) != raw):
            raise ValueError()
        return result
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        raise Unknown('anchor_cli_unknown') from None
    except Exception:
        raise Unknown('anchor_cli_unknown') from None


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print('{"status":"OFF"}')
        return 0
    if (len(args) != 9 or args[0] != '--enabled' or args[1] != '--source'
            or args[3] != '--pin' or args[5] != '--pin-sha256' or args[7] != '--output-root'):
        print('{"status":"REFUSED"}', file=sys.stderr)
        return 2
    try:
        result = run(enabled=True, source_path=args[2], pin_path=args[4],
                     pin_sha256=args[6], output_root=args[8])
        print(json.dumps(result, sort_keys=True, separators=(',', ':')))
        return 0
    except Refused:
        print('{"status":"REFUSED"}', file=sys.stderr)
        return 2
    except Unknown:
        print('{"status":"UNKNOWN"}', file=sys.stderr)
        return 3


if __name__ == '__main__':
    sys.exit(main())
