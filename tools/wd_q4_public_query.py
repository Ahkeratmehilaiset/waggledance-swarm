"""Default-off, caller-pinned public Q4 adapter proposal; no CLI or activation.

This is glue, not authentication. The caller must independently trust every pin,
the policy/roster/source publisher, runtime config, code and encoder assets.
expected_source_sha256 means the verified checkpoint view's anchor_sha256.
expected_encoder_sha256 pins canonical JSON of the descriptor's three encoder
fields (sort_keys=True, separators=(",", ":"), UTF-8, ensure_ascii=True).
Ordinary pointer changes are rechecked, not protected against same-user ABA.
The existing backend may take its lock/write scratch when explicitly enabled;
this adapter itself never writes. Native execution is NOT authorized by a flag.
"""
import hashlib
import json
from pathlib import Path

from tools import wd_q4_agent_query as q4_query
from tools import wd_q4_public_pointer_resolver as pointer_resolver

_ARTIFACT_LIMIT = 16 * 1024 * 1024
_SCOPE = "wd.q4-shared-projected-events.v1"
_ENCODER_KEYS = frozenset({"model", "revision", "weights_sha256"})
_RESULT_KEYS = frozenset({"ref_sha256", "doc_id_sha256", "ref_kind", "canonical_event"})


class PublicQueryRefused(ValueError):
    """Fixed reason only: no queries, paths or backend messages."""


def _encoder_digest(value):
    if (type(value) is not dict or set(value) != _ENCODER_KEYS
            or any(type(value[key]) is not str or not value[key] for key in _ENCODER_KEYS)
            or not pointer_resolver._pin(value["weights_sha256"])):
        raise ValueError()
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode("utf-8")).hexdigest()


