"""Synthetic descriptor + session only; never launches a live lane or installer."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys

import pytest

try:
    from tools import wd_q4_session_worker as worker
except ImportError:
    worker = None


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def case(monkeypatch, tmp_path):
    source = Path(__file__).with_name("test_wd_q4_lane_session_cli.py")
    spec = importlib.util.spec_from_file_location("worker_cli_test_fixtures", source)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    _, flags, old = helper.case.__wrapped__(monkeypatch, tmp_path)
    doc = {"schema": "wd.q4-shared-session-config.v1", "fleet_sha256": flags["fleet-sha256"],
           "lanes": ["codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5"]}
    for name in ("bundle-root", "public-plan", "interpreter", "interpreter-sha256", "cwd"):
        doc[name.replace("-", "_")] = flags[name]
    for name in ("interpreter-max-bytes", "timeout-seconds", "max-output-bytes"):
        doc[name.replace("-", "_")] = int(flags[name])
    for name in ("binding", "fixed-plan", "snapshot", "runtime-manifest"):
        key = name.replace("-", "_")
        doc[key + "_path"] = flags[name]
        doc[key + "_sha256"] = flags[name + "-sha256"]
    def args(document=doc, fleet_sha=None):
        raw = json.dumps(document, separators=(",", ":")).encode()
        path = tmp_path / "session-descriptor.json"
        path.write_bytes(raw)
        return ["--enabled", "--descriptor", str(path), "--descriptor-sha256", sha(raw),
                "--fleet", flags["fleet"], "--fleet-sha256", fleet_sha or flags["fleet-sha256"],
                "--lane", flags["lane"]]
    return doc, flags, old, args


def test_default_off_neither_reads_stdin_nor_runs_cli(monkeypatch, capsys):
    class NoRead:
        @property
        def buffer(self): pytest.fail("off worker touched stdin")
    monkeypatch.setattr(sys, "stdin", NoRead())
    assert worker.main([]) == 2
    assert capsys.readouterr().out == ""


def test_synthetic_chain_binds_pins_and_passes_one_bounded_input(case, monkeypatch, capsys):
    doc, flags, old, args = case
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"synthetic situation\n"), encoding="utf-8"))
    assert worker.main(args()) == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert captured.out.endswith("\n") and result["result_count"] == len(old["_expected"])
    assert result["lane"] == flags["lane"] and result["trust"]["lane_authentication"] == "UNKNOWN"
    assert "synthetic situation" not in captured.out + captured.err


@pytest.mark.parametrize("change", [lambda d: d.pop("binding_sha256"),
                                    lambda d: d.update(extra="bad"),
                                    lambda d: d.update(lanes=["codex-lead-1"]),
                                    lambda d: d.update(timeout_seconds=1801),
                                    lambda d: d.update(fleet_sha256="0" * 64)])
def test_invalid_closed_descriptor_refuses_before_reading_query(case, monkeypatch, capsys, change):
    class NoRead:
        @property
        def buffer(self): pytest.fail("invalid descriptor read stdin")
    monkeypatch.setattr(sys, "stdin", NoRead())
    doc, _, _, args = case
    change(doc)
    assert worker.main(args()) == 2
    assert capsys.readouterr().out == ""


def test_bad_descriptor_pin_and_wrong_lane_refuse(case, monkeypatch, capsys):
    doc, _, _, args = case
    a = args()
    a[a.index("--descriptor-sha256") + 1] = "0" * 64
    assert worker.main(a) == 2
    a = args()
    a[a.index("--lane") + 1] = "not-a-lane"
    assert worker.main(a) == 2
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("code", [2, 3, 4, 130])
def test_cli_refusal_timeout_and_cleanup_codes_are_not_reclassified(case, monkeypatch, code):
    from tools import wd_q4_lane_session_cli as cli
    _, _, _, args = case
    monkeypatch.setattr(cli, "main", lambda argv: code)
    assert worker.main(args()) == code


def test_oversize_query_refuses_before_session_dispatch(case, monkeypatch, capsys):
    from tools import wd_q4_lane_session_cli as cli
    _, _, _, args = case
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"x" * (cli.MAX_STDIN + 1)), encoding="utf-8"))
    monkeypatch.setattr(cli.session, "query_session", lambda **_: pytest.fail("oversized query dispatched"))
    assert worker.main(args()) == 2
    assert capsys.readouterr().out == ""


def test_case_and_escaped_colliding_descriptor_keys_fail_before_stdin(case, monkeypatch, capsys, tmp_path):
    class NoRead:
        @property
        def buffer(self): pytest.fail("colliding descriptor read stdin")
    monkeypatch.setattr(sys, "stdin", NoRead())
    doc, _, _, args = case
    for duplicate in ('"SCHEMA"', r'"\u0073chema"'):
        a = args()
        path = Path(a[a.index("--descriptor") + 1])
        raw = (json.dumps(doc)[:-1] + "," + duplicate + ':"wd.q4-shared-session-config.v1"}').encode()
        path.write_bytes(raw)
        a[a.index("--descriptor-sha256") + 1] = sha(raw)
        assert worker.main(a) == 2
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("phase", ["descriptor", "fleet"])
def test_preflight_keyboard_interrupt_is_sanitized_and_preserves_status(case, monkeypatch, capsys, phase):
    from tools import wd_q4_lane_session_cli as cli
    marker = "SYNTHETIC_KEYBOARD_INTERRUPT_SENTINEL"
    _, _, _, args = case
    original = cli._file
    calls = 0
    def interrupt_file(*a, **kw):
        nonlocal calls
        calls += 1
        if calls == (1 if phase == "descriptor" else 2):
            raise KeyboardInterrupt(marker)
        return original(*a, **kw)
    monkeypatch.setattr(cli, "_file", interrupt_file)
    class NoRead:
        @property
        def buffer(self): pytest.fail("interrupted preflight read stdin")
    monkeypatch.setattr(sys, "stdin", NoRead())
    assert worker.main(args()) == 130
    captured = capsys.readouterr()
    assert captured.out == "" and marker not in captured.err and "Traceback" not in captured.err


@pytest.mark.parametrize("original,expected", [(7, 7), (0, 2), (3, 2), (4, 2), (130, 2),
                                                (True, 2), (None, 2), ("synthetic-secret-message", 2)])
def test_preflight_systemexit_sanitizes_payload_and_preserves_allowed_code(case, monkeypatch, capsys, original, expected):
    from tools import wd_q4_lane_session_cli as cli
    _, _, _, args = case
    def leave(*_a, **_kw):
        raise SystemExit(original)
    monkeypatch.setattr(cli, "_file", leave)
    assert worker.main(args()) == expected
    captured = capsys.readouterr()
    assert captured.out == "" and "synthetic-secret-message" not in captured.err
    assert "session_exit" in captured.err and "Traceback" not in captured.err


def test_unrelated_baseexception_is_not_silently_swallowed(case, monkeypatch):
    from tools import wd_q4_lane_session_cli as cli
    _, _, _, args = case
    class OtherInterrupt(BaseException):
        pass
    def leave(*_a, **_kw):
        raise OtherInterrupt()
    monkeypatch.setattr(cli, "_file", leave)
    with pytest.raises(OtherInterrupt):
        worker.main(args())
