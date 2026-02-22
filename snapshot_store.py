#!/usr/bin/env python3
"""Persist leaderboard snapshots as JSON files and a compact JSONL time series."""

from __future__ import annotations

import gzip
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


DEFAULT_SNAPSHOT_DIR = "data/snapshots"
DEFAULT_TIMESERIES_DIR = "data/timeseries"
DEFAULT_TOP_N = 20


# ---------------------------------------------------------------------------
# Snapshot storage (full JSON files)
# ---------------------------------------------------------------------------

def save_snapshot(
    snapshot: dict,
    snapshot_dir: str | Path = DEFAULT_SNAPSHOT_DIR,
    compress: bool = True,
) -> Path:
    """Write a full snapshot to a timestamped JSON file.

    Args:
        snapshot: The parsed snapshot dict from ``leaderboard_parser``.
        snapshot_dir: Directory for snapshot files.
        compress: If True, gzip the JSON to save space (~5x reduction).

    Returns:
        The path to the written file.
    """
    snapshot_dir = Path(snapshot_dir)
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    ts = snapshot.get("timestamp", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    # Convert ISO timestamp to filename-safe format
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        dt = datetime.now(timezone.utc)
    filename = dt.strftime("%Y%m%d_%H%M%S")

    content = json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n"

    if compress:
        filepath = snapshot_dir / f"{filename}.json.gz"
        filepath.write_bytes(gzip.compress(content.encode("utf-8")))
    else:
        filepath = snapshot_dir / f"{filename}.json"
        filepath.write_text(content, encoding="utf-8")

    return filepath


def load_snapshot(filepath: str | Path) -> dict | None:
    """Load a snapshot from a JSON or gzipped JSON file."""
    filepath = Path(filepath)
    if not filepath.exists():
        return None
    try:
        if filepath.suffix == ".gz":
            data = gzip.decompress(filepath.read_bytes()).decode("utf-8")
        else:
            data = filepath.read_text(encoding="utf-8")
        return json.loads(data)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"Warning: failed to load snapshot {filepath}: {exc}", file=sys.stderr)
        return None


def list_snapshots(snapshot_dir: str | Path = DEFAULT_SNAPSHOT_DIR) -> list[Path]:
    """Return snapshot file paths sorted oldest-first."""
    snapshot_dir = Path(snapshot_dir)
    if not snapshot_dir.exists():
        return []
    files = []
    for f in snapshot_dir.iterdir():
        if f.name.endswith(".json") or f.name.endswith(".json.gz"):
            files.append(f)
    files.sort(key=lambda p: p.name)
    return files


def load_latest_snapshot(snapshot_dir: str | Path = DEFAULT_SNAPSHOT_DIR) -> dict | None:
    """Load the most recent snapshot from the snapshot directory."""
    files = list_snapshots(snapshot_dir)
    if not files:
        return None
    return load_snapshot(files[-1])


# ---------------------------------------------------------------------------
# JSONL time series (top-N compact tracking)
# ---------------------------------------------------------------------------

def append_top_n(
    snapshot: dict,
    top_n: int = DEFAULT_TOP_N,
    timeseries_dir: str | Path = DEFAULT_TIMESERIES_DIR,
) -> Path:
    """Append a compact top-N record to the JSONL time series file.

    Args:
        snapshot: The full snapshot dict.
        top_n: Number of top models to include.
        timeseries_dir: Directory for time series files.

    Returns:
        The path to the JSONL file.
    """
    timeseries_dir = Path(timeseries_dir)
    timeseries_dir.mkdir(parents=True, exist_ok=True)
    filepath = timeseries_dir / "top20.jsonl"

    models = snapshot.get("models", [])
    # Models should already be sorted by rank from the parser
    top_models = []
    for m in models[:top_n]:
        compact = {
            "rank": m.get("rank"),
            "name": m.get("model_name"),
            "score": m.get("score"),
        }
        if m.get("ci") is not None:
            compact["ci"] = m["ci"]
        if m.get("votes") is not None:
            compact["votes"] = m["votes"]
        if m.get("rank_ub") is not None:
            compact["rank_ub"] = m["rank_ub"]
        top_models.append(compact)

    # Include top overtake probabilities if available.
    overtake = snapshot.get("overtake")
    overtake_compact: list[dict] = []
    if overtake and overtake.get("overtake_probabilities"):
        for entry in overtake["overtake_probabilities"][:5]:
            overtake_compact.append({
                "name": entry.get("model_name"),
                "prob": round(entry.get("overtake_prob", 0), 6),
                "gap": entry.get("score_gap"),
            })

    record: dict = {
        "ts": snapshot.get("timestamp"),
        "date": snapshot.get("leaderboard_date"),
        "models": top_models,
    }
    if overtake_compact:
        record["overtake_top5"] = overtake_compact
    if overtake and overtake.get("leader"):
        record["leader_prob_staying_1"] = overtake["leader"].get("prob_staying_1")

    # Include settlement projection summaries if available.
    projections = snapshot.get("projections")
    if projections:
        proj_compact: dict = {}
        for cadence in ("weekly", "monthly"):
            data = projections.get(cadence)
            if data and data.get("projections"):
                proj_compact[cadence] = {
                    "settlement": data.get("settlement_label"),
                    "days": data.get("days_remaining"),
                    "top3": [
                        {
                            "name": p.get("model_name"),
                            "now": round(p.get("prob_now", 0), 6),
                            "settle": round(p.get("prob_at_settlement", 0), 6),
                        }
                        for p in data["projections"][:3]
                    ],
                }
        if proj_compact:
            record["projections"] = proj_compact

    line = json.dumps(record, ensure_ascii=False) + "\n"
    with open(filepath, "a", encoding="utf-8") as f:
        f.write(line)

    return filepath


