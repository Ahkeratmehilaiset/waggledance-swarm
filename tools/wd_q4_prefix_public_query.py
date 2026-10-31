"""Default-off prefix-only Q4 reader proposal, not a CLI or live connection.

The caller must independently authenticate every pin, publisher, source and
runtime. A prefix checkpoint does not prove freshness, source authenticity,
caller identity, same-user race resistance or complete earlier history. The
checkpoint's protected prefix is consumed only in-process, never returned.
Query execution refuses before stdin until the approved secure transport exists.
Nothing here supplies a scheduler, transport, deployment or model authority.
"""
import hashlib
import hmac
from pathlib import Path

from sandbox.mind.step2 import shared_capture_adapter as adapter
from sandbox.mind.step2 import shared_bridge_snapshot as snapshot
from sandbox.mind.step2 import shared_index_checkpoint as checkpoint
from tools import wd_q4_public_pointer_resolver as resolver
from waggledance.core.bridge_log_reader import BridgeCursor, BridgePrefixPin

class PrefixPublicRefused(ValueError):
    """Fixed public refusal; no private paths, source bytes or prefix digests."""


def _view(root, tasks, *, pointer_sha256, policy_bytes, policy_sha256,
          roster_bytes, roster_sha256, public_policy_sha256):
    """Strict schema only; never call the legacy checkpoint accessor."""
    result = checkpoint.read_verified_prefix_pointer(
        root, tasks, expected_pointer_sha256=pointer_sha256,
        policy_bytes=policy_bytes, policy_sha256=policy_sha256,
        roster_bytes=roster_bytes, roster_sha256=roster_sha256,
        expected_public_policy_sha256=public_policy_sha256)
    if type(result) is not tuple or len(result) != 2 or type(result[1]) is not BridgePrefixPin:
        raise ValueError()
    view, protected_prefix = result
    if (type(view) is not dict or set(view) != resolver._VIEW_KEYS
            or view["pointer_sha256"] != pointer_sha256
            or type(view["generation"]) is not str or resolver._NAME.fullmatch(view["generation"]) is None
            or type(view["artifacts"]) is not dict or set(view["artifacts"]) != resolver._ARTIFACT_KEYS
            or not all(resolver._pin(x) for x in view["artifacts"].values())
            or not all(resolver._pin(view[k]) for k in ("anchor_sha256", "cursor_end_sha256", "watermark_sha256"))
            or type(protected_prefix.cursor) is not BridgeCursor or not resolver._pin(protected_prefix.sha256)
            or not hmac.compare_digest(hashlib.sha256(snapshot._token(protected_prefix.cursor).encode()).hexdigest(),
                                       view["watermark_sha256"])):
        raise ValueError()
    return view


def resolve_prefix_public_pointer(root, *, expected_pointer_sha256, policy_bytes, policy_sha256,
                                  roster_bytes, roster_sha256, public_policy_bytes, public_policy_sha256,
                                  enabled=False):
    """Resolve a caller-anchored prefix checkpoint; no legacy fallback or public prefix."""
    if enabled is not True:
        raise PrefixPublicRefused("disabled")
    try:
        if not all(resolver._pin(x) for x in (expected_pointer_sha256, policy_sha256,
                                               roster_sha256, public_policy_sha256)):
            raise ValueError()
        root = checkpoint._secure(Path(root))
        tasks, _ = adapter.strict_pins(policy_bytes, policy_sha256, roster_bytes, roster_sha256)
        public_tasks, public_types = adapter.public_status_policy(public_policy_bytes, public_policy_sha256, tasks)
        view = _view(root, tasks, pointer_sha256=expected_pointer_sha256,
                     policy_bytes=policy_bytes, policy_sha256=policy_sha256,
                     roster_bytes=roster_bytes, roster_sha256=roster_sha256,
                     public_policy_sha256=public_policy_sha256)
        generation = checkpoint._secure(root / view["generation"])
        descriptor, projection = generation / "descriptor.json", generation / "projection.json"
        resolver._read_matching(descriptor, view["artifacts"]["descriptor_sha256"], limit=65536)
        projection_bytes = resolver._read_matching(projection, view["artifacts"]["projection_sha256"],
                                                    limit=checkpoint._MAX)
        parsed = checkpoint._parse(projection_bytes)
        records = parsed.get("records")
        if type(records) is not list:
            raise ValueError()
        for record in records:
            if type(record) is not dict or type(record.get("meta")) is not dict:
                raise ValueError()
            meta = record["meta"]
            if (meta.get("task_id") not in public_tasks or meta.get("type") not in public_types
                    or type(meta.get("agent")) is not str or meta["agent"] not in resolver._PUBLIC_AGENTS):
                raise ValueError()
        resolver._read_matching(root / checkpoint.POINTER, expected_pointer_sha256, limit=checkpoint._MAX_POINTER_BYTES)
        return {"memory_root": str(generation), "descriptor_path": str(descriptor),
                "descriptor_sha256": view["artifacts"]["descriptor_sha256"],
                "memory_sha256": view["artifacts"]["memory_sha256"], "projection_path": str(projection),
                "projection_sha256": view["artifacts"]["projection_sha256"],
                "receipt_sha256": view["artifacts"]["receipt_sha256"], "pointer_sha256": expected_pointer_sha256,
                "generation": view["generation"], "policy_sha256": policy_sha256, "roster_sha256": roster_sha256,
                "public_policy_sha256": public_policy_sha256, "source_mode": "public-v1",
                "freshness": "UNKNOWN", "identity": "NOT_AUTHENTICATED",
                "publisher_authentication": "NOT_AUTHENTICATED", "source_filter_attestation": "NOT_VERIFIED"}
    except Exception:
        raise PrefixPublicRefused("resolution_refused") from None


def query_public_prefix(config, stdin, *, root, expected_pointer_sha256=None, expected_source_sha256=None,
                        expected_encoder_sha256=None, policy_bytes=None, policy_sha256=None,
                        roster_bytes=None, roster_sha256=None, public_policy_bytes=None,
                        public_policy_sha256=None, enabled=False):
    """Refuse before consuming stdin or invoking any runtime until the secure peer is bound."""
    if enabled is not True:
        raise PrefixPublicRefused("disabled")
    raise PrefixPublicRefused("secure_transport_unavailable")
