"""Public card publisher wrapper: default-off, hash-pinned typed card request, bounded child, sanitized result.

Synthetic bundles only; the packaged bridge Python wrapper is replaced by a synthetic recorder that checks the exact
fixed entrypoint, arguments and the child-only pin split. No live bridge, no real card is published.
"""
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "ops/windows/reboot/Invoke-WdQ4PublicCard.ps1"
JOB_HELPER = SCRIPT.with_name("WdQ4ProcessJob.ps1")
_PROBE = "synthetic-parent-value-never-forwarded"
_INPUTS = ("policy", "public_policy", "card_policy", "roster", "vocabulary", "path_allowlist")
_REF = "e" * 64
_WAL = "a" * 64
_SECRET = "synthetic-claim-text-never-echoed"
_PORTABLE_OFF_TESTS = frozenset((
    "test_default_off_without_any_pins",
    "test_default_off_ignores_every_other_argument",
))


@pytest.fixture(autouse=True)
def _require_supported_wrapper_host(request):
    if os.name == "nt":
        return
    if request.node.originalname not in _PORTABLE_OFF_TESTS:
        pytest.skip("wrapper -Run requires Windows C: paths and kernel32 Job objects")
    if shutil.which("pwsh") is None:
        pytest.skip("portable default-off check requires PowerShell 7 (pwsh)")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def _ps_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def _written(**extra):
    value = {"clock_authentication": "UNKNOWN", "delivery": "canonical", "event_ref_sha256": _REF,
             "publisher_authentication": "UNKNOWN", "source_authentication": "UNKNOWN", "status": "WRITTEN",
             "truth": "UNVERIFIED"}
    value.update(extra)
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _fake_wrapper(marker, record, *, line, code, sleep=0, stderr="", grandchild=0):
    """Synthetic Invoke-WdBridgePython.ps1: record what the child saw, then answer like the CLI would."""
    late = str(record) + ".late"
    body = [
        "param([Parameter(Position=0)]$Tool,[switch]$VerifyPackage,[Parameter(ValueFromRemainingArguments=$true)][string[]]$Rest)",
        "$seen=[ordered]@{tool=[string]$Tool; verify=[bool]$VerifyPackage; rest=@($Rest);",
        " active=[string]$env:WD_MIND_PUBLIC_ACTIVE_MANIFEST_SHA256; candidate=[string]$env:WD_REBOOT_EXPECTED_MANIFEST_HASH;",
        " session=[string]$env:AGENT_BRIDGE_SESSION_ID; run=[string]$env:AGENT_BRIDGE_RUN_ID; bin=[string]$env:WD_BRIDGE_BIN;",
        " agent=[string]$env:AGENT_BRIDGE_AGENT; probe=[string]$env:WD_CARD_PROBE_NOT_FORWARDED;",
        " role=[string]$env:AGENT_BRIDGE_ROLE; command=[Environment]::CommandLine}",
        "[IO.File]::WriteAllText(" + _ps_literal(record) + ",($seen | ConvertTo-Json -Compress))",
        "[IO.File]::AppendAllText(" + _ps_literal(marker) + ",'x')",
    ]
    if grandchild:
        body.append("Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') -WindowStyle Hidden -ArgumentList "
                    "@('-NoProfile','-Command',\"Start-Sleep -Seconds " + str(int(grandchild)) + "; "
                    "[IO.File]::WriteAllText(" + _ps_literal(late + ".grandchild").replace('"', '`"') + ",'late')\")")
    if sleep:
        body.append("Start-Sleep -Seconds " + str(int(sleep)))
        body.append("[IO.File]::WriteAllText(" + _ps_literal(late) + ",'late')")
    if stderr:
        body.append("[Console]::Error.WriteLine(" + _ps_literal(stderr) + ")")
    if line is not None:
        body.append(_ps_literal(line))
    body.append("$global:LASTEXITCODE=" + str(int(code)))
    return ("\n".join(body) + "\n").encode("ascii")


