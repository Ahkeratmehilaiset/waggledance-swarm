"""Synthetic-only tests. Every filesystem fixture stays under the audit root."""
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile

import pytest


SPEC = importlib.util.spec_from_file_location(
    "prospective_capture", Path(__file__).with_name("prospective_capture.py")
)
pc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pc)
AUDIT = Path("C:/Python/project2/.codex-audit/m1-p3-prospective-20261020/adapter-author")


def raw(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


@pytest.fixture
def synthetic():
    AUDIT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="synthetic-", dir=AUDIT) as directory:
        root = Path(directory)
        source = root / "corpus"
        source.mkdir()
        (source / "z.md").write_bytes(b"synthetic z")
        (source / "a.md").write_bytes(b"synthetic a")
        (source / "MEMORY.md").write_bytes(b"synthetic index excluded")
        (source / "ignore.txt").write_bytes(b"synthetic nonlesson")
        prereg = raw({"schema": "wd.m1-prospective-preregistration.v1", "status": "LOCKED", "study_id": "synthetic-study"})
        protocol = raw({"schema": "wd.m1-p3-protocol-lock.v1", "status": "LOCKED", "study_id": "synthetic-study"})
        out = root / "capture"
        identity = {
            "schema": "wd.m1-p3-capture-authorization.v1", "status": "LOCKED_CAPTURE_GO",
            "study_id": "synthetic-study", "capture_id": "synthetic-once",
            "source_dir": str(source), "destination": str(out),
            "preregistration_sha256": sha(prereg), "protocol_sha256": sha(protocol),
        }
        yield root, source, out, {"preregistration": prereg, "protocol": protocol, "identity": identity}


def test_capture_disabled_without_any_reads(synthetic, monkeypatch):
    _, source, out, kw = synthetic
    monkeypatch.setattr(pc, "_volume_check", lambda p: pytest.fail("disabled capture inspected disk"))
    with pytest.raises(pc.CaptureError, match="execute_required"):
        pc.capture_baseline(source, out, **kw)
    assert not out.exists()


