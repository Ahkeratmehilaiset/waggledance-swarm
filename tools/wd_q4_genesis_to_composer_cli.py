"""Fixed, default-off preparation from a completed genesis receipt to a descriptor.

Never launches genesis, installs, activates, updates CURRENT, or starts a lane.
Run via the verified candidate runtime with ``--enable --config PATH --sha256
DIGEST``; no arguments returns OFF. The closed config binds the genesis config,
its receipt and private tuple, request and public-plan templates, candidate
commit and manifest, current deployment state, and the permanent stage, output,
artifact-private and checkpoint roots. Both private roots carry caller-pinned
device/inode identities. The final PREPARED output contains the descriptor and
deployment-pointer paths and digests; it is not evidence of installation.
Every input pin is independently supplied in a caller-pinned private config.
A caller must separately verify the completed genesis receipt and chosen pins;
hashes are not authentication. Same-user filesystem races remain out of scope.
Once snapshot preparation begins, any failure is UNKNOWN; never retry blindly.
"""
import base64
import json
import os
from pathlib import Path
import re
import sys

from sandbox.mind.step2 import prefix_private_state as private_state
from sandbox.mind.step2 import shared_capture_adapter as capture
from sandbox.mind.step2 import shared_index_checkpoint as checkpoint
from tools import wd_q4_descriptor_cli as cli
from tools import wd_q4_genesis_bootstrap as genesis
from tools import wd_q4_genesis_cli as genesis_cli
from tools import wd_q4_genesis_request_prepare as request_prep
from tools import wd_q4_genesis_snapshot_prepare as snapshot_prep
from tools import wd_q4_live_snapshot_publish as live
from tools import wd_q4_live_update_recovery as recovery
from tools import wd_q4_production_continuation as continuation
from tools import wd_q4_cutoff_watermark as watermark
from tools import wd_q4_roster_from_fleet as roster_producer
from tools import wd_q4_staged_composer as composer
from tools import wd_q4_status_prefix_runner as runner
from waggledance.core.bridge_log_reader import _file_identity

_HEX = re.compile(r'[0-9a-f]{64}\Z')
_RECEIPT_V1 = {'status','pointer_sha256','runner_receipt_sha256'}
_RECEIPT_V2 = _RECEIPT_V1 | {'cutoff_utc','previous_cutoff_utc','route_sha256','cutoff_monotonicity'}
_RECEIPT_V3 = _RECEIPT_V2 | {'watermark_record_sha256'}
_KEYS = {'schema','genesis_config_path','genesis_config_sha256','receipt_path','receipt_sha256',
         'genesis_tuple_sha256','request_template_path','request_template_sha256',
         'public_plan_template_path','public_plan_template_sha256','expected_commit','stage_dir','output_dir',
         'generation_root','bundle_store_root','active_bundle_root','state_sha256',
         'artifact_private_root','artifact_root_device','artifact_root_inode',
         'index_root_device','index_root_inode'}


def _read(path, pin, limit=65536):
    return cli._read_pinned(path,limit,pin,'source_refused')


def _tuple_part(doc, name):
    item=doc[name]
    if type(item) is not dict or set(item)!={'bytes_b64','sha256'}: raise ValueError()
    raw=base64.b64decode(item['bytes_b64'],validate=True)
    if len(raw)>65536 or snapshot_prep._sha(raw)!=item['sha256']: raise ValueError()
    return genesis_cli._doc(raw)


