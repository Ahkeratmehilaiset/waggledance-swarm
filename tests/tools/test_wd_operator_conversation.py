"""UI-only conversation contracts; no CLI, bridge, or model invocation."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "ops/windows/reboot/Show-WdOperatorConversation.ps1"
SHELLS = list(dict.fromkeys(filter(None, [shutil.which("powershell.exe"), shutil.which("pwsh")])) )


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def run_ps(shell, body, **process_options):
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", *(["-STA"] if os.name == "nt" else []),
                             "-Command", "$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest; . " + quote(SCRIPT) + "\n" + body],
                            capture_output=True, text=True, timeout=30, cwd=ROOT, **process_options)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_operator_view_exists():
    assert SCRIPT.is_file(), "managed Lead needs an actual operator conversation UI"


@pytest.mark.skipif(os.name != "nt", reason="WinForms needs Windows")
@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("hidden_startup", [False, True])
@pytest.mark.parametrize("mode", ["visible", "hidden", "headless"])
def test_window_visibility_honors_view_mode_under_hidden_process_startup(shell, hidden_startup, mode):
    startup = subprocess.STARTUPINFO()
    if hidden_startup:
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
    record = run_ps(shell, """
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class WdWindowVisibilityProbe {
    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool IsWindowVisible(IntPtr window);
    [DllImport("kernel32.dll")]
    public static extern IntPtr GetConsoleWindow();
}
'@
$view=New-WdOperatorConversationView """ + {"visible": "", "hidden": "-Hidden", "headless": "-Headless"}[mode] + """
try {
    Update-WdOperatorConversationView -View $view
    $visible=$false; $managedVisible=$false; $handle=0L
    if ($null -ne $view.Form) {
        $handle=$view.Form.Handle.ToInt64()
        $visible=[WdWindowVisibilityProbe]::IsWindowVisible($view.Form.Handle)
        $managedVisible=$view.Form.Visible
    }
    [pscustomobject]@{
        visible=$visible; managed_visible=$managedVisible; handle=$handle
        has_form=($null -ne $view.Form); controls=$view.Controls.Count
        console_handle=[WdWindowVisibilityProbe]::GetConsoleWindow().ToInt64()
    } | ConvertTo-Json -Compress
} finally { Close-WdOperatorConversationView -View $view }
""", startupinfo=startup, creationflags=subprocess.CREATE_NO_WINDOW)
    assert record["visible"] is (mode == "visible"), record
    assert record["managed_visible"] is (mode == "visible"), record
    assert record["has_form"] is (mode != "headless"), record
    assert bool(record["handle"]) is (mode != "headless"), record
    assert bool(record["controls"]) is (mode != "headless"), record
    assert record["console_handle"] == 0, record


@pytest.mark.parametrize("shell", SHELLS)
def test_agent_labels_are_backend_supplied_without_pooled_context(shell):
    record = run_ps(shell, """
$labels=@()
foreach ($entry in @(@('codex-lead-1','gpt-5.6-sol / ultra'),@('codex-tools-1','gpt-5.6-terra / high'),@('claude-rco-1','sonnet / max'),@('claude-rco-2','sonnet / max'),@('fable-5','fable / max'))) {
  $view=New-WdOperatorConversationView -Headless -AgentLabel $entry[0] -ModelLabel $entry[1] -Title ($entry[0] + ' conversation')
  $labels += [pscustomobject]@{agent=$view.State.AgentLabel;model=$view.State.ModelLabel;title=$view.State.Title;scope=$view.State.ContextNotice}
}
$labels | ConvertTo-Json -Depth 4 -Compress
""")
    assert [row["agent"] for row in record] == ["codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5"]
    assert [row["model"] for row in record] == ["gpt-5.6-sol / ultra", "gpt-5.6-terra / high", "sonnet / max", "sonnet / max", "fable / max"]
    assert all(row["title"] == row["agent"] + " conversation" for row in record)
    assert all("separate sessions" in row["scope"] for row in record)


@pytest.mark.parametrize("shell", SHELLS)
def test_interrupt_is_latched_until_terminal_not_polling_or_rpc_ack(shell):
    record = run_ps(shell, """
