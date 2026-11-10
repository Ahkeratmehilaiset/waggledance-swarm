"""Synthetic tests for the default-off CPU site overlay; never copies packages."""
import hashlib
import importlib
import os
from pathlib import Path
import shutil

import pytest


def _inputs(tmp_path):
    owned = tmp_path / ".codex-audit"
    owned.mkdir()
    base, cpu = owned / "base", owned / "cpu"
    base.mkdir()
    cpu.mkdir()
    (base / "a.py").write_bytes(b"base")
    (cpu / "b.py").write_bytes(b"cpu")
    roots = {"base": str(base), "cpu": str(cpu)}
    pins = {name: {"device": os.stat(root).st_dev, "inode": os.stat(root).st_ino}
            for name, root in roots.items()}
    mapping = [
        {"root": "base", "source": "a.py", "target": "pkg/a.py", "size": 4,
         "sha256": hashlib.sha256(b"base").hexdigest()},
        {"root": "cpu", "source": "b.py", "target": "pkg/b.py", "size": 3,
         "sha256": hashlib.sha256(b"cpu").hexdigest()},
    ]
    return roots, pins, mapping, owned / "overlay"


def test_off_no_io(monkeypatch):
    overlay = importlib.import_module("tools.wd_q4_cpu_site_overlay")
    monkeypatch.setattr(overlay.manifest, "_check_chain", lambda *_: pytest.fail("unexpected I/O"))
    assert overlay.stage() == {"status": "OFF"}


def test_two_pinned_roots_copy_exact_bytes_deterministically(tmp_path):
    overlay = importlib.import_module("tools.wd_q4_cpu_site_overlay")
    roots, pins, mapping, target = _inputs(tmp_path)
    result = overlay.stage(enabled=True, roots=roots, root_pins=pins, mapping=list(reversed(mapping)),
                           output=str(target), max_bytes=7)
    assert result == {"status": "STAGED_UNVERIFIED", "files": 2, "bytes": 7}
    assert (target / "pkg/a.py").read_bytes() == b"base"
    assert (target / "pkg/b.py").read_bytes() == b"cpu"
    with pytest.raises(ValueError):
        overlay.stage(enabled=True, roots=roots, root_pins=pins, mapping=mapping, output=str(target), max_bytes=7)
    assert (target / "pkg/a.py").read_bytes() == b"base"


@pytest.mark.parametrize("fault", ["collision", "case_collision", "traversal", "absolute", "bad_digest",
                                   "bad_size", "wrong_root_pin", "budget", "root_overlap"])
def test_bad_inputs_refuse_before_output(tmp_path, fault):
    overlay = importlib.import_module("tools.wd_q4_cpu_site_overlay")
    roots, pins, mapping, target = _inputs(tmp_path)
    if fault == "collision":
        mapping[1]["target"] = mapping[0]["target"]
    elif fault == "case_collision":
        mapping[1]["target"] = "pkg/A.py"
    elif fault == "traversal":
        mapping[1]["source"] = "../a.py"
    elif fault == "absolute":
        mapping[1]["target"] = "C:/escape"
    elif fault == "bad_digest":
        mapping[0]["sha256"] = "0" * 64
    elif fault == "bad_size":
        mapping[0]["size"] = 5
    elif fault == "wrong_root_pin":
        pins["base"]["inode"] += 1
    elif fault == "root_overlap":
        roots["cpu"] = roots["base"]
        pins["cpu"] = pins["base"]
    with pytest.raises(ValueError):
        overlay.stage(enabled=True, roots=roots, root_pins=pins, mapping=mapping,
                      output=str(target), max_bytes=6 if fault == "budget" else 100)
    assert not target.exists()


def test_low_disk_refuses_before_output(tmp_path, monkeypatch):
    overlay = importlib.import_module("tools.wd_q4_cpu_site_overlay")
    roots, pins, mapping, target = _inputs(tmp_path)
    usage = shutil._ntuple_diskusage(10**12, 0, 3 * 1024**3)
    monkeypatch.setattr(overlay.shutil, "disk_usage", lambda _: usage)
    with pytest.raises(ValueError):
        overlay.stage(enabled=True, roots=roots, root_pins=pins, mapping=mapping, output=str(target), max_bytes=100)
    assert not target.exists()


def test_source_hardlink_refuses_before_output(tmp_path):
    overlay = importlib.import_module("tools.wd_q4_cpu_site_overlay")
    roots, pins, mapping, target = _inputs(tmp_path)
    os.link(str(Path(roots["base"]) / "a.py"), str(Path(roots["base"]) / "alias.py"))
    with pytest.raises(ValueError):
        overlay.stage(enabled=True, roots=roots, root_pins=pins, mapping=mapping, output=str(target), max_bytes=100)
    assert not target.exists()


