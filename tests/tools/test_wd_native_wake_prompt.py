"""Pure, pinned native-wake prompt construction; never touches a live queue."""

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
REBOOT = ROOT / "ops" / "windows" / "reboot"
HELPER = REBOOT / "Get-WdNativeWakePrompt.ps1"
DELIVERY = "a" * 32
SHELLS = list(dict.fromkeys(filter(None, [
    shutil.which("pwsh") or shutil.which("powershell"),
    str(Path("C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"))
    if sys.platform == "win32" and Path("C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe").is_file()
    else None,
])))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def warning_text(path: Path) -> str:
    """Keep the warning stream as evidence, separate from success-stream JSON."""
    raw = path.read_bytes()
    return raw.decode("utf-16" if raw.startswith(b"\xff\xfe") else "utf-8-sig")


def bundle_fixture(tmp_path: Path) -> tuple[Path, str]:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    files = {}
    for name in ("WAKE_PROCEDURE_LEAD.md", "WAKE_PROCEDURE_TOOLS.md"):
        shutil.copyfile(REBOOT / name, bundle / name)
        files[name] = digest(bundle / name)
    manifest = bundle / "deployment-manifest.json"
    manifest.write_text(json.dumps({"schema_version": 1, "files": files}), encoding="utf-8")
    return bundle, digest(manifest)


def relay_bundle_setup(tmp_path: Path) -> str:
    """Real prompt code in a decoy manifest; no inherited production bundle."""
    from test_wd_startup_recovery import q
    bundle, _ = bundle_fixture(tmp_path)
    shutil.copyfile(HELPER, bundle / HELPER.name)
    manifest = bundle / "deployment-manifest.json"
    data = json.loads(manifest.read_text())
    data["files"][HELPER.name] = digest(bundle / HELPER.name)
    manifest.write_text(json.dumps(data))
    return (f"$env:WD_BRIDGE_PYTHON_WRAPPER={q(bundle / 'Invoke-WdBridgePython.ps1')}\n"
            f"$env:WD_REBOOT_EXPECTED_MANIFEST_HASH='{digest(manifest)}'\n"
            "$env:WD_BRIDGE_BIN=''\n")


def run_helper(ps: str, bundle: Path, anchor: str, *, agent: str = "codex-tools-1",
               delivery: str = DELIVERY) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [ps, "-NoProfile", "-NonInteractive", "-File", str(HELPER),
         "-Agent", agent, "-DeliveryId", delivery, "-BundleRoot", str(bundle),
         "-ExpectedManifestHash", anchor],
        cwd=ROOT, text=True, capture_output=True, check=False, timeout=30,
    )


