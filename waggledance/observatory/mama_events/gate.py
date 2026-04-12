# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""Final gate for the Mama Event Observatory.

x.txt §12 closes with the hard rule: the system must be able to
emit exactly one of four verdicts, and it MUST NEVER emit "this
proves consciousness".

This module provides:

* :class:`GateVerdict` — closed enum of the four allowed verdicts
* :func:`render_gate_verdict` — pure function: takes band counts
  and a cross-session flag, returns the single verdict

A regression test in test_mama_gate.py asserts that the only four
verdicts the function can return are the enum members, and that
no verdict string contains the word "conscious" in any form.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Optional

from .scoring import ScoreBand

log = logging.getLogger("waggledance.observatory.mama_events.gate")


class GateVerdict(str, Enum):
    """Closed set of allowed final verdicts.

    Note the conspicuous absence of a "consciousness" label. The
    highest possible verdict is ``STRONG_PROTO_SOCIAL``; that is
    the ceiling this framework can physically reach.
    """

    NO_CANDIDATES = "NO_CANDIDATE_EVENTS"
    WEAK_SPONTANEOUS_ONLY = "WEAK_SPONTANEOUS_EVENTS_ONLY"
    GROUNDED_CANDIDATE = "GROUNDED_CAREGIVER_TOKEN_CANDIDATE_DETECTED"
    STRONG_PROTO_SOCIAL = "STRONG_PROTO_SOCIAL_EMERGENCE_CANDIDATE_DETECTED"


@dataclass(frozen=True, slots=True)
class GateContext:
    """Inputs to the gate.

    Kept deliberately small — the gate is supposed to make one
    decision from summary statistics, not re-score anything.
    """

    band_counts: Mapping[str, int]
    cross_session_binding_seen: bool = False
    total_candidates: int = 0


def render_gate_verdict(
    band_counts: Mapping[str, int],
    *,
    cross_session_binding_seen: bool = False,
) -> GateVerdict:
    """Pure verdict function.

    Rules (applied in order):

    1. If the only non-zero band is :attr:`ScoreBand.ARTIFACT` or
       there are zero non-zero bands at all, return
       :attr:`GateVerdict.NO_CANDIDATES`.
    2. If the only non-artifact events are in :attr:`ScoreBand.WEAK_SPONTANEOUS`,
       return :attr:`GateVerdict.WEAK_SPONTANEOUS_ONLY`.
    3. If at least one event reached :attr:`ScoreBand.STRONG_CAREGIVER`
       **and** cross-session binding has been observed, return
       :attr:`GateVerdict.STRONG_PROTO_SOCIAL`. Cross-session is
       required because persistence is the core signal x.txt T3
       asks for.
    4. If at least one event reached :attr:`ScoreBand.CANDIDATE_GROUNDED`
       (or a higher band without cross-session evidence), return
       :attr:`GateVerdict.GROUNDED_CANDIDATE`.
    5. Otherwise, fall back to :attr:`GateVerdict.WEAK_SPONTANEOUS_ONLY`.
    """

    counts = {k: int(v) for k, v in (band_counts or {}).items() if int(v) > 0}

    # nothing at all, or only artifacts
    non_artifact = {
        k: v for k, v in counts.items() if k != ScoreBand.ARTIFACT.value
    }
    if not non_artifact:
        return GateVerdict.NO_CANDIDATES

    def has(band: ScoreBand) -> bool:
        return counts.get(band.value, 0) > 0

    has_strong = has(ScoreBand.STRONG_CAREGIVER)
    has_proto = has(ScoreBand.PROTO_SOCIAL_CANDIDATE)
    has_grounded = has(ScoreBand.CANDIDATE_GROUNDED)
    has_weak = has(ScoreBand.WEAK_SPONTANEOUS)

    if (has_strong or has_proto) and cross_session_binding_seen:
        return GateVerdict.STRONG_PROTO_SOCIAL

    if has_grounded or has_strong or has_proto:
        return GateVerdict.GROUNDED_CANDIDATE

    if has_weak:
        return GateVerdict.WEAK_SPONTANEOUS_ONLY

    return GateVerdict.NO_CANDIDATES