$view=New-WdOperatorConversationView -Headless
Set-WdOperatorConversationStatus -View $view -TurnActive $true -CanInterrupt $true -OwnerEpoch e1 -ObservedTurnId t1
Submit-WdOperatorConversationAction -View $view -Kind interrupt
[void]@(Get-WdOperatorConversationActions -View $view)
Set-WdOperatorConversationStatus -View $view -TurnActive $true -CanInterrupt $true -OwnerEpoch e1 -ObservedTurnId t1
$blocked=$false
try { Submit-WdOperatorConversationAction -View $view -Kind interrupt } catch { $blocked=$true }
$waiting=$view.State.Interrupting
Set-WdOperatorConversationStatus -View $view -TurnActive $true -CanInterrupt $true -Interrupting $false
$stillBlocked=-not $view.State.CanInterrupt
Set-WdOperatorConversationStatus -View $view -TurnActive $false -CanInterrupt $false -ObservedTurnId ''
$cleared=-not $view.State.Interrupting
Set-WdOperatorConversationStatus -View $view -TurnActive $true -CanInterrupt $true -ObservedTurnId t2
Submit-WdOperatorConversationAction -View $view -Kind interrupt
[pscustomobject]@{blocked=$blocked;waiting=$waiting;stillBlocked=$stillBlocked;cleared=$cleared;nextCount=@(Get-WdOperatorConversationActions -View $view).Count} | ConvertTo-Json -Compress
""")
    assert record == {"blocked": True, "waiting": True, "stillBlocked": True, "cleared": True, "nextCount": 1}


@pytest.mark.parametrize("shell", SHELLS)
def test_user_rows_distinguish_pending_native_acceptance_rejection_and_unknown(shell):
    record = run_ps(shell, """
$view=New-WdOperatorConversationView -Headless
Set-WdOperatorConversationStatus -View $view -CanSend $true
$before=@(); $after=@()
foreach ($outcome in @('accepted','rejected','unknown')) {
  Submit-WdOperatorConversationAction -View $view -Kind send -Text ($outcome + ' message')
  $action=@(Get-WdOperatorConversationActions -View $view)[0]
  $row=@($view.State.Messages | Where-Object {$_.ItemId -ceq $action.id})[0]
  $before += $row.DeliveryState
  Resolve-WdOperatorConversationAction -View $view -ActionId $action.id -DeliveryState $outcome -Reason 'native outcome'
  $after += $row.DeliveryState
}
[pscustomobject]@{before=$before;after=$after;rows=$view.State.Messages.Count;pending=$view.PendingSends.Count;status=$view.State.Status} | ConvertTo-Json -Compress
""")
    assert record["before"] == ["pending"] * 3
    assert record["after"] == ["accepted", "rejected", "unknown"]
    assert record["rows"] == 3 and record["pending"] == 0
    assert "unknown" in record["status"].lower() and "Draft retained" in record["status"]


@pytest.mark.parametrize("shell", SHELLS)
def test_reconciliation_is_explicit_idle_text_only_and_not_ordinary_resume(shell):
    record = run_ps(shell, """
