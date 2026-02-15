#!/usr/bin/env python3
"""Parse the Arena leaderboard HTML into structured model data."""

from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from html import unescape
from typing import Optional
from urllib import error

from leaderboard_notifier import fetch_html, run_with_retries


# ---------------------------------------------------------------------------
# Rank‑spread parsing
# ---------------------------------------------------------------------------

def parse_rank_spread(raw: str, model_rank: int) -> tuple[int, int] | None:
    """Split a concatenated rank‑spread string into (rank_ub, rank_lb).

    The Arena leaderboard encodes the confidence‑interval bounds on rank as a
    single integer with no delimiter.  For example ``"1634"`` means rank_ub=16,
    rank_lb=34.  We rely on the constraint rank_ub <= model_rank <= rank_lb and
    rank_ub <= rank_lb to find the correct split.

    Returns ``None`` if no valid split is found.
    """
    digits = raw.strip()
    if not digits or not digits.isdigit():
        return None

    n = len(digits)
    candidates: list[tuple[int, int, float]] = []

    for split_pos in range(1, n):
        left = digits[:split_pos]
        right = digits[split_pos:]
        # Reject parts with leading zeros (except the number "0" itself,
        # which is not a valid rank anyway).
        if (len(left) > 1 and left[0] == "0") or (len(right) > 1 and right[0] == "0"):
            continue
        ub = int(left)
        lb = int(right)
        if ub < 1 or lb < 1:
            continue
        if ub > lb:
            continue

        width = lb - ub

        # How far is the model rank from this CI?
        if model_rank < ub:
            overshoot = ub - model_rank
        elif model_rank > lb:
            overshoot = model_rank - lb
        else:
            overshoot = 0

        # Scoring: we want to minimise a combined metric that balances:
        #  - overshoot (rank outside CI): heavily penalised, but a small
        #    overshoot on a narrow CI should beat zero overshoot on an
        #    absurdly wide CI.
        #  - width: prefer tighter confidence intervals.
        # The multiplier on overshoot (20) is large enough that a perfect
        # fit almost always wins, but small enough that a 1-position miss
        # on a CI of width 18 (score = 20 + 18 = 38) still beats an exact
        # fit on width 633 (score = 0 + 633 = 633).
        score = overshoot * 20 + width
        candidates.append((ub, lb, score))

    if not candidates:
        return None

    candidates.sort(key=lambda c: c[2])
    return (candidates[0][0], candidates[0][1])


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def _strip_tags(html_fragment: str) -> str:
    """Remove HTML tags and decode entities."""
    text = re.sub(r"<[^>]+>", " ", html_fragment)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_href(cell_html: str) -> str | None:
    """Return the first href value from an anchor tag, if any."""
    m = re.search(r'<a\b[^>]*\bhref=["\']([^"\']+)["\']', cell_html, flags=re.I)
    return m.group(1) if m else None


def _extract_link_text(cell_html: str) -> str | None:
    """Return the text content of the first <a> tag."""
    m = re.search(r"<a\b[^>]*>(.*?)</a>", cell_html, flags=re.I | re.S)
    return _strip_tags(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# Page‑level metadata
# ---------------------------------------------------------------------------

def parse_page_metadata(html: str) -> dict:
    """Extract page‑level information (date stamp, total votes, total models)."""
    meta: dict = {}

    # Date stamp — e.g. "Feb 11, 2026"
    date_match = re.search(
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},?\s+\d{4}",
        html,
    )
    if date_match:
        meta["leaderboard_date"] = date_match.group(0)

    # Total votes — e.g. "5,271,984 votes"
    votes_match = re.search(r"([\d,]+)\s+votes", html, flags=re.I)
    if votes_match:
        meta["total_votes"] = int(votes_match.group(1).replace(",", ""))

    # Total models — e.g. "305 models"
    models_match = re.search(r"(\d+)\s+models", html, flags=re.I)
    if models_match:
        meta["total_models"] = int(models_match.group(1))

    return meta


# ---------------------------------------------------------------------------
# Table column detection
# ---------------------------------------------------------------------------

_COLUMN_PATTERNS = {
    "rank": re.compile(r"^\s*(?:#|rank)\s*$", re.I),
    "rank_spread": re.compile(r"rank\s*spread", re.I),
    "model": re.compile(r"^\s*model\s*$", re.I),
    "score": re.compile(r"^\s*(?:arena\s+)?score\s*$", re.I),
    "votes": re.compile(r"^\s*votes?\s*$", re.I),
}


def _detect_columns(header_cells: list[str]) -> dict[str, int]:
    """Map logical column names to 0‑based indices from a header row."""
    mapping: dict[str, int] = {}
    for idx, raw_cell in enumerate(header_cells):
        text = _strip_tags(raw_cell).strip()
        for col_name, pattern in _COLUMN_PATTERNS.items():
            if pattern.search(text):
                mapping[col_name] = idx
                break
    return mapping


# ---------------------------------------------------------------------------
# Row parsing
# ---------------------------------------------------------------------------

