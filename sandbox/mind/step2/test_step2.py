"""Tests for HEX MEMORY STEP 2 on synthetic data: cells, memory binding and the Q4 rule (no e5, no F: data)."""

import errno
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cell as cell_mod  # noqa: E402
import corpus as corpus_mod  # noqa: E402
import encode as encode_mod  # noqa: E402
import memory as memory_mod  # noqa: E402
import q4 as q4_mod  # noqa: E402
import run_q4 as run_mod  # noqa: E402

CUTOFF = "2026-10-14T00:00:00.000000Z"
DIM = 16
TOPICS = {
    "json": "decoded guard json string escaped marker raw text regex",
    "retry": "watcher retry reasons narrowed fatal refusal startup",
    "digest": "powershell convertto json escaping digest recompute edition",
    "nonce": "reply nonce request id echo verbatim requester lookup",
}


class FakeEncoder:
    """Deterministic bag-of-words vectors, so the tests need no model."""

    def vector(self, text):
        out = np.zeros(DIM, dtype=np.float32)
        for word in q4_mod.tokens(text):
            out[int(hashlib.sha256(word.encode()).hexdigest(), 16) % DIM] += 1.0
        norm = np.linalg.norm(out)
        return (out / norm if norm else np.ones(DIM, dtype=np.float32) / np.sqrt(DIM)).astype("<f4")

    def query(self, text):
        return self.vector(text)


def lesson(name, body, mtime="2026-10-12T10:00:00.000000Z"):
    text = f"---\nname: {name}\ndescription: {body}\n---\n\n{body} details about {name}."
    return {"record_id": f"lesson:{name}@{hashlib.sha256(text.encode()).hexdigest()[:12]}", "kind": "lesson",
            "intent": "lesson", "time_utc": "2026-10-12T00:00:00.000000Z", "text": text, "summary": body,
            "stored_text": text, "meta": {"kind": "lesson", "file": f"{name}.md"},
            "provenance": {"source_kind": "memory_note", "file": f"{name}.md", "mtime_utc": mtime,
                           "source_sha256": "0" * 64, "used_sha256": "0" * 64, "transform": None,
                           "stored_text_cut_by_guard": False}}


def event(ts, message, kind="message", task="lane/task-1"):
    text = f"{kind} info claude-rco-1 {task}: {message}"
    compact = hashlib.sha256(text.encode()).hexdigest()
    return {"record_id": f"event:{compact[:16]}", "kind": "event", "intent": "chat", "time_utc": ts, "text": text,
            "summary": text[:600], "stored_text": text,
            "meta": {"kind": "event", "ts_utc": ts, "agent": "claude-rco-1", "type": kind, "task_id": task},
            "provenance": {"source_kind": "bridge_event_capture", "capture": "c.json", "capture_sha256": "1" * 64,
                           "index": 0, "compact_event_id": compact,
                           "event_ref": f"{ts}|claude-rco-1|{kind}|{task}", "stored_text_cut_by_guard": False}}


def records():
    rows = [lesson(f"lesson_{key}", body) for key, body in TOPICS.items()]
    rows += [lesson(f"filler_{i}", f"unrelated note number {i} about colours gardens and menus") for i in range(6)]
    rows.append(event("2026-10-13T08:00:00.000000Z", "system cpu memory status health uptime disk process"))
    rows.append(event("2026-10-13T09:00:00.000000Z", "watcher retry reasons narrowed again in pr review"))
    return sorted(rows, key=lambda row: row["record_id"])


