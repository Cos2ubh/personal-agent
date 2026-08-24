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
#     Unregister-ScheduledTask -TaskName "PersonalAgentNotifier" -Confirm:$false
# ----------------------------------------------------------------------------

# We check exit codes / result objects manually — don't want PS auto-throwing
# on schtasks' stderr writes.
$ErrorActionPreference = "Continue"

$taskName = "PersonalAgentNotifier"
$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$python = Join-Path $projectRoot "venv\Scripts\pythonw.exe"
$script = Join-Path $projectRoot "notifier.py"

if (-not (Test-Path $python)) {
    Write-Host "ERROR: pythonw.exe not found at $python. Is the venv set up?" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $script)) {
    Write-Host "ERROR: notifier.py not found at $script." -ForegroundColor Red
    exit 1
}

# Remove any existing task so re-runs are idempotent
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Removing existing task '$taskName'..."
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

# Build the task using PowerShell's ScheduledTasks module — argument quoting
# happens natively and correctly, no schtasks.exe string-parsing footguns.
Write-Host "Creating scheduled task '$taskName'..."

$action = New-ScheduledTaskAction -Execute $python -Argument "`"$script`""

# Fire every 5 minutes for the next 10 years — effectively forever for a
# personal-agent task. TimeSpan::MaxValue overflows Task Scheduler's XML.
$trigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(1)) `
    -RepetitionInterval (New-TimeSpan -Minutes 5) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

# Run whether or not the user is logged on. StartWhenAvailable so a missed
# fire (laptop asleep) catches up on wake.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2)

try {
    # Omit -Principal so the task inherits the current user's identity by default.
    Register-ScheduledTask `
        -TaskName $taskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Description "Personal Agent — polls the reminders DB and fires Windows toast for anything due." `
        -Force `
        -ErrorAction Stop | Out-Null
} catch {
    Write-Host "ERROR: Register-ScheduledTask failed: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Common cause: running from a non-elevated shell." -ForegroundColor Yellow
    Write-Host "Try right-click PowerShell -> Run as administrator, then retry." -ForegroundColor Yellow
    exit 1
}

Write-Host ""
Write-Host "Done. The notifier will run every 5 minutes and fire Windows toast" -ForegroundColor Green
Write-Host "notifications for any due reminders." -ForegroundColor Green
Write-Host ""
Write-Host "Test it now (one-shot):"
Write-Host "    Start-ScheduledTask -TaskName $taskName"
Write-Host ""
Write-Host "Check status:"
Write-Host "    Get-ScheduledTask -TaskName $taskName"
Write-Host ""
Write-Host "Remove later:"
Write-Host "    Unregister-ScheduledTask -TaskName $taskName -Confirm:`$false"
