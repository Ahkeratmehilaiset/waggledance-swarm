"""The v2 content policy pair (Lead GO 2026-11-08 21:33Z on lead-mind-content-policy-current-fable-20261108-v1, AL1
successor 21:57Z on lead-mind-policy-al1-fable-20261108-v1).

Policy DATA only. ``configs/mind/content_message_policy_v2.json`` declares the true ``wd.mind-content-policy.v2``
schema with ``approved_source_refs`` EMPTY, so it admits nothing until a reviewed exact ref list is pinned (default
closed); it keeps every v1 lane, type, limit and the empty removal list and lists exactly the approved current
non-review task ids beside the production task. ``configs/mind/content_non_review_policy_v2.json`` lists the same
ids so the content allowlist is a subset (``policy_not_subset`` otherwise). The v1 files are untouched and stay a
distinct schema: a v1 document never carries ``approved_source_refs`` and a v2 document never loads without it.
Every exclusion stays in code and is proved here against the real v2 bytes with synthetic rows and a synthetic,
unreviewed ref list: a review-lane author, a row addressed to a review lane, decision and finding typed rows, a
sealed-named or unlisted task and a credential-bearing text never produce a record even when their refs are listed.
"""
import hashlib
import json
from pathlib import Path

import pytest

from sandbox.mind.step2 import content_memory as cm
from sandbox.mind.step2 import shared_capture_adapter as adapter
from sandbox.mind.step2 import shared_content_projection as cp
from sandbox.mind.step2 import shared_index_builder as sib
from sandbox.mind.step2 import shared_projection as sp
from sandbox.mind.step2 import test_content_memory as tcm
from sandbox.mind.step2 import test_content_route as tcr
from sandbox.mind.step2 import test_shared_content_projection as tcp
from tools.bridge_compact_view import event_id

ROOT = Path(__file__).resolve().parents[3]
MESSAGE_V1 = ROOT / "configs" / "mind" / "content_message_policy_v1.json"
PILOT_V1 = ROOT / "configs" / "mind" / "pilot_non_review_policy.json"
MESSAGE_V2 = ROOT / "configs" / "mind" / "content_message_policy_v2.json"
NON_REVIEW_V2 = ROOT / "configs" / "mind" / "content_non_review_policy_v2.json"

PRODUCTION = "lead-mind-production-20261031"
CURRENT = "lead-mind-current-recall-policy-fable-20261108-v1"
PACKAGE = "lead-mind-4h-package-20261108"
APPROVED = (PRODUCTION, PACKAGE, "lead-mind-4h-integration-tools-20261108-v1",
            "lead-mind-4h-tools-package-20261108-v1", "lead-mind-4h-fable-memory-20261108-v1",
            "lead-mind-4h-live-anchor-fable-20261108-v1", "lead-mind-final-head-memory-fable-20261108-v1", CURRENT)
UNLISTED_CURRENT = "lead-mind-ci-faiss-fix-20261108-v1"
RCO_TASK = "lead-mind-orchestrator-rco1-20261108-v1"
SEALED_TASK = "lead-mind-4h-sealed-eval-20261108-v1"


def pinned(path):
    raw = path.read_bytes()
    return raw, hashlib.sha256(raw).hexdigest()


def strict_v2():
    raw, sha = pinned(NON_REVIEW_V2)
    synthetic = tcr.strict_pins()
    return {"policy_bytes": raw, "policy_sha256": sha, "roster_bytes": synthetic["roster_bytes"],
            "roster_sha256": synthetic["roster_sha256"]}


def test_v2_message_policy_is_the_true_v2_schema_default_closed_with_the_v1_shape_and_the_approved_ids():
    v1 = cp.load_content_policy(*pinned(MESSAGE_V1))
    v2 = cp.load_content_policy(*pinned(MESSAGE_V2))
    doc = json.loads(MESSAGE_V2.read_bytes())
    assert doc["schema"] == cp.POLICY_SCHEMA_EXACT_SOURCES == "wd.mind-content-policy.v2"
    assert doc["approved_source_refs"] == [] and v2["approved_source_refs"] == frozenset()
    assert v2["approved_tasks"] == frozenset(APPROVED)
    assert len(APPROVED) == 8 and len(doc["approved_tasks"]) == 8
    for key in ("lanes", "types", "removed_source_refs", "max_message_chars", "max_chunk_chars"):
        assert v2[key] == v1[key], key
    assert v2["lanes"] == frozenset({tcp.LEAD, tcp.TOOLS, tcp.FABLE}) and tcp.RCO1 not in v2["lanes"]
    assert set(v2) == set(v1) | {"approved_source_refs"}


