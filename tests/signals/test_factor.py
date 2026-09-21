"""因子排序与目标权重纯函数测试。"""

from __future__ import annotations

import math

import pytest

from trading_assistant.signals.factor import calculate_factor_weights


def test_factor_weights_select_top_scores_with_stable_ties() -> None:
    scores = {"C.US": 1.0, "B.US": 2.0, "A.US": 2.0, "D.US": -1.0}
    result = calculate_factor_weights(
        scores,
        top_n=3,
        target_gross_exposure=0.75,
        max_unscorable_fraction=0.2,
    )
    assert result.status == "REBALANCE"
    assert dict(result.target_weights) == {"A.US": 0.25, "B.US": 0.25, "C.US": 0.25}


def test_factor_decision_skips_when_universe_is_too_small() -> None:
    result = calculate_factor_weights(
        {"A.US": 1.0},
        top_n=2,
        target_gross_exposure=0.75,
        max_unscorable_fraction=0.2,
    )
    assert result.status == "SKIP"
    assert result.target_weights == ()
    assert result.reason == "insufficient_eligible_scores"


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
        calculate_factor_weights(
            scores, top_n=top_n, target_gross_exposure=gross, max_unscorable_fraction=0.2
        )


@pytest.mark.parametrize("missing", [1, 3, 10])
def test_partial_cross_section_keeps_denominator_and_protection(missing: int) -> None:
    scores = {f"S{i}.US": None if i < missing else float(i - 5) for i in range(10)}
    result = calculate_factor_weights(
        scores, top_n=3, target_gross_exposure=0.75, max_unscorable_fraction=0.2
    )
    assert result.candidate_count == 10
    assert result.eligible_count == 10 - missing
    assert len(result.preserve_positions) == missing
    assert result.status == ("REBALANCE" if missing == 1 else "SKIP")