def _parse_score_ci(text: str) -> tuple[int | None, int | None, bool]:
    """Parse a score±CI cell, returning (score, ci, is_preliminary)."""
    is_preliminary = bool(re.search(r"preliminary", text, re.I))
    # Remove "Preliminary" text for numeric parsing
    cleaned = re.sub(r"preliminary", "", text, flags=re.I).strip()

    # Try "score±ci" or "score ± ci"
    m = re.search(r"(-?\d[\d,]*)\s*[±\+\-/]\s*(\d[\d,]*)", cleaned)
    if m:
        score = int(m.group(1).replace(",", ""))
        ci = int(m.group(2).replace(",", ""))
        return score, ci, is_preliminary

    # Just a bare number
    m = re.search(r"(-?\d[\d,]+)", cleaned)
    if m:
        score = int(m.group(1).replace(",", ""))
        return score, None, is_preliminary

    return None, None, is_preliminary


def _parse_model_cell(cell_html: str) -> dict:
    """Extract model name, organization, license, and URL from the model cell."""
    result: dict = {}

    # Model URL from link
    href = _extract_href(cell_html)
    if href:
        result["model_url"] = href

    # Model name — prefer link text, fall back to full cell text
    link_text = _extract_link_text(cell_html)
    full_text = _strip_tags(cell_html)

    if link_text:
        result["model_name"] = link_text
        # Remaining text after removing model name may contain org/license info
        remainder = full_text.replace(link_text, "", 1).strip()
    else:
        # No link — the whole cell is the model name (possibly with org info)
        result["model_name"] = full_text
        remainder = ""

    # Try to parse organization and license from remainder
    # Common patterns: "Anthropic · Proprietary", "OpenAI Proprietary", etc.
    if remainder:
        parts = re.split(r"\s*[·|/]\s*", remainder)
        parts = [p.strip() for p in parts if p.strip()]
        if len(parts) >= 2:
            result["organization"] = parts[0]
            result["license"] = parts[1]
        elif len(parts) == 1:
            # Heuristic: if it looks like a license, treat it as one
            text = parts[0]
            if re.search(r"(?i)^(?:proprietary|open|apache|mit|cc|gpl|bsd)", text):
                result["license"] = text
            else:
                result["organization"] = text

    return result


def _parse_votes(text: str) -> int | None:
    """Parse a vote count that may contain commas."""
    cleaned = text.replace(",", "").strip()
    m = re.search(r"(\d+)", cleaned)
    if m:
        return int(m.group(1))
    return None


# ---------------------------------------------------------------------------
# Full table parsing
# ---------------------------------------------------------------------------

def parse_leaderboard_table(html: str) -> list[dict]:
    """Parse the leaderboard HTML into a list of structured model dicts.

    Returns all models found, sorted by rank.
    """
    # Find all tables that look like leaderboard tables
    tables = re.findall(r"<table\b[^>]*>(.*?)</table>", html, flags=re.I | re.S)
    if not tables:
        # Fall back to parsing the whole page as if it were one big table
        tables = [html]

    best_result: list[dict] = []

    for table_html in tables:
        rows = re.findall(r"<tr\b[^>]*>(.*?)</tr>", table_html, flags=re.I | re.S)
        if not rows:
            continue

        # Detect columns from header row
        header_cells = re.findall(r"<t[hd]\b[^>]*>(.*?)</t[hd]>", rows[0], flags=re.I | re.S)
        col_map = _detect_columns(header_cells)

        # Must have at least rank and model columns
        if "rank" not in col_map and "model" not in col_map:
            # Try to detect from any row containing "rank" and "model" text
            for row in rows[:3]:
                cells = re.findall(r"<t[hd]\b[^>]*>(.*?)</t[hd]>", row, flags=re.I | re.S)
                col_map = _detect_columns(cells)
                if "rank" in col_map and "model" in col_map:
                    break
            else:
                continue

        parsed_rows: list[dict] = []
        for row_html in rows[1:]:  # Skip header
            cells_raw = re.findall(r"<t[hd]\b[^>]*>(.*?)</t[hd]>", row_html, flags=re.I | re.S)
            if len(cells_raw) < 2:
                continue

            entry: dict = {}

            # Rank
            rank_idx = col_map.get("rank", 0)
            if rank_idx < len(cells_raw):
                rank_text = _strip_tags(cells_raw[rank_idx])
                m = re.search(r"(\d+)", rank_text)
                if m:
                    entry["rank"] = int(m.group(1))
                else:
                    continue
            else:
                continue

            if entry["rank"] <= 0 or entry["rank"] > 1000:
                continue

            # Rank spread
            if "rank_spread" in col_map:
                rs_idx = col_map["rank_spread"]
                if rs_idx < len(cells_raw):
                    rs_text = _strip_tags(cells_raw[rs_idx]).strip()
                    entry["rank_spread_raw"] = rs_text
                    parsed = parse_rank_spread(rs_text, entry["rank"])
                    if parsed:
                        entry["rank_ub"] = parsed[0]
                        entry["rank_lb"] = parsed[1]

            # Model
            model_idx = col_map.get("model")
            if model_idx is not None and model_idx < len(cells_raw):
                model_data = _parse_model_cell(cells_raw[model_idx])
                if not model_data.get("model_name"):
                    continue
                # Filter out numeric-only model names (not real models)
                if not re.search(r"[a-zA-Z]", model_data["model_name"]):
                    continue
                entry.update(model_data)
            else:
                continue

            # Score / CI
            score_idx = col_map.get("score")
            if score_idx is not None and score_idx < len(cells_raw):
                score_text = _strip_tags(cells_raw[score_idx])
                score, ci, is_prelim = _parse_score_ci(score_text)
                if score is not None:
                    entry["score"] = score
                if ci is not None:
                    entry["ci"] = ci
                entry["is_preliminary"] = is_prelim

            # Votes
            votes_idx = col_map.get("votes")
            if votes_idx is not None and votes_idx < len(cells_raw):
                votes_text = _strip_tags(cells_raw[votes_idx])
                votes = _parse_votes(votes_text)
                if votes is not None:
                    entry["votes"] = votes

            parsed_rows.append(entry)

        if len(parsed_rows) > len(best_result):
            best_result = parsed_rows

    # If column-based parsing failed, try a looser row-based approach
    if not best_result:
        best_result = _fallback_parse(html)

    best_result.sort(key=lambda r: r.get("rank", 9999))
    return best_result


