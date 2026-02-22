#!/usr/bin/env python3
"""Tests for projections.py — settlement-date overtake projections."""

import math
import unittest
from datetime import datetime, timedelta, timezone

from projections import (
    MONTHLY,
    WEEKLY,
    bulk_vote_rates,
    compute_settlement_projections,
    days_until,
    enrich_snapshot_with_projections,
    format_all_projections,
    format_projections_section,
    next_settlement_date,
    project_ci,
    projected_overtake_at_date,
    time_to_resolution,
)


class TestNextSettlementDate(unittest.TestCase):
    """Settlement date computation for weekly and monthly cadences."""

    def test_weekly_from_wednesday(self):
        # Wednesday Feb 18 2026 12:00 UTC → Saturday Feb 21 2026 15:00 UTC
        wed = datetime(2026, 2, 18, 12, 0, 0, tzinfo=timezone.utc)
        result = next_settlement_date(WEEKLY, from_date=wed)
        self.assertEqual(result.weekday(), 5)  # Saturday
        self.assertEqual(result.day, 21)
        self.assertEqual(result.hour, 15)  # 10:00 AM EST

    def test_weekly_from_saturday_before_settlement_hour(self):
        # Saturday Feb 21 2026 10:00 UTC (before 15:00) → same day
        sat_early = datetime(2026, 2, 21, 10, 0, 0, tzinfo=timezone.utc)
        result = next_settlement_date(WEEKLY, from_date=sat_early)
        self.assertEqual(result.day, 21)
        self.assertEqual(result.hour, 15)

    def test_weekly_from_saturday_after_settlement_hour(self):
        # Saturday Feb 21 2026 16:00 UTC (after 15:00) → next Saturday
        sat_late = datetime(2026, 2, 21, 16, 0, 0, tzinfo=timezone.utc)
        result = next_settlement_date(WEEKLY, from_date=sat_late)
        self.assertEqual(result.day, 28)
        self.assertEqual(result.weekday(), 5)

    def test_monthly_mid_month(self):
        # Feb 18 2026 → Feb 28 2026 (non-leap year)
        mid = datetime(2026, 2, 18, 12, 0, 0, tzinfo=timezone.utc)
        result = next_settlement_date(MONTHLY, from_date=mid)
        self.assertEqual(result.month, 2)
        self.assertEqual(result.day, 28)

    def test_monthly_last_day_after_settlement_rolls_forward(self):
        # Feb 28 2026 16:00 UTC (after 15:00 settlement) → March 31 2026
        end_feb = datetime(2026, 2, 28, 16, 0, 0, tzinfo=timezone.utc)
        result = next_settlement_date(MONTHLY, from_date=end_feb)
        self.assertEqual(result.month, 3)
        self.assertEqual(result.day, 31)

    def test_monthly_december_rolls_to_january(self):
        dec = datetime(2025, 12, 31, 16, 0, 0, tzinfo=timezone.utc)
        result = next_settlement_date(MONTHLY, from_date=dec)
        self.assertEqual(result.year, 2026)
        self.assertEqual(result.month, 1)
        self.assertEqual(result.day, 31)

    def test_invalid_cadence_raises(self):
        with self.assertRaises(ValueError):
            next_settlement_date("quarterly")


