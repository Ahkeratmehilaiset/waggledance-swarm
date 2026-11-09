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
import math

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


def make_guarded_staged_transport(*, bundle_root, expected_commit, expected_manifest_sha256, expected_wrapper_sha256,
        public_plan_path, expected_public_plan_sha256, executable, expected_interpreter_sha256,
        interpreter_max_bytes, fixed_plan_bytes, expected_fixed_plan_sha256, fixed_bundle_bytes,
        runtime_manifest_bytes, cwd, timeout_seconds, max_output_bytes, enabled=False,
        _owner_guard=None, _owner_context=None):
    """Fixed staged-card dispatch only, requiring the exact existing guard context."""
    try:
        if (enabled is not True or type(_owner_guard) is not guard_completion._Guard
                or _owner_guard._accept_owner_context(_owner_context) is not _owner_context
                or type(fixed_plan_bytes) is not bytes
                or not hmac.compare_digest(hashlib.sha256(fixed_plan_bytes).hexdigest(), expected_fixed_plan_sha256)):
            raise ValueError()
        plan = fixed._fixed_plan(fixed_plan_bytes)
        if (plan['backend'] != 'staged-card-wrapper-v1' or type(fixed_bundle_bytes) is not bytes
                or len(fixed_bundle_bytes) != plan['bundle_bytes']
                or hashlib.sha256(fixed_bundle_bytes).hexdigest() != plan['bundle_sha256']
                or type(runtime_manifest_bytes) is not bytes
                or hashlib.sha256(runtime_manifest_bytes).hexdigest() != plan['runtime_manifest_sha256']
                or expected_interpreter_sha256 != plan['interpreter_sha256']):
            raise ValueError()
        root, wrapper = lane._binding(bundle_root,expected_commit,expected_manifest_sha256,expected_wrapper_sha256)
        path, public_bytes, pins = lane._public_plan(public_plan_path,expected_public_plan_sha256)
        if (root != plan['bundle_root'] or wrapper != plan['wrapper_path'] or path != plan['public_plan_path']
                or expected_commit != plan['bundle_commit']
                or expected_manifest_sha256 != plan['deployment_manifest_sha256']
                or expected_wrapper_sha256 != plan['wrapper_sha256']
                or expected_public_plan_sha256 != plan['public_plan_sha256']):
            raise ValueError()
        parts = fixed._fixed_card_bundle_decode(fixed_bundle_bytes)
        public = lane._json(public_bytes)
        if public['schema'] != 'wd.q4-public-wrapper-plan.v3':
            raise ValueError()
        expected = {'pointer_sha256':plan['expected_pointer_sha256'],
            'source_anchor_sha256':plan['expected_source_sha256'], 'encoder_sha256':plan['expected_encoder_sha256'],
            'policy_sha256':plan['policy_sha256'], 'roster_sha256':plan['roster_sha256'],
            'public_policy_sha256':plan['expected_public_policy_sha256'],
            'card_decision_sha256':plan['card_decision_sha256'], 'card_policy_sha256':plan['card_policy_sha256'],
            'descriptor_sha256':hashlib.sha256(parts['descriptor']).hexdigest()}
        if (pins != expected or any(hashlib.sha256(parts[key]).hexdigest() != plan[pin]
                for key,pin in (('pointer','expected_pointer_sha256'),('policy','policy_sha256'),
                    ('roster','roster_sha256'),('public_policy','expected_public_policy_sha256'),
                    ('card_decision','card_decision_sha256'),('card_policy','card_policy_sha256')))):
            raise ValueError()
        arguments = ('-BundleRoot',root,'-ExpectedBundleCommit',expected_commit,
                     '-ExpectedManifestSha256',expected_manifest_sha256,'-Public','-PublicPlanPath',path,
                     '-ExpectedPublicPlanSha256',expected_public_plan_sha256)
    except Exception:
        raise BoundLaneTransportRefused('binding_refused') from None

    def transport(*, wrapper, arguments: tuple, stdin_bytes: bytes):
        try:
            if (_owner_guard._accept_owner_context(_owner_context) is not _owner_context
                    or wrapper != lane._binding(bundle_root,expected_commit,expected_manifest_sha256,expected_wrapper_sha256)[1]
                    or wrapper != plan['wrapper_path'] or arguments != arguments_expected
                    or lane._public_plan(public_plan_path,expected_public_plan_sha256) != (path,public_bytes,pins)
                    or type(stdin_bytes) is not bytes or not 0 < len(stdin_bytes) <= lane._MAX_QUERY):
                raise ValueError()
        except Exception:
            raise BoundLaneTransportRefused('binding_refused') from None
        return fixed.run_fixed_peer(executable=executable,expected_interpreter_sha256=expected_interpreter_sha256,
            interpreter_max_bytes=interpreter_max_bytes,plan_bytes=fixed_plan_bytes,
            expected_plan_sha256=expected_fixed_plan_sha256,bundle_bytes=fixed_bundle_bytes,
            runtime_manifest_bytes=runtime_manifest_bytes,query_bytes=stdin_bytes,cwd=cwd,
            timeout_seconds=timeout_seconds,max_output_bytes=max_output_bytes,public_snapshot_enabled=True,
            _owner_guard=_owner_guard,_owner_context=_owner_context)

    arguments_expected = arguments
    return transport


