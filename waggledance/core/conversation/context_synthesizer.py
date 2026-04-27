# SPDX-License-Identifier: BUSL-1.1
"""Context synthesizer — Phase 9 §V.

Pure function: given current state slices + presence_log history,
produce a deterministic conversational context bundle the meta_dialogue
layer renders into output.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .presence_log import PresenceEntry


@dataclass(frozen=True)
class ContextBundle:
    """Materials a meta_dialogue turn may reference."""
    references_to_past: tuple[str, ...]
    current_uncertainty_summary: tuple[str, ...]
    blind_spots_surfaced: tuple[str, ...]
    delta_since_last_turn: tuple[str, ...]
    learning_intents: tuple[str, ...]
    forbidden_substrings: frozenset[str]
    confidence_overclaim_substrings: frozenset[str]

    def to_dict(self) -> dict:
        return {
            "references_to_past": list(self.references_to_past),
            "current_uncertainty_summary":
                list(self.current_uncertainty_summary),
            "blind_spots_surfaced": list(self.blind_spots_surfaced),
            "delta_since_last_turn": list(self.delta_since_last_turn),
            "learning_intents": list(self.learning_intents),
        }


# ── YAML-lite parsers (avoid PyYAML hard dependency) ──────────────

def _parse_simple_list_under(yaml_text: str, key: str) -> list[str]:
    """Extract list values under a top-level key in a YAML-lite file
    of the form:
        key:
          - "value 1"
          - value_2
    """
    out: list[str] = []
    in_section = False
    for raw in yaml_text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.startswith(f"{key}:"):
            in_section = True
            continue
        if in_section:
            if line and not line.startswith(" ") and not stripped.startswith("-"):
                # Reached a sibling top-level key
                break
            if stripped.startswith("- "):
                v = stripped[2:].strip()
                if v.startswith('"') and v.endswith('"'):
                    v = v[1:-1]
                if v.startswith("'") and v.endswith("'"):
                    v = v[1:-1]
                out.append(v)
    return out


def load_forbidden_patterns(path: Path | str) -> tuple[frozenset[str],
                                                              frozenset[str]]:
    """Returns (forbidden_substrings, confidence_overclaim_substrings)
    from the YAML file at path. Both as case-insensitive lowercase
    frozensets for substring scanning."""
    p = Path(path)
    if not p.exists():
        return frozenset(), frozenset()
    text = p.read_text(encoding="utf-8")
    forb = _parse_simple_list_under(text, "forbidden_substrings")
    over = _parse_simple_list_under(text, "confidence_overclaim_substrings")
    return (
        frozenset(s.lower() for s in forb),
        frozenset(s.lower() for s in over),
    )


def load_personality_self_reference(path: Path | str) -> tuple[
    tuple[str, ...], tuple[str, ...]
]:
    """Returns (may_say, never_say) phrase lists."""
    p = Path(path)
    if not p.exists():
        return (), ()
    text = p.read_text(encoding="utf-8")
    may = _parse_simple_list_under(text, "may_say")
    never = _parse_simple_list_under(text, "never_say")
    return tuple(may), tuple(never)


# ── Context synthesis ────────────────────────────────────────────-

def synthesize(*,
                  recent_presence: list[PresenceEntry],
                  self_model: dict | None = None,
                  prev_self_model: dict | None = None,
                  forbidden_patterns_path: Path | str,
                  max_references: int = 3,
                  ) -> ContextBundle:
    """Build a deterministic context bundle. Same inputs ALWAYS
    produce the same bundle."""
    forb, over = load_forbidden_patterns(forbidden_patterns_path)

    # references_to_past: most-recent N turn excerpts (deterministic
    # by entry order)
    refs = []
    for e in reversed(recent_presence):
        if e.kind == "turn" and e.user_turn_excerpt:
            refs.append(e.user_turn_excerpt[:120])
        if len(refs) >= max_references:
            break
    refs.reverse()

    # current_uncertainty_summary: low-confidence facts from self_model
    uncertainty: list[str] = []
    if self_model:
        for t in self_model.get("workspace_tensions") or []:
            if t.get("severity") in ("high", "medium"):
                uncertainty.append(
                    f"open tension: {t.get('type', 'unknown')} "
                    f"({t.get('severity')})"
                )

    # blind_spots_surfaced
    blind: list[str] = []
    if self_model:
        for bs in self_model.get("blind_spots") or []:
            blind.append(
                f"blind spot: {bs.get('domain', '?')} ({bs.get('severity', '?')})"
            )

    # delta_since_last_turn
    delta: list[str] = []
    if self_model and prev_self_model:
        prev_t_ids = {t.get("tension_id") for t in
                       (prev_self_model.get("workspace_tensions") or [])}
        cur_t_ids = {t.get("tension_id") for t in
                      (self_model.get("workspace_tensions") or [])}
        added = cur_t_ids - prev_t_ids
        resolved = prev_t_ids - cur_t_ids
        for tid in sorted(added):
            if tid:
                delta.append(f"new tension since last turn: {tid}")
        for tid in sorted(resolved):
            if tid:
                delta.append(f"resolved tension since last turn: {tid}")

    # learning_intents: high-severity blind spots + persisting tensions
    intents: list[str] = []
    if self_model:
        for bs in self_model.get("blind_spots") or []:
            if bs.get("severity") == "high":
                intents.append(
                    f"want to learn: cover blind spot {bs.get('domain')}"
                )
        for t in self_model.get("workspace_tensions") or []:
            if t.get("lifecycle_status") == "persisting":
                intents.append(
                    f"want to resolve: tension {t.get('tension_id')}"
                )

    return ContextBundle(
        references_to_past=tuple(refs),
        current_uncertainty_summary=tuple(uncertainty),
        blind_spots_surfaced=tuple(blind),
        delta_since_last_turn=tuple(delta),
        learning_intents=tuple(intents),
        forbidden_substrings=forb,
        confidence_overclaim_substrings=over,
    )


# ── Forbidden-pattern scanner ────────────────────────────────────-

@dataclass(frozen=True)
class PatternViolation:
    pattern: str
    kind: str            # "forbidden" | "confidence_overclaim"
    location: int        # char offset


def scan_for_forbidden(text: str,
                            forbidden: frozenset[str],
                            overclaim: frozenset[str],
                            ) -> list[PatternViolation]:
    """Returns deterministic ordered list of violations
    (sorted by char offset, then pattern)."""
    out: list[PatternViolation] = []
    text_l = text.lower()
    for pat in forbidden:
        idx = text_l.find(pat)
        while idx != -1:
            out.append(PatternViolation(
                pattern=pat, kind="forbidden", location=idx,
            ))
            idx = text_l.find(pat, idx + 1)
    for pat in overclaim:
        idx = text_l.find(pat)
        while idx != -1:
            out.append(PatternViolation(
                pattern=pat, kind="confidence_overclaim", location=idx,
            ))
            idx = text_l.find(pat, idx + 1)
    out.sort(key=lambda v: (v.location, v.pattern))
    return out