def test_capture_roundtrip_sorted_sealed_exclusive(synthetic):
    _, source, out, kw = synthetic
    before = dt.datetime.now(dt.timezone.utc)
    receipt = pc.capture_baseline(source, out, execute=True, **kw)
    manifest_bytes = (out / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    assert [r["name"] for r in manifest["files"]] == ["a.md", "z.md"]
    assert receipt["manifest_sha256"] == sha(manifest_bytes)
    assert pc.parse_utc(manifest["sealed_utc"]) >= before
    for row in manifest["files"]:
        assert (out / "blobs" / row["sha256"]).read_bytes() == (source / row["name"]).read_bytes()
    assert pc.verify_baseline(out, expected_manifest_sha256=receipt["manifest_sha256"])["manifest_sha256"] == receipt["manifest_sha256"]
    with pytest.raises(pc.CaptureError, match="destination_exists"):
        pc.capture_baseline(source, out, execute=True, **kw)


@pytest.mark.parametrize("mutation", ["proposed", "hash", "unknown", "path"])
def test_capture_identity_refused_before_intent(synthetic, mutation):
    root, source, out, kw = synthetic
    if mutation == "proposed":
        kw["identity"]["status"] = "PROPOSED"
    elif mutation == "hash":
        kw["identity"]["protocol_sha256"] = "0" * 64
    elif mutation == "unknown":
        kw["identity"]["gold"] = "forbidden"
    else:
        kw["identity"]["destination"] = str(root / "different")
    with pytest.raises(pc.CaptureError):
        pc.capture_baseline(source, out, execute=True, **kw)
    assert not out.exists()


def test_outside_audit_and_overlap_refused(synthetic):
    _, source, _, kw = synthetic
    for out in (Path("C:/outside-audit-synthetic"), source / "nested"):
        kw["identity"]["destination"] = str(out)
        with pytest.raises(pc.CaptureError):
            pc.capture_baseline(source, out, execute=True, **kw)
        assert not out.exists()


def test_low_space_refused(synthetic, monkeypatch):
    _, source, out, kw = synthetic
    monkeypatch.setattr(pc.shutil, "disk_usage", lambda p: (0, 0, 1))
    with pytest.raises(pc.CaptureError, match="free_space"):
        pc.capture_baseline(source, out, execute=True, **kw)
    assert not out.exists()


def test_budget_and_partial_io_consume_destination(synthetic, monkeypatch):
    _, source, out, kw = synthetic
    monkeypatch.setattr(pc, "BUDGET_BYTES", 2000)
    (source / "a.md").write_bytes(b"synthetic-budget" * 120)
    with pytest.raises(pc.CaptureError, match="budget"):
        pc.capture_baseline(source, out, execute=True, **kw)
    assert (out / "intent.json").exists()
    assert not (out / "SEALED.json").exists()
    assert (out / "STOP_NOT_READY.json").exists()
    with pytest.raises(pc.CaptureError, match="destination_exists"):
        pc.capture_baseline(source, out, execute=True, **kw)


def test_partial_blob_io_is_terminal(synthetic, monkeypatch):
    _, source, out, kw = synthetic
    original = pc._exclusive_write
    def fail_blob(path, data):
        if path.parent.name == "blobs":
            with open(path, "xb") as stream:
                stream.write(b"partial")
            raise OSError("synthetic partial I/O")
        return original(path, data)
    monkeypatch.setattr(pc, "_exclusive_write", fail_blob)
    with pytest.raises(pc.CaptureError, match="capture_failed"):
        pc.capture_baseline(source, out, execute=True, **kw)
    assert (out / "intent.json").exists()
    assert (out / "STOP_NOT_READY.json").exists()
    assert not (out / "SEALED.json").exists()


def test_corrupt_blob_and_extra_file_refused(synthetic):
    _, source, out, kw = synthetic
    receipt = pc.capture_baseline(source, out, execute=True, **kw)
    blob = next((out / "blobs").iterdir())
    blob.write_bytes(b"synthetic corruption")
    with pytest.raises(pc.CaptureError):
        pc.verify_baseline(out, expected_manifest_sha256=receipt["manifest_sha256"])


def test_reparse_source_and_destination_refused(synthetic):
    root, source, out, kw = synthetic
    junction = root / "junction"
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(source)], capture_output=True)
    assert result.returncode == 0, "synthetic NTFS junction fixture failed"
    try:
        for changed_source, changed_out in ((junction, out), (source, junction / "out")):
            kw["identity"].update(source_dir=str(changed_source), destination=str(changed_out))
            with pytest.raises(pc.CaptureError, match="reparse"):
                pc.capture_baseline(changed_source, changed_out, execute=True, **kw)
    finally:
        os.rmdir(junction)


IDS = ["00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-000000000002", "00000000-0000-4000-8000-000000000003"]
SESSION = "00000000-0000-4000-8000-000000000010"


def records():
    rows = [
        {"type": "user", "uuid": IDS[0], "timestamp": "2026-10-20T10:00:02Z", "message": {"role": "user", "content": "Check the synthetic operation carefully."}},
        {"type": "assistant", "uuid": IDS[1], "timestamp": "2026-10-20T10:00:04.000Z", "message": {"role": "assistant", "content": [{"type": "text", "text": "I will inspect the synthetic state first."}]}},
        {"type": "assistant", "uuid": IDS[2], "timestamp": "2026-10-20T10:00:06.000000Z", "message": {"role": "assistant", "content": [{"type": "text", "text": "ACTION TEXT MUST NOT ENTER"}, {"type": "tool_use", "id": "toolu_synthetic1", "name": "Bash", "input": {"command": "PRIVATE SYNTHETIC ACTION", "description": "ACTION DESCRIPTION MUST NOT ENTER"}}]}},
    ]
    for row in rows:
        row["sessionId"] = SESSION
    return rows


def adapt(rows=None, **overrides):
    rows = records() if rows is None else rows
    lines = [raw(r) for r in rows]
    reference = {"session_id": SESSION, "uuid": IDS[2], "tool_use_id": "toolu_synthetic1", "record_sha256": sha(lines[-1]), "action_utc": rows[-1]["timestamp"]}
    kw = {"reference": reference, "baseline_utc": "2026-10-20T10:00:00.000000Z", "collection_start_utc": "2026-10-20T10:00:02.000000Z", "collection_end_utc": "2026-10-20T11:00:00.000000Z", "timestamp_resolution_us": 1_000_000}
    kw.update(overrides)
    return pc.extract_action_evidence(b"\n".join(lines) + b"\n", **kw)


