"""Synthetic only: candidate bundle round-trip and explicitly pinned runner."""
import hashlib
import importlib
import importlib.util
import os
from pathlib import Path

import pytest

from tools import wd_q4_secure_transport as transport, wd_q4_snapshot_candidate as capture

_spec = importlib.util.spec_from_file_location("snapshot_fixture", Path(__file__).with_name("test_wd_q4_snapshot_candidate.py"))
_fixture_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture_module)
_fixture = _fixture_module._fixture

pytestmark = pytest.mark.skipif(os.name != "nt", reason="snapshot checkpoint requires a Windows fixed volume")


def test_capture_decodes_exact_fixed_bundle_bytes(tmp_path):
    descriptors = _fixture(tmp_path)
    captured = capture.capture(root=tmp_path, descriptors=descriptors, enabled=True)
    decoded = transport._fixed_bundle_decode(captured["bundle_bytes"])
    assert captured["bundle_sha256"] == hashlib.sha256(captured["bundle_bytes"]).hexdigest()
    assert decoded["pointer_bytes"] == b"synthetic pointer"
    assert decoded["cell_snapshots"]["learning"]["artifacts"]["receipt"] == b"cr"


def test_runner_is_off_by_default_and_persists_only_preapproved_bundle(tmp_path):
    runner = importlib.import_module("tools.wd_q4_snapshot_bundle_runner")
    assert runner.run() == {"status": "OFF"}
    source, output = tmp_path / "source", tmp_path / "out"
    source.mkdir()
    output.mkdir()
    descriptors = _fixture(source)
    expected = capture.capture(root=source, descriptors=descriptors, enabled=True)
    result = runner.run(enabled=True, root=source, descriptors=descriptors, output_dir=output,
                        expected_bundle_sha256=expected["bundle_sha256"])
    assert result["status"] == "CANDIDATE_UNVERIFIED"
    bundle = output / result["file"]
    assert bundle.read_bytes() == expected["bundle_bytes"]
    assert result["sha256"] == hashlib.sha256(bundle.read_bytes()).hexdigest()
    assert result["bytes"] == len(expected["bundle_bytes"])
    with pytest.raises(runner.BundleRunnerRefused):
        runner.run(enabled=True, root=source, descriptors=descriptors, output_dir=output,
                   expected_bundle_sha256=expected["bundle_sha256"])
    assert bundle.read_bytes() == expected["bundle_bytes"]
    assert list(output.iterdir()) == [bundle]


def test_wrong_bundle_pin_refuses_before_output(tmp_path):
    runner = importlib.import_module("tools.wd_q4_snapshot_bundle_runner")
    source, output = tmp_path / "source", tmp_path / "out"
    source.mkdir()
    output.mkdir()
    with pytest.raises(runner.BundleRunnerRefused):
        runner.run(enabled=True, root=source, descriptors=_fixture(source), output_dir=output,
                   expected_bundle_sha256="0" * 64)
    assert list(output.iterdir()) == []


def test_output_inside_source_refuses_without_writing(tmp_path):
    runner = importlib.import_module("tools.wd_q4_snapshot_bundle_runner")
    source = tmp_path / "source"
    source.mkdir()
    descriptors = _fixture(source)
    output = source / "out"
    output.mkdir()
    expected = capture.capture(root=source, descriptors=descriptors, enabled=True)
    with pytest.raises(runner.BundleRunnerRefused):
        runner.run(enabled=True, root=source, descriptors=descriptors, output_dir=output,
                   expected_bundle_sha256=expected["bundle_sha256"])
    assert list(output.iterdir()) == []


def test_changed_pointer_refuses_and_does_not_disclose_inputs(tmp_path):
    runner = importlib.import_module("tools.wd_q4_snapshot_bundle_runner")
    source, output = tmp_path / "source", tmp_path / "out"
    source.mkdir()
    output.mkdir()
    descriptors = _fixture(source)
    expected = capture.capture(root=source, descriptors=descriptors, enabled=True)
    (source / descriptors["pointer_bytes"]["path"]).write_bytes(b"synthetic-changed-private-value")
    with pytest.raises(runner.BundleRunnerRefused) as caught:
        runner.run(enabled=True, root=source, descriptors=descriptors, output_dir=output,
                   expected_bundle_sha256=expected["bundle_sha256"])
    assert str(caught.value) == "snapshot_bundle_runner_refused"
    assert caught.value.__context__ is None
    assert "synthetic-changed-private-value" not in repr(caught.value)
    assert list(output.iterdir()) == []


