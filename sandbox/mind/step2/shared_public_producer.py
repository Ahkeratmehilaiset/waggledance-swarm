"""Default-off synthetic public-v1 producer primitive: ONE function, caller-bound trust, no live wiring.

``publish_public_generation`` wires the EXISTING pieces in their fixed order and adds nothing of its own:
``shared_producer.prepare_public_delta`` (the Lead strict public snapshot + its in-process verifier), the public-v1
``shared_index_builder.build_memory`` (source mode persisted, legacy/public series never mixed) and the Lead
``shared_index_checkpoint`` genesis / empty advance / history commit under its writer lock. Every input is the
caller's: source path, generation sidecar, cursor, anchor bytes, policy and roster pins, public policy, previous
pointer digest + predecessor artifacts, root and encoder. The caller's pins are a SEPARATE record that must agree
with the configuration and with the bytes it names; a disagreement or an absent pin refuses before anything is read.

What this primitive does NOT do (Lead GO lead-mind-public-producer-fable-20261031-v1 + clarification 20:18:14Z):
it never verifies publisher identity or source authenticity (both are reported ``UNKNOWN``, never invented and
never accepted as authenticated because hashes match); it reads no bridge events itself, has no scheduler,
installer, activation, ACL, key or runtime effect, moves no model and touches no real data. It is OFF by default,
produces public-v1 generations only (a legacy predecessor refuses inside the builder), never deletes: when the
checkpoint refuses after the builder has published a directory, that directory is left for the caller to inspect.
"""
import hashlib
import hmac
import re
from dataclasses import dataclass, field
from pathlib import Path

from sandbox.mind.step2 import shared_capture_adapter as adapter
from sandbox.mind.step2 import shared_index_builder as builder
from sandbox.mind.step2 import shared_index_checkpoint as checkpoint
from sandbox.mind.step2 import shared_producer as producer

HEX64 = re.compile(r"[0-9a-f]{64}")
STATUS_MODE = "public-v1"
UNKNOWN = "UNKNOWN"
STATUSES = frozenset({"OFF", "PUBLISHED_GENESIS", "PUBLISHED_HISTORY", "EMPTY_ADVANCED", "EMPTY_NO_CHANGE"})
REFUSALS = frozenset({"request_invalid", "pins_absent", "pins_mismatch", "prepare_refused", "empty_genesis",
                      "build_refused", "checkpoint_refused", "publication_state_unknown"})
REPORT_KEYS = frozenset({"status", "generation", "pointer_sha256", "artifacts", "records", "source_mode",
                         "publisher_identity", "source_authenticity", "freshness", "authenticated", "pins_bound"})


class PublicProducerRefused(ValueError):
    """Fixed, data-free refusal token (one of ``REFUSALS``)."""


@dataclass(frozen=True, slots=True)
class CallerPins:
    """The caller's independently supplied digests; they must agree with the configuration AND with its bytes."""
    policy_sha256: str
    roster_sha256: str
    public_policy_sha256: str
    anchor_sha256: str


@dataclass(frozen=True, slots=True)
class Predecessor:
    """The caller's pinned previous generation: the verified pointer digest it is published under + its artifacts."""
    pointer_sha256: str
    memory_dir: object = field(repr=False)
    memory_sha256: str = ""
    projection_sha256: str = ""
    build_receipt_sha256: str = ""


@dataclass(frozen=True, slots=True)
class PublicPublishRequest:
    """Everything the primitive needs, all caller-owned; ``predecessor is None`` means a genesis publication."""
    config: object = field(repr=False)  # producer.PublicSourceConfig
    pins: object  # CallerPins
    root: object = field(repr=False)
    generation: str
    encode: object = field(repr=False)
    identity: dict = field(repr=False)
    dim: int
    predecessor: object = None  # Predecessor | None
    timeout: float = 0.2


def _refuse(token):
    raise PublicProducerRefused(token) from None


def _hex(value):
    return isinstance(value, str) and HEX64.fullmatch(value) is not None


def _digest_of(data, digest):
    return type(data) is bytes and _hex(digest) and hmac.compare_digest(hashlib.sha256(data).hexdigest(), digest)


