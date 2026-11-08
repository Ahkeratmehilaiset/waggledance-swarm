"""Can the 11-06 content policy mechanism carry a newly pinned task allowlist limited to the exact 4h task ids,
without changing any secret, RCO, sealed-data or other exclusion rule? (Lead 2026-11-08 18:05Z on
lead-mind-4h-fable-memory-20261108-v1.) Synthetic evidence only: nothing here is an approved policy.

Answer the tests pin: YES for the non-RCO 4h task ids (they fit ``shared_projection.TASK``, carry no sealed marker,
load through ``shared_content_projection.load_content_policy`` and through the strict non-review policy of the
capture adapter); NO for any RCO 4h task id (the adapter's ``_REVIEW_TASK`` pattern refuses the whole non-review
policy that lists one, and the content allowlist must be a subset of it); the allowlist is policy DATA, every
exclusion rule stays in code and is pinned below. Both the content message policy AND the provisioned non-review
policy must list the ids: the content allowlist alone refuses ``policy_not_subset``.
"""
import hashlib
import json

import pytest

from sandbox.mind.step2 import content_memory as cm
from sandbox.mind.step2 import shared_capture_adapter as adapter
from sandbox.mind.step2 import shared_content_projection as cp
from sandbox.mind.step2 import shared_projection as sp
from sandbox.mind.step2 import test_content_memory as tcm
from sandbox.mind.step2 import test_content_route as tcr
from sandbox.mind.step2 import test_shared_content_projection as tcp

FOUR_H_TASKS = ("lead-mind-4h-fable-memory-20261108-v1", "lead-mind-4h-tools-package-20261108-v1",
                "lead-mind-4h-integration-tools-20261108-v1", "lead-mind-4h-package-20261108")
RCO_FOUR_H_TASKS = ("lead-mind-4h-onepass-rco1-20261108-v1", "lead-mind-4h-producer-rco2-20261108-v1",
                    "lead-mind-4h-q1-final-rco2-20261108-v1")
SEALED_TASK = "lead-mind-4h-sealed-eval-20261108-v1"
RCO1, RCO2 = "claude-rco-1", "claude-rco-2"


def allowlist_policy(tasks=FOUR_H_TASKS):
    return tcr.raw(tcp.policy_doc(approved_tasks=list(tasks), types=["message", "done", "status", "handoff"]))


def exact_synthetic_policy(legacy_bytes, rows):
    doc = json.loads(legacy_bytes)
    doc["schema"] = "wd.mind-content-policy.v2"
    # Synthetic test rows only; real refs still require independent review.
    doc["approved_source_refs"] = sorted(tcp.rows_of(*rows))
    return tcr.raw(doc)


def test_the_non_rco_4h_task_ids_fit_every_grammar_of_the_11_06_policy_mechanism():
    for task in FOUR_H_TASKS:
        assert sp.TASK.fullmatch(task) is not None
        assert not any(marker in task for marker in sp.SEALED_MARKERS)
        assert adapter._REVIEW_TASK.search(task) is None
    data = allowlist_policy()
    policy = cp.load_content_policy(data, tcr.sha(data))
    assert policy["approved_tasks"] == frozenset(FOUR_H_TASKS)
    assert policy["lanes"] == frozenset({tcp.LEAD, tcp.TOOLS, tcp.FABLE})
    strict = tcr.strict_pins(tasks=FOUR_H_TASKS + (tcp.TASK,))
    tasks, roster = adapter.strict_pins(strict["policy_bytes"], strict["policy_sha256"], strict["roster_bytes"],
                                        strict["roster_sha256"])
    assert tasks == frozenset(FOUR_H_TASKS + (tcp.TASK,)) and set(roster) == sp.LANES