def _fallback_parse(html: str) -> list[dict]:
    """Loose fallback parser when column detection fails.

    Looks for rows with a leading integer (rank) followed by text (model name)
    and a numeric score.
    """
    rows = re.findall(r"<tr\b[^>]*>(.*?)</tr>", html, flags=re.I | re.S)
    results: list[dict] = []
    for row_html in rows:
        cells_raw = re.findall(r"<t[hd]\b[^>]*>(.*?)</t[hd]>", row_html, flags=re.I | re.S)
        cells = [_strip_tags(c) for c in cells_raw]
        cells = [c for c in cells if c]
        if len(cells) < 2:
            continue

        # First cell should be a rank
        m = re.match(r"^(\d+)$", cells[0].strip())
        if not m:
            continue
        rank = int(m.group(1))
        if rank <= 0 or rank > 1000:
            continue

        # Second cell should be a model name (contains letters)
        model_name = cells[1]
        if not re.search(r"[a-zA-Z]", model_name):
            continue

        entry: dict = {"rank": rank, "model_name": model_name}
        # Try to find a score in remaining cells
        for cell in cells[2:]:
            score, ci, is_prelim = _parse_score_ci(cell)
            if score is not None:
                entry["score"] = score
                if ci is not None:
                    entry["ci"] = ci
                entry["is_preliminary"] = is_prelim
                break
        results.append(entry)
    return results


# ---------------------------------------------------------------------------
# High‑level fetch + parse
# ---------------------------------------------------------------------------

def fetch_and_parse(
    url: str,
    timeout: int = 30,
    retries: int = 3,
    retry_backoff_seconds: float = 2.0,
) -> dict:
    """Fetch the leaderboard page and return a full structured snapshot.

    Returns a dict with keys: timestamp, leaderboard_date, total_models,
    total_votes, models.

    Raises on network errors after retries are exhausted.
    """
    html = run_with_retries(
        "leaderboard fetch",
        lambda: fetch_html(url, timeout),
        retries=retries,
        retry_backoff_seconds=retry_backoff_seconds,
    )
    return parse_html(html)


def parse_html(html: str) -> dict:
    """Parse raw HTML into a structured snapshot dict (no network I/O)."""
    metadata = parse_page_metadata(html)
    models = parse_leaderboard_table(html)

    return {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "leaderboard_date": metadata.get("leaderboard_date"),
        "total_models": metadata.get("total_models", len(models)),
        "total_votes": metadata.get("total_votes"),
        "models": models,
    }


def safe_fetch_and_parse(
    url: str,
    timeout: int = 30,
    retries: int = 3,
    retry_backoff_seconds: float = 2.0,
) -> dict | None:
    """Like fetch_and_parse but returns None on failure instead of raising.

    Logs the error to stderr so the caller can continue with hash‑based
    detection even if structured parsing fails.
    """
    try:
        return fetch_and_parse(
            url,
            timeout=timeout,
            retries=retries,
            retry_backoff_seconds=retry_backoff_seconds,
        )
    except Exception as exc:
        print(
            f"Warning: structured leaderboard parsing failed: {exc}",
            file=sys.stderr,
        )
        return None


def safe_parse_html(html: str) -> dict | None:
    """Like parse_html but returns None on failure instead of raising."""
    try:
        return parse_html(html)
    except Exception as exc:
        print(
            f"Warning: structured HTML parsing failed: {exc}",
            file=sys.stderr,
        )
        return None