def _validate_request(request):
    if (type(request) is not PublicPublishRequest or type(request.config) is not producer.PublicSourceConfig
            or type(request.config.source) is not producer.SourceConfig
            or not isinstance(request.root, (str, Path)) or not isinstance(request.generation, str)
            or builder.MEMORY_NAME.fullmatch(request.generation) is None or not callable(request.encode)
            or not isinstance(request.identity, dict) or type(request.dim) is not int
            or not isinstance(request.timeout, (int, float)) or isinstance(request.timeout, bool)
            or request.timeout <= 0
            or (request.predecessor is not None and type(request.predecessor) is not Predecessor)):
        _refuse("request_invalid")
    predecessor = request.predecessor
    if predecessor is not None and (
            not _hex(predecessor.pointer_sha256) or not isinstance(predecessor.memory_dir, (str, Path))
            or not all(_hex(getattr(predecessor, key)) for key in ("memory_sha256", "projection_sha256", "build_receipt_sha256"))):
        _refuse("request_invalid")


def _bind_pins(request):
    """The caller's pins must name the configuration's digests exactly AND each digest must be the digest of the
    bytes the configuration carries. Agreement is integrity checking of what the caller supplied, never authenticity."""
    pins, config, source = request.pins, request.config, request.config.source
    if pins is None:
        _refuse("pins_absent")
    if type(pins) is not CallerPins or not all(_hex(getattr(pins, key)) for key in
                                                ("policy_sha256", "roster_sha256", "public_policy_sha256", "anchor_sha256")):
        _refuse("pins_mismatch")
    pairs = ((pins.policy_sha256, source.policy_sha256, source.policy_bytes),
             (pins.roster_sha256, source.roster_sha256, source.roster_bytes),
             (pins.public_policy_sha256, config.public_policy_sha256, config.public_policy_bytes),
             (pins.anchor_sha256, source.anchor_sha256, source.anchor_bytes))
    for caller, configured, data in pairs:
        if not _hex(configured) or not hmac.compare_digest(caller, configured) or not _digest_of(data, caller):
            _refuse("pins_mismatch")


def _report(status, *, generation, pointer_sha256, artifacts, records, public_policy_sha256):
    """``freshness`` is UNKNOWN by construction: ``pointer_sha256`` is the pointer the checkpoint published under its
    writer lock for THIS call; whether it is still the live pointer when the caller reads the report is not known."""
    report = {"status": status, "generation": generation, "pointer_sha256": pointer_sha256, "artifacts": artifacts,
              "records": records, "source_mode": {"status_mode": STATUS_MODE, "public_policy_sha256": public_policy_sha256},
              "publisher_identity": UNKNOWN, "source_authenticity": UNKNOWN, "freshness": UNKNOWN,
              "authenticated": False, "pins_bound": True}
    if set(report) != REPORT_KEYS or status not in STATUSES:
        _refuse("request_invalid")
    return report


def _locked_pointer_digest(value, generation, artifacts):
    """The receipt digest comes from the EXACT pointer value the checkpoint returned while it held the writer lock,
    serialized with the checkpoint's own canonical form (the bytes it published). A later root read could name a
    pointer another publication replaced ours with, so no file is read here. The value must name OUR generation and
    artifacts, or the receipt binds nothing; publication state must then be checked independently."""
    if (not isinstance(value, dict) or value.get("generation") != generation or value.get("artifacts") != artifacts
            or not isinstance(value.get("schema"), str)):
        raise ValueError()
    digest = checkpoint._sha(checkpoint._canonical(value))
    if not _hex(digest):
        raise ValueError()
    return digest


