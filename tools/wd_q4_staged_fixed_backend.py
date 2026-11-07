"""Opt-in fixed C2 input validation; no launcher or live-state discovery.

Caller must independently pin/authenticate the plan and trusted code. Hashes
prove byte consistency only. Path rechecks do not prevent same-user ABA races.
"""
import hashlib
import hmac
import json
from pathlib import Path


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


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
