"""Offline producer regressions using the real compact reader on synthetic fixtures.

Load definitions only: never start the producer loop, PowerShell, GitHub or a live
bridge read. All fixture logs and capture outputs live under .codex-audit/.
"""

import ast
import base64
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools import bridge_compact_view as compact  # noqa: E402


@pytest.fixture
def audit():
    directory = ROOT / ".codex-audit"
    directory.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="capture-provenance-test-", dir=directory) as name:
        yield Path(name)


@pytest.fixture
def producer():
    path = Path(__file__).with_name("mind_capture_producer.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nodes = [node for node in tree.body
             if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.ClassDef))
             or (isinstance(node, ast.Assign) and len(node.targets) == 1
                 and isinstance(node.targets[0], ast.Name) and node.targets[0].id.isupper())]
    module = ModuleType("offline_mind_capture_producer")
    module.src = ROOT
    module.args = SimpleNamespace(window=2, workers=1, seed_from=None)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), module.__dict__)
    return module


class FixtureReader:
    """The pinned script's Compact argument mapping, with synthetic input only."""

    def __init__(self, producer, directory, generation="g1"):
        self.producer = producer
        self.path = directory / "fixture.jsonl"
        self.path.write_bytes(b"")
        self.calls = []
        self.views = []
        self.generation(generation)

    def generation(self, value):
        path = self.path.with_name("events.generation.json")
        if value is not None:
            path.write_text(json.dumps({"generation": value}), encoding="ascii")

    def append(self, number):
        event = {"type": "message", "ts_utc": "2026-10-14T18:00:00Z", "message": f"fixture-{number}"}
        with self.path.open("ab") as handle:
            handle.write((json.dumps(event) + "\n").encode("utf-8"))
        return event

    def __call__(self, *options):
        self.calls.append(options)
        argv = ["--events", str(self.path)]
        for option, value in zip(options[::2], options[1::2]):
            argv.extend([{"-ContinuityTail": "--tail", "-AfterEvent": "--after", "-EventId": "--event-id"}[option],
                         value])
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            status = compact.main(argv)
        if status != 0:
            raise self.producer.CaptureRefused("compact_reader_failed")
        data = output.getvalue().encode("utf-8")
        view = json.loads(data)
        if view["schema"] == "wd.bridge.compact-view.v1":
            self.views.append(view)
        return data


def setup_reader(producer, audit, monkeypatch, generation="g1"):
    source = FixtureReader(producer, audit, generation)
    monkeypatch.setattr(producer, "reader", source)
    state = audit / "state"
    state.mkdir()
    return source, state


def save(producer, audit, name, rows, metadata):
    directory = audit / name
    directory.mkdir()
    path = producer.write_capture(directory, rows, metadata)
    sidecar = directory / "capture-provenance.json"
    return path, sidecar, json.loads(sidecar.read_bytes())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_regression_old_producer_loses_cursor_after_mutable_state_advances(producer, audit, monkeypatch):
    source, state = setup_reader(producer, audit, monkeypatch)
    source.append(1)
    source.append(2)
    source.append(3)
    rows, metadata = producer.advance(state)
    token = source.views[-1]["cursor"]
    # This is the named regression: the old advance() discards this cursor.
    assert metadata["provenance"]["cursor_end"] == token
    path, sidecar, saved = save(producer, audit, "seed", rows, metadata)
    before = sidecar.read_bytes()
    provenance = saved["read"]["provenance"]
    assert saved["read"]["mode"] == "seed"
    assert provenance["cursor_prior"] is None
    assert provenance["cursor_start"] is None
    assert provenance["reader_scope"] == "bounded_snapshot_not_complete_history"
    assert provenance["reader_stats"] == source.views[0]["reader"]
    decoded = json.loads(base64.urlsafe_b64decode(token[4:] + "=" * (-len(token[4:]) % 4)))
    assert provenance["position_end"] == decoded
    assert provenance["generation_status"] == "KNOWN"
    assert provenance["file_identity_status"] == "KNOWN"
    assert provenance["coverage"] == "PARTIAL"
    assert provenance["window_scope"] == "sliding_window_not_complete_history"
    assert saved["capture_sha256"] == digest(path)
    assert saved["detail_manifest_sha256"] == digest(path.with_name("detail-manifest.txt"))
    producer.shadow.read_capture(json.loads(path.read_bytes()))  # strict existing consumer
    source.append(4)
    rows, delta = producer.advance(state)
    _, _, next_saved = save(producer, audit, "delta", rows, delta)
    assert delta["mode"] == "delta"
    assert delta["provenance"]["cursor_prior"] == token
    assert delta["provenance"]["cursor_start"] == token
    assert delta["provenance"]["reader_scope"] == "position_delta_batch_not_complete_history"
    assert next_saved["read"]["provenance"]["cursor_end"] == (state / "cursor.txt").read_text(encoding="ascii")
    assert next_saved["read"]["provenance"]["cursor_end"] != token
    assert sidecar.read_bytes() == before
    assert json.loads(sidecar.read_bytes())["read"]["provenance"]["cursor_end"] == token