def test_exact_action_and_preaction_literal_only():
    result = adapt()
    assert result["uuid"] == IDS[2]
    assert result["tool_use_id"] == "toolu_synthetic1"
    assert result["record_sha256"] == sha(raw(records()[-1]))
    assert result["situation"] == "Check the synthetic operation carefully. | I will inspect the synthetic state first."
    assert "ACTION" not in result["situation"]
    assert len(result["situation"]) <= 600


def test_session_identity_missing_refused():
    rows = records()
    for row in rows:
        del row["sessionId"]
    with pytest.raises(pc.CaptureError, match="session"):
        adapt(rows)


def test_reference_session_mismatch_refused():
    rows = records()
    reference = {"session_id": "00000000-0000-4000-8000-000000000011", "uuid": IDS[2], "tool_use_id": "toolu_synthetic1", "record_sha256": sha(raw(rows[-1])), "action_utc": rows[-1]["timestamp"]}
    with pytest.raises(pc.CaptureError, match="session_identity_mismatch"):
        adapt(rows, reference=reference)


@pytest.mark.parametrize("change", ["duplicate", "uncertain", "tie", "before_baseline", "bad_utc", "unknown", "tool_ambiguous", "record_hash", "uuid"])
def test_evidence_strict_refusals(change):
    rows = records()
    kw = {}
    if change == "duplicate":
        rows[1]["uuid"] = IDS[0]
    elif change == "uncertain":
        kw["timestamp_resolution_us"] = None
    elif change == "tie":
        rows[1]["timestamp"] = rows[-1]["timestamp"]
    elif change == "before_baseline":
        kw["baseline_utc"] = "2026-10-20T10:00:05Z"
    elif change == "bad_utc":
        rows[0]["timestamp"] = "2026-10-20T10:00:02"
    elif change == "unknown":
        rows[0]["surprise"] = 1
    elif change == "tool_ambiguous":
        rows[-1]["message"]["content"].append({"type": "tool_use", "id": "toolu_synthetic2", "name": "Read", "input": {}})
        kw["reference"] = {"session_id": SESSION, "uuid": IDS[2], "tool_use_id": None, "record_sha256": sha(raw(rows[-1])), "action_utc": rows[-1]["timestamp"]}
    elif change == "record_hash":
        kw["reference"] = {"session_id": SESSION, "uuid": IDS[2], "tool_use_id": "toolu_synthetic1", "record_sha256": "0" * 64, "action_utc": rows[-1]["timestamp"]}
    else:
        kw["reference"] = {"session_id": SESSION, "uuid": IDS[1], "tool_use_id": "toolu_synthetic1", "record_sha256": sha(raw(rows[-1])), "action_utc": rows[-1]["timestamp"]}
    with pytest.raises(pc.CaptureError):
        adapt(rows, **kw)


def test_malformed_json_and_duplicate_json_keys_never_skipped():
    for prefix in (b"{broken}\n", b'{"type":"user","type":"assistant"}\n'):
        with pytest.raises(pc.CaptureError):
            pc.extract_action_evidence(prefix, reference={}, baseline_utc="2026-10-20T10:00:00Z", collection_start_utc="2026-10-20T10:00:01Z", collection_end_utc="2026-10-20T11:00:00Z", timestamp_resolution_us=1_000_000)


def test_notification_and_long_literal_truncation():
    rows = records()
    rows[0]["message"]["content"] = "x" * 800
    rows[1]["message"]["content"][0]["text"] = "y" * 800
    result = adapt(rows)
    assert len(result["situation"]) <= 600
    assert result["situation"] == "x" * 120 + " .. " + "x" * 120 + " | " + "y" * 250
    rows[0]["message"]["content"] = "<task-notification>synthetic ignored</task-notification>"
    assert "notification" not in adapt(rows)["situation"]