def test_v1_remains_a_distinct_schema_without_exact_source_admission():
    v1_doc = json.loads(MESSAGE_V1.read_bytes())
    assert v1_doc["schema"] == cp.POLICY_SCHEMA == "wd.mind-content-policy.v1"
    assert "approved_source_refs" not in v1_doc and "approved_source_refs" not in cp.load_content_policy(*pinned(MESSAGE_V1))
    v2_doc = json.loads(MESSAGE_V2.read_bytes())
    for mutated in (dict(v1_doc, approved_source_refs=[]),  # a v1 document cannot carry the v2 key
                    {k: v for k, v in v2_doc.items() if k != "approved_source_refs"},  # a v2 document needs it
                    dict(v2_doc, schema=cp.POLICY_SCHEMA)):  # the v1 schema with the v2 key is neither
        data = tcr.raw(mutated)
        with pytest.raises(cp.ContentRefused, match="^policy_refused$"):
            cp.load_content_policy(data, tcr.sha(data))


def test_v2_non_review_policy_lists_the_same_ids_and_the_content_list_is_a_subset():
    strict = strict_v2()
    tasks, roster = adapter.strict_pins(strict["policy_bytes"], strict["policy_sha256"], strict["roster_bytes"],
                                        strict["roster_sha256"])
    assert tasks == frozenset(APPROVED) and set(roster) == sp.LANES
    assert json.loads(NON_REVIEW_V2.read_bytes()) == {"approved_tasks": sorted(APPROVED),
                                                      "schema": "wd.q4-non-review-policy.v1"}
    assert cp.load_content_policy(*pinned(MESSAGE_V2))["approved_tasks"] <= tasks


def test_the_v1_files_are_untouched_so_the_status_and_card_routes_do_not_widen():
    assert json.loads(PILOT_V1.read_bytes())["approved_tasks"] == [PRODUCTION]
    assert json.loads(MESSAGE_V1.read_bytes())["approved_tasks"] == [PRODUCTION]


def test_every_approved_id_fits_the_grammars_and_names_no_review_lane():
    for task in APPROVED:
        assert sp.TASK.fullmatch(task) is not None
        assert not any(marker in task for marker in sp.SEALED_MARKERS)
        assert adapter._REVIEW_TASK.search(task) is None and "rco" not in task
    assert adapter._REVIEW_TASK.search(RCO_TASK) is not None


@pytest.mark.parametrize("path", [MESSAGE_V2, NON_REVIEW_V2])
def test_v2_files_are_plain_ascii_json_with_sorted_keys_and_sorted_ids(path):
    raw = path.read_bytes()
    doc = json.loads(raw.decode("ascii"))
    assert list(doc) == sorted(doc)
    assert doc["approved_tasks"] == sorted(doc["approved_tasks"]) and len(set(doc["approved_tasks"])) == 8
    assert raw.endswith(b"}\n") and b"\r" not in raw


def test_an_rco_id_added_to_the_v2_non_review_list_refuses_the_whole_policy():
    doc = json.loads(NON_REVIEW_V2.read_bytes())
    doc["approved_tasks"] = doc["approved_tasks"] + [RCO_TASK]
    data = tcr.raw(doc)
    synthetic = tcr.strict_pins()
    with pytest.raises(ValueError):
        adapter.strict_pins(data, tcr.sha(data), synthetic["roster_bytes"], synthetic["roster_sha256"])


def rows_v2():
    rco_author = tcp.row("Katselmoija kirjoitti.", minute=4, agent=tcp.RCO1, task_id=CURRENT)
    rco_author["agent_uuid"] = tcp.ROSTER[tcp.RCO1]
    return [
        tcp.row("Lead antoi nykyisen tehtavan.", minute=1, task_id=CURRENT),
        tcp.row("Fable eteni paketissa.", minute=2, type="status", agent=tcp.FABLE, to=tcp.LEAD, task_id=PACKAGE),
        tcp.row("Tools paatti tuotantotehtavan.", minute=3, type="done", agent=tcp.TOOLS, task_id=PRODUCTION),
        rco_author,
        tcp.row("Leadin viesti katselmoijalle.", minute=5, to=tcp.RCO2, task_id=CURRENT),
        tcp.row("Listaamaton nykyinen tehtava.", minute=6, task_id=UNLISTED_CURRENT),
        tcp.row("Suljettu tehtava.", minute=7, task_id=SEALED_TASK),
        tcp.row("Loydos nykyisessa tehtavassa.", minute=8, type="finding", task_id=CURRENT),
        tcp.row("Paatos nykyisessa tehtavassa.", minute=9, type="decision", task_id=CURRENT),
        tcp.row("Avain token=abcdefghijklmnopqrstuvwxyz012345 unohtui lokiin.", minute=10, task_id=CURRENT),
    ]