$view=New-WdOperatorConversationView -Headless
$unavailable=$false
try { Submit-WdOperatorConversationAction -View $view -Kind reconcile -Text 'Inspect the outcome without changing files' } catch { $unavailable=$true }
Set-WdOperatorConversationStatus -View $view -CanReconcile $true -RecoveryReason 'Known terminal turn needs read-only reconciliation' -TurnActive $false -CanSend $true -CanToggleAutomation $true -AutomationEnabled $false -OwnerEpoch e1 -ObservedTurnId t1
$ordinaryBlocked=$false
try { Submit-WdOperatorConversationAction -View $view -Kind send -Text 'Continue.' } catch { $ordinaryBlocked=$true }
$automaticBlocked=$false
try { Submit-WdOperatorConversationAction -View $view -Kind automation_toggle -Enabled $true } catch { $automaticBlocked=$true }
$emptyBlocked=$false
try { Submit-WdOperatorConversationAction -View $view -Kind reconcile } catch { $emptyBlocked=$true }
$imagesBlocked=$false
try { Submit-WdOperatorConversationAction -View $view -Kind reconcile -Text 'Inspect' -Paths @('image.png') } catch { $imagesBlocked=$true }
Submit-WdOperatorConversationAction -View $view -Kind reconcile -Text 'Inspect the outcome without changing files'
$action=@(Get-WdOperatorConversationActions -View $view)[0]
$repeatBlocked=$false
try { Submit-WdOperatorConversationAction -View $view -Kind reconcile -Text 'again' } catch { $repeatBlocked=$true }
Resolve-WdOperatorConversationAction -View $view -ActionId $action.id -DeliveryState accepted
Set-WdOperatorConversationStatus -View $view -TurnActive $true -CanReconcile $true
$activeBlocked=$false
try { Submit-WdOperatorConversationAction -View $view -Kind reconcile -Text 'again' } catch { $activeBlocked=$true }
[pscustomobject]@{unavailable=$unavailable;ordinaryBlocked=$ordinaryBlocked;automaticBlocked=$automaticBlocked;emptyBlocked=$emptyBlocked;imagesBlocked=$imagesBlocked;repeatBlocked=$repeatBlocked;activeBlocked=$activeBlocked;action=$action;delivery=$view.State.Messages[0].DeliveryState;automation=$view.State.AutomationEnabled} | ConvertTo-Json -Depth 5 -Compress
""")
    assert all(record[key] for key in ["unavailable", "ordinaryBlocked", "automaticBlocked", "emptyBlocked", "imagesBlocked", "repeatBlocked", "activeBlocked"])
    assert record["action"]["kind"] == "reconcile" and record["action"]["text"] == "Inspect the outcome without changing files"
    assert record["action"]["owner_epoch"] == "e1" and record["delivery"] == "accepted"
    assert record["automation"] is False


@pytest.mark.skipif(os.name != "nt", reason="WinForms needs Windows")
@pytest.mark.parametrize("shell", SHELLS)
def test_offscreen_agent_delivery_interrupt_and_reconcile_controls(shell):
    record = run_ps(shell, """
$view=New-WdOperatorConversationView -Hidden -Title 'Tools conversation' -AgentLabel codex-tools-1 -ModelLabel 'gpt-5.6-terra / high (pinned)'
$click=[Reflection.BindingFlags]'Instance,NonPublic'
Set-WdOperatorConversationStatus -View $view -CanSend $true -CanInterrupt $true -TurnActive $true -OwnerEpoch e1 -ObservedTurnId t1
$view.Controls.Input.Text='adjust this turn'
[void]$view.Controls.Send.GetType().GetMethod('OnClick',$click).Invoke($view.Controls.Send,@([EventArgs]::Empty))
$send=@(Get-WdOperatorConversationActions -View $view)[0]
$pending=$view.Controls.Transcript.Text.Contains('[user - pending]')
Resolve-WdOperatorConversationAction -View $view -ActionId $send.id -DeliveryState unknown
$unknown=$view.Controls.Transcript.Text.Contains('[user - unknown]') -and $view.Controls.Input.Text -ceq 'adjust this turn'
[void]$view.Controls.Interrupt.GetType().GetMethod('OnClick',$click).Invoke($view.Controls.Interrupt,@([EventArgs]::Empty))
[void]@(Get-WdOperatorConversationActions -View $view)
Set-WdOperatorConversationStatus -View $view -CanInterrupt $true -TurnActive $true
$stopping=-not $view.Controls.Interrupt.Enabled -and $view.Controls.Interrupt.Text -ceq 'Stopping...'
Set-WdOperatorConversationStatus -View $view -CanReconcile $true -RecoveryReason 'Original work remains unresolved' -CanSend $false -CanToggleAutomation $false -TurnActive $false -CanInterrupt $false
$onlyRecovery=$view.Controls.Reconcile.Enabled -and -not $view.Controls.Send.Enabled -and -not $view.Controls.Continue.Enabled -and -not $view.Controls.Automation.Enabled
$view.Controls.Input.Text='Inspect the existing outcome read-only'
[void]$view.Controls.Reconcile.GetType().GetMethod('OnClick',$click).Invoke($view.Controls.Reconcile,@([EventArgs]::Empty))
$reconcile=@(Get-WdOperatorConversationActions -View $view)[0]
$retained=$view.Controls.Input.Text -ceq $reconcile.text
Resolve-WdOperatorConversationAction -View $view -ActionId $reconcile.id -DeliveryState accepted
$accepted=$view.Controls.Transcript.Text.Contains('[user - accepted]') -and $view.Controls.Input.Text.Length -eq 0
$title=$view.Form.Text
Close-WdOperatorConversationView -View $view
[pscustomobject]@{title=$title;pending=$pending;unknown=$unknown;stopping=$stopping;onlyRecovery=$onlyRecovery;kind=$reconcile.kind;retained=$retained;accepted=$accepted} | ConvertTo-Json -Compress
""")
    assert record == {"title": "Tools conversation", "pending": True, "unknown": True,
                      "stopping": True, "onlyRecovery": True, "kind": "reconcile",
                      "retained": True, "accepted": True}


@pytest.mark.parametrize("shell", SHELLS)
def test_actions_require_backend_availability_and_are_not_falsely_sent(shell):
    record = run_ps(shell, """
