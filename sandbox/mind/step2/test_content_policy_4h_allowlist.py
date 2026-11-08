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
    policy = allowlist_policy()
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
