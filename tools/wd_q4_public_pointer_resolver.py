"""Opt-in, caller-pinned public Q4 generation resolver; not a live transport.

The caller must obtain ``expected_pointer_sha256`` from an independent trusted
source. This module does not authenticate that source, the writer, the caller,
or the clock; it does not provide freshness, anti-replay, a protected watermark,
or same-user race protection. It never derives the expected pin from the file.
The checkpoint implementation must support the public-v1 mode. A returned
binding is not a lease: a caller must independently protect/revalidate inputs
through their use. It contains paths and is not a public log or event payload.
An optional caller-pinned synthetic card route decision is verified and bound
(default None: the v6 behaviour and binding are unchanged); it authenticates no
issuer, and the checkpoint itself still validates v6 public histories only.
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
_PUBLIC_AGENTS = checkpoint._preflight_contract.PUBLIC_AGENTS


def _pin(value):
    return isinstance(value, str) and _HEX.fullmatch(value) is not None


def _read_matching(path, expected, *, limit):
    raw = checkpoint._read(path, max_bytes=limit)
    if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), expected):
        raise ValueError()
    return raw


def resolve_public_pointer(root, *, expected_pointer_sha256, policy_bytes, policy_sha256,
                           roster_bytes, roster_sha256, public_policy_bytes, public_policy_sha256,
                           enabled=False, card_decision_bytes=None, card_decision_sha256=None):
    """Resolve one independently pinned pointer into a Q4 reader binding.

    All policy bytes are caller-supplied and their origins must be separately
    trusted. The checkpoint validates the public generation/history. This
    boundary additionally applies the public task/type/agent allowlists to
    every retained projection record. Agent names are not authenticated and
    recipient filtering is not proven here. No writes or query execution occur.
    With a card decision (both values or neither), the decision must verify for
    these policy and roster pins and the generation projection must be the card
    projection it names; the binding then carries ``card_decision_sha256``.
    """
    if enabled is not True:
        raise PublicPointerRefused("disabled")
    try:
        if not all(_pin(value) for value in (expected_pointer_sha256, policy_sha256, roster_sha256,
                                              public_policy_sha256)):
            raise ValueError()
        card = card_decision_bytes is not None or card_decision_sha256 is not None
        if card:
            from sandbox.mind.step2 import shared_card_projection as cards
            decision = cards.verify_route_decision(card_decision_bytes, card_decision_sha256,
                                                   policy_sha256=policy_sha256, roster_sha256=roster_sha256)
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
        if card and parsed.get("schema") != decision["projection_schema"]:
            raise ValueError()
        if type(records) is not list:
            raise ValueError()
        for record in records:
            if type(record) is not dict or type(record.get("meta")) is not dict:
                raise ValueError()
            meta = record["meta"]
            if (meta.get("task_id") not in public_tasks or meta.get("type") not in public_types
                    or type(meta.get("agent")) is not str or meta["agent"] not in _PUBLIC_AGENTS):
                raise ValueError()
        # Refuse an ordinary concurrent pointer replacement during resolution.
        _read_matching(root / checkpoint.POINTER, expected_pointer_sha256, limit=checkpoint._MAX_POINTER_BYTES)
        binding = {
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
        if card:
            binding["card_decision_sha256"] = card_decision_sha256
        return binding
    except Exception:
        raise PublicPointerRefused("resolution_refused") from None


def resolve_card_snapshot_pointer(root, *, pointer_bytes=None, expected_pointer_sha256=None,
                                  policy_bytes=None, policy_sha256=None, roster_bytes=None,
                                  roster_sha256=None, card_decision_bytes=None,
                                  card_decision_sha256=None, card_policy_bytes=None,
                                  source_path=None, generation_path=None, end_cursor=None,
                                  enabled=False):
    """Validate one caller-pinned synthetic C2 snapshot into a staged binding.

    This separate opt-in route never calls the v6 history verifier, publishes
    a pointer or supplies a history receipt. The caller supplies the snapshot
    bytes and every source/decision pin; none is inferred from installed state.
    The result is private resolver data, not a public event or query lease.
    Query/outer/CLI consumers must explicitly support this distinct binding;
    it cannot stand in for the legacy public-v1 binding. Caller/source trust,
    freshness and same-user race protection remain unproven.
    """
    if enabled is not True:
        raise PublicPointerRefused("disabled")
    try:
        import json
        from sandbox.mind.step2 import shared_card_checkpoint as card_checkpoint

        if (type(pointer_bytes) is not bytes or len(pointer_bytes) > 1024 * 1024
                or not _pin(expected_pointer_sha256)
                or hashlib.sha256(pointer_bytes).hexdigest() != expected_pointer_sha256):
            raise ValueError()
        pointer = json.loads(pointer_bytes)
        if (type(pointer) is not dict or type(pointer.get("generation")) is not str
                or not _NAME.fullmatch(pointer["generation"])):
            raise ValueError()
        root = checkpoint._secure(Path(root))
        if not root.is_absolute() or not root.is_dir():
            raise ValueError()
        generation = root / pointer["generation"]
        checked = card_checkpoint.validate_card_snapshot(
            enabled=True, pointer_bytes=pointer_bytes,
            expected_pointer_sha256=expected_pointer_sha256, generation_dir=generation,
            decision_bytes=card_decision_bytes, decision_sha256=card_decision_sha256,
            policy_bytes=policy_bytes, policy_sha256=policy_sha256,
            roster_bytes=roster_bytes, roster_sha256=roster_sha256,
            card_policy_bytes=card_policy_bytes, source_path=source_path,
            generation_path=generation_path, end_cursor=end_cursor)
        if checked != {"status": "VALIDATED_STAGED", "completeness": "UNKNOWN"}:
            raise ValueError()
        artifacts = pointer["artifacts"]
        # Bind the exact bytes still present after C2's complete verification.
        for name in ("descriptor", "memory", "projection"):
            _read_matching(generation / (name + ".json"), artifacts[name + ".json"],
                           limit=65536 if name == "descriptor" else checkpoint._MAX)
        return {
            "status": "VALIDATED_STAGED", "completeness": "UNKNOWN",
            "memory_root": str(generation), "descriptor_path": str(generation / "descriptor.json"),
            "descriptor_sha256": artifacts["descriptor.json"],
            "memory_sha256": artifacts["memory.json"],
            "projection_path": str(generation / "projection.json"),
            "projection_sha256": artifacts["projection.json"],
            "pointer_sha256": expected_pointer_sha256, "generation": pointer["generation"],
            "policy_sha256": policy_sha256, "roster_sha256": roster_sha256,
            "card_decision_sha256": card_decision_sha256,
            "card_policy_sha256": hashlib.sha256(card_policy_bytes).hexdigest(),
            "source_prefix": pointer["source_prefix"], "source_mode": "card-snapshot-v1",
            "freshness": "UNKNOWN", "identity": "NOT_AUTHENTICATED",
            "publisher_authentication": "NOT_AUTHENTICATED",
            "source_filter_attestation": "NOT_VERIFIED",
        }
    except Exception:
        raise PublicPointerRefused("resolution_refused") from None
