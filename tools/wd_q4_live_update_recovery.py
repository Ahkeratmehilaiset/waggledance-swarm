"""Explicit, default-off reconciliation of one completed fenced update.

Only a caller-pinned attempt and progress receipt can be reconciled. The shared
OS lock excludes cooperating live-update processes, not hostile same-user
writers. Never removes incomplete generation directories or auto-retries an
unknown write. Source authenticity, protected ACLs, freshness and native
process-tree termination remain outside this protocol.
"""
import argparse
from dataclasses import asdict
import os
from pathlib import Path
import re
import secrets
import sys

from sandbox.mind.step2 import prefix_private_state as private_state
from sandbox.mind.step2 import shared_index_checkpoint as checkpoint
from sandbox.mind.step2 import shared_capture_adapter as capture_adapter
from sandbox.mind.step2.reader_slot import reader_slot
from tools import wd_q4_lane_caller_cli as caller
from tools import wd_q4_live_snapshot_publish as live
from tools import wd_q4_live_update_once as update


class RecoveryRefused(ValueError):
    """Evidence insufficient before recovery writes."""


class RecoveryOutcomeUnknown(RuntimeError):
    """Evidence or recovery outcome is uncertain; do not automatically retry."""


class RecoveryInterrupted(BaseException):
    """Recovery interruption leaves its outcome uncertain."""


def _same_except(left, right, keys):
    return (type(left) is dict and type(right) is dict and set(left) == set(right)
            and all(left[key] == right[key] for key in set(left) - set(keys)))


def _verify_job_attestation(private, attempt, config_sha, intent_sha, marker_raw, receipt_sha, helper_sha):
    """Verify a host observation; same-user bytes do not authenticate the writer."""
    try:
        if any(type(x) is not str or not update._HEX.fullmatch(x)
               for x in (attempt, config_sha, intent_sha, receipt_sha, helper_sha)):
            raise ValueError()
        raw = live._read(private / ('q4-live-job-result-' + attempt + '.json'), 65536, receipt_sha)
        receipt = caller._json(raw)
        keys = {'schema', 'attempt', 'config_sha256', 'intent_sha256', 'marker_sha256',
                'reservation_sha256', 'job_helper_sha256', 'cleanup', 'child_outcome', 'exit_code'}
        if (set(receipt) != keys or receipt['schema'] != 'wd.q4-live-job-result.v1'
                or receipt['attempt'] != attempt or receipt['config_sha256'] != config_sha
                or receipt['intent_sha256'] != intent_sha or receipt['marker_sha256'] != update._sha(marker_raw)
                or receipt['job_helper_sha256'] != helper_sha or receipt['cleanup'] != 'confirmed'
                or receipt['child_outcome'] not in ('completed', 'timeout', 'refused')
                or (receipt['child_outcome'] == 'completed'
                    and (type(receipt['exit_code']) is not int or receipt['exit_code'] == 0))
                or (receipt['child_outcome'] != 'completed' and receipt['exit_code'] is not None)):
            raise ValueError()
        raw_reservation = live._read(private / ('q4-live-runner-reservation-' + attempt + '.json'),
                                     65536, receipt['reservation_sha256'])
        reservation = caller._json(raw_reservation)
        if (set(reservation) != {'schema', 'attempt', 'token', 'intent_sha256'}
                or reservation['schema'] != 'wd.q4-live-runner-reservation.v1'
                or reservation['attempt'] != attempt or reservation['intent_sha256'] != intent_sha
                or type(reservation['token']) is not str or not update._HEX.fullmatch(reservation['token'])):
            raise ValueError()
        return reservation
    except Exception:
        raise RecoveryOutcomeUnknown('recovery_outcome_unknown') from None


def _recovery_generation(attempt, current, nonce):
    return update._sha(('wd-q4-recovery:' + attempt + ':' + current + ':' + nonce).encode('ascii'))


