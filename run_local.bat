@echo off
REM Run the leaderboard notifier as a local daemon on Windows.
REM
REM Usage:
REM   run_local.bat                 -- polls every 60s
REM   run_local.bat 30              -- polls every 30s
REM   run_local.bat --dry-run       -- polls every 60s, no Discord posts
REM   run_local.bat 30 --dry-run    -- polls every 30s, no Discord posts
REM
REM Requires DISCORD_WEBHOOK_URL in environment or .env file.
REM State is stored in .local_state\ (gitignored).

setlocal enabledelayedexpansion

REM Change to script directory so paths are relative to the repo root
cd /d "%~dp0"

REM Load .env if present
if exist .env (
    for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
        REM Skip blank lines and comments
        set "line=%%A"
        if defined line (
            if not "!line:~0,1!"=="#" (
                set "%%A=%%B"
            )
        )
    )
)

REM Validate webhook URL
if not defined DISCORD_WEBHOOK_URL (
    echo Error: set DISCORD_WEBHOOK_URL in your environment or .env file. 1>&2
    exit /b 1
)

REM Parse arguments: first arg can be an interval (number) or a flag
set "INTERVAL=60"
set "EXTRA_FLAGS="

if "%~1"=="" goto :run

REM Check if first arg starts with a dash (flag) or is a number (interval)
set "first=%~1"
if "!first:~0,1!"=="-" (
    set "EXTRA_FLAGS=%*"
) else (
    set "INTERVAL=%~1"
    shift
    :collect_flags
    if "%~1"=="" goto :run
    set "EXTRA_FLAGS=!EXTRA_FLAGS! %~1"
    shift
    goto :collect_flags
)

:run
REM Create state and data directories
if not exist .local_state mkdir .local_state
if not exist data\snapshots mkdir data\snapshots
if not exist data\timeseries mkdir data\timeseries

echo Polling every %INTERVAL%s. Press Ctrl+C to stop.

python leaderboard_notifier.py ^
    --state-file .local_state\leaderboard_state.json ^
    --structured-cache .local_state\structured_snapshot.json ^
    --snapshot-dir data\snapshots ^
    --timeseries-dir data\timeseries ^
    --confirmation-checks 1 ^
    --loop ^
    --min-interval-seconds %INTERVAL% ^
    --max-interval-seconds %INTERVAL% ^
    %EXTRA_FLAGS%
