# Fix HP Thunderbolt Dock - must run as Administrator
Write-Host "=== HP Thunderbolt Dock Reset ==="

# 1. Disable and re-enable the Thunderbolt controller
$tb = Get-PnpDevice | Where-Object {$_.FriendlyName -match "Thunderbolt.*Controller"}
foreach ($dev in $tb) {
    Write-Host "Resetting: $($dev.FriendlyName)"
    Disable-PnpDevice -InstanceId $dev.InstanceId -Confirm:$false -ErrorAction SilentlyContinue
    Start-Sleep 3
    Enable-PnpDevice -InstanceId $dev.InstanceId -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "  Re-enabled"
}

Start-Sleep 5

# 2. Rescan
Write-Host "Scanning devices..."
pnputil /scan-devices

Start-Sleep 5

# 3. Check results
Write-Host ""
Write-Host "=== Dock Status ==="
Get-PnpDevice | Where-Object {$_.FriendlyName -match "Thunderbolt|Dock"} | Select-Object Status,FriendlyName | Format-Table -AutoSize

Write-Host ""
Write-Host "=== Volumes ==="
Get-Volume | Format-Table DriveLetter,FileSystemLabel,SizeRemaining,Size -AutoSize

Write-Host ""
Write-Host "=== Corsair Status ==="
Get-PnpDevice | Where-Object {$_.FriendlyName -match "Corsair"} | Select-Object Status,FriendlyName,Problem | Format-Table -AutoSize