@pytest.mark.parametrize("task", RCO_FOUR_H_TASKS)
def test_an_rco_4h_task_id_cannot_enter_the_allowlist(task):
    """Explicit gap: the non-review policy that lists one RCO task id is refused as a whole, so bound responses on
    RCO tasks stay outside any content memory; the content allowlist alone is then not a subset."""
    assert adapter._REVIEW_TASK.search(task) is not None
    strict = tcr.strict_pins(tasks=FOUR_H_TASKS + (task,))
    with pytest.raises(ValueError):
        adapter.strict_pins(strict["policy_bytes"], strict["policy_sha256"], strict["roster_bytes"],
                            strict["roster_sha256"])
    data = allowlist_policy(FOUR_H_TASKS + (task,))
    assert task in cp.load_content_policy(data, tcr.sha(data))["approved_tasks"]  # grammar alone does not stop it


def test_a_sealed_marker_in_a_task_id_refuses_the_allowlist_itself():
    data = allowlist_policy(FOUR_H_TASKS + (SEALED_TASK,))
    with pytest.raises(cp.ContentRefused, match="^policy_refused$"):
        cp.load_content_policy(data, tcr.sha(data))


def rows_4h():
    rco_row = tcp.row("Katselmoija vastasi.", minute=4, agent=RCO1, task_id=FOUR_H_TASKS[0])
    rco_row["agent_uuid"] = tcp.ROSTER[RCO1]
    return [
        tcp.row("Fable sai muistitehtavan.", minute=1, task_id=FOUR_H_TASKS[0]),
        tcp.row("Tools paketti valmis.", minute=2, type="done", agent=tcp.TOOLS, task_id=FOUR_H_TASKS[1]),
        tcp.row("Fable eteni.", minute=3, type="status", agent=tcp.FABLE, to=tcp.LEAD, task_id=FOUR_H_TASKS[2]),
        tcp.row("Vanha tehtava.", minute=5, task_id=tcp.TASK),
        rco_row,
        tcp.row("Leadin viesti katselmoijalle.", minute=6, to=RCO2, task_id=FOUR_H_TASKS[1]),
        tcp.row("Suljettu tehtava.", minute=7, task_id=SEALED_TASK),
        tcp.row("Loydos.", minute=8, type="finding", task_id=FOUR_H_TASKS[0]),
    ]


def build_4h(tmp_path, *, strict_tasks):
    pytest.importorskip("faiss")
    path, generation, cursor, prefix_sha = tcr.write_prefix(tmp_path / "prefix", rows_4h())
    policy = exact_synthetic_policy(allowlist_policy(), rows_4h())
    strict = tcr.strict_pins(tasks=strict_tasks)
    prefix = {"sha256": prefix_sha, "offset": cursor.offset, "generation": cursor.generation}
    decision, decision_sha = tcr.decision_of(prefix=prefix, message_pin=tcr.sha(policy), strict=strict)
    out = tmp_path / "out"
    out.mkdir()
    messages = {"source_path": str(path), "generation_path": str(generation), "end_cursor": cursor,
                "policy_bytes": policy, "policy_sha256": tcr.sha(policy)}
    built = cm.build_route_content_memory(enabled=True, messages=messages, documents=None, decision_bytes=decision,
                                          decision_sha256=decision_sha, encode=tcm.Spy(), identity=tcm.IDENTITY,
                                          dim=tcm.DIM, root=out, name="allowlist-4h", trusted_git=None, **strict)
    return built, out / "allowlist-4h"


def test_a_build_under_the_4h_allowlist_recalls_exactly_the_allowed_rows(tmp_path):
    built, memory_dir = build_4h(tmp_path, strict_tasks=FOUR_H_TASKS + (tcp.TASK,))
    projection = json.loads((memory_dir / "projection.json").read_bytes())
    records = projection["messages"]["records"]
    assert {(record["meta"]["agent"], record["meta"]["task_id"], record["meta"]["type"]) for record in records} == {
        (tcp.LEAD, FOUR_H_TASKS[0], "message"), (tcp.TOOLS, FOUR_H_TASKS[1], "done"),
        (tcp.FABLE, FOUR_H_TASKS[2], "status")}
    assert built["records"] == len(records) == 3
    text = json.dumps(projection).lower()
    assert "rco" not in text and "sealed" not in text and "loydos" not in text and "vanha" not in text
    assert projection["documents"] is None


