"""Synthetic host -> test-only historical entry -> outer -> session CLI.

Only the session query backend is synthetic. This
does not prove real retrieval, source freshness, authentication, install or
cross-process quarantine. No real Mind data or running lanes are accessed.
"""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from q4_legacy_session_fixtures import legacy_bytes

from sandbox.mind.step2 import runtime_builder

ROOT = Path(__file__).resolve().parents[2]
HOST = ROOT / "ops/windows/reboot/Invoke-WdQ4SessionHost.ps1"
JOB = ROOT / "ops/windows/reboot/WdQ4ProcessJob.ps1"
ENTRY = ROOT / "tools/wd_q4_session_outer_entry.py"
OUTER = ROOT / "tools/wd_q4_session_outer_synthetic.py"
CLI = ROOT / "tools/wd_q4_lane_session_cli.py"
PWSH = shutil.which("pwsh")
QUERY = b"q4-e2e-query-only-2683c489"
ANSWER_QUERY = b"q4-e2e-answer-only-8054b638"

pytestmark = pytest.mark.skipif(os.name != "nt" or not PWSH, reason="Windows PowerShell 7 required")


@pytest.fixture(autouse=True)
def legacy_entry_path(monkeypatch, tmp_path):
    entry = tmp_path / "test-only-legacy-entry.py"
    entry.write_bytes(legacy_bytes("outer_entry"))
    monkeypatch.setitem(globals(), "ENTRY", entry)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def q(value):
    return "'" + str(value).replace("'", "''") + "'"


@pytest.fixture
def plan(tmp_path):
    spec = importlib.util.spec_from_file_location("q4_outer_fixture", Path(__file__).with_name(
        "test_wd_q4_session_outer_synthetic.py"))
    fixtures = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixtures)
    inputs = fixtures.synthetic.__wrapped__(tmp_path / "fixture")
    root = Path(inputs["runtime_root"])
    code = root / "assets/code"
    outer = code / "tools/wd_q4_session_outer_synthetic.py"
    outer.write_bytes(legacy_bytes("outer_synthetic"))
    worker = code / "tools/wd_q4_lane_session_cli.py"
    worker.write_bytes(legacy_bytes("session_cli"))
    # Only the session query seam is substituted; argv and stdin assertions
    # run INSIDE the synthetic child and are not written to an artifact.
    _, answer, reason = fixtures._projected_answer("m0-snapshot-ready")
    assert reason is None and answer["status"] == "ANSWER"
    seam = ("import sys\ndef query_session(**kw):\n"
            f" assert kw['situation'] in ({QUERY.decode()!r},{ANSWER_QUERY.decode()!r})\n"
            f" assert all({QUERY.decode()!r} not in x and {ANSWER_QUERY.decode()!r} not in x for x in sys.argv)\n"
            " assert kw['lane']=='codex-lead-1' and kw['enabled'] is True\n"
            f" if kw['situation']=={ANSWER_QUERY.decode()!r}: return {answer!r}\n"
            f" return {fixtures.ANSWER!r}\n").encode("utf-8")
    (code / "tools/wd_q4_lane_session_client.py").write_bytes(seam)
    for name in ("wd_q4_fixed_parent_attestation.py", "wd_q4_secure_transport.py"):
        (code / "tools" / name).write_bytes((ROOT / "tools" / name).read_bytes())
    manifest = tmp_path / "runtime-manifest.json"
    built = runtime_builder.build_runtime_manifest(root, manifest, generation=fixtures.GENERATION,
                                                    revision=fixtures.REVISION)
    options = {"outer-path": str(outer), "outer-sha256": sha(outer), "runtime-root": str(root),
               "runtime-generation": fixtures.GENERATION, "runtime-revision": fixtures.REVISION,
               "verifier-sha256": inputs["verifier_sha256"], "public-outer-sha256": inputs["outer_sha256"],
               "public-child-sha256": inputs["child_sha256"], "public-query-sha256": inputs["query_sha256"],
               "worker-sha256": sha(worker), "lane": "codex-lead-1", "bundle-root": str(tmp_path / "bundle"),
               "public-plan": str(tmp_path / "public-plan"), "interpreter": sys.executable,
               "interpreter-sha256": sha(Path(sys.executable)), "interpreter-max-bytes": "134217728",
               "cwd": str(tmp_path / "work"), "timeout-seconds": "30", "max-output-bytes": "4096",
               "runtime-manifest": str(manifest), "runtime-manifest-sha256": sha(manifest)}
    for name in ("fleet", "binding", "fixed-plan", "snapshot"):
        path = tmp_path / name
        path.write_bytes(("synthetic-" + name).encode())
        options[name] = str(path)
        options[name + "-sha256"] = sha(path)
    for key, value in fixtures.BINDING.items():
        options["expected-" + key.replace("_", "-")] = value
    work = tmp_path / "work"
    work.mkdir()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return options, scratch, fixtures.ANSWER, answer


