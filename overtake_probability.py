#!/usr/bin/env python3
"""Compute the probability that a challenger model overtakes the current #1.

Useful for pricing Kalshi prediction-market contracts: the overtake
probability is the fair value of a "Yes" contract, and
(1 - overtake_probability) is the fair value of a "No" contract.

The math models each Arena score as N(μ, σ²) where σ = CI / 1.96.
"""

from __future__ import annotations

import math
from typing import Optional


# ---------------------------------------------------------------------------
# Core math
# ---------------------------------------------------------------------------

def _normal_sf(z: float) -> float:
    """Survival function of the standard normal (1 - CDF) using math.erfc."""
    return 0.5 * math.erfc(z / math.sqrt(2))


def compute_overtake_probability(
    score_a: int | float,
    ci_a: int | float,
    score_b: int | float,
    ci_b: int | float,
) -> float:
    """Probability that model B's true strength exceeds model A's.

    Args:
        score_a: Arena score of model A (the leader).
        ci_a: ± 95 % confidence interval of model A.
        score_b: Arena score of model B (the challenger).
        ci_b: ± 95 % confidence interval of model B.

    Returns:
        Probability between 0 and 1 that B overtakes A.
    """
    # Edge case: zero CI on both sides.
    if ci_a == 0 and ci_b == 0:
        if score_b > score_a:
            return 1.0
        if score_b < score_a:
            return 0.0
        return 0.5

    sigma_a = ci_a / 1.96
    sigma_b = ci_b / 1.96
    sigma_diff = math.sqrt(sigma_a ** 2 + sigma_b ** 2)

    if sigma_diff == 0:
        # Both CIs round to zero after division — treat as deterministic.
        gap = score_a - score_b
        return 1.0 if gap < 0 else (0.0 if gap > 0 else 0.5)

    gap = score_a - score_b
    z = gap / sigma_diff
    return _normal_sf(z)


# ---------------------------------------------------------------------------
# Projected probability with more votes
# ---------------------------------------------------------------------------

def projected_overtake_probability(
    score_a: int | float,
    ci_a: int | float,
    votes_a: int,
    score_b: int | float,
    ci_b: int | float,
    votes_b: int,
    future_votes_multiplier: float,
) -> float:
    """Estimate overtake probability after more votes are collected.

    CIs shrink proportional to 1/sqrt(n), so if votes double the CI
    shrinks by 1/sqrt(2).  Assumes the score gap remains constant
    (conservative estimate).

    Args:
        future_votes_multiplier: How many times the current votes each
            model will have (e.g. 2.0 means votes double).

    Returns:
        Projected overtake probability.
    """
    if future_votes_multiplier <= 0:
        raise ValueError("future_votes_multiplier must be positive")

    shrink = 1.0 / math.sqrt(future_votes_multiplier)
    return compute_overtake_probability(
        score_a, ci_a * shrink,
        score_b, ci_b * shrink,
    )


# ---------------------------------------------------------------------------
# Batch computation from a snapshot
# ---------------------------------------------------------------------------

# Models with z-score beyond this threshold are reported as "<0.01%".
_Z_THRESHOLD = 6.0


def compute_all_overtake_probabilities(
    snapshot: dict,
    top_n: int = 20,
) -> dict:
    """Compute overtake probabilities for all top models vs the current #1.

    Args:
        snapshot: A structured snapshot dict (with "models" list).
        top_n: Only consider the top N models.

    Returns:
        A dict with "leader" info and an "overtake_probabilities" list.
    """
    models = snapshot.get("models", [])
    if not models:
        return {"leader": None, "overtake_probabilities": []}

    # Models should already be sorted by rank.
    leader = models[0]
    leader_score = leader.get("score")
    leader_ci = leader.get("ci")

    if leader_score is None or leader_ci is None:
        return {"leader": None, "overtake_probabilities": []}

    results: list[dict] = []

    for m in models[1:top_n]:
        score = m.get("score")
        ci = m.get("ci")
        if score is None or ci is None:
            continue

        gap = leader_score - score
        prob = compute_overtake_probability(leader_score, leader_ci, score, ci)

        entry: dict = {
            "model_name": m.get("model_name"),
            "rank": m.get("rank"),
            "score": score,
            "ci": ci,
            "score_gap": gap,
            "overtake_prob": prob,
            "fair_no_price_cents": round((1.0 - prob) * 100, 4),
        }
        if m.get("rank_ub") is not None:
            entry["rank_ub"] = m["rank_ub"]
        if m.get("is_preliminary"):
            entry["is_preliminary"] = True

        results.append(entry)

    # For the leader: probability of being overtaken by #2.
    prob_staying_1 = 1.0
    if results:
        prob_staying_1 = 1.0 - results[0]["overtake_prob"]

    leader_info = {
        "model_name": leader.get("model_name"),
        "score": leader_score,
        "ci": leader_ci,
        "prob_staying_1": round(prob_staying_1, 6),
    }
    if leader.get("rank_ub") is not None:
        leader_info["rank_ub"] = leader["rank_ub"]

    return {
        "leader": leader_info,
        "overtake_probabilities": results,
    }


