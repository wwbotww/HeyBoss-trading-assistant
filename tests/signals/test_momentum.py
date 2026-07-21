"""双动量纯函数测试。"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd
import pytest

from trading_assistant.signals.momentum import calculate_dual_momentum_weights


def _prices(values: dict[str, Sequence[float | None]]) -> pd.DataFrame:
    return pd.DataFrame(values, index=pd.period_range("2025-01", periods=7, freq="M"))


def test_selects_positive_top_n_with_stable_tie_break() -> None:
    closes = _prices(
        {
            "BBB.ARCA": [100, 100, 100, 100, 100, 100, 120],
            "AAA.ARCA": [100, 100, 100, 100, 100, 100, 120],
            "CCC.ARCA": [100, 100, 100, 100, 100, 100, 110],
            "BIL.ARCA": [90, 90, 90, 90, 90, 90, 91],
        }
    )

    result = calculate_dual_momentum_weights(
        closes,
        lookback_months=6,
        top_n=2,
        fallback_instrument="BIL.ARCA",
    )

    assert result == {"AAA.ARCA": 0.5, "BBB.ARCA": 0.5}


def test_all_negative_uses_fallback_without_ranking_it() -> None:
    closes = _prices(
        {
            "AAA.ARCA": [100, 100, 100, 100, 100, 100, 90],
            "BBB.ARCA": [100, 100, 100, 100, 100, 100, 80],
            "BIL.ARCA": [90, 90, 90, 90, 90, 90, 91],
        }
    )
    assert calculate_dual_momentum_weights(
        closes,
        lookback_months=6,
        top_n=3,
        fallback_instrument="BIL.ARCA",
    ) == {"BIL.ARCA": 1.0}


def test_insufficient_calendar_history_keeps_cash() -> None:
    closes = pd.DataFrame(
        {"AAA.ARCA": [100, 110]},
        index=pd.PeriodIndex(["2025-01", "2025-07"], freq="M"),
    ).drop(index="2025-01")
    assert (
        calculate_dual_momentum_weights(
            closes,
            lookback_months=6,
            top_n=1,
            fallback_instrument="BIL.ARCA",
        )
        == {}
    )


def test_empty_or_invalid_prices_keep_cash() -> None:
    assert (
        calculate_dual_momentum_weights(
            pd.DataFrame(),
            lookback_months=6,
            top_n=1,
            fallback_instrument="BIL.ARCA",
        )
        == {}
    )
    closes = _prices(
        {
            "AAA.ARCA": [0, 1, 1, 1, 1, 1, 2],
            "BBB.ARCA": [100, 100, 100, 100, 100, 100, 0],
            "BIL.ARCA": [90, 90, 90, 90, 90, 90, 0],
        }
    )
    assert (
        calculate_dual_momentum_weights(
            closes,
            lookback_months=6,
            top_n=1,
            fallback_instrument="BIL.ARCA",
        )
        == {}
    )


def test_missing_candidate_is_excluded_without_blocking_valid_candidate() -> None:
    closes = _prices(
        {
            "AAA.ARCA": [100, 100, 100, 100, 100, 100, 110],
            "HALT.ARCA": [100, 100, 100, 100, 100, 100, None],
            "BIL.ARCA": [90, 90, 90, 90, 90, 90, 91],
        }
    )
    assert calculate_dual_momentum_weights(
        closes,
        lookback_months=6,
        top_n=2,
        fallback_instrument="BIL.ARCA",
    ) == {"AAA.ARCA": 1.0}


@pytest.mark.parametrize(("lookback", "top_n"), [(0, 1), (1, 0)])
def test_invalid_parameters_raise(lookback: int, top_n: int) -> None:
    with pytest.raises(ValueError, match="必须大于等于 1"):
        calculate_dual_momentum_weights(
            _prices({"AAA.ARCA": [1, 1, 1, 1, 1, 1, 1]}),
            lookback_months=lookback,
            top_n=top_n,
            fallback_instrument="BIL.ARCA",
        )