def blind():
    return {"id": "opaque_synthetic_01", "situation": "Inspect the synthetic state carefully.", "snapshot_sha256": "a" * 64}


@pytest.mark.parametrize("field", ["gold", "gold_basename", "nomination", "nonce", "path", "extra"])
def test_blind_schema_rejects_leaking_keys(field):
    row = blind()
    row[field] = "synthetic"
    with pytest.raises(pc.CaptureError):
        pc.project_blind_case(row)


@pytest.mark.parametrize("text", ["../escape", "C:\\synthetic\\lesson.md", "[[synthetic]]", "RECURRED synthetic", "nonce=123", "gold_basename synthetic"])
def test_blind_text_rejects_obvious_leaks_and_paths(text):
    row = blind()
    row["situation"] = text
    with pytest.raises(pc.CaptureError):
        pc.project_blind_case(row)


def test_blind_projection_and_separate_exclusive_tree(synthetic):
    root, _, _, _ = synthetic
    row = blind()
    assert pc.project_blind_case(row) == row
    arm, nominations, scores = root / "arm", root / "nominations", root / "scores"
    receipt = pc.write_blind_package([row], arm, nomination_dir=nominations, score_dir=scores, execute=True)
    data = (arm / "cases.jsonl").read_bytes()
    assert receipt["cases_sha256"] == sha(data)
    assert json.loads(data) == row
    assert not nominations.exists() and not scores.exists()
    with pytest.raises(pc.CaptureError):
        pc.write_blind_package([row], arm, nomination_dir=nominations, score_dir=scores, execute=True)
    with pytest.raises(pc.CaptureError):
        pc.write_blind_package([row], nominations / "arm", nomination_dir=nominations, score_dir=scores, execute=True)


def test_missing_native_and_reference_session_refused():
    rows = records()
    rows[0]["sessionId"] = "unknown"
    with pytest.raises(pc.CaptureError, match="session_identity_invalid"):
        adapt(rows)
    rows = records()
    rows[0]["sessionId"] = IDS[0]
    with pytest.raises(pc.CaptureError, match="session_identity_mismatch"):
        adapt(rows)
    reference = {"uuid": IDS[2], "tool_use_id": "toolu_synthetic1", "record_sha256": sha(raw(rows[-1])), "action_utc": rows[-1]["timestamp"]}
    with pytest.raises(pc.CaptureError):
        adapt(rows, reference=reference)


def test_exact_time_bounds_resolution_and_close():
    with pytest.raises(pc.CaptureError, match="understated"):
        adapt(timestamp_resolution_us=1)
    with pytest.raises(pc.CaptureError, match="strict_window"):
        adapt(baseline_utc="2026-10-20T10:00:04.000000Z")
    with pytest.raises(pc.CaptureError, match="strict_window"):
        adapt(collection_end_utc="2026-10-20T10:00:06.500000Z")
    rows = records()
    rows[0]["timestamp"] = "2026-10-20T10:00:02.000000Z"
    rows[1]["timestamp"] = "2026-10-20T10:00:04.000000Z"
    assert adapt(rows, timestamp_resolution_us=1)["timestamp_resolution_us"] == 1


def test_explicit_multitool_selection_and_duplicate_ids():
    rows = records()
    rows[-1]["message"]["content"].append({"type": "tool_use", "id": "toolu_synthetic2", "name": "Read", "input": {}})
    assert adapt(rows)["tool_use_id"] == "toolu_synthetic1"
    rows[-1]["message"]["content"][-1]["id"] = "toolu_synthetic1"
    with pytest.raises(pc.CaptureError, match="tool_identity_invalid"):
        adapt(rows)