@pytest.fixture
def bundles(tmp_path):
    active = tmp_path / "active"
    binary = active / "tools-bootstrap/.agent-bridge/bin"
    binary.mkdir(parents=True)
    active_files = {}
    for name in ("Get-BridgeExecutionEvidence.ps1", "Write-AgentEvent.ps1"):
        data = ("synthetic-" + name).encode()
        (binary / name).write_bytes(data)
        active_files["tools-bootstrap/.agent-bridge/bin/" + name] = sha(data)
    active_raw = json.dumps({"files": active_files}).encode()
    (active / "deployment-manifest.json").write_bytes(active_raw)
    candidate = tmp_path / "candidate"
    marker = tmp_path / "called.txt"
    record = tmp_path / "seen.json"
    files = {SCRIPT.name: SCRIPT.read_bytes(),
             "Invoke-WdBridgePython.ps1": _fake_wrapper(marker, record, line=_written(), code=0),
             "BridgeCodeContext.ps1": b"synthetic-context", "bridge-code-files.json": b"{}", "wd-fleet.json": b"{}",
             "WdQ4ProcessJob.ps1": JOB_HELPER.read_bytes(),
             "tools-bootstrap/tools/wd_q4_public_card_publish_cli.py": b"synthetic-cli",
             "tools-bootstrap/tools/wd_q4_public_card_publish.py": b"synthetic-publisher",
             "tools-bootstrap/tools/wd_q4_public_card_prepare.py": b"synthetic-prepare",
             "tools-bootstrap/tools/wd_q4_public_card_prefix_verifier.py": b"synthetic-verifier",
             "tools-bootstrap/tools/wd_mind_public_event_writer.py": b"synthetic-writer",
             "tools-bootstrap/tools/wd_q4_real_card_snapshot_cli.py": b"synthetic-files",
             "tools-bootstrap/configs/mind/pilot_non_review_policy.json": b"synthetic-policy",
             "tools-bootstrap/configs/mind/pilot_public_status_policy.json": b"synthetic-public"}
    for name, data in files.items():
        path = candidate / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    item = {"active": active, "bin": binary, "active_pin": sha(active_raw), "candidate": candidate,
            "marker": marker, "record": record, "files": files, "tmp": tmp_path, "scratch": tmp_path / "scratch"}
    item["scratch"].mkdir()
    _seal(item)
    inputs = {}
    for name in _INPUTS:
        data = ("synthetic-" + name).encode()
        path = tmp_path / "inputs" / (name + ".json")
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(data)
        inputs[name] = {"path": str(path), "sha256": sha(data)}
    item["inputs"] = inputs
    _request(item, {"schema": "wd.q4-public-card-publish-request.v1", "claim": {"text": _SECRET}, "inputs": inputs})
    return item


def _seal(item):
    candidate = item["candidate"]
    names = sorted(str(p.relative_to(candidate)).replace("\\", "/") for p in candidate.rglob("*")
                   if p.is_file() and p.name != "deployment-manifest.json")
    raw = json.dumps({"files": {name: sha((candidate / name).read_bytes()) for name in names}}).encode()
    (candidate / "deployment-manifest.json").write_bytes(raw)
    item["candidate_pin"] = sha(raw)


def _request(item, value, name="card-request.json"):
    path = item["tmp"] / "requests" / name
    path.parent.mkdir(exist_ok=True)
    raw = json.dumps(value, sort_keys=True).encode() if not isinstance(value, bytes) else value
    path.write_bytes(raw)
    item["request"], item["request_pin"] = path, sha(raw)


def _swap_wrapper(item, **kwargs):
    path = item["candidate"] / "Invoke-WdBridgePython.ps1"
    path.write_bytes(_fake_wrapper(item["marker"], item["record"], **kwargs))
    _seal(item)