$view=New-WdOperatorConversationView -Headless
$blocked=$false
try { Submit-WdOperatorConversationAction -View $view -Kind send -Text 'too early' } catch { $blocked=$true }
Set-WdOperatorConversationStatus -View $view -Text 'Working' -CanSend $true -CanInterrupt $true -CanToggleAutomation $true -AutomationEnabled $true -TurnActive $true -OwnerEpoch e1 -ObservedTurnId t1
Submit-WdOperatorConversationAction -View $view -Kind send -Text 'Adjust this same turn'
Submit-WdOperatorConversationAction -View $view -Kind automation_toggle -Enabled $false
Submit-WdOperatorConversationAction -View $view -Kind interrupt
$actions=@(Get-WdOperatorConversationActions -View $view)
[pscustomobject]@{blocked=$blocked;actions=$actions;remaining=@(Get-WdOperatorConversationActions -View $view).Count;messages=$view.State.Messages.Count;delivery=$view.State.Messages[0].DeliveryState;automation=$view.State.AutomationEnabled;status=$view.State.Status} | ConvertTo-Json -Depth 8 -Compress
""")
    assert record["blocked"] is True
    assert [action["kind"] for action in record["actions"]] == ["send", "automation_toggle", "interrupt"]
    assert record["actions"][0]["owner_epoch"] == "e1"
    assert record["actions"][0]["observed_turn_id"] == "t1"
    assert record["remaining"] == 0 and record["messages"] == 1
    assert record["delivery"] == "pending"
    assert record["automation"] is True  # Backend, not the click, confirms state.
    assert "Queued" in record["status"]


@pytest.mark.parametrize("shell", SHELLS)
def test_question_answers_are_explicit_complete_and_not_transcript_echoes(shell):
    record = run_ps(shell, """
$view=New-WdOperatorConversationView -Headless
Show-WdOperatorConversationQuestion -View $view -RequestId r1 -Questions @(
  [pscustomobject]@{id='choice';header='Choose';question='Which?';options=@([pscustomobject]@{label='Keep';description='Keep scope'});isOther=$false;isSecret=$false},
  [pscustomobject]@{id='detail';header='Detail';question='Explain';options=@();isOther=$true;isSecret=$true}
)
$incomplete=$false
try { Submit-WdOperatorConversationAction -View $view -Kind question_answer -RequestId r1 -Answers @{choice=@('Keep')} } catch { $incomplete=$true }
Submit-WdOperatorConversationAction -View $view -Kind question_answer -RequestId r1 -Answers @{choice=@('Keep');detail=@('operator supplied value')}
$answer=@(Get-WdOperatorConversationActions -View $view)[0]
$pending=$view.State.Question.Pending
Clear-WdOperatorConversationQuestion -View $view -RequestId r1
[pscustomobject]@{incomplete=$incomplete;answer=$answer;pending=$pending;cleared=($null -eq $view.State.Question);messages=$view.State.Messages.Count} | ConvertTo-Json -Depth 8 -Compress
""")
    assert record["incomplete"] is True
    assert record["answer"]["answer"] == {"choice": ["Keep"], "detail": ["operator supplied value"]}
    assert record["pending"] is True and record["cleared"] is True
    assert record["messages"] == 0


@pytest.mark.parametrize("shell", SHELLS)
def test_plaintext_deltas_and_display_history_are_bounded(shell):
    record = run_ps(shell, """
