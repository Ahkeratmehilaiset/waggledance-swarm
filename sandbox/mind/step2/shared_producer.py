"""Default-off synthetic preparation for the future shared-index producer.

The caller must authenticate source, cursor, policy, roster and all hashes.
Matching bytes and hashes do not establish their authenticity. Only one
bounded, in-memory delta is prepared; publication is deliberately unavailable
until history, builder source binding and atomic checkpoint contracts agree.
No scheduler, CLI, installation or live-capture writer is provided here.
"""
from dataclasses import dataclass, field

from sandbox.mind.step2 import shared_bridge_snapshot as snapshot
from sandbox.mind.step2 import shared_capture_adapter as adapter


class ProducerRefused(ValueError):
    """Fixed, data-free refusal."""


@dataclass(frozen=True, slots=True)
class SourceConfig:
    source: object = field(repr=False)
    generation_path: object = field(repr=False)
    cursor: object = field(repr=False)
    anchor_bytes: bytes = field(repr=False)
    anchor_sha256: str
    policy_bytes: bytes = field(repr=False)
    policy_sha256: str
    roster_bytes: bytes = field(repr=False)
    roster_sha256: str
    max_bytes: int
    max_rows: int


@dataclass(frozen=True, slots=True)
class PublicSourceConfig:
    """Explicit public-v1 policy and source; caller independently pins both."""
    source: SourceConfig = field(repr=False)
    public_policy_bytes: bytes = field(repr=False)
    public_policy_sha256: str


@dataclass(frozen=True, slots=True)
class PreparedDelta:
    """Uncommitted caller-context result; neither authentic nor a checkpoint."""
    result: dict = field(repr=False)
    status: str = "PREPARED_NOT_PUBLISHED"


def prepare_delta(*, enabled=False, config=None):
    """Stage a strict snapshot; OFF by default and never advances a cursor."""
    try:
        if type(enabled) is not bool:
            raise ValueError()
        if not enabled:
            return {"status": "OFF"}
        if (type(config) is not SourceConfig or type(config.max_bytes) is not int
                or not 1 <= config.max_bytes <= snapshot.MAX_BYTES
                or type(config.max_rows) is not int or not 1 <= config.max_rows <= snapshot.MAX_ROWS):
            raise ValueError()
        tasks, _ = adapter.strict_pins(config.policy_bytes, config.policy_sha256,
                                       config.roster_bytes, config.roster_sha256)
        result = snapshot.read_anchored_delta(
            config.source, config.cursor, tasks, anchor_bytes=config.anchor_bytes,
            anchor_sha256=config.anchor_sha256, generation_path=config.generation_path,
            max_bytes=config.max_bytes, max_rows=config.max_rows,
            policy_bytes=config.policy_bytes, policy_sha256=config.policy_sha256,
            roster_bytes=config.roster_bytes, roster_sha256=config.roster_sha256)
        if snapshot.verify_anchored_projection(
            result, result["projection"], expected_anchor_sha256=config.anchor_sha256,
            expected_policy_sha256=config.policy_sha256, expected_roster_sha256=config.roster_sha256
        ) is not True:
            raise ValueError()
        return PreparedDelta(result=result)
    except Exception:
        raise ProducerRefused("producer_refused") from None


def prepare_public_delta(*, enabled=False, config=None):
    """Stage one public-v1 snapshot; no publication, scheduler, cursor CAS or live wiring.

    The in-process snapshot commitment does not authenticate the source, pins,
    or same-user callers. Empty output does not establish complete history.
    """
    try:
        if type(enabled) is not bool:
            raise ValueError()
        if not enabled:
            return {"status": "OFF"}
        if type(config) is not PublicSourceConfig or type(config.source) is not SourceConfig:
            raise ValueError()
        source = config.source
        if (type(source.max_bytes) is not int or not 1 <= source.max_bytes <= snapshot.MAX_BYTES
                or type(source.max_rows) is not int or not 1 <= source.max_rows <= snapshot.MAX_ROWS):
            raise ValueError()
        tasks, _ = adapter.strict_pins(source.policy_bytes, source.policy_sha256,
                                       source.roster_bytes, source.roster_sha256)
        adapter.public_status_policy(config.public_policy_bytes, config.public_policy_sha256, tasks)
        result = snapshot.read_anchored_delta(
            source.source, source.cursor, tasks, anchor_bytes=source.anchor_bytes,
            anchor_sha256=source.anchor_sha256, generation_path=source.generation_path,
            max_bytes=source.max_bytes, max_rows=source.max_rows,
            policy_bytes=source.policy_bytes, policy_sha256=source.policy_sha256,
            roster_bytes=source.roster_bytes, roster_sha256=source.roster_sha256,
            public_policy_bytes=config.public_policy_bytes, public_policy_sha256=config.public_policy_sha256)
        if snapshot.verify_anchored_projection(
            result, result["projection"], expected_anchor_sha256=source.anchor_sha256,
            expected_policy_sha256=source.policy_sha256, expected_roster_sha256=source.roster_sha256,
            expected_mode="public-v1", expected_public_policy_sha256=config.public_policy_sha256,
            expected_anchor_bytes=source.anchor_bytes
        ) is not True:
            raise ValueError()
        return PreparedDelta(result=result)
    except Exception:
        raise ProducerRefused("producer_refused") from None


def publish_prepared(_prepared):
    """No builder/checkpoint invocation until the history contract is bound."""
    raise ProducerRefused("history_checkpoint_integration_incomplete")
