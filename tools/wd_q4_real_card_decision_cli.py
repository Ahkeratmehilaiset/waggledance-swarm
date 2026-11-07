"""Default-off, audit-only writer for an independently pinned card decision.

The request, its separate input pins and external prefix anchor are consistency
inputs, not authorization, source authenticity or proof of freshness.  This
never writes to the bridge, installs a bundle, or activates a decision.  A write
failure or later mismatch leaves UNKNOWN output that must not be retried blindly.
The caller must retain the private request, anchor and output pins separately.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import sys

from tools import wd_q4_real_card_snapshot_cli as io

_HEX=re.compile(r'[0-9a-f]{64}\Z')
_INPUTS=frozenset({'policy','roster','card_policy','public_policy','vocabulary','path_allowlist','anchors'})
_KEYS=frozenset({'schema','mode','source_mode','inputs','expected_card_module_sha256','source_path',
                 'cursor','prefix_sha256','anchor','git'})


class Refused(ValueError): pass
class Unknown(RuntimeError): pass
class Interrupted(BaseException): pass


def _sha(raw):return hashlib.sha256(raw).hexdigest()


def _write_new(path,raw):
    with path.open('xb') as stream:
        if stream.write(raw)!=len(raw):raise OSError()
        stream.flush();os.fsync(stream.fileno())


def _disjoint(left,right):
    return not (left==right or left.is_relative_to(right) or right.is_relative_to(left))


def run(*,enabled=False,request_path=None,request_sha256=None,output_root=None):
    if enabled is False:return {'status':'OFF'}
    try:
        if enabled is not True:raise ValueError()
        request_file=io._path(request_path);request_raw=io._read(request_file,request_sha256,1024*1024)
        req=io._json(request_raw)
        if (type(req) is not dict or set(req)!=_KEYS or req['schema']!='wd.q4-real-card-decision-request.v1'
                or req['mode'] not in ('real','synthetic')
                or req['source_mode'] not in ('synthetic_fixture','operator_authorized_source')
                or type(req['inputs']) is not dict or set(req['inputs'])!=_INPUTS):raise ValueError()
        output=io._path(output_root,directory=True)
        if '.codex-audit' not in output.parts or any(output.iterdir()):raise ValueError()
        watched=[];inputs={}
        for name,spec in req['inputs'].items():
            f,data=io._spec(spec,1024*1024);inputs[name]=data;watched.append((f,spec['sha256'],1024*1024,data))
        source=io._path(req['source_path'])
        spec=req['anchor']
        if type(spec) is not dict or set(spec)!={'root','generation_sha256','binding_sha256'}:raise ValueError()
        anchor_root=io._path(spec['root'],directory=True)
        if (not _disjoint(output,source.parent) or not _disjoint(output,anchor_root)
                or request_file.is_relative_to(output) or any(w[0].is_relative_to(output) for w in watched)):raise ValueError()
        git=req['git']
        if type(git) is not dict or set(git)!={'executable','sha256','repository'}:raise ValueError()
        exe=io._path(git['executable']);exe_raw=io._read(exe,git['sha256'],64*1024*1024)
        watched.append((exe,git['sha256'],64*1024*1024,exe_raw))
        repository=io._path(git['repository'],directory=True)
        if not _disjoint(output,repository):raise ValueError()
        if req['source_mode']=='synthetic_fixture' and any('.codex-audit' not in p.parts for p in
             [request_file,source,anchor_root,output,repository,*[w[0] for w in watched[:-1]]]):raise ValueError()
        c=req['cursor']
        if (type(c) is not dict or set(c)!={'offset','file_identity','generation'}
                or type(c['offset']) is not int or c['offset']<=0
                or type(c['file_identity']) is not str or c['generation'] is not None
                or type(req['prefix_sha256']) is not str or not _HEX.fullmatch(req['prefix_sha256'])):raise ValueError()
        from waggledance.core.bridge_log_reader import BridgeCursor
        from tools import wd_q4_external_generation_anchor as anchor
        from tools import wd_q4_real_card_decision_prepare as prepare
        from sandbox.mind.step2 import shared_card_projection as projection
        original=BridgeCursor(c['offset'],c['file_identity'],None)
        anchor_args=dict(enabled=True,source_path=str(source),original_cursor=original,
            expected_prefix_sha256=req['prefix_sha256'],output_root=str(anchor_root),
            expected_generation_sha256=spec['generation_sha256'],expected_binding_sha256=spec['binding_sha256'])
        verified=anchor.verify(**anchor_args)
        if verified.get('status')!='VERIFIED_WITHIN_PREFIX':raise ValueError()
        adapted=BridgeCursor(c['offset'],c['file_identity'],verified['generation'])
        kwargs={name+'_bytes':value for name,value in inputs.items()}
        kwargs.update({name+'_sha256':req['inputs'][name]['sha256'] for name in _INPUTS})
        kwargs.update(enabled=True,mode=req['mode'],expected_card_module_sha256=req['expected_card_module_sha256'],
            source_path=str(source),generation_path=str(anchor_root/'generation.json'),end_cursor=adapted,
            expected_prefix_sha256=req['prefix_sha256'],git_executable=str(exe),git_executable_sha256=git['sha256'],
            git_repository=str(repository))
        prepared=prepare.prepare(**kwargs)
        fields={'status','decision_bytes','decision_sha256','mode','public_policy_sha256','anchors_sha256',
                'source_authentication','publisher_authentication','freshness','completeness','card_validation','file_identity_binding'}
        if (type(prepared) is not dict or set(prepared)!=fields or prepared['status']!='PREPARED'
                or prepared['mode']!=req['mode'] or prepared['public_policy_sha256']!=req['inputs']['public_policy']['sha256']
                or prepared['anchors_sha256']!=req['inputs']['anchors']['sha256']
                or any(prepared[k]!='UNKNOWN' for k in ('source_authentication','publisher_authentication','freshness','completeness'))
                or prepared['card_validation']!='NOT_RUN' or prepared['file_identity_binding']!='CALLER_MUST_RETAIN'):
            raise ValueError()
        raw=prepared['decision_bytes'];pin=prepared['decision_sha256']
        if type(raw) is not bytes or type(pin) is not str or not _HEX.fullmatch(pin) or _sha(raw)!=pin:raise ValueError()
        decision=projection.verify_route_decision(raw,pin,policy_sha256=req['inputs']['policy']['sha256'],
                                                  roster_sha256=req['inputs']['roster']['sha256'])
        if (decision['synthetic'] is not (req['mode']=='synthetic') or decision['card_module_sha256']!=req['expected_card_module_sha256']
                or decision['card_policy_sha256']!=req['inputs']['card_policy']['sha256']
                or decision['vocabulary_sha256']!=req['inputs']['vocabulary']['sha256']
                or decision['path_allowlist_sha256']!=req['inputs']['path_allowlist']['sha256']
                or decision['source_prefix']!={'sha256':req['prefix_sha256'],'offset':c['offset'],'generation':adapted.generation}):raise ValueError()
        def recheck():
            if io._read(request_file,request_sha256,1024*1024)!=request_raw:raise ValueError()
            for f,digest,limit,data in watched:
                if io._read(f,digest,limit)!=data:raise ValueError()
            if anchor.verify(**anchor_args)!=verified:raise ValueError()
        recheck()
    except Exception:
        raise Refused('card_decision_cli_refused') from None
    try:
        target=output/'card-decision.json'
        _write_new(target,raw)
        if io._read(target,pin,256*1024)!=raw:raise ValueError()
        recheck()
        if projection.verify_route_decision(raw,pin,policy_sha256=req['inputs']['policy']['sha256'],
                                             roster_sha256=req['inputs']['roster']['sha256'])!=decision:raise ValueError()
        return {'status':'WRITTEN','decision_sha256':pin,'mode':req['mode'],'source_mode':req['source_mode'],
                'source_authentication':'UNKNOWN','publisher_authentication':'UNKNOWN','freshness':'UNKNOWN',
                'completeness':'UNKNOWN','card_validation':'NOT_RUN'}
    except (KeyboardInterrupt,SystemExit,GeneratorExit):
        raise Interrupted('card_decision_cli_unknown') from None
    except Exception:
        raise Unknown('card_decision_cli_unknown') from None


def main(argv=None):
    args=sys.argv[1:] if argv is None else argv
    if not args:print('{"status":"OFF"}');return 0
    if (len(args)!=7 or args[0]!='--enabled' or args[1]!='--request' or args[3]!='--request-sha256'
            or args[5]!='--output-root'):
        print('{"status":"REFUSED"}',file=sys.stderr);return 2
    try:
        print(json.dumps(run(enabled=True,request_path=args[2],request_sha256=args[4],output_root=args[6]),sort_keys=True,separators=(',',':')))
        return 0
    except Refused:
        print('{"status":"REFUSED"}',file=sys.stderr);return 2
    except (Unknown,Interrupted):
        print('{"status":"UNKNOWN"}',file=sys.stderr);return 3


if __name__=='__main__':sys.exit(main())
