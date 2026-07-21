"""NT ParquetDataCatalog 仓储测试。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from nautilus_trader.model.data import BarType
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from tests.data.helpers import make_bar
from trading_assistant.data.catalog import CatalogRepository


def test_catalog_round_trip_and_idempotent_append(tmp_path: Path) -> None:
    """Instrument 与 Bar 应使用 NT 原生 Catalog 幂等读写。"""
    repository = CatalogRepository(tmp_path / "catalog")
    instrument = TestInstrumentProvider.equity("SPY", "ARCA")
    bar_type = BarType.from_str("SPY.ARCA-1-DAY-LAST-EXTERNAL")
    bars = [
        make_bar(date(2026, 7, 13)),
        make_bar(date(2026, 7, 14), close=102.0),
    ]

    assert repository.write_instruments([instrument]) == 1
    assert repository.write_instruments([instrument]) == 0
    assert repository.append_new_bars(bars) == 2
    assert repository.append_new_bars(list(reversed(bars))) == 0

    loaded = repository.read_bars(bar_type)
    assert [bar.ts_init for bar in loaded] == [bar.ts_init for bar in bars]
    assert repository.latest_bar_timestamp(bar_type) == bars[-1].ts_init
    assert repository.earliest_bar_timestamp(bar_type) == bars[0].ts_init
    assert repository.catalog.instruments(instrument_ids=["SPY.ARCA"])[0].id == instrument.id


def test_catalog_only_appends_timestamps_after_latest(tmp_path: Path) -> None:
    """同批次重复数据应过滤; 更早和更新的缺失时间戳都应写入。"""
    repository = CatalogRepository(tmp_path / "catalog")
    first = make_bar(date(2026, 7, 13))
    second = make_bar(date(2026, 7, 14), close=102.0)
    earlier = make_bar(date(2026, 7, 10), close=99.0)

    assert repository.append_new_bars([first]) == 1
    assert repository.append_new_bars([earlier, first, second, second]) == 2
    assert len(repository.read_bars(first.bar_type)) == 3


def test_catalog_removes_non_serializable_ibkr_info(tmp_path: Path) -> None:
    """IB API 附加对象不得阻止 NT 原生 Instrument 写入。"""
    repository = CatalogRepository(tmp_path / "catalog")
    instrument = TestInstrumentProvider.equity("SPY", "ARCA")
    data = instrument.to_dict(instrument)
    data["info"] = {"unsupported": object()}
    instrument_with_ibkr_info = instrument.__class__.from_dict(data)

    assert repository.write_instruments([instrument_with_ibkr_info]) == 1
    stored = repository.catalog.instruments(instrument_ids=["SPY.ARCA"])[0]
    assert stored.id == instrument.id
    assert stored.info is None