def _find_runner_receipt(private, expected_pin):
    candidates = list(private.glob('q4-status-result-*.json'))
    if len(candidates) > 50000:
        raise ValueError()
    matches = []
    for path in candidates:
        if re.fullmatch(r'q4-status-result-[0-9a-f]{64}\.json', path.name) is None:
            raise ValueError()
        raw = live._read(path, 65536)
        if update._sha(raw) == expected_pin:
            matches.append((path.name, caller._json(raw)))
    if len(matches) != 1:
        raise ValueError()
    return matches[0]


def _recorded_recovery_generation(private, config_sha, progress_sha, attempt, current, generation):
    paths = list(private.glob('q4-live-recovery-intent-' + attempt + '-*.json'))
    if len(paths) > 128:
        raise ValueError()
    for path in paths:
        match = re.fullmatch(r'q4-live-recovery-intent-' + attempt + r'-([0-9a-f]{64})\.json', path.name)
        if match is None:
            raise ValueError()
        nonce = match.group(1)
        value = caller._json(live._read(path, 65536))
        if value == {'schema': 'wd.q4-live-recovery-intent.v1', 'attempt': attempt, 'nonce': nonce,
                     'config_sha256': config_sha, 'progress_sha256': progress_sha, 'pointer_sha256': current,
                     'generation': _recovery_generation(attempt, current, nonce)} and value['generation'] == generation:
            return True
    return False


def _prior_selector(config, baseline, previous, current, attempt, private, config_sha, progress_sha):
    path = Path(config['baseline_path']).parent.parent / 'q4-live-current.json'
    if not os.path.lexists(path):
        if baseline[4]['pointer_sha256'] != previous:
            raise ValueError()
        return None
    raw = live._read(path, 4096)
    item = caller._json(raw)
    if (set(item) != live._KEYS or item['schema'] != live._SCHEMA
            or item['baseline_sha256'] != config['baseline_sha256']
            or type(item['generation']) is not str or live._GEN.fullmatch(item['generation']) is None
            or type(item['descriptor_sha256']) is not str or not update._HEX.fullmatch(item['descriptor_sha256'])
            or item['index_pointer_sha256'] not in (previous, current)):
        raise ValueError()
    candidate_path = path.parent / item['generation'] / 'shared-session-config.v2.json'
    candidate = caller._configuration(str(candidate_path), item['descriptor_sha256'])
    live._checked_pair(baseline, candidate, config['manifest_sha256'], **update._selector_options(config))
    if candidate[4]['pointer_sha256'] != item['index_pointer_sha256']:
        raise ValueError()
    if item['index_pointer_sha256'] == current:
        if (item['generation'] != current and not _recorded_recovery_generation(
                private, config_sha, progress_sha, attempt, current, item['generation'])):
            raise ValueError()
        selected = live.select(enabled=True, baseline_path=config['baseline_path'],
            baseline_sha256=config['baseline_sha256'], baseline=baseline,
            manifest_sha256=config['manifest_sha256'], load_configuration=caller._configuration,
                        **update._selector_options(config))
        if selected['configuration'][2] != item['descriptor_sha256']:
            raise ValueError()
        return {'status': 'PUBLISHED', 'pointer_sha256': current,
                'descriptor_sha256': item['descriptor_sha256'], 'publication_sha256': update._sha(raw),
                'freshness': 'UNKNOWN', 'source_authenticity': 'UNKNOWN', 'publisher_authentication': 'UNKNOWN'}
    return None


