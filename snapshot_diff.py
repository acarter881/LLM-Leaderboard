#!/usr/bin/env python3
"""Compute structured diffs between leaderboard snapshots and format for Discord."""

from __future__ import annotations

import json
from typing import Optional

MAX_DISCORD_MESSAGE_LENGTH = 1900


# ---------------------------------------------------------------------------
# Structured diff computation
# ---------------------------------------------------------------------------

def compute_diff(previous: dict, current: dict) -> dict:
    """Compare two full snapshots and return a structured diff.

    Args:
        previous: The earlier snapshot dict (with "models" list, etc.).
        current: The later snapshot dict.

    Returns:
        A dict describing all changes between the two snapshots.
    """
    prev_models = {m["model_name"]: m for m in previous.get("models", []) if "model_name" in m}
    curr_models = {m["model_name"]: m for m in current.get("models", []) if "model_name" in m}

    prev_names = set(prev_models.keys())
    curr_names = set(curr_models.keys())

    diff: dict = {
        "timestamp": current.get("timestamp"),
        "leaderboard_date_changed": (
            previous.get("leaderboard_date") != current.get("leaderboard_date")
        ),
        "previous_leaderboard_date": previous.get("leaderboard_date"),
        "current_leaderboard_date": current.get("leaderboard_date"),
        "new_models": [],
        "removed_models": [],
        "rank_changes": [],
        "rank_ub_changes": [],
        "score_changes": [],
        "ci_changes": [],
        "vote_changes": [],
        "preliminary_changes": [],
    }

    # New models
    for name in sorted(curr_names - prev_names):
        m = curr_models[name]
        diff["new_models"].append({
            "model_name": name,
            "rank": m.get("rank"),
            "score": m.get("score"),
            "rank_ub": m.get("rank_ub"),
            "votes": m.get("votes"),
            "is_preliminary": m.get("is_preliminary", False),
        })

    # Removed models
    for name in sorted(prev_names - curr_names):
        m = prev_models[name]
        diff["removed_models"].append({
            "model_name": name,
            "previous_rank": m.get("rank"),
            "previous_score": m.get("score"),
        })

    # Changes to existing models
    for name in sorted(prev_names & curr_names):
        prev_m = prev_models[name]
        curr_m = curr_models[name]

        # Rank changes
        prev_rank = prev_m.get("rank")
        curr_rank = curr_m.get("rank")
        if prev_rank is not None and curr_rank is not None and prev_rank != curr_rank:
            diff["rank_changes"].append({
                "model_name": name,
                "previous_rank": prev_rank,
                "current_rank": curr_rank,
                "delta": curr_rank - prev_rank,  # negative = moved up
            })

        # Rank UB changes (settlement-critical)
        prev_ub = prev_m.get("rank_ub")
        curr_ub = curr_m.get("rank_ub")
        if prev_ub is not None and curr_ub is not None and prev_ub != curr_ub:
            diff["rank_ub_changes"].append({
                "model_name": name,
                "previous_rank_ub": prev_ub,
                "current_rank_ub": curr_ub,
                "delta": curr_ub - prev_ub,
            })

        # Score changes
        prev_score = prev_m.get("score")
        curr_score = curr_m.get("score")
        if prev_score is not None and curr_score is not None and prev_score != curr_score:
            diff["score_changes"].append({
                "model_name": name,
                "previous_score": prev_score,
                "current_score": curr_score,
                "delta": curr_score - prev_score,
                "current_rank": curr_m.get("rank"),
            })

        # CI changes
        prev_ci = prev_m.get("ci")
        curr_ci = curr_m.get("ci")
        if prev_ci is not None and curr_ci is not None and prev_ci != curr_ci:
            diff["ci_changes"].append({
                "model_name": name,
                "previous_ci": prev_ci,
                "current_ci": curr_ci,
                "delta": curr_ci - prev_ci,
            })

        # Vote changes
        prev_votes = prev_m.get("votes")
        curr_votes = curr_m.get("votes")
        if prev_votes is not None and curr_votes is not None and prev_votes != curr_votes:
            diff["vote_changes"].append({
                "model_name": name,
                "previous_votes": prev_votes,
                "current_votes": curr_votes,
                "delta": curr_votes - prev_votes,
                "current_rank": curr_m.get("rank"),
            })

        # Preliminary status changes
        prev_prelim = prev_m.get("is_preliminary", False)
        curr_prelim = curr_m.get("is_preliminary", False)
        if prev_prelim != curr_prelim:
            diff["preliminary_changes"].append({
                "model_name": name,
                "was_preliminary": prev_prelim,
                "is_preliminary": curr_prelim,
            })

    return diff


def has_changes(diff: dict) -> bool:
    """Return True if the diff contains any meaningful changes."""
    change_keys = [
        "new_models", "removed_models", "rank_changes", "rank_ub_changes",
        "score_changes", "ci_changes", "vote_changes", "preliminary_changes",
    ]
    if diff.get("leaderboard_date_changed"):
        return True
    return any(diff.get(k) for k in change_keys)


