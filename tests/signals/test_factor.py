"""因子排序与目标权重纯函数测试。"""

from __future__ import annotations

import math

import pytest

from trading_assistant.signals.factor import calculate_factor_weights


def test_factor_weights_select_top_scores_with_stable_ties() -> None:
    scores = {"C.US": 1.0, "B.US": 2.0, "A.US": 2.0, "D.US": -1.0}
    assert calculate_factor_weights(
        scores,
        top_n=3,
        target_gross_exposure=0.75,
    ) == {"A.US": 0.25, "B.US": 0.25, "C.US": 0.25}


def test_factor_weights_remain_cash_when_universe_is_too_small() -> None:
    assert (
        calculate_factor_weights(
            {"A.US": 1.0},
            top_n=2,
            target_gross_exposure=0.75,
        )
        == {}
    )


@pytest.mark.parametrize(
    ("scores", "top_n", "gross", "message"),
    [
        ({"A.US": math.nan}, 1, 0.5, "finite"),
        ({"A.US": 1.0}, 0, 0.5, "top_n"),
        ({"A.US": 1.0}, 1, 0.0, "target_gross"),
        ({"A.US": 1.0}, 1, 1.1, "target_gross"),
    ],
)
def test_factor_weights_reject_invalid_inputs(
    scores: dict[str, float],
    top_n: int,
    gross: float,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        calculate_factor_weights(scores, top_n=top_n, target_gross_exposure=gross)
