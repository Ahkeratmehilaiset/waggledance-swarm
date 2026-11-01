"""Default-off prefix-only Q4 reader, not a CLI or live connection.

The caller must independently authenticate every pin, publisher, source and
runtime. A prefix checkpoint does not prove freshness, source authenticity,
caller identity, same-user race resistance or complete earlier history. The
checkpoint's protected prefix is consumed only in-process, never returned.

``query_public_prefix`` is the prefix twin of ``wd_q4_public_query.query_public``:
OFF by default; every caller pin (pointer, source anchor, encoder, policy, roster,
public policy) and the caller-asserted ``VerifiedQ4Config`` are validated, the
strict prefix pointer is resolved and re-verified, the descriptor, memory and
projection bytes are read by their pinned digests and the config is checked to
name exactly that generation (never rebound) BEFORE stdin is read; the backend
runs only through ``wd_q4_agent_query.query_and_resolve`` (a separate pinned
runtime through the launcher). Synthetic tests replace the launcher with an
explicitly labeled in-process stand-in; nothing here supplies a transport,
scheduler, deployment, runtime tree or model authority, and the honest
``UNKNOWN`` markers (publisher authentication, source filter attestation,
freshness, same-user ABA) are never upgraded because digests match.
"""
import hashlib
import hmac
import json
from pathlib import Path

from sandbox.mind.step2 import shared_capture_adapter as adapter
from sandbox.mind.step2 import shared_bridge_snapshot as snapshot
from sandbox.mind.step2 import shared_index_checkpoint as checkpoint
from tools import wd_q4_agent_query as q4_query
from tools import wd_q4_public_pointer_resolver as resolver
from waggledance.core.bridge_log_reader import BridgeCursor, BridgePrefixPin

SCHEMA = "wd.q4-prefix-public-query.v1"
_ARTIFACT_LIMIT = 16 * 1024 * 1024
_PUBLIC_TEXT_LIMIT = 320  # UTF-8 bytes per projected metadata result, the legacy public adapter's bound
_SCOPE = "wd.q4-shared-projected-events.v1"
_ENCODER_KEYS = frozenset({"model", "revision", "weights_sha256"})
_RESULT_KEYS = frozenset({"ref_sha256", "doc_id_sha256", "ref_kind", "canonical_event", "stored_text"})
_DESCRIPTOR_KEYS = frozenset({"schema", "scope", "memory_sha256", "dim", "encoder"})

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


def _encoder_digest(value):
    """Canonical JSON digest of the descriptor's three encoder fields; the caller pins it from the plan, not from disk."""
    if (type(value) is not dict or set(value) != _ENCODER_KEYS
            or any(type(value[key]) is not str or not value[key] for key in _ENCODER_KEYS)
            or not resolver._pin(value["weights_sha256"])):
        raise ValueError()
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode("utf-8")).hexdigest()


def _clean_results(result):
    """The resolver's output reduced to its public shape; any other key, type, pin or oversize text refuses it all."""
    if (type(result) is not dict or set(result) != {"status", "results"}
            or result["status"] not in ("ANSWER", "ABSTAIN", "DEGRADED")
            or type(result["results"]) is not list or len(result["results"]) > 100
            or (result["status"] == "ANSWER" and not result["results"])
            or (result["status"] == "ABSTAIN" and result["results"])):
        raise ValueError()
    clean = []
    for item in result["results"]:
        if (type(item) is not dict or set(item) != _RESULT_KEYS
                or type(item["stored_text"]) is not str
                or len(item["stored_text"].encode("utf-8", errors="strict")) > _PUBLIC_TEXT_LIMIT
                or item["ref_kind"] != "sha256_of_projected_metadata" or item["canonical_event"] is not False
                or not all(resolver._pin(item[key]) for key in ("ref_sha256", "doc_id_sha256"))):
            raise ValueError()
        clean.append({key: item[key] for key in sorted(_RESULT_KEYS)})
    return clean


