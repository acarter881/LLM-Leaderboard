#!/usr/bin/env python3
"""Tests for overtake_probability module."""

import math
import sys
import os

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from overtake_probability import (
    compute_overtake_probability,
    projected_overtake_probability,
    compute_all_overtake_probabilities,
    enrich_snapshot,
    format_overtake_section,
    head_to_head_win_rate,
    compute_h2h_vs_leader,
    enrich_snapshot_with_h2h,
    format_h2h_section,
)


# ---------------------------------------------------------------------------
# Core function tests
# ---------------------------------------------------------------------------

class TestComputeOvertakeProbability:
    def test_identical_models_give_50_percent(self):
        """Two models with same score and CI → 50% overtake probability."""
        prob = compute_overtake_probability(1400, 10, 1400, 10)
        assert abs(prob - 0.5) < 1e-9

    def test_large_gap_tight_ci_near_zero(self):
        """Large score gap with tight CIs → near-zero probability."""
        prob = compute_overtake_probability(1500, 5, 1400, 5)
        assert prob < 1e-10

    def test_small_gap_wide_ci_near_50(self):
        """Small gap with wide CIs → probability near 50%."""
        prob = compute_overtake_probability(1400, 50, 1398, 50)
        assert 0.45 < prob < 0.55

    def test_symmetry(self):
        """P(A overtakes B) + P(B overtakes A) = 1."""
        p_ab = compute_overtake_probability(1504, 9, 1501, 8)
        p_ba = compute_overtake_probability(1501, 8, 1504, 9)
        assert abs(p_ab + p_ba - 1.0) < 1e-9

    def test_real_data_opus_vs_opus(self):
        """Real data: opus-4-6-thinking 1504±9 vs opus-4-6 1501±8 → ~31%."""
        prob = compute_overtake_probability(1504, 9, 1501, 8)
        # Expected ~0.31 based on the math
        assert 0.25 < prob < 0.40

    def test_challenger_ahead_gives_high_probability(self):
        """When the 'challenger' actually has a higher score, prob > 0.5."""
        prob = compute_overtake_probability(1400, 10, 1420, 10)
        assert prob > 0.9

    def test_zero_ci_behind(self):
        """CI=0, challenger behind → probability 0."""
        prob = compute_overtake_probability(1500, 0, 1400, 0)
        assert prob == 0.0

    def test_zero_ci_ahead(self):
        """CI=0, challenger ahead → probability 1."""
        prob = compute_overtake_probability(1400, 0, 1500, 0)
        assert prob == 1.0

    def test_zero_ci_tied(self):
        """CI=0, same score → probability 0.5."""
        prob = compute_overtake_probability(1400, 0, 1400, 0)
        assert prob == 0.5

    def test_one_zero_ci(self):
        """One model has CI=0, the other doesn't."""
        prob = compute_overtake_probability(1500, 0, 1490, 10)
        # Challenger behind with some uncertainty → low but not zero
        assert 0.0 < prob < 0.1

    def test_returns_float(self):
        result = compute_overtake_probability(1500, 10, 1490, 10)
        assert isinstance(result, float)

    def test_probability_bounded_0_1(self):
        """Result is always between 0 and 1."""
        for gap in [0, 1, 5, 10, 50, 100]:
            prob = compute_overtake_probability(1500, 10, 1500 - gap, 10)
            assert 0.0 <= prob <= 1.0


# ---------------------------------------------------------------------------
# Projected probability tests
# ---------------------------------------------------------------------------