def query_public(config, stdin, *, root, expected_pointer_sha256=None,
                 expected_source_sha256=None, expected_encoder_sha256=None,
                 policy_bytes=None, policy_sha256=None, roster_bytes=None,
                 roster_sha256=None, public_policy_bytes=None,
                 public_policy_sha256=None, enabled=False):
    """Resolve, bind and query one caller-selected public generation.

    No environment switch, self-derived expected pin, legacy fallback, publisher
    attestation or live channel is supplied here. Disabled refuses before any
    config validation, stdin read, resolver or backend invocation.
    """
    if enabled is not True:
        raise PublicQueryRefused("disabled")
    try:
        pins = (expected_pointer_sha256, expected_source_sha256,
                expected_encoder_sha256, policy_sha256, roster_sha256,
                public_policy_sha256)
        if not all(pointer_resolver._pin(value) for value in pins):
            raise ValueError()
        if not all(type(value) is bytes for value in
                   (policy_bytes, roster_bytes, public_policy_bytes)):
            raise ValueError()
        q4_query._validate_config(config)
        binding = pointer_resolver.resolve_public_pointer(
            root, expected_pointer_sha256=expected_pointer_sha256,
            policy_bytes=policy_bytes, policy_sha256=policy_sha256,
            roster_bytes=roster_bytes, roster_sha256=roster_sha256,
            public_policy_bytes=public_policy_bytes,
            public_policy_sha256=public_policy_sha256, enabled=True)
        tasks, _ = pointer_resolver.adapter.strict_pins(
            policy_bytes, policy_sha256, roster_bytes, roster_sha256)
        # The current public resolver omits the source anchor from its return.
        # Reuse the existing verifier, never obtain an expected pin from disk.
        view = pointer_resolver.checkpoint.read_verified_pointer(
            root, tasks, expected_pointer_sha256=expected_pointer_sha256,
            policy_bytes=policy_bytes, policy_sha256=policy_sha256,
            roster_bytes=roster_bytes, roster_sha256=roster_sha256,
            expected_mode="public-v1",
            expected_public_policy_sha256=public_policy_sha256)
        if (type(view) is not dict or set(view) != pointer_resolver._VIEW_KEYS
                or view["anchor_sha256"] != expected_source_sha256
                or view["pointer_sha256"] != expected_pointer_sha256
                or view["generation"] != binding["generation"]
                or type(view["artifacts"]) is not dict
                or set(view["artifacts"]) != pointer_resolver._ARTIFACT_KEYS
                or binding["source_mode"] != "public-v1"
                or binding["pointer_sha256"] != expected_pointer_sha256):
            raise ValueError()
        for field in pointer_resolver._ARTIFACT_KEYS:
            if not pointer_resolver._pin(view["artifacts"][field]):
                raise ValueError()
            if view["artifacts"][field] != binding[field]:
                raise ValueError()
        for field, expected in (("policy_sha256", policy_sha256),
                                ("roster_sha256", roster_sha256),
                                ("public_policy_sha256", public_policy_sha256)):
            if binding[field] != expected:
                raise ValueError()
        if (config.memory != binding["memory_root"]
                or config.descriptor != binding["descriptor_path"]
                or config.descriptor_sha256 != binding["descriptor_sha256"]):
            raise ValueError()  # Never replace caller-pinned config silently.
        read = pointer_resolver._read_matching
        descriptor_bytes = read(Path(binding["descriptor_path"]),
                                binding["descriptor_sha256"], limit=65536)
        descriptor = pointer_resolver.checkpoint._parse(descriptor_bytes)
        if (type(descriptor) is not dict
                or set(descriptor) != {"schema", "scope", "memory_sha256", "dim", "encoder"}
                or descriptor["schema"] != "wd.bridge-mind-q4-memory-descriptor.v2"
                or descriptor["scope"] != _SCOPE
                or type(descriptor["dim"]) is not int or descriptor["dim"] < 1
                or descriptor["memory_sha256"] != binding["memory_sha256"]
                or _encoder_digest(descriptor["encoder"]) != expected_encoder_sha256
                or descriptor["encoder"]["revision"] != config.runtime_revision):
            raise ValueError()
        memory_bytes = read(Path(binding["memory_root"]) / "memory.json",
                            binding["memory_sha256"], limit=_ARTIFACT_LIMIT)
        projection_bytes = read(Path(binding["projection_path"]),
                                binding["projection_sha256"], limit=_ARTIFACT_LIMIT)
        pointer_path = Path(root) / pointer_resolver.checkpoint.POINTER
        pointer_limit = pointer_resolver.checkpoint._MAX_POINTER_BYTES
        read(pointer_path, expected_pointer_sha256, limit=pointer_limit)
        # query_and_resolve strictly expects the full policy task set. The
        # public resolver separately enforces the public subset/type allowlist.
        result = q4_query.query_and_resolve(
            config, stdin, memory_bytes, projection_bytes, tasks,
            policy_bytes=policy_bytes, policy_sha256=policy_sha256,
            roster_bytes=roster_bytes, roster_sha256=roster_sha256,
            expected_memory_sha256=binding["memory_sha256"])
        if (type(result) is not dict or set(result) != {"status", "results"}
                or result["status"] not in ("ANSWER", "ABSTAIN", "DEGRADED")
                or type(result["results"]) is not list or len(result["results"]) > 100
                or (result["status"] == "ANSWER" and not result["results"])
                or (result["status"] == "ABSTAIN" and result["results"])):
            raise ValueError()
        clean = []
        for item in result["results"]:
            if (type(item) is not dict
                    or set(item) != _RESULT_KEYS | {"stored_text"}
                    or type(item["stored_text"]) is not str
                    or item["ref_kind"] != "sha256_of_projected_metadata"
                    or item["canonical_event"] is not False
                    or not all(pointer_resolver._pin(item[key])
                               for key in ("ref_sha256", "doc_id_sha256"))):
                raise ValueError()
            clean.append({key: item[key] for key in sorted(_RESULT_KEYS)})
        read(pointer_path, expected_pointer_sha256, limit=pointer_limit)
        output = {
            "schema": "wd.q4-public-query.v1", "status": result["status"],
            "results": clean,
            "binding": {
                "pointer_sha256": expected_pointer_sha256,
                "source_anchor_sha256": expected_source_sha256,
                "encoder_sha256": expected_encoder_sha256,
                "policy_sha256": policy_sha256, "roster_sha256": roster_sha256,
                "public_policy_sha256": public_policy_sha256,
                **{key: binding[key] for key in sorted(pointer_resolver._ARTIFACT_KEYS)},
            },
            "publisher_authentication": "UNKNOWN",
            "source_filter_attestation": "UNKNOWN", "freshness": "UNKNOWN",
            "same_user_aba": "UNKNOWN",
        }
        if len(json.dumps(output, ensure_ascii=True, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")) > q4_query.MAX_OUTPUT:
            raise ValueError()
        return output
    except Exception:
        raise PublicQueryRefused("public_query_refused") from None
