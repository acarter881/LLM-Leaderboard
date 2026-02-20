#!/usr/bin/env python3
"""Settlement-date projections for Kalshi-style prediction market contracts.

Combines vote accumulation rates from time series data with CI-shrinkage
math to project overtake probabilities at specific future settlement dates.

Kalshi contract types this module targets:
  - Weekly (KXTOPMODEL): settles Saturday, based on highest Arena Score.
  - Monthly (KXLLM1):    settles last day of month, same criterion.

The key insight: CI shrinks as 1/sqrt(n) where n is the total vote count.
If a model is accumulating votes at a known rate, we can project what its
CI will be at settlement and compute the overtake probability at that date
rather than only at the current instant.
"""

from __future__ import annotations

import calendar
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from overtake_probability import compute_overtake_probability

WEEKLY = "weekly"
MONTHLY = "monthly"

# Saturday = 5 in Python's weekday() (Monday=0 .. Sunday=6).
_SATURDAY = 5


# ---------------------------------------------------------------------------
# Settlement date helpers
# ---------------------------------------------------------------------------

def next_settlement_date(
    cadence: str = WEEKLY,
    from_date: Optional[datetime] = None,
) -> datetime:
    """Compute the next Kalshi contract settlement date.

    Weekly contracts settle on Saturday.  Monthly contracts settle on the
    last calendar day of the month.  Both settle at noon ET (17:00 UTC in
    EST; this is an approximation — EDT would be 16:00 UTC).

    If *from_date* falls exactly on a settlement day but before the
    settlement hour, that same day is returned.
    """
    if from_date is None:
        from_date = datetime.now(timezone.utc)

    settlement_hour_utc = 17  # noon ET ≈ 17:00 UTC

    if cadence == WEEKLY:
        days_ahead = (_SATURDAY - from_date.weekday()) % 7
        candidate = from_date + timedelta(days=days_ahead)
        candidate = candidate.replace(
            hour=settlement_hour_utc, minute=0, second=0, microsecond=0,
        )
        if candidate <= from_date:
            candidate += timedelta(days=7)
        return candidate

    if cadence == MONTHLY:
        last_day = calendar.monthrange(from_date.year, from_date.month)[1]
        candidate = from_date.replace(
            day=last_day,
            hour=settlement_hour_utc, minute=0, second=0, microsecond=0,
        )
        if candidate <= from_date:
            # Roll to last day of next month.
            if from_date.month == 12:
                next_year, next_month = from_date.year + 1, 1
            else:
                next_year, next_month = from_date.year, from_date.month + 1
            last_day = calendar.monthrange(next_year, next_month)[1]
            candidate = from_date.replace(
                year=next_year, month=next_month, day=last_day,
                hour=settlement_hour_utc, minute=0, second=0, microsecond=0,
            )
        return candidate

    raise ValueError(f"Unknown cadence: {cadence!r}. Use 'weekly' or 'monthly'.")


def days_until(
    target: datetime,
    from_date: Optional[datetime] = None,
) -> float:
    """Fractional days remaining until *target*.

    Returns 0.0 if *target* is in the past.
    """
    if from_date is None:
        from_date = datetime.now(timezone.utc)
    delta = target - from_date
    return max(delta.total_seconds() / 86400.0, 0.0)


# ---------------------------------------------------------------------------
# CI projection
# ---------------------------------------------------------------------------

def project_ci(
    current_ci: float,
    current_votes: int,
    votes_per_day: float,
    days_ahead: float,
) -> float:
    """Project a model's CI at a future date based on vote accumulation.

    CI shrinks as 1/sqrt(n) where n is the total vote count.  Given a
    constant accumulation rate, projected_votes = current + vpd * days,
    and projected_ci = current_ci * sqrt(current / projected).
    """
    if current_votes <= 0 or current_ci <= 0 or days_ahead <= 0:
        return float(current_ci)
    projected_votes = current_votes + votes_per_day * days_ahead
    if projected_votes <= current_votes:
        return float(current_ci)
    shrink = math.sqrt(current_votes / projected_votes)
    return current_ci * shrink


# ---------------------------------------------------------------------------
# Time-aware overtake probability
# ---------------------------------------------------------------------------

