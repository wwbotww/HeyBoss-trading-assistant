"""实际利率与风险偏好宏观象限契约测试。"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from trading_assistant.market_radar.fred import FredObservation
from trading_assistant.market_radar.macro import (
    RiskAppetiteComponents,
    RiskAppetitePoint,
    RiskAppetiteSnapshot,
)
from trading_assistant.market_radar.regime import (
    ALIGNMENT_MAX_AGE_DAYS,
    NEUTRAL_BAND,
    MacroRegimeSnapshot,
    calculate_macro_regime_snapshot,
    classify_regime,
)

NOW = datetime(2026, 9, 4, 12, tzinfo=UTC)
START = date(2025, 1, 1)


def _observations(
    count: int, *, end_gap: int = 0, flat: bool = False
) -> tuple[FredObservation, ...]:
    values = [
        1.2 if flat else 1.2 + 0.002 * index + 0.08 * math.sin(index / 11) for index in range(count)
    ]
    return tuple(
        FredObservation(
            series_id="DFII10",
            observation_date=START + timedelta(days=index),
            value=value,
            realtime_start=date(2026, 9, 4),
            realtime_end=date(2026, 9, 4),
        )
        for index, value in enumerate(values[:-end_gap] if end_gap else values)
    )


def _risk_snapshot(as_of_date: date) -> RiskAppetiteSnapshot:
    points = tuple(
        RiskAppetitePoint(
            day=as_of_date - timedelta(days=59 - index),
            score=0.6,
            credit_z=1,
            volatility_z=0,
        )
        for index in range(60)
    )
    return RiskAppetiteSnapshot(
        as_of_date=as_of_date,
        calculated_at_utc=NOW,
        validity="complete",
        observations=580,
        required=504,
        credit_source="etf_proxy",
        price_source="eodhd_nt_catalog",
        components=RiskAppetiteComponents(
            day=as_of_date,
            hyg_close=80,
            lqd_close=100,
            vix_close=20,
            vix3m_close=22,
            credit_log_change_20=0.01,
            volatility_term_log=-0.09,
        ),
        current=points[-1],
        trajectory=points,
    )


def test_complete_regime_uses_bounded_as_of_alignment_and_round_trips() -> None:
    observations = _observations(600)
    risk = _risk_snapshot(observations[-1].observation_date)

    snapshot = calculate_macro_regime_snapshot(
        real_rate_observations=observations,
        risk_appetite=risk,
        calculated_at_utc=NOW,
    )

    assert snapshot.validity == "complete"
    assert snapshot.current is not None
    assert snapshot.current.day == risk.as_of_date
    assert snapshot.current.real_rate_observation_date <= snapshot.current.day
    assert len(snapshot.trajectory) == 60
    assert snapshot.real_rate.series_id == "DFII10"
    assert snapshot.real_rate.observations == 580
    assert snapshot.real_rate.required == 504
    assert snapshot.real_rate.pressure_z is not None
    assert snapshot.neutral_band == NEUTRAL_BAND
    assert snapshot.alignment_max_age_days == ALIGNMENT_MAX_AGE_DAYS
    assert MacroRegimeSnapshot.from_payload(snapshot.to_payload()) == snapshot


def test_future_observations_are_never_used() -> None:
    observations = _observations(605)
    risk_date = observations[-6].observation_date
    snapshot = calculate_macro_regime_snapshot(
        real_rate_observations=observations,
        risk_appetite=_risk_snapshot(risk_date),
        calculated_at_utc=NOW,
    )

    assert snapshot.real_rate.latest_observation_date == risk_date
    assert snapshot.current is not None
    assert snapshot.current.real_rate_observation_date == risk_date


def test_current_rate_older_than_three_calendar_days_is_unavailable() -> None:
    full = _observations(600)
    risk = _risk_snapshot(full[-1].observation_date)
    snapshot = calculate_macro_regime_snapshot(
        real_rate_observations=full[:-4],
        risk_appetite=risk,
        calculated_at_utc=NOW,
    )

    assert snapshot.real_rate.validity == "complete"
    assert snapshot.validity == "unavailable"
    assert snapshot.current is None
    assert snapshot.trajectory == ()


def test_short_history_and_zero_mad_are_not_presented_as_neutral() -> None:
    short = _observations(300)
    short_snapshot = calculate_macro_regime_snapshot(
        real_rate_observations=short,
        risk_appetite=_risk_snapshot(short[-1].observation_date),
        calculated_at_utc=NOW,
    )
    assert short_snapshot.validity == "insufficient_history"
    assert short_snapshot.real_rate.pressure_z is None

    linear = _observations(545, flat=True)
    unavailable = calculate_macro_regime_snapshot(
        real_rate_observations=linear,
        risk_appetite=_risk_snapshot(linear[-1].observation_date),
        calculated_at_utc=NOW,
    )
    assert unavailable.real_rate.validity == "unavailable"
    assert unavailable.validity == "unavailable"


@pytest.mark.parametrize(
    ("real_rate_z", "risk_score", "expected"),
    [
        (-1.0, 1.0, "easing_risk_on"),
        (1.0, 1.0, "growth_reflation"),
        (-1.0, -1.0, "growth_concern"),
        (1.0, -1.0, "tightening_shock"),
        (NEUTRAL_BAND, 1.0, "transition"),
        (1.0, -NEUTRAL_BAND, "transition"),
    ],
)
def test_regime_quadrants_are_classified_by_backend(
    real_rate_z: float,
    risk_score: float,
    expected: str,
) -> None:
    assert (
        classify_regime(
            real_rate_pressure_z=real_rate_z,
            risk_appetite_score=risk_score,
        )
        == expected
    )


def test_payload_and_model_invariants_fail_closed() -> None:
    observations = _observations(600)
    snapshot = calculate_macro_regime_snapshot(
        real_rate_observations=observations,
        risk_appetite=_risk_snapshot(observations[-1].observation_date),
        calculated_at_utc=NOW,
    )
    payload = snapshot.to_payload()
    payload["unknown"] = True
    with pytest.raises(ValueError, match="unknown fields"):
        MacroRegimeSnapshot.from_payload(payload)
    assert snapshot.current is not None
    with pytest.raises(ValueError, match="classification"):
        replace(snapshot.current, regime_label="错误标签")
    with pytest.raises(ValueError, match="risk-appetite score"):
        replace(snapshot.current, risk_appetite_score=0)
    with pytest.raises(ValueError, match="future observation"):
        replace(
            snapshot,
            real_rate=replace(
                snapshot.real_rate,
                latest_observation_date=snapshot.as_of_date + timedelta(days=1),
            ),
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        calculate_macro_regime_snapshot(
            real_rate_observations=observations,
            risk_appetite=_risk_snapshot(observations[-1].observation_date),
            calculated_at_utc=datetime(2026, 9, 4),
        )
