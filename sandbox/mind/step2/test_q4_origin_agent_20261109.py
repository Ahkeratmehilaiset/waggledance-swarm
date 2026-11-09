"""Startup own-task reads keep only results whose verified origin lane is the named lane, after the unchanged
full-set consensus; every other question is untouched. Model-free synthetic memories only."""

import copy
import hashlib
import random
from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("faiss")

from sandbox.mind.step2 import cell, q4
from sandbox.mind.step2.test_q4_readonly_port import FakeEncoder, FakeIndex

LANES = ("codex-lead-1", "codex-tools-1", "fable-5", "claude-rco-1", "claude-rco-2")
BUILD_LANES = ("codex-lead-1", "codex-tools-1", "fable-5")  # reviewer rows are excluded by the projection policy
STARTUP = "%s startup read: own current task, last bound reply, operator directive of the day"
WHEN = "2026-01-02T00:00:00Z"
AVAILABLE = "2026-01-01T00:00:00.000000Z"


def lane_memory(lanes, *, counts=None, metadata="rows", text=None):
    """Rows per lane in the given order (record ids sort in that order); ``metadata`` selects what the
    collection exposes: per-row dicts, a short list, non-dict entries, mismatched w/d agents, or nothing."""
    rows, texts, metas = [], [], []
    for order, lane in enumerate(lanes):
        for number in range((counts or {}).get(lane, 4)):
            record = "%s-%s-%d" % (chr(ord("a") + order), lane, number)
            for kind in ("w", "d"):
                rows.append({"kind": "lesson", "time_utc": AVAILABLE,
                             "provenance": {"record": record, "view": kind, "available_utc": AVAILABLE,
                                            "file": "%s.md" % record, "tier": {"source_type": "learned_from_case"}}})
                texts.append(text or "current task bound reply directive note %d" % number)
                agent = lane
                if metadata == "mismatch" and kind == "d":
                    agent = "someone-else"
                metas.append({"agent": agent, "type": "message", "view": kind})
    if metadata == "short":
        metas = metas[:-1]
    elif metadata == "not-dict":
        metas = [["agent", lane] for lane in metas]
    elif metadata == "no-agent":
        metas = [{"type": "message"} for _ in metas]
    collection = SimpleNamespace(_index=FakeIndex(len(rows)), count=len(rows), _texts=texts)
    if metadata != "absent":
        collection._metadata = metas
    views = {"learning": SimpleNamespace(healthy=True, collection=collection, rows=rows)}
    topology = SimpleNamespace(get_neighbors=lambda cell_id, max_ring: ["learning"])
    return SimpleNamespace(views=views, memory={"dim": 2}, topology=topology)


def refs(answer):
    return [item["ref"] for item in answer["results"]]


def lanes_of(answer):
    return {ref.split("-", 1)[1].rsplit("-", 1)[0] for ref in refs(answer)}


# --- RED reproduction: the launcher's fixed startup situation answers with another lane's rows --------------


def test_startup_read_never_returns_another_lanes_rows():
    engine = q4.Q4(lane_memory(BUILD_LANES), FakeEncoder())
    answer = engine.ask(STARTUP % "fable-5", WHEN)
    assert lanes_of(answer) <= {"fable-5"}
    assert answer["status"] != "ANSWER" or answer["results"]


def test_startup_read_abstains_with_a_fixed_reason_when_the_consensus_set_is_foreign():
    engine = q4.Q4(lane_memory(BUILD_LANES), FakeEncoder())
    answer = engine.ask(STARTUP % "fable-5", WHEN)
    assert answer["status"] == "ABSTAIN" and answer["reason"] == "no_lane_origin_results"
    assert answer["results"] == [] and answer["telemetry"]["rejected_origin"] == 3


# --- five identities ------------------------------------------------------------------------------------


