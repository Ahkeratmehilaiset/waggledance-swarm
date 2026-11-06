"""Default-off, read-only mapping from public projected refs to bridge event IDs.

This verifies one minute against one *caller-anchored complete snapshot*. The
caller independently supplies/authenticates the source hash, policy/roster pins,
and a coverage interval established outside this module. A matching hash is not
source authenticity, proof of an unrotated bridge, a clock guarantee, or proof
that late events cannot arrive. Results are scoped to the pinned snapshot and
must not be represented as global uniqueness or a review/consensus vote.

The existing bridge reader is paged with a verified prefix hash per page; the
last complete prefix must match the caller's pinned snapshot. It is not a time
index. Snapshots exceeding the bounded source, page or hashing budgets, partial
records, missing files, changed bytes, malformed rows, and unproven coverage
return UNKNOWN; a window with more than 2000 rows is refused. Only
public status events admitted by the pinned policy and roster are compared.
Outputs contain no message, payload, paths, projected text or policy contents.
"""
import datetime as dt
import hashlib
import hmac
import re

from sandbox.mind.step2 import shared_capture_adapter as capture
from sandbox.mind.step2 import shared_projection as projection
from tools.bridge_compact_view import event_id
from waggledance.core import bridge_log_reader as bridge

_HEX = re.compile(r'[0-9a-f]{64}\Z')
_MAX_WINDOW_ROWS = 2000
_MAX_SOURCE_BYTES = 512 * 1024 * 1024
_MAX_PAGE_BYTES = 16 * 1024 * 1024
_MAX_PAGE_ROWS = 100000
_MAX_PAGES = 128
_MAX_HASH_BUDGET = 6 * 1024 * 1024 * 1024


class VerifierRefused(ValueError):
    """Fixed error, carrying no source content or path."""


def _unknown():
    return {'status': 'UNKNOWN', 'canonical_event_refs': [], 'count': 0}


def _timestamp(text):
    if type(text) is not str:
        raise ValueError()
    value = projection.corpus_mod.parse_utc(text)
    if value is None:
        raise ValueError()
    return value