def invoke(item, *, launcher=None, extra=(), env_changes=None, **overrides):
    values = {"candidate": str(item["candidate"]), "candidate_pin": item["candidate_pin"],
              "request": str(item["request"]), "request_pin": item["request_pin"], "active_pin": item["active_pin"],
              "scratch": str(item["scratch"])}
    values.update(overrides)
    env = dict(os.environ, WD_BRIDGE_BIN=str(item["bin"]), WD_REBOOT_EXPECTED_MANIFEST_HASH=values["active_pin"],
               AGENT_BRIDGE_SESSION_ID="wd-reboot-synthetic-session", AGENT_BRIDGE_RUN_ID="wd-reboot-synthetic-run",
               AGENT_BRIDGE_AGENT="codex-lead-1", AGENT_BRIDGE_ROLE="synthetic-role", WD_CARD_PROBE_NOT_FORWARDED=_PROBE)
    env.pop("WD_MIND_PUBLIC_ACTIVE_MANIFEST_SHA256", None)
    env.update(env_changes or {})
    command = ["powershell", "-NoProfile", "-NonInteractive", "-File",
               str(launcher or (item["candidate"] / SCRIPT.name)), "-Run",
               "-CandidateBundle", values["candidate"], "-ExpectedCandidateManifestSha256", values["candidate_pin"],
               "-CardRequest", values["request"], "-ExpectedCardRequestSha256", values["request_pin"],
               "-ScratchRoot", values["scratch"], *extra]
    return subprocess.run(command, capture_output=True, text=True, timeout=90, env=env)


def _refused_before_child(item, result):
    assert result.returncode == 2, result.stdout + result.stderr
    assert json.loads(result.stdout) == {"status": "REFUSED"}
    assert not item["marker"].exists()


