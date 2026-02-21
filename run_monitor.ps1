# PowerShell wrapper that runs leaderboard_notifier.py and sends a Discord
# alert (then auto-restarts) whenever the process exits unexpectedly.
#
# Usage:
#   .\run_monitor.ps1                 # polls every 60s
#   .\run_monitor.ps1 30              # polls every 30s
#   .\run_monitor.ps1 --dry-run       # polls every 60s, no Discord posts
#   .\run_monitor.ps1 30 --dry-run    # both combined
#
# Requires DISCORD_WEBHOOK_URL in environment or .env file.
# State is stored in .local_state\ (gitignored).

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Change to script directory so paths are relative to repo root
Set-Location $PSScriptRoot

# Load .env if present
if (Test-Path ".env") {
    Get-Content ".env" | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
            $key, $value = $line -split "=", 2
            [Environment]::SetEnvironmentVariable($key.Trim(), $value.Trim(), "Process")
        }
    }
}

$webhookUrl = $env:DISCORD_WEBHOOK_URL
if (-not $webhookUrl) {
    Write-Error "Error: set DISCORD_WEBHOOK_URL in your environment or .env file."
    exit 1
}

# Parse arguments: optional leading number is the interval, rest are flags
$interval = 60
$extraFlags = @()

if ($args.Count -gt 0) {
    if ($args[0] -match '^\d+$') {
        $interval = [int]$args[0]
        $extraFlags = @($args | Select-Object -Skip 1)
    } else {
        $extraFlags = @($args)
    }
}

# Create state and data directories
foreach ($dir in ".local_state", "data\snapshots", "data\timeseries") {
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
}

$baseArgs = @(
    "leaderboard_notifier.py",
    "--state-file", ".local_state\leaderboard_state.json",
    "--structured-cache", ".local_state\structured_snapshot.json",
    "--snapshot-dir", "data\snapshots",
    "--timeseries-dir", "data\timeseries",
    "--confirmation-checks", "1",
    "--loop",
    "--min-interval-seconds", "$interval",
    "--max-interval-seconds", "$interval"
) + $extraFlags

$restartDelay = 30
$centralTz = [TimeZoneInfo]::FindSystemTimeZoneById("Central Standard Time")

function Get-CentralTime {
    $ct = [TimeZoneInfo]::ConvertTimeFromUtc([DateTime]::UtcNow, $centralTz)
    $abbr = if ($centralTz.IsDaylightSavingTime($ct)) { "CDT" } else { "CST" }
    return "$($ct.ToString('yyyy-MM-dd HH:mm:ss')) $abbr"
}

Write-Host "Polling every ${interval}s with crash monitoring. Press Ctrl+C to stop."

while ($true) {
    $startTime = Get-CentralTime
    Write-Host "`nStarting leaderboard_notifier.py at $startTime"

    python @baseArgs
    $exitCode = $LASTEXITCODE

    $timestamp = Get-CentralTime
    Write-Host "leaderboard_notifier.py exited with code $exitCode at $timestamp" -ForegroundColor Red

    # Send a Discord alert about the crash
    $body = @{
        content = "leaderboard_notifier.py exited with code $exitCode at $timestamp. Restarting in ${restartDelay}s..."
    } | ConvertTo-Json -Compress
    try {
        Invoke-RestMethod -Uri $webhookUrl -Method Post -ContentType "application/json" -Body $body
        Write-Host "Discord crash alert sent."
    } catch {
        Write-Warning "Failed to send Discord crash alert: $_"
    }

    Write-Host "Restarting in ${restartDelay}s..."
    Start-Sleep -Seconds $restartDelay
}