@pytest.mark.parametrize("change,reason", [("generation", "generation_changed"), ("replace", "file_identity_changed")])
def test_reseed_retains_rejected_prior_cursor_and_new_identity(producer, audit, monkeypatch, change, reason):
    source, state = setup_reader(producer, audit, monkeypatch)
    source.append(1)
    rows, initial = producer.advance(state)
    _, _, first = save(producer, audit, "first", rows, initial)
    old = (state / "cursor.txt").read_bytes()
    if change == "generation":
        source.generation("g2")
    else:
        source.path.rename(audit / "replaced-fixture.jsonl")
        source.path.write_bytes(b"")
    source.append(2)
    rows, metadata = producer.advance(state)
    _, _, saved = save(producer, audit, "reseed", rows, metadata)
    value = saved["read"]["provenance"]
    assert metadata["mode"] == "reseed"
    assert value["reseed_reason"] == "compact_reader_refused"
    assert value["cursor_prior"].encode("ascii") == old
    assert value["cursor_start"] is None  # a refused cursor did not start the accepted tail
    assert value["identity_change"] == reason
    assert value["coverage"] == "PARTIAL"
    assert first["read"]["provenance"]["cursor_end"].encode("ascii") == old


def test_null_generation_remains_unknown_and_opaque_cursor_is_not_normalized(producer, audit, monkeypatch):
    source, state = setup_reader(producer, audit, monkeypatch, generation=None)
    source.append(1)
    rows, metadata = producer.advance(state)
    _, _, saved = save(producer, audit, "unknown-generation", rows, metadata)
    value = saved["read"]["provenance"]
    assert value["position_end"]["generation"] is None
    assert value["generation_status"] == "UNKNOWN"
    assert value["file_identity_status"] == "KNOWN"
    assert value["coverage"] == "PARTIAL"
    original = (state / "cursor.txt").read_bytes() + b"\n"
    (state / "cursor.txt").write_bytes(original)
    _, reseeded = producer.advance(state)
    assert reseeded["mode"] == "reseed"
    assert reseeded["provenance"]["cursor_prior"].encode("ascii") == original
    assert reseeded["provenance"]["position_prior"] is None
    assert reseeded["provenance"]["reseed_reason"] == "cursor_invalid"
    # The pinned shell command takes only the bc1 alphabet: keep invalid bytes
    # as evidence, without interpolating them into a PowerShell command.
    assert ("-AfterEvent", original.decode("ascii")) not in source.calls


@pytest.mark.parametrize("token", ["", "future-reader-token", "bc1.e30"])
def test_unavailable_identity_stays_unknown_without_fabrication(producer, audit, monkeypatch, token):
    source, state = setup_reader(producer, audit, monkeypatch)
    source.append(1)

    def unknown_cursor(*options):
        view = json.loads(source(*options))
        if view["schema"] == "wd.bridge.compact-view.v1":
            view["cursor"] = token
        return json.dumps(view).encode("utf-8")

    monkeypatch.setattr(producer, "reader", unknown_cursor)
    rows, metadata = producer.advance(state)
    _, _, saved = save(producer, audit, "unknown-identity", rows, metadata)
    value = saved["read"]["provenance"]
    assert value["cursor_end"] == token
    assert value["position_end"] is None
    assert value["file_identity_status"] == "UNKNOWN"
    assert value["generation_status"] == "UNKNOWN"
    assert value["coverage"] == "PARTIAL"


def test_empty_valid_window_continues_by_delta_instead_of_reseeding(producer, audit, monkeypatch):
    source, state = setup_reader(producer, audit, monkeypatch)
    producer.advance(state)
    _, metadata = producer.advance(state)
    assert metadata["mode"] == "delta"
    assert len(source.views) == 2
    assert source.views[-1]["scope"] == "position_delta_batch_not_complete_history"


def test_sidecar_hashes_prevent_cross_capture_mix_and_expose_window_loss(producer, audit, monkeypatch):
    source, state = setup_reader(producer, audit, monkeypatch)
    source.append(1)
    rows, metadata = producer.advance(state)
    first, _, first_provenance = save(producer, audit, "first", rows, metadata)
    for number in range(2, 6):
        source.append(number)
    rows, metadata = producer.advance(state)
    second, _, second_provenance = save(producer, audit, "second", rows, metadata)
    assert first_provenance["capture_sha256"] == digest(first)
    assert first_provenance["capture_sha256"] != digest(second)
    assert first_provenance["detail_manifest_sha256"] != digest(second.with_name("detail-manifest.txt"))
    value = second_provenance["read"]["provenance"]
    assert value["dropped_rows"] == 3
    assert value["retained_rows"] == 2
    assert len(value["listed_refs"]) == 4
    assert value["coverage"] == "PARTIAL"
    assert value["manifest_digest_scope"] == "retained_ref_row_digest_source_manifest"


def test_saved_provenance_cannot_be_overwritten(producer, audit, monkeypatch):
    source, state = setup_reader(producer, audit, monkeypatch)
    source.append(1)
    rows, metadata = producer.advance(state)
    capture, sidecar, _ = save(producer, audit, "saved", rows, metadata)
    files = (capture, sidecar, capture.with_name("detail-manifest.txt"))
    before = [path.read_bytes() for path in files]
    with pytest.raises(producer.CaptureRefused, match="^capture_provenance_exists$"):
        producer.write_capture(capture.parent, [], metadata)
    assert [path.read_bytes() for path in files] == before


@pytest.mark.parametrize("missing", ["window.jsonl", "cursor.txt"])
def test_incomplete_state_reseeds_with_honest_start(producer, audit, monkeypatch, missing):
    source, state = setup_reader(producer, audit, monkeypatch)
    source.append(1)
    _, initial = producer.advance(state)
    token = initial["provenance"]["cursor_end"]
    (state / missing).unlink()
    source.append(2)
    _, metadata = producer.advance(state)
    assert metadata["mode"] == "reseed"
    value = metadata["provenance"]
    assert value["reseed_reason"] == "state_incomplete"
    assert value["cursor_prior"] == (None if missing == "cursor.txt" else token)
    assert value["cursor_start"] is None
    assert value["coverage"] == "PARTIAL"