@pytest.mark.parametrize("lane", LANES)
def test_each_identity_receives_only_its_own_rows_or_abstains(lane):
    for first in BUILD_LANES:
        ordered = (first,) + tuple(other for other in BUILD_LANES if other != first)
        answer = q4.Q4(lane_memory(ordered), FakeEncoder()).ask(STARTUP % lane, WHEN)
        if lane == first:
            assert answer["status"] == "ANSWER" and lanes_of(answer) == {lane} and len(answer["results"]) == 3
        else:
            assert answer["status"] == "ABSTAIN" and answer["reason"] == "no_lane_origin_results"
            assert answer["results"] == []


@pytest.mark.parametrize("lane", ("claude-rco-1", "claude-rco-2"))
def test_reviewer_identities_abstain_because_their_rows_are_excluded_upstream(lane):
    answer = q4.Q4(lane_memory(BUILD_LANES), FakeEncoder()).ask(STARTUP % lane, WHEN)
    assert answer["status"] == "ABSTAIN" and answer["results"] == []


def test_missing_own_rows_abstain():
    answer = q4.Q4(lane_memory(("codex-lead-1", "codex-tools-1")), FakeEncoder()).ask(STARTUP % "fable-5", WHEN)
    assert answer["status"] == "ABSTAIN" and answer["results"] == []


@pytest.mark.parametrize("lane", ("mallory", "fable-5-evil", "x"))
def test_unknown_or_forged_lane_tokens_abstain(lane):
    answer = q4.Q4(lane_memory(BUILD_LANES), FakeEncoder()).ask(STARTUP % lane, WHEN)
    assert answer["status"] == "ABSTAIN" and answer["results"] == []


# --- unverifiable origin is rejected (fail closed) -------------------------------------------------------


@pytest.mark.parametrize("metadata", ("absent", "short", "not-dict", "no-agent", "mismatch"))
def test_rows_without_a_verified_origin_are_rejected_for_startup_reads(metadata):
    answer = q4.Q4(lane_memory(("fable-5",), metadata=metadata), FakeEncoder()).ask(STARTUP % "fable-5", WHEN)
    assert answer["status"] == "ABSTAIN" and answer["reason"] == "no_lane_origin_results"
    assert answer["results"] == [] and answer["telemetry"]["rejected_origin"] == 3


def test_origin_is_read_from_the_collection_metadata_not_from_the_text():
    memory = lane_memory(("codex-lead-1",), text="fable-5 fable-5 current task bound reply directive")
    answer = q4.Q4(memory, FakeEncoder()).ask(STARTUP % "fable-5", WHEN)
    assert answer["status"] == "ABSTAIN" and answer["results"] == []


# --- unrelated, directive and private-shaped questions are byte-identical to the unfiltered engine -------


def strip_ms(answer):
    answer = copy.deepcopy(answer)
    answer["telemetry"].pop("ms", None)
    return answer


@pytest.mark.parametrize("question", [
    "current task bound reply",
    "operator directive of the day",
    "operator personal email address and phone number",
    "fable-5 status today",
    "startup read: own current task",  # no lane token: not the launcher shape
    "Fable-5 startup read: own current task",  # lane tokens are lower case
    "please run fable-5 startup read: own current task",  # not at the start
    "retry network",
    "unrelated token",
])
def test_non_startup_questions_are_unchanged_by_origin_metadata(question):
    with_origin = q4.Q4(lane_memory(BUILD_LANES), FakeEncoder()).ask(question, WHEN)
    without = q4.Q4(lane_memory(BUILD_LANES, metadata="absent"), FakeEncoder()).ask(question, WHEN)
    assert strip_ms(with_origin) == strip_ms(without)
    assert "rejected_origin" not in with_origin["telemetry"]


def test_a_plain_question_may_still_return_every_lane():
    answer = q4.Q4(lane_memory(BUILD_LANES), FakeEncoder()).ask("bound reply current task", WHEN)
    assert answer["status"] == "ANSWER" and lanes_of(answer) == {"codex-lead-1"}


# --- monotonicity: the startup filter runs after the unchanged full-set consensus ------------------------


