"""NT ParquetDataCatalog 仓储测试。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog
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


def test_catalog_replaces_complete_bar_series(tmp_path: Path) -> None:
    """调整价修订时应替换完整 BarType; 完全相同则不重复写入。"""
    repository = CatalogRepository(tmp_path / "catalog")
    first = make_bar(date(2026, 7, 13), close=101)
    second = make_bar(date(2026, 7, 14), close=102)
    revised = make_bar(date(2026, 7, 13), close=100)

    assert repository.replace_bars([first, second]) == 2
    assert repository.replace_bars([first, second]) == 0
    assert repository.replace_bars([revised, second]) == 2
    assert repository.read_bars(first.bar_type) == [revised, second]


def test_catalog_replaces_only_requested_bar_range(tmp_path: Path) -> None:
    """增量修订应保留窗口外历史并保持幂等。"""
    repository = CatalogRepository(tmp_path / "catalog")
    before = make_bar(date(2026, 7, 10), close=100)
    old_first = make_bar(date(2026, 7, 13), close=101)
    old_second = make_bar(date(2026, 7, 14), close=102)
    after = make_bar(date(2026, 7, 15), high=104, close=103)
    revised_first = make_bar(date(2026, 7, 13), close=99)
    revised_second = make_bar(date(2026, 7, 14), high=105, close=104)
    assert repository.replace_bars([before, old_first, old_second, after]) == 4

    assert (
        repository.replace_bar_range(
            [revised_first, revised_second],
            start_ns=old_first.ts_init,
            end_ns=old_second.ts_init,
        )
        == 2
    )
    assert repository.read_bars(before.bar_type) == [
        before,
        revised_first,
        revised_second,
        after,
    ]
    assert (
        repository.replace_bar_range(
            [revised_first, revised_second],
            start_ns=old_first.ts_init,
            end_ns=old_second.ts_init,
        )
        == 0
    )


def test_catalog_range_replacement_restores_old_data_after_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """任一范围写入失败时不得留下删除后的半成品。"""
    repository = CatalogRepository(tmp_path / "catalog")
    first = make_bar(date(2026, 7, 13), close=101)
    second = make_bar(date(2026, 7, 14), close=102)
    revised = make_bar(date(2026, 7, 13), close=99)
    repository.replace_bars([first, second])
    original_write = repository.catalog.write_data

    def fail_once(data: list[object], **_kwargs: object) -> None:
        del data
        monkeypatch.setattr(repository.catalog, "write_data", original_write)
        raise OSError("simulated write failure")

    monkeypatch.setattr(repository.catalog, "write_data", fail_once)
    with pytest.raises(OSError, match="simulated"):
        repository.replace_bar_range(
            [revised],
            start_ns=first.ts_init,
            end_ns=first.ts_init,
        )
    assert repository.read_bars(first.bar_type) == [first, second]


def test_catalog_range_replacement_rejects_invalid_boundaries(tmp_path: Path) -> None:
    repository = CatalogRepository(tmp_path / "catalog")
    bar = make_bar(date(2026, 7, 13))
    with pytest.raises(ValueError, match="must not exceed"):
        repository.replace_bar_range([bar], start_ns=2, end_ns=1)
    with pytest.raises(ValueError, match="inside"):
        repository.replace_bar_range(
            [bar],
            start_ns=bar.ts_init + 1,
            end_ns=bar.ts_init + 2,
        )


@pytest.mark.parametrize("operation", ["append", "replace_full", "replace_range", "delete"])
def test_failed_publication_or_rollback_keeps_readers_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """真实底层写入失败或回滚也失败时, 不能释放半成品给后续读者。"""
    repository = CatalogRepository(tmp_path / "catalog")
    first = make_bar(date(2026, 7, 13), close=101)
    second = make_bar(date(2026, 7, 14), close=102)
    repository.replace_bars([first])

    def fail_write(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk unavailable")

    method = "delete_data_range" if operation == "delete" else "write_data"
    operations = {
        "append": lambda: repository.append_new_bars([second]),
        "replace_full": lambda: repository.replace_bars([second]),
        "replace_range": lambda: repository.replace_bar_range(
            [second], start_ns=first.ts_init, end_ns=second.ts_init
        ),
        "delete": lambda: repository.catalog.delete_data_range(Bar, identifier=str(first.bar_type)),
    }
    with monkeypatch.context() as patch:
        patch.setattr(ParquetDataCatalog, method, fail_write)
        with pytest.raises(OSError, match="disk unavailable"):
            operations[operation]()
    assert (tmp_path / "catalog" / ".catalog-writing").exists()
    with pytest.raises(RuntimeError, match="publication was interrupted"):
        repository.read_bars(first.bar_type)
    with pytest.raises(RuntimeError, match="publication was interrupted"):
        repository.append_new_bars([second])
