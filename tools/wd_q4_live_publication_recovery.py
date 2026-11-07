"""Explicit cooperative P1 publication reconciliation, never an automatic retry.

The published marker predates creation of its named temporary file and does not
contain that file's identity. Consequently an existing temporary file is not
deleted, even when its bytes match. Same-user writers can forge or race these
files; this protocol is not authentication or hostile-user isolation.
"""
import json
import os
from pathlib import Path
import re

from sandbox.mind.step2.reader_slot import reader_slot
from tools import wd_q4_live_snapshot_publish as live

_MARKER_KEYS = frozenset({'schema', 'attempt', 'baseline_sha256',
                          'previous_selector_sha256', 'publication_sha256', 'temporary_name'})
_ATTEMPT = re.compile(r'[0-9a-f]{32}\Z')


def recover_once(*, enabled=False, baseline_path=None, baseline_sha256=None, baseline=None,
                 candidate_path=None, candidate_sha256=None, candidate=None, manifest_sha256=None,
                 pending_sha256=None, previous_selector_sha256=None):
    """Validate one caller-pinned pending attempt; ambiguity always remains UNKNOWN."""
    if enabled is False:
        return {'status': 'OFF'}
    result = None
    try:
        if (enabled is not True or any(type(x) is not str or not live._HEX.fullmatch(x)
                for x in (baseline_sha256, candidate_sha256, pending_sha256))
                or type(manifest_sha256) is not str or not live._HEX.fullmatch(manifest_sha256.lower())
                or (previous_selector_sha256 is not None and
                    (type(previous_selector_sha256) is not str or not live._HEX.fullmatch(previous_selector_sha256)))
                or type(baseline_path) is not str or type(candidate_path) is not str):
            raise ValueError()
        manifest_sha256 = manifest_sha256.lower()
        bp = live.pointer_io._absolute(baseline_path)
        cp = live.pointer_io._absolute(candidate_path)
        if (bp.name != 'shared-session-config.v2.json' or cp.name != bp.name
                or bp.parent.parent != cp.parent.parent or not live._GEN.fullmatch(bp.parent.name)
                or not live._GEN.fullmatch(cp.parent.name)):
            raise ValueError()
        if (any(Path(candidate[0][key]) != cp.parent / name for key, name in live._GENERATION_PATHS)
                or any(Path(baseline[0][key]) != bp.parent / name for key, name in live._BASELINE_GENERATION_PATHS)):
            raise ValueError()
        root = bp.parent.parent
        pending = root / live._PUBLICATION_PENDING
        lock = root / live._PUBLICATION_LOCK
        target = root / live._POINTER
        live.pointer_io._chain(root)
        if os.path.lexists(lock):
            live.pointer_io._chain(lock)
        with reader_slot(lock, timeout_seconds=5):
            live.pointer_io._chain(root)
            live.pointer_io._chain(lock)
            live.pointer_io._chain(pending)
            marker_info = pending.stat()
            marker_raw = live._read(pending, 4096, pending_sha256)
            marker = live._parse(marker_raw)
            if (type(marker) is not dict or set(marker) != _MARKER_KEYS
                    or marker['schema'] != 'wd.q4-live-publication-pending.v1'
                    or type(marker['attempt']) is not str or not _ATTEMPT.fullmatch(marker['attempt'])
                    or marker['temporary_name'] != '.q4-live-current-' + marker['attempt'] + '.tmp'
                    or marker['baseline_sha256'] != baseline_sha256
                    or marker['previous_selector_sha256'] != previous_selector_sha256
                    or type(marker['publication_sha256']) is not str
                    or not live._HEX.fullmatch(marker['publication_sha256'])):
                raise ValueError()
            temp = root / marker['temporary_name']
            if os.path.lexists(temp):
                raise ValueError()  # There is no bound file identity in the v1 marker.
            if (baseline[2] != baseline_sha256 or candidate[2] != candidate_sha256
                    or live._read(bp, 65536, baseline_sha256) != baseline[1]
                    or live._read(cp, 65536, candidate_sha256) != candidate[1]):
                raise ValueError()
            manifest_path = Path(baseline[0]['bundle_root']) / 'deployment-manifest.json'
            live._read(manifest_path, 4 * 1024 * 1024, manifest_sha256)
            index_root, (pointer_path, pointer_limit) = live._checked_pair(
                baseline, candidate, manifest_sha256, p1_enabled=True)
            live._validate_roots(root, Path(baseline[0]['bundle_root']), Path(index_root))
            index_pin = candidate[0]['expected_binding']['pointer_sha256']
            live._read(pointer_path, pointer_limit, index_pin)
            raw = json.dumps({'schema': live._SCHEMA, 'baseline_sha256': baseline_sha256,
                              'generation': cp.parent.name, 'descriptor_sha256': candidate_sha256,
                              'index_pointer_sha256': index_pin}, sort_keys=True,
                             separators=(',', ':'), ensure_ascii=True).encode('ascii')
            if live._sha(raw) != marker['publication_sha256']:
                raise ValueError()
            present = os.path.lexists(target)
            current = live._read(target, 4096) if present else None
            new = current == raw
            previous = ((current is None and previous_selector_sha256 is None)
                        or (current is not None and previous_selector_sha256 is not None
                            and live._sha(current) == previous_selector_sha256))
            if new == previous:
                raise ValueError()
            if previous and present:
                old = live._parse(current)
                if (type(old) is not dict or set(old) != live._KEYS or old['schema'] != live._SCHEMA
                        or old['baseline_sha256'] != baseline_sha256
                        or type(old['generation']) is not str or not live._GEN.fullmatch(old['generation'])
                        or any(type(old[key]) is not str or not live._HEX.fullmatch(old[key])
                               for key in ('descriptor_sha256', 'index_pointer_sha256'))):
                    raise ValueError()
            # Revalidate all externally mutable bindings immediately before removing
            # the exact marker. The lock serializes cooperating publishers only.
            checked_root, checked_pointer = live._checked_pair(baseline, candidate, manifest_sha256, p1_enabled=True)
            if Path(checked_root) != Path(index_root) or checked_pointer != (pointer_path, pointer_limit):
                raise ValueError()
            if (live._read(bp, 65536, baseline_sha256) != baseline[1]
                    or live._read(cp, 65536, candidate_sha256) != candidate[1]):
                raise ValueError()
            live._read(manifest_path, 4 * 1024 * 1024, manifest_sha256)
            live._read(pointer_path, pointer_limit, index_pin)
            if (os.path.lexists(temp) or os.path.lexists(target) != present
                    or (present and live._read(target, 4096) != current)
                    or not os.path.samestat(marker_info, pending.stat())
                    or live._read(pending, 4096, pending_sha256) != marker_raw):
                raise ValueError()
            pending.unlink()
            result = {'status': 'PUBLISHED' if new else 'NOT_PUBLISHED',
                      'publication_sha256': marker['publication_sha256']}
    except Exception:
        pass
    if result is not None:
        return result
    raise live.LiveSnapshotOutcomeUnknown('live_publication_unknown') from None
