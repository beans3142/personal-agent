# Keeps the Discord agent running in this window. Restarts it when it exits.
# -Fresh starts the first run with a new conversation.
param(
    [switch]$Fresh,
    [string]$DebugFile
)

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$mutex = New-Object System.Threading.Mutex($false, 'Local\personal-agent-discord')
if (-not $mutex.WaitOne(0)) {
    Write-Host 'Agent is already running.'
    exit 0
}

$claudeArgs = @(
    '--settings', (Join-Path $PSScriptRoot 'agent-settings.json'),
    '--setting-sources', 'project,local',
    '--channels', 'plugin:discord@claude-plugins-official'
)
if ($DebugFile) {
    $claudeArgs += @('--debug-file', $DebugFile)
}

try {
    while ($true) {
        $started = Get-Date
        if ($Fresh) {
            $Fresh = $false
            & claude @claudeArgs
        }
        else {
            & claude @claudeArgs --continue
            # --continue fails at once when this folder has no earlier conversation.
            if (((Get-Date) - $started).TotalSeconds -lt 10) {
                & claude @claudeArgs
            }
        }
        Write-Host "Agent exited at $(Get-Date). Restarting in 10 seconds. Press Ctrl-C to stop."
        Start-Sleep -Seconds 10
    }
}
finally {
    $mutex.ReleaseMutex()
}
