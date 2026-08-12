# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""Pure advisory projection for bridge attention requests.

The producer-owned ``requested_blocking`` field is only a scheduling hint.
This module deliberately has no path that can admit an immediate interrupt:
the current bridge HMAC does not bind the requested level, recipient, agent
UUID, or target head.  Until a separately reviewed authentication contract
exists, level 2 is capped at checkpoint review (effective level 1).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from waggledance.core.bridge_event_schema import BridgeEvent, validate_event


@dataclass(frozen=True)
class BridgeAttentionDecision:
    """Consumer-owned, non-authoritative scheduling projection."""

    requested_blocking: int
    effective_blocking: int
    decision: str
    interrupt_now: bool = False
    runtime_authority_granted: bool = False

    def __post_init__(self) -> None:
        expected = {
            0: (0, "background_queue"),
            1: (1, "checkpoint_review"),
            2: (1, "authenticated_interrupt_admission_unavailable"),
        }
        if type(self.requested_blocking) is not int:
            raise TypeError("requested_blocking must be an exact integer")
        if self.requested_blocking not in expected:
            raise ValueError("requested_blocking must be 0, 1, or 2")
        expected_effective, expected_decision = expected[self.requested_blocking]
        if (
            self.effective_blocking != expected_effective
            or self.decision != expected_decision
            or self.interrupt_now is not False
            or self.runtime_authority_granted is not False
        ):
            raise ValueError(
                "attention decision cannot grant an authenticated interrupt"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_blocking": self.requested_blocking,
            "effective_blocking": self.effective_blocking,
            "decision": self.decision,
            "interrupt_now": self.interrupt_now,
            "runtime_authority_granted": self.runtime_authority_granted,
        }


def admit_bridge_attention(
    event: BridgeEvent | Mapping[str, Any],
) -> BridgeAttentionDecision:
    """Return the maximum safe advisory attention level for ``event``.

    Missing legacy fields map to background level 0.  Levels 0 and 1 retain
    their scheduling meaning.  Requested level 2 cannot become an interrupt
    in this foundation and is capped at level 1 with an explicit reason.
    Severity, status, message text, and payload content never infer a level.
    """

    # BridgeEvent is intentionally not assignment-validated for historical
    # compatibility.  Revalidate even an existing model so a caller cannot
    # mutate or model-construct an invalid level across this trust boundary.
    raw_event = event.model_dump() if isinstance(event, BridgeEvent) else event
    model = validate_event(raw_event)
    requested = model.requested_blocking
    if requested == 0:
        effective = 0
        decision = "background_queue"
    elif requested == 1:
        effective = 1
        decision = "checkpoint_review"
    else:
        effective = 1
        decision = "authenticated_interrupt_admission_unavailable"
    return BridgeAttentionDecision(
        requested_blocking=requested,
        effective_blocking=effective,
        decision=decision,
    )


__all__ = ["BridgeAttentionDecision", "admit_bridge_attention"]