def _recover_closed_job(*, private, root, config, baseline, descriptor, binding, inputs, public,
                        marker_path, marker_info, marker_raw, marker, route, plan, policy, roster, tasks,
                        attempt, config_sha, job_sha, target):
    """Recover only a completed, closed fence; a host receipt is not writer authentication.

    Partial publication or marker release remains UNKNOWN and is never auto-retried.
    """
    manifest = caller._json(update._read(str(Path(descriptor['bundle_root']) / 'deployment-manifest.json'),
                                         caller.MAX_MANIFEST, config['manifest_sha256']))
    helper_sha = caller._hex(manifest['files']['WdQ4ProcessJob.ps1'])
    update._read(str(Path(descriptor['bundle_root']) / 'WdQ4ProcessJob.ps1'), 1024 * 1024, helper_sha)
    reservation = _verify_job_attestation(private, attempt, config_sha, marker['intent_sha256'],
                                          marker_raw, job_sha, helper_sha)
    marker_bytes = update._encode({'schema': 'wd.q4-runner-reservation.v1',
                                   'attempt': attempt, 'token': reservation['token']})
    markers = (root / update.runner._PENDING, private / update.runner._PENDING)
    infos = tuple(None if update.runner._absent(path) else path.lstat() for path in markers)
    selected = _prior_selector(config, baseline, marker['previous_pointer_sha256'], target,
                               attempt, private, config_sha, job_sha)
    record_path = private / ('q4-live-job-reconciled-' + attempt + '.json')
    if any(info is None for info in infos):
        # An absent runner marker is tolerable only after the exact result was
        # durably recorded and the selector was independently revalidated.
        if selected is None:
            raise ValueError()
        expected_record = update._encode({'schema': 'wd.q4-live-job-reconciled.v1', 'attempt': attempt,
            'config_sha256': config_sha, 'job_receipt_sha256': job_sha,
            'intent_sha256': marker['intent_sha256'], 'pointer_sha256': target, 'result': selected})
        if live._read(record_path, 65536) != expected_record:
            raise ValueError()
    def verify(*, remove=False):
        if _verify_job_attestation(private, attempt, config_sha, marker['intent_sha256'],
                                   marker_raw, job_sha, helper_sha) != reservation:
            raise ValueError()
        for path, info in zip(markers, infos):
            if info is None:
                if not update.runner._absent(path):
                    raise ValueError()
            else:
                update.runner._verify_own(path, marker_bytes, info)
        update.runner._verify_own(marker_path, marker_raw, marker_info)
        with private_state.locked_state(private, root, enabled=True) as state:
            with private_state._checkpoint_lock(root, 0.2):
                if os.path.lexists(private / 'q4-adopt-recovery-job.pending.json'):
                    raise RecoveryOutcomeUnknown('recovery_outcome_unknown')
                if (checkpoint._active_fence(root) is not None
                        or os.path.lexists(checkpoint._fence_record(root, target, 'not_landed'))
                        or checkpoint._read(checkpoint._fence_record(root, target, 'landed'),
                                            max_bytes=checkpoint._MAX_FENCE_BYTES)
                           != checkpoint._fence_bytes(marker['previous_pointer_sha256'], target)):
                    raise ValueError()
                pointer, prefix = checkpoint.read_verified_prefix_pointer(root, tasks, expected_pointer_sha256=target,
                    policy_bytes=policy, policy_sha256=route['policy_sha256'], roster_bytes=roster,
                    roster_sha256=route['roster_sha256'], expected_public_policy_sha256=route['public_policy_sha256'])
                if pointer['generation'] != plan['generation']:
                    raise ValueError()
                state.require(target, prefix)
                if remove:
                    if (_prior_selector(config, baseline, marker['previous_pointer_sha256'], target,
                                        attempt, private, config_sha, job_sha) != selected
                            or live._read(record_path, 65536) != record):
                        raise ValueError()
                    for path, info in zip(markers, infos):
                        if info is None:
                            if not update.runner._absent(path):
                                raise ValueError()
                        else:
                            update.runner._verify_own(path, marker_bytes, info)
                            os.unlink(path)
                    update.runner._verify_own(marker_path, marker_raw, marker_info)
                    os.unlink(marker_path)
    verify()
    if selected is None:
        nonce = secrets.token_hex(32)
        generation = _recovery_generation(attempt, target, nonce)
        intent = update._encode({'schema': 'wd.q4-live-recovery-intent.v1', 'attempt': attempt,
            'nonce': nonce, 'config_sha256': config_sha, 'progress_sha256': job_sha,
            'pointer_sha256': target, 'generation': generation})
        intent_path = private / ('q4-live-recovery-intent-' + attempt + '-' + nonce + '.json')
        intent_info = update.runner._create(intent_path, intent)
        update.runner._verify_own(intent_path, intent, intent_info)
        selected = update._publish(config, baseline, root, public, {'pointer_sha256': target}, generation=generation,
                                   **update._selector_options(config))
    record = update._encode({'schema': 'wd.q4-live-job-reconciled.v1', 'attempt': attempt,
                             'config_sha256': config_sha, 'job_receipt_sha256': job_sha,
                             'intent_sha256': marker['intent_sha256'], 'pointer_sha256': target, 'result': selected})
    try:
        record_info = update.runner._create(record_path, record)
    except FileExistsError:
        record_info = record_path.lstat()
    update.runner._verify_own(record_path, record, record_info)
    verify(remove=True)
    return {**selected, 'recovery_receipt_sha256': update._sha(record), 'receipt_file': record_path.name}