class TestProjectedOvertakeProbability:
    def test_ci_shrinks_with_more_votes(self):
        """More votes → tighter CIs → lower overtake probability (when behind)."""
        prob_now = compute_overtake_probability(1504, 9, 1501, 8)
        prob_future = projected_overtake_probability(
            1504, 9, 10000,
            1501, 8, 10000,
            future_votes_multiplier=4.0,
        )
        # With 4x votes, CIs halve, probability should decrease
        assert prob_future < prob_now

    def test_multiplier_1_equals_current(self):
        """Multiplier of 1.0 should give the same result as current."""
        prob_now = compute_overtake_probability(1504, 9, 1501, 8)
        prob_proj = projected_overtake_probability(
            1504, 9, 10000,
            1501, 8, 10000,
            future_votes_multiplier=1.0,
        )
        assert abs(prob_now - prob_proj) < 1e-9

    def test_invalid_multiplier_raises(self):
        with pytest.raises(ValueError):
            projected_overtake_probability(1500, 10, 1000, 1490, 10, 1000, 0)
        with pytest.raises(ValueError):
            projected_overtake_probability(1500, 10, 1000, 1490, 10, 1000, -1)

    def test_large_multiplier_converges_to_deterministic(self):
        """With massive vote increase, probability approaches 0 for a gap."""
        prob = projected_overtake_probability(
            1504, 9, 10000,
            1501, 8, 10000,
            future_votes_multiplier=10000.0,
        )
        assert prob < 0.01


# ---------------------------------------------------------------------------
# Batch computation tests
# ---------------------------------------------------------------------------

def _make_snapshot(models_data: list[tuple]) -> dict:
    """Helper to create a snapshot dict from (name, rank, score, ci) tuples."""
    models = []
    for name, rank, score, ci in models_data:
        models.append({
            "model_name": name,
            "rank": rank,
            "score": score,
            "ci": ci,
        })
    return {"models": models, "timestamp": "2026-02-16T14:00:00Z"}


class TestComputeAllOvertakeProbabilities:
    def test_basic_output_structure(self):
        snapshot = _make_snapshot([
            ("model-a", 1, 1504, 9),
            ("model-b", 2, 1501, 8),
            ("model-c", 3, 1480, 4),
        ])
        result = compute_all_overtake_probabilities(snapshot)

        assert result["leader"]["model_name"] == "model-a"
        assert result["leader"]["score"] == 1504
        assert 0 <= result["leader"]["prob_staying_1"] <= 1
        assert len(result["overtake_probabilities"]) == 2

    def test_entries_have_required_fields(self):
        snapshot = _make_snapshot([
            ("leader", 1, 1500, 10),
            ("challenger", 2, 1490, 8),
        ])
        result = compute_all_overtake_probabilities(snapshot)
        entry = result["overtake_probabilities"][0]

        assert "model_name" in entry
        assert "rank" in entry
        assert "score" in entry
        assert "ci" in entry
        assert "score_gap" in entry
        assert "overtake_prob" in entry
        assert "fair_no_price_cents" in entry

    def test_score_gap_is_correct(self):
        snapshot = _make_snapshot([
            ("leader", 1, 1500, 10),
            ("challenger", 2, 1480, 8),
        ])
        result = compute_all_overtake_probabilities(snapshot)
        assert result["overtake_probabilities"][0]["score_gap"] == 20

    def test_fair_no_price_cents(self):
        snapshot = _make_snapshot([
            ("leader", 1, 1500, 10),
            ("challenger", 2, 1500, 10),
        ])
        result = compute_all_overtake_probabilities(snapshot)
        # 50% overtake → fair No = 50 cents
        fair_no = result["overtake_probabilities"][0]["fair_no_price_cents"]
        assert abs(fair_no - 50.0) < 0.01

    def test_empty_snapshot(self):
        result = compute_all_overtake_probabilities({"models": []})
        assert result["leader"] is None
        assert result["overtake_probabilities"] == []

    def test_single_model(self):
        snapshot = _make_snapshot([("only", 1, 1500, 10)])
        result = compute_all_overtake_probabilities(snapshot)
        assert result["leader"]["model_name"] == "only"
        assert result["leader"]["prob_staying_1"] == 1.0
        assert result["overtake_probabilities"] == []

    def test_preliminary_flag_preserved(self):
        snapshot = {
            "models": [
                {"model_name": "leader", "rank": 1, "score": 1500, "ci": 10},
                {"model_name": "prelim", "rank": 2, "score": 1490, "ci": 20, "is_preliminary": True},
            ]
        }
        result = compute_all_overtake_probabilities(snapshot)
        assert result["overtake_probabilities"][0].get("is_preliminary") is True

    def test_rank_ub_preserved(self):
        snapshot = {
            "models": [
                {"model_name": "leader", "rank": 1, "score": 1500, "ci": 10, "rank_ub": 1},
                {"model_name": "chal", "rank": 2, "score": 1490, "ci": 8, "rank_ub": 1},
            ]
        }
        result = compute_all_overtake_probabilities(snapshot)
        assert result["leader"]["rank_ub"] == 1
        assert result["overtake_probabilities"][0]["rank_ub"] == 1

    def test_top_n_limits_output(self):
        models = [("leader", 1, 1500, 10)]
        for i in range(2, 30):
            models.append((f"model-{i}", i, 1500 - i, 10))
        snapshot = _make_snapshot(models)
        result = compute_all_overtake_probabilities(snapshot, top_n=5)
        assert len(result["overtake_probabilities"]) == 4  # top_n minus leader

    def test_prob_staying_1_is_complement_of_number_2(self):
        snapshot = _make_snapshot([
            ("leader", 1, 1504, 9),
            ("number2", 2, 1501, 8),
        ])
        result = compute_all_overtake_probabilities(snapshot)
        prob_overtake = result["overtake_probabilities"][0]["overtake_prob"]
        prob_stay = result["leader"]["prob_staying_1"]
        assert abs(prob_stay + prob_overtake - 1.0) < 1e-6

    def test_missing_ci_skips_model(self):
        snapshot = {
            "models": [
                {"model_name": "leader", "rank": 1, "score": 1500, "ci": 10},
                {"model_name": "no-ci", "rank": 2, "score": 1490},
                {"model_name": "has-ci", "rank": 3, "score": 1480, "ci": 8},
            ]
        }
        result = compute_all_overtake_probabilities(snapshot)
        names = [e["model_name"] for e in result["overtake_probabilities"]]
        assert "no-ci" not in names
        assert "has-ci" in names


