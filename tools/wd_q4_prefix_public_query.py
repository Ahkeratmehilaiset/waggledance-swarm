"""Default-off prefix-only Q4 reader, not a CLI or live connection.

The caller must independently authenticate every pin, publisher, source and
runtime. A prefix checkpoint does not prove freshness, source authenticity,
caller identity, same-user race resistance or complete earlier history. The
checkpoint's protected prefix is consumed only in-process, never returned.

``query_public_prefix`` is the production entry and the prefix twin of
``wd_q4_public_query.query_public``. It is OFF by default and, once enabled,
refuses ``secure_transport_unavailable`` BEFORE any pin is evaluated, any file
is read, stdin is consumed, any backend or launcher is reached or anything
native (FAISS, numpy) is deserialized: no verified secure transport exists yet.
The future transport must bind the trusted fixed-peer READY state and the
verified snapshot binding, and it lands as reviewed code on this path, never as
an argument, a flag or a process setting; no bool-only trust exists here.

``_synthetic_seam_query_public_prefix`` is a NON-PRODUCTION internal test seam.
It runs only with a constructed ``SyntheticInProcessBackend`` marker (a bool,
None or a bare callable refuses) and performs the full verified query order:
six caller pins (pointer, source anchor, encoder, policy, roster, public
policy) and the caller-asserted ``VerifiedQ4Config`` are validated; the strict
prefix pointer view (JSON) is bound to the source anchor pin and the descriptor
(JSON, read by its pinned digest) to the encoder pin and the runtime revision
BEFORE the resolver, the memory or the projection bytes are touched; the config
must name exactly that generation (never rebound); memory and projection are
read by digest and the pointer is re-read before and after the backend; only
then is stdin handed to the injected backend. In tests the backend is the
agent-query resolver with its launcher replaced by an explicitly labeled
in-process stand-in; the module itself never calls the launcher.

Public output carries the pointer, encoder and public-policy pins plus the four
artifact digests; the source anchor, policy and roster digests are verified but
withheld (listed by name under ``withheld``) because they commit to private
material. The honest ``UNKNOWN`` markers (publisher authentication, source
filter attestation, freshness, same-user ABA) are never upgraded because
digests match. Every refusal is a fixed code raised outside every handler, so
it carries no cause, no context and no inner frames. Nothing here supplies a
scheduler, transport, deployment, runtime tree or model authority.
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
_WITHHELD = ("policy_sha256", "roster_sha256", "source_anchor_sha256")


class PrefixPublicRefused(ValueError):
    """Fixed public refusal; no private paths, source bytes, prefix digests, cause or context."""


class SyntheticInProcessBackend:
    """NON-PRODUCTION INTERNAL TEST SEAM marker.

    Wraps the in-process backend a synthetic test injects (the agent-query resolver with its launcher replaced by a
    stand-in). Only tests construct it; the production entry accepts no such argument.
    """
    __slots__ = ("run",)

    def __init__(self, run):
        if run is None or type(run) is bool or not callable(run):
            raise TypeError("synthetic backend must wrap a callable")
        self.run = run


def _attempt(work):
    """The module's only handler: the caller raises its fixed refusal AFTER this handler closed."""
    try:
        return True, work()
    except Exception:
        return False, None


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


def _resolve(root, *, expected_pointer_sha256, policy_bytes, policy_sha256,
             roster_bytes, roster_sha256, public_policy_bytes, public_policy_sha256):
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


def resolve_prefix_public_pointer(root, *, expected_pointer_sha256, policy_bytes, policy_sha256,
                                  roster_bytes, roster_sha256, public_policy_bytes, public_policy_sha256,
                                  enabled=False):
    """Resolve a caller-anchored prefix checkpoint; no legacy fallback or public prefix."""
    if enabled is not True:
        raise PrefixPublicRefused("disabled")
    ok, binding = _attempt(lambda: _resolve(
        root, expected_pointer_sha256=expected_pointer_sha256, policy_bytes=policy_bytes,
        policy_sha256=policy_sha256, roster_bytes=roster_bytes, roster_sha256=roster_sha256,
        public_policy_bytes=public_policy_bytes, public_policy_sha256=public_policy_sha256))
    if not ok:
        raise PrefixPublicRefused("resolution_refused")
    return binding


