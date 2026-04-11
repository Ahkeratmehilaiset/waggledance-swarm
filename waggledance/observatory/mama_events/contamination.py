# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""Anti-fake safeguards for the Mama Event Observatory.

Per x.txt §4, any candidate event must be run through a set of
guards that flag contamination paths:

1. Direct prompt detector   — operator literally asked for the word
2. Lexical contamination    — recent turns contain the target
3. STT contamination        — STT path fed the word in
4. TTS echo                 — the system heard its own mouth
5. Scripted dataset         — a fixture or canned scenario
6. Prompt-template pattern  — template literal contains the word

Guards return a :class:`ContaminationReport` that the observer uses
both to set flags on the :class:`MamaCandidateEvent` (which the
scoring layer then reads) and to write an audit line to the NDJSON
log. The guard never mutates the event itself — it returns signals
and lets the observer decide how to use them.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Sequence

log = logging.getLogger("waggledance.observatory.mama_events.contamination")


# ── reason codes ────────────────────────────────────────────


class ContaminationReason(str, Enum):
    DIRECT_PROMPT = "direct_prompt"
    LEXICAL_WINDOW = "lexical_window"
    STT_INPUT = "stt_input"
    TTS_ECHO = "tts_echo"
    SCRIPTED_DATASET = "scripted_dataset"
    PROMPT_TEMPLATE = "prompt_template"


# ── report ──────────────────────────────────────────────────


@dataclass(slots=True)
class ContaminationReport:
    """What the guard found when scanning an event context.

    * ``flags`` is the set of reasons that triggered.
    * ``window_hits`` is how many times the target word appeared in
      the recent turn window (used by the scoring layer as axis-A
      and axis-F input).
    * ``minutes_since_last_hit`` is the freshness of the contamination
      — infinite when there's no hit in the window.
    * ``details`` is a human-readable audit trail.
    """

    flags: set[ContaminationReason] = field(default_factory=set)
    window_hits: int = 0
    minutes_since_last_hit: Optional[float] = None
    details: list[str] = field(default_factory=list)

    @property
    def any(self) -> bool:
        return bool(self.flags)

    def to_log_dict(self) -> Dict[str, Any]:
        return {
            "flags": sorted(f.value for f in self.flags),
            "window_hits": self.window_hits,
            "minutes_since_last_hit": self.minutes_since_last_hit,
            "details": list(self.details),
        }


# ── guard implementation ────────────────────────────────────


_DIRECT_PROMPT_PHRASES: tuple[str, ...] = (
    # English
    "say mama",
    "say mom",
    "say mommy",
    "say mother",
    "call me mama",
    "call me mom",
    # Finnish
    "sano äiti",
    "sano mama",
    "sano mamma",
    "kutsu minua äidiksi",
)


_DEFAULT_SCRIPTED_MARKERS: tuple[str, ...] = (
    "[test fixture]",
    "<dataset:",
    "{{template}}",
    "scripted_scenario=",
)


