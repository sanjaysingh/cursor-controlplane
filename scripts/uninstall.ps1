# Uninstall cursor-controlplane on Windows: scheduled task, exe folder, data folder, PATH entry.
#
# Usage:
#   .\uninstall.ps1
#   .\uninstall.ps1 -Yes
#   $env:CONTROL_PLANE_UNINSTALL_YES = "1"; .\uninstall.ps1
#   .\uninstall.ps1 -KeepData   # remove task + binary only; keep %APPDATA%\cursor-controlplane\

param(
    [switch]$Yes,
    [switch]$KeepData
)

$ErrorActionPreference = "Stop"

if ($env:CONTROL_PLANE_UNINSTALL_YES -eq "1") {
    $Yes = $true
}

$destDir = Join-Path $env:LOCALAPPDATA "Programs\cursor-controlplane"
$exePath = Join-Path $destDir "cursor-controlplane.exe"
$taskName = "CursorControlPlane"

function Get-ControlPlaneDataDir {
    if ($env:CONTROL_PLANE_DATA_DIR) {
        return $env:CONTROL_PLANE_DATA_DIR
    }
    if ($env:APPDATA) {
        return (Join-Path $env:APPDATA "cursor-controlplane")
    }
    if ($env:LOCALAPPDATA) {
        return (Join-Path $env:LOCALAPPDATA "cursor-controlplane")
    }
    return (Join-Path $HOME "AppData\Roaming\cursor-controlplane")
}

$dataDir = Get-ControlPlaneDataDir
$legacyDataDir = Join-Path $env:LOCALAPPDATA "cursor-controlplane"

if ($env:CONTROL_PLANE_DATA_DIR) {
    $dataDir = $env:CONTROL_PLANE_DATA_DIR
}

if (-not $Yes) {
    $r = Read-Host "Uninstall cursor-controlplane (task, binary, data)? [y/N]"
    if ($r -notmatch '^[Yy]') {
        Write-Host "Aborted."
        exit 1
    }
}

# Stop scheduled task
try {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
} catch {
    $null
}
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

# Remove install folder (exe)
if (Test-Path $destDir) {
    Remove-Item -Recurse -Force $destDir
    Write-Host "Removed $destDir"
} else {
    Write-Host "Install folder not found: $destDir (skipping)"
}

# Remove data (DB, service.json)
if (-not $KeepData) {
    if (Test-Path $dataDir) {
        Remove-Item -Recurse -Force $dataDir
        Write-Host "Removed $dataDir"
    } else {
        Write-Host "Data folder not found: $dataDir (skipping)"
    }
    if ($legacyDataDir -and ($legacyDataDir -ne $dataDir) -and (Test-Path $legacyDataDir)) {
        Remove-Item -Recurse -Force $legacyDataDir
        Write-Host "Removed legacy data folder: $legacyDataDir"
    }
} else {
    Write-Host "Kept data folder: $dataDir"
}

# Remove PATH entry we added (install dir)
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -and $destDir) {
    $parts = $userPath -split ';' | Where-Object { $_ -and ($_ -ne $destDir) -and ($_.TrimEnd('\') -ne $destDir.TrimEnd('\')) }
    $newPath = ($parts -join ';').TrimEnd(';')
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    Write-Host "Updated user PATH (removed install directory if present)."
}

Write-Host "Uninstall finished. Open a new terminal if PATH changed."