def load_timeseries(
    timeseries_dir: str | Path = DEFAULT_TIMESERIES_DIR,
) -> list[dict]:
    """Load all records from the top-N JSONL time series file."""
    filepath = Path(timeseries_dir) / "top20.jsonl"
    if not filepath.exists():
        return []
    records = []
    for line in filepath.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


# ---------------------------------------------------------------------------
# Cache integration helpers (for GitHub Actions)
# ---------------------------------------------------------------------------

def save_latest_for_cache(
    snapshot: dict,
    cache_path: str | Path,
) -> None:
    """Save a snapshot to a specific path for caching.

    This stores the latest snapshot at a well-known path so the next
    run can load it for diffing.
    """
    cache_path = Path(cache_path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n"
    cache_path.write_text(content, encoding="utf-8")


def load_from_cache(cache_path: str | Path) -> dict | None:
    """Load a snapshot from the GitHub Actions cache path."""
    cache_path = Path(cache_path)
    if not cache_path.exists():
        return None
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _snapshot_to_timeseries_record(snapshot: dict) -> dict | None:
    """Build a lightweight timeseries record from a snapshot.

    Used to include the current snapshot's vote data in the rate
    calculation before append_top_n writes the full record to disk.
    """
    ts = snapshot.get("timestamp")
    models = snapshot.get("models", [])
    if not ts or not models:
        return None
    compact_models = []
    for m in models[:20]:
        entry: dict = {"name": m.get("model_name"), "votes": m.get("votes")}
        if entry["name"] is None:
            continue
        if m.get("score") is not None:
            entry["score"] = m["score"]
        if m.get("ci") is not None:
            entry["ci"] = m["ci"]
        compact_models.append(entry)
    if not compact_models:
        return None
    return {"ts": ts, "models": compact_models}


# ---------------------------------------------------------------------------
# Storage orchestrator
# ---------------------------------------------------------------------------

def store_snapshot(
    snapshot: dict,
    previous_snapshot: dict | None = None,
    snapshot_dir: str | Path = DEFAULT_SNAPSHOT_DIR,
    timeseries_dir: str | Path = DEFAULT_TIMESERIES_DIR,
    cache_path: str | Path | None = None,
    only_on_change: bool = True,
) -> dict:
    """Store a snapshot to all configured locations.

    Args:
        snapshot: The parsed snapshot dict.
        previous_snapshot: The previous snapshot for change detection.
        snapshot_dir: Directory for full snapshot files.
        timeseries_dir: Directory for JSONL time series.
        cache_path: Path for GitHub Actions cache.
        only_on_change: If True, only write full snapshots when data changed.

    Returns:
        A dict with storage results: snapshot_path, timeseries_path, changed.
    """
    changed = _snapshots_differ(previous_snapshot, snapshot) if previous_snapshot else True
    result: dict = {"changed": changed}

    # Enrich snapshot with overtake probabilities before storing.
    try:
        from overtake_probability import enrich_snapshot
        enrich_snapshot(snapshot)
    except Exception as exc:
        print(f"Warning: overtake probability enrichment failed: {exc}", file=sys.stderr)

    # Enrich with head-to-head win rates (top 5 vs leader).
    try:
        from overtake_probability import enrich_snapshot_with_h2h
        enrich_snapshot_with_h2h(snapshot)
    except Exception as exc:
        print(f"Warning: H2H win rate enrichment failed: {exc}", file=sys.stderr)

    # Enrich with settlement projections (weekly + monthly).
    # Include the current snapshot as a synthetic timeseries record so
    # vote rates reflect the latest data (append_top_n runs later).
    try:
        from projections import enrich_snapshot_with_projections
        from snapshot_store import load_timeseries as _load_ts
        ts = _load_ts(timeseries_dir)
        current_ts_record = _snapshot_to_timeseries_record(snapshot)
        if current_ts_record:
            ts.append(current_ts_record)
        enrich_snapshot_with_projections(snapshot, timeseries=ts)
    except Exception as exc:
        print(f"Warning: settlement projection enrichment failed: {exc}", file=sys.stderr)

    # Always update the cache (needed for next diff comparison)
    if cache_path:
        save_latest_for_cache(snapshot, cache_path)

    # Only store full snapshot if data changed (to manage repo size)
    if not only_on_change or changed:
        result["snapshot_path"] = str(save_snapshot(snapshot, snapshot_dir))
        result["timeseries_path"] = str(append_top_n(snapshot, timeseries_dir=timeseries_dir))
    else:
        result["snapshot_path"] = None
        result["timeseries_path"] = None

    return result


def _snapshots_differ(prev: dict, curr: dict) -> bool:
    """Check if two snapshots have meaningfully different model data."""
    if prev is None or curr is None:
        return True
    if prev.get("leaderboard_date") != curr.get("leaderboard_date"):
        return True
    if prev.get("total_votes") != curr.get("total_votes"):
        return True

    prev_models = {m.get("model_name"): m for m in prev.get("models", [])}
    curr_models = {m.get("model_name"): m for m in curr.get("models", [])}

    if set(prev_models.keys()) != set(curr_models.keys()):
        return True

    for name, curr_m in curr_models.items():
        prev_m = prev_models.get(name)
        if prev_m is None:
            return True
        for key in ("rank", "score", "ci", "votes", "rank_ub", "rank_lb", "is_preliminary"):
            if curr_m.get(key) != prev_m.get(key):
                return True

    return False
