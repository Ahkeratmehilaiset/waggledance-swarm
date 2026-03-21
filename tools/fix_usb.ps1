# Fix Corsair USB drive - must run as Administrator
Write-Host "=== Corsair USB Fix ==="

# Disable and re-enable UAS Mass Storage devices
$uas = Get-PnpDevice | Where-Object {$_.FriendlyName -match "UAS Mass Storage"}
foreach ($dev in $uas) {
    Write-Host "Resetting: $($dev.FriendlyName) [$($dev.InstanceId)]"
    Disable-PnpDevice -InstanceId $dev.InstanceId -Confirm:$false -ErrorAction SilentlyContinue
    Start-Sleep 2
    Enable-PnpDevice -InstanceId $dev.InstanceId -Confirm:$false -ErrorAction SilentlyContinue
}

# Also try enabling Corsair disk devices directly
$corsair = Get-PnpDevice | Where-Object {$_.FriendlyName -match "Corsair"}
foreach ($dev in $corsair) {
    Write-Host "Enabling: $($dev.FriendlyName) [$($dev.Status)]"
    Enable-PnpDevice -InstanceId $dev.InstanceId -Confirm:$false -ErrorAction SilentlyContinue
}

# Rescan
Write-Host "Scanning devices..."
pnputil /scan-devices

Start-Sleep 3
Write-Host ""
Write-Host "=== Volumes ==="
Get-Volume | Format-Table DriveLetter,FileSystemLabel,SizeRemaining,Size -AutoSize

Write-Host ""
Write-Host "=== Corsair Status ==="
Get-PnpDevice | Where-Object {$_.FriendlyName -match "Corsair"} | Select-Object Status,FriendlyName | Format-Table -AutoSize
