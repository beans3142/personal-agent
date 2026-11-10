# Installs the Daevis PC voice assistant for the current Windows user.
# Run in PowerShell from the repository root:  .\deploy\install-voice.ps1
# Remove:  .\deploy\install-voice.ps1 -Uninstall
param([switch]$Uninstall)
$ErrorActionPreference = 'Stop'
$dir = Join-Path $env:USERPROFILE '.daevis\voice'
$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'

Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'voice-pc\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
if ($Uninstall) {
    Remove-ItemProperty -LiteralPath $runKey -Name 'DaevisVoice' -ErrorAction SilentlyContinue
    Write-Output 'Daevis voice stopped and removed from login startup. Files in ~/.daevis/voice were kept.'
    return
}
if (-not (Get-ChildItem (Join-Path $env:USERPROFILE '.daevis\pcs\*\node.json') -ErrorAction SilentlyContinue)) {
    throw 'This PC is not registered. Register it first with "PC 등록" in Discord.'
}
$python = & python -c "import sys; print(sys.executable)"
& python -m pip install --user --quiet sounddevice faster-whisper nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"
if ($LASTEXITCODE -ne 0) { throw 'Python package installation failed.' }
New-Item -ItemType Directory -Force -Path $dir | Out-Null
Copy-Item (Join-Path $PSScriptRoot 'voice-pc.py') (Join-Path $dir 'voice-pc.py') -Force
$command = '"' + $python + '" "' + (Join-Path $dir 'voice-pc.py') + '"'
$vbsPath = Join-Path $dir 'start-voice.vbs'
[IO.File]::WriteAllText($vbsPath, 'CreateObject("WScript.Shell").Run "' + $command.Replace('"', '""') + '", 0, False', (New-Object Text.UTF8Encoding($false)))
New-ItemProperty -LiteralPath $runKey -Name 'DaevisVoice' -Value ('wscript.exe "' + $vbsPath + '"') -PropertyType String -Force | Out-Null
Start-Process wscript.exe -ArgumentList ('"' + $vbsPath + '"')
Write-Output 'Daevis voice is listening. Say "대비스, ..." — a beep means it heard you. It starts again at every Windows login.'
Write-Output 'Log (no transcripts): ~/.daevis/voice/voice.log'
