param([Parameter(Mandatory=$true)][ValidatePattern('^[A-Za-z0-9_-]{40,64}$')][string]$Code)
$ErrorActionPreference = 'Stop'
$relayUrl = 'https://game.daehoon.games/pc-power'

# Use an installed Python; install per-user Python when winget is available.
$pythonPath = $null
foreach ($candidate in @('python.exe','py.exe')) {
    $candidateCommand = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($candidateCommand) {
        try {
            $candidatePath = & $candidateCommand.Source -c 'import sys; print(sys.executable)' 2>$null
            if ($LASTEXITCODE -eq 0 -and $candidatePath -and (Test-Path -LiteralPath $candidatePath)) { $pythonPath = $candidatePath; break }
        } catch {}
    }
}
if (-not $pythonPath) {
    if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) { throw 'Python 3 is required. Install Python from python.org, then run this registration command again.' }
    & winget.exe install --id Python.Python.3.13 -e --scope user --silent --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) { throw 'Python installation failed; PC registration was not consumed.' }
    $pythonPath = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'
    if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Python installation path was not found.' }
}
$clientBytes = (Invoke-WebRequest -UseBasicParsing -Uri ($relayUrl+'/client.py') -MaximumRedirection 0).Content
if ($clientBytes -isnot [string]) { $clientBytes = [Text.Encoding]::UTF8.GetString($clientBytes) }
$registration = Invoke-RestMethod -Uri ($relayUrl+'/enroll') -Method Post -ContentType 'application/json' -Body (@{code=$Code}|ConvertTo-Json) -MaximumRedirection 0
if ($registration.node -notmatch '^pc-[a-f0-9]{16}$' -or $registration.url -ne $relayUrl) { throw 'Invalid enrollment response.' }
$sha = [Security.Cryptography.SHA256]::Create()
$actualHash = ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($clientBytes)))).Replace('-','').ToLowerInvariant()
if ($actualHash -ne $registration.sha256) { throw 'Client integrity check failed. Remove this PC registration in Discord and register again.' }
$pcFolder = Join-Path $env:USERPROFILE ('.daevis\pcs\'+$registration.node)
New-Item -ItemType Directory -Path $pcFolder -Force | Out-Null
$utf8 = New-Object Text.UTF8Encoding($false)
[IO.File]::WriteAllText((Join-Path $pcFolder 'device-node.py'),$clientBytes,$utf8)
[IO.File]::WriteAllText((Join-Path $pcFolder 'node-token'),$registration.token,$utf8)
$nodeConfig = @{url=$relayUrl;node=$registration.node;token_file=(Join-Path $pcFolder 'node-token');actions=@('text_message','pc_shutdown','pc_cancel_shutdown');allow_shutdown=$true;poll_seconds=5}
[IO.File]::WriteAllText((Join-Path $pcFolder 'node.json'),($nodeConfig|ConvertTo-Json),$utf8)
$runner = @'
import msvcrt,sys,runpy,os
from pathlib import Path
root=Path(__file__).resolve().parent
lock=(root/'runner.lock').open('a+b')
lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
except OSError:raise SystemExit(0)
sys.stdout=(root/'node.log').open('a',encoding='utf-8',buffering=1)
sys.stderr=(root/'node-error.log').open('a',encoding='utf-8',buffering=1)
(root/'node.pid').write_text(str(os.getpid()))
sys.argv=[str(root/'device-node.py'),'--config',str(root/'node.json')]
runpy.run_path(str(root/'device-node.py'),run_name='__main__')
'@
[IO.File]::WriteAllText((Join-Path $pcFolder 'run-node.py'),$runner,$utf8)
$launchCommand = '"'+$pythonPath+'" "'+(Join-Path $pcFolder 'run-node.py')+'"'
$vbs = 'CreateObject("WScript.Shell").Run "'+$launchCommand.Replace('"','""')+'", 0, False'
$vbsPath = Join-Path $pcFolder 'start-node.vbs'
[IO.File]::WriteAllText($vbsPath,$vbs,$utf8)
New-ItemProperty -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name ('DaevisPC_'+$registration.node) -Value ('wscript.exe "'+$vbsPath+'"') -PropertyType String -Force | Out-Null
Start-Process -FilePath wscript.exe -ArgumentList ('"'+$vbsPath+'"') -WindowStyle Hidden
Write-Output ('Registered '+$registration.label+'. The PC client runs while this Windows user is logged in. No shutdown was performed.')