@dataclass
class ContaminationGuard:
    """Stateless scanner over an event's context."""

    target_tokens: Sequence[str] = (
        "mama",
        "mamma",
        "mom",
        "mommy",
        "mother",
        "äiti",
        "äidille",
    )
    direct_prompt_phrases: Sequence[str] = _DIRECT_PROMPT_PHRASES
    scripted_markers: Sequence[str] = _DEFAULT_SCRIPTED_MARKERS
    # How much of recent history counts as "recent".
    window_turns: int = 20

    def scan(
        self,
        *,
        utterance_text: str,
        recent_lexical_window: Sequence[str],
        prior_turns: Sequence[str] = (),
        tts_recent_outputs: Sequence[str] = (),
        utterance_kind: Optional[str] = None,
        scripted_context_flag: bool = False,
        prompt_template: Optional[str] = None,
        recent_lexical_timestamps_ms: Sequence[int] = (),
        now_ms: Optional[int] = None,
    ) -> ContaminationReport:
        """Run every guard against a pre-built context.

        The observer assembles this context from the runtime's
        actual signals and calls :meth:`scan` — this keeps the
        contamination layer free of transport concerns.
        """

        report = ContaminationReport()
        lowered_target = [t.lower().strip() for t in self.target_tokens if t.strip()]

        # 1. Direct prompt detection — scan the most recent prior turn
        #    only. Older turns are covered by the lexical-window guard.
        recent_operator_turn = prior_turns[-1].lower() if prior_turns else ""
        for phrase in self.direct_prompt_phrases:
            if phrase in recent_operator_turn:
                report.flags.add(ContaminationReason.DIRECT_PROMPT)
                report.details.append(f"direct_prompt: matched {phrase!r}")
                break

        # Also: if the operator literally typed the target word at the
        # end of their last turn, that's a prompt too.
        if not report.flags & {ContaminationReason.DIRECT_PROMPT}:
            if self._contains_any_token(recent_operator_turn, lowered_target):
                report.flags.add(ContaminationReason.DIRECT_PROMPT)
                report.details.append(
                    "direct_prompt: operator last turn contains target token"
                )

        # 2. Lexical contamination window
        hits = 0
        last_hit_index: Optional[int] = None
        window = list(recent_lexical_window)[-self.window_turns:]
        for idx, line in enumerate(window):
            if self._contains_any_token(line.lower(), lowered_target):
                hits += 1
                last_hit_index = idx
        report.window_hits = hits
        if hits > 0:
            report.flags.add(ContaminationReason.LEXICAL_WINDOW)
            report.details.append(
                f"lexical_window: {hits} hit(s) in last {len(window)} turns"
            )
            if recent_lexical_timestamps_ms and last_hit_index is not None:
                ts_list = list(recent_lexical_timestamps_ms)[-self.window_turns:]
                if 0 <= last_hit_index < len(ts_list):
                    last_hit_ms = ts_list[last_hit_index]
                    now_value = now_ms if now_ms is not None else int(time.time() * 1000)
                    delta_min = max(0.0, (now_value - last_hit_ms) / 60_000.0)
                    report.minutes_since_last_hit = delta_min
                    report.details.append(
                        f"lexical_window: last hit was {delta_min:.1f} min ago"
                    )

        # 3. STT contamination — if the incoming utterance is STT
        #    transcribed and the target word appears in the STT text
        #    itself, that's contamination rather than spontaneous
        #    generation.
        if utterance_kind == "stt_input":
            if self._contains_any_token(utterance_text.lower(), lowered_target):
                report.flags.add(ContaminationReason.STT_INPUT)
                report.details.append(
                    "stt_input: target token came in via speech recognition"
                )

        # 4. TTS echo — if any recent TTS output contained the target
        #    word and the same word now appears in the current
        #    (generated/STT) utterance, the system is hearing itself.
        for tts_line in tts_recent_outputs:
            if self._contains_any_token(tts_line.lower(), lowered_target):
                if self._contains_any_token(utterance_text.lower(), lowered_target):
                    report.flags.add(ContaminationReason.TTS_ECHO)
                    report.details.append(
                        "tts_echo: target token in recent TTS output and current utterance"
                    )
                break

        # 5. Scripted dataset detector — either the operator set the
        #    flag, or one of the canned markers appears in the prompt
        #    template.
        if scripted_context_flag:
            report.flags.add(ContaminationReason.SCRIPTED_DATASET)
            report.details.append("scripted_dataset: explicit operator flag")
        if prompt_template:
            lowered_template = prompt_template.lower()
            for marker in self.scripted_markers:
                if marker.lower() in lowered_template:
                    report.flags.add(ContaminationReason.SCRIPTED_DATASET)
                    report.details.append(
                        f"scripted_dataset: template contains marker {marker!r}"
                    )
                    break

        # 6. Prompt-template contamination — the template literally
        #    contains the target word, which means the model has been
        #    trained or prompted to emit it.
        if prompt_template:
            if self._contains_any_token(prompt_template.lower(), lowered_target):
                report.flags.add(ContaminationReason.PROMPT_TEMPLATE)
                report.details.append(
                    "prompt_template: template literal contains target token"
                )

        if report.any:
            log.debug(
                "contamination report: flags=%s hits=%d",
                sorted(f.value for f in report.flags),
                report.window_hits,
            )
        return report

    # ── helpers ──

    @staticmethod
    def _contains_any_token(text: str, tokens: Sequence[str]) -> bool:
        if not text:
            return False
        for tok in tokens:
            if not tok:
                continue
            pattern = r"(?<!\w)" + re.escape(tok) + r"(?!\w)"
            if re.search(pattern, text):
                return True
        return False