def verify(*, enabled=False, record=None, policy_bytes=None, policy_sha256=None,
           public_policy_bytes=None, public_policy_sha256=None, roster_bytes=None, roster_sha256=None,
           source_path=None, expected_snapshot_sha256=None, coverage_start_utc=None, coverage_end_utc=None):
    """Resolve public projection within an independently anchored complete source.

    coverage_start_utc/coverage_end_utc are external half-open coverage claims,
    not inferred or authenticated here. Return MATCH/AMBIGUOUS/NONE only within
    that supplied, hash-pinned snapshot; UNKNOWN means no conclusion. Bad policy,
    malformed input or a window above the row cap raises a fixed VerifierRefused.
    """
    if enabled is False:
        return {'status': 'OFF'}
    if enabled is not True:
        raise VerifierRefused('input_refused')
    bad_input = False
    try:
        tasks, roster = capture.strict_pins(policy_bytes, policy_sha256, roster_bytes, roster_sha256)
        public_tasks, public_types = capture.public_status_policy(public_policy_bytes, public_policy_sha256, tasks)
        if type(record) is not dict or type(record.get('provenance')) is not dict:
            raise ValueError()
        provenance = record['provenance']
        ref = provenance.get('event_ref')
        if (provenance.get('source_kind') != 'projected_bridge_metadata'
                or provenance.get('ref_kind') != 'sha256_of_projected_metadata'
                or provenance.get('canonical_event') is not False
                or type(ref) is not str or _HEX.fullmatch(ref) is None):
            raise ValueError()
        minute = _timestamp(record.get('time_utc'))
        if (minute.second != 0 or minute.microsecond != 0
                or projection.corpus_mod.utc_text(minute) != record['time_utc']):
            raise ValueError()
        end = minute + dt.timedelta(seconds=60)
        source_missing = (source_path is None or source_path == '' or expected_snapshot_sha256 is None
                          or expected_snapshot_sha256 == '')
        if not source_missing and (type(source_path) is not str or type(expected_snapshot_sha256) is not str
                                   or _HEX.fullmatch(expected_snapshot_sha256) is None):
            raise ValueError()
        try:
            covered = _timestamp(coverage_start_utc) <= minute and _timestamp(coverage_end_utc) >= end
        except (ValueError, OverflowError):
            covered = False
    except Exception:
        bad_input = True
    if bad_input:
        record = policy_bytes = public_policy_bytes = roster_bytes = source_path = None
        raise VerifierRefused('input_refused')
    if source_missing or not covered:
        return _unknown()
    overflow = False
    invalid = False
    refs = []
    event = read = probe = None
    try:
        # First bounded read discovers the file identity and a fixed snapshot
        # length, but its rows are discarded: the page is reread below under a
        # prefix check starting from SHA256(empty).
        probe = bridge.read_bridge_log(source_path, max_bytes=_MAX_PAGE_BYTES, max_rows=_MAX_PAGE_ROWS)
        if (probe.status not in (bridge.BridgeReadStatus.OK, bridge.BridgeReadStatus.IDLE)
                or probe.snapshot_length is None or probe.candidate_cursor is None
                or probe.snapshot_length > _MAX_SOURCE_BYTES):
            invalid = True
        else:
            fixed_length = probe.snapshot_length
            first = probe.candidate_cursor
            cursor = bridge.BridgeCursor(0, first.file_identity, first.generation)
            prefix_sha = hashlib.sha256(b'').hexdigest()
            probe = first = None
            window_count = 0
            pages = hash_budget = 0
            while True:
                pages += 1
                # Initial prefix hash, page hash extension and final prefix hash.
                hash_budget += 2 * min(fixed_length, cursor.offset + _MAX_PAGE_BYTES)
                if pages > _MAX_PAGES or hash_budget > _MAX_HASH_BUDGET:
                    invalid = True
                    break
                read = bridge.read_bridge_log(source_path, cursor=cursor, max_bytes=_MAX_PAGE_BYTES,
                    max_rows=_MAX_PAGE_ROWS, expected_prefix_sha256=prefix_sha, max_prefix_bytes=_MAX_SOURCE_BYTES)
                if (read.status not in (bridge.BridgeReadStatus.OK, bridge.BridgeReadStatus.IDLE)
                        or read.snapshot_length != fixed_length or read.candidate_cursor is None
                        or not isinstance(read, bridge.BridgePrefixReadResult) or read.candidate_prefix is None):
                    invalid = True
                    break
                for event in read.rows:
                    when = _timestamp(event.get('ts_utc'))
                    if not minute <= when < end:
                        continue
                    window_count += 1
                    if window_count > _MAX_WINDOW_ROWS:
                        overflow = True
                        break
                    capture._event_budget(event)
                    if not capture._public_status_event(event, public_tasks, public_types):
                        continue
                    agent = event.get('agent')
                    if type(agent) is not str or agent not in roster or event.get('agent_uuid') != roster[agent]:
                        invalid = True
                        break
                    if capture._exclude_rco_event(event) or capture._exclude_review_signal(event):
                        continue
                    fields, _ = projection.approved_fields(event, tasks, include_refs=False, roster=roster,
                                                            exclude_review_signal=True)
                    if projection.projected_ref(fields) == ref:
                        refs.append(event_id(event))
                if overflow or invalid:
                    break
                next_cursor = read.candidate_cursor
                next_sha = read.candidate_prefix.sha256
                if next_cursor.offset == fixed_length:
                    if not hmac.compare_digest(next_sha, expected_snapshot_sha256):
                        invalid = True
                    break
                if next_cursor.offset <= cursor.offset:
                    invalid = True
                    break
                cursor, prefix_sha = next_cursor, next_sha
    except Exception:
        invalid = True
    finally:
        event = read = probe = policy_bytes = public_policy_bytes = roster_bytes = record = None
    if overflow:
        raise VerifierRefused('window_too_large')
    if invalid:
        return _unknown()
    return {'status': 'NONE' if not refs else 'MATCH' if len(refs) == 1 else 'AMBIGUOUS',
            'canonical_event_refs': refs, 'count': len(refs)}
