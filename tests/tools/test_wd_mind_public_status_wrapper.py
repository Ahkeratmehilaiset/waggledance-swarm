"""Public-status wrapper is default-off before inspecting bundle or session."""
import json
import hashlib
import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "ops/windows/reboot/Invoke-WdMindPublicStatus.ps1"


def sha(data):
    return hashlib.sha256(data).hexdigest()


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
    active_pin = sha(active_raw)
    candidate = tmp_path / "candidate"
    marker = tmp_path / "called.txt"
    wrapper = ("param($Tool,[Parameter(ValueFromRemainingArguments=$true)][string[]]$Rest)\n"
        "if ($Tool -cne 'tools/wd_mind_public_event_writer_cli.py' -or $Rest[0] -cne '--enable' -or "
        f"$env:WD_MIND_PUBLIC_ACTIVE_MANIFEST_SHA256 -cne '{active_pin}') {{ throw 'mismatch' }}; "
        f"[IO.File]::AppendAllText('{str(marker).replace(chr(39), chr(39)*2)}','x'); "
        "$global:LASTEXITCODE=0; '{\"status\":\"WRITTEN\",\"delivery\":\"canonical\"}'\n").encode()
    files = {"Invoke-WdMindPublicStatus.ps1": SCRIPT.read_bytes(),
        "Invoke-WdBridgePython.ps1": wrapper, "BridgeCodeContext.ps1": b"synthetic-context",
        "bridge-code-files.json": b"{}", "wd-fleet.json": b"{}",
        "tools-bootstrap/tools/wd_mind_public_event_writer.py": b"synthetic-writer",
        "tools-bootstrap/tools/wd_mind_public_event_writer_cli.py": b"synthetic-cli",
        "tools-bootstrap/configs/mind/pilot_non_review_policy.json": b"synthetic-policy",
        "tools-bootstrap/configs/mind/pilot_public_status_policy.json": b"synthetic-public"}
    for name, data in files.items():
        path = candidate / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    candidate_raw = json.dumps({"files": {name: sha(data) for name, data in files.items()}}).encode()
    (candidate / "deployment-manifest.json").write_bytes(candidate_raw)
    return {"active": active, "bin": binary, "active_pin": active_pin, "candidate": candidate,
            "candidate_pin": sha(candidate_raw), "marker": marker}


def invoke(item, *, candidate_pin=None, active_pin=None):
    env = dict(os.environ, WD_BRIDGE_BIN=str(item["bin"]),
               WD_REBOOT_EXPECTED_MANIFEST_HASH=active_pin or item["active_pin"])
    env.pop("WD_MIND_PUBLIC_ACTIVE_MANIFEST_SHA256", None)
    return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-File", str(item["candidate"] / SCRIPT.name), "-Run",
        "-CandidateBundle", str(item["candidate"]), "-ExpectedCandidateManifestSha256", candidate_pin or item["candidate_pin"]],
        capture_output=True, text=True, timeout=30, env=env)


def test_default_off_without_any_pins():
    result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-File", str(SCRIPT)],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"status": "OFF"}


def test_separate_candidate_and_active_anchors_reach_fixed_entrypoint(bundles):
    assert bundles["active_pin"] != bundles["candidate_pin"]
    result = invoke(bundles)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {"status": "WRITTEN", "delivery": "canonical"}
    assert bundles["marker"].read_text() == "x"


def test_launcher_copy_outside_candidate_refuses_before_entrypoint(bundles, tmp_path):
    copied = tmp_path / "unbound-launcher.ps1"
    copied.write_bytes(SCRIPT.read_bytes())
    env = dict(os.environ, WD_BRIDGE_BIN=str(bundles["bin"]),
               WD_REBOOT_EXPECTED_MANIFEST_HASH=bundles["active_pin"])
    result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-File", str(copied), "-Run",
        "-CandidateBundle", str(bundles["candidate"]), "-ExpectedCandidateManifestSha256", bundles["candidate_pin"]],
        capture_output=True, text=True, timeout=30, env=env)
    assert result.returncode == 2 and json.loads(result.stdout) == {"status": "REFUSED"}
    assert not bundles["marker"].exists()