def test_the_content_allowlist_alone_is_not_enough_the_provisioned_policy_must_list_the_ids(tmp_path):
    with pytest.raises(cm.ContentMemoryRefused, match="^policy_not_subset$"):
        build_4h(tmp_path, strict_tasks=(tcp.TASK, tcr.OTHER_TASK))
    assert not (tmp_path / "out" / "allowlist-4h").exists()


def test_the_exclusion_rules_the_allowlist_relies_on_are_unchanged():
    """Pins of the code rules an allowlist must not touch; a change to any of them turns this red."""
    assert sp.REVIEW_LANES == frozenset({RCO1, RCO2}) and sp.REVIEW_LANES <= sp.LANES
    assert sp.SEALED_MARKERS == ("sealed", "gold", "heldout", "judge")
    assert {"finding", "decision", "veto", "vote", "changes_requested", "rco_pass", "recusal"} <= sp.EXCLUDED_TYPES
    assert adapter._RCO == frozenset({RCO1, RCO2})
    assert adapter._exclude_rco_event({"agent": RCO1, "to": tcp.LEAD}) is True
    assert adapter._exclude_rco_event({"agent": tcp.LEAD, "to": RCO2}) is True
    assert adapter._exclude_rco_event({"agent": tcp.LEAD, "to": tcp.FABLE}) is False
    assert adapter._REVIEW_TASK.search("lead-mind-4h-review-20261108") is not None
    assert cp.MAX_MESSAGE_CHARS == 4096 and cp.POLICY_LIMIT == 65536


# The candidate pinned DATA handed to Lead on lead-mind-4h-real-memory-fable-20261108-v1 (compact one-line JSON without
# a trailing newline, so a CRLF conversion cannot move the pin). NOT approved; authority stays with the operator or the
# provisioning owner. The non-review candidate keeps the existing production task so the pilot behaviour is unchanged.
CONTENT_CANDIDATE = b'{"approved_tasks":["lead-mind-4h-fable-memory-20261108-v1","lead-mind-4h-tools-package-20261108-v1","lead-mind-4h-integration-tools-20261108-v1","lead-mind-4h-package-20261108"],"lanes":["codex-lead-1","codex-tools-1","fable-5"],"max_chunk_chars":300,"max_message_chars":4096,"removed_source_refs":[],"schema":"wd.mind-content-policy.v1","types":["done","handoff","message","milestone","progress","status","test"]}'
CONTENT_CANDIDATE_SHA256 = "c157ad2fe6942f12affdb6319453b527a37f36eed2b500c81153b001b7484dcd"
NONREVIEW_CANDIDATE = b'{"approved_tasks":["lead-mind-4h-fable-memory-20261108-v1","lead-mind-4h-tools-package-20261108-v1","lead-mind-4h-integration-tools-20261108-v1","lead-mind-4h-package-20261108","lead-mind-production-20261031"],"schema":"wd.q4-non-review-policy.v1"}'
NONREVIEW_CANDIDATE_SHA256 = "d1b54b3b246f287a7c8164968dcd904d0a77d6a5ec850ae80d6f72b4b5ae0ce4"
EXISTING_PRODUCTION_TASK = "lead-mind-production-20261031"


def fleet_shaped_roster():
    """The deployed fleet record shape: lanes with many more keys, the Tools supervisor apart; synthetic uuids."""
    lanes = [{"agent": lane, "agent_uuid": tcp.ROSTER[lane], "branch": "x", "cli": "x", "model": "native"}
             for lane in tcp.ROSTER if lane != tcp.TOOLS]
    fleet = tcr.raw({"schema_version": 7, "fleet_name": "synthetic", "lanes": lanes, "git_executable": "x",
                     "tools_supervisor": {"agent": tcp.TOOLS, "agent_uuid": tcp.ROSTER[tcp.TOOLS], "model": "native"}})
    return fleet, tcr.sha(fleet)


