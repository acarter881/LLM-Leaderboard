# LLM-Leaderboard

Monitor the [Arena LLM leaderboard](https://arena.ai/leaderboard/text/overall-no-style-control) and send a message to a Discord channel when the page content changes.

## Script

`leaderboard_notifier.py` checks the leaderboard page, hashes normalized page text, compares it with the previous hash from a local state file, and sends a Discord webhook message when a change is detected.

## Usage

```bash
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
python leaderboard_notifier.py
```

Useful options:

```bash
python leaderboard_notifier.py --dry-run
python leaderboard_notifier.py --force-send
python leaderboard_notifier.py --state-file /path/to/state.json
python leaderboard_notifier.py --url https://arena.ai/leaderboard/text/overall-no-style-control
```

## Automation (cron example)

Run every 30 minutes:

```bash
*/30 * * * * cd /workspace/LLM-Leaderboard && DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..." /usr/bin/python3 leaderboard_notifier.py >> leaderboard_notifier.log 2>&1
```