def projected_overtake_at_date(
    score_a: float, ci_a: float, votes_a: int, vpd_a: float,
    score_b: float, ci_b: float, votes_b: int, vpd_b: float,
    days_ahead: float,
) -> dict:
    """Compute overtake probability now and projected at a future date.

    Assumes score gap stays constant (conservative) and CIs shrink based
    on vote accumulation rates.

    Returns a dict with current and projected probabilities plus the
    projected CI values.
    """
    prob_now = compute_overtake_probability(score_a, ci_a, score_b, ci_b)

    proj_ci_a = project_ci(ci_a, votes_a, vpd_a, days_ahead)
    proj_ci_b = project_ci(ci_b, votes_b, vpd_b, days_ahead)
    prob_at_settlement = compute_overtake_probability(
        score_a, proj_ci_a, score_b, proj_ci_b,
    )

    return {
        "prob_now": prob_now,
        "prob_at_settlement": prob_at_settlement,
        "days_ahead": days_ahead,
        "proj_ci_a": round(proj_ci_a, 2),
        "proj_ci_b": round(proj_ci_b, 2),
        "proj_votes_a": round(votes_a + vpd_a * days_ahead),
        "proj_votes_b": round(votes_b + vpd_b * days_ahead),
    }


def time_to_resolution(
    score_a: float, ci_a: float, votes_a: int, vpd_a: float,
    score_b: float, ci_b: float, votes_b: int, vpd_b: float,
    threshold: float = 0.05,
    max_days: float = 365.0,
    step: float = 0.25,
) -> Optional[float]:
    """Estimate when overtake probability drops below *threshold*.

    Scans forward in time at *step*-day increments until the overtake
    probability falls below the threshold, indicating the ranking has
    effectively "locked in."

    Returns fractional days, or None if it won't happen within *max_days*.
    """
    if compute_overtake_probability(score_a, ci_a, score_b, ci_b) < threshold:
        return 0.0

    days = step
    while days <= max_days:
        proj_ci_a = project_ci(ci_a, votes_a, vpd_a, days)
        proj_ci_b = project_ci(ci_b, votes_b, vpd_b, days)
        prob = compute_overtake_probability(score_a, proj_ci_a, score_b, proj_ci_b)
        if prob < threshold:
            return days
        days += step
    return None


# ---------------------------------------------------------------------------
# Bulk vote rate computation (single pass over timeseries)
# ---------------------------------------------------------------------------

