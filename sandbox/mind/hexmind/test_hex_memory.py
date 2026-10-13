import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("faiss")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import hex_memory as hm  # noqa: E402

LESSON = """---
name: feedback-lowercase-before-a-case-sensitive-check
description: a leak check after lossy lowercasing misses a case-sensitive policy
---
Lowercasing the text before a case-sensitive leak check lets a mixed-case variant through.
"""


def event(ts, agent, kind, message, status="info"):
    return {"agent": agent, "ts_utc": ts, "type": kind, "status": status, "task_id": f"{agent}/t", "message": message}


def capture(tmp_path):
    events = [
        event("2026-10-12T07:36:00Z", "claude-rco-2", "message", "status system health uptime of the monitor"),
        event("2026-10-12T08:00:00Z", "codex-lead-1", "message", "composition of the nine members for the merge"),
        event("2026-10-12T09:00:00Z", "claude-rco-1", "finding", "veto: changes requested on the gate", "finding"),
        event("2026-10-12T09:30:00Z", "codex-lead-1", "claim", "claim bookkeeping"),
        event("2026-10-13T06:00:00Z", "codex-tools-1", "message", "later event that must not leak into the past"),
    ]
    path = tmp_path / "capture.json"
    path.write_text(json.dumps({"events": events}), encoding="utf-8")
    lessons = tmp_path / "lessons"
    lessons.mkdir()
    (lessons / "feedback_lowercase_case_check_20261009.md").write_text(LESSON, encoding="utf-8")
    (lessons / "MEMORY.md").write_text("index only", encoding="utf-8")
    return path, lessons


def build(tmp_path, before=None, embedder="hash"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    path, lessons = capture(tmp_path)
    out = tmp_path / "index"
    argv = ["build", "--capture", str(path), "--lessons", str(lessons), "--out", str(out), "--embedder", embedder]
    if before:
        argv += ["--before", before]
    assert hm.main(argv) == 0
    return out, json.loads((out / "hex-memory-receipt.json").read_text(encoding="ascii"))


def test_records_land_in_wd_cells_with_event_refs_and_lessons_in_learning(tmp_path):
    out, receipt = build(tmp_path)
    assert receipt["records"] == 5  # 4 talking events + 1 lesson; the claim and MEMORY.md are not records
    assert receipt["by_cell"]["learning"] == 1 and receipt["by_cell"]["safety"] == 1
    meta = json.loads((out / "faiss" / "cell_safety" / "meta.json").read_text(encoding="utf-8"))
    assert len(meta["doc_ids"][0]) == 64 and meta["metadata"][0]["type"] == "finding"
    learning = json.loads((out / "faiss" / "cell_learning" / "meta.json").read_text(encoding="utf-8"))
    assert learning["doc_ids"][0].startswith("lesson:feedback_lowercase_case_check_20261009@")


def test_before_keeps_every_later_event_and_same_day_lesson_out(tmp_path):
    _, receipt = build(tmp_path, before="2026-10-12T09:00:00Z")
    assert receipt["records"] == 3  # two events before 09:00 and the 10-09 lesson; the finding at 09:00 is not before


def test_merge_reaches_the_learning_cell_where_wd_hybrid_stops_at_the_local_cell(tmp_path):
    out, _ = build(tmp_path)
    memory = hm.Memory(out)
    question = "seen before? lowercasing before a case-sensitive leak check"
    merged = memory.recall(question, k=2, route="merge")
    assert merged["hits"][0]["doc_id"].startswith("lesson:") and merged["hits"][0]["cell"] == "learning"
    hybrid = memory.recall(question, k=2, route="wd_hybrid")  # general holds 2 records = k: no room left
    assert [hit["cell"] for hit in hybrid["hits"]] == ["general", "general"]


def test_a_record_the_privacy_guard_refuses_is_counted_and_never_stored(tmp_path, monkeypatch):
    token = "zz" + "refuse" + "zz"
    monkeypatch.setattr(hm.ws, "_has_marker", lambda text: token in text)
    path, lessons = capture(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["events"][1]["message"] += " " + token
    path.write_text(json.dumps(data), encoding="utf-8")
    out = tmp_path / "index"
    assert hm.main(["build", "--capture", str(path), "--lessons", str(lessons), "--out", str(out),
                    "--embedder", "hash"]) == 0
    receipt = json.loads((out / "hex-memory-receipt.json").read_text(encoding="ascii"))
    assert receipt["refused_by_privacy_guard"] == {"event": 1} and receipt["records"] == 4
    stored = "".join(p.read_text(encoding="utf-8") for p in (out / "faiss").rglob("meta.json"))
    assert token not in stored


def test_the_hash_embedder_is_byte_identical_across_builds(tmp_path):
    first, _ = build(tmp_path / "a")
    second, _ = build(tmp_path / "b")
    for cell in ("general", "learning", "safety", "system"):
        left, right = first / "faiss" / f"cell_{cell}", second / "faiss" / f"cell_{cell}"
        if left.exists():
            assert (left / "index.faiss").read_bytes() == (right / "index.faiss").read_bytes()