class HashIndex:
    """Per-row unit vectors derived from the row text, so the two vector views rank non-trivially."""

    def __init__(self, texts, dim=8):
        self.vectors = (np.vstack([unit(text, dim) for text in texts]) if texts
                        else np.zeros((0, dim))).astype(np.float32)

    def reconstruct_n(self, start, count):
        assert (start, count) == (0, len(self.vectors))
        return self.vectors


class HashEncoder:
    def __init__(self, dim=8):
        self.dim = dim

    def query(self, text):
        return unit(text, self.dim)


def unit(text, dim):
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    vector = np.array([(digest[i % len(digest)] - 128.0) for i in range(dim)], dtype=np.float32)
    return vector / (np.linalg.norm(vector) or 1.0)


VOCABULARY = ("current", "task", "bound", "reply", "directive", "operator", "day", "note", "memory", "index",
              "gate", "review", "claim", "release", "savepoint", "restart", "bundle", "pointer", "fleet", "startup")


def random_memory(rng, *, own_everywhere=False):
    rows, texts, metas = [], [], []
    for lane in BUILD_LANES:
        for number in range(rng.randint(0, 6)):
            record = "%s-%d" % (lane, number)
            text = " ".join(rng.choice(VOCABULARY) for _ in range(rng.randint(3, 9)))
            for kind in ("w", "d"):
                rows.append({"kind": "lesson", "time_utc": AVAILABLE,
                             "provenance": {"record": record, "view": kind, "available_utc": AVAILABLE,
                                            "file": "%s.md" % record, "tier": {"source_type": "learned_from_case"}}})
                texts.append(text)
                metas.append({"agent": lane, "view": kind})
    collection = SimpleNamespace(_index=HashIndex(texts), count=len(rows), _texts=texts, _metadata=metas)
    views = {"learning": SimpleNamespace(healthy=True, collection=collection, rows=rows)}
    topology = SimpleNamespace(get_neighbors=lambda cell_id, max_ring: ["learning"])
    memory = SimpleNamespace(views=views, memory={"dim": 8}, topology=topology)
    return memory, metas


def test_startup_filter_never_creates_an_answer_and_only_removes_shown_results():
    rng = random.Random(20261109)
    seen_answer = seen_abstain = 0
    for _ in range(300):
        memory, metas = random_memory(rng)
        lane = rng.choice(LANES)
        situation = STARTUP % lane + (" " + " ".join(rng.choice(VOCABULARY) for _ in range(rng.randint(0, 4))))
        filtered = q4.Q4(memory, HashEncoder()).ask(situation, WHEN)
        for meta in metas:  # the same memory with every row owned by the named lane: the filter keeps everything
            meta["agent"] = lane
        baseline = q4.Q4(memory, HashEncoder()).ask(situation, WHEN)
        assert set(refs(filtered)) <= set(refs(baseline))
        assert refs(filtered) == [ref for ref in refs(baseline) if ref in set(refs(filtered))]
        if baseline["status"] != "ANSWER":
            assert strip_ms(filtered) == strip_ms(baseline)
        else:
            assert filtered["status"] in ("ANSWER", "ABSTAIN")
            assert (filtered["status"] == "ANSWER") == bool(filtered["results"])
            assert filtered["telemetry"]["rejected_origin"] == len(baseline["results"]) - len(filtered["results"])
            seen_answer += filtered["status"] == "ANSWER"
            seen_abstain += filtered["status"] == "ABSTAIN"
    assert seen_answer and seen_abstain


def test_the_startup_shape_is_the_launchers_fixed_prefix():
    assert q4.startup_origin_lane(STARTUP % "codex-lead-1") == "codex-lead-1"
    assert q4.startup_origin_lane("codex-tools-1 startup read: own current task") == "codex-tools-1"
    assert q4.startup_origin_lane("fable-5 startup read: own current tasks") is None
    assert q4.startup_origin_lane("fable-5 startup read: own current task") == "fable-5"
    assert q4.startup_origin_lane(" fable-5 startup read: own current task") is None
    assert q4.startup_origin_lane("startup read: own current task") is None
    assert q4.startup_origin_lane(None) is None


