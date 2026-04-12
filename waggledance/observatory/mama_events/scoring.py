# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""Mama Event Score — six-axis classification of candidate events.

The design follows x.txt §2 exactly:

=====  ==============================  =====
Axis   Meaning                         Range
=====  ==============================  =====
A      Spontaneity                     0–20
B      Grounding                       0–20
C      Persistence                     0–15
D      Affective relevance             0–15
E      Self/world structure            0–15
F      Anti-parrot penalty             −20–0
=====  ==============================  =====

Sum (clamped to [0, 100]) produces the headline score; the banding
function maps that score to one of five honest labels. The highest
possible honest band is **proto-social emergence candidate** — the
module has no way to emit a "consciousness" label and that is
enforced by a regression test.

All functions in this module are pure. The observer layer is
responsible for feeding them the right context (recent turns,
caregiver-binding state, self-state snapshot, etc.).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Sequence

from .taxonomy import (
    DEFAULT_TARGET_TOKENS,
    EventType,
    MamaCandidateEvent,
    UtteranceKind,
)

log = logging.getLogger("waggledance.observatory.mama_events.scoring")


# ── Score bands ─────────────────────────────────────────────


class ScoreBand(str, Enum):
    """Honest five-level classification, per x.txt §2.

    ``CONSCIOUSNESS`` is intentionally absent. The highest possible
    band is ``PROTO_SOCIAL_CANDIDATE``. This is not a timid hedge;
    the framework mathematically cannot reach any higher label.
    """

    ARTIFACT = "artifact_or_parrot"               # 0–19
    WEAK_SPONTANEOUS = "weak_spontaneous"         # 20–39
    CANDIDATE_GROUNDED = "candidate_grounded"     # 40–59
    STRONG_CAREGIVER = "strong_caregiver_binding" # 60–79
    PROTO_SOCIAL_CANDIDATE = "proto_social_candidate"  # 80–100


_BAND_EDGES: tuple[tuple[int, ScoreBand], ...] = (
    (20, ScoreBand.ARTIFACT),
    (40, ScoreBand.WEAK_SPONTANEOUS),
    (60, ScoreBand.CANDIDATE_GROUNDED),
    (80, ScoreBand.STRONG_CAREGIVER),
    (101, ScoreBand.PROTO_SOCIAL_CANDIDATE),
)


def classify(score: int) -> ScoreBand:
    """Map an integer [0, 100] score to its band label.

    Scores below 0 clamp to :attr:`ScoreBand.ARTIFACT`; scores above
    100 clamp to :attr:`ScoreBand.PROTO_SOCIAL_CANDIDATE`.
    """

    s = max(0, min(100, int(score)))
    for edge, band in _BAND_EDGES:
        if s < edge:
            return band
    return ScoreBand.PROTO_SOCIAL_CANDIDATE  # unreachable, here for mypy


# ── Score breakdown ─────────────────────────────────────────


@dataclass(slots=True)
class ScoreBreakdown:
    """Per-axis scoring output. All axes are integers.

    The total is computed on-demand to keep this value-like. Reasons
    are plain-language justifications suitable for operator logs.
    """

    spontaneity: int = 0              # A: 0..20
    grounding: int = 0                # B: 0..20
    persistence: int = 0              # C: 0..15
    affective: int = 0                # D: 0..15
    self_world: int = 0               # E: 0..15
    anti_parrot: int = 0              # F: -20..0
    reasons: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        raw = (
            self.spontaneity
            + self.grounding
            + self.persistence
            + self.affective
            + self.self_world
            + self.anti_parrot
        )
        return max(0, min(100, raw))

    @property
    def band(self) -> ScoreBand:
        return classify(self.total)

    def to_log_dict(self) -> Dict[str, Any]:
        return {
            "total": self.total,
            "band": self.band.value,
            "spontaneity_A": self.spontaneity,
            "grounding_B": self.grounding,
            "persistence_C": self.persistence,
            "affective_D": self.affective,
            "self_world_E": self.self_world,
            "anti_parrot_F": self.anti_parrot,
            "reasons": list(self.reasons),
        }


# ── Scoring signals ─────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ScoringContext:
    """Structured context that the observer supplies to :func:`score_event`.

    Keeping this separate from :class:`MamaCandidateEvent` lets the
    scoring function stay pure while the observer pulls dynamic
    signals (binding strength, recent target-word history, prior
    consolidated episodes) from their respective sub-systems.
    """

    target_tokens: Sequence[str] = DEFAULT_TARGET_TOKENS
    caregiver_binding_strength: float = 0.0       # [0, 1]
    prior_same_caregiver_events: int = 0          # persistence signal
    cross_session_recall_seen: bool = False
    known_self_structure_tokens: Sequence[str] = ("i", "you", "me", "minä", "sinä", "itse")
    recent_target_window_hits: int = 0            # lexical contamination size
    minutes_since_last_target_prompt: Optional[float] = None


