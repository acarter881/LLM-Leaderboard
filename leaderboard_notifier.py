#!/usr/bin/env python3
"""Monitor the Arena leaderboard page and notify Discord when it changes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import socket
import sys
import textwrap
import time
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Callable, TypeVar
from urllib import error, request
from urllib.parse import urlparse

DEFAULT_URL = "https://arena.ai/leaderboard/text/overall-no-style-control"
DEFAULT_STATE_FILE = "leaderboard_state.json"
DEFAULT_TIMEOUT = 30
DEFAULT_RETRIES = 3
DEFAULT_RETRY_BACKOFF_SECONDS = 2.0
DEFAULT_CONFIRMATION_CHECKS = 2
DISCORD_WEBHOOK_HOSTS = ("discord.com", "discordapp.com", "ptb.discord.com", "canary.discord.com")
DEFAULT_SNAPSHOT_TOP_N = 10
MAX_DISCORD_MESSAGE_LENGTH = 1800

# Structured time-series defaults
DEFAULT_SNAPSHOT_DIR = "data/snapshots"
DEFAULT_TIMESERIES_DIR = "data/timeseries"
DEFAULT_STRUCTURED_CACHE = ".github/state/structured_snapshot.json"


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
    def normalize_text(content: str) -> str:
        text_only = re.sub(r"<[^>]+>", " ", content)
        text_only = unescape(text_only)
        text_only = re.sub(
            r"\b(?:last\s+updated|updated\s+at|generated\s+at|timestamp)\b[^\n<]{0,80}",
            " ",
            text_only,
            flags=re.I,
        )
        text_only = re.sub(
            r"\b\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?(?:\s*UTC|\s*GMT|Z)?\b",
            " ",
            text_only,
            flags=re.I,
        )
        text_only = re.sub(r"\b(?:ga|gtm|utm_[a-z_]+|analytics|tracking)\b", " ", text_only, flags=re.I)
        return re.sub(r"\s+", " ", text_only).strip()

    base_html = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
    base_html = re.sub(r"<script\b[^>]*>.*?</script>", " ", base_html, flags=re.I | re.S)
    base_html = re.sub(r"<style\b[^>]*>.*?</style>", " ", base_html, flags=re.I | re.S)
    base_html = re.sub(r"<(?:nav|footer|aside)\b[^>]*>.*?</(?:nav|footer|aside)>", " ", base_html, flags=re.I | re.S)

    anchor_patterns = [
        r"(?i)arena\s+llm\s+leaderboard",
        r"(?i)overall[-\s]+no[-\s]+style[-\s]+control",
        r"(?i)(?:id|class)=[\"'][^\"']*leaderboard[^\"']*[\"']",
        r"(?i)>\s*leaderboard\s*<",
        r"(?i)\b(?:rank|model|score|elo)\b",
    ]

    match_spans: list[tuple[int, int]] = []
    for pattern in anchor_patterns:
        for match in re.finditer(pattern, base_html):
            match_spans.append((match.start(), match.end()))

    if match_spans:
        min_start = min(start for start, _ in match_spans)
        max_end = max(end for _, end in match_spans)
        padding = 5000
        focused_region = base_html[max(0, min_start - padding) : min(len(base_html), max_end + padding)]
        normalized_focused = normalize_text(focused_region)
        if normalized_focused:
            return normalized_focused

    print(
        "Warning: focused leaderboard extraction failed; using whole-page normalization fallback.",
        file=sys.stderr,
    )
    return normalize_text(base_html)


def compute_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def is_leaderboard_url(url: str) -> bool:
    parsed = urlparse(url)
    path = (parsed.path or "").lower()
    return re.search(r"(?:^|/)leaderboard(?:/|$)", path) is not None


def page_subject(url: str) -> str:
    return "leaderboard" if is_leaderboard_url(url) else "tracked page"


def _strip_html(value: str) -> str:
    stripped = re.sub(r"<[^>]+>", " ", value)
    stripped = unescape(stripped)
    return re.sub(r"\s+", " ", stripped).strip()


def _parse_rank(cell: str) -> int | None:
    match = re.search(r"\d+", cell)
    if not match:
        return None
    try:
        return int(match.group(0))
    except ValueError:
        return None


def _parse_score(cell: str) -> float | None:
    match = re.search(r"-?\d+(?:\.\d+)?", cell.replace(",", ""))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _is_plausible_model_name(value: str) -> bool:
    normalized = value.strip()
    if not normalized:
        return False
    if normalized.lower() == "model":
        return False
    if not re.search(r"[a-z]", normalized, flags=re.I):
        return False
    return True


def _parse_snapshot_rows(row_html_blocks: list[str]) -> list[dict]:
    snapshots: list[dict] = []
    for row_html in row_html_blocks:
        cells_raw = re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", row_html, flags=re.I | re.S)
        cells = [_strip_html(cell) for cell in cells_raw]
        cells = [cell for cell in cells if cell]
        if len(cells) < 2:
            continue

        rank = _parse_rank(cells[0])
        if rank is None or rank <= 0 or rank > 1000:
            continue

        model_name = cells[1]
        if not _is_plausible_model_name(model_name):
            continue

        score = None
        for cell in cells[2:]:
            score = _parse_score(cell)
            if score is not None:
                break

        entry = {"rank": rank, "model": model_name}
        if score is not None:
            entry["score"] = score
        snapshots.append(entry)
    return snapshots


def parse_leaderboard_snapshot(html: str, top_n: int = DEFAULT_SNAPSHOT_TOP_N) -> list[dict]:
    table_snapshots: list[list[dict]] = []
    for table_html in re.findall(r"<table\b[^>]*>.*?</table>", html, flags=re.I | re.S):
        if re.search(r"\brank\b", table_html, flags=re.I) is None:
            continue
        if re.search(r"\bmodel\b", table_html, flags=re.I) is None:
            continue
        row_blocks = re.findall(r"<tr\b[^>]*>.*?</tr>", table_html, flags=re.I | re.S)
        table_snapshot = _parse_snapshot_rows(row_blocks)
        if table_snapshot:
            table_snapshots.append(table_snapshot)

    if table_snapshots:
        snapshots = max(table_snapshots, key=len)
    else:
        row_blocks = re.findall(r"<tr\b[^>]*>.*?</tr>", html, flags=re.I | re.S)
        snapshots = _parse_snapshot_rows(row_blocks)

    snapshots.sort(key=lambda item: item["rank"])
    seen_ranks: set[int] = set()
    deduplicated: list[dict] = []
    for row in snapshots:
        if row["rank"] in seen_ranks:
            continue
        seen_ranks.add(row["rank"])
        deduplicated.append(row)
    return deduplicated[:top_n]


def format_score(value: float | int | None) -> str:
    if value is None:
        return "?"
    return f"{value:.2f}".rstrip("0").rstrip(".")


def diff_snapshots(previous: list[dict], current: list[dict]) -> dict[str, list[str]]:
    previous_by_model = {row["model"]: row for row in previous}
    current_by_model = {row["model"]: row for row in current}

    new_entries: list[str] = []
    rank_movements: list[str] = []
    score_deltas: list[str] = []

    for row in current:
        previous_row = previous_by_model.get(row["model"])
        if previous_row is None:
            score_part = f" (score {format_score(row.get('score'))})" if row.get("score") is not None else ""
            new_entries.append(f"#{row['rank']} {row['model']}{score_part}")
            continue

        rank_delta = previous_row["rank"] - row["rank"]
        if rank_delta != 0:
            direction = "↑" if rank_delta > 0 else "↓"
            rank_movements.append(
                f"{direction} {row['model']}: #{previous_row['rank']} → #{row['rank']}"
            )

        previous_score = previous_row.get("score")
        current_score = row.get("score")
        if previous_score is None or current_score is None:
            continue
        delta = float(current_score) - float(previous_score)
        if abs(delta) < 1e-9:
            continue
        sign = "+" if delta > 0 else ""
        score_deltas.append(
            f"{row['model']}: {format_score(previous_score)} → {format_score(current_score)} ({sign}{delta:.2f})"
        )

    top_window_size = max(len(previous), len(current))

    for model, previous_row in previous_by_model.items():
        if model in current_by_model:
            continue
        rank_movements.append(f"↘ {model}: dropped from top {top_window_size}")

    return {
        "new_entries": new_entries,
        "rank_movements": rank_movements,
        "score_deltas": score_deltas,
    }


def bound_message_length(message: str, url: str, max_length: int = MAX_DISCORD_MESSAGE_LENGTH) -> str:
    if len(message) <= max_length:
        return message
    suffix = f"\n… (truncated for Discord limits; see URL: {url})"
    allowed = max(0, max_length - len(suffix))
    trimmed = message[:allowed].rstrip()
    return f"{trimmed}{suffix}"


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


def _is_timeout_error(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True
    if isinstance(exc, error.URLError):
        return isinstance(getattr(exc, "reason", None), (TimeoutError, socket.timeout))
    return False


def _is_retryable_network_error(exc: BaseException) -> bool:
    if _is_timeout_error(exc):
        return True
    if isinstance(exc, error.HTTPError):
        return 500 <= exc.code < 600
    if isinstance(exc, error.URLError):
        return True
    return False


T = TypeVar("T")


def run_with_retries(
    operation_name: str,
    operation: Callable[[], T],
    retries: int,
    retry_backoff_seconds: float,
) -> T:
    max_attempts = retries + 1
    for attempt in range(1, max_attempts + 1):
        try:
            return operation()
        except Exception as exc:
            retryable = _is_retryable_network_error(exc)
            if not retryable or attempt == max_attempts:
                raise
            backoff = retry_backoff_seconds * (2 ** (attempt - 1))
            print(
                f"Retrying {operation_name} (attempt {attempt}/{max_attempts}) after {backoff:.1f}s: {exc}",
                file=sys.stderr,
            )
            time.sleep(backoff)


def build_message(
    url: str,
    old_hash: str | None,
    new_hash: str,
    previous_snapshot: list[dict] | None = None,
    current_snapshot: list[dict] | None = None,
    use_legacy_hash_message: bool = False,
) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    old_display = old_hash[:12] if old_hash else "(none)"
    subject = page_subject(url)

    if use_legacy_hash_message or previous_snapshot is None or current_snapshot is None:
        message = textwrap.dedent(
            f"""
            🔔 Arena {subject} update detected.
            URL: {url}
            Previous fingerprint: {old_display}
            New fingerprint: {new_hash[:12]}
            Checked at: {timestamp}
            """
        ).strip()
        return bound_message_length(message, url)

    # When both snapshots are empty, the legacy parser couldn't extract
    # model data — fall back to a fingerprint-only message rather than
    # showing a misleading "Top 0 snapshot changes" header.
    if not previous_snapshot and not current_snapshot:
        message = textwrap.dedent(
            f"""
            🔔 Arena {subject} update detected.
            URL: {url}
            Previous fingerprint: {old_display}
            New fingerprint: {new_hash[:12]}
            Checked at: {timestamp}
            (Model snapshot data unavailable for detailed comparison.)
            """
        ).strip()
        return bound_message_length(message, url)

    diffs = diff_snapshots(previous_snapshot, current_snapshot)
    snapshot_window_size = max(len(previous_snapshot), len(current_snapshot))

    sections = [
        f"🔔 Arena {subject} update detected.",
        f"URL: {url}",
        f"Top {snapshot_window_size} snapshot changes:",
    ]

    if diffs["new_entries"]:
        sections.append("New entries:")
        sections.extend(f"- {line}" for line in diffs["new_entries"])

    if diffs["rank_movements"]:
        sections.append("Rank movements:")
        sections.extend(f"- {line}" for line in diffs["rank_movements"])

    if diffs["score_deltas"]:
        sections.append("Score deltas:")
        sections.extend(f"- {line}" for line in diffs["score_deltas"])

    if not diffs["new_entries"] and not diffs["rank_movements"] and not diffs["score_deltas"]:
        sections.append("No top-rank snapshot differences found (page fingerprint changed).")

    sections.extend(
        [
            f"Previous fingerprint: {old_display}",
            f"New fingerprint: {new_hash[:12]}",
            f"Checked at: {timestamp}",
        ]
    )

    return bound_message_length("\n".join(sections), url)


def build_force_send_no_change_message(url: str, existing_hash: str | None) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    hash_display = existing_hash[:12] if existing_hash else "(none)"
    subject = page_subject(url)
    message = textwrap.dedent(
        f"""
        🔔 Arena {subject} force-send test.
        URL: {url}
        No {subject} change was detected for this check.
        Current fingerprint: {hash_display}
        Checked at: {timestamp}
        """
    ).strip()
    return bound_message_length(message, url)


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
        "--retries",
        type=int,
        default=DEFAULT_RETRIES,
        help=f"Number of retries for temporary network failures (default: {DEFAULT_RETRIES})",
    )
    parser.add_argument(
        "--retry-backoff-seconds",
        type=float,
        default=DEFAULT_RETRY_BACKOFF_SECONDS,
        help=(
            "Base backoff in seconds between retries; doubles each retry "
            f"(default: {DEFAULT_RETRY_BACKOFF_SECONDS})"
        ),
    )
    parser.add_argument(
        "--confirmation-checks",
        type=int,
        default=DEFAULT_CONFIRMATION_CHECKS,
        help=(
            "Number of consecutive checks that must observe a new fingerprint "
            f"before notifying (default: {DEFAULT_CONFIRMATION_CHECKS})"
        ),
    )
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
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Repeat checks in a loop with randomized delays",
    )
    parser.add_argument(
        "--min-interval-seconds",
        type=int,
        default=120,
        help="Minimum randomized delay between loop checks (default: 120)",
    )
    parser.add_argument(
        "--max-interval-seconds",
        type=int,
        default=300,
        help="Maximum randomized delay between loop checks (default: 300)",
    )
    parser.add_argument(
        "--max-checks",
        type=int,
        help="Optional cap on number of checks when --loop is enabled",
    )
    parser.add_argument(
        "--snapshot-dir",
        type=Path,
        default=Path(DEFAULT_SNAPSHOT_DIR),
        help=f"Directory for full JSON snapshots (default: {DEFAULT_SNAPSHOT_DIR})",
    )
    parser.add_argument(
        "--timeseries-dir",
        type=Path,
        default=Path(DEFAULT_TIMESERIES_DIR),
        help=f"Directory for JSONL time series (default: {DEFAULT_TIMESERIES_DIR})",
    )
    parser.add_argument(
        "--structured-cache",
        type=Path,
        default=Path(DEFAULT_STRUCTURED_CACHE),
        help=f"Cache path for latest structured snapshot (default: {DEFAULT_STRUCTURED_CACHE})",
    )
    parser.add_argument(
        "--no-structured",
        action="store_true",
        help="Disable structured parsing (hash-only mode)",
    )
    return parser.parse_args()


def run_single_check(args: argparse.Namespace) -> int:

    try:
        html = run_with_retries(
            "leaderboard fetch",
            lambda: fetch_html(args.url, args.timeout),
            retries=args.retries,
            retry_backoff_seconds=args.retry_backoff_seconds,
        )
    except error.HTTPError as exc:
        print(f"Failed to fetch leaderboard page: HTTP {exc.code} {exc.reason}", file=sys.stderr)
        return 1
    except error.URLError as exc:
        print(f"Failed to fetch leaderboard page: {exc}", file=sys.stderr)
        return 1

    normalized = normalize_html_for_hash(html)
    new_hash = compute_hash(normalized)
    current_snapshot = parse_leaderboard_snapshot(html) if is_leaderboard_url(args.url) else None

    # --- Structured parsing (runs alongside hash detection) ---
    structured_snapshot = None
    structured_diff = None
    use_structured = is_leaderboard_url(args.url) and not getattr(args, "no_structured", False)

    if use_structured:
        try:
            from leaderboard_parser import safe_parse_html
            from snapshot_store import store_snapshot, load_from_cache
            from snapshot_diff import compute_diff, has_changes, format_discord_message, format_diff_summary, format_snapshot_message

            structured_snapshot = safe_parse_html(html)
            if structured_snapshot:
                model_count = len(structured_snapshot.get("models", []))
                print(f"Structured parser: extracted {model_count} models.")
        except ImportError as exc:
            print(f"Warning: structured parsing modules not available: {exc}", file=sys.stderr)
            use_structured = False

    state = load_state(args.state_file)
    old_hash = state.get("hash")
    old_snapshot = state.get("snapshot")
    if not isinstance(old_snapshot, list):
        old_snapshot = None

    pending_hash = state.get("pending_hash")
    pending_snapshot = state.get("pending_snapshot")
    if not isinstance(pending_snapshot, list):
        pending_snapshot = None
    pending_count = state.get("pending_count", 0)
    if not isinstance(pending_count, int) or pending_count < 0:
        pending_count = 0

    changed = False
    hash_confirmed = False  # True when the 2-check confirmation passes (even if veto suppresses notification)
    effective_old_hash = old_hash
    effective_old_snapshot = old_snapshot

    if old_hash == new_hash:
        state.pop("pending_hash", None)
        state.pop("pending_snapshot", None)
        state.pop("pending_count", None)
    else:
        if pending_hash == new_hash:
            pending_count += 1
        else:
            pending_hash = new_hash
            pending_snapshot = current_snapshot
            pending_count = 1

        state["pending_hash"] = pending_hash
        state["pending_snapshot"] = pending_snapshot
        state["pending_count"] = pending_count

        if pending_count >= args.confirmation_checks:
            changed = True
            hash_confirmed = True
            effective_old_hash = old_hash
            effective_old_snapshot = old_snapshot
            state.pop("pending_hash", None)
            state.pop("pending_snapshot", None)
            state.pop("pending_count", None)

    if changed:
        print(f"{page_subject(args.url).capitalize()} content changed.")
    else:
        if old_hash != new_hash:
            print(
                f"Observed a new {page_subject(args.url)} fingerprint but waiting for confirmation "
                f"({pending_count}/{args.confirmation_checks})."
            )
        else:
            print(f"No {page_subject(args.url)} change detected.")

    # --- Structured diff & storage ---
    if use_structured and structured_snapshot:
        try:
            previous_structured = load_from_cache(args.structured_cache)

            if changed or previous_structured is None:
                # Compute structured diff
                if previous_structured:
                    structured_diff = compute_diff(previous_structured, structured_snapshot)
                    summary = format_diff_summary(structured_diff)
                    print(f"Structured diff: {summary}")

                    # --- Structured veto ---
                    # If the hash changed but the structured model data
                    # did NOT change, suppress the notification.  This
                    # prevents false positives from cosmetic page changes
                    # (e.g. new sidebar filters, layout tweaks).
                    if changed and not has_changes(structured_diff):
                        print(
                            "Page fingerprint changed but structured leaderboard "
                            "data is identical — suppressing notification."
                        )
                        changed = False

                # Store snapshot and update time series
                store_result = store_snapshot(
                    structured_snapshot,
                    previous_snapshot=previous_structured,
                    snapshot_dir=args.snapshot_dir,
                    timeseries_dir=args.timeseries_dir,
                    cache_path=args.structured_cache,
                    only_on_change=True,
                )
                if store_result.get("snapshot_path"):
                    print(f"Snapshot saved: {store_result['snapshot_path']}")
            else:
                # Only update cache when hash is stable (no pending confirmation).
                # Preserving the old cache during the pending window ensures the
                # structured diff has a meaningful baseline when confirmation arrives.
                if old_hash == new_hash:
                    from snapshot_store import save_latest_for_cache
                    save_latest_for_cache(structured_snapshot, args.structured_cache)
        except Exception as exc:
            print(f"Warning: structured storage/diff failed: {exc}", file=sys.stderr)

    # Guard against duplicate notifications across overlapping workflow runs.
    last_notified_hash = state.get("last_notified_hash")
    if changed and new_hash == last_notified_hash:
        print(
            "Notification already sent for this fingerprint in a prior run — skipping."
        )
        changed = False

    should_send = args.force_send or changed

    if should_send:
        if args.force_send and not changed:
            message = build_force_send_no_change_message(args.url, new_hash)
        else:
            # Use rich structured diff message if available
            if use_structured and structured_diff and has_changes(structured_diff):
                overtake_data = structured_snapshot.get("overtake") if structured_snapshot else None
                message = format_discord_message(structured_diff, args.url, overtake_data=overtake_data)
            elif use_structured and structured_snapshot and structured_snapshot.get("models"):
                # Structured snapshot exists but no diff (e.g. cache miss) —
                # show current top models instead of falling through to
                # the legacy parser which may return empty results.
                message = format_snapshot_message(
                    structured_snapshot,
                    args.url,
                    old_hash=effective_old_hash,
                    new_hash=new_hash,
                )
            else:
                use_legacy_hash_message = old_hash is not None and old_snapshot is None
                message = build_message(
                    args.url,
                    effective_old_hash,
                    new_hash,
                    previous_snapshot=effective_old_snapshot,
                    current_snapshot=current_snapshot,
                    use_legacy_hash_message=use_legacy_hash_message,
                )
        if args.dry_run:
            print("[dry-run] Would send Discord message:")
            print(message)
        else:
            try:
                run_with_retries(
                    "Discord message send",
                    lambda: send_discord_message(args.webhook_url, message, args.timeout),
                    retries=args.retries,
                    retry_backoff_seconds=args.retry_backoff_seconds,
                )
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
            state["last_notified_hash"] = new_hash

    state_updates = {
        "url": args.url,
        "snapshot_top_n": DEFAULT_SNAPSHOT_TOP_N,
        "last_checked_utc": datetime.now(timezone.utc).isoformat(),
    }
    # Update the stored hash whenever the confirmation threshold is reached
    # (even if the structured veto suppressed the notification), so the next
    # check doesn't restart the confirmation cycle for the same hash.
    if hash_confirmed or old_hash is None:
        state_updates["hash"] = new_hash
        state_updates["snapshot"] = current_snapshot
    state.update(state_updates)
    save_state(args.state_file, state)
    return 0


def main() -> int:
    args = parse_args()

    if not args.webhook_url and not args.dry_run:
        print("Error: provide --webhook-url or set DISCORD_WEBHOOK_URL", file=sys.stderr)
        return 2

    if args.min_interval_seconds < 0 or args.max_interval_seconds < 0:
        print("Error: interval values must be non-negative", file=sys.stderr)
        return 2
    if args.retries < 0:
        print("Error: --retries must be non-negative", file=sys.stderr)
        return 2
    if args.retry_backoff_seconds < 0:
        print("Error: --retry-backoff-seconds must be non-negative", file=sys.stderr)
        return 2
    if args.confirmation_checks <= 0:
        print("Error: --confirmation-checks must be greater than 0", file=sys.stderr)
        return 2
    if args.min_interval_seconds > args.max_interval_seconds:
        print("Error: --min-interval-seconds cannot be greater than --max-interval-seconds", file=sys.stderr)
        return 2
    if args.max_checks is not None and args.max_checks <= 0:
        print("Error: --max-checks must be greater than 0", file=sys.stderr)
        return 2

    if not args.loop:
        return run_single_check(args)

    check_count = 0
    while True:
        check_count += 1
        print(f"Starting check {check_count}.")
        result = run_single_check(args)
        if result != 0:
            return result

        if args.max_checks is not None and check_count >= args.max_checks:
            print(f"Reached max checks ({args.max_checks}); stopping loop.")
            return 0

        sleep_seconds = random.randint(args.min_interval_seconds, args.max_interval_seconds)
        print(f"Sleeping {sleep_seconds} seconds before next check.")
        time.sleep(sleep_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