@pytest.mark.parametrize("mode", ["fsync", "readback", "listing"])
def test_write_and_source_instability_are_terminal(synthetic, monkeypatch, mode):
    _, source, out, kw = synthetic
    if mode == "fsync":
        def broken(_):
            raise OSError("synthetic fsync failure")
        monkeypatch.setattr(pc.os, "fsync", broken)
    elif mode == "readback":
        original = Path.read_bytes
        def corrupt(path):
            return b"synthetic bad readback" if path.parent.name == "blobs" else original(path)
        monkeypatch.setattr(Path, "read_bytes", corrupt)
    else:
        original = pc._lessons
        calls = []
        def unstable(path):
            calls.append(1)
            if len(calls) == 2:
                (source / "new.md").write_bytes(b"synthetic unexpected addition")
            return original(path)
        monkeypatch.setattr(pc, "_lessons", unstable)
    with pytest.raises(pc.CaptureError, match="capture_failed") as failure:
        pc.capture_baseline(source, out, execute=True, **kw)
    assert (out / "intent.json").exists()
    assert not (out / "SEALED.json").exists()
    if mode == "fsync":
        assert "terminal_receipt_unavailable" in str(failure.value)
    else:
        assert (out / "STOP_NOT_READY.json").exists()


def test_verify_expected_pin_extra_tree_and_manifest_corruption(synthetic):
    _, source, out, kw = synthetic
    receipt = pc.capture_baseline(source, out, execute=True, **kw)
    with pytest.raises(pc.CaptureError, match="seal_mismatch"):
        pc.verify_baseline(out, expected_manifest_sha256="0" * 64)
    (out / "unexpected").write_bytes(b"synthetic extra")
    with pytest.raises(pc.CaptureError, match="tree_mismatch"):
        pc.verify_baseline(out, expected_manifest_sha256=receipt["manifest_sha256"])
    (out / "manifest.json").write_bytes(b"{synthetic broken}")
    with pytest.raises(pc.CaptureError):
        pc.verify_baseline(out, expected_manifest_sha256=receipt["manifest_sha256"])


def test_non_ntfs_or_uncertain_volume_refused(synthetic, monkeypatch):
    _, source, out, kw = synthetic
    def refuse(_):
        raise pc.CaptureError("ntfs_required")
    monkeypatch.setattr(pc, "_volume_check", refuse)
    with pytest.raises(pc.CaptureError, match="ntfs_required"):
        pc.capture_baseline(source, out, execute=True, **kw)
    assert not out.exists()


def test_hardlinked_source_and_preexisting_empty_output_refused(synthetic):
    root, source, out, kw = synthetic
    os.link(source / "a.md", root / "synthetic-hardlink")
    with pytest.raises(pc.CaptureError, match="lesson_identity_invalid"):
        pc.capture_baseline(source, out, execute=True, **kw)
    assert (out / "intent.json").exists()


def test_blind_injected_id_and_duplicate_case_refused(synthetic):
    root, _, _, _ = synthetic
    row = blind()
    row["id"] = "../synthetic_escape"
    with pytest.raises(pc.CaptureError):
        pc.project_blind_case(row)
    with pytest.raises(pc.CaptureError, match="blind_empty_or_duplicate"):
        pc.write_blind_package([blind(), blind()], root / "arm", nomination_dir=root / "nominations", score_dir=root / "scores", execute=True)


def test_disabled_cli_reads_no_inputs(capsys):
    assert pc.main(["--source-dir", "C:/synthetic", "--destination", "C:/synthetic", "--preregistration", "C:/missing", "--protocol", "C:/missing", "--identity", "C:/missing"]) == 2
    assert "execute_required" in capsys.readouterr().out


@pytest.mark.parametrize("mode", ["session_type", "overflow", "path_type"])
def test_invalid_identity_and_extreme_bounds_use_closed_refusals(synthetic, mode):
    if mode == "path_type":
        _, source, out, kw = synthetic
        kw["identity"]["source_dir"] = 42
        with pytest.raises(pc.CaptureError):
            pc.capture_baseline(source, out, execute=True, **kw)
    elif mode == "session_type":
        rows = records()
        reference = {"session_id": [], "uuid": IDS[2], "tool_use_id": "toolu_synthetic1", "record_sha256": sha(raw(rows[-1])), "action_utc": rows[-1]["timestamp"]}
        with pytest.raises(pc.CaptureError):
            adapt(rows, reference=reference)
    else:
        rows = records()
        rows[-1]["timestamp"] = "9999-12-31T23:59:59.999999Z"
        with pytest.raises(pc.CaptureError):
            adapt(rows, collection_end_utc="9999-12-31T23:59:59.999999Z")