def write_inputs(root, rows):
    corpus_dir, vectors_dir = root / "corpus", root / "vectors"
    corpus_dir.mkdir(parents=True)
    vectors_dir.mkdir(parents=True)
    body = "".join(corpus_mod.canonical(row) + "\n" for row in rows).encode("utf-8")
    (corpus_dir / "corpus.jsonl").write_bytes(body)
    manifest = {"cutoff_utc": CUTOFF, "records": len(rows), "corpus_sha256": hashlib.sha256(body).hexdigest(),
                "record_ids_sha256": hashlib.sha256("\n".join(r["record_id"] for r in rows).encode()).hexdigest()}
    (corpus_dir / "corpus-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    fake = FakeEncoder()
    vectors = np.vstack([np.vstack([fake.vector(row[encode_mod.VIEW_FIELD[view]]) for row in rows])
                         for view in encode_mod.VIEWS]).astype("<f4")
    raw = vectors.tobytes()
    (vectors_dir / "vectors.f32").write_bytes(raw)
    (vectors_dir / "vectors-manifest.json").write_text(json.dumps({
        "views": list(encode_mod.VIEWS), "count": len(rows), "rows": 2 * len(rows), "dim": DIM,
        "corpus_sha256": manifest["corpus_sha256"], "record_ids_sha256": manifest["record_ids_sha256"],
        "vectors_sha256": hashlib.sha256(raw).hexdigest(), "encoder": {"model": "fake", "dim": DIM}}),
        encoding="utf-8")
    return corpus_dir, vectors_dir


@pytest.fixture()
def built(tmp_path):
    corpus_dir, vectors_dir = write_inputs(tmp_path, records())
    memory_dir = tmp_path / "memory"
    memory_mod.build(corpus_dir, vectors_dir, memory_dir)
    return memory_dir


def ask(memory_dir, situation, when="2026-10-14T12:00:00Z"):
    return q4_mod.Q4(memory_mod.Memory(memory_dir), FakeEncoder()).ask(situation, when)


def test_build_places_lessons_in_learning_and_binds_every_cell(built):
    loaded = memory_mod.Memory(built)
    status = loaded.status()
    assert all(row["status"] == "HEALTHY" for row in status.values())
    assert status["learning"]["count"] == 2 * 10
    assert set(loaded.memory["cells"]) == set(status)


def test_answer_needs_all_three_views_and_shows_refs_only(built):
    out = ask(built, "the decoded guard checks a json string with an escaped marker in raw text")
    assert out["status"] == "ANSWER" and out["abstain"] is False
    assert out["results"][0]["ref"] == "lesson_json.md"
    assert all(set(result["view_ranks"].values()) <= {1, 2, 3} for result in out["results"])
    assert "text" not in json.dumps(out["results"]).replace("time_utc", "")


def test_unrelated_situation_abstains_with_reason(built):
    out = ask(built, "quantum chromodynamics lattice gauge symmetry")
    assert out["status"] == "ABSTAIN" and out["abstain"] is True
    assert out["reason"] in {"no_view_consensus", "empty_cell"}
    assert out["results"] == []


def test_records_not_before_the_situation_are_never_candidates(built):
    out = ask(built, "watcher retry reasons narrowed again in pr review", when="2026-10-13T08:30:00Z")
    refs = [result["ref"] for result in out["results"]]
    assert not any("09:00:00" in ref for ref in refs)
    assert out["telemetry"]["excluded_not_before_situation"] == 1
    late = ask(built, "watcher retry reasons narrowed again in pr review", when="2026-10-13T09:00:00.5Z")
    assert late["telemetry"]["excluded_not_before_situation"] == 1  # inside the 1 s guard band


def test_self_match_is_excluded(built):
    situation = "watcher retry reasons narrowed again in pr review"
    out = ask(built, situation)
    assert out["telemetry"]["excluded_self_match"] >= 1
    assert all("2026-10-13T09:00:00" not in result["ref"] for result in out["results"])


def test_quarantined_cell_degrades_never_abstains(built):
    meta = next((built / "cells" / "learning").glob("gen-*/faiss/cell_learning/meta.json"))
    meta.write_bytes(meta.read_bytes().replace(b"lesson_json", b"lesson_jsoX"))
    loaded = memory_mod.Memory(built)
    assert loaded.views["learning"].status == "QUARANTINED"
    for situation in ("quantum chromodynamics lattice gauge symmetry", "decoded guard json escaped marker raw text"):
        out = q4_mod.Q4(loaded, FakeEncoder()).ask(situation, "2026-10-14T12:00:00Z")
        assert out["status"] == "DEGRADED" and out["abstain"] is False and out["degraded"] is True
        assert all(result["fallback"] for result in out["results"])
        assert out["degraded_cells"][0]["cell"] == "learning"


def test_consistent_cell_rollback_fails_against_memory_binding(built):
    cell_dir = built / "cells" / "learning"
    memory_mod.Memory(built).rebuild("learning")
    snapshot = (built / "memory.json").read_bytes()
    log_lines = (cell_dir / "LOG.jsonl").read_bytes().splitlines(keepends=True)
    (cell_dir / "LOG.jsonl").write_bytes(log_lines[0])
    first = json.loads(log_lines[0])
    cell_mod.write_atomic(cell_dir / "current.json", cell_mod.canonical(
        {"generation": first["generation"], "seq": first["seq"], "receipt_sha256": first["receipt_sha256"]}).encode())
    assert cell_mod.load(cell_dir, "learning").healthy
    assert (built / "memory.json").read_bytes() == snapshot
    assert memory_mod.Memory(built).views["learning"].reason == "memory_binding_mismatch"


def test_rebuild_keeps_content_and_makes_a_new_generation(built):
    before = cell_mod.load(built / "cells" / "learning", "learning")
    pointer = memory_mod.Memory(built).rebuild("learning")
    after = memory_mod.Memory(built).views["learning"]
    assert pointer["generation"] == before.pointer["generation"] + 1
    assert after.healthy and after.receipt["content_sha256"] == before.receipt["content_sha256"]


def test_refused_inputs(built):
    assert ask(built, "   ")["status"] == "REFUSED"
    assert ask(built, "anything", when="2026-10-14 12:00")["status"] == "REFUSED"


def test_run_writes_the_grader_contract_and_never_reads_expected(built, tmp_path):
    cases = {"cases": [
        {"id": "P1", "situation": "decoded guard json string escaped marker raw text",
         "situation_time_utc": "2026-10-14T12:00:00Z"},
        {"id": "N1", "situation": "quantum chromodynamics lattice gauge symmetry",
         "situation_time_utc": "2026-10-14T12:00:00Z"}]}
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(cases), encoding="utf-8")
    run_mod.encode_mod.E5 = FakeEncoder
    manifest = run_mod.run(built, path, tmp_path / "out", "fresh")
    lines = [json.loads(line) for line in (tmp_path / "out" / "outputs.jsonl").read_text().splitlines()]
    assert [line["id"] for line in lines] == ["P1", "N1"]
    for line in lines:
        assert isinstance(line["abstain"], bool) and isinstance(line["degraded"], bool)
        assert set(line) <= {"id", "status", "abstain", "degraded", "fallback", "crash", "results", "reason", "error"}
        assert all(isinstance(result["ref"], str) for result in line["results"])
    assert manifest["outputs_sha256"] == hashlib.sha256((tmp_path / "out" / "outputs.jsonl").read_bytes()).hexdigest()