@pytest.mark.parametrize("fault", ["partial_write", "fsync", "before_rename", "after_rename", "concurrent_final"])
def test_output_fault_reports_unknown_without_deleting_or_retrying(tmp_path, monkeypatch, fault):
    runner = importlib.import_module("tools.wd_q4_snapshot_bundle_runner")
    source, output = tmp_path / "source", tmp_path / "out"
    source.mkdir()
    output.mkdir()
    descriptors = _fixture(source)
    expected = capture.capture(root=source, descriptors=descriptors, enabled=True)
    original_rename = os.rename
    original_fdopen = os.fdopen
    rename_calls = []

    if fault == "partial_write":
        class PartialStream:
            def __init__(self, inner):
                self.inner = inner

            def __enter__(self):
                self.inner.__enter__()
                return self

            def __exit__(self, *args):
                return self.inner.__exit__(*args)

            def write(self, data):
                self.inner.write(data[:3])
                raise OSError("synthetic partial write")

        monkeypatch.setattr(runner.os, "fdopen", lambda fd, mode: PartialStream(original_fdopen(fd, mode)))
    elif fault == "fsync":
        def fail_fsync(fd):
            raise OSError("synthetic fsync failure")
        monkeypatch.setattr(runner.os, "fsync", fail_fsync)
    else:
        def failing_rename(src, dst):
            rename_calls.append((src, dst))
            if fault == "after_rename":
                original_rename(src, dst)
            elif fault == "concurrent_final":
                Path(dst).write_bytes(b"synthetic concurrent owner")
                return original_rename(src, dst)
            raise OSError("synthetic rename failure")
        monkeypatch.setattr(runner.os, "rename", failing_rename)

    with pytest.raises(runner.BundleRunnerOutputStateUnknown) as caught:
        runner.run(enabled=True, root=source, descriptors=descriptors, output_dir=output,
                   expected_bundle_sha256=expected["bundle_sha256"])
    assert str(caught.value) == "snapshot_bundle_output_state_unknown"
    files = list(output.iterdir())
    assert len(files) == (2 if fault == "concurrent_final" else 1)
    final = output / ("q4-snapshot-" + expected["bundle_sha256"] + ".bundle")
    if fault == "after_rename":
        assert final.read_bytes() == expected["bundle_bytes"]
    elif fault == "concurrent_final":
        assert final.read_bytes() == b"synthetic concurrent owner"
        assert len(rename_calls) == 1
    else:
        assert not final.exists()
        assert ".partial-" in files[0].name
        if fault == "partial_write":
            assert files[0].read_bytes() == expected["bundle_bytes"][:3]
    assert len(rename_calls) <= 1


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("after_move", [False, True])
def test_output_interrupt_is_unknown_and_sanitized(tmp_path, monkeypatch, interrupt, after_move):
    runner = importlib.import_module("tools.wd_q4_snapshot_bundle_runner")
    source, output = tmp_path / "source", tmp_path / "out"
    source.mkdir()
    output.mkdir()
    descriptors = _fixture(source)
    expected = capture.capture(root=source, descriptors=descriptors, enabled=True)
    original_rename = os.rename

    def interrupted_rename(src, dst):
        if after_move:
            original_rename(src, dst)
        raise interrupt("synthetic secret")

    monkeypatch.setattr(runner.os, "rename", interrupted_rename)
    with pytest.raises(runner.BundleRunnerOutputStateUnknown) as caught:
        runner.run(enabled=True, root=source, descriptors=descriptors, output_dir=output,
                   expected_bundle_sha256=expected["bundle_sha256"])
    assert str(caught.value) == "snapshot_bundle_output_state_unknown"
    assert caught.value.__context__ is None
    assert "synthetic secret" not in repr(caught.value)
    files = list(output.iterdir())
    assert len(files) == 1
    assert (".partial-" not in files[0].name) is after_move


def test_failed_fdopen_closes_only_the_new_fd_and_reports_unknown(tmp_path, monkeypatch):
    runner = importlib.import_module("tools.wd_q4_snapshot_bundle_runner")
    source, output = tmp_path / "source", tmp_path / "out"
    source.mkdir()
    output.mkdir()
    descriptors = _fixture(source)
    expected = capture.capture(root=source, descriptors=descriptors, enabled=True)
    observed = []

    def fail_fdopen(fd, mode):
        observed.append(fd)
        raise OSError("synthetic fdopen failure")

    monkeypatch.setattr(runner.os, "fdopen", fail_fdopen)
    with pytest.raises(runner.BundleRunnerOutputStateUnknown):
        runner.run(enabled=True, root=source, descriptors=descriptors, output_dir=output,
                   expected_bundle_sha256=expected["bundle_sha256"])
    assert len(observed) == 1
    with pytest.raises(OSError):
        os.fstat(observed[0])
    files = list(output.iterdir())
    assert len(files) == 1 and ".partial-" in files[0].name
