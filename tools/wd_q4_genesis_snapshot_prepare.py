"""Explicitly stage policy inputs and capture a genesis checkpoint snapshot.

This is a component for a cooperating caller, not a CLI, install, selector or
activation route. All pins and the existing checkpoint root are caller supplied.
Returned snapshot bytes are private: never log them. A prepared snapshot alone
is neither a descriptor nor runtime evidence. After attempted writes failures
are UNKNOWN; never silently remove or overwrite an incomplete artifact. This
does not authenticate same-user writers or protect against hostile path races.
"""
import hashlib
import os
from pathlib import Path
import re

from sandbox.mind.step2 import shared_capture_adapter as capture
from sandbox.mind.step2 import shared_index_checkpoint as checkpoint
from tools import wd_q4_live_update_once as update

_HEX = re.compile(r'[0-9a-f]{64}\Z')


class PrepareRefused(ValueError):
    """Refused before this function attempted any write."""


class PrepareUnknown(RuntimeError):
    """A write was attempted; inspect state before a further attempt."""


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def prepare_snapshot(*, enabled=False, checkpoint_root=None, expected_pointer_sha256=None,
        policy_bytes=None, expected_policy_sha256=None, roster_bytes=None, expected_roster_sha256=None,
        public_policy_bytes=None, expected_public_policy_sha256=None):
    if enabled is False:
        return {'status': 'OFF'}
    attempted = False
    try:
        if enabled is not True:
            raise ValueError()
        items = [('policy', policy_bytes, expected_policy_sha256), ('roster', roster_bytes, expected_roster_sha256),
                 ('public-policy', public_policy_bytes, expected_public_policy_sha256)]
        if (type(expected_pointer_sha256) is not str or not _HEX.fullmatch(expected_pointer_sha256)
                or any(type(raw) is not bytes or not 0 < len(raw) <= 65536
                       or type(pin) is not str or not _HEX.fullmatch(pin) or _sha(raw) != pin
                       for _, raw, pin in items)):
            raise ValueError()
        root = checkpoint._secure(Path(checkpoint_root))
        if not root.is_dir():
            raise ValueError()
        tasks, _ = capture.strict_pins(policy_bytes, expected_policy_sha256, roster_bytes, expected_roster_sha256)
        checkpoint.read_verified_prefix_pointer(root, tasks, expected_pointer_sha256=expected_pointer_sha256,
            policy_bytes=policy_bytes, policy_sha256=expected_policy_sha256,
            roster_bytes=roster_bytes, roster_sha256=expected_roster_sha256,
            expected_public_policy_sha256=expected_public_policy_sha256)
        paths = [root / ('q4-snapshot-input-' + name + '-' + pin + '.json') for name, _, pin in items]
        for path, (_, raw, _) in zip(paths, items):
            if os.path.lexists(path) and checkpoint._read(path, max_bytes=65536) != raw:
                raise ValueError()
        for path, (_, raw, _) in zip(paths, items):
            if not os.path.lexists(path):
                attempted = True
                update._exclusive(path, raw)
            if checkpoint._read(path, max_bytes=65536) != raw:
                raise ValueError()
        pointer, snapshot = update._snapshot(root, expected_pointer_sha256, *paths)
        if update._sha(update._encode(pointer)) != expected_pointer_sha256:
            raise ValueError()
        return {'status': 'PREPARED', 'pointer_sha256': expected_pointer_sha256,
                'snapshot_bytes': snapshot, 'snapshot_sha256': _sha(snapshot),
                'policy_paths': tuple(str(path) for path in paths)}
    except Exception:
        if attempted:
            raise PrepareUnknown('snapshot_prepare_unknown') from None
        raise PrepareRefused('snapshot_prepare_refused') from None
