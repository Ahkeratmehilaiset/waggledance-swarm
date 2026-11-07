"""Verify independently pinned frozen producer and reader metadata, default off.

No filesystem or network access. The caller must authenticate the bundle,
pointer, policy and producer/watermark receipt pins independently and bind the
bundle to an actual reader. Hashes and cooperative receipts are not signatures.
This reports an observed producer cutoff within the pinned prefix, not a live
read, source authentication, complete history, wall-clock freshness, crash
durability, or protection against same-user rollback or forged receipts.
The genesis may deliberately skip a prehistory prefix; this proof does not
establish whether any earlier events were indexed or accessible.
"""
import hashlib
import hmac
import re

from sandbox.mind.step2 import shared_capture_adapter as capture
from sandbox.mind.step2 import shared_index_checkpoint as checkpoint
from sandbox.mind.step2.shared_bridge_snapshot import _token
from waggledance.core.bridge_log_reader import BridgeCursor
from tools import wd_q4_genesis_to_composer_cli as composer
from tools import wd_q4_production_continuation as continuation
from tools import wd_q4_secure_transport as transport
from tools import wd_q4_status_prefix_runner as runner

_HEX = re.compile(r'[0-9a-f]{64}\Z')
_WATERMARK_KEYS = {'schema','previous_record_sha256','cutoff_utc','attempt',
                   'route_sha256','runner_receipt_sha256','pointer_sha256'}
_RUNNER_KEYS = {'schema','marker_token_sha256','marker_identities','root_identities','route_sha256',
                'plan_sha256','prior_pointer_sha256','pointer_sha256','generation','status',
                'watermark_sha256','prefix_sha256'}


def _pinned(raw, pin, limit):
    if (type(raw) is not bytes or not 0 < len(raw) <= limit or type(pin) is not str
            or _HEX.fullmatch(pin) is None or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), pin)):
        raise ValueError()


