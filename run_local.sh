#!/usr/bin/env bash
# Run the leaderboard notifier as a local daemon.
#
# Usage:
#   ./run_local.sh                  # polls every 60s
#   ./run_local.sh --dry-run        # same but no Discord posts
#   ./run_local.sh 30               # custom interval in seconds
#
# Requires DISCORD_WEBHOOK_URL in environment or .env file.
# State is stored in .local_state/ (gitignored).

set -euo pipefail
cd "$(dirname "$0")"

# Load .env if present
if [[ -f .env ]]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

if [[ -z "${DISCORD_WEBHOOK_URL:-}" ]]; then
    echo "Error: set DISCORD_WEBHOOK_URL in your environment or .env file." >&2
    exit 1
fi

INTERVAL="${1:-60}"
EXTRA_FLAGS=""

# If first arg is a flag (starts with -), treat it as extra flags
if [[ "$INTERVAL" == -* ]]; then
    EXTRA_FLAGS="$*"
    INTERVAL=60
else
    shift || true
    EXTRA_FLAGS="$*"
fi

STATE_DIR=".local_state"
mkdir -p "$STATE_DIR" data/snapshots data/timeseries

echo "Polling every ${INTERVAL}s. Press Ctrl+C to stop."
exec python leaderboard_notifier.py \
    --state-file "$STATE_DIR/leaderboard_state.json" \
    --structured-cache "$STATE_DIR/structured_snapshot.json" \
    --snapshot-dir data/snapshots \
    --timeseries-dir data/timeseries \
    --confirmation-checks 1 \
    --loop \
    --min-interval-seconds "$INTERVAL" \
    --max-interval-seconds "$INTERVAL" \
    $EXTRA_FLAGS