$view=New-WdOperatorConversationView -Headless
Add-WdOperatorConversationMessage -View $view -Role assistant -Text 'first ' -ItemId m1
Add-WdOperatorConversationMessage -View $view -Role assistant -Text '<script>literal</script>' -ItemId m1 -Delta
$delta=$view.State.Messages[0].Text
1..250 | ForEach-Object { Add-WdOperatorConversationMessage -View $view -Role assistant -Text ('x' * 1024) }
Add-WdOperatorConversationMessage -View $view -Role bridge -Text ('b' * 40000)
[pscustomobject]@{delta=$delta;count=$view.State.Messages.Count;characters=($view.State.Messages | Measure-Object -Property Text -Character).Characters;omitted=$view.State.DisplayOmitted;bridge=$view.State.BridgeText.Length} | ConvertTo-Json -Compress
""")
    assert record["delta"] == "first <script>literal</script>"
    assert record["count"] <= 200 and record["characters"] <= 131072
    assert record["omitted"] is True and record["bridge"] <= 32768


@pytest.mark.parametrize("shell", SHELLS)
def test_attachment_validation_and_queue_bound(shell, tmp_path):
    attachment = tmp_path / "operator image.png"
    attachment.write_text("ordinary local data", encoding="utf-8")
    record = run_ps(shell, f"""
$view=New-WdOperatorConversationView -Headless
Set-WdOperatorConversationStatus -View $view -Text ready -CanSend $true
Submit-WdOperatorConversationAction -View $view -Kind send -Text 'Inspect image data' -Paths @({quote(attachment)})
$action=@(Get-WdOperatorConversationActions -View $view)[0]
Resolve-WdOperatorConversationAction -View $view -ActionId $action.id -Accepted $true
$invalid=$false
try {{ Submit-WdOperatorConversationAction -View $view -Kind send -Text 'x' -Paths @({quote(tmp_path)}) }} catch {{ $invalid=$true }}
1..32 | ForEach-Object {{ Submit-WdOperatorConversationAction -View $view -Kind send -Text 'queued data' }}
$full=$false
try {{ Submit-WdOperatorConversationAction -View $view -Kind send -Text 'overflow' }} catch {{ $full=$true }}
Submit-WdOperatorConversationAction -View $view -Kind close
$drained=@(Get-WdOperatorConversationActions -View $view)
[pscustomobject]@{{action=$action;invalid=$invalid;full=$full;count=$drained.Count;last=$drained[-1].kind}} | ConvertTo-Json -Depth 8 -Compress
""")
    assert record["action"]["attachments"][0]["path"] == str(attachment)
    assert record["action"]["attachments"][0]["kind"] == "image"
    assert record["invalid"] is True and record["full"] is True
    assert record["count"] == 33 and record["last"] == "close"


@pytest.mark.skipif(os.name != "nt", reason="WinForms needs Windows")
@pytest.mark.parametrize("shell", SHELLS)
def test_winforms_offscreen_smoke_and_control_availability(shell):
    record = run_ps(shell, """
