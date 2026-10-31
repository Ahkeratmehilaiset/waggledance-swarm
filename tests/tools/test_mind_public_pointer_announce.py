"""Pure, opt-in public pointer announcement + ADVISORY selection (Lead GO lead-mind-pointer-announce-fable-20261031-v1).

Synthetic only: rows are literal dicts, the roster carries synthetic uuids, the producer report is built with the real
producer's own report constructor. Nothing here reads or writes the bridge, a model, a file or a clock. A selection is
advice for a caller that must still obtain its accepted pointer through a separately trusted channel.
"""
import hashlib
import json

import pytest

from sandbox.mind.step2 import shared_projection as projection
from sandbox.mind.step2 import shared_public_pointer_announce as ann
from sandbox.mind.step2 import shared_public_producer as pub

TASK = "lead-mind-public-producer-synthetic-task"
ROSTER = {"codex-lead-1": "aaaa1111-1111-4111-8111-111111111aaa", "codex-tools-1": "bbbb2222-2222-4222-8222-222222222bbb",
          "claude-rco-1": "cccc3333-3333-4333-8333-333333333ccc", "claude-rco-2": "dddd4444-4444-4444-8444-444444444ddd",
          "fable-5": "eeee5555-5555-4555-8555-555555555eee"}
PUBLISHER = "codex-lead-1"
SENTINEL = "zebra-private-path-7f3a"


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


ARTIFACTS = {"descriptor_sha256": sha("descriptor"), "memory_sha256": sha("memory"),
             "projection_sha256": sha("projection"), "receipt_sha256": sha("receipt")}
POLICY_SHA = sha("public-policy")
ANCHOR_SHA = sha("anchor")
POINTER_A, POINTER_B, POINTER_C = sha("pointer-a"), sha("pointer-b"), sha("pointer-c")


def report(status="PUBLISHED_GENESIS", generation="gen-a", pointer_sha256=POINTER_A, artifacts=ARTIFACTS, records=3):
    """The REAL producer report constructor, so the payload builder is coupled to the producer's closed shape."""
    return pub._report(status, generation=generation, pointer_sha256=pointer_sha256, artifacts=artifacts, records=records,
                       public_policy_sha256=POLICY_SHA)


def payload(**changes):
    body = {"schema": ann.SCHEMA, "generation": "gen-a", "pointer_sha256": POINTER_A, "anchor_sha256": ANCHOR_SHA,
            "artifacts": dict(ARTIFACTS), "source_mode": {"status_mode": "public-v1", "public_policy_sha256": POLICY_SHA},
            "authenticity": "UNKNOWN", "freshness": "UNKNOWN"}
    body.update(changes)
    return body


def row(ts="2026-10-31T20:00:00.0000000Z", agent=PUBLISHER, uuid="roster", task_id=TASK, type_="message", **changes):
    base = {"ts_utc": ts, "agent": agent, "agent_uuid": ROSTER[agent] if uuid == "roster" else uuid, "type": type_,
            "status": "milestone", "task_id": task_id, "message": "", "payload": payload(), "to": ""}
    base.update(changes)
    return base


def _refused(call, token):
    with pytest.raises(ann.PointerAnnounceRefused) as info:
        call()
    assert str(info.value) == token and token in ann.REFUSALS


def _select(rows, **kw):
    kw.setdefault("task_id", TASK)
    kw.setdefault("publisher_agent", PUBLISHER)
    kw.setdefault("roster", ROSTER)
    return ann.select_announced_pointer(rows, enabled=True, **kw)


def test_surface_is_closed_and_off_by_default():
    assert ann.announcement_payload(report(), ANCHOR_SHA) == {"status": "OFF"}
    assert ann.select_announced_pointer([row()], task_id=TASK, publisher_agent=PUBLISHER, roster=ROSTER) == {"status": "OFF"}
    for enabled in (1, "true", None):
        _refused(lambda: ann.announcement_payload(report(), ANCHOR_SHA, enabled=enabled), "request_invalid")
        _refused(lambda: ann.select_announced_pointer([], task_id=TASK, publisher_agent=PUBLISHER, roster=ROSTER, enabled=enabled),
                 "request_invalid")
    assert ann.SCHEMA == "wd.q4-public-pointer-announce.v1" and ann.SELECTION_SCHEMA == "wd.q4-public-pointer-selection.v1"
    assert ann.PAYLOAD_KEYS == {"schema", "generation", "pointer_sha256", "anchor_sha256", "artifacts", "source_mode",
                                "authenticity", "freshness"}
    assert ann.SELECTION_KEYS == {"schema", "generation", "pointer_sha256", "anchor_sha256", "artifacts", "source_mode",
                                  "announced_ts_utc", "publisher_agent", "candidates", "advisory", "authenticity", "freshness",
                                  "coverage", "ordering", "same_user_integrity"}
    assert ann.REFUSALS == {"request_invalid", "report_invalid", "not_a_publication", "rows_invalid", "no_candidate",
                            "candidate_rejected", "ambiguous", "conflicting"}
    assert ann.ANNOUNCED_STATUSES == {"PUBLISHED_GENESIS", "PUBLISHED_HISTORY"}
    assert issubclass(ann.PointerAnnounceRefused, ValueError)
    assert not hasattr(ann, "publish_public_generation") and not hasattr(ann, "read_bridge_log")


