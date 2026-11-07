"""Default-off, read-only plan for a five-identity synthetic E5 smoke.

No lane is started and no guard or descriptor is created by this module. The
caller independently authenticates all supplied pins and must create a fresh
synthetic fleet, descriptor and guard state before execution. These checks do
not authenticate a caller or agent, prove native cleanup, or demonstrate live
agent use. Guard names are proposals: absence, creation and actual path use are
not checked. Never use real bridge or agent state as a fixture.
"""
import hashlib
import json
import re
import sys
from pathlib import Path

LANES=('codex-lead-1','codex-tools-1','claude-rco-1','claude-rco-2','fable-5')
_HEX64=re.compile(r'[0-9a-f]{64}\Z')
_IDENTITY=re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})\Z')
_RUN=re.compile(r'synthetic-[a-z0-9-]{3,64}\Z')


def _json(raw,pin):
    if (type(raw) is not bytes or not 0<len(raw)<=65536 or type(pin) is not str
            or _HEX64.fullmatch(pin) is None or hashlib.sha256(raw).hexdigest()!=pin):
        raise ValueError()
    def unique(pairs):
        out={}
        for key,value in pairs:
            if key in out: raise ValueError()
            out[key]=value
        return out
    result=json.loads(raw.decode('utf-8','strict'),object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    if type(result) is not dict: raise ValueError()
    return result


def _successful(receipt):
    count,refs=receipt.get('result_count'),receipt.get('reference_count')
    return (receipt.get('exit_code')==0 and type(receipt.get('exit_code')) is int
            and all(type(receipt.get(key)) is str and _HEX64.fullmatch(receipt[key]) is not None
                    for key in ('stdout_sha256','stderr_sha256'))
            and receipt.get('status')=='VALIDATED' and receipt.get('cleanup')=='chain_confirmed'
            and type(count) is int and type(refs) is int and 0<=count<=3 and refs==count
            and ((receipt.get('answer_status')=='ABSTAIN' and count==0)
                 or (receipt.get('answer_status')=='ANSWER' and count>0)))


def make_plan(*, enabled=False, first_receipt_bytes=None, expected_first_sha256=None,
              chain_report_bytes=None, expected_chain_sha256=None,
              expected_runtime_manifest_sha256=None, fresh_run_id=None):
    if enabled is False: return {'status':'OFF'}
    try:
        if (enabled is not True or type(fresh_run_id) is not str or _RUN.fullmatch(fresh_run_id) is None
                or type(expected_runtime_manifest_sha256) is not str
                or _HEX64.fullmatch(expected_runtime_manifest_sha256) is None): raise ValueError()
        first=_json(first_receipt_bytes,expected_first_sha256)
        chain=_json(chain_report_bytes,expected_chain_sha256)
        if (first.get('schema')!='synthetic-real-genesis-lane.v1' or first.get('lane')!='codex-lead-1'
                or first.get('identity')!='declared-not-authenticated'
                or first.get('guard_provision')!='lower-level synthetic, anchored CLI not used'
                or first.get('coverage')!='UNKNOWN' or first.get('freshness')!='UNKNOWN'
                or not _successful(first) or first.get('runtime_manifest_sha256')!=expected_runtime_manifest_sha256
                or chain.get('schema')!='synthetic-real-genesis-chain-prep.v1' or chain.get('status')!='PREPARED'
                or chain.get('authority')!='synthetic bundle and manifest; direct materialization bypasses fixed CURRENT composer'):
            raise ValueError()
        for key in ('manifest_sha256','baseline_sha256','genesis_pointer_sha256'):
            if (type(first.get(key)) is not str or _HEX64.fullmatch(first[key]) is None
                    or first[key]!=chain.get(key)): raise ValueError()
        for key in ('runtime_revision','runtime_generation'):
            if (type(first.get(key)) is not str or _IDENTITY.fullmatch(first[key]) is None
                    or first[key]!=chain.get(key)): raise ValueError()
        return {'status':'READY_TO_PREPARE_SYNTHETIC_GUARDS','fresh_run_id':fresh_run_id,
                'first_receipt_sha256':expected_first_sha256,'chain_report_sha256':expected_chain_sha256,
                'runtime_manifest_sha256':expected_runtime_manifest_sha256,
                'runtime_revision':first['runtime_revision'],'runtime_generation':first['runtime_generation'],
                'genesis_pointer_sha256':first['genesis_pointer_sha256'],
                'lanes':[{'lane':lane,'guard_relative':fresh_run_id+'/shared/.codex-audit/'+lane+'/q4-caller-state'}
                         for lane in LANES],
                'guard_existence':'NOT_CHECKED','fixture_setup':'NOT_RUN',
                'native_execution':'NOT_RUN','real_agent_use':'UNKNOWN'}
    except Exception:
        return {'status':'UNKNOWN'}


def classify_results(plan,receipts,*,expected_receipt_sha256=None,expected_fresh_manifest_sha256=None,
                     expected_fresh_descriptor_sha256=None):
    """Check individually pinned reports; the caller must authenticate each pin."""
    try:
        if (type(plan) is not dict or plan.get('status')!='READY_TO_PREPARE_SYNTHETIC_GUARDS'
                or type(plan.get('fresh_run_id')) is not str or _RUN.fullmatch(plan['fresh_run_id']) is None
                or any(type(pin) is not str or _HEX64.fullmatch(pin) is None
                       for pin in (expected_fresh_manifest_sha256,expected_fresh_descriptor_sha256))
                or type(receipts) is not list or len(receipts)!=len(LANES)
                or type(expected_receipt_sha256) is not dict or set(expected_receipt_sha256)!=set(LANES)):
            raise ValueError()
        by_lane={}
        for raw in receipts:
            # Parse only to select its caller-pinned digest, then parse and
            # revalidate the complete bytes with the duplicate-key guard.
            if type(raw) is not bytes or not 0<len(raw)<=65536: raise ValueError()
            guessed=json.loads(raw.decode('utf-8','strict'))
            if type(guessed) is not dict or guessed.get('lane') not in LANES: raise ValueError()
            row=_json(raw,expected_receipt_sha256[guessed['lane']])
            if (row.get('schema')!='synthetic-real-genesis-lane.v1'
                    or row.get('lane') not in LANES or row['lane'] in by_lane
                    or row.get('run_id')!=plan['fresh_run_id']
                    or row.get('guard_relative')!=plan['fresh_run_id']+'/shared/.codex-audit/'+row['lane']+'/q4-caller-state'
                    or row.get('identity')!='declared-not-authenticated'
                    or row.get('coverage')!='UNKNOWN' or row.get('freshness')!='UNKNOWN'
                    or row.get('manifest_sha256')!=expected_fresh_manifest_sha256
                    or row.get('baseline_sha256')!=expected_fresh_descriptor_sha256
                    or any(row.get(key)!=plan.get(key) for key in
                           ('runtime_manifest_sha256','runtime_revision','runtime_generation','genesis_pointer_sha256'))
                    or not _successful(row)):
                raise ValueError()
            by_lane[row['lane']]=row
        if set(by_lane)!=set(LANES): raise ValueError()
        return {'status':'SYNTHETIC_FIVE_RECEIPTS_MATCH','real_agent_use':'UNKNOWN',
                'publisher_authentication':'UNKNOWN','freshness':'UNKNOWN'}
    except Exception:
        return {'status':'UNKNOWN'}


def main(argv=None):
    args=sys.argv[1:] if argv is None else argv
    if not args:
        print('{"status":"OFF"}')
        return 0
    try:
        if len(args)!=7 or args[0]!='--enable': raise ValueError()
        first,first_pin,chain,chain_pin,runtime_pin,run_id=args[1:]
        with Path(first).open('rb') as stream: first_bytes=stream.read(65537)
        with Path(chain).open('rb') as stream: chain_bytes=stream.read(65537)
        result=make_plan(enabled=True,first_receipt_bytes=first_bytes,expected_first_sha256=first_pin,
            chain_report_bytes=chain_bytes,expected_chain_sha256=chain_pin,
            expected_runtime_manifest_sha256=runtime_pin,fresh_run_id=run_id)
    except Exception:
        result={'status':'UNKNOWN'}
    print(json.dumps(result,sort_keys=True,separators=(',',':')))
    return 0 if result['status']=='READY_TO_PREPARE_SYNTHETIC_GUARDS' else 2


if __name__=='__main__':
    sys.exit(main())
