"""Meta-dialogue — Phase 9 §V.

Renders meta-conversational responses to questions like:
- "what changed in you since last time?"
- "what would you want to learn next?"
- "why do you believe X?"

Pure functions: takes a ContextBundle + a question kind, returns a
deterministic structured response. Style enforcement (forbidden
patterns, calibration phrasing) happens at render time.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .context_synthesizer import ContextBundle, PatternViolation, scan_for_forbidden


META_QUESTION_KINDS = (
    "what_changed_since_last_time",
    "what_would_you_learn_next",
    "why_do_you_believe",
    "what_are_you_uncertain_about",
    "what_are_your_blind_spots",
)


@dataclass(frozen=True)
class MetaResponse:
    question_kind: str
    structured_lines: tuple[str, ...]
    references: tuple[str, ...]
    rendered_text: str
    pattern_violations: tuple[PatternViolation, ...]

    @property
    def is_clean(self) -> bool:
        return all(v.kind != "forbidden" for v in self.pattern_violations)

    def to_dict(self) -> dict:
        return {
            "question_kind": self.question_kind,
            "structured_lines": list(self.structured_lines),
            "references": list(self.references),
            "rendered_text": self.rendered_text,
            "pattern_violations": [
                {"pattern": v.pattern, "kind": v.kind,
                 "location": v.location}
                for v in self.pattern_violations
            ],
            "is_clean": self.is_clean,
        }


def respond_what_changed(ctx: ContextBundle) -> MetaResponse:
    lines = list(ctx.delta_since_last_turn)
    if not lines:
        lines = ["I don't see a structural change since our last turn."]
    text = "\n".join(["Since last time:"] + [f"- {l}" for l in lines])
    violations = scan_for_forbidden(
        text, ctx.forbidden_substrings, ctx.confidence_overclaim_substrings,
    )
    return MetaResponse(
        question_kind="what_changed_since_last_time",
        structured_lines=tuple(lines),
        references=ctx.references_to_past,
        rendered_text=text,
        pattern_violations=tuple(violations),
    )


def respond_what_to_learn(ctx: ContextBundle) -> MetaResponse:
    lines = list(ctx.learning_intents)
    if not lines:
        lines = ["I don't currently have a strong learning intent."]
    text = "\n".join(["I would want to learn next:"] + [f"- {l}" for l in lines])
    violations = scan_for_forbidden(
        text, ctx.forbidden_substrings, ctx.confidence_overclaim_substrings,
    )
    return MetaResponse(
        question_kind="what_would_you_learn_next",
        structured_lines=tuple(lines),
        references=ctx.references_to_past,
        rendered_text=text,
        pattern_violations=tuple(violations),
    )


def respond_uncertainty(ctx: ContextBundle) -> MetaResponse:
    lines = list(ctx.current_uncertainty_summary)
    if not lines:
        lines = ["I don't have a notable open uncertainty right now."]
    text = "\n".join(["My current open uncertainty:"] + [f"- {l}" for l in lines])
    violations = scan_for_forbidden(
        text, ctx.forbidden_substrings, ctx.confidence_overclaim_substrings,
    )
    return MetaResponse(
        question_kind="what_are_you_uncertain_about",
        structured_lines=tuple(lines),
        references=ctx.references_to_past,
        rendered_text=text,
        pattern_violations=tuple(violations),
    )


def respond_blind_spots(ctx: ContextBundle) -> MetaResponse:
    lines = list(ctx.blind_spots_surfaced)
    if not lines:
        lines = ["No blind spots currently surfaced."]
    text = "\n".join(["My current blind spots:"] + [f"- {l}" for l in lines])
    violations = scan_for_forbidden(
        text, ctx.forbidden_substrings, ctx.confidence_overclaim_substrings,
    )
    return MetaResponse(
        question_kind="what_are_your_blind_spots",
        structured_lines=tuple(lines),
        references=ctx.references_to_past,
        rendered_text=text,
        pattern_violations=tuple(violations),
    )


def respond_why_do_you_believe(ctx: ContextBundle) -> MetaResponse:
    """Generic 'why' answer: surface the references_to_past + the
    uncertainty + the blind spots that bear on a current claim."""
    lines: list[str] = []
    if ctx.references_to_past:
        lines.append("Based on our prior turns: "
                      + "; ".join(ctx.references_to_past))
    if ctx.current_uncertainty_summary:
        lines.append("Open uncertainty I weigh: "
                      + "; ".join(ctx.current_uncertainty_summary))
    if not lines:
        lines = ["I don't have enough context to give a calibrated reason."]
    text = "\n".join(["Why I believe what I believe:"]
                       + [f"- {l}" for l in lines])
    violations = scan_for_forbidden(
        text, ctx.forbidden_substrings, ctx.confidence_overclaim_substrings,
    )
    return MetaResponse(
        question_kind="why_do_you_believe",
        structured_lines=tuple(lines),
        references=ctx.references_to_past,
        rendered_text=text,
        pattern_violations=tuple(violations),
    )


_DISPATCH = {
    "what_changed_since_last_time": respond_what_changed,
    "what_would_you_learn_next": respond_what_to_learn,
    "what_are_you_uncertain_about": respond_uncertainty,
    "what_are_your_blind_spots": respond_blind_spots,
    "why_do_you_believe": respond_why_do_you_believe,
}


def respond(question_kind: str, ctx: ContextBundle) -> MetaResponse:
    if question_kind not in _DISPATCH:
        raise ValueError(
            f"unknown meta question kind: {question_kind!r}; "
            f"allowed: {sorted(_DISPATCH.keys())}"
        )
    return _DISPATCH[question_kind](ctx)
