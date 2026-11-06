"""Offline cases for the on-demand capacity read route: a fake status run and an injected clock, no observer."""

import hashlib
import json
import subprocess  # only its TimeoutExpired class: every status run here is the fake one
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import capacity_lens as lens  # noqa: E402
import capacity_read as route  # noqa: E402

NOW = "2026-10-13T11:00:00+00:00"
THREADS = ("c0ffee11-aaaa-4bbb-8ccc-000000000001", "deadbe22-aaaa-4bbb-8ccc-000000000002")
SECRETS = ("sentinel-pool-1d7", "sentinelauthctx5e2", "sentinel-account-type", "sentinel-plan-type", "918273645",
           "sentinel-rate-payload", "sentinel-model-catalog", "sentinelalertid9c")
UNSAFE = "ops.user" + chr(64) + "example.com"


def synthetic_status(observed_at=NOW):
    """A synthetic observer status with sentinel identity, token and payload values, thread ids and an unsafe label."""
    return {"schema": lens.STATUS_SCHEMA, "execution_allowed": False, "observed_at": observed_at,
            "observations": [{"provider": "claude", "native_thread_id": THREADS[0], "observation_age_seconds": 30.0,
                              "freshness": "provider_timestamp_unknown", "quota_state": UNSAFE,
                              "account_pool": SECRETS[0], "auth_context_id": SECRETS[1],
                              "account_type": SECRETS[2], "plan_type": SECRETS[3],
                              "usage": {"total_input_tokens": int(SECRETS[4])},
                              "payload": {"rate_limits": SECRETS[5]}, "catalog": [SECRETS[6]],
                              "quota_windows": [{"name": "seven_day", "used_percent": 31, "resets_at": 1792000800}]}],
            "native_activity": [{"provider": "claude", "native_thread_id": THREADS[1],
                                 "activity_state": "idle_observed",
                                 "last_successful_turn_at": "2026-10-13T10:00:00+00:00"}],
            "collection": {"codex": {"last_success": "2026-10-13T10:58:00+00:00", "collection_state": "observed",
                                     "freshness_ttl_seconds": 300}},
            "alerts": [{"alert_id": SECRETS[7], "provider": "claude", "native_thread_id": THREADS[1],
                        "state": "rate_limited"}],
            "failed_providers": []}


@pytest.fixture
def run(tmp_path, monkeypatch, capsys):
    """route.main() over a fake status run (nothing starts) and an injected lens clock; returns (code, out, err,
    runs). The locator path is a synthetic file under tmp_path, the only thing the route ever reads by path."""
    script = tmp_path / "Get-WdCapacityStatus.ps1"
    script.write_text("# synthetic locator, never run\n", encoding="ascii")
    monkeypatch.setattr(route, "STATUS_SCRIPT", script)
    # CR2: the installed integrity record, here synthetic, carries the installer's pin of the locator (upper hex).
    integrity = tmp_path / "integrity.json"
    monkeypatch.setattr(route, "INTEGRITY", integrity)

    def record(value):
        """Write the synthetic integrity record (a function of the locator's pin), or remove it for None."""
        if value is None:
            integrity.unlink()
            return
        pin = hashlib.sha256(script.read_bytes()).hexdigest().upper()
        value = value(pin) if callable(value) else value
        integrity.write_bytes(value if isinstance(value, bytes) else json.dumps(value).encode("ascii"))

    record(lambda pin: {"schema_version": 1, "files": {"Get-WdCapacityStatus.ps1": pin, "other.ps1": "0" * 64}})
    # CR1: the status host is the synthetic System32 Windows PowerShell, never run.
    system = tmp_path / "System32"
    host = system / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    host.parent.mkdir(parents=True)
    host.write_bytes(b"synthetic host, never run")
    monkeypatch.setattr(lens, "system_directory", lambda: system)
    monkeypatch.chdir(tmp_path)

    def go(raw, argv=(), checked="2026-10-13T11:00:30+00:00", returncode=0, during=None):
        runs = []

        def fake_run(args, **kwargs):
            runs.append(args)
            assert kwargs == {"capture_output": True, "timeout": 60}
            if during is not None:
                during()
            return types.SimpleNamespace(returncode=returncode, stdout=raw)

        monkeypatch.setattr(lens, "subprocess", types.SimpleNamespace(run=fake_run,
                                                                      TimeoutExpired=subprocess.TimeoutExpired))
        monkeypatch.setattr(lens, "clock", lambda: lens.parse_time(checked))
        before = sorted(path.name for path in tmp_path.rglob("*"))
        try:
            code = route.main(list(argv))
        except SystemExit as exc:
            code = exc.code
        captured = capsys.readouterr()
        assert sorted(path.name for path in tmp_path.rglob("*")) == before  # the route writes nothing
        return code, captured.out, captured.err, runs

    go.script, go.host, go.record = script, host, record
    return go


