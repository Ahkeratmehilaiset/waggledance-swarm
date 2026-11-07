"""Default-off, caller-pinned request CLI for the real-format card producer.

Source mode is a caller declaration, not authentication.  The synthetic mode
requires audit-local fixture paths; the explicit operator mode does not infer
permission from the request.  Source/publisher authenticity and completeness
remain UNKNOWN.  This stages files only: no selector, scheduler, or activation.
An uncertain outcome after producer entry must be reconciled, never retried.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

_HEX = re.compile(r'[0-9a-f]{64}\Z')
_INPUTS = frozenset({'decision','vocabulary','path_allowlist','policy','roster','card_policy'})
_KEYS = frozenset({'schema','source_mode','inputs','source_path','source_generation','cursor',
                   'now_utc','output_root','generation_name','git','encoder','trusted_model_root'})


class Refused(ValueError): pass
class Unknown(RuntimeError): pass


def _sha(raw): return hashlib.sha256(raw).hexdigest()


def _json(raw):
    def unique(pairs):
        result = {}
        for key,value in pairs:
            if key in result: raise ValueError()
            result[key] = value
        return result
    return json.loads(raw.decode('utf-8','strict'), object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))


def _path(value, *, directory=False):
    if type(value) is not str or not value: raise ValueError()
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts: raise ValueError()
    for part in (path,*path.parents):
        info=part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0) & 0x400: raise ValueError()
    if path.resolve(strict=True) != path or (not path.is_dir() if directory else not path.is_file()): raise ValueError()
    return path


def _read(path, pin, limit):
    if type(pin) is not str or not _HEX.fullmatch(pin): raise ValueError()
    before=path.stat()
    if before.st_size>limit: raise ValueError()
    with path.open('rb') as stream:
        raw=stream.read(limit+1)
        if not os.path.samestat(before,os.fstat(stream.fileno())): raise ValueError()
    if len(raw)!=before.st_size or _sha(raw)!=pin: raise ValueError()
    return raw


def _spec(value,limit):
    if type(value) is not dict or set(value)!={'path','sha256'}: raise ValueError()
    path=_path(value['path'])
    return path,_read(path,value['sha256'],limit)


def run(*, enabled=False, request_path=None, request_sha256=None):
    if enabled is False: return {'status':'OFF'}
    try:
        if enabled is not True: raise ValueError()
        path=_path(request_path);raw=_read(path,request_sha256,1024*1024);req=_json(raw)
        if (type(req) is not dict or set(req)!=_KEYS or req['schema']!='wd.q4-real-card-snapshot-request.v1'
                or req['source_mode'] not in ('synthetic_fixture','operator_authorized_source')
                or type(req['inputs']) is not dict or set(req['inputs'])!=_INPUTS): raise ValueError()
        inputs={}; watched=[]
        for name,spec in req['inputs'].items():
            f,data=_spec(spec,1024*1024);inputs[name]=data;watched.append((f,spec['sha256'],1024*1024,data))
        gen_path,gen_raw=_spec(req['source_generation'],1024*1024)
        watched.append((gen_path,req['source_generation']['sha256'],1024*1024,gen_raw))
        source=_path(req['source_path']);output=_path(req['output_root'],directory=True)
        git=req['git']
        if type(git) is not dict or set(git)!={'executable','sha256','repository','anchors'}: raise ValueError()
        exe=_path(git['executable']);git_raw=_read(exe,git['sha256'],64*1024*1024)
        watched.append((exe,git['sha256'],64*1024*1024,git_raw))
        repository=_path(git['repository'],directory=True)
        if req['source_mode']=='synthetic_fixture':
            if any('.codex-audit' not in p.parts for p in [path,source,gen_path,output,repository,*[w[0] for w in watched[:-1]]]):
                raise ValueError()
        cursor=req['cursor']
        if (type(cursor) is not dict or set(cursor)!={'offset','file_identity','generation'}
                or type(cursor['offset']) is not int or cursor['offset']<=0
                or type(cursor['file_identity']) is not str or type(cursor['generation']) is not str): raise ValueError()
        from waggledance.core.bridge_log_reader import BridgeCursor
        from tools import wd_q4_real_card_snapshot_producer as producer
        generation={name+'_bytes':value for name,value in inputs.items()}
        generation.update({name+'_sha256':req['inputs'][name]['sha256'] for name in
                           ('decision','vocabulary','path_allowlist','policy','roster')})
        generation.update(source_path=str(source),generation_path=str(gen_path),
            end_cursor=BridgeCursor(cursor['offset'],cursor['file_identity'],cursor['generation']),
            now_utc=req['now_utc'],output_root=output,generation_name=req['generation_name'],
            real_git_executable=str(exe),real_git_repository=str(repository),real_approved_anchors=git['anchors'])
    except Exception:
        raise Refused('real_card_cli_refused') from None
    try:
        result=producer.produce(enabled=True,generation=generation,encoder_config=req['encoder'],
                                trusted_model_root=req['trusted_model_root'])
    except producer.Refused:
        raise Refused('real_card_cli_refused') from None
    except BaseException:
        raise Unknown('real_card_cli_unknown') from None
    try:
        if _read(path,request_sha256,1024*1024)!=raw: raise ValueError()
        for f,pin,limit,expected in watched:
            if _read(f,pin,limit)!=expected: raise ValueError()
        expected={'status','pointer_sha256','memory_sha256','descriptor_sha256','completeness',
                  'source_authentication','publisher_authentication','freshness','rejected_invalid_expiry'}
        if (type(result) is not dict or set(result)!=expected or result['status']!='VALIDATED_STAGED'
                or type(result['rejected_invalid_expiry']) is not int or not 0<=result['rejected_invalid_expiry']<=4096
                or any(result[k]!='UNKNOWN' for k in ('completeness','source_authentication','publisher_authentication','freshness'))
                or any(type(result[k]) is not str or not _HEX.fullmatch(result[k]) for k in
                       ('pointer_sha256','memory_sha256','descriptor_sha256'))): raise ValueError()
        return {**result,'source_mode':req['source_mode']}
    except BaseException:
        raise Unknown('real_card_cli_unknown') from None


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
