"""Default-off adoption of one completed V3 continuation into live readers.

Only caller-pinned inputs are accepted. A durable pending marker remains on any
uncertain write or interruption; never retry automatically. Cooperative locks do
not authenticate publishers, exclude hostile same-user writes, or prove freshness.
This module neither schedules work nor installs a bundle or launches a query.
"""
from contextlib import ExitStack
import hashlib
import hmac
import json
import os
from pathlib import Path

from sandbox.mind.step2 import shared_capture_adapter as capture
from sandbox.mind.step2 import shared_index_checkpoint as checkpoint
from sandbox.mind.step2 import prefix_private_state as private_state
from sandbox.mind.step2.reader_slot import reader_slot
from tools import wd_q4_genesis_bootstrap as genesis
from tools import wd_q4_genesis_to_composer_cli as composer
from tools import wd_q4_lane_caller_cli as caller
from tools import wd_q4_live_snapshot_publish as live
from tools import wd_q4_live_update_once as update
from tools import wd_q4_live_update_recovery as recovery
from tools import wd_q4_status_prefix_runner as runner
from waggledance.core.bridge_log_reader import _file_identity


class AdoptRefused(ValueError):
    """No adoption marker was attempted."""


class AdoptOutcomeUnknown(RuntimeError):
    """Adoption may have written state; reconciliation is required."""


class AdoptInterrupted(BaseException):
    """An interrupted adoption must not be retried without reconciliation."""


class _Pending(Exception):
    """An existing attempt is not evidence of a clean state."""


def _sha(raw): return hashlib.sha256(raw).hexdigest()


def _pinned(raw,pin,limit):
    if (type(raw) is not bytes or not 0<len(raw)<=limit or type(pin) is not str
            or not caller._HEX.fullmatch(pin) or not hmac.compare_digest(_sha(raw),pin)):
        raise ValueError()
    return caller._json(raw)


def _source(route,independent,prefix):
    if (type(independent) is not dict or set(independent)!={'identity','prefix_bytes','prefix_sha256'}
            or type(independent['prefix_bytes']) is not int or independent['prefix_bytes']<=0
            or not runner._HEX.fullmatch(independent['prefix_sha256'])
            or independent['identity']!=route['cursor']['file_identity']
            or prefix.cursor.file_identity!=independent['identity']
            or prefix.cursor.generation!=route['cursor']['generation']
            or prefix.cursor.offset<max(independent['prefix_bytes'],route['cursor']['offset'])):
        raise ValueError()
    source=runner._plain_file(route['source'])
    with source.open('rb') as stream:
        if (os.fstat(stream.fileno()).st_size<prefix.cursor.offset
                or _file_identity(stream)!=independent['identity']
                or genesis._snapshot(stream,independent['prefix_bytes'])!=independent['prefix_sha256']
                or genesis._snapshot(stream,route['cursor']['offset'])!=route['prefix_sha256']
                or genesis._snapshot(stream,prefix.cursor.offset)!=prefix.sha256):
            raise ValueError()


def _prior_pointer(baseline_path,baseline_sha256,baseline,manifest_sha256):
    bp=live.pointer_io._absolute(baseline_path)
    selector=bp.parent.parent/'q4-live-current.json'
    if not os.path.lexists(selector):
        return baseline[4]['pointer_sha256'],None
    raw=live._read(selector,4096)
    doc=live._parse(raw)
    if (set(doc)!=live._KEYS or doc['schema']!=live._SCHEMA or doc['baseline_sha256']!=baseline_sha256
            or type(doc['generation']) is not str or not live._GEN.fullmatch(doc['generation'])
            or type(doc['descriptor_sha256']) is not str or not caller._HEX.fullmatch(doc['descriptor_sha256'])
            or type(doc['index_pointer_sha256']) is not str or not caller._HEX.fullmatch(doc['index_pointer_sha256'])):
        raise ValueError()
    path=bp.parent.parent/doc['generation']/'shared-session-config.v2.json'
    candidate=caller._configuration(str(path),doc['descriptor_sha256'])
    if (candidate[4]['pointer_sha256']!=doc['index_pointer_sha256']
            or any(Path(candidate[0][key])!=path.parent/name for key,name in live._GENERATION_PATHS)):
        raise ValueError()
    live._checked_pair(baseline,candidate,manifest_sha256)
    if live._read(selector,4096)!=raw: raise ValueError()
    return doc['index_pointer_sha256'],raw