# --- the read-only snapshot facade exposes the receipt-hashed collection metadata -------------------------


def test_snapshot_facade_exposes_verified_metadata_read_only():
    metadata = [{"agent": "fable-5", "view": "w"}, {"agent": "fable-5", "view": "d"}]
    candidate = SimpleNamespace(search=lambda *a: [], _texts=["one", "one"], _doc_ids=["r#w", "r#d"], count=2,
                                _index=FakeIndex(2), dim=2, _metadata=metadata)
    facade = cell._ReadOnlySnapshotCollection(candidate, verified_dim=2)
    assert facade._metadata == tuple(metadata) and type(facade._metadata) is tuple
    metadata[0]["agent"] = "someone-else"
    assert facade._metadata[0]["agent"] == "fable-5"
    with pytest.raises(AttributeError):
        facade._metadata = ()


def test_snapshot_facade_without_metadata_exposes_an_empty_tuple():
    candidate = SimpleNamespace(search=lambda *a: [], _texts=["one"], _doc_ids=["r#w"], count=1,
                                _index=FakeIndex(1), dim=2)
    assert cell._ReadOnlySnapshotCollection(candidate, verified_dim=2)._metadata == ()


def test_q4_reads_origin_through_the_snapshot_facade():
    memory = lane_memory(("fable-5",))
    view = memory.views["learning"]
    raw = view.collection
    raw.search = lambda *a: []
    raw._doc_ids = [row["provenance"]["record"] + "#" + row["provenance"]["view"] for row in view.rows]
    raw.dim = 2
    view.collection = cell._ReadOnlySnapshotCollection(raw, verified_dim=2)
    answer = q4.Q4(memory, FakeEncoder()).ask(STARTUP % "fable-5", WHEN)
    assert answer["status"] == "ANSWER" and lanes_of(answer) == {"fable-5"}


# --- recipient label: a result explicitly addressed to the named build lane is kept too -----------------------


def addressed_memory(author, to, *, count=4, mismatch=None):
    """Rows authored by ``author`` and addressed to ``to`` (a label, None, or a non-string), first in id order;
    ``mismatch`` names the view ("w" or "d") whose rows carry a different recipient label."""
    memory = lane_memory((author,), counts={author: count})
    for position, meta in enumerate(memory.views["learning"].collection._metadata):
        if to != "absent":
            meta["to"] = to
        if mismatch is not None and meta["view"] == mismatch:
            meta["to"] = "someone-else"
    return memory


@pytest.mark.parametrize("lane", BUILD_LANES)
def test_an_assignment_addressed_to_the_lane_is_kept_for_its_startup_read(lane):
    author = next(other for other in BUILD_LANES if other != lane)
    answer = q4.Q4(addressed_memory(author, lane), FakeEncoder()).ask(STARTUP % lane, WHEN)
    assert answer["status"] == "ANSWER" and lanes_of(answer) == {author} and len(answer["results"]) == 3
    assert answer["telemetry"]["rejected_origin"] == 0


@pytest.mark.parametrize("lane", LANES)
def test_an_assignment_addressed_to_another_lane_is_rejected(lane):
    other = next(build for build in BUILD_LANES if build != lane)
    author = next(build for build in BUILD_LANES if build not in (lane, other))
    answer = q4.Q4(addressed_memory(author, other), FakeEncoder()).ask(STARTUP % lane, WHEN)
    assert answer["status"] == "ABSTAIN" and answer["reason"] == "no_lane_origin_results"
    assert answer["results"] == [] and answer["telemetry"]["rejected_origin"] == 3


