#requires -Version 5.1
<# Synthetic protocol model only. No controller, subprocess, pipe or worker is
   started here. Native handle retention, lane authentication, and a hard OS
   deadline are not established. Production integration is intentionally off. #>
function Start-WdMindContentController {
    [CmdletBinding()]
    param([switch] $Enabled)
    throw 'mind_content_controller_not_implemented'
}
