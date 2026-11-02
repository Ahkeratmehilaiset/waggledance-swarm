"""Default-off lane-binding carrier: two caller-pinned documents in, the ``query_public`` pin keyword set out.

``resolve_lane_binding`` takes the fleet document bytes (roster membership through the existing
``tools.wd_q4_roster_from_fleet.build_roster_snapshot`` producer) and a SEPARATELY pinned closed binding document
(schema ``wd.q4-lane-binding.v1``), both with caller-supplied sha256 pins, and returns exactly the pin keywords of
``tools.wd_q4_lane_query.query_public`` for ONE declared lane: ``lane``, ``expected_commit``,
``expected_manifest_sha256``, ``expected_wrapper_sha256``, ``expected_public_plan_sha256`` and ``expected_binding``
(the eight ``BINDING_KEYS`` digests). The caller still supplies situation, time, roots, plan location, the pinned
projection/policy/roster bytes and the transport itself; this module reads no file, calls nothing but the two pure
helpers above, and never looks at the live fleet configuration. The committed fleet file carries no binding
document, so without a separately pinned one every call refuses and the live configuration stays OFF.

Lane membership here is a DECLARATION: the lane name must be one of the roster snapshot lanes and listed in the
binding document. That is not lane authentication; nothing in this module proves who the caller is.

Order: switch (``enabled`` must be the bool ``True``; ``False`` returns ``{"status": "OFF"}`` and reads nothing),
binding bytes type, byte budget and pin (nothing parsed yet), fleet pin and roster membership through the producer,
binding document parse and closed shape, roster consistency (``binding.roster_sha256`` must equal the snapshot
digest the producer derives from the pinned fleet, else ``roster_refused``), consumer contract (signature and
``BINDING_KEYS`` of the real ``query_public``), lane. Every refusal is ``LaneBindingRefused`` with one fixed token
from ``REASONS``, raised outside every exception handler so the exception message and chained exceptions contain no
parser detail, digest, path or document content. Traceback frame locals can still contain input documents; callers
must not log locals or otherwise disclose them.
"""
import hashlib
import hmac
import inspect
import json
import re

from tools import wd_q4_lane_query as lane_query
from tools import wd_q4_roster_from_fleet as roster_mod

__all__ = ["LaneBindingRefused", "resolve_lane_binding", "SCHEMA", "BINDING_KEYS", "OUTPUT_KEYS", "REASONS",
           "BINDING_LIMIT", "IDENTITY"]

SCHEMA = "wd.q4-lane-binding.v1"
IDENTITY = "declared_not_authenticated"
BINDING_LIMIT = 1 << 16
BINDING_KEYS = frozenset({"pointer_sha256", "source_anchor_sha256", "encoder_sha256", "policy_sha256",
                          "roster_sha256", "public_policy_sha256", "descriptor_sha256", "projection_sha256"})
OUTPUT_KEYS = frozenset({"lane", "expected_commit", "expected_manifest_sha256", "expected_wrapper_sha256",
                         "expected_public_plan_sha256", "expected_binding"})
REASONS = frozenset({"disabled", "fleet_refused", "binding_refused", "roster_refused", "lane_refused",
                     "contract_refused"})

_DOCUMENT_KEYS = frozenset({"schema", "bundle_commit", "manifest_sha256", "wrapper_sha256", "public_plan_sha256",
                            "binding", "lanes"})
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
# query_public parameters the caller supplies at call time; everything else in its signature is a pin this
# carrier resolves, and the two sets must partition the real signature exactly.
_CALLER_PARAMETERS = frozenset({"situation", "time", "bundle_root", "public_plan_path", "projection_bytes",
                                "policy_bytes", "roster_bytes", "transport", "enabled"})


class LaneBindingRefused(ValueError):
    """Fixed refusal token from ``REASONS``; carries no document content, digest or path."""


def _hex64(value):
    return type(value) is str and _HEX64.fullmatch(value) is not None


def _pairs_without_duplicates(pairs):
    document = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("duplicate_key")
        document[key] = value
    return document


def _reject_constant(_value):
    raise ValueError("nonstandard_json_constant")