def test_the_candidate_bytes_are_exactly_the_pinned_ones_and_load_through_both_mechanisms():
    assert hashlib.sha256(CONTENT_CANDIDATE).hexdigest() == CONTENT_CANDIDATE_SHA256
    assert hashlib.sha256(NONREVIEW_CANDIDATE).hexdigest() == NONREVIEW_CANDIDATE_SHA256
    policy = cp.load_content_policy(CONTENT_CANDIDATE, CONTENT_CANDIDATE_SHA256)
    assert policy["approved_tasks"] == frozenset(FOUR_H_TASKS)
    assert policy["lanes"] == frozenset({tcp.LEAD, tcp.TOOLS, tcp.FABLE})
    existing = json.loads(open("configs/mind/content_message_policy_v1.json", "rb").read())
    candidate = json.loads(CONTENT_CANDIDATE)
    assert set(candidate) == set(existing)
    assert {k: v for k, v in candidate.items() if k != "approved_tasks"} == {
        k: v for k, v in existing.items() if k != "approved_tasks"}  # only the task list differs
    assert candidate["approved_tasks"] == list(FOUR_H_TASKS)
    roster_bytes, roster_sha = fleet_shaped_roster()
    tasks, roster = adapter.strict_pins(NONREVIEW_CANDIDATE, NONREVIEW_CANDIDATE_SHA256, roster_bytes, roster_sha)
    assert tasks == frozenset(FOUR_H_TASKS) | {EXISTING_PRODUCTION_TASK} and set(roster) == sp.LANES
    assert policy["approved_tasks"] < tasks  # content allowlist is a strict subset of the non-review candidate


@pytest.mark.parametrize("exact_sources", [False, True])
def test_candidate_build_requires_exact_synthetic_sources(tmp_path, exact_sources):
    pytest.importorskip("faiss")
    path, generation, cursor, prefix_sha = tcr.write_prefix(tmp_path / "prefix", rows_4h())
    roster_bytes, roster_sha = fleet_shaped_roster()
    strict = {"policy_bytes": NONREVIEW_CANDIDATE, "policy_sha256": NONREVIEW_CANDIDATE_SHA256,
              "roster_bytes": roster_bytes, "roster_sha256": roster_sha}
    prefix = {"sha256": prefix_sha, "offset": cursor.offset, "generation": cursor.generation}
    content_policy = exact_synthetic_policy(CONTENT_CANDIDATE, rows_4h()) if exact_sources else CONTENT_CANDIDATE
    content_pin = tcr.sha(content_policy)
    decision, decision_sha = tcr.decision_of(prefix=prefix, message_pin=content_pin, strict=strict)
    out = tmp_path / "out"
    out.mkdir()
    messages = {"source_path": str(path), "generation_path": str(generation), "end_cursor": cursor,
                "policy_bytes": content_policy, "policy_sha256": content_pin}
    if not exact_sources:
        with pytest.raises(cm.ContentMemoryRefused, match="^exact_source_policy_required$"):
            cm.build_route_content_memory(enabled=True, messages=messages, documents=None, decision_bytes=decision,
                                          decision_sha256=decision_sha, encode=tcm.Spy(), identity=tcm.IDENTITY,
                                          dim=tcm.DIM, root=out, name="candidate-4h", trusted_git=None, **strict)
        return
    built = cm.build_route_content_memory(enabled=True, messages=messages, documents=None, decision_bytes=decision,
                                          decision_sha256=decision_sha, encode=tcm.Spy(), identity=tcm.IDENTITY,
                                          dim=tcm.DIM, root=out, name="candidate-4h", trusted_git=None, **strict)
    projection = json.loads((out / "candidate-4h" / "projection.json").read_bytes())
    records = projection["messages"]["records"]
    assert built["records"] == len(records) == 3
    assert {record["meta"]["task_id"] for record in records} == set(FOUR_H_TASKS[:3])
    text = json.dumps(projection).lower()
    assert "rco" not in text and "sealed" not in text and "loydos" not in text and "vanha" not in text