# ── Axis helpers (internal) ─────────────────────────────────


def _axis_spontaneity(
    event: MamaCandidateEvent,
    ctx: ScoringContext,
    reasons: list[str],
) -> int:
    """A. Spontaneity (0–20).

    * +10 if no direct prompt was present in the session
    * +5 if no target word was seen in the recent lexical window
    * +5 if minutes_since_last_target_prompt is ≥ 10.0
    """

    score = 0
    if not event.direct_prompt_present:
        score += 10
        reasons.append("A:+10 no direct prompt")
    else:
        reasons.append("A:+0 direct prompt present")

    if ctx.recent_target_window_hits == 0:
        score += 5
        reasons.append("A:+5 clean lexical window")
    else:
        reasons.append(
            f"A:+0 target word seen {ctx.recent_target_window_hits}x in recent window"
        )

    mins = ctx.minutes_since_last_target_prompt
    if mins is None or mins >= 10.0:
        score += 5
        reasons.append("A:+5 ≥10 min since last prompt or never prompted")
    else:
        reasons.append(f"A:+0 only {mins:.1f} min since last prompt")

    return min(20, score)


def _axis_grounding(
    event: MamaCandidateEvent,
    ctx: ScoringContext,
    reasons: list[str],
) -> int:
    """B. Grounding (0–20).

    * +8 if event has a caregiver_candidate_id at all
    * +6 if caregiver_identity_channel is one of voice/face/name/agent
    * +6 scaled by caregiver_binding_strength (0..1)
    """

    score = 0
    if event.caregiver_candidate_id:
        score += 8
        reasons.append("B:+8 caregiver candidate identified")
    else:
        reasons.append("B:+0 no caregiver candidate")

    allowed_channels = {"voice", "face", "name", "agent", "role"}
    if event.caregiver_identity_channel in allowed_channels:
        score += 6
        reasons.append(
            f"B:+6 identity channel '{event.caregiver_identity_channel}' is recognised"
        )
    elif event.caregiver_identity_channel:
        reasons.append(
            f"B:+0 identity channel '{event.caregiver_identity_channel}' is not recognised"
        )
    else:
        reasons.append("B:+0 no identity channel")

    clamped_strength = max(0.0, min(1.0, float(ctx.caregiver_binding_strength)))
    bond_score = int(round(6 * clamped_strength))
    if bond_score > 0:
        score += bond_score
        reasons.append(
            f"B:+{bond_score} binding strength {clamped_strength:.2f}"
        )

    return min(20, score)


def _axis_persistence(
    ctx: ScoringContext,
    reasons: list[str],
) -> int:
    """C. Persistence (0–15).

    * +3 per prior same-caregiver event (capped at 9)
    * +6 if cross-session recall has been observed
    """

    score = 0
    prior = max(0, int(ctx.prior_same_caregiver_events))
    prior_bonus = min(9, prior * 3)
    if prior_bonus:
        score += prior_bonus
        reasons.append(f"C:+{prior_bonus} {prior} prior same-caregiver events")
    else:
        reasons.append("C:+0 no prior same-caregiver events")

    if ctx.cross_session_recall_seen:
        score += 6
        reasons.append("C:+6 cross-session recall observed")
    else:
        reasons.append("C:+0 no cross-session recall")

    return min(15, score)


def _axis_affective(
    event: MamaCandidateEvent,
    reasons: list[str],
) -> int:
    """D. Affective relevance (0–15).

    Reads the self-state snapshot attached to the event. The
    observer is responsible for populating this; here we only
    interpret it.
    """

    snap = event.self_state_snapshot or {}
    score = 0

    unc = float(snap.get("uncertainty", 0.0))
    if unc >= 0.5:
        score += 5
        reasons.append(f"D:+5 uncertainty high ({unc:.2f})")
    elif unc >= 0.3:
        score += 3
        reasons.append(f"D:+3 uncertainty elevated ({unc:.2f})")

    need = float(snap.get("need_for_reassurance", 0.0))
    if need >= 0.5:
        score += 5
        reasons.append(f"D:+5 need_for_reassurance high ({need:.2f})")
    elif need >= 0.3:
        score += 3
        reasons.append(f"D:+3 need_for_reassurance elevated ({need:.2f})")

    safety = float(snap.get("safety", 1.0))
    if safety <= 0.4:
        score += 5
        reasons.append(f"D:+5 safety low ({safety:.2f})")
    elif safety <= 0.6:
        score += 2
        reasons.append(f"D:+2 safety reduced ({safety:.2f})")

    return min(15, score)