$view=New-WdOperatorConversationView -Hidden
$initial=$view.Controls.Send.Enabled
Set-WdOperatorConversationStatus -View $view -Text Working -CanSend $true -CanInterrupt $true -CanToggleAutomation $true -TurnActive $true
$view.Controls.Input.Text='same-turn adjustment'
# A hidden parent intentionally cannot receive PerformClick. Invoke the real
# control event directly without showing a window or sending desktop keys.
[void]$view.Controls.Send.GetType().GetMethod('OnClick',[Reflection.BindingFlags]'Instance,NonPublic').Invoke($view.Controls.Send,@([EventArgs]::Empty))
Update-WdOperatorConversationView -View $view
$action=@(Get-WdOperatorConversationActions -View $view)[0]
$caption=$view.Controls.Send.Text
$readOnly=$view.Controls.Transcript.ReadOnly
Close-WdOperatorConversationView -View $view
[pscustomobject]@{initial=$initial;kind=$action.kind;caption=$caption;readOnly=$readOnly;disposed=$view.Form.IsDisposed} | ConvertTo-Json -Compress
""")
    assert record == {"initial": False, "kind": "send", "caption": "Steer current turn", "readOnly": True, "disposed": True}


@pytest.mark.skipif(os.name != "nt", reason="WinForms needs Windows")
@pytest.mark.parametrize("shell", SHELLS)
def test_offscreen_questions_and_manual_continue_are_explicit(shell):
    record = run_ps(shell, """
$view=New-WdOperatorConversationView -Hidden
Set-WdOperatorConversationStatus -View $view -Text ready -CanSend $true -TurnActive $false
[void]$view.Controls.Continue.GetType().GetMethod('OnClick',[Reflection.BindingFlags]'Instance,NonPublic').Invoke($view.Controls.Continue,@([EventArgs]::Empty))
$resume=@(Get-WdOperatorConversationActions -View $view)[0]
Show-WdOperatorConversationQuestion -View $view -RequestId r1 -Questions @(
  [pscustomobject]@{id='choice';question='Choose';options=@([pscustomobject]@{label='Keep'})},
  [pscustomobject]@{id='detail';question='Private input';isSecret=$true}
)
$unselected=$view.QuestionInputs.choice.Combo.SelectedIndex
$view.QuestionInputs.choice.Combo.SelectedIndex=0
$view.QuestionInputs.detail.Input.Text='explicit value'
$masked=$view.QuestionInputs.detail.Input.UseSystemPasswordChar
[void]$view.Controls.Answer.GetType().GetMethod('OnClick',[Reflection.BindingFlags]'Instance,NonPublic').Invoke($view.Controls.Answer,@([EventArgs]::Empty))
$answer=@(Get-WdOperatorConversationActions -View $view)[0]
$disabled=-not $view.Controls.Answer.Enabled
Close-WdOperatorConversationView -View $view
[pscustomobject]@{resume=$resume.text;unselected=$unselected;masked=$masked;answer=$answer.answer;disabled=$disabled} | ConvertTo-Json -Depth 8 -Compress
""")
    assert record == {"resume": "Continue.", "unselected": -1, "masked": True,
                      "answer": {"choice": ["Keep"], "detail": ["explicit value"]}, "disabled": True}


@pytest.mark.skipif(os.name != "nt", reason="WinForms needs Windows")
@pytest.mark.parametrize("shell", SHELLS)
def test_draft_waits_for_backend_acceptance_and_preserves_newer_edits(shell, tmp_path):
    unsupported = tmp_path / "note.txt"
    unsupported.write_text("ordinary data")
    image = tmp_path / "picture.png"
    image.write_bytes(b"fixture image data")
    record = run_ps(shell, f"""
