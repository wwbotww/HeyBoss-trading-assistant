"""市场雷达价格指标与快照契约测试。"""

from __future__ import annotations

import math
import statistics
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import pytest

from trading_assistant.market_radar.config import MarketRadarConfig, load_market_radar_config
from trading_assistant.market_radar.metrics import (
    PriceBar,
    PriceRadarSnapshot,
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