def has_significant_changes(diff: dict, top_n_votes: int = 10) -> bool:
    """Return True if the diff contains changes that warrant a notification.

    Vote changes for models outside the top *top_n_votes* are excluded —
    they update constantly as users vote and would trigger a notification
    on almost every check.  Vote changes for top-N models ARE significant
    because vote count is a tiebreaker when rank UB and Arena Score match.
    """
    always_significant = [
        "new_models", "removed_models", "rank_changes", "rank_ub_changes",
        "score_changes", "ci_changes", "preliminary_changes",
    ]
    if diff.get("leaderboard_date_changed"):
        return True
    if any(diff.get(k) for k in always_significant):
        return True
    # Vote changes only matter for the top N models.
    for vc in diff.get("vote_changes", []):
        rank = vc.get("current_rank")
        if rank is not None and rank <= top_n_votes:
            return True
    return False


# ---------------------------------------------------------------------------
# Discord message formatting
# ---------------------------------------------------------------------------

def _sign(n: int | float) -> str:
    return f"+{n}" if n > 0 else str(n)


def format_discord_message(
    diff: dict,
    url: str,
    top_n: int = 10,
    overtake_data: dict | None = None,
    projections_data: dict | None = None,
) -> str:
    """Format a structured diff as a rich Discord notification.

    Focuses on the top *top_n* models for rank/score details and always
    highlights Rank UB changes (settlement-critical).

    If *overtake_data* (output of ``compute_all_overtake_probabilities``)
    is provided, an overtake-probability section is appended.

    If *projections_data* (output of ``enrich_snapshot_with_projections``)
    is provided, settlement projection sections are appended.
    """
    sections: list[str] = []
    sections.append("**Arena Leaderboard Update**")

    # Leaderboard date change
    if diff.get("leaderboard_date_changed"):
        prev_date = diff.get("previous_leaderboard_date", "?")
        curr_date = diff.get("current_leaderboard_date", "?")
        sections.append(f"Leaderboard refreshed: {prev_date} → {curr_date}")

    # Settlement-critical: Rank UB changes
    ub_changes = diff.get("rank_ub_changes", [])
    if ub_changes:
        sections.append("")
        sections.append("**⚠ RANK UB CHANGES (Settlement-Critical):**")
        for c in ub_changes:
            arrow = "↑" if c["delta"] < 0 else "↓"
            sections.append(
                f"  {arrow} {c['model_name']}: Rank UB {c['previous_rank_ub']} → {c['current_rank_ub']} ({_sign(c['delta'])})"
            )

    # New models
    new_models = diff.get("new_models", [])
    if new_models:
        sections.append("")
        sections.append("**New Models:**")
        for m in new_models[:top_n]:
            parts = [f"  #{m.get('rank', '?')} {m['model_name']}"]
            if m.get("score") is not None:
                parts.append(f"score {m['score']}")
            if m.get("is_preliminary"):
                parts.append("[Preliminary]")
            sections.append(" — ".join(parts))
        if len(new_models) > top_n:
            sections.append(f"  … and {len(new_models) - top_n} more")

    # Models removed
    removed = diff.get("removed_models", [])
    if removed:
        sections.append("")
        sections.append("**Models Removed:**")
        for m in removed[:top_n]:
            sections.append(f"  {m['model_name']} (was #{m.get('previous_rank', '?')})")
        if len(removed) > top_n:
            sections.append(f"  … and {len(removed) - top_n} more")

    # Rank changes (top N only)
    rank_changes = diff.get("rank_changes", [])
    top_rank_changes = [c for c in rank_changes if c.get("current_rank", 999) <= top_n or c.get("previous_rank", 999) <= top_n]
    if top_rank_changes:
        # Sort by current rank
        top_rank_changes.sort(key=lambda c: c.get("current_rank", 999))
        sections.append("")
        sections.append(f"**Rank Changes (Top {top_n}):**")
        for c in top_rank_changes[:top_n]:
            arrow = "↑" if c["delta"] < 0 else "↓"
            sections.append(
                f"  {arrow} {c['model_name']}: #{c['previous_rank']} → #{c['current_rank']} ({_sign(-c['delta'])})"
            )

    # Score changes — only models in contention for #1 (top 5 by rank)
    score_changes = diff.get("score_changes", [])
    if score_changes:
        contention_cutoff = 5
        contention_changes = [
            c for c in score_changes
            if c.get("current_rank") is not None and c["current_rank"] <= contention_cutoff
        ]
        contention_changes.sort(key=lambda c: c.get("current_rank", 999))
        if contention_changes:
            sections.append("")
            sections.append("**Score Changes (Top Contenders):**")
            for c in contention_changes:
                sections.append(
                    f"  {c['model_name']}: {c['previous_score']} → {c['current_score']} ({_sign(c['delta'])})"
                )

    # CI changes for top models
    ci_changes = diff.get("ci_changes", [])
    if ci_changes:
        notable_ci = [c for c in ci_changes if abs(c.get("delta", 0)) >= 2][:5]
        if notable_ci:
            sections.append("")
            sections.append("**CI Changes (notable):**")
            for c in notable_ci:
                direction = "narrowed" if c["delta"] < 0 else "widened"
                sections.append(
                    f"  {c['model_name']}: ±{c['previous_ci']} → ±{c['current_ci']} ({direction})"
                )

    # Preliminary status changes
    prelim_changes = diff.get("preliminary_changes", [])
    if prelim_changes:
        sections.append("")
        sections.append("**Preliminary Status:**")
        for c in prelim_changes:
            if c["is_preliminary"]:
                sections.append(f"  {c['model_name']} → now Preliminary")
            else:
                sections.append(f"  {c['model_name']} → no longer Preliminary")

    # Vote accumulation summary
    vote_changes = diff.get("vote_changes", [])
    if vote_changes:
        total_new_votes = sum(c.get("delta", 0) for c in vote_changes)
        if total_new_votes > 0:
            sections.append("")
            sections.append(f"Total new votes across all tracked models: +{total_new_votes:,}")

    # Overtake probabilities
    if overtake_data:
        try:
            from overtake_probability import format_overtake_section
            overtake_section = format_overtake_section(overtake_data)
            if overtake_section:
                sections.append(overtake_section)
        except Exception:
            pass

    # Settlement projections
    if projections_data:
        try:
            from projections import format_all_projections
            proj_section = format_all_projections(projections_data)
            if proj_section:
                sections.append(proj_section)
        except Exception:
            pass

    message = "\n".join(sections)
    return _truncate(message, MAX_DISCORD_MESSAGE_LENGTH, url)