$view=New-WdOperatorConversationView -Hidden
Set-WdOperatorConversationStatus -View $view -Text ready -CanSend $true
$view.Controls.Input.Text='original draft'
$view.AttachmentPaths=@({quote(unsupported)})
$click=$view.Controls.Send.GetType().GetMethod('OnClick',[Reflection.BindingFlags]'Instance,NonPublic')
[void]$click.Invoke($view.Controls.Send,@([EventArgs]::Empty))
$invalidCount=@(Get-WdOperatorConversationActions -View $view).Count
$invalidDraft=$view.Controls.Input.Text
$view.AttachmentPaths=@({quote(image)})
Set-WdOperatorConversationStatus -View $view -Text working -TurnActive $true
$activeDisabled=-not $view.Controls.Send.Enabled
[void]$click.Invoke($view.Controls.Send,@([EventArgs]::Empty))
$activeCount=@(Get-WdOperatorConversationActions -View $view).Count
$activeDraft=$view.Controls.Input.Text
$view.AttachmentPaths=@()
Set-WdOperatorConversationStatus -View $view -Text ready -TurnActive $false
[void]$click.Invoke($view.Controls.Send,@([EventArgs]::Empty))
$first=@(Get-WdOperatorConversationActions -View $view)[0]
$queuedDraft=$view.Controls.Input.Text
Resolve-WdOperatorConversationAction -View $view -ActionId $first.id -Accepted $false -Reason 'native rejected before delivery'
$rejectedDraft=$view.Controls.Input.Text
[void]$click.Invoke($view.Controls.Send,@([EventArgs]::Empty))
$second=@(Get-WdOperatorConversationActions -View $view)[0]
$view.Controls.Input.Text='newer edit'
Resolve-WdOperatorConversationAction -View $view -ActionId $second.id -Accepted $true
$newer=$view.Controls.Input.Text
[void]$click.Invoke($view.Controls.Send,@([EventArgs]::Empty))
$third=@(Get-WdOperatorConversationActions -View $view)[0]
Resolve-WdOperatorConversationAction -View $view -ActionId $third.id -Accepted $true
$cleared=$view.Controls.Input.Text
Close-WdOperatorConversationView -View $view
[pscustomobject]@{{invalidCount=$invalidCount;invalidDraft=$invalidDraft;activeDisabled=$activeDisabled;activeCount=$activeCount;activeDraft=$activeDraft;queuedDraft=$queuedDraft;rejectedDraft=$rejectedDraft;newer=$newer;cleared=$cleared}} | ConvertTo-Json -Compress
""")
    assert record == {"invalidCount": 0, "invalidDraft": "original draft", "activeDisabled": True,
                      "activeCount": 0, "activeDraft": "original draft", "queuedDraft": "original draft",
                      "rejectedDraft": "original draft", "newer": "newer edit", "cleared": ""}


@pytest.mark.skipif(os.name != "nt", reason="Windows contained fake transport")
@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("agent,model,effort,label", [
    ("codex-lead-1", "gpt-5.6-sol", "ultra", "Lead"),
    ("codex-tools-1", "gpt-5.6-terra", "high", "Tools"),
])
def test_real_headless_view_backend_steer_question_and_checkpoint(shell, tmp_path, agent, model, effort, label):
    """Real UI API and real owner loop; only the native model process is fake."""
    runtime = tmp_path / "bridge"
    runtime.mkdir()
    (tmp_path / ".codex-audit").mkdir()
    fake = tmp_path / "fake_conversation.py"
    fake.write_text('''import datetime, json, pathlib, sys
root = pathlib.Path(sys.argv[1])
def send(value): print(json.dumps(value), flush=True)
for line in sys.stdin:
    message = json.loads(line)
    with (root / "calls.jsonl").open("a") as log: log.write(json.dumps(message)+"\\n")
    method = message.get("method", "")
    if method == "initialize": send({"id":message["id"],"result":{}})
    elif method == "thread/start": send({"id":message["id"],"result":{"thread":{"id":"one-thread"}}})
    elif method == "turn/start":
        send({"id":message["id"],"result":{"turn":{"id":"one-turn"}}})
        send({"method":"turn/started","params":{"threadId":"one-thread","turn":{"id":"one-turn"}}})
    elif method == "turn/steer":
        assert message["params"]["expectedTurnId"] == "one-turn"
        send({"id":message["id"],"result":{"turnId":"one-turn"}})
        send({"id":"question-1","method":"item/tool/requestUserInput","params":{"threadId":"one-thread","turnId":"one-turn","questions":[{"id":"choice","question":"Keep scope?","options":[{"label":"Keep"}]}]}})
    elif message.get("id") == "question-1":
        assert message["result"]["answers"] == {"choice":{"answers":["Keep"]}}
        spec = json.loads(next((root / ".codex-audit/wd-turn-loop").glob("*.spec.json")).read_text(encoding="utf-8-sig"))
        stamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
        pathlib.Path(spec["compact_state_path"]).write_text(json.dumps({"schema":"wd.lane-current.v1","agent":spec["agent"],"worktree":spec["worktree"],"task_id":"ui-test","status":"idle","next_action":"wait","updated_at_utc":stamp}))
        receipt = {key:spec[key] for key in ["turn_id","agent","session_id","generation","compact_state_path"]}
        receipt.update(disposition="idle",task_id="ui-test")
        pathlib.Path(spec["receipt_path"]).write_text(json.dumps(receipt))
        send({"method":"item/agentMessage/delta","params":{"threadId":"one-thread","turnId":"one-turn","itemId":"answer","delta":"Completed scoped test"}})
        send({"method":"turn/completed","params":{"threadId":"one-thread","turn":{"id":"one-turn","status":"completed"}}})
''', encoding="utf-8")
    native_python = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Python/Python313/python.exe"
    if not native_python.is_file():
        import sys
        native_python = Path(sys.executable)
    old_runner = ROOT / "ops/windows/reboot/Invoke-WdLaneTurnLoop.ps1"
    backend = ROOT / "ops/windows/reboot/Invoke-WdCodexConversationLoop.ps1"
    record = run_ps(shell, f"""
