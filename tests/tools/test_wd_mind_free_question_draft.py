"""Private draft for a proposed explicit free-question candidate request."""

from hashlib import sha256
import json

import pytest

from tools import wd_mind_full_runtime as runtime


LANE = "codex-tools-1"
STARTUP_TEXT = "current task active work next step recent status"
QUESTIONS = (
    "Which label marks the synthetic green bench?",
    "Which label marks the synthetic blue bench?",
)


def startup_request():
    return {
        "schema": "wd.mind-pipe-request.v1",
        "query_id": "startup_work_overview",
        "nonce": "d" * 64,
        "lane": LANE,
        "scope": "own",
    }


def synthetic_hit(query):
    return {
        "ref_sha256": "a" * 64,
        "doc_id_sha256": "b" * 64,
        "source_event_sha256": sha256(query.encode("utf-8")).hexdigest(),
    }


@pytest.fixture
def mapper(monkeypatch):
    requests = []
    handler = runtime._FlatRequestHandler(
        binding=None,
        snapshot=None,
        lane=LANE,
        lanes=(LANE,),
        rotator=None,
        adapter=None,
        factory=None,
        timeout=None,
        bulk=None,
    )

    def synthetic_owner(self, request, bound_lane):
        requests.append((bound_lane, dict(request)))
        hit = synthetic_hit(request["query"])
        return {
            "schema": "wd.mind-flat-owner-response.v1",
            "lane": bound_lane,
            "scope": request["scope"],
            "authority": "none",
            "expires_utc": None,
            "expiry_scope": "source_validity_only",
            "snapshot_freshness": "UNKNOWN",
            "result": {
                "schema": "wd.mind-flat-f32-query.v1",
                "status": "SEMANTIC_CANDIDATE",
                "authority": "none",
                "relevance": "UNVERIFIED",
                "index_match": "NOT_APPLICABLE",
                "own_latest_task": "UNKNOWN",
                "results": [{
                    "ref_sha256": hit["ref_sha256"],
                    "doc_id_sha256": hit["doc_id_sha256"],
                    "source_offset": 24,
                    "source_length": 64,
                    "chunk_index": 0,
                    "source_reference": {
                        "sha256": hit["source_event_sha256"],
                        "kind": "sha256_of_canonical_event_json",
                        "verified": (
                            "MATCH_SELECTED_ROW" if self._retained is not None else "MATCH_WITHIN_PREFIX"
                        ),
                        "sender_authentication": "UNKNOWN",
                    },
                }],
            },
        }

    monkeypatch.setattr(runtime._FlatRequestHandler, "_handle_for_lane", synthetic_owner)
    return handler, requests


def assert_served_query(mapper, request, expected, **options):
    handler, observed = mapper
    answer = runtime.map_pipe_candidates(enabled=True, handler=handler, request=request, **options)
    assert observed == [(LANE, {
        "schema": "wd.mind-flat-owner-request.v1",
        "lane": LANE,
        "scope": "own",
        "query": expected,
    })]
    assert answer == {
        "schema": "wd.mind-pipe-candidates.v1",
        "nonce": request["nonce"],
        "lane": LANE,
        "scope": "own",
        "status": "CANDIDATES",
        "candidates": [synthetic_hit(expected)],
    }


def test_startup_keeps_fixed_overview_text(mapper):
    assert_served_query(mapper, startup_request(), STARTUP_TEXT)


def test_startup_keeps_located_candidate_schema(mapper):
    handler, observed = mapper
    handler._retained = object.__new__(runtime._RetainedFlatQueryState)
    request = startup_request()
    result = runtime.map_pipe_candidates(
        enabled=True, handler=handler, request=request, _include_locations=True
    )
    assert observed[0][1]["query"] == STARTUP_TEXT
    assert result == {
        "schema": "wd.mind-pipe-candidates.v2",
        "nonce": request["nonce"],
        "lane": LANE,
        "scope": "own",
        "status": "CANDIDATES",
        "candidates": [
            dict(synthetic_hit(STARTUP_TEXT), source_offset=24, source_length=64, chunk_index=0)
        ],
    }


def test_startup_rejects_added_question_field(mapper):
    handler, observed = mapper
    request = dict(startup_request(), question=QUESTIONS[0])
    with pytest.raises(runtime.FullRuntimeRefused):
        runtime.map_pipe_candidates(enabled=True, handler=handler, request=request)
    assert observed == []


def free_question_request(question):
    return dict(
        startup_request(),
        schema="wd.mind-pipe-request.v3",
        query_id="free_question",
        question=question,
    )


@pytest.mark.parametrize("question", QUESTIONS, ids=("green-bench", "blue-bench"))
def test_explicit_free_question_uses_provided_text(mapper, question):
    request = free_question_request(question)
    assert_served_query(mapper, request, question, free_question_binding=(LANE, "own"))


def test_free_question_requires_trusted_binding(mapper):
    handler, observed = mapper
    with pytest.raises(runtime.FullRuntimeRefused):
        runtime.map_pipe_candidates(
            enabled=True, handler=handler, request=free_question_request(QUESTIONS[0])
        )
    assert observed == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("scope", "shared", id="wrong-scope"),
        pytest.param("lane", "codex-lead-1", id="wrong-lane"),
        pytest.param("nonce", "D" * 64, id="wrong-nonce"),
        pytest.param("query_id", "startup_work_overview", id="wrong-query-id"),
        pytest.param("question", 12, id="numeric-question"),
        pytest.param("question", " \t ", id="blank-question"),
        pytest.param("question", "a\x00b", id="nul-question"),
        pytest.param("question", "x" * 4096, id="ascii-frame-overflow"),
        pytest.param("question", "\u20ac" * 700, id="escaped-frame-overflow"),
        pytest.param("additional", "unrecognized", id="extra-field"),
    ],
)
def test_free_question_refuses_invalid_request_before_serving(mapper, field, value):
    handler, observed = mapper
    request = free_question_request(QUESTIONS[0])
    request[field] = value
    with pytest.raises(runtime.FullRuntimeRefused):
        runtime.map_pipe_candidates(
            enabled=True, handler=handler, request=request, free_question_binding=(LANE, "own")
        )
    assert observed == []


def test_free_question_accepts_exact_existing_frame_limit(mapper):
    request = free_question_request("")
    header_bytes = len(json.dumps(request, sort_keys=True, ensure_ascii=True, separators=(",", ":")))
    question = "x" * (4096 - header_bytes)
    request["question"] = question
    assert_served_query(mapper, request, question, free_question_binding=(LANE, "own"))