ALLOWED_INDEXES = (0, 1, 2)  # the lead message, the fable status and the tools done row


def build_v2(tmp_path, *, strict, policy=None):
    pytest.importorskip("faiss")
    path, generation, cursor, prefix_sha = tcr.write_prefix(tmp_path / "prefix", rows_v2())
    policy, policy_sha = pinned(MESSAGE_V2) if policy is None else (policy, tcr.sha(policy))
    prefix = {"sha256": prefix_sha, "offset": cursor.offset, "generation": cursor.generation}
    decision, decision_sha = tcr.decision_of(prefix=prefix, message_pin=policy_sha, strict=strict)
    out = tmp_path / "out"
    out.mkdir()
    messages = {"source_path": str(path), "generation_path": str(generation), "end_cursor": cursor,
                "policy_bytes": policy, "policy_sha256": policy_sha}
    built = cm.build_route_content_memory(enabled=True, messages=messages, documents=None, decision_bytes=decision,
                                          decision_sha256=decision_sha, encode=tcm.Spy(), identity=tcm.IDENTITY,
                                          dim=tcm.DIM, root=out, name="policy-v2", trusted_git=None, **strict)
    return built, out / "policy-v2"


def test_the_shipped_v2_bytes_admit_nothing_at_the_projection_and_at_the_route(tmp_path):
    """Default closed: with ``approved_source_refs`` empty no row of any approved task becomes a record, no count
    is disclosed, and the route refuses to build an empty memory."""
    raw, sha = pinned(MESSAGE_V2)
    rows = {event_id(event): event for event in rows_v2()}
    result = cp.build_content_projection(enabled=True, rows=rows, policy_bytes=raw, policy_sha256=sha,
                                         roster=tcp.ROSTER)
    assert result["records"] == [] and result["refused"] == {} and result["policy_sha256"] == sha
    with pytest.raises((sib.BuildRefused, cm.ContentMemoryRefused), match="^projection_empty$"):
        build_v2(tmp_path, strict=strict_v2())
    assert not (tmp_path / "out" / "policy-v2").exists()


def test_a_synthetic_unreviewed_ref_list_admits_exactly_the_listed_allowed_rows_and_no_excluded_row():
    """Synthetic refs only (nothing here is a reviewed list): listing a ref is admission, never an override of the
    lane, recipient, type, task and screen exclusions, which keep every excluded row out even when listed."""
    rows = rows_v2()
    doc = json.loads(MESSAGE_V2.read_bytes())
    doc["approved_source_refs"] = sorted(event_id(event) for event in rows)  # every synthetic ref, excluded ones too
    data = tcr.raw(doc)
    result = cp.build_content_projection(enabled=True, rows={event_id(e): e for e in rows}, policy_bytes=data,
                                         policy_sha256=tcr.sha(data), roster=tcp.ROSTER)
    assert {r["provenance"]["source_event_ref"] for r in result["records"]} == {event_id(rows[i]) for i in ALLOWED_INDEXES}
    assert {(r["meta"]["agent"], r["meta"]["task_id"], r["meta"]["type"]) for r in result["records"]} == {
        (tcp.LEAD, CURRENT, "message"), (tcp.FABLE, PACKAGE, "status"), (tcp.TOOLS, PRODUCTION, "done")}
    # listed but excluded: the two unlisted-task rows count, the credential drop counts, the review-lane author,
    # the review-lane recipient and the decision and finding typed rows are silent
    assert result["refused"] == {"task_not_approved": 2, "credential": 1}
    text = json.dumps(result).lower()
    # ("token-redacted" is the masking label for any run of 16 or more chars, so the word token itself may appear)
    for absent in ("rco", "sealed", "suljettu", "loydos", "paatos", "avain", "unohtui", "abcdefghijklmnop",
                   "listaamaton", "katselmoija"):
        assert absent not in text, absent


def test_the_v2_content_list_is_refused_against_the_untouched_v1_pilot_policy(tmp_path):
    raw, sha = pinned(PILOT_V1)
    synthetic = tcr.strict_pins()
    strict = {"policy_bytes": raw, "policy_sha256": sha, "roster_bytes": synthetic["roster_bytes"],
              "roster_sha256": synthetic["roster_sha256"]}
    with pytest.raises(cm.ContentMemoryRefused, match="^policy_not_subset$"):
        build_v2(tmp_path, strict=strict)
    assert not (tmp_path / "out" / "policy-v2").exists()