def _verify_v2(receipt, runner_receipt, genesis_route, private, root, view, pointer_doc):
    """Bind cutoff evidence to the exact completed runner and verified predecessor."""
    expected_monotonicity=('COOPERATIVE_LEASE_PERSISTED_CRASH_UNKNOWN'
                           if set(receipt)==_RECEIPT_V3 else 'CALLER_ASSERTED_REPLAY_CRASH_UNPROVEN')
    if (receipt['status']!='caught_up_to_cutoff'
            or receipt['cutoff_monotonicity']!=expected_monotonicity
            or type(receipt['route_sha256']) is not str or not _HEX.fullmatch(receipt['route_sha256'])):
        raise ValueError()
    cutoff=runner._cutoff_datetime(receipt['cutoff_utc'])
    previous=runner._cutoff_datetime(receipt['previous_cutoff_utc'])
    floor=runner._cutoff_datetime(genesis_route['cutoff_utc'])
    route_pin=receipt['route_sha256']
    if runner_receipt.get('route_sha256')!=route_pin:
        raise ValueError()
    route_raw=_read(str(private/('q4-continuation-route-'+route_pin+'.json')),route_pin)
    route=genesis_cli._doc(route_raw)
    expected_keys=set(genesis_route)-{'genesis_skip_sha256'}
    dynamic={'cursor','prefix_sha256','plan_sha256','anchor_sha256','cutoff_utc'}
    if (set(route)!=expected_keys or route.get('cutoff_utc')!=receipt['cutoff_utc']
            or any(route[k]!=genesis_route[k] for k in expected_keys-dynamic)
            or route.get('anchor_sha256')!=view['anchor_sha256']
            or runner_receipt.get('plan_sha256')!=route.get('plan_sha256')):
        raise ValueError()
    prior_pin=pointer_doc['previous_pointer_sha256']
    if (type(prior_pin) is not str or not _HEX.fullmatch(prior_pin)
            or runner_receipt.get('prior_pointer_sha256')!=prior_pin): raise ValueError()
    previous_raw=checkpoint._read(root/('q4-pointer-'+prior_pin+'.json'),max_bytes=checkpoint._MAX_POINTER_BYTES)
    if snapshot_prep._sha(previous_raw)!=prior_pin: raise ValueError()
    previous_pointer=genesis_cli._doc(previous_raw)
    if (route.get('cursor')!=previous_pointer.get('watermark')
            or route.get('prefix_sha256')!=previous_pointer.get('prefix_watermark_sha256')):
        raise ValueError()
    previous_checkpoint=continuation._utc(continuation._checkpoint_cutoff(previous_pointer,genesis_route))
    current_checkpoint=continuation._utc(continuation._checkpoint_cutoff(view,genesis_route))
    if previous < max(floor,previous_checkpoint) or cutoff < previous or current_checkpoint!=cutoff:
        raise ValueError()


class _WatermarkIncomplete(Exception):
    """A pending cooperative attempt cannot be classified as a refusal."""


def _verify_v3(receipt, runner_receipt, private, root, pointer_pin, prefix):
    """Recheck cooperative watermark and actual current state under both locks."""
    pin=receipt['watermark_record_sha256']
    if type(pin) is not str or not _HEX.fullmatch(pin): raise ValueError()
    private_lock=private/private_state._LOCK
    checkpoint_lock=root/checkpoint.LOCK
    if not private_lock.is_file() or not checkpoint_lock.is_file(): raise _WatermarkIncomplete()
    composer.pointer._chain(private_lock)
    composer.pointer._chain(checkpoint_lock)
    with private_state.locked_state(private,root,enabled=True) as state:
        with private_state._checkpoint_lock(root,0.2):
            if (os.path.lexists(private/'q4-cutoff.pending.json')
                    or any(os.path.lexists(path/runner._PENDING) for path in (private,root))
                    or checkpoint._active_fence(root) is not None):
                raise _WatermarkIncomplete()
            if checkpoint._current_pointer(root)!=pointer_pin: raise ValueError()
            state.require(pointer_pin,prefix)
            record=watermark._load_head(private,pin)
            if (record is None or record['pointer_sha256']!=pointer_pin
                    or record['route_sha256']!=receipt['route_sha256']
                    or record['runner_receipt_sha256']!=receipt['runner_receipt_sha256']
                    or record['cutoff_utc']!=receipt['cutoff_utc']
                    or runner_receipt.get('route_sha256')!=record['route_sha256']):
                raise ValueError()
            if watermark._load_head(private,pin)!=record: raise ValueError()


