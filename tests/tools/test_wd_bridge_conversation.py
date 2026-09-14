"""The Windows conversation window is a bounded, byte-inert observation tool."""
import hashlib
import json
from pathlib import Path
import re
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


def summary(output):
    summaries = [item["text"] for item in output if "rows=" in item["text"]]
    assert len(summaries) == 1, "plain output should emit one final session summary"
    text = summaries[0]
    match = re.search(
        r"rows=(\d+) visible=(\d+) hidden: hb=(\d+) wake=(\d+) ack=(\d+) filter=(\d+) skipped=(\d+)",
        text,
    )
    assert match, text
    counts = dict(zip(["rows", "visible", "hb", "wake", "ack", "filter", "skipped"],
                      map(int, match.groups())))
    assert "READ-ONLY" in text
    assert "authority" in text.lower()
    return counts, text


def test_all_heartbeat_context_reports_hidden_counts_without_idle_spam(runtime):
    write_rows(runtime, [event("hidden liveness", type="heartbeat") for _ in range(4)])
    output = run_view(runtime, iterations=5)
    counts, text = summary(output)
    assert counts == dict(rows=4, visible=0, hb=4, wake=0, ack=0, filter=0, skipped=0)
    assert "lag=0B" in text
    assert not any("hidden liveness" in item["text"] for item in output)
    assert len(output) <= 4, "unchanged polls should not flood plain output"


def test_plain_text_counts_rows_not_unique_work_and_separates_exclusions(runtime):
    work = event("repeated visible discussion")
    write_rows(runtime, [event(type="heartbeat"), event(type="wake_request"),
                        event(status="received"), work, work,
                        event("unknown future discussion", type="future_kind")])
    output = run_view(runtime, extra="-PlainText")
    counts, text = summary(output)
    assert counts == dict(rows=6, visible=3, hb=1, wake=1, ack=1, filter=0, skipped=0)
    assert sum("repeated visible discussion" in item["text"] for item in output) == 2
    assert "lag=0B" in text


def test_agent_and_type_filters_match_exact_fields_and_disclose_hidden_rows(runtime):
    write_rows(runtime, [event("selected discussion"),
                        event("other sender", agent="codex-lead-10"),
                        event("other type", type="message_extra"),
                        event("other lane", agent="codex-tools-1"),
                        event("heartbeat noise", type="heartbeat")])
    output = run_view(runtime, extra="-PlainText -AgentFilter codex-lead-1 -TypeFilter message")
    counts, _ = summary(output)
    assert counts == dict(rows=5, visible=1, hb=1, wake=0, ack=0, filter=3, skipped=0)
    text = "\n".join(item["text"] for item in output)
    assert "selected discussion" in text
    assert all(hidden not in text for hidden in ["other sender", "other type", "other lane", "heartbeat noise"])


def test_zero_tail_counts_only_selected_skipped_context_and_new_rows(runtime):
    path = write_rows(runtime, [event("skipped existing discussion")])
    new_row = json.dumps(event("new visible discussion")) + "\n"
    mutation = "if ($global:polls -eq 1) { [IO.File]::AppendAllText(" + quote(path) + ", " + quote(new_row) + ") }"
    output = run_view(runtime, tail=0, iterations=3, mutation=mutation, extra="-PlainText")
    counts, text = summary(output)
    assert counts == dict(rows=2, visible=1, hb=0, wake=0, ack=0, filter=0, skipped=1)
    assert "lag=0B" in text
    assert not any("skipped existing discussion" in item["text"] for item in output)


def test_delta_row_cap_exposes_exact_remaining_byte_lag(runtime):
    path = write_rows(runtime, [event("initial context")])
    lines = [json.dumps(event("delta discussion " + str(index))) + "\n" for index in range(205)]
    delta_path = runtime[0] / "supplied-delta.jsonl"
    delta_path.write_text("".join(lines), encoding="utf-8", newline="")
    mutation = ("if ($global:polls -eq 1) { [IO.File]::AppendAllText(" + quote(path) +
                ", [IO.File]::ReadAllText(" + quote(delta_path) + ")) }")
    output = run_view(runtime, tail=0, iterations=2, mutation=mutation, extra="-PlainText")
    counts, text = summary(output)
    assert counts == dict(rows=201, visible=200, hb=0, wake=0, ack=0, filter=0, skipped=1)
    assert "lag=" + str(len("".join(lines[200:]).encode("utf-8"))) + "B" in text
    assert sum("delta discussion" in item["text"] for item in output) == 200


