"""Read-only bounded bridge prefix pin; private audit receipt, default OFF.

The source is never written and event contents are never emitted. A pin is a
bounded observed file identity/cursor/hash, not source authentication, a
complete history, a trusted clock, or protection from hostile same-user ABA.
Only generation-absent logs are supported. Time checks run between blocking
reads; a process-level watchdog is needed for a hard wall-clock bound.
"""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import time

_PAGE=16*1024*1024
_MAX_PREFIX=512*1024*1024
_MAX_HASH_WORK=6*1024*1024*1024
_MAX_PAGES=128


class Refused(ValueError): pass
class Unknown(RuntimeError): pass
class Interrupted(BaseException): pass


def _plain(path):
    for part in (path,*path.parents):
        info=part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400: raise ValueError()


def _write_new(path,raw):
    with path.open('xb') as stream:
        if stream.write(raw)!=len(raw): raise OSError()
        stream.flush();os.fsync(stream.fileno())


def _identity(bridge,path,expected,minimum):
    _plain(path)
    with bridge._open_log(path) as stream:
        if bridge._file_identity(stream)!=expected or os.fstat(stream.fileno()).st_size<minimum:
            raise ValueError()


def capture(*,enabled=False,source_path=None,generation_path=None,output_root=None,timeout_seconds=180):
    if enabled is False:return {'status':'OFF'}
    try:
        if (enabled is not True or type(source_path) is not str or type(generation_path) is not str
                or type(output_root) is not str or type(timeout_seconds) is not int
                or not 1<=timeout_seconds<=300): raise ValueError()
        source=Path(source_path);generation=Path(generation_path);root=Path(output_root)
        if (not source.is_absolute() or not generation.is_absolute() or not root.is_absolute()
                or '..' in source.parts or '..' in root.parts or '..' in generation.parts
                or source.name!='events.jsonl' or generation!=source.with_name('events.generation.json')
                or '.codex-audit' not in root.parts or not root.is_dir() or not source.is_file()):raise ValueError()
        _plain(source);_plain(root)
        if (source.resolve(strict=True)!=source or root.resolve(strict=True)!=root
                or root.is_relative_to(source.parent) or source.parent.is_relative_to(root)
                or os.path.lexists(generation) or any(root.iterdir())):raise ValueError()
        from waggledance.core import bridge_log_reader as bridge
        deadline=time.monotonic()+timeout_seconds
        tail=bridge.read_bridge_log_tail_lines(source,tail_rows=1,max_bytes=bridge.MAX_MAX_BYTES,
                                               generation_path=generation)
        if (tail.status not in (bridge.BridgeReadStatus.OK,bridge.BridgeReadStatus.IDLE)
                or tail.generation is not None or type(tail.end_offset) is not int
                or not 0<tail.end_offset<=_MAX_PREFIX or not tail.file_identity):raise ValueError()
        boundary=tail.end_offset
        cursor=bridge.BridgeCursor(0,tail.file_identity,None)
        digest=hashlib.sha256(b'').hexdigest();pages=work=0
        while cursor.offset<boundary:
            if time.monotonic()>deadline or os.path.lexists(generation):raise ValueError()
            pages+=1;limit=min(_PAGE,boundary-cursor.offset)
            if (pages>_MAX_PAGES or limit<1 or limit>bridge.MAX_MAX_BYTES
                    or work+2*(cursor.offset+limit)+2*boundary>_MAX_HASH_WORK):raise ValueError()
            result=bridge.read_bridge_log(source,cursor=cursor,max_bytes=limit,max_rows=bridge.MAX_MAX_ROWS,
                expected_prefix_sha256=digest,max_prefix_bytes=_MAX_PREFIX)
            if (not isinstance(result,bridge.BridgePrefixReadResult)
                    or result.status not in (bridge.BridgeReadStatus.OK,bridge.BridgeReadStatus.IDLE)
                    or result.candidate_cursor is None or result.candidate_prefix is None):raise ValueError()
            next_cursor=result.candidate_cursor
            if (next_cursor.file_identity!=tail.file_identity or next_cursor.generation is not None
                    or not cursor.offset<next_cursor.offset<=boundary):raise ValueError()
            work+=cursor.offset+next_cursor.offset
            cursor,digest=next_cursor,result.candidate_prefix.sha256
            _identity(bridge,source,tail.file_identity,boundary)
        if cursor.offset!=boundary or os.path.lexists(generation) or time.monotonic()>deadline:raise ValueError()
        with bridge._open_log(source) as stream:
            if (bridge._file_identity(stream)!=tail.file_identity or os.fstat(stream.fileno()).st_size<boundary
                    or bridge._hash_prefix(stream,boundary).hexdigest()!=digest):raise ValueError()
        _identity(bridge,source,tail.file_identity,boundary)
        if os.path.lexists(generation) or time.monotonic()>deadline:raise ValueError()
        receipt={'schema':'wd.q4-private-bridge-prefix-pin.v1',
                 'cursor':{'offset':cursor.offset,'file_identity':cursor.file_identity,'generation':None},
                 'prefix_sha256':digest,'source_authentication':'UNKNOWN','freshness':'UNKNOWN',
                 'completeness':'UNKNOWN','scope':'bounded_observed_prefix'}
        raw=json.dumps(receipt,sort_keys=True,separators=(',',':')).encode('ascii')
    except Exception:
        raise Refused('bridge_prefix_pin_refused') from None
    try:
        path=root/'prefix-pin.json'
        _write_new(path,raw)
        if path.read_bytes()!=raw:raise ValueError()
        if os.path.lexists(generation) or time.monotonic()>deadline:raise ValueError()
        _identity(bridge,source,tail.file_identity,boundary)
        with bridge._open_log(source) as stream:
            if (bridge._file_identity(stream)!=tail.file_identity or os.fstat(stream.fileno()).st_size<boundary
                    or bridge._hash_prefix(stream,boundary).hexdigest()!=digest):raise ValueError()
        _identity(bridge,source,tail.file_identity,boundary)
        if os.path.lexists(generation) or time.monotonic()>deadline:raise ValueError()
        return {'status':'PINNED','receipt_sha256':hashlib.sha256(raw).hexdigest(),
                'offset':cursor.offset,'source_authentication':'UNKNOWN','freshness':'UNKNOWN'}
    except (KeyboardInterrupt,SystemExit,GeneratorExit):
        raise Interrupted('bridge_prefix_pin_unknown') from None
    except Exception:
        raise Unknown('bridge_prefix_pin_unknown') from None


def main(argv=None):
    args=sys.argv[1:] if argv is None else argv
    if not args:print('{"status":"OFF"}');return 0
    if len(args)!=7 or args[0]!='--enabled' or args[1]!='--source' or args[3]!='--generation-path' or args[5]!='--output-root':
        print('{"status":"REFUSED"}',file=sys.stderr);return 2
    try:
        print(json.dumps(capture(enabled=True,source_path=args[2],generation_path=args[4],output_root=args[6]),sort_keys=True,separators=(',',':')))
        return 0
    except Refused:
        print('{"status":"REFUSED"}',file=sys.stderr);return 2
    except (Unknown,Interrupted):
        print('{"status":"UNKNOWN"}',file=sys.stderr);return 3


if __name__=='__main__':sys.exit(main())