def _parse_ts(ts_str: Optional[str]) -> Optional[datetime]:
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def bulk_vote_rates(
    timeseries: list[dict],
    model_names: set[str],
    lookback_days: float = 7.0,
    now: Optional[datetime] = None,
) -> dict[str, float]:
    """Compute votes_per_day for multiple models in a single timeseries pass.

    Returns a dict mapping model name to votes_per_day (0.0 if insufficient
    data).  Uses the timeseries ``"name"`` field (not ``"model_name"``).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=lookback_days)

    # Track earliest and latest (ts, votes) per model in the window.
    earliest: dict[str, tuple[datetime, int]] = {}
    latest: dict[str, tuple[datetime, int]] = {}

    for record in timeseries:
        ts = _parse_ts(record.get("ts"))
        if ts is None or ts < cutoff:
            continue
        for m in record.get("models", []):
            name = m.get("name")
            if name not in model_names or m.get("votes") is None:
                continue
            votes = m["votes"]
            if name not in earliest or ts < earliest[name][0]:
                earliest[name] = (ts, votes)
            if name not in latest or ts > latest[name][0]:
                latest[name] = (ts, votes)

    rates: dict[str, float] = {}
    for name in model_names:
        if (
            name in earliest
            and name in latest
            and earliest[name][0] != latest[name][0]
        ):
            elapsed = (latest[name][0] - earliest[name][0]).total_seconds() / 86400.0
            delta = latest[name][1] - earliest[name][1]
            rates[name] = max(delta / max(elapsed, 0.01), 0.0)
        else:
            rates[name] = 0.0

    return rates


# ---------------------------------------------------------------------------
# Batch computation from a snapshot
# ---------------------------------------------------------------------------

def compute_settlement_projections(
    snapshot: dict,
    timeseries: Optional[list[dict]] = None,
    settlement_date: Optional[datetime] = None,
    cadence: str = WEEKLY,
    top_n: int = 10,
    rate_lookback_days: float = 7.0,
    now: Optional[datetime] = None,
) -> dict:
    """Compute projected overtake probabilities at a settlement date.

    Args:
        snapshot: A structured snapshot dict (with ``"models"`` list).
        timeseries: Pre-loaded timeseries records.  If None, an empty
            list is used (vote rates default to 0, projections degrade
            to current probabilities).
        settlement_date: Explicit settlement datetime.  If None,
            computed from *cadence*.
        cadence: ``"weekly"`` or ``"monthly"`` (used if settlement_date
            is None).
        top_n: Number of top models to analyse.
        rate_lookback_days: Days of history for vote rate estimation.
        now: Override for "current time" (useful for testing).

    Returns:
        A dict with ``settlement_date``, ``days_remaining``, ``cadence``,
        ``leader``, and ``projections`` list.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    if settlement_date is None:
        settlement_date = next_settlement_date(cadence, from_date=now)
    remaining = days_until(settlement_date, from_date=now)

    models = snapshot.get("models", [])
    if not models:
        return _empty_projections(settlement_date, remaining, cadence)

    leader = models[0]
    leader_score = leader.get("score")
    leader_ci = leader.get("ci")
    leader_votes = leader.get("votes")
    if leader_score is None or leader_ci is None:
        return _empty_projections(settlement_date, remaining, cadence)

    # Gather model names for vote rate lookup.
    model_names = {
        m.get("model_name") for m in models[:top_n] if m.get("model_name")
    }
    rates = bulk_vote_rates(
        timeseries or [], model_names, lookback_days=rate_lookback_days, now=now,
    )

    leader_name = leader.get("model_name", "?")
    leader_vpd = rates.get(leader_name, 0.0)

    projections: list[dict] = []
    for m in models[1:top_n]:
        score = m.get("score")
        ci = m.get("ci")
        votes = m.get("votes")
        name = m.get("model_name")
        if score is None or ci is None or name is None:
            continue

        vpd = rates.get(name, 0.0)
        result = projected_overtake_at_date(
            leader_score, leader_ci, leader_votes or 0, leader_vpd,
            score, ci, votes or 0, vpd,
            remaining,
        )
        result["model_name"] = name
        result["rank"] = m.get("rank")
        result["score"] = score
        result["ci"] = ci
        result["votes"] = votes
        result["votes_per_day"] = round(vpd, 1)
        result["organization"] = m.get("organization")

        # Time to resolution (when does overtake become <5% likely?)
        ttr = time_to_resolution(
            leader_score, leader_ci, leader_votes or 0, leader_vpd,
            score, ci, votes or 0, vpd,
        )
        result["days_to_lock"] = round(ttr, 1) if ttr is not None else None

        projections.append(result)

    # Organisation-level aggregation: for each org, what's the max overtake
    # probability at settlement from any of their models?
    org_probs: dict[str, float] = {}
    leader_org = leader.get("organization")
    for p in projections:
        org = p.get("organization")
        if org and org != leader_org:
            org_probs[org] = max(org_probs.get(org, 0.0), p["prob_at_settlement"])

    return {
        "settlement_date": settlement_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "settlement_label": settlement_date.strftime("%a %b %d"),
        "days_remaining": round(remaining, 2),
        "cadence": cadence,
        "leader": {
            "model_name": leader_name,
            "score": leader_score,
            "ci": leader_ci,
            "votes": leader_votes,
            "votes_per_day": round(leader_vpd, 1),
            "organization": leader_org,
            "proj_ci": round(project_ci(leader_ci, leader_votes or 0, leader_vpd, remaining), 2),
        },
        "projections": projections,
        "org_projections": [
            {"organization": org, "max_overtake_prob": round(prob, 6)}
            for org, prob in sorted(org_probs.items(), key=lambda x: -x[1])
        ],
    }


def _empty_projections(
    settlement_date: datetime, remaining: float, cadence: str,
) -> dict:
    return {
        "settlement_date": settlement_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "settlement_label": settlement_date.strftime("%a %b %d"),
        "days_remaining": round(remaining, 2),
        "cadence": cadence,
        "leader": None,
        "projections": [],
        "org_projections": [],
    }


# ---------------------------------------------------------------------------
# Snapshot enrichment
# ---------------------------------------------------------------------------