def test_payload_is_closed_and_built_only_from_a_publication_report():
    out = ann.announcement_payload(report(), ANCHOR_SHA, enabled=True)
    assert out == payload()
    assert set(out) == ann.PAYLOAD_KEYS and out["authenticity"] == out["freshness"] == "UNKNOWN"
    assert out["artifacts"] is not ARTIFACTS and out["artifacts"] == ARTIFACTS  # a copy, closed to the four digests
    assert ann.announcement_payload(report("PUBLISHED_HISTORY", generation="gen-b", pointer_sha256=POINTER_B), ANCHOR_SHA,
                                    enabled=True)["generation"] == "gen-b"
    assert len(json.dumps(out, separators=(",", ":"), ensure_ascii=True).encode("ascii")) <= ann.PAYLOAD_LIMIT
    for status in ("EMPTY_ADVANCED", "EMPTY_NO_CHANGE"):
        empty = pub._report(status, generation=None, pointer_sha256=POINTER_A, artifacts=None, records=0,
                            public_policy_sha256=POLICY_SHA)
        _refused(lambda: ann.announcement_payload(empty, ANCHOR_SHA, enabled=True), "not_a_publication")
    _refused(lambda: ann.announcement_payload({"status": "OFF"}, ANCHOR_SHA, enabled=True), "report_invalid")
    _refused(lambda: ann.announcement_payload(None, ANCHOR_SHA, enabled=True), "report_invalid")
    _refused(lambda: ann.announcement_payload(dict(report(), extra=SENTINEL), ANCHOR_SHA, enabled=True), "report_invalid")
    _refused(lambda: ann.announcement_payload(dict(report(), pointer_sha256=POINTER_A.upper()), ANCHOR_SHA, enabled=True),
             "report_invalid")
    _refused(lambda: ann.announcement_payload(dict(report(), generation="Gen A"), ANCHOR_SHA, enabled=True), "report_invalid")
    _refused(lambda: ann.announcement_payload(report(artifacts=dict(ARTIFACTS, path=SENTINEL)), ANCHOR_SHA, enabled=True),
             "report_invalid")
    _refused(lambda: ann.announcement_payload(dict(report(), source_mode={"status_mode": "legacy", "public_policy_sha256": None}),
                                              ANCHOR_SHA, enabled=True), "report_invalid")
    _refused(lambda: ann.announcement_payload(dict(report(), authenticated=True), ANCHOR_SHA, enabled=True), "report_invalid")
    _refused(lambda: ann.announcement_payload(report(), "not-a-digest", enabled=True), "report_invalid")
    _refused(lambda: ann.announcement_payload(report(), None, enabled=True), "report_invalid")
    text = json.dumps(out)
    assert SENTINEL not in text and "records" not in out and "cursor" not in text and "\\" not in text and ":/" not in text


def test_selection_picks_the_newest_matching_announcement_and_reports_only_unknowns():
    rows = [row(ts="2026-10-31T19:00:00.0000000Z"),  # older announcement of gen-a
            row(ts="2026-10-31T19:30:00.0000000Z", agent="fable-5", type_="claim", payload={"detail": SENTINEL}),  # unrelated
            row(ts="2026-10-31T19:45:00.0000000Z", task_id="other-task", payload={"schema": "wd.other.v1"}),  # unrelated
            row(ts="2026-10-31T20:00:00.0000000Z", payload=payload(generation="gen-b", pointer_sha256=POINTER_B)),
            row(ts="2026-10-31T20:00:00.0000000Z", payload=payload(generation="gen-b", pointer_sha256=POINTER_B)),  # exact dup
            {"ts_utc": "2026-10-31T20:10:00.0000000Z", "agent": "codex-tools-1", "type": "message"}]  # no payload at all
    out = _select(rows)
    assert set(out) == ann.SELECTION_KEYS and out["schema"] == ann.SELECTION_SCHEMA
    assert out["generation"] == "gen-b" and out["pointer_sha256"] == POINTER_B and out["anchor_sha256"] == ANCHOR_SHA
    assert out["artifacts"] == ARTIFACTS and out["source_mode"] == {"status_mode": "public-v1", "public_policy_sha256": POLICY_SHA}
    assert out["announced_ts_utc"] == "2026-10-31T20:00:00.0000000Z" and out["publisher_agent"] == PUBLISHER
    assert out["candidates"] == 3 and out["advisory"] is True
    for key in ("authenticity", "freshness", "coverage", "ordering", "same_user_integrity"):
        assert out[key] == "UNKNOWN"
    text = json.dumps(out)
    assert SENTINEL not in text and all(uuid not in text for uuid in ROSTER.values())
    # fractional second precision does not confuse the ordering; a later second-only timestamp still wins
    later = row(ts="2026-10-31T20:00:01Z", payload=payload(generation="gen-c", pointer_sha256=POINTER_C))
    assert _select(rows + [later])["generation"] == "gen-c"
    assert _select([row()])["candidates"] == 1