def query_public_prefix(config, stdin, *, root, expected_pointer_sha256=None, expected_source_sha256=None,
                        expected_encoder_sha256=None, policy_bytes=None, policy_sha256=None,
                        roster_bytes=None, roster_sha256=None, public_policy_bytes=None,
                        public_policy_sha256=None, enabled=False):
    """One bounded agent query over a caller-anchored PREFIX checkpoint; see the module doc for the order.

    stdin is consumed only inside ``query_and_resolve``, after every pin, the config and the verified binding hold.
    The returned text is approved projected metadata from the pinned projection, never a canonical event.
    """
    if enabled is not True:
        raise PrefixPublicRefused("disabled")
    try:
        pins = (expected_pointer_sha256, expected_source_sha256, expected_encoder_sha256,
                policy_sha256, roster_sha256, public_policy_sha256)
        if not all(resolver._pin(value) for value in pins):
            raise ValueError()
        if not all(type(value) is bytes for value in (policy_bytes, roster_bytes, public_policy_bytes)):
            raise ValueError()
        q4_query._validate_config(config)
        binding = resolve_prefix_public_pointer(
            root, expected_pointer_sha256=expected_pointer_sha256, policy_bytes=policy_bytes,
            policy_sha256=policy_sha256, roster_bytes=roster_bytes, roster_sha256=roster_sha256,
            public_policy_bytes=public_policy_bytes, public_policy_sha256=public_policy_sha256, enabled=True)
        secure_root = checkpoint._secure(Path(root))
        tasks, _ = adapter.strict_pins(policy_bytes, policy_sha256, roster_bytes, roster_sha256)
        # the strict prefix view again, for the source anchor the resolver binding does not carry; the protected
        # prefix pin stays inside _view
        view = _view(secure_root, tasks, pointer_sha256=expected_pointer_sha256,
                     policy_bytes=policy_bytes, policy_sha256=policy_sha256,
                     roster_bytes=roster_bytes, roster_sha256=roster_sha256,
                     public_policy_sha256=public_policy_sha256)
        if (not hmac.compare_digest(view["anchor_sha256"], expected_source_sha256)
                or view["generation"] != binding["generation"]
                or binding["source_mode"] != "public-v1"
                or binding["pointer_sha256"] != expected_pointer_sha256
                or any(view["artifacts"][key] != binding[key] for key in resolver._ARTIFACT_KEYS)
                or binding["policy_sha256"] != policy_sha256 or binding["roster_sha256"] != roster_sha256
                or binding["public_policy_sha256"] != public_policy_sha256):
            raise ValueError()
        if (config.memory != binding["memory_root"] or config.descriptor != binding["descriptor_path"]
                or config.descriptor_sha256 != binding["descriptor_sha256"]):
            raise ValueError()  # the caller's config names this exact generation; it is never rebound here
        read = resolver._read_matching
        descriptor = checkpoint._parse(read(Path(binding["descriptor_path"]), binding["descriptor_sha256"],
                                            limit=65536))
        if (type(descriptor) is not dict or set(descriptor) != _DESCRIPTOR_KEYS
                or descriptor["schema"] != "wd.bridge-mind-q4-memory-descriptor.v2" or descriptor["scope"] != _SCOPE
                or type(descriptor["dim"]) is not int or descriptor["dim"] < 1
                or descriptor["memory_sha256"] != binding["memory_sha256"]
                or not hmac.compare_digest(_encoder_digest(descriptor["encoder"]), expected_encoder_sha256)
                or descriptor["encoder"]["revision"] != config.runtime_revision):
            raise ValueError()
        memory_bytes = read(Path(binding["memory_root"]) / "memory.json", binding["memory_sha256"],
                            limit=_ARTIFACT_LIMIT)
        projection_bytes = read(Path(binding["projection_path"]), binding["projection_sha256"], limit=_ARTIFACT_LIMIT)
        pointer_path = secure_root / checkpoint.POINTER
        read(pointer_path, expected_pointer_sha256, limit=checkpoint._MAX_POINTER_BYTES)
        # query_and_resolve takes the full policy task set; the prefix resolver enforced the public subset above
        result = q4_query.query_and_resolve(
            config, stdin, memory_bytes, projection_bytes, tasks,
            policy_bytes=policy_bytes, policy_sha256=policy_sha256,
            roster_bytes=roster_bytes, roster_sha256=roster_sha256,
            expected_memory_sha256=binding["memory_sha256"])
        clean = _clean_results(result)
        read(pointer_path, expected_pointer_sha256, limit=checkpoint._MAX_POINTER_BYTES)
        output = {
            "schema": SCHEMA, "status": result["status"], "results": clean,
            "binding": {
                "pointer_sha256": expected_pointer_sha256, "source_anchor_sha256": expected_source_sha256,
                "encoder_sha256": expected_encoder_sha256, "policy_sha256": policy_sha256,
                "roster_sha256": roster_sha256, "public_policy_sha256": public_policy_sha256,
                **{key: binding[key] for key in sorted(resolver._ARTIFACT_KEYS)},
            },
            "publisher_authentication": "UNKNOWN", "source_filter_attestation": "UNKNOWN",
            "freshness": "UNKNOWN", "same_user_aba": "UNKNOWN",
        }
        if len(json.dumps(output, ensure_ascii=True, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")) > q4_query.MAX_OUTPUT:
            raise ValueError()
        return output
    except Exception:
        raise PrefixPublicRefused("prefix_query_refused") from None
