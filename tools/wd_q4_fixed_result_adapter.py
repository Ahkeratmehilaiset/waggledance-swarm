"""Default-off pinned fixed-peer to lane-public result route.

This module itself calls the fixed peer and converts only that call's result;
it accepts no externally supplied result or injected transport. The caller
must independently authenticate the plan digest. The descriptor digest comes
from the pinned pointer, not an independent caller pin. Source authentication
and freshness remain UNKNOWN: projection membership is not retrieval proof.
No lane identity is authenticated. Same-process monkeypatching of this module
or its dependencies is outside this boundary; the runtime is not isolated.
Private pointer fields are never returned. This is neither an installer nor a
running service and inherits all limitations of ``run_fixed_peer``.
"""

import hashlib
import hmac
import json

from tools import wd_q4_lane_link as lane_link
from tools import wd_q4_secure_transport as fixed
from tools import wd_q4_guard_completion as guard_completion
from tools.wd_q4_fixed_parent_attestation import AttestationCleanupUnknown


class FixedResultAdapterRefused(ValueError):
    """Fixed failure without source, result, query or path details."""


_RESULT_KEYS = frozenset({"schema", "status", "results", "source_authentication", "freshness", "runtime", "validation"})
_UNKNOWN = ("freshness", "publisher_authentication", "source_filter_attestation", "same_user_aba")


def invoke_fixed_public(*, executable, expected_interpreter_sha256, interpreter_max_bytes,
                        plan_bytes, expected_plan_sha256, bundle_bytes, runtime_manifest_bytes,
                        query_bytes, cwd, timeout_seconds, max_output_bytes, enabled=False,
                        _owner_guard=None, _owner_context=None):
    """Call fixed peer and return the v2 result; no detached result is accepted."""
    try:
        if enabled is not True:
            raise ValueError()
        owner_options = {}
        if _owner_guard is not None or _owner_context is not None:
            if (type(_owner_guard) is not guard_completion._Guard
                    or _owner_guard._accept_owner_context(_owner_context) is not _owner_context):
                raise ValueError()
            owner_options = {"_owner_guard": _owner_guard, "_owner_context": _owner_context}
        if (type(plan_bytes) is not bytes or type(expected_plan_sha256) is not str
                or lane_link._HEX64.fullmatch(expected_plan_sha256) is None
                or not hmac.compare_digest(hashlib.sha256(plan_bytes).hexdigest(), expected_plan_sha256)):
            raise ValueError()
        plan = fixed._fixed_plan(plan_bytes)
        if (plan["backend"] != "public-snapshot-v1" or type(bundle_bytes) is not bytes
                or len(bundle_bytes) != plan["bundle_bytes"]
                or not hmac.compare_digest(hashlib.sha256(bundle_bytes).hexdigest(), plan["bundle_sha256"])):
            raise ValueError()
        parts = fixed._fixed_bundle_decode(bundle_bytes)
        for part, pin in (("policy_bytes", "policy_sha256"), ("roster_bytes", "roster_sha256"),
                          ("public_policy_bytes", "expected_public_policy_sha256")):
            if not hmac.compare_digest(hashlib.sha256(parts[part]).hexdigest(), plan[pin]):
                raise ValueError()
        from sandbox.mind.step2 import shared_index_pointer as checkpoint
        pointer = checkpoint._parse_pointer_snapshot(parts["pointer_bytes"],
            expected_pointer_sha256=plan["expected_pointer_sha256"], policy_sha=plan["policy_sha256"],
            roster_sha=plan["roster_sha256"], prefix_required=True)
        if owner_options and not hmac.compare_digest(_owner_guard._binding[3], pointer["artifacts"]["descriptor_sha256"]):
            raise ValueError()
        if not hmac.compare_digest(pointer["anchor_sha256"], plan["expected_source_sha256"]):
            raise ValueError()
        try:
            result_bytes, code = fixed.run_fixed_peer(
                executable=executable, expected_interpreter_sha256=expected_interpreter_sha256,
                interpreter_max_bytes=interpreter_max_bytes, plan_bytes=plan_bytes,
                expected_plan_sha256=expected_plan_sha256, bundle_bytes=bundle_bytes,
                runtime_manifest_bytes=runtime_manifest_bytes, query_bytes=query_bytes, cwd=cwd,
                timeout_seconds=timeout_seconds, max_output_bytes=max_output_bytes, public_snapshot_enabled=True,
                **owner_options)
        except fixed._Refused:
            return fixed._canonical({"schema": "wd.q4-public-query.v2", "status": "REFUSED",
                                     "reason": "runtime_refused"}), 2
        if type(code) is not int or type(result_bytes) is not bytes:
            raise ValueError()
        if code == 2 and result_bytes == b"":
            return fixed._canonical({"schema": "wd.q4-public-query.v2", "status": "REFUSED",
                                     "reason": "runtime_refused"}), 2
        if code != 0 or not result_bytes or len(result_bytes) > fixed._MAX_OUTPUT:
            raise ValueError()
        result = json.loads(result_bytes.decode("ascii", "strict"), object_pairs_hook=fixed._unique_pairs,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if (type(result) is not dict or set(result) != _RESULT_KEYS or fixed._canonical(result) != result_bytes
                or result["schema"] != "wd.q4-fixed-public-result.v1"
                or result["source_authentication"] != "UNKNOWN" or result["freshness"] != "UNKNOWN"
                or result["runtime"] != fixed._PUBLIC_RUNTIME
                or result["validation"] != "PARENT_RERESOLVED_AGAINST_PINNED_PROJECTION"
                or result["status"] not in ("ANSWER", "ABSTAIN", "DEGRADED")):
            raise ValueError()
        items = fixed._fixed_closed_results(result["results"])
        if ((result["status"] == "ANSWER" and not items) or (result["status"] == "ABSTAIN" and items)
                or len({(item["ref_sha256"], item["doc_id_sha256"]) for item in items}) != len(items)
                or any(len(item["stored_text"].encode("utf-8", "strict")) > 320 for item in items)):
            raise ValueError()
        pins = {"pointer_sha256": plan["expected_pointer_sha256"],
                "source_anchor_sha256": plan["expected_source_sha256"], "encoder_sha256": plan["expected_encoder_sha256"],
                "policy_sha256": plan["policy_sha256"], "roster_sha256": plan["roster_sha256"],
                "public_policy_sha256": plan["expected_public_policy_sha256"],
                "descriptor_sha256": pointer["artifacts"]["descriptor_sha256"]}
        binding = {**pins, **{key: pointer["artifacts"][key] for key in
                              ("memory_sha256", "projection_sha256", "receipt_sha256")}}
        raw = fixed._canonical({"schema": "wd.q4-public-query.v2", "status": result["status"],
                                "binding": binding, "results": items, **{key: "UNKNOWN" for key in _UNKNOWN}})
        lane_link._public_result(raw, 0, pins)
        return raw, 0
    except (AttestationCleanupUnknown, fixed.TransportCleanupUnknown, TimeoutError):
        raise
    except Exception:
        pass
    finally:
        # Exceptions can retain this frame; never retain the caller's query in it.
        del query_bytes
    raise FixedResultAdapterRefused("fixed_result_refused") from None