def test_default_off_without_any_pins():
    host = "powershell" if os.name == "nt" else "pwsh"
    result = subprocess.run([host, "-NoProfile", "-NonInteractive", "-File", str(SCRIPT)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"status": "OFF"}


def test_default_off_ignores_every_other_argument(bundles):
    host = "powershell" if os.name == "nt" else "pwsh"
    result = subprocess.run([host, "-NoProfile", "-NonInteractive", "-File", str(bundles["candidate"] / SCRIPT.name),
                             "-CandidateBundle", str(bundles["candidate"]), "-CardRequest", str(bundles["request"])],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0 and json.loads(result.stdout) == {"status": "OFF"}
    assert not bundles["marker"].exists()


def test_written_reaches_fixed_entrypoint_in_child_with_split_pins_and_inherited_session(bundles):
    assert bundles["active_pin"] != bundles["candidate_pin"]
    result = invoke(bundles)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == json.loads(_written())
    assert bundles["marker"].read_text() == "x"
    seen = json.loads(bundles["record"].read_text(encoding="utf-8-sig"))
    assert seen["tool"] == "tools/wd_q4_public_card_publish_cli.py" and seen["verify"] is True
    assert seen["rest"] == ["--enabled", "--request", str(bundles["request"]), "--request-sha256", bundles["request_pin"]]
    assert seen["active"] == bundles["active_pin"] and seen["candidate"] == bundles["candidate_pin"]
    assert seen["session"] == "wd-reboot-synthetic-session" and seen["run"] == "wd-reboot-synthetic-run"
    assert Path(seen["bin"]) == bundles["bin"] and seen["agent"] == "codex-lead-1"
    assert seen["probe"] == "" and seen["role"] == ""  # Job child gets a fixed allowlist, not the whole parent env
    command = seen["command"]
    encoded = re.search(r'-EncodedCommand"?\s+"?([A-Za-z0-9+/=]+)', command).group(1)
    program = base64.b64decode(encoded, validate=True).decode("utf-16-le")
    assert "tools/wd_q4_public_card_publish_cli.py" in program  # the decoded child program, not an empty parse
    for value in ("wd-reboot-synthetic-session", "wd-reboot-synthetic-run", "codex-lead-1", _PROBE, _SECRET):
        assert value not in command and value not in program  # session values travel on stdin only
    assert _SECRET not in result.stdout + result.stderr


def test_queued_result_is_sanitized_and_not_mistaken_for_canonical(bundles):
    _swap_wrapper(bundles, line=_written(status="QUEUED", delivery="queued", retained_wal_sha256=_WAL), code=0)
    result = invoke(bundles)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == json.loads(_written(status="QUEUED", delivery="queued", retained_wal_sha256=_WAL))


@pytest.mark.parametrize("answer", [
    ("refused", None, 2, "REFUSED", 2),
    ("unknown", None, 3, "UNKNOWN", 3),
    ("extra field", _written(message=_SECRET), 0, "UNKNOWN", 3),
    ("wrong truth", _written(truth="VERIFIED"), 0, "UNKNOWN", 3),
    ("short ref", _written(event_ref_sha256="e" * 63), 0, "UNKNOWN", 3),
    ("queued without wal", _written(status="QUEUED", delivery="queued"), 0, "UNKNOWN", 3),
    ("queued bad wal", _written(status="QUEUED", delivery="queued", retained_wal_sha256="A" * 64), 0, "UNKNOWN", 3),
    ("written as queued delivery", _written(delivery="queued"), 0, "UNKNOWN", 3),
    ("two lines", _written() + "\n" + _written(), 0, "UNKNOWN", 3),
    ("success text with error code", _written(), 2, "UNKNOWN", 3),
    ("bad exit", None, 7, "UNKNOWN", 3),
], ids=lambda value: value[0])
def test_child_outcome_is_mapped_without_retry_or_pass_through(bundles, answer):
    _, line, code, status, exit_code = answer
    _swap_wrapper(bundles, line=line, code=code, stderr=_SECRET)
    result = invoke(bundles)
    assert bundles["marker"].read_text() == "x"  # entered exactly once, never retried
    assert result.returncode == exit_code and json.loads(result.stdout) == {"status": status}
    assert _SECRET not in result.stdout + result.stderr


def test_child_over_time_bound_is_unknown_and_its_job_tree_is_ended(bundles):
    _swap_wrapper(bundles, line=_written(), code=0, sleep=6, grandchild=6)
    result = invoke(bundles, extra=("-TimeoutSeconds", "3"))
    assert result.returncode == 3 and json.loads(result.stdout) == {"status": "UNKNOWN"}
    assert bundles["marker"].read_text() == "x"
    time.sleep(9)  # both sleepers would have written by now had the owned Job not ended them
    late = Path(str(bundles["record"]) + ".late")
    assert not late.exists() and not Path(str(late) + ".grandchild").exists()


def test_grandchild_is_ended_with_the_job_after_a_normal_answer(bundles):
    _swap_wrapper(bundles, line=_written(), code=0, grandchild=4)
    result = invoke(bundles)
    assert result.returncode == 0, result.stdout + result.stderr
    time.sleep(7)
    assert not Path(str(bundles["record"]) + ".late.grandchild").exists()


@pytest.mark.parametrize("outcome", [("completed", "unknown"), ("cleanup_unknown", "unknown"), ("timeout", "confirmed"),
                                     ("refused", "confirmed"), ("refused", "not_started")], ids="-".join)
def test_job_outcome_other_than_completed_with_confirmed_cleanup_is_unknown(bundles, outcome):
    """A synthetic, manifest-pinned Job helper reports the outcome; the wrapper must not trust its stdout then."""
    helper = ("function Invoke-WdQ4ProcessJob { param($Executable,$Arguments,$WorkingDirectory,$ScratchRoot,$InputBytes,"
              "$TimeoutSeconds,$MaxOutputBytes,[switch]$DetailedOutcome)\n"
              "[IO.File]::AppendAllText(" + _ps_literal(bundles["marker"]) + ",'x')\n"
              "[pscustomobject]@{Outcome='" + outcome[0] + "';Cleanup='" + outcome[1] + "';ExitCode=0;"
              "Stdout=[Text.Encoding]::UTF8.GetBytes(" + _ps_literal(_written() + "\n") + ")} }\n")
    (bundles["candidate"] / "WdQ4ProcessJob.ps1").write_text(helper, encoding="ascii")
    _seal(bundles)
    result = invoke(bundles)
    assert bundles["marker"].read_text() == "x"
    assert result.returncode == 3 and json.loads(result.stdout) == {"status": "UNKNOWN"}


@pytest.mark.parametrize("fault", ["missing", "empty", "file", "relative", "inside_candidate", "contains_candidate",
                                   "inside_active", "contains_request", "contains_input", "junction"])
def test_scratch_root_must_be_independent_before_child(bundles, fault):
    tmp = bundles["tmp"]
    scratch = {"missing": tmp / "absent-scratch", "empty": "", "file": tmp / "scratch-file",
               "relative": "scratch", "inside_candidate": bundles["candidate"] / "scratch",
               "contains_candidate": tmp, "inside_active": bundles["active"] / "scratch",
               "contains_request": bundles["request"].parent, "contains_input": tmp / "inputs",
               "junction": tmp / "scratch-junction"}[fault]
    if fault == "file":
        scratch.write_bytes(b"x")
    if fault in ("inside_candidate", "inside_active"):
        scratch.mkdir()
    if fault == "junction":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(scratch), str(bundles["scratch"])], check=True,
                       capture_output=True)
    if fault == "inside_candidate":
        _seal(bundles)
    _refused_before_child(bundles, invoke(bundles, scratch=str(scratch)))


