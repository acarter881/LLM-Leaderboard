# LLM-Leaderboard

Monitor the [Arena LLM leaderboard](https://arena.ai/leaderboard/text/overall-no-style-control) and send a message to a Discord channel when the page content changes.

## Cloud-only setup (GitHub Actions)

This repository is configured to run **entirely in GitHub Actions** so you do not need to run the notifier locally.

### 1) Add your Discord webhook secret

In your GitHub repository:

1. Go to **Settings → Secrets and variables → Actions**.
2. Create a new repository secret named `DISCORD_WEBHOOK_URL`.
3. Set it to your Discord webhook URL.

### 2) Enable and run the workflow

The workflow file is at `.github/workflows/leaderboard-notifier.yml`.

It supports:

- **Scheduled runs** hourly.
- **Manual runs** via **Actions → Arena Leaderboard Notifier → Run workflow**.
  - Optional `force_send` input for webhook delivery testing.
  - Optional `dry_run` input to validate hashing/change detection without posting to Discord.

### Why hourly scheduling with internal polling

GitHub Actions cron schedules are not guaranteed to run more frequently than every 5 minutes, and real trigger timing can still vary. To get more responsive checks, this workflow starts hourly and then runs multiple checks inside one workflow execution using randomized delays.

Current loop configuration in the workflow:

- `--loop`
- `--min-interval-seconds 120`
- `--max-interval-seconds 300`
- `--max-checks 12`

This gives an effective internal polling cadence of roughly **2–5 minutes** between checks for up to 12 checks per workflow run.

Tradeoff: each run stays alive longer, which increases GitHub Actions runtime/minutes consumption.

### 3) State persistence in the cloud

GitHub runners are ephemeral, so the workflow saves and restores the state file using the GitHub Actions cache:

- State path: `.github/state/leaderboard_state.json`
- Cache prefix: `leaderboard-state-`

This keeps change detection consistent between scheduled runs without any local machine.

## Script details

`leaderboard_notifier.py` checks the leaderboard page, hashes normalized page text, compares it with the previous hash from a state file, and sends a Discord webhook message when a change is detected.

To reduce false positives, hashing now focuses on a narrower leaderboard-specific HTML region identified by stable anchors (for example leaderboard title text, leaderboard container markers, and common table section labels). During normalization, dynamic or non-semantic content (timestamps, tracking-related snippets, and broad nav/footer/aside regions) is stripped before whitespace collapsing and HTML unescaping.

If focused extraction fails, the script falls back to whole-page normalization and prints a warning to stderr so operators can still monitor changes without silently missing checks.

Useful options:

```bash
python leaderboard_notifier.py --dry-run
python leaderboard_notifier.py --force-send --max-checks 1
python leaderboard_notifier.py --state-file /path/to/state.json
python leaderboard_notifier.py --url https://arena.ai/leaderboard/text/overall-no-style-control
python leaderboard_notifier.py --loop --min-interval-seconds 120 --max-interval-seconds 300 --max-checks 12
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