def recover(*, enabled=False, config_path=None, expected_config_sha256=None,
            expected_attempt=None, expected_progress_sha256=None,
            expected_runner_receipt_sha256=None, expected_pointer_sha256=None, expected_job_receipt_sha256=None):
    if enabled is False:
        return {'status': 'OFF'}
    entered = False
    failure = None
    try:
        if (enabled is not True or any(type(item) is not str or not update._HEX.fullmatch(item)
                for item in (expected_config_sha256, expected_attempt))):
            raise ValueError()
        job_mode = expected_job_receipt_sha256 is not None
        if job_mode:
            if (expected_progress_sha256 is not None or expected_runner_receipt_sha256 is not None
                    or any(type(x) is not str or not update._HEX.fullmatch(x)
                           for x in (expected_job_receipt_sha256, expected_pointer_sha256))):
                raise RecoveryOutcomeUnknown('recovery_outcome_unknown')
            reconstruct = False
        elif expected_progress_sha256 is None:
            if any(type(item) is not str or not update._HEX.fullmatch(item)
                   for item in (expected_runner_receipt_sha256, expected_pointer_sha256)):
                raise ValueError()
            reconstruct = True
        elif (type(expected_progress_sha256) is not str or not update._HEX.fullmatch(expected_progress_sha256)
              or expected_runner_receipt_sha256 is not None or expected_pointer_sha256 is not None):
            raise ValueError()
        else:
            reconstruct = False
        config = update._configuration(update._read(config_path, 65536, expected_config_sha256))
        if os.environ.get('WD_REBOOT_EXPECTED_MANIFEST_HASH', '').lower() != config['manifest_sha256']:
            raise ValueError()
        baseline = caller._configuration(config['baseline_path'], config['baseline_sha256'])
        descriptor, _, _, _, binding, inputs = baseline
        template_route = caller._json(update._read(config['route_path'], 65536, config['route_sha256']))
        template_plan = caller._json(update._read(config['plan_path'], 65536, template_route['plan_sha256']))
        template_anchor = caller._json(update._read(config['anchor_path'], 16384, template_route['anchor_sha256']))
        policy = update._read(config['policy_path'], 65536, template_route['policy_sha256'])
        public_policy = update._read(config['public_policy_path'], 65536, template_route['public_policy_sha256'])
        public, _, _ = live._plan(descriptor, inputs)
        if (Path(public['root']) != Path(template_route['checkpoint_root'])
                or Path(public['policy_path']) != Path(config['policy_path'])
                or Path(public['public_policy_path']) != Path(config['public_policy_path'])
                or any(binding[key] != template_route[key] for key in ('policy_sha256', 'roster_sha256', 'public_policy_sha256'))):
            raise ValueError()
        roster = update._read(public['roster_path'], 65536, template_route['roster_sha256'])
        tasks, _ = capture_adapter.strict_pins(policy, template_route['policy_sha256'], roster, template_route['roster_sha256'])
        root, private = Path(template_route['checkpoint_root']), Path(template_route['private_state_root'])
        update._require_write_roots(config['baseline_path'], descriptor['bundle_root'], root, private)
        private_info = private.stat()
        lock_path = private / 'q4-live-update.lock'
        if not lock_path.is_file():
            raise ValueError()
        live.pointer_io._chain(lock_path)
        with reader_slot(lock_path, timeout_seconds=0.2):
            # The adoption recovery Job reserves its marker under the same
            # pair. No other recovery may proceed during that attempt.
            with private_state.locked_state(private, root, enabled=True):
                with private_state._checkpoint_lock(root, 0.2):
                    if os.path.lexists(private / 'q4-adopt-recovery-job.pending.json'):
                        raise RecoveryOutcomeUnknown('recovery_outcome_unknown')
            # Adoption serializes on this same lock. A retained marker means
            # its publication outcome is unresolved and must not be replaced.
            if os.path.lexists(private / 'q4-continuation-adopt.pending.json'):
                raise RecoveryOutcomeUnknown('recovery_outcome_unknown')
            marker_path = private / 'q4-live-update.pending.json'
            marker_info = marker_path.lstat()
            marker_raw = live._read(marker_path, 65536)
            marker = caller._json(marker_raw)
            if (set(marker) != {'schema', 'attempt', 'config_sha256', 'previous_pointer_sha256', 'intent_sha256'}
                    or marker['schema'] != 'wd.q4-live-update-pending.v1' or marker['attempt'] != expected_attempt
                    or marker['config_sha256'] != expected_config_sha256):
                raise ValueError()
            intent = caller._json(live._read(private / ('q4-live-update-intent-' + expected_attempt + '.json'),
                                               262144, marker['intent_sha256']))
            if (set(intent) != {'schema', 'attempt', 'config_sha256', 'previous_pointer_sha256', 'route', 'plan', 'anchor'}
                    or intent['schema'] != 'wd.q4-live-update-intent.v1' or intent['attempt'] != expected_attempt
                    or intent['config_sha256'] != expected_config_sha256
                    or intent['previous_pointer_sha256'] != marker['previous_pointer_sha256']):
                raise ValueError()
            route, plan, anchor = intent['route'], intent['plan'], intent['anchor']
            if (not _same_except(route, template_route, {'cursor', 'prefix_sha256', 'anchor_sha256', 'plan_sha256'})
                    or not _same_except(plan, template_plan, {'generation', 'expected_pointer_sha256', 'pins'})
                    or not _same_except(plan['pins'], template_plan['pins'], {'anchor_sha256'})
                    or not _same_except(anchor, template_anchor, {'cursor_start'})
                    or route['cursor'] != anchor['cursor_start']
                    or plan['expected_pointer_sha256'] != marker['previous_pointer_sha256']
                    or route['plan_sha256'] != update._sha(update._encode(plan))
                    or route['anchor_sha256'] != update._sha(update._encode(anchor))
                    or plan['pins']['anchor_sha256'] != route['anchor_sha256']):
                raise ValueError()
            if job_mode:
                entered = True  # all E2 uncertainty is reported UNKNOWN, including missing evidence
                if expected_pointer_sha256 == marker['previous_pointer_sha256']:
                    raise ValueError()
                return _recover_closed_job(private=private, root=root, config=config, baseline=baseline,
                    descriptor=descriptor, binding=binding, inputs=inputs, public=public,
                    marker_path=marker_path, marker_info=marker_info, marker_raw=marker_raw, marker=marker,
                    route=route, plan=plan, policy=policy, roster=roster, tasks=tasks, attempt=expected_attempt,
                    config_sha=expected_config_sha256, job_sha=expected_job_receipt_sha256, target=expected_pointer_sha256)
            progress_path = private / ('q4-live-update-progress-' + expected_attempt + '.json')
            if reconstruct:
                if os.path.lexists(progress_path):
                    raise ValueError()
                found_name, found_receipt = _find_runner_receipt(private, expected_runner_receipt_sha256)
                progress = {'schema': 'wd.q4-live-update-progress.v1', 'attempt': expected_attempt,
                    'marker_sha256': update._sha(marker_raw), 'config_sha256': expected_config_sha256,
                    'intent_sha256': marker['intent_sha256'], 'previous_pointer_sha256': marker['previous_pointer_sha256'],
                    'pointer_sha256': expected_pointer_sha256, 'runner_receipt_sha256': expected_runner_receipt_sha256,
                    'runner_receipt_file': found_name, 'route_sha256': update._sha(update._encode(route)),
                    'plan_sha256': update._sha(update._encode(plan)), 'status': found_receipt.get('status')}
                progress_raw = update._encode(progress)
                expected_progress_sha256 = update._sha(progress_raw)
            else:
                progress_raw = live._read(progress_path, 65536, expected_progress_sha256)
                progress = caller._json(progress_raw)
            if (set(progress) != {'schema', 'attempt', 'marker_sha256', 'config_sha256', 'intent_sha256',
                    'previous_pointer_sha256', 'pointer_sha256', 'runner_receipt_sha256', 'runner_receipt_file',
                    'route_sha256', 'plan_sha256', 'status'} or progress['schema'] != 'wd.q4-live-update-progress.v1'
                    or progress['attempt'] != expected_attempt or progress['marker_sha256'] != update._sha(marker_raw)
                    or progress['config_sha256'] != expected_config_sha256 or progress['intent_sha256'] != marker['intent_sha256']
                    or progress['previous_pointer_sha256'] != marker['previous_pointer_sha256']
                    or progress['route_sha256'] != update._sha(update._encode(route))
                    or progress['plan_sha256'] != update._sha(update._encode(plan))
                    or progress['status'] not in ('PUBLISHED_HISTORY', 'EMPTY_ADVANCED', 'EMPTY_NO_CHANGE')
                    or type(progress['pointer_sha256']) is not str or not update._HEX.fullmatch(progress['pointer_sha256'])
                    or ((progress['pointer_sha256'] == marker['previous_pointer_sha256'])
                        != (progress['status'] == 'EMPTY_NO_CHANGE'))
                    or type(progress['runner_receipt_file']) is not str
                    or re.fullmatch(r'q4-status-result-[0-9a-f]{64}\.json', progress['runner_receipt_file']) is None):
                raise ValueError()
            receipt = caller._json(live._read(private / progress['runner_receipt_file'], 65536,
                                               progress['runner_receipt_sha256']))
            if (receipt.get('schema') != 'wd.q4-status-result.v1'
                    or any(receipt.get(key) != progress[key] for key in
                           ('pointer_sha256', 'route_sha256', 'plan_sha256', 'status'))
                    or receipt.get('prior_pointer_sha256') != progress['previous_pointer_sha256']):
                raise ValueError()
            target, previous = progress['pointer_sha256'], progress['previous_pointer_sha256']
            with private_state.locked_state(private, root, enabled=True) as state:
                with private_state._checkpoint_lock(root, 0.2):
                    if os.path.lexists(private / 'q4-adopt-recovery-job.pending.json'):
                        raise RecoveryOutcomeUnknown('recovery_outcome_unknown')
                    if checkpoint._active_fence(root) is not None:
                        raise ValueError()
                    if progress['status'] != 'EMPTY_NO_CHANGE' and (
                            os.path.lexists(checkpoint._fence_record(root, target, 'not_landed'))
                            or checkpoint._read(checkpoint._fence_record(root, target, 'landed'),
                                               max_bytes=checkpoint._MAX_FENCE_BYTES)
                            != checkpoint._fence_bytes(previous, target)):
                        raise ValueError()
                    _, prefix = checkpoint.read_verified_prefix_pointer(root, tasks, expected_pointer_sha256=target,
                        policy_bytes=policy, policy_sha256=route['policy_sha256'], roster_bytes=roster,
                        roster_sha256=route['roster_sha256'], expected_public_policy_sha256=route['public_policy_sha256'])
                    state.require(target, prefix)
                    if progress['status'] == 'EMPTY_NO_CHANGE' and (
                            asdict(prefix.cursor) != route['cursor'] or prefix.sha256 != route['prefix_sha256']):
                        raise ValueError()
            if progress['status'] == 'EMPTY_NO_CHANGE':
                selector_path = Path(config['baseline_path']).parent.parent / 'q4-live-current.json'
                if os.path.lexists(selector_path):
                    current = live.select(enabled=True, baseline_path=config['baseline_path'],
                        baseline_sha256=config['baseline_sha256'], baseline=baseline,
                        manifest_sha256=config['manifest_sha256'], load_configuration=caller._configuration,
                        **update._selector_options(config))
                    if current['configuration'][4]['pointer_sha256'] != target:
                        raise ValueError()
                else:
                    caller._require_current_snapshot(descriptor, binding, inputs)
                    if binding['pointer_sha256'] != target:
                        raise ValueError()
                selected = {'status': 'NO_CHANGE', 'pointer_sha256': target, 'freshness': 'UNKNOWN',
                            'source_authenticity': 'UNKNOWN', 'publisher_authentication': 'UNKNOWN'}
            else:
                selected = _prior_selector(config, baseline, previous, target, expected_attempt, private,
                                           expected_config_sha256, expected_progress_sha256)
            if reconstruct:
                entered = True
                progress_info = update.runner._create(progress_path, progress_raw)
                update.runner._verify_own(progress_path, progress_raw, progress_info)
            if selected is None:
                entered = True
                recovery_nonce = secrets.token_hex(32)
                generation = _recovery_generation(expected_attempt, target, recovery_nonce)
                recovery_intent = update._encode({'schema': 'wd.q4-live-recovery-intent.v1',
                    'attempt': expected_attempt, 'nonce': recovery_nonce, 'config_sha256': expected_config_sha256,
                    'progress_sha256': expected_progress_sha256, 'pointer_sha256': target, 'generation': generation})
                recovery_intent_path = private / ('q4-live-recovery-intent-' + expected_attempt + '-' + recovery_nonce + '.json')
                intent_info = update.runner._create(recovery_intent_path, recovery_intent)
                update.runner._verify_own(recovery_intent_path, recovery_intent, intent_info)
                selected = update._publish(config, baseline, root, public, {'pointer_sha256': target}, generation=generation,
                                   **update._selector_options(config))
            recovery_receipt = update._encode({'schema': 'wd.q4-live-update-recovery-result.v1',
                'attempt': expected_attempt, 'config_sha256': expected_config_sha256,
                'progress_sha256': expected_progress_sha256, 'result': selected})
            path = private / ('q4-live-update-recovery-result-' + expected_attempt + '.json')
            entered = True
            try:
                receipt_info = update.runner._create(path, recovery_receipt)
            except FileExistsError:
                # A prior attempt can have finished this exact receipt before
                # stopping at the pending marker.  Never accept partial or
                # different bytes, links, or a changed file identity.
                receipt_info = path.lstat()
            update.runner._verify_own(path, recovery_receipt, receipt_info)
            # Re-establish fence, checkpoint, private cursor and selector
            # together before clearing the owned marker, including on reuse.
            with private_state.locked_state(private, root, enabled=True) as state:
                with private_state._checkpoint_lock(root, 0.2):
                    if os.path.lexists(private / 'q4-adopt-recovery-job.pending.json'):
                        raise RecoveryOutcomeUnknown('recovery_outcome_unknown')
                    if checkpoint._active_fence(root) is not None:
                        raise ValueError()
                    if progress['status'] != 'EMPTY_NO_CHANGE' and (
                            os.path.lexists(checkpoint._fence_record(root, target, 'not_landed'))
                            or checkpoint._read(checkpoint._fence_record(root, target, 'landed'),
                                               max_bytes=checkpoint._MAX_FENCE_BYTES)
                            != checkpoint._fence_bytes(previous, target)):
                        raise ValueError()
                    _, prefix = checkpoint.read_verified_prefix_pointer(root, tasks, expected_pointer_sha256=target,
                        policy_bytes=policy, policy_sha256=route['policy_sha256'], roster_bytes=roster,
                        roster_sha256=route['roster_sha256'], expected_public_policy_sha256=route['public_policy_sha256'])
                    state.require(target, prefix)
                    if progress['status'] == 'EMPTY_NO_CHANGE':
                        if asdict(prefix.cursor) != route['cursor'] or prefix.sha256 != route['prefix_sha256']:
                            raise ValueError()
                        selector_path = Path(config['baseline_path']).parent.parent / 'q4-live-current.json'
                        if os.path.lexists(selector_path):
                            check = live.select(enabled=True, baseline_path=config['baseline_path'],
                                baseline_sha256=config['baseline_sha256'], baseline=baseline,
                                manifest_sha256=config['manifest_sha256'], load_configuration=caller._configuration,
                        **update._selector_options(config))
                            if check['configuration'][4]['pointer_sha256'] != target:
                                raise ValueError()
                        else:
                            caller._require_current_snapshot(descriptor, binding, inputs)
                            if binding['pointer_sha256'] != target:
                                raise ValueError()
                    elif _prior_selector(config, baseline, previous, target, expected_attempt, private,
                                         expected_config_sha256, expected_progress_sha256) != selected:
                        raise ValueError()
                    if not os.path.samestat(private.stat(), private_info):
                        raise ValueError()
                    update.runner._verify_own(path, recovery_receipt, receipt_info)
                    update.runner._verify_own(marker_path, marker_raw, marker_info)
                    marker_path.unlink()
            return {**selected, 'recovery_receipt_sha256': update._sha(recovery_receipt), 'receipt_file': path.name}
    except RecoveryOutcomeUnknown:
        failure = 'unknown'
    except Exception:
        failure = 'unknown' if entered else 'refused'
    except BaseException:
        if not entered:
            raise
        failure = 'interrupt'
    config_path = expected_config_sha256 = expected_attempt = expected_progress_sha256 = None
    expected_runner_receipt_sha256 = expected_pointer_sha256 = None
    config = baseline = policy = public_policy = roster = intent = progress = receipt = marker_raw = None
    if failure == 'unknown':
        raise RecoveryOutcomeUnknown('recovery_outcome_unknown') from None
    if failure == 'interrupt':
        raise RecoveryInterrupted('recovery_outcome_unknown') from None
    raise RecoveryRefused('recovery_refused') from None


