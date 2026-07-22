from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "report_edgebench_scores", ROOT / "scripts" / "report_edgebench_scores.py"
)
assert SPEC and SPEC.loader
REPORT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = REPORT
SPEC.loader.exec_module(REPORT)


def test_load_official_curves_contains_tree_task() -> None:
    curves = REPORT.load_official_curves(ROOT / "README.md")

    assert len(curves) == 51
    assert curves["tree_block_partitioning"]["models"]["GPT-5.5"][0] == 28.8
    assert curves["tree_block_partitioning"]["models"]["Opus 4.8"][-1] == 37.7


@pytest.mark.parametrize(
    ("task", "raw", "expected"),
    [
        ("tree_block_partitioning", 19.423611, 19.423611),
        ("ad_placement_optimization", 48_260_987_772.0, 45.85126429097374),
        ("wireless_electricity_layout", 54_075_524_923_419_384.0, 0.0),
    ],
)
def test_rescale_known_formal_scores(task: str, raw: float, expected: float) -> None:
    assert REPORT.rescale_raw_score(task, raw) == pytest.approx(expected)


def test_tree_comparison_uses_matching_two_hour_reference() -> None:
    observation = REPORT.observation_from_raw(
        "tree_block_partitioning", 19.423611, "gpt-5.5"
    )
    curves = REPORT.load_official_curves(ROOT / "README.md")

    result = REPORT.build_comparison(observation, curves, budget_hours=2)
    comparison = result["official_comparison"]

    assert comparison["matched_model"] == "GPT-5.5"
    assert comparison["matched_model_score"] == 28.8
    assert comparison["delta_vs_matched_model"] == pytest.approx(-9.376389)
    assert comparison["rank_including_run"] == 4
    assert comparison["rank_population"] == 6


def test_vliw_reports_float_tail_below_official_zero() -> None:
    observation = REPORT.observation_from_raw(
        "vliw_kernel_optimization", 8593.0, "gpt-5.5"
    )
    curves = REPORT.load_official_curves(ROOT / "README.md")

    result = REPORT.build_comparison(observation, curves, budget_hours=2)

    assert result["edgebench_score"] == 0.0
    assert result["edgebench_score_extended"] == pytest.approx(
        0.01 * 4475.526541978607 / 8593.0
    )
    assert "EdgeBench score: 0.0/100" in REPORT.render_text(result)
    assert "Local extended score:" in REPORT.render_text(result)
    assert "(diagnostic only)" in REPORT.render_text(result)
