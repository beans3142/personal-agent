# Starts the agent at Windows sign-in. Run with -Remove to undo.
param([switch]$Remove)

$startup = [Environment]::GetFolderPath('Startup')
$entry = Join-Path $startup 'personal-agent.cmd'

if ($Remove) {
    Remove-Item $entry -ErrorAction SilentlyContinue
    Write-Host 'Autostart removed.'
    exit 0
}

$script = Join-Path $PSScriptRoot 'start-agent.ps1'
$lines = @(
    '@echo off',
    "start `"Discord Agent`" powershell -NoExit -NoProfile -ExecutionPolicy Bypass -File `"$script`""
)
Set-Content -Path $entry -Value $lines -Encoding ASCII
Write-Host "Autostart installed: $entry"
