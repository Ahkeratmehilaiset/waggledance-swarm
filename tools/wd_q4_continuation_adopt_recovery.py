"""Default-off evidence-based reconciliation of one interrupted adoption.

Never changes a selector, removes a generation, moves an existing directory,
or retries publication. A prior selector is recoverable only when the target
generation is absent; existing or incomplete targets remain UNKNOWN because
the adoption marker does not prove their creation or exclusive ownership.
Cooperative locks do not authenticate writers or prevent same-user TOCTOU.
"""
from contextlib import ExitStack
import os
from pathlib import Path

from sandbox.mind.step2 import prefix_private_state as private_state
from sandbox.mind.step2 import shared_capture_adapter as capture
from sandbox.mind.step2 import shared_index_checkpoint as checkpoint
from sandbox.mind.step2.reader_slot import reader_slot, ReaderBusy
from tools import wd_q4_continuation_adopt as adopt
from tools import wd_q4_cutoff_watermark as watermark
from tools import wd_q4_genesis_to_composer_cli as composer
from tools import wd_q4_lane_caller_cli as caller
from tools import wd_q4_live_snapshot_publish as live
from tools import wd_q4_live_update_once as update
from tools import wd_q4_live_update_recovery as runner_recovery
from tools import wd_q4_status_prefix_runner as runner


class RecoveryRefused(ValueError):
    """No reconciliation was attempted."""


class RecoveryUnknown(RuntimeError):
    """Evidence is incomplete or conflicted; leave all state untouched."""


class RecoveryInterrupted(BaseException):
    """Interrupted reconciliation does not establish an outcome."""


_MARKER_KEYS = frozenset({'schema', 'receipt_sha256', 'baseline_sha256', 'watermark_record_sha256',
                          'pointer_sha256', 'prior_pointer_sha256', 'prior_selector_sha256'})


