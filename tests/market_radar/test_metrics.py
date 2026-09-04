"""市场雷达价格指标与快照契约测试。"""

from __future__ import annotations

import math
import statistics
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import pytest

from trading_assistant.market_radar.config import MarketRadarConfig, load_market_radar_config
from trading_assistant.market_radar.membership import (
    CurrentMarketMember,
    CurrentMarketMembership,
)
from trading_assistant.market_radar.metrics import (
    BreadthMetric,
    CurrentBreadthSnapshot,
    PriceBar,
    PriceRadarSnapshot,
    calculate_current_breadth_snapshot,
    calculate_price_snapshot,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CALCULATED_AT = datetime(2026, 9, 3, 1, tzinfo=UTC)


def _config() -> MarketRadarConfig:
    return load_market_radar_config(PROJECT_ROOT / "config" / "market-radar.yaml")


def _bars(
    count: int,
    *,
    scale: float = 1.0,
    drift: float = 0.001,
    start: date = date(2025, 1, 1),
) -> tuple[PriceBar, ...]:
    result: list[PriceBar] = []
    for index in range(count):
        close = scale * (100 + 100 * drift * index + 0.8 * math.sin(index / 4))
        result.append(
            PriceBar(
                day=start + timedelta(days=index),
                high=close * 1.012,
                low=close * 0.988,
                close=close,
            )
        )
    return tuple(result)


def _complete_inputs(config: MarketRadarConfig) -> dict[str, tuple[PriceBar, ...]]:
    return {
        instrument_id: _bars(
            220,
            scale=1 + index / 20,
            drift=0.0007 + index / 100_000,
        )
        for index, instrument_id in enumerate(config.price_instrument_ids)
    }


def test_calculates_complete_market_sector_and_stock_metrics() -> None:
    config = _config()
    inputs = _complete_inputs(config)

    snapshot = calculate_price_snapshot(
        config,
        inputs,
        calculated_at_utc=CALCULATED_AT,
    )

    spy = inputs[config.benchmark]
    rsp = inputs[config.equal_weight_benchmark]
    assert snapshot.as_of_date == spy[-1].day
    assert snapshot.coverage.eligible == 25
    assert snapshot.coverage.observed == 25
    assert snapshot.coverage.ratio == 1
    assert snapshot.market.spy_return_20.value == pytest.approx(spy[-1].close / spy[-21].close - 1)
    assert snapshot.market.spy_distance_ma_200.value == pytest.approx(
        spy[-1].close / statistics.fmean(item.close for item in spy[-200:]) - 1
    )
    assert snapshot.market.rsp_spy_return_20.value == pytest.approx(
        rsp[-1].close / rsp[-21].close - spy[-1].close / spy[-21].close
    )

    sector = snapshot.sectors[0]
    sector_bars = inputs[sector.instrument_id]
    assert sector.relative_strength_60.value == pytest.approx(
        sector_bars[-1].close / sector_bars[-61].close - spy[-1].close / spy[-61].close
    )

    stock = snapshot.stocks[0]
    stock_bars = inputs[stock.instrument_id]
    sector_id = dict(config.sector_etfs)[stock.sector]
    stock_sector_bars = inputs[sector_id]
    expected_momentum = stock_bars[-22].close / stock_bars[-127].close - 1
    expected_sector_momentum = stock_sector_bars[-22].close / stock_sector_bars[-127].close - 1
    returns = [
        math.log(current.close / previous.close) for previous, current in pairwise(stock_bars[-21:])
    ]
    peak = -math.inf
    expected_drawdown = 0.0
    for item in stock_bars[-126:]:
        peak = max(peak, item.close)
        expected_drawdown = min(expected_drawdown, item.close / peak - 1)
    true_ranges = [
        max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        )
        for previous, current in pairwise(stock_bars[-21:])
    ]
    assert stock.momentum_126_21.value == pytest.approx(expected_momentum)
    assert stock.sector_relative_momentum_126_21.value == pytest.approx(
        expected_momentum - expected_sector_momentum
    )
    assert stock.realized_volatility_20.value == pytest.approx(
        statistics.stdev(returns) * math.sqrt(252)
    )
    assert stock.max_drawdown_126.value == pytest.approx(expected_drawdown)
    assert stock.atr_20_ratio.value == pytest.approx(
        statistics.fmean(true_ranges) / stock_bars[-1].close
    )
    assert all(
        metric.validity == "complete"
        for metric in (
            stock.momentum_126_21,
            stock.sector_relative_momentum_126_21,
            stock.distance_ma_200,
            stock.realized_volatility_20,
            stock.max_drawdown_126,
            stock.atr_20_ratio,
        )
    )


