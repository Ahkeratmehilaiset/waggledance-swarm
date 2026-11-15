"""Synthetic-only nonpositive relevance regression; no real model or data."""
import datetime as dt
import io
import json
import math

import pytest

from test_wd_mind_content_query_readonly import (
    QUERY_TIME, SEMANTIC_DIM, _semantic_case, _semantic_entry, reader,
)


@pytest.mark.parametrize("all_tasks", [False, True], ids=["v2", "v3"])
def test_unrelated_query_with_only_nonpositive_cosines_abstains(reader, tmp_path, all_tasks):
    case = _semantic_case(reader, tmp_path, all_tasks=all_tasks)
    case["encoder_provider"].query = lambda text: (-1.0,) + (0.0,) * (SEMANTIC_DIM - 1)
    case["stream"] = io.BytesIO(json.dumps({
        "situation": "How does a banana root network absorb nutrients?", "time": QUERY_TIME,
    }).encode())
    result = _semantic_entry(reader, case)
    assert result["status"] == "ABSTAIN" and result["results"] == []


def _vector(score):
    return (score, math.sqrt(1 - score * score)) + (0.0,) * (SEMANTIC_DIM - 2)


def _rank_fixture(reader, w, d, *, query="network retry investigation"):
    records = {str(i): {"stored_text": f"network transport update {i}",
                         "provenance": {"available_utc": "2026-01-01T00:00:00Z"}}
               for i in range(4)}
    entries = {key: {"w": _vector(w), "d": _vector(d)} for key in records}
    class Encoder:
        def query(self, _):
            return (1.0,) + (0.0,) * (SEMANTIC_DIM - 1)
    return reader._q4_rank(records, entries, Encoder(), query, dt.datetime(2026, 1, 2, tzinfo=dt.timezone.utc))


@pytest.mark.parametrize("w,d", [(0.0, 0.0), (-0.5, -0.5), (0.5, 0.0), (0.0, 0.5),
                                  (-0.5, 0.5), (0.5, -0.5)])
def test_both_view_scores_must_be_positive(reader, w, d):
    assert _rank_fixture(reader, w, d) == []


@pytest.mark.parametrize("score", [1e-8, 0.5, 1.0])
def test_positive_scores_and_lexical_agreement_can_still_match(reader, score):
    assert _rank_fixture(reader, score, score)


def test_positive_scores_do_not_bypass_lexical_agreement(reader):
    assert _rank_fixture(reader, 0.5, 0.5, query="banana roots") == []


def test_nonfinite_query_vector_refuses(reader):
    records = {str(i): {"stored_text": f"network transport update {i}",
                         "provenance": {"available_utc": "2026-01-01T00:00:00Z"}}
               for i in range(4)}
    entries = {key: {"w": _vector(0.5), "d": _vector(0.5)} for key in records}
    class Encoder:
        def query(self, _):
            return (float("nan"),) + (0.0,) * (SEMANTIC_DIM - 1)
    with pytest.raises(ValueError, match="invalid_vector"):
        reader._q4_rank(records, entries, Encoder(), "network retry investigation",
                        dt.datetime(2026, 1, 2, tzinfo=dt.timezone.utc))