@pytest.mark.parametrize("fault", ["active_pin", "candidate_pin", "active_helper", "candidate_wrapper", "candidate_launcher", "candidate_code", "candidate_fleet"])
def test_wrong_or_changed_anchor_refuses_before_entrypoint(bundles, fault):
    kwargs = {}
    if fault == "active_pin": kwargs["active_pin"] = "0" * 64
    elif fault == "candidate_pin": kwargs["candidate_pin"] = "0" * 64
    else:
        target = {"active_helper": bundles["bin"] / "Write-AgentEvent.ps1",
                  "candidate_wrapper": bundles["candidate"] / "Invoke-WdBridgePython.ps1",
                  "candidate_launcher": bundles["candidate"] / SCRIPT.name,
                  "candidate_code": bundles["candidate"] / "tools-bootstrap/tools/wd_mind_public_event_writer.py",
                  "candidate_fleet": bundles["candidate"] / "wd-fleet.json"}[fault]
        target.write_bytes(target.read_bytes() + (b"\n#tamper\n" if fault == "candidate_launcher" else b"tamper"))
    result = invoke(bundles, **kwargs)
    assert result.returncode == 2 and json.loads(result.stdout) == {"status": "REFUSED"}
    assert not bundles["marker"].exists()


@pytest.mark.parametrize("outcome", ["unknown", "queued"])
def test_entered_outcome_is_not_retried_or_mistaken_for_canonical(bundles, outcome):
    marker = str(bundles["marker"]).replace("'", "''")
    if outcome == "unknown":
        payload, code = '{"status":"UNKNOWN"}', 3
    else:
        payload, code = '{"status":"QUEUED","retained_wal_sha256":"' + 'a' * 64 + '"}', 0
    script = ("param($Tool,[Parameter(ValueFromRemainingArguments=$true)][string[]]$Rest)\n"
              f"[IO.File]::AppendAllText('{marker}','x'); $global:LASTEXITCODE={code}; '{payload}'\n")
    wrapper = bundles["candidate"] / "Invoke-WdBridgePython.ps1"
    wrapper.write_text(script, encoding="ascii")
    manifest_path = bundles["candidate"] / "deployment-manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["files"]["Invoke-WdBridgePython.ps1"] = sha(wrapper.read_bytes())
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    bundles["candidate_pin"] = sha(manifest_path.read_bytes())
    result = invoke(bundles)
    assert bundles["marker"].read_text() == "x"
    if outcome == "unknown":
        assert result.returncode == 3 and json.loads(result.stdout) == {"status": "UNKNOWN"}
    else:
        assert result.returncode == 0 and json.loads(result.stdout) == {"status": "QUEUED", "retained_wal_sha256": "a" * 64}


