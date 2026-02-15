"""Tests for the snapshot diff engine and Discord formatting."""

import unittest

from snapshot_diff import compute_diff, has_changes, format_discord_message, format_diff_summary


def _make_snapshot(models, date="Feb 11, 2026"):
    return {
        "timestamp": "2026-02-15T14:30:00Z",
        "leaderboard_date": date,
        "total_models": len(models),
        "models": models,
    }


def _model(name, rank, score=1400, ci=10, votes=1000, rank_ub=None, is_preliminary=False):
    m = {
        "model_name": name,
        "rank": rank,
        "score": score,
        "ci": ci,
        "votes": votes,
        "is_preliminary": is_preliminary,
    }
    if rank_ub is not None:
        m["rank_ub"] = rank_ub
    return m


class TestComputeDiff(unittest.TestCase):
    def test_no_changes(self):
        models = [_model("model-a", 1)]
        diff = compute_diff(_make_snapshot(models), _make_snapshot(models))
        self.assertFalse(has_changes(diff))

    def test_new_model_detected(self):
        prev = _make_snapshot([_model("model-a", 1)])
        curr = _make_snapshot([_model("model-a", 1), _model("model-b", 2)])
        diff = compute_diff(prev, curr)
        self.assertTrue(has_changes(diff))
        self.assertEqual(len(diff["new_models"]), 1)
        self.assertEqual(diff["new_models"][0]["model_name"], "model-b")

    def test_removed_model_detected(self):
        prev = _make_snapshot([_model("model-a", 1), _model("model-b", 2)])
        curr = _make_snapshot([_model("model-a", 1)])
        diff = compute_diff(prev, curr)
        self.assertTrue(has_changes(diff))
        self.assertEqual(len(diff["removed_models"]), 1)
        self.assertEqual(diff["removed_models"][0]["model_name"], "model-b")

    def test_rank_change_detected(self):
        prev = _make_snapshot([_model("model-a", 1), _model("model-b", 2)])
        curr = _make_snapshot([_model("model-a", 2), _model("model-b", 1)])
        diff = compute_diff(prev, curr)
        self.assertTrue(has_changes(diff))
        self.assertEqual(len(diff["rank_changes"]), 2)

    def test_rank_ub_change_detected(self):
        prev = _make_snapshot([_model("model-a", 1, rank_ub=1)])
        curr = _make_snapshot([_model("model-a", 1, rank_ub=2)])
        diff = compute_diff(prev, curr)
        self.assertTrue(has_changes(diff))
        self.assertEqual(len(diff["rank_ub_changes"]), 1)
        self.assertEqual(diff["rank_ub_changes"][0]["previous_rank_ub"], 1)
        self.assertEqual(diff["rank_ub_changes"][0]["current_rank_ub"], 2)

    def test_score_change_detected(self):
        prev = _make_snapshot([_model("model-a", 1, score=1500)])
        curr = _make_snapshot([_model("model-a", 1, score=1510)])
        diff = compute_diff(prev, curr)
        self.assertTrue(has_changes(diff))
        self.assertEqual(diff["score_changes"][0]["delta"], 10)

    def test_ci_change_detected(self):
        prev = _make_snapshot([_model("model-a", 1, ci=10)])
        curr = _make_snapshot([_model("model-a", 1, ci=5)])
        diff = compute_diff(prev, curr)
        self.assertTrue(has_changes(diff))
        self.assertEqual(diff["ci_changes"][0]["delta"], -5)

    def test_vote_change_detected(self):
        prev = _make_snapshot([_model("model-a", 1, votes=1000)])
        curr = _make_snapshot([_model("model-a", 1, votes=1500)])
        diff = compute_diff(prev, curr)
        self.assertTrue(has_changes(diff))
        self.assertEqual(diff["vote_changes"][0]["delta"], 500)

    def test_preliminary_change_detected(self):
        prev = _make_snapshot([_model("model-a", 1, is_preliminary=True)])
        curr = _make_snapshot([_model("model-a", 1, is_preliminary=False)])
        diff = compute_diff(prev, curr)
        self.assertTrue(has_changes(diff))
        self.assertEqual(len(diff["preliminary_changes"]), 1)

    def test_leaderboard_date_change(self):
        prev = _make_snapshot([_model("model-a", 1)], date="Feb 10, 2026")
        curr = _make_snapshot([_model("model-a", 1)], date="Feb 11, 2026")
        diff = compute_diff(prev, curr)
        self.assertTrue(has_changes(diff))
        self.assertTrue(diff["leaderboard_date_changed"])


class TestFormatDiscordMessage(unittest.TestCase):
    def test_message_contains_rank_ub_warning(self):
        prev = _make_snapshot([_model("model-a", 1, rank_ub=1)])
        curr = _make_snapshot([_model("model-a", 1, rank_ub=2)])
        diff = compute_diff(prev, curr)
        msg = format_discord_message(diff, "https://example.com")
        self.assertIn("RANK UB CHANGES", msg)
        self.assertIn("Settlement-Critical", msg)
        self.assertIn("model-a", msg)

    def test_message_includes_new_models(self):
        prev = _make_snapshot([])
        curr = _make_snapshot([_model("new-model", 1, score=1500)])
        diff = compute_diff(prev, curr)
        msg = format_discord_message(diff, "https://example.com")
        self.assertIn("New Models", msg)
        self.assertIn("new-model", msg)

    def test_message_includes_url(self):
        diff = compute_diff(_make_snapshot([]), _make_snapshot([]))
        msg = format_discord_message(diff, "https://arena.ai/leaderboard")
        self.assertIn("https://arena.ai/leaderboard", msg)


class TestFormatDiffSummary(unittest.TestCase):
    def test_summary_with_changes(self):
        prev = _make_snapshot([_model("model-a", 1, score=1500)])
        curr = _make_snapshot([_model("model-a", 2, score=1490), _model("model-b", 1, score=1510)])
        diff = compute_diff(prev, curr)
        summary = format_diff_summary(diff)
        self.assertIn("new", summary)
        self.assertIn("rank", summary)
        self.assertIn("score", summary)

    def test_summary_no_changes(self):
        diff = compute_diff(_make_snapshot([_model("a", 1)]), _make_snapshot([_model("a", 1)]))
        self.assertEqual(format_diff_summary(diff), "no changes")


if __name__ == "__main__":
    unittest.main()
