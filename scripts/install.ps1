# Install latest cursor-controlplane.exe from GitHub Releases (Windows).
#
# Usage (run in PowerShell):
#   irm https://raw.githubusercontent.com/sanjaysingh/cursor-controlplane/main/scripts/install.ps1 | iex
#
# With background service (Scheduled Task at logon):
#   $env:CONTROL_PLANE_INSTALL_SERVICE = "1"
#   irm ... | iex
# Or download the script and run:
#   .\install.ps1 -WithService
#
# Override repo: $env:CONTROL_PLANE_REPO = "myorg/cursor-controlplane"

param(
    [switch]$WithService
)

$ErrorActionPreference = "Stop"

if ($env:CONTROL_PLANE_INSTALL_SERVICE -eq "1") {
    $WithService = $true
}

$repo = if ($env:CONTROL_PLANE_REPO) { $env:CONTROL_PLANE_REPO } else { "sanjaysingh/cursor-controlplane" }

$asset = "cursor-controlplane-windows-amd64.exe"
$url = "https://github.com/$repo/releases/latest/download/$asset"
$destDir = Join-Path $env:LOCALAPPDATA "Programs\cursor-controlplane"
$exePath = Join-Path $destDir "cursor-controlplane.exe"
$dataDir = Join-Path $env:LOCALAPPDATA "cursor-controlplane"
$taskName = "CursorControlPlane"

New-Item -ItemType Directory -Force -Path $destDir | Out-Null
Write-Host "Downloading $url ..."
Invoke-WebRequest -Uri $url -OutFile $exePath -UseBasicParsing

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$destDir*") {
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$destDir", "User")
    Write-Host "Added $destDir to user PATH. Open a new terminal or refresh PATH."
}

Write-Host "Installed $exePath"

New-Item -ItemType Directory -Force -Path $dataDir | Out-Null

if ($WithService) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

    $action = New-ScheduledTaskAction -Execute $exePath -Argument "serve" -WorkingDirectory $destDir
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue

    $marker = @{ type = "scheduled-task"; name = $taskName } | ConvertTo-Json -Compress
    Set-Content -Path (Join-Path $dataDir "service.json") -Value $marker -Encoding utf8

    Write-Host "Registered scheduled task: $taskName (run at logon; run: cursor-controlplane restart to reload config)"
    Write-Host "Configure with: cursor-controlplane configure"
}
