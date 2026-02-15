"""Tests for the structured leaderboard HTML parser."""

import unittest

from leaderboard_parser import (
    parse_html,
    parse_leaderboard_table,
    parse_page_metadata,
    parse_rank_spread,
    _parse_score_ci,
    _parse_model_cell,
    _parse_votes,
)


class TestPageMetadata(unittest.TestCase):
    def test_extracts_date(self):
        html = '<div>Last updated: Feb 11, 2026</div>'
        meta = parse_page_metadata(html)
        self.assertEqual(meta["leaderboard_date"], "Feb 11, 2026")

    def test_extracts_total_votes(self):
        html = '<span>5,271,984 votes</span>'
        meta = parse_page_metadata(html)
        self.assertEqual(meta["total_votes"], 5271984)

    def test_extracts_total_models(self):
        html = '<span>305 models</span>'
        meta = parse_page_metadata(html)
        self.assertEqual(meta["total_models"], 305)

    def test_missing_metadata_returns_empty(self):
        html = '<div>Nothing useful here</div>'
        meta = parse_page_metadata(html)
        self.assertNotIn("leaderboard_date", meta)
        self.assertNotIn("total_votes", meta)
        self.assertNotIn("total_models", meta)


class TestScoreCIParsing(unittest.TestCase):
    def test_score_with_ci(self):
        score, ci, prelim = _parse_score_ci("1504±10")
        self.assertEqual(score, 1504)
        self.assertEqual(ci, 10)
        self.assertFalse(prelim)

    def test_score_with_ci_spaces(self):
        score, ci, prelim = _parse_score_ci("1504 ± 10")
        self.assertEqual(score, 1504)
        self.assertEqual(ci, 10)
        self.assertFalse(prelim)

    def test_preliminary_tag(self):
        score, ci, prelim = _parse_score_ci("1504±10 Preliminary")
        self.assertEqual(score, 1504)
        self.assertEqual(ci, 10)
        self.assertTrue(prelim)

    def test_bare_score(self):
        score, ci, prelim = _parse_score_ci("1504")
        self.assertEqual(score, 1504)
        self.assertIsNone(ci)
        self.assertFalse(prelim)

    def test_score_with_commas(self):
        score, ci, prelim = _parse_score_ci("1,504±10")
        self.assertEqual(score, 1504)
        self.assertEqual(ci, 10)


class TestModelCellParsing(unittest.TestCase):
    def test_model_with_link(self):
        cell = '<a href="https://example.com/model">claude-opus-4-6</a>'
        result = _parse_model_cell(cell)
        self.assertEqual(result["model_name"], "claude-opus-4-6")
        self.assertEqual(result["model_url"], "https://example.com/model")

    def test_model_with_org_and_license(self):
        cell = '<a href="https://example.com">claude-opus-4-6</a> Anthropic · Proprietary'
        result = _parse_model_cell(cell)
        self.assertEqual(result["model_name"], "claude-opus-4-6")
        self.assertEqual(result["organization"], "Anthropic")
        self.assertEqual(result["license"], "Proprietary")

    def test_model_plain_text(self):
        cell = 'gpt-4o'
        result = _parse_model_cell(cell)
        self.assertEqual(result["model_name"], "gpt-4o")

    def test_model_with_license_only(self):
        cell = '<a href="#">some-model</a> Proprietary'
        result = _parse_model_cell(cell)
        self.assertEqual(result["model_name"], "some-model")
        self.assertEqual(result["license"], "Proprietary")


class TestVotesParsing(unittest.TestCase):
    def test_plain_number(self):
        self.assertEqual(_parse_votes("3922"), 3922)

    def test_number_with_commas(self):
        self.assertEqual(_parse_votes("35,697"), 35697)

    def test_number_with_spaces(self):
        self.assertEqual(_parse_votes(" 3922 "), 3922)

    def test_empty_returns_none(self):
        self.assertIsNone(_parse_votes(""))