def _preflight(path, pin):
    config=genesis_cli._doc(_read(path,pin))
    if set(config)!=_KEYS or config['schema']!='wd.q4-genesis-to-composer.v1': raise ValueError()
    if (type(config['expected_commit']) is not str or
            re.fullmatch(r'[0-9a-f]{40}',config['expected_commit']) is None): raise ValueError()
    for key in (k for k in _KEYS if k.endswith('sha256')):
        if type(config[key]) is not str or not _HEX.fullmatch(config[key]): raise ValueError()
    gc=genesis_cli._doc(_read(config['genesis_config_path'],config['genesis_config_sha256']))
    if set(gc)!=genesis_cli._KEYS or gc['schema']!='wd.q4-genesis-cli.v1': raise ValueError()
    receipt=genesis_cli._doc(_read(config['receipt_path'],config['receipt_sha256']))
    receipt_keys=set(receipt)
    if (receipt_keys not in (_RECEIPT_V1,_RECEIPT_V2,_RECEIPT_V3)
            or receipt['status'] not in ('PUBLISHED_GENESIS','caught_up_to_cutoff')
            or any(type(receipt[k]) is not str or not _HEX.fullmatch(receipt[k])
                   for k in ('pointer_sha256','runner_receipt_sha256'))):
        raise ValueError()
    pointer_pin=receipt['pointer_sha256']
    bundle=Path(gc['bundle_root']); root=Path(gc['checkpoint_root']); private=Path(gc['private_state_root'])
    root_info=os.stat(root)
    if (type(config['index_root_device']) is not int or type(config['index_root_inode']) is not int
            or (root_info.st_dev,root_info.st_ino)!=(config['index_root_device'],config['index_root_inode'])):
        raise ValueError()
    tuple_path=str(private/'q4-genesis-tuple-v1.json')
    gt=genesis_cli._doc(_read(tuple_path,config['genesis_tuple_sha256'],1024*1024))
    if (gt.get('schema')!='wd.q4-genesis-private-tuple.v1' or gt.get('publication')!='UNKNOWN'
            or gt.get('allow_partial_genesis')!=gc['allow_partial_genesis']
            or gt.get('independent_source_pin')!={'identity':gc['source_file_identity'],
                'prefix_bytes':gc['source_bytes'],'prefix_sha256':gc['source_sha256']}):
        raise ValueError()
    route, plan, anchor=(_tuple_part(gt,name) for name in ('route','plan','anchor'))
    expected_cursor=route.get('cursor')
    if (type(expected_cursor) is not dict or set(expected_cursor)!={'offset','file_identity','generation'}
            or type(expected_cursor['offset']) is not int or expected_cursor['offset']<0
            or expected_cursor['file_identity']!=gc['source_file_identity']
            or expected_cursor['generation']!=gc['generation']): raise ValueError()
    skip_pin=route.get('genesis_skip_sha256')
    if expected_cursor['offset']==0:
        if skip_pin is not None or route.get('prefix_sha256')!=snapshot_prep._sha(b''): raise ValueError()
    else:
        if type(skip_pin) is not str or not _HEX.fullmatch(skip_pin): raise ValueError()
        skip_raw=runner._read_genesis_skip(private)
        skip=runner._genesis_skip(skip_raw)
        if (snapshot_prep._sha(skip_raw)!=skip_pin or skip['offset']!=expected_cursor['offset']
                or skip['file_identity']!=expected_cursor['file_identity']
                or skip['generation']!=expected_cursor['generation']
                or skip['prefix_sha256']!=route.get('prefix_sha256')
                or any(skip[k]!=route.get(k) for k in ('policy_sha256','roster_sha256','public_policy_sha256'))):
            raise ValueError()
    expected_anchor={'schema':'wd.q4-source-anchor.v1','source_path':gc['source_path'],
        'generation_path':gc['generation_path'],'cursor_start':expected_cursor,'policy_sha256':gc['policy_sha256'],
        'roster_sha256':route.get('roster_sha256'),'status_mode':'public-v1',
        'public_policy_sha256':gc['public_policy_sha256']}
    if (route.get('source')!=gc['source_path'] or route.get('generation_path')!=gc['generation_path']
            or route.get('checkpoint_root')!=gc['checkpoint_root'] or route.get('private_state_root')!=gc['private_state_root']
            or route.get('trusted_model_root')!=gc['trusted_model_root'] or route.get('policy_sha256')!=gc['policy_sha256']
            or route.get('public_policy_sha256')!=gc['public_policy_sha256']
            or route.get('max_bytes')!=gc['max_bytes'] or route.get('max_rows')!=gc['max_rows']
            or route.get('cutoff_utc')!=gc['cutoff_utc'] or route.get('batch_rows',False)!=gc['batch_rows']
            or route.get('plan_sha256')!=gt['plan']['sha256'] or route.get('anchor_sha256')!=gt['anchor']['sha256']
            or plan.get('pins',{}).get('anchor_sha256')!=gt['anchor']['sha256']
            or {k:v for k,v in plan.items() if k!='pins'}!=gc['plan_template']
            or anchor!=expected_anchor): raise ValueError()
    manifest=genesis_cli._doc(_read(str(bundle/'deployment-manifest.json'),gc['manifest_sha256'],2*1024*1024))
    if (manifest.get('source_commit')!=config['expected_commit'] or type(manifest.get('files')) is not dict
            or str(manifest['files'].get('wd-fleet.json','')).lower()!=gc['fleet_sha256']): raise ValueError()
    fleet=_read(str(bundle/'wd-fleet.json'),gc['fleet_sha256'],1024*1024)
    policy=_read(gc['policy_path'],gc['policy_sha256']); public=_read(gc['public_policy_path'],gc['public_policy_sha256'])
    roster,roster_sha=roster_producer.build_roster_snapshot(fleet,gc['fleet_sha256'],enabled=True)
    if route.get('roster_sha256')!=roster_sha: raise ValueError()
    tasks,_=capture.strict_pins(policy,gc['policy_sha256'],roster,roster_sha)
    view,prefix=checkpoint.read_verified_prefix_pointer(root,tasks,expected_pointer_sha256=pointer_pin,
        policy_bytes=policy,policy_sha256=gc['policy_sha256'],roster_bytes=roster,roster_sha256=roster_sha,
        expected_public_policy_sha256=gc['public_policy_sha256'])
    _,runner_receipt=recovery._find_runner_receipt(private,receipt['runner_receipt_sha256'])
    pointer_raw=checkpoint._read(root/checkpoint.POINTER,max_bytes=checkpoint._MAX_POINTER_BYTES)
    pointer_doc=genesis_cli._doc(pointer_raw)
    if (runner_receipt.get('schema')!='wd.q4-status-result.v1'
            or snapshot_prep._sha(pointer_raw)!=pointer_pin
            or runner_receipt.get('pointer_sha256')!=pointer_pin
            or runner_receipt.get('generation')!=view['generation']
            or runner_receipt.get('prefix_sha256')!=prefix.sha256
            or runner_receipt.get('watermark_sha256')!=view['watermark_sha256']
            or (receipt['status']=='PUBLISHED_GENESIS' and
                (runner_receipt.get('status')!='PUBLISHED_GENESIS'
                 or pointer_doc.get('previous_pointer_sha256') is not None))
            or (receipt['status']=='caught_up_to_cutoff' and
                (runner_receipt.get('status') not in ('PUBLISHED_HISTORY','EMPTY_ADVANCED')
                 or pointer_doc.get('previous_pointer_sha256') is None
                 or runner_receipt.get('prior_pointer_sha256')!=pointer_doc['previous_pointer_sha256']))
            or prefix.cursor.offset<max(gc['source_bytes'],expected_cursor['offset'])
            or prefix.cursor.file_identity!=gc['source_file_identity'] or prefix.cursor.generation!=gc['generation']):
        raise ValueError()
    if receipt_keys in (_RECEIPT_V2,_RECEIPT_V3):
        _verify_v2(receipt,runner_receipt,route,private,root,view,pointer_doc)
    source=runner._plain_file(gc['source_path'])
    with source.open('rb') as stream:
        size=os.fstat(stream.fileno()).st_size
        if (size < max(prefix.cursor.offset,gc['source_bytes'])
                or _file_identity(stream)!=gc['source_file_identity']
                or genesis._snapshot(stream,gc['source_bytes'])!=gc['source_sha256']
                or genesis._snapshot(stream,prefix.cursor.offset)!=prefix.sha256
                or genesis._snapshot(stream,expected_cursor['offset'])!=route['prefix_sha256']
                or (receipt['status']=='PUBLISHED_GENESIS'
                    and genesis._complete_prefix_size(stream,size)>prefix.cursor.offset)): raise ValueError()
    private_lock=private/private_state._LOCK
    if not private_lock.is_file(): raise ValueError()
    composer.pointer._chain(private_lock)
    with private_state.locked_state(private,root,enabled=True) as state:
        state.require(pointer_pin,prefix)
    if receipt_keys==_RECEIPT_V3:
        _verify_v3(receipt,runner_receipt,private,root,pointer_pin,prefix)
    stage=composer.pointer._absolute(config['stage_dir']); rootgen=composer.pointer._absolute(config['generation_root'])
    store=composer.pointer._absolute(config['bundle_store_root']); active=composer.pointer._absolute(config['active_bundle_root'])
    output=composer.pointer._absolute(config['output_dir'])
    artifact_root=composer.pointer._absolute(config['artifact_private_root'])
    composer.pointer._chain(artifact_root)
    identity=os.stat(artifact_root)
    if (not artifact_root.is_dir() or type(config['artifact_root_device']) is not int
            or type(config['artifact_root_inode']) is not int
            or (identity.st_dev,identity.st_ino)!=(config['artifact_root_device'],config['artifact_root_inode'])
            or not composer._identity_contains(artifact_root,stage)
            or not composer._identity_contains(artifact_root,output)
            or any(not live._disjoint(artifact_root,item) for item in
                   (bundle,store,root,private,source.parent,Path(gc['trusted_model_root'])))
            or any(not live._disjoint(root,item) for item in (bundle,store,source.parent))): raise ValueError()
    composer._check_roots(stage,bundle,rootgen,store,active)
    composer._anchored_active(config['state_sha256'],active,store)
    composer.pointer._chain(output)
    if (not output.is_dir() or any(output.iterdir()) or
            any(not live._disjoint(output,item) for item in (bundle,root,stage,private))): raise ValueError()
    _read(config['request_template_path'],config['request_template_sha256'])
    _read(config['public_plan_template_path'],config['public_plan_template_sha256'])
    v3=(receipt,runner_receipt,private,root,pointer_pin,prefix) if receipt_keys==_RECEIPT_V3 else None
    return config,gc,pointer_pin,policy,roster,roster_sha,public,v3


