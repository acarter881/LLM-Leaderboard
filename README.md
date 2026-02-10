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

- **Scheduled runs** every 30 minutes.
- **Manual runs** via **Actions → Arena Leaderboard Notifier → Run workflow**.

### 3) State persistence in the cloud

GitHub runners are ephemeral, so the workflow saves and restores the state file using the GitHub Actions cache:

- State path: `.github/state/leaderboard_state.json`
- Cache prefix: `leaderboard-state-`

This keeps change detection consistent between scheduled runs without any local machine.

## Script details

`leaderboard_notifier.py` checks the leaderboard page, hashes normalized page text, compares it with the previous hash from a state file, and sends a Discord webhook message when a change is detected.

Useful options:

```bash
python leaderboard_notifier.py --dry-run
python leaderboard_notifier.py --force-send
python leaderboard_notifier.py --state-file /path/to/state.json
python leaderboard_notifier.py --url https://arena.ai/leaderboard/text/overall-no-style-control
```
