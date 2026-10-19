"""M1 exact-ID top-three scoring and M3 caller-side card costs; no I/O or model calls.

This is instrumentation, NOT a measurement authorization or frozen protocol.
The run owner must verify the preregistration/Grok lock, sample, temporal memory
cut, measured SHA, independence and single-run receipt BEFORE supplying real
cases. No semantic matching, candidate reordering, model retries or data loading
is performed here. Timing includes the supplied generator (including any adapter)
but excludes tokenization/scoring. Report that boundary; it is not pure engine
latency. Without a named exact tokenizer token costs are unknown, not estimated.
"""

from dataclasses import dataclass
from time import perf_counter_ns
from typing import Callable


class MeasurementError(ValueError):
    """Fixed error codes; never interpolate private input or provider errors."""


@dataclass(frozen=True)
class Case:
    query: object
    acceptable_lesson_ids: tuple[str, ...]


@dataclass(frozen=True)
class Card:
    text: str
    ranked_lesson_ids: tuple[str, ...]


def _ids(value, *, empty_allowed):
    return (isinstance(value, tuple) and (empty_allowed or bool(value))
            and all(isinstance(item, str) and bool(item) for item in value)
            and len(set(value)) == len(value))


def run_pilot(
    cases: tuple[Case, ...],
    generate: Callable[[object], Card],
    *,
    token_count: Callable[[str], int] | None = None,
    tokenizer_id: str | None = None,
    clock_ns: Callable[[], int] = perf_counter_ns,
) -> dict:
    """One generation per case in frozen order; return aggregates only.

    The generator receives only the query, not gold IDs. Its returned IDs must
    already be ordered lesson IDs, without duplicates; first three slots count.
    An empty valid card is a miss. A failed/malformed card aborts the run with a
    fixed error, never a retry, a reduced denominator or a partial success report.
    The caller must durably mark a started trial consumed even on this exception.
    """
    if not isinstance(cases, tuple) or not cases or any(
        not isinstance(case, Case) or not _ids(case.acceptable_lesson_ids, empty_allowed=False)
        for case in cases
    ):
        raise MeasurementError("invalid_sample")
    if ((token_count is None) != (tokenizer_id is None)
            or (token_count is not None and
                (not callable(token_count) or not isinstance(tokenizer_id, str) or not tokenizer_id.strip()))):
        raise MeasurementError("invalid_tokenizer")
    elapsed, sizes, counts = [], [], []
    hits = 0
    for case in cases:
        start = clock_ns()
        try:
            card = generate(case.query)
        except Exception:
            raise MeasurementError("generation_failed") from None
        end = clock_ns()
        if type(start) is not int or type(end) is not int or end < start:
            raise MeasurementError("invalid_clock")
        if (not isinstance(card, Card) or not isinstance(card.text, str)
                or not _ids(card.ranked_lesson_ids, empty_allowed=True)):
            raise MeasurementError("invalid_card")
        try:
            size = len(card.text.encode("utf-8"))
        except UnicodeError:
            raise MeasurementError("invalid_utf8") from None
        if token_count is not None:
            try:
                tokens = token_count(card.text)
            except Exception:
                raise MeasurementError("tokenizer_failed") from None
            if type(tokens) is not int or tokens < 0:
                raise MeasurementError("invalid_token_count")
            counts.append(tokens)
        hits += int(bool(set(card.ranked_lesson_ids[:3]) & set(case.acceptable_lesson_ids)))
        elapsed.append(end - start)
        sizes.append(size)
    n = len(cases)
    return {
        "schema": "mind.measurement-tools.aggregate.v1",
        "cases": n,
        "top3_hits": hits,
        "top3_rate": hits / n,
        "card_ms_total": sum(elapsed) / 1_000_000,
        "card_ms_mean": sum(elapsed) / n / 1_000_000,
        "card_ms_max": max(elapsed) / 1_000_000,
        "card_bytes_total": sum(sizes),
        "card_bytes_mean": sum(sizes) / n,
        "card_tokens_total": sum(counts) if token_count is not None else None,
        "card_tokens_mean": sum(counts) / n if token_count is not None else None,
        "tokenizer_id": tokenizer_id,
        "timing_scope": "generator_including_adapter_excluding_tokenizer_and_scoring",
    }
