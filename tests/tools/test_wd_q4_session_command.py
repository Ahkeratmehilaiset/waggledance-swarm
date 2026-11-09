"""Synthetic command seam only: no fleet, bridge, or Mind data."""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
from tools import wd_q4_session_outer_entry as entry
from q4_legacy_session_fixtures import legacy_bytes

ROOT = Path(__file__).resolve().parents[2]
COMMAND = ROOT / "ops/windows/reboot/Invoke-WdQ4SessionCommand.ps1"
PWSH = shutil.which("pwsh")
pytestmark = pytest.mark.skipif(os.name != "nt" or not PWSH, reason="Windows PowerShell 7 required")


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def run(tmp_path, status="OK", *, enabled=True, wrong_hash=False, query=b"private-query-marker", repeat=False,
        inject_query_option=False, reload_before_second=False, reset_mutant=False, expected_invocations=None,
        extra_entry_args=(), expected_host_utc=None, drop_entry_key=None):
    host = tmp_path / "synthetic-host.ps1"
    invocations = tmp_path / "synthetic-host-invocations"
    host.write_text(
        "function Invoke-WdQ4SessionHost { param([switch]$Enabled,$JobHelper,$JobHelperSha256,"
        "$Interpreter,$InterpreterSha256,$Bootstrap,$BootstrapSha256,$BootstrapArguments,"
        "$WorkingDirectory,$ScratchRoot,[byte[]]$InputBytes,$TimeoutSeconds,$MaxOutputBytes)\n"
        " [IO.File]::AppendAllText(" + quote(invocations) + ",'call' + [char]10)\n"
        " if ([Text.Encoding]::UTF8.GetString($InputBytes) -cne 'private-query-marker') { throw 'input' }\n"
        " if (@($BootstrapArguments | Where-Object { $_ -match 'private-query-marker' }).Count -ne 0) { throw 'query in args' }\n"
        + (" if (@($BootstrapArguments | Where-Object { $_ -ceq '--query-utc' }).Count -ne 1 -or "
           "$BootstrapArguments[-2] -cne '--query-utc' -or $BootstrapArguments[-1] -cne "
           + quote(expected_host_utc) + ") { throw 'query time missing' }\n" if expected_host_utc else "")
        +
        " [pscustomobject]@{Status='" + ("OK" if status == "BAD_JSON" else status) + "';Output="
        + ("[Text.Encoding]::UTF8.GetBytes('not-json' + [char]10)" if status == "BAD_JSON" else
           "[Text.Encoding]::UTF8.GetBytes('{\"synthetic\":true}' + [char]10)" if status == "OK" else "$null")
        + "}\n}\n", encoding="utf-8")
    digest = hashlib.sha256(host.read_bytes()).hexdigest()
    if wrong_hash:
        digest = "0" * 64
    keys = ("JobHelper", "Interpreter", "Bootstrap", "WorkingDirectory", "ScratchRoot")
    options = ";".join(key + "=" + quote(tmp_path / key) for key in keys)
    options += "".join(";" + key + "='" + ("a" * 64) + "'" for key in
                       ("JobHelperSha256", "InterpreterSha256", "BootstrapSha256"))
    entry_args = ["--enabled"]
    for key in sorted(entry._OPTIONS):
        if key != drop_entry_key:
            entry_args.extend(("--" + key, "synthetic-pin-value"))
    if inject_query_option:
        entry_args[1] = "--query"
    entry_args.extend(extra_entry_args)
    options += ";BootstrapArguments=@(" + ",".join(quote(value) for value in entry_args) + ")"
    options += ";TimeoutSeconds=5;MaxOutputBytes=4096"
    call = ("Invoke-WdQ4SessionCommand " + ("-Enabled " if enabled else "") + "-HostScript " + quote(host)
            + " -HostSha256 '" + digest + "' -HostArguments $options")
    command_path = COMMAND
    if reset_mutant:
        original = COMMAND.read_text(encoding="utf-8")
        guard = ("if ($null -eq (Get-Variable WdQ4SessionCommandUnknown -Scope Script -ErrorAction SilentlyContinue)) {\n"
                 "    $script:WdQ4SessionCommandUnknown = $false\n}")
        assert original.count(guard) == 1
        command_path = tmp_path / "synthetic-command-reset-mutant.ps1"
        command_path.write_text(original.replace(guard, "$script:WdQ4SessionCommandUnknown = $false"),
                                encoding="utf-8")
    script = ("$ErrorActionPreference='Stop'\n. " + quote(command_path) + "\n$options=@{" + options + "}\n"
              + "$r=" + call + "\n"
              + "@{status=$r.Status;output=$(if($null -eq $r.Output){$null}else{[Text.Encoding]::UTF8.GetString($r.Output)})}"
                "|ConvertTo-Json -Compress\n")
    if repeat:
        script += (". " + quote(command_path) + "\n" if reload_before_second else "")
        script += "$r=" + call + "\n@{status=$r.Status}|ConvertTo-Json -Compress\n"
    result = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-EncodedCommand",
                             base64.b64encode(script.encode("utf-16-le")).decode("ascii")],
                            input=query, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert query not in result.stdout + result.stderr
    if expected_invocations is not None:
        calls = invocations.read_text(encoding="utf-8").splitlines() if invocations.exists() else []
        assert calls == ["call"] * expected_invocations
    return [json.loads(line) for line in result.stdout.splitlines()]


