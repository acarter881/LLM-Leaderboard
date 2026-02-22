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
        self.assertEqual(data["models"], {})

    def test_extracts_model_scores(self):
        records = [
            _make_record("2026-02-18T12:00:00Z", [
                {"rank": 1, "name": "Alpha", "score": 1500, "ci": 8, "votes": 5000},
                {"rank": 2, "name": "Beta", "score": 1490, "ci": 10, "votes": 4000},
            ]),
            _make_record("2026-02-19T12:00:00Z", [
                {"rank": 1, "name": "Alpha", "score": 1502, "ci": 7, "votes": 5500},
                {"rank": 2, "name": "Beta", "score": 1492, "ci": 9, "votes": 4400},
            ]),
        ]
        data = extract_chart_data(records, top_n=5)
        self.assertEqual(len(data["timestamps"]), 2)
        self.assertIn("Alpha", data["models"])
        self.assertIn("Beta", data["models"])
        self.assertEqual(data["models"]["Alpha"][0]["score"], 1500)
        self.assertEqual(data["models"]["Alpha"][1]["score"], 1502)

    def test_model_absent_in_some_records(self):
        records = [
            _make_record("2026-02-18T12:00:00Z", [
                {"rank": 1, "name": "Alpha", "score": 1500, "ci": 8, "votes": 5000},
            ]),
            _make_record("2026-02-19T12:00:00Z", [
                {"rank": 1, "name": "Alpha", "score": 1502, "ci": 7, "votes": 5500},
                {"rank": 2, "name": "Beta", "score": 1492, "ci": 9, "votes": 4400},
            ]),
        ]
        data = extract_chart_data(records, top_n=5)
        # Beta should be None for the first record.
        self.assertIsNone(data["models"]["Beta"][0])
        self.assertIsNotNone(data["models"]["Beta"][1])

    def test_extracts_overtake_data(self):
        records = [
            _make_record(
                "2026-02-18T12:00:00Z",
                [{"rank": 1, "name": "Alpha", "score": 1500}],
                overtake=[{"name": "Beta", "prob": 0.35, "gap": 10}],
                leader_prob=0.65,
            ),
        ]
        data = extract_chart_data(records, top_n=5)
        self.assertEqual(data["overtake"]["Beta"][0], 0.35)
        self.assertEqual(data["leader_prob"][0], 0.65)

    def test_extracts_h2h_data(self):
        records = [
            _make_record(
                "2026-02-18T12:00:00Z",
                [{"rank": 1, "name": "Alpha", "score": 1500}],
                h2h=[{"name": "Beta", "wr": 0.48, "gap": 10}],
            ),
        ]
        data = extract_chart_data(records, top_n=5)
        self.assertEqual(data["h2h"]["Beta"][0], 0.48)

    def test_top_n_limits_models(self):
        models = [{"rank": i, "name": f"model-{i}", "score": 1500 - i} for i in range(1, 25)]
        records = [_make_record("2026-02-18T12:00:00Z", models)]
        data = extract_chart_data(records, top_n=5)
        # Only the top 5 models should be included.
        self.assertEqual(len(data["models"]), 5)


class TestGenerateDashboard(unittest.TestCase):
    def test_generates_html_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a minimal timeseries file.
            ts_dir = Path(tmpdir) / "timeseries"
            ts_dir.mkdir()
            record = _make_record(
                "2026-02-18T12:00:00Z",
                [{"rank": 1, "name": "Alpha", "score": 1500, "ci": 8, "votes": 5000}],
            )
            (ts_dir / "top20.jsonl").write_text(json.dumps(record) + "\n")

            output = Path(tmpdir) / "test.html"
            result = generate_dashboard(timeseries_dir=ts_dir, output_path=output)
            self.assertEqual(result, output)
            self.assertTrue(output.exists())
            content = output.read_text()
            self.assertIn("Arena Leaderboard Dashboard", content)
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