def test_real_bridge_wrapper_with_synthetic_active_helpers_and_two_manifest_pins(tmp_path):
    """Real candidate wrapper and writer; active bridge helpers are synthetic and never append."""
    root = SCRIPT.parents[3]
    spec = importlib.util.spec_from_file_location("bridge_context_fixture", root / "tests/tools/test_wd_bridge_code_context.py")
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    if fixture.PWSH is None or not fixture.HAS_BRIDGE_PYTHON:
        pytest.skip("pinned bridge interpreter unavailable")
    entries = ("tools/wd_mind_public_event_writer.py", "tools/wd_mind_public_event_writer_cli.py")
    closure, _ = fixture._intra_repo_closure(entries)
    closure.update(("configs/mind/pilot_non_review_policy.json", "configs/mind/pilot_public_status_policy.json"))
    candidate = fixture._stage_fake_bundle(tmp_path / "candidate-root", tuple(sorted(closure)),
        {"mind_public_status": "tools/wd_mind_public_event_writer_cli.py"})
    fleet_raw = (root / "ops/windows/reboot/wd-fleet.json").read_bytes()
    (candidate / "wd-fleet.json").write_bytes(fleet_raw)
    manifest_path = candidate / "deployment-manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["files"]["wd-fleet.json"] = sha(fleet_raw).upper()
    (candidate / SCRIPT.name).write_bytes(SCRIPT.read_bytes())
    manifest["files"][SCRIPT.name] = sha((candidate / SCRIPT.name).read_bytes()).upper()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    candidate_pin = sha(manifest_path.read_bytes())
    active = tmp_path / "active"
    binary = active / "tools-bootstrap/.agent-bridge/bin"
    binary.mkdir(parents=True)
    guard = ("$b=Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)); "
             "$p=Join-Path $b 'deployment-manifest.json'; "
             "$s=[IO.File]::OpenRead($p); $h=[Security.Cryptography.SHA256]::Create(); "
             "try {$v=[BitConverter]::ToString($h.ComputeHash($s)).Replace('-','')} finally {$s.Dispose();$h.Dispose()}; "
             "if ($v -ine $env:WD_REBOOT_EXPECTED_MANIFEST_HASH) { exit 9 }; ")
    evidence = (guard + "@{pin_status='manifest_and_launcher_verified'; observed_agent='codex-lead-1'; "
                "native_conversation_id='synthetic-conversation'}|ConvertTo-Json -Compress\n").encode()
    fake_writer = ("param($Agent,$Type,$TaskId,$Status,$PayloadJson,[switch]$ReceiptJson)\n" + guard +
        "@{agent=$Agent; agent_uuid=$env:AGENT_BRIDGE_AGENT_UUID; type=$Type; task_id=$TaskId; status=$Status; "
        "to=''; message=''; paths=@(); write_scope=@(); payload=@{public_status=$true}; "
        "_bridge_delivery=@{schema='waggledance.bridge.delivery-receipt.v1'; accepted=$true; "
        "delivery_status='canonical'; canonical_durable=$true}} | ConvertTo-Json -Depth 5 -Compress\n").encode()
    active_files = {}
    for name, data in (("Get-BridgeExecutionEvidence.ps1", evidence), ("Write-AgentEvent.ps1", fake_writer)):
        (binary / name).write_bytes(data)
        active_files["tools-bootstrap/.agent-bridge/bin/" + name] = sha(data)
    registry = active / "tools-bootstrap/configs/bridge_identity_registry.json"
    registry.parent.mkdir(parents=True)
    registry.write_bytes(b"synthetic-registry")
    active_files["tools-bootstrap/configs/bridge_identity_registry.json"] = sha(registry.read_bytes())
    active_raw = json.dumps({"files": active_files}).encode()
    (active / "deployment-manifest.json").write_bytes(active_raw)
    fleet = json.loads(fleet_raw)
    lead = next(row for row in fleet["lanes"] if row["agent"] == "codex-lead-1")
    env = dict(os.environ, WD_BRIDGE_BIN=str(binary), WD_REBOOT_EXPECTED_MANIFEST_HASH=sha(active_raw),
        WD_BRIDGE_PYTHON=fixture.BRIDGE_PYTHON, AGENT_BRIDGE_AGENT="codex-lead-1",
        AGENT_BRIDGE_AGENT_UUID=lead["agent_uuid"], AGENT_BRIDGE_SESSION_ID="wd-reboot-synthetic",
        AGENT_BRIDGE_RUN_ID="wd-reboot-synthetic")
    env.pop("WD_MIND_PUBLIC_ACTIVE_MANIFEST_SHA256", None)
    result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-File", str(candidate / SCRIPT.name), "-Run",
        "-CandidateBundle", str(candidate), "-ExpectedCandidateManifestSha256", candidate_pin],
        capture_output=True, text=True, timeout=60, env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {"status": "WRITTEN", "delivery": "canonical"}
