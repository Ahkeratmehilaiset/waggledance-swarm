"""Opt-in fixed C2 input validation; no launcher or live-state discovery.

Caller must independently pin/authenticate the plan and trusted code. Hashes
prove byte consistency only. Path rechecks do not prevent same-user ABA races.
"""
import hashlib
import hmac
import json
import os
from pathlib import Path


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _wrapper_command(plan, system_root):
    """The only admitted staged command; query bytes are supplied on stdin."""
    from tools import wd_q4_secure_transport as fixed
    for value in (system_root, plan['bundle_root'], plan['wrapper_path'], plan['public_plan_path']):
        fixed._staged_path(value)
    if os.path.normcase(os.path.normpath(plan['wrapper_path'])) != os.path.normcase(
            os.path.join(plan['bundle_root'], 'Invoke-WdQ4Query.ps1')):
        raise ValueError('staged_wrapper_refused')
    for key in ('deployment_manifest_sha256','public_plan_sha256'):
        fixed._hex64(plan[key])
    if type(plan['bundle_commit']) is not str or len(plan['bundle_commit']) != 40 or any(
            c not in '0123456789abcdef' for c in plan['bundle_commit']):
        raise ValueError('staged_wrapper_refused')
    host = os.path.join(system_root,'System32','WindowsPowerShell','v1.0','powershell.exe')
    return [host,'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',plan['wrapper_path'],
            '-BundleRoot',plan['bundle_root'],'-ExpectedBundleCommit',plan['bundle_commit'],
            '-ExpectedManifestSha256',plan['deployment_manifest_sha256'],'-Public',
            '-PublicPlanPath',plan['public_plan_path'],'-ExpectedPublicPlanSha256',plan['public_plan_sha256']]


def validate_card_input(plan, bundle_bytes, public_plan_bytes, *, enabled=False):
    """Validate the exact synthetic or caller-authorized C2 tuple without querying."""
    if enabled is False:
        return {'status': 'OFF'}
    try:
        if enabled is not True:
            raise ValueError()
        from tools import wd_q4_secure_transport as fixed
        from tools import wd_q4_lane_link as link
        from tools import wd_q4_public_pointer_resolver as resolver
        from tools.wd_q4_public_query import _encoder_digest
        from waggledance.core.bridge_log_reader import BridgeCursor
        if (type(bundle_bytes) is not bytes or len(bundle_bytes) != plan['bundle_bytes']
                or not hmac.compare_digest(_sha(bundle_bytes), plan['bundle_sha256'])
                or type(public_plan_bytes) is not bytes
                or not hmac.compare_digest(_sha(public_plan_bytes), plan['public_plan_sha256'])):
            raise ValueError()
        _, actual_plan, pins = link._public_plan(plan['public_plan_path'], plan['public_plan_sha256'])
        if actual_plan != public_plan_bytes:
            raise ValueError()
        public = link._json(public_plan_bytes)
        if public['schema'] != 'wd.q4-public-wrapper-plan.v3':
            raise ValueError()
        data = fixed._fixed_card_bundle_decode(bundle_bytes)
        expected = {'pointer':'expected_pointer_sha256', 'policy':'policy_sha256', 'roster':'roster_sha256',
                    'public_policy':'expected_public_policy_sha256', 'card_decision':'card_decision_sha256',
                    'card_policy':'card_policy_sha256'}
        for key, pin_key in expected.items():
            pin = plan[pin_key]
            if (_sha(data[key]) != pin or public[key+'_sha256'] != pin
                    or resolver._read_matching(Path(public[key+'_path']), pin, limit=16*1024*1024) != data[key]):
                raise ValueError()
        if (public['source_anchor_sha256'] != plan['expected_source_sha256']
                or public['encoder_sha256'] != plan['expected_encoder_sha256']):
            raise ValueError()
        snapshot = dict(root=public['root'], pointer_bytes=data['pointer'],
            expected_pointer_sha256=plan['expected_pointer_sha256'], policy_bytes=data['policy'],
            policy_sha256=plan['policy_sha256'], roster_bytes=data['roster'], roster_sha256=plan['roster_sha256'],
            card_decision_bytes=data['card_decision'], card_decision_sha256=plan['card_decision_sha256'],
            card_policy_bytes=data['card_policy'], source_path=public['source_path'],
            generation_path=public['generation_path'], end_cursor=BridgeCursor(**public['end_cursor']), enabled=True)
        binding = resolver.resolve_card_snapshot_pointer(**snapshot)
        if (binding.get('status') != 'VALIDATED_STAGED' or binding.get('completeness') != 'UNKNOWN'
                or binding['source_prefix']['sha256'] != plan['expected_source_sha256']
                or binding['descriptor_sha256'] != public['config']['descriptor_sha256']
                or str(binding['descriptor_path']) != public['config']['descriptor']
                or str(binding['memory_root']) != public['config']['memory']):
            raise ValueError()
        for key, path in (('memory', Path(binding['memory_root'])/'memory.json'),
                          ('projection', Path(binding['projection_path'])),
                          ('descriptor', Path(binding['descriptor_path']))):
            pin = binding[key+'_sha256']
            if resolver._read_matching(path, pin, limit=16*1024*1024) != data[key]:
                raise ValueError()
        descriptor = link._json(data['descriptor'])
        if (_encoder_digest(descriptor['encoder']) != plan['expected_encoder_sha256']
                or descriptor['encoder']['revision'] != public['config']['runtime_revision']):
            raise ValueError()
        return {'status':'VALIDATED_STAGED', 'completeness':'UNKNOWN'}
    except Exception:
        raise ValueError('staged_card_input_refused') from None