def enrich_snapshot_with_projections(
    snapshot: dict,
    timeseries: Optional[list[dict]] = None,
    top_n: int = 10,
    rate_lookback_days: float = 7.0,
    now: Optional[datetime] = None,
) -> dict:
    """Add settlement projections for both weekly and monthly cadences.

    Writes ``snapshot["projections"]`` in-place and returns the dict of
    projection results keyed by cadence.
    """
    results: dict[str, dict] = {}
    for cadence in (WEEKLY, MONTHLY):
        results[cadence] = compute_settlement_projections(
            snapshot,
            timeseries=timeseries,
            cadence=cadence,
            top_n=top_n,
            rate_lookback_days=rate_lookback_days,
            now=now,
        )
    snapshot["projections"] = results
    return results


# ---------------------------------------------------------------------------
# Discord formatting
# ---------------------------------------------------------------------------

def format_projections_section(
    projections_data: dict,
    threshold: float = 0.0001,
    max_lines: int = 5,
) -> str:
    """Format one cadence's projections as a Discord message section.

    Args:
        projections_data: Output of ``compute_settlement_projections``
            for a single cadence.
        threshold: Minimum probability to show individually.
        max_lines: Maximum model lines to include.

    Returns:
        A string section ready to append to a Discord message, or ""
        if there is nothing to report.
    """
    leader = projections_data.get("leader")
    projections = projections_data.get("projections", [])
    if not leader or not projections:
        return ""

    label = projections_data.get("settlement_label", "?")
    days_left = projections_data.get("days_remaining", 0)
    cadence = projections_data.get("cadence", "?")
    header_tag = "Weekly" if cadence == WEEKLY else "Monthly"

    lines: list[str] = []
    lines.append(
        f"\n**{header_tag} Settlement Projections ({label}, {days_left:.1f}d):**"
    )

    # Leader CI projection
    leader_ci = leader.get("ci")
    proj_ci = leader.get("proj_ci")
    leader_vpd = leader.get("votes_per_day", 0)
    if leader_ci and proj_ci and leader_ci != proj_ci:
        lines.append(
            f"  #1 {leader['model_name']}: "
            f"CI ±{leader_ci} → ±{proj_ci} "
            f"({leader_vpd:.0f} votes/day)"
        )

    shown = 0
    below_threshold = 0
    for entry in projections:
        prob_now = entry["prob_now"]
        prob_settle = entry["prob_at_settlement"]
        if prob_now < threshold and prob_settle < threshold:
            below_threshold += 1
            continue
        if shown >= max_lines:
            below_threshold += 1
            continue

        rank = entry.get("rank", "?")
        name = entry["model_name"]
        vpd = entry.get("votes_per_day", 0)

        now_str = _fmt_prob(prob_now)
        settle_str = _fmt_prob(prob_settle)

        lock_str = ""
        dtl = entry.get("days_to_lock")
        if dtl is not None and dtl > 0:
            lock_str = f" | locks in ~{dtl:.0f}d"

        lines.append(
            f"  #{rank} {name}: {now_str} now → {settle_str} at settlement"
            f" ({vpd:.0f} v/d{lock_str})"
        )
        shown += 1

    if below_threshold:
        lines.append(f"  All others: <0.01%")

    # Org-level summary
    org_projs = projections_data.get("org_projections", [])
    notable_orgs = [o for o in org_projs if o["max_overtake_prob"] >= threshold]
    if notable_orgs:
        leader_org = leader.get("organization", "?")
        lines.append(f"  **Org risk (vs {leader_org}):** " + ", ".join(
            f"{o['organization']} {_fmt_prob(o['max_overtake_prob'])}"
            for o in notable_orgs[:4]
        ))

    return "\n".join(lines)


def format_all_projections(
    projections_by_cadence: dict,
    threshold: float = 0.0001,
    max_lines: int = 5,
) -> str:
    """Format weekly + monthly projections for Discord.

    Args:
        projections_by_cadence: Output of ``enrich_snapshot_with_projections``,
            keyed by cadence (``"weekly"``, ``"monthly"``).

    Returns:
        Combined string of all cadence sections.
    """
    parts: list[str] = []
    for cadence in (WEEKLY, MONTHLY):
        data = projections_by_cadence.get(cadence)
        if data:
            section = format_projections_section(
                data, threshold=threshold, max_lines=max_lines,
            )
            if section:
                parts.append(section)
    return "\n".join(parts)


def _fmt_prob(prob: float) -> str:
    if prob < 0.0001:
        return "<0.01%"
    if prob >= 0.9999:
        return ">99.99%"
    return f"{prob * 100:.1f}%"
