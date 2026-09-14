#requires -Version 5.1
<#
UI-only managed Lead conversation. Dot-source from the owning backend.
No CLI/RPC/bridge operations, process starts, persistence, or approvals occur
here. Actions are proposals awaiting backend acceptance, not sent messages.
Headless mode exercises the same reducer without WinForms or a desktop.
#>

function Get-WdOperatorProperty {
    param($Object, [string] $Name, $Default = $null)
    if ($null -eq $Object) { return $Default }
    if ($Object -is [Collections.IDictionary]) {
        if ($Object.Contains($Name)) { return $Object[$Name] }
    } elseif ($null -ne $Object.PSObject.Properties[$Name]) { return $Object.PSObject.Properties[$Name].Value }
    return $Default
}

function Get-WdOperatorLocalAttachments {
    param([string[]] $Paths = @())
    if ($Paths.Count -gt 4) { throw 'Attach at most four local files per message.' }
    $total = 0L
    foreach ($path in $Paths) {
        if (-not [IO.Path]::IsPathRooted($path) -or $path.StartsWith('\\')) {
            throw 'Attachments must be explicit local paths, not network paths.'
        }
        $full = [IO.Path]::GetFullPath($path)
        $item = Get-Item -LiteralPath $full -Force -ErrorAction Stop
        if ($item.PSIsContainer -or $item.Length -gt 10485760) {
            throw 'Each attachment must be a regular file of at most 10 MiB.'
        }
        $component = $item
        while ($null -ne $component) {
            if (($component.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw 'Attachment paths cannot traverse links or reparse points.'
            }
            $component = if ($component -is [IO.FileInfo]) { $component.Directory } else { $component.Parent }
        }
        $total += $item.Length
        if ($total -gt 20971520) { throw 'Attachments total more than 20 MiB.' }
        $kind = if ($item.Extension.ToLowerInvariant() -in @('.png','.jpg','.jpeg','.webp','.gif')) { 'image' } else { 'file' }
        [pscustomobject]@{ path=$full; name=$item.Name; kind=$kind; size_bytes=[long]$item.Length }
    }
}

function Submit-WdOperatorConversationAction {
    param(
        [Parameter(Mandatory)] $View,
        [Parameter(Mandatory)] [ValidateSet('send','interrupt','automation_toggle','question_answer','close')] [string] $Kind,
        [string] $Text = '', [string[]] $Paths = @(), [bool] $Enabled = $false,
        [string] $RequestId = '', [hashtable] $Answers = @{}
    )
    if ($View.State.Closed) { throw 'The conversation view is closed.' }
    $action = [ordered]@{
        id=[guid]::NewGuid().ToString('n'); kind=$Kind
        owner_epoch=$View.State.OwnerEpoch; observed_turn_id=$View.State.ObservedTurnId
    }
    if ($Kind -eq 'close') {
        # Reserve one independent close slot, even if all 32 data slots are full.
        if (-not $View.State.CloseRequested) { $View.CloseAction = [pscustomobject]$action }
        $View.State.CloseRequested = $true
        return
    }
    if ($View.State.CloseRequested) { throw 'The conversation is closing.' }
    if ($View.Actions.Count -ge 32) { throw 'The local action queue is full; wait for the backend.' }
    switch ($Kind) {
        'send' {
            if (-not $View.State.CanSend) { throw 'The backend is not accepting messages.' }
            if ($Text.Length -gt 16384) { throw 'Message exceeds the 16,384 character limit.' }
            $attachments = @(Get-WdOperatorLocalAttachments -Paths $Paths)
            if ([string]::IsNullOrWhiteSpace($Text)) { throw 'Enter instruction text, including a caption when attaching images.' }
            if (@($attachments | Where-Object { $_.kind -ne 'image' }).Count) { throw 'Only image attachments are supported. Other files can be referenced by path in your message.' }
            if ($View.State.TurnActive -and $attachments.Count) { throw 'Images cannot steer an active turn. Keep the draft until idle, or remove the images.' }
            if ($View.PendingSends.Count -ge 32) { throw 'Too many sends await backend acceptance.' }
            $action.text = $Text; $action.attachments = $attachments
            $View.PendingSends[$action.id] = [pscustomobject]@{ Text=$Text; Paths=@($Paths) }
        }
        'interrupt' {
            if (-not $View.State.CanInterrupt) { throw 'No interruptible backend turn is available.' }
            $View.State.CanInterrupt = $false
        }
        'automation_toggle' {
            if (-not $View.State.CanToggleAutomation) { throw 'Automation control is unavailable.' }
            $action.enabled = $Enabled
            $View.State.CanToggleAutomation = $false
        }
        'question_answer' {
            $request = $View.State.Question
            if ($null -eq $request -or $request.Pending -or $RequestId -cne $request.RequestId) { throw 'No matching unanswered question is available.' }
            if ($Answers.Count -ne $request.Questions.Count) { throw 'Answer every question explicitly.' }
            $validated = @{}
            foreach ($question in $request.Questions) {
                $values = @($Answers[$question.id])
                if ($values.Count -ne 1 -or [string]::IsNullOrWhiteSpace([string]$values[0]) -or
                    ([string]$values[0]).Length -gt 16384) { throw 'Each question needs one bounded explicit answer.' }
                if ($question.options.Count -gt 0 -and -not $question.isOther -and
                    [string]$values[0] -cnotin @($question.options | ForEach-Object { $_.label })) {
                    throw 'Select one of the offered answers.'
                }
                $validated[$question.id] = [string[]]@([string]$values[0])
            }
            $action.request_id = $RequestId; $action.answer = $validated
            $request.Pending = $true
        }
    }
    $View.Actions.Enqueue([pscustomobject]$action)
    $View.State.Status = 'Queued for backend acceptance. No execution or delivery is confirmed yet.'
    Sync-WdOperatorConversationView -View $View
}

function Resolve-WdOperatorConversationAction {
    param([Parameter(Mandatory)] $View, [Parameter(Mandatory)] [string] $ActionId,
        [Parameter(Mandatory)] [bool] $Accepted, [string] $Reason = '')
    if (-not $View.PendingSends.ContainsKey($ActionId)) { return }
    $pending = $View.PendingSends[$ActionId]
    $View.PendingSends.Remove($ActionId)
    if ($Accepted) {
        # Never clear edits composed after the queued version. The backend calls
        # this only for the matching native RPC result, not merely a pipe write.
        if (-not $View.Headless -and $View.Controls.Input.Text -ceq $pending.Text -and
            ($View.AttachmentPaths -join "`n") -ceq ($pending.Paths -join "`n")) {
            $View.Controls.Input.Clear(); $View.AttachmentPaths=@()
        }
        $View.State.Status='Backend accepted the message; task completion is not implied.'
    } else {
        $View.State.Status='Not sent. Draft retained. ' + $Reason.Substring(0,[Math]::Min(1024,$Reason.Length))
    }
    Sync-WdOperatorConversationView -View $View
}

function Get-WdOperatorConversationActions {
    param([Parameter(Mandatory)] $View)
    while ($View.Actions.Count -gt 0) { $View.Actions.Dequeue() }
    if ($null -ne $View.CloseAction) {
        $action = $View.CloseAction; $View.CloseAction = $null
        $action
    }
}

function Set-WdOperatorConversationStatus {
    param([Parameter(Mandatory)] $View, [string] $Text,
        [bool] $CanSend, [bool] $CanInterrupt, [bool] $CanToggleAutomation,
        [bool] $AutomationEnabled, [bool] $TurnActive,
        [string] $OwnerEpoch, [string] $ObservedTurnId)
    foreach ($name in @('CanSend','CanInterrupt','CanToggleAutomation','AutomationEnabled','TurnActive','OwnerEpoch','ObservedTurnId')) {
        if ($PSBoundParameters.ContainsKey($name)) { $View.State.$name = $PSBoundParameters[$name] }
    }
    if ($PSBoundParameters.ContainsKey('Text')) { $View.State.Status = $Text.Substring(0, [Math]::Min(4096, $Text.Length)) }
    Sync-WdOperatorConversationView -View $View
}

function Add-WdOperatorConversationMessage {
    param([Parameter(Mandatory)] $View,
        [ValidateSet('user','operator','assistant','system','status','tool','bridge')] [string] $Role,
        [AllowEmptyString()] [string] $Text, [string] $ItemId = '', [switch] $Delta)
    if ($ItemId.Length -gt 256) { throw 'Message item ID is too long.' }
    if ($Role -eq 'bridge') {
        $value = if ($Delta) { $View.State.BridgeText + $Text } else { $Text }
        $View.State.BridgeText = $value.Substring([Math]::Max(0, $value.Length - 32768))
    } else {
        $message = $null
        if ($ItemId) { $message = @($View.State.Messages | Where-Object { $_.ItemId -ceq $ItemId } | Select-Object -First 1) }
        if ($null -ne $message -and @($message).Count -gt 0) {
            $message = @($message)[0]
            if ($message.Role -cne $Role) { throw 'Message item role changed.' }
            $message.Text = if ($Delta) { $message.Text + $Text } else { $Text }
        } else {
            $message = [pscustomobject]@{ Role=$Role; Text=$Text; ItemId=$ItemId }
            $View.State.Messages.Add($message)
        }
        if ($message.Text.Length -gt 32768) {
            $message.Text = $message.Text.Substring($message.Text.Length - 32768)
            $View.State.DisplayOmitted = $true
        }
        $length = 0
        foreach ($item in $View.State.Messages) { $length += $item.Text.Length }
        while ($View.State.Messages.Count -gt 200 -or $length -gt 131072) {
            $length -= $View.State.Messages[0].Text.Length
            $View.State.Messages.RemoveAt(0); $View.State.DisplayOmitted = $true
        }
    }
    $View.State.Revision++
    Sync-WdOperatorConversationView -View $View
}

function Show-WdOperatorConversationQuestion {
    param([Parameter(Mandatory)] $View, [Parameter(Mandatory)] [string] $RequestId,
        [Parameter(Mandatory)] [object[]] $Questions)
    if (-not $RequestId -or $RequestId.Length -gt 256 -or $Questions.Count -lt 1 -or $Questions.Count -gt 8) { throw 'Unsupported question request size.' }
    if ($null -ne $View.State.Question) { throw 'Resolve the current question request before displaying another.' }
    $normalized = @(); $ids = @{}
    foreach ($question in $Questions) {
        $id = [string](Get-WdOperatorProperty $question 'id')
        if (-not $id -or $id.Length -gt 128 -or $ids.ContainsKey($id)) { throw 'Question IDs must be nonempty and unique.' }
        $ids[$id] = $true
        $options = @(Get-WdOperatorProperty $question 'options' | Where-Object { $null -ne $_ })
        $questionText = [string](Get-WdOperatorProperty $question 'question')
        $headerText = [string](Get-WdOperatorProperty $question 'header')
        if ($options.Count -gt 8 -or $questionText.Length -gt 16384 -or $headerText.Length -gt 256) { throw 'Question content is too large.' }
        $normalizedOptions = @()
        foreach ($option in $options) {
            $label = [string](Get-WdOperatorProperty $option 'label')
            $description = [string](Get-WdOperatorProperty $option 'description')
            if (-not $label -or $label.Length -gt 256 -or $description.Length -gt 2048) { throw 'Unsupported question option.' }
            $normalizedOptions += [pscustomobject]@{ label=$label; description=$description }
        }
        $normalized += [pscustomobject]@{
            id=$id; header=$headerText; question=$questionText
            options=$normalizedOptions; isOther=[bool](Get-WdOperatorProperty $question 'isOther' $false)
            isSecret=[bool](Get-WdOperatorProperty $question 'isSecret' $false)
        }
    }
    $View.State.Question = [pscustomobject]@{ RequestId=$RequestId; Questions=$normalized; Pending=$false }
    if (-not $View.Headless) { Initialize-WdOperatorQuestionControls -View $View }
    Sync-WdOperatorConversationView -View $View
}

function Clear-WdOperatorConversationQuestion {
    param([Parameter(Mandatory)] $View, [Parameter(Mandatory)] [string] $RequestId)
    if ($null -ne $View.State.Question -and $View.State.Question.RequestId -ceq $RequestId) {
        $View.State.Question = $null
        if (-not $View.Headless) { Initialize-WdOperatorQuestionControls -View $View }
        Sync-WdOperatorConversationView -View $View
    }
}

function Initialize-WdOperatorQuestionControls {
    param($View)
    $panel = $View.Controls.Questions
    foreach ($control in @($panel.Controls)) { $control.Dispose() }
    $panel.Controls.Clear(); $View.QuestionInputs = @{}
    $question = $View.State.Question
    $View.Controls.Layout.RowStyles[2].Height = if ($null -eq $question) { 0 } else { 260 }
    $panel.Visible = $null -ne $question
    if ($null -eq $question) { return }
    foreach ($item in $question.Questions) {
        $label = New-Object Windows.Forms.RichTextBox
        $label.Text = $item.header + ': ' + $item.question
        $label.Width = 860; $label.Height = 75; $label.ForeColor = [Drawing.Color]::WhiteSmoke
        $label.BackColor = [Drawing.Color]::FromArgb(32,36,44)
        $label.ReadOnly=$true; $label.DetectUrls=$false; $label.BorderStyle='None'
        $panel.Controls.Add($label)
        $combo = New-Object Windows.Forms.ComboBox
        $combo.Width = 820; $combo.DropDownStyle = 'DropDownList'
        foreach ($option in $item.options) { [void]$combo.Items.Add([string]$option.label + ' - ' + [string]$option.description) }
        $combo.SelectedIndex = -1; $combo.Visible = $item.options.Count -gt 0
        $panel.Controls.Add($combo)
        $detail = New-Object Windows.Forms.RichTextBox
        $detail.Width=820; $detail.Height=52; $detail.ReadOnly=$true; $detail.DetectUrls=$false
        $detail.BackColor=[Drawing.Color]::FromArgb(32,36,44); $detail.ForeColor=[Drawing.Color]::WhiteSmoke
        $detail.Text='Select an option to read its full description.'; $detail.Visible=$item.options.Count -gt 0
        $combo.Tag=[pscustomobject]@{Detail=$detail;Options=$item.options}
        $combo.Add_SelectedIndexChanged({
            param($sender,$event)
            if ($sender.SelectedIndex -ge 0) { $sender.Tag.Detail.Text=[string]$sender.Tag.Options[$sender.SelectedIndex].description }
        })
        $panel.Controls.Add($detail)
        $inputBox = New-Object Windows.Forms.TextBox
        $inputBox.Width = 820; $inputBox.MaxLength = 16384
        $inputBox.Visible = $item.isOther -or $item.options.Count -eq 0
        $inputBox.UseSystemPasswordChar = $item.isSecret
        $panel.Controls.Add($inputBox)
        $View.QuestionInputs[$item.id] = [pscustomobject]@{ Question=$item; Input=$inputBox; Combo=$combo }
    }
    $button = New-Object Windows.Forms.Button
    $button.Text = 'Send explicit answers'; $button.Width = 180; $button.Height = 34
    $button.Add_Click({
        try {
            $answers = @{}
            foreach ($key in $View.QuestionInputs.Keys) {
                $row = $View.QuestionInputs[$key]
                $answer = $row.Input.Text
                if (-not $answer -and $row.Combo.SelectedIndex -ge 0) { $answer = [string]$row.Question.options[$row.Combo.SelectedIndex].label }
                $answers[$key] = [string[]]@($answer)
            }
            Submit-WdOperatorConversationAction -View $View -Kind question_answer -RequestId $View.State.Question.RequestId -Answers $answers
        } catch { Set-WdOperatorConversationStatus -View $View -Text ('Answer not accepted: ' + $_.Exception.Message) }
    }.GetNewClosure())
    $panel.Controls.Add($button); $View.Controls.Answer = $button
}

function Sync-WdOperatorConversationView {
    param($View)
    if ($View.Headless -or $View.State.Closed -or $View.Form.IsDisposed) { return }
    $View.Controls.Status.Text = $View.State.Status
    $View.Controls.Send.Enabled = $View.State.CanSend -and -not $View.State.CloseRequested -and
        $View.PendingSends.Count -eq 0 -and -not ($View.State.TurnActive -and $View.AttachmentPaths.Count -gt 0)
    $View.Controls.Send.Text = if ($View.State.TurnActive) { 'Steer current turn' } else { 'Send' }
    $View.Controls.Continue.Enabled = $View.Controls.Send.Enabled -and -not $View.State.TurnActive
    $View.Controls.Input.Enabled = -not $View.State.CloseRequested
    $View.Controls.Attach.Enabled = $View.State.CanSend -and -not $View.State.TurnActive -and $View.PendingSends.Count -eq 0
    $View.Controls.Interrupt.Enabled = $View.State.CanInterrupt
    $View.Controls.Automation.Enabled = $View.State.CanToggleAutomation
    $View.Controls.Automation.Text = if ($View.State.AutomationEnabled) { 'Pause automation' } else { 'Resume automation' }
    $View.Controls.Attachments.Text = if ($View.State.TurnActive -and $View.AttachmentPaths.Count) {
        'Images cannot steer an active turn. Wait until idle, interrupt, or clear the images; your draft is retained.'
    } elseif ($View.AttachmentPaths.Count) { 'Local image data: ' + (($View.AttachmentPaths | ForEach-Object { [IO.Path]::GetFileName($_) }) -join ', ') } else { 'Images need a caption and an idle turn. Other files may be referenced by path; nothing is executed by this UI.' }
    if ($null -ne $View.State.Question -and $View.Controls.ContainsKey('Answer')) {
        $View.Controls.Answer.Enabled = -not $View.State.Question.Pending
        foreach ($row in $View.QuestionInputs.Values) {
            $row.Input.Enabled = -not $View.State.Question.Pending
            $row.Combo.Enabled = -not $View.State.Question.Pending
        }
    }
    if ($View.RenderedRevision -ne $View.State.Revision) {
        $parts = @()
        if ($View.State.DisplayOmitted) { $parts += '[Older display text omitted. This does not erase the backend conversation context.]' }
        foreach ($message in $View.State.Messages) { $parts += ('[' + $message.Role + '] ' + $message.Text) }
        $box = $View.Controls.Transcript
        $start = $box.SelectionStart; $length = $box.SelectionLength
        $keepPosition = $box.Focused -or $length -gt 0
        $box.Text = $parts -join "`r`n`r`n"
        if ($keepPosition) { $box.Select([Math]::Min($start,$box.TextLength), [Math]::Min($length,[Math]::Max(0,$box.TextLength-$start))) }
        else { $box.Select($box.TextLength,0); $box.ScrollToCaret() }
        $View.Controls.Bridge.Text = $View.State.BridgeText
        $View.RenderedRevision = $View.State.Revision
    }
}

function New-WdOperatorConversationView {
    param([switch] $Headless, [switch] $Hidden,
        [string] $Title = 'WaggleDance - Lead conversation',
        [string] $ModelLabel = 'Lead: gpt-5.6-sol / ultra (pinned)')
    $view = [pscustomobject]@{
        Headless=[bool]$Headless; Form=$null; Controls=@{}; QuestionInputs=@{}
        Actions=(New-Object 'Collections.Generic.Queue[object]'); CloseAction=$null; PendingSends=@{}
        AttachmentPaths=@(); RenderedRevision=-1; Disposing=$false
        State=[pscustomobject]@{
            Status='Waiting for the owning backend. Messages are not yet accepted.'
            CanSend=$false; CanInterrupt=$false; CanToggleAutomation=$false
            AutomationEnabled=$false; TurnActive=$false; OwnerEpoch=''; ObservedTurnId=''
            Closed=$false; CloseRequested=$false; Question=$null
            Messages=(New-Object 'Collections.Generic.List[object]'); Revision=0; DisplayOmitted=$false
            BridgeText='Bridge feed not connected. Other agents retain their separate sessions.'
        }
    }
    if ($Headless) { return $view }
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { throw 'The conversation window requires Windows; use Headless only for reducer tests.' }
    if ([Threading.Thread]::CurrentThread.GetApartmentState() -ne 'STA') { throw 'The conversation window requires a PowerShell -STA host.' }
    Add-Type -AssemblyName System.Windows.Forms, System.Drawing
    [Windows.Forms.Application]::EnableVisualStyles()
    $form = New-Object Windows.Forms.Form
    $form.Text=$Title; $form.Size=New-Object Drawing.Size(1100,850)
    $form.MinimumSize=New-Object Drawing.Size(780,640)
    $form.BackColor=[Drawing.Color]::FromArgb(24,27,33); $form.ForeColor=[Drawing.Color]::WhiteSmoke
    $form.Font=New-Object Drawing.Font('Segoe UI',10)
    $form.StartPosition='CenterScreen'; $view.Form=$form
    $layout=New-Object Windows.Forms.TableLayoutPanel
    $layout.Dock='Fill'; $layout.ColumnCount=1; $layout.RowCount=7; $layout.Padding=New-Object Windows.Forms.Padding(14)
    foreach ($height in @(80,0,0,26,105,44,48)) {
        [void]$layout.RowStyles.Add((New-Object Windows.Forms.RowStyle([Windows.Forms.SizeType]::Absolute,$height)))
    }
    $layout.RowStyles[1].SizeType='Percent'; $layout.RowStyles[1].Height=100
    $form.Controls.Add($layout); $view.Controls.Layout=$layout
    $header=New-Object Windows.Forms.Label
    $header.Dock='Fill'; $header.Text=$ModelLabel + "`r`nLead only: other agents retain their separate sessions and model contexts.`r`nSend steers the active turn. Interrupt stops it; Continue resumes this conversation. Automation controls new bridge turns."
    $layout.Controls.Add($header,0,0)
    $tabs=New-Object Windows.Forms.TabControl; $tabs.Dock='Fill'
    foreach ($entry in @(@('Conversation','Transcript'),@('Bridge activity','Bridge'))) {
        $tab=New-Object Windows.Forms.TabPage; $tab.Text=$entry[0]
        $box=New-Object Windows.Forms.RichTextBox
        $box.Dock='Fill'; $box.ReadOnly=$true; $box.DetectUrls=$false; $box.HideSelection=$false
        $box.BackColor=[Drawing.Color]::FromArgb(32,36,44); $box.ForeColor=[Drawing.Color]::FromArgb(231,235,241)
        $box.BorderStyle='None'; $box.Font=New-Object Drawing.Font('Segoe UI',11)
        $tab.Controls.Add($box); $tabs.TabPages.Add($tab); $view.Controls[$entry[1]]=$box
    }
    $layout.Controls.Add($tabs,0,1)
    $questions=New-Object Windows.Forms.FlowLayoutPanel
    $questions.Dock='Fill'; $questions.AutoScroll=$true; $questions.FlowDirection='TopDown'; $questions.WrapContents=$false; $questions.Visible=$false
    $layout.Controls.Add($questions,0,2); $view.Controls.Questions=$questions
    $attached=New-Object Windows.Forms.Label; $attached.Dock='Fill'; $attached.AutoEllipsis=$true
    $layout.Controls.Add($attached,0,3); $view.Controls.Attachments=$attached
    $inputBox=New-Object Windows.Forms.TextBox
    $inputBox.Dock='Fill'; $inputBox.Multiline=$true; $inputBox.AcceptsReturn=$true
    $inputBox.ScrollBars='Vertical'; $inputBox.MaxLength=16384
    $inputBox.BackColor=[Drawing.Color]::FromArgb(39,44,53); $inputBox.ForeColor=[Drawing.Color]::WhiteSmoke
    $layout.Controls.Add($inputBox,0,4); $view.Controls.Input=$inputBox
    $buttons=New-Object Windows.Forms.FlowLayoutPanel; $buttons.Dock='Fill'
    foreach ($entry in @(@('Send','Send',150),@('Continue','Continue',90),@('Attach','Attach...',90),@('Clear','Clear files',85),@('Interrupt','Interrupt',85),@('Automation','Resume automation',165))) {
        $button=New-Object Windows.Forms.Button; $button.Text=$entry[1]; $button.Width=$entry[2]; $button.Height=34
        $button.FlatStyle='Flat'; $buttons.Controls.Add($button); $view.Controls[$entry[0]]=$button
    }
    $layout.Controls.Add($buttons,0,5)
    $status=New-Object Windows.Forms.Label; $status.Dock='Fill'; $status.ForeColor=[Drawing.Color]::FromArgb(166,197,221)
    $layout.Controls.Add($status,0,6); $view.Controls.Status=$status
    $send = {
        try {
            Submit-WdOperatorConversationAction -View $view -Kind send -Text $view.Controls.Input.Text -Paths $view.AttachmentPaths
        } catch { Set-WdOperatorConversationStatus -View $view -Text ('Message not accepted: ' + $_.Exception.Message) }
    }.GetNewClosure()
    $view.Controls.Send.Add_Click($send)
    $view.Controls.Continue.Add_Click({
        try { Submit-WdOperatorConversationAction -View $view -Kind send -Text 'Continue.' }
        catch { Set-WdOperatorConversationStatus -View $view -Text ('Continue not accepted: ' + $_.Exception.Message) }
    }.GetNewClosure())
    $inputBox.Add_KeyDown({
        param($sender,$event)
        if ($event.Control -and $event.KeyCode -eq [Windows.Forms.Keys]::Enter) {
            $event.SuppressKeyPress=$true
            if ($view.Controls.Send.Enabled) { $view.Controls.Send.PerformClick() }
        }
    }.GetNewClosure())
    $view.Controls.Attach.Add_Click({
        $dialog=New-Object Windows.Forms.OpenFileDialog
        try {
            $dialog.Title='Attach local images as data'; $dialog.Multiselect=$true; $dialog.CheckFileExists=$true
            $dialog.Filter='Images (*.png;*.jpg;*.jpeg;*.webp;*.gif)|*.png;*.jpg;*.jpeg;*.webp;*.gif'
            if ($dialog.ShowDialog($view.Form) -eq [Windows.Forms.DialogResult]::OK) {
                $selected=@(Get-WdOperatorLocalAttachments -Paths $dialog.FileNames)
                if (@($selected | Where-Object { $_.kind -ne 'image' }).Count) { throw 'Only local image attachments are supported.' }
                $view.AttachmentPaths=@($dialog.FileNames); Sync-WdOperatorConversationView -View $view
            }
        } catch { Set-WdOperatorConversationStatus -View $view -Text ('Attachment not accepted: ' + $_.Exception.Message) }
        finally { $dialog.Dispose() }
    }.GetNewClosure())
    $view.Controls.Clear.Add_Click({ $view.AttachmentPaths=@(); Sync-WdOperatorConversationView -View $view }.GetNewClosure())
    $view.Controls.Interrupt.Add_Click({
        try { Submit-WdOperatorConversationAction -View $view -Kind interrupt }
        catch { Set-WdOperatorConversationStatus -View $view -Text ('Interrupt not accepted: ' + $_.Exception.Message) }
    }.GetNewClosure())
    $view.Controls.Automation.Add_Click({
        try { Submit-WdOperatorConversationAction -View $view -Kind automation_toggle -Enabled (-not $view.State.AutomationEnabled) }
        catch { Set-WdOperatorConversationStatus -View $view -Text ('Automation request not accepted: ' + $_.Exception.Message) }
    }.GetNewClosure())
    $form.Add_FormClosing({
        param($sender,$event)
        if (-not $view.Disposing) {
            $event.Cancel=$true
            Submit-WdOperatorConversationAction -View $view -Kind close
            $view.Form.Hide()
        }
    }.GetNewClosure())
    Sync-WdOperatorConversationView -View $view
    if (-not $Hidden) { $form.Show(); $inputBox.Focus() | Out-Null }
    return $view
}

function Update-WdOperatorConversationView {
    param([Parameter(Mandatory)] $View)
    if (-not $View.Headless -and -not $View.State.Closed) {
        [Windows.Forms.Application]::DoEvents()
        Sync-WdOperatorConversationView -View $View
    }
}

function Close-WdOperatorConversationView {
    param([Parameter(Mandatory)] $View)
    $View.Disposing=$true; $View.State.Closed=$true
    if (-not $View.Headless -and $null -ne $View.Form -and -not $View.Form.IsDisposed) {
        $View.Form.Close(); $View.Form.Dispose()
    }
    $View.Actions.Clear(); $View.PendingSends.Clear(); $View.CloseAction=$null; $View.State.Question=$null
}