def _emit(result):
    print(json.dumps(result,sort_keys=True,separators=(',',':'),ensure_ascii=True))


def main(argv=None):
    args=list(sys.argv[1:] if argv is None else argv)
    if not args:
        _emit({'status':'OFF'}); return 0
    if (len(args)!=5 or args[0]!='--enable' or args[1]!='--config' or args[3]!='--sha256'
            or not _HEX.fullmatch(args[4])):
        _emit({'status':'REFUSED'}); return 2
    try:
        c,g,pin,policy,roster,roster_sha,public,v3=_preflight(args[2],args[4])
    except _WatermarkIncomplete:
        _emit({'status':'UNKNOWN'}); return 3
    except Exception:
        _emit({'status':'REFUSED'}); return 2
    except BaseException:
        _emit({'status':'UNKNOWN'}); return 3
    try:
        snap=snapshot_prep.prepare_snapshot(enabled=True,checkpoint_root=g['checkpoint_root'],expected_pointer_sha256=pin,
            policy_bytes=policy,expected_policy_sha256=g['policy_sha256'],roster_bytes=roster,expected_roster_sha256=roster_sha,
            public_policy_bytes=public,expected_public_policy_sha256=g['public_policy_sha256'])
        if v3 is not None:
            _verify_v3(*v3)
        ready=request_prep.prepare(enabled=True,request_template_path=c['request_template_path'],
            expected_request_sha256=c['request_template_sha256'],public_plan_template_path=c['public_plan_template_path'],
            expected_public_plan_template_sha256=c['public_plan_template_sha256'],bundle_root=g['bundle_root'],
            expected_commit=c['expected_commit'],expected_manifest_sha256=g['manifest_sha256'],
            checkpoint_root=g['checkpoint_root'],policy_paths=snap['policy_paths'],snapshot_bytes=snap['snapshot_bytes'],
            expected_snapshot_sha256=snap['snapshot_sha256'],expected_pointer_sha256=pin,
            stage_dir=c['stage_dir'],output_dir=c['output_dir'])
        out=composer.compose(enabled=True,request_path=ready['request_path'],stage_dir=c['stage_dir'],
            expected_bundle=g['bundle_root'],expected_commit=c['expected_commit'],
            expected_manifest_sha256=g['manifest_sha256'].upper(),expected_public_plan_sha256=ready['public_plan_sha256'],
            generation_root=c['generation_root'],bundle_store_root=c['bundle_store_root'],
            active_bundle_root=c['active_bundle_root'],expected_state_sha256=c['state_sha256'])
        if v3 is not None:
            _verify_v3(*v3)
        if out.get('status')!='PREPARED': raise ValueError()
        _emit({'status':'PREPARED','pointer_path':out['pointer_path'],'pointer_sha256':out['pointer_sha256'],
               'descriptor_path':out['descriptor_path'],'descriptor_sha256':out['descriptor_sha256']})
        return 0
    except BaseException:
        _emit({'status':'UNKNOWN'}); return 3


if __name__=='__main__':
    sys.exit(main())