class TestLeaderboardTableParsing(unittest.TestCase):
    def test_basic_table(self):
        html = """
        <div>Feb 11, 2026 · 5,271,984 votes · 305 models</div>
        <table>
            <tr>
                <th>Rank</th>
                <th>Rank Spread</th>
                <th>Model</th>
                <th>Score</th>
                <th>Votes</th>
            </tr>
            <tr>
                <td>1</td>
                <td>12</td>
                <td><a href="https://anthropic.com">claude-opus-4-6-thinking</a> Anthropic · Proprietary</td>
                <td>1504±10</td>
                <td>3,922</td>
            </tr>
            <tr>
                <td>2</td>
                <td>13</td>
                <td><a href="https://openai.com">gpt-4.5</a> OpenAI · Proprietary</td>
                <td>1490±8</td>
                <td>5,100</td>
            </tr>
            <tr>
                <td>3</td>
                <td>36</td>
                <td><a href="https://deepseek.com">deepseek-r2</a> DeepSeek · Open</td>
                <td>1485±12 Preliminary</td>
                <td>1,200</td>
            </tr>
        </table>
        """
        models = parse_leaderboard_table(html)
        self.assertEqual(len(models), 3)

        m1 = models[0]
        self.assertEqual(m1["rank"], 1)
        self.assertEqual(m1["model_name"], "claude-opus-4-6-thinking")
        self.assertEqual(m1["organization"], "Anthropic")
        self.assertEqual(m1["license"], "Proprietary")
        self.assertEqual(m1["score"], 1504)
        self.assertEqual(m1["ci"], 10)
        self.assertEqual(m1["votes"], 3922)
        self.assertEqual(m1["rank_ub"], 1)
        self.assertEqual(m1["rank_lb"], 2)
        self.assertFalse(m1["is_preliminary"])

        m3 = models[2]
        self.assertEqual(m3["rank"], 3)
        self.assertTrue(m3["is_preliminary"])
        self.assertEqual(m3["rank_ub"], 3)
        self.assertEqual(m3["rank_lb"], 6)

    def test_selects_largest_matching_table(self):
        html = """
        <table>
            <tr><th>Rank</th><th>Model</th><th>Score</th></tr>
            <tr><td>1</td><td>small-model</td><td>100</td></tr>
        </table>
        <table>
            <tr><th>Rank</th><th>Model</th><th>Score</th></tr>
            <tr><td>1</td><td>model-a</td><td>1500</td></tr>
            <tr><td>2</td><td>model-b</td><td>1490</td></tr>
            <tr><td>3</td><td>model-c</td><td>1480</td></tr>
        </table>
        """
        models = parse_leaderboard_table(html)
        self.assertEqual(len(models), 3)
        self.assertEqual(models[0]["model_name"], "model-a")

    def test_ignores_numeric_only_model_names(self):
        html = """
        <table>
            <tr><th>Rank</th><th>Model</th><th>Score</th></tr>
            <tr><td>1</td><td>12</td><td>100</td></tr>
            <tr><td>2</td><td>45</td><td>90</td></tr>
        </table>
        """
        models = parse_leaderboard_table(html)
        self.assertEqual(len(models), 0)


class TestParseHtml(unittest.TestCase):
    def test_full_parse(self):
        html = """
        <html>
        <body>
        <div>Feb 11, 2026 · 5,271,984 votes · 305 models</div>
        <table>
            <tr><th>Rank</th><th>Rank Spread</th><th>Model</th><th>Score</th><th>Votes</th></tr>
            <tr><td>1</td><td>12</td><td>claude-opus-4-6</td><td>1504±10</td><td>3,922</td></tr>
        </table>
        </body>
        </html>
        """
        result = parse_html(html)
        self.assertEqual(result["leaderboard_date"], "Feb 11, 2026")
        self.assertEqual(result["total_votes"], 5271984)
        self.assertIn("timestamp", result)
        self.assertEqual(len(result["models"]), 1)
        self.assertEqual(result["models"][0]["model_name"], "claude-opus-4-6")


if __name__ == "__main__":
    unittest.main()
