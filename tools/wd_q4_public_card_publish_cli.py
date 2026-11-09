"""Default-off, hash-pinned request for one typed public-card write.

This does not establish policy authorization, fact, publisher identity or
clock authenticity. Once publication is attempted, uncertainty is returned
as UNKNOWN without retry. All payload content stays out of stdout and stderr.
"""
import json
import re
import sys

from tools import wd_q4_public_card_publish as publisher
from tools import wd_q4_real_card_snapshot_cli as files

_INPUTS=frozenset({'policy','public_policy','card_policy','roster','vocabulary','path_allowlist'})
_HEX=re.compile(r'[a-f0-9]{64}\Z')


class Refused(ValueError): pass
class Unknown(RuntimeError): pass


def _result(value):
    common={'status','delivery','event_ref_sha256','truth','source_authentication',
            'publisher_authentication','clock_authentication'}
    if type(value) is not dict: raise ValueError()
    if value.get('status')=='WRITTEN':
        if set(value)!=common or value.get('delivery')!='canonical': raise ValueError()
    elif value.get('status')=='QUEUED':
        if (set(value)!=common|{'retained_wal_sha256'} or value.get('delivery')!='queued'
                or type(value['retained_wal_sha256']) is not str
                or not _HEX.fullmatch(value['retained_wal_sha256'])): raise ValueError()
    else: raise ValueError()
    if (type(value['event_ref_sha256']) is not str or not _HEX.fullmatch(value['event_ref_sha256'])
            or value['truth']!='UNVERIFIED' or any(value[key]!='UNKNOWN' for key in
              ('source_authentication','publisher_authentication','clock_authentication'))): raise ValueError()
    return value


def run(*,enabled=False,request_path=None,request_sha256=None):
    if enabled is False: return {'status':'OFF'}
    arguments=None
    try:
        if enabled is not True: raise ValueError()
        path=files._path(request_path); raw=files._read(path,request_sha256,65536); req=files._json(raw)
        if (type(req) is not dict or set(req)!={'schema','claim','inputs'}
                or req['schema']!='wd.q4-public-card-publish-request.v1'
                or type(req['claim']) is not dict or type(req['inputs']) is not dict
                or set(req['inputs'])!=_INPUTS): raise ValueError()
        arguments={'enabled':True,'claim':req['claim']}; watched=[]
        for name,spec in req['inputs'].items():
            f,data=files._spec(spec,65536);watched.append((f,spec['sha256'],data))
            arguments[name+'_bytes']=data;arguments[name+'_sha256']=spec['sha256']
    except Exception:
        arguments=None
    if arguments is None: raise Refused('public_card_publish_cli_refused')
    result=None; refused=False
    try:
        value=publisher.publish(**arguments)
        if files._read(path,request_sha256,65536)!=raw: raise ValueError()
        for f,pin,data in watched:
            if files._read(f,pin,65536)!=data: raise ValueError()
        result=_result(value)
    except publisher.Refused:
        refused=True
    except BaseException:
        pass
    finally:
        arguments=req=raw=watched=None
    if refused: raise Refused('public_card_publish_cli_refused')
    if result is None: raise Unknown('public_card_publish_cli_unknown')
    return result


def main(argv=None):
    args=sys.argv[1:] if argv is None else argv
    if not args:
        print('{"status":"OFF"}');return 0
    if len(args)!=5 or args[0]!='--enabled' or args[1]!='--request' or args[3]!='--request-sha256':
        print('{"status":"REFUSED"}',file=sys.stderr);return 2
    try:
        print(json.dumps(run(enabled=True,request_path=args[2],request_sha256=args[4]),sort_keys=True,separators=(',',':')))
        return 0
    except Refused:
        print('{"status":"REFUSED"}',file=sys.stderr);return 2
    except Unknown:
        print('{"status":"UNKNOWN"}',file=sys.stderr);return 3


if __name__=='__main__': sys.exit(main())
