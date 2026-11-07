"""Actual legacy worker refuses before input; guarded caller is separate."""
from pathlib import Path
import hashlib
import subprocess
import sys

import pytest

from tools import wd_q4_lane_caller_cli as guarded
from tools import wd_q4_session_worker as worker
from q4_legacy_session_fixtures import legacy_bytes


@pytest.mark.parametrize("argv", [None, [], ["--enabled"], ["--enabled", "--descriptor", "synthetic"]])
def test_actual_worker_refuses_before_options_files_or_stdin(monkeypatch, capsys, argv):
    class NoRead:
        @property
        def buffer(self):
            pytest.fail("legacy worker inspected stdin")
        def read(self, *args):
            pytest.fail("legacy worker read stdin")
    monkeypatch.setattr(sys, "stdin", NoRead())
    monkeypatch.setattr(worker, "_options", lambda *_: pytest.fail("legacy worker parsed arguments"))
    monkeypatch.setattr(worker, "_cli_arguments", lambda *_: pytest.fail("legacy worker read configuration"))
    monkeypatch.setattr(guarded, "main", lambda *_: pytest.fail("guarded caller unexpectedly invoked"))
    assert worker.main(argv) == 2
    assert capsys.readouterr().out == ""


def test_helpers_remain_importable_and_guarded_caller_is_unchanged(monkeypatch):
    assert callable(worker._options) and callable(worker._cli_arguments)
    original = guarded.main
    assert worker.main(["--enabled"]) == 2
    assert guarded.main is original


def test_historical_worker_fixture_is_separate_and_byte_pinned():
    raw = legacy_bytes("session_worker")
    assert hashlib.sha256(raw).hexdigest() == "ddcd15522bfe3421824cf5bdafbef5df5015706e696e7305d681c6b96623c6e3"
    assert b"options = _options(" in raw
    assert worker.main(["--enabled"]) == 2


def test_actual_main_entry_exits_two_without_question_read(tmp_path):
    script = Path(worker.__file__).resolve()
    program = ("import runpy,sys\np=sys.argv[1]\n"
               "def trace(frame,event,arg):\n"
               " if event=='call' and frame.f_code.co_filename==p and frame.f_code.co_name in ('_options','_cli_arguments'):\n"
               "  raise SystemExit(71)\n"
               " return trace\n"
               "sys.settrace(trace)\n"
               "sys.stdin=type('NoRead',(),{'read':lambda *a: (_ for _ in ()).throw(AssertionError('stdin_read')),"
               "'buffer':property(lambda s: (_ for _ in ()).throw(AssertionError('stdin_buffer')))})()\n"
               "sys.argv=[p,'--enabled']; runpy.run_path(p,run_name='__main__')\n")
    completed = subprocess.run([sys.executable, "-I", "-B", "-c", program, str(script)],
                               cwd=tmp_path, capture_output=True, timeout=20)
    assert completed.returncode == 2
    assert completed.stdout == b"" and b"stdin_read" not in completed.stderr and b"stdin_buffer" not in completed.stderr
