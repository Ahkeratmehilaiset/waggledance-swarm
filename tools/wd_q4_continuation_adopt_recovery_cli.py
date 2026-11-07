"""One explicit, pinned adoption reconciliation; no automatic retry.

Locks and pins are cooperative identity boundaries, not authentication of a
same-user process or proof of escaped-child termination.
"""
import argparse
import json
import os
import sys
from tools import wd_q4_continuation_runtime_bootstrap as pins

_KEYS = frozenset({'schema','bundle_root','manifest_sha256','baseline_path','baseline_sha256',
                   'tuple_path','tuple_sha256','receipt_path','receipt_sha256'})


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError('arguments')


def main(argv=None):
    parser=_Parser(add_help=False, allow_abbrev=False)
    parser.add_argument('--enable', action='store_true')
    parser.add_argument('--config')
    parser.add_argument('--sha256')
    try:
        tokens=list(sys.argv[1:] if argv is None else argv)
        if not tokens:
            print('{"status":"OFF"}')
            return 0
        if len(tokens)!=5 or tokens[0]!='--enable' or tokens[1]!='--config' or tokens[3]!='--sha256':
            raise ValueError()
        args=parser.parse_args(tokens)
        if not args.config or not args.sha256: raise ValueError()
        config=pins._doc(pins._read(args.config,args.sha256,65536))
        if (type(config) is not dict or set(config)!=_KEYS or config['schema']!='wd.q4-adopt-recovery-cli.v1'
                or os.environ.get('WD_REBOOT_EXPECTED_MANIFEST_HASH','').lower()!=config['manifest_sha256']):
            raise ValueError()
        pins._read(config['baseline_path'], config['baseline_sha256'], 1024*1024)
        tup=pins._read(config['tuple_path'],config['tuple_sha256'],1024*1024)
        receipt=pins._read(config['receipt_path'],config['receipt_sha256'],65536)
    except (Exception, SystemExit):
        print('{"status":"REFUSED"}')
        return 2
    try:
        from tools import wd_q4_continuation_adopt_recovery as recovery
        result=recovery.recover_once(enabled=True,baseline_path=config['baseline_path'],
            baseline_sha256=config['baseline_sha256'],manifest_sha256=config['manifest_sha256'],
            genesis_tuple_bytes=tup,genesis_tuple_sha256=config['tuple_sha256'],
            receipt_bytes=receipt,receipt_sha256=config['receipt_sha256'])
        if type(result) is not dict or set(result)!={'status'} or result['status'] not in ('ADOPTED','NOT_ADOPTED','NOT_NEEDED'):
            raise ValueError()
        print(json.dumps(result,separators=(',',':')))
        return 0
    except recovery.RecoveryRefused:
        print('{"status":"REFUSED"}')
        return 2
    except BaseException:
        print('{"status":"UNKNOWN"}')
        return 3


if __name__=='__main__':
    raise SystemExit(main())