def test_publish_refuses_vectors_that_cannot_be_trusted(tmp_path):
    rows = [dict(row, record_id=f"{row['record_id']}#w", meta=dict(row["meta"], view="w"),
                 provenance=dict(row["provenance"], available_utc=row["time_utc"])) for row in records()[:2]]
    good = np.vstack([FakeEncoder().vector(row["text"]) for row in rows])
    for vectors, code in ((np.zeros_like(good), "vector_zero_norm"),
                          (np.where(good == good.max(), np.nan, good), "vector_not_finite")):
        with pytest.raises(cell_mod.CellError) as error:
            cell_mod.publish(tmp_path / "c", "learning", rows, vectors, dim=DIM, cutoff_utc=CUTOFF,
                             encoder={}, corpus_sha256="0" * 64)
        assert error.value.code == code
    shutil.rmtree(tmp_path / "c", ignore_errors=True)


# --- D18 (2026-10-19, fable-5): an orphan generation never blocks a later publish ------------------------------------
# From code, not observed: publish renames the staged generation to gen-NNNNNN BEFORE it appends the log line, and
# the append sits outside the staging cleanup. An error there (a full disk, say) or a crash leaves a directory the
# log never names, and every later publish computed the same generation from the log and was refused as
# generation_exists, so the cell never took a new generation again while load still reported the old one HEALTHY.
# Each test here was red on the D17 module first, except the last, a guard that passes on both.


class D18Crash(BaseException):
    """A crash stand-in that no ``except Exception`` catches."""


def d18_rows(start, stop):
    rows = records()[start:stop]
    return rows, np.vstack([FakeEncoder().vector(row["text"]) for row in rows])


def d18_publish(cell_dir, rows, vectors):
    return cell_mod.publish(cell_dir, "learning", rows, vectors, dim=DIM, cutoff_utc=CUTOFF, encoder={},
                            corpus_sha256="0" * 64)


def d18_tree(path):
    """Every file under ``path`` with its bytes, so an untouched directory compares equal."""
    return {str(item.relative_to(path)): item.read_bytes() for item in sorted(path.rglob("*")) if item.is_file()}