def test_default_off_does_not_read_stdin_or_load_host(tmp_path):
    assert run(tmp_path, enabled=False) == [{"status": "OFF", "output": None}]


def test_pinned_host_gets_only_stdin_and_output_is_explicitly_unvalidated(tmp_path):
    assert run(tmp_path) == [{"status": "UNVALIDATED", "output": '{"synthetic":true}\n'}]


@pytest.mark.parametrize("status", ["REFUSED", "UNKNOWN", "TIMEOUT"])
def test_non_ok_host_never_returns_output(tmp_path, status):
    assert run(tmp_path, status) == [{"status": status, "output": None}]


def test_hash_mismatch_refuses(tmp_path):
    assert run(tmp_path, wrong_hash=True) == [{"status": "REFUSED", "output": None}]


def test_oversized_stdin_refuses(tmp_path):
    assert run(tmp_path, query=b"x" * 16001) == [{"status": "REFUSED", "output": None}]


def test_query_option_never_reaches_host(tmp_path):
    assert run(tmp_path, inject_query_option=True) == [{"status": "REFUSED", "output": None}]


def test_optional_query_utc_reaches_pinned_host_without_query_in_arguments(tmp_path):
    when = "2026-01-01T00:01:02.000000Z"
    assert run(tmp_path, extra_entry_args=("--query-utc", when), expected_host_utc=when,
               expected_invocations=1) == [{"status": "UNVALIDATED", "output": '{"synthetic":true}\n'}]


@pytest.mark.parametrize("extra", [
    ("--query-utc", "2026-01-01T00:01:02Z"),
    ("--query-utc", "2026-01-01T00:01:02.000001Z"),
    ("--query-utc", "private-query-marker"),
    ("--query-utc", "2026-01-01T00:01:02.000000Z", "--query-utc", "2026-01-01T00:01:02.000000Z"),
], ids=("seconds-only", "nonzero-fraction", "query-in-arg", "duplicate"))
def test_invalid_or_duplicate_query_utc_never_reaches_host(tmp_path, extra):
    assert run(tmp_path, extra_entry_args=extra, expected_invocations=0) == [{"status": "REFUSED", "output": None}]


def test_query_utc_does_not_replace_a_required_binding(tmp_path):
    when = "2026-01-01T00:01:02.000000Z"
    assert run(tmp_path, extra_entry_args=("--query-utc", when), drop_entry_key="lane",
               expected_invocations=0) == [{"status": "REFUSED", "output": None}]


