#!/usr/bin/env python3
"""Query helpers for the leaderboard time series data.

All functions read from the JSONL time series and/or snapshot files.
Designed for both programmatic use and simple CLI invocation.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from snapshot_store import DEFAULT_TIMESERIES_DIR, load_timeseries, list_snapshots, load_snapshot


# ---------------------------------------------------------------------------
# Core query functions
# ---------------------------------------------------------------------------

def vote_accumulation_rate(
    model_name: str,
    days: int = 7,
    timeseries_dir: str | Path = DEFAULT_TIMESERIES_DIR,
) -> dict | None:
    """Compute the vote accumulation rate for a model over the last N days.

    Returns a dict with: model, start_votes, end_votes, delta, days_observed,
    votes_per_day, or None if insufficient data.
    """
    records = load_timeseries(timeseries_dir)
    if not records:
        return None

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    relevant: list[tuple[datetime, int]] = []

    for rec in records:
        ts = _parse_ts(rec.get("ts"))
        if ts is None:
            continue
        for m in rec.get("models", []):
            if m.get("name") == model_name and m.get("votes") is not None:
                if ts >= cutoff:
                    relevant.append((ts, m["votes"]))

    if len(relevant) < 2:
        return None

    relevant.sort(key=lambda x: x[0])
    start_ts, start_votes = relevant[0]
    end_ts, end_votes = relevant[-1]
    days_observed = max((end_ts - start_ts).total_seconds() / 86400, 0.01)

    return {
        "model": model_name,
        "start_votes": start_votes,
        "end_votes": end_votes,
        "delta": end_votes - start_votes,
        "days_observed": round(days_observed, 2),
        "votes_per_day": round((end_votes - start_votes) / days_observed, 1),
    }


def ci_threshold_date(
    model_name: str,
    threshold: int = 5,
    timeseries_dir: str | Path = DEFAULT_TIMESERIES_DIR,
) -> str | None:
    """Find the earliest timestamp when a model's CI dropped below a threshold.

    Returns the ISO timestamp string, or None if it never happened.
    """
    records = load_timeseries(timeseries_dir)
    for rec in records:
        for m in rec.get("models", []):
            if m.get("name") == model_name and m.get("ci") is not None:
                if m["ci"] < threshold:
                    return rec.get("ts")
    return None


def score_trajectory(
    model_names: list[str] | None = None,
    top_n: int = 5,
    days: int = 30,
    timeseries_dir: str | Path = DEFAULT_TIMESERIES_DIR,
) -> dict[str, list[dict]]:
    """Return the Elo score trajectory for models over the last N days.

    Args:
        model_names: Specific models to track. If None, uses the top N from
            the latest record.
        top_n: Number of top models to track if model_names is None.
        days: Number of days of history.

    Returns:
        A dict mapping model name → list of {ts, score, rank} records.
    """
    records = load_timeseries(timeseries_dir)
    if not records:
        return {}

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Determine which models to track
    if model_names is None:
        # Use latest record to pick top N
        latest = records[-1] if records else {}
        model_names = [m["name"] for m in latest.get("models", [])[:top_n] if m.get("name")]

    name_set = set(model_names)
    trajectories: dict[str, list[dict]] = {name: [] for name in model_names}

    for rec in records:
        ts = _parse_ts(rec.get("ts"))
        if ts is None or ts < cutoff:
            continue
        for m in rec.get("models", []):
            name = m.get("name")
            if name in name_set:
                point: dict = {"ts": rec.get("ts")}
                if m.get("score") is not None:
                    point["score"] = m["score"]
                if m.get("rank") is not None:
                    point["rank"] = m["rank"]
                if m.get("ci") is not None:
                    point["ci"] = m["ci"]
                trajectories[name].append(point)

    return trajectories


def rank_ub_changes(
    days: int = 7,
    timeseries_dir: str | Path = DEFAULT_TIMESERIES_DIR,
) -> list[dict]:
    """Find models whose Rank UB changed in the last N days.

    Returns a list of dicts: model, first_rank_ub, last_rank_ub, delta.
    """
    records = load_timeseries(timeseries_dir)
    if not records:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Track first and last rank_ub per model
    first_seen: dict[str, int] = {}
    last_seen: dict[str, int] = {}

    for rec in records:
        ts = _parse_ts(rec.get("ts"))
        if ts is None or ts < cutoff:
            continue
        for m in rec.get("models", []):
            name = m.get("name")
            ub = m.get("rank_ub")
            if name and ub is not None:
                if name not in first_seen:
                    first_seen[name] = ub
                last_seen[name] = ub

    changes = []
    for name in first_seen:
        first = first_seen[name]
        last = last_seen.get(name, first)
        if first != last:
            changes.append({
                "model": name,
                "first_rank_ub": first,
                "last_rank_ub": last,
                "delta": last - first,
            })

    changes.sort(key=lambda c: abs(c["delta"]), reverse=True)
    return changes


def days_at_top(
    timeseries_dir: str | Path = DEFAULT_TIMESERIES_DIR,
) -> dict | None:
    """Determine how many days the current #1 model has held the top position.

    Returns a dict with: model, days, first_seen_at_top, or None if no data.
    """
    records = load_timeseries(timeseries_dir)
    if not records:
        return None

    # Find current #1
    latest = records[-1] if records else {}
    top_models = latest.get("models", [])
    if not top_models:
        return None

    current_top = top_models[0].get("name")
    if not current_top:
        return None

    # Walk backwards to find when this model first appeared at #1
    first_at_top_ts = None
    for rec in records:
        models = rec.get("models", [])
        if models and models[0].get("name") == current_top:
            ts_str = rec.get("ts")
            if ts_str:
                first_at_top_ts = ts_str

    if not first_at_top_ts:
        return None

    # Walk forward to find the earliest continuous run at #1
    earliest_continuous = None
    for rec in records:
        models = rec.get("models", [])
        if models and models[0].get("name") == current_top:
            if earliest_continuous is None:
                earliest_continuous = rec.get("ts")
        else:
            earliest_continuous = None

    if earliest_continuous is None:
        earliest_continuous = first_at_top_ts

    first_dt = _parse_ts(earliest_continuous)
    if first_dt is None:
        return None

    days_held = (datetime.now(timezone.utc) - first_dt).total_seconds() / 86400

    return {
        "model": current_top,
        "days": round(days_held, 1),
        "first_seen_at_top": earliest_continuous,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_ts(ts_str: str | None) -> datetime | None:
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# CLI interface
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Query leaderboard time series data.")
    parser.add_argument(
        "--timeseries-dir",
        type=Path,
        default=Path(DEFAULT_TIMESERIES_DIR),
        help=f"Directory containing JSONL time series (default: {DEFAULT_TIMESERIES_DIR})",
    )
    sub = parser.add_subparsers(dest="command")

    # vote-rate
    vr = sub.add_parser("vote-rate", help="Vote accumulation rate for a model")
    vr.add_argument("model", help="Model name")
    vr.add_argument("--days", type=int, default=7, help="Look-back window in days")

    # ci-threshold
    ct = sub.add_parser("ci-threshold", help="When did a model's CI drop below a threshold?")
    ct.add_argument("model", help="Model name")
    ct.add_argument("--threshold", type=int, default=5, help="CI threshold value")

    # score-trajectory
    st = sub.add_parser("score-trajectory", help="Elo score trajectory")
    st.add_argument("--models", nargs="*", help="Model names (default: top 5)")
    st.add_argument("--top-n", type=int, default=5, help="Number of top models if --models not given")
    st.add_argument("--days", type=int, default=30, help="Look-back window in days")

    # rank-ub-changes
    rc = sub.add_parser("rank-ub-changes", help="Models with Rank UB changes")
    rc.add_argument("--days", type=int, default=7, help="Look-back window in days")

    # days-at-top
    sub.add_parser("days-at-top", help="How long has the current #1 held the top spot?")

    args = parser.parse_args()
    ts_dir = args.timeseries_dir

    if args.command == "vote-rate":
        result = vote_accumulation_rate(args.model, days=args.days, timeseries_dir=ts_dir)
        if result is None:
            print(f"Insufficient data for model '{args.model}' in the last {args.days} days.")
            return 1
        print(json.dumps(result, indent=2))

    elif args.command == "ci-threshold":
        result = ci_threshold_date(args.model, threshold=args.threshold, timeseries_dir=ts_dir)
        if result is None:
            print(f"Model '{args.model}' CI never dropped below ±{args.threshold}.")
            return 0
        print(f"Model '{args.model}' CI first dropped below ±{args.threshold} at: {result}")

    elif args.command == "score-trajectory":
        result = score_trajectory(
            model_names=args.models, top_n=args.top_n, days=args.days, timeseries_dir=ts_dir
        )
        if not result:
            print("No trajectory data found.")
            return 1
        print(json.dumps(result, indent=2))

    elif args.command == "rank-ub-changes":
        result = rank_ub_changes(days=args.days, timeseries_dir=ts_dir)
        if not result:
            print(f"No Rank UB changes in the last {args.days} days.")
            return 0
        print(json.dumps(result, indent=2))

    elif args.command == "days-at-top":
        result = days_at_top(timeseries_dir=ts_dir)
        if result is None:
            print("No data available.")
            return 1
        print(f"{result['model']} has held #1 for {result['days']} days (since {result['first_seen_at_top']})")

    else:
        parser.print_help()
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
