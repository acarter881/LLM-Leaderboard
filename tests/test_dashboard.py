"""Tests for the dashboard generator."""

import json
import tempfile
import unittest
from pathlib import Path

from dashboard import extract_chart_data, generate_dashboard


def _make_record(ts, models, overtake=None, h2h=None, leader_prob=None):
    record = {"ts": ts, "date": "Feb 18, 2026", "models": models}
    if overtake:
        record["overtake_top5"] = overtake
    if h2h:
        record["h2h_top5"] = h2h
    if leader_prob is not None:
        record["leader_prob_staying_1"] = leader_prob
    return record


class TestExtractChartData(unittest.TestCase):
    def test_empty_timeseries(self):
        data = extract_chart_data([])
        self.assertEqual(data["timestamps"], [])
        self.assertIsNone(data["leader"])
        self.assertEqual(data["contenders"], [])

    def test_identifies_leader(self):
        records = [
            _make_record("2026-02-18T12:00:00Z", [
                {"rank": 1, "name": "Alpha", "score": 1500, "ci": 8, "votes": 5000},
                {"rank": 2, "name": "Beta", "score": 1490, "ci": 10, "votes": 4000},
            ]),
        ]
        data = extract_chart_data(records)
        self.assertEqual(data["leader"], "Alpha")

    def test_contenders_from_overtake(self):
        """Models with overtake prob > 1% become contenders."""
        records = [
            _make_record(
                "2026-02-18T12:00:00Z",
                [
                    {"rank": 1, "name": "Alpha", "score": 1500, "ci": 8, "votes": 5000},
                    {"rank": 2, "name": "Beta", "score": 1490, "ci": 10, "votes": 4000},
                    {"rank": 3, "name": "Gamma", "score": 1480, "ci": 9, "votes": 3000},
                ],
                overtake=[
                    {"name": "Beta", "prob": 0.35, "gap": 10},
                    {"name": "Gamma", "prob": 0.001, "gap": 20},
                ],
            ),
        ]
        data = extract_chart_data(records)
        # Beta is a contender (35%); Gamma is not (0.1%).
        self.assertIn("Beta", data["contenders"])
        self.assertNotIn("Gamma", data["contenders"])
        # Leader is never a contender.
        self.assertNotIn("Alpha", data["contenders"])

    def test_fallback_contenders_without_overtake_data(self):
        """Without overtake data, #2 and #3 become contenders."""
        records = [
            _make_record("2026-02-18T12:00:00Z", [
                {"rank": 1, "name": "Alpha", "score": 1500, "ci": 8, "votes": 5000},
                {"rank": 2, "name": "Beta", "score": 1490, "ci": 10, "votes": 4000},
                {"rank": 3, "name": "Gamma", "score": 1480, "ci": 9, "votes": 3000},
                {"rank": 4, "name": "Delta", "score": 1470, "ci": 7, "votes": 2000},
            ]),
        ]
        data = extract_chart_data(records)
        self.assertEqual(data["contenders"], ["Beta", "Gamma"])

    def test_score_gap_computation(self):
        records = [
            _make_record("2026-02-18T12:00:00Z", [
                {"rank": 1, "name": "Alpha", "score": 1500, "ci": 8, "votes": 5000},
                {"rank": 2, "name": "Beta", "score": 1490, "ci": 10, "votes": 4000},
            ]),
            _make_record("2026-02-19T12:00:00Z", [
                {"rank": 1, "name": "Alpha", "score": 1502, "ci": 7, "votes": 5500},
                {"rank": 2, "name": "Beta", "score": 1498, "ci": 9, "votes": 4400},
            ]),
        ]
        data = extract_chart_data(records)
        # Gap = leader - contender: 10, then 4 (Beta catching up).
        self.assertEqual(data["score_gap"]["Beta"], [10, 4])

    def test_votes_includes_leader(self):
        records = [
            _make_record("2026-02-18T12:00:00Z", [
                {"rank": 1, "name": "Alpha", "score": 1500, "ci": 8, "votes": 5000},
                {"rank": 2, "name": "Beta", "score": 1490, "ci": 10, "votes": 4000},
            ]),
        ]
        data = extract_chart_data(records)
        # Both leader and contender should have vote data.
        self.assertEqual(data["votes"]["Alpha"], [5000])
        self.assertEqual(data["votes"]["Beta"], [4000])

    def test_ci_includes_leader(self):
        records = [
            _make_record("2026-02-18T12:00:00Z", [
                {"rank": 1, "name": "Alpha", "score": 1500, "ci": 8, "votes": 5000},
                {"rank": 2, "name": "Beta", "score": 1490, "ci": 10, "votes": 4000},
            ]),
        ]
        data = extract_chart_data(records)
        self.assertEqual(data["ci"]["Alpha"], [8])
        self.assertEqual(data["ci"]["Beta"], [10])

    def test_overtake_data(self):
        records = [
            _make_record(
                "2026-02-18T12:00:00Z",
                [
                    {"rank": 1, "name": "Alpha", "score": 1500, "ci": 8, "votes": 5000},
                    {"rank": 2, "name": "Beta", "score": 1490, "ci": 10, "votes": 4000},
                ],
                overtake=[{"name": "Beta", "prob": 0.35, "gap": 10}],
                leader_prob=0.65,
            ),
        ]
        data = extract_chart_data(records)
        self.assertEqual(data["overtake"]["Beta"], [0.35])
        self.assertEqual(data["leader_prob"], [0.65])

    def test_h2h_data(self):
        records = [
            _make_record(
                "2026-02-18T12:00:00Z",
                [
                    {"rank": 1, "name": "Alpha", "score": 1500, "ci": 8, "votes": 5000},
                    {"rank": 2, "name": "Beta", "score": 1490, "ci": 10, "votes": 4000},
                ],
                h2h=[{"name": "Beta", "wr": 0.48, "gap": 10}],
            ),
        ]
        data = extract_chart_data(records)
        self.assertEqual(data["h2h"]["Beta"], [0.48])

    def test_contender_absent_in_earlier_record(self):
        """Score gap is None when contender is absent from a snapshot."""
        records = [
            _make_record("2026-02-18T12:00:00Z", [
                {"rank": 1, "name": "Alpha", "score": 1500, "ci": 8, "votes": 5000},
            ]),
            _make_record("2026-02-19T12:00:00Z", [
                {"rank": 1, "name": "Alpha", "score": 1502, "ci": 7, "votes": 5500},
                {"rank": 2, "name": "Beta", "score": 1492, "ci": 9, "votes": 4400},
            ]),
        ]
        data = extract_chart_data(records)
        # Beta wasn't in the first snapshot.
        self.assertIsNone(data["score_gap"]["Beta"][0])
        self.assertEqual(data["score_gap"]["Beta"][1], 10)

    def test_contenders_ordered_by_rank(self):
        records = [
            _make_record(
                "2026-02-18T12:00:00Z",
                [
                    {"rank": 1, "name": "Alpha", "score": 1500, "ci": 8, "votes": 5000},
                    {"rank": 2, "name": "Beta", "score": 1490, "ci": 10, "votes": 4000},
                    {"rank": 5, "name": "Epsilon", "score": 1460, "ci": 9, "votes": 3000},
                ],
                overtake=[
                    {"name": "Epsilon", "prob": 0.10, "gap": 40},
                    {"name": "Beta", "prob": 0.35, "gap": 10},
                ],
            ),
        ]
        data = extract_chart_data(records)
        # Beta (rank 2) should come before Epsilon (rank 5).
        self.assertEqual(data["contenders"], ["Beta", "Epsilon"])