def make_guarded_content_transport(*, bundle_root, expected_commit, expected_manifest_sha256, expected_wrapper_sha256,
        public_plan_path, expected_public_plan_sha256, executable, expected_interpreter_sha256,
        interpreter_max_bytes, fixed_plan_bytes, expected_fixed_plan_sha256, fixed_bundle_bytes,
        runtime_manifest_bytes, cwd, timeout_seconds, max_output_bytes, enabled=False,
        _owner_guard=None, _owner_context=None):
    """Dispatch only the seven-pin content wrapper through the existing guarded fixed peer."""
    try:
        if (enabled is not True or type(_owner_guard) is not guard_completion._Guard
                or _owner_guard._accept_owner_context(_owner_context) is not _owner_context
                or type(fixed_plan_bytes) is not bytes or type(expected_fixed_plan_sha256) is not str
                or not hmac.compare_digest(hashlib.sha256(fixed_plan_bytes).hexdigest(), expected_fixed_plan_sha256)):
            raise ValueError()
        plan = fixed._fixed_plan(fixed_plan_bytes)
        if (plan['backend'] != 'content-wrapper-v1' or type(fixed_bundle_bytes) is not bytes
                or len(fixed_bundle_bytes) != plan['bundle_bytes']
                or not hmac.compare_digest(hashlib.sha256(fixed_bundle_bytes).hexdigest(), plan['bundle_sha256'])
                or type(runtime_manifest_bytes) is not bytes
                or not hmac.compare_digest(hashlib.sha256(runtime_manifest_bytes).hexdigest(), plan['runtime_manifest_sha256'])
                or expected_interpreter_sha256 != plan['interpreter_sha256']):
            raise ValueError()
        root, wrapper = lane._binding(bundle_root, expected_commit, expected_manifest_sha256, expected_wrapper_sha256)
        path, public_bytes, pins = lane._public_plan(public_plan_path, expected_public_plan_sha256)
        public = lane._json(public_bytes)
        config = public.get('config') if type(public) is dict else None
        inner_timeout = config.get('timeout') if type(config) is dict else None
        if (type(inner_timeout) not in (int, float) or not math.isfinite(inner_timeout)
                or type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
                or not (0 < inner_timeout < timeout_seconds <= 1800)):
            raise ValueError()
        if (root != plan['bundle_root'] or wrapper != plan['wrapper_path'] or path != plan['public_plan_path']
                or expected_commit != plan['bundle_commit']
                or expected_manifest_sha256 != plan['deployment_manifest_sha256']
                or expected_wrapper_sha256 != plan['wrapper_sha256']
                or expected_public_plan_sha256 != plan['public_plan_sha256']
                or pins != {key: plan[key] for key in fixed._CONTENT_PINS}):
            raise ValueError()
        parts = fixed._fixed_content_bundle_decode(fixed_bundle_bytes)
        for key in ('policy', 'roster', 'content_decision', 'descriptor', 'memory', 'projection'):
            if not hmac.compare_digest(hashlib.sha256(parts[key]).hexdigest(), plan[key + '_sha256']):
                raise ValueError()
        arguments_expected = ('-BundleRoot', root, '-ExpectedBundleCommit', expected_commit,
                              '-ExpectedManifestSha256', expected_manifest_sha256, '-Public', '-PublicPlanPath', path,
                              '-ExpectedPublicPlanSha256', expected_public_plan_sha256)
    except Exception:
        raise BoundLaneTransportRefused('binding_refused') from None

    def transport(*, wrapper, arguments, stdin_bytes):
        try:
            if (_owner_guard._accept_owner_context(_owner_context) is not _owner_context
                    or wrapper != lane._binding(bundle_root, expected_commit, expected_manifest_sha256,
                                                 expected_wrapper_sha256)[1]
                    or wrapper != plan['wrapper_path'] or arguments != arguments_expected
                    or lane._public_plan(public_plan_path, expected_public_plan_sha256) != (path, public_bytes, pins)
                    or type(stdin_bytes) is not bytes or not 0 < len(stdin_bytes) <= lane._MAX_QUERY):
                raise ValueError()
        except Exception:
            raise BoundLaneTransportRefused('binding_refused') from None
        return fixed.run_fixed_peer(executable=executable, expected_interpreter_sha256=expected_interpreter_sha256,
            interpreter_max_bytes=interpreter_max_bytes, plan_bytes=fixed_plan_bytes,
            expected_plan_sha256=expected_fixed_plan_sha256, bundle_bytes=fixed_bundle_bytes,
            runtime_manifest_bytes=runtime_manifest_bytes, query_bytes=stdin_bytes, cwd=cwd,
            timeout_seconds=timeout_seconds, max_output_bytes=max_output_bytes, public_snapshot_enabled=True,
            _owner_guard=_owner_guard, _owner_context=_owner_context)
    return transport