def test_missing_source_reports_unknown_lag_not_zero(runtime):
    output = run_view(runtime, iterations=3, extra="-PlainText")
    counts, text = summary(output)
    assert counts == dict(rows=0, visible=0, hb=0, wake=0, ack=0, filter=0, skipped=0)
    assert "lag=unknown" in text
    assert list((runtime[0] / "shared").iterdir()) == []


def test_role_and_status_only_severity_have_textual_badges(runtime):
    agents = ["codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5", "observer-1"]
    write_rows(runtime, [event("role badge", agent=agent) for agent in agents] +
               [event("status-only failure", status="failed"), event("status-only warning", status="warn")])
    output = run_view(runtime, extra="-PlainText")
    role_lines = [item["text"] for item in output if "role badge" in item["text"]]
    for line, agent, badge in zip(role_lines, agents, ["LEAD", "TOOLS", "RCO1", "RCO2", "FABLE", "OTHER"]):
        assert "[" + agent + "]" in line
        assert "[" + badge + "]" in line
        assert "[message/progress]" in line
    assert len(role_lines) == 6
    failure = next(item for item in output if "status-only failure" in item["text"])
    warning = next(item for item in output if "status-only warning" in item["text"])
    assert "[ERROR]" in failure["text"] and failure["color"] == "Red"
    assert "[WARN]" in warning["text"] and warning["color"] == "Yellow"


def run_view_functions(runtime, body):
    command = "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)\n"
    command += ". " + quote(SCRIPT) + " -RuntimeRoot " + quote(runtime[0]) + " -PlainText\n" + body
    before = {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in runtime[0].rglob("*") if p.is_file()}
    result = subprocess.run([runtime[1], "-NoProfile", "-NonInteractive", "-Command", command],
                            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=45)
    assert result.returncode == 0, result.stderr
    assert before == {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in runtime[0].rglob("*") if p.is_file()}
    return json.loads(result.stdout)


def test_pause_defers_initial_read_and_preserves_cursor_until_resume_and_quit(runtime):
    report = run_view_functions(runtime, r"""
function Write-Host { param($Object, $ForegroundColor) }
$global:view = New-WdConversationView
$global:view.Interactive = $false
$global:view.Controls = $true
$global:poll = 0
$global:keyPoll = -1
$global:keys = @('P', '', 'P', 'P', '', 'P', 'Q')
$global:reads = [Collections.Generic.List[object]]::new()
$global:frames = [Collections.Generic.List[object]]::new()
$global:firstCursor = [pscustomobject]@{offset=100; file_identity='ordinary-fixture'; generation=$null}
function New-WdConversationView { return $global:view }
function Get-WdConversationKey {
    if ($global:keyPoll -eq $global:poll) { return '' }
    $global:keyPoll = $global:poll
    return $global:keys[$global:poll]
}
function Start-Sleep { param($Milliseconds) $global:poll++ }
function Read-BridgeEventTail {
    param($Path, $MaxLines, $MaxBytes)
    $global:reads.Add(@{kind='tail'; poll=$global:poll; max_rows=$MaxLines; max_bytes=$MaxBytes})
    return [pscustomobject]@{status='OK'; reason='rows_available'; rows=@([pscustomobject]@{type='message';message='first'});
        candidate_cursor=$global:firstCursor; snapshot_length=150}
}
function Read-BridgeEventDelta {
    param($Path, $Cursor, $MaxRows, $MaxBytes)
    $global:reads.Add(@{kind='delta'; poll=$global:poll; offset=$Cursor.offset;
        identity=$Cursor.file_identity; same_cursor=[object]::ReferenceEquals($Cursor, $global:firstCursor);
        max_rows=$MaxRows; max_bytes=$MaxBytes})
    return [pscustomobject]@{status='OK'; reason='rows_available'; rows=@([pscustomobject]@{type='message';message='second'});
        candidate_cursor=[pscustomobject]@{offset=150;file_identity='ordinary-fixture';generation=$null}; snapshot_length=150}
}
function Show-WdConversationFrame {
    param($View)
    $global:frames.Add(@{poll=$global:poll; paused=$View.Paused; rows=$View.Counts.rows;
        summary=(Get-WdConversationSummary $View)})
}
Invoke-WdConversationLoop -EventsPath 'unused-ordinary-fixture' -Tail 40 -PollMs 100 -Iterations 10
@{reads=@($global:reads.ToArray()); frames=@($global:frames.ToArray()); quit=$global:view.Quit;
    rows=$global:view.Counts.rows; visible=$global:view.Counts.visible} | ConvertTo-Json -Depth 6 -Compress
""")
    assert [(read["kind"], read["poll"]) for read in report["reads"]] == [("tail", 2), ("delta", 5)]
    delta = report["reads"][1]
    assert delta["same_cursor"] is True
    assert delta["offset"] == 100 and delta["identity"] == "ordinary-fixture"
    assert delta["max_rows"] == 200 and delta["max_bytes"] == 4194304
    assert [frame["rows"] for frame in report["frames"]] == [0, 0, 1, 1, 1, 2]
    assert all("lag=unknown" in frame["summary"] for frame in report["frames"] if frame["paused"])
    assert report["quit"] is True and report["rows"] == report["visible"] == 2