def test_selection_refuses_malformed_ambiguous_conflicting_and_forged_candidates():
    _refused(lambda: _select([]), "no_candidate")
    _refused(lambda: _select([row(task_id="other-task", payload={"schema": "wd.other.v1"})]), "no_candidate")
    # an explicit announcement that does not come from the configured publisher/task/uuid is a REJECTION, never skipped
    _refused(lambda: _select([row(uuid="ffff9999-9999-4999-8999-999999999fff")]), "candidate_rejected")  # forged label
    _refused(lambda: _select([row(agent="fable-5")]), "candidate_rejected")  # another lane announcing
    _refused(lambda: _select([row(task_id="other-task")]), "candidate_rejected")  # the schema under another task
    _refused(lambda: _select([row(type_="done")]), "candidate_rejected")  # not a message row
    _refused(lambda: _select([row(), row(uuid="ffff9999-9999-4999-8999-999999999fff", ts="2026-10-31T20:30:00.0000000Z")]),
             "candidate_rejected")  # one forged candidate poisons the whole selection
    _refused(lambda: _select([row(payload=payload(pointer_sha256=POINTER_A.upper()))]), "candidate_rejected")
    _refused(lambda: _select([row(payload=dict(payload(), path=SENTINEL))]), "candidate_rejected")  # extra key
    _refused(lambda: _select([row(payload=payload(authenticity="VERIFIED"))]), "candidate_rejected")  # claims trust
    _refused(lambda: _select([row(payload=payload(source_mode={"status_mode": "legacy", "public_policy_sha256": None}))]),
             "candidate_rejected")
    _refused(lambda: _select([row(ts="2026-10-31 20:00:00")]), "candidate_rejected")  # timestamp shape
    _refused(lambda: _select([row(ts="2026-10-31T20:00:00.0000000Z"),
                              row(ts="2026-10-31T20:00:00.0000000Z", payload=payload(pointer_sha256=POINTER_B))]), "ambiguous")
    _refused(lambda: _select([row(ts="2026-10-31T19:00:00.0000000Z"),
                              row(ts="2026-10-31T20:00:00.0000000Z", payload=payload(pointer_sha256=POINTER_B))]), "conflicting")
    _refused(lambda: _select([row(ts="2026-10-31T19:00:00.0000000Z"),
                              row(ts="2026-10-31T20:00:00.0000000Z", payload=payload(artifacts=dict(ARTIFACTS, memory_sha256=sha("x"))))]),
             "conflicting")
    _refused(lambda: _select([row(ts="2026-10-31T19:00:00.0000000Z", payload=payload(generation="gen-b", pointer_sha256=POINTER_B)),
                              row(ts="2026-10-31T20:00:00.0000000Z", payload=payload(generation="gen-a", pointer_sha256=POINTER_B))]),
             "conflicting")  # one pointer digest for two generations
    _refused(lambda: _select("not-rows"), "rows_invalid")
    _refused(lambda: _select([row(), "not-a-row"]), "rows_invalid")
    _refused(lambda: _select([row()] * (ann.ROWS_LIMIT + 1)), "rows_invalid")
    _refused(lambda: _select([row()], publisher_agent="grok-scout-1"), "request_invalid")  # not a projection lane
    _refused(lambda: _select([row()], roster=dict(ROSTER, **{PUBLISHER: "not-a-uuid"})), "request_invalid")
    _refused(lambda: _select([row()], roster={k: v for k, v in ROSTER.items() if k != PUBLISHER}), "request_invalid")
    _refused(lambda: _select([row()], task_id=""), "request_invalid")
    _refused(lambda: _select([row()], task_id="x" * (projection.BYTE_CAPS["task_id"] + 1)), "request_invalid")
