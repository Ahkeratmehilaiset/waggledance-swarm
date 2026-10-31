"""Default-off lane-side public Q4 caller over ``tools.wd_q4_lane_link``; neither a CLI, a transport nor a wiring.

One lane turn asks the pinned public Q4 wrapper one bounded question and receives a closed answer. Everything
this module needs comes from the caller as explicit arguments: the bundle root, commit, deployment-manifest and
wrapper digests, the hash-pinned public plan, the caller's OWN expected binding digests (pointer, source anchor,
encoder, policy, roster, public policy) and an injected transport. Nothing is read from the environment, argv,
a log or a default location; the situation text travels only as in-memory stdin bytes to the transport, never in
arguments, exceptions or output. The seam (``wd_q4_lane_link.invoke_public``) verifies bundle, manifest, wrapper
and plan and validates the reply; this module additionally requires the reply binding to equal the caller's own
expected digests and filters every returned text through a closed character class before anything is returned.

``lane`` is a declaration shared by the five known lanes; it is never authentication, and this module does not
prove freshness, publisher authentication, source filtering, same-user protection, process containment or a
whole-call time bound. Until a separately trusted transport exists (``TRANSPORT_INTERFACE``), only an injected
synthetic callable can be used; nothing here enables, installs or activates a producer, runtime or model.
"""
from datetime import datetime, timezone
import hmac
import json
from os import PathLike
import re

from tools import wd_q4_lane_link as link

SCHEMA = "wd.q4-lane-answer.v1"
LANES = link._LANES
MAX_SITUATION_CHARS = 4000
MAX_QUERY_BYTES = 65536
MAX_RESULTS = 100
TRANSPORT_INTERFACE = "transport(*, wrapper: str, arguments: tuple, stdin_bytes: bytes) -> (bytes, int)"
BINDING_KEYS = frozenset({"pointer_sha256", "source_anchor_sha256", "encoder_sha256", "policy_sha256",
                          "roster_sha256", "public_policy_sha256"})
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_PIN64 = re.compile(r"[0-9A-Fa-f]{64}\Z")
_TEXT = re.compile(r"[A-Za-z0-9._: -]{1,320}\Z")
_HEX_RUN = re.compile(r"[0-9a-fA-F]{32,}")
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_SEAM_REASONS = frozenset({"disabled", "invalid_lane", "transport_unavailable", "binding_refused", "invalid_query",
                           "transport_refused"})
_TRUST = {"lane_authentication": "UNKNOWN", "freshness": "UNKNOWN", "publisher_authentication": "UNKNOWN",
          "transport": "caller_injected"}


class LaneQueryRefused(ValueError):
    """Fixed public reason only: never the situation, a path, a pin, a reply or a transport detail."""


def _hex(value, pattern):
    return type(value) is str and pattern.fullmatch(value) is not None


def _inputs(situation, time, expected_commit, expected_manifest_sha256, expected_wrapper_sha256,
            expected_public_plan_sha256, expected_binding, bundle_root, public_plan_path):
    """Syntax of every caller input, checked before any file is touched; returns the canonical query bytes."""
    if (not _hex(expected_commit, _HEX40) or not _hex(expected_manifest_sha256, _PIN64)
            or not _hex(expected_wrapper_sha256, _PIN64) or not _hex(expected_public_plan_sha256, _HEX64)
            or type(expected_binding) is not dict or set(expected_binding) != BINDING_KEYS
            or any(not _hex(expected_binding[key], _HEX64) for key in BINDING_KEYS)
            or not isinstance(bundle_root, (str, PathLike)) or not isinstance(public_plan_path, (str, PathLike))):
        raise ValueError()
    if (type(situation) is not str or not 1 <= len(situation) <= MAX_SITUATION_CHARS
            or any(ord(char) < 0x20 or ord(char) == 0x7F for char in situation)):
        raise ValueError()
    if not isinstance(time, datetime) or time.tzinfo is None or time.utcoffset() is None:
        raise ValueError()
    query = json.dumps({"situation": situation, "time": time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
                       sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")
    if len(query) > MAX_QUERY_BYTES:
        raise ValueError()
    return query


def _text(value):
    if (type(value) is not str or _TEXT.fullmatch(value) is None or "@" in value
            or _HEX_RUN.search(value) is not None or _UUID.search(value) is not None):
        raise ValueError()
    return value


def _answer(lane, value, expected_binding):
    """Closed projection of a seam-validated reply; the caller's own pins decide, never the plan alone."""
    if value["status"] == "REFUSED":
        return {"schema": SCHEMA, "lane": lane, "status": "REFUSED", "reason": "runtime_refused",
                "result_count": 0, "results": [], "binding": None, "trust": dict(_TRUST)}
    binding = value["binding"]
    if any(not hmac.compare_digest(binding[key], expected_binding[key]) for key in BINDING_KEYS):
        raise LaneQueryRefused("binding_refused")
    results = value["results"]
    if type(results) is not list or len(results) > MAX_RESULTS:
        raise LaneQueryRefused("privacy_refused")
    try:
        clean = [{"ref_sha256": item["ref_sha256"], "doc_id_sha256": item["doc_id_sha256"],
                  "stored_text": _text(item["stored_text"])} for item in results]
    except Exception:
        raise LaneQueryRefused("privacy_refused") from None
    return {"schema": SCHEMA, "lane": lane, "status": value["status"], "reason": "", "result_count": len(clean),
            "results": clean, "binding": {key: expected_binding[key] for key in sorted(BINDING_KEYS)},
            "trust": dict(_TRUST)}


def query_public(*, lane, situation, time, bundle_root, expected_commit, expected_manifest_sha256,
                 expected_wrapper_sha256, public_plan_path, expected_public_plan_sha256, expected_binding,
                 transport=None, enabled=False):
    """One bounded public Q4 question for one declared lane over an injected transport; OFF by default.

    Order: switch, lane, transport presence, input syntax (nothing read yet), canonical query bytes in memory,
    then ``wd_q4_lane_link.invoke_public`` (binds bundle, manifest, wrapper and plan, calls the transport once,
    validates the reply against the plan pins), then the caller's own ``expected_binding`` must equal the reply
    binding and every ``stored_text`` must pass the closed text filter. Any other outcome is a fixed refusal.
    """
    if enabled is not True:
        raise LaneQueryRefused("disabled")
    if type(lane) is not str or lane not in LANES:
        raise LaneQueryRefused("invalid_lane")
    if not callable(transport):
        raise LaneQueryRefused("transport_unavailable")
    try:
        query = _inputs(situation, time, expected_commit, expected_manifest_sha256, expected_wrapper_sha256,
                        expected_public_plan_sha256, expected_binding, bundle_root, public_plan_path)
    except Exception:
        raise LaneQueryRefused("invalid_input") from None
    try:
        value = link.invoke_public(lane=lane, bundle_root=bundle_root, expected_commit=expected_commit,
                                   expected_manifest_sha256=expected_manifest_sha256,
                                   expected_wrapper_sha256=expected_wrapper_sha256,
                                   public_plan_path=public_plan_path,
                                   expected_public_plan_sha256=expected_public_plan_sha256,
                                   query_bytes=query, transport=transport, enabled=True)
    except link.LaneLinkRefused as refusal:
        reason = refusal.args[0] if refusal.args and refusal.args[0] in _SEAM_REASONS else "transport_refused"
        raise LaneQueryRefused(reason) from None
    except Exception:
        raise LaneQueryRefused("transport_refused") from None
    try:
        return _answer(lane, value, expected_binding)
    except LaneQueryRefused:
        raise
    except Exception:
        raise LaneQueryRefused("privacy_refused") from None