def call_host(options, scratch, *, enabled=True, wrong_pin=None, query=QUERY):
    args = ["--enabled"]
    for key, value in options.items():
        args.extend(("--" + key, value))
    if wrong_pin:
        i = args.index("--" + wrong_pin)
        args[i + 1] = "0" * 64
    assert all(query.decode() not in item for item in args)
    array = "@(" + ",".join(q(x) for x in args) + ")"
    script = ("$ErrorActionPreference='Stop'\n. " + q(HOST) + "\n"
              "$m=[IO.MemoryStream]::new()\n[Console]::OpenStandardInput().CopyTo($m)\n$q=$m.ToArray()\n"
              "$r=Invoke-WdQ4SessionHost " + ("-Enabled " if enabled else "")
              + "-JobHelper " + q(JOB) + " -JobHelperSha256 " + q(sha(JOB))
              + " -Interpreter " + q(sys.executable) + " -InterpreterSha256 " + q(sha(Path(sys.executable)))
              + " -Bootstrap " + q(ENTRY) + " -BootstrapSha256 " + q(sha(ENTRY))
              + " -BootstrapArguments " + array + " -WorkingDirectory " + q(options["cwd"])
              + " -ScratchRoot " + q(scratch) + " -InputBytes $q -TimeoutSeconds 30 -MaxOutputBytes 4096\n"
              "@{status=$r.Status;output=$(if($null -eq $r.Output){$null}else{[Text.Encoding]::UTF8.GetString($r.Output)})}|ConvertTo-Json -Compress\n")
    assert query.decode() not in script
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    result = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                            input=query, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert query not in result.stdout + result.stderr
    return json.loads(result.stdout)


def test_default_off_does_not_launch(plan):
    options, scratch, _, _ = plan
    assert call_host(options, scratch, enabled=False) == {"status": "OFF", "output": None}
    assert list(scratch.iterdir()) == []


def test_packaged_entry_refuses_the_legacy_enabled_route(plan, monkeypatch):
    """Exercise the current production entry, not the historical fixture."""
    monkeypatch.setitem(globals(), "ENTRY", ROOT / "tools/wd_q4_session_outer_entry.py")
    options, scratch, _, _ = plan
    assert call_host(options, scratch) == {"status": "REFUSED", "output": None}


def test_real_chain_with_synthetic_query_seam_returns_bounded_answer(plan):
    options, scratch, answer, _ = plan
    result = call_host(options, scratch)
    assert result["status"] == "OK"
    assert result["output"].count("\n") == 1 and result["output"].endswith("\n")
    assert json.loads(result["output"]) == answer


def test_real_chain_returns_synthetic_projected_answer(plan):
    options, scratch, _, answer = plan
    result = call_host(options, scratch, query=ANSWER_QUERY)
    assert result["status"] == "OK"
    assert result["output"].count("\n") == 1 and result["output"].endswith("\n")
    assert json.loads(result["output"]) == answer


@pytest.mark.parametrize("wrong_pin", ["public-query-sha256", "worker-sha256", "runtime-manifest-sha256"])
def test_bad_pin_refuses_without_answer(plan, wrong_pin):
    options, scratch, _, _ = plan
    assert call_host(options, scratch, wrong_pin=wrong_pin) == {"status": "REFUSED", "output": None}