. {quote(old_runner)}
. {quote(backend)}
function New-WdConversationNativeProcess {{ param($CliPath,$Worktree)
    Initialize-WdConversationNativeType
    New-Object WdConversationProcess({quote(native_python)},[string[]]@({quote(fake)},{quote(tmp_path)}),$Worktree)
}}
$script:actualFactory=${{function:New-WdOperatorConversationView}}
$script:actualPump=${{function:Update-WdOperatorConversationView}}
$script:stage=0; $script:initial=$true
function New-WdOperatorConversationView {{ param([switch]$Headless,[string]$AgentLabel,[string]$ModelLabel,[string]$Title)
    $script:actualView=& $script:actualFactory @PSBoundParameters
    $script:initial=$script:actualView.State.CanSend
    return $script:actualView
}}
function Update-WdOperatorConversationView {{ param($View)
    & $script:actualPump -View $View
    if ($View.State.Question -and -not $View.State.Question.Pending) {{
        Submit-WdOperatorConversationAction -View $View -Kind question_answer -RequestId $View.State.Question.RequestId -Answers @{{choice=@('Keep')}}
        $script:stage=2
    }} elseif ($script:stage -eq 0 -and $View.State.CanSend -and $View.State.TurnActive) {{
        Submit-WdOperatorConversationAction -View $View -Kind send -Text 'Adjust this same active turn'
        $script:stage=1
    }} elseif ($script:stage -eq 2 -and $View.State.CanSend -and -not $View.State.TurnActive) {{
        Submit-WdOperatorConversationAction -View $View -Kind close
    }}
}}
$owner=Invoke-WdCodexConversationLoop -Agent {quote(agent)} -Model {quote(model)} -Effort {quote(effort)} -CliPath {quote(native_python)} -Worktree {quote(tmp_path)} -RuntimeRoot {quote(runtime)} -SessionId ui-integration -Generation {'b'*40} -CompactStatePath {quote(tmp_path / '.codex-audit/wd-current-state.json')} -StartupPrompt 'Scoped fake test' -Headless -RpcTimeoutSeconds 3 -TurnTimeoutSeconds 8 -MaxIterations 160
[pscustomobject]@{{initial=$script:initial;stage=$script:stage;closed=$script:actualView.State.Closed;label=$script:actualView.State.AgentLabel;model=$script:actualView.State.ModelLabel;owner=$owner;messages=$script:actualView.State.Messages.ToArray()}} | ConvertTo-Json -Depth 12 -Compress
""")
    assert record["initial"] is False and record["stage"] == 2 and record["closed"] is True
    assert record["owner"]["last_disposition"] == "idle"
    assert record["label"] == label and record["model"] == f"{label}: {model} / {effort} (pinned)"
    user_rows = [message for message in record["messages"] if message["Role"] == "user"]
    assert len(user_rows) == 1 and user_rows[0]["DeliveryState"] == "accepted"
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert sum(call.get("method") == "thread/start" for call in calls) == 1
    assert sum(call.get("method") == "turn/start" for call in calls) == 1
    assert sum(call.get("method") == "turn/steer" for call in calls) == 1
    assert any(message["Text"] == "Completed scoped test" for message in record["messages"])
    assert not list((tmp_path / ".codex-audit/wd-turn-loop").glob("*.pending"))