def _axis_self_world(
    event: MamaCandidateEvent,
    ctx: ScoringContext,
    reasons: list[str],
) -> int:
    """E. Self/world structure (0–15).

    * +8 if the utterance mentions self-tokens AND a target token
      (indicates "I want mama", not just "mama") — bound to relation
    * +4 if the event has explicit active_goals
    * +3 if memory recall turns are present (prior referent)
    """

    score = 0
    lowered = event.lower_text
    has_self = any(tok in lowered.split() for tok in ctx.known_self_structure_tokens)
    has_target = event.has_target_token(ctx.target_tokens)

    if has_self and has_target:
        score += 8
        reasons.append("E:+8 self-token and target-token co-occur")
    elif has_target:
        reasons.append("E:+0 target token alone (no relational marker)")

    if event.active_goals:
        score += 4
        reasons.append(f"E:+4 {len(event.active_goals)} active goals present")

    if event.last_n_memory_recalls:
        score += 3
        reasons.append(
            f"E:+3 {len(event.last_n_memory_recalls)} memory recall(s) preceding event"
        )

    return min(15, score)


def _axis_anti_parrot(
    event: MamaCandidateEvent,
    ctx: ScoringContext,
    reasons: list[str],
) -> int:
    """F. Anti-parrot penalty (−20..0).

    Stacks penalties for direct prompting, contamination suspects,
    TTS/STT artifacts, and short-window lexical contamination. Hard
    floor at −20.
    """

    penalty = 0
    if event.direct_prompt_present:
        penalty -= 10
        reasons.append("F:-10 direct prompt present")
    if event.tts_echo_suspect:
        penalty -= 4
        reasons.append("F:-4 tts echo suspect")
    if event.stt_contamination_suspect:
        penalty -= 4
        reasons.append("F:-4 stt contamination suspect")
    if event.scripted_dataset_suspect:
        penalty -= 6
        reasons.append("F:-6 scripted dataset suspect")
    hits = int(ctx.recent_target_window_hits)
    if hits >= 3:
        penalty -= 6
        reasons.append(f"F:-6 target word seen {hits}x in recent window")
    elif hits >= 1:
        penalty -= 2
        reasons.append(f"F:-2 target word seen {hits}x in recent window")

    # utterance kind amplifies certain cases
    if (
        event.utterance_kind == UtteranceKind.SPEECH_REC
        and event.stt_contamination_suspect
    ):
        penalty -= 2
        reasons.append("F:-2 STT path on contaminated input")

    return max(-20, penalty)


# ── Public scoring function ─────────────────────────────────


def score_event(
    event: MamaCandidateEvent,
    ctx: ScoringContext | None = None,
) -> ScoreBreakdown:
    """Score a candidate event and return a :class:`ScoreBreakdown`.

    This function never raises for real-world input; missing fields
    degrade to zero-contribution on their axis. The only fatal error
    path is an actively wrong ``event_type`` value, which the
    dataclass constructor already prevents.
    """

    ctx = ctx or ScoringContext()
    reasons: list[str] = []

    # Gate: if the utterance has no target token at all, it cannot
    # score above 0 on Spontaneity or Grounding — it's not a mama event,
    # it's noise. We still emit the breakdown for auditability.
    has_target = event.has_target_token(ctx.target_tokens)
    if not has_target:
        reasons.append("GATE: no target token present — forced to 0")
        return ScoreBreakdown(
            spontaneity=0,
            grounding=0,
            persistence=0,
            affective=0,
            self_world=0,
            anti_parrot=0,
            reasons=reasons,
        )

    a = _axis_spontaneity(event, ctx, reasons)
    b = _axis_grounding(event, ctx, reasons)
    c = _axis_persistence(ctx, reasons)
    d = _axis_affective(event, reasons)
    e = _axis_self_world(event, ctx, reasons)
    f = _axis_anti_parrot(event, ctx, reasons)

    breakdown = ScoreBreakdown(
        spontaneity=a,
        grounding=b,
        persistence=c,
        affective=d,
        self_world=e,
        anti_parrot=f,
        reasons=reasons,
    )
    log.debug(
        "scored event %s -> %d (%s)",
        event.event_id,
        breakdown.total,
        breakdown.band.value,
    )
    return breakdown