def test_reparse_input_refuses_before_output(tmp_path, monkeypatch):
    overlay = importlib.import_module("tools.wd_q4_cpu_site_overlay")
    roots, pins, mapping, target = _inputs(tmp_path)
    original = overlay.manifest._is_reparse
    source_stat = os.lstat(Path(roots["base"]) / "a.py")
    monkeypatch.setattr(overlay.manifest, "_is_reparse",
                        lambda info: info.st_ino == source_stat.st_ino or original(info))
    with pytest.raises(ValueError):
        overlay.stage(enabled=True, roots=roots, root_pins=pins, mapping=mapping, output=str(target), max_bytes=100)
    assert not target.exists()


def test_oversize_source_refuses_without_unbounded_hash_read(tmp_path, monkeypatch):
    overlay = importlib.import_module("tools.wd_q4_cpu_site_overlay")
    roots, pins, mapping, target = _inputs(tmp_path)
    source = Path(roots["base"]) / "a.py"
    source.write_bytes(b"a" * 65536)
    mapping[0]["size"] = 1
    mapping[0]["sha256"] = hashlib.sha256(b"a").hexdigest()
    original = overlay.runtime_builder._hash_file
    original_open = overlay.manifest._open_regular
    observed_limits = []

    def reject_unbounded_hash(path):
        if Path(path) == source:
            pytest.fail("oversize source was sent to unbounded preflight hashing")
        return original(path)

    monkeypatch.setattr(overlay.runtime_builder, "_hash_file", reject_unbounded_hash)

    def bounded_open(path, limit):
        if Path(path) == source:
            observed_limits.append(limit)
            assert limit == mapping[0]["size"]
        return original_open(path, limit)

    monkeypatch.setattr(overlay.manifest, "_open_regular", bounded_open)
    with pytest.raises(ValueError):
        overlay.stage(enabled=True, roots=roots, root_pins=pins, mapping=mapping, output=str(target), max_bytes=100)
    assert observed_limits == [1]
    assert not target.exists()


def test_nested_output_refuses_before_any_write(tmp_path):
    overlay = importlib.import_module("tools.wd_q4_cpu_site_overlay")
    roots, pins, mapping, _ = _inputs(tmp_path)
    target = Path(roots["base"]) / "nested-output"
    with pytest.raises(overlay.OverlayRefused) as caught:
        overlay.stage(enabled=True, roots=roots, root_pins=pins, mapping=mapping, output=str(target), max_bytes=100)
    assert str(caught.value) == "site_overlay_refused" and caught.value.__context__ is None
    assert not target.exists()


def test_parent_identity_still_refuses_if_lexical_check_misses(tmp_path, monkeypatch):
    overlay = importlib.import_module("tools.wd_q4_cpu_site_overlay")
    roots, pins, mapping, _ = _inputs(tmp_path)
    target = Path(roots["base"]) / "nested-output"
    monkeypatch.setattr(overlay.planner, "_related", lambda *_: False)
    with pytest.raises(overlay.OverlayRefused):
        overlay.stage(enabled=True, roots=roots, root_pins=pins, mapping=mapping, output=str(target), max_bytes=100)
    assert not target.exists()


def test_preflight_oserror_is_a_sanitized_refusal(tmp_path, monkeypatch):
    overlay = importlib.import_module("tools.wd_q4_cpu_site_overlay")
    roots, pins, mapping, target = _inputs(tmp_path)
    def fail(*args):
        raise OSError("synthetic-secret-path")
    monkeypatch.setattr(overlay, "_hash_exact", fail)
    with pytest.raises(overlay.OverlayRefused) as caught:
        overlay.stage(enabled=True, roots=roots, root_pins=pins, mapping=mapping, output=str(target), max_bytes=100)
    assert str(caught.value) == "site_overlay_refused" and caught.value.__context__ is None
    assert not target.exists()