def _parse(binding_bytes):
    try:
        document = json.loads(binding_bytes.decode("utf-8", "strict"), object_pairs_hook=_pairs_without_duplicates,
                              parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        document = None
        failed = True
    else:
        failed = False
    if failed:
        raise LaneBindingRefused("binding_refused")
    return document


def _roster(fleet_bytes, expected_fleet_sha256):
    """``(lane set, snapshot digest)`` from the pinned producer; any producer refusal is one ``fleet_refused``."""
    snapshot = digest = None
    try:
        snapshot, digest = roster_mod.build_roster_snapshot(fleet_bytes, expected_fleet_sha256, enabled=True)
    except roster_mod.RosterProducerRefused:
        snapshot = None
    if snapshot is None:
        raise LaneBindingRefused("fleet_refused")
    roster = json.loads(snapshot.decode("ascii"))
    lanes = {entry["agent"] for entry in roster["lanes"]}
    lanes.add(roster["tools_supervisor"]["agent"])
    return frozenset(lanes), digest


def _shape(document, roster_lanes):
    if type(document) is not dict or frozenset(document) != _DOCUMENT_KEYS:
        return False
    if document["schema"] != SCHEMA:
        return False
    commit = document["bundle_commit"]
    if type(commit) is not str or _HEX40.fullmatch(commit) is None:
        return False
    if not all(_hex64(document[key]) for key in ("manifest_sha256", "wrapper_sha256", "public_plan_sha256")):
        return False
    binding = document["binding"]
    if type(binding) is not dict or frozenset(binding) != BINDING_KEYS or not all(map(_hex64, binding.values())):
        return False
    lanes = document["lanes"]
    # element types first: an unhashable element must be a refusal, never a TypeError out of set()
    if type(lanes) is not list or not lanes or not all(type(lane) is str for lane in lanes):
        return False
    return len(set(lanes)) == len(lanes) and all(lane in roster_lanes for lane in lanes)


def _contract_holds():
    try:
        parameters = inspect.signature(lane_query.query_public).parameters
    except (TypeError, ValueError):
        return False
    if any(parameter.kind is not inspect.Parameter.KEYWORD_ONLY for parameter in parameters.values()):
        return False
    if frozenset(parameters) - _CALLER_PARAMETERS != OUTPUT_KEYS or not _CALLER_PARAMETERS <= frozenset(parameters):
        return False
    return lane_query.BINDING_KEYS == BINDING_KEYS


def resolve_lane_binding(fleet_bytes, expected_fleet_sha256, binding_bytes, expected_binding_sha256, *, lane,
                         enabled=False):
    """The ``query_public`` pin keyword set for ``lane`` from the pinned fleet and binding documents; see module doc.

    ``enabled`` must be the bool ``True``; ``False`` returns ``{"status": "OFF"}`` without reading any argument.
    """
    if enabled is False:
        return {"status": "OFF"}
    if enabled is not True:
        raise LaneBindingRefused("disabled")
    if type(binding_bytes) is not bytes or len(binding_bytes) > BINDING_LIMIT or not _hex64(expected_binding_sha256):
        raise LaneBindingRefused("binding_refused")
    if not hmac.compare_digest(hashlib.sha256(binding_bytes).hexdigest(), expected_binding_sha256):
        raise LaneBindingRefused("binding_refused")
    roster_lanes, roster_digest = _roster(fleet_bytes, expected_fleet_sha256)
    document = _parse(binding_bytes)
    if not _shape(document, roster_lanes):
        raise LaneBindingRefused("binding_refused")
    # fleet/roster consistency: the document's roster pin must be the digest the pinned producer derives from
    # the pinned fleet, so the lane list and the roster pin handed to query_public describe the same roster
    if not hmac.compare_digest(document["binding"]["roster_sha256"], roster_digest):
        raise LaneBindingRefused("roster_refused")
    if not _contract_holds():
        raise LaneBindingRefused("contract_refused")
    if type(lane) is not str or lane not in roster_lanes or lane not in document["lanes"]:
        raise LaneBindingRefused("lane_refused")
    return {
        "lane": lane,
        "expected_commit": document["bundle_commit"],
        "expected_manifest_sha256": document["manifest_sha256"],
        "expected_wrapper_sha256": document["wrapper_sha256"],
        "expected_public_plan_sha256": document["public_plan_sha256"],
        "expected_binding": {key: document["binding"][key] for key in sorted(BINDING_KEYS)},
    }