def main(argv=None):
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument('--recover', action='store_true')
    parser.add_argument('--config')
    parser.add_argument('--sha256')
    parser.add_argument('--attempt')
    parser.add_argument('--progress-sha256')
    parser.add_argument('--runner-receipt-sha256')
    parser.add_argument('--job-receipt-sha256')
    parser.add_argument('--pointer-sha256')
    supplied = list(sys.argv[1:] if argv is None else argv)
    try:
        args = parser.parse_args(supplied)
    except SystemExit as exc:
        return int(exc.code) if type(exc.code) is int else 2
    if not args.recover:
        print('{"status":"OFF"}')
        return 0
    pins = (args.sha256, args.attempt)
    evidence = (args.progress_sha256, args.runner_receipt_sha256, args.job_receipt_sha256)
    flags = ('--recover', '--config', '--sha256', '--attempt', '--progress-sha256',
             '--runner-receipt-sha256', '--job-receipt-sha256', '--pointer-sha256')
    valid = (type(args.config) is str and bool(args.config)
             and all(type(pin) is str and update._HEX.fullmatch(pin) for pin in pins)
             and all(sum(token == flag or token.startswith(flag + '=') for token in supplied) <= 1 for flag in flags)
             and sum(value is not None for value in evidence) == 1
             and all(value is None or update._HEX.fullmatch(value) for value in evidence)
             and ((args.progress_sha256 is not None and args.pointer_sha256 is None)
                  or (args.progress_sha256 is None and type(args.pointer_sha256) is str
                      and update._HEX.fullmatch(args.pointer_sha256))))
    if not valid:
        print('{"status":"REFUSED"}', file=sys.stderr)
        return 2
    try:
        result = recover(enabled=True, config_path=args.config, expected_config_sha256=args.sha256,
                         expected_attempt=args.attempt, expected_progress_sha256=args.progress_sha256,
                         expected_runner_receipt_sha256=args.runner_receipt_sha256,
                         expected_pointer_sha256=args.pointer_sha256,
                         expected_job_receipt_sha256=args.job_receipt_sha256)
    except RecoveryOutcomeUnknown:
        print('{"status":"UNKNOWN"}', file=sys.stderr)
        return 3
    except RecoveryInterrupted:
        print('{"status":"UNKNOWN"}', file=sys.stderr)
        return 130
    except Exception:
        print('{"status":"REFUSED"}', file=sys.stderr)
        return 2
    print(update._encode(result).decode('ascii'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
