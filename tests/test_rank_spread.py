"""Tests for rank spread parsing logic."""

import unittest

from leaderboard_parser import parse_rank_spread


class TestRankSpreadParser(unittest.TestCase):
    """Test the core rank spread heuristic using all examples from the spec."""

    def test_single_digit_ranks_12(self):
        self.assertEqual(parse_rank_spread("12", model_rank=1), (1, 2))

    def test_same_rank_tight_ci_33(self):
        self.assertEqual(parse_rank_spread("33", model_rank=3), (3, 3))

    def test_single_digit_ranks_45(self):
        self.assertEqual(parse_rank_spread("45", model_rank=4), (4, 5))

    def test_single_ub_two_digit_lb_615(self):
        self.assertEqual(parse_rank_spread("615", model_rank=6), (6, 15))

    def test_single_ub_two_digit_lb_616(self):
        self.assertEqual(parse_rank_spread("616", model_rank=6), (6, 16))

    def test_single_ub_two_digit_lb_617(self):
        self.assertEqual(parse_rank_spread("617", model_rank=6), (6, 17))

    def test_single_ub_two_digit_lb_620(self):
        self.assertEqual(parse_rank_spread("620", model_rank=6), (6, 20))

    def test_single_ub_two_digit_lb_922(self):
        self.assertEqual(parse_rank_spread("922", model_rank=9), (9, 22))

    def test_two_digit_ub_two_digit_lb_1031(self):
        self.assertEqual(parse_rank_spread("1031", model_rank=10), (10, 31))

    def test_two_digit_ub_two_digit_lb_1634(self):
        self.assertEqual(parse_rank_spread("1634", model_rank=16), (16, 34))

    def test_two_digit_ub_three_digit_lb_74104(self):
        self.assertEqual(parse_rank_spread("74104", model_rank=74), (74, 104))

    def test_two_digit_ub_three_digit_lb_92105(self):
        self.assertEqual(parse_rank_spread("92105", model_rank=92), (92, 105))

    def test_three_digit_ub_three_digit_lb_123135(self):
        self.assertEqual(parse_rank_spread("123135", model_rank=123), (123, 135))

    def test_three_digit_ub_three_digit_lb_304305(self):
        self.assertEqual(parse_rank_spread("304305", model_rank=304), (304, 305))

    def test_empty_string_returns_none(self):
        self.assertIsNone(parse_rank_spread("", model_rank=1))

    def test_non_numeric_returns_none(self):
        self.assertIsNone(parse_rank_spread("abc", model_rank=1))

    def test_single_digit_returns_none(self):
        # A single digit can't be split into two valid ranks
        self.assertIsNone(parse_rank_spread("5", model_rank=5))

    def test_whitespace_stripped(self):
        self.assertEqual(parse_rank_spread("  12  ", model_rank=1), (1, 2))

    def test_rank_in_middle_of_spread(self):
        # model_rank=20, spread encodes 16-34
        self.assertEqual(parse_rank_spread("1634", model_rank=20), (16, 34))

    def test_rank_at_lower_bound(self):
        # model_rank=34, spread encodes 16-34
        self.assertEqual(parse_rank_spread("1634", model_rank=34), (16, 34))

    def test_wider_spread(self):
        # A model at rank 50 with spread encoding 40-65
        self.assertEqual(parse_rank_spread("4065", model_rank=50), (40, 65))

    def test_rank_ub_always_lte_lb(self):
        """Every valid parse must have ub <= lb."""
        test_cases = [
            ("12", 1), ("33", 3), ("615", 6), ("1634", 16),
            ("74104", 74), ("304305", 304),
        ]
        for raw, rank in test_cases:
            result = parse_rank_spread(raw, model_rank=rank)
            self.assertIsNotNone(result, f"Expected valid parse for {raw}, rank={rank}")
            self.assertLessEqual(result[0], result[1],
                                 f"ub > lb for {raw}: {result}")


class TestRankSpreadEdgeCases(unittest.TestCase):
    """Edge cases and ambiguous rank spread values."""

    def test_rank_slightly_outside_ci_still_parses(self):
        # Sometimes the displayed rank might be 1 position outside the CI
        # due to page rendering timing. The relaxed pass should still find it.
        result = parse_rank_spread("1634", model_rank=15)
        self.assertIsNotNone(result)
        # Should still find the 16,34 split as it's closest
        self.assertEqual(result, (16, 34))

    def test_ambiguous_four_digit_prefers_rank_guided_split(self):
        # "2050" with rank 20 should split as 20,50 not 2,050 or 205,0
        self.assertEqual(parse_rank_spread("2050", model_rank=20), (20, 50))

    def test_ambiguous_four_digit_rank_2(self):
        # "2050" with rank 2: split "2"/"050" rejected (leading zero in "050"),
        # so the relaxed pass finds (20, 50) as the only valid split.
        self.assertEqual(parse_rank_spread("2050", model_rank=2), (20, 50))


if __name__ == "__main__":
    unittest.main()
