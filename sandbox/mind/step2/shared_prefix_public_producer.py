"""Default-off strict-prefix public producer primitive; no scheduler or live wiring.

Every source, pin, prior pointer and encoder is independently caller-owned.
The prefix detects certain prior rewrites, not source authenticity, freshness,
complete earlier history, hostile same-user mutation or process identity. The
private checkpoint contains prefix digests and must be protected by the caller.
This primitive never reads live data unless explicitly invoked with caller paths,
never installs or deletes, and never includes a prefix digest in its report.
request.fenced=True (opt-in, default off) publishes through the private intent and
the checkpoint fence; only a resolved "landed" reports, any Exception reports
publication_state_unknown. KeyboardInterrupt/SystemExit propagate unchanged: the
caller quarantines both roots and never retries (states in _publish_fenced). It
fences cooperating writers only.
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
from sandbox.mind.step2 import prefix_private_state as private_state
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
    private_state_root: object = field(default=None, repr=False)
    fenced: bool = False


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
            or not 0 < request.timeout <= 30
            or type(request.fenced) is not bool):
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
    try:
        with private_state.locked_state(request.private_state_root, request.root,
                                        enabled=True, timeout=request.timeout) as state:
            state.require(request.expected_pointer_sha256, request.start_prefix)
            if request.expected_pointer_sha256 is None:
                pointer = checkpoint._secure(Path(request.root) / checkpoint.POINTER, missing_leaf=True)
                if pointer.exists():
                    raise private_state.StateRefused("private_state_refused")
            return _publish_locked(request, state)
    except PrefixProducerRefused:
        raise
    except Exception:
        pass
    _fail("private_state_refused")


def publish_public_catchup(*, enabled=False, request=None, max_windows=None):
    """Publish a complete bounded catch-up as one deduplicated public delta.

    OFF by default. A partial chain or empty genesis refuses without advancing.
    Caught up means the last observed snapshot end, not complete prior history
    or currentness after reading. Source/refusal counts are not aggregated.
    Caller owns source, pins, model and private prefix state; this is not live
    wiring or source authentication. A failed build can leave an orphan folder.
    """
    state = pointer = token = None
    reason = "private_state_refused"
    try:
        if type(enabled) is not bool:
            _fail("request_invalid")
        if not enabled:
            return {"status": "OFF"}
        _validate(request)
        _bind(request)
        if type(max_windows) is not int or not 1 <= max_windows <= snapshot.CATCHUP_MAX_WINDOWS:
            _fail("request_invalid")
        with private_state.locked_state(request.private_state_root, request.root,
                                        enabled=True, timeout=request.timeout) as state:
            state.require(request.expected_pointer_sha256, request.start_prefix)
            if request.expected_pointer_sha256 is None:
                pointer = checkpoint._secure(Path(request.root) / checkpoint.POINTER, missing_leaf=True)
                if pointer.exists():
                    raise private_state.StateRefused("private_state_refused")
            return _publish_locked_catchup(request, state, max_windows)
    except PrefixProducerRefused as refused:
        token = str(refused)
        if token in {"request_invalid", "pins_mismatch", "catchup_incomplete", "prepare_refused",
                     "empty_catchup_unsupported", "empty_genesis", "build_refused", "publication_state_unknown",
                     "private_state_refused"}:
            reason = token
    except Exception:
        pass
    finally:
        # Erase this API boundary's inputs even on KeyboardInterrupt. Nested
        # backend frames and native stdout/stderr are not covered by this.
        del request, enabled, max_windows, state, pointer, token
    _fail(reason)


def _publish_locked_catchup(request, state, max_windows):
    config = source = pins = tasks = previous = previous_prefix = chain = None
    catchup_source = projection = frozen = response = None
    try:
        config, source, pins = request.config, request.config.source, request.pins
        from sandbox.mind.step2 import shared_index_builder as builder
        tasks = adapter.strict_pins(source.policy_bytes, pins.policy_sha256, source.roster_bytes, pins.roster_sha256)[0]
        adapter.public_status_policy(config.public_policy_bytes, pins.public_policy_sha256, tasks)
        previous = None
        if request.expected_pointer_sha256 is not None:
            previous, previous_prefix = checkpoint.read_verified_prefix_pointer(Path(request.root), tasks,
                expected_pointer_sha256=request.expected_pointer_sha256, policy_bytes=source.policy_bytes,
                policy_sha256=pins.policy_sha256, roster_bytes=source.roster_bytes,
                roster_sha256=pins.roster_sha256, expected_public_policy_sha256=pins.public_policy_sha256)
            if previous_prefix != request.start_prefix:
                raise ValueError()
        chain = snapshot.read_public_catchup(source.source, source.cursor, tasks, anchor_bytes=source.anchor_bytes,
            anchor_sha256=pins.anchor_sha256, generation_path=source.generation_path,
            max_bytes=source.max_bytes, max_rows=source.max_rows, max_windows=max_windows,
            policy_bytes=source.policy_bytes, policy_sha256=pins.policy_sha256,
            roster_bytes=source.roster_bytes, roster_sha256=pins.roster_sha256,
            public_policy_bytes=config.public_policy_bytes, public_policy_sha256=pins.public_policy_sha256,
            expected_prefix_sha256=request.start_prefix.sha256, max_prefix_bytes=request.max_prefix_bytes, enabled=True)
        if chain.get("caught_up") is not True:
            _fail("catchup_incomplete")
        catchup_source = {"catchup_result": chain, "expected_anchor_sha256": pins.anchor_sha256,
            "mode": "public-catchup-v1", "public_policy_bytes": config.public_policy_bytes,
            "public_policy_sha256": pins.public_policy_sha256, "anchor_bytes": source.anchor_bytes,
            "expected_start_prefix": request.start_prefix}
        projection, frozen = builder.verified_catchup_projection(catchup_source, policy_bytes=source.policy_bytes,
            policy_sha256=pins.policy_sha256, roster_bytes=source.roster_bytes, roster_sha256=pins.roster_sha256)
        # Own a frozen copy so an external mutable result cannot be swapped
        # between the builder and the repeated lock-held checkpoint checks.
        catchup_source["catchup_result"] = frozen
    except PrefixProducerRefused:
        raise
    except Exception:
        pass
    else:
        response = _publish_prepared(request, state, frozen, projection, tasks, previous, catchup_source=catchup_source)
        return {**response, "coverage": "caught_up_to_final_snapshot_not_history", "source_refusals": "NOT_AGGREGATED"}
    finally:
        del request, state, max_windows, config, source, pins, tasks, previous, previous_prefix, chain
        del catchup_source, projection, frozen, response
    _fail("prepare_refused")


def _publish_locked(request, state):
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
        pass
    else:
        return _publish_prepared(request, state, result, projection, tasks, previous)
    _fail("prepare_refused")


def _publish_prepared(request, state, result, projection, tasks, previous, *, catchup_source=None):
    config, source, pins = request.config, request.config.source, request.pins
    root = Path(request.root)
    common = dict(policy_bytes=source.policy_bytes, expected_policy_sha256=pins.policy_sha256,
                  roster_bytes=source.roster_bytes, expected_roster_sha256=pins.roster_sha256,
                  expected_anchor_sha256=pins.anchor_sha256, expected_mode="public-v1",
                  expected_public_policy_sha256=pins.public_policy_sha256,
                  public_policy_bytes=config.public_policy_bytes, expected_anchor_bytes=source.anchor_bytes,
                  timeout=request.timeout, expected_start_prefix=request.start_prefix)
    if not projection["records"]:
        if catchup_source is not None:
            _fail("empty_catchup_unsupported")
        if previous is None:
            _fail("empty_genesis")
        if request.fenced:
            try:
                candidate = result["candidate_prefix"]
                args = (root, result, tasks)
                kwargs = dict(expected_pointer_sha256=request.expected_pointer_sha256, **common)
                prepared = checkpoint.prepare_prefix_empty(*args, enabled=True, **kwargs)
                if type(prepared) is not dict or set(prepared) != {"status", "attempt_sha256"}:
                    raise ValueError()
                if prepared["status"] == "no_change" and prepared["attempt_sha256"] == request.expected_pointer_sha256:
                    return _report("EMPTY_NO_CHANGE", prepared["attempt_sha256"], None)  # nothing opened
                if prepared["status"] != "prepared":
                    raise ValueError()
            except Exception:
                pass
            else:
                return _publish_fenced(request, state, prepared["attempt_sha256"], candidate, "EMPTY_ADVANCED", None,
                                       checkpoint.advance_prefix_empty, args, kwargs)
            _fail("publication_state_unknown")
        try:
            candidate = result["candidate_prefix"]
            state.begin(request.expected_pointer_sha256)
            advanced = checkpoint.advance_prefix_empty(root, result, tasks,
                expected_pointer_sha256=request.expected_pointer_sha256, **common)
            if type(advanced) is not dict or set(advanced) != {"status", "pointer_sha256"}:
                raise ValueError()
            if advanced["status"] == "no_change" and advanced["pointer_sha256"] == request.expected_pointer_sha256:
                state.finish()
                return _report("EMPTY_NO_CHANGE", advanced["pointer_sha256"], None)
            if advanced["status"] != "advanced":
                raise ValueError()
            state.commit(request.expected_pointer_sha256, advanced["pointer_sha256"], candidate)
            return _report("EMPTY_ADVANCED", advanced["pointer_sha256"], None)
        except Exception:
            pass
        _fail("publication_state_unknown")
    try:
        from sandbox.mind.step2 import shared_index_builder as builder
        predecessor = None
        if previous is not None:
            prior = previous["artifacts"]
            predecessor = {"memory_dir": str(root / previous["generation"]), "memory_sha256": prior["memory_sha256"],
                "projection_sha256": prior["projection_sha256"], "build_receipt_sha256": prior["receipt_sha256"],
                "watermark": {"root": str(root), "pointer_sha256": request.expected_pointer_sha256}}
        build_source = catchup_source if catchup_source is not None else {"snapshot_result": result, "expected_anchor_sha256": pins.anchor_sha256,
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
        pass
    else:
        return _commit_prepared(request, state, result, tasks, previous, common, artifacts, root, source,
                                catchup_source=catchup_source)
    _fail("build_refused")


def _publish_fenced(request, state, attempt, candidate, status, generation, commit, args, kwargs):
    """Opt-in fenced route: durable intent + fence for exactly prior -> attempt, fenced commit, resolution.

    The commit result is not trusted: resolution under the checkpoint lock decides. Only "landed" reports.
    An Exception reports publication_state_unknown, never retried here:
    - from begin_intent: the fence it may have opened for exactly this binding is closed as not_landed (that
      attempt can never land afterwards; a fence of another binding is never touched); a slot it may have
      created keeps the private root pending;
    - from the commit: resolution still runs; "not_landed" removes the fence, KEEPS the intent and leaves the
      pointer unfenced, so a later writer can move it and strand the intent (resolution then refuses);
      "unknown" or a refused resolution keeps what is there.
    KeyboardInterrupt, SystemExit and every other non-Exception propagate unchanged with no cleanup. The caller
    must quarantine both roots as publication unknown and never retry; the persisted state is then one of: an
    open fence with no intent (interrupted inside begin_intent: every cooperating writer refuses until it is
    resolved with the fence's own binding), fence + intent (before or inside the commit: resolve_intent decides
    later), or a recorded outcome with the intent kept (inside resolution: a rerun reaches the same outcome).
    Older unfenced writers are not stopped by the fence.
    """
    outcome = None
    try:
        if not _pin(attempt):
            raise ValueError()
        state.begin_intent(request.expected_pointer_sha256, request.start_prefix, attempt, candidate,
                           enabled=True, fence=True)
    except Exception:
        try:
            checkpoint.resolve_prefix_fence(request.root, expected_prior_pointer_sha256=request.expected_pointer_sha256,
                                            expected_attempt_sha256=attempt, enabled=True, timeout=request.timeout)
        except Exception:
            pass  # no fence of exactly this binding, or a refusal: nothing changed
    else:
        try:
            commit(*args, expected_attempt_sha256=attempt, fenced=True, **kwargs)
        except Exception:
            pass  # landed or not is decided only by the resolution below
        try:
            outcome = state.resolve_intent(enabled=True)
        except Exception:
            pass
        if outcome == "landed":
            return _report(status, attempt, generation)
    _fail("publication_state_unknown")


def _commit_prepared(request, state, result, tasks, previous, common, artifacts, root, source, *, catchup_source=None):
    if request.fenced:
        try:
            candidate = result["candidate_prefix"]
            extra = {"_catchup_source": catchup_source} if catchup_source is not None else {}
            args = (root, request.generation, source.cursor, result["candidate_cursor"], tasks)
            if previous is None:
                kwargs = dict(expected_pointer_sha256=None, expected_artifacts=artifacts, initial_cursor=source.cursor,
                              snapshot_result=result, **common, **extra)
                prepare, commit, status = checkpoint.prepare_prefix_genesis, checkpoint.commit_prefix_genesis, "PUBLISHED_GENESIS"
            else:
                kwargs = dict(snapshot_result=result, expected_pointer_sha256=request.expected_pointer_sha256,
                              expected_artifacts=artifacts, **common, **extra)
                prepare, commit, status = checkpoint.prepare_prefix_history, checkpoint.commit_prefix_history, "PUBLISHED_HISTORY"
            prepared = prepare(*args, enabled=True, **kwargs)
            if type(prepared) is not dict or set(prepared) != {"status", "attempt_sha256"} or prepared["status"] != "prepared":
                raise ValueError()
        except Exception:
            pass
        else:
            return _publish_fenced(request, state, prepared["attempt_sha256"], candidate, status, request.generation,
                                   commit, args, kwargs)
        _fail("publication_state_unknown")
    try:
        candidate = result["candidate_prefix"]
        state.begin(request.expected_pointer_sha256)
        if previous is None:
            extra = {"_catchup_source": catchup_source} if catchup_source is not None else {}
            receipt = checkpoint.commit_prefix_genesis(root, request.generation, source.cursor,
                result["candidate_cursor"], tasks, expected_pointer_sha256=None, expected_artifacts=artifacts,
                initial_cursor=source.cursor, snapshot_result=result, **common, **extra)
            status = "PUBLISHED_GENESIS"
        else:
            extra = {"_catchup_source": catchup_source} if catchup_source is not None else {}
            receipt = checkpoint.commit_prefix_history(root, request.generation, source.cursor,
                result["candidate_cursor"], tasks, snapshot_result=result,
                expected_pointer_sha256=request.expected_pointer_sha256, expected_artifacts=artifacts, **common, **extra)
            status = "PUBLISHED_HISTORY"
        if type(receipt) is not dict or set(receipt) != {"status", "pointer_sha256"} or receipt["status"] != "committed":
            raise ValueError()
        state.commit(request.expected_pointer_sha256, receipt["pointer_sha256"], candidate)
        return _report(status, receipt["pointer_sha256"], request.generation)
    except Exception:
        pass
    _fail("publication_state_unknown")
