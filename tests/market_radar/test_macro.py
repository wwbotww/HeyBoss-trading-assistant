"""宏观风险偏好计算与 payload 契约测试。"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta

import pytest

from trading_assistant.market_radar import macro as macro_module
from trading_assistant.market_radar.macro import (
    MIN_ROBUST_OBSERVATIONS,
    RiskAppetiteComponents,
    RiskAppetitePoint,
    RiskAppetiteSnapshot,
    calculate_risk_appetite_snapshot,
)
from trading_assistant.market_radar.metrics import PriceBar

NOW = datetime(2026, 9, 4, 12, tzinfo=UTC)


def _bars(values: list[float], *, start: date = date(2024, 1, 1)) -> tuple[PriceBar, ...]:
    return tuple(
        PriceBar(
            day=start + timedelta(days=index),
            high=value * 1.01,
            low=value * 0.99,
            close=value,
        )
        for index, value in enumerate(values)
    )


def _histories(count: int) -> tuple[tuple[PriceBar, ...], ...]:
    indexes = range(count)
    hyg = [80 * math.exp(0.0002 * index + 0.025 * math.sin(index / 11)) for index in indexes]
    lqd = [100 * math.exp(0.0001 * index + 0.012 * math.cos(index / 17)) for index in indexes]
    vix = [20 * math.exp(0.12 * math.sin(index / 13) + 0.004 * (index % 7)) for index in indexes]
    vix3m = [22 * math.exp(0.06 * math.cos(index / 19)) for index in indexes]
    return _bars(hyg), _bars(lqd), _bars(vix), _bars(vix3m)


def test_complete_snapshot_uses_exact_formula_and_sixty_point_trajectory() -> None:
    hyg, lqd, vix, vix3m = _histories(600)

    snapshot = calculate_risk_appetite_snapshot(
        hyg_bars=hyg,
        lqd_bars=lqd,
        vix_bars=vix,
        vix3m_bars=vix3m,
        calculated_at_utc=NOW,
    )

    assert snapshot.validity == "complete"
    assert snapshot.credit_source == "etf_proxy"
    assert snapshot.price_source == "eodhd_nt_catalog"
    assert snapshot.observations == 580
    assert snapshot.required == MIN_ROBUST_OBSERVATIONS
    assert snapshot.current is not None
    assert len(snapshot.trajectory) == 60
    assert snapshot.trajectory[-1] == snapshot.current
    assert snapshot.current.score == pytest.approx(
        0.60 * snapshot.current.credit_z - 0.40 * snapshot.current.volatility_z
    )
    assert snapshot.components.credit_log_change_20 == pytest.approx(
        math.log((hyg[-1].close / lqd[-1].close) / (hyg[-21].close / lqd[-21].close))
    )
    assert snapshot.components.volatility_term_log == pytest.approx(
        math.log(vix[-1].close / vix3m[-1].close)
    )
    assert RiskAppetiteSnapshot.from_payload(snapshot.to_payload()) == snapshot


def test_short_history_is_published_without_a_score() -> None:
    hyg, lqd, vix, vix3m = _histories(200)

    snapshot = calculate_risk_appetite_snapshot(
        hyg_bars=hyg,
        lqd_bars=lqd,
        vix_bars=vix,
        vix3m_bars=vix3m,
        calculated_at_utc=NOW,
    )

    assert snapshot.validity == "insufficient_history"
    assert snapshot.observations == 180
    assert snapshot.current is None
    assert snapshot.trajectory == ()
    assert snapshot.components.day == hyg[-1].day


def test_zero_mad_is_unavailable_instead_of_neutral() -> None:
    constant = _bars([100.0] * 525)

    snapshot = calculate_risk_appetite_snapshot(
        hyg_bars=constant,
        lqd_bars=constant,
        vix_bars=constant,
        vix3m_bars=constant,
        calculated_at_utc=NOW,
    )

    assert snapshot.validity == "unavailable"
    assert snapshot.observations == 505
    assert snapshot.current is None
    assert snapshot.trajectory == ()


def test_robust_z_is_clamped_and_requires_minimum_history() -> None:
    values = [float(index % 13) for index in range(MIN_ROBUST_OBSERVATIONS - 1)]
    assert macro_module._robust_z(values) is None
    assert macro_module._robust_z([*values, 1_000_000.0]) == 3.0
    assert macro_module._robust_z([*values, -1_000_000.0]) == -3.0


def test_input_dates_must_be_ordered_and_share_the_latest_day() -> None:
    hyg, lqd, vix, vix3m = _histories(30)
    with pytest.raises(ValueError, match="latest trading date"):
        calculate_risk_appetite_snapshot(
            hyg_bars=hyg,
            lqd_bars=lqd[:-1],
            vix_bars=vix,
            vix3m_bars=vix3m,
            calculated_at_utc=NOW,
        )
    with pytest.raises(ValueError, match="unique and increasing"):
        calculate_risk_appetite_snapshot(
            hyg_bars=(hyg[1], hyg[0], *hyg[2:]),
            lqd_bars=lqd,
            vix_bars=vix,
            vix3m_bars=vix3m,
            calculated_at_utc=NOW,
        )


def test_missing_or_invalid_calculation_inputs_fail_closed() -> None:
    hyg, lqd, vix, vix3m = _histories(30)
    with pytest.raises(ValueError, match="history is required"):
        calculate_risk_appetite_snapshot(
            hyg_bars=(),
            lqd_bars=lqd,
            vix_bars=vix,
            vix3m_bars=vix3m,
            calculated_at_utc=NOW,
        )
    with pytest.raises(ValueError, match="at least 21"):
        calculate_risk_appetite_snapshot(
            hyg_bars=hyg[:20],
            lqd_bars=lqd[:20],
            vix_bars=vix[:20],
            vix3m_bars=vix3m[:20],
            calculated_at_utc=NOW,
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        calculate_risk_appetite_snapshot(
            hyg_bars=hyg,
            lqd_bars=lqd,
            vix_bars=vix,
            vix3m_bars=vix3m,
            calculated_at_utc=datetime(2026, 9, 4),
        )


def test_snapshot_and_payload_invariants_reject_invalid_values() -> None:
    components = RiskAppetiteComponents(
        day=date(2026, 9, 3),
        hyg_close=80,
        lqd_close=100,
        vix_close=20,
        vix3m_close=22,
        credit_log_change_20=0.01,
        volatility_term_log=-0.09,
    )
    point = RiskAppetitePoint(
        day=date(2026, 9, 3),
        score=0.6,
        credit_z=1,
        volatility_z=0,
    )
    snapshot = RiskAppetiteSnapshot(
        as_of_date=date(2026, 9, 3),
        calculated_at_utc=NOW,
        validity="complete",
        observations=504,
        required=504,
        credit_source="etf_proxy",
        price_source="eodhd_nt_catalog",
        components=components,
        current=point,
        trajectory=(point,),
    )
    payload = snapshot.to_payload()
    payload["unknown"] = True
    with pytest.raises(ValueError, match="unknown fields"):
        RiskAppetiteSnapshot.from_payload(payload)
    with pytest.raises(ValueError, match="does not match"):
        RiskAppetitePoint(
            day=date(2026, 9, 3),
            score=0,
            credit_z=1,
            volatility_z=0,
        )
    with pytest.raises(ValueError, match="positive prices"):
        RiskAppetiteComponents(
            day=date(2026, 9, 3),
            hyg_close=0,
            lqd_close=100,
            vix_close=20,
            vix3m_close=22,
            credit_log_change_20=0,
            volatility_term_log=0,
        )
