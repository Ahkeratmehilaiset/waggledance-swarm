"""Explicit, default-off provisioning of fleet-derived cooperative guard directories.

The caller must obtain the fleet pin from an independently verified deployment
manifest. Checking a caller-supplied digest here proves consistency only. The
runtime, ``shared`` and ``.codex-audit`` must already exist; this module creates
only missing lane and ``q4-caller-state`` directories. Existing markers, files,
directories and ACLs are never modified or removed. An interruption or error
can leave newly created directories for inspection; there is no rollback.

READY means the five directory chains were observed and rebound, not that the
eventual lane identities have effective write/delete permission. Identity and
reparse checks do not prevent a hostile same-user directory swap between check
and mkdir; they are not a TOCTOU, ACL, authentication or durability guarantee.
This is a library only; it has no automatic invocation or deployment hook.
"""

import hashlib
import hmac
import json

from tools import wd_q4_guard_state_binding as binding


_LANES = ("codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5")


class ProvisionUnavailable(RuntimeError):
    """Fixed refusal without filesystem paths, fleet bytes or exception details."""


def _existing(path, parent_ids):
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    observed = binding._observe(path)
    if observed[1:] != parent_ids:
        raise ValueError()
    return observed


def provision_guard_state(*, fleet_bytes=None, expected_fleet_sha256=None, enabled=False):
    """Provision fixed lanes only from already externally anchored fleet bytes."""
    if enabled is False:
        return {"status": "OFF"}
    failed = False
    try:
        if (enabled is not True or type(fleet_bytes) is not bytes
                or not 0 < len(fleet_bytes) <= binding._MAX_FLEET
                or type(expected_fleet_sha256) is not str
                or binding._HEX.fullmatch(expected_fleet_sha256) is None
                or not hmac.compare_digest(hashlib.sha256(fleet_bytes).hexdigest(),
                                           expected_fleet_sha256.lower())):
            raise ValueError()
        fleet = json.loads(fleet_bytes.decode("utf-8", "strict"), object_pairs_hook=binding._unique,
                           parse_constant=binding._constant)
        if type(fleet) is not dict:
            raise ValueError()
        audit = binding._root(fleet.get("runtime_root")) / "shared" / ".codex-audit"
        audit_ids = binding._observe(audit)  # This ancestor must already exist.

        # Inspect all pre-existing entries before creating any new entries.
        before = {}
        for lane in _LANES:
            lane_dir = audit / lane
            lane_ids = _existing(lane_dir, audit_ids)
            state_ids = None if lane_ids is None else _existing(lane_dir / "q4-caller-state", lane_ids)
            before[lane] = (lane_ids, state_ids)

        for lane in _LANES:
            if binding._observe(audit) != audit_ids:
                raise ValueError()
            lane_dir = audit / lane
            lane_ids, state_ids = before[lane]
            if lane_ids is None:
                lane_dir.mkdir()
                lane_ids = _existing(lane_dir, audit_ids)
            elif _existing(lane_dir, audit_ids) != lane_ids:
                raise ValueError()
            if lane_ids is None:
                raise ValueError()
            state = lane_dir / "q4-caller-state"
            if state_ids is None:
                if binding._observe(lane_dir) != lane_ids:
                    raise ValueError()
                state.mkdir()
                state_ids = _existing(state, lane_ids)
            elif _existing(state, lane_ids) != state_ids:
                raise ValueError()
            if state_ids is None:
                raise ValueError()
            before[lane] = (lane_ids, state_ids)

        for lane in _LANES:
            if binding._observe(audit) != audit_ids:
                raise ValueError()
            lane_ids, state_ids = before[lane]
            lane_dir = audit / lane
            if (_existing(lane_dir, audit_ids) != lane_ids
                    or _existing(lane_dir / "q4-caller-state", lane_ids) != state_ids):
                raise ValueError()
            binding.bind_state_root(fleet_bytes=fleet_bytes, expected_fleet_sha256=expected_fleet_sha256,
                                    lane=lane).revalidate()
    except Exception:
        failed = True
    if failed:
        del fleet_bytes, expected_fleet_sha256
        raise ProvisionUnavailable("guard_state_provision_unavailable")
    return {"status": "READY"}