def test_insufficient_and_missing_history_are_explicit() -> None:
    config = _config()
    spy = _bars(10)

    snapshot = calculate_price_snapshot(
        config,
        {config.benchmark: spy},
        calculated_at_utc=CALCULATED_AT,
    )

    assert snapshot.coverage.observed == 1
    assert snapshot.coverage.ratio == pytest.approx(1 / 25)
    assert snapshot.market.spy_return_20.value is None
    assert snapshot.market.spy_return_20.validity == "insufficient_history"
    assert snapshot.market.spy_return_20.observations == 10
    assert snapshot.market.spy_return_20.required == 21
    assert snapshot.market.rsp_spy_return_20.validity == "unavailable"
    assert snapshot.sectors[0].relative_strength_20.validity == "unavailable"
    assert snapshot.stocks[0].momentum_126_21.validity == "unavailable"


def test_stale_entity_is_unavailable_even_with_enough_history() -> None:
    config = _config()
    spy = _bars(220)
    aapl = _bars(219)

    snapshot = calculate_price_snapshot(
        config,
        {config.benchmark: spy, "AAPL.US": aapl},
        calculated_at_utc=CALCULATED_AT,
    )

    assert snapshot.coverage.observed == 1
    assert snapshot.stocks[0].momentum_126_21.validity == "unavailable"
    assert snapshot.stocks[0].momentum_126_21.observations == 219


def test_snapshot_payload_round_trip_is_strict_and_versionless() -> None:
    config = _config()
    snapshot = calculate_price_snapshot(
        config,
        _complete_inputs(config),
        calculated_at_utc=CALCULATED_AT,
    )

    payload = snapshot.to_payload()
    assert "schema_version" not in payload
    assert PriceRadarSnapshot.from_payload(payload) == snapshot
    payload["unknown"] = True
    with pytest.raises(ValueError, match="unknown"):
        PriceRadarSnapshot.from_payload(payload)


def test_invalid_inputs_fail_before_snapshot_publication() -> None:
    config = _config()
    with pytest.raises(ValueError, match="benchmark"):
        calculate_price_snapshot(config, {}, calculated_at_utc=CALCULATED_AT)
    with pytest.raises(ValueError, match="outside"):
        calculate_price_snapshot(
            config,
            {config.benchmark: _bars(2), "UNKNOWN.US": _bars(2)},
            calculated_at_utc=CALCULATED_AT,
        )
    with pytest.raises(ValueError, match="ascending"):
        calculate_price_snapshot(
            config,
            {config.benchmark: tuple(reversed(_bars(2)))},
            calculated_at_utc=CALCULATED_AT,
        )
    with pytest.raises(ValueError, match="positive"):
        PriceBar(date(2026, 9, 1), high=1, low=0, close=1)


def _breadth_membership(count: int, *, membership_date: date) -> CurrentMarketMembership:
    return CurrentMarketMembership(
        source="test_current_members",
        membership_date=membership_date,
        members=tuple(
            CurrentMarketMember(
                source_symbol=f"S{index:03d}",
                instrument_id=f"S{index:03d}.US",
                data_symbol=f"S{index:03d}.US",
            )
            for index in range(count)
        ),
    )


def _breadth_bars(
    count: int, *, rising: bool, start: date = date(2025, 1, 1)
) -> tuple[PriceBar, ...]:
    values = [100 + index if rising else 500 - index for index in range(count)]
    return tuple(
        PriceBar(
            day=start + timedelta(days=index),
            high=float(close + 1),
            low=float(close - 1),
            close=float(close),
        )
        for index, close in enumerate(values)
    )


def _ending_breadth_bars(count: int, *, as_of: date) -> tuple[PriceBar, ...]:
    return _breadth_bars(
        count,
        rising=True,
        start=as_of - timedelta(days=count - 1),
    )


def test_current_breadth_metrics_are_reproducible_and_versionless() -> None:
    membership = _breadth_membership(20, membership_date=date(2025, 9, 8))
    inputs = {
        member.instrument_id: _breadth_bars(252, rising=index < 10)
        for index, member in enumerate(membership.members)
    }
    benchmark = _breadth_bars(252, rising=True)

    snapshot = calculate_current_breadth_snapshot(
        membership,
        inputs,
        benchmark,
        calculated_at_utc=CALCULATED_AT,
    )

    assert snapshot.as_of_date == date(2025, 9, 9)
    assert snapshot.member_count == 20
    assert snapshot.b50.value == pytest.approx(0.5)
    assert snapshot.b200.value == pytest.approx(0.5)
    assert snapshot.ad10.value == pytest.approx(0)
    assert snapshot.nhnl.value == pytest.approx(0)
    assert all(
        metric.validity == "complete"
        and metric.eligible == 20
        and metric.observed == 20
        and metric.ratio == 1
        for metric in (snapshot.b50, snapshot.b200, snapshot.ad10, snapshot.nhnl)
    )
    assert snapshot.b50.history_required == 50
    assert snapshot.b200.history_required == 200
    assert snapshot.ad10.history_required == 11
    assert snapshot.nhnl.history_required == 252
    payload = snapshot.to_payload()
    assert "schema_version" not in payload
    assert CurrentBreadthSnapshot.from_payload(payload) == snapshot
    payload["unknown"] = True
    with pytest.raises(ValueError, match="unknown"):
        CurrentBreadthSnapshot.from_payload(payload)