def leaks(text):
    return [value for value in SECRETS + tuple(thread[:8] for thread in THREADS) + (UNSAFE,) if value in text]


def test_the_route_prints_the_guarded_lens_text_and_writes_nothing(run):
    raw = json.dumps(synthetic_status()).encode("utf-8")
    code, out, err, runs = run(raw)
    assert (code, err) == (0, "")
    assert runs == [[str(run.host), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(run.script), "-Json"]]
    lines = out.splitlines()
    assert lines[0].startswith("capacity (installed observer, read-only) observed 2026-10-13T11:00:00Z "
                               "(status age under 1 min, FRESH); ")
    assert "LIMITS claude seven_day: 31%; resets 2026-10-14T18:00Z (in 1860 min); observer " \
           "provider_timestamp_unknown, quota UNKNOWN, under 1 min old" in lines
    assert "COLLECTION codex: OK; state observed; last success 2026-10-13T10:58Z (2 min ago); no attempt " \
           "recorded; observer TTL 300 s" in lines
    assert "ALERTS: 1 (claude rate_limited x1)" in lines
    assert leaks(out) == []


def test_the_json_view_carries_the_digest_and_age_and_nothing_raw(run):
    raw = json.dumps(synthetic_status()).encode("utf-8")
    code, out, err, runs = run(raw, ["--json"])
    assert (code, err) == (0, "")
    view = json.loads(out)
    assert view["schema"] == lens.LENS_SCHEMA and view["authority"] == "none"
    assert view["status_sha256"] == hashlib.sha256(raw).hexdigest()
    assert view["status_age"] == {"checked_utc": "2026-10-13T11:00:30Z", "age_s": 30.0, "state": "FRESH"}
    assert leaks(out) == []


def test_a_stale_status_is_printed_with_every_figure_line_labelled(run):
    code, out, err, runs = run(json.dumps(synthetic_status()).encode("utf-8"), checked="2026-10-13T14:00:00+00:00")
    lines = out.splitlines()
    assert code == 0 and lines[0].startswith(
        "capacity (installed observer, read-only) observed 2026-10-13T11:00:00Z (status age 180 min, STALE); ")
    assert len(lines) > 1 and all(line.endswith(" (status STALE)") for line in lines[1:])


def test_a_future_status_is_refused_and_nothing_is_printed(run):
    code, out, err, runs = run(json.dumps(synthetic_status()).encode("utf-8"), checked="2026-10-13T10:50:00+00:00")
    assert (code, out) == ("refused: status_time_future", "")


@pytest.mark.parametrize("raw, returncode, reason", [
    (b"{", 0, "status_unreadable"), (b"[]", 0, "status_schema_unexpected"),
    (json.dumps(synthetic_status(observed_at=5)).encode("utf-8"), 0, "status_shape_unexpected"),
    (b"", 1, "status_failed")])
def test_a_status_the_lens_refuses_prints_only_a_fixed_refusal(run, raw, returncode, reason):
    code, out, err, runs = run(raw, returncode=returncode)
    assert (code, out, err) == (f"refused: {reason}", "", "")


def test_a_missing_locator_is_refused_before_any_run(run):
    run.script.unlink()
    code, out, err, runs = run(b"{}")
    assert (code, out, runs) == ("refused: status_locator_missing", "", [])


def test_no_argument_selects_another_script_or_writes_a_file(run):
    for argv in (["--status-script", "other.ps1"], ["--out-dir", "x"], ["--declared", "c0ffee11=fable-5"], ["x"]):
        code, out, err, runs = run(b"{}", argv)
        assert code == 2 and runs == [] and out == ""


def test_the_route_runs_the_installed_locator_path():
    assert str(route.STATUS_SCRIPT) == "C:" + chr(92) + "Python" + chr(92) + "Get-WdCapacityStatus.ps1"