def validate_card_result(plan, bundle_bytes, public_plan_bytes, query_bytes, result_bytes, *, enabled=False):
    """Recheck C2 and independently resolve every result against caller-pinned bytes."""
    if enabled is False:
        return {'status': 'OFF'}
    try:
        if enabled is not True or validate_card_input(plan,bundle_bytes,public_plan_bytes,enabled=True) != {
                'status':'VALIDATED_STAGED','completeness':'UNKNOWN'}:
            raise ValueError()
        from tools import wd_q4_lane_link as link
        from tools import wd_q4_secure_transport as fixed
        from sandbox.mind.step2 import shared_capture_adapter as capture
        from sandbox.mind.step2 import shared_result_resolver as resolver
        query = link._json(query_bytes)
        if (type(query) is not dict or set(query) != {'situation','time'}
                or type(query['situation']) is not str or type(query['time']) is not str):
            raise ValueError()
        _, _, pins = link._public_plan(plan['public_plan_path'], plan['public_plan_sha256'])
        answer = link._public_result(result_bytes, 0, pins)
        data = fixed._fixed_card_bundle_decode(bundle_bytes)
        if (answer['status'] not in ('ANSWER','ABSTAIN','DEGRADED') or
                any(answer['binding'][key+'_sha256'] != _sha(data[key])
                    for key in ('memory','projection','descriptor'))):
            raise ValueError()
        tasks, _ = capture.strict_pins(data['policy'], plan['policy_sha256'], data['roster'], plan['roster_sha256'])
        claims = [{key:item[key] for key in ('ref_sha256','doc_id_sha256','ref_kind','canonical_event')}
                  for item in answer['results']]
        resolved = resolver.resolve_results(memory_manifest=data['memory'], expected_memory_sha256=_sha(data['memory']),
            projection_bytes=data['projection'], results=claims, query_utc=query['time'], approved_tasks=tasks,
            policy_bytes=data['policy'], policy_sha256=plan['policy_sha256'], roster_bytes=data['roster'],
            roster_sha256=plan['roster_sha256'], card_decision_bytes=data['card_decision'],
            card_decision_sha256=plan['card_decision_sha256'])
        resolved = [{key:item[key] for key in ('ref_sha256','doc_id_sha256','ref_kind','canonical_event','stored_text')}
                    for item in resolved]
        if (resolved != answer['results'] or validate_card_input(plan,bundle_bytes,public_plan_bytes,enabled=True) !=
                {'status':'VALIDATED_STAGED','completeness':'UNKNOWN'}):
            raise ValueError()
        return json.dumps(answer,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode('ascii')
    except Exception:
        raise ValueError('staged_card_result_refused') from None
