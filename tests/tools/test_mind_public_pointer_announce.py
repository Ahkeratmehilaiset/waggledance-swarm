"""Pure, opt-in public pointer announcement + ADVISORY selection (Lead GO lead-mind-pointer-announce-fable-20261031-v1).

Synthetic only: rows are literal dicts, the roster carries synthetic uuids, the producer report is built with the real
producer's own report constructor. Nothing here reads or writes the bridge, a model, a file or a clock. A selection is
advice for a caller that must still obtain its accepted pointer through a separately trusted channel.
"""
import hashlib
import json
import os

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
    body = {"schema": ann.SCHEMA, "kind": "publication", "generation": "gen-a", "pointer_sha256": POINTER_A, "anchor_sha256": ANCHOR_SHA,
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


@pytest.mark.parametrize("uuid", ["ä", "\ud800", "not-a-uuid"])
def test_non_ascii_or_malformed_candidate_uuid_has_fixed_refusal(uuid):
    _refused(lambda: _select([row(uuid=uuid)]), "candidate_rejected")


def test_unencodable_task_id_has_fixed_refusal():
    _refused(lambda: _select([row()], task_id="\ud800"), "request_invalid")


@pytest.mark.parametrize("timestamp", ["2026-99-99T99:99:99Z", "2025-02-29T12:00:00Z",
                                       "2026-10-31T24:00:00Z", "0000-01-01T00:00:00Z"])
def test_impossible_utc_timestamp_has_fixed_refusal(timestamp):
    _refused(lambda: _select([row(ts=timestamp)]), "candidate_rejected")


def test_valid_leap_day_and_seven_fraction_digits_remain_ordered():
    rows = [row(ts="2024-02-29T12:00:00.0000001Z"),
            row(ts="2024-02-29T12:00:00.0000002Z", payload=payload(pointer_sha256=POINTER_B))]
    assert _select(rows)["pointer_sha256"] == POINTER_B


def test_surface_is_closed_and_off_by_default():
    assert ann.announcement_payload(report(), ANCHOR_SHA) == {"status": "OFF"}
    assert ann.select_announced_pointer([row()], task_id=TASK, publisher_agent=PUBLISHER, roster=ROSTER) == {"status": "OFF"}
    for enabled in (1, "true", None):
        _refused(lambda: ann.announcement_payload(report(), ANCHOR_SHA, enabled=enabled), "request_invalid")
        _refused(lambda: ann.select_announced_pointer([], task_id=TASK, publisher_agent=PUBLISHER, roster=ROSTER, enabled=enabled),
                 "request_invalid")
    assert ann.SCHEMA == "wd.q4-public-pointer-announce.v1" and ann.SELECTION_SCHEMA == "wd.q4-public-pointer-selection.v1"
    assert ann.PAYLOAD_KEYS == {"schema", "kind", "generation", "pointer_sha256", "anchor_sha256", "artifacts", "source_mode",
                                "authenticity", "freshness"}
    assert ann.SELECTION_KEYS == {"schema", "kind", "generation", "pointer_sha256", "anchor_sha256", "artifacts", "source_mode",
                                  "announced_ts_utc", "publisher_agent", "candidates", "advisory", "view_fields_consistent",
                                  "source_mode_consistency", "authenticity", "freshness", "coverage", "ordering",
                                  "same_user_integrity"}
    assert ann.REFUSALS == {"request_invalid", "report_invalid", "no_change_not_announced", "view_required", "view_mismatch",
                            "rows_invalid", "no_candidate", "candidate_rejected", "ambiguous", "conflicting"}
    assert ann.ANNOUNCED_STATUSES == {"PUBLISHED_GENESIS", "PUBLISHED_HISTORY", "EMPTY_ADVANCED"}
    assert ann.KINDS == {"publication", "empty_advance"}
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
    no_change = pub._report("EMPTY_NO_CHANGE", generation=None, pointer_sha256=POINTER_A, artifacts=None, records=0,
                            public_policy_sha256=POLICY_SHA)
    _refused(lambda: ann.announcement_payload(no_change, ANCHOR_SHA, enabled=True), "no_change_not_announced")
    _refused(lambda: ann.announcement_payload(dict(no_change, status="OFF"), ANCHOR_SHA, enabled=True), "report_invalid")
    # an EMPTY_ADVANCED report carries no generation or artifacts: it is announceable ONLY through a caller-verified
    # checkpoint view (read_verified_pointer shape) whose pointer digest is the advanced one
    advanced = pub._report("EMPTY_ADVANCED", generation=None, pointer_sha256=POINTER_B, artifacts=None, records=0,
                           public_policy_sha256=POLICY_SHA)
    view_b = {"pointer_sha256": POINTER_B, "generation": "gen-a", "artifacts": dict(ARTIFACTS), "anchor_sha256": ANCHOR_SHA,
              "cursor_end_sha256": sha("end"), "watermark_sha256": sha("watermark")}
    _refused(lambda: ann.announcement_payload(advanced, ANCHOR_SHA, enabled=True), "view_required")
    _refused(lambda: ann.announcement_payload(advanced, ANCHOR_SHA, verified_view=dict(view_b, pointer_sha256=POINTER_A), enabled=True),
             "view_mismatch")  # the view is for another pointer
    _refused(lambda: ann.announcement_payload(advanced, sha("other-anchor"), verified_view=view_b, enabled=True), "view_mismatch")
    _refused(lambda: ann.announcement_payload(advanced, ANCHOR_SHA, verified_view=dict(view_b, extra=1), enabled=True), "view_mismatch")
    _refused(lambda: ann.announcement_payload(advanced, ANCHOR_SHA, verified_view={"pointer_sha256": POINTER_B}, enabled=True),
             "view_mismatch")
    empty_payload = ann.announcement_payload(advanced, ANCHOR_SHA, verified_view=view_b, enabled=True)
    assert empty_payload == payload(kind="empty_advance", pointer_sha256=POINTER_B)
    assert "cursor_end_sha256" not in empty_payload and "watermark_sha256" not in empty_payload
    # a publication report may also carry the view; then it must agree with the report
    assert ann.announcement_payload(report(), ANCHOR_SHA, verified_view=dict(view_b, pointer_sha256=POINTER_A), enabled=True) == payload()
    _refused(lambda: ann.announcement_payload(report(), ANCHOR_SHA, verified_view=view_b, enabled=True), "view_mismatch")
    _refused(lambda: ann.announcement_payload(report(), ANCHOR_SHA, verified_view=dict(view_b, pointer_sha256=POINTER_A, generation="gen-z"),
                                              enabled=True), "view_mismatch")
    _refused(lambda: ann.announcement_payload({"status": "OFF"}, ANCHOR_SHA, enabled=True), "report_invalid")
    for odd in ([SENTINEL], {"k": SENTINEL}, 7, None, b"PUBLISHED_GENESIS"):  # v3: unhashable or non-string status
        _refused(lambda: ann.announcement_payload(dict(report(), status=odd), ANCHOR_SHA, enabled=True), "report_invalid")
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
    assert out["candidates"] == 3 and out["advisory"] is True and out["kind"] == "publication"
    for key in ("authenticity", "freshness", "coverage", "ordering", "same_user_integrity", "view_fields_consistent",
                "source_mode_consistency"):
        assert out[key] == "UNKNOWN"
    # the caller may hand in the checkpoint view it verified for the selected digest: consistency is checked, never trust
    view = {"pointer_sha256": POINTER_B, "generation": "gen-b", "artifacts": dict(ARTIFACTS), "anchor_sha256": ANCHOR_SHA,
            "cursor_end_sha256": sha("end"), "watermark_sha256": sha("wm")}
    with_view = _select(rows, verified_view=view)
    # v3: the six-field view carries neither source_mode nor the public-policy pin, so only the VIEW FIELDS are called
    # consistent (pointer, generation, artifacts, anchor); the source-mode binding stays UNKNOWN, never implied
    assert with_view["view_fields_consistent"] is True and with_view["source_mode_consistency"] == "UNKNOWN"
    assert "view_consistent" not in with_view
    _refused(lambda: _select(rows, verified_view=dict(view, pointer_sha256=POINTER_A)), "view_mismatch")
    _refused(lambda: _select(rows, verified_view=dict(view, artifacts=dict(ARTIFACTS, memory_sha256=sha("x")))), "view_mismatch")
    _refused(lambda: _select(rows, verified_view="view"), "view_mismatch")
    # an EMPTY ADVANCE of gen-b (new pointer, same generation binding) is a normal continuation, never a conflict
    advance = row(ts="2026-10-31T20:05:00.0000000Z", payload=payload(kind="empty_advance", generation="gen-b", pointer_sha256=POINTER_C))
    followed = _select(rows + [advance])
    assert followed["pointer_sha256"] == POINTER_C and followed["kind"] == "empty_advance" and followed["generation"] == "gen-b"
    assert followed["artifacts"] == ARTIFACTS and followed["anchor_sha256"] == ANCHOR_SHA
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
    # same generation, a new pointer digest, IDENTICAL binding (artifacts, anchor, mode): followable, not a conflict
    assert _select([row(ts="2026-10-31T19:00:00.0000000Z"),
                    row(ts="2026-10-31T20:00:00.0000000Z", payload=payload(pointer_sha256=POINTER_B))])["pointer_sha256"] == POINTER_B
    _refused(lambda: _select([row(payload=payload(kind="unknown-kind"))]), "candidate_rejected")
    for odd in (["publication"], {"kind": SENTINEL}, 3, None):  # v3: unhashable or non-string kind is a fixed refusal
        _refused(lambda: _select([row(payload=payload(kind=odd))]), "candidate_rejected")
    for odd in ([PUBLISHER], {"agent": PUBLISHER}, None):  # v3: unhashable publisher names refuse, never TypeError
        _refused(lambda: _select([row()], publisher_agent=odd), "request_invalid")
    _refused(lambda: _select([row(payload=payload(schema=[ann.SCHEMA]))]), "no_candidate")  # a list schema is not the schema
    _refused(lambda: _select([row(ts="2026-10-31T19:00:00.0000000Z"),
                              row(ts="2026-10-31T20:00:00.0000000Z", payload=payload(pointer_sha256=POINTER_B, anchor_sha256=sha("other")))]),
             "conflicting")  # same generation, different anchor binding
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


# ---- v2 (Lead bound continuation 20:58:35Z): a verified EMPTY ADVANCE changes the pointer digest but keeps the ----
# ---- generation binding; readers must be able to follow it. REAL chain over synthetic bytes through the producer ----
# ---- test helpers (FAISS + Windows-pinned checkpoint), announced and selected here, verified through the Lead reader. ----
@pytest.mark.skipif(os.name != "nt", reason="checkpoint publication is pinned to fixed Windows volumes")
def test_real_chain_publish_a_then_empty_advance_is_announced_selected_and_followed_then_b(tmp_path):
    chain = pytest.importorskip("test_mind_shared_public_producer",
                                reason="integration prerequisite: the producer test helpers (FAISS) are required")
    root = tmp_path / "mem"
    root.mkdir()
    log, generation_path, start = chain._log(tmp_path / "log", chain.MIXED_EVENTS)
    config_a = chain._config(log, generation_path, start)
    report_a = chain._publish(chain._request(config_a, root, "gen-a"))
    p0, anchor_a = report_a["pointer_sha256"], config_a.source.anchor_sha256
    view_a = chain._public_read(root, p0)
    payload_a = ann.announcement_payload(report_a, anchor_a, verified_view=view_a, enabled=True)
    assert payload_a["kind"] == "publication" and payload_a["pointer_sha256"] == p0 and payload_a["generation"] == "gen-a"
    roster, task = chain.ROSTER, chain.TASK
    rows = [{"ts_utc": "2026-10-31T20:00:00.0000000Z", "agent": "codex-lead-1", "agent_uuid": roster["codex-lead-1"],
             "type": "message", "status": "milestone", "task_id": task, "message": "", "to": "", "payload": payload_a}]
    selected = ann.select_announced_pointer(rows, task_id=task, publisher_agent="codex-lead-1", roster=roster,
                                            verified_view=view_a, enabled=True)
    assert selected["pointer_sha256"] == p0 and selected["view_fields_consistent"] is True
    # the strict empty delta: pointer advances, generation and artifacts stay; NO_CHANGE needs no announcement
    predecessor = chain.pub.Predecessor(pointer_sha256=p0, memory_dir=root / "gen-a",
                                        memory_sha256=report_a["artifacts"]["memory_sha256"],
                                        projection_sha256=report_a["artifacts"]["projection_sha256"],
                                        build_receipt_sha256=report_a["artifacts"]["receipt_sha256"])
    log, generation_path, resume = chain._log(tmp_path / "log", chain.EMPTY_EVENTS, start=chain.verified_cursor(config_a, root, p0))
    config_empty = chain._config(log, generation_path, resume)
    report_empty = chain._publish(chain._request(config_empty, root, "gen-x", predecessor=predecessor))
    assert report_empty["status"] == "EMPTY_ADVANCED"
    p1 = report_empty["pointer_sha256"]
    view_1 = chain._public_read(root, p1)  # the caller's INDEPENDENT verification of the advanced pointer
    assert view_1["generation"] == "gen-a" and view_1["artifacts"] == report_a["artifacts"] and view_1["anchor_sha256"] == anchor_a
    with pytest.raises(ann.PointerAnnounceRefused, match="view_required"):
        ann.announcement_payload(report_empty, anchor_a, enabled=True)
    with pytest.raises(ann.PointerAnnounceRefused, match="view_mismatch"):  # the superseded view does not announce p1
        ann.announcement_payload(report_empty, anchor_a, verified_view=view_a, enabled=True)
    payload_empty = ann.announcement_payload(report_empty, anchor_a, verified_view=view_1, enabled=True)
    assert payload_empty["kind"] == "empty_advance" and payload_empty["pointer_sha256"] == p1
    assert payload_empty["generation"] == "gen-a" and payload_empty["artifacts"] == report_a["artifacts"]
    log, generation_path, same = chain._log(tmp_path / "log", [], start=chain.verified_cursor(config_empty, root, p1))
    predecessor_p1 = chain.pub.Predecessor(pointer_sha256=p1, memory_dir=root / "gen-a",
                                           memory_sha256=report_a["artifacts"]["memory_sha256"],
                                           projection_sha256=report_a["artifacts"]["projection_sha256"],
                                           build_receipt_sha256=report_a["artifacts"]["receipt_sha256"])
    no_change = chain._publish(chain._request(chain._config(log, generation_path, same), root, "gen-y", predecessor=predecessor_p1))
    assert no_change["status"] == "EMPTY_NO_CHANGE"
    with pytest.raises(ann.PointerAnnounceRefused, match="no_change_not_announced"):
        ann.announcement_payload(no_change, anchor_a, verified_view=view_1, enabled=True)
    rows.append(dict(rows[0], ts_utc="2026-10-31T20:05:00.0000000Z", payload=payload_empty))
    followed = ann.select_announced_pointer(rows, task_id=task, publisher_agent="codex-lead-1", roster=roster, enabled=True)
    assert followed["pointer_sha256"] == p1 and followed["kind"] == "empty_advance" and followed["generation"] == "gen-a"
    assert chain._public_read(root, followed["pointer_sha256"])["generation"] == "gen-a"  # the reader follows the advance
    with pytest.raises(chain.checkpoint.CheckpointRefused):  # the superseded announcement no longer resolves
        chain._public_read(root, selected["pointer_sha256"])
    # later B: a real history publication supersedes both announcements
    log, generation_path, resume_b = chain._log(tmp_path / "log", chain.LATER_PUBLIC_EVENTS, start=same)
    config_b = chain._config(log, generation_path, resume_b)
    report_b = chain._publish(chain._request(config_b, root, "gen-b", predecessor=predecessor_p1))
    p2 = report_b["pointer_sha256"]
    payload_b = ann.announcement_payload(report_b, config_b.source.anchor_sha256, verified_view=chain._public_read(root, p2), enabled=True)
    rows.append(dict(rows[0], ts_utc="2026-10-31T20:10:00.0000000Z", payload=payload_b))
    final = ann.select_announced_pointer(rows, task_id=task, publisher_agent="codex-lead-1", roster=roster, enabled=True)
    assert final["pointer_sha256"] == p2 and final["generation"] == "gen-b" and final["kind"] == "publication" and final["candidates"] == 3
    assert chain._public_read(root, p2)["artifacts"] == final["artifacts"]
    # a forged same-generation announcement with another artifact set still poisons the selection
    forged = dict(rows[1], ts_utc="2026-10-31T20:06:00.0000000Z",
                  payload=dict(payload_empty, artifacts=dict(payload_empty["artifacts"], memory_sha256="0" * 64)))
    with pytest.raises(ann.PointerAnnounceRefused, match="conflicting"):
        ann.select_announced_pointer(rows + [forged], task_id=task, publisher_agent="codex-lead-1", roster=roster, enabled=True)
    text = json.dumps(rows)
    assert chain.SENTINEL not in text and start.file_identity not in text and "bc1." not in text
