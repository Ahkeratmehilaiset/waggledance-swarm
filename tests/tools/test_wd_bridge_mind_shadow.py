"""Dormant Bridge Mind shadow pipeline: owned captures in, verified publication, delta and board text out.

The chain tests need the projector core (``tools/wd_bridge_mind.py``) and the missing-slot board
(``tools/wd_pr_missing_slot_board.py``). They are not on this branch's base, so those tests skip here and run in a
declared overlay with the exact core and board blobs; the refusal and isolation tests run everywhere.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
from pathlib import Path

import pytest

from tools import wd_bridge_mind_shadow as shadow

AS_OF = "2026-01-01T01:00:00Z"
HEAD = "a" * 40
GUARD_WORD = "PRIVATE_" + "MARKER"  # split, so this file carries no guarded literal
UUIDS = {agent: f"00000000-0000-4000-8000-{index:012x}" for index, agent in enumerate(
    ("claude-rco-1", "claude-rco-2", "codex-lead-1", "codex-tools-1", "fable-5"), 1)}


def _event(minute, agent, kind, status, payload, **extra):
    return {"ts_utc": f"2026-01-01T00:{minute:02d}:00Z", "agent": agent, "agent_uuid": UUIDS[agent], "type": kind,
            "status": status, "task_id": f"{agent}/task", "session_id": f"s-{agent}", "run_id": f"r-{agent}",
            "message": "shadow fixture", "payload": payload, **extra}


EVENTS = [
    _event(50, "codex-lead-1", "decision", "build_consensus_pass", {"pr": 7, "head": HEAD, "required_checks_green": True}),
    _event(51, "codex-tools-1", "decision", "build_consensus_pass", {"pr": 7, "head": HEAD}),
    _event(52, "claude-rco-1", "decision", "rco_pass", {"pr": 7, "head": HEAD}),
    _event(53, "fable-5", "claim", "claimed", {}, write_scope=["tools/x.py"]),
]


def _capture(events=EVENTS, observed=AS_OF):
    return {"schema": "wd.bridge-mind-capture.v1", "reader": "Read-AgentBridge.ps1 -Raw",
            "reader_stdout_sha256": hashlib.sha256(b"pinned stdout").hexdigest(), "observed_utc": observed,
            "events": copy.deepcopy(list(events))}


@pytest.fixture
def audit(tmp_path):
    root = tmp_path / ".codex-audit"
    root.mkdir()
    return root


def _write(path: Path, value) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _chain():
    """The core and board modules, or a skip on a tree that does not carry them yet."""
    return pytest.importorskip("tools.wd_bridge_mind"), pytest.importorskip("tools.wd_pr_missing_slot_board")


def test_a_capture_runs_the_whole_chain_and_the_receipt_names_every_digest(audit):
    core, _ = _chain()
    capture = _write(audit / "capture.json", _capture())
    receipt = shadow.run(capture, audit, audit / "run1")
    files = sorted(path.name for path in (audit / "run1").iterdir())
    assert files == ["board.txt", "receipt.json", "reflected.publication.json"]
    assert receipt["capture"]["sha256"] == hashlib.sha256(capture.read_bytes()).hexdigest()
    assert receipt["board_text_sha256"] == hashlib.sha256((audit / "run1" / "board.txt").read_bytes()).hexdigest()
    assert json.loads((audit / "run1" / "receipt.json").read_text(encoding="ascii")) == receipt
    verified = core.read_snapshot(audit / "run1" / "reflected.publication.json", audit_root=audit)
    assert receipt["publication"]["snapshot_sha256"] == hashlib.sha256(core.canonical_bytes(verified)).hexdigest()
    assert verified["source"]["log_identity"] == "capture-window:" + receipt["capture"]["sha256"]
    assert verified["source"]["as_of_utc"] == AS_OF and receipt["delta_lines"] is None
    assert [item["kind"] for item in verified["attention"]][0] == "last_missing_vote"
    assert receipt["global_question"]["question"].startswith("PR7 at aaaaaaaaaaaa is reported CI-green; missing: "
                                                             "claude-rco-2")
    assert "#7 aaaaaaaaaaaa" in (audit / "run1" / "board.txt").read_text(encoding="utf-8")
    # Event time only: the same capture gives the same bytes, in another directory, at any wall-clock time.
    again = shadow.run(capture, audit, audit / "run2")
    assert again["publication"]["snapshot_sha256"] == receipt["publication"]["snapshot_sha256"]
    assert again["board_text_sha256"] == receipt["board_text_sha256"]


def test_a_corrupted_previous_publication_is_refused_and_a_valid_one_gives_the_delta(audit):
    """Shadow S1 twin: the envelope is read through the core, so its digest is verified, never stripped by hand."""
    _chain()
    first = _write(audit / "capture1.json", _capture(EVENTS[:2], observed="2026-01-01T00:55:00Z"))
    shadow.run(first, audit, audit / "run1")
    publication = audit / "run1" / "reflected.publication.json"
    second = _write(audit / "capture2.json", _capture())
    receipt = shadow.run(second, audit, audit / "run2", previous_path=publication)
    assert receipt["delta_lines"] > 0 and (audit / "run2" / "delta.jsonl").read_text(encoding="utf-8")
    envelope = json.loads(publication.read_text(encoding="utf-8"))
    envelope["snapshot"]["source"]["duplicate_count"] = 0
    envelope["snapshot"]["source"]["as_of_utc"] = "2026-01-01T00:56:00Z"  # still valid JSON and shape, other bytes
    corrupted = _write(audit / "corrupted.publication.json", envelope)
    with pytest.raises(shadow.ShadowRefused, match="the core refused: snapshot_read_refused"):
        shadow.run(second, audit, audit / "run3", previous_path=corrupted)
    assert not (audit / "run3").exists()
    bare = _write(audit / "bare.json", envelope["snapshot"])  # a bare snapshot is not a publication
    with pytest.raises(shadow.ShadowRefused, match="snapshot_read_refused"):
        shadow.run(second, audit, audit / "run4", previous_path=bare)


def test_a_guarded_word_in_the_board_text_refuses_the_run_and_writes_no_board(audit, monkeypatch):
    """Every output is guarded: the board text is rendered through the board's guarded serializer, so a renderer
    that emits a guarded word refuses the run before board.txt or the receipt is written."""
    _, board = _chain()
    real = board.render_mind_text
    monkeypatch.setattr(board, "render_mind_text", lambda view: real(view) + "note " + GUARD_WORD + "\n")
    capture = _write(audit / "capture.json", _capture())
    with pytest.raises(shadow.ShadowRefused, match="the board refused the verified snapshot"):
        shadow.run(capture, audit, audit / "run")
    assert sorted(path.name for path in (audit / "run").iterdir()) == ["reflected.publication.json"]


@pytest.mark.parametrize("state", ["OPEN", "MERGED"])
def test_a_pr_context_passes_through_to_the_reflection_and_is_named_in_the_receipt(audit, state):
    _chain()
    capture = _write(audit / "capture.json", _capture())
    context = {"schema": "wd.bridge-mind-pr-context.v1", "authority": "none",
               "capture_sha256": hashlib.sha256(b"github capture").hexdigest(), "observed_utc": AS_OF,
               "charter": {"loaded": True, "charter_blob": "c" * 40, "classifier_blob": "d" * 40},
               "prs": {"7": {"state": state, "head": HEAD, "head_ref_name": "lane/pr-7", "path_class": None}}}
    path = _write(audit / "context.json", context)
    receipt = shadow.run(capture, audit, audit / "run", context_path=path)
    assert receipt["context"]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    kinds = [item["kind"] for item in json.loads((audit / "run" / "reflected.publication.json").read_text(
        encoding="utf-8"))["snapshot"]["attention"]]
    assert ("last_missing_vote" in kinds) is (state == "OPEN")


@pytest.mark.parametrize("change, message", [
    (lambda c: c.update(schema="wd.bridge-mind-capture.v0"), "not a wd.bridge-mind-capture.v1 object"),
    (lambda c: c.pop("reader_stdout_sha256"), "not a wd.bridge-mind-capture.v1 object"),
    (lambda c: c.update(reader="Get-Content events.jsonl"), "does not name its pinned reader"),
    (lambda c: c.update(reader_stdout_sha256="AB" * 32), "does not name its pinned reader"),
    (lambda c: c.update(observed_utc="2026-01-01 01:00"), "does not name its pinned reader"),
    (lambda c: c.update(events={"0": {}}), "not a list of objects"),
    (lambda c: c.update(events=[[]]), "not a list of objects"),
])
def test_a_capture_that_does_not_name_its_pinned_reader_and_time_is_refused(change, message):
    capture = _capture()
    change(capture)
    with pytest.raises(shadow.ShadowRefused, match=message):
        shadow.read_capture(capture)


def test_inputs_and_outputs_must_stay_inside_the_owned_audit_root(audit, tmp_path):
    capture = _write(audit / "capture.json", _capture())
    outside = _write(tmp_path / "capture.json", _capture())
    other = tmp_path / "audit"
    other.mkdir()
    (audit / "exists").mkdir()
    cases = [
        (lambda: shadow._owned(outside, audit), "not inside the audit root"),
        (lambda: shadow._owned(capture, other), "not a .codex-audit directory"),
        (lambda: shadow._owned(audit / "sub" / ".." / "capture.json", audit), "climbs out"),
        (lambda: shadow._owned(audit / "exists", audit, new=True), "already exists"),
        (lambda: shadow._owned(audit / "missing.json", audit), "does not exist"),
    ]
    for call, message in cases:
        with pytest.raises(shadow.ShadowRefused, match=message):
            call()
    assert shadow._owned(capture, audit) == capture


def test_a_missing_core_or_board_refuses_with_a_fixed_message_and_no_fallback(audit, monkeypatch):
    capture = _write(audit / "capture.json", _capture())
    real = shadow.importlib.import_module

    def without(name, *args, **kwargs):
        if name in ("tools.wd_bridge_mind", "tools.wd_pr_missing_slot_board"):
            raise ImportError(name)
        return real(name, *args, **kwargs)

    monkeypatch.setattr(shadow.importlib, "import_module", without)
    with pytest.raises(shadow.ShadowRefused, match="a required Mind module is missing: tools.wd_bridge_mind"):
        shadow.run(capture, audit, audit / "run")
    assert not (audit / "run").exists()
    assert shadow.main(["--capture", str(capture), "--audit-root", str(audit), "--out-dir", str(audit / "run")]) == 2


def test_nothing_in_tools_the_bridge_bin_or_waggledance_names_the_shadow_pipeline():
    """Dormant: the pipeline is run by hand on owned captures; no gate, Monitor or bridge code reaches it."""
    root = Path(shadow.__file__).resolve().parents[1]
    readers = [path for path in (root / "tools").glob("*.py") if path.name != "wd_bridge_mind_shadow.py"]
    readers += list((root / ".agent-bridge" / "bin").glob("*.ps1"))
    readers += list((root / "waggledance").rglob("*.py"))
    assert len(readers) > 100
    assert not [path.name for path in readers
                if "wd_bridge_mind_shadow" in path.read_text(encoding="utf-8", errors="replace")]


def test_the_shadow_pipeline_reads_no_log_and_imports_no_gate_bridge_writer_or_process():
    source = Path(shadow.__file__).read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert imported <= {"__future__", "argparse", "hashlib", "importlib", "json", "os", "pathlib", "re", "sys",
                        "typing", "tools", "waggledance.core.bridge_log_reader", "tools.bridge_compact_view"}
    for word in ("subprocess", "events.jsonl", "Write-AgentEvent", "check_bridge_changes_requested",
                 "check_rco_pass_present", "idle_consensus_auto_merge", "merge_with_bridge_receipt", "urllib",
                 "socket", '"gh"', "'gh'", "datetime.now", "time.time"):
        assert word not in source
