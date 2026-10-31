"""Projection v5 to Q4 integration using synthetic events and an in-memory fake index.

This is not a test of the production index builder, real embedding, or source authenticity.
"""

from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("faiss")  # Q4's memory module imports FAISS.

from sandbox.mind.step2 import q4, shared_projection


class SyntheticEncoder:
    def query(self, text):
        return np.array([1.0, 0.0], dtype=np.float32)


class SyntheticIndex:
    def __init__(self, vectors):
        self.vectors = np.asarray(vectors, dtype=np.float32)

    def reconstruct_n(self, start, count):
        assert start == 0 and count == len(self.vectors)
        return self.vectors.copy()


def build_q4(records, target_ref):
    """Represent each projection by its two documented Q4 views, entirely in memory."""
    rows, texts, vectors = [], [], []
    for record in records:
        ref = record["provenance"]["event_ref"]
        for view in ("w", "d"):
            provenance = dict(record["provenance"], record=record["record_id"], view=view)
            rows.append({"kind": record["kind"], "time_utc": record["time_utc"], "provenance": provenance})
            texts.append(shared_projection.encoder_input(record))
            vectors.append([1.0 if ref == target_ref else 0.2, 0.0])
    collection = SimpleNamespace(_index=SyntheticIndex(vectors), count=len(rows), _texts=texts)
    view = SimpleNamespace(healthy=True, rows=rows, collection=collection)
    memory = SimpleNamespace(views={"general": view}, memory={"dim": 2}, topology=None)
    return q4.Q4(memory, SyntheticEncoder())


def projected_events():
    events = [
        {"ts_utc": "2026-01-01T11:58:20Z", "agent": lane, "type": "progress",
         "status": "done", "task_id": "unknown", "message": "never-index-this", "payload": {"private": "no"}}
        for lane in ("codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2")
    ]
    events.append({"ts_utc": "2026-01-01T12:00:59Z", "agent": "fable-5", "type": "progress",
                   "status": "done", "task_id": "unknown", "message": "never-index-this-either"})
    # Same approved metadata (including the floored minute), distinct discarded private input.
    events.append(dict(events[0], ts_utc="2026-01-01T11:58:45Z", message="different-private-text"))
    projection = shared_projection.project_events(events)
    assert projection["accepted"] == 6 and projection["refused"] == {}
    assert projection["records"][0] == projection["records"][-1]
    return projection["records"]


def test_projection_to_q4_positive_result_and_noncanonical_reference():
    records = projected_events()
    future = records[4]
    engine = build_q4(records, future["provenance"]["event_ref"])
    answer = engine.ask("fable progress beacon", "2026-01-01T12:01:01.000001Z")
    assert answer["status"] == "ANSWER"
    assert answer["results"][0]["ref"] == future["provenance"]["event_ref"]
    assert all(item["ref_kind"] == "sha256_of_projected_metadata" and item["canonical_event"] is False
               for item in answer["results"])
    assert "never-index-this" not in str(answer)


@pytest.mark.parametrize("when,visible", [
    ("2026-01-01T12:00:10Z", False),
    ("2026-01-01T12:00:59Z", False),
    ("2026-01-01T12:01:01Z", False),  # boundary + one-second guard is still excluded
    ("2026-01-01T12:01:01.000001Z", True),
])
def test_future_projection_respects_strict_next_minute_plus_guard(when, visible):
    records = projected_events()
    future = records[4]
    ref = future["provenance"]["event_ref"]
    engine = build_q4(records, ref)
    answer = engine.ask("fable progress beacon", when)
    assert shared_projection.visible_at(future, when, guard_band_s=q4.RULE["guard_band_s"]) is visible
    assert answer["status"] == "ANSWER"
    assert (ref in [item["ref"] for item in answer["results"]]) is visible
    assert answer["telemetry"]["candidates"] == (5 if visible else 4)


def test_duplicate_projection_is_one_q4_record_not_two_canonical_events():
    records = projected_events()
    engine = build_q4(records, records[4]["provenance"]["event_ref"])
    assert len(engine.ids) == 5
    assert len(set(engine.ids)) == 5
    answer = engine.ask("progress beacon", "2026-01-01T12:02:00Z")
    assert answer["status"] == "ANSWER"
    assert len({item["doc_id"] for item in answer["results"]}) == len(answer["results"])
    assert all(item["canonical_event"] is False for item in answer["results"])