def test_malformed_host_ok_output_is_not_returned(tmp_path):
    assert run(tmp_path, status="BAD_JSON") == [{"status": "UNKNOWN", "output": None}]


def test_unknown_prevents_second_request_in_process(tmp_path):
    assert run(tmp_path, status="UNKNOWN", repeat=True) == [
        {"status": "UNKNOWN", "output": None}, {"status": "UNKNOWN"}]


def test_unknown_survives_redot_source_without_a_second_host_call(tmp_path):
    assert run(tmp_path, status="UNKNOWN", repeat=True, reload_before_second=True, expected_invocations=1) == [
        {"status": "UNKNOWN", "output": None}, {"status": "UNKNOWN"}]


def test_redot_source_regression_detects_a_synthetic_reset_mutant(tmp_path):
    # Test-only mutant proves that the preceding assertion detects a reset;
    # its command copy is confined to this synthetic test's audit temp.
    assert run(tmp_path, status="UNKNOWN", repeat=True, reload_before_second=True,
               reset_mutant=True, expected_invocations=1) == [
        {"status": "UNKNOWN", "output": None}, {"status": "REFUSED"}]


@pytest.mark.parametrize("legacy_fixture", [True, False], ids=["test-only-legacy", "packaged-refused"])
def test_host_and_entry_with_synthetic_query_backend(tmp_path, legacy_fixture):
    path = Path(__file__).with_name("test_wd_q4_session_host_e2e.py")
    spec = importlib.util.spec_from_file_location("synthetic_host_e2e_fixture", path)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    options, scratch, expected, _ = fixture.plan.__wrapped__(tmp_path)
    args = ["--enabled"]
    for key, value in options.items():
        args.extend(("--" + key, value))
    args_ps = "@(" + ",".join(quote(value) for value in args) + ")"
    job, host, bootstrap = fixture.JOB, fixture.HOST, fixture.ENTRY
    if legacy_fixture:
        bootstrap = tmp_path / "test-only-legacy-entry.py"
        bootstrap.write_bytes(legacy_bytes("outer_entry"))
    options_ps = ("JobHelper=" + quote(job) + ";JobHelperSha256=" + quote(fixture.sha(job))
        + ";Interpreter=" + quote(fixture.sys.executable)
        + ";InterpreterSha256=" + quote(fixture.sha(Path(fixture.sys.executable)))
        + ";Bootstrap=" + quote(bootstrap) + ";BootstrapSha256=" + quote(fixture.sha(bootstrap))
        + ";BootstrapArguments=" + args_ps + ";WorkingDirectory=" + quote(options["cwd"])
        + ";ScratchRoot=" + quote(scratch) + ";TimeoutSeconds=30;MaxOutputBytes=4096")
    script = ("$ErrorActionPreference='Stop'\n. " + quote(COMMAND) + "\n"
              + "$r=Invoke-WdQ4SessionCommand -Enabled -HostScript " + quote(host)
              + " -HostSha256 " + quote(fixture.sha(host)) + " -HostArguments @{" + options_ps + "}\n"
              + "@{status=$r.Status;output=$(if($null -eq $r.Output){$null}else{[Text.Encoding]::UTF8.GetString($r.Output)})}"
                "|ConvertTo-Json -Compress\n")
    result = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-EncodedCommand",
                             base64.b64encode(script.encode("utf-16-le")).decode("ascii")],
                            input=fixture.QUERY, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert fixture.QUERY not in result.stdout + result.stderr
    data = json.loads(result.stdout)
    if legacy_fixture:
        assert data["status"] == "UNVALIDATED"
        assert json.loads(data["output"]) == expected
    else:
        # The packaged legacy route is intentionally retired; it publishes no answer.
        assert data == {"status": "REFUSED", "output": None}
