"""Opt-in, caller-pinned public Q4 generation resolver; not a live transport.

The caller must obtain ``expected_pointer_sha256`` from an independent trusted
source. This module does not authenticate that source, the writer, the caller,
or the clock; it does not provide freshness, anti-replay, a protected watermark,
or same-user race protection. It never derives the expected pin from the file.
The checkpoint implementation must support the public-v1 mode. A returned
binding is not a lease: a caller must independently protect/revalidate inputs
through their use. It contains paths and is not a public log or event payload.
"""

import hashlib
import hmac
import re
from pathlib import Path

from sandbox.mind.step2 import shared_capture_adapter as adapter
from sandbox.mind.step2 import shared_index_checkpoint as checkpoint


class PublicPointerRefused(ValueError):
    """Fixed refusal with no paths, source rows or policy content."""


_HEX = re.compile(r"[0-9a-f]{64}\Z")
_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
_ARTIFACT_KEYS = frozenset({"descriptor_sha256", "memory_sha256", "projection_sha256", "receipt_sha256"})
_VIEW_KEYS = frozenset({"pointer_sha256", "generation", "artifacts", "anchor_sha256",
                        "cursor_end_sha256", "watermark_sha256"})


def _pin(value):
    return isinstance(value, str) and _HEX.fullmatch(value) is not None


def _read_matching(path, expected, *, limit):
    raw = checkpoint._read(path, max_bytes=limit)
    if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), expected):
        raise ValueError()
    return raw


def resolve_public_pointer(root, *, expected_pointer_sha256, policy_bytes, policy_sha256,
                           roster_bytes, roster_sha256, public_policy_bytes, public_policy_sha256,
                           enabled=False):
    """Resolve one independently pinned pointer into a Q4 reader binding.

    All policy bytes are caller-supplied and their origins must be separately
    trusted. The checkpoint validates the public generation/history. This
    boundary additionally applies the public task/type allowlist to every
    retained projection record. It performs no writes or query execution.
    """
    if enabled is not True:
        raise PublicPointerRefused("disabled")
    try:
        if not all(_pin(value) for value in (expected_pointer_sha256, policy_sha256, roster_sha256,
                                              public_policy_sha256)):
            raise ValueError()
        root = checkpoint._secure(Path(root))
        tasks, _ = adapter.strict_pins(policy_bytes, policy_sha256, roster_bytes, roster_sha256)
        public_tasks, public_types = adapter.public_status_policy(public_policy_bytes, public_policy_sha256, tasks)
        view = checkpoint.read_verified_pointer(
            root, tasks, expected_pointer_sha256=expected_pointer_sha256,
            policy_bytes=policy_bytes, policy_sha256=policy_sha256,
            roster_bytes=roster_bytes, roster_sha256=roster_sha256,
            expected_mode="public-v1", expected_public_policy_sha256=public_policy_sha256,
        )
        if (type(view) is not dict or set(view) != _VIEW_KEYS
                or view["pointer_sha256"] != expected_pointer_sha256
                or not isinstance(view["generation"], str) or not _NAME.fullmatch(view["generation"])
                or type(view["artifacts"]) is not dict or set(view["artifacts"]) != _ARTIFACT_KEYS
                or not all(_pin(v) for v in view["artifacts"].values())
                or not all(_pin(view[k]) for k in ("anchor_sha256", "cursor_end_sha256", "watermark_sha256"))):
            raise ValueError()
        generation = checkpoint._secure(root / view["generation"])
        descriptor = generation / "descriptor.json"
        projection = generation / "projection.json"
        _read_matching(descriptor, view["artifacts"]["descriptor_sha256"], limit=65536)
        projection_bytes = _read_matching(projection, view["artifacts"]["projection_sha256"], limit=checkpoint._MAX)
        parsed = checkpoint._parse(projection_bytes)
        records = parsed.get("records")
        if type(records) is not list:
            raise ValueError()
        for record in records:
            if type(record) is not dict or type(record.get("meta")) is not dict:
                raise ValueError()
            meta = record["meta"]
            if meta.get("task_id") not in public_tasks or meta.get("type") not in public_types:
                raise ValueError()
        # Refuse an ordinary concurrent pointer replacement during resolution.
        _read_matching(root / checkpoint.POINTER, expected_pointer_sha256, limit=checkpoint._MAX_POINTER_BYTES)
        return {
            "memory_root": str(generation), "descriptor_path": str(descriptor),
            "descriptor_sha256": view["artifacts"]["descriptor_sha256"],
            "memory_sha256": view["artifacts"]["memory_sha256"],
            "projection_path": str(projection), "projection_sha256": view["artifacts"]["projection_sha256"],
            "receipt_sha256": view["artifacts"]["receipt_sha256"],
            "pointer_sha256": expected_pointer_sha256, "generation": view["generation"],
            "policy_sha256": policy_sha256, "roster_sha256": roster_sha256,
            "public_policy_sha256": public_policy_sha256,
            "source_mode": "public-v1", "freshness": "UNKNOWN", "identity": "NOT_AUTHENTICATED",
            "publisher_authentication": "NOT_AUTHENTICATED", "source_filter_attestation": "NOT_VERIFIED",
        }
    except Exception:
        raise PublicPointerRefused("resolution_refused") from None
