# Fix HP Thunderbolt Dock G4 - direct USB device reset
# Must run as Administrator
Write-Host "=== HP Dock G4 Direct Reset ==="

# Target the dock USB device specifically
$dock = Get-PnpDevice | Where-Object {$_.FriendlyName -match "HP Thunderbolt Dock"}
foreach ($dev in $dock) {
    Write-Host "Disabling: $($dev.FriendlyName) [$($dev.InstanceId)]"
    Disable-PnpDevice -InstanceId $dev.InstanceId -Confirm:$false -ErrorAction Stop
    Write-Host "  Disabled. Waiting 3s..."
    Start-Sleep 3
    Write-Host "  Enabling..."
    Enable-PnpDevice -InstanceId $dev.InstanceId -Confirm:$false -ErrorAction Stop
    Write-Host "  Enabled."
}

# Also reset all USB host controllers (this re-enumerates all USB devices)
Write-Host ""
Write-Host "Resetting USB host controllers..."
$hcs = Get-PnpDevice | Where-Object {$_.FriendlyName -match "eXtensible Host Controller" -and $_.Status -eq "OK"}
foreach ($hc in $hcs) {
    Write-Host "  Resetting: $($hc.FriendlyName)"
    Disable-PnpDevice -InstanceId $hc.InstanceId -Confirm:$false -ErrorAction SilentlyContinue
    Start-Sleep 2
    Enable-PnpDevice -InstanceId $hc.InstanceId -Confirm:$false -ErrorAction SilentlyContinue
}

Write-Host "Waiting 10s for re-enumeration..."
Start-Sleep 10
pnputil /scan-devices
Start-Sleep 3

Write-Host ""
Write-Host "=== Results ==="
Get-PnpDevice | Where-Object {$_.FriendlyName -match "Thunderbolt|Dock|Corsair"} | Select-Object Status,FriendlyName | Format-Table -AutoSize
Get-Volume | Format-Table DriveLetter,FileSystemLabel,SizeRemaining,Size -AutoSize