# ---------------------------------------------------------------------------
# Enrichment: attach overtake data to a snapshot dict
# ---------------------------------------------------------------------------

def enrich_snapshot(snapshot: dict, top_n: int = 20) -> dict:
    """Add an ``overtake_probabilities`` field to *snapshot* (in-place).

    Returns the overtake data dict for convenience.
    """
    data = compute_all_overtake_probabilities(snapshot, top_n=top_n)
    snapshot["overtake"] = data
    return data


# ---------------------------------------------------------------------------
# Head-to-head win rate (Bradley-Terry)
# ---------------------------------------------------------------------------

_ELO_SCALE = 400


def head_to_head_win_rate(score_a: float, score_b: float) -> float:
    """Predicted probability that model A beats model B in a single battle.

    Uses the Bradley-Terry / Elo formula:
      P(A beats B) = 1 / (1 + 10^((score_B - score_A) / 400))
    """
    return 1.0 / (1.0 + 10 ** ((score_b - score_a) / _ELO_SCALE))


def compute_h2h_vs_leader(
    snapshot: dict,
    top_n: int = 5,
) -> dict:
    """Compute head-to-head predicted win rates for top models vs #1.

    Returns a dict with ``leader`` info and a ``matchups`` list, each entry
    containing the predicted H2H win rate of the leader against that model.
    """
    models = snapshot.get("models", [])
    if not models:
        return {"leader": None, "matchups": []}

    leader = models[0]
    leader_score = leader.get("score")
    if leader_score is None:
        return {"leader": None, "matchups": []}

    matchups: list[dict] = []
    for m in models[1:top_n]:
        score = m.get("score")
        if score is None:
            continue
        # Win rate of the challenger against the leader.
        challenger_wr = head_to_head_win_rate(score, leader_score)
        matchups.append({
            "model_name": m.get("model_name"),
            "rank": m.get("rank"),
            "score": score,
            "win_rate_vs_leader": round(challenger_wr, 4),
            "score_gap": leader_score - score,
        })

    return {
        "leader": {
            "model_name": leader.get("model_name"),
            "score": leader_score,
        },
        "matchups": matchups,
    }


def enrich_snapshot_with_h2h(snapshot: dict, top_n: int = 5) -> dict:
    """Add ``h2h`` field to *snapshot* (in-place)."""
    data = compute_h2h_vs_leader(snapshot, top_n=top_n)
    snapshot["h2h"] = data
    return data


def format_h2h_section(h2h_data: dict) -> str:
    """Format head-to-head win rates as a Discord message section."""
    leader = h2h_data.get("leader")
    matchups = h2h_data.get("matchups", [])
    if not leader or not matchups:
        return ""

    leader_name = leader.get("model_name", "?")
    lines: list[str] = []
    lines.append(f"\n**Head-to-Head Win Rates (vs #1 {leader_name}):**")
    for m in matchups:
        rank = m.get("rank", "?")
        name = m["model_name"]
        wr = m["win_rate_vs_leader"]
        gap = m["score_gap"]
        wr_pct = f"{wr * 100:.1f}%"
        gap_str = f"{gap:+d}pt" if isinstance(gap, int) else f"{gap:+.0f}pt"
        # Highlight if challenger is favored (>50%).
        marker = " \u2191" if wr > 0.50 else ""
        lines.append(f"  #{rank} {name}: {wr_pct}{marker} ({gap_str})")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Discord formatting
# ---------------------------------------------------------------------------

def format_overtake_section(
    overtake_data: dict,
    threshold: float = 0.0001,
    max_lines: int = 5,
) -> str:
    """Format overtake probabilities as a Discord message section.

    Args:
        overtake_data: Output of ``compute_all_overtake_probabilities``.
        threshold: Only show models with overtake_prob >= this value.
        max_lines: Maximum number of model lines to include.

    Returns:
        A string section ready to append to a Discord message, or ""
        if there is nothing to report.
    """
    leader = overtake_data.get("leader")
    probs = overtake_data.get("overtake_probabilities", [])
    if not leader or not probs:
        return ""

    leader_name = leader.get("model_name", "?")
    lines: list[str] = []
    lines.append(f"\n**Overtake Probabilities (vs #1 {leader_name}):**")

    shown = 0
    below_threshold = 0
    for entry in probs:
        prob = entry["overtake_prob"]
        if prob < threshold:
            below_threshold += 1
            continue
        if shown >= max_lines:
            below_threshold += 1
            continue

        rank = entry.get("rank", "?")
        name = entry["model_name"]
        fair_no = entry["fair_no_price_cents"]

        if prob < 0.0001:
            prob_str = "<0.01%"
        else:
            prob_str = f"{prob * 100:.1f}%"

        fair_no_str = f"{fair_no:.0f}" if fair_no >= 1 else f"{fair_no:.1f}"
        line = f"  #{rank} {name}: {prob_str} (fair No: {fair_no_str}\u00a2)"

        if entry.get("is_preliminary"):
            line += " \u26a0\ufe0f Preliminary"

        lines.append(line)
        shown += 1

    if below_threshold:
        lines.append(f"  All others: <0.01%")

    return "\n".join(lines)