def format_diff_summary(diff: dict) -> str:
    """One-line summary of what changed, suitable for logging."""
    parts: list[str] = []
    for key, label in [
        ("new_models", "new"),
        ("removed_models", "removed"),
        ("rank_changes", "rank Δ"),
        ("rank_ub_changes", "rank UB Δ"),
        ("score_changes", "score Δ"),
        ("ci_changes", "CI Δ"),
        ("vote_changes", "vote Δ"),
        ("preliminary_changes", "prelim Δ"),
    ]:
        count = len(diff.get(key, []))
        if count:
            parts.append(f"{count} {label}")
    if diff.get("leaderboard_date_changed"):
        parts.append("date refreshed")
    return ", ".join(parts) if parts else "no changes"


def format_snapshot_message(
    snapshot: dict,
    url: str,
    old_hash: str | None = None,
    new_hash: str | None = None,
    top_n: int = 10,
) -> str:
    """Format a message from a structured snapshot when no previous diff is available.

    Used when a change is detected but there's no prior structured snapshot to
    diff against (e.g. first run or cache eviction).  Lists the current top
    models so the notification is still informative.
    """
    sections: list[str] = []
    sections.append("**Arena Leaderboard Update**")

    date = snapshot.get("leaderboard_date")
    if date:
        sections.append(f"Leaderboard date: {date}")

    total_votes = snapshot.get("total_votes")
    if total_votes is not None:
        sections.append(f"Total votes: {total_votes:,}")

    models = snapshot.get("models", [])
    total_models = snapshot.get("total_models", len(models))
    sections.append(f"Total models tracked: {total_models}")

    if models:
        sections.append("")
        sections.append(f"**Current Top {min(top_n, len(models))}:**")
        for m in models[:top_n]:
            rank = m.get("rank", "?")
            name = m.get("model_name", "?")
            score = m.get("score")
            parts = [f"  #{rank} {name}"]
            if score is not None:
                parts.append(f"score {score}")
            if m.get("is_preliminary"):
                parts.append("[Preliminary]")
            sections.append(" — ".join(parts))

    # Overtake probabilities
    overtake = snapshot.get("overtake")
    if overtake:
        try:
            from overtake_probability import format_overtake_section
            overtake_section = format_overtake_section(overtake)
            if overtake_section:
                sections.append(overtake_section)
        except Exception:
            pass

    # Settlement projections
    proj = snapshot.get("projections")
    if proj:
        try:
            from projections import format_all_projections
            proj_section = format_all_projections(proj)
            if proj_section:
                sections.append(proj_section)
        except Exception:
            pass

    if old_hash or new_hash:
        sections.append("")
        if old_hash:
            sections.append(f"Previous fingerprint: {old_hash[:12]}")
        if new_hash:
            sections.append(f"New fingerprint: {new_hash[:12]}")

    sections.append("")
    sections.append("(No prior structured snapshot for detailed diff.)")

    message = "\n".join(sections)
    return _truncate(message, MAX_DISCORD_MESSAGE_LENGTH, url)


def _truncate(message: str, max_length: int, url: str) -> str:
    if len(message) <= max_length:
        return message
    suffix = f"\n… (truncated; see {url})"
    allowed = max(0, max_length - len(suffix))
    return message[:allowed].rstrip() + suffix
