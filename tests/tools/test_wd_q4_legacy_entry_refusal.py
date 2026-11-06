"""Packaged legacy entry points refuse before consuming data or starting work."""

import subprocess
import sys
from pathlib import Path

import pytest

from tools import wd_q4_lane_session_cli as lane_cli
from tools import wd_q4_session_outer_entry as outer_entry
from tools import wd_q4_session_outer_synthetic as outer


def test_legacy_session_cli_enabled_refuses_before_parsing_or_io(monkeypatch):
    calls = []
    monkeypatch.setattr(lane_cli, "_parse", lambda *a: calls.append("parse") or {})
    assert lane_cli.main(["--enabled"]) == 2
    assert calls == []


def test_legacy_outer_entry_enabled_refuses_before_parsing_or_io(monkeypatch):
    calls = []
    monkeypatch.setattr(outer_entry, "_parse", lambda *a: calls.append("parse") or {})
    assert outer_entry.main(["--enabled"]) == 2
    assert calls == []


def test_legacy_outer_enabled_refuses_before_pinned_reads(tmp_path):
    source = str(Path(outer.__file__).resolve())
    script = r'''
import importlib.util, pathlib, sys
spec = importlib.util.spec_from_file_location("outer_test", sys.argv[1])
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
called = []
def spy(*args):
    called.append('read')
    raise ValueError()
module._pinned = spy
try:
    module.run_session(enabled=True, runtime_root=sys.argv[2], argv=['enabled'], query=b'synthetic')
except module.SessionOuterRefused:
    pass
else:
    raise AssertionError('enabled legacy outer returned')
assert called == [], called
'''
    result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", script, source, str(tmp_path)],
                            capture_output=True, text=True, timeout=15, check=False)
    assert result.returncode == 0, result.stderr


def test_outer_default_off_and_validator_remain_available():
    assert outer.run_session() == {"status": "OFF"}
    assert callable(outer._validate_lane_answer)


def test_packaged_answer_validator_accepts_matching_and_rejects_wrong_lane():
    binding = {name: "a" * 64 for name in outer._BINDING_KEYS}
    answer = {"schema": "wd.q4-lane-answer.v1", "lane": "codex-lead-1", "status": "ABSTAIN",
              "reason": "", "result_count": 0, "results": [], "binding": binding, "trust": dict(outer._TRUST)}
    assert outer._validate_lane_answer(answer, "codex-lead-1", binding) == answer
    with pytest.raises(ValueError):
        outer._validate_lane_answer(answer, "fable-5", binding)


@pytest.mark.parametrize("module", ["wd_q4_lane_session_cli", "wd_q4_session_outer_entry"])
def test_packaged_cli_exits_with_stdin_still_open(module):
    root = str(Path(__file__).resolve().parents[2])
    code = ("import importlib,sys;sys.path.insert(0,sys.argv[1]);"
            "m=importlib.import_module('tools.'+sys.argv[2]);sys.exit(m.main(['--enabled']))")
    child = subprocess.Popen([sys.executable, "-I", "-S", "-B", "-c", code, root, module],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert child.wait(timeout=10) == 2
        assert child.stdout.read() == b""
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        for stream in (child.stdin, child.stdout, child.stderr):
            if stream:
                stream.close()
