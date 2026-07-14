from __future__ import annotations

import pytest

from sforge.harness.score_rescale import (
    RescaleSpec,
    rescale_score,
    rescale_score_extended,
)


WIRELESS_RESCALE = RescaleSpec(
    kind="piecewise_log_min",
    baseline=2.6059066264593484e16,
    rank30=2444746514528109.0,
    rank1=362439546835871.0,
    super_anchor=181219773417935.5,
)


def test_extended_score_preserves_official_zero_and_adds_local_tail() -> None:
    raw = 6.502510377996556e16

    assert rescale_score(WIRELESS_RESCALE, raw) == 0.0
    assert rescale_score_extended(WIRELESS_RESCALE, raw) == pytest.approx(
        0.004007539388112296
    )


def test_extended_tail_distinguishes_valid_improvements() -> None:
    worse = rescale_score_extended(WIRELESS_RESCALE, 7.2e16)
    better = rescale_score_extended(WIRELESS_RESCALE, 6.5e16)

    assert worse is not None and better is not None
    assert 0.0 < worse < better < 0.01


def test_extended_tail_does_not_reward_invalid_result() -> None:
    assert rescale_score_extended(
        WIRELESS_RESCALE,
        6.5e16,
        valid=False,
    ) == 0.0
