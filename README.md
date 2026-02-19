# LLM-Leaderboard

Monitor the [Arena LLM leaderboard](https://arena.ai/leaderboard/text/overall-no-style-control) and send a message to a Discord channel when the rankings change.

In addition to detecting *that* the leaderboard changed, the structured time series system extracts *what* changed — rank movements, Elo score deltas, confidence interval shifts, vote accumulation, and new model arrivals — and stores historical snapshots for analysis.

## Local setup (recommended for fast polling)

GitHub Actions cron is best-effort and can delay runs by 30–60+ minutes. For time-sensitive monitoring, run the notifier locally. It polls continuously and sends Discord notifications within seconds of a change.

### Prerequisites

- **Python 3.10+** — check with `python --version` (or `python3 --version` on macOS/Linux).
- **A Discord webhook URL** — from your Discord channel's **Integrations → Webhooks** page.
- No additional Python packages are required (stdlib only).

### Windows setup

1. **Clone the repo** (Git Bash, PowerShell, or CMD):

   ```
   git clone https://github.com/acarter881/LLM-Leaderboard.git
   cd LLM-Leaderboard
   ```

2. **Set your webhook** as an environment variable. Pick one method:

   - **Option A — `.env` file** (used by `run_local.sh` if you run via Git Bash):

     Create a file called `.env` in the repo root with one line:
     ```
     DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/YOUR_ID/YOUR_TOKEN
     ```

   - **Option B — PowerShell session variable** (simplest for a quick test):

     ```powershell
     $env:DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/YOUR_ID/YOUR_TOKEN"
     ```

   - **Option C — Permanent Windows environment variable** (persists across reboots):

     ```
     Settings → System → About → Advanced system settings → Environment Variables
     ```
     Add a **User variable** named `DISCORD_WEBHOOK_URL` with your webhook URL as the value.

3. **Create the local state directory:**

   ```
   mkdir .local_state
   ```

4. **Run the notifier** (PowerShell or CMD):

   ```
   python leaderboard_notifier.py --loop --confirmation-checks 1 --min-interval-seconds 60 --max-interval-seconds 60 --state-file .local_state\leaderboard_state.json --structured-cache .local_state\structured_snapshot.json
   ```

   To poll every 30 seconds instead:

   ```
   python leaderboard_notifier.py --loop --confirmation-checks 1 --min-interval-seconds 30 --max-interval-seconds 30 --state-file .local_state\leaderboard_state.json --structured-cache .local_state\structured_snapshot.json
   ```

   To do a dry run first (no Discord post):

   ```
   python leaderboard_notifier.py --loop --confirmation-checks 1 --min-interval-seconds 60 --max-interval-seconds 60 --state-file .local_state\leaderboard_state.json --structured-cache .local_state\structured_snapshot.json --dry-run
   ```

5. **Leave the terminal open.** Press `Ctrl+C` to stop. The notifier is resilient to transient network errors and will retry with backoff.

### macOS / Linux setup

1. Clone the repo and `cd` into it.
2. Create a `.env` file:
   ```bash
   echo 'DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/YOUR_ID/YOUR_TOKEN' > .env
   ```
3. Run:
   ```bash
   ./run_local.sh        # polls every 60s
   ./run_local.sh 30     # polls every 30s
   ./run_local.sh --dry-run  # test without posting to Discord
   ```

### Local state files

Local runs store state in `.local_state/` (gitignored):
- `.local_state/leaderboard_state.json` — hash-based change detection
- `.local_state/structured_snapshot.json` — latest structured snapshot for diffing

These are separate from the GitHub Actions state, so both can run simultaneously without conflict.

## Cloud setup (GitHub Actions)

GitHub Actions is kept as a **backup**. It runs every 5 minutes (best-effort) and does not require any local machine.

### 1) Add your Discord webhook secret

In your GitHub repository:

1. Go to **Settings → Secrets and variables → Actions**.
2. Create a new repository secret named `DISCORD_WEBHOOK_URL`.
3. Set it to your Discord webhook URL.

### 2) Enable and run the workflow

The workflow file is at `.github/workflows/leaderboard-notifier.yml`.

It supports:

- **Scheduled runs** every 5 minutes (best-effort; GitHub may delay during high load).
- **Manual runs** via **Actions → Arena Leaderboard Notifier → Run workflow**.
  - Optional `force_send` input for webhook delivery testing.
  - Optional `dry_run` input to validate hashing/change detection without posting to Discord.

Each run does a single check and finishes in under a minute.

### 3) State persistence in the cloud

GitHub runners are ephemeral, so the workflow saves and restores state using the GitHub Actions cache:

- State paths:
  - `.github/state/leaderboard_state.json` — hash-based change detection state
  - `.github/state/structured_snapshot.json` — latest structured snapshot (used for diffing)
- Data paths (also cached):
  - `data/snapshots/` — full gzipped JSON snapshots (one per detected change)
  - `data/timeseries/top20.jsonl` — append-only compact time series for top 20 models
- Cache prefix: `leaderboard-state-`

This keeps change detection and historical data consistent between scheduled runs without any local machine.

## Architecture

The system has two layers that run alongside each other:

1. **Hash-based change detection** (`leaderboard_notifier.py`) — fast, reliable signal that *something* changed. Normalizes page HTML, hashes it, compares to cached state. This is the primary trigger for notifications.

2. **Structured time series** — when a change is detected, the structured parser extracts detailed data and computes a diff showing *what* changed. This adds context to notifications and builds a historical record.

### Module overview

| Module | Purpose |
|--------|---------|
| `leaderboard_notifier.py` | Entrypoint. Hash-based detection + orchestration |
| `leaderboard_parser.py` | HTML → structured data. Rank spread parsing |
| `snapshot_store.py` | JSON snapshot files, JSONL time series, cache I/O |
| `snapshot_diff.py` | Structured diffs between snapshots, Discord formatting |
| `analytics.py` | CLI tool to query historical time series data |

### Structured data extracted per model

| Field | Example | Notes |
|-------|---------|-------|
| `rank` | `1` | Integer position in table |
| `rank_ub` | `1` | Rank upper bound (primary settlement criterion) |
| `rank_lb` | `2` | Rank lower bound |
| `rank_spread_raw` | `"12"` | Raw concatenated rank CI from the page |
| `model_name` | `"claude-opus-4-6-thinking"` | Model identifier |
| `organization` | `"Anthropic"` | Company/org if available |
| `license` | `"Proprietary"` | License type |
| `score` | `1504` | Arena/Elo score |
| `ci` | `10` | ± confidence interval on score |
| `votes` | `3922` | Number of votes/battles |
| `is_preliminary` | `false` | Whether model has "Preliminary" tag |
| `model_url` | `"https://..."` | Link from model name |

### Rank spread parsing

The Arena leaderboard encodes rank confidence interval bounds as a single concatenated number with no delimiter. For example, `1634` means rank UB = 16, rank LB = 34.

The parser tries every possible split position and scores each candidate based on CI width and distance from the model's actual rank. Narrow CIs that contain the model rank are preferred. A small overshoot (rank slightly outside the CI) is tolerated over an absurdly wide exact-fit CI.

Examples:

| Raw | Rank UB | Rank LB |
|-----|---------|---------|
| `12` | 1 | 2 |
| `615` | 6 | 15 |
| `1634` | 16 | 34 |
| `74104` | 74 | 104 |
| `304305` | 304 | 305 |

### Diff detection

When a change is confirmed, the diff engine compares the previous and current structured snapshots and detects:

- New models added / models removed
- Rank changes (model moved up or down)
- **Rank UB changes** (settlement-critical, highlighted in Discord notifications)
- Score changes (with delta)
- CI changes (widened or narrowed)
- Vote count changes (with delta)
- Preliminary status changes
- Leaderboard date refreshes

### Storage

- **Full snapshots**: `data/snapshots/YYYYMMDD_HHMMSS.json.gz` — gzipped JSON (~5x compression). Only stored when data actually changed.
- **Top-20 time series**: `data/timeseries/top20.jsonl` — one JSON line appended per snapshot with compact model data.
- Both directories are persisted via GitHub Actions cache between runs.

## Script details

`leaderboard_notifier.py` checks a target page URL, hashes normalized page text, compares it with the previous hash from a state file, and sends a Discord webhook message when a change is detected. To reduce noisy flip-flop alerts from transient upstream variants, a new fingerprint must be seen for consecutive checks before it is announced.

When run locally, the notifier automatically creates `leaderboard_state.json` in the repository root (or at the path you pass via `--state-file`) after the first successful check.

To reduce false positives, hashing focuses on a narrower leaderboard-specific HTML region identified by stable anchors (for example leaderboard title text, leaderboard container markers, and common table section labels). During normalization, dynamic or non-semantic content (timestamps, tracking-related snippets, and broad nav/footer/aside regions) is stripped before whitespace collapsing and HTML unescaping.

If focused extraction fails, the script falls back to whole-page normalization and prints a warning to stderr so operators can still monitor changes without silently missing checks.

Snapshot parsing also ignores rows where the "model" field is numeric-only (a common non-leaderboard table artifact), and prefers tables that explicitly contain rank/model headers. This reduces false "rank movement" alerts caused by unrelated page metadata tables.

Useful options:

```bash
python leaderboard_notifier.py --dry-run
python leaderboard_notifier.py --force-send --max-checks 1
python leaderboard_notifier.py --state-file /path/to/state.json
python leaderboard_notifier.py --url https://arena.ai/leaderboard/text/overall-no-style-control
python leaderboard_notifier.py --loop --min-interval-seconds 30 --max-interval-seconds 30 --max-checks 2
python leaderboard_notifier.py --retries 3 --retry-backoff-seconds 2
python leaderboard_notifier.py --confirmation-checks 2
python leaderboard_notifier.py --no-structured  # hash-only mode, no structured parsing
python leaderboard_notifier.py --snapshot-dir ./my-snapshots --timeseries-dir ./my-timeseries
```

### Quick testing checklist

- Test message delivery (requires `DISCORD_WEBHOOK_URL`):

  ```bash
  python leaderboard_notifier.py --force-send --max-checks 1
  ```

- Test hashing/change detection without posting to Discord:

  ```bash
  python leaderboard_notifier.py --dry-run --force-send --max-checks 1
  ```


When `--force-send` is used and no change is detected, the script now sends a clearly labeled force-send test message instead of a diff-style change report.

- In GitHub Actions, run the workflow manually and set:
  - `force_send: true` to verify Discord delivery using your repository secret.
  - `dry_run: true` to verify logic without sending a message.


### Retry behavior for transient network failures

The notifier automatically retries temporary network failures for both leaderboard fetches and Discord delivery attempts.

- Retryable failures: timeouts, `URLError` network failures, and HTTP `5xx` responses.
- Non-retryable failures: invalid webhook configuration and HTTP `4xx` client/auth/permission errors.
- Retry logging: each retry prints the attempt count, delay, and failure reason to stderr.

CLI flags:

- `--retries` (default: `3`) — number of retry attempts after the initial request fails.
- `--retry-backoff-seconds` (default: `2`) — base backoff delay in seconds; each retry doubles the delay.
- `--confirmation-checks` (default: `2`; workflow uses `1`) — number of consecutive checks that must observe a new fingerprint before notification. Set to `1` for immediate detection.

## Analytics CLI

`analytics.py` provides subcommands for querying the stored time series data:

```bash
# Vote accumulation rate for a model over the last 7 days
python analytics.py vote-rate claude-opus-4-6-thinking --days 7

# When did a model's CI drop below ±5?
python analytics.py ci-threshold claude-opus-4-6-thinking --threshold 5

# Elo score trajectory for the top 5 models over 30 days
python analytics.py score-trajectory --top-n 5 --days 30

# Specific models
python analytics.py score-trajectory --models claude-opus-4-6-thinking gpt-4.5 --days 14

# Which models changed Rank UB in the last 7 days?
python analytics.py rank-ub-changes --days 7

# How long has the current #1 held the top position?
python analytics.py days-at-top
```

All subcommands read from the JSONL time series file. Use `--timeseries-dir` to point at a custom directory.

## Tests

Run the full test suite:

```bash
python -m unittest discover -s tests -v
```

Tests cover rank spread parsing (all documented examples), HTML table parsing, structured diff logic, snapshot storage round-trips, and Discord message formatting.

## Troubleshooting

### `Failed to send Discord message: HTTP Error 403: Forbidden`

If your workflow reaches `Run leaderboard notifier` and fails with HTTP 403, the GitHub Actions setup is usually fine and the request is reaching Discord. A 403 response means the webhook URL itself is being rejected.

Check the following:

1. **Secret name matches exactly**: `DISCORD_WEBHOOK_URL` (already correct in this repo and workflow).
2. **Secret value is the full Discord webhook URL** from your channel's **Integrations → Webhooks** page.
3. **Webhook is still active** (not deleted/regenerated).
4. **No extra characters** were copied into the secret (spaces/newlines/quotes).
5. **Run once with** `force_send: true` and `dry_run: false` to verify delivery.

Tip: if this still returns 403, regenerate the Discord webhook and update the repository secret with the newly generated URL.

### Better error output for webhook failures

The notifier now prints Discord's response body for HTTP errors (when available). This helps distinguish common cases like:

- `{"message": "Unknown Webhook", "code": 10015}`
- `{"message": "Missing Permissions", "code": 50013}`

It also trims whitespace around the configured webhook URL and validates that it looks like an HTTPS Discord webhook URL before sending.