def recover_once(*, enabled=False, baseline_path=None, baseline_sha256=None, manifest_sha256=None,
                 genesis_tuple_bytes=None, genesis_tuple_sha256=None, receipt_bytes=None, receipt_sha256=None):
    if enabled is False:
        return {'status': 'OFF'}
    marker_candidate = None
    try:
        if enabled is not True:
            raise ValueError()
        gt = adopt._pinned(genesis_tuple_bytes, genesis_tuple_sha256, 1024 * 1024)
        early_route = composer._tuple_part(gt, 'route')
        marker_candidate = Path(early_route['private_state_root']) / 'q4-continuation-adopt.pending.json'
        receipt = adopt._pinned(receipt_bytes, receipt_sha256, 65536)
        if (gt.get('schema') != 'wd.q4-genesis-private-tuple.v1' or gt.get('publication') != 'UNKNOWN'
                or set(receipt) != composer._RECEIPT_V3
                or receipt.get('status') not in ('caught_up_to_cutoff', 'partial_ready_prefix')
                or receipt.get('cutoff_monotonicity') != 'COOPERATIVE_LEASE_PERSISTED_CRASH_UNKNOWN'
                or any(type(receipt.get(key)) is not str or not caller._HEX.fullmatch(receipt[key])
                       for key in ('pointer_sha256', 'runner_receipt_sha256', 'route_sha256',
                                   'watermark_record_sha256'))):
            raise ValueError()
        route, initial_plan, anchor = (composer._tuple_part(gt, name) for name in ('route', 'plan', 'anchor'))
        root, private = Path(route['checkpoint_root']), Path(route['private_state_root'])
        if os.environ.get('WD_REBOOT_EXPECTED_MANIFEST_HASH', '').lower() != manifest_sha256:
            raise ValueError()
        baseline = caller._configuration(baseline_path, baseline_sha256)
        descriptor, _, _, _, binding, inputs = baseline
        if set(descriptor['lanes']) != caller._LANES or len(descriptor['lanes']) != len(caller._LANES):
            raise ValueError()
        public, _, _ = live._plan(descriptor, inputs)
        if (Path(public['root']) != root or any(route[key] != binding[key] for key in
                ('policy_sha256', 'roster_sha256', 'public_policy_sha256'))
                or route['plan_sha256'] != gt['plan']['sha256'] or route['anchor_sha256'] != gt['anchor']['sha256']
                or initial_plan.get('pins', {}).get('anchor_sha256') != gt['anchor']['sha256']
                or anchor.get('cursor_start') != route['cursor']):
            raise ValueError()
        update._require_write_roots(baseline_path, descriptor['bundle_root'], root, private)
        policy = live._read(Path(public['policy_path']), 65536, route['policy_sha256'])
        roster = live._read(Path(public['roster_path']), 65536, route['roster_sha256'])
        tasks, _ = capture.strict_pins(policy, route['policy_sha256'], roster, route['roster_sha256'])
        marker = private / 'q4-continuation-adopt.pending.json'
        locks = [private / 'q4-continuation-orchestrator.lock', private / 'q4-live-update.lock',
                 private / 'q4-cutoff-owner.lock']
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        raise RecoveryInterrupted('recovery_interrupted') from None
    except Exception:
        if marker_candidate is not None and os.path.lexists(marker_candidate):
            raise RecoveryUnknown('recovery_unknown') from None
        raise RecoveryRefused('recovery_refused') from None
    try:
        with ExitStack() as stack:
            for lock in locks:
                checkpoint._secure(lock, missing_leaf=True)
                stack.enter_context(reader_slot(lock, timeout_seconds=0.2))
            # Until a verified call-bound Job-child capability exists, no direct
            # caller (including the Job child) may bypass this quarantine.
            with private_state.locked_state(private, root, enabled=True):
                with private_state._checkpoint_lock(root, 0.2):
                    if os.path.lexists(private / 'q4-adopt-recovery-job.pending.json'):
                        raise RecoveryUnknown('recovery_unknown')
            if not os.path.lexists(marker):
                return {'status': 'NOT_NEEDED'}
            marker_info = marker.lstat()
            marker_raw = checkpoint._read(checkpoint._secure(marker), max_bytes=65536)
            runner._verify_own(marker, marker_raw, marker_info)
            pending = caller._json(marker_raw)
            if (marker_raw != update._encode(pending) or set(pending) != _MARKER_KEYS
                    or pending['schema'] != 'wd.q4-continuation-adopt-pending.v1'
                    or pending['receipt_sha256'] != receipt_sha256 or pending['baseline_sha256'] != baseline_sha256
                    or pending['watermark_record_sha256'] != receipt['watermark_record_sha256']
                    or pending['pointer_sha256'] != receipt['pointer_sha256']
                    or type(pending['prior_pointer_sha256']) is not str
                    or not caller._HEX.fullmatch(pending['prior_pointer_sha256'])
                    or (pending['prior_selector_sha256'] is not None and
                        (type(pending['prior_selector_sha256']) is not str
                         or not caller._HEX.fullmatch(pending['prior_selector_sha256'])))):
                raise ValueError()
            view, prefix = checkpoint.read_verified_prefix_pointer(root, tasks,
                expected_pointer_sha256=receipt['pointer_sha256'], policy_bytes=policy,
                policy_sha256=route['policy_sha256'], roster_bytes=roster, roster_sha256=route['roster_sha256'],
                expected_public_policy_sha256=route['public_policy_sha256'])
            _, runner_receipt = runner_recovery._find_runner_receipt(private, receipt['runner_receipt_sha256'])
            pointer_raw = checkpoint._read(root / checkpoint.POINTER, max_bytes=checkpoint._MAX_POINTER_BYTES)
            pointer = caller._json(pointer_raw)
            if (adopt._sha(pointer_raw) != receipt['pointer_sha256']
                    or pointer.get('previous_pointer_sha256') != pending['prior_pointer_sha256']
                    or runner_receipt.get('schema') != 'wd.q4-status-result.v1'
                    or runner_receipt.get('status') not in ('PUBLISHED_HISTORY', 'EMPTY_ADVANCED')
                    or runner_receipt.get('pointer_sha256') != receipt['pointer_sha256']
                    or runner_receipt.get('generation') != view['generation']
                    or runner_receipt.get('prefix_sha256') != prefix.sha256
                    or runner_receipt.get('watermark_sha256') != view['watermark_sha256']):
                raise ValueError()
            if receipt['status'] == 'partial_ready_prefix':
                adopt._verify_empty_partial(receipt, runner_receipt, route, private, root, view, prefix, pointer)
            else:
                composer._verify_v2(receipt, runner_receipt, route, private, root, view, pointer)
            composer._verify_v3(receipt, runner_receipt, private, root, receipt['pointer_sha256'], prefix)
            adopt._source(route, gt['independent_source_pin'], prefix)
            selector = Path(baseline_path).parent.parent / 'q4-live-current.json'
            selector_raw = live._read(selector, 4096) if os.path.lexists(selector) else None
            selector_pin = None if selector_raw is None else adopt._sha(selector_raw)
            generation = Path(baseline_path).parent.parent / receipt['pointer_sha256']
            if selector_pin == pending['prior_selector_sha256']:
                prior, prior_raw = adopt._prior_pointer(baseline_path, baseline_sha256, baseline, manifest_sha256)
                if (prior != pending['prior_pointer_sha256'] or prior_raw != selector_raw
                        or os.path.lexists(generation)):
                    raise ValueError()
                outcome = 'NOT_ADOPTED'
            else:
                if selector_raw is None:
                    raise ValueError()
                doc = live._parse(selector_raw)
                if (doc.get('generation') != receipt['pointer_sha256']
                        or doc.get('index_pointer_sha256') != receipt['pointer_sha256']):
                    raise ValueError()
                selected = live.select(enabled=True, baseline_path=baseline_path, baseline_sha256=baseline_sha256,
                    baseline=baseline, manifest_sha256=manifest_sha256, load_configuration=caller._configuration)
                if (selected['publication_sha256'] != selector_pin
                        or selected['configuration'][4]['pointer_sha256'] != receipt['pointer_sha256']):
                    raise ValueError()
                outcome = 'ADOPTED'
            # The source checks above may acquire their own short locks. Repeat
            # all decisive mutable bindings while holding the same private ->
            # checkpoint pair as the adoption admission and only unlink our
            # exact unchanged marker, never a generation or selector.
            with private_state.locked_state(private, root, enabled=True) as state:
                with private_state._checkpoint_lock(root, 0.2):
                    if (checkpoint._active_fence(root) is not None
                            or any(os.path.lexists(path) for path in (
                                private / 'q4-adopt-recovery-job.pending.json',
                                private / 'q4-cutoff.pending.json', private / private_state._PENDING,
                                private / runner._PENDING, root / runner._PENDING,
                                private / 'q4-continuation-orchestrator.pending.json',
                                private / 'q4-continuation-orchestrator-job.pending.json',
                                private / 'q4-live-update.pending.json'))
                            or checkpoint._current_pointer(root) != receipt['pointer_sha256']):
                        raise ValueError()
                    state.require(receipt['pointer_sha256'], prefix)
                    record = watermark._load_head(private, receipt['watermark_record_sha256'])
                    if (record is None or record['pointer_sha256'] != receipt['pointer_sha256']
                            or record['route_sha256'] != receipt['route_sha256']
                            or record['runner_receipt_sha256'] != receipt['runner_receipt_sha256']
                            or record['cutoff_utc'] != receipt['cutoff_utc']):
                        raise ValueError()
                    current = live._read(selector, 4096) if os.path.lexists(selector) else None
                    if current != selector_raw or (outcome == 'NOT_ADOPTED' and os.path.lexists(generation)):
                        raise ValueError()
                    if outcome == 'ADOPTED':
                        selected = live.select(enabled=True, baseline_path=baseline_path, baseline_sha256=baseline_sha256,
                            baseline=baseline, manifest_sha256=manifest_sha256, load_configuration=caller._configuration)
                        if (selected['publication_sha256'] != selector_pin
                                or selected['configuration'][4]['pointer_sha256'] != receipt['pointer_sha256']):
                            raise ValueError()
                    runner._verify_own(marker, marker_raw, marker_info)
                    os.unlink(marker)
            return {'status': outcome}
    except ReaderBusy:
        raise RecoveryRefused('recovery_busy') from None
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        raise RecoveryInterrupted('recovery_interrupted') from None
    except Exception:
        raise RecoveryUnknown('recovery_unknown') from None