def test_launcher_copy_outside_candidate_refuses_before_child(bundles, tmp_path):
    copied = tmp_path / "unbound-launcher.ps1"
    copied.write_bytes(SCRIPT.read_bytes())
    _refused_before_child(bundles, invoke(bundles, launcher=copied))


@pytest.mark.parametrize("fault", ["active_pin", "candidate_pin", "request_pin", "request_pin_upper", "active_helper",
                                   "candidate_wrapper", "candidate_launcher", "candidate_cli", "candidate_publisher",
                                   "candidate_policy", "candidate_fleet", "request_relative", "request_dotdot",
                                   "request_missing", "timeout_zero", "no_active_bin", "candidate_job_helper"])
def test_wrong_or_changed_anchor_refuses_before_child(bundles, fault):
    kwargs, extra, env = {}, (), None
    if fault == "active_pin":
        kwargs["active_pin"] = "0" * 64
    elif fault == "candidate_pin":
        kwargs["candidate_pin"] = "0" * 64
    elif fault == "request_pin":
        kwargs["request_pin"] = "0" * 64
    elif fault == "request_pin_upper":
        kwargs["request_pin"] = bundles["request_pin"].upper()
    elif fault == "request_relative":
        kwargs["request"] = os.path.relpath(bundles["request"])
    elif fault == "request_dotdot":
        kwargs["request"] = str(bundles["request"].parent / ".." / "requests" / bundles["request"].name)
    elif fault == "request_missing":
        kwargs["request"] = str(bundles["request"].with_name("absent.json"))
    elif fault == "timeout_zero":
        extra = ("-TimeoutSeconds", "0")
    elif fault == "no_active_bin":
        env = {"WD_BRIDGE_BIN": ""}
    else:
        target = {"active_helper": bundles["bin"] / "Write-AgentEvent.ps1",
                  "candidate_wrapper": bundles["candidate"] / "Invoke-WdBridgePython.ps1",
                  "candidate_launcher": bundles["candidate"] / SCRIPT.name,
                  "candidate_cli": bundles["candidate"] / "tools-bootstrap/tools/wd_q4_public_card_publish_cli.py",
                  "candidate_publisher": bundles["candidate"] / "tools-bootstrap/tools/wd_q4_public_card_publish.py",
                  "candidate_policy": bundles["candidate"] / "tools-bootstrap/configs/mind/pilot_non_review_policy.json",
                  "candidate_fleet": bundles["candidate"] / "wd-fleet.json",
                  "candidate_job_helper": bundles["candidate"] / "WdQ4ProcessJob.ps1"}[fault]
        target.write_bytes(target.read_bytes() + (b"\n#tamper\n" if target.suffix == ".ps1" else b"tamper"))
    _refused_before_child(bundles, invoke(bundles, extra=extra, env_changes=env, **kwargs))


