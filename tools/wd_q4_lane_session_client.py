"""Default-off composition of externally pinned Q4 session inputs.

No configuration or snapshot is discovered here. The caller independently
authenticates every pin and supplies a coherent fixed bundle. The declared lane
is roster membership, not session authentication. This module does not prove
publisher/source authenticity, freshness, semantic retrieval, or installation.
It uses the existing bound transport, which reads the caller-pinned wrapper and
public-plan paths; this facade itself adds no reader or root discovery. It
does not defend against same-process monkeypatches or same-user file races.
Exceptions are reconstructed without this function's input locals; calling
frames and objects retained by an attestation lease are outside that boundary.
Interrupts are re-raised after scrubbing: KeyboardInterrupt arguments are
dropped; SystemExit preserves None or an exact int, but replaces strings,
bools and all other objects with failure code 1 to avoid exposing payloads.
Asynchronous interruption during the cleanup itself is not eliminated.
"""

import hashlib
import hmac

from tools import wd_q4_bound_lane_transport as bound
from tools import wd_q4_lane_binding as lane_binding
from tools import wd_q4_lane_query as lane_query
from tools import wd_q4_roster_from_fleet as roster
from tools import wd_q4_secure_transport as fixed
from tools import wd_q4_guard_completion as guard_completion
from tools.wd_q4_fixed_parent_attestation import AttestationCleanupUnknown


class LaneSessionRefused(ValueError):
    """Fixed session refusal with no input bytes, paths, or query text."""