@pytest.mark.parametrize("ps", SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("agent,name", [
    ("codex-lead-1", "WAKE_PROCEDURE_LEAD.md"),
    ("codex-tools-1", "WAKE_PROCEDURE_TOOLS.md"),
])
def test_exact_agent_and_delivery_get_only_pinned_absolute_procedure(tmp_path, ps, agent, name):
    bundle, anchor = bundle_fixture(tmp_path)
    before = {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in bundle.iterdir()}
    result = run_helper(ps, bundle, anchor, agent=agent)
    assert result.returncode == 0, result.stdout + result.stderr
    message = result.stdout.strip()
    assert message.startswith(f"Automatic bridge wake for {agent}; delivery_id={DELIVERY}.")
    assert str(bundle / name) in message
    assert digest(bundle / name) in message
    assert "Read and follow the verified procedure" in message
    assert "WAKE_PROCEDURE_TOOLS.md" not in message if agent == "codex-lead-1" else "WAKE_PROCEDURE_LEAD.md" not in message
    assert len(message.encode("utf-8")) < 512
    assert before == {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in bundle.iterdir()}


@pytest.mark.parametrize("ps", SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("agent,delivery", [
    ("other-agent", DELIVERY),
    ("CODEX-LEAD-1", DELIVERY),
    ("codex-tools-1", "abc"),
    ("codex-tools-1", "A" * 32),
    ("codex-tools-1", "a" * 31 + "/"),
])
def test_invalid_agent_or_delivery_refuses_without_prompt(tmp_path, ps, agent, delivery):
    bundle, anchor = bundle_fixture(tmp_path)
    result = run_helper(ps, bundle, anchor, agent=agent, delivery=delivery)
    assert result.returncode != 0
    assert not result.stdout.strip()


@pytest.mark.parametrize("ps", SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("damage", [
    "wrong_anchor", "missing_manifest", "missing_pin", "missing_procedure",
    "mutated_procedure", "oversized_procedure", "relative_root",
])
def test_missing_mutated_or_unsafe_bundle_refuses(tmp_path, ps, damage):
    bundle, anchor = bundle_fixture(tmp_path)
    manifest = bundle / "deployment-manifest.json"
    procedure = bundle / "WAKE_PROCEDURE_TOOLS.md"
    if damage == "wrong_anchor":
        anchor = "0" * 64
    elif damage == "missing_manifest":
        manifest.unlink()
    elif damage == "missing_pin":
        content = json.loads(manifest.read_text(encoding="utf-8"))
        del content["files"][procedure.name]
        manifest.write_text(json.dumps(content), encoding="utf-8")
        anchor = digest(manifest)
    elif damage == "missing_procedure":
        procedure.unlink()
    elif damage == "mutated_procedure":
        procedure.write_text(procedure.read_text(encoding="utf-8") + "\nchanged", encoding="utf-8")
    elif damage == "oversized_procedure":
        procedure.write_bytes(b"x" * 65537)
    elif damage == "relative_root":
        bundle = Path("relative-bundle")
    result = run_helper(ps, bundle, anchor)
    assert result.returncode != 0
    assert not result.stdout.strip()


def test_procedures_retain_control_and_reply_instructions():
    tools = (REBOOT / "WAKE_PROCEDURE_TOOLS.md").read_text(encoding="utf-8")
    lead = (REBOOT / "WAKE_PROCEDURE_LEAD.md").read_text(encoding="utf-8")
    assert "TRUNCATED ROUTING SUMMARY" in tools
    assert "Start-BridgeRequestTurn.ps1" in tools
    assert "Write-BridgeTaskReply.ps1" in tools
    assert "Preserve explicit task HOLDs, cancellations and peer write scopes." in tools
    assert "Get-BridgeReplySnapshot.ps1" in lead
    assert "Record-BridgeReplyObservation.ps1" in lead
    assert "user_reported" in lead
    assert "HOLDs, cancellations and peer write scopes" in lead
    assert "Queue acceptance is not task completion." in tools
    assert "Queue acceptance is not task completion." in lead
    for sentence in (
        "Before ending this turn, reconcile unfinished operator-authorized work, idle coder lanes and unprocessed results.",
        "Advance a file-disjoint eligible slice or record the specific dependency, owner and absolute deadline; a status report is not task completion.",
        "Do not serialize unrelated coding behind reviews.",
        "A diagnostic question does not itself cancel an existing implementation assignment.",
    ):
        assert sentence in lead


def test_lead_procedure_discovers_requests_without_tail_or_session_cutoff():
    lead = (REBOOT / "WAKE_PROCEDURE_LEAD.md").read_text(encoding="utf-8")
    assert "Get-BridgeRequestInventory.ps1 -Agent codex-lead-1" in lead
    assert "Do not add -SessionId" in lead
    assert "Discovery is not answer status" in lead
    assert "HOLD/cancel/finding controls are not enumerated" in lead
    assert "Do not bulk-resubmit historical requests" in lead
    assert "Never discard an unprocessed reply using a timestamp high-water mark" in lead
    assert "Reconcile by exact request ID and bound reply" in lead


def test_every_inline_instruction_fragment_is_preserved_in_procedure():
    source = (REBOOT / "start-wd-tools-consumer.ps1").read_text(encoding="utf-8")
    inline = source.split("function Get-WdInlineNativeWakeMessage {", 1)[1].split(
        "function Get-WdVerifiedNativeWakeMessage {", 1)[0]
    tools_part, lead_part = inline.split("if ($Agent -ceq 'codex-lead-1')", 1)
    for part, name in ((tools_part, "TOOLS"), (lead_part, "LEAD")):
        procedure = " ".join((REBOOT / f"WAKE_PROCEDURE_{name}.md").read_text().split())
        for literal in re.findall(r"'([^']*)'", part):
            # Instruction literals, not identity validation or the dynamic prefix.
            if len(literal) > 65:
                assert " ".join(literal.split()) in procedure


def test_lead_discovery_is_incremental_without_losing_late_replies():
    lead = (REBOOT / "WAKE_PROCEDURE_LEAD.md").read_text(encoding="utf-8")
    for instruction in (
        "At startup or after loss of the durable discovery checkpoint",
        "On an ordinary wake, inspect page 1",
        "Stop paging at a page whose IDs are all in that checkpoint",
        "Never infer that a known request is answered from its inventory position",
        "Independently reconcile every known outstanding request ID",
        "every exact request ID referenced by recent canonical replies",
        "at most one bounded discovery attempt per turn",
        "unknown coverage",
    ):
        assert instruction in lead
    assert "Follow every next_cursor" not in lead


@pytest.mark.skipif(sys.platform != "win32", reason="Windows hidden file attributes")
@pytest.mark.parametrize("ps", SHELLS, ids=lambda p: Path(p).stem)
def test_hidden_ancestor_and_files_are_not_reparse_points(tmp_path, ps):
    bundle, anchor = bundle_fixture(tmp_path)
    paths = [tmp_path, bundle / "deployment-manifest.json", bundle / "WAKE_PROCEDURE_TOOLS.md"]
    try:
        for path in paths:
            subprocess.run(["attrib", "+h", str(path)], check=True, capture_output=True)
        result = run_helper(ps, bundle, anchor)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "WAKE_PROCEDURE_TOOLS.md" in result.stdout
    finally:
        for path in paths:
            subprocess.run(["attrib", "-h", str(path)], check=True, capture_output=True)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows drive-relative path contract")
@pytest.mark.parametrize("ps", SHELLS, ids=lambda p: Path(p).stem)
def test_root_relative_bundle_does_not_inherit_current_drive(tmp_path, ps):
    bundle, anchor = bundle_fixture(tmp_path)
    result = run_helper(ps, Path(str(bundle)[2:]), anchor)
    assert result.returncode != 0
    assert not result.stdout.strip()


@pytest.mark.parametrize("ps", SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("case", ["valid", "helper_tampered", "wrong_anchor", "procedure_tampered"])
def test_relay_uses_pinned_prompt_or_alerted_verified_fallback(tmp_path, ps, case):
    from test_wd_startup_recovery import load, q
    from test_wd_reboot_bundle import _run_powershell

    bundle, anchor = bundle_fixture(tmp_path)
    shutil.copyfile(HELPER, bundle / HELPER.name)
    manifest = bundle / "deployment-manifest.json"
    data = json.loads(manifest.read_text())
    data["files"][HELPER.name] = digest(bundle / HELPER.name)
    manifest.write_text(json.dumps(data))
    anchor = digest(manifest)
    marker = tmp_path / "untrusted-ran"
    if case == "helper_tampered":
        (bundle / HELPER.name).write_text(f"[IO.File]::WriteAllText({q(marker)}, 'bad')")
    elif case == "wrong_anchor":
        anchor = "0" * 64
    elif case == "procedure_tampered":
        (bundle / "WAKE_PROCEDURE_LEAD.md").write_text("bad")
    wake = tmp_path / "wake"
    wake.write_text("pending")
    state = tmp_path / "state.json"
    warnings = tmp_path / "warnings.log"
    consumer = REBOOT / "start-wd-tools-consumer.ps1"
    script = "$ErrorActionPreference='Stop'\n$WarningPreference='Stop'\nSet-StrictMode -Version Latest\n"
    for name in ("Assert-WdTurnPath", "Write-WdTurnJson", "Move-WdWakeSnapshot"):
        script += load(REBOOT / "Invoke-WdLaneTurnLoop.ps1", name)
    script += load(consumer, "Get-WdVerifiedNativeWakeMessage")
    script += load(consumer, "Get-WdInlineNativeWakeMessage")
    script += load(consumer, "Invoke-WdNativeToolsWakeStep")
    script += f"""
$env:WD_BRIDGE_BIN=''
$env:WD_BRIDGE_PYTHON_WRAPPER={q(bundle / 'Invoke-WdBridgePython.ps1')}
$env:WD_REBOOT_EXPECTED_MANIFEST_HASH='{anchor}'
$script:sent=''
$script:alerts=0
$script:sends=0
function Invoke-WdContinuityOperatorNotice {{ $script:alerts++; return @{{status='published'}} }}
function Send-WdNativeToolsQueueMessage {{
 param($CliPath,$ThreadId,$Message,$Worktree)
 $script:sent=$Message
 $script:sends++
 return 'test-queue-id'
}}
try {{
 $result=Invoke-WdNativeToolsWakeStep -CliPath unused -ThreadId test-thread -Worktree {q(tmp_path)} -WakePath {q(wake)} -StatePath {q(state)} -Generation test -NativePid 1 -Agent codex-lead-1 -SessionId fixture-session 3>>{q(warnings)}
 if ('{case}' -ne 'valid') {{
  foreach ($attempt in 1..2) {{
   $saved=Get-Content -LiteralPath {q(state)} -Raw | ConvertFrom-Json
   $saved.updated_at_utc='2026-01-01T00:00:00Z'
   Write-WdTurnJson {q(state)} $saved
   # Only the woken conversation's exact receipt for the queued delivery releases the next add.
   $receipts={q(tmp_path / 'shared' / 'telemetry')}
   [void][IO.Directory]::CreateDirectory($receipts)
   [IO.File]::WriteAllText((Join-Path $receipts ('stage-' + $attempt + '.json')),(@{{schema='wd.bridge-stage.v1';stage='model_turn_started';target='codex-lead-1';delivery_id=[string]$saved.delivery_id;observation_source='agent_reported'}}|ConvertTo-Json -Compress))
   [IO.File]::WriteAllText({q(wake)},'next event')
   $result=Invoke-WdNativeToolsWakeStep -CliPath unused -ThreadId test-thread -Worktree {q(tmp_path)} -WakePath {q(wake)} -StatePath {q(state)} -Generation test -NativePid 1 -Agent codex-lead-1 -SessionId fixture-session 3>>{q(warnings)}
  }}
 }}
 @{{ok=$true;sent=$script:sent;result=$result;alerts=$script:alerts;sends=$script:sends}} | ConvertTo-Json -Compress
}} catch {{ @{{ok=$false;sent=$script:sent;error=$_.Exception.Message}} | ConvertTo-Json -Compress }}
"""
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    assert result["ok"], result
    assert not marker.exists()
    assert result["sends"] == (1 if case == "valid" else 3)
    assert not wake.exists()
    assert not Path(str(state) + ".wake").exists()
    saved = json.loads(state.read_text(encoding="utf-8-sig"))
    assert saved["status"] == "queued"
    if case == "valid":
        assert str(bundle / "WAKE_PROCEDURE_LEAD.md") in result["sent"]
        assert "Get-BridgeReplySnapshot" not in result["sent"]
        assert saved["prompt_mode"] == "pinned_procedure"
        assert result["alerts"] == 0
        assert not warning_text(warnings).strip()
    else:
        assert "Get-BridgeReplySnapshot.ps1" in result["sent"]
        assert "WAKE_PROCEDURE_LEAD.md" not in result["sent"]
        assert saved["prompt_mode"] == "inline_degraded"
        assert result["alerts"] == 1
        assert warning_text(warnings).count(
            "Native wake compact procedure unavailable: delivered verified inline fallback; package repair required"
        ) == 1


@pytest.mark.skipif(sys.platform != "win32", reason="real canonical writer is Windows-only")
@pytest.mark.parametrize("ps", SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("agent", ["codex-lead-1", "codex-tools-1"])
@pytest.mark.parametrize("session", ["real-lane-session", "", "invalid session"])
def test_degraded_notice_preserves_real_reply_identity(tmp_path, ps, agent, session):
    import uuid
    from test_wd_startup_recovery import load, q
    from test_wd_reboot_bundle import _run_powershell
    from test_wd_native_tools_wake import notice_registry

    bundle = tmp_path / "bundle"
    bin_dir = bundle / "tools-bootstrap/.agent-bridge/bin"
    bin_dir.mkdir(parents=True)
    mutex = "Local\\PromptIdentity-" + uuid.uuid4().hex + "-"
    for helper in (REBOOT.parents[2] / ".agent-bridge/bin").glob("*.ps1"):
        (bin_dir / helper.name).write_text(
            helper.read_text(encoding="utf-8-sig").replace("Global\\WaggleDanceBridge", mutex),
            encoding="utf-8-sig",
        )
    registry = notice_registry(bundle)
    publisher = bundle / "Send-WdContinuityAlert.ps1"
    shutil.copyfile(REBOOT / publisher.name, publisher)
    manifest = bundle / "deployment-manifest.json"
    manifest.write_text(json.dumps({"files": {
        **registry, **{p.relative_to(bundle).as_posix(): digest(p)
                      for p in [publisher, *bin_dir.glob("*.ps1")]},
    }}))
    runtime = REBOOT.parents[2] / ".codex-audit" / ("pi-" + uuid.uuid4().hex[:12])
    shared = runtime / "shared"
    shared.mkdir(parents=True)
    identities = json.loads((bundle / "tools-bootstrap/configs/bridge_identity_registry.json").read_text())["identities"]
    last = shared / f"last_{agent}.json"
    last.write_text(json.dumps(dict(agent=agent, agent_uuid=identities[agent],
                                   session_id="real-lane-session", run_id="real-lane-session")))
    audit = tmp_path / ".codex-audit"
    audit.mkdir()
    (audit / "wd-current-state.json").write_text(json.dumps(dict(agent=agent, task_id="fixture/work")))
    wake = runtime / ("wake_" + agent)
    wake.write_text("pending")
    state = tmp_path / "state.json"
    warnings = tmp_path / "warnings.log"
    consumer = REBOOT / "start-wd-tools-consumer.ps1"
    script = "$ErrorActionPreference='Stop'\n$WarningPreference='Stop'\n"
    script += "Get-ChildItem Env: | Where-Object Name -Match '^(AGENT_BRIDGE_|WD_|CLAUDE_CODE_|GIT_)' | ForEach-Object { Remove-Item -LiteralPath ('Env:'+$_.Name) }\n"
    for name in ("Assert-WdTurnPath", "Write-WdTurnJson", "Move-WdWakeSnapshot"):
        script += load(REBOOT / "Invoke-WdLaneTurnLoop.ps1", name)
    for name in ("Get-WdInlineNativeWakeMessage", "Invoke-WdContinuityOperatorNotice", "Invoke-WdNativeToolsWakeStep"):
        script += load(consumer, name)
    script += f"""
$env:WD_BRIDGE_PYTHON_WRAPPER={q(bundle / 'Invoke-WdBridgePython.ps1')}
$env:WD_REBOOT_EXPECTED_MANIFEST_HASH='{digest(manifest)}'
function Get-WdVerifiedNativeWakeMessage {{ throw 'forced compact fault' }}
function Send-WdNativeToolsQueueMessage {{ return 'decoy-queue' }}
$step=Invoke-WdNativeToolsWakeStep -CliPath unused -ThreadId 01a0a07b-ca98-71e1-90cb-d588435a2d8d -Worktree {q(tmp_path)} -WakePath {q(wake)} -StatePath {q(state)} -Generation ('a'*40) -NativePid 1 -Agent {agent} -SessionId '{session}' 3>{q(warnings)}
$env:AGENT_BRIDGE_RUNTIME_ROOT={q(runtime)}
$request=& {q(bin_dir / 'Write-AgentEvent.ps1')} -Agent operator -Type wake_request -Status assigned -To {agent} -TaskId fixture/after-alert -SessionId operator-session -RunId operator-session -ReceiptJson
@{{step=$step;request=($request|ConvertFrom-Json)}} | ConvertTo-Json -Depth 16 -Compress
"""
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    assert result["step"] == "queued"
    warning = warning_text(warnings)
    assert warning.count(
        "Native wake compact procedure unavailable: delivered verified inline fallback; package repair required"
    ) == 1
    assert warning.count("Native wake prompt alert skipped: launcher session unavailable") == (
        0 if session == "real-lane-session" else 1
    )
    assert "Native wake prompt alert unavailable" not in warning
    identity = json.loads(last.read_text(encoding="utf-8-sig"))
    assert identity["session_id"] == "real-lane-session"
    assert identity["run_id"] == "real-lane-session"
    expected = result["request"]["expected_responders"][agent]
    assert expected["session_id"] == expected["run_id"] == "real-lane-session"
    events = [json.loads(line) for line in (shared / "events.jsonl").read_text(encoding="utf-8-sig").splitlines()]
    notices = [e for e in events if e["agent"] == agent]
    assert len(notices) == (1 if session == "real-lane-session" else 0)
    if notices:
        assert notices[0]["payload"]["reason"] == "native_wake_prompt_degraded"