def test_d18_a_failed_log_append_after_the_rename_never_blocks_a_later_publish(tmp_path, monkeypatch):
    """The log append fails after generation 2 was renamed into place: that publish fails and releases its lock, the
    published memory stays generation 1, and the orphan gen-000002 stays as it is; the next publish takes generation
    3, never adopting, changing or removing the orphan."""
    cell_dir = tmp_path / "c"
    d18_publish(cell_dir, *d18_rows(0, 2))
    real_open = open

    def open_(path, mode="r", *args, **kwargs):
        if Path(path).name == "LOG.jsonl" and "a" in mode:
            raise OSError(errno.ENOSPC, "injected: no space left for the log line")
        return real_open(path, mode, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(cell_mod, "open", open_, raising=False)
        with pytest.raises(OSError):
            d18_publish(cell_dir, *d18_rows(2, 4))
    orphan = cell_dir / "gen-000002"
    assert orphan.is_dir() and not (cell_dir / "writer.lock").exists() and len(cell_mod.read_log(cell_dir)) == 1
    view = cell_mod.load(cell_dir, "learning")
    assert view.healthy and view.pointer["generation"] == 1  # the failure left the published memory as it was
    untouched = d18_tree(orphan)
    rows, vectors = d18_rows(4, 7)
    pointer = d18_publish(cell_dir, rows, vectors)
    assert pointer["generation"] == 3 and pointer["seq"] == 2  # one past the orphan; the log stays a gapless chain
    view = cell_mod.load(cell_dir, "learning")
    assert view.healthy and view.pointer == pointer
    assert [row["record_id"] for row in view.rows] == [row["record_id"] for row in rows]  # never the orphan's records
    assert [line["generation"] for line in cell_mod.read_log(cell_dir)] == [1, 3]
    assert d18_tree(orphan) == untouched


D18_CRASH = """
import json
import os
import sys
from pathlib import Path
import numpy as np
here, cell_dir, inputs = Path(sys.argv[1]), Path(sys.argv[2]), json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
sys.path.insert(0, str(here))
import cell
real_rename = os.rename

def rename_then_crash(source, target):
    real_rename(source, target)
    if Path(target).name.startswith("gen-"):
        os._exit(17)  # a hard crash: no except, no finally, no lock release

os.rename = rename_then_crash
cell.publish(cell_dir, "learning", inputs["rows"], np.array(inputs["vectors"], dtype="<f4"), dim=inputs["dim"],
             cutoff_utc=inputs["cutoff"], encoder={}, corpus_sha256="0" * 64)
"""


def test_d18_after_a_hard_crash_right_after_the_rename_removing_the_stale_lock_is_enough(tmp_path):
    """A child process publishing generation 2 dies right after the rename: no except and no finally, so its writer
    lock stays beside the orphan (a stale lock is never broken automatically). Removing that lock, the manual step
    for a stale lock, must be enough for the next publish; before D18 the orphan refused it forever."""
    cell_dir = tmp_path / "c"
    d18_publish(cell_dir, *d18_rows(0, 2))
    rows, vectors = d18_rows(2, 4)
    inputs = tmp_path / "inputs.json"
    inputs.write_text(json.dumps({"rows": rows, "vectors": vectors.tolist(), "dim": DIM, "cutoff": CUTOFF}),
                      encoding="utf-8")
    run = subprocess.run([sys.executable, "-B", "-c", D18_CRASH, str(HERE), str(cell_dir), str(inputs)],
                         capture_output=True, text=True)
    assert run.returncode == 17, run.stderr[-2000:]
    orphan = cell_dir / "gen-000002"
    assert orphan.is_dir() and (cell_dir / "writer.lock").exists() and len(cell_mod.read_log(cell_dir)) == 1
    assert cell_mod.load(cell_dir, "learning").pointer["generation"] == 1
    untouched = d18_tree(orphan)
    (cell_dir / "writer.lock").unlink()  # the manual step for a stale lock
    pointer = d18_publish(cell_dir, *d18_rows(4, 7))
    assert pointer["generation"] == 3 and cell_mod.load(cell_dir, "learning").healthy
    assert d18_tree(orphan) == untouched


def test_d18_the_next_generation_is_one_past_every_canonical_generation_entry(tmp_path):
    """A file at a generation name blocks that name as surely as a directory; names that publish never makes
    (other widths, other characters, a suffix) never move the count."""
    cell_dir = tmp_path / "c"
    d18_publish(cell_dir, *d18_rows(0, 2))
    (cell_dir / "gen-000005").write_bytes(b"a stray file at a generation name")
    for name in ("gen-5", "gen-0000009", "gen-00000x", "gen-000007.tmp"):
        (cell_dir / name).mkdir()
    pointer = d18_publish(cell_dir, *d18_rows(2, 4))
    assert pointer["generation"] == 6 and cell_mod.load(cell_dir, "learning").healthy
    assert (cell_dir / "gen-000005").read_bytes() == b"a stray file at a generation name"


def test_d18_generation_names_past_six_digits_count_too(tmp_path):
    cell_dir = tmp_path / "c"
    d18_publish(cell_dir, *d18_rows(0, 2))
    (cell_dir / "gen-1000000").mkdir()
    pointer = d18_publish(cell_dir, *d18_rows(2, 4))
    assert pointer["generation"] == 1000001 and cell_mod.load(cell_dir, "learning").healthy


def test_d18_a_logged_generation_is_never_numbered_again_even_when_its_directory_is_gone(tmp_path):
    """A guard (it passes on the D17 module too): the log, not the directory listing alone, names what was
    published, so a generation whose directory went missing is skipped as well."""
    cell_dir = tmp_path / "c"
    d18_publish(cell_dir, *d18_rows(0, 2))
    d18_publish(cell_dir, *d18_rows(2, 4))
    (cell_dir / "gen-000002").rename(tmp_path / "moved-gen-000002")
    assert cell_mod.load(cell_dir, "learning").reason == "generation_missing"
    pointer = d18_publish(cell_dir, *d18_rows(4, 7))
    assert pointer["generation"] == 3 and cell_mod.load(cell_dir, "learning").healthy
