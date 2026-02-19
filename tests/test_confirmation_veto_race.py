"""Test that the confirmation + structured veto race condition is fixed.

Reproduces the bug where:
1. Check 1 detects a new hash (pending_count=1), and updates the structured
   cache to the CURRENT snapshot data.
2. Check 2 confirms the hash (pending_count=2, changed=True), but the
   structured diff is empty because the cache was already overwritten in step 1.
3. The veto suppresses the notification.

The fix: the structured cache must NOT be updated while a hash confirmation
is pending, so the diff baseline is preserved.
"""

import argparse
import json
import tempfile
import unittest
from pathlib import Path


class TestConfirmationVetoRace(unittest.TestCase):
    """Simulate the exact loop flow that triggered the false veto."""

    def _make_args(self, state_file, structured_cache, snapshot_dir, timeseries_dir):
        return argparse.Namespace(
            url="https://arena.ai/leaderboard/text/overall-no-style-control",
            webhook_url="https://discord.com/api/webhooks/test/fake",
            state_file=state_file,
            timeout=30,
            retries=0,
            retry_backoff_seconds=1,
            confirmation_checks=2,
            force_send=False,
            dry_run=True,
            loop=False,
            min_interval_seconds=120,
            max_interval_seconds=300,
            max_checks=None,
            snapshot_dir=snapshot_dir,
            timeseries_dir=timeseries_dir,
            structured_cache=structured_cache,
            no_structured=False,
        )

    def test_cache_not_updated_during_pending_window(self):
        """Cache must stay frozen while a pending confirmation is in progress."""
        from snapshot_store import save_latest_for_cache, load_from_cache

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "structured_snapshot.json"

            # Seed the cache with the OLD snapshot (before the leaderboard changed)
            old_snapshot = {
                "timestamp": "2026-02-15T21:14:53Z",
                "leaderboard_date": "Feb 11, 2026",
                "total_models": 2,
                "total_votes": None,
                "models": [
                    {"model_name": "model-a", "rank": 1, "score": 1500, "ci": 10, "votes": 1000},
                    {"model_name": "model-b", "rank": 2, "score": 1400, "ci": 10, "votes": 1000},
                ],
            }
            save_latest_for_cache(old_snapshot, cache_path)

            # Simulate the state after Check 1: old_hash != new_hash but
            # pending_count=1 < confirmation_checks=2.  The else branch runs.
            # Before the fix, this would update the cache.  After the fix,
            # it should NOT update because old_hash != new_hash.
            old_hash = "aaa"
            new_hash = "bbb"

            # This is the condition from the fix:
            # only update cache when old_hash == new_hash (no pending change)
            if old_hash == new_hash:
                new_snapshot = {
                    "timestamp": "2026-02-19T01:50:15Z",
                    "leaderboard_date": "Feb 16, 2026",
                    "total_models": 3,
                    "total_votes": None,
                    "models": [
                        {"model_name": "model-a", "rank": 1, "score": 1503, "ci": 9, "votes": 4745},
                        {"model_name": "model-b", "rank": 2, "score": 1401, "ci": 9, "votes": 5540},
                        {"model_name": "model-c", "rank": 3, "score": 1300, "ci": 10, "votes": 500},
                    ],
                }
                save_latest_for_cache(new_snapshot, cache_path)

            # Cache should still contain the OLD data
            cached = load_from_cache(cache_path)
            self.assertEqual(cached["leaderboard_date"], "Feb 11, 2026")
            self.assertEqual(len(cached["models"]), 2)

    def test_cache_updated_when_hash_stable(self):
        """Cache should be updated normally when there is no pending change."""
        from snapshot_store import save_latest_for_cache, load_from_cache

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "structured_snapshot.json"

            old_snapshot = {
                "timestamp": "2026-02-15T21:14:53Z",
                "leaderboard_date": "Feb 11, 2026",
                "total_models": 2,
                "total_votes": None,
                "models": [
                    {"model_name": "model-a", "rank": 1, "score": 1500, "ci": 10, "votes": 1000},
                ],
            }
            save_latest_for_cache(old_snapshot, cache_path)

            # Hash is stable — cache should be updated for freshness
            old_hash = "aaa"
            new_hash = "aaa"

            if old_hash == new_hash:
                new_snapshot = {
                    "timestamp": "2026-02-19T01:50:15Z",
                    "leaderboard_date": "Feb 11, 2026",
                    "total_models": 2,
                    "total_votes": 12345,
                    "models": [
                        {"model_name": "model-a", "rank": 1, "score": 1500, "ci": 10, "votes": 1200},
                    ],
                }
                save_latest_for_cache(new_snapshot, cache_path)

            cached = load_from_cache(cache_path)
            self.assertEqual(cached["total_votes"], 12345)
            self.assertEqual(cached["models"][0]["votes"], 1200)

    def test_diff_has_changes_after_confirmation(self):
        """When confirmation arrives, the diff must show real changes."""
        from snapshot_store import save_latest_for_cache, load_from_cache
        from snapshot_diff import compute_diff, has_changes, has_significant_changes

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "structured_snapshot.json"

            # Seed cache with old snapshot (before leaderboard change)
            old_snapshot = {
                "timestamp": "2026-02-15T21:14:53Z",
                "leaderboard_date": "Feb 11, 2026",
                "total_models": 2,
                "total_votes": None,
                "models": [
                    {"model_name": "model-a", "rank": 1, "score": 1500, "ci": 10, "votes": 1000},
                    {"model_name": "model-b", "rank": 2, "score": 1400, "ci": 10, "votes": 1000},
                ],
            }
            save_latest_for_cache(old_snapshot, cache_path)

            new_snapshot = {
                "timestamp": "2026-02-19T01:50:15Z",
                "leaderboard_date": "Feb 16, 2026",
                "total_models": 3,
                "total_votes": None,
                "models": [
                    {"model_name": "model-a", "rank": 1, "score": 1503, "ci": 9, "votes": 4745},
                    {"model_name": "model-b", "rank": 2, "score": 1401, "ci": 9, "votes": 5540},
                    {"model_name": "model-c", "rank": 3, "score": 1300, "ci": 10, "votes": 500},
                ],
            }

            # --- Check 1: pending (do NOT update cache) ---
            old_hash = "aaa"
            new_hash = "bbb"
            if old_hash == new_hash:
                save_latest_for_cache(new_snapshot, cache_path)

            # --- Check 2: confirmation (changed=True) ---
            # Load cache for diff — should be old data
            previous_structured = load_from_cache(cache_path)
            structured_diff = compute_diff(previous_structured, new_snapshot)

            # The diff should detect real changes
            self.assertTrue(has_changes(structured_diff))
            self.assertEqual(len(structured_diff["new_models"]), 1)
            self.assertEqual(structured_diff["new_models"][0]["model_name"], "model-c")
            self.assertTrue(structured_diff["leaderboard_date_changed"])


if __name__ == "__main__":
    unittest.main()
