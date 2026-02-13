import unittest

from leaderboard_notifier import parse_leaderboard_snapshot


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


if __name__ == "__main__":
    unittest.main()
