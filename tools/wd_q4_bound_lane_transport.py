"""Default-off synthetic-capable lane ABI to pinned fixed-peer route.

The wrapper is verified as the independently pinned lane ABI artifact, but is
NOT executed by this transport; semantic equivalence with the wrapper is not
established. The caller must independently authenticate both plan pins and the
bundle pins. This does not authenticate any lane or source, establish freshness,
or protect against same-process monkeypatching and same-user TOCTOU. No install
or activation occurs. Timeout and cleanup-unknown errors must be preserved by
each caller; the runtime's full cleanup remains the caller's responsibility.
"""

import hashlib
import hmac

from sandbox.mind.step2 import shared_index_pointer as checkpoint
from tools import wd_q4_fixed_result_adapter as adapter
from tools import wd_q4_lane_link as lane
from tools import wd_q4_secure_transport as fixed
from tools import wd_q4_guard_completion as guard_completion


class BoundLaneTransportRefused(ValueError):
    """A fixed failure without paths, query, or returned content."""


def make_bound_transport(*, bundle_root, expected_commit, expected_manifest_sha256, expected_wrapper_sha256,
                         public_plan_path, expected_public_plan_sha256, executable, expected_interpreter_sha256,
                         interpreter_max_bytes, fixed_plan_bytes, expected_fixed_plan_sha256, fixed_bundle_bytes,
                         runtime_manifest_bytes, cwd, timeout_seconds, max_output_bytes, enabled=False,
                         _owner_guard=None, _owner_context=None):
    """Create exact-wrapper ABI callable only after checking both pinned plans."""
    accepted = False
    plan = parts = pointer = fixed_pins = root = expected_wrapper = path = public_bytes = public_pins = None
    expected_arguments = None
    try:
        if enabled is not True:
            raise ValueError()
        owner_options = {}
        if _owner_guard is not None or _owner_context is not None:
            if (type(_owner_guard) is not guard_completion._Guard
                    or _owner_guard._accept_owner_context(_owner_context) is not _owner_context):
                raise ValueError()
            owner_options = {"_owner_guard": _owner_guard, "_owner_context": _owner_context}
        if (type(fixed_plan_bytes) is not bytes or type(expected_fixed_plan_sha256) is not str
                or lane._HEX64.fullmatch(expected_fixed_plan_sha256) is None
                or not hmac.compare_digest(hashlib.sha256(fixed_plan_bytes).hexdigest(), expected_fixed_plan_sha256)):
            raise ValueError()
        plan = fixed._fixed_plan(fixed_plan_bytes)
        if (plan["backend"] != "public-snapshot-v1" or type(fixed_bundle_bytes) is not bytes
                or len(fixed_bundle_bytes) != plan["bundle_bytes"]
                or not hmac.compare_digest(hashlib.sha256(fixed_bundle_bytes).hexdigest(), plan["bundle_sha256"])):
            raise ValueError()
        parts = fixed._fixed_bundle_decode(fixed_bundle_bytes)
        for part, pin in (("policy_bytes", "policy_sha256"), ("roster_bytes", "roster_sha256"),
                          ("public_policy_bytes", "expected_public_policy_sha256")):
            if not hmac.compare_digest(hashlib.sha256(parts[part]).hexdigest(), plan[pin]):
                raise ValueError()
        pointer = checkpoint._parse_pointer_snapshot(parts["pointer_bytes"],
            expected_pointer_sha256=plan["expected_pointer_sha256"], policy_sha=plan["policy_sha256"],
            roster_sha=plan["roster_sha256"], prefix_required=True)
        if owner_options and not hmac.compare_digest(_owner_guard._binding[3], pointer["artifacts"]["descriptor_sha256"]):
            raise ValueError()
        if not hmac.compare_digest(pointer["anchor_sha256"], plan["expected_source_sha256"]):
            raise ValueError()
        fixed_pins = {"pointer_sha256": plan["expected_pointer_sha256"],
                      "source_anchor_sha256": plan["expected_source_sha256"],
                      "encoder_sha256": plan["expected_encoder_sha256"], "policy_sha256": plan["policy_sha256"],
                      "roster_sha256": plan["roster_sha256"],
                      "public_policy_sha256": plan["expected_public_policy_sha256"],
                      "descriptor_sha256": pointer["artifacts"]["descriptor_sha256"]}
        root, expected_wrapper = lane._binding(bundle_root, expected_commit, expected_manifest_sha256,
                                               expected_wrapper_sha256)
        path, public_bytes, public_pins = lane._public_plan(public_plan_path, expected_public_plan_sha256)
        if set(fixed_pins) != set(public_pins) or any(not hmac.compare_digest(fixed_pins[k], public_pins[k])
                                                     for k in fixed_pins):
            raise ValueError()
        expected_arguments = ("-BundleRoot", root, "-ExpectedBundleCommit", expected_commit,
            "-ExpectedManifestSha256", expected_manifest_sha256, "-Public", "-PublicPlanPath", path,
            "-ExpectedPublicPlanSha256", expected_public_plan_sha256)
        accepted = True
    except Exception:
        pass
    if not accepted:
        del bundle_root, expected_commit, expected_manifest_sha256, expected_wrapper_sha256
        del public_plan_path, expected_public_plan_sha256, executable, expected_interpreter_sha256
        del interpreter_max_bytes, fixed_plan_bytes, expected_fixed_plan_sha256, fixed_bundle_bytes
        del runtime_manifest_bytes, cwd, timeout_seconds, max_output_bytes, enabled
        del _owner_guard, _owner_context
        del plan, parts, pointer, fixed_pins, root, expected_wrapper, path, public_bytes, public_pins
        del expected_arguments
        raise BoundLaneTransportRefused("binding_refused") from None

    def validate_call(wrapper, arguments, stdin_bytes):
        try:
            if owner_options and _owner_guard._accept_owner_context(_owner_context) is not _owner_context:
                raise ValueError()
            if (type(wrapper) is not str or wrapper != expected_wrapper or type(arguments) is not tuple
                    or arguments != expected_arguments or type(stdin_bytes) is not bytes
                    or not 0 < len(stdin_bytes) <= lane._MAX_QUERY):
                raise ValueError()
            if (lane._binding(bundle_root, expected_commit, expected_manifest_sha256, expected_wrapper_sha256)
                    != (root, expected_wrapper)
                    or lane._public_plan(public_plan_path, expected_public_plan_sha256) != (path, public_bytes, public_pins)):
                raise ValueError()
        except Exception:
            return False
        return True

    def dispatch(stdin_bytes):
        return adapter.invoke_fixed_public(
            executable=executable, expected_interpreter_sha256=expected_interpreter_sha256,
            interpreter_max_bytes=interpreter_max_bytes, plan_bytes=fixed_plan_bytes,
            expected_plan_sha256=expected_fixed_plan_sha256, bundle_bytes=fixed_bundle_bytes,
            runtime_manifest_bytes=runtime_manifest_bytes, query_bytes=stdin_bytes, cwd=cwd,
            timeout_seconds=timeout_seconds, max_output_bytes=max_output_bytes, enabled=True, **owner_options)

    def transport(*, wrapper, arguments, stdin_bytes):
        if not validate_call(wrapper, arguments, stdin_bytes):
            del wrapper, arguments, stdin_bytes
            raise BoundLaneTransportRefused("binding_refused") from None
        return dispatch(stdin_bytes)

    return transport