def test_control_cycles_clear_old_display_preserve_counts_and_bound_entries(runtime):
    report = run_view_functions(runtime, r"""
$view = New-WdConversationView
$view.Interactive = $true
$view.Counts.rows = 12
$view.Counts.visible = 9
for ($i=0; $i -lt 205; $i++) { Add-WdConversationLine $view ('line-' + $i) 'Green' }
$bounded = @{count=$view.Entries.Count; first=$view.Entries[0].Text; last=$view.Entries[199].Text}
Update-WdConversationControl $view 'A'
$agentFirst = $view.Agent
$oldDisplayCleared = @($view.Entries | Where-Object { $_.Text -like 'line-*' }).Count -eq 0
for ($i=0; $i -lt 5; $i++) { Update-WdConversationControl $view 'A' }
Update-WdConversationControl $view 'T'
$typeFirst = $view.Kind
for ($i=0; $i -lt 7; $i++) { Update-WdConversationControl $view 'T' }
Update-WdConversationControl $view 'P'
$paused = $view.Paused
Update-WdConversationControl $view 'Spacebar'
Update-WdConversationControl $view 'Q'
@{bounded=$bounded; agent_first=$agentFirst; type_first=$typeFirst; agent_all=$view.Agent; type_all=$view.Kind;
    old_display_cleared=$oldDisplayCleared; rows=$view.Counts.rows; visible=$view.Counts.visible;
    paused=$paused; resumed=(-not $view.Paused); quit=$view.Quit} | ConvertTo-Json -Depth 5 -Compress
""")
    assert report["bounded"] == dict(count=200, first="line-5", last="line-204")
    assert report["agent_first"] == "codex-lead-1" and report["type_first"] == "message"
    assert report["agent_all"] == report["type_all"] == ""
    assert report["old_display_cleared"] is True
    assert report["rows"] == 12 and report["visible"] == 9
    assert report["paused"] and report["resumed"] and report["quit"]


def test_cell_renderer_obeys_host_cell_width_and_marks_clipping(runtime):
    report = run_view_functions(runtime, r"""
$results = [Collections.Generic.List[object]]::new()
$wide = [string][char]0x754C + 'abc'
$combined = 'e' + [string][char]0x0301 + 'abc'
foreach ($text in @('abcdef', 'cat', $wide, $combined)) {
    foreach ($width in @(0, 1, 2, 5)) {
        $rendered = ConvertTo-WdConversationCellText $text $width
        $results.Add(@{width=$width; rendered=$rendered; cells=$Host.UI.RawUI.LengthInBufferCells($rendered)})
    }
}
@{samples=@($results.ToArray()); clipped=(ConvertTo-WdConversationCellText 'abcdef' 3);
    padded=(ConvertTo-WdConversationCellText 'cat' 5)} | ConvertTo-Json -Depth 5 -Compress
""")
    assert report["clipped"] == "ab>"
    assert report["padded"] == "cat  "
    assert all(sample["cells"] <= sample["width"] for sample in report["samples"])
    assert all(sample["rendered"] == "" for sample in report["samples"] if sample["width"] == 0)