class TestDaysUntil(unittest.TestCase):
    def test_future_date(self):
        now = datetime(2026, 2, 18, 12, 0, 0, tzinfo=timezone.utc)
        target = datetime(2026, 2, 21, 12, 0, 0, tzinfo=timezone.utc)
        self.assertAlmostEqual(days_until(target, from_date=now), 3.0)

    def test_fractional_days(self):
        now = datetime(2026, 2, 18, 12, 0, 0, tzinfo=timezone.utc)
        target = datetime(2026, 2, 20, 0, 0, 0, tzinfo=timezone.utc)
        self.assertAlmostEqual(days_until(target, from_date=now), 1.5)

    def test_past_date_returns_zero(self):
        now = datetime(2026, 2, 21, 12, 0, 0, tzinfo=timezone.utc)
        target = datetime(2026, 2, 18, 12, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(days_until(target, from_date=now), 0.0)


class TestProjectCI(unittest.TestCase):
    def test_ci_shrinks_with_more_votes(self):
        # 10000 votes, 500/day, 10 days → 15000 votes
        # shrink = sqrt(10000/15000) ≈ 0.8165
        ci = project_ci(10.0, 10000, 500.0, 10.0)
        expected = 10.0 * math.sqrt(10000 / 15000)
        self.assertAlmostEqual(ci, expected, places=4)

    def test_zero_days_returns_current(self):
        self.assertEqual(project_ci(10.0, 10000, 500.0, 0.0), 10.0)

    def test_zero_votes_returns_current(self):
        self.assertEqual(project_ci(10.0, 0, 500.0, 10.0), 10.0)

    def test_zero_rate_returns_current(self):
        self.assertEqual(project_ci(10.0, 10000, 0.0, 10.0), 10.0)

    def test_doubles_votes_shrinks_by_sqrt2(self):
        # 1000 votes, 1000 in 10 days → 2000 total → shrink by 1/sqrt(2)
        ci = project_ci(10.0, 1000, 100.0, 10.0)
        expected = 10.0 / math.sqrt(2)
        self.assertAlmostEqual(ci, expected, places=4)


class TestProjectedOvertakeAtDate(unittest.TestCase):
    def test_tighter_cis_reduce_overtake_when_leader_ahead(self):
        # Leader ahead by 20 points.  As CIs shrink, overtake becomes less likely.
        result = projected_overtake_at_date(
            score_a=1350, ci_a=10, votes_a=5000, vpd_a=200,
            score_b=1330, ci_b=12, votes_b=4000, vpd_b=150,
            days_ahead=7.0,
        )
        self.assertGreater(result["prob_now"], result["prob_at_settlement"])
        self.assertAlmostEqual(result["days_ahead"], 7.0)

    def test_zero_days_now_equals_settlement(self):
        result = projected_overtake_at_date(
            score_a=1350, ci_a=10, votes_a=5000, vpd_a=200,
            score_b=1340, ci_b=10, votes_b=5000, vpd_b=200,
            days_ahead=0.0,
        )
        self.assertAlmostEqual(result["prob_now"], result["prob_at_settlement"])

    def test_no_vote_data_still_works(self):
        result = projected_overtake_at_date(
            score_a=1350, ci_a=10, votes_a=0, vpd_a=0,
            score_b=1340, ci_b=10, votes_b=0, vpd_b=0,
            days_ahead=7.0,
        )
        # Without vote data, projections degrade to current probability.
        self.assertAlmostEqual(result["prob_now"], result["prob_at_settlement"])


class TestTimeToResolution(unittest.TestCase):
    def test_already_resolved(self):
        # 50-point gap with small CIs — already <5% overtake.
        ttr = time_to_resolution(
            score_a=1400, ci_a=5, votes_a=10000, vpd_a=100,
            score_b=1350, ci_b=5, votes_b=10000, vpd_b=100,
        )
        self.assertEqual(ttr, 0.0)

    def test_close_race_takes_time(self):
        # 5-point gap, wide CIs → needs votes to accumulate.
        ttr = time_to_resolution(
            score_a=1350, ci_a=15, votes_a=3000, vpd_a=200,
            score_b=1345, ci_b=15, votes_b=3000, vpd_b=200,
        )
        self.assertIsNotNone(ttr)
        self.assertGreater(ttr, 0)

    def test_returns_none_if_never_resolves(self):
        # Same score, no votes being added.
        ttr = time_to_resolution(
            score_a=1350, ci_a=10, votes_a=5000, vpd_a=0,
            score_b=1345, ci_b=10, votes_b=5000, vpd_b=0,
            max_days=30,
        )
        self.assertIsNone(ttr)


class TestBulkVoteRates(unittest.TestCase):
    def test_basic_rate(self):
        now = datetime(2026, 2, 20, 12, 0, 0, tzinfo=timezone.utc)
        ts = [
            {
                "ts": "2026-02-18T12:00:00Z",
                "models": [{"name": "ModelA", "votes": 1000, "rank": 1, "score": 1350}],
            },
            {
                "ts": "2026-02-20T12:00:00Z",
                "models": [{"name": "ModelA", "votes": 1400, "rank": 1, "score": 1350}],
            },
        ]
        rates = bulk_vote_rates(ts, {"ModelA"}, lookback_days=7, now=now)
        # 400 votes in 2 days = 200/day
        self.assertAlmostEqual(rates["ModelA"], 200.0, places=0)

    def test_missing_model_returns_zero(self):
        rates = bulk_vote_rates([], {"ModelX"}, now=datetime.now(timezone.utc))
        self.assertEqual(rates["ModelX"], 0.0)

    def test_single_datapoint_returns_zero(self):
        now = datetime(2026, 2, 20, 12, 0, 0, tzinfo=timezone.utc)
        ts = [
            {
                "ts": "2026-02-20T12:00:00Z",
                "models": [{"name": "ModelA", "votes": 1000, "rank": 1, "score": 1350}],
            },
        ]
        rates = bulk_vote_rates(ts, {"ModelA"}, lookback_days=7, now=now)
        self.assertEqual(rates["ModelA"], 0.0)


class TestComputeSettlementProjections(unittest.TestCase):
    def _make_snapshot(self):
        return {
            "timestamp": "2026-02-20T12:00:00Z",
            "leaderboard_date": "Feb 20, 2026",
            "total_models": 3,
            "models": [
                {"rank": 1, "model_name": "Alpha", "score": 1350, "ci": 8, "votes": 5000, "organization": "OrgA"},
                {"rank": 2, "model_name": "Beta", "score": 1340, "ci": 10, "votes": 4000, "organization": "OrgB"},
                {"rank": 3, "model_name": "Gamma", "score": 1320, "ci": 15, "votes": 3000, "organization": "OrgB"},
            ],
        }

    def test_basic_projections(self):
        now = datetime(2026, 2, 20, 12, 0, 0, tzinfo=timezone.utc)
        result = compute_settlement_projections(
            self._make_snapshot(),
            timeseries=[],
            cadence=WEEKLY,
            now=now,
        )
        self.assertIn("settlement_date", result)
        self.assertIn("days_remaining", result)
        self.assertGreater(result["days_remaining"], 0)
        self.assertEqual(len(result["projections"]), 2)
        self.assertEqual(result["leader"]["model_name"], "Alpha")

    def test_org_projections_aggregated(self):
        now = datetime(2026, 2, 20, 12, 0, 0, tzinfo=timezone.utc)
        result = compute_settlement_projections(
            self._make_snapshot(),
            timeseries=[],
            cadence=WEEKLY,
            now=now,
        )
        # OrgB has Beta and Gamma; should appear once with max prob.
        org_names = [o["organization"] for o in result["org_projections"]]
        self.assertIn("OrgB", org_names)
        # OrgA is the leader's org, should NOT appear.
        self.assertNotIn("OrgA", org_names)

    def test_empty_snapshot(self):
        result = compute_settlement_projections(
            {"models": []}, cadence=WEEKLY,
        )
        self.assertIsNone(result["leader"])
        self.assertEqual(result["projections"], [])

    def test_with_timeseries_data(self):
        now = datetime(2026, 2, 20, 12, 0, 0, tzinfo=timezone.utc)
        ts = [
            {
                "ts": "2026-02-17T12:00:00Z",
                "models": [
                    {"name": "Alpha", "votes": 4500, "rank": 1, "score": 1350},
                    {"name": "Beta", "votes": 3500, "rank": 2, "score": 1340},
                ],
            },
            {
                "ts": "2026-02-20T12:00:00Z",
                "models": [
                    {"name": "Alpha", "votes": 5000, "rank": 1, "score": 1350},
                    {"name": "Beta", "votes": 4000, "rank": 2, "score": 1340},
                ],
            },
        ]
        result = compute_settlement_projections(
            self._make_snapshot(),
            timeseries=ts,
            cadence=WEEKLY,
            now=now,
        )
        # Leader should have positive vote rate.
        self.assertGreater(result["leader"]["votes_per_day"], 0)
        # Beta should have positive vote rate.
        beta = result["projections"][0]
        self.assertGreater(beta["votes_per_day"], 0)
        # With tightening CIs, settlement prob should differ from current.
        self.assertNotAlmostEqual(
            beta["prob_now"], beta["prob_at_settlement"], places=4,
        )


class TestEnrichSnapshot(unittest.TestCase):
    def test_adds_both_cadences(self):
        snapshot = {
            "models": [
                {"rank": 1, "model_name": "A", "score": 1350, "ci": 8, "votes": 5000},
                {"rank": 2, "model_name": "B", "score": 1340, "ci": 10, "votes": 4000},
            ],
        }
        result = enrich_snapshot_with_projections(snapshot)
        self.assertIn(WEEKLY, result)
        self.assertIn(MONTHLY, result)
        self.assertIn("projections", snapshot)


class TestFormatProjections(unittest.TestCase):
    def test_format_section_basic(self):
        now = datetime(2026, 2, 20, 12, 0, 0, tzinfo=timezone.utc)
        snapshot = {
            "models": [
                {"rank": 1, "model_name": "Leader", "score": 1350, "ci": 8, "votes": 5000, "organization": "OrgA"},
                {"rank": 2, "model_name": "Chaser", "score": 1340, "ci": 10, "votes": 4000, "organization": "OrgB"},
            ],
        }
        data = compute_settlement_projections(snapshot, cadence=WEEKLY, now=now)
        section = format_projections_section(data)
        self.assertIn("Settlement Projections", section)
        self.assertIn("Chaser", section)

    def test_format_all_projections(self):
        now = datetime(2026, 2, 20, 12, 0, 0, tzinfo=timezone.utc)
        snapshot = {
            "models": [
                {"rank": 1, "model_name": "A", "score": 1350, "ci": 8, "votes": 5000},
                {"rank": 2, "model_name": "B", "score": 1340, "ci": 10, "votes": 4000},
            ],
        }
        by_cadence = enrich_snapshot_with_projections(snapshot, now=now)
        text = format_all_projections(by_cadence)
        self.assertIn("Weekly", text)
        self.assertIn("Monthly", text)

    def test_format_dedup_when_same_settlement_date(self):
        """When weekly and monthly settle on the same day, show one combined section."""
        # Saturday Feb 28 2026 is both the last day of month AND a Saturday.
        now = datetime(2026, 2, 25, 12, 0, 0, tzinfo=timezone.utc)
        snapshot = {
            "models": [
                {"rank": 1, "model_name": "A", "score": 1350, "ci": 8, "votes": 5000},
                {"rank": 2, "model_name": "B", "score": 1340, "ci": 10, "votes": 4000},
            ],
        }
        by_cadence = enrich_snapshot_with_projections(snapshot, now=now)
        text = format_all_projections(by_cadence)
        # Should show the combined header, not separate Weekly + Monthly.
        self.assertIn("Weekly & Monthly", text)
        self.assertEqual(text.count("Settlement Projections"), 1)

    def test_format_empty_returns_empty(self):
        data = compute_settlement_projections({"models": []}, cadence=WEEKLY)
        self.assertEqual(format_projections_section(data), "")


if __name__ == "__main__":
    unittest.main()
