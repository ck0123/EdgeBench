from __future__ import annotations

from sforge.harness.selection import select_best


def test_pass_rate_first_breaks_partial_pass_ties_by_maximized_score() -> None:
    result = select_best(
        [
            {"round": "auto-2", "pass_rate": 0.8, "score": 3.3e-9},
            {"round": "auto-52", "pass_rate": 0.8, "score": 3.9603145032},
        ],
        score_direction="maximize",
        policy="pass_rate_first",
    )

    assert result["best_round"] == "auto-52"
    assert result["best_pass_rate"] == 0.8
    assert result["best_score"] == 3.9603145032


def test_pass_rate_first_still_prioritizes_higher_pass_rate() -> None:
    result = select_best(
        [
            {"round": "higher-score", "pass_rate": 0.8, "score": 99.0},
            {"round": "higher-pass-rate", "pass_rate": 1.0, "score": 1.0},
        ],
        score_direction="maximize",
        policy="pass_rate_first",
    )

    assert result["best_round"] == "higher-pass-rate"
    assert result["best_score"] == 1.0


def test_pass_rate_first_honors_minimized_score_for_equal_pass_rate() -> None:
    result = select_best(
        [
            {"round": "slow", "pass_rate": 0.5, "score": 20.0},
            {"round": "fast", "pass_rate": 0.5, "score": 10.0},
        ],
        score_direction="minimize",
        policy="pass_rate_first",
    )

    assert result["best_round"] == "fast"
    assert result["best_score"] == 10.0


def test_pass_rate_first_prefers_a_score_over_missing_score_at_equal_pass_rate() -> None:
    result = select_best(
        [
            {"round": "unscored", "pass_rate": 0.8, "score": None},
            {"round": "scored", "pass_rate": 0.8, "score": 0.1},
        ],
        score_direction="maximize",
        policy="pass_rate_first",
    )

    assert result["best_round"] == "scored"
    assert result["best_score"] == 0.1


def test_pass_rate_first_keeps_pass_rate_as_score_for_unscored_tasks() -> None:
    result = select_best(
        [{"round": "tests", "pass_rate": 0.75, "score": None}],
        policy="pass_rate_first",
    )

    assert result["best_round"] == "tests"
    assert result["best_score"] == 0.75