def run_once(*,enabled=False,baseline_path=None,baseline_sha256=None,manifest_sha256=None,
             genesis_tuple_bytes=None,genesis_tuple_sha256=None,receipt_bytes=None,receipt_sha256=None):
    if enabled is False: return {'status':'OFF'}
    entered=False; failure=None; result=None; interrupt_type=None
    try:
        if enabled is not True: raise ValueError()
        gt=_pinned(genesis_tuple_bytes,genesis_tuple_sha256,1024*1024)
        receipt=_pinned(receipt_bytes,receipt_sha256,65536)
        if (gt.get('schema')!='wd.q4-genesis-private-tuple.v1' or gt.get('publication')!='UNKNOWN'
                or set(receipt)!=composer._RECEIPT_V3 or receipt.get('status')!='caught_up_to_cutoff'
                or receipt.get('cutoff_monotonicity')!='COOPERATIVE_LEASE_PERSISTED_CRASH_UNKNOWN'
                or any(type(receipt.get(key)) is not str or not caller._HEX.fullmatch(receipt[key])
                       for key in ('pointer_sha256','runner_receipt_sha256','route_sha256','watermark_record_sha256'))):
            raise ValueError()
        route,initial_plan,anchor=(composer._tuple_part(gt,name) for name in ('route','plan','anchor'))
        root=Path(route['checkpoint_root']); private=Path(route['private_state_root'])
        if os.environ.get('WD_REBOOT_EXPECTED_MANIFEST_HASH','').lower()!=manifest_sha256: raise ValueError()
        baseline=caller._configuration(baseline_path,baseline_sha256)
        descriptor,_,_,fleet,binding,inputs=baseline
        if set(descriptor['lanes'])!=caller._LANES or len(descriptor['lanes'])!=len(caller._LANES): raise ValueError()
        public,_,_=live._plan(descriptor,inputs)
        if (Path(public['root'])!=root or any(route[key]!=binding[key]
                for key in ('policy_sha256','roster_sha256','public_policy_sha256'))
                or route['plan_sha256']!=gt['plan']['sha256'] or route['anchor_sha256']!=gt['anchor']['sha256']
                or initial_plan.get('pins',{}).get('anchor_sha256')!=gt['anchor']['sha256']
                or anchor.get('cursor_start')!=route['cursor']): raise ValueError()
        config={'baseline_path':baseline_path,'baseline_sha256':baseline_sha256,'manifest_sha256':manifest_sha256}
        update._require_write_roots(baseline_path,descriptor['bundle_root'],root,private)
        policy=live._read(Path(public['policy_path']),65536,route['policy_sha256'])
        roster=live._read(Path(public['roster_path']),65536,route['roster_sha256'])
        tasks,_=capture.strict_pins(policy,route['policy_sha256'],roster,route['roster_sha256'])
        marker=private/'q4-continuation-adopt.pending.json'
        locks=[private/'q4-continuation-orchestrator.lock',private/'q4-live-update.lock',private/'q4-cutoff-owner.lock']
        with ExitStack() as stack:
            for lock in locks:
                checkpoint._secure(lock,missing_leaf=True)
                stack.enter_context(reader_slot(lock,timeout_seconds=0.2))
            if any(os.path.lexists(private/name) for name in ('q4-continuation-adopt.pending.json',
                    'q4-continuation-orchestrator.pending.json','q4-live-update.pending.json')):
                raise _Pending()
            view,prefix=checkpoint.read_verified_prefix_pointer(root,tasks,expected_pointer_sha256=receipt['pointer_sha256'],
                policy_bytes=policy,policy_sha256=route['policy_sha256'],roster_bytes=roster,
                roster_sha256=route['roster_sha256'],expected_public_policy_sha256=route['public_policy_sha256'])
            _,runner_receipt=recovery._find_runner_receipt(private,receipt['runner_receipt_sha256'])
            pointer_raw=checkpoint._read(root/checkpoint.POINTER,max_bytes=checkpoint._MAX_POINTER_BYTES)
            pointer=caller._json(pointer_raw)
            if (_sha(pointer_raw)!=receipt['pointer_sha256'] or runner_receipt.get('schema')!='wd.q4-status-result.v1'
                    or runner_receipt.get('status') not in ('PUBLISHED_HISTORY','EMPTY_ADVANCED')
                    or runner_receipt.get('pointer_sha256')!=receipt['pointer_sha256']
                    or runner_receipt.get('generation')!=view['generation']
                    or runner_receipt.get('prefix_sha256')!=prefix.sha256
                    or runner_receipt.get('watermark_sha256')!=view['watermark_sha256']): raise ValueError()
            composer._verify_v2(receipt,runner_receipt,route,private,root,view,pointer)
            composer._verify_v3(receipt,runner_receipt,private,root,receipt['pointer_sha256'],prefix)
            _source(route,gt['independent_source_pin'],prefix)
            prior,selector_raw=_prior_pointer(baseline_path,baseline_sha256,baseline,manifest_sha256)
            if prior!=pointer['previous_pointer_sha256']: raise ValueError()
            marker_raw=update._encode({'schema':'wd.q4-continuation-adopt-pending.v1',
                'receipt_sha256':receipt_sha256,'baseline_sha256':baseline_sha256,
                'watermark_record_sha256':receipt['watermark_record_sha256'],
                'pointer_sha256':receipt['pointer_sha256'],'prior_pointer_sha256':prior,
                'prior_selector_sha256':None if selector_raw is None else _sha(selector_raw)})
            entered=True
            marker_info=runner._create(marker,marker_raw)
            runner._verify_own(marker,marker_raw,marker_info)
            result=update._publish(config,baseline,root,public,receipt)
            composer._verify_v3(receipt,runner_receipt,private,root,receipt['pointer_sha256'],prefix)
            _source(route,gt['independent_source_pin'],prefix)
            selected=live.select(enabled=True,baseline_path=baseline_path,baseline_sha256=baseline_sha256,
                baseline=baseline,manifest_sha256=manifest_sha256,load_configuration=caller._configuration)
            if (selected['publication_sha256']!=result['publication_sha256']
                    or selected['configuration'][4]['pointer_sha256']!=receipt['pointer_sha256']): raise ValueError()
            runner._verify_own(marker,marker_raw,marker_info)
            os.unlink(marker)
    except (KeyboardInterrupt,SystemExit,GeneratorExit) as signal:
        interrupt_type=type(signal)
        failure='interrupt' if entered else 'interrupt_before'
    except (composer._WatermarkIncomplete,_Pending):
        failure='unknown'
    except Exception:
        failure='unknown' if entered else 'refused'
    except BaseException:
        failure='interrupt' if entered else 'refused'
    if failure=='interrupt': raise AdoptInterrupted('adoption_unknown') from None
    if failure=='interrupt_before': raise interrupt_type() from None
    if failure=='unknown': raise AdoptOutcomeUnknown('adoption_unknown') from None
    if failure=='refused': raise AdoptRefused('adoption_refused') from None
    return result