def query_session(*, lane, situation, time, fleet_bytes, expected_fleet_sha256, binding_bytes,
                  expected_binding_sha256, bundle_root, public_plan_path, executable, expected_interpreter_sha256,
                  interpreter_max_bytes, fixed_plan_bytes, expected_fixed_plan_sha256, fixed_bundle_bytes,
                  runtime_manifest_bytes, cwd, timeout_seconds, max_output_bytes, enabled=False,
                  _owner_guard=None, _owner_context=None):
    """Call the bound peer with one explicit snapshot, never a discovered one."""
    failure = reason = lease = prior = pins = plan = parts = transport = roster_bytes = roster_sha = None
    exit_code = None
    try:
        if enabled is not True:
            failure, reason = "refused", "disabled"
        else:
            owner_options = {}
            if _owner_guard is not None or _owner_context is not None:
                if (type(_owner_guard) is not guard_completion._Guard
                        or _owner_guard._accept_owner_context(_owner_context) is not _owner_context):
                    raise ValueError()
                owner_options = {"_owner_guard": _owner_guard, "_owner_context": _owner_context}
            pins = lane_binding.resolve_lane_binding(fleet_bytes, expected_fleet_sha256, binding_bytes,
                expected_binding_sha256, lane=lane, enabled=True)
            if owner_options and _owner_guard._binding[1] != lane:
                raise ValueError()
            if (type(fixed_plan_bytes) is not bytes or not 0 < len(fixed_plan_bytes) <= fixed._MAX_PLAN
                    or type(expected_fixed_plan_sha256) is not str
                    or len(expected_fixed_plan_sha256) != 64
                    or not hmac.compare_digest(hashlib.sha256(fixed_plan_bytes).hexdigest(), expected_fixed_plan_sha256)):
                raise ValueError()
            plan = fixed._fixed_plan(fixed_plan_bytes)
            content = plan['backend'] == 'content-wrapper-v1'
            staged = plan['backend'] == 'staged-card-wrapper-v1'
            if (plan["backend"] not in ("public-snapshot-v1", "staged-card-wrapper-v1", "content-wrapper-v1")
                    or ((staged or content) and not owner_options) or type(fixed_bundle_bytes) is not bytes
                    or len(fixed_bundle_bytes) != plan["bundle_bytes"]
                    or not hmac.compare_digest(hashlib.sha256(fixed_bundle_bytes).hexdigest(), plan["bundle_sha256"])
                    or type(runtime_manifest_bytes) is not bytes or len(runtime_manifest_bytes) > fixed._MAX_RUNTIME_MANIFEST
                    or not hmac.compare_digest(hashlib.sha256(runtime_manifest_bytes).hexdigest(),
                                               plan["runtime_manifest_sha256"])
                    or type(expected_interpreter_sha256) is not str
                    or not hmac.compare_digest(expected_interpreter_sha256, plan["interpreter_sha256"])):
                raise ValueError()
            parts = (fixed._fixed_content_bundle_decode(fixed_bundle_bytes) if content else
                     fixed._fixed_card_bundle_decode(fixed_bundle_bytes) if staged else
                     fixed._fixed_bundle_decode(fixed_bundle_bytes))
            roster_bytes, roster_sha = roster.build_roster_snapshot(fleet_bytes, expected_fleet_sha256, enabled=True)
            if (not hmac.compare_digest(roster_sha, pins["expected_binding"]["roster_sha256"])
                    or not hmac.compare_digest(roster_bytes, parts['roster'] if staged or content else parts["roster_bytes"])):
                raise ValueError()
            factory = (bound.make_guarded_content_transport if content else
                       bound.make_guarded_staged_transport if staged else bound.make_bound_transport)
            transport = factory(
                bundle_root=bundle_root, expected_commit=pins["expected_commit"],
                expected_manifest_sha256=pins["expected_manifest_sha256"],
                expected_wrapper_sha256=pins["expected_wrapper_sha256"], public_plan_path=public_plan_path,
                expected_public_plan_sha256=pins["expected_public_plan_sha256"], executable=executable,
                expected_interpreter_sha256=expected_interpreter_sha256, interpreter_max_bytes=interpreter_max_bytes,
                fixed_plan_bytes=fixed_plan_bytes, expected_fixed_plan_sha256=expected_fixed_plan_sha256,
                fixed_bundle_bytes=fixed_bundle_bytes, runtime_manifest_bytes=runtime_manifest_bytes, cwd=cwd,
                timeout_seconds=timeout_seconds, max_output_bytes=max_output_bytes, enabled=True, **owner_options)
            return lane_query.query_public(
                **pins, situation=situation, time=time, bundle_root=bundle_root, public_plan_path=public_plan_path,
                projection_bytes=parts['projection'] if staged or content else parts["active_artifact_bytes"]["projection.json"],
                policy_bytes=parts['policy'] if staged or content else parts["policy_bytes"],
                roster_bytes=parts['roster'] if staged or content else parts["roster_bytes"], transport=transport, enabled=True)
    except AttestationCleanupUnknown as exc:
        failure, lease = "attestation", exc.lease
    except fixed.TransportCleanupUnknown as exc:
        failure = "transport"
        allowed = {"timeout", "refused", "interrupt", "exit", "none", "other", "prior_unknown"}
        prior = exc.prior_outcome if type(exc.prior_outcome) is str and exc.prior_outcome in allowed else "other"
    except TimeoutError:
        failure = "timeout"
    except KeyboardInterrupt:
        failure = "interrupt"
    except SystemExit as exc:
        failure = "exit"
        exit_code = exc.code if exc.code is None or type(exc.code) is int else 1
    except Exception:
        failure, reason = "refused", "session_refused"
    del lane, situation, time, fleet_bytes, expected_fleet_sha256, binding_bytes, expected_binding_sha256
    del bundle_root, public_plan_path, executable, expected_interpreter_sha256, interpreter_max_bytes
    del fixed_plan_bytes, expected_fixed_plan_sha256, fixed_bundle_bytes, runtime_manifest_bytes
    del cwd, timeout_seconds, max_output_bytes, enabled, pins, plan, parts, transport, roster_bytes, roster_sha
    del _owner_guard, _owner_context
    if failure == "attestation":
        raise AttestationCleanupUnknown(lease)
    if failure == "transport":
        raise fixed.TransportCleanupUnknown(prior)
    if failure == "timeout":
        raise TimeoutError("secure_transport_timeout")
    if failure == "interrupt":
        raise KeyboardInterrupt()
    if failure == "exit":
        raise SystemExit(exit_code)
    raise LaneSessionRefused(reason)
