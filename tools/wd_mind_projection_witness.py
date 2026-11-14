"""In-memory, source-rederived witness for an already-screened projection.

No raw JSONL, writer, pointer, encoder, network or bundle operation is provided.
The caller must independently authorize the source, policy, roster and code
pins. A witness is a bounded observation, not a signature, an enduring proof of
membership without the original prefix, a sender identity, or proof that
unrecognized sensitive material was excluded.
"""
from __future__ import annotations

import hashlib
import hmac

from tools import wd_mind_content_query_readonly as content

_WITNESS_SCHEMA = "wd.mind-projection-witness.v1"
_MAX_WITNESS_BYTES = 16 * 1024 * 1024


class ProjectionWitnessRefused(ValueError):
    """Fixed diagnostic that never exposes source, policy or path values."""


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _build(inputs):
    # rederive validates all independently supplied pins and source bytes.
    projection = content.rederive_message_projection(**inputs)
    projection_bytes = content._serialized(projection)
    projection_sha256 = _sha(projection_bytes)
    # A separate complete re-read verifies the exact serialized bytes and the
    # source cursor/generation/identity again before any result is released.
    report = content.validate_content_snapshot(projection_bytes=projection_bytes,
                                               projection_sha256=projection_sha256, **inputs)
    if report.get("status") != "MATCH" or report.get("projection_sha256") != projection_sha256:
        raise ValueError("rederivation_mismatch")
    decision = content._pinned(inputs["decision_bytes"], inputs["decision_sha256"])
    cursor = inputs["messages"]["end_cursor"]
    refs = sorted({row["provenance"]["source_event_ref"] for row in projection["messages"]["records"]})
    if any(not content._hex(ref) for ref in refs):
        raise ValueError("invalid_source_ref")
    anchor = report["source_prefix"]
    witness = {
        "schema": _WITNESS_SCHEMA,
        "authority": "none",
        "scope": "bounded_projection_observation_not_gate",
        "source_anchor": {"sha256": anchor["sha256"], "offset": anchor["offset"],
                          "generation": anchor["generation"], "file_identity": cursor.file_identity,
                          # The validated decision has no event-time cutoff.
                          # An offset or latest retained event is not one.
                          "event_time_cutoff_utc": None},
        "projection_sha256": projection_sha256,
        "projection_schema": projection["schema"],
        "policy_bindings": {"decision_sha256": inputs["decision_sha256"],
                            "policy_sha256": inputs["policy_sha256"],
                            "message_policy_sha256": inputs["messages"]["policy_sha256"],
                            "roster_sha256": inputs["roster_sha256"],
                            "task_scope_sha256": inputs["approved_tasks_sha256"],
                            "identifier_policy_hmac": inputs["identifier_policy_binding"][2]},
        "code_bindings": {"content_module_sha256": decision["content_module_sha256"],
                          "message_module_sha256": decision["message_module_sha256"],
                          "doc_module_sha256": decision["doc_module_sha256"],
                          "prefix_module_sha256": content._REQUIRED_CODE["tools/wd_mind_content_prefix_readonly.py"]},
        "record_count": len(projection["messages"]["records"]),
        "source_event_refs": refs,
        "claims": {"source_consistency": "MATCH_AT_REDERIVATION", "membership_requires_source": True,
                   "coverage": "UNKNOWN", "sender_authentication": "UNKNOWN",
                   "operator_authorization": "UNKNOWN", "privacy_coverage": "UNKNOWN",
                   "freshness": "UNKNOWN", "source_cutoff": "UNKNOWN", "same_user_aba": "UNKNOWN"},
    }
    witness_bytes = content._serialized(witness)
    if len(witness_bytes) > _MAX_WITNESS_BYTES:
        raise ValueError("witness_over_budget")
    return {"projection_bytes": projection_bytes, "projection_sha256": projection_sha256,
            "witness_bytes": witness_bytes, "witness_sha256": _sha(witness_bytes)}


def build_projection_witness(**inputs):
    """Return only screened bytes and a bounded witness after full rederivation."""
    try:
        return _build(inputs)
    except Exception:
        raise ProjectionWitnessRefused("projection_witness_refused") from None


def verify_projection_witness(*, projection_bytes, projection_sha256, witness_bytes, witness_sha256, **inputs):
    """Verify pins, then rederive both artifacts; original source is required."""
    try:
        # Caller hashes ensure transport consistency, not authorization. They
        # do not replace the decision/source checks or complete rederivation.
        content._pinned(projection_bytes, projection_sha256, content._MAX_PROJECTION_BYTES)
        content._pinned(witness_bytes, witness_sha256, _MAX_WITNESS_BYTES)
        current = _build(inputs)
        if (not hmac.compare_digest(projection_bytes, current["projection_bytes"])
                or not hmac.compare_digest(witness_bytes, current["witness_bytes"])):
            raise ValueError("witness_mismatch")
        return {"status": "MATCH_AT_REDERIVATION", "authority": "none",
                "projection_sha256": current["projection_sha256"], "witness_sha256": current["witness_sha256"],
                "source_membership": "MATCH_WITHIN_PREFIX_AT_REDERIVATION", "coverage": "UNKNOWN",
                "sender_authentication": "UNKNOWN", "operator_authorization": "UNKNOWN",
                "privacy_coverage": "UNKNOWN", "freshness": "UNKNOWN", "same_user_aba": "UNKNOWN"}
    except Exception:
        raise ProjectionWitnessRefused("projection_witness_refused") from None