@pytest.mark.parametrize("to", ["absent", None, 5, ["fable-5"], "", "FABLE-5"])
def test_unreadable_or_absent_recipient_labels_never_keep_a_result(to):
    answer = q4.Q4(addressed_memory("codex-lead-1", to), FakeEncoder()).ask(STARTUP % "fable-5", WHEN)
    assert answer["status"] == "ABSTAIN" and answer["results"] == []


@pytest.mark.parametrize("view", ["w", "d"])
def test_recipient_labels_must_agree_between_the_w_and_d_rows(view):
    answer = q4.Q4(addressed_memory("codex-lead-1", "fable-5", mismatch=view), FakeEncoder()).ask(
        STARTUP % "fable-5", WHEN)
    assert answer["status"] == "ABSTAIN" and answer["results"] == []


@pytest.mark.parametrize("lane", ("claude-rco-1", "claude-rco-2"))
def test_a_forged_reviewer_recipient_label_is_the_only_way_a_reviewer_could_recall_and_it_is_upstream_impossible(lane):
    # The projection never emits a reviewer recipient (policy lanes exclude reviewers; reviewer-addressed rows are
    # dropped before projection), so this synthetic metadata cannot come from a built memory; the engine itself
    # has no lane roles and would keep it. The guarantee is the pinned projection, stated here, not hidden.
    answer = q4.Q4(addressed_memory("codex-lead-1", lane), FakeEncoder()).ask(STARTUP % lane, WHEN)
    assert answer["status"] == "ANSWER"


def test_recipient_labels_do_not_change_non_startup_answers():
    memory = addressed_memory("codex-lead-1", "fable-5")
    plain = addressed_memory("codex-lead-1", "absent")
    for question in ("bound reply current task", "operator directive of the day", "fable-5 status today"):
        assert strip_ms(q4.Q4(memory, FakeEncoder()).ask(question, WHEN)) == \
            strip_ms(q4.Q4(plain, FakeEncoder()).ask(question, WHEN))


def test_startup_filter_with_recipients_never_creates_an_answer_and_only_removes_shown_results():
    rng = random.Random(20261110)
    seen_answer = seen_abstain = seen_kept_by_recipient = 0
    for _ in range(300):
        memory, metas = random_memory(rng)
        for meta in metas:
            meta["to"] = rng.choice(BUILD_LANES + (None, "mallory"))
        lane = rng.choice(LANES)
        situation = STARTUP % lane + (" " + " ".join(rng.choice(VOCABULARY) for _ in range(rng.randint(0, 4))))
        filtered = q4.Q4(memory, HashEncoder()).ask(situation, WHEN)
        if filtered["status"] == "ANSWER":
            own = {ref for ref in refs(filtered) if ref.startswith(lane + "-")}
            seen_kept_by_recipient += len(own) < len(refs(filtered))
        for meta in metas:
            meta["agent"] = lane
        baseline = q4.Q4(memory, HashEncoder()).ask(situation, WHEN)
        assert set(refs(filtered)) <= set(refs(baseline))
        assert refs(filtered) == [ref for ref in refs(baseline) if ref in set(refs(filtered))]
        if baseline["status"] != "ANSWER":
            assert strip_ms(filtered) == strip_ms(baseline)
        else:
            assert filtered["status"] in ("ANSWER", "ABSTAIN")
            assert filtered["telemetry"]["rejected_origin"] == len(baseline["results"]) - len(filtered["results"])
            seen_answer += filtered["status"] == "ANSWER"
            seen_abstain += filtered["status"] == "ABSTAIN"
    assert seen_answer and seen_abstain and seen_kept_by_recipient


# --- UF2: the lane filter must see the whole agreement set, not only the shown_max cut -----------------------


def narrow_rule(shown_max=1):
    return dict(q4.RULE, shown_max=shown_max)


def test_pinned_rule_agreement_set_cannot_exceed_the_shown_cut():
    # Every agreed record ranks within agree_depth in every view, and ranks are distinct, so the agreement set
    # holds at most agree_depth records; with shown_max >= agree_depth the cut can never discard one of them.
    assert q4.RULE["shown_max"] >= q4.RULE["agree_depth"] == 3