def observe(*, enabled=False, bundle_bytes=None, expected_bundle_sha256=None,
            expected_pointer_sha256=None, policy_sha256=None, roster_sha256=None,
            public_policy_sha256=None, receipt_bytes=None, expected_receipt_sha256=None,
            watermark_bytes=None, expected_watermark_sha256=None, runner_receipt_bytes=None,
            route_bytes=None):
    if enabled is False:
        return {'status': 'OFF'}
    try:
        pins = (expected_pointer_sha256, policy_sha256, roster_sha256, public_policy_sha256)
        if enabled is not True or any(type(p) is not str or _HEX.fullmatch(p) is None for p in pins):
            raise ValueError()
        _pinned(bundle_bytes, expected_bundle_sha256, transport._MAX_BUNDLE)
        _pinned(receipt_bytes, expected_receipt_sha256, 65536)
        _pinned(watermark_bytes, expected_watermark_sha256, 8192)
        result = runner._json(receipt_bytes, 65536)
        watermark = runner._json(watermark_bytes, 8192)
        if (set(result) != composer._RECEIPT_V3 or result['status'] not in ('caught_up_to_cutoff','partial_ready_prefix')
                or result['cutoff_monotonicity'] != 'COOPERATIVE_LEASE_PERSISTED_CRASH_UNKNOWN'
                or result['watermark_record_sha256'] != expected_watermark_sha256
                or result['pointer_sha256'] != expected_pointer_sha256
                or set(watermark) != _WATERMARK_KEYS or watermark['schema'] != 'wd.q4-cutoff-record.v1'
                or watermark['pointer_sha256'] != expected_pointer_sha256
                or watermark['route_sha256'] != result['route_sha256']
                or watermark['runner_receipt_sha256'] != result['runner_receipt_sha256']
                or watermark['cutoff_utc'] != result['cutoff_utc']
                or type(watermark['attempt']) is not str or _HEX.fullmatch(watermark['attempt']) is None
                or (watermark['previous_record_sha256'] is not None and
                    (type(watermark['previous_record_sha256']) is not str or
                     _HEX.fullmatch(watermark['previous_record_sha256']) is None))):
            raise ValueError()
        cutoff = runner._cutoff_datetime(result['cutoff_utc'])
        previous_cutoff = runner._cutoff_datetime(result['previous_cutoff_utc'])
        if cutoff < previous_cutoff:
            raise ValueError()
        _pinned(route_bytes, result['route_sha256'], 65536)
        _pinned(runner_receipt_bytes, result['runner_receipt_sha256'], 65536)
        route = runner._json(route_bytes, 65536)
        rr = runner._json(runner_receipt_bytes, 65536)
        if (frozenset(route) not in (runner._ROUTE_KEYS | {'cutoff_utc'},
                                     runner._ROUTE_KEYS | {'cutoff_utc','batch_rows'})
                or route['schema'] != 'wd.q4-status-prefix-route.v1' or route['cutoff_utc'] != result['cutoff_utc']
                or route['policy_sha256'] != policy_sha256 or route['roster_sha256'] != roster_sha256
                or route['public_policy_sha256'] != public_policy_sha256
                or set(rr) != _RUNNER_KEYS or rr['schema'] != 'wd.q4-status-result.v1'
                or (result['status'], rr['status']) not in (('caught_up_to_cutoff','PUBLISHED_HISTORY'),
                                                          ('partial_ready_prefix','EMPTY_ADVANCED'))
                or rr['pointer_sha256'] != expected_pointer_sha256
                or rr['route_sha256'] != result['route_sha256'] or rr['plan_sha256'] != route['plan_sha256']):
            raise ValueError()
        parts = transport._fixed_bundle_decode(bundle_bytes)
        _pinned(parts['pointer_bytes'], expected_pointer_sha256, checkpoint._MAX_POINTER_BYTES)
        for key, pin in (('policy_bytes',policy_sha256),('roster_bytes',roster_sha256),
                         ('public_policy_bytes',public_policy_sha256)):
            _pinned(parts[key], pin, 65536)
        tasks, roster = capture.strict_pins(parts['policy_bytes'],policy_sha256,parts['roster_bytes'],roster_sha256)
        capture.public_status_policy(parts['public_policy_bytes'],public_policy_sha256,tasks)
        pointer = checkpoint._parse_pointer_snapshot(parts['pointer_bytes'], expected_pointer_sha256=expected_pointer_sha256,
            policy_sha=policy_sha256, roster_sha=roster_sha256, prefix_required=True)
        checkpoint._bind_active_artifact_bytes(parts['active_artifact_bytes'],expected_hashes=pointer['artifacts'],enabled=True)
        checkpoint._validate_pointer_history(None,parts['pointer_bytes'],pointer,tasks,roster,policy_sha256,roster_sha256,
            policy_bytes=parts['policy_bytes'],roster_bytes=parts['roster_bytes'],
            expected_source_mode=checkpoint._requested_source_mode('public-v1',public_policy_sha256),
            prefix_required=True,snapshot_bytes=parts['history_bytes'])
        prior = pointer['previous_pointer_sha256']
        if (type(prior) is not str or _HEX.fullmatch(prior) is None or rr['prior_pointer_sha256'] != prior
                or rr['generation'] != pointer['generation']
                or rr['watermark_sha256'] != hashlib.sha256(_token(BridgeCursor(**pointer['watermark'])).encode('utf-8')).hexdigest()
                or rr['prefix_sha256'] != pointer['prefix_watermark_sha256']):
            raise ValueError()
        empty = rr['status'] == 'EMPTY_ADVANCED'
        if ((not empty and (pointer['empty_advance'] is not None or route['anchor_sha256'] != pointer['anchor_sha256']))
                or (empty and (type(pointer['empty_advance']) is not dict
                               or route['anchor_sha256'] != pointer['empty_advance']['anchor_sha256']))):
            raise ValueError()
        prior_raw = parts['history_bytes']['q4-pointer-' + prior + '.json']
        _pinned(prior_raw, prior, checkpoint._MAX_POINTER_BYTES)
        prior_pointer = checkpoint._parse(prior_raw)
        if (route['cursor'] != prior_pointer['watermark']
                or route['prefix_sha256'] != prior_pointer['prefix_watermark_sha256']):
            raise ValueError()
        prior_receipt = parts['history_bytes'][prior_pointer['generation'] + '/' + checkpoint.ARTIFACTS['receipt_sha256']]
        _pinned(prior_receipt, prior_pointer['artifacts']['receipt_sha256'], checkpoint._MAX)
        prior_coverage = checkpoint._parse(prior_receipt).get('source_coverage')
        if type(prior_coverage) is not dict or previous_cutoff < continuation._utc(prior_coverage['cutoff_utc']):
            raise ValueError()
        build = checkpoint._parse(parts['active_artifact_bytes'][checkpoint.ARTIFACTS['receipt_sha256']])
        coverage = build.get('source_coverage')
        if (type(coverage) is not dict or set(coverage) != {'kind','end_reason','cutoff_utc'}
                or coverage['kind'] != 'ready_caught_up' or coverage['end_reason'] != 'snapshot_end'
                or (continuation._utc(coverage['cutoff_utc']) > cutoff if empty
                    else continuation._utc(coverage['cutoff_utc']) != cutoff)):
            raise ValueError()
        if empty:
            return {'status':'ADVANCED_WITHOUT_CUTOFF',
                    'build_cutoff_utc':continuation._utc(coverage['cutoff_utc']).isoformat().replace('+00:00','Z'),
                    'freshness':'UNKNOWN','source_authentication':'UNKNOWN','history_coverage':'UNKNOWN'}
        return {'status':'CUTOFF_WITHIN_PINNED_PREFIX','cutoff_utc':cutoff.isoformat().replace('+00:00','Z'),
                'freshness':'UNKNOWN','source_authentication':'UNKNOWN','history_coverage':'UNKNOWN'}
    except Exception:
        return {'status':'UNKNOWN'}