# ---------------------------------------------------------------------------
# Enrich snapshot tests
# ---------------------------------------------------------------------------

class TestEnrichSnapshot:
    def test_adds_overtake_field(self):
        snapshot = _make_snapshot([
            ("leader", 1, 1500, 10),
            ("chal", 2, 1490, 8),
        ])
        data = enrich_snapshot(snapshot)
        assert "overtake" in snapshot
        assert snapshot["overtake"] is data
        assert data["leader"]["model_name"] == "leader"


# ---------------------------------------------------------------------------
# Discord formatting tests
# ---------------------------------------------------------------------------

class TestFormatOvertakeSection:
    def test_empty_data_returns_empty(self):
        assert format_overtake_section({"leader": None, "overtake_probabilities": []}) == ""

    def test_includes_leader_name(self):
        data = compute_all_overtake_probabilities(_make_snapshot([
            ("the-leader", 1, 1500, 10),
            ("chal", 2, 1490, 8),
        ]))
        text = format_overtake_section(data)
        assert "the-leader" in text

    def test_includes_rank_and_name(self):
        data = compute_all_overtake_probabilities(_make_snapshot([
            ("leader", 1, 1500, 10),
            ("challenger-x", 2, 1490, 10),
        ]))
        text = format_overtake_section(data)
        assert "#2 challenger-x" in text

    def test_preliminary_flagged(self):
        snapshot = {
            "models": [
                {"model_name": "leader", "rank": 1, "score": 1500, "ci": 10},
                {"model_name": "prelim", "rank": 2, "score": 1490, "ci": 20, "is_preliminary": True},
            ]
        }
        data = compute_all_overtake_probabilities(snapshot)
        text = format_overtake_section(data)
        assert "Preliminary" in text

    def test_max_lines_respected(self):
        models = [("leader", 1, 1500, 10)]
        for i in range(2, 12):
            models.append((f"m{i}", i, 1495, 10))
        data = compute_all_overtake_probabilities(_make_snapshot(models))
        text = format_overtake_section(data, max_lines=3)
        # Should have header + 3 model lines + "All others" line
        lines = [l for l in text.strip().split("\n") if l.strip()]
        model_lines = [l for l in lines if l.strip().startswith("#")]
        assert len(model_lines) <= 3

    def test_threshold_filters_low_probability(self):
        data = compute_all_overtake_probabilities(_make_snapshot([
            ("leader", 1, 1500, 5),
            ("far-behind", 2, 1400, 5),
        ]))
        text = format_overtake_section(data, threshold=0.05)
        # far-behind has negligible probability, should be filtered
        assert "far-behind" not in text or "All others" in text