def publish_public_generation(*, enabled=False, request=None):
    """OFF by default. Enabled: bind the caller's pins, prepare ONE public-v1 delta (Lead strict chain), then either
    advance the checkpoint on a strict empty delta (predecessor required; an unmoved cursor is ``EMPTY_NO_CHANGE``,
    a verified repeat and not a publication), or build the public generation and commit it as genesis (no
    predecessor) or as history (predecessor + verified watermark). The receipt's ``pointer_sha256`` is derived from
    the pointer value the checkpoint returned under its lock, never from a later root read. Returns the closed report
    (``REPORT_KEYS``) with ``publisher_identity``, ``source_authenticity`` and ``freshness`` always ``UNKNOWN``;
    refuses with one fixed token (``REFUSALS``), never text. Nothing is deleted on any path."""
    if type(enabled) is not bool:
        _refuse("request_invalid")
    if not enabled:
        return {"status": "OFF"}
    _validate_request(request)
    _bind_pins(request)
    config, source, pins = request.config, request.config.source, request.pins
    try:
        prepared = producer.prepare_public_delta(enabled=True, config=config)
        if type(prepared) is not producer.PreparedDelta:
            raise ValueError()
        tasks, _ = adapter.strict_pins(source.policy_bytes, source.policy_sha256, source.roster_bytes, source.roster_sha256)
    except Exception:  # noqa: BLE001 - ProducerRefused or any shape failure: one token, no text
        _refuse("prepare_refused")
    result = prepared.result
    projection = result["projection"]
    root = Path(request.root)
    public = {"expected_mode": STATUS_MODE, "expected_public_policy_sha256": pins.public_policy_sha256,
              "public_policy_bytes": config.public_policy_bytes, "expected_anchor_bytes": source.anchor_bytes}
    checkpoint_pins = {"policy_bytes": source.policy_bytes, "expected_policy_sha256": pins.policy_sha256,
                       "roster_bytes": source.roster_bytes, "expected_roster_sha256": pins.roster_sha256,
                       "expected_anchor_sha256": pins.anchor_sha256, "timeout": request.timeout}
    predecessor = request.predecessor
    if not isinstance(projection, dict) or not isinstance(projection.get("records"), list):
        _refuse("prepare_refused")
    if not projection["records"]:
        if predecessor is None:
            _refuse("empty_genesis")  # an empty delta is a pointer event, never a first generation
        try:
            advanced = checkpoint.advance_empty_delta(root, result, tasks, expected_pointer_sha256=predecessor.pointer_sha256,
                                                      **checkpoint_pins, **public)
            if not isinstance(advanced, dict) or not _hex(advanced.get("pointer_sha256")):
                raise ValueError()
            if advanced.get("status") == "advanced":
                status = "EMPTY_ADVANCED"
            elif (advanced.get("status") == "no_change"
                  and hmac.compare_digest(advanced["pointer_sha256"], predecessor.pointer_sha256)):
                status = "EMPTY_NO_CHANGE"  # the cursor did not move: a verified repeat, NOT a new publication
            else:
                raise ValueError()
        except Exception:  # noqa: BLE001 - an exception does not prove that the pointer was not updated
            _refuse("publication_state_unknown")
        return _report(status, generation=None, pointer_sha256=advanced["pointer_sha256"], artifacts=None,
                       records=0, public_policy_sha256=pins.public_policy_sha256)
    build_source = {"snapshot_result": result, "expected_anchor_sha256": pins.anchor_sha256, "mode": STATUS_MODE,
                    "public_policy_bytes": config.public_policy_bytes, "public_policy_sha256": pins.public_policy_sha256,
                    "anchor_bytes": source.anchor_bytes}
    builder_pins = {"policy_bytes": source.policy_bytes, "policy_sha256": pins.policy_sha256,
                    "roster_bytes": source.roster_bytes, "roster_sha256": pins.roster_sha256}
    builder_predecessor = None
    if predecessor is not None:
        builder_predecessor = {"memory_dir": predecessor.memory_dir, "memory_sha256": predecessor.memory_sha256,
                               "projection_sha256": predecessor.projection_sha256,
                               "build_receipt_sha256": predecessor.build_receipt_sha256,
                               "watermark": {"root": root, "pointer_sha256": predecessor.pointer_sha256}}
    try:
        built = builder.build_memory(projection, request.encode, request.identity, request.dim, root, request.generation,
                                     exclude_review_signal=True, pins=builder_pins, source=build_source,
                                     predecessor=builder_predecessor)
    except Exception:  # noqa: BLE001 - BuildRefused (incl. predecessor_mode_mismatch for a legacy series): one token
        _refuse("build_refused")
    artifacts = {"descriptor_sha256": built["descriptor_sha256"], "memory_sha256": built["memory_sha256"],
                 "projection_sha256": built["projection_sha256"], "receipt_sha256": built["build_receipt_sha256"]}
    start, end = source.cursor, result["candidate_cursor"]
    try:
        if predecessor is None:
            value = checkpoint.commit_generation(root, request.generation, start, end, tasks, expected_pointer_sha256=None,
                                                 expected_artifacts=artifacts, initial_cursor=start, snapshot_result=result,
                                                 **checkpoint_pins, **public)
            status = "PUBLISHED_GENESIS"
        else:
            value = checkpoint.commit_history_generation(root, request.generation, start, end, tasks, snapshot_result=result,
                                                         expected_pointer_sha256=predecessor.pointer_sha256,
                                                         expected_artifacts=artifacts, **checkpoint_pins, **public)
            status = "PUBLISHED_HISTORY"
        pointer_sha256 = _locked_pointer_digest(value, request.generation, artifacts)  # the locked value, no root read
    except Exception:  # noqa: BLE001 - the pointer may already be published; re-check before retrying
        _refuse("publication_state_unknown")
    return _report(status, generation=request.generation, pointer_sha256=pointer_sha256, artifacts=artifacts,
                   records=built["build_receipt"]["records"], public_policy_sha256=pins.public_policy_sha256)
