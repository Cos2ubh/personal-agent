# install_notifier.ps1
# ----------------------------------------------------------------------------
# Registers the Personal Agent notifier with Windows Task Scheduler so due
# reminders fire as Windows toast notifications every 5 minutes — even when
# the agent chat isn't open.
#
# Usage:
#     powershell -ExecutionPolicy Bypass -File scripts\install_notifier.ps1
#
# To remove:
#     schtasks /Delete /TN "PersonalAgentNotifier" /F
# ----------------------------------------------------------------------------

$ErrorActionPreference = "Stop"

$taskName = "PersonalAgentNotifier"
$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$python = Join-Path $projectRoot "venv\Scripts\pythonw.exe"
$script = Join-Path $projectRoot "notifier.py"

if (-not (Test-Path $python)) {
    Write-Error "pythonw.exe not found at $python. Is the venv set up?"
    exit 1
}
if (-not (Test-Path $script)) {
    Write-Error "notifier.py not found at $script."
    exit 1
}

# Remove any existing task with the same name so re-runs are idempotent
schtasks /Query /TN $taskName 2>$null > $null
if ($LASTEXITCODE -eq 0) {
    Write-Host "Removing existing task '$taskName'..."
    schtasks /Delete /TN $taskName /F | Out-Null
}

# Create a new task: run every 5 minutes, indefinitely, as the current user
Write-Host "Creating scheduled task '$taskName'..."
$action = "`"$python`" `"$script`""

schtasks /Create `
    /TN $taskName `
    /TR $action `
    /SC MINUTE `
    /MO 5 `
    /F | Out-Null

if ($LASTEXITCODE -ne 0) {
    Write-Error "schtasks create failed. You may need to run this from an elevated PowerShell."
    exit 1
}

Write-Host ""
Write-Host "Done. The notifier will run every 5 minutes and fire Windows toast" -ForegroundColor Green
Write-Host "notifications for any due reminders." -ForegroundColor Green
Write-Host ""
Write-Host "Test it now (one-shot):"
Write-Host "    schtasks /Run /TN $taskName"
Write-Host ""
Write-Host "Check status:"
Write-Host "    schtasks /Query /TN $taskName"
Write-Host ""
Write-Host "Remove later:"
Write-Host "    schtasks /Delete /TN $taskName /F"
