import unittest

from leaderboard_notifier import build_message, diff_snapshots, parse_leaderboard_snapshot


class SnapshotParsingTests(unittest.TestCase):
    def test_ignores_numeric_only_model_names(self):
        html = """
        <table>
          <tr><th>Rank</th><th>Model</th><th>Score</th></tr>
          <tr><td>1</td><td>12</td><td>-3</td></tr>
          <tr><td>2</td><td>45</td><td>-2.5</td></tr>
        </table>
        """
        self.assertEqual(parse_leaderboard_snapshot(html), [])

    def test_prefers_leaderboard_like_table(self):
        html = """
        <table id="misc">
          <tr><th>Rank</th><th>Model</th><th>Score</th></tr>
          <tr><td>1</td><td>12</td><td>-3</td></tr>
          <tr><td>2</td><td>45</td><td>-2.5</td></tr>
        </table>

        <table id="leaderboard">
          <tr><th>Rank</th><th>Model</th><th>Score</th></tr>
          <tr><td>1</td><td>GPT-5</td><td>95.0</td></tr>
          <tr><td>2</td><td>Claude 4</td><td>94.1</td></tr>
        </table>
        """
        snapshot = parse_leaderboard_snapshot(html)
        self.assertEqual(
            snapshot,
            [
                {"rank": 1, "model": "GPT-5", "score": 95.0},
                {"rank": 2, "model": "Claude 4", "score": 94.1},
            ],
        )

    def test_diff_reports_drop_from_previous_window_when_current_empty(self):
        previous = [
            {"rank": 1, "model": "GPT-5"},
            {"rank": 2, "model": "Claude 4"},
        ]
        current = []
        diff = diff_snapshots(previous, current)
        self.assertEqual(
            diff["rank_movements"],
            [
                "↘ GPT-5: dropped from top 2",
                "↘ Claude 4: dropped from top 2",
            ],
        )

    def test_message_uses_non_zero_snapshot_window_when_current_empty(self):
        previous = [{"rank": 1, "model": "GPT-5"}]
        message = build_message(
            "https://arena.ai/leaderboard/text/overall-no-style-control",
            "abc123",
            "def456",
            previous_snapshot=previous,
            current_snapshot=[],
        )
        self.assertIn("Top 1 snapshot changes:", message)
        self.assertIn("dropped from top 1", message)


if __name__ == "__main__":
    unittest.main()
