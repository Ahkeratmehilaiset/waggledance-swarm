"""
Logical hex-cell topology for hybrid retrieval.

Provides deterministic domain-based cell assignment with bounded neighbor lists.
Each cell represents a knowledge domain; neighbors are adjacent domains that
share conceptual overlap.

This is a LOGICAL overlay only — no visual hex grid.
Cells map queries to local FAISS indices for fast retrieval before
falling back to global ChromaDB.

Cell assignment rules (deterministic, no ML clustering):
  - Safety override: high-confidence incident tokens preempt ordinary intents
  - Intent-based: math queries → math cell, thermal → thermal cell, etc.
  - Keyword fallback: other domain keywords checked for general/unmapped intents
  - Default: general cell for unclassified queries

Neighbor topology (ring-1 adjacency):
  Each cell has 2-4 neighbors based on domain overlap.
  Ring-2 (neighbor-of-neighbor) is computed but bounded.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Set

log = logging.getLogger(__name__)


# ── Cell definitions ──────────────────────────────────────────

CELL_GENERAL = "general"
CELL_THERMAL = "thermal"
CELL_ENERGY = "energy"
CELL_SAFETY = "safety"
CELL_SEASONAL = "seasonal"
CELL_MATH = "math"
CELL_SYSTEM = "system"
CELL_LEARNING = "learning"

ALL_CELLS: List[str] = [
    CELL_GENERAL,
    CELL_THERMAL,
    CELL_ENERGY,
    CELL_SAFETY,
    CELL_SEASONAL,
    CELL_MATH,
    CELL_SYSTEM,
    CELL_LEARNING,
]

# ── Ring-1 neighbor adjacency (bidirectional) ─────────────────
# Each entry: cell -> set of ring-1 neighbors
# Rationale:
#   thermal <-> energy (heating costs), thermal <-> seasonal (temp varies by season)
#   energy <-> safety (grid overload), energy <-> math (cost calculations)
#   safety <-> system (system health), safety <-> general (general advice)
#   seasonal <-> general (general knowledge), seasonal <-> learning (seasonal patterns)
#   math <-> general (general calculations), math <-> system (resource math)
#   learning <-> system (model metrics), learning <-> general (knowledge)

_ADJACENCY: Dict[str, FrozenSet[str]] = {
    CELL_GENERAL:  frozenset({CELL_SAFETY, CELL_SEASONAL, CELL_MATH, CELL_LEARNING}),
    CELL_THERMAL:  frozenset({CELL_ENERGY, CELL_SEASONAL, CELL_SAFETY}),
    CELL_ENERGY:   frozenset({CELL_THERMAL, CELL_SAFETY, CELL_MATH}),
    CELL_SAFETY:   frozenset({CELL_THERMAL, CELL_ENERGY, CELL_SYSTEM, CELL_GENERAL}),
    CELL_SEASONAL: frozenset({CELL_THERMAL, CELL_GENERAL, CELL_LEARNING}),
    CELL_MATH:     frozenset({CELL_ENERGY, CELL_GENERAL, CELL_SYSTEM}),
    CELL_SYSTEM:   frozenset({CELL_SAFETY, CELL_MATH, CELL_LEARNING}),
    CELL_LEARNING: frozenset({CELL_SEASONAL, CELL_GENERAL, CELL_SYSTEM}),
}

# ── Intent → cell mapping ────────────────────────────────────

_INTENT_TO_CELL: Dict[str, str] = {
    "math": CELL_MATH,
    "thermal": CELL_THERMAL,
    "optimization": CELL_ENERGY,
    "seasonal": CELL_SEASONAL,
    "constraint": CELL_SAFETY,
    "stats": CELL_SYSTEM,
    "statistical": CELL_SYSTEM,
    "symbolic": CELL_MATH,
    "causal": CELL_GENERAL,
    "anomaly": CELL_SYSTEM,
    "retrieval": CELL_GENERAL,
    "chat": CELL_GENERAL,
}

# ── Keyword routing ──────────────────────────────────────────

_DOMAIN_KEYWORDS: Dict[str, List[str]] = {
    CELL_THERMAL: [
        "temperature", "lämpötila", "heat", "frost", "pakkanen",
        "celsius", "fahrenheit", "heating", "cooling", "lämmitys",
    ],
    CELL_ENERGY: [
        "energy", "energia", "power", "teho", "watt", "kilowatt",
        "electricity", "sähkö", "grid", "solar", "battery",
    ],
    CELL_SAFETY: [
        "safety", "turvallisuus", "alarm", "hälytys", "palovaroitin",
        "fire", "tulipalo", "smoke", "savu", "violation", "risk", "riski",
        "danger",
    ],
    CELL_SEASONAL: [
        "season", "vuodenaika", "spring", "kevät", "summer", "kesä",
        "autumn", "syksy", "winter", "talvi", "month", "kuukausi",
    ],
    CELL_MATH: [
        "calculate", "laske", "formula", "kaava", "equation", "yhtälö",
        "percent", "prosentti", "multiply", "divide",
    ],
    CELL_SYSTEM: [
        "system", "järjestelmä", "cpu", "memory", "muisti", "disk",
        "process", "status", "tila", "health", "uptime",
    ],
    CELL_LEARNING: [
        "learn", "oppi", "train", "koulut", "model", "malli",
        "specialist", "dream", "night", "yö",
    ],
}

# These token sequences are deliberately narrower than the ordinary safety-cell
# vocabulary.  They may preempt an already classified intent, so each sequence
# must describe a high-confidence incident rather than an ambiguous domain
# word such as "risk", "smoke", or "alarm" on its own.
_INCIDENT_AFFIRMATIVE_MODIFIER_TOKENS: FrozenSet[str] = frozenset(
    {"definitely", "just", "now", "still"}
)
_SMOKE_DETECTION_TOKEN_SEQUENCES: tuple[tuple[str, ...], ...] = (
    ("smoke", "detected"),
    ("smoke", "was", "detected"),
    ("smoke", "has", "been", "detected"),
)
_ACTIVE_ALARM_SUFFIXES: tuple[tuple[str, ...], ...] = (
    ("going", "off"),
    ("is", "going", "off"),
    ("was", "going", "off"),
    ("has", "gone", "off"),
    ("went", "off"),
    ("sounding",),
    ("is", "sounding"),
    ("ringing",),
    ("is", "ringing"),
    ("beeping",),
    ("is", "beeping"),
    ("sounded",),
    ("activated",),
    ("has", "activated"),
    ("triggered",),
    ("has", "triggered"),
)
_ALARM_DESCRIPTOR_TOKENS: FrozenSet[str] = frozenset(
    {"detector", "device", "system"}
)
_PALOVAROITIN_ACTIVE_TOKENS: FrozenSet[str] = frozenset(
    {"hälyttää", "piippaa", "soi", "ulvoo"}
)
_NON_INCIDENT_ACTIVITY_TOKENS: FrozenSet[str] = frozenset(
    {
        "demo",
        "demonstration",
        "drill",
        "harjoitus",
        "harjoituksen",
        "harjoituksessa",
        "koulutus",
        "koulutuksen",
        "koulutuksessa",
        "paloharjoitus",
        "paloharjoituksen",
        "paloharjoituksessa",
        "practice",
        "simulaatio",
        "simulaation",
        "simulaatiossa",
        "simulated",
        "simulation",
        "test",
        "testaus",
        "testauksen",
        "testauksessa",
        "testi",
        "testin",
        "testissä",
        "testing",
        "training",
    }
)
_NEGATION_TOKENS: FrozenSet[str] = frozenset(
    {"ei", "eikä", "älä", "never", "no", "non", "not"}
)
_POSTFIX_NEGATION_TOKENS: FrozenSet[str] = frozenset({"free"})
_NON_NEGATING_NOT_FOLLOWERS: FrozenSet[str] = frozenset({"only"})
_NEGATION_LOOKBACK = 5
_MAX_FIRE_ALARM_TOKEN_DISTANCE = 16
_MAX_PALOVAROITIN_CUE_DISTANCE = 8
_FIRE_ALARM_CONTEXT_PADDING = 8
_ACTIVITY_ATTACHMENT_PADDING = 16
_CORRECTION_CONTEXT_PADDING = 16
_UNCERTAINTY_TOKEN_SEQUENCES: tuple[tuple[str, ...], ...] = (
    ("can", "t", "confirm"),
    ("can", "t", "verify"),
    ("cannot", "confirm"),
    ("cannot", "verify"),
    ("don", "t", "think"),
    ("en", "usko"),
    ("en", "ole", "varma"),
    ("ei", "ole", "varmaa"),
    ("ehkä",),
    ("it", "is", "possible", "that"),
    ("it", "is", "unknown", "whether"),
    ("maybe",),
    ("perhaps",),
    ("not", "sure", "whether"),
    ("unable", "to", "confirm"),
    ("unable", "to", "verify"),
    ("unclear", "if"),
    ("unclear", "whether"),
    ("uncertain", "whether"),
    ("we", "cannot", "rule", "out"),
)
_POSTPOSED_UNCERTAINTY_SEQUENCES: tuple[tuple[str, ...], ...] = (
    ("is", "unclear"),
    ("or", "maybe", "not"),
    ("tai", "ehkä", "ei"),
)
_UNCERTAINTY_TOKENS: FrozenSet[str] = frozenset({"tuskin"})
_CLAUSE_BOUNDARY_TOKENS: FrozenSet[str] = frozenset(
    {",", ";", ":", "(", ")", "/", "–", "—"}
)
_CONTROL_SCOPE_BREAK_TOKENS: FrozenSet[str] = frozenset(
    {
        *_CLAUSE_BOUNDARY_TOKENS,
        "also",
        "although",
        "and",
        "because",
        "but",
        "however",
        "ja",
        "meanwhile",
        "muista",
        "mutta",
        "myös",
        "please",
        "plus",
        "then",
        "vaan",
        "whereas",
        "while",
        "yet",
    }
)
_NEGATION_SCOPE_BREAK_TOKENS: FrozenSet[str] = frozenset(
    {
        *_CONTROL_SCOPE_BREAK_TOKENS,
        "after",
        "as",
        "before",
        "ennen",
        "kuin",
        "kun",
        "once",
        "since",
        "though",
        "vaikka",
        "when",
        "why",
    }
)
_NON_ATTACHED_TEMPORAL_TOKENS: FrozenSet[str] = frozenset(
    {
        "after",
        "before",
        "ended",
        "jälkeen",
        "once",
        "outside",
        "päätyessä",
        "päätyttyä",
        "ulkopuolella",
    }
)
_AFFIRMATIVE_NEGATION_FOLLOWERS: FrozenSet[str] = frozenset(
    {"doubt", "epäilystäkään", "just", "less", "merely", "only", "vain"}
)
_AFFIRMATIVE_NEGATION_SEQUENCES: tuple[tuple[str, ...], ...] = (
    ("do", "not", "ignore"),
    ("ei", "syytä", "epäillä"),
    ("älä", "sivuuta"),
    ("never", "dismiss"),
    ("never", "ignore"),
    ("no", "denying"),
    ("no", "need", "to", "doubt"),
    ("no", "question"),
    ("no", "reason", "to", "doubt"),
    ("not", "ignore"),
    ("not", "dismiss"),
    ("not", "the", "slightest", "doubt"),
    ("älä", "jätä", "huomiotta"),
    ("ei", "voi", "kiistää"),
)
_AFFIRMATIVE_SCOPE_NEGATION_SEQUENCES: tuple[tuple[str, ...], ...] = (
    ("ei", "rajoitu"),
    ("not", "confined", "to"),
    ("not", "limited", "to"),
)
_WHILE_ACTIVITY_MODIFIERS: FrozenSet[str] = frozenset(
    {
        "a",
        "actual",
        "an",
        "annual",
        "fire",
        "just",
        "new",
        "our",
        "only",
        "real",
        "routine",
        "s",
        "scheduled",
        "simulated",
        "the",
        "today",
        "vain",
    }
)
_SAFETY_ACTIVITY_TARGET_TOKENS: FrozenSet[str] = frozenset(
    {
        "alarm",
        "alarms",
        "detector",
        "detectors",
        "fire",
        "palovaroitin",
        "palovaroittimen",
        "sensor",
        "sensors",
        "smoke",
    }
)
_ACTIVITY_TARGET_MODIFIERS: FrozenSet[str] = frozenset(
    {
        "a",
        "actual",
        "an",
        "new",
        "our",
        "real",
        "scheduled",
        "simulated",
        "the",
        "this",
    }
)
_ACTIVITY_POST_TARGET_MARKERS: FrozenSet[str] = frozenset({"for", "of", "on"})
_SAFETY_ACTIVITY_TARGET_SEQUENCES: tuple[tuple[str, ...], ...] = (
    ("alarm",),
    ("alarm", "detector"),
    ("alarm", "system"),
    ("detector",),
    ("detectors",),
    ("fire", "alarm"),
    ("fire", "alarm", "system"),
    ("fire", "detector"),
    ("palovaroitin",),
    ("palovaroittimen",),
    ("sensor",),
    ("sensors",),
    ("smoke", "detector"),
    ("smoke", "detectors"),
    ("smoke", "sensor"),
    ("smoke", "sensors"),
)
_ACTIVITY_TRAILING_GRAMMAR_TOKENS: FrozenSet[str] = frozenset(
    {
        "after",
        "aikana",
        "alkamisen",
        "before",
        "began",
        "ended",
        "ends",
        "jälkeen",
        "päättymistä",
        "ran",
        "run",
        "started",
        "that",
        "underway",
        "was",
        "which",
        "yhteydessä",
    }
)
_SAFE_ACTIVITY_TRAILING_SEQUENCES: FrozenSet[tuple[str, ...]] = frozenset(
    {
        ("aikana",),
        ("after", "lunch"),
        ("alkamisen", "jälkeen"),
        ("before", "lunch"),
        ("began",),
        ("ended",),
        ("ends",),
        ("jälkeen",),
        ("päättymistä",),
        ("ran",),
        ("run",),
        ("started",),
        ("that", "began"),
        ("that", "ended"),
        ("that", "ended", "moments", "later"),
        ("that", "ends"),
        ("that", "is", "underway"),
        ("that", "started"),
        ("underway",),
        ("was", "underway"),
        ("which", "began"),
        ("which", "ended"),
        ("which", "ends"),
        ("which", "is", "underway"),
        ("which", "started"),
        ("yhteydessä",),
    }
)
_ACTIVITY_TEMPORAL_PREPOSITIONS: FrozenSet[str] = frozenset(
    {"after", "before"}
)
_ACTIVITY_RELATIVE_PRONOUNS: FrozenSet[str] = frozenset({"that", "which"})
_ACTIVITY_TEMPORAL_REFERENCE_TOKENS: FrozenSet[str] = frozenset(
    {
        "breakfast",
        "dinner",
        "lunch",
        "midnight",
        "noon",
        "today",
        "tomorrow",
        "yesterday",
    }
)
_ACTIVITY_DAYPART_TOKENS: FrozenSet[str] = frozenset(
    {"afternoon", "evening", "morning", "night", "sunrise", "sunset"}
)
_ACTIVITY_DAY_MODIFIER_TOKENS: FrozenSet[str] = frozenset(
    {"next", "this", "today", "tomorrow", "yesterday"}
)
_ACTIVITY_TIME_UNIT_TOKENS: FrozenSet[str] = frozenset(
    {
        "day",
        "days",
        "hour",
        "hours",
        "minute",
        "minutes",
        "moment",
        "moments",
        "second",
        "seconds",
        "week",
        "weeks",
    }
)
_ACTIVITY_TIME_DIRECTION_TOKENS: FrozenSet[str] = frozenset(
    {"afterward", "afterwards", "ago", "earlier", "later"}
)
_ACTIVITY_TIME_ADVERB_TOKENS: FrozenSet[str] = frozenset(
    {
        "already",
        "currently",
        "earlier",
        "later",
        "recently",
        "shortly",
        "still",
        "today",
    }
)
_ACTIVITY_ASPECT_MODIFIERS: FrozenSet[str] = frozenset(
    {"currently", "still"}
)
_MAX_ACTIVITY_TEMPORAL_DETAIL_TOKENS = 5
_INCIDENT_AFFIRMATION_SEQUENCES: tuple[tuple[str, ...], ...] = (
    ("actual", "fire"),
    ("actual", "smoke"),
    ("oikea", "tulipalo"),
    ("oikean", "tulipalon"),
    ("real", "fire"),
    ("real", "smoke"),
)
_NEGATED_SIMULATION_AFFIRMATION_SEQUENCES: tuple[tuple[str, ...], ...] = (
    ("not", "simulated", "fire"),
    ("not", "simulated", "smoke"),
)
_INCIDENT_AFFIRMATION_PADDING = 16
_INCIDENT_AFFIRMATION_BINDING_LOOKAROUND = 8
_INCIDENT_AFFIRMATION_ACTION_TOKENS: FrozenSet[str] = frozenset(
    {
        "analyze",
        "calculate",
        "cost",
        "data",
        "dataset",
        "density",
        "documentation",
        "estimate",
        "imagery",
        "insurance",
        "model",
        "pricing",
        "rendering",
        "risk",
        "sale",
        "software",
        "statistics",
        "update",
    }
)
_INCIDENT_AFFIRMATION_NEGATED_PREDICATES: FrozenSet[str] = frozenset(
    {"denied", "denies", "disputed", "doubt", "doubts", "missed"}
)
_INCIDENT_REFERENCE_MODIFIERS: FrozenSet[str] = frozenset(
    {"certainly", "clearly", "definitely", "varmasti"}
)
_POSTPOSED_INCIDENT_DENIAL_SEQUENCES: tuple[tuple[str, ...], ...] = (
    ("is", "not", "present"),
    ("is", "not", "confirmed"),
    ("is", "ruled", "out"),
    ("never", "confirmed"),
    ("not", "present"),
    ("ruled", "out"),
    ("was", "not", "present"),
    ("was", "not", "confirmed"),
    ("was", "never", "confirmed"),
    ("was", "ruled", "out"),
    ("remains", "absent"),
)
_POSTPOSED_INCIDENT_CONFIRMATION_SEQUENCES: tuple[tuple[str, ...], ...] = (
    ("broke", "out"),
    ("confirmed",),
    ("caused",),
    ("detected",),
    ("exists",),
    ("filled", "the", "room"),
    ("has", "been", "detected"),
    ("produced",),
    ("triggered",),
    ("was", "the", "cause"),
    ("has", "been", "confirmed"),
    ("is", "confirmed"),
    ("is", "present"),
    ("is", "visible"),
    ("on", "vahvistettu"),
    ("on", "havaittu"),
    ("havaittu",),
    ("takia",),
    ("todettiin",),
    ("vuoksi",),
    ("was", "confirmed"),
    ("was", "present"),
    ("was", "visible"),
    ("varmistettu",),
    ("vahvistettiin",),
)
_INCIDENT_AFFIRMATION_ACTIVE_PREDICATES: FrozenSet[str] = frozenset(
    {
        "confirm",
        "confirmed",
        "confirms",
        "detected",
        "havaittu",
        "observed",
        "reported",
        "saw",
        "varmistettu",
    }
)
_INCIDENT_POSITIVE_STATE_TOKENS: FrozenSet[str] = frozenset(
    {"burning", "present", "visible"}
)
_NON_INCIDENT_REPLACEMENT_TOKENS: FrozenSet[str] = frozenset(
    {"steam", "vapor", "vapour"}
)
_CORRECTION_DISCOURSE_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("correction",),
    ("to", "clarify"),
)
_INHERENT_ACTIVITY_CONTEXT_TOKENS: FrozenSet[str] = frozenset(
    {
        "harjoituksessa",
        "koulutuksessa",
        "paloharjoituksessa",
        "simulaatiossa",
        "simulated",
        "testauksessa",
        "testissä",
    }
)
_EXPLICIT_EVENT_REFERENCE_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("this", "is"),
    ("this", "was"),
    ("that", "is"),
    ("that", "was"),
    ("it", "is"),
    ("it", "was"),
    ("se", "on"),
    ("se", "oli"),
    ("tämä", "on"),
    ("tämä", "oli"),
)
_EXPLICIT_ACTIVITY_CONTEXT_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("as", "part", "of"),
    ("during",),
    ("in",),
    ("while",),
)
_ACTIVITY_START_TOKENS: FrozenSet[str] = frozenset(
    {"alkamisen", "alkoi", "began", "käynnistyi", "started"}
)
_ACTIVITY_END_TOKENS: FrozenSet[str] = frozenset(
    {
        "ended",
        "ends",
        "over",
        "päättyessä",
        "päättyi",
        "päättymistä",
        "päätyttyä",
    }
)
_TOKEN_RE = re.compile(r"\w+|[,;:()/–—]", flags=re.UNICODE)
_SENTENCE_SEGMENT_RE = re.compile(r"([^.!?\n]+)([.!?\n]+|$)")


def _keyword_matches(query: str, keyword: str) -> bool:
    pattern = rf"(?<!\w){re.escape(keyword.lower())}(?!\w)"
    return re.search(pattern, query) is not None


def _sequence_indexes(
    tokens: tuple[str, ...],
    sequence: tuple[str, ...],
) -> List[int]:
    return [
        index
        for index in range(len(tokens) - len(sequence) + 1)
        if tokens[index:index + len(sequence)] == sequence
    ]


def _incident_sequence_end(
    tokens: tuple[str, ...],
    start: int,
    sequence: tuple[str, ...],
) -> Optional[int]:
    """Return the real end of a bounded incident sequence match.

    Incident phrases may contain one explicitly affirmative modifier before or
    between their fixed cue tokens.  Keeping this allowlist and skip count
    narrow prevents negation, uncertainty, or arbitrary prose from turning an
    approximate match into a safety override.
    """
    cursor = start
    modifier_skipped = False
    for expected in sequence:
        if cursor >= len(tokens):
            return None
        if tokens[cursor] != expected:
            if (
                modifier_skipped
                or tokens[cursor] not in _INCIDENT_AFFIRMATIVE_MODIFIER_TOKENS
            ):
                return None
            modifier_skipped = True
            cursor += 1
            if cursor >= len(tokens) or tokens[cursor] != expected:
                return None
        cursor += 1
    return cursor


def _negation_is_affirmative(
    tokens: tuple[str, ...],
    index: int,
) -> bool:
    follower = tokens[index + 1] if index + 1 < len(tokens) else ""
    if follower in _AFFIRMATIVE_NEGATION_FOLLOWERS:
        return True
    return any(
        tokens[index:index + len(sequence)] == sequence
        for sequence in _AFFIRMATIVE_NEGATION_SEQUENCES
    )


def _has_effective_negation(
    tokens: tuple[str, ...],
    start: int,
    end: int,
) -> bool:
    for index in range(max(0, start), min(len(tokens), end)):
        token = tokens[index]
        if token not in _NEGATION_TOKENS:
            continue
        if (
            _negation_is_affirmative(tokens, index)
            or (
                token == "not"
                and index + 1 < len(tokens)
                and tokens[index + 1] in _NON_NEGATING_NOT_FOLLOWERS
            )
        ):
            continue
        return True
    return False


def _is_negated(tokens: tuple[str, ...], index: int) -> bool:
    if (
        index + 1 < len(tokens)
        and tokens[index + 1] in _POSTFIX_NEGATION_TOKENS
    ):
        return True
    start = max(0, index - _NEGATION_LOOKBACK)
    for boundary_index in range(index - 1, start - 1, -1):
        if tokens[boundary_index] in _NEGATION_SCOPE_BREAK_TOKENS:
            start = boundary_index + 1
            break
    return _has_effective_negation(
        tokens,
        start,
        index,
    )


def _has_unnegated_non_incident_activity(tokens: tuple[str, ...]) -> bool:
    return any(
        token in _NON_INCIDENT_ACTIVITY_TOKENS
        and not _is_negated(tokens, index)
        for index, token in enumerate(tokens)
    )


def _activity_prefix_is_compatible(
    tokens: tuple[str, ...],
    prefix_end: int,
    activity_index: int,
) -> bool:
    prefix = tokens[prefix_end:activity_index]
    for index, token in enumerate(prefix):
        if (
            token in _WHILE_ACTIVITY_MODIFIERS
            or token in _NON_INCIDENT_ACTIVITY_TOKENS
            or token in _SAFETY_ACTIVITY_TARGET_TOKENS
            or (
                token == "system"
                and index > 0
                and prefix[index - 1] == "alarm"
            )
        ):
            continue
        return False
    return True


def _is_safety_activity_target(tokens: tuple[str, ...]) -> bool:
    if not tokens:
        return False
    return any(
        tokens[:len(sequence)] == sequence
        and _is_safe_activity_trailing(tokens[len(sequence):])
        for sequence in _SAFETY_ACTIVITY_TARGET_SEQUENCES
    )


def _is_bounded_activity_temporal_detail(tokens: tuple[str, ...]) -> bool:
    if not tokens:
        return True
    if (
        len(tokens) > _MAX_ACTIVITY_TEMPORAL_DETAIL_TOKENS
        or any(token in _CONTROL_SCOPE_BREAK_TOKENS for token in tokens)
    ):
        return False
    if len(tokens) == 1:
        return tokens[0] in (
            _ACTIVITY_TEMPORAL_REFERENCE_TOKENS
            | _ACTIVITY_DAYPART_TOKENS
            | _ACTIVITY_TIME_ADVERB_TOKENS
            | _ACTIVITY_TIME_DIRECTION_TOKENS
        )
    if len(tokens) == 2:
        if (
            tokens[0] == "at"
            and tokens[1] in (
                _ACTIVITY_TEMPORAL_REFERENCE_TOKENS
                | _ACTIVITY_DAYPART_TOKENS
            )
        ):
            return True
        if (
            tokens[0] in _ACTIVITY_DAY_MODIFIER_TOKENS
            and tokens[1] in _ACTIVITY_DAYPART_TOKENS
        ):
            return True
        if tokens[0] in _ACTIVITY_DAYPART_TOKENS and tokens[1] == "meal":
            return True
    if tokens[-1] not in _ACTIVITY_TIME_DIRECTION_TOKENS:
        return False
    quantity = tokens[:-1]
    if quantity[-1:] and quantity[-1] in _ACTIVITY_TIME_UNIT_TOKENS:
        return len(quantity) <= 3
    return all(token in _ACTIVITY_TIME_ADVERB_TOKENS for token in quantity)


def _is_safe_activity_trailing(tokens: tuple[str, ...]) -> bool:
    if not tokens:
        return True
    if all(token in _NON_INCIDENT_ACTIVITY_TOKENS for token in tokens):
        return True
    if tokens in _SAFE_ACTIVITY_TRAILING_SEQUENCES:
        return True

    if tokens[0] in _ACTIVITY_TEMPORAL_PREPOSITIONS:
        detail = tokens[1:]
        return bool(detail) and _is_bounded_activity_temporal_detail(detail)

    predicate = tokens
    if tokens[0] in _ACTIVITY_RELATIVE_PRONOUNS:
        predicate = tokens[1:]
    if not predicate:
        return False
    if predicate[0] in {"is", "was"}:
        state_index = 1
        if (
            state_index < len(predicate)
            and predicate[state_index] in _ACTIVITY_ASPECT_MODIFIERS
        ):
            state_index += 1
        if (
            state_index >= len(predicate)
            or predicate[state_index] != "underway"
        ):
            return False
        detail = predicate[state_index + 1:]
    elif predicate[:2] == ("remained", "underway"):
        detail = predicate[2:]
    elif predicate[0] == "continued":
        detail = predicate[1:]
    elif (
        predicate[0] in _ACTIVITY_START_TOKENS
        or predicate[0] in _ACTIVITY_END_TOKENS
    ):
        detail = predicate[1:]
    else:
        return False
    return _is_bounded_activity_temporal_detail(detail)


def _activity_post_target_is_compatible(
    tokens: tuple[str, ...],
    activity_index: int,
) -> bool:
    suffix = tokens[activity_index + 1:]
    marker_index = 0
    if len(suffix) >= 2 and suffix[0] == "run":
        marker_index = 1
    if (
        marker_index < len(suffix)
        and suffix[marker_index] in _ACTIVITY_POST_TARGET_MARKERS
    ):
        targets = tuple(
            item
            for item in suffix[marker_index + 1:]
            if item not in _ACTIVITY_TARGET_MODIFIERS
        )
        return _is_safety_activity_target(targets)

    if tokens[activity_index] == "testing":
        targets = tuple(
            token
            for token in suffix
            if token not in _ACTIVITY_TARGET_MODIFIERS
        )
        return not targets or _is_safety_activity_target(targets)

    trailing = tuple(
        token for token in suffix if token not in _ACTIVITY_TARGET_MODIFIERS
    )
    return _is_safe_activity_trailing(trailing)


def _is_fire_activity_modifier(
    tokens: tuple[str, ...],
    fire_index: int,
) -> bool:
    search_end = min(len(tokens), fire_index + 5)
    return any(
        tokens[activity_index] in _NON_INCIDENT_ACTIVITY_TOKENS
        and _activity_prefix_is_compatible(tokens, fire_index, activity_index)
        and _activity_post_target_is_compatible(tokens, activity_index)
        for activity_index in range(fire_index + 1, search_end)
    )


def _preceding_control_clause(
    tokens: tuple[str, ...],
    event_start: int,
) -> tuple[str, ...]:
    search_start = max(0, event_start - _ACTIVITY_ATTACHMENT_PADDING)
    for boundary_index in range(event_start - 1, search_start - 1, -1):
        if tokens[boundary_index] not in _CLAUSE_BOUNDARY_TOKENS:
            continue
        clause_start = max(0, boundary_index - _ACTIVITY_ATTACHMENT_PADDING)
        for index in range(boundary_index - 1, clause_start - 1, -1):
            if tokens[index] in _CONTROL_SCOPE_BREAK_TOKENS:
                clause_start = index if tokens[index] == "while" else index + 1
                break
        return tokens[clause_start:boundary_index]
    return ()


def _contains_uncertainty_sequence(tokens: tuple[str, ...]) -> bool:
    return any(
        _sequence_indexes(tokens, sequence)
        for sequence in _UNCERTAINTY_TOKEN_SEQUENCES
    )


def _has_postposed_uncertainty(
    tokens: tuple[str, ...],
    event_end: int,
) -> bool:
    tail = tokens[event_end:event_end + _FIRE_ALARM_CONTEXT_PADDING]
    while tail and tail[0] in _CLAUSE_BOUNDARY_TOKENS:
        tail = tail[1:]
    return any(
        tail[:len(sequence)] == sequence
        for sequence in _POSTPOSED_UNCERTAINTY_SEQUENCES
    )


def _is_uncertain(
    tokens: tuple[str, ...],
    event_start: int,
    event_end: int,
) -> bool:
    lookback_start = max(0, event_start - _FIRE_ALARM_CONTEXT_PADDING)
    for boundary_index in range(event_start - 1, lookback_start - 1, -1):
        if tokens[boundary_index] in _CONTROL_SCOPE_BREAK_TOKENS:
            lookback_start = boundary_index + 1
            break
    if _contains_uncertainty_sequence(tokens[lookback_start:event_start]):
        return True
    if any(token in _UNCERTAINTY_TOKENS for token in tokens[event_start:event_end]):
        return True
    if _has_postposed_uncertainty(tokens, event_end):
        return True

    preceding = _preceding_control_clause(tokens, event_start)
    return any(
        len(preceding) >= len(sequence)
        and preceding[-len(sequence):] == sequence
        for sequence in _UNCERTAINTY_TOKEN_SEQUENCES
    )


def _is_explicit_event_control_clause(tokens: tuple[str, ...]) -> bool:
    matching_prefix = next(
        (
            prefix
            for prefix in _EXPLICIT_EVENT_REFERENCE_PREFIXES
            if tokens[:len(prefix)] == prefix
        ),
        (),
    )
    if not matching_prefix or _has_affirmative_scope_negation(tokens):
        return False
    return any(
        token in _NON_INCIDENT_ACTIVITY_TOKENS
        and not _is_negated(tokens, index)
        and _activity_prefix_is_compatible(tokens, len(matching_prefix), index)
        and _activity_post_target_is_compatible(tokens, index)
        for index, token in enumerate(tokens)
    )


def _following_control_clause(
    tokens: tuple[str, ...],
    event_end: int,
) -> tuple[str, ...]:
    search_end = min(len(tokens), event_end + _FIRE_ALARM_CONTEXT_PADDING)
    for boundary_index in range(event_end, search_end):
        if tokens[boundary_index] not in _CONTROL_SCOPE_BREAK_TOKENS:
            continue
        clause_end = min(
            len(tokens),
            boundary_index + 1 + _FIRE_ALARM_CONTEXT_PADDING,
        )
        for index in range(boundary_index + 1, clause_end):
            if tokens[index] in _CONTROL_SCOPE_BREAK_TOKENS:
                clause_end = index
                break
        clause_start = boundary_index + 1
        if tokens[boundary_index] == "while":
            clause_start = boundary_index
        return tokens[clause_start:clause_end]
    return ()


def _is_explicit_activity_context(tokens: tuple[str, ...]) -> bool:
    activity_indexes = [
        index
        for index, token in enumerate(tokens)
        if token in _NON_INCIDENT_ACTIVITY_TOKENS
        and not _is_negated(tokens, index)
    ]
    if not activity_indexes or _has_affirmative_scope_negation(tokens):
        return False
    if any(
        tokens[index] in _INHERENT_ACTIVITY_CONTEXT_TOKENS
        and _activity_prefix_is_compatible(tokens, 0, index)
        and _activity_post_target_is_compatible(tokens, index)
        for index in activity_indexes
    ):
        return True
    if "aikana" in tokens or "yhteydessä" in tokens:
        return any(
            _activity_prefix_is_compatible(tokens, 0, activity_index)
            and _activity_post_target_is_compatible(tokens, activity_index)
            for activity_index in activity_indexes
        )
    matching_prefix = next(
        (
            prefix
            for prefix in _EXPLICIT_ACTIVITY_CONTEXT_PREFIXES
            if tokens[:len(prefix)] == prefix
        ),
        (),
    )
    if matching_prefix:
        return any(
            _activity_prefix_is_compatible(
                tokens,
                len(matching_prefix),
                activity_index,
            )
            and _activity_post_target_is_compatible(tokens, activity_index)
            for activity_index in activity_indexes
        )
    return False


def _scope_bounds(
    tokens: tuple[str, ...],
    event_start: int,
    event_end: int,
) -> tuple[int, int]:
    scope_start = max(0, event_start - _ACTIVITY_ATTACHMENT_PADDING)
    scope_end = min(len(tokens), event_end + _ACTIVITY_ATTACHMENT_PADDING)
    for index in range(event_start - 1, scope_start - 1, -1):
        if tokens[index] in _CONTROL_SCOPE_BREAK_TOKENS:
            scope_start = index if tokens[index] == "while" else index + 1
            break
    for index in range(event_end, scope_end):
        if tokens[index] in _CONTROL_SCOPE_BREAK_TOKENS:
            scope_end = index
            break
    return scope_start, scope_end


def _is_event_internal_activity_context(
    tokens: tuple[str, ...],
    activity_index: int,
) -> bool:
    clause_start, clause_end = _scope_bounds(
        tokens,
        activity_index,
        activity_index + 1,
    )
    clause = tokens[clause_start:clause_end]
    relative_index = activity_index - clause_start
    for index in range(relative_index + 1, len(clause)):
        if (
            clause[index] == "alarm"
            or clause[index] in _PALOVAROITIN_ACTIVE_TOKENS
        ):
            clause = clause[:index]
            break
    if _is_explicit_activity_context(clause):
        return True
    for prefix in _EXPLICIT_ACTIVITY_CONTEXT_PREFIXES:
        for prefix_start in _sequence_indexes(clause[:relative_index], prefix):
            if _is_explicit_activity_context(clause[prefix_start:]):
                return True
    if _is_explicit_activity_context(clause[relative_index:]):
        return True
    return (
        any(
            token in _SAFETY_ACTIVITY_TARGET_TOKENS
            for token in clause[:relative_index]
        )
        and _activity_prefix_is_compatible(clause, 0, relative_index)
        and _activity_post_target_is_compatible(clause, relative_index)
    )


def _has_local_attached_activity(
    tokens: tuple[str, ...],
    event_start: int,
    event_end: int,
) -> bool:
    scope_start, scope_end = _scope_bounds(tokens, event_start, event_end)
    if _is_explicit_activity_context(tokens[event_end:scope_end]):
        return True
    for activity_index in range(scope_start, scope_end):
        if (
            tokens[activity_index] not in _NON_INCIDENT_ACTIVITY_TOKENS
            or _is_negated(tokens, activity_index)
        ):
            continue
        relation_start = min(activity_index, event_start)
        temporal_end = min(len(tokens), activity_index + _ACTIVITY_ATTACHMENT_PADDING)
        if activity_index < event_start:
            if _is_explicit_activity_context(tokens[scope_start:event_start]):
                return True
            continue

        relation_anchor = (
            event_start if activity_index < event_end else event_end
        )
        between = tokens[relation_anchor:activity_index]
        activity_tail = tokens[activity_index + 1:temporal_end]
        if (
            ("before" in between or "ennen" in between)
            and any(token in _ACTIVITY_END_TOKENS for token in activity_tail)
        ):
            return True
        if (
            "after" in between
            and any(token in _ACTIVITY_END_TOKENS for token in activity_tail)
        ):
            continue
        if (
            ("after" in between or "once" in between)
            and any(token in _ACTIVITY_START_TOKENS for token in activity_tail)
        ):
            return True
        if (
            "jälkeen" in activity_tail
            and any(token in _ACTIVITY_START_TOKENS for token in activity_tail)
        ):
            return True
        internal_activity = (
            event_start <= activity_index < event_end
            and _is_event_internal_activity_context(tokens, activity_index)
        )
        has_non_attached_temporal = any(
            token in _NON_ATTACHED_TEMPORAL_TOKENS
            for token in tokens[relation_start:temporal_end]
        )
        if has_non_attached_temporal:
            explicit_relation = any(
                _sequence_indexes(tokens[scope_start:activity_index], prefix)
                for prefix in _EXPLICIT_ACTIVITY_CONTEXT_PREFIXES
            )
            if internal_activity and explicit_relation:
                return True
            continue
        if internal_activity:
            return True
        if (
            tokens[activity_index] in _INHERENT_ACTIVITY_CONTEXT_TOKENS
            and _is_explicit_activity_context(tokens[activity_index:scope_end])
        ):
            return True
    return False


def _is_bare_denial_clause(tokens: tuple[str, ...]) -> bool:
    words = tuple(token for token in tokens if token not in _CLAUSE_BOUNDARY_TOKENS)
    allowed = _NEGATION_TOKENS | {
        "absolutely",
        "certainly",
        "definitely",
        "probably",
    }
    return (
        bool(words)
        and any(token in _NEGATION_TOKENS for token in words)
        and all(token in allowed for token in words)
    )


def _has_negated_non_incident_activity(tokens: tuple[str, ...]) -> bool:
    for index, token in enumerate(tokens):
        if token not in _NON_INCIDENT_ACTIVITY_TOKENS:
            continue
        if (
            index + 1 < len(tokens)
            and tokens[index + 1] in _POSTFIX_NEGATION_TOKENS
        ):
            return True
        start = max(0, index - _CORRECTION_CONTEXT_PADDING)
        for boundary_index in range(index - 1, start - 1, -1):
            if tokens[boundary_index] in _CONTROL_SCOPE_BREAK_TOKENS:
                start = boundary_index + 1
                break
        if _has_effective_negation(tokens, start, index):
            return True
    return False


def _has_affirmative_scope_negation(tokens: tuple[str, ...]) -> bool:
    if any(
        _sequence_indexes(tokens, sequence)
        for sequence in _AFFIRMATIVE_SCOPE_NEGATION_SEQUENCES
    ):
        return True
    for index, token in enumerate(tokens):
        if token not in _NEGATION_TOKENS:
            continue
        lookahead = tokens[index + 1:index + 5]
        if any(
            follower in _AFFIRMATIVE_NEGATION_FOLLOWERS
            for follower in lookahead
        ):
            return True
    return bool(tokens[:1]) and tokens[0] in _AFFIRMATIVE_NEGATION_FOLLOWERS


def _starts_with_event_reference(tokens: tuple[str, ...]) -> bool:
    return any(
        tokens[:len(prefix)] == prefix
        for prefix in _EXPLICIT_EVENT_REFERENCE_PREFIXES
    )


def _is_question_response_correction(tokens: tuple[str, ...]) -> bool:
    words = tuple(token for token in tokens if token not in _CLAUSE_BOUNDARY_TOKENS)
    if words[:1] == ("actually",):
        words = words[1:]
    if _has_negated_non_incident_activity(tokens):
        return False
    if _is_bare_denial_clause(words):
        return True
    if words in {
        ("en", "usko"),
        ("i", "don", "t", "think", "so"),
    }:
        return True
    if words[:1] not in {("ei",), ("no",)}:
        return False
    tail = words[1:]
    if not tail:
        return True
    if _has_affirmative_scope_negation(tail):
        return False
    if not _starts_with_event_reference(tail):
        return False
    return (
        _is_explicit_event_control_clause(tail)
        or any(token in _NON_INCIDENT_REPLACEMENT_TOKENS for token in tail)
    )


def _is_statement_correction(tokens: tuple[str, ...]) -> bool:
    bounded = tokens[:_CORRECTION_CONTEXT_PADDING]
    words = tuple(token for token in bounded if token not in _CLAUSE_BOUNDARY_TOKENS)
    if _is_bare_denial_clause(bounded) or words in {
        ("en", "usko"),
        ("i", "don", "t", "think", "so"),
    }:
        return True
    if words[:1] == ("actually",):
        words = words[1:]
    for prefix in _CORRECTION_DISCOURSE_PREFIXES:
        if words[:len(prefix)] == prefix:
            words = words[len(prefix):]
            break
    if words[:1] in {("ei",), ("no",)}:
        words = words[1:]
    if not words or _has_affirmative_scope_negation(words):
        return False
    return _is_explicit_event_control_clause(words)


def _postposed_correction_applies(
    tokens: tuple[str, ...],
    event_end: int,
    correction_tokens: tuple[str, ...],
    *,
    question_segment: bool,
) -> bool:
    remaining = len(tokens) - event_end
    trailing = tokens[event_end:event_end + _CORRECTION_CONTEXT_PADDING]
    if _has_negated_non_incident_activity(trailing):
        return False
    if _is_statement_correction(trailing):
        return True
    if _is_statement_correction(_following_control_clause(tokens, event_end)):
        return True
    # A next-sentence correction can only bind to an event near the end of the
    # current segment.  The fixed cap also avoids rescanning an unbounded suffix
    # for every repeated trigger.
    if remaining > _CORRECTION_CONTEXT_PADDING:
        return False
    for boundary_index, token in enumerate(trailing):
        if token not in _CONTROL_SCOPE_BREAK_TOKENS:
            continue
        if any(
            item not in _CONTROL_SCOPE_BREAK_TOKENS
            for item in trailing[boundary_index + 1:]
        ):
            return False
    if question_segment:
        return _is_question_response_correction(correction_tokens)
    return _is_statement_correction(correction_tokens)


def _is_anchor_negated(tokens: tuple[str, ...], event_start: int) -> bool:
    lookback_start = max(0, event_start - _NEGATION_LOOKBACK)
    for boundary_index in range(event_start - 1, lookback_start - 1, -1):
        if tokens[boundary_index] in _NEGATION_SCOPE_BREAK_TOKENS:
            lookback_start = boundary_index + 1
            break
    lookback = tokens[lookback_start:event_start]
    for index, token in enumerate(lookback):
        if token not in _NEGATION_TOKENS:
            continue
        follower = lookback[index + 1] if index + 1 < len(lookback) else ""
        if _negation_is_affirmative(lookback, index) or follower == "sure":
            continue
        return True
    return _is_bare_denial_clause(
        _preceding_control_clause(tokens, event_start)
    )


def _has_attached_non_incident_activity(
    tokens: tuple[str, ...],
    event_start: int,
    event_end: int,
) -> bool:
    if _has_incident_affirmation(tokens, event_start, event_end):
        return False
    if _has_local_attached_activity(tokens, event_start, event_end):
        return True
    following = _following_control_clause(tokens, event_end)
    return (
        _is_explicit_event_control_clause(following)
        or _is_explicit_activity_context(following)
        or _is_explicit_activity_context(
            _preceding_control_clause(tokens, event_start)
        )
    )


def _has_incident_affirmation(
    tokens: tuple[str, ...],
    event_start: int,
    event_end: int,
) -> bool:
    scope_start = max(0, event_start - _INCIDENT_AFFIRMATION_PADDING)
    scope_end = min(len(tokens), event_end + _INCIDENT_AFFIRMATION_PADDING)
    context = tokens[scope_start:scope_end]

    def is_directly_negated(sequence_start: int, sequence_end: int) -> bool:
        lookback_start = max(0, sequence_start - 3)
        for index in range(sequence_start - 1, lookback_start - 1, -1):
            if tokens[index] in _NEGATION_SCOPE_BREAK_TOKENS:
                lookback_start = index + 1
                break
        lookback = list(tokens[lookback_start:sequence_start])
        while lookback and lookback[-1] in {"a", "an", "any", "the"}:
            lookback.pop()
        if lookback and lookback[-1] in _NEGATION_TOKENS:
            return True
        post = tokens[
            sequence_end:sequence_end + _INCIDENT_AFFIRMATION_BINDING_LOOKAROUND
        ]
        return any(
            post[:len(sequence)] == sequence
            for sequence in _POSTPOSED_INCIDENT_DENIAL_SEQUENCES
        )

    def is_bound(
        sequence_start: int,
        sequence_end: int,
        *,
        allow_proximity: bool = False,
    ) -> bool:
        pre_start = max(
            0,
            sequence_start - _INCIDENT_AFFIRMATION_BINDING_LOOKAROUND,
        )
        pre = tokens[pre_start:sequence_start]
        post = tokens[
            sequence_end:sequence_end + _INCIDENT_AFFIRMATION_BINDING_LOOKAROUND
        ]
        if "because" in pre or (
            "from" in pre and sequence_start >= event_end
        ):
            return True
        local_pre_offset = 0
        for index, token in enumerate(pre):
            if token in _CONTROL_SCOPE_BREAK_TOKENS:
                local_pre_offset = index + 1
        local_pre = pre[local_pre_offset:]
        local_pre_start = pre_start + local_pre_offset
        reference_parts = list(local_pre)
        while reference_parts and reference_parts[-1] in (
            {"a", "an", "the"} | _INCIDENT_REFERENCE_MODIFIERS
        ):
            reference_parts.pop()
        reference_pre = tuple(reference_parts)
        if any(
            len(reference_pre) >= len(prefix)
            and reference_pre[-len(prefix):] == prefix
            for prefix in (
                *_EXPLICIT_EVENT_REFERENCE_PREFIXES,
                ("but", "it", "is"),
                ("it", "is"),
                ("there", "is"),
                ("there", "is", "a"),
                ("there", "is", "an"),
                ("there", "was"),
                ("kyseessä", "on"),
                ("kyseessä", "oli"),
                ("it", "was", "caused", "by"),
                ("this", "was", "caused", "by"),
                ("the", "cause", "was"),
            )
        ):
            return True
        matched_confirmation = next(
            (
                sequence
                for sequence in _POSTPOSED_INCIDENT_CONFIRMATION_SEQUENCES
                if post[:len(sequence)] == sequence
            ),
            (),
        )
        has_post_confirmation = bool(matched_confirmation)
        if not has_post_confirmation and post[:1] in {
            ("is",),
            ("remains",),
            ("was",),
        }:
            state_index = 1
            if (
                state_index < len(post)
                and post[state_index] in _INCIDENT_REFERENCE_MODIFIERS
            ):
                state_index += 1
            has_post_confirmation = (
                state_index < len(post)
                and post[state_index] in _INCIDENT_POSITIVE_STATE_TOKENS
            )
        if matched_confirmation in {
            ("detected",),
            ("exists",),
            ("has", "been", "detected"),
        }:
            qualifier = post[len(matched_confirmation):]
            if qualifier[:1] in {("by",), ("in",)} and any(
                token in (
                    _NON_INCIDENT_ACTIVITY_TOKENS
                    | _INCIDENT_AFFIRMATION_ACTION_TOKENS
                )
                for token in qualifier[:4]
            ):
                has_post_confirmation = False
        if has_post_confirmation:
            return True
        if any(
            _sequence_indexes(local_pre, sequence)
            for sequence in _AFFIRMATIVE_NEGATION_SEQUENCES
        ) or any(
            token in _NEGATION_TOKENS
            and _negation_is_affirmative(local_pre, index)
            for index, token in enumerate(local_pre)
        ):
            return True
        for index, token in enumerate(local_pre):
            if token not in {"no", "nobody"}:
                continue
            predicate = local_pre[index + 1:]
            if not predicate or predicate[0] in {"database", "need"}:
                continue
            if any(
                item in _INCIDENT_AFFIRMATION_NEGATED_PREDICATES
                for item in predicate
            ) or _sequence_indexes(predicate, ("failed", "to", "detect")):
                return True
        if any(
            token in _INCIDENT_AFFIRMATION_ACTIVE_PREDICATES
            and not _is_negated(tokens, local_pre_start + index)
            and "nobody" not in local_pre[:index]
            and not _contains_uncertainty_sequence(local_pre[:index + 1])
            for index, token in enumerate(local_pre)
        ):
            return True
        if not allow_proximity:
            return False
        if sequence_end <= event_start:
            relation = tokens[sequence_end:event_start]
        elif sequence_start >= event_end:
            relation = tokens[event_end:sequence_start]
        else:
            relation = ()
        return (
            len(relation) <= _INCIDENT_AFFIRMATION_BINDING_LOOKAROUND
            and not any(
                token in _INCIDENT_AFFIRMATION_ACTION_TOKENS
                for token in relation
            )
        )

    for sequence in _NEGATED_SIMULATION_AFFIRMATION_SEQUENCES:
        for relative_index in _sequence_indexes(context, sequence):
            sequence_start = scope_start + relative_index
            sequence_end = sequence_start + len(sequence)
            if is_bound(sequence_start, sequence_end, allow_proximity=True):
                return True

    excluded_followers = (
        _NON_INCIDENT_ACTIVITY_TOKENS | _SAFETY_ACTIVITY_TARGET_TOKENS
        | _INCIDENT_AFFIRMATION_ACTION_TOKENS
    )
    for sequence in _INCIDENT_AFFIRMATION_SEQUENCES:
        for relative_index in _sequence_indexes(context, sequence):
            sequence_start = scope_start + relative_index
            sequence_end = sequence_start + len(sequence)
            follower = tokens[sequence_end] if sequence_end < len(tokens) else ""
            if (
                not is_directly_negated(sequence_start, sequence_end)
                and follower not in excluded_followers
                and is_bound(sequence_start, sequence_end)
            ):
                return True
    return False


def _fire_alarm_pair_crosses_unrelated_clause(
    tokens: tuple[str, ...],
    fire_index: int,
    alarm_index: int,
) -> bool:
    pair_start = min(fire_index, alarm_index)
    pair_end = max(fire_index, alarm_index)
    between = tokens[pair_start + 1:pair_end]
    if not any(token in _CLAUSE_BOUNDARY_TOKENS for token in between):
        return False
    for index in range(pair_start + 1, pair_end):
        if tokens[index] != "alarm":
            continue
        cue_index = index + 1
        if (
            cue_index < pair_end
            and tokens[cue_index] in _ALARM_DESCRIPTOR_TOKENS
        ):
            cue_index += 1
        if any(
            _incident_sequence_end(tokens, cue_index, suffix) is not None
            for suffix in _ACTIVE_ALARM_SUFFIXES
        ):
            return True
    if fire_index < alarm_index:
        return False
    return not (
        "because" in between
        or "from" in between
        or _sequence_indexes(between, ("there", "is"))
        or _has_incident_affirmation(tokens, fire_index, fire_index + 1)
    )


def _has_active_fire_alarm(
    tokens: tuple[str, ...],
    correction_tokens: tuple[str, ...] = (),
    *,
    question_segment: bool = False,
) -> bool:
    """Recognize bounded fire + active-alarm descriptions.

    Fire and alarm need not be adjacent, but they must remain close and the
    alarm must have an explicit activation cue.  Drill/test vocabulary in the
    same local window keeps simulated activity on its typed route.
    """
    for alarm_index, token in enumerate(tokens):
        if token != "alarm":
            continue
        cue_index = alarm_index + 1
        if (
            cue_index < len(tokens)
            and tokens[cue_index] in _ALARM_DESCRIPTOR_TOKENS
        ):
            cue_index += 1
        suffix_end = None
        for suffix in _ACTIVE_ALARM_SUFFIXES:
            candidate_end = _incident_sequence_end(tokens, cue_index, suffix)
            if candidate_end is not None:
                suffix_end = candidate_end
                break
        else:
            continue

        # Scan only the fixed local window.  This makes the matcher O(n) even
        # for adversarial input containing thousands of fire/alarm tokens.
        fire_start = max(0, alarm_index - _MAX_FIRE_ALARM_TOKEN_DISTANCE)
        fire_end = min(len(tokens), alarm_index + _MAX_FIRE_ALARM_TOKEN_DISTANCE + 1)
        for fire_index in range(fire_start, fire_end):
            if tokens[fire_index] != "fire":
                continue
            if tokens[fire_index + 1:fire_index + 2] == ("sale",):
                continue
            if _is_fire_activity_modifier(tokens, fire_index):
                continue
            if _fire_alarm_pair_crosses_unrelated_clause(
                tokens,
                fire_index,
                alarm_index,
            ):
                continue
            event_start = min(fire_index, alarm_index)
            event_end = max(fire_index + 1, suffix_end)
            if (
                not _is_anchor_negated(tokens, event_start)
                and not _is_uncertain(tokens, event_start, event_end)
                and not _has_attached_non_incident_activity(
                    tokens,
                    event_start,
                    event_end,
                )
                and not _postposed_correction_applies(
                    tokens,
                    event_end,
                    correction_tokens,
                    question_segment=question_segment,
                )
            ):
                return True
    return False


def _has_active_palovaroitin(
    tokens: tuple[str, ...],
    correction_tokens: tuple[str, ...] = (),
    *,
    question_segment: bool = False,
) -> bool:
    for alarm_index, token in enumerate(tokens):
        if token != "palovaroitin":
            continue
        cue_end = min(
            len(tokens),
            alarm_index + _MAX_PALOVAROITIN_CUE_DISTANCE + 1,
        )
        for cue_index in range(alarm_index + 1, cue_end):
            if tokens[cue_index] not in _PALOVAROITIN_ACTIVE_TOKENS:
                continue
            if (
                not _is_negated(tokens, cue_index)
                and not _is_anchor_negated(tokens, alarm_index)
                and not _is_uncertain(tokens, alarm_index, cue_index + 1)
                and not _has_attached_non_incident_activity(
                    tokens,
                    alarm_index,
                    cue_index + 1,
                )
                and not _postposed_correction_applies(
                    tokens,
                    cue_index + 1,
                    correction_tokens,
                    question_segment=question_segment,
                )
            ):
                return True
    return False


def _has_safety_override(query: str) -> bool:
    if not query:
        return False
    # Keep anchors within one sentence-like segment so unrelated statements
    # cannot combine into an incident. Clause punctuation is retained as a
    # scope marker: fire/alarm anchors may span it, but activity suppressors may
    # not mask an incident from an unrelated adjacent clause.
    segments = [
        (match.group(1), match.group(2))
        for match in _SENTENCE_SEGMENT_RE.finditer(query.lower())
        if match.group(1).strip()
    ]
    for segment_index, (segment, delimiter) in enumerate(segments):
        tokens = tuple(_TOKEN_RE.findall(segment))
        if not tokens:
            continue
        question_segment = "?" in delimiter
        correction_tokens: tuple[str, ...] = ()
        if segment_index + 1 < len(segments):
            next_tokens = tuple(_TOKEN_RE.findall(segments[segment_index + 1][0]))
            bounded_next_tokens = next_tokens[:_CORRECTION_CONTEXT_PADDING]
            correction_tokens = bounded_next_tokens
            if _is_explicit_event_control_clause(bounded_next_tokens):
                tokens += (";",) + bounded_next_tokens
        for index in range(len(tokens)):
            for sequence in _SMOKE_DETECTION_TOKEN_SEQUENCES:
                sequence_end = _incident_sequence_end(tokens, index, sequence)
                if sequence_end is None:
                    continue
                if (
                    not _is_negated(tokens, index)
                    and not _is_anchor_negated(tokens, index)
                    and not _is_uncertain(
                        tokens,
                        index,
                        sequence_end,
                    )
                    and not _has_attached_non_incident_activity(
                        tokens,
                        index,
                        sequence_end,
                    )
                    and not _postposed_correction_applies(
                        tokens,
                        sequence_end,
                        correction_tokens,
                        question_segment=question_segment,
                    )
                ):
                    return True
        if _has_active_palovaroitin(
            tokens,
            correction_tokens,
            question_segment=question_segment,
        ) or _has_active_fire_alarm(
            tokens,
            correction_tokens,
            question_segment=question_segment,
        ):
            return True
    return False


@dataclass
class CellAssignment:
    """Result of assigning a query to a hex cell."""
    cell_id: str
    intent: str
    method: str  # "intent" | "keyword" | "default"
    neighbors_ring1: List[str] = field(default_factory=list)
    neighbors_ring2: List[str] = field(default_factory=list)

    def all_neighbor_cells(self) -> List[str]:
        """Return ring-1 + ring-2 neighbors (deduplicated, excludes self)."""
        seen: Set[str] = set()
        result: List[str] = []
        for c in self.neighbors_ring1 + self.neighbors_ring2:
            if c not in seen and c != self.cell_id:
                seen.add(c)
                result.append(c)
        return result


class HexCellTopology:
    """Deterministic hex-cell topology for hybrid retrieval.

    Maps queries to logical cells, provides neighbor lookups,
    and supports ring-1 and ring-2 traversal.
    """

    def __init__(self) -> None:
        self._adjacency = dict(_ADJACENCY)
        self._stats = {
            "assignments": 0,
            "by_intent": 0,
            "by_keyword": 0,
            "by_default": 0,
            "cell_counts": {c: 0 for c in ALL_CELLS},
        }

    def assign_cell(self, intent: str, query: str = "") -> CellAssignment:
        """Assign a query to a hex cell based on intent and keywords.

        Priority:
          1. High-confidence safety incident override
          2. Direct intent mapping (from SolverRouter.classify_intent)
          3. Keyword scan (if intent is general or unmapped)
          4. Default to CELL_GENERAL
        """
        cell_id: Optional[str] = None
        method = "default"

        # 1. High-confidence incidents must not be hidden by an ordinary
        # inferred intent.  Ambiguous safety-domain words remain part of the
        # ordinary general-intent keyword scan below.
        if _has_safety_override(query):
            cell_id = CELL_SAFETY
            method = "keyword"

        # 2. Intent mapping
        elif intent in _INTENT_TO_CELL:
            cell_id = _INTENT_TO_CELL[intent]
            method = "intent"

        # 3. Keyword fallback for general or unmapped intents
        if cell_id is None or cell_id == CELL_GENERAL:
            keyword_cell = self._keyword_scan(query)
            if keyword_cell is not None:
                cell_id = keyword_cell
                method = "keyword"

        # 4. Default
        if cell_id is None:
            cell_id = CELL_GENERAL
            method = "default"

        # Neighbors
        ring1 = sorted(self._adjacency.get(cell_id, frozenset()))
        ring2 = self._ring2(cell_id)

        # Stats
        self._stats["assignments"] += 1
        self._stats[f"by_{method}"] += 1
        self._stats["cell_counts"][cell_id] = self._stats["cell_counts"].get(cell_id, 0) + 1

        return CellAssignment(
            cell_id=cell_id,
            intent=intent,
            method=method,
            neighbors_ring1=ring1,
            neighbors_ring2=ring2,
        )

    def get_neighbors(self, cell_id: str, max_ring: int = 1) -> List[str]:
        """Get neighbor cell IDs up to max_ring hops."""
        ring1 = sorted(self._adjacency.get(cell_id, frozenset()))
        if max_ring <= 1:
            return ring1
        ring2 = self._ring2(cell_id)
        return ring1 + ring2

    def stats(self) -> dict:
        return dict(self._stats)

    # ── Internal ──────────────────────────────────────────────

    def _keyword_scan(self, query: str) -> Optional[str]:
        """Scan query for domain keywords. Returns best-matching cell or None."""
        if not query:
            return None
        q = query.lower()
        best_cell: Optional[str] = None
        best_count = 0
        for cell_id, keywords in _DOMAIN_KEYWORDS.items():
            count = sum(1 for kw in keywords if _keyword_matches(q, kw))
            if count > best_count:
                best_count = count
                best_cell = cell_id
        return best_cell if best_count > 0 else None

    def _ring2(self, cell_id: str) -> List[str]:
        """Compute ring-2 neighbors (neighbor-of-neighbor, excluding self and ring-1)."""
        ring1 = self._adjacency.get(cell_id, frozenset())
        ring2: Set[str] = set()
        for n in ring1:
            for nn in self._adjacency.get(n, frozenset()):
                if nn != cell_id and nn not in ring1:
                    ring2.add(nn)
        return sorted(ring2)
