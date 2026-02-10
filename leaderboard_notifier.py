#!/usr/bin/env python3
"""Monitor the Arena leaderboard page and notify Discord when it changes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import textwrap
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from urllib import error, request
from urllib.parse import urlparse

DEFAULT_URL = "https://arena.ai/leaderboard/text/overall-no-style-control"
DEFAULT_STATE_FILE = ".leaderboard_state.json"
DEFAULT_TIMEOUT = 30
DISCORD_WEBHOOK_HOSTS = ("discord.com", "discordapp.com", "ptb.discord.com", "canary.discord.com")


def fetch_html(url: str, timeout: int) -> str:
    req = request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            )
        },
    )
    with request.urlopen(req, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def normalize_html_for_hash(html: str) -> str:
    without_scripts = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.I | re.S)
    without_styles = re.sub(r"<style\b[^>]*>.*?</style>", "", without_scripts, flags=re.I | re.S)
    text_only = re.sub(r"<[^>]+>", " ", without_styles)
    text_only = unescape(text_only)
    return re.sub(r"\s+", " ", text_only).strip()


def compute_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_state(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def send_discord_message(webhook_url: str, message: str, timeout: int) -> None:
    cleaned_webhook_url = webhook_url.strip()
    if not cleaned_webhook_url:
        raise ValueError("Discord webhook URL is empty")

    parsed_webhook_url = urlparse(cleaned_webhook_url)
    if parsed_webhook_url.scheme != "https" or parsed_webhook_url.netloc not in DISCORD_WEBHOOK_HOSTS:
        raise ValueError("Webhook URL does not look like a Discord webhook URL")

    payload = json.dumps({"content": message}).encode("utf-8")
    req = request.Request(
        cleaned_webhook_url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "LLM-Leaderboard-Notifier/1.0",
        },
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as response:
        if response.status < 200 or response.status >= 300:
            raise RuntimeError(f"Discord webhook returned HTTP {response.status}")


def build_message(url: str, old_hash: str | None, new_hash: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    old_display = old_hash[:12] if old_hash else "(none)"
    return textwrap.dedent(
        f"""
        🔔 Arena leaderboard update detected.
        URL: {url}
        Previous fingerprint: {old_display}
        New fingerprint: {new_hash[:12]}
        Checked at: {timestamp}
        """
    ).strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check Arena leaderboard page for updates and notify Discord webhook."
    )
    parser.add_argument("--url", default=DEFAULT_URL, help=f"Leaderboard URL (default: {DEFAULT_URL})")
    parser.add_argument(
        "--webhook-url",
        default=os.environ.get("DISCORD_WEBHOOK_URL"),
        help="Discord webhook URL (or set DISCORD_WEBHOOK_URL env var)",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=Path(DEFAULT_STATE_FILE),
        help=f"Path for cached state (default: {DEFAULT_STATE_FILE})",
    )
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="HTTP timeout in seconds")
    parser.add_argument(
        "--force-send",
        action="store_true",
        help="Send Discord notification even if no change is detected",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do everything except posting to Discord",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not args.webhook_url and not args.dry_run:
        print("Error: provide --webhook-url or set DISCORD_WEBHOOK_URL", file=sys.stderr)
        return 2

    try:
        html = fetch_html(args.url, args.timeout)
    except error.URLError as exc:
        print(f"Failed to fetch leaderboard page: {exc}", file=sys.stderr)
        return 1

    normalized = normalize_html_for_hash(html)
    new_hash = compute_hash(normalized)

    state = load_state(args.state_file)
    old_hash = state.get("hash")
    changed = old_hash != new_hash

    if changed:
        print("Leaderboard content changed.")
    else:
        print("No leaderboard change detected.")

    should_send = args.force_send or changed

    if should_send:
        message = build_message(args.url, old_hash, new_hash)
        if args.dry_run:
            print("[dry-run] Would send Discord message:")
            print(message)
        else:
            try:
                send_discord_message(args.webhook_url, message, args.timeout)
            except error.HTTPError as exc:
                details = ""
                try:
                    details = exc.read().decode("utf-8", errors="replace").strip()
                except Exception:
                    details = ""
                if details:
                    print(
                        f"Failed to send Discord message: HTTP {exc.code} {exc.reason} | {details}",
                        file=sys.stderr,
                    )
                else:
                    print(f"Failed to send Discord message: HTTP {exc.code} {exc.reason}", file=sys.stderr)
                return 1
            except (error.URLError, ValueError) as exc:
                print(f"Failed to send Discord message: {exc}", file=sys.stderr)
                return 1
            print("Discord notification sent.")

    state.update(
        {
            "url": args.url,
            "hash": new_hash,
            "last_checked_utc": datetime.now(timezone.utc).isoformat(),
        }
    )
    save_state(args.state_file, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