@pytest.mark.parametrize("failure", [KeyboardInterrupt, SystemExit])
def test_preflight_interrupt_is_sanitized_and_scrubs_own_frames(tmp_path, monkeypatch, failure):
    overlay = importlib.import_module("tools.wd_q4_cpu_site_overlay")
    roots, pins, mapping, target = _inputs(tmp_path)
    def fail(*args):
        raise failure("synthetic-private-preflight")
    monkeypatch.setattr(overlay, "_hash_exact", fail)
    try:
        overlay.stage(enabled=True, roots=roots, root_pins=pins, mapping=mapping,
                      output=str(target), max_bytes=100)
    except BaseException as exc:
        assert type(exc) is failure
        assert str(exc) == "site_overlay_interrupted"
        assert exc.__context__ is None and exc.__cause__ is None
        tb = exc.__traceback__
        names = []
        while tb:
            frame = tb.tb_frame
            if Path(frame.f_code.co_filename).name == "wd_q4_cpu_site_overlay.py":
                names.append(frame.f_code.co_name)
                for name in ("enabled", "roots", "root_pins", "mapping", "output", "max_bytes"):
                    assert frame.f_locals.get(name) is None
            tb = tb.tb_next
        assert names == ["stage"]
    else:
        pytest.fail("preflight did not refuse")
    assert not target.exists()


@pytest.mark.parametrize("after_mkdir", [False, True])
@pytest.mark.parametrize("failure", [OSError, KeyboardInterrupt])
def test_mkdir_attempt_failure_is_unknown_without_cleanup(tmp_path, monkeypatch, after_mkdir, failure):
    overlay = importlib.import_module("tools.wd_q4_cpu_site_overlay")
    roots, pins, mapping, target = _inputs(tmp_path)
    original = os.mkdir
    calls = []
    def fail(path, *args, **kwargs):
        if Path(path) == target:
            calls.append(path)
            if after_mkdir:
                original(path, *args, **kwargs)
            raise failure("synthetic-private-detail")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(overlay.os, "mkdir", fail)
    with pytest.raises(overlay.OverlayOutputStateUnknown) as caught:
        overlay.stage(enabled=True, roots=roots, root_pins=pins, mapping=mapping, output=str(target), max_bytes=100)
    assert str(caught.value) == "site_overlay_output_state_unknown" and caught.value.__context__ is None
    assert len(calls) == 1
    assert target.exists() is after_mkdir


def test_mid_copy_disk_floor_is_unknown_and_keeps_partial(tmp_path, monkeypatch):
    overlay = importlib.import_module("tools.wd_q4_cpu_site_overlay")
    roots, pins, mapping, target = _inputs(tmp_path)
    calls = []
    def fake_usage(path):
        calls.append(path)
        free = overlay._FLOOR + 100 if len(calls) == 1 else overlay._FLOOR - 1
        return shutil._ntuple_diskusage(10**12, 0, free)
    monkeypatch.setattr(overlay.shutil, "disk_usage", fake_usage)
    with pytest.raises(overlay.OverlayOutputStateUnknown) as caught:
        overlay.stage(enabled=True, roots=roots, root_pins=pins, mapping=mapping, output=str(target), max_bytes=100)
    assert str(caught.value) == "site_overlay_output_state_unknown" and caught.value.__context__ is None
    assert len(calls) == 2
    assert (target / "pkg" / "a.py").exists()
    assert (target / "pkg" / "a.py").read_bytes() == b""

@pytest.mark.parametrize("kind", ["base", "cpu", "output"])
def test_fixture_owns_audit_child_for_all_synthetic_paths(tmp_path, kind):
    # A surrounding D15 audit ancestor must not mask an invalid CI fixture.
    roots, _, _, target = _inputs(tmp_path)
    selected = target if kind == "output" else Path(roots[kind])
    assert ".codex-audit" in tuple(part.casefold() for part in selected.relative_to(tmp_path).parts)


def test_output_outside_audit_is_refused_without_mkdir(tmp_path, monkeypatch):
    import uuid
    overlay = importlib.import_module("tools.wd_q4_cpu_site_overlay")
    roots, pins, mapping, _ = _inputs(tmp_path)
    # Only read the canonical worktree parent; never create output outside audit.
    target = Path.cwd() / ("cpu-overlay-rejected-" + uuid.uuid4().hex)
    assert ".codex-audit" not in tuple(part.casefold() for part in target.parts)
    assert not target.exists()
    original = os.mkdir
    attempts = []
    def record(path, *args, **kwargs):
        if Path(path) == target:
            attempts.append(path)
            pytest.fail("unapproved output creation attempted")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(overlay.os, "mkdir", record)
    with pytest.raises(overlay.OverlayRefused, match="^site_overlay_refused$"):
        overlay.stage(enabled=True, roots=roots, root_pins=pins, mapping=mapping,
                      output=str(target), max_bytes=100)
    assert attempts == []
    assert not target.exists()