@pytest.mark.parametrize("fault", ["schema", "extra_key", "missing_claim", "claim_not_object", "inputs_missing_name",
                                   "inputs_extra_name", "spec_extra_key", "spec_pin_upper", "spec_path_relative",
                                   "not_json", "bom", "oversize", "empty"])
def test_untyped_card_request_refuses_before_child(bundles, fault):
    value = {"schema": "wd.q4-public-card-publish-request.v1", "claim": {"text": _SECRET},
             "inputs": json.loads(json.dumps(bundles["inputs"]))}
    if fault == "schema":
        value["schema"] = "wd.q4-public-card-publish-request.v2"
    elif fault == "extra_key":
        value["enabled"] = True
    elif fault == "missing_claim":
        value.pop("claim")
    elif fault == "claim_not_object":
        value["claim"] = _SECRET
    elif fault == "inputs_missing_name":
        value["inputs"].pop("roster")
    elif fault == "inputs_extra_name":
        value["inputs"]["anchors"] = value["inputs"]["roster"]
    elif fault == "spec_extra_key":
        value["inputs"]["policy"]["size"] = 1
    elif fault == "spec_pin_upper":
        value["inputs"]["policy"]["sha256"] = value["inputs"]["policy"]["sha256"].upper()
    elif fault == "spec_path_relative":
        value["inputs"]["policy"]["path"] = "inputs/policy.json"
    raw = {"not_json": b"{not json", "bom": b"\xef\xbb\xbf" + json.dumps(value).encode(),
           "oversize": json.dumps(dict(value, claim={"text": "x" * 70000})).encode(), "empty": b""}.get(fault)
    _request(bundles, raw if raw is not None else value, name=fault + ".json")
    _refused_before_child(bundles, invoke(bundles))


def test_parent_bridge_pins_are_not_changed_by_a_run(bundles):
    """The calling lane keeps its own anchors; only the bounded child sees the candidate pin."""
    probe = bundles["tmp"] / "probe.ps1"
    probe.write_text(
        "& " + _ps_literal(bundles["candidate"] / SCRIPT.name) + " -Run -CandidateBundle " + _ps_literal(bundles["candidate"]) +
        " -ExpectedCandidateManifestSha256 " + _ps_literal(bundles["candidate_pin"]) + " -CardRequest " +
        _ps_literal(bundles["request"]) + " -ExpectedCardRequestSha256 " + _ps_literal(bundles["request_pin"]) +
        " -ScratchRoot " + _ps_literal(bundles["scratch"]) + " | Out-Null\n"
        "[Console]::Out.WriteLine(([string]$env:WD_REBOOT_EXPECTED_MANIFEST_HASH) + ' ' + "
        "([string]$env:WD_MIND_PUBLIC_ACTIVE_MANIFEST_SHA256) + '.')\n", encoding="ascii")
    env = dict(os.environ, WD_BRIDGE_BIN=str(bundles["bin"]), WD_REBOOT_EXPECTED_MANIFEST_HASH=bundles["active_pin"])
    env.pop("WD_MIND_PUBLIC_ACTIVE_MANIFEST_SHA256", None)
    result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-File", str(probe)],
                            capture_output=True, text=True, timeout=90, env=env)
    assert bundles["marker"].read_text() == "x"
    first, last = result.stdout.strip().splitlines()  # the wrapper writes its result to the console, then the probe line
    assert json.loads(first)["status"] == "WRITTEN"
    assert last == bundles["active_pin"] + " ."