def _encoder_digest(value):
    """Canonical JSON digest of the descriptor's three encoder fields; the caller pins it from the plan, not from disk."""
    if (type(value) is not dict or set(value) != _ENCODER_KEYS
            or any(type(value[key]) is not str or not value[key] for key in _ENCODER_KEYS)
            or not resolver._pin(value["weights_sha256"])):
        raise ValueError()
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode("utf-8")).hexdigest()


def _clean_results(result):
    """The backend's output reduced to its public shape; any other key, type, pin or oversize text refuses it all."""
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


def _verified_query(config, stdin, *, root, expected_pointer_sha256, expected_source_sha256,
                    expected_encoder_sha256, policy_bytes, policy_sha256, roster_bytes, roster_sha256,
                    public_policy_bytes, public_policy_sha256, backend):
    """The verified order shared by any future transport-backed entry; raises on every deviation."""
    pins = (expected_pointer_sha256, expected_source_sha256, expected_encoder_sha256,
            policy_sha256, roster_sha256, public_policy_sha256)
    if not all(resolver._pin(value) for value in pins):
        raise ValueError()
    if not all(type(value) is bytes for value in (policy_bytes, roster_bytes, public_policy_bytes)):
        raise ValueError()
    q4_query._validate_config(config)
    secure_root = checkpoint._secure(Path(root))
    tasks, _ = adapter.strict_pins(policy_bytes, policy_sha256, roster_bytes, roster_sha256)
    # 1. the strict prefix pointer view (JSON) bound to the caller's source anchor; the protected prefix pin
    #    stays inside _view
    view = _view(secure_root, tasks, pointer_sha256=expected_pointer_sha256,
                 policy_bytes=policy_bytes, policy_sha256=policy_sha256,
                 roster_bytes=roster_bytes, roster_sha256=roster_sha256,
                 public_policy_sha256=public_policy_sha256)
    if not hmac.compare_digest(view["anchor_sha256"], expected_source_sha256):
        raise ValueError()
    # 2. the descriptor (JSON, by its pinned digest) bound to the caller's encoder pin and runtime revision
    generation = checkpoint._secure(secure_root / view["generation"])
    descriptor_path = generation / "descriptor.json"
    read = resolver._read_matching
    descriptor = checkpoint._parse(read(descriptor_path, view["artifacts"]["descriptor_sha256"], limit=65536))
    if (type(descriptor) is not dict or set(descriptor) != _DESCRIPTOR_KEYS
            or descriptor["schema"] != "wd.bridge-mind-q4-memory-descriptor.v2" or descriptor["scope"] != _SCOPE
            or type(descriptor["dim"]) is not int or descriptor["dim"] < 1
            or descriptor["memory_sha256"] != view["artifacts"]["memory_sha256"]
            or not hmac.compare_digest(_encoder_digest(descriptor["encoder"]), expected_encoder_sha256)
            or descriptor["encoder"]["revision"] != config.runtime_revision):
        raise ValueError()
    # 3. the caller's config names exactly this generation; it is never rebound here
    if (config.memory != str(generation) or config.descriptor != str(descriptor_path)
            or config.descriptor_sha256 != view["artifacts"]["descriptor_sha256"]):
        raise ValueError()
    # 4. the public allowlist over the projection, cross-checked against the view
    binding = resolve_prefix_public_pointer(
        root, expected_pointer_sha256=expected_pointer_sha256, policy_bytes=policy_bytes,
        policy_sha256=policy_sha256, roster_bytes=roster_bytes, roster_sha256=roster_sha256,
        public_policy_bytes=public_policy_bytes, public_policy_sha256=public_policy_sha256, enabled=True)
    if (type(binding) is not dict or binding["generation"] != view["generation"]
            or binding["source_mode"] != "public-v1" or binding["pointer_sha256"] != expected_pointer_sha256
            or binding["memory_root"] != str(generation) or binding["descriptor_path"] != str(descriptor_path)
            or any(view["artifacts"][key] != binding[key] for key in resolver._ARTIFACT_KEYS)
            or binding["policy_sha256"] != policy_sha256 or binding["roster_sha256"] != roster_sha256
            or binding["public_policy_sha256"] != public_policy_sha256):
        raise ValueError()
    # 5. the artifacts by digest, the pointer re-read, then stdin reaches the injected backend
    memory_bytes = read(generation / "memory.json", binding["memory_sha256"], limit=_ARTIFACT_LIMIT)
    projection_bytes = read(Path(binding["projection_path"]), binding["projection_sha256"], limit=_ARTIFACT_LIMIT)
    pointer_path = secure_root / checkpoint.POINTER
    read(pointer_path, expected_pointer_sha256, limit=checkpoint._MAX_POINTER_BYTES)
    result = backend(config, stdin, memory_bytes, projection_bytes, tasks,
                     policy_bytes=policy_bytes, policy_sha256=policy_sha256,
                     roster_bytes=roster_bytes, roster_sha256=roster_sha256,
                     expected_memory_sha256=binding["memory_sha256"])
    clean = _clean_results(result)
    read(pointer_path, expected_pointer_sha256, limit=checkpoint._MAX_POINTER_BYTES)
    output = {
        "schema": SCHEMA, "status": result["status"], "results": clean,
        "binding": {
            "pointer_sha256": expected_pointer_sha256, "encoder_sha256": expected_encoder_sha256,
            "public_policy_sha256": public_policy_sha256,
            **{key: binding[key] for key in sorted(resolver._ARTIFACT_KEYS)},
        },
        "withheld": list(_WITHHELD),
        "publisher_authentication": "UNKNOWN", "source_filter_attestation": "UNKNOWN",
        "freshness": "UNKNOWN", "same_user_aba": "UNKNOWN",
    }
    if len(json.dumps(output, ensure_ascii=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")) > q4_query.MAX_OUTPUT:
        raise ValueError()
    return output


def _synthetic_seam_query_public_prefix(config, stdin, *, root, expected_pointer_sha256=None,
                                        expected_source_sha256=None, expected_encoder_sha256=None,
                                        policy_bytes=None, policy_sha256=None, roster_bytes=None,
                                        roster_sha256=None, public_policy_bytes=None, public_policy_sha256=None,
                                        synthetic_backend=None, enabled=False):
    """NON-PRODUCTION INTERNAL TEST SEAM: the verified query order over an injected in-process backend.

    Not wired to any CLI, child, outer, lane or transport. Runs only with a constructed SyntheticInProcessBackend;
    a flag, None or a bare callable refuses before anything is read.
    """
    if enabled is not True:
        raise PrefixPublicRefused("disabled")
    if type(synthetic_backend) is not SyntheticInProcessBackend:
        raise PrefixPublicRefused("synthetic_backend_required")
    ok, output = _attempt(lambda: _verified_query(
        config, stdin, root=root, expected_pointer_sha256=expected_pointer_sha256,
        expected_source_sha256=expected_source_sha256, expected_encoder_sha256=expected_encoder_sha256,
        policy_bytes=policy_bytes, policy_sha256=policy_sha256, roster_bytes=roster_bytes,
        roster_sha256=roster_sha256, public_policy_bytes=public_policy_bytes,
        public_policy_sha256=public_policy_sha256, backend=synthetic_backend.run))
    if not ok:
        raise PrefixPublicRefused("prefix_query_refused")
    return output


def query_public_prefix(config, stdin, *, root, expected_pointer_sha256=None, expected_source_sha256=None,
                        expected_encoder_sha256=None, policy_bytes=None, policy_sha256=None,
                        roster_bytes=None, roster_sha256=None, public_policy_bytes=None,
                        public_policy_sha256=None, enabled=False):
    """Production entry: OFF by default; enabled, it refuses before pins, files, stdin, backend or native code.

    No verified secure transport exists. When one does, it binds the fixed-peer READY state and the snapshot
    binding and lands here as reviewed code calling the verified order; no argument or flag opens this path.
    """
    if enabled is not True:
        raise PrefixPublicRefused("disabled")
    raise PrefixPublicRefused("secure_transport_unavailable")
