# Set up the Clean Your TV helper on a Windows PC from the unpacked kit (cyt-vidaa\ next to sidee\).
# Run:  powershell -ExecutionPolicy Bypass -File cyt-vidaa\setup\windows\setup-kit.ps1
# It asks for administrator rights (firewall and power settings), then:
#   Python + paho-mqtt in a venv, the TV pairing, Node + ffmpeg for Stremio, firewall ports, never sleep,
#   and a logon task that keeps the helper running.
$ErrorActionPreference = 'Stop'

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    # Elevate, passing who the real user is: the task and pairing belong to them, not to the admin account.
    Start-Process powershell -Verb RunAs -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-NoExit',
        '-File', "`"$PSCommandPath`"", '-ForUser', $env:USERNAME, '-ForHome', "`"$env:USERPROFILE`"")
    exit
}
$forUser = $env:USERNAME; $forHome = $env:USERPROFILE
for ($i = 0; $i -lt $args.Count; $i++) {
    if ($args[$i] -eq '-ForUser') { $forUser = $args[$i + 1] }
    if ($args[$i] -eq '-ForHome') { $forHome = $args[$i + 1].Trim('"') }
}

$dir = (Resolve-Path "$PSScriptRoot\..\..").Path          # ...\cyt-vidaa
$kit = Split-Path $dir
if (-not (Test-Path "$kit\sidee\core")) { throw "sidee\core is missing next to cyt-vidaa\" }
$ProgressPreference = 'SilentlyContinue'

Write-Host "1/6  Python"
$py = "$forHome\AppData\Local\Programs\Python\Python312\python.exe"
if (-not (Test-Path $py)) { $py = "$env:ProgramFiles\Python312\python.exe" }
if (-not (Test-Path $py)) {
    $exe = "$env:TEMP\python-3.12.10-amd64.exe"
    Invoke-WebRequest https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe -OutFile $exe
    $p = Start-Process $exe -Wait -PassThru -ArgumentList '/quiet', 'InstallAllUsers=1', 'PrependPath=1', 'Include_test=0'
    $py = "$env:ProgramFiles\Python312\python.exe"
    if (-not (Test-Path $py)) {
        throw "Python's installer failed (exit code $($p.ExitCode)). Run $exe by hand to see why, then run this again."
    }
}
if (-not (Test-Path "$dir\.venv\Scripts\python.exe")) { & $py -m venv "$dir\.venv" }
& "$dir\.venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check "paho-mqtt==2.1.0" yt-dlp yt-dlp-ejs truststore
$vpy = "$dir\.venv\Scripts\python.exe"

Write-Host "2/6  TV pairing"
New-Item -ItemType Directory -Force "$forHome\.sidee" | Out-Null
if ((Test-Path "$dir\setup\session.json") -and -not (Test-Path "$forHome\.sidee\session.json")) {
    Move-Item "$dir\setup\session.json" "$forHome\.sidee\session.json"
}
if (-not (Test-Path "$forHome\.sidee\session.json")) { Write-Warning "No pairing: run  $vpy $kit\sidee\sidee.py  and pair" }

Write-Host "3/6  Node + ffmpeg (Stremio's streaming server)"
if (Get-Command winget -ErrorAction SilentlyContinue) {
    foreach ($id in 'OpenJS.NodeJS.LTS', 'Gyan.FFmpeg') {
        winget install -e --id $id --scope machine --silent --accept-package-agreements --accept-source-agreements | Out-Null
    }
} else { Write-Warning "winget not found: install Node.js LTS and ffmpeg by hand for Stremio" }

Write-Host "4/6  Firewall: TV may reach 8765 (helper) and 11470 (Stremio)"
Get-NetFirewallRule -DisplayName 'Clean Your TV*' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName 'Clean Your TV helper' -Direction Inbound -Protocol TCP -LocalPort 8765, 11470 `
    -Action Allow -Profile Any | Out-Null
Get-NetConnectionProfile | Where-Object NetworkCategory -eq Public |
    ForEach-Object { Set-NetConnectionProfile -InterfaceIndex $_.InterfaceIndex -NetworkCategory Private }

Write-Host "5/6  Never sleep while plugged in; closing the lid does nothing"
powercfg /change standby-timeout-ac 0; powercfg /change hibernate-timeout-ac 0
powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
powercfg /setdcvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
powercfg /setactive SCHEME_CURRENT

Write-Host "6/6  Logon task"
$action = New-ScheduledTaskAction -Execute "$env:WINDIR\System32\conhost.exe" `
    -Argument "--headless `"$vpy`" -u `"$dir\setup\windows\run_helper.py`"" -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:COMPUTERNAME\$forUser"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'Clean Your TV helper' -Action $action -Trigger $trigger -Settings $settings `
    -Force -Description "Serves the TV launcher ($dir)." | Out-Null
Start-ScheduledTask -TaskName 'Clean Your TV helper'

$ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.PrefixOrigin -eq 'Dhcp' } | Select-Object -First 1).IPAddress
Write-Host ""
Write-Host "Done. This PC: $ip   Helper: http://${ip}:8765/   Log: $dir\data\helper.log" -ForegroundColor Green
Write-Host "Restart once so Node/ffmpeg are on the helper's PATH. Keep this account signed in (the helper starts at logon)."