def test_a_locator_that_is_a_link_is_refused_before_any_run(run, tmp_path):
    target = tmp_path / "elsewhere.ps1"
    target.write_text("# synthetic\n", encoding="ascii")
    run.script.unlink()
    try:
        run.script.symlink_to(target)
    except OSError:
        pytest.skip("this account may not create symbolic links")
    code, out, err, runs = run(b"{}")
    assert (code, out, runs) == ("refused: status_locator_missing", "", [])


def test_the_view_and_the_text_are_guarded_before_anything_is_printed(run, monkeypatch):
    guarded = []
    monkeypatch.setattr(route.ws, "guard_object", lambda value: guarded.append(type(value).__name__))
    code, out, err, runs = run(json.dumps(synthetic_status()).encode("utf-8"))
    assert code == 0 and guarded == ["dict", "list"]


# CR2 (RCO2 on PR 2044): the fixed locator is bound to the installer's own pin, read from the installed integrity
# record; the route never derives or invents one.
def test_a_locator_whose_bytes_differ_from_the_installed_pin_is_refused_before_any_run(run):
    run.script.write_text("# a different locator\n", encoding="ascii")
    code, out, err, runs = run(json.dumps(synthetic_status()).encode("utf-8"))
    assert (code, out, runs) == ("refused: status_locator_pin_mismatch", "", [])


UNPINNED = {
    "missing": None, "not_json": b"{", "not_utf8": b"\xff\xfe{", "not_an_object": b"[]",
    "schema_2": lambda pin: {"schema_version": 2, "files": {"Get-WdCapacityStatus.ps1": pin}},
    "schema_true": lambda pin: {"schema_version": True, "files": {"Get-WdCapacityStatus.ps1": pin}},
    "schema_text": lambda pin: {"schema_version": "1", "files": {"Get-WdCapacityStatus.ps1": pin}},
    "no_files": {"schema_version": 1},
    "files_a_list": lambda pin: {"schema_version": 1, "files": [pin]},
    "no_entry": lambda pin: {"schema_version": 1, "files": {"other.ps1": pin}},
    "short_pin": lambda pin: {"schema_version": 1, "files": {"Get-WdCapacityStatus.ps1": pin[:63]}},
    "long_pin": lambda pin: {"schema_version": 1, "files": {"Get-WdCapacityStatus.ps1": pin + "0"}},
    "not_hex": {"schema_version": 1, "files": {"Get-WdCapacityStatus.ps1": "g" * 64}},
    "a_number": {"schema_version": 1, "files": {"Get-WdCapacityStatus.ps1": 5}}}


@pytest.mark.parametrize("case", sorted(UNPINNED))
def test_a_missing_or_unusable_installed_pin_is_refused_before_any_run(run, case):
    run.record(UNPINNED[case])
    code, out, err, runs = run(json.dumps(synthetic_status()).encode("utf-8"))
    assert (code, out, err, runs) == ("refused: status_locator_unpinned", "", "", [])


def test_an_integrity_record_that_is_a_link_is_refused_before_any_run(run, tmp_path):
    target = tmp_path / "elsewhere.json"
    target.write_bytes(route.INTEGRITY.read_bytes())
    route.INTEGRITY.unlink()
    try:
        route.INTEGRITY.symlink_to(target)
    except OSError:
        pytest.skip("this account may not create symbolic links")
    code, out, err, runs = run(json.dumps(synthetic_status()).encode("utf-8"))
    assert (code, out, runs) == ("refused: status_locator_unpinned", "", [])


@pytest.mark.parametrize("change", ["rewritten", "removed"])
def test_a_locator_that_changes_during_the_run_is_refused_and_nothing_is_printed(run, change):
    def during():
        if change == "removed":
            run.script.unlink()
            run.script.mkdir()  # the name stays, so the route's no-file-written check still holds
        else:
            run.script.write_text("# swapped while running\n", encoding="ascii")

    code, out, err, runs = run(json.dumps(synthetic_status()).encode("utf-8"), during=during)
    assert (code, out, err) == ("refused: status_locator_changed", "", "") and len(runs) == 1


def test_a_planted_powershell_in_the_current_directory_is_never_run(run, tmp_path):
    (tmp_path / "powershell.exe").write_bytes(b"planted, must never run")
    code, out, err, runs = run(json.dumps(synthetic_status()).encode("utf-8"))
    assert code == 0 and [args[0] for args in runs] == [str(run.host)]


def test_the_route_reads_the_pin_from_the_installed_integrity_record():
    assert str(route.INTEGRITY) == "C:" + chr(92) + "Python" + chr(92) + "WD_REBOOT_INTEGRITY_CURRENT.json"
