"""Content lane timestamps come from the pinned projection, not child output."""
from datetime import datetime, timezone
import copy

from tools import wd_q4_lane_query as query
from tools import wd_q4_lane_caller_adapter as adapter


def _case(status="ANSWER"):
    binding = {key: "a" * 64 for key in query.CONTENT_BINDING_KEYS}
    record = {"stored_text": "Work update", "time_utc": "2026-11-08T12:00:00.000000Z",
              "available_utc": "2026-11-08T12:01:00.000000Z", "provenance": {"source_kind": "bridge_message"}}
    key = ("b" * 64, "c" * 64)
    child_binding = dict(binding, source_mode="content-snapshot-v1")
    value = {"status": status, "binding": child_binding, "schema": "wd.q4-content-snapshot-query.v1",
             "completeness": "UNKNOWN", "results": [] if status == "ABSTAIN" else [
                 {"ref_sha256": key[0], "doc_id_sha256": key[1], "stored_text": record["stored_text"]}]}
    return binding, {key: record}, value


def test_content_answer_derives_times_from_projection(monkeypatch):
    monkeypatch.setattr(query.sp, "visible_at", lambda *args, **kwargs: True)
    binding, records, value = _case()
    answer, reason = query._answer("fable-5", value, binding, records, datetime(2026, 11, 8, 12, 2, tzinfo=timezone.utc))
    assert reason is None
    assert answer["schema"] == "wd.q4-lane-answer.v2"
    assert answer["query_time_utc"] == "2026-11-08T12:02:00.000000Z"
    assert answer["snapshot_cutoff_utc"] == "2026-11-08T12:01:00.000000Z"
    assert answer["results"][0]["record_time_utc"] == "2026-11-08T12:00:00.000000Z"
    assert answer["results"][0]["time_basis"] == "bridge_event_minute"
    assert answer["trust"]["freshness"] == answer["trust"]["coverage_after_cutoff"] == "UNKNOWN"


def test_abstain_preserves_snapshot_boundary():
    binding, records, value = _case("ABSTAIN")
    answer, reason = query._answer("fable-5", value, binding, records, datetime(2026, 11, 8, 12, 2, tzinfo=timezone.utc))
    assert reason is None
    assert answer["snapshot_cutoff_utc"] == "2026-11-08T12:01:00.000000Z"
    assert answer["query_time_utc"] == "2026-11-08T12:02:00.000000Z"


def test_adapter_rejects_forged_future_record_and_wrong_binding(monkeypatch):
    monkeypatch.setattr(query.sp, "visible_at", lambda *args, **kwargs: True)
    binding, records, value = _case()
    answer, reason = query._answer("fable-5", value, binding, records, datetime(2026, 11, 8, 12, 2, tzinfo=timezone.utc))
    assert reason is None and adapter._validated(answer, "fable-5", binding) is not None
    forged = copy.deepcopy(answer)
    forged["results"][0]["record_time_utc"] = "2026-11-09T12:00:00.000000Z"
    assert adapter._validated(forged, "fable-5", binding) is None
    assert adapter._validated(answer, "fable-5", {"projection_sha256": "a" * 64}) is None


def test_document_time_basis_and_untrusted_child_time(monkeypatch):
    monkeypatch.setattr(query.sp, "visible_at", lambda *args, **kwargs: True)
    binding, records, value = _case()
    next(iter(records.values()))["provenance"]["source_kind"] = "repo_doc"
    value["results"][0]["record_time_utc"] = "2099-01-01T00:00:00.000000Z"
    answer, reason = query._answer("fable-5", value, binding, records, datetime(2026, 11, 8, 12, 2, tzinfo=timezone.utc))
    assert reason is None
    assert answer["results"][0]["record_time_utc"] == "2026-11-08T12:00:00.000000Z"
    assert answer["results"][0]["time_basis"] == "pinned_commit_time"