class TestGenerateDashboard(unittest.TestCase):
    def test_generates_html_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ts_dir = Path(tmpdir) / "timeseries"
            ts_dir.mkdir()
            record = _make_record(
                "2026-02-18T12:00:00Z",
                [
                    {"rank": 1, "name": "Alpha", "score": 1500, "ci": 8, "votes": 5000},
                    {"rank": 2, "name": "Beta", "score": 1490, "ci": 10, "votes": 4000},
                ],
            )
            (ts_dir / "top20.jsonl").write_text(json.dumps(record) + "\n")

            output = Path(tmpdir) / "test.html"
            result = generate_dashboard(timeseries_dir=ts_dir, output_path=output)
            self.assertEqual(result, output)
            self.assertTrue(output.exists())
            content = output.read_text()
            self.assertIn("Kalshi Settlement Dashboard", content)
            self.assertIn("Plotly", content)
            self.assertIn("Alpha", content)

    def test_empty_timeseries_still_generates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ts_dir = Path(tmpdir) / "timeseries"
            ts_dir.mkdir()
            (ts_dir / "top20.jsonl").write_text("")

            output = Path(tmpdir) / "test.html"
            result = generate_dashboard(timeseries_dir=ts_dir, output_path=output)
            self.assertTrue(output.exists())


if __name__ == "__main__":
    unittest.main()
