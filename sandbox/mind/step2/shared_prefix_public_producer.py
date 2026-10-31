"""Default-off strict-prefix public producer primitive; no scheduler or live wiring.

Every source, pin, prior pointer and encoder is independently caller-owned.
The prefix detects certain prior rewrites, not source authenticity, freshness,
complete earlier history, hostile same-user mutation or process identity. The
private checkpoint contains prefix digests and must be protected by the caller.
This primitive never reads live data unless explicitly invoked with caller paths,
never installs or deletes, and never includes a prefix digest in its report.
"""
from dataclasses import dataclass, field
import hashlib
import hmac
import math
from pathlib import Path
import re

from sandbox.mind.step2 import shared_bridge_snapshot as snapshot
from sandbox.mind.step2 import shared_capture_adapter as adapter
from sandbox.mind.step2 import shared_index_checkpoint as checkpoint
from sandbox.mind.step2 import shared_producer as producer
from sandbox.mind.step2 import shared_public_producer as public
from waggledance.core.bridge_log_reader import BridgeCursor, BridgePrefixPin, MAX_PREFIX_BYTES

_HEX = re.compile(r"[0-9a-f]{64}\Z")


class PrefixProducerRefused(ValueError):
    """Fixed token only, never source paths, events or private prefix bytes."""


@dataclass(frozen=True, slots=True)
class PrefixPublicRequest:
    config: object = field(repr=False)
    pins: object = field(repr=False)
    start_prefix: object = field(repr=False)
    max_prefix_bytes: int
    root: object = field(repr=False)
    generation: str
    expected_pointer_sha256: str | None
    encode: object = field(repr=False)
    identity: object = field(repr=False)
    dim: int
    timeout: float = 0.2


def _fail(reason):
    raise PrefixProducerRefused(reason) from None


def _pin(value):
    return type(value) is str and _HEX.fullmatch(value) is not None


def _validate(request):
    if (type(request) is not PrefixPublicRequest
            or type(request.config) is not producer.PublicSourceConfig
            or type(request.config.source) is not producer.SourceConfig
            or type(request.start_prefix) is not BridgePrefixPin
            or type(request.start_prefix.cursor) is not BridgeCursor
            or not _pin(request.start_prefix.sha256)
            or request.start_prefix.cursor != request.config.source.cursor
            or type(request.max_prefix_bytes) is not int or not 0 <= request.max_prefix_bytes <= MAX_PREFIX_BYTES
            or type(request.start_prefix.cursor.offset) is not int
            or not 0 <= request.start_prefix.cursor.offset <= request.max_prefix_bytes
            or not isinstance(request.root, (str, Path))
            or type(request.generation) is not str or checkpoint.NAME.fullmatch(request.generation) is None
            or (request.expected_pointer_sha256 is not None and not _pin(request.expected_pointer_sha256))
            or not callable(request.encode) or type(request.identity) is not dict
            or type(request.dim) is not int or request.dim < 1
            or type(request.timeout) not in (int, float) or not math.isfinite(request.timeout)
            or not 0 < request.timeout <= 30):
        _fail("request_invalid")
    source = request.config.source
    if (type(source.max_bytes) is not int or not 1 <= source.max_bytes <= snapshot.MAX_BYTES
            or type(source.max_rows) is not int or not 1 <= source.max_rows <= snapshot.MAX_ROWS):
        _fail("request_invalid")


def _bind(request):
    config, source, pins = request.config, request.config.source, request.pins
    if type(pins) is not public.CallerPins:
        _fail("pins_mismatch")
    for key, configured, raw in (("anchor_sha256", source.anchor_sha256, source.anchor_bytes),
                                  ("policy_sha256", source.policy_sha256, source.policy_bytes),
                                  ("roster_sha256", source.roster_sha256, source.roster_bytes),
                                  ("public_policy_sha256", config.public_policy_sha256, config.public_policy_bytes)):
        expected = getattr(pins, key)
        if (not _pin(expected) or not _pin(configured) or type(raw) is not bytes
                or not hmac.compare_digest(expected, configured)
                or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), expected)):
            _fail("pins_mismatch")


def _report(status, pointer_sha256, generation):
    if status not in ("PUBLISHED_GENESIS", "PUBLISHED_HISTORY", "EMPTY_ADVANCED", "EMPTY_NO_CHANGE") or not _pin(pointer_sha256):
        _fail("publication_state_unknown")
    return {"status": status, "pointer_sha256": pointer_sha256, "generation": generation,
            "publisher_authentication": "UNKNOWN", "source_authenticity": "UNKNOWN", "freshness": "UNKNOWN"}


