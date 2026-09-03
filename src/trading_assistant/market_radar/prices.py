"""市场雷达价格监测池到既有历史数据管道的适配。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from nautilus_trader.model.data import Bar, BarType

from trading_assistant.data.catalog import CatalogRepository
from trading_assistant.data.config import InstrumentSpec
from trading_assistant.market_radar.config import MarketRadarConfig
from trading_assistant.market_radar.metrics import PriceBar

_INTERNAL_BAR_SUFFIX = "1-DAY-LAST-INTERNAL"


def build_price_instrument_specs(
    config: MarketRadarConfig,
    trading_instruments: tuple[InstrumentSpec, ...],
) -> tuple[InstrumentSpec, ...]:
    """组合监测专用 ETF 与交易配置中的 watchlist InstrumentSpec。"""
    trading_by_id = {spec.instrument_id: spec for spec in trading_instruments}
    monitor_by_id = {spec.instrument_id: spec for spec in config.monitor_instruments}

    missing_watchlist = sorted(set(config.watchlist) - trading_by_id.keys())
    if missing_watchlist:
        raise ValueError(
            "market radar watchlist is missing from trading instruments: "
            + ", ".join(missing_watchlist)
        )

    resolved: list[InstrumentSpec] = []
    for instrument_id in config.price_instrument_ids:
        spec = trading_by_id.get(instrument_id) or monitor_by_id.get(instrument_id)
        if spec is None:
            raise ValueError(f"market radar price instrument is not configured: {instrument_id}")
        resolved.append(spec)

    ids = tuple(spec.instrument_id for spec in resolved)
    if len(ids) != len(set(ids)):
        raise ValueError("market radar price universe contains duplicate instruments")
    return tuple(resolved)


def price_bar_from_nautilus(bar: Bar) -> PriceBar:
    """把规范 INTERNAL Bar 缩减为纯指标输入。"""
    if not str(bar.bar_type).endswith(f"-{_INTERNAL_BAR_SUFFIX}"):
        raise ValueError("market radar metrics only accept INTERNAL daily bars")
    return PriceBar(
        day=datetime.fromtimestamp(bar.ts_event / 1_000_000_000, tz=UTC).date(),
        high=bar.high.as_double(),
        low=bar.low.as_double(),
        close=bar.close.as_double(),
    )


def load_internal_price_bars(
    catalog_path: Path,
    instrument_ids: tuple[str, ...],
) -> dict[str, tuple[PriceBar, ...]]:
    """只从 NT Catalog 读取规范 INTERNAL 日线。"""
    catalog = CatalogRepository(catalog_path)
    return {
        instrument_id: tuple(
            price_bar_from_nautilus(bar)
            for bar in catalog.read_bars(
                BarType.from_str(f"{instrument_id}-{_INTERNAL_BAR_SUFFIX}")
            )
        )
        for instrument_id in instrument_ids
    }