def test_unavailable_dashboard_falls_back_once_without_losing_buffered_text(runtime):
    report = run_view_functions(runtime, r"""
$global:printed = [Collections.Generic.List[string]]::new()
function Write-Host { param($Object, $ForegroundColor) $global:printed.Add([string]$Object) }
function Write-WdConversationScreenLine { param($Y, $Text, $Color, $Width) throw 'ordinary unavailable console' }
$view = New-WdConversationView
$view.Interactive = $true
$view.Controls = $true
$view.Paused = $true
Add-WdConversationLine $view 'first buffered discussion' 'Cyan'
Add-WdConversationLine $view 'second buffered discussion' 'Green'
Show-WdConversationFrame $view
Show-WdConversationFrame $view
@{printed=@($global:printed.ToArray()); interactive=$view.Interactive; controls=$view.Controls;
    paused=$view.Paused; remaining=$view.Entries.Count} | ConvertTo-Json -Depth 4 -Compress
""")
    assert report["printed"].count("first buffered discussion") == 1
    assert report["printed"].count("second buffered discussion") == 1
    assert sum("Interactive display unavailable" in text for text in report["printed"]) == 1
    assert report["interactive"] is report["controls"] is report["paused"] is False
    assert report["remaining"] == 0


def test_compact_dashboard_text_retains_message_and_warning_badges_without_line_breaks(runtime):
    report = run_view_functions(runtime, r"""
$event = [pscustomobject]@{ts_utc='2026-09-13T12:00:00Z'; agent='codex-tools-1'; type='message'; status='warn';
    to='codex-lead-1'; task_id='ordinary-review'; message=("first line`nsecond line`t" + ('x' * 1300))}
Format-WdConversationEvent $event | ConvertTo-Json -Compress
""")
    assert report["Color"] == "Yellow"
    assert "[codex-tools-1]" in report["Text"]
    assert "[to:codex-lead-1]" in report["Text"] and "[task:ordinary-review]" in report["Text"]
    for text in [report["Text"], report["ScreenText"]]:
        assert "[TOOLS]" in text and "[WARN]" in text and "[message/warn]" in text
        assert "first line second line " in text
        assert "[truncated]" in text
        assert "\n" not in text and "\t" not in text
    assert len(report["ScreenText"]) < len(report["Text"])


def test_dashboard_geometry_offsets_and_resizes_redraw_without_idle_repaints(runtime):
    report = run_view_functions(runtime, r"""
$global:geometry = [pscustomobject]@{Width=79; Height=10; Left=20; Top=0}
$global:writes = [Collections.Generic.List[object]]::new()
$global:notices = [Collections.Generic.List[string]]::new()
function Get-WdConversationGeometry { return $global:geometry }
function Write-WdConversationScreenLine {
    param($X, $Y, $Text, $Color, $Width)
    $global:writes.Add(@{x=$X; y=$Y; text=$Text; color=$Color; width=$Width})
}
function Write-Host { param($Object, $ForegroundColor) $global:notices.Add([string]$Object) }
$view = New-WdConversationView
$view.Interactive = $true
$view.Controls = $true
Add-WdConversationLine $view 'ordinary visible discussion' 'Green'
Show-WdConversationFrame $view
$first = @($global:writes.ToArray())
$global:writes.Clear()
Show-WdConversationFrame $view
$idleWrites = $global:writes.Count
$global:geometry.Left = 30
Show-WdConversationFrame $view
$scrolled = @($global:writes.ToArray())
$global:writes.Clear()
$global:geometry.Width = 59
$global:geometry.Height = 8
$global:geometry.Top = 3
Show-WdConversationFrame $view
$resized = @($global:writes.ToArray())
$global:writes.Clear()
Show-WdConversationFrame $view
@{first=$first; idle_writes=$idleWrites; scrolled=$scrolled; resized=$resized;
    resized_idle_writes=$global:writes.Count; interactive=$view.Interactive;
    notices=@($global:notices.ToArray())} | ConvertTo-Json -Depth 6 -Compress
""")
    assert report["interactive"] is True, report["notices"]
    assert len(report["first"]) == 10
    assert all(line["x"] == 20 and line["width"] == 79 for line in report["first"])
    assert [line["y"] for line in report["first"]] == list(range(10))
    assert report["idle_writes"] == 0
    assert len(report["scrolled"]) == 10
    assert all(line["x"] == 30 and line["width"] == 79 for line in report["scrolled"])
    assert [line["text"] for line in report["scrolled"]] == [line["text"] for line in report["first"]]
    assert len(report["resized"]) == 8
    assert all(line["x"] == 30 and line["width"] == 59 for line in report["resized"])
    assert [line["y"] for line in report["resized"]] == list(range(3, 11))
    assert report["resized_idle_writes"] == 0
    assert report["notices"] == []
