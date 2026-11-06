"""Synthetic tuple validation: no files are read by the builder."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import wd_q4_session_outer_entry as entry


@pytest.fixture
def inputs(tmp_path):
    base = str(tmp_path)
    worker = {name: base for name in entry._WORKER_KEYS}
    for name in worker:
        if name.endswith("sha256"):
            worker[name] = "a" * 64
    worker.update({"lane": "codex-lead-1", "interpreter-max-bytes": "1024",
                   "timeout-seconds": "30", "max-output-bytes": "4096"})
    outer = {name: "a" * 64 for name in entry._OUTER_KEYS}
    outer.update({"runtime-root": base, "runtime-generation": "b" * 40, "runtime-revision": "c" * 40,
                  "outer-path": str(Path(base) / "assets/code/tools/wd_q4_session_outer_synthetic.py")})
    expected = {name: "d" * 64 for name in entry._BINDING_KEYS}
    return worker, outer, expected


def builder():
    from tools.wd_q4_session_tuple import build_session_argv
    return build_session_argv


def test_off_by_default(inputs):
    with pytest.raises(ValueError, match="^session_tuple_refused$"):
        builder()(*inputs)


def test_exact_tuple_is_accepted_by_entry_parser_without_io(inputs, monkeypatch):
    worker, outer, expected = inputs
    argv = builder()(worker, outer, expected, enabled=True)
    monkeypatch.setattr(entry, "sys", SimpleNamespace(flags=SimpleNamespace(
        isolated=1, no_site=1, dont_write_bytecode=1)))
    result = entry._parse(argv)
    assert set(result) == set(entry._OPTIONS)
    assert all(result[name] == value for name, value in worker.items())
    assert all(result[name] == value for name, value in outer.items())
    assert all(result["expected-" + name.replace("_", "-")] == value for name, value in expected.items())
    assert "--query" not in argv


@pytest.mark.parametrize("group", [0, 1, 2])
@pytest.mark.parametrize("kind", ["missing", "extra", "query"])
def test_field_sets_are_exact(inputs, group, kind):
    values = [dict(item) for item in inputs]
    if kind == "missing":
        values[group].pop(next(iter(values[group])))
    else:
        values[group]["query" if kind == "query" else "extra"] = "synthetic-private"
    with pytest.raises(ValueError, match="^session_tuple_refused$"):
        builder()(*values, enabled=True)


@pytest.mark.parametrize("group,key,value", [
    (0, "lane", "unknown-lane"), (0, "binding-sha256", "A" * 64),
    (0, "timeout-seconds", "1801"), (0, "timeout-seconds", "0"),
    (0, "interpreter-max-bytes", "134217729"), (0, "max-output-bytes", "65537"),
    (0, "cwd", "relative"), (0, "fleet", ""), (0, "binding", "C:\\a\\..\\b"),
    (1, "outer-path", "C:\\wrong\\file.py"), (1, "runtime-generation", "a" * 39),
    (1, "outer-sha256", "b" * 63), (2, "pointer_sha256", "private-value"),
    (0, "lane", None),
])
def test_invalid_fields_refuse(inputs, group, key, value):
    values = [dict(item) for item in inputs]
    values[group][key] = value
    with pytest.raises(ValueError, match="^session_tuple_refused$"):
        builder()(*values, enabled=True)


def test_exact_true_enable_and_independent_result(inputs):
    with pytest.raises(ValueError, match="^session_tuple_refused$"):
        builder()(*inputs, enabled=1)
    argv = builder()(*inputs, enabled=True)
    inputs[0]["lane"] = "other"
    assert argv[argv.index("--lane") + 1] == "codex-lead-1"


def test_same_path_cannot_have_two_different_hashes(inputs):
    worker, outer, expected = inputs
    worker["fleet"] = worker["snapshot"]
    worker["fleet-sha256"] = "f" * 64
    worker["snapshot-sha256"] = "e" * 64
    with pytest.raises(ValueError, match="^session_tuple_refused$"):
        builder()(worker, outer, expected, enabled=True)


def test_builder_does_not_open_or_stat_files(inputs, monkeypatch):
    import builtins
    import os
    build = builder()
    def forbidden(*args, **kwargs):
        raise AssertionError("unexpected filesystem access")
    with monkeypatch.context() as blocked:
        blocked.setattr(builtins, "open", forbidden)
        blocked.setattr(os, "stat", forbidden)
        blocked.setattr(os, "lstat", forbidden)
        argv = build(*inputs, enabled=True)
    assert argv[0] == "--enabled"


@pytest.mark.parametrize("fault", ["query", "bad_hash", "bad_path", "bad_limit", "collision", "disabled"])
def test_refusal_does_not_keep_inputs_in_its_frame(inputs, fault):
    worker, outer, expected = (dict(value) for value in inputs)
    marker = "synthetic-private-marker"
    enabled = True
    if fault == "query":
        worker["query"] = marker
    elif fault == "bad_hash":
        expected["pointer_sha256"] = marker
    elif fault == "bad_path":
        worker["cwd"] = marker
    elif fault == "bad_limit":
        worker["timeout-seconds"] = marker
    elif fault == "collision":
        worker["snapshot-sha256"] = "b" * 64
    else:
        enabled = False
    with pytest.raises(ValueError, match="^session_tuple_refused$") as caught:
        builder()(worker, outer, expected, enabled=enabled)
    assert caught.value.__context__ is None
    trace = caught.value.__traceback__
    frames = []
    while trace is not None:
        if trace.tb_frame.f_code.co_name == "build_session_argv":
            frames.append(trace.tb_frame)
        trace = trace.tb_next
    assert len(frames) == 1
    local = frames[0].f_locals
    assert marker not in repr(local)
    assert all(value is not worker and value is not outer and value is not expected for value in local.values())
