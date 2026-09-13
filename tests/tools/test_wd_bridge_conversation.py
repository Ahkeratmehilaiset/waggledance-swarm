"""The Windows conversation window is a bounded, byte-inert observation tool."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "ops/windows/reboot/Show-WdBridgeConversation.ps1"
READER = ROOT / ".agent-bridge/bin/BridgeIncrementalReader.ps1"
SHELLS = sorted({p for p in (shutil.which("powershell.exe"), shutil.which("pwsh")) if p})
pytestmark = pytest.mark.skipif(not SHELLS, reason="PowerShell required")


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def event(message="ordinary discussion", **fields):
    return dict(ts_utc="2026-09-13T12:00:00Z", agent="codex-lead-1",
                type="message", status="progress", message=message) | fields


@pytest.fixture(params=SHELLS or [None])
def runtime(request):
    audit = ROOT / ".codex-audit"
    audit.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="bridge-conversation-", dir=audit) as directory:
        root = Path(directory)
        (root / "shared").mkdir()
        yield root, request.param


def write_rows(runtime, rows):
    path = runtime[0] / "shared/events.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def run_view(runtime, *, iterations=1, tail=40, mutation="", extra=""):
    command = r"""
$global:polls = 0
function Write-Host {
    param($Object, $ForegroundColor, [switch]$NoNewline)
    [Console]::WriteLine((@{text=[string]$Object; color=[string]$ForegroundColor} | ConvertTo-Json -Compress))
}
function Start-Sleep { param($Milliseconds) $global:polls++; MUTATION }
function Start-Process { throw 'viewer attempted process start' }
function Set-Content { throw 'viewer attempted write' }
function Write-BridgeIncrementalState { throw 'viewer attempted cursor write' }
& SCRIPT -RuntimeRoot RUNTIME -ReaderPath READER -MaxIterations ITERATIONS -InitialTail TAIL -PollIntervalMs 100 EXTRA
""".replace("MUTATION", mutation).replace("SCRIPT", quote(SCRIPT)).replace("RUNTIME", quote(runtime[0])).replace("READER", quote(READER)).replace("ITERATIONS", str(iterations)).replace("TAIL", str(tail)).replace("EXTRA", extra)
    result = subprocess.run([runtime[1], "-NoProfile", "-NonInteractive", "-Command", command],
                            cwd=ROOT, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stderr
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def test_sender_colors_severity_labels_and_byte_inert_read(runtime):
    rows = [event(agent=agent) for agent in ["codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5"]]
    rows += [event("error detail", severity="error"), event("warning detail", severity="warning")]
    path = write_rows(runtime, rows)
    sentinel = runtime[0] / "cursor.json"
    sentinel.write_bytes(b"unchanged")
    before = {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in runtime[0].rglob("*") if p.is_file()}
    output = run_view(runtime)
    discussion = [item for item in output if "ordinary discussion" in item["text"]]
    assert [item["color"] for item in discussion] == ["Cyan", "Green", "Yellow", "Magenta", "Blue"]
    assert all("message/progress" in item["text"] for item in discussion)
    assert next(item for item in output if "error detail" in item["text"])["color"] == "Red"
    assert next(item for item in output if "warning detail" in item["text"])["color"] == "Yellow"
    assert path.exists()
    assert before == {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in runtime[0].rglob("*") if p.is_file()}


def test_infrastructure_suppressed_and_replay_bounded(runtime):
    write_rows(runtime, [event("old context")] + [event("noise", type=kind) for kind in
               ["heartbeat", "liveness", "wake_request"]] +
               [event("ack noise", status=status) for status in ["received", "seen", "acknowledged", "wake_ack"]] +
               [event("new substantive")])
    output = run_view(runtime, tail=8)
    text = "\n".join(item["text"] for item in output)
    assert "new substantive" in text
    assert "old context" not in text
    assert "noise" not in text


def test_delta_retains_partial_line_and_does_not_replay_prior_rows(runtime):
    path = write_rows(runtime, [event("initial row")])
    line = json.dumps(event("completed partial"))
    with path.open("a", encoding="utf-8") as stream:
        stream.write(line[:-1])
    mutation = "if ($global:polls -eq 1) { [IO.File]::AppendAllText(" + quote(path) + ", " + quote("}\n") + ") }"
    output = run_view(runtime, iterations=3, mutation=mutation)
    text = "\n".join(item["text"] for item in output)
    assert text.count("initial row") == 1
    assert text.count("completed partial") == 1
    assert "partial" in text.lower()


@pytest.mark.parametrize("change", ["rotation", "truncation"])
def test_discontinuity_visible_and_fresh_bounded_context_replayed(runtime, change):
    path = write_rows(runtime, [event("initial long row " + "x" * 400)])
    new_row = json.dumps(event("after change")) + "\n"
    rename = "[IO.File]::Move(" + quote(path) + ", " + quote(path.with_suffix(".old")) + "); " if change == "rotation" else ""
    mutation = "if ($global:polls -eq 1) { " + rename + "[IO.File]::WriteAllText(" + quote(path) + ", " + quote(new_row) + ") }"
    output = run_view(runtime, iterations=3, mutation=mutation)
    text = "\n".join(item["text"] for item in output)
    assert "history gap possible" in text
    assert text.count("after change") == 1


def test_missing_and_malformed_logs_visible_without_runtime_creation(runtime):
    output = run_view(runtime)
    assert any("log_missing" in item["text"] for item in output)
    assert list((runtime[0] / "shared").iterdir()) == []
    path = runtime[0] / "shared/events.jsonl"
    path.write_bytes(b"ordinary invalid record\n")
    output = run_view(runtime)
    assert any("invalid_json" in item["text"] and item["color"] == "Red" for item in output)
    assert path.read_bytes() == b"ordinary invalid record\n"


def test_plain_text_controls_and_truncation_remain_one_labeled_line(runtime):
    write_rows(runtime, [event("line one\nline two\t" + "x" * 1300)])
    output = run_view(runtime)
    message = next(item["text"] for item in output if "line one" in item["text"])
    assert "\n" not in message and "\t" not in message
    assert "[truncated]" in message
    assert len(message) < 1600


def test_zero_initial_tail_shows_only_new_rows(runtime):
    path = write_rows(runtime, [event("existing row")])
    new_row = json.dumps(event("new row")) + "\n"
    mutation = "if ($global:polls -eq 1) { [IO.File]::AppendAllText(" + quote(path) + ", " + quote(new_row) + ") }"
    output = run_view(runtime, tail=0, iterations=2, mutation=mutation)
    text = "\n".join(item["text"] for item in output)
    assert "existing row" not in text
    assert text.count("new row") == 1


def test_existing_runtime_mutex_exits_without_entering_reader(runtime):
    # A separate holder process owns the same session-local mutex while the
    # viewer runs in its child process. No persisted lock/checkpoint is needed.
    digest = hashlib.sha256(str(runtime[0].resolve()).rstrip("\\/").lower().encode()).hexdigest().upper()
    mutex_name = "Local\\WD-BridgeConversation-" + digest
    command = "$mutex = New-Object Threading.Mutex($false, " + quote(mutex_name) + "); "
    command += "[void]$mutex.WaitOne(0); try { & " + quote(runtime[1])
    command += " -NoProfile -NonInteractive -File " + quote(SCRIPT)
    command += " -RuntimeRoot " + quote(runtime[0])
    # Invalid reader would fail if duplicate suppression reached the reader.
    command += " -ReaderPath " + quote(runtime[0] / "missing-reader.ps1")
    command += " -MaxIterations 1 } finally { $mutex.ReleaseMutex(); $mutex.Dispose() }"
    result = subprocess.run([runtime[1], "-NoProfile", "-NonInteractive", "-Command", command],
                            cwd=ROOT, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stderr
    assert "already open for this runtime" in result.stdout
    assert list((runtime[0] / "shared").iterdir()) == []