def publish_public_prefix(*, enabled=False, request=None):
    """Process one prefix-bound synthetic-ready publication, never legacy fallback."""
    if type(enabled) is not bool:
        _fail("request_invalid")
    if not enabled:
        return {"status": "OFF"}
    _validate(request)
    _bind(request)
    config, source, pins = request.config, request.config.source, request.pins
    try:
        tasks, _ = adapter.strict_pins(source.policy_bytes, pins.policy_sha256, source.roster_bytes, pins.roster_sha256)
        adapter.public_status_policy(config.public_policy_bytes, pins.public_policy_sha256, tasks)
        root = Path(request.root)
        previous = None
        if request.expected_pointer_sha256 is not None:
            previous, previous_prefix = checkpoint.read_verified_prefix_pointer(root, tasks,
                expected_pointer_sha256=request.expected_pointer_sha256, policy_bytes=source.policy_bytes,
                policy_sha256=pins.policy_sha256, roster_bytes=source.roster_bytes,
                roster_sha256=pins.roster_sha256, expected_public_policy_sha256=pins.public_policy_sha256)
            if type(previous_prefix) is not BridgePrefixPin or previous_prefix != request.start_prefix:
                raise ValueError()
        result = snapshot.read_anchored_delta(source.source, source.cursor, tasks, anchor_bytes=source.anchor_bytes,
            anchor_sha256=pins.anchor_sha256, generation_path=source.generation_path,
            max_bytes=source.max_bytes, max_rows=source.max_rows, policy_bytes=source.policy_bytes,
            policy_sha256=pins.policy_sha256, roster_bytes=source.roster_bytes, roster_sha256=pins.roster_sha256,
            public_policy_bytes=config.public_policy_bytes, public_policy_sha256=pins.public_policy_sha256,
            expected_prefix_sha256=request.start_prefix.sha256, max_prefix_bytes=request.max_prefix_bytes)
        projection = result["projection"]
        if snapshot.verify_anchored_projection(result, projection, expected_anchor_sha256=pins.anchor_sha256,
            expected_policy_sha256=pins.policy_sha256, expected_roster_sha256=pins.roster_sha256,
            expected_mode="public-v1", expected_public_policy_sha256=pins.public_policy_sha256,
            expected_anchor_bytes=source.anchor_bytes, expected_start_prefix=request.start_prefix) is not True:
            raise ValueError()
        if type(projection) is not dict or type(projection.get("records")) is not list:
            raise ValueError()
    except Exception:
        _fail("prepare_refused")
    common = dict(policy_bytes=source.policy_bytes, expected_policy_sha256=pins.policy_sha256,
                  roster_bytes=source.roster_bytes, expected_roster_sha256=pins.roster_sha256,
                  expected_anchor_sha256=pins.anchor_sha256, expected_mode="public-v1",
                  expected_public_policy_sha256=pins.public_policy_sha256,
                  public_policy_bytes=config.public_policy_bytes, expected_anchor_bytes=source.anchor_bytes,
                  timeout=request.timeout, expected_start_prefix=request.start_prefix)
    if not projection["records"]:
        if previous is None:
            _fail("empty_genesis")
        try:
            advanced = checkpoint.advance_prefix_empty(root, result, tasks,
                expected_pointer_sha256=request.expected_pointer_sha256, **common)
            if type(advanced) is not dict or set(advanced) != {"status", "pointer_sha256"}:
                raise ValueError()
            if advanced["status"] == "no_change" and advanced["pointer_sha256"] == request.expected_pointer_sha256:
                return _report("EMPTY_NO_CHANGE", advanced["pointer_sha256"], None)
            if advanced["status"] != "advanced":
                raise ValueError()
            return _report("EMPTY_ADVANCED", advanced["pointer_sha256"], None)
        except Exception:
            _fail("publication_state_unknown")
    try:
        from sandbox.mind.step2 import shared_index_builder as builder
        predecessor = None
        if previous is not None:
            prior = previous["artifacts"]
            predecessor = {"memory_dir": str(root / previous["generation"]), "memory_sha256": prior["memory_sha256"],
                "projection_sha256": prior["projection_sha256"], "build_receipt_sha256": prior["receipt_sha256"],
                "watermark": {"root": str(root), "pointer_sha256": request.expected_pointer_sha256}}
        build_source = {"snapshot_result": result, "expected_anchor_sha256": pins.anchor_sha256,
            "mode": "public-v1", "public_policy_bytes": config.public_policy_bytes,
            "public_policy_sha256": pins.public_policy_sha256, "anchor_bytes": source.anchor_bytes,
            "expected_start_prefix": request.start_prefix}
        built = builder.build_memory(projection, request.encode, request.identity, request.dim, root, request.generation,
            exclude_review_signal=True, pins={"policy_bytes": source.policy_bytes, "policy_sha256": pins.policy_sha256,
                "roster_bytes": source.roster_bytes, "roster_sha256": pins.roster_sha256},
            source=build_source, predecessor=predecessor)
        artifacts = {"descriptor_sha256": built["descriptor_sha256"], "memory_sha256": built["memory_sha256"],
                     "projection_sha256": built["projection_sha256"], "receipt_sha256": built["build_receipt_sha256"]}
    except Exception:
        _fail("build_refused")
    try:
        if previous is None:
            receipt = checkpoint.commit_prefix_genesis(root, request.generation, source.cursor,
                result["candidate_cursor"], tasks, expected_pointer_sha256=None, expected_artifacts=artifacts,
                initial_cursor=source.cursor, snapshot_result=result, **common)
            status = "PUBLISHED_GENESIS"
        else:
            receipt = checkpoint.commit_prefix_history(root, request.generation, source.cursor,
                result["candidate_cursor"], tasks, snapshot_result=result,
                expected_pointer_sha256=request.expected_pointer_sha256, expected_artifacts=artifacts, **common)
            status = "PUBLISHED_HISTORY"
        if type(receipt) is not dict or set(receipt) != {"status", "pointer_sha256"} or receipt["status"] != "committed":
            raise ValueError()
        return _report(status, receipt["pointer_sha256"], request.generation)
    except Exception:
        _fail("publication_state_unknown")