# ---------------------------------------------------------------------------
# Head-to-head win rate tests
# ---------------------------------------------------------------------------

class TestHeadToHeadWinRate:
    def test_equal_scores_give_50_percent(self):
        assert head_to_head_win_rate(1500, 1500) == pytest.approx(0.5)

    def test_higher_score_favored(self):
        wr = head_to_head_win_rate(1514, 1500)
        assert wr > 0.5
        # ~14pt gap ≈ 52%
        assert wr == pytest.approx(0.52, abs=0.005)

    def test_lower_score_unfavored(self):
        wr = head_to_head_win_rate(1486, 1500)
        assert wr < 0.5

    def test_symmetry(self):
        wr_a = head_to_head_win_rate(1510, 1490)
        wr_b = head_to_head_win_rate(1490, 1510)
        assert wr_a + wr_b == pytest.approx(1.0)

    def test_large_gap_strongly_favored(self):
        wr = head_to_head_win_rate(1600, 1400)
        # 200pt gap → ~76% (Elo scale of 400)
        assert wr > 0.75


class TestComputeH2hVsLeader:
    def test_basic_matchups(self):
        snapshot = _make_snapshot([
            ("leader", 1, 1504, 8),
            ("chal-a", 2, 1501, 10),
            ("chal-b", 3, 1490, 12),
        ])
        data = compute_h2h_vs_leader(snapshot, top_n=5)
        assert data["leader"]["model_name"] == "leader"
        assert len(data["matchups"]) == 2
        assert data["matchups"][0]["model_name"] == "chal-a"
        # chal-a is 3pts behind → slightly under 50%
        assert data["matchups"][0]["win_rate_vs_leader"] < 0.50

    def test_top_n_respected(self):
        models = [("leader", 1, 1504, 8)]
        for i in range(2, 10):
            models.append((f"m{i}", i, 1490, 10))
        data = compute_h2h_vs_leader(_make_snapshot(models), top_n=3)
        assert len(data["matchups"]) == 2  # top_n=3 means leader + 2

    def test_empty_snapshot(self):
        data = compute_h2h_vs_leader({"models": []})
        assert data["leader"] is None
        assert data["matchups"] == []


class TestEnrichSnapshotWithH2h:
    def test_adds_h2h_field(self):
        snapshot = _make_snapshot([
            ("leader", 1, 1504, 8),
            ("chal", 2, 1490, 10),
        ])
        enrich_snapshot_with_h2h(snapshot)
        assert "h2h" in snapshot
        assert snapshot["h2h"]["leader"]["model_name"] == "leader"


class TestFormatH2hSection:
    def test_empty_returns_empty(self):
        assert format_h2h_section({"leader": None, "matchups": []}) == ""

    def test_includes_leader_name(self):
        data = compute_h2h_vs_leader(_make_snapshot([
            ("the-boss", 1, 1504, 8),
            ("contender", 2, 1490, 10),
        ]))
        text = format_h2h_section(data)
        assert "the-boss" in text
        assert "Head-to-Head" in text

    def test_favored_model_gets_arrow(self):
        # Give challenger a higher score than leader
        data = compute_h2h_vs_leader(_make_snapshot([
            ("leader", 1, 1490, 8),
            ("hot-model", 2, 1504, 10),
        ]))
        text = format_h2h_section(data)
        assert "\u2191" in text  # up arrow for >50%

    def test_score_gap_shown(self):
        data = compute_h2h_vs_leader(_make_snapshot([
            ("leader", 1, 1504, 8),
            ("chal", 2, 1490, 10),
        ]))
        text = format_h2h_section(data)
        assert "pt" in text
