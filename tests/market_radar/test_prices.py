"""市场雷达价格监测池装配测试。"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from tests.data.helpers import make_bar
from trading_assistant.data.catalog import CatalogRepository
from trading_assistant.data.config import InstrumentSpec, load_instruments
from trading_assistant.market_radar.config import load_market_radar_config
from trading_assistant.market_radar.prices import (
    build_price_instrument_specs,
    load_internal_price_bars,
    price_bar_from_nautilus,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_price_universe_reuses_trading_specs_for_watchlist() -> None:
    config = load_market_radar_config(PROJECT_ROOT / "config" / "market-radar.yaml")
    trading = load_instruments(PROJECT_ROOT / "config" / "instruments.yaml")

    result = build_price_instrument_specs(config, trading)

    assert tuple(spec.instrument_id for spec in result) == config.price_instrument_ids
    assert len(result) == 25
    aapl = next(spec for spec in result if spec.instrument_id == "AAPL.US")
    assert aapl is next(spec for spec in trading if spec.instrument_id == "AAPL.US")
    assert aapl.factor_security_id == "eodhd:isin:US0378331005"
    spy = next(spec for spec in result if spec.instrument_id == "SPY.US")
    assert spy.factor_security_id is None
    assert spy.primary_exchange == "ARCA"


def test_existing_trading_spec_wins_for_a_market_etf() -> None:
    config = load_market_radar_config(PROJECT_ROOT / "config" / "market-radar.yaml")
    trading = load_instruments(PROJECT_ROOT / "config" / "instruments.yaml")
    configured_spy = next(
        spec for spec in config.monitor_instruments if spec.instrument_id == "SPY.US"
    )
    trading_spy = replace(configured_spy, live_instrument_id="SPY.ARCA")

    result = build_price_instrument_specs(config, (trading_spy, *trading))

    assert result[0] is trading_spy


def test_missing_watchlist_spec_fails_closed() -> None:
    config = load_market_radar_config(PROJECT_ROOT / "config" / "market-radar.yaml")
    trading = tuple(
        spec
        for spec in load_instruments(PROJECT_ROOT / "config" / "instruments.yaml")
        if spec.instrument_id != "AAPL.US"
    )

    with pytest.raises(ValueError, match=r"AAPL\.US"):
        build_price_instrument_specs(config, trading)


def test_unresolved_market_role_fails_closed() -> None:
    config = load_market_radar_config(PROJECT_ROOT / "config" / "market-radar.yaml")
    trading = load_instruments(PROJECT_ROOT / "config" / "instruments.yaml")
    monitors: tuple[InstrumentSpec, ...] = tuple(
        spec for spec in config.monitor_instruments if spec.instrument_id != "SPY.US"
    )

    with pytest.raises(ValueError, match=r"SPY\.US"):
        build_price_instrument_specs(replace(config, monitor_instruments=monitors), trading)


def test_load_price_bars_reads_only_internal_catalog_series(tmp_path: Path) -> None:
    repository = CatalogRepository(tmp_path / "catalog")
    external = make_bar(date(2026, 9, 1), instrument_id="SPY.US", close=101)
    internal = make_bar(
        date(2026, 9, 1),
        instrument_id="SPY.US",
        bar_type_suffix="1-DAY-LAST-INTERNAL",
        close=99,
    )
    repository.replace_bars([external, internal])

    result = load_internal_price_bars(tmp_path / "catalog", ("SPY.US", "RSP.US"))

    assert result["SPY.US"][0].day == date(2026, 9, 1)
    assert result["SPY.US"][0].close == 99
    assert result["RSP.US"] == ()
    with pytest.raises(ValueError, match="INTERNAL"):
        price_bar_from_nautilus(external)