def test_uf2_own_evidence_beyond_a_narrow_cut_is_kept_not_discarded():
    # lead-0 sorts first and wins every tie; fable-0 and fable-1 also pass the depth-3 consensus. A cut of one
    # shown before the lane filter would show lead-0 only, reject it and abstain although own evidence agreed.
    memory = lane_memory(("codex-lead-1", "fable-5"), counts={"codex-lead-1": 1, "fable-5": 4})
    answer = q4.Q4(memory, FakeEncoder(), rule=narrow_rule(1)).ask(STARTUP % "fable-5", WHEN)
    assert answer["status"] == "ANSWER" and lanes_of(answer) == {"fable-5"} and len(answer["results"]) == 1
    assert answer["results"][0]["ref"] == "b-fable-5-0.md"
    assert answer["telemetry"]["rejected_origin"] == 1


def test_uf2_addressed_evidence_beyond_a_narrow_cut_is_kept_not_discarded():
    memory = lane_memory(("codex-lead-1", "codex-tools-1"), counts={"codex-lead-1": 1, "codex-tools-1": 4})
    for meta in memory.views["learning"].collection._metadata:
        if meta["agent"] == "codex-tools-1":
            meta["to"] = "fable-5"
    answer = q4.Q4(memory, FakeEncoder(), rule=narrow_rule(2)).ask(STARTUP % "fable-5", WHEN)
    assert answer["status"] == "ANSWER" and lanes_of(answer) == {"codex-tools-1"} and len(answer["results"]) == 2
    assert answer["telemetry"]["rejected_origin"] == 1


def test_uf2_filter_never_admits_a_record_outside_the_agreement_set():
    # fable rows exist but rank 4th and beyond in every view: no consensus for them, so the startup read abstains.
    memory = lane_memory(("codex-lead-1", "fable-5"), counts={"codex-lead-1": 3, "fable-5": 4})
    answer = q4.Q4(memory, FakeEncoder(), rule=narrow_rule(1)).ask(STARTUP % "fable-5", WHEN)
    assert answer["status"] == "ABSTAIN" and answer["reason"] == "no_lane_origin_results"
    assert answer["results"] == [] and answer["telemetry"]["rejected_origin"] == 3


def test_uf2_a_narrow_cut_still_applies_after_the_filter():
    memory = lane_memory(("fable-5",), counts={"fable-5": 5})
    answer = q4.Q4(memory, FakeEncoder(), rule=narrow_rule(2)).ask(STARTUP % "fable-5", WHEN)
    assert answer["status"] == "ANSWER" and len(answer["results"]) == 2
    assert [item["ref"] for item in answer["results"]] == ["a-fable-5-0.md", "a-fable-5-1.md"]


def test_uf2_narrow_cut_filter_is_monotone_against_the_unfiltered_agreement_set():
    rng = random.Random(20261111)
    seen = 0
    for _ in range(200):
        memory, metas = random_memory(rng)
        for meta in metas:
            meta["to"] = rng.choice(BUILD_LANES + (None,))
        lane = rng.choice(LANES)
        situation = STARTUP % lane
        rule = narrow_rule(rng.choice((1, 2)))
        filtered = q4.Q4(memory, HashEncoder(), rule=rule).ask(situation, WHEN)
        for meta in metas:
            meta["agent"] = lane
        wide = q4.Q4(memory, HashEncoder(), rule=dict(rule, shown_max=3)).ask(situation, WHEN)
        # every shown record passed the unchanged consensus (it is in the unfiltered depth-3 agreement set)
        assert set(refs(filtered)) <= set(refs(wide))
        assert len(filtered["results"]) <= rule["shown_max"]
        if wide["status"] != "ANSWER":
            assert filtered["status"] == wide["status"] and filtered["results"] == []
        else:
            seen += filtered["status"] == "ANSWER"
    assert seen
