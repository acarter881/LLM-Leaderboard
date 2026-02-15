"""Tests for snapshot storage and time series persistence."""

import gzip
import json
import tempfile
import unittest
from pathlib import Path

from snapshot_store import (
    save_snapshot,
    load_snapshot,
    list_snapshots,
    load_latest_snapshot,
    append_top_n,
    load_timeseries,
    save_latest_for_cache,
    load_from_cache,
    _snapshots_differ,
)


def _sample_snapshot(n_models=3):
    models = []
    for i in range(1, n_models + 1):
        models.append({
            "rank": i,
            "model_name": f"model-{i}",
            "score": 1500 - i * 10,
            "ci": 10,
            "votes": 1000 * i,
            "rank_ub": i,
            "rank_lb": i + 2,
        })
    return {
        "timestamp": "2026-02-15T14:30:00Z",
        "leaderboard_date": "Feb 11, 2026",
        "total_models": n_models,
        "total_votes": 5000000,
        "models": models,
    }


class TestSnapshotSaveLoad(unittest.TestCase):
    def test_save_and_load_compressed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            snapshot = _sample_snapshot()
            path = save_snapshot(snapshot, snapshot_dir=tmpdir, compress=True)
            self.assertTrue(path.name.endswith(".json.gz"))
            loaded = load_snapshot(path)
            self.assertEqual(loaded["total_models"], 3)
            self.assertEqual(len(loaded["models"]), 3)

    def test_save_and_load_uncompressed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            snapshot = _sample_snapshot()
            path = save_snapshot(snapshot, snapshot_dir=tmpdir, compress=False)
            self.assertTrue(path.name.endswith(".json"))
            loaded = load_snapshot(path)
            self.assertEqual(loaded["total_models"], 3)

    def test_list_snapshots_sorted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            snap1 = _sample_snapshot()
            snap1["timestamp"] = "2026-02-15T10:00:00Z"
            save_snapshot(snap1, snapshot_dir=tmpdir)

            snap2 = _sample_snapshot()
            snap2["timestamp"] = "2026-02-15T14:00:00Z"
            save_snapshot(snap2, snapshot_dir=tmpdir)

            files = list_snapshots(tmpdir)
            self.assertEqual(len(files), 2)
            # Earlier file comes first
            self.assertLess(files[0].name, files[1].name)

    def test_load_latest_snapshot(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            snap1 = _sample_snapshot()
            snap1["timestamp"] = "2026-02-15T10:00:00Z"
            snap1["total_votes"] = 1000
            save_snapshot(snap1, snapshot_dir=tmpdir)

            snap2 = _sample_snapshot()
            snap2["timestamp"] = "2026-02-15T14:00:00Z"
            snap2["total_votes"] = 2000
            save_snapshot(snap2, snapshot_dir=tmpdir)

            latest = load_latest_snapshot(tmpdir)
            self.assertEqual(latest["total_votes"], 2000)

    def test_load_nonexistent_returns_none(self):
        self.assertIsNone(load_snapshot("/nonexistent/path.json"))

    def test_list_snapshots_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertEqual(list_snapshots(tmpdir), [])


class TestTopNTimeSeries(unittest.TestCase):
    def test_append_and_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            snapshot = _sample_snapshot()
            path = append_top_n(snapshot, top_n=2, timeseries_dir=tmpdir)
            self.assertTrue(path.name.endswith(".jsonl"))

            records = load_timeseries(tmpdir)
            self.assertEqual(len(records), 1)
            self.assertEqual(len(records[0]["models"]), 2)
            self.assertEqual(records[0]["models"][0]["name"], "model-1")

    def test_append_is_additive(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            snap1 = _sample_snapshot()
            snap1["timestamp"] = "2026-02-15T10:00:00Z"
            append_top_n(snap1, timeseries_dir=tmpdir)

            snap2 = _sample_snapshot()
            snap2["timestamp"] = "2026-02-15T14:00:00Z"
            append_top_n(snap2, timeseries_dir=tmpdir)

            records = load_timeseries(tmpdir)
            self.assertEqual(len(records), 2)

    def test_load_empty_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertEqual(load_timeseries(tmpdir), [])


class TestCacheHelpers(unittest.TestCase):
    def test_save_and_load_cache(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "cache" / "snapshot.json"
            snapshot = _sample_snapshot()
            save_latest_for_cache(snapshot, cache_path)
            loaded = load_from_cache(cache_path)
            self.assertEqual(loaded["total_models"], 3)

    def test_load_missing_cache_returns_none(self):
        self.assertIsNone(load_from_cache("/nonexistent/cache.json"))


class TestSnapshotsDiffer(unittest.TestCase):
    def test_identical_snapshots(self):
        snap = _sample_snapshot()
        self.assertFalse(_snapshots_differ(snap, snap))

    def test_different_dates(self):
        snap1 = _sample_snapshot()
        snap2 = _sample_snapshot()
        snap2["leaderboard_date"] = "Feb 12, 2026"
        self.assertTrue(_snapshots_differ(snap1, snap2))

    def test_different_votes(self):
        snap1 = _sample_snapshot()
        snap2 = _sample_snapshot()
        snap2["total_votes"] = 6000000
        self.assertTrue(_snapshots_differ(snap1, snap2))

    def test_different_model_score(self):
        snap1 = _sample_snapshot()
        snap2 = _sample_snapshot()
        snap2["models"][0]["score"] = 1600
        self.assertTrue(_snapshots_differ(snap1, snap2))

    def test_new_model_added(self):
        snap1 = _sample_snapshot(n_models=2)
        snap2 = _sample_snapshot(n_models=3)
        self.assertTrue(_snapshots_differ(snap1, snap2))

    def test_none_previous(self):
        self.assertTrue(_snapshots_differ(None, _sample_snapshot()))


if __name__ == "__main__":
    unittest.main()