@pytest.mark.parametrize(
    ("observed", "validity", "has_value"),
    [
        (19, "complete", True),
        (18, "partial", True),
        (17, "insufficient_coverage", False),
    ],
)
def test_breadth_coverage_thresholds_use_each_metric_real_denominator(
    observed: int,
    validity: str,
    has_value: bool,
) -> None:
    membership = _breadth_membership(20, membership_date=date(2025, 9, 8))
    as_of = date(2025, 9, 9)
    inputs = {
        member.instrument_id: _ending_breadth_bars(
            50 if index < observed else 49,
            as_of=as_of,
        )
        for index, member in enumerate(membership.members)
    }

    snapshot = calculate_current_breadth_snapshot(
        membership,
        inputs,
        _breadth_bars(252, rising=True),
        calculated_at_utc=CALCULATED_AT,
    )

    assert snapshot.b50.observed == observed
    assert snapshot.b50.ratio == pytest.approx(observed / 20)
    assert snapshot.b50.validity == validity
    assert (snapshot.b50.value is not None) is has_value
    assert snapshot.b200.validity == "insufficient_coverage"
    assert snapshot.b200.observed == 0


def test_ad10_uses_ten_aligned_daily_advance_decline_ratios() -> None:
    membership = _breadth_membership(1, membership_date=date(2025, 1, 10))
    benchmark = _breadth_bars(11, rising=True)
    closes = (100, 101, 100, 101, 100, 101, 100, 101, 100, 101, 100)
    bars = tuple(
        PriceBar(
            day=date(2025, 1, 1) + timedelta(days=index),
            high=close + 1,
            low=close - 1,
            close=close,
        )
        for index, close in enumerate(closes)
    )
    expected = 1.0
    alpha = 2 / 11
    for value in (-1.0, 1.0, -1.0, 1.0, -1.0, 1.0, -1.0, 1.0, -1.0):
        expected = alpha * value + (1 - alpha) * expected

    snapshot = calculate_current_breadth_snapshot(
        membership,
        {membership.members[0].instrument_id: bars},
        benchmark,
        calculated_at_utc=CALCULATED_AT,
    )

    assert snapshot.ad10.value == pytest.approx(expected)
    assert snapshot.ad10.observed == 1


def test_current_breadth_rejects_stale_future_and_invalid_inputs() -> None:
    benchmark = _breadth_bars(252, rising=True)
    as_of = benchmark[-1].day
    fresh = _breadth_membership(1, membership_date=as_of - timedelta(days=7))
    bars = {fresh.members[0].instrument_id: benchmark}
    calculate_current_breadth_snapshot(
        fresh,
        bars,
        benchmark,
        calculated_at_utc=CALCULATED_AT,
    )
    with pytest.raises(ValueError, match="stale"):
        calculate_current_breadth_snapshot(
            _breadth_membership(1, membership_date=as_of - timedelta(days=8)),
            bars,
            benchmark,
            calculated_at_utc=CALCULATED_AT,
        )
    with pytest.raises(ValueError, match="later"):
        calculate_current_breadth_snapshot(
            _breadth_membership(1, membership_date=as_of + timedelta(days=1)),
            bars,
            benchmark,
            calculated_at_utc=CALCULATED_AT,
        )
    with pytest.raises(ValueError, match="benchmark"):
        calculate_current_breadth_snapshot(
            fresh,
            bars,
            (),
            calculated_at_utc=CALCULATED_AT,
        )
    with pytest.raises(ValueError, match="outside"):
        calculate_current_breadth_snapshot(
            fresh,
            {**bars, "UNKNOWN.US": benchmark},
            benchmark,
            calculated_at_utc=CALCULATED_AT,
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        calculate_current_breadth_snapshot(
            fresh,
            bars,
            benchmark,
            calculated_at_utc=datetime(2026, 9, 3),
        )


def test_breadth_metric_validates_coverage_and_value_contract() -> None:
    with pytest.raises(ValueError, match="ratio"):
        BreadthMetric(1, "complete", 10, 10, 0.9, 50)
    with pytest.raises(ValueError, match="validity"):
        BreadthMetric(1, "partial", 10, 10, 1, 50)
    with pytest.raises(ValueError, match="must not expose"):
        BreadthMetric(1, "insufficient_coverage", 10, 8, 0.8, 50)
    with pytest.raises(ValueError, match="within"):
        BreadthMetric(2, "complete", 10, 10, 1, 50)
