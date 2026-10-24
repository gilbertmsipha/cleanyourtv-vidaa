# Start the helper at every logon, with no window, restarting it if it stops.
# Run from the repo:  powershell -ExecutionPolicy Bypass -File setup\windows\install.ps1
# Remove it again:    Unregister-ScheduledTask -TaskName 'Clean Your TV helper' -Confirm:$false
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path "$PSScriptRoot\..\..").Path
$py = (Get-Command python).Source

# python.exe, not pythonw.exe: Windows' firewall prompt is answered per program, and python.exe is the one that
# was allowed when the helper first ran. conhost --headless keeps it from opening a window.
$action = New-ScheduledTaskAction -Execute "$env:WINDIR\System32\conhost.exe" `
    -Argument "--headless `"$py`" -u `"$root\setup\windows\run_helper.py`"" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'Clean Your TV helper' -Action $action -Trigger $trigger -Settings $settings -Force `
    -Description "Serves the TV launcher and opens it when the TV comes on or an app closes ($root)." | Out-Null
Start-ScheduledTask -TaskName 'Clean Your TV helper'
Write-Host "Installed. Log: $root\data\helper.log"
Write-Host "Run the helper once by hand first (python helper\cyt_helper.py) so Windows asks to allow it through the firewall."
